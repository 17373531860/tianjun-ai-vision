# 04 · 前端 + Electron 读码笔记

> 覆盖范围：`frontend/src/` 全部 97 文件（含 `Monitor/index.vue` 5867 行分段通读）、`electron/` 核心壳文件（`main.js` / `backend-manager.js` / `license-manager.js` / `preload.js`；vendor/splash 第三方资源仅记用途）。
> 行号锚点均来自当前仓库快照，后续改动以代码为准。

---

## 一、路由表

**文件**：`frontend/src/router/index.js`（197 行）

| 路径 | name | 组件 | 守卫/备注 |
|------|------|------|-----------|
| `/activation` | Activation | `views/Activation/index.vue` | 独立页；License 无效时跳转（行 168） |
| `/login` | Login | `views/Login/index.vue` | 独立页；鉴权启用且匿名兜底关时强制登录（行 143–146） |
| `/` | — | `layout/index.vue` | redirect → `/monitor`（行 20） |
| `/monitor` | Monitor | `views/Monitor/index.vue` | 默认首页；可记忆路由（行 77–78） |
| `/project` | Project | `views/Project/index.vue` | 检测中侧边栏禁用（layout 行 109–114） |
| `/model` | Model | `views/Model/index.vue` | 同上 |
| `/data` | Data | `views/Data/index.vue` | 同上 |
| `/source` | Source | `views/Source/index.vue` | 同上 |
| `/settings` | Settings | `views/Settings/index.vue` | 同上 |
| `/alarm` | Alarm | `views/Alarm/index.vue` | 同上 |
| `/mes` | MES | `views/MES/index.vue` | 同上 |

**路由机制要点**（同文件）：

- **Hash 模式**：`createWebHashHistory()`（行 67），打包后 `file://` 兼容。
- **License 守卫**（行 100–177）：Electron 下首次 `getLicenseStatus()`；非 Electron 跳过（行 155–159）；异常保守回 Activation（行 172–175）。
- **鉴权守卫**（行 137–151）：`useAuthStore.requiresLogin()` → 跳 `/login?redirect=...`。
- **路由记忆**：`localStorage['tianjun:lastRoute']`（行 75–78, 184–189）；Activation/Login 不可恢复。
- **插件动态路由**：不在此文件注册；由 `usePluginLoader` 在 boot 后 `router.addRoute()`（见第六节）。

---

## 二、Store 档案

共 **9** 个 Pinia store，均在 `frontend/src/store/`。

| Store ID | 文件行数 | 职责摘要 | 关键 state / getter / action |
|----------|----------|----------|------------------------------|
| `auth` | `useAuthStore.js` 282 行 | v3.10 用户系统 | `authEnabled` / `currentUser` / `token`；`ROUTE_PERM_MAP`（行 42–51）；`init`/`login`/`logout`/`canAccessRoute`/`requiresLogin` |
| `project` | `useProjectStore.js` 26 行 | 当前项目轻量缓存 | `currentProjectId/Name/currentProject`；`setCurrentProject`/`setRunningStatus` |
| `source` | `useSourceStore.js` 137 行 | 输入源 UI 配置 | 6 种源类型 settings；`saveConfig`/`loadConfig` → `localStorage['source_config']` |
| `system` | `useSystemStore.js` 388 行 | 全局显示+检测框+性能 | `display`（navbar/monitor 开关）；`detection`（toast/语音）；`channelDetections`；`loadDetectionFromProject` 含 localStorage 兜底+回写 DB（行 250–299）；`saveDetectionSettings` 多工位写全部绑定项目（行 326–356） |
| `polling` | `usePollingStore.js` 83 行 | 管理面板轮询间隔 | 对齐后端 `POLLING_DEFAULTS` / `LOG_LIMIT_DEFAULTS`；`load`/`get`/`logLimit` |
| `plugin` | `usePluginStore.js` 131 行 | 插件 CRUD 状态 | Settings 抽离；`licenseMismatch` getter |
| `plugin-theme` | `usePluginThemeStore.js` 296 行 | 主题+插件 UI 注册表 | `apply()` 拉 manifest；`pluginSlots`/`pluginMenus`/`settingsTabs`/`projectTabs`；`addPluginSlot` 用静态 `markRaw`（行 159–168） |
| `debug` | `useDebugStore.js` 98 行 | 调试中心后端半边 | 1s 轮询 `/debug/logs`；与 `utils/debug.js` 前端缓冲合并 |
| `scannerDisable` | `useScannerDisableStore.js` 67 行 | 按工位禁用扫码 | `disabledChannels`；`applyServerHint` 同步轮询推送（行 55–65） |

**Store 间协作**：

- Navbar（`layout/Navbar.vue`）同时读 `system` + `project` + `auth` + `plugin-theme`。
- Monitor 读 `project`/`system`/`source`/`scannerDisable`/`plugin-theme`；写 `system.isDetecting`（Monitor 行 1561–1563, 2635–2638）。
- 插件 slot 组件存于 `plugin-theme.pluginSlots`，由 `TjSlot.vue` 消费。

---

## 三、API 层档案

**axios 实例**：`frontend/src/api/index.js`（245 行）

| 项 | 行号 | 说明 |
|----|------|------|
| baseURL | 30–35, 54–55 | `VITE_API_BASE_URL` 或 `http://localhost:8001/api/v1` |
| 桌面检测 | 9–24 | `file://` 或 userAgent 含 electron |
| MJPEG host | 41–52 | `getBackendHost()`：dev 空串走 Vite 代理；桌面直连 8001 |
| Token 注入 | 77–85, 165–171 | `localStorage['tianjun:auth_token']` |
| 401 处理 | 222–235 | 清 token + hash 跳 `#/login` |
| 冷启动重试 | 112–207 | 仅 `ERR_NETWORK`；`backendEverReady` 总闸；轮询 URL 跳过（行 138–150） |

**分模块封装**（18 文件，`import api from './index'` 或 `@/api/index`）：

| 模块文件 | 行数 | 前缀/职责 |
|----------|------|-----------|
| `auth.js` | 157 | `/auth/*` `/users/*` `/roles/*` `/api-keys/*` |
| `detection.js` | 107 | `/source/detection/*` `/workstations/*` `/scanner/scan-pair/*`；`getDetectionResults` 支持 `known_shots` 去重（行 52–57） |
| `project.js` | 28 | `/projects/*` |
| `model.js` | 52 | `/models/*` 含 convert/resolve-path |
| `data.js` | 235 | `/data/sessions/*` 导出/备份/清理 |
| `export.js` | 209 | `/export/custom/*` `/export/realtime/*` `/export/scheduled/*` |
| `mes.js` | 36 | `/mes/orders|workpieces|defects|defect-codes` |
| `gateway.js` | 38 | `/mes/gateway/*` `/mes/inbound/*` |
| `scanner.js` | 25 | `/scanner/*` 含 disable/simulate |
| `wmax.js` | 84 | `/scanner/wmax/*` 35+ 端点 |
| `external_device.js` | 15 | `/external-devices/*` |
| `weighing.js` | 14 | `/weighing/*` |
| `cluster.js` | 23 | `/cluster/*` |
| `channel_group.js` | 10 | `/channel-groups/*` |
| `workpiece_flow.js` | 13 | `/workpiece-flows/*` |
| `packaging_flow.js` | 21 | `/packaging-flows/*` |
| `plugins.js` | 30 | `/plugins/*` |
| `index.js` | 245 | 核心实例 + 拦截器 |

**注意**：`detection.js` 行 65 仍有 `/detection/reset`（非 `/source/` 前缀）— 与 AGENTS 路由表不一致，属历史路径，调用点仅在 Monitor 间接引用需核对后端是否仍挂载。

---

## 四、视图档案

**壳层**

| 文件 | 行数 | 职责 |
|------|------|------|
| `App.vue` | 50 | 根组件；监听 Electron `onDeepGateDowngraded` / `onBackendRecovered`（行 15–37） |
| `main.js` | 188 | Pinia/Router/ElementPlus/i18n；生产静默 log（行 17–22）；rem 自适应（行 26–41）；插件 bootstrap 等后端 90s（行 110–158）；全局注册 `TjSlot`（行 168–169） |
| `layout/index.vue` | 147 | 侧栏+Navbar+router-view+BottomBar；`canShow`=插件隐藏∩权限（行 105–107）；`startScanGun()`（行 99–100） |
| `layout/Navbar.vue` | ~714 | 项目选择器、身份块、设置下拉（语言/自动保存/开发者模式/最小化） |
| `layout/BottomBar.vue` | 118 | 作业员/设备/模式/状态/运行时间 |

**业务视图（按路由）**

| 视图 | 行数 | 一句话 |
|------|------|--------|
| `Monitor/index.vue` | **5867** | 检测主屏（见第五节） |
| `Project/index.vue` | 2704 | 项目 CRUD + 7 配置 Tab + 插件注入 Tab |
| `Settings/index.vue` | 2617 | 显示/检测框/性能/插件/工位组/串行流/包装流/鉴权/调试 |
| `Data/index.vue` | 2258 | Session/Cycle 查询、导出、自定义导出/定时规则对话框 |
| `Source/index.vue` | 1417 | 6 类输入源启停与参数 |
| `MES/index.vue` | 92 | Tab 容器：工单/工件/缺陷/扫码/网关/集群/WMax/外设/入站/拉单 |
| `MES/*Panel.vue` | 268–1492 | 各 MES 子面板（最大 ScannerPanel 1492 行） |
| `Model/index.vue` | 356 | 模型上传/转换/激活 |
| `Alarm/index.vue` | 939 | 报警设备与事件绑定 |
| `Login/index.vue` | 156 | 登录表单 |
| `Activation/index.vue` | 140 | License 激活 |

**Monitor 子组件**

| 组件 | 行数 | 职责 |
|------|------|------|
| `ChannelVideoCard.vue` | 90 | 多工位视频卡片+overlay canvas 注册 |
| `SopStepPanel.vue` | 131 | SOP 步骤条+自动滚动 |
| `PerItemPanel.vue` | 414 | 逐件/混合逐件面板 |
| `WeighingPanel.vue` | 198 | 称重投料看板 |
| `PackagingFlowCard.vue` | 284 | 包装箱结算进度 |
| `CustomMixItemPanel.vue` | 124 | 混合 tracking 物品校验 |
| `ExternalAlarmBanner.vue` | 240 | 外部 MES 在途报警横幅 |
| `RecordingFailureOverlay.vue` | 95 | 录像失败列表 |
| `VirtualScanGun.vue` | 91 | 包装线虚拟扫码测试 |
| `framePump.js` | 58 | 多通道 MJPEG 解码背压 |

**i18n**：5 语言包 `locales/zh-CN.js` 等；默认 `zh-CN`（`main.js` 行 46）。

---

## 五、Monitor 深读

**文件**：`frontend/src/views/Monitor/index.vue`（5867 行：template 1–1327，script 1329–5788，style 5790–5867）

### 5.1 布局分支（template）

| 条件 | 行号 | 布局 |
|------|------|------|
| `layoutBodyOverride` 非空 | 9–124 | 插件整页覆盖；宿主仍渲染双工位 Toast/人工确认/录像异常 |
| `channelCount === 2` | 127–309 | 双列：视频+MES 条+计数+SOP+控制 |
| `channelCount > 2` | 312–475 | 2×2 视频格 + 选中工位详情底栏 |
| 否则（单工位） | 478–1304 | 12 栅格：左 7 视频+模式面板，右 5 统计+图表+步骤表+控制 |
| `layoutFooterOverride` | 1308–1313 | 插件底栏 slot |
| `ExternalAlarmBanner` | 1326 | 全局在途报警 |

### 5.2 模式专属面板（单工位左列）

| logic_mode | 组件/区块 | 行号 |
|------------|-----------|------|
| sequential/detection/custom | `SopStepPanel` | 606–611 |
| tracking | 内联清点/容器卡片 | 614–676 |
| weighing | `WeighingPanel` | 679–682 |
| per_item | `PerItemPanel` | 685–689 |
| custom+per_item 混合 | 第二个 `PerItemPanel` mix | 700–705 |
| custom+tracking 混合 | `CustomMixItemPanel` | 707–711 |
| 包装 | `PackagingFlowCard` + `VirtualScanGun` | 714–724 |
| 周期性强制动作 | 内联进度条 | 727–788 |

### 5.3 核心状态与数据流（script）

**Store 引用**：行 1332–1362

**多工位**（行 1763–2770）：

- `channelCount` ← `GET /workstations/`（`fetchChannelCount` 行 2712–2746）
- `multiChannelData[ch]`：每通道运行时快照（行 1853–1870 初始结构）
- 轮询：`startMultiPolling` 150ms（行 2327–2367）→ `processChannelResult`（行 2030–2325）
- 视频：单工位 `<img>` 双缓冲（行 1668–2909）；多工位 `fetch` 解析 multipart MJPEG → canvas（行 1881–2028）
- `STREAM_HOST` 硬编码 `http://localhost:8001`（行 1877）— **桌面若改端口可能不一致**（单工位 `buildStreamUrl` 用 `getBackendHost()` 行 2772）

**单工位轮询**（行 4438–4722）：

- 间隔 150ms；`STREAM_SWAP_INTERVAL=600` 次后 swap 释内存（行 4431, 4450–4454）
- 截图去重：`performance.screenshotDedup` → `known_shots` 参数（行 4456–4467）
- 周期边界：`current_cycle_id` + `resultHoldActive` 展示期（行 4544–4578, 1718–1759）
- 流卡死：`fps>0 && !isStreaming` 超 4s 强制重连（行 4478–4493）

**检测控制**：

- 单工位：`startDetection`（行 4179–4354）含副模型强制完整启动（行 4198–4245）
- 多工位：`startDetectionForChannel`（行 2551–2633）
- 停止前 `confirmScanPairBeforeStop`（行 2646–2680）
- 人工确认：`pendingAckDisplay` + `ackPendingForChannel` + 提权窗（行 5545–5666）

**步骤/PT 算法**（`updateStepsFromBackend` 行 4855–5209）：

- 期望序列按位置分配 `completedByPos`（行 4949–4965）
- OK/NG：`cycleSumStepDurations` 权威 PT 守门（行 4984–5033）
- 跟踪模式补丁：行 5115–5141

**生命周期**（行 5669–5787）：

- `onMounted`：先 `await fetchChannelCount()`（行 5675）再 `getSourceStatus`；避免单/多工位流竞态（注释 D2/D3）
- `onUnmounted`：停轮询/流/图表/定时器（行 5742–5773）

### 5.4 插件 slot 契约（Monitor 内）

| slot name | 行号 | 用途 |
|-----------|------|------|
| `monitor.layout.body` | 9–22, 1365 | 整页 layout 覆盖 |
| `monitor.layout.footer` | 1308–1313, 1366 | 底栏覆盖 |
| `monitor.step-cell.duration` | 1040–1049 | 步骤 PT 列 |
| `monitor.step-cell.status` | 1053–1078 | 步骤结果列 |
| `cycle-result.indicator` | 31–46, 282–297 | OK/NG Toast |
| `monitor.workpiece-flow.indicator` | 1318–1322 | RFC11 串行流指示 |

`layoutBodyActions`（行 1377–1483）向插件暴露开始/停止/画框/MES/步骤表渲染等完整能力。

### 5.5 关键常量

| 常量 | 值 | 行号 |
|------|-----|------|
| 轮询间隔 | 150ms | 2366, 4721 |
| 包装 state 轮询 | 1500ms | 1622 |
| STREAM_SWAP_INTERVAL | 600 tick ≈90s | 4431 |
| WORKPIECE_RESULT_HOLD_MS | 3500ms | 2969 |
| STREAM_FIRST_FRAME_TIMEOUT_MS | 5000ms | 1697 |

---

## 六、插件前端链路

```
main.js (110–158)
  └─ waitBackendReady() → GET /plugins/active/manifest (最多 90s)
  └─ usePluginThemeStore.apply()     → DOM 主题/css/hidden_menus/ui_hidden
  └─ loadActivePluginFrontend(router)
       └─ fetch ESM entry (cache: no-store)
       └─ 三级 import: blob → data:URL → http URL  (usePluginLoader.js 124–163)
       └─ register({ host, registry })
            ├─ host: vue/pinia/router/i18n/echarts/api/customerCode
            ├─ registry.routes.add → router.addRoute + themeStore.addPluginRoute
            ├─ registry.menus.add → themeStore.pluginMenus → layout 侧栏
            ├─ registry.slots.register → themeStore.pluginSlots → TjSlot
            ├─ registry.tabs.register → settingsTabs/projectTabs
            └─ registry.stores.register → 调用 useStoreFn()
  └─ app.mount('#app')
```

**关键文件**：

| 文件 | 行数 | 职责 |
|------|------|------|
| `composables/usePluginLoader.js` | 310 | G2 Tier2/3 加载器；双通道日志 `POST /plugins/client-log`（行 58–74） |
| `store/usePluginThemeStore.js` | 296 | 主题副作用 + slot/menu/tab 注册表 |
| `components/TjSlot.vue` | 55 | ui_hidden / 插件覆盖 / 默认 slot 三态 |
| `layout/index.vue` | 44–54 | 渲染 `sortedPluginMenus` |

**限制**（loader 注释行 29–31）：无 SES 沙箱；切换插件需重启；单 active 插件。

---

## 七、Electron 壳档案

### 7.1 进程与窗口

**`electron/main.js`**（1094 行）

| 模块 | 行号 | 说明 |
|------|------|------|
| 文件日志 | 25–122 | `userData/logs/electron.log` + `backend.log`，10MB 滚动 |
| GPU/内存限制 | 124–134 | `max-old-space-size=512` 等 |
| 单实例锁 | 137–140 | 第二实例激活已有窗口（行 756+） |
| CONFIG | 247–254 | backend 8001；frontend dev 6001 |
| BackendManager | 276–347 | 见 7.2 |
| readWorkstationConfig | 368+ | splash/window/fullscreen/deepReadyGate |
| createWindow | 391+ | 主窗 webPreferences；`webSecurity: false`（插件 http import 需要） |
| Splash | 504–554 | 新版 cyber splash + legacy 兜底 |
| 8 步关机 | 612–739 | `startGracefulShutdown` → `executeShutdown` 8 POST steps → `finishShutdown` |
| 崩溃恢复 | 212–244, 764+ | render-gone/unresponsive/GPU 重载 |
| IPC | 900–1092 | 见 preload 对照 |

**`electron/preload.js`**（27 行）→ `window.electronAPI`：

| API | IPC | 用途 |
|-----|-----|------|
| `getLicenseStatus` | `get-license-status` | 路由守卫 |
| `getMachineIdReport` | `get-machine-id-report` | 设置页诊断 |
| `importLicense` | `import-license` | 激活页 |
| `onDeepGateDowngraded` | `startup:deep-gate-downgraded` | App.vue 警告 |
| `onBackendRecovered` | `backend:recovered` | 看门狗恢复提示 |
| `gracefulQuit` | `app:graceful-quit` | Navbar 退出 |
| `minimizeWindow` / `setFullScreen` | `window:*` | 窗口控制 |
| `openLogsDir` | `app:open-logs-dir` | 调试 |

### 7.2 后端进程管理

**`electron/backend-manager.js`**（776 行）

| 项 | 说明 |
|----|------|
| 启动命令 | `python -m uvicorn backend.main:app`（spawn 细节见文件中部） |
| dev Python | `CONDA_PREFIX` 或 `~/anaconda3/envs/tianjun`（行 79–86） |
| 生产 Python | `resources/python/python.exe` |
| 环境变量 | `TIANJUN_DATA_DIR`、`TIANJUN_APP_VERSION` 等（行 154–174） |
| 就绪探针 | 浅：`/` 或 health；深：`/system/startup-ready`（`deepReadyGate`） |
| 深探降级 | 浅就绪 30s 后 DB 仍失败 → 放行主窗 + `deep-gate-downgraded` 事件（行 66–71） |
| 看门狗 v3.29 | `_everReady` 后崩溃自动拉起；限流 3 次/60s（行 52–54, 309–333） |

### 7.3 License

**`electron/license-manager.js`**（355 行）

- RSA 公钥验签（行 7–15）
- 机器指纹：Win PowerShell 一把取 board/uuid/disk/mac（行 61–87）；Linux `/sys/class/dmi`（行 101–125）
- 占位符过滤 `BAD_VALUES`（行 27–50）

### 7.4 启动时序（简图）

```
app.ready → setupFileLogger → readWorkstationConfig
  → createSplashWindow → startBackend() [BackendManager]
  → 就绪探针通过 → createWindow(load frontend dist or dev URL)
  → splash:finished / timeout → maybeShowMainWindow
  → 前端 main.js: waitBackendReady → plugin bootstrap → mount
```

---

## 八、分层依赖

```
┌─────────────────────────────────────────────────────────────┐
│ Electron 主进程 (main.js)                                    │
│  spawn uvicorn · License · 8步关机 · 日志 · 看门狗          │
└──────────────────────────┬──────────────────────────────────┘
                           │ preload → electronAPI
┌──────────────────────────▼──────────────────────────────────┐
│ Vue 3 应用 (main.js → App.vue → router-view)                 │
│  ┌──────────── layout (Navbar/BottomBar/侧栏) ─────────────┐  │
│  │  Views (Monitor/Project/...)                           │  │
│  │    ↕ Pinia Stores (9)                                  │  │
│  │    ↕ api/*.js → axios (index.js 拦截器)                │  │
│  └────────────────────────────────────────────────────────┘  │
│  插件: usePluginLoader → themeStore.slots/menus/routes      │
│  全局: TjSlot · useScanGun · i18n · ElementPlus              │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP /api/v1/*  +  /video_feed
┌──────────────────────────▼──────────────────────────────────┐
│ FastAPI 后端 (backend.main:app @ 8001)                       │
└─────────────────────────────────────────────────────────────┘
```

**跨层契约**：

| 层 | 依赖 |
|----|------|
| 前端 → 后端 | 统一 `/api/v1/`；MJPEG 顶层 `/video_feed?channel=N`（非 api 前缀） |
| 前端 → Electron | 可选 `window.electronAPI`；纯浏览器 dev 时 License/窗口 API 为空 |
| 插件 → 主程序 | manifest.frontend + ESM `register()`；slot 名契约不可随意删字段 |
| Electron → 后端 | 关机 8 步 POST；健康探针；stdout 落盘 |

**localStorage 键（前端持久化）**：

`tianjun:auth_token` · `tianjun:lastRoute` · `display_settings` · `detection_settings` · `source_config` · `performance_settings` · `developer_mode` · `last_session_name` · `auto_save_settings`

---

## 九、疑点清单

| ID | 严重度 | 位置 | 描述 |
|----|--------|------|------|
| F-01 | 中 | Monitor 1877 vs 2772 | 多工位 `STREAM_HOST` 硬编码 `localhost:8001`；单工位用 `getBackendHost()`。非默认端口或远程后端时多工位视频可能连错 |
| F-02 | 低 | `detection.js` 65 | `resetDetection` 仍调 `/detection/reset` 非 `/source/detection/reset`，需核对后端是否别名挂载 |
| F-03 | 低 | Monitor 5867 行单文件 | 上帝组件：检测/UI/MES/插件/图表/轮询全耦合；后续拆分风险高 |
| F-04 | 中 | 插件 loader | 无沙箱；插件 ESM 与主程序同权限，恶意插件可调用 host.api |
| F-05 | 低 | `plugin-theme` apply | 插件切换需重启才完整生效（设计如此，Settings 有提示） |
| F-06 | 低 | Navbar 147 | 下拉「退出」与 v3.10「登出」并存，语义易混（一个 gracefulQuit 一个 auth logout） |
| F-07 | 中 | 冷启动 | 前端 90s 等 manifest + axios 180s 重试 + Electron 深探 5min：多层等待策略重叠，排障时需分清哪层超时 |
| F-08 | 低 | BottomBar 109–112 | 运行时间从 mount 起计时，非后端 session 真实时长 |
| F-09 | 低 | `useScanGun` | USB 枪依赖键盘速度启发式；人手极快或枪配置错误可能误判 |
| F-10 | 信息 | splash/vendor | three.js/mediapipe 等大体积 vendor 仅 splash 动画用，与主窗业务无直接耦合 |

---

## 附录：阅读覆盖清单

| 目录 | 文件数 | 说明 |
|------|--------|------|
| `frontend/src/` | 97 | 全部通读；Monitor 分 6 段（1–700, 701–1400, 1401–2100, 2101–2800, 2801–3500, 3501–4200, 4201–4900, 4901–5600, 5601–5867） |
| `electron/` 核心 | 4 | main.js, backend-manager.js, license-manager.js, preload.js |
| `electron/splash/` | — | 仅记为启动动画资源，未逐行读 vendor |

**文档路径**：`docs/dev/_reading_notes/04_frontend_electron.md`
