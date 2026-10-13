"""图文生成 Agent：把文案渲染成小红书风格的宣传海报图片。

支持两种模式：
    - 提示词模式：仅根据文案生成海报（不嵌入商品图片）
    - 图片 + 提示词模式：在海报中嵌入用户上传的商品图片

图片以 base64 data URI 内联，避免 playwright 读取本地路径的权限问题。

【独立 API】本模块使用独立的图文生成 API 配置（与文案 / 视频模块解耦），通过环境变量控制：
    IMAGE_PROVIDER       local（默认，本地 playwright 渲染海报）| dashscope（阿里云百炼通义万相文生图）
    IMAGE_API_KEY        第三方 API Key（provider=local 时无需配置）
    IMAGE_API_BASE_URL   dashscope：文生图 endpoint（.../text2image/image-synthesis）
    IMAGE_API_MODEL      模型名，如 wanx2.1-t2i-turbo
    IMAGE_API_SIZE       出图尺寸，如 1024*1024（默认）
    IMAGE_POLL_INTERVAL  轮询间隔（秒，默认 5）
    IMAGE_POLL_TIMEOUT   轮询超时（秒，默认 300）
默认 provider=local，行为与改造前完全一致；配置 dashscope 即切换到独立第三方文生图 API。
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

    load_dotenv(_ROOT / ".env", override=True)
except Exception:  # noqa: BLE001
    pass

OUTPUT_DIR = Path("data/media/images")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class ImageAPIError(RuntimeError):
    """图文生成 API 相关错误（配置缺失 / 调用失败 / 超时）。"""


def _image_config() -> dict:
    """读取图文生成 Agent 的独立 API 配置（每次调用读取，支持热更新 .env）。"""
    return {
        "provider": os.getenv("IMAGE_PROVIDER", "local").strip().lower(),
        "key": os.getenv("IMAGE_API_KEY", "").strip(),
        "base_url": os.getenv("IMAGE_API_BASE_URL", "").strip().rstrip("/"),
        "model": os.getenv("IMAGE_API_MODEL", "wanx2.1-t2i-turbo").strip(),
        "size": os.getenv("IMAGE_API_SIZE", "1024*1024").strip(),
        "poll_interval": float(os.getenv("IMAGE_POLL_INTERVAL", "5")),
        "poll_timeout": float(os.getenv("IMAGE_POLL_TIMEOUT", "300")),
    }


def _require_key(cfg: dict) -> None:
    # 占位符（如 ***）视为未配置，给出友好提示而非发起无效请求
    if not cfg["key"] or set(cfg["key"]) <= {"*"}:
        raise ImageAPIError(
            "未配置图文生成 API Key，请在项目根目录 .env 中设置 IMAGE_API_KEY"
            "（参考 .env.example 中的 IMAGE_* 配置项）。本模块使用独立的图文生成 API。"
        )


def _prompt_of(title: str, content: str, tags: list) -> str:
    tag_text = " ".join(tags) if isinstance(tags, list) else str(tags)
    return "；".join(p for p in [title, content, tag_text] if str(p).strip())[:900] or "商品宣传海报"


def _task_url(cfg: dict, task_id: str) -> str:
    """由合成 endpoint 推导任务查询地址：{scheme}://{host}/api/v1/tasks/{task_id}。"""
    parts = urlsplit(cfg["base_url"])
    return f"{parts.scheme}://{parts.netloc}/api/v1/tasks/{task_id}"


def _submit_dashscope(cfg: dict, prompt: str) -> str:
    """阿里云百炼（DashScope）通义万相文生图：提交异步任务，返回 task_id。"""
    import httpx

    headers = {
        "Authorization": f"Bearer {cfg['key']}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": cfg["model"],
        "input": {"prompt": prompt},
        "parameters": {"size": cfg["size"], "n": 1},
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(cfg["base_url"], headers=headers, json=payload)
    if resp.status_code >= 400:
        raise ImageAPIError(f"提交图文任务失败 HTTP {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    task_id = (body.get("output") or {}).get("task_id")
    if not task_id:
        raise ImageAPIError(f"提交图文任务失败：{body}")
    return str(task_id)


def _poll_dashscope(cfg: dict, task_id: str) -> str:
    """阿里云百炼：轮询异步任务，成功后返回图片 URL。"""
    import httpx

    url = _task_url(cfg, task_id)
    headers = {"Authorization": f"Bearer {cfg['key']}"}
    deadline = time.time() + cfg["poll_timeout"]
    while time.time() < deadline:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise ImageAPIError(f"查询图文任务失败 HTTP {resp.status_code}: {resp.text[:300]}")

        output = (resp.json().get("output") or {})
        status = str(output.get("task_status") or "").upper()
        if status in ("SUCCEEDED", "SUCCESS"):
            results = output.get("results") or []
            if results and results[0].get("url"):
                return str(results[0]["url"])
            raise ImageAPIError(f"图文任务已完成但未返回图片地址：{output}")
        if status in ("FAILED", "CANCELED", "CANCELLED", "UNKNOWN"):
            raise ImageAPIError(f"图文生成失败：{output.get('message') or output.get('code') or output}")
        time.sleep(cfg["poll_interval"])
    raise ImageAPIError(f"图文生成超时（>{int(cfg['poll_timeout'])}s），请稍后重试")


def _generate_via_api(cfg: dict, title: str, content: str, tags: list) -> str:
    """调用独立的第三方文生图 API，下载成图到本地并返回路径。"""
    import httpx

    _require_key(cfg)
    task_id = _submit_dashscope(cfg, _prompt_of(title, content, tags))
    image_url = _poll_dashscope(cfg, task_id)

    out_path = OUTPUT_DIR / f"{uuid.uuid4().hex[:8]}.png"
    with httpx.stream("GET", image_url, timeout=120.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    return str(out_path)


HTML_TEMPLATE = """
<html><body style="
    width: 1080px; height: 1440px; margin: 0;
    background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
    padding: 80px 60px; font-family: 'Microsoft YaHei', sans-serif;
    display: flex; flex-direction: column; box-sizing: border-box;
">
    <div style="font-size: 72px; color: #ff6b6b; margin-bottom: 20px;">✨</div>
    <h1 style="font-size: 64px; font-weight: bold; color: #333;
        line-height: 1.3; margin-bottom: 40px;">
        {title}
    </h1>
    {image_block}
    <p style="font-size: 36px; line-height: 1.8; color: #555;
        flex: 1; white-space: pre-wrap;">
        {content}
    </p>
    <div style="font-size: 30px; color: #ff6b6b; margin-top: 40px;">
        {tags}
    </div>
</body></html>
"""

IMAGE_BLOCK = """
    <div style="text-align: center; margin-bottom: 40px;">
        <img src="{src}" style="
            max-width: 100%; max-height: 560px; border-radius: 24px;
            box-shadow: 0 16px 40px rgba(0,0,0,0.18); object-fit: cover;
        " />
    </div>
"""


def _to_data_uri(image_path: str) -> str:
    """把本地商品图片转成 base64 data URI。"""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"商品图片不存在：{image_path}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def generate_image(title: str, content: str, tags: list, input_image_path: str | None = None) -> str:
    """生成单张图文海报，返回文件路径。

    按 IMAGE_PROVIDER 分派：local（默认，本地 playwright 渲染）
    或 dashscope（独立第三方文生图 API）。
    input_image_path 为空时仅用文案渲染；local 模式下会把该商品图片嵌入海报顶部。
    """
    cfg = _image_config()
    if cfg["provider"] != "local" and cfg["base_url"]:
        return _generate_via_api(cfg, title, content, tags)
    return _generate_local(title, content, tags, input_image_path)


def _generate_local(title: str, content: str, tags: list, input_image_path: str | None = None) -> str:
    """本地 playwright 渲染海报（原有实现，作为默认 provider）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安装 playwright，无法渲染图文海报。请执行："
            "pip install playwright && playwright install chromium"
        ) from exc

    image_block = ""
    if input_image_path:
        image_block = IMAGE_BLOCK.format(src=_to_data_uri(input_image_path))

    html = HTML_TEMPLATE.format(
        title=title,
        content=content,
        tags=" ".join(tags) if isinstance(tags, list) else str(tags),
        image_block=image_block,
    )
    filename = OUTPUT_DIR / f"{uuid.uuid4().hex[:8]}.png"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1440})
        page.set_content(html)
        page.screenshot(path=str(filename))
        browser.close()

    return str(filename)


def image_generator_node(state: dict) -> dict:
    """Supervisor 调度入口。"""
    try:
        copy_result = state.get("copy_result") or {}
        if not copy_result.get("content"):
            return {"status": "failed", "error": "没有文案，无法生成图文"}

        image_path = generate_image(
            title=copy_result.get("title", ""),
            content=copy_result.get("content", ""),
            tags=copy_result.get("tags", []),
            input_image_path=state.get("input_image_path"),
        )
        return {
            "image_result": {"image_path": image_path},
            "status": "done",
        }
    except Exception as e:
        return {"status": "failed", "error": f"图文生成失败：{e}"}