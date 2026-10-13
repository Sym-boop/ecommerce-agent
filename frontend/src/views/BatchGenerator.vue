<template>
  <div class="gen-page batch">
    <!-- ===== 页头 ===== -->
    <div class="gen-hero">
      <span class="gen-hero-icon">
        <el-icon :size="24"><Grid /></el-icon>
      </span>
      <div>
        <h1 class="gen-hero-title">批量生成</h1>
        <p class="gen-hero-desc">
          一次看到所有生成结果：商品 → 批量文案 → 每条文案再批量生成图文 / 视频。每个节点可单独重新生成，每一级可一键保存 / 一键重新生成。
        </p>
      </div>
      <el-button v-if="batchId" class="hero-reset" text :icon="RefreshLeft" @click="backToStart">
        重新开始
      </el-button>
    </div>

    <!-- ===================================================================
         开始面板：选商品 + 批量条数
    ==================================================================== -->
    <el-card v-if="!batchId" class="gen-card start-form" shadow="never">
      <template #header>选择商品并设置批量条数</template>
      <el-form label-position="top">
        <el-form-item label="商品（内置清单，可自定义）" required>
          <div class="product-row">
            <el-select v-model="selectedProductId" placeholder="请选择商品" filterable style="flex: 1" @change="onProductChange">
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
          <div v-if="product.sell_points?.length" class="product-points">卖点：{{ product.sell_points.join(' / ') }}</div>
        </div>

        <el-form-item label="关键词（可选，用于引导文案方向）">
          <el-input v-model="keyword" placeholder="如：通勤 / 降噪 / 送礼 / 学生党" maxlength="40" show-word-limit />
        </el-form-item>

        <el-form-item :label="`一次批量生成文案条数（1 ~ ${MAX_COPY}）`">
          <el-slider v-model="copyCount" :min="1" :max="MAX_COPY" show-stops style="max-width: 420px" />
        </el-form-item>
      </el-form>

      <el-button
        type="primary"
        size="large"
        :icon="Grid"
        :loading="creating"
        :disabled="!product"
        class="gen-submit"
        @click="startBatch"
      >
        {{ creating ? '正在批量生成…' : `开始批量生成 ${copyCount} 条文案` }}
      </el-button>
    </el-card>

    <!-- ===================================================================
         分叉树工作台
    ==================================================================== -->
    <div v-else class="board">
      <!-- 左侧：各级一键操作 -->
      <aside class="level-tools">
        <div class="lt-head">级别操作</div>
        <div v-for="lv in levels" :key="lv.key" class="lt-item" :class="{ 'is-empty': lv.count === 0 }">
          <div class="lt-name">
            <el-icon><component :is="lv.icon" /></el-icon>
            <span>{{ lv.label }}</span>
            <b>{{ lv.count }}</b>
          </div>
          <div class="lt-actions">
            <el-button size="small" :disabled="lv.count === 0" :loading="savingLevel === lv.key" @click="saveLevel(lv.key)">
              一键保存
            </el-button>
            <el-button size="small" type="primary" plain :disabled="lv.count === 0" @click="regenLevel(lv.key)">
              一键重新生成
            </el-button>
          </div>
        </div>
        <div class="lt-foot">
          <el-button size="small" type="danger" plain :icon="Delete" @click="discardBatch">丢弃本次批量</el-button>
        </div>
      </aside>

      <!-- 右侧：分叉树 -->
      <div class="tree-wrap">
        <div class="tree">
          <!-- 根：商品 -->
          <div class="root-node">
            <div class="root-label">商品</div>
            <div class="root-name">{{ batchProduct?.name || '未命名商品' }}</div>
            <div class="root-meta">
              <el-tag v-if="batchProduct?.category" size="small" effect="plain">{{ batchProduct.category }}</el-tag>
              <el-tag v-if="batchKeyword" size="small" type="warning" effect="plain">关键词：{{ batchKeyword }}</el-tag>
            </div>
          </div>

          <div class="branches">
            <div v-for="c in copyNodes" :key="c.id" class="branch">
              <!-- 文案节点 -->
              <div class="node copy-node" :class="nodeClass(c)">
                <div class="node-head">
                  <span class="node-badge copy">文案 {{ c.index + 1 }}</span>
                  <span class="node-status" :class="statusClass(c)">{{ statusText(c) }}</span>
                </div>

                <div v-if="c.status === 'done'" class="node-body">
                  <h3 class="copy-title">{{ c.title }}</h3>
                  <div class="copy-body">{{ c.content }}</div>
                  <div class="node-tags">
                    <el-tag v-for="t in (c.tags || []).slice(0, 5)" :key="t" size="small" effect="plain">{{ t }}</el-tag>
                  </div>
                  <div v-if="c.review && c.review.copy_ok === false" class="violations">
                    <span class="viol-hint">命中违禁词：</span>
                    <span v-for="(v, i) in c.review.violations || []" :key="i" class="violation-chip">{{ v.word }}</span>
                  </div>
                </div>
                <div v-else-if="c.status === 'failed'" class="node-body">
                  <el-alert type="error" :closable="false" show-icon :title="c.error || '文案生成失败'" />
                </div>
                <div v-else class="node-loading">
                  <span class="spinner"></span> 文案生成中…
                </div>

                <!-- 每级：可选图片 / 关键词 + 批量条数 + 生成按钮 -->
                <div class="asset-controls">
                  <el-input v-model="copyKeywords[c.id]" size="small" placeholder="关键词（可选）" class="ac-input" maxlength="40" />
                  <div class="ac-row">
                    <el-upload
                      class="ac-upload"
                      list-type="picture-card"
                      :auto-upload="false"
                      :limit="1"
                      accept="image/*"
                      :show-file-list="false"
                      :on-change="(f) => onPickImage(c.id, f)"
                    >
                      <span class="ac-upload-inner">
                        <el-icon><Plus /></el-icon>
                        <em>{{ imageFiles[c.id] ? '已选图' : '图片' }}</em>
                      </span>
                    </el-upload>
                    <el-button v-if="imageFiles[c.id]" size="small" text @click="clearImage(c.id)">清除</el-button>
                  </div>
                  <div class="ac-row">
                    <span class="ac-label">图文</span>
                    <el-input-number v-model="imageCounts[c.id]" :min="1" :max="MAX_ASSET" size="small" :controls="false" class="ac-num" />
                    <el-button size="small" type="success" plain :icon="PictureFilled" :disabled="c.status !== 'done'" @click="genAsset(c, 'image')">
                      生成图文
                    </el-button>
                    <span class="ac-label">视频</span>
                    <el-input-number v-model="videoCounts[c.id]" :min="1" :max="MAX_ASSET" size="small" :controls="false" class="ac-num" />
                    <el-button size="small" type="danger" plain :icon="VideoCameraFilled" :disabled="c.status !== 'done'" @click="genAsset(c, 'video')">
                      生成视频
                    </el-button>
                  </div>
                </div>

                <!-- 每个文案节点自身的操作 -->
                <div class="node-actions">
                  <el-button size="small" :icon="RefreshRight" :disabled="c.status === 'processing' || c.status === 'pending'"
                    @click="regenNode(c)">重新生成文案</el-button>
                  <el-button size="small" type="primary" :icon="Promotion" :disabled="c.status !== 'done'" @click="saveCopy(c)">
                    保存这条
                  </el-button>
                </div>
              </div>

              <!-- 子节点：图文 / 视频 -->
              <div v-if="childrenOf(c.id).length" class="children">
                <div v-for="ch in childrenOf(c.id)" :key="ch.id" class="node child-node" :class="nodeClass(ch)">
                  <div class="node-head">
                    <span class="node-badge" :class="ch.type">{{ ch.type === 'image' ? '图文' : '视频' }} {{ ch.index + 1 }}</span>
                    <span class="node-status" :class="statusClass(ch)">{{ statusText(ch) }}</span>
                  </div>
                  <div v-if="ch.status === 'done' && ch.url" class="child-media">
                    <el-image
                      v-if="ch.type === 'image'"
                      class="child-img"
                      :src="mediaUrl(ch.url)"
                      fit="cover"
                      :preview-src-list="[mediaUrl(ch.url)]"
                      hide-on-click-modal
                    />
                    <video v-else class="child-video" :src="mediaUrl(ch.url)" controls preload="metadata" muted></video>
                  </div>
                  <div v-else-if="ch.status === 'failed'" class="node-body">
                    <el-alert type="error" :closable="false" show-icon :title="ch.error || '生成失败'" />
                  </div>
                  <div v-else class="node-loading"><span class="spinner"></span> 生成中…</div>
                  <div class="node-actions">
                    <el-button size="small" :icon="RefreshRight" :disabled="ch.status === 'processing' || ch.status === 'pending'"
                      @click="regenNode(ch)">重新生成</el-button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
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
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Grid, Plus, RefreshRight, RefreshLeft, Delete, Promotion,
  PictureFilled, VideoCameraFilled, Document, Picture, VideoCamera,
} from '@element-plus/icons-vue'
import {
  listProducts, createProduct,
  batchCopy, batchAsset, batchRegenerate, batchSession, batchSave, batchDiscard,
} from '@/api'
import { mediaUrl } from '@/utils/format'

defineOptions({ name: 'BatchGenerator' })

const MAX_COPY = 8
const MAX_ASSET = 5

/* ---------------- 开始面板 ---------------- */
const products = ref([])
const selectedProductId = ref('')
const product = ref(null)
const keyword = ref('')
const copyCount = ref(3)
const creating = ref(false)
const customVisible = ref(false)
const creatingProduct = ref(false)
const customForm = ref({ name: '', category: '', sell_points: '', price: undefined })

/* ---------------- 批量会话 / 分叉树 ---------------- */
const batchId = ref('')
const nodes = ref([])
const batchProduct = ref({})
const batchKeyword = ref('')
const savingLevel = ref('')

/* 每个文案节点的图文 / 视频参数 */
const copyKeywords = reactive({})
const imageCounts = reactive({})
const videoCounts = reactive({})
const imageFiles = reactive({})

let pollTimer = null

const copyNodes = computed(() =>
  nodes.value.filter((n) => n.type === 'copy').sort((a, b) => a.index - b.index),
)
const childrenOf = (copyId) =>
  nodes.value.filter((n) => n.parent_id === copyId).sort((a, b) => a.index - b.index)

const levels = computed(() => [
  { key: 'copy', label: '文案', icon: Document, count: nodes.value.filter((n) => n.type === 'copy').length },
  { key: 'image', label: '图文', icon: Picture, count: nodes.value.filter((n) => n.type === 'image').length },
  { key: 'video', label: '视频', icon: VideoCamera, count: nodes.value.filter((n) => n.type === 'video').length },
])

/* ================= 数据加载 ================= */
async function fetchProducts() {
  try {
    products.value = (await listProducts()) || []
  } catch {
    products.value = []
  }
}
function onProductChange(id) {
  product.value = products.value.find((x) => x.id === id) || null
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
      name, category: customForm.value.category,
      sell_points: customForm.value.sell_points, price: customForm.value.price,
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

/* ================= 批量生成 ================= */
async function startBatch() {
  if (!product.value) {
    ElMessage.warning('请先选择商品')
    return
  }
  creating.value = true
  try {
    const snap = await batchCopy({
      product: product.value, keyword: keyword.value, count: copyCount.value,
    })
    applySnapshot(snap)
    startPolling()
    ElMessage.success(`已提交 ${copyCount.value} 条文案的批量生成`)
  } catch (e) {
    ElMessage.error(e?.message || '批量生成失败')
  } finally {
    creating.value = false
  }
}

function applySnapshot(snap) {
  if (!snap) return
  batchId.value = snap.batch_id || batchId.value
  nodes.value = snap.nodes || []
  batchProduct.value = snap.product || {}
  batchKeyword.value = snap.keyword || ''
  // 初始化每条文案的默认批量条数与参数
  nodes.value.forEach((n) => {
    if (n.type === 'copy') {
      if (imageCounts[n.id] === undefined) imageCounts[n.id] = 1
      if (videoCounts[n.id] === undefined) videoCounts[n.id] = 1
      if (copyKeywords[n.id] === undefined) copyKeywords[n.id] = n.keyword || ''
    }
  })
}

async function refresh() {
  if (!batchId.value) return
  try {
    applySnapshot(await batchSession(batchId.value))
  } catch {
    // 会话丢失等异常不打断轮询
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    await refresh()
    const active = nodes.value.some((n) => n.status === 'pending' || n.status === 'processing')
    if (!active) stopPolling()
  }, 2000)
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function onPickImage(copyId, uploadFile) {
  imageFiles[copyId] = uploadFile.raw
}
function clearImage(copyId) {
  imageFiles[copyId] = null
}

async function genAsset(copyNode, type) {
  const isImage = type === 'image'
  const count = isImage ? imageCounts[copyNode.id] : videoCounts[copyNode.id]
  try {
    const fd = new FormData()
    fd.append('batch_id', batchId.value)
    fd.append('copy_id', copyNode.id)
    fd.append('type', type)
    fd.append('count', String(count || 1))
    fd.append('keyword', String(copyKeywords[copyNode.id] || ''))
    if (imageFiles[copyNode.id]) fd.append('image', imageFiles[copyNode.id])
    applySnapshot(await batchAsset(fd))
    startPolling()
    ElMessage.success(`已提交 ${count} 个${isImage ? '图文' : '视频'}生成`)
  } catch (e) {
    ElMessage.error(e?.message || '提交失败')
  }
}

async function regenNode(node) {
  try {
    applySnapshot(await batchRegenerate({ batch_id: batchId.value, node_id: node.id }))
    startPolling()
  } catch (e) {
    ElMessage.error(e?.message || '重新生成失败')
  }
}

/* ================= 等级操作 ================= */
async function saveLevel(level) {
  const copyIds = nodes.value
    .filter((n) => n.type === 'copy' && n.status === 'done')
    .filter((c) => {
      if (level === 'copy') return true
      return nodes.value.some((n) => n.parent_id === c.id && n.type === level && n.status === 'done')
    })
    .map((c) => c.id)
  await doSave(copyIds, level)
}

async function saveCopy(copyNode) {
  await doSave([copyNode.id], 'copy')
}

async function doSave(copyIds, level) {
  if (!copyIds.length) {
    ElMessage.warning('暂无可保存的内容')
    return
  }
  savingLevel.value = level || 'copy'
  try {
    const res = await batchSave({ batch_id: batchId.value, copy_ids: copyIds })
    ElMessage.success(res?.message || '已存到投放界面并写入历史记录')
  } catch (e) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    savingLevel.value = ''
  }
}

async function regenLevel(level) {
  const targets = nodes.value.filter((n) => {
    if (level === 'copy') return n.type === 'copy'
    return n.type === level
  })
  if (!targets.length) {
    ElMessage.warning('该级别暂无可重新生成的节点')
    return
  }
  const list = targets.filter((n) => n.status === 'done' || n.status === 'failed')
  if (!list.length) return
  try {
    await Promise.all(list.map((n) => batchRegenerate({ batch_id: batchId.value, node_id: n.id })))
    await refresh()
    startPolling()
    ElMessage.success(`已提交 ${list.length} 个节点的重新生成`)
  } catch (e) {
    ElMessage.error(e?.message || '重新生成失败')
  }
}

async function discardBatch() {
  try {
    await ElMessageBox.confirm('丢弃本次批量生成？所有未保存的节点将被清除。', '丢弃本次批量', {
      type: 'warning', confirmButtonText: '丢弃', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await batchDiscard({ batch_id: batchId.value })
    ElMessage.success('已丢弃本次批量生成')
    backToStart()
  } catch (e) {
    ElMessage.error(e?.message || '丢弃失败')
  }
}

function backToStart() {
  stopPolling()
  batchId.value = ''
  nodes.value = []
  batchProduct.value = {}
  batchKeyword.value = ''
  savingLevel.value = ''
}

/* ================= 展示辅助 ================= */
function statusText(n) {
  if (n.status === 'done') return '已完成'
  if (n.status === 'failed') return '失败'
  if (n.status === 'processing' || n.status === 'pending') return '生成中'
  return '等待中'
}
function statusClass(n) {
  return {
    'is-ok': n.status === 'done',
    'is-bad': n.status === 'failed',
    'is-run': n.status === 'processing' || n.status === 'pending',
  }
}
function nodeClass(n) {
  return {
    'is-ok': n.status === 'done',
    'is-bad': n.status === 'failed',
    'is-run': n.status === 'processing' || n.status === 'pending',
  }
}

onMounted(fetchProducts)
</script>

<style scoped>
.batch :deep(.gen-card-head) {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.hero-reset {
  margin-left: auto;
  color: #c8d3e8 !important;
}

/* 开始面板 */
.product-row { display: flex; gap: 10px; width: 100%; }
.product-preview {
  background: #f7f9fc; border: 1px solid var(--card-border); border-radius: 10px;
  padding: 12px 14px; margin-bottom: 16px;
}
.product-preview-head { display: flex; align-items: center; gap: 8px; }
.product-name { font-weight: 600; color: #1d2b4f; }
.product-points { font-size: 12px; color: #909399; margin-top: 6px; }
.gen-submit { width: 100%; }

/* ===== 工作台布局 ===== */
.board { display: flex; gap: 16px; align-items: flex-start; }

.level-tools {
  flex: 0 0 208px;
  position: sticky;
  top: 0;
  background: rgba(255, 255, 255, 0.86);
  backdrop-filter: blur(6px);
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 14px;
  box-shadow: 0 6px 18px rgba(29, 43, 79, 0.08);
}
.lt-head { font-weight: 600; color: #1d2b4f; margin-bottom: 10px; }
.lt-item {
  border-top: 1px dashed var(--card-border);
  padding: 10px 0;
}
.lt-item.is-empty { opacity: 0.55; }
.lt-name { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #303133; margin-bottom: 8px; }
.lt-name b { margin-left: auto; color: #1d2b4f; }
.lt-actions { display: flex; flex-direction: column; gap: 6px; }
.lt-actions .el-button { margin-left: 0 !important; width: 100%; }
.lt-foot { border-top: 1px dashed var(--card-border); padding-top: 12px; margin-top: 4px; }
.lt-foot .el-button { width: 100%; }

/* ===== 分叉树 ===== */
.tree-wrap { flex: 1; min-width: 0; overflow-x: auto; padding-bottom: 8px; }
.tree { min-width: 640px; }
.root-node {
  display: inline-flex; flex-direction: column; gap: 6px;
  background: linear-gradient(120deg, #1d2b4f, #2e4c86);
  color: #fff; border-radius: 12px; padding: 14px 18px;
  box-shadow: 0 8px 20px rgba(29, 43, 79, 0.22);
}
.root-label { font-size: 12px; opacity: 0.75; }
.root-name { font-size: 16px; font-weight: 600; }
.root-meta { display: flex; gap: 6px; flex-wrap: wrap; }

.branches {
  margin-top: 14px;
  padding-left: 26px;
  border-left: 2px solid rgba(64, 158, 255, 0.35);
  display: flex; flex-direction: column; gap: 16px;
}
.branch { position: relative; }
.branch::before {
  content: '';
  position: absolute; left: -26px; top: 26px;
  width: 26px; height: 2px; background: rgba(64, 158, 255, 0.35);
}

.node {
  background: #fff;
  border: 1px solid var(--card-border);
  border-left: 4px solid #c0c4cc;
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 4px 14px rgba(29, 43, 79, 0.06);
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
}
.node.is-ok { border-left-color: #67c23a; }
.node.is-bad { border-left-color: #f56c6c; }
.node.is-run { border-left-color: #409eff; box-shadow: 0 0 0 3px rgba(64, 158, 255, 0.14); }

.node-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.node-badge {
  font-size: 12px; font-weight: 600; color: #fff;
  padding: 2px 9px; border-radius: 9px; background: #1d2b4f;
}
.node-badge.copy { background: #409eff; }
.node-badge.image { background: #23bea0; }
.node-badge.video { background: #f56c6c; }
.node-status { margin-left: auto; font-size: 12px; padding: 1px 8px; border-radius: 9px; }
.node-status.is-ok { color: #67c23a; background: rgba(103, 194, 58, 0.12); }
.node-status.is-bad { color: #f56c6c; background: rgba(245, 108, 108, 0.12); }
.node-status.is-run { color: #409eff; background: rgba(64, 158, 255, 0.12); }

.copy-title { font-size: 14px; font-weight: 600; margin: 0 0 6px; color: #303133; }
.copy-body {
  background: #f7f9fc; border: 1px solid var(--card-border); border-radius: 8px;
  padding: 10px 12px; font-size: 13px; line-height: 1.7; color: #4b5563;
  white-space: pre-wrap; word-break: break-word; max-height: 168px; overflow-y: auto;
}
.node-tags { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 8px; }
.violations { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; align-items: center; }
.viol-hint { font-size: 12px; color: #f56c6c; }
.violation-chip { font-size: 12px; background: #fff5f5; border: 1px solid #f5c2c0; border-radius: 8px; padding: 0 7px; color: #f56c6c; }

.node-loading { display: flex; align-items: center; gap: 8px; color: #909399; font-size: 13px; padding: 10px 0; }
.spinner {
  width: 14px; height: 14px; border-radius: 50%;
  border: 2px solid rgba(64, 158, 255, 0.3); border-top-color: #409eff;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* 每级生成控制 */
.asset-controls {
  margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--card-border);
  display: flex; flex-direction: column; gap: 8px;
}
.ac-input { width: 100%; }
.ac-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ac-label { font-size: 12px; color: #909399; }
.ac-num { width: 66px; }
.ac-upload :deep(.el-upload--picture-card) {
  width: 62px; height: 62px; line-height: 1; border-radius: 8px;
}
.ac-upload-inner { display: flex; flex-direction: column; align-items: center; gap: 2px; font-size: 11px; color: #909399; }
.ac-upload-inner em { font-style: normal; }

.node-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }

/* 子节点 */
.children {
  margin: 10px 0 0 26px; padding-left: 20px;
  border-left: 2px dashed rgba(35, 190, 160, 0.4);
  display: flex; flex-direction: column; gap: 10px;
}
.child-node { position: relative; }
.child-node::before {
  content: ''; position: absolute; left: -20px; top: 22px;
  width: 20px; height: 2px; background: rgba(35, 190, 160, 0.4);
}
.child-media { margin-top: 4px; }
.child-img { width: 100%; max-width: 260px; border-radius: 10px; border: 1px solid var(--card-border); display: block; }
.child-video { width: 100%; max-width: 300px; border-radius: 10px; background: #000; display: block; }
</style>