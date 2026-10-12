"""Milvus Collection 初始化脚本（企业化，单一事实来源：app.common.milvus_cli）

按 milvus_cli 的统一 schema 幂等创建 knowledge_base（含向量索引）：
    id(int64, 主键, auto_id) / vector(float_vector, dim) / text(varchar)
    platform / category / original_title(varchar) / metadata(JSON)

用法（项目根目录）:
    python scripts/init_milvus.py                 # 幂等创建，已存在则跳过
    python scripts/init_milvus.py --recreate      # 删除同名集合后重建（清空数据）
    python scripts/init_milvus.py --index-type IVF_FLAT --dim 1024

特性:
    - 建库前校验 Milvus 连通性；失败给出明确报错并以退出码 1 结束
    - 建库前用 Embedding 探针校验向量维度，避免维度不匹配导致后续插入失败
    - 结束时打印集合统计（行数）

依赖环境变量（项目根目录 .env）:
    MILVUS_URI / MILVUS_HOST / MILVUS_PORT    Milvus 地址
    EMBEDDING_API_BASE / EMBEDDING_API_KEY    在线向量化服务（文案相似案例检索用）
    EMBEDDING_MODEL                           向量模型，默认 text-embedding-v4（dim=1024）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 将 backend/ 加入 sys.path，以便复用 app.common.milvus_cli
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.common import milvus_cli  # noqa: E402


def init_collection(
    recreate: bool = False,
    index_type: str | None = None,
    dim: int | None = None,
) -> tuple[bool, dict]:
    """创建集合。返回 (是否成功, describe 信息)。失败时 describe 为 {}。"""
    wrapper = milvus_cli.MilvusClientWrapper(dimension=dim or milvus_cli.MilvusClientWrapper.DEFAULT_DIMENSION)
    if index_type:
        wrapper.INDEX_TYPE = index_type

    print(f"Milvus 地址 : {wrapper._uri}")
    print(f"集合名称    : {wrapper.COLLECTION}")
    print(f"向量维度    : {wrapper._dim}")
    print(f"索引类型    : {wrapper.INDEX_TYPE} / {wrapper.METRIC_TYPE}")

    # 1) 连通性 + 向量维度探针校验
    try:
        wrapper.client  # 触发连接（不可达会抛错）
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 无法连接 Milvus（{exc}）", file=sys.stderr)
        print("       请确认 .env 的 MILVUS_URI 指向已启动的 Milvus 服务。", file=sys.stderr)
        return False, {}

    if wrapper._embedding_fn is None:
        print("[错误] 未配置可用 Embedding（EMBEDDING_API_BASE/KEY 或 OPENAI_*）", file=sys.stderr)
        print("       请先在 .env 配置在线向量化服务，否则无法建库/灌库。", file=sys.stderr)
        return False, {}
    try:
        probe_dim = len(wrapper.embed(["_dimension_probe_"])[0])
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] Embedding 探针调用失败（{exc}）", file=sys.stderr)
        return False, {}
    if probe_dim != wrapper._dim:
        print(
            f"[错误] 向量维度不匹配：Embedding 输出 {probe_dim}，但建库维度为 {wrapper._dim}。\n"
            f"       请把 --dim（或 milvus_cli.DEFAULT_DIMENSION）改为 {probe_dim} 后重建集合。",
            file=sys.stderr,
        )
        return False, {}
    print(f"Embedding   : 可用（维度 {probe_dim} 校验通过）")

    # 2) 建库（幂等 / 重建）
    if recreate and wrapper.has_collection():
        print("--recreate 已启用，删除旧集合 ...")
        wrapper.drop_collection()
    existed = wrapper.has_collection()
    wrapper.ensure_collection()
    print("集合已存在，跳过创建。" if existed else f"已创建集合 {wrapper.COLLECTION}（含向量索引）。")

    info = wrapper.describe()
    return True, info


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 Milvus Collection 与向量索引")
    parser.add_argument("--recreate", action="store_true",
                        help="删除同名集合后重建（会清空已有数据）")
    parser.add_argument("--index-type", default=None, choices=["HNSW", "IVF_FLAT"],
                        help="向量索引类型，默认取 milvus_cli.INDEX_TYPE（IVF_FLAT）")
    parser.add_argument("--dim", type=int, default=None,
                        help="向量维度，默认取 milvus_cli.DEFAULT_DIMENSION（1024）")
    args = parser.parse_args()

    if args.recreate:
        print("警告: --recreate 已启用，同名集合及其数据将被删除重建！\n")

    ok, info = init_collection(recreate=args.recreate, index_type=args.index_type, dim=args.dim)
    if not ok:
        return 1

    print("\n初始化结果:")
    print(f"  collection={info['collection']}  exists={info['exists']}  当前 {info['row_count']} 条")
    print("\n完成。下一步可运行: python scripts/load_knowledge.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())