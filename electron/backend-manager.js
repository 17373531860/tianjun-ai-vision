/**
 * 后端进程管理器
 * 负责启动、监控和停止 FastAPI 后端服务
 */

const { spawn, execSync } = require('child_process');
const path = require('path');
const http = require('http');
const fs = require('fs');
const EventEmitter = require('events');

class BackendManager extends EventEmitter {
  constructor(options = {}) {
    super();
    
    this.options = {
      port: options.port || 8001,
      host: options.host || 'localhost',
      startupTimeout: options.startupTimeout || 120000,  // 增加到 120 秒
      healthCheckInterval: options.healthCheckInterval || 5000,
      isDev: options.isDev || false,
      resourcesPath: options.resourcesPath || '',
      appPath: options.appPath || '',
    };
    
    this.process = null;
    this.isRunning = false;
    this.healthCheckTimer = null;
    this.startTime = null;
  }
  
  /**
   * 获取 Python 可执行文件路径
   */
  getPythonPath() {
    if (this.options.isDev) {
      // 开发模式：使用系统 conda 环境
      const condaEnv = process.env.CONDA_PREFIX || 
        path.join(process.env.HOME || process.env.USERPROFILE, 'anaconda3/envs/tianjun');
      
      return process.platform === 'win32'
        ? path.join(condaEnv, 'python.exe')
        : path.join(condaEnv, 'bin', 'python');
    } else {
      // 生产模式：使用打包的 Python 环境
      const pythonDir = path.join(this.options.resourcesPath, 'python');
      return process.platform === 'win32'
        ? path.join(pythonDir, 'python.exe')
        : path.join(pythonDir, 'bin', 'python');
    }
  }
  
  /**
   * 获取后端代码路径
   */
  getBackendPath() {
    if (this.options.isDev) {
      return path.join(this.options.appPath, 'backend');
    } else {
      return path.join(this.options.resourcesPath, 'backend');
    }
  }
  
  /**
   * 设置环境变量
   */
  getEnvironment() {
    const env = { ...process.env };
    
    if (this.options.isDev) {
      // 开发模式
      const condaEnv = process.env.CONDA_PREFIX ||
        path.join(process.env.HOME || process.env.USERPROFILE, 'anaconda3/envs/tianjun');
      
      if (process.platform === 'win32') {
        env.PATH = condaEnv + path.delimiter +
          path.join(condaEnv, 'Scripts') + path.delimiter +
          path.join(condaEnv, 'Library', 'bin') + path.delimiter +
          env.PATH;
      } else {
        env.PATH = path.join(condaEnv, 'bin') + path.delimiter + env.PATH;
      }
    } else {
      // 生产模式
      const pythonDir = path.join(this.options.resourcesPath, 'python');
      const backendPath = this.getBackendPath();
      
      if (process.platform === 'win32') {
        env.PATH = pythonDir + path.delimiter +
          path.join(pythonDir, 'Scripts') + path.delimiter +
          path.join(pythonDir, 'Library', 'bin') + path.delimiter +
          env.PATH;
      } else {
        env.PATH = path.join(pythonDir, 'bin') + path.delimiter + env.PATH;
      }
      
      // 注意：不要设置 PYTHONHOME，会干扰 conda-pack 打包的环境
      // env.PYTHONHOME = pythonDir;
      
      // 设置 PYTHONPATH 以便找到 backend 模块
      env.PYTHONPATH = path.dirname(backendPath);
      
      // 禁用 Python 字节码缓存（避免权限问题）
      env.PYTHONDONTWRITEBYTECODE = '1';
      
      // 禁用 Python 用户 site-packages（避免冲突）
      env.PYTHONNOUSERSITE = '1';
    }
    
    return env;
  }
  
  /**
   * 检查后端健康状态
   */
  checkHealth() {
    return new Promise((resolve) => {
      const req = http.request({
        hostname: this.options.host,
        port: this.options.port,
        path: '/api/v1/source/status',
        method: 'GET',
        timeout: 2000,
      }, (res) => {
        resolve(res.statusCode === 200);
      });
      
      req.on('error', () => resolve(false));
      req.on('timeout', () => {
        req.destroy();
        resolve(false);
      });
      
      req.end();
    });
  }
  
  /**
   * 等待后端启动
   */
  async waitForStartup() {
    const startTime = Date.now();
    
    while (Date.now() - startTime < this.options.startupTimeout) {
      if (await this.checkHealth()) {
        return true;
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    
    return false;
  }
  
  /**
   * 启动后端服务
   */
  async start() {
    if (this.isRunning) {
      console.log('[BackendManager] Backend is already running');
      return true;
    }
    
    const pythonPath = this.getPythonPath();
    const backendPath = this.getBackendPath();
    const workingDir = path.dirname(backendPath);
    
    console.log('[BackendManager] Configuration:');
    console.log(`  Python: ${pythonPath}`);
    console.log(`  Backend: ${backendPath}`);
    console.log(`  Working dir: ${workingDir}`);
    console.log(`  Port: ${this.options.port}`);
    console.log(`  Dev mode: ${this.options.isDev}`);
    
    // 验证 Python 存在
    if (!fs.existsSync(pythonPath)) {
      throw new Error(`Python not found: ${pythonPath}`);
    }
    
    // 验证后端目录存在
    if (!fs.existsSync(backendPath)) {
      throw new Error(`Backend not found: ${backendPath}`);
    }
    
    const env = this.getEnvironment();
    const args = [
      '-m', 'uvicorn',
      'backend.main:app',
      '--host', '0.0.0.0',
      '--port', this.options.port.toString(),
      '--no-access-log', // 减少日志输出
    ];
    
    return new Promise((resolve, reject) => {
      console.log(`[BackendManager] Starting: ${pythonPath} ${args.join(' ')}`);
      
      this.process = spawn(pythonPath, args, {
        cwd: workingDir,
        env: env,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
        detached: process.platform !== 'win32',
      });
      
      this.startTime = Date.now();
      
      // 处理输出
      this.process.stdout.on('data', (data) => {
        const msg = data.toString().trim();
        if (msg) {
          console.log(`[Backend] ${msg}`);
          this.emit('stdout', msg);
        }
      });
      
      this.process.stderr.on('data', (data) => {
        const msg = data.toString().trim();
        if (msg) {
          console.log(`[Backend] ${msg}`);
          this.emit('stderr', msg);
        }
      });
      
      this.process.on('error', (err) => {
        console.error(`[BackendManager] Process error: ${err.message}`);
        this.isRunning = false;
        this.emit('error', err);
        reject(err);
      });
      
      this.process.on('exit', (code, signal) => {
        console.log(`[BackendManager] Process exited (code: ${code}, signal: ${signal})`);
        this.isRunning = false;
        this.stopHealthCheck();
        this.emit('exit', { code, signal });
      });
      
      // 等待后端就绪
      this.waitForStartup().then((ready) => {
        if (ready) {
          this.isRunning = true;
          this.startHealthCheck();
          console.log('[BackendManager] Backend is ready');
          this.emit('ready');
          resolve(true);
        } else {
          const error = new Error('Backend startup timeout');
          this.stop();
          reject(error);
        }
      });
    });
  }
  
  /**
   * 停止后端服务
   */
  async stop() {
    this.stopHealthCheck();
    
    if (!this.process) {
      return;
    }
    
    console.log('[BackendManager] Stopping backend...');
    
    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        if (this.process) {
          console.log('[BackendManager] Force killing process...');
          this.process.kill('SIGKILL');
        }
        resolve();
      }, 5000);
      
      this.process.once('exit', () => {
        clearTimeout(timeout);
        resolve();
      });
      
      // 发送终止信号
      if (process.platform === 'win32') {
        // Windows: 使用 taskkill 终止进程树
        try {
          execSync(`taskkill /pid ${this.process.pid} /f /t`, { stdio: 'ignore' });
        } catch (e) {
          // 忽略错误（进程可能已经退出）
        }
      } else {
        // Unix: 发送 SIGTERM 到进程组
        try {
          process.kill(-this.process.pid, 'SIGTERM');
        } catch (e) {
          this.process.kill('SIGTERM');
        }
      }
    });
  }
  
  /**
   * 启动健康检查
   */
  startHealthCheck() {
    if (this.healthCheckTimer) {
      return;
    }
    
    this.healthCheckTimer = setInterval(async () => {
      const healthy = await this.checkHealth();
      if (!healthy && this.isRunning) {
        console.warn('[BackendManager] Health check failed');
        this.emit('unhealthy');
      }
    }, this.options.healthCheckInterval);
  }
  
  /**
   * 停止健康检查
   */
  stopHealthCheck() {
    if (this.healthCheckTimer) {
      clearInterval(this.healthCheckTimer);
      this.healthCheckTimer = null;
    }
  }
  
  /**
   * 获取运行状态
   */
  getStatus() {
    return {
      isRunning: this.isRunning,
      pid: this.process?.pid,
      uptime: this.startTime ? Date.now() - this.startTime : 0,
      port: this.options.port,
    };
  }
}

module.exports = BackendManager;
