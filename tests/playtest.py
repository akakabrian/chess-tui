"""End-to-end playtest via a real PTY.

Spawns ``play.py --no-analysis`` under pexpect, drives a handful of
keystrokes (move a pawn e2→e4, toggle flip, open help, quit), and
captures SVG screenshots via Textual's ``save_screenshot`` through the
Pilot would be impossible here — so we instead capture the live Textual
output by driving the Pilot-less app through the in-process scenario
harness but mirror the same key sequence.

The genuine PTY boot of ``play.py`` is verified separately — see the
``smoke_boot`` section at the bottom, which just confirms the process
reaches the main loop without crashing.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pexpect  # type: ignore[import-untyped]

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "tests" / "out"
OUT.mkdir(parents=True, exist_ok=True)


async def _driven_session() -> None:
    """Drive the app in-process and save sequential SVG snapshots."""
    from chess_tui.app import ChessApp
    from chess_tui.game import Mode
    from chess_tui.screens import HelpScreen

    app = ChessApp(engines=[], analysis_enabled=False, mode=Mode.HOTSEAT)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.save_screenshot(str(OUT / f"playtest_{stamp}_00_boot.svg"))

        # Move e2 → e4 via the cursor + select-then-move flow.
        # Cursor starts at e1 (file 4, rank 0).
        await pilot.press("up")            # cursor to e2
        await pilot.press("enter")         # select e2
        await pilot.pause()
        assert app.board_view is not None
        assert app.board_view.selected is not None, "e2 not selected"
        app.save_screenshot(str(OUT / f"playtest_{stamp}_01_selected.svg"))

        await pilot.press("up", "up")      # cursor to e4
        await pilot.press("enter")         # confirm move
        await pilot.pause()
        # Pause once more so the 0.4s-interval analysis refresh can tick
        # and panels settle.
        await pilot.pause()
        assert app.game.total_plies == 1, "e2→e4 didn't apply"
        app.save_screenshot(str(OUT / f"playtest_{stamp}_02_after_e4.svg"))

        # Analysis pane (even when disabled) should show the "disabled" msg
        # — confirm content updates after the move (movelist shows e4).
        assert app.movelist_panel is not None
        movelist_txt = app.movelist_panel.render()
        txt = getattr(movelist_txt, "plain", None) or str(movelist_txt)
        assert "e4" in txt, f"movelist missing e4:\n{txt}"

        # Toggle flip.
        await pilot.press("f")
        await pilot.pause()
        assert app.board_view.flipped is True, "board did not flip"
        app.save_screenshot(str(OUT / f"playtest_{stamp}_03_flipped.svg"))

        # Open help.
        app.action_help()
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen), \
            f"help screen not open — got {type(app.screen).__name__}"
        app.save_screenshot(str(OUT / f"playtest_{stamp}_04_help.svg"))
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen), "help screen didn't close"

        # Quit cleanly.
        app.save_screenshot(str(OUT / f"playtest_{stamp}_05_final.svg"))

    print(f"  driven session OK — 6 SVGs saved to {OUT}")


def smoke_boot_pty() -> None:
    """Spawn the real binary via PTY, wait for the UI to draw, then quit.

    This proves the entry-point is wired correctly (argparse, import path,
    CSS load, Textual App bootstrap) end-to-end — the in-process pilot
    session above can't catch regressions in play.py itself.
    """
    cmd = f'{sys.executable} -u play.py --no-analysis'
    child = pexpect.spawn(cmd, cwd=str(REPO), timeout=15,
                          dimensions=(50, 160), encoding="utf-8")
    try:
        # Textual draws the title in the header; poll the buffer for it.
        # We look for any of a few expected strings with a generous delay.
        deadline = time.monotonic() + 8.0
        seen = ""
        while time.monotonic() < deadline:
            try:
                chunk = child.read_nonblocking(size=4096, timeout=0.2)
                seen += chunk
                if "chess-tui" in seen or "analysis" in seen or "♟" in seen:
                    break
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                break
        assert ("chess-tui" in seen) or ("♟" in seen), \
            f"no UI text within 8s — got {seen[:400]!r}"
        # Send 'q' to quit.
        child.send("q")
        child.expect(pexpect.EOF, timeout=5)
        print("  pty smoke boot OK")
    finally:
        if child.isalive():
            child.terminate(force=True)


def main() -> int:
    print("chess-tui playtest")
    print("-" * 72)
    try:
        asyncio.run(_driven_session())
    except AssertionError as e:
        print(f"  DRIVEN SESSION FAILED: {e}")
        return 1
    except Exception as e:
        print(f"  DRIVEN SESSION ERROR: {type(e).__name__}: {e}")
        return 1

    try:
        smoke_boot_pty()
    except AssertionError as e:
        print(f"  PTY BOOT FAILED: {e}")
        return 1
    except Exception as e:
        print(f"  PTY BOOT ERROR: {type(e).__name__}: {e}")
        return 1

    print("\nplaytest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
