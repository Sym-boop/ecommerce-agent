<template>
  <div class="approvals-page">
    <!-- ===== 顶部筛选 ===== -->
    <div class="page-toolbar">
      <el-radio-group v-model="activeTab" @change="reload">
        <el-radio-button value="copy-review">
          违禁词审查
          <el-badge v-if="stats.copy_review" :value="stats.copy_review" :max="99" type="danger" class="tab-badge" />
        </el-radio-button>
        <el-radio-button value="budget-review">
          投放预算审查
          <el-badge v-if="stats.budget_review" :value="stats.budget_review" :max="99" type="danger" class="tab-badge" />
        </el-radio-button>
        <el-radio-button value="pending">
          待审批
          <el-badge v-if="stats.pending" :value="stats.pending" :max="99" type="danger" class="tab-badge" />
        </el-radio-button>
        <el-radio-button value="processed">已处理</el-radio-button>
      </el-radio-group>

      <span v-if="reviewConfig" class="review-config-hint">
        违禁词库 {{ reviewConfig.banned_word_count }} 条 ·
        投放预算区间 ¥{{ reviewConfig.min_budget.toLocaleString() }} ~ ¥{{ reviewConfig.max_budget.toLocaleString() }}
      </span>

      <el-button :icon="Refresh" @click="reloadAll">刷新</el-button>
    </div>

    <!-- ===== 流水线说明 ===== -->
    <el-steps :active="stageStep" align-center finish-status="success" class="pipeline-steps">
      <el-step title="生成广告" />
      <el-step title="审查处置" :description="`违禁词 ${stats.copy_review} · 预算 ${stats.budget_review}`" />
      <el-step title="待审批" :description="`${stats.pending} 条`" />
      <el-step title="广告投放" :description="`已审批 ${stats.approved} 条`" />
      <el-step title="历史发售" :description="`已投放 ${stats.delivered} 条`" />
    </el-steps>

    <!-- ===== 违禁词审查 ===== -->
    <div v-if="activeTab === 'copy-review'" v-loading="copyLoading" class="review-block">
      <el-alert
        class="review-summary"
        :type="copyReview.total ? 'warning' : 'success'"
        :closable="false"
        show-icon
        :title="`待处置 ${copyReview.total} 条命中违禁词的广告，共命中 ${copyReview.hit_word_total} 处风险表达`"
      >
        <div class="review-summary-desc">
          依据 <span class="mono">{{ reviewConfig?.banned_word_source || 'data/raw/forbidden_words.txt' }}</span>
          违禁词库逐条扫描广告标题与正文；修正后放行，待预算审查一并通过后自动进入待审批。
        </div>
      </el-alert>

      <el-empty v-if="!copyLoading && copyReview.items.length === 0" description="暂无命中违禁词的广告，生成广告后自动送审" />

      <el-space v-else direction="vertical" fill :size="14" style="display: flex">
        <el-card
          v-for="row in copyReview.items"
          :key="row.id"
          shadow="hover"
          class="review-card is-abnormal"
        >
          <template #header>
            <div class="card-head">
              <div class="card-head-left">
                <el-tag :type="AD_TYPE_META[row.type]?.tagType || 'info'" effect="plain">
                  {{ AD_TYPE_META[row.type]?.label || row.type }}
                </el-tag>
                <el-tag type="danger" effect="dark">命中违禁词</el-tag>
                <span class="card-title">{{ row.title || '（无标题）' }}</span>
                <span class="mono card-sku">{{ row.id }}</span>
              </div>
              <span class="mono card-time">{{ formatTime(row.created_at) }}</span>
            </div>
          </template>

          <el-row :gutter="18">
            <el-col :span="15">
              <div class="field-label">
                广告标题
                <el-button link type="primary" size="small" @click="resetDraft(row)">恢复原稿</el-button>
              </div>
              <el-input v-model="drafts[row.id].title" placeholder="广告标题" class="edit-title" />
              <div class="field-label mt12">广告正文</div>
              <el-input v-model="drafts[row.id].content" type="textarea" :rows="7" resize="vertical" class="copy-textarea" />
            </el-col>
            <el-col :span="9">
              <el-alert
                :type="resolvedCount(row) === row.violations.length ? 'success' : 'error'"
                :closable="false"
                show-icon
                class="violation-box"
                :title="`命中 ${row.violations.length} 处，已处理 ${resolvedCount(row)} 处`"
              >
                <div
                  v-for="(v, i) in row.violations"
                  :key="i"
                  class="violation-row"
                  :class="{ 'is-resolved': isResolved(row, v.word) }"
                >
                  <div>
                    <el-tag :type="isResolved(row, v.word) ? 'success' : 'danger'" size="small" effect="plain">
                      {{ v.word }}
                    </el-tag>
                    <span class="violation-type">{{ v.type }}</span>
                  </div>
                  <el-button
                    v-if="v.suggestion"
                    size="small"
                    :type="isResolved(row, v.word) ? 'success' : 'default'"
                    :disabled="isResolved(row, v.word)"
                    @click="applySuggestion(row, v)"
                  >
                    {{ isResolved(row, v.word) ? '已替换' : `替换为「${v.suggestion}」` }}
                  </el-button>
                  <span v-else class="violation-type">请在左侧手动修改</span>
                </div>
              </el-alert>
              <div class="field-label mt12">预算状态</div>
              <el-tag :type="row.budget_ok ? 'success' : 'danger'" effect="plain">
                {{ row.budget_ok ? '预算正常' : row.budget_msg || '预算异常' }}
              </el-tag>
            </el-col>
          </el-row>

          <template #footer>
            <div class="card-footer">
              <span class="spacer"></span>
              <el-button
                type="success"
                :loading="actingId === row.id"
                :disabled="resolvedCount(row) !== row.violations.length"
                @click="handleClearCopy(row)"
              >
                确认修正并放行
              </el-button>
            </div>
          </template>
        </el-card>
      </el-space>
    </div>

    <!-- ===== 投放预算审查 ===== -->
    <div v-else-if="activeTab === 'budget-review'" v-loading="budgetLoading" class="review-block">
      <el-alert
        class="review-summary"
        :type="budgetReview.abnormal_count ? 'warning' : 'success'"
        :closable="false"
        show-icon
        :title="`待处置 ${budgetReview.abnormal_count} 条预算异常的广告`"
      >
        <div class="review-summary-desc">
          审查区间：¥{{ budgetReview.min_budget?.toLocaleString() }} ~ ¥{{ budgetReview.max_budget?.toLocaleString() }}；
          低于下限视为预算过低，高于上限视为预算超支。调整预算后放行，与违禁词审查一并通过后进入待审批。
        </div>
      </el-alert>

      <el-empty v-if="!budgetLoading && budgetReview.items.length === 0" description="暂无预算异常的广告，生成广告时填写投放预算即可自动送审" />

      <el-table v-else :data="budgetReview.items" stripe border class="budget-table">
        <el-table-column prop="id" label="广告编号" width="190" class-name="mono" />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag :type="AD_TYPE_META[row.type]?.tagType || 'info'" size="small" effect="plain">
              {{ AD_TYPE_META[row.type]?.label || row.type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标题 / 摘要" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.title || row.prompt || '（无标题）' }}</template>
        </el-table-column>
        <el-table-column label="投放预算" width="140" align="right">
          <template #default="{ row }">¥{{ Number(row.budget || 0).toLocaleString() }}</template>
        </el-table-column>
        <el-table-column label="审查结论" width="120">
          <template #default="{ row }">
            <el-tag :type="VERDICT_META[row.budget_verdict]?.type" effect="dark" size="small">
              {{ VERDICT_META[row.budget_verdict]?.label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="220">
          <template #default="{ row }">{{ row.budget_msg }}</template>
        </el-table-column>
        <el-table-column label="违禁词" width="100">
          <template #default="{ row }">
            <el-tag :type="row.copy_ok ? 'success' : 'danger'" size="small" effect="plain">
              {{ row.copy_ok ? '通过' : '待处置' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="openBudgetDialog(row)">调整预算并放行</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ===== 待审批 ===== -->
    <div v-else-if="activeTab === 'pending'" v-loading="pendingLoading" class="review-block">
      <el-alert
        class="review-summary"
        type="info"
        :closable="false"
        show-icon
        :title="`${pendingList.length} 条广告已通过违禁词与预算审查，等待人工审批`"
      >
        <div class="review-summary-desc">审批通过后广告进入「广告投放」；驳回则进入已处理并退出流水线。</div>
      </el-alert>

      <el-empty v-if="!pendingLoading && pendingList.length === 0" description="暂无待审批广告，完成审查处置后会自动汇聚到这里" />

      <el-space v-else direction="vertical" fill :size="14" style="display: flex">
        <el-card v-for="item in pendingList" :key="item.id" shadow="hover" class="approval-card">
          <template #header>
            <div class="card-head">
              <div class="card-head-left">
                <el-tag :type="AD_TYPE_META[item.type]?.tagType || 'info'" effect="plain">
                  {{ AD_TYPE_META[item.type]?.label || item.type }}
                </el-tag>
                <el-tag effect="plain">{{ PLATFORM_META[item.platform] || item.platform || '未指定平台' }}</el-tag>
                <span class="card-title">{{ item.title || '（无标题）' }}</span>
                <span class="mono card-sku">{{ item.id }}</span>
              </div>
              <div class="card-head-right">
                <span class="mono card-time">{{ formatTime(item.created_at) }}</span>
                <el-tag type="warning" effect="dark">待审批</el-tag>
              </div>
            </div>
          </template>

          <el-row :gutter="18">
            <el-col :span="15">
              <div v-if="item.type === 'copy'" class="review-copy-body">{{ item.content || '—' }}</div>
              <template v-else>
                <el-image
                  v-if="item.type === 'image' && item.result_url"
                  class="approval-media"
                  :src="mediaUrl(item.result_url)"
                  :preview-src-list="[mediaUrl(item.result_url)]"
                  fit="cover"
                  hide-on-click-modal
                />
                <video v-else-if="item.type === 'video' && item.result_url" class="approval-media" :src="mediaUrl(item.result_url)" controls preload="metadata"></video>
                <div v-if="item.content" class="review-copy-body mt12">{{ item.content }}</div>
              </template>
            </el-col>
            <el-col :span="9">
              <el-descriptions :column="1" border size="small">
                <el-descriptions-item label="生成提示词">{{ item.prompt || '—' }}</el-descriptions-item>
                <el-descriptions-item label="投放预算">
                  ¥{{ Number(item.budget || 0).toLocaleString() }}
                  <span class="threshold-hint">{{ item.budget_msg }}</span>
                </el-descriptions-item>
                <el-descriptions-item label="审查结论">
                  <el-tag type="success" size="small" effect="plain">违禁词通过</el-tag>
                  <el-tag type="success" size="small" effect="plain" style="margin-left: 6px">预算通过</el-tag>
                </el-descriptions-item>
              </el-descriptions>
            </el-col>
          </el-row>

          <template #footer>
            <div class="card-footer">
              <span class="spacer"></span>
              <el-button type="danger" plain @click="openReject(item)">驳回</el-button>
              <el-button type="success" :loading="actingId === item.id" @click="handleApprove(item)">通过并进入投放</el-button>
            </div>
          </template>
        </el-card>
      </el-space>
    </div>

    <!-- ===== 已处理 ===== -->
    <div v-else v-loading="processedLoading" class="review-block">
      <el-card shadow="never">
        <el-table :data="processedList" stripe>
          <el-table-column prop="id" label="广告编号" width="190" class-name="mono" />
          <el-table-column label="类型" width="100">
            <template #default="{ row }">
              <el-tag :type="AD_TYPE_META[row.type]?.tagType || 'info'" size="small" effect="plain">
                {{ AD_TYPE_META[row.type]?.label || row.type }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="标题 / 摘要" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">{{ row.title || row.prompt || '（无标题）' }}</template>
          </el-table-column>
          <el-table-column label="阶段" width="110">
            <template #default="{ row }">
              <el-tag :type="STAGE_META[row.stage]?.type" size="small" effect="dark">
                {{ STAGE_META[row.stage]?.label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="投放预算" width="130" align="right">
            <template #default="{ row }">¥{{ Number(row.budget || 0).toLocaleString() }}</template>
          </el-table-column>
          <el-table-column prop="handler" label="处理人" width="120" />
          <el-table-column label="处理时间" width="170">
            <template #default="{ row }">{{ formatTime(row.handled_at) }}</template>
          </el-table-column>
          <el-table-column prop="comment" label="审批意见" min-width="160" show-overflow-tooltip />
        </el-table>
      </el-card>
    </div>

    <!-- ===== 调整预算对话框 ===== -->
    <el-dialog v-model="budgetDialogVisible" title="调整投放预算并放行" width="440px">
      <div class="reject-target">
        <el-tag size="small" effect="plain">{{ budgetTarget?.id }}</el-tag>
        <span>{{ budgetTarget?.title || budgetTarget?.prompt }}</span>
      </div>
      <el-input-number v-model="budgetInput" :min="0" :step="1000" :controls="false" style="width: 200px" />
      <span class="budget-hint">
        合理区间 ¥{{ budgetReview.min_budget?.toLocaleString() }} ~ ¥{{ budgetReview.max_budget?.toLocaleString() }}
      </span>
      <template #footer>
        <el-button @click="budgetDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="acting" @click="confirmClearBudget">确认放行</el-button>
      </template>
    </el-dialog>

    <!-- ===== 驳回原因对话框 ===== -->
    <el-dialog v-model="rejectDialogVisible" title="驳回审批" width="460px">
      <div class="reject-target">
        <el-tag size="small" effect="plain">{{ rejectTarget?.id }}</el-tag>
        <span>{{ rejectTarget?.title || rejectTarget?.prompt }}</span>
      </div>
      <el-input
        v-model="rejectComment"
        type="textarea"
        :rows="4"
        maxlength="200"
        show-word-limit
        placeholder="请填写驳回原因（必填）"
      />
      <template #footer>
        <el-button @click="rejectDialogVisible = false">取消</el-button>
        <el-button type="danger" :loading="acting" @click="confirmReject">确认驳回</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  getCopyReview,
  getBudgetReview,
  getReviewConfig,
  getReviewStats,
  getReviewPending,
  getReviewProcessed,
  clearCopyReview,
  clearBudgetReview,
  approveAd,
  rejectAd,
} from '@/api'
import { useAppStore } from '@/stores/app'
import { formatTime, mediaUrl, PLATFORM_META, AD_TYPE_META, STAGE_META } from '@/utils/format'

const appStore = useAppStore()
const activeTab = ref('copy-review')

// 预算审查结论
const VERDICT_META = {
  normal: { label: '正常', type: 'success' },
  over: { label: '预算超支', type: 'danger' },
  low: { label: '预算过低', type: 'warning' },
}

// ---------- 各阶段数据 ----------
const reviewConfig = ref(null)
const stats = reactive({ copy_review: 0, budget_review: 0, pending: 0, approved: 0, delivered: 0, rejected: 0 })
const copyLoading = ref(false)
const budgetLoading = ref(false)
const pendingLoading = ref(false)
const processedLoading = ref(false)
const copyReview = reactive({ items: [], total: 0, hit_word_total: 0 })
const budgetReview = reactive({ items: [], total: 0, abnormal_count: 0, min_budget: 0, max_budget: 0 })
const pendingList = ref([])
const processedList = ref([])

const stageStep = computed(() => {
  if (stats.delivered) return 5
  if (stats.approved) return 4
  if (stats.pending) return 3
  if (stats.copy_review || stats.budget_review) return 2
  return 1
})

async function fetchReviewConfig() {
  try {
    reviewConfig.value = await getReviewConfig()
  } catch {
    reviewConfig.value = null
  }
}

async function fetchStats() {
  try {
    Object.assign(stats, await getReviewStats())
  } catch {
    /* 忽略 */
  }
}

async function fetchCopyReview() {
  copyLoading.value = true
  try {
    const res = await getCopyReview()
    Object.assign(copyReview, res)
    // 为每条待处置广告初始化可编辑草稿
    ;(res.items || []).forEach((row) => {
      if (!drafts[row.id]) drafts[row.id] = { title: row.title, content: row.content }
    })
  } catch {
    Object.assign(copyReview, { items: [], total: 0, hit_word_total: 0 })
  } finally {
    copyLoading.value = false
  }
}

async function fetchBudgetReview() {
  budgetLoading.value = true
  try {
    Object.assign(budgetReview, await getBudgetReview())
  } catch {
    Object.assign(budgetReview, { items: [], total: 0, abnormal_count: 0, min_budget: 0, max_budget: 0 })
  } finally {
    budgetLoading.value = false
  }
}

async function fetchPending() {
  pendingLoading.value = true
  try {
    pendingList.value = (await getReviewPending()).items || []
  } catch {
    pendingList.value = []
  } finally {
    pendingLoading.value = false
  }
}

async function fetchProcessed() {
  processedLoading.value = true
  try {
    processedList.value = (await getReviewProcessed()).items || []
  } catch {
    processedList.value = []
  } finally {
    processedLoading.value = false
  }
}

async function reloadAll() {
  await Promise.all([
    fetchStats(),
    fetchCopyReview(),
    fetchBudgetReview(),
    fetchPending(),
    fetchProcessed(),
    appStore.refreshPendingCount(),
  ])
}

function reload() {
  if (activeTab.value === 'copy-review') fetchCopyReview()
  else if (activeTab.value === 'budget-review') fetchBudgetReview()
  else if (activeTab.value === 'pending') fetchPending()
  else fetchProcessed()
}

// ---------- 违禁词修正 ----------
const drafts = reactive({})
const actingId = ref('')
const acting = ref(false)

function ensureDraft(row) {
  if (!drafts[row.id]) drafts[row.id] = { title: row.title, content: row.content }
  return drafts[row.id]
}

function resetDraft(row) {
  drafts[row.id].title = row.title
  drafts[row.id].content = row.content
}

function applySuggestion(row, violation) {
  const draft = ensureDraft(row)
  if (!violation.suggestion) {
    ElMessage.info('该风险词暂无系统建议，请手动修改标题或正文')
    return
  }
  let hit = false
  if ((draft.title || '').includes(violation.word)) {
    draft.title = draft.title.split(violation.word).join(violation.suggestion)
    hit = true
  }
  if ((draft.content || '').includes(violation.word)) {
    draft.content = draft.content.split(violation.word).join(violation.suggestion)
    hit = true
  }
  if (hit) ElMessage.success(`已将「${violation.word}」替换为「${violation.suggestion}」`)
  else ElMessage.info('当前标题与正文中已不存在该词')
}

function isResolved(row, word) {
  const draft = drafts[row.id]
  const text = `${draft?.title || ''}${draft?.content || ''}`
  return !draft || !text.includes(word)
}

function resolvedCount(row) {
  return (row.violations || []).filter((v) => isResolved(row, v.word)).length
}

async function handleClearCopy(row) {
  actingId.value = row.id
  try {
    const ad = await clearCopyReview(row.id, {
      title: ensureDraft(row).title,
      content: ensureDraft(row).content,
    })
    ElMessage.success(ad.stage === 'pending' ? '违禁词已放行，广告进入待审批' : '违禁词已放行，仍有预算问题待处置')
    delete drafts[row.id]
    await reloadAll()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '放行失败，请稍后重试')
  } finally {
    actingId.value = ''
  }
}

// ---------- 预算调整 ----------
const budgetDialogVisible = ref(false)
const budgetTarget = ref(null)
const budgetInput = ref(0)

function openBudgetDialog(row) {
  budgetTarget.value = row
  budgetInput.value = Number(row.budget || 0) || budgetReview.min_budget
  budgetDialogVisible.value = true
}

async function confirmClearBudget() {
  const amount = Number(budgetInput.value)
  if (!amount || amount <= 0) {
    ElMessage.warning('请填写有效的投放预算')
    return
  }
  acting.value = true
  try {
    const ad = await clearBudgetReview(budgetTarget.value.id, { budget: amount })
    ElMessage.success(ad.stage === 'pending' ? '预算已放行，广告进入待审批' : '预算已放行，仍有违禁词待处置')
    budgetDialogVisible.value = false
    await reloadAll()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '放行失败，请稍后重试')
  } finally {
    acting.value = false
  }
}

// ---------- 待审批操作 ----------
async function handleApprove(item) {
  try {
    await ElMessageBox.confirm('确认通过该广告？通过后将进入「广告投放」。', '审批确认', {
      type: 'warning', confirmButtonText: '确认通过', cancelButtonText: '再想想',
    })
  } catch {
    return
  }
  actingId.value = item.id
  try {
    await approveAd(item.id, {})
    ElMessage.success('审批通过，广告已进入投放候选')
    await reloadAll()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '审批失败')
  } finally {
    actingId.value = ''
  }
}

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
    await rejectAd(rejectTarget.value.id, { comment: rejectComment.value.trim() })
    ElMessage.success('已驳回')
    rejectDialogVisible.value = false
    await reloadAll()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '驳回失败')
  } finally {
    acting.value = false
  }
}

onMounted(async () => {
  await fetchReviewConfig()
  await reloadAll()
})
</script>

<style scoped>
.tab-badge {
  margin-left: 6px;
}

.review-config-hint {
  font-size: 12px;
  color: #6b7280;
  margin-left: auto;
}
.pipeline-steps {
  margin-bottom: 16px;
}
.review-block {
  min-height: 200px;
}
.review-summary {
  margin-bottom: 14px;
}
.review-summary-desc {
  margin-top: 6px;
  font-size: 12px;
  color: #606266;
  line-height: 1.6;
}
.review-card.is-abnormal {
  border-color: #f56c6c;
}
.review-copy-body {
  white-space: pre-wrap;
  line-height: 1.75;
  font-size: 13px;
  color: #303133;
  background: #f7f9fc;
  border-radius: 8px;
  padding: 12px 14px;
}
.approval-media {
  width: 180px;
  max-width: 100%;
  border-radius: 8px;
  border: 1px solid var(--card-border);
  background: #000;
}
.budget-table {
  margin-top: 4px;
}
.budget-hint {
  margin-left: 12px;
  font-size: 12px;
  color: #909399;
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
.mt12 {
  margin-top: 12px;
}
.copy-textarea :deep(textarea) {
  line-height: 1.7;
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

.threshold-hint {
  font-size: 12px;
  color: #909399;
  margin-left: 6px;
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