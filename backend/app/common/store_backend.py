"""存储后端：JSON 文件（默认）或 SQLite，通过配置切换。

**为什么需要这一层**

原实现把所有结构化数据写成 JSON 文件，全量重写。这在小数据量下没问题，
但有两个明确的扩展性瓶颈：

1. **写放大**：新增一条物料要把整个 `ads.json` 序列化重写一遍；
   1000 条物料时加一条要写 1000 条的量。
2. **无并发控制**：`threading.Lock` 只保护单进程内多线程，
   多 worker / Celery worker 之间会互相覆盖写。

**设计取舍**

不重写 9 个 store 的内存缓存逻辑（那是更大的重构风险），
而是在**唯一的数据出入口** `json_store` 下面插一层后端：

    store (ad_store / versions / quality_store / ...)
        └── json_store.read_json / write_json      ← 接口不变
              └── StoreBackend                     ← 本模块
                    ├── JsonFileBackend   （默认，行为与原先完全一致）
                    └── SqliteBackend     （WAL 模式，事务安全，支持多进程）

这样切换后端只需改一个环境变量 `STORE_BACKEND=sqlite`，
业务代码零改动。首次切到 SQLite 时会**自动从现有 JSON 文件迁移**数据。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

# backend/app/common/store_backend.py → parents[3] = 仓库根目录
_ROOT = Path(__file__).resolve().parents[3]


def _resolve_dir() -> Path:
    """数据目录：与 `json_store.DATA_DIR` 保持一致（**仓库根/data**）。

    这里必须与 json_store 选同一个目录，否则会出现"换了后端数据看起来变少了"
    ——实测踩过：从 backend/ 目录启动时 cwd 下的 data/ 与仓库根的 data/ 是两份不同的数据，
    按 cwd 解析会读到另一份。
    """
    return _ROOT / "data"


class StoreBackend:
    """存储后端接口。"""

    name = "base"

    def read(self, filename: str) -> Optional[Any]:
        raise NotImplementedError

    def write(self, filename: str, payload: Any) -> None:
        raise NotImplementedError

    def exists(self, filename: str) -> bool:
        raise NotImplementedError

    def path_of(self, filename: str) -> Path:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"backend": self.name}


class JsonFileBackend(StoreBackend):
    """JSON 文件后端：原子写（先写 .tmp 再 os.replace）。"""

    name = "json"

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self._dir = data_dir or _resolve_dir()

    def path_of(self, filename: str) -> Path:
        return self._dir / filename

    def exists(self, filename: str) -> bool:
        return self.path_of(filename).exists()

    def read(self, filename: str) -> Optional[Any]:
        path = self.path_of(filename)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def write(self, filename: str, payload: Any) -> None:
        path = self.path_of(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写临时文件再替换，避免进程中断留下半截文件
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "dir": str(self._dir), "atomic_write": True}


class SqliteBackend(StoreBackend):
    """SQLite 后端：WAL 模式 + 事务，支持多进程安全读写。

    表结构（documents 表：把每个 JSON 文档存一行）::

        CREATE TABLE documents (
            name       TEXT PRIMARY KEY,   -- 原 JSON 文件名，如 ads.json
            payload    TEXT NOT NULL,      -- 文档内容（JSON 文本）
            updated_at TEXT NOT NULL
        )

    为什么用「一个文档一行」而不是把每条广告拆成一行？
    因为本层要保证**业务代码零改动**——store 拿到的仍是完整列表/字典。
    拆表能进一步获得行级并发与 SQL 过滤，那属于下一步的重构；
    当前这一步已经解决了**写放大的原子性与多进程覆盖**问题
    （SQLite 事务 + WAL 保证不会写出半截文件，也不会互相覆盖整个文件）。
    """

    name = "sqlite"

    def __init__(self, db_path: Optional[Path] = None, data_dir: Optional[Path] = None) -> None:
        self._dir = data_dir or _resolve_dir()
        self._db_path = db_path or (self._dir / "store.sqlite")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # 记录所有线程创建的连接，便于统一关闭。
        # Windows 上未关闭的连接会锁住 .sqlite 文件（临时目录删不掉 / 文件无法替换）。
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._closed = False
        self._init_schema()

    # -- 连接管理（每线程一个连接）--
    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        # 创建与登记必须在同一把锁内完成：否则 close() 可能在"已创建但未登记"
        # 的窗口里取走列表，导致这个连接永远不会被关闭（Windows 下锁住文件）。
        with self._conns_lock:
            if self._closed:
                raise sqlite3.ProgrammingError("存储后端已关闭")
            conn = sqlite3.connect(str(self._db_path), timeout=15.0)
            conn.execute("PRAGMA journal_mode=WAL")     # 读写并发
            conn.execute("PRAGMA synchronous=NORMAL")   # 性能与安全的折中
            conn.execute("PRAGMA busy_timeout=15000")
            self._local.conn = conn
            self._all_conns.append(conn)
        return conn

    def _init_schema(self) -> None:
        with self._conn as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS documents (
                       name       TEXT PRIMARY KEY,
                       payload    TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS meta (
                       key TEXT PRIMARY KEY, value TEXT
                   )"""
            )
            conn.commit()

    def path_of(self, filename: str) -> Path:
        """兼容旧调用：返回原 JSON 路径（导出/排查用）。"""
        return self._dir / filename

    def exists(self, filename: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM documents WHERE name=?", (filename,))
        return cur.fetchone() is not None

    def read(self, filename: str) -> Optional[Any]:
        cur = self._conn.execute("SELECT payload FROM documents WHERE name=?", (filename,))
        row = cur.fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            logger.warning("SQLite 中的文档解析失败: %s", filename)
            return None

    def write(self, filename: str, payload: Any) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn as conn:  # with 块结束即提交，异常则回滚
            conn.execute(
                """INSERT INTO documents(name, payload, updated_at) VALUES (?,?,?)
                   ON CONFLICT(name) DO UPDATE SET payload=excluded.payload,
                                                   updated_at=excluded.updated_at""",
                (filename, text, now),
            )
            conn.commit()

    def migrate_from_json(self) -> dict[str, Any]:
        """把现有 JSON 文件导入 SQLite（已存在的文档不覆盖）。

        首次切到 SQLite 时自动调用，保证数据不丢。

        **递归扫描子目录**：数据并不都在 data/ 顶层，例如历史记录在
        `data/history/records.json`、店铺在 `data/stores/<platform>.json`。
        只扫顶层会静默漏掉这些文档（实测踩过：切到 SQLite 后历史记录变 0）。
        文档名用相对 data/ 的 POSIX 路径，与 json_store 的 filename 口径一致。
        """
        imported: list[str] = []
        skipped: list[str] = []
        for path in sorted(self._dir.rglob("*.json")):
            if not path.is_file():
                continue
            name = path.relative_to(self._dir).as_posix()
            if self.exists(name):
                skipped.append(name)
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("迁移跳过 %s（解析失败: %s）", name, exc)
                continue
            self.write(name, payload)
            imported.append(name)

        if imported:
            logger.info("已从 JSON 迁移 %d 个文档到 SQLite: %s", len(imported), imported)
        return {"imported": imported, "skipped": skipped, "db": str(self._db_path)}

    def backup_json(self) -> Optional[Path]:
        """把 SQLite 内容导出回 JSON 目录（便于排查与回退）。"""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = self._dir / f"sqlite-export-{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        cur = self._conn.execute("SELECT name, payload FROM documents")
        count = 0
        for name, payload in cur.fetchall():
            (out_dir / name).write_text(payload, encoding="utf-8")
            count += 1
        if count == 0:
            shutil.rmtree(out_dir, ignore_errors=True)
            return None
        return out_dir

    def describe(self) -> dict[str, Any]:
        return {
            **super().describe(),
            "db": str(self._db_path),
            "journal_mode": "WAL",
            "documents": self._count(),
        }

    def _count(self) -> int:
        try:
            return self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        except sqlite3.Error:
            return 0

    def close(self) -> None:
        """关闭**所有线程**创建的连接（测试与优雅退出用）。

        Windows 上未关闭的连接会锁住 .sqlite 文件，导致临时目录无法删除，
        所以显式提供关闭入口而不是依赖 GC。并发写测试会在多个线程里建连接，
        只关当前线程是不够的。

        关闭前先做一次 WAL checkpoint：把 -wal 里的内容并回主库并截断日志，
        否则 -wal / -shm 文件仍被持有，Windows 下同样删不掉。
        """
        with self._conns_lock:
            self._closed = True
            conns = list(self._all_conns)
            self._all_conns.clear()
            self._local = threading.local()
        for conn in conns:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "SqliteBackend":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False


# ---------------------------------------------------------------------------
# MySQL：真正的服务型数据库（多进程 / 多实例共享）
# ---------------------------------------------------------------------------

class MysqlBackend(StoreBackend):
    """MySQL 后端：服务型数据库，多进程 / 多实例并发安全。

    与 SQLite 的差别（为什么还要这一层）：
      - SQLite 是**单文件**，只能在同一台机器上共享；多实例部署做不到
      - MySQL 是**独立服务**，应用可以是无状态多副本，数据在服务端

    表结构::

        CREATE TABLE documents (
            name       VARCHAR(255) PRIMARY KEY,   -- 相对 data/ 的路径，如 history/records.json
            payload    LONGTEXT NOT NULL,          -- JSON 文本
            updated_at DATETIME NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4

    连接策略：**每线程一条连接 + 使用前 ping 探活**。
    MySQL 有 wait_timeout，长连接会被服务端断开，所以不能只在创建时连一次。
    """

    name = "mysql"

    def __init__(self, data_dir: Optional[Path] = None, config: Optional[dict] = None) -> None:
        self._dir = data_dir or _resolve_dir()
        self._local = threading.local()
        self._conns_lock = threading.Lock()
        self._all_conns: list = []      # 记录所有线程的连接，便于统一关闭
        self._closed = False
        self._cfg = config or {
            "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DATABASE", "ecommerce_agent"),
        }
        self._ensure_database()
        self._init_schema()

    # -- 连接管理 --
    def _new_conn(self):
        import pymysql

        return pymysql.connect(
            host=self._cfg["host"], port=self._cfg["port"],
            user=self._cfg["user"], password=self._cfg["password"],
            database=self._cfg["database"], charset="utf8mb4",
            # autocommit=True：单条语句立即生效，且 SELECT 不持有快照。
            # 若用 False，第一次 SELECT 会开 REPEATABLE READ 事务，
            # 同一连接后续读取永远看到旧快照（多进程/多线程场景下是隐蔽 bug）。
            autocommit=True, connect_timeout=5,
        )

    @property
    def _conn(self):
        if self._closed:
            raise RuntimeError("MySQL 存储后端已关闭")
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.ping(reconnect=True)   # 探活：长连接可能被服务端断开
                return conn
            except Exception:  # noqa: BLE001
                with self._conns_lock:
                    if conn in self._all_conns:
                        self._all_conns.remove(conn)
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        with self._conns_lock:
            conn = self._new_conn()
            self._local.conn = conn
            self._all_conns.append(conn)
        return conn

    def _ensure_database(self) -> None:
        """库不存在则创建（演示/首次运行友好）。"""
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
                """CREATE TABLE IF NOT EXISTS documents (
                       name       VARCHAR(255) PRIMARY KEY,
                       payload    LONGTEXT NOT NULL,
                       updated_at DATETIME NOT NULL
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
            )
        conn.commit()

    # -- StoreBackend 接口 --
    def path_of(self, filename: str) -> Path:
        """兼容旧调用：返回本地 JSON 路径（排查/导出用）。"""
        return self._dir / filename

    def exists(self, filename: str) -> bool:
        conn = self._conn
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM documents WHERE name=%s", (filename,))
            return cur.fetchone() is not None

    def read(self, filename: str) -> Optional[Any]:
        conn = self._conn
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM documents WHERE name=%s", (filename,))
            row = cur.fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            logger.warning("MySQL 中的文档解析失败: %s", filename)
            return None

    def write(self, filename: str, payload: Any) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        conn = self._conn
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO documents(name, payload, updated_at) VALUES (%s,%s,NOW())
                       ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=NOW()""",
                    (filename, text),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def migrate_from_json(self) -> dict[str, Any]:
        """从本地 JSON 递归导入（已存在的不覆盖）。"""
        imported: list[str] = []
        skipped: list[str] = []
        for path in sorted(self._dir.rglob("*.json")):
            if not path.is_file():
                continue
            name = path.relative_to(self._dir).as_posix()
            if self.exists(name):
                skipped.append(name)
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("迁移跳过 %s（解析失败: %s）", name, exc)
                continue
            self.write(name, payload)
            imported.append(name)
        if imported:
            logger.info("已从 JSON 迁移 %d 个文档到 MySQL: %s", len(imported), imported)
        return {"imported": imported, "skipped": skipped,
                "db": f"{self._cfg['host']}:{self._cfg['port']}/{self._cfg['database']}"}

    def _count(self) -> int:
        conn = self._conn
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM documents")
            return int(cur.fetchone()[0])

    def close(self) -> None:
        with self._conns_lock:
            self._closed = True
            conns = list(self._all_conns)
            self._all_conns.clear()
            self._local = threading.local()
        for conn in conns:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "MysqlBackend":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False

    def describe(self) -> dict[str, Any]:
        try:
            doc_count = self._count()
        except Exception:  # noqa: BLE001
            doc_count = -1
        return {
            **super().describe(),
            "host": f"{self._cfg['host']}:{self._cfg['port']}",
            "database": self._cfg["database"],
            "engine": "InnoDB / utf8mb4",
            "documents": doc_count,
        }


# ---------------------------------------------------------------------------
# 后端选择（单例）
# ---------------------------------------------------------------------------

_backend: Optional[StoreBackend] = None
_lock = threading.Lock()


def get_backend() -> StoreBackend:
    """返回当前存储后端（按 `STORE_BACKEND` 环境变量选择）。

    支持 json（默认）/ sqlite / mysql。
    mysql/sqlite 首次启用时会从本地 JSON 递归迁移。
    """
    global _backend
    with _lock:
        if _backend is not None:
            return _backend

        choice = os.getenv("STORE_BACKEND", "json").strip().lower()

        if choice == "mysql":
            try:
                backend = MysqlBackend()
                migrated = backend.migrate_from_json()
                logger.info("存储后端: mysql（%s/%s）；迁移 %d 个文档",
                            backend._cfg["host"], backend._cfg["database"],
                            len(migrated.get("imported", [])))
                _backend = backend
            except Exception as exc:  # noqa: BLE001
                logger.error("MySQL 不可用（%s），回退 JSON 文件后端", exc)
                _backend = JsonFileBackend()
        elif choice == "sqlite":
            backend = SqliteBackend()
            migrated = backend.migrate_from_json()
            logger.info("存储后端: sqlite（%s）；从 JSON 迁移 %d 个文档",
                        backend._db_path, len(migrated.get("imported", [])))
            _backend = backend
        else:
            _backend = JsonFileBackend()
            logger.info("存储后端: json（原子写）；多实例部署请设置 "
                        "STORE_BACKEND=mysql 或 sqlite")
        return _backend


def reset_backend(backend: Optional[StoreBackend] = None) -> None:
    """替换后端（测试用）。"""
    global _backend
    with _lock:
        _backend = backend


def backend_info() -> dict[str, Any]:
    backend = get_backend()
    return {**backend.describe(), "env": os.getenv("STORE_BACKEND", "json")}
