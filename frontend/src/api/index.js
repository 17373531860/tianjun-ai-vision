import axios from 'axios';

// 检测是否在 Electron 环境中运行
const isElectron = typeof window !== 'undefined' && window.electronAPI?.isElectron;

// 动态获取 API 基础 URL
function getBaseURL() {
  // 优先使用环境变量
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL;
  }
  
  // Electron 生产环境：直接连接本地后端
  if (isElectron) {
    return 'http://localhost:8001/api/v1';
  }
  
  // Web 开发环境：使用代理
  return '/api/v1';
}

// 获取后端主机地址（用于视频流等非 API 请求）
export function getBackendHost() {
  if (isElectron) {
    return 'http://localhost:8001';
  }
  // Web 开发环境：使用相对路径（通过代理）
  return '';
}

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
    // 可以在这里添加 token 等认证信息
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
