<template>
  <div class="delivery" :style="themeStyle">
    <!-- ================= 平台选择（一级） ================= -->
    <template v-if="!current">
      <div class="gen-hero">
        <span class="gen-hero-icon">
          <el-icon :size="24"><Promotion /></el-icon>
        </span>
        <div>
          <h1 class="gen-hero-title">广告投放</h1>
          <p class="gen-hero-desc">
            选择目标平台，进入平台投放台：投放广告、查看投放历史、了解平台介绍。投放为 Mock 模拟，不产生真实扣费。
          </p>
        </div>
      </div>

      <div class="platform-grid">
        <div
          v-for="p in platforms"
          :key="p.value"
          class="platform-card"
          :style="{ '--c': p.theme_color }"
          @click="enterPlatform(p)"
        >
          <div class="platform-card-top">
            <PlatformLogo :logo-key="p.logo_key" :color="p.theme_color" :label="p.label" :size="54" />
            <div class="platform-card-name">
              <div class="platform-title">{{ p.label }}</div>
              <div class="platform-belong">{{ p.belong }}</div>
            </div>
            <el-tag size="small" effect="plain" :style="tagStyle(p)">{{ p.category }}</el-tag>
          </div>
          <p class="platform-intro">{{ p.intro }}</p>
          <div class="platform-features">
            <span v-for="f in p.features" :key="f" class="feature-chip">{{ f }}</span>
          </div>
          <div class="platform-card-foot">
            <span class="enter-text">进入投放台</span>
            <el-icon><ArrowRight /></el-icon>
          </div>
        </div>
      </div>
    </template>

    <!-- ================= 平台投放台（二级） ================= -->
    <template v-else>
      <div class="platform-hero" :style="heroStyle">
        <span class="watermark">
          <PlatformLogo :logo-key="current.logo_key" :color="current.theme_color" :label="current.label" :size="200" />
        </span>
        <el-button class="back-btn" text :icon="ArrowLeft" @click="exitPlatform">返回平台列表</el-button>
        <div class="platform-hero-main">
          <PlatformLogo :logo-key="current.logo_key" :color="current.theme_color" :label="current.label" :size="60" />
          <div>
            <h1 class="platform-hero-title">
              {{ current.label }}
              <span class="platform-hero-belong">{{ current.belong }}</span>
            </h1>
            <p class="platform-hero-desc">{{ current.intro }}</p>
          </div>
        </div>
      </div>

      <div class="platform-body">
        <!-- 左侧导航 -->
        <div class="side-nav">
          <div
            v-for="t in tabs"
            :key="t.key"
            class="side-item"
            :class="{ 'is-active': tab === t.key }"
            :style="tab === t.key ? { '--c': current.theme_color } : {}"
            @click="switchTab(t.key)"
          >
            <el-icon><component :is="t.icon" /></el-icon>
            <span>{{ t.label }}</span>
          </div>
          <div class="side-stats">
            <div class="side-stat"><b>{{ counts.available }}</b><span>未投放</span></div>
            <div class="side-stat"><b>{{ counts.history }}</b><span>已投放</span></div>
            <div class="side-stat"><b>{{ deliveries.length }}</b><span>投放批次</span></div>
          </div>
        </div>

        <!-- 右侧内容 -->
        <div class="platform-content">
          <!-- 广告投放 -->
          <template v-if="tab === 'deliver'">
            <el-card class="gen-card store-card" shadow="never">
              <template #header>
                <div class="gen-card-head">
                  <span>店铺（{{ stores.length }}）</span>
                  <div class="store-head-actions">
                    <el-button size="small" :icon="Refresh" @click="loadStores">刷新</el-button>
                    <el-button size="small" type="primary" :icon="Plus" @click="openStoreDialog">添加店铺</el-button>
                  </div>
                </div>
              </template>
              <el-empty
                v-if="stores.length === 0"
                description="该平台暂无店铺，点击右上角「添加店铺」录入店铺 / 商品信息"
                :image-size="70"
              />
              <div v-else class="store-list">
                <div
                  v-for="s in stores"
                  :key="s.id"
                  class="store-item"
                  :style="{ '--c': current.theme_color }"
                >
                  <div class="store-main">
                    <div class="store-row">
                      <span class="store-name">{{ s.name }}</span>
                      <el-tag size="small" effect="plain">店铺 ID {{ s.shop_id }}</el-tag>
                      <el-tag v-if="s.douyin_account" size="small" effect="plain" type="info">
                        抖音号 {{ s.douyin_account }}
                      </el-tag>
                    </div>
                    <div class="store-sub">
                      <span>商品 {{ (s.products || []).length }} 个</span>
                      <span v-if="s.products && s.products.length" class="mono">
                        {{ s.products.map((p) => p.product_id).join(' / ') }}
                      </span>
                      <span v-if="s.note" class="store-note">{{ s.note }}</span>
                    </div>
                  </div>
                  <div class="store-actions">
                    <el-button
                      size="small"
                      :icon="MagicStick"
                      :loading="simulatingId === s.id"
                      @click="simulateStore(s)"
                    >
                      模拟投递检测
                    </el-button>
                    <el-button
                      size="small"
                      text
                      type="danger"
                      :icon="Delete"
                      @click="handleDeleteStore(s)"
                    />
                  </div>
                </div>
              </div>

              <div
                v-if="simulateResult"
                class="preflight-result"
                :class="simulateResult.ready ? 'is-ok' : 'is-fail'"
              >
                <div class="preflight-head">
                  <el-tag size="small" :type="simulateResult.ready ? 'success' : 'danger'" effect="dark">
                    {{ simulateResult.ready ? '可以投递' : '不可投递' }}
                  </el-tag>
                  <span>
                    模拟投递检测（{{ simulateResult.mode }} 模式）
                    <template v-if="simulateResult.store"> · 店铺 {{ simulateResult.store.name }}</template>
                  </span>
                  <el-button class="preflight-close" size="small" text :icon="Close" @click="simulateResult = null" />
                </div>
                <ul class="preflight-checks">
                  <li
                    v-for="(c, i) in simulateResult.checks"
                    :key="i"
                    :class="c.ok ? 'ok' : 'fail'"
                  >
                    <el-icon><component :is="c.ok ? CircleCheck : CircleClose" /></el-icon>
                    <span class="check-name">{{ c.name }}</span>
                    <em v-if="c.detail" class="check-detail">{{ c.detail }}</em>
                  </li>
                </ul>
              </div>
            </el-card>

            <el-card class="gen-card" shadow="never">
              <template #header>
                <div class="gen-card-head">
                  <span>投放预算</span>
                  <span v-if="budgetConfig" class="budget-hint">
                    审查区间 ¥{{ budgetConfig.min_budget.toLocaleString() }} ~
                    ¥{{ budgetConfig.max_budget.toLocaleString() }}
                  </span>
                </div>
              </template>
              <el-input-number v-model="budget" :min="0" :step="1000" :controls="false" style="width: 220px" />

              <el-divider content-position="left">待投放广告（未投放）</el-divider>
              <el-empty
                v-if="availableAds.length === 0"
                description="暂无待投放广告，请先在「广告生成」存到投放界面并经审查中心审批通过"
                :image-size="70"
              />
              <div v-else class="ad-list">
                <label
                  v-for="ad in availableAds"
                  :key="ad.id"
                  class="ad-item"
                  :class="{ 'is-checked': selected.includes(ad.id) }"
                  :style="selected.includes(ad.id) ? { '--c': current.theme_color } : {}"
                >
                  <el-checkbox :model-value="selected.includes(ad.id)" @change="toggle(ad.id)" />
                  <div class="ad-main">
                    <div class="ad-row">
                      <el-tag size="small" :type="typeMeta(ad.type).tagType" effect="plain">
                        {{ typeMeta(ad.type).label }}
                      </el-tag>
                      <span class="ad-id mono">{{ ad.id }}</span>
                      <span class="ad-time">{{ formatTime(ad.created_at) }}</span>
                      <el-button
                        class="ad-del"
                        size="small"
                        text
                        type="danger"
                        :icon="Delete"
                        @click.stop.prevent="handleDelete(ad)"
                      />
                    </div>
                    <div class="ad-text">{{ ad.title || ad.prompt || '（无标题）' }}</div>
                  </div>
                  <el-image
                    v-if="ad.type === 'image' && ad.result_url"
                    class="ad-thumb"
                    :src="mediaUrl(ad.result_url)"
                    fit="cover"
                    :preview-src-list="[mediaUrl(ad.result_url)]"
                    hide-on-click-modal
                  />
                  <video
                    v-else-if="ad.type === 'video' && ad.result_url"
                    class="ad-thumb"
                    :src="mediaUrl(ad.result_url)"
                    preload="metadata"
                    muted
                  ></video>
                </label>
              </div>

              <el-divider content-position="left">投放历史中的广告（可再次投放）</el-divider>
              <el-empty v-if="historyAds.length === 0" description="该平台暂无投放历史" :image-size="70" />
              <div v-else class="ad-list">
                <label
                  v-for="ad in historyAds"
                  :key="ad.id"
                  class="ad-item is-history"
                  :class="{ 'is-checked': selected.includes(ad.id) }"
                  :style="selected.includes(ad.id) ? { '--c': current.theme_color } : {}"
                >
                  <el-checkbox :model-value="selected.includes(ad.id)" @change="toggle(ad.id)" />
                  <div class="ad-main">
                    <div class="ad-row">
                      <el-tag size="small" type="primary" effect="dark">历史投放</el-tag>
                      <el-tag size="small" :type="typeMeta(ad.type).tagType" effect="plain">
                        {{ typeMeta(ad.type).label }}
                      </el-tag>
                      <span class="ad-id mono">{{ ad.id }}</span>
                      <span class="ad-time">{{ formatTime(ad.created_at) }}</span>
                      <el-button
                        class="ad-del"
                        size="small"
                        text
                        type="danger"
                        :icon="Delete"
                        @click.stop.prevent="handleDelete(ad)"
                      />
                    </div>
                    <div class="ad-text">{{ ad.title || ad.prompt || '（无标题）' }}</div>
                  </div>
                  <el-image
                    v-if="ad.type === 'image' && ad.result_url"
                    class="ad-thumb"
                    :src="mediaUrl(ad.result_url)"
                    fit="cover"
                    :preview-src-list="[mediaUrl(ad.result_url)]"
                    hide-on-click-modal
                  />
                  <video
                    v-else-if="ad.type === 'video' && ad.result_url"
                    class="ad-thumb"
                    :src="mediaUrl(ad.result_url)"
                    preload="metadata"
                    muted
                  ></video>
                </label>
              </div>

              <div class="deliver-actions">
                <el-button type="primary" size="large" :icon="Promotion" :loading="submitting"
                  :disabled="selected.length === 0" @click="handleDeliver">
                  {{ submitting ? '投放中…' : `一键投放到 ${current.label}（已选 ${selected.length}）` }}
                </el-button>
                <el-button size="large" text @click="selected = []">清空选择</el-button>
              </div>

              <div v-if="latest" class="deliver-result" :style="{ '--c': current.theme_color }">
                <div class="deliver-result-head">
                  <el-tag size="small" type="success" effect="dark">批次 {{ latest.batch_id }}</el-tag>
                  <span>{{ latest.platform_label }}</span>
                  <span class="deliver-time">{{ formatTime(latest.submitted_at) }}</span>
                </div>
                <div class="deliver-summary">
                  共 {{ latest.items.length }} 条 · 成功 <b class="ok">{{ latest.success_count }}</b> ·
                  失败 <b class="fail">{{ latest.failed_count }}</b> · 预算 ¥{{ Number(latest.budget).toLocaleString() }}
                </div>
              </div>
            </el-card>
          </template>

          <!-- 历史投放查看 -->
          <template v-else-if="tab === 'history'">
            <el-card class="gen-card" shadow="never">
              <template #header>
                <div class="gen-card-head">
                  <span>历史投放批次（{{ deliveries.length }}）</span>
                  <el-button size="small" :icon="Refresh" @click="loadPlatform">刷新</el-button>
                </div>
              </template>
              <el-empty v-if="deliveries.length === 0" description="该平台暂无投放批次" :image-size="80" />
              <el-timeline v-else>
                <el-timeline-item
                  v-for="d in deliveries"
                  :key="d.batch_id"
                  :timestamp="formatTime(d.submitted_at)"
                  :color="current.theme_color"
                  placement="top"
                >
                  <div class="batch-row">
                    <el-tag size="small" effect="plain">{{ d.platform_label }}</el-tag>
                    <span class="mono">{{ d.batch_id }}</span>
                    <span class="batch-budget">预算 ¥{{ Number(d.budget).toLocaleString() }}</span>
                    <span class="batch-count">成功 {{ d.success_count }} / 失败 {{ d.failed_count }}</span>
                  </div>
                  <div class="batch-items">
                    <span v-for="it in d.items" :key="it.ad_id" class="batch-item">
                      <el-tag size="small" :type="it.status === 'success' ? 'success' : 'danger'" effect="plain">
                        {{ it.status === 'success' ? '成功' : '失败' }}
                      </el-tag>
                      {{ it.title || it.ad_id }}
                    </span>
                  </div>
                </el-timeline-item>
              </el-timeline>
            </el-card>
          </template>

          <!-- 平台详细介绍 -->
          <template v-else>
            <el-card class="gen-card" shadow="never">
              <template #header>平台详细介绍</template>
              <div class="detail-head">
                <PlatformLogo :logo-key="current.logo_key" :color="current.theme_color" :label="current.label" :size="52" />
                <div>
                  <div class="detail-title">{{ current.label }} · {{ current.belong }}</div>
                  <div class="detail-sub">{{ current.category }}</div>
                </div>
              </div>
              <p class="detail-text">{{ current.detail }}</p>
              <div class="detail-features">
                <span v-for="f in current.features" :key="f" class="feature-chip"
                  :style="{ color: current.theme_color, borderColor: current.theme_color + '55' }">
                  {{ f }}
                </span>
              </div>
              <el-divider>投放概况</el-divider>
              <div class="detail-stats">
                <div class="detail-stat"><b>{{ current.delivered_ad_count ?? 0 }}</b><span>已投放广告</span></div>
                <div class="detail-stat"><b>{{ current.delivery_batch_count ?? 0 }}</b><span>投放批次</span></div>
              </div>
            </el-card>
          </template>
        </div>
      </div>
    </template>

    <el-dialog v-model="storeDialog" title="添加店铺" width="520px" append-to-body>
      <el-form label-width="96px" :model="storeForm">
        <el-form-item label="店铺名称">
          <el-input v-model="storeForm.name" placeholder="如：抖音小店 312014721" />
        </el-form-item>
        <el-form-item label="店铺 ID" required>
          <el-input v-model="storeForm.shop_id" placeholder="如：312014721" />
        </el-form-item>
        <el-form-item label="抖音号">
          <el-input v-model="storeForm.douyin_account" placeholder="如：cisheng88193" />
        </el-form-item>
        <el-form-item label="广告主 ID">
          <el-input v-model="storeForm.advertiser_id" placeholder="千川广告主账户 ID（可选）" />
        </el-form-item>
        <el-form-item label="商品 ID">
          <el-input v-model="storeForm.products" placeholder="多个用逗号分隔，如：3848012001225867368" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="storeForm.note" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="storeDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingStore" @click="submitStore">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Promotion, ArrowRight, ArrowLeft, Refresh, Clock, Document, Delete,
  Plus, MagicStick, CircleCheck, CircleClose, Close,
} from '@element-plus/icons-vue'
import PlatformLogo from '@/components/PlatformLogo.vue'
import {
  listDeliveryPlatforms, getPlatformDetail, getPlatformAds, getPlatformDeliveries,
  submitDelivery, getDeliveryConfig, deletePlatformAd,
  listStores, createStore, deleteStore, preflightQianchuan,
} from '@/api'
import { formatTime, mediaUrl } from '@/utils/format'

const TYPE_META = {
  copy: { label: '文案广告', tagType: 'info' },
  image: { label: '图文广告', tagType: 'success' },
  video: { label: '视频广告', tagType: 'danger' },
}
const typeMeta = (type) => TYPE_META[type] || { label: type, tagType: 'info' }

const tabs = [
  { key: 'deliver', label: '广告投放', icon: Promotion },
  { key: 'history', label: '历史投放查看', icon: Clock },
  { key: 'detail', label: '平台详细介绍', icon: Document },
]

const platforms = ref([])
const current = ref(null)
const tab = ref('deliver')

const availableAds = ref([])
const historyAds = ref([])
const deliveries = ref([])
const selected = ref([])
const budget = ref(10000)
const budgetConfig = ref(null)
const submitting = ref(false)
const latest = ref(null)

// 店铺信息（按平台区分，落盘到 data/stores/<platform>.json）+ 模拟投递检测
const stores = ref([])
const storeDialog = ref(false)
const savingStore = ref(false)
const simulatingId = ref('')
const simulateResult = ref(null)
const storeForm = ref({ name: '', shop_id: '', douyin_account: '', advertiser_id: '', products: '', note: '' })

const themeStyle = computed(() =>
  current.value ? { background: current.value.bg_color, minHeight: '100%' } : {},
)
const heroStyle = computed(() => ({
  background: `linear-gradient(120deg, ${hexA(current.value.theme_color, 0.92)}, ${hexA(current.value.theme_color, 0.7)})`,
}))
const tagStyle = (p) => ({ color: p.theme_color, borderColor: p.theme_color + '66' })
const counts = computed(() => ({
  available: availableAds.value.length,
  history: historyAds.value.length,
}))

function hexA(hex, alpha) {
  const h = (hex || '#409eff').replace('#', '')
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  const r = parseInt(full.slice(0, 2), 16)
  const g = parseInt(full.slice(2, 4), 16)
  const b = parseInt(full.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

async function fetchPlatforms() {
  try {
    platforms.value = (await listDeliveryPlatforms()) || []
  } catch {
    platforms.value = []
  }
}

async function enterPlatform(p) {
  tab.value = 'deliver'
  latest.value = null
  selected.value = []
  const detail = await getPlatformDetail(p.value).catch(() => null)
  current.value = detail || p
  await loadPlatform()
}

function exitPlatform() {
  current.value = null
  latest.value = null
  selected.value = []
  stores.value = []
  simulateResult.value = null
}

async function loadPlatform() {
  if (!current.value) return
  simulateResult.value = null
  const [ads, dels, detail] = await Promise.all([
    getPlatformAds(current.value.value).catch(() => ({ available: [], history: [] })),
    getPlatformDeliveries(current.value.value).catch(() => []),
    getPlatformDetail(current.value.value).catch(() => null),
  ])
  availableAds.value = ads?.available || []
  historyAds.value = ads?.history || []
  deliveries.value = dels || []
  if (detail) current.value = detail
  await loadStores()
}

// ---------- 店铺信息 ----------
async function loadStores() {
  if (!current.value) return
  try {
    const res = await listStores(current.value.value)
    stores.value = res?.stores || []
  } catch {
    stores.value = []
  }
}

function openStoreDialog() {
  storeForm.value = { name: '', shop_id: '', douyin_account: '', advertiser_id: '', products: '', note: '' }
  storeDialog.value = true
}

async function submitStore() {
  if (!storeForm.value.shop_id.trim()) {
    ElMessage.warning('请填写店铺 ID')
    return
  }
  savingStore.value = true
  try {
    await createStore(current.value.value, { ...storeForm.value })
    ElMessage.success('店铺已添加')
    storeDialog.value = false
    await loadStores()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '添加店铺失败')
  } finally {
    savingStore.value = false
  }
}

async function handleDeleteStore(store) {
  try {
    await ElMessageBox.confirm(`确认删除店铺「${store.name}」？删除后不可恢复。`, '删除店铺', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteStore(current.value.value, store.id)
    ElMessage.success('店铺已删除')
    await loadStores()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '删除店铺失败')
  }
}

async function simulateStore(store) {
  if (current.value.value !== 'qianchuan') {
    ElMessage.info('该平台投递 Agent 尚未接入，暂仅支持「巨量千川（抖音）」模拟投递检测')
    return
  }
  const productId = store.products?.[0]?.product_id || ''
  if (!productId) {
    ElMessage.warning('该店铺未登记商品 ID，无法模拟投递')
    return
  }
  simulatingId.value = store.id
  simulateResult.value = null
  try {
    const res = await preflightQianchuan({
      shop_id: store.shop_id,
      product_id: productId,
      aweme_id: store.aweme_id || store.douyin_account,
    })
    simulateResult.value = res
    if (res?.ready) ElMessage.success('模拟投递检测通过：可以投递')
    else ElMessage.warning('模拟投递检测未通过，请查看明细')
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '模拟投递检测失败')
  } finally {
    simulatingId.value = ''
  }
}

function switchTab(key) {
  tab.value = key
}

function toggle(id) {
  const i = selected.value.indexOf(id)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(id)
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
  const amount = Number(budget.value)
  if (!amount || amount <= 0) {
    ElMessage.warning('请填写投放预算')
    return
  }
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
      platform: current.value.value,
      ad_ids: selected.value,
      budget: amount,
    })
    latest.value = res
    selected.value = []
    ElMessage.success(`已投放到「${res.platform_label}」，成功 ${res.success_count} 条`)
    await loadPlatform()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '投放失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

async function handleDelete(ad) {
  try {
    await ElMessageBox.confirm(`确认删除广告物料 ${ad.id} ？删除后不可恢复。`, '删除广告', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deletePlatformAd(current.value.value, ad.id)
    selected.value = selected.value.filter((id) => id !== ad.id)
    ElMessage.success('已删除该广告')
    await loadPlatform()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '删除失败，请稍后重试')
  }
}

onMounted(async () => {
  await Promise.all([fetchPlatforms(), fetchBudgetConfig()])
})
</script>

<style scoped>
.delivery { border-radius: 12px; padding: 4px; transition: background 0.3s ease; }

/* ===== 平台卡片（一级） ===== */
.platform-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 16px;
}
.platform-card {
  background: #fff;
  border: 1px solid var(--card-border);
  border-top: 4px solid var(--c);
  border-radius: 14px;
  padding: 20px 22px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.platform-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 14px 30px color-mix(in srgb, var(--c) 20%, transparent);
}
.platform-card-top { display: flex; align-items: center; gap: 14px; }
.platform-card-name { flex: 1; }
.platform-title { font-size: 17px; font-weight: 700; color: #1d2b4f; }
.platform-belong { font-size: 12px; color: #909399; margin-top: 2px; }
.platform-intro {
  font-size: 13px; color: #606266; line-height: 1.7; margin: 0;
  min-height: 66px;
}
.platform-features { display: flex; gap: 6px; flex-wrap: wrap; }
.feature-chip {
  font-size: 12px; padding: 2px 9px; border-radius: 10px;
  background: #f4f6fa; color: #5a6b8c; border: 1px solid transparent;
}
.platform-card-foot {
  display: flex; align-items: center; justify-content: space-between;
  color: var(--c); font-size: 13px; font-weight: 600;
  border-top: 1px dashed var(--card-border); padding-top: 12px;
}

/* ===== 平台投放台（二级） ===== */
.platform-hero {
  position: relative;
  overflow: hidden;
  border-radius: 14px;
  padding: 22px 26px;
  color: #fff;
  margin-bottom: 16px;
  box-shadow: 0 8px 24px rgba(29, 43, 79, 0.18);
}
.platform-hero .watermark {
  position: absolute;
  right: -20px;
  bottom: -40px;
  opacity: 0.12;
  pointer-events: none;
}
.back-btn {
  color: #fff !important;
  padding-left: 0;
  margin-bottom: 10px;
}
.platform-hero-main { display: flex; align-items: center; gap: 16px; position: relative; z-index: 1; }
.platform-hero-title { font-size: 22px; font-weight: 700; margin: 0 0 6px; }
.platform-hero-belong {
  font-size: 13px; font-weight: 500; opacity: 0.85;
  margin-left: 10px; padding: 2px 10px; border-radius: 10px;
  background: rgba(255, 255, 255, 0.18);
}
.platform-hero-desc { margin: 0; font-size: 13px; opacity: 0.92; max-width: 820px; line-height: 1.6; }

.platform-body { display: flex; gap: 16px; align-items: flex-start; }
.side-nav {
  flex: 0 0 200px;
  background: #fff;
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 10px;
  box-shadow: 0 4px 14px rgba(29, 43, 79, 0.06);
}
.side-item {
  display: flex; align-items: center; gap: 9px; padding: 11px 14px;
  border-radius: 10px; cursor: pointer; color: #5a6b8c; font-size: 14px;
  transition: background 0.2s ease, color 0.2s ease;
}
.side-item:hover { background: #f4f6fa; }
.side-item.is-active {
  background: color-mix(in srgb, var(--c) 12%, #fff);
  color: var(--c);
  font-weight: 600;
}
.side-stats {
  display: flex; gap: 6px; margin-top: 10px;
  border-top: 1px dashed var(--card-border); padding-top: 12px;
}
.side-stat { flex: 1; text-align: center; }
.side-stat b { display: block; font-size: 18px; color: #1d2b4f; }
.side-stat span { font-size: 11px; color: #909399; }

.platform-content { flex: 1; min-width: 0; }
.gen-card-head { display: flex; align-items: center; justify-content: space-between; }
.budget-hint { font-size: 12px; color: #909399; }

.ad-list { display: flex; flex-direction: column; gap: 8px; }
.ad-item {
  display: flex; align-items: center; gap: 12px;
  border: 1px solid var(--card-border); border-radius: 10px; padding: 10px 12px;
  cursor: pointer; transition: border-color 0.2s ease, background 0.2s ease; margin: 0;
}
.ad-item:hover { background: #fafbfd; }
.ad-item.is-checked { border-color: var(--c); background: color-mix(in srgb, var(--c) 7%, #fff); }
.ad-item.is-history { background: #fbfbff; }
.ad-main { flex: 1; min-width: 0; }
.ad-row { display: flex; align-items: center; gap: 8px; }
.ad-id { font-size: 12px; color: #909399; }
.ad-time { margin-left: auto; font-size: 12px; color: #909399; }
.ad-del { flex-shrink: 0; }
.ad-text {
  font-size: 13px; color: #4b5563; margin-top: 4px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ad-thumb {
  width: 60px; height: 60px; border-radius: 8px;
  border: 1px solid var(--card-border); flex-shrink: 0; background: #000;
}
.deliver-actions { display: flex; align-items: center; gap: 10px; margin-top: 16px; flex-wrap: wrap; }

.deliver-result {
  margin-top: 14px; border: 1px solid var(--card-border);
  border-left: 4px solid var(--c); border-radius: 10px; padding: 12px 14px;
  background: #fafbfd;
}
.deliver-result-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.deliver-time { margin-left: auto; font-size: 12px; color: #909399; }
.deliver-summary { font-size: 13px; color: #606266; }
.deliver-summary .ok { color: #67c23a; }
.deliver-summary .fail { color: #f56c6c; }

.batch-row { display: flex; align-items: center; gap: 10px; font-size: 13px; flex-wrap: wrap; }
.batch-budget { color: #2e4c86; }
.batch-count { margin-left: auto; color: #909399; font-size: 12px; }
.batch-items { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
.batch-item { font-size: 12px; color: #606266; display: inline-flex; align-items: center; gap: 4px; }

.detail-head { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; }
.detail-title { font-size: 17px; font-weight: 700; color: #1d2b4f; }
.detail-sub { font-size: 12px; color: #909399; margin-top: 3px; }
.detail-text { font-size: 14px; line-height: 1.9; color: #4b5563; margin: 0 0 14px; }
.detail-features { display: flex; gap: 8px; flex-wrap: wrap; }
.detail-features .feature-chip { background: #fff; border: 1px solid; }
.detail-stats { display: flex; gap: 32px; }
.detail-stat b { font-size: 24px; color: #1d2b4f; }
.detail-stat span { display: block; font-size: 12px; color: #909399; margin-top: 2px; }

/* ===== 店铺信息 ===== */
.store-card { margin-bottom: 16px; }
.store-head-actions { display: flex; gap: 8px; }
.store-list { display: flex; flex-direction: column; gap: 8px; }
.store-item {
  display: flex; align-items: center; gap: 12px;
  border: 1px solid var(--card-border); border-left: 3px solid var(--c);
  border-radius: 10px; padding: 10px 14px;
}
.store-main { flex: 1; min-width: 0; }
.store-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.store-name { font-size: 14px; font-weight: 600; color: #1d2b4f; }
.store-sub {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin-top: 4px; font-size: 12px; color: #909399;
}
.store-note { color: #b1b3b8; }
.store-actions { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }

.preflight-result {
  margin-top: 12px; border: 1px solid var(--card-border);
  border-left: 4px solid #67c23a; border-radius: 10px; padding: 12px 14px; background: #fafcf9;
}
.preflight-result.is-fail { border-left-color: #f56c6c; background: #fef8f8; }
.preflight-head { display: flex; align-items: center; gap: 10px; font-size: 13px; color: #606266; }
.preflight-close { margin-left: auto; }
.preflight-checks { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.preflight-checks li { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.preflight-checks li.ok { color: #529b2e; }
.preflight-checks li.fail { color: #c45656; }
.check-name { color: #4b5563; }
.check-detail { color: #909399; font-style: normal; font-size: 12px; }
.mono { font-family: Consolas, Monaco, monospace; font-size: 12px; color: #606266; }
</style>