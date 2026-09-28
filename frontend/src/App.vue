<template>
  <el-container class="app-shell">
    <el-header class="app-header" height="56px">
      <div class="brand">
        <el-icon :size="22"><Shop /></el-icon>
        <span class="brand-title">电商多智能体运营驾驶舱</span>
        <el-tag
          v-if="appStore.useMock"
          size="small"
          type="warning"
          effect="dark"
          round
          class="mock-tag"
        >
          MOCK 模式
        </el-tag>
      </div>

      <el-menu
        :default-active="activeMenu"
        mode="horizontal"
        router
        :ellipsis="false"
        class="app-menu"
        background-color="transparent"
        text-color="#c8d3e8"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/canvas">
          <el-icon><Share /></el-icon>
          <span>任务拓扑看板</span>
        </el-menu-item>
        <el-menu-item index="/approvals">
          <el-badge
            :value="appStore.pendingCount"
            :hidden="appStore.pendingCount === 0"
            :max="99"
            class="approval-badge"
          >
            <el-icon><Bell /></el-icon>
            <span>审批中心</span>
          </el-badge>
        </el-menu-item>
      </el-menu>

      <div class="header-right">
        <span class="conn-status" :class="{ 'is-on': appStore.connected }">
          <span class="conn-dot"></span>
          {{ appStore.connected ? '实时通道已连接' : '通道断开，重连中…' }}
        </span>
      </div>
    </el-header>

    <el-main class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElNotification } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { useTaskStore } from '@/stores/task'
import { useApprovalStore } from '@/stores/approval'
import { APPROVAL_TYPE_META } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const taskStore = useTaskStore()
const approvalStore = useApprovalStore()

const activeMenu = computed(() =>
  route.path.startsWith('/approvals') ? '/approvals' : '/canvas',
)

onMounted(() => {
  appStore.initSocket()

  // 节点状态增量更新 → 拓扑看板
  appStore.onSocketEvent('node:update', (data) => {
    taskStore.applyNodeUpdate(data)
  })

  // 任务整体状态变更
  appStore.onSocketEvent('task:update', (data) => {
    taskStore.applyTaskUpdate(data)
  })

  // 风控熔断：新审批单推送
  appStore.onSocketEvent('approval:created', (approval) => {
    appStore.refreshPendingCount()
    approvalStore.upsertPending(approval)
    ElNotification({
      title: `风控拦截：${APPROVAL_TYPE_META[approval.type]?.label || '新审批'}待处理`,
      message:
        approval.type === 'copy'
          ? `《${approval.title || approval.product_name}》需人工复核发布`
          : approval.type === 'pricing'
            ? `${approval.product_name} 建议${approval.action} ${Math.abs(approval.change_percent).toFixed(2)}%`
            : `${approval.campaign_name} 超预算阈值已阻断`,
      type: 'warning',
      duration: 6000,
      onClick: () => router.push('/approvals'),
    })
  })

  // 审批单状态变化（多端协同）
  appStore.onSocketEvent('approval:updated', (approval) => {
    approvalStore.applyUpdated(approval)
    appStore.refreshPendingCount()
  })
})
</script>

<style scoped>
.app-shell {
  height: 100%;
}

.app-header {
  display: flex;
  align-items: center;
  gap: 36px;
  padding: 0 22px;
  background: linear-gradient(90deg, #1d2b4f 0%, #243b6b 55%, #2e4c86 100%);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.18);
  position: relative;
  z-index: 10;
}

.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #fff;
  flex-shrink: 0;
}
.brand-title {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.5px;
  white-space: nowrap;
}
.mock-tag {
  margin-left: 2px;
}

.app-menu {
  border-bottom: none !important;
  flex: 1;
}
.app-menu .el-menu-item {
  border-bottom: 3px solid transparent !important;
}
.app-menu .el-menu-item.is-active {
  border-bottom-color: #79bbff !important;
  background-color: rgba(255, 255, 255, 0.06) !important;
}
.approval-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.header-right {
  flex-shrink: 0;
}
.conn-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: #a9b8d4;
}
.conn-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f56c6c;
  box-shadow: 0 0 0 3px rgba(245, 108, 108, 0.18);
}
.conn-status.is-on .conn-dot {
  background: #67c23a;
  box-shadow: 0 0 0 3px rgba(103, 194, 58, 0.2);
}

.app-main {
  padding: 18px 22px;
  height: calc(100% - 56px);
  overflow: auto;
}
</style>
