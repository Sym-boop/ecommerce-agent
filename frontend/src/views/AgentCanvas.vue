<template>
  <!--
    ==========================================================================
    任务拓扑看板（LEGACY · 已从菜单移除）

    状态：路由仍保留（/canvas），但顶部菜单不再展示，页面为「只读视图」。
    原因：项目做过范围收敛，主链路改成「以商品为入口的固定流水线」
          （见 backend/app/agent/ad_graph.py 的 LangGraph 编排）。
          这条「下发运营目标 → Supervisor 动态拆解」的路径退为次要，
          UI 入口随之移除，避免两套流程并存造成困惑。

    仍在运行时依赖：task/store（listTasks / getTask / retryTask / cancelTask）
                    → GET /api/v1/tasks、/api/v1/tasks/:id、retry、cancel
    注意：本页已「不可下发目标」，因此不会调用 POST /api/v1/goals；
          该端点对应的 supervisor.py 目前只有直接调 API 才会触发。

    若要彻底下线，需同步清理：本文件 + router 中的 /canvas 路由 +
    App.vue 的菜单项（已移除）+ 后端 task_engine 的 supervisor 分支 + /goals 端点。
    ==========================================================================
  -->
  <div class="canvas-page">
    <!-- ===== 顶部工具栏 ===== -->
    <div class="page-toolbar">
      <span class="readonly-badge">只读视图 · 不可下发目标</span>
      <el-select
        :model-value="taskStore.currentId"
        placeholder="选择历史任务"
        style="width: 320px"
        @change="taskStore.selectTask"
      >
        <el-option
          v-for="t in taskStore.tasks"
          :key="t.id"
          :label="`${t.id}｜${t.goal.slice(0, 22)}${t.goal.length > 22 ? '…' : ''}`"
          :value="t.id"
        >
          <span style="float: left; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
            {{ t.goal }}
          </span>
          <el-tag
            :type="TASK_STATUS_META[t.status]?.type"
            size="small"
            style="float: right; margin-top: 4px"
          >
            {{ TASK_STATUS_META[t.status]?.label }}
          </el-tag>
        </el-option>
      </el-select>

      <span class="spacer"></span>

      <el-button-group v-if="taskStore.current">
        <el-button :icon="ZoomIn" @click="zoomStep(0.15)" title="放大" />
        <el-button class="zoom-label" disabled>{{ Math.round(viewport.k * 100) }}%</el-button>
        <el-button :icon="ZoomOut" @click="zoomStep(-0.15)" title="缩小" />
        <el-button :icon="FullScreen" @click="fitView" title="适应画布" />
      </el-button-group>
      <el-button :icon="Refresh" @click="taskStore.fetchTasks()" :loading="taskStore.loading">
        刷新
      </el-button>
      <el-popconfirm
        v-if="taskStore.current?.status === 'running'"
        title="确定终止当前任务？未决审批将被自动驳回"
        @confirm="handleCancel"
      >
        <template #reference>
          <el-button type="danger" :icon="CircleClose">终止任务</el-button>
        </template>
      </el-popconfirm>
    </div>

    <!-- ===== 任务概要 ===== -->
    <el-alert
      v-if="taskStore.current"
      class="task-banner"
      :closable="false"
      :type="taskStore.current.status === 'failed' ? 'error' : taskStore.current.status === 'done' ? 'success' : 'info'"
      show-icon
    >
      <template #title>
        <div class="banner-inner">
          <span class="mono banner-id">{{ taskStore.current.id }}</span>
          <span class="banner-goal">{{ taskStore.current.goal }}</span>
          <el-tag :type="TASK_STATUS_META[taskStore.current.status]?.type" effect="dark" size="small">
            {{ TASK_STATUS_META[taskStore.current.status]?.label }}
          </el-tag>
          <el-tag v-if="taskStore.current.budget" size="small" effect="plain">
            预算 ¥{{ taskStore.current.budget.toLocaleString() }}
          </el-tag>
          <span class="banner-stats">
            <el-tag
              v-for="(count, st) in statusCounter"
              :key="st"
              :type="NODE_STATUS_META[st]?.type"
              size="small"
              effect="plain"
            >
              {{ NODE_STATUS_META[st]?.label }} {{ count }}
            </el-tag>
          </span>
        </div>
      </template>
    </el-alert>

    <!-- ===== 拓扑画布 ===== -->
    <div
      ref="canvasRef"
      class="canvas-area"
      @wheel.prevent="onWheel"
      @mousedown="onDragStart"
    >
      <el-empty
        v-if="!taskStore.current"
        class="canvas-empty"
        description="暂无可查看的任务拓扑（该看板为只读视图）"
      />

      <svg
        v-else
        class="topology-svg"
        :width="viewSize.w"
        :height="viewSize.h"
      >
        <defs>
          <marker id="arrow-idle" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
            <path d="M0,0 L8,3 L0,6 Z" fill="#c0c4cc" />
          </marker>
          <marker id="arrow-active" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
            <path d="M0,0 L8,3 L0,6 Z" fill="#409eff" />
          </marker>
          <marker id="arrow-done" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
            <path d="M0,0 L8,3 L0,6 Z" fill="#67c23a" />
          </marker>
          <marker id="arrow-blocked" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
            <path d="M0,0 L8,3 L0,6 Z" fill="#e6a23c" />
          </marker>
        </defs>

        <g :transform="`translate(${viewport.x},${viewport.y}) scale(${viewport.k})`">
          <!-- 连线 -->
          <g v-for="edge in edgeViews" :key="`${edge.source}-${edge.target}`">
            <path
              :d="edge.d"
              class="edge-path"
              :class="`is-${edge.state}`"
              :marker-end="`url(#arrow-${edge.state})`"
            />
            <text
              v-if="edge.label"
              :x="edge.lx"
              :y="edge.ly"
              text-anchor="middle"
              class="edge-label"
              :class="{ 'is-blocked': edge.state === 'blocked' }"
            >
              {{ edge.label }}
            </text>
          </g>

          <!-- 节点（foreignObject 内嵌 HTML 卡片） -->
          <foreignObject
            v-for="node in nodeViews"
            :key="node.id"
            :x="node.x"
            :y="node.y"
            :width="NODE_W"
            :height="NODE_H"
          >
            <div
              class="node-card"
              :class="`is-${node.status}`"
              @mousedown.stop
              @click="openDrawer(node)"
            >
              <div class="node-title">
                <span class="status-dot" :class="`is-${node.status}`"></span>
                <el-icon :color="nodeIconColor(node.status)">
                  <component :is="NODE_ICONS[node.type]" />
                </el-icon>
                <span>{{ node.name }}</span>
              </div>
              <div class="node-sub">{{ nodeSubText(node) }}</div>
            </div>
          </foreignObject>
        </g>
      </svg>

      <!-- 图例 -->
      <div v-if="taskStore.current" class="legend">
        <span v-for="(meta, key) in NODE_STATUS_META" :key="key" class="legend-item">
          <span class="status-dot" :class="`is-${key}`"></span>{{ meta.label }}
        </span>
      </div>
    </div>

    <!-- ===== 执行过程：逐步 diff（每个 Agent 的输入 → 输出）===== -->
    <div v-if="taskStore.current" class="diff-card">
      <div class="diff-card-head">
        <span class="diff-card-title">执行过程逐步对照 · 每个 Agent 的输入 → 输出</span>
        <span class="diff-legend">
          <span class="diff-legend-item is-minus">− 输入</span>
          <span class="diff-legend-item is-plus">+ 输出</span>
        </span>
      </div>

      <el-empty v-if="orderedNodes.length === 0" description="该任务暂无执行节点" :image-size="70" />

      <div v-else class="diff-steps">
        <div
          v-for="(node, idx) in orderedNodes"
          :key="node.id"
          class="diff-step"
          :class="`is-${node.status}`"
        >
          <div class="diff-step-head">
            <span class="diff-step-index">{{ idx + 1 }}</span>
            <span class="status-dot" :class="`is-${node.status}`"></span>
            <span class="diff-step-name">{{ node.name }}</span>
            <span class="mono diff-step-type">{{ node.type }}</span>
            <el-tag :type="NODE_STATUS_META[node.status]?.type" size="small" effect="plain">
              {{ NODE_STATUS_META[node.status]?.label }}
            </el-tag>
            <span class="diff-step-time">{{ formatTime(node.started_at) }}</span>
            <span v-if="node.error" class="diff-step-error">{{ node.error }}</span>
          </div>

          <div class="diff-panes">
            <div class="diff-pane is-minus">
              <div class="diff-pane-label">
                <span class="diff-sign">−</span> 输入 INPUT
              </div>
              <pre v-if="node.input" class="diff-json">{{ prettyJson(node.input) }}</pre>
              <pre v-else class="diff-json is-empty">（无输入）</pre>
            </div>
            <div class="diff-pane is-plus">
              <div class="diff-pane-label">
                <span class="diff-sign">+</span> 输出 OUTPUT
              </div>
              <pre v-if="node.output" class="diff-json">{{ prettyJson(node.output) }}</pre>
              <pre v-else class="diff-json is-empty">（暂无输出）</pre>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 节点详情抽屉 ===== -->
    <el-drawer
      v-model="drawerVisible"
      size="470px"
      :title="selectedNode ? selectedNode.name : '节点详情'"
      destroy-on-close
    >
      <template v-if="selectedNode">
        <div class="drawer-head">
          <el-tag :type="NODE_STATUS_META[selectedNode.status]?.type" effect="dark">
            {{ NODE_STATUS_META[selectedNode.status]?.label }}
          </el-tag>
          <span class="mono node-type">{{ selectedNode.type }}</span>
        </div>
        <el-descriptions :column="1" size="small" border class="drawer-desc">
          <el-descriptions-item label="开始时间">{{ formatTime(selectedNode.started_at) }}</el-descriptions-item>
          <el-descriptions-item label="完成时间">{{ formatTime(selectedNode.finished_at) }}</el-descriptions-item>
          <el-descriptions-item label="耗时">{{ durationText }}</el-descriptions-item>
          <el-descriptions-item v-if="selectedNode.error" label="错误">
            <span class="error-text">{{ selectedNode.error }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="selectedNode.output?.approval_id"
          class="handoff-tip"
          type="warning"
          show-icon
          :closable="false"
          title="该节点触发风控规则，已阻断并生成人工审批单"
        >
          <div class="handoff-tip-body">
            <el-button size="small" type="warning" @click="router.push('/approvals')">
              前往审批中心处理
            </el-button>
          </div>
        </el-alert>

        <el-tabs v-model="detailTab" class="detail-tabs">
          <el-tab-pane label="执行日志" name="logs">
            <div ref="logBoxRef" class="log-box">
              <el-timeline v-if="selectedNode.logs.length">
                <el-timeline-item
                  v-for="(item, idx) in selectedNode.logs"
                  :key="idx"
                  :timestamp="formatTime(item.ts)"
                  :color="LOG_LEVEL_META[item.level]?.color"
                  placement="top"
                >
                  <div class="log-line">
                    <span class="log-level" :style="{ color: LOG_LEVEL_META[item.level]?.color }">
                      {{ LOG_LEVEL_META[item.level]?.label }}
                    </span>
                    {{ item.msg }}
                  </div>
                </el-timeline-item>
              </el-timeline>
              <el-empty v-else description="暂无日志" :image-size="70" />
            </div>
          </el-tab-pane>
          <el-tab-pane label="输入 / 输出" name="io">
            <div class="io-section">
              <div class="io-title">输入参数</div>
              <pre v-if="selectedNode.input" class="json-view">{{ JSON.stringify(selectedNode.input, null, 2) }}</pre>
              <el-empty v-else description="无输入" :image-size="60" />
            </div>
            <div class="io-section">
              <div class="io-title">输出结果</div>
              <pre v-if="selectedNode.output" class="json-view">{{ JSON.stringify(selectedNode.output, null, 2) }}</pre>
              <el-empty v-else description="暂无输出" :image-size="60" />
            </div>
          </el-tab-pane>
        </el-tabs>

        <template v-if="selectedNode.status === 'failed'">
          <el-divider />
          <div class="drawer-actions">
            <el-button
              type="primary"
              :icon="RefreshRight"
              :loading="retrying"
              @click="handleRetry"
            >
              重试该节点
            </el-button>
            <el-button type="danger" plain @click="drawerVisible = false">关闭</el-button>
          </div>
        </template>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ZoomIn, ZoomOut, FullScreen, Refresh, CircleClose, RefreshRight,
  Operation, EditPen, TrendCharts, Lock, CircleCheck, Money,
} from '@element-plus/icons-vue'
import { useTaskStore } from '@/stores/task'
import {
  formatTime, NODE_STATUS_META, TASK_STATUS_META, LOG_LEVEL_META,
} from '@/utils/format'

const router = useRouter()
const taskStore = useTaskStore()

const NODE_W = 184
const NODE_H = 72
const COL_GAP = 70
const ROW_GAP = 40

const NODE_ICONS = {
  supervisor: Operation,
  copywriter: EditPen,
  pricing: TrendCharts,
  ads: Money,
  handoff: Lock,
  end: CircleCheck,
}

const NODE_STATUS_COLORS = {
  pending: '#909399',
  running: '#409eff',
  success: '#67c23a',
  failed: '#f56c6c',
  waiting: '#e6a23c',
}
const nodeIconColor = (status) => NODE_STATUS_COLORS[status] || '#909399'

function nodeSubText(node) {
  if (node.status === 'running') return '正在执行…'
  if (node.status === 'waiting') return '等待人工审批'
  if (node.status === 'failed') return node.error || '执行失败'
  if (node.type === 'copywriter' && node.output) return `已生成 ${node.output.platforms ?? ''} 平台文案`
  if (node.type === 'pricing' && node.output) return `${node.output.action}｜置信度 ${((node.output.confidence ?? 0) * 100).toFixed(0)}%`
  if (node.type === 'ads' && node.output) return node.output.blocked ? '已阻断，待审批' : '校验通过'
  if (node.type === 'supervisor' && node.output) return `分发 ${node.output.sub_tasks?.length ?? 0} 个子任务`
  if (node.type === 'handoff' && node.output) return `已放行 ${node.output.resolved ?? 0} 项审批`
  if (node.type === 'end') return '链路结束'
  return '等待调度'
}

// ---------- 执行过程 diff：按执行顺序排列节点，展示输入 / 输出 ----------
const orderedNodes = computed(() => {
  const nodes = taskStore.current?.nodes || []
  return nodes
    .map((node, index) => ({ node, index }))
    .sort((a, b) => {
      const ta = a.node.started_at ? new Date(a.node.started_at).getTime() : Infinity
      const tb = b.node.started_at ? new Date(b.node.started_at).getTime() : Infinity
      if (ta !== tb) return ta - tb
      return a.index - b.index
    })
    .map(({ node }) => node)
})

function prettyJson(value) {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

async function handleCancel() {
  await taskStore.cancel(taskStore.currentId)
  ElMessage.warning('任务已终止')
}

const retrying = ref(false)
async function handleRetry() {
  retrying.value = true
  try {
    await taskStore.retry(taskStore.currentId, selectedNode.value?.id)
    ElMessage.success('已重新触发执行')
  } finally {
    retrying.value = false
  }
}

// ---------- 画布视口 ----------
const canvasRef = ref(null)
const viewSize = reactive({ w: 900, h: 640 })
const viewport = reactive({ x: 0, y: 0, k: 1 })

const observeResize = () => {
  const el = canvasRef.value
  if (!el) return
  viewSize.w = el.clientWidth
  viewSize.h = el.clientHeight
  const ro = new ResizeObserver(() => {
    viewSize.w = el.clientWidth
    viewSize.h = el.clientHeight
  })
  ro.observe(el)
}

function zoomStep(delta, cx, cy) {
  const prev = viewport.k
  const next = Math.min(1.8, Math.max(0.4, +(prev + delta).toFixed(2)))
  if (next === prev) return
  // 以画布中心（或鼠标点）为锚点缩放
  const px = cx ?? viewSize.w / 2
  const py = cy ?? viewSize.h / 2
  viewport.x = px - ((px - viewport.x) * next) / prev
  viewport.y = py - ((py - viewport.y) * next) / prev
  viewport.k = next
}

function onWheel(e) {
  zoomStep(e.deltaY < 0 ? 0.1 : -0.1, e.offsetX, e.offsetY)
}

let dragging = false
let dragStart = null
function onDragStart(e) {
  dragging = true
  dragStart = { x: e.clientX - viewport.x, y: e.clientY - viewport.y }
  window.addEventListener('mousemove', onDragging)
  window.addEventListener('mouseup', onDragEnd)
}
function onDragging(e) {
  if (!dragging) return
  viewport.x = e.clientX - dragStart.x
  viewport.y = e.clientY - dragStart.y
}
function onDragEnd() {
  dragging = false
  window.removeEventListener('mousemove', onDragging)
  window.removeEventListener('mouseup', onDragEnd)
}

// ---------- 布局计算 ----------
const nodeViews = computed(() => {
  const task = taskStore.current
  if (!task) return []
  const rankOf = {}
  task.nodes.forEach((n) => { rankOf[n.id] = 0 })
  for (let i = 0; i < task.nodes.length; i++) {
    let changed = false
    for (const e of task.edges) {
      if (rankOf[e.source] === undefined) continue
      if (rankOf[e.target] < rankOf[e.source] + 1) {
        rankOf[e.target] = rankOf[e.source] + 1
        changed = true
      }
    }
    if (!changed) break
  }

  const columns = {}
  task.nodes.forEach((n) => {
    const r = rankOf[n.id]
    ;(columns[r] ||= []).push(n)
  })
  Object.values(columns).forEach((col) => {
    col.sort((a, b) => a.id.localeCompare(b.id))
  })

  const positioned = []
  Object.entries(columns).forEach(([r, col]) => {
    const rank = Number(r)
    const totalH = col.length * NODE_H + (col.length - 1) * ROW_GAP
    const startY = Math.max(30, viewSize.h / 2 - totalH / 2)
    col.forEach((node, i) => {
      positioned.push({
        ...node,
        x: 36 + rank * (NODE_W + COL_GAP),
        y: startY + i * (NODE_H + ROW_GAP),
      })
    })
  })
  return positioned
})

const posMap = computed(() => Object.fromEntries(nodeViews.value.map((n) => [n.id, n])))

function edgeUiState(edge) {
  const s = taskStore.current.nodes.find((n) => n.id === edge.source)
  const t = taskStore.current.nodes.find((n) => n.id === edge.target)
  if (!s || !t) return 'idle'

  // 判断该条件边/常规边是否为实际执行路径
  if (edge.kind === 'condition') {
    if (!s.output?.approval_id) return 'idle'
  } else if (edge.source !== 'n-sup' && edge.target === 'n-end') {
    // 子节点直达「完成」的常规边：仅在未触发审批时才会走
    if (s.output?.approval_id) return 'idle'
  }

  if (t.status === 'success') return 'done'
  if (t.status === 'waiting' || t.status === 'failed') return 'blocked'
  if (t.status === 'running') return 'active'
  if (s.status === 'running') return 'active'
  if (s.status === 'success') return 'active'
  return 'idle'
}

const edgeViews = computed(() => {
  const task = taskStore.current
  if (!task) return []
  return task.edges.map((edge) => {
    const s = posMap.value[edge.source]
    const t = posMap.value[edge.target]
    if (!s || !t) return null
    const x1 = s.x + NODE_W
    const y1 = s.y + NODE_H / 2
    const x2 = t.x
    const y2 = t.y + NODE_H / 2
    const dx = Math.max(40, (x2 - x1) / 2)
    const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`
    return {
      ...edge,
      d,
      state: edgeUiState(edge),
      lx: (x1 + x2) / 2,
      ly: (y1 + y2) / 2 - 7,
    }
  }).filter(Boolean)
})

function fitView() {
  if (nodeViews.value.length === 0) return
  const maxX = Math.max(...nodeViews.value.map((n) => n.x + NODE_W))
  const maxY = Math.max(...nodeViews.value.map((n) => n.y + NODE_H))
  const k = Math.min(
    (viewSize.w - 70) / maxX,
    (viewSize.h - 60) / maxY,
    1.15,
  )
  viewport.k = +k.toFixed(2)
  viewport.x = (viewSize.w - maxX * viewport.k) / 2
  viewport.y = 24
}

// ---------- 节点详情 ----------
const drawerVisible = ref(false)
const detailTab = ref('logs')
const selectedNodeId = ref('')
const logBoxRef = ref(null)

const selectedNode = computed(() => {
  if (!taskStore.current || !selectedNodeId.value) return null
  return taskStore.current.nodes.find((n) => n.id === selectedNodeId.value) || null
})

const durationText = computed(() => {
  const n = selectedNode.value
  if (!n?.started_at) return '-'
  const end = n.finished_at ? new Date(n.finished_at).getTime() : Date.now()
  const ms = end - new Date(n.started_at).getTime()
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
})

function openDrawer(node) {
  selectedNodeId.value = node.id
  detailTab.value = 'logs'
  drawerVisible.value = true
}

watch(
  () => selectedNode.value?.logs.length,
  async () => {
    await nextTick()
    const box = logBoxRef.value
    if (box) box.scrollTop = box.scrollHeight
  },
)

// ---------- 状态统计 ----------
const statusCounter = computed(() => {
  const counter = { running: 0, waiting: 0, success: 0, failed: 0 }
  taskStore.current?.nodes.forEach((n) => {
    if (counter[n.status] !== undefined) counter[n.status] += 1
  })
  return counter
})

onMounted(async () => {
  await taskStore.fetchTasks(true)
  await nextTick()
  observeResize()
  fitView()
})

watch(
  () => taskStore.currentId,
  async () => {
    await nextTick()
    fitView()
  },
)
</script>

<style scoped>
.canvas-page {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.zoom-label {
  min-width: 56px;
  font-weight: 600;
}
.zoom-label.is-disabled {
  color: #606266 !important;
}

.task-banner {
  margin-bottom: 12px;
}
.banner-inner {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.banner-id {
  font-size: 12px;
  color: #909399;
}
.banner-goal {
  font-weight: 600;
}
.banner-stats {
  display: inline-flex;
  gap: 6px;
  margin-left: auto;
}

.canvas-area {
  position: relative;
  flex: 1;
  min-height: 420px;
  border: 1px solid var(--card-border);
  border-radius: 10px;
  background-color: var(--canvas-bg);
  background-image: radial-gradient(circle, #dfe4ec 1.2px, transparent 1.2px);
  background-size: 22px 22px;
  overflow: hidden;
  cursor: grab;
}
.canvas-area:active {
  cursor: grabbing;
}
.canvas-empty {
  position: absolute;
  inset: 0;
  justify-content: center;
}
.topology-svg {
  display: block;
}

.legend {
  position: absolute;
  right: 16px;
  bottom: 14px;
  display: flex;
  gap: 16px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--card-border);
  border-radius: 8px;
  padding: 8px 14px;
  font-size: 12px;
  color: #606266;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.quick-goals {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
}
.quick-tag {
  cursor: pointer;
}

.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.node-type {
  font-size: 12px;
  color: #909399;
}
.drawer-desc {
  margin-bottom: 12px;
}
.error-text {
  color: #f56c6c;
}
.handoff-tip {
  margin-bottom: 12px;
}
.handoff-tip-body {
  margin-top: 6px;
}
.detail-tabs {
  margin-top: 4px;
}
.log-box {
  max-height: 46vh;
  overflow-y: auto;
  padding-right: 6px;
}
.log-line {
  font-size: 13px;
  line-height: 1.7;
}
.log-level {
  display: inline-block;
  width: 42px;
  font-weight: 700;
  font-family: 'JetBrains Mono', Consolas, monospace;
}
.io-section {
  margin-bottom: 16px;
}
.io-title {
  font-weight: 600;
  margin-bottom: 8px;
  font-size: 13px;
  color: #303133;
}
.drawer-actions {
  display: flex;
  gap: 10px;
}

/* ===== 只读物料展示 ===== */
.readonly-badge {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 14px;
  border-radius: 8px;
  background: #f4f6fa;
  border: 1px solid var(--card-border);
  color: #6b7280;
  font-size: 13px;
  white-space: nowrap;
}
/* ===== 执行过程 diff 视图 ===== */
.diff-card {
  margin-top: 16px;
  border: 1px solid var(--card-border);
  border-radius: 12px;
  background: #fff;
  padding: 14px 16px 16px;
}
.diff-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.diff-card-title {
  font-weight: 600;
  color: #1d2b4f;
}
.diff-legend {
  display: inline-flex;
  gap: 14px;
  font-size: 12px;
}
.diff-legend-item.is-minus {
  color: #f56c6c;
}
.diff-legend-item.is-plus {
  color: #2da44e;
}

.diff-steps {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 520px;
  overflow-y: auto;
  padding-right: 4px;
}
.diff-step {
  border: 1px solid var(--card-border);
  border-radius: 10px;
  overflow: hidden;
  background: #fafbfd;
}
.diff-step.is-failed {
  border-color: #fbc4c4;
}
.diff-step-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  background: #f4f6fa;
  border-bottom: 1px solid var(--card-border);
  flex-wrap: wrap;
}
.diff-step-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #2e4c86;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}
.diff-step-name {
  font-weight: 600;
  color: #1d2b4f;
}
.diff-step-type {
  font-size: 12px;
  color: #909399;
}
.diff-step-time {
  margin-left: auto;
  font-size: 12px;
  color: #909399;
}
.diff-step-error {
  width: 100%;
  font-size: 12px;
  color: #f56c6c;
}

.diff-panes {
  display: grid;
  grid-template-columns: 1fr 1fr;
}
.diff-pane {
  padding: 10px 12px;
  min-width: 0;
}
.diff-pane.is-minus {
  border-right: 1px solid var(--card-border);
  background: #fff5f5;
}
.diff-pane.is-plus {
  background: #f2fbf3;
}
.diff-pane-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 8px;
}
.diff-pane.is-minus .diff-pane-label {
  color: #f56c6c;
}
.diff-pane.is-plus .diff-pane-label {
  color: #2da44e;
}
.diff-sign {
  font-size: 15px;
  line-height: 1;
}
.diff-json {
  margin: 0;
  font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow: auto;
}
.diff-json.is-empty {
  color: #b8bcc4;
  font-style: italic;
}
</style>
