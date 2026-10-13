"""批量生成会话（分叉树）存储。

「批量生成」页以 Dify 风格的分叉树展示一次批量生成的全部产出：

    商品根 → 文案节点（批量 N，1 ≤ N ≤ 8）
                 ├─ 图文节点（批量 M，1 ≤ M ≤ 5）
                 └─ 视频节点（批量 M，1 ≤ M ≤ 5）

每个节点都可单独「重新生成」；每一级（文案 / 图文 / 视频）支持「一键保存 / 一键重新生成」。
节点保存在进程内内存中（需单 worker 运行），批量生成结束后可整体保存到广告流水线与历史记录。

节点字段：
    id, type(copy|image|video), parent_id, index, title, content, tags,
    url, input_image_url, review, status(pending|processing|done|failed),
    error, attempt, created_at
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime

_LOCK = threading.Lock()
_SESSIONS: dict[str, dict] = {}

MAX_COPY_BATCH = 8
MAX_ASSET_BATCH = 5


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def create_session(*, product: dict, keyword: str = "", platform: str = "xiaohongshu") -> dict:
    """新建一个批量生成会话（分叉树根）。"""
    batch_id = f"BATCH-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    session = {
        "batch_id": batch_id,
        "product": product or {},
        "keyword": keyword or "",
        "platform": platform or "xiaohongshu",
        "nodes": [],
        "created_at": _now(),
    }
    with _LOCK:
        _SESSIONS[batch_id] = session
    return session


def get_session(batch_id: str) -> dict | None:
    with _LOCK:
        return _SESSIONS.get(batch_id)


def delete_session(batch_id: str) -> bool:
    with _LOCK:
        return _SESSIONS.pop(batch_id, None) is not None


def add_node(batch_id: str, *, node_type: str, parent_id: str | None = None,
             index: int = 0, **fields) -> dict | None:
    """在会话中新增一个节点（文案 / 图文 / 视频）。"""
    with _LOCK:
        session = _SESSIONS.get(batch_id)
        if session is None:
            return None
        node = {
            "id": f"N-{uuid.uuid4().hex[:8]}",
            "type": node_type,
            "parent_id": parent_id,
            "index": index,
            "title": "",
            "content": "",
            "tags": [],
            "url": None,
            "input_image_url": None,
            "review": {},
            "status": "pending",
            "error": None,
            "attempt": 1,
            "created_at": _now(),
        }
        node.update(fields)
        session["nodes"].append(node)
        return dict(node)


def get_node(batch_id: str, node_id: str) -> dict | None:
    with _LOCK:
        session = _SESSIONS.get(batch_id)
        if session is None:
            return None
        return next((n for n in session["nodes"] if n["id"] == node_id), None)


def update_node(batch_id: str, node_id: str, **patch) -> dict | None:
    with _LOCK:
        session = _SESSIONS.get(batch_id)
        if session is None:
            return None
        node = next((n for n in session["nodes"] if n["id"] == node_id), None)
        if node is None:
            return None
        node.update(patch)
        return dict(node)


def snapshot(batch_id: str) -> dict | None:
    """返回会话快照（含根商品信息与全部节点）。"""
    with _LOCK:
        session = _SESSIONS.get(batch_id)
        if session is None:
            return None
        return {
            "batch_id": session["batch_id"],
            "product": dict(session["product"]),
            "keyword": session["keyword"],
            "platform": session["platform"],
            "created_at": session["created_at"],
            "nodes": [dict(n) for n in session["nodes"]],
            "max_copy_batch": MAX_COPY_BATCH,
            "max_asset_batch": MAX_ASSET_BATCH,
        }