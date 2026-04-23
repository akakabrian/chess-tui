"""Performance micro-benchmarks for chess-tui hot paths.

Measures (all in milliseconds):

* ``board_render`` — full rich.Text build for the 8×8 BoardView.
* ``cursor_move``  — Pilot-driven arrow key → repainted board.
* ``move_apply``   — pushing a legal move and refreshing all panels.
* ``book_lookup``  — polyglot lookup at start position.
* ``analysis_snapshot`` — one engine.analysis tick read via the worker.
* ``pgn_roundtrip`` — write 40-ply PGN and parse it back.

Run via ``python -m tests.perf``. We print a one-line summary per test.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from typing import Any, Callable

import chess

from chess_tui.app import ChessApp
from chess_tui.board_view import BoardView
from chess_tui.book import OpeningBook
from chess_tui.engine import MultiEngineAnalyzer, discover_engines
from chess_tui.game import Game, Mode


def _bench(name: str, fn: Callable[[], Any], *,
           iters: int = 200, warmup: int = 10) -> None:
    for _ in range(warmup):
        fn()
    samples: list[float] = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    median = statistics.median(samples)
    mean = statistics.mean(samples)
    p95 = sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]
    print(f"  {name:24s}  median={median:6.3f} ms  "
          f"mean={mean:6.3f} ms  p95={p95:6.3f} ms  "
          f"n={iters}")


def bench_board_render() -> None:
    game = Game()
    bv = BoardView(board_getter=lambda: game.board)
    _bench("board_render", lambda: bv.render())


def bench_move_apply() -> None:
    """How expensive is try_move + san_history walk?"""
    def run():
        game = Game()
        moves = [
            chess.Move.from_uci(u) for u in
            ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6"]
        ]
        for m in moves:
            game.try_move(m)
        _ = game.san_history()
    _bench("move_apply_6", run)


def bench_book_lookup() -> None:
    book = OpeningBook()
    if not book.available:
        print("  book_lookup             SKIPPED (no polyglot book)")
        return
    board = chess.Board()
    _bench("book_lookup", lambda: book.entries(board))
    book.close()


def bench_pgn_roundtrip() -> None:
    game = Game()
    uci = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6",
           "b5a4", "g8f6", "e1g1", "f8e7", "f1e1", "b7b5",
           "a4b3", "d7d6", "c2c3", "e8g8", "h2h3", "c6a5",
           "b3c2", "c7c5", "d2d4", "d8c7"]
    for u in uci:
        game.try_move(chess.Move.from_uci(u))
    pgn = game.to_pgn()
    def run():
        Game.from_pgn(pgn)
    _bench("pgn_roundtrip_22", run)


async def bench_cursor_move_async() -> None:
    """Pilot-driven cursor move → one refresh cycle."""
    app = ChessApp(engines=[], analysis_enabled=False, mode=Mode.HOTSEAT)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        samples: list[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            await pilot.press("right")
            samples.append((time.perf_counter() - t0) * 1000.0)
            await pilot.press("left")
        median = statistics.median(samples)
        p95 = sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]
        print(f"  {'cursor_move_pilot':24s}  median={median:6.3f} ms  "
              f"p95={p95:6.3f} ms  n={len(samples)}")


def bench_cursor_move() -> None:
    asyncio.run(bench_cursor_move_async())


def bench_analysis_snapshot() -> None:
    specs = [s for s in discover_engines() if s.name == "stockfish"]
    if not specs:
        print("  analysis_snapshot       SKIPPED (no stockfish)")
        return
    analyzer = MultiEngineAnalyzer(specs, multipv=1, hash_mb=32, threads=1)
    try:
        analyzer.start()
        board = chess.Board()
        analyzer.set_position(board)
        # Wait for first snapshot.
        start = time.monotonic()
        snap = None
        while time.monotonic() - start < 5.0:
            snap = analyzer.snapshot("stockfish")
            if snap is not None and snap.best is not None:
                break
            time.sleep(0.02)
        t1 = time.monotonic() - start
        if snap is None:
            print("  analysis_snapshot       NO SNAPSHOT")
        else:
            print(f"  {'analysis_first_snap':24s}  "
                  f"time_to_first={t1*1000:7.1f} ms  "
                  f"depth={snap.best.depth if snap.best else '-'}")
    finally:
        analyzer.stop()


def main() -> None:
    print("chess-tui perf suite")
    print("-" * 72)
    bench_board_render()
    bench_move_apply()
    bench_book_lookup()
    bench_pgn_roundtrip()
    bench_cursor_move()
    bench_analysis_snapshot()


if __name__ == "__main__":
    main()
