const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const http = require('http');
const BackendManager = require('./backend-manager');
const LicenseManager = require('./license-manager');

let licenseManager = null;
let isLicensed = false;

// GPU stability: disable shader disk cache, enable GPU restart on crash
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');
app.commandLine.appendSwitch('disk-cache-size', '0');
app.commandLine.appendSwitch('enable-features', 'VizDisplayCompositor');
app.commandLine.appendSwitch('disable-features', 'GpuProcessHighPriorityWin');

// Memory limits to prevent renderer OOM from long-running MJPEG decode
app.commandLine.appendSwitch('js-flags', '--max-old-space-size=512');
app.commandLine.appendSwitch('max-old-space-size', '512');
app.commandLine.appendSwitch('max-decoded-image-bytes', '268435456');
app.commandLine.appendSwitch('force-gpu-mem-available-mb', '256');

// Single instance lock: prevent multiple app instances
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
}

// 保持对窗口对象的全局引用
let mainWindow = null;
let shutdownWindow = null;
let backendManager = null;
let isQuitting = false;
let shutdownCancelled = false;

// 应用配置
const CONFIG = {
  appName: '天军科技AI视觉检测系统',
  backendPort: 8001,
  backendHost: 'localhost',
  isDev: !app.isPackaged,
};

// 获取资源路径
function getResourcePath(...segments) {
  if (CONFIG.isDev) {
    // 开发模式：使用项目目录
    return path.join(__dirname, '..', ...segments);
  } else {
    // 生产模式：使用打包后的资源目录
    return path.join(process.resourcesPath, ...segments);
  }
}

// 初始化后端管理器
function initBackendManager() {
  backendManager = new BackendManager({
    port: CONFIG.backendPort,
    host: CONFIG.backendHost,
    isDev: CONFIG.isDev,
    resourcesPath: CONFIG.isDev ? path.join(__dirname, '..') : process.resourcesPath,
    appPath: path.join(__dirname, '..'),
    userDataPath: app.getPath('userData'),
  });
  
  // 监听后端事件
  backendManager.on('ready', () => {
    console.log('[App] Backend is ready');
  });
  
  backendManager.on('error', (err) => {
    console.error('[App] Backend error:', err.message);
  });
  
  backendManager.on('exit', ({ code, signal }) => {
    // 只有在非正常退出流程中才显示错误
    if (!isQuitting && code !== 0 && code !== null) {
      dialog.showErrorBox('后端服务错误', `后端服务意外退出 (code: ${code})`);
      // 后端崩溃了，直接退出应用
      isQuitting = true;
      if (shutdownWindow && !shutdownWindow.isDestroyed()) {
        shutdownWindow.close();
      }
      app.exit(1);
    }
  });
  
  backendManager.on('unhealthy', () => {
    console.warn('[App] Backend health check failed');
  });
  
  return backendManager;
}

// 启动后端服务
async function startBackend() {
  if (!backendManager) {
    initBackendManager();
  }
  return backendManager.start();
}

// 停止后端服务（优雅关闭）
async function stopBackend() {
  if (backendManager) {
    await backendManager.stop();
  }
}

// 创建主窗口
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 900,
    minWidth: 1200,
    minHeight: 700,
    title: CONFIG.appName,
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: {
      webSecurity: false,  // 允许加载本地文件
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
    show: false,
    backgroundColor: '#1a1a2e',
  });
  
  // 移除菜单栏（可选）
  // mainWindow.setMenuBarVisibility(false);
  
  // 加载前端页面
  if (CONFIG.isDev) {
    // 开发模式：加载 Vite 开发服务器
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    // 生产模式：加载打包后的前端文件
    const indexPath = getResourcePath('app', 'dist', 'index.html');
    mainWindow.loadFile(indexPath);
  }
  
  // 窗口准备好后显示
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // Renderer crash recovery: auto-reload when the Chromium renderer dies
  // (e.g. OOM from long-running MJPEG decode, GPU process crash, etc.)
  mainWindow.webContents.on('render-process-gone', (event, details) => {
    console.error(`[App] Renderer crashed! reason=${details.reason}, exitCode=${details.exitCode}`);
    if (!isQuitting && mainWindow && !mainWindow.isDestroyed()) {
      setTimeout(() => {
        console.log('[App] Reloading after renderer crash...');
        mainWindow.webContents.reload();
      }, 1000);
    }
  });

  mainWindow.webContents.on('unresponsive', () => {
    console.warn('[App] Renderer unresponsive, will reload in 5s if still stuck...');
    setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed() && !isQuitting) {
        try {
          mainWindow.webContents.reload();
        } catch (e) {
          console.error('[App] Failed to reload unresponsive renderer:', e);
        }
      }
    }, 5000);
  });

  mainWindow.webContents.on('responsive', () => {
    console.log('[App] Renderer became responsive again');
  });
  
  // 拦截窗口关闭事件
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      startGracefulShutdown();
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// 创建启动画面
function createSplashWindow() {
  const splash = new BrowserWindow({
    width: 400,
    height: 300,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
  
  // 加载启动画面 HTML
  splash.loadFile(path.join(__dirname, 'splash.html'));
  
  return splash;
}

// 创建关闭进度窗口
function createShutdownWindow() {
  shutdownWindow = new BrowserWindow({
    width: 420,
    height: 320,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    closable: false,
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });
  
  shutdownWindow.loadFile(path.join(__dirname, 'shutdown.html'));
  
  shutdownWindow.on('closed', () => {
    shutdownWindow = null;
  });
  
  return shutdownWindow;
}

// 发送HTTP请求到后端
function sendBackendRequest(method, path, timeout = 5000) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: '127.0.0.1',
      port: CONFIG.backendPort,
      path: path,
      method: method,
      timeout: timeout,
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          resolve(data);
        }
      });
    });
    
    req.on('error', (e) => reject(e));
    req.on('timeout', () => {
      req.destroy();
      reject(new Error('Request timeout'));
    });
    
    req.end();
  });
}

// 优雅关闭流程
async function startGracefulShutdown() {
  if (isQuitting) return;
  
  console.log('[App] Starting graceful shutdown...');
  shutdownCancelled = false;
  
  // 检查后端是否还在运行
  const backendRunning = backendManager && backendManager.isRunning;
  
  if (!backendRunning) {
    // 后端已经不在运行，直接退出
    console.log('[App] Backend not running, exiting directly...');
    isQuitting = true;
    app.exit(0);
    return;
  }
  
  // 隐藏主窗口，显示关闭进度窗口
  if (mainWindow) {
    mainWindow.hide();
  }
  createShutdownWindow();
}

// 执行关闭步骤
async function executeShutdown() {
  const steps = [
    'stop_detection',
    'save_counters', 
    'end_cycle',
    'end_session',
    'stop_recording',
    'release_camera',
    'release_model',
    'cleanup'
  ];
  
  const completedSteps = [];
  
  try {
    for (let i = 0; i < steps.length; i++) {
      if (shutdownCancelled) {
        console.log('[App] Shutdown cancelled by user');
        return false;
      }
      
      const step = steps[i];
      const progress = Math.round(((i + 0.5) / steps.length) * 100);
      
      // 更新进度
      if (shutdownWindow) {
        shutdownWindow.webContents.send('shutdown-progress', {
          progress,
          currentStep: step,
          completedSteps: [...completedSteps],
          message: `正在处理...`
        });
      }
      
      // 执行步骤
      try {
        await sendBackendRequest('POST', `/api/v1/source/shutdown/step/${step}`, 10000);
      } catch (e) {
        console.log(`[App] Step ${step} failed or skipped: ${e.message}`);
        // 继续执行，不中断
      }
      
      completedSteps.push(step);
      
      // 更新完成状态
      if (shutdownWindow) {
        shutdownWindow.webContents.send('shutdown-progress', {
          progress: Math.round(((i + 1) / steps.length) * 100),
          currentStep: steps[i + 1] || null,
          completedSteps: [...completedSteps],
          message: completedSteps.length === steps.length ? '关闭完成' : '正在处理...'
        });
      }
    }
    
    return true;
  } catch (error) {
    console.error('[App] Shutdown error:', error);
    if (shutdownWindow) {
      shutdownWindow.webContents.send('shutdown-error', error.message);
    }
    return false;
  }
}

// 完成关闭
async function finishShutdown(forced = false) {
  isQuitting = true;
  
  console.log(`[App] Finishing shutdown (forced: ${forced})...`);
  
  // 关闭进度窗口
  if (shutdownWindow && !shutdownWindow.isDestroyed()) {
    try {
      shutdownWindow.webContents.send('shutdown-complete');
      await new Promise(resolve => setTimeout(resolve, 300));
      shutdownWindow.close();
    } catch (e) {
      console.log('[App] Error closing shutdown window:', e.message);
    }
    shutdownWindow = null;
  }
  
  // 停止后端（带超时保护）
  try {
    await Promise.race([
      stopBackend(),
      new Promise(resolve => setTimeout(resolve, 3000)) // 最多等3秒
    ]);
  } catch (e) {
    console.log('[App] Error stopping backend:', e.message);
  }
  
  // 确保退出应用
  console.log('[App] Quitting application...');
  app.exit(0);
}

// 取消关闭
function cancelShutdown() {
  console.log('[App] Shutdown cancelled');
  shutdownCancelled = true;
  
  if (shutdownWindow) {
    shutdownWindow.close();
  }
  
  if (mainWindow) {
    mainWindow.show();
  }
}

// When a second instance is launched, focus the existing window instead
app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

// GPU / utility process crash handler
app.on('child-process-gone', (event, details) => {
  console.error(`[App] Child process gone: type=${details.type}, reason=${details.reason}`);
  if (details.type === 'GPU' && mainWindow && !mainWindow.isDestroyed() && !isQuitting) {
    console.log('[App] GPU process crashed, reloading renderer...');
    setTimeout(() => mainWindow.webContents.reload(), 1500);
  }
});

// 应用启动
app.whenReady().then(async () => {
  console.log(`[App] Starting ${CONFIG.appName}...`);
  console.log(`[App] Is packaged: ${app.isPackaged}`);
  console.log(`[App] Resources path: ${process.resourcesPath}`);
  
  const resPath = CONFIG.isDev ? path.join(__dirname, '..') : process.resourcesPath;
  licenseManager = new LicenseManager(app.getPath('userData'), resPath);

  if (CONFIG.isDev) {
    console.log('[App] Dev mode — skipping license check');
    console.log(`[App] Machine ID: ${licenseManager.getMachineId()}`);
    isLicensed = true;
  } else {
    licenseManager.tryAutoInstall();
    const result = licenseManager.verify();
    console.log(`[App] License check: ${result.message}`);
    isLicensed = result.valid;
  }

  if (!isLicensed) {
    createWindow();
    return;
  }

  const splash = createSplashWindow();
  
  try {
    await startBackend();
    createWindow();
    
    setTimeout(() => {
      splash.close();
    }, 500);
    
  } catch (error) {
    console.error(`[App] Failed to start: ${error.message}`);
    splash.close();
    dialog.showErrorBox('启动失败', `无法启动应用: ${error.message}`);
    app.quit();
  }
});

// 所有窗口关闭时（这里不做任何事，关闭由 graceful shutdown 处理）
app.on('window-all-closed', () => {
  // 不做任何事，让 graceful shutdown 流程处理
  if (process.platform !== 'darwin' && isQuitting) {
    // 已经在退出流程中，不需要额外处理
  }
});

// macOS 点击 dock 图标重新创建窗口
app.on('activate', () => {
  if (mainWindow === null && !isQuitting) {
    createWindow();
  }
});

// 应用退出前清理（作为最后的保障）
app.on('before-quit', async (event) => {
  if (!isQuitting) {
    event.preventDefault();
    isQuitting = true;
    
    console.log('[App] Before quit - stopping backend...');
    try {
      await stopBackend();
    } catch (error) {
      console.error('[App] Error stopping backend:', error);
    }
    
    app.quit();
  }
});

// IPC 通信处理
ipcMain.handle('get-app-info', () => {
  return {
    name: CONFIG.appName,
    version: app.getVersion(),
    isDev: CONFIG.isDev,
  };
});

ipcMain.handle('get-backend-url', () => {
  return `http://${CONFIG.backendHost}:${CONFIG.backendPort}`;
});

ipcMain.handle('get-license-status', () => {
  if (!licenseManager) return { valid: false, reason: 'not_initialized' };
  return { valid: isLicensed, machineId: licenseManager.getMachineId(), info: licenseManager.getLicenseInfo() };
});

ipcMain.handle('import-license', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '导入授权文件',
    filters: [{ name: '授权文件', extensions: ['lic'] }],
    properties: ['openFile']
  });
  if (result.canceled || !result.filePaths.length) {
    return { valid: false, reason: 'cancelled', message: '已取消' };
  }
  const verifyResult = licenseManager.importLicense(result.filePaths[0]);
  if (verifyResult.valid) {
    isLicensed = true;
    console.log('[App] License activated, starting backend...');
    try {
      await startBackend();
      mainWindow.webContents.send('license-activated');
    } catch (err) {
      console.error('[App] Failed to start backend after activation:', err.message);
    }
  }
  return verifyResult;
});

// 关闭进度窗口的 IPC 处理
ipcMain.on('shutdown-window-ready', async () => {
  // 窗口准备好后开始执行关闭
  const success = await executeShutdown();
  if (success && !shutdownCancelled) {
    await finishShutdown(false);
  }
});

ipcMain.on('shutdown-cancel', () => {
  cancelShutdown();
});

ipcMain.on('shutdown-force', async () => {
  await finishShutdown(true);
});
