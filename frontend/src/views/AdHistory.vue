<template>
  <div class="gen-page">
    <!-- ===== 页头 ===== -->
    <div class="gen-hero">
      <span class="gen-hero-icon">
        <el-icon :size="24"><Clock /></el-icon>
      </span>
      <div>
        <h1 class="gen-hero-title">历史广告发售</h1>
        <p class="gen-hero-desc">
          按时间归档查看历史发售的广告物料（文案 / 图文 / 视频），含生成提示词与输入商品图片。
        </p>
      </div>
    </div>

    <!-- ===== 筛选栏 ===== -->
    <div class="history-toolbar">
      <el-radio-group v-model="typeFilter">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="copy">文案广告</el-radio-button>
        <el-radio-button value="image">图文广告</el-radio-button>
        <el-radio-button value="video">视频广告</el-radio-button>
      </el-radio-group>
      <el-button :icon="Refresh" :loading="loading" @click="fetchAds">刷新</el-button>
    </div>

    <el-empty
      v-if="!loading && groups.length === 0"
      description="暂无已发售的广告物料，可前往「文案 / 图文 / 视频生成」页面创作"
    />

    <!-- ===== 按时间分组 ===== -->
    <div v-for="group in groups" :key="group.label" class="history-group">
      <div class="history-group-head">
        <span class="history-date">{{ group.label }}</span>
        <span class="history-count">共 {{ group.items.length }} 条</span>
      </div>

      <div class="history-list">
        <div v-for="ad in group.items" :key="ad.id" class="history-item">
          <div class="history-item-head">
            <el-tag size="small" :type="typeMeta(ad.type).tagType" effect="plain">
              {{ typeMeta(ad.type).label }}
            </el-tag>
            <span class="history-id">{{ ad.id }}</span>
            <span class="history-time">{{ formatTime(ad.created_at) }}</span>
          </div>

          <h4 v-if="ad.title" class="history-title">{{ ad.title }}</h4>
          <div class="history-prompt">
            <span class="history-prompt-label">提示词</span>{{ ad.prompt || '—' }}
          </div>

          <div class="history-body">
            <div v-if="ad.type === 'copy'" class="history-copy">{{ ad.content || '—' }}</div>
            <template v-else>
              <el-image
                v-if="ad.type === 'image'"
                class="history-media"
                :src="mediaUrl(ad.result_url)"
                :preview-src-list="[mediaUrl(ad.result_url)]"
                fit="cover"
                hide-on-click-modal
              />
              <video
                v-else
                class="history-media"
                :src="mediaUrl(ad.result_url)"
                controls
                preload="metadata"
              ></video>

              <div v-if="ad.input_image" class="history-input">
                <span class="history-input-label">输入图片</span>
                <el-image
                  style="width: 88px; height: 88px; border-radius: 8px"
                  :src="mediaUrl(ad.input_image)"
                  fit="cover"
                  :preview-src-list="[mediaUrl(ad.input_image)]"
                  hide-on-click-modal
                />
              </div>
            </template>
          </div>

          <div class="history-meta">
            <el-tag v-if="ad.platform" size="small" effect="plain">
              {{ PLATFORM_META[ad.platform] || ad.platform }}
            </el-tag>
            <el-tag size="small" type="success" effect="plain">已投放</el-tag>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Clock, Refresh } from '@element-plus/icons-vue'
import { listAds } from '@/api'
import { formatTime, mediaUrl, PLATFORM_META } from '@/utils/format'

const TYPE_META = {
  copy: { label: '文案广告', tagType: 'info' },
  image: { label: '图文广告', tagType: 'success' },
  video: { label: '视频广告', tagType: 'danger' },
}
const typeMeta = (type) => TYPE_META[type] || { label: type, tagType: 'info' }

const ads = ref([])
const loading = ref(false)
const typeFilter = ref('all')

async function fetchAds() {
  loading.value = true
  try {
    ads.value = await listAds()
  } catch {
    ads.value = []
  } finally {
    loading.value = false
  }
}

const filtered = computed(() => {
  // 仅展示已投放（stage=delivered）的广告物料
  const delivered = ads.value.filter((ad) => ad.stage === 'delivered')
  return typeFilter.value === 'all'
    ? delivered
    : delivered.filter((ad) => ad.type === typeFilter.value)
})

/** 把时间归类为「今天 / 昨天 / YYYY-MM-DD」 */
function dateLabel(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '未知时间'
  const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diffDays = Math.round((startOf(new Date()) - startOf(d)) / 86400000)
  if (diffDays === 0) return '今天'
  if (diffDays === 1) return '昨天'
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

/** 按日期分组（列表本身已按时间倒序） */
const groups = computed(() => {
  const map = new Map()
  filtered.value.forEach((ad) => {
    const label = dateLabel(ad.created_at)
    if (!map.has(label)) map.set(label, [])
    map.get(label).push(ad)
  })
  return [...map.entries()].map(([label, items]) => ({ label, items }))
})

onMounted(fetchAds)
</script>

<style scoped>
.history-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 12px;
  flex-wrap: wrap;
}

.history-group {
  margin-bottom: 22px;
}
.history-group-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding-bottom: 8px;
  margin-bottom: 12px;
  border-bottom: 2px solid var(--card-border);
}
.history-date {
  font-size: 16px;
  font-weight: 600;
  color: #1d2b4f;
}
.history-count {
  font-size: 12px;
  color: #909399;
}

.history-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}
.history-item {
  border: 1px solid var(--card-border);
  border-radius: 12px;
  padding: 14px 16px;
  background: #fff;
  transition: box-shadow 0.2s ease;
}
.history-item:hover {
  box-shadow: 0 4px 16px rgba(29, 43, 79, 0.1);
}
.history-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.history-id {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  color: #909399;
}
.history-time {
  margin-left: auto;
  font-size: 12px;
  color: #909399;
}
.history-title {
  margin: 0 0 8px;
  font-size: 15px;
  color: #303133;
  line-height: 1.4;
}
.history-prompt {
  font-size: 13px;
  color: #606266;
  line-height: 1.7;
  background: #f7f9fc;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
}
.history-prompt-label {
  display: inline-block;
  font-size: 11px;
  color: #fff;
  background: #a0aec0;
  border-radius: 4px;
  padding: 1px 6px;
  margin-right: 8px;
}
.history-body {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  flex-wrap: wrap;
}
.history-copy {
  font-size: 13px;
  line-height: 1.8;
  color: #4b5563;
  white-space: pre-wrap;
  word-break: break-word;
  width: 100%;
}
.history-media {
  width: 160px;
  max-width: 100%;
  border-radius: 8px;
  border: 1px solid var(--card-border);
  display: block;
  background: #000;
}
.history-input {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.history-input-label {
  font-size: 11px;
  color: #909399;
}
.history-meta {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>