"""Modal screens — help, new game, PGN load/save, confirm."""

from __future__ import annotations

from typing import Callable, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


HELP_TEXT = """
[bold]chess-tui[/] — keybindings

[bold]board & moves[/]
  arrows / hjkl      move cursor
  space / enter      select square / make move
  esc                cancel selection
  u                  undo last move
  f                  flip board
  < / >              step through history (home/end jump)
  m                  make engine hint move

[bold]panes[/]
  a                  toggle analysis engines
  + / -              increase / decrease MultiPV
  [ / ]              decrease / increase engine time slice
  e                  show engine info
  o                  toggle opening explorer

[bold]game modes[/]
  N                  new game dialog
  P                  load PGN
  S                  save PGN
  R                  play a puzzle
  H                  hint (engine best move)
  ?                  this help
  q                  quit
"""


class HelpScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "close"),
        Binding("q", "app.pop_screen", "close"),
        Binding("question_mark", "app.pop_screen", "close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(HELP_TEXT.strip(), id="help-body")


class NewGameScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "close"),
    ]

    def __init__(self, on_accept: Callable[[str], None]) -> None:
        super().__init__()
        self._cb = on_accept

    def compose(self) -> ComposeResult:
        with Vertical(id="newgame-dialog"):
            yield Static(
                "[bold]New game[/]\n\n"
                "Pick a mode:\n\n"
                "  [cyan]h[/] — hotseat (two humans)\n"
                "  [cyan]e[/] — vs engine (human plays white)\n"
                "  [cyan]b[/] — vs engine (human plays black)\n"
                "  [cyan]a[/] — analysis board\n"
                "  [cyan]p[/] — puzzle trainer\n\n"
                "[dim]esc to cancel[/]",
                id="ng-body",
            )

    def on_key(self, event) -> None:
        k = event.key
        if k in ("h", "e", "b", "a", "p"):
            self._cb(k)
            self.app.pop_screen()
        elif k == "escape":
            self.app.pop_screen()


class PgnLoadScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "close"),
    ]

    def __init__(self, on_load: Callable[[str], None]) -> None:
        super().__init__()
        self._cb = on_load

    def compose(self) -> ComposeResult:
        with Vertical(id="pgn-dialog"):
            yield Static("[bold]Load PGN[/] — paste text then press Ctrl+S, esc to cancel\n",
                         id="pgn-head")
            yield Input(placeholder="path to .pgn file", id="pgn-path")
            yield Static("", id="pgn-status")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._cb(event.value)
        self.app.pop_screen()


class PromotionScreen(ModalScreen):
    """Ask which piece to promote to."""
    BINDINGS = [
        Binding("escape", "app.pop_screen", "close"),
    ]

    def __init__(self, on_choice: Callable[[str], None]) -> None:
        super().__init__()
        self._cb = on_choice

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(
                "[bold]Promote to[/]\n\n"
                "  [cyan]q[/] queen   [cyan]r[/] rook   [cyan]b[/] bishop   [cyan]n[/] knight",
                id="promo-body",
            )

    def on_key(self, event) -> None:
        if event.key in ("q", "r", "b", "n"):
            self._cb(event.key)
            self.app.pop_screen()
        elif event.key == "escape":
            self._cb("q")
            self.app.pop_screen()
