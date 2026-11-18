"""量化评测：RAG 召回质量 + 违禁词检出质量。

这是之前"没做量化评测"的补课。两个评测都基于**已有数据 + 可复现的标注**，
不使用任何人工编造的分数。

================================ RAG 召回 ================================
真值来源：`data/processed/copy_processed.json` 里每条样本自带 `category` 与 `platform`。
         "相关" 定义为 **文档 category == 查询 category**。

两种评测模式：
  filtered：query=category, 过滤 platform, top_k=3   ← 线上真实调用方式
  cross   ：query=category, 不过滤,        top_k=5   ← 跨平台召回上限

指标：Recall@k / Precision@k / MRR / 拒识率（无关查询应返回空）

============================== 违禁词检出 ==============================
真值来源：`data/raw/forbidden_words.txt`（词 + 分类）。
正样本：把违禁词嵌进自然句 → 期望命中
负样本：形近但合法的表达（"最近""最后""如何"）→ 期望不命中
指标：Precision / Recall / F1 / 误报率

运行：python scripts/evaluate_quality.py
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


# ============================ RAG 召回评测 ============================

def load_samples() -> list[dict]:
    path = Path("data/processed/copy_processed.json")
    return json.loads(path.read_text(encoding="utf-8"))


def eval_rag() -> dict:
    from app.common import milvus_cli

    samples = load_samples()
    print(f"样本数: {len(samples)}")

    # 装载知识库（Milvus 不可达时自动走 TF-IDF 内存通道）
    wrapper = milvus_cli._get_instance()
    mode = "milvus" if wrapper._client is not None else "tfidf-fallback"
    milvus_cli.insert_data(samples)
    print(f"检索通道: {mode}")

    # 按 (category, platform) 分组，作为真值索引
    by_cat_plat: dict[tuple[str, str], int] = defaultdict(int)
    by_cat: dict[str, int] = defaultdict(int)
    for s in samples:
        cat = (s.get("category") or "").strip()
        plat = (s.get("platform") or "").strip()
        if not cat:
            continue
        by_cat_plat[(cat, plat)] += 1
        by_cat[cat] += 1

    print(f"类目数: {len(by_cat)}   (类目,平台) 组合数: {len(by_cat_plat)}")

    def run_mode(name: str, top_k: int, use_filter: bool) -> dict:
        recalls, precisions, rrs = [], [], []
        empty_when_should = 0     # 该类目下无其他文档时仍返回结果
        total = 0
        for s in samples:
            cat = (s.get("category") or "").strip()
            plat = (s.get("platform") or "").strip()
            if not cat:
                continue
            filters = {"platform": plat} if use_filter else None
            hits = milvus_cli.search(query_text=cat, top_k=top_k, filters=filters)
            total += 1

            # 真值：同 category 的文档数（排除自己）
            if use_filter:
                relevant_total = max(0, by_cat_plat.get((cat, plat), 0) - 1)
            else:
                relevant_total = max(0, by_cat.get(cat, 0) - 1)

            # 命中判定：返回文档的 category 是否等于查询 category
            hit_flags = [((h.get("category") or "").strip() == cat) for h in hits]
            # 排除"召回到自己"（文本完全相同）
            self_text = (s.get("text") or "").strip()
            hit_flags = [
                flag and ((h.get("text") or "").strip() != self_text)
                for flag, h in zip(hit_flags, hits)
            ]
            n_hit = sum(hit_flags)

            if relevant_total > 0:
                recalls.append(min(1.0, n_hit / relevant_total))
            if hits:
                precisions.append(n_hit / len(hits))
            else:
                precisions.append(0.0)
                if relevant_total > 0:
                    empty_when_should += 1

            # MRR：第一个相关结果的位置
            rr = 0.0
            for idx, flag in enumerate(hit_flags, start=1):
                if flag:
                    rr = 1.0 / idx
                    break
            rrs.append(rr)

        def mean(xs):
            return sum(xs) / len(xs) if xs else 0.0

        return {
            "mode": name,
            "top_k": top_k,
            "queries": total,
            "recall": round(mean(recalls), 4),
            "precision": round(mean(precisions), 4),
            "mrr": round(mean(rrs), 4),
            "empty_on_missing": empty_when_should,
        }

    print("\n--- 评测 ---")
    r_filtered = run_mode("filtered(旧：平台硬过滤)", top_k=3, use_filter=True)
    r_cross = run_mode("cross(无过滤)", top_k=5, use_filter=False)

    # 现行实现：软偏好（跨平台召回后同平台优先）——直接测 copywriter 的真实函数
    from app.agent.copywriter import retrieve_similar_copy

    recalls, precisions, rrs = [], [], []
    for s in samples:
        cat = (s.get("category") or "").strip()
        plat = (s.get("platform") or "").strip()
        if not cat:
            continue
        hits = retrieve_similar_copy(cat, plat, top_k=3)
        relevant_total = max(0, by_cat.get(cat, 0) - 1)
        self_text = (s.get("text") or "").strip()
        flags = [
            ((h.get("category") or "").strip() == cat) and ((h.get("text") or "").strip() != self_text)
            for h in hits
        ]
        n_hit = sum(flags)
        if relevant_total > 0:
            recalls.append(min(1.0, n_hit / relevant_total))
        precisions.append(n_hit / len(hits) if hits else 0.0)
        rr = 0.0
        for idx, flag in enumerate(flags, start=1):
            if flag:
                rr = 1.0 / idx
                break
        rrs.append(rr)

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    r_soft = {
        "mode": "soft(现行：同平台优先)",
        "top_k": 3,
        "queries": len(rrs),
        "recall": round(mean(recalls), 4),
        "precision": round(mean(precisions), 4),
        "mrr": round(mean(rrs), 4),
    }
    r_cross["mode"] = "cross(无过滤)"

    # 拒识率：用一个完全不相关的类目查询，期望返回空
    noise_queries = ["量子计算芯片", "航空航天发动机", "zzz无意义查询", "核磁共振设备", "炼钢高炉"]
    empty_count = 0
    for q in noise_queries:
        hits = milvus_cli.search(query_text=q, top_k=3, filters=None)
        if not hits:
            empty_count += 1
    reject_rate = empty_count / len(noise_queries)

    for r in (r_filtered, r_cross, r_soft):
        print(f"  [{r['mode']}] queries={r['queries']} "
              f"Recall@{r['top_k']}={r['recall']} "
              f"Precision@{r['top_k']}={r['precision']} MRR={r['mrr']}")
    print(f"  [拒识] 无关类目查询 {len(noise_queries)} 个，返回空 {empty_count} 个 "
          f"→ 拒识率={reject_rate:.2%}")

    return {
        "channel": mode,
        "samples": len(samples),
        "categories": len(by_cat),
        "filtered": r_filtered,
        "cross": r_cross,
        "soft": r_soft,
        "reject_rate": round(reject_rate, 4),
    }


# ============================ 违禁词评测 ============================

def build_labeled_set() -> tuple[list[tuple[str, bool, str]], list[str]]:
    """构造标注集。

    正样本：真实广告语里含违禁词（期望命中）
    负样本：形近但合法的日常表达（期望不命中）
    返回 (样本列表, 词表)
    """
    positives = [
        # 极限词
        ("这款耳机是全网最好的音质", True, "最好"),
        ("销量第一的洗发水，闭眼入", True, "第一"),
        ("100%有效，无效退款", True, "100%"),
        ("顶级配方，专柜同款", True, "顶级"),
        ("史上最低价，错过等一年", True, "史上最低价"),
        ("绝对正品，假一赔十", True, "绝对"),
        ("国家级认证产品", True, "国家级"),
        ("唯一官方授权渠道", True, "唯一"),
        # 医疗功效
        ("这款茶可以治疗失眠", True, "治疗"),
        ("坚持使用能根治痘痘", True, "根治"),
        ("具有抗癌功效", True, "抗癌"),
        # 金融承诺
        ("投资稳赚不赔", True, "稳赚"),
        ("保本保收益，零风险", True, "保本"),
        # 伪造资质
        ("政府特供专供商品", True, "政府特供"),
        ("原产地直销，药监局认证", True, "药监局认证"),
    ]
    negatives = [
        # 「最」的日常用法：最近 / 最后 / 最初
        ("最近入手了一款水杯，用着还不错", False, "最近"),
        ("最后我还是选择了这款", False, "最后"),
        ("最初买它是被颜值吸引", False, "最初"),
        # 「第一」的非极限用法
        ("第一次用这种材质的杯子", False, "第一次"),
        ("收到货第一眼就很喜欢", False, "第一眼"),
        # 普通描述
        ("容量 500ml，保温 12 小时", False, "普通"),
        ("这款水杯采用 304 不锈钢内胆", False, "普通"),
        ("适合办公室和户外使用", False, "普通"),
        ("有黑、白、粉三种颜色可选", False, "普通"),
        ("包装很精致，送人合适", False, "普通"),
        # 含「好」但不是极限词
        ("质量不错，值得回购", False, "不错"),
        ("手感很好，颜值也高", False, "很好"),
    ]
    samples = positives + negatives
    return samples, [s[2] for s in samples]


def eval_banned_words() -> dict:
    from app.common import banned_words as bw

    st = bw.status()
    print(f"词表: {st.get('count', '?')} 条, 可用={st.get('available')}")

    samples, _ = build_labeled_set()
    tp = fp = fn = tn = 0
    errors: list[str] = []

    for text, expect_hit, tag in samples:
        try:
            hits = bw.check(text)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"检测异常[{tag}] {text}: {exc}")
            continue
        got_hit = bool(hits)
        if expect_hit and got_hit:
            tp += 1
        elif expect_hit and not got_hit:
            fn += 1
            errors.append(f"漏报[{tag}] {text}")
        elif (not expect_hit) and got_hit:
            fp += 1
            words = [h.get("word") for h in hits]
            errors.append(f"误报[{tag}] {text} → 命中{words}")
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0

    print(f"\n  正样本 {tp + fn} 条 / 负样本 {fp + tn} 条")
    print(f"  命中 TP={tp} 误报 FP={fp} 漏报 FN={fn} 正确排除 TN={tn}")
    print(f"  Precision={precision:.2%}  Recall={recall:.2%}  F1={f1:.2%}  误报率={fpr:.2%}")
    if errors:
        print("  明细:")
        for e in errors:
            print(f"    - {e}")

    return {
        "vocabulary": st.get("count"),
        "positives": tp + fn,
        "negatives": fp + tn,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_positive_rate": round(fpr, 4),
        "errors": errors,
    }


def main() -> int:
    print("=" * 68)
    print("量化评测 ① RAG 召回质量")
    print("=" * 68)
    rag = eval_rag()

    print("\n" + "=" * 68)
    print("量化评测 ② 违禁词检出质量")
    print("=" * 68)
    banned = eval_banned_words()

    out = Path("data/reports/quality_eval.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rag": rag, "banned_words": banned},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {out}")

    # 判定
    ok = True
    if rag["soft"]["precision"] < 0.5:
        print(f"⚠️  RAG 现行实现 Precision@3 = {rag['soft']['precision']:.2%}，低于 50%"); ok = False
    if rag["reject_rate"] < 0.8:
        print(f"⚠️  无关查询拒识率 = {rag['reject_rate']:.2%}，低于 80%"); ok = False
    if banned["precision"] < 0.8:
        print(f"⚠️  违禁词 Precision = {banned['precision']:.2%}，低于 80%"); ok = False
    if banned["recall"] < 0.8:
        print(f"⚠️  违禁词 Recall = {banned['recall']:.2%}，低于 80%"); ok = False
    print(f"\n{'✅ 评测通过' if ok else '❌ 存在不达标项'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
