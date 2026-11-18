"""投屏演示用的临时静态服务器（不修改项目源码）。

作用：把 frontend/dist 这个已构建好的前端产物托管在 5173 端口，
并把 /api、/media 反向代理到后端，同时代理 /ws WebSocket，
等价于 `npm run dev` 的 vite proxy（含 ws: true）。

用法（在项目根目录）：python demo_server.py
"""

from __future__ import annotations

import asyncio
import http.client
import os
from contextlib import suppress

import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "frontend", "dist")
BACKEND_HOST, BACKEND_PORT = "127.0.0.1", 8001
PORT = 5173

app = FastAPI(title="演示静态托管 + API 代理")

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "content-encoding",
}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_api(path: str, request: Request):
    return await _proxy(request, f"/api/{path}")


@app.api_route("/media/{path:path}", methods=["GET"])
async def proxy_media(path: str, request: Request):
    return await _proxy(request, f"/media/{path}")


async def _proxy(request: Request, target_path: str):
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    headers["host"] = f"{BACKEND_HOST}:{BACKEND_PORT}"
    conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=180)
    try:
        conn.request(request.method, target_path, body=body, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        out_headers = {k: v for k, v in resp.getheaders() if k.lower() not in _HOP_BY_HOP}
        return Response(
            content=payload,
            status_code=resp.status,
            headers=out_headers,
            media_type=resp.getheader("content-type"),
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=502, content={"code": 502, "msg": f"后端不可达: {exc}"})
    finally:
        conn.close()


# SPA 回退：静态资源命中则直接返回，否则交给 index.html 让 vue-router 处理
app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")


# ---------------------------------------------------------------------------
# WebSocket 代理：/ws/dashboard 双向转发到后端（等价 vite proxy 的 ws: true）
# 前端从页面 origin 推导 ws 地址，所以必须由本代理转发，否则顶部显示「通道断开」
# ---------------------------------------------------------------------------

@app.websocket("/ws/{path:path}")
async def proxy_ws(client_ws: WebSocket, path: str):
    await client_ws.accept()
    target = f"ws://{BACKEND_HOST}:{BACKEND_PORT}/ws/{path}"

    try:
        async with websockets.connect(target, open_timeout=10, ping_interval=None) as server_ws:
            async def client_to_server() -> None:
                try:
                    while True:
                        msg = await client_ws.receive_text()
                        await server_ws.send(msg)
                except WebSocketDisconnect:
                    pass

            async def server_to_client() -> None:
                async for msg in server_ws:
                    await client_ws.send_text(msg if isinstance(msg, str) else msg.decode("utf-8", "replace"))

            tasks = [asyncio.create_task(client_to_server()), asyncio.create_task(server_to_client())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t
    except Exception as exc:  # noqa: BLE001
        print(f"[ws] 后端连接失败 {target}: {exc}")
    finally:
        with suppress(Exception):
            await client_ws.close()


@app.get("/{full_path:path}")
async def spa(full_path: str):
    if full_path:
        candidate = os.path.join(DIST, full_path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
    return FileResponse(os.path.join(DIST, "index.html"))


if __name__ == "__main__":
    import uvicorn

    print(f"演示前端: http://127.0.0.1:{PORT}   (后端代理 -> http://{BACKEND_HOST}:{BACKEND_PORT})")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
