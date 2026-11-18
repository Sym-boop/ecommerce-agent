"""投放效果回流（闭环）。

**解决什么问题**

之前 `ab_report` 已经能算 CTR / CVR，但**没有任何接口能写入** `impressions` /
`clicks` / `conversions`——也就是说"效果数据"永远是空的，A/B 对照只能算
合规率和质量分，算不了 ROI。这个模块补的就是这一环。

**数据模型**

平台回传的是**按天增量**，落成时序记录：

    {ad_id, platform, date, impressions, clicks, conversions, spend, updated_at}

主键 `(ad_id, platform, date)` —— 平台重推同一天数据时**覆盖而不是累加**，
保证重试幂等（否则重试会把曝光数翻倍）。

**校验规则**（脏数据比没数据更危险）

| 规则 | 原因 |
|---|---|
| 计数必须是非负整数 | 小数/负数说明上游口径错了 |
| `clicks <= impressions` | 点击不可能多于曝光 |
| `conversions <= clicks` | 转化不可能多于点击 |
| `date` 必须是 YYYY-MM-DD 且不晚于今天 | 未来日期通常是时区或脏数据 |
| `ad_id` 必须存在 | 否则数据挂不到素材上，静默丢失 |

校验失败**整条拒收**并返回明确原因——不做"尽力而为的部分写入"，
因为部分写入会造成口径不一致。

**写回素材**

每次写入后把该素材所有天的累计值回写到 ad 记录上，
这样 `ab_report` 不需要改就能用上真实指标。
"""
from __future__ import annotations

import threading
from datetime import date as _date
from datetime import datetime
from typing import Any, Optional

from app.common import json_store

_LOCK = threading.RLock()
_PATH = "metrics/daily.json"

# 允许的字段
_FIELDS = ("impressions", "clicks", "conversions", "spend")


class MetricsError(ValueError):
    """回传数据不合法。"""


def _load() -> list[dict[str, Any]]:
    data = json_store.read_json(_PATH, default=[])
    return data if isinstance(data, list) else []


def _save(rows: list[dict[str, Any]]) -> None:
    json_store.write_json(_PATH, rows)


def _key(ad_id: str, platform: str, day: str) -> tuple[str, str, str]:
    return (str(ad_id), str(platform or ""), str(day))


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    """校验并归一化一条回传数据，非法则抛 `MetricsError`。"""
    if not isinstance(payload, dict):
        raise MetricsError("payload 必须是对象")

    ad_id = str(payload.get("ad_id") or "").strip()
    if not ad_id:
        raise MetricsError("缺少 ad_id")

    platform = str(payload.get("platform") or "").strip()

    day = str(payload.get("date") or "").strip()
    if not day:
        day = _date.today().isoformat()
    try:
        parsed = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        raise MetricsError(f"date 格式应为 YYYY-MM-DD，收到 {day!r}") from None
    if parsed > _date.today():
        raise MetricsError(f"date 不能晚于今天，收到 {day}")

    clean: dict[str, Any] = {"ad_id": ad_id, "platform": platform, "date": day}

    for field in _FIELDS:
        raw = payload.get(field, 0)
        if raw in (None, ""):
            raw = 0
        if isinstance(raw, bool):          # bool 是 int 子类，要单独拦
            raise MetricsError(f"{field} 必须是数字，不能是布尔值")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise MetricsError(f"{field} 必须是数字，收到 {raw!r}") from None
        if value < 0:
            raise MetricsError(f"{field} 不能为负，收到 {value}")
        if field != "spend" and value != int(value):
            raise MetricsError(f"{field} 必须是整数，收到 {value}")
        clean[field] = int(value) if field != "spend" else round(value, 2)

    if clean["clicks"] > clean["impressions"]:
        raise MetricsError(
            f"clicks({clean['clicks']}) 不能大于 impressions({clean['impressions']})"
        )
    if clean["conversions"] > clean["clicks"]:
        raise MetricsError(
            f"conversions({clean['conversions']}) 不能大于 clicks({clean['clicks']})"
        )
    return clean


def record(payload: dict[str, Any]) -> dict[str, Any]:
    """记录一条回传（同 ad/平台/日期 覆盖），返回该素材的累计指标。"""
    from app.common import ad_store

    clean = validate(payload)
    if ad_store.get_ad(clean["ad_id"]) is None:
        raise MetricsError(f"素材不存在: {clean['ad_id']}")

    with _LOCK:
        rows = _load()
        k = _key(clean["ad_id"], clean["platform"], clean["date"])
        replaced = False
        for i, row in enumerate(rows):
            if _key(row.get("ad_id", ""), row.get("platform", ""), row.get("date", "")) == k:
                rows[i] = {**clean, "updated_at": datetime.now().isoformat(timespec="seconds")}
                replaced = True
                break
        if not replaced:
            rows.append({**clean, "updated_at": datetime.now().isoformat(timespec="seconds")})
        _save(rows)

    return {**_totals(clean["ad_id"], rows), "replaced": replaced}


def record_batch(items: list[dict[str, Any]]) -> dict[str, Any]:
    """批量回传。任一条不合法则整批拒收（不写半截）。"""
    if not isinstance(items, list) or not items:
        raise MetricsError("items 必须是非空数组")
    from app.common import ad_store

    cleans: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for idx, item in enumerate(items):
        try:
            clean = validate(item)
        except MetricsError as exc:
            raise MetricsError(f"第 {idx + 1} 条不合法：{exc}") from None
        if ad_store.get_ad(clean["ad_id"]) is None:
            raise MetricsError(f"第 {idx + 1} 条：素材不存在 {clean['ad_id']}")
        k = _key(clean["ad_id"], clean["platform"], clean["date"])
        if k in seen:
            raise MetricsError(
                f"第 {idx + 1} 条：同一批里出现重复的 (ad_id, platform, date)"
            )
        seen.add(k)
        cleans.append(clean)

    now = datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        rows = _load()
        index = {
            _key(r.get("ad_id", ""), r.get("platform", ""), r.get("date", "")): i
            for i, r in enumerate(rows)
        }
        for clean in cleans:
            k = _key(clean["ad_id"], clean["platform"], clean["date"])
            entry = {**clean, "updated_at": now}
            if k in index:
                rows[index[k]] = entry
            else:
                index[k] = len(rows)
                rows.append(entry)
        _save(rows)

    ad_ids = {c["ad_id"] for c in cleans}
    return {
        "accepted": len(cleans),
        "rejected": 0,
        "ads_updated": len(ad_ids),
        "rows_total": len(rows),
    }


def _totals(ad_id: str, rows: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """某素材的累计指标 + 派生比率。"""
    rows = rows if rows is not None else _load()
    mine = [r for r in rows if str(r.get("ad_id", "")) == str(ad_id)]
    imp = sum(int(r.get("impressions", 0) or 0) for r in mine)
    clk = sum(int(r.get("clicks", 0) or 0) for r in mine)
    conv = sum(int(r.get("conversions", 0) or 0) for r in mine)
    spend = round(sum(float(r.get("spend", 0) or 0) for r in mine), 2)
    return {
        "ad_id": ad_id,
        "days": len(mine),
        "impressions": imp,
        "clicks": clk,
        "conversions": conv,
        "spend": spend,
        "ctr": round(clk / imp, 4) if imp else None,
        "cvr": round(conv / clk, 4) if clk else None,
        "cpa": round(spend / conv, 2) if conv else None,
        "cpm": round(spend / imp * 1000, 2) if imp else None,
    }


def sync_to_ads() -> dict[str, Any]:
    """把累计指标写回素材记录，供 ab_report 直接使用。"""
    from app.common import ad_store

    rows = _load()
    ad_ids = {str(r.get("ad_id", "")) for r in rows if r.get("ad_id")}
    updated = 0
    for ad_id in ad_ids:
        totals = _totals(ad_id, rows)
        if ad_store.get_ad(ad_id) is None:
            continue
        ad_store.update_ad(
            ad_id,
            impressions=totals["impressions"],
            clicks=totals["clicks"],
            conversions=totals["conversions"],
            spend=totals["spend"],
            ctr=totals["ctr"],
            cvr=totals["cvr"],
            metrics_synced_at=datetime.now().isoformat(timespec="seconds"),
        )
        updated += 1
    return {"ads_updated": updated, "rows": len(rows)}


def _group_of(platform: str) -> str:
    return platform or "未标注"


def summary(*, days: int = 30, platform: str = "") -> dict[str, Any]:
    """按时段汇总：整体 + 分平台 + 分日趋势。"""
    from app.common import ad_store

    rows = _load()
    if platform:
        rows = [r for r in rows if str(r.get("platform", "")) == platform]

    if days > 0:
        from datetime import timedelta

        cutoff = (_date.today() - timedelta(days=days)).isoformat()
        rows = [r for r in rows if str(r.get("date", "")) >= cutoff]

    def agg(bucket: list[dict[str, Any]]) -> dict[str, Any]:
        imp = sum(int(r.get("impressions", 0) or 0) for r in bucket)
        clk = sum(int(r.get("clicks", 0) or 0) for r in bucket)
        conv = sum(int(r.get("conversions", 0) or 0) for r in bucket)
        spend = round(sum(float(r.get("spend", 0) or 0) for r in bucket), 2)
        return {
            "impressions": imp,
            "clicks": clk,
            "conversions": conv,
            "spend": spend,
            "ctr": round(clk / imp, 4) if imp else None,
            "cvr": round(conv / clk, 4) if clk else None,
            "cpa": round(spend / conv, 2) if conv else None,
            "rows": len(bucket),
        }

    overall = agg(rows)

    by_platform: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_platform.setdefault(_group_of(str(r.get("platform", ""))), []).append(r)
    by_platform = {k: agg(v) for k, v in sorted(by_platform.items())}

    by_date: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_date.setdefault(str(r.get("date", "")), []).append(r)
    by_date = {k: agg(v) for k, v in sorted(by_date.items())}

    # 分「AI 直出 / 人工修正」——这才是能回答"值不值"的对照
    by_source: dict[str, dict[str, Any]] = {}
    for r in rows:
        ad = ad_store.get_ad(str(r.get("ad_id", ""))) or {}
        src = "human_edited" if ad.get("human_edited") or ad.get("source") == "human_feedback" else "ai"
        by_source.setdefault(src, []).append(r)
    by_source = {k: agg(v) for k, v in sorted(by_source.items())}

    return {
        "window_days": days,
        "filter_platform": platform or None,
        "overall": overall,
        "by_platform": by_platform,
        "by_date": by_date,
        "by_source": by_source,
    }


def list_rows(*, ad_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
    rows = _load()
    if ad_id:
        rows = [r for r in rows if str(r.get("ad_id", "")) == str(ad_id)]
    rows = sorted(rows, key=lambda r: (str(r.get("date", "")), str(r.get("ad_id", ""))))
    return rows[-limit:]
