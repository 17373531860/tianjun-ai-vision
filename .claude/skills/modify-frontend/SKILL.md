---
name: modify-frontend
description: "安全修改前端代码：Store 依赖、API 调用链、组件间数据流、i18n、ElementPlus 组件约束。修改 Vue 组件或 Pinia Store 前先用本 skill 做影响分析。"
argument-hint: "[要修改的组件或功能]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__playwright, mcp__context7"
---

# modify-frontend: 前端安全修改分析（v3.5.x）

你正在帮用户安全修改前端 Vue3 代码。

计划修改: $ARGUMENTS

## 0. 技术栈速查（事实源 AGENTS.md 第二节）

| 项 | 值 |
|---|---|
| 框架 | Vue 3.5.x + Composition API |
| 状态管理 | Pinia 3.x（**4 个 store**） |
| 路由 | Vue Router 4.x（hash 模式） |
| UI 库 | Element Plus 2.13.x（中文 doc 主） |
| i18n | vue-i18n 11.x（5 语种） |
| 构建 | Vite 7.x |
| 样式 | Tailwind CSS 3.4 + ECharts 6 + 自定义 `style.css` |
| 图标 | `@element-plus/icons-vue` 2.3 |
| HTTP | axios 1.13（统一封装在 `api/index.js`） |

> 项目根**没有** `package.json`，前端单独是 `frontend/` 子包；`electron/package.json` 是版本号唯一权威源。

## 1. 真实视图清单（事实源：`frontend/src/views/`）

实际只有 **10 个视图目录**（AGENTS.md 写"12 个"不准），加 3 个 layout 组件：

| 视图 | 路由名 | 行数 | 备注 |
|---|---|---|---|
| `Activation/index.vue` | `Activation` | 140 | **独立路由 `/activation`**（不在 Layout 下，激活前唯一可达） |
| `Monitor/index.vue` | `Monitor` | **4051 ⚠️** | 项目最大 .vue，检测中心 |
| `Project/index.vue` | `Project` | 2925 | 项目配置（7 个 JSON 字段集中地） |
| `Source/index.vue` | `Source` | 1288 | 6 种视频源配置 |
| `Data/index.vue` | `Data` | 1985 | 数据中心 + 自定义导出入口 |
| `Data/components/CustomExportDialog.vue` | — | — | v3.5.0 自定义导出 |
| `Data/components/RealtimeRulesDialog.vue` | — | — | v3.5.0 实时规则 |
| `MES/index.vue` | `MES` | 75 | **7 个 Tab 容器**（不挂 WMaxPanel） |
| `MES/{Order,Workpiece,Defect,Scanner,Gateway,External,Cluster}Panel.vue` | — | 243~1381 | 7 个子面板 |
| `MES/WMaxPanel.vue` | — | 888 | **不在 MES tab 中**，由 `ScannerPanel.vue` 内部 v-if 挂载 |
| `Model/index.vue` | `Model` | 339 | 模型仓库（**单数 `Model`**，不是 `Models`） |
| `Alarm/index.vue` | `Alarm` | ~1480 | 报警设置（含「NG 短信推送」卡，调 `api/sms.js`） |
| `Settings/index.vue` | `Settings` | 1441 ⚠️ | 系统设置（含画面变换、操作员管理） |
| `Report/index.vue` | — | — | **已删**（2026-07 死代码清理，报表展示由 Data 页接管） |

Layout：`frontend/src/layout/index.vue`(117) + `Navbar.vue`(549 ⚠️) + `BottomBar.vue`(69)。

## 2. 路由结构（事实源：`frontend/src/router/index.js`）

```
/activation                ← 独立路由，激活页（Activation）
/                          ← Layout（侧边抽屉 + Navbar + 内容 + BottomBar）
  /monitor      Monitor    ← 默认 redirect 目标
  /project      Project
  /model        Model
  /source       Source
  /data         Data
  /mes          MES
  /alarm        Alarm
  /settings     Settings
```

总共 **8 个 Layout 子路由 + 1 个独立 Activation = 9 路由**。**没有 Login / Reports（复数）/ Logs 等路由**——AGENTS.md 旧描述里的 12 视图是历史描述。

### 路由守卫关键事实
- `licenseChecked` 模块级单例，第一次进入 + Electron 环境会调 `window.electronAPI.getLicenseStatus()`，无效跳 `/activation`
- `LAST_ROUTE_KEY = 'tianjun:lastRoute'` 记忆上次路由（仅 `REMEMBERABLE_NAMES` 8 个），冷启动会自动恢复
- `licenseChecked = false` 时**不放行业务页** —— 改路由守卫时不要"异常即放行"

## 3. Pinia Store 清单（事实源：`frontend/src/store/`，共 **4 个**）

| Store | 行数 | 持久化 key | 主要用途 |
|---|---|---|---|
| `useSystemStore` | 277 | `display_settings` / `detection_settings` / `developer_mode` / `performance_settings` | 显示开关、检测框、性能、操作员、**多通道检测配置** |
| `useProjectStore` | 25 | 无（纯内存，由 Navbar 写入） | 当前项目 id/name/object，运行状态 |
| `useSourceStore` | 137 | `source_config` | 6 种视频源配置 |
| `useScannerDisableStore` | 67（**v3.4.2 新增**）| 无（后端 `_disabled_channels.json` 落盘）| 按工位禁用扫码联动状态 |

> `useChannelStore` **不存在**——多通道状态分布在 `useSystemStore.channelDetections`（per-channel 检测配置）和 Monitor 组件内的 `multiChannelData[ch]` 局部 state。

### useSystemStore 关键字段
- `language` / `theme` / `isDetecting` / `currentProjectId` / `developerMode`
- `display.{ navbar, monitor, brandName, appName, inspectorName, deviceNumber }` —— 全局显示开关
- `detection`（绑定**项目级**，从 `Project.detection_config` 加载）：boxColor、字体、`toasts.{ok,ng,scan,warn_no_barcode}`、`customToasts[]`
- `channelDetections: { [channelId]: detectionConfig }` —— 多工位每通道独立配置
- `data.{ retentionDays, autoCleanup, autoBackup, backupPath }`
- `performance.{ frameLimitEnabled, targetStreamFps, halfPrecision, mediapipe* }`

### Action 清单
- `setLanguage / setTheme / setDetecting / setCurrentProjectId`
- `loadSettings()` —— 读 `display_settings`，**深合并 navbar / monitor.defaultCounters**
- `loadDetectionFromProject(detectionConfig)` —— 项目切换时调
- `loadDetectionForChannel(channelId, detectionConfig)` / `getChannelDetection(channelId)`（回退到全局 `detection`）
- `saveDetectionSettings()` —— 同时写 `Project.detection_config` 和 `localStorage.detection_settings`
- `setDeveloperMode / loadDeveloperMode / loadPerformanceSettings / savePerformanceSettings`

### useScannerDisableStore（v3.4.2）
```
state: { disabledChannels: number[], loaded, loading, toggling }
getters: isChannelDisabled(channelId), disabledSet
actions:
  loadStatus(force=false)         # GET /scanner/disable/status
  toggle(channelId, disabled)     # POST /scanner/disable/toggle
  applyServerHint(ch, disabled)   # 来自 source/status 推送的 mes.scan_disabled 字段
```
> 不要绕过 store 自己直接调 API：禁用闭包是后端算的，全前端只信任 `disabledChannels`。

## 4. localStorage 持久化键（**真实**，事实源：`grep localStorage`）

| Key | 写入者 | 读取者 |
|---|---|---|
| `display_settings` | useSystemStore.loadSettings/Settings/Monitor | 同左 + Navbar |
| `detection_settings` | useSystemStore.saveDetectionSettings | useSystemStore + Settings |
| `developer_mode` | useSystemStore.setDeveloperMode | useSystemStore.loadDeveloperMode |
| `performance_settings` | useSystemStore.savePerformanceSettings | useSystemStore.loadPerformanceSettings |
| `source_config` | useSourceStore.saveConfig | useSourceStore.loadConfig + Monitor 启动检查 |
| `auto_save_settings` | Navbar | Navbar / Source/index.vue |
| `tianjun:lastRoute` | router.afterEach | router.beforeEach（冷启动恢复） |
| `mes_order_form_template_v1` | OrderPanel | OrderPanel（**legacy fallback**，已被 library 替代） |
| `mes_order_template_library_v1` | OrderPanel | OrderPanel |
| `mes_order_template_active_id_v1` | OrderPanel | OrderPanel |
| `mes_order_form_template_visible_migrated_v1` | OrderPanel | OrderPanel（v2.7.15 一次性迁移标记） |
| `mes_order_archived_ids_v1` | OrderPanel | OrderPanel |

> **旧 SKILL 提到的 `tianjun_xxx_*` 前缀键已不存在**。只有 `tianjun:lastRoute` 一个保留 `tianjun:` 前缀。

## 5. API 客户端清单（`frontend/src/api/*.js`，共 17 个）

| 文件 | 行数 | 后端前缀 | 状态 |
|---|---|---|---|
| `index.js` | 102 | — | axios 实例 + `getBackendHost`（视频流用） |
| `cluster.js` | 23 | `/cluster/*` | OK |
| `data.js` | 223 | `/data/*` | OK（含 `operator_id` 过滤） |
| `detection.js` | 48 | `/source/*` | OK |
| `export.js` | 160 | `/export/*` | OK（注释说 `txt/csv` 是过时的，**实际 5 格式**） |
| `external_device.js` | 14 | `/external-devices/*` | OK |
| `gateway.js` | 20 | `/mes/gateway/*` | OK |
| `mes.js` | 36 | `/mes/*` | OK |
| `model.js` | 52 | `/models/*` | OK |
| `operators.js` | 8 | `/operators/*` | OK |
| `project.js` | 25 | `/projects/*` | OK |
| `report.js` | — | `/reports/*` | **已删**（2026-07 死代码清理） |
| `scanner.js` | 25 | `/scanner/*` | OK |
| `wmax.js` | 84 | `/scanner/wmax/*` | OK |
| `videoArchive.js` | ~90 | `/export/video-archive/*` | OK（v3.53 录像归档；`downloadEvidencePack` 是 blob 下载，timeout 300s） |
| `task.js` | — | `/tasks/*` | **已删**（2026-07 死代码清理） |
| `camera.js` | — | `/cameras/*` | **已删**（2026-07 死代码清理；旧式相机表后端仍在） |

后端 baseURL：默认 `http://localhost:8001/api/v1`，可被 `VITE_API_BASE_URL` 覆盖。

## 6. 组件依赖关系图

```
App.vue
└── router-view
    ├── Activation/index.vue                      ← window.electronAPI（License）
    └── layout/index.vue (Layout)
        ├── Navbar.vue (549) ← useSystemStore, useProjectStore, useSourceStore(动态 import),
        │                     api/project, api/operators, api/index(直接 axios)
        │   ├── 项目选择器 → handleProjectChange → activateProject + setProjectConfig
        │   ├── 自动恢复 → auto_save_settings + 动态 import api/index, store/useSourceStore
        │   ├── 当前作业员 / 设备编号显示 ← display.navbar.* 开关
        │   └── 报警自动连接 → api (直接 axios)
        ├── router-view（8 子路由，互斥）
        │   ├── Monitor/index.vue (4051 ⚠️) ← useSystemStore, useProjectStore, useSourceStore,
        │   │                                  useScannerDisableStore, api/detection, api/model,
        │   │                                  api/operators, api/project, api/data
        │   │   └── multiChannelData[ch].project   ← 每通道独立项目（不要用全局 currentProject）
        │   │   └── speak(text, ch) / getToastConfig(toastId, ch)  ← 多通道参数
        │   ├── Project/index.vue (2925) ← useSystemStore, useProjectStore, api/project, api/model
        │   ├── Source/index.vue (1288) ← useSystemStore, useSourceStore, api/index, api/detection,
        │   │                              api/model, api/project
        │   ├── Data/index.vue (1985) ← useSystemStore, useProjectStore, api/data, api/detection,
        │   │   │                        api/export, api/operators
        │   │   └── VideoArchiveDialog.vue ← api/videoArchive, api/export（v3.53 归档规则/台账/证据包弹窗）
        │   │   └── components/CustomExportDialog.vue
        │   │   └── components/RealtimeRulesDialog.vue
        │   ├── MES/index.vue (75) ← 7 Tab 容器
        │   │   ├── OrderPanel.vue (1192) ← api/mes, api/cluster, api/project
        │   │   ├── WorkpiecePanel.vue (243) ← api/mes
        │   │   ├── DefectPanel.vue (269) ← api/mes
        │   │   ├── ScannerPanel.vue (1381 ⚠️) ← api/scanner + 内部挂 WMaxPanel.vue
        │   │   ├── GatewayPanel.vue (1073 ⚠️) ← api/gateway
        │   │   ├── ExternalDevicePanel.vue (831) ← api/external_device
        │   │   └── ClusterPanel.vue (834) ← api/cluster
        │   ├── Model/index.vue (339) ← api/model
        │   ├── Alarm/index.vue (921) ← useSystemStore, useProjectStore, api/index, api/project
        │   └── Settings/index.vue (1441 ⚠️) ← useSystemStore, api/operators, api/project, api/index
        └── BottomBar.vue (69) ← useSystemStore, useProjectStore
```

## 7. 修改前 grep 矩阵

修改前必查的 grep 命令：

| 修改对象 | 必查 grep |
|---|---|
| 任意 Store action | `rg "store\.<actionName>\|<storeName>\(\)" frontend/src` |
| Store state field | `rg "<storeVar>\.<field>" frontend/src` |
| API 函数 | `rg "import .* from '@/api/<file>'" frontend/src` + `rg "<funcName>\(" frontend/src` |
| 视图组件 | `rg "from '@/views/<dir>'" frontend/src` |
| localStorage key | `rg "'<key>'" frontend/src` |
| display.* 开关字段 | `rg "display\.<sub>\.<field>" frontend/src` |
| 多通道方法 | `rg "channelDetections\|loadDetectionForChannel\|getChannelDetection\|multiChannelData" frontend/src` |
| 路由跳转 | `rg "router\.push\|<router-link" frontend/src` |
| Element Plus 组件 | `rg "<el-<tag>" frontend/src` |

## 8. Dead code / 已知不一致（不要扩展、不要复活）

| 位置 | 状态 | 说明 |
|---|---|---|
| ~~Report 视图 + task/camera/report 三个 api 文件~~ | **已删**（2026-07 死代码清理） | 全仓核实无引用 + build 绿后删除，别复活 |
| `frontend/src/api/export.js` 顶部注释 | 已修正 | fmt 注释已补齐 `txt/csv/docx/xlsx/pdf` 5 种 |
| `useSystemStore.display.brandName / appName / inspectorName / deviceNumber` | 默认值 | 真实显示走 `display.navbar.*` 开关 + 用户输入 |
| `Navbar.vue` 自动恢复 vs Settings 修改 | 时序坑 | Navbar 用 `JSON.parse` 直接覆盖 `store.display`，而非深合并 |
| `WMaxPanel.vue` | 不在 MES tab 直接挂载 | 由 `ScannerPanel.vue:10` 通过 `v-if="activeTab==='wmax' && hasWmaxDevice"` 间接挂 |

## 9. ElementPlus 约束（v2.13.x）

### 必须显式 import 才能用 `h()` 渲染
项目用 Vite + ElementPlus 全量引入（`main.js: app.use(ElementPlus)`），但**函数式渲染时字符串组件名不解析**：

```js
// 错：Vite + ElementPlus 全量引入下渲染成空节点
return h('el-input', { modelValue, 'onUpdate:modelValue': onInput })

// 对：必须显式 import 组件对象
import { ElInput, ElInputNumber, ElDatePicker, ElTimePicker, ElSwitch, ElSelect, ElOption } from 'element-plus'
return h(ElInput, { modelValue, 'onUpdate:modelValue': onInput })
```

适用：`OrderPanel / GatewayPanel / ScannerPanel` 等所有用 `h()` 写动态表单的文件。排查路径：对话框打开后 label 在、输入框区域空白、F12 看到未解析的 `<el-input>` 标签 → 90% 是这个问题。

### `el-select` 单选 v-model 必须是 string/number，**不能 boolean**
```vue
<!-- 错：v-model 绑 boolean，下拉选中后 el-select 不更新显示 -->
<el-select v-model="form.enabled"><el-option :value="true" label="启用" /><el-option :value="false" label="禁用" /></el-select>

<!-- 对：用 string 'on'/'off' 或单独 v-if 渲染 el-switch -->
<el-switch v-model="form.enabled" />
```

### `el-input` placeholder 不能是空字符串
空 placeholder 在某些版本下会强制改回组件默认 `请输入`，绕过办法：用 `:placeholder="' '"`（一个空格）或 v-bind 计算属性。

### `ElInputNumber` 默认 `precision=2` → 整数字段显示 `0.00`
整数字段（`planned_qty / count / interval` 等）单独 `precision: 0 + step: 1`。

### `el-table` 的 `empty-text` 不要嵌套引号
```vue
<!-- 错：HTML attr 双引号嵌字符串引号会语法错 -->
empty-text=""暂无规则""
<!-- 对 -->
empty-text="暂无规则，点上方按钮新建一条"
```

### `el-icon` 用 CSS class 控制大小，别用 `:size`
`:size` 是绝对 px，不跟随 rem 缩放：用 `<el-icon class="text-[1.75rem]">`。

### `axios error.response.data.detail` 类型规范化
后端 422 / 400 的 `detail` 可能是 `string | list[ValidationError] | dict`，**展示前必须规范化为 string**：
```js
const d = e.response?.data?.detail
const msg = typeof d === 'string' ? d
          : Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join('; ')
          : d ? JSON.stringify(d) : '未知错误'
ElMessage.error(msg)
```
适用：`ExternalDevicePanel / ScannerPanel / GatewayPanel / OrderPanel`。

## 10. Vue 模板 / Vite 坑点

### Vue 模板字符串插值
Vue template 在 attribute 里看到 `{{ }}` 会先解析成 Vue 表达式 → 想在 placeholder/默认文本里写 Jinja2 模板片段时不能直接写：
```vue
<!-- 错：Vue 把它当变量解析 -->
<el-input :placeholder="'{{ field.path }}'" />
<!-- 对：用 computed 绕过 -->
<el-input :placeholder="contentPlaceholder" />
<!-- 或 div 文字里用 HTML entity -->
<div>&#123;&#123; field.path &#125;&#125;</div>
```

### Vite HMR 注意
- HMR 不会重置 Pinia store —— 改 store 默认值后必须刷整页，否则旧 state 残留
- HMR 后 `onMounted` 不重跑，`watch` 副作用累积；改组件副作用时观察是否需要 `onUnmounted` 清理
- 路由懒加载在 HMR 后偶发"白屏" → 强刷即可
- `import.meta.env.DEV` 真值 → main.js 中**生产构建会静默 console.log/debug**，调试日志只在 dev 可见

### Element Plus auto-import 不在本项目
本项目用 `app.use(ElementPlus)` 全量引入，**没有用** `unplugin-vue-components` 的 `ElementPlusResolver`。所以：
- 模板里 `<el-input>` OK（全量注册）
- `h(...)` 必须显式 import（参考第 9 节）

## 11. i18n 规范

`vue-i18n` 11.x，5 语种：`zh-CN / zh-TW / en-US / ja-JP / ko-KR`。**`legacy: false` + `globalInjection: true`**，所以模板里直接 `$t('menu.monitor')`。

实际状态：i18n key **只覆盖 layout 部分**（菜单、Navbar、BottomBar 少量项），其余视图全是中文硬编码。

新文本规则：
- 如果是菜单 / 顶栏 / 底栏 / 全局按钮 → 加到 `locales/zh-CN.js` 对应 group，其他语言**先空着**（用户不要求）
- 如果是页面内的中文标签 / 提示 → **直接写中文字符串**（与现有代码风格一致），不强行 i18n
- 不要把 `$t()` 引入到没用 i18n 的视图里制造一致性混乱

## 12. 强制分析流程（修改前必走）

### 第 1 步：确定修改范围
- 读取要修改的组件完整代码
- 列出所有 import：`import { ... } from '@/store/* | @/api/* | element-plus | ...`
- 列出 `defineProps / defineEmits / defineExpose / defineModel`
- 列出对外暴露的 axios endpoint（如果是 api 文件）

### 第 2 步：追踪数据流
1. 改 Store action / state → grep 所有消费者
2. 改 API 函数签名 → 对照后端路由（用 `api-sync` skill）+ grep 调用点
3. 改 props / emit → 确认父组件适配
4. 改 localStorage key → 确认所有读取者适配 + 是否需要迁移标记

### 第 3 步：检查特殊约束（**容易遗漏**）
- **检测中导航锁**：`systemStore.isDetecting === true` 时只能停在 Monitor，layout 侧栏所有非 Monitor 链接被 `nav-disabled` + `handleNav` 拦截
- **License 守卫**：`licenseChecked` 第一次进入业务路由必跳 `electronAPI.getLicenseStatus()`；非 Electron 直接放行
- **自动恢复**：`Navbar.vue` 启动时读 `auto_save_settings` 可能覆盖 store；改启动逻辑前先确认它的执行顺序
- **项目切换**：`handleProjectChange` 重置 `useSystemStore.detection`、可能重置多通道 `channelDetections`
- **双重 runTime 计时器**：Navbar 和 BottomBar 各有独立 `setInterval`，`onUnmounted` 都要清
- **store.display 直接覆盖**：`Navbar.vue` 用 `JSON.parse` **直接赋值**，不是深合并；改 `display` 默认值后老用户会丢字段
- **多通道**：Monitor 用 `multiChannelData[ch].project` 启动检测，**不要**改成全局 `currentProject`

### 第 4 步：生成影响报告
```
修改的组件/文件: [路径]
依赖的 Store: [列出]
依赖的 API: [列出]
被影响的组件: [列出所有消费者]
localStorage 影响: [涉及的 key + 是否需要 migration 标记]
导航锁影响: [是否影响检测中行为]
自动恢复影响: [是否影响 Navbar 启动恢复]
多通道影响: [是否波及 channelDetections / multiChannelData]
建议测试: [具体测试步骤]
```

## 13. 屏幕自适应 (rem 缩放)

`main.js: initResponsive()`：根据视口宽度（基准 1920px）动态设 `<html>` font-size，范围 12~24 px，`window.__uiScale = fs / 16`。

### 编码规范
- **CSS / Tailwind 必须用 rem，禁止硬编码 px**（尺寸 / 间距 / 字号）
  - 对：`text-sm`、`p-4`、`gap-2`、`text-[0.625rem]`、`min-w-[5.625rem]`
  - 错：`text-[10px]`、`min-w-[90px]`
- **内联 fontSize**：`fontSize: (value / 16) + 'rem'`，**不要** `value + 'px'`
- **Canvas 绘制**：`ctx.font = \`bold ${basePx * (window.__uiScale || 1)}px Arial\``
- **el-icon 大小**：CSS class `text-[1.75rem]` 替代 `:size="28"` prop（`:size` 不跟随缩放）
- 颜色 / 阴影 / 1px 边框可以保留 px（视觉装饰）

## 14. display 显示开关字段速查

```
display.brandName / appName / inspectorName / deviceNumber  ← 实际值（非开关）
display.navbar.brandName / appName / projectSelector / inspector / deviceId
            / mode / status / runtime / realtime            ← Navbar+BottomBar 显示开关
display.monitor.stepStrip / statsPanel / defectChart / capacityChart / stepTable
display.monitor.showFps / showLatency / showDetectionCount  ← 性能指标
display.monitor.ctIncludeNg                                  ← CT 是否包含 NG 周期
display.monitor.ptMode / ctMode                              ← 'avg' | 'last' | 'current'
display.monitor.ngTop3 / ngTopDisplayMode                    ← NG TOP3 + 'percentage'|'count'
display.monitor.defaultCounters.{ showTotal, showGood, showBad, showNgSteps }
```

## 15. 修改原则

1. **响应式安全**：不直接替换 reactive 对象，用属性赋值或深合并
2. **清理副作用**：`onUnmounted` 清 `setInterval / setTimeout / ResizeObserver / EventSource`
3. **API 错误处理**：不要忽略 catch，至少 `ElMessage.error`，并按第 9 节规范化 detail
4. **Store 写入**：通过 action 写，不要直接 `store.xxx = yyy`（除非是临时 hotfix）
5. **i18n**：见第 11 节
6. **Element Plus 暗色主题**：样式覆盖在 `style.css`，不要散在各组件 scoped style
7. **rem 单位**：见第 13 节
8. **Dead code 不要复活**：第 8 节列的文件不要修，整体死代码留给后续清理
9. **多通道**：用 `multiChannelData[ch].project` / `getChannelDetection(ch)`，不要用全局 currentProject 启动检测

## 16. 高频踩坑参考（部分历史 bug）

> AGENTS.md 第十二节统计 `modify-frontend` 历史 bug 45 条，下面是高频四类：

- Vue 模板 `{{ }}` 在 attr 中被吞 → 用 computed / HTML entity（第 10 节）
- `h()` 渲染字符串组件名 → 显式 import 组件对象（第 9 节）
- `el-select` 单选 v-model 绑 boolean → 改 string / 用 el-switch（第 9 节）
- `display_settings` 直接覆盖 vs 深合并 → Navbar.vue:477 老路径用直接覆盖，改默认值时注意老 key 丢失

## 17. 插件 UI 插槽 + 设置页面板新增模式（v3.16.0）

> 本节记录两类高复用前端扩展套路，源自 v3.16.0 补齐福建金龙 R1–R5 配置 UI。

### 17.1 给主程序加「插件可填充」的插槽（TjSlot）

主程序只留**插槽位**、不含业务逻辑；插件激活后注册组件填进来，未激活显示占位默认内容。

```vue
<TjSlot name="project.step-cell.durations" :step="step" :project="activeProject">
  <span class="text-[0.625rem] text-gray-600">需插件</span>
</TjSlot>
```

- 插槽 `name` 命名 `<view>.<位置>.<语义>`，与插件 `registry.slots.register(name, comp)` 对齐
- 默认 slot 内容 = 无插件时的占位，主程序保持零业务
- 插件侧组件用 `host.vue.reactive` 管本地态、`host.api.get/put` 调自有后端路由（已带登录 token）
- ⚠️ 7 个主程序 slot 接入点见 v3.13.1（settings/project tab、cycle-result.indicator、step-cell.duration/status、layout.body/footer）
- ⚠️ **v3.18.0 (RFC12 整页覆盖)**：`*.layout.body` 整页覆盖挂载点已从「仅 Monitor」扩展到**全部 8 个主视图**（Monitor/Project/Settings/Source/Model/Data/Alarm/MES 各有 `<TjSlot name="<view>.layout.body">` 包住整个页面 body）。无插件注册时 TjSlot 回退默认内容=原生整页，**零差异**；与局部 slot（如 `project.step-cell.durations` 三档列）**正交共存**——单 active 插件设计下，整页覆盖型插件（如 showcase）与局部增强型插件（如金龙三档）不会同时生效，不互相干扰。改这 8 个视图的最外层模板结构时，注意别破坏 `*.layout.body` TjSlot 的开闭包裹（详见 `docs/plugin-system/design/12_full_page_override_rfc.md`）。

### 17.2 给某页新增一个 Tab 配置面板

仿 `WorkpieceFlowPanel.vue` / `ChannelGroupPanel.vue` 三件套（⚠️ 信息架构重构后这两个面板在 `views/Source/`，包装结算/触发中心在 `views/MES/`——新面板先按域归位：工位协同进 Source、生产对接进 MES、纯系统级才进 Settings，见 AGENTS.md 产品决策原则）：

1. `frontend/src/api/<name>.js` — CRUD axios 封装（`import api from './index'`）
2. `frontend/src/views/<域>/<Name>Panel.vue` — 列表 + 新建/编辑 `el-dialog`（成员多选/策略 `el-select`/启用 `el-switch`）
3. 对应页 `index.vue` — `import` 面板 + 加 `<el-tab-pane label="..."><XxxPanel /></el-tab-pane>`（MES 页是分组按钮式 tab，见 `views/MES/index.vue` 的 `tabGroups`）

- 后端路由可能早已存在（如「工位组互通」R4 的 `/channel-groups` 在 v3.13.1/v3.15.5 已有），只是缺前端入口——**先确认后端有没有，别重造**
- `el-select` 单选别绑 boolean（见第 9 节）；对话框关闭重置表单避免脏数据残留

## v3.29.0 新增前端要点（外部 MES 双向对接 + 可配置化）

- **监控页开工四要素上屏**（`Monitor/index.vue`）：信息条扩展四要素 chip（任务号/产品代号/工序工步/操作员），数据来自 `/source/detection/results` 的 `mes.order.extra_data.inbound`；逐项显隐开关在入站对接面板（`OrderInboundPanel.vue` 的 `task_info_display`），**默认全关 = 维持原界面不追加任何标签**——改 Monitor 信息条务必保持"未开即原样"
- **在途报警横幅**（`Monitor/ExternalAlarmBanner.vue`，独立组件）：轮询 `/mes/inbound/active-alarms` 取未消除报警持续显示，外观/行为全可配（显隐/停靠/主色/刷新间隔/字段）；外部消除后下一轮轮询自动消失。**横幅现场点不掉是设计**（只能外部系统消除）
- **管理面板轮询间隔/日志条数可配**（`Settings/index.vue` 新增「轮询间隔」Tab + `store/usePollingStore.js`）：各 MES 面板（Cluster/Order/Scanner/ExternalDevice/WMax/UsbScanGun）的刷新频率与"最近日志"条数不再写死，统一读 store；新加管理面板时**别再写死轮询间隔/日志条数常量**，接入 `usePollingStore` 即可
- **后端自愈提示**（`App.vue`）：监听 `electronAPI.onBackendRecovered` 弹常驻 toast + 记 `app.lifecycle` 调试类别
