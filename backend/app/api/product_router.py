"""商品目录 API（广告生成页「选择商品 / 自定义商品」）。

    GET  /api/v1/products   商品列表（内置清单 + 自定义商品）
    POST /api/v1/products   JSON: { name, category?, sell_points?, price? } 新增自定义商品
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.common import product_store

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


@router.get("")
async def list_products_api():
    return ok(product_store.list_products())


@router.post("")
async def create_product_api(payload: dict | None = None):
    body = payload or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return fail(400, "请填写商品名称")

    raw_points = body.get("sell_points")
    if isinstance(raw_points, list):
        sell_points = [str(s).strip() for s in raw_points if str(s).strip()]
    elif isinstance(raw_points, str):
        sell_points = [s.strip() for s in raw_points.replace("，", ",").split(",") if s.strip()]
    else:
        sell_points = []

    raw_price = body.get("price")
    try:
        price = float(raw_price) if raw_price not in (None, "") else None
    except (TypeError, ValueError):
        price = None

    product = product_store.add_product(
        name=name,
        category=str(body.get("category") or "通用").strip() or "通用",
        sell_points=sell_points,
        price=price,
    )
    return ok(product)