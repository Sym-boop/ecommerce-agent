"""抖音 / 巨量千川 广告投递 Agent。

职责：把「已生成的广告物料 + 投放参数」翻译成千川创编接口所需请求，并在提交前做预检。

数据来源：mcp_servers/douyin/（独立 MCP 工具包，与拼多多等平台分目录隔离）。
运行模式：QIANCHUAN_DELIVERY_MODE=mock（默认，仅预检 + 模拟结果）/ real（真实调用创编接口）。

**当前能力边界（2026-11）**

已实现且可验证：
    - 预检 preflight()：mock 校验本地店铺；real 调平台可投清单
    - 提交 deliver()：real 调 create_plan
    - 查询 query()：查计划状态（审核中 / 投放中 / 已关停）
    - 关停 stop()：**真实投放的安全网**，没有它投出去就停不下来
    - OAuth 授权码换 token、token 持久化与自动续期（见 client.py / token_store.py）

**尚未完成（需要真实广告主账号才能做，不能靠读文档猜）**：
    - 素材上传：本地海报/视频 → 千川素材库 → 拿到 image_id / video_id
    - 字段映射：扁平业务字段 → 千川 `ad/create` 的嵌套结构
      （该接口一次同时创建「计划 + 定向 + 创意」，专业版/极速版传参不同）
    这两项不完成，切 real 会在平台侧报参数错误——**这是已知边界，不是遗漏**。

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
from mcp_servers.douyin.config import is_real, qianchuan_config


def requirements() -> list[dict]:
    """投递所需字段说明（供 API / 前端展示）。"""
    return qc_tools.delivery_requirements()


def capability() -> dict:
    """该平台 Agent 的能力与配置状态（供前端决定按钮可用性）。

    注意用 `is_real()` 而不是 `bool()`：`.env.example` 里 token 是 `***` 占位符，
    `bool("***")` 是 True，会把「未配置」误报成「已授权」——这个坑踩过一次。
    """
    cfg = qianchuan_config()
    authorized = is_real(cfg["access_token"])
    return {
        "agent": "douyin_qianchuan",
        "capability": "real_ready",          # 有真实代码路径，缺的是授权
        "mode": cfg["mode"],                 # mock / real
        "authorized": authorized,
        "auth_source": cfg.get("auth_source", "") if authorized else "",
        "app_configured": is_real(cfg["app_id"]) and is_real(cfg["app_secret"]),
        # 真实投放还差的两项（前置依赖，不是开关）
        "blockers": _blockers(cfg, authorized),
    }


def _blockers(cfg: dict, authorized: bool) -> list[str]:
    """列出真实投放还缺什么——让前端和面试都能一眼说清「差在哪」。"""
    missing: list[str] = []
    if not (is_real(cfg["app_id"]) and is_real(cfg["app_secret"])):
        missing.append("未配置开放平台 app_id / app_secret（需企业资质 + 应用审核）")
    if not authorized:
        missing.append("未完成广告主 OAuth 授权（无 access_token）")
    missing.append("素材上传未实现：本地海报/视频需先上传到千川素材库换取 image_id / video_id")
    missing.append("字段映射未完成：ad/create 需嵌套结构（计划+定向+创意），需真实账号联调")
    return missing


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
        return {
            "submitted": False,
            "mode": qianchuan_config()["mode"],
            "reason": "预检未通过",
            "preflight": check,
        }
    result = await qc_tools.qianchuan_submit_delivery(payload)
    return {
        "submitted": True,
        "mode": qianchuan_config()["mode"],
        "preflight": check,
        "result": result,
        "ad_promotion_id": result.get("ad_promotion_id") or "",
    }


async def query(ad_promotion_id: str) -> dict:
    """查询计划状态（投放后轮询用）。"""
    return await qc_tools.qianchuan_query_delivery(ad_promotion_id)


async def stop(ad_promotion_id: str) -> dict:
    """关停计划（真实投放的安全网）。"""
    return await qc_tools.qianchuan_stop_delivery(ad_promotion_id)


__all__ = [
    "requirements",
    "capability",
    "account_overview",
    "preflight",
    "deliver",
    "query",
    "stop",
]
