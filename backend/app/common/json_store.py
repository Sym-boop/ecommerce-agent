"""统一数据持久化入口（可切换 JSON 文件 / SQLite 后端）。

项目内多个 store（广告物料 / 投放批次 / 批量生成会话 / 计费账目 / 版本链 / 质量记录 …）
需要跨重启保留数据。**所有 store 都只经过本模块**，因此这里是插入存储后端的唯一入口。

后端由 `store_backend.py` 提供，用 `STORE_BACKEND` 环境变量切换：

    STORE_BACKEND=json     默认。原子写盘（先写 .tmp 再 os.replace），
                           避免进程中断留下半截文件。
    STORE_BACKEND=sqlite   SQLite（WAL）。事务保证不会写出半截文件，
                           且支持多进程/多 worker 并发读写，解决 JSON 的写放大与覆盖问题。

**对外接口保持不变**，业务代码无需改动；`DATA_DIR` 仍指向 JSON 目录，
便于排查与导出（SQLite 后端下 `path_of()` 返回该目录下的同名路径）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.common import store_backend

# backend/app/common/json_store.py → parents[3] = 仓库根目录
_ROOT = Path(__file__).resolve().parents[3]
# 保持模块级可写：demo_bootstrap.py 等会重定向该目录（沙箱/测试场景）
DATA_DIR = _ROOT / "data"


def _backend():
    """取得当前后端。

    注意：`demo_bootstrap.py`（沙箱适配启动器）与测试会重定向模块级 `DATA_DIR`。
    这里把重定向同步给 JSON 后端，保证读写落在同一个目录上。
    """
    backend = store_backend.get_backend()
    if isinstance(backend, store_backend.JsonFileBackend) and backend._dir != DATA_DIR:
        backend._dir = DATA_DIR
    return backend


def path_of(filename: str) -> Path:
    """返回数据文件名对应的路径（filename 为 data/ 下的相对文件名）。"""
    return DATA_DIR / filename


def exists(filename: str) -> bool:
    """数据是否已存在（用于判断要不要写入演示数据）。"""
    if filename in _MEMORY_OVERRIDES:
        return True
    return _backend().exists(filename)


def read_json(filename: str, default: Any = None) -> Any:
    if filename in _MEMORY_OVERRIDES:
        return _MEMORY_OVERRIDES[filename]
    value = _backend().read(filename)
    return default if value is None else value


def read_list(filename: str, default: list | None = None) -> list:
    fallback = default if default is not None else []
    value = read_json(filename, fallback)
    return value if isinstance(value, list) else fallback


def read_dict(filename: str, default: dict | None = None) -> dict:
    fallback = default if default is not None else {}
    value = read_json(filename, fallback)
    return value if isinstance(value, dict) else fallback


def write_json(filename: str, payload: Any) -> None:
    """写入（后端决定原子性实现：JSON 原子替换 / SQLite 事务）。

    `use_memory()` 生效期间只写内存、**不落盘**——单测必须走这条路径，
    否则会把真实数据文件覆盖掉。
    """
    if filename in _MEMORY_OVERRIDES:
        _MEMORY_OVERRIDES[filename] = payload
        return
    _backend().write(filename, payload)


# ---------------------------------------------------------------------------
# 测试隔离：内存覆盖
# ---------------------------------------------------------------------------
# 背景（实测踩过）：store 模块在 import 时就 `read_list(...)` 载入内存，
# 并在每次变更时 `write_json` 全量落盘。单元测试若直接改 `ad_store._ADS`
# 并触发 `_persist()`，就会**把仓库里真实的 data/*.json 覆盖掉**。
# 因此测试必须先把相关文档切到内存覆盖，确保磁盘不受影响。

_MEMORY_OVERRIDES: dict[str, Any] = {}


def use_memory(filename: str, payload: Any = None) -> None:
    """把某个文档切到内存（测试用）。payload 为 None 时按空列表初始化。"""
    _MEMORY_OVERRIDES[filename] = [] if payload is None else payload


def is_memory(filename: str) -> bool:
    return filename in _MEMORY_OVERRIDES


def clear_memory() -> None:
    _MEMORY_OVERRIDES.clear()
