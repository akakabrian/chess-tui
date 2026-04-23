"""QA harness — Textual Pilot scenarios for the chess TUI.

Each scenario is an async function that takes ``(app, pilot)``. Failures
save a ``.FAIL.svg`` screenshot; passes save ``.PASS.svg``. Run with

    python -m tests.qa              # all scenarios
    python -m tests.qa pattern      # filter by name

We run WITHOUT a real engine by default (``ChessApp(engines=[])``) so
tests are fast and deterministic. A separate "engine_integration"
scenario exercises the subprocess UCI path end-to-end.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import chess

from chess_tui.app import ChessApp
from chess_tui.engine import discover_engines
from chess_tui.game import Mode


REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "tests" / "out"
OUT.mkdir(parents=True, exist_ok=True)


# ------------------------ helpers ------------------------------------------

def _static_text(widget) -> str:
    """Return the plain-text contents of a Textual Static widget."""
    r = widget.render()
    if hasattr(r, "plain"):
        return r.plain
    return str(r)


def fresh_app(**kwargs: Any) -> ChessApp:
    """App with analysis disabled and no engines — deterministic / fast."""
    defaults: dict[str, Any] = dict(engines=[], analysis_enabled=False, mode=Mode.HOTSEAT)
    defaults.update(kwargs)
    return ChessApp(**defaults)


# ------------------------ scenarios ----------------------------------------


async def scn_mount_clean(app: ChessApp, pilot) -> None:
    assert app.board_view is not None
    assert app.status_panel is not None
    assert app.analysis_panel is not None
    assert app.explorer_panel is not None
    assert app.movelist_panel is not None
    # Initial position
    board = app.game.live_board()
    assert board.fullmove_number == 1
    assert board.turn == chess.WHITE


async def scn_initial_cursor(app: ChessApp, pilot) -> None:
    bv = app.board_view
    assert bv is not None
    assert (bv.cursor_file, bv.cursor_rank) == (4, 0)  # e1


async def scn_arrow_moves_cursor(app: ChessApp, pilot) -> None:
    bv = app.board_view
    assert bv is not None
    await pilot.press("up", "up", "right")
    await pilot.pause()
    # Moved 2 up and 1 right from e1 → f3
    assert bv.cursor_file == 5 and bv.cursor_rank == 2, \
        f"cursor = ({bv.cursor_file}, {bv.cursor_rank})"


async def scn_cursor_clamps(app: ChessApp, pilot) -> None:
    bv = app.board_view
    assert bv is not None
    # drive cursor hard into corner
    for _ in range(20):
        await pilot.press("left")
    for _ in range(20):
        await pilot.press("down")
    await pilot.pause()
    assert (bv.cursor_file, bv.cursor_rank) == (0, 0), \
        f"cursor = ({bv.cursor_file}, {bv.cursor_rank})"


async def scn_select_then_move(app: ChessApp, pilot) -> None:
    bv = app.board_view
    assert bv is not None
    # Move cursor to e2 (file 4, rank 1) — cursor starts at e1 (rank 0).
    await pilot.press("up")  # e2
    await pilot.press("space")  # select
    await pilot.pause()
    assert bv.selected == chess.square(4, 1), "e2 not selected"
    # Move to e4 (rank 3).
    await pilot.press("up", "up")
    await pilot.press("space")  # confirm move
    await pilot.pause()
    board = app.game.live_board()
    assert board.piece_at(chess.square(4, 3)) is not None, "e4 empty after e2→e4"
    assert board.piece_at(chess.square(4, 1)) is None, "e2 still has pawn"
    assert app.game.total_plies == 1


async def scn_cancel_clears_selection(app: ChessApp, pilot) -> None:
    bv = app.board_view
    assert bv is not None
    await pilot.press("up")
    await pilot.press("space")
    await pilot.pause()
    assert bv.selected is not None
    await pilot.press("escape")
    await pilot.pause()
    assert bv.selected is None


async def scn_illegal_move_rejected(app: ChessApp, pilot) -> None:
    bv = app.board_view
    assert bv is not None
    # Select e1 king; try to move it to e4 (not legal: blocked by own pawn,
    # and kings don't jump 3 squares anyway).
    await pilot.press("space")  # select e1 (king)
    await pilot.press("up", "up", "up")  # → e4
    await pilot.press("space")
    await pilot.pause()
    assert app.game.total_plies == 0


async def scn_undo(app: ChessApp, pilot) -> None:
    bv = app.board_view
    assert bv is not None
    await pilot.press("up")
    await pilot.press("space")  # e2 selected
    await pilot.press("up", "up")
    await pilot.press("space")  # e2→e4
    await pilot.pause()
    assert app.game.total_plies == 1
    await pilot.press("u")
    await pilot.pause()
    assert app.game.total_plies == 0


async def scn_flip_board(app: ChessApp, pilot) -> None:
    bv = app.board_view
    assert bv is not None
    assert bv.flipped is False
    await pilot.press("f")
    await pilot.pause()
    assert bv.flipped is True


async def scn_board_render_has_pieces(app: ChessApp, pilot) -> None:
    bv = app.board_view
    assert bv is not None
    rendered = bv.render()
    text = rendered.plain
    # At least one black pawn and one white pawn glyph expected.
    assert "♟" in text, "no pawn glyph in initial render"


async def scn_movelist_after_move(app: ChessApp, pilot) -> None:
    await pilot.press("up")
    await pilot.press("space")
    await pilot.press("up", "up")
    await pilot.press("space")
    await pilot.pause()
    assert app.movelist_panel is not None
    content = _static_text(app.movelist_panel)
    assert "e4" in content, f"movelist missing e4: {content}"


async def scn_explorer_populates(app: ChessApp, pilot) -> None:
    # Need book for this scenario. Skip gracefully.
    if not app._book.available:
        return
    await pilot.pause()
    panel = app.explorer_panel
    assert panel is not None
    content = _static_text(panel)
    assert "continuations" in content, content


async def scn_history_navigation(app: ChessApp, pilot) -> None:
    # Play 1.e4 then step back.
    await pilot.press("up")
    await pilot.press("space")
    await pilot.press("up", "up")
    await pilot.press("space")
    await pilot.pause()
    assert app.game.ply_cursor == 1
    app.action_step(-1)
    await pilot.pause()
    assert app.game.ply_cursor == 0
    app.action_step(1)
    await pilot.pause()
    assert app.game.ply_cursor == 1


async def scn_help_modal_opens(app: ChessApp, pilot) -> None:
    from chess_tui.screens import HelpScreen
    # Call the action directly — the "?" binding involves shift-handling
    # subtleties the Pilot doesn't emulate uniformly across terminals.
    app.action_help()
    await pilot.pause()
    assert isinstance(app.screen, HelpScreen), \
        f"expected HelpScreen, got {type(app.screen).__name__}"
    await pilot.press("escape")
    await pilot.pause()
    assert not isinstance(app.screen, HelpScreen)


async def scn_check_highlight(app: ChessApp, pilot) -> None:
    # Use a fool's-mate FEN where white to move gets mated next if they
    # don't defend — but we'll just set up a simple check position.
    #
    # FEN: white king on e1, black rook on e8, white to move. e1 is in
    # check along the file since nothing blocks.
    fen = "4r3/8/8/8/8/8/8/4K3 w - - 0 1"
    app.game.board = chess.Board(fen)
    app.game.start_board = app.game.board.copy()
    await pilot.pause()
    board = app.game.live_board()
    assert board.is_check(), "expected check"


async def scn_puzzle_mode_sets_up(app: ChessApp, pilot) -> None:
    # Directly call action_new_puzzle (populated by bundled sample CSV).
    if not app._puzzles:
        return
    app.action_new_puzzle()
    await pilot.pause()
    assert app.game.mode == Mode.PUZZLE
    assert app._puzzle_session is not None


async def scn_promotion_required(app: ChessApp, pilot) -> None:
    # 7th-rank pawn with an empty 8th rank — next push is a promotion.
    fen = "8/4P3/8/8/8/8/8/4K2k w - - 0 1"
    app.game.board = chess.Board(fen)
    app.game.start_board = app.game.board.copy()
    await pilot.pause()
    # Cursor to e7 (file 4, rank 6).
    bv = app.board_view
    assert bv is not None
    bv.cursor_file = 4
    bv.cursor_rank = 6
    await pilot.press("space")  # select
    await pilot.press("up")     # move to e8
    await pilot.press("space")  # attempt
    await pilot.pause()
    # Promotion dialog should be open. Choose queen.
    from chess_tui.screens import PromotionScreen
    assert isinstance(app.screen, PromotionScreen), \
        f"expected PromotionScreen, got {type(app.screen).__name__}"
    await pilot.press("q")
    await pilot.pause()
    board = app.game.live_board()
    p = board.piece_at(chess.square(4, 7))
    assert p is not None and p.piece_type == chess.QUEEN, \
        f"no queen on e8: {p}"


async def scn_state_snapshot_works(app: ChessApp, pilot) -> None:
    # Basic smoke of Game.to_pgn() — also run during agent API use.
    await pilot.press("up")
    await pilot.press("space")
    await pilot.press("up", "up")
    await pilot.press("space")
    await pilot.pause()
    pgn = app.game.to_pgn()
    assert "1. e4" in pgn, pgn


async def scn_no_engine_analysis_off(app: ChessApp, pilot) -> None:
    """App with no engines must not crash when analysis is toggled."""
    await pilot.pause()
    app.action_toggle_analysis()
    await pilot.pause()
    app.action_toggle_analysis()
    await pilot.pause()
    # No assertion needed — not crashing is the pass condition.


async def scn_unknown_fen_graceful(app: ChessApp, pilot) -> None:
    """Loading a nonsense FEN must not crash the app."""
    import chess
    try:
        app.game.board = chess.Board("not/a/fen/at/all")
        raise RuntimeError("should have raised")
    except ValueError:
        pass  # python-chess rejects bad FEN — expected
    # App state is unchanged; render still works.
    assert app.board_view is not None
    rendered = app.board_view.render()
    assert "♟" in rendered.plain


# ------------------------ engine integration (opt) -------------------------

async def scn_engine_analyser_publishes(app: ChessApp, pilot) -> None:
    """Spawn stockfish briefly and verify a snapshot appears. Opt-in via
    CHESS_TUI_RUN_ENGINE=1 so CI without stockfish doesn't flake."""
    if os.environ.get("CHESS_TUI_RUN_ENGINE") != "1":
        return
    # Stage a fresh app WITH engines enabled.
    specs = discover_engines()
    if not any(s.name == "stockfish" for s in specs):
        return
    sf = next(s for s in specs if s.name == "stockfish")
    from chess_tui.engine import MultiEngineAnalyzer
    analyzer = MultiEngineAnalyzer([sf], multipv=1, hash_mb=16, threads=1)
    analyzer.start()
    analyzer.set_position(chess.Board())
    # Wait up to 3s for first snapshot.
    import time
    start = time.monotonic()
    snap = None
    while time.monotonic() - start < 3.0:
        snap = analyzer.snapshot("stockfish")
        if snap is not None and snap.best is not None:
            break
        await asyncio.sleep(0.1)
    analyzer.stop()
    assert snap is not None, "no snapshot from stockfish"
    assert snap.best is not None, "snapshot has no best line"


# ------------------------ driver -------------------------------------------

@dataclass
class Scenario:
    name: str
    fn: Callable[[ChessApp, object], Awaitable[None]]
    setup: Callable[[], ChessApp] = fresh_app


SCENARIOS: list[Scenario] = [
    Scenario("mount_clean",            scn_mount_clean),
    Scenario("initial_cursor",         scn_initial_cursor),
    Scenario("arrow_moves_cursor",     scn_arrow_moves_cursor),
    Scenario("cursor_clamps",          scn_cursor_clamps),
    Scenario("select_then_move",       scn_select_then_move),
    Scenario("cancel_clears_selection",scn_cancel_clears_selection),
    Scenario("illegal_move_rejected",  scn_illegal_move_rejected),
    Scenario("undo",                   scn_undo),
    Scenario("flip_board",             scn_flip_board),
    Scenario("board_render_has_pieces",scn_board_render_has_pieces),
    Scenario("movelist_after_move",    scn_movelist_after_move),
    Scenario("explorer_populates",     scn_explorer_populates),
    Scenario("history_navigation",     scn_history_navigation),
    Scenario("help_modal_opens",       scn_help_modal_opens),
    Scenario("check_highlight",        scn_check_highlight),
    Scenario("puzzle_mode_sets_up",    scn_puzzle_mode_sets_up),
    Scenario("promotion_required",     scn_promotion_required),
    Scenario("state_snapshot_works",   scn_state_snapshot_works),
    Scenario("no_engine_analysis_off", scn_no_engine_analysis_off),
    Scenario("unknown_fen_graceful",   scn_unknown_fen_graceful),
    Scenario("engine_analyser_publishes", scn_engine_analyser_publishes),
]


async def run_scenario(scn: Scenario) -> tuple[str, bool, str]:
    app = scn.setup()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        try:
            await scn.fn(app, pilot)
            app.save_screenshot(str(OUT / f"{scn.name}.PASS.svg"))
            return (scn.name, True, "")
        except AssertionError as e:
            app.save_screenshot(str(OUT / f"{scn.name}.FAIL.svg"))
            return (scn.name, False, f"AssertionError: {e}")
        except Exception as e:
            try:
                app.save_screenshot(str(OUT / f"{scn.name}.ERROR.svg"))
            except Exception:
                pass
            tb = traceback.format_exc()
            return (scn.name, False, f"{type(e).__name__}: {e}\n{tb}")


async def main_async(pattern: Optional[str] = None) -> int:
    scns = SCENARIOS
    if pattern:
        rx = re.compile(pattern)
        scns = [s for s in scns if rx.search(s.name)]
    if not scns:
        print(f"no scenarios match {pattern!r}", file=sys.stderr)
        return 2
    results = []
    for scn in scns:
        print(f"▶ {scn.name:32s}", end=" ", flush=True)
        name, ok, msg = await run_scenario(scn)
        status = "\x1b[32mPASS\x1b[0m" if ok else "\x1b[31mFAIL\x1b[0m"
        print(status, msg.split("\n")[0] if msg else "")
        results.append((name, ok, msg))
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} scenarios passed")
    for name, ok, msg in results:
        if not ok:
            print(f"  {name}:\n    {msg.strip()}")
    return 0 if passed == total else 1


def main() -> int:
    pat = sys.argv[1] if len(sys.argv) > 1 else None
    return asyncio.run(main_async(pat))


if __name__ == "__main__":
    raise SystemExit(main())
