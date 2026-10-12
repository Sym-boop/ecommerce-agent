"""FastAPI 启动入口（成员B - 通用基础模块）

按前端契约（frontend/src/api/mock.js 为基线，2026-09-23 前端联调会议确认）实现:
    - REST 前缀 /api/v1，统一响应体 { code: 0, msg: 'ok', data: ... }
      POST /goals                  提交运营目标，触发任务编排
      GET  /tasks                  任务列表（拓扑摘要）
      GET  /tasks/{id}             任务详情（节点/连线/日志/输入输出）
      POST /tasks/{id}/retry       重试被驳回的审批项
      POST /tasks/{id}/cancel      手动终止任务
      GET  /approvals              审批单列表（status/type/start_time/end_time 过滤）
      GET  /approvals/{id}         审批单详情
      POST /approvals/{id}/approve 审批通过（文案可携带微调内容）
      POST /approvals/{id}/reject  审批驳回（必填原因）
      PUT  /copies/{id}            文案微调保存新版本
    - WebSocket /ws/dashboard，下行帧 {event, data}，上行 {"action":"ping"} → pong
      事件: node:update / task:update / approval:created / approval:updated

启动（在 backend/ 目录下）:
    uvicorn app.main:app --reload --port 8000
接口文档: http://localhost:8000/docs

注意: 任务/审批当前为进程内内存存储（task_engine），需单 worker 运行；
      多 worker + Redis 持久化由后续迭代完成。

环境变量:
    ALLOWED_ORIGINS  CORS 白名单，逗号分隔，默认 http://localhost:5173,http://localhost:8080
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.common import task_engine
from app.api.video_router import router as video_router
from app.api.copy_router import router as copy_router
from app.api.image_router import router as image_router
from app.api.ads_router import router as ads_router
from app.api.delivery_router import router as delivery_router
from app.api.review_router import router as review_router
from app.api.auth_router import router as auth_router
from fastapi.staticfiles import StaticFiles


logger = logging.getLogger("uvicorn.error")

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生命周期：启动时探测依赖服务（失败不阻塞启动，状态经 /health 暴露）。

    Milvus 探测放入后台线程并限时等待：VM / Milvus 未启动时不会阻塞服务启动。
    """
    import threading

    def _probe_milvus() -> None:
        try:
            from app.common.milvus_cli import get_client
            get_client()
            logger.info("Milvus 连接正常")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Milvus 不可用（请确认 docker-compose 服务已启动）: %s", exc)

    probe = threading.Thread(target=_probe_milvus, daemon=True)
    probe.start()
    probe.join(timeout=3)
    if probe.is_alive():
        logger.warning("Milvus 探测超时（>3s），服务以内存模式继续启动")

    yield
    from app.common.milvus_cli import close_client
    close_client()


app = FastAPI(
    title="电商多智能体运营系统",
    description="多 Agent 电商运营自动化系统：文案 / 定价 / 广告风控 / 审批",
    version=APP_VERSION,
    lifespan=lifespan,
)

_origins = [
    o.strip() for o in os.getenv(
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

Path("data/media").mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory="data/media"), name="media")

# 独立生成模块 API（文案 / 图文 / 视频）+ 广告物料历史
app.include_router(copy_router)
app.include_router(image_router)
app.include_router(video_router)
app.include_router(ads_router)
app.include_router(delivery_router)
app.include_router(review_router)
app.include_router(auth_router)
# ---------------------------------------------------------------------------
# 统一响应体
# ---------------------------------------------------------------------------

def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
def health() -> dict:
    """健康检查：返回各依赖服务的可用状态。"""
    milvus_ok = redis_ok = False
    knowledge_base_rows = 0
    try:
        from app.common.milvus_cli import get_wrapper
        wrapper = get_wrapper()
        wrapper.client.list_collections()  # 真正探测连通性（不可达会抛错）
        milvus_ok = True
        knowledge_base_rows = wrapper.count() if wrapper.has_collection() else 0
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
        "knowledge_base_rows": knowledge_base_rows,
        "redis": redis_ok,
    }


# ---------------------------------------------------------------------------
# 任务与运营目标（REST 契约见 frontend/src/api/index.js）
# ---------------------------------------------------------------------------

@app.post("/api/v1/goals", tags=["tasks"])
async def create_goal(payload: dict):
    try:
        task = task_engine.create_task(payload or {})
    except task_engine.EngineError as exc:
        return fail(exc.status, exc.msg)
    return ok({"task_id": task["id"]})


@app.get("/api/v1/tasks", tags=["tasks"])
def list_tasks():
    return ok(task_engine.list_tasks())


@app.get("/api/v1/tasks/{task_id}", tags=["tasks"])
def get_task(task_id: str):
    try:
        return ok(task_engine.get_task(task_id))
    except task_engine.EngineError as exc:
        return fail(exc.status, exc.msg)


@app.post("/api/v1/tasks/{task_id}/retry", tags=["tasks"])
async def retry_task(task_id: str):
    try:
        return ok(await task_engine.retry_task(task_id))
    except task_engine.EngineError as exc:
        return fail(exc.status, exc.msg)


@app.post("/api/v1/tasks/{task_id}/cancel", tags=["tasks"])
async def cancel_task(task_id: str):
    try:
        return ok(await task_engine.cancel_task(task_id))
    except task_engine.EngineError as exc:
        return fail(exc.status, exc.msg)


# ---------------------------------------------------------------------------
# 人机协同审批
# ---------------------------------------------------------------------------

@app.get("/api/v1/approvals", tags=["approvals"])
def list_approvals(status: str | None = None, type: str | None = None,
                   start_time: str | None = None, end_time: str | None = None):
    return ok(task_engine.list_approvals(status, type, start_time, end_time))


@app.get("/api/v1/approvals/{approval_id}", tags=["approvals"])
def get_approval(approval_id: str):
    try:
        return ok(task_engine.get_approval(approval_id))
    except task_engine.EngineError as exc:
        return fail(exc.status, exc.msg)


@app.post("/api/v1/approvals/{approval_id}/approve", tags=["approvals"])
async def approve_approval(approval_id: str, payload: dict | None = None):
    body = payload or {}
    try:
        approval = await task_engine.resolve_approval(
            approval_id, "approve",
            comment=str(body.get("comment") or ""),
            edited_content=body.get("edited_content"),
        )
    except task_engine.EngineError as exc:
        return fail(exc.status, exc.msg)
    return ok(approval)


@app.post("/api/v1/approvals/{approval_id}/reject", tags=["approvals"])
async def reject_approval(approval_id: str, payload: dict | None = None):
    body = payload or {}
    try:
        approval = await task_engine.resolve_approval(
            approval_id, "reject", comment=str(body.get("comment") or ""),
        )
    except task_engine.EngineError as exc:
        return fail(exc.status, exc.msg)
    return ok(approval)


@app.put("/api/v1/copies/{approval_id}", tags=["approvals"])
async def save_copy_draft(approval_id: str, payload: dict | None = None):
    body = payload or {}
    try:
        return ok(await task_engine.save_copy_draft(approval_id, str(body.get("content") or "")))
    except task_engine.EngineError as exc:
        return fail(exc.status, exc.msg)


# ---------------------------------------------------------------------------
# WebSocket 任务事件推送（/ws/dashboard，帧协议对齐 frontend/src/utils/websocket.js）
# ---------------------------------------------------------------------------

class DashboardBroadcaster:
    """管理驾驶舱前端长连接，下发 {event, data} 帧。"""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast_frame(self, event: str, data) -> int:
        """向所有在线客户端推送事件帧，返回推送成功的连接数。"""
        message = json.dumps({"event": event, "data": data}, ensure_ascii=False, default=str)
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


manager = DashboardBroadcaster()


async def _broadcast(event: str, data) -> None:
    """task_engine 的事件广播出口。"""
    await manager.broadcast_frame(event, data)


task_engine.set_broadcaster(_broadcast)


@app.websocket("/ws/dashboard")
async def ws_dashboard(websocket: WebSocket):
    """驾驶舱 WebSocket 端点：心跳保活 + 任务/审批事件实时推送。"""
    await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if frame.get("action") == "ping":
                await websocket.send_text(json.dumps({"event": "pong", "data": {"ts": time.time()}}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
