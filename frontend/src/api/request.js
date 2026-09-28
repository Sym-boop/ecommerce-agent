import axios from 'axios'
import { ElMessage } from 'element-plus'
import { mockAdapter, USE_MOCK } from './mock'

// 统一响应体约定（与后端对齐）：{ code: 0, msg: 'ok', data: ... }
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 15000,
})

// Mock 模式：后端未就绪时使用内置适配器，返回模拟数据
if (USE_MOCK) {
  service.defaults.adapter = mockAdapter
}

// 请求拦截：注入鉴权 Token
service.interceptors.request.use((config) => {
  const token = localStorage.getItem('dashboard_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截：统一解包与错误处理
service.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) {
        return body.data
      }
      ElMessage.error(body.msg || '请求失败')
      return Promise.reject(new Error(body.msg || 'Business error'))
    }
    return body
  },
  (error) => {
    if (error.response?.status === 401) {
      ElMessage.error('登录态已过期，请重新登录')
    } else {
      ElMessage.error(error.response?.data?.msg || error.message || '网络异常')
    }
    return Promise.reject(error)
  },
)

export default service
