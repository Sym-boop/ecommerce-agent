"""广告物料仓库（JSON 落盘，进程重启后不丢）。

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

持久化：落盘到 <仓库根>/data/ads.json（原子写入），进程重启后数据不丢。
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import datetime, timedelta

from app.common import json_store

_LOCK = threading.Lock()
_AD_FILE = "ads.json"
# 数据文件在启动时是否已存在：决定要不要写入演示数据（用户删空后不再自动重建）
_AD_FILE_EXISTED = json_store.exists(_AD_FILE)
# 广告物料落盘到 <仓库根>/data/ads.json，进程重启后不丢
_ADS: list[dict] = json_store.read_list(_AD_FILE, [])


def content_fingerprint(ad_type: str, title: str = "", content: str = "") -> str:
    """内容指纹：用于识别「同一份素材被重复登记」。

    原来的实现没有幂等保护，重复点击保存 / 重试会让同一份文案入库多条，
    审查中心出现内容完全相同的多张卡片需要重复审批。
    """
    raw = f"{(ad_type or '').strip()}|{(title or '').strip()}|{(content or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _ensure_fingerprints() -> None:
    """为历史数据补齐指纹（老记录没有该字段）。"""
    for ad in _ADS:
        if not isinstance(ad, dict):
            continue
        if not ad.get("fingerprint"):
            ad["fingerprint"] = content_fingerprint(
                ad.get("type") or "", ad.get("title") or "", ad.get("content") or ""
            )


def dedupe_existing() -> int:
    """清理历史遗留的重复物料（保留最早一条），返回删除条数。

    判定口径：**同类型 + 同标题 + 同正文** 视为重复。
    只对未投放的重复项去重；已 delivered 的历史记录一律保留（历史不可篡改）。
    """
    global _ADS
    with _LOCK:
        _ensure_fingerprints()
        seen: dict[str, dict] = {}
        kept: list[dict] = []
        removed = 0
        # _ADS 是时间倒序，倒序遍历可保证「最早的一条」优先入选
        for ad in reversed(_ADS):
            fp = ad.get("fingerprint")
            if ad.get("stage") == "delivered" or not fp:
                kept.append(ad)
                continue
            if fp in seen:
                removed += 1
                continue
            seen[fp] = ad
            kept.append(ad)
        if removed:
            _ADS = list(reversed(kept))
            _persist()
    return removed


def _persist() -> None:
    """把当前广告物料原子写盘（调用方需持有 _LOCK）。"""
    json_store.write_json(_AD_FILE, _ADS)


def revalidate_violations() -> dict:
    """用**当前**违禁词规则重新校验存量物料的审查结论。

    为什么需要：审查结论（`violations` / `copy_ok`）是**登记时算好并落盘**的。
    检测规则被修正后（例如修掉了单字「最」的子串误报），历史数据里仍留着按旧规
    则产生的误报——表现为"改了 bug 但界面还是显示违规"。

    这里在启动时用当前规则重算一次，让存量数据**自愈**。
    已 delivered / rejected 的终态物料不改动（保留当时的事实结论）。

    返回 {"checked", "fixed", "unavailable"}。
    """
    from app.common.banned_words import BannedWordsUnavailable, check

    fixed = checked = 0
    with _LOCK:
        for ad in _ADS:
            if not isinstance(ad, dict):
                continue
            if ad.get("stage") in {"delivered", "rejected"}:
                continue
            text = f"{ad.get('title') or ''} {ad.get('content') or ''}"
            try:
                hits = check(text)
            except BannedWordsUnavailable:
                return {"checked": checked, "fixed": fixed, "unavailable": True}
            checked += 1
            violations = [
                {
                    "word": h.get("word"),
                    "type": h.get("category") or "违禁词",
                    "suggestion": h.get("replacement") or "",
                    "context": h.get("context") or "",
                    "start": h.get("start", -1),
                }
                for h in hits
            ]
            new_ok = not violations
            old_words = [v.get("word") for v in (ad.get("violations") or [])]
            new_words = [v["word"] for v in violations]
            if old_words != new_words or bool(ad.get("copy_ok")) != new_ok:
                ad["violations"] = violations
                ad["copy_ok"] = new_ok
                ad["review_revalidated_at"] = datetime.now().isoformat(timespec="seconds")
                fixed += 1
        if fixed:
            _persist()
    return {"checked": checked, "fixed": fixed, "unavailable": False}


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
    review_unavailable: bool = False,
    dedupe: bool = True,
    generated_by: str = "",
    category: str = "",
    source: str = "ai",
    session_id: str = "",
) -> dict:
    """登记一条广告物料。

    ad_type: copy（文案）| image（图文）| video（视频）
    input_image: 用户上传的商品图片（媒体 URL），文案类可为空
    result_url: 图文 / 视频的成品地址；文案类为空
    stage: 流水线阶段，由 pipeline 评估后写入
    review_unavailable: 风控词表不可用时的留痕标记（fail-closed）
    dedupe: 是否对「同类型+同标题+同正文」的未投放物料做幂等去重
    generated_by: 生成者（用于职责分离：禁止自审）
    category / source / session_id: 供数据飞轮与检索过滤使用

    返回: 新登记（或命中去重后已存在的）物料记录。
    """
    created = created_at or datetime.now()
    fingerprint = content_fingerprint(ad_type, title, content)

    with _LOCK:
        if dedupe:
            _ensure_fingerprints()
            for ad in _ADS:
                if ad.get("fingerprint") == fingerprint and ad.get("stage") != "delivered":
                    # 幂等：同一份素材重复保存时返回既有记录，不再新增
                    ad["_deduped"] = True
                    return ad

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
            "review_unavailable": review_unavailable,
            "fingerprint": fingerprint,
            "generated_by": generated_by,
            "category": category,
            "source": source,
            "session_id": session_id,
            "created_at": created.isoformat(timespec="seconds"),
        }
        _ADS.insert(0, record)
        _persist()
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
        _persist()
        return ad


def delete_ad(ad_id: str) -> bool:
    """删除指定广告物料，返回是否删除成功（供「广告投放」界面清理物料）。"""
    with _LOCK:
        before = len(_ADS)
        _ADS[:] = [a for a in _ADS if a["id"] != ad_id]
        removed = len(_ADS) != before
        if removed:
            _persist()
        return removed


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
        if updated:
            _persist()
    return updated


# ---------------------------------------------------------------------------
# 演示数据：预置几条已发售的历史广告，便于「历史广告发售」首次打开即有内容
# 仅在 <仓库根>/data/ads.json 不存在时写入一次；用户删空后不再自动重建。
# ---------------------------------------------------------------------------

_DEMO_COPY = [
    ("降噪蓝牙耳机 Pro3", "xiaohongshu", "通勤党狂喜！这款降噪耳机真的救我狗命", "地铁上开着降噪，整个世界都安静了…", 0),
    ("轻透防晒隔离霜 SPF50+", "xiaohongshu", "敏感肌也能闭眼入的防晒", "清爽不假白，涂完直接出门～", 1),
    ("316 不锈钢保温杯", "tiktok", "POV: your water stays hot for 24h", "This bottle changed my workday.", 2),
    ("Memory Foam Pillow", "amazon", "Ergonomic Memory Foam Pillow | Cooling Gel", "1. Ergonomic design.\n2. Cooling gel.\n3. Hypoallergenic.", 4),
]


def seed_demo() -> None:
    """写入演示历史数据（仅当数据文件不存在时执行一次）。"""
    with _LOCK:
        if _AD_FILE_EXISTED or _ADS:
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