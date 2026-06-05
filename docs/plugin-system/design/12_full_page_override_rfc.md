# 12 — RFC 12：全页面整页覆盖 slot（Full-Page Override）

> 状态：Draft（v3.16 目标）
> 作者：AI agent + 项目主作者
> 前置：design 05（Tier 2 UI / slot 机制）、design 09（v3.13 平台升级）、`frontend/src/components/TjSlot.vue`、`frontend/src/composables/usePluginLoader.js`
>
> 目的：把"客户插件整页覆盖主程序某页主体"的能力从**仅 Monitor**（`monitor.layout.body`）扩展到**全部 8 个主程序页面**，使客户级整套 UI 重设计可以纯插件落地，主程序保持精简、零差异默认。

---

## 一、背景与动机

### 1.1 现状（design 05 §1.2 / U4 遗留）

- 整页覆盖能力**只有 Monitor**：`<component :is="layoutBodyOverride">`，且**强约束 `channelCount === 2`**（双工位）才挂载。
- 其余 7 页（Project / Model / Source / Data / MES / Alarm / Settings）**只能追加 Tab 或新增独立路由**，**不能替换整页主体**。
- design 05 §1.2 明确把"替换主程序某页为客户专属版"标为 ⚠️ 不支持，U4 留待后续放开。

### 1.2 需求

- 展会 / 客户级"整套 UI 重设计"诉求：8 页全部换成客户专属高科技外观，但**不污染主程序**、**不破坏既有功能与数据通路**。
- 归位判定（AGENTS.md 第三节）：**整页覆盖能力 = 平台基础设施 → 进主程序**；**具体客户 UI = 客户专属插件**。本 RFC 只做平台能力面，UI 由客户插件承载。

---

## 二、设计原则（不可违反）

1. **零差异默认**：没有 active 插件 / 插件没注册对应 slot 时，所有页面渲染**与现在字节级一致**（`<TjSlot>` 未注册 → 渲染默认 slot = 主程序原 DOM）。
2. **复用既有机制**：不发明新机制，全部走现有 `TjSlot` + `registry.slots.register(name, component)` + `usePluginThemeStore.pluginSlots`。
3. **数据走 props + host.api 双通道**：
   - 页面**实时/复杂前置**数据（如 Monitor 的检测轮询、控制按钮业务前置）→ 主程序经 props/`actions` 透传（契约性 API）。
   - 页面**标准 CRUD**（Project/Model/Data/MES/Alarm/Settings 的列表/增删改查）→ 插件直接用 `host.api`（已鉴权 axios）调既有 `/api/v1/*` REST，**不逐字段透传**，避免 props 爆炸。
4. **错误隔离是底线**：任一页插件组件抛错 → Vue `errorHandler` 兜底，主程序其它页面继续可用（与现有 `monitor.layout.body` 一致）。
5. **契约冻结**：本 RFC 定义的各 `*.layout.body` props 在 `manifest_version=1` 内冻结，删字段需升 plugin SDK 主版本。

---

## 三、新增 slot 清单

| slot 名 | 宿主文件 | 覆盖范围 | 透传 props |
|---|---|---|---|
| `monitor.layout.body` | `Monitor/index.vue` | 整页主体（**解除双工位限制**，见 §5） | 见 §4.1（现有） |
| `project.layout.body` | `Project/index.vue` | 项目页主体 | §4.2 |
| `model.layout.body` | `Model/index.vue` | 模型仓库主体 | §4.3 |
| `source.layout.body` | `Source/index.vue` | 输入源主体 | §4.4 |
| `data.layout.body` | `Data/index.vue` | 数据中心主体 | §4.5 |
| `mes.layout.body` | `MES/index.vue` | MES 壳主体（7 Tab 由插件自管） | §4.6 |
| `alarm.layout.body` | `Alarm/index.vue` | 报警页主体 | §4.7 |
| `settings.layout.body` | `Settings/index.vue` | 设置页主体（插件 Tab 仍可叠加） | §4.8 |

> 兼容：现有 `settings.tab.*` / `project.tab.*` / `cycle-result.indicator` / `monitor.step-cell.*` / `monitor.layout.footer` **全部保留不动**。整页覆盖与"追加 Tab"两条路并存，客户按需选。

---

## 四、各页 props 契约

> 通用约定：所有 `*.layout.body` 都以 `<TjSlot name="X.layout.body" v-bind="ctx">` 形式挂载，默认 slot = 主程序原页面 DOM。插件组件接收 `$attrs` 即 ctx。所有页面 ctx 至少含 `channel-count`（Number）。CRUD 一律走 `host.api`。

### 4.1 monitor.layout.body（现有，含展会扩展字段）
- `channel-count` / `multi-channel-data` / `selected-channel` / `channel-model-stats` / `current-project` / `actions` / `stream-url-builder`（保持现状）
- 新增（见 RFC 阶段 1 后端）：`multi-channel-data[ch]` 内补 `behaviorScore`、`recentCycleTimes[]`；设备状态/合格率趋势由插件经 `host.api` 拉 `GET /system/device-status`、`GET /data/stats/yield-trend`。

### 4.2 project.layout.body
- `current-project`（Object|null，已激活/选中项目）、`project-list`（Array，列表快照）、`channel-count`
- `actions`：`{ reload(), selectProject(id), activateProject(id), saveProject(payload), deleteProject(id) }`（封装主程序的激活后 `set-project` 热同步等前置）
- 其余（模型列表/格式/ROI 编辑）插件经 `host.api` 调 `/projects`、`/models`、`/models/formats/*`。

### 4.3 model.layout.body
- `channel-count`；其余全部 `host.api`（`/models`、`/models/upload`、`/models/{id}`）。

### 4.4 source.layout.body
- `workstation-mode`（Number=channel_count）、`channel-count`、`developer-mode`（Boolean）
- `actions`：`{ saveAndStart(channelConfigs), reloadWorkstationMode() }`（封装停旧流→启新流→绑项目→跳转的复杂链路）。

### 4.5 data.layout.body
- `current-project`、`channel-count`；其余 `host.api` 调 `/data/*`、`/export/*`。

### 4.6 mes.layout.body
- `channel-count`、`active-tab`（String，默认 `orders`）、`@update:active-tab`
- 7 个子面板数据插件自管，经 `host.api` 调 `/mes/*`、`/scanner/*`、`/cluster/*`、`/external-devices/*`。

### 4.7 alarm.layout.body
- `channel-count`、`selected-channel`、`@update:selected-channel`、`current-project`
- `host.api` 调 `/alarm/*`、`/projects/{id}`。

### 4.8 settings.layout.body
- `channel-count`、`plugin-settings-tabs`（透传现有插件 Tab 列表，便于插件整页内继续渲染它们）
- `host.api` 调 `/system/*`、`/auth/*`、`/users/*`、`/roles/*`、`/api-keys/*`、`/workpiece-flows/*`、`/source/*`（性能/卡尔曼/变换）。

---

## 五、Monitor 解除双工位限制

### 5.1 问题
现有 `Monitor/index.vue`：
```
v-if="layoutBodyOverride && channelCount !== 2"  → 黄色"请切双工位"提示
v-if="layoutBodyOverride && channelCount === 2"  → 挂载插件 + 双工位 Toast 网格(cols-2, v-for ch in 2)
```
单工位 / 四工位插件无法整页覆盖；且 Toast 叠层网格硬编码 2 列。

### 5.2 方案
- 删除"请切双工位"黄条（或仅在插件 manifest 显式声明 `requires_dual_station` 时保留 —— 默认不限制）。
- 覆盖块 gate 改为 `v-if="layoutBodyOverride"`（任意工位数都挂载）。
- 宿主 Toast 叠层网格按 `channelCount` 自适应列数（1→单列全屏；2→双列；>2→插件自管，宿主退化为单层 center Toast）。最稳做法：**插件整页覆盖时，宿主只渲染全局 center Toast / 人工确认 / 录像异常**，列级 Toast 交给插件（它已掌握每通道布局）。本 RFC 采用此简化：宿主保留全局浮层，列级 Toast 由插件用 `cycle-result.indicator` slot 自行摆位。
- `<component :is>` 已透传 `channel-count`，插件据此切单/多工位布局。

### 5.3 workpiece-flow.indicator slot bug
`Monitor/index.vue` 现有：
```
<TjSlot slot-name="monitor.workpiece-flow.indicator" ... />
```
`TjSlot` 只认 `name`，`slot-name` 不生效。修为 `name="monitor.workpiece-flow.indicator"`，并补默认 slot（RFC11 横幅）或保持空默认。

---

## 六、宿主接入改造（每页一处，零差异）

每个页面把"原主体根节点"包进 TjSlot 默认 slot：
```vue
<TjSlot name="project.layout.body" v-bind="pageCtx">
  <!-- 原 Project 页全部模板搬进来作默认内容 -->
</TjSlot>
```
- `pageCtx` = computed/ref 组装的 §4 props。
- 未注册插件 slot → 渲染默认内容 = 现状字节级一致。
- 注册了 → 渲染插件组件，`v-bind="$attrs"` 透传 ctx。

> 注意：部分页面（Settings/Project）已在内部用 `<TjSlot name="settings.tab.*">`；整页覆盖在更外层，两者不冲突（整页覆盖优先级更高，插件若整页覆盖则自行决定是否再渲染 tab slot）。

---

## 七、插件侧用法

```js
// register({ host, registry })
import MonitorPage from "./pages/monitor.js"; // 导出 Vue 组件 (h() 渲染)
registry.slots.register("monitor.layout.body", MonitorPage);
registry.slots.register("project.layout.body", ProjectPage);
// ... 8 页全注册 ...
```
- 组件用 `host.vue`（`h` / `defineComponent` / `ref` / `computed`）写，**无需构建工具链**（与福建金龙 `index.esm.js` 一致）。
- 数据：props 取实时/前置，`host.api` 取 CRUD，`host.echarts` 画图。
- 样式：`theme.css`（建议 `.tjsc-` 等前缀避免污染）。

---

## 八、错误隔离与回退

- 复用现有：`<component :is="pluginComponent">` 抛错 → `main.js` 注册的 `app.config.errorHandler` 捕获。
- 建议（非阻塞）：给 `*.layout.body` 宿主块包 `wrapPluginView` 式 `onErrorCaptured`，组件级 fallback 显示"插件此页出错"，不影响其它页。本 RFC 先复用全局 errorHandler，wrapView 作为后续增强。

---

## 九、影响与风险

- **8 个主程序页面文件各加一层 TjSlot 包裹**：纯加法，未注册插件时零差异，但需逐页冒烟确认默认渲染不变。
- **Monitor 解限**：改动 gate + Toast 叠层逻辑，需双工位 / 单工位 / 四工位三场景回归。
- **契约冻结**：props 一旦发布客户依赖，后续只能加不能删。

---

## 十、验收

- 未装插件：8 页 DOM / 行为与改造前一致（截图 diff / e2e）。
- 装展会插件：8 页全部走插件 UI，功能必保清单逐项通过。
- 插件单页抛错：仅该页 fallback，其它页可用。

---

**最后更新**：实施中（v3.16 目标）
