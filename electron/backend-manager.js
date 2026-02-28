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
      startupTimeout: options.startupTimeout || 300000,  // 增加到 300 秒
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
    
    // 强制 Python 使用 UTF-8 编码（解决 Windows 中文乱码）
    env.PYTHONIOENCODING = 'utf-8';
    env.PYTHONLEGACYWINDOWSSTDIO = '0';
    env.PYTHONUTF8 = '1';
    
    // Windows 控制台设为 UTF-8 代码页
    if (process.platform === 'win32') {
      env.CHCP = '65001';
    }
    
    return env;
  }
  
  /**
   * 检查后端健康状态
   */
  checkHealth() {
    return new Promise((resolve) => {
      const req = http.request({
        hostname: '127.0.0.1',  // 强制使用 IPv4，避免 localhost 解析为 IPv6
        port: this.options.port,
        path: '/api/v1/source/status',
        method: 'GET',
        timeout: 10000,  // 增加到 10 秒
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
      await new Promise(resolve => setTimeout(resolve, 2000));  // 增加检查间隔到 2 秒
    }
    
    return false;
  }
  
  /**
   * 检查端口是否被占用
   */
  isPortInUse() {
    try {
      if (process.platform === 'win32') {
        const result = execSync(`netstat -ano | findstr :${this.options.port} | findstr LISTENING`, {
          encoding: 'utf-8',
          stdio: ['pipe', 'pipe', 'pipe']
        });
        return result.trim().length > 0;
      } else {
        const result = execSync(`lsof -ti :${this.options.port}`, {
          encoding: 'utf-8',
          stdio: ['pipe', 'pipe', 'pipe']
        });
        return result.trim().length > 0;
      }
    } catch (e) {
      return false;
    }
  }
  
  /**
   * 清理残留的后端进程（启动前调用）
   */
  async cleanupStaleProcesses() {
    console.log('[BackendManager] Checking for stale backend processes...');
    
    let cleaned = false;
    
    try {
      if (process.platform === 'win32') {
        // Windows: 第一步 - 查找占用端口 8001 的进程并杀掉
        try {
          const result = execSync(`netstat -ano | findstr :${this.options.port}`, { 
            encoding: 'utf-8',
            stdio: ['pipe', 'pipe', 'pipe']
          });
          
          const lines = result.trim().split('\n');
          const pids = new Set();
          
          for (const line of lines) {
            // 匹配 LISTENING 或 ESTABLISHED 状态的连接
            if (line.includes(':' + this.options.port)) {
              const parts = line.trim().split(/\s+/);
              if (parts.length >= 5) {
                const pid = parseInt(parts[parts.length - 1]);
                if (pid && pid > 0 && pid !== process.pid) {
                  pids.add(pid);
                }
              }
            }
          }
          
          if (pids.size > 0) {
            console.log(`[BackendManager] Found ${pids.size} process(es) using port ${this.options.port}`);
            for (const pid of pids) {
              console.log(`[BackendManager] Killing process PID: ${pid}`);
              try {
                // 使用 /f 强制终止，/t 终止子进程树
                execSync(`taskkill /pid ${pid} /f /t`, { 
                  encoding: 'utf-8',
                  stdio: ['pipe', 'pipe', 'pipe']
                });
                cleaned = true;
              } catch (e) {
                console.log(`[BackendManager] Failed to kill PID ${pid}: ${e.message}`);
              }
            }
          }
        } catch (e) {
          // netstat 没有找到任何结果
          console.log('[BackendManager] No processes found on port ' + this.options.port);
        }
        
        // Windows: 第二步 - 如果端口还被占用，尝试查找 uvicorn 相关进程
        if (this.isPortInUse()) {
          console.log('[BackendManager] Port still in use, trying to find uvicorn processes...');
          try {
            // 查找命令行包含 uvicorn 的进程
            const result = execSync('tasklist /v /fo csv', {
              encoding: 'utf-8',
              stdio: ['pipe', 'pipe', 'pipe'],
              maxBuffer: 10 * 1024 * 1024 // 10MB buffer
            });
            
            const lines = result.trim().split('\n');
            for (const line of lines) {
              // 查找 python.exe 进程
              if (line.toLowerCase().includes('python.exe')) {
                const match = line.match(/"[^"]*","(\d+)"/);
                if (match) {
                  const pid = parseInt(match[1]);
                  if (pid && pid > 0) {
                    console.log(`[BackendManager] Killing Python process PID: ${pid}`);
                    try {
                      execSync(`taskkill /pid ${pid} /f /t`, { stdio: 'ignore' });
                      cleaned = true;
                    } catch (e) {
                      // 忽略
                    }
                  }
                }
              }
            }
          } catch (e) {
            console.log('[BackendManager] tasklist failed:', e.message);
          }
        }
        
        // 等待端口释放
        if (cleaned) {
          console.log('[BackendManager] Waiting for port to be released...');
          // 最多等待 5 秒
          for (let i = 0; i < 10; i++) {
            await new Promise(resolve => setTimeout(resolve, 500));
            if (!this.isPortInUse()) {
              console.log('[BackendManager] Port released successfully');
              break;
            }
            console.log(`[BackendManager] Port still in use, waiting... (${i + 1}/10)`);
          }
        }
        
      } else {
        // Linux/Mac: 使用 lsof 和 pkill
        try {
          // 查找占用端口的进程
          const result = execSync(`lsof -ti :${this.options.port}`, {
            encoding: 'utf-8',
            stdio: ['pipe', 'pipe', 'pipe']
          });
          
          const pids = result.trim().split('\n').filter(p => p);
          
          for (const pid of pids) {
            console.log(`[BackendManager] Killing stale process PID: ${pid}`);
            try {
              execSync(`kill -9 ${pid}`, { stdio: 'ignore' });
            } catch (e) {
              // 进程可能已经退出
            }
          }
          
          if (pids.length > 0) {
            console.log(`[BackendManager] Cleaned up ${pids.length} stale process(es)`);
            // 等待端口释放
            await new Promise(resolve => setTimeout(resolve, 1000));
          }
        } catch (e) {
          // lsof 没有找到任何结果
          console.log('[BackendManager] No stale processes found on port');
        }
        
        // 额外清理：查找残留的 uvicorn 进程
        try {
          execSync('pkill -9 -f "uvicorn.*backend.main"', { stdio: 'ignore' });
        } catch (e) {
          // 没有匹配的进程
        }
      }
      
      console.log('[BackendManager] Stale process cleanup completed');
    } catch (e) {
      console.error('[BackendManager] Error during cleanup:', e.message);
    }
  }
  
  /**
   * 启动后端服务
   */
  async start() {
    if (this.isRunning) {
      console.log('[BackendManager] Backend is already running');
      return true;
    }
    
    // 先清理可能残留的后端进程
    await this.cleanupStaleProcesses();
    
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
      
      // Windows 下先设置控制台代码页为 UTF-8
      if (process.platform === 'win32') {
        try {
          execSync('chcp 65001', { stdio: 'ignore' });
        } catch (e) {
          // 忽略
        }
      }
      
      this.process = spawn(pythonPath, args, {
        cwd: workingDir,
        env: env,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
        detached: process.platform !== 'win32',
      });
      
      this.startTime = Date.now();
      
      // 强制以 UTF-8 读取子进程输出
      this.process.stdout.setEncoding('utf-8');
      this.process.stderr.setEncoding('utf-8');
      
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
   * 停止后端服务 - 优雅关闭
   */
  async stop() {
    this.stopHealthCheck();
    
    if (!this.process) {
      return;
    }
    
    console.log('[BackendManager] Stopping backend gracefully...');
    
    // 首先尝试通过 API 请求后端自行关闭
    try {
      await this.requestGracefulShutdown();
      console.log('[BackendManager] Graceful shutdown request sent');
      
      // 等待后端自行关闭（最多等待 3 秒）
      const exitedGracefully = await this.waitForExit(3000);
      if (exitedGracefully) {
        console.log('[BackendManager] Backend exited gracefully');
        return;
      }
    } catch (e) {
      console.log('[BackendManager] Graceful shutdown request failed:', e.message);
    }
    
    // 如果优雅关闭失败，使用进程信号
    console.log('[BackendManager] Falling back to process termination...');
    
    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        if (this.process) {
          console.log('[BackendManager] Force killing process...');
          try {
            if (process.platform === 'win32') {
              execSync(`taskkill /pid ${this.process.pid} /f /t`, { stdio: 'ignore' });
            } else {
              this.process.kill('SIGKILL');
            }
          } catch (e) {
            // 忽略错误
          }
        }
        resolve();
      }, 5000);
      
      this.process.once('exit', () => {
        clearTimeout(timeout);
        resolve();
      });
      
      // 发送终止信号
      if (process.platform === 'win32') {
        // Windows: 先尝试不带 /f 的 taskkill
        try {
          execSync(`taskkill /pid ${this.process.pid} /t`, { stdio: 'ignore' });
        } catch (e) {
          // 如果失败（进程可能已经在关闭中），忽略
        }
      } else {
        // Unix: 发送 SIGTERM 到进程组
        try {
          process.kill(-this.process.pid, 'SIGTERM');
        } catch (e) {
          try {
            this.process.kill('SIGTERM');
          } catch (e2) {
            // 忽略
          }
        }
      }
    });
  }
  
  /**
   * 请求后端优雅关闭
   */
  requestGracefulShutdown() {
    return new Promise((resolve, reject) => {
      const req = http.request({
        hostname: '127.0.0.1',
        port: this.options.port,
        path: '/api/v1/source/shutdown/complete',
        method: 'POST',
        timeout: 3000,
      }, (res) => {
        resolve(res.statusCode === 200);
      });
      
      req.on('error', (e) => reject(e));
      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Request timeout'));
      });
      
      req.end();
    });
  }
  
  /**
   * 等待进程退出
   */
  waitForExit(timeout) {
    return new Promise((resolve) => {
      if (!this.process) {
        resolve(true);
        return;
      }
      
      const timer = setTimeout(() => {
        resolve(false);
      }, timeout);
      
      this.process.once('exit', () => {
        clearTimeout(timer);
        resolve(true);
      });
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
