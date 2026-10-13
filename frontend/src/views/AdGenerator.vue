<template>
  <div class="gen-page adgen">
    <!-- ===== 页头 ===== -->
    <div class="gen-hero">
      <span class="gen-hero-icon">
        <el-icon :size="24"><MagicStick /></el-icon>
      </span>
      <div>
        <h1 class="gen-hero-title">广告生成</h1>
        <p class="gen-hero-desc">
          选商品 → 一键生成文案（自动审查）→ 按需继续生成图文 / 视频。不满意可随时单独重新生成，商品与关键词保持不变。
        </p>
      </div>
      <el-button
        v-if="mode === 'workspace'"
        class="hero-reset"
        text
        :icon="RefreshLeft"
        @click="backToStart"
      >
        重新开始
      </el-button>
    </div>

    <!-- ===================================================================
         开始面板：使用历史记录 / 开始生成
    ==================================================================== -->
    <div v-if="mode === 'start'" class="start-panel">
      <div class="start-cards">
        <div class="start-card" :class="{ 'is-active': startTab === 'history' }" @click="openHistory">
          <span class="start-icon" style="--c: #e6a23c"><el-icon :size="24"><Clock /></el-icon></span>
          <div class="start-body">
            <div class="start-title">使用历史记录</div>
            <div class="start-desc">调用过往的模板记录（半成品 / 正常）作为起点继续生成</div>
          </div>
          <el-icon class="start-arrow"><ArrowRight /></el-icon>
        </div>
        <div class="start-card" :class="{ 'is-active': startTab === 'new' }" @click="startTab = 'new'">
          <span class="start-icon" style="--c: #409eff"><el-icon :size="24"><MagicStick /></el-icon></span>
          <div class="start-body">
            <div class="start-title">开始生成</div>
            <div class="start-desc">选择或自定义商品，一键生成该商品的爆款文案</div>
          </div>
          <el-icon class="start-arrow"><ArrowRight /></el-icon>
        </div>
      </div>

      <!-- 开始生成：商品选择 -->
      <el-card v-if="startTab === 'new'" class="gen-card start-form" shadow="never">
        <template #header>选择商品</template>

        <el-form label-position="top">
          <el-form-item label="商品（内置清单，可自定义）" required>
            <div class="product-row">
              <el-select
                v-model="selectedProductId"
                placeholder="请选择商品"
                filterable
                style="flex: 1"
                @change="onProductChange"
              >
                <el-option
                  v-for="p in products"
                  :key="p.id"
                  :label="`${p.name}${p.category ? ' · ' + p.category : ''}`"
                  :value="p.id"
                />
              </el-select>
              <el-button :icon="Plus" @click="customVisible = true">自定义商品</el-button>
            </div>
          </el-form-item>

          <div v-if="product" class="product-preview">
            <div class="product-preview-head">
              <span class="product-name">{{ product.name }}</span>
              <el-tag v-if="product.category" size="small" effect="plain">{{ product.category }}</el-tag>
              <el-tag v-if="product.price" size="small" type="danger" effect="plain">¥{{ product.price }}</el-tag>
            </div>
            <div v-if="product.sell_points?.length" class="product-points">
              卖点：{{ product.sell_points.join(' / ') }}
            </div>
          </div>

          <el-form-item label="关键词（可选，用于引导文案方向）">
            <el-input
              v-model="keyword"
              placeholder="如：通勤 / 降噪 / 送礼 / 学生党"
              maxlength="40"
              show-word-limit
            />
          </el-form-item>
        </el-form>

        <el-button
          type="primary"
          size="large"
          :icon="MagicStick"
          :loading="loadingCopy"
          :disabled="!product"
          class="gen-submit"
          @click="generateCopy(false)"
        >
          {{ loadingCopy ? '文案生成中…' : '一键生成文案' }}
        </el-button>
      </el-card>

      <!-- 使用历史记录：历史列表 -->
      <el-card v-if="startTab === 'history'" class="gen-card start-form" shadow="never">
        <template #header>
          <div class="gen-card-head">
            <span>历史记录（{{ historyRecords.length }}）</span>
            <el-button size="small" :icon="Refresh" @click="fetchHistory">刷新</el-button>
          </div>
        </template>

        <el-empty v-if="historyRecords.length === 0" description="暂无历史记录" :image-size="80" />

        <div v-else class="history-list">
          <div
            v-for="r in historyRecords"
            :key="r.id"
            class="history-item"
            @click="loadHistory(r)"
          >
            <div class="history-item-main">
              <div class="history-item-head">
                <el-tag v-if="r.stage === 'wip'" size="small" type="danger" effect="dark">半成品</el-tag>
                <span class="history-product">{{ r.product?.name || '未命名商品' }}</span>
                <span v-if="r.keyword" class="history-keyword">关键词：{{ r.keyword }}</span>
                <span class="history-time">{{ formatTime(r.created_at) }}</span>
              </div>
              <div class="history-copy">{{ r.copy?.title || '（暂无文案）' }}</div>
              <div class="history-flags">
                <el-tag v-if="r.image_url" size="small" type="success" effect="plain">含图文</el-tag>
                <el-tag v-if="r.video_url" size="small" type="danger" effect="plain">含视频</el-tag>
              </div>
            </div>
            <el-icon class="start-arrow"><ArrowRight /></el-icon>
          </div>
        </div>
      </el-card>
    </div>

    <!-- ===================================================================
         生成工作台：横向步骤卡
    ==================================================================== -->
    <div v-else class="workspace">
      <div class="step-nav">
        <el-button :icon="ArrowLeft" circle :disabled="scrollAtStart" @click="scrollSteps(-1)" />
        <span class="step-nav-hint">横向滚动查看各步骤</span>
        <el-button :icon="ArrowRight" circle :disabled="scrollAtEnd" @click="scrollSteps(1)" />
      </div>

      <div ref="stepsEl" class="steps" @scroll="onStepsScroll">
        <!-- 步骤 1：商品 -->
        <div class="step-card">
          <div class="step-head">
            <span class="step-no">1</span>
            <span class="step-title">商品</span>
          </div>
          <div class="step-body">
            <div class="kv-name">{{ product?.name || '未选择商品' }}</div>
            <div class="kv-tags">
              <el-tag v-if="product?.category" size="small" effect="plain">{{ product.category }}</el-tag>
              <el-tag v-if="product?.price" size="small" type="danger" effect="plain">¥{{ product.price }}</el-tag>
            </div>
            <div v-if="product?.sell_points?.length" class="kv-points">
              {{ product.sell_points.join(' / ') }}
            </div>
            <div v-if="keyword" class="kv-keyword">关键词：{{ keyword }}</div>
            <el-button size="small" text type="primary" :icon="RefreshLeft" @click="backToStart">
              更换商品
            </el-button>
          </div>
        </div>

        <!-- 步骤 2：文案 -->
        <div class="step-card" :class="{ 'is-focus': sessionStatus === 'review_required' }">
          <div class="step-head">
            <span class="step-no">2</span>
            <span class="step-title">爆款文案</span>
            <span class="step-review" :class="reviewClass">
              <el-icon><component :is="reviewIcon" /></el-icon>
              {{ reviewText }}
            </span>
          </div>
          <div class="step-body">
            <div v-if="!copy.content" class="step-empty">尚未生成文案</div>
            <template v-else>
              <h3 class="copy-title">{{ copy.title }}</h3>
              <div class="copy-body">{{ copy.content }}</div>

              <el-alert
                v-if="sessionStatus === 'review_required'"
                type="warning"
                :closable="false"
                show-icon
                class="copy-alert"
              >
                <template #title>文案审查命中违禁词，需人工处置</template>
                <div class="violations">
                  <span v-for="(v, i) in review.violations || []" :key="i" class="violation-chip">
                    {{ v.word }}
                    <em v-if="v.suggestion">→ {{ v.suggestion }}</em>
                  </span>
                </div>
              </el-alert>
              <el-alert
                v-else-if="sessionStatus === 'review_rejected'"
                type="error"
                :closable="false"
                show-icon
                title="文案审查已被驳回，无法继续生成图文 / 视频"
                class="copy-alert"
              />

              <div class="step-actions">
                <el-button
                  size="small"
                  :icon="RefreshRight"
                  :loading="loadingCopy"
                  @click="generateCopy(true)"
                >
                  重新生成文案
                </el-button>
                <template v-if="sessionStatus === 'review_required'">
                  <el-button size="small" type="success" @click="handleReview('approve')">放行</el-button>
                  <el-button size="small" type="danger" @click="handleReview('reject')">驳回</el-button>
                </template>
              </div>
            </template>
          </div>
        </div>

        <!-- 步骤 3：图文 -->
        <div class="step-card">
          <div class="step-head">
            <span class="step-no">3</span>
            <span class="step-title">图文广告</span>
            <el-checkbox v-model="wantImage" class="step-want" :disabled="!canAsset">生成图文</el-checkbox>
          </div>
          <div class="step-body">
            <template v-if="wantImage">
              <el-input
                v-model="imageKeyword"
                size="small"
                placeholder="关键词（可选）"
                class="step-input"
                maxlength="40"
              />
              <el-upload
                class="step-uploader"
                list-type="picture-card"
                :auto-upload="false"
                :limit="1"
                accept="image/*"
                :on-change="onPickImage"
                :on-remove="clearImage"
              >
                <el-icon><Plus /></el-icon>
              </el-upload>
              <div class="gen-upload-hint">可选上传商品图片：上传则基于图片 + 文案生成，不上传仅基于文案生成</div>

              <el-button
                size="small"
                type="primary"
                :icon="PictureFilled"
                :loading="imageGenerating"
                :disabled="!canAsset"
                class="step-generate"
                @click="generateAsset('image')"
              >
                {{ image.url ? '重新生成图文' : '生成图文' }}
              </el-button>

              <div v-if="image.url" class="asset-preview">
                <el-image
                  class="asset-img"
                  :src="mediaUrl(image.url)"
                  fit="cover"
                  :preview-src-list="[mediaUrl(image.url)]"
                  hide-on-click-modal
                />
                <span class="step-review is-ok"><el-icon><CircleCheck /></el-icon> 文案已通过审查</span>
              </div>
              <el-alert
                v-else-if="image.error"
                type="error"
                :closable="false"
                show-icon
                :title="image.error"
              />
            </template>
            <div v-else class="step-empty">勾选「生成图文」后可基于文案生成</div>
          </div>
        </div>

        <!-- 步骤 4：视频 -->
        <div class="step-card">
          <div class="step-head">
            <span class="step-no">4</span>
            <span class="step-title">视频广告</span>
            <el-checkbox v-model="wantVideo" class="step-want" :disabled="!canAsset">生成视频</el-checkbox>
          </div>
          <div class="step-body">
            <template v-if="wantVideo">
              <el-input
                v-model="videoKeyword"
                size="small"
                placeholder="关键词（可选）"
                class="step-input"
                maxlength="40"
              />
              <el-upload
                class="step-uploader"
                list-type="picture-card"
                :auto-upload="false"
                :limit="1"
                accept="image/*"
                :on-change="onPickVideo"
                :on-remove="clearVideo"
              >
                <el-icon><Plus /></el-icon>
              </el-upload>
              <div class="gen-upload-hint">可选上传首帧图片：上传则基于图片生成，不上传则用文案海报作为首帧</div>

              <el-button
                size="small"
                type="primary"
                :icon="VideoCameraFilled"
                :loading="videoGenerating"
                :disabled="!canAsset"
                class="step-generate"
                @click="generateAsset('video')"
              >
                {{ video.url ? '重新生成视频' : '生成视频' }}
              </el-button>

              <div v-if="video.url" class="asset-preview">
                <video class="asset-video" :src="mediaUrl(video.url)" controls preload="metadata"></video>
                <span class="step-review is-ok"><el-icon><CircleCheck /></el-icon> 文案已通过审查</span>
              </div>
              <el-alert
                v-else-if="video.error"
                type="error"
                :closable="false"
                show-icon
                :title="video.error"
              />
            </template>
            <div v-else class="step-empty">勾选「生成视频」后可基于文案生成</div>
          </div>
        </div>

        <!-- 步骤 5：完成 -->
        <div class="step-card step-finish">
          <div class="step-head">
            <span class="step-no">5</span>
            <span class="step-title">完成</span>
          </div>
          <div class="step-body">
            <div class="finish-summary">
              <div class="finish-row"><span>文案</span><b>{{ copy.content ? '已生成' : '—' }}</b></div>
              <div class="finish-row"><span>图文</span><b>{{ image.url ? '已生成' : '未生成' }}</b></div>
              <div class="finish-row"><span>视频</span><b>{{ video.url ? '已生成' : '未生成' }}</b></div>
              <div class="finish-row"><span>文案审查</span><b>{{ reviewText }}</b></div>
            </div>
            <p class="finish-tip">
              满意后点击下方「存到投放界面」，广告将进入审查 → 审批 → 投放流水线，并写入历史记录。
            </p>
          </div>
        </div>
      </div>

      <!-- ===== 底部操作栏 ===== -->
      <div class="action-bar">
        <el-button
          type="primary"
          size="large"
          :icon="Promotion"
          :loading="saving"
          :disabled="!copy.content"
          @click="handleSave"
        >
          存到投放界面
        </el-button>
        <el-button
          type="warning"
          size="large"
          plain
          :icon="VideoPause"
          :loading="pausing"
          :disabled="!copy.content"
          @click="handlePause"
        >
          暂停生成并退出
        </el-button>
        <el-button
          type="danger"
          size="large"
          plain
          :icon="Delete"
          :loading="discarding"
          @click="handleDiscard"
        >
          删除此次生成
        </el-button>
        <span v-if="savedHint" class="action-hint">{{ savedHint }}</span>
      </div>
    </div>

    <!-- ===== 自定义商品弹窗 ===== -->
    <el-dialog v-model="customVisible" title="自定义商品" width="460px">
      <el-form label-position="top">
        <el-form-item label="商品名称" required>
          <el-input v-model="customForm.name" placeholder="如：便携折叠水杯" maxlength="30" />
        </el-form-item>
        <el-form-item label="类目">
          <el-input v-model="customForm.category" placeholder="如：家居 / 数码 / 服饰" maxlength="12" />
        </el-form-item>
        <el-form-item label="卖点（逗号分隔，可选）">
          <el-input v-model="customForm.sell_points" placeholder="如：轻便, 大容量, 不漏水" />
        </el-form-item>
        <el-form-item label="价格（元，可选）">
          <el-input-number v-model="customForm.price" :min="0" :controls="false" style="width: 160px" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="customVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingProduct" @click="handleCreateProduct">创建并选用</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  MagicStick, Clock, ArrowRight, ArrowLeft, Plus, Refresh, RefreshRight, RefreshLeft,
  PictureFilled, VideoCameraFilled, Promotion, Delete, VideoPause,
  CircleCheck, CircleClose, Warning,
} from '@element-plus/icons-vue'
import {
  listProducts, createProduct, listHistory,
  adgenCopy, adgenReview, adgenAsset, adgenAssetStatus, adgenImport,
  adgenSave, adgenPause, adgenDiscard,
} from '@/api'
import { formatTime, mediaUrl } from '@/utils/format'

defineOptions({ name: 'AdGenerator' })

/* ---------------- 视图状态 ---------------- */
const mode = ref('start')          // start | workspace
const startTab = ref('new')        // new | history

/* ---------------- 商品 ---------------- */
const products = ref([])
const selectedProductId = ref('')
const product = ref(null)
const keyword = ref('')
const customVisible = ref(false)
const creatingProduct = ref(false)
const customForm = ref({ name: '', category: '', sell_points: '', price: undefined })

/* ---------------- 生成会话 ---------------- */
const sessionId = ref('')
const copy = ref({})
const review = ref({})
const sessionStatus = ref('')
const image = ref({})
const video = ref({})

/* ---------------- 图文 / 视频 ---------------- */
const wantImage = ref(false)
const wantVideo = ref(false)
const imageKeyword = ref('')
const videoKeyword = ref('')
const imageFile = ref(null)
const videoFile = ref(null)

/* ---------------- 加载态 ---------------- */
const loadingCopy = ref(false)
const imageGenerating = ref(false)
const videoGenerating = ref(false)
const saving = ref(false)
const pausing = ref(false)
const discarding = ref(false)
const savedHint = ref('')

/* ---------------- 历史记录 ---------------- */
const historyRecords = ref([])

/* ---------------- 横向滚动 ---------------- */
const stepsEl = ref(null)
const scrollAtStart = ref(true)
const scrollAtEnd = ref(false)

const canAsset = computed(() => !!copy.value.content && sessionStatus.value === 'copy_ready')

const reviewText = computed(() => {
  if (!copy.value.content) return '待生成'
  if (sessionStatus.value === 'review_rejected') return '已驳回'
  if (sessionStatus.value === 'review_required') return `命中 ${review.value.violations?.length || 0} 处`
  return review.value.copy_ok === false ? '命中违禁词' : '审查通过'
})
const reviewClass = computed(() => ({
  'is-ok': reviewText.value === '审查通过',
  'is-warn': sessionStatus.value === 'review_required',
  'is-bad': sessionStatus.value === 'review_rejected',
  'is-idle': reviewText.value === '待生成',
}))
const reviewIcon = computed(() => {
  if (reviewText.value === '审查通过') return CircleCheck
  if (sessionStatus.value === 'review_rejected') return CircleClose
  if (sessionStatus.value === 'review_required') return Warning
  return Warning
})

/* ================= 数据加载 ================= */
async function fetchProducts() {
  try {
    products.value = (await listProducts()) || []
  } catch {
    products.value = []
  }
}

async function fetchHistory() {
  try {
    historyRecords.value = (await listHistory()) || []
  } catch {
    historyRecords.value = []
  }
}

function onProductChange(id) {
  const p = products.value.find((x) => x.id === id)
  product.value = p || null
}

async function handleCreateProduct() {
  const name = (customForm.value.name || '').trim()
  if (!name) {
    ElMessage.warning('请填写商品名称')
    return
  }
  creatingProduct.value = true
  try {
    const created = await createProduct({
      name,
      category: customForm.value.category,
      sell_points: customForm.value.sell_points,
      price: customForm.value.price,
    })
    await fetchProducts()
    selectedProductId.value = created.id
    product.value = created
    customVisible.value = false
    customForm.value = { name: '', category: '', sell_points: '', price: undefined }
    ElMessage.success(`已创建并选用「${created.name}」`)
  } catch (e) {
    ElMessage.error(e?.message || '创建商品失败')
  } finally {
    creatingProduct.value = false
  }
}

/* ================= 文案生成 / 重新生成 ================= */
async function generateCopy(regenerate) {
  if (!regenerate && !product.value) {
    ElMessage.warning('请先选择商品')
    return
  }
  loadingCopy.value = true
  try {
    const payload = regenerate
      ? { session_id: sessionId.value, regenerate: true }
      : { product: product.value, keyword: keyword.value }
    const snap = await adgenCopy(payload)
    applySession(snap)
    mode.value = 'workspace'
    await nextTick()
    updateScrollState()
    ElMessage.success(regenerate ? '已重新生成文案' : '文案生成完成')
    if (snap.status === 'review_required') {
      ElMessage.warning('文案命中违禁词，请在文案卡中处置')
    }
  } catch (e) {
    ElMessage.error(e?.message || '文案生成失败')
  } finally {
    loadingCopy.value = false
  }
}

async function handleReview(decision) {
  try {
    const snap = await adgenReview(sessionId.value, { decision })
    applySession(snap)
    ElMessage.success(decision === 'approve' ? '已放行' : '已驳回')
  } catch (e) {
    ElMessage.error(e?.message || '审查处置失败')
  }
}

function applySession(snap) {
  if (!snap) return
  sessionId.value = snap.session_id || sessionId.value
  product.value = snap.product || product.value
  keyword.value = snap.keyword ?? keyword.value
  copy.value = snap.copy || {}
  review.value = snap.review || {}
  sessionStatus.value = snap.status || 'copy_ready'
  image.value = snap.image || {}
  video.value = snap.video || {}
  if (image.value.url) wantImage.value = true
  if (video.value.url) wantVideo.value = true
}

/* ================= 图文 / 视频生成 ================= */
function onPickImage(uploadFile) {
  imageFile.value = uploadFile.raw
}
function onPickVideo(uploadFile) {
  videoFile.value = uploadFile.raw
}
function clearImage() {
  imageFile.value = null
}
function clearVideo() {
  videoFile.value = null
}

async function generateAsset(type) {
  const isImage = type === 'image'
  const kw = (isImage ? imageKeyword.value : videoKeyword.value || '').trim()
  const file = isImage ? imageFile.value : videoFile.value
  const flag = isImage ? imageGenerating : videoGenerating
  flag.value = true
  try {
    const fd = new FormData()
    fd.append('session_id', sessionId.value)
    fd.append('type', type)
    fd.append('keyword', kw)
    if (file) fd.append('image', file)
    const { task_id } = await adgenAsset(fd)
    await pollAsset(task_id, type)
  } catch (e) {
    ElMessage.error(e?.message || `${isImage ? '图文' : '视频'}生成失败`)
  } finally {
    flag.value = false
  }
}

async function pollAsset(taskId, type) {
  const target = type === 'image' ? image : video
  for (let i = 0; i < 120; i += 1) {
    let task
    try {
      task = await adgenAssetStatus(taskId)
    } catch {
      break
    }
    if (task.status === 'succeeded') {
      target.value = { url: task.url, error: task.error }
      ElMessage.success(`${type === 'image' ? '图文' : '视频'}生成完成`)
      return
    }
    if (task.status === 'failed') {
      target.value = { url: null, error: task.error || '生成失败' }
      ElMessage.error(task.error || `${type === 'image' ? '图文' : '视频'}生成失败`)
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 2000))
  }
  target.value = { url: null, error: '生成超时，请稍后重试' }
}

/* ================= 底部三按钮 ================= */
async function handleSave() {
  if (!copy.value.content) return
  saving.value = true
  try {
    const res = await adgenSave({ session_id: sessionId.value })
    sessionStatus.value = 'saved'
    savedHint.value = res?.message || '已进入广告流水线，待审批通过后可在「广告投放」投放'
    ElMessage.success('已存到投放界面并写入历史记录')
    await fetchHistory()
  } catch (e) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handlePause() {
  if (!copy.value.content) return
  pausing.value = true
  try {
    await adgenPause({ session_id: sessionId.value })
    ElMessage.success('已保存为半成品并退出')
    resetWorkspace()
  } catch (e) {
    ElMessage.error(e?.message || '保存半成品失败')
  } finally {
    pausing.value = false
  }
}

async function handleDiscard() {
  try {
    await ElMessageBox.confirm('删除此次生成？未保存的数据将被清除。', '删除此次生成', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  discarding.value = true
  try {
    await adgenDiscard({ session_id: sessionId.value })
    ElMessage.success('已删除此次生成')
    resetWorkspace()
  } catch (e) {
    ElMessage.error(e?.message || '删除失败')
  } finally {
    discarding.value = false
  }
}

/* ================= 历史记录载入 ================= */
async function openHistory() {
  startTab.value = 'history'
  await fetchHistory()
}

async function loadHistory(record) {
  try {
    const snap = await adgenImport(record.id)
    applySession(snap)
    keyword.value = snap.keyword || ''
    mode.value = 'workspace'
    await nextTick()
    updateScrollState()
    ElMessage.success('已载入历史模板')
  } catch (e) {
    ElMessage.error(e?.message || '载入历史记录失败')
  }
}

/* ================= 状态重置 ================= */
function backToStart() {
  resetWorkspace()
  mode.value = 'start'
  startTab.value = 'new'
}

function resetWorkspace() {
  sessionId.value = ''
  copy.value = {}
  review.value = {}
  sessionStatus.value = ''
  image.value = {}
  video.value = {}
  wantImage.value = false
  wantVideo.value = false
  imageKeyword.value = ''
  videoKeyword.value = ''
  imageFile.value = null
  videoFile.value = null
  savedHint.value = ''
}

/* ================= 横向滚动 ================= */
function scrollSteps(dir) {
  const el = stepsEl.value
  if (!el) return
  el.scrollBy({ left: dir * 372, behavior: 'smooth' })
}
function onStepsScroll() {
  updateScrollState()
}
function updateScrollState() {
  const el = stepsEl.value
  if (!el) return
  scrollAtStart.value = el.scrollLeft <= 4
  scrollAtEnd.value = el.scrollLeft + el.clientWidth >= el.scrollWidth - 4
}

onMounted(fetchProducts)
</script>

<style scoped>
.adgen :deep(.gen-card-head) {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.hero-reset {
  margin-left: auto;
  color: #c8d3e8 !important;
}

/* ===== 开始面板 ===== */
.start-panel { display: flex; flex-direction: column; gap: 16px; }
.start-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
.start-card {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fff;
  border: 1.5px solid var(--card-border);
  border-radius: 14px;
  padding: 20px 22px;
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}
.start-card:hover { transform: translateY(-2px); box-shadow: 0 10px 24px rgba(29, 43, 79, 0.1); }
.start-card.is-active { border-color: #409eff; box-shadow: 0 0 0 3px rgba(64, 158, 255, 0.14); }
.start-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 48px; height: 48px; border-radius: 13px; flex-shrink: 0;
  background: color-mix(in srgb, var(--c) 14%, #fff); color: var(--c);
}
.start-body { flex: 1; }
.start-title { font-size: 16px; font-weight: 600; color: #1d2b4f; }
.start-desc { font-size: 12px; color: #909399; margin-top: 4px; line-height: 1.5; }
.start-arrow { color: #c0c4cc; }
.start-form { margin-top: 0; }

.product-row { display: flex; gap: 10px; width: 100%; }
.product-preview {
  background: #f7f9fc; border: 1px solid var(--card-border); border-radius: 10px;
  padding: 12px 14px; margin-bottom: 16px;
}
.product-preview-head { display: flex; align-items: center; gap: 8px; }
.product-name { font-weight: 600; color: #1d2b4f; }
.product-points { font-size: 12px; color: #909399; margin-top: 6px; }
.gen-submit { width: 100%; }

/* 历史记录 */
.history-list {
  display: flex; flex-direction: column; gap: 10px;
  max-height: 52vh; overflow-y: auto; padding-right: 4px;
}
.history-item {
  display: flex; align-items: center; gap: 12px;
  border: 1px solid var(--card-border); border-radius: 10px; padding: 12px 14px;
  cursor: pointer; transition: border-color 0.2s ease, background 0.2s ease;
}
.history-item:hover { border-color: #409eff; background: #f2f8ff; }
.history-item-main { flex: 1; min-width: 0; }
.history-item-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.history-product { font-weight: 600; color: #1d2b4f; }
.history-keyword { font-size: 12px; color: #606266; }
.history-time { margin-left: auto; font-size: 12px; color: #909399; }
.history-copy {
  font-size: 13px; color: #4b5563; margin-top: 6px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.history-flags { display: flex; gap: 6px; margin-top: 6px; }

/* ===== 工作台：横向步骤卡 ===== */
.workspace { display: flex; flex-direction: column; gap: 12px; }
.step-nav { display: flex; align-items: center; gap: 12px; }
.step-nav-hint { font-size: 12px; color: #909399; }

.steps {
  display: flex;
  gap: 16px;
  overflow-x: auto;
  padding: 4px 2px 14px;
  scroll-behavior: smooth;
}
.step-card {
  flex: 0 0 356px;
  background: #fff;
  border: 1px solid var(--card-border);
  border-radius: 14px;
  box-shadow: 0 4px 14px rgba(29, 43, 79, 0.06);
  display: flex;
  flex-direction: column;
  transition: border-color 0.2s ease;
}
.step-card.is-focus { border-color: #e6a23c; box-shadow: 0 0 0 3px rgba(230, 162, 60, 0.15); }
.step-head {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 16px; border-bottom: 1px solid var(--card-border);
}
.step-no {
  display: inline-flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; border-radius: 50%;
  background: #1d2b4f; color: #fff; font-size: 13px; font-weight: 600; flex-shrink: 0;
}
.step-title { font-weight: 600; color: #1d2b4f; }
.step-want { margin-left: auto; }
.step-body {
  padding: 16px; display: flex; flex-direction: column; gap: 10px;
  flex: 1; overflow-y: auto; max-height: 58vh;
}
.step-empty { color: #a8abb2; font-size: 13px; padding: 24px 0; text-align: center; }

.step-review {
  margin-left: auto; display: inline-flex; align-items: center; gap: 4px;
  font-size: 12px; padding: 2px 8px; border-radius: 10px;
}
.step-review.is-ok { color: #67c23a; background: rgba(103, 194, 58, 0.12); }
.step-review.is-warn { color: #e6a23c; background: rgba(230, 162, 60, 0.14); }
.step-review.is-bad { color: #f56c6c; background: rgba(245, 108, 108, 0.12); }
.step-review.is-idle { color: #909399; background: #f2f3f5; }

.kv-name { font-size: 16px; font-weight: 600; color: #1d2b4f; }
.kv-tags { display: flex; gap: 6px; flex-wrap: wrap; }
.kv-points { font-size: 13px; color: #606266; line-height: 1.6; }
.kv-keyword { font-size: 12px; color: #909399; }

.copy-title { font-size: 15px; font-weight: 600; margin: 0; color: #303133; line-height: 1.5; }
.copy-body {
  background: #f7f9fc; border: 1px solid var(--card-border); border-radius: 10px;
  padding: 12px 14px; font-size: 13px; line-height: 1.8; color: #4b5563;
  white-space: pre-wrap; word-break: break-word; max-height: 32vh; overflow-y: auto;
}
.copy-alert { margin-top: 4px; }
.violations { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
.violation-chip {
  font-size: 12px; background: #fff; border: 1px solid #f5c2c0; border-radius: 8px;
  padding: 1px 8px; color: #f56c6c;
}
.violation-chip em { color: #67c23a; font-style: normal; }
.step-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.step-input { width: 100%; }
.step-uploader :deep(.el-upload--picture-card),
.step-uploader :deep(.el-upload-list__item) { width: 84px; height: 84px; }
.step-generate { width: 100%; }
.asset-preview { display: flex; flex-direction: column; gap: 8px; }
.asset-img {
  width: 100%; border-radius: 10px; border: 1px solid var(--card-border);
  max-height: 30vh;
}
.asset-video {
  width: 100%; border-radius: 10px; border: 1px solid var(--card-border);
  background: #000; max-height: 30vh;
}

.step-finish { flex: 0 0 300px; }
.finish-summary { display: flex; flex-direction: column; gap: 8px; }
.finish-row {
  display: flex; justify-content: space-between; font-size: 13px;
  color: #606266; border-bottom: 1px dashed var(--card-border); padding-bottom: 8px;
}
.finish-row b { color: #1d2b4f; }
.finish-tip { font-size: 12px; color: #909399; line-height: 1.7; margin: 4px 0 0; }

/* ===== 底部操作栏 ===== */
.action-bar {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  background: #fff; border: 1px solid var(--card-border); border-radius: 14px;
  padding: 14px 18px; box-shadow: 0 4px 14px rgba(29, 43, 79, 0.06);
}
.action-hint { font-size: 12px; color: #909399; }
</style>