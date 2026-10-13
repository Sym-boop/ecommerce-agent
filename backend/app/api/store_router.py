"""店铺信息 API（按平台区分，落盘到 <仓库根>/data/stores/<platform>.json）。

端点：
    GET    /api/v1/stores/{platform}            该平台下的全部店铺
    POST   /api/v1/stores/{platform}            新增店铺（shop_id / 抖音号 / 商品）
    DELETE /api/v1/stores/{platform}/{store_id} 删除店铺

platform 取值与投放平台一致：qianchuan / juguang / alimama / duoduo。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.common import store_store

router = APIRouter(prefix="/api/v1/stores", tags=["stores"])


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


@router.get("/{platform}")
async def list_platform_stores(platform: str):
    """某平台下的全部店铺。"""
    return ok({
        "platform": platform,
        "platform_label": store_store.PLATFORM_LABELS.get(platform, platform),
        "stores": store_store.list_stores(platform),
    })


@router.post("/{platform}")
async def create_store(platform: str, payload: dict | None = None):
    """新增店铺：{ name?, shop_id, douyin_account?, aweme_id?, advertiser_id?, products?, note? }"""
    body = dict(payload or {})
    if not str(body.get("shop_id") or "").strip():
        return fail(400, "请填写店铺 ID（shop_id）")
    return ok(store_store.add_store(platform, body))


@router.delete("/{platform}/{store_id}")
async def remove_store(platform: str, store_id: str):
    """删除店铺。"""
    if not store_store.delete_store(platform, store_id):
        return fail(404, "店铺不存在或已被删除")
    return ok({"store_id": store_id, "deleted": True})