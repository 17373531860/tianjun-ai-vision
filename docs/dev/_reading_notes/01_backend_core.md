# 后端核心读码笔记

> 阅读范围：2026-07-05 分段通读 `backend/main.py`、`backend/core/config.py`、`backend/api/source*.py` 全族（含 23 个 mixin + 14 个 has-a 组件）、`router_manifest.py`、`channel_manager.py`、`alarm.py`、`debug.py`、`system_display.py`、`services/weighing_engine.py`。**共 47 个文件**。

---

## 一、逐文件档案

### 1.1 启动与配置层

#### `backend/main.py`（1281 行）

| 维度 | 内容 |
|---|---|
| **职责** | FastAPI 应用入口：环境引导 → DB 建表/迁移/种子 → 多通道自动恢复 → MES/扫码/集群/外设初始化 → 定时清理 → 路由挂载 → 插件/入站别名/定时导出/健康探测 → 优雅关机 API + MJPEG 端点 |
| **核心函数** | L1-4 `OPENCV_FFMPEG_CAPTURE_OPTIONS` 必须在 cv2 前 setdefault；L77-81 `migrate_database()` 断言桩（禁止回流 ALTER）；L380-402 `_run_startup_init()` 统一启动初始化；L419-487 `auto_load_active_project()` 按工位配置加载项目；L565-664 `auto_restore_video_sources()` 恢复视频源+自动开始检测；L667-714 `_init_mes_services()` 注入 MES Hook 到 VSM；L848-925 `cleanup_on_exit()` 退出钩子；L1100-1213 Electron 8 步关机 API |
| **线程锁** | L845 `_cleanup_lock`（防重复清理）；L504 CUDA 预热 daemon 线程；L728/794/814/837 多个 `threading.Timer` 定时任务 |
| **上下游** | 上游：环境变量、`router_manifest.mount_all_routers`；下游：所有 VSM 通道、MES Hook、alarm_router、各 Coordinator |
| **状态变量** | `_cleanup_done`、`_cleanup_timer`、`_daily_cleanup_timer`、`_extdev_cleanup_timer`、`_token_purge_timer` |
| **注释坑** | L629-633 开机自动恢复检测**不再看** was_detecting，只要源在跑+模型就绪就自动开始；L397-399 插件加载必须在 app 创建**之后** |

#### `backend/core/config.py`（201 行）

| 维度 | 内容 |
|---|---|
| **职责** | 路径常量（BASE_DIR/DATA_DIR）、旧数据迁移、Settings 单例、上传/录制目录自动创建 |
| **核心函数** | L22-40 `_is_empty_db()`；L43-83 `_fix_db_paths()` 修正 models/video_clips 绝对路径（**表名是 models 不是 ml_models**）；L86-151 `_migrate_old_data()` 模块 import 时即执行 |
| **线程锁** | 无 |
| **上下游** | 被 main.py、database、所有需要 DATA_DIR 的模块引用 |
| **注释坑** | L56-59 历史文档误称 ml_models；L154 `_migrate_old_data()` import 即跑，测试需设 `TIANJUN_DATA_DIR` 隔离 |

#### `backend/api/router_manifest.py`（110 行）

| 维度 | 内容 |
|---|---|
| **职责** | **主程序路由唯一登记处**（OVERLAP-3 治理）；`mount_all_routers(app)` 一次性挂载全部 `/api/v1/*` 路由 |
| **核心函数** | L24-109 `mount_all_routers()` — 分三段：CRUD 聚合段(L28-50)、重量级核心段(L52-90)、测试专用段(L92-109, `RUNTIME_MODE=test`) |
| **线程锁** | 无 |
| **上下游** | 仅被 main.py L1011 调用；import 放函数体内防 cv2 提前加载 |
| **注释坑** | L49-50 showcase_stats 与 sessions 共用 `/data` 前缀，**必须先注册 showcase_stats**；L73 Detection router 已删，走 source_routes |

---

### 1.2 多工位与外围 API

#### `backend/api/channel_manager.py`（885 行）

| 维度 | 内容 |
|---|---|
| **职责** | 多通道 VSM 容器；读写 `workstation_config.json`；GPU 分配；模型按通道独立实例加载 |
| **核心类/函数** | L39 `ChannelManager`；L82-87 `get(channel_id)`；L99-159 **`set_channel_count()`** 缩容时 4 处 on_channel_removed 清理链；L165-216 模型加载双签名（main vs slot）；L670+ FastAPI 路由 `/workstations/*` |
| **线程锁** | L44 `_lock`（通道 resize）；L50 `_global_warmup_lock`（跨通道 GPU warmup 串行）；L55 `_model_lock` |
| **上下游** | 持有 `Dict[int, VideoSourceManager]`；被 source_routes、main 启动恢复、alarm/MES 路由引用 |
| **状态变量** | `channel_count`、`channels`、`workstation_config.json` 各段（sources/gpu/auto_resume 等） |
| **注释坑** | L118-144 降工位必须调 4 个 on_channel_removed，缺一不可（AGENTS 不变量 4）；**禁止整写 workstation_config.json** |

#### `backend/api/alarm.py`（1067 行）

| 维度 | 内容 |
|---|---|
| **职责** | 串口报警灯/蜂鸣器；共享灯柱合成（ng/warn/ok/idle 四类优先级）；按通道路由 |
| **核心类** | L113 `AlarmManager`（单设备）；L620 `AlarmRouter`（channel→manager 映射）；L848 `alarm_router` 全局单例 |
| **核心方法** | L302 `trigger_alarm()`；L156 `set_shared_mode()`；L776 **`on_channel_removed()`** 共享模式只摘 ch、非共享断串口 |
| **线程锁** | L149 `_state_lock`（RLock）；L133 `_alarm_thread`；L151-152 延迟关灯 Timer |
| **上下游** | 被 `_trigger_event` → alarm_router.trigger_alarm；start/stop_detection → idle_light |
| **注释坑** | L776-805 共享模式降工位**不能** disconnect 物理设备；L16 新增报警事件必须归入四类优先级，禁止 bypass 合成器 |

#### `backend/api/debug.py`（177 行）

| 维度 | 内容 |
|---|---|
| **职责** | 调试中心 API：开关/日志/前端埋点回传；通道快照；集群 flow 回放测试 |
| **端点** | L30-72 `/debug/flags|logs|client-log`；L75-92 `/debug/channels`；L95-177 `/debug/test_cluster_flow` |
| **线程锁** | 无 |
| **注释坑** | L16-17 不挂鉴权，依赖开关默认全关；L99 验证的是 mes_hooks `_handle_cycle_end` 后半段 |

#### `backend/api/system_display.py`（371 行）

| 维度 | 内容 |
|---|---|
| **职责** | SystemConfig KV：display 品牌字段、polling 间隔、log-limits、device-status（RFC12）、license-cache |
| **端点** | L38 `/startup-ready` 深度就绪探针（**不鉴权**）；L84-109 display CRUD；L127-161 polling；L310 `/device-status` |
| **线程锁** | 无 |
| **注释坑** | L33-37 startup-ready 比 `/source/status` 更深，Electron 冷启动用 |

#### `backend/services/weighing_engine.py`（648 行）

| 维度 | 内容 |
|---|---|
| **职责** | v3.31 原生称重投料模式（logic_mode='weighing'）；纯状态机 + 副作用执行层分离 |
| **核心类** | L109 `WeighingStation`（单通道状态机：idle/await_tare/filling/done）；L343 `WeighingEngine`（多通道登记+feed_weight）；L642 `get_weighing_engine()` 单例 |
| **核心函数** | L92 `judge_amount()` 缺料/超量判定；L490 `feed_weight()` 外设管线入口；L509 `_execute()` 事件→报警/去皮/落库 |
| **线程锁** | L354 `_lock`（RLock） |
| **上下游** | 配置来自 `pipeline_config.weighing`；报警走 `_trigger_event`；落库 `WeighingRecord` |
| **注释坑** | L14-16 Station 不直接调主程序副作用，全经 Engine 翻译；非 weighing 通道 feed 直接忽略 |

---

### 1.3 VideoSourceManager 主类

#### `backend/api/source.py`（2054 行）

| 维度 | 内容 |
|---|---|
| **职责** | VSM 主类 + 全局 proxy；18 个 mixin 多继承；P7 has-a 兼容层；推理/检测控制入口 |
| **核心类** | L188 `VideoSourceManager(MRO: Tracking→…→PerItem)`；L1933 `_VideoManagerProxy`；L2045 `get_video_manager(channel)` |
| **兼容层** | L196-270 `_COMPONENT_ROUTES` 六组件路由表；L272-300 `__getattr__`/`__setattr__` 转发 drawer/mp_overlay/counters_mgr/video_transform/inference_exec/sequence_labels |
| **保留在主类** | L549-737 PT 锚点/累加/补落账；L800-836 tracker yaml；L907-960 `_reset_counting_cycle()`；L1009-1029 rod_filter；L1087-1131 推理线程启停；L1351-1413 `_clear_step_runtime_state()`；L1415-1590 start/stop_detection；L1630-1743 reset_stats；L1774+ generate_mjpeg（与 streaming_mixin 重复实现，主类版含 debug 埋点） |
| **线程锁** | L311 `frame_lock`；L312 `capture_lock`；L376 `_progress_lock`；L401 `detection_lock` |
| **注释坑** | L1929-1938 proxy 仅 ch0 默认；多通道必须 `channel_manager.get(ch)`；RenderMixin/StreamingMixin 已抽出但主类仍保留部分方法 |

#### `backend/api/source_routes.py`（2178 行）

| 维度 | 内容 |
|---|---|
| **职责** | `/api/v1/source/*` 全部 HTTP 端点：摄像头/RTSP/海康/视频/图片/检测/项目配置/轮询结果/ack-event/per_item 控制 |
| **核心端点** | L744-888 视频源启停；L1041-1175 检测启停/暂停/standby/reset；L1183-1318 ack_pending_event（含 elevated）；L1336-1760 **`get_detection_results`** 前端轮询核心；L1761 set_project_config |
| **线程锁** | 无（委托 VSM） |
| **注释坑** | L1795 `_dev_mocks_enabled()` 需 `ENABLE_DEV_MOCKS=1`；检测路由已统一到 source_routes，旧 `/api/detection/*` 已删 |

---

### 1.4 Mixin 档案（23 个）

| 文件 | 行数 | 职责 | 核心入口（行号） | 锁 |
|---|---:|---|---|---|
| `source_tracking_mixin.py` | 1106 | logic_mode=tracking 物品清点/track | `_update_tracking_stats` ~L200+ | 无专属 |
| `source_inference_loop_mixin.py` | 341 | 推理双线程主循环 | `_inference_loop` L25+；`_inference_select_and_run_model` | `_inference_frame_lock` |
| `source_step_stats_mixin.py` | 685 | 每帧步骤状态机主入口 | **`_update_step_stats` L58** | 无 |
| `source_capture_loop_mixin.py` | 406 | 采集线程循环 | `_capture_loop` | capture_lock/frame_lock |
| `source_event_trigger_mixin.py` | 643 | 事件中心 | **`_trigger_event` L64** → end_cycle | 无 |
| `source_model_load_mixin.py` | 652 | YOLO/TRT 加载 | `load_model` L56+ | router.gpu_lock/warmup_lock |
| `source_check_modes_mixin.py` | 24 | 聚合 4 子 mixin | 空壳多继承 L17-23 | — |
| `source_container_grouping_mixin.py` | 754 | 容器分组 per-box 结算 | `_settle_box` L475 `_trigger_event(1/2)` | — |
| `source_checklist_mixin.py` | 308 | counting 模式 checklist | `_settle_counting_cycle` L292+ | `_settle_lock` RLock |
| `source_events_check_mixin.py` | 157 | 事件 FSM（settlement×logic 交叉） | `_check_events` L41+ | — |
| `source_sequential_mixin.py` | 424 | 顺序/自定义顺序结算 | `_settle_sequential_cycle` L27+ | — |
| `source_settlement_mixin.py` | 1503 | 结算总控+跨周期/last_first | **`_settle_for_cross_cycle` L754** 分发；`_process_last_first_mode` L774 | — |
| `source_detect_runners_mixin.py` | 518 | YOLO 三种 runner | `_detect_only/_detect_and_track/_detect_segment` L136+ | `_gpu_lock_ctx` |
| `source_camera_start_mixin.py` | 764 | 6 种视频源启动 | `start_camera/rtsp/hcnetsdk/...` L114+ | 启 `_capture_loop` 线程 |
| `source_session_lifecycle_mixin.py` | 1608 | Session/Cycle DB 生命周期 | **`end_cycle` L~400+**；`start_session` | — |
| `source_recording_thread_mixin.py` | 305 | FFmpeg 录制线程 | `_recording_loop` L76 线程 | `_writer_lock` |
| `source_recording_api_mixin.py` | 403 | 录制 API | session/cycle/step 录制启停 | `_step_writers_lock` |
| `source_lifecycle_mixin.py` | 581 | pause/resume/stop/clear | `stop` L~100+ | — |
| `source_periodic_actions_mixin.py` | 888 | v3.5 周期性强制动作 | `_run_periodic_actions_on_start` | — |
| `source_synthetic_mixin.py` | 202 | 虚拟剧本源（测试） | `_SYNTHETIC_LOCK` L18 | class Lock |
| `source_per_item_mixin.py` | 1621 | logic_mode=per_item 逐件覆盖 | `_update_step_stats_per_item` L650+ | — |
| `source_render_mixin.py` | 139 | 画框（**未接入 VSM MRO**，方法经 drawer 兼容层） | `_draw_box` L85 | — |
| `source_streaming_mixin.py` | 165 | MJPEG（**未接入 VSM MRO**，主类 L1774 有完整版） | `generate_mjpeg` L48 | frame_lock |

---

### 1.5 Has-a 组件（15 个）

| 文件 | 行数 | 职责 | 核心 API | 锁 |
|---|---:|---|---|---|
| `source_state_init.py` | 398 | VSM 状态字段初始化 13 组 helper | `init_state(h)` L377 | 多处 Lock/RLock 创建 |
| `source_project_config_apply.py` | 550 | set_project_config 实现 | `apply_project_config` L471；L275 写 settlement_mode | — |
| `source_drawer.py` | 303 | Kalman + 画框 | `Drawer.draw_box` L68+ | — |
| `source_mediapipe.py` | 704 | MediaPipe 二段 pipeline | `MediaPipeOverlay` L236；worker 线程 L425 | `_init_lock` `_pending_lock` |
| `source_counters.py` | 74 | 计数器持久化 | `persist/save_snapshot_to_db` | — |
| `source_video_transform.py` | 121 | 旋转/镜像/坐标映射 | `apply_to_frame/map_bbox_to_display` | — |
| `source_inference_executor.py` | 49 | 推理 ThreadPoolExecutor | `get/shutdown` | — |
| `source_inference_router.py` | 283 | 多模型 GPU 调度 | `InferenceRouter` L139；`gpu_lock` L155 | gpu/warmup/event Lock |
| `source_sequence_labels.py` | 210 | 步骤标签查询（无状态） | `get_expected_labels` L107 等 7 方法 | — |
| `source_geometry.py` | 106 | 几何/字体工具 | `point_in_polygon/get_chinese_font` | — |
| `source_roi.py` | 167 | 逐步骤 ROI mask | `ensure_roi_mask/apply_roi_mask` | — |
| `source_recorder.py` | 255 | FFmpeg 录制器 + KalmanFilter2D | `FFmpegRecorder` L28 | `_lock` |
| `source_sdk_loader.py` | 179 | HCNetSDK/海康工业相机/调试日志 | `debug_log/get_hikvision_device_list` | — |
| `source_custom_mix.py` | 1076 | custom 混合子状态机 | `CustomMixMachine` L836；`compose_settle_event` L1032 | — |
| `source_label_split.py` | 484 | v3.32 同标签区域拆分（虚拟步骤）+ 就位提示：检测出口标签改写层（fixed/anchor 两种定位 × 多轮次 × 每轮独立区域） | `parse_label_splits` L108；`LabelSplitEngine.apply` L362；`parse_placement_guide` L426；`PlacementGuideState` L442 | 无（每通道单实例仅推理线程访问） |

---

## 二、模块级综合

### 2.1 VSM 生命周期（简图）

```
启动(main) → channel_manager 创建 VSM[ch]
         → auto_load_active_project → set_project_config
         → auto_restore_video_sources → start_camera/rtsp/...
         → (可选) start_detection → _start_inference_thread + _ensure_session_active

运行中:  capture_thread(_capture_loop) ──帧──→ inference_thread(_inference_loop)
                                              └──→ _update_step_stats / _update_tracking_stats / per_item
                                              └──→ 结算 → _trigger_event → end_cycle → MES Hook

停止: stop_detection → stop_inference/recording/scanner/alarm_idle
     stop/exit → end_session → cleanup_on_exit
```

**Session/Cycle 关键路径**（`source_session_lifecycle_mixin.py`）：
- `start_detection` L1506 → `_ensure_session_active()` 懒开 session
- `_trigger_event` L197 → **`end_cycle(is_good, event_id, ...)`** 落库 + 清 `current_cycle_id`
- MES 异步：mes_hooks L550 enqueue → worker **`_handle_cycle_end`** L1369

### 2.2 5 logic_mode × 4 settlement_mode 分发点

**logic_mode 枚举**（项目配置）：`sequential` | `detection` | `custom` | `tracking` | `per_item` | `weighing`（设备驱动，不走 YOLO 主循环）

**settlement_mode 枚举**（pipeline_config）：`first_step` | `last_step` | `last_first` | `cross_cycle`（v3.8.x 类二，与 last_first 互斥）

| 分发点 | 行号 | 行为 |
|---|---|---|
| `_update_step_stats` 入口 | step_stats L58-86 | per_item 分流；pending_ack 阻塞 |
| `_settle_for_cross_cycle` | settlement L754-772 | **核心分发**：custom+seq→`_settle_custom_cycle`；sequential→`_settle_sequential`；detection→`_settle_detection`；custom→`_settle_custom` |
| `_process_last_first_mode` | settlement L774+ | 仅 settlement=last_first + seq-like；R1 末步锚结算 |
| `_process_cross_cycle_groups` | settlement L~600+ | 仅 cross_cycle=true 守门 |
| `_check_events` | events_check L41-143 | 按 logic×settlement 决定何时触发结算事件 |
| `_inference_select_and_run_model` | inference_loop L67-125 | tracking→track；seg→segment；其余 detect_only |
| `apply_project_config` | project_config_apply L275-316 | 写 settlement_mode；last_first 清 strict_order；last_step 移除结算步 |

**weighing/per_item/tracking** 各自有独立状态机，不经过 `_settle_for_cross_cycle`。

### 2.3 `_trigger_event` 触发点清单

**定义**：`source_event_trigger_mixin.py` L64 — 守门（pending_ack、settle_dedup、ng_protect）→ NG 补做 defer → **`end_cycle`** → 计数器/MES/报警/插件 hook

| 来源模块 | 典型行号 | 场景 |
|---|---|---|
| `source_settlement_mixin` | L95,188,247,331,464,501,1488 | 各模式结算 OK/NG |
| `source_sequential_mixin` | L118,132,311,421 | 顺序 NG/OK |
| `source_events_check_mixin` | L92,157 | 事件 FSM 步骤完成 |
| `source_checklist_mixin` | L292-306 | counting 模式齐套/顺序 |
| `source_container_grouping_mixin` | L475,482 | 单箱 OK/NG |
| `source_step_stats_mixin` | L178 | 静态步骤触发 |
| `source_session_lifecycle_mixin` | L429,1520,1580 | 强制超时/空闲 NG |
| `source_event_trigger_mixin` | L622,631 | NG 补做 resolve |
| `source_per_item_mixin` | L1212,1232 | 逐件完成/NG |
| `weighing_engine._do_alarm` | weighing ~L543+ | 缺料/超量/前置校验 |

**`_handle_cycle_end`**：不在 source 族内，在 `backend/services/mes_hooks.py` L1369 — 由 `_trigger_event`→`end_cycle` 后异步 enqueue；debug.py L95 可回放其后半段。

### 2.4 `__getattr__` 路由表（P7 兼容层）

```python
# source.py L262-269 _COMPONENT_ROUTES
('drawer',          '_DRAWER_FIELDS',   '_DRAWER_METHOD_ALIASES')      # kalman, draw_box
('mp_overlay',      '_MP_FIELDS',       '_MP_METHOD_ALIASES')        # _mp_*, init/release/overlay
('counters_mgr',    '_COUNTERS_FIELDS', '_COUNTERS_METHOD_ALIASES')  # counters, persist
('video_transform', '_VT_FIELDS',       '_VT_METHOD_ALIASES')        # rotation/flip, 坐标映射
('inference_exec',  '_IE_FIELDS',       '_IE_METHOD_ALIASES')        # ThreadPoolExecutor
('sequence_labels', '_SEQ_FIELDS',      '_SEQ_METHOD_ALIASES')       # 7 个标签查询
```

**规则**（L272-300）：组件实例名本身访问会 AttributeError；字段/方法别名透明转发；MediaPipe 4 个 public 配置字段留在 VSM 本体。

### 2.5 ChannelManager 清理链（`set_channel_count` 缩容）

顺序（channel_manager L109-144，每步 try/except 隔离）：

1. `mgr.end_session()` + `mgr.stop()`
2. `get_mes_hook().on_channel_removed(cid)`
3. **`alarm_router.on_channel_removed(cid)`**
4. `channel_group_coordinator.on_channel_removed(cid)`
5. `workpiece_flow_coordinator.on_channel_removed(cid)`

缺任一步 → MES dict 残留 / 报警串口未释放 / 协调器幽灵工位。

### 2.6 线程全景

| 线程 | 创建位置 | 职责 |
|---|---|---|
| `_thread` capture | camera_start L306+ / source L1256 | `_capture_loop` 读帧 |
| `_inference_thread` | source L1093 | `_inference_loop` GPU 推理 |
| `_recording_thread` | recording_thread L76 | FFmpeg 写盘 |
| `_alarm_thread` | alarm L133 | 蜂鸣器节奏 |
| MediaPipe worker | mediapipe L425 | 骨架推理 |
| `_inference_executor` pool | inference_executor L39 | 同步推理任务 max_workers=1 |
| main 定时器 | main L728+ | 24h 清理/每日清理/外设日志/token |
| CUDA warmup | main L504 | 启动预热 |
| shutdown delayed | main L1206 | 延迟 os._exit |

**锁矩阵（VSM 级）**：frame_lock、capture_lock、detection_lock、_inference_frame_lock、_confirmed_detections_lock、_progress_lock、_settle_lock(RLock)、_step_writers_lock、_writer_lock、router.gpu_lock/warmup_lock（跨模型串行）。

---

## 三、疑点清单

| # | 疑点 | 依据 | 建议 |
|---|---|---|---|
| 1 | **RenderMixin / StreamingMixin 未进 VSM MRO**，但 source.py 主类仍保留 `generate_mjpeg`/`get_frame` 等，与 streaming_mixin 重复 | grep MRO vs 主类 L1745+ | 确认是否计划删除主类重复实现或把 mixin 接回 MRO |
| 2 | **TrackingMixin 在 MRO 最前**，其方法若与子 mixin 同名会优先 — 目前无冲突但新增方法需注意 | source.py L188 MRO 顺序 | 新 mixin 方法命名避免与 Tracking 前缀冲突 |
| 3 | **`_handle_cycle_end` 在 mes_hooks 不在 source 族**，读码笔记范围外但 `_trigger_event` 强依赖其异步语义 | mes_hooks L1369 | 读 MES 笔记时补全 enqueue/ critical 队列语义 |
| 4 | **weighing 模式与 VSM 并行存在**：logic_mode=weighing 时 VSM 仍可能跑 YOLO（若 start_detection），两者边界靠 project 配置 + engine.is_weighing_channel | weighing_engine L347-348 | 确认 weighing 项目是否应禁止 VSM start_detection |
| 5 | **settlement_mode 第四个值 cross_cycle** 与 last_first 互斥，前端+apply 双重校验，但 `_process_cross_cycle_groups` 与 `_process_last_first_mode` 共用 `_blocked_labels` | settlement L788-789 注释说互斥 | 配置脏数据时行为未实测 |
| 6 | **proxy `video_manager`** 默认 ch0，main.py shutdown_step 仍部分走 ch0 兜底路径 L1127-1135 | main L1103-1135 | 多通道关机是否应全走 channel_manager 循环（已知 v2.7.3 已改大部分） |
| 7 | **source_routes get_detection_results ~1300 行巨型函数**，轮询热路径 | source_routes L1336 | 性能/可维护性风险，值得单独 profiling |
| 8 | **PeriodicActionsMixin 888 行** 与主程序计数器/事件交叉，reset_stats 需同步 `_periodic_*` 字段 | source.py L1652-1672 | 改 periodic 规则时检查 reset_stats 清单 |
| 9 | **InferenceRouter 多模型** Step 7-9 迁移中，老字段 `self.model` 与 `models['main']` 双写，property 虚拟化未完成 | state_init L77-94 注释 | 读 model_load_mixin 时注意 _mirror_host_to_main |
| 10 | **custom_mix 1076 行** 与 container_grouping/per_item 状态交叉，reset_stats 需 `_custom_mix.reset()` | source.py L1726-1731 | custom 项目切换/清零测试矩阵 |

---

*文档版本：2026-07-05 · 读者：后端核心首轮通读 · 下一步建议：补 `mes_hooks.py` + `source_session_lifecycle` end_cycle 逐行跟读*
