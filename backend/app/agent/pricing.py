# ============================================
# backend/app/agent/pricing.py
# 动态定价与竞品监控 Agent
# 负责：经 ERP MCP 取自身商品价 → 对比竞品 → 波动分析 → 调价建议
# ============================================

from __future__ import annotations

import asyncio
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TypedDict

# 项目根目录，便于 import mcp_servers
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_RAW_DIR = _ROOT / "data" / "raw"
_PROCESSED_DIR = _ROOT / "data" / "processed"
_PRODUCTS_CSV = _RAW_DIR / "products.csv"
_COMPETITORS_CSV_CANDIDATES = [
    _PROCESSED_DIR / "competitors.csv",
    _RAW_DIR / "competitors.csv",
]

# 调价幅度超过该比例则建议人工审批
APPROVAL_THRESHOLD = 0.05
# 建议价相对竞品中位数的目标折扣（略低以抢量）
TARGET_DISCOUNT_VS_MEDIAN = 0.02


class PricingState(TypedDict, total=False):
    """与 Supervisor 对齐的输入状态"""
    goal: str
    sku: str
    sub_tasks: list
    price_result: dict
    status: str
    error: Optional[str]


# --------------------------------------------
# 一、MCP / 本地数据访问
# --------------------------------------------
def _run_async(coro):
    """在同步上下文中执行 async MCP tool。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def fetch_own_product_via_mcp(sku: str) -> dict[str, Any]:
    """
    通过 A 的 ERP MCP（erp_connector.query_product）获取自身商品。
    成功后用 products.csv 补齐 cost / min_price（MCP 当前未返回）。
    """
    from mcp_servers.erp_connector import query_product

    product = _run_async(query_product(sku))
    if hasattr(product, "model_dump"):
        data = product.model_dump()
    elif isinstance(product, dict):
        data = dict(product)
    else:
        data = {
            "sku": getattr(product, "sku", sku),
            "name": getattr(product, "name", ""),
            "price": float(getattr(product, "price", 0) or 0),
            "stock": int(getattr(product, "stock", 0) or 0),
        }

    local = _load_own_product_from_csv(sku)
    if float(data.get("price") or 0) <= 0 and local:
        data = {
            "sku": sku,
            "name": local["name"],
            "price": local["price"],
            "stock": local.get("stock", 0),
            "cost": local.get("cost", 0),
            "min_price": local.get("min_price", 0),
        }
    elif local:
        data.setdefault("cost", local.get("cost"))
        data.setdefault("min_price", local.get("min_price"))
        if not data.get("name") or data.get("name") == "未知商品":
            data["name"] = local.get("name", data.get("name"))
    return data


def fetch_competitors_via_mcp(sku: str) -> list[dict[str, Any]]:
    """
    优先调用 ERP MCP 的 query_competitors（若 A 已实现）；
    当前 erp_connector 尚未提供该工具时，回退到 data/processed|raw 竞品表。
    """
    try:
        from mcp_servers import erp_connector

        tool = getattr(erp_connector, "query_competitors", None)
        if callable(tool):
            result = _run_async(tool(sku))
            rows = result if isinstance(result, list) else list(result or [])
            normalized = [_normalize_competitor_row(r, sku) for r in rows]
            if normalized:
                return normalized
    except Exception:
        pass
    return _load_competitors_from_csv(sku)


def _normalize_competitor_row(row: Any, our_sku: str) -> dict[str, Any]:
    if hasattr(row, "model_dump"):
        row = row.model_dump()
    if not isinstance(row, dict):
        row = dict(row)
    return {
        "our_sku": row.get("our_sku") or our_sku,
        "our_product_name": row.get("our_product_name") or row.get("our_name") or "",
        "competitor_sku": row.get("competitor_sku") or row.get("sku") or "",
        "competitor_name": row.get("competitor_name") or row.get("name") or "",
        "platform": row.get("platform") or "",
        "price": float(row.get("price") or 0),
        "currency": row.get("currency") or "CNY",
        "sales_30d": int(float(row.get("sales_30d") or 0)),
        "rating": float(row.get("rating") or 0),
        "review_count": int(float(row.get("review_count") or 0)),
        "category": row.get("category") or "",
        "crawled_at": str(row.get("crawled_at") or ""),
    }


def _load_own_product_from_csv(sku: str) -> Optional[dict[str, Any]]:
    if not _PRODUCTS_CSV.exists():
        return None
    with _PRODUCTS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("sku") == sku:
                return {
                    "sku": sku,
                    "name": row.get("name") or "",
                    "price": float(row.get("price") or 0),
                    "cost": float(row.get("cost") or 0),
                    "stock": int(float(row.get("stock") or 0)),
                    "min_price": float(row.get("min_price") or 0),
                    "category": row.get("category") or "",
                }
    return None


def _resolve_competitors_csv() -> Optional[Path]:
    for path in _COMPETITORS_CSV_CANDIDATES:
        if path.exists():
            return path
    return None


def _load_competitors_from_csv(sku: str) -> list[dict[str, Any]]:
    path = _resolve_competitors_csv()
    if path is None:
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("our_sku") != sku:
                continue
            rows.append(_normalize_competitor_row(row, sku))
    return rows


def extract_sku(state: PricingState) -> Optional[str]:
    """从 state.sku 或 goal 文本中解析 SKU。"""
    sku = (state.get("sku") or "").strip()
    if sku:
        return sku.upper() if sku.upper().startswith("SKU-") else sku
    goal = state.get("goal") or ""
    match = re.search(r"SKU-?\s*(\d+)", goal, re.IGNORECASE)
    if match:
        num = match.group(1)
        return f"SKU-{num.zfill(4)}" if len(num) <= 4 else f"SKU-{num}"
    name_map = {
        "T恤": "SKU-1001",
        "t恤": "SKU-1001",
        "牛仔外套": "SKU-1002",
        "连衣裙": "SKU-1003",
        "碎花": "SKU-1003",
        "阔腿牛仔": "SKU-1004",
        "牛仔裤": "SKU-1004",
        "卫衣": "SKU-1005",
    }
    for key, mapped in name_map.items():
        if key in goal:
            return mapped
    return None


# --------------------------------------------
# 二、竞品比价与波动分析
# --------------------------------------------
def _parse_date(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19] if len(value) >= 19 else value, fmt)
        except ValueError:
            continue
    return None


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _latest_snapshot_per_competitor(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每个 competitor_sku 取最新 crawled_at 一条。"""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("competitor_sku") or row.get("competitor_name") or ""
        prev = best.get(key)
        if prev is None:
            best[key] = row
            continue
        d_new = _parse_date(row.get("crawled_at", ""))
        d_old = _parse_date(prev.get("crawled_at", ""))
        if d_new and d_old and d_new >= d_old:
            best[key] = row
        elif d_new and not d_old:
            best[key] = row
    return list(best.values())


def analyze_price_volatility(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按竞品聚合历史价格，计算近期波动。"""
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = row.get("competitor_sku") or ""
        by_comp[key].append(row)

    per_competitor = []
    change_rates: list[float] = []
    for comp_sku, items in by_comp.items():
        items_sorted = sorted(
            items,
            key=lambda r: _parse_date(r.get("crawled_at", "")) or datetime.min,
        )
        if not items_sorted:
            continue
        first, last = items_sorted[0], items_sorted[-1]
        p0, p1 = float(first["price"]), float(last["price"])
        change = ((p1 - p0) / p0) if p0 > 0 else 0.0
        change_rates.append(change)
        per_competitor.append(
            {
                "competitor_sku": comp_sku,
                "competitor_name": last.get("competitor_name"),
                "platform": last.get("platform"),
                "price_start": round(p0, 2),
                "price_end": round(p1, 2),
                "change_rate": round(change, 4),
                "snapshots": len(items_sorted),
            }
        )

    avg_change = sum(change_rates) / len(change_rates) if change_rates else 0.0
    abs_moves = [abs(c) for c in change_rates]
    return {
        "competitor_count": len(per_competitor),
        "avg_change_rate": round(avg_change, 4),
        "max_abs_change_rate": round(max(abs_moves), 4) if abs_moves else 0.0,
        "trend": (
            "下行" if avg_change <= -0.02 else "上行" if avg_change >= 0.02 else "平稳"
        ),
        "details": per_competitor,
    }


def build_comparison(own: dict[str, Any], latest_comps: list[dict[str, Any]]) -> dict[str, Any]:
    prices = [float(c["price"]) for c in latest_comps if float(c.get("price") or 0) > 0]
    own_price = float(own.get("price") or 0)
    median_price = _median(prices)
    avg_price = sum(prices) / len(prices) if prices else 0.0
    min_c = min(prices) if prices else 0.0
    max_c = max(prices) if prices else 0.0
    gap_vs_median = ((own_price - median_price) / median_price) if median_price else 0.0

    ranked = []
    for c in latest_comps:
        cp = float(c.get("price") or 0)
        ranked.append(
            {
                "competitor_sku": c.get("competitor_sku"),
                "competitor_name": c.get("competitor_name"),
                "platform": c.get("platform"),
                "price": cp,
                "sales_30d": c.get("sales_30d"),
                "rating": c.get("rating"),
                "vs_our_price": round(cp - own_price, 2),
                "vs_our_pct": round(((cp - own_price) / own_price), 4) if own_price else 0.0,
            }
        )
    ranked.sort(key=lambda x: x["price"])

    return {
        "own_price": own_price,
        "competitor_median": round(median_price, 2),
        "competitor_avg": round(avg_price, 2),
        "competitor_min": round(min_c, 2),
        "competitor_max": round(max_c, 2),
        "gap_vs_median_pct": round(gap_vs_median, 4),
        "competitors": ranked,
    }


# --------------------------------------------
# 三、调价建议（含置信度与依据）
# --------------------------------------------
def generate_pricing_advice(
    own: dict[str, Any],
    comparison: dict[str, Any],
    volatility: dict[str, Any],
) -> dict[str, Any]:
    own_price = float(own.get("price") or 0)
    cost = float(own.get("cost") or 0)
    min_price = float(own.get("min_price") or 0)
    stock = int(own.get("stock") or 0)
    median = float(comparison.get("competitor_median") or 0)
    gap = float(comparison.get("gap_vs_median_pct") or 0)
    trend = volatility.get("trend", "平稳")
    avg_change = float(volatility.get("avg_change_rate") or 0)

    reasons: list[str] = []
    action = "维持"
    suggested = own_price
    confidence = 0.55

    if own_price <= 0:
        return {
            "action": "维持",
            "suggested_price": 0.0,
            "change_pct": 0.0,
            "confidence": 0.2,
            "reason": "自身价格无效，无法生成调价建议",
            "need_approval": False,
            "basis": reasons,
        }

    if median <= 0:
        return {
            "action": "维持",
            "suggested_price": round(own_price, 2),
            "change_pct": 0.0,
            "confidence": 0.3,
            "reason": "缺少有效竞品价格，建议维持现价并补充竞品数据",
            "need_approval": False,
            "basis": ["竞品样本为空或价格全为 0"],
        }

    target = median * (1 - TARGET_DISCOUNT_VS_MEDIAN)
    if trend == "下行":
        target *= 1 + min(avg_change, 0)
        reasons.append(f"竞品近端均价波动 {avg_change:.1%}，整体呈下行")
    elif trend == "上行":
        reasons.append(f"竞品近端均价波动 {avg_change:.1%}，整体呈上行")
    else:
        reasons.append("竞品价格近端波动平稳")

    reasons.append(
        f"自身价 {own_price:.2f} vs 竞品中位数 {median:.2f}（偏离 {gap:.1%}）"
    )

    if stock >= 400 and gap > 0:
        target *= 0.98
        reasons.append(f"库存偏高（{stock}），倾向更进取的跟价")
    elif stock > 0 and stock <= 80 and gap < -0.05:
        target = max(target, own_price)
        reasons.append(f"库存偏低（{stock}），不宜大幅降价抢量")

    if gap >= 0.05:
        action = "降价"
        suggested = target
        confidence = 0.72
        reasons.append("自身明显高于竞品中位，建议下调以恢复价格竞争力")
    elif gap <= -0.08:
        if stock <= 150:
            action = "涨价"
            suggested = min(own_price * 1.03, median * 0.98)
            confidence = 0.65
            reasons.append("自身显著低于竞品中位且库存可控，可小幅提价改善毛利")
        else:
            action = "维持"
            suggested = own_price
            confidence = 0.7
            reasons.append("自身已具备价格优势，维持现价即可")
    else:
        if trend == "下行" and avg_change <= -0.03:
            action = "降价"
            suggested = min(own_price, target)
            confidence = 0.68
            reasons.append("市场价格整体下行，建议跟随微调")
        else:
            action = "维持"
            suggested = own_price
            confidence = 0.75
            reasons.append("与竞品中位偏离有限，建议维持观察")

    floor = max(cost * 1.05 if cost > 0 else 0.0, min_price)
    if floor > 0 and suggested < floor:
        reasons.append(f"建议价触及保护价 {floor:.2f}，已抬至底线")
        suggested = floor
        if suggested >= own_price * 0.995:
            action = "维持"
            suggested = own_price

    n = int(volatility.get("competitor_count") or 0)
    if n >= 5:
        confidence += 0.08
    elif n >= 3:
        confidence += 0.04
    else:
        confidence -= 0.1
        reasons.append("竞品样本偏少，建议谨慎采纳")

    if volatility.get("max_abs_change_rate", 0) >= 0.15:
        confidence -= 0.08
        reasons.append("部分竞品价格剧烈波动，信号噪声较大")

    confidence = max(0.2, min(0.95, round(confidence, 2)))
    change_pct = ((suggested - own_price) / own_price) if own_price else 0.0
    if abs(change_pct) < 0.005:
        action = "维持"
        suggested = own_price
        change_pct = 0.0
    elif change_pct < 0:
        action = "降价"
    else:
        action = "涨价"

    need_approval = abs(change_pct) >= APPROVAL_THRESHOLD
    if need_approval:
        reasons.append(f"调价幅度 {change_pct:.1%} ≥ {APPROVAL_THRESHOLD:.0%}，建议人工审批")

    return {
        "action": action,
        "suggested_price": round(suggested, 2),
        "change_pct": round(change_pct, 4),
        "confidence": confidence,
        "reason": "；".join(reasons),
        "need_approval": need_approval,
        "basis": reasons,
        "guards": {
            "cost": cost,
            "min_price": min_price,
            "price_floor": round(floor, 2) if floor else None,
            "approval_threshold": APPROVAL_THRESHOLD,
        },
    }


def _competitor_source_label() -> str:
    try:
        from mcp_servers import erp_connector

        if callable(getattr(erp_connector, "query_competitors", None)):
            return "mcp_servers.erp_connector.query_competitors"
    except Exception:
        pass
    path = _resolve_competitors_csv()
    return str(path) if path else "unavailable"


# --------------------------------------------
# 四、主入口
# --------------------------------------------
def analyze_sku(sku: str) -> dict[str, Any]:
    """输入 SKU，返回竞品比价结果与结构化调价建议。"""
    own = fetch_own_product_via_mcp(sku)
    all_comp_rows = fetch_competitors_via_mcp(sku)
    latest = _latest_snapshot_per_competitor(all_comp_rows)
    volatility = analyze_price_volatility(all_comp_rows)
    comparison = build_comparison(own, latest)
    advice = generate_pricing_advice(own, comparison, volatility)

    return {
        "sku": sku,
        "product_name": own.get("name") or "",
        "own_product": {
            "sku": own.get("sku", sku),
            "name": own.get("name"),
            "price": float(own.get("price") or 0),
            "cost": float(own.get("cost") or 0),
            "stock": int(own.get("stock") or 0),
            "min_price": float(own.get("min_price") or 0),
            "source": "mcp:erp_connector.query_product",
        },
        "comparison": comparison,
        "volatility": {
            "competitor_count": volatility["competitor_count"],
            "avg_change_rate": volatility["avg_change_rate"],
            "max_abs_change_rate": volatility["max_abs_change_rate"],
            "trend": volatility["trend"],
            "details": volatility["details"],
        },
        "recommendation": advice,
        "action": advice["action"],
        "confidence": advice["confidence"],
        "reason": advice["reason"],
        "data_source": {
            "own": "mcp_servers.erp_connector.query_product",
            "competitors": _competitor_source_label(),
        },
    }


def pricing_node(state: PricingState) -> dict:
    """
    LangGraph / Supervisor 可调度节点。
    返回 { price_result, status }，失败时 status=failed。
    """
    sku = extract_sku(state)
    if not sku:
        return {
            "status": "failed",
            "error": "未能从输入中解析 SKU，请传入 sku 字段或在 goal 中包含商品名/SKU",
        }
    try:
        result = analyze_sku(sku)
        return {"price_result": result, "status": "done"}
    except Exception as exc:  # noqa: BLE001 — 节点边界统一收敛错误
        return {"status": "failed", "error": f"定价分析失败: {exc}"}


if __name__ == "__main__":
    demo_sku = sys.argv[1] if len(sys.argv) > 1 else "SKU-1001"
    out = pricing_node({"sku": demo_sku, "goal": f"监控 {demo_sku} 竞品并调价"})
    print("status:", out.get("status"))
    if out.get("status") != "done":
        print("error:", out.get("error"))
        sys.exit(1)
    pr = out["price_result"]
    rec = pr["recommendation"]
    print(f"sku: {pr['sku']} | {pr['product_name']}")
    print(f"own: {pr['own_product']['price']} | median: {pr['comparison']['competitor_median']}")
    print(f"trend: {pr['volatility']['trend']} ({pr['volatility']['avg_change_rate']:.2%})")
    print(f"action: {rec['action']} -> {rec['suggested_price']} ({rec['change_pct']:.2%})")
    print(f"confidence: {rec['confidence']}")
    print(f"reason: {rec['reason']}")
