"""抖音 · 巨量千川 平台配置读取。

所有密钥统一放在项目根目录 .env，字段与注释见 .env.example 的
「抖音 · 巨量千川（广告投递 Agent）」段。本模块只读不写，每次调用实时读取（支持热更新）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# mcp_servers/douyin/config.py → parents[2] = 仓库根目录
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env", override=False)
except Exception:  # noqa: BLE001
    pass

DEFAULT_BASE_URL = "https://api.oceanengine.com"
DEFAULT_CREATE_PLAN_PATH = "/open_api/v1.0/qianchuan/ad/create/"
# 查计划详情：用于投放后轮询审核状态
DEFAULT_QUERY_PLAN_PATH = "/open_api/v1.0/qianchuan/ad/get/"
# 关停计划：真实投放的安全网。没有它，投出去的计划停不下来
DEFAULT_STOP_PLAN_PATH = "/open_api/v1.0/qianchuan/ad/update/status/"
# OAuth：授权页与授权码换 token（首次拿 token 的唯一入口）
DEFAULT_OAUTH_AUTHORIZE_URL = "https://open.oceanengine.com/audit/oauth.html"
DEFAULT_OAUTH_TOKEN_PATH = "/open_api/oauth2/access_token/"


def is_real(value: str) -> bool:
    """占位符（空串、*** 等）一律视为未配置。"""
    value = (value or "").strip()
    return bool(value) and set(value) != {"*"}


def qianchuan_config() -> dict:
    """读取抖音 / 巨量千川配置（每次调用读取，支持热更新 .env）。

    token 的取值顺序：**环境变量优先，`data/qianchuan_token.json` 兜底**。
    为什么这样排：`.env` 是人工配置的长期值（比如服务商给的固定 token），
    OAuth 授权拿到的是会过期的动态值。两者并存时以人工配置为准，
    避免自动续期把人家手工填的值悄悄改掉。
    """
    access_token = os.getenv("QIANCHUAN_ACCESS_TOKEN", "").strip()
    refresh_token = os.getenv("QIANCHUAN_REFRESH_TOKEN", "").strip()
    advertiser_id = os.getenv("QIANCHUAN_ADVERTISER_ID", "").strip()
    auth_source = "env" if is_real(access_token) else ""

    if not is_real(access_token):
        # 环境变量没配，尝试 OAuth 授权的持久化结果
        try:
            from . import token_store

            doc = token_store.load()
            if doc:
                access_token = (doc.get("access_token") or "").strip()
                refresh_token = refresh_token or (doc.get("refresh_token") or "").strip()
                advertiser_id = advertiser_id or (doc.get("advertiser_id") or "").strip()
                if is_real(access_token):
                    auth_source = "oauth_file"
        except Exception:  # noqa: BLE001
            # token 文件损坏不应让整个配置读取失败，降级成「未授权」
            pass

    return {
        "app_id": os.getenv("QIANCHUAN_APP_ID", "").strip(),
        "app_secret": os.getenv("QIANCHUAN_APP_SECRET", "").strip(),
        "access_token": access_token,
        "refresh_token": refresh_token,
        "advertiser_id": advertiser_id,
        "auth_source": auth_source,
        "base_url": os.getenv("QIANCHUAN_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "mode": os.getenv("QIANCHUAN_DELIVERY_MODE", "mock").strip().lower(),
        "create_plan_path": os.getenv("QIANCHUAN_CREATE_PLAN_PATH", DEFAULT_CREATE_PLAN_PATH).strip(),
        "query_plan_path": os.getenv("QIANCHUAN_QUERY_PLAN_PATH", DEFAULT_QUERY_PLAN_PATH).strip(),
        "stop_plan_path": os.getenv("QIANCHUAN_STOP_PLAN_PATH", DEFAULT_STOP_PLAN_PATH).strip(),
        "oauth_authorize_url": os.getenv(
            "QIANCHUAN_OAUTH_AUTHORIZE_URL", DEFAULT_OAUTH_AUTHORIZE_URL).strip(),
        "oauth_token_path": os.getenv("QIANCHUAN_OAUTH_TOKEN_PATH", DEFAULT_OAUTH_TOKEN_PATH).strip(),
        "redirect_uri": os.getenv("QIANCHUAN_REDIRECT_URI", "").strip(),
        "timeout": float(os.getenv("QIANCHUAN_TIMEOUT", "30")),
    }


def is_configured() -> bool:
    """是否已配置可用于真实调用的 access_token。"""
    return is_real(qianchuan_config()["access_token"])


def has_credentials() -> bool:
    """是否已配置 app_id / app_secret（可用于刷新 token）。"""
    cfg = qianchuan_config()
    return is_real(cfg["app_id"]) and is_real(cfg["app_secret"])