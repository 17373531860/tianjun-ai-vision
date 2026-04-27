---
name: debug-frontend
description: "诊断前端问题：Monitor视图轮询异常、状态不同步、ECharts渲染错误、Toast/语音不触发、双缓冲MJPEG问题、多工位显示异常。当页面显示不正确或交互失效时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# debug-frontend: 前端诊断

你正在诊断天军AI视觉检测系统的 **Vue3 前端**。

用户问题: $ARGUMENTS

## 前端架构总览

```
frontend/src/
├── main.js                          -- 入口，Vue3+Pinia+ElementPlus+i18n
├── App.vue                          -- 根组件（仅 router-view）
├── router/index.js                  -- Hash路由，License守卫
├── api/                             -- Axios API 封装层
│   ├── index.js                     -- Axios实例，baseURL=localhost:8001
│   ├── camera.js, data.js, detection.js, model.js, project.js, report.js, task.js
│   ├── mes.js                       -- MES API (工单/工件/缺陷, 22端点)
│   └── scanner.js                   -- 扫码器 API (8端点)
├── store/                           -- Pinia 状态管理
│   ├── useProjectStore.js           -- 当前项目状态
│   ├── useSourceStore.js            -- 视频源配置（localStorage持久化）
│   └── useSystemStore.js            -- 系统设置（display/detection/performance）
├── layout/
│   ├── index.vue                    -- 主布局（侧边栏+Navbar+BottomBar）
│   ├── Navbar.vue (~500L)           -- 项目选择器+自动恢复+时钟
│   └── BottomBar.vue                -- 底部状态栏
├── views/
│   ├── Monitor/index.vue (~1600L)   -- 核心检测监控页
│   ├── Project/index.vue (~900L)    -- 项目配置
│   ├── Source/index.vue             -- 输入源设置
│   ├── Data/index.vue               -- 数据中心
│   ├── Report/index.vue             -- 报表
│   ├── Model/index.vue              -- 模型管理
│   ├── Alarm/index.vue              -- 报警设置
│   ├── Settings/index.vue           -- 系统设置
│   ├── MES/                         -- MES 管理 (v2.3.0+)
│   │   ├── index.vue                -- MES 主页 (Tab容器)
│   │   ├── OrderPanel.vue           -- 工单管理
│   │   ├── WorkpiecePanel.vue       -- 工件追溯
│   │   ├── DefectPanel.vue          -- 缺陷分析 (帕累托图 + 缺陷代码)
│   │   └── ScannerPanel.vue         -- VS600 扫码器管理
│   └── Activation/index.vue         -- 许可证激活
└── utils/
    ├── format.js                    -- 格式化工具（基本未用）
    └── websocket.js                 -- WebSocket客户端（未被任何视图使用）
```

## 3个Pinia Store

### useProjectStore
```javascript
state: { currentProjectId, currentProjectName, currentProject, isRunning }
actions: setCurrentProject(project), setRunningStatus(status)
```
- 无持久化，刷新丢失（靠Navbar自动恢复）

### useSourceStore
```javascript
state: { sourceType, cameraSettings, hikvisionSettings, rtspSettings, 
         hcnetsdkSettings, videoPath, imagePath, isStreaming, streamUrl }
actions: 各种setter, saveConfig(), loadConfig()
```
- **localStorage 持久化**，key: `tianjun_source_config`

### useSystemStore
```javascript
state: { language, theme, isDetecting, currentProjectId,
         developerMode,   // v2.3.0+ 密码保护开关 (localStorage: tianjun_developer_mode)
         display: { brandName, appName, inspectorName, deviceNumber, 
                    navbar toggles, monitor panel toggles, defaultCounterVisibility, ngDisplayMode },
         detection: { boxColors, lineWidth, fontSize, confidenceDisplay, 
                      ngReasonDisplay, voice, toasts: { systemPresets, customToasts } },
         performance: { frameLimitEnabled, targetFps, useHalf, mediapipe* } }
```
- display/detection/performance/developerMode 各自 localStorage 持久化
- `loadDetectionFromProject(config)` 从项目配置合并检测设置
- `saveDetectionSettings()` 写回项目DB + localStorage

## Monitor 页面核心逻辑 (~2800行)

### 项目切换与检测启动

**项目切换时的状态重置（2026-04修复）：**
```javascript
watch(currentProject, (newProject, oldProject) => {
  if (oldProject && oldProject.id !== newProject.id) {
    isRunning = false; isPaused = false; isDetecting = false;
  }
})
```
不重置 → 切换项目后点"开始"会走 resume 路径（不加载新模型）→ 用旧模型推理

**startDetection() 三条路径：**
1. **待机恢复:** `isRunning && !isDetecting` → `resumeInference()` — 不加载模型
2. **暂停恢复:** `isPaused` → `resumeDetection()` → 后端 `resume()` — 不加载模型
3. **完整启动:** 走到底 → `resolveModelPath()` → `apiStartDetection(modelPath)` — 加载模型

⚠ 路径1和2不传模型路径，切换项目后如果状态没重置会用错模型

**onMounted 流重连（2026-04修复）：**
- 后端运行中 → `forceReconnectStream()` + `startPolling()`
- 后端暂停中（有源+模型但未运行）→ 也 `forceReconnectStream()`（画面仍显示）
- 有源但未运行 → 也重连（不再黑屏）

### 轮询机制
```javascript
// 单工位: 300ms 间隔
// 多工位: 200ms 间隔
pollTimer = setInterval(() => {
    getDetectionResults(channel) → 更新 UI
}, interval)
```

返回数据结构:
```json
{
  "detections": [...],      // 检测框
  "counters": {...},        // 计数器
  "current_steps": [...],   // 当前步骤状态
  "events": [...],          // 事件列表
  "tracking_data": {...},   // 追踪模式数据
  "video_info": {...},      // 视频文件进度
  "fps": 30,
  "latency_ms": 15,
  "mes": {                  // v2.3.0+ MES 实时信息
    "current_workpiece": { "serial": "...", "status": "..." },
    "active_order": { "order_no": "...", "progress": 0.85, "yield_rate": 0.97 }
  }
}
```

### 三种显示模式
1. **单工位:** 主视频 + SOP卡片 + 右侧仪表盘
2. **双工位:** 左右分屏，各有视频+SOP+计数器
3. **四工位:** 2x2网格 + 选中通道详情面板

### Toast 事件系统
```javascript
// shownEventIds: Set 防止重复显示
// 检查新事件 → 匹配系统预设/自定义Toast → 显示动画
// Set 超过500条时清理到最近200条
```

### 语音 TTS
```javascript
// SpeechSynthesis API
// Chromium bug workaround: cancel() 后等 100ms 再 speak()
```

## 常见问题诊断

### v3.0 全功能 QA 结论
- `Activation/index.vue` 在浏览器环境会主动 `router.replace('/')`，这是为了只在 Electron 内走真实授权流程。浏览器访问 `#/activation` 跳回 Monitor 不影响客户桌面版授权，只影响浏览器调试。
- `Report/index.vue` 文件和 `api/report.js` 存在，但当前 `router/index.js` 与侧边栏没有 `/report` 入口；访问 `#/report` 会 no match。若要给客户独立报表中心，必须同时加路由、侧边栏入口，并复测查询与 CSV 导出。
- `Settings/index.vue` 的"基本信息设置"是卡片标题，不是按钮；自动化点击它失败不算功能问题。
- Settings 操作员"删除"是后端软删除（`active=false`），前端若继续拉全量 `GET /operators`，用户会误以为没删。若要符合直觉，列表默认传 `active=true`；如需恢复停用人员，再做"显示停用/恢复"入口。
- MES 的"模拟扫码/模拟数据"按钮只在开发者模式显示，Playwright 复测前要设置 `localStorage['tianjun_developer_mode']='true'` 或通过 UI 开启开发者模式。

### 页面状态不同步
1. 检查 Pinia store 更新是否触发响应式
2. Navbar 的 `handleProjectChange()` 是否完整执行
3. `systemStore.display = JSON.parse(saved)` 直接覆盖而非深合并（会丢失新字段）
4. 多处运行时计时器（Navbar + BottomBar）不同步

### Monitor 轮询异常
1. 检查 pollTimer 是否在 onUnmounted 中清除
2. 确认 `/source/detection/results` 端点返回正确数据
3. 多工位: 每个通道独立轮询，4通道 = 每秒20次请求

### MJPEG 黑屏
1. 单工位: 检查双缓冲 img 元素切换逻辑
2. 多工位: 检查 ReadableStream JPEG boundary 解析
3. 确认 `getBackendHost()` 返回正确地址
4. Electron: 直连 `localhost:8001`；浏览器: 经 Vite proxy

### Toast/语音不触发
1. 检查 `systemStore.detection.toasts` 配置是否正确加载
2. 确认事件 ID 未被 `shownEventIds` 过滤（重复事件不显示）
3. 语音: 确认浏览器 SpeechSynthesis 可用
4. Toast position 配置: top-left/top-right/bottom-left/bottom-right

### 检测启动失败 / 检测无结果
1. 检查 `startDetection()` 调用链:
   - 获取模型路径 → resolveModelPath
   - 发送项目配置 → setProjectConfig
   - 启动检测 → POST /source/detection/start
2. 任何一步失败都会导致检测未启动但UI可能已更新
3. **切换项目后无检测框:** 检查后端日志是否有新模型加载（`[ChannelManager] ch0 loaded NEW model`）。如果没有，说明前端走了 resume 路径没传模型
4. **页面导航后黑屏:** Monitor 组件无 keep-alive，每次进入都 remount。检查 `onMounted` → `getSourceStatus()` 返回的 `is_running` 状态

## 死代码文件（不要浪费时间看这些）
- `layout/MainLayout.vue` — 旧布局，未使用
- `layout/Sidebar.vue` — 占位组件，实际侧边栏在 index.vue 内联
- `views/Dashboard.vue` — 静态原型，未挂路由
- `components/Video/StreamCanvas.vue` — 未被引用
- `api/task.js` — 旧版任务API，已被 data.js 取代
- `utils/websocket.js` — WebSocket工具，Monitor用HTTP轮询代替
- `assets/css/main.css` — 可能未被导入

## Toast 系统 (v2.5.0+)

### 预设 Toast 类型
`useSystemStore.defaultDetection.toasts` 包含 4 个系统预设：
- `ok`: 合格提示框（绿色）
- `ng`: NG 提示框（红色）
- `scan`: 扫码成功提示框（青色，可在扫码器设备设置中开关）
- `warn_no_barcode`: 未绑码告警（黄色，周期结算无条码时触发）

### 关键注意事项
- **深拷贝**: `detection` 状态初始化必须用 `JSON.parse(JSON.stringify(defaultDetection))`，浅拷贝会导致嵌套的 `toasts` 对象共享引用
- **防御性渲染**: 设置页 `<el-card>` 需加 `v-if="store.detection.toasts?.ok"` 防止 toasts 为 null 时崩溃
- **project detection_config**: 数据库中存的 `detection_config` 可能为 `null` 或 `"null"` 字符串，`loadDetectionFromProject` 需正确处理

### 单通道 MES 数据传递
Monitor 单通道模式下 `startPolling` 必须显式赋值：
```javascript
multiChannelData.value[0].mes = data.mes
```
否则 `mesData` computed 属性为空，warn_no_barcode banner 和 scan toast 都不会触发。

## GatewayPanel Modbus 配置 (v2.5.0+)

`GatewayPanel.vue` 新增 `modbus_rtu` 适配器类型：
- 选中后切换为串口/TCP 配置表单（隐藏 REST 专属字段）
- `buildConfig()` 根据 `adapter_type` 分支构建不同 config
- `openEdit()` 需从 `cfg` 加载所有 Modbus 字段（transport/port/host/baudrate 等）

## 路由记忆机制 (v2.7.x 新增)

**问题背景**：之前每次启动应用都强制跳到 `/monitor`，无视用户上次离开的页面。

**实现位置**：`frontend/src/router/index.js`
- `LAST_ROUTE_KEY = 'tianjun:lastRoute'`（localStorage）
- `REMEMBERABLE_NAMES = {'Monitor','Project','Model','Data','Source','Settings','Alarm','MES'}`
- `router.afterEach`：每次成功导航后，如果 `to.name` 在白名单内 → 写入 localStorage（`Activation` 不记忆）
- `router.beforeEach`：**冷启动**判定（`from.name === undefined && to.path === '/monitor'`），从 localStorage 读出上次路径并 redirect 到那里；只触发一次（`lastRouteRestored` 标志位）

**排查清单**：
- 启动后还是默认到 Monitor？→ DevTools → Application → Local Storage 查 `tianjun:lastRoute` 是否被写入
- 想跳过记忆（演示场景）？→ `localStorage.removeItem('tianjun:lastRoute')` 或临时把名字从 `REMEMBERABLE_NAMES` 删
- 死循环跳转？→ 检查 `lastRouteRestored` 是否被重置；正常应该一次会话只跳一次

## 已知陷阱
- License检查异常时 `licenseChecked` 仍设为 true（静默通过）
- `@vueuse/core` 和 `sortablejs` 依赖可能未使用
- i18n 绝大部分文字硬编码中文，切换语言基本无效
- `performance.memory` 仅 Chrome 支持
- `index.html` 标题和图标还是 Vite 默认的
- Monitor 组件的 `isRunning`/`isPaused`/`isDetecting` 是组件局部 ref，remount 后全部重置为 false，需通过 `getSourceStatus()` 与后端同步
- `startDetection()` 的 resume 快捷路径不传模型路径，切换项目后必须确保状态标志被重置（否则用旧模型推理无结果）
- Vite HMR 编辑 Monitor.vue 会导致组件重载，等同于页面导航离开再回来
- `detection` 状态的 `toasts` 对象必须深拷贝初始化，浅拷贝会导致多项目间共享引用
- `detection_config` 为 null 时设置页 Toast 卡片不渲染，需 v-if 防御
