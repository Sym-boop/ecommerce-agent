import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  loginAccount,
  registerAccount,
  logoutAccount,
  fetchMe,
} from '@/api'

const TOKEN_KEY = 'dashboard_token'
const USER_KEY = 'dashboard_user'

// 登录态：真实用户表（用户名 + 密码），token 存 localStorage 供请求拦截器注入
export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const user = ref(JSON.parse(localStorage.getItem(USER_KEY) || 'null'))
  const loading = ref(false)

  function persist(nextToken, nextUser) {
    token.value = nextToken || ''
    user.value = nextUser || null
    if (nextToken) localStorage.setItem(TOKEN_KEY, nextToken)
    else localStorage.removeItem(TOKEN_KEY)
    if (nextUser) localStorage.setItem(USER_KEY, JSON.stringify(nextUser))
    else localStorage.removeItem(USER_KEY)
  }

  async function login(payload) {
    loading.value = true
    try {
      const { token: t, user: u } = await loginAccount(payload)
      persist(t, u)
      return u
    } finally {
      loading.value = false
    }
  }

  async function register(payload) {
    loading.value = true
    try {
      const { token: t, user: u } = await registerAccount(payload)
      persist(t, u)
      return u
    } finally {
      loading.value = false
    }
  }

  async function loadMe() {
    if (!token.value) return null
    try {
      const u = await fetchMe()
      persist(token.value, u)
      return u
    } catch {
      // token 失效（401）时清除本地登录态
      persist('', null)
      return null
    }
  }

  async function logout() {
    try {
      if (token.value) await logoutAccount()
    } catch {
      // 退出接口失败不阻断本地清理
    }
    persist('', null)
  }

  const isLoggedIn = () => !!token.value

  return { token, user, loading, login, register, loadMe, logout, isLoggedIn }
})