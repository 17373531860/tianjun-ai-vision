---
name: add-event-type
description: "新增事件类型的完整流程：后端事件定义和触发、报警联动、前端Toast显示、语音播报。当需要添加新的检测事件（如自定义OK/NG/警告）时使用。"
argument-hint: "[新事件类型描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, Edit, Write, mcp__context7"
---

# add-event-type: 新增事件类型

你正在帮用户为天军AI视觉检测系统添加新的事件类型。

需求: $ARGUMENTS

## 事件系统架构

```
source.py (检测到事件)
  → 写入 events 列表 (内存)
  → 触发报警 POST /alarm/trigger/{event_type}
  → 更新计数器 counters

Monitor/index.vue (轮询获取)
  ← getDetectionResults 返回 events 列表
  → 匹配 Toast 配置 → 显示动画
  → 匹配语音配置 → TTS 播报
  → shownEventIds 防重复
```

## 事件配置结构 (Project.events_config)

```json
[
  {
    "id": "event_ok",                // 唯一ID
    "name": "OK",                    // 事件名称
    "trigger": "cycle_ok",           // 触发条件
    "counter_action": {              // 计数器操作
      "counter": "ok",              // 计数器名
      "action": "increment"         // increment/decrement/reset
    },
    "toast": "ok",                   // Toast类型: ok/ng/自定义ID
    "notification": true,            // 是否通知
    "alarm_trigger": {               // 报警配置(可选)
      "color": "green",
      "effect": "on",
      "buzzer": false,
      "duration": 3
    }
  }
]
```

## 新增事件类型需要修改的位置

### 1. 后端 source.py — 事件触发

**a. 定义新的触发条件:**
搜索现有触发条件（如 `cycle_ok`, `cycle_ng`, `step_complete`），在对应位置添加新触发:

```python
# 在检测逻辑中的适当位置
event = {
    'id': f'event_{timestamp}',
    'type': 'new_event_type',
    'timestamp': time.time(),
    'data': { ... }  # 事件关联数据
}
self.events.append(event)
```

**b. 计数器更新:**
如果事件关联计数器操作:
```python
if event_config.get('counter_action'):
    counter = event_config['counter_action']['counter']
    action = event_config['counter_action']['action']
    if action == 'increment':
        self.counters[counter] = self.counters.get(counter, 0) + 1
```

**c. 报警触发:**
```python
if event_config.get('alarm_trigger'):
    # POST /alarm/trigger/{event_type}
    requests.post(f'http://localhost:8001/api/v1/alarm/trigger/{event_type}',
                  json=event_config['alarm_trigger'])
```

### 2. 前端 Project/index.vue — 事件配置UI

**位置:** Events tab

**a. 触发条件选项:**
搜索触发条件的下拉选项列表，添加新触发条件。

**b. 事件编辑表单:**
如果新事件有特殊配置字段，在事件编辑区添加。

### 3. 前端 Monitor/index.vue — Toast 显示

**a. Toast 匹配:**
Monitor 轮询到新事件后，匹配 systemStore 中的 Toast 配置:
```javascript
// systemStore.detection.toasts.systemPresets: { ok: {...}, ng: {...} }
// systemStore.detection.toasts.customToasts: [{ id, color, text, ... }]
```

如果新事件需要专门的 Toast 样式:
- 系统预设: 在 `systemStore` 的 `toasts.systemPresets` 中添加
- 或让用户在 Settings 页自定义

**b. 语音播报:**
```javascript
// systemStore.detection.voice
if (voice.enabled && voice[eventType]) {
    speak(voice[eventType].text)
}
```

### 4. 前端 Settings/index.vue — Toast 预设

如果需要为新事件添加系统级 Toast 预设:
- Detection Box Settings tab → Toast 配置区域
- 添加新的系统预设卡片

### 5. 前端 Alarm/index.vue — 报警联动

如果需要在报警页面配置新事件的报警行为:
- 事件触发配置区域
- 为新事件添加灯色/效果/蜂鸣器配置

## 计数器系统

### 内置计数器
- `total` — 总周期数
- `ok` — OK周期数
- `ng` — NG周期数
- `consecutive_ng` — 连续NG数

### 自定义计数器
通过 `counters_config` 配置:
```json
{
  "custom_1": {"visible": true, "label": "自定义计数器1"}
}
```

### 计数器操作
事件可配置以下计数器操作:
- `increment` — 加1
- `decrement` — 减1
- `reset` — 归零
- `set` — 设为指定值

## 检查清单

- [ ] source.py: 事件触发逻辑（写入 self.events）
- [ ] source.py: 计数器更新（如关联计数器）
- [ ] source.py: 报警触发（如关联报警）
- [ ] Project/index.vue: 触发条件选项
- [ ] Project/index.vue: 事件配置表单
- [ ] Monitor/index.vue: Toast 匹配和显示
- [ ] Monitor/index.vue: 语音播报（如需要）
- [ ] Settings/index.vue: Toast 预设（如需要）
- [ ] Alarm/index.vue: 报警联动配置（如需要）
- [ ] counters_config 默认值（Navbar.vue handleProjectChange）

## 注意事项

- events 列表在内存中，重启丢失（这是设计如此，事件是瞬态的）
- Monitor 通过 `shownEventIds` Set 防止重复显示，超500条时清理
- 报警触发是 HTTP 调用而非直接函数调用（解耦）
- 语音 TTS 有 Chromium bug：cancel() 后需要 100ms 延迟再 speak()
