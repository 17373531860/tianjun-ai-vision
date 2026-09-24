// WS 推送加速器客户端 (M8)。
// 纪律与后端一致: 轮询是真相源, WS 只是加速器 —— 收到提示帧就触发一次
// 页面自己的 refresh; WS 挂了静默指数退避重连 (2s→30s), 期间纯轮询兜底,
// 用户无感知、零功能损失。
import { onBeforeUnmount, onMounted } from 'vue'

/**
 * @param {(topic: string) => void} onHint 收到提示帧的回调 (topic: wall|events)
 * @param {number} minIntervalMs 同 topic 回调节流 (默认 300ms, 防抖高频帧)
 */
export function useWsHint(onHint, minIntervalMs = 300) {
  let ws = null
  let retryMs = 2000
  let retryTimer = null
  let closed = false
  const lastFired = {}

  function url() {
    const token = localStorage.getItem('hub_token')
    if (!token) return null
    const base = import.meta.env.VITE_HUB_API_BASE || '/api/v1'
    // 相对 base → 同源; 绝对 base (开发跨端口) → 换协议拼 host
    const u = new URL(base, location.origin)
    u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:'
    u.pathname = `${u.pathname.replace(/\/$/, '')}/ws`
    u.search = `token=${encodeURIComponent(token)}`
    return u.toString()
  }

  function connect() {
    if (closed) return
    const target = url()
    if (!target) return   // 未登录不连
    try {
      ws = new WebSocket(target)
    } catch { scheduleRetry(); return }
    ws.onopen = () => { retryMs = 2000 }
    ws.onmessage = (ev) => {
      let topic = ''
      try { topic = JSON.parse(ev.data).topic } catch { return }
      const now = Date.now()
      if (now - (lastFired[topic] || 0) < minIntervalMs) return
      lastFired[topic] = now
      onHint(topic)
    }
    ws.onclose = () => { ws = null; scheduleRetry() }
    ws.onerror = () => { try { ws?.close() } catch { /* 已断 */ } }
  }

  function scheduleRetry() {
    if (closed || retryTimer) return
    retryTimer = setTimeout(() => {
      retryTimer = null
      retryMs = Math.min(retryMs * 2, 30000)
      connect()
    }, retryMs)
  }

  onMounted(connect)
  onBeforeUnmount(() => {
    closed = true
    clearTimeout(retryTimer)
    try { ws?.close() } catch { /* 已断 */ }
  })
}
