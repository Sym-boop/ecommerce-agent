"""Celery 异步任务队列配置（成员B - 通用基础模块）

职责（需求 2.1.3）:
    - CEL-001 定时调价任务: 每 15 分钟抓取竞品价格并生成调价建议
    - CEL-002 批量文案生成: 批量触发文案生成，异步返回结果

启动方式（在 backend/ 目录下）:
    # Worker（Windows 建议加 --pool=solo）
    celery -A app.common.celery_app worker --loglevel=info --pool=solo
    # Beat 定时器（触发 CEL-001）
    celery -A app.common.celery_app beat --loglevel=info

与队友的函数契约（模块未就绪时任务自动跳过并告警，不影响队列运行）:
    - 成员E pricing.py 需提供: run_pricing_check(skus: list[str] | None) -> dict
    - 成员D copywriter.py 需提供: generate_copy(product: dict, platform: str) -> dict

环境变量:
    REDIS_URL  默认 redis://localhost:6379/0（成员A的 docker-compose 提供 Redis）
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from celery import Celery

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

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
    # CEL-001: 每 15 分钟执行一次竞品价格抓取与调价建议生成
    beat_schedule={
        "run-pricing-check-every-15min": {
            "task": "tasks.run_pricing_check",
            "schedule": timedelta(minutes=15),
        },
    },
)


@celery_app.task(name="tasks.run_pricing_check", bind=True, max_retries=3, default_retry_delay=60)
def run_pricing_check(self, skus: list[str] | None = None) -> dict:
    """CEL-001 定时调价任务：抓取竞品价格 -> 分析波动 -> 生成调价建议。

    Args:
        skus: 可选，指定商品 SKU 列表；为空时由 pricing Agent 自行决定监控范围。
    Returns:
        pricing Agent 的返回值（结构化调价建议列表）。
    """
    try:
        from app.agent.pricing import run_pricing_check as _run
    except ImportError:
        logger.warning("app.agent.pricing 尚未实现（成员E负责），本次定时调价任务跳过")
        return {"status": "skipped", "reason": "pricing agent not ready"}

    try:
        return _run(skus=skus)
    except Exception as exc:  # noqa: BLE001
        logger.error("定时调价任务执行失败，准备重试: %s", exc)
        raise self.retry(exc=exc) from exc


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
        from app.agent.copywriter import generate_copy as _generate
    except ImportError:
        logger.warning("app.agent.copywriter 尚未实现（成员D负责），批量文案任务跳过")
        return {"status": "skipped", "reason": "copywriter agent not ready"}

    results: list[dict] = []
    success = failed = 0
    for product in products:
        for platform in platforms:
            entry = {"product": product.get("sku") or product.get("name", ""), "platform": platform}
            try:
                entry["copy"] = _generate(product, platform)
                entry["status"] = "success"
                success += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("单条文案生成失败 [%s/%s]: %s", entry["product"], platform, exc)
                entry["status"] = "failed"
                entry["error"] = str(exc)
                failed += 1
            results.append(entry)
    return {"total": len(results), "success": success, "failed": failed, "results": results}
