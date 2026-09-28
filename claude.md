# claude.md — Claude Code 项目协作规范

本文件供 Claude Code 等 AI 编程助手在本仓库工作时的上下文约定，也作为团队代码风格基线。

## 项目上下文

- 多 Agent 电商运营系统：FastAPI + LangGraph（后端）、Vue 3 + Element Plus（前端）、Milvus + Redis（中间件）、FastMCP（外部系统适配）。
- 六人分工见 [readme.md](readme.md) 第七节。**修改他人负责文件前先确认**：前端（`frontend/`）与文档归 F，后端 Agent 归 C/D/E，Milvus/脚本归 B，MCP 归 A。

## 语言与注释

- 代码注释、提交信息、UI 文案、文档一律使用中文；标识符用英文。
- 注释只写在逻辑不自明处，不写「翻译式」注释。

## 代码风格

### 前端（`frontend/`，Vue 3 `<script setup>`）

- 组件：`PascalCase.vue`，放在 `src/views/`（页面）或按需 `src/components/`；组合式函数/单例放 `src/utils/`。
- 状态：Pinia setup store（`defineStore('xxx', () => {...})`），store id 与文件名一致（app / task / approval）。
- API：一律走 `src/api/request.js` 的 axios 实例，禁止直接 `fetch`；响应体统一 `{code, msg, data}`。
- WebSocket：统一走 `src/utils/websocket.js` 的 `dashboardSocket` 单例，帧协议 `{event, data}`，事件名见该文件头部注释。
- Mock：所有 Mock 逻辑集中在 `src/api/mock.js`，通过 `VITE_USE_MOCK` 开关，Mock 数据口径需与 `data/samples/` 一致。
- 样式：`<style scoped>`，状态色使用 `src/styles/index.css` 中的既有约定（pending 灰 / running 蓝 / success 绿 / failed 红 / waiting 黄）。

### 后端（`backend/`，Python）

- PEP 8，类型注解（pydantic v2 模型定义请求/响应）。
- 路由前缀 `/api/v1`；异步优先（async/await）；耗时任务走 Celery。

## 接口契约（前后端对齐基线）

前端 Mock（`frontend/src/api/mock.js`）即契约：

- `POST /api/v1/goals` `{goal, budget?}` → `{task_id}`（budget ≥ 50000 触发广告熔断）
- `GET /tasks`、`GET /tasks/:id`、`POST /tasks/:id/retry|cancel`
- `GET /approvals?status=&type=&start_time=&end_time=`、`GET /approvals/:id`、`POST /approvals/:id/approve|reject`
- `PUT /copies/:id` 保存微调新版本
- 下行 WS 事件：`node:update` / `task:update` / `approval:created` / `approval:updated` / `pong`

改动契约时必须同步更新：mock.js、`src/api/index.js`、后端路由、本文件。

## Git 约定

- 分支：`feat/xxx`、`fix/xxx`、`docs/xxx`。
- 提交信息：`<type>: <中文摘要>`，type ∈ feat / fix / docs / refactor / test / chore。例：`feat: 审批中心支持违禁词一键替换`。
- 提交前：前端跑通 `npm run build`；后端通过 `python -m compileall` 或对应单测。
- 禁止提交：`.env`、`node_modules`、`volumes/`、大文件二进制（见 `.gitignore`）。

## 验证基线

- 前端改动：`cd frontend && npm run build` 必须零错误；涉及交互的在浏览器走一遍「下发目标 → 审批闭环」。
- 样本改动：`node data/samples/generate_samples.mjs` 可复现且 JSON/JSONL 可解析。
- 文档改动：保持与 requirements.md 验收标准编号（VEC/CPY/SUP/DSH/APR…）一致。
