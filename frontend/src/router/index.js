import { createRouter, createWebHistory } from 'vue-router'

// 路由表：任务拓扑看板（默认页）/ 审批中心
const routes = [
  {
    path: '/',
    redirect: '/canvas',
  },
  {
    path: '/canvas',
    name: 'agent-canvas',
    component: () => import('@/views/AgentCanvas.vue'),
    meta: { title: '任务拓扑看板' },
  },
  {
    path: '/approvals',
    name: 'approvals',
    component: () => import('@/views/Approvals.vue'),
    meta: { title: '审批中心' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta.title
    ? `${to.meta.title} - 电商多智能体运营驾驶舱`
    : '电商多智能体运营驾驶舱'
})

export default router
