"""BoardView widget — 8×8 chess board renderer.

Each square renders as a 3-char cell (`" ♛ "`). Total board = 24 wide +
1-col file labels on the left + 2-col rank labels on the right = 27ish
columns; we pad to 30 for aesthetics.

The render is a single ``rich.Text`` built on refresh; at 8×8 (~180
segments) that's <1 ms on any machine so we don't need the scrollview
render_line trick.
"""

from __future__ import annotations

from typing import Optional

import chess
from rich.text import Text
from textual import events
from textual.reactive import reactive
from textual.widget import Widget

from . import pieces


class BoardView(Widget):
    # Cursor coords are (file, rank) 0..7 in standard chess indexing
    # (file 0 = 'a', rank 0 = 1st). ``flipped`` mirrors the display so
    # black sits at bottom.
    cursor_file: reactive[int] = reactive(4)
    cursor_rank: reactive[int] = reactive(0)
    selected: reactive[Optional[int]] = reactive(None)  # selected src square
    flipped: reactive[bool] = reactive(False)

    WIDTH = 30
    HEIGHT = 10

    def __init__(self, board_getter, *, show_coords: bool = True, **kw):
        super().__init__(**kw)
        # board_getter() returns the currently-rendered chess.Board. The
        # BoardView never stores a board — the App owns state.
        self._get_board = board_getter
        self.show_coords = show_coords
        # UI overlays — set by the app per-redraw via setters.
        self._last_move: Optional[chess.Move] = None
        self._legal_for_selected: list[int] = []
        self._legal_captures_for_selected: list[int] = []
        self._best_move: Optional[chess.Move] = None  # engine hint overlay
        self._book_moves: list[int] = []  # destination squares in book

    # ---- overlay setters (the app calls these then .refresh()) -----------

    def set_last_move(self, move: Optional[chess.Move]) -> None:
        self._last_move = move

    def set_legal_hints(self, legal_dests: list[int], capture_dests: list[int]) -> None:
        self._legal_for_selected = legal_dests
        self._legal_captures_for_selected = capture_dests

    def set_best_move(self, move: Optional[chess.Move]) -> None:
        self._best_move = move

    def set_book_destinations(self, squares: list[int]) -> None:
        self._book_moves = squares

    # ---- rendering -------------------------------------------------------

    def render(self) -> Text:
        board = self._get_board()
        t = Text()
        king_in_check_sq = _king_in_check_square(board)
        check_sq = king_in_check_sq

        ranks = range(7, -1, -1) if not self.flipped else range(0, 8)
        files = range(0, 8) if not self.flipped else range(7, -1, -1)

        # top file header
        if self.show_coords:
            t.append("  ", pieces.STYLE_COORD)
            for f in files:
                t.append(f" {chr(ord('a') + f)} ", pieces.STYLE_COORD)
            t.append("\n")

        for r in ranks:
            if self.show_coords:
                t.append(f"{r+1} ", pieces.STYLE_COORD)
            for f in files:
                sq = chess.square(f, r)
                bg = pieces.square_bg(sq)
                # overlays (order matters — later wins)
                if self._best_move is not None and sq in (self._best_move.from_square, self._best_move.to_square):
                    bg = pieces.BG_HINT
                if self._last_move is not None and sq in (self._last_move.from_square, self._last_move.to_square):
                    bg = pieces.BG_LASTMOVE
                if sq in self._legal_for_selected:
                    bg = pieces.BG_LEGAL
                if sq in self._legal_captures_for_selected:
                    bg = pieces.BG_LEGAL_CAPTURE
                if self.selected is not None and sq == self.selected:
                    bg = pieces.BG_SELECTED
                if check_sq is not None and sq == check_sq:
                    bg = pieces.BG_CHECK
                cursor_sq = chess.square(self.cursor_file, self.cursor_rank)
                if sq == cursor_sq:
                    bg = pieces.BG_CURSOR

                piece = board.piece_at(sq)
                if piece is not None:
                    glyph = pieces.piece_glyph(piece)
                    style = pieces.piece_style(piece, bg)
                    t.append(f" {glyph} ", style)
                else:
                    # Empty-square glyph — subtle dot on centre squares
                    # helps orient the eye without being loud.
                    g = "·" if ((f + r) & 1) == 0 else " "
                    if sq in self._legal_for_selected:
                        g = "•"
                    t.append(f" {g} ", pieces.empty_style(bg))
            if self.show_coords:
                t.append(f" {r+1}", pieces.STYLE_COORD)
            t.append("\n")

        if self.show_coords:
            t.append("  ", pieces.STYLE_COORD)
            for f in files:
                t.append(f" {chr(ord('a') + f)} ", pieces.STYLE_COORD)
        return t

    # ---- mouse ----------------------------------------------------------

    def on_click(self, event: events.Click) -> None:
        # Screen coords: row 0 = top file header, rows 1..8 = rank 8..1,
        # row 9 = bottom header. Col 0..1 = rank label, cols 2..25 = board
        # (3 cols per file), cols 26..27 = trailing rank label.
        y = event.y
        x = event.x
        if self.show_coords:
            row = y - 1
            col = x - 2
        else:
            row = y
            col = x
        if not (0 <= row < 8 and 0 <= col < 24):
            return
        f = col // 3
        # row 0 = topmost visible rank = rank 7 if not flipped else 0
        if not self.flipped:
            rank = 7 - row
            file_ = f
        else:
            rank = row
            file_ = 7 - f
        self.cursor_file = file_
        self.cursor_rank = rank
        # bubble up so the App can handle select/move
        self.post_message(BoardClicked(file_, rank, event.button == 3))


def _king_in_check_square(board: chess.Board) -> Optional[int]:
    if not board.is_check():
        return None
    king_sq = board.king(board.turn)
    return king_sq


# ---------------------------------------------------------------------------
# Custom messages — decouple the view from the App.

from textual.message import Message


class BoardClicked(Message):
    def __init__(self, file_: int, rank: int, right_button: bool = False) -> None:
        super().__init__()
        self.file = file_
        self.rank = rank
        self.right_button = right_button
        self.square = chess.square(file_, rank)
