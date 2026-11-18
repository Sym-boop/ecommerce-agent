"""Reranker：用 Cross-Encoder 对召回的候选做精排。

**为什么需要它**

向量召回（bi-encoder）是「问题与文档分别编码再算余弦」，快但粗——
它把语义压成一个点，丢掉了 query 与 doc 之间的细粒度交互。

Cross-Encoder 把 `(query, doc)` **拼在一起过一遍模型**，直接输出相关性分数，
精度明显更高，代价是慢（每个候选一次前向）。

所以标准做法是**两段式**：向量召回拿宽候选 → Cross-Encoder 精排取窄结果。
本项目召回从 `max(top_k×3, 9)` 条里挑，正好适合精排。

**模型**

`BAAI/bge-reranker-base`（~1.06 GB，CPU 可跑）。
从 **modelscope** 下载——HuggingFace 在本网络环境被墙（实测 403 / 连接重置），
modelscope 可用。下载脚本见 `scripts/download_reranker.py`。

**为什么必须 fail-open**

精排是**增强**，不是必需。模型文件缺失、加载失败、推理异常时，
一律退化为「保持召回顺序」而不是让检索失败——少一层精排只是效果差一点，
但检索挂掉会让整个文案生成不可用。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

_REPO_ROOT = Path(__file__).resolve().parents[3]

# 模型目录：优先环境变量，否则仓库内 models/
_MODEL_DIR = Path(os.getenv("RERANK_MODEL_DIR") or (_REPO_ROOT / "models" / "bge-reranker-base"))

# 开关：默认开启，但模型缺失时自动失效（不会报错）
_ENABLED = os.getenv("RAG_RERANK", "1").strip() not in ("0", "false", "False", "no")

# 单次精排最多处理多少条候选（防止候选过多时 CPU 推理拖慢请求）
_MAX_CANDIDATES = int(os.getenv("RERANK_MAX_CANDIDATES", "20"))

_model: Optional[Any] = None
_load_tried = False
_load_error: Optional[str] = None


def model_dir() -> Path:
    return _MODEL_DIR


def available() -> bool:
    """模型是否就绪（只检查文件，不加载）。"""
    if not _ENABLED:
        return False
    need = ["config.json", "model.safetensors", "tokenizer.json"]
    return all((_MODEL_DIR / f).exists() for f in need)


def _load() -> Any:
    """惰性加载 CrossEncoder，只尝试一次。"""
    global _model, _load_tried, _load_error
    if _model is not None:
        return _model
    if _load_tried:
        return None
    _load_tried = True

    if not _ENABLED:
        _load_error = "RAG_RERANK 已关闭"
        return None
    if not available():
        _load_error = f"模型文件不全：{_MODEL_DIR}"
        logger.warning("Reranker 不可用（%s），检索将保持召回顺序", _load_error)
        return None

    try:
        from sentence_transformers import CrossEncoder  # type: ignore

        _model = CrossEncoder(str(_MODEL_DIR), max_length=512, device="cpu")
        logger.info("Reranker 已加载：%s", _MODEL_DIR.name)
        return _model
    except Exception as exc:  # noqa: BLE001
        _load_error = f"{type(exc).__name__}: {exc}"
        logger.warning("Reranker 加载失败（%s），检索将保持召回顺序", _load_error)
        return None


def status() -> dict[str, Any]:
    """状态，供 /health 展示。"""
    return {
        "enabled": _ENABLED,
        "model_dir": str(_MODEL_DIR),
        "available": available(),
        "loaded": _model is not None,
        "error": _load_error,
    }


def rerank(
    query: str,
    docs: list[dict[str, Any]],
    top_k: int = 3,
    text_field: str = "text",
) -> dict[str, Any]:
    """对候选精排并截断。

    返回 `{"docs": [...], "reranked": bool, "reason": str}`：
    - `docs` 按精排分降序（失败时保持原顺序）
    - `reranked` 表示这次**是否真的精排过**（不是"配置开了"就算真）

    每个返回的 doc 会带上 `rerank_score`（真的精排时）便于排查。
    """
    if not docs:
        return {"docs": [], "reranked": False, "reason": "无候选"}

    model = _load()
    if model is None:
        return {"docs": docs[:top_k], "reranked": False,
                "reason": _load_error or "模型未就绪"}

    candidates = docs[:_MAX_CANDIDATES]
    pairs = [(query, str(d.get(text_field) or "")) for d in candidates]

    try:
        scores = model.predict(pairs, batch_size=8, show_progress_bar=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reranker 推理失败（%s），保持召回顺序", exc)
        return {"docs": docs[:top_k], "reranked": False,
                "reason": f"推理失败: {type(exc).__name__}"}

    scored = []
    for doc, sc in zip(candidates, scores):
        item = dict(doc)
        item["rerank_score"] = round(float(sc), 6)
        scored.append(item)

    # 稳定排序：同分时保持召回顺序（sorted 是稳定的）
    scored.sort(key=lambda d: d["rerank_score"], reverse=True)
    return {"docs": scored[:top_k], "reranked": True,
            "reason": f"精排 {len(candidates)} 条 → 取前 {min(top_k, len(scored))}"}
