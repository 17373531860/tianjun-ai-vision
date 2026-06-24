import axios from 'axios';
import { dbg, dbgOn } from '@/utils/debug';

// 后端默认地址（当环境变量未配置时使用）
const DEFAULT_BACKEND_HOST = 'http://localhost:8001';
const ENV_API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

// 检测是否在桌面应用环境中运行（每次调用时检测）
function isDesktopApp() {
  if (typeof window === 'undefined') return false;
  
  // 最可靠的方式：检查 URL 协议
  const href = window.location.href;
  if (href.startsWith('file://') || href.startsWith('file:///')) {
    return true;
  }
  
  // 备用检测：userAgent
  if (navigator.userAgent.toLowerCase().includes('electron')) {
    return true;
  }
  
  return false;
}

function trimSlash(s) {
  return (s || '').replace(/\/+$/, '');
}

function getBaseURL() {
  // 优先使用环境变量（支持部署时配置）
  if (ENV_API_BASE_URL) return trimSlash(ENV_API_BASE_URL);
  // 未配置时回退默认本机后端
  return `${DEFAULT_BACKEND_HOST}/api/v1`;
}

// MJPEG video stream uses Vite proxy in dev mode (same-origin, reliable
// browser rendering) and direct backend connection in Electron/desktop.
// This is safe because API calls already bypass the proxy, so the MJPEG
// long-lived stream no longer blocks API requests.
export function getBackendHost() {
  // 若配置了绝对 API 地址，尝试从中提取 host（用于视频流等非 /api/v1 地址）
  if (ENV_API_BASE_URL && /^https?:\/\//i.test(ENV_API_BASE_URL)) {
    try {
      const url = new URL(ENV_API_BASE_URL);
      return `${url.protocol}//${url.host}`;
    } catch (_) {
      // ignore, fallback below
    }
  }
  return isDesktopApp() ? DEFAULT_BACKEND_HOST : '';
}

const baseURL = getBaseURL();
console.log('[API] Final baseURL:', baseURL);

const api = axios.create({
  baseURL: getBaseURL(),
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json'
  }
});

// ============================================================
// v3.10.0 用户系统: token 注入 + 401 自动登出
//
// 设计原则:
// 1. token 从 localStorage('tianjun:auth_token') 直读, 不依赖 store
//    (避免循环依赖: useAuthStore 也 import 这个 api 实例)
// 2. 401 = 当前 token 失效 → 清 token + localStorage + 跳 /login
//    但仅在 auth_enabled=true 状态下才跳转, 否则只清 token
// 3. 登录 / 启用鉴权这两个端点本身不带 token 也能用, 所以请求拦截器
//    "有就加, 没有就跳过" 不强制
// ============================================================

const AUTH_TOKEN_KEY = 'tianjun:auth_token';

function readToken() {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

function clearToken() {
  try {
    localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {}
}

// 调试设置 'api.request': 高频轮询端点开了开关也跳过, 否则日志全被轮询刷屏
const DBG_SKIP_URLS = ['/source/detection/results', '/source/status', '/debug/logs', '/debug/flags', '/debug/client-log', '/system/device-status'];
function dbgSkip(url) {
  return DBG_SKIP_URLS.some(u => (url || '').includes(u));
}

// ============================================================
// v3.23.x: 冷启动网络错误自动重试 (默认开, 覆盖所有走本实例的后端调用)
//
// 根因: 工控机开机时前端 file:// 页面加载远早于后端就绪 (后端要 CUDA 预热 + 大模型
//   加载几十秒). 落地即发的首屏请求 (项目列表 / GPU / 摄像头 / 扫码枪配置 / MES ...)
//   撞上后端没起 → Network Error → 各调用方 catch 后放弃且不再补 → 项目页空白、
//   USB 扫码枪 cached.enabled 停在 false 静默失效不拉工单。"重启就不行、重开又好"
//   就是这一拉撞没撞上冷启动窗口的随机性。
// 修法: 在 axios 层对"没有 HTTP 响应"的网络错误做退避重试, 直到后端就绪或超预算。
//   - 只重试网络错误 (请求没到服务器, 重发安全幂等); 4xx/5xx 已到服务器, 不重试。
//   - 一处生效, 自动覆盖所有客户在用的接口, 不漏。
//   - 测试环境 (vitest MODE=test) 不重试, 避免模拟网络错误的用例挂 90s。
// ============================================================
const RETRY_MAX_WINDOW_MS = 180000;  // 覆盖工控机冷启动 + CUDA 预热 + 大模型加载最坏情况 (实测 ~56s 的 3 倍余量)
const RETRY_INTERVAL_MS = 1500;     // 退避间隔 (固定, 简单稳妥)
const RETRY_ENABLED = (import.meta?.env?.MODE !== 'test');

// 总闸: 只在"后端从未就绪过"的冷启动窗口内才重试。收到过任意一次成功响应即说明后端
// 已起, 此后再出网络错误属运行中掉线 —— 该快速失败 + 让轮询靠下个 tick 自愈, 而不是
// 死重试堆积。这样无论轮询跳过名单有没有列全 / 将来新增多少轮询, 运行中崩溃都不会堆积。
let backendEverReady = false;

function isNetworkError(error) {
  // 只重试"连不上本机后端"这一种 (冷启动后端端口还没起 → 连接被拒)。
  // 刻意排除以下情况, 避免误重试:
  //   - 有 HTTP 响应 (error.response): 后端已应答, 含 MES 等外部失败后端回的 success:false /
  //     4xx/5xx —— 这类是"业务结果", 立刻返回给调用方, 绝不重发。
  //   - 请求超时 (ECONNABORTED): 后端在、只是某次调用慢 (如卡在死掉的外部 MES 上),
  //     重发只会把慢操作再打一遍, 不重试。
  //   - 主动取消 (ERR_CANCELED): 调用方/路由切走主动 abort, 不重试。
  if (!error || error.response) return false;
  if (error.code === 'ECONNABORTED' || error.code === 'ERR_CANCELED') return false;
  return error.code === 'ERR_NETWORK' || error.message === 'Network Error';
}

// 重试只为"一次性首屏加载"服务。高频轮询端点本身挂在定时器上、每个 tick 就自带重发,
// 一旦也卷进重试, 运行中后端崩了会出现"每个 tick 各自重试 90s + setInterval 仍不停发新
// tick" → 几百个并发请求堆积、后端恢复瞬间灌爆 (请求风暴)。所以轮询端点一律跳过重试,
// 它们靠下个 tick 自愈即可, 无需补。
const RETRY_SKIP_URLS = [
  '/source/detection/results',  // 监控页 ~200ms (最高频)
  '/source/status',             // 监控页状态轮询
  '/system/device-status',      // 设备状态面板
  '/debug/logs', '/debug/flags', '/debug/client-log',  // 调试中心轮询 + 日志回传
  '/scanner/status', '/scanner/latest', '/scanner/logs',  // 扫码面板 5s 轮询
];
function retrySkip(url) {
  const u = url || '';
  if (RETRY_SKIP_URLS.some(s => u.includes(s))) return true;
  // 包装结算进度轮询 /packaging-flows/{id}/state (1.5s) 跳过; 其余 packaging CRUD 仍重试
  if (u.includes('/packaging-flows/') && u.endsWith('/state')) return true;
  return false;
}

function canRetry(config) {
  if (!RETRY_ENABLED || !config || config.__noRetry) return false;
  // 后端已就绪过 → 运行中掉线, 不重试 (快速失败 + 轮询自愈)
  if (backendEverReady) return false;
  // 关机端点不重试 (退出时不该续命)
  if ((config.url || '').includes('/shutdown')) return false;
  // 高频轮询端点不重试 (自带 tick 重发, 重试只会堆积)
  if (retrySkip(config.url)) return false;
  return true;
}

// 请求拦截器: 有 token 就加 Authorization header
api.interceptors.request.use(
  (config) => {
    const token = readToken();
    if (token) {
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${token}`;
    }
    // 调试埋点: 记录请求发出时刻 (响应侧算耗时)
    if (dbgOn('api.request') && !dbgSkip(config.url)) {
      config.__dbgT0 = Date.now();
      const params = config.params ? ` params=${JSON.stringify(config.params).slice(0, 200)}` : '';
      dbg('api.request', `→ ${String(config.method || 'get').toUpperCase()} ${config.url}`, params.trim());
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器: 401 = token 失效, 清掉 + 跳 /login (D3 启用状态下)
api.interceptors.response.use(
  (response) => {
    backendEverReady = true;  // 收到过成功响应 → 后端已起, 关掉冷启动重试总闸
    const cfg = response.config || {};
    if (cfg.__dbgT0) {
      dbg('api.request', `← ${response.status} ${cfg.url}`, `${Date.now() - cfg.__dbgT0}ms`);
    }
    return response;
  },
  (error) => {
    // 后端给出任何 HTTP 响应 (含 4xx/5xx) 都说明它已起 → 关冷启动重试总闸
    if (error && error.response) backendEverReady = true;
    // --- 冷启动网络错误退避重试 (排在 401/日志之前; 网络错误本就没 response) ---
    const rcfg = error && error.config;
    if (isNetworkError(error) && canRetry(rcfg)) {
      if (rcfg.__retryStart === undefined) rcfg.__retryStart = Date.now();
      if (Date.now() - rcfg.__retryStart < RETRY_MAX_WINDOW_MS) {
        rcfg.__retryCount = (rcfg.__retryCount || 0) + 1;
        if (rcfg.__retryCount === 1 || rcfg.__retryCount % 5 === 0) {
          console.warn(`[API Retry] 网络错误重试 #${rcfg.__retryCount} ${rcfg.url} (${error.message})`);
        }
        return new Promise((resolve) => setTimeout(resolve, RETRY_INTERVAL_MS)).then(() => api(rcfg));
      }
      console.error(`[API Retry] 放弃重试 ${rcfg.url}: 超 ${RETRY_MAX_WINDOW_MS}ms 仍网络错误`);
    }
    // 调试埋点: 失败请求无论哪个开关都按 api.request 记 (这是排障最常用的信息)
    if (dbgOn('api.request')) {
      const cfg = (error.config || (error.response && error.response.config)) || {};
      if (!dbgSkip(cfg.url)) {
        const st = error.response ? error.response.status : 'NETWORK';
        const detail = error.response ? JSON.stringify(error.response.data).slice(0, 300) : error.message;
        dbg('api.request', `✗ ${st} ${cfg.url}`, detail);
      }
    }
    if (error.response) {
      const { status, data, config } = error.response;
      console.error(`API Error ${status}:`, data);

      if (status === 401) {
        // 跳过 login 端点自己的 401 (登录失败提示交给调用方)
        const isLoginCall = config?.url?.includes('/auth/login');
        if (!isLoginCall) {
          clearToken();
          // 仅在桌面/浏览器环境下跳转, 防止单元测试触发
          if (typeof window !== 'undefined' && window.location) {
            // 用 hash 路由跳转 (避免依赖 router 实例)
            const hash = window.location.hash || '';
            if (!hash.startsWith('#/login') && !hash.startsWith('#/activation')) {
              window.location.hash = '#/login';
            }
          }
        }
      }
      // 403 不动 token, 仅打日志 (调用方按 detail 提示用户)
    } else if (error.request) {
      console.error('Network Error:', error.message);
    }
    return Promise.reject(error);
  }
);

export default api;
