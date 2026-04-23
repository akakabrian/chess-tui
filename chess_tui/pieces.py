"""Unicode glyphs + styles for chess pieces and squares.

Terminal-safe single-cell glyphs are used. All piece glyphs are the
standard U+2654..U+265F block. Light/dark square backgrounds are near-
black with a faint tint so piece fg colors do the visual work.
"""

from __future__ import annotations

import chess
from rich.style import Style

# ------------------------- piece glyphs -------------------------------------
#
# python-chess stores pieces as (type, colour). We render using the
# SOLID (black-filled) glyphs for both sides and distinguish via fg
# colour — this reads MUCH more clearly in terminals than the outline
# white-piece glyphs, which vanish on dark backgrounds.

PIECE_GLYPH = {
    chess.PAWN:   "♟",
    chess.KNIGHT: "♞",
    chess.BISHOP: "♝",
    chess.ROOK:   "♜",
    chess.QUEEN:  "♛",
    chess.KING:   "♚",
}

# Alternative outline glyphs (white-king-style), used optionally.
PIECE_GLYPH_OUTLINE = {
    chess.PAWN:   "♙",
    chess.KNIGHT: "♘",
    chess.BISHOP: "♗",
    chess.ROOK:   "♖",
    chess.QUEEN:  "♕",
    chess.KING:   "♔",
}


def piece_glyph(piece: chess.Piece) -> str:
    return PIECE_GLYPH[piece.piece_type]


# ------------------------- colours ------------------------------------------

# Piece colours — high-contrast for dark terminals.
FG_WHITE_PIECE = "rgb(245,245,250)"
FG_BLACK_PIECE = "rgb(230,130,120)"  # warm coral — "opposing" hue
FG_EMPTY       = "rgb(60,70,90)"

# Square bgs (very subtle — tile fg does the work).
BG_LIGHT = "rgb(55,62,78)"
BG_DARK  = "rgb(32,38,52)"

# Highlight layers (composed atop bg).
BG_LASTMOVE  = "rgb(80,85,40)"    # olive
BG_CHECK     = "rgb(130,30,30)"   # red, bright
BG_CURSOR    = "rgb(60,110,160)"  # steel blue
BG_SELECTED  = "rgb(60,150,90)"   # green — selected source square
BG_LEGAL     = "rgb(90,100,40)"   # yellow-olive — square is a legal dest
BG_LEGAL_CAPTURE = "rgb(140,70,40)"  # darker red — legal capture dest
BG_HINT      = "rgb(120,90,170)"  # purple — engine's best move highlight

STYLE_COORD  = Style(color="rgb(110,120,140)", dim=True)


def square_bg(sq: int) -> str:
    """Return the base bg colour of a square."""
    return BG_LIGHT if (chess.square_file(sq) + chess.square_rank(sq)) & 1 else BG_DARK


def piece_style(piece: chess.Piece, bg: str) -> Style:
    fg = FG_WHITE_PIECE if piece.color == chess.WHITE else FG_BLACK_PIECE
    return Style(color=fg, bgcolor=bg, bold=(piece.piece_type in (chess.KING, chess.QUEEN)))


def empty_style(bg: str) -> Style:
    return Style(color=FG_EMPTY, bgcolor=bg)
