import { createRouter, createWebHistory } from 'vue-router'

// 路由表：登录 / 生成器（文案/图文/视频）/ 任务拓扑看板 / 审查中心
const routes = [
  {
    path: '/',
    redirect: '/copy',
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/copy',
    name: 'copy-generator',
    component: () => import('@/views/CopyGenerator.vue'),
    meta: { title: '文案生成' },
  },
  {
    path: '/image',
    name: 'image-generator',
    component: () => import('@/views/ImageGenerator.vue'),
    meta: { title: '图文生成' },
  },
  {
    path: '/video',
    name: 'video-generator',
    component: () => import('@/views/VideoGenerator.vue'),
    meta: { title: '视频生成' },
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
