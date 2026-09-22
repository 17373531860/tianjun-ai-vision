// 枢纽 API 客户端 — 唯一 axios 实例
// baseURL: 开发走 vite 代理 /api/v1 → 9100; 生产同源 (dist 由枢纽后端分发)
import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_HUB_API_BASE || '/api/v1',
  timeout: 10000,
})

api.interceptors.request.use((cfg) => {
  const t = localStorage.getItem('hub_token')
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    // token 失效统一踢回登录页 (hash 路由)
    if (err.response && err.response.status === 401
        && !String(err.config?.url || '').includes('/auth/login')) {
      localStorage.removeItem('hub_token')
      localStorage.removeItem('hub_user')
      if (!location.hash.startsWith('#/login')) location.hash = '#/login'
    }
    return Promise.reject(err)
  },
)

export default api
