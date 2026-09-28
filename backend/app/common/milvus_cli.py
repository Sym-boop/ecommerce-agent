
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

from pymilvus import MilvusClient, DataType  # type: ignore


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
        self._uri = uri or os.getenv("MILVUS_URI", "http://192.168.26.129:19530")
        self._token = token or os.getenv("MILVUS_TOKEN", "")
        self._dim = dimension
        self._client: Optional[MilvusClient] = None
        self._embedding_fn = self._load_embedding_fn()
        # 内存 fallback 存储（无 Milvus 服务时使用）
        self._memory_docs: list[dict[str, Any]] = []
        self._memory_available = False

    # ------------------------------------------------------------------
    # 属性访问
    # ------------------------------------------------------------------
    @property
    def client(self) -> MilvusClient:
        if self._client is None:
            kwargs: dict[str, Any] = {"uri": self._uri}
            if self._token:
                kwargs["token"] = self._token
            self._client = MilvusClient(**kwargs)
        return self._client

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    def _load_embedding_fn(self):
        """懒加载 embedding 函数，优先 DeepSeek，fallback 到 OpenAI，再 fallback 到 bge-m3。"""
        # 1. DeepSeek embedding
        ds_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if ds_api_key and not ds_api_key.startswith("sk-xxxx"):
            try:
                import httpx  # noqa: PLC0415

                def _deepseek_embed(texts: list[str]) -> list[list[float]]:
                    url = "https://api.deepseek.com/v1/embeddings"
                    headers = {
                        "Authorization": f"Bearer {ds_api_key}",
                        "Content-Type": "application/json",
                    }
                    body = {
                        "model": "deepseek-embedding",
                        "input": texts,
                        "encoding_format": "float",
                    }
                    with httpx.Client(timeout=30.0) as c:
                        resp = c.post(url, headers=headers, json=body)
                        resp.raise_for_status()
                    data = resp.json()
                    items = data.get("data", [])
                    if not items:
                        raise RuntimeError("DeepSeek embedding 返回空")
                    return [item["embedding"] for item in items]

                return _deepseek_embed
            except Exception:
                pass

        # 2. OpenAI embedding
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        openai_base = os.getenv("OPENAI_BASE_URL", "").rstrip("/")
        if openai_key and not openai_key.startswith("sk-xxxx"):
            try:
                from openai import OpenAI  # type: ignore

                def _openai_embed(texts: list[str]) -> list[list[float]]:
                    client = OpenAI(api_key=openai_key, base_url=openai_base or None)
                    resp = client.embeddings.create(
                        model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
                        input=texts,
                        encoding_format="float",
                    )
                    return [d.embedding for d in resp.data]

                return _openai_embed
            except Exception:
                pass

        # 3. BGE-M3 (本地 / langchain)
        try:
            from langchain_huggingface import HuggingFaceEmbeddings  # type: ignore

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
                "未配置可用 embedding 函数，请在 .env 中设置 DEEPSEEK_API_KEY / OPENAI_API_KEY，"
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
            return {"insert_count": len(res.get("insert_count", 0)), "collection": name, "mode": "milvus"}
        except Exception as exc:
            print(f"[milvus_cli] Milvus 连接失败（{exc}），降级到内存模式", file=sys.stderr)
            return self._insert_memory(data)

    def _insert_memory(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        """内存 fallback：纯文本相似度检索，用于本地开发调试。"""
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
        from sklearn.metrics.pairwise import cosine_similarity  # type: ignore

        for item in data:
            self._memory_docs.append(item)

        if not hasattr(self, "_tfidf"):
            texts = [d.get(self.TEXT_FIELD, "") for d in self._memory_docs]
            self._tfidf = TfidfVectorizer(stop_words=None)
            self._tfidf.fit(texts)

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
                expr=expr,
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



"""Milvus 客户端统一封装（成员B - 通用基础模块）

对 backend/app/agent 下的各 Agent 暴露简洁的向量知识库操作接口，
屏蔽 pymilvus 细节。成员D（copywriter）/ 成员E（pricing）直接调用本模块。

对外接口一览
------------
连接管理:
    get_client()                  获取 Milvus 客户端单例（带重试）
    ensure_collections()          幂等创建全部 Collection 与索引（init_milvus.py 也复用此函数）
    count(collection)             统计集合行数
    delete_by_filter(collection, expr)  按表达式删除

爆款文案（VEC-002，供 D 检索 / load_knowledge.py 灌库）:
    insert_copywriting(records)          批量写入，自动向量化
    search_copywriting(query, ...)       语义检索历史高转化文案

违禁词合规（VEC-003，供 D 生成时过滤）:
    insert_banned_words(records)         批量写入
    load_banned_words(force_reload)      全量加载（本地缓存 5 分钟，支持热更新）
    check_banned_words(text, words=None) 子串扫描，返回违规片段与替换建议

竞品参数（VEC-004，供 E 检索）:
    insert_competitor_params(records)    批量写入
    query_competitor(sku=...)            按 SKU 精确查询
    search_competitor_params(text, ...)  语义检索

向量化（供 insert/search 内部使用，也可单独调用）:
    embed_texts(texts)                   OpenAI 兼容 API；未配置时退化为本地哈希向量（仅供离线联调）

环境变量（与 .env.example 对齐，由成员A维护）
------------------------------------------------
    MILVUS_URI            Milvus 地址（可选，优先级最高）
    MILVUS_HOST/PORT      Milvus 主机与端口，默认 localhost:19530
    MILVUS_TOKEN          Milvus 鉴权 token，可选
    MILVUS_INDEX_TYPE     向量索引类型 HNSW / IVF_FLAT，默认 HNSW
    EMBEDDING_DIM         向量维度，默认 1024（建库后不可更改）
    EMBEDDING_API_BASE    OpenAI 兼容 Embedding 服务地址（如 https://api.xxx.com/v1）
    EMBEDDING_API_KEY     对应 API Key
    EMBEDDING_MODEL       Embedding 模型名，默认 bge-m3
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量与集合 Schema 定义
# ---------------------------------------------------------------------------

COLLECTION_COPYWRITING = "copywriting_samples"   # 爆款文案样本
COLLECTION_BANNED_WORDS = "banned_words"         # 违禁词表
COLLECTION_COMPETITOR = "competitor_params"      # 竞品参数快照

ALL_COLLECTIONS = (COLLECTION_COPYWRITING, COLLECTION_BANNED_WORDS, COLLECTION_COMPETITOR)

def _resolve_milvus_uri() -> str:
    """Milvus 地址解析：优先 MILVUS_URI，其次 .env.example 约定的 MILVUS_HOST/MILVUS_PORT。"""
    uri = os.getenv("MILVUS_URI", "").strip()
    if uri:
        return uri
    host = os.getenv("MILVUS_HOST", "localhost").strip() or "localhost"
    port = os.getenv("MILVUS_PORT", "19530").strip() or "19530"
    return f"http://{host}:{port}"


MILVUS_URI = _resolve_milvus_uri()
MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", "") or None
MILVUS_INDEX_TYPE = os.getenv("MILVUS_INDEX_TYPE", "HNSW").upper()
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

# OpenAI 兼容 Embedding API 配置；两者都配置后才走在线向量化
EMBEDDING_API_BASE = os.getenv("EMBEDDING_API_BASE", "").rstrip("/")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "bge-m3")

_CLIENT: Any = None

_BANNED_CACHE: list[dict] | None = None
_BANNED_CACHE_TS: float = 0.0
_BANNED_CACHE_TTL = 300  # 秒，违禁词库热更新间隔（需求 3.2.3）


def get_client(retries: int = 3, retry_interval: float = 2.0):
    """获取 Milvus 客户端单例，连接失败按间隔重试。

    供全部 insert/search/delete 函数内部使用；Agent 代码一般无需直接调用。
    Raises:
        ConnectionError: 重试耗尽后仍无法连接 Milvus。
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    from pymilvus import MilvusClient

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            _CLIENT = MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN)
            _CLIENT.list_collections()  # 探活
            logger.info("Milvus 连接成功: %s", MILVUS_URI)
            return _CLIENT
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            _CLIENT = None
            logger.warning("Milvus 连接失败（第 %s/%s 次）: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(retry_interval)
    raise ConnectionError(f"无法连接 Milvus（{MILVUS_URI}）: {last_exc}") from last_exc


def close_client() -> None:
    """关闭并释放客户端单例（FastAPI 生命周期结束时调用）。"""
    global _CLIENT
    if _CLIENT is not None:
        try:
            _CLIENT.close()
        except Exception:  # noqa: BLE001
            pass
        _CLIENT = None


# ---------------------------------------------------------------------------
# Collection 管理
# ---------------------------------------------------------------------------

def _build_schema(collection: str):
    from pymilvus import DataType

    schema = _client_module().create_schema(auto_id=True, enable_dynamic_field=True)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM)

    if collection == COLLECTION_COPYWRITING:
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)       # 业务主键，约定 KB-COPY-*（前端联调会议 2026-09-23）
        schema.add_field("text", DataType.VARCHAR, max_length=65535)      # 文案正文
        schema.add_field("platform", DataType.VARCHAR, max_length=32)     # xiaohongshu / tiktok / amazon
        schema.add_field("category", DataType.VARCHAR, max_length=64)     # 商品类目
        schema.add_field("product", DataType.VARCHAR, max_length=256)     # 商品名/关键词
        schema.add_field("metrics", DataType.JSON)                        # 转化率等效果指标
        schema.add_field("create_time", DataType.INT64)                   # 入库时间戳(ms)
    elif collection == COLLECTION_BANNED_WORDS:
        schema.add_field("word", DataType.VARCHAR, max_length=256)        # 违禁词
        schema.add_field("replacement", DataType.VARCHAR, max_length=256) # 建议替换词
        schema.add_field("category", DataType.VARCHAR, max_length=64)     # 违规类型
        schema.add_field("severity", DataType.INT64)                      # 严重级别 1-5
    elif collection == COLLECTION_COMPETITOR:
        schema.add_field("sku", DataType.VARCHAR, max_length=64)          # 本品 SKU
        schema.add_field("competitor", DataType.VARCHAR, max_length=128)  # 竞品标识
        schema.add_field("price", DataType.FLOAT)                         # 竞品价格
        schema.add_field("sales", DataType.INT64)                         # 销量
        schema.add_field("rating", DataType.FLOAT)                        # 评分
        schema.add_field("snapshot_time", DataType.INT64)                 # 快照时间戳(ms)
    else:
        raise ValueError(f"未知的集合: {collection}")
    return schema


def _client_module():
    """延迟导入 pymilvus 顶层模块，便于单元测试 mock。"""
    from pymilvus import MilvusClient
    return MilvusClient


def _build_index_params(index_type: str):
    index_params = _client_module().prepare_index_params()
    if index_type == "HNSW":
        index_params.add_index(
            field_name="vector", index_type="HNSW", metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
    else:  # IVF_FLAT
        index_params.add_index(
            field_name="vector", index_type="IVF_FLAT", metric_type="COSINE",
            params={"nlist": 1024},
        )
    return index_params


def ensure_collections(drop: bool = False, index_type: str | None = None) -> dict[str, str]:
    """幂等创建全部知识库 Collection（Schema + 向量索引）。

    Args:
        drop: 为 True 时先删除同名集合再重建（init_milvus.py --drop）。
        index_type: HNSW / IVF_FLAT，默认取环境变量 MILVUS_INDEX_TYPE。
    Returns:
        {"copywriting_samples": "created" | "exists" | "recreated", ...}
    """
    client = get_client()
    index_type = (index_type or MILVUS_INDEX_TYPE).upper()
    result: dict[str, str] = {}
    for name in ALL_COLLECTIONS:
        exists = client.has_collection(name)
        if exists and drop:
            client.drop_collection(name)
            exists = False
        if not exists:
            client.create_collection(
                collection_name=name,
                schema=_build_schema(name),
                index_params=_build_index_params(index_type),
            )
            result[name] = "recreated" if drop else "created"
        else:
            result[name] = "exists"
    return result


def count(collection: str) -> int:
    """统计集合行数，集合不存在时返回 0。"""
    client = get_client()
    if not client.has_collection(collection):
        return 0
    stats = client.get_collection_stats(collection)
    return int(stats.get("row_count", 0))


def delete_by_filter(collection: str, expr: str) -> None:
    """按布尔表达式删除记录，如 delete_by_filter(COLLECTION_COMPETITOR, 'sku == "A001"')。"""
    get_client().delete(collection_name=collection, filter=expr)


# ---------------------------------------------------------------------------
# 向量化
# ---------------------------------------------------------------------------

def _hash_embed(text: str) -> list[float]:
    """本地哈希向量（无外部 API 时的离线联调降级方案，不具备真实语义）。"""
    vec = [0.0] * EMBEDDING_DIM
    for token in re.findall(r"\w+", text.lower(), flags=re.UNICODE):
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        vec[h % EMBEDDING_DIM] += 1.0 if (h >> 16) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _api_embed(texts: list[str]) -> list[list[float]]:
    """调用 OpenAI 兼容 /embeddings 接口。"""
    import httpx

    resp = httpx.post(
        f"{EMBEDDING_API_BASE}/embeddings",
        headers={"Authorization": f"Bearer {EMBEDDING_API_KEY}"},
        json={"model": EMBEDDING_MODEL, "input": texts},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]


def embed_texts(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """批量文本向量化。

    配置了 EMBEDDING_API_BASE/EMBEDDING_API_KEY 时调用在线 API，
    否则退化为本地哈希向量（仅保证管线可跑通，检索无语义效果）。
    """
    if not texts:
        return []
    use_api = bool(EMBEDDING_API_BASE and EMBEDDING_API_KEY)
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        chunk = [t if t else " " for t in texts[i:i + batch_size]]
        if use_api:
            vectors.extend(_api_embed(chunk))
        else:
            vectors.extend(_hash_embed(t) for t in chunk)
    return vectors


# ---------------------------------------------------------------------------
# 写入（供 load_knowledge.py / 各 Agent 灌库）
# ---------------------------------------------------------------------------

_INSERT_BATCH = 500


def _insert(collection: str, records: list[dict], text_of, extra_of) -> int:
    """通用写入：文本向量化后分批插入，返回成功条数。"""
    if not records:
        return 0
    client = get_client()
    total = 0
    for i in range(0, len(records), _INSERT_BATCH):
        batch = records[i:i + _INSERT_BATCH]
        vectors = embed_texts([text_of(r) for r in batch])
        rows = []
        for record, vector in zip(batch, vectors):
            row = dict(extra_of(record))
            row["vector"] = vector
            rows.append(row)
        client.insert(collection_name=collection, data=rows)
        total += len(rows)
    logger.info("写入 %s 完成: %s 条", collection, total)
    return total


def insert_copywriting(records: list[dict]) -> int:
    """批量写入爆款文案。

    records 字段: text(必填)、platform、category、product、metrics(dict)、
                  id/doc_id(可选，业务主键，约定 KB-COPY-*，未传则留空由 Milvus 自增主键标识)。
    """
    return _insert(
        COLLECTION_COPYWRITING, records,
        text_of=lambda r: f"{r.get('product', '')} {r.get('category', '')} {r.get('text', '')}",
        extra_of=lambda r: {
            "doc_id": str(r.get("doc_id") or r.get("id") or ""),
            "text": r["text"],
            "platform": r.get("platform", ""),
            "category": r.get("category", ""),
            "product": r.get("product", ""),
            "metrics": r.get("metrics") or {},
            "create_time": int(r.get("create_time") or time.time() * 1000),
        },
    )


def insert_banned_words(records: list[dict]) -> int:
    """批量写入违禁词。records 字段: word(必填)、replacement、category、severity(int)。"""
    return _insert(
        COLLECTION_BANNED_WORDS, records,
        text_of=lambda r: r["word"],
        extra_of=lambda r: {
            "word": r["word"],
            "replacement": r.get("replacement", ""),
            "category": r.get("category", ""),
            "severity": int(r.get("severity") or 1),
        },
    )


def insert_competitor_params(records: list[dict]) -> int:
    """批量写入竞品参数。records 字段: sku(必填)、price(必填)、competitor、sales、rating、snapshot_time。"""
    return _insert(
        COLLECTION_COMPETITOR, records,
        text_of=lambda r: f"{r.get('sku', '')} {r.get('competitor', '')} price={r.get('price', '')} rating={r.get('rating', '')}",
        extra_of=lambda r: {
            "sku": r["sku"],
            "competitor": r.get("competitor", ""),
            "price": float(r["price"]),
            "sales": int(r.get("sales") or 0),
            "rating": float(r.get("rating") or 0),
            "snapshot_time": int(r.get("snapshot_time") or time.time() * 1000),
        },
    )


# ---------------------------------------------------------------------------
# 检索（供 D/E 调用）
# ---------------------------------------------------------------------------

def _search(collection: str, query_text: str, top_k: int, filter_expr: str | None, output_fields: list[str]) -> list[dict]:
    results = get_client().search(
        collection_name=collection,
        data=embed_texts([query_text]),
        limit=top_k,
        filter=filter_expr or "",
        output_fields=output_fields,
        search_params={"params": {"ef": 64}} if MILVUS_INDEX_TYPE == "HNSW" else {"params": {"nprobe": 32}},
    )
    hits: list[dict] = []
    for hit in (results[0] if results else []):
        entity = dict(hit.get("entity", {}))
        entity["score"] = hit.get("distance")
        hits.append(entity)
    return hits


def search_copywriting(query: str, top_k: int = 10, platform: str | None = None,
                       category: str | None = None) -> list[dict]:
    """按语义检索历史高转化文案（VEC-002）。

    Args:
        query: 查询文本（如商品卖点描述）。
        top_k: 返回条数。
        platform: 可选，按平台过滤（xiaohongshu/tiktok/amazon）。
        category: 可选，按类目过滤。
    Returns:
        [{"doc_id", "text", "platform", "category", "product", "metrics", "score"}, ...]
    """
    expr_parts = []
    if platform:
        expr_parts.append(f'platform == "{platform}"')
    if category:
        expr_parts.append(f'category == "{category}"')
    return _search(
        COLLECTION_COPYWRITING, query, top_k, " and ".join(expr_parts),
        ["doc_id", "text", "platform", "category", "product", "metrics"],
    )


def query_competitor(sku: str, limit: int = 100) -> list[dict]:
    """按 SKU 精确查询竞品参数快照（VEC-004），按时间倒序。"""
    rows = get_client().query(
        collection_name=COLLECTION_COMPETITOR,
        filter=f'sku == "{sku}"',
        output_fields=["sku", "competitor", "price", "sales", "rating", "snapshot_time"],
        limit=limit,
    )
    return sorted(rows, key=lambda r: r.get("snapshot_time", 0), reverse=True)


def search_competitor_params(query: str, top_k: int = 10) -> list[dict]:
    """按语义检索竞品参数（如按商品描述找相似竞品）。"""
    return _search(
        COLLECTION_COMPETITOR, query, top_k, None,
        ["sku", "competitor", "price", "sales", "rating", "snapshot_time"],
    )


# ---------------------------------------------------------------------------
# 违禁词合规检查（VEC-003）
# ---------------------------------------------------------------------------

def load_banned_words(force_reload: bool = False) -> list[dict]:
    """全量加载违禁词库，本地缓存 5 分钟（支持热更新，无需重启）。"""
    global _BANNED_CACHE, _BANNED_CACHE_TS
    now = time.time()
    if not force_reload and _BANNED_CACHE is not None and now - _BANNED_CACHE_TS < _BANNED_CACHE_TTL:
        return _BANNED_CACHE
    if count(COLLECTION_BANNED_WORDS) == 0:
        _BANNED_CACHE = []
    else:
        _BANNED_CACHE = get_client().query(
            collection_name=COLLECTION_BANNED_WORDS,
            filter='word != ""',
            output_fields=["word", "replacement", "category", "severity"],
            limit=16384,
        )
    _BANNED_CACHE_TS = now
    return _BANNED_CACHE


def check_banned_words(text: str, words: list[dict] | None = None) -> list[dict]:
    """扫描文本中的违禁词，返回违规片段与替换建议（CPY-004）。

    Args:
        text: 待检查文案。
        words: 可选，外部传入违禁词列表；不传则使用 Milvus 中的缓存词库。
    Returns:
        [{"word", "start", "end", "replacement", "category", "severity"}, ...]
    """
    if words is None:
        words = load_banned_words()
    hits: list[dict] = []
    for item in words:
        word = item.get("word", "")
        if not word:
            continue
        start = 0
        while True:
            idx = text.find(word, start)
            if idx < 0:
                break
            hits.append({
                "word": word,
                "start": idx,
                "end": idx + len(word),
                "replacement": item.get("replacement", ""),
                "category": item.get("category", ""),
                "severity": item.get("severity", 1),
            })
            start = idx + len(word)
    return hits

