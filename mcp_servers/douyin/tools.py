"""抖音 · 巨量千川 工具实现（纯函数，供 MCP Server 与后端投递 Agent 共用）。

运行模式（QIANCHUAN_DELIVERY_MODE）：
    mock（默认）不发起真实网络请求，返回结构一致的模拟数据，便于授权前联调全链路；
    real        通过 QianchuanClient 调用巨量引擎开放平台真实接口。

投递链路工具：账户概览 / 投递上下文 / 投递预检 / 提交投放。
"""
from __future__ import annotations

import json
from pathlib import Path

from .catalog import resolve_path
from .client import QianchuanAPIError, QianchuanClient
from .config import qianchuan_config

# mcp_servers/douyin/tools.py → parents[2] = 仓库根目录
_ROOT = Path(__file__).resolve().parents[2]
_STORE_DIR = _ROOT / "data" / "stores"


def _local_stores() -> list[dict]:
    """读取本地店铺信息（<仓库根>/data/stores/qianchuan.json，按平台区分）。

    与后端 app/common/store_store.py 共用同一份文件；MCP Server 需可独立运行，
    故此处直接读取，不依赖后端模块。
    """
    path = _STORE_DIR / "qianchuan.json"
    if not path.exists():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    stores = doc.get("stores") if isinstance(doc, dict) else None
    return stores if isinstance(stores, list) else []

# ---------------------------------------------------------------------------
# 投递所需参数定义（前端展示、Agent 预检、文档说明共用同一份事实来源）
# ---------------------------------------------------------------------------
DELIVERY_REQUIREMENTS: list[dict] = [
    {"field": "advertiser_id", "label": "千川广告主账户 ID", "required": True,
     "source": "oauth2_advertiser_get / QIANCHUAN_ADVERTISER_ID", "note": "投递主体，须已授权当前 access_token"},
    {"field": "shop_id", "label": "抖音店铺 ID", "required": True,
     "source": "qianchuan_shop_advertiser_list_v1", "note": "商家广告主必填；达人 / 机构广告主可空"},
    {"field": "product_id", "label": "小店商品 ID", "required": True,
     "source": "qianchuan_product_available_get_v1", "note": "要投放的商品（抖音小店商品 ID，不是 SKU）"},
    {"field": "aweme_id", "label": "授权抖音号 ID", "required": True,
     "source": "qianchuan_aweme_authorized_get_v1", "note": "短视频投放需短视频带货权限，直播投放需直播带货权限"},
    {"field": "video_id", "label": "视频素材 ID", "required": False,
     "source": "qianchuan_video_get_v1 / qianchuan_file_video_aweme_get_v1", "note": "短视频投放填写（与图文二选一）"},
    {"field": "image_id", "label": "图片 / 图文素材 ID", "required": False,
     "source": "qianchuan_image_get_v1 / qianchuan_carousel_get_v1", "note": "图文投放填写"},
    {"field": "marketing_goal", "label": "营销目标 / 投放场景", "required": True,
     "source": "创编参数", "note": "如 短视频带货 / 直播间 / 商品全域"},
    {"field": "budget", "label": "预算（元）", "required": True,
     "source": "qianchuan_suggest_budget_v1", "note": "日预算或总预算，需不低于平台最低值"},
    {"field": "bid", "label": "出价（元）", "required": False,
     "source": "qianchuan_suggest_bid_v1", "note": "非 ROI 目标时使用，与 roi_goal 二选一"},
    {"field": "roi_goal", "label": "支付 ROI 目标", "required": False,
     "source": "qianchuan_suggest_roi_goal_v1", "note": "ROI 目标出价时使用，与 bid 二选一"},
    {"field": "orientation_package_id", "label": "定向包 ID", "required": False,
     "source": "qianchuan_orientation_package_get_v1", "note": "不填则使用系统智能定向"},
    {"field": "name", "label": "计划名称", "required": True,
     "source": "创编参数", "note": "便于在千川后台识别本次投放"},
]


def delivery_requirements() -> list[dict]:
    """返回「投递所需参数」说明（供 API / 文档展示）。"""
    return [dict(item) for item in DELIVERY_REQUIREMENTS]


def _mock_mode() -> bool:
    return qianchuan_config()["mode"] != "real"


def _mock(tool_or_path: str, params: dict) -> dict:
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "mock": True,
            "tool": tool_or_path,
            "params": params,
            "list": [],
            "message": "mock 模式：未调用真实千川接口；设置 QIANCHUAN_DELIVERY_MODE=real 并配置真实 access_token 后生效",
        },
    }


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
async def qianchuan_api_call(path: str = "", tool: str = "", params: dict | None = None, method: str = "GET") -> dict:
    """通用接口调用：可直接传 path，或传目录中的 tool 名（自动解析 path）。"""
    real_path = path or (resolve_path(tool) if tool else "")
    if not real_path:
        raise ValueError(f"未知工具或缺少 path：tool={tool!r} path={path!r}")
    params = dict(params or {})
    if _mock_mode():
        return _mock(tool or real_path, params)
    return QianchuanClient().call(real_path, params, method=method)


# ---------------------------------------------------------------------------
# 投递链路工具
# ---------------------------------------------------------------------------
async def qianchuan_account_overview() -> dict:
    """账户概览：广告主信息 + 账户余额 + 钱包余额（投递前自查）。"""
    if _mock_mode():
        return {
            "mock": True,
            "advertiser": {"id": "1700000000000000", "name": "演示广告主", "role": "PLATFORM_ROLE_SHOP_ACCOUNT"},
            "balance": {"general_balance": 12000.0, "bid_balance": 8000.0, "brand_balance": 0.0},
            "wallet": {"total_balance": 20000.0, "grant_balance": 2000.0},
        }
    client = QianchuanClient()
    return {
        "advertiser": client.call("/open_api/2/advertiser/info/"),
        "balance": client.call("/open_api/v1.0/qianchuan/account/balance/get/"),
        "wallet": client.call("/open_api/v1.0/qianchuan/finance/wallet/get/"),
    }


async def qianchuan_delivery_context(product_id: str = "", shop_id: str = "") -> dict:
    """投递上下文：可投商品 / 可投抖音号 / 可用素材，用于组装投递参数。

    mock 模式下，商品与抖音号取自本地店铺信息文件夹（data/stores/qianchuan.json），
    使「模拟投递」也能基于真实登记数据得出结论。
    """
    if _mock_mode():
        stores = _local_stores()
        products = [dict(p) for s in stores for p in s.get("products", [])]
        awemes = [
            {"aweme_id": s.get("aweme_id") or s.get("douyin_account"),
             "aweme_name": s.get("douyin_account") or s.get("name")}
            for s in stores
            if (s.get("aweme_id") or s.get("douyin_account"))
        ]
        return {
            "mock": True,
            "products": products,
            "awemes": awemes,
            "videos": [{"video_id": "v_demo_001", "name": "演示视频素材"}],
            "carousels": [{"carousel_id": "c_demo_001", "name": "演示图文素材"}],
        }
    client = QianchuanClient()
    ctx: dict = {}
    ctx["products"] = client.call(
        "/open_api/v1.0/qianchuan/product/available/get/",
        {"shop_id": shop_id} if shop_id else {},
    )
    ctx["awemes"] = client.call("/open_api/v1.0/qianchuan/aweme/authorized/get/")
    ctx["videos"] = client.call(
        "/open_api/v1.0/qianchuan/video/get/",
        {"product_id": product_id} if product_id else {},
    )
    ctx["carousels"] = client.call(
        "/open_api/v1.0/qianchuan/carousel/aweme/get/",
        {"product_id": product_id} if product_id else {},
    )
    return ctx


def _collect_ids(node) -> set:
    """递归收集响应里所有 *_id 值，用于校验商品 / 抖音号 / 素材是否在可投清单内。"""
    found: set = set()

    def walk(item):
        if isinstance(item, dict):
            for key, value in item.items():
                if (key == "id" or key.endswith("_id")) and isinstance(value, (str, int)):
                    found.add(str(value))
                walk(value)
        elif isinstance(item, list):
            for element in item:
                walk(element)

    walk(node)
    return found


async def qianchuan_delivery_preflight(
    shop_id: str = "",
    product_id: str = "",
    aweme_id: str = "",
    video_id: str = "",
    image_id: str = "",
) -> dict:
    """投递预检：校验必填项，并确认店铺 / 商品 / 抖音号 / 素材是否可投，返回 ready 布尔与明细。

    mock 模式：基于本地店铺信息文件夹（data/stores/qianchuan.json）校验店铺 / 商品 / 抖音号，
    未提供投放素材时使用平台模拟素材，从而给出「是否可投递」的模拟结论。
    real 模式：调用千川接口拉取可投清单后逐项比对。
    """
    checks: list[dict] = []

    def add(ok: bool, name: str, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if _mock_mode():
        stores = _local_stores()
        if not stores:
            add(False, "本地店铺信息", "data/stores/qianchuan.json 暂无店铺，请先在投放界面「添加店铺」")
            return {"ready": False, "mode": "mock", "checks": checks, "store": None}

        store = next(
            (s for s in stores if str(s.get("shop_id") or "") == str(shop_id)), None
        ) if shop_id else None
        add(store is not None, "店铺已登记（本地）", f"shop_id={shop_id or '缺少 shop_id'}")

        if store is not None:
            product_ids = {str(p.get("product_id") or "") for p in store.get("products", [])}
            add(bool(product_id) and str(product_id) in product_ids,
                "商品属于该店铺（本地）", f"product_id={product_id or '缺少 product_id'}")
            accounts = {str(store.get("aweme_id") or ""), str(store.get("douyin_account") or "")}
            accounts.discard("")
            add(bool(aweme_id) and str(aweme_id) in accounts,
                "抖音号与该店铺绑定（本地）", f"aweme_id={aweme_id or '缺少 aweme_id'}")

        material = video_id or image_id
        add(True, "投放素材", material or "mock 模式：未提供素材，将使用平台模拟素材")
        return {
            "ready": all(item["ok"] for item in checks),
            "mode": "mock",
            "checks": checks,
            "store": ({"id": store.get("id"), "name": store.get("name"), "shop_id": store.get("shop_id")}
                      if store else None),
        }

    add(bool(product_id), "已选择商品 ID", product_id or "缺少 product_id")
    add(bool(aweme_id), "已选择投放抖音号", aweme_id or "缺少 aweme_id")
    add(bool(video_id or image_id), "已选择图文 / 视频素材",
        video_id or image_id or "video_id 与 image_id 至少填一个")

    context = await qianchuan_delivery_context(product_id=product_id, shop_id=shop_id)
    known = _collect_ids(context)
    add(str(product_id) in known, "商品在可投清单内", f"product_id={product_id}")
    add(str(aweme_id) in known, "抖音号已授权可投", f"aweme_id={aweme_id}")
    material = str(video_id or image_id)
    add(material in known, "素材在素材库内", f"material_id={material}")
    return {"ready": all(item["ok"] for item in checks), "mode": "real", "checks": checks}


async def qianchuan_submit_delivery(payload: dict | None = None) -> dict:
    """提交投放：mock 模式返回模拟计划；real 模式调用创编（create_plan）接口。

    payload 为创编请求体，字段见 delivery_requirements()（advertiser_id 等由客户端自动注入）。
    """
    payload = dict(payload or {})
    if _mock_mode():
        return {
            "mock": True,
            "ad_promotion_id": "QCP-DEMO-0001",
            "status": "SUBMITTED",
            "message": "mock 模式：已生成模拟投放计划，未真实提交千川创编接口",
            "payload": payload,
        }
    cfg = qianchuan_config()
    return QianchuanClient().call(cfg["create_plan_path"], payload, method="POST")


__all__ = [
    "QianchuanAPIError",
    "DELIVERY_REQUIREMENTS",
    "delivery_requirements",
    "qianchuan_api_call",
    "qianchuan_account_overview",
    "qianchuan_delivery_context",
    "qianchuan_delivery_preflight",
    "qianchuan_submit_delivery",
]