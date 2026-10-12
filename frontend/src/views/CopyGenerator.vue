<template>
  <div class="gen-page">
    <!-- ===== 页头 ===== -->
    <div class="gen-hero">
      <span class="gen-hero-icon">
        <el-icon :size="24"><EditPen /></el-icon>
      </span>
      <div>
        <h1 class="gen-hero-title">爆款文案生成</h1>
        <p class="gen-hero-desc">
          输入商品信息与推广诉求，一键生成高转化的广告文案；生成后可在「广告投放」页选择目标平台进行投放。
        </p>
      </div>
    </div>

    <el-row :gutter="18">
      <!-- ===== 参数配置 ===== -->
      <el-col :xs="24" :lg="10">
        <el-card class="gen-card" shadow="never">
          <template #header>参数配置</template>
          <el-form label-position="top">
            <el-form-item label="商品名称" required>
              <el-input
                v-model="form.productName"
                placeholder="例如：316 不锈钢保温杯"
                maxlength="40"
                show-word-limit
              />
            </el-form-item>
            <el-form-item label="核心卖点">
              <el-input
                v-model="form.sellPoints"
                type="textarea"
                :rows="3"
                placeholder="多个卖点可用逗号分隔，例如：24 小时保温、轻便、密封防漏"
              />
            </el-form-item>
            <el-form-item label="推广诉求（选填）">
              <el-input
                v-model="form.extra"
                type="textarea"
                :rows="2"
                placeholder="例如：突出场景化种草，语气活泼，带 3 个话题标签"
              />
            </el-form-item>
            <el-button
              type="primary"
              size="large"
              :icon="MagicStick"
              :loading="loading"
              class="gen-submit"
              @click="handleGenerate"
            >
              {{ loading ? '生成中…' : '一键生成文案' }}
            </el-button>
          </el-form>
        </el-card>
      </el-col>

      <!-- ===== 生成结果 ===== -->
      <el-col :xs="24" :lg="14">
        <el-card class="gen-card" shadow="never">
          <template #header>
            <div class="gen-card-head">
              <span>生成结果</span>
              <el-button v-if="result" size="small" :icon="CopyDocument" @click="copyResult">
                复制文案
              </el-button>
            </div>
          </template>

          <el-empty
            v-if="!result && !loading"
            class="gen-result-empty"
            description="填写左侧参数后点击「一键生成文案」"
          />

          <div v-if="loading" class="gen-progress">
            <div class="gen-progress-label">
              <span>{{ stageText }}</span>
              <span>{{ Math.round(progress) }}%</span>
            </div>
            <el-progress :percentage="progress" :show-text="false" :stroke-width="8" />
            <p class="gen-tip" style="margin-top: 10px">
              正在调度「爆款文案 Agent」检索高转化范例并生成草稿…
            </p>
          </div>

          <el-alert
            v-if="error"
            type="error"
            :closable="false"
            show-icon
            :title="error"
            style="margin-bottom: 12px"
          />

          <div v-if="result" class="gen-result">
            <h3 class="gen-result-title">{{ result.title || '（未返回标题）' }}</h3>
            <div class="gen-result-body">{{ result.content || '（未返回正文）' }}</div>
            <div class="gen-result-meta">
              <el-tag size="small" type="info" effect="plain">任务 {{ taskId }}</el-tag>
              <el-tag
                v-for="t in result.tags || []"
                :key="t"
                size="small"
                type="success"
                effect="plain"
              >
                {{ t }}
              </el-tag>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument, EditPen, MagicStick } from '@element-plus/icons-vue'
import { generateCopy, getCopyStatus } from '@/api'

const POLL_INTERVAL = 1200
const POLL_MAX = 60
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const form = reactive({
  productName: '',
  sellPoints: '',
  extra: '',
})

const loading = ref(false)
const progress = ref(0)
const stageText = ref('正在提交任务…')
const error = ref('')
const result = ref(null)
const taskId = ref('')

/** 轮询文案生成任务，直到成功/失败 */
async function pollStatus(id) {
  for (let i = 0; i < POLL_MAX; i += 1) {
    const status = await getCopyStatus(id)
    if (status.status === 'succeeded' || status.status === 'failed') return status
    progress.value = Math.min(90, 12 + i * 4)
    stageText.value = i < 3 ? '正在检索高转化参考文案…' : '正在生成文案草稿…'
    await sleep(POLL_INTERVAL)
  }
  throw new Error('生成超时，请稍后重试')
}

async function handleGenerate() {
  if (!form.productName.trim()) {
    ElMessage.warning('请填写商品名称')
    return
  }
  loading.value = true
  error.value = ''
  result.value = null
  taskId.value = ''
  progress.value = 8
  stageText.value = '正在提交任务…'

  try {
    const { task_id } = await generateCopy({
      product_name: form.productName.trim(),
      product_sell_points: form.sellPoints.trim(),
      extra: form.extra.trim(),
    })
    taskId.value = task_id
    progress.value = 20

    const status = await pollStatus(task_id)
    if (status.status !== 'succeeded') {
      error.value = status.error || '文案生成失败，请稍后重试'
      return
    }
    result.value = {
      title: status.title || '',
      content: status.content || '',
      tags: status.tags || [],
    }
    progress.value = 100
    ElMessage.success('文案生成完成')
  } catch (e) {
    error.value =
      e?.response?.data?.msg || e?.message || '生成失败，请检查后端服务是否可用'
  } finally {
    loading.value = false
  }
}

async function copyResult() {
  if (!result.value) return
  const text = `${result.value.title}\n\n${result.value.content}`
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('当前环境不支持自动复制，请手动选择文本')
  }
}
</script>

<style scoped>
.gen-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.gen-submit {
  width: 100%;
}
</style>