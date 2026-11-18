"""启动自检：把配置问题在**启动时**显式暴露出来。

**为什么要这个模块**

我们实际踩过一个坑：`.env` 里 `LLM_MODEL=deepseek-v41-flash` 是无效模型名，
DeepSeek 返回 HTTP 400，而 `copywriter.generate_copy()` 捕获异常后**静默降级成模板文案**——
不报错、日志里只有一行 warning，结果是很长时间都以为功能正常，实际大模型压根没被调用。

同类问题还有：`IMAGE_API_KEY` / `VIDEO_API_KEY` 是 `***` 占位符、
`MILVUS_HOST` 指向不可达的地址等。

**策略**：不阻断启动（演示环境要能带病运行），但把每一项的判定结果集中打出来，
并让 `/api/v1/system/config-status` 可查询，避免"看起来正常"。
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("uvicorn.error")


def _is_placeholder(value: str) -> bool:
    """判断是否是占位符（如 ***、sk-xxxx、空）。"""
    v = (value or "").strip()
    if not v:
        return True
    if set(v) <= {"*"}:
        return True
    if v.startswith("sk-xxxx") or v.startswith("your_") or v.startswith("<"):
        return True
    return False


# 官方支持的模型名（写错会 400，且会被静默降级掩盖）
_KNOWN_BAD_MODELS = {
    "deepseek-v41-flash": "deepseek-flash",
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v3": "deepseek-chat",
    "gpt-4-turbo-preview": "按服务商文档填写",
}


def collect() -> dict[str, Any]:
    """收集配置检查结果。

    返回:
        {"ok": bool, "errors": [...], "warnings": [...], "items": [...]}
    """
    errors: list[str] = []
    warnings: list[str] = []
    items: list[dict[str, Any]] = []

    def item(name: str, value: str, level: str, note: str) -> None:
        items.append({"name": name, "value": value, "level": level, "note": note})
        if level == "error":
            errors.append(f"{name}: {note}")
        elif level == "warn":
            warnings.append(f"{name}: {note}")

    # ---- 文案 Agent：LLM ----
    llm_key = os.getenv("LLM_API_KEY", "")
    llm_model = os.getenv("LLM_MODEL", "")
    llm_base = os.getenv("LLM_BASE_URL", "")
    if _is_placeholder(llm_key):
        item("LLM_API_KEY", "(未配置)", "error", "未配置有效 Key，文案将静默降级为模板文案")
    else:
        item("LLM_API_KEY", f"{llm_key[:8]}…", "ok", "已配置")

    if not llm_model:
        item("LLM_MODEL", "(空)", "error", "未配置模型名")
    elif llm_model in _KNOWN_BAD_MODELS:
        item("LLM_MODEL", llm_model, "error",
             f"已知无效模型名，调用会返回 400 并被静默降级；应改为 {_KNOWN_BAD_MODELS[llm_model]}")
    else:
        item("LLM_MODEL", llm_model, "ok", "格式正常（正确性以服务商文档为准）")

    if llm_base and "console" in llm_base:
        item("LLM_BASE_URL", llm_base, "warn", "疑似填了网页控制台域名，应填 API 域名")
    else:
        item("LLM_BASE_URL", llm_base or "(空)", "ok" if llm_base else "warn", "API 域名")

    # ---- 图文 Agent ----
    img_provider = os.getenv("IMAGE_PROVIDER", "local").strip().lower()
    img_key = os.getenv("IMAGE_API_KEY", "")
    if img_provider == "local":
        item("IMAGE_PROVIDER", "local", "ok", "本地 Playwright 渲染海报，无需第三方 Key")
    elif _is_placeholder(img_key):
        item("IMAGE_API_KEY", "(占位符)", "error",
             f"IMAGE_PROVIDER={img_provider} 但未配置有效 Key，图文生成会失败")
    else:
        item("IMAGE_API_KEY", f"{img_key[:8]}…", "ok", "已配置")

    # ---- 视频 Agent ----
    vid_provider = os.getenv("VIDEO_PROVIDER", "kling").strip().lower()
    vid_key = os.getenv("VIDEO_API_KEY", "")
    if _is_placeholder(vid_key):
        item("VIDEO_API_KEY", "(占位符)", "warn",
             f"VIDEO_PROVIDER={vid_provider} 但未配置有效 Key，视频生成会失败（不影响文案链路）")
    else:
        item("VIDEO_API_KEY", f"{vid_key[:8]}…", "ok", "已配置")

    # ---- 中间件 ----
    milvus = os.getenv("MILVUS_URI") or f"http://{os.getenv('MILVUS_HOST', 'localhost')}:{os.getenv('MILVUS_PORT', '19530')}"
    is_local = "127.0.0.1" in milvus or "localhost" in milvus
    item("MILVUS_URI", milvus, "ok" if is_local else "warn",
         "本地地址" if is_local else "非本地地址，请确认该地址可达（不可达时向量召回会降级）")

    redis_url = os.getenv("REDIS_URL") or f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '16379')}/0"
    item("REDIS_URL", redis_url, "ok", "Celery broker / backend")

    # ---- 风控词表 ----
    try:
        from app.common.banned_words import status as bw_status
        bw = bw_status()
        if bw.get("available"):
            item("违禁词表", f"{bw.get('word_count')} 条", "ok",
                 f"替换建议 {bw.get('replacement_count', 0)} 条")
        else:
            item("违禁词表", "(不可用)", "error",
                 f"风控已 fail-closed，新物料无法登记通过：{bw.get('error')}")
    except Exception as exc:  # noqa: BLE001
        item("违禁词表", "(检查失败)", "error", str(exc))

    # ---- 千川投递模式 ----
    mode = os.getenv("QIANCHUAN_DELIVERY_MODE", "mock").strip().lower()
    item("QIANCHUAN_DELIVERY_MODE", mode, "ok",
         "模拟投递，不产生真实扣费" if mode != "real" else "真实投递，会实际创建广告计划")

    return {"ok": not errors, "errors": errors, "warnings": warnings, "items": items}


def log_startup_report() -> dict[str, Any]:
    """把自检结果打到日志（启动时调用）。"""
    result = collect()
    bar = "=" * 62
    lines = [bar, "启动配置自检"]
    for it in result["items"]:
        mark = {"ok": "✅", "warn": "⚠️ ", "error": "❌"}[it["level"]]
        lines.append(f"  {mark} {it['name']:<24} {it['value']:<28} {it['note']}")
    if result["errors"]:
        lines.append(f"  → {len(result['errors'])} 项错误、{len(result['warnings'])} 项警告，详见 /api/v1/system/config-status")
    else:
        lines.append("  → 配置检查通过")
    lines.append(bar)
    report = "\n".join(lines)
    if result["errors"]:
        logger.error(report)
    elif result["warnings"]:
        logger.warning(report)
    else:
        logger.info(report)
    return result
