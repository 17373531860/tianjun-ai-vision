---
name: modify-source
description: "安全修改 source.py 的前置分析：列出所有调用者、线程访问点、状态变量依赖链、has-a 组件兼容层。修改 VideoSourceManager 前必须先用这个 skill 进行影响分析。"
argument-hint: "[计划修改的功能或方法名]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# modify-source: source.py 安全修改分析（v3.5.x 主线）

你正在帮用户安全修改 `backend/api/source.py`（**1573 行主类骨架** + 15 个继承式 mixin + 6 个 has-a 组件 + 工具模块）。
**这仍是整个系统最危险的文件，任何修改前必须完成以下分析。**

计划修改: $ARGUMENTS

---

## 1. 当前架构（v3.5.x P7 重构之后）

```
backend/api/source.py (1573L)               -- VSM 类骨架 + __init__ + __getattr__/__setattr__ + _COMPONENT_ROUTES
                                            -- get_video_manager / _get_mgr 入口 + Pydantic 模型 + 历史 re-export
│
├─ 15 个继承式 mixin (源文件名 → 类名 → 一句话职责)
│   1. source_tracking_mixin.py            TrackingMixin            tracking 模式 (物品清点) 12 个 _tracking_* 字段+方法
│   2. source_inference_loop_mixin.py      InferenceLoopMixin       推理线程主循环 _inference_loop + 4 个 _inference_*
│   3. source_step_stats_mixin.py          StepStatsMixin           _update_step_stats 步骤计数/时间窗/帧确认聚合
│   4. source_capture_loop_mixin.py        CaptureLoopMixin         采集线程主循环 _capture_loop (生产推理任务)
│   5. source_event_trigger_mixin.py       EventTriggerMixin        _trigger_event 中心事件 hook (报警/Toast/语音/MES)
│   6. source_model_load_mixin.py          ModelLoadMixin           load_model + TRT engine imgsz 多级探测
│   7. source_check_modes_mixin.py         CheckModesMixin          判定模式聚合器 (内部多继承 4 个子 mixin, 见下)
│   8. source_settlement_mixin.py          SettlementMixin          周期结算 8 种 _settle_* + _process_*_step
│   9. source_detect_runners_mixin.py      DetectRunnersMixin       _detect_only / _detect_and_track / _detect_segment
│  10. source_camera_start_mixin.py        CameraStartMixin         start_camera/rtsp/video/image/hikvision/hcnetsdk + reconnect
│  11. source_session_lifecycle_mixin.py   SessionLifecycleMixin    start/end_session, start/end_cycle, record_step (1111L ⚠)
│  12. source_recording_thread_mixin.py    RecordingThreadMixin     录制后台线程消费 _recording_queue
│  13. source_recording_api_mixin.py       RecordingApiMixin        录制公共 API (start/stop session/cycle/step recording)
│  14. source_lifecycle_mixin.py           LifecycleMixin           pause/resume/stop_detection + clear_caches
│  15. source_periodic_actions_mixin.py    PeriodicActionsMixin     v3.5.0 周期性强制动作 (每 N 轮 NG/OK 触发事件)
│
├─ CheckModesMixin 内部聚合 4 个子 mixin (MRO):
│   ├─ source_container_grouping_mixin.py  ContainerGroupingMixin   容器分组 + per-box 结算
│   ├─ source_checklist_mixin.py           ChecklistMixin           checklist 维护 + counting 模式
│   ├─ source_events_check_mixin.py        EventsCheckMixin         事件 FSM
│   └─ source_sequential_mixin.py          SequentialMixin          顺序 / 自定义顺序 / 自定义检测
│
├─ 6 个 has-a 组件 (self.<attr> = <Class>(...))
│   ┌── 组件文件 ──────────── 实例属性 ──── 数据所有权 ───────────────────────────┐
│   │ source_drawer.py            self.drawer           Kalman 滤波 + 中文字体 + 检测平滑 + draw_box
│   │ source_mediapipe.py         self.mp_overlay       MediaPipe pose/hands 模型 + 8 个内部状态
│   │ source_counters.py          self.counters_mgr     项目计数器 dict + 持久化 (json) + DB snapshot
│   │ source_video_transform.py   self.video_transform  画面旋转/镜像 + 检测框坐标映射 (无 host 依赖)
│   │ source_inference_executor.py self.inference_exec  持久推理线程池 (单线程)
│   │ source_sequence_labels.py   self.sequence_labels  步骤标签查询 (无状态, 仅依赖 project_config)
│   └────────────────────────────────────────────────────────────────────────────┘
│
├─ 工具/数据模块 (非 mixin, 非 has-a)
│   ├─ source_state_init.py             13 个 _init_* helper, _init_inference_vars 转发到这里
│   ├─ source_project_config_apply.py   apply_project_config (set_project_config 实现外移)
│   ├─ source_routes.py                 FastAPI 端点 (/source/*) — 调用 VSM 实例
│   ├─ source_geometry.py               IoU / 归一化 / 字体缓存
│   ├─ source_recorder.py               FFmpegRecorder + KalmanFilter2D
│   ├─ source_sdk_loader.py             HCNetSDK + MvCamera + debug_log re-export
│   └─ rod_filter.py                    传动杆同伴过滤 + RodSessionGate (源外, 但深度集成)
│
└─ ⚠ 4 个孤立 mixin 文件 (定义了类但 VSM 没继承, 历史拆分残留 — 见 AGENTS.md 第九节)
    source_render_mixin.py / source_streaming_mixin.py / source_recording_mixin.py / source_industrial_camera_mixin.py
    动这些前先 grep 是否真的有人用; 大概率是死代码, 修复期间不要扩展它们.
```

> **注**：以 `source.py:185` 的 class 行为准 = **15 个 mixin**（v3.5.x P7 重构后）。AGENTS.md 已对齐。

---

## 2. has-a 组件兼容层（最大架构特点，必读）

P7 重构把 5 个原 mixin 改成"组合"后，老代码（如 `self._kalman_enabled = True` / `self._draw_box(...)` / `self._apply_frame_transform(frame)`）仍能跑，**靠的是 VSM 类的 `__getattr__` / `__setattr__` 路由**。

源码位置：`source.py:185-296`，关键结构：

| 组件 | 接管字段集 | 方法别名表 | 路由名 |
|---|---|---|---|
| `self.drawer` | `_DRAWER_FIELDS` (7 个 kalman/检测平滑字段) | `_DRAWER_METHOD_ALIASES` (5 个) | `drawer` |
| `self.mp_overlay` | `_MP_FIELDS` (8 个 mp_* 字段) | `_MP_METHOD_ALIASES` (3 个) | `mp_overlay` |
| `self.counters_mgr` | `_COUNTERS_FIELDS` (`counters`) | `_COUNTERS_METHOD_ALIASES` (3 个) | `counters_mgr` |
| `self.video_transform` | `_VT_FIELDS` (rotation/flip_h/flip_v) | `_VT_METHOD_ALIASES` (4 个) | `video_transform` |
| `self.inference_exec` | `_IE_FIELDS` (`_inference_executor`) | `_IE_METHOD_ALIASES` (2 个) | `inference_exec` |
| `self.sequence_labels` | `_SEQ_FIELDS` (空) | `_SEQ_METHOD_ALIASES` (7 个) | `sequence_labels` |

**`_COMPONENT_ROUTES` 元组是单一来源**（`source.py:258-266`）。新增 has-a 组件只需：
1. 加 `_XXX_FIELDS` / `_XXX_METHOD_ALIASES` 类属性
2. 在 `_COMPONENT_ROUTES` 元组追加一项 `(component_attr_name, fields_const_name, methods_const_name)`
3. 在 `source_state_init.py:_init_components(h)` 里实例化

**修改老代码时的硬规则**：
- 你**不能**在 mixin 里直接 `self.counters = {...}` —— 会被 `__setattr__` 拦截路由到 `self.counters_mgr.counters`，这是预期行为，但只有 `_COUNTERS_FIELDS` 里登记的才转发。新增字段先决定它的所有权属于哪个组件再赋值。
- 你**不能**对组件实例属性本身赋值（`self.drawer = X`）—— `__getattr__` 抛 AttributeError，但 `__setattr__` 会走 super，所以可以替换；除非你在做单测 mock，否则别这么做。
- 重命名组件方法时**两边都要改**：组件类里的 method + `_XXX_METHOD_ALIASES` 里的 value。光改一边静默断链。
- `__getattr__` 对未登记字段抛 `AttributeError`（不会无限递归到组件）—— 加新字段忘了登记，写代码时 IDE 不会报错，运行时才炸。

---

## 3. `source_state_init.py` 的 13 个 `_init_*` 函数

VSM 的 `_init_inference_vars(self)` 现在只是 1 行 wrapper（`source.py:467-474`）。真正初始化代码全在 `source_state_init.py`，按主题切分为 13 个 helper：

| # | 函数 | 负责的状态域 |
|---|---|---|
| 1 | `_init_inference_threading` | 推理双线程：`_inference_thread / _inference_running / _latest_frame_for_inference / _inference_frame_lock / _confirmed_detections(_lock)` |
| 2 | `_init_health_and_timeout` | 心跳 + 推理超时：`_last_*_heartbeat / _inference_timeout / _max_consecutive_timeouts` |
| 3 | `_init_components` | **所有 has-a 组件实例化**：drawer / mp_overlay (隐式) / counters_mgr / inference_exec / sequence_labels |
| 4 | `_init_fps_stats` | `fps_actual / fps_inference / latency / _fps_*_counter / _frame_seq` |
| 5 | `_init_step_state` | 步骤检测全部状态：截图/计数/时间窗/帧确认/静态步骤/替补/同时出现组 + **rod_filter cfg + RodSessionGate** |
| 6 | `_init_event_and_cycle_state` | `events_log / current_cycle_steps / last_added_step / cycle_complete / _first_step_* / _cycle_regression` |
| 7 | `_init_tracking_state` | tracking 模式所有 `_tracking_*` 字段 + scan_pair (`_tracking_was_complete / _scan_pair_settle_hint`) + scan_mode='D' (`_scan_d_armed_box / _scan_d_box_states`) |
| 8 | `_init_event_counter_state` | event counting 模式：`_event_counters / _event_state / _event_visible_frames` |
| 9 | `_init_stack_state` | v2.7.4 堆叠模式：`_stack_state / _stack_counters / _stack_disappeared_at` |
| 10 | `_init_container_state` | container 模式：`_container_mode / _box_objects / _box_settled_results` + **`_settle_lock`(RLock, v3.4.2)** + `_force_settling_in_progress`(v3.1.2) |
| 11 | `_init_cycle_time_state` | `cycle_start_time / cycle_times / ng_cycle_times / step_durations(_history) / step_intervals` |
| 12 | `_init_session_state` | `current_session_id/uuid / current_cycle_id/uuid / cycle_step_records / recording_enabled / 班次 _session_start_shift` |
| 13 | `_init_recording_state` | `video_writer / cycle_video_writer / step_video_writers / _recording_queue(maxsize=30) / _recording_thread / recording_failures` |

**新增状态字段时**：
1. 决定它属于哪个 `_init_*` 主题域，加到对应 helper（不要塞回 `_init_inference_vars`，那只是 wrapper）。
2. 如果是组件状态 → 改组件类的 `__init__` 而不是这里。
3. 如果是跨主题 → 优先放更窄的主题（避免 helper 越来越胖）。
4. **遗漏初始化 = 上一次的状态残留 = 难以复现的 bug**。`_init_components` 里组件本身重建即可清空组件状态，不用每次都重置。

---

## 4. 修改前的 Grep 矩阵（强制）

### 4.1 必查文件（VSM 核心圈）

```text
backend/api/source.py                       — 主类骨架 + __getattr__/__setattr__
backend/api/source_*_mixin.py               — 23 个 mixin 文件全部 grep (15 直系 + 4 子 + 4 孤立)
backend/api/source_state_init.py            — 13 个 _init_* 函数
backend/api/source_project_config_apply.py  — apply_project_config (set_project_config 实际实现)
backend/api/source_drawer.py / source_mediapipe.py / source_counters.py /
  source_video_transform.py / source_inference_executor.py /
  source_sequence_labels.py                 — 6 个 has-a 组件
backend/api/source_routes.py                — FastAPI 端点, 调用 VSM 实例
backend/api/source_recorder.py              — FFmpegRecorder
backend/api/source_geometry.py              — IoU/归一化/字体缓存
backend/api/source_sdk_loader.py            — 海康 SDK 加载
backend/api/rod_filter.py                   — 传动杆过滤
```

### 4.2 调用方圈（VSM 实例的所有持有者/调用者）

```text
backend/api/channel_manager.py              — 持有 VSM 实例 (per channel)
backend/main.py                             — 注册 source_router; video_feed/snapshot 直接挂 app
backend/services/mes_hooks.py               — on_session_start/end + on_cycle_start/end + dispatch_cycle_end_export
backend/api/sessions.py / sessions_*.py     — 数据/导出 (4 个文件), 共用 ffmpeg_path
backend/services/scanner.py                 — 通过 mes_hooks 间接联动
backend/services/cluster_collector.py       — 通过 mes_hooks 触发 box 聚齐
frontend/src/api/source.js                  — axios 封装
frontend/src/views/Monitor/index.vue        — 检测主页轮询 detection/results
frontend/src/views/Source/index.vue         — 视频源 CRUD
```

### 4.3 ⚠ 易踩坑的旧引用 / 已删

```text
backend/hotfix.py                           — ❌ 不存在 (老 SKILL 误传)
patches/session_fix_patch.py                — ❌ 不存在 (老 SKILL 误传)
backend/api/detection.py                    — ❌ 已删 (等价端点在 source_routes.py)
backend/services/detector.py                — ❌ 已删
```

如果在某个 mixin 里看到这些路径的注释或 import，**直接删干净**，不要复活。

### 4.4 同名方法冲突区（v3.5.x 仍存在）

| 方法名 | 文件 A | 文件 B | 谁生效 |
|---|---|---|---|
| `start_hcnetsdk` | `source_camera_start_mixin.py` | `source_industrial_camera_mixin.py` | A（B 没继承） |
| `start_hikvision_camera` | 同上 | 同上 | 同上 |
| 录像相关方法 | `source_recording_mixin.py` (孤立) | `source_recording_thread_mixin.py + source_recording_api_mixin.py`（生效） | B/C |

**修这两组任意一边时**：先用 `grep -n "def start_hcnetsdk" backend/api/` 确认重复实现，决定保留哪份再动。

---

## 5. 线程安全要点

| 线程 | 入口函数 / 类 | 主要读写字段 |
|---|---|---|
| 采集线程 | `CaptureLoopMixin._capture_loop` | `current_frame` / `_latest_frame_for_inference` / `is_running` / `_last_capture_heartbeat` / `fps_actual` |
| 推理线程 | `InferenceLoopMixin._inference_loop` (单条, 由 `InferenceExecutor` 持有) | `_confirmed_detections` / `current_cycle_steps` / `step_*` / `counters` / `fps_inference` |
| 录制线程 | `RecordingThreadMixin` | `_recording_queue` / `video_writer` / `cycle_video_writer` |
| FastAPI handler | `source_routes.py` async 函数 | `is_detecting` / `current_session_id` / `current_cycle_id` / `project_config` |
| MES Hook 线程 | `MESHookManager._worker_loop` | 通过 hook 调 VSM 只读字段（`get_detection_results` 注入返回值） |
| 报警线程 | `AlarmManager` | 不直接读写 VSM, 通过 `_trigger_event` 事件触发 |

**强约束**：
- `current_frame` 用 `frame_lock`；`capture` 对象用 `capture_lock`；`_confirmed_detections` 用 `_confirmed_detections_lock`；`_inference_frame_lock` 保护推理输入帧。
- `_settle_lock` 是 **RLock**（v3.4.2 修过）：推理线程 `_settle_counting_cycle` 与 mes_hooks 线程 `settle_for_scan_pair` 可能并发同一 cycle，必须可重入。
- 跨线程的"秒→帧"换算（tracking lost / event tolerance / step dedup）必须用 `max(self.fps_inference, 10)`（详见第 11 节），用 `fps_actual` 会放大 3~6 倍。
- `recording_failures` 用 `_recording_failure_lock`；`step_video_writers` 用 `_step_writers_lock`。
- 新增的跨线程变量要么用现有锁，要么加新锁。简单 bool/引用替换可以无锁，但要确保单写多读。

---

## 6. MES Hook 集成点（5 处，修生命周期方法时必看）

source.py / mixin 中向 `MESHookManager` 单例（`backend.services.mes_hooks`）发出回调：

1. `start_session()` 后 → `self._mes_hook.on_session_start(...)`
2. `end_session()` 后 → `self._mes_hook.on_session_end(...)`
3. `start_cycle()` 后 → `self._mes_hook.on_cycle_start(...)`
4. `end_cycle()` 后 → `self._mes_hook.on_cycle_end(...)` —— 内置后再调 `MESGateway.dispatch()` 推送外部 + `dispatch_cycle_end_export` 触发自定义实时导出
5. `get_detection_results()` 返回前 → 注入 `result['mes']` + 当前操作员信息（供 Monitor 展示）

**规则**：所有 MES Hook 都在 try/except 中，异常不打断检测流程。但**修改这 4 个生命周期方法的签名/返回值/触发时序时**，要确认外部 MES 推送上下文和 `_handle_cycle_end → dispatch_cycle_end_export` 链仍完整。

---

## 7. 高危操作清单

下述修改**必须做完整影响分析**：

- 改 `_capture_loop()` → 影响所有视频源 + 推理输入帧。
- 改 `set_project_config()` 或 `apply_project_config(...)` → 影响所有检测模式 + 7 个 JSON 配置字段解析。
- 改 `start_cycle()` → 影响数据记录完整性。**禁止**在此清空 `backup_steps_seen_in_cycle`（替补步骤可能在周期正式开始前就被检测到）。
- 改 `end_cycle()` 或 `_settle_*` → 影响数据完整性 + MES 推送 + 周期性强制动作。结算函数负责清空 `backup_steps_seen_in_cycle`。
- 改 `_update_step_stats()` → 影响替补步骤 (`backup_in_detected` 处理在此函数)。
- 改 `_inject_backup_steps()` → 影响替补步骤注入。
- 改 `record_step()` → 影响步骤统计 + DB 写入。
- 改 `load_model()` → 影响 PyTorch / TRT 引擎加载链路 + `imgsz` 探测。
- **添加/删除实例字段** → 必须同步 `source_state_init.py` 的对应 `_init_*` helper；如果该字段属于 has-a 组件，同步登记到 `_XXX_FIELDS`/`_XXX_METHOD_ALIASES`。
- 改 `__getattr__` / `__setattr__` / `_COMPONENT_ROUTES` → **整个兼容层动摇**，必须读完所有 mixin 中带 `_kalman / _mp_ / _draw_box / counters / _apply_frame_transform / _inference_executor / _get_*_step_label` 的字符串再决定。

---

## 8. 步骤去重规则（绝对不可违反！）

**去重间隔（max_interval）只允许"上一步和这一步相同"时才生效。**

- A-A（连续相同）→ 去重，不重复记录。
- A-B-A（中间有其他有效步骤）→ 不去重，第二个 A 必须记录，结算时判 NG（步骤回退）。
- **禁止使用 `if label not in self.current_cycle_steps` 做全局去重！** 此 bug 多次复发，必须杜绝。
- 非连续重复时，步骤消失由 `disappear_delay` 确认。
- 正确做法：只拦截 `last_added_step == label`（连续重复），其余必须进入周期。

---

## 9. 已移除的机制（不要恢复）

- **`_just_settled` 标志**（2026-04 移除）：原本在结算后阻止非首步启动新周期，但会导致 NG 后所有步骤被永久拒绝。现在依靠 `min_duration` / `min_frames` 过滤误检。
- **`backend/hotfix.py` / `patches/session_fix_patch.py`**：v3.x 已删，不要在新代码或 SKILL 注释里复活引用。所有逻辑已合进 mixin，看到引用立即清掉。

---

## 10. 传动杆同伴过滤（rod_filter）

文件：`backend/api/rod_filter.py`。在 source 中的 6 个 hook：

1. `_init_step_state(h)` 里挂 `h._rod_gate` + `h._rod_filter_cfg`（已移到 `source_state_init.py:78`）。
2. `set_project_config` (实际在 `source_project_config_apply.py`) 里调 `read_rod_filter_config` 重读开关并重建 gate。
3. `_apply_rod_filters(detections)` helper 统一封装两层调用，过滤异常**绝不挡主流程**。
4. `_detect_only` / `_detect_and_track` / `_detect_segment` 三个推理入口 `return detections` 前一律走 `self._apply_rod_filters(detections)`。
5. `start_cycle` 的"真正落地点"（`self.current_cycle_id = cycle.id` 之后）调 `self._rod_gate.reset()`。
6. `stop_detection` 开头调 `self._rod_gate.reset()`。

**修改规则**：
- 配置读取路径：**推荐 `pipeline_config["rod_companion_filter"]`**（前端 Project 页保存路径），兼容 `project_config["rod_companion_filter"]` 顶层。**缺字段全按 OFF**，老项目零影响。
- 过滤按 **label 字符串** 匹配，不硬编码 class_id。改模型 / 类名重映射时不会炸。
- `dets` 坐标必须是归一化 (0-1)；内部用归一化 xyxy 计算 IoU。
- 新增"其他类别也要做类似过滤" → 复用 `filter_rod_by_companion` 的思路做另一层，**不要塞进 rod_filter.py**，防止 label 耦合。

---

## 11. 画面变换（VideoTransform 组件）

字段所有权在 `self.video_transform`（`source_video_transform.py`），通过兼容层暴露：
`video_rotation` / `video_flip_h` / `video_flip_v` 三个字段，`_apply_frame_transform / _has_display_transform / _map_bbox_original_to_display / _map_detections_original_to_display` 四个方法。

**应用位置**：`_capture_loop` 里 `original_frame = frame.copy()` **紧跟下一行**。必须在所有消费者（推理 / MJPEG / 录像 / 快照）之前，否则检测框坐标和画面会错位。

**坐标对齐**：变换发生在所有消费者之前，下游拿到的都是变换后帧，检测框直接正确，**严禁**在前端或推理后再对检测框做二次旋转/翻转。

**per_channel 持久化**：`_save_device_config` 写 `device_config.json` 的 `per_channel[str(channel_id)]` 子键，读写时**必须先读旧配置再合并**，否则会覆盖其它通道。

---

## 12. fps_inference 与"秒→帧"换算

- `self.fps_actual` = **采集线程**的 FPS（`_capture_loop` 每读一帧 +1），典型 25~37。
- `self.fps_inference` = **推理线程**的 FPS（`_inference_loop` 每跑一次模型 +1），受 GPU/模型限制常 5~8。
- 两者**独立统计**，不要混用。
- **强制规则**：任何在 `_inference_loop` 里累加的帧计数（tracking lost/gone、event tolerance、step dedup 等）所对应的"秒→帧"换算，**必须**用 `max(self.fps_inference, 10)`。
  - 用 `fps_actual` 会放大 `fps_actual / fps_inference` 倍，典型 3~6 倍延迟。
  - 必须加 `max(..., 10)` 兜底，`fps_inference` 启动瞬间为 0。
- **不要**为 tracking 模式加 `min_cycle_age = max(max_lost_sec, 1.0)` 这类秒级硬兜底，会把用户 <1s 的配置强行拉到 1s。
- API 层已在 `get_detection_results` / `get_source_status` / `get_manager_config` 透出 `fps_inference`。

---

## 13. 周期性强制动作（v3.5.0）

- **mixin**: `source_periodic_actions_mixin.py` (`PeriodicActionsMixin`)。VSM MRO 中**保持在 LifecycleMixin 之后**，否则 hook 顺序错。
- **集成入口（3 处）**：
  1. `source_project_config_apply.apply_project_config` 末尾调 `h._apply_periodic_actions(config)` — 解析 `pipeline_config.periodic_actions` 列表，从 `backend/data/counters/project_{pid}_ch{ch}.json` 恢复 counter。
  2. `source_session_lifecycle_mixin.end_cycle()` 在 commit + MES Hook 之后调 `self._check_periodic_actions(cycle.step_sequence, is_good)` — **必须独立 try/except**，否则会把 cycle 关闭流程拖崩。
  3. `source_routes.get_detection_results` 末尾调 `mgr.get_periodic_actions_status()` 注入 `result['periodic_actions']`。
- **配置项 4 维度**（任意修改都要看 frontend `Project/index.vue` 对应选项）：
  - `count_basis ∈ {all, good_only, ng_only}` — 哪些 cycle 计入。
  - `reset_policy ∈ {always, only_when_due}` — 触发动作 E 时是否重置。
  - `overdue_repeat ∈ {every_cycle, once, cooldown:N}` — 超期后多久触发一次。
  - `interval` 任意正整数；`due_warning_event_id` / `overdue_event_id` 可选。
- **不要**在 `_check_periodic_actions` 里调 `end_cycle()`（cycle 已经结算完）；用 `_emit_periodic_notification(event_id, reason)` 写一条轻量事件。
- **持久化**：`_persist_periodic_counters` 写 `backend/data/counters/project_{pid}_ch{ch}.json`，启动 `_restore_periodic_counters` 读。
- **测试**：`tests/test_pairwise_periodic.py` (54 组合 → 11 pairwise) + `tests/features/periodic_actions.feature`。

---

## 14. 修改原则

1. **最小改动**：只改必要代码，不做顺手重构。
2. **保持接口兼容**：不改函数签名，除非所有调用者同步修改（包括 `_XXX_METHOD_ALIASES` 中映射的旧名）。
3. **初始化同步**：新增状态字段必须加入 `source_state_init.py` 的对应 `_init_*` helper。
4. **组件归属**：新字段如果概念上属于 drawer/mp/counters/video_transform/inference_exec/sequence_labels，**写到组件类内**，再决定是否登记进 `_XXX_FIELDS`。
5. **线程安全**：跨线程变量使用现有锁 (`frame_lock` / `capture_lock` / `_inference_frame_lock` / `_confirmed_detections_lock` / `_settle_lock`(RLock) / `_step_writers_lock` / `_writer_lock` / `_recording_failure_lock`)，无合适锁就加新锁。
6. **DB 事务**：涉及多个 DB 操作时使用事务；session 操作走 `with SessionLocal() as session:` 模式。
7. **异常处理**：不要 bare except，至少 log 异常信息。MES Hook 调用必须 try/except 防止打断检测流程。
8. **mixin 顶层 import 自查**：改动 `source_*_mixin.py` 后，必须检查 `os/cv2/threading/uuid/datetime/platform/hik_log/debug_log` 等是否在顶部 `import`。mixin 拆分后曾多次出现 `NameError: name 'xxx' is not defined`（v2.7.x 修了 5 个）。

---

## 15. 影响报告模板

每次改 source.py / mixin / has-a 组件前，先填这张表：

```text
修改内容: [函数名 / 字段名 / 行为变化]
所属层: [VSM 主类 / 某 mixin / 某 has-a 组件 / state_init helper / 兼容层路由表]
影响的线程: [采集 / 推理 / 录制 / handler / MES hook / 报警 / 跨线程]
影响的调用链: [grep 出的所有调用者文件:行号]
兼容层影响: [是否登记/取消登记 _XXX_FIELDS 或 _XXX_METHOD_ALIASES]
state_init 影响: [需要更新哪个 _init_* helper]
DB 影响: [是否影响 Session/Cycle/Step 写入或读出格式]
MES Hook 影响: [是否影响 5 个 Hook 调用点的签名/时序]
前端影响: [/source/* 哪些端点的返回字段变了]
风险等级: [低 / 中 / 高 / 极高]
建议测试: [冒烟 + 单测 + 手测步骤]
```

---

## 16. 必跑测试矩阵

修改 source 后，**至少**跑下面这一组：

1. `python -c "from backend.api.source import VideoSourceManager; vsm = VideoSourceManager(0)"` — 构造不炸（验证 import + `_init_inference_vars` + 6 个 has-a 组件实例化）。
2. `python -m pytest tests/test_periodic_actions_v352.py` — 周期性动作 + state_init 链路。
3. `python -m pytest tests/test_pt_ct_modes_exposure.py tests/test_detection_results_exposure.py` — `get_detection_results` 字段对外承诺。
4. `python tools/check_imports.py`（如存在）— mixin 顶层 import 自查。
5. **手动冒烟**：起后端 + 前端 → Source 页接 USB / 视频文件各一路 → 跑一个 cycle → 确认 detection/results 返回 + 数据页能看到 cycle/step → 关掉视频源不报错。
6. 改了多通道相关字段：`channel_manager.set_channel_count(2)` → 跑两路 → 关一路 → 确认 `mes_hook.on_channel_removed` + `alarm_router.on_channel_removed` 都被调（AGENTS 第八节关键不变量 4）。
7. 改了 has-a 组件接口：grep 一遍所有 mixin 是否还有该字段/方法的字符串引用，避免兼容层路由后才被发现的运行时 AttributeError。
