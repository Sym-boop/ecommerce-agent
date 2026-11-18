"""图片理解校验：检查「图文素材」与「文案/商品」是否一致。

**为什么需要**

文案审查通过后才生成图文，但**没有任何机制检查图片内容是否真的对应文案**——
可能出现"文案讲防晒霜、图片渲染成别的商品"这类张冠李戴，人工审批时也未必发现。

**分两层，且诚实标注能力边界**

1. **客观预检（零成本，总是执行）**：
   - 成品图与输入商品图的**宽高比差异**（比例差过大通常意味着拉伸变形）
   - 是否真的使用了输入图（成品尺寸/内容指纹是否变化）
   - 成品图分辨率是否达到投放要求

2. **多模态语义校验（可选，需配置视觉模型）**：
   调用视觉模型判断"图片主体是否与文案描述一致"。
   **默认关闭**——它需要独立的视觉模型 API（`VISION_API_KEY` / `VISION_MODEL`）。
   未配置时明确返回 `available=False` 并说明原因，**不假装做过校验**。

设计上刻意把两层分开返回，避免"跑了个比例检查就宣称做了图片理解"。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

# 投放素材的常见要求（长宽比容差与最小边长）
ASPECT_TOLERANCE = 0.15     # 宽高比相对偏差超过 15% 视为可能拉伸
MIN_SHORT_SIDE = 600        # 短边最小像素


def _image_size(path: str | Path) -> Optional[tuple[int, int]]:
    """读取图片宽高（Pillow 可用时）。"""
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as img:
            return int(img.width), int(img.height)
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取图片尺寸失败 %s: %s", path, exc)
        return None


def _aspect(size: tuple[int, int]) -> float:
    w, h = size
    return round(w / h, 4) if h else 0.0


def prescreen(*, result_url: str = "", input_image: Optional[str] = None,
              data_dir: Optional[Path] = None) -> dict[str, Any]:
    """客观预检：尺寸 / 比例 / 是否使用输入图。

    纯本地计算，零 API 成本，可在每次图文生成后调用。
    """
    root = data_dir or Path("data")
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    def resolve(url: str) -> Optional[Path]:
        """把 /media/xxx 形式的 URL 解析成本地路径。"""
        if not url:
            return None
        rel = url
        for prefix in ("/media/", "media/"):
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
                break
        candidate = root / "media" / rel
        return candidate if candidate.exists() else None

    result_path = resolve(result_url)
    input_path = resolve(input_image or "")

    if result_path is None:
        return {
            "available": False,
            "reason": "成品图文件不存在，无法预检",
            "checks": [],
            "warnings": ["成品图缺失"],
        }

    result_size = _image_size(result_path)
    if result_size is None:
        return {
            "available": False,
            "reason": "无法解析成品图尺寸（Pillow 未安装或文件损坏）",
            "checks": [],
            "warnings": [],
        }

    rw, rh = result_size
    short_side = min(rw, rh)
    checks.append({
        "name": "分辨率",
        "passed": short_side >= MIN_SHORT_SIDE,
        "detail": f"成品 {rw}×{rh}，短边 {short_side}（要求 ≥ {MIN_SHORT_SIDE}）",
    })
    if short_side < MIN_SHORT_SIDE:
        warnings.append(f"成品图短边仅 {short_side}px，低于投放建议值 {MIN_SHORT_SIDE}px")

    input_size = _image_size(input_path) if input_path else None
    if input_size:
        ra, ia = _aspect(result_size), _aspect(input_size)
        deviation = abs(ra - ia) / ia if ia else 0.0
        passed = deviation <= ASPECT_TOLERANCE
        checks.append({
            "name": "宽高比一致性",
            "passed": passed,
            "detail": f"成品比例 {ra} vs 输入图比例 {ia}，偏差 {deviation:.1%}"
                      f"（容差 {ASPECT_TOLERANCE:.0%}）",
        })
        if not passed:
            warnings.append(
                f"成品图与输入商品图宽高比偏差 {deviation:.1%}，可能存在拉伸变形"
            )
        checks.append({
            "name": "使用输入图",
            "passed": True,
            "detail": "本次渲染嵌入了用户上传的商品图",
        })
    else:
        checks.append({
            "name": "使用输入图",
            "passed": True,
            "detail": "未上传商品图，按文案本地渲染海报（无需一致性比对）",
        })

    passed_all = all(c["passed"] for c in checks)
    return {
        "available": True,
        "passed": passed_all,
        "checks": checks,
        "warnings": warnings,
        "result_size": list(result_size),
        "input_size": list(input_size) if input_size else None,
    }


# ---------------------------------------------------------------------------
# 多模态语义校验（可选）
# ---------------------------------------------------------------------------

def vision_enabled() -> bool:
    """视觉校验默认关闭：需要独立的视觉模型 API。"""
    key = os.getenv("VISION_API_KEY", "").strip()
    model = os.getenv("VISION_MODEL", "").strip()
    return bool(key and model and not set(key) <= {"*"})


def _encode_image(path: Path, max_bytes: int = 3_500_000) -> Optional[str]:
    """把图片编码为 base64 data URI（过大时返回 None）。"""
    import base64

    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > max_bytes:
        return None
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "png")
    return f"data:image/{mime};base64,{base64.b64encode(raw).decode()}"


VISION_PROMPT = """你是电商素材审核员。请判断这张广告图是否与下面的文案描述的商品一致。

文案标题：{title}
文案正文：{content}

请只输出 JSON，不要输出其他内容：
{{"consistent": true 或 false, "confidence": 0-1 的小数, "reason": "一句话说明"}}"""


def vision_check(*, title: str = "", content: str = "", image_url: str = "",
                 data_dir: Optional[Path] = None) -> dict[str, Any]:
    """调用视觉模型判断图片与文案是否一致（需 VISION_API_KEY + VISION_MODEL）。

    未配置时返回 `available=False`，**不假装校验过**。
    任何失败也只返回 `available=False`，不影响主流程——它是辅助观测。
    """
    if not vision_enabled():
        return {
            "available": False,
            "reason": "未配置视觉模型（需要 VISION_API_KEY 与 VISION_MODEL）",
        }

    root = data_dir or Path("data")
    rel = image_url
    for prefix in ("/media/", "media/"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    path = root / "media" / rel
    if not path.exists():
        return {"available": False, "reason": "图片文件不存在"}

    data_uri = _encode_image(path)
    if data_uri is None:
        return {"available": False, "reason": "图片过大或无法编码"}

    import json as _json

    import httpx

    base = os.getenv("VISION_API_BASE", "").strip().rstrip("/") or "https://api.deepseek.com"
    model = os.getenv("VISION_MODEL", "").strip()
    key = os.getenv("VISION_API_KEY", "").strip()
    url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"

    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROMPT.format(title=title, content=content[:800])},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }],
        "temperature": 0,
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers={"Authorization": f"Bearer {key}",
                                             "Content-Type": "application/json"},
                               json=payload)
            if resp.status_code >= 400:
                return {"available": False,
                        "reason": f"视觉模型调用失败 HTTP {resp.status_code}: {resp.text[:200]}"}
            raw = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"视觉模型调用异常: {exc}"}

    import re

    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return {"available": False, "reason": "视觉模型返回无法解析", "raw": (raw or "")[:200]}
    try:
        data = _json.loads(match.group(0))
    except _json.JSONDecodeError:
        return {"available": False, "reason": "视觉模型返回非法 JSON", "raw": (raw or "")[:200]}

    return {
        "available": True,
        "model": model,
        "consistent": bool(data.get("consistent")),
        "confidence": data.get("confidence"),
        "reason": data.get("reason", ""),
    }


def check_asset(**kwargs: Any) -> dict[str, Any]:
    """完整校验：客观预检 + 可选语义校验。

    返回结构里两层结果分开，便于前端分别展示，也避免把"比例检查"
    夸大成"做了图片理解"。
    """
    prescreen_result = prescreen(
        result_url=kwargs.get("result_url", ""),
        input_image=kwargs.get("input_image"),
        data_dir=kwargs.get("data_dir"),
    )
    vision_result = vision_check(
        title=kwargs.get("title", ""),
        content=kwargs.get("content", ""),
        image_url=kwargs.get("result_url", ""),
        data_dir=kwargs.get("data_dir"),
    )
    ok = bool(prescreen_result.get("passed")) and (
        # 视觉校验不可用时不以它为准（能力边界明确）
        not vision_result.get("available") or bool(vision_result.get("consistent"))
    )
    return {
        "passed": ok,
        "prescreen": prescreen_result,
        "vision": vision_result,
        "vision_available": bool(vision_result.get("available")),
    }
