<template>
  <div class="gen-page">
    <!-- ===== 页头 ===== -->
    <div class="gen-hero">
      <span class="gen-hero-icon">
        <el-icon :size="24"><Promotion /></el-icon>
      </span>
      <div>
        <h1 class="gen-hero-title">广告投放</h1>
        <p class="gen-hero-desc">
          选择目标平台（巨量千川 / 小红书等），勾选已审批的广告物料，一键批量投放。投放为 Mock 模拟，不产生真实扣费。
        </p>
      </div>
    </div>

    <el-row :gutter="18">
      <!-- ===== 投递配置 ===== -->
      <el-col :xs="24" :lg="14">
        <el-card class="gen-card" shadow="never">
          <template #header>投递配置</template>

          <el-form label-position="top">
            <el-form-item label="目标平台" required>
              <el-radio-group v-model="platform">
                <el-radio-button v-for="p in platforms" :key="p.value" :value="p.value">
                  {{ p.label }}
                </el-radio-button>
              </el-radio-group>
            </el-form-item>

            <el-form-item label="投放预算（元）" required>
              <el-input-number
                v-model="budget"
                :min="0"
                :step="1000"
                :controls="false"
                style="width: 200px"
              />
              <span v-if="budgetConfig" class="budget-hint">
                审查区间 ¥{{ budgetConfig.min_budget.toLocaleString() }} ~
                ¥{{ budgetConfig.max_budget.toLocaleString() }}，超出将被拦截
              </span>
            </el-form-item>
          </el-form>

          <div class="ad-picker-head">
            <span class="ad-picker-title">已审批广告物料（{{ availableAds.length }}）</span>
            <div class="ad-picker-actions">
              <el-button size="small" text @click="selectAll">全选</el-button>
              <el-button size="small" text @click="clearAll">清空</el-button>
            </div>
          </div>

          <el-empty
            v-if="availableAds.length === 0"
            description="暂无待投放的广告物料，请先在文案 / 图文 / 视频生成页创作"
            :image-size="80"
          />

          <el-checkbox-group v-else v-model="selectedIds" class="ad-picker-list">
            <label
              v-for="ad in availableAds"
              :key="ad.id"
              class="ad-picker-item"
              :class="{ 'is-checked': selectedIds.includes(ad.id) }"
            >
              <el-checkbox :value="ad.id" class="ad-picker-check">
                <div class="ad-picker-body">
                  <div class="ad-picker-row">
                    <el-tag size="small" :type="typeMeta(ad.type).tagType" effect="plain">
                      {{ typeMeta(ad.type).label }}
                    </el-tag>
                    <span class="ad-picker-id">{{ ad.id }}</span>
                    <span class="ad-picker-time">{{ formatTime(ad.created_at) }}</span>
                  </div>
                  <div class="ad-picker-text">{{ ad.title || ad.prompt || '（无标题）' }}</div>
                </div>
              </el-checkbox>
              <el-image
                v-if="ad.type === 'image' && ad.result_url"
                class="ad-picker-thumb"
                :src="mediaUrl(ad.result_url)"
                fit="cover"
                :preview-src-list="[mediaUrl(ad.result_url)]"
                hide-on-click-modal
              />
              <video
                v-else-if="ad.type === 'video' && ad.result_url"
                class="ad-picker-thumb"
                :src="mediaUrl(ad.result_url)"
                preload="metadata"
                muted
              ></video>
            </label>
          </el-checkbox-group>

          <el-button
            type="primary"
            size="large"
            :icon="Promotion"
            :loading="submitting"
            :disabled="selectedIds.length === 0"
            class="gen-submit"
            @click="handleDeliver"
          >
            {{ submitting ? '投放中…' : `一键投放（已选 ${selectedIds.length}）` }}
          </el-button>
        </el-card>
      </el-col>

      <!-- ===== 投放结果 ===== -->
      <el-col :xs="24" :lg="10">
        <el-card class="gen-card" shadow="never">
          <template #header>
            <div class="gen-card-head">
              <span>投放结果</span>
              <el-button size="small" :icon="Refresh" @click="fetchDeliveries">刷新</el-button>
            </div>
          </template>

          <div v-if="latest" class="deliver-result">
            <div class="deliver-result-head">
              <el-tag size="small" type="success" effect="dark">批次 {{ latest.batch_id }}</el-tag>
              <span class="deliver-platform">{{ latest.platform_label }}</span>
            </div>
            <div class="deliver-summary">
              共 {{ latest.items.length }} 条 · 成功
              <b class="ok">{{ latest.success_count }}</b> · 失败
              <b class="fail">{{ latest.failed_count }}</b>
              <span v-if="latest.budget" class="deliver-budget">
                预算 ¥{{ Number(latest.budget).toLocaleString() }}
              </span>
              <span class="deliver-time">{{ formatTime(latest.submitted_at) }}</span>
            </div>
            <div class="deliver-items">
              <div v-for="it in latest.items" :key="it.ad_id" class="deliver-item">
                <el-tag size="small" :type="it.status === 'success' ? 'success' : 'danger'" effect="plain">
                  {{ it.status === 'success' ? '投放成功' : '投放失败' }}
                </el-tag>
                <span class="deliver-item-id">{{ it.ad_id }}</span>
                <span class="deliver-item-text">{{ it.title || '（无标题）' }}</span>
                <span v-if="it.status === 'success'" class="deliver-item-reach">
                  预估曝光 {{ it.reach.toLocaleString() }}
                </span>
                <span v-else class="deliver-item-err">{{ it.error }}</span>
              </div>
            </div>
          </div>
          <el-empty v-else description="尚未发起投放" :image-size="80" />

          <el-divider>历史投放批次</el-divider>
          <div v-if="deliveries.length === 0" class="deliver-empty">暂无历史批次</div>
          <el-timeline v-else>
            <el-timeline-item
              v-for="d in deliveries"
              :key="d.batch_id"
              :timestamp="formatTime(d.submitted_at)"
              type="success"
              placement="top"
            >
              <div class="history-row">
                <el-tag size="small" effect="plain">{{ d.platform_label }}</el-tag>
                <span class="mono">{{ d.batch_id }}</span>
                <span v-if="d.budget" class="history-budget">预算 ¥{{ Number(d.budget).toLocaleString() }}</span>
                <span class="history-count">成功 {{ d.success_count }} / 失败 {{ d.failed_count }}</span>
              </div>
            </el-timeline-item>
          </el-timeline>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Promotion, Refresh } from '@element-plus/icons-vue'
import { listAds, listDeliveryPlatforms, listDeliveries, submitDelivery, getDeliveryConfig } from '@/api'
import { formatTime, mediaUrl } from '@/utils/format'

const TYPE_META = {
  copy: { label: '文案广告', tagType: 'info' },
  image: { label: '图文广告', tagType: 'success' },
  video: { label: '视频广告', tagType: 'danger' },
}
const typeMeta = (type) => TYPE_META[type] || { label: type, tagType: 'info' }

const platforms = ref([])
const platform = ref('')
const budget = ref(10000)
const budgetConfig = ref(null)
const ads = ref([])
const selectedIds = ref([])
const submitting = ref(false)
const latest = ref(null)
const deliveries = ref([])

// 仅展示「已审批」且尚未投放的广告物料
const availableAds = computed(() => ads.value.filter((ad) => ad.status === 'published'))

function selectAll() {
  selectedIds.value = availableAds.value.map((ad) => ad.id)
}
function clearAll() {
  selectedIds.value = []
}

async function fetchPlatforms() {
  try {
    platforms.value = await listDeliveryPlatforms()
    if (!platform.value && platforms.value.length) platform.value = platforms.value[0].value
  } catch {
    platforms.value = []
  }
}

async function fetchAds() {
  try {
    ads.value = await listAds()
  } catch {
    ads.value = []
  }
}

async function fetchDeliveries() {
  try {
    deliveries.value = await listDeliveries()
  } catch {
    deliveries.value = []
  }
}

async function fetchBudgetConfig() {
  try {
    budgetConfig.value = await getDeliveryConfig()
    if (budgetConfig.value) {
      budget.value = Math.min(
        Math.max(budget.value, budgetConfig.value.min_budget),
        budgetConfig.value.max_budget,
      )
    }
  } catch {
    budgetConfig.value = null
  }
}

async function handleDeliver() {
  if (!platform.value) {
    ElMessage.warning('请选择目标平台')
    return
  }
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请至少勾选一条广告物料')
    return
  }
  const amount = Number(budget.value)
  if (!amount || amount <= 0) {
    ElMessage.warning('请填写投放预算')
    return
  }
  // 前端预审：预算需落在审查区间内
  if (budgetConfig.value) {
    if (amount < budgetConfig.value.min_budget) {
      ElMessage.warning(`投放预算过低（最低 ¥${budgetConfig.value.min_budget.toLocaleString()}）`)
      return
    }
    if (amount > budgetConfig.value.max_budget) {
      ElMessage.warning(`投放预算超支（上限 ¥${budgetConfig.value.max_budget.toLocaleString()}）`)
      return
    }
  }
  submitting.value = true
  try {
    const res = await submitDelivery({
      platform: platform.value,
      ad_ids: selectedIds.value,
      budget: amount,
    })
    latest.value = res
    ElMessage.success(`已投放到「${res.platform_label}」，成功 ${res.success_count} 条`)
    selectedIds.value = []
    await Promise.all([fetchAds(), fetchDeliveries()])
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '投放失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await Promise.all([fetchPlatforms(), fetchAds(), fetchDeliveries(), fetchBudgetConfig()])
})
</script>

<style scoped>
.gen-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.gen-submit {
  width: 100%;
  margin-top: 14px;
}

.budget-hint {
  margin-left: 12px;
  font-size: 12px;
  color: #909399;
}

.ad-picker-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.ad-picker-title {
  font-weight: 600;
  color: #1d2b4f;
}
.ad-picker-actions {
  display: flex;
  gap: 4px;
}

.ad-picker-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 42vh;
  overflow-y: auto;
  padding-right: 4px;
  width: 100%;
}
.ad-picker-item {
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--card-border);
  border-radius: 10px;
  padding: 10px 12px;
  cursor: pointer;
  transition: border-color 0.2s ease, background 0.2s ease;
  margin: 0;
}
.ad-picker-item.is-checked {
  border-color: #409eff;
  background: #f2f8ff;
}
.ad-picker-check {
  flex: 1;
  height: auto;
  margin-right: 0;
}
.ad-picker-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ad-picker-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ad-picker-id {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  color: #909399;
}
.ad-picker-time {
  margin-left: auto;
  font-size: 12px;
  color: #909399;
}
.ad-picker-text {
  font-size: 13px;
  color: #4b5563;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.ad-picker-thumb {
  width: 64px;
  height: 64px;
  border-radius: 8px;
  border: 1px solid var(--card-border);
  flex-shrink: 0;
  background: #000;
}

.deliver-result {
  border: 1px solid var(--card-border);
  border-radius: 10px;
  padding: 12px 14px;
  background: #fafbfd;
}
.deliver-result-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.deliver-platform {
  font-weight: 600;
  color: #1d2b4f;
}
.deliver-summary {
  font-size: 13px;
  color: #606266;
  margin-bottom: 10px;
}
.deliver-summary .ok {
  color: #67c23a;
}
.deliver-summary .fail {
  color: #f56c6c;
}
.deliver-time {
  margin-left: 10px;
  font-size: 12px;
  color: #909399;
}
.deliver-budget {
  margin-left: 12px;
  color: #2e4c86;
  font-weight: 600;
}
.deliver-items {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 24vh;
  overflow-y: auto;
}
.deliver-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  flex-wrap: wrap;
}
.deliver-item-id {
  font-family: Consolas, Monaco, monospace;
  color: #909399;
}
.deliver-item-text {
  color: #4b5563;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}
.deliver-item-reach {
  color: #409eff;
}
.deliver-item-err {
  color: #f56c6c;
}
.deliver-empty {
  font-size: 13px;
  color: #909399;
}
.history-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  flex-wrap: wrap;
}
.history-count {
  margin-left: auto;
  color: #909399;
  font-size: 12px;
}
.history-budget {
  font-size: 12px;
  color: #2e4c86;
}
.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  color: #606266;
}
</style>