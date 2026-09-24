# ============================================
# backend/app/agent/pricing.py
# 动态定价与竞品监控 —— 单 Agent（供 Supervisor 调度）
# 感知：ERP MCP + CSV；决策：大模型；输出：敌我对比 + 调价建议
# ============================================

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TypedDict

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:
    pass

_RAW_DIR = _ROOT / "data" / "raw"
_PROCESSED_DIR = _ROOT / "data" / "processed"
_PRODUCTS_CSV_CANDIDATES = [
    _RAW_DIR / "our_products.csv",
    _RAW_DIR / "products.csv",
]
_COMPETITORS_CSV_CANDIDATES = [
    _PROCESSED_DIR / "competitors.csv",
    _RAW_DIR / "competitors.csv",
]

LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat").strip()
APPROVAL_THRESHOLD = 0.05


class PricingState(TypedDict, total=False):
    """与 SupervisorState 对齐的可调度输入。"""
    goal: str
    sku: str
    our_price: float
    product_info: dict
    sub_tasks: list
    price_result: dict
    status: str
    error: Optional[str]
    approval_required: bool
    approval_reason: str


# ============================================================
# 一、数据感知：仅 MCP + CSV
# ============================================================
def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _load_erp_tools():
    try:
        from mcp_servers.erp_connector import query_competitor_prices, query_product

        return query_product, query_competitor_prices
    except Exception as exc:
        raise RuntimeError(
            "无法加载 mcp_servers.erp_connector（请确认已安装 fastmcp 等依赖）"
        ) from exc


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
    return {
        "sku": data.get("sku") or sku,
        "name": data.get("name") or "",
        "price": float(data.get("price") or 0),
        "stock": int(data.get("stock") or 0),
    }


def _resolve_products_csv() -> Optional[Path]:
    for path in _PRODUCTS_CSV_CANDIDATES:
        if path.exists():
            return path
    return None


def _resolve_competitors_csv() -> Optional[Path]:
    for path in _COMPETITORS_CSV_CANDIDATES:
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
                    "source": f"csv:{path.name}",
                }
    return None


def _normalize_competitor_row(row: Any, our_sku: str, source: str) -> dict[str, Any]:
    if hasattr(row, "model_dump"):
        row = row.model_dump()
    if not isinstance(row, dict):
        row = {
            "competitor_name": getattr(row, "competitor_name", ""),
            "price": getattr(row, "price", 0),
            "platform": getattr(row, "platform", ""),
            "timestamp": getattr(row, "timestamp", ""),
        }
    name = row.get("competitor_name") or row.get("name") or ""
    return {
        "our_sku": row.get("our_sku") or our_sku,
        "competitor_sku": row.get("competitor_sku") or name,
        "competitor_name": name,
        "platform": row.get("platform") or "",
        "price": float(row.get("price") or 0),
        "sales_30d": int(float(row.get("sales_30d") or 0)),
        "rating": float(row.get("rating") or 0),
        "crawled_at": str(row.get("crawled_at") or row.get("timestamp") or ""),
        "source": source,
    }


def _load_competitors_from_csv(sku: str) -> list[dict[str, Any]]:
    path = _resolve_competitors_csv()
    if path is None:
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("our_sku") != sku:
                continue
            rows.append(_normalize_competitor_row(row, sku, source=f"csv:{path.name}"))
    return rows


def fetch_own_product(sku: str) -> dict[str, Any]:
    query_product, _ = _load_erp_tools()
    data: dict[str, Any] = {}
    source = "mcp:erp_connector.query_product"
    try:
        data = _product_to_dict(_run_async(query_product(sku)), sku)
    except Exception:
        data = {}

    local = _load_own_product_from_csv(sku)
    if float(data.get("price") or 0) <= 0:
        if not local:
            raise RuntimeError(f"MCP 与 CSV 均未找到有效商品价：{sku}")
        return local

    if local:
        data["cost"] = local.get("cost", 0)
        data["min_price"] = local.get("min_price", 0)
        data["category"] = local.get("category", "")
        if not data.get("name") or data.get("name") == "未知商品":
            data["name"] = local.get("name") or data.get("name")
    else:
        data.setdefault("cost", 0)
        data.setdefault("min_price", 0)

    data["source"] = source
    return data


def fetch_competitors(sku: str) -> list[dict[str, Any]]:
    _, query_competitor_prices = _load_erp_tools()
    mcp_rows: list[dict[str, Any]] = []
    try:
        result = _run_async(query_competitor_prices(sku))
        raw_list = result if isinstance(result, list) else list(result or [])
        mcp_rows = [
            _normalize_competitor_row(r, sku, source="mcp:query_competitor_prices")
            for r in raw_list
        ]
    except Exception:
        mcp_rows = []

    csv_rows = _load_competitors_from_csv(sku)
    merged = list(csv_rows) + mcp_rows
    if not merged:
        raise RuntimeError(f"MCP 与 CSV 均未找到竞品数据：{sku}")
    return merged


def extract_sku(state: PricingState) -> Optional[str]:
    """兼容 Supervisor：sku / product_info.sku / goal 文本。"""
    sku = (state.get("sku") or "").strip()
    if not sku:
        info = state.get("product_info") or {}
        if isinstance(info, dict):
            sku = str(info.get("sku") or "").strip()
    if sku:
        return sku.upper() if sku.upper().startswith("SKU-") else sku

    goal = state.get("goal") or ""
    match = re.search(r"SKU-?\s*(\d+)", goal, re.IGNORECASE)
    if match:
        num = match.group(1)
        return f"SKU-{num.zfill(4)}" if len(num) <= 4 else f"SKU-{num}"
    return None


# ============================================================
# 二、敌我数据对比（结构化，供 Supervisor / 审批展示）
# ============================================================
def _parse_date(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    candidates: list[tuple[str, str]] = []
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
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _latest_competitors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("competitor_sku") or row.get("competitor_name") or ""
        if not key:
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = row
            continue
        d_new = _parse_date(str(row.get("crawled_at") or ""))
        d_old = _parse_date(str(prev.get("crawled_at") or ""))
        prefer = False
        if d_new and d_old and d_new >= d_old:
            prefer = True
        elif d_new and not d_old:
            prefer = True
        elif str(row.get("source", "")).startswith("mcp") and not str(prev.get("source", "")).startswith("mcp"):
            prefer = True
        if prefer:
            best[key] = row
    return sorted(best.values(), key=lambda x: float(x.get("price") or 0))


def build_comparison(own: dict[str, Any], competitor_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """构建格式化敌我对比：表格行 + 汇总指标 + 可读文本。"""
    latest = _latest_competitors(competitor_rows)
    own_price = float(own.get("price") or 0)
    prices = [float(c["price"]) for c in latest if float(c.get("price") or 0) > 0]
    median = _median(prices)
    avg = sum(prices) / len(prices) if prices else 0.0
    cmin = min(prices) if prices else 0.0
    cmax = max(prices) if prices else 0.0
    gap = ((own_price - median) / median) if median else 0.0

    table: list[dict[str, Any]] = [
        {
            "side": "我方",
            "name": own.get("name") or own.get("sku"),
            "sku": own.get("sku"),
            "platform": "自营",
            "price": own_price,
            "vs_our": 0.0,
            "vs_our_pct": 0.0,
            "sales_30d": None,
            "rating": None,
            "stock": own.get("stock"),
            "cost": own.get("cost"),
            "min_price": own.get("min_price"),
            "crawled_at": None,
            "source": own.get("source"),
        }
    ]
    for c in latest:
        cp = float(c.get("price") or 0)
        table.append(
            {
                "side": "竞品",
                "name": c.get("competitor_name"),
                "sku": c.get("competitor_sku"),
                "platform": c.get("platform"),
                "price": cp,
                "vs_our": round(cp - own_price, 2),
                "vs_our_pct": round(((cp - own_price) / own_price), 4) if own_price else 0.0,
                "sales_30d": c.get("sales_30d"),
                "rating": c.get("rating"),
                "stock": None,
                "cost": None,
                "min_price": None,
                "crawled_at": c.get("crawled_at"),
                "source": c.get("source"),
            }
        )

    # Markdown 表格（审批页 / 日志友好）
    lines = [
        "| 身份 | 名称 | 平台 | 价格 | 相对我方 | 销量(30d) | 评分 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in table:
        vs = "—" if row["side"] == "我方" else f"{row['vs_our']:+.2f} ({row['vs_our_pct']:.1%})"
        sales = "—" if row.get("sales_30d") in (None, 0) else str(row.get("sales_30d"))
        rating = "—" if row.get("rating") in (None, 0) else str(row.get("rating"))
        lines.append(
            f"| {row['side']} | {row.get('name') or '-'} | {row.get('platform') or '-'} | "
            f"{row['price']:.2f} | {vs} | {sales} | {rating} |"
        )

    summary = {
        "own_price": own_price,
        "competitor_count": len(latest),
        "competitor_median": round(median, 2),
        "competitor_avg": round(avg, 2),
        "competitor_min": round(cmin, 2),
        "competitor_max": round(cmax, 2),
        "gap_vs_median_pct": round(gap, 4),
    }

    text = (
        f"【敌我价格对比】SKU={own.get('sku')} {own.get('name') or ''}\n"
        f"我方现价 {own_price:.2f}｜竞品中位 {median:.2f}｜均价 {avg:.2f}｜"
        f"区间 [{cmin:.2f}, {cmax:.2f}]｜相对中位偏离 {gap:.1%}\n"
        + "\n".join(lines)
    )

    return {
        "ours": {
            "sku": own.get("sku"),
            "name": own.get("name"),
            "price": own_price,
            "cost": float(own.get("cost") or 0),
            "stock": int(own.get("stock") or 0),
            "min_price": float(own.get("min_price") or 0),
            "source": own.get("source"),
        },
        "competitors_latest": latest,
        "summary": summary,
        "table": table,
        "formatted_text": text,
        "markdown_table": "\n".join(lines),
    }


def build_report(comparison: dict[str, Any], advice: dict[str, Any]) -> str:
    """敌我对比 + 调价建议的完整可读报告。"""
    parts = [comparison.get("formatted_text") or "", "", "【调价建议】"]
    parts.append(f"动作: {advice.get('action')}")
    parts.append(
        f"现价 → 建议价: {advice.get('current_price')} → {advice.get('suggested_price')} "
        f"({float(advice.get('change_pct') or 0):.2%})"
    )
    parts.append(
        f"置信度: {advice.get('confidence')}（{advice.get('confidence_level') or '-'}）"
    )
    parts.append(f"需审批: {advice.get('need_approval')}")
    parts.append(f"理由: {advice.get('reason') or ''}")
    if advice.get("analysis"):
        parts.append(f"分析: {advice.get('analysis')}")
    evidence = advice.get("evidence") or []
    if evidence:
        parts.append("依据:")
        for i, item in enumerate(evidence, 1):
            if isinstance(item, dict):
                parts.append(f"  {i}. [{item.get('type')}] {item.get('detail')}")
            else:
                parts.append(f"  {i}. {item}")
    return "\n".join(parts)


# ============================================================
# 三、大模型调价分析
# ============================================================
PRICING_SYSTEM_PROMPT = """你是电商「动态定价与竞品监控」单智能体（Pricing Agent），结果将交给 Supervisor 调度与审批流使用。

根据敌我对比数据和原始竞品记录，输出调价建议 JSON（不要 Markdown 围栏，不要额外文字）。

要求：
1. action 只能是：涨价 / 降价 / 维持
2. suggested_price > 0，且不低于 max(成本×1.05, min_price)（二者为 0 则忽略）
3. confidence ∈ [0,1]；confidence_level 为 高/中/低
4. evidence ≥ 3 条，含 type、detail
5. need_approval：|建议价-现价|/现价 ≥ 0.05 则为 true，否则 false
6. change_pct = (suggested_price - current_price) / current_price
7. 只基于给定数据，不要编造

JSON schema：
{
  "action": "涨价|降价|维持",
  "current_price": 0.00,
  "suggested_price": 0.00,
  "change_pct": 0.00,
  "confidence": 0.00,
  "confidence_level": "高|中|低",
  "need_approval": false,
  "reason": "一句话总结（供 Supervisor.price_result.reason）",
  "evidence": [{"type": "...", "detail": "..."}],
  "analysis": "完整分析说明"
}
"""


def _build_user_prompt(
    sku: str,
    own: dict[str, Any],
    competitors: list[dict[str, Any]],
    comparison: dict[str, Any],
    goal: str = "",
) -> str:
    payload = {
        "task": goal or f"对 {sku} 进行竞品监控并给出调价建议",
        "sku": sku,
        "approval_threshold": APPROVAL_THRESHOLD,
        "own_product": own,
        "comparison_summary": comparison.get("summary"),
        "comparison_table": comparison.get("table"),
        "competitor_records": competitors,
    }
    return "请基于以下敌我对比与原始数据，输出调价建议 JSON：\n\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def _chat_completions_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _call_llm(messages: list[dict[str, str]]) -> str:
    if not LLM_API_KEY or LLM_API_KEY.startswith("sk-xxxx"):
        raise RuntimeError("未配置有效 LLM_API_KEY，请检查项目根目录 .env")

    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("需要 httpx：pip install httpx") from exc

    url = _chat_completions_url(LLM_BASE_URL)
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    body: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    with httpx.Client(timeout=180.0) as client:
        resp = client.post(url, headers=headers, json=body)
        if resp.status_code >= 400 and "response_format" in body:
            body.pop("response_format", None)
            resp = client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"大模型调用失败 HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"大模型返回格式异常: {data}") from exc


def _extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("大模型返回空内容")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(text[start : end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"无法解析大模型 JSON：{text[:300]}")


# ============================================================
# 四、Pricing Agent（Supervisor 可调度）
# ============================================================
class PricingAgent:
    name = "pricing_agent"

    def collect(self, sku: str) -> dict[str, Any]:
        own = fetch_own_product(sku)
        competitors = fetch_competitors(sku)
        comparison = build_comparison(own, competitors)
        return {
            "sku": sku,
            "own": own,
            "competitors": competitors,
            "comparison": comparison,
            "data_source": {
                "own": own.get("source"),
                "competitors": sorted(
                    {c.get("source") for c in competitors if c.get("source")}
                ),
            },
        }

    def reason(self, context: dict[str, Any], goal: str = "") -> dict[str, Any]:
        messages = [
            {"role": "system", "content": PRICING_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_user_prompt(
                    context["sku"],
                    context["own"],
                    context["competitors"],
                    context["comparison"],
                    goal=goal,
                ),
            },
        ]
        raw_text = _call_llm(messages)
        answer = _extract_json_object(raw_text)
        answer["_llm_text"] = raw_text
        return answer

    def run(self, sku: str, goal: str = "") -> dict[str, Any]:
        """
        返回供 Supervisor 使用的 price_result：
        - 兼容字段：action / confidence / reason
        - 扩展字段：comparison（敌我对比）/ recommendation / report
        """
        context = self.collect(sku)
        advice = self.reason(context, goal=goal)
        own = context["own"]
        comparison = context["comparison"]
        report = build_report(comparison, advice)

        need_approval = bool(advice.get("need_approval"))
        action = advice.get("action")
        confidence = advice.get("confidence")
        reason = advice.get("reason") or advice.get("analysis") or ""

        return {
            # ---- Supervisor 兼容核心字段 ----
            "action": action,
            "confidence": confidence,
            "reason": reason,
            # ---- 调度 / 审批扩展 ----
            "sku": sku,
            "product_name": own.get("name") or "",
            "our_price": float(own.get("price") or 0),
            "suggested_price": advice.get("suggested_price"),
            "change_pct": advice.get("change_pct"),
            "confidence_level": advice.get("confidence_level"),
            "need_approval": need_approval,
            "approval_required": need_approval,
            "approval_reason": (
                f"调价建议「{action}」幅度触发审批：{reason}" if need_approval else ""
            ),
            # ---- 格式化敌我对比 + 建议 ----
            "comparison": comparison,
            "recommendation": {k: v for k, v in advice.items() if not str(k).startswith("_")},
            "evidence": advice.get("evidence") or [],
            "analysis": advice.get("analysis") or "",
            "report": report,
            "agent": self.name,
            "data_source": context["data_source"],
            "own_product": own,
        }


def analyze_sku(sku: str, goal: str = "") -> dict[str, Any]:
    return PricingAgent().run(sku, goal=goal)


def pricing_node(state: PricingState) -> dict:
    """
    Supervisor 调度入口。
    成功返回：
      {
        "price_result": {action, confidence, reason, comparison, report, ...},
        "status": "done",
        "approval_required": bool,
        "approval_reason": str,
      }
    """
    sku = extract_sku(state)
    if not sku:
        return {
            "status": "failed",
            "error": "未能解析 SKU，请传入 sku / product_info.sku 或在 goal 中写明 SKU-xxxx",
        }
    try:
        goal = state.get("goal") or ""
        price_result = analyze_sku(sku, goal=goal)
        need_approval = bool(price_result.get("need_approval"))
        out: dict[str, Any] = {
            "price_result": price_result,
            "status": "done",
            "approval_required": need_approval,
            "approval_reason": price_result.get("approval_reason") or "",
        }
        # 方便 Supervisor 写入 results["pricing"]
        out["results"] = {"pricing": price_result}
        return out
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"定价 Agent 失败: {exc}"}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    demo_sku = sys.argv[1] if len(sys.argv) > 1 else "SKU-1001"
    out = pricing_node({"sku": demo_sku, "goal": f"监控 {demo_sku} 竞品并调价"})
    if out.get("status") != "done":
        print(out.get("error"))
        sys.exit(1)

    pr = out["price_result"]
    print(pr.get("report") or "")
    print()
    print("--- Supervisor 兼容字段 ---")
    print(
        json.dumps(
            {
                "action": pr.get("action"),
                "confidence": pr.get("confidence"),
                "reason": pr.get("reason"),
                "suggested_price": pr.get("suggested_price"),
                "approval_required": out.get("approval_required"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
