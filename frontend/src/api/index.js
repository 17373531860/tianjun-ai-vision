import axios from 'axios';

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

// 请求拦截器: 有 token 就加 Authorization header
api.interceptors.request.use(
  (config) => {
    const token = readToken();
    if (token) {
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器: 401 = token 失效, 清掉 + 跳 /login (D3 启用状态下)
api.interceptors.response.use(
  (response) => response,
  (error) => {
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
