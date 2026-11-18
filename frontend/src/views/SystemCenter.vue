<template>
  <div class="gen-page system">
    <!-- ===== 页头 ===== -->
    <div class="gen-hero">
      <span class="gen-hero-icon">
        <el-icon :size="24"><Monitor /></el-icon>
      </span>
      <div>
        <h1 class="gen-hero-title">系统中心</h1>
        <p class="gen-hero-desc">
          配置自检 · 质量评估 · 操作审计 · 素材版本 · 数据飞轮 · 运行指标 · 角色权限。
          这里回答「系统现在健康吗、生成质量在变好吗、谁改了什么、钱花在哪」。
        </p>
      </div>
      <el-button class="hero-reset" text :icon="Refresh" :loading="loading" @click="reloadAll">
        刷新
      </el-button>
    </div>

    <!-- ===== 概览卡片 ===== -->
    <div class="stat-grid">
      <div v-for="s in statCards" :key="s.key" class="stat-card" :style="{ '--c': s.color }">
        <span class="stat-icon"><el-icon :size="18"><component :is="s.icon" /></el-icon></span>
        <div>
          <div class="stat-value">{{ s.value }}</div>
          <div class="stat-label">{{ s.label }}</div>
        </div>
      </div>
    </div>

    <!-- ===== 页签 ===== -->
    <el-radio-group v-model="tab" class="sys-tabs" @change="onTabChange">
      <el-radio-button value="config">配置自检</el-radio-button>
      <el-radio-button value="quality">质量评估</el-radio-button>
      <el-radio-button value="metrics">运行指标</el-radio-button>
      <el-radio-button value="audit">操作审计</el-radio-button>
      <el-radio-button value="versions">素材版本</el-radio-button>
      <el-radio-button value="flywheel">数据飞轮</el-radio-button>
      <el-radio-button value="rbac">角色权限</el-radio-button>
    </el-radio-group>

    <!-- ============ 配置自检 ============ -->
    <el-card v-if="tab === 'config'" class="gen-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>启动配置自检</span>
          <span class="hint">
            对「静默降级」的防线：Key 未配置或模型名写错时，文案会悄悄退回模板，只有这里能一眼看出来
          </span>
        </div>
      </template>

      <el-alert
        v-if="configStatus && !configStatus.ok"
        type="error"
        :closable="false"
        show-icon
        :title="`发现 ${configStatus.errors.length} 项配置错误、${configStatus.warnings.length} 项警告`"
        style="margin-bottom: 14px"
      />
      <el-alert
        v-else
        type="success"
        :closable="false"
        show-icon
        title="配置检查通过"
        style="margin-bottom: 14px"
      />

      <el-table :data="configStatus?.items || []" size="small" style="width: 100%">
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="LEVEL_TYPE[row.level]" effect="dark" size="small">
              {{ LEVEL_LABEL[row.level] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="配置项" width="200" class-name="mono" />
        <el-table-column prop="value" label="当前值" min-width="200" class-name="mono" show-overflow-tooltip />
        <el-table-column prop="note" label="说明" min-width="280" show-overflow-tooltip />
      </el-table>

      <el-divider content-position="left">风控词表</el-divider>
      <div class="row-inline">
        <el-tag :type="bannedAvailable ? 'success' : 'danger'" effect="dark">
          {{ bannedAvailable ? '词表可用' : '词表不可用（风控已 fail-closed）' }}
        </el-tag>
        <span class="mono muted">
          违禁词 {{ reviewConfig?.banned_word_count ?? '-' }} 条 ·
          替换建议 {{ reviewConfig?.replacement_count ?? '-' }} 条
        </span>
        <el-button size="small" :icon="Refresh" @click="doReloadWords" :loading="reloadingWords">
          热加载词表
        </el-button>
      </div>
    </el-card>

    <!-- ============ 质量评估 ============ -->
    <template v-if="tab === 'quality'">
      <el-card class="gen-card" shadow="never">
        <template #header>
          <div class="card-head">
            <span>生成质量看板</span>
            <span class="hint">共 {{ quality?.total_records ?? 0 }} 条评估记录（每次生成自动评估，零额外成本）</span>
          </div>
        </template>

        <el-empty v-if="!quality?.total_records" description="还没有评估记录，去「广告生成」生成一条文案即可看到" />

        <template v-else>
          <div class="metric-row">
            <div class="metric-box">
              <div class="metric-num">{{ quality.summary.avg_score }}</div>
              <div class="metric-cap">平均质量分</div>
            </div>
            <div class="metric-box">
              <div class="metric-num">{{ quality.summary.min_score }} ~ {{ quality.summary.max_score }}</div>
              <div class="metric-cap">分数区间</div>
            </div>
            <div class="metric-box">
              <div class="metric-num">{{ percent(quality.summary.avg_sell_point_coverage) }}</div>
              <div class="metric-cap">平均卖点覆盖率</div>
            </div>
          </div>

          <el-divider content-position="left">指标通过率</el-divider>
          <div class="rate-list">
            <div v-for="(label, key) in RATE_LABELS" :key="key" class="rate-item">
              <span class="rate-name">{{ label }}</span>
              <el-progress
                :percentage="Math.round((quality.metric_rates[key] || 0) * 100)"
                :stroke-width="14"
                :color="rateColor(quality.metric_rates[key] || 0)"
              />
            </div>
          </div>

          <el-divider content-position="left">分数分布</el-divider>
          <div class="dist-list">
            <div v-for="(count, bucket) in quality.score_distribution" :key="bucket" class="dist-item">
              <span class="dist-name">{{ bucket }}</span>
              <div class="dist-bar">
                <div
                  class="dist-fill"
                  :style="{ width: distWidth(count), '--c': bucketColor(bucket) }"
                ></div>
              </div>
              <span class="dist-count">{{ count }}</span>
            </div>
          </div>
        </template>
      </el-card>

      <el-card v-if="quality?.low_score_samples?.length" class="gen-card" shadow="never">
        <template #header>
          <div class="card-head">
            <span>低分样本（用于定位问题）</span>
            <span class="hint">分数最低的 5 条，重点看「缺失卖点」列</span>
          </div>
        </template>
        <el-table :data="quality.low_score_samples" size="small">
          <el-table-column prop="score" label="分数" width="80" />
          <el-table-column prop="platform" label="平台" width="120" />
          <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip />
          <el-table-column label="缺失卖点" min-width="200">
            <template #default="{ row }">
              <span v-if="!row.missing?.length" class="muted">—</span>
              <el-tag v-for="m in row.missing" :key="m" size="small" type="warning" effect="plain" class="miss-tag">
                {{ m }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="at" label="时间" width="170" class-name="mono" />
        </el-table>
      </el-card>
    </template>

    <!-- ============ 运行指标 ============ -->
    <el-card v-if="tab === 'metrics'" class="gen-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>运行指标</span>
          <span class="hint">统计自 {{ metrics?.since || '-' }}；「降级次数」是识别静默故障的关键指标</span>
        </div>
      </template>

      <div class="metric-row">
        <div class="metric-box">
          <div class="metric-num">¥{{ (metrics?.cost_estimate ?? 0).toFixed(4) }}</div>
          <div class="metric-cap">估算成本</div>
        </div>
        <div class="metric-box">
          <div class="metric-num">{{ (metrics?.llm_tokens?.total ?? 0).toLocaleString() }}</div>
          <div class="metric-cap">LLM Token 总量</div>
        </div>
        <div class="metric-box">
          <div class="metric-num">{{ totalDegraded }}</div>
          <div class="metric-cap">降级次数</div>
        </div>
      </div>

      <el-table :data="capabilityRows" size="small" style="width: 100%">
        <el-table-column prop="name" label="能力" width="180" class-name="mono" />
        <el-table-column prop="calls" label="调用" width="80" />
        <el-table-column label="失败率" width="100">
          <template #default="{ row }">
            <span :class="{ 'is-bad': row.failure_rate > 0 }">{{ percent(row.failure_rate) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="降级率" width="100">
          <template #default="{ row }">
            <span :class="{ 'is-warn': row.degradation_rate > 0 }">{{ percent(row.degradation_rate) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="avg_ms" label="平均耗时(ms)" width="130" />
        <el-table-column prop="p95_ms" label="P95(ms)" width="110" />
        <el-table-column prop="max_ms" label="最大(ms)" width="110" />
      </el-table>

      <el-divider content-position="left">计数器</el-divider>
      <div class="chip-row">
        <el-tag v-for="(v, k) in metrics?.counters || {}" :key="k" effect="plain" class="mono">
          {{ k }}: {{ v }}
        </el-tag>
        <span v-if="!Object.keys(metrics?.counters || {}).length" class="muted">暂无计数</span>
      </div>
    </el-card>

    <!-- ============ 操作审计 ============ -->
    <el-card v-if="tab === 'audit'" class="gen-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>操作审计日志</span>
          <span class="hint">{{ auditStats?.total ?? 0 }} 条记录 · 「谁、何时、对哪条素材做了什么、改前改后是什么」</span>
        </div>
      </template>

      <div class="filter-row">
        <el-input v-model="auditFilter.action" placeholder="按动作过滤，如 review.approve" clearable style="width: 260px" />
        <el-input v-model="auditFilter.actor" placeholder="按操作人过滤" clearable style="width: 180px" />
        <el-button :icon="Search" @click="loadAudit">查询</el-button>
        <el-tag v-if="auditForbidden" type="warning" effect="plain">当前角色无权查看审计（需管理员）</el-tag>
      </div>

      <el-table :data="auditRows" size="small" v-loading="auditLoading" style="width: 100%">
        <el-table-column prop="ts" label="时间" width="170" class-name="mono" />
        <el-table-column prop="action" label="动作" width="200" class-name="mono" />
        <el-table-column prop="actor_display" label="操作人" width="120" />
        <el-table-column prop="actor_role" label="角色" width="90" />
        <el-table-column prop="target" label="对象" width="200" class-name="mono" show-overflow-tooltip />
        <el-table-column label="变更" min-width="260">
          <template #default="{ row }">
            <span v-if="!row.changes" class="muted">{{ row.detail ? JSON.stringify(row.detail).slice(0, 80) : '—' }}</span>
            <span v-else class="mono changes">
              <span v-for="(v, k) in row.changes" :key="k" class="change-item">
                {{ k }}: <del>{{ brief(v.before) }}</del> → <ins>{{ brief(v.after) }}</ins>
              </span>
            </span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ============ 素材版本 ============ -->
    <el-card v-if="tab === 'versions'" class="gen-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>素材版本管理</span>
          <span class="hint">合规审计需要「谁在什么时候把哪个词改成了什么」</span>
        </div>
      </template>

      <div class="filter-row">
        <el-select v-model="versionAdId" filterable placeholder="选择素材（广告编号）" style="width: 380px" @change="loadVersions">
          <el-option v-for="ad in ads" :key="ad.id" :label="`${ad.id} · ${ad.title || '(无标题)'}`" :value="ad.id" />
        </el-select>
        <el-button :icon="Refresh" @click="loadVersions" :disabled="!versionAdId">加载</el-button>
      </div>

      <el-empty v-if="!versionAdId" description="请选择一条素材查看版本链" />
      <template v-else>
        <el-timeline v-if="versionChain.length" class="ver-timeline">
          <el-timeline-item
            v-for="v in versionChain"
            :key="v.version"
            :timestamp="`${v.at} · ${v.actor || '未知'}`"
            :type="v.version === versionChain.length ? 'primary' : ''"
          >
            <div class="ver-head">
              <b>v{{ v.version }}</b>
              <span class="muted">{{ v.note || '—' }}</span>
            </div>
            <div class="ver-title">{{ v.title || '(无标题)' }}</div>
            <div class="ver-content">{{ v.content }}</div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="该素材暂无版本记录" />

        <template v-if="versionDiff?.available">
          <el-divider content-position="left">
            差异对比 v{{ versionDiff.from?.version }} → v{{ versionDiff.to?.version }}
            （+{{ versionDiff.added }} / -{{ versionDiff.removed }}）
          </el-divider>
          <pre class="diff-view"><code><span
            v-for="(line, i) in versionDiff.lines" :key="i"
            :class="diffLineClass(line)"
          >{{ line }}
</span></code></pre>
        </template>
      </template>
    </el-card>

    <!-- ============ 数据飞轮 ============ -->
    <el-card v-if="tab === 'flywheel'" class="gen-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>数据飞轮</span>
          <span class="hint">人工修正并通过的文案会回写向量库，成为下次生成的检索参考——系统越用越准</span>
        </div>
      </template>

      <div class="metric-row">
        <div class="metric-box">
          <div class="metric-num">{{ flywheel?.total_samples ?? 0 }}</div>
          <div class="metric-cap">已回流样本</div>
        </div>
        <div class="metric-box">
          <div class="metric-num">{{ flywheel?.pending_flush ?? 0 }}</div>
          <div class="metric-cap">待补写向量库</div>
        </div>
        <div class="metric-box">
          <div class="metric-num">{{ flywheel?.flushed ?? 0 }}</div>
          <div class="metric-cap">已补写</div>
        </div>
      </div>

      <div class="row-inline">
        <el-button :icon="Refresh" @click="doFlush" :loading="flushing" :disabled="!flywheel?.pending_flush">
          补写向量库
        </el-button>
        <span class="muted">
          Milvus 不可用时样本会先入队（不丢数据），恢复后点这里补写
        </span>
      </div>

      <el-divider content-position="left">最近回流样本</el-divider>
      <el-table :data="flywheelSamples" size="small" style="width: 100%">
        <el-table-column prop="at" label="时间" width="170" class-name="mono" />
        <el-table-column prop="ad_id" label="素材" width="200" class-name="mono" />
        <el-table-column prop="category" label="类目" width="110" />
        <el-table-column prop="actor" label="操作人" width="120" />
        <el-table-column prop="mode" label="写入" width="100">
          <template #default="{ row }">
            <el-tag :type="row.mode === 'milvus' ? 'success' : 'warning'" size="small" effect="plain">
              {{ row.mode === 'milvus' ? '已入库' : '入队待写' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="text" label="内容" min-width="260" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- ============ 角色权限 ============ -->
    <template v-if="tab === 'rbac'">
      <el-card class="gen-card" shadow="never">
        <template #header>
          <div class="card-head">
            <span>当前登录角色</span>
            <span class="hint">
              {{ rbac?.current?.display_name || '未登录' }} ·
              {{ rbac?.current?.role || '未设置角色' }}
            </span>
          </div>
        </template>
        <div class="chip-row">
          <el-tag
            v-for="p in rbac?.current_permissions || []"
            :key="p" type="success" effect="plain" class="mono"
          >{{ p }}</el-tag>
          <span v-if="!rbac?.current_permissions?.length" class="muted">当前角色没有任何权限</span>
        </div>
        <el-alert
          v-if="!rbac?.current_permissions?.length"
          type="warning" :closable="false" show-icon style="margin-top: 12px"
          title="当前账号角色不在权限矩阵中，审批类操作会被拒绝（403）"
          description="可让管理员把该账号的角色改为「运营 / 审查 / 审批 / 管理员」之一。"
        />
      </el-card>

      <el-card class="gen-card" shadow="never">
        <template #header>
          <div class="card-head">
            <span>角色 - 权限矩阵</span>
            <span class="hint">职责分离：生成、审查、审批由不同角色承担</span>
          </div>
        </template>
        <el-table :data="rbac?.roles || []" size="small" style="width: 100%">
          <el-table-column prop="label" label="角色" width="160" />
          <el-table-column label="权限" min-width="300">
            <template #default="{ row }">
              <el-tag v-for="p in row.permissions" :key="p" size="small" effect="plain" class="mono perm-tag">
                {{ p }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="可自审" width="110">
            <template #default="{ row }">
              <el-tag :type="row.self_review_exempt ? 'warning' : 'success'" size="small" effect="plain">
                {{ row.self_review_exempt ? '豁免' : '禁止' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>

        <el-divider content-position="left">职责分离规则</el-divider>
        <ul class="rule-list">
          <li v-for="(r, i) in rbac?.rules || []" :key="i">{{ r }}</li>
        </ul>
      </el-card>

      <el-card class="gen-card" shadow="never">
        <template #header>审批 SLA（待办积压与超时）</template>
        <div class="metric-row">
          <div class="metric-box">
            <div class="metric-num">{{ sla?.pending_total ?? 0 }}</div>
            <div class="metric-cap">待审批</div>
          </div>
          <div class="metric-box">
            <div class="metric-num" :class="{ 'is-bad': sla?.pending_overdue > 0 }">{{ sla?.pending_overdue ?? 0 }}</div>
            <div class="metric-cap">超时(>{{ sla?.sla_hours ?? 24 }}h)</div>
          </div>
          <div class="metric-box">
            <div class="metric-num">{{ sla?.avg_waiting_hours ?? 0 }} h</div>
            <div class="metric-cap">平均等待</div>
          </div>
          <div class="metric-box">
            <div class="metric-num">{{ sla?.max_waiting_hours ?? 0 }} h</div>
            <div class="metric-cap">最长等待</div>
          </div>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Monitor, Refresh, Search, Bell, TrendCharts, Document, Coin,
} from '@element-plus/icons-vue'
import {
  getConfigStatus, getMetrics, listAuditLog, getAuditStats,
  getQualityDashboard, getVersions, getVersionDiff,
  getFlywheel, getFlywheelSamples, flushFlywheel,
  getRbac, getReviewConfig, getReviewSla, reloadBannedWords,
  listAds,
} from '@/api'

const LEVEL_TYPE = { ok: 'success', warn: 'warning', error: 'danger' }
const LEVEL_LABEL = { ok: '正常', warn: '警告', error: '错误' }
const RATE_LABELS = {
  format_ok_rate: '格式完整率（标题/正文/标签齐全）',
  violation_free_rate: '合规通过率（零违禁词）',
  length_ok_rate: '长度合规率',
  tag_count_ok_rate: '标签数量合规率',
}

const tab = ref('config')
const loading = ref(false)

const configStatus = ref(null)
const metrics = ref(null)
const quality = ref(null)
const auditRows = ref([])
const auditStats = ref(null)
const auditForbidden = ref(false)
const auditLoading = ref(false)
const auditFilter = reactive({ action: '', actor: '' })
const ads = ref([])
const versionAdId = ref('')
const versionChain = ref([])
const versionDiff = ref(null)
const flywheel = ref(null)
const flywheelSamples = ref([])
const flushing = ref(false)
const rbac = ref(null)
const sla = ref(null)
const reviewConfig = ref(null)
const reloadingWords = ref(false)

const bannedAvailable = computed(() => !!reviewConfig.value?.banned_word_available)

const statCards = computed(() => [
  { key: 'quality', label: '平均质量分', value: quality.value?.summary?.avg_score ?? '—', icon: TrendCharts, color: '#409eff' },
  { key: 'cost', label: '估算成本(元)', value: (metrics.value?.cost_estimate ?? 0).toFixed(4), icon: Coin, color: '#67c23a' },
  { key: 'degraded', label: '降级次数', value: totalDegraded.value, icon: Document, color: '#e6a23c' },
  { key: 'pending', label: '待审批', value: sla.value?.pending_total ?? 0, icon: Bell, color: '#9b8cff' },
])

const totalDegraded = computed(() => {
  const caps = metrics.value?.capabilities || {}
  return Object.values(caps).reduce((sum, c) => sum + (c.degraded || 0), 0)
})

const capabilityRows = computed(() => {
  const caps = metrics.value?.capabilities || {}
  return Object.entries(caps).map(([name, c]) => ({ name, ...c }))
})

const percent = (v) => `${Math.round((Number(v) || 0) * 100)}%`
const brief = (v) => {
  if (v === null || v === undefined) return '—'
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s.length > 40 ? `${s.slice(0, 40)}…` : s
}
const rateColor = (v) => (v >= 0.9 ? '#67c23a' : v >= 0.7 ? '#e6a23c' : '#f56c6c')
const bucketColor = (b) => ({ '<60': '#f56c6c', '60-69': '#e6a23c', '70-79': '#409eff', '80-89': '#67c23a', '90-100': '#529b2e' }[b] || '#409eff')
const distWidth = (count) => {
  const buckets = quality.value?.score_distribution || {}
  const max = Math.max(1, ...Object.values(buckets))
  return `${Math.round((count / max) * 100)}%`
}
const diffLineClass = (line) => {
  if (line.startsWith('+++') || line.startsWith('---')) return 'diff-meta'
  if (line.startsWith('@@')) return 'diff-hunk'
  if (line.startsWith('+')) return 'diff-add'
  if (line.startsWith('-')) return 'diff-del'
  return ''
}

async function reloadAll() {
  loading.value = true
  try {
    const [cfg, m, q, fw, rb, rc, s] = await Promise.allSettled([
      getConfigStatus(), getMetrics(), getQualityDashboard(),
      getFlywheel(), getRbac(), getReviewConfig(), getReviewSla(),
    ])
    if (cfg.status === 'fulfilled') configStatus.value = cfg.value
    if (m.status === 'fulfilled') metrics.value = m.value
    if (q.status === 'fulfilled') quality.value = q.value
    if (fw.status === 'fulfilled') flywheel.value = fw.value
    if (rb.status === 'fulfilled') rbac.value = rb.value
    if (rc.status === 'fulfilled') reviewConfig.value = rc.value
    if (s.status === 'fulfilled') sla.value = s.value
  } finally {
    loading.value = false
  }
}

async function loadAudit() {
  auditLoading.value = true
  try {
    const params = { limit: 200 }
    if (auditFilter.action) params.action = auditFilter.action
    if (auditFilter.actor) params.actor = auditFilter.actor
    auditRows.value = await listAuditLog(params)
    auditForbidden.value = false
    auditStats.value = await getAuditStats().catch(() => null)
  } catch (e) {
    if (e?.response?.status === 403) {
      auditForbidden.value = true
      auditRows.value = []
    }
  } finally {
    auditLoading.value = false
  }
}

async function loadVersions() {
  if (!versionAdId.value) return
  try {
    const res = await getVersions(versionAdId.value)
    versionChain.value = res.versions || []
    versionDiff.value = await getVersionDiff(versionAdId.value).catch(() => null)
  } catch {
    versionChain.value = []
    versionDiff.value = null
  }
}

async function loadFlywheel() {
  try {
    flywheel.value = await getFlywheel()
    flywheelSamples.value = await getFlywheelSamples({ limit: 50 })
  } catch {
    /* ignore */
  }
}

async function doFlush() {
  flushing.value = true
  try {
    const res = await flushFlywheel()
    ElMessage.success(`补写完成：成功 ${res.succeeded} / 尝试 ${res.attempted}`)
    await loadFlywheel()
  } catch (e) {
    ElMessage.error(e?.response?.data?.msg || e?.response?.data?.detail || '补写失败')
  } finally {
    flushing.value = false
  }
}

async function doReloadWords() {
  reloadingWords.value = true
  try {
    const res = await reloadBannedWords()
    ElMessage.success(`词表已热加载：${res.word_count} 条（替换建议 ${res.replacement_count} 条）`)
    reviewConfig.value = await getReviewConfig()
    configStatus.value = await getConfigStatus()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '热加载失败（需要管理员权限）')
  } finally {
    reloadingWords.value = false
  }
}

function onTabChange(name) {
  if (name === 'audit') loadAudit()
  if (name === 'flywheel') loadFlywheel()
  if (name === 'versions' && !ads.value.length) {
    listAds().then((rows) => { ads.value = rows || [] }).catch(() => {})
  }
}

onMounted(async () => {
  await reloadAll()
  try {
    ads.value = await listAds()
  } catch {
    /* ignore */
  }
})
</script>

<style scoped>
.system { padding-bottom: 24px; }

.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}
.stat-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid var(--card-border);
  border-left: 4px solid var(--c);
}
.stat-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--c) 14%, transparent);
  color: var(--c);
}
.stat-value { font-size: 20px; font-weight: 700; color: #1d2b4f; line-height: 1.2; }
.stat-label { font-size: 12px; color: #909399; }

.sys-tabs { margin-bottom: 16px; flex-wrap: wrap; }

.card-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.card-head .hint { font-size: 12px; color: #909399; font-weight: 400; }

.row-inline { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.filter-row { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.chip-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.muted { color: #909399; font-size: 12px; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; }

.metric-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 14px;
  margin-bottom: 8px;
}
.metric-box {
  padding: 14px;
  border-radius: 10px;
  background: #f7f9fc;
  text-align: center;
}
.metric-num { font-size: 20px; font-weight: 700; color: #1d2b4f; }
.metric-cap { font-size: 12px; color: #909399; margin-top: 4px; }

.rate-list { display: flex; flex-direction: column; gap: 12px; }
.rate-item { display: grid; grid-template-columns: 260px 1fr; align-items: center; gap: 12px; }
.rate-name { font-size: 13px; color: #606266; }

.dist-list { display: flex; flex-direction: column; gap: 10px; }
.dist-item { display: grid; grid-template-columns: 70px 1fr 50px; align-items: center; gap: 12px; }
.dist-name { font-size: 13px; color: #606266; }
.dist-bar { height: 14px; background: #f2f3f5; border-radius: 7px; overflow: hidden; }
.dist-fill { height: 100%; background: var(--c); border-radius: 7px; transition: width 0.4s ease; }
.dist-count { font-size: 13px; color: #909399; text-align: right; }

.miss-tag { margin-right: 4px; }
.is-bad { color: #f56c6c; font-weight: 600; }
.is-warn { color: #e6a23c; font-weight: 600; }

.changes { font-size: 12px; }
.change-item { display: inline-block; margin-right: 10px; }
.change-item del { color: #f56c6c; text-decoration: line-through; }
.change-item ins { color: #67c23a; text-decoration: none; }

.ver-timeline { padding-left: 4px; }
.ver-head { display: flex; align-items: baseline; gap: 10px; }
.ver-title { font-weight: 600; color: #1d2b4f; margin: 4px 0; }
.ver-content {
  font-size: 13px;
  color: #606266;
  white-space: pre-wrap;
  max-height: 120px;
  overflow: auto;
  background: #f7f9fc;
  padding: 8px 10px;
  border-radius: 8px;
}

.diff-view {
  background: #1d2b4f;
  color: #d7e0f2;
  padding: 14px 16px;
  border-radius: 10px;
  font-size: 12.5px;
  line-height: 1.7;
  overflow-x: auto;
  margin: 0;
}
.diff-add { color: #85ce61; background: rgba(103, 194, 58, 0.14); display: block; }
.diff-del { color: #f89898; background: rgba(245, 108, 108, 0.14); display: block; }
.diff-hunk { color: #79bbff; display: block; }
.diff-meta { color: #a9b8d4; display: block; }

.perm-tag { margin: 0 4px 4px 0; }
.rule-list { margin: 0; padding-left: 20px; color: #606266; font-size: 13px; line-height: 2; }

/* 移动端适配：窄屏下指标卡与表单自动堆叠 */
@media (max-width: 768px) {
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
  .metric-row { grid-template-columns: repeat(2, 1fr); }
  .rate-item { grid-template-columns: 1fr; gap: 6px; }
  .filter-row :deep(.el-input) { width: 100% !important; }
  .sys-tabs :deep(.el-radio-button__inner) { padding: 8px 10px; font-size: 12px; }
}
</style>
