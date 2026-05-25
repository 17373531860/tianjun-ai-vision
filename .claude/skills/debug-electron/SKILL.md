---
name: debug-electron
description: "诊断 Electron 桌面壳问题：后端进程管理、8步关机流程、License验证、崩溃恢复、Splash启动、GPU内存限制。当桌面应用启动失败、关机卡住或License异常时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__sequential-thinking, mcp__sentry"
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
- `electron/splash/main.js` — v3.8.2 起的赛博 splash 状态机 + 摄像头逻辑（v3.9.1 起改三档可配）
- `electron/splash/preload.js` — splash IPC 桥（`getWorkstationConfig` / `onBackendLog` / `notifySplashFinished`）
- `backend/main.py` — shutdown/step 端点实现
- `backend/scripts/generate_license.py` — License生成工具

## 已知陷阱
- 开发模式加载 localhost:5173 但 Vite 实际在 6001（端口不匹配）
- splash.html 版本号硬编码 v1.0.0，实际是 v2.2.0
- shutdown.html 使用 nodeIntegration:true（安全风险，但功能需要）
- 单实例锁: 第二个实例会静默退出，可能让用户困惑

## v3.9.1 新增：Splash 摄像头三档配置 + 鼠标点击跳过

> v3.8.2 引入了赛博 splash + MediaPipe 手势识别后，客户工厂工控机经常装 Todesk / 向日葵等远程虚拟相机被 splash 误用；工控机一般没键盘，ESC 跳过手势对客户不可达。v3.9.1 给出系统化解。

### 摄像头三档可配（auto / specific / disabled）

**配置位置**：`backend/data/workstation_config.json` 顶层 `splash` 字段。**不是**放在 `channels` 里——splash 用的相机不一定是任何工位的检测相机，跟生产线相机彻底解耦。

```json
{
  "channels": { ... },
  "splash": {
    "camera_mode": "auto" | "specific" | "disabled",
    "device_id":   "...",     // specific 模式锁定的 MediaDeviceInfo.deviceId
    "device_label": "..."     // 仅回显, splash 不读
  }
}
```

**三档语义**：
| 模式 | 行为 | 拿不到时 |
|---|---|---|
| `auto`（默认） | 沿用老逻辑：先工位 1 `usb_device_id` → 否则 `facingMode: 'user'` 系统默认 | 无摄像头自动播放兜底 |
| `specific` | `deviceId.exact` 强制锁定 | "指定相机不可用（自动播放）"，**不回退到 todesk** |
| `disabled` | 直接跳过 `getUserMedia`，进自动播放 | — |

**关键区别 `exact` vs `ideal`**：v3.8.2 的 auto 用 `ideal`（软锁定，找不到自动换设备 → 经常换到 todesk）；v3.9.1 specific 必须用 `exact`（硬锁定，找不到走兜底，不偷换）。

### API & 前端入口

- 后端：`GET /api/v1/workstations/splash-camera` / `PUT /api/v1/workstations/splash-camera`（`backend/api/channel_manager.py`）
- 前端：Settings → 显示设置 → 监视设置卡片下方的『启动动画手势相机』
- 路由顺序：`/splash-camera` **必须**注册在 `/{channel_id}/...` 之前，否则被 catch-all 抢走

### 鼠标点击跳过手势

**触发源**：
- ESC 键（v3.8.2 起）
- window click（v3.9.1 起，排除 `<button>` `<a>` `<input>` `<select>` `<textarea>`）

两条路径都走统一函数 `skipToReady(reason)` → `transitionTo(COLLAPSE)` → 1.2s 后 → `transitionTo(EXPLOSION)`。

**守门**：仅 `Stage.IDLE` / `Stage.HOVER` 阶段响应；`COLLAPSE` / `EXPLOSION` / `READY` 期间忽略，动画播到一半不会被打断。

### 排查模板

| 现象 | 第一步看 | 第二步看 | 修复方向 |
|---|---|---|---|
| Splash 总是拿到 todesk 虚拟相机 | 主前端 Settings → 启动动画手势相机的模式 | `workstation_config.json` 顶层 `splash.camera_mode` | 改 `specific` 锁定真实摄像头，或改 `disabled` 直接关 |
| Splash 启动很慢，狂等 | 客户机有没有键盘 / 是不是触摸屏 | 是否点屏幕没反应 | v3.9.1 已加 click 跳过，确认是这个版本之后 |
| Settings 扫描相机点了没反应 | 浏览器有没有摄像头权限 | 控制台 `enumerateDevices` 报错 | 客户机第一次扫会弹权限弹窗（Electron 默认放行，应该不弹给客户） |
| Splash 选了 `specific` 但下次启动还是默认 | `workstation_config.json` 顶层 `splash` 段是否存在 | `set_splash_config` 是否真写盘 | `curl /api/v1/workstations/splash-camera` 验证 |
| Splash 点屏幕跳到一半又被打断 | 是否有快速重复点击 | `skipToReady` 守门是否生效 | 守门只看 `currentStage`，IDLE/HOVER 之外二次点击被吞 |
| Splash 点了 audioBtn 也跳过了 | `closest('button')` 是否生效 | `<button>` 嵌套元素的 click target | 已加 `closest('button, a, input, select, textarea')` 豁免 |

### 跨进程数据访问要点

Splash 是早于主前端加载的**独立 BrowserWindow**：
- 不能用 localStorage / Pinia store
- 跨进程数据只能走 IPC（`splash:get-workstation-config` 同步读 JSON 文件）
- 添加新配置项给 splash 用：**写文件**（`workstation_config.json`）+ 主进程 IPC handler（`electron/main.js`），不要试图走 HTTP API（splash 启动期间后端可能还没起来）
