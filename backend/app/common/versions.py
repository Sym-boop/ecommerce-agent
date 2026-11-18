"""素材版本管理（保留每次修改的版本链 + 支持 diff 对比与回滚）。

**为什么需要**

审查场景下「谁在什么时候把哪个词改成了什么」是**合规审计的硬需求**。
原先 `PUT /copies/{id}` 直接覆盖内容，改前的版本就丢了，
既没法对比（运营想知道改了什么），也没法回滚（改坏了想退回）。

**存储**

`data/versions.json`：`{ad_id: [版本1, 版本2, ...]}`，每个版本是一条快照：
`{version, at, actor, title, content, note}`。版本号从 1 递增。

**为什么不做真正的行级 diff 存储**

存全量快照最简单可靠，且文案长度有限（几百字），存储代价可接受；
diff 在**读取时**按需计算，避免存 diff 链带来的"必须按顺序重放"的脆弱性。
"""
from __future__ import annotations

import difflib
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.common import json_store

logger = logging.getLogger("uvicorn.error")

_LOCK = threading.RLock()
_VERSION_FILE = "versions.json"
_VERSIONS: dict[str, list[dict]] = json_store.read_dict(_VERSION_FILE, {})

MAX_VERSIONS_PER_AD = 50


def _persist() -> None:
    json_store.write_json(_VERSION_FILE, _VERSIONS)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def record(ad_id: str, *, title: str = "", content: str = "",
           actor: str = "", note: str = "") -> dict[str, Any]:
    """追加一个版本快照（内容与上一版相同则跳过，避免噪声版本）。"""
    with _LOCK:
        chain = _VERSIONS.setdefault(ad_id, [])
        if chain:
            last = chain[-1]
            if (last.get("title") or "") == (title or "") and (last.get("content") or "") == (content or ""):
                return last
        entry = {
            "version": len(chain) + 1,
            "at": _now(),
            "actor": actor or "",
            "title": title or "",
            "content": content or "",
            "note": note or "",
        }
        chain.append(entry)
        if len(chain) > MAX_VERSIONS_PER_AD:
            # 保留第一版（原始生成）与最近若干版，便于对比"最初 vs 现在"
            _VERSIONS[ad_id] = [chain[0]] + chain[-(MAX_VERSIONS_PER_AD - 1):]
            for i, e in enumerate(_VERSIONS[ad_id], start=1):
                e["version"] = i
        _persist()
        return entry


def history(ad_id: str) -> list[dict[str, Any]]:
    """返回某条素材的版本链（按版本号升序）。"""
    with _LOCK:
        return [dict(v) for v in _VERSIONS.get(ad_id, [])]


def latest(ad_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        chain = _VERSIONS.get(ad_id) or []
        return dict(chain[-1]) if chain else None


def diff(ad_id: str, from_version: Optional[int] = None,
         to_version: Optional[int] = None, field: str = "content") -> dict[str, Any]:
    """计算两个版本之间的差异（默认：第一版 → 最新版）。

    返回统一 diff 行列表（`difflib.unified_diff`），以及增删行数统计。
    """
    chain = history(ad_id)
    if not chain:
        return {"ad_id": ad_id, "available": False, "reason": "该素材没有版本记录"}

    def pick(v: Optional[int], default_idx: int) -> dict[str, Any]:
        if v is None:
            return chain[default_idx]
        for item in chain:
            if item.get("version") == v:
                return item
        return chain[default_idx]

    left = pick(from_version, 0)
    right = pick(to_version, len(chain) - 1)
    a = str(left.get(field) or "").splitlines()
    b = str(right.get(field) or "").splitlines()

    lines = list(difflib.unified_diff(
        a, b,
        fromfile=f"v{left.get('version')}", tofile=f"v{right.get('version')}",
        lineterm="", n=2,
    ))
    added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    return {
        "ad_id": ad_id,
        "available": True,
        "field": field,
        "from": {"version": left.get("version"), "at": left.get("at"), "actor": left.get("actor")},
        "to": {"version": right.get("version"), "at": right.get("at"), "actor": right.get("actor")},
        "lines": lines,
        "added": added,
        "removed": removed,
        "identical": a == b,
        "version_count": len(chain),
    }


def version_of(ad_id: str, version: int) -> Optional[dict[str, Any]]:
    """取指定版本的快照（供回滚时读取内容）。"""
    for item in history(ad_id):
        if item.get("version") == version:
            return item
    return None


def stats() -> dict[str, Any]:
    """版本库概览。"""
    with _LOCK:
        total = sum(len(v) for v in _VERSIONS.values())
        multi = sum(1 for v in _VERSIONS.values() if len(v) > 1)
        return {
            "tracked_ads": len(_VERSIONS),
            "total_versions": total,
            "ads_with_multiple_versions": multi,
            "max_per_ad": MAX_VERSIONS_PER_AD,
        }


def _resolve_root() -> Path:
    return json_store.path_of(_VERSION_FILE)
