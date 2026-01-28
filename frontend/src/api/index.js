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

// 获取 API 基础 URL（每次创建请求时动态获取）
function getBaseURL() {
  const isDesktop = isDesktopApp();
  console.log('[API] isDesktop:', isDesktop, 'href:', window.location.href);
  
  // 优先使用环境变量
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL;
  }
  
  // 桌面应用：直接连接本地后端（使用完整 URL）
  if (isDesktop) {
    const url = BACKEND_URL + '/api/v1';
    console.log('[API] Using desktop URL:', url);
    return url;
  }
  
  // Web 开发环境：使用代理
  return '/api/v1';
}

// 获取后端主机地址（用于视频流等非 API 请求）
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
  (config) => {
    // 在桌面应用中，确保使用完整 URL
    if (typeof window !== 'undefined') {
      const isFileProtocol = window.location.protocol === 'file:' || 
                             window.location.href.startsWith('file:');
      
      if (isFileProtocol && config.baseURL && !config.baseURL.startsWith('http')) {
        // 如果是 file:// 协议且 baseURL 不是完整 URL，修正它
        config.baseURL = BACKEND_URL + '/api/v1';
        console.log('[API Interceptor] Fixed baseURL to:', config.baseURL);
      }
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
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
