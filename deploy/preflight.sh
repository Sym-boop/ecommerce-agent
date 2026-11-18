#!/usr/bin/env bash
# 部署前预检：在服务器上跑一遍，把「一定会卡住你」的问题提前暴露。
#
# 用法：
#   bash deploy/preflight.sh
#
# 退出码：0 = 可以构建；1 = 有硬性问题，先修再构建

set -uo pipefail

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; RST=$'\033[0m'
hard_fail=0
warn=0

ok()   { echo "  ${GRN}✅${RST} $*"; }
bad()  { echo "  ${RED}❌${RST} $*"; hard_fail=$((hard_fail+1)); }
meh()  { echo "  ${YLW}⚠️${RST}  $*"; warn=$((warn+1)); }

echo "============================================================"
echo " 部署前预检"
echo "============================================================"

# ---------------- 1. 系统资源 ----------------
echo
echo "[1/6] 系统资源"

mem_mb=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)
if [ "$mem_mb" -ge 3800 ]; then
    ok "内存 ${mem_mb} MB"
elif [ "$mem_mb" -ge 1900 ]; then
    meh "内存 ${mem_mb} MB —— 能起，但 Playwright 渲染海报可能 OOM。建议 4G"
else
    bad "内存 ${mem_mb} MB —— 不足 2G，后端起不来"
fi

disk_gb=$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')
disk_gb=${disk_gb:-0}
if [ "$disk_gb" -ge 15 ]; then
    ok "磁盘剩余 ${disk_gb} GB"
elif [ "$disk_gb" -ge 8 ]; then
    meh "磁盘剩余 ${disk_gb} GB —— 镜像+卷约需 8–10G，偏紧"
else
    bad "磁盘剩余 ${disk_gb} GB —— 不够（镜像 + Chromium + 数据卷）"
fi

cpus=$(nproc 2>/dev/null || echo 0)
[ "$cpus" -ge 2 ] && ok "CPU ${cpus} 核" || meh "CPU ${cpus} 核，构建会很慢"

# ---------------- 2. Docker ----------------
echo
echo "[2/6] Docker"

if command -v docker >/dev/null 2>&1; then
    ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"
else
    bad "未安装 docker —— curl -fsSL https://get.docker.com | sh"
fi

if docker compose version >/dev/null 2>&1; then
    ok "docker compose $(docker compose version --short 2>/dev/null)"
elif command -v docker-compose >/dev/null 2>&1; then
    meh "只有旧版 docker-compose，请改用 docker compose（v2）"
else
    bad "缺少 docker compose 插件"
fi

if docker info >/dev/null 2>&1; then
    ok "Docker 守护进程运行中"
else
    bad "Docker 守护进程未运行 —— systemctl start docker"
fi

# ---------------- 3. 项目文件 ----------------
echo
echo "[3/6] 项目文件"

need_files="Dockerfile .dockerignore docker-compose.prod.yml .env.prod.example \
deploy/entrypoint.sh frontend/Dockerfile frontend/nginx.conf \
backend/requirements.txt backend/requirements-ml.txt data/processed/copy_processed.json"
for f in $need_files; do
    [ -f "$f" ] && ok "$f" || bad "缺少 $f"
done

# ---------------- 4. 配置 ----------------
echo
echo "[4/6] 生产配置"

if [ -f .env.prod ]; then
    ok ".env.prod 存在"

    llm=$(grep -E '^LLM_API_KEY=' .env.prod | cut -d= -f2- | tr -d ' ')
    if [ -z "$llm" ]; then
        meh "LLM_API_KEY 为空 —— 能跑，但文案生成会退化成模板兜底"
    elif [ "${llm:0:7}" = "sk-xxxx" ]; then
        meh "LLM_API_KEY 还是占位符 —— 同上看"
    else
        ok "LLM_API_KEY 已填（长度 ${#llm}）"
    fi

    mode=$(grep -E '^QIANCHUAN_DELIVERY_MODE=' .env.prod | cut -d= -f2- | tr -d ' ')
    if [ "$mode" = "mock" ]; then
        ok "QIANCHUAN_DELIVERY_MODE=mock（无平台授权前必须保持）"
    elif [ "$mode" = "real" ]; then
        bad "DELIVERY_MODE=real，但项目没有平台授权 —— 会直接报错，改回 mock"
    else
        meh "DELIVERY_MODE=${mode:-未设置}，建议显式写 mock"
    fi

    store=$(grep -E '^STORE_BACKEND=' .env.prod | cut -d= -f2- | tr -d ' ')
    [ "$store" = "mysql" ] && meh "STORE_BACKEND=mysql，但要先起 MySQL 服务" \
                           || ok "STORE_BACKEND=${store:-json}（单实例推荐 json）"
else
    bad "缺少 .env.prod —— cp .env.prod.example .env.prod 后再填 LLM_API_KEY"
fi

# ---------------- 5. 网络与端口 ----------------
echo
echo "[5/6] 网络与端口"

if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 8 -o /dev/null https://api.deepseek.com 2>/dev/null; then
        ok "能访问 api.deepseek.com"
    else
        meh "访问 api.deepseek.com 失败 —— 检查出网/代理；不影响启动，但文案生成会降级"
    fi
else
    meh "没装 curl，跳过联网检查"
fi

if command -v ss >/dev/null 2>&1; then
    if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE '(:80)$'; then
        bad "80 端口已被占用 —— 停掉占用者，或改 compose 里的端口映射"
    else
        ok "80 端口空闲"
    fi
else
    meh "没有 ss 命令，跳过端口检查"
fi

# ---------------- 6. 依赖清单自检 ----------------
echo
echo "[6/6] 依赖清单完整性"
# 这些包曾经漏过，漏了会静默降级而不是报错
for pkg in jieba tiktoken scikit-learn Pillow PyMySQL pypdf python-docx numpy; do
    if grep -qiE "^${pkg}" backend/requirements.txt; then
        ok "$pkg"
    else
        bad "requirements.txt 缺 $pkg —— 会导致运行时静默降级"
    fi
done
grep -qiE "^playwright" backend/requirements.txt && ok "playwright" || bad "缺 playwright"

# ---------------- 结论 ----------------
echo
echo "============================================================"
if [ "$hard_fail" -gt 0 ]; then
    echo " ${RED}有 ${hard_fail} 个硬性问题${RST}，先修再构建（另有 ${warn} 个提醒）"
    echo "============================================================"
    exit 1
fi
echo " ${GRN}预检通过${RST}（${warn} 个提醒）"
echo
echo " 下一步："
echo "   docker compose -f docker-compose.prod.yml up -d --build"
echo "   docker compose -f docker-compose.prod.yml logs -f backend"
echo "============================================================"
exit 0
