"""
Milvus 客户端封装：连接、插入、检索、删除

调用方（当前真实链路）：
  - copywriter.py  相似爆款文案召回（RAG）
  - pipeline.py    违禁词检测（check_banned_words）
  - main.py /health 连通性探测
注：pricing.py 虽也设计为调用方，但该 Agent 已弃用（见其文件头说明），不再计入。

统一 schema（单一事实来源，init_milvus.py / load_knowledge.py 共用）：
  - collection: knowledge_base
  - vector 字段: vector (dim=1024, IVF_FLAT)
  - 顶层标量字段: platform / category / original_title（供 search 过滤）
  - metadata: JSON 字段（存放其余键）
  - 度量: COSINE
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

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
        # embedding 惰性加载：构造时不加载模型（见 embed()）。
        # 走 TF-IDF 降级通道时不需要 embedding，避免无谓的初始化开销。
        self._embedding_fn = None
        self._memory_docs: list[dict[str, Any]] = []
        self._memory_available = False
        # 熔断器：Milvus 不可达时，冷却期内直接走内存降级，不再反复重连。
        # 否则每次 search 都要等一次连接超时（实测 180 次查询跑了 15 分钟）。
        self._breaker_until: float = 0.0
        self._breaker_hits: int = 0

    # ------------------------------------------------------------------
    # 熔断器
    # ------------------------------------------------------------------
    def _breaker_open(self) -> bool:
        import time as _time
        return _time.monotonic() < self._breaker_until

    def _trip_breaker(self, exc: Exception) -> None:
        import time as _time
        cooldown = float(os.getenv("MILVUS_BREAKER_SECONDS", "60"))
        self._breaker_until = _time.monotonic() + cooldown
        self._breaker_hits += 1
        # 降噪开关：**首次仍用 WARNING**（依赖挂了这件事不能藏起来），
        # 之后的重试降为 INFO。
        # 为什么需要：离线/演示环境里 Milvus 不可达是**预期状态**，而健康检查会
        # 每 10 秒探一次，60 秒熔断到期就再触发一次 WARNING——日志被同一句话刷满，
        # 真正的问题反而看不见。
        quiet = os.getenv("MILVUS_LOG_QUIET", "0").strip() not in ("0", "false", "False", "")
        log_fn = logger.info if (quiet and self._breaker_hits > 1) else logger.warning
        log_fn(
            "Milvus 熔断 %ss（第 %d 次）：%s；期间直接使用内存降级通道",
            int(cooldown), self._breaker_hits, exc,
        )
        # 丢掉已坏的客户端，恢复后重新建连
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    def _reset_breaker(self) -> None:
        if self._breaker_until:
            logger.info("Milvus 已恢复，解除熔断")
        self._breaker_until = 0.0

    def breaker_state(self) -> dict[str, Any]:
        import time as _time
        return {
            "open": self._breaker_open(),
            "remaining_seconds": max(0.0, round(self._breaker_until - _time.monotonic(), 1)),
            "trips": self._breaker_hits,
        }

    def ping(self) -> bool:
        """探测连通性。**必须走熔断器**，否则健康检查会在依赖挂掉时阻塞。

        实测问题：`/health` 原来直接调 `wrapper.client.list_collections()`，
        绕过了超时与熔断。Milvus 不可达时每次要等约 30 秒，
        压测中 20 并发下 /health 全部超时（错误率 97.5%）。
        """
        if self._breaker_open():
            return False
        try:
            self.client.list_collections()
            self._reset_breaker()
            return True
        except Exception as exc:  # noqa: BLE001
            self._trip_breaker(exc)
            return False

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
        """委托给 app.common.embeddings（单一事实来源）。

        原来这里是完整的三层链路实现，与本地向量检索需要的 embedding 重复。
        复制两份的典型问题是「索引按 1024 维建、查询按 1536 维算」——
        维度不一致不会报错，只会让相似度悄悄算错。所以统一到一处。
        """
        from app.common.embeddings import get_embed_fn

        return get_embed_fn()

    def embed(self, texts: list[str]) -> list[list[float]]:
        # 惰性加载：embedding 模型（如 bge-m3）初始化很慢，首次加载在离线环境
        # 可能因为联网重试花掉几分钟。只有在**真的要用向量**时才加载——
        # 走 TF-IDF 降级通道的路径完全不需要它。
        if self._embedding_fn is None:
            self._embedding_fn = self._load_embedding_fn()
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
        # 熔断期内不去探 Milvus（否则每次健康检查都要等一次连接超时）
        if self._breaker_open():
            return False
        try:
            return bool(self.client.has_collection(collection_name or self.COLLECTION))
        except Exception:  # noqa: BLE001
            return False

    def count(self, collection_name: Optional[str] = None) -> int:
        """返回集合行数（Milvus 不可用时返回内存条数）。"""
        name = collection_name or self.COLLECTION
        if self._breaker_open():
            return len(self._memory_docs)
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
        breaker = self.breaker_state()
        has = self.has_collection() if not breaker["open"] else False
        return {
            "uri": self._uri,
            "collection": self.COLLECTION,
            "dimension": self._dim,
            "index_type": self.INDEX_TYPE,
            "metric_type": self.METRIC_TYPE,
            "exists": has,
            "row_count": self.count() if has else len(self._memory_docs),
            "channel": "memory-fallback" if breaker["open"] else "milvus",
            "breaker": breaker,
            # 惰性加载：None 表示"还没用过"，不代表不可用
            "embedding_loaded": self._embedding_fn is not None,
        }

    # ------------------------------------------------------------------
    # 按给定向量读写（图片等非文本模态用）
    # ------------------------------------------------------------------
    def insert_vectors(
        self,
        items: list[dict[str, Any]],
        collection_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """插入**已算好的向量**（不做 embedding）。

        每项需包含 `_vector`（定长 float 列表），其余字段同 `insert_data`。
        图片这类非文本模态没有「文本 embedding」可算，只能自带向量。
        """
        if not items:
            return {"insert_count": 0, "collection": self.COLLECTION, "mode": "empty"}

        name = collection_name or self.COLLECTION
        for it in items:
            vec = it.get("_vector")
            if not isinstance(vec, (list, tuple)) or len(vec) != self._dim:
                raise ValueError(
                    f"向量维度不符：期望 {self._dim}，收到 "
                    f"{len(vec) if isinstance(vec, (list, tuple)) else type(vec).__name__}"
                )

        if self._breaker_open():
            return self._insert_memory(items)

        try:
            self.ensure_collection(name)
            payload: list[dict[str, Any]] = []
            for item in items:
                meta = {k: v for k, v in item.items()
                        if k not in self._RESERVED and k != "_vector"}
                payload.append({
                    self.VECTOR_FIELD: [float(x) for x in item["_vector"]],
                    self.TEXT_FIELD: str(item.get(self.TEXT_FIELD, "")),
                    self.PLATFORM_FIELD: str(item.get(self.PLATFORM_FIELD, "")),
                    self.CATEGORY_FIELD: str(item.get(self.CATEGORY_FIELD, "")),
                    self.TITLE_FIELD: str(item.get(self.TITLE_FIELD, "")),
                    self.META_FIELD: meta,
                })
            res = self.client.insert(collection_name=name, data=payload)
            self._reset_breaker()
            return {"insert_count": res.get("insert_count", 0), "collection": name, "mode": "milvus"}
        except Exception as exc:  # noqa: BLE001
            self._trip_breaker(exc)
            return self._insert_memory(items)

    def search_vector(
        self,
        vector: list[float],
        top_k: int = 5,
        collection_name: Optional[str] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """按给定向量检索 Top-K（图片相似检索用）。"""
        name = collection_name or self.COLLECTION
        if len(vector) != self._dim:
            raise ValueError(f"查询向量维度不符：期望 {self._dim}，收到 {len(vector)}")

        if self._breaker_open():
            return self._search_memory_vector(vector, top_k, filters)

        try:
            if not self.client.has_collection(name):
                raise RuntimeError("collection 不存在")
            expr = ""
            if filters:
                conds = []
                for k, v in filters.items():
                    if isinstance(v, list):
                        conds.append(f'{k} in {json.dumps(v)}')
                    else:
                        conds.append(f'{k} == "{v}"')
                expr = " and ".join(conds)
            search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
            results = self.client.search(
                collection_name=name,
                data=[[float(x) for x in vector]],
                limit=top_k,
                search_params=search_params,
                filter=expr,
                output_fields=[self.TEXT_FIELD, self.META_FIELD,
                               self.PLATFORM_FIELD, self.CATEGORY_FIELD, self.TITLE_FIELD],
                timeout=float(os.getenv("MILVUS_TIMEOUT", "5")),
            )
            docs: list[dict[str, Any]] = []
            for hits in results:
                for hit in hits:
                    entity = hit["entity"]
                    meta = entity.get(self.META_FIELD) or {}
                    docs.append({
                        "id": hit["id"],
                        "score": hit["distance"],
                        self.TEXT_FIELD: entity.get(self.TEXT_FIELD, ""),
                        self.PLATFORM_FIELD: entity.get(self.PLATFORM_FIELD, ""),
                        self.CATEGORY_FIELD: entity.get(self.CATEGORY_FIELD, ""),
                        self.TITLE_FIELD: entity.get(self.TITLE_FIELD, ""),
                        **meta,
                    })
            self._reset_breaker()
            return docs
        except Exception as exc:  # noqa: BLE001
            self._trip_breaker(exc)
            return self._search_memory_vector(vector, top_k, filters)

    def _search_memory_vector(
        self,
        vector: list[float],
        top_k: int,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """内存降级：对带 `_vector` 的文档算余弦相似度。"""
        import math

        docs = [d for d in self._memory_docs if d.get("_vector")]
        if filters:
            filtered = []
            for d in docs:
                if all(str(d.get(k, "")) == str(v) for k, v in filters.items()
                       if not isinstance(v, list)):
                    filtered.append(d)
            docs = filtered or docs
        if not docs:
            return []

        qn = math.sqrt(sum(x * x for x in vector)) or 1.0
        scored = []
        for d in docs:
            v = d["_vector"]
            if len(v) != len(vector):
                continue
            dot = sum(a * b for a, b in zip(vector, v))
            dn = math.sqrt(sum(x * x for x in v)) or 1.0
            scored.append((dot / (qn * dn), d))
        scored.sort(key=lambda x: -x[0])
        return [{**d, "score": round(s, 6)} for s, d in scored[:top_k]]

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

        # 熔断期内直接走内存，避免每次写入都等一次连接超时
        if self._breaker_open():
            return self._insert_memory(data)

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
            self._reset_breaker()
            return {"insert_count": res.get("insert_count", 0), "collection": name, "mode": "milvus"}
        except Exception as exc:
            self._trip_breaker(exc)
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

        # 熔断期内直接走内存检索：避免每次查询都等一次连接超时
        if self._breaker_open():
            return self._search_memory(query_text, top_k=top_k, filters=filters)

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
            self._reset_breaker()
            return docs
        except Exception as exc:
            self._trip_breaker(exc)
            return self._search_memory(query_text, top_k=top_k, filters=filters)

    def seed_memory_from_disk(self) -> int:
        """把本地语料灌进内存，供降级检索使用，返回载入条数。

        **为什么需要这个**：降级检索用的是 `self._memory_docs`，而它只在
        「本进程内调用过 insert_data」时才会有数据。应用启动后若没人写过，
        这一层就是空的 —— 一旦 Milvus 不可达，降级路径直接返回空参考，
        「本地 TF-IDF 降级」名不副实。

        实测发现这个问题后补上：首次走降级时从 processed 语料惰性载入。
        """
        if self._memory_docs:
            return 0
        import json as _json

        candidates = [
            _ROOT / "data" / "processed" / "copy_processed.json",
            _ROOT / "backend" / "data" / "processed" / "copy_processed.json",
        ]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            logger.warning("降级检索：未找到本地语料文件，内存通道为空")
            return 0
        try:
            rows = _json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("降级检索：本地语料读取失败（%s）", exc)
            return 0
        added = 0
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and r.get(self.TEXT_FIELD):
                self._memory_docs.append(dict(r))
                added += 1
        if added:
            logger.info("降级检索：已从 %s 载入 %d 条语料到内存", path.name, added)
        return added

    def _search_memory(
        self,
        query_text: str,
        top_k: int = 5,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """内存 fallback 检索：TF-IDF + cosine similarity。"""
        # 首次降级时惰性载入本地语料，否则这一层是空的
        if not self._memory_docs:
            self.seed_memory_from_disk()
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
            # 相似度阈值过滤：不足 top_k 时**不能拿无关文档补位**。
            # 实测踩过：查「蓝牙耳机」会把「保温杯」以 score=0.0 塞进参考结果，
            # 这类无关参考会污染 Prompt，反而拉低生成质量。
            # 宁可只召回 1 条相关的，也不要 3 条里混 2 条无关的。
            min_score = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.05"))
            return [
                {**doc, "score": float(sims[i])}
                for i, doc in ranked[:top_k]
                if float(sims[i]) > min_score
            ]
        except Exception:
            # 打分失败时返回空列表：不拿无关文档凑数，上游会降级为「暂无参考」
            return []

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
    """违禁词扫描（兼容旧入口，实现见 app.common.banned_words）。

    历史实现是纯子串匹配（`word in text`），有两个已修缺陷：
      - 单字词误报：「最近」「最后」会被词表里的「最」命中；
      - 冗余命中：「最」⊂「最好」，同一条文案重复报多条。

    现改为分词 + 词边界 + 最长优先去重，并回填替换建议。
    """
    from app.common.banned_words import check

    return check(text)




