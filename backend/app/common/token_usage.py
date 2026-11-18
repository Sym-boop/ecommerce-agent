"""LLM Token 用量账本（按日 / 按月聚合）。

**为什么单独做一本账**

`metrics` 里的 token 计数是**进程内累加**——重启归零，也没有时间维度，
只能回答「这个进程用了多少」，答不了「这个月用了多少」。

这里改成 **JSONL 追加写**：每次 LLM 调用追加一行，读取时按时间窗口聚合。
好处是：
- 重启不丢（落盘）
- 天然支持任意窗口（今天 / 本月 / 最近 30 天）
- 追加写不会因为并发而互相覆盖

**一行记录长这样**

    {"at":"2026-11-04T19:30:12","model":"deepseek-flash","scene":"copywriter",
     "prompt_tokens":812,"completion_tokens":176,"total_tokens":988,
     "cost":0.001164}

`cost` 用和 `metrics` 一致的单价表（输入 0.001/千 token，输出 0.002/千 token），
**这是估算不是账单**——真实计费以服务商为准。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

_LOCK = threading.RLock()

# 与 metrics.py 保持一致的单价（元 / 千 token）
PRICE_INPUT_PER_1K = float(os.getenv("LLM_PRICE_INPUT_PER_1K", "0.001"))
PRICE_OUTPUT_PER_1K = float(os.getenv("LLM_PRICE_OUTPUT_PER_1K", "0.002"))

_LOG_NAME = "metrics/token_usage.jsonl"


def _path() -> Path:
    """账本路径：跟随存储后端的 data 目录。"""
    from app.common import json_store

    base = getattr(json_store, "DATA_DIR", Path("data"))
    p = Path(base) / _LOG_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> datetime:
    return datetime.now()


def record(
    *,
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    scene: str = "",
    at: Optional[datetime] = None,
) -> dict[str, Any]:
    """记一次调用。返回写入的那一行（方便调用方打印/断言）。"""
    prompt_tokens = max(0, int(prompt_tokens or 0))
    completion_tokens = max(0, int(completion_tokens or 0))
    cost = (prompt_tokens / 1000 * PRICE_INPUT_PER_1K
            + completion_tokens / 1000 * PRICE_OUTPUT_PER_1K)
    row = {
        "at": (at or _now()).isoformat(timespec="seconds"),
        "model": str(model or ""),
        "scene": str(scene or ""),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost": round(cost, 6),
    }
    try:
        with _LOCK:
            with _path().open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        # 记账失败不能阻断主流程（生成是本业，记账是旁路）
        logger.warning("token 账本写入失败: %s", exc)
    return row


def load_all() -> list[dict[str, Any]]:
    """读全部记录（跳过坏行）。"""
    p = _path()
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.warning("token 账本读取失败: %s", exc)
    return rows


def _window(period: str) -> tuple[Optional[str], str]:
    """返回 (起始日期 ISO 或 None, 窗口标签)。"""
    today = date.today()
    p = (period or "month").strip().lower()
    if p in ("today", "day"):
        return today.isoformat(), "day"
    if p in ("month", "this_month"):
        return today.replace(day=1).isoformat(), "month"
    if p in ("year", "this_year"):
        return today.replace(month=1, day=1).isoformat(), "year"
    if p in ("all", "total"):
        return None, "all"
    # 形如 30d / 7d
    if p.endswith("d") and p[:-1].isdigit():
        return (today - timedelta(days=int(p[:-1]))).isoformat(), f"last_{p[:-1]}d"
    return today.replace(day=1).isoformat(), "month"


def _agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = sum(int(r.get("prompt_tokens", 0) or 0) for r in rows)
    completion = sum(int(r.get("completion_tokens", 0) or 0) for r in rows)
    cost = round(sum(float(r.get("cost", 0) or 0) for r in rows), 6)
    return {
        "calls": len(rows),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_estimate": cost,
    }


def summary(period: str = "month") -> dict[str, Any]:
    """按窗口汇总 + 按日/按月/按模型拆分。"""
    start, label = _window(period)
    rows = load_all()
    if start:
        rows = [r for r in rows if str(r.get("at", ""))[:10] >= start]

    overall = _agg(rows)

    by_day: dict[str, list[dict[str, Any]]] = {}
    by_month: dict[str, list[dict[str, Any]]] = {}
    by_model: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        day = str(r.get("at", ""))[:10]
        if day:
            by_day.setdefault(day, []).append(r)
            by_month.setdefault(day[:7], []).append(r)
        by_model.setdefault(str(r.get("model") or "unknown"), []).append(r)

    # 估算月用量：用窗口内日均 × 30，仅在窗口 >= 1 天时有意义
    daily_avg = round(overall["total_tokens"] / max(1, len(by_day)), 1) if by_day else 0.0

    return {
        "period": label,
        "since": start,
        "overall": overall,
        "daily_avg_tokens": daily_avg,
        "projected_30d_tokens": int(daily_avg * 30),
        "projected_30d_cost": round(
            (overall["cost_estimate"] / max(1, len(by_day))) * 30, 4) if by_day else 0.0,
        "by_day": {k: _agg(v) for k, v in sorted(by_day.items())},
        "by_month": {k: _agg(v) for k, v in sorted(by_month.items())},
        "by_model": {k: _agg(v) for k, v in sorted(by_model.items())},
        "price_per_1k": {"input": PRICE_INPUT_PER_1K, "output": PRICE_OUTPUT_PER_1K},
        "note": "cost 为按单价表的估算值，非服务商账单",
    }


def last_n(n: int = 20) -> list[dict[str, Any]]:
    """最近 n 条明细。"""
    rows = load_all()
    return rows[-max(1, n):]


def reset() -> dict[str, Any]:
    """清空账本（仅测试用）。"""
    with _LOCK:
        p = _path()
        removed = p.exists()
        if removed:
            p.unlink()
    return {"removed": removed}
