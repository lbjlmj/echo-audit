/**
 * axios 封装：按后端路由域分文件（TECH_DESIGN §2.1 api/）。
 * 请求拦截附 JWT；响应拦截 401 清会话回登录入口（错误码按域分段由调用方 ElMessage 展示）。
 */
import axios from 'axios'

export const http = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('echo_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('echo_token')
      // history 路由模式：整页跳转登录入口（避免循环依赖 router 实例）
      if (!window.location.pathname.startsWith('/hub')) window.location.href = '/hub'
    }
    return Promise.reject(err)
  },
)

/** 提取后端 detail 错误文案（PR-1xxx 等域段码在首段） */
export function errText(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } } }
  const detail = e.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(String).join('；')
  return '请求失败，请稍后重试'
}
