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

环境变量（需同步到 .env.example，由成员A维护）
------------------------------------------------
    MILVUS_URI            Milvus 地址，默认 http://localhost:19530
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

MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
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
    now_ms = int(time.time() * 1000)
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

    records 字段: text(必填)、platform、category、product、metrics(dict)。
    """
    return _insert(
        COLLECTION_COPYWRITING, records,
        text_of=lambda r: f"{r.get('product', '')} {r.get('category', '')} {r.get('text', '')}",
        extra_of=lambda r: {
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
        [{"text", "platform", "category", "product", "metrics", "score"}, ...]
    """
    expr_parts = []
    if platform:
        expr_parts.append(f'platform == "{platform}"')
    if category:
        expr_parts.append(f'category == "{category}"')
    return _search(
        COLLECTION_COPYWRITING, query, top_k, " and ".join(expr_parts),
        ["text", "platform", "category", "product", "metrics"],
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
