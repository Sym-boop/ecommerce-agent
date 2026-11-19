"""抖音 / 巨量千川 广告投递 API（对接后端投递 Agent）。

    GET  /api/v1/qianchuan/requirements   → 投递所需字段说明
    GET  /api/v1/qianchuan/capability     → 该 Agent 的能力与「还缺什么」
    GET  /api/v1/qianchuan/account        → 账户概览（广告主 / 余额 / 钱包）
    POST /api/v1/qianchuan/preflight      → 投递预检
    POST /api/v1/qianchuan/deliver        → 执行投递（预检 + 提交，默认 mock）
    POST /api/v1/qianchuan/stop           → 关停计划（真实投放的安全网）

OAuth 授权（首次拿 token 的唯一入口）：
    GET  /api/v1/qianchuan/oauth/url      → 生成授权页地址（需 app_id）
    GET  /api/v1/qianchuan/oauth/callback → 授权码换 token 并落盘
    GET  /api/v1/qianchuan/token/status   → 授权态（脱敏，不返回明文）
    POST /api/v1/qianchuan/token/refresh  → 手动续期
    DELETE /api/v1/qianchuan/token        → 清除授权

MCP 工具与投递 Agent 实现见 mcp_servers/douyin/ 与 app/agent/douyin_delivery_agent.py。
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter

from app.agent import douyin_delivery_agent as agent

# 复用 MCP 侧的 token 存储与客户端（同一份文件，避免两套实现）
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp_servers.douyin import token_store  # noqa: E402
from mcp_servers.douyin.client import QianchuanAPIError, QianchuanClient  # noqa: E402
from mcp_servers.douyin.config import qianchuan_config  # noqa: E402

router = APIRouter(prefix="/api/v1/qianchuan", tags=["qianchuan"])


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str, data=None) -> dict:
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"code": status, "msg": msg, "data": data})


@router.get("/requirements")
async def requirements_api():
    """投递所需参数说明（店铺 ID / 商品 ID / 抖音号 / 素材等）。"""
    return ok(agent.requirements())


@router.get("/capability")
async def capability_api():
    """该 Agent 的能力状态：模式、是否已授权、真实投放还缺什么。"""
    return ok(agent.capability())


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


@router.post("/stop")
async def stop_api(payload: dict | None = None):
    """关停投放计划：{ ad_promotion_id }。"""
    body = payload or {}
    plan_id = str(body.get("ad_promotion_id") or "").strip()
    if not plan_id:
        return fail(400, "请提供 ad_promotion_id")
    try:
        return ok(await agent.stop(plan_id))
    except Exception as exc:  # noqa: BLE001
        return fail(502, f"关停失败：{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# OAuth 授权
# ---------------------------------------------------------------------------
@router.get("/oauth/url")
async def oauth_url_api():
    """生成授权页地址。

    原来没有这一步：token 只能手工粘进 `.env`，等于把 OAuth 流程跳过了。
    真实链路必须先跳这个地址，用户同意后回调带回 auth_code。
    """
    cfg = qianchuan_config()
    if not cfg["app_id"]:
        return fail(400, "未配置 QIANCHUAN_APP_ID（需先在巨量引擎开放平台完成应用审核）")
    if not cfg["redirect_uri"]:
        return fail(400, "未配置 QIANCHUAN_REDIRECT_URI（OAuth 回调地址，需与开放平台登记一致）")
    query = urlencode({
        "app_id": cfg["app_id"],
        "redirect_uri": cfg["redirect_uri"],
        "state": "qianchuan",
        "material_auth": "1",
    })
    return ok({
        "authorize_url": f"{cfg['oauth_authorize_url']}?{query}",
        "redirect_uri": cfg["redirect_uri"],
    })


@router.get("/oauth/callback")
async def oauth_callback_api(auth_code: str = "", code: str = ""):
    """OAuth 回调：用授权码换 token 并落盘。

    兼收 `auth_code` 与 `code` 两个参数名——不同文档版本写法不一致，多一个别名少一次联调。
    """
    ac = (auth_code or code or "").strip()
    if not ac:
        return fail(400, "缺少 auth_code")
    try:
        doc = QianchuanClient().exchange_auth_code(ac)
    except QianchuanAPIError as exc:
        return fail(502, f"换 token 失败：{exc}")
    return ok({
        "authorized": True,
        "expires_in": doc.get("expires_in"),
        "advertiser_id": token_store.status().get("advertiser_id", ""),
    })


@router.get("/token/status")
async def token_status_api():
    """授权态（脱敏）：是否已授权、剩余有效期、是否需要续期。"""
    return ok(token_store.status())


@router.post("/token/refresh")
async def token_refresh_api():
    """手动续期（定时任务也可调用）。"""
    try:
        doc = QianchuanClient().refresh_access_token()
    except QianchuanAPIError as exc:
        return fail(502, f"续期失败：{exc}")
    return ok({"refreshed": True, "expires_in": doc.get("expires_in")})


@router.delete("/token")
async def token_clear_api():
    """清除本地授权态。"""
    token_store.clear()
    return ok({"cleared": True})
