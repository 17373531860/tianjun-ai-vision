// =====================================================================
// Splash preload (v3.8.2)
// ---------------------------------------------------------------------
// 给 splash renderer 暴露最小化 IPC 桥:
//   - getWorkstationConfig(): 读 backend/data/workstation_config.json
//                              拿 ch1.usb_device_id 锁定手势识别用的摄像头
//   - onBackendLog(cb):       订阅后端 stdout/stderr 实时日志
//   - onBackendReady(cb):     收到后端健康检查通过事件
//   - notifySplashFinished(): 告诉主进程 splash 播放完毕，关 splash + 显示主窗
// =====================================================================

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('splashAPI', {
  getWorkstationConfig: () => ipcRenderer.invoke('splash:get-workstation-config'),

  onBackendLog: (callback) => {
    ipcRenderer.on('splash:backend-log', (_event, payload) => {
      try { callback(payload); } catch (e) { console.error('[splashAPI] onBackendLog cb err:', e); }
    });
  },

  onBackendReady: (callback) => {
    ipcRenderer.on('splash:backend-ready', () => {
      try { callback(); } catch (e) { console.error('[splashAPI] onBackendReady cb err:', e); }
    });
  },

  notifySplashFinished: () => ipcRenderer.send('splash:finished'),
});
