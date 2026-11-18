"""广告流水线状态机与阶段评估（生成 → 审查 → 审批 → 投放 → 历史）。

在 ad_store 之上封装一套「广告从生成到发售」的状态流转，供审查中心、
广告投放、历史发售三处界面共享同一份事实来源：

    review ──(违禁词 + 预算 均通过)──► pending ──审批通过──► approved
       │                                                 │
       │                                            投放成功
       │                                                 ▼
       └──────────────审批驳回──► rejected            delivered

阶段评估：
    - 违禁词：复用 milvus_cli.check_banned_words（data/raw/forbidden_words.txt）
    - 预算：MIN_BUDGET ~ MAX_BUDGET 区间校验（过低 / 超支 均需人工处置）

审查中心四个页签的取数：
    copy-review    stage=review 且命中违禁词
    budget-review  stage=review 且预算异常
    pending        stage=pending
    processed      stage=approved / rejected
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.common import ad_store

logger = logging.getLogger("uvicorn.error")

# 投放预算审查区间（元）：过低无有效曝光、超支存在资金风险，均需人工处置
MIN_BUDGET = 1000
MAX_BUDGET = 100000

CURRENT_OPERATOR = "当前登录运营"

# 阶段常量
STAGE_REVIEW = "review"
STAGE_PENDING = "pending"
STAGE_APPROVED = "approved"
STAGE_DELIVERED = "delivered"
STAGE_REJECTED = "rejected"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 阶段评估
# ---------------------------------------------------------------------------

def check_copy(text: str, *, strict: bool = True) -> list[dict]:
    """违禁词扫描，返回命中明细（含替换建议）。

    风控是 **fail-closed** 的：词表不可用时抛 `BannedWordsUnavailable`，
    由调用方拒绝推进，而不是当作「无违禁词」放行。

    历史上这里是 `except: hits = []` —— 词表读不到就把所有文案判为通过，
    属于方向性错误（检索可以降级，风控不能降级）；现已改为向上抛出。

    参数:
        strict: False 时退回旧行为（返回空列表），仅用于不做风控的展示场景。
    """
    from app.common.banned_words import BannedWordsUnavailable, check

    try:
        hits = check(text or "") or []
    except BannedWordsUnavailable:
        if strict:
            raise
        hits = []
    return [
        {
            "word": h.get("word"),
            "type": h.get("category") or "违禁词",
            "suggestion": h.get("replacement") or "",
            "context": h.get("context") or "",
            "start": h.get("start", -1),
        }
        for h in hits
    ]


def budget_verdict(budget: float) -> tuple[str, str]:
    """按投放区间给出预算审查结论：over（超支）/ low（过低）/ normal（正常）。"""
    if budget > MAX_BUDGET:
        return "over", f"预算超支：¥{budget:,.0f} 超过单次投放上限 ¥{MAX_BUDGET:,}"
    if budget < MIN_BUDGET:
        return "low", f"预算过低：¥{budget:,.0f} 低于最低投放门槛 ¥{MIN_BUDGET:,}，难以获得有效曝光"
    return "normal", f"预算正常：处于 ¥{MIN_BUDGET:,} ~ ¥{MAX_BUDGET:,} 合理区间"


def check_budget(budget: float | None) -> tuple[bool, str, str]:
    """预算审查：未设置预算视为通过。"""
    if budget is None:
        return True, "normal", "未设置投放预算（跳过预算审查）"
    verdict, msg = budget_verdict(float(budget))
    return verdict == "normal", verdict, msg


def _stage_from(copy_ok: bool, budget_ok: bool) -> str:
    return STAGE_PENDING if (copy_ok and budget_ok) else STAGE_REVIEW


# ---------------------------------------------------------------------------
# 生成登记
# ---------------------------------------------------------------------------

def register_ad(*, ad_type: str, title: str = "", prompt: str = "", platform: str = "",
                content: str = "", input_image: str | None = None,
                result_url: str | None = None, budget: float | None = None,
                actor: str = "", category: str = "", source: str = "ai",
                session_id: str = "") -> dict:
    """生成完成后登记广告：自动完成违禁词 / 预算审查并落定初始阶段。

    fail-closed：词表不可用时**不判定为通过**，而是标记 `review_unavailable`
    并留在 review 阶段等待人工处置。

    category / source / session_id 用于「数据飞轮」：人工认可的样本需要带上
    类目与平台才能作为下次检索的参考。
    """
    from app.common import audit, metrics, versions
    from app.common.banned_words import BannedWordsUnavailable

    text = f"{title or ''} {content or ''}"
    try:
        violations = check_copy(text)
        review_unavailable = False
    except BannedWordsUnavailable as exc:
        # 风控不可用 → 不允许登记为「通过」。标记为待人工处置并留痕。
        logger.error("违禁词表不可用，登记 fail-closed: %s", exc)
        violations = []
        review_unavailable = True
        metrics.incr("review_fail_closed")

    copy_ok = (not violations) and not review_unavailable
    budget_ok, verdict, budget_msg = check_budget(budget)
    ad = ad_store.add_ad(
        ad_type=ad_type,
        title=title,
        prompt=prompt,
        platform=platform,
        content=content,
        input_image=input_image,
        result_url=result_url,
        budget=budget,
        copy_ok=copy_ok,
        budget_ok=budget_ok,
        violations=violations,
        review_unavailable=review_unavailable,
        budget_verdict=verdict,
        budget_msg=budget_msg,
        stage=_stage_from(copy_ok, budget_ok),
        generated_by=actor or CURRENT_OPERATOR,
        category=category,
        source=source,
        session_id=session_id,
    )

    # 版本链起点：留下"AI 原始生成"这一版，后续人工修改才有对比基准
    if content or title:
        versions.record(ad["id"], title=title, content=content,
                        actor=actor or CURRENT_OPERATOR, note="AI 原始生成")

    # 幂等命中（同内容重复保存）不重复记审计，避免噪声
    deduped = bool(ad.pop("_deduped", False))
    ad["deduped"] = deduped
    if deduped:
        metrics.incr("dedupe_hit")
        audit.record("ad.register.deduped", target=ad["id"],
                     detail={"type": ad_type, "title": title[:60]}, result="deduped")
    else:
        audit.record("ad.register", target=ad["id"],
                     detail={"type": ad_type, "title": title[:60], "stage": ad.get("stage"),
                             "copy_ok": copy_ok, "violations": [v.get("word") for v in violations]})
    return ad


# ---------------------------------------------------------------------------
# 阶段推进
# ---------------------------------------------------------------------------

def _advance_if_ready(ad: dict | None) -> dict | None:
    """两种审查均通过时，把广告从 review 推进到 pending。"""
    if ad and ad.get("stage") == STAGE_REVIEW and ad.get("copy_ok") and ad.get("budget_ok"):
        ad = ad_store.update_ad(ad["id"], stage=STAGE_PENDING)
    return ad


def clear_copy(ad_id: str, content: str | None = None, title: str | None = None,
               actor: str = "", note: str = "") -> dict | None:
    """违禁词审查放行：可携带修正后的标题 / 正文，重新扫描通过后放行。

    这是**数据飞轮的关键入口**：如果本次放行**携带了人工修改**，说明
    "人类给出了比模型更好的版本"，该版本会被回写为检索样本，
    让下次同类目生成能参考它。
    """
    from app.common import audit, knowledge_feedback, metrics, versions

    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return None

    before = {"title": ad.get("title") or "", "content": ad.get("content") or ""}
    patch: dict = {}
    if title is not None:
        patch["title"] = title
    if content is not None:
        patch["content"] = content

    new_title = patch.get("title", before["title"])
    new_content = patch.get("content", before["content"])
    edited = (new_title != before["title"]) or (new_content != before["content"])

    text = f"{new_title} {new_content}"
    violations = check_copy(text)
    patch["violations"] = violations
    patch["copy_ok"] = not violations
    if not violations:
        patch["review_unavailable"] = False

    updated = _advance_if_ready(ad_store.update_ad(ad_id, **patch))

    audit.record("review.copy_clear", target=ad_id,
                 before=before, after={"title": new_title, "content": new_content},
                 detail={"edited": edited, "violations": [v.get("word") for v in violations],
                         "note": note},
                 result="ok" if not violations else "still_violating")

    # 只在**真正放行**时记版本：仍命中违禁词的尝试会被拒绝，
    # 不该产生"看起来像已修正"的版本记录（否则版本链会出现重复的空版本）
    if edited and not violations:
        versions.record(ad_id, title=new_title, content=new_content,
                        actor=actor or CURRENT_OPERATOR, note=note or "违禁词审查修正")

    # 数据飞轮：人工修正且已通过 → 回写为检索样本
    if edited and not violations and updated is not None:
        try:
            feedback = knowledge_feedback.contribute(
                ad_id=ad_id,
                text=f"{new_title}\n{new_content}",
                title=new_title,
                platform=str(ad.get("platform") or ""),
                category=str(ad.get("category") or ""),
                actor=actor or CURRENT_OPERATOR,
                note="违禁词审查人工修正后放行",
            )
            metrics.incr(f"flywheel_{feedback.get('mode', 'unknown')}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("数据飞轮回写失败 ad=%s: %s", ad_id, exc)

    return updated


def clear_budget(ad_id: str, budget: float, actor: str = "", note: str = "") -> dict | None:
    """预算审查放行：携带调整后的预算，重新校验通过后放行。"""
    from app.common import audit

    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return None
    before = {"budget": ad.get("budget")}
    budget_ok, verdict, msg = check_budget(float(budget))
    ad = ad_store.update_ad(
        ad_id, budget=float(budget), budget_ok=budget_ok,
        budget_verdict=verdict, budget_msg=msg,
    )
    audit.record("review.budget_clear", target=ad_id,
                 before=before, after={"budget": float(budget)},
                 detail={"verdict": verdict, "message": msg, "note": note},
                 result="ok" if budget_ok else "still_abnormal")
    return _advance_if_ready(ad)


def approve_ad(ad_id: str, comment: str = "", actor: str = "") -> dict | None:
    """待审批通过：推进到 approved，进入广告投放候选。"""
    from app.common import audit, metrics

    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return None
    before = {"stage": ad.get("stage")}
    updated = ad_store.update_ad(
        ad_id, stage=STAGE_APPROVED, handled_at=_now_iso(),
        handler=actor or CURRENT_OPERATOR, comment=comment or "同意",
    )
    audit.record("review.approve", target=ad_id, before=before,
                 after={"stage": STAGE_APPROVED}, detail={"comment": comment or "同意"})
    metrics.incr("approved")

    # 审批通过也意味着"这份内容被人类认可"——若无人工改动，
    # 仍把它作为正样本回流（有改动的情况已在 clear_copy 回流过）
    if not (ad.get("source") == "human_feedback"):
        try:
            from app.common import knowledge_feedback
            knowledge_feedback.contribute(
                ad_id=ad_id,
                text=f"{ad.get('title') or ''}\n{ad.get('content') or ''}",
                title=str(ad.get("title") or ""),
                platform=str(ad.get("platform") or ""),
                category=str(ad.get("category") or ""),
                actor=actor or CURRENT_OPERATOR,
                note="审批通过的正样本",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("审批样本回流失败 ad=%s: %s", ad_id, exc)
    return updated


def reject_ad(ad_id: str, comment: str = "", actor: str = "") -> dict | None:
    """审批驳回：置为 rejected，退出流水线并进入已处理。"""
    from app.common import audit, metrics

    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return None
    before = {"stage": ad.get("stage")}
    updated = ad_store.update_ad(
        ad_id, stage=STAGE_REJECTED, handled_at=_now_iso(),
        handler=actor or CURRENT_OPERATOR, comment=comment,
    )
    audit.record("review.reject", target=ad_id, before=before,
                 after={"stage": STAGE_REJECTED}, detail={"comment": comment})
    metrics.incr("rejected")
    return updated


# ---------------------------------------------------------------------------
# 分阶段取数
# ---------------------------------------------------------------------------

def list_tab(tab: str) -> list[dict]:
    """按审查中心的页签取数。"""
    ads = ad_store.list_ads()
    if tab == "copy-review":
        return [a for a in ads if a.get("stage") == STAGE_REVIEW and not a.get("copy_ok", True)]
    if tab == "budget-review":
        return [a for a in ads if a.get("stage") == STAGE_REVIEW and not a.get("budget_ok", True)]
    if tab == "pending":
        return [a for a in ads if a.get("stage") == STAGE_PENDING]
    if tab == "processed":
        return [a for a in ads if a.get("stage") in (STAGE_APPROVED, STAGE_REJECTED)]
    return ads


def stats() -> dict:
    """各阶段数量统计（供角标与汇总展示）。"""
    ads = ad_store.list_ads()
    return {
        "copy_review": sum(
            1 for a in ads if a.get("stage") == STAGE_REVIEW and not a.get("copy_ok", True)
        ),
        "budget_review": sum(
            1 for a in ads if a.get("stage") == STAGE_REVIEW and not a.get("budget_ok", True)
        ),
        "pending": sum(1 for a in ads if a.get("stage") == STAGE_PENDING),
        "approved": sum(1 for a in ads if a.get("stage") == STAGE_APPROVED),
        "delivered": sum(1 for a in ads if a.get("stage") == STAGE_DELIVERED),
        "rejected": sum(1 for a in ads if a.get("stage") == STAGE_REJECTED),
    }