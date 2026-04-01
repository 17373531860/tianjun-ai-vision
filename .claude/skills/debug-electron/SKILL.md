---
name: debug-electron
description: "诊断 Electron 桌面壳问题：后端进程管理、8步关机流程、License验证、崩溃恢复、Splash启动、GPU内存限制。当桌面应用启动失败、关机卡住或License异常时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# debug-electron: Electron 桌面壳诊断

你正在诊断天军AI视觉检测系统的 **Electron 桌面应用层**。

用户问题: $ARGUMENTS

## Electron 架构

```
electron/
├── main.js (587L)           -- 主进程：窗口、IPC、关机、License
├── backend-manager.js (617L) -- Python后端进程管理
├── license-manager.js (202L) -- RSA硬件绑定License
├── preload.js (12L)          -- contextBridge API
├── splash.html (114L)        -- 启动画面
├── shutdown.html (363L)      -- 关机进度窗口
└── package.json              -- Electron 28, electron-builder
```

## 启动流程

```
app.ready
  → 创建 Splash 窗口 (400x300, frameless)
  → BackendManager.start()
    → 清理残留进程 (port 8001)
    → 解析 Python 路径 (conda/packed)
    → 启动 uvicorn backend.main:app --port 8001
    → 每2秒轮询 /api/v1/source/status
    → 300秒超时
  → backend 'ready' 事件
    → LicenseManager 验证
      → 读取 machine_id (SHA256(主板+UUID+CPU))
      → 尝试 auto-install (resources/licenses/*.lic)
      → 验证 RSA-SHA256 签名 + machineId + 过期日期
    → 创建主窗口 (1600x900)
      → 开发模式: http://localhost:5173 ⚠️ (应该是6001)
      → 生产模式: file://resources/app/dist/index.html
    → 关闭 Splash
```

## 关机流程 (8步)

```
窗口关闭/Ctrl+Q
  → 拦截 'close' 事件
  → 打开 shutdown.html 窗口
  → 依次执行8步 (每步有超时):
    1. stop_detection     POST /source/shutdown/step/stop_detection
    2. save_counters      POST /source/shutdown/step/save_counters
    3. end_cycle          POST /source/shutdown/step/end_cycle
    4. end_session        POST /source/shutdown/step/end_session
    5. stop_recording     POST /source/shutdown/step/stop_recording
    6. release_camera     POST /source/shutdown/step/release_camera
    7. release_model      POST /source/shutdown/step/release_model
    8. cleanup            POST /source/shutdown/step/cleanup
  → POST /source/shutdown/complete (触发延迟退出)
  → BackendManager.stop()
  → app.quit()
```

**强制关闭:** shutdown.html 提供 "强制关闭" 按钮 → 发送 `shutdown-force` IPC → app.exit(0)

## Python 路径解析 (BackendManager)

```
开发模式:
  CONDA_PREFIX → {CONDA_PREFIX}/python
  或 ~/anaconda3/envs/tianjun/bin/python

生产模式:
  Windows: resources/python/python.exe
  Linux: resources/python/bin/python
```

## License 系统 (LicenseManager)

```
Machine ID 生成:
  Linux: /sys/class/dmi/id/board_name + product_uuid + /proc/cpuinfo
  Windows: wmic baseboard + csproduct UUID + CPU
  → SHA256 → "TJ-" + 12位大写HEX

验证:
  读取 userData/license.lic
  → JSON: { data: "{machineId, customerName, expiresAt}", signature: "base64" }
  → RSA-SHA256 验签 (public key 硬编码在代码中)
  → 检查 machineId 匹配 + 未过期

生成工具: backend/scripts/generate_license.py
  → 需要 keys/private.pem 私钥
```

## GPU 内存限制 (main.js)

```javascript
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache')
app.commandLine.appendSwitch('js-flags', '--max-old-space-size=512')
app.commandLine.appendSwitch('max-decoded-image-bytes', 256 * 1024 * 1024)
app.commandLine.appendSwitch('gpu-memory-buffer-compositor-resources', 256)
```
目的: 防止长时间 MJPEG 解码导致内存溢出

## 崩溃恢复

| 事件 | 处理 |
|------|------|
| render-process-gone | 1秒后 reload |
| unresponsive | 5秒后 reload |
| child-process-gone (GPU) | 1.5秒后 reload |
| backend unexpected exit | 显示错误弹窗 + app.exit(1) |
| backend unhealthy | 连续3次健康检查失败 → 触发 |

## 诊断步骤

### 启动失败
1. 检查 Python 路径解析: BackendManager._findPython()
2. 检查后端日志: BackendManager 的 stdout/stderr 事件
3. 确认 port 8001 未被占用
4. 检查残留进程清理是否成功
5. 300秒超时: 后端启动太慢（模型加载、数据迁移等）

### 关机卡住
1. 确认哪一步超时（shutdown.html 显示进度）
2. 常见卡住点: stop_detection（推理线程未退出）、release_camera（SDK未释放）
3. 检查 backend/main.py 中对应 shutdown/step 端点的实现
4. 强制关闭: 用户点击 "强制关闭" 或超时后自动强退

### License 问题
1. 确认 machine_id: `window.electronAPI.getLicenseStatus()` 返回 machineId
2. 检查 .lic 文件是否在正确位置 (userData/license.lic)
3. 验证签名: 确认 public key 与生成工具使用的 private key 配对
4. 过期检查: license data 中的 expiresAt 字段

### 内存泄漏
1. Electron DevTools → Performance Monitor 查看 JS Heap
2. main.js 的内存限制是否足够
3. MJPEG 双缓冲是否正确释放旧 img

## 关键文件
- `electron/main.js` — 主进程逻辑
- `electron/backend-manager.js` — Python进程管理
- `electron/license-manager.js` — License系统
- `electron/preload.js` — renderer API
- `electron/shutdown.html` — 关机进度UI
- `backend/main.py` — shutdown/step 端点实现
- `backend/scripts/generate_license.py` — License生成工具

## 已知陷阱
- 开发模式加载 localhost:5173 但 Vite 实际在 6001（端口不匹配）
- splash.html 版本号硬编码 v1.0.0，实际是 v2.2.0
- shutdown.html 使用 nodeIntegration:true（安全风险，但功能需要）
- 单实例锁: 第二个实例会静默退出，可能让用户困惑
