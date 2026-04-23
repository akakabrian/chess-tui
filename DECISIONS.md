# chess-tui — design decisions

## 1. Angle — multi-engine analysis workstation, not online play

[trevorbayless/cli-chess](https://github.com/trevorbayless/cli-chess)
already owns the "Textual chess client for lichess.org" niche (online
play + Lichess API).  `chess-tui` differentiates on:

* **multiple local UCI engines** running simultaneously (Stockfish,
  GNU Chess, optional Leela) with live MultiPV side-by-side,
* **polyglot opening explorer** sourced from a redistributable book,
* **puzzle trainer** from Lichess CC0 puzzle DB (bundled sample),
* **REST agent API** (``aiohttp``) so an LLM or script can drive the
  board,
* **pure offline** — no network dependency, no account required.

The UI therefore privileges *analysis affordances* (eval bar, top-N
PVs, book weights, history navigation) over competitive-play
ergonomics.

## 2. Engine integration — python-chess subprocess UCI

No SWIG / C-binding layer. Stockfish and GNU Chess are standalone
binaries shipping via OS package managers; `python-chess` wraps the
UCI protocol (``chess.engine.SimpleEngine.popen_uci``) in a clean
sync/async API. We use the **sync** API from a worker *thread* per
engine because:

1. Textual's async loop is busy with UI rendering; keeping analysis
   off the main loop avoids "engine thinks → UI freezes".
2. The async engine API in python-chess is less ergonomic for
   long-running "analyse infinitely, restart on position change"
   patterns.
3. Worker-thread design also composes naturally with ``call_from_thread``
   if a future phase adds engine-driven overlays.

Per-engine ``_Worker`` thread loops on a `Condition`; `set_position`
publishes a new board and the worker calls `analysis.stop()` then
picks up the new target.

## 3. MultiPV is analysis-call, not an engine option

``python-chess`` reserves ``MultiPV`` for ``engine.analysis(..., multipv=N)``
rather than ``engine.configure({"MultiPV": N})``. Setting it via
`configure` raises ``EngineError: cannot set MultiPV which is
automatically managed``. Our ``EngineHandle.configure`` filters out
``MultiPV`` and ``Ponder`` from the options dict and passes ``multipv``
at analyse time.

## 4. Opening book — gnuchess-book (polyglot), not bundled

Ubuntu's ``gnuchess-book`` package ships
``/usr/share/games/gnuchess/book.bin`` — a ~300 KB polyglot book under
the same GPL license as gnuchess itself. We load it via
``chess.polyglot.MemoryMappedReader`` and gracefully no-op when absent.

Falls back to ``$CHESS_TUI_BOOK`` env var or ``/usr/share/gnuchess/book.bin``
(older packaging). The `smallbook.bin` shipped by gnuchess is NOT
polyglot format (it's gnuchess's own format) — don't try to load it.

## 5. Polyglot castling normalisation

Polyglot encodes castling as the king capturing its own rook
(`e1h1`, `e8a8`). `python-chess`'s legal_moves uses the standard
two-square encoding (`e1g1`, `e8c8`). `book.py:_normalize_castling`
translates the former to the latter when the moving piece is a king
and the rank aligns, otherwise drops the entry.

## 6. Puzzle format — Lichess CSV (CC0), tiny sample bundled

Full ``lichess_db_puzzle.csv`` is ~600 MB and we don't want it in git.
We ship ``data/puzzles_sample.csv`` with ~9 hand-validated entries
covering fool's mate, scholar's mate, back-rank mate, K+Q / K+R
endgames, and a tactical trade — plenty for the mode to be useful
standalone. Users can point ``--puzzles path.csv`` at the full DB.

Puzzle CSV column order matches Lichess exactly; we parse only
``PuzzleId, FEN, Moves, Rating, Themes``.  `Moves` is space-separated
UCI — the FIRST is the opponent's "setup" move that produces the
position the player sees; the REST is the forced solution sequence
alternating sides.

## 7. Board rendering — single rich.Text, not render_line()

At 8×8 = 64 squares × 3 glyphs each, a full rebuild is <1 ms
(measured: 0.46 ms median, 0.52 ms p95 on this box). No ScrollView /
render_line() viewport cropping is warranted — Textual's dirty-region
compositor already limits actual terminal output to changed cells.

## 8. Modal escape is NOT priority

App-level `escape` was originally `priority=True` so selection-cancel
would fire regardless of focus. That broke `HelpScreen`'s own
`escape → pop_screen`. The fix matches the skill gotcha exactly:
remove `priority=True` from `escape`. Arrow keys + space/enter stay
priority because they need to beat scrollable-sibling bindings.

## 9. Layout — 4-ish panes, board left

```
  [board + status + flash]  |  [analysis]
                            |  [opening explorer]
                            |  [move list (SAN)]
                            |  [log]
```

Board is fixed 32-char wide (fits 8 files × 3 chars + 2-char rank
labels + padding). Right column is 1fr and stacks four Static panels
plus a RichLog. This means the board doesn't get resized when the
side panels have lots of content — always legible.

## 10. Agent API — aiohttp background worker on same loop

Following simcity-tui's pattern: when ``--agent-port N`` is passed,
the app schedules an asyncio worker (`run_worker(..., group="agent")`)
that starts an aiohttp site bound to 127.0.0.1. Endpoints all
operate on the shared `self.game` / `self._analyzer`, so an agent and
the human UI see exactly the same state. No auth — localhost-only by
design. Tailnet exposure is opt-in via a separate reverse proxy.

## 11. QA / API / perf split into three runners

- ``tests/qa.py``        — 19 Pilot scenarios over the full app
- ``tests/api_qa.py``    —  8 aiohttp scenarios over the agent API
- ``tests/perf.py``      — 6 micro-benchmarks on hot paths

Full `make test` runs all three. During hot iteration:
`make test-only PAT=<scenario-regex>` is cheaper.

## 12. Publishing notes

No vendor source tree — engines are external binaries, book is OS
package, puzzle sample is small CSV. `.gitignore` excludes `.venv/`
and `data/puzzles_full.csv` (user-supplied full DB). License TBD —
wrapper code is MIT but interacts with GPL engines at the subprocess
level, no linkage, so wrapper can stay permissive.
