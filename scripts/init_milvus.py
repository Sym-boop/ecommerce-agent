"""Milvus Collection 初始化脚本（成员B，需求 SCR-001）

创建三个知识库集合并设置向量索引（维度、索引类型可配）:
    - copywriting_samples  爆款文案样本（VEC-002）
    - banned_words         违禁词表（VEC-003）
    - competitor_params    竞品参数快照（VEC-004）

用法（项目根目录）:
    python scripts/init_milvus.py                 # 幂等创建，已存在则跳过
    python scripts/init_milvus.py --drop          # 删除同名集合后重建（清空数据）
    python scripts/init_milvus.py --index-type IVF_FLAT

依赖环境变量:
    MILVUS_URI         默认 http://localhost:19530（成员A的 docker-compose 启动 Milvus）
    MILVUS_INDEX_TYPE  默认 HNSW
    EMBEDDING_DIM      默认 1024（建库后不可更改，需与 Embedding 模型输出维度一致）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 将 backend/ 加入 sys.path，以便复用 app.common.milvus_cli
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.common import milvus_cli  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 Milvus Collection 与向量索引")
    parser.add_argument("--drop", action="store_true", help="删除同名集合后重建（会清空已有数据）")
    parser.add_argument("--index-type", default=None, choices=["HNSW", "IVF_FLAT"],
                        help="向量索引类型，默认取环境变量 MILVUS_INDEX_TYPE（HNSW）")
    args = parser.parse_args()

    print(f"Milvus 地址: {milvus_cli.MILVUS_URI}")
    print(f"向量维度: {milvus_cli.EMBEDDING_DIM}，索引类型: {args.index_type or milvus_cli.MILVUS_INDEX_TYPE}")
    if args.drop:
        print("警告: --drop 已启用，同名集合将被删除重建！")

    result = milvus_cli.ensure_collections(drop=args.drop, index_type=args.index_type)

    print("\n初始化结果:")
    for name, status in result.items():
        rows = milvus_cli.count(name)
        print(f"  [{status:>9}] {name:<24} 当前 {rows} 条")
    print("\n完成。下一步可运行: python scripts/load_knowledge.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
