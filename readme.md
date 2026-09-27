# 多 Agent 电商全流程智能运营与决策系统

基于 **LangGraph 多智能体编排** 的电商运营自动化系统：商品分析 → 爆款文案生成 → 竞品监控调价 → 广告投放风控 → 人工审批（Handoff）全链路智能化，配套 **Vue 3 运营驾驶舱**。

> 详细功能与验收标准见 [docs/requirements.md](docs/requirements.md)；压测与评测样本见 [data/samples/README.md](data/samples/README.md)。

## 一、系统模块

| 模块 | 位置 | 说明 |
|---|---|---|
| ERP 与第三方电商平台 MCP 适配器 | `mcp_servers/` | FastMCP 封装商品查询、库存同步、调价与广告下发 API（千川 / Meta） |
| 向量知识与爆款样本库 | `backend/app/common/milvus_cli.py` | Milvus 管理爆款文案、违禁词库与竞品参数向量索引 |
| 运营主控协调智能体 | `backend/app/agent/supervisor.py` | 接收运营目标、任务拆解与多子 Agent 调度分发（LangGraph） |
| 爆款文案与多语种生成器 | `backend/app/agent/copywriter.py` | 小红书 / TikTok / 亚马逊多平台营销图文草稿 |
| 动态定价与竞品监控智能体 | `backend/app/agent/pricing.py` | 竞品价格波动比对与智能调价建议 |
| 电商多智能体运营驾驶舱 | `frontend/` | 任务拓扑看板、文案在线微调、调价 / 广告熔断审批流 |

## 二、技术栈

- **后端**：Python 3.11 + FastAPI + LangGraph + Celery + pymilvus
- **前端**：Vue 3.5 + Vite 5 + Element Plus 2.8 + Pinia + vue-router + axios
- **中间件**：Milvus 2.4（etcd + MinIO）、Redis 7
- **AI**：DeepSeek / OpenAI 兼容 API（`LLM_BASE_URL` 可配）

## 三、本地启动步骤

### 0. 环境要求

- Docker Desktop（含 docker compose）
- Node.js ≥ 18（开发使用 v24）
- Python ≥ 3.11
- Git

### 1. 克隆并配置环境变量

```bash
git clone <repo-url> project-ecommerce-agent
cd project-ecommerce-agent
cp .env.example .env    # 按需修改 MILVUS_HOST / LLM_API_KEY 等
```

### 2. 启动中间件（Milvus + Redis）

```bash
docker compose up -d
```

启动后包含：

| 服务 | 地址 |
|---|---|
| Milvus standalone | `localhost:19530`（健康检查 `:9091`） |
| Redis | `localhost:16379`（容器内 6379，AOF 持久化） |

> 默认卷挂载到 `./volumes/`，可用 `DOCKER_VOLUME_DIRECTORY` 环境变量改路径。

### 3. 启动后端（端口 8000）

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- REST 前缀：`/api/v1`
- WebSocket：`/ws/dashboard`
- 接口文档：http://localhost:8000/docs

> 后端尚未实现时，前端可先用 Mock 模式独立演示（见第 5 步）。

### 4. 启动 MCP 微服务（可选，联调外部系统时）

```bash
cd mcp_servers
pip install -r requirements.txt
python erp_connector.py     # ERP 商品/库存/调价服务
python ads_manager.py       # 千川/Meta 广告下发服务
```

### 5. 启动前端驾驶舱（端口 5173）

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 http://localhost:5173 ，默认进入「任务拓扑看板」，顶部菜单可切换「审批中心」。

## 四、前端 Mock 模式说明

后端未就绪或纯前端演示时，前端可脱离后端独立运行：

| 环境变量 | 文件 | 取值 | 含义 |
|---|---|---|---|
| `VITE_USE_MOCK` | `frontend/.env.development` | `true` | 全量内存 Mock：REST 走 mockAdapter，WebSocket 走 MockSocket，含任务生命周期模拟与风控熔断审批推送 |
| | | `false` | 真实对接后端：axios 走 Vite 代理 → `localhost:8000` |
| `VITE_API_BASE_URL` | 同上 | `/api/v1` | REST 前缀 |
| `VITE_WS_URL` | 同上 | `/ws/dashboard` | WebSocket 路径（ws(s) 自动补全） |

切换后重启 `npm run dev` 生效。Mock 内置演示剧本：

- 下发「大促全链路」目标（预算 ≥ 50000）→ Supervisor 拆解 → 文案 / 定价 / 广告三子 Agent 并行 → 触发 3 张审批单（文案违禁词、调价 299→265、广告预算熔断）
- 审批全部通过 → 任务完成；任一驳回 → Handoff 失败熔断
- 预置 1 条历史任务 + 3 条已处理审批单

## 五、前端页面功能

- **任务拓扑看板**（`/canvas`）：下发运营目标、DAG 拓扑实时流转、节点下钻（执行日志 / 输入输出）、失败重试、手动终止
- **审批中心**（`/approvals`）：文案在线微调（违禁词一键替换 + 版本管理）、调价审批、广告熔断审批、驳回原因必填、已处理记录检索

## 六、目录结构

```
project-ecommerce-agent/
├── backend/               # FastAPI + LangGraph 后端
│   ├── app/agent/         # supervisor / copywriter / pricing
│   ├── app/common/        # milvus_cli / celery_app
│   └── app/main.py        # FastAPI 入口
├── mcp_servers/           # FastMCP 微服务（ERP / 广告）
├── frontend/              # Vue3 驾驶舱
│   └── src/{api,router,stores,utils,views,styles}
├── scripts/               # Milvus 初始化 / 知识库导入 / 测试脚本
├── data/samples/          # 压测与评测样本（F 维护，见其 README）
├── docs/                  # 需求规格说明书 / 会议纪要
├── docker-compose.yml     # Milvus + Redis 中间件
└── .env.example           # 环境变量模板
```

## 七、团队分工

| 成员 | 职责 | 主要文件 |
|---|---|---|
| A | MCP 适配器 | `mcp_servers/` |
| B | 向量知识库 / 知识底座 | `backend/app/common/milvus_cli.py`、`scripts/` |
| C | Supervisor 调度 / 需求文档 | `backend/app/agent/supervisor.py`、`docs/requirements.md` |
| D | 文案生成器 | `backend/app/agent/copywriter.py` |
| E | 定价与竞品监控 | `backend/app/agent/pricing.py` |
| F | 前端驾驶舱 + 项目文档 + 压测样本 | `frontend/`、`readme.md`、`claude.md`、`docs/meeting-notes/`、`data/samples/` |

## 八、常用命令速查

```bash
docker compose up -d                        # 起 Milvus + Redis
docker compose ps                           # 查看中间件状态
cd backend && uvicorn app.main:app --reload --port 8000   # 后端
cd frontend && npm run dev                  # 前端（Mock 默认开启）
cd frontend && npm run build                # 前端生产构建
node data/samples/generate_samples.mjs      # 再生成检索评测样本
```
