// 极简认证状态 (M2 不引 Pinia; 状态面很小, localStorage + reactive 足够)
import { reactive } from 'vue'
import api from './api'

export const authState = reactive({
  token: localStorage.getItem('hub_token') || '',
  user: JSON.parse(localStorage.getItem('hub_user') || 'null'),
})

export async function login(username, password) {
  const r = await api.post('/auth/login', { username, password })
  authState.token = r.data.token
  authState.user = r.data
  localStorage.setItem('hub_token', r.data.token)
  localStorage.setItem('hub_user', JSON.stringify(r.data))
}

export function clearAuth() {
  authState.token = ''
  authState.user = null
  localStorage.removeItem('hub_token')
  localStorage.removeItem('hub_user')
}

export async function logout() {
  try { await api.post('/auth/logout') } catch { /* token 已失效也照常清 */ }
  clearAuth()
}
