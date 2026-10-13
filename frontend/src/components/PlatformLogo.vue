<template>
  <!-- 自绘平台品牌徽标（可按 logo_key 替换为真实 logo，见 frontend/src/assets/platforms/） -->
  <svg :width="size" :height="size" viewBox="0 0 48 48" class="platform-logo" :aria-label="label">
    <defs>
      <linearGradient :id="gradId" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" :stop-color="color" stop-opacity="0.95" />
        <stop offset="100%" :stop-color="color" stop-opacity="0.72" />
      </linearGradient>
    </defs>

    <rect x="2" y="2" width="44" height="44" rx="13" :fill="`url(#${gradId})`" />

    <!-- 抖音 / 巨量千川：音符 -->
    <g v-if="logoKey === 'douyin'" fill="#fff">
      <path d="M29 11c.6 3.4 2.6 5.4 6 5.9v4.1c-2.2.1-4.2-.5-6-1.7v8.5a8.3 8.3 0 1 1-8.3-8.3c.5 0 1 .04 1.5.13v4.3a4.2 4.2 0 1 0 2.8 3.9V11H29z" />
    </g>

    <!-- 小红书 / 聚光：圆角书签 -->
    <g v-else-if="logoKey === 'xiaohongshu'" fill="#fff">
      <rect x="13" y="12" width="22" height="24" rx="5" opacity="0.92" />
      <rect x="18.5" y="12" width="2.6" height="9" rx="1.3" fill="#ff2442" />
      <rect x="27" y="12" width="2.6" height="9" rx="1.3" fill="#ff2442" />
      <circle cx="20.5" cy="22" r="1.9" fill="#ff2442" />
      <circle cx="27.5" cy="22" r="1.9" fill="#ff2442" />
    </g>

    <!-- 阿里妈妈：抽象「M」字徽标 -->
    <g v-else-if="logoKey === 'alimama'" fill="#fff">
      <path d="M13 33V15h4.4l6.6 9.4L30.6 15H35v18h-4.4V22.3L24 31.4l-6.6-9.1V33H13z" />
    </g>

    <!-- 拼多多 / 多多进宝：心形花瓣 -->
    <g v-else-if="logoKey === 'pinduoduo'" fill="#fff">
      <path d="M24 34c-6.6-4.2-10.5-7.6-10.5-12.2 0-3.1 2.3-5.3 5.2-5.3 2.1 0 3.9 1.1 5.3 3.1 1.4-2 3.2-3.1 5.3-3.1 2.9 0 5.2 2.2 5.2 5.3C34.5 26.4 30.6 29.8 24 34z" />
    </g>

    <!-- 兜底：首字 -->
    <text
      v-else
      x="24"
      y="30"
      text-anchor="middle"
      font-size="18"
      font-weight="700"
      fill="#fff"
    >
      {{ (label || '·').slice(0, 1) }}
    </text>
  </svg>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  logoKey: { type: String, default: '' },
  color: { type: String, default: '#409eff' },
  label: { type: String, default: '' },
  size: { type: [Number, String], default: 46 },
})

const gradId = computed(() => `pl-${props.logoKey || 'x'}-${Math.random().toString(36).slice(2, 7)}`)
</script>

<style scoped>
.platform-logo {
  display: block;
  flex-shrink: 0;
  border-radius: 13px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}
</style>