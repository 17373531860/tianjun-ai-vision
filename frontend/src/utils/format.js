// 后端 SQLite 的 created_at / received_at / pushed_at 等字段
// 都是 UTC 时间（来自 server_default=func.now()），但 isoformat()
// 输出时不带时区后缀，例如 "2026-04-21T22:28:19"。
// 直接 new Date() 会按浏览器本地时区解析，导致显示晚 8 小时。
// 这里统一当作 UTC 解析，再按浏览器时区显示（国内就是北京时间）。
function toDate(input) {
  if (input == null || input === '') return null
  if (input instanceof Date) return input
  if (typeof input === 'number') return new Date(input)
  let s = String(input)
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(s)
  if (!hasTz && /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s)) {
    s = s.replace(' ', 'T') + 'Z'
  }
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}

export function formatDate(input) {
  const d = toDate(input)
  return d ? d.toLocaleString() : '-'
}

// yyyy-MM-dd HH:mm:ss（北京时区；实际按浏览器时区）
export function formatTime(input) {
  const d = toDate(input)
  if (!d) return '-'
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

export function formatCoordinate(x, y) {
  return `(${x.toFixed(2)}, ${y.toFixed(2)})`;
}
