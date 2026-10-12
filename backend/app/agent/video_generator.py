"""视频生成 Agent：调用独立的第三方「图生视频」API，把商品图片 + 提示词生成为约 10s 动态短视频。

设计要点:
    - 独立第三方 API（默认对接可灵 Kling 图生视频），与文案 / 图文生成模块解耦
    - 全部通过环境变量配置，密钥不写死在代码里：
        VIDEO_PROVIDER       供应商标识（默认 kling）
        VIDEO_API_KEY        供应商 API Key（缺失时返回友好错误，不阻塞开发联调）
        VIDEO_API_BASE_URL   供应商 API 根地址
        VIDEO_API_MODEL      模型名（如 kling-v1 / kling-v1-6）
        VIDEO_DURATION       目标时长（秒），默认 10
        VIDEO_POLL_INTERVAL  轮询间隔（秒），默认 5
        VIDEO_POLL_TIMEOUT   轮询超时（秒），默认 300
    - 主流程：提交任务 → 轮询任务状态 → 下载成片到 data/media/videos

对接其他供应商时，只需替换 `_submit_task` / `_poll_task` 两个函数的实现即可。
"""
from __future__ import annotations

import base64
import os
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env", override=True)
except Exception:  # noqa: BLE001
    pass

OUTPUT_DIR = Path("data/media/videos")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR = Path("data/media/temp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)


class VideoAPIError(RuntimeError):
    """视频生成 API 相关错误（配置缺失 / 调用失败 / 超时）。"""


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def _config() -> dict:
    """读取视频生成 API 配置（每次调用读取，支持热更新 .env）。"""
    return {
        "provider": os.getenv("VIDEO_PROVIDER", "kling").strip().lower(),
        "key": os.getenv("VIDEO_API_KEY", "").strip(),
        "base_url": os.getenv("VIDEO_API_BASE_URL", "https://api-beijing.klingai.com").rstrip("/"),
        "model": os.getenv("VIDEO_API_MODEL", "kling-v1").strip(),
        "duration": int(os.getenv("VIDEO_DURATION", "10")),
        "poll_interval": float(os.getenv("VIDEO_POLL_INTERVAL", "5")),
        "poll_timeout": float(os.getenv("VIDEO_POLL_TIMEOUT", "300")),
    }


def _require_key(cfg: dict) -> None:
    # 占位符（如 ***）视为未配置，给出友好提示而非发起无效请求
    if not cfg["key"] or set(cfg["key"]) <= {"*"}:
        raise VideoAPIError(
            "未配置视频生成 API Key，请在项目根目录 .env 中设置 VIDEO_API_KEY"
            "（参考 .env.example 中的 VIDEO_* 配置项）。本模块使用独立的图生视频第三方 API。"
        )


def _headers(cfg: dict) -> dict:
    return {
        "Authorization": f"Bearer {cfg['key']}",
        "Content-Type": "application/json",
    }


def _image_payload(image_path: str) -> str:
    """图片输入：本地文件转 base64，网络地址原样透传。"""
    if str(image_path).startswith(("http://", "https://")):
        return image_path
    path = Path(image_path)
    if not path.exists():
        raise VideoAPIError(f"商品图片不存在：{image_path}")
    return base64.b64encode(path.read_bytes()).decode("ascii")


# ---------------------------------------------------------------------------
# 供应商接口（默认对齐可灵 Kling 的图生视频协议）
# ---------------------------------------------------------------------------

def _submit_task(cfg: dict, prompt: str, image_path: str, duration: int) -> str:
    """提交图生视频任务，返回第三方 task_id。"""
    import httpx

    url = f"{cfg['base_url']}/v1/videos/image2video"
    payload = {
        "model_name": cfg["model"],
        "image": _image_payload(image_path),
        "prompt": prompt or "商品动态展示",
        "duration": str(duration),
        "mode": "std",
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, headers=_headers(cfg), json=payload)
    if resp.status_code >= 400:
        raise VideoAPIError(f"提交视频任务失败 HTTP {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    data = body.get("data") or {}
    task_id = data.get("task_id") or data.get("id")
    if str(body.get("code", 0)) not in ("0", "None") or not task_id:
        raise VideoAPIError(f"提交视频任务失败：{body}")
    return str(task_id)


def _poll_task(cfg: dict, task_id: str) -> str:
    """轮询任务状态，成功后返回成片 URL。"""
    import httpx

    url = f"{cfg['base_url']}/v1/videos/image2video/{task_id}"
    deadline = time.time() + cfg["poll_timeout"]
    while time.time() < deadline:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=_headers(cfg))
        if resp.status_code >= 400:
            raise VideoAPIError(f"查询视频任务失败 HTTP {resp.status_code}: {resp.text[:300]}")

        body = resp.json()
        data = body.get("data") or {}
        status = str(data.get("task_status") or "").lower()
        if status in ("succeed", "succeeded", "success"):
            videos = (data.get("task_result") or {}).get("videos") or []
            if videos and videos[0].get("url"):
                return videos[0]["url"]
            raise VideoAPIError(f"视频任务已完成但未返回成片地址：{body}")
        if status in ("failed", "fail", "error"):
            raise VideoAPIError(f"视频生成失败：{data.get('task_status_msg') or body}")
        time.sleep(cfg["poll_interval"])
    raise VideoAPIError(f"视频生成超时（>{int(cfg['poll_timeout'])}s），请稍后重试")


def _download(url: str, out_path: Path) -> str:
    """下载成片到本地。"""
    import httpx

    with httpx.stream("GET", url, timeout=180.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    return str(out_path)


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------

def generate_short_video(prompt: str, image_path: str, duration: int | None = None) -> str:
    """根据提示词 + 商品图片，生成约 10s 的动态短视频，返回本地文件路径。

    这是本模块的独立主入口，与文案 / 图文生成链路完全解耦。
    """
    cfg = _config()
    _require_key(cfg)
    duration = duration or cfg["duration"]

    task_id = _submit_task(cfg, prompt, image_path, duration)
    video_url = _poll_task(cfg, task_id)

    out_path = OUTPUT_DIR / f"{uuid.uuid4().hex[:8]}.mp4"
    return _download(video_url, out_path)


def generate_video(image_path: str, title: str, content: str) -> str:
    """兼容旧签名的包装：由标题 + 正文拼出提示词后生成短视频。"""
    prompt = "；".join(p for p in [title, content] if p).strip() or "商品动态展示"
    return generate_short_video(prompt=prompt, image_path=image_path)


def video_generator_node(state: dict) -> dict:
    """Supervisor 调度入口。"""
    try:
        copy_result = state.get("copy_result") or {}
        image_result = state.get("image_result") or {}

        image_path = image_result.get("image_path")
        if not image_path:
            return {"status": "failed", "error": "没有图片，无法生成视频"}

        prompt = state.get("video_prompt") or "；".join(
            p for p in [copy_result.get("title"), copy_result.get("content")] if p
        )
        video_path = generate_short_video(prompt=prompt, image_path=image_path)
        return {
            "video_result": {"video_path": video_path},
            "status": "done",
        }
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "error": f"视频生成失败：{e}"}