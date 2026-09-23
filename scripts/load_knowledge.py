"""知识库批量导入脚本（成员B，需求 SCR-002 / VEC-005）

读取 data/processed/ 下成员D/E清洗好的数据，向量化后批量写入 Milvus。

支持的数据文件（位于 data/processed/，JSON / JSONL / CSV 均可）:
    copywriting.*        爆款文案   必填字段: text, platform
                                    可选字段: category, product, metrics
    banned_words.*       违禁词表   必填字段: word
                                    可选字段: replacement, category, severity
    competitor_params.*  竞品参数   必填字段: sku, price
                                    可选字段: competitor, sales, rating, snapshot_time

用法（项目根目录）:
    python scripts/load_knowledge.py                     # 导入全部可识别文件
    python scripts/load_knowledge.py --type copywriting  # 只导入爆款文案
    python scripts/load_knowledge.py --input data/processed --batch-size 100

依赖: 先运行 scripts/init_milvus.py 创建集合；
      向量化优先走 EMBEDDING_API_BASE/EMBEDDING_API_KEY 配置的在线服务，
      未配置时退化为本地哈希向量（仅联调用，无真实语义）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.common import milvus_cli  # noqa: E402


# ---------------------------------------------------------------------------
# 文件解析
# ---------------------------------------------------------------------------

def read_records(path: Path) -> list[dict]:
    """按扩展名解析 JSON / JSONL / CSV 为字典列表。"""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):  # 允许 {"data": [...]} 包装
            data = data.get("data", [])
        return list(data)
    if suffix == ".csv":
        import pandas as pd
        df = pd.read_csv(path, encoding="utf-8-sig").fillna("")
        return df.to_dict(orient="records")
    return []


def validate(records: list[dict], required: list[str], source: str) -> list[dict]:
    """校验必填字段，过滤并提示不合规记录。"""
    valid, dropped = [], 0
    for r in records:
        if all(str(r.get(f, "")).strip() for f in required):
            valid.append(r)
        else:
            dropped += 1
    if dropped:
        print(f"  [警告] {source}: 跳过 {dropped} 条缺少必填字段 {required} 的记录")
    return valid


LOADERS = {
    "copywriting": {
        "patterns": ("copywriting", "文案"),
        "required": ["text", "platform"],
        "insert": milvus_cli.insert_copywriting,
    },
    "banned_words": {
        "patterns": ("banned_words", "banned", "违禁词"),
        "required": ["word"],
        "insert": milvus_cli.insert_banned_words,
    },
    "competitor_params": {
        "patterns": ("competitor", "竞品"),
        "required": ["sku", "price"],
        "insert": milvus_cli.insert_competitor_params,
    },
}


def match_type(filename: str) -> str | None:
    lower = filename.lower()
    for dtype, conf in LOADERS.items():
        if any(p in lower for p in conf["patterns"]):
            return dtype
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="向量化并导入知识库数据到 Milvus")
    parser.add_argument("--input", default="data/processed", help="数据目录，默认 data/processed")
    parser.add_argument("--type", dest="dtype", default="all",
                        choices=["all", *LOADERS.keys()], help="只导入指定类型，默认全部")
    parser.add_argument("--batch-size", type=int, default=200, help="分批提示信息粒度，默认 200")
    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.is_dir():
        print(f"数据目录不存在: {input_dir.resolve()}")
        return 1

    files = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in (".json", ".jsonl", ".csv"))
    if not files:
        print(f"{input_dir}/ 下没有可导入的 JSON/JSONL/CSV 文件（等待成员D/E产出清洗数据）")
        return 1

    summary: dict[str, int] = {}
    failed = False
    for path in files:
        dtype = match_type(path.name)
        if dtype is None or (args.dtype != "all" and args.dtype != dtype):
            continue
        conf = LOADERS[dtype]
        records = validate(read_records(path), conf["required"], path.name)
        print(f"导入 {path.name} -> {dtype}: {len(records)} 条")
        try:
            summary[dtype] = summary.get(dtype, 0) + conf["insert"](records)
        except Exception as exc:  # noqa: BLE001
            failed = True
            print(f"  [失败] {exc}")

    print("\n导入汇总:")
    for dtype, n in summary.items():
        collection = {
            "copywriting": milvus_cli.COLLECTION_COPYWRITING,
            "banned_words": milvus_cli.COLLECTION_BANNED_WORDS,
            "competitor_params": milvus_cli.COLLECTION_COMPETITOR,
        }[dtype]
        print(f"  {dtype:<20} 新增 {n:>6} 条，集合当前共 {milvus_cli.count(collection)} 条")
    if not summary:
        print("  未导入任何数据（未匹配到约定文件名，参见脚本头部说明）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
