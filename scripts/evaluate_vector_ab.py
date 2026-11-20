"""RAG 召回层 A/B：TF-IDF vs 本地向量，叠加精排开关（2×2）。

**为什么要做这个实验**

项目此前所有 RAG 数字（P@3 = 66.67% / 67.82%）都来自 **TF-IDF 降级通道** ——
Milvus 在演示环境连不通，而 Windows 没有 milvus-lite、机器也没装 Docker。
所以「向量检索」这件事在数字上从来没被验证过。

这个脚本把本地向量索引（bge-m3 + numpy 余弦）接进来，用**同一批样本、
同一套指标口径**做四组对照，把「向量到底有没有用」变成一个可以量化的结论。

**指标口径**（与 `evaluate_quality.py` 完全一致，不能另造一套）

    真值 relevant_total = 同类目文档数 - 1（排除查询自身）
    命中 = 返回文档 category 与查询相同，且文本不等于查询文本（排除自召回）
    Precision = n_hit / len(hits)
    Recall    = min(1, n_hit / relevant_total)
    RR        = 1 / 第一个命中的位置

**四组**：
    1. tfidf  + 无精排     ← 纯字面检索
    2. vector + 无精排     ← 纯语义检索（本脚本新增的能力）
    3. tfidf  + 精排       ← 现行默认
    4. vector + 精排       ← 若 2 优于 1，这组才值得作为新默认

运行：python scripts/evaluate_vector_ab.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
os.chdir(_ROOT)


def load_samples() -> list[dict]:
    path = _ROOT / "data" / "processed" / "copy_processed.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("items") or []
    return [s for s in items if (s.get("category") or "").strip()]


def metrics(rows: list[tuple[str, str, list[dict]]]) -> dict:
    """按官方口径算 P / R / MRR / 空召回数。

    rows: [(category, self_text, hits), ...]
    """
    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    precisions, recalls, rrs = [], [], []
    empty = 0
    for hits in (r[2] for r in rows):
        if not hits:
            empty += 1

    by_cat: dict[str, int] = defaultdict(int)
    for cat, _self, _hits in rows:
        by_cat[cat] += 1

    for cat, self_text, hits in rows:
        relevant_total = max(0, by_cat.get(cat, 0) - 1)
        flags = [
            ((h.get("category") or "").strip() == cat)
            and ((h.get("text") or "").strip() != self_text)
            for h in hits
        ]
        n_hit = sum(flags)
        precisions.append(n_hit / len(hits) if hits else 0.0)
        if relevant_total > 0:
            recalls.append(min(1.0, n_hit / relevant_total))
        rr = 0.0
        for idx, flag in enumerate(flags, start=1):
            if flag:
                rr = 1.0 / idx
                break
        rrs.append(rr)

    return {
        "queries": len(rows),
        "precision": round(mean(precisions), 4),
        "recall": round(mean(recalls), 4),
        "mrr": round(mean(rrs), 4),
        "empty_recall": empty,
    }


def run(backend: str, use_rerank: bool, samples: list[dict], top_k: int = 3) -> dict:
    """跑一组：设定召回后端与精排开关，逐条查询。"""
    from app.agent import copywriter
    from app.common import rerank as rerank_mod

    os.environ["VECTOR_BACKEND"] = backend
    # rerank 的开关在 import 时读到模块常量里，直接改变量比改 env 可靠
    rerank_mod._ENABLED = bool(use_rerank)

    # 统计「精排是否真的执行了」。
    # 不加这个计数就会踩坑：rerank 加载失败时是 **fail-open**（保持原顺序返回），
    # 结果看起来「精排无增益」，其实是根本没跑（实测已经踩过一次）。
    applied = {"reranked": 0, "fallback": 0}
    _orig_rerank = rerank_mod.rerank

    def _counting_rerank(*args, **kwargs):
        out = _orig_rerank(*args, **kwargs)
        if out.get("reranked"):
            applied["reranked"] += 1
        else:
            applied["fallback"] += 1
        return out

    rerank_mod.rerank = _counting_rerank

    rows = []
    t0 = time.time()
    for s in samples:
        cat = (s.get("category") or "").strip()
        plat = (s.get("platform") or "").strip()
        me = (s.get("text") or "").strip()
        try:
            hits = copywriter.retrieve_similar_copy(cat, plat, top_k=top_k)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ 查询失败（{cat}）：{exc}")
            hits = []
        rows.append((cat, me, hits))
    elapsed = time.time() - t0
    rerank_mod.rerank = _orig_rerank
    result = metrics(rows)
    result.update({
        "backend": backend,
        "rerank": use_rerank,
        # 精排真正的执行次数：0 说明开关开了但没跑起来
        "rerank_applied": applied["reranked"],
        "rerank_fallback": applied["fallback"],
        "seconds": round(elapsed, 1),
        "ms_per_query": round(1000 * elapsed / max(1, len(rows)), 1),
    })
    return result


def main() -> int:
    samples = load_samples()
    cats = {(s.get("category") or "").strip() for s in samples}
    print("=" * 68)
    print("RAG 召回 A/B：TF-IDF vs 本地向量（bge-m3 + 余弦）")
    print("=" * 68)
    print(f"样本 {len(samples)} 条 / 类目 {len(cats)} 个")

    # 先确认 embedding 可用（否则 vector 组会退化成 TF-IDF，结论就假了）
    from app.common import embeddings
    from app.common import vector_store

    desc = embeddings.describe()
    print(f"embedding: source={desc['source']} model={desc['model']}")

    if desc["source"] == "local":
        print("首次构建本地索引需要加载模型（约 30–60 秒），请稍候…")

    print("\n--- 预热本地索引 ---")
    t0 = time.time()
    try:
        docs = __import__("app.agent.copywriter", fromlist=["_load_corpus"])._load_corpus()
        info = vector_store.LocalVectorIndex(docs).build()
        print(f"  索引就绪：{info.get('docs')} 条 / {info.get('dim')} 维 / "
              f"{'重建' if info.get('built') else '缓存命中'} / {time.time()-t0:.1f}s")
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 本地索引构建失败：{exc}")
        print("  没有向量索引时，vector 组会退化成字面检索，结论不可信 —— 中止。")
        return 1

    modes = [
        ("tfidf", False, "TF-IDF · 无精排"),
        ("local", False, "本地向量 · 无精排"),
        ("tfidf", True, "TF-IDF · 精排"),
        ("local", True, "本地向量 · 精排"),
    ]
    results = []
    print("\n--- 四组对照 ---")
    for backend, use_rerank, label in modes:
        r = run(backend, use_rerank, samples)
        results.append({**r, "label": label})
        applied = f"精排执行 {r['rerank_applied']}/{r['queries']}"
        print(f"  {label:<20} P@3={r['precision']:.2%}  R@3={r['recall']:.2%}  "
              f"MRR={r['mrr']:.4f}  空召回={r['empty_recall']}  "
              f"{r['ms_per_query']}ms/query  {applied if use_rerank else ''}")

    print("\n" + "=" * 68)
    print("结论对照（P@3 越高越好，MRR 越接近 1 越好）")
    print("=" * 68)
    print(f"{'组':<22}{'P@3':>9}{'R@3':>9}{'MRR':>9}{'空召回':>8}{'ms/query':>11}")
    for r in results:
        print(f"{r['label']:<22}{r['precision']:>8.2%}{r['recall']:>9.2%}"
              f"{r['mrr']:>9.4f}{r['empty_recall']:>8}{r['ms_per_query']:>11}")

    # 关键增量：向量 vs TF-IDF（同精排条件）
    import statistics
    base_no = results[0]
    vec_no = results[1]
    base_re = results[2]
    vec_re = results[3]
    if any(r["rerank"] and r["rerank_applied"] == 0 for r in results):
        print("\n⚠️ 精排组执行数为 0 —— 精排没有真的跑起来（fail-open），"
              "这两行的精排结论**不可用**，先修模型加载再看。")

    print("\n--- 增量 ---")
    # 一致性校验：如果两种召回的指标完全一样，很可能是后端名写错、
    # 两组跑的是同一条路径（本次就踩过：脚本传 vector，而 _recall 只认 local）
    same = (results[0]["precision"] == results[1]["precision"]
            and results[0]["mrr"] == results[1]["mrr"]
            and results[0]["empty_recall"] == results[1]["empty_recall"])
    if same:
        print("\n⚠️ 向量组与 TF-IDF 组指标完全相同 —— 很可能两组走了同一条路径，"
              "先确认 VECTOR_BACKEND 取值（合法值：milvus / local / tfidf）。")

    print(f"  纯召回层：向量 - TF-IDF     P@3 {vec_no['precision']-base_no['precision']:+.2%}  "
          f"MRR {vec_no['mrr']-base_no['mrr']:+.4f}")
    print(f"  叠加精排：向量 - TF-IDF     P@3 {vec_re['precision']-base_re['precision']:+.2%}  "
          f"MRR {vec_re['mrr']-base_re['mrr']:+.4f}")
    print(f"  精排增益（向量基础上）      P@3 {vec_re['precision']-vec_no['precision']:+.2%}  "
          f"MRR {vec_re['mrr']-vec_no['mrr']:+.4f}")

    out = _ROOT / "data" / "reports" / "vector_ab.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "samples": len(samples),
        "categories": len(cats),
        "embedding": desc,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {out.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
