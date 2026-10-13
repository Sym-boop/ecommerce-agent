"""Celery 异步任务队列配置（成员B - 通用基础模块）

职责（需求 2.1.3）:
    - CEL-002 批量文案生成: 批量触发文案生成，异步返回结果

启动方式（在 backend/ 目录下）:
    # Worker（Windows 建议加 --pool=solo）
    celery -A app.common.celery_app worker --loglevel=info --pool=solo

与队友的函数契约（模块未就绪时任务自动跳过并告警，不影响队列运行）:
    - 成员D copywriter.py 需提供: generate_copy(product: dict, platform: str) -> dict

环境变量（与 .env.example 对齐，成员A维护）:
    REDIS_HOST / REDIS_PORT  默认 localhost:16379（docker-compose 映射端口）
    REDIS_URL                可选，完整连接串，优先级最高
"""

from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env", override=True)

import logging
import os

from celery import Celery

logger = logging.getLogger(__name__)


def _redis_url() -> str:
    """Redis 地址解析：优先 REDIS_URL，其次 .env.example 约定的 REDIS_HOST/REDIS_PORT。"""
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    host = os.getenv("REDIS_HOST", "localhost").strip() or "localhost"
    port = os.getenv("REDIS_PORT", "16379").strip() or "16379"
    return f"redis://{host}:{port}/0"


REDIS_URL = _redis_url()

celery_app = Celery(
    "ecommerce_agent",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=False,
    # 任务执行完再确认，Worker 崩溃时任务不丢
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=600,
    task_soft_time_limit=540,
    result_expires=86400,
)


@celery_app.task(name="tasks.batch_generate_copy", bind=True, max_retries=3, default_retry_delay=60)
def batch_generate_copy(self, products: list[dict], platforms: list[str]) -> dict:
    """CEL-002 批量文案生成：为多个商品 x 多平台异步生成文案。

    Args:
        products: 商品信息列表，字段与 copywriter Agent 的入参约定一致。
        platforms: 平台列表，如 ["xiaohongshu", "tiktok", "amazon"]。
    Returns:
        {"total", "success", "failed", "results": [{"product", "platform", "status", "copy"}]}
    """
    try:
        from app.agent.copywriter import generate_copy as _generate, CopyWriterInput
    except ImportError:
        logger.warning("app.agent.copywriter 尚未实现（成员D负责），批量文案任务跳过")
        return {"status": "skipped", "reason": "copywriter agent not ready"}

    results: list[dict] = []
    success = failed = 0
    for product in products:
        for platform in platforms:
            entry = {"product": product.get("sku") or product.get("name", ""), "platform": platform}
            try:
                input_data = CopyWriterInput(
                    product_name=product.get("product_name") or product.get("name", "")[:30],
                    product_sell_points=product.get("product_sell_points") or [],
                    product_category=product.get("product_category") or "通用",
                    platform=platform,
                )
                result = _generate(input_data)
                entry["copy"] = result.model_dump() if hasattr(result, "model_dump") else result
                entry["status"] = "success"
                success += 1
            except Exception as exc:
                logger.error("单条文案生成失败 [%s/%s]: %s", entry["product"], platform, exc)
                entry["status"] = "failed"
                entry["error"] = str(exc)
                failed += 1
            results.append(entry)
    return {"total": len(results), "success": success, "failed": failed, "results": results}
