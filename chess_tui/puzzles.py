"""Lichess puzzle DB loader (CC0).

The full ``lichess_db_puzzle.csv`` is ~600 MB. We ship a tiny hand-
curated sample (``data/puzzles_sample.csv``) covering the common mate-
in-1 / tactical-shot patterns so the puzzle mode is immediately usable
without a 600 MB download.

CSV schema (Lichess columns):
    PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,GameUrl,OpeningTags

We read only: PuzzleId, FEN, Moves, Rating, Themes.

``Moves`` is a whitespace-separated list of UCI moves. The FIRST move
is the opponent's move (to reach the puzzle position as presented);
the remaining moves are the expected solution, white → black → white …
alternating.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import chess


SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "puzzles_sample.csv"


@dataclass
class Puzzle:
    puzzle_id: str
    fen: str                 # position BEFORE the setup move is played
    moves_uci: List[str]     # first = setup, rest = solution sequence
    rating: int
    themes: List[str]

    @property
    def setup_move(self) -> chess.Move:
        return chess.Move.from_uci(self.moves_uci[0])

    @property
    def solution_uci(self) -> List[str]:
        return self.moves_uci[1:]

    def presented_board(self) -> chess.Board:
        """Board the player sees — after the setup move was played."""
        b = chess.Board(self.fen)
        b.push(self.setup_move)
        return b

    def solution_moves(self) -> List[chess.Move]:
        b = self.presented_board()
        out: list[chess.Move] = []
        for uci in self.solution_uci:
            mv = chess.Move.from_uci(uci)
            if mv not in b.legal_moves:
                break
            out.append(mv)
            b.push(mv)
        return out


def load_puzzles(path: Optional[Path] = None) -> List[Puzzle]:
    path = path or SAMPLE_PATH
    if not path.exists():
        return []
    out: list[Puzzle] = []
    with path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                out.append(Puzzle(
                    puzzle_id=row["PuzzleId"],
                    fen=row["FEN"],
                    moves_uci=row["Moves"].split(),
                    rating=int(row.get("Rating") or 0),
                    themes=(row.get("Themes") or "").split(),
                ))
            except Exception:
                continue
    return out


def pick_puzzle(puzzles: List[Puzzle], *, rng: Optional[random.Random] = None,
                min_rating: int = 0, max_rating: int = 99_999,
                theme: Optional[str] = None) -> Optional[Puzzle]:
    rng = rng or random.Random()
    pool = [p for p in puzzles
            if min_rating <= p.rating <= max_rating
            and (theme is None or theme in p.themes)]
    if not pool:
        return None
    return rng.choice(pool)


class PuzzleSession:
    """Stateful solver — tracks progress through a puzzle's solution."""

    def __init__(self, puzzle: Puzzle) -> None:
        self.puzzle = puzzle
        self._solution = puzzle.solution_moves()
        self._played = 0
        self.board = puzzle.presented_board()
        self.failed: bool = False
        self.solved: bool = False

    @property
    def next_expected(self) -> Optional[chess.Move]:
        if self._played >= len(self._solution):
            return None
        return self._solution[self._played]

    @property
    def is_human_turn(self) -> bool:
        return not (self.failed or self.solved) and self._played % 2 == 0

    def attempt(self, move: chess.Move) -> bool:
        """Player attempts a move. Returns True if it matches expected."""
        expected = self.next_expected
        if expected is None:
            return False
        if move != expected and not self._alt_equivalent(move, expected):
            self.failed = True
            return False
        self.board.push(move)
        self._played += 1
        if self._played >= len(self._solution):
            self.solved = True
            return True
        # Play opponent's reply automatically.
        opp = self.next_expected
        if opp is not None:
            self.board.push(opp)
            self._played += 1
            if self._played >= len(self._solution):
                self.solved = True
        return True

    def _alt_equivalent(self, played: chess.Move, expected: chess.Move) -> bool:
        # Lichess solutions sometimes accept any move that mates or wins
        # material equivalently. We only accept the literal move for
        # simplicity — mate-alternatives rare enough in our sample.
        return False
