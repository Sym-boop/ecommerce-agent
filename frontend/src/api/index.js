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

// ---------- 广告投放 ----------
// 注意：只有「巨量千川」有真实投递 Agent（real_ready），其余三个平台是预留位（reserved）。
// 后端对 reserved 平台的 /delivery/submit 返回 501，前端据 capability 置灰按钮。

/** 可投放的目标平台列表（含 capability：real_ready / reserved） */
export function listDeliveryPlatforms() {
  return request.get('/delivery/platforms')
}

/** 各平台投递 Agent 的能力与「还缺什么」（前端渲染按钮状态与提示） */
export function getDeliveryCapabilities() {
  return request.get('/delivery/capabilities')
}

/** 一键投放：{ platform, ad_ids: [..], budget } → 真实/模拟投放结果（预算超出区间会被拦截） */
export function submitDelivery(payload) {
  return request.post('/delivery/submit', payload)
}

/** 关停某物料已投放的计划（真实投放的安全网）：{ ad_id } */
export function stopDelivery(payload) {
  return request.post('/delivery/stop', payload)
}

/** 历史投放批次记录 */
export function listDeliveries() {
  return request.get('/delivery/list')
}

/** 投放预算审查区间配置 { min_budget, max_budget } */
export function getDeliveryConfig() {
  return request.get('/delivery/config')
}

/** 某平台详细介绍与投放概况 */
export function getPlatformDetail(key) {
  return request.get(`/delivery/platforms/${key}`)
}

/** 某平台可选广告：{ available: 未投放, history: 投放历史 } */
export function getPlatformAds(key) {
  return request.get(`/delivery/platforms/${key}/ads`)
}

/** 某平台的历史投放批次 */
export function getPlatformDeliveries(key) {
  return request.get(`/delivery/platforms/${key}/deliveries`)
}

// ---------- 商品目录（广告生成页：选择 / 自定义商品） ----------

/** 商品列表（内置清单 + 自定义商品） */
export function listProducts() {
  return request.get('/products')
}

/** 新增自定义商品：{ name, category?, sell_points?, price? } */
export function createProduct(payload) {
  return request.post('/products', payload)
}

// ---------- 生成历史记录（正常 / 半成品） ----------

/** 历史记录列表（含 wip 半成品标记） */
export function listHistory() {
  return request.get('/history')
}

/** 保存一次生成为正常记录 */
export function saveHistory(payload) {
  return request.post('/history', payload)
}

/** 保存为半成品记录（红色标签） */
export function pauseHistory(payload) {
  return request.post('/history/pause', payload)
}

/** 删除一条历史记录 */
export function deleteHistory(recordId) {
  return request.delete(`/history/${recordId}`)
}

// ---------- 广告生成（LangGraph 主图：文案 → 审查 → 图文 / 视频） ----------

/**
 * 生成 / 重新生成文案（自动送文案审查）
 * payload: { session_id?, product, keyword?, platform?, regenerate? }
 */
/** 爆款文案模版列表（平台 × 结构风格）；不传 platform 返回全部 */
export function getAdgenTemplates(platform = '') {
  return request.get('/adgen/templates', { params: platform ? { platform } : {} })
}

export function adgenCopy(payload) {
  return request.post('/adgen/copy', payload, { timeout: 120000 })
}

/** 文案命中违禁词后的人工处置：decision = approve | reject */
export function adgenReview(sessionId, payload) {
  return request.post(`/adgen/review/${sessionId}`, payload)
}

/** 基于已通过审查的文案生成图文 / 视频（multipart：session_id、type、keyword?、image?） */
export function adgenAsset(formData) {
  return request.post('/adgen/asset', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

/** 查询图文 / 视频生成状态 */
export function adgenAssetStatus(taskId) {
  return request.get(`/adgen/asset/status/${taskId}`)
}

/** 生成会话快照（供横向步骤卡回填） */
export function adgenSession(sessionId) {
  return request.get(`/adgen/session/${sessionId}`)
}

/** 载入历史记录为生成会话（作为模板继续生成图文 / 视频） */
export function adgenImport(recordId) {
  return request.post('/adgen/import', { record_id: recordId })
}

/** 存到投放界面（进入广告流水线 + 写入历史记录） */
export function adgenSave(payload) {
  return request.post('/adgen/save', payload)
}

/** 暂停生成并退出（写入半成品历史记录） */
export function adgenPause(payload) {
  return request.post('/adgen/pause', payload)
}

/** 删除此次生成（未保存则仅清会话） */
export function adgenDiscard(payload) {
  return request.post('/adgen/discard', payload)
}

// ---------- 批量生成（Dify 风格分叉树） ----------

/**
 * 批量生成文案（count 1~8）；首次调用传 product 创建会话，之后传 batch_id 追加
 * payload: { batch_id?, product?, keyword?, platform?, count }
 */
export function batchCopy(payload) {
  return request.post('/batch/copy', payload, { timeout: 120000 })
}

/** 为某条文案批量生成 图文 / 视频（multipart：batch_id、copy_id、type、count、keyword?、image?；count 1~5） */
export function batchAsset(formData) {
  return request.post('/batch/asset', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

/** 重新生成分叉树中的单个节点（文案 / 图文 / 视频） */
export function batchRegenerate(payload) {
  return request.post('/batch/regenerate', payload, { timeout: 120000 })
}

/** 批量生成会话快照（分叉树节点） */
export function batchSession(batchId) {
  return request.get(`/batch/session/${batchId}`)
}

/** 保存到投放界面 + 历史记录（可传 copy_ids 指定文案节点） */
export function batchSave(payload) {
  return request.post('/batch/save', payload)
}

/** 丢弃整棵批量生成分叉树 */
export function batchDiscard(payload) {
  return request.post('/batch/discard', payload)
}

// ---------- 广告投放：删除广告物料 ----------

/** 删除某平台下的一条广告（未投放 / 历史投放均可） */
export function deletePlatformAd(platformKey, adId) {
  return request.delete(`/delivery/platforms/${platformKey}/ads/${adId}`)
}

// ---------- 店铺信息（按平台区分，落盘到 data/stores/<platform>.json） ----------

/** 某平台下的全部店铺：{ platform, platform_label, stores: [...] } */
export function listStores(platform) {
  return request.get(`/stores/${platform}`)
}

/** 新增店铺：{ name?, shop_id, douyin_account?, aweme_id?, advertiser_id?, products?, note? } */
export function createStore(platform, payload) {
  return request.post(`/stores/${platform}`, payload)
}

/** 删除一条店铺 */
export function deleteStore(platform, storeId) {
  return request.delete(`/stores/${platform}/${storeId}`)
}

// ---------- 抖音 · 巨量千川 投递 Agent（独立 MCP 工具） ----------

/** 投递所需参数说明（店铺 ID / 商品 ID / 抖音号 / 素材等） */
export function getQianchuanRequirements() {
  return request.get('/qianchuan/requirements')
}

/** 千川账户概览（广告主 / 余额 / 钱包） */
export function getQianchuanAccount() {
  return request.get('/qianchuan/account')
}

/** 投递预检：{ shop_id, product_id, aweme_id, video_id?, image_id? } → { ready, checks } */
export function preflightQianchuan(payload) {
  return request.post('/qianchuan/preflight', payload)
}

/** 执行投放（预检 + 提交，默认 mock） */
export function deliverQianchuan(payload) {
  return request.post('/qianchuan/deliver', payload)
}

// ---------- 充值中心（独立图文 API 账户，Mock 记账） ----------

/** 账户概览：余额 / 点数 / 计费口径 */
export function getBillingAccount() {
  return request.get('/billing/account')
}

/** 充值套餐列表 */
export function listBillingPackages() {
  return request.get('/billing/packages')
}

/** 充值 / 消费流水 */
export function listBillingInvoices() {
  return request.get('/billing/invoices')
}

/** 充值（Mock）：{ package_id } → 新余额与到账点数 */
export function rechargeAccount(payload) {
  return request.post('/billing/recharge', payload)
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

/** 批量审批：{ ids: [...], action: 'approve'|'reject', comment } */
export function batchReview(payload) {
  return request.post('/review/batch', payload)
}

/** 审批 SLA：待办积压 / 超时清单 / 平均等待时长 */
export function getReviewSla() {
  return request.get('/review/sla')
}

/** 热加载违禁词表（需管理员权限） */
export function reloadBannedWords() {
  return request.post('/review/banned-words/reload')
}

// ---------- 系统管理（配置自检 / 指标 / 审计 / 质量 / 版本 / 数据飞轮） ----------

/** 启动配置自检：Key 是否配置、模型名是否有效、词表是否可用 */
export function getConfigStatus() {
  return request.get('/system/config-status')
}

/** 运行指标：调用量 / 失败率 / 降级次数 / P95 耗时 / 成本估算 */
export function getMetrics() {
  return request.get('/system/metrics')
}

/** 操作审计日志（支持按动作/对象/操作人过滤，需管理员权限） */
export function listAuditLog(params) {
  return request.get('/system/audit', { params })
}

/** 审计概览 */
export function getAuditStats() {
  return request.get('/system/audit/stats')
}

/** 质量评估看板：平均分 / 指标通过率 / 分数分布 / 低分样本 */
export function getQualityDashboard(params) {
  return request.get('/system/quality', { params })
}

/** 某条素材的版本链 */
export function getVersions(adId) {
  return request.get(`/system/versions/${adId}`)
}

/** 版本 diff（默认第一版 → 最新版） */
export function getVersionDiff(adId, params) {
  return request.get(`/system/versions/${adId}/diff`, { params })
}

/** 数据飞轮概览：已回流样本 / 待补写数 */
export function getFlywheel() {
  return request.get('/system/flywheel')
}

/** 最近回流的人工认可样本 */
export function getFlywheelSamples(params) {
  return request.get('/system/flywheel/samples', { params })
}

/** 把待入库样本补写进向量库（需管理员权限） */
export function flushFlywheel() {
  return request.post('/system/flywheel/flush')
}

/** 角色权限矩阵 + 当前登录角色 */
export function getRbac() {
  return request.get('/system/rbac')
}
