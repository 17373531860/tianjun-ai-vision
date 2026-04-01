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
          ├── Navbar.vue ← useProjectStore, useSystemStore, api/project
          │   ├── 项目选择器 → handleProjectChange → activateProject + setProjectConfig
          │   ├── 自动恢复 → localStorage + 动态import api/index, store/useSourceStore
          │   └── 报警自动连接 → api (直接axios)
          ├── router-view (页面内容)
          │   ├── Monitor/index.vue ← useProjectStore, useSystemStore, useSourceStore, api/detection, api/model
          │   ├── Project/index.vue ← api/project, api/model, useProjectStore, useSystemStore
          │   ├── Source/index.vue ← api/index, api/detection, api/model, api/project, useSourceStore, useSystemStore
          │   ├── Data/index.vue ← api/data, api/detection, useProjectStore, useSystemStore
          │   ├── Report/index.vue ← api/report, api/project
          │   ├── Model/index.vue ← api/model
          │   ├── Alarm/index.vue ← api/index, api/project, useProjectStore, useSystemStore
          │   ├── Settings/index.vue ← useSystemStore
          │   └── Activation/index.vue ← window.electronAPI
          └── BottomBar.vue ← useSystemStore, useProjectStore
```

## Store 数据流

### useProjectStore → 消费者
- **Navbar.vue:** 写入 currentProject, currentProjectId
- **Monitor/index.vue:** 读取 currentProject (步骤配置、检测参数)
- **Data/index.vue:** 读取 currentProjectId (筛选数据)
- **BottomBar.vue:** 读取 isRunning
- **layout/index.vue:** 无直接依赖

### useSystemStore → 消费者
- **Settings/index.vue:** 读写 display, detection, performance
- **Navbar.vue:** 读写 display (自动恢复时直接覆盖!)
- **Monitor/index.vue:** 读取 detection (检测框、Toast、语音配置)
- **layout/index.vue:** 读取 isDetecting (导航锁)
- **BottomBar.vue:** 读取各显示设置
- **Project/index.vue:** loadDetectionFromProject (合并项目检测配置)

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
