"""店铺信息仓库（按平台区分的 JSON 文件存储）。

目录：<仓库根>/data/stores/<platform>.json
每个文件承载一个平台下的店铺列表，供「广告投放」界面展示 / 添加 / 删除。

文件结构：
    {
      "platform": "qianchuan",
      "platform_label": "巨量千川（抖音）",
      "updated_at": "2026-10-13T21:50:00",
      "stores": [
        {
          "id": "ST-20261013-a1b2c3",
          "name": "抖音小店 312014721",
          "shop_id": "312014721",
          "douyin_account": "cisheng88193",
          "aweme_id": "cisheng88193",
          "advertiser_id": "",
          "note": "",
          "products": [{"product_id": "3848012001225867368", "name": "主推商品"}],
          "created_at": "2026-10-13T21:50:00"
        }
      ]
    }

注意：店铺信息为跨重启持久化的本地文件，与投递 MCP 工具（mcp_servers/douyin/）共享同一份数据。
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

# backend/app/common/store_store.py → parents[3] = 仓库根目录
_ROOT = Path(__file__).resolve().parents[3]
_STORES_DIR = _ROOT / "data" / "stores"
# 平台 key → 展示名（与 app/api/delivery_router.py 的 DELIVERY_PLATFORMS 保持一致）
PLATFORM_LABELS = {
    "qianchuan": "巨量千川（抖音）",
    "juguang": "聚光（小红书）",
    "alimama": "阿里妈妈（淘宝/天猫）",
    "duoduo": "多多进宝（拼多多）",
}

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def platform_file(platform: str) -> Path:
    """某平台对应的店铺信息文件路径。"""
    return _STORES_DIR / f"{(platform or '').strip()}.json"


def _empty_doc(platform: str) -> dict:
    return {
        "platform": platform,
        "platform_label": PLATFORM_LABELS.get(platform, platform),
        "updated_at": _now(),
        "stores": [],
    }


def _read(platform: str) -> dict:
    path = platform_file(platform)
    if not path.exists():
        return _empty_doc(platform)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_doc(platform)
    if not isinstance(doc, dict):
        return _empty_doc(platform)
    doc.setdefault("platform", platform)
    doc.setdefault("platform_label", PLATFORM_LABELS.get(platform, platform))
    if not isinstance(doc.get("stores"), list):
        doc["stores"] = []
    return doc


def _write(platform: str, doc: dict) -> None:
    _STORES_DIR.mkdir(parents=True, exist_ok=True)
    doc["updated_at"] = _now()
    platform_file(platform).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_stores(platform: str) -> list[dict]:
    """某平台下的全部店铺。"""
    with _LOCK:
        return list(_read(platform).get("stores", []))


def get_store(platform: str, store_id: str) -> dict | None:
    """按内部 id 取回单条店铺。"""
    with _LOCK:
        return next(
            (s for s in _read(platform).get("stores", []) if s.get("id") == store_id),
            None,
        )


def add_store(platform: str, payload: dict) -> dict:
    """新增店铺并落盘。"""
    payload = dict(payload or {})
    with _LOCK:
        doc = _read(platform)
        created = _now()
        store = {
            "id": f"ST-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
            "name": (str(payload.get("name") or "").strip()
                     or f"{PLATFORM_LABELS.get(platform, platform)}店铺"),
            "shop_id": str(payload.get("shop_id") or "").strip(),
            "douyin_account": str(payload.get("douyin_account") or "").strip(),
            "aweme_id": str(payload.get("aweme_id") or payload.get("douyin_account") or "").strip(),
            "advertiser_id": str(payload.get("advertiser_id") or "").strip(),
            "note": str(payload.get("note") or "").strip(),
            "products": _normalize_products(payload.get("products")),
            "created_at": created,
        }
        doc["stores"].append(store)
        _write(platform, doc)
        return store


def delete_store(platform: str, store_id: str) -> bool:
    """删除指定店铺，返回是否删除成功。"""
    with _LOCK:
        doc = _read(platform)
        before = len(doc.get("stores", []))
        doc["stores"] = [s for s in doc["stores"] if s.get("id") != store_id]
        if len(doc["stores"]) == before:
            return False
        _write(platform, doc)
        return True


def _normalize_products(raw) -> list[dict]:
    """把前端传入的商品（列表 / 逗号分隔字符串）统一成 [{product_id, name}]。"""
    products: list[dict] = []
    if isinstance(raw, str):
        raw = [p for p in raw.replace("，", ",").split(",") if p.strip()]
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                pid = str(item.get("product_id") or "").strip()
                if pid:
                    products.append({"product_id": pid, "name": str(item.get("name") or "").strip()})
            elif isinstance(item, str) and item.strip():
                products.append({"product_id": item.strip(), "name": ""})
    return products