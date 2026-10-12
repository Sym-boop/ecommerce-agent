"""广告物料历史 API。

    GET /api/v1/ads → 全部广告物料（文案 / 图文 / 视频，按时间倒序）

供「历史广告发售」页面按时间归档查看，以及「任务拓扑看板」展示生成结果。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.common import ad_store

router = APIRouter(prefix="/api/v1/ads", tags=["ads"])


@router.get("")
async def list_ads_api():
    return {"code": 0, "msg": "ok", "data": ad_store.list_ads()}