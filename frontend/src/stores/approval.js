import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  listApprovals,
  approveApproval,
  rejectApproval,
  saveCopyDraft,
} from '@/api'

// 审批中心状态：待审批 / 已处理列表，审批操作与 Socket 增量同步
export const useApprovalStore = defineStore('approval', () => {
  const pendingList = ref([])
  const processedList = ref([])
  const loading = ref(false)
  const filters = ref({ type: '', start_time: '', end_time: '' })

  function buildParams(status) {
    const params = { status }
    if (filters.value.type) params.type = filters.value.type
    if (filters.value.start_time) params.start_time = filters.value.start_time
    if (filters.value.end_time) params.end_time = filters.value.end_time
    return params
  }

  async function fetchPending() {
    loading.value = true
    try {
      pendingList.value = await listApprovals(buildParams('pending'))
    } finally {
      loading.value = false
    }
  }

  async function fetchProcessed() {
    loading.value = true
    try {
      processedList.value = await listApprovals(buildParams('processed'))
    } finally {
      loading.value = false
    }
  }

  async function approve(approvalId, payload) {
    const updated = await approveApproval(approvalId, payload)
    moveToProcessed(updated)
    return updated
  }

  async function reject(approvalId, comment) {
    const updated = await rejectApproval(approvalId, { comment })
    moveToProcessed(updated)
    return updated
  }

  async function saveDraft(approvalId, content) {
    return saveCopyDraft(approvalId, { content })
  }

  function moveToProcessed(approval) {
    pendingList.value = pendingList.value.filter((a) => a.id !== approval.id)
    const idx = processedList.value.findIndex((a) => a.id === approval.id)
    if (idx >= 0) processedList.value[idx] = approval
    else processedList.value.unshift(approval)
  }

  // Socket: 新审批单（风控熔断推送）
  function upsertPending(approval) {
    if (approval.status !== 'pending') return
    if (filters.value.type && filters.value.type !== approval.type) return
    const idx = pendingList.value.findIndex((a) => a.id === approval.id)
    if (idx >= 0) pendingList.value[idx] = approval
    else pendingList.value.unshift(approval)
  }

  // Socket: 审批状态变化（多标签页/其他人操作时同步）
  function applyUpdated(approval) {
    const pIdx = pendingList.value.findIndex((a) => a.id === approval.id)
    if (pIdx >= 0) {
      if (approval.status === 'pending') pendingList.value[pIdx] = approval
      else pendingList.value.splice(pIdx, 1)
    }
    const dIdx = processedList.value.findIndex((a) => a.id === approval.id)
    if (dIdx >= 0) processedList.value[dIdx] = approval
    else if (approval.status !== 'pending') processedList.value.unshift(approval)
  }

  return {
    pendingList,
    processedList,
    loading,
    filters,
    fetchPending,
    fetchProcessed,
    approve,
    reject,
    saveDraft,
    upsertPending,
    applyUpdated,
  }
})
