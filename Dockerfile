# 后端镜像：FastAPI + LangGraph 应用
#
# 关键设计（都是踩坑换来的）：
# 1. WORKDIR=/app、PYTHONPATH=/app/backend —— 代码里有 `Path("data")` 和
#    `_ROOT = parents[3]` 两种写法，必须让两者同时指向 /app/data，否则
#    素材会写到 /app/backend/data，重启就"消失"。
# 2. 装 fonts-noto-cjk —— Playwright 渲染中文海报，缺字体时中文全是方块。
# 3. 只跑 1 个 worker —— JSON 存储 + SqliteSaver checkpointer 都是单机假设，
#    多 worker 会互相覆盖。要扩多实例必须先换 MySQL + 共享 checkpointer。
# 4. 不把 .env 打进镜像 —— 密钥只通过 compose 的 env_file 注入。
# 5. ML 依赖（torch，1GB+）默认不装 —— 见 requirements-ml.txt 里的说明。

FROM python:3.13-slim

# INSTALL_ML=1 时才装 sentence-transformers（含 torch）
ARG INSTALL_ML=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# 运行时必需：curl（健康检查）、tzdata、中文字体
# 构建期需要：build-essential + python3-dev（个别包在 cp313 上可能要编译）
# ——它们在同一个 RUN 里装完再 purge，不会留在最终镜像里
COPY backend/requirements.txt backend/requirements-ml.txt /app/backend/
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl tzdata fonts-noto-cjk; \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime; echo $TZ > /etc/timezone; \
    apt-get install -y --no-install-recommends build-essential python3-dev; \
    pip install --upgrade pip; \
    pip install -r /app/backend/requirements.txt; \
    if [ "$INSTALL_ML" = "1" ]; then pip install -r /app/backend/requirements-ml.txt; fi; \
    apt-get purge -y --auto-remove build-essential python3-dev; \
    apt-get install -y --no-install-recommends curl; \
    rm -rf /var/lib/apt/lists/* /root/.cache

# Chromium 与它需要的系统库（--with-deps 会自己 apt-get update）
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# 预热 tiktoken 编码表，避免首次切块时联网（失败不阻断构建）
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" || true

# 启动前自检：关键模块能否导入。缺依赖要在构建期暴露，而不是运行期静默降级
RUN python -c "\
import jieba, tiktoken, sklearn, PIL, pymysql, pypdf, docx, numpy; \
print('核心依赖导入 OK:', 'jieba', jieba.__version__)"

# 业务代码
COPY backend/ /app/backend/

# 内置种子数据放在 seed-data，运行期由 entrypoint 灌进卷（避免卷把镜像内容遮住）
#
# 注意 data/*.json：ads.json / versions.json / products.json 是演示数据，
# 不带上它们页面就是空的——第一次部署很容易漏这一步。
COPY data/raw        /app/seed-data/raw
COPY data/processed  /app/seed-data/processed
COPY data/samples    /app/seed-data/samples
COPY data/*.json     /app/seed-data/
# 效果闭环的演示数据（daily.json）；token_usage.jsonl 是实验数据，不入镜像
COPY data/metrics/daily.json /app/seed-data/metrics/daily.json

COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
