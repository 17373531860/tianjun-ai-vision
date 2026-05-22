const { contextBridge, ipcRenderer } = require('electron');

// 暴露安全的 API 给渲染进程
contextBridge.exposeInMainWorld('electronAPI', {
  getAppInfo: () => ipcRenderer.invoke('get-app-info'),
  getBackendUrl: () => ipcRenderer.invoke('get-backend-url'),
  getLicenseStatus: () => ipcRenderer.invoke('get-license-status'),
  importLicense: () => ipcRenderer.invoke('import-license'),
  onLicenseActivated: (callback) => ipcRenderer.on('license-activated', callback),
  // v3.8.2: 全屏 + 无边框模式下没有窗口×按钮, 前端 Navbar 的"退出"按钮走这里
  // 触发主进程 mainWindow.close → 已注册的 startGracefulShutdown 8 步关机流程
  gracefulQuit: () => ipcRenderer.invoke('app:graceful-quit'),
  platform: process.platform,
  isElectron: true,
});
