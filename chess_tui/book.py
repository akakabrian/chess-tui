"""Polyglot opening book loader — graceful no-op when the book is
unavailable so the UI stays usable without a book."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import chess
import chess.polyglot

log = logging.getLogger("chess_tui.book")


# Candidate book paths. Users can override with $CHESS_TUI_BOOK.
CANDIDATE_BOOKS = [
    "/usr/share/games/gnuchess/book.bin",
    "/usr/share/gnuchess/book.bin",
]


@dataclass
class BookEntry:
    move: chess.Move
    san: str
    weight: int              # polyglot weight
    learn: int
    share: float             # weight / total_weight at this position


class OpeningBook:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path: Optional[Path] = None
        self._reader: Optional[chess.polyglot.MemoryMappedReader] = None
        self._try_open(path)

    def _try_open(self, override: Optional[str]) -> None:
        import os
        if override is None:
            override = os.environ.get("CHESS_TUI_BOOK")
        cands: list[str] = [override] if override else []
        cands.extend(CANDIDATE_BOOKS)
        for cand in cands:
            if not cand:
                continue
            p = Path(cand)
            if p.exists() and p.stat().st_size > 0:
                try:
                    self._reader = chess.polyglot.MemoryMappedReader(str(p))
                    self.path = p
                    log.info("opening book loaded: %s (%d bytes)", p, p.stat().st_size)
                    return
                except Exception:
                    log.warning("polyglot open failed for %s", p, exc_info=True)
        log.info("no polyglot book available; opening explorer will be empty")

    @property
    def available(self) -> bool:
        return self._reader is not None

    def close(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
            self._reader = None

    def entries(self, board: chess.Board) -> List[BookEntry]:
        if self._reader is None:
            return []
        try:
            raw = list(self._reader.find_all(board))
        except Exception:
            return []
        if not raw:
            return []
        total = sum(e.weight for e in raw) or 1
        out: list[BookEntry] = []
        for e in raw:
            mv = e.move
            if mv not in board.legal_moves:
                # Polyglot may encode castling as king-takes-rook — normalize.
                mv = _normalize_castling(board, mv)
                if mv is None or mv not in board.legal_moves:
                    continue
            out.append(BookEntry(
                move=mv,
                san=board.san(mv),
                weight=e.weight,
                learn=getattr(e, "learn", 0),
                share=e.weight / total,
            ))
        out.sort(key=lambda be: -be.weight)
        return out


def _normalize_castling(board: chess.Board, mv: chess.Move) -> Optional[chess.Move]:
    """Polyglot sometimes stores castling as king→rook. Convert to the
    standard king→two-squares form that ``board.legal_moves`` contains."""
    if not board.piece_at(mv.from_square):
        return None
    piece = board.piece_at(mv.from_square)
    if piece is None or piece.piece_type != chess.KING:
        return None
    fr, fc = chess.square_rank(mv.from_square), chess.square_file(mv.from_square)
    tr, tc = chess.square_rank(mv.to_square),   chess.square_file(mv.to_square)
    if fr != tr:
        return None
    if tc == 7:
        target = chess.square(6, fr)
    elif tc == 0:
        target = chess.square(2, fr)
    else:
        return None
    candidate = chess.Move(mv.from_square, target)
    return candidate if candidate in board.legal_moves else None
