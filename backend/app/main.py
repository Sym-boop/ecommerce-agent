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
import threading
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
from app.api.product_router import router as product_router
from app.api.history_router import router as history_router
from app.api.adgen_router import router as adgen_router
from app.api.batch_router import router as batch_router
from app.api.billing_router import router as billing_router
from app.api.qianchuan_router import router as qianchuan_router
from app.api.store_router import router as store_router
from app.api.system_router import router as system_router
from app.api.metrics_router import router as metrics_router
from app.api.diagnose_router import router as diagnose_router
from fastapi.staticfiles import StaticFiles


logger = logging.getLogger("uvicorn.error")

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生命周期：启动时探测依赖服务 + 自检配置（失败不阻塞启动，状态经 /health 暴露）。

    Milvus 探测放入后台线程并限时等待：VM / Milvus 未启动时不会阻塞服务启动。
    """
    import threading

    # 启动自检：配置项 fail-fast 提示（不阻断启动，但把问题显式打出来）
    try:
        from app.common.config_check import log_startup_report
        log_startup_report()
    except Exception as exc:  # noqa: BLE001
        logger.warning("启动自检失败: %s", exc)

    # 清理历史遗留的重复物料（同类型+同标题+同正文）
    try:
        from app.common import ad_store
        removed = ad_store.dedupe_existing()
        if removed:
            logger.info("启动去重：清理重复广告物料 %d 条", removed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("启动去重失败: %s", exc)

    # 用当前违禁词规则重算存量物料的审查结论：
    # 检测规则修好后（如去掉单字「最」的误报），历史数据里的旧误报会"自愈"
    try:
        from app.common import ad_store
        result = ad_store.revalidate_violations()
        if result.get("fixed"):
            logger.info("审查结论自愈：重算 %d 条，修正 %d 条",
                        result.get("checked"), result.get("fixed"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("审查结论重算失败: %s", exc)

    def _probe_milvus() -> None:
        try:
            from app.common.milvus_cli import get_client
            get_client()
            logger.info("Milvus 连接正常")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Milvus 不可用（请确认 docker-compose 服务已启动）: %s", exc)
            # 探测失败就把熔断器打开，避免首批 /health 请求各等约 30 秒
            try:
                from app.common.milvus_cli import get_wrapper
                get_wrapper()._trip_breaker(exc)
            except Exception:  # noqa: BLE001
                pass

    probe = threading.Thread(target=_probe_milvus, daemon=True)
    probe.start()
    probe.join(timeout=3)
    if probe.is_alive():
        logger.warning("Milvus 探测超时（>3s），服务以内存模式继续启动")
        # 超时同样视为不可达：打开熔断，让后续请求立即走降级通道
        try:
            from app.common.milvus_cli import get_wrapper
            get_wrapper()._trip_breaker(RuntimeError("启动探测超时（>3s）"))
        except Exception:  # noqa: BLE001
            pass

    # 启动依赖探活后台线程：/health 只读快照，不在请求里做网络探活。
    # （启动时阻塞探活 3 秒是为了让首批请求的 /health 别显示"未知"）
    _start_health_refresher()

    yield
    from app.common.milvus_cli import close_client
    close_client()


app = FastAPI(
    title="电商多智能体运营系统",
    description="多 Agent 电商运营自动化系统：广告生成（文案/图文/视频）/ 审查审批 / 多平台投放",
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


@app.middleware("http")
async def actor_context_middleware(request, call_next):
    """每个请求开始时解析操作人并写入 ContextVar。

    **必须在中间件里做**：FastAPI 会把同步端点丢到 anyio 工作线程池执行，
    依赖函数与端点函数可能落在**不同线程**上；用 threading.local 会串号
    （实测：运营发起的生成被记成上一个请求的审查员）。
    中间件运行在请求任务的上下文中，配合 ContextVar 可保证按请求隔离。
    """
    try:
        from app.api.deps import resolve_actor

        resolve_actor(request.headers.get("authorization"))
    except Exception:  # noqa: BLE001
        pass
    return await call_next(request)

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
app.include_router(product_router)
app.include_router(history_router)
app.include_router(adgen_router)
app.include_router(batch_router)
app.include_router(billing_router)
app.include_router(qianchuan_router)
app.include_router(store_router)
# 系统管理：配置自检 / 指标 / 审计 / 质量 / 版本 / 数据飞轮
app.include_router(system_router)
# 投放效果回流：平台回传 → 时序落库 → CTR/CVR/CPA 汇总
app.include_router(metrics_router)
app.include_router(diagnose_router)
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

# 依赖探活快照：由**后台线程**定期刷新，/health 只读内存。
#
# 为什么不在请求里探活：
#   /health 是同步端点，会被丢进 anyio 线程池（默认 40 线程）。50 并发压测时，
#   熔断窗口刚过的一次真实 Milvus 连接要 **5 秒**，一批请求同时卡在探活上，
#   后续请求排队 —— 实测 P99 达到 **20000ms**（5s × 4 轮排队）。
#   把探活移出请求路径后，/health 变成纯内存读。
_HEALTH_REFRESH_SECONDS = float(os.getenv("HEALTH_REFRESH_SECONDS", "10"))
_HEALTH_SNAPSHOT: dict[str, Any] = {
    "milvus": False,
    "redis": False,
    "knowledge_base_rows": 0,
    "at": 0.0,
}
_health_thread: Optional[threading.Thread] = None


def _probe_dependencies_once() -> None:
    """探一次依赖，写入快照。只在后台线程里跑。"""
    milvus_ok = False
    rows = 0
    try:
        from app.common.milvus_cli import get_wrapper

        wrapper = get_wrapper()
        milvus_ok = wrapper.ping()
        rows = wrapper.count()
    except Exception:  # noqa: BLE001
        pass

    redis_ok = False
    try:
        import redis as redis_lib

        from app.common.celery_app import REDIS_URL

        redis_lib.from_url(
            REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
        ).ping()
        redis_ok = True
    except Exception:  # noqa: BLE001
        pass

    _HEALTH_SNAPSHOT.update(
        milvus=milvus_ok, redis=redis_ok,
        knowledge_base_rows=rows, at=time.time(),
    )


def _health_refresher() -> None:
    """后台刷新依赖状态。慢探活只影响这里，不影响请求延迟。"""
    while True:
        try:
            _probe_dependencies_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("依赖探活失败: %s", exc)
        time.sleep(_HEALTH_REFRESH_SECONDS)


def _start_health_refresher() -> None:
    global _health_thread
    if _health_thread is not None and _health_thread.is_alive():
        return
    _health_thread = threading.Thread(
        target=_health_refresher, name="health-refresher", daemon=True
    )
    _health_thread.start()
    logger.info("依赖探活后台线程已启动（间隔 %ss）", _HEALTH_REFRESH_SECONDS)


@app.get("/health", tags=["system"])
def health() -> dict:
    """健康检查：依赖状态来自**后台刷新快照**，本请求不发起任何网络探活。

    `banned_words.available=False` 是**严重**状态：风控已 fail-closed，
    新物料无法登记通过，必须尽快修复词表。
    """
    snap = dict(_HEALTH_SNAPSHOT)
    milvus_ok = bool(snap["milvus"])
    redis_ok = bool(snap["redis"])
    knowledge_base_rows = int(snap["knowledge_base_rows"] or 0)

    try:
        from app.common import rerank as rerank_mod
        rerank_status = rerank_mod.status()
    except Exception as exc:  # noqa: BLE001
        rerank_status = {"available": False, "error": str(exc)}

    try:
        from app.common.banned_words import status as bw_status
        banned = bw_status()
    except Exception as exc:  # noqa: BLE001
        banned = {"available": False, "error": str(exc), "word_count": 0}

    # 风控不可用时整体状态不应报 ok（避免"看起来健康"）
    healthy = milvus_ok and redis_ok and banned.get("available", False)
    return {
        "status": "ok" if healthy else "degraded",
        "version": APP_VERSION,
        "milvus": milvus_ok,
        # 首次刷新尚未完成时 at=0，不要算出一个天文数字
        "probe_age_seconds": (
            round(time.time() - float(snap["at"]), 1) if snap.get("at") else None
        ),
        "knowledge_base_rows": knowledge_base_rows,
        "redis": redis_ok,
        "rerank": rerank_status,
        "banned_words": banned,
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
