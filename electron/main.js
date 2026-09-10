const { app, BrowserWindow, ipcMain, dialog, Menu, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const BackendManager = require('./backend-manager');
const LicenseManager = require('./license-manager');
const {
  advanceCrashWindow,
  buildKioskHash,
  buildMainWindowHash,
  buildStationAssignments,
  displayTargetsOverlap,
  enumerateDisplaysForApply,
  filterStationAssignmentsByChannelCount,
  isMainRenderer,
  isWindowOnOccupiedTarget,
  normalizeMultiMonitorConfig,
  partitionResolvedStationAssignments,
  resolveDisplayTarget,
  toDisplayDto,
} = require('./multi-monitor');

// v3.15.2: Windows 控制台默认 GBK(936) → 主进程 console.log 的中文 + 转发的后端日志
// 在用户手动从 cmd 启动时整屏乱码(澶╁啗...). 启动最早把当前控制台输出代码页切到
// UTF-8(65001), 让排错日志可读. 双击桌面图标无附加控制台时此调用在隐藏子控制台里
// 执行, 静默无副作用; 非 Windows 平台跳过.
if (process.platform === 'win32') {
  try {
    require('child_process').execSync('chcp 65001', { stdio: 'ignore' });
  } catch (_) { /* 无控制台 / 执行失败均忽略, 不影响主流程 */ }
}

// v3.8.2: 工业部署默认杀掉应用菜单栏（File / Edit / View / Window / Help）
// 工控机用户不需要这些；DevTools 仍可通过 Ctrl+Shift+I 打开
Menu.setApplicationMenu(null);

let licenseManager = null;
let isLicensed = false;

// ===== 文件日志：主进程全量日志 + 后端 stdout 全量日志落盘 =====
// 痛点根治：打包后双击启动看不到任何日志，客户机排障必须从 cmd 重启。
// 现在 console.log/warn/error 全量进 electron.log，后端 stdout/stderr 全量进
// backend.log（均 UTF-8 写入，绕开 Windows 控制台 GBK 乱码），出问题直接取文件。
// 单文件最大 10MB，超出滚动一次（.1.log → 删除，当前 → .1.log）。
let _logDir = null;
function getLogDir() {
  if (!_logDir) {
    _logDir = path.join(app.getPath('userData'), 'logs');
    fs.mkdirSync(_logDir, { recursive: true });
  }
  return _logDir;
}

// 通用滚动写文件器: electron.log / backend.log 共用一套实现
function makeRotatingWriter(fileName) {
  let stream = null;
  let written = 0;
  const MAX = 10 * 1024 * 1024;
  const open = () => {
    const logPath = path.join(getLogDir(), fileName);
    try {
      const st = fs.statSync(logPath);
      if (st.size > MAX) {
        const old = path.join(getLogDir(), fileName.replace(/\.log$/, '.1.log'));
        try { fs.unlinkSync(old); } catch (_e) { /* 不存在 */ }
        try { fs.renameSync(logPath, old); } catch (_e) { /* 重命名失败也不致命 */ }
      }
      written = (fs.existsSync(logPath) && fs.statSync(logPath).size) || 0;
    } catch (_e) { written = 0; /* 文件不存在，首次运行 */ }
    stream = fs.createWriteStream(logPath, { flags: 'a', encoding: 'utf-8' });
  };
  return (line) => {
    try {
      if (!stream) open();
      const buf = `[${new Date().toISOString()}] ${line}\n`;
      stream.write(buf);
      written += Buffer.byteLength(buf);
      if (written > MAX) { try { stream.end(); } catch (_e) {} stream = null; }
    } catch (_e) { /* 日志写入失败静默，不能反过来 console.error 造成无限递归 */ }
  };
}

let _backendLogWrite = null;
function backendLog(streamName, msg) {
  try {
    if (!_backendLogWrite) _backendLogWrite = makeRotatingWriter('backend.log');
    _backendLogWrite(`${streamName === 'stderr' ? 'ERR' : 'OUT'} ${msg}`);
  } catch (_e) { /* 静默 */ }
}

function setupFileLogger() {
  try {
    const writeRaw = makeRotatingWriter('electron.log');
    const writeLine = (level, args) => {
      try {
        const line = args.map(a => {
          if (a instanceof Error) return a.stack || a.message;
          if (typeof a === 'object') {
            try { return JSON.stringify(a); } catch (_) { return String(a); }
          }
          return String(a);
        }).join(' ');
        writeRaw(`${level} ${line}`);
      } catch (_e) { /* 静默 */ }
    };
    const origLog = console.log.bind(console);
    const origError = console.error.bind(console);
    const origWarn = console.warn.bind(console);
    // console.log 也落盘: [Backend] 行已单独进 backend.log, 这里跳过避免双写撑爆 electron.log
    console.log = (...args) => {
      const first = typeof args[0] === 'string' ? args[0] : '';
      if (!first.startsWith('[Backend]')) writeLine('INFO ', args);
      origLog(...args);
    };
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
let splashWindow = null;        // v3.8.2: 新版赛博 splash 窗口 (手势启动动画)
let legacySplashWindow = null;  // v3.12.x: 旧版简单 splash 窗口 (splash.enabled=false 时使用)
let shutdownWindow = null;
let backendManager = null;
let isQuitting = false;
let shutdownCancelled = false;
const stationWindows = new Map();
let reusedMainWindowAssignment = null;
let mainWindowStationRouteTimer = null;
let mainWindowMinimizedForMultiMonitor = false;
let mainWindowParkedForMultiMonitor = false;
let mainWindowUrlBeforeMultiMonitor = '';
let multiMonitorOccupiedTargets = [];
let mainWindowAvoidanceTransition = false;
let renderGoneReloadTimer = null;
let unresponsiveReloadTimer = null;
let gpuCrashReloadTimer = null;
let splashCloseTimer = null;
let splashFinishedByRenderer = false;   // v3.8.2: splash 端是否已通过 IPC 通知"播完了"
let deepReadyGate = false;              // v3.23.x: 加深启动就绪门槛 (默认关), 启动早期从 workstation_config.json 读
let deepGateDowngradePending = false;   // v3.23.x: 加深门槛降级待通知 (后端起了但 DB 探测超时降级放行)
let deepGateDowngradeNotified = false;  // v3.23.x: 降级已通知前端 (只弹一次)
const managedTimeouts = new Set();

// v3.23.x: 把"加深就绪门槛降级"提示推给前端 (主窗就绪后弹一次 ElMessage)。
// 事件可能在主窗建好前就来, 故置 pending, 主窗 did-finish-load 时再 flush。
function flushDeepGateDowngradeNotice() {
  if (!deepGateDowngradePending || deepGateDowngradeNotified) return;
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const wc = mainWindow.webContents;
  if (!wc || wc.isLoading()) return;
  try {
    wc.send('startup:deep-gate-downgraded');
    deepGateDowngradeNotified = true;
  } catch (e) {
    console.warn('[App] 推送加深门槛降级提示失败:', e.message);
  }
}

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
    deepReadyGate,  // v3.23.x: 加深就绪门槛 (默认关), 决定就绪探针用浅探还是深探
  });
  
  // 监听后端事件
  backendManager.on('ready', () => {
    console.log('[App] Backend is ready');
  });

  // 后端 stdout/stderr 全量落盘 backend.log (UTF-8): 双击启动也能事后取到完整后端日志,
  // 不再需要"关软件从 cmd 重启"来抓现场
  backendManager.on('stdout', (msg) => backendLog('stdout', msg));
  backendManager.on('stderr', (msg) => backendLog('stderr', msg));
  
  backendManager.on('error', (err) => {
    console.error('[App] Backend error:', err.message);
  });
  
  // v3.29.0 看门狗: 后端进程意外退出不再直接退应用, 交由 BackendManager 自动拉起。
  // 仅当看门狗放弃 (restart-failed) 时, 才回退到"弹错误框 + 退出"的老行为。
  backendManager.on('exit', ({ code, signal }) => {
    if (!isQuitting) {
      console.warn(`[App] 后端进程退出 (code: ${code}, signal: ${signal}), 看门狗接管`);
    }
  });

  backendManager.on('restarting', ({ attempt, delay }) => {
    console.warn(`[App] 看门狗: 后端异常, ${delay}ms 后第 ${attempt} 次自动恢复`);
  });

  backendManager.on('restarted', ({ attempt }) => {
    console.log(`[App] 看门狗: 后端已自动恢复 (第 ${attempt} 次), 通知前端`);
    // 轻提示前端"已恢复, 请重新开始检测" (恢复后是全新后端进程, 不在检测态)
    if (mainWindow && !mainWindow.isDestroyed()) {
      const wc = mainWindow.webContents;
      if (wc && !wc.isLoading()) {
        try { wc.send('backend:recovered'); } catch (e) { /* 忽略 */ }
      }
    }
  });

  backendManager.on('restart-failed', ({ code }) => {
    if (isQuitting) return;
    dialog.showErrorBox('后端服务错误',
      `后端服务多次异常退出, 自动恢复失败 (code: ${code})。\n应用将退出, 请重启软件。`);
    isQuitting = true;
    if (shutdownWindow && !shutdownWindow.isDestroyed()) {
      shutdownWindow.close();
    }
    app.exit(1);
  });
  
  backendManager.on('unhealthy', () => {
    console.warn('[App] Backend health check failed');
  });

  // v3.23.x: 加深门槛降级 (uvicorn 起了但 DB 探测超时) → 标记待通知, 主窗就绪后弹一次
  backendManager.on('deep-gate-downgraded', () => {
    console.warn('[App] 加深就绪门槛降级放行, 将提示前端');
    deepGateDowngradePending = true;
    flushDeepGateDowngradeNotice();
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

// v3.10.x: 读 workstation_config.json (主进程启动早期, 比前端先, 必须直读 JSON)
// 返回值:
//   splash.enabled (默认 false), splash.* (相机/超时, 透传给 splash renderer)
//   window.fullscreen (默认 false, 客户要求默认窗口模式带原生标题栏)
function readWorkstationConfig() {
  // 安装版用户数据目录: userData/workstation_config.json
  // 开发版回退优先序: backend/ → backend/data/ (取决于 TIANJUN_DATA_DIR 是否设置)
  // 见 backend/core/config.py: DATA_DIR 默认 == BASE_DIR == backend/
  const candidates = [
    path.join(app.getPath('userData'), 'workstation_config.json'),
    path.join(__dirname, '..', 'backend', 'workstation_config.json'),
    path.join(__dirname, '..', 'backend', 'data', 'workstation_config.json'),
  ];
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) {
        return JSON.parse(fs.readFileSync(p, 'utf-8'));
      }
    } catch (e) {
      console.warn(`[App] 读 ${p} 失败: ${e.message}`);
    }
  }
  return {};
}

function getMainWindowDisplay(primaryDisplay = null) {
  const fallback = primaryDisplay || screen.getPrimaryDisplay();
  if (!mainWindow || mainWindow.isDestroyed()) return fallback;
  try {
    return screen.getDisplayMatching(mainWindow.getBounds()) || fallback;
  } catch (e) {
    console.warn(`[MultiMonitor] 定位主窗口显示器失败，回退 OS 主屏: ${e.message}`);
    return fallback;
  }
}

function getDisplayDtos() {
  const primary = screen.getPrimaryDisplay();
  const mainDisplay = getMainWindowDisplay(primary);
  return screen.getAllDisplays().map((display) => (
    toDisplayDto(display, primary.id, mainDisplay && mainDisplay.id)
  ));
}

function destroyStationWindows(reason = '布局关闭') {
  const removedCount = stationWindows.size;
  for (const [windowKey, entry] of stationWindows.entries()) {
    stationWindows.delete(windowKey);
    try {
      if (entry.window && !entry.window.isDestroyed()) entry.window.destroy();
    } catch (e) {
      console.warn(`[MultiMonitor] 关闭工位 ${entry.channelId} ${entry.role} 窗口失败: ${e.message}`);
    }
  }
  if (removedCount > 0) {
    console.log(`[MultiMonitor] 已清理 ${removedCount} 个工位窗口: ${reason}`);
  }
}

function getStationWindowDescriptors() {
  const descriptors = [...stationWindows.values()]
    .map((entry) => ({ channel_id: entry.channelId, role: entry.role }));
  if (reusedMainWindowAssignment) {
    descriptors.push({
      channel_id: reusedMainWindowAssignment.channelId,
      role: 'main',
      reused_main_window: true,
    });
  }
  return descriptors
    .sort((left, right) => (
      left.channel_id - right.channel_id
      || (left.role === 'main' ? -1 : 1)
    ));
}

function loadMainWindowApplicationUrl(savedUrl = '') {
  if (!mainWindow || mainWindow.isDestroyed()) return Promise.resolve();
  if (savedUrl && savedUrl !== 'about:blank') return mainWindow.loadURL(savedUrl);
  if (CONFIG.isDev) return mainWindow.loadURL(getFrontendDevURL());
  return mainWindow.loadFile(getResourcePath('app', 'dist', 'index.html'));
}

function loadMainWindowStationRoute(channelId) {
  if (!mainWindow || mainWindow.isDestroyed()) return Promise.resolve();
  const routeHash = buildMainWindowHash(channelId);
  if (CONFIG.isDev) {
    const base = getFrontendDevURL().replace(/\/$/, '');
    return mainWindow.loadURL(`${base}/#${routeHash}`);
  }
  return mainWindow.loadFile(getResourcePath('app', 'dist', 'index.html'), { hash: routeHash });
}

function clearMainWindowStationRouteTimer() {
  if (!mainWindowStationRouteTimer) return;
  clearManagedTimeout(mainWindowStationRouteTimer);
  mainWindowStationRouteTimer = null;
}

function placeMainWindowOnTarget(target) {
  if (!mainWindow || mainWindow.isDestroyed() || !target || !target.bounds) return;
  const targetBounds = target.bounds;
  try {
    const wasFullScreen = mainWindow.isFullScreen();
    if (wasFullScreen) {
      // Windows 全屏窗跨显示器时 setBounds 可能只改“退出全屏后的恢复位置”，
      // 先退出再回到全屏，保证热应用也落到映射的 OS 主屏。
      mainWindow.setFullScreen(false);
      mainWindow.setBounds(targetBounds, false);
      mainWindow.setFullScreen(true);
      return;
    }
    const currentBounds = mainWindow.getBounds();
    const width = Math.min(currentBounds.width, targetBounds.width);
    const height = Math.min(currentBounds.height, targetBounds.height);
    mainWindow.setBounds({
      x: targetBounds.x + Math.floor((targetBounds.width - width) / 2),
      y: targetBounds.y + Math.floor((targetBounds.height - height) / 2),
      width,
      height,
    }, false);
  } catch (e) {
    console.warn(`[MultiMonitor] 主窗口移动到映射显示器失败: ${e.message}`);
  }
}

function setReusedMainWindowAssignment(nextAssignment) {
  const previous = reusedMainWindowAssignment;
  const next = nextAssignment || null;
  const changed = (previous && previous.key) !== (next && next.key);
  reusedMainWindowAssignment = next;

  if (!mainWindow || mainWindow.isDestroyed() || isQuitting) return;
  if (!next) {
    clearMainWindowStationRouteTimer();
    if (!previous) return;
    try {
      const currentUrl = mainWindow.webContents && mainWindow.webContents.getURL
        ? mainWindow.webContents.getURL()
        : '';
      if (currentUrl.includes('station_view=1')) {
        Promise.resolve(loadMainWindowApplicationUrl())
          .catch((e) => console.warn(`[MultiMonitor] 恢复主应用普通路由失败: ${e.message}`));
      }
    } catch (e) {
      console.warn(`[MultiMonitor] 退出主窗口工位复用失败: ${e.message}`);
    }
    return;
  }

  // OS 主屏承担一个工位主屏时，复用正常主应用窗：保留 Layout/侧栏，且不再停车到 about:blank。
  mainWindowParkedForMultiMonitor = false;
  mainWindowMinimizedForMultiMonitor = false;
  mainWindowUrlBeforeMultiMonitor = '';
  try {
    placeMainWindowOnTarget(next.target);
    mainWindow.setSkipTaskbar(false);
    if (mainWindow.isMinimized()) mainWindow.restore();
    if (!mainWindow.isVisible()) mainWindow.show();
    mainWindow.focus();
  } catch (e) {
    console.warn(`[MultiMonitor] 恢复复用主窗口失败: ${e.message}`);
  }

  let currentUrl = '';
  try {
    currentUrl = mainWindow.webContents && mainWindow.webContents.getURL
      ? mainWindow.webContents.getURL()
      : '';
  } catch (_e) { /* 页面可能仍在初始化 */ }
  if (!changed && currentUrl && currentUrl !== 'about:blank') return;

  clearMainWindowStationRouteTimer();
  mainWindowStationRouteTimer = setManagedTimeout(() => {
    mainWindowStationRouteTimer = null;
    if (!mainWindow || mainWindow.isDestroyed() || isQuitting
        || !reusedMainWindowAssignment
        || reusedMainWindowAssignment.key !== next.key) return;
    Promise.resolve(loadMainWindowStationRoute(next.channelId))
      .catch((e) => console.error(`[MultiMonitor] 主窗口工位页加载失败: ${e.message}`));
  }, 50);
}

function parkMainWindowRendererForMultiMonitor() {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindowParkedForMultiMonitor) return false;
  const webContents = mainWindow.webContents;
  try {
    const currentUrl = webContents && webContents.getURL ? webContents.getURL() : '';
    if (currentUrl && currentUrl !== 'about:blank') mainWindowUrlBeforeMultiMonitor = currentUrl;
    mainWindowParkedForMultiMonitor = true;
    // 导航到空白页会立即取消当前页面的 fetch/MJPEG，并阻止最小化 renderer
    // 在后台继续拉全图；禁用布局时再恢复原 URL。
    if (webContents && webContents.stop) webContents.stop();
    Promise.resolve(mainWindow.loadURL('about:blank')).catch((e) => {
      console.warn(`[MultiMonitor] 主应用 renderer 停车页加载失败: ${e.message}`);
    });
    return true;
  } catch (e) {
    mainWindowParkedForMultiMonitor = false;
    console.warn(`[MultiMonitor] 主应用 renderer 停车失败: ${e.message}`);
    return false;
  }
}

function restoreMainWindowAfterMultiMonitor() {
  const shouldReveal = mainWindowMinimizedForMultiMonitor;
  const shouldRestoreRenderer = mainWindowParkedForMultiMonitor;
  if (!shouldReveal && !shouldRestoreRenderer) return;

  const savedUrl = mainWindowUrlBeforeMultiMonitor;
  mainWindowMinimizedForMultiMonitor = false;
  mainWindowParkedForMultiMonitor = false;
  mainWindowUrlBeforeMultiMonitor = '';
  if (!mainWindow || mainWindow.isDestroyed() || isQuitting) return;

  const reveal = () => {
    if (!shouldReveal || !mainWindow || mainWindow.isDestroyed() || isQuitting) return;
    try {
      mainWindow.setSkipTaskbar(false);
      if (mainWindow.isMinimized()) mainWindow.restore();
      if (!mainWindow.isVisible()) mainWindow.show();
      mainWindow.focus();
    } catch (e) {
      console.warn(`[MultiMonitor] 恢复主应用窗口失败: ${e.message}`);
    }
  };

  try {
    mainWindow.setSkipTaskbar(false);
    if (shouldRestoreRenderer) {
      Promise.resolve(loadMainWindowApplicationUrl(savedUrl))
        .catch((e) => console.warn(`[MultiMonitor] 恢复主应用页面失败: ${e.message}`))
        .finally(reveal);
    } else {
      reveal();
    }
  } catch (e) {
    console.warn(`[MultiMonitor] 恢复主应用页面失败: ${e.message}`);
    reveal();
  }
}

function enforceMainWindowAvoidance(reason = '窗口状态变化') {
  if (!mainWindow || mainWindow.isDestroyed() || isQuitting
      || reusedMainWindowAssignment || !multiMonitorOccupiedTargets.length) return false;
  let mainBounds = null;
  try {
    mainBounds = mainWindow.getBounds();
  } catch (e) {
    console.warn(`[MultiMonitor] 读取主应用窗口位置失败: ${e.message}`);
    return false;
  }
  if (!isWindowOnOccupiedTarget(mainBounds, multiMonitorOccupiedTargets)) return false;
  if (mainWindowAvoidanceTransition) return true;

  mainWindowAvoidanceTransition = true;
  try {
    const newlyParked = parkMainWindowRendererForMultiMonitor();
    mainWindow.setSkipTaskbar(true);
    if (!mainWindow.isMinimized()) {
      // BrowserWindow 已创建即可最小化；启动期无需先 show/showInactive，避免闪屏。
      mainWindow.minimize();
      mainWindowMinimizedForMultiMonitor = true;
    }
    if (newlyParked) {
      console.log(`[MultiMonitor] 主应用窗口与工位显示区域重叠，已停车并最小化避让 (${reason})`);
    }
    return true;
  } catch (e) {
    console.warn(`[MultiMonitor] 主应用窗口避让失败: ${e.message}`);
    return false;
  } finally {
    mainWindowAvoidanceTransition = false;
  }
}

function updateMainWindowAvoidanceTargets(occupiedTargets) {
  multiMonitorOccupiedTargets = Array.isArray(occupiedTargets) ? [...occupiedTargets] : [];
  if (!multiMonitorOccupiedTargets.length) {
    restoreMainWindowAfterMultiMonitor();
    return false;
  }
  if (enforceMainWindowAvoidance('应用多屏布局')) return true;
  restoreMainWindowAfterMultiMonitor();
  return false;
}

function loadStationRoute(window, channelId, role, auxViewMode = 'follow') {
  const routeHash = buildKioskHash(channelId, role, auxViewMode);
  if (CONFIG.isDev) {
    const base = getFrontendDevURL().replace(/\/$/, '');
    return window.loadURL(`${base}/#${routeHash}`);
  }
  const indexPath = getResourcePath('app', 'dist', 'index.html');
  return window.loadFile(indexPath, { hash: routeHash });
}

function createStationWindow(channelId, role, target, signature, auxViewMode = 'follow') {
  const bounds = target.bounds;
  const roleLabel = role === 'aux' ? '副屏' : '主屏';
  const windowKey = `${channelId}:${role}`;
  const stationWindow = new BrowserWindow({
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
    frame: false,
    fullscreen: false,
    show: false,
    closable: false,
    minimizable: false,
    maximizable: false,
    resizable: false,
    title: `${CONFIG.appName} - 工位 ${channelId + 1} ${roleLabel}`,
    icon: path.join(__dirname, 'build', 'icon.png'),
    backgroundColor: '#02060c',
    webPreferences: {
      webSecurity: false,
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });
  stationWindow.setMenuBarVisibility(false);

  const entry = {
    window: stationWindow,
    channelId,
    role,
    signature,
    reloadAttempts: 0,
    lastCrashAt: 0,
  };
  stationWindows.set(windowKey, entry);

  stationWindow.once('ready-to-show', () => {
    if (stationWindow.isDestroyed() || isQuitting) return;
    try {
      stationWindow.setBounds(bounds, false);
      stationWindow.setFullScreen(true);
      stationWindow.show();
      console.log(`[MultiMonitor] 工位 ${channelId} ${roleLabel}已显示 (${target.source})`);
    } catch (e) {
      console.error(`[MultiMonitor] 工位 ${channelId} ${roleLabel}钉屏失败: ${e.message}`);
    }
  });

  stationWindow.webContents.on('render-process-gone', (_event, details) => {
    if (isQuitting || stationWindow.isDestroyed()) return;
    const crashState = advanceCrashWindow(entry, Date.now());
    entry.reloadAttempts = crashState.reloadAttempts;
    entry.lastCrashAt = crashState.lastCrashAt;
    console.error(
      `[MultiMonitor] 工位 ${channelId} ${roleLabel} renderer 退出: ${details.reason}, `
      + `60 秒窗口内第 ${entry.reloadAttempts} 次`,
    );
    if (entry.reloadAttempts > 2) {
      console.error(`[MultiMonitor] 工位 ${channelId} ${roleLabel} renderer 60 秒内第 3 次失败，停止自动重载`);
      return;
    }
    setManagedTimeout(() => {
      if (!isQuitting && !stationWindow.isDestroyed()) {
        try { stationWindow.webContents.reload(); } catch (e) {
          console.error(`[MultiMonitor] 工位 ${channelId} renderer 重载失败: ${e.message}`);
        }
      }
    }, 1000);
  });
  stationWindow.on('closed', () => {
    if (stationWindows.get(windowKey) === entry) stationWindows.delete(windowKey);
  });

  loadStationRoute(stationWindow, channelId, role, auxViewMode).catch((e) => {
    console.error(`[MultiMonitor] 工位 ${channelId} ${roleLabel}页面加载失败: ${e.message}`);
  });
  return entry;
}

function applyMultiMonitorConfig(rawConfig) {
  const { config, warnings } = normalizeMultiMonitorConfig(rawConfig);
  if (!isLicensed) {
    destroyStationWindows('License 未通过');
    setReusedMainWindowAssignment(null);
    updateMainWindowAvoidanceTargets([]);
    return { ok: false, enabled: false, windows: [], warnings: [...warnings, 'License 未通过，未创建工位窗口'] };
  }
  if (!config.enabled) {
    destroyStationWindows('多屏模式关闭');
    setReusedMainWindowAssignment(null);
    updateMainWindowAvoidanceTargets([]);
    return { ok: true, enabled: false, readonly: config.readonly, windows: [], warnings };
  }

  const displayResult = enumerateDisplaysForApply(getDisplayDtos);
  const displays = displayResult.displays;
  let primaryDisplay = displays.find((display) => display.isPrimary) || null;
  if (!primaryDisplay) {
    try {
      const rawPrimaryDisplay = screen.getPrimaryDisplay();
      const currentMainDisplay = getMainWindowDisplay(rawPrimaryDisplay);
      primaryDisplay = toDisplayDto(
        rawPrimaryDisplay,
        rawPrimaryDisplay.id,
        currentMainDisplay && currentMainDisplay.id,
      );
    } catch (e) {
      warnings.push(`未能定位 OS 主显示器: ${e.message}`);
    }
  }
  warnings.push(...displayResult.warnings);
  if (displayResult.error) {
    console.warn(`[MultiMonitor] 枚举显示器失败: ${displayResult.error}; 按记忆/手工 bounds 降级`);
  }
  const occupiedTargets = [];
  const workstationConfig = readWorkstationConfig();
  const activeAssignmentResult = filterStationAssignmentsByChannelCount(
    buildStationAssignments(config.mapping),
    workstationConfig.channel_count,
  );
  for (const skippedChannelId of activeAssignmentResult.skippedChannelIds) {
    warnings.push(
      `工位 ${skippedChannelId} 超出当前 channel_count=${workstationConfig.channel_count}，保留配置但不创建窗口`,
    );
  }
  for (const stationAssignment of activeAssignmentResult.assignments) {
    const {
      key, channelId, role, assignment, auxViewMode,
    } = stationAssignment;
    const roleLabel = role === 'aux' ? '副屏' : '主屏';
    const target = resolveDisplayTarget(assignment, displays);
    if (!target || !target.bounds) {
      warnings.push(`工位 ${channelId} ${roleLabel}无可用显示器位置，已跳过`);
      continue;
    }
    if (target.warning) warnings.push(`工位 ${channelId} ${roleLabel}: ${target.warning}`);
    const conflict = occupiedTargets.find((occupied) => (
      displayTargetsOverlap(target, occupied.target)
    ));
    if (conflict) {
      const conflictRoleLabel = conflict.role === 'aux' ? '副屏' : '主屏';
      warnings.push(
        `工位 ${channelId} ${roleLabel}与工位 ${conflict.channelId} ${conflictRoleLabel}`
        + '显示区域重叠，已跳过',
      );
      continue;
    }
    const signature = JSON.stringify({
      bounds: target.bounds,
      role,
      ...(role === 'aux' ? { auxViewMode } : {}),
    });
    const resolvedEntry = {
      key, channelId, role, target, signature, auxViewMode,
    };
    occupiedTargets.push(resolvedEntry);
  }

  const partition = partitionResolvedStationAssignments(occupiedTargets, primaryDisplay);
  const reusedAssignment = partition.reusedMainAssignments[0] || null;
  const stationWindowDesired = new Map(
    partition.stationWindowAssignments.map((item) => [item.key, item]),
  );

  for (const [windowKey, entry] of stationWindows.entries()) {
    const next = stationWindowDesired.get(windowKey);
    if (!next || next.signature !== entry.signature || entry.window.isDestroyed()) {
      stationWindows.delete(windowKey);
      try {
        if (!entry.window.isDestroyed()) entry.window.destroy();
      } catch (e) {
        console.warn(`[MultiMonitor] 重建工位 ${entry.channelId} ${entry.role}窗口前清理失败: ${e.message}`);
      }
    }
  }

  // 先销毁 OS 主屏上旧的 kiosk 工位窗，再恢复带侧栏的主应用窗，避免热应用时短暂叠屏。
  setReusedMainWindowAssignment(reusedAssignment);
  updateMainWindowAvoidanceTargets(
    partition.stationWindowAssignments.map((item) => item.target),
  );

  for (const [windowKey, desiredEntry] of stationWindowDesired.entries()) {
    if (!stationWindows.has(windowKey)) {
      try {
        createStationWindow(
          desiredEntry.channelId,
          desiredEntry.role,
          desiredEntry.target,
          desiredEntry.signature,
          desiredEntry.auxViewMode,
        );
      } catch (e) {
        warnings.push(`工位 ${desiredEntry.channelId} ${desiredEntry.role}窗口创建失败: ${e.message}`);
      }
    }
  }

  for (const warning of warnings) console.warn(`[MultiMonitor] ${warning}`);
  return {
    ok: true,
    enabled: true,
    readonly: config.readonly,
    windows: getStationWindowDescriptors(),
    warnings,
  };
}

// 创建主窗口
// v3.10.x: 新增 opts.fullscreen 参数, 默认 false (窗口模式带标题栏)
function createWindow(opts = {}) {
  const fullscreen = !!opts.fullscreen;
  mainWindow = new BrowserWindow({
    // v3.10.x: 全屏 / 窗口模式由 workstation_config.window.fullscreen 控制, 默认窗口模式
    // 全屏 → frame:false 杀掉 Windows 标题栏 (kiosk 风格)
    // 窗口 → frame:true (默认), 给客户原生最小化/最大化/关闭三件套, 1600x900 居中
    fullscreen,
    frame: !fullscreen,
    width: fullscreen ? undefined : 1600,
    height: fullscreen ? undefined : 900,
    minWidth: 1024,
    minHeight: 720,
    center: !fullscreen,
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
  // 多屏启用后，Windows 任务栏恢复、最大化或拖动都可能让主窗重新压到工位屏。
  // 在窗口状态事件内同步复核；命中占用区就立即回停车页并重新最小化。
  for (const eventName of ['show', 'restore', 'maximize', 'move', 'resize', 'enter-full-screen']) {
    mainWindow.on(eventName, () => enforceMainWindowAvoidance(eventName));
  }
  
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

  // v3.23.x: 前端加载完后, 若加深门槛已降级, 补推一次提示
  mainWindow.webContents.on('did-finish-load', () => {
    flushDeepGateDowngradeNotice();
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

  // 渲染进程 console 转发到主进程终端.
  // v3.15.4: 扩展匹配 — 除了带 [⬛ 标记的主程序埋点, 额外转发所有含 Plugin /
  // PluginLoader / RendererDiag 的日志. 打包后主窗口走 file://, 前端 console 默认
  // 进不了启动终端 + 工业机开不了 F12, 这条转发是前端插件加载排障的命脉, 必须够宽.
  mainWindow.webContents.on('console-message', (event, level, message, line, sourceId) => {
    try {
      // error 级(level=3)无条件转发落盘 — 前端任何报错客户机都留痕; 其余按埋点标记过滤
      if (level === 3 || message.includes('[⬛') || /Plugin|PluginLoader|RendererDiag/i.test(message)) {
        const levels = ['DEBUG', 'INFO', 'WARN', 'ERROR'];
        console.log(`[Renderer:${levels[level] || level}] ${message}`);
      }
    } catch (e) { /* 转发失败不影响渲染进程 */ }
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

// v3.12.x: 旧版简单 splash (splash.enabled=false 时使用)
// 设计沿用 v3.8.1 之前的 splash.html: 400x300 圆角弹窗 + 蓝色渐变 + loading 进度条
// 用途: 后端启动期间提供等待视觉反馈, 避免"主窗 UI 已显示但后端 API 全部 404"的体验灾难
function createLegacySplashWindow() {
  const splash = new BrowserWindow({
    width: 400,
    height: 300,
    frame: false,
    transparent: true,            // splash.html body 用了圆角, 透明背景保证视觉无锯齿
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    center: true,
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  // 通过 url query 注入版本号, 避免 splash.html 硬编码导致版本漂移
  splash.loadFile(path.join(__dirname, 'splash.html'), {
    query: { v: app.getVersion() },
  });

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
    destroyStationWindows('后端未运行，直接退出');
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
  destroyStationWindows('完成关机流程');
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
  // v3.48.1: 3s→8s。stop() 内部最坏路径是"优雅等3s→软杀等5s→强杀",
  // 3s race 会在强杀执行前就 app.exit, 后端残留成僵尸(客户机每次启动都清残留)。
  try {
    await Promise.race([
      stopBackend(),
      new Promise(resolve => setTimeout(resolve, 8000))
    ]);
  } catch (e) {
    console.log('[App] Error stopping backend:', e.message);
  }
  
  // v3.48.1 最后兜底: 无论上面走到哪一步, 退出前同步确认后端已死,
  // 绝不把残留 python(占着 GPU 显存)留给下一次启动
  try {
    if (backendManager) backendManager.forceKillSync();
  } catch (e) {
    console.log('[App] forceKillSync error:', e.message);
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
  
  if (mainWindow && !mainWindow.isDestroyed() && !enforceMainWindowAvoidance('取消关机')) {
    mainWindow.show();
  }
}

// When a second instance is launched, focus the existing window instead
app.on('second-instance', () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (enforceMainWindowAvoidance('second-instance')) return;
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

  // v3.10.x: 启动早期读 workstation_config.json — splash.enabled / window.fullscreen
  // 都在主进程一次性决策, 重启生效 (热切只能切窗口模式, 不能切 splash 行为).
  const wsCfg = readWorkstationConfig();
  const splashCfg = (wsCfg && wsCfg.splash) || {};
  const winCfg = (wsCfg && wsCfg.window) || {};
  const splashEnabled = splashCfg.enabled === true;  // 默认 false
  const windowFullscreen = winCfg.fullscreen === true;  // 默认 false
  deepReadyGate = ((wsCfg && wsCfg.startup_ready_gate) || {}).enabled === true;  // 默认 false
  console.log(`[App] 启动配置: splash.enabled=${splashEnabled}, window.fullscreen=${windowFullscreen}, startup_ready_gate=${deepReadyGate}`);

  if (!isLicensed) {
    createWindow({ fullscreen: windowFullscreen });
    mainWindow.once('ready-to-show', () => mainWindow.show()); // 未授权页直接显示，绕过 splash
    return;
  }

  // v3.12.x: splash.enabled=false 时启用"旧版简单 splash" (loading 转圈 + 等后端 ready)
  // 修复 v3.10.x 的体验灾难: 当时直接 splashFinishedByRenderer=true + createWindow,
  // 导致前端 HTML 加载完就立刻 show, 而后端还在装 24 个模型, 用户看到 UI 但 API 全部 404.
  // 现在的两阶段: 显示 legacy splash → await startBackend() → 关 splash → show 主窗.
  if (!splashEnabled) {
    console.log('[App] splash.enabled=false, 启用旧版简单 splash (等待后端 ready)');
    legacySplashWindow = createLegacySplashWindow();
    // 默认 (门槛关): 主窗后台创建 + 后台加载前端 (createWindow 默认 show:false), 暂不显示;
    //   前端首屏请求会撞冷启动, 但 axios 层会自动退避重试补齐, 不空白。
    // 加深门槛开: 暂不创建主窗, 等后端深度就绪后再 createWindow, 让前端首屏请求直接
    //   打到已就绪后端 (进来即满, 连重试那一两秒都省)。代价: 主窗出现更晚。
    // splashFinishedByRenderer 保持 false 卡住 maybeShowMainWindow, 等后端 ready 才放行.
    if (!deepReadyGate) {
      createWindow({ fullscreen: windowFullscreen });
    }
    initBackendManager();
    try {
      await startBackend();
      // 后端 ready: (加深门槛时此刻才加载前端) 关 legacy splash → 放行 → show 主窗
      if (deepReadyGate && !mainWindow) {
        createWindow({ fullscreen: windowFullscreen });
      }
      if (legacySplashWindow && !legacySplashWindow.isDestroyed()) {
        try { legacySplashWindow.close(); } catch (e) { console.warn('[App] 关 legacy splash 失败:', e.message); }
      }
      legacySplashWindow = null;
      splashFinishedByRenderer = true;
      maybeShowMainWindow();
    } catch (error) {
      console.error(`[App] Failed to start: ${error.message}`);
      if (legacySplashWindow && !legacySplashWindow.isDestroyed()) {
        try { legacySplashWindow.close(); } catch (_e) { /* ignore */ }
      }
      legacySplashWindow = null;
      dialog.showErrorBox('启动失败', `无法启动应用: ${error.message}`);
      app.quit();
    }
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
    if (!mainWindow) createWindow({ fullscreen: windowFullscreen });
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

// ─────────────────────────────────────────────────────────────────────
// v3.10.x: 窗口控制 IPC (最小化 + 全屏热切)
// ---------------------------------------------------------------------
//   window:minimize      — 前端"最小化"按钮 → 主窗收进任务栏 (进程不退出)
//   window:set-fullscreen — 前端全屏开关热切, 同时持久化让下次启动生效
// ─────────────────────────────────────────────────────────────────────
ipcMain.handle('window:minimize', () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try {
      mainWindow.minimize();
      return { ok: true };
    } catch (e) {
      console.warn('[App] minimize 失败:', e.message);
      return { ok: false, error: e.message };
    }
  }
  return { ok: false, error: 'no mainWindow' };
});

ipcMain.handle('window:set-fullscreen', (_evt, fullscreen) => {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return { ok: false, error: 'no mainWindow' };
  }
  try {
    // 注: Electron 的 setFullScreen 在 frame:true 窗口上是 F11 模式
    // (会临时隐藏标题栏但保留进程), 退出全屏后标题栏恢复.
    // 跟启动时 frame:false 的 kiosk 全屏体验略有差异, 但满足"临时全屏"需求,
    // 客户要真 kiosk 全屏必须重启 (frame 是 BrowserWindow 启动时一次性参数).
    mainWindow.setFullScreen(!!fullscreen);
    return { ok: true, fullscreen: mainWindow.isFullScreen() };
  } catch (e) {
    console.warn('[App] setFullScreen 失败:', e.message);
    return { ok: false, error: e.message };
  }
});

ipcMain.handle('multi-monitor:get-displays', () => {
  try {
    return { ok: true, displays: getDisplayDtos() };
  } catch (e) {
    console.warn('[MultiMonitor] 枚举显示器失败:', e.message);
    return { ok: false, displays: [], error: e.message };
  }
});

ipcMain.handle('multi-monitor:apply', (_evt, rawConfig) => {
  if (!mainWindow || mainWindow.isDestroyed()
      || !isMainRenderer(_evt.sender, mainWindow.webContents)) {
    return {
      ok: false,
      enabled: false,
      windows: getStationWindowDescriptors(),
      warnings: [],
      error: '仅主窗口可应用多屏布局',
    };
  }
  try {
    return applyMultiMonitorConfig(rawConfig);
  } catch (e) {
    console.error('[MultiMonitor] 应用布局失败:', e);
    return { ok: false, enabled: false, windows: [], warnings: [], error: e.message };
  }
});

// 主窗 ready + splash finished 双满足才显示主窗; 也容忍 splash 失败时强制显示
function maybeShowMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (!splashFinishedByRenderer) {
    // splash 还没结束, 主窗先后台预热不显示
    return;
  }
  try {
    if (isLicensed) {
      const config = readWorkstationConfig();
      applyMultiMonitorConfig(config.multi_monitor);
    }
    if (!enforceMainWindowAvoidance('显示主窗') && !mainWindowMinimizedForMultiMonitor) {
      mainWindow.show();
      mainWindow.focus();
    }
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
  destroyStationWindows('应用退出');
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

// 调试设置页"打开日志目录"按钮: 直接弹文件管理器到 logs/ (electron.log + backend.log)
ipcMain.handle('app:open-logs-dir', async () => {
  try {
    const dir = getLogDir();
    const err = await require('electron').shell.openPath(dir);
    return { ok: !err, dir, error: err || undefined };
  } catch (e) {
    return { ok: false, error: e && e.message };
  }
});

ipcMain.handle('get-license-status', () => {
  if (!licenseManager) return { valid: false, reason: 'not_initialized' };
  return { valid: isLicensed, machineId: licenseManager.getMachineId(), info: licenseManager.getLicenseInfo() };
});

// v3.10.2: machineId 指纹诊断 — 现场工程师/客户支持可读, 排查"同 ID 多机"问题
ipcMain.handle('get-machine-id-report', () => {
  if (!licenseManager) return { error: 'not_initialized' };
  return licenseManager.getMachineIdReport();
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
    // 后端启动放后台, 立即把验签结果还给激活页 — 冷启动可能要几分钟,
    // 挂在 await 上会让激活按钮转圈转满整个后端启动 (客户现场实际投诉点)。
    // 启动完成 → license-activated 跳主页; 启动失败 → 事件 + 错误框, 不再静默吞掉。
    startBackend()
      .then(() => {
        applyMultiMonitorConfig(readWorkstationConfig().multi_monitor);
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('license-activated');
        }
      })
      .catch((err) => {
        console.error('[App] Failed to start backend after activation:', err.message);
        if (mainWindow && !mainWindow.isDestroyed()) {
          try {
            mainWindow.webContents.send('license-backend-start-failed', { message: err.message });
          } catch (_e) { /* 忽略 */ }
        }
        dialog.showErrorBox('启动失败',
          `授权已激活, 但后端服务启动失败:\n${err.message}\n\n请关闭软件后重新打开。`);
      });
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
