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
