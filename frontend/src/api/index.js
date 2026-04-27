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

// 请求拦截器
api.interceptors.request.use(
  (config) => config,
  (error) => Promise.reject(error)
);

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response;
  },
  (error) => {
    // 统一错误处理
    if (error.response) {
      const { status, data } = error.response;
      console.error(`API Error ${status}:`, data);
      
      switch (status) {
        case 401:
          // 未授权
          break;
        case 403:
          // 禁止访问
          break;
        case 404:
          // 资源不存在
          break;
        case 500:
          // 服务器错误
          break;
      }
    } else if (error.request) {
      console.error('Network Error:', error.message);
    }
    return Promise.reject(error);
  }
);

export default api;
