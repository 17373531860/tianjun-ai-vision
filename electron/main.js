const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const BackendManager = require('./backend-manager');

// 保持对窗口对象的全局引用
let mainWindow = null;
let backendManager = null;
let isQuitting = false;

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
  });
  
  // 监听后端事件
  backendManager.on('ready', () => {
    console.log('[App] Backend is ready');
  });
  
  backendManager.on('error', (err) => {
    console.error('[App] Backend error:', err.message);
  });
  
  backendManager.on('exit', ({ code, signal }) => {
    if (!isQuitting && code !== 0) {
      dialog.showErrorBox('后端服务错误', `后端服务意外退出 (code: ${code})`);
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

// 停止后端服务
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

// 应用启动
app.whenReady().then(async () => {
  console.log(`[App] Starting ${CONFIG.appName}...`);
  console.log(`[App] Is packaged: ${app.isPackaged}`);
  console.log(`[App] Resources path: ${process.resourcesPath}`);
  
  // 显示启动画面
  const splash = createSplashWindow();
  
  try {
    // 启动后端服务
    await startBackend();
    
    // 创建主窗口
    createWindow();
    
    // 关闭启动画面
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

// 所有窗口关闭时退出（Windows & Linux）
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// macOS 点击 dock 图标重新创建窗口
app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});

// 应用退出前清理
app.on('before-quit', async (event) => {
  if (!isQuitting) {
    event.preventDefault();
    isQuitting = true;
    
    console.log('[App] Shutting down...');
    await stopBackend();
    
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
