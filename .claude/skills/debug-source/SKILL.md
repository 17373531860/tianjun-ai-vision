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

## 十三、读取检查清单（开始修问题前）

1. `backend/api/source.py`（先 grep 函数名再 Read，全文 1573 行别一次读完）
2. 对应 mixin（按 §一 表格找对应文件）
3. `backend/api/source_session_lifecycle_mixin.py`（cycle / session / record_step）
4. `backend/api/source_step_stats_mixin.py`（step 生命周期）
5. `backend/api/source_event_trigger_mixin.py`（如涉及事件）
6. `backend/api/source_periodic_actions_mixin.py`（如涉及保养 / 周期性提醒）
7. `backend/hotfix.py` + `backend/patches/`（确认补丁层）
8. `backend/api/channel_manager.py`（如涉及多通道）
9. `backend/services/mes_hooks.py`（如涉及 MES，但具体诊断走 `debug-mes` skill）

排查问题前**必须**先确定问题属于：source 状态机 / 推理 / 视频 / MES / 通道 / 报警 ——
对应 skill 不一样：
- `debug-source` ← 你在这里
- `debug-detection` 模型/推理结果
- `debug-video` 视频源采集 / 录像
- `debug-channel` 多通道 GPU / 串扰
- `debug-mes` MES 数据 / 扫码 / Hook
- `debug-alarm` 报警串口
- `debug-session` 数据记录 (Session/Cycle/Step DB 行) 异常
