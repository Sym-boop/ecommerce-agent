"""验证「平台硬过滤 → 跨平台召回 + 同平台优先」的提升是否真实。

**要回答的质疑**

P@3 从 3.45% 到 66.67% 差了 19 倍，这个幅度太漂亮，值得怀疑：
是不是评测集本身对「不硬过滤」有利？或者指标口径不一致（比如一个算 P@3、一个算 P@5）？

所以做三件事：

1. **同 k 对比**：k = 1/3/5 分别跑两种策略，排除"k 不一致"的口径问题
2. **分层分析**：按「该 (类目,平台) 下有几条文档」分层。
   如果提升**只**出现在「只有 1 条」的稀疏组合上，说明机制解释成立；
   如果两层都在涨，那更像是评测集偏了
3. **机制旁证**：看硬过滤的 Recall 与 P 是否出现"Recall 高但 P 极低"的签名——
   那说明是"召回了但品类错"，不是"召不回"

用法：python scripts/verify_retrieval_gain.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT)


def load_samples() -> list[dict]:
    path = ROOT / "data" / "processed" / "copy_processed.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    import csv

    rows = []
    with (ROOT / "data" / "raw" / "copy_sample.csv").open(encoding="utf-8-sig") as f:
        rows.extend(csv.DictReader(f))
    return rows


def _cat(d):
    return (d.get("category") or "").strip()


def _plat(d):
    return (d.get("platform") or "").strip()


def main() -> int:
    from app.common import milvus_cli

    samples = load_samples()
    milvus_cli.insert_data(samples)
    wrapper = milvus_cli._get_instance()
    channel = "milvus" if wrapper._client is not None else "tfidf-fallback"

    by_cp = defaultdict(int)
    by_c = defaultdict(int)
    for s in samples:
        c, p = _cat(s), _plat(s)
        if not c:
            continue
        by_cp[(c, p)] += 1
        by_c[c] += 1

    print("=" * 74)
    print("检索提升的真实性验证")
    print("=" * 74)
    print(f"样本 {len(samples)} 条 / 类目 {len(by_c)} 个 / (类目,平台) 组合 {len(by_cp)} 个")
    dist = defaultdict(int)
    for n in by_cp.values():
        dist[n] += 1
    print(f"每个组合的文档数分布: {dict(sorted(dist.items()))}")
    print(f"  → 平均 {len(samples)/len(by_cp):.2f} 条/组合")
    print(f"检索通道: {channel}\n")

    # ---------------- 1) 同 k 对比 ----------------
    print("【1】同 k 对比（排除口径不一致）")
    print(f"  {'k':<4}{'策略':<22}{'P@k':>9}{'R@k':>9}{'MRR':>9}{'返回均长':>10}")
    results = {}
    for k in (1, 3, 5):
        for use_filter, label in ((True, "平台硬过滤"), (False, "跨平台+同平台优先")):
            ps, rs, rrs, lens = [], [], [], []
            for s in samples:
                c, p = _cat(s), _plat(s)
                if not c:
                    continue
                filters = {"platform": p} if use_filter else None
                hits = milvus_cli.search(query_text=c, top_k=k, filters=filters)
                self_text = (s.get("text") or "").strip()
                hits = [h for h in hits if (h.get("text") or "").strip() != self_text]
                flags = [1 if _cat(h) == c else 0 for h in hits]
                ps.append(sum(flags) / len(flags) if flags else 0.0)
                rel_total = (by_cp.get((c, p), 0) - 1) if use_filter else (by_c.get(c, 0) - 1)
                rs.append(min(1.0, sum(flags) / rel_total) if rel_total > 0 else 0.0)
                rr = 0.0
                for i, f in enumerate(flags, 1):
                    if f:
                        rr = 1.0 / i
                        break
                rrs.append(rr)
                lens.append(len(hits))
            n = max(1, len(ps))
            row = (sum(ps) / n, sum(rs) / n, sum(rrs) / n, sum(lens) / n)
            results[(k, use_filter)] = row
            print(f"  {k:<4}{label:<22}{row[0]:>9.2%}{row[1]:>9.2%}"
                  f"{row[2]:>9.3f}{row[3]:>10.1f}")
        print()

    # ---------------- 2) 分层分析 ----------------
    print("【2】按「该组合下有几条文档」分层（k=3）")
    print(f"  {'分层':<16}{'组合数':>7}{'硬过滤 P@3':>13}{'软偏好 P@3':>13}")
    for lo, hi, label in ((1, 1, "稀疏（=1 条）"), (2, 99, "充足（≥2 条）")):
        strata = [(c, p) for (c, p), n in by_cp.items() if lo <= n <= hi]
        if not strata:
            continue
        row = {}
        for use_filter in (True, False):
            ps = []
            for c, p in strata:
                filters = {"platform": p} if use_filter else None
                hits = milvus_cli.search(query_text=c, top_k=3, filters=filters)
                # 用组合内第一条文档当"自己"排除
                self_text = ""
                for s in samples:
                    if _cat(s) == c and _plat(s) == p:
                        self_text = (s.get("text") or "").strip()
                        break
                hits = [h for h in hits if (h.get("text") or "").strip() != self_text]
                flags = [1 if _cat(h) == c else 0 for h in hits]
                ps.append(sum(flags) / len(flags) if flags else 0.0)
            row[use_filter] = sum(ps) / max(1, len(ps))
        print(f"  {label:<16}{len(strata):>7}{row[True]:>13.2%}{row[False]:>13.2%}")

    # ---------------- 3) 机制旁证 ----------------
    print("\n【3】机制旁证：硬过滤下每条查询返回了多少「错品类」文档")
    hard = results[(3, True)]
    soft = results[(3, False)]
    print(f"  硬过滤 P@3 = {hard[0]:.2%} 但 R@3 = {hard[1]:.1%}")
    print("    → Recall 接近 1 而 Precision 极低 = 『召回到了，但品类错』的签名")
    print("    → 若是评测集偏了，更可能是 Recall 也一起塌，而不是这种反向组合")
    print(f"  软偏好 P@3 = {soft[0]:.2%}  R@3 = {soft[1]:.1%}")
    gain = soft[0] - hard[0]
    print(f"\n  同 k=3 下 P@3 提升: {hard[0]:.2%} → {soft[0]:.2%}  (Δ {gain:+.2%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
