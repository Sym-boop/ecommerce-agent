"""验证「效果闭环」：回传 → 校验 → 时序聚合 → CTR/CVR 实算。

这个脚本要证明四件事：
  1. 合法数据能写入，且**同 (ad, platform, date) 重推是覆盖不是累加**（幂等）
  2. 脏数据被**整条拒收**，并给出具体字段原因
  3. 派生指标算式正确（CTR / CVR / CPA / CPM 手动对得上）
  4. 汇总能按平台、按日、按「AI 直出 vs 人工修正」拆分

运行：python scripts/verify_metrics_loop.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8001"


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def main() -> int:
    print("=" * 70)
    print("验证：投放效果回流闭环")
    print("=" * 70)

    # 找一个已投放的素材
    code, resp = call("GET", "/api/v1/ads")
    ads = (resp.get("data") or [])
    if not ads:
        print("❌ 没有素材，无法验证")
        return 2
    ad = ads[0]
    ad_id = ad["id"]
    platform = ad.get("platform") or "xiaohongshu"
    print(f"使用素材: {ad_id}  平台={platform}")

    ok_all = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok_all
        print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
        if not cond:
            ok_all = False

    today = date.today()
    d1 = (today - timedelta(days=2)).isoformat()
    d2 = (today - timedelta(days=1)).isoformat()

    # ---------- 1. 合法数据写入 ----------
    print("\n[1] 回传合法数据")
    for day, imp, clk, conv, spend in [(d1, 10000, 320, 32, 168.0),
                                       (d2, 12000, 420, 46, 210.5)]:
        code, resp = call("POST", "/api/v1/metrics/report", {
            "ad_id": ad_id, "platform": platform, "date": day,
            "impressions": imp, "clicks": clk, "conversions": conv, "spend": spend,
        })
        check(f"{day} 写入成功", code == 200 and resp.get("code") == 200,
              f"HTTP {code} {resp.get('msg','')}")

    # ---------- 2. 幂等：同一天重推应覆盖 ----------
    print("\n[2] 幂等：同 (ad, platform, date) 重推是覆盖不是累加")
    code, resp = call("POST", "/api/v1/metrics/report", {
        "ad_id": ad_id, "platform": platform, "date": d2,
        "impressions": 12000, "clicks": 420, "conversions": 46, "spend": 210.5,
    })
    data = resp.get("data") or {}
    check("重推返回 replaced=True", data.get("replaced") is True, f"{data.get('replaced')}")
    check("累计曝光仍为 22000（未翻倍）", data.get("impressions") == 22000,
          f"impressions={data.get('impressions')}")

    # ---------- 3. 派生指标算式 ----------
    print("\n[3] 派生指标算式")
    # 累计: imp=22000 clk=740 conv=78 spend=378.5
    expect_ctr = round(740 / 22000, 4)
    expect_cvr = round(78 / 740, 4)
    expect_cpa = round(378.5 / 78, 2)
    expect_cpm = round(378.5 / 22000 * 1000, 2)
    check(f"CTR = 740/22000 = {expect_ctr}", data.get("ctr") == expect_ctr, f"得 {data.get('ctr')}")
    check(f"CVR = 78/740 = {expect_cvr}", data.get("cvr") == expect_cvr, f"得 {data.get('cvr')}")
    check(f"CPA = 378.5/78 = {expect_cpa}", data.get("cpa") == expect_cpa, f"得 {data.get('cpa')}")
    check(f"CPM = 378.5/22000*1000 = {expect_cpm}", data.get("cpm") == expect_cpm, f"得 {data.get('cpm')}")

    # ---------- 4. 脏数据必须被拒 ----------
    print("\n[4] 脏数据校收（整条拒收 + 明确原因）")
    bad_cases = [
        ("clicks > impressions", {"impressions": 100, "clicks": 200}),
        ("conversions > clicks", {"impressions": 1000, "clicks": 100, "conversions": 200}),
        ("负数曝光", {"impressions": -5}),
        ("小数曝光", {"impressions": 12.5}),
        ("未来日期", {"impressions": 10, "date": (today + timedelta(days=3)).isoformat()}),
        ("日期格式错", {"impressions": 10, "date": "2026/10/27"}),
        ("布尔值", {"impressions": True}),
        ("素材不存在", {"ad_id": "NOT-EXIST", "impressions": 10}),
    ]
    for label, override in bad_cases:
        payload = {"ad_id": ad_id, "platform": platform, "date": d1,
                   "impressions": 100, "clicks": 10, "conversions": 1, "spend": 1.0}
        payload.update(override)
        code, resp = call("POST", "/api/v1/metrics/report", payload)
        rejected = code == 400 or resp.get("code") == 400
        check(f"拒绝「{label}」", rejected, f"{resp.get('msg','')[:52]}")

    # 批量里有一条坏 → 整批拒收
    code, resp = call("POST", "/api/v1/metrics/report", {"items": [
        {"ad_id": ad_id, "platform": platform, "date": d1, "impressions": 100, "clicks": 10},
        {"ad_id": ad_id, "platform": platform, "date": d1, "impressions": 50, "clicks": 999},
    ]})
    check("批量中有一条非法 → 整批拒收（不写半截）", code == 400 or resp.get("code") == 400,
          f"{resp.get('msg','')[:60]}")

    # ---------- 5. 汇总拆分 ----------
    print("\n[5] 汇总拆分")
    code, resp = call("GET", "/api/v1/metrics/summary?days=7")
    s = resp.get("data") or {}
    overall = s.get("overall") or {}
    check("整体汇总额度正确", overall.get("impressions") == 22000 and overall.get("clicks") == 740,
          f"imp={overall.get('impressions')} clk={overall.get('clicks')}")
    check("按平台拆分包含当前平台", platform in (s.get("by_platform") or {}),
          f"keys={list((s.get('by_platform') or {}).keys())}")
    check("按日拆分有 2 天", len(s.get("by_date") or {}) >= 2,
          f"days={list((s.get('by_date') or {}).keys())}")
    check("按来源拆分（AI vs 人工）", bool(s.get("by_source")),
          f"keys={list((s.get('by_source') or {}).keys())}")

    # ---------- 6. A/B 报告能否用上 ----------
    print("\n[6] A/B 报告（ab_report）是否已用上真实指标")
    code, resp = call("GET", "/api/v1/system/ab-report")
    ab = resp.get("data") or {}
    groups = ab.get("groups") or {}
    has_metrics = any((g.get("impressions") or 0) > 0 for g in groups.values())
    print(f"    ab 分组: {list(groups.keys())}")
    for name, g in groups.items():
        print(f"      {name}: 素材={g.get('materials')} imp={g.get('impressions')} "
              f"ctr={g.get('ctr')} cvr={g.get('cvr')}")
    check("ab_report 已能读到真实曝光", has_metrics,
          "看上面各组 imp 是否非 0")

    print("\n" + "=" * 70)
    print(f"结果：{'全部通过 ✅' if ok_all else '存在失败项 ❌'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
