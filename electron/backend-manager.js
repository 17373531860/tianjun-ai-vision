/**
 * 后端进程管理器
 * 负责启动、监控和停止 FastAPI 后端服务
 */

const { spawn, execSync } = require('child_process');
const path = require('path');
const http = require('http');
const fs = require('fs');
const EventEmitter = require('events');

// v3.7.x: 把 electron/package.json 的版本号注入到后端进程的环境变量.
// 打包后 electron/package.json 被 electron-builder 默认行为打进 resources/app.asar,
// Python 侧 open() 读不到 — 所以 export_context._read_app_info 无论怎么找路径
// 都只能拿到 "0.0.0" 兜底. 用 env 传是最稳的, electron 主进程 require 同目录
// package.json 在开发 / 打包 / asar 内 都能正确解析.
let _APP_PKG_META = { version: '0.0.0', name: '', description: '', author: '' };
try {
  const _pkg = require('./package.json');
  _APP_PKG_META = {
    version: _pkg.version || '0.0.0',
    name: _pkg.name || '',
    description: _pkg.description || '',
    author: typeof _pkg.author === 'string'
      ? _pkg.author
      : (_pkg.author && _pkg.author.name) || '',
    buildDate: _pkg.buildDate || '',
    commitHash: _pkg.commitHash || '',
  };
} catch (e) {
  console.warn('[BackendManager] 读 ./package.json 失败, 版本号将留默认:', e.message);
}

class BackendManager extends EventEmitter {
  constructor(options = {}) {
    super();
    
    this.options = {
      port: options.port || 8001,
      host: options.host || 'localhost',
      startupTimeout: options.startupTimeout || 300000,
      // v3.47 启动超时策略升级: startupTimeout 不再是硬死线。
      // 超过 startupTimeout 后, 只要后端最近 startupQuietMs 内仍有日志输出
      // (= 进程活着且在干活, 如 Defender 首扫/模型加载/相机重连), 就继续等;
      // 静默超过 startupQuietMs 或总时长到 startupMaxMs 才判启动失败。
      // 背景: 客户工控机开机首启曾 >5min, 老的 300s 硬超时直接弹"启动失败"退出,
      // 用户重开一次 (缓存已热) 反而能起 — 即"第一次启动不了要重开"的根因。
      startupQuietMs: options.startupQuietMs ?? 90000,
      startupMaxMs: options.startupMaxMs ?? 1200000,
      healthCheckInterval: options.healthCheckInterval || 5000,
      isDev: options.isDev || false,
      resourcesPath: options.resourcesPath || '',
      appPath: options.appPath || '',
      userDataPath: options.userDataPath || '',
      // v3.23.x: 加深启动就绪门槛 (默认关). 开 → 就绪探针改用深度探针
      // (/system/startup-ready, 真查数据库+项目), 确保放主窗进来时一切就绪。
      deepReadyGate: options.deepReadyGate === true,
      // v3.29.0 看门狗: 后端进程意外退出时自动拉起 (限流兜底, 防崩溃死循环)。
      // 仅在"已成功就绪过一次"后才生效; 初次启动失败仍走原启动失败流程。
      maxRestarts: options.maxRestarts ?? 3,            // 限流时间窗内最大自动重启次数
      restartWindowMs: options.restartWindowMs ?? 60000, // 限流时间窗 (ms)
    };
    
    this.process = null;
    this.isRunning = false;
    this.healthCheckTimer = null;
    this.startTime = null;
    // v3.29.0 看门狗状态
    this._intentionalStop = false;        // stop() 主动停止标志, 置位时看门狗让路
    this._everReady = false;              // 是否成功就绪过 (只有就绪过后崩溃才自动拉起)
    this._restartTimestamps = [];         // 限流时间窗内的重启时刻
    this._restartBackoffMs = [1000, 2000, 4000];  // 退避间隔 (按窗内第几次取, 封顶 4s)
    this._restartTimer = null;
    // v3.23.x: 加深门槛降级保护 — uvicorn 已起(浅探 200)但深探(查 DB)持续失败
    // 超过此阈值, 说明数据库异常/损坏, 干等也没用 → 降级放主窗进来 + 通知一次,
    // 让前端照常显示(再靠错误提示/重试), 而不是无限卡在启动动画到 5 分钟超时退出。
    this._deepDowngradeMs = 30000;       // 浅就绪后再多等 30s DB; 超了就降级
    this._shallowUpSince = null;          // 首次"浅就绪但深未就绪"的时刻
    this.deepDowngraded = false;          // 已降级标记 (只通知一次)
    this._startupResolved = false;        // 启动就绪阶段是否已结束 (降级判定只在启动期生效)
    // v3.47 启动等待状态
    this._lastOutputAt = 0;               // 后端最近一次 stdout/stderr 输出时刻 (活性判据)
    this._startupExitInfo = null;         // 首次启动期进程退出信息 → waitForStartup 立即止损, 不再空轮询满超时
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
    
    // 生产模式：将用户数据目录传给 Python 后端，使数据存在安装目录之外
    if (!this.options.isDev && this.options.userDataPath) {
      env.TIANJUN_DATA_DIR = this.options.userDataPath;
    }
    
    // 强制 Python 使用 UTF-8 编码（解决 Windows 中文乱码）
    env.PYTHONIOENCODING = 'utf-8';
    env.PYTHONLEGACYWINDOWSSTDIO = '0';
    env.PYTHONUTF8 = '1';
    // stdout 不缓冲: 日志实时刷出, 否则崩溃瞬间 backend.log 缺最后几行关键现场
    env.PYTHONUNBUFFERED = '1';

    // v3.7.x: 把 Electron 端 package.json 的 app 元数据注入到 Python 后端.
    // 后端 export_context._read_app_info() 优先读这些 env, 拿不到才回退到
    // fs 搜索 electron/package.json. 打包后 package.json 进了 app.asar,
    // Python 没法 fs 读, 所以必须靠 env 传 — 否则 app.version 永远 0.0.0.
    env.TIANJUN_APP_VERSION = _APP_PKG_META.version;
    if (_APP_PKG_META.name) env.TIANJUN_APP_NAME = _APP_PKG_META.name;
    if (_APP_PKG_META.description) env.TIANJUN_APP_PRODUCT_NAME = _APP_PKG_META.description;
    if (_APP_PKG_META.author) env.TIANJUN_APP_BRAND = _APP_PKG_META.author;
    if (_APP_PKG_META.buildDate) env.TIANJUN_APP_BUILD_DATE = _APP_PKG_META.buildDate;
    if (_APP_PKG_META.commitHash) env.TIANJUN_APP_COMMIT = _APP_PKG_META.commitHash;
    
    // Windows 控制台设为 UTF-8 代码页
    if (process.platform === 'win32') {
      env.CHCP = '65001';
    }
    
    return env;
  }
  
  /**
   * 底层探测: 给定路径 GET 一次, 200 即 true。
   */
  _probe(path) {
    return new Promise((resolve) => {
      const req = http.request({
        hostname: '127.0.0.1',  // 强制使用 IPv4，避免 localhost 解析为 IPv6
        port: this.options.port,
        path,
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
   * 检查后端健康状态
   *
   * 门槛关 (默认): 探 /source/status, uvicorn 起来即 200。
   * 门槛开: 探 /system/startup-ready (真查数据库+项目), 进来即一切就绪。
   *   + 降级保护: 若浅探(/source/status)已 200 即 uvicorn 起来了, 但深探持续
   *     失败超过 _deepDowngradeMs, 判定数据库异常 (干等无意义), 降级当就绪放行
   *     并 emit('deep-gate-downgraded') 通知一次。冷启动期(连浅探都没起)不计时,
   *     正常等待, 不误降级。
   */
  async checkHealth() {
    // 门槛关, 或启动就绪阶段已结束(降级/正常放行后的运行期监控): 一律浅探。
    // 降级判定只在启动等待期生效, 避免运行中 DB 短暂卡顿误弹"启动降级"提示。
    if (!this.options.deepReadyGate || this._startupResolved) {
      return this._probe('/api/v1/source/status');
    }
    const deepOk = await this._probe('/api/v1/system/startup-ready');
    if (deepOk) {
      this._shallowUpSince = null;  // 深就绪, 清降级时钟
      return true;
    }
    const shallowOk = await this._probe('/api/v1/source/status');
    if (!shallowOk) {
      // uvicorn 还没起 → 正常冷启动, 继续等, 不启动降级时钟
      this._shallowUpSince = null;
      return false;
    }
    // uvicorn 起来了但深探不行 → DB 还没好 / 异常. 起降级时钟
    if (this._shallowUpSince === null) this._shallowUpSince = Date.now();
    if (Date.now() - this._shallowUpSince >= this._deepDowngradeMs) {
      if (!this.deepDowngraded) {
        this.deepDowngraded = true;
        console.warn('[BackendManager] 加深就绪门槛降级: uvicorn 已起但数据库探测持续失败, 放行主窗 (DB 可能异常)');
        this.emit('deep-gate-downgraded');
      }
      return true;  // 降级放行
    }
    return false;  // 再给 DB 一点时间
  }
  
  /**
   * 等待后端启动 (v3.47 策略见构造器注释)
   *
   * 判失败的三种情况:
   *   1. 启动期后端进程已退出 (_startupExitInfo) → 立即失败, 不再空轮询满超时
   *   2. 总时长超过 startupMaxMs 绝对上限
   *   3. 总时长超过 startupTimeout 基础线, 且后端已静默超过 startupQuietMs
   */
  async waitForStartup() {
    const startTime = Date.now();
    this._lastOutputAt = Date.now();

    for (;;) {
      if (this._startupExitInfo) {
        const { code, signal } = this._startupExitInfo;
        console.error(`[BackendManager] 后端进程在启动期退出 (code=${code}, signal=${signal}), 停止等待`);
        return false;
      }
      if (await this.checkHealth()) {
        return true;
      }
      const elapsed = Date.now() - startTime;
      if (elapsed >= this.options.startupMaxMs) {
        console.error(`[BackendManager] 启动等待超过绝对上限 ${this.options.startupMaxMs / 1000}s, 判启动失败`);
        return false;
      }
      if (elapsed >= this.options.startupTimeout &&
          Date.now() - this._lastOutputAt >= this.options.startupQuietMs) {
        console.error(
          `[BackendManager] 启动超过 ${this.options.startupTimeout / 1000}s 且后端已静默 ` +
          `${Math.round((Date.now() - this._lastOutputAt) / 1000)}s, 判启动失败`
        );
        return false;
      }
      if (elapsed >= this.options.startupTimeout) {
        // 超过基础线但后端仍在输出日志 → 大概率是冷盘/杀软首扫/模型加载, 继续等
        console.log(`[BackendManager] 启动已 ${Math.round(elapsed / 1000)}s, 后端仍有输出, 继续等待...`);
      }
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
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
          console.log('[BackendManager] Port still in use, trying to find backend uvicorn processes...');
          try {
            // 仅清理“明确属于本应用后端”的 Python 进程，避免误杀系统其他 Python 服务
            // 需要命令行同时包含: python + uvicorn + backend.main
            const psCmd = `powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'backend.main' } | Select-Object -ExpandProperty ProcessId"`;
            const result = execSync(psCmd, {
              encoding: 'utf-8',
              stdio: ['pipe', 'pipe', 'pipe'],
              maxBuffer: 10 * 1024 * 1024
            });

            const pids = result
              .trim()
              .split(/\r?\n/)
              .map(s => parseInt((s || '').trim(), 10))
              .filter(pid => Number.isInteger(pid) && pid > 0 && pid !== process.pid);

            for (const pid of pids) {
              console.log(`[BackendManager] Killing backend uvicorn PID: ${pid}`);
              try {
                execSync(`taskkill /pid ${pid} /f /t`, { stdio: 'ignore' });
                cleaned = true;
              } catch (e) {
                // 忽略，继续清理其他进程
              }
            }
          } catch (e) {
            console.log('[BackendManager] Unable to resolve backend uvicorn process list:', e.message);
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
    // 新一轮启动: 清主动停止标志, 让看门狗在本轮进程崩溃时能接管
    this._intentionalStop = false;
    this._startupExitInfo = null;  // v3.47: 清上一轮的启动期退出记录
    this._lastOutputAt = Date.now();
    
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
          this._lastOutputAt = Date.now();  // v3.47: 活性判据, 供启动等待策略用
          console.log(`[Backend] ${msg}`);
          this.emit('stdout', msg);
        }
      });
      
      this.process.stderr.on('data', (data) => {
        const msg = data.toString().trim();
        if (msg) {
          this._lastOutputAt = Date.now();
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
        // v3.47: 首次启动期 (从未就绪过) 进程退出 → 记下退出信息,
        // waitForStartup 下一拍立即止损, 不再对着死进程空轮询满超时 (曾要等满 5 分钟才报错)。
        if (!this._everReady) {
          this._startupExitInfo = { code, signal };
        }
        this.emit('exit', { code, signal });
        // v3.29.0 看门狗: 已就绪过 + 非主动停止 → 后端进程意外死亡, 自动拉起。
        // 初次启动期(未就绪过)的退出交给原启动失败流程处理, 不在此重启。
        if (this._everReady && !this._intentionalStop) {
          this._scheduleRestart(code, signal);
        }
      });
      
      // 等待后端就绪
      this.waitForStartup().then((ready) => {
        this._startupResolved = true;  // 启动期结束, 之后健康检查只浅探, 不再降级判定
        if (ready) {
          this.isRunning = true;
          this._everReady = true;  // 标记已就绪过, 之后崩溃才触发看门狗自动拉起
          this.startHealthCheck();
          console.log('[BackendManager] Backend is ready');
          this.emit('ready');
          resolve(true);
        } else {
          // v3.47: 区分"进程死了"和"真超时", 弹给用户的错误信息更可诊断
          const exitInfo = this._startupExitInfo;
          const error = exitInfo
            ? new Error(`后端进程启动失败 (exit code: ${exitInfo.code}, signal: ${exitInfo.signal || 'none'}), 详见 logs/backend.log`)
            : new Error('Backend startup timeout');
          this.stop();
          reject(error);
        }
      });
    });
  }
  
  /**
   * v3.29.0 看门狗: 编排"后端进程意外死亡 → 退避后自动拉起"。
   *
   * 限流兜底: restartWindowMs 时间窗内最多重启 maxRestarts 次; 超出判定为
   * 稳定性故障 (崩溃死循环), 放弃自动恢复并 emit('restart-failed'), 交由主进程
   * 回退到"弹错误框 + 退出"的老行为。退避间隔 1s→2s→4s, 避免端口/资源未释放即重拉。
   *
   * 事件: restarting({attempt,delay}) → (拉起中) → restarted({attempt}) | restart-failed({code,signal})
   */
  _scheduleRestart(code, signal) {
    const now = Date.now();
    // 清理时间窗外的历史重启记录
    this._restartTimestamps = this._restartTimestamps.filter(
      t => now - t < this.options.restartWindowMs
    );
    if (this._restartTimestamps.length >= this.options.maxRestarts) {
      console.error(
        `[BackendManager] 看门狗: ${this.options.restartWindowMs / 1000}s 内已自动重启 ` +
        `${this._restartTimestamps.length} 次仍未稳定, 判定稳定性故障, 放弃自动恢复`
      );
      this.emit('restart-failed', { code, signal });
      return;
    }
    const attempt = this._restartTimestamps.length + 1;
    this._restartTimestamps.push(now);
    const delay = this._restartBackoffMs[
      Math.min(attempt - 1, this._restartBackoffMs.length - 1)
    ];
    console.warn(
      `[BackendManager] 看门狗: 后端意外退出 (code=${code}, signal=${signal}), ` +
      `${delay}ms 后第 ${attempt} 次自动拉起`
    );
    this.emit('restarting', { attempt, delay });
    this._restartTimer = setTimeout(() => {
      this._restartTimer = null;
      this.process = null;
      this.isRunning = false;
      this.start()
        .then(() => {
          console.log('[BackendManager] 看门狗: 后端已自动恢复');
          this.emit('restarted', { attempt });
        })
        .catch((err) => {
          console.error('[BackendManager] 看门狗: 自动拉起失败:', err.message);
          // start() 失败时其内部已 stop()(置主动停止), 不会再触发 exit 重启,
          // 故在此显式再调度一次, 直到命中限流上限。
          this._scheduleRestart(code, signal);
        });
    }, delay);
  }

  /**
   * 停止后端服务 - 优雅关闭
   */
  async stop() {
    // 看门狗让路: 主动停止 (关机/换版) 不触发自动重启, 并清掉待执行的重启
    this._intentionalStop = true;
    if (this._restartTimer) {
      clearTimeout(this._restartTimer);
      this._restartTimer = null;
    }
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
