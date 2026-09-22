/**
 * backendTarget — 解析「后端在哪」的唯一判据（纯函数，便于单测）。
 *
 * 三种运行形态，各自要的答案不一样：
 *
 * | 形态 | 页面怎么加载 | API 该打给谁 | 视频流该打给谁 |
 * |---|---|---|---|
 * | Electron 出厂壳 | `file://` | `http://localhost:8001` | 同上 |
 * | Vite 开发服务器 | `http://<host>:6001` | `http://<host>:8001` | 空（走 Vite 代理） |
 * | 一体机浏览器（一拖多） | `http://<工作站IP>:8001` | 空（同源） | 空（同源） |
 *
 * 一拖多的关键：一体机页面是工作站用 HTTP 送出来的，API 必须走**同源相对路径**。
 * 老逻辑在非桌面环境硬编码 `http://localhost:8001`，一体机会把请求打给自己那台
 * 机器的 8001（根本没后端）→ 整页 Network Error。这是本模块存在的理由。
 *
 * 开发态之所以不用同源：Vite 端口上还挂着 MJPEG 长连接，浏览器对单 origin 只有
 * 6 条 HTTP/1.1 连接，API 混进去会被流饿死。分成两个 origin 各自一套连接池。
 * 生产同源没这个问题：一台一体机只看自己一路流，剩 5 条连接够用。
 */

// 开发态后端端口（与 vite.config.js 的 proxy target 对齐）。
// 换端口走 VITE_API_BASE_URL，不要改这里（副本 worktree 用 .env.development.local）。
export const DEV_BACKEND_PORT = 8001;
export const DEFAULT_BACKEND_HOST = 'http://localhost:8001';

function trimSlash(s) {
  return (s || '').replace(/\/+$/, '');
}

/**
 * 页面是否跑在桌面壳里（Electron 用 file:// 加载打包好的前端）。
 */
export function isDesktopContext({ href = '', userAgent = '' } = {}) {
  if (href.startsWith('file://')) return true;
  return userAgent.toLowerCase().includes('electron');
}

/**
 * 视频流 / 快照等非 `/api/v1` 端点的 host 前缀。返回 `''` 表示同源。
 */
export function resolveBackendHost({
  envBase = '', href = '', userAgent = '',
} = {}) {
  if (envBase && /^https?:\/\//i.test(envBase)) {
    try {
      const url = new URL(envBase);
      return `${url.protocol}//${url.host}`;
    } catch {
      // 配歪了就往下走默认判据
    }
  }
  // 浏览器一律同源：开发态走 Vite 代理，生产态就是工作站自己。
  // 这样一体机换 IP / 换端口 / 走 nginx 都不用改前端配置。
  return isDesktopContext({ href, userAgent }) ? DEFAULT_BACKEND_HOST : '';
}

/**
 * axios 的 baseURL。
 */
export function resolveApiBaseURL({
  envBase = '', href = '', userAgent = '', protocol = 'http:', hostname = 'localhost',
  isDev = false,
} = {}) {
  if (envBase) return trimSlash(envBase);
  if (isDesktopContext({ href, userAgent })) return `${DEFAULT_BACKEND_HOST}/api/v1`;
  if (isDev) {
    // 开发服务器：后端在同一台机器的 8001，但要用**页面的 hostname**而不是
    // localhost —— 否则从别的机器开 http://<开发机IP>:6001 时，API 会被打到
    // 访问者自己的 localhost 上。
    const host = hostname || 'localhost';
    return `${protocol}//${host}:${DEV_BACKEND_PORT}/api/v1`;
  }
  // 生产浏览器：同源相对路径（一体机场景）
  return '/api/v1';
}
