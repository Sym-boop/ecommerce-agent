"""异步任务基础设施：**按能力而非按框架**定义接口。

**为什么需要它**

文案生成要等大模型 5~15 秒，批量生成更久。同步调用会把 HTTP 请求挂住，
用户只能盯着转圈，也无法查询进度、无法取消、无法重试。

**为什么不是"直接上 Celery"**

Celery 需要可达的 Redis broker。在本项目的实际环境里 Redis 往往不可达
（`.env` 指向的地址是虚机地址；本地 Docker 也未必在跑）。
如果硬绑 Celery，结果是**功能在演示环境根本跑不起来**。

所以这里定义一层 `JobBackend` 抽象，提供两个实现：

    ├── ThreadPoolBackend  进程内线程池。零依赖，本地/演示环境默认使用。
    └── CeleryBackend       broker 可达时自动启用，支持多 worker 横向扩展。

后端**自动探测**：Redis 可达 → Celery；否则 → 线程池。启动时打印选择结果，
并可通过 `GET /api/v1/system/jobs/backend` 查询——避免"以为在用 Celery 其实没有"。
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Optional

logger = logging.getLogger("uvicorn.error")

# 任务状态
PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

_TERMINAL = {SUCCEEDED, FAILED, CANCELLED}

_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_MAX_JOBS = 500


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 后端抽象
# ---------------------------------------------------------------------------

class JobBackend:
    """任务后端接口。"""

    name = "base"
    distributed = False

    def submit(self, job_id: str, fn: Callable[..., Any], *args, **kwargs) -> None:
        raise NotImplementedError

    def cancel(self, job_id: str) -> bool:
        return False

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "distributed": self.distributed}


class ThreadPoolBackend(JobBackend):
    """进程内线程池：零依赖，适合本地与演示环境。"""

    name = "threadpool"
    distributed = False

    def __init__(self, workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._futures: dict[str, Any] = {}
        self._workers = workers

    def submit(self, job_id: str, fn: Callable[..., Any], *args, **kwargs) -> None:
        def _runner() -> Any:
            _mark_running(job_id)
            return fn(*args, **kwargs)

        self._futures[job_id] = self._pool.submit(_runner)

    def cancel(self, job_id: str) -> bool:
        fut = self._futures.get(job_id)
        if fut is None:
            return False
        # 已经在跑的任务无法强杀（Python 线程不可中断），只能取消未开始的
        cancelled = fut.cancel()
        if cancelled:
            _finish(job_id, CANCELLED, error="任务在开始执行前被取消")
        return cancelled

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "workers": self._workers,
                "note": "进程内线程池；单实例，重启后运行中的任务丢失"}


class CeleryBackend(JobBackend):
    """Celery：broker 可达时启用，支持多 worker 与结果持久化。"""

    name = "celery"
    distributed = True

    def __init__(self) -> None:
        from app.common.celery_app import celery_app

        self._app = celery_app

    def submit(self, job_id: str, fn: Callable[..., Any], *args, **kwargs) -> None:
        # 把可调用对象按名字送过去，worker 侧再导入；不可 pickle 的闭包无法跨进程
        target = getattr(fn, "__qualname__", "")
        module = getattr(fn, "__module__", "")
        if not target or "<locals>" in target:
            raise RuntimeError(
                "Celery 后端要求任务是模块级函数（可跨进程序列化），"
                "当前传入的是局部函数或闭包"
            )

        def _run() -> Any:
            _mark_running(job_id)
            return fn(*args, **kwargs)

        # 用通用任务包装，避免为每个业务函数都注册一个 celery task
        self._app.send_task(
            "app.common.jobs.run_job",
            args=[module, target, list(args), kwargs],
            task_id=job_id,
        )

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "broker": getattr(self._app.conf, "broker_url", "")}


# ---------------------------------------------------------------------------
# 后端选择
# ---------------------------------------------------------------------------

_backend: Optional[JobBackend] = None
_backend_reason = ""


def _redis_reachable(timeout: float = 1.5) -> tuple[bool, str]:
    """探测 Redis 是否可达（决定能否用 Celery）。"""
    try:
        from app.common.celery_app import REDIS_URL

        import redis as redis_lib

        client = redis_lib.from_url(REDIS_URL, socket_connect_timeout=timeout,
                                    socket_timeout=timeout)
        client.ping()
        return True, REDIS_URL
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def get_backend() -> JobBackend:
    """返回任务后端（自动选择，结果会被缓存）。"""
    global _backend, _backend_reason
    with _LOCK:
        if _backend is not None:
            return _backend

        forced = os.getenv("JOB_BACKEND", "").strip().lower()
        if forced == "threadpool":
            _backend = ThreadPoolBackend()
            _backend_reason = "JOB_BACKEND=threadpool（环境变量强制）"
        elif forced == "celery":
            try:
                _backend = CeleryBackend()
                _backend_reason = "JOB_BACKEND=celery（环境变量强制）"
            except Exception as exc:  # noqa: BLE001
                _backend = ThreadPoolBackend()
                _backend_reason = f"强制 Celery 但初始化失败（{exc}），回退线程池"
        else:
            ok, info = _redis_reachable()
            if ok:
                try:
                    _backend = CeleryBackend()
                    _backend_reason = f"Redis 可达（{info}），使用 Celery"
                except Exception as exc:  # noqa: BLE001
                    _backend = ThreadPoolBackend()
                    _backend_reason = f"Redis 可达但 Celery 初始化失败（{exc}），回退线程池"
            else:
                _backend = ThreadPoolBackend()
                _backend_reason = f"Redis 不可达（{info[:80]}），使用进程内线程池（功能可用，但不支持多 worker）"

        logger.info("异步任务后端: %s —— %s", _backend.name, _backend_reason)
        return _backend


def backend_info() -> dict[str, Any]:
    """后端信息（供 /api/v1/system/jobs/backend 展示）。"""
    backend = get_backend()
    return {**backend.describe(), "reason": _backend_reason}


# ---------------------------------------------------------------------------
# 任务登记与状态
# ---------------------------------------------------------------------------

def _trim() -> None:
    """只保留最近的任务，避免内存无限增长（终态优先淘汰）。"""
    if len(_JOBS) <= _MAX_JOBS:
        return
    finished = [jid for jid, j in _JOBS.items() if j["status"] in _TERMINAL]
    for jid in finished[: len(_JOBS) - _MAX_JOBS]:
        _JOBS.pop(jid, None)


def _mark_running(job_id: str) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None and job["status"] == PENDING:
            job["status"] = RUNNING
            job["started_at"] = _now()
            job["_t0"] = time.perf_counter()


def _finish(job_id: str, status: str, result: Any = None, error: str = "") -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job["status"] = status
        job["finished_at"] = _now()
        if result is not None:
            job["result"] = result
        if error:
            job["error"] = error
        t0 = job.pop("_t0", None)
        if t0 is not None:
            job["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)


def submit(kind: str, fn: Callable[..., Any], *args, actor: str = "", **kwargs) -> dict[str, Any]:
    """提交一个异步任务，立即返回任务记录（含 job_id）。"""
    job_id = f"JOB-{uuid.uuid4().hex[:10]}"
    record: dict[str, Any] = {
        "job_id": job_id,
        "kind": kind,
        "status": PENDING,
        "actor": actor,
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "elapsed_ms": None,
        "result": None,
        "error": "",
        "backend": get_backend().name,
    }
    with _LOCK:
        _JOBS[job_id] = record
        _trim()

    try:
        # 把 job_id 注入执行体：ThreadPool 是同进程，可直接传；
        # Celery 侧由 run_job 任务用 task_id 关联（见 CeleryBackend.submit）
        get_backend().submit(job_id, _run_job, fn, *args, __job_id__=job_id, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _finish(job_id, FAILED, error=f"任务提交失败: {exc}")
        logger.error("任务提交失败 kind=%s: %s", kind, exc)
    return get(job_id) or record


def _run_job(fn: Callable[..., Any], *args, **kwargs) -> None:
    """执行体：包装业务函数，把结果/异常写回任务记录。

    Celery 侧通过 `run_job` 任务反查 task_id 得到 job_id；
    线程池侧由 submit 直接注入 `__job_id__`。
    """
    job_id = kwargs.pop("__job_id__", "")
    if not job_id:
        # Celery worker 路径：当前任务 id 即 job_id
        try:
            from celery import current_task

            job_id = getattr(current_task.request, "id", "") or ""
        except Exception:  # noqa: BLE001
            job_id = ""
    if job_id:
        _mark_running(job_id)
    try:
        result = fn(*args, **kwargs)
        if job_id:
            _finish(job_id, SUCCEEDED, result=result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error("任务执行失败: %s", exc)
        if job_id:
            _finish(job_id, FAILED, error=str(exc))
        raise


def get(job_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs(limit: int = 50, kind: str = "", status: str = "") -> list[dict[str, Any]]:
    with _LOCK:
        rows = [dict(j) for j in _JOBS.values()]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows[:limit]


def cancel(job_id: str) -> bool:
    return get_backend().cancel(job_id)


def stats() -> dict[str, Any]:
    with _LOCK:
        rows = list(_JOBS.values())
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {
        "total": len(rows),
        "by_status": by_status,
        "backend": get_backend().name,
        "backend_reason": _backend_reason,
    }
