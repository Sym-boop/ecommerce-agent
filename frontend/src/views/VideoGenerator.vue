<template>
  <div class="gen-page">
    <!-- ===== 页头 ===== -->
    <div class="gen-hero">
      <span class="gen-hero-icon">
        <el-icon :size="24"><VideoCameraFilled /></el-icon>
      </span>
      <div>
        <h1 class="gen-hero-title">动态短视频生成</h1>
        <p class="gen-hero-desc">
          上传商品图片并填写提示词，一键生成约 {{ form.duration }} 秒的动态短视频，适合信息流投放与商品种草。
        </p>
      </div>
    </div>

    <el-row :gutter="18">
      <!-- ===== 参数配置 ===== -->
      <el-col :xs="24" :lg="10">
        <el-card class="gen-card" shadow="never">
          <template #header>参数配置</template>
          <el-form label-position="top">
            <el-form-item label="商品图片" required>
              <el-upload
                class="gen-uploader"
                list-type="picture-card"
                :auto-upload="false"
                :limit="1"
                accept="image/png,image/jpeg,image/webp,image/bmp"
                v-model:file-list="fileList"
                :on-exceed="handleExceed"
              >
                <el-icon><Plus /></el-icon>
              </el-upload>
              <p class="gen-upload-hint">
                支持 png / jpg / jpeg / webp / bmp，建议使用主体清晰的商品白底图或场景图。
              </p>
            </el-form-item>

            <el-form-item label="提示词（描述期望的动态效果）" required>
              <el-input
                v-model="form.prompt"
                type="textarea"
                :rows="4"
                maxlength="300"
                show-word-limit
                placeholder="例如：镜头缓慢环绕商品，背景光影流动，突出质感，营造高级商业广告氛围"
              />
            </el-form-item>

            <el-form-item label="视频时长">
              <el-slider v-model="form.duration" :min="5" :max="15" :step="5" show-stops />
              <span class="gen-tip">当前时长：{{ form.duration }} 秒</span>
            </el-form-item>

            <el-form-item label="投放预算（元，选填）">
              <el-input-number v-model="form.budget" :min="0" :step="1000" :controls="false" style="width: 200px" />
              <span class="gen-budget-hint">填写后广告将自动送「预算审查」，异常需在审查中心处置</span>
            </el-form-item>

            <el-button
              type="primary"
              size="large"
              :icon="MagicStick"
              :loading="loading"
              class="gen-submit"
              @click="handleGenerate"
            >
              {{ loading ? '生成中…' : '一键生成短视频' }}
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
              <el-button v-if="videoUrl" size="small" :icon="Download" @click="downloadVideo">
                下载视频
              </el-button>
            </div>
          </template>

          <el-empty
            v-if="!videoUrl && !loading"
            class="gen-result-empty"
            description="上传商品图片并填写提示词后点击「一键生成短视频」"
          />

          <div v-if="loading" class="gen-progress">
            <div class="gen-progress-label">
              <span>{{ stageText }}</span>
              <span>{{ Math.round(progress) }}%</span>
            </div>
            <el-progress
              :percentage="progress"
              :show-text="false"
              :stroke-width="8"
              :striped="true"
              :striped-flow="true"
            />
            <p class="gen-tip" style="margin-top: 10px">
              正在调用独立的图生视频 API 生成动态短视频，通常需要 1~3 分钟，请耐心等待…
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

          <div v-if="videoUrl" class="gen-result">
            <video
              class="gen-result-video"
              :src="mediaUrl(videoUrl)"
              controls
              autoplay
              loop
              playsinline
            ></video>
            <div class="gen-result-meta">
              <el-tag size="small" effect="plain">约 {{ form.duration }} 秒</el-tag>
              <el-tag size="small" type="success" effect="plain">任务 {{ taskId }}</el-tag>
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
import { Download, MagicStick, Plus, VideoCameraFilled } from '@element-plus/icons-vue'
import { generateVideo, getVideoStatus } from '@/api'
import { mediaUrl } from '@/utils/format'

const POLL_INTERVAL = 3000
const POLL_MAX = 120
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const form = reactive({
  prompt: '',
  duration: 10,
  budget: null,
})

const fileList = ref([])
const loading = ref(false)
const progress = ref(0)
const stageText = ref('正在上传商品图片…')
const error = ref('')
const videoUrl = ref('')
const taskId = ref('')

const STATUS_TEXT = {
  pending: '任务排队中…',
  processing: '正在生成视频…',
}

function handleExceed() {
  ElMessage.warning('只能上传 1 张商品图片，请先移除已有图片')
}

async function handleGenerate() {
  const file = fileList.value[0]?.raw
  if (!file) {
    ElMessage.warning('请先上传商品图片')
    return
  }
  if (!form.prompt.trim()) {
    ElMessage.warning('请填写视频生成提示词')
    return
  }

  loading.value = true
  error.value = ''
  videoUrl.value = ''
  taskId.value = ''
  progress.value = 5
  stageText.value = '正在上传商品图片…'

  const formData = new FormData()
  formData.append('image', file)
  formData.append('prompt', form.prompt.trim())
  formData.append('duration', String(form.duration))
  formData.append('budget', form.budget == null ? '' : String(form.budget))

  try {
    const { task_id } = await generateVideo(formData)
    taskId.value = task_id
    progress.value = 15

    for (let i = 0; i < POLL_MAX; i += 1) {
      const status = await getVideoStatus(task_id)
      if (status.status === 'succeeded') {
        videoUrl.value = status.video_url || ''
        progress.value = 100
        ElMessage.success('短视频生成完成')
        return
      }
      if (status.status === 'failed') {
        error.value = status.error || '视频生成失败，请稍后重试'
        return
      }
      stageText.value = STATUS_TEXT[status.status] || '正在生成视频…'
      progress.value = Math.min(92, 15 + (i + 1) * 3)
      await sleep(POLL_INTERVAL)
    }
    error.value = '生成超时，请稍后在「任务拓扑看板」查看或重试'
  } catch (e) {
    error.value =
      e?.response?.data?.msg || e?.message || '生成失败，请检查后端服务与视频 API 配置'
  } finally {
    loading.value = false
  }
}

function downloadVideo() {
  const url = mediaUrl(videoUrl.value)
  if (!url) return
  const a = document.createElement('a')
  a.href = url
  a.download = `video-${taskId.value || Date.now()}.mp4`
  a.target = '_blank'
  a.click()
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
.gen-budget-hint {
  margin-left: 12px;
  font-size: 12px;
  color: #909399;
}
</style>