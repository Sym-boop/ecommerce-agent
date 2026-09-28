<template>
  <div class="approvals-page">
    <!-- ===== 顶部筛选 ===== -->
    <div class="page-toolbar">
      <el-radio-group v-model="activeTab" @change="onTabChange">
        <el-radio-button value="pending">
          待审批
          <el-badge
            v-if="approvalStore.pendingList.length"
            :value="approvalStore.pendingList.length"
            :max="99"
            class="tab-badge"
          />
        </el-radio-button>
        <el-radio-button value="processed">已处理</el-radio-button>
      </el-radio-group>

      <el-select
        v-model="approvalStore.filters.type"
        placeholder="全部类型"
        clearable
        style="width: 140px"
        @change="reload"
      >
        <el-option label="文案发布" value="copy" />
        <el-option label="调价申请" value="pricing" />
        <el-option label="广告出价" value="ads" />
      </el-select>

      <el-date-picker
        v-model="dateRange"
        type="daterange"
        value-format="YYYY-MM-DD"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        style="width: 260px"
        @change="onDateChange"
      />

      <el-button :icon="Refresh" @click="reload">刷新</el-button>
    </div>

    <!-- ===== 待审批列表 ===== -->
    <div v-loading="approvalStore.loading">
      <template v-if="activeTab === 'pending'">
        <el-empty
          v-if="approvalStore.pendingList.length === 0"
          description="暂无待审批事项，风控熔断触发的文案/调价/广告审批将实时推送到这里"
        />
        <el-space v-else direction="vertical" fill :size="14" style="display: flex">
          <!-- 文案审批卡 -->
          <el-card
            v-for="item in pendingCopies"
            :key="item.id"
            shadow="hover"
            class="approval-card"
            :class="{ 'is-critical': item.priority === 'critical' }"
          >
            <template #header>
              <div class="card-head">
                <div class="card-head-left">
                  <el-tag type="primary">文案发布</el-tag>
                  <el-tag effect="plain">{{ PLATFORM_META[item.platform] || item.platform }}</el-tag>
                  <el-tag effect="plain" size="small">{{ item.language }}</el-tag>
                  <span class="card-title">{{ item.product_name }}</span>
                  <span class="mono card-sku">{{ item.sku }}</span>
                </div>
                <div class="card-head-right">
                  <span class="mono card-time">{{ formatTime(item.created_at) }}</span>
                  <el-tag type="warning" effect="dark">待审批</el-tag>
                </div>
              </div>
            </template>

            <el-row :gutter="18">
              <el-col :span="15">
                <div class="field-label">
                  文案标题
                  <span class="field-hint">可在线微调，保存后生成新版本</span>
                </div>
                <el-input v-model="drafts[item.id].title" placeholder="文案标题" class="edit-title" />

                <div class="field-label mt12">
                  文案正文
                  <el-button link type="primary" size="small" @click="resetDraft(item)">
                    恢复 Agent 原稿
                  </el-button>
                </div>
                <el-input
                  v-model="drafts[item.id].content"
                  type="textarea"
                  :rows="9"
                  resize="vertical"
                  class="copy-textarea"
                />
              </el-col>

              <el-col :span="9">
                <el-alert
                  v-if="item.violations.length"
                  :type="resolvedCount(item) === item.violations.length ? 'success' : 'error'"
                  :closable="false"
                  show-icon
                  class="violation-box"
                  :title="resolvedCount(item) === 0
                    ? `违禁词 / 风险表达扫描：命中 ${item.violations.length} 处`
                    : `违禁词 / 风险表达扫描：命中 ${item.violations.length} 处，已处理 ${resolvedCount(item)} 处`"
                >
                  <div
                    v-for="(v, i) in item.violations"
                    :key="i"
                    class="violation-row"
                    :class="{ 'is-resolved': isResolved(item, v.word) }"
                  >
                    <div>
                      <el-tag
                        :type="isResolved(item, v.word) ? 'success' : 'danger'"
                        size="small"
                        :effect="isResolved(item, v.word) ? 'light' : 'plain'"
                      >{{ v.word }}</el-tag>
                      <span class="violation-type">{{ v.type }}</span>
                      <span v-if="isResolved(item, v.word)" class="resolved-text">已替换</span>
                    </div>
                    <el-button
                      size="small"
                      :type="isResolved(item, v.word) ? 'success' : 'default'"
                      :disabled="isResolved(item, v.word)"
                      @click="applySuggestion(item, v)"
                    >
                      {{ isResolved(item, v.word) ? '已替换' : `替换为「${v.suggestion}」` }}
                    </el-button>
                  </div>
                </el-alert>
                <el-alert
                  v-else
                  type="success"
                  :closable="false"
                  show-icon
                  title="违禁词扫描通过，未命中风险词"
                  class="violation-box"
                />

                <div class="field-label mt12">版本记录（{{ item.versions.length }}）</div>
                <el-timeline class="version-timeline">
                  <el-timeline-item
                    v-for="ver in [...item.versions].reverse()"
                    :key="ver.version"
                    :timestamp="`v${ver.version} · ${ver.editor} · ${formatTime(ver.created_at)}`"
                    placement="top"
                  />
                </el-timeline>
              </el-col>
            </el-row>

            <template #footer>
              <div class="card-footer">
                <el-button
                  :loading="savingId === item.id"
                  @click="handleSaveDraft(item)"
                >
                  保存微调版本
                </el-button>
                <span class="spacer"></span>
                <el-button type="danger" plain @click="openReject(item)">驳回</el-button>
                <el-button type="success" @click="handleApproveCopy(item)">
                  微调后通过并发布
                </el-button>
              </div>
            </template>
          </el-card>

          <!-- 调价审批卡 -->
          <el-card
            v-for="item in pendingPricings"
            :key="item.id"
            shadow="hover"
            class="approval-card"
          >
            <template #header>
              <div class="card-head">
                <div class="card-head-left">
                  <el-tag type="warning">调价申请</el-tag>
                  <el-tag type="danger" effect="dark" v-if="item.threshold_exceeded">
                    超过 ±{{ item.threshold_percent }}% 阈值
                  </el-tag>
                  <span class="card-title">{{ item.product_name }}</span>
                  <span class="mono card-sku">{{ item.sku }}</span>
                </div>
                <div class="card-head-right">
                  <span class="mono card-time">{{ formatTime(item.created_at) }}</span>
                  <el-tag type="warning" effect="dark">待审批</el-tag>
                </div>
              </div>
            </template>

            <el-row :gutter="18">
              <el-col :span="14">
                <div class="price-compare">
                  <div class="price-box">
                    <div class="price-label">当前售价</div>
                    <div class="price-old">¥{{ item.current_price.toFixed(2) }}</div>
                  </div>
                  <el-icon :size="22" color="#909399"><Right /></el-icon>
                  <div class="price-box">
                    <div class="price-label">建议售价</div>
                    <div class="price-new">¥{{ item.suggested_price.toFixed(2) }}</div>
                  </div>
                  <el-tag
                    :type="item.change_percent < 0 ? 'danger' : 'success'"
                    size="large"
                    effect="dark"
                    class="price-change"
                  >
                    {{ item.change_percent > 0 ? '+' : '' }}{{ item.change_percent.toFixed(2) }}%
                  </el-tag>
                </div>

                <el-descriptions :column="2" border size="small" class="mt12">
                  <el-descriptions-item label="建议动作">{{ item.action }}</el-descriptions-item>
                  <el-descriptions-item label="置信度">
                    <el-progress
                      :percentage="Math.round(item.confidence * 100)"
                      :status="item.confidence >= 0.8 ? 'success' : ''"
                      :stroke-width="14"
                    />
                  </el-descriptions-item>
                  <el-descriptions-item label="预期收益" :span="2">
                    {{ item.expected_revenue }}
                  </el-descriptions-item>
                  <el-descriptions-item label="风险评估" :span="2">
                    <el-tag :type="RISK_META[item.risk_level]?.type" size="small">
                      {{ RISK_META[item.risk_level]?.label }}
                    </el-tag>
                    <span class="risk-note">{{ item.risk_note }}</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="调价依据" :span="2">
                    {{ item.reason }}
                  </el-descriptions-item>
                </el-descriptions>
              </el-col>

              <el-col :span="10">
                <div class="field-label">竞品实时比价（ERP MCP）</div>
                <el-table :data="item.competitors" size="small" border>
                  <el-table-column prop="name" label="竞品" min-width="130" />
                  <el-table-column label="到手价" width="110" align="right">
                    <template #default="{ row }">
                      <span :class="{ 'cheaper': row.price + (row.shipping || 0) <= item.suggested_price }">
                        ¥{{ (row.price + (row.shipping || 0)).toFixed(2) }}
                      </span>
                    </template>
                  </el-table-column>
                </el-table>
                <el-alert
                  class="mt12"
                  :type="item.threshold_exceeded ? 'error' : 'info'"
                  :closable="false"
                  show-icon
                  :title="
                    item.threshold_exceeded
                      ? `降幅超过风控阈值 ±${item.threshold_percent}%，Agent 已暂停调价执行`
                      : '波动在风控阈值内，可快速审批'
                  "
                />
              </el-col>
            </el-row>

            <template #footer>
              <div class="card-footer">
                <span class="spacer"></span>
                <el-button type="danger" plain @click="openReject(item)">驳回</el-button>
                <el-button type="success" @click="handleApprove(item)">一键通过并调价</el-button>
              </div>
            </template>
          </el-card>

          <!-- 广告出价审批卡 -->
          <el-card
            v-for="item in pendingAds"
            :key="item.id"
            shadow="hover"
            class="approval-card is-critical"
          >
            <template #header>
              <div class="card-head">
                <div class="card-head-left">
                  <el-tag type="danger">广告出价</el-tag>
                  <el-tag effect="plain">{{ item.platform }}</el-tag>
                  <span class="card-title">{{ item.campaign_name }}</span>
                </div>
                <div class="card-head-right">
                  <el-tag type="danger" effect="dark">预算熔断阻断中</el-tag>
                  <span class="mono card-time">{{ formatTime(item.created_at) }}</span>
                </div>
              </div>
            </template>

            <el-row :gutter="18">
              <el-col :span="12">
                <el-descriptions :column="2" border size="small">
                  <el-descriptions-item label="计划出价">
                    <b class="danger-text">¥{{ item.bid.toFixed(2) }}</b>
                  </el-descriptions-item>
                  <el-descriptions-item label="出价阈值">
                    ¥{{ item.bid_threshold.toFixed(2) }}
                  </el-descriptions-item>
                  <el-descriptions-item label="日预算">
                    ¥{{ item.daily_budget.toLocaleString() }}
                  </el-descriptions-item>
                  <el-descriptions-item label="总预算">
                    <b class="danger-text">¥{{ item.total_budget.toLocaleString() }}</b>
                    <span class="threshold-hint">（阈值 ¥{{ item.budget_threshold.toLocaleString() }}）</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="ROI 预测">
                    <b>{{ item.roi_forecast }}</b>
                  </el-descriptions-item>
                  <el-descriptions-item label="预计点击">{{ item.expected_clicks }}</el-descriptions-item>
                  <el-descriptions-item label="申请原因" :span="2">{{ item.reason }}</el-descriptions-item>
                </el-descriptions>
              </el-col>
              <el-col :span="12">
                <div class="field-label">预算渠道分配</div>
                <div v-for="row in item.allocation" :key="row.channel" class="alloc-row">
                  <div class="alloc-head">
                    <span>{{ row.channel }}</span>
                    <span class="mono">
                      {{ row.percent }}% · ¥{{ row.amount.toLocaleString() }}
                    </span>
                  </div>
                  <el-progress :percentage="row.percent" :show-text="false" :stroke-width="12" />
                </div>
                <el-alert
                  type="error"
                  :closable="false"
                  show-icon
                  class="mt12"
                  title="出价与总预算超过风控阈值，广告下发 API 已被熔断阻断；审批通过后方可执行投放"
                />
              </el-col>
            </el-row>

            <template #footer>
              <div class="card-footer">
                <span class="spacer"></span>
                <el-button type="danger" plain @click="openReject(item)">驳回</el-button>
                <el-button type="success" @click="handleApprove(item)">确认通过并下发投放</el-button>
              </div>
            </template>
          </el-card>
        </el-space>
      </template>

      <!-- ===== 已处理记录 ===== -->
      <el-card v-else shadow="never">
        <el-table :data="approvalStore.processedList" v-loading="approvalStore.loading" stripe>
          <el-table-column prop="id" label="审批单号" width="170" class-name="mono" />
          <el-table-column label="类型" width="100">
            <template #default="{ row }">
              <el-tag :type="APPROVAL_TYPE_META[row.type]?.type" size="small">
                {{ APPROVAL_TYPE_META[row.type]?.label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="摘要" min-width="220">
            <template #default="{ row }">{{ summaryOf(row) }}</template>
          </el-table-column>
          <el-table-column prop="task_id" label="关联任务" width="180" class-name="mono" />
          <el-table-column label="结果" width="90">
            <template #default="{ row }">
              <el-tag :type="APPROVAL_STATUS_META[row.status]?.type" size="small">
                {{ APPROVAL_STATUS_META[row.status]?.label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="handler" label="处理人" width="110" />
          <el-table-column label="处理时间" width="170">
            <template #default="{ row }">{{ formatTime(row.handled_at) }}</template>
          </el-table-column>
          <el-table-column prop="comment" label="审批意见" min-width="160" show-overflow-tooltip />
        </el-table>
      </el-card>
    </div>

    <!-- ===== 驳回原因对话框 ===== -->
    <el-dialog v-model="rejectDialogVisible" title="驳回审批" width="460px">
      <div class="reject-target">
        <el-tag :type="APPROVAL_TYPE_META[rejectTarget?.type]?.type" size="small">
          {{ APPROVAL_TYPE_META[rejectTarget?.type]?.label }}
        </el-tag>
        <span>{{ summaryOf(rejectTarget) }}</span>
      </div>
      <el-input
        v-model="rejectComment"
        type="textarea"
        :rows="4"
        maxlength="200"
        show-word-limit
        placeholder="请填写驳回原因（必填），将随熔断环路回传给 Agent 与提交人"
      />
      <template #footer>
        <el-button @click="rejectDialogVisible = false">取消</el-button>
        <el-button type="danger" :loading="acting" @click="confirmReject">确认驳回</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Right } from '@element-plus/icons-vue'
import { useApprovalStore } from '@/stores/approval'
import {
  formatTime,
  PLATFORM_META,
  APPROVAL_TYPE_META,
  APPROVAL_STATUS_META,
} from '@/utils/format'

const approvalStore = useApprovalStore()
const activeTab = ref('pending')
const dateRange = ref(null)

const RISK_META = {
  low: { label: '低风险', type: 'success' },
  medium: { label: '中风险', type: 'warning' },
  high: { label: '高风险', type: 'danger' },
}

const pendingCopies = computed(() => approvalStore.pendingList.filter((a) => a.type === 'copy'))
const pendingPricings = computed(() => approvalStore.pendingList.filter((a) => a.type === 'pricing'))
const pendingAds = computed(() => approvalStore.pendingList.filter((a) => a.type === 'ads'))

// ---------- 筛选 ----------
function onDateChange(val) {
  if (val && val.length === 2) {
    approvalStore.filters.start_time = `${val[0]}T00:00:00`
    approvalStore.filters.end_time = `${val[1]}T23:59:59`
  } else {
    approvalStore.filters.start_time = ''
    approvalStore.filters.end_time = ''
  }
  reload()
}

function onTabChange() {
  reload()
}

function reload() {
  if (activeTab.value === 'pending') approvalStore.fetchPending()
  else approvalStore.fetchProcessed()
}

// ---------- 文案微调 ----------
const drafts = reactive({})
const savingId = ref('')

watch(
  () => approvalStore.pendingList,
  (list) => {
    // 为新到达的文案审批单初始化本地草稿
    list.forEach((item) => {
      if (item.type === 'copy' && !drafts[item.id]) {
        drafts[item.id] = { title: item.title, content: item.content }
      }
    })
  },
  { deep: true, immediate: true },
)

function resetDraft(item) {
  drafts[item.id].title = item.title
  drafts[item.id].content = item.content
}

function applySuggestion(item, violation) {
  const draft = drafts[item.id]
  if (!draft.content.includes(violation.word)) {
    ElMessage.info('当前正文中已不存在该词')
    return
  }
  draft.content = draft.content.split(violation.word).join(violation.suggestion)
  ElMessage.success(`已将「${violation.word}」替换为「${violation.suggestion}」`)
}

// 违禁词是否已从草稿正文中移除（用于按钮禁用与"已替换"态）
function isResolved(item, word) {
  const draft = drafts[item.id]
  return !draft || !draft.content.includes(word)
}

function resolvedCount(item) {
  return item.violations.filter((v) => isResolved(item, v.word)).length
}

async function handleSaveDraft(item) {
  savingId.value = item.id
  try {
    const { version } = await approvalStore.saveDraft(item.id, drafts[item.id].content)
    item.title = drafts[item.id].title
    ElMessage.success(`微调内容已保存为 v${version}`)
  } finally {
    savingId.value = ''
  }
}

// ---------- 审批操作 ----------
const acting = ref(false)
const rejectDialogVisible = ref(false)
const rejectTarget = ref(null)
const rejectComment = ref('')

function openReject(item) {
  rejectTarget.value = item
  rejectComment.value = ''
  rejectDialogVisible.value = true
}

async function confirmReject() {
  if (!rejectComment.value.trim()) {
    ElMessage.warning('请先填写驳回原因')
    return
  }
  acting.value = true
  try {
    await approvalStore.reject(rejectTarget.value.id, rejectComment.value.trim())
    ElMessage.success('已驳回，结果将沿熔断环路回传')
    rejectDialogVisible.value = false
  } finally {
    acting.value = false
  }
}

async function handleApprove(item) {
  try {
    await ElMessageBox.confirm(
      `确认通过该${APPROVAL_TYPE_META[item.type].label}？操作将在 2 秒内下发执行。`,
      '审批确认',
      { type: 'warning', confirmButtonText: '确认通过', cancelButtonText: '再想想' },
    )
  } catch {
    return
  }
  acting.value = true
  try {
    await approvalStore.approve(item.id, {})
    ElMessage.success('审批通过，已下发执行')
  } finally {
    acting.value = false
  }
}

async function handleApproveCopy(item) {
  const draft = drafts[item.id]
  const edited = draft.content !== item.content || draft.title !== item.title
  try {
    await ElMessageBox.confirm(
      edited
        ? '将以微调后的内容通过并发布到平台，确认继续？'
        : '未做修改，将按 Agent 原稿通过并发布，确认继续？',
      '文案发布确认',
      { type: 'warning', confirmButtonText: '确认通过并发布', cancelButtonText: '再想想' },
    )
  } catch {
    return
  }
  acting.value = true
  try {
    await approvalStore.approve(item.id, {
      edited_content: edited ? draft.content : undefined,
      comment: edited ? '微调后通过' : '原稿通过',
    })
    ElMessage.success('文案审批通过，已进入发布队列')
  } finally {
    acting.value = false
  }
}

function summaryOf(row) {
  if (!row) return ''
  if (row.type === 'copy') return `《${row.title || row.product_name}》→ ${PLATFORM_META[row.platform] || row.platform}`
  if (row.type === 'pricing')
    return `${row.product_name}：¥${row.current_price} → ¥${row.suggested_price}（${row.change_percent > 0 ? '+' : ''}${row.change_percent?.toFixed(2)}%）`
  return row.campaign_name
}

onMounted(() => {
  approvalStore.fetchPending()
  approvalStore.fetchProcessed()
})
</script>

<style scoped>
.tab-badge {
  margin-left: 6px;
}

.approval-card.is-critical {
  border-color: #f56c6c;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}
.card-head-left,
.card-head-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.card-title {
  font-weight: 600;
}
.card-sku,
.card-time {
  font-size: 12px;
  color: #909399;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.field-hint {
  font-weight: 400;
  font-size: 12px;
  color: #909399;
}
.mt12 {
  margin-top: 12px;
}
.copy-textarea :deep(textarea) {
  line-height: 1.7;
}
.edit-title {
  font-weight: 600;
}

.violation-box {
  margin-bottom: 0;
}
.violation-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px dashed #fde2e2;
}
.violation-row:last-child {
  border-bottom: none;
}
.violation-type {
  margin-left: 8px;
  font-size: 12px;
  color: #909399;
}
.violation-row.is-resolved {
  border-bottom-color: #e1f3d8;
  opacity: 0.75;
}
.resolved-text {
  margin-left: 8px;
  font-size: 12px;
  color: #67c23a;
  font-weight: 600;
}
.version-timeline {
  padding-left: 4px;
  margin-top: 8px;
  max-height: 180px;
  overflow-y: auto;
}
.version-timeline :deep(.el-timeline-item__timestamp) {
  color: #909399;
  font-size: 12px;
}

.price-compare {
  display: flex;
  align-items: center;
  gap: 18px;
  background: #f7f9fc;
  border-radius: 10px;
  padding: 16px 20px;
}
.price-box {
  flex: 1;
}
.price-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
.price-old {
  font-size: 26px;
  color: #909399;
  text-decoration: line-through;
  font-weight: 700;
}
.price-new {
  font-size: 30px;
  color: #f56c6c;
  font-weight: 700;
}
.price-change {
  font-size: 16px;
}
.risk-note {
  margin-left: 10px;
  font-size: 12px;
  color: #606266;
}
.cheaper {
  color: #67c23a;
  font-weight: 700;
}

.danger-text {
  color: #f56c6c;
}
.threshold-hint {
  font-size: 12px;
  color: #909399;
  margin-left: 6px;
}
.alloc-row {
  margin-bottom: 12px;
}
.alloc-head {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  margin-bottom: 4px;
}

.card-footer {
  display: flex;
  align-items: center;
  gap: 10px;
}
.spacer {
  flex: 1;
}

.reject-target {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  font-size: 13px;
}
</style>
