"""抖音 / 巨量千川 广告投递 Agent。

职责：把「已生成的广告物料 + 投放参数」翻译成千川创编接口所需请求，并在提交前做预检。

数据来源：mcp_servers/douyin/（独立 MCP 工具包，与拼多多等平台分目录隔离）。
运行模式：QIANCHUAN_DELIVERY_MODE=mock（默认，仅预检 + 模拟结果）/ real（真实调用创编接口）。

投递所需参数（同一份事实来源见 mcp_servers/douyin/tools.py 的 DELIVERY_REQUIREMENTS）：
    advertiser_id 广告主账户、shop_id 店铺、product_id 商品、aweme_id 抖音号、
    video_id/image_id 素材、marketing_goal 营销目标、budget 预算、bid/roi_goal 出价等。
"""
from __future__ import annotations

import sys
from pathlib import Path

# backend/app/agent/douyin_delivery_agent.py → parents[3] = 仓库根目录
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp_servers.douyin import tools as qc_tools
from mcp_servers.douyin.config import qianchuan_config


def requirements() -> list[dict]:
    """投递所需字段说明（供 API / 前端展示）。"""
    return qc_tools.delivery_requirements()


async def account_overview() -> dict:
    """账户概览（广告主信息 / 余额 / 钱包）。"""
    return await qc_tools.qianchuan_account_overview()


async def preflight(payload: dict) -> dict:
    """投递预检：校验必填项与商品 / 抖音号 / 素材是否可投。"""
    return await qc_tools.qianchuan_delivery_preflight(
        shop_id=payload.get("shop_id", ""),
        product_id=payload.get("product_id", ""),
        aweme_id=payload.get("aweme_id", ""),
        video_id=payload.get("video_id", ""),
        image_id=payload.get("image_id", ""),
    )


async def deliver(payload: dict | None = None) -> dict:
    """执行投放：先预检，通过后提交创编接口（mock / real）。"""
    payload = dict(payload or {})
    check = await preflight(payload)
    if not check.get("ready"):
        return {"submitted": False, "mode": qianchuan_config()["mode"], "reason": "预检未通过", "preflight": check}
    result = await qc_tools.qianchuan_submit_delivery(payload)
    return {"submitted": True, "mode": qianchuan_config()["mode"], "preflight": check, "result": result}


__all__ = ["requirements", "account_overview", "preflight", "deliver"]