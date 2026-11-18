"""基于 MySQL 的分布式任务队列（多 worker 安全）。

**为什么要这个**

进程内线程池只能单实例：重启丢任务、无法横向扩容。
Celery 需要 Redis broker，但 Redis 未必可达。

MySQL 8 支持 `SELECT ... FOR UPDATE SKIP LOCKED`，
可以用一张表实现**多 worker 安全的任务领取**：
worker A 锁住的行，worker B 直接跳过，不会重复消费。

**核心机制**

```
提交：INSERT INTO jobs(status='pending')
领取：BEGIN;
        SELECT id FROM jobs WHERE status='pending' ORDER BY created_at
          LIMIT 1 FOR UPDATE SKIP LOCKED;
        UPDATE jobs SET status='running', worker_id=?, started_at=NOW();
      COMMIT;
完成：UPDATE jobs SET status='succeeded', result=?, finished_at=NOW()
失败：UPDATE jobs SET status='failed', error=?, attempts=attempts+1
```

**与进程内线程池的差别**

| | 线程池 | MySQL 队列 |
|---|---|---|
| 多实例 | ❌ | ✅ 靠 SKIP LOCKED 保证不重复 |
| 重启 | ❌ 任务丢失 | ✅ 状态在库里，可恢复 |
| 依赖 | 无 | 需要 MySQL 8 |

**注意**：任务必须有**可序列化的 kind + payload**，不能传闭包——
因为消费方可能是另一个进程。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

logger = logging.getLogger("uvicorn.error")

# 任务状态
PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

# kind -> 处理函数（模块级注册，供多进程 worker 按名字查找）
_HANDLERS: dict[str, Callable[[dict], Any]] = {}
_HANDLERS_LOCK = threading.Lock()


def register_handler(kind: str, fn: Callable[[dict], Any]) -> None:
    """注册某种任务的处理器。worker 按 kind 查找。"""
    with _HANDLERS_LOCK:
        _HANDLERS[kind] = fn


def get_handler(kind: str) -> Optional[Callable[[dict], Any]]:
    with _HANDLERS_LOCK:
        return _HANDLERS.get(kind)


def registered_kinds() -> list[str]:
    with _HANDLERS_LOCK:
        return sorted(_HANDLERS)


def _now() -> datetime:
    return datetime.now()


class MysqlJobQueue:
    """MySQL 任务队列（生产者 + 消费者共用一张表）。"""

    def __init__(self, config: Optional[dict] = None, table: str = "jobs") -> None:
        self._cfg = config or {
            "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DATABASE", "ecommerce_agent"),
        }
        self._table = table
        self._local = threading.local()
        self._conns: list = []
        self._lock = threading.Lock()
        self._closed = False
        self._ensure_database()
        self._init_schema()

    # -- 连接 --
    def _new_conn(self):
        import pymysql

        # autocommit=True：单条语句立即生效，且 SELECT 不会持有快照。
        # 如果用 autocommit=False，第一次 SELECT 就会开 REPEATABLE READ 事务，
        # 之后同一连接上的读取永远看到旧快照（实测踩过：worker 明明已提交，
        # 主线程 stats() 一直读到 pending=60）。
        # 需要事务的地方（claim 的 FOR UPDATE）用 conn.begin() 显式开启。
        return pymysql.connect(
            host=self._cfg["host"], port=self._cfg["port"],
            user=self._cfg["user"], password=self._cfg["password"],
            database=self._cfg["database"], charset="utf8mb4",
            autocommit=True, connect_timeout=5,
        )

    @property
    def _conn(self):
        if self._closed:
            raise RuntimeError("任务队列已关闭")
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.ping(reconnect=True)
                return conn
            except Exception:  # noqa: BLE001
                with self._lock:
                    if conn in self._conns:
                        self._conns.remove(conn)
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        with self._lock:
            conn = self._new_conn()
            self._local.conn = conn
            self._conns.append(conn)
        return conn

    def _ensure_database(self) -> None:
        import pymysql

        conn = pymysql.connect(
            host=self._cfg["host"], port=self._cfg["port"],
            user=self._cfg["user"], password=self._cfg["password"],
            charset="utf8mb4", connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self._cfg['database']}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        conn = self._conn
        with conn.cursor() as cur:
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS `{self._table}` (
                        id          VARCHAR(64) PRIMARY KEY,
                        kind        VARCHAR(64) NOT NULL,
                        status      VARCHAR(16) NOT NULL DEFAULT 'pending',
                        payload     LONGTEXT,
                        result      LONGTEXT,
                        error       TEXT,
                        attempts    INT NOT NULL DEFAULT 0,
                        actor       VARCHAR(64) DEFAULT '',
                        worker_id   VARCHAR(64) DEFAULT '',
                        created_at  DATETIME NOT NULL,
                        started_at  DATETIME NULL,
                        finished_at DATETIME NULL,
                        INDEX idx_status_created (status, created_at),
                        INDEX idx_kind (kind)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
            )
        conn.commit()

    # -- 生产者 --
    def submit(self, kind: str, payload: dict, *, actor: str = "",
               job_id: Optional[str] = None) -> dict[str, Any]:
        job_id = job_id or f"JOB-{uuid.uuid4().hex[:10]}"
        conn = self._conn
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO `{self._table}`(id, kind, status, payload, actor, created_at)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                    (job_id, kind, PENDING, json.dumps(payload, ensure_ascii=False),
                     actor, _now()),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return self.get(job_id) or {}

    # -- 消费者 --
    def claim(self, worker_id: str) -> Optional[dict[str, Any]]:
        """领取一个待处理任务（SKIP LOCKED：其他 worker 不会拿到同一行）。"""
        conn = self._conn
        try:
            conn.begin()   # 显式事务：SELECT ... FOR UPDATE 必须在一个事务里
            with conn.cursor() as cur:
                # 关键：FOR UPDATE SKIP LOCKED
                cur.execute(
                    f"""SELECT id FROM `{self._table}`
                        WHERE status=%s ORDER BY created_at ASC
                        LIMIT 1 FOR UPDATE SKIP LOCKED""",
                    (PENDING,),
                )
                row = cur.fetchone()
                if row is None:
                    conn.commit()      # 释放行锁
                    return None
                job_id = row[0]
                cur.execute(
                    f"""UPDATE `{self._table}`
                        SET status=%s, worker_id=%s, started_at=%s, attempts=attempts+1
                        WHERE id=%s""",
                    (RUNNING, worker_id, _now(), job_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return self.get(job_id)

    def complete(self, job_id: str, result: Any = None) -> None:
        conn = self._conn
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE `{self._table}`
                        SET status=%s, result=%s, finished_at=%s WHERE id=%s""",
                    (SUCCEEDED, json.dumps(result, ensure_ascii=False, default=str),
                     _now(), job_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def fail(self, job_id: str, error: str) -> None:
        conn = self._conn
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE `{self._table}` SET status=%s, error=%s, finished_at=%s
                        WHERE id=%s""",
                    (FAILED, str(error)[:2000], _now(), job_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def reclaim_stale(self, timeout_seconds: int = 600) -> int:
        """把"卡在 running 超过 timeout"的任务放回 pending（worker 崩溃恢复）。"""
        conn = self._conn
        cutoff = _now() - timedelta(seconds=timeout_seconds)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE `{self._table}` SET status=%s, worker_id=''
                        WHERE status=%s AND started_at < %s""",
                    (PENDING, RUNNING, cutoff),
                )
                n = cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return int(n or 0)

    # -- 查询 --
    def _row_to_dict(self, row, cols) -> dict[str, Any]:
        d = dict(zip(cols, row))
        for key in ("payload", "result"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (TypeError, json.JSONDecodeError):
                    pass
        for key in ("created_at", "started_at", "finished_at"):
            if d.get(key) is not None:
                d[key] = d[key].isoformat(timespec="seconds")
        return d

    _COLS = ("id", "kind", "status", "payload", "result", "error",
             "attempts", "actor", "worker_id", "created_at", "started_at", "finished_at")

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        conn = self._conn
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {','.join(self._COLS)} FROM `{self._table}` WHERE id=%s", (job_id,)
            )
            row = cur.fetchone()
        return self._row_to_dict(row, self._COLS) if row else None

    def list_jobs(self, limit: int = 50, kind: str = "", status: str = "") -> list[dict[str, Any]]:
        sql = f"SELECT {','.join(self._COLS)} FROM `{self._table}` WHERE 1=1"
        params: list[Any] = []
        if kind:
            sql += " AND kind=%s"
            params.append(kind)
        if status:
            sql += " AND status=%s"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        conn = self._conn
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [self._row_to_dict(r, self._COLS) for r in cur.fetchall()]

    def stats(self) -> dict[str, Any]:
        conn = self._conn
        with conn.cursor() as cur:
            cur.execute(f"SELECT status, COUNT(*) FROM `{self._table}` GROUP BY status")
            by_status = {k: int(v) for k, v in cur.fetchall()}
            cur.execute(f"SELECT COUNT(DISTINCT worker_id) FROM `{self._table}` WHERE worker_id<>''")
            workers = int(cur.fetchone()[0] or 0)
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "distinct_workers": workers,
            "table": self._table,
            "database": f"{self._cfg['host']}:{self._cfg['port']}/{self._cfg['database']}",
        }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            conns = list(self._conns)
            self._conns.clear()
            self._local = threading.local()
        for c in conns:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass


class QueueWorker:
    """消费者：循环领取任务并执行，直到 stop()。

    每个 worker 有自己的 worker_id，便于观察"任务被谁领走了"。
    """

    def __init__(self, queue: MysqlJobQueue, worker_id: Optional[str] = None,
                 poll_interval: float = 0.2) -> None:
        self._q = queue
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:6]}"
        self._poll = poll_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.processed = 0
        self.failed = 0

    def start(self) -> "QueueWorker":
        self._thread = threading.Thread(target=self._run, name=self.worker_id, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._q.claim(self.worker_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] 领取任务失败: %s", self.worker_id, exc)
                time.sleep(1.0)
                continue
            if job is None:
                time.sleep(self._poll)
                continue
            self._execute(job)

    def _execute(self, job: dict[str, Any]) -> None:
        kind = job.get("kind", "")
        handler = get_handler(kind)
        if handler is None:
            self._q.fail(job["id"], f"没有注册 kind={kind} 的处理器")
            self.failed += 1
            return
        try:
            result = handler(job.get("payload") or {})
            self._q.complete(job["id"], result)
            self.processed += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] 任务 %s 执行失败: %s", self.worker_id, job["id"], exc)
            self._q.fail(job["id"], str(exc))
            self.failed += 1

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "QueueWorker":
        return self.start()

    def __exit__(self, *exc: Any) -> bool:
        self.stop()
        return False
