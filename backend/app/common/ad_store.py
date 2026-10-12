"""广告物料仓库（进程内内存存储）。

统一记录各生成模块（文案 / 图文 / 视频）成功产出的广告物料，供：
    - 「任务拓扑看板」展示已生成的图文 / 视频（含提示词、输入商品图片）
    - 「历史广告发售」页面按时间归档查看

注意：与 task_engine 一致为进程内内存存储，需单 worker 运行。
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta

_LOCK = threading.Lock()
_ADS: list[dict] = []


def add_ad(
    *,
    ad_type: str,
    title: str = "",
    prompt: str = "",
    platform: str = "",
    content: str = "",
    input_image: str | None = None,
    result_url: str | None = None,
    created_at: datetime | None = None,
    status: str = "published",
) -> dict:
    """登记一条广告物料。

    ad_type: copy（文案）| image（图文）| video（视频）
    input_image: 用户上传的商品图片（媒体 URL），文案类可为空
    result_url: 图文 / 视频的成品地址；文案类为空
    """
    created = created_at or datetime.now()
    record = {
        "id": f"AD-{created.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
        "type": ad_type,
        "title": title,
        "prompt": prompt,
        "platform": platform,
        "content": content,
        "input_image": input_image,
        "result_url": result_url,
        "status": status,
        "created_at": created.isoformat(timespec="seconds"),
    }
    with _LOCK:
        _ADS.insert(0, record)
    return record


def list_ads() -> list[dict]:
    """返回全部广告物料（按时间倒序）。"""
    with _LOCK:
        return list(_ADS)


def mark_delivered(ad_ids: list[str], platform: str = "", status: str = "delivered") -> int:
    """把指定广告标记为已投放（供投放中心调用），返回实际更新条数。"""
    ids = set(ad_ids or [])
    if not ids:
        return 0
    updated = 0
    with _LOCK:
        for ad in _ADS:
            if ad["id"] in ids:
                ad["status"] = status
                if platform:
                    ad["delivery_platform"] = platform
                updated += 1
    return updated


# ---------------------------------------------------------------------------
# 演示数据：预置几条历史文案广告，便于「历史广告发售」首次打开即有内容
# ---------------------------------------------------------------------------

_DEMO_COPY = [
    ("降噪蓝牙耳机 Pro3", "xiaohongshu", "通勤党狂喜！这款降噪耳机真的救我狗命🎧", "地铁上开着降噪，整个世界都安静了…", 0),
    ("轻透防晒隔离霜 SPF50+", "xiaohongshu", "敏感肌也能闭眼入的防晒☀️", "清爽不假白，涂完直接出门～", 1),
    ("316 不锈钢保温杯", "tiktok", "POV: your water stays hot for 24h 🤯", "This bottle changed my workday.", 2),
    ("Memory Foam Pillow", "amazon", "Ergonomic Memory Foam Pillow | Cooling Gel | Hypoallergenic", "1. Ergonomic design.\n2. Cooling gel.\n3. Hypoallergenic.", 4),
]


def seed_demo() -> None:
    """写入演示历史数据（仅当仓库为空时执行一次）。"""
    with _LOCK:
        if _ADS:
            return
    now = datetime.now()
    for name, platform, title, content, days_ago in _DEMO_COPY:
        add_ad(
            ad_type="copy",
            title=title,
            prompt=f"为「{name}」生成 {platform} 平台推广文案",
            platform=platform,
            content=content,
            created_at=now - timedelta(days=days_ago, hours=days_ago),
        )


seed_demo()