# 前端联调与样本评审会纪要

- **时间**：2026-09-23 20:00 - 20:40
- **形式**：线上
- **参会**：C、F（B/D/E 线下同步结论）
- **记录人**：F

## 一、前端进展（F）

- 驾驶舱两页面完成并构建通过（`npm run build` 零错误）：
  - 任务拓扑看板：下发目标 → DAG 实时流转 → 节点下钻（日志/输入输出）→ 失败重试 → 手动终止。
  - 审批中心：文案在线微调（违禁词一键替换 + 版本时间线）、调价审批、广告预算熔断审批、驳回原因必填。
- Mock 模式浏览器实测通过主链路：下发大促目标 → 3 子 Agent 并行 → 恰好生成 3 张审批单 → 审批闭环。
- WebSocket 封装：25s 心跳 + 指数退避重连 + 断线状态提示。

## 二、与 C 对齐的接口契约（确认稿）

- 任务结构：`{id, goal, status, budget, created_at, nodes[], edges[]}`；node 含 `status/pending/running/success/failed/waiting`、`logs[]`、`approval_id`。
- 条件边：文案「重大发布」、定价「超阈值幅度」、广告「预算熔断」三类走 Handoff 人工审批。
- REST 与 WS 事件以 `frontend/src/api/mock.js` 为契约基线，C 实现后端时逐条对齐；改动需同步 claude.md。

## 三、样本集评审（F 出品，全员确认）

`data/samples/` 交付：检索评测 1000 条（Recall@10）、违禁词 50 正例 + 10 负例、目标拆解 50 条（5 类任务）、文案质量 12 条（三平台）、定价用例 20 条、压测场景 6 个 + 灰度四阶段。评审结论：口径与 requirements.md 验收编号一致，直接用于 M5 压测。

## 四、遗留与待办

- [ ] F：Handoff 节点等待审批时卡片状态文案偶发不同步（store 正确、显示未刷新），继续定位。
- [ ] C：后端 main.py / 路由实现按契约落地，完成后 F 关闭 Mock 切真实联调。
- [ ] B：`scripts/load_knowledge.py` 写入 Milvus 时主键对齐 `KB-COPY-*` 约定（见 data/samples/README.md）。
- [ ] F：后端就绪后做一轮真联调回归（Axios 直连 + WS 真通道）。
