<template>
  <div class="gen-page">
    <!-- ===== 页头 ===== -->
    <div class="gen-hero">
      <span class="gen-hero-icon">
        <el-icon :size="24"><PictureFilled /></el-icon>
      </span>
      <div>
        <h1 class="gen-hero-title">图文广告生成</h1>
        <p class="gen-hero-desc">
          填写提示词即可生成图文海报；也可上传商品图片，基于「图片 + 提示词」生成更贴合商品的图文广告。
        </p>
      </div>
    </div>

    <el-row :gutter="18">
      <!-- ===== 参数配置 ===== -->
      <el-col :xs="24" :lg="10">
        <el-card class="gen-card" shadow="never">
          <template #header>参数配置</template>
          <el-form label-position="top">
            <el-form-item label="提示词（描述想要的图文广告）" required>
              <el-input
                v-model="form.prompt"
                type="textarea"
                :rows="4"
                maxlength="300"
                show-word-limit
                placeholder="例如：为 316 不锈钢保温杯做一张小红书种草图，突出 24 小时保温、轻便，清新暖色调"
              />
            </el-form-item>

            <el-form-item label="商品图片（选填）">
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
                {{ fileList.length ? '将按「图片 + 提示词」生成' : '不上传则仅根据提示词生成' }}
              </p>
            </el-form-item>

            <el-button
              type="primary"
              size="large"
              :icon="MagicStick"
              :loading="loading"
              class="gen-submit"
              @click="handleGenerate"
            >
              {{ loading ? '生成中…' : '一键生成图文' }}
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
              <el-button v-if="imageUrl" size="small" :icon="Download" @click="downloadImage">
                下载图片
              </el-button>
            </div>
          </template>

          <el-empty
            v-if="!imageUrl && !loading"
            class="gen-result-empty"
            description="填写提示词后点击「一键生成图文」"
          />

          <div v-if="loading" class="gen-progress">
            <div class="gen-progress-label">
              <span>{{ stageText }}</span>
              <span>{{ Math.round(progress) }}%</span>
            </div>
            <el-progress :percentage="progress" :show-text="false" :stroke-width="8" />
            <p class="gen-tip" style="margin-top: 10px">
              正在生成配图文案并渲染宣传图（图文 Agent）…
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

          <div v-if="imageUrl" class="gen-result">
            <el-image
              class="gen-result-img"
              :src="mediaUrl(imageUrl)"
              :preview-src-list="[mediaUrl(imageUrl)]"
              fit="contain"
              hide-on-click-modal
            />
            <div v-if="inputImageUrl" class="gen-input-preview">
              <span class="gen-input-label">输入商品图片</span>
              <el-image
                style="width: 96px; height: 96px; border-radius: 8px"
                :src="mediaUrl(inputImageUrl)"
                fit="cover"
                :preview-src-list="[mediaUrl(inputImageUrl)]"
                hide-on-click-modal
              />
            </div>
            <div v-if="copyTitle || copyContent" style="margin-top: 16px">
              <h3 class="gen-result-title">{{ copyTitle }}</h3>
              <div class="gen-result-body">{{ copyContent }}</div>
            </div>
            <div class="gen-result-meta">
              <el-tag size="small" type="info" effect="plain">任务 {{ taskId }}</el-tag>
              <el-tag size="small" :type="inputImageUrl ? 'success' : 'warning'" effect="plain">
                {{ inputImageUrl ? '图片 + 提示词' : '仅提示词' }}
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
import { Download, MagicStick, PictureFilled, Plus } from '@element-plus/icons-vue'
import { generateImage, getImageStatus } from '@/api'
import { mediaUrl } from '@/utils/format'

const POLL_INTERVAL = 1500
const POLL_MAX = 60
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const form = reactive({
  prompt: '',
})

const fileList = ref([])
const loading = ref(false)
const progress = ref(0)
const stageText = ref('正在提交任务…')
const error = ref('')
const imageUrl = ref('')
const inputImageUrl = ref('')
const copyTitle = ref('')
const copyContent = ref('')
const taskId = ref('')

function handleExceed() {
  ElMessage.warning('只能上传 1 张商品图片，请先移除已有图片')
}

/** 轮询图文生成任务，直到成功/失败 */
async function pollStatus(id) {
  for (let i = 0; i < POLL_MAX; i += 1) {
    const status = await getImageStatus(id)
    if (status.status === 'succeeded' || status.status === 'failed') return status
    progress.value = Math.min(90, 12 + i * 4)
    stageText.value = i < 3 ? '正在生成配图文案…' : '正在渲染宣传图…'
    await sleep(POLL_INTERVAL)
  }
  throw new Error('生成超时，请稍后重试')
}

async function handleGenerate() {
  if (!form.prompt.trim()) {
    ElMessage.warning('请填写提示词')
    return
  }
  loading.value = true
  error.value = ''
  imageUrl.value = ''
  inputImageUrl.value = ''
  copyTitle.value = ''
  copyContent.value = ''
  taskId.value = ''
  progress.value = 8
  stageText.value = '正在提交任务…'

  const formData = new FormData()
  formData.append('prompt', form.prompt.trim())
  const file = fileList.value[0]?.raw
  if (file) formData.append('image', file)

  try {
    const { task_id } = await generateImage(formData)
    taskId.value = task_id
    progress.value = 20

    const status = await pollStatus(task_id)
    if (status.status !== 'succeeded' || !status.image_url) {
      error.value = status.error || '图文生成失败，请稍后重试'
      return
    }
    imageUrl.value = status.image_url
    inputImageUrl.value = status.input_image_url || ''
    copyTitle.value = status.copy_title || ''
    copyContent.value = status.copy_content || ''
    progress.value = 100
    ElMessage.success('图文生成完成')
  } catch (e) {
    error.value =
      e?.response?.data?.msg || e?.message || '生成失败，请检查后端服务是否可用'
  } finally {
    loading.value = false
  }
}

function downloadImage() {
  const url = mediaUrl(imageUrl.value)
  if (!url) return
  const a = document.createElement('a')
  a.href = url
  a.download = `image-ad-${taskId.value || Date.now()}.png`
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
.gen-input-preview {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
}
.gen-input-label {
  font-size: 12px;
  color: #909399;
}
</style>