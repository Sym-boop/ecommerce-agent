"""可观测性指标与成本统计（内存累加 + 落盘快照）。

**跟踪什么**

- **调用量 / 失败率**：按能力（文案 / 图文 / 视频 / 审查）分组
- **耗时**：平均 / P95，用于定位"哪个环节慢"
- **估算成本**：按 token 用量估算（文案），图文/视频按「张 / 条」计
- **降级次数**：LLM 失败静默降级为模板文案的次数——**这个指标最关键**，
  它让"看起来正常其实没调通"变得可观测（我们踩过这个坑）

实现上不引入 Prometheus 等外部依赖：进程内累加，配合 `/api/v1/system/metrics`
暴露，并定期快照到 `data/metrics.json` 以便重启后仍能看到历史累计。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

_ROOT = Path(__file__).resolve().parents[3]
_SNAPSHOT_REL = Path("data") / "metrics.json"

_LOCK = threading.RLock()

# 各能力的单价（元），用于估算成本；文案按 token，图文/视频按次
_PRICE = {
    # 每 1000 token 的估算价（元）——仅供参考，实际以账单为准
    "llm_input_per_1k": 0.001,
    "llm_output_per_1k": 0.002,
    "image_per_call": 0.10,
    "video_per_call": 1.00,
}

_state: dict[str, Any] = {
    "since": None,
    "capabilities": {},   # name -> {calls, ok, failed, degraded, total_ms, max_ms, durations[]}
    "llm_tokens": {"input": 0, "output": 0},
    "cost_estimate": 0.0,
    "counters": {},       # 自定义计数器
}


def _cap(name: str) -> dict[str, Any]:
    caps = _state["capabilities"]
    if name not in caps:
        caps[name] = {
            "calls": 0, "ok": 0, "failed": 0, "degraded": 0,
            "total_ms": 0.0, "max_ms": 0.0, "durations": [],
        }
    return caps[name]


def _resolve(path: Path) -> Path:
    if path.is_absolute():
        return path
    return path if path.parent.exists() else _ROOT / path


def _persist() -> None:
    """快照落盘（失败不影响业务）。"""
    try:
        path = _resolve(_SNAPSHOT_REL)
        path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "since": _state["since"],
            "capabilities": {
                k: {kk: vv for kk, vv in v.items() if kk != "durations"}
                for k, v in _state["capabilities"].items()
            },
            "llm_tokens": _state["llm_tokens"],
            "cost_estimate": round(_state["cost_estimate"], 6),
            "counters": _state["counters"],
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("指标快照写入失败: %s", exc)


if _state["since"] is None:
    _state["since"] = datetime.now().isoformat(timespec="seconds")


class track:
    """上下文管理器：记录一次调用的耗时与结果。

    用法::

        with track("copy_generation") as t:
            result = call_llm(...)
            t.tokens(input=120, output=300)
            t.degraded()          # 标记发生了降级
    """

    def __init__(self, capability: str) -> None:
        self.capability = capability
        self._start = 0.0
        self._degraded = False

    def __enter__(self) -> "track":
        self._start = time.perf_counter()
        return self

    def degraded(self) -> None:
        self._degraded = True

    def tokens(self, input: int = 0, output: int = 0) -> None:
        with _LOCK:
            _state["llm_tokens"]["input"] += max(0, int(input))
            _state["llm_tokens"]["output"] += max(0, int(output))
            _state["cost_estimate"] += (
                max(0, input) / 1000 * _PRICE["llm_input_per_1k"]
                + max(0, output) / 1000 * _PRICE["llm_output_per_1k"]
            )

    def cost(self, kind: str, count: int = 1) -> None:
        """按次计费的能力（image / video）。"""
        with _LOCK:
            _state["cost_estimate"] += _PRICE.get(f"{kind}_per_call", 0.0) * max(0, count)

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        with _LOCK:
            cap = _cap(self.capability)
            cap["calls"] += 1
            cap["total_ms"] += elapsed_ms
            cap["max_ms"] = max(cap["max_ms"], elapsed_ms)
            cap["durations"].append(round(elapsed_ms, 1))
            if len(cap["durations"]) > 500:
                del cap["durations"][:250]
            if exc_type is not None:
                cap["failed"] += 1
            else:
                cap["ok"] += 1
            if self._degraded:
                cap["degraded"] += 1
        return False  # 不吞异常


def incr(name: str, delta: int = 1) -> None:
    """自定义计数器（如 rbac_denied、dedupe_hit）。"""
    with _LOCK:
        _state["counters"][name] = _state["counters"].get(name, 0) + delta


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(pct / 100 * (len(s) - 1)))))
    return s[idx]


def snapshot() -> dict[str, Any]:
    """返回当前指标快照（含派生指标）。"""
    with _LOCK:
        caps: dict[str, Any] = {}
        for name, c in _state["capabilities"].items():
            calls = c["calls"] or 1
            caps[name] = {
                "calls": c["calls"],
                "ok": c["ok"],
                "failed": c["failed"],
                "degraded": c["degraded"],
                "failure_rate": round(c["failed"] / calls, 4),
                "degradation_rate": round(c["degraded"] / calls, 4),
                "avg_ms": round(c["total_ms"] / calls, 1),
                "p95_ms": _percentile(c["durations"], 95),
                "max_ms": round(c["max_ms"], 1),
            }
        tokens = dict(_state["llm_tokens"])
        tokens["total"] = tokens["input"] + tokens["output"]
        return {
            "since": _state["since"],
            "capabilities": caps,
            "llm_tokens": tokens,
            "cost_estimate": round(_state["cost_estimate"], 6),
            "price_table": dict(_PRICE),
            "counters": dict(_state["counters"]),
        }


def reset() -> None:
    """清空指标（测试用）。"""
    with _LOCK:
        _state["capabilities"] = {}
        _state["llm_tokens"] = {"input": 0, "output": 0}
        _state["cost_estimate"] = 0.0
        _state["counters"] = {}
        _state["since"] = datetime.now().isoformat(timespec="seconds")
