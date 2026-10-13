"""广告投放 API（Mock 模拟）。

四大投放平台（收敛自原 9 个 mock 平台），每个平台含品牌主题色、浅色背景与介绍：
    qianchuan  巨量千川（抖音）
    juguang    聚光（小红书）
    alimama    阿里妈妈（淘宝/天猫）
    duoduo     多多进宝（拼多多）

端点：
    GET  /api/v1/delivery/platforms                可投放平台列表（含介绍 / 主题色）
    GET  /api/v1/delivery/platforms/{key}          某平台详细介绍
    GET  /api/v1/delivery/platforms/{key}/ads      该平台的「未投放广告」与「投放历史广告」
    GET  /api/v1/delivery/platforms/{key}/deliveries 该平台的历史投放批次
    GET  /api/v1/delivery/config                   预算审查区间配置
    GET  /api/v1/delivery/list                     全部历史投放批次
    POST /api/v1/delivery/submit                   一键投放（仅允许 stage=approved）

投放成功后会把对应广告在 ad_store 中标记为已投放（stage=delivered）。
本模块以 Mock 方式模拟第三方平台返回，不产生真实投放。
"""
from __future__ import annotations

import random
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.common import ad_store, json_store
from app.common.pipeline import MIN_BUDGET, MAX_BUDGET, budget_verdict

router = APIRouter(prefix="/api/v1/delivery", tags=["delivery"])

# 四大投放平台（theme_color 为品牌主色，bg_color 为界面浅色背景）
DELIVERY_PLATFORMS = [
    {
        "value": "qianchuan",
        "label": "巨量千川",
        "belong": "抖音",
        "category": "短视频信息流",
        "theme_color": "#161823",
        "bg_color": "#f5f6f8",
        "logo_key": "douyin",
        "intro": "抖音电商官方一体化智能营销平台，覆盖直播、短视频、搜索与商城全场景投放。",
        "features": ["全域推广", "直播间直投", "短视频带货", "智能出价"],
        "detail": (
            "巨量千川是抖音电商面向商家的智能营销平台，整合广告投放、直播间引流与商品推广能力。"
            "依托抖音的超大流量池与推荐算法，可将广告精准分发给潜在消费人群。"
            "支持「极速推广」与「专业推广」两种模式：前者一键托管、适合新手；"
            "后者可自定义人群包、出价与创意组合，适合精细化运营的成熟商家。"
        ),
    },
    {
        "value": "juguang",
        "label": "聚光",
        "belong": "小红书",
        "category": "内容种草投放",
        "theme_color": "#ff2442",
        "bg_color": "#fff5f6",
        "logo_key": "xiaohongshu",
        "intro": "小红书官方商业化广告平台，以真人笔记与搜索场景驱动高转化的种草投放。",
        "features": ["笔记加热", "搜索推广", "人群定向", "达人合作"],
        "detail": (
            "聚光平台是小红书面向品牌与商家的商业化投放工具，核心在于「内容种草 + 搜索承接」。"
            "通过信息流笔记推广与搜索关键词竞价，把优质种草内容推送给高意向用户。"
            "平台强调真实、有温度的社区内容，适合美妆、服饰、家居等决策链路较长的品类，"
            "配合蒲公英达人合作可实现从种草到成交的闭环。"
        ),
    },
    {
        "value": "alimama",
        "label": "阿里妈妈",
        "belong": "淘宝 / 天猫",
        "category": "电商站内投放",
        "theme_color": "#ff6a00",
        "bg_color": "#fff8f2",
        "logo_key": "alimama",
        "intro": "阿里巴巴旗下数字营销平台，覆盖淘宝天猫站内搜索、展示与推荐场景。",
        "features": ["万相台", "直通车", "引力魔方", "品牌广告"],
        "detail": (
            "阿里妈妈是阿里巴巴集团统一的数字营销平台，串联淘宝、天猫的搜索、展示与推荐流量。"
            "旗下「万相台无界版」整合直通车、引力魔方等产品，可一站式完成新品冷启动、"
            "爆款拉新与店铺复购运营。依托阿里电商数据资产，实现从曝光到成交的品效合一，"
            "是淘系商家日常投放的主力阵地。"
        ),
    },
    {
        "value": "duoduo",
        "label": "多多进宝",
        "belong": "拼多多",
        "category": "社交电商投放",
        "theme_color": "#e02e24",
        "bg_color": "#fff5f4",
        "logo_key": "pinduoduo",
        "intro": "拼多多官方推广平台，以高性价比流量与社交裂变助力商品快速起量。",
        "features": ["多多搜索", "多多场景", "百亿补贴", "拼团裂变"],
        "detail": (
            "多多进宝是拼多多面向商家的推广平台，包含「多多搜索」与「多多场景」两大核心工具。"
            "搜索版聚焦关键词竞价、精准承接购买意图；场景版覆盖推荐信息流，助力商品快速积累销量与评价。"
            "结合百亿补贴与拼团玩法，可在低价心智人群中实现高性价比的爆发式起量，"
            "尤其适合白牌与产业带商家。"
        ),
    },
]
_PLATFORM_MAP = {p["value"]: p for p in DELIVERY_PLATFORMS}

# 投放批次记录（落盘到 <仓库根>/data/deliveries.json，进程重启后不丢）
_LOCK = threading.Lock()
_DEL_FILE = "deliveries.json"
_DELIVERIES: list[dict] = json_store.read_list(_DEL_FILE, [])


def _persist_deliveries() -> None:
    """把投放批次原子写盘（调用方需持有 _LOCK）。"""
    json_store.write_json(_DEL_FILE, _DELIVERIES)


def list_records() -> list[dict]:
    """历史投放批次快照。"""
    with _LOCK:
        return list(_DELIVERIES)


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


def _platform_or_none(key: str) -> dict | None:
    return _PLATFORM_MAP.get((key or "").strip())


@router.get("/platforms")
async def list_platforms():
    """返回可投放的四大平台列表（含介绍 / 主题色 / 品牌归属）。"""
    return ok(DELIVERY_PLATFORMS)


@router.get("/platforms/{key}")
async def platform_detail(key: str):
    """某平台的详细介绍与投放概况。"""
    meta = _platform_or_none(key)
    if meta is None:
        return fail(404, "投放平台不存在")
    with _LOCK:
        batches = [d for d in _DELIVERIES if d["platform"] == meta["value"]]
    ads = ad_store.list_ads()
    delivered = [a for a in ads if a.get("stage") == "delivered"
                 and a.get("delivery_platform") == meta["value"]]
    return ok({
        **meta,
        "delivery_batch_count": len(batches),
        "delivered_ad_count": len(delivered),
        "recent_batches": batches[:5],
    })


@router.get("/platforms/{key}/ads")
async def platform_ads(key: str):
    """该平台可选的广告：未投放（stage=approved）与投放历史（stage=delivered）。"""
    meta = _platform_or_none(key)
    if meta is None:
        return fail(404, "投放平台不存在")
    ads = ad_store.list_ads()
    available = [a for a in ads if a.get("stage") == "approved"]
    history = [a for a in ads if a.get("stage") == "delivered"
               and a.get("delivery_platform") == meta["value"]]
    return ok({"available": available, "history": history,
               "available_count": len(available), "history_count": len(history)})


@router.get("/platforms/{key}/deliveries")
async def platform_deliveries(key: str):
    """该平台的历史投放批次（按时间倒序）。"""
    meta = _platform_or_none(key)
    if meta is None:
        return fail(404, "投放平台不存在")
    with _LOCK:
        batches = [d for d in _DELIVERIES if d["platform"] == meta["value"]]
    return ok(batches)


@router.delete("/platforms/{key}/ads/{ad_id}")
async def delete_platform_ad(key: str, ad_id: str):
    """删除一条广告物料（投放界面清理未投放 / 历史广告）。"""
    meta = _platform_or_none(key)
    if meta is None:
        return fail(404, "投放平台不存在")
    if ad_store.get_ad(ad_id) is None:
        return fail(404, "广告物料不存在或已被删除")
    ad_store.delete_ad(ad_id)
    return ok({"ad_id": ad_id, "deleted": True})


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
    """全部历史投放批次（按时间倒序）。"""
    with _LOCK:
        return ok(list(_DELIVERIES))


@router.post("/submit")
async def submit_delivery(payload: dict | None = None):
    """一键投放：把勾选的广告投递到目标平台（Mock），投放前先做预算审查。"""
    body = payload or {}
    platform = str(body.get("platform") or "").strip()
    ad_ids = [str(a) for a in (body.get("ad_ids") or []) if str(a).strip()]
    raw_budget = body.get("budget")

    meta = _platform_or_none(platform)
    if meta is None:
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

    # 取出待投放的广告物料（以 ad_store 为准）；仅允许审批通过（stage=approved）的物料
    catalog = {ad["id"]: ad for ad in ad_store.list_ads()}
    missing = [a for a in ad_ids if a not in catalog]
    if missing:
        return fail(404, "勾选的广告物料不存在")
    selected = [catalog[a] for a in ad_ids]
    # 允许「已审批通过」的物料投放，也允许「已投放」的物料再次投放；
    # 其余阶段（审查中 / 待审批 / 已驳回）一律拦截。
    allowed_stages = {"approved", "delivered"}
    blocked = [ad["id"] for ad in selected if ad.get("stage") not in allowed_stages]
    if blocked:
        return fail(400, "存在未审批通过的广告物料，请先在「审查中心」完成审查与审批")

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

    # 投放成功的广告在物料仓库中标记为已投放（进入「历史广告发售」）
    ad_store.mark_delivered(success_ids, platform=platform)

    record = {
        "batch_id": batch_id,
        "platform": platform,
        "platform_label": meta["label"],
        "platform_belong": meta["belong"],
        "platform_category": meta["category"],
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
        _persist_deliveries()
    return ok(record)