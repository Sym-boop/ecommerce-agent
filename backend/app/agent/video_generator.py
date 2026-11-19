"""视频生成 Agent：调用独立的第三方「图生视频」API，把商品图片 + 提示词生成为约 10s 动态短视频。

设计要点:
    - 独立第三方 API，按 VIDEO_PROVIDER 分派协议，与文案 / 图文生成模块解耦：
        kling（默认）  可灵 Kling 图生视频协议
        aliyun         阿里云百炼（DashScope）图生视频协议，异步任务 + /api/v1/tasks 轮询
    - 全部通过环境变量配置，密钥不写死在代码里：
        VIDEO_PROVIDER       供应商标识（kling / aliyun / local，默认 kling）
                     local = 本地合成：把海报做成「缓慢推移 + 淡入淡出」的 MP4，
                             不调用任何第三方模型、不需要 Key，用于无授权/离线演示
                     注意 local 是**本地合成**，不是 AI 生成视频，别混着说
        VIDEO_API_KEY        供应商 API Key（缺失时返回友好错误，不阻塞开发联调）
        VIDEO_API_BASE_URL   供应商 API 地址（aliyun 填完整的 video-synthesis endpoint）
        VIDEO_API_MODEL      模型名（kling: kling-v1；aliyun: 如 wanx2.1-i2v-turbo）
        VIDEO_DURATION       目标时长（秒），默认 10
        VIDEO_POLL_INTERVAL  轮询间隔（秒），默认 5
        VIDEO_POLL_TIMEOUT   轮询超时（秒），默认 300
    - 主流程：提交任务 → 轮询任务状态 → 下载成片到 data/media/videos

新增其他供应商时，在 `_submit_task` / `_poll_task` 的 provider 分派处补充对应实现即可。
"""
from __future__ import annotations

import base64
import mimetypes
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    # override=False：**真实环境变量优先**，.env 只补空缺。
    # 原来用 True，会让 .env 覆盖掉命令行/容器注入的值 —— 于是
    # `VIDEO_PROVIDER=local python ...` 这类临时覆盖全部失效（踩过）。
    load_dotenv(_ROOT / ".env", override=False)
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
            "（参考 .env.example 中的 VIDEO_* 配置项）。"
            "若只需本地出片、不接第三方模型，可设 VIDEO_PROVIDER=local："
            "该通道用内置 ffmpeg 把海报合成短视频，不需要 Key。"
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


def _image_data_uri(image_path: str) -> str:
    """阿里云百炼 img_url 入参：公网地址原样透传，本地文件转 data URI。"""
    if str(image_path).startswith(("http://", "https://")):
        return image_path
    path = Path(image_path)
    if not path.exists():
        raise VideoAPIError(f"商品图片不存在：{image_path}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ---------------------------------------------------------------------------
# 供应商接口：按 provider 分派（kling 默认 / aliyun 百炼 DashScope）
# ---------------------------------------------------------------------------

_ALIYUN_PROVIDERS = {"aliyun", "dashscope", "bailian", "aliyun-bailian"}
# 本地合成通道：不需要 Key，用 imageio-ffmpeg 自带的 ffmpeg 把静图渲染成短视频
_LOCAL_PROVIDERS = {"local", "ffmpeg", "offline"}


def _submit_task(cfg: dict, prompt: str, image_path: str, duration: int) -> str:
    """提交图生视频任务，返回第三方 task_id。"""
    if cfg["provider"] in _ALIYUN_PROVIDERS:
        return _submit_aliyun(cfg, prompt, image_path, duration)
    return _submit_kling(cfg, prompt, image_path, duration)


def _poll_task(cfg: dict, task_id: str) -> str:
    """轮询任务状态，成功后返回成片 URL。"""
    if cfg["provider"] in _ALIYUN_PROVIDERS:
        return _poll_aliyun(cfg, task_id)
    return _poll_kling(cfg, task_id)


def _submit_kling(cfg: dict, prompt: str, image_path: str, duration: int) -> str:
    """可灵 Kling：提交图生视频任务，返回 task_id。"""
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


def _poll_kling(cfg: dict, task_id: str) -> str:
    """可灵 Kling：轮询任务状态，成功后返回成片 URL。"""
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


def _aliyun_uses_media(model: str) -> bool:
    """wan2.7 及以后改用 input.media 数组输入；更早的 wan2.x 使用 input.img_url。"""
    return model.strip().lower().startswith("wan2.7")


def _aliyun_input(cfg: dict, prompt: str, image_path: str) -> dict:
    """按模型协议构造输入体：wan2.7 → media 数组；旧版 → img_url。"""
    text = prompt or "商品动态展示"
    url = _image_data_uri(image_path)
    if _aliyun_uses_media(cfg["model"]):
        return {"prompt": text, "media": [{"type": "first_frame", "url": url}]}
    return {"prompt": text, "img_url": url}


def _submit_aliyun(cfg: dict, prompt: str, image_path: str, duration: int) -> str:
    """阿里云百炼（DashScope）：提交图生视频异步任务，返回 task_id。

    需带 X-DashScope-Async: enable 触发异步；body 使用 model / input / parameters 结构。
    """
    import httpx

    headers = {
        "Authorization": f"Bearer {cfg['key']}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": cfg["model"],
        "input": _aliyun_input(cfg, prompt, image_path),
        "parameters": {
            "duration": int(duration),
            "resolution": "720P",
        },
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(cfg["base_url"], headers=headers, json=payload)
    if resp.status_code >= 400:
        raise VideoAPIError(f"提交视频任务失败 HTTP {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    output = body.get("output") or {}
    task_id = output.get("task_id")
    if not task_id:
        raise VideoAPIError(f"提交视频任务失败：{body}")
    return str(task_id)


def _aliyun_task_url(cfg: dict, task_id: str) -> str:
    """由合成 endpoint 推导任务查询地址：{scheme}://{host}/api/v1/tasks/{task_id}。"""
    parts = urlsplit(cfg["base_url"])
    return f"{parts.scheme}://{parts.netloc}/api/v1/tasks/{task_id}"


def _poll_aliyun(cfg: dict, task_id: str) -> str:
    """阿里云百炼（DashScope）：轮询异步任务，成功后返回成片 URL。"""
    import httpx

    url = _aliyun_task_url(cfg, task_id)
    headers = {"Authorization": f"Bearer {cfg['key']}"}
    deadline = time.time() + cfg["poll_timeout"]
    while time.time() < deadline:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise VideoAPIError(f"查询视频任务失败 HTTP {resp.status_code}: {resp.text[:300]}")

        body = resp.json()
        output = body.get("output") or {}
        status = str(output.get("task_status") or "").upper()
        if status in ("SUCCEEDED", "SUCCESS"):
            video_url = output.get("video_url")
            if video_url:
                return str(video_url)
            raise VideoAPIError(f"视频任务已完成但未返回成片地址：{body}")
        if status in ("FAILED", "CANCELED", "CANCELLED", "UNKNOWN"):
            raise VideoAPIError(
                f"视频生成失败：{output.get('message') or output.get('code') or body}"
            )
        time.sleep(cfg["poll_interval"])
    raise VideoAPIError(f"视频生成超时（>{int(cfg['poll_timeout'])}s），请稍后重试")


# ---------------------------------------------------------------------------
# 本地合成通道（VIDEO_PROVIDER=local）
# ---------------------------------------------------------------------------

def _ffmpeg_exe() -> str:
    """取 ffmpeg 可执行文件路径。

    优先用 `imageio-ffmpeg` 自带的静态编译版本——它随 pip 包一起分发，
    Windows / Linux / Docker 都有对应二进制，**不需要系统安装 ffmpeg**。
    这样部署环境不必为了一个兜底通道去装系统包。
    """
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise VideoAPIError(
            f"本地合成需要 imageio-ffmpeg（pip install imageio-ffmpeg）：{exc}"
        ) from exc


def local_available() -> bool:
    """本地合成通道是否可用（前端据此决定是否给出「本地出片」选项）。"""
    try:
        import imageio_ffmpeg  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _generate_local(prompt: str, image_path: str, duration: int) -> str:
    """把静图合成一段短视频：缓慢推进 + 淡入淡出。

    **这是本地合成，不是 AI 生成视频。** 它的用途是：
    - 没有第三方 Key 时，链路仍能产出可播放的成片（演示 / 联调）
    - 素材本身已经是排版好的海报，推进一下比纯静态图更像广告位素材

    产物：1080x1440 / 25fps / H.264 / 无音轨，几秒出片（实测 6 秒成片约 2.5 秒）。
    """
    import subprocess

    src = Path(image_path)
    if not src.exists():
        raise VideoAPIError(f"素材图片不存在：{image_path}")

    duration = max(2, min(int(duration or 6), 30))
    fps = 25
    fade = 0.5
    exe = _ffmpeg_exe()
    out_path = OUTPUT_DIR / f"{uuid.uuid4().hex[:8]}.mp4"

    # 用「放大后对角平移」做出运动，**不要用 zoompan**。
    #
    # 踩过的坑：一开始写的是
    #     zoompan=z='min(zoom+0.0007,1.12)':d=1:s=1080x1440
    # 配合 `-loop 1`。结果是**画面完全静止**——实测 1s 与 5s 两帧的平均像素差
    # 是 0.00（只有淡入淡出带来的亮度变化）。原因是 `d=1` 让每个输入帧都从
    # zoom=1 重新开始一段长度为 1 的缩放，累积不起来。
    #
    # 换成 crop 的 x/y 表达式：它按 `t`（当前时间）逐帧求值，平移必然生效。
    # 实测同样参数下 1s→5s 平均像素差 31.4、28% 像素在变，肉眼可见在动。
    # 源图先放大到 1.2 倍，才有平移余量（否则没有可移动的像素）。
    pan = f"(t/{duration})"
    vf = (
        "scale=1296:1728:force_original_aspect_ratio=increase,"
        "crop=1296:1728,"
        f"crop=1080:1440:x='(iw-ow)*{pan}':y='(ih-oh)*{pan}',"
        f"fade=t=in:st=0:d={fade},fade=t=out:st={duration - fade}:d={fade},"
        "format=yuv420p"
    )
    cmd = [
        exe, "-y", "-loop", "1", "-i", str(src), "-t", str(duration),
        "-vf", vf, "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
        str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise VideoAPIError(f"本地合成超时（>180s）：{exc}") from exc
    if proc.returncode != 0:
        raise VideoAPIError(f"本地合成失败（ffmpeg 退出码 {proc.returncode}）：{proc.stderr[-400:]}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise VideoAPIError("本地合成失败：产物为空")
    return str(out_path)


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
    duration = duration or cfg["duration"]

    # 本地合成通道：不校验 Key、不提交第三方任务，直接出片
    if cfg["provider"] in _LOCAL_PROVIDERS:
        return _generate_local(prompt, image_path, duration)

    _require_key(cfg)
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