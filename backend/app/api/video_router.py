"""视频生成 API（独立模块）

一键生成：用户提示词 + 商品图片 → 约 10s 动态短视频（走独立第三方图生视频 API）。

    POST /api/v1/video/generate         multipart: image(文件) + prompt + duration
        → { task_id }
    GET  /api/v1/video/status/{task_id} → { status, video_url, error }

生成过程异步执行（BackgroundTasks），任务状态存于进程内内存（需单 worker 运行）。
成片通过 /media/videos/xxx.mp4 静态挂载对外提供播放地址。
"""
from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile
from fastapi.responses import JSONResponse

from app.agent.video_generator import generate_short_video
from app.common import ad_store

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/v1/video", tags=["video"])

UPLOAD_DIR = Path("data/media/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# task_id -> { task_id, status(pending|processing|succeeded|failed), prompt, video_url, error }
VIDEO_TASKS: dict[str, dict] = {}


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


def _run_generation(task_id: str, prompt: str, image_path: str, duration: int) -> None:
    """后台执行视频生成，并回写任务状态。"""
    task = VIDEO_TASKS.get(task_id)
    if task is None:
        return
    task["status"] = "processing"
    try:
        path = generate_short_video(prompt=prompt, image_path=image_path, duration=duration)
        video_url = f"/media/videos/{Path(path).name}"
        task["status"] = "succeeded"
        task["video_url"] = video_url
        ad_store.add_ad(
            ad_type="video",
            title=prompt[:40],
            prompt=prompt,
            input_image=task.get("input_image_url"),
            result_url=video_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("视频生成失败 task=%s: %s", task_id, exc)
        task["status"] = "failed"
        task["error"] = str(exc)


@router.post("/generate")
async def generate_video_api(
    background: BackgroundTasks,
    image: UploadFile = File(...),
    prompt: str = Form(""),
    duration: int = Form(10),
):
    """提交视频生成任务：商品图片 + 提示词，返回 task_id 供前端轮询。"""
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return fail(400, "请上传 png / jpg / jpeg / webp / bmp 格式的商品图片")
    if not (prompt or "").strip():
        return fail(400, "请填写视频生成提示词")

    task_id = f"VID-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    image_path = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
    with open(image_path, "wb") as f:
        shutil.copyfileobj(image.file, f)

    VIDEO_TASKS[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "prompt": prompt,
        "input_image_url": f"/media/uploads/{image_path.name}",
        "video_url": None,
        "error": None,
    }
    background.add_task(_run_generation, task_id, prompt, str(image_path), duration)
    return ok({"task_id": task_id})


@router.get("/status/{task_id}")
async def video_status_api(task_id: str):
    """查询视频生成任务状态。"""
    task = VIDEO_TASKS.get(task_id)
    if task is None:
        return fail(404, "视频任务不存在")
    return ok(task)