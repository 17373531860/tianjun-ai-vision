const { app, BrowserWindow, ipcMain, dialog, Menu } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const BackendManager = require('./backend-manager');
const LicenseManager = require('./license-manager');

// v3.8.2: 工业部署默认杀掉应用菜单栏（File / Edit / View / Window / Help）
// 工控机用户不需要这些；DevTools 仍可通过 Ctrl+Shift+I 打开
Menu.setApplicationMenu(null);

let licenseManager = null;
let isLicensed = false;

// ===== 文件日志：把 console.error / console.warn 复制一份到磁盘 =====
// 解决"出错只在控制台、客户机器没有保留任何痕迹"的痛点。
// 单文件最大 10MB，超出则滚动一次（.1.log → 删除，当前 → .1.log）。
function setupFileLogger() {
  try {
    const logDir = path.join(app.getPath('userData'), 'logs');
    fs.mkdirSync(logDir, { recursive: true });
    const logPath = path.join(logDir, 'electron.log');

    // 启动时检查滚动
    try {
      const st = fs.statSync(logPath);
      if (st.size > 10 * 1024 * 1024) {
        const old = path.join(logDir, 'electron.1.log');
        try { fs.unlinkSync(old); } catch (_e) { /* 不存在 */ }
        try { fs.renameSync(logPath, old); } catch (_e) { /* 重命名失败也不致命 */ }
      }
    } catch (_e) { /* 文件不存在，首次运行 */ }

    const stream = fs.createWriteStream(logPath, { flags: 'a' });
    const writeLine = (level, args) => {
      try {
        const ts = new Date().toISOString();
        const line = args.map(a => {
          if (a instanceof Error) return a.stack || a.message;
          if (typeof a === 'object') {
            try { return JSON.stringify(a); } catch (_) { return String(a); }
          }
          return String(a);
        }).join(' ');
        stream.write(`[${ts}] ${level} ${line}\n`);
      } catch (_e) { /* 日志写入失败时静默，不能反过来再 console.error 造成无限递归 */ }
    };
    const origError = console.error.bind(console);
    const origWarn = console.warn.bind(console);
    console.error = (...args) => { writeLine('ERROR', args); origError(...args); };
    console.warn = (...args) => { writeLine('WARN ', args); origWarn(...args); };
    writeLine('INFO ', [`Electron 启动 (pid=${process.pid}, version=${app.getVersion()})`]);
  } catch (e) {
    // 如果连初始化都失败，至少保留控制台输出，不影响主流程
    console.error('[App] 文件日志初始化失败:', e && e.message);
  }
}
app.on('ready', setupFileLogger);
// 早期错误（ready 之前）也记下来：先注入临时缓冲
const _earlyErrorBuffer = [];
const _earlyError = console.error.bind(console);
const _earlyWarn = console.warn.bind(console);
console.error = (...args) => { _earlyErrorBuffer.push({ lvl: 'ERROR', args }); _earlyError(...args); };
console.warn = (...args) => { _earlyErrorBuffer.push({ lvl: 'WARN ', args }); _earlyWarn(...args); };
app.on('ready', () => {
  // setupFileLogger 已替换 console.error/warn，回灌缓冲
  for (const { lvl, args } of _earlyErrorBuffer) {
    if (lvl === 'ERROR') console.error('[startup-buffered]', ...args);
    else console.warn('[startup-buffered]', ...args);
  }
  _earlyErrorBuffer.length = 0;
});

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
let splashWindow = null;        // v3.8.2: 新版赛博 splash 窗口
let shutdownWindow = null;
let backendManager = null;
let isQuitting = false;
let shutdownCancelled = false;
let renderGoneReloadTimer = null;
let unresponsiveReloadTimer = null;
let gpuCrashReloadTimer = null;
let splashCloseTimer = null;
let splashFinishedByRenderer = false;   // v3.8.2: splash 端是否已通过 IPC 通知"播完了"
const managedTimeouts = new Set();

function setManagedTimeout(callback, delayMs) {
  const timer = setTimeout(() => {
    managedTimeouts.delete(timer);
    callback();
  }, delayMs);
  managedTimeouts.add(timer);
  return timer;
}

function clearManagedTimeout(timer) {
  if (!timer) return;
  clearTimeout(timer);
  managedTimeouts.delete(timer);
}

function clearAllManagedTimeouts() {
  for (const timer of managedTimeouts) {
    clearTimeout(timer);
  }
  managedTimeouts.clear();
}

function clearReloadTimers() {
  if (renderGoneReloadTimer) {
    clearManagedTimeout(renderGoneReloadTimer);
    renderGoneReloadTimer = null;
  }
  if (unresponsiveReloadTimer) {
    clearManagedTimeout(unresponsiveReloadTimer);
    unresponsiveReloadTimer = null;
  }
  if (gpuCrashReloadTimer) {
    clearManagedTimeout(gpuCrashReloadTimer);
    gpuCrashReloadTimer = null;
  }
}

function scheduleMainWindowReload(reason, delayMs, timerKey) {
  if (isQuitting || !mainWindow || mainWindow.isDestroyed()) return;

  if (timerKey === 'render') {
    if (renderGoneReloadTimer) clearManagedTimeout(renderGoneReloadTimer);
    renderGoneReloadTimer = setManagedTimeout(() => {
      renderGoneReloadTimer = null;
      if (isQuitting || !mainWindow || mainWindow.isDestroyed()) return;
      console.log(`[App] ${reason}，正在重载渲染进程...`);
      try { mainWindow.webContents.reload(); } catch (e) { console.error('[App] 重载失败:', e); }
    }, delayMs);
    return;
  }

  if (timerKey === 'unresponsive') {
    if (unresponsiveReloadTimer) clearManagedTimeout(unresponsiveReloadTimer);
    unresponsiveReloadTimer = setManagedTimeout(() => {
      unresponsiveReloadTimer = null;
      if (isQuitting || !mainWindow || mainWindow.isDestroyed()) return;
      console.log(`[App] ${reason}，正在重载渲染进程...`);
      try { mainWindow.webContents.reload(); } catch (e) { console.error('[App] 重载失败:', e); }
    }, delayMs);
    return;
  }

  if (gpuCrashReloadTimer) clearManagedTimeout(gpuCrashReloadTimer);
  gpuCrashReloadTimer = setManagedTimeout(() => {
    gpuCrashReloadTimer = null;
    if (isQuitting || !mainWindow || mainWindow.isDestroyed()) return;
    console.log(`[App] ${reason}，正在重载渲染进程...`);
    try { mainWindow.webContents.reload(); } catch (e) { console.error('[App] 重载失败:', e); }
  }, delayMs);
}

// 应用配置
const CONFIG = {
  appName: '天军科技AI视觉检测系统',
  backendPort: 8001,
  backendHost: 'localhost',
  frontendPort: 6001,
  frontendHost: 'localhost',
  isDev: !app.isPackaged,
};

function getFrontendDevURL() {
  const envUrl = process.env.FRONTEND_DEV_URL || process.env.VITE_DEV_SERVER_URL;
  if (envUrl && /^https?:\/\//i.test(envUrl)) {
    return envUrl;
  }
  return `http://${CONFIG.frontendHost}:${CONFIG.frontendPort}`;
}

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
    fullscreen: true,          // v3.8.2: 工业部署默认全屏（取代 1600x900 窗口模式）
    frame: false,              // v3.8.2: 杀 Windows 标题栏（截图框出来的第一条）
    title: CONFIG.appName,
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: {
      webSecurity: false,  // 允许加载本地文件
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
    show: false,
    backgroundColor: '#02060c',  // v3.8.2: 跟 splash 同色，关 splash → show 主窗时不闪
  });

  mainWindow.setMenuBarVisibility(false);  // v3.8.2: 杀菜单栏（截图框出来的第二条）
  
  // 加载前端页面
  if (CONFIG.isDev) {
    // 开发模式：加载 Vite 开发服务器
    mainWindow.loadURL(getFrontendDevURL());
    mainWindow.webContents.openDevTools();
  } else {
    // 生产模式：加载打包后的前端文件
    const indexPath = getResourcePath('app', 'dist', 'index.html');
    mainWindow.loadFile(indexPath);
  }
  
  // v3.8.2: 主窗准备好不立即显示, 等 splash 完成播放后通过 IPC 显示。
  // 这样后端启动 / 前端 Vue 加载 都在 splash 后台进行, 用户看到的是连贯过场。
  mainWindow.once('ready-to-show', () => {
    maybeShowMainWindow();
  });

  // Renderer crash recovery: auto-reload when the Chromium renderer dies
  mainWindow.webContents.on('render-process-gone', (event, details) => {
    const mem = process.memoryUsage();
    console.error(`[App] ===== 渲染进程崩溃 =====`);
    console.error(`[App] 原因: ${details.reason}`);
    console.error(`[App] 退出码: ${details.exitCode}`);
    console.error(`[App] 主进程内存: RSS=${(mem.rss/1048576).toFixed(1)}MB, Heap=${(mem.heapUsed/1048576).toFixed(1)}/${(mem.heapTotal/1048576).toFixed(1)}MB`);
    console.error(`[App] 时间: ${new Date().toLocaleString()}`);
    console.error(`[App] ===========================`);
    scheduleMainWindowReload('渲染进程崩溃', 1000, 'render');
  });

  mainWindow.webContents.on('unresponsive', () => {
    const mem = process.memoryUsage();
    console.warn(`[App] ===== 渲染进程无响应 =====`);
    console.warn(`[App] 主进程内存: RSS=${(mem.rss/1048576).toFixed(1)}MB, Heap=${(mem.heapUsed/1048576).toFixed(1)}/${(mem.heapTotal/1048576).toFixed(1)}MB`);
    console.warn(`[App] 时间: ${new Date().toLocaleString()}`);
    console.warn(`[App] 5秒后将自动重载...`);
    console.warn(`[App] =============================`);
    scheduleMainWindowReload('渲染进程无响应', 5000, 'unresponsive');
  });

  mainWindow.webContents.on('responsive', () => {
    console.log('[App] 渲染进程恢复响应');
  });

  // 监控渲染进程页面加载状态
  mainWindow.webContents.on('did-finish-load', () => {
    console.log(`[App] ✓ 页面加载完成: ${mainWindow.webContents.getURL()}`);
  });

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL) => {
    console.error(`[App] ✗ 页面加载失败: ${validatedURL}, 错误: ${errorDescription} (${errorCode})`);
  });

  mainWindow.webContents.on('console-message', (event, level, message, line, sourceId) => {
    if (message.includes('[⬛')) {
      const levels = ['DEBUG', 'INFO', 'WARN', 'ERROR'];
      console.log(`[Renderer:${levels[level] || level}] ${message}`);
    }
  });
  
  // 拦截窗口关闭事件
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      startGracefulShutdown();
    }
  });

  mainWindow.on('closed', () => {
    clearReloadTimers();
    mainWindow = null;
  });
}

// 创建启动画面（v3.8.2 全新 cyber splash: 粒子球 + 手势 + 真实后端日志驱动进度条）
function createSplashWindow() {
  const splash = new BrowserWindow({
    fullscreen: true,                // 全屏（取代旧 400x300 弹窗模式）
    frame: false,
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: false,
    icon: path.join(__dirname, 'build', 'icon.png'),
    backgroundColor: '#02060c',      // 跟 splash CSS --cyber-deep 同色
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'splash', 'preload.js'),
      // splash 需要 file:// 协议下能 ES module + getUserMedia + AudioContext
      webSecurity: false,
    },
  });

  splash.loadFile(path.join(__dirname, 'splash', 'index.html'));
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
  clearReloadTimers();
  if (splashCloseTimer) {
    clearManagedTimeout(splashCloseTimer);
    splashCloseTimer = null;
  }
  clearAllManagedTimeouts();
  
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
  const mem = process.memoryUsage();
  console.error(`[App] ===== 子进程退出 =====`);
  console.error(`[App] 类型: ${details.type}, 原因: ${details.reason}, 退出码: ${details.exitCode}`);
  console.error(`[App] 进程名: ${details.name || '未知'}`);
  console.error(`[App] 主进程内存: RSS=${(mem.rss/1048576).toFixed(1)}MB`);
  console.error(`[App] 时间: ${new Date().toLocaleString()}`);
  console.error(`[App] ==========================`);
  if (details.type === 'GPU' && mainWindow && !mainWindow.isDestroyed() && !isQuitting) {
    console.log('[App] GPU进程崩溃, 1.5秒后重载渲染进程...');
    scheduleMainWindowReload('GPU进程崩溃', 1500, 'gpu');
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
    mainWindow.once('ready-to-show', () => mainWindow.show()); // 未授权页直接显示，绕过 splash
    return;
  }

  // v3.8.2: 全新赛博 splash, 全屏覆盖, 接 BackendManager 日志实时驱动进度
  splashWindow = createSplashWindow();
  splashFinishedByRenderer = false;

  // 把后端日志和 ready 事件转发给 splash renderer
  initBackendManager();
  const forwardStdout = (msg) => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.webContents.send('splash:backend-log', { stream: 'stdout', msg });
    }
  };
  const forwardStderr = (msg) => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.webContents.send('splash:backend-log', { stream: 'stderr', msg });
    }
  };
  const forwardReady = () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.webContents.send('splash:backend-ready');
    }
  };
  backendManager.on('stdout', forwardStdout);
  backendManager.on('stderr', forwardStderr);
  backendManager.once('ready', forwardReady);

  try {
    // 后端启动期间, splash 自顾自播放粒子球 + 等用户握拳; 进度由 stdout 行数驱动
    await startBackend();
    // 后端 ready 后立即创建主窗后台预热(show:false), 真正显示交给 splash:finished
    if (!mainWindow) createWindow();
  } catch (error) {
    console.error(`[App] Failed to start: ${error.message}`);
    if (splashWindow && !splashWindow.isDestroyed()) splashWindow.close();
    splashWindow = null;
    dialog.showErrorBox('启动失败', `无法启动应用: ${error.message}`);
    app.quit();
  }
});

// =====================================================================
// v3.8.2: Splash 与主窗的 IPC 协议
// ---------------------------------------------------------------------
//   splash:get-workstation-config — splash 读 ch1.usb_device_id 锁手势摄像头
//   splash:finished               — splash 端完成播放 + 进度满, 通知关 splash + 显示主窗
//   app:graceful-quit             — 前端 Navbar "退出" 按钮触发 8 步关机流程
// =====================================================================
ipcMain.handle('splash:get-workstation-config', () => {
  try {
    const cfgPath = path.join(__dirname, '..', 'backend', 'data', 'workstation_config.json');
    if (!fs.existsSync(cfgPath)) return {};
    return JSON.parse(fs.readFileSync(cfgPath, 'utf-8'));
  } catch (e) {
    console.warn('[Splash] 读 workstation_config.json 失败:', e.message);
    return {};
  }
});

ipcMain.on('splash:finished', () => {
  splashFinishedByRenderer = true;
  console.log('[App] splash 端通知播放完毕, 准备关闭 splash + 显示主窗');
  if (splashWindow && !splashWindow.isDestroyed()) {
    try { splashWindow.close(); } catch (e) { console.warn('[App] 关 splash 失败:', e.message); }
    splashWindow = null;
  }
  maybeShowMainWindow();
});

ipcMain.handle('app:graceful-quit', () => {
  console.log('[App] 收到前端优雅退出请求, 触发 mainWindow.close → graceful shutdown');
  if (mainWindow && !mainWindow.isDestroyed()) {
    // 主窗 close 事件已经被拦截 → startGracefulShutdown 8 步流程
    mainWindow.close();
  } else {
    app.quit();
  }
  return { ok: true };
});

// 主窗 ready + splash finished 双满足才显示主窗; 也容忍 splash 失败时强制显示
function maybeShowMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (!splashFinishedByRenderer) {
    // splash 还没结束, 主窗先后台预热不显示
    return;
  }
  try {
    mainWindow.show();
    mainWindow.focus();
  } catch (e) {
    console.warn('[App] 显示主窗失败:', e.message);
  }
}

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
    clearReloadTimers();
    if (splashCloseTimer) {
      clearManagedTimeout(splashCloseTimer);
      splashCloseTimer = null;
    }
    clearAllManagedTimeouts();
    
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
