# chess-tui

A terminal-native **chess study workstation** — not just a chess
frontend.  Runs multiple local UCI engines side-by-side for analysis,
pulls opening continuations from a polyglot book, trains on tactics
puzzles, and exposes everything over a REST API so an LLM agent can
drive the same board the human sees.

Built with [Textual](https://textual.textualize.io/) +
[python-chess](https://github.com/niklasf/python-chess).  Engines are
external binaries (Stockfish / GNU Chess) communicated with via UCI
subprocess — no C-binding layer.

## Install

### Linux (Debian / Ubuntu / Mint)

```bash
sudo apt install stockfish gnuchess gnuchess-book
make all          # creates venv, installs deps
make run
```

### macOS

```bash
brew install stockfish gnu-chess
make all
make run
```

`gnu-chess` on brew does NOT ship a polyglot book; the opening
explorer will be empty unless you provide one via
``CHESS_TUI_BOOK=/path/to/book.bin`` or `--book`.

## Usage

```
python play.py                       # hotseat, with live analysis
python play.py --mode engine         # human (white) vs stockfish
python play.py --mode analysis       # free exploration
python play.py --mode puzzle         # tactics trainer
python play.py --pgn game.pgn        # load PGN
python play.py --agent-port 8765     # expose REST API alongside TUI
```

### Keybindings

| key                   | action                        |
|-----------------------|-------------------------------|
| arrows / h j k l      | move cursor                   |
| space / enter         | select / make move            |
| esc                   | cancel selection              |
| u                     | undo                          |
| f                     | flip board                    |
| `<` `>`               | jump to start / end of game   |
| `,` `.`               | step back / forward in history|
| a                     | toggle analysis engines       |
| `+` `-`               | increase / decrease MultiPV   |
| m                     | play engine's best move       |
| H                     | hint (highlight best move)    |
| N                     | new game dialog               |
| R                     | new puzzle                    |
| S                     | save PGN                      |
| ?                     | help                          |
| q                     | quit                          |

## Agent REST API

Enabled with `--agent-port 8765` (localhost only).

```
GET  /health         → {"ok": true}
GET  /state          → full snapshot (FEN, legal moves, engine PVs…)
GET  /pgn            → current game PGN
GET  /book?fen=...   → polyglot continuations
POST /move           body: {"uci": "e2e4"}
POST /reset          body: {"fen": "..."}
POST /analyse        body: {"depth": 15} → one-shot analysis
POST /bestmove       body: {"time": 0.3} → engine bestmove
```

Example:

```bash
# In one terminal:
python play.py --mode analysis --agent-port 8765 &

# In another:
curl http://127.0.0.1:8765/state
curl -X POST -d '{"uci":"e2e4"}' -H 'content-type: application/json' \
     http://127.0.0.1:8765/move
```

## Testing

```
make test              # TUI scenarios + API scenarios + perf baseline
make test-only PAT=explorer      # regex filter on scenario names
make perf                         # perf baseline only
CHESS_TUI_RUN_ENGINE=1 .venv/bin/python -m tests.qa engine
                                  # opt-in engine-subprocess scenario
```

## Layout

```
chess-tui/
├── play.py                entry — argparse → chess_tui.app.run(...)
├── chess_tui/
│   ├── app.py             Textual App + panel refreshers
│   ├── board_view.py      BoardView widget (8×8 render)
│   ├── engine.py          MultiEngineAnalyzer + UCI wrappers
│   ├── book.py            polyglot book reader
│   ├── puzzles.py         Lichess CSV loader + session runner
│   ├── game.py            chess.Board + history + PGN + modes
│   ├── pieces.py          Unicode glyphs + styles
│   ├── screens.py         modal dialogs (help, new game, …)
│   ├── agent_api.py       aiohttp REST endpoints
│   └── tui.tcss           stylesheet
├── tests/
│   ├── qa.py              19 Textual Pilot scenarios
│   ├── api_qa.py          8 aiohttp client scenarios
│   └── perf.py            6 micro-benchmarks
├── data/
│   └── puzzles_sample.csv bundled ~9 hand-validated puzzles
├── DECISIONS.md           design decisions + gotchas
├── Makefile               bootstrap / build / run / test
└── pyproject.toml
```

## License

Wrapper code MIT.  Stockfish and GNU Chess are GPLv3 (used via
subprocess — no linkage).  `gnuchess-book/book.bin` is GPL.
`python-chess` is GPLv3.
