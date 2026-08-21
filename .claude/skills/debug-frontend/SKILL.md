---
name: debug-frontend
description: "诊断前端问题：Monitor视图轮询异常、状态不同步、ECharts渲染错误、Toast/语音不触发、双缓冲MJPEG问题、多工位显示异常。当页面显示不正确或交互失效时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__playwright, mcp__context7, mcp__sentry, mcp__sequential-thinking"
---

# debug-frontend: 前端诊断（v3.5.x）

诊断天军 AI 视觉检测系统的 **Vue3 + Pinia + Element Plus + ECharts** 前端。

用户问题: $ARGUMENTS

> 事实校验基线：`frontend/src/views/Monitor/index.vue` (6847，v3.47 多工位布局重构后) / `views/Data/index.vue` (1985) / `store/useSystemStore.js` (277)。
> v3.47 多工位布局：模板顶层链 `layoutBodyOverride → channelCount===2 → ===3（三行横排）→ >3（网格总览+分页+放大详情）→ 单工位`；4+ 工位只拉**可见工位**的 MJPEG 流（`visibleStreamChannels/syncMultiStreams`），数据轮询仍覆盖全部工位——排查"翻页后某工位没画面"先看该工位是否在当前页/放大路。
> v3.48.1 快照轮询回退（多工位视频"加载不出/黑屏"治本）：浏览器同 host HTTP/1.1 只有 **6 条并发连接**，可见工位 > `MAX_MJPEG_STREAMS`(4) 时 `syncMultiStreams` 掐掉全部 MJPEG 长连接改走 `/snapshot?channel=N` 单帧轮询；另外任一工位 MJPEG **连续 2 次零帧断流**（`mjpegZeroFrameFails`，WebKit fetch 不支持 multipart/x-mixed-replace 的典型症状）也会单独降级快照。取帧节奏 `_snapshotIntervalMs()` 按并发工位数自适应：≤2 路 80ms（~12fps）/ ≤4 路 120ms / ≤9 路 200ms / 更多 300ms。排查"画面像幻灯片"先分清**显示帧率**（快照节奏决定）和底部 FPS（后端推理帧率），两者本来就不相等。
> 前端通过 `axios.create({ baseURL })`，dev 默认 `http://localhost:8001/api/v1`，Electron 走 `file://` 时取 `DEFAULT_BACKEND_HOST`，浏览器经 Vite proxy。

---

## 1. 前端架构（v3.5.x 真相）

```
frontend/src/
├── main.js                          Vue3 + Pinia + ElementPlus + i18n
├── router/index.js (147)            Hash 路由 + License 守卫 + 路由记忆
├── api/index.js                     axios 实例（baseURL=/api/v1） + getBackendHost()
├── api/{detection,project,model,data,mes,scanner,export,...}.js
├── store/  (4 个 Pinia)
│   ├── useSystemStore.js (277)      display/detection/performance（v3.5.1 加 ptMode/ctMode/ngTopDisplayMode）
│   ├── useProjectStore.js           当前项目（无持久化）
│   ├── useSourceStore.js            视频源（localStorage）
│   └── useScannerDisableStore.js    扫码器禁用记忆（v3.4.2）
├── layout/
│   ├── index.vue                    侧边栏 + Navbar + BottomBar
│   └── Navbar.vue (~550)            项目选择器 + 自动恢复 + 时钟
└── views/  (12 个)
    ├── Monitor/index.vue (4051)     检测中心（项目最大 .vue）
    ├── Data/index.vue (1985)        数据中心 + 自定义导出
    ├── Project/index.vue (2925)     项目配置
    ├── Source / Model / Alarm / Settings / Activation
    └── MES/  (5 个 Panel: Order / Workpiece / Defect / Scanner+WMax / Gateway)
```

> **死代码**（不要花时间排查）：`api/task.js`、`api/camera.js`、`Report/index.vue`（路由未挂）、`utils/websocket.js`。

---

## 2. 4 个 Pinia Store（v3.5.x 字段表）

### useSystemStore（核心）
- `display.brandName / appName / inspectorName / deviceNumber` — 全局基本信息
- `display.navbar.{brandName, appName, projectSelector, inspector, deviceId, mode, status, runtime, realtime}` — 导航栏开关
- `display.monitor.{stepStrip, statsPanel, defectChart, capacityChart, stepTable, showFps, showLatency, showDetectionCount}` — Monitor 面板开关
- `display.monitor.ctIncludeNg` — CT 是否含 NG（默认 false）
- **`display.monitor.ptMode` / `ctMode`**（**v3.5.1 新增**）— `'avg' | 'last' | 'current'`
- `display.monitor.ngTop3 / ngTopDisplayMode`（v3.5.1）— NG Top3 显示模式 `'percentage' | 'count'`
- `display.monitor.defaultCounters.{showTotal, showGood, showBad, showNgSteps}` — 默认计数器开关
- `detection.{boxColor, boxColorNG, boxLineWidth, labelFontSize, showConfidence, showNgReason, voiceEnabled, voiceVolume}`
- `detection.toasts.{ok, ng, scan, warn_no_barcode}` + `customToasts[]`
- `performance.{frameLimitEnabled, targetStreamFps, halfPrecision, mediapipeEnabled, ...}`
- `developerMode`（密码保护，localStorage `developer_mode`）

**持久化键**：`display_settings` / `performance_settings` / `developer_mode` / `detection_settings`（备份）。
**深拷贝/深合并**：`detection` 初始化 `JSON.parse(JSON.stringify(defaultDetection))`；`loadSettings()` 对 `display.monitor.defaultCounters` 做兜底默认值。

### useProjectStore
`{ currentProjectId, currentProjectName, currentProject, isRunning }`，无持久化，靠 Navbar.vue 的 `restoreLastProject()` 启动恢复。

### useSourceStore
持久化到 `localStorage['tianjun_source_config']`，覆盖 6 种源：camera / video / image / hikvision (NVR) / hcnetsdk / industrial (MvCamera) / rtsp。

### useScannerDisableStore
v3.4.2 加：每个扫码器设备的"主动禁用"标记，影响 Monitor 上的"未绑码"提示。

---

## 3. Monitor 视图核心（4051 行，按职责切片）

### 3.1 启停三条路径
1. **standby 恢复**：`isRunning && !isDetecting` → `resumeInference()` — 不传模型路径
2. **暂停恢复**：`isPaused` → `resumeDetection()` — 不传模型路径
3. **完整启动**：`resolveModelPath()` → `setProjectConfig()` → `apiStartDetection(modelPath)`

> 路径 1/2 不传模型，**切换项目时若忘了 reset `isRunning/isPaused/isDetecting`，会用旧模型推理**。Monitor 已有 `watch(currentProject)` 强制重置（line 1947 附近）。

### 3.2 轮询机制（v3.5.x 真值）
| 视图 | 间隔 | 实现 | 端点 |
|---|---|---|---|
| Monitor 单工位 | **150ms** (`setInterval`，重叠保护 `pollingInProgress`) | `pollingTimer` (line 3099) | `GET /api/v1/source/detection/results?channel=0` |
| Monitor 多工位 | **150ms** (`Promise.all` 并发) | `multiPollingTimer` (line 1650) | 同上，按 `channel=ch` 并发 N 次 |
| Data 页 | 1000ms（仅刷计数器） | `pollingTimer` (Data line 1089) | 同上 |

> 老版文档写"300ms / 200ms"已过时，以代码 `}, 150);` 为准。

`/source/detection/results` 返回核心字段（实际全集 50+ 字段，按需取）：
```json
{
  "isRunning": true, "isDetecting": true, "fps": 30, "latency": 12,
  "detections": [...],            // 检测框（归一化坐标 x/y/w/h）
  "counters": {...},              // 计数器
  "step_counts": {...}, "step_durations": {...},
  "avg_step_durations": {...}, "last_step_durations": {...},
  "step_intervals": {...}, "current_cycle_steps": [...],
  "average_cycle_time": 0, "average_cycle_time_with_ng": 0,
  "last_cycle_time": 0, "last_cycle_time_with_ng": 0,
  "current_cycle_time": 0,
  "recent_events": [{ "id", "event_name", "is_ng", "should_warn_no_barcode", ... }],
  "tracking": {...}, "video_info": {...},
  "periodic_actions": [...],      // v3.5.0
  "mes": { "workpiece": {...}, "order": {...}, "scan_event": {...},
           "warn_no_barcode": false, "rebind_prompt": null,
           "recording_failures": [...] }
}
```

### 3.3 MJPEG 双缓冲（**改前端 Monitor 必读**）

**单工位**（line 1149-1944）：两个 `<img>` 元素 `streamSrc0` / `streamSrc1` 交替持流，避免切流时 Chromium 解码器内存累积导致崩溃。
- `connectStream()` 把 `streamSrc0 = ${getBackendHost()}/video_feed?t=${Date.now()}` 立即生效，`streamSrc1=''`。
- 轮询每 600 次（≈90 秒）调一次 `swapStream()`：先把背景 img src 设为新 URL，等 `onStreamReady()`（其 onload）触发后翻面 + 清空旧 src 释放内存。
- `onStreamError()` 累计 `streamErrorCount`，按 `min(errCount * 300, 3000)ms` 退避重连，>50 次放弃。
- `forceReconnectStream()` 在 start/stop/resume/pause 路径都会调（保证流跟上后端状态）。

**多工位**（line 1295-1370）：每个通道一条独立 fetch + ReadableStream，自己解析 `--frame` boundary 拿到 JPEG 字节，解码到 `multiVideoCanvasRefs[ch]` canvas。检测框画在覆盖层 `multiCanvasRefs[ch]`。
- `STREAM_HOST = 'http://localhost:8001'`、`BOUNDARY = '--frame'`、`HEADER_END = '\r\n\r\n'`。
- `multiStreamAborts[ch]` 是 `AbortController`，`stopMultiStreams()` 必须 abort 所有，否则切回单工位时 socket 泄漏。
- buffer 初始 512 KB，碰到大帧自动 grow。

**通道切换不闪烁**：多工位视图下 4 路 canvas 全部常驻，`selectedChannel` 只决定"哪一路放大显示"，**画面不重连**。切换工位数 (`channelCount`) 时调 `initMultiChannelData(count)` + `startMultiStreams(count)`，**老通道的 stream 先 abort 再重建**。

### 3.4 检测框 Canvas 渲染

**v3.5.x 三层 clip 防御**（line 1681 `clipNormalizedBox`）：
```javascript
const clipNormalizedBox = (det) => {
  const x = Math.max(0, Math.min(1, Number(det.x) || 0));
  const y = Math.max(0, Math.min(1, Number(det.y) || 0));
  const w = Math.max(0, Math.min(1 - x, Number(det.w) || 0));
  const h = Math.max(0, Math.min(1 - y, Number(det.h) || 0));
  return { x, y, w, h };
};
```
即使后端漏 clip / Kalman 滤波偶发飘出，前端也保证框画在画面内。

**适配实际帧分辨率**：`multiFrameNaturalSize[ch] = {w, h}` 是 `<img onload>` 时记录的原图尺寸；`drawMultiDetections()` 按 `Math.min(cw/nat.w, ch/nat.h)` 等比缩放并居中（`dx/dy`），与 MJPEG `<img>` 的 `object-contain` 行为对齐。**改这里要同时改单/多工位两条路径**。

### 3.5 Toast 事件触发（**v3.5.x 后端权威决策**）

`processChannelResult()`（line ~1480-1635）按 `event.id` 去重（`shownEventIds` Set，>500 时收缩到 200）。

**warn_no_barcode 关键改动**（line 1622-1633）：
```javascript
const _shouldWarn = event.should_warn_no_barcode !== undefined
  ? !!event.should_warn_no_barcode
  : (!event.barcode && !event.is_ng);  // 旧兼容路径
```
> v3.5.x 起 **后端在 `event.should_warn_no_barcode` 上盖章**（综合 `is_warn_no_barcode()` + 是否有扫码器在场 + 工位是否禁用扫码）。**前端不要再自己叠加判断**，老兼容路径只在字段缺失时兜底。

**事件 → toastId 映射**：`ok` / `ng` / `scan` / `warn_no_barcode` / 自定义 `event.toast_id`。`getToastConfig(toastId, ch)` 优先取通道私有 `channelDetections[ch]`，没有再回落 `detection`。

### 3.6 PT / CT 显示模式（**v3.5.1 新增**）

```javascript
// PT — 步骤检测时间，单步级别
const formatStepPT = (stepLabel) => {
  const mode = systemStore.display?.monitor?.ptMode || 'avg';
  // avg → avgStepDurations[label] / last → lastStepDurations / current → stepDurations
};

// CT — 周期时间，整轮级别
const getDisplayCT = (chData) => {
  const mode = systemStore.display?.monitor?.ctMode || 'avg';
  const includeNg = systemStore.display?.monitor?.ctIncludeNg;
  // current 模式忽略 includeNg；avg/last 才看 ctIncludeNg
};
```

**数据来源**：`/source/detection/results` 同时下发 `avg_*` / `last_*` / 当前轮 raw（`step_durations` / `current_cycle_time`）三档原始数据，前端按 `ptMode/ctMode` 选档显示。后端**永远全量返回**，切换模式不需要重连。

> 改 `display.monitor.ptMode/ctMode` 还会同步影响 **Data 页 CSV 导出**：`exportCsv* (selectedDate, ..., ptMode, ctMode)` 把模式当 query 透传给 `/api/v1/data/export/csv?pt_mode=avg&ct_mode=avg`，`avg` 时多一列"耗时(平均/秒)"。

### 3.7 ECharts 渲染（仅单工位）

- `defectChartRef`（饼图：合格/不良）+ `capacityGaugeRef`（产能仪表盘）。
- `initCharts()` 必须 `dispose()` 旧实例再 `echarts.init()`，否则 HMR / remount 后双实例叠加内存翻倍。
- 节流：`CHART_UPDATE_INTERVAL = 2000ms`（`lastChartUpdate` 控制），轮询里只在间隔到了才 setOption。
- **多工位不画 ECharts**（`updateMultiCharts()` 内注释 `Data-driven rendering via template`），全部由模板/计数器格子展示。

---

## 4. 状态不同步问题排查

| 现象 | 排查 |
|---|---|
| 项目切换后画面/计数没变 | Pinia DevTools 看 `useProjectStore.currentProjectId` 是否变；Navbar 的 `handleProjectChange()` 是否走完 `setProjectConfig + reload detection settings`；Monitor `watch(currentProject)` 是否触发了 `isRunning/isPaused/isDetecting` 重置 |
| Settings 改了但 Monitor 不刷新 | localStorage 存的可能是旧 schema；尝试 `localStorage.removeItem('display_settings')` 后刷新；`saveDetectionSettings()` 应同步写 `updateProject(id, { detection_config })` + localStorage |
| 多工位部分通道丢数据 | 看 `multiChannelData[ch]` 是否被 `initMultiChannelData(count)` 初始化；`multiPollingInProgress` 重叠保护是否卡死（错误 `finally` 路径） |
| 后端心跳超时画面不黑 | `forceReconnectStream()` 走 `streamErrorCount` 退避，看 onStreamError 是否被触发；MJPEG 长连接经过 Electron 主进程时被防火墙断流不会触发 onerror，需要轮询里"isRunning && !streamSrc0 && !streamSrc1" 兜底重连 |
| F5 刷新后状态全丢 | 正常，Monitor 组件无 keep-alive，`onMounted` 走 `getSourceStatus()` 跟后端同步 `isRunning/isDetecting` |

---

## 5. 通用诊断步骤（按顺序）

1. **复现现象**：单工位/多工位、源类型、是否带 MES、是否扫码器在场，记下 channel_id。
2. **DevTools Console**：找 `[API]` / `[MJPEGStream]` / `[ChannelManager]` 等前缀的 warn/error；License 异常前端会静默通过（`licenseChecked=true`），不要被骗。
3. **DevTools Network**：
   - `/api/v1/source/detection/results` 状态码 / 频率（应 ~150ms）/ payload 字段是否齐全。
   - `/video_feed?channel=N` 是否 200 且 `Content-Type: multipart/x-mixed-replace; boundary=frame`；多次断开重连说明退避机制在跑。
   - 比对 `event.should_warn_no_barcode` 真值，确认是后端决策还是前端兜底。
4. **Pinia DevTools**：`useSystemStore.detection.toasts.warn_no_barcode.enabled`、`useSystemStore.display.monitor.ptMode/ctMode`、`useProjectStore.currentProject`。
5. **Vue DevTools**：定位组件树到 `MonitorIndex`，看 `multiChannelData[ch]`、`activeStream`、`streamSrc0/1`、`shownEventIds.size`、`isRunning/isPaused/isDetecting` 是否符合预期。
6. **后端对照**：用 `debug-source` / `debug-channel` / `debug-mes` 的现象表对照后端是否真出问题。

---

## 6. 已知陷阱（v3.5.x）

- `Activation/index.vue` 在浏览器（非 Electron）会 `router.replace('/')`；想浏览器调试激活流，要么改路由要么直接读 `electron/license-manager.js`。
- `Settings/index.vue` 的"基本信息设置"是卡片标题不是按钮，自动化点击它会失败。
- 操作员"删除"是后端软删除（`active=false`），前端默认列表应该过滤 `active=true`，否则用户以为没删。
- MES 的"模拟扫码/模拟数据"按钮只在开发者模式可见（`localStorage['developer_mode']='true'`）。
- `index.html` title/icon 还是 Vite 默认（无影响但容易让 QA 误报）。
- Monitor remount 后所有局部 ref（`isRunning/isPaused/isDetecting`）重置，需要 `getSourceStatus()` 同步；Vite HMR 等同 remount。
- 前端硬编码中文很多，i18n 切语言基本无效（不是 bug，是产品决策）。
- License 验证异常仍会设 `licenseChecked=true` 静默通过（设计如此）。
- `detection_config` 在 DB 可能是 `null` 或字符串 `"null"`，`loadDetectionFromProject` 已处理两种情况。

---

## 7. 路由记忆（v2.7.x，仍生效）

- `localStorage['tianjun:lastRoute']`，白名单 `Monitor / Project / Model / Data / Source / Settings / Alarm / MES`（**Activation 不记忆**）。
- `router.afterEach` 写入；`router.beforeEach` 仅在冷启动 (`from.name === undefined && to.path === '/monitor'`) 触发一次重定向（`lastRouteRestored` 标志位）。
- 想跳过：`localStorage.removeItem('tianjun:lastRoute')`。

---

## 7.x. v3.7.3 关键踩坑（Monitor 副模型启动 / MJPEG 后来者上位 / Project 副模型 UI）

### 7.x.1 Monitor 启动检测时副模型不加载（极坑）

**症状**：Project 页配好副模型 → Monitor 点开始 → 提示"副模型未加载"，只跑主模型。

**根因 A（store 永远旧）**：`syncProjectConfig` 把 `getProjectDetail(projectId)` 返回值当 project 用，但它**是 axios response** —— 真 project 在 `.data` 里。旧代码 `const fresh = await getProjectDetail(proj.id); if (fresh && fresh.id) ...` 永远不进 if，store 永远是老配置 → `extraSlots = []` → 走单模型路径。

```javascript
// ❌ 错: fresh 是 axios resp, fresh.id 是 undefined
const fresh = await getProjectDetail(proj.id);
// ✅ 对:
const resp = await getProjectDetail(proj.id);
const fresh = resp?.data;
```

**根因 B（resume 分支跳过 pipeline）**：后端启动期 `_reload_model_for_active_project` 自动装主模型 → Monitor mount 时 `getSourceStatus` 看到 `model_loaded=true` 把 `isPaused` 标为 true → 启动走 `resumeDetection()` 分支，它**不重新解析 `pipeline_config.models`**，副模型彻底没机会装。

**v3.7.3 修法**：`startDetection` 顶部计算 `_hasExtraSlots`，若为 true 强制 `apiStopDetection()` 后走完整启动路径（release + load 主+副），bypass resume 分支。

**调试**：客户反馈"副模型没加载"先让他 F12 看 `[Monitor/start-detection]` 诊断日志 — 里面会打 `extraSlotsLen` / `extraSlotsDetail`，立刻能看出是 store 没更新还是没走对启动路径。

### 7.x.2 MJPEG 切换路由 / 刷新黑屏几秒

**症状**：从 Project 切回 Monitor、或浏览器刷新、或多 client 连同一通道，新页面 `<img>` 黑屏 2-5 秒才出图。

**根因**：Chrome HTTP keep-alive 不立即关闭旧 MJPEG socket，旧 `_generate_mjpeg_stream` generator 仍在 `with frame_lock:` 里 hold 锁 + thread-pool worker；新连接抢不到锁就一直没首帧。

**v3.7.3 修法**（后端，前端无改动）：每条 generator 分配 `connection_id`，记录 channel 当前最新 id 到 `_mjpeg_active_conn_id`；旧 generator 每次 yield 前检测自己是否过期，过期主动 `break` 释放资源。同 channel 同时只保留 1 条 generator。

**前端没改动 = 用户看到的差别**：切 Monitor 路由首帧延迟从 2-5s 降到 < 200ms，不再有"黑屏一闪"。如果客户机 Chrome 版本极老 / 走代理时还能遇到，多半是反代层 keep-alive 设置过激进。

### 7.x.3 Project 页删副模型后 step label 残留

**症状**：Project 页删副模型 → 步骤详情里它的 step 还在 → 改东西保存 → Monitor 报"step xxx 的 from_model 找不到对应 model"。

**老坑**：`removeExtraModel` 只 `splice(extra_models)`，不动 `steps_config` / `sequence_order` / `detection_steps` / `custom_*` / `simultaneous_groups`。

**v3.7.3 修法**：
- `removeExtraModel` 加 `ElMessageBox.confirm` 列出会被一起删的 step，确认后 `onStepEnabledChange(step, false)` 清各引用 + 删 step 本体
- `initProjectDefaults` 加孤儿步骤自动清理：加载老项目时若 `from_model` 不再有对应 slot，自动剔除 step + sequence/condition 引用

**调试**：客户报 "from_model 找不到 model" 报错先看 v3.7.3 自动清理日志（`console.warn('[Project] 自动清理孤儿步骤: ...')`），多半是历史脏数据，重新打开项目就会自愈。

### 7.x.4 Project 页副模型参数 UX

v3.7.3 起 IoU / 优先级 / FP16 收进"高级参数 ▾"折叠区（默认收起），客户截图反馈"参数不见了"实际是没展开折叠区。

非 `pytorch_fp32` 格式时 FP16 checkbox 自动 disable + tooltip 解释（精度由编译文件决定，这开关无效）。客户切到 TensorRT 后误以为"FP16 失效"实际是设计如此。

---

## 8. 历史踩坑速查（详细见 changelog）

| 版本 | 修复 |
|---|---|
| v3.37.0 | Navbar 5s 轮询跟随后端激活项目（外部 MES 开工切项目界面自动跟上，`syncActiveProjectFromBackend`，跟随不回调 activate 防重载模型）; **Logo 打包版回退回归**——public 资源地址写进运行时字符串（`'/app-icon.png'`）构建期改写不到，file:// + 相对 base 下解析到盘根 404，必须用 `import.meta.env.BASE_URL + '文件名'` 拼（静态 src 没这个问题，Vite 会编译成 import.meta.url 相对定位） |
| v3.7.3 | 副模型启动强制全启动 / syncProjectConfig 取 resp.data / MJPEG 后来者上位 / 孤儿步骤清理 |
| v3.5.1 | PT/CT 三档显示（ptMode/ctMode）+ Data 导出联动 |
| v3.5.0 | 周期性强制动作进度面板 + 自定义 Toast 事件 + 自定义导出对话框 |
| v3.4.2 | useScannerDisableStore + ERROR 不续 LON + 禁用扫码守门 |
| v3.4.1 | D 模式扫码器灯一直闪 + 校验工位拿错 |
| v3.3.0 | scan_pair 状态机 → Monitor 上 warn_no_barcode banner |
| v3.1.3 | MJPEG 双缓冲 + onMounted 流重连不黑屏 |
| v3.0.0 | Monitor 项目切换状态重置（防用旧模型） |
| v2.7.x | 路由记忆 + Monitor 状态守卫 |
| v2.6.0 | 多工位视图（双/四工位） + ChannelManager 隔离 |
| v2.5.0 | Toast 系统 + GatewayPanel modbus_rtu 适配 |

> 改 Monitor 前**必看**关键不变量（AGENTS.md 第八节第 7 条）：双缓冲 MJPEG + 多通道 state 隔离，v2.6 / v3.0 / v3.1.3 多次回归过。

---

## 8.5 调试中心 (v3.19.0+) — 前端排障首选入口

- 入口：设置页「调试设置」Tab（需开发者模式，`localStorage.developer_mode`），实时日志查看器支持分类过滤/关键词搜索/暂停/导出
- 前端埋点工具：`frontend/src/utils/debug.js`（`dbg(category, title, detail)`），日志批量回传后端 `POST /api/v1/debug/client-log`，与后端日志统一进环形缓冲 + 落盘
- 已埋点位置：路由跳转与鉴权守卫（`router/index.js`）、axios 请求/响应/错误拦截器（`api/index.js`）、Monitor 等核心视图按钮交互
- 前端开关存 Pinia `useDebugStore`，后端开关经 `/api/v1/debug/flags` 读写；默认全关零差异
- 给新组件加埋点：import `dbg` → 在交互处理器里 `dbg('frontend.xxx', '动作', '细节')`，分类需在 debug_center 的目录里注册才会出现在面板

## 9. 改前端时配套读什么 skill

- 改 Vue 组件 / Pinia store → `modify-frontend`
- 改后端 API → `modify-api` + `api-sync`
- 改项目配置（pipeline/steps/events）→ `modify-project-config`
- 视频流卡顿/录像异常 → `debug-video`
- 多工位串扰 → `debug-channel`
- 扫码 / 工件 / Toast 联动 → `debug-mes`
- License / Splash / 关机 → `debug-electron`

---

## v3.40.0 补充：监控页"后端自行拉起检测"的前端接管机制（川南反馈）

**场景**：检测中心处于停止态（无轮询无取流），中控开工报文让后端自动开始检测。老行为前端毫无感知（开始按钮不灰 / FPS 0 / 信息条不出），切页再切回才恢复。

**v3.40 双保险**（都在 `Monitor/index.vue`）：
1. **空闲看门狗** `startIdleWatchdog`：空闲时每 2s 探一次源状态，发现后端 `is_running` 就自动接管（同步运行/检测态 + 起轮询 + 重连画面）。轮询已在跑 / 多工位（multiPolling 覆盖）/ 用户操作中都休眠不抢。
2. **轮询真相源同步**：`startPolling` 循环里以后端 `is_detecting`/`is_running` 为真相源回写前端状态，`isOperating` 期间不抢（按钮乐观更新优先）。

**排查要点**：客户报"后端在跑前端没反应"先看这两处是否被绕过（如新加的启动路径没走 `getSourceStatus` 暴露 `is_running`）；回归护栏 `tests/e2e_browser/test_idle_watchdog_adopt.py` + UAT `tests/uat/uat_20260716_cn_idle_autostart_adopt.py`。
**关联但独立**：末步结果列随余像闪烁回退是另一个修复（`_posDone` 权威 PT 锁定，UAT `uat_20260716_cn_last_step_flicker.py`），后端侧还有严格前缀守门（见 `debug-source` v3.40 节）。


## v3.51.5 补充：多工位启动恢复三修（捷昌 B 站现场）

1. **多工位禁用单工位 localStorage 兜底**：`Monitor/index.vue` `autoRestoreSource()` 先查后端 `channel_count`，>1 直接跳过 localStorage 恢复——旧缓存的 `device_index` 会与后端多通道恢复赛跑抢相机，酿成"左工位显示右工位的流+模型、右工位黑屏"串位事故。单工位行为不变。回归护栏 `tests/e2e_browser/test_monitor_autorestore_guard.py`。
2. **is_running 跳变强制重连流**：`processChannelResult` 检测到某通道 `is_running` false→true（后端迟到恢复），重置 `mjpegZeroFrameFails`、断开旧 MJPEG、`syncMultiStreams()` 重连——治"视频源加载完还要切页才出画面"。
3. **fetchChannelCount 失败重试**：进页时后端未就绪导致 `getWorkstations` 失败，3 秒后重试而不是锁死单工位模式。
4. **WorkpiecePanel 多工位默认关"仅当前项目"**：多工位下"当前项目"只是最后激活的项目，默认过滤会藏其它工位项目的工件 → 操作员批量删除删不干净 → `strict_ok_dedup` 拒码找不到原因。`channel_count>1` 时 `onlyCurrentProject` 默认 false。回归 `tests/e2e_browser/test_workpiece_panel_multiws_v3515.py`。

## v3.52.0 补充：多屏工位显示一期（Monitor kiosk 模式 + 多屏流控）

**架构**：主屏放大态与副屏 kiosk 共用 `SingleChannelMonitor.vue`（只消费父级轮询数据与 canvas 注册，不自行取流）；kiosk 由 Electron 子窗加载同一 `/monitor` 路由 + `?kiosk=1&channel=N&readonly=0/1` query。

1. **kiosk 写通路全屏蔽**：`kioskMode` 下不渲染 Toast/人工确认覆盖层/权限提升弹窗/录像异常面板/插件 layout 覆盖/工件流指示/外部报警横幅，`onMounted` 提前 return（不起扫码、自动恢复、看门狗），`showMultiToast` 直接 return。只轮询 `kioskChannel` 一个工位 + 一路视频。排查"副屏怎么不弹 XX"先想到这是设计不是 bug。
2. **多屏流控**（后端每通道只保留最新一条 MJPEG 连接）：多屏开启时主屏总览强制快照轮询（`syncMultiStreams` 的 `useSnapshotAll`）为副屏让出长连接；主屏放大只拉目标工位一路——**会踢掉该工位副屏的 MJPEG**，副屏靠 v3.48.1 零帧检测降级快照。客户报"副屏画面变卡"先问主屏是不是停在放大态。
3. **⚠️ 不变量：网格总览整卡点击=放大单路（v3.47 交付行为），多屏开关不得改变**。v3.52 合并审计拦下过一次"多屏关闭时误改为仅选中"的回归；2/3 工位整卡点击=选中不变。双向守门：`tests/e2e_browser/test_multi_workstation_layout.py::test_grid_overview_pagination_and_zoom` + `test_multi_monitor_phase1.py::test_disabled_workstations_keep_legacy_selection`。
4. **kiosk 路由不污染主窗记忆**：`router/index.js` `isMultiMonitorRoute` 守门——kiosk query 不触发冷启动路由恢复、不写 `LAST_ROUTE_KEY`；`layout/index.vue` kiosk 下隐藏导航/底栏且不启 `startScanGun()`（⚠️ 现场焦点落副屏时 USB 扫码枪输入会丢，部署交代主屏持焦）。
5. **start 迟返操作锁释放**：单工位 `startPolling` 里"正操作 + 前端未 detecting + 后端 `is_detecting=true`"三条件齐 → 释放 `isOperating` 并对齐运行态（治 start HTTP promise 不返回按钮转圈）。

## v3.54 补充：检测主页自定义布局（拖拽排版）

**架构**（"样式接管"而非组件重排，核心 `frontend/src/views/Monitor/layout/monitorLayout.js` 模块级单例）：
- 模板给形态根容器打 `data-layout-canvas="<形态族>"`（single/dual/triple/grid/zoom），区块打 `data-layout-slot="<slot id>"`。无自定义布局时这些属性完全惰性，flex/grid 默认排版零差异。
- 某形态存在自定义布局（或编辑态）时，runtime 把画布内 slot 元素绝对定位到画布百分比坐标；双/三工位=编辑第一列、其余列镜像。原 inline style 接管前暂存，恢复默认原样还原。
- 存储：后端 `SystemConfig` KV（键 `monitor_layout.{form_key}`），API `/api/v1/system/monitor-layouts`（读不鉴权；写/删挂 `monitor.layout.edit` 权限）。**刻意不用 localStorage**——升级/备份/换机不丢。
- 编辑入口：显示设置「检测主页自定义布局」卡 →`/monitor?layout_edit=1`；编辑器 `layout/LayoutEditorOverlay.vue`（八向手柄/吸附/撤销重做/16:9 锁/z 序/localStorage 草稿防崩溃）。

**排查要点**：
1. "布局没生效/区块叠一起" → F12 看元素有无 `data-layout-applied="1"`；`GET /system/monitor-layouts` 看该形态键有没有数据；任何布局应用异常自动整体退场回默认渲染（`requestApply` 的 catch），所以"主页正常但自定义丢了"优先查布局 JSON 是否被判坏。
2. "升级后新区块看不到" → reconcile 兜底：布局不认识的新 slot 落左下兜底区（可再编辑），不会丢；布局里有、页面已删的 slot 静默忽略。
3. "改乱了救不回" → 显示设置「全部形态恢复默认」（`DELETE /system/monitor-layouts`），或编辑态工具条「恢复默认」只删当前形态。
4. kiosk 副屏不进编辑态（`LayoutEditorOverlay` 挂 `v-if="!kioskMode"`），但保存的布局对 zoom 形态照常生效。

回归护栏：`tests/test_monitor_layout_api.py`（21 单测）+ `tests/e2e_browser/test_monitor_layout_editor.py`（6 e2e）+ UAT `tests/uat/uat_monitor_layout.py`（14 断言，证据 `tests/uat/artifacts/monitor-layout/`）。
