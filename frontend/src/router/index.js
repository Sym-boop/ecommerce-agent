import { createRouter, createWebHistory } from 'vue-router'

// 路由表：登录 / 首页 / 广告生成 / 广告投放 / 历史广告发售 / 审查中心（任务看板仅保留路由，不在菜单展示）
const routes = [
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/Home.vue'),
    meta: { title: '首页' },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/adgen',
    name: 'ad-generator',
    component: () => import('@/views/AdGenerator.vue'),
    meta: { title: '广告生成' },
  },
  {
    path: '/batch',
    name: 'batch-generator',
    component: () => import('@/views/BatchGenerator.vue'),
    meta: { title: '批量生成' },
  },
  {
    path: '/history',
    name: 'ad-history',
    component: () => import('@/views/AdHistory.vue'),
    meta: { title: '历史广告发售' },
  },
  {
    path: '/delivery',
    name: 'ad-delivery',
    component: () => import('@/views/DeliveryCenter.vue'),
    meta: { title: '广告投放' },
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
    meta: { title: '审查中心' },
  },
  {
    path: '/recharge',
    name: 'recharge',
    component: () => import('@/views/RechargeCenter.vue'),
    meta: { title: '充值中心' },
  },
  {
    path: '/help',
    name: 'help',
    component: () => import('@/views/HelpCenter.vue'),
    meta: { title: '帮助中心' },
  },
  {
    path: '/system',
    name: 'system-center',
    component: () => import('@/views/SystemCenter.vue'),
    meta: { title: '系统中心' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 未登录拦截：除登录页外统一跳转到 /login，并带上回跳地址
router.beforeEach((to) => {
  const loggedIn = !!localStorage.getItem('dashboard_token')
  if (!to.meta.public && !loggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.public && loggedIn) {
    return { path: '/' }
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title
    ? `${to.meta.title} - 电商多智能体运营驾驶舱`
    : '电商多智能体运营驾驶舱'
})

export default router
