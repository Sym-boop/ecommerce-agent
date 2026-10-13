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

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_STORE_PATH = _ROOT / "data" / "history" / "records.json"

_LOCK = threading.Lock()
_LOADED = False
_RECORDS: list[dict] = []

STAGE_NORMAL = "normal"
STAGE_WIP = "wip"


def _load() -> list[dict]:
    if not _STORE_PATH.exists():
        return []
    try:
        raw = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
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
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(
        json.dumps(_RECORDS, ensure_ascii=False, indent=2), encoding="utf-8",
    )


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