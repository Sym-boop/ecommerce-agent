"""审查中心 API（只读审查，不改变业务数据）。

    GET /api/v1/review/config → 审查阈值配置（违禁词来源 + 投放预算区间）
    GET /api/v1/review/copy   → 对全部「文案广告」做违禁词审查，返回命中明细与建议
    GET /api/v1/review/budget → 对历史投放批次做预算审查（超支 / 过低 / 正常）

违禁词来源：data/raw/forbidden_words.txt（复用 milvus_cli.check_banned_words 真实链路）。
预算区间：MIN_BUDGET ~ MAX_BUDGET（复用 delivery_router 的审查阈值）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from app.common import ad_store
from app.common.milvus_cli import check_banned_words
from app.api import delivery_router

router = APIRouter(prefix="/api/v1/review", tags=["review"])

# 项目根目录（data/raw 所在）
_ROOT = Path(__file__).resolve().parents[3]


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def _banned_word_count() -> int:
    fp = _ROOT / "data" / "raw" / "forbidden_words.txt"
    if not fp.exists():
        return 0
    with fp.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


@router.get("/config")
async def review_config():
    """审查中心阈值配置。"""
    return ok({
        "banned_word_source": "data/raw/forbidden_words.txt",
        "banned_word_count": _banned_word_count(),
        "min_budget": delivery_router.MIN_BUDGET,
        "max_budget": delivery_router.MAX_BUDGET,
    })


@router.get("/copy")
async def review_copy():
    """文案违禁词审查：逐条扫描文案广告的标题与正文。"""
    items: list[dict] = []
    for ad in ad_store.list_ads():
        if ad.get("type") != "copy":
            continue
        text = f"{ad.get('title') or ''} {ad.get('content') or ''}"
        try:
            hits = check_banned_words(text) or []
        except Exception:  # noqa: BLE001
            hits = []
        violations = [
            {
                "word": h.get("word"),
                "type": h.get("category") or "违禁词",
                "suggestion": h.get("replacement") or "",
            }
            for h in hits
        ]
        items.append({
            "ad_id": ad.get("id"),
            "title": ad.get("title") or "",
            "content": ad.get("content") or "",
            "platform": ad.get("platform") or "",
            "created_at": ad.get("created_at"),
            "violations": violations,
            "passed": not violations,
        })
    return ok({
        "items": items,
        "total": len(items),
        "failed_count": sum(1 for it in items if not it["passed"]),
        "passed_count": sum(1 for it in items if it["passed"]),
    })


@router.get("/budget")
async def review_budget():
    """投放预算审查：逐条判断历史投放批次预算是否超支 / 过低。"""
    items: list[dict] = []
    for rec in delivery_router.list_records():
        budget = float(rec.get("budget") or 0)
        verdict, message = delivery_router.budget_verdict(budget)
        items.append({
            "batch_id": rec.get("batch_id"),
            "platform": rec.get("platform"),
            "platform_label": rec.get("platform_label"),
            "submitted_at": rec.get("submitted_at"),
            "status": rec.get("status"),
            "budget": budget,
            "success_count": rec.get("success_count", 0),
            "failed_count": rec.get("failed_count", 0),
            "verdict": verdict,
            "message": message,
        })
    return ok({
        "min_budget": delivery_router.MIN_BUDGET,
        "max_budget": delivery_router.MAX_BUDGET,
        "items": items,
        "total": len(items),
        "abnormal_count": sum(1 for it in items if it["verdict"] != "normal"),
    })