---
name: debug-source
description: "诊断 source.py VideoSourceManager 的问题：检测状态机、Cycle/Step 生命周期、_trigger_event 中心 hook、v3.5.x 周期性强制动作、ghost cycle、线程安全。当遇到检测逻辑异常、步骤判定错误、周期不结束、计数器不对时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__sequential-thinking, mcp__sentry, mcp__context7"
---

# debug-source: VideoSourceManager 诊断（v3.5.x 主线）

你正在诊断 **天军 AI 视觉检测系统**的核心 `VideoSourceManager`。
v3.5.x 主线下它由 1 个主类骨架 + **15 个 mixin** + **6 个 has-a 组件**组成，约 5000 行。
任何"检测逻辑不对 / 周期不结束 / 计数器跳数 / 步骤错乱"问题，先来这里定位。

用户问题: $ARGUMENTS

## 一、文件结构地图（v3.5.x 真相）

```
backend/api/source.py (~1573 行)         主类 VideoSourceManager
│   ├── __init__ + reset_stats           （reset_stats 见下文 §五）
│   ├── set_project_config               （配置入口；调 _apply_periodic_actions 等）
│   ├── 兼容层 __getattr__/__setattr__   （把老属性路由到 has-a 组件）
│   └── has-a 组件（self.xxx）：
│       ├── self.drawer (source_drawer.py)            框/字体/Kalman 平滑
│       ├── self.mediapipe (source_mediapipe.py)      MediaPipe 叠层
│       ├── self.counters (source_counters.py)        计数器持久化
│       ├── self.video_transform (source_video_transform.py)
│       └── self.inference_executor (source_inference_executor.py)
│
├── 生命周期/采集 mixin
│   ├── source_capture_loop_mixin.py     _capture_loop 主采集循环
│   ├── source_inference_loop_mixin.py   _inference_loop 推理线程 + fps_inference
│   ├── source_lifecycle_mixin.py        pause/resume/standby/resume_inference/stop
│   ├── source_camera_start_mixin.py     start_camera/rtsp/video/image
│   ├── source_industrial_camera_mixin.py  start_hcnetsdk/start_hikvision_camera
│   │   （⚠ 与 camera_start_mixin 同名方法共存，依赖 MRO）
│   └── source_session_lifecycle_mixin.py  start/end_session, start/end_cycle,
│                                           record_step, _force_timeout_ng,
│                                           _discard_empty_cycle, _reconcile_step_records
│
├── 检测/推理 mixin
│   ├── source_detect_runners_mixin.py   _detect_only / _detect_and_track / _detect_segment
│   │                                    （v3.5.1 三层 clip_bbox_normalized 都在这里）
│   ├── source_model_load_mixin.py       load_model + TRT imgsz 探测
│   └── source_geometry.py               clip_bbox_normalized / bbox_iou / 字体缓存
│
├── 状态机 mixin
│   ├── source_step_stats_mixin.py       _update_step_stats（步骤生命周期主逻辑）
│   ├── source_event_trigger_mixin.py    _trigger_event 中心 hook
│   ├── source_periodic_actions_mixin.py v3.5.0 周期性强制动作 + v3.5.2 开机首检
│   ├── source_settlement_mixin.py       _settle_*_cycle / _process_*_step / _inject_backup_steps
│   ├── source_check_modes_mixin.py      4 种检查模式聚合（仅靠 MRO）
│   ├── source_sequential_mixin.py       顺序模式 / 自定义顺序
│   ├── source_container_grouping_mixin.py  容器分组 + per-box 结算
│   ├── source_checklist_mixin.py        checklist 维护 + counting
│   ├── source_events_check_mixin.py     步骤完成后的事件 FSM (_check_events)
│   └── source_tracking_mixin.py         tracking 模式 _update_tracking_stats
│
├── 录像 mixin
│   ├── source_recording_thread_mixin.py + source_recording_api_mixin.py
│   └── source_recording_mixin.py        （历史合并版 546 行，与上两者重叠）
│
└── source_routes.py (1279 ⚠️)           /api/v1/source/* 路由
```

**搜方法名时 grep `backend/api/source*.py` 全集，不要只 grep `source.py`。**

## 二、Cycle 生命周期（核心路径）

发生在 `source_session_lifecycle_mixin.py` 中：

```
检测到第一个有效步骤
  → start_cycle()
       ├── current_cycle_number += 1
       ├── INSERT DetectionCycle 行 → current_cycle_id 赋值
       ├── 重置 _rod_gate (传动杆 SessionGate)
       ├── scanner.resume_after_cycle (once_per_cycle 死锁兜底)
       ├── _mes_hook.on_cycle_start
       └── start_cycle_recording (FFmpeg 周期录像)
       ⚠ start_cycle 不清空 backup_steps_seen_in_cycle
         （结算前的备用步骤检测必须保留）

  → record_step() / _update_step_stats()  每个步骤完成时

  → 触发结算（任意一种）：
     ├── _check_events 内匹配条件 → _trigger_event
     ├── _force_timeout_ng（步骤 max_duration 超时 / cycle_max_duration 超时）
     ├── 空闲超时（idle_timeout_seconds）→ _settle_*_cycle → _trigger_event
     └── 容器模式 cluster_collector 等待超时

  → _trigger_event(event_id, reason)（中心 hook，§四）
       ├── settle_dedup 守门
       ├── NG 保护（ng_cycle_protect_seconds 抑制连发 NG）
       ├── 计 cycle_time → cycle_times / ng_cycle_times
       ├── end_cycle(is_good=...)
       │     ├── stop_cycle_recording
       │     ├── UPDATE DetectionCycle (end_time, duration, is_good, event_*, step_sequence)
       │     ├── _mes_hook.on_cycle_end
       │     ├── _check_periodic_actions(cycle.step_sequence, is_good)  ← v3.5.0
       │     ├── scanner.resume_after_cycle
       │     ├── _box_objects.clear() （容器模式清残留）
       │     ├── notify_cycle_settled （v3.1.2 多工位广播）
       │     └── current_cycle_id = None / current_cycle_uuid = None
       ├── 计 NG步骤 / NG TOP3
       ├── 执行 events_config[].actions（counter delta）
       ├── events_log.append（前端轮询拿）
       └── alarm_router.trigger_alarm(event{N}, channel_id=...)
```

`_discard_empty_cycle()` 是反向路径：
- DELETE StepRecord + DetectionCycle，把 current_cycle_number 回滚 1。
- 用在"结算后发现 step_sequence 经 `_filter_cycle_by_duration` 过滤后变空"的场景。

### Ghost cycle 问题（必须知道）

**症状**：cycle 刚结算结束，紧接着同一个 label 立刻又被识别成"新出现"，触发了一个空内容 / 1 步的"幽灵周期"。

**根因**：
- `_settle_*_cycle` / `end_cycle` 路径上**所有**结算分支都做 `step_last_seen.clear()`
  + `step_start_time.clear()` + `step_consecutive_frames.clear()`（见 `source_settlement_mixin.py` /
  `source_session_lifecycle_mixin.py` / `source_sequential_mixin.py` 几十处）。
- 清空之后，下一帧再看到 last_step 的 label 时，`prev_count == 0`，
  状态机判它是"新一轮的第一帧"，立刻设 `_step_raw_start[label] = current_time`、
  `start_step_recording`，几帧后就达到 `min_frames` 进入 detected_labels，
  又触发 `start_cycle()` → 形成 ghost cycle。

**推荐解法（不是单一修复点）**：
1. 给 last_step（或所有"结算附近的步骤"）配 `min_duration`（步骤时间配置）。
   `_update_step_stats` 在 §三的 `_step_raw_start` 守门会过滤掉短促 reappear。
2. 顺序模式可用 `step_accept_once`（已在 cycle 中的 accept_once 步骤即使
   max/min_duration 失效也保留）。
3. 给该 label 配 `disappear_delay` 大一些，让上一周期的"消失态"覆盖到新周期判定前。

**禁止**用"全局禁记"思路："`if label not in self.current_cycle_steps` 直接 drop"——
会把合法的 A-B-A 第二个 A 静默吞掉，已被多次因 P0 bug 回退。

## 三、Step 生命周期

主逻辑在 `source_step_stats_mixin.py: _update_step_stats(detections, original_frame)`，
**只在推理线程调用**。一帧的处理顺序：

```
本帧 detections（已过 confidence 阈值）
  ↓
1) 收集 frame_detected_labels （per-label 通过本步阈值的）
  ↓
2) 累加 step_consecutive_frames[label] += 1
   - 第一次出现：_step_raw_start[label] = current_time; start_step_recording
   - 达到 min_frames：标 step_frame_confirmed[label]=True，加入 detected_labels
   - first_seq_lbl 重新被确认 → _first_step_reconfirmed = True
  ↓
3) 备用步骤（is_backup）：从 detected_labels 移除 → 进 backup_steps_seen_in_cycle
  ↓
4) 本帧没看到的 label：_step_gap_count += 1
   - > gap_tolerance：重置 consecutive_frames / frame_confirmed / gap_count
   - 静态步骤标志 step_static_triggered[label] = False（允许再次触发）
  ↓
5) 静态步骤触发：consecutive_frames >= trigger_frames 且未触发过
   → step_static_triggered = True → _trigger_event(trigger_event)
   → _check_static_step_conditions
  ↓
6) Simultaneous-group 缓冲层：_process_simultaneous_groups 排序
   - 缓冲中的标签放入 _currently_pending_labels（不更新 step_last_seen，跳过消失检测）
  ↓
7) max_duration 超时：
   - timeout_ng=True → _force_timeout_ng（这步会触发 NG）
   - 否则：清掉该 label 的 step_last_seen / step_start_time / 帧计数（"模拟再次出现"）
  ↓
8) min_duration 守门：current_time - _step_raw_start[label] < min_duration → continue
   ↓
9) _process_single_step → 加入 current_cycle_steps + last_added_step + step_start_time
   （第一个有效步骤会调用 start_cycle）
  ↓
10) 消失检测（settle）：遍历 step_last_seen
    - last_seen 超过 disappear_delay 还没看到 →
        - 算 duration = last_seen - step_start_time
        - min_duration / max_duration 范围外 → is_valid=False
          - accept_once 的步骤已经在 cycle 里 → 保留
          - 否则 → 从 current_cycle_steps 移除
        - del step_last_seen[label] + step_start_time[label]
        - is_valid → step_counts[label] += 1, step_durations[label] = duration,
          last_step_completed_time 更新
        - record_step（写 StepRecord 表 + step 视频）
        - **v3.5.2: _check_periodic_actions_on_first_step(label)**
          （开机首检判定，§四 §六）
        - 收集到 pending_event_checks
  ↓
11) 延迟事件判定：所有消失步骤记录完后，统一 _check_events(label)
    （这样事件触发 end_cycle 时不会丢失同帧其他步骤的 record）
  ↓
12) 周期总时长超时：cycle_max_duration → _force_timeout_ng
  ↓
13) 空闲超时：current_time - _last_step_added_time > idle_timeout_seconds
    → _settle_*_cycle 强制结算
```

**关键阶段词汇对照**：
- "started" = 出现在 frame_detected_labels（consecutive_frames 开始累加）
- "settled / confirmed" = step_frame_confirmed=True + 加入 current_cycle_steps（这一刻才正式
  影响 cycle 判定）
- "cleared" = 第 10 步消失 + record_step + 从 step_last_seen 删除

## 四、_trigger_event 是事件中心 hook（必须懂）

文件：`source_event_trigger_mixin.py: EventTriggerMixin._trigger_event(event_id, reason)`。

**全部事件**最终都走这个函数，包括：
- OK / NG（event_id=1/2）
- 自定义事件（events_config 里配的）
- 静态步骤 trigger_event
- 各 _settle_*_cycle 触发的 NG / 自定义条件匹配

**职责**（顺序）：
1. settle_dedup 守门（pipeline_config.settle_dedup=true）
2. NG 保护（pipeline_config.ng_cycle_protect_seconds），距上次 NG 太近 → 调 `_discard_empty_cycle`
3. 在 events_config 查找 event 对象（支持 `event_1` / `1` / `'1'` 三种 id 写法）
4. 计 cycle_time（OK→cycle_times；NG→ng_cycle_times，各 ring buffer 100）
5. **调 `end_cycle(is_good, event_id, event_name, reason)`**（§二）
6. NG 步骤计数（仅 sequential / custom-on-sequential 模式 + reason 含"缺少"/"周期不完整"）
7. NG TOP3：从 reason 解析"缺少 / 重复步骤 / 顺序错误 / 缺件"等关键字 → ng_step_cycle_counts
8. 执行 event.actions（counter +delta）+ `_persist_counters`
9. 写 events_log（含 had_workpiece、should_warn_no_barcode v3.5.2）
10. `alarm_router.trigger_alarm(f'event{event_id}', channel_id=...)`

### 插件挂事件后处理的方式

**主线现状**：`_trigger_event` 没有显式的 hook 列表，也没有 plugin registry。挂插件目前用：
1. 改 events_config 加自定义事件 + actions（最优，纯配置）
2. 在 `mes_hooks.py` 的 `on_cycle_end` 加业务回调（`_handle_cycle_end` 是项目最大 Hub，
   见 AGENTS.md §五 Hub 关系图）
3. 把回调插到 `_trigger_event` 末尾（侵入式，仅在主作者同意 + 明确为插件预留 hook 时做）

**禁止**：在 `_trigger_event` 内部 monkey patch end_cycle 或重新调用 end_cycle ——
end_cycle 已把 cycle_id 置 None，二次调用会写空记录或抛异常。
周期性强制动作的 `_emit_periodic_notification`（§六）就是为此**绕过 end_cycle** 设计的。

## 五、reset_stats 的字段清理（v3.5.2 真相）

`source.py:1248: VideoSourceManager.reset_stats()`。**前端"清零"按钮 / API `/source/reset` 直接调它**。

**会清零的字段**（保留视频源、模型、project_config）：
- `step_counts / step_screenshots / step_last_seen / step_start_time / step_durations / step_intervals`
- `counters[*] = 0` + `_persist_counters`
- **v3.5.2 新增**：`_periodic_counters[*] = 0` + 所有 rule 的 `last_overdue_count = -1`
  + `_persist_periodic_counters`
- **v3.5.2 新增**：`_run_on_start_pending.clear()`
- `current_cycle_steps / backup_steps_seen_in_cycle / last_added_step`
- `events_log / _event_seq / ng_step_cycle_counts`
- `cycle_times / ng_cycle_times / cycle_start_time / step_durations_history`
- 各种 settlement / first-step flag、`_step_raw_start`、`_last_ng_time`、
  `_last_disappeared_step_times`
- Simultaneous-group buffer (`_sim_group_buffers / _currently_pending_labels`)
- Kalman / 帧计数 / static_triggered
- tracking 模式：`_reset_counting_cycle`

**不会清的**：`project_config / source_type / capture / model / current_session_id`。

> **历史钉子**：v3.5.0 / v3.5.1 reset_stats **不**清零 `_periodic_counters`（认为客户清零计数器
> 不应误抹保养计划）。v3.5.2 现场反馈"想从零开始就该全归零"后**改成清零**。
> 排查"reset_stats 后保养进度异常"时一定要确认运行版本。

## 六、周期性强制动作（v3.5.0 + v3.5.2，必读）

文件：`source_periodic_actions_mixin.py: PeriodicActionsMixin`。

**业务**：每做完 N 轮主流程后必须做一次"治具清洁 / 上油 / 校准"等动作，少做了报警，
做了就重置计数。

**核心字段（VSM 实例上）**：
| 字段 | 类型 | 含义 |
|---|---|---|
| `_periodic_actions` | `List[Dict]` | 解析后的规则（trigger_labels 已转为 set） |
| `_periodic_counters` | `Dict[rule_id, int]` | 当前累计 |
| `_run_on_start_pending` | `Set[rule_id]` | v3.5.2 开机首检待判定 |
| 持久化 | JSON | `DATA_DIR/counters/project_{pid}_ch{ch}_periodic.json` |

**调用图**：

```
set_project_config / apply_project_config
  → _apply_periodic_actions(config)
       ├── 解析 pipeline_config.periodic_actions
       ├── self._periodic_actions = parsed
       ├── self._periodic_counters = {r['id']: 0 for r in parsed}（先清零）
       ├── self._run_on_start_pending = set()（首检集合）
       └── _restore_periodic_counters(config)（从持久化文件覆盖回来）

start_detection / resume / resume_inference
  → _run_periodic_actions_on_start()
       └── 遍历 run_on_start=true 的规则：
            - 把 counter 推到 interval（= 已到期，Monitor 红黄状态）
            - 不立即 emit
            - rule_id 加入 _run_on_start_pending

每个步骤完成（_update_step_stats 第 10 步）
  → _check_periodic_actions_on_first_step(label)
       └── 遍历 pending：
            - label ∈ trigger_labels  → 静默 reset（counter=0），客户做了首件
            - label ∉ trigger_labels  → emit overdue 或 due，提醒先做首件
            - 无论哪条 → 从 pending 移除（一次性）

end_cycle commit 后
  → _check_periodic_actions(cycle_steps, is_good)
       └── 遍历每条规则：
            - count_basis 决定本轮算不算（all/good_only/ng_only）
            - did_trigger = trigger_labels & cycle_step_set
            - reset_policy='always'：触发就 reset
              reset_policy='only_when_due'：必须 counter ≥ interval 才 reset
            - 没 reset 且 counts_this_cycle → counter += 1
            - counter == interval：emit due_warning_event_id（一次）
            - counter > interval：按 overdue_repeat 决定 emit overdue_event_id
              （every_cycle / once / cooldown:N）
       → 持久化 _persist_periodic_counters
```

**事件分发不走 `_trigger_event`，走 `_emit_periodic_notification`**：
- 不调 `end_cycle`（cycle 已经结束）
- 不动 cycle_times / NG步骤 / NG TOP3
- events_log 加一条 `source='periodic_action'` + `should_warn_no_barcode=False`
- 跑事件 actions（counter delta）
- `alarm_router.trigger_alarm('event{N}', channel_id=...)`

**已知问题（v3.5.x 主线尚未修）**：
1. **连续超期记录漏触发**：`overdue_repeat='cooldown:N'` 时用 `last_overdue_count` 节流；
   如果中间发生 reset，rule['last_overdue_count'] 被置 -1；下一次重新累计到 overdue 时
   节流逻辑可能让"连续两个超期 cycle"合并成一次提醒。
2. **_apply_periodic_actions 总是把 counter 重置回 0 再恢复持久化**：如果项目配置在
   "已超期 30/20" 时被改保存（仅改了 name 字段），_periodic_counters 也会从 0 重新读盘 —
   read 的时候若磁盘文件落后就丢进度（生产线一般问题不大，但快速调试时容易踩到）。
3. v3.5.2 的"临时诊断 print"还没拆，日志会有 `[PeriodicActions/DBG]` 前缀，
   不要误以为是 bug 输出。

## 七、检测模式状态机

四种 logic_mode 在 `source_check_modes_mixin.py` 通过 MRO 聚合，实际逻辑分散：

| 模式 | 主文件 | 关键状态变量 |
|---|---|---|
| sequential | source_sequential_mixin.py + settlement_mixin._settle_sequential_cycle | current_step_index, step_sequence, last_added_step, _cycle_regression |
| detection | settlement_mixin._settle_detection_cycle | current_cycle_steps, backup_steps_seen_in_cycle |
| custom | settlement_mixin._settle_custom_cycle (228L) | custom_conditions（按 priority 排序匹配子序列）, custom_based_on, custom_sequence_order |
| tracking | source_tracking_mixin.py | _tracking_objects, _tracking_class_counters, _stack_state, _container_mode |

详见 `source_settlement_mixin.py` 头注释。tracking 模式的**堆叠子模式**和**最大识别数子模式**
（v2.7.4）见 `source_tracking_mixin._tracking_run_stack_fsm` / `_tracking_apply_max_recognized`。

## 八、线程安全（哪些方法只能在哪条线程调）

**线程清单**：
| 线程 | 创建处 | 主要工作 |
|---|---|---|
| capture | `resume()` / `start_*` 启动 | `_capture_loop`：读帧 → 派发到 `_latest_frame_for_inference` |
| inference | `_start_inference_thread` | `_inference_loop` → `_detect_*` → `_update_step_stats` / `_update_tracking_stats` |
| recording | `_start_recording_thread` | FFmpeg 进程 + 周期/步骤录像写盘 |
| FastAPI handler | uvicorn worker | `/api/v1/source/*` 各 handler |

**只能在推理线程调**：
- `_update_step_stats / _update_tracking_stats`（写 step_*, _stack_*, _tracking_* 等几十个 dict）
- `_process_single_step / _process_simultaneous_groups`
- `_check_events / _trigger_event / _force_timeout_ng`（间接）
- `_check_periodic_actions / _check_periodic_actions_on_first_step / _emit_periodic_notification`
- `start_cycle / end_cycle / _settle_*_cycle / record_step / _reconcile_step_records`
- `_inference_publish_detections`（带 detection_lock + _confirmed_detections_lock）

**只能在 capture 线程调**：
- `_capture_loop` 内部所有读取 capture / hik_camera / hcnet 的逻辑
- `_inference_grab_latest_frame` 的"派发端"（写 `_latest_frame_for_inference`，带 `_inference_frame_lock`）

**API handler 跨线程访问的字段**（**裸读**，没锁）：
- `current_cycle_id / current_cycle_uuid / current_session_id`（int / str / None）
- `counters / step_counts / step_durations / step_intervals` (dict, 推理线程在写)
- `current_cycle_steps`（list，推理线程在 append/[s for s in ...]）
- `events_log / cycle_times / ng_cycle_times`（list，推理线程在 append）
- `_periodic_counters / _periodic_actions`（dict / list，推理线程在写）

**有锁保护的**：
- `current_frame`（`frame_lock`，capture vs MJPEG handler）
- `current_detections`（`detection_lock`，推理 vs API handler）
- `_confirmed_detections`（`_confirmed_detections_lock`，推理 vs capture loop）
- `_latest_frame_for_inference`（`_inference_frame_lock`，capture vs inference）

实操结论：
- 改 dict 类字段要警惕"迭代时被改"——用 `list(d.items())` 复制后再走逻辑。
- 加新统计字段优先 dict 复制 + 写回；不要 inplace 改后直接 yield 给 handler。
- 所有 cycle / step / event 的写入路径都收口到推理线程；handler 只读不写（除了 stop / pause / reset_stats 这种）。

## 九、v3.5.1 三层 clipping 防御（框跑外面已修）

文件：`source_geometry.py: clip_bbox_normalized(x1, y1, x2, y2, w, h)`。

**规则**：把像素坐标 (x1,y1,x2,y2) 转归一化 (x,y,w,h)，所有边都 clip 到 [0, 1]。

**三层防御**：
1. ultralytics 模型输出已自带 letterbox clip（第一层）
2. `_detect_*_runners` 调 `clip_bbox_normalized` 时再 clip 一次（第二层）
3. drawer / Kalman 累积误差仍可能让显示框越界 → drawer 端再次 clip（第三层）

**排查"框跑外面"**：先看 `_detect_only` / `_detect_and_track` / `_detect_segment` 是否真的调
了 `clip_bbox_normalized`（grep `clip_bbox_normalized` in `source_detect_runners_mixin.py`）。
如果是 hotfix 版本可能这里被覆盖了 → 看 `backend/hotfix.py` 和 `backend/patches/`。

## 十、排查模板（"现象 → 第一步看 → 第二步看 → 修复方向"）

### 周期不结束 / 周期不开始
- **现象 1：检测到第一步但 cycle_id 一直 None**
  - 第一步看：`_mes_hook.is_scan_required(channel_id)` 是不是 True 但 `has_pending_workpiece` 是 False（`source_session_lifecycle_mixin.py: start_cycle` 第二段）—— 扫码绑定守门挡住了
  - 第二步看：`current_session_id` 是否存在；不存在则 `_ensure_session_active` 应该兜底（项目没加载 / `recording_enabled=False`）
  - 修复方向：要么扫码 / 要么把项目的 `pipeline_config.scan_required` 关掉

- **现象 2：步骤检测正常但 end_cycle 一直不触发**
  - 第一步看：cycle_max_duration 是否设置；idle_timeout_seconds 是否过大；步骤是否真的"消失"过 `disappear_delay`（grep "步骤完成" 日志）
  - 第二步看：检测模式是否 detection（必须所有步骤都到才结算）；是否一直缺最后一步
  - 第二步看：tracking 模式是否在等 cluster_collector 聚齐 box（`backend/services/cluster_collector.py`）
  - 修复方向：缩短 idle_timeout_seconds / 设 cycle_max_duration / 改回 sequential 模式

### 步骤判定错误
- **现象：明明做了步骤，但 cycle 没有这步**
  - 第一步看：步骤的 enabled / min_frames / step_conf_thresholds 配置（`steps_config`）
  - 第二步看：min_duration / max_duration 范围。step 持续时间不在 [min, max] 会被
    `_update_step_stats` 第 10 步从 current_cycle_steps 移除
  - 第三步看：gap_tolerance 太小 → 中间一帧丢检测就重置 consecutive_frames
  - 第三步看：是否被加入 `step_backup_map`（备用步骤会从 detected_labels 移除，等结算时注入）
  - 修复方向：调阈值 / 加 gap_tolerance / 看是不是被 step_accept_once 拦了

- **现象：A-B-A 的第二个 A 没记进去（步骤回退误判）**
  - 第一步看：是不是有人在某层加了 `if label not in current_cycle_steps` 的全局去重
  - 第二步看：last_added_step 是 A 时挡 A，但 last_added_step 是 B 时不应挡 A
  - 修复方向：步骤去重只能"上一步相同 (last_added_step == label)"才生效，绝不全局去重

### 计数器不对
- **现象：counters['OK'] 跳数 / 重复 +1 / NG 没 +1**
  - 第一步看：哪个 mixin 在写 counters（grep `self.counters\[`）
    - `_trigger_event` 末尾会执行 `event.actions` 里配的 counter delta
    - `_emit_periodic_notification` 也会跑 actions（独立路径！）
    - `record_step` / `record_cycle` 不直接写 counters，只写 DB
  - 第二步看：是不是同一个 cycle 的 NG 走了"NG 保护"分支（settle_dedup）但又走了 `_discard_empty_cycle` —— 这种情况 counters 不应该 +1，但有时 hotfix 会破坏这个不变量
  - 第三步看：多通道污染 —— `counter_file = DATA_DIR/counters/project_{pid}_ch{ch}.json`，
    确认 channel_id 没拿错
  - 修复方向：定位 +1 调用栈（在 `_persist_counters` 加临时 traceback）→ 确认调用来源

- **现象：周期性强制动作 counter 不对（`_periodic_counters[rule_id]`）**
  - 第一步看：`[PeriodicActions/DBG]` 日志里 `apply_periodic_actions` 的"prev → reset → restored"
    记录，确认重启 / 切项目时是否真的从持久化恢复了
  - 第二步看：`_check_periodic_actions` 里的 `count_basis` / `reset_policy` —— 是否走对了分支
  - 第三步看：是不是 reset_stats 被前端误调（v3.5.2 后会清零 periodic_counters，§五）
  - 修复方向：确认 v3.5.2 的语义是否符合客户预期；不行就把 reset_stats 里的清零块 hotfix 掉

### Ghost cycle（cycle 刚结束又开新 cycle，几乎空）
- 第一步看：`_settle_*_cycle` / `end_cycle` 后是否清空了 step_last_seen（一定是）
- 第二步看：last_step 的 label 在 cycle 结束当帧是否仍在 frame_detected_labels
- 修复方向：见 §二"Ghost cycle"，给 last_step 配 `min_duration` / `disappear_delay`

### 框跑外面（检测框越界）
- 第一步看：`source_detect_runners_mixin.py` 是否真调 `clip_bbox_normalized`
- 第二步看：drawer / Kalman 端是否还做最后一次 clip
- 修复方向：v3.5.1 已加三层防御，如果还跑外说明被 hotfix 覆盖或路径绕过

### 周期性强制动作的事件没弹 / 弹错
- 第一步看：`[PeriodicActions]` 日志，确认 `_check_periodic_actions` / `on_first_step` 是否被调
- 第二步看：events_config 里有没有该 event_id（emit 的轻量路径要在 events_config 里查得到事件对象）
- 第三步看：`overdue_repeat` 是不是 'once' 但已经触发过；或 'cooldown:N' 节流住了
- 修复方向：手动把 `last_overdue_count = -1` 重置；改成 'every_cycle' 验证

### MES 数据不对但检测正常
- → 不在 source.py 内。看 `backend/services/mes_hooks.py` + `backend/services/work_order.py`，
  使用 `debug-mes` skill。

### 视频卡顿 / FPS 低
- → 用 `debug-video` 和 `debug-detection` skill，不在本 skill 范围。

## 十一、必看的运行时补丁

**改源码前必查**（防止"明明改了源码但行为还是老的"）：
- `backend/hotfix.py`：猴子补丁了 `start_rtsp` 等方法
- `backend/patches/session_fix_patch.py`：补丁了 `resume_inference / start_detection / resume`
- `apply_runtime_patches()` 在 `backend/main.py` 启动时调用

打包过的客户机环境下，`backend/api/source*.py` 中有些会被 Nuitka 编成 `.pyd`（白名单见
`.github/workflows/build.yml: CORE_FILES`）。**改了源码也得检查这个白名单**——不在白名单里的
mixin 改动就是源码裸跑（IP 漏出去），但行为对得上。

## 十二、已知小坑

- `bare except:` 在多处静默吞异常（grep `except:` 定位）
- DB session 是 `SessionLocal()` 手动创建非依赖注入，记得 `db.close()`
- `from ctypes import *` 污染命名空间，海康 SDK 相关常量都在全局里
- `os._exit(0)` 绕过 Python 清理（debug 时遇 dump 别意外）
- `_init_inference_vars` 初始化~100 个变量，加新字段必须同步进 `reset_stats`
- 跟踪模式秒→帧换算必须用 `max(self.fps_inference, 10)`，不能用 `fps_actual`
  （v2.7.13 钉死的规则；fps_actual=采集，跟踪/事件帧数累加在推理线程）
- 同名方法 MRO 陷阱：`source_camera_start_mixin` 与 `source_industrial_camera_mixin`
  的 `start_hcnetsdk / start_hikvision_camera` 同名共存，改前先 `import inspect; print(inspect.getmro(VideoSourceManager))`
- `mes_hooks.py` 内部 import 必须 `from services.xxx` 不是 `from backend.services.xxx`，
  否则 ImportError 被静默吞掉

## 十二·五、v3.7.3 关键行为变更（顺序模式 / 鬼周期 / accept_once）

> 这块是 v3.7.3 才稳定的，遇到顺序判定问题先看这一节，否则可能照旧版理解错。

### 1. 顺序模式判定 — 多次出现 label 不再误判 NG

**老坑**：`sequence_order` 含重复元素（例 `A-B-C-B-D`，B 出现 2 次）时，v3.7.2 之前的实现：
- `_check_sequential_completion` 用 `unique_steps = [...]` 去重后比对，A-B-C-B-D 期望被截成 A-B-C-D
- `_settle_sequential_cycle` / `_finalize_sequential_cycle` 用 `unique_steps.index(label)` 总返首次位置，B 第二次出现的索引永远是第一个 B 的位置 → 顺序检查总挂

**v3.7.3 起**：
- `_check_sequential_completion` 改用 `collections.Counter` 区分多次出现；unexpected / duplicated / missing 都按 multiset 判定（actual_counter[label] vs expected_counter[label]）
- `_settle_sequential_cycle` / `_finalize_sequential_cycle` 走"multiset 相同 → 逐位比较 current_cycle_steps vs expected_labels"，不再 unique 去重

**调试要点**：客户报 0% OK 率时优先查 `pipeline_config.sequence_order` 是否含重复 label；前 / 后端都需 v3.7.3+ 才能正确处理（旧客户机要发新版）。

### 2. "鬼周期" 防护演化轨迹（v3.7.3 拦阻名单 → v3.8.x 治本方案）

**鬼周期的根本原因**（v3.7.2 及之前）：
- 周期 settle 完后立刻 `step_last_seen.clear()` / `step_frame_confirmed.clear()`
- 但模型对上一周期最后一步（例"放置产品"）的连续识别**还在持续**
- 下一帧识别到"放置产品" → `_process_single_step` 立即把它加入新 cycle → ghost cycle (`cycle_steps = ["放置产品"]`)
- 客户接下来做"拿取配件 1"被 strict_order 拦截，UI"闪一下没反应"
- 直到下一个"放置产品"再来触发 settle → 整轮 NG，客户感知"软件突然失灵"

**v3.7.3 中间方案** — `_post_settle_ignore_labels`（鬼周期拦阻名单）：
- settle 时把"仍处于 `step_frame_confirmed=True`"的 label 全部记到 `self._post_settle_ignore_labels: set`
- `_process_single_step` 入口拦截这些 label，必须先 disappear 一次才能再加入新 cycle
- **缺点**：拦阻名单语义模糊（客户故意做的步骤被误拦）、与"按步骤的消失等待时间"语义冲突、状态字段散落

**v3.8.x 治本方案** — 删除拦阻名单，分两层治理：
1. **第一层（参数）**：把 `max_interval` / `gap_tolerance` 合并成单一"消失等待时间 disappear_delay"
   - 客户只调一个参数：步骤消失等待几秒才认为真的消失
   - 残影只要消失等待时间够长就能被自然消化（不会触发新 cycle）
   - 规则 1：消失等待期间若出现其他有意义步骤，立即终止等待（语义上"客户已经推进到下一步"）
2. **第二层（同时出现组重构）**：见 §十二·六
   - 类一：周期内顺序无关的同时组（B-C 谁先谁后）
   - 类二：跨周期同时组（上一周期末步 E + 下一周期首步 A 互相等待）
   - 类二自带"被屏蔽标签集合 + 跨周期等待状态"，**结构性消除鬼周期**

**v3.8.x 已删除的字段**（grep 不到也别复活）：
- `_post_settle_ignore_labels`（拦阻名单本体）
- `_capture_post_settle_ignore_labels`（settle 时的写入函数）
- `_first_step_had_gap` / `_first_step_reconfirmed` / `_first_step_disappeared_at`（首步重出辅助标记）

**调试要点**（v3.8.x 之后）：
- 客户报"settle 后第一个动作没反应"先查 `disappear_delay` 是否过长
- 客户报"做完一件马上鬼周期"先查项目是否配了跨周期同时组（一般 E-A 互相等待就能消除）
- 看日志：`[跨周期等待终止·*]` 表示跨周期路由生效；`[跨周期屏蔽解除]` 表示出现组外步骤后屏蔽集合被清空
- 测试时强制清空：调 `mgr._clear_step_runtime_state()`（一次性清掉所有 11 个状态字段，含新增的 `_blocked_labels` / `_cross_cycle_waiting`）

### 3. `accept_once` 周期内 N 次配额放行

**老坑**（v3.7.2 及之前）：
- `_process_single_step` 里 `if step_accept_once.get(label) and label in current_cycle_steps: 拦截`
- `sequence_order` 含 `检查外观×2` 时第 2 次识别被拦下 → 框冒蓝色但不变绿

**v3.7.3 起**：
- 计算 `expected_count = expected_seq.count(label)` 和 `current_count = current_cycle_steps.count(label)`
- 若 `current_count < max(1, expected_count)` 不拦截，让后续 add-to-cycle 走"期望重复"分支
- 否则按老 first_step / 已完成 cycle 等分支处理

**调试要点**：
- accept_once 现在不再是"周期内 1 次硬规则"，是"按 sequence_order 期望次数限额"
- 期望次数=0（label 不在 sequence_order 里）时仍按 1 次限额兜底

### 4. v3.7.x / v3.8.x 状态字段索引（按所属 init 函数）

| 字段 | 类型 | 用途 | 初始化位置 |
|---|---|---|---|
| `_periodic_counters` | `dict[rule_id, int]` | 周期性强制动作触发计数 | `_init_periodic_actions` |
| `_run_on_start_pending` | `set[rule_id]` | run_on_start 规则待发漏检告警的标记 | `_init_periodic_actions` |
| `_mjpeg_active_conn_id` | `int` | MJPEG 后来者上位 — 当前 channel 最新 generator id | StreamingMixin 内 lazy init |
| `_mjpeg_next_conn_id` | `int` | MJPEG generator id 分配序号 | StreamingMixin 内 lazy init |
| `_cycle_regression` | `bool` | A-B-A 步骤回退标记 | `_init_event_and_cycle_state` |
| `_sim_group_buffers` | `dict[group_idx, {...}]` | 类一同时出现组缓冲（顺序无关的成员逐个挂起再按优先顺序输出） | `_init_event_and_cycle_state` |
| `_blocked_labels` | `set[str]` | **v3.8.x** 跨周期同时组的被屏蔽标签集合 — 上周期残影不再触发新 cycle，出现组外有意义步骤时一次性解除 | `_init_event_and_cycle_state` |
| `_cross_cycle_waiting` | `dict[group_idx, {'phase','first_member','first_role','wait_start_time',...}]` | **v3.8.x** 跨周期同时组的等待状态机；4 种终止路径：另一侧到达 / 组外步骤 / 超时 / 屏蔽残影 | `_init_event_and_cycle_state` |

新增 state 字段时**必须**同步：
1. 对应 `_init_*` 函数（一般是 `source_state_init._init_event_and_cycle_state`）
2. `_clear_step_runtime_state`（`source.py`）— 这是 v3.8.x 引入的"一次性清空所有运行时状态"统一入口，被 `start_detection` / `stop_detection` / `apply_project_config` / `_force_timeout_ng` 共用
3. `reset_stats` 显式保留 / 清零
4. 若涉及周期开始 / 结束行为，去 `source_settlement_mixin` 和 `source_session_lifecycle_mixin` 检查 reset 时机

---

## 十二·六、v3.8.x 同时出现组重构（鬼周期治本方案）

> 这一节是 v3.8.x 主要架构变化，配合 §十二·五·2 的演化轨迹一起看。

### 总览：两类同时组

| 类别 | `cross_cycle` | 解决问题 | 主路径 |
|---|---|---|---|
| 类一·周期内同时组 | `false` | 一个周期内 B-C 谁先谁后由模型决定（不稳定），但客户需要它们按固定顺序写入 cycle | `_process_simultaneous_groups`（缓冲 + 结算前重排）|
| 类二·跨周期同时组 | `true` | 上周期末步残影 + 下周期首步同时出现，避免鬼周期 | `_process_cross_cycle_groups`（等待状态机 + 屏蔽集合）|

### 类一·非跨周期（周期内重排）

**配置 schema**：
```jsonc
{
  "enabled": true,
  "cross_cycle": false,
  "labels": ["B", "C"],
  "priority_order": ["B", "C"],     // 决定写入 cycle_steps 的顺序
  "time_window": 2.0                // 缓冲窗口（秒）
}
```

**行为**：
- 缓冲收集：组内成员逐个识别后挂起，等待全员到齐 → 按 `priority_order` 一次性输出到 `ready_ordered`
- **缓冲提前释放**（v3.8.x 新加）：等待期间出现"非组内有意义步骤"（detected_labels 减去组成员后非空），立即终止缓冲，已收集成员按 `priority_order` 输出
- **结算前重排**（v3.8.x 新加）：`_settle_*_cycle` 在 `_filter_cycle_by_duration` 之后、正式结算之前调 `_reorder_simultaneous_groups_in_cycle()`，把 `current_cycle_steps` 里同组成员按 `priority_order` 兜底回写
  - 解决"运行时缓冲被打断 / 模型先后顺序不稳定 → cycle_steps 顺序错乱"

### 类二·跨周期（鬼周期治本）

**配置 schema**：
```jsonc
{
  "enabled": true,
  "cross_cycle": true,
  "labels": ["E", "A"],
  "priority_order": ["E", "A"],
  "prev_cycle_labels": ["E"],        // 上一周期成员（一般是结算步骤）
  "next_cycle_labels": ["A"],        // 下一周期成员（一般是首步）
  "time_window": 3.0                 // 等待窗口（秒）
}
```

**前端配置 UI**：每个组成员可选"上周期 / 下周期"归属（`period_roles[label] = 'prev'|'next'`），保存时推导成 `prev_cycle_labels` / `next_cycle_labels`；加载时反向推导回 `period_roles`。

**互斥校验**：`cross_cycle=true` 的组不允许与 `settle_dedup=true` 并存（前端保存时校验，后端默认允许写入但运行时跨周期路由会绕过 settle_dedup）。

**状态机的 4 种终止路径**：

| 路径 | 触发 | 行为 |
|---|---|---|
| ① 另一侧到达 | 一侧成员先入等待，另一侧成员在 `time_window` 内到达 | 结算上周期 + 启动下周期（next 成员入 cycle_steps）+ 全员塞 `_blocked_labels` |
| ② 组外有意义步骤 | 等待中出现 `detected_labels - group_labels` 非空的标签 | 立即结算上周期 + 全员屏蔽 + 让组外步骤走正常路径 |
| ③ 等待超时 | `current_time - wait_start_time > time_window` | 自动结算上周期 + 全员屏蔽 |
| ④ 屏蔽残影（不终止）| 等待中同侧成员重复识别（残影持续闪） | 单帧消费掉，状态不变（不重复写入 cycle_steps） |

**入场逻辑差异化**（关键）：
- `first_role == 'prev'`（如 E 先到）：把成员加入 `current_cycle_steps`（情况乙：上周期末步进序列）
- `first_role == 'next'`（如 A 先到 + 当前周期非空）：成员**不**加入 cycle_steps（属于下周期），仅更新 `step_last_seen`

**`_blocked_labels` 解除机制**：每帧主循环先调 `_handle_blocked_labels_release(detected_labels)`，若本帧出现"非任何跨周期组成员"的有意义标签，一次性清空屏蔽集合（语义：客户已推进到下下步，残影窗口结束）。

### 主循环接入位置

`source_step_stats_mixin._update_step_stats` 在 `_process_simultaneous_groups` **之前**插入两步：

```
1. _handle_blocked_labels_release(detected_labels)  # 先检查是否解除屏蔽
2. consumed = _process_cross_cycle_groups(frame_detected_labels, detected_labels, current_time)
3. detected_labels -= consumed
   frame_detected_labels -= consumed
4. → _process_simultaneous_groups（类一缓冲）
5. → ready_ordered + _process_single_step（常规路径）
```

### 跨周期路由的 `_settle_for_cross_cycle()`

跨周期路由不走"识别到结算步骤 → 自然 settle"路径，而是直接调一个适配函数 `_settle_for_cross_cycle()`：
- 按当前 `logic_mode` 选 `_settle_sequential_cycle` / `_settle_custom_cycle` / `_settle_detection_cycle`
- `current_cycle_steps` 为空时跳过（避免重复结算）

### 排查模板

| 现象 | 第一步看 | 第二步看 | 修复方向 |
|---|---|---|---|
| 配置跨周期组后 settle 后 A 没进新周期 | 日志找 `[跨周期等待终止·*]` | 看 `_cross_cycle_waiting` 字典是否还在 waiting 状态 | 检查 `prev_cycle_labels` / `next_cycle_labels` 配置是否正确分组 |
| 仍然出鬼周期 | 看 cycle_steps 第一个步骤的标签 | 看 `_blocked_labels` 是否在残影期间没记上 | 把残影 label 加进跨周期组 + 加大 `time_window` |
| settle 后客户做新动作没反应 | 看 `_blocked_labels` 内容 | 看本帧 `detected_labels` 是否被 `_handle_blocked_labels_release` 解除 | 检查动作 label 是否被误划进跨周期组 |
| 类一缓冲输出顺序错乱 | 看 `_reorder_simultaneous_groups_in_cycle` 是否被 settle 调用 | 看 `priority_order` 配置 | 缺则补 |
| settle_dedup 与跨周期组互相影响 | 前端是否走过保存校验 | 后端直写 DB 绕过校验 | 提示客户从前端走保存或后端拒收 |

---

## 十二·七、v3.8.x last_first 结算模式（末步结算 + 首步开周期）

**触发场景**：客户工艺要求 D 出现就立即结算（不等 D 消失），且任何状态下 A 重出现都意味着"新周期开始 / 上周期 NG"。

### 入口与守门

- 配置位：`pipeline_config.settlement_mode = 'last_first'`
- 状态字段：`_pending_first_step` (bool)，`_blocked_labels` 复用类二（互斥保证不会冲突）
- 入口：`source_settlement_mixin.py:_process_last_first_mode` 在 `_update_step_stats` 跨周期路由后被调
- 守门：方法首行 `if settlement_mode != 'last_first': return set()` → **其他模式零影响**
- logic_mode 必须是 `sequential` 或 `custom-based-on-sequential`，否则方法直接退化

### 6 条核心规则（按代码内顺序）

| 规则 | 条件 | 动作 |
|---|---|---|
| **R5** | D 在 `_blocked_labels` + 当前帧含 D | 消费 D（残影屏蔽，不触发任何动作） |
| **R1** | 当前帧含 D + D 不在屏蔽中 | 把 D 写入 `cycle_steps` → `_settle_for_cross_cycle()` → `_pending_first_step=True` + `_blocked_labels.add(D)` + 消费 D |
| **R3** | `cycle_steps` 已含 A + 当前帧含 A + `cycle_steps[-1] != D` | 立即结算上周期（NG 缺 D）→ 手动设 `cycle_steps=[A]` + `start_cycle()` + `step_last_seen[A]=now` + 消费 A |
| **R2** | `_pending_first_step=True` + 当前帧含 A | `_pending_first_step=False`（让主循环 `_process_single_step` 写 A 入 cycle_steps） |
| **R4** | `cycle_steps` 空 + A 不在帧 + 序列内非 D 的下一步在帧中 | `_pending_first_step=False`（让主循环写顶替步骤入 cycle_steps） |
| **R6** | 帧里出现非屏蔽集合的有意义步骤 | 复用现行 `_handle_blocked_labels_release` 自动清空 `_blocked_labels` |

### 互斥校验（前端阻 + 后端兜底）

| 不能与 last_first 共存 | 校验位置 | 兜底行为 |
|---|---|---|
| 任意 step.strict_order=true | 前端 watch + 保存校验 | `_apply_pipeline_config` 强清空 `step_strict_order` |
| 跨周期同时出现组（cross_cycle=True） | 前端保存校验 | 复用 `_blocked_labels` 不会两边同时写 |
| pipeline_config.per_item.enabled=true | 前端保存校验 | per_item 走独立路径，不会触发 last_first |
| pipeline_config.first_step_aborts_pending_settle=true | 前端 watch + UI 隐藏开关 | `_apply_pipeline_config` 强制覆盖为 false |
| logic_mode = detection / tracking | 守门内 `is_seq_like` 直接返空 | — |

### 排查模板

| 现象 | 第一步看 | 第二步看 | 修复方向 |
|---|---|---|---|
| 选了 last_first 但 D 出现没结算 | 日志看 `_process_last_first_mode` 是否被调 | settlement_mode 是否真的是 'last_first' | 检查前端是否漏发字段 / 后端 apply_pipeline_config 是否覆盖 |
| 周期 1 结算后周期 2 出现幽灵步骤 | `_blocked_labels` 是否含 D | `_handle_blocked_labels_release` 是否提前解除 | 检查 disappear_delay 是否过短，模型残影窗口未跨过 |
| ABC 跳 D 直接 A，期望 NG 但出 OK | 第一个 cycle 是否真有 A | R3 触发前 cycle_steps 末尾是否真的不是 D | grep `[last_first R3]` 日志确认 fallback 路径 |
| ABCD 完整出现但仍判 NG | settle 内部诊断哪缺步 | step_last_seen 是否被前一帧 disappear_delay 误清 | 检查 step_min_frames / disappear_delay |
| 选了 last_first 后老逻辑（first_step）还在跑 | 看 `source_events_check_mixin.py:_check_events` | last_first 是否已加进 `('first_step', 'last_first')` 守门 | 已加，回归测试覆盖 |

### 测试入口

- 单测：`tests/test_settlement_last_first_v38.py` 24 条（State 0/1/2 + 互斥 + 退化）
- E2E：`tests/test_synthetic_last_first_e2e.py` + `tests/scenarios/last_first_settlement.json`

---

## 十二·B、v3.9.1 新增：事件手动确认 + 严格顺序 PT anchor 修正

> 这一节是 v3.9.1 patch 新增到 source 状态机的两个关键机制，全部跟 mixin/host 状态强相关。

### 事件手动确认（require_ack）

**配置位置**：项目 `events_config` 里每个事件支持新字段：
- `require_ack`（默认 false）：触发后冻结主推流 + 弹前端 overlay 等工人按确认
- `ack_timeout_sec`（默认 0）：超时自动确认，0 不超时
- `ack_resets_periodic`（默认 false）：确认后重置该规则的 N 轮计数器，防"未压墨提示框关不掉一直弹"

**架构**：
- 内部状态 `_pending_ack`（dict，含 `event_id` / `event_name` / `triggered_at` / `timeout_sec` / `is_periodic` / `periodic_rule_id`）—— `source_state_init.py` 初始化
- `_trigger_event` 检测到 `require_ack=true` 时挂状态，主推流线程（`source_capture_loop_mixin.py`）检测到该状态后停止帧推送
- 周期性强制动作（保养提示等）走独立路径 `_emit_periodic_notification`，原本绕过 `_trigger_event`；v3.9.1 后两条路径统一接入 `_pending_ack`
- 前端调 `POST /api/v1/source/detection/ack-event` → `source_routes.py:ack_pending_event` 清状态，`ack_resets_periodic=true` 时重置 `periodic_actions[rule_id].counter`

**排查模板**：

| 现象 | 第一步看 | 第二步看 | 修复方向 |
|---|---|---|---|
| NG 事件触发但没弹 overlay | `events_config[event].require_ack` 是否 true | `_pending_ack` 是否真的被设 | grep `_trigger_event` 加 print 看条件分支 |
| 周期性动作 (如未压墨) 触发后没弹 overlay | `_emit_periodic_notification` 路径是否接入 `_pending_ack` | 项目配置 `events_config` 里对应事件是否真的开 `require_ack` | 确认 v3.9.1 patch 已合并 |
| 确认后又立刻再弹 | `ack_resets_periodic` 是否 true | `periodic_actions[rule_id].counter` 是否重置 | 没重置 → 客户没勾这个选项, 引导客户开 |
| 主程序卡死, 看不到视频 | `_pending_ack` 是否残留 | 前端是否真发 ack POST | 直接 POST 后端 `/ack-event` 强制清; 或重启检测 |

**关键不变量**：
- `_pending_ack` 残留 → 整个工位的帧推送都停 → 必须有清理路径（前端按钮 / 后端 API / `_clear_step_runtime_state` 内部清）
- 多通道场景：每个 channel 独立 `_pending_ack`，互不影响（VSM 实例级）

### 严格顺序 PT 起点 anchor 修正

**背景**：客户严格顺序模式（`step.strict_order=true`）下，CT=6.21s 但 PT 加起来比 CT 大很多；翻转步骤 PT 高达 7.5s 而实际只用 0.6 秒。

**根因**：翻转标签在"正面涂黑"还在执行时就被 YOLO 提前识别 → `step_start_time[翻转]` 被记录到上一步骤还在执行的时刻 → `duration = last_seen - start_time` 横跨两步操作 → 严格顺序下相邻步骤 interval 出现负值（步骤完成时间早于开始时间）。

**修复**：`backend/api/source.py:_resolve_step_pt_anchor(label, fallback_start)` 助手函数，严格顺序步骤的 PT 起点夹到 `max(fallback_start, last_step_completed_time)`，确保起点不早于上一步完成时刻。

**接入位置（5 个写入点必须全部用同一 anchor）**：
| 位置 | 函数 | 原因 |
|---|---|---|
| `source.py` | `_supplement_step_durations` | settle 时补计跨度 |
| `source_session_lifecycle_mixin.py` | `_flush_active_steps_pt` | cycle 结束时 flush 还在画面的标签 |
| `source_sequential_mixin.py` | `_check_sequential_mode` 补计段 | sequential 模式 NG 路径补计 |
| `source_sequential_mixin.py` | `_check_custom_sequential_mode` 补计段 | custom-based-on-sequential 模式 NG 路径补计 |
| `source_routes.py` | `step_inflight_durations` 计算块 | **关键**：in-flight 实时 PT 也必须用同一 anchor，否则步骤完成时 PT 从大数字"啪"地跳到小数字（客户报 "PT 闪一下"） |

**排查模板**：

| 现象 | 第一步看 | 第二步看 | 修复方向 |
|---|---|---|---|
| CT < PT 之和 | `step.strict_order` 是否全 true | `_resolve_step_pt_anchor` 是否被调（grep "_resolve_step_pt_anchor"）| 没接入 → patch 不完整 |
| 步骤间 interval 负值 | `step_start_time` 是否在上一步 last_seen 之后 | `last_step_completed_time` 是否被正确更新 | 检查 `_check_*_mode` 结算路径 |
| 步骤完成瞬间 PT 闪一下从大跳小 | `step_inflight_durations` 是否用 anchor | `source_routes.py` 里 inflight 块是否调 `_resolve_step_pt_anchor` | v3.9.1 已修, 没修 = patch 缺位 |
| 非严格顺序模式下 PT 突然变小 | `step.strict_order=false` 是否被误开 | 非 strict 时 anchor 返 fallback_start 不改值 | 确认 step config |

---

## 十二·C、v3.9.1 新增：PT 计算口径 visible（累计可见时长）

> 这是后端永远算两份 PT 字段、前端 Settings 选源的显示口径功能。源生命周期相关，不是状态机改动。

**两份字段并行**：
- `step_durations` / `cycle_sum_step_durations`（跨度，老字段，CSV 导出 / history 等下游消费者契约不变）
- `step_visible_seconds`（累计可见时长，新字段，每帧累加 `detected_labels` 里每个 label 的 `_dt`，上限 1.0s 防 FPS 极低时单帧大跨度污染）

**生命周期**（在 `source_session_lifecycle_mixin.py`）：
- `start_cycle()` 调用时清零（与 `step_cycle_durations` 同步）
- 周期间隙保留（前端 resultHold 展示期内还能看见上一周期数据）
- 累计逻辑在 `source_step_stats_mixin.py` 每帧入口

**排查**：
- 客户报"我的 PT 数字变小了，跟以前不一样" → 看 Settings → PT 计算口径，可能选了 `visible`；切回 `span` 即可
- 客户报"step_visible_seconds 字段缺失" → 旧 SQLite session 表不入库这字段，只在 runtime 透出 → 不是 bug

---

## 十二·D、v3.10.1 新增：PT 多段策略 + 帧号锚 + 零段污染（含一个典型踩坑）

### 现象 → 根因 → 修复（v3.10.1 BUG-001）

**客户/QA 报**：显示设置切到 PT 多段合并策略 = **仅首段**（`first_only`）后，Monitor 步骤统计 PT 列大面积变成 `0.0s` 或 `--`，明明步骤已检测、有时长。

**根因链**（典型）：

1. `_supplement_step_durations`（settle 时给"画面还在的 label"兜底落账）用 `if duration >= 0` 守门，**放过了 0**。
2. 同帧 `step_last_seen == step_start_time` 场景（兜底首帧 / 单帧消失），`duration = 0`。
3. `_accumulate_step_pt(label, 0.0)` 把 `0.0` 追加进 `step_cycle_segments[label]` 历史。
4. 一个周期触发十几次这种 0.0 落账，真实首段（如 `2.24`）被挤到 list 中后部。
5. 前端 `formatStepPT` 在 `first_only` 取 `segments[0]` = `0.0` → `if (duration <= 0) return '--'` → 显示 `--`。

**`sum` / `max` 为什么没暴露**：`sum` 加 0 = 加 0；`max(0.0, 0.0, ..., 2.24)` = `2.24`。**只有 `first_only` 取首位才会被零段污染**。

**修复**：`_accumulate_step_pt` **入口加守门**（v3.10.1）：

```python
if rounded_dur is None or rounded_dur <= 0.005:
    return self.step_cycle_durations.get(label, 0.0)
```

5 个调用点共用一道守门，不用每处分别加。

### 排查步骤

1. **抓 segments 真实值**（关键命令，能 1 秒定位"是不是零段污染"）：

   ```bash
   curl -s "http://localhost:8001/api/v1/source/detection/results?channel=0" | python3 -c "
   import json, sys
   d = json.load(sys.stdin)
   seg = d.get('step_cycle_segments') or {}
   for k, v in seg.items():
       print(f'{k}: segments(n={len(v)})={v}')
   print('cycle_sum:', d.get('cycle_sum_step_durations'))
   "
   ```

   - **健康样本**（修复后）：`正面涂黑: segments(n=1)=[4.78]`
   - **病变样本**（修复前）：`正面涂黑: segments(n=22)=[0.0, 0.0, ..., 2.24, 0.8, 0.07, 0.0, 0.02, 0.04]`

2. 看 `segments` 长度：单段连续场景 n=1；YOLO 抖动多段场景 n=2~5；**n > 10 几乎一定是零段污染或老版本未修**。

3. 看是否本机版本 < v3.10.1：`cat electron/package.json | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"version\"])'`。

### 三种策略的真实行为（debug 时常误判）

| 场景 | sum | max | first_only |
|---|---|---|---|
| `segments = [2.0]` 单段 | 2.0 | 2.0 | 2.0 — 三档完全一样 |
| `segments = [0.14, 0.65, 2.04]` 多段（主操作在末尾） | 2.83 | 2.04 | **0.14 漏主操作** |
| `segments = [4.78]` YOLO 输出一整段 5 秒 | 4.78 | 4.78 | 4.78 — 三档同样无救 |
| `segments = [0, 0, ..., 2.24, ...]` 零段污染（修前） | 2.24+ | 2.24 | **0.0 / --** |

**关键认知**：YOLO 真实输出一整段 N 秒时，**三种显示策略完全等价**，都救不了。要切 max / first_only 起效，必须 YOLO 把同一动作识别成多段。

### 帧号锚踩坑

**症状**：视频源场景下 PT 跳变 / CT 跟视频时钟对不上 / 客户机解码慢时 PT 比真实小 30%+。

**根因**：`_compute_duration_sec` 视频源走 `(end_fr - start_fr) / fps_source`，**任一帧号字段是 0** 就退化成 fallback wall-clock。

**最常见错误**：新加的状态机分支 `step_start_time[label] = some_time` **没同步**写 `step_start_frame_pos[label] = self._video_frame_pos()` → anchor 取 0 → duration 算成超大。

**排查命令**：

```bash
curl -s "http://localhost:8001/api/v1/source/detection/results?channel=0" | python3 -c "
import json, sys
d = json.load(sys.stdin)
# 间接看后端是否走了帧号锚 (in-flight 值在视频快进时应该比 wall-clock 增长慢)
print('inflight:', d.get('step_inflight_durations'))
print('current_cycle_time:', d.get('current_cycle_time'))
"
```

视频快进 2x 跑，若 `current_cycle_time` 跟前端进度条对得上 → 帧号锚正确生效；若以 wall-clock 速度爆涨 → 有路径漏镜像写。

### 防重复结算（settle_dedup_window_seconds）

**字段位置**：`pipeline_config.settle_dedup`（开关）+ `pipeline_config.settle_dedup_window_seconds`（默认 2.0）。

**排查"开了开关但还在重复结算"**：

1. 看后端日志有没有 `[_trigger_event] 防重复结算: 距上次事件 X.XXs < Y.YYs, 抑制 (event=..., reason=...)` 行 — 没有就说明守门没生效。
2. 看 DB 该项目 `pipeline_config` 字段是否真有 `settle_dedup: true`（v3.10.1 之前 `saveProject` 漏写）。
3. 看 `_last_event_time` 是否在 `_init_cycle_time_state` 初始化（应有）。
4. **不要**把这个 dedup 跟 `ng_cycle_protect_seconds` 混用 — 后者只挡 NG → NG，前者覆盖所有方向，两个可同时启用。

---

## 十三、读取检查清单（开始修问题前）

1. `backend/api/source.py`（先 grep 函数名再 Read，全文 1573 行别一次读完）
2. 对应 mixin（按 §一 表格找对应文件）
3. `backend/api/source_session_lifecycle_mixin.py`（cycle / session / record_step）
4. `backend/api/source_step_stats_mixin.py`（step 生命周期 + v3.8.x 跨周期路由接入位置）
5. `backend/api/source_settlement_mixin.py`（v3.8.x 类一缓冲 + 结算前重排 + 类二跨周期路由都在这里）
6. `backend/api/source_event_trigger_mixin.py`（如涉及事件）
7. `backend/api/source_periodic_actions_mixin.py`（如涉及保养 / 周期性提醒）
8. `backend/hotfix.py` + `backend/patches/`（确认补丁层）
9. `backend/api/channel_manager.py`（如涉及多通道）
10. `backend/services/mes_hooks.py`（如涉及 MES，但具体诊断走 `debug-mes` skill）

排查问题前**必须**先确定问题属于：source 状态机 / 推理 / 视频 / MES / 通道 / 报警 ——
对应 skill 不一样：
- `debug-source` ← 你在这里
- `debug-detection` 模型/推理结果
- `debug-video` 视频源采集 / 录像
- `debug-channel` 多通道 GPU / 串扰
- `debug-mes` MES 数据 / 扫码 / Hook
- `debug-alarm` 报警串口
- `debug-session` 数据记录 (Session/Cycle/Step DB 行) 异常
