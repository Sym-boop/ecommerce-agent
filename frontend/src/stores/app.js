import { defineStore } from 'pinia'
import { ref } from 'vue'
import { dashboardSocket } from '@/utils/websocket'
import { USE_MOCK } from '@/api/mock'
import { listApprovals } from '@/api'

// 全局应用状态：WebSocket 连接、待审批角标计数
export const useAppStore = defineStore('app', () => {
  const connected = ref(false)
  const useMock = ref(USE_MOCK)
  const pendingCount = ref(0)

  async function refreshPendingCount() {
    try {
      const list = await listApprovals({ status: 'pending' })
      pendingCount.value = list.length
    } catch {
      // 角标刷新失败不打断页面
    }
  }

  function onSocketEvent(event, handler) {
    return dashboardSocket.on(event, handler)
  }

  function initSocket() {
    dashboardSocket.onStatusChange = (val) => {
      connected.value = val
    }
    dashboardSocket.connect()
    refreshPendingCount()
  }

  return {
    connected,
    useMock,
    pendingCount,
    initSocket,
    refreshPendingCount,
    onSocketEvent,
  }
})
