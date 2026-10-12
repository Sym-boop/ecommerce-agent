"""文案生成 API（独立模块）

一键生成：商品信息 / 提示词 → 高转化广告文案（文案 Agent）。
    POST /api/v1/copy/generate   JSON: { product_name, product_sell_points, platform, extra }
        → { task_id }
    GET  /api/v1/copy/status/{task_id} → { status, title, content, tags, platform, error }

生成异步执行（BackgroundTasks）；成功后登记到广告物料仓库（ad_store）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from app.agent.copywriter import CopyWriterInput, generate_copy
from app.common import pipeline

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/v1/copy", tags=["copy"])

# task_id -> { task_id, status, title, content, tags, platform, error }
COPY_TASKS: dict[str, dict] = {}


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


def _normalize_sell_points(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    if isinstance(raw, str):
        return [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]
    return []


def _parse_budget(raw) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _run_generation(task_id: str, payload: dict) -> None:
    task = COPY_TASKS.get(task_id)
    if task is None:
        return
    task["status"] = "processing"
    try:
        input_data = CopyWriterInput(
            product_name=payload["product_name"],
            product_sell_points=payload["sell_points"],
            product_category=payload["category"],
            platform=payload["platform"],
        )
        result = generate_copy(input_data)
        task.update(
            status="succeeded",
            title=result.title,
            content=result.content,
            tags=result.tags,
            platform=result.platform,
        )
        # 登记到广告流水线：自动完成违禁词 / 预算审查并落定阶段
        pipeline.register_ad(
            ad_type="copy",
            title=result.title,
            prompt=payload["prompt"],
            platform=result.platform,
            content=result.content,
            budget=payload.get("budget"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("文案生成失败 task=%s: %s", task_id, exc)
        task["status"] = "failed"
        task["error"] = str(exc)


@router.post("/generate")
async def generate_copy_api(background: BackgroundTasks, payload: dict | None = None):
    body = payload or {}
    product_name = str(body.get("product_name") or "").strip()
    if not product_name:
        return fail(400, "请填写商品名称")

    platform = str(body.get("platform") or "xiaohongshu").strip() or "xiaohongshu"
    sell_points = _normalize_sell_points(body.get("product_sell_points"))
    extra = str(body.get("extra") or "").strip()
    prompt = "；".join(
        p for p in [
            f"为商品「{product_name}」生成 {platform} 平台推广文案",
            "、".join(sell_points) if sell_points else "",
            extra,
        ] if p
    )

    task_id = f"CPY-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    COPY_TASKS[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "title": "",
        "content": "",
        "tags": [],
        "platform": platform,
        "error": None,
    }
    background.add_task(_run_generation, task_id, {
        "product_name": product_name,
        "sell_points": sell_points,
        "category": str(body.get("product_category") or "通用").strip() or "通用",
        "platform": platform,
        "prompt": prompt,
        "budget": _parse_budget(body.get("budget")),
    })
    return ok({"task_id": task_id})


@router.get("/status/{task_id}")
async def copy_status_api(task_id: str):
    task = COPY_TASKS.get(task_id)
    if task is None:
        return fail(404, "文案任务不存在")
    return ok(task)