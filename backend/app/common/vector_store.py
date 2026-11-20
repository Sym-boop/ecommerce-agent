"""本地向量索引：不需要 Milvus 也能做真正的向量检索。

**为什么需要它**

项目的检索原来只有两条路：
    1. Milvus（`milvus_cli`）—— 需要 19530 端口，演示机上连不通
    2. TF-IDF 内存检索 —— 字面匹配，不是语义

于是所有实测数字（P@3 66.67%）其实都来自 **TF-IDF 降级通道**。这不算撒谎，
但经不起一句「你的向量检索真的在跑吗」。而 Milvus 在 Windows 上没有
`milvus-lite`（官方不支持），本机又没装 Docker —— 换句话说，**演示环境永远
看不到向量召回**。

这个模块把向量检索的最后一公里补上：embedding 用 `embeddings.get_embed_fn()`
（默认本地 bge-m3，1024 维），相似度用 numpy 余弦，索引持久化成 `.npz`。
87 条语料的规模下，numpy 全量点积是微秒级的，不需要 ANN。

**它不是什么**：不是 Milvus 的替代品。数据量上万后应该换掉，所以我留了
`VectorBackend` 这个接口位置，`MilvusBackend` 是生产选项（未实现，见 `TODO`）。

**持久化格式**

    data/vector_index/
        index.npz    # vectors: (n, d) float32 + ids: (n,) str
        meta.json    # 指纹、模型名、维度、文档元信息

指纹由「文档内容 + 模型名」算出，变了就重建——避免出现「模型换了但索引还是旧的」
这种静默错配（相似度会莫名其妙地差，但不会报错）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DIR = _ROOT / "data" / "vector_index"

# 文本字段：语料里标题和正文是分开的，embedding 时拼起来更能表达语义
_TEXT_FIELDS = ("title", "original_title", "content", "text")

_LOCK = threading.RLock()
_STORE: "LocalVectorIndex | None" = None


def _doc_text(doc: dict) -> str:
    """把一条语料拼成用于 embedding 的文本。

    只用标题会丢正文语义；只用正文会丢标题里的核心词。两者拼起来，
    标题放前面（很多 embedding 模型对前段更敏感）。
    """
    parts: list[str] = []
    for field in _TEXT_FIELDS:
        value = doc.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        if len(parts) >= 2:      # 标题 + 正文两段足够，避免拼太长
            break
    return "\n".join(parts)


def _fingerprint(texts: list[str], model: str) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    for t in texts:
        h.update(b"\x00")
        h.update(t.encode("utf-8"))
    return h.hexdigest()[:32]


class LocalVectorIndex:
    """内存 + 磁盘持久化的本地向量索引（余弦相似度）。"""

    def __init__(self, docs: list[dict], index_dir: Path | None = None,
                 model: str = ""):
        self._dir = Path(index_dir or _DEFAULT_DIR)
        self._docs = list(docs)
        self._model = model
        self._vectors = None            # np.ndarray (n, d) float32，单位化后存
        self._index_of: dict[str, int] = {}

    # ---------------- 构建 / 加载 ----------------
    def build(self, force: bool = False) -> dict:
        """构建（或从缓存加载）索引，返回构建信息。

        缓存命中条件：指纹一致。指纹含模型名，所以换模型必然重建。
        """
        import numpy as np

        from app.common.embeddings import get_embed_fn

        texts = [_doc_text(d) for d in self._docs]
        model = self._model or os.getenv("BGE_MODEL_NAME", "BAAI/bge-m3")
        fp = _fingerprint(texts, model)
        info: dict[str, Any] = {"docs": len(texts), "model": model, "fingerprint": fp}

        if not force:
            cached = self._load_cache(fp)
            if cached is not None:
                info.update({"built": False, "cached": True, "dim": int(self._vectors.shape[1])})
                return info

        embed = get_embed_fn()
        if embed is None:
            raise RuntimeError("没有可用的 embedding 实现，无法构建向量索引")

        vectors = np.asarray(embed(texts), dtype="float32")
        if vectors.ndim != 2 or vectors.shape[0] != len(texts):
            raise RuntimeError(f"embedding 维度异常：{vectors.shape}")

        # 预先单位化：余弦相似度退化为点积，检索时少一次除法
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._vectors = vectors / norms
        self._index_of = {str(d.get("id") or i): i for i, d in enumerate(self._docs)}

        self._save_cache(fp)
        info.update({"built": True, "cached": False, "dim": int(vectors.shape[1])})
        logger.info("本地向量索引已构建：%s 条 / %s 维 / 模型 %s",
                    len(texts), vectors.shape[1], model)
        return info

    def _cache_files(self) -> tuple[Path, Path]:
        return self._dir / "index.npz", self._dir / "meta.json"

    def _load_cache(self, fp: str) -> Optional[Any]:
        import numpy as np

        npz, meta = self._cache_files()
        if not (npz.exists() and meta.exists()):
            return None
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            if info.get("fingerprint") != fp:
                return None
            data = np.load(npz, allow_pickle=False)
            vectors = data["vectors"].astype("float32")
            if vectors.shape[0] != len(self._docs):
                return None
            self._vectors = vectors
            self._index_of = {str(d.get("id") or i): i for i, d in enumerate(self._docs)}
            return vectors
        except Exception as exc:  # noqa: BLE001
            logger.warning("向量索引缓存损坏，将重建：%s", exc)
            return None

    def _save_cache(self, fp: str) -> None:
        import numpy as np

        npz, meta = self._cache_files()
        self._dir.mkdir(parents=True, exist_ok=True)
        # 注意：np.savez_compressed 会在文件名后面**自动补 .npz**（若没有该后缀），
        # 所以临时文件必须以 .npz 结尾，否则会写出 index.tmp.npz、replace 时找不到
        # 目标文件（踩过：FileNotFoundError WinError 2）。
        tmp = self._dir / "index.tmp.npz"
        np.savez_compressed(tmp, vectors=self._vectors)
        tmp.replace(npz)
        meta.write_text(json.dumps({
            "fingerprint": fp,
            "model": self._model,
            "dim": int(self._vectors.shape[1]),
            "docs": len(self._docs),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------------- 查询 ----------------
    @property
    def size(self) -> int:
        return len(self._docs)

    @property
    def dim(self) -> int:
        return int(self._vectors.shape[1]) if self._vectors is not None else 0

    def search(self, query: str, top_k: int = 3,
               platform: str = "") -> list[dict]:
        """语义检索；返回带 `score`（余弦，0~1 区间）的文档副本。

        `platform` 只做**加权**不做硬过滤：硬过滤在稀疏语料下会把召回打空
        （84 个「类目×平台」组合里 81 个只有 1 条文档）。同平台加一个小分，
        与 `rerank.py` 的平台加成保持同一口径。
        """
        import numpy as np

        from app.common.embeddings import get_embed_fn

        if self._vectors is None:
            raise RuntimeError("索引尚未构建，先调用 build()")
        q = (query or "").strip()
        if not q:
            return []

        embed = get_embed_fn()
        if embed is None:
            raise RuntimeError("没有可用的 embedding 实现")

        vec = np.asarray(embed([q])[0], dtype="float32")
        norm = float(np.linalg.norm(vec)) or 1.0
        vec = vec / norm

        scores = self._vectors @ vec          # 已单位化，点积即余弦
        bonus = float(os.getenv("VECTOR_PLATFORM_BONUS", "0.03"))
        plat = (platform or "").strip()
        if plat and bonus:
            adj = np.array([
                bonus if str(d.get("platform") or "").strip() == plat else 0.0
                for d in self._docs
            ], dtype="float32")
            scores = scores + adj

        k = max(1, min(int(top_k), len(scores)))
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx])]

        out: list[dict] = []
        for i in idx:
            doc = dict(self._docs[int(i)])
            doc["score"] = round(float(scores[int(i)]), 6)
            out.append(doc)
        return out


def get_local_index(docs: list[dict], force_rebuild: bool = False) -> LocalVectorIndex:
    """取单例索引（进程内），文档集变化时重建。

    单例的理由：构建索引要跑一次 embedding（87 条约几秒，首次含模型加载约 30 秒），
    每个请求重建是灾难。指纹变了会自动重建，所以不必手动失效。
    """
    global _STORE
    with _LOCK:
        if _STORE is None or force_rebuild or _STORE.size != len(docs):
            _STORE = LocalVectorIndex(docs)
            _STORE.build(force=force_rebuild)
        elif _STORE.dim == 0:
            _STORE.build()
        return _STORE


def backend_name() -> str:
    """当前向量后端标识（供 /health 与自检展示）。"""
    return (os.getenv("VECTOR_BACKEND", "milvus") or "milvus").strip().lower()


__all__ = ["LocalVectorIndex", "get_local_index", "backend_name"]
