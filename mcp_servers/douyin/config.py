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


def is_real(value: str) -> bool:
    """占位符（空串、*** 等）一律视为未配置。"""
    value = (value or "").strip()
    return bool(value) and set(value) != {"*"}


def qianchuan_config() -> dict:
    """读取抖音 / 巨量千川配置（每次调用读取，支持热更新 .env）。"""
    return {
        "app_id": os.getenv("QIANCHUAN_APP_ID", "").strip(),
        "app_secret": os.getenv("QIANCHUAN_APP_SECRET", "").strip(),
        "access_token": os.getenv("QIANCHUAN_ACCESS_TOKEN", "").strip(),
        "refresh_token": os.getenv("QIANCHUAN_REFRESH_TOKEN", "").strip(),
        "advertiser_id": os.getenv("QIANCHUAN_ADVERTISER_ID", "").strip(),
        "base_url": os.getenv("QIANCHUAN_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "mode": os.getenv("QIANCHUAN_DELIVERY_MODE", "mock").strip().lower(),
        "create_plan_path": os.getenv("QIANCHUAN_CREATE_PLAN_PATH", DEFAULT_CREATE_PLAN_PATH).strip(),
        "timeout": float(os.getenv("QIANCHUAN_TIMEOUT", "30")),
    }


def is_configured() -> bool:
    """是否已配置可用于真实调用的 access_token。"""
    return is_real(qianchuan_config()["access_token"])


def has_credentials() -> bool:
    """是否已配置 app_id / app_secret（可用于刷新 token）。"""
    cfg = qianchuan_config()
    return is_real(cfg["app_id"]) and is_real(cfg["app_secret"])