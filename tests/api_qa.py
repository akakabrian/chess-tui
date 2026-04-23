"""REST agent-API scenarios.

Starts the aiohttp server on a free port, hits every endpoint, asserts
response shape. Run via ``python -m tests.api_qa``.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from typing import Optional

import aiohttp

from chess_tui.app import ChessApp
from chess_tui.game import Mode


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _run(pattern: Optional[str] = None) -> int:
    port = _free_port()
    app = ChessApp(engines=[], analysis_enabled=False,
                   mode=Mode.ANALYSIS, agent_port=port)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        # Give the agent worker a beat to spin up.
        for _ in range(20):
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(f"http://127.0.0.1:{port}/health",
                                        timeout=aiohttp.ClientTimeout(total=0.5)) as r:
                        if r.status == 200:
                            break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            print("agent API never came up", file=sys.stderr)
            return 1

        base = f"http://127.0.0.1:{port}"
        failures = 0
        total = 0

        async def check(name, coro):
            nonlocal failures, total
            if pattern and pattern not in name:
                return
            total += 1
            try:
                await coro
                print(f"\x1b[32mPASS\x1b[0m {name}")
            except AssertionError as e:
                failures += 1
                print(f"\x1b[31mFAIL\x1b[0m {name}: {e}")
            except Exception as e:
                failures += 1
                print(f"\x1b[31mERR \x1b[0m {name}: {type(e).__name__}: {e}")

        async with aiohttp.ClientSession() as sess:

            async def _health():
                async with sess.get(f"{base}/health") as r:
                    data = await r.json()
                    assert data.get("ok") is True

            async def _state_initial():
                async with sess.get(f"{base}/state") as r:
                    data = await r.json()
                    assert data["turn"] == "white"
                    assert data["ply"] == 0
                    assert "e2e4" in data["legal_uci"]
                    assert data["mode"] == "analysis"

            async def _move_e4():
                async with sess.post(f"{base}/move",
                                     json={"uci": "e2e4"}) as r:
                    data = await r.json()
                    assert data["ok"] is True
                    assert data["state"]["ply"] == 1
                    assert data["state"]["turn"] == "black"

            async def _illegal_move():
                async with sess.post(f"{base}/move",
                                     json={"uci": "a1a8"}) as r:
                    data = await r.json()
                    assert data["ok"] is False

            async def _pgn():
                async with sess.get(f"{base}/pgn") as r:
                    text = await r.text()
                    assert "1. e4" in text, text

            async def _book():
                async with sess.get(f"{base}/book") as r:
                    data = await r.json()
                    assert "entries" in data

            async def _reset():
                async with sess.post(f"{base}/reset", json={}) as r:
                    data = await r.json()
                    assert data["ok"] is True
                    assert data["state"]["ply"] == 0

            async def _bestmove():
                async with sess.post(f"{base}/bestmove",
                                     json={"time": 0.1}) as r:
                    data = await r.json()
                    if r.status == 503:
                        return  # no engine — acceptable
                    assert data["ok"] is True
                    assert data["uci"] is not None

            await check("health",        _health())
            await check("state_initial", _state_initial())
            await check("move_e4",       _move_e4())
            await check("illegal_move",  _illegal_move())
            await check("pgn",           _pgn())
            await check("book",          _book())
            await check("reset",         _reset())
            await check("bestmove",      _bestmove())

        print(f"\n{total - failures}/{total} API scenarios passed")
        return 0 if failures == 0 else 1


def main() -> int:
    pat = sys.argv[1] if len(sys.argv) > 1 else None
    return asyncio.run(_run(pat))


if __name__ == "__main__":
    raise SystemExit(main())
