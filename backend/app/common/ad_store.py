"""广告物料仓库（进程内内存存储）。

统一记录各生成模块（文案 / 图文 / 视频）成功产出的广告物料，并承载
「生成 → 审查 → 审批 → 投放 → 历史」的流水线状态机，供：
    - 「审查中心」按阶段筛选（违禁词审查 / 预算审查 / 待审批 / 已处理）
    - 「广告投放」只展示审批通过（stage=approved）的物料
    - 「历史广告发售」只展示已投放（stage=delivered）的物料

阶段 stage 取值：
    review    审查中（命中违禁词或预算异常，需人工处置）
    pending   两种审查均通过，待审批
    approved  审批通过，可投放
    delivered 已投放，进入历史
    rejected  审批驳回，退出流水线

审查标记：
    copy_ok     文案（标题+正文）是否通过违禁词审查
    budget_ok   投放预算是否落在合理区间
    violations  命中的违禁词明细
    budget      该广告的投放预算（元），None 表示未设置（视为通过预算审查）

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
    budget: float | None = None,
    copy_ok: bool = True,
    budget_ok: bool = True,
    violations: list[dict] | None = None,
    budget_verdict: str = "normal",
    budget_msg: str = "",
    stage: str = "pending",
    created_at: datetime | None = None,
    status: str = "published",
) -> dict:
    """登记一条广告物料。

    ad_type: copy（文案）| image（图文）| video（视频）
    input_image: 用户上传的商品图片（媒体 URL），文案类可为空
    result_url: 图文 / 视频的成品地址；文案类为空
    stage: 流水线阶段，由 pipeline 评估后写入
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
        "budget": budget,
        "copy_ok": copy_ok,
        "budget_ok": budget_ok,
        "violations": violations or [],
        "budget_verdict": budget_verdict,
        "budget_msg": budget_msg,
        "stage": stage,
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


def get_ad(ad_id: str) -> dict | None:
    """按 id 取回广告物料（返回内部引用，修改需经 update_ad 加锁）。"""
    with _LOCK:
        return next((ad for ad in _ADS if ad["id"] == ad_id), None)


def update_ad(ad_id: str, **patch) -> dict | None:
    """就地更新指定广告物料的字段，返回更新后的记录。"""
    with _LOCK:
        ad = next((a for a in _ADS if a["id"] == ad_id), None)
        if ad is None:
            return None
        ad.update(patch)
        return ad


def delete_ad(ad_id: str) -> bool:
    """删除指定广告物料，返回是否删除成功（供「广告投放」界面清理物料）。"""
    with _LOCK:
        before = len(_ADS)
        _ADS[:] = [a for a in _ADS if a["id"] != ad_id]
        return len(_ADS) != before


def mark_delivered(ad_ids: list[str], platform: str = "", status: str = "delivered") -> int:
    """把指定广告标记为已投放并推进到 delivered 阶段（供投放中心调用）。"""
    ids = set(ad_ids or [])
    if not ids:
        return 0
    updated = 0
    with _LOCK:
        for ad in _ADS:
            if ad["id"] in ids:
                ad["status"] = status
                ad["stage"] = "delivered"
                if platform:
                    ad["delivery_platform"] = platform
                updated += 1
    return updated


# ---------------------------------------------------------------------------
# 演示数据：预置几条已发售的历史广告，便于「历史广告发售」首次打开即有内容
# ---------------------------------------------------------------------------

_DEMO_COPY = [
    ("降噪蓝牙耳机 Pro3", "xiaohongshu", "通勤党狂喜！这款降噪耳机真的救我狗命", "地铁上开着降噪，整个世界都安静了…", 0),
    ("轻透防晒隔离霜 SPF50+", "xiaohongshu", "敏感肌也能闭眼入的防晒", "清爽不假白，涂完直接出门～", 1),
    ("316 不锈钢保温杯", "tiktok", "POV: your water stays hot for 24h", "This bottle changed my workday.", 2),
    ("Memory Foam Pillow", "amazon", "Ergonomic Memory Foam Pillow | Cooling Gel", "1. Ergonomic design.\n2. Cooling gel.\n3. Hypoallergenic.", 4),
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
            stage="delivered",
            status="delivered",
            created_at=now - timedelta(days=days_ago, hours=days_ago),
        )


seed_demo()