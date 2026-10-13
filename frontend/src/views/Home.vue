<template>
  <div class="home-page">
    <!-- ===== 动态主视觉（纯 CSS/HTML 自绘） ===== -->
    <div class="hero">
      <div class="hero-grid"></div>
      <div class="hero-orbs">
        <span v-for="n in 14" :key="n" class="orb" :style="orbStyle(n)"></span>
      </div>

      <div class="hero-main">
        <h1 class="hero-title">
          电商多智能体<span class="accent">运营驾驶舱</span>
        </h1>
        <p class="hero-sub">
          一条广告，从商品到投放，全流程自动生成 · 审查 · 审批 · 分发
        </p>

        <!-- 流水线动画：生成 → 审查 → 审批 → 投放 -->
        <div class="pipeline">
          <div
            v-for="(s, i) in stages"
            :key="s.key"
            class="pipe-node"
            :style="{ animationDelay: `${i * 0.35}s` }"
          >
            <span class="pipe-icon" :style="{ '--c': s.color }">
              <el-icon :size="18"><component :is="s.icon" /></el-icon>
            </span>
            <span class="pipe-label">{{ s.label }}</span>
          </div>
          <span class="pipe-track"><span class="pipe-spark"></span></span>
        </div>

        <div class="hero-cta">
          <el-button type="primary" size="large" :icon="MagicStick" @click="router.push('/adgen')">
            开始生成广告
          </el-button>
          <el-button size="large" :icon="Promotion" @click="router.push('/delivery')">
            进入广告投放
          </el-button>
        </div>
      </div>
    </div>

    <!-- ===== 概览指标 ===== -->
    <div class="metrics">
      <div v-for="m in metricCards" :key="m.key" class="metric-card" :style="{ '--c': m.color }">
        <span class="metric-icon"><el-icon :size="20"><component :is="m.icon" /></el-icon></span>
        <div class="metric-body">
          <div class="metric-value">
            <span v-if="loading" class="metric-skeleton"></span>
            <template v-else>{{ m.value }}</template>
          </div>
          <div class="metric-label">{{ m.label }}</div>
        </div>
      </div>
    </div>

    <!-- ===== 快捷入口 ===== -->
    <div class="quick">
      <div v-for="q in quickLinks" :key="q.path" class="quick-card" @click="router.push(q.path)">
        <span class="quick-icon" :style="{ '--c': q.color }">
          <el-icon :size="22"><component :is="q.icon" /></el-icon>
        </span>
        <div class="quick-body">
          <div class="quick-title">{{ q.title }}</div>
          <div class="quick-desc">{{ q.desc }}</div>
        </div>
        <el-icon class="quick-arrow"><ArrowRight /></el-icon>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  MagicStick, Promotion, EditPen, PictureFilled, VideoCameraFilled,
  Stamp, Clock, Bell, ArrowRight, DataLine,
} from '@element-plus/icons-vue'
import { getReviewStats, listHistory, listAds } from '@/api'

const router = useRouter()

const stages = [
  { key: 'gen', label: '生成', icon: MagicStick, color: '#409eff' },
  { key: 'review', label: '审查', icon: Stamp, color: '#e6a23c' },
  { key: 'approve', label: '审批', icon: Bell, color: '#9b8cff' },
  { key: 'deliver', label: '投放', icon: Promotion, color: '#67c23a' },
]

const orbStyle = (n) => {
  const left = (n * 37) % 100
  const size = 3 + (n % 4)
  const delay = (n % 7) * 0.7
  const dur = 6 + (n % 5)
  return {
    left: `${left}%`,
    width: `${size}px`,
    height: `${size}px`,
    animationDelay: `${delay}s`,
    animationDuration: `${dur}s`,
  }
}

const loading = ref(true)
const stats = ref({})
const historyCount = ref(0)
const adCount = ref(0)

const metricCards = computed(() => [
  { key: 'pending', label: '待审批广告', value: stats.value.pending ?? 0, color: '#e6a23c', icon: Bell },
  { key: 'approved', label: '已审批可投放', value: stats.value.approved ?? 0, color: '#409eff', icon: Promotion },
  { key: 'delivered', label: '已投放广告', value: stats.value.delivered ?? 0, color: '#67c23a', icon: Clock },
  { key: 'history', label: '生成历史记录', value: historyCount.value, color: '#9b8cff', icon: DataLine },
  { key: 'ads', label: '广告物料总数', value: adCount.value, color: '#ff8f5a', icon: EditPen },
])

const quickLinks = [
  { path: '/adgen', title: '广告生成', desc: '选商品 → 文案 → 图文 / 视频', icon: MagicStick, color: '#409eff' },
  { path: '/delivery', title: '广告投放', desc: '巨量千川 · 聚光 · 阿里妈妈 · 多多进宝', icon: Promotion, color: '#67c23a' },
  { path: '/history', title: '历史广告发售', desc: '按时间回看已投放广告', icon: Clock, color: '#e6a23c' },
  { path: '/approvals', title: '审查中心', desc: '违禁词 / 预算审查与审批处置', icon: Stamp, color: '#f56c6c' },
]

onMounted(async () => {
  try {
    const [s, h, a] = await Promise.allSettled([getReviewStats(), listHistory(), listAds()])
    if (s.status === 'fulfilled') stats.value = s.value || {}
    if (h.status === 'fulfilled') historyCount.value = (h.value || []).length
    if (a.status === 'fulfilled') adCount.value = (a.value || []).length
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.home-page {
  max-width: 1320px;
  margin: 0 auto;
}

/* ===== 主视觉 ===== */
.hero {
  position: relative;
  overflow: hidden;
  border-radius: 18px;
  padding: 40px 40px 34px;
  color: #fff;
  background: radial-gradient(circle at 15% 0%, #2b3f6e 0%, #1b2643 45%, #141c33 100%);
  box-shadow: 0 12px 34px rgba(20, 28, 51, 0.34);
}
.hero-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.05) 1px, transparent 1px);
  background-size: 34px 34px;
  mask-image: radial-gradient(circle at 30% 20%, #000 0%, transparent 78%);
  animation: grid-drift 22s linear infinite;
}
@keyframes grid-drift {
  to { background-position: 34px 34px; }
}
.hero-orbs { position: absolute; inset: 0; pointer-events: none; }
.orb {
  position: absolute;
  bottom: -12px;
  border-radius: 50%;
  background: #79bbff;
  opacity: 0.5;
  animation-name: float-up;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
}
@keyframes float-up {
  0% { transform: translateY(0); opacity: 0; }
  15% { opacity: 0.6; }
  100% { transform: translateY(-320px); opacity: 0; }
}

.hero-main { position: relative; z-index: 2; }
.hero-title {
  font-size: 30px;
  font-weight: 700;
  margin: 0 0 10px;
  letter-spacing: 1px;
}
.hero-title .accent {
  background: linear-gradient(90deg, #79bbff, #67c23a);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.hero-sub {
  margin: 0 0 26px;
  font-size: 14px;
  color: #a9b8d4;
  letter-spacing: 0.5px;
}

/* 流水线动画 */
.pipeline {
  position: relative;
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 30px;
  flex-wrap: wrap;
}
.pipe-node {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 16px;
  border-radius: 30px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.14);
  backdrop-filter: blur(4px);
  animation: node-glow 3s ease-in-out infinite;
}
@keyframes node-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(121, 187, 255, 0); }
  50% { box-shadow: 0 0 0 6px rgba(121, 187, 255, 0.12); }
}
.pipe-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--c) 26%, transparent);
  color: var(--c);
}
.pipe-label { font-size: 14px; font-weight: 600; }
.pipe-track {
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  height: 2px;
  background: linear-gradient(90deg, transparent, rgba(121, 187, 255, 0.35), transparent);
  z-index: 1;
}
.pipe-spark {
  position: absolute;
  top: -2px;
  width: 26px;
  height: 6px;
  border-radius: 4px;
  background: linear-gradient(90deg, transparent, #79bbff, transparent);
  animation: spark-move 3.2s ease-in-out infinite;
}
@keyframes spark-move {
  0% { left: -4%; opacity: 0; }
  20% { opacity: 1; }
  80% { opacity: 1; }
  100% { left: 96%; opacity: 0; }
}

.hero-cta { display: flex; gap: 12px; flex-wrap: wrap; }

/* ===== 概览指标 ===== */
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 14px;
  margin-top: 18px;
}
.metric-card {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fff;
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 18px 20px;
  box-shadow: 0 4px 14px rgba(29, 43, 79, 0.06);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.metric-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 10px 24px rgba(29, 43, 79, 0.12);
}
.metric-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--c) 14%, #fff);
  color: var(--c);
  flex-shrink: 0;
}
.metric-value { font-size: 24px; font-weight: 700; color: #1d2b4f; line-height: 1.2; }
.metric-label { font-size: 12px; color: #909399; margin-top: 2px; }
.metric-skeleton {
  display: inline-block;
  width: 42px;
  height: 22px;
  border-radius: 6px;
  background: linear-gradient(90deg, #eef1f6, #e2e7f0, #eef1f6);
  background-size: 200% 100%;
  animation: skeleton 1.2s linear infinite;
}
@keyframes skeleton {
  to { background-position: -200% 0; }
}

/* ===== 快捷入口 ===== */
.quick {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 14px;
  margin-top: 18px;
}
.quick-card {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fff;
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 18px 20px;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}
.quick-card:hover {
  transform: translateY(-3px);
  border-color: color-mix(in srgb, var(--c, #409eff) 40%, #fff);
  box-shadow: 0 10px 24px rgba(29, 43, 79, 0.12);
}
.quick-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  border-radius: 13px;
  background: color-mix(in srgb, var(--c) 14%, #fff);
  color: var(--c);
  flex-shrink: 0;
}
.quick-body { flex: 1; }
.quick-title { font-size: 15px; font-weight: 600; color: #1d2b4f; }
.quick-desc { font-size: 12px; color: #909399; margin-top: 3px; }
.quick-arrow { color: #c0c4cc; }
</style>