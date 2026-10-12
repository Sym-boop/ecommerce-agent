"""
Milvus 客户端封装：连接、插入、检索、删除
供 copywriter.py / pricing.py 等 Agent 共同调用

统一 schema（单一事实来源，init_milvus.py / load_knowledge.py 共用）：
  - collection: knowledge_base
  - vector 字段: vector (dim=1024, IVF_FLAT)
  - 顶层标量字段: platform / category / original_title（供 search 过滤）
  - metadata: JSON 字段（存放其余键）
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
    PLATFORM_FIELD = "platform"
    CATEGORY_FIELD = "category"
    TITLE_FIELD = "original_title"
    DEFAULT_DIMENSION = 1024
    INDEX_TYPE = "IVF_FLAT"
    METRIC_TYPE = "COSINE"
    # 顶层字段之外的键统一收纳进 metadata(JSON)
    _RESERVED = (
        PK_FIELD, VECTOR_FIELD, TEXT_FIELD, META_FIELD,
        PLATFORM_FIELD, CATEGORY_FIELD, TITLE_FIELD,
    )

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
        # 在线向量化服务通常对单次请求的输入条数有限制（如阿里云 text-embedding-v4），
        # 批次大小可经 EMBEDDING_BATCH_SIZE 调整；超限时按 400 自动二分重试。
        batch_size = max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", "10")))
        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            results.extend(self._embed_batch(texts[i : i + batch_size]))
        return results

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """调用一次 embedding；失败且批量 >1 时二分降级重试（应对单请求条数上限）。"""
        try:
            return self._embedding_fn(batch)
        except Exception:
            if len(batch) <= 1:
                raise
            mid = len(batch) // 2
            return self._embed_batch(batch[:mid]) + self._embed_batch(batch[mid:])

    # ------------------------------------------------------------------
    # Collection 管理
    # ------------------------------------------------------------------
    def ensure_collection(self, collection_name: Optional[str] = None) -> None:
        """若 collection 不存在则按统一 schema 创建（含向量索引）。

        schema 为「单一事实来源」，与 scripts/init_milvus.py、load_knowledge.py 共用：
            id(int64, 主键, auto_id) / vector(float_vector, dim) / text(varchar)
            platform / category / original_title(varchar) / metadata(JSON, 动态字段)
        platform 等作为顶层标量字段，保证 search(filters={"platform": ...}) 可过滤。
        """
        name = collection_name or self.COLLECTION
        if self.client.has_collection(name):
            return
        schema = self.client.create_schema(enable_dynamic_field=True)
        schema.add_field(self.PK_FIELD, DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field(self.VECTOR_FIELD, DataType.FLOAT_VECTOR, dim=self._dim)
        schema.add_field(self.TEXT_FIELD, DataType.VARCHAR, max_length=65535)
        schema.add_field(self.PLATFORM_FIELD, DataType.VARCHAR, max_length=64)
        schema.add_field(self.CATEGORY_FIELD, DataType.VARCHAR, max_length=64)
        schema.add_field(self.TITLE_FIELD, DataType.VARCHAR, max_length=512)
        schema.add_field(self.META_FIELD, DataType.JSON)

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name=self.VECTOR_FIELD,
            index_type=self.INDEX_TYPE,
            metric_type=self.METRIC_TYPE,
            params={"nlist": 128},
        )
        self.client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=index_params,
        )

    def has_collection(self, collection_name: Optional[str] = None) -> bool:
        try:
            return bool(self.client.has_collection(collection_name or self.COLLECTION))
        except Exception:  # noqa: BLE001
            return False

    def count(self, collection_name: Optional[str] = None) -> int:
        """返回集合行数（Milvus 不可用时返回内存条数）。"""
        name = collection_name or self.COLLECTION
        try:
            stats = self.client.get_collection_stats(name)
            return int(stats.get("row_count", 0))
        except Exception:  # noqa: BLE001
            return len(self._memory_docs)

    def drop_collection(self, collection_name: Optional[str] = None) -> bool:
        """删除集合（存在才删），返回是否实际删除。"""
        name = collection_name or self.COLLECTION
        try:
            if not self.client.has_collection(name):
                return False
            try:
                self.client.release_collection(name)
            except Exception:  # noqa: BLE001
                pass
            self.client.drop_collection(name)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[milvus_cli] 删除集合失败（{exc}）", file=sys.stderr)
            return False

    def flush(self, collection_name: Optional[str] = None) -> None:
        try:
            self.client.flush(collection_name or self.COLLECTION)
        except Exception:  # noqa: BLE001
            pass

    def describe(self) -> dict[str, Any]:
        """集合运行时信息，供脚本 / 健康检查输出。"""
        return {
            "uri": self._uri,
            "collection": self.COLLECTION,
            "dimension": self._dim,
            "index_type": self.INDEX_TYPE,
            "metric_type": self.METRIC_TYPE,
            "exists": self.has_collection(),
            "row_count": self.count() if self.has_collection() else 0,
            "embedding_ready": self._embedding_fn is not None,
        }

    # ------------------------------------------------------------------
    # 插入
    # ------------------------------------------------------------------
    def insert_data(
        self,
        data: list[dict[str, Any]],
        collection_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """批量插入文档，自动调用 embedding。

        data 每项必须包含 text；可选 platform / category / original_title 会写入同名
        顶层字段（供过滤），其余键统一收纳进 metadata(JSON)。主键由 Milvus 自动生成
        （auto_id），无需调用方指定。
        返回: {"insert_count": int, "collection": str, "mode": "milvus"|"memory"|"empty"}
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
                meta = {k: v for k, v in item.items() if k not in self._RESERVED}
                payload.append({
                    self.VECTOR_FIELD: vectors[i],
                    self.TEXT_FIELD: item[self.TEXT_FIELD],
                    self.PLATFORM_FIELD: str(item.get(self.PLATFORM_FIELD, "")),
                    self.CATEGORY_FIELD: str(item.get(self.CATEGORY_FIELD, "")),
                    self.TITLE_FIELD: str(item.get(self.TITLE_FIELD, "")),
                    self.META_FIELD: meta,
                })

            res = self.client.insert(collection_name=name, data=payload)
            return {"insert_count": res.get("insert_count", 0), "collection": name, "mode": "milvus"}
        except Exception as exc:
            print(f"[milvus_cli] Milvus 写入失败（{exc}），降级到内存模式", file=sys.stderr)
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
            # self.client.load_collection(name)
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
                output_fields=[
                    self.TEXT_FIELD, self.META_FIELD,
                    self.PLATFORM_FIELD, self.CATEGORY_FIELD, self.TITLE_FIELD,
                ],
                timeout=float(os.getenv("MILVUS_TIMEOUT", "5")),
            )
            docs: list[dict[str, Any]] = []
            for hits in results:
                for hit in hits:
                    entity = hit["entity"]
                    meta = entity.get(self.META_FIELD) or {}
                    doc: dict[str, Any] = {
                        "id": hit["id"],
                        "score": hit["distance"],
                        self.TEXT_FIELD: entity.get(self.TEXT_FIELD, ""),
                        self.PLATFORM_FIELD: entity.get(self.PLATFORM_FIELD, ""),
                        self.CATEGORY_FIELD: entity.get(self.CATEGORY_FIELD, ""),
                        self.TITLE_FIELD: entity.get(self.TITLE_FIELD, ""),
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


# 集合管理便捷函数（供脚本 / 健康检查使用）
def get_wrapper() -> MilvusClientWrapper:
    return _get_instance()


def ensure_collection(*args: Any, **kwargs: Any) -> None:
    return _get_instance().ensure_collection(*args, **kwargs)


def has_collection(*args: Any, **kwargs: Any) -> bool:
    return _get_instance().has_collection(*args, **kwargs)


def count(*args: Any, **kwargs: Any) -> int:
    return _get_instance().count(*args, **kwargs)


def drop_collection(*args: Any, **kwargs: Any) -> bool:
    return _get_instance().drop_collection(*args, **kwargs)

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




