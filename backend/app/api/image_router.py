"""图文生成 API（独立模块）

一键生成：提示词（可选商品图片）→ 图文广告海报（图文 Agent）。
    POST /api/v1/image/generate   multipart: image(可选文件) + prompt + platform
        → { task_id }
    GET  /api/v1/image/status/{task_id}
        → { status, prompt, input_image_url, image_url, copy_title, copy_content, error }

两种模式：
    - 不上传图片：仅根据提示词生成海报
    - 上传图片：根据「图片 + 提示词」生成海报（商品图嵌入海报）

生成异步执行（BackgroundTasks）；成功后登记到广告物料仓库（ad_store）。
"""
from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile
from fastapi.responses import JSONResponse

from app.agent.copywriter import CopyWriterInput, generate_copy
from app.agent.image_generator import generate_image
from app.common import ad_store

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/v1/image", tags=["image"])

UPLOAD_DIR = Path("data/media/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# task_id -> { ... }
IMAGE_TASKS: dict[str, dict] = {}


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


def _compose_copy(prompt: str, platform: str) -> tuple[str, str, list[str]]:
    """根据提示词生成海报文案；LLM 不可用时降级为直接使用提示词。"""
    try:
        result = generate_copy(CopyWriterInput(
            product_name=prompt[:20] or "新品",
            product_sell_points=[prompt],
            product_category="通用",
            platform=platform,
        ))
        if result.content:
            return result.title, result.content, list(result.tags)
    except Exception as exc:  # noqa: BLE001
        logger.warning("图文文案生成降级为提示词: %s", exc)
    return prompt[:40], prompt, []


def _run_generation(task_id: str, prompt: str, platform: str, input_image_path: str | None) -> None:
    task = IMAGE_TASKS.get(task_id)
    if task is None:
        return
    task["status"] = "processing"
    try:
        title, content, tags = _compose_copy(prompt, platform)
        path = generate_image(title=title, content=content, tags=tags, input_image_path=input_image_path)
        image_url = f"/media/images/{Path(path).name}"
        task.update(
            status="succeeded",
            image_url=image_url,
            copy_title=title,
            copy_content=content,
        )
        ad_store.add_ad(
            ad_type="image",
            title=title,
            prompt=prompt,
            platform=platform,
            content=content,
            input_image=task.get("input_image_url"),
            result_url=image_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("图文生成失败 task=%s: %s", task_id, exc)
        task["status"] = "failed"
        task["error"] = str(exc)


@router.post("/generate")
async def generate_image_api(
    background: BackgroundTasks,
    prompt: str = Form(""),
    platform: str = Form("xiaohongshu"),
    image: UploadFile | None = File(None),
):
    """提交图文生成任务：提示词 + 可选商品图片，返回 task_id 供前端轮询。"""
    if not (prompt or "").strip():
        return fail(400, "请填写图文生成提示词")

    saved_path: Path | None = None
    if image is not None and image.filename:
        suffix = Path(image.filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            return fail(400, "请上传 png / jpg / jpeg / webp / bmp 格式的商品图片")
        saved_path = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
        with open(saved_path, "wb") as f:
            shutil.copyfileobj(image.file, f)

    task_id = f"IMG-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    IMAGE_TASKS[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "prompt": prompt,
        "platform": platform,
        "input_image_url": f"/media/uploads/{saved_path.name}" if saved_path else None,
        "image_url": None,
        "copy_title": "",
        "copy_content": "",
        "error": None,
    }
    background.add_task(
        _run_generation, task_id, prompt, platform,
        str(saved_path) if saved_path else None,
    )
    return ok({"task_id": task_id})


@router.get("/status/{task_id}")
async def image_status_api(task_id: str):
    task = IMAGE_TASKS.get(task_id)
    if task is None:
        return fail(404, "图文任务不存在")
    return ok(task)