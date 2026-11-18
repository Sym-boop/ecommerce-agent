"""广告生成历史记录（正常 / 半成品）。

「广告生成」页在生成前可载入历史记录作为模板继续生成；生成结束时可选择：
    - 存到投放界面  → 写入一条正常记录（stage=normal）
    - 暂停生成并退出 → 写入一条半成品记录（stage=wip，前端红色标签）
    - 删除此次生成  → 若已落盘则删除该记录

采用 JSON 落盘（data/history/records.json），保证多端一致、跨页面可复用。

记录字段：
    id, product{name,category,sell_points}, keyword, platform,
    copy{title,content,tags}, image_url, video_url, stage(normal|wip), created_at
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.common import json_store

_ROOT = Path(__file__).resolve().parents[3]
# 数据文件名（相对 data/）：读写统一走 json_store，以复用存储后端与测试隔离
_STORE_FILE = "history/records.json"

_LOCK = threading.Lock()
_LOADED = False
_RECORDS: list[dict] = []

STAGE_NORMAL = "normal"
STAGE_WIP = "wip"


def _load() -> list[dict]:
    raw = json_store.read_json(_STORE_FILE, [])
    return [r for r in (raw if isinstance(raw, list) else []) if isinstance(r, dict) and r.get("id")]


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    with _LOCK:
        if _LOADED:
            return
        _RECORDS.extend(_load())
        _LOADED = True


def _flush() -> None:
    """落盘（经 json_store → 走当前存储后端，并支持测试内存隔离）。"""
    json_store.write_json(_STORE_FILE, _RECORDS)


def list_records() -> list[dict]:
    """返回全部历史记录（按时间倒序）。"""
    _ensure_loaded()
    with _LOCK:
        return sorted(_RECORDS, key=lambda r: r.get("created_at") or "", reverse=True)


def get_record(record_id: str) -> dict | None:
    _ensure_loaded()
    with _LOCK:
        return next((r for r in _RECORDS if r["id"] == record_id), None)


def save_record(*, product: dict, keyword: str = "", platform: str = "",
                copy: dict | None = None, image_url: str | None = None,
                video_url: str | None = None, stage: str = STAGE_NORMAL,
                record_id: str | None = None) -> dict:
    """新增或更新一条历史记录。

    传入 record_id 时做「更新」（用于同一会话多次保存），否则新建。
    """
    _ensure_loaded()
    created = datetime.now()
    with _LOCK:
        record = next((r for r in _RECORDS if r["id"] == record_id), None) if record_id else None
        if record is None:
            record = {
                "id": f"REC-{created.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
                "created_at": created.isoformat(timespec="seconds"),
            }
            _RECORDS.insert(0, record)
        record.update({
            "product": product or {},
            "keyword": keyword or "",
            "platform": platform or "",
            "copy": copy or {},
            "image_url": image_url,
            "video_url": video_url,
            "stage": stage if stage in (STAGE_NORMAL, STAGE_WIP) else STAGE_NORMAL,
        })
        _flush()
        return dict(record)


def mark_wip(record_id: str) -> dict | None:
    """把记录标记为半成品（红色标签）。"""
    _ensure_loaded()
    with _LOCK:
        record = next((r for r in _RECORDS if r["id"] == record_id), None)
        if record is None:
            return None
        record["stage"] = STAGE_WIP
        _flush()
        return dict(record)


def delete_record(record_id: str) -> bool:
    """删除一条历史记录，返回是否删除成功。"""
    _ensure_loaded()
    with _LOCK:
        before = len(_RECORDS)
        _RECORDS[:] = [r for r in _RECORDS if r["id"] != record_id]
        removed = len(_RECORDS) != before
        if removed:
            _flush()
        return removed