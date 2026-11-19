"""千川 access_token 的持久化与自动续期。

**为什么需要它**

千川走 OAuth2：`access_token` 有效期通常一天左右，用 `refresh_token` 续期，
续期会**同时下发新的 refresh_token**（旧的作废）。

原来的实现有三个问题：

1. `refresh_access_token()` 把新 token 返回给调用方，**但没有任何地方接住并保存** ——
   相当于续了个寂寞，下次仍然读 `.env` 里的旧值，然后鉴权失败。
2. token 只能手工填进 `.env`，改完要重启进程（`load_dotenv(override=False)` 不覆盖已加载的）。
3. 没有「快过期了提前续」的能力，只能等报错再人工处理。

这里把 token 落到 `data/qianchuan_token.json`：读的时候 env 优先、文件兜底；
续期后写回文件；调用前检查剩余有效期，快过期就先续。

**为什么不用数据库**：这张表只有一行（单广告主的授权态），JSON 足够，
且便于出问题时直接打开看。多广告主时应换成表，按 advertiser_id 分区。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# backend/app/../.. → mcp_servers/douyin/token_store.py 在仓库内，直接用相对根目录
_ROOT = Path(__file__).resolve().parents[2]
_LOCK = threading.Lock()


def _token_file() -> Path:
    """token 落盘路径（每次读环境变量，不缓存在模块常量里）。

    为什么做成函数：一开始写成模块级常量，结果测试在 import 之后才设
    `QIANCHUAN_TOKEN_FILE`，常量早已固化 —— 测试数据直接写进了仓库真实的
    `data/qianchuan_token.json`。这类「常量在导入时求值」的坑在配置类里很常见，
    凡是可能被测试或运行期覆盖的路径，都应该每次现取。
    """
    override = os.getenv("QIANCHUAN_TOKEN_FILE")
    if override:
        return Path(override)
    return _ROOT / "data" / "qianchuan_token.json"


# 剩余有效期小于该秒数就提前续期，避免「刚好在请求途中等过期」
REFRESH_MARGIN_SECONDS = int(os.getenv("QIANCHUAN_REFRESH_MARGIN", "600"))


def _read() -> dict[str, Any]:
    path = _token_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取千川 token 文件失败（%s），按未授权处理", exc)
        return {}


def _write(doc: dict[str, Any]) -> None:
    path = _token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load() -> dict[str, Any]:
    """当前保存的授权态（可能为空）。"""
    with _LOCK:
        return _read()


def save(access_token: str, refresh_token: str = "", expires_in: float | None = None,
         advertiser_id: str = "", source: str = "oauth") -> dict[str, Any]:
    """保存授权态。`expires_in` 为服务端返回的有效秒数，换算成绝对时间存。"""
    now = time.time()
    doc = {
        "access_token": (access_token or "").strip(),
        "refresh_token": (refresh_token or "").strip() or load().get("refresh_token", ""),
        "advertiser_id": (advertiser_id or "").strip() or load().get("advertiser_id", ""),
        "expires_at": (now + float(expires_in)) if expires_in else None,
        "saved_at": now,
        "source": source,
    }
    with _LOCK:
        _write(doc)
    logger.info("千川 token 已保存（来源 %s，有效期 %s 秒）", source, expires_in)
    return doc


def clear() -> None:
    with _LOCK:
        path = _token_file()
        if path.exists():
            path.unlink()
    logger.info("千川 token 已清除")


def remaining_seconds() -> float | None:
    """距过期还有多少秒；没有记录有效期时返回 None（视为未知，不主动续）。"""
    doc = load()
    expires_at = doc.get("expires_at")
    if not expires_at:
        return None
    return float(expires_at) - time.time()


def needs_refresh() -> bool:
    """是否该续期：有 refresh_token，且剩余有效期已低于安全余量。"""
    doc = load()
    if not doc.get("refresh_token"):
        return False
    left = remaining_seconds()
    if left is None:
        return False
    return left <= REFRESH_MARGIN_SECONDS


def status() -> dict[str, Any]:
    """给 /token/status 用的脱敏视图（不返回 token 明文）。"""
    doc = load()
    left = remaining_seconds()
    return {
        "authorized": bool(doc.get("access_token")),
        "has_refresh_token": bool(doc.get("refresh_token")),
        "advertiser_id": doc.get("advertiser_id", ""),
        "expires_in_seconds": round(left, 1) if left is not None else None,
        "needs_refresh": needs_refresh(),
        "saved_at": doc.get("saved_at"),
        "source": doc.get("source", ""),
    }
