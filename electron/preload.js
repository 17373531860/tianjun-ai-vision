const { contextBridge, ipcRenderer } = require('electron');

// 暴露安全的 API 给渲染进程
contextBridge.exposeInMainWorld('electronAPI', {
  getAppInfo: () => ipcRenderer.invoke('get-app-info'),
  getBackendUrl: () => ipcRenderer.invoke('get-backend-url'),
  getLicenseStatus: () => ipcRenderer.invoke('get-license-status'),
  // v3.10.2: machineId 指纹诊断 — 设置页 / 关于页可调, 显示哪些硬件源生效
  getMachineIdReport: () => ipcRenderer.invoke('get-machine-id-report'),
  importLicense: () => ipcRenderer.invoke('import-license'),
  onLicenseActivated: (callback) => ipcRenderer.on('license-activated', callback),
  // v3.8.2: 全屏 + 无边框模式下没有窗口×按钮, 前端 Navbar 的"退出"按钮走这里
  // 触发主进程 mainWindow.close → 已注册的 startGracefulShutdown 8 步关机流程
  gracefulQuit: () => ipcRenderer.invoke('app:graceful-quit'),
  // v3.10.x: 窗口控制 — 最小化收任务栏 + 全屏热切
  // 设置 → 显示设置里的"最小化界面" / "全屏" 按钮调这两个
  minimizeWindow: () => ipcRenderer.invoke('window:minimize'),
  setFullScreen: (fullscreen) => ipcRenderer.invoke('window:set-fullscreen', !!fullscreen),
  // 调试设置页"打开日志目录": logs/ 下有 electron.log(主进程) + backend.log(后端全量)
  openLogsDir: () => ipcRenderer.invoke('app:open-logs-dir'),
  platform: process.platform,
  isElectron: true,
});
