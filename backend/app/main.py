"""FastAPI 启动入口（成员B - 通用基础模块）

职责:
    - 路由注册、中间件、CORS、生命周期管理
    - /health 健康检查（暴露 Milvus/Redis 依赖状态）
    - /ws/tasks WebSocket 通道：向驾驶舱实时推送任务状态（DSH-002 / CEL-003）
      成员C 的 supervisor / Celery 任务可通过 broadcast_task_event() 推送事件

启动（在 backend/ 目录下）:
    uvicorn app.main:app --reload --port 8000
接口文档: http://localhost:8000/docs

环境变量:
    ALLOWED_ORIGINS  CORS 白名单，逗号分隔，默认 http://localhost:5173,http://localhost:8080
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("uvicorn.error")

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生命周期：启动时探测依赖服务（失败不阻塞启动，状态经 /health 暴露）。"""
    try:
        from app.common.milvus_cli import get_client
        get_client()
        logger.info("Milvus 连接正常")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Milvus 不可用（请确认 docker-compose 服务已启动）: %s", exc)
    yield
    from app.common.milvus_cli import close_client
    close_client()


app = FastAPI(
    title="电商多智能体运营系统",
    description="多 Agent 电商运营自动化系统：选品 / 文案 / 定价 / 审批",
    version=APP_VERSION,
    lifespan=lifespan,
)

_origins = [
    o.strip() for o in __import__("os").getenv(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8080"
    ).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# TODO(成员C): supervisor / 各 Agent 的 API 路由在此 include_router 注册
# from app.agent.supervisor import router as supervisor_router
# app.include_router(supervisor_router, prefix="/api")


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
def health() -> dict:
    """健康检查：返回各依赖服务的可用状态。"""
    milvus_ok = False
    redis_ok = False
    try:
        from app.common.milvus_cli import get_client
        get_client().list_collections()
        milvus_ok = True
    except Exception:  # noqa: BLE001
        pass
    try:
        import redis as redis_lib
        from app.common.celery_app import REDIS_URL
        redis_lib.from_url(REDIS_URL, socket_connect_timeout=2).ping()
        redis_ok = True
    except Exception:  # noqa: BLE001
        pass
    return {
        "status": "ok" if (milvus_ok and redis_ok) else "degraded",
        "version": APP_VERSION,
        "milvus": milvus_ok,
        "redis": redis_ok,
    }


# ---------------------------------------------------------------------------
# WebSocket 任务事件推送（DSH-002 实时状态刷新）
# ---------------------------------------------------------------------------

class TaskEventBroadcaster:
    """管理驾驶舱前端的长连接，并广播任务状态事件。"""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast(self, event: dict) -> int:
        """向所有在线客户端推送事件，返回推送成功的连接数。"""
        message = json.dumps(event, ensure_ascii=False)
        dead: list[WebSocket] = []
        sent = 0
        for ws in self._connections:
            try:
                await ws.send_text(message)
                sent += 1
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
        return sent


manager = TaskEventBroadcaster()


async def broadcast_task_event(event: dict) -> int:
    """供 supervisor / Celery 任务调用的推送入口。

    事件结构约定: {"task_id", "node", "status", "detail", "ts"}
    status ∈ running / success / failed / pending_approval
    """
    event.setdefault("ts", time.time())
    return await manager.broadcast(event)


@app.websocket("/ws/tasks")
async def ws_tasks(websocket: WebSocket):
    """驾驶舱 WebSocket 端点：前端连接后接收任务状态推送。"""
    await manager.connect(websocket)
    try:
        while True:
            # 仅保活；前端发来的心跳/指令文本暂不处理
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
