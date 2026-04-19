---
name: modify-project-config
description: "安全修改项目配置结构：步骤/逻辑/事件/计数器的完整数据流。项目配置从前端Project页→后端API→source.py状态机→DB→前端Monitor页，修改任何一环都需要全链路分析。"
argument-hint: "[要修改的配置项]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# modify-project-config: 项目配置安全修改分析

你正在帮用户安全修改项目配置结构。项目配置是**贯穿整个系统的核心数据流**。

计划修改: $ARGUMENTS

## 配置数据流全景

```
前端 Project/index.vue (编辑)
  → updateProject API (保存到DB)
    → Project 模型的 JSON 字段
      → steps_config, events_config, counters_config
      → alarm_config, detection_config, data_config
      → pipeline_config

前端 Navbar.vue (切换项目)
  → activateProject → getProjectDetail
    → handleProjectChange() 解析配置
      → setProjectConfig API → source.py
        → VideoSourceManager.set_project_config()
          → 解析到 ~50个 内部变量
          → 控制检测状态机行为

前端 Monitor/index.vue (使用配置)
  → 读取 projectStore.currentProject
  → 渲染 SOP 卡片、计数器、事件列表
  → startDetection 时发送配置到后端
```

## 7个JSON配置字段详解

### 1. steps_config (步骤配置)
```json
[
  {
    "label": "step1",           // YOLO标签名
    "display_name": "第一步",    // 显示名称
    "enabled": true,
    "threshold": 0.5,           // 单步置信度阈值
    "min_duration": 0,          // 最短持续时间(秒)
    "max_duration": 0,          // 最长持续时间(秒), 0=不限
    "timeout_ng": false,        // 超时是否判NG
    "dedup_interval": 2,        // 去重间隔(秒)
    "disappear_delay": 1,       // 消失确认延迟(秒)
    "min_frames": 3,            // 最小确认帧数
    "detection_type": "static", // static/dynamic
    "backup_step": "",          // 替代步骤标签
    "strict_order": false,      // 严格顺序
    "accept_once": true,        // 只接受一次
    // 追踪模式专属:
    "lost_seconds": 5,
    "position_lock": false,
    "count_mode": "total",
    "event_count": 0,
    "gone_frames": 30,
    // v2.7.4 新增字段:
    "hide_in_view": false,            // [前端可视化] 实时画面/SOP/步骤详情都不显示该 label，但后端检测/计数/报警/CSV 全部正常
    "stack_enabled": false,           // [仅 count_mode=track] 堆叠模式开关
    "stack_reappear_seconds": 1.0,    // 堆叠模式：消失 ≥N 秒后再现算下一层
    "stack_required_count": 2,        // 堆叠模式：期望层数（最小 2）
    "max_recognized": 0               // [仅 count_mode=track] 同时最多识别几个该物品；0=无上限；超出按距离归并到 Top-N(置信度) 的 track_id
  }
]
```

**解析点:** `source.py: set_project_config()` → 映射到 `self.step_configs`, `self.step_sequence` 等

**v2.7.4 三个新字段的处理位置：**
- `hide_in_view`: **纯前端**，后端不读，仅在 `frontend/src/views/Monitor/index.vue` 的 `drawDetections / drawMultiDetections / stepsToShow / tableData` 过滤
- `stack_enabled / stack_reappear_seconds / stack_required_count`: 在 `_update_tracking_stats` 的步骤循环里解析为 `stack_steps[label]`，独立状态机维护 `_stack_state / _stack_counters / _stack_disappeared_at / _stack_visible_frames`；最终通过 `_rebuild_checklist` 用 `max(tracking_class_counters, stack_counters)` 合并
- `max_recognized`: 在 `_update_tracking_stats` 入口处对 detections 做 ID 合并（按置信度选 keeper + 最近距离归并），不动 ByteTrack 内部状态

### 2. pipeline_config (检测管线配置)
```json
{
  "logic_mode": "sequential",    // sequential/detection/custom/tracking
  "custom_conditions": [...],    // 自定义模式条件
  "cycle_end_strategy": "all_done", // 追踪模式
  "roi_config": {...},           // ROI区域
  "trigger_labels": [...],       // 追踪触发标签
  "expected_counts": {...},      // 期望计数
  "container_mode": false,       // 容器模式
  "shift_split_enabled": false,  // 班次分割
  "shift_split_time": "08:00"
}
```

**解析点:** `source.py: set_project_config()` → `self.logic_mode`, `self.custom_conditions` 等

### 3. counters_config (计数器配置)
```json
{
  "total": {"visible": true, "label": "总数"},
  "ok": {"visible": true, "label": "OK"},
  "ng": {"visible": true, "label": "NG"},
  "consecutive_ng": {"visible": true, "label": "连续NG"},
  "custom_counter_1": {"visible": false, "label": "自定义1"}
}
```

### 4. events_config (事件配置)
```json
[
  {
    "id": "event_ok",
    "name": "OK事件",
    "trigger": "cycle_ok",
    "counter_action": {"counter": "ok", "action": "increment"},
    "toast": "ok",
    "notification": true
  }
]
```

### 5. detection_config (检测参数)
```json
{
  "conf_threshold": 0.25,
  "iou_threshold": 0.45,
  "max_det": 300
}
```

### 6. alarm_config (报警配置)
```json
{
  "port": "/dev/ttyUSB0",
  "baudrate": 9600,
  "protocol": "modbus_4color",
  "events": {...}
}
```

### 7. data_config (数据记录配置)
```json
{
  "record_video": true,
  "record_images": false,
  "retention_days": 30
}
```

## 强制分析流程

### 第1步: 确认修改的配置项

1. 确认属于哪个 JSON 字段
2. 读取当前的 JSON 结构
3. 确认修改类型: 新增key / 修改类型 / 删除key / 改嵌套结构

### 第2步: 追踪全链路

**必须检查以下全部位置:**

| 位置 | 文件 | 作用 |
|------|------|------|
| 编辑UI | `Project/index.vue` | 用户编辑配置 |
| 保存API | `projects.py: update_project` | 写入DB |
| DB模型 | `models.py: Project` | 存储 |
| 项目切换 | `Navbar.vue: handleProjectChange` | 解析+填充默认值 |
| 发送到引擎 | `detection.js: setProjectConfig` | 前端→后端 |
| 引擎解析 | `source.py: set_project_config` | 解析到状态变量 |
| 检测使用 | `source.py: _capture_loop` 等 | 运行时读取 |
| 结果显示 | `Monitor/index.vue` | 渲染UI |

### 第3步: 检查默认值处理

**关键:** Navbar.vue 的 `handleProjectChange()` 中有大量默认值填充:
```javascript
// 如果项目没有某个配置，会用默认值填充
if (!project.steps_config) project.steps_config = []
if (!project.pipeline_config) project.pipeline_config = { logic_mode: 'sequential' }
```

新增配置项必须在此处添加默认值，否则旧项目数据加载时会缺失。

### 第4步: 检查 set_project_config 解析

`source.py: set_project_config()` 是配置解析的核心。
搜索你要修改的配置 key 在该函数中的处理逻辑。
如果是新增 key，需要在此函数中添加解析。

### 第5步: 生成影响报告

```
修改的配置: [JSON字段.key]
Project/index.vue: [编辑UI需要的修改]
Navbar.vue: [默认值需要的修改]
source.py set_project_config: [解析逻辑需要的修改]
source.py 运行时: [使用逻辑需要的修改]
Monitor/index.vue: [显示逻辑需要的修改]
旧数据兼容: [旧项目缺少该字段时的处理]
```

## 修改原则

1. **新增 key 必须向后兼容:** 旧项目没有该 key 时不能崩溃
2. **默认值在 3 处同步:** Navbar默认值 + set_project_config默认值 + 前端编辑UI默认值
3. **不删除 key:** 保留旧 key 不会有副作用，删除可能导致旧数据崩溃
4. **JSON 类型安全:** Python 端 json.loads 后要做类型检查
5. **全链路测试:** 创建新项目 → 编辑配置 → 切换项目 → 启动检测 → 查看结果
