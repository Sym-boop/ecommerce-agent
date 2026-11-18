"""验证 WebSocket 代理是否转发正常（临时工具）。

期望：连上 ws://127.0.0.1:5173/ws/dashboard 后发 {"action":"ping"}，能收到 {"event":"pong"}。

用法：python demo_tools/probe_ws.py
"""

import asyncio
import json

import websockets


async def main() -> None:
    url = "ws://127.0.0.1:5173/ws/dashboard"
    print("连接", url)
    async with websockets.connect(url, open_timeout=15) as ws:
        print("握手成功")
        await ws.send(json.dumps({"action": "ping"}))
        reply = await asyncio.wait_for(ws.recv(), timeout=15)
        print("收到:", reply)
        frame = json.loads(reply)
        assert frame.get("event") == "pong", f"预期 pong，实际 {frame}"
        print("✅ WebSocket 代理正常（心跳往返成功）")


if __name__ == "__main__":
    asyncio.run(main())
