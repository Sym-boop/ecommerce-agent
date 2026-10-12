"""审查中心 API（广告流水线的审查 / 审批枢纽）。

围绕「生成 → 审查 → 审批 → 投放 → 历史」的广告流水线，按阶段提供取数与处置：

    GET  /api/v1/review/config              审查阈值配置（违禁词来源 + 预算区间）
    GET  /api/v1/review/stats               各阶段数量统计（角标 / 汇总）
    GET  /api/v1/review/copy                违禁词审查：命中违禁词的广告
    GET  /api/v1/review/budget              预算审查：预算异常的广告
    GET  /api/v1/review/pending             待审批：两种审查均通过的广告
    GET  /api/v1/review/processed           已处理：审批通过 / 驳回的广告
    POST /api/v1/review/copy/{ad_id}/clear  违禁词放行（可携带修正后的正文）
    POST /api/v1/review/budget/{ad_id}/clear 预算放行（携带调整后的预算）
    POST /api/v1/review/approve/{ad_id}     审批通过 → 进入广告投放
    POST /api/v1/review/reject/{ad_id}      审批驳回 → 进入已处理

违禁词来源：data/raw/forbidden_words.txt（复用 milvus_cli.check_banned_words 真实链路）。
预算区间：MIN_BUDGET ~ MAX_BUDGET（见 app.common.pipeline）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.common import pipeline

router = APIRouter(prefix="/api/v1/review", tags=["review"])

# 项目根目录（data/raw 所在）
_ROOT = Path(__file__).resolve().parents[3]


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


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
        "min_budget": pipeline.MIN_BUDGET,
        "max_budget": pipeline.MAX_BUDGET,
    })


@router.get("/stats")
async def review_stats():
    """各阶段数量统计。"""
    return ok(pipeline.stats())


@router.get("/copy")
async def review_copy():
    """违禁词审查：列出命中违禁词、待人工处置的广告。"""
    items = pipeline.list_tab("copy-review")
    return ok({
        "items": items,
        "total": len(items),
        "failed_count": len(items),
        "hit_word_total": sum(len(it.get("violations") or []) for it in items),
    })


@router.get("/budget")
async def review_budget():
    """预算审查：列出预算超支 / 过低、待人工处置的广告。"""
    items = pipeline.list_tab("budget-review")
    return ok({
        "min_budget": pipeline.MIN_BUDGET,
        "max_budget": pipeline.MAX_BUDGET,
        "items": items,
        "total": len(items),
        "abnormal_count": len(items),
    })


@router.get("/pending")
async def review_pending():
    """待审批：违法 / 预算审查均通过，等待人工审批的广告。"""
    items = pipeline.list_tab("pending")
    return ok({"items": items, "total": len(items)})


@router.get("/processed")
async def review_processed():
    """已处理：审批通过 / 驳回的广告。"""
    items = pipeline.list_tab("processed")
    return ok({"items": items, "total": len(items)})


@router.post("/copy/{ad_id}/clear")
async def clear_copy_review(ad_id: str, payload: dict | None = None):
    """违禁词审查放行：可用修正后的正文重新扫描，通过后放行。"""
    body = payload or {}
    content = body.get("content")
    title = body.get("title")
    ad = pipeline.clear_copy(
        ad_id,
        content=str(content) if content is not None else None,
        title=str(title) if title is not None else None,
    )
    if ad is None:
        return fail(404, "广告物料不存在")
    if not ad.get("copy_ok"):
        return fail(400, "修正后仍命中违禁词，请继续处理后再放行")
    return ok(ad)


@router.post("/budget/{ad_id}/clear")
async def clear_budget_review(ad_id: str, payload: dict | None = None):
    """预算审查放行：携带调整后的预算，重新校验通过后放行。"""
    body = payload or {}
    raw = body.get("budget")
    try:
        budget = float(raw)
    except (TypeError, ValueError):
        return fail(400, "请填写调整后的投放预算")
    if budget <= 0:
        return fail(400, "投放预算必须大于 0")
    ad = pipeline.clear_budget(ad_id, budget)
    if ad is None:
        return fail(404, "广告物料不存在")
    if not ad.get("budget_ok"):
        return fail(400, f"预算仍不符合要求：{ad.get('budget_msg')}")
    return ok(ad)


@router.post("/approve/{ad_id}")
async def approve_ad(ad_id: str, payload: dict | None = None):
    """待审批通过：广告进入「广告投放」候选与「已处理」。"""
    body = payload or {}
    from app.common import ad_store
    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return fail(404, "广告物料不存在")
    if ad.get("stage") != pipeline.STAGE_PENDING:
        return fail(409, "该广告当前不在待审批状态")
    updated = pipeline.approve_ad(ad_id, comment=str(body.get("comment") or ""))
    return ok(updated)


@router.post("/reject/{ad_id}")
async def reject_ad(ad_id: str, payload: dict | None = None):
    """审批驳回：填写原因后置为已处理。"""
    body = payload or {}
    comment = str(body.get("comment") or "").strip()
    if not comment:
        return fail(400, "驳回必须填写原因")
    from app.common import ad_store
    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return fail(404, "广告物料不存在")
    if ad.get("stage") != pipeline.STAGE_PENDING:
        return fail(409, "该广告当前不在待审批状态")
    updated = pipeline.reject_ad(ad_id, comment=comment)
    return ok(updated)