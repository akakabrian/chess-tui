"""UCI engine wrappers around python-chess.

Stage 2 "engine integration" — the engines are external binaries
(Stockfish, GNU Chess). We drive them via ``chess.engine.SimpleEngine``
(python-chess subprocess+UCI). This module exposes:

* :class:`EngineHandle` — one spawned engine; ``analyse`` / ``play``.
* :class:`MultiEngineAnalyzer` — background analysis coordinator.
* :func:`discover_engines` — locate installed binaries on this host.

Design notes (see DECISIONS.md §2):

* Analysis runs in a background *thread* via python-chess's sync API,
  not async. Textual workers thread out fine; the sync API is simpler
  than the asyncio UCI client and we aren't multiplexing many engines.
* The analyzer exposes a shared ``AnalysisSnapshot`` the UI reads each
  redraw — no callbacks from the worker into Textual's event loop.
* Each engine runs "infinite" analysis on the current position and is
  interrupted (``stop``) + re-analysed on move changes. Depth / nps /
  pv get read from the engine's ``InfoDict`` stream.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

import chess
import chess.engine

log = logging.getLogger("chess_tui.engine")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

DEFAULT_ENGINE_BINARIES = {
    # name      -> list of candidate commands
    "stockfish": ["stockfish", "/usr/games/stockfish", "/usr/local/bin/stockfish"],
    "gnuchess":  ["gnuchess",  "/usr/games/gnuchess",  "/usr/local/bin/gnuchess"],
    "lc0":       ["lc0",       "/usr/local/bin/lc0"],
}


@dataclass
class EngineSpec:
    name: str
    path: str
    args: List[str] = field(default_factory=list)

    @property
    def argv(self) -> List[str]:
        return [self.path, *self.args]


def discover_engines() -> List[EngineSpec]:
    """Return engine specs for binaries present on this host."""
    specs: List[EngineSpec] = []
    for name, cands in DEFAULT_ENGINE_BINARIES.items():
        for cand in cands:
            path = shutil.which(cand) if "/" not in cand else cand
            if path and Path(path).exists():
                args: List[str] = []
                # GNU Chess must be launched in UCI mode explicitly.
                if name == "gnuchess":
                    args = ["--uci"]
                specs.append(EngineSpec(name=name, path=path, args=args))
                break
    return specs


# ---------------------------------------------------------------------------
# Snapshots (thread → UI shared state)
# ---------------------------------------------------------------------------

@dataclass
class LineInfo:
    """One principal variation entry."""
    rank: int              # 1..N MultiPV rank
    depth: int
    seldepth: int
    nodes: int
    nps: int
    score_cp: Optional[int]    # score from side-to-move's POV, centipawns
    mate_in: Optional[int]     # mate distance, +ve mate for us, -ve against
    pv: List[chess.Move]       # principal variation (moves)
    pv_san: List[str]          # same, in SAN — rendered for UI


@dataclass
class AnalysisSnapshot:
    """Point-in-time analysis result from one engine."""
    engine: str                       # engine name (eg "stockfish")
    fen: str                          # position FEN the lines are from
    turn_white: bool                  # whose turn it is at that position
    lines: List[LineInfo] = field(default_factory=list)
    updated: float = 0.0              # monotonic() when we last wrote

    @property
    def best(self) -> Optional[LineInfo]:
        return self.lines[0] if self.lines else None


# ---------------------------------------------------------------------------
# EngineHandle — one subprocess
# ---------------------------------------------------------------------------

class EngineHandle:
    """Thin wrapper around SimpleEngine with retry / lifecycle helpers."""

    def __init__(self, spec: EngineSpec) -> None:
        self.spec = spec
        self._engine: Optional[chess.engine.SimpleEngine] = None

    def start(self) -> None:
        if self._engine is not None:
            return
        self._engine = chess.engine.SimpleEngine.popen_uci(self.spec.argv)

    def quit(self) -> None:
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:  # pragma: no cover
                log.exception("engine %s failed to quit cleanly", self.spec.name)
            finally:
                self._engine = None

    @property
    def engine(self) -> chess.engine.SimpleEngine:
        if self._engine is None:
            self.start()
        assert self._engine is not None
        return self._engine

    # Configure / reconfigure options (hash, threads, …). Safe to call
    # before analysis starts; ignored if option is unsupported.
    # python-chess manages MultiPV via the analysis() call itself, so we
    # filter it out here to avoid EngineError.
    _ENGINE_MANAGED_OPTS = frozenset({"MultiPV", "Ponder"})

    def configure(self, options: dict) -> None:
        eng = self.engine
        safe: dict = {}
        for k, v in options.items():
            if k in self._ENGINE_MANAGED_OPTS:
                continue
            if k in eng.options:
                safe[k] = v
        if safe:
            eng.configure(safe)

    def play_move(self, board: chess.Board, limit: chess.engine.Limit) -> chess.Move:
        return self.engine.play(board, limit).move  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# MultiEngineAnalyzer — one background thread per engine, re-analyses on
# each set_position(). UI reads ``snapshot(name)`` cheaply.
# ---------------------------------------------------------------------------

class MultiEngineAnalyzer:
    def __init__(self, specs: Iterable[EngineSpec],
                 *, multipv: int = 3, hash_mb: int = 64,
                 threads: int = 1) -> None:
        self._specs = list(specs)
        self._multipv = multipv
        self._hash = hash_mb
        self._threads = threads
        self._workers: dict[str, _Worker] = {}
        self._snapshots: dict[str, AnalysisSnapshot] = {}
        self._lock = threading.Lock()

    # ---- lifecycle ----

    def start(self) -> None:
        for spec in self._specs:
            if spec.name in self._workers:
                continue
            w = _Worker(spec, analyzer=self)
            w.start()
            self._workers[spec.name] = w

    def stop(self) -> None:
        for w in self._workers.values():
            w.shutdown()
        self._workers.clear()

    # ---- position changes ----

    def set_position(self, board: chess.Board) -> None:
        """Push a new analysis target to every worker."""
        for w in self._workers.values():
            w.set_position(board.copy())

    # ---- snapshots (UI reads) ----

    def snapshot(self, name: str) -> Optional[AnalysisSnapshot]:
        with self._lock:
            return self._snapshots.get(name)

    def all_snapshots(self) -> dict[str, AnalysisSnapshot]:
        with self._lock:
            return dict(self._snapshots)

    # ---- worker callbacks ----

    def _publish(self, name: str, snap: AnalysisSnapshot) -> None:
        with self._lock:
            self._snapshots[name] = snap

    @property
    def engine_names(self) -> List[str]:
        return [s.name for s in self._specs]

    @property
    def multipv(self) -> int:
        return self._multipv

    @property
    def hash_mb(self) -> int:
        return self._hash

    @property
    def threads(self) -> int:
        return self._threads


class _Worker(threading.Thread):
    """One engine, one analysis thread."""

    def __init__(self, spec: EngineSpec, *, analyzer: MultiEngineAnalyzer) -> None:
        super().__init__(daemon=True, name=f"engine-{spec.name}")
        self.spec = spec
        self.analyzer = analyzer
        self._handle = EngineHandle(spec)
        self._board: Optional[chess.Board] = None
        self._cond = threading.Condition()
        self._alive = True

    def set_position(self, board: chess.Board) -> None:
        with self._cond:
            self._board = board
            self._cond.notify_all()

    def shutdown(self) -> None:
        with self._cond:
            self._alive = False
            self._cond.notify_all()
        try:
            self._handle.quit()
        except Exception:
            pass

    def run(self) -> None:  # pragma: no cover - engine I/O
        try:
            self._handle.start()
            self._handle.configure({
                "Threads": self.analyzer.threads,
                "Hash": self.analyzer.hash_mb,
                "UCI_AnalyseMode": True,
            })
        except Exception:
            log.exception("worker %s failed to start", self.spec.name)
            return

        engine = self._handle.engine
        while True:
            with self._cond:
                while self._alive and self._board is None:
                    self._cond.wait()
                if not self._alive:
                    return
                board = self._board
                self._board = None  # consume

            assert board is not None
            try:
                self._analyse(engine, board)
            except chess.engine.EngineTerminatedError:
                return
            except Exception:
                log.exception("worker %s analysis loop", self.spec.name)

    def _analyse(self, engine: chess.engine.SimpleEngine, board: chess.Board) -> None:
        """Run ``analysis`` until a new board is handed in."""
        with engine.analysis(board, multipv=self.analyzer.multipv) as analysis:
            for info in analysis:
                # Check for cancellation — new position queued?
                with self._cond:
                    interrupted = self._board is not None or not self._alive
                if interrupted:
                    analysis.stop()
                    break
                snap = self._collect(analysis, board)
                if snap is not None:
                    self.analyzer._publish(self.spec.name, snap)

    def _collect(self, analysis: chess.engine.SimpleAnalysisResult,
                 board: chess.Board) -> Optional[AnalysisSnapshot]:
        multi = analysis.multipv
        if not multi:
            return None
        lines: list[LineInfo] = []
        for rank, info in enumerate(multi, start=1):
            pv = info.get("pv", []) or []
            score = info.get("score")
            cp = mate = None
            if score is not None:
                pov = score.pov(board.turn)
                if pov.is_mate():
                    mate = pov.mate()
                else:
                    cp = pov.score(mate_score=100_000)
            pv_san = _try_san(board, pv)
            lines.append(LineInfo(
                rank=rank,
                depth=int(info.get("depth") or 0),
                seldepth=int(info.get("seldepth") or 0),
                nodes=int(info.get("nodes") or 0),
                nps=int(info.get("nps") or 0),
                score_cp=cp,
                mate_in=mate,
                pv=list(pv),
                pv_san=pv_san,
            ))
        lines.sort(key=lambda li: li.rank)
        return AnalysisSnapshot(
            engine=self.spec.name,
            fen=board.fen(),
            turn_white=board.turn == chess.WHITE,
            lines=lines,
            updated=time.monotonic(),
        )


def _try_san(board: chess.Board, moves: Iterable[chess.Move]) -> List[str]:
    """Render a move sequence as SAN on a copy; drop tail on illegality."""
    out: list[str] = []
    b = board.copy(stack=False)
    for mv in moves:
        if mv is None or mv == chess.Move.null():
            break
        if not b.is_legal(mv):
            break
        out.append(b.san(mv))
        b.push(mv)
    return out


# ---------------------------------------------------------------------------
# Convenience: one-shot bestmove (used by the hotseat "engine opponent" and
# hint requests). Cheaper than starting a background analyzer.
# ---------------------------------------------------------------------------

def bestmove(spec: EngineSpec, board: chess.Board, *,
             time_limit: float = 0.2, depth: Optional[int] = None
             ) -> Optional[chess.Move]:
    """Run ``engine.play`` once and return the chosen move."""
    eng = chess.engine.SimpleEngine.popen_uci(spec.argv)
    try:
        if depth is not None:
            limit = chess.engine.Limit(depth=depth)
        else:
            limit = chess.engine.Limit(time=time_limit)
        return eng.play(board, limit).move
    finally:
        try:
            eng.quit()
        except Exception:
            pass
