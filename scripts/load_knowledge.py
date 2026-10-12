"""知识库批量导入脚本（企业化，统一走 app.common.milvus_cli）

把清洗好的爆款文案向量化并写入 Milvus 的 knowledge_base 集合。

数据来源（按优先级自动选择）:
    data/processed/copy_processed.json   已清洗，字段: text/platform/category/original_title...
    data/raw/copy_sample.csv             原始，字段: title/content/platform/product_category/tags

用法（项目根目录）:
    python scripts/load_knowledge.py                 # 自动选择数据源；集合非空则拒绝以免重复
    python scripts/load_knowledge.py --recreate      # 清空集合后重灌（可重复执行，结果一致）
    python scripts/load_knowledge.py --append        # 追加到现有集合
    python scripts/load_knowledge.py --input data/raw/copy_sample.csv --batch-size 20
    python scripts/load_knowledge.py --verify        # 灌库后跑一次真实检索自检

依赖: 先运行 scripts/init_milvus.py；向量化走 EMBEDDING_API_BASE/EMBEDDING_API_KEY。
      若 Milvus / Embedding 不可用，脚本会明确报错并以退出码 1 结束（不会静默降级）。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.common import milvus_cli  # noqa: E402


# ---------------------------------------------------------------------------
# 数据源解析与归一化
# ---------------------------------------------------------------------------

def _normalize_processed(records: list[dict]) -> list[dict]:
    """processed JSON 已是目标结构，仅补齐 / 规整字段。"""
    out = []
    for r in records:
        text = str(r.get("text", "")).strip()
        if not text:
            continue
        item = dict(r)
        item["text"] = text
        item.setdefault("platform", r.get("platform", ""))
        item.setdefault("category", r.get("category", ""))
        item.setdefault("original_title", r.get("original_title", ""))
        item.setdefault("source", "copywriting")
        out.append(item)
    return out


def _normalize_raw_csv(rows: list[dict]) -> list[dict]:
    """原始 CSV -> 与 processed 一致的 text 结构（类目/平台/标题/正文）。"""
    out = []
    for r in rows:
        title = str(r.get("title", "")).strip()
        content = str(r.get("content", "")).strip()
        if not (title or content):
            continue
        platform = str(r.get("platform", "")).strip()
        category = str(r.get("product_category", "")).strip()
        text = f"类目:{category} 平台:{platform} 标题:{title} 正文:{content}".strip()
        out.append({
            "text": text,
            "platform": platform,
            "category": category,
            "original_title": title,
            "original_content": content,
            "tags": str(r.get("tags", "")).strip(),
            "source": "copywriting",
        })
    return out


def _read_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):  # 允许 {"data": [...]} 包装
        data = data.get("data", [])
    return list(data)


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def resolve_source(input_arg: str | None) -> tuple[Path, str]:
    """返回 (数据文件路径, 解析类型 'processed'|'raw')。"""
    if input_arg:
        p = Path(input_arg)
        if not p.is_file():
            raise FileNotFoundError(f"数据文件不存在: {p}")
        return p, ("processed" if p.suffix.lower() == ".json" else "raw")

    candidates = [
        (PROJECT_ROOT / "data/processed/copy_processed.json", "processed"),
        (PROJECT_ROOT / "data/raw/copy_sample.csv", "raw"),
    ]
    for p, kind in candidates:
        if p.is_file():
            return p, kind
    raise FileNotFoundError("未找到可用数据源（data/processed/copy_processed.json 或 data/raw/copy_sample.csv）")


def load_records(input_arg: str | None = None) -> tuple[list[dict], Path]:
    path, kind = resolve_source(input_arg)
    raw = _read_json(path) if kind == "processed" else _read_csv(path)
    records = _normalize_processed(raw) if kind == "processed" else _normalize_raw_csv(raw)
    print(f"数据源: {path}（{kind}），有效记录 {len(records)} 条")
    return records, path


# ---------------------------------------------------------------------------
# 灌库
# ---------------------------------------------------------------------------

def load_dataset(
    input_arg: str | None = None,
    batch_size: int = 50,
    recreate: bool = False,
    append: bool = False,
    verify: bool = False,
) -> int:
    records, _ = load_records(input_arg)
    if not records:
        print("[错误] 没有可导入的有效记录。", file=sys.stderr)
        return 1

    wrapper = milvus_cli.get_wrapper()
    print(f"Milvus 地址: {wrapper._uri}  集合: {wrapper.COLLECTION}")

    try:
        wrapper.client  # 触发连接
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 无法连接 Milvus（{exc}）", file=sys.stderr)
        return 1
    if wrapper._embedding_fn is None:
        print("[错误] 未配置可用 Embedding（EMBEDDING_API_BASE/KEY），无法向量化。", file=sys.stderr)
        return 1

    if recreate:
        print("--recreate 已启用，删除旧集合 ...")
        wrapper.drop_collection()

    existed = wrapper.has_collection()
    wrapper.ensure_collection()
    current = wrapper.count() if existed else 0

    if existed and current > 0 and not (append or recreate):
        print(
            f"[中止] 集合已存在且含 {current} 条记录，为避免重复导入已停止。\n"
            f"       如需清空重灌请加 --recreate；如需追加请加 --append。",
            file=sys.stderr,
        )
        return 1

    total = 0
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        res = wrapper.insert_data(batch)
        if res.get("mode") != "milvus":
            print(f"[错误] 第 {i}~{i + len(batch)} 条写入未走 Milvus（mode={res.get('mode')}），已中止。",
                  file=sys.stderr)
            return 1
        total += res.get("insert_count", 0)
        print(f"  已写入 {total}/{len(records)}")

    wrapper.flush()
    print(f"\n导入完成：新增 {total} 条，集合当前共 {wrapper.count()} 条。")

    if verify:
        print("\n检索自检：")
        docs = wrapper.search("保温杯 316不锈钢 便携", top_k=3)
        if not docs:
            print("  [警告] 检索未返回结果，请检查数据与索引。", file=sys.stderr)
            return 1
        for d in docs:
            print(f"  score={d.get('score'):.4f}  platform={d.get('platform')}  title={d.get('original_title')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="向量化并导入爆款文案知识库到 Milvus")
    parser.add_argument("--input", default=None, help="数据文件路径（默认自动选择 processed/raw）")
    parser.add_argument("--batch-size", type=int, default=50, help="分批写入条数，默认 50")
    parser.add_argument("--recreate", action="store_true", help="清空集合后重灌（可重复执行）")
    parser.add_argument("--append", action="store_true", help="追加到现有集合")
    parser.add_argument("--verify", action="store_true", help="灌库后执行一次真实检索自检")
    args = parser.parse_args()

    try:
        return load_dataset(
            input_arg=args.input, batch_size=args.batch_size,
            recreate=args.recreate, append=args.append, verify=args.verify,
        )
    except FileNotFoundError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())