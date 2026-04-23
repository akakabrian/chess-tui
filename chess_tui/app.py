"""Textual app — 4-ish pane chess study TUI.

Layout:

    +----------------------+-----------------------------+
    | [board]              | analysis (multi-engine)     |
    |  ranks × files grid  | eval bar, top-N PVs         |
    |                      +-----------------------------+
    |                      | opening explorer (polyglot) |
    |                      +-----------------------------+
    |                      | move list (SAN)             |
    |                      +-----------------------------+
    | status | flash       | log                          |
    +----------------------+-----------------------------+

Key interactions: cursor + select-then-move, or mouse-click.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Optional

import chess
from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, RichLog, Static

from .board_view import BoardClicked, BoardView
from .book import OpeningBook
from .engine import (
    AnalysisSnapshot,
    EngineSpec,
    MultiEngineAnalyzer,
    bestmove,
    discover_engines,
)
from .game import Game, Mode
from .puzzles import PuzzleSession, load_puzzles, pick_puzzle
from .screens import HelpScreen, NewGameScreen, PromotionScreen


log = logging.getLogger("chess_tui.app")


# ------------------------- helper: eval bar --------------------------------

def _eval_bar(cp: Optional[int], mate: Optional[int], *, width: int = 24,
              turn_white: bool = True) -> Text:
    """Build a horizontal eval bar from white's POV.

    Input scores are from side-to-move POV; we invert to white's POV.
    """
    if mate is not None:
        # ±mate → pin to full bar + label
        if (mate > 0) == turn_white:
            fill = width
        else:
            fill = 0
        label = f" M{abs(mate)} "
    elif cp is not None:
        if not turn_white:
            cp = -cp
        # map [-600, +600] cp → [0, width]
        cp_clip = max(-600, min(600, cp))
        frac = (cp_clip + 600) / 1200.0
        fill = int(round(frac * width))
        label = f" {cp/100:+.2f} "
    else:
        fill = width // 2
        label = "  ?.??  "

    t = Text()
    white_part = "█" * fill
    black_part = "█" * (width - fill)
    t.append(white_part, Style(color="rgb(245,245,250)"))
    t.append(black_part, Style(color="rgb(80,85,100)"))
    t.append(label, Style(color="rgb(200,210,230)", bold=True))
    return t


def _format_score(cp: Optional[int], mate: Optional[int]) -> str:
    if mate is not None:
        return f"#{mate:+d}" if mate else "#0"
    if cp is None:
        return "?"
    return f"{cp/100:+.2f}"


# ===========================================================================
# ChessApp
# ===========================================================================


class ChessApp(App):
    CSS_PATH = "tui.tcss"
    TITLE = "chess-tui"
    SUB_TITLE = "multi-engine analysis workstation"

    BINDINGS = [
        # Board movement (priority — scrollable siblings would eat these).
        Binding("up",    "cursor(0,1)",  priority=True, show=False),
        Binding("down",  "cursor(0,-1)", priority=True, show=False),
        Binding("left",  "cursor(-1,0)", priority=True, show=False),
        Binding("right", "cursor(1,0)",  priority=True, show=False),
        Binding("k",     "cursor(0,1)",  priority=True, show=False),
        Binding("j",     "cursor(0,-1)", priority=True, show=False),
        Binding("h",     "cursor(-1,0)", priority=True, show=False),
        Binding("l",     "cursor(1,0)",  priority=True, show=False),

        Binding("space", "select",    priority=True, show=False),
        Binding("enter", "select",    priority=True, show=False),
        # NOTE: escape is intentionally NOT priority — ModalScreen's own
        # "escape → pop_screen" binding must win while a modal is open.
        Binding("escape","cancel",    show=False),

        Binding("u",     "undo",      show=True,  description="undo"),
        Binding("f",     "flip",      show=True,  description="flip"),
        Binding("comma", "step(-1)",  show=False),
        Binding("less_than_sign", "history_start", show=False),
        Binding("greater_than_sign","history_end", show=False),
        Binding("period","step(1)",   show=False),

        Binding("a",     "toggle_analysis", show=True, description="analysis"),
        Binding("o",     "toggle_explorer", show=False),
        Binding("plus",  "bump_multipv(1)", show=False),
        Binding("minus", "bump_multipv(-1)", show=False),
        Binding("m",     "play_best", show=True, description="engine move"),
        Binding("H",     "hint", show=True, description="hint"),

        Binding("N",     "new_game_dialog", show=True, description="new"),
        Binding("R",     "new_puzzle", show=True, description="puzzle"),
        Binding("S",     "save_pgn",   show=True, description="save"),
        Binding("P",     "load_pgn",   show=False, description="load"),
        Binding("question_mark", "help", show=True),
        Binding("q",     "quit",       show=True),
    ]

    def __init__(self,
                 mode: Mode = Mode.HOTSEAT,
                 start_fen: Optional[str] = None,
                 *,
                 engines: Optional[list[EngineSpec]] = None,
                 multipv: int = 3,
                 engine_time: float = 0.3,
                 analysis_enabled: bool = True,
                 puzzle_path: Optional[Path] = None,
                 book: Optional[OpeningBook] = None,
                 agent_port: Optional[int] = None,
                 ) -> None:
        super().__init__()
        board = chess.Board(start_fen) if start_fen else chess.Board()
        self.game = Game(board=board, mode=mode)
        self._engine_specs = engines if engines is not None else discover_engines()
        self._multipv = multipv
        self._engine_time = engine_time
        self._analysis_enabled = analysis_enabled and bool(self._engine_specs)
        self._analyzer: Optional[MultiEngineAnalyzer] = None
        self._book = book if book is not None else OpeningBook()
        self._puzzles = load_puzzles(puzzle_path)
        self._puzzle_rng = random.Random()
        self._puzzle_session: Optional[PuzzleSession] = None
        self._agent_port = agent_port
        self._agent_runner = None

        # widgets (populated in on_mount)
        self.board_view: Optional[BoardView] = None
        self.analysis_panel: Optional[Static] = None
        self.explorer_panel: Optional[Static] = None
        self.movelist_panel: Optional[Static] = None
        self.status_panel: Optional[Static] = None
        self.flash_panel: Optional[Static] = None
        self.log_panel: Optional[RichLog] = None

    # --------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with Vertical(id="board-pane"):
                yield BoardView(board_getter=lambda: self.game.live_board(), id="board")
                yield Static("", id="status")
                yield Static("", id="flash")
            with Vertical(id="side"):
                yield Static("", id="analysis")
                yield Static("", id="explorer-moves")
                yield Static("", id="movelist")
                yield RichLog(id="log", max_lines=200, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.board_view = self.query_one("#board", BoardView)
        self.analysis_panel = self.query_one("#analysis", Static)
        self.explorer_panel = self.query_one("#explorer-moves", Static)
        self.movelist_panel = self.query_one("#movelist", Static)
        self.status_panel = self.query_one("#status", Static)
        self.flash_panel = self.query_one("#flash", Static)
        self.log_panel = self.query_one("#log", RichLog)

        self._log(f"[bold]chess-tui[/] ready — engines: "
                  f"{', '.join(s.name for s in self._engine_specs) or 'none'}"
                  f"  book: {'yes' if self._book.available else 'no'}")

        if self._analysis_enabled:
            self._start_analyzer()
        self._refresh_all_panels()
        # Periodic re-read of engine snapshots into the UI.
        self.set_interval(0.4, self._refresh_all_panels)
        if self._agent_port is not None:
            self.run_worker(self._launch_agent_api, exclusive=True, group="agent")

    async def _launch_agent_api(self) -> None:
        if self._agent_port is None:
            return
        try:
            from .agent_api import start_server
            self._agent_runner = await start_server(self, port=self._agent_port)
            if self._agent_runner is not None:
                self._log(f"[cyan]agent API[/] listening on "
                          f"http://127.0.0.1:{self._agent_port}")
        except Exception as e:
            self._log(f"[red]agent API failed[/]: {e}")

    def on_unmount(self) -> None:
        if self._analyzer is not None:
            self._analyzer.stop()
        self._book.close()

    # --------------------------------------------------------------- engine

    def _start_analyzer(self) -> None:
        if not self._engine_specs:
            return
        self._analyzer = MultiEngineAnalyzer(
            self._engine_specs, multipv=self._multipv,
            hash_mb=64, threads=1)
        self._analyzer.start()
        self._analyzer.set_position(self.game.live_board())

    def _stop_analyzer(self) -> None:
        if self._analyzer is not None:
            self._analyzer.stop()
            self._analyzer = None

    def _kick_analysis(self) -> None:
        if self._analyzer is not None:
            self._analyzer.set_position(self.game.live_board())

    # ----------------------------------------------------- cursor / select

    def action_cursor(self, dx: int, dy: int) -> None:
        bv = self.board_view
        if bv is None:
            return
        # Flipped: arrow-up still means "toward opponent", regardless of
        # flip — that's the intuitive behaviour. We emulate by inverting
        # dy when flipped (and dx too since files flip).
        if bv.flipped:
            dx, dy = -dx, -dy
        bv.cursor_file = max(0, min(7, bv.cursor_file + dx))
        bv.cursor_rank = max(0, min(7, bv.cursor_rank + dy))
        self._refresh_overlays()

    def action_select(self) -> None:
        bv = self.board_view
        if bv is None:
            return
        sq = chess.square(bv.cursor_file, bv.cursor_rank)
        if bv.selected is None:
            piece = self.game.live_board().piece_at(sq)
            if piece is None or piece.color != self.game.live_board().turn:
                self._flash("no piece to move on that square")
                return
            bv.selected = sq
            self._refresh_overlays()
            return
        self._try_user_move(bv.selected, sq)

    def action_cancel(self) -> None:
        bv = self.board_view
        if bv is None:
            return
        bv.selected = None
        self._refresh_overlays()

    def action_flip(self) -> None:
        if self.board_view is None:
            return
        self.board_view.flipped = not self.board_view.flipped
        self._flash("board flipped")

    # ------------------------------------------------ mouse click routing

    def on_board_clicked(self, msg: BoardClicked) -> None:
        bv = self.board_view
        if bv is None:
            return
        sq = msg.square
        if bv.selected is None:
            piece = self.game.live_board().piece_at(sq)
            if piece is None or piece.color != self.game.live_board().turn:
                return
            bv.selected = sq
            self._refresh_overlays()
            return
        if bv.selected == sq:
            bv.selected = None
            self._refresh_overlays()
            return
        self._try_user_move(bv.selected, sq)

    # --------------------------------------------------------------- moves

    def _try_user_move(self, src: int, dst: int) -> None:
        board = self.game.live_board()
        # Find a matching legal move (incl. promotion choice).
        candidates = [m for m in board.legal_moves
                      if m.from_square == src and m.to_square == dst]
        if not candidates:
            self._flash("illegal move")
            bv = self.board_view
            if bv is not None:
                bv.selected = None
                self._refresh_overlays()
            return
        if len(candidates) > 1 and any(m.promotion for m in candidates):
            # Promotion — ask via modal.
            def _on_promo(choice: str) -> None:
                mapping = {"q": chess.QUEEN, "r": chess.ROOK,
                           "b": chess.BISHOP, "n": chess.KNIGHT}
                promo = mapping.get(choice, chess.QUEEN)
                chosen = next((m for m in candidates if m.promotion == promo), candidates[0])
                self._commit_move(chosen)
            self.push_screen(PromotionScreen(_on_promo))
            return
        self._commit_move(candidates[0])

    def _commit_move(self, move: chess.Move) -> None:
        san = self.game.live_board().san(move)
        if self._puzzle_session is not None and self._puzzle_session.is_human_turn:
            ok = self._puzzle_session.attempt(move)
            if ok:
                # Sync the puzzle session board into the game for rendering.
                self.game.board = self._puzzle_session.board.copy()
                self.game.start_board = self.game.board.copy()  # fresh
                self.game.start_board = chess.Board(self._puzzle_session.puzzle.fen)
                # rebuild move_stack so san_history() matches
                self._rebuild_stack_from_puzzle()
                self._log(f"[green]✓[/] {san}")
                if self._puzzle_session.solved:
                    self._flash("★ puzzle solved!")
                    self._log(f"[bold green]★ solved[/] — puzzle "
                              f"{self._puzzle_session.puzzle.puzzle_id}")
            else:
                self._log(f"[red]✗[/] {san} — expected "
                          f"[bold]{self._puzzle_session.puzzle.moves_uci[self._puzzle_session_played_before()]}[/]")
                self._flash("wrong — press R for a new puzzle")
        else:
            ok = self.game.try_move(move)
            if not ok:
                self._flash("illegal move")
                return
            self._log(f"{self.game.ply_cursor}. {san}")

        bv = self.board_view
        if bv is not None:
            bv.selected = None
        self._kick_analysis()
        self._refresh_all_panels()
        # Vs-engine — if it's now the engine's turn, schedule a reply.
        if self.game.mode == Mode.ENGINE and self._puzzle_session is None:
            if self.game.live_board().turn != self.game.human_color and not self.game.is_over():
                self.set_timer(0.05, self._engine_reply)

    def _puzzle_session_played_before(self) -> int:
        # Internal index used for the "expected move" log message when the
        # user attempts a wrong move.
        ps = self._puzzle_session
        if ps is None:
            return 0
        # ps._played still equals whatever was before attempt() failed.
        return ps._played + 1  # +1 because moves_uci[0] was the setup move

    def _rebuild_stack_from_puzzle(self) -> None:
        """After a puzzle move, sync game.board.move_stack so the SAN list is
        consistent with what's displayed."""
        ps = self._puzzle_session
        if ps is None:
            return
        start = chess.Board(ps.puzzle.fen)
        self.game.start_board = start
        replay = start.copy()
        stack = [chess.Move.from_uci(u) for u in ps.puzzle.moves_uci[:ps._played + 1]]
        for mv in stack:
            if mv in replay.legal_moves:
                replay.push(mv)
        self.game.board = replay

    def _engine_reply(self) -> None:
        """Have the vs-engine mode's non-player side move."""
        if not self._engine_specs:
            return
        spec = self._engine_specs[0]  # stockfish by default
        board = self.game.live_board()
        try:
            mv = bestmove(spec, board, time_limit=self._engine_time)
        except Exception:
            log.exception("engine reply failed")
            self._log("[red]engine reply failed[/]")
            return
        if mv is None or mv not in board.legal_moves:
            return
        san = board.san(mv)
        self.game.try_move(mv)
        self._log(f"[cyan]engine[/] {self.game.ply_cursor}. {san}")
        self._kick_analysis()
        self._refresh_all_panels()

    # --------------------------------------------------------------- actions

    def action_undo(self) -> None:
        if self.game.undo():
            self._flash("undo")
            self._kick_analysis()
            self._refresh_all_panels()
        else:
            self._flash("nothing to undo")

    def action_step(self, delta: int) -> None:
        self.game.step(delta)
        self._kick_analysis()
        self._refresh_all_panels()

    def action_history_start(self) -> None:
        self.game.to_start()
        self._kick_analysis()
        self._refresh_all_panels()

    def action_history_end(self) -> None:
        self.game.to_end()
        self._kick_analysis()
        self._refresh_all_panels()

    def action_toggle_analysis(self) -> None:
        if self._analyzer is None:
            self._start_analyzer()
            self._analysis_enabled = True
            self._flash("analysis ON")
        else:
            self._stop_analyzer()
            self._analysis_enabled = False
            self._flash("analysis OFF")

    def action_toggle_explorer(self) -> None:
        # Explorer is always-on if book is available; this toggles the
        # visible content to "hidden" for screen recording cleanliness.
        panel = self.explorer_panel
        if panel is None:
            return
        panel.display = not panel.display
        self._flash("explorer " + ("shown" if panel.display else "hidden"))

    def action_bump_multipv(self, delta: int) -> None:
        self._multipv = max(1, min(8, self._multipv + delta))
        self._flash(f"MultiPV = {self._multipv}")
        if self._analyzer is not None:
            # restart to pick up new multipv
            self._stop_analyzer()
            self._start_analyzer()

    def action_play_best(self) -> None:
        snap = self._best_snapshot()
        if snap is None or snap.best is None or not snap.best.pv:
            self._flash("no engine move available yet")
            return
        mv = snap.best.pv[0]
        self._commit_move(mv)

    def action_hint(self) -> None:
        snap = self._best_snapshot()
        bv = self.board_view
        if snap is None or snap.best is None or not snap.best.pv or bv is None:
            self._flash("no hint yet")
            return
        bv.set_best_move(snap.best.pv[0])
        bv.refresh()
        self._flash(f"hint: {snap.best.pv_san[0] if snap.best.pv_san else snap.best.pv[0].uci()}")

    def action_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
            return
        self.push_screen(HelpScreen())

    def action_new_game_dialog(self) -> None:
        self.push_screen(NewGameScreen(self._on_new_game_choice))

    def _on_new_game_choice(self, choice: str) -> None:
        mode_map = {
            "h": (Mode.HOTSEAT, chess.WHITE),
            "e": (Mode.ENGINE,  chess.WHITE),
            "b": (Mode.ENGINE,  chess.BLACK),
            "a": (Mode.ANALYSIS,chess.WHITE),
            "p": (Mode.PUZZLE,  chess.WHITE),
        }
        mode, color = mode_map.get(choice, (Mode.HOTSEAT, chess.WHITE))
        if mode == Mode.PUZZLE:
            self.action_new_puzzle()
            return
        self.game = Game(mode=mode, human_color=color)
        self._puzzle_session = None
        if self.board_view is not None:
            self.board_view.selected = None
            self.board_view.set_best_move(None)
        self._flash(f"new {mode.value} game")
        self._log(f"[bold]new game[/] — {mode.value}")
        self._kick_analysis()
        self._refresh_all_panels()
        if mode == Mode.ENGINE and color == chess.BLACK:
            # engine moves first (as white)
            self.set_timer(0.05, self._engine_reply)

    def action_new_puzzle(self) -> None:
        if not self._puzzles:
            self._flash("no puzzles loaded")
            return
        pz = pick_puzzle(self._puzzles, rng=self._puzzle_rng)
        if pz is None:
            self._flash("no puzzle matches filters")
            return
        sess = PuzzleSession(pz)
        self._puzzle_session = sess
        # Build a game whose start board is the puzzle's FEN, then apply
        # the setup move so the presented position is move #1.
        start = chess.Board(pz.fen)
        self.game = Game(board=start, mode=Mode.PUZZLE)
        self.game.start_board = start.copy()
        self.game.board = start.copy()
        self.game.board.push(pz.setup_move)
        if self.board_view is not None:
            self.board_view.selected = None
            self.board_view.set_best_move(None)
            self.board_view.flipped = (sess.board.turn == chess.BLACK)
        side = "white" if sess.board.turn == chess.WHITE else "black"
        self._log(f"[bold]puzzle[/] {pz.puzzle_id} · {pz.rating} · {' '.join(pz.themes)}")
        self._flash(f"your move — {side} to play ({len(pz.solution_uci)} plies)")
        self._kick_analysis()
        self._refresh_all_panels()

    def action_save_pgn(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        p = Path(f"game-{stamp}.pgn")
        try:
            self.game.write_pgn(p)
        except Exception as e:
            self._flash(f"save failed: {e}")
            return
        self._flash(f"saved → {p}")
        self._log(f"[cyan]saved[/] {p.resolve()}")

    def action_load_pgn(self) -> None:
        # Minimal version — arg-level load only for now; a proper file
        # picker would be phase-E polish.
        self._flash("load: pass --pgn PATH at launch for now")

    # --------------------------------------------------------------- refresh

    def _refresh_all_panels(self) -> None:
        self._refresh_overlays()
        self._refresh_status()
        self._refresh_movelist()
        self._refresh_analysis()
        self._refresh_explorer()

    def _refresh_overlays(self) -> None:
        bv = self.board_view
        if bv is None:
            return
        board = self.game.live_board()
        # last move
        last: Optional[chess.Move] = None
        if self.game.ply_cursor > 0:
            last = self.game.board.move_stack[self.game.ply_cursor - 1]
        bv.set_last_move(last)
        # legal dest highlights for selected square
        dests: list[int] = []
        caps: list[int] = []
        if bv.selected is not None:
            for m in board.legal_moves:
                if m.from_square == bv.selected:
                    if board.is_capture(m):
                        caps.append(m.to_square)
                    else:
                        dests.append(m.to_square)
        bv.set_legal_hints(dests, caps)
        # book dests
        book_dests: list[int] = []
        for e in self._book.entries(board):
            book_dests.append(e.move.to_square)
        bv.set_book_destinations(book_dests)
        bv.refresh()

    def _refresh_status(self) -> None:
        if self.status_panel is None:
            return
        board = self.game.live_board()
        turn = "white" if board.turn == chess.WHITE else "black"
        ply = self.game.ply_cursor
        total = self.game.total_plies
        nav = f" · viewing {ply}/{total}" if ply != total else ""
        mode = self.game.mode.value
        parts = [
            f"[bold]{mode}[/] · {turn} to move · ply {ply}{nav}",
        ]
        if board.is_check():
            parts.append("[bold red]CHECK[/]")
        outcome = self.game.outcome_text() if self.game.is_over() else ""
        if outcome:
            parts.append(f"[bold rgb(255,200,120)]{outcome}[/]")
        if self._puzzle_session is not None:
            pz = self._puzzle_session
            if pz.solved:
                parts.append("[green]★ puzzle solved[/]")
            elif pz.failed:
                parts.append("[red]✗ puzzle failed[/]")
            else:
                parts.append(f"[cyan]puzzle {pz.puzzle.puzzle_id}[/] ({pz.puzzle.rating})")
        self.status_panel.update("\n".join(parts))

    def _refresh_movelist(self) -> None:
        if self.movelist_panel is None:
            return
        san = self.game.san_history()
        cursor = self.game.ply_cursor
        out = Text()
        out.append("[move list]\n", Style(dim=True))
        for i, move_san in enumerate(san):
            if i % 2 == 0:
                out.append(f"{(i // 2) + 1:>3}. ",
                           Style(color="rgb(110,120,140)", dim=True))
            st = Style(color="rgb(230,230,240)", bold=(i + 1 == cursor))
            if i + 1 == cursor:
                st = Style(bgcolor="rgb(50,70,110)", color="rgb(255,255,255)", bold=True)
            out.append(move_san, st)
            out.append("  " if i % 2 == 0 else "\n")
        self.movelist_panel.update(out)

    def _refresh_analysis(self) -> None:
        if self.analysis_panel is None:
            return
        if not self._analysis_enabled:
            self.analysis_panel.update("[dim]analysis disabled — press 'a' to enable[/]")
            return
        if not self._engine_specs:
            self.analysis_panel.update("[dim]no engines detected — install stockfish[/]")
            return
        snaps: list[AnalysisSnapshot] = []
        if self._analyzer is not None:
            for s in self._analyzer._specs:  # stable order
                snap = self._analyzer.snapshot(s.name)
                if snap is not None:
                    snaps.append(snap)
        out = Text()
        if not snaps:
            out.append("[dim]engines starting…[/]", Style())
            self.analysis_panel.update(out)
            return
        for snap in snaps:
            best = snap.best
            out.append(f"{snap.engine:10s}", Style(color="rgb(180,200,230)", bold=True))
            if best is None:
                out.append("  thinking…\n")
                continue
            out.append(f"  d{best.depth}/{best.seldepth}  "
                       f"{best.nps/1000:.0f}kn/s  "
                       f"{best.nodes:,}n\n",
                       Style(color="rgb(140,150,170)"))
            out.append("  ")
            out.append(_eval_bar(best.score_cp, best.mate_in,
                                 width=22, turn_white=snap.turn_white))
            out.append("\n")
            for line in snap.lines[: self._multipv]:
                score = _format_score(line.score_cp, line.mate_in)
                out.append(f"    {line.rank}. ",
                           Style(color="rgb(110,120,140)"))
                out.append(f"{score:>7s} ",
                           Style(color="rgb(200,220,250)", bold=True))
                pv_text = " ".join(line.pv_san[:10])
                if len(line.pv_san) > 10:
                    pv_text += " …"
                out.append(pv_text, Style(color="rgb(230,230,240)"))
                out.append("\n")
            out.append("\n")
        self.analysis_panel.update(out)

    def _refresh_explorer(self) -> None:
        if self.explorer_panel is None:
            return
        if not self._book.available:
            self.explorer_panel.update("[dim]opening book not available[/]")
            return
        board = self.game.live_board()
        entries = self._book.entries(board)
        out = Text()
        out.append("[opening explorer] ", Style(color="rgb(180,200,230)", bold=True))
        out.append(f"{len(entries)} continuations\n",
                   Style(color="rgb(110,120,140)"))
        if not entries:
            out.append("  [dim]out of book[/]", Style())
            self.explorer_panel.update(out)
            return
        for e in entries[:8]:
            pct = f"{e.share*100:5.1f}%"
            out.append(f"  {e.san:8s} ",
                       Style(color="rgb(230,230,240)", bold=True))
            out.append(f"w={e.weight:>5d}  {pct}\n",
                       Style(color="rgb(140,160,200)"))
        self.explorer_panel.update(out)

    # --------------------------------------------------------------- utils

    def _best_snapshot(self) -> Optional[AnalysisSnapshot]:
        if self._analyzer is None:
            return None
        snaps = self._analyzer.all_snapshots()
        # Prefer stockfish if available.
        for name in ("stockfish", "lc0", "gnuchess"):
            if name in snaps:
                return snaps[name]
        return next(iter(snaps.values()), None)

    def _flash(self, msg: str) -> None:
        if self.flash_panel is not None:
            self.flash_panel.update(msg)

    def _log(self, msg: str) -> None:
        if self.log_panel is not None:
            self.log_panel.write(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(mode: str = "hotseat",
        fen: Optional[str] = None,
        pgn: Optional[Path] = None,
        puzzle_path: Optional[Path] = None,
        no_analysis: bool = False,
        multipv: int = 3,
        engine_time: float = 0.3,
        agent_port: Optional[int] = None,
        ) -> None:
    game_mode = Mode(mode)
    start_fen = fen
    specs = discover_engines()
    app = ChessApp(
        mode=game_mode,
        start_fen=start_fen,
        engines=specs,
        multipv=multipv,
        engine_time=engine_time,
        analysis_enabled=(not no_analysis),
        puzzle_path=puzzle_path,
        agent_port=agent_port,
    )
    if pgn is not None:
        g = Game.read_pgn(Path(pgn))
        app.game = g
    app.run()
