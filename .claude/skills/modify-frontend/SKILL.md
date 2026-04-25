---
name: modify-frontend
description: "安全修改前端代码：Store依赖、API调用链、组件间数据流、i18n、ElementPlus组件约束。修改Vue组件或Store前先分析影响。"
argument-hint: "[要修改的组件或功能]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# modify-frontend: 前端安全修改分析

你正在帮用户安全修改前端 Vue3 代码。

计划修改: $ARGUMENTS

## 组件依赖关系图

```
App.vue
  └── router-view
      └── layout/index.vue (主布局)
          ├── Navbar.vue ← useProjectStore, useSystemStore, api/project, api/operators（当前操作员展示：作业员姓名、设备编号；与 Monitor 选择器同步）
          │   ├── 项目选择器 → handleProjectChange → activateProject + setProjectConfig
          │   ├── 自动恢复 → localStorage + 动态import api/index, store/useSourceStore
          │   └── 报警自动连接 → api (直接axios)
          ├── router-view (页面内容)
          │   ├── Monitor/index.vue ← useProjectStore, useSystemStore, useSourceStore, api/detection, api/model, api/operators（操作员选择器、与导航栏同步当前作业员）
          │   ├── Project/index.vue ← api/project, api/model, useProjectStore, useSystemStore
          │   ├── Source/index.vue ← api/index, api/detection, api/model, api/project, useSourceStore, useSystemStore
          │   ├── Data/index.vue ← api/data, api/detection, useProjectStore, useSystemStore（操作员筛选、Session/Cycle 列表展示操作员列）
          │   ├── Report/index.vue ← api/report, api/project
          │   ├── Model/index.vue ← api/model
          │   ├── Alarm/index.vue ← api/index, api/project, useProjectStore, useSystemStore
          │   ├── Settings/index.vue ← useSystemStore, api/operators（操作员管理卡片：列表、新增、编辑、停用）
          │   ├── MES/index.vue ← api/mes, api/scanner, api/gateway (Tabs: OrderPanel, WorkpiecePanel, DefectPanel, ScannerPanel, GatewayPanel)
          │   │   ├── OrderPanel.vue ← api/mes (工单CRUD + 状态机)
          │   │   ├── WorkpiecePanel.vue ← api/mes (工件追溯)
          │   │   ├── DefectPanel.vue ← api/mes (缺陷帕累托 + 缺陷代码管理)
          │   │   ├── ScannerPanel.vue ← api/scanner (VS600设备配置 + 状态)
          │   │   └── GatewayPanel.vue ← api/gateway (外部 MES：连接 CRUD、字段映射、测试、通讯日志)
          │   └── Activation/index.vue ← window.electronAPI
          └── BottomBar.vue ← useSystemStore, useProjectStore
```

### MES / Gateway 相关前端文件（修改对接或 Monitor 额外字段时必查）

| 文件 | 说明 |
|------|------|
| `frontend/src/api/gateway.js` | 外部 MES Gateway API（`/mes/gateway/*`）封装 |
| `frontend/src/views/MES/GatewayPanel.vue` | 「外部对接」Tab：连接 CRUD、字段映射、测试、通讯日志 |
| `frontend/src/views/MES/index.vue` | MES 主入口，含「外部对接」Tab 挂载 |
| `frontend/src/views/Monitor/index.vue` | 额外字段输入（`extraFieldsSchema` 等与 Gateway schema 联动） |

### 操作员管理相关前端文件

| 文件 | 说明 |
|------|------|
| `frontend/src/api/operators.js` | 操作员 API 封装：`/operators` CRUD、`set-current`、`current` |
| `frontend/src/views/Settings/index.vue` | 操作员管理卡片：列表、新增、编辑、停用 |
| `frontend/src/views/Monitor/index.vue` | 操作员选择器；与 Navbar 同步当前作业员 |
| `frontend/src/layout/Navbar.vue` | 显示作业员姓名、设备编号（与 `display.navbar` 等开关配合） |
| `frontend/src/views/Data/index.vue` | 操作员筛选；Session/Cycle 表格列展示操作员 |
| `frontend/src/api/data.js` | `getSessionsByDate`（及同类列表接口）需支持 `operatorId` 参数，与后端 `operator_id` 过滤对齐 |

## Store 数据流

### useProjectStore → 消费者
- **Navbar.vue:** 写入 currentProject, currentProjectId
- **Monitor/index.vue:** 读取 currentProject (步骤配置、检测参数)
- **Data/index.vue:** 读取 currentProjectId (筛选数据)
- **BottomBar.vue:** 读取 isRunning
- **layout/index.vue:** 无直接依赖

### useSystemStore → 消费者
- **Settings/index.vue:** 读写 display, detection, performance
- **Navbar.vue:** 读写 display (自动恢复时直接覆盖!), 读写 developerMode (密码保护切换)
- **Monitor/index.vue:** 读取 detection (检测框、Toast、语音配置)
- **layout/index.vue:** 读取 isDetecting (导航锁)
- **BottomBar.vue:** 读取各显示设置
- **Project/index.vue:** loadDetectionFromProject (合并项目检测配置)
- **Source/index.vue:** 读取 developerMode (控制多工位选项可见性)

### useSourceStore → 消费者
- **Source/index.vue:** 读写所有字段
- **Navbar.vue:** 自动恢复时动态import读取
- **Monitor/index.vue:** 读取 sourceType, isStreaming

## localStorage 持久化键

| Key | 写入者 | 读取者 | 内容 |
|-----|--------|--------|------|
| `tianjun_source_config` | useSourceStore | useSourceStore | 视频源配置 |
| `tianjun_display_settings` | useSystemStore | useSystemStore, Navbar | 显示设置 |
| `tianjun_detection_settings` | useSystemStore | useSystemStore | 检测框设置 |
| `tianjun_performance_settings` | useSystemStore | useSystemStore | 性能设置 |
| `tianjun_developer_mode` | useSystemStore | useSystemStore, Navbar, Source | 开发者模式开关 |
| `tianjun_auto_save` | Navbar | Navbar | 自动保存开关 |
| `tianjun_last_project` | Navbar | Navbar | 上次项目ID |
| `tianjun_last_source_*` | Source | Navbar(恢复) | 上次使用的视频源 |

## 强制分析流程

### 第1步: 确定修改范围

1. 读取要修改的组件完整代码
2. 列出该组件的所有 import（Store、API、子组件）
3. 列出该组件 emit 的事件和 expose 的方法

### 第2步: 追踪数据流

1. 如果修改 Store → 搜索所有使用该 Store 的组件
2. 如果修改 API 调用 → 确认后端端点兼容性
3. 如果修改 props/emit → 确认父组件适配
4. 如果修改 localStorage key → 确认所有读取者适配

### 第3步: 检查特殊约束

- **检测中导航锁:** `systemStore.isDetecting` 为 true 时只能访问 Monitor
- **自动恢复:** Navbar 的自动恢复逻辑可能覆盖你的修改
- **项目切换:** `handleProjectChange` 会重置大量状态
- **双重计时器:** Navbar 和 BottomBar 各有独立的 runTime 计时器
- **store.display 直接覆盖:** Navbar 用 `JSON.parse` 直接赋值而非深合并

### 第4步: 生成影响报告

```
修改的组件/文件: [路径]
依赖的Store: [列出]
依赖的API: [列出]
被影响的组件: [列出所有消费者]
localStorage影响: [涉及的key]
导航锁影响: [是否影响检测中的行为]
自动恢复影响: [是否影响启动恢复流程]
建议测试: [具体测试步骤]
```

## 屏幕自适应 (rem 缩放体系)

系统通过动态 `html font-size` 实现 12寸~50寸 屏幕自适应（main.js `initResponsive()`）。

### 工作原理
- 基准: 1920px 视口 → 16px root font-size
- 范围: 12px (小屏下限) ~ 24px (大屏上限)
- Tailwind 的所有工具类（`p-4`, `text-sm`, `h-16` 等）底层是 rem，会自动跟随缩放

### 编码规范
- **CSS/Tailwind 中必须用 rem**，禁止硬编码 px 用于尺寸/间距/字体大小
  - ✓ `text-sm`, `p-4`, `gap-2` (Tailwind 默认 rem)
  - ✓ `text-[0.625rem]`, `min-w-[5.625rem]` (Tailwind 任意值用 rem)
  - ✗ `text-[10px]`, `min-w-[90px]` (不会跟随缩放)
- **内联 fontSize 用 rem 换算:** `fontSize: (value / 16) + 'rem'`，不要 `value + 'px'`
- **Canvas 绘制用 `window.__uiScale`:** `ctx.font = \`bold ${basePx * (window.__uiScale || 1)}px Arial\``
- **el-icon 图标大小:** 用 CSS class `text-[1.75rem]` 替代 `:size="28"` prop（`:size` 是绝对 px，不跟随缩放）
- **颜色/阴影/边框的 1px 可以保留 px**（视觉装饰不需要缩放）

### display Store 显示开关字段

```
display.navbar.brandName      → Navbar 品牌名称显示 (v-if)
display.navbar.appName        → Navbar 中间标题显示 (v-if)
display.navbar.projectSelector → Navbar 项目选择器
display.navbar.inspector      → BottomBar 作业员 + Settings 基本信息开关
display.navbar.deviceId       → BottomBar 设备编号 + Settings 基本信息开关
display.navbar.mode           → BottomBar 当前模式
display.navbar.status         → BottomBar 运行状态
display.navbar.runtime        → BottomBar 运行时间
display.navbar.realtime       → Navbar 实时时间
display.monitor.ngTop3        → Monitor NG步骤TOP3 面板 (v-if)
display.monitor.ngTopDisplayMode → NG TOP3 显示模式 ('percentage' | 'count')
display.monitor.stepStrip     → SOP流程条
display.monitor.statsPanel    → 右侧统计面板
display.monitor.defectChart   → 不良统计图表
display.monitor.capacityChart → 产能完成图表
display.monitor.stepTable     → 步骤统计表格
display.monitor.showFps / showLatency / showDetectionCount → 性能指标
display.monitor.ctIncludeNg   → CT 是否包含 NG 周期
display.monitor.defaultCounters.showTotal/showGood/showBad/showNgSteps → 内置计数器
```

## 修改原则

1. **响应式安全:** 不要直接替换 reactive 对象，用属性赋值或深合并
2. **清理定时器:** onUnmounted 中清除 setInterval/setTimeout
3. **API 错误处理:** 不要忽略 catch，至少 ElMessage.error
4. **Store 写入:** 通过 action 写入，不要直接 `store.xxx = yyy`
5. **i18n:** 新文本如果需要国际化，加到 locales/zh-CN.js（其他语言暂不管）
6. **Element Plus:** 遵循暗色主题，样式在 style.css 中覆盖
7. **尺寸单位:** 用 rem 不用 px（见「屏幕自适应」章节）

## v2.6.0 多通道前端要点

### Monitor 多通道数据结构
- `multiChannelData[ch].project` — 通道绑定的完整项目数据（从后端 workstation_config 加载）
- `multiChannelData[ch].projectName` — 通道项目名
- `startDetectionForChannel(ch)` **必须**用 `multiChannelData[ch].project` 而非全局 `currentProject`
- `syncProjectConfig(channel, explicitProject)` 第二参数可传入通道独立项目

### 每通道检测设置
- `useSystemStore.channelDetections` — `{ channelId: detectionConfig }` map
- `systemStore.loadDetectionForChannel(chId, dc)` — 加载通道检测配置
- `systemStore.getChannelDetection(chId)` — 获取通道检测配置（回退到全局）
- `speak(text, ch)` 和 `getToastConfig(toastId, ch)` 支持通道参数

### 新增前端 API 模块
- `frontend/src/api/cluster.js` — 集群汇总 API
- `frontend/src/api/external_device.js` — 外部设备 API

### 新增页面组件
- `frontend/src/views/MES/ClusterPanel.vue` — 集群配置 (Master/Slave)
- `frontend/src/views/MES/ExternalDevicePanel.vue` — 外部设备管理

## v2.7.5 前端要点

### 工单表单模板（`OrderPanel.vue`）
- localStorage key：`mes_order_form_template_v1`
- 一项 item 结构：`{ key, label, type, required, preset, optionsText }`
- `type` 取值：`text | textarea | number | date | time | datetime | select | switch`
- 预设字段 key 必须与后端 ORM 对齐：`order_no / product_name / product_code / product_spec / planned_qty / priority / remark`
- `DynamicFieldInput` 组件（本地定义，`h()` 渲染）统一按 type 选控件
- 保存时：
  - 预设字段 → payload 顶层
  - 非预设 → `payload.extra_data`
  - **必填字段被删**：新建时自动补 `ORD-{Date.now()}` / `未命名`，编辑时信任后端校验
  - 编辑时保留原 `extra_data` 中模板外的键，不丢失
- `select` 选项格式：`"紧急=1,高=2,正常=3,低=4"`（label=value）或 `"A,B,C"`（label==value）
- 自定义字段 key 校验：`^[A-Za-z_][A-Za-z0-9_]*$` 且唯一

### 画面变换 UI（`Settings/index.vue`）
- 位置：「显示设置」 tab 下的「画面变换」卡片
- `transformChannel` 选通道；`rotation/flip_h/flip_v` 绑到本地 form
- 保存调 `POST /source/transform/config?channel=N`，立即对后续帧生效
- **前端绝对不要对检测框做二次旋转/翻转**——后端已经处理好，检测框坐标已经在显示坐标系下
- **v2.7.14 起语义变了**：推理永远走原图（保持训练精度），变换只作用于显示/录像/快照；后端会把 bbox 从原图坐标系映射到显示坐标系再下发，前端拿到的就是对齐的

### 外部设备 UI（`ExternalDevicePanel.vue` / `ScannerPanel.vue`）
- 所有 axios 错误处理统一：`detail` 可能是 string | list | dict，展示前要规范化为 string
  ```js
  const d = e.response?.data?.detail
  const msg = typeof d === 'string' ? d
            : Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join('; ')
            : d ? JSON.stringify(d) : '未知错误'
  ElMessage.error(msg)
  ```
- 串口子参数（data bits / parity / stop bits）存在 `form.protocol_config` 而非顶层
- 保存返回 200 但有 `warning` 字段 → 用 `ElMessage.warning` 提示（设备暂时不可连通）

## v2.7.12 前端坑点（必看，反复翻车）

### 函数式组件用 `h()` 渲染 ElementPlus 控件 → 不能写字符串组件名

`frontend/src/views/MES/OrderPanel.vue` 的 `DynamicFieldInput` 犯过 3 次同一个错：

```js
// ✗ 错 — 本项目 Vite + ElementPlus auto-import 下渲染成空节点，输入框全部隐形
return h('el-input', { modelValue, 'onUpdate:modelValue': onInput })

// ✓ 对 — 必须显式 import 组件对象
import { ElInput, ElInputNumber, ElDatePicker, ElTimePicker, ElSwitch, ElSelect, ElOption } from 'element-plus'
return h(ElInput, { modelValue, 'onUpdate:modelValue': onInput })
```

排查路径：对话框打开后 label 在、控件区域是空的、F12 Elements 看到 `<el-input>` 未被解析成真实 DOM → 90% 是这个问题。

适用文件：任何用 `h()` 手写渲染函数的文件（GatewayPanel / OrderPanel / ScannerPanel / 动态表单组件）。

### `ElInputNumber` 默认 precision=2 → 整数字段会显示 `0.00`

- 整数字段（如 `planned_qty` 计划数量）单独判 key 走 `precision: 0 + step: 1`
- 或给字段 meta 加 `integer: true` 字段后在渲染分支里取用

### 扫码器冷却配置（ScannerPanel.vue）

- 新字段 `ok_rescan_cooldown_sec` (int, 秒, 默认 0 关闭) 在"去重/冷却"区域
- 语义：OK 工件在该秒数内的同条码重扫静默忽略；NG 不受影响便于纠错重扫
- 后端 `ScannerConnection` dataclass / `MESHookManager._handle_scan` 对应字段必须同步

### 集群 ClusterPanel 目标明细列表渲染

- `sub_reports` 必须去重：前端显示时相信后端已按 `(channel_id, source_address)` 保留最新一条
- 若仍看到同通道多行 → 说明 `cluster_collector._receive_station_report_locked` 的去重逻辑被改坏，去查 backend 而不是前端

### MES GatewayPanel 适配器下拉 4 选 1

- 单选值：`rest` / `form-data` / `form-urlencoded` / `query-string` / `modbus_rtu`
- 每种下面配一句话说明 + 例子，避免用户选错
- `form-data` 独有"表单字段名 form_key"，其余 3 种 HTTP 形式不用

## v2.7.15 前端要点

### `OrderPanel.vue` — 模板结构 + 表头全动态化

v2.7.15 把工单管理表格表头从 9 个硬编码 `<el-table-column>` 改成完全跟模板走，模板项结构扩展：

- **新字段 `visible: bool`**：每项是否在表格里显示（默认 true，旧 localStorage `undefined` 兼容也按 true）
- **新字段 `system: bool`** + **`type: 'system'`**：标记列表系统列（来源/状态/进度/良品-不良/良率/创建时间/操作），不进新建工单表单
- 三类字段语义：
  - **PRESET**：表单字段，进新建工单弹窗，进工单管理表头（key 与后端 ORM 对齐）
  - **SYSTEM**：列表系统列，**只**进表头（用户能改 label/visible/顺序，不能改 key/type/required/options/删除）
  - **CUSTOM**：自定义字段，进表单弹窗保存到 `extra_data`，表头从 `row.extra_data[key]` 取值显示

### 关键常量与 computed

- `PRESET_META` / `PRESET_ORDER` / `PRESET_KEYS`：表单字段元数据
- `SYSTEM_COL_META`：每项 `{ label, typeLabel }`，typeLabel 用于字段配置弹窗"类型"列友好显示
- `COL_META`：表格 9 列的 `{ prop, width, minWidth, fixed }`，渲染时用 `colMetaFor(key)` 取值（自定义字段没定义时回落 `{prop:key, width:120}`）
- `colLabel(key)` computed-like：表头 label 解析顺序 `template.label → SYSTEM_COL_META.label → PRESET_META.label → key 字面量`
- `visibleColOrder` computed：`template.filter(it => it.visible !== false).map(it => it.key)`，**不再加兜底**，全关就空（用户意图）
- `formItems` computed：`template.filter(it => it.type !== 'system')`，新建/编辑表单只渲染表单字段
- `canHide()` 现在永远 true（任意列都能显隐）

### localStorage 迁移

- 模板 key：`mes_order_form_template_v1`（不变）
- **新增一次性迁移标记 `mes_order_form_template_visible_migrated_v1`**：第一次加载 v2.7.15 时把所有列的 visible 强制重置为 true，避免老用户被旧版未持久化的 visible=undefined 污染
- `loadTemplate` 还会自动补齐缺失的 SYSTEM 列到尾部（兼容 v2.7.14 及之前）
- `openTemplate` 拷贝 `tplDraft` 时再 normalize 一次 visible（防 HMR 残留状态）

### 字段配置弹窗的列保护

| 字段类型 | key | label | type | required | options | 显示 | 上下移 | 删除 |
|---|---|---|---|---|---|---|---|---|
| PRESET | 锁定 | 可改 | 锁定 | 可改 | 可改 | 可改 | 可改 | 可删 |
| SYSTEM | 锁定 | 可改 | 锁定（显示 typeLabel + 后缀"不可改"） | 锁定 | 锁定 | 可改 | 可改 | 锁定 |
| CUSTOM | 可改 | 可改 | 可改 | 可改 | 可改 | 可改 | 可改 | 可删 |

`saveTemplate` 落盘前会强制把 SYSTEM 列的 type='system'/required=false/optionsText='' 写回，防止任何路径绕过 UI 限制污染数据。

### 改这个文件的注意事项

1. **不要把表头改回硬编码** —— 客户改了模板就不生效
2. **新增 SYSTEM 列要同时加进 `SYSTEM_COL_META` + `COL_META` + 表头 v-for 的 `<template v-else-if>` 分支**（三处不同步会渲染异常）
3. **新增 PRESET 表单字段要同时加进 `PRESET_META` + `PRESET_ORDER` + 后端 ORM**（前端校验 key 必须对得上）
4. **表头 v-for 里的 `<template #default="{ row }">`** 只能写一个 default slot，所有列分支都在里面用 v-if/else-if 分流（曾经踩坑：写多个 `<template #default v-if=...>` 会被 Vue 拒绝渲染）
