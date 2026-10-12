// ============================================
// API 接口定义层（REST 契约，供成员B/C后端对齐）
// 基础前缀：/api/v1
// 统一响应体：{ code: 0, msg: 'ok', data: ... }
// ============================================
import request from './request'

// ---------- 运营目标与任务拓扑 ----------

/** 提交运营目标，触发 Supervisor 拆解与调度 */
export function createGoal(payload) {
  // payload: { goal: string, platforms?: string[], languages?: string[], budget?: number }
  return request.post('/goals', payload)
}

/** 获取任务列表（拓扑摘要） */
export function listTasks() {
  return request.get('/tasks')
}

/** 获取单个任务详情（含节点、连线、日志、输入输出） */
export function getTask(taskId) {
  return request.get(`/tasks/${taskId}`)
}

/** 重试失败任务（可指定节点） */
export function retryTask(taskId, nodeId) {
  return request.post(`/tasks/${taskId}/retry`, { node_id: nodeId || null })
}

/** 取消/跳过任务节点 */
export function cancelTask(taskId) {
  return request.post(`/tasks/${taskId}/cancel`)
}

// ---------- 人机协同审批 ----------

/**
 * 审批单列表
 * @param {Object} params - { status: 'pending'|'processed', type, start_time, end_time }
 */
export function listApprovals(params) {
  return request.get('/approvals', { params })
}

/** 审批单详情 */
export function getApproval(approvalId) {
  return request.get(`/approvals/${approvalId}`)
}

/** 审批通过（文案审批可携带微调后的内容） */
export function approveApproval(approvalId, payload) {
  // payload: { comment?: string, edited_content?: string }
  return request.post(`/approvals/${approvalId}/approve`, payload || {})
}

/** 审批驳回（必须填写驳回原因） */
export function rejectApproval(approvalId, payload) {
  // payload: { comment: string }
  return request.post(`/approvals/${approvalId}/reject`, payload)
}

/** 保存文案微调结果（生成新版本，不改变审批状态） */
export function saveCopyDraft(approvalId, payload) {
  // payload: { content: string }
  return request.put(`/copies/${approvalId}`, payload)
}

// ---------- 视频生成（独立第三方图生视频链路） ----------

/**
 * 提交视频生成任务：商品图片 + 提示词 → 约 10s 动态短视频
 * @param {FormData} formData - 含 image(文件)、prompt、duration
 * @returns {Promise<{task_id: string}>}
 */
export function generateVideo(formData) {
  return request.post('/video/generate', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    // 上传 + 异步生成，放宽超时（生成进度由 status 接口轮询）
    timeout: 120000,
  })
}

/** 查询视频生成任务状态：{ task_id, status, prompt, video_url, error } */
export function getVideoStatus(taskId) {
  return request.get(`/video/status/${taskId}`)
}

// ---------- 文案生成（独立模块） ----------

/** 提交文案生成任务：商品信息 → 广告文案，返回 task_id */
export function generateCopy(payload) {
  // payload: { product_name, product_sell_points, platform, extra }
  return request.post('/copy/generate', payload)
}

/** 查询文案生成任务状态：{ status, title, content, tags, platform, error } */
export function getCopyStatus(taskId) {
  return request.get(`/copy/status/${taskId}`)
}

// ---------- 图文生成（独立模块，商品图片可选） ----------

/** 提交图文生成任务：提示词 + 可选商品图片 → 图文海报 */
export function generateImage(formData) {
  return request.post('/image/generate', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

/** 查询图文生成任务状态：{ status, prompt, input_image_url, image_url, error } */
export function getImageStatus(taskId) {
  return request.get(`/image/status/${taskId}`)
}

// ---------- 广告物料历史 ----------

/** 获取全部已生成的广告物料（文案 / 图文 / 视频，按时间倒序） */
export function listAds() {
  return request.get('/ads')
}

// ---------- 广告投放（Mock 模拟） ----------

/** 可投放的目标平台列表（巨量千川 / 小红书 / 抖音…） */
export function listDeliveryPlatforms() {
  return request.get('/delivery/platforms')
}

/** 一键投放：{ platform, ad_ids: [..], budget } → mock 投放结果（预算超出区间会被拦截） */
export function submitDelivery(payload) {
  return request.post('/delivery/submit', payload)
}

/** 历史投放批次记录 */
export function listDeliveries() {
  return request.get('/delivery/list')
}

/** 投放预算审查区间配置 { min_budget, max_budget } */
export function getDeliveryConfig() {
  return request.get('/delivery/config')
}

// ---------- 用户鉴权（真实用户表） ----------

/** 注册：{ username, password, display_name? } → { token, user } */
export function registerAccount(payload) {
  return request.post('/auth/register', payload)
}

/** 登录：{ username, password } → { token, user }；用户名不存在时提示「账号不存在，请先注册」 */
export function loginAccount(payload) {
  return request.post('/auth/login', payload)
}

/** 获取当前登录用户 */
export function fetchMe() {
  return request.get('/auth/me')
}

/** 退出登录 */
export function logoutAccount() {
  return request.post('/auth/logout')
}

// ---------- 审查中心（只读审查） ----------

/** 审查阈值配置（违禁词来源 + 投放预算区间） */
export function getReviewConfig() {
  return request.get('/review/config')
}

/** 文案违禁词审查：扫描全部文案广告 */
export function getCopyReview() {
  return request.get('/review/copy')
}

/** 投放预算审查：判断历史批次预算超支 / 过低 */
export function getBudgetReview() {
  return request.get('/review/budget')
}

/** 审查中心各阶段数量统计 { copy_review, budget_review, pending, approved, delivered, rejected } */
export function getReviewStats() {
  return request.get('/review/stats')
}

/** 待审批广告列表（两种审查均通过） */
export function getReviewPending() {
  return request.get('/review/pending')
}

/** 已处理广告列表（审批通过 / 驳回） */
export function getReviewProcessed() {
  return request.get('/review/processed')
}

/** 违禁词审查放行（可携带修正后的正文，重新扫描通过后放行） */
export function clearCopyReview(adId, payload) {
  return request.post(`/review/copy/${adId}/clear`, payload || {})
}

/** 预算审查放行（携带调整后的预算，重新校验通过后放行） */
export function clearBudgetReview(adId, payload) {
  return request.post(`/review/budget/${adId}/clear`, payload)
}

/** 待审批通过 → 广告进入「广告投放」候选 */
export function approveAd(adId, payload) {
  return request.post(`/review/approve/${adId}`, payload || {})
}

/** 审批驳回 → 广告进入「已处理」 */
export function rejectAd(adId, payload) {
  return request.post(`/review/reject/${adId}`, payload)
}
