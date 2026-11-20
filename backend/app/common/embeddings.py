"""Embedding 函数统一入口（三层链路）。

原来这段逻辑写在 `milvus_cli.MilvusClient._load_embedding_fn()` 里，只有向量库能用。
但本地向量检索（`vector_store.py`）也需要同一份 embedding —— 复制一份就会出现
两套模型配置、两套维度假设，最容易出的问题是「索引按 1024 维建的，查询按 1536 维算」。

所以抽到这里，`milvus_cli` 改为委托，保持**单一事实来源**。

三层优先级（都能用时取第一个）：
    1. 任意 OpenAI 兼容的 embedding 接口（`EMBEDDING_API_BASE` + `EMBEDDING_API_KEY`）
       —— 线上推荐：不占本地内存、可水平扩展
    2. OpenAI 官方（`OPENAI_API_KEY`）
    3. 本地 HuggingFace 模型（默认 `BAAI/bge-m3`，1024 维）
       —— 离线/演示可用，代价是首次加载慢、占内存

**失败一律返回 None，不抛异常**：调用方据此降级到 TF-IDF，
而不是让整个检索链路因为「没配 embedding」而报错。
"""
from __future__ import annotations

import functools
import logging
import os
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

EmbedFn = Callable[[list[str]], list[list[float]]]


def _is_real(value: str) -> bool:
    """占位符（空串、***）视为未配置。"""
    value = (value or "").strip()
    return bool(value) and set(value) != {"*"}


def _try_api_embedding() -> Optional[EmbedFn]:
    """OpenAI 兼容的 /embeddings 接口（如阿里云 text-embedding-v4）。"""
    base = os.getenv("EMBEDDING_API_BASE", "").rstrip("/")
    key = os.getenv("EMBEDDING_API_KEY", "").strip()
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
    if not (base and _is_real(key)):
        return None
    try:
        import httpx

        def _embed(texts: list[str]) -> list[list[float]]:
            url = f"{base}/embeddings"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            body = {"model": model, "input": texts, "encoding_format": "float"}
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, headers=headers, json=body)
                resp.raise_for_status()
            items = resp.json().get("data", [])
            if not items:
                raise RuntimeError("embedding 接口返回空")
            return [item["embedding"] for item in items]

        return _embed
    except Exception as exc:  # noqa: BLE001
        logger.info("embedding 接口不可用：%s", exc)
        return None


def _try_openai_embedding() -> Optional[EmbedFn]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    base = os.getenv("OPENAI_BASE_URL", "").rstrip("/")
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    if not _is_real(key):
        return None
    try:
        from openai import OpenAI

        def _embed(texts: list[str]) -> list[list[float]]:
            client = OpenAI(api_key=key, base_url=base or None)
            resp = client.embeddings.create(model=model, input=texts, encoding_format="float")
            return [d.embedding for d in resp.data]

        return _embed
    except Exception as exc:  # noqa: BLE001
        logger.info("OpenAI embedding 不可用：%s", exc)
        return None


def _hf_cache_dir(model_name: str) -> Optional["os.PathLike[str] | str"]:
    """模型是否已在 HF 缓存里（只看目录存在，不联网）。"""
    repo = model_name.replace("/", "--")
    base = Path(os.getenv("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
    candidate = base / "hub" / f"models--{repo}"
    return candidate if candidate.exists() else None


def _network_ok(host: str = "huggingface.co", timeout: float = 1.5) -> bool:
    """快速探测能否连上 Hub。

    为什么要探测：模型已在本地缓存时，`HuggingFaceEmbeddings` 仍会先尝试访问
    Hub。在断网/被墙的机器上，这一步要等 TCP 超时（实测 WinError 10060），
    白白多花几十秒，最后还是走缓存。所以先探一下，连不上就直接进离线模式。
    """
    import socket

    try:
        socket.create_connection((host, 443), timeout=timeout).close()
        return True
    except Exception:  # noqa: BLE001
        return False


def _try_local_embedding() -> Optional[EmbedFn]:
    """本地 bge-m3（1024 维）。首次加载约 2GB 模型，慢但离线可用。

    网络不可达时自动切离线模式（用本地缓存），避免每次加载都等 TCP 超时。
    """
    model_name = os.getenv("BGE_MODEL_NAME", "BAAI/bge-m3")

    cached = _hf_cache_dir(model_name)
    if cached and not os.getenv("HF_HUB_OFFLINE") and not _network_ok():
        # 有缓存 + 连不上 Hub → 直接离线，别浪费时间重试
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        logger.warning("HF Hub 不可达，改用本地缓存以离线模式加载 %s", model_name)

    try:
        from langchain_huggingface import HuggingFaceEmbeddings

        hf = HuggingFaceEmbeddings(model_name=model_name)

        def _embed(texts: list[str]) -> list[list[float]]:
            return hf.embed_documents(texts)

        return _embed
    except Exception as exc:  # noqa: BLE001
        # 用 warning 而不是 info：这类失败会**静默降级**到 TF-IDF，
        # 日志级别太低就变成「以为在用向量，其实在用字面匹配」（踩过）
        logger.warning("本地 embedding（%s）不可用：%s", model_name, exc)
        return None


@functools.lru_cache(maxsize=1)
def get_embed_fn() -> Optional[EmbedFn]:
    """取可用的 embedding 函数；都不可用返回 None。

    做缓存：每次检索都重新加载一次 bge-m3 是不可接受的（首次约 30 秒）。
    缓存也意味着**运行期改 .env 需要重启进程**才生效——这与「支持热更新」的
    其它配置不同，是有意的取舍：模型加载太贵，不能每次调用都探。
    """
    for factory in (_try_api_embedding, _try_openai_embedding, _try_local_embedding):
        fn = factory()
        if fn is not None:
            logger.info("embedding 已就绪：%s", factory.__name__)
            return fn
    logger.warning("没有可用的 embedding 实现，向量检索将降级")
    return None


def describe() -> dict:
    """当前 embedding 配置概览（给自检与状态接口用，不加载模型）。"""
    base = os.getenv("EMBEDDING_API_BASE", "").rstrip("/")
    key = os.getenv("EMBEDDING_API_KEY", "")
    if base and _is_real(key):
        source, model = "api", os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
    elif _is_real(os.getenv("OPENAI_API_KEY", "")):
        source, model = "openai", os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    else:
        source, model = "local", os.getenv("BGE_MODEL_NAME", "BAAI/bge-m3")
    return {
        "source": source,
        "model": model,
        "loaded": get_embed_fn.cache_info().currsize > 0,
    }
