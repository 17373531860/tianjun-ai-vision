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
         display: { brandName, appName, inspectorName, deviceNumber, 
                    navbar toggles, monitor panel toggles, defaultCounterVisibility, ngDisplayMode },
         detection: { boxColors, lineWidth, fontSize, confidenceDisplay, 
                      ngReasonDisplay, voice, toasts: { systemPresets, customToasts } },
         performance: { frameLimitEnabled, targetFps, useHalf, mediapipe* } }
```
- display/detection/performance 各自 localStorage 持久化
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
  "latency_ms": 15
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

## 已知陷阱
- License检查异常时 `licenseChecked` 仍设为 true（静默通过）
- `@vueuse/core` 和 `sortablejs` 依赖可能未使用
- i18n 绝大部分文字硬编码中文，切换语言基本无效
- `performance.memory` 仅 Chrome 支持
- `index.html` 标题和图标还是 Vite 默认的
- Monitor 组件的 `isRunning`/`isPaused`/`isDetecting` 是组件局部 ref，remount 后全部重置为 false，需通过 `getSourceStatus()` 与后端同步
- `startDetection()` 的 resume 快捷路径不传模型路径，切换项目后必须确保状态标志被重置（否则用旧模型推理无结果）
- Vite HMR 编辑 Monitor.vue 会导致组件重载，等同于页面导航离开再回来
