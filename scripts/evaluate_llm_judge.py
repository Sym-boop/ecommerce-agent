"""实测第二级拒识（LLM 语义复核）——v2：修正自我排除 + 加对抗测试。

**为什么要有 v2**

v1 直接对每个 (类目, 平台) 取 `retrieve_similar_copy` 再复核，得出「252 条候选全部
同类目、复核全保留」。但这个结论有两个问题：

1. **没排除"召回到自己"**：`evaluate_quality.py` 判定命中时会剔除文本完全相同的
   那条（自己），v1 没剔 → P@3 被虚高到 100%
2. **没有可剔除的对象**：TF-IDF 通道下检索本身就只返回同词文档，第二级无事可做，
   于是"判别力"完全没被检验

所以 v2 分两段：

- **A 段 真实链路**：按样本逐个评测（排除自己），看复核前后的真实变化
- **B 段 对抗注入**：人为把「错误类目」的参考混进候选，看 LLM 能否挑出来。
  这一段才真正回答"第二级有没有用"和"会不会误杀"。

真值：`文档类目 == 查询类目`。

用法：
    python scripts/evaluate_llm_judge.py            # A+B 都跑
    python scripts/evaluate_llm_judge.py --only-b   # 只跑对抗测试（省 token）
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


def _text(d: dict) -> str:
    return (d.get("text") or "").strip()


# ----------------------------------------------------------------------
# A 段：真实链路（排除自己）
# ----------------------------------------------------------------------
def phase_a(samples: list[dict], top_k: int = 3) -> dict:
    from app.agent import copywriter as cw

    # 候选集只由 (类目, 平台, top_k) 决定 → 复核结论可缓存，避免重复烧 token
    cache: dict[tuple[str, str], dict] = {}
    stat = defaultdict(int)
    p_before, p_after = [], []

    for s in samples:
        cat, plat = _cat(s), (s.get("platform") or "").strip()
        if not cat:
            continue
        key = (cat, plat)
        if key not in cache:
            refs = cw.retrieve_similar_copy(cat, plat, top_k=top_k)
            verdict = cw.judge_relevance(cat, refs) if refs else {
                "kept": [], "dropped": [], "judged": False, "reason": "无候选"}
            cache[key] = {"refs": refs, **verdict}

        refs = [r for r in cache[key]["refs"] if _text(r) != _text(s)]   # 排除自己
        kept = [r for r in cache[key]["kept"] if _text(r) != _text(s)]

        if not refs:
            stat["无候选(排除自己后)"] += 1
            continue

        for r in refs:
            stat["A_候选"] += 1
            if _cat(r) == cat:
                stat["A_候选_正确"] += 1
        for r in cache[key]["dropped"]:
            if _text(r) == _text(s):
                continue
            stat["A_剔除"] += 1
            if _cat(r) == cat:
                stat["A_剔除_正确"] += 1     # ← 误杀

        def prec(rows):
            return (sum(1 for r in rows if _cat(r) == cat) / len(rows)) if rows else 0.0

        p_before.append(prec(refs))
        if kept:
            p_after.append(prec(kept))
        else:
            p_after.append(0.0)
            stat["A_整组拒识"] += 1

    return {
        "stat": dict(stat),
        "p_before": sum(p_before) / len(p_before) if p_before else 0.0,
        "p_after": sum(p_after) / len(p_after) if p_after else 0.0,
        "n": len(p_before),
    }


# ----------------------------------------------------------------------
# B 段：对抗注入（人为混入错误类目）
# ----------------------------------------------------------------------
def phase_b(samples: list[dict], limit: int = 20) -> dict:
    """每个查询：1 条同品类（应保留）+ 1 条不同品类（应剔除）。

    正确行为 = 保留同类目、剔除异类目。
    - 把异类目**保留**了 → 判别失败（漏放）
    - 把同类目**剔除**了 → 误杀
    """
    from app.agent import copywriter as cw

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        if _cat(s):
            by_cat[_cat(s)].append(s)
    cats = [c for c, v in by_cat.items() if len(v) >= 2]
    random.seed(42)
    random.shuffle(cats)
    cats = cats[:limit]

    stat = defaultdict(int)
    detail = []

    for cat in cats:
        good = dict(by_cat[cat][0])
        good["_truth"] = "same"
        # 挑一个别的类目
        other_cat = random.choice([c for c in by_cat if c != cat])
        bad = dict(by_cat[other_cat][0])
        bad["_truth"] = "diff"

        refs = [good, bad]
        verdict = cw.judge_relevance(cat, refs)
        kept_ids = {id(r) for r in verdict["kept"]}
        dropped_ids = {id(r) for r in verdict["dropped"]}

        kept_good = id(good) in kept_ids
        dropped_bad = id(bad) in dropped_ids
        stat["B_用例"] += 1
        if kept_good and dropped_bad:
            stat["B_完全正确"] += 1
        if not kept_good:
            stat["B_误杀正确参考"] += 1
        if not dropped_bad:
            stat["B_漏放错误参考"] += 1
        if not verdict["judged"]:
            stat["B_复核未生效"] += 1

        detail.append({
            "category": cat,
            "good_category": _cat(good),
            "bad_category": other_cat,
            "kept_good": kept_good,
            "dropped_bad": dropped_bad,
            "judged": verdict["judged"],
        })

    return {"stat": dict(stat), "detail": detail}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--limit-b", type=int, default=20, help="对抗测试用例数")
    ap.add_argument("--only-b", action="store_true")
    args = ap.parse_args()

    from app.common import milvus_cli

    samples = load_samples()
    wrapper = milvus_cli._get_instance()
    milvus_cli.insert_data(samples)
    print(f"样本数: {len(samples)} | 检索通道: "
          f"{'milvus' if wrapper._client is not None else 'tfidf-fallback'}")
    print("=" * 72)

    report: dict = {}

    if not args.only_b:
        print("【A 段】真实链路（已排除「召回到自己」）")
        a = phase_a(samples, top_k=args.top_k)
        st = a["stat"]
        print(f"  参与评测样本        : {a['n']}")
        print(f"  候选总数            : {st.get('A_候选', 0)}")
        print(f"  其中同类目          : {st.get('A_候选_正确', 0)}")
        print(f"  复核剔除            : {st.get('A_剔除', 0)}"
              f"  (其中同类目 = 误杀: {st.get('A_剔除_正确', 0)})")
        print(f"  整组被拒识          : {st.get('A_整组拒识', 0)}")
        print(f"  复核前 P@{args.top_k}（按样本平均）  : {a['p_before']:.2%}")
        print(f"  复核后 P@{args.top_k}（按样本平均）  : {a['p_after']:.2%}")
        print(f"  变化                : {a['p_after']-a['p_before']:+.2%}")
        report["A"] = a

    print()
    print(f"【B 段】对抗注入（{args.limit_b} 个用例：每例 1 条同类目 + 1 条异类目）")
    b = phase_b(samples, limit=args.limit_b)
    st = b["stat"]
    n = st.get("B_用例", 0)
    print(f"  用例数              : {n}")
    print(f"  完全正确（保留对+剔除错）: {st.get('B_完全正确', 0)}/{n}"
          f" = {st.get('B_完全正确',0)/max(1,n):.1%}")
    print(f"  ⚠️ 误杀（同类目被剔除）  : {st.get('B_误杀正确参考', 0)}/{n}"
          f" = {st.get('B_误杀正确参考',0)/max(1,n):.1%}")
    print(f"  ⚠️ 漏放（异类目被保留）  : {st.get('B_漏放错误参考', 0)}/{n}"
          f" = {st.get('B_漏放错误参考',0)/max(1,n):.1%}")
    print(f"  复核未生效          : {st.get('B_复核未生效', 0)}")
    report["B"] = b

    out = ROOT / "data" / "reports" / "llm_judge_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已保存: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
