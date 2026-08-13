# 04 · 前端 + Electron 读码笔记

> 覆盖范围：`frontend/src/` 全部约 100 文件（含 `Monitor/index.vue` 6245 行分段通读）、`electron/` 核心壳文件（`main.js` / `backend-manager.js` / `license-manager.js` / `preload.js`；vendor/splash 第三方资源仅记用途）。
> 行号锚点均来自当前仓库快照，后续改动以代码为准。

> **v3.47 补账（2026-08-07，多分支汇合发版）**：
> - `views/Monitor/index.vue`（→6847 行）：**多工位布局重构**——模板顶层链 `layoutBodyOverride → channelCount===2 → ===3（三行横排：左视频 16:9 + 右数据面板 ChannelVideoCard）→ >3（网格总览「多工位总览」工具条 auto/2x2/3x3/4x4 + 分页 + 点卡片放大详情）→ 单工位`；新状态区（:2265 起）`gridPage/gridDims/zoomedChannel/gridPageChannels`；MJPEG 按可见工位收放：`visibleStreamChannels()`（≤3 全拉 / 放大只拉一路 / 网格只拉当前页）+ `syncMultiStreams()`（翻页/换布局/进出放大 watcher 触发，:2315），数据轮询仍覆盖全部工位；流被服务端正常收流（done 非异常）也自动重连（带可见性守门，:2523）；`startMultiStreams` 入口守卫 layout.body 插件独占（互踢修复）；列级 OK/NG Toast 从仅双工位放开到任意工位数（`channelCount >= 1` 网格，:30）；`fetchChannelCount` 工位数变化后放大越界退回总览
> - `views/Source/index.vue`：自定义工位数输入放开（同步后端 MAX_CHANNELS 64）
> - `views/Project/LogicConfigTab.vue` + `index.vue`：容器装箱清点卡新增记账五开关（滑块去重默认开/托盘去重/空账清理/指针让位/结账同源默认关）
> - 新增 `views/Interconnect/index.vue` + `api/interconnect.js`：互连设置页四块（连接配置/帧采样回传/模型拉取/运行状态与采样流水表）；`layout/index.vue` + `router/index.js` 加「互连」导航
> - `views/Activation/index.vue`：导入授权验签通过即进「系统启动中」等待态；监听 `license-backend-start-failed` 弹明确错误（不再无限转圈）
> - `views/Alarm/index.vue`：NG 汇总数字口径单选（panel/window）+ `smsSummaryNoticeBody` 动态文案
> - Electron：`main.js` 启动 300s 死线改弹性（后端日志滚动就延展、静默 90s 判死、绝对上限 20min；启动期进程退出立即止损报错）；`backend-manager.js` 日志滚动判活；`license-manager.js` machineId 缓存命中快路径 + 指纹校验后台异步（不匹配删缓存下次强制重算）；`preload.js` 透出 `license-backend-start-failed`；`build/installer.iss` 装完把安装目录+数据目录加 Defender 排除（卸载移除）

> **v3.48 补账（2026-08-10，SY9 + tianyong + dev-qing 三分支汇合发版）**：
> - 新增 `views/MES/PlcPanel.vue`（688 行）+ `api/plc.js`：RFC 13 PLC 连接器面板——连接卡片（8 驱动选择 + conn_params 按驱动动态表单）/点位表（地址/类型/字节序/scale + 实时值）/触发规则/事件写回/方案模板（s7_db_handshake V0.2 一键套用）/IO 日志；挂在 MES 页新 tab（`views/MES/index.vue`）
> - 新增 `views/Settings/TriggerPanel.vue`（770 行）+ `api/triggers.js`：RFC 14 触发中心面板——触发源卡片（虚拟按钮 pixel_region/脚踏板 hid_key/HTTP/串口报文/定时/mock 六类 × 类型化参数表单）+ 动作链配置（manual_settle/trigger_event/ack_alarm 等全局动作注册表）+ 模板下拉 + 实时状态/触发历史；挂在系统设置新 tab（`views/Settings/index.vue`）
> - `store/usePollingStore.js`：注册 `plc_status(3s)/plc_live(1.5s)/trigger_status(3s)/trigger_live(1.5s)` 轮询档
> - `views/Project/LogicConfigTab.vue` + `index.vue`：①「计数组合判定表」卡（tianyong）——labels×rows 组合表 + 计数口径单选（按步骤账本 steps/按位置去重 positional）+ positional 六追踪参数网格；②容器装箱清点卡（SY9）——空槽标签+每盘槽位数（槽位完整性门，默认关、带 2026-08-05 实测警示文案）、物品框/托盘框去重 IoU 阈值（缺省 0.45/0，双边客户零差异）
> - `views/Monitor/CustomMixItemPanel.vue`（SY9）：预计进箱数字（结账同源口径）
> - `views/Settings/PackagingFlowPanel.vue`（SY9）：组⑦新增「只认收尾后放的工单」开关（默认关，长警示文案说明代价）
> - `views/MES/ExternalDevicePanel.vue` + `api/external_device.js`（dev-qing）：`modbus_pulse` 协议配置区（线圈地址/pulse_ms/cooldown_ms/trigger_mode）+ 手动试发按钮
> - `views/Alarm/index.vue`（dev-qing）：短信汇总「发送形态」单选（merged_detail 一条内分列多工位=默认 / per_channel 逐工位逐条）+ `summary_channel_ids` 参与工位多选

> **v3.50 补账（2026-08-12，捷昌二期：齐件即结算 + 扫码器生命周期）**：
> - `views/Project/LogicConfigTab.vue`：跟踪配置卡新增「全部合格立即结算」开关（`data-testid=settle-on-complete-switch`），**仅 `tracking_cycle_strategy` 为 roi_exit/container 时渲染**（v-if 条件），tooltip 说明语义 + 与 scan_pair 互斥警示；开启时下方警示文案提醒确认帧数配置。
> - `views/Project/StepsConfigTab.vue`：物品设置表（tracking 模式）新增「确认放入帧数」列（`settle_confirm_frames`，el-input-number min=1，`data-testid=settle-confirm-frames-input`），**仅 `settleOnCompleteActive` computed 为真时渲染**（开关开 + 策略支持），count_mode≠track 行置灰。
> - `views/Project/index.vue`：`initProjectDefaults` 回读 `pipeline_config.tracking_settle_on_complete` + 步骤 `settle_confirm_frames` 默认 1；保存映射仅 roi_exit/container 策略写 true（其他策略强制 false，防脏配置）。
> - `views/MES/ScannerPanel.vue`：新「扫码器生命周期」选项区——「重新亮灯时机」下拉（cycle_end/ok_only）+「亮灯作废旧码」+「强制去重」开关；前两项 `lifecycleApplicable` computed 守门（device_type=text_lon 且 scan_mode ∈ once_per_cycle/D，USB 键盘枪无灯控置灰 + 提示文案）；强制去重任何设备可用；defaultForm 三字段默认=现状；scan_pair 提示文案补与齐件即结算互斥说明。
> - `views/Monitor/index.vue`：①MES 信息条新增「恢复扫码」按钮（`data-testid=resume-scanner-btn`，`mesData.scanner_resume_blocked` 为真时显示，点击 `POST /scanner/resume?channel_id=` + 成功/无枪/失败三态 toast）；**信息条外层 v-if 补 `scanner_resume_blocked` 条件**——否则 NG 后无工件/工单时整条不渲染按钮出不来（开发中实测踩的坑）；②`handleScanToast` 统一警告分支：`scan_warning || scan_pair_dup_warning` 走 `warn_reason` 文案的 warning toast（多工位带工位号前缀），时间戳去重沿用。
>
> **v3.51 补账（2026-08-14，捷昌 B 站双工位整改）**：
> - `views/Settings/index.vue`：显示设置页「开机自动恢复检测」卡片后新增**「启用项目时自动接管未绑定工位」卡片**（`data-testid="adopt-unbound-switch"`，`GET/PUT /projects/activate-config`，默认开=存量收养行为；说明文案标注"多工位多项目部署建议关闭"）；`loadAdoptUnboundConfig`/`onAdoptUnboundChange` + onMounted 装载；新 import `Connection` 图标。
> - `views/Settings/ChannelGroupPanel.vue`：创建/编辑对话框在 `settle_strategy === 'synchronized_all_ok'` 时渲染**「统一播报」开关**（`form.unified_ok_report`，说明文案"全部合格后统一报一次合格，NG 仍立即播报"）；列表「联动策略」列 `row.unified_ok_report` 为真时追加橙色 `统一播报` tag；`_newForm` 默认 false。
> - 回归：e2e `tests/e2e_browser/test_v3_51_switches.py`（4 用例：activate-config API 默认值 / adopt 开关点击-落库-刷新回填 / unified_ok_report API 往返 / 面板 tag 可见）。
> - `views/Project/LogicConfigTab.vue`（v3.50.0a 收编）：跟踪配置卡「扫码后才计数」开关（`tracking_scan_gate`）+ 齐件即结算 tooltip 新语义文案（含"配周期超时兜底"提醒）。
>
> **v3.48.1 补账（2026-08-11，体验修复补丁版）**：
> - `views/Monitor/index.vue`（→6942 行）：**快照轮询回退**——`syncMultiStreams` 在可见工位 > `MAX_MJPEG_STREAMS`(4) 时掐掉全部 MJPEG 长连接改 `/snapshot?channel=N` 单帧轮询（浏览器同 host HTTP/1.1 仅 6 条并发连接，九路 MJPEG + 数据轮询互踢饿死）；新增 `mjpegZeroFrameFails`/`_registerMjpegDeath`：任一工位 MJPEG 连续 2 次零帧断流（WebKit fetch 不支持 multipart/x-mixed-replace）单独降级快照；`startSnapshotPolling` 定时器 40ms 基础节拍 + `_snapshotIntervalMs()` 按并发工位数自适应取帧间隔（≤2 路 80ms / ≤4 路 120ms / ≤9 路 200ms / 更多 300ms），`snapshotInFlight` 背压跳 tick，`stopMultiStreams` 清零帧计数
> - `views/Data/index.vue`：周期列表「全部/仅OK/仅NG」筛选（`cycleResultFilter` → cycles 端点 `result` 参数，换筛选重置分页/展开态）；播放弹窗 0.5x~4x 倍速（`videoPlaybackRate` + `applyPlaybackRate`，`@loadedmetadata` 时套用、换视频不重置）+「下载录像」按钮（a[download] 指向同 `/data/videos/{id}`）；视频报错文案引导下载兜底
> - `api/data.js`：`getSessionCycles` 加第 4 参 `result`
> - Electron：`main.js` `finishShutdown` 的 stopBackend race 3s→8s + `app.exit(0)` 前必调 `forceKillSync`；`backend-manager.js`（→806 行）新增 `forceKillSync()` 同步强杀兜底（win32 `taskkill /f /t` / POSIX 杀进程组）——治"退出留僵尸 python 占 GPU 显存、每次启动清残留"

> **v3.49 补账（2026-08-12，捷昌整改批次）**：
> - `views/Settings/index.vue`：①显示设置页新增两只开关——「MES 外推并发派发」（`data-testid="mes-async-dispatch-switch"` → `GET/PUT /mes/gateway/async-dispatch`）与「扫码新码先上屏」（`data-testid="scan-pair-new-first-switch"` → `GET/PUT /scanner/scan-pair/new-code-first`），均默认开、即时生效；②性能设置页新增**数据库卡片**（`data-testid="db-info-card"` / dialect 标签 `db-dialect-tag`）——展示 dialect/位置/服务端版本/连接池，数据源 `GET /system/db-info`，带刷新按钮
> - `views/MES/ClusterPanel.vue`：①集群配置面加「上报超时 report_timeout_sec」「异步上报 report_async」两项（写 `/cluster/config`）；②新增「上报链路状态」区（`data-testid="cluster-report-status"`）——内存队列/落盘积压/已补发三数字，轮询 `GET /cluster/report-status`
> - `views/MES/GatewayPanel.vue`：连接弹窗新增「重试预算(秒)」输入框（`data-testid="gw-retry-budget"`）——值存进连接 **config JSON** 的 `retry_budget_sec`（非顶层字段），0=不限
> - `views/Monitor/index.vue`：当前条码 override 适配 scan_pair 新码先上屏时序（新码顶替即上屏，不等旧窗口结算返回）
> - `api/cluster.js`：新增 `getReportStatus()` 封装
> - Electron：`backend-manager.js` 后端 spawn 环境注入 **`DATABASE_URL`**（进程环境变量优先，其次数据目录 `db_config.json`；都没有=SQLite 零差异）；`build/installer.iss` 新增 **PostgreSQL 可选组件**（默认不勾选，选装才铺 PG 运行时）

> v3.41 复核（2026-07-17）：基线 a23a8d2（v3.32/v3.33 之交）→ v3.41.0 的前端/Electron 增量已回写本文——各条目下的「v3.3x 起」引用块即补账内容，行号锚点已整体刷新为当前快照。逐版动机详见 `docs/changelog/`（v3.33.0 ~ v3.41.0 各版 md）。

> **v3.44 补账（2026-07-22）**：NG 处置整改批次四文件——
> - `views/Project/LogicConfigTab.vue`：6 张 NG 相关卡收敛为两张——「NG 判定与处置」（一行一场景：违规当场/缺步骤/少数量/数量门，绑 `pipeline_config.ng_handling`，档位不选高级参数不渲染，跨 tab 联动提示 NG 事件是否勾了需人工确认）+「结算冷却」（防重复结算+NG 周期保护合并）；旧「NG 补做策略/实时NG/收尾防呆」卡与结算方式卡内违序行删除。跟踪配置新增动作确认三参数（出现/消失帧数/不应期秒）。
> - `views/Project/index.vue`：加载时无 `ng_handling` 从 legacy 键合成（JS 版与后端 resolve_ng_handling 同构）；保存只落新块不回写老键；last_first 强制 violation=none。
> - `views/Project/EventsConfigTab.vue`：NG 事件（id=2）「需人工确认」下反向联动明示——处置按钮来源于逻辑设置统一卡、「确认后保留周期」被处置按钮顶掉的关系。
> - `views/Monitor/index.vue`：确认弹窗（大/小两处）新增包装挂账形态——在制箱明细（第几箱/已进箱/目标/挂起态）+「认NG落账进下一箱/重做本箱不记NG」二选一；成功提示按箱语义。`pendingAckChannelStates` 接后端 `pending_ack.pkg_hold`。
> - `views/Monitor/PackagingFlowCard.vue`：工单收尾快照横幅（完成/作废+最终结果+箱明细，扫新单顶掉）+ NG 箱账挂起横幅（只引导去确认弹窗，不重复给按钮）；`pending_box` 按 reason 分流（ng_ack vs 少装）。
>
> **v3.45 补账（2026-07-29）**：上银热补丁 0a~0e 收编 + 箱标签扫码授权 + 包装工单同步 + 萍乡称重整改 + 海康帧率可配 + dev-qing 逐件合入（9973d4c），共 13 个前端文件——细节见各条目「v3.45」行：
> - 称重族：`api/weighing.js`（+`getWeighingOperators`）、`Monitor/WeighingPanel.vue`（人员下拉 operator_from_users）、`Project/WeighingConfigTab.vue`（作业员名单开关）。
> - 包装族：`Settings/PackagingFlowPanel.vue`（组⑧箱标签扫码 + 工单同步开关 + 上银预设扩容）、`Monitor/PackagingFlowCard.vue`（等扫箱标签横幅/完成态文案/标签取量目标）、`MES/OrderPanel.vue`（来源标签"包装扫码"）。
> - 容器/NG 处置族：`Project/LogicConfigTab.vue`（峰值封顶/稳定帧/每盘校验入口 + gate 升级放行 + short_count=hold 档）、`Project/StepsConfigTab.vue`（容器角色提示标签）、`Project/index.vue`（新键默认值与保存映射）。
> - 其他：`Monitor/index.vue`（per_item associated 过滤+双色守门；待机快速恢复不重推配置+按源类型文案；画布 ResizeObserver）、`Source/index.vue`（海康目标帧率下拉+持久化）、`MES/GatewayPanel.vue`（端口框修复）。

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
| `system` | `useSystemStore.js` 398 行 | 全局显示+检测框+性能 | `display`（navbar/monitor 开关 + 自定义 Logo）；`detection`（toast/语音）；`channelDetections`；`loadSettings` 深度合并恢复（行 218）；`loadDetectionFromProject` 含 localStorage 兜底+回写 DB；`saveDetectionSettings` 多工位写全部绑定项目 |
| `polling` | `usePollingStore.js` 83 行 | 管理面板轮询间隔 | 对齐后端 `POLLING_DEFAULTS` / `LOG_LIMIT_DEFAULTS`；`load`/`get`/`logLimit` |
| `plugin` | `usePluginStore.js` 131 行 | 插件 CRUD 状态 | Settings 抽离；`licenseMismatch` getter |
| `plugin-theme` | `usePluginThemeStore.js` 296 行 | 主题+插件 UI 注册表 | `apply()` 拉 manifest；`pluginSlots`/`pluginMenus`/`settingsTabs`/`projectTabs`；`addPluginSlot` 用静态 `markRaw`（行 159–168） |
| `debug` | `useDebugStore.js` 98 行 | 调试中心后端半边 | 1s 轮询 `/debug/logs`；与 `utils/debug.js` 前端缓冲合并 |
| `scannerDisable` | `useScannerDisableStore.js` 67 行 | 按工位禁用扫码 | `disabledChannels`；`applyServerHint` 同步轮询推送（行 55–65） |

> v3.35.1~v3.39 起（`system` store 新增键，v3.41 复核）：
> `display.logoDataUrl`（行 90，v3.36 导航栏自定义 Logo 的 256×256 PNG data URL，空=内置图标）；
> `display.navbar.shift`（行 103，v3.35.1 顶栏当前班次显示，默认关）；
> `display.monitor.showBypassSn`（行 122，v3.38 扫码器旁路 SN 显示，默认关）；
> `display.monitor.showScanButtons`（行 124，v3.39 川南反馈——操作员不碰软件的部署可隐藏"清除本次扫码/禁用扫码"按钮，默认显示）；
> `performance.mediapipePosePointColor` / `mediapipeHandsPointColor`（行 195/198，骨架关键点颜色与连线分开配，空=跟随连线）。
> ⚠️ v3.38 修复：Navbar 恢复 `display_settings` 必须走 `loadSettings()` 深度合并（行 218），整表覆盖会在旧 localStorage 缺 `monitor` 子树时让监控页渲染崩溃（只剩背景）。

**Store 间协作**：

- Navbar（`layout/Navbar.vue`）同时读 `system` + `project` + `auth` + `plugin-theme`。
- Monitor 读 `project`/`system`/`source`/`scannerDisable`/`plugin-theme`；写 `system.isDetecting`（Monitor 行 1599, 2879）。
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
| `detection.js` | 111 | `/source/detection/*` `/workstations/*` `/scanner/scan-pair/*`；`getDetectionResults` 支持 `known_shots` 去重（行 52–57）；v3.42 增 `inferOnce`（行 61，标定用单帧推理，给「抓取锚点框」在停止/待机态兜底） |
| `project.js` | 28 | `/projects/*` |
| `model.js` | 52 | `/models/*` 含 convert/resolve-path |
| `data.js` | 235 | `/data/sessions/*` 导出/备份/清理 |
| `export.js` | 216 | `/export/custom/*` `/export/realtime/*` `/export/scheduled/*`；v3.38 加 `getScannerBypassStatus`（行 136–140，扫码器旁路 SN 只读查询） |
| `mes.js` | 36 | `/mes/orders|workpieces|defects|defect-codes` |
| `gateway.js` | 40 | `/mes/gateway/*` `/mes/inbound/*`；v3.39 加 `clearActiveAlarmsManual`（行 40，软件内手动消除在途报警，需入站配置开 `allow_manual_clear`） |
| `scanner.js` | 25 | `/scanner/*` 含 disable/simulate |
| `wmax.js` | 84 | `/scanner/wmax/*` 35+ 端点 |
| `external_device.js` | 15 | `/external-devices/*` |
| `weighing.js` | 15 | `/weighing/*`；v3.45 加 `getWeighingOperators`（行 15，`GET /weighing/operators` 作业员候选名单，配 operator_from_users 下拉） |
| `sms.js` | 8 | ★ `/sms/config|ports|test`（Alarm 页 NG 短信推送） |
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
| `layout/Navbar.vue` | 797 | 项目选择器、身份块、设置下拉（语言/自动保存/开发者模式/最小化）、自定义 Logo、当前班次 |
| `layout/BottomBar.vue` | 118 | 作业员/设备/模式/状态/运行时间 |

> Navbar v3.35.1~v3.38 变更（v3.41 复核）：
> - **v3.37 激活项目自动跟随**：外部 MES 开工切项目后界面不刷新也能跟上——每 5s 轻量轮询后端当前激活项目（`syncActiveProjectFromBackend` 行 440–462，定时器行 748），不一致时把项目装进前端各 store 并提示"项目已由外部系统切换"；**只对齐显示、不回调激活接口**（后端已激活，再调会无谓重载模型）。手动切换与跟随共用同一段装载逻辑 `applyProjectToStores`（行 372–435）。
> - **v3.36 自定义 Logo + v3.37 回归修复**：右上角图标优先取 `display.logoDataUrl`（行 161）；⚠️ 回退地址必须 `import.meta.env.BASE_URL + 'app-icon.png'`（行 182）——写死 `'/app-icon.png'` 运行时字符串 Vite 改写不到，打包版 `file://` + 相对 base 下解析到盘根导致图标空白（v3.36.0 打包版回归，dev 不复现）。
> - **v3.35.1 当前班次（发版归入 v3.38.0 自定义班次列表）**：顶栏作业员旁显示当前班次（`currentShiftName` 行 617–639，显示开关 + 项目启用班次拆分双守门）；与后端班次解析同口径——"最近一个已开始的班次"，未配自定义列表回退白/晚两班，每秒 tick 跨班次边界刷新。代码注释里的 v3.35.1 是开发期版本号，changelog 见 v3.38.0 FEAT-005。
> - **v3.38 显示设置恢复改深度合并**：见第二节 `system` store 警示条。

**业务视图（按路由）**

| 视图 | 行数 | 一句话 |
|------|------|--------|
| `Monitor/index.vue` | **6347** | 检测主屏（见第五节；v3.45 复核） |
| `Project/index.vue` | 3286 | 项目 CRUD + 7 配置 Tab + 插件注入 Tab（v3.45 复核） |
| `Settings/index.vue` | 2723 | 显示/检测框/性能/插件/工位组/串行流/包装流/鉴权/调试（v3.41 复核） |
| `Data/index.vue` | 2294 | Session/Cycle 查询、导出、自定义导出/定时规则对话框（v3.41 复核） |
| `Source/index.vue` | 1436 | 6 类输入源启停与参数；v3.45 海康 SDK 目标帧率下拉（见表下注） |
| `MES/index.vue` | 92 | Tab 容器：工单/工件/缺陷/扫码/网关/集群/WMax/外设/入站/拉单 |
| `MES/*Panel.vue` | 268–1512 | 各 MES 子面板（最大 GatewayPanel 1512 行；v3.45 复核） |
| `Model/index.vue` | 356 | 模型上传/转换/激活 |
| `Alarm/index.vue` | ~1482 | 报警设备与事件绑定；**短信** 增加「NG 短信推送」卡（`sms.js`：总开关 / AT·HTTP 二选一 / 收件人 / 测试发送；与灯塔串口隔离） |
| `Login/index.vue` | 156 | 登录表单 |
| `Activation/index.vue` | 140 | License 激活 |

> 业务视图 v3.33~v3.45 变更（动机详见对应 changelog）：
> - **`Data/index.vue`（v3.38 自定义班次筛选）**：时间段下拉在项目配了自定义班次列表（≥2 条有效）时按列表动态出选项（`customShifts` 行 962–973，本班结束时刻=下一班开始、末班跨天回到首班）；切项目后已选班次名失效自动回落"全天"（watch 行 976–982）；`getShiftHours`（行 984–996）班次名 → 起止时刻。未配列表保持旧白/晚两班零差异。
> - **`Settings/index.vue`**：v3.36 「导航栏 Logo」上传块（模板行 48–60；`onNavbarLogoChange` 行 2040–2071 前端居中裁方压 256×256 PNG data URL 存 display，`resetNavbarLogo` 行 2072；回退地址同走 BASE_URL 拼接，行 2038）；v3.35.1 「当前班次」显示开关（行 97）；v3.38 「旁路 SN 码」开关（行 171）；v3.39 「扫码操作按钮」显隐开关（行 178）；v3.32+ MediaPipe 姿态/手部**关键点颜色**独立取色器（行 1109–1153，清空=跟随连线颜色）。
> - **`MES/GatewayPanel.vue`**：v3.35 第 6 种适配器「数据库直写」——适配器单选加 `database`（行 116），连接表单（库型达梦/MySQL/PG/SQLServer/SQLite + 主机/端口/账号/库名/表名，模板行 326–369），HTTP 语义字段（URL/鉴权/健康探测/4xx 重试）统一由 `isHttpAdapter`（行 804）守门对其隐藏；`buildConfig` 对 database 分支单独出配置（行 1286–1316，模板顶层键名=目标表列名）。v3.41 事件下拉补 `weighing_product_done`（称重成品结案，行 707）——两阶段流水线称重的正式结案事件，此前没露出导致达梦直写连接照 v2.0 手册配 `cycle_end` 一条收不到。**v3.45 端口框修复（BUG-010）**：Modbus TCP 与数据库直写两处「端口」输入框——独立窄 label（label-width 45px）+ 容器禁压缩 + 去步进钮 + 限 1~65535——治"全局 label 120px 把端口框挤到 25px 宽，粘贴出超长数字看不见还把连接搞炸"（与后端 database_adapter 端口守门配套，见 02 册）。
> - **`MES/OrderPanel.vue`（1220 行，v3.45 复核）**：来源标签三态——external=橙"外部"、**v3.45 新增 packaging=绿"包装扫码"**（包装工单同步 `_sync_work_order` 镜像进来的单，见 02 册协调器条目）、其余灰"手动"。
> - **`MES/OrderInboundPanel.vue`（815 行）**：v3.37 「开工后自动开始检测」开关 `start_detection_on_task`（行 86，默认关）；v3.39 在途报警软件内消除两开关（「横幅手动消除」`allow_manual_clear` 行 295 + 「清零联动消除」`clear_on_counter_reset` 行 299，默认全关保持"只能外部消除"契约）；v3.39 监控页任务信息条两显示开关（「工单进度徽标」`show_order_chip` 行 316 + 「两行表格布局」`two_line_layout` 行 320，默认=原界面）；v3.42 「终态工单再开工」下拉 `terminal_order_policy`（行 111，revive 自动复活为在产（默认）/ reject 拒收提示，emptyForm/applyConfig/buildConfig 三处同步行 489/603/724）。
> - **`MES/UsbScanGunDialog.vue`（238 行）+ `composables/useScanGun.js`（236 行）**：v3.35 USB 扫码枪第 4 种用途 `ack`（报警确认按钮）——本质是只发固定码的 HID 按键，按一下走事件人工确认接口解除本工位定格（称重缺料/超量/投错等 require_ack 报警的物理确认入口）；路由纯函数 `routeCode` 对 `ack` 短路（useScanGun 行 67），`doAck`（行 139–157）不进包装/拉单/绑定链路，无待确认事件温和提示不当故障；对话框补用途单选/工位选择文案/模拟测试路由标签。
> - **`Settings/PackagingFlowPanel.vue`（1008 行，v3.45 复核）**：v3.35 包装线扫码健壮性三件——复合条码取段（多段拼接码按分隔符拆段、按前缀认段或取第 N 段 + 取段预览 `compositePreviewResult` 前端镜像后端取段逻辑）；工单号识别正则 `order_code_pattern`（仅开第一单时校验，挡开机第一枪误扫数量码）；「放工单=工单收尾」`tail_paper_as_close_action`（行 375，v3.43 文案改语义：箱归周期结算、放工单归工单收尾）。v3.43 新增：缺工单判定方式单选（`paperJudgeMode` computed 行 632——scan/timeout 二选一映射 `tail_paper_scan_alarm` + `tail_paper_timeout_s` 两个落库字段，选 timeout 自动给 30s 缺省）+ 放工单时限秒数 + 「完成工单重扫拦截」开关与提示事件下拉（行 425–438）。上银预设（`applyHiwinPreset` 行 714–775）v3.43 一键化补齐：每箱 96 固定值（items_per_box_source='config'）、自动切项目（同名匹配）、异常→事件映射全套（缺工单=2 其余=3）、重扫拦截默认开——套完只需手配工位/扫码器/拉单连接。**v3.45 两件**：① 组⑦加「同步到工单管理」开关 `sync_work_orders`（默认开——扫码开工的工单同步进工单页来源=包装扫码，开工"生产中"/收尾"已完成"/中止"已取消"；关=老行为只存运行记录）；② 新增**组⑧「箱标签扫码——每箱扫标签放行 / 从标签取本箱数量」**（仅滑块口径显示）——`box_label_scan_required` 总开关（每箱进「等扫箱标签」态含第一箱）+ 取量子开关 `label_qty_enabled`（必须扫到带数量的复合二维码才放行，**逐箱可变**覆盖工单级每箱数/尾数计划）+ 数量段号 `label_qty_segment`（按组③分隔符拆段取第 N 段）+ 识别正则 `label_qty_pattern`（段序不固定的兜底，配了优先于段号——"格式变了只改这里不改代码"）+ 重扫处置 `label_rescan_action`（ignore/update，update=贴错标签重贴重扫覆盖本箱目标）+ 未扫做完整箱处置 `unauthorized_cycle_action`（hold 挂起等人工（默认）/ book 报警照常落账）+ 收尾对账 `label_total_check` + 三个事件下拉；上银预设同步扩容（组⑧全开、事件=3、取第 3 段）。
> - **`Project/CreateProjectDialog.vue`**：v3.37 修复"图像分割"被误禁用且误标"语义分割"——恢复可选、文案改"图像分割 (Instance Segmentation)"（行 12）。
> - **`Source/index.vue`（1436 行，v3.45 复核）**：v3.45 海康 SDK 源加「目标帧率」下拉（10/15/25/30/50/60，单工位与多工位配置面各一处），带提示"需设备端码流帧率同步调高才有效，超过码流实际帧率不会增加画面帧数"；启动时 `fps: cfg.hcnetFps || 25` 传后端，`makeDefaultWsConfig` 加 `hcnetFps: 25` 缺省，持久化写 `hcnet_fps` 键（工位配置回读同款）——与后端 `main.py` 自动恢复读同一键闭环（此前重启自动恢复必跌回硬编码 25fps，见 01 册 main.py 条目）。

**Project 子组件**（v3.32 视图拆分产物；v3.41 补账新建条目）

| 组件 | 行数 | 职责 / v3.3x 变更 |
|------|------|------|
| `StepsConfigTab.vue` | 1228 | 步骤表 A/B 双表编辑。v3.35 顺序类模式加「外设门控」列（`isSeqLike` 行 1156，弹层配 tare 去皮门控 / weight_judge 称重判定；`onGateEnabledChange` 行 1171–1198 启用任一门控即注入 `pipeline_config.weighing.drive_mode='step_gate'` 融合模式，「称重配置」页签随之出现）；v3.35 表 B 加「等待不被打断」列（`disappear_uninterruptible` 行 692，工具驻留画面产线防消失等待被其他步骤掐掉） |
| `LogicConfigTab.vue` | 2158 | 逻辑模式/结算/逐件/区域事件规则编辑。v3.33 逐件加「重复打同一颗螺丝防护」块（`duplicate_screw_alarm` + 移开/重压确认帧数/报警节流/提示时长四参数，行 1043–1091）与「换板兜底结算」`workpiece_absent_settle_frames`（行 852–865）；v3.34 区域事件规则加秒基「确认时长」`min_seconds`（行 1331–1340，帧率解耦，0=按帧数）；v3.36.1 overlap 规则加「目标框扩边」`object_margin`（行 1356–1365，工件下沿扫码几何盲区补丁，纯空间量与帧率无关）；v3.42 区域规则「抓取锚点框」`grabRegionAnchor` 实时结果为空时走 `inferOnce` 单帧推理兜底（行 1886，与 LabelSplitDialog 同款）；v3.43 新增「实时NG（违规即时结算）」卡片（行 83–107，顺序型非 last_first / 检测模式 / 自定义-基于顺序时显示，写 `pipeline_config.instant_ng_on_violation`，配套 Project/index.vue 的 initProjectDefaults 回读与保存点、last_first 强制置关） |
| `WeighingConfigTab.vue` | 574 | 称重配置页签（`logic_mode='weighing'` 或融合模式出现）。v3.35 融合模式提示条（`isStepGate` 行 505）+ 前置选择有效期 `context_expiry`（行 515，never/daily/shift/hours 四策略）+ 视觉料源防错 `visual_guard` 规则表（行 516，复用主 ROI 编辑器画判定区域）；v3.38 「检测中心显示实时称重数值条」开关 `show_monitor_weights`（行 475）；v3.39 两阶段流水线三卡——「驱动模式」下拉（scale 逐道投料 / pipeline 两阶段流水线，行 14–27）、「流水线参数」卡（三标签绑定/判定料别/皮重范围/队列深度/秤台区 ROI，行 29–84）、「秤指令时序」卡（17 项现场可调，与后端 timing 17 键一一对应：去皮触发源三档/稳定窗/离秤确认/清零延迟与重发/标签帧数与新鲜期/装料与收尾超时等，行 86–168；`pipe`/`timing` computed 行 511–512） |
| `EventsConfigTab.vue` | 156 | 事件卡片编辑。v3.34 require_ack 事件展开「确认后保留周期（断点补做）」`ack_keep_cycle` 勾选框（行 78–90，勾上=确认只解除定格保留在制周期，从断点补做；默认不勾=确认即整件重做） |
| `LabelSplitDialog.vue` | 645 | 同标签区域拆分编辑器。v3.34 多轮次真实模型两防护输入框——切换确认时长 `trigger_min_seconds`（过滤单帧误检闪现）+ 切换标签置信度下限 `trigger_conf`（行 131–150）；老规则缺省回填 0 零差异（load 行 281–292）；v3.42 「抓取锚点框」`grabAnchorRef` 实时结果为空时走 `inferOnce` 单帧推理兜底（行 481）+ 标定流程文案改为"停止/待机后抓取"（打包版检测中锁菜单切不进项目页，这是唯一通路） |
| `labelSplit.js` | 245 | 拆分规则纯逻辑（默认值/校验/虚拟步骤同步）。v3.34 默认规则带 `trigger_min_seconds: 0.5` / `trigger_conf: 0`（行 25–26）；校验放行"区域名与原始标签同名"——仅未开多轮次时才是真冲突（行 94–101，多轮次最终名带轮次前缀不会自我映射） |
| `CreateProjectDialog.vue` | 43 | 新建项目弹窗（任务类型下拉，v3.37 恢复图像分割可选） |

> `Project/index.vue` 本体 v3.34~v3.39 变更：`initProjectDefaults` 补 `ack_keep_cycle` 默认 false（行 1320）；`ensureWeighingDefaults`（行 1130–1225）补 v3.35 `drive_mode`/`context_expiry`/`visual_guard`/防错报警映射 + v3.38 `show_monitor_weights` + v3.39 `pipeline`/`timing` 子树默认值，融合模式（顺序 SOP+步骤门控）也持有 weighing 子树同样补默认（行 1349–1354）；称重配置页签第二出现条件 `isStepGateFusion`（行 814，模板行 512）；主 ROI 编辑器复用扩两处——v3.35 视觉料源防错规则区域、v3.39 流水线秤台区 `onscale_polygon`（`pipelineZoneEditing` 行 802，`openPipelineRoiEditor` 行 853，互斥标记）；v3.38 数据设置加自定义班次列表编辑（行 471–498，每班只填开始时刻、按时刻排序跨天自动衔接，≥2 条生效）。

**Monitor 子组件**

| 组件 | 行数 | 职责 |
|------|------|------|
| `ChannelVideoCard.vue` | 90 | 多工位视频卡片+overlay canvas 注册 |
| `SopStepPanel.vue` | 131 | SOP 步骤条+自动滚动 |
| `PerItemPanel.vue` | 465 | 逐件/混合逐件面板（v3.41 复核） |
| `WeighingPanel.vue` | 260 | 称重投料看板（v3.41 复核） |
| `WeighingLiveBar.vue` | 91 | v3.38 新增：融合模式实时称重数值条（见下） |
| `PackagingFlowCard.vue` | 297 | 包装箱结算进度；v3.43 增 awaiting_paper「等放工单收尾」状态横幅/状态色，强制结案按钮对该状态可用 |
| `CustomMixItemPanel.vue` | 124 | 混合 tracking 物品校验 |
| `ExternalAlarmBanner.vue` | 290 | 外部 MES 在途报警横幅（v3.41 复核） |
| `RecordingFailureOverlay.vue` | 95 | 录像失败列表 |
| `VirtualScanGun.vue` | 101 | 包装线虚拟扫码测试；v3.43 扫码成功/失败弹右上角 ElNotification（与 USB 真枪通路同款体验），预设补 111/222/333（对应 `tests/uat/mock_hiwin_mes_server.py` 仿真工单库） |
| `framePump.js` | 58 | 多通道 MJPEG 解码背压 |

> Monitor 子组件 v3.33~v3.39 变更（v3.41 复核）：
> - **`PerItemPanel.vue`（v3.33 重复打防护 UI）**：`last_warning` 有值时显示"⚠ 重复打"黄条（模板行 100–105）；前端自己按 `duplicate_warning_display_sec` 倒计时撤横幅、不依赖后端持续送帧清空（`shownWarning` + watch `last_warning.ts` 行 352–369——视频停/暂停时后端不再清，只靠后端会一直挂着；ts 不变不刷新计时）；被重打的个体色块叠琥珀色环（`item.dup > 0` 行 393–394，tooltip 行 418）；面板高度随横幅条数拉长而非压缩步骤卡片区（`panelHeight` 行 379–386）。
> - **`WeighingPanel.vue`（v3.38 皮重 + v3.39 流水线看板）**：头部加皮重显示（去皮那一刻的工件/容器自重，`tareWeight` 行 166）、投料中标注"净重(已投料)"；`drive_mode='pipeline'` 时（`isPipeline` 行 171）切换为流水线看板——秤上件/最近稳定净重/待收尾队列（FIFO，离秤已结算等收尾动作正式结案）/最近结算 3 条（模板行 55–92），隐藏扫码开始入口（件号自动生成），实时重量优先显示"有效读数" `effective_weight`（已扣离秤未清零期间的零点基线，行 161–163），补 `empty`（空秤·待上件）/`departing`（离秤确认中）两相位文案（行 177）。
> - **`ExternalAlarmBanner.vue`（v3.39 软件内手动消除出口）**：入站配置 `allow_manual_clear` 开启时横幅出现"手动消除"按钮（模板行 18–25，配置读取行 132），`manualClear`（行 68–88）带确认弹窗调 `clearActiveAlarmsManual`（后端还有配置开关+权限双闸）；默认关保持"只能外部消除"的对接契约。
> - **`WeighingLiveBar.vue`（v3.38 新增，91 行；组件注释标 v3.35.1 为开发期版本号）**：融合模式（视觉 SOP 驱动 + 秤步骤门控）下 SOP 面板占核心位，称重数值以横条补充——实时读数/皮重/净重（已去皮时当前读数即净重，行 65）/步骤门控状态 chips（`gates` passed=绿 ✓、等秤=琥珀闪烁）/最近一次判定；自轮询 `/weighing/state` 800ms（行 89），通道未登记称重引擎静默；显隐由父级按项目称重配置 `show_monitor_weights` 守门（Monitor `isStepGateWeighing`）。

**i18n**：5 语言包 `locales/zh-CN.js` 等；默认 `zh-CN`（`main.js` 行 46）。

---

## 五、Monitor 深读

**文件**：`frontend/src/views/Monitor/index.vue`（6272 行；v3.43 复核）

> **v3.43 增量**：① SOP 流程卡片显示条件补排除称重投料模式（`!isWeighingMode`，行 612）——称重看板与 SOP 同链互斥且 SOP 在前，不排除的话称重项目永远被 SOP 卡片抢占、专属看板一次都轮不到（萍乡百斯特现场撞出；融合模式 logic_mode 是 sequential 不受影响）。② 人工确认弹窗体验：原因优先读后端固化的 `pending_ack.reason`（不随 30s 事件窗滚动丢失，行 5937），新增「确认后」处置方式明示行 + 按钮文案/颜色按 `keeps_cycle` 分道（"断点继续"绿 / "整件重做"橙，行 1246–1290），确认成功 toast 同步区分。配套 `BottomBar.vue`/`Navbar.vue` 模式文案表补 weighing/region_events（五语言 locales 同步加 `mode.weighing`/`mode.region_events`——此前称重项目底栏显示"未定义"）。

### 5.1 布局分支（template）

| 条件 | 行号 | 布局 |
|------|------|------|
| `layoutBodyOverride` 非空 | 9–124 | 插件整页覆盖；宿主仍渲染双工位 Toast/人工确认/录像异常 |
| `channelCount === 2` | 127–311 | 双列：视频+MES 条+计数+SOP+控制 |
| `channelCount > 2` | 313–478 | 2×2 视频格 + 选中工位详情底栏 |
| 否则（单工位） | 480–1339 | 12 栅格：左 7 视频+模式面板，右 5 统计+图表+步骤表+控制 |
| `layoutFooterOverride` | 1344–1348 | 插件底栏 slot |
| `ExternalAlarmBanner` | 1361 | 全局在途报警 |

### 5.2 模式专属面板（单工位左列）

| logic_mode | 组件/区块 | 行号 |
|------------|-----------|------|
| sequential/detection/custom | `SopStepPanel` | 608–612 |
| tracking | 内联清点/容器卡片 | 615–678 |
| weighing | `WeighingPanel` | 681–685 |
| per_item | `PerItemPanel` | 687–691 |
| 融合模式称重数值条（v3.38） | `WeighingLiveBar` | 702–706 |
| custom+per_item 混合 | 第二个 `PerItemPanel` mix | 708–712 |
| custom+tracking 混合 | `CustomMixItemPanel` | 715–720 |
| 包装 | `PackagingFlowCard` + `VirtualScanGun` | 722–732 |
| 周期性强制动作 | 内联进度条 | 735–795 |

> v3.38 起：`WeighingLiveBar` 显隐由 `isStepGateWeighing`（行 3162–3166）守门——`pipeline_config.weighing.drive_mode === 'step_gate'` 且 `show_monitor_weights !== false`；与 SOP 面板并存（步骤看 SOP、重量看横条）。

### 5.3 核心状态与数据流（script）

**Store 引用**：行 1366–1400

**多工位**（行 1782–2990）：

- `channelCount` ← `GET /workstations/`（`fetchChannelCount` 行 2954–2990）
- `multiChannelData[ch]`：每通道运行时快照（行 1852 起初始结构）
- 轮询：`startMultiPolling` 150ms（行 2420–2459）→ `processChannelResult`（行 2115–2395）
- 视频：单工位 `<img>` 双缓冲；多工位 `fetch` 解析 multipart MJPEG → canvas（行 1962–2110）
- `STREAM_HOST` 硬编码 `http://localhost:8001`（行 1962）— **桌面若改端口可能不一致**（单工位 `buildStreamUrl` 用 `getBackendHost()` 行 3014）

**单工位轮询**（行 4711–5014）：

- 间隔 150ms（行 5014）；`STREAM_SWAP_INTERVAL=600` 次后 swap 释内存（行 4703, 4723）
- 截图去重：`performance.screenshotDedup` → `known_shots` 参数（行 4730）
- 周期边界：`current_cycle_id` + `resultHoldActive` 展示期（行 4819–4887, 1799–1830）
- 流卡死：`fps>0 && !isStreaming` 超时强制重连（行 4770–4790）

> v3.40 起（川南反馈，轮询以后端为真相源）：轮询循环内用后端返回的检测/运行布尔同步前端 `isDetecting`/`isRunning`（行 4750–4765）——检测由后端自行拉起/停下（开工报文自动开始检测等）时按钮灰度/状态章跟得上；`isOperating` 期间不抢（用户点开始/停止的乐观更新优先，完成后自然对齐）。

> v3.40 起（空闲看门狗，川南反馈）：监控页空闲（无轮询无取流）时后端被开工报文自动拉起，老行为前端毫无感知（FPS 0/开始按钮不灰/信息条不出），要切页再切回才恢复。`startIdleWatchdog`（行 5531–5567）空闲时每 2s 探一次源状态，发现后端已在跑就自动接管——同步运行/检测态 + `startPolling()` + `forceReconnectStream()`，等价一次页面重进；轮询已在跑/多工位/用户操作中三种情况休眠让位。`onMounted` 启动（行 6035）、`onUnmounted` 停止（行 6100）。

> v3.38 起（扫码器旁路 SN）：`display.monitor.showBypassSn` 打开后 MES 信息条显示旁路 SN 只读框（模板行 838–848）；每 2s 轮询后台监控线程内存缓存（`pollBypassStatus`/`startBypassPolling` 行 1652–1690），watch 开关即起即停（行 1701–1708），专属规则优先、无则兜底（`bypassSnFor` 行 1684–1688）。

> v3.39 起（任务信息条显示定制，川南"信息显示不全"）：`taskInfoDisplay` 加 `show_order_chip`（关=信息条与双工位 MES 条都不渲染"工单 单号 进度 良率"块，行 159/857）与 `two_line_layout`（开=任务要素改"表头一行+信息一行"表格布局防截断，模板行 866–888，样式 `.task-info-table` 行 6146–6166）；默认值=原界面（行 3258–3259），随入站配置加载（行 3277–3278）。同版 `display.monitor.showScanButtons` 守门"清除本次扫码/禁用扫码"按钮显隐（单/多工位共 5 处 v-if，行 172/190/383/385/895）。

> v3.41.1 起（USB 相机曝光保持，技彩锁帧修复）：页面 localStorage 自动恢复相机源（`autoRestoreSource` 行 5843 附近）启动相机时随分辨率/FPS 一并回传曝光两参数（自动曝光开关默认开、手动曝光值非数字兜底 -6）——老代码只传分辨率/FPS，重开后后端不知道要关自动曝光，AE 复活压死帧率。后端三条重开路径的配套见 `01_backend_core.md` source mixin 表下 v3.41.1 注。

> v3.38 起（NG top3 保持）：后端 NG 步骤 map 为空时保留现有 top3 展示（待机/重开 sync 配置不再清累计），仅总产量与不良总数都归零（真清零）才清空——单工位行 5469–5480、多工位 `processChannelResult` 行 2219–2229；工位清零同时重置 `ngStepRanking`（行 5686）。

**检测控制**：

- 单工位：`startDetection`（行 4451 起）含副模型强制完整启动
- 多工位：`startDetectionForChannel`（行 2793 起）
- 停止前 `confirmScanPairBeforeStop`（行 2888–2924）
- 人工确认：`pendingAckDisplay`（行 5898–5942）+ `ackPendingForChannel` + 提权窗

**步骤/PT 算法**（`updateStepsFromBackend` 行 5148 起）：

- 期望序列按位置分配 `completedByPos`（行 5252–5261）
- OK/NG：`cycleSumStepDurations` 权威 PT 守门（行 5280–5330）
- 跟踪模式补丁：行 5390 附近

> v3.40 起（末步结果权威锁定，川南反馈）：末步"完成"判定原来只认已离开画面（`_leftFrame`），成品滞留画面余像重现时结果列被打回 '--' 再变回、肉眼持续闪烁。修复：末步已有权威 PT（`_posAuthoritative`，行 5305 = 完成过一次完整出现）后结果锁定——`_isLastStep ? (_leftFrame || _posAuthoritative) : ...`（行 5318–5323），不随余像回退。

> v3.32.0 起（区域事件模式前端适配，基线后收尾提交）：步骤行是"动作规则名"、画面框是"模型类别名"，两者永远对不上——"进行中"判定改看后端 in-flight PT（`stepIsLive` 行 5150–5157）；结果列只标 OK 不做"重复/乱序/漏做=NG"的顺序推断（行 5325–5328，周期好坏由后端结算判定说了算，复检序列同动作出现两次合法）。

**生命周期**（行 6020–6143）：

- `onMounted`：先 `await fetchChannelCount()`（行 6026）再 `getSourceStatus`；避免单/多工位流竞态（注释 D2/D3）
- `onUnmounted`（行 6096 起）：停轮询/流/图表/定时器 + 空闲看门狗 + 旁路 SN 轮询

### 5.4 插件 slot 契约（Monitor 内）

| slot name | 行号 | 用途 |
|-----------|------|------|
| `monitor.layout.body` | 9–22, 1402 | 整页 layout 覆盖 |
| `monitor.layout.footer` | 1344–1348, 1403 | 底栏覆盖 |
| `monitor.step-cell.duration` | 1076 | 步骤 PT 列 |
| `monitor.step-cell.status` | 1089 | 步骤结果列 |
| `cycle-result.indicator` | 34, 286 | OK/NG Toast |
| `monitor.workpiece-flow.indicator` | 1354 | RFC11 串行流指示 |

`layoutBodyActions`（行 1414 起）向插件暴露开始/停止/画框/MES/步骤表渲染等完整能力。

### 5.5 关键常量

| 常量 | 值 | 行号 |
|------|-----|------|
| 轮询间隔 | 150ms | 2459, 5014 |
| 包装 state 轮询 | 1500ms | 1697 |
| 旁路 SN 轮询（v3.38） | 2000ms | 1673 |
| 空闲看门狗探活（v3.40） | 2000ms | 5563 |
| STREAM_SWAP_INTERVAL | 600 tick ≈90s | 4703 |
| WORKPIECE_RESULT_HOLD_MS | 3500ms | 3216 |
| STREAM_FIRST_FRAME_TIMEOUT_MS | 5000ms | 1782 |
| WeighingLiveBar 自轮询 | 800ms | 组件内行 89 |
| Navbar 激活项目跟随轮询（v3.37） | 5000ms | Navbar 行 748 |

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

> v3.41 复核：自基线 a23a8d2 以来核心壳文件（`main.js` / `backend-manager.js` / `license-manager.js` / `preload.js`）**无变更**（仅 `package.json` 版本号 bump），7.1~7.4 小节维持原样。唯一实质变更在打包脚本 `electron/build/installer.iss`（284 行）——v3.33 安装目录可选 + 升级"搬家"：升级也永远显示"选择安装位置"页（`DisableDirPage=no` + `UsePreviousAppDir=yes`，默认仍是上次目录）；[Code] 段从卸载注册表读上次安装目录（`GetPreviousInstallDir`），本次选了不同目录时先抢救旧目录内可能残留的用户数据库到用户数据目录（`BackupUserDataFrom` 新旧两处都查），装完后**确认旧目录确实躺着我们的主程序 exe 才整目录清除**（`DetectInstallDirMove`，防注册表脏值误删无关目录），并把开机自启快捷方式重指到新位置。同目录覆盖安装行为不变。

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
| F-01 | 中 | Monitor 1962 vs 3014 | 多工位 `STREAM_HOST` 硬编码 `localhost:8001`；单工位用 `getBackendHost()`。非默认端口或远程后端时多工位视频可能连错（v3.41 复核仍在） |
| F-02 | 低 | `detection.js` 65 | `resetDetection` 仍调 `/detection/reset` 非 `/source/detection/reset`，需核对后端是否别名挂载 |
| F-03 | 低 | Monitor 6245 行单文件 | 上帝组件：检测/UI/MES/插件/图表/轮询全耦合；后续拆分风险高（基线以来又 +378 行：旁路 SN/信息条定制/空闲看门狗/称重数值条等） |
| F-04 | 中 | 插件 loader | 无沙箱；插件 ESM 与主程序同权限，恶意插件可调用 host.api |
| F-05 | 低 | `plugin-theme` apply | 插件切换需重启才完整生效（设计如此，Settings 有提示） |
| F-06 | 低 | Navbar 设置下拉 | 下拉「退出」与 v3.10「登出」并存，语义易混（一个 gracefulQuit 一个 auth logout）（v3.41 复核：行号已漂移，以代码为准） |
| F-07 | 中 | 冷启动 | 前端 90s 等 manifest + axios 180s 重试 + Electron 深探 5min：多层等待策略重叠，排障时需分清哪层超时 |
| F-08 | 低 | BottomBar 109–112 | 运行时间从 mount 起计时，非后端 session 真实时长 |
| F-09 | 低 | `useScanGun` | USB 枪依赖键盘速度启发式；人手极快或枪配置错误可能误判 |
| F-10 | 信息 | splash/vendor | three.js/mediapipe 等大体积 vendor 仅 splash 动画用，与主窗业务无直接耦合 |

---

## 附录：阅读覆盖清单

| 目录 | 文件数 | 说明 |
|------|--------|------|
| `frontend/src/` | 约 100（v3.41 复核，含 v3.38 新增 `WeighingLiveBar.vue`） | 基线全部通读；v3.33~v3.41 增量按 `git diff a23a8d2..HEAD` 26 个变更文件逐 diff 回写。Monitor 当前 6245 行 |
| `electron/` 核心 | 4 | main.js, backend-manager.js, license-manager.js, preload.js（基线以来无变更）；`build/installer.iss` v3.33 变更见第七节 |
| `electron/splash/` | — | 仅记为启动动画资源，未逐行读 vendor |

**文档路径**：`docs/dev/_reading_notes/04_frontend_electron.md`
**最后复核**：2026-07-17（v3.41.0，基线 a23a8d2 → HEAD 前端/Electron 增量补账）
