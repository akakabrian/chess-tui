"""Game state — wraps ``chess.Board`` with a move list, undo, PGN,
clock, and a tiny notion of "mode" (hotseat / vs engine / puzzle /
analysis).  Everything the TUI and agent API read is consolidated
here."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional

import chess
import chess.pgn


class Mode(str, Enum):
    HOTSEAT  = "hotseat"     # two humans taking turns
    ENGINE   = "engine"      # human vs engine
    ANALYSIS = "analysis"    # free exploration, no clock, no engine move
    PUZZLE   = "puzzle"      # puzzle trainer


@dataclass
class ClockState:
    """Simple Fischer clock. Times are in seconds."""
    white_ms: int = 10 * 60 * 1000
    black_ms: int = 10 * 60 * 1000
    increment_ms: int = 3 * 1000
    running: bool = False
    active_white: bool = True
    last_tick: float = field(default_factory=time.monotonic)

    def snapshot(self) -> dict:
        return {
            "white_ms": self.white_ms,
            "black_ms": self.black_ms,
            "running": self.running,
            "increment_ms": self.increment_ms,
        }


class Game:
    """Position + history + metadata."""

    def __init__(self, board: Optional[chess.Board] = None,
                 *, mode: Mode = Mode.HOTSEAT,
                 human_color: chess.Color = chess.WHITE,
                 clock: Optional[ClockState] = None,
                 flipped: bool = False) -> None:
        self.board: chess.Board = board if board is not None else chess.Board()
        self.start_board: chess.Board = self.board.copy()  # for PGN
        self.mode = mode
        self.human_color = human_color
        self.flipped = flipped
        self.clock = clock or ClockState()
        self.status_msg: str = ""
        # Move navigation — a cursor into the played move list. This lets
        # the player step backward through the game without losing
        # history (analysis navigation).
        self._ply_cursor: int = 0

    # ----------------------------------------------------------------- core

    @property
    def ply_cursor(self) -> int:
        return self._ply_cursor

    @property
    def total_plies(self) -> int:
        return len(self.board.move_stack)

    def full_history(self) -> List[chess.Move]:
        return list(self.board.move_stack)

    def live_board(self) -> chess.Board:
        """Board at the cursor position (may differ from self.board)."""
        if self._ply_cursor == self.total_plies:
            return self.board
        b = self.start_board.copy()
        for mv in self.board.move_stack[: self._ply_cursor]:
            b.push(mv)
        return b

    # ----------------------------------------------------------------- moves

    def try_move(self, move: chess.Move) -> bool:
        """Push ``move`` if legal on the live board and we're at the tip.

        Playing a move while cursor is in history first truncates the
        history beyond the cursor — the standard "branch on new move"
        behaviour found in most GUI analysis boards.
        """
        if self._ply_cursor != self.total_plies:
            # Truncate history from cursor forward. Replay from start_board
            # (not chess.Board()) so custom initial positions — puzzles,
            # imported PGNs with a SetUp/FEN header — are preserved.
            new_stack = self.board.move_stack[: self._ply_cursor]
            replay = self.start_board.copy()
            for mv in new_stack:
                replay.push(mv)
            self.board = replay
        if move not in self.board.legal_moves:
            return False
        self.board.push(move)
        self._ply_cursor = self.total_plies
        return True

    def undo(self) -> bool:
        if self.total_plies == 0:
            return False
        self.board.pop()
        self._ply_cursor = self.total_plies
        return True

    def goto(self, ply: int) -> None:
        self._ply_cursor = max(0, min(self.total_plies, ply))

    def step(self, delta: int) -> None:
        self.goto(self._ply_cursor + delta)

    def to_start(self) -> None:
        self.goto(0)

    def to_end(self) -> None:
        self.goto(self.total_plies)

    # ------------------------------------------------------------------ pgn

    def to_pgn(self, *, white: str = "Player", black: str = "Player") -> str:
        game = chess.pgn.Game()
        game.headers["Event"] = "chess-tui"
        game.headers["White"] = white
        game.headers["Black"] = black
        # If the start board isn't the standard position, record the FEN.
        if self.start_board.fen() != chess.STARTING_FEN:
            game.headers["SetUp"] = "1"
            game.headers["FEN"] = self.start_board.fen()
        node = game
        for mv in self.board.move_stack:
            node = node.add_main_variation(mv)
        result = self.board.result(claim_draw=True)
        game.headers["Result"] = result
        return str(game)

    @classmethod
    def from_pgn(cls, pgn_text: str) -> "Game":
        g = chess.pgn.read_game(io.StringIO(pgn_text))
        if g is None:
            raise ValueError("empty PGN")
        board = g.board()
        start = board.copy()
        for mv in g.mainline_moves():
            board.push(mv)
        out = cls(board=board)
        out.start_board = start
        return out

    def write_pgn(self, path: Path, *, white: str = "Player",
                  black: str = "Player") -> None:
        path.write_text(self.to_pgn(white=white, black=black))

    @classmethod
    def read_pgn(cls, path: Path) -> "Game":
        return cls.from_pgn(path.read_text())

    # -------------------------------------------------------------- san list

    def san_history(self) -> List[str]:
        """Walk the start board + stack to build the SAN list. Cheap
        enough for live UI (~N text ops for N moves)."""
        b = self.start_board.copy()
        out: list[str] = []
        for mv in self.board.move_stack:
            out.append(b.san(mv))
            b.push(mv)
        return out

    # -------------------------------------------------------------- status

    def is_over(self) -> bool:
        b = self.live_board()
        return b.is_game_over(claim_draw=True)

    def outcome_text(self) -> str:
        b = self.live_board()
        out = b.outcome(claim_draw=True)
        if out is None:
            return ""
        winner = out.winner
        kind = out.termination.name.lower().replace("_", " ")
        if winner is True:
            return f"1-0 · white wins ({kind})"
        if winner is False:
            return f"0-1 · black wins ({kind})"
        return f"½-½ · draw ({kind})"
