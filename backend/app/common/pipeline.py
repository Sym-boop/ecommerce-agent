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

from datetime import datetime

from app.common import ad_store
from app.common.milvus_cli import check_banned_words

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

def check_copy(text: str) -> list[dict]:
    """违禁词扫描，返回命中明细（Milvus 不可用时降级为空，不阻断流程）。"""
    try:
        hits = check_banned_words(text or "") or []
    except Exception:  # noqa: BLE001
        hits = []
    return [
        {
            "word": h.get("word"),
            "type": h.get("category") or "违禁词",
            "suggestion": h.get("replacement") or "",
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
                result_url: str | None = None, budget: float | None = None) -> dict:
    """生成完成后登记广告：自动完成违禁词 / 预算审查并落定初始阶段。"""
    text = f"{title or ''} {content or ''}"
    violations = check_copy(text)
    copy_ok = not violations
    budget_ok, verdict, budget_msg = check_budget(budget)
    return ad_store.add_ad(
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
        budget_verdict=verdict,
        budget_msg=budget_msg,
        stage=_stage_from(copy_ok, budget_ok),
    )


# ---------------------------------------------------------------------------
# 阶段推进
# ---------------------------------------------------------------------------

def _advance_if_ready(ad: dict | None) -> dict | None:
    """两种审查均通过时，把广告从 review 推进到 pending。"""
    if ad and ad.get("stage") == STAGE_REVIEW and ad.get("copy_ok") and ad.get("budget_ok"):
        ad = ad_store.update_ad(ad["id"], stage=STAGE_PENDING)
    return ad


def clear_copy(ad_id: str, content: str | None = None, title: str | None = None) -> dict | None:
    """违禁词审查放行：可携带修正后的标题 / 正文，重新扫描通过后放行。"""
    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return None
    patch: dict = {}
    if title is not None:
        patch["title"] = title
    if content is not None:
        patch["content"] = content
    text = f"{patch.get('title', ad.get('title') or '')} {patch.get('content', ad.get('content') or '')}"
    violations = check_copy(text)
    patch["violations"] = violations
    patch["copy_ok"] = not violations
    return _advance_if_ready(ad_store.update_ad(ad_id, **patch))


def clear_budget(ad_id: str, budget: float) -> dict | None:
    """预算审查放行：携带调整后的预算，重新校验通过后放行。"""
    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return None
    budget_ok, verdict, msg = check_budget(float(budget))
    ad = ad_store.update_ad(
        ad_id, budget=float(budget), budget_ok=budget_ok,
        budget_verdict=verdict, budget_msg=msg,
    )
    return _advance_if_ready(ad)


def approve_ad(ad_id: str, comment: str = "") -> dict | None:
    """待审批通过：推进到 approved，进入广告投放候选。"""
    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return None
    return ad_store.update_ad(
        ad_id, stage=STAGE_APPROVED, handled_at=_now_iso(),
        handler=CURRENT_OPERATOR, comment=comment or "同意",
    )


def reject_ad(ad_id: str, comment: str = "") -> dict | None:
    """审批驳回：置为 rejected，退出流水线并进入已处理。"""
    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return None
    return ad_store.update_ad(
        ad_id, stage=STAGE_REJECTED, handled_at=_now_iso(),
        handler=CURRENT_OPERATOR, comment=comment,
    )


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