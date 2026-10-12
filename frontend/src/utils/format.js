// 公共格式化工具

export function formatTime(iso, withSeconds = true) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}${withSeconds ? `:${pad(d.getSeconds())}` : ''}`
  return `${date} ${time}`
}

export const NODE_STATUS_META = {
  pending: { label: '等待中', type: 'info' },
  running: { label: '执行中', type: 'primary' },
  success: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  waiting: { label: '待审批', type: 'warning' },
}

export const TASK_STATUS_META = {
  running: { label: '执行中', type: 'primary' },
  done: { label: '已完成', type: 'success' },
  failed: { label: '已终止', type: 'danger' },
}

export const APPROVAL_TYPE_META = {
  copy: { label: '文案发布', type: 'primary' },
  pricing: { label: '调价申请', type: 'warning' },
  ads: { label: '广告出价', type: 'danger' },
}

export const APPROVAL_STATUS_META = {
  pending: { label: '待审批', type: 'warning' },
  approved: { label: '已通过', type: 'success' },
  rejected: { label: '已驳回', type: 'danger' },
}

/** 广告物料类型 */
export const AD_TYPE_META = {
  copy: { label: '文案广告', tagType: 'info' },
  image: { label: '图文广告', tagType: 'success' },
  video: { label: '视频广告', tagType: 'danger' },
}

/** 广告流水线阶段 */
export const STAGE_META = {
  review: { label: '审查中', type: 'warning' },
  pending: { label: '待审批', type: 'warning' },
  approved: { label: '已审批', type: 'success' },
  delivered: { label: '已投放', type: 'primary' },
  rejected: { label: '已驳回', type: 'danger' },
}

export const PLATFORM_META = {
  xiaohongshu: '小红书',
  tiktok: 'TikTok',
  amazon: '亚马逊',
}

/** 生成器页面可选平台（文案/图文共用） */
export const PLATFORM_OPTIONS = [
  { value: 'xiaohongshu', label: '小红书' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'amazon', label: '亚马逊' },
]

/** 后端静态资源根地址；默认留空使用相对路径（走 vite proxy / 同源），可用 VITE_API_ORIGIN 覆盖为绝对地址 */
export const API_ORIGIN = String(import.meta.env.VITE_API_ORIGIN || '')

/**
 * 把后端返回的媒体路径（如 data/media/images/x.png 或 /media/videos/x.mp4）
 * 转成可直接访问的绝对地址；外部 http(s) 地址原样返回。
 */
export function mediaUrl(path) {
  if (!path) return ''
  if (/^https?:\/\//i.test(path)) return path
  const rel = String(path)
    .replace(/\\/g, '/')
    .replace(/^.*?data\/media/, '/media')
  return rel.startsWith('/') ? API_ORIGIN + rel : `${API_ORIGIN}/${rel}`
}

export const LOG_LEVEL_META = {
  info: { label: 'INFO', color: '#909399' },
  success: { label: ' OK ', color: '#67c23a' },
  warn: { label: 'WARN', color: '#e6a23c' },
  error: { label: 'ERR ', color: '#f56c6c' },
}
