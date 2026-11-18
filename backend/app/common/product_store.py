"""商品目录（选品 / 自定义商品）。

「广告生成」页第一步让用户选择商品：既可从内置商品清单中挑选，也可自定义商品。
本模块以 `data/raw/our_products.csv` 作为初始数据源，并支持把自定义商品落盘到
`data/products.json`（进程重启后仍可见）。

对外函数：
    list_products()                       列出全部商品（内置 + 自定义）
    get_product(product_id)               按 id 取回商品
    add_product(name, category, ...)      新增自定义商品（内存 + 落盘）
"""
from __future__ import annotations

import csv
import threading
import uuid
from pathlib import Path

from app.common import json_store

_ROOT = Path(__file__).resolve().parents[3]
_CSV_PATH = _ROOT / "data" / "raw" / "our_products.csv"
# 数据文件名（相对 data/）：读写统一走 json_store，以复用存储后端与测试隔离
_STORE_FILE = "products.json"

_LOCK = threading.Lock()
_LOADED = False
_PRODUCTS: list[dict] = []


def _slug(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum() or ch in "-_")[:24]


def _load_catalog() -> list[dict]:
    """从 our_products.csv 读取内置商品清单。"""
    if not _CSV_PATH.exists():
        return []
    items: list[dict] = []
    with _CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            sku = (row.get("sku") or "").strip()
            category = (row.get("category") or "通用").strip() or "通用"
            price = (row.get("price") or "").strip()
            items.append({
                "id": sku or f"P-{_slug(name)}",
                "sku": sku,
                "name": name,
                "category": category,
                "price": float(price) if price else None,
                "sell_points": [category],
                "source": "catalog",
            })
    return items


def _load_custom() -> list[dict]:
    """读取用户自定义商品（落盘文件不存在时返回空）。"""
    raw = json_store.read_json(_STORE_FILE, [])
    items: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get("name"):
            items.append({
                "id": item.get("id") or f"P-{_slug(item['name'])}",
                "sku": item.get("sku", ""),
                "name": item["name"],
                "category": item.get("category") or "通用",
                "price": item.get("price"),
                "sell_points": item.get("sell_points") or [],
                "source": "custom",
            })
    return items


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    with _LOCK:
        if _LOADED:
            return
        _PRODUCTS.extend(_load_catalog())
        # 自定义商品放前面，便于用户快速找到刚新增的商品
        _PRODUCTS[:0] = _load_custom()
        _LOADED = True


def _flush_custom() -> None:
    """把自定义商品写回 data/products.json（经 json_store，支持存储后端与测试隔离）。"""
    custom = [p for p in _PRODUCTS if p.get("source") == "custom"]
    json_store.write_json(_STORE_FILE, custom)


def list_products() -> list[dict]:
    """返回全部商品（自定义在前，内置在后）。"""
    _ensure_loaded()
    with _LOCK:
        return list(_PRODUCTS)


def get_product(product_id: str) -> dict | None:
    _ensure_loaded()
    with _LOCK:
        return next((p for p in _PRODUCTS if p["id"] == product_id), None)


def add_product(name: str, category: str = "通用",
                sell_points: list[str] | None = None,
                price: float | None = None) -> dict:
    """新增自定义商品（重名则返回已存在的商品，避免重复）。"""
    _ensure_loaded()
    name = (name or "").strip()
    if not name:
        raise ValueError("商品名称不能为空")
    with _LOCK:
        existing = next((p for p in _PRODUCTS if p["name"] == name), None)
        if existing is not None:
            return existing
        record = {
            "id": f"CUS-{uuid.uuid4().hex[:8]}",
            "sku": "",
            "name": name,
            "category": (category or "通用").strip() or "通用",
            "price": price,
            "sell_points": [s for s in (sell_points or []) if s],
            "source": "custom",
        }
        _PRODUCTS.insert(0, record)
        _flush_custom()
        return record