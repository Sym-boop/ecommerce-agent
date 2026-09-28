"""
Milvus 客户端封装：连接、插入、检索、删除
供 copywriter.py / pricing.py 等 Agent 共同调用

与 B 的 init_milvus.py 对齐：
  - collection: knowledge_base
  - vector 字段: vector (dim=1024)
  - metadata: JSON 字段（存放 platform / category / original_title 等）
  - 度量: COSINE
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# 项目根目录
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=True)

# pymilvus 为可选依赖：未安装或无 Milvus 服务时自动降级为内存模式（仅用于开发调试）。
# 顶层不直接 import，避免缺包时整个模块无法导入。
try:
    from pymilvus import MilvusClient, DataType  # type: ignore
    _PYMILVUS_AVAILABLE = True
except ImportError:
    MilvusClient = None  # type: ignore
    DataType = None  # type: ignore
    _PYMILVUS_AVAILABLE = False


class MilvusClientWrapper:
    """
    Milvus 客户端封装，提供 insert / search / delete 接口。
    本机无 Milvus 服务时自动降级为内存模式（仅用于开发调试）。
    """

    COLLECTION = "knowledge_base"
    VECTOR_FIELD = "vector"
    PK_FIELD = "id"
    TEXT_FIELD = "text"
    META_FIELD = "metadata"
    DEFAULT_DIMENSION = 1024  # 与 B 的 init_milvus.py 保持一致

    def __init__(
            self,
            uri: Optional[str] = None,
            token: Optional[str] = None,
            dimension: int = DEFAULT_DIMENSION,
    ):
        # 优先 uri 参数 → 其次 MILVUS_URI → 再次 MILVUS_HOST/PORT → 最后 localhost
        if uri:
            resolved_uri = uri
        elif os.getenv("MILVUS_URI"):
            resolved_uri = os.getenv("MILVUS_URI")
        else:
            host = os.getenv("MILVUS_HOST", "localhost")
            port = os.getenv("MILVUS_PORT", "19530")
            resolved_uri = f"http://{host}:{port}"

        self._uri = resolved_uri
        self._token = token or os.getenv("MILVUS_TOKEN", "")
        self._dim = dimension
        self._client: Optional[MilvusClient] = None
        self._embedding_fn = self._load_embedding_fn()
        self._memory_docs: list[dict[str, Any]] = []
        self._memory_available = False

    # ------------------------------------------------------------------
    # 属性访问
    # ------------------------------------------------------------------
    @property
    def client(self) -> "MilvusClient":
        if not _PYMILVUS_AVAILABLE:
            raise RuntimeError("未安装 pymilvus（pip install pymilvus），Milvus 功能不可用，将使用内存模式")
        if self._client is None:
            kwargs: dict[str, Any] = {
                "uri": self._uri,
                # RPC 连接/请求超时（秒）：Milvus 不可达时快速失败，
                # 让调用方降级到内存模式，避免终端长时间无响应地卡在 gRPC 重试上
                "timeout": float(os.getenv("MILVUS_TIMEOUT", "5")),
            }
            if self._token:
                kwargs["token"] = self._token
            self._client = MilvusClient(**kwargs)
        return self._client

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    def _load_embedding_fn(self):
        # 1. 通用 OpenAI 兼容 embedding（优先）
        emb_base = os.getenv("EMBEDDING_API_BASE", "").rstrip("/")
        emb_key = os.getenv("EMBEDDING_API_KEY", "").strip()
        emb_model = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
        if emb_base and emb_key and not emb_key.startswith("sk-xxxx"):
            try:
                import httpx

                def _api_embed(texts: list[str]) -> list[list[float]]:
                    url = f"{emb_base}/embeddings"
                    headers = {
                        "Authorization": f"Bearer {emb_key}",
                        "Content-Type": "application/json",
                    }
                    body = {"model": emb_model, "input": texts, "encoding_format": "float"}
                    with httpx.Client(timeout=30.0) as c:
                        resp = c.post(url, headers=headers, json=body)
                        resp.raise_for_status()
                    data = resp.json()
                    items = data.get("data", [])
                    if not items:
                        raise RuntimeError("embedding API 返回空")
                    return [item["embedding"] for item in items]

                return _api_embed
            except Exception:
                pass

        # 2. OpenAI 官方
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        openai_base = os.getenv("OPENAI_BASE_URL", "").rstrip("/")
        if openai_key and not openai_key.startswith("sk-xxxx"):
            try:
                from openai import OpenAI

                def _openai_embed(texts: list[str]) -> list[list[float]]:
                    client = OpenAI(api_key=openai_key, base_url=openai_base or None)
                    resp = client.embeddings.create(
                        model=emb_model,
                        input=texts,
                        encoding_format="float",
                    )
                    return [d.embedding for d in resp.data]

                return _openai_embed
            except Exception:
                pass

        # 3. HuggingFace 本地
        try:
            from langchain_huggingface import HuggingFaceEmbeddings

            model_name = os.getenv("BGE_MODEL_NAME", "BAAI/bge-m3")
            hf_embed = HuggingFaceEmbeddings(model_name=model_name)

            def _bge_embed(texts: list[str]) -> list[list[float]]:
                return hf_embed.embed_documents(texts)

            return _bge_embed
        except Exception:
            pass

        return None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedding_fn is None:
            raise RuntimeError(
                "未配置可用 embedding 函数，请在 .env 中设置 OPENAI_API_KEY，"
                "或安装 langchain-huggingface"
            )
        batch_size = 100
        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            results.extend(self._embedding_fn(batch))
        return results

    # ------------------------------------------------------------------
    # Collection 管理
    # ------------------------------------------------------------------
    def ensure_collection(self, collection_name: Optional[str] = None) -> None:
        """若 collection 不存在则创建（含向量索引）。"""
        name = collection_name or self.COLLECTION
        if self.client.has_collection(name):
            return
        schema = self.client.create_schema(enable_dynamic_field=True)
        schema.add_field(self.PK_FIELD, DataType.INT64, is_primary=True)
        schema.add_field(self.VECTOR_FIELD, DataType.FLOAT_VECTOR, dim=self._dim)
        schema.add_field(self.TEXT_FIELD, DataType.VARCHAR, max_length=65535)

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name=self.VECTOR_FIELD,
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 128},
        )
        self.client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=index_params,
        )

    # ------------------------------------------------------------------
    # 插入
    # ------------------------------------------------------------------
    def insert_data(
        self,
        data: list[dict[str, Any]],
        collection_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """批量插入文档，自动调用 embedding。

        data 每项应包含 text 字段，其余作为 metadata JSON 存入。
        返回: {"insert_count": int, "collection": str, "mode": "milvus"|"memory"}
        """
        if not data:
            return {"insert_count": 0, "collection": self.COLLECTION, "mode": "empty"}

        name = collection_name or self.COLLECTION

        # 尝试 Milvus；连接失败则降级到内存模式
        try:
            self.ensure_collection(name)
            texts = [item[self.TEXT_FIELD] for item in data]
            vectors = self.embed(texts)

            payload: list[dict[str, Any]] = []
            for i, item in enumerate(data):
                meta = {k: v for k, v in item.items() if k not in (self.TEXT_FIELD, self.VECTOR_FIELD)}
                payload.append({
                    self.PK_FIELD: i,
                    self.VECTOR_FIELD: vectors[i],
                    self.TEXT_FIELD: item[self.TEXT_FIELD],
                    self.META_FIELD: meta,
                })

            res = self.client.insert(collection_name=name, data=payload)
            return {"insert_count": res.get("insert_count", 0), "collection": name, "mode": "milvus"}
        except Exception as exc:
            print(f"[milvus_cli] Milvus 连接失败（{exc}），降级到内存模式", file=sys.stderr)
            return self._insert_memory(data)

    def _insert_memory(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        """内存 fallback：仅落盘到内存列表；相似度在 _search_memory 中现算（TF-IDF 不可用时退化为顺序返回）。"""
        for item in data:
            self._memory_docs.append(item)
        return {"insert_count": len(data), "collection": self.COLLECTION, "mode": "memory"}

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def search(
        self,
        query_text: str,
        top_k: int = 5,
        collection_name: Optional[str] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """基于 query_text 检索 Top-K 文档。
        返回: [{"id": int, "text": str, "platform": str, "category": str, "score": float, ...}]
        """
        name = collection_name or self.COLLECTION

        # 先尝试 Milvus 真实检索
        try:
            if not self.client.has_collection(name):
                raise RuntimeError("collection 不存在")
            self.client.load_collection(name)
            query_vec = self.embed([query_text])[0]
            expr = ""
            if filters:
                conditions = []
                for k, v in filters.items():
                    if isinstance(v, list):
                        conditions.append(f'{k} in {json.dumps(v)}')
                    else:
                        conditions.append(f'{k} == "{v}"')
                expr = " and ".join(conditions)

            search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
            results = self.client.search(
                collection_name=name,
                data=[query_vec],
                limit=top_k,
                search_params=search_params,
                filter=expr,
                output_fields=[self.TEXT_FIELD, self.META_FIELD],
            )
            docs: list[dict[str, Any]] = []
            for hits in results:
                for hit in hits:
                    meta = hit["entity"].get(self.META_FIELD, {})
                    doc: dict[str, Any] = {
                        "id": hit["id"],
                        "score": hit["distance"],
                        self.TEXT_FIELD: hit["entity"].get(self.TEXT_FIELD, ""),
                        **meta,
                    }
                    docs.append(doc)
            return docs
        except Exception as exc:
            print(f"[milvus_cli] Milvus 检索失败（{exc}），降级到内存模式", file=sys.stderr)
            return self._search_memory(query_text, top_k=top_k, filters=filters)

    def _search_memory(
        self,
        query_text: str,
        top_k: int = 5,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """内存 fallback 检索：TF-IDF + cosine similarity。"""
        if not self._memory_docs:
            return []

        # 过滤
        docs = self._memory_docs
        if filters:
            filtered = []
            for d in docs:
                match = True
                for k, v in filters.items():
                    dv = d.get(k, "")
                    if isinstance(v, list):
                        if dv not in v:
                            match = False
                            break
                    elif str(dv) != str(v):
                        match = False
                        break
                if match:
                    filtered.append(d)
            docs = filtered

        if not docs:
            return []

        # TF-IDF 检索
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
            from sklearn.metrics.pairwise import cosine_similarity  # type: ignore

            all_texts = [d.get(self.TEXT_FIELD, "") for d in docs] + [query_text]
            tfidf = TfidfVectorizer(stop_words=None)
            matrix = tfidf.fit_transform(all_texts)
            query_idx = len(all_texts) - 1
            sims = cosine_similarity(matrix[query_idx:query_idx+1], matrix[:query_idx]).flatten()

            ranked = sorted(
                enumerate(docs),
                key=lambda x: sims[x[0]],
                reverse=True,
            )
            return [
                {**doc, "score": float(sims[i])}
                for i, doc in ranked[:top_k]
            ]
        except Exception:
            # 最终兜底：返回前 top_k 条
            return [{"score": 0.0, **doc} for doc in docs[:top_k]]

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------
    def delete(
        self,
        ids: list[int],
        collection_name: Optional[str] = None,
    ) -> dict[str, Any]:
        name = collection_name or self.COLLECTION
        if not ids:
            return {"delete_count": 0}
        expr = f"{self.PK_FIELD} in {ids}"
        try:
            res = self.client.delete(collection_name=name, filter=expr)
            self.client.flush(name)
            return {"delete_count": res.get("delete_count", 0)}
        except Exception:
            # 内存模式：按 id 过滤
            before = len(self._memory_docs)
            self._memory_docs = [d for i, d in enumerate(self._memory_docs) if i not in ids]
            return {"delete_count": before - len(self._memory_docs)}

    # ------------------------------------------------------------------
    # 模块级便捷函数
    # ------------------------------------------------------------------
    def search_docs(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return self.search(*args, **kwargs)


# 模块级单例
_instance: Optional[MilvusClientWrapper] = None


def _get_instance() -> MilvusClientWrapper:
    global _instance
    if _instance is None:
        _instance = MilvusClientWrapper()
    return _instance


# 模块级兼容函数（旧调用方 milvus_cli.search(...) 等）
def search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return _get_instance().search(*args, **kwargs)


def insert_data(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _get_instance().insert_data(*args, **kwargs)


def delete(*args: Any, **kwargs: Any) -> dict[str, Any]:

    return _get_instance().delete(*args, **kwargs)

# ============================================================
# 模块级函数：供 main.py / task_engine.py 使用
# ============================================================

def get_client() -> MilvusClient:
    """返回底层 MilvusClient（供 main.py 健康检查用）。"""
    return _get_instance().client


def close_client() -> None:
    """关闭客户端（供 main.py lifespan 退出时调用）。"""
    global _instance
    if _instance is not None and _instance._client is not None:
        try:
            _instance._client.close()
        except Exception:
            pass
        _instance._client = None
        _instance = None


def check_banned_words(text: str) -> list[dict]:
    """违禁词扫描（供 task_engine 的 _safe_check_banned 调用）。

    从本地文件 data/raw/forbidden_words.txt 加载，命中即返回。
    """
    fp = _ROOT / "data" / "raw" / "forbidden_words.txt"
    if not fp.exists():
        return []
    hits: list[dict] = []
    with fp.open("r", encoding="utf-8") as f:
        for line in f:
            word = line.strip()
            if word and word in text:
                hits.append({
                    "word": word,
                    "category": "违禁词",
                    "replacement": "",
                })
    return hits




