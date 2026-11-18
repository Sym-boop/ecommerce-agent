#!/usr/bin/env bash
# 容器启动入口：先把种子数据灌进卷，再起服务。
#
# 为什么需要这一步：compose 会把宿主机目录挂到 /app/data，而**挂载会遮住
# 镜像里已有的 /app/data**。所以镜像把种子数据放在 /app/seed-data，这里判断
# 卷是否为空，空才灌一次——已有数据（比如物料、审批记录）不会被覆盖。

set -euo pipefail

DATA_DIR="${DATA_DIR:-/app/data}"
SEED_DIR="${SEED_DIR:-/app/seed-data}"

mkdir -p "$DATA_DIR/media/images" "$DATA_DIR/media/videos"

# 只在卷是空的时候灌种子，避免每次重启把用户数据冲掉
if [ -d "$SEED_DIR" ] && [ -z "$(ls -A "$DATA_DIR" 2>/dev/null | grep -v '^media$' || true)" ]; then
    echo "[entrypoint] 数据卷为空，灌入种子数据：$SEED_DIR → $DATA_DIR"
    cp -rn "$SEED_DIR"/. "$DATA_DIR"/ 2>/dev/null || true
else
    echo "[entrypoint] 数据卷已有内容，跳过种子灌入"
fi

# 关键配置兜底：生产的存储后端与降级开关
export STORE_BACKEND="${STORE_BACKEND:-json}"
export RAG_RERANK="${RAG_RERANK:-1}"
# 未拿到平台授权前必须保持 mock，否则投递会直接报错
export QIANCHUAN_DELIVERY_MODE="${QIANCHUAN_DELIVERY_MODE:-mock}"

echo "[entrypoint] STORE_BACKEND=$STORE_BACKEND  DELIVERY_MODE=$QIANCHUAN_DELIVERY_MODE"
echo "[entrypoint] 启动：$*"
exec "$@"
