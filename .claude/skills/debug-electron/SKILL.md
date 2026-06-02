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

---

## v3.10.1 新增：Splash 默认关 + 窗口模式可配 + 最小化 IPC

### `splash.enabled` 字段（默认 `false`）

**改动语义**：客户老板要求"启动直接进主程序，不要那个 splash 动画"。`workstation_config.json` 顶层 `splash.enabled` 默认 `false`。

**Electron 主进程启动早期**（`main.js: app.whenReady().then(...)`）：

1. 调 `readWorkstationConfig()` **直读 JSON 文件**（比前端先起，不能用 HTTP）。三层 fallback 路径：
   - `app.getPath('userData')/workstation_config.json`（**安装版**首选）
   - `<exe>/../backend/workstation_config.json`（**开发版**）
   - `<exe>/../backend/data/workstation_config.json`（`TIANJUN_DATA_DIR` 设置时）
2. `splashEnabled = cfg.splash?.enabled === true`（默认 false）
3. **`false` 走"无 splash 路径"**：
   - `splashFinishedByRenderer = true`（绕过 splash:finished IPC 守门）
   - `createWindow({ fullscreen })` 建主窗（开头隐藏）
   - `initBackendManager() + startBackend()` 后台预热
   - 后端 ready → `maybeShowMainWindow()` 直接 show
4. **`true` 走老 splash 完整流程**：摄像头 / 手动跳过 / 闲置超时 全部不动。

**排查"客户更新到 v3.10.1 但还在播 splash"**：
- `workstation_config.json` 里 `splash.enabled` 是不是被显式设成了 `true`（老配置写 enabled=true 不会自动改回 false，只有新装才默认 false）。
- 控制台日志看 `[App] 启动配置: splash.enabled=...` 行。

### `window.fullscreen` 字段（默认 `false`）

**改动语义**：v3.8.2 起默认全屏 + 无标题栏（kiosk）。客户桌面办公场景反馈"不能最小化 / 不能调大小"，v3.10.1 改回默认窗口模式带原生标题栏。

**`workstation_config.json` 顶层 `window` 段**：

```json
{
  "splash": { "enabled": false, "camera_mode": "auto", ... },
  "window": { "fullscreen": false },
  "channels": [...]
}
```

**Electron `createWindow({ fullscreen })` 行为**：

| fullscreen | frame | 尺寸 | 用途 |
|---|---|---|---|
| `true` (kiosk) | `false` | 占满显示器 | 工业部署，无标题栏 |
| `false` (默认) | `true` | 1600×900 居中 | 桌面办公，Windows 原生最小化 / 最大化 / 关闭 |

**两个新 IPC 通道**：

| 通道 | 入参 | 行为 |
|---|---|---|
| `window:minimize` | — | `mainWindow.minimize()` 收任务栏，进程不退出 |
| `window:set-fullscreen` | `{ fullscreen: boolean }` | 热切 `mainWindow.setFullScreen(...)` + `mainWindow.setMenuBarVisibility/setAutoHideMenuBar` + 持久化到 `workstation_config.window.fullscreen` |

**`preload.js` 暴露**：

```js
window.electronAPI.minimizeWindow()       // → window:minimize
window.electronAPI.setFullScreen(true)    // → window:set-fullscreen
```

**前端入口**：`Settings/index.vue` → "窗口模式" 卡片（仅 `isElectronEnv` 显示）。

**排查模板**：

| 现象 | 第一步看 | 第二步看 | 修复 |
|---|---|---|---|
| 客户更新后窗口变窗口模式了 | `workstation_config.window.fullscreen` 是否 false | 是 v3.10.1 默认行为 | 客户想全屏 → Settings 切开关 |
| 全屏开关切了但下次启动还是窗口 | `set-fullscreen` IPC 是否真写盘 | grep `[Window] 持久化 fullscreen` 日志 | 检查 `ChannelManager.set_window_config` 是否被调 |
| 最小化按钮点了没反应 | `isElectronEnv` 是否 true | 浏览器预览态下按钮 disabled | 必须打包桌面版下才生效 |
| Settings 显示 "⚠ 当前在浏览器中预览" | `window.electronAPI` 是否注入 | 是否 dev 模式 vite serve | 这是正常提示，开发环境无法测窗口 IPC |

### `readWorkstationConfig` 路径三层 fallback 的踩坑

**症状**：新装客户机改了 `workstation_config.json` 但 Electron 启动还按老行为。

**根因**：写错了 `workstation_config.json` 的位置。安装版/开发版/`TIANJUN_DATA_DIR` 三个路径**互不互通**，主进程**只读第一个存在的**。

**排查命令**（Windows 客户机）：

```bat
:: 看真实安装版应该读哪个
echo %APPDATA%\tianjun-ai-vision\workstation_config.json
:: 看 Electron 启动早期实际读到的
:: → 在 Settings 页底部"诊断信息"区显示 (TODO: v3.10.x 后续补上)
```

**新增配置项必须**：写到 `channel_manager.py` 的 `get_*_config / set_*_config` 出口，前端走 `/api/v1/workstations/*` 调；splash 启动早期需要的字段则**额外**在 `electron/main.js: readWorkstationConfig()` 直读。

## v3.15.1 新增：打包资源遗漏 + 启动就绪时序（客户视频实证）

> v3.15.0 安装包客户现场暴露三个问题，根因都不在业务逻辑，而在「打包」和「就绪判定」。两段现场视频实证，全部已修。

### 陷阱一：运行时资源忘加打包白名单 → 文件在安装包里直接消失

**`electron/package.json` 有两个独立白名单，新增资源两边都要想到**：

| 白名单 | 落地 | 漏配后果（v3.15.0 实例） |
|---|---|---|
| `extraResources` | `resources/` 文件系统 | backend 那条 `filter` **只含 `.py/.pyd/.dll/...`，`.json`/`.md` 不会被带**。`plugin.schema.json` + `customer-codes.md` 从未进包 → 插件安装 500 `PLUGIN_MANIFEST_SCHEMA_FAIL` / `FileNotFoundError: plugin.schema.json 不存在`。修复：单列 `{"from":"../docs/plugin-system","to":"docs/plugin-system"}` |
| `files` | `app.asar`（主进程侧） | 只写了 `splash/**/*`（手势动画目录），漏了根目录 `splash.html`（旧版简单动画）。旧版 splash 窗口 `transparent:true`，`loadFile('splash.html')` 失败即**全透明空窗肉眼不可见** → 启动从桌面到主界面全程无动画。修复：`files` 加 `splash.html` |

**排查模板**：

| 现象 | 第一步看 | 根因 | 修复 |
|---|---|---|---|
| 插件装不上 / 报 schema 不存在路径 `resources\docs\plugin-system\...` | 客户机该路径文件是否存在 | extraResources 没打 docs | 加 extraResources 条目 + 重新打包（或热补丁补文件） |
| 启动空桌面几秒后主界面突然出现、无任何 splash | 客户配置 `splash.enabled`（默认 false 走旧版） | `splash.html` 没进 `files` → 透明空窗 | `files` 加 `splash.html` 重新打包 |
| 任何"代码里 `loadFile`/读文件但客户机找不到" | 该资源在哪个白名单 | 大概率漏配 | 加白名单 + **build.yml 自检清单同步加一条** |

> **CI 自检兜底**：v3.15.1 起 `build.yml` 有「Verify packaged resources」step 硬验证关键资源在产物里，缺则红灯阻断发版。新增运行时资源时顺手加进自检清单。详见 `build-release` skill 第四节。

### 陷阱二：就绪健康检查太浅 → 主界面显示了但 GPU 子系统还没热

**症状**：进设置页右上角偶发弹「获取GPU列表失败」/ CUDA 显示不可用（但后端日志 GPU 实际正常）。

**根因链**：
1. `backend-manager.js` 的 `checkHealth()` 探的是 `/api/v1/source/status`——**纯对象状态读取，完全不碰 torch**。进程一起来、路由一挂，立刻判「就绪」显示主界面。
2. 项目**未激活**时，后端启动 `auto_load_active_project()` 不加载任何模型 → 全程不碰 GPU → CUDA 上下文是冷的。
3. 主界面一进来，`Settings/index.vue` 的 `onMounted` 并发打 6+ 个请求，其中 GPU 列表查询首次调 `torch.cuda` 触发 **CUDA 上下文冷初始化（实测约 5 秒）**，叠加并发竞争 → 偶发超时失败。

**修复（v3.15.1）**：
- 后端 `main.py` 启动后台线程**预热 CUDA**（`torch.cuda.init()`），把 5 秒冷开销移到启动期（splash 期间用户本就在等），幂等 + 异常隔离 + 无 GPU 自动跳过。
- 前端 GPU 列表查询在**页面自动加载**时失败静默重试 2 次再提示；手动点刷新保持立即提示。

> **不要**为这个去把健康检查改成「等 GPU 就绪」——那会拖慢每次启动。预热 + 前端容错是更对的解法。

### 陷阱三：插件前端资源被打包环境 HTTP 缓存焊死（v3.17.3）

**症状**：客户机插件升级后新 UI（如金龙「步骤三档配置列」）始终不出现；卸载、重装、重启软件、换不同版本的包反复折腾都没用；**开发机一切正常**。客户机加载日志里 `register()` 只注册出旧槽（如只有 `monitor.layout.body`），缺新槽（如 `project.step-cell.durations`）。

**致命误导**：加载日志 `ESM 文本已拉到 {"bytes": N}` 的 `N` 是 `text.length`（字符数，中文按 1 算），**不是字节数**。排查时别拿它跟磁盘文件字节数直接比，会得出「文件不一样」的错误结论。要确认磁盘真实文件，用 PowerShell `(Get-Item '...index.esm.js').Length` 看字节数。

**根因链**：
1. 前端 `usePluginLoader.js` 拉 ESM 的 `fetch` 未禁缓存，后端 `plugins.py` 的 `get_active_asset` 发资源的 `FileResponse` 也未设禁缓存头。
2. 打包后主窗口走 `file://`，跨源拉 `http://localhost:8001/api/v1/plugins/active/assets/...`，**Chromium 缓存极其激进**，把第一次装上的旧 ESM 焊死在 disk cache。
3. 之后客户换包/升级，磁盘文件**确实被成功替换**（安装没问题、不是文件锁），但 fetch 始终命中缓存返回最早那份旧文件 → 旧 `register()` 注册不出新槽 → 新 UI 永不出现。
4. 开发机走 vite 开发服务器默认不缓存，故**永远不复现**——典型「开发机好、客户机坏」的打包环境专属陷阱。

**修复（v3.17.3）**：
- 前端 `fetch(url, { credentials: "omit", cache: "no-store" })`。
- 后端 `FileResponse(..., headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"})`。

**现场止血（不发新版也能救）**：完全退出软件（确认 `python.exe` 进程已结束）→ 删 Electron 缓存目录 → 重启：
```
powershell "Remove-Item -Recurse -Force '%APPDATA%\tianjun-ai-vision\Cache','%APPDATA%\tianjun-ai-vision\Code Cache','%APPDATA%\tianjun-ai-vision\GPUCache' -ErrorAction SilentlyContinue"
```
重启后看 `ESM 文本已拉到` 的数字变化 + 是否多出新槽 `slot registered`，即可确认缓存被清、读到真文件。

> **排查口诀**：插件「换包不生效 / 升级看不到新功能」且开发机正常 → 先怀疑缓存（清 Electron 缓存验证），**别**先怀疑用户装错或文件锁。
