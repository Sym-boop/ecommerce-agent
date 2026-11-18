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
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api import deps
from app.api.deps import Actor
from app.common import pipeline, rbac

router = APIRouter(prefix="/api/v1/review", tags=["review"])

# 项目根目录（data/raw 所在）
_ROOT = Path(__file__).resolve().parents[3]


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


def _banned_word_count() -> int:
    """违禁词表条数（走统一模块，保证与检测口径一致）。"""
    try:
        from app.common.banned_words import status as bw_status
        return int(bw_status().get("word_count") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _banned_word_status() -> dict:
    try:
        from app.common.banned_words import status as bw_status
        return bw_status()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc), "word_count": 0}


@router.get("/config")
async def review_config():
    """审查中心阈值配置（含词表可用状态与替换建议条数）。"""
    bw = _banned_word_status()
    return ok({
        "banned_word_source": bw.get("source") or "data/raw/forbidden_words.txt",
        "banned_word_count": bw.get("word_count", 0),
        "banned_word_available": bw.get("available", False),
        "banned_word_error": bw.get("error"),
        "replacement_count": bw.get("replacement_count", 0),
        "min_budget": pipeline.MIN_BUDGET,
        "max_budget": pipeline.MAX_BUDGET,
        "roles": rbac.describe(),
    })


@router.post("/banned-words/reload")
async def reload_banned_words(actor: Actor = Depends(deps.require_perm(rbac.PERM_ADMIN))):
    """热加载违禁词表（改完词表文件后无需重启服务）。"""
    from app.common import audit
    from app.common.banned_words import reload as bw_reload

    status = bw_reload()
    audit.record("config.reload", target="banned_words", target_type="config",
                 detail={"word_count": status.get("word_count"),
                         "replacement_count": status.get("replacement_count"),
                         "available": status.get("available")},
                 result="ok" if status.get("available") else "failed")
    return ok(status)


@router.post("/check")
async def check_text_api(payload: dict | None = None):
    """违禁词预检（**不落库**）：给前端做实时校验与压测用。

    与 `/copy/{ad_id}/clear` 的区别：这里只跑检测、不写任何状态。
    所以可以放心高频调用——它也是本项目里**唯一的纯 CPU 端点**
    （jieba 分词 + 词边界匹配），压测时用来观察 CPU 密集路径的表现。

    请求：`{"title": "...", "content": "..."}`
    返回：`{"count": n, "ok": bool, "hits": [{word, category, replacement, start, end}]}`
    """
    body = payload or {}
    title = str(body.get("title") or "")
    content = str(body.get("content") or "")
    if not title and not content:
        return fail(400, "title 与 content 至少提供一个")

    from app.common import banned_words

    try:
        # strict=True：词表不可用时抛异常，由下面转成 503（fail-closed）
        hits = banned_words.check(f"{title}\n{content}")
    except banned_words.BannedWordsUnavailable as exc:
        return fail(503, f"风控依赖不可用，拒绝放行：{exc}")

    suggestions = [h for h in hits if h.get("replacement")]
    return ok({
        "count": len(hits),
        "ok": not hits,
        "hits": hits,
        "suggestion_count": len(suggestions),
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
async def clear_copy_review(ad_id: str, payload: dict | None = None,
                            actor: Actor = Depends(deps.require_perm(rbac.PERM_REVIEW))):
    """违禁词审查放行：可用修正后的正文重新扫描，通过后放行。

    若携带了人工修改，该版本会**回流为检索样本**（数据飞轮）。
    """
    from app.common import ad_store

    body = payload or {}
    current = ad_store.get_ad(ad_id)
    if current is None:
        return fail(404, "广告物料不存在")
    deps.enforce_separation_of_duties(actor, current.get("generated_by"), "审查")

    content = body.get("content")
    title = body.get("title")
    ad = pipeline.clear_copy(
        ad_id,
        content=str(content) if content is not None else None,
        title=str(title) if title is not None else None,
        actor=actor.username,
        note=str(body.get("note") or ""),
    )
    if ad is None:
        return fail(404, "广告物料不存在")
    if not ad.get("copy_ok"):
        return fail(400, "修正后仍命中违禁词，请继续处理后再放行")
    return ok(ad)


@router.post("/budget/{ad_id}/clear")
async def clear_budget_review(ad_id: str, payload: dict | None = None,
                              actor: Actor = Depends(deps.require_perm(rbac.PERM_REVIEW))):
    """预算审查放行：携带调整后的预算，重新校验通过后放行。"""
    from app.common import ad_store

    body = payload or {}
    current = ad_store.get_ad(ad_id)
    if current is None:
        return fail(404, "广告物料不存在")
    deps.enforce_separation_of_duties(actor, current.get("generated_by"), "预算审查")

    raw = body.get("budget")
    try:
        budget = float(raw)
    except (TypeError, ValueError):
        return fail(400, "请填写调整后的投放预算")
    if budget <= 0:
        return fail(400, "投放预算必须大于 0")
    ad = pipeline.clear_budget(ad_id, budget, actor=actor.username,
                               note=str(body.get("note") or ""))
    if ad is None:
        return fail(404, "广告物料不存在")
    if not ad.get("budget_ok"):
        return fail(400, f"预算仍不符合要求：{ad.get('budget_msg')}")
    return ok(ad)


@router.post("/approve/{ad_id}")
async def approve_ad(ad_id: str, payload: dict | None = None,
                     actor: Actor = Depends(deps.require_perm(rbac.PERM_APPROVE))):
    """待审批通过：广告进入「广告投放」候选与「已处理」。

    职责分离：**不能审批自己生成的广告**（管理员角色豁免以便单人演示）。
    """
    from app.common import ad_store

    body = payload or {}
    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return fail(404, "广告物料不存在")
    if ad.get("stage") != pipeline.STAGE_PENDING:
        return fail(409, "该广告当前不在待审批状态")
    deps.enforce_separation_of_duties(actor, ad.get("generated_by"), "审批")

    updated = pipeline.approve_ad(ad_id, comment=str(body.get("comment") or ""),
                                 actor=actor.username)
    return ok(updated)


@router.post("/reject/{ad_id}")
async def reject_ad(ad_id: str, payload: dict | None = None,
                    actor: Actor = Depends(deps.require_perm(rbac.PERM_APPROVE))):
    """审批驳回：填写原因后置为已处理。"""
    from app.common import ad_store

    body = payload or {}
    comment = str(body.get("comment") or "").strip()
    if not comment:
        return fail(400, "驳回必须填写原因")
    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return fail(404, "广告物料不存在")
    if ad.get("stage") != pipeline.STAGE_PENDING:
        return fail(409, "该广告当前不在待审批状态")
    deps.enforce_separation_of_duties(actor, ad.get("generated_by"), "审批")

    updated = pipeline.reject_ad(ad_id, comment=comment, actor=actor.username)
    return ok(updated)


# ---------------------------------------------------------------------------
# 审批 SLA：待办积压与超时升级
# ---------------------------------------------------------------------------

SLA_HOURS = 24


def _age_hours(item: dict) -> float:
    """物料在待审批状态停留的小时数（缺少时间戳时返回 0）。"""
    from datetime import datetime

    ts = item.get("created_at") or item.get("handled_at")
    if not ts:
        return 0.0
    try:
        created = datetime.fromisoformat(str(ts).replace("Z", ""))
    except ValueError:
        return 0.0
    return round((datetime.now() - created).total_seconds() / 3600, 2)


@router.get("/sla")
async def review_sla():
    """审批 SLA：待办积压、超时清单与平均等待时长。"""
    pending = pipeline.list_tab("pending")
    copy_review = pipeline.list_tab("copy-review")
    budget_review = pipeline.list_tab("budget-review")

    rows = []
    for item in pending:
        hours = _age_hours(item)
        rows.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "platform": item.get("platform"),
            "waiting_hours": hours,
            "overdue": hours > SLA_HOURS,
            "generated_by": item.get("generated_by"),
        })
    rows.sort(key=lambda r: -r["waiting_hours"])

    waits = [r["waiting_hours"] for r in rows]
    return ok({
        "sla_hours": SLA_HOURS,
        "pending_total": len(pending),
        "pending_overdue": sum(1 for r in rows if r["overdue"]),
        "review_backlog": len(copy_review) + len(budget_review),
        "avg_waiting_hours": round(sum(waits) / len(waits), 2) if waits else 0.0,
        "max_waiting_hours": max(waits) if waits else 0.0,
        "items": rows,
    })


# ---------------------------------------------------------------------------
# 批量审批
# ---------------------------------------------------------------------------

@router.post("/batch")
async def batch_review(payload: dict | None = None,
                       actor: Actor = Depends(deps.require_perm(rbac.PERM_APPROVE))):
    """批量审批：一次性通过 / 驳回多条待审批广告。

    请求体::

        {"ids": ["AD-...", ...], "action": "approve" | "reject", "comment": "..."}

    驳回时 comment 必填。**逐条校验职责分离**，被拒绝的条目单独返回原因，
    不影响其余条目的处理（部分成功语义）。
    """
    from app.common import ad_store, audit

    body = payload or {}
    ids = [str(i).strip() for i in (body.get("ids") or []) if str(i).strip()]
    action = str(body.get("action") or "").strip().lower()
    comment = str(body.get("comment") or "").strip()

    if not ids:
        return fail(400, "请选择要处理的广告")
    if action not in ("approve", "reject"):
        return fail(400, "action 只能是 approve 或 reject")
    if action == "reject" and not comment:
        return fail(400, "批量驳回必须填写原因")

    succeeded: list[str] = []
    skipped: list[dict] = []

    for ad_id in ids:
        ad = ad_store.get_ad(ad_id)
        if ad is None:
            skipped.append({"id": ad_id, "reason": "物料不存在"})
            continue
        if ad.get("stage") != pipeline.STAGE_PENDING:
            skipped.append({"id": ad_id, "reason": f"当前阶段为 {ad.get('stage')}，不在待审批"})
            continue
        try:
            deps.enforce_separation_of_duties(actor, ad.get("generated_by"), "审批")
        except Exception as exc:  # noqa: BLE001  (HTTPException)
            detail = getattr(exc, "detail", str(exc))
            skipped.append({"id": ad_id, "reason": detail})
            continue

        if action == "approve":
            pipeline.approve_ad(ad_id, comment=comment or "批量通过", actor=actor.username)
        else:
            pipeline.reject_ad(ad_id, comment=comment, actor=actor.username)
        succeeded.append(ad_id)

    audit.record("review.batch", target=",".join(ids[:10]), target_type="batch",
                 detail={"action": action, "succeeded": len(succeeded),
                         "skipped": len(skipped), "comment": comment},
                 result="ok" if succeeded else "skipped")

    return ok({
        "action": action,
        "succeeded": succeeded,
        "skipped": skipped,
        "succeeded_count": len(succeeded),
        "skipped_count": len(skipped),
    })