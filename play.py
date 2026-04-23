"""Entry point — ``python play.py [--mode hotseat|engine|analysis|puzzle]``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(prog="chess-tui",
                                description="Terminal chess study workstation.")
    p.add_argument("--mode", choices=("hotseat", "engine", "analysis", "puzzle"),
                   default="hotseat", help="game mode (default: hotseat)")
    p.add_argument("--fen", default=None, help="start from this FEN")
    p.add_argument("--pgn", default=None, help="load this PGN on startup")
    p.add_argument("--puzzles", default=None,
                   help="path to a Lichess-format CSV (default: bundled sample)")
    p.add_argument("--no-analysis", action="store_true",
                   help="don't spawn engine analysis workers")
    p.add_argument("--multipv", type=int, default=3, help="engine MultiPV lines")
    p.add_argument("--engine-time", type=float, default=0.3,
                   help="seconds per engine move in vs-engine mode")
    p.add_argument("--agent-port", type=int, default=None,
                   help="expose the agent REST API on this port (localhost only)")
    args = p.parse_args()

    from chess_tui.app import run as run_app
    run_app(
        mode=args.mode,
        fen=args.fen,
        pgn=Path(args.pgn) if args.pgn else None,
        puzzle_path=Path(args.puzzles) if args.puzzles else None,
        no_analysis=args.no_analysis,
        multipv=args.multipv,
        engine_time=args.engine_time,
        agent_port=args.agent_port,
    )


if __name__ == "__main__":
    main()
