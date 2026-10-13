"""广告生成历史记录 API（正常 / 半成品）。

    GET    /api/v1/history            历史记录列表（含 wip 半成品标记）
    POST   /api/v1/history            保存一次生成（正常记录）
    POST   /api/v1/history/pause      保存为半成品（stage=wip）
    DELETE /api/v1/history/{id}       删除一条记录
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.common import history_store

router = APIRouter(prefix="/api/v1/history", tags=["history"])


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


def _payload_to_record(body: dict, stage: str) -> dict:
    return history_store.save_record(
        product=body.get("product") or {},
        keyword=str(body.get("keyword") or ""),
        platform=str(body.get("platform") or ""),
        copy=body.get("copy") or {},
        image_url=body.get("image_url"),
        video_url=body.get("video_url"),
        stage=stage,
        record_id=body.get("record_id"),
    )


@router.get("")
async def list_history_api():
    return ok(history_store.list_records())


@router.post("")
async def save_history_api(payload: dict | None = None):
    """保存本次生成为正常记录（无红色标签）。"""
    return ok(_payload_to_record(payload or {}, history_store.STAGE_NORMAL))


@router.post("/pause")
async def pause_history_api(payload: dict | None = None):
    """保存本次生成为半成品记录（红色标签），用于「暂停生成并退出」。"""
    return ok(_payload_to_record(payload or {}, history_store.STAGE_WIP))


@router.delete("/{record_id}")
async def delete_history_api(record_id: str):
    if not history_store.delete_record(record_id):
        return fail(404, "历史记录不存在")
    return ok({"id": record_id, "deleted": True})