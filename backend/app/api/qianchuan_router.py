"""抖音 / 巨量千川 广告投递 API（对接后端投递 Agent）。

    GET  /api/v1/qianchuan/requirements → 投递所需字段说明
    GET  /api/v1/qianchuan/account      → 账户概览（广告主 / 余额 / 钱包）
    POST /api/v1/qianchuan/preflight    → 投递预检
    POST /api/v1/qianchuan/deliver      → 执行投递（预检 + 提交，默认 mock）

MCP 工具与投递 Agent 实现见 mcp_servers/douyin/ 与 app/agent/douyin_delivery_agent.py。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.agent import douyin_delivery_agent as agent

router = APIRouter(prefix="/api/v1/qianchuan", tags=["qianchuan"])


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


@router.get("/requirements")
async def requirements_api():
    """投递所需参数说明（店铺 ID / 商品 ID / 抖音号 / 素材等）。"""
    return ok(agent.requirements())


@router.get("/account")
async def account_api():
    """账户概览：广告主信息、账户余额、钱包余额。"""
    return ok(await agent.account_overview())


@router.post("/preflight")
async def preflight_api(payload: dict | None = None):
    """投递预检：不下发投放，仅校验参数与可投清单。"""
    return ok(await agent.preflight(payload or {}))


@router.post("/deliver")
async def deliver_api(payload: dict | None = None):
    """执行投放（先预检，通过后提交创编接口；默认 mock 模式）。"""
    return ok(await agent.deliver(payload or {}))