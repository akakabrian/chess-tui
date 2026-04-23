"""Optional REST agent API for chess-tui.

Mirrors the pattern from simcity-tui / openttd-tui: an aiohttp server
runs on the same asyncio loop as the Textual app, letting a remote
agent (LLM or script) drive the board.

Endpoints (JSON in/out, no auth — bind to 127.0.0.1 only):

    GET  /state                       snapshot (FEN, legal moves, lines)
    GET  /pgn                         PGN of the current game
    GET  /book?fen=...                polyglot continuations
    POST /move   {"uci": "e2e4"}      play move on the live board
    POST /reset  {"fen": "..."?}      reset position (optional FEN)
    POST /analyse {"depth": 15}       one-shot analysis (blocking)
    POST /bestmove {"time": 0.3}      one-shot bestmove
    GET  /health                      {"ok": true, "version": "..."}

Launched via ``play.py --agent`` or environment ``CHESS_TUI_AGENT=1``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import chess

try:
    from aiohttp import web
except ImportError:  # pragma: no cover - optional dep
    web = None  # type: ignore

log = logging.getLogger("chess_tui.agent_api")


def _state_dict(app) -> dict:
    board = app.game.live_board()
    snaps: dict[str, Any] = {}
    if app._analyzer is not None:
        for name, snap in app._analyzer.all_snapshots().items():
            best = snap.best
            snaps[name] = {
                "depth": best.depth if best else 0,
                "nodes": best.nodes if best else 0,
                "nps":   best.nps if best else 0,
                "lines": [
                    {
                        "rank": l.rank,
                        "score_cp": l.score_cp,
                        "mate_in": l.mate_in,
                        "pv_uci": [m.uci() for m in l.pv],
                        "pv_san": l.pv_san,
                    } for l in snap.lines
                ],
            }
    return {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "ply": app.game.ply_cursor,
        "total_plies": app.game.total_plies,
        "legal_uci": [m.uci() for m in board.legal_moves],
        "in_check": board.is_check(),
        "is_over": app.game.is_over(),
        "outcome": app.game.outcome_text(),
        "mode": app.game.mode.value,
        "history_san": app.game.san_history(),
        "engines": snaps,
        "book_available": app._book.available,
    }


def build_app(app) -> "web.Application":
    if web is None:
        raise RuntimeError("aiohttp not installed — pip install 'chess-tui[agent]'")

    routes = web.RouteTableDef()

    @routes.get("/health")
    async def _health(req):
        return web.json_response({"ok": True, "version": "0.1.0"})

    @routes.get("/state")
    async def _state(req):
        return web.json_response(_state_dict(app))

    @routes.get("/pgn")
    async def _pgn(req):
        return web.Response(text=app.game.to_pgn(),
                            content_type="application/x-chess-pgn")

    @routes.get("/book")
    async def _book(req):
        fen = req.query.get("fen")
        board = chess.Board(fen) if fen else app.game.live_board()
        entries = app._book.entries(board)
        return web.json_response({
            "fen": board.fen(),
            "entries": [
                {
                    "uci": e.move.uci(),
                    "san": e.san,
                    "weight": e.weight,
                    "share": e.share,
                } for e in entries
            ],
        })

    @routes.post("/move")
    async def _move(req):
        body = await req.json()
        uci = body.get("uci", "")
        try:
            move = chess.Move.from_uci(uci)
        except Exception as e:
            return web.json_response({"ok": False, "err": f"bad uci: {e}"}, status=400)
        ok = app.game.try_move(move)
        if ok:
            app._kick_analysis()
            app._refresh_all_panels()
        return web.json_response({"ok": ok, "state": _state_dict(app)})

    @routes.post("/reset")
    async def _reset(req):
        body = {}
        try:
            body = await req.json()
        except Exception:
            pass
        fen = body.get("fen")
        from .game import Game, Mode
        app.game = Game(board=chess.Board(fen) if fen else chess.Board(),
                        mode=Mode.ANALYSIS)
        app._kick_analysis()
        app._refresh_all_panels()
        return web.json_response({"ok": True, "state": _state_dict(app)})

    @routes.post("/analyse")
    async def _analyse(req):
        body = {}
        try:
            body = await req.json()
        except Exception:
            pass
        depth = int(body.get("depth", 12))
        from .engine import discover_engines
        specs = discover_engines()
        if not specs:
            return web.json_response({"ok": False, "err": "no engine"}, status=503)
        spec = specs[0]
        import chess.engine
        engine = chess.engine.SimpleEngine.popen_uci(spec.argv)
        try:
            info = engine.analyse(app.game.live_board(),
                                  chess.engine.Limit(depth=depth),
                                  multipv=app._multipv)
        finally:
            try:
                engine.quit()
            except Exception:
                pass
        multi = info if isinstance(info, list) else [info]
        out = []
        board = app.game.live_board()
        for rank, x in enumerate(multi, start=1):
            pv = x.get("pv") or []
            score = x.get("score")
            cp = mate = None
            if score is not None:
                pov = score.pov(board.turn)
                if pov.is_mate():
                    mate = pov.mate()
                else:
                    cp = pov.score(mate_score=100_000)
            out.append({
                "rank": rank,
                "depth": int(x.get("depth") or 0),
                "score_cp": cp,
                "mate_in": mate,
                "pv_uci": [m.uci() for m in pv],
            })
        return web.json_response({"ok": True, "engine": spec.name, "lines": out})

    @routes.post("/bestmove")
    async def _best(req):
        body = {}
        try:
            body = await req.json()
        except Exception:
            pass
        t = float(body.get("time", 0.3))
        from .engine import bestmove, discover_engines
        specs = discover_engines()
        if not specs:
            return web.json_response({"ok": False, "err": "no engine"}, status=503)
        mv = bestmove(specs[0], app.game.live_board(), time_limit=t)
        return web.json_response({
            "ok": True,
            "uci": mv.uci() if mv else None,
            "engine": specs[0].name,
        })

    waio = web.Application()
    waio.add_routes(routes)
    return waio


async def start_server(app, *, host: str = "127.0.0.1", port: int = 8765):
    """Start the aiohttp server on the running asyncio loop; return
    the runner so the caller can ``await runner.cleanup()`` on exit."""
    if web is None:
        log.warning("aiohttp not available — agent API disabled")
        return None
    waio = build_app(app)
    runner = web.AppRunner(waio)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("agent API listening on http://%s:%d", host, port)
    return runner
