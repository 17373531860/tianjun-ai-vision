import axios from 'axios';

// 后端地址常量
const BACKEND_URL = 'http://localhost:8001';

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

// API requests always go directly to backend, bypassing Vite proxy.
// The MJPEG long-lived stream stalls proxied API calls, so we must
// connect to the backend directly for all API requests.
function getBaseURL() {
  return BACKEND_URL + '/api/v1';
}

// MJPEG video stream uses Vite proxy in dev mode (same-origin, reliable
// browser rendering) and direct backend connection in Electron/desktop.
// This is safe because API calls already bypass the proxy, so the MJPEG
// long-lived stream no longer blocks API requests.
export function getBackendHost() {
  return isDesktopApp() ? BACKEND_URL : '';
}

const baseURL = getBaseURL();
console.log('[API] Final baseURL:', baseURL);

const api = axios.create({
  baseURL: getBaseURL(),
  timeout: 30000,
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
