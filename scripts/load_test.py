"""并发压测：测出 P50 / P95 / P99、吞吐、错误率。

不依赖 locust，用标准库线程池 + 统计自算，保证在任何环境可跑。

三类接口分别测（延迟特征差别很大，混在一起没有意义）：
  1. 读接口   GET /health、GET /api/v1/ads        —— 反映基础吞吐
  2. 写接口   POST /api/v1/review/check          —— 违禁词扫描（CPU 密集）
  3. 状态机   GET /api/v1/review/stats           —— 聚合查询

用法：
    python scripts/load_test.py                 # 默认 20 并发 × 每接口 200 次
    python scripts/load_test.py --concurrency 50 --requests 500
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8001"

ENDPOINTS = [
    # (名称, method, path, body)
    ("health", "GET", "/health", None),
    ("list_ads", "GET", "/api/v1/ads", None),
    ("review_stats", "GET", "/api/v1/review/stats", None),
    ("banned_check", "POST", "/api/v1/review/check",
     {"title": "办公室必备！同事都来问链接的杯子", "content": "最近入手了一款水杯，保温 12 小时，容量 500ml。"}),
]


def one_request(method: str, path: str, body: dict | None) -> tuple[bool, float, int]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            return True, time.perf_counter() - t0, resp.status
    except urllib.error.HTTPError as e:
        e.read()
        # 只有 2xx 算成功。原实现把 200–499 都算通过，
        # 结果 404 被当成成功——"零错误率"是假的（实测过：路由写错时
        # 25% 的请求 404 却报 0% 错误）。
        return (200 <= e.code < 300), time.perf_counter() - t0, e.code
    except Exception:  # noqa: BLE001
        return False, time.perf_counter() - t0, 0


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def run(name: str, method: str, path: str, body: dict | None,
        concurrency: int, total: int) -> dict:
    print(f"\n[{name}] {method} {path}  并发={concurrency} 请求={total}")
    latencies: list[float] = []
    errors = 0
    t_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(one_request, method, path, body) for _ in range(total)]
        for fut in as_completed(futures):
            ok, dt, _code = fut.result()
            latencies.append(dt)
            if not ok:
                errors += 1

    elapsed = time.perf_counter() - t_start
    ok_count = total - errors
    stats = {
        "endpoint": name,
        "requests": total,
        "concurrency": concurrency,
        "success": ok_count,
        "errors": errors,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "elapsed_s": round(elapsed, 3),
        "throughput_rps": round(ok_count / elapsed, 1) if elapsed > 0 else 0.0,
        "p50_ms": round(pct(latencies, 50) * 1000, 1),
        "p90_ms": round(pct(latencies, 90) * 1000, 1),
        "p95_ms": round(pct(latencies, 95) * 1000, 1),
        "p99_ms": round(pct(latencies, 99) * 1000, 1),
        "max_ms": round(max(latencies) * 1000, 1) if latencies else 0.0,
        "mean_ms": round(statistics.mean(latencies) * 1000, 1) if latencies else 0.0,
    }
    print(f"    成功 {ok_count}/{total}  错误率 {stats['error_rate']:.2%}  "
          f"吞吐 {stats['throughput_rps']} rps")
    print(f"    P50={stats['p50_ms']}ms  P90={stats['p90_ms']}ms  "
          f"P95={stats['p95_ms']}ms  P99={stats['p99_ms']}ms  max={stats['max_ms']}ms")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--requests", type=int, default=200)
    args = ap.parse_args()

    print("=" * 70)
    print(f"并发压测 → {BASE}")
    print("=" * 70)

    # 探活
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=10) as r:
            r.read()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 后端不可达（{exc}）。请先启动：cd backend && "
              f"python -m uvicorn app.main:app --port 8001")
        return 2

    results = []
    for name, method, path, body in ENDPOINTS:
        results.append(run(name, method, path, body, args.concurrency, args.requests))

    # 汇总
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    print(f"{'接口':<16}{'RPS':>8}{'P50':>9}{'P95':>9}{'P99':>9}{'错误率':>9}")
    for s in results:
        print(f"{s['endpoint']:<16}{s['throughput_rps']:>8}{s['p50_ms']:>8}ms"
              f"{s['p95_ms']:>8}ms{s['p99_ms']:>8}ms{s['error_rate']:>8.1%}")

    total_req = sum(s["requests"] for s in results)
    total_err = sum(s["errors"] for s in results)
    print(f"\n总请求 {total_req}，总错误 {total_err}，整体错误率 {total_err/total_req:.2%}")

    out = ROOT / "data" / "reports" / "load_test.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "base_url": BASE,
        "concurrency": args.concurrency,
        "results": results,
        "total_requests": total_req,
        "total_errors": total_err,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已保存: {out}")

    return 0 if total_err / total_req < 0.01 else 1


if __name__ == "__main__":
    sys.exit(main())
