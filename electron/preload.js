const { contextBridge, ipcRenderer } = require('electron');

// 暴露安全的 API 给渲染进程
contextBridge.exposeInMainWorld('electronAPI', {
  getAppInfo: () => ipcRenderer.invoke('get-app-info'),
  getBackendUrl: () => ipcRenderer.invoke('get-backend-url'),
  getLicenseStatus: () => ipcRenderer.invoke('get-license-status'),
  importLicense: () => ipcRenderer.invoke('import-license'),
  onLicenseActivated: (callback) => ipcRenderer.on('license-activated', callback),
  platform: process.platform,
  isElectron: true,
});
