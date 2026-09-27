<template>
  <div class="canvas-page">
    <!-- ===== 顶部工具栏 ===== -->
    <div class="page-toolbar">
      <el-button type="primary" :icon="Promotion" @click="goalDialogVisible = true">
        下发运营目标
      </el-button>
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
        description="暂无任务，点击「下发运营目标」启动 Supervisor 调度链路"
      >
        <el-button type="primary" @click="goalDialogVisible = true">立即下发目标</el-button>
      </el-empty>

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

    <!-- ===== 下发目标对话框 ===== -->
    <el-dialog v-model="goalDialogVisible" title="下发运营目标" width="560px">
      <el-form label-position="top">
        <el-form-item label="运营目标（自然语言）" required>
          <el-input
            v-model="goalForm.goal"
            type="textarea"
            :rows="3"
            placeholder="例如：给新品做一波小红书/TikTok 推广，并根据竞品价格给出调价建议"
          />
        </el-form-item>
        <el-form-item label="广告总预算（元，≥50000 将触发预算熔断审批）">
          <el-input-number v-model="goalForm.budget" :min="0" :step="5000" :max="500000" />
        </el-form-item>
        <el-form-item label="快捷示例">
          <div class="quick-goals">
            <el-tag
              v-for="(q, i) in QUICK_GOALS"
              :key="i"
              class="quick-tag"
              @click="goalForm.goal = q.goal; goalForm.budget = q.budget"
            >
              {{ q.label }}
            </el-tag>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="goalDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="taskStore.submitting" @click="handleSubmitGoal">
          下发并调度
        </el-button>
      </template>
    </el-dialog>

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
  Promotion, ZoomIn, ZoomOut, FullScreen, Refresh, CircleClose, RefreshRight,
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

// ---------- 目标下发 ----------
const goalDialogVisible = ref(false)
const goalForm = reactive({ goal: '', budget: 0 })
const QUICK_GOALS = [
  { label: '🌸 新品种草推广', goal: '给新品法式碎花连衣裙做一波小红书和TikTok种草推广', budget: 0 },
  { label: '📉 竞品跟价', goal: '监控降噪耳机Pro3的竞品价格，给出调价建议', budget: 0 },
  { label: '🔥 大促全链路（含广告熔断）', goal: '黑五大促：新品连衣裙全平台种草推广、跟进竞品调价并加投千川信息流广告', budget: 80000 },
]

async function handleSubmitGoal() {
  if (!goalForm.goal.trim()) {
    ElMessage.warning('请先填写运营目标')
    return
  }
  await taskStore.submitGoal({ goal: goalForm.goal.trim(), budget: goalForm.budget || undefined })
  goalDialogVisible.value = false
  goalForm.goal = ''
  goalForm.budget = 0
  ElMessage.success('目标已下发，Supervisor 开始拆解调度')
  await nextTick()
  fitView()
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
</style>
