# 广告生成流水线重构 + 投放中心改造 + LangGraph 迁移 方案

## 一、Context（为什么做）

当前系统是「生成 → 审查 → 审批 → 投放 → 历史」的广告流水线，但生成侧仍是**三个割裂的页面**（文案/图文/视频各一套表单），彼此不共享商品与文案上下文；投放侧是 9 个 mock 平台的平铺列表，没有平台化子页面。

本次要把生成侧收敛为**一个「广告生成」页**，沿着「选商品 → 生成文案（可重生成）→ 选择性生成图文/视频（各可重生成）→ 每次输出后审查 → 存投放/存半成品/删除」的链路走；投放侧按 4 大真实平台（巨量千川/聚光/阿里妈妈/多多进宝）做平台化子界面；同时清理竞价 agent，并把后端编排从自研切换到 **LangGraph**。

**现状关键事实（已核实）**

- 编排是自研的：[supervisor.py](file:///d:/project-ecommerce-agent/backend/app/agent/supervisor.py)、[task\_engine.py](file:///d:/project-ecommerce-agent/backend/app/common/task_engine.py)；**未使用 LangChain 编排**（仅 [milvus\_cli.py](file:///d:/project-ecommerce-agent/backend/app/common/milvus_cli.py#L160) 用 `langchain-huggingface` 做 embedding），`requirements.txt` **无 langgraph**。

- `ad_store`、`_DELIVERIES`、`task_engine.TASKS/APPROVALS` 均为**进程内内存**，无落盘。

- 竞价 agent 牵连：`agent/pricing.py`、`supervisor.py`、`task_engine.py`（定价节点 + `_make_pricing_approval`）、`common/celery_app.py`（每 15 分钟定时调价）、前端 `mock.js`/`format.js`/`App.vue`、`scripts/test_supervisor.py`，以及数据 `data/raw/competitors.csv`、`data/processed/competitors.csv`、`data/raw/our_products.csv`。

***

## 二、需要你拍板的决策点（默认取 A，可改）

| #  | 决策                 | 选项                                                                                                                                                                                                                                                                                                               |
| -- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1 | **竞价 agent 移除范围**  | **B 仅摘编排入口**：保留 `pricing.py` 与全部数据文件，只从 supervisor/task\_engine/前端入口移除**C 只清理前端展示**：后端能力与数据全保留                                                                                                                                                                                                                   |
| D2 | **LangGraph 迁移范围** | **A（默认）用 StateGraph 重写「广告生成主图」**：`select_product → gen_copy → review_copy →(条件边)→ gen_image / gen_video → end`，并用 `interrupt`+checkpointer 实现审批暂停/恢复；路由层（copy/image/video/review/delivery）保持不变，`task_engine` 保留为 REST/WS 层**B 全量重写**：连 `task_engine` 的任务 DAG 生命周期一起换成 LangGraph**C 暂缓**：本次先做前端与投放，LangGraph 单独立项 |
| D3 | **审查触发点**          | **A（默认）仅文案审查**：文案生成/重生成后走违禁词审查；图文、视频以「已通过审查的文案」为输入，不再单独审查**B 三者都审查**：文案、图文、视频每次输出后都跑违禁词审查                                                                                                                                                                                                                        |
| D4 | **历史记录（正常/半成品）存储** | **A（默认）后端 JSON 落盘** `data/history/records.json`（多端一致、可跨页面复用）**B 前端 localStorage**（不进后端，简单但仅本机）                                                                                                                                                                                                                   |

补充说明（无需单独拍板，按此执行，若不同意请在审阅时指出）：

- **平台 logo**：为避免侵权素材，用**自绘 SVG/文字徽标 + 平台主题色**实现，并预留 `frontend/src/assets/platforms/` 目录供你放入真实 logo 图片后自动替换。

- **「横向滚轮」**：广告生成页以**横向滚动的步骤卡**呈现（步骤 1 选品 → 步骤 2 文案 → 步骤 3 图文/视频），支持左右滚动/上一步下一步。

- **「主界面简短动态」**：新增首页 `/`（默认入口，替换现在的 `/copy` 重定向），用纯 CSS/SVG 自绘的简短动画（动态渐变 + 关键指标概览），无需外部素材、自动播放。

***

## 三、后端改造

### 1. 商品目录（选商品 / 自定义商品）

- 新增 `backend/app/common/product_store.py`：以 `data/raw/our_products.csv` 为初始数据源，内存态 + 可选落盘 `data/products.json`；函数 `list_products()` / `add_product(name, category, sell_points...)`。

- 新增 `backend/app/api/product_router.py`：`GET /api/v1/products`、`POST /api/v1/products`（自定义商品）。

### 2. 广告生成编排（新路由）

- 新增 `backend/app/api/adgen_router.py`（前缀 `/api/v1/adgen`），作为生成侧唯一入口：

  - `POST /copy`：`{product_id | custom_product, keyword?}` → 生成文案；`regenerate=true` 时**沿用原商品与关键词**只重出文案。

  - `POST /asset`：`{copy_id, type: image|video, keyword?, image?}` → **基于该文案**生成图文/视频（复用现有 image/video 生成实现）。

  - `GET /session/{id}`：返回本次生成的完整中间态（商品/关键词/文案/图文/视频/审查状态），供前端横向步骤卡渲染。

- 复用现有实现：`agent/copywriter.py`、`agent/image_generator.py`、`agent/video_generator.py` 的生成函数；三个旧路由 `copy_router/image_router/video_router` 保留（供 LangGraph 节点与内部调用），不再被前端直接使用。

### 3. 审查机制

- 文案每次产出后调用 `milvus_cli.check_banned_words`（复用 [pipeline.py](file:///d:/project-ecommerce-agent/backend/app/common/pipeline.py) 的 `check_copy`），命中 → 进「审查中心 · 违禁词审查」；通过 → 允许继续生成图文/视频。

- 按 D3 决定是否对图文/视频输出也审查。

### 4. 历史记录（正常 / 半成品）

- 新增 `backend/app/common/history_store.py`：JSON 落盘 `data/history/records.json`；字段：`id, product, keyword, copy{title,content}, image_url?, video_url?, stage(normal|wip), created_at`。

- 新增 `backend/app/api/history_router.py`：

  - `GET /api/v1/history`：列表（含 `wip` 标记，前端红色标签）。

  - `POST /api/v1/history`：保存本次生成（「存到投放界面」同时写入 `ad_store` 与历史）。

  - `DELETE /api/v1/history/{id}`：「删除此次生成」。

  - `POST /api/v1/history/{id}/pause`：「暂停生成并退出」→ 标记 `wip`。

### 5. 投放中心重构

- 改造 [delivery\_router.py](file:///d:/project-ecommerce-agent/backend/app/api/delivery_router.py)：`DELIVERY_PLATFORMS` 收敛为 4 个平台，每个含 `value/label/intro/theme_color/logo_key/belong`：

  - `qianchuan` 巨量千川（抖音）、`juguang` 聚光（小红书）、`alimama` 阿里妈妈（淘宝/天猫）、`duoduo` 多多进宝（拼多多）。

- 端点：`GET /platforms`（含介绍/主题色）、`GET /platforms/{key}`（详细介绍）、`GET /platforms/{key}/deliveries`（该平台历史投放）、`POST /submit`（复用，仅允许 `stage=approved`）。

- 「未投放广告」与「投放历史广告」区分：未投放 = `ad_store` 中 `stage=approved`；历史 = `stage=delivered` 或 `_DELIVERIES` 批次。

### 6. LangGraph 迁移（按 D2）

- `requirements.txt` / `pyproject.toml` 增加 `langgraph`。

- 新增 `backend/app/agent/ad_graph.py`：`StateGraph(AdGenState)` 定义主图与条件边；`gen_copy` 后接 `review_copy` 条件边（命中违禁 → 走审查/中断）；审批用 `interrupt` + checkpointer 持久化，恢复走 `/review/approve`。

- 移除图中的 `pricing` 节点（按 D1）。

### 7. 竞价 agent 清理（按 D1）

***

## 四、前端改造

1. **路由**（[router/index.js](file:///d:/project-ecommerce-agent/frontend/src/router/index.js)）：删除 `/copy`、`/image`、`/video`；新增 `/`（首页）与 `/adgen`（广告生成）；`/delivery`、`/history`、`/approvals` 保留。
2. **菜单**（[App.vue](file:///d:/project-ecommerce-agent/frontend/src/App.vue#L32-L62)）：改为 首页 / 广告生成 / 广告投放 / 历史广告发售 / 审查中心；`keep-alive` 的 include 由三生成页改为 `AdGenerator`；清理 `pricing` 通知分支。
3. **新增** **`views/Home.vue`**：自绘简短动态首页 + 概览指标（调用 `/health`、`/review/stats`）。
4. **新增** **`views/AdGenerator.vue`**：

   - 步骤 1：选商品（下拉，含「自定义商品」）+ 可选关键词 + 可选「载入历史记录」（正常无标签 / 半成品红色标签）。

   - 步骤 2：一键生成文案 → 显示结果 → 「重新生成」（商品/关键词不变）。

   - 步骤 3：勾选是否继续生成 图文 / 视频（可同时）→ 基于文案生成 → 每个可单独「重新生成」（可选是否提供图片、关键词）。

   - 底部三按钮：**存到投放界面**、**删除此次生成**、**暂停生成并退出（存为半成品）**。

   - 每次文案输出后展示审查结果（是否命中违禁词）。
5. **重构** **`views/DeliveryCenter.vue`**：平台卡片（logo + 简介）→ 进入平台子视图（左侧导航：广告投放 / 历史投放查看 / 平台详细介绍；背景使用该平台浅色主题 + logo 水印）；广告投放可勾选「未投放广告」或「投放历史中的广告」，两组分区展示。
6. **`api/index.js`**：新增 product/adgen/history/platform-detail 接口，移除三生成页旧接口（或保留但不再引用）；`format.js` 增加平台元数据与记录状态元数据；`mock.js` 清理 pricing 相关内容（按 D1）。
7. 删除 `views/CopyGenerator.vue`、`ImageGenerator.vue`、`VideoGenerator.vue`（内容并入 `AdGenerator.vue`）。

***

## 五、涉及的关键文件

**后端**：`app/main.py`（注册新 router）、`app/api/{adgen_router,product_router,history_router,delivery_router,review_router}.py`、`app/common/{product_store,history_store,pipeline,ad_store}.py`、`app/agent/ad_graph.py`、`app/agent/{supervisor,copywriter}.py`、`common/task_engine.py`、`common/celery_app.py`、`requirements.txt`
**前端**：`src/router/index.js`、`src/App.vue`、`src/views/{Home,AdGenerator,DeliveryCenter,AdHistory,Approvals}.vue`、`src/api/index.js`、`src/api/mock.js`、`src/utils/format.js`
**数据**：新增 `data/history/records.json`、`data/products.json`；按 D1 处理 `data/raw/competitors.csv` 等

***

## 六、验证方案

1. **后端**（`backend/.venv` 或 `ai0702`，`uvicorn app.main:app --reload --port 8001`）：

   - 全链路 HTTP 实测：选商品 → 生成文案 → 重生成（校验商品/关键词不变）→ 命中违禁词进审查 → 放行 → 生成图文/视频 → 存到投放 → 在 `/delivery/platforms` 与 `/history` 可见 → 暂停存半成品（`wip`）→ 删除。

   - 投放门禁：非 `approved` 物料提交返回 400。

   - LangGraph：确认图编译通过、审批 `interrupt`/恢复生效；`GET /health` 正常。
2. **前端**：`npm run build` 无报错；浏览器冒烟（首页动态、广告生成横向步骤全流程、投放中心四平台子页与左侧导航、广告投放/历史区分、审查中心四页签、菜单无旧生成页/无任务拓扑看板）。
3. **回归**：确认移除竞价后，`supervisor`/`task_engine` 无残留 import 报错（`python -c "import app.main"` 通过）。

