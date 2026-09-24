// 快照轮询 composable — <img> 不能带 Authorization header,
// 走 axios blob + objectURL (旧 URL 及时 revoke, 防内存涨)
import { onBeforeUnmount, onMounted, ref } from 'vue'
import api from '../api'

export function useSnapshot(urlFn, intervalMs = 1000) {
  const src = ref('')
  const broken = ref(false)
  let timer = null
  let currentUrl = ''
  let inFlight = false

  async function tick() {
    if (inFlight) return // 上一帧还没回来: 跳拍不排队
    const url = urlFn()
    if (!url) return // 调用方尚未定位目标 (如放大层未选中工位)
    inFlight = true
    try {
      const r = await api.get(url, { responseType: 'blob' })
      const u = URL.createObjectURL(r.data)
      if (currentUrl) URL.revokeObjectURL(currentUrl)
      currentUrl = u
      src.value = u
      broken.value = false
    } catch {
      broken.value = true // 保留上一帧, 由调用方灰屏标注
    } finally {
      inFlight = false
    }
  }

  onMounted(() => {
    tick()
    timer = setInterval(tick, intervalMs)
  })
  onBeforeUnmount(() => {
    clearInterval(timer)
    if (currentUrl) URL.revokeObjectURL(currentUrl)
  })

  return { src, broken }
}
