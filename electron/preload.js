const { contextBridge, ipcRenderer } = require('electron');

// 暴露安全的 API 给渲染进程
contextBridge.exposeInMainWorld('electronAPI', {
  // 获取应用信息
  getAppInfo: () => ipcRenderer.invoke('get-app-info'),
  
  // 获取后端 URL
  getBackendUrl: () => ipcRenderer.invoke('get-backend-url'),
  
  // 平台信息
  platform: process.platform,
  
  // 是否在 Electron 环境中
  isElectron: true,
});
