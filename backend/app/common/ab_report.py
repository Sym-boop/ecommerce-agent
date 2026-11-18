"""投放效果回流与 A/B 对照（管道已通；真实 CTR/CVR 待平台授权）。

**诚实说明能力边界**

真实效果数据（曝光 / 点击 / 转化）来自广告平台，需要平台授权与真实投递。
当前 `QIANCHUAN_DELIVERY_MODE=mock`，没有真实数据。

所以这一层的做法是：**把可用于对照的分组维度先落盘并算出可比指标**，
接入平台后只需把真实指标填进同一个分组即可，不必重构。

**当前可用的对照维度（全部来自真实数据，不是模拟）**

1. **来源对照**：`source=ai`（AI 直接生成）vs 人工介入过的样本
   —— 由数据飞轮记录，可回答"人工修正后的文案质量是否更好"
2. **质量分对照**：各分组的平均质量分、通过率、卖点覆盖率
3. **审查负担对照**：各分组的违禁词命中率（人工参与是否降低了违规率）
4. **审批结果对照**：各分组的通过 / 驳回比例

**接入真实数据后自动增强**：把 `impressions` / `clicks` / `conversions`
写进素材记录，本模块的 `ctr` / `cvr` 字段会自动计算并纳入分组统计。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")


def _rate(numerator: float, denominator: float) -> Optional[float]:
    """安全求比率（分母为 0 返回 None，避免出现 NaN 误导判断）。"""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _group_of(ad: dict) -> str:
    """把素材归到对照分组。

    - `human_edited`：人工修正过（有版本记录 note 含"修正"）
    - `ai`：AI 直接产出、无人工介入
    """
    if (ad.get("source") or "ai") != "ai":
        return "human_edited"
    versions = ad.get("_version_notes") or []
    if any("修正" in (n or "") for n in versions):
        return "human_edited"
    return "ai"


def _materialize(ad: dict) -> dict[str, Any]:
    """从素材记录里抽出可对照的指标。"""
    violations = ad.get("violations") or []
    impressions = ad.get("impressions")
    clicks = ad.get("clicks")
    conversions = ad.get("conversions")
    return {
        "id": ad.get("id"),
        "platform": ad.get("platform") or "",
        "stage": ad.get("stage"),
        "group": _group_of(ad),
        "violation_count": len(violations),
        "copy_ok": bool(ad.get("copy_ok")),
        "budget_ok": bool(ad.get("budget_ok")),
        "quality_score": ad.get("quality_score"),
        # 真实效果指标：平台授权后写入即可自动参与统计
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "ctr": _rate(clicks, impressions) if impressions else None,
        "cvr": _rate(conversions, clicks) if clicks else None,
    }


def build_report(*, limit: int = 500) -> dict[str, Any]:
    """生成投放效果对照报告。"""
    from app.common import ad_store, versions

    ads = ad_store.list_ads()[:limit]

    # 把版本备注挂上，用于判断"是否人工修正过"
    for ad in ads:
        try:
            chain = versions.history(ad.get("id") or "")
            ad["_version_notes"] = [v.get("note") or "" for v in chain]
        except Exception:  # noqa: BLE001
            ad["_version_notes"] = []

    rows = [_materialize(ad) for ad in ads]

    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        g = groups.setdefault(row["group"], {
            "count": 0, "violation_total": 0, "copy_ok_count": 0,
            "quality_sum": 0.0, "quality_n": 0,
            "impressions": 0, "clicks": 0, "conversions": 0,
            "has_metrics": False,
        })
        g["count"] += 1
        g["violation_total"] += row["violation_count"]
        g["copy_ok_count"] += 1 if row["copy_ok"] else 0
        if isinstance(row.get("quality_score"), (int, float)):
            g["quality_sum"] += float(row["quality_score"])
            g["quality_n"] += 1
        if row.get("impressions"):
            g["has_metrics"] = True
            g["impressions"] += int(row["impressions"] or 0)
            g["clicks"] += int(row["clicks"] or 0)
            g["conversions"] += int(row["conversions"] or 0)

    summary: dict[str, Any] = {}
    for name, g in groups.items():
        count = g["count"] or 1
        summary[name] = {
            "materials": g["count"],
            "avg_violations": round(g["violation_total"] / count, 3),
            "compliance_rate": round(g["copy_ok_count"] / count, 4),
            "avg_quality_score": (
                round(g["quality_sum"] / g["quality_n"], 1) if g["quality_n"] else None
            ),
            "impressions": g["impressions"] if g["has_metrics"] else None,
            "clicks": g["clicks"] if g["has_metrics"] else None,
            "conversions": g["conversions"] if g["has_metrics"] else None,
            "ctr": _rate(g["clicks"], g["impressions"]) if g["has_metrics"] else None,
            "cvr": _rate(g["conversions"], g["clicks"]) if g["has_metrics"] else None,
        }

    # 平台维度交叉对照
    by_platform: dict[str, dict[str, Any]] = {}
    for row in rows:
        p = row["platform"] or "未指定"
        bucket = by_platform.setdefault(p, {"count": 0, "violation_total": 0, "copy_ok": 0})
        bucket["count"] += 1
        bucket["violation_total"] += row["violation_count"]
        bucket["copy_ok"] += 1 if row["copy_ok"] else 0
    platform_summary = {
        p: {
            "materials": b["count"],
            "avg_violations": round(b["violation_total"] / (b["count"] or 1), 3),
            "compliance_rate": round(b["copy_ok"] / (b["count"] or 1), 4),
        }
        for p, b in by_platform.items()
    }

    has_real_metrics = any(s.get("impressions") for s in summary.values())

    return {
        "total_materials": len(rows),
        "groups": summary,
        "by_platform": platform_summary,
        "real_metrics_available": has_real_metrics,
        "metrics_note": (
            "已接入真实投放指标（曝光/点击/转化），CTR 与 CVR 为实算值。"
            if has_real_metrics else
            "尚未接入真实投放指标：当前投递为 mock 模式，没有平台回传的曝光/点击/转化数据。"
            "本报告先提供「来源 / 平台」两个维度的合规率与质量分对照；"
            "接入平台数据后，把 impressions/clicks/conversions 写入素材记录即可自动纳入统计。"
        ),
        "stage_distribution": _stage_distribution(rows),
    }


def _stage_distribution(rows: list[dict]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for r in rows:
        s = r.get("stage") or "unknown"
        dist[s] = dist.get(s, 0) + 1
    return dict(sorted(dist.items(), key=lambda kv: -kv[1]))


def record_metrics(ad_id: str, *, impressions: int = 0, clicks: int = 0,
                   conversions: int = 0) -> Optional[dict]:
    """把平台回传的真实效果指标写入素材（管道入口，供后续对接调用）。"""
    from app.common import ad_store

    ad = ad_store.get_ad(ad_id)
    if ad is None:
        return None
    return ad_store.update_ad(
        ad_id,
        impressions=int(impressions),
        clicks=int(clicks),
        conversions=int(conversions),
    )
