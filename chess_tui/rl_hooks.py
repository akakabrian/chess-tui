"""RL exposure hooks for chess-tui.

Headless adapter — bypasses Textual entirely. An RL "step" is one
move; opponent move is played by random-legal or optional stockfish.

State vector layout (flat float32, STATE_DIM = 64*13 + 7 = 839):
  [0:832]   64 squares × 13 channels:
            [P,N,B,R,Q,K, p,n,b,r,q,k, empty] one-hot per square.
  [832]     side-to-move (1=white, 0=black)
  [833]     castling rights (packed /16):
            K+2Q+4k+8q bits
  [834]     en-passant file / 7 (-1 if none)
  [835]     halfmove clock / 100
  [836]     fullmove / 200
  [837]     material delta / 39 (agent POV, clipped)
  [838]     1.0 (bias)

NOTE spec says "<500 dims" for most games but allows chess 64*13 +
metadata. 839 is intentional per spec wording.

Actions: discrete index into a fixed 4672-size move table (same as
AlphaZero) is overkill for smoke — we use a simpler scheme:
  Agent surfaces `legal_moves()` and `step_move_uci(uci)` or
  `step_move_index(i)` where `i` indexes into the current legal-move
  list. Env uses max(len legal_moves) as action_space.n and masks
  illegal by mapping to a resign-ish no-op (terminates episode).

Reward shaping:
  Terminal +1 win / -1 loss / 0 draw.
  Per-move shaping: +/- material_delta * 0.01 (tiny).

Opponent: random legal by default (seedable); can be swapped later.
"""

from __future__ import annotations

import random
from typing import Optional

import chess
import numpy as np


STATE_DIM = 64 * 13 + 7  # 839
MAX_LEGAL_MOVES = 218     # chess max branching factor (tight upper bound)

# Piece values for material-delta shaping.
_PIECE_VALUE = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}

_PIECE_TO_CHANNEL = {
    (chess.PAWN,   True):  0,
    (chess.KNIGHT, True):  1,
    (chess.BISHOP, True):  2,
    (chess.ROOK,   True):  3,
    (chess.QUEEN,  True):  4,
    (chess.KING,   True):  5,
    (chess.PAWN,   False): 6,
    (chess.KNIGHT, False): 7,
    (chess.BISHOP, False): 8,
    (chess.ROOK,   False): 9,
    (chess.QUEEN,  False): 10,
    (chess.KING,   False): 11,
}


def _material(board: chess.Board, color: chess.Color) -> int:
    total = 0
    for piece_type, val in _PIECE_VALUE.items():
        total += val * len(board.pieces(piece_type, color))
    return total


class RLGame:
    """Headless chess driver with random-legal opponent."""

    def __init__(self, seed: int = 0, agent_white: bool = True):
        self.seed = seed
        self.rng = random.Random(seed)
        self.agent_color = chess.WHITE if agent_white else chess.BLACK
        self.board = chess.Board()
        self._prev_material_delta = 0
        self._terminal_bonus_delivered = False
        # If agent is black, opponent moves first.
        if self.board.turn != self.agent_color and not self.board.is_game_over():
            self._opponent_move()

    def reset(self) -> None:
        self.rng = random.Random(self.seed)
        self.board = chess.Board()
        self._prev_material_delta = 0
        self._terminal_bonus_delivered = False
        if self.board.turn != self.agent_color and not self.board.is_game_over():
            self._opponent_move()

    # Actions ----------------------------------------------------------

    def legal_moves(self) -> list[chess.Move]:
        return list(self.board.legal_moves)

    def step_move_index(self, i: int) -> bool:
        """Play the i-th legal move (wraps if out-of-range).
        Returns False if no legal moves / game over."""
        if self.board.is_game_over():
            return False
        legals = self.legal_moves()
        if not legals:
            return False
        move = legals[i % len(legals)]
        self.board.push(move)
        if not self.board.is_game_over():
            self._opponent_move()
        return True

    def _opponent_move(self) -> None:
        legals = list(self.board.legal_moves)
        if not legals:
            return
        self.board.push(self.rng.choice(legals))

    # RL surface -------------------------------------------------------

    def game_state_vector(self) -> np.ndarray:
        vec = np.zeros(STATE_DIM, dtype=np.float32)
        for sq in range(64):
            piece = self.board.piece_at(sq)
            if piece is None:
                vec[sq * 13 + 12] = 1.0
            else:
                ch = _PIECE_TO_CHANNEL[(piece.piece_type, piece.color)]
                vec[sq * 13 + ch] = 1.0
        vec[832] = 1.0 if self.board.turn == chess.WHITE else 0.0
        cr = 0
        if self.board.has_kingside_castling_rights(chess.WHITE):
            cr |= 1
        if self.board.has_queenside_castling_rights(chess.WHITE):
            cr |= 2
        if self.board.has_kingside_castling_rights(chess.BLACK):
            cr |= 4
        if self.board.has_queenside_castling_rights(chess.BLACK):
            cr |= 8
        vec[833] = cr / 16.0
        ep = self.board.ep_square
        vec[834] = (chess.square_file(ep) / 7.0) if ep is not None else -1.0
        vec[835] = min(100, self.board.halfmove_clock) / 100.0
        vec[836] = min(200, self.board.fullmove_number) / 200.0
        agent_mat = _material(self.board, self.agent_color)
        opp_mat = _material(self.board, not self.agent_color)
        delta = agent_mat - opp_mat
        vec[837] = max(-39, min(39, delta)) / 39.0
        vec[838] = 1.0
        self._prev_material_delta = delta
        return vec

    def game_reward(self) -> float:
        # Shaping: delta in material-delta since last call.
        agent_mat = _material(self.board, self.agent_color)
        opp_mat = _material(self.board, not self.agent_color)
        delta = agent_mat - opp_mat
        shape = 0.01 * (delta - self._prev_material_delta)
        self._prev_material_delta = delta

        reward = shape
        if self.board.is_game_over() and not self._terminal_bonus_delivered:
            outcome = self.board.outcome(claim_draw=True)
            if outcome is not None:
                if outcome.winner is None:
                    reward += 0.0  # draw
                elif outcome.winner == self.agent_color:
                    reward += 1.0
                else:
                    reward -= 1.0
            self._terminal_bonus_delivered = True
        return float(reward)

    def is_terminal(self) -> bool:
        return bool(self.board.is_game_over())


def state_vector_len() -> int:
    return STATE_DIM
