"""通用 JSON 文件持久化（进程重启后数据不丢）。

项目内多个 store（广告物料 / 投放批次 / 批量生成会话 / 计费账目 …）需要跨重启保留数据。
本模块提供统一的「原子读 / 原子写」能力：写盘时先写临时文件再 os.replace，
避免进程在写入过程中被中断导致文件损坏。

数据统一落在 <仓库根>/data/ 下，与 store_store.py / history_store.py / product_store.py 一致。

说明：Milvus 是向量检索引擎（用于违禁词相似度匹配），不适合存广告物料这类结构化记录；
结构化数据的本地持久化统一采用 JSON 文件。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# backend/app/common/json_store.py → parents[3] = 仓库根目录
_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = _ROOT / "data"


def path_of(filename: str) -> Path:
    """返回数据文件名对应的绝对路径（filename 为 data/ 下的相对文件名）。"""
    return DATA_DIR / filename


def exists(filename: str) -> bool:
    """数据文件是否已存在（用于判断要不要写入演示数据）。"""
    return path_of(filename).exists()


def read_json(filename: str, default: Any = None) -> Any:
    path = path_of(filename)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_list(filename: str, default: list | None = None) -> list:
    fallback = default if default is not None else []
    value = read_json(filename, fallback)
    return value if isinstance(value, list) else fallback


def read_dict(filename: str, default: dict | None = None) -> dict:
    fallback = default if default is not None else {}
    value = read_json(filename, fallback)
    return value if isinstance(value, dict) else fallback


def write_json(filename: str, payload: Any) -> None:
    """原子写盘：先写 .tmp 再替换，避免出现半截的损坏文件。"""
    path = path_of(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)