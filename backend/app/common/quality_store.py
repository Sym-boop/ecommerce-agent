"""质量评估记录仓库（JSON 落盘）。

`quality.evaluate()` 是纯函数，本模块负责**把评估结果存下来**，
这样质量看板才能看到趋势（而不是只有"此刻"的快照）。
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Optional

from app.common import json_store, quality

logger = logging.getLogger("uvicorn.error")

_LOCK = threading.RLock()
_FILE = "quality_evals.json"
_EVALS: list[dict] = json_store.read_list(_FILE, [])

MAX_RECORDS = 2000


def _persist() -> None:
    json_store.write_json(_FILE, _EVALS)


def record(*, ad_id: str = "", session_id: str = "", platform: str = "",
           title: str = "", content: str = "", tags: Optional[list] = None,
           sell_points: Optional[list] = None, violations: Optional[list] = None,
           reference_count: int = 0, source: str = "generation",
           actor: str = "") -> dict[str, Any]:
    """评估并落盘一条记录，返回完整评估结果。"""
    result = quality.evaluate(
        title=title, content=content, tags=tags, platform=platform,
        sell_points=sell_points, violations=violations,
        reference_count=reference_count,
    )
    entry = {
        "id": f"EV-{uuid.uuid4().hex[:10]}",
        "at": datetime.now().isoformat(timespec="seconds"),
        "ad_id": ad_id,
        "session_id": session_id,
        "source": source,
        "actor": actor,
        "title": (title or "")[:120],
        **result,
    }
    with _LOCK:
        _EVALS.insert(0, entry)
        if len(_EVALS) > MAX_RECORDS:
            del _EVALS[MAX_RECORDS:]
        _persist()
    return entry


def list_evals(limit: int = 200, platform: str = "") -> list[dict]:
    with _LOCK:
        rows = list(_EVALS)
    if platform:
        rows = [r for r in rows if (r.get("platform") or "") == platform]
    return rows[:limit]


def dashboard(limit: int = 500) -> dict[str, Any]:
    """质量看板数据：汇总 + 分布 + 低分样本。"""
    rows = list_evals(limit=limit)
    summary = quality.summarize(rows)

    # 分数分桶（0-59 / 60-69 / 70-79 / 80-89 / 90-100）
    buckets = {"<60": 0, "60-69": 0, "70-79": 0, "80-89": 0, "90-100": 0}
    for r in rows:
        s = float(r.get("score") or 0)
        if s < 60:
            buckets["<60"] += 1
        elif s < 70:
            buckets["60-69"] += 1
        elif s < 80:
            buckets["70-79"] += 1
        elif s < 90:
            buckets["80-89"] += 1
        else:
            buckets["90-100"] += 1

    # 低分样本（用于定位问题）
    low = sorted(rows, key=lambda r: float(r.get("score") or 0))[:5]

    # 指标通过率
    if rows:
        def rate(key: str) -> float:
            hit = sum(1 for r in rows if (r.get("metrics") or {}).get(key))
            return round(hit / len(rows), 3)

        rates = {
            "format_ok_rate": rate("format_ok"),
            "violation_free_rate": rate("violation_free"),
            "length_ok_rate": rate("length_ok"),
            "tag_count_ok_rate": rate("tag_count_ok"),
        }
    else:
        rates = {}

    return {
        "summary": summary,
        "score_distribution": buckets,
        "metric_rates": rates,
        "low_score_samples": [
            {"id": r.get("id"), "score": r.get("score"), "title": r.get("title"),
             "platform": r.get("platform"), "at": r.get("at"),
             "missing": (r.get("metrics") or {}).get("sell_points_missing")}
            for r in low
        ],
        "total_records": len(rows),
    }


def stats() -> dict[str, Any]:
    with _LOCK:
        return {"total": len(_EVALS), "max": MAX_RECORDS,
                "latest": _EVALS[0]["at"] if _EVALS else None}
