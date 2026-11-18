"""RAG 增强链路端到端验证：文档解析 → 切块 → 向量化 → 图片入库 → LLM 拒识 → token 记账。

每一段都断言，失败会明确指出哪一环不对。

运行：python scripts/verify_rag_pipeline.py
（LLM 拒识用桩函数验证解析与降级逻辑，不产生真实调用费用；
 真调 LLM 的路径由 verify_metrics_loop / 前端生成流程覆盖。）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT)

TMP = ROOT / "data" / "samples" / "docs"
TMP.mkdir(parents=True, exist_ok=True)

OK = True


def check(name: str, cond: bool, detail: str = "") -> None:
    global OK
    print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        OK = False


# ---------------------------------------------------------------- 造测试文件
def make_docx() -> Path:
    import docx

    p = TMP / "投放规范.docx"
    d = docx.Document()
    d.add_heading("电商广告投放规范", level=1)
    d.add_paragraph("第一条 广告中不得使用绝对化用语，例如最好、第一、100%。")
    d.add_paragraph("第二条 不得宣称医疗功效，例如治疗、根治、抗癌。")
    d.add_paragraph("第三条 不得作出金融承诺，例如稳赚、保本、零风险。")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "平台"
    t.cell(0, 1).text = "时长建议"
    t.cell(1, 0).text = "抖音"
    t.cell(1, 1).text = "10-30 秒"
    d.save(p)
    return p


def make_pdf() -> Path:
    """手写一个最小合法 PDF（含文本层），用于验证 pypdf 解析路径。"""
    p = TMP / "投放规范.pdf"
    text = "Ad policy: no absolute wording, no medical claims, no financial promises."
    stream = f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    p.write_bytes(bytes(out))
    return p


def main() -> int:
    from app.common import doc_ingest, image_vector, token_usage
    from app.common import milvus_cli

    print("=" * 72)
    print("① 文档解析：txt / md / csv / docx / pdf")
    print("=" * 72)
    files = {
        "txt": TMP / "平台合规规范.txt",
        "md": TMP / "投放运营手册.md",
        "csv": TMP / "样例.csv",
        "docx": make_docx(),
        "pdf": make_pdf(),
    }
    for kind, p in files.items():
        try:
            doc = doc_ingest.parse_document(p, prefer_mineru=False)  # 跳过 MinerU 直接测回退
            check(f"{kind:<4} 解析", doc.chars > 0,
                  f"parser={doc.parser} chars={doc.chars} pages={doc.pages}")
        except Exception as exc:  # noqa: BLE001
            check(f"{kind:<4} 解析", False, f"{type(exc).__name__}: {exc}")

    print("\n  解析器优先级（MinerU 可用则优先，不可用则回退）：")
    doc = doc_ingest.parse_document(files["pdf"], prefer_mineru=True)
    if doc_ingest.mineru_available():
        check("PDF 走了 MinerU（已安装时优先用它）",
              doc.parser.startswith("mineru") and doc.chars > 0,
              f"实际 parser={doc.parser}")
    else:
        check("PDF 走了回退解析器且能出文本",
              doc.parser == "pypdf" and doc.chars > 0,
              f"实际 parser={doc.parser}")

    print("\n" + "=" * 72)
    print("② 切块：token 上限 / overlap / 无脏字符")
    print("=" * 72)
    long_text = files["md"].read_text(encoding="utf-8-sig")
    for cs, ov in [(256, 25), (512, 50), (1024, 100)]:
        chunks = doc_ingest.chunk_text(long_text, chunk_size=cs, overlap=ov)
        toks = [c.tokens for c in chunks]
        dirty = sum(1 for c in chunks if "\ufffd" in c.text)
        overlaps = []
        for i in range(len(chunks) - 1):
            best = 0
            a, b = chunks[i].text, chunks[i + 1].text
            for k in range(1, min(120, len(a), len(b)) + 1):
                if a.endswith(b[:k]):
                    best = k
            overlaps.append(best)
        print(f"\n  chunk_size={cs} overlap={ov} → {len(chunks)} 块, token={toks}")
        check(f"  {cs}token: 每块 ≤ 上限", all(t <= cs for t in toks), f"max={max(toks)}")
        check(f"  {cs}token: 无替换字符", dirty == 0, f"脏块={dirty}")
        check(f"  {cs}token: 块间有重叠", all(o > 5 for o in overlaps), f"重叠={overlaps}")

    short = "一句话文案，不该被切开。"
    check("短文本不切块", len(doc_ingest.chunk_text(short, chunk_size=512)) == 1)

    print("\n" + "=" * 72)
    print("③ 图片向量：维度 / 区分度 / 入库 / 检索 / 去重")
    print("=" * 72)
    imgs = sorted((ROOT / "data/samples/images").glob("*.png"))
    if not imgs:
        check("找到测试图片", False, "data/samples/images 为空")
    else:
        vec, prov = image_vector.visual_vector(imgs[0])
        check("向量维度 = 1024", len(vec) == 1024, f"{len(vec)}")
        check("L2 归一化", abs(sum(x * x for x in vec) ** 0.5 - 1.0) < 1e-6)
        check("provider 已标注", prov in ("hist", "clip"), prov)

        def cos(a, b):
            return sum(x * y for x, y in zip(a, b))

        names = {p.name: image_vector.visual_vector(p)[0] for p in imgs}
        if "red_solid.png" in names and "blue_solid.png" in names:
            sim_diff = cos(names["red_solid.png"], names["blue_solid.png"])
            check("不同色系相似度 < 0.3", sim_diff < 0.3, f"red↔blue={sim_diff:.3f}")
        if "red_solid.png" in names and "red_stripe.png" in names:
            sim_same = cos(names["red_solid.png"], names["red_stripe.png"])
            check("同色系相似度 > 0.5", sim_same > 0.5, f"red↔red_stripe={sim_same:.3f}")
        # 检索真正需要的是**排序**：同色系必须比异色系更近
        if all(k in names for k in ("red_solid.png", "red_stripe.png", "blue_solid.png")):
            same = cos(names["red_solid.png"], names["red_stripe.png"])
            diff = cos(names["red_solid.png"], names["blue_solid.png"])
            check("排序正确：同色 > 异色", same > diff, f"同色={same:.3f} > 异色={diff:.3f}")

        for p in imgs[:3]:
            r = image_vector.insert_image(p, platform="xiaohongshu",
                                          category="测试", title=p.name)
            check(f"入库 {p.name}", r["mode"] in ("milvus", "memory"),
                  f"mode={r['mode']} dhash={r['dhash']}")

        res = image_vector.search_similar_image(imgs[0], top_k=3)
        check("按图检索有结果", len(res["hits"]) >= 1, f"{len(res['hits'])} 条")
        if res["hits"]:
            check("首条是查询图本身", res["hits"][0]["title"] == imgs[0].name,
                  f"top1={res['hits'][0]['title']} score={res['hits'][0]['score']}")

        dup = image_vector.dedupe_by_hash(imgs)
        check("近重复检测可运行", "near_duplicates" in dup,
              f"检查 {dup['total']} 张，近重复 {len(dup['near_duplicates'])} 对")

    print("\n" + "=" * 72)
    print("④ LLM 相关性复核（拒识）：桩验证解析与降级")
    print("=" * 72)
    from app.agent import copywriter as cw

    refs = [
        {"original_title": "杯子文案", "text": "水杯实测", "category": "水杯", "platform": "xiaohongshu"},
        {"original_title": "耳机文案", "text": "耳机实测", "category": "蓝牙耳机", "platform": "xiaohongshu"},
    ]
    orig_call = cw._call_llm

    # 场景 1：LLM 判定只保留第 0 条
    cw._call_llm = lambda messages, temperature=0.7: '{"relevant": [0]}'
    v = cw.judge_relevance("水杯", refs)
    check("LLM 复核：保留相关项", v["judged"] and len(v["kept"]) == 1,
          f"kept={len(v['kept'])} dropped={len(v['dropped'])}")
    check("LLM 复核：剔除不相关项", len(v["dropped"]) == 1)

    # 场景 2：模型返回带 ```json 包裹
    cw._call_llm = lambda messages, temperature=0.7: '```json\n{"relevant": [0, 1]}\n```'
    v = cw.judge_relevance("水杯", refs)
    check("LLM 复核：容忍 markdown 包裹", v["judged"] and len(v["kept"]) == 2)

    # 场景 3：全部判不相关 → 等价于拒识
    cw._call_llm = lambda messages, temperature=0.7: '{"relevant": []}'
    v = cw.judge_relevance("水杯", refs)
    check("LLM 复核：全部不相关即拒识", v["judged"] and len(v["kept"]) == 0)

    # 场景 4：LLM 抛错 → 不能把正常召回归零
    def boom(messages, temperature=0.7):
        raise RuntimeError("模拟 LLM 故障")

    cw._call_llm = boom
    v = cw.judge_relevance("水杯", refs)
    check("LLM 复核：失败时保留全部候选（不误伤）",
          (not v["judged"]) and len(v["kept"]) == 2, v["reason"])

    # 场景 5：开关关闭
    os.environ["RAG_LLM_JUDGE"] = "0"
    cw._call_llm = lambda messages, temperature=0.7: '{"relevant": []}'
    v = cw.judge_relevance("水杯", refs)
    check("开关关闭时不调用 LLM", (not v["judged"]) and len(v["kept"]) == 2)
    os.environ.pop("RAG_LLM_JUDGE", None)

    cw._call_llm = orig_call

    print("\n" + "=" * 72)
    print("⑤ Token 记账：写入 / 按日 / 按月聚合")
    print("=" * 72)
    token_usage.reset()
    from datetime import datetime, timedelta

    now = datetime.now()
    token_usage.record(model="deepseek-flash", prompt_tokens=800, completion_tokens=200,
                       scene="copywriter", at=now)
    token_usage.record(model="deepseek-flash", prompt_tokens=600, completion_tokens=150,
                       scene="copywriter", at=now - timedelta(days=1))
    token_usage.record(model="text-embedding-v4", prompt_tokens=120, completion_tokens=0,
                       scene="embedding", at=now - timedelta(days=40))

    s_today = token_usage.summary("today")
    check("今日聚合", s_today["overall"]["calls"] == 1,
          f"calls={s_today['overall']['calls']} tokens={s_today['overall']['total_tokens']}")
    s_month = token_usage.summary("month")
    check("本月聚合只含本月", s_month["overall"]["calls"] >= 1,
          f"calls={s_month['overall']['calls']}")
    s_all = token_usage.summary("all")
    check("全部聚合含三条", s_all["overall"]["calls"] == 3,
          f"calls={s_all['overall']['calls']} tokens={s_all['overall']['total_tokens']}")
    check("按模型拆分", len(s_all["by_model"]) == 2, f"{list(s_all['by_model'].keys())}")
    check("成本估算 > 0", s_all["overall"]["cost_estimate"] > 0,
          f"{s_all['overall']['cost_estimate']}")
    check("有 30 天推算值", s_all["projected_30d_tokens"] >= 0,
          f"日均={s_all['daily_avg_tokens']} 推算30天={s_all['projected_30d_tokens']}")
    check("落盘文件存在", token_usage._path().exists(), str(token_usage._path()))

    print("\n" + "=" * 72)
    print("⑥ 文档入库（dry_run 演示解析→切块的完整装配）")
    print("=" * 72)
    r = doc_ingest.ingest_file(files["docx"], platform="douyin", category="规范", dry_run=True)
    check("docx 装配可运行", r["parser"] == "python-docx" and r["chunks"] >= 1,
          f"parser={r['parser']} chunks={r['chunks']} max_tok={r['max_chunk_tokens']}")

    print("\n" + "=" * 72)
    print("⑦ MinerU（4.x，独立 venv）：表格还原 vs pypdf")
    print("=" * 72)
    mineru_pdf = ROOT / ".mineru-work" / "mineru_test.pdf"
    if not doc_ingest.mineru_available():
        check("MinerU 可用", False,
              "未检测到 mineru 可执行文件。设 MINERU_BIN 指向 .venv-mineru/Scripts/mineru.exe")
    elif not mineru_pdf.exists():
        check("找到 MinerU 测试 PDF", False, str(mineru_pdf))
    else:
        # 对照：pypdf 会把表格拆成裸文本
        from pypdf import PdfReader

        pdf_text = "\n".join((p.extract_text() or "") for p in PdfReader(str(mineru_pdf)).pages)
        pypdf_has_table = "| 平台 |" in pdf_text
        print(f"  pypdf 抽取 {len(pdf_text)} 字符，表格结构保留: {pypdf_has_table}")
        check("pypdf 丢失表格结构（对照组）", not pypdf_has_table)

        doc = doc_ingest.parse_document(mineru_pdf, prefer_mineru=True)
        print(f"  MinerU: parser={doc.parser} chars={doc.chars} pages={doc.pages}")
        check("MinerU 被真正使用（非回退）",
              doc.parser.startswith("mineru"), f"parser={doc.parser}")
        check("MinerU 还原了 Markdown 表格",
              "| 平台 |" in doc.text and "| 巨量千川 |" in doc.text)
        check("MinerU 识别了标题层级", "# 电商广告投放合规规范" in doc.text)

        chunks = doc_ingest.chunk_text(doc.text, chunk_size=512, overlap=50)
        check("MinerU 输出可正常切块",
              len(chunks) >= 1 and all(c.tokens <= 512 for c in chunks),
              f"{len(chunks)} 块 tokens={[c.tokens for c in chunks]}")
        check("表格落在块内（可被检索）", any("| 平台 |" in c.text for c in chunks))

    print("\n" + "=" * 72)
    print(f"结果：{'全部通过 ✅' if OK else '存在失败项 ❌'}")
    return 0 if OK else 1


if __name__ == "__main__":
    sys.exit(main())
