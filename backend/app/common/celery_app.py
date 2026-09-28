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
    - 成员E pricing.py 已提供: analyze_sku(sku, goal="") -> dict（调价建议）
    - 成员D copywriter.py 需提供: generate_copy(product: dict, platform: str) -> dict

环境变量（与 .env.example 对齐，成员A维护）:
    REDIS_HOST / REDIS_PORT  默认 localhost:16379（docker-compose 映射端口）
    REDIS_URL                可选，完整连接串，优先级最高
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import timedelta
from pathlib import Path

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
    # CEL-001: 每 15 分钟执行一次竞品价格抓取与调价建议生成
    beat_schedule={
        "run-pricing-check-every-15min": {
            "task": "tasks.run_pricing_check",
            "schedule": timedelta(minutes=15),
        },
    },
)

# 本品商品清单（成员E维护 data/raw/our_products.csv，列名 sku）
_OUR_PRODUCTS_CSV = Path(__file__).resolve().parents[3] / "data" / "raw" / "our_products.csv"


def _load_skus() -> list[str]:
    """从 our_products.csv 读取需要监控的 SKU 列表。"""
    if not _OUR_PRODUCTS_CSV.exists():
        return []
    with _OUR_PRODUCTS_CSV.open(encoding="utf-8-sig", newline="") as f:
        return [row["sku"].strip() for row in csv.DictReader(f) if row.get("sku", "").strip()]


@celery_app.task(name="tasks.run_pricing_check", bind=True, max_retries=3, default_retry_delay=60)
def run_pricing_check(self, skus: list[str] | None = None) -> dict:
    """CEL-001 定时调价任务：遍历本品 SKU -> 调用定价 Agent 生成调价建议。

    Args:
        skus: 可选，指定商品 SKU 列表；为空时读取 data/raw/our_products.csv。
    Returns:
        {"total", "success", "failed", "skipped", "results": [{"sku", "status", "advice"/"error"}]}
    """
    try:
        from app.agent.pricing import analyze_sku
    except ImportError:
        logger.warning("app.agent.pricing 尚未实现（成员E负责），本次定时调价任务跳过")
        return {"status": "skipped", "reason": "pricing agent not ready"}

    targets = skus or _load_skus()
    if not targets:
        logger.warning("未找到待监控 SKU（data/raw/our_products.csv 缺失或为空），任务跳过")
        return {"status": "skipped", "reason": "no skus configured"}

    results: list[dict] = []
    success = failed = 0
    for sku in targets:
        entry: dict = {"sku": sku}
        try:
            entry["advice"] = analyze_sku(sku, goal=f"定时巡检：监控 {sku} 竞品价格并给出调价建议")
            entry["status"] = "success"
            success += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("SKU %s 调价分析失败: %s", sku, exc)
            entry["status"] = "failed"
            entry["error"] = str(exc)
            failed += 1
        results.append(entry)
    return {"total": len(results), "success": success, "failed": failed, "results": results}


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
