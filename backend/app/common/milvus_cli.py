"""
Milvus 客户端封装：连接、插入、检索、删除
供 copywriter.py / pricing.py 等 Agent 共同调用
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

from pymilvus import MilvusClient  # type: ignore


class MilvusClientWrapper:
    """Milvus 客户端封装，提供 insert / search / delete 接口。"""

    COLLECTION = "copy_knowledge"
    DEFAULT_DIMENSION = 1536  # text-embedding-3-small / bge-m3 默认维度
    VECTOR_FIELD = "embedding"
    PK_FIELD = "id"
    TEXT_FIELD = "text"
    METADATA_FIELDS = ("platform", "category", "original_title", "original_content", "tags")

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

    # ------------------------------------------------------------------
    # 属性访问（兼容旧调用方 milvus_cli.search(...)）
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

            model_name = os.getenv(
                "BGE_MODEL_NAME",
                "BAAI/bge-m3",
            )
            hf_embed = HuggingFaceEmbeddings(model_name=model_name)

            def _bge_embed(texts: list[str]) -> list[list[float]]:
                return hf_embed.embed_documents(texts)

            return _bge_embed
        except Exception:
            pass

        # 无可用 embedding 函数
        return None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedding_fn is None:
            raise RuntimeError(
                "未配置可用 embedding 函数，请在 .env 中设置 DEEPSEEK_API_KEY / OPENAI_API_KEY，"
                "或安装 langchain-huggingface"
            )
        # DeepSeek / OpenAI 批量上限通常为 100，分段调用
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
        from pymilvus import DataType  # type: ignore

        schema = self.client.create_schema(
            enable_dynamic_field=True,
        )
        schema.add_field(self.PK_FIELD, DataType.INT64, is_primary=True)
        schema.add_field(self.VECTOR_FIELD, DataType.FLOAT_VECTOR, dim=self._dim)
        for f in self.METADATA_FIELDS:
            schema.add_field(f, DataType.VARCHAR, max_length=65535)

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name=self.VECTOR_FIELD,
            index_type="IVF_FLAT",
            metric_type="IP",
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

        data 每项应包含 text 字段，其余作为 metadata 存入。
        返回: {"insert_count": int, "collection": str}
        """
        name = collection_name or self.COLLECTION
        self.ensure_collection(name)

        texts = [item[self.TEXT_FIELD] for item in data]
        vectors = self.embed(texts)

        payload: list[dict[str, Any]] = []
        for i, item in enumerate(data):
            row: dict[str, Any] = {
                self.PK_FIELD: i,
                self.VECTOR_FIELD: vectors[i],
                self.TEXT_FIELD: item[self.TEXT_FIELD],
            }
            for f in self.METADATA_FIELDS:
                if f in item:
                    row[f] = item[f]
            payload.append(row)

        res = self.client.insert(collection_name=name, data=payload)
        return {"insert_count": len(res.get("insert_count", 0)), "collection": name}

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
        """基于 query_text 语义检索 Top-K 文档。

        返回: [{"id": int, "text": str, "platform": str, "category": str, "score": float, ...}]
        """
        name = collection_name or self.COLLECTION
        if not self.client.has_collection(name):
            return []

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

        search_params = {
            "metric_type": "IP",
            "params": {"nprobe": 16},
        }

        results = self.client.search(
            collection_name=name,
            data=[query_vec],
            limit=top_k,
            search_params=search_params,
            expr=expr,
            output_fields=[self.TEXT_FIELD] + list(self.METADATA_FIELDS),
        )

        docs: list[dict[str, Any]] = []
        for hits in results:
            for hit in hits:
                doc: dict[str, Any] = {"id": hit["id"], "score": hit["distance"]}
                for f in [self.TEXT_FIELD] + list(self.METADATA_FIELDS):
                    if f in hit["entity"]:
                        doc[f] = hit["entity"][f]
                docs.append(doc)
        return docs

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
        res = self.client.delete(collection_name=name, filter=expr)
        return {"delete_count": res.get("delete_count", 0)}

    # ------------------------------------------------------------------
    # 便捷函数（兼容旧调用：milvus_cli.search(...) / milvus_cli.insert_data(...)）
    # ------------------------------------------------------------------
    def _ensure_instance(self) -> None:
        """确保 client 已初始化（供模块级函数调用）。"""
        if self._client is None:
            _ = self.client  # noqa: F841

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
