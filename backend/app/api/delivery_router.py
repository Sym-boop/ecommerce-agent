"""广告投放 API。

**重要：这里的「投放」分两种，不要混**

| 平台 | capability | 实际行为 |
|---|---|---|
| 巨量千川 | `real_ready` | 走 `douyin_delivery_agent`：预检 → 提交 → 记真实计划 ID；模式由 `QIANCHUAN_DELIVERY_MODE` 决定（默认 mock） |
| 聚光 / 阿里妈妈 / 多多进宝 | `reserved` | **没有实现**。接口直接返回 501，按钮在前端置灰 |

为什么这么改（历史问题）：原实现给四个平台统一发「随机 95% 成功 + 随机触达 2000~50000」的
模拟回执，页面上还写着 Mock。结果是**既承认是模拟，又编了成功率和触达量**——
面试官一句「触达数据从哪来」就崩，而且这种半真半假比纯 mock 更伤可信度。

现在的原则：**要么真，要么明说没有。不编数。**

端点：
    GET    /platforms                    平台列表（含 capability）
    GET    /capabilities                 各平台 Agent 能力与缺什么（前端横幅用）
    GET    /platforms/{key}              平台详情与投放概况
    GET    /platforms/{key}/ads          该平台可选 / 已投放广告
    GET    /platforms/{key}/deliveries   该平台历史批次
    GET    /config                       预算区间配置
    GET    /list                         全部历史批次
    POST   /submit                       一键投放（仅 real_ready 平台）
    POST   /stop                         关停已投放计划（安全网）
    DELETE /platforms/{key}/ads/{ad_id}  删除物料
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.agent import douyin_delivery_agent
from app.common import ad_store, json_store, store_store
from app.common.pipeline import MIN_BUDGET, MAX_BUDGET, budget_verdict

router = APIRouter(prefix="/api/v1/delivery", tags=["delivery"])

# 四大投放平台。capability 决定前端按钮能否点、后端是否放行：
#   real_ready  有真实代码路径（可能因未授权而走 mock，但链路是真的）
#   reserved    仅展示位，没有实现
DELIVERY_PLATFORMS = [
    {
        "value": "qianchuan",
        "label": "巨量千川",
        "belong": "抖音",
        "category": "短视频信息流",
        "theme_color": "#161823",
        "bg_color": "#f5f6f8",
        "logo_key": "douyin",
        "capability": "real_ready",
        "intro": "抖音电商官方一体化智能营销平台，覆盖直播、短视频、搜索与商城全场景投放。",
        "features": ["全域推广", "直播间直投", "短视频带货", "智能出价"],
        "detail": (
            "巨量千川是抖音电商面向商家的智能营销平台，整合广告投放、直播间引流与商品推广能力。"
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
        "capability": "reserved",
        "intro": "小红书官方商业化广告平台，以真人笔记与搜索场景驱动高转化的种草投放。",
        "features": ["笔记加热", "搜索推广", "人群定向", "达人合作"],
        "detail": (
            "聚光平台是小红书面向品牌与商家的商业化投放工具，核心在于「内容种草 + 搜索承接」。"
            "**本平台投递 Agent 尚未接入**，当前仅作为平台信息展示位。"
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
        "capability": "reserved",
        "intro": "阿里巴巴旗下数字营销平台，覆盖淘宝天猫站内搜索、展示与推荐场景。",
        "features": ["万相台", "直通车", "引力魔方", "品牌广告"],
        "detail": (
            "阿里妈妈是阿里巴巴集团统一的数字营销平台，串联淘宝、天猫的搜索、展示与推荐流量。"
            "**本平台投递 Agent 尚未接入**，当前仅作为平台信息展示位。"
            "阿里妈妈走服务商体系（企业主体 + 服务商资质 + 保证金），个人开发者无法对接。"
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
        "capability": "reserved",
        "intro": "拼多多官方推广平台，以高性价比流量与社交裂变助力商品快速起量。",
        "features": ["多多搜索", "多多场景", "百亿补贴", "拼团裂变"],
        "detail": (
            "多多进宝是拼多多面向商家的推广平台，包含「多多搜索」与「多多场景」两大核心工具。"
            "**本平台投递 Agent 尚未接入**，当前仅作为平台信息展示位。"
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
    """可投放平台列表（含 capability：real_ready 可投 / reserved 仅展示）。"""
    return ok(DELIVERY_PLATFORMS)


@router.get("/capabilities")
async def capabilities():
    """各平台投递 Agent 的能力与「还缺什么」。

    前端用它在页头渲染提示：千川显示模式与缺项，其余显示「未接入」。
    同时也是**面试时最有说服力的一段**——差在哪写死在接口里，不是口头解释。
    """
    result = []
    for p in DELIVERY_PLATFORMS:
        if p["capability"] == "real_ready":
            info = douyin_delivery_agent.capability()
        else:
            info = {
                "agent": None,
                "capability": "reserved",
                "mode": None,
                "authorized": False,
                "blockers": ["该平台投递 Agent 未实现（仅平台信息展示位）"],
            }
        result.append({"platform": p["value"], "label": p["label"], **info})
    return ok(result)


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


# ---------------------------------------------------------------------------
# 投放
# ---------------------------------------------------------------------------
def _default_store() -> dict:
    """取已登记的千川店铺（前端只传平台+物料+预算，其余参数从登记信息补齐）。"""
    stores = store_store.list_stores("qianchuan")
    return stores[0] if stores else {}


def _build_qianchuan_payload(ad: dict, body: dict, budget: float) -> dict:
    """把「物料 + 请求参数 + 已登记店铺」拼成千川提交参数。

    前端只传 platform / ad_ids / budget（见 DeliveryCenter.vue），
    所以这里要从登记店铺补 shop_id / product_id / aweme_id，
    再从物料补素材与标题。缺哪个由预检报出来，**不在这里瞎填默认值**。
    """
    store = _default_store()
    products = store.get("products") or []
    first_product = products[0] if products else {}
    return {
        "advertiser_id": body.get("advertiser_id") or store.get("advertiser_id", ""),
        "shop_id": body.get("shop_id") or store.get("shop_id", ""),
        "product_id": body.get("product_id") or first_product.get("product_id", ""),
        "aweme_id": body.get("aweme_id") or store.get("aweme_id", ""),
        "image_id": body.get("image_id") or "",
        "video_id": body.get("video_id") or "",
        "marketing_goal": body.get("marketing_goal") or "短视频带货",
        "budget": budget,
        "bid": body.get("bid"),
        "roi_goal": body.get("roi_goal"),
        "name": body.get("name") or f"{ad.get('title') or '未命名素材'}-投放",
    }


@router.post("/submit")
async def submit_delivery(payload: dict | None = None):
    """一键投放。

    - `reserved` 平台 → **501**，不做任何模拟（前端按钮本就是置灰的）
    - `real_ready` 平台 → 走投递 Agent：预检 → 提交 → 记录真实结果
    - **幂等**：同一物料已在该平台投过（有 platform_plan_id）则跳过，返回原计划 ID，
      避免重复提交产生多条真实计划、重复扣费
    """
    body = payload or {}
    platform = str(body.get("platform") or "").strip()
    ad_ids = [str(a) for a in (body.get("ad_ids") or []) if str(a).strip()]
    raw_budget = body.get("budget")

    meta = _platform_or_none(platform)
    if meta is None:
        return fail(400, "请选择有效的目标平台")
    if meta["capability"] != "real_ready":
        return fail(501, f"「{meta['label']}」投递 Agent 尚未接入，当前仅支持巨量千川")
    if not ad_ids:
        return fail(400, "请至少勾选一条广告物料")

    # 预算审查：必填且在合理区间内
    try:
        budget = float(raw_budget)
    except (TypeError, ValueError):
        return fail(400, "请填写投放预算")
    if budget <= 0:
        return fail(400, "投放预算必须大于 0")
    verdict, verdict_msg = budget_verdict(budget)
    if verdict != "normal":
        return fail(400, f"投放预算审查未通过：{verdict_msg}")

    catalog = {ad["id"]: ad for ad in ad_store.list_ads()}
    missing = [a for a in ad_ids if a not in catalog]
    if missing:
        return fail(404, "勾选的广告物料不存在")
    selected = [catalog[a] for a in ad_ids]
    allowed_stages = {"approved", "delivered"}
    blocked = [ad["id"] for ad in selected if ad.get("stage") not in allowed_stages]
    if blocked:
        return fail(400, "存在未审批通过的广告物料，请先在「审查中心」完成审查与审批")

    submitted_at = datetime.now()
    batch_id = f"DLV-{submitted_at.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"

    items: list[dict] = []
    success_ids: list[str] = []
    for ad in selected:
        # 幂等：已投过且有平台计划 ID → 跳过，不再真实提交
        if ad.get("delivery_platform") == platform and ad.get("platform_plan_id"):
            items.append({
                "ad_id": ad["id"], "ad_type": ad.get("type", ""), "title": ad.get("title", ""),
                "status": "skipped", "ad_promotion_id": ad["platform_plan_id"],
                "reason": "该物料已在此平台投放，跳过以避免重复建计划",
            })
            continue

        qc_payload = _build_qianchuan_payload(ad, body, budget)
        try:
            result = await douyin_delivery_agent.deliver(qc_payload)
        except Exception as exc:  # noqa: BLE001
            items.append({
                "ad_id": ad["id"], "ad_type": ad.get("type", ""), "title": ad.get("title", ""),
                "status": "failed", "ad_promotion_id": None,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            continue

        if not result.get("submitted"):
            pre = result.get("preflight") or {}
            detail = "；".join(
                f"{c['name']}:{c['detail']}" for c in (pre.get("checks") or []) if not c["ok"]
            ) or result.get("reason", "预检未通过")
            items.append({
                "ad_id": ad["id"], "ad_type": ad.get("type", ""), "title": ad.get("title", ""),
                "status": "failed", "ad_promotion_id": None,
                "reason": f"预检未通过（{detail}）",
            })
            continue

        plan_id = result.get("ad_promotion_id") or ""
        items.append({
            "ad_id": ad["id"], "ad_type": ad.get("type", ""), "title": ad.get("title", ""),
            "status": "success",
            "ad_promotion_id": plan_id,
            "mode": result.get("mode"),
            # 不编触达数：真实触达要么来自平台回报，要么就是没有
            "reach": None,
            "reason": None,
        })
        success_ids.append(ad["id"])
        if plan_id:
            ad_store.update_ad(
                ad["id"],
                platform_plan_id=plan_id,
                delivery_mode=result.get("mode"),
                delivered_at=submitted_at.isoformat(timespec="seconds"),
            )

    if success_ids:
        ad_store.mark_delivered(success_ids, platform=platform)

    record = {
        "batch_id": batch_id,
        "platform": platform,
        "platform_label": meta["label"],
        "platform_belong": meta["belong"],
        "platform_category": meta["category"],
        "mode": douyin_delivery_agent.capability().get("mode"),
        "status": "已投放" if success_ids else ("已跳过" if items and all(
            i["status"] == "skipped" for i in items) else "投放失败"),
        "submitted_at": submitted_at.isoformat(timespec="seconds"),
        "budget": budget,
        "budget_verdict": verdict,
        "budget_verdict_msg": verdict_msg,
        "items": items,
        "success_count": len(success_ids),
        "failed_count": sum(1 for i in items if i["status"] == "failed"),
        "skipped_count": sum(1 for i in items if i["status"] == "skipped"),
    }
    with _LOCK:
        _DELIVERIES.insert(0, record)
        _persist_deliveries()
    return ok(record)


@router.post("/stop")
async def stop_delivery(payload: dict | None = None):
    """关停某个物料已投放的计划。

    真实投放的安全网：投错了要能立刻停，否则钱一直烧。
    """
    body = payload or {}
    ad_id = str(body.get("ad_id") or "").strip()
    if not ad_id:
        return fail(400, "请提供 ad_id")
    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return fail(404, "广告物料不存在")
    plan_id = str(ad.get("platform_plan_id") or "").strip()
    if not plan_id:
        return fail(400, "该物料没有平台计划 ID，无法关停（可能从未投放）")

    try:
        result = await douyin_delivery_agent.stop(plan_id)
    except Exception as exc:  # noqa: BLE001
        return fail(502, f"关停失败：{type(exc).__name__}: {exc}")

    ad_store.update_ad(ad_id, delivery_status="stopped")
    return ok({
        "ad_id": ad_id,
        "ad_promotion_id": plan_id,
        "stopped": True,
        "mode": douyin_delivery_agent.capability().get("mode"),
        "result": result,
    })
