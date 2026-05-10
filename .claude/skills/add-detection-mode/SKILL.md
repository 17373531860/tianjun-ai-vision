---
name: add-detection-mode
description: "新增检测模式的完整流程：状态机扩展、set_project_config解析、前端Project配置UI、Monitor显示逻辑。当需要添加新的检测判定逻辑时使用。"
argument-hint: "[新检测模式的描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, Edit, Write, mcp__context7, mcp__sequential-thinking"
---

# add-detection-mode: 新增检测模式

你正在帮用户为天军AI视觉检测系统添加新的检测模式。

需求: $ARGUMENTS

## 现有4种模式的实现位置

| 模式 | pipeline_config.logic_mode | source.py 关键代码 |
|------|---------------------------|-------------------|
| 顺序 | `sequential` | current_step_index 递增, _get_expected_sequence_labels() |
| 检测 | `detection` | 无序检查 current_cycle_steps 完成度 |
| 自定义 | `custom` | custom_conditions 优先级匹配 + base_mode |
| 追踪 | `tracking` | tracked_items 字典, ID跟踪, container_mode |

## 新增检测模式需要修改的位置（全链路）

### 1. 后端 source.py — 状态机实现

**位置:** `VideoSourceManager` 类中

**a. set_project_config() — 配置解析**
搜索 `logic_mode` 在 set_project_config 中的处理:
```python
# 需要添加新模式的配置解析
if self.logic_mode == 'new_mode':
    self.new_mode_param = pipeline_config.get('new_mode_param', default_value)
```

**b. _init_inference_vars() — 状态变量初始化**
```python
# 必须在这里初始化新模式需要的所有状态变量
self.new_mode_state = None
```

**c. 检测循环中的分支 — 核心逻辑**
搜索现有模式的分支判断（如 `if self.logic_mode == 'sequential'`），在同层级添加新分支:
```python
elif self.logic_mode == 'new_mode':
    # 实现新模式的步骤判定逻辑
    pass
```

**d. start_cycle() / end_cycle() — 周期管理**
如果新模式有特殊的周期开始/结束条件，需要修改这两个方法。

**e. record_step() — 步骤记录**
如果步骤记录格式有差异，可能需要修改。

### 2. 后端 models.py — 数据模型（通常不需要改）

如果新模式需要存储额外数据，可能需要:
- DetectionCycle 添加新字段
- StepRecord 添加新字段
- 对应的 migration 在 main.py:migrate_database()

### 3. 前端 Project/index.vue — 配置UI

**位置:** 逻辑设置 tab (`Logic Settings`)

**a. 模式选择器**
搜索现有模式的选项列表（如 `sequential`, `detection`, `custom`, `tracking`），添加新选项。

**b. 模式特有配置面板**
每种模式有独立的配置区域。添加新模式的配置面板:
```vue
<div v-if="project.pipeline_config.logic_mode === 'new_mode'">
  <!-- 新模式的配置字段 -->
</div>
```

**c. 步骤表格列**
如果新模式需要步骤级别的特殊配置，需要在步骤表格中添加列。
搜索 `tracking` 相关的条件列渲染，参考其模式。

### 4. 前端 Navbar.vue — 默认值

在 `handleProjectChange()` 中添加新模式的 pipeline_config 默认值:
```javascript
if (!project.pipeline_config.new_mode_param) {
    project.pipeline_config.new_mode_param = defaultValue
}
```

### 5. 前端 Monitor/index.vue — 显示逻辑

**a. SOP 卡片显示**
不同模式可能有不同的 SOP 卡片渲染逻辑。搜索 `logic_mode` 在 Monitor 中的使用。

**b. 计数器和统计**
如果新模式有特殊的计数逻辑，需要在统计面板中处理。

**c. 检测结果轮询**
`getDetectionResults` 返回的数据结构如果变化，需要在轮询回调中处理。

### 6. 前端 locales/zh-CN.js — 翻译

添加新模式的显示名称（虽然大部分硬编码中文，但最好也加）。

## 检查清单

- [ ] source.py: set_project_config() 添加配置解析
- [ ] source.py: _init_inference_vars() 添加状态变量初始化
- [ ] source.py: 检测循环添加新模式分支
- [ ] source.py: start_cycle/end_cycle 适配（如需要）
- [ ] Project/index.vue: 模式选择器添加选项
- [ ] Project/index.vue: 新模式配置面板
- [ ] Project/index.vue: 步骤表格列适配（如需要）
- [ ] Navbar.vue: handleProjectChange 默认值
- [ ] Monitor/index.vue: 显示逻辑适配
- [ ] models.py + migration（如需要新DB字段）
- [ ] 旧项目兼容性（缺少新配置时不崩溃）

## 风险提醒

- source.py 的检测循环是**多线程热路径**，新增分支要注意线程安全
- 新增状态变量**必须**加入 _init_inference_vars()，否则 reset 时残留
- pipeline_config 是 JSON 字段，旧项目没有新 key，解析时必须有默认值
- set_project_config 被 hotfix.py 和 patch 文件可能间接调用
