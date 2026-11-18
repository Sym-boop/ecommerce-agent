"""Reranker 的 A/B 评测。

**为什么不能只看 P@3**

在 TF-IDF 降级通道上，召回候选**本来就全是同类目**（无关的被 0.05 阈值清掉了），
所以 P@3 已经是 100%——精排再怎么排也提不上去。

精排真正起作用的是**候选里混了错类目**的时候：向量的粗召回会把
"字面相近但不同品类"的文档带进来，这时候谁排第一就很关键。

所以这个脚本测两段：

- **A 段 真实召回**：P@3 / Recall@3 / MRR，开 vs 关精排（预期：持平）
- **B 段 对抗混合**：每个查询给「1 条同类目 + 2 条异类目」，看 top-1 命中率
  与 MRR 的变化（预期：显著提升）

用法：
    python scripts/evaluate_rerank.py            # A+B
    python scripts/evaluate_rerank.py --only-b   # 只跑对抗段（快）
"""

from __future__ import annotations

import argparse
import json
import os
import random
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


def _cat(d: dict) -> str:
    return (d.get("category") or "").strip()


def metrics(rows: list[tuple[str, list[dict]]]) -> dict:
    """rows: [(查询类目, 返回列表)]"""
    p_list, r_list, rr_list = [], [], []
    for cat, docs in rows:
        flags = [1 if _cat(d) == cat else 0 for d in docs]
        p_list.append(sum(flags) / len(flags) if flags else 0.0)
        r_list.append(1.0 if any(flags) else 0.0)      # recall@k：有没有命中
        rr = 0.0
        for i, f in enumerate(flags, 1):
            if f:
                rr = 1.0 / i
                break
        rr_list.append(rr)
    n = max(1, len(rows))
    return {
        "n": len(rows),
        "precision": round(sum(p_list) / n, 4),
        "recall": round(sum(r_list) / n, 4),
        "mrr": round(sum(rr_list) / n, 4),
        "top1_acc": round(sum(1 for _, d in rows if d and _cat(d[0]) == _) / n, 4),
    }


def phase_a(samples: list[dict], top_k: int = 3) -> tuple[dict, dict]:
    """真实召回：开 vs 关精排。"""
    from app.common import milvus_cli, rerank

    milvus_cli.insert_data(samples)
    pairs, seen = [], set()
    for s in samples:
        cat, plat = _cat(s), (s.get("platform") or "").strip()
        if cat and (cat, plat) not in seen:
            seen.add((cat, plat))
            pairs.append((cat, plat))

    def run(enabled: bool):
        os.environ["RAG_RERANK"] = "1" if enabled else "0"
        # 直接调底层，绕过分平台重排，只看精排本身
        out = []
        for cat, plat in pairs:
            cands = milvus_cli.search(query_text=cat, top_k=top_k, filters=None)
            if not cands:
                out.append((cat, []))
                continue
            if enabled:
                out.append((cat, rerank.rerank(cat, cands, top_k=top_k)["docs"]))
            else:
                out.append((cat, cands[:top_k]))
        return metrics(out)

    off = run(False)
    on = run(True)
    os.environ.pop("RAG_RERANK", None)
    return off, on


def phase_b(samples: list[dict], limit: int = 40) -> tuple[dict, dict]:
    """对抗混合：1 条同类目 + 2 条异类目，看 top-1 是否排对。"""
    from app.common import rerank

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        if _cat(s):
            by_cat[_cat(s)].append(s)
    cats = [c for c, v in by_cat.items() if len(v) >= 2]
    random.seed(7)
    random.shuffle(cats)
    cats = cats[:limit]

    cases = []
    for cat in cats:
        good = by_cat[cat][0]
        others = [c for c in by_cat if c != cat]
        bad1 = by_cat[random.choice(others)][0]
        bad2 = by_cat[random.choice(others)][0]
        docs = [dict(good), dict(bad1), dict(bad2)]
        random.shuffle(docs)          # 打乱，让 baseline 的 top-1 接近随机
        cases.append((cat, docs))

    # baseline：保持打乱后的顺序
    base_rows = [(cat, docs[:3]) for cat, docs in cases]
    # 精排后
    rr_rows = []
    for cat, docs in cases:
        out = rerank.rerank(cat, docs, top_k=3)
        rr_rows.append((cat, out["docs"]))
    return metrics(base_rows), metrics(rr_rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-b", action="store_true")
    ap.add_argument("--limit-b", type=int, default=40)
    args = ap.parse_args()

    from app.common import rerank

    print("=" * 66)
    print("Reranker A/B 评测")
    print("=" * 66)
    st = rerank.status()
    print(f"模型目录: {st['model_dir']}")
    print(f"可用: {st['available']}  开关: {st['enabled']}")

    samples = load_samples()
    print(f"样本数: {len(samples)}\n")

    report: dict = {}

    if not args.only_b:
        print("【A 段】真实召回（开 vs 关 精排）")
        off, on = phase_a(samples)
        print(f"  样本组合 {off['n']}")
        print(f"  {'指标':<12}{'关精排':>10}{'开精排':>10}{'变化':>10}")
        for k, label in (("precision", "P@3"), ("recall", "Recall@3"),
                         ("mrr", "MRR"), ("top1_acc", "Top1 命中")):
            d = on[k] - off[k]
            print(f"  {label:<12}{off[k]:>10.2%}{on[k]:>10.2%}{d:>+10.2%}")
        report["A"] = {"off": off, "on": on}

    print()
    print(f"【B 段】对抗混合（每例 1 同类目 + 2 异类目，共 {args.limit_b} 例）")
    b_off, b_on = phase_b(samples, limit=args.limit_b)
    print(f"  {'指标':<12}{'关精排':>10}{'开精排':>10}{'变化':>10}")
    for k, label in (("top1_acc", "Top1 命中"), ("mrr", "MRR"),
                     ("precision", "P@3")):
        d = b_on[k] - b_off[k]
        print(f"  {label:<12}{b_off[k]:>10.2%}{b_on[k]:>10.2%}{d:>+10.2%}")
    report["B"] = {"off": b_off, "on": b_on}

    out = ROOT / "data" / "reports" / "rerank_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
