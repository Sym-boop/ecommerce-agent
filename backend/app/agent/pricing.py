# ============================================
# backend/app/agent/pricing.py
# 动态定价与竞品监控 Agent
# 负责：经 ERP MCP 取自身/竞品价 → 对比 → 波动分析 → 调价建议
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
# 自家商品表：优先 our_products.csv（当前仓库文件名），兼容旧名 products.csv
_PRODUCTS_CSV_CANDIDATES = [
    _RAW_DIR / "our_products.csv",
    _RAW_DIR / "products.csv",
]
_COMPETITORS_CSV_CANDIDATES = [
    _PROCESSED_DIR / "competitors.csv",
    _RAW_DIR / "competitors.csv",
]

APPROVAL_THRESHOLD = 0.05
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


async def _mcp_query_product_shim(sku: str) -> dict[str, Any]:
    """
    与 erp_connector.query_product 同契约的离线实现。
    当本机未安装 fastmcp、无法 import MCP 模块时使用，保证定价链路可跑通。
    """
    mock_data = {
        "SKU-1001": {"sku": "SKU-1001", "name": "夏季纯棉T恤", "price": 59.9, "stock": 500},
        "SKU-1002": {"sku": "SKU-1002", "name": "复古牛仔外套", "price": 199.0, "stock": 120},
    }
    return mock_data.get(sku, {"sku": sku, "name": "未知商品", "price": 0.0, "stock": 0})


async def _mcp_query_competitor_prices_shim(sku: str) -> list[dict[str, Any]]:
    """与 erp_connector.query_competitor_prices 同契约的离线实现。"""
    mock_data = {
        "SKU-1001": [
            {
                "competitor_name": "竞品A",
                "price": 55.0,
                "platform": "淘宝",
                "timestamp": "2026-09-23 10:00:00",
            },
            {
                "competitor_name": "竞品B",
                "price": 58.5,
                "platform": "拼多多",
                "timestamp": "2026-09-23 10:00:00",
            },
        ],
        "SKU-1002": [
            {
                "competitor_name": "竞品C",
                "price": 195.0,
                "platform": "京东",
                "timestamp": "2026-09-23 10:00:00",
            },
        ],
    }
    return list(mock_data.get(sku, []))


def _load_erp_tools():
    """
    加载 A 的 ERP MCP 工具：query_product / query_competitor_prices。
    优先真实 mcp_servers.erp_connector；依赖缺失时回退到同契约 shim。
    返回 (query_product, query_competitor_prices, mode) mode in {mcp, shim}
    """
    try:
        from mcp_servers.erp_connector import query_competitor_prices, query_product

        return query_product, query_competitor_prices, "mcp"
    except Exception:
        return _mcp_query_product_shim, _mcp_query_competitor_prices_shim, "shim"


def _product_to_dict(product: Any, sku: str) -> dict[str, Any]:
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
    data["sku"] = data.get("sku") or sku
    data["price"] = float(data.get("price") or 0)
    data["stock"] = int(data.get("stock") or 0)
    return data


def fetch_own_product_via_mcp(sku: str) -> dict[str, Any]:
    """
    通过 ERP MCP（erp_connector.query_product）获取自身商品。
    调用失败或价格无效时，回退 our_products.csv / products.csv；CSV 补齐 cost / min_price。
    """
    data: dict[str, Any] = {}
    source = "csv:our_products"

    query_product, _, mode = _load_erp_tools()
    try:
        product = _run_async(query_product(sku))
        data = _product_to_dict(product, sku)
        source = (
            "mcp:erp_connector.query_product"
            if mode == "mcp"
            else "mcp_shim:query_product"
        )
    except Exception:
        data = {}

    local = _load_own_product_from_csv(sku)
    if float(data.get("price") or 0) <= 0:
        if local:
            data = {
                "sku": sku,
                "name": local["name"],
                "price": local["price"],
                "stock": local.get("stock", 0),
                "cost": local.get("cost", 0),
                "min_price": local.get("min_price", 0),
            }
            source = f"csv:{_resolve_products_csv().name}" if _resolve_products_csv() else "csv"
        else:
            data = {
                "sku": sku,
                "name": "",
                "price": 0.0,
                "stock": 0,
                "cost": 0.0,
                "min_price": 0.0,
            }
    elif local:
        data.setdefault("cost", local.get("cost", 0))
        data.setdefault("min_price", local.get("min_price", 0))
        if not data.get("name") or data.get("name") == "未知商品":
            data["name"] = local.get("name", data.get("name"))

    data["source"] = source
    return data


def fetch_competitors_via_mcp(sku: str) -> list[dict[str, Any]]:
    """
    通过 ERP MCP（erp_connector.query_competitor_prices）获取竞品价格。
    字段对齐 CompetitorPrice: competitor_name / price / platform / timestamp。
    MCP 返回空或仅最新价时，合并 CSV 多日快照以便波动分析。
    """
    _, query_competitor_prices, mode = _load_erp_tools()
    mcp_rows: list[dict[str, Any]] = []
    src_tag = "mcp" if mode == "mcp" else "mcp_shim"

    try:
        result = _run_async(query_competitor_prices(sku))
        raw_list = result if isinstance(result, list) else list(result or [])
        mcp_rows = [_normalize_competitor_row(r, sku, source=src_tag) for r in raw_list]
    except Exception:
        mcp_rows = []

    csv_rows = _load_competitors_from_csv(sku)

    if mcp_rows:
        merged = list(csv_rows)
        for row in mcp_rows:
            merged.append(row)
        return merged

    return csv_rows


def _normalize_competitor_row(
    row: Any,
    our_sku: str,
    source: str = "csv",
) -> dict[str, Any]:
    """兼容 MCP CompetitorPrice 与 CSV 列名。"""
    if hasattr(row, "model_dump"):
        row = row.model_dump()
    if not isinstance(row, dict):
        row = {
            "competitor_name": getattr(row, "competitor_name", ""),
            "price": getattr(row, "price", 0),
            "platform": getattr(row, "platform", ""),
            "timestamp": getattr(row, "timestamp", ""),
        }

    # MCP: timestamp；CSV: crawled_at
    crawled_at = str(
        row.get("crawled_at")
        or row.get("timestamp")
        or ""
    )
    competitor_name = row.get("competitor_name") or row.get("name") or ""
    competitor_sku = row.get("competitor_sku") or competitor_name or ""

    return {
        "our_sku": row.get("our_sku") or our_sku,
        "our_product_name": row.get("our_product_name") or row.get("our_name") or "",
        "competitor_sku": competitor_sku,
        "competitor_name": competitor_name,
        "platform": row.get("platform") or "",
        "price": float(row.get("price") or 0),
        "currency": row.get("currency") or "CNY",
        "sales_30d": int(float(row.get("sales_30d") or 0)),
        "rating": float(row.get("rating") or 0),
        "review_count": int(float(row.get("review_count") or 0)),
        "category": row.get("category") or "",
        "crawled_at": crawled_at,
        "source": source,
    }


def _resolve_products_csv() -> Optional[Path]:
    for path in _PRODUCTS_CSV_CANDIDATES:
        if path.exists():
            return path
    return None


def _load_own_product_from_csv(sku: str) -> Optional[dict[str, Any]]:
    path = _resolve_products_csv()
    if path is None:
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as f:
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
            rows.append(_normalize_competitor_row(row, sku, source="csv"))
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
    if not value:
        return None
    candidates = []
    if len(value) >= 19:
        candidates.append((value[:19], "%Y-%m-%d %H:%M:%S"))
    if len(value) >= 10:
        candidates.append((value[:10], "%Y-%m-%d"))
        candidates.append((value[:10], "%Y/%m/%d"))
    for text, fmt in candidates:
        try:
            return datetime.strptime(text, fmt)
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
    """每个竞品取最新一条；同日时优先 MCP 来源。"""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("competitor_sku") or row.get("competitor_name") or ""
        if not key:
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = row
            continue
        d_new = _parse_date(row.get("crawled_at", ""))
        d_old = _parse_date(prev.get("crawled_at", ""))
        prefer_new = False
        if d_new and d_old:
            if d_new > d_old:
                prefer_new = True
            elif d_new == d_old and row.get("source") in ("mcp", "mcp_shim") and prev.get("source") not in (
                "mcp",
                "mcp_shim",
            ):
                prefer_new = True
        elif d_new and not d_old:
            prefer_new = True
        elif row.get("source") in ("mcp", "mcp_shim") and prev.get("source") not in ("mcp", "mcp_shim") and not d_old:
            prefer_new = True
        if prefer_new:
            best[key] = row
    return list(best.values())


def analyze_price_volatility(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按竞品聚合历史价格，计算近期波动。"""
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = row.get("competitor_sku") or row.get("competitor_name") or ""
        if key:
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
    # 仅单点快照时无法谈趋势
    multi = any(d["snapshots"] >= 2 for d in per_competitor)
    if not multi:
        trend = "样本不足"
    elif avg_change <= -0.02:
        trend = "下行"
    elif avg_change >= 0.02:
        trend = "上行"
    else:
        trend = "平稳"

    return {
        "competitor_count": len(per_competitor),
        "avg_change_rate": round(avg_change, 4),
        "max_abs_change_rate": round(max(abs_moves), 4) if abs_moves else 0.0,
        "trend": trend,
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
                "source": c.get("source"),
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
def _confidence_level(score: float) -> str:
    if score >= 0.75:
        return "高"
    if score >= 0.55:
        return "中"
    return "低"


def generate_pricing_advice(
    own: dict[str, Any],
    comparison: dict[str, Any],
    volatility: dict[str, Any],
) -> dict[str, Any]:
    """
    生成结构化调价建议。
    必含：action / suggested_price / confidence / reason / basis（依据列表）。
    """
    own_price = float(own.get("price") or 0)
    cost = float(own.get("cost") or 0)
    min_price = float(own.get("min_price") or 0)
    stock = int(own.get("stock") or 0)
    median = float(comparison.get("competitor_median") or 0)
    avg_comp = float(comparison.get("competitor_avg") or 0)
    min_comp = float(comparison.get("competitor_min") or 0)
    max_comp = float(comparison.get("competitor_max") or 0)
    gap = float(comparison.get("gap_vs_median_pct") or 0)
    trend = volatility.get("trend", "平稳")
    avg_change = float(volatility.get("avg_change_rate") or 0)
    n_comp = int(volatility.get("competitor_count") or 0)

    # 结构化依据：每条含 type + detail，便于前端/审批展示
    evidence: list[dict[str, str]] = []

    def add_evidence(etype: str, detail: str) -> None:
        evidence.append({"type": etype, "detail": detail})

    action = "维持"
    suggested = own_price
    confidence = 0.55

    if own_price <= 0:
        add_evidence("数据异常", "自身价格无效或未取到商品价")
        return _pack_advice(
            action="维持",
            suggested=0.0,
            own_price=0.0,
            confidence=0.2,
            evidence=evidence,
            need_approval=False,
            cost=cost,
            min_price=min_price,
            floor=None,
        )

    if median <= 0 or n_comp <= 0:
        add_evidence("数据不足", "缺少有效竞品价格，建议维持现价并补充竞品数据")
        return _pack_advice(
            action="维持",
            suggested=own_price,
            own_price=own_price,
            confidence=0.3,
            evidence=evidence,
            need_approval=False,
            cost=cost,
            min_price=min_price,
            floor=max(cost * 1.05 if cost > 0 else 0.0, min_price) or None,
        )

    # --- 依据 1：竞品波动 ---
    if trend == "下行":
        add_evidence("价格波动", f"竞品近端均价变化 {avg_change:.1%}，整体呈下行趋势")
    elif trend == "上行":
        add_evidence("价格波动", f"竞品近端均价变化 {avg_change:.1%}，整体呈上行趋势")
    elif trend == "样本不足":
        add_evidence("价格波动", "竞品多为单点快照，波动信号有限，以最新比价为主")
    else:
        add_evidence("价格波动", f"竞品价格近端波动平稳（均变幅 {avg_change:.1%}）")

    # --- 依据 2：比价 ---
    add_evidence(
        "竞品比价",
        (
            f"自身价 {own_price:.2f}；竞品中位 {median:.2f} / 均价 {avg_comp:.2f} "
            f"/ 区间 [{min_comp:.2f}, {max_comp:.2f}]；相对中位偏离 {gap:.1%}"
        ),
    )

    target = median * (1 - TARGET_DISCOUNT_VS_MEDIAN)
    if trend == "下行":
        target *= 1 + min(avg_change, 0)

    # --- 依据 3：库存 ---
    if stock >= 400 and gap > 0:
        target *= 0.98
        add_evidence("库存", f"库存偏高（{stock}），倾向更进取跟价")
    elif stock > 0 and stock <= 80 and gap < -0.05:
        target = max(target, own_price)
        add_evidence("库存", f"库存偏低（{stock}），不宜大幅降价抢量")
    else:
        add_evidence("库存", f"当前库存 {stock}，对调价方向无强约束")

    # --- 决策 ---
    if gap >= 0.05:
        action = "降价"
        suggested = target
        confidence = 0.72
        add_evidence("决策", "自身明显高于竞品中位，建议下调以恢复价格竞争力")
    elif gap <= -0.08:
        if stock <= 150:
            action = "涨价"
            suggested = min(own_price * 1.03, median * 0.98)
            confidence = 0.65
            add_evidence("决策", "自身显著低于竞品中位且库存可控，可小幅提价改善毛利")
        else:
            action = "维持"
            suggested = own_price
            confidence = 0.7
            add_evidence("决策", "自身已具备价格优势，维持现价即可")
    else:
        if trend == "下行" and avg_change <= -0.03:
            action = "降价"
            suggested = min(own_price, target)
            confidence = 0.68
            add_evidence("决策", "市场价格整体下行，建议跟随微调")
        else:
            action = "维持"
            suggested = own_price
            confidence = 0.75
            add_evidence("决策", "与竞品中位偏离有限，建议维持观察")

    # --- 依据 4：成本/最低价保护 ---
    floor = max(cost * 1.05 if cost > 0 else 0.0, min_price)
    if floor > 0 and suggested < floor:
        add_evidence("价格保护", f"建议价触及保护价 {floor:.2f}（成本/最低价约束），已抬至底线")
        suggested = floor
        if suggested >= own_price * 0.995:
            action = "维持"
            suggested = own_price
    elif floor > 0:
        add_evidence(
            "价格保护",
            f"建议价高于保护价 {floor:.2f}（cost={cost:.2f}, min_price={min_price:.2f}）",
        )

    # --- 置信度修正 ---
    if n_comp >= 5:
        confidence += 0.08
        add_evidence("置信度修正", f"竞品样本充足（{n_comp} 个），置信度上调")
    elif n_comp >= 3:
        confidence += 0.04
        add_evidence("置信度修正", f"竞品样本一般（{n_comp} 个），置信度小幅上调")
    else:
        confidence -= 0.1
        add_evidence("置信度修正", f"竞品样本偏少（{n_comp} 个），置信度下调，建议谨慎采纳")

    if volatility.get("max_abs_change_rate", 0) >= 0.15:
        confidence -= 0.08
        add_evidence(
            "置信度修正",
            f"部分竞品最大波幅 {volatility['max_abs_change_rate']:.1%}，信号噪声较大，置信度下调",
        )

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
        add_evidence(
            "审批",
            f"调价幅度 {change_pct:.1%} ≥ 阈值 {APPROVAL_THRESHOLD:.0%}，建议人工审批后再执行",
        )
    else:
        add_evidence("审批", f"调价幅度 {change_pct:.1%} 未超阈值 {APPROVAL_THRESHOLD:.0%}，可自动建议")

    return _pack_advice(
        action=action,
        suggested=suggested,
        own_price=own_price,
        confidence=confidence,
        evidence=evidence,
        need_approval=need_approval,
        cost=cost,
        min_price=min_price,
        floor=floor if floor > 0 else None,
    )


def _pack_advice(
    *,
    action: str,
    suggested: float,
    own_price: float,
    confidence: float,
    evidence: list[dict[str, str]],
    need_approval: bool,
    cost: float,
    min_price: float,
    floor: Optional[float],
) -> dict[str, Any]:
    change_pct = ((suggested - own_price) / own_price) if own_price else 0.0
    if abs(change_pct) < 0.005 and own_price > 0:
        change_pct = 0.0
        suggested = own_price
        action = "维持"

    basis_texts = [e["detail"] for e in evidence]
    reason = "；".join(basis_texts) if basis_texts else "暂无依据"

    return {
        # 核心交付字段
        "action": action,  # 涨价 / 降价 / 维持
        "current_price": round(own_price, 2),
        "suggested_price": round(suggested, 2),
        "change_pct": round(change_pct, 4),
        "confidence": confidence,  # 0~1
        "confidence_level": _confidence_level(confidence),  # 高 / 中 / 低
        "reason": reason,  # 依据摘要（单字符串）
        "basis": basis_texts,  # 依据列表（纯文本）
        "evidence": evidence,  # 依据列表（带类型：价格波动/竞品比价/库存/...）
        "need_approval": need_approval,
        "guards": {
            "cost": cost,
            "min_price": min_price,
            "price_floor": round(floor, 2) if floor else None,
            "approval_threshold": APPROVAL_THRESHOLD,
        },
    }


def _data_source_labels(own: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, str]:
    has_mcp = any(r.get("source") == "mcp" for r in rows)
    has_shim = any(r.get("source") == "mcp_shim" for r in rows)
    _, _, mode = _load_erp_tools()
    if has_mcp:
        comp_label = "mcp:erp_connector.query_competitor_prices(+csv_history)"
    elif has_shim:
        comp_label = "mcp_shim:query_competitor_prices(+csv_history)"
    elif mode in ("mcp", "shim"):
        path = _resolve_competitors_csv()
        comp_label = f"mcp_empty→csv:{path.name}" if path else "mcp_empty"
    else:
        path = _resolve_competitors_csv()
        comp_label = f"csv:{path}" if path else "unavailable"
    return {
        "own": own.get("source") or "unknown",
        "competitors": comp_label,
    }


# --------------------------------------------
# 四、主入口
# --------------------------------------------
def analyze_sku(sku: str) -> dict[str, Any]:
    """输入 SKU，返回竞品比价结果与结构化调价建议（含置信度与依据）。"""
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
            "source": own.get("source"),
        },
        "comparison": comparison,
        "volatility": {
            "competitor_count": volatility["competitor_count"],
            "avg_change_rate": volatility["avg_change_rate"],
            "max_abs_change_rate": volatility["max_abs_change_rate"],
            "trend": volatility["trend"],
            "details": volatility["details"],
        },
        # 完整建议对象
        "recommendation": advice,
        # 扁平字段：方便 Supervisor / 审批页直接取用
        "action": advice["action"],
        "suggested_price": advice["suggested_price"],
        "confidence": advice["confidence"],
        "confidence_level": advice["confidence_level"],
        "reason": advice["reason"],
        "basis": advice["basis"],
        "evidence": advice["evidence"],
        "need_approval": advice["need_approval"],
        "data_source": _data_source_labels(own, all_comp_rows),
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
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"定价分析失败: {exc}"}


if __name__ == "__main__":
    # 强制 stdout 使用 UTF-8，避免 Windows 控制台中文乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    demo_sku = sys.argv[1] if len(sys.argv) > 1 else "SKU-1001"
    out = pricing_node({"sku": demo_sku, "goal": f"监控 {demo_sku} 竞品并调价"})
    print("status:", out.get("status"))
    if out.get("status") != "done":
        print("error:", out.get("error"))
        sys.exit(1)

    pr = out["price_result"]
    rec = pr["recommendation"]
    print("=" * 48)
    print(f"SKU: {pr['sku']} | {pr['product_name']}")
    print(f"自身价: {pr['own_product']['price']} | 竞品中位: {pr['comparison']['competitor_median']}")
    print(f"波动趋势: {pr['volatility']['trend']} ({pr['volatility']['avg_change_rate']:.2%})")
    print("-" * 48)
    print("【调价建议】")
    print(f"  动作: {rec['action']}")
    print(f"  现价 → 建议价: {rec['current_price']} → {rec['suggested_price']} ({rec['change_pct']:.2%})")
    print(f"  置信度: {rec['confidence']}（{rec['confidence_level']}）")
    print(f"  需审批: {rec['need_approval']}")
    print("【依据】")
    for i, item in enumerate(rec["evidence"], 1):
        print(f"  {i}. [{item['type']}] {item['detail']}")
    print("=" * 48)
