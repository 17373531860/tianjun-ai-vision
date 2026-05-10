---
name: modify-project-config
description: "安全修改项目配置结构：pipeline_config / steps_config / events_config 等 7 个 JSON 字段的全链路影响分析。配置从前端 Project 页 → POST /projects → DB → 激活后 set_project_config → VSM 状态机 → Monitor 展示，改任一环都要全链路对齐。"
argument-hint: "[要修改的配置项]"
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__context7, mcp__sequential-thinking"
---

# modify-project-config: 项目配置安全修改分析（v3.5.x）

你正在帮用户安全修改项目配置结构。Project 是天军系统的"客户脚本"，
**贯穿前端编辑 → DB → 后端 VSM 状态机 → Monitor 显示**的核心数据流。
任何一环对不上都会出现"配了但不生效 / 切项目残留 / 老项目崩溃"。

计划修改: $ARGUMENTS

---

## 一、Project 的 7 个 JSON 字段（v3.5.x 现状）

**ORM** (`backend/models/models.py: Project`)

```python
class Project(Base):
    __tablename__ = "projects"
    id, name, task_type, default_model_id, model_format, is_active
    logic_mode      = Column(String, default="sequential")   # 顶层列, 不在 JSON 里!
    pipeline_config = Column(JSON)   # 管线/步骤序列/容器/周期性强制动作
    steps_config    = Column(JSON)   # 每步配置（label/阈值/帧/disappear_delay 等）
    events_config   = Column(JSON)   # 事件定义（OK/NG/自定义）
    counters_config = Column(JSON)
    alarm_config    = Column(JSON)
    detection_config= Column(JSON)
    data_config     = Column(JSON)
```

**Pydantic Schema** (`backend/schemas/project.py`)：`ProjectBase / ProjectCreate / ProjectUpdate / ProjectResponse`，
新增 JSON 顶层字段时 4 个 schema 都要加。

---

### 1. pipeline_config（管线 + 周期性动作 + 容器 + 误判过滤）

```jsonc
{
  // 顺序模式
  "sequence_order": ["step1", "step2", "step3"],
  // 检测模式
  "detection_steps": [...],
  // 自定义模式
  "custom_based_on": "sequential|detection|null",
  "custom_conditions": [
    { "id": 1, "priority": 1, "sequence": ["a","b"], "event_id": 3 }
  ],
  "custom_sequence_order": [...],
  "custom_detection_steps": [...],

  // 结算 / 周期超时（apply 在 _apply_pipeline_config）
  "settlement_mode": "first_step|last_step",
  "idle_timeout_seconds": 0,
  "cycle_max_duration": 0,
  "ng_cycle_protect_seconds": 0,
  "settle_dedup": false,
  "accumulate_repeats": false,
  "simultaneous_groups": [...],

  // tracking 模式
  "tracking_cycle_strategy": "all_gone|container",
  "tracking_container_label": "",
  "container_box_mode": "single",
  "container_settle_min_items": 1,         // v3.1.4
  "container_id_drift_merge_iou": 0,       // v3.2.0
  "container_id_drift_merge_max_gone_frames": 30,
  "tracking_trigger_label": "",
  "tracking_match_thresh": 0.8,
  "tracking_check_order": false,
  "tracking_swap_detection": false,
  "tracking_appearance_match": false,
  "tracking_id_lock": false,
  "tracking_id_lock_frames": 15,
  "tracking_roi": { "enabled": true, "polygon": [...] },
  "counting_expected_items": { "label1": 2 },

  // 误判过滤（v2.7.8 起走 pipeline_config，apply 在 _apply_rod_filter）
  "rod_companion_filter": { "enabled": false, "iou_threshold": 0.25,
                            "rod_label": "", "companion_labels": [] },
  "rod_session_gate":     { "enabled": false, "rod_label": "", "gate_labels": [] },

  // ★ v3.5.0 新增：周期性强制动作（每 N 轮做 E）
  // ★ v3.5.2 新增：每条规则的 run_on_start（开机首检）
  "periodic_actions": [
    {
      "id": "pa_1700000000_0",
      "name": "每20件清洁治具",
      "enabled": true,
      "trigger_step_ids": [3, 5],
      "interval": 20,
      "count_basis": "all|good_only|ng_only",
      "reset_policy": "always|only_when_due",
      "due_warning_event_id": 4,
      "overdue_event_id": 5,
      "overdue_repeat": "every_cycle|once|cooldown:N",
      "channel_filter": [0, 1],
      "run_on_start": false                   // v3.5.2
    }
  ]
}
```

### 2. steps_config（每步配置）

```jsonc
[
  {
    "id": 1,
    "label": "step1",            // YOLO 标签名
    "displayLabel": "第一步",     // 别名 display_name 也兼容
    "enabled": true,
    "threshold": 50,             // ★ 前端发百分比, _apply_steps_config 自动 / 100
    "min_duration": 0,
    "max_duration": 0,
    "max_interval": 1.0,
    "disappear_delay": 0,
    "timeout_ng": false,
    "min_frames": 1,
    "gap_tolerance": 0,
    "strict_order": false,
    "accept_once": false,
    "detection_type": "static|dynamic",
    "static_trigger_frames": 30,
    "join_cycle": true,
    "triggerEvent": null,        // 静态步骤被触发后弹的 event_id
    "backup_for": null,          // primary step 的 id（替补步骤）
    // 追踪模式
    "count_mode": "track|event|total",
    "event_required_count": 1,
    "event_gone_frames": 8,
    "tracking_gone_confirm_frames": null,
    "tracking_max_lost_seconds": 5.0,
    "tracking_position_lock": false,
    // v2.7.4
    "hide_in_view": false,
    "stack_enabled": false,
    "stack_reappear_seconds": 1.0,
    "stack_required_count": 2,
    "max_recognized": 0
  }
]
```

`enabled=false` 的步骤会**整体被 `_apply_steps_config` 跳过**。

### 3. events_config（事件定义）

```jsonc
[
  { "id": 1, "name": "合格(OK)", "color": "#10b981",
    "actions": [{ "counter_name": "合格总数", "delta": 1 },
                { "counter_name": "总产量",   "delta": 1 }],
    "show_notification": true, "toast_id": "ok" },
  { "id": 2, "name": "不良(NG)", "color": "#ef4444",
    "actions": [{ "counter_name": "不良总数", "delta": 1 },
                { "counter_name": "总产量",   "delta": 1 }],
    "show_notification": true, "toast_id": "ng" },
  // v3.5.0 起：可加自定义事件，被 periodic_actions / custom_conditions / 静态步骤
  // 通过 id 引用；触发走同一条 _trigger_event 链路。
  { "id": 4, "name": "保养到期", "color": "#f59e0b",
    "actions": [], "show_notification": true, "toast_id": "ng" }
]
```

`_trigger_event` (`source_event_trigger_mixin.py`) 同时支持数字 id（`1`/`2`/`4`）
和字符串 id（`'event_1'`），逐项匹配 `events_config[*].id`。
`actions[*]` 同时兼容 `delta` 和 `value` 两种字段名。

### 4-7. 其他四个 JSON

- **counters_config**: `[{name, value}]`（apply 在 `_apply_counters`，写盘到
  `DATA_DIR/counters/project_{id}_ch{ch}.json`）
- **alarm_config**: 串口/协议/triggers，由 Navbar 切项目时 `POST /alarm/config` + `/alarm/connect` 自动应用
- **detection_config**: 检测框样式 / Toast / 语音；进 `useSourceStore.loadDetectionFromProject`
- **data_config**: 数据导出开关 + 班次拆分（`shift_split_enabled / day_shift_start / night_shift_start`）

---

## 二、配置数据流全链路（v3.5.x）

```
┌─ 前端 Project/index.vue (2925 行) ───────────────────┐
│  selectProject → getProjectDetail                    │
│  initProjectDefaults(project)   ← 必填默认值 + 把     │
│      pipeline_config 解包到 activeProject 顶层       │
│  handleSaveProject               ← 把顶层重新塞回    │
│      pipeline_config + data_config 再 PUT /projects  │
└──────┬───────────────────────────────────────────────┘
       │  PUT  /api/v1/projects/{id}
       ▼
┌─ backend/api/projects.py ────────────────────────────┐
│  update_project: setattr 到 ORM, db.commit           │
│  POST /projects/{id}/activate                        │
│    → 写 is_active + _reload_model_for_active_project │
│    → 清各通道 MES pending（避免旧码混入新项目）        │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  Navbar.handleProjectChange
┌─ frontend/src/layout/Navbar.vue ─────────────────────┐
│  activateProject + getProjectDetail                  │
│  填默认值（counters/steps/events/pipeline_config）    │
│  从 pipeline_config 解包 sequence_order/...到顶层    │
│  projectStore.setCurrentProject(project)             │
│  store.loadDetectionFromProject(project.detection_*) │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  Monitor.startDetection → syncProjectConfig
┌─ POST /api/v1/source/detection/set-project?channel=N─┐
│  ProjectConfigRequest 仅取 7 个 JSON 中 5 个          │
│  (steps/pipeline/events/counters/data) + logic_mode  │
└──────┬───────────────────────────────────────────────┘
       │
       ▼
┌─ backend/api/source.py: VSM.set_project_config ──────┐
│  薄 wrapper → apply_project_config(self, config) 在  │
│  source_project_config_apply.py (P7 第十一刀):       │
│    _apply_rod_filter           (rod_filter.py)       │
│    _reset_step_state_dicts                           │
│    _apply_steps_config         (threshold/100, 帧, 静态)│
│    _apply_backup_steps         (backup_for → 映射)    │
│    _apply_pipeline_config      (settlement / 超时)    │
│    _apply_counters             (默认 4 计数器 + 持久化)│
│    _reset_cycle_state                                │
│    _apply_tracking_mode        (tracker yaml + 容器)  │
│    _apply_periodic_actions ★   (v3.5.0)              │
│    _print_summary                                    │
└──────┬───────────────────────────────────────────────┘
       │  扁平 setattr → host (VideoSourceManager)
       ▼
┌─ 运行期消费点 ───────────────────────────────────────┐
│  source_inference_loop_mixin.py: 读 logic_mode       │
│  source_events_check_mixin.py:   按模式分发 OK/NG     │
│  source_step_stats_mixin.py:     调 _check_periodic_ │
│      actions_on_first_step (v3.5.2 开机首检)         │
│  source_session_lifecycle_mixin.py: cycle_end 后调   │
│      _check_periodic_actions(cycle_steps, is_good)   │
│  source_lifecycle_mixin.py: start_detection 时调     │
│      _run_periodic_actions_on_start (v3.5.2)         │
│  source_drawer.py / settlement_mixin.py / ...        │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  GET /api/v1/source/detection/results?channel=N
┌─ 前端 Monitor/index.vue (4051 行) ───────────────────┐
│  pollDetectionResults → 写 stats / events / counters │
│  data.periodic_actions → periodicActions ref         │
│  渲染 SOP 卡片 / 计数器 / Tracking 进度 / 周期性强制  │
│  动作进度条（ok/due/overdue 三态）                    │
└──────────────────────────────────────────────────────┘
```

> 注意：set-project 路由的 `ProjectConfigRequest` **只发送 5 个 JSON**（`alarm_config / detection_config` 不发），
> 因为这两个由前端独立的 alarm.connect / sourceStore 处理，**不进 VSM 状态机**。

---

## 三、修改前必跑的 grep 矩阵

| 检查点 | 命令（按 key 名 grep）|
|---|---|
| 后端 apply 解析 | `rg 'config.get\(.<KEY>.\)' backend/api/source_project_config_apply.py backend/api/source_*_mixin.py backend/api/rod_filter.py` |
| 后端运行期读取 | `rg 'project_config.get\(.pipeline_config.\)\.get\(.<KEY>.\)' backend/` |
| Schema 是否漏 | `rg '<KEY>' backend/schemas/project.py` |
| Source route 透传 | `rg '<KEY>' backend/api/source_routes.py` |
| 前端默认值 | `rg '<KEY>' frontend/src/views/Project/index.vue` （看 initProjectDefaults + handleSaveProject）|
| 前端 Navbar 切换 | `rg '<KEY>' frontend/src/layout/Navbar.vue` |
| 前端 Monitor 显示 | `rg '<KEY>' frontend/src/views/Monitor/index.vue` |
| Monitor 同步入口 | `rg 'syncProjectConfig' frontend/src/views/Monitor/index.vue -n` |
| ORM Column | `rg '<KEY>' backend/models/models.py` |
| reset_stats 清零 | `rg '<状态变量>' backend/api/source.py`（看 reset_stats） |
| 持久化 | `rg 'DATA_DIR.*counters' backend/api/source_*.py` |

---

## 四、强制分析流程

### 第 1 步：确认归属

- 在哪个 JSON 字段下？（pipeline_config / steps_config / events_config / ...）
- 修改类型：新增 key / 改类型 / 改嵌套 / 删 key
- **跨字段引用**？（典型：`periodic_actions[*].due_warning_event_id` → `events_config[*].id`，
  `custom_conditions[*].event_id` → `events_config[*].id`，
  `step.triggerEvent` → `events_config[*].id`，
  `step.backup_for` → 另一 step 的 `id`）

### 第 2 步：跑 grep 矩阵确认全链路所有触点

新增 key 必须在下列**全部**位置闭环：

| 位置 | 文件 | 缺一会怎样 |
|---|---|---|
| ORM JSON Column | `models/models.py` | 已是 JSON Column 通常无需改；新增**顶层列**才需要加 + 写 ALTER TABLE 到 `backend/main.py: migrate_database()` |
| Pydantic Schema | `schemas/project.py`（4 处）| PUT /projects 验证不过 |
| Project 路由透传 | `api/projects.py`（5 处构造 ProjectResponse）| GET 拿不到字段 |
| set-project 路由 schema | `api/source_routes.py: ProjectConfigRequest` | Monitor 同步时丢字段 |
| set_project_config 解析 | `api/source_project_config_apply.py: apply_project_config` 或对应 mixin（如 `source_periodic_actions_mixin.py`）| 后端**完全不读** |
| 状态变量初始化 | `api/source_state_init.py` | reset_stats / 切项目时找不到属性 |
| reset_stats 清零 | `api/source.py: reset_stats` | 切项目残留旧数值 |
| 持久化（如有）| `DATA_DIR/counters/...` | 重启丢状态 |
| 前端 initProjectDefaults | `views/Project/index.vue`（line ~1971）| 老项目打开报错 / 字段消失 |
| 前端 handleSaveProject | `views/Project/index.vue`（line ~2273+）pipeline_config 组装段 | 编辑后**保存不上** |
| 前端 Navbar handleProjectChange | `layout/Navbar.vue`（line ~284）| 切项目后字段没解包到顶层 |
| 前端 syncProjectConfig | `views/Monitor/index.vue`（line ~2935）| 启动检测时字段没送到后端 |
| 前端 Monitor 渲染 | `views/Monitor/index.vue` 各 ref | UI 看不到 |
| 前端 Project UI | `views/Project/index.vue` 编辑组件 | 客户编不了 |

### 第 3 步：给出影响报告

```
修改的字段: pipeline_config.<KEY>
- 后端 apply: source_project_config_apply.py / source_*_mixin.py 改动点
- 后端 schema: schemas/project.py + source_routes.py: ProjectConfigRequest
- 后端运行期: 读取该字段的 mixin / executor
- VSM 状态: __init__ / state_init / reset_stats 三处
- 前端 Project: initProjectDefaults 默认值 + handleSaveProject 写回
- 前端 Navbar: handleProjectChange 默认值
- 前端 Monitor: syncProjectConfig 透传 + 渲染
- 持久化: DATA_DIR/counters/... 是否要落盘
- 老项目兼容: 缺 key 时 .get(key, default)
- 多通道: 是否要 channel_id / 是否要 channel_filter 限定
```

---

## 五、修改原则

1. **新增需求优先扩 JSON 而不是加 Column**（AGENTS.md 七节"关键扩展点"明示）。
2. **向后兼容**：所有 `.get(key, default)`，旧项目缺字段不能崩。
3. **默认值同步 4 处**：
   `initProjectDefaults` + `handleSaveProject` 写回 + `Navbar.handleProjectChange` + `apply_project_config`。
4. **threshold 单位**：前端百分比 (10-100)，后端 `_apply_steps_config` 自动 `/ 100`。**不要在前端手动除**。
5. **logic_mode 不在 JSON 里**：是顶层 String 列，新加值要在 `source_events_check_mixin.py` 的分发处加分支。
6. **跨字段引用 by id**：`events_config[*].id`、`steps_config[*].id`、`periodic_actions[*].id` 都是稳定字符串/整数，
   修改时不要重新分配 id，否则 `_trigger_event` 找不到。
7. **新状态变量必须进 reset_stats**（v3.5.2 `_periodic_counters / _run_on_start_pending` 就是这么补上的）。
8. **多通道**：状态变量都要 per-channel（`_periodic_counter_path` 拼 `ch{channel_id}`），
   切通道数（`channel_manager.set_channel_count`）必须考虑残留落盘文件。
9. **激活时副作用**：`POST /projects/{id}/activate` 会**重载模型 + 清 MES pending**；
   增加新副作用要加 try/except，**绝不让 activate 主流程失败**。
10. **set_project_config 不重置已激活的视频源 / 模型**：只重置步骤 / 周期 / 计数器状态。
    新加状态变量如属"周期内"语义，必须在 `_reset_cycle_state` 或 `_reset_step_state_dicts` 里清零。

---

## 六、v3.5.x 常见踩坑（必查）

| 坑 | 症状 | 根因 |
|---|---|---|
| 前端加字段没到后端解析 | 编辑保存成功但运行不生效 | `apply_project_config` 漏 key |
| 后端解析没存到 host | apply 日志 OK 但运行时 `getattr(self,...)` 报错 | 没 `setattr(h, key, value)` |
| 状态没在 reset_stats 重置 | 用户点"清零"后字段还有旧值 | 漏在 `source.py: reset_stats` 加清零逻辑 |
| 新字段在 pipeline_config 里读不到 | rod_filter v2.7.6 旧坑 | `_build_project_config` 没把字段抬到顶层（v2.7.8 已经统一从 pipeline_config 读，不要再走顶层） |
| 老项目打开 Project 页崩 | `Cannot read property of undefined` | `initProjectDefaults` 漏给默认值 |
| 编辑后保存不上 | PUT /projects 缺字段 | `handleSaveProject` 的 pipeline_config 组装段漏 |
| 切项目后字段还在 | Navbar 没解包 → 顶层旧值 | `handleProjectChange` 漏处理 |
| 启动检测后后端无配置 | Monitor 没透传 | `syncProjectConfig` 漏 key（pipeline_config 整体透传通常 OK，但如果**抬到了顶层**就要单独发） |
| periodic_actions 切项目残留 | 老项目计数没归零 | 切项目走 `apply_project_config`，会从 `DATA_DIR/counters/project_{id}_ch{ch}_periodic.json` **按 project_id 重新装**，确认文件路径用了对的 project_id |
| run_on_start 不弹 | 配置开了但开机首检静默 | `_run_periodic_actions_on_start` 在 `start_detection` / `resume_inference` 才调；`_check_periodic_actions_on_first_step` 必须在第一个步骤 disappear 时调（`source_step_stats_mixin.py`） |
| 自定义事件触发不出来 | events_config 里有但不响应 | 检查 `id` 是不是数字/字符串混用，`_trigger_event` 双向匹配，但有些调用点（如 `due_warning_event_id`）保存的是 number，配出来是 string 会失配 |
| Schema 校验失败 | `pipeline_config` 整体被丢 | `ProjectUpdate` Optional[dict] 是顶层，新加**顶层列**才要改 schema；JSON 内嵌 key 不需要 |
| MES Hook 错乱 | 切项目后旧 workpiece 串到新项目 | 已由 `activate_project` 调 `hook.clear_pending_scan` 处理；新增类似副作用要参考此处 |
| **顺序模式不触发 OK/NG（高频踩坑）** | 走完 step_a/b/c 三步，cycle 永远不结算，OK/NG 事件永不触发，counters 全 0 | `pipeline_config.sequence_order` **必填**。`source_sequential_mixin.py:_check_sequential_mode` 第 15-27 行：`if not sequence_order: return`，没配就直接早退、连 step 都不计数（settle 走的是别的路径，counters 触发依赖此函数）。诊断：后端日志找不到「顺序模式检查/结算: 期望=...」就是这个 |
| **结算模式默认 first_step、不是 last_step（高频踩坑）** | step_a 只出现一次的剧本永远不结算（"第一步再次出现才结算"是 first_step 模式语义） | `pipeline_config.settlement_mode` 默认是 `'first_step'`，意思「第一步**再次被检测到**」才触发结算。要"最后一步消失就结算"得显式配 `'last_step'`。诊断：后端日志看到「[第一步结算] [step_a] 再次检测到 ... 结算当前周期」=first_step 模式；看到 `_check_sequential_mode` 由 `_check_events(last_step)` 触发 = last_step 模式 |
| **activate ≠ set-project**（写测试时高频踩坑） | `POST /projects/{id}/activate` 后 mgr.events_config 为空，事件触发不到 | `activate_project` 只重载模型 + 写 DB `is_active=True`，**不会** push events/counters/steps 进 `mgr.project_config`。前端是靠 Monitor 启动时 `syncProjectConfig` → `POST /source/detection/set-project` 才把 5 个 JSON 推进 mgr。脚本测试要么走完整链路，要么手动 POST `/source/detection/set-project` |
| **synthetic with_project=True 用的是「最小项目」** | 跑 synthetic 想看 OK/NG 触发，结果 `_trigger_event` 找不到事件直接 return False | `test_runtime_routes.py:_build_min_project_config` 只有 `steps_config`，`events_config: []`，`pipeline_config: {}` (无 `sequence_order` 也无 `settlement_mode`)。**要触发事件**：先 `create_activate_push` 全 payload + `set-project`，再 `start_synth(with_project=False)` 让 mgr 沿用我们推的 config |

---

## 七、自检清单

修改完后逐条核对：

- [ ] 跑了上面 grep 矩阵，确认所有触点都改了
- [ ] `apply_project_config` / 对应 mixin 增加了 `.get(key, default)` 解析
- [ ] host 上的状态变量在 `source_state_init.py` 里有初始化
- [ ] `reset_stats` 里清零（如属周期内/会话内状态）
- [ ] `initProjectDefaults` + `handleSaveProject` 双向闭环
- [ ] `Navbar.handleProjectChange` 默认值（如果字段被解包到顶层）
- [ ] `Monitor.syncProjectConfig` 透传（不挂 pipeline_config 内部时才需要）
- [ ] 4 个 Pydantic schema 同步（仅顶层列改动）
- [ ] `projects.py` 5 处 ProjectResponse 构造同步（仅顶层列改动）
- [ ] 老项目打开 / 编辑 / 保存 / 激活 / 启动检测 / cycle 跑通一轮
- [ ] 多通道场景：每通道独立状态 / 持久化文件路径带 `ch{channel_id}`
- [ ] 跨字段 id 引用没断（`due_warning_event_id` ↔ `events_config[*].id` 等）
- [ ] lint 0 错误，启动后端无 traceback

---

## 八、UAT 校验项目配置生效的最短链路（合成验证）

写自动化测试或客户复现时验"配了一个 N 步顺序模式 + 自定义事件 + 计数器" 端到端确实跑通：

```python
# 1) 整个 payload（必含 sequence_order + last_step）
payload = {
    "name": "uat",
    "task_type": "detection",
    "logic_mode": "sequential",
    "pipeline_config": {
        "sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}],
        "settlement_mode": "last_step",   # 没这行，OK 永远不触发
    },
    "steps_config": [...],          # threshold 用 0-100，后端会 /100
    "events_config": [...],         # OK/NG/自定义都在这
    "counters_config": [...],       # 默认 4 个不会自动建，要列出来
    ...
}

# 2) 顺序：建 → 激活 → set-project（必须三步全做）
pid = POST /api/v1/projects                      json=payload
POST /api/v1/projects/{pid}/activate
POST /api/v1/source/detection/set-project?channel=0  json={...payload, project_id:pid}
# ↑ activate 不 push project_config 进 mgr，必须再来一次 set-project

# 3) 跑 synthetic（with_project=False 才会用 mgr 已 push 的项目，而不是 min config）
POST /api/v1/test/synthetic/start  json={"scenario": "ok_sequential_cycle.json",
                                          "with_project": False}
POST /api/v1/source/detection/start?channel=0

# 4) 轮询 GET /api/v1/source/detection/results 直到
#    counters['合格总数'] == 1 且 step_counts 三步都 ≥ 1
```

**Verify 矩阵**（每条都一定要过）：

| 验证项 | 期望 | 失败定位 |
|---|---|---|
| 三步都计数 | `step_counts == {step_a:1, step_b:1, step_c:1}` | step_min_frames / step_conf_thresholds / step_roi_polygons 哪个把帧吃了 |
| OK 事件触发 | `counters['合格总数'] == 1` | `_check_sequential_mode` 没跑：`sequence_order` 没配 / `settlement_mode` 是 first_step 没结算 |
| NG（反向剧本） | reverse `c→b→a` 跑出来 `counters['不良总数'] == 1` | sequence_order 是不是数字（注意是 `{"step_id":1}` 不是字符串） |
| 步骤 ROI 限定 | bbox 中心在 polygon 外那帧不计 step | `step.roi` 必须是 [[nx,ny],...] 归一化、≥3 点 |
| 移动目标 + ROI | 中段在 ROI 内、两端在 ROI 外 → step 计数 == 1（只有中段被认） | step_stats_mixin line 60-65 严格的中心点-多边形测试 |
