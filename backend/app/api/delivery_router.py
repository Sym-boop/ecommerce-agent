"""广告投放 API（Mock 模拟）。

    广告投放中心：选择目标平台 + 勾选已审批的广告物料 → 一键投放。
    本模块以 Mock 方式模拟第三方投放平台的返回，不产生真实投放。

    GET  /api/v1/delivery/platforms → 可投放的目标平台列表
    GET  /api/v1/delivery/config    → 预算审查区间配置
    POST /api/v1/delivery/submit    JSON: { platform, ad_ids: [..], budget }
        预算审查：过低（< MIN_BUDGET）或超支（> MAX_BUDGET）将被拦截，返回 400
        → { batch_id, platform, platform_label, status, submitted_at, budget, budget_verdict, items[], success_count, failed_count }
    GET  /api/v1/delivery/list      → 历史投放批次（按时间倒序）

投放成功后会把对应广告在 ad_store 中标记为已投放（status=delivered）。
"""
from __future__ import annotations

import random
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.common import ad_store

router = APIRouter(prefix="/api/v1/delivery", tags=["delivery"])

# 可投放的目标平台（Mock 常量）
DELIVERY_PLATFORMS = [
    {"value": "juliangqianchuan", "label": "巨量千川", "category": "信息流投放"},
    {"value": "xiaohongshu", "label": "小红书", "category": "种草投放"},
    {"value": "douyin", "label": "抖音", "category": "短视频投放"},
    {"value": "wechat_channels", "label": "微信视频号", "category": "视频号投放"},
    {"value": "kuaishou", "label": "快手", "category": "短视频投放"},
    {"value": "tmall", "label": "天猫", "category": "电商站内"},
    {"value": "jd", "label": "京东", "category": "电商站内"},
    {"value": "amazon", "label": "亚马逊", "category": "跨境电商"},
    {"value": "tiktok", "label": "TikTok", "category": "跨境短视频"},
]
_PLATFORM_MAP = {p["value"]: p for p in DELIVERY_PLATFORMS}

# 投放预算审查区间（元）：过低无有效曝光、超支存在资金风险，均需人工审查
MIN_BUDGET = 1000
MAX_BUDGET = 100000

# 投放批次记录（进程内内存存储，需单 worker 运行）
_LOCK = threading.Lock()
_DELIVERIES: list[dict] = []


def budget_verdict(budget: float) -> tuple[str, str]:
    """按投放区间给出预算审查结论：over（超支）/ low（过低）/ normal（正常）。"""
    if budget > MAX_BUDGET:
        return "over", f"预算超支：¥{budget:,.0f} 超过单次投放上限 ¥{MAX_BUDGET:,}"
    if budget < MIN_BUDGET:
        return "low", f"预算过低：¥{budget:,.0f} 低于最低投放门槛 ¥{MIN_BUDGET:,}，难以获得有效曝光"
    return "normal", f"预算正常：处于 ¥{MIN_BUDGET:,} ~ ¥{MAX_BUDGET:,} 合理区间"


def list_records() -> list[dict]:
    """历史投放批次快照（供审查中心读取）。"""
    with _LOCK:
        return list(_DELIVERIES)


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


@router.get("/platforms")
async def list_platforms():
    """返回可投放的目标平台列表。"""
    return ok(DELIVERY_PLATFORMS)


@router.get("/config")
async def delivery_config():
    """预算审查区间配置（供投放页与审查中心展示）。"""
    return ok({
        "min_budget": MIN_BUDGET,
        "max_budget": MAX_BUDGET,
        "platforms": len(DELIVERY_PLATFORMS),
    })


@router.get("/list")
async def list_deliveries():
    """历史投放批次（按时间倒序）。"""
    with _LOCK:
        return ok(list(_DELIVERIES))


@router.post("/submit")
async def submit_delivery(payload: dict | None = None):
    """一键投放：把勾选的广告投递到目标平台（Mock），投放前先做预算审查。"""
    body = payload or {}
    platform = str(body.get("platform") or "").strip()
    ad_ids = [str(a) for a in (body.get("ad_ids") or []) if str(a).strip()]
    raw_budget = body.get("budget")

    if platform not in _PLATFORM_MAP:
        return fail(400, "请选择有效的目标平台")
    if not ad_ids:
        return fail(400, "请至少勾选一条广告物料")

    # ★ 预算审查：必填且在合理区间内
    try:
        budget = float(raw_budget)
    except (TypeError, ValueError):
        return fail(400, "请填写投放预算")
    if budget <= 0:
        return fail(400, "投放预算必须大于 0")
    verdict, verdict_msg = budget_verdict(budget)
    if verdict != "normal":
        return fail(400, f"投放预算审查未通过：{verdict_msg}")

    # 取出待投放的广告物料（以 ad_store 为准）
    catalog = {ad["id"]: ad for ad in ad_store.list_ads()}
    selected = [catalog[a] for a in ad_ids if a in catalog]
    if not selected:
        return fail(404, "勾选的广告物料不存在")

    platform_meta = _PLATFORM_MAP[platform]
    submitted_at = datetime.now()
    batch_id = f"DLV-{submitted_at.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"

    items: list[dict] = []
    success_ids: list[str] = []
    for ad in selected:
        deliver_id = f"{platform.upper()[:4]}-{uuid.uuid4().hex[:8].upper()}"
        # Mock：绝大多数投放成功，偶发失败以贴近真实平台
        success = random.random() > 0.05
        item = {
            "ad_id": ad["id"],
            "ad_type": ad.get("type", ""),
            "title": ad.get("title", ""),
            "status": "success" if success else "failed",
            "deliver_id": deliver_id if success else None,
            "reach": random.randint(2000, 50000) if success else 0,
            "error": None if success else "Mock：平台侧审核未通过，请稍后重试",
        }
        items.append(item)
        if success:
            success_ids.append(ad["id"])

    # 投放成功的广告在物料仓库中标记为已投放
    ad_store.mark_delivered(success_ids, platform=platform)

    record = {
        "batch_id": batch_id,
        "platform": platform,
        "platform_label": platform_meta["label"],
        "platform_category": platform_meta["category"],
        "status": "已投放" if success_ids else "投放失败",
        "submitted_at": submitted_at.isoformat(timespec="seconds"),
        "budget": budget,
        "budget_verdict": verdict,
        "budget_verdict_msg": verdict_msg,
        "items": items,
        "success_count": len(success_ids),
        "failed_count": len(items) - len(success_ids),
    }
    with _LOCK:
        _DELIVERIES.insert(0, record)
    return ok(record)