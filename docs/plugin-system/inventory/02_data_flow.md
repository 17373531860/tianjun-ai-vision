# 02 — 一条业务流走完：扫码 → 检测 → MES → 显示

> 适用版本：v3.6.0（HEAD `5c97791`）
> 本文目的：把现实业务里**一个工件从被扫码到结果上屏**经过的所有代码路径、数据形态、跨线程边界、异常分支，**一次性铺平**。这不是教程，是**给插件系统看的"插入点地图"**——任何想在中途切入的插件都要从这条线上找位置。
>
> 阅读建议：先看第一节"剧本"，再按阶段顺序读 0~8 节，最后看第十二节"插件可插入点 13 个"。
>
> 配套：`01_module_map.md`（静态依赖）/ `03_extension_points.md`（详细接口契约）。

---

## 一、剧本：一个工件的一生

```
[现实世界]                             [代码层]
  ┌──────────────────────────────┐
  │ 1. 工人扫码枪扫产品条码         │ → 阶段 1: TCP 字节进 _text_lon_listen_loop
  │ 2. 产品送上传送带               │
  │ 3. 称重器读重量                 │ → 阶段 1.5: 扫码注入到外部设备 + 配对
  │ 4. 摄像头拍画面                 │ → 阶段 2: capture_loop 入帧队列
  │ 5. AI 模型识别缺陷              │ → 阶段 3: inference_loop 推理
  │ 6. 步骤逻辑判定 OK / NG         │ → 阶段 4: settlement_mixin 结算
  │ 7. 周期结束触发                 │ → 阶段 5: end_cycle
  │ 8. 工单 / 工件 / 缺陷数据落库   │ → 阶段 6: mes-hook-worker 异步消费
  │ 9. 集群主从聚齐 box (可选)      │ → 阶段 7: cluster_collector
  │ 10. 实时导出 (txt/csv/...)      │ → 阶段 7: export_realtime
  │ 11. 报警灯 + 蜂鸣器联动         │ → 阶段 7: alarm_router
  │ 12. MES 推送外部系统            │ → 阶段 7: mes_gateway
  │ 13. 屏幕显示检测框 + 弹 toast   │ → 阶段 8: 前端 6.7Hz 轮询
  └──────────────────────────────┘
```

**整条线穿过的进程边界**：

| 边界 | 位置 | 关键约束 |
|---|---|---|
| 硬件 → Python | TCP socket（扫码 LON 55256 / WMax 55266+76+86）；USB / RTSP / 海康 SDK（视频）；串口（报警 / 称重） | timeout / 重试 |
| 主线程 → worker 线程 | mes-hook-worker 队列、capture/inference 双线程、cluster heartbeat、export 渲染 | 队列深度、丢失风险 |
| Python → DB | SQLite WAL + busy_timeout=15s | 写入冲突→重试 |
| Python → 前端 | HTTP 短连接 + MJPEG 长连接 | polling 节流 |
| Python → 外部 MES | HTTP / Modbus | 重试 1 + retry_count |

---

## 二、术语和数据结构速查

### 2.1 通道（channel） — 多工位隔离单位

```
channel_manager.channels: dict[int, VideoSourceManager]
                              ↑
                              每个通道一个独立 VSM 实例:
                                self.capture (cv2.VideoCapture | None)
                                self.model (YOLO | None)
                                self.current_session_id (int | None)
                                self.current_cycle_id (int | None)
                                self.is_running (bool)        ← 视频源在跑
                                self.is_detecting (bool)      ← 检测在跑
                                self.is_paused (bool)
                                self.is_standby (bool)        ← 待机=停检测但保画面
                                self.project_config (dict)    ← Project 应用后的配置
                                self.counters (Counters 实例)
                                self.drawer (Drawer 实例)
                                ...30+ 状态字段
```

### 2.2 关键数据结构

**条码事件** — Scanner → MESHook：
```python
{
  "channel_id": int,        # 该扫码绑定的工位（broadcast 时是主工位）
  "serial_no": str,         # 解析后的真条码（barcode_parser 处理后）
  "raw_data": str,          # 原始字节解 utf-8（或 latin-1 兜底）
  "project_id": int,        # 当前激活项目 id (从 _project_id_getter 拿)
  "device_id": int,         # 扫码器设备 id (来自 ScannerDevice 表)
  "scan_mode": str,         # 'A'/'B'/'C'/'D' 或 'continuous' 等
  "pairing_group": str,     # 设备分组号（v2.8.1+ 优先按这个配对）
}
```

**步骤判定结果** — InferenceLoop 内部：
```python
detection_results = [
  {
    "label": "缺角",          # YOLO 类别名
    "confidence": 0.93,      # 置信度
    "bbox": [x1, y1, x2, y2], # 像素坐标
    "bbox_norm": [0.1, 0.2, 0.5, 0.6],  # 归一化 (clip 后)
    "class_id": 3,
  },
  ...
]
```

**事件触发参数** — `_trigger_event`：
```python
event_id   # 0=OK, 1=自定义事件 1, 2=NG, 3=自定义事件 3, ...
reason     # str, 触发原因 (用于落 step_records.event_reason)
```

**Cycle End 参数** — `mes_hook.on_cycle_end`：
```python
{
  "channel_id": int,
  "cycle_id": int,             # detection_cycles 主键
  "is_good": bool,
  "event_name": str,           # 来自 events_config 的 name
  "result_reason": str,
  "duration": float,           # 周期总时长（秒）
  "step_sequence": list,       # [(step_idx, label, confidence, frames, duration), ...]
  "project_id": int,
}
```

### 2.3 全局单例索引（异步边界源头）

| 单例 | 类 | 持有线程 | 队列 / 锁 |
|---|---|---|---|
| `channel_manager` | `ChannelManager` | 不持线程，但 channels 内每个 VSM 持 capture/inference 双线程 | 无队列；每个 VSM 有 `frame_lock` / `result_lock` |
| `mes_hook` | `MESHookManager` | `_worker_loop` 1 条 | `_task_queue` (Queue, maxsize ~1000) + `_spill_dir` 落盘兜底 |
| `scanner_svc` | `ScannerService` | 每个设备一条 listen 线程 | `_testing_ips_lock`（仅测试期忽略码） |
| `cluster_collector` | `ClusterCollector` | `_timeout_thread` + `_heartbeat_thread` | `_lock` |
| `extdev_svc` | `ExternalDeviceService` | 每个设备一条 pipeline 线程 | 内部 `_connections` dict |
| `alarm_router` | `AlarmRouter` | 不持线程；每个 `AlarmManager` 持 watchdog | `_lock` per-manager |

---

## 三、阶段 0：系统启动准备（产线开工前）

**前提条件检查**（如果其中一步失败，整条业务流走不通）：

1. **后端启动**：`backend/main.py:_run_startup_init()` 完成 5 步迁移 + seed
2. **项目加载**：`auto_load_active_project()` 把 `Project.{steps_config, pipeline_config, events_config, ...}` 应用到 `ChannelManager.channels[ch_id].set_project_config(config)`
3. **视频源启动**：`auto_restore_video_sources()` 按 `workstation_config.json` 恢复（USB / RTSP / 海康 / video_file）
4. **MES 服务初始化**：`_init_mes_services()` 把 `mes_hook` 实例注入到 `vm._mes_hook` + 每个 channel 的 `ch_mgr._mes_hook` + `scanner_svc.set_mes_hook(mes_hook)`
5. **扫码器启动**：`scanner_svc.start_all()` 加载 `ScannerDevice.enabled=True` 的设备 → 每个起一条 listen 线程
6. **集群启动**：`cluster.start()` — host 模式启 timeout / heartbeat 后台线程
7. **外部设备启动**：`extdev_svc.start_all()` — 每个外设起 pipeline 线程

**这 7 步全部异步，没有强同步保证**——如果第 4 步失败（MESHook 启动失败），第 5 步的 scanner 仍会启动，但收到的码无处可投。

**插件系统介入候选**：

> 阶段 0 是**插件最适合自我注册的窗口**。已有的 `_init_mes_services()` 可以加一句 `_init_plugin_system()`，扫描 `%APPDATA%/tianjun-ai-vision/plugins/` 加载所有签名通过的插件，注册到中心化 registry。

---

## 四、阶段 1：扫码触发（"工件登记"那一刻）

### 4.1 调用栈（端到端）

```
[硬件: WMax 扫码枪 192.168.0.100 → TCP 55256]
  ↓ TCP 字节流
[ScannerConnection._socket]  (per device, in services/scanner.py)
  ↓ 80ms idle-timeout 切帧
ScannerService._text_lon_listen_loop(conn, sock)            services/scanner.py:1444
  ↓ 解码 (utf-8 / latin-1) + dedup (last_scan + dedup_interval_sec)
ScannerService._on_data_received(conn, raw_data)            services/scanner.py:1719
  ↓ BarcodeParser.parse(raw_data)                            services/barcode_parser.py
  │  返回 ParseResult(serial_no, ok)
  │  ⚠️ 注意: 该 parser 无 DataLen 校验, 见 05_tech_debt 第 X 项
  ↓
  [分支 A: 测试期间 → 静默 return]                          (testing_ips_lock)
  [分支 B: 重码且未过 dedup_interval_sec → 静默 return]
  [分支 C: ScannerDisable 状态 → 不入 MES]                  v3.4.2
  ↓
  ┌── 同时做 2 件事 ─────────────────────────────────────┐
  │ ① 注入称重器 (配对外部设备)                            │
  │   ScannerService._inject_barcode_to_external_devices  services/scanner.py:1811
  │     for dev_conn in ext_svc._connections.values():
  │       if matches(scanner_group, target_channel, dev_conn):
  │         dev_conn._injected_barcode = serial_no         (设备线程后续读)
  │                                                        │
  │ ② 通知 MES Hook                                         │
  │   project_id = self._project_id_getter(channel_id)    (从 VSM 拿)
  │   mes_hook.on_scan_received(                           services/mes_hooks.py:381
  │     channel_id, serial_no, raw_data, project_id, device_id)
  └────────────────────────────────────────────────────────┘
```

### 4.2 MESHook 入队 + 异步消费

```
mes_hook.on_scan_received(...)             services/mes_hooks.py:381
  ↓ check is_channel_scan_disabled(channel_id) → 二次防御 (v3.4.2)
  ↓ self._enqueue(self._handle_scan_received, channel_id, serial_no, ...,
                  critical=True)
  ↓ 入 self._task_queue (queue.Queue)
  ↓ [✓ 同步路径完成: 主调线程立即返回]


[后台线程: mes-hook-worker]
  while not self._stop_event.is_set():
    self._drain_spill_once(max_items=20)    # 先放回上次落盘的关键事件
    task = self._task_queue.get(timeout=1.0)
    func, args, kwargs = task
    try:
        func(*args, **kwargs)
    except Exception:
        if critical:
            self._spill_to_disk(task)        # 关键事件落 disk 等下次启动重放
        traceback.print_exc()
```

### 4.3 工件创建/查找（`_handle_scan_received`）

**条件分支**（按 `ScannerDevice.scan_mode` 不同走不同路径）：

| scan_mode | 模式名 | 行为 |
|---|---|---|
| `'A'` | 单工件单扫 | 每次扫码 = 一个新工件，绑定到下一个 cycle |
| `'B'` | 节流型 | throttle_idle_ms 节流，去重处理 |
| `'C'` | 多工件 | 一次扫码绑多个 cycle 直到下次扫码 |
| `'D'` | 容器跨线（v3.4.0） | 工件按容器分组，跨工位追踪，scan_d_geometry='line'/'zone' |
| `'continuous'` | 连续模式 | 扫码不断连接 |
| `bind_timing='scan_pair'` | 码-码闭环（v3.3.0） | 扫 A 后等 scan_pair_max_wait_sec 秒等扫 B，B 触发结算 |

**工件 ORM 写入**：

```python
# services/workpiece.py:create_or_get_workpiece
wp = db.query(Workpiece).filter(
    Workpiece.serial_no == serial_no,
    Workpiece.batch_id.in_(active_batch_ids),  # 或 project_id 范围
).first()

if wp:
    if rebind_mode == 'rescan' and wp.status in ('ok', 'ng'):
        # v3.4.x: 重扫已结算工件 → 走"复检"路径
        wp.status = 'pending_recheck'
    # 直接复用已有工件
else:
    wp = Workpiece(
        serial_no=serial_no,
        batch_id=current_batch_id,
        project_id=project_id,
        channel_id=channel_id,
        status='pending',
        ...
    )
    db.add(wp); db.commit()

# 关键状态: 把 wp.id 放进 _inspecting 字典等下个 cycle 结算
mes_hook._inspecting[channel_id] = wp.id
```

### 4.4 扫码后报警联动（"未绑码"告警）

```
ScannerService._on_data_received → 没有码到达
                                  ↓ external_device.weight_no_barcode_alarm_delay_sec 倒计时
                                  ↓ 倒计时到 → alarm_router.trigger_alarm("weight_no_barcode", ch)
                                  ↓ AlarmManager._send_command("alarm")  (Modbus 灯柱亮)
```

**v3.5.2 守门**：`mes_hook.is_warn_no_barcode()` + `has_any_scanner_present()`，没扫码器就不报警。

---

## 五、阶段 2：视频帧采集（capture_loop）

### 5.1 调用栈

```
[硬件: USB / RTSP / 海康 SDK / 视频文件]
  ↓
VideoSourceManager._capture_loop()       backend/api/source_capture_loop_mixin.py:25
  while self.is_running:
    ret, frame = self.capture.read()     ← 阻塞 IO (USB 1/30s, RTSP 视网络)
    if not ret:
        ↓ 重连机制 (CameraStartMixin.reconnect)
    ↓
    frame = self.video_transform.apply(frame)  ← rotate / mirror
    ↓
    with self.frame_lock:
        self.latest_frame = frame
        self.latest_frame_ts = time.time()
    ↓
    [if self.recording_enabled:]
        FFmpegRecorder.write_frame(frame)  ← session/cycle 录像
    ↓
    self.frame_count += 1
    [signal frame_event]                   ← inference_loop 等这个
```

**双缓冲架构**（关键事实）：
- `capture_loop` 主线程：纯**取帧 + 显示**（生成 MJPEG）
- `inference_loop` 副线程：**推理 + 步骤判定**

这两条线**不互锁等待**——推理慢于采集时，最新帧覆盖旧帧（**不堆积**）。所以 `frame_count` 和 `inference_count` 不是 1:1。

### 5.2 MJPEG 生成（同时为前端供帧）

```
@app.get("/video_feed")  backend/main.py:929
  vm = channel_manager.get(channel)
  return StreamingResponse(vm.generate_mjpeg(), ...)
  
VideoSourceManager.generate_mjpeg()  (在 source.py 主类)
  while True:
    frame = self.latest_frame.copy()              ← 拿最近帧
    frame = self.drawer.draw_box(frame, ...)      ← 叠加检测框 (Kalman 平滑)
    frame = self.mp_overlay.apply(frame)          ← MediaPipe 叠加 (如果启用)
    ret, jpg = cv2.imencode('.jpg', frame, ...)
    yield b'--frame\r\n...' + jpg.tobytes()
```

> ⚠️ MJPEG 流是**长连接**，前端 `<img src>` 一直挂着；600 次轮询（5 分钟）做一次 double-buffer swap 释放 Chromium 解码器内存（详见 `Monitor/index.vue:3085`）。

### 5.3 失败/异常分支

| 情况 | 处理 |
|---|---|
| `cap.read()` 返回 False（断流） | `CameraStartMixin.reconnect` 重试 N 次 |
| 海康 SDK 出错 | `source_industrial_camera_mixin` 重置回调 |
| RTSP 卡死（超过 retry） | mark `self.is_running = False`，前端 `/video_feed` 收到 EOF → 占位"No Source" 帧 |
| 视频文件播完 | 循环回到帧 0（如 project_config 设置 loop=True） |

---

## 六、阶段 3：模型推理（inference_loop）

### 6.1 调用栈

```
[副线程]
VideoSourceManager._inference_loop()       backend/api/source_inference_loop_mixin.py:103
  while self.is_running and self.is_detecting:
    if self.is_paused or self.is_standby:
        time.sleep(0.05); continue
    ↓
    frame, ts = self.latest_frame, self.latest_frame_ts
    ↓
    if frame is None or ts == self._last_inference_ts:  ← 节流 (避免重推同帧)
        time.sleep(0.01); continue
    ↓
    detection_results = self.inference_executor.infer(frame)
        ↓
        DetectRunnersMixin._detect_only(frame)     # 普通检测
          OR _detect_and_track(frame)             # 检测+跟踪
          OR _detect_segment(frame)               # 分割
        ↓
        results = self.model.predict(frame, conf=..., iou=..., imgsz=..., device=...)
        ↓ 转换为标准格式 + clip_bbox_normalized
        return [
          {"label": ..., "confidence": ..., "bbox": ..., "bbox_norm": ...},
          ...
        ]
    ↓
    self.latest_results = detection_results       ← 给 generate_mjpeg 画框
    ↓
    self.fps_counter.tick()                       ← 推理 FPS 统计
    ↓
    self._update_step_stats(detection_results)    ← 步骤统计聚合 (StepStatsMixin)
    ↓
    [触发 _check_modes 路由 → 见阶段 4]
```

### 6.2 关键参数 (来自 `Project.steps_config`)

```python
{
  "label": "缺角",
  "min_frames": 5,        # 至少连续命中 N 帧才算"出现"
  "max_frames": 30,       # 超过 N 帧仍命中算"长时间出现"
  "confidence": 0.7,      # 类别专属阈值（覆盖全局 conf）
  "max_count": 1,         # 该类别最多出现几次
}
```

**关键点**：`Project.pipeline_config.inference_mode` 决定走哪个 runner（detect / track / segment），**插件想加 runner 必须扩展 `DetectRunnersMixin`**。

---

## 七、阶段 4：步骤判定（settlement_mixin）

### 7.1 检查模式分发

```
[inference_loop 末尾]
  ↓
CheckModesMixin._check_current_mode(detection_results)   source_check_modes_mixin.py
  ↓ self.project_config['logic_mode']
  ┌─────────────────────────────────────────────┐
  │ 'box'        → ChecklistMixin._check_box     │
  │ 'sequential' → SequentialMixin._check_seq   │
  │ 'counting'   → ContainerGroupingMixin._check │
  │ 'events'     → EventsCheckMixin._check       │
  │ 'custom'     → SettlementMixin._settle_custom│
  └─────────────────────────────────────────────┘
```

### 7.2 步骤序列状态机（box 模式举例）

```python
self.current_step_idx     # 当前步骤索引
self.step_frame_count     # 当前步骤已命中帧数
self.step_history         # 已完成步骤列表

# 每帧：
if 当前步骤目标在 detection_results 里：
    self.step_frame_count += 1
    if self.step_frame_count >= step.min_frames:
        # 步骤通过 → 推进
        self.step_history.append({
            "idx": self.current_step_idx,
            "label": step.label,
            "frames": self.step_frame_count,
            "duration": time.time() - self.step_start_ts,
            "max_confidence": ...,
            "avg_confidence": ...,
        })
        self.current_step_idx += 1
        self.step_frame_count = 0
elif 距离 step_start_ts 超过 max_frames * (1/fps)：
    # 步骤超时 → 触发 NG
    self._trigger_event(2, f"步骤 {step.label} 超时")
```

### 7.3 周期性强制动作（v3.5.0+）

```python
PeriodicActionsMixin._maybe_run_periodic_action(cycle_count, project_config)
  for action in project_config['pipeline_config'].get('periodic_actions', []):
    if cycle_count % action['interval'] == 0:
        # 例: 每 10 个 cycle 强制 NG 一次（产线抽检）
        self._trigger_event(action['event_id'], "周期性强制动作")
```

**run_on_start** 标志：v3.5.0 末加的——是否第一轮就触发（默认 False，改成 True 可"开机首检"）。

---

## 八、阶段 5：周期结束 + 结算（end_cycle）

### 8.1 触发路径（按结算模式）

| 结算模式 | 触发时机 | 函数 |
|---|---|---|
| **顺序模式 (sequential)** | 完成最后一步 | `SequentialMixin._settle_sequential` |
| **盒装模式 (box)** | 容器满 | `CheckModesMixin._settle_box` |
| **计数模式 (counting)** | 累计达到 max_count | `CheckModesMixin._settle_counting` |
| **自定义事件** | `_trigger_event` 直接触发 | `SettlementMixin._settle_custom` |
| **scan_pair 配对** | 第二次扫码到达 | `services/mes_hooks._handle_scan_pair_event` |
| **强制结算** | 用户点 reset / 强制 NG | API `/source/detection/reset` |

所有路径最终统一调：

```
SessionLifecycleMixin.end_cycle(is_good, event_id, event_name, reason)   source_session_lifecycle_mixin.py:390
  ↓
  停录像 (FFmpegRecorder.stop_cycle_recording)
  ↓
  写 detection_cycles 表:
      cycle.end_time = now
      cycle.duration = end - start
      cycle.is_good = is_good
      cycle.event_id = event_id
      cycle.event_name = event_name
      cycle.result_reason = reason
  ↓
  写 step_records 表 (每个步骤一行)
  ↓
  更新 detection_sessions 累计:
      session.total_cycles += 1
      session.good_cycles += 1 if is_good
      session.ng_cycles  += 1 if not is_good
      session.avg_cycle_time = ...
  ↓
  Counters.handle_cycle_end(is_good, event_name)  source_counters.py
      counters['total'] += 1
      counters[event_name] = counters.get(event_name, 0) + 1
      [触发计数器持久化 → counters_snapshot.json]
  ↓
  ┌── 通知 MES Hook ──────────────────────────────┐
  │ self._mes_hook.on_cycle_end(                  │
  │   channel_id=self.channel_id,                 │
  │   cycle_id=cycle.id,                          │
  │   is_good=is_good,                            │
  │   event_name=event_name,                      │
  │   result_reason=reason,                       │
  │   duration=cycle.duration,                    │
  │   step_sequence=self.step_history,            │
  │   project_id=self.project_config['id'],       │
  │ )                                             │
  │   ↓ 入 _task_queue → 见阶段 6                 │
  └────────────────────────────────────────────────┘
  ↓
  ┌── 触发报警 ─────────────────────────────────┐
  │ if not is_good:                              │
  │   alarm_router.trigger_alarm(                │
  │     event_type=event_name or "ng",           │
  │     channel_id=self.channel_id)              │
  │   ↓ AlarmManager._send_command("alarm")      │
  │   ↓ Modbus 串口写 → 灯塔亮 + 蜂鸣器响        │
  │ alarm_router.start_idle_light(self.channel_id)│
  └──────────────────────────────────────────────┘
  ↓
  ┌── 通知前端（间接） ──────────────────────────┐
  │ self.event_history.append({...})              │
  │ self.recent_events.append({...})              │
  │   ↓ 下次 /detection/results 会带新事件        │
  │   ↓ 前端 6.7Hz polling 看到新事件 → toast    │
  └──────────────────────────────────────────────┘
  ↓
  开新 cycle (start_cycle) 或停留等待扫码
```

### 8.2 同步 vs 异步切分

**同步部分**（`end_cycle` 调用栈内完成）：
- DB 写入（detection_cycles / step_records / detection_sessions）
- 录像停止
- 计数器更新
- 报警触发（`trigger_alarm` 立即发 Modbus）

**异步部分**（入 `mes_hook._task_queue` 等 worker 处理）：
- 工件状态更新（`Workpiece.set_result`）
- 缺陷记录（`DefectService.create_record`）
- 工单计数（`WorkOrderService.update_counters`）
- 集群上报（`cluster_collector.report_cycle_end` → host）
- 实时导出规则触发（`dispatch_cycle_end_export`）
- MES Gateway 推送（在 `_handle_cycle_end` 末尾）

**关键时序约束**：异步部分**可能延迟数秒**，所以前端**马上**看到 cycle 数 +1，但**工件页**可能要等 1~2 个轮询周期才显示新工件。

---

## 九、阶段 6：MES Hook 异步处理（_handle_cycle_end）

### 9.1 worker 消费

```
[mes-hook-worker 线程]
task = self._task_queue.get(timeout=1.0)
func = self._handle_cycle_end (绑定的 args 已封)
func(db, channel_id, cycle_id, is_good, event_name, result_reason, duration, step_sequence, project_id)
```

### 9.2 完整链路（_handle_cycle_end，~150 行）

```python
def _handle_cycle_end(self, db, channel_id, cycle_id, is_good, event_name,
                     result_reason, duration, step_sequence, project_id):

    # === 1. 取出 _inspecting 工件 ===
    wp_id = self._inspecting.get(channel_id)
    # ⚠️ v3.4.2 hotfix-2 race: scan_pair 模式下 promote 可能已替换 wp_id

    # === 2. 工件状态写库 ===
    if wp_id:
        WorkpieceService(self).set_result(db, wp_id, is_good, cycle_id=cycle_id)
            # 写 wp.status = 'ok'/'ng', wp.latest_cycle_id, wp.last_inspect_at

    # === 3. 不良项缺陷记录 ===
    if not is_good and step_sequence:
        ng_steps = [s for s in step_sequence if s.get('is_ng')]
        for s in ng_steps:
            DefectService(self).create_record(
                db, cycle_id, workpiece_id=wp_id,
                defect_label=s['label'],
                defect_code=...  # 来自 defect_codes 表的映射
            )

    # === 4. 工单计数 ===
    work_order_id = self._get_active_work_order(channel_id, project_id)
    if work_order_id:
        WorkOrderService(self).update_counters(db, work_order_id, is_good)
            # 写 work_orders.qty_ok / qty_ng / qty_total

    # === 5. 集群上报（slave 角色） ===
    if self._cluster_role() == 'slave':
        cluster_collector.report_cycle_end(channel_id, cycle_id, ...)
            # HTTP POST 到 host_url + box_serial 聚集

    # === 6. 实时导出规则触发 ===
    try:
        dispatch_cycle_end_export(
            db,
            channel_id=channel_id,
            cycle_id=cycle_id,
            project_id=project_id,
            license_payload=self._license_cache,
        )
        # services/export_realtime.py:39
        # ↓
        # 扫 ExportRealtimeRule.is_enabled=True 且 trigger_event='cycle_end'
        # ↓
        # 按规则的 filter_config 过滤通道/项目
        # ↓
        # 调 build_cycle_context(db, cycle_id) 拿 308 字段渲染上下文
        # ↓
        # 调 render_to_file(template, context, output_path) 落盘
        # ↓
        # 写 ExportRunLog 状态
    except Exception:
        traceback.print_exc()  # 实时导出失败不阻塞主流程

    # === 7. MES Gateway 推送（standalone / host 角色） ===
    role = self._cluster_role()
    if role in ('standalone', 'host'):
        gateways = MESConnection.bound_to(channel_id)  # 按 bound_channels JSON 过滤
        for gw in gateways:
            mes_gateway.push(gw, payload={
                'cycle_id': cycle_id, 'is_good': is_good, ...
            })
            # 走 mes_adapters._REGISTRY[gw.adapter_type] (rest/form-data/...)
            # 重试 1 + retry_count, 失败写 mes_comm_logs
    elif role == 'slave':
        # slave 角色不推 MES, 等 host 聚齐 box 后由 host 统一推
        pass

    # === 8. _inspecting 清理 ===
    if wp_id:
        del self._inspecting[channel_id]
```

### 9.3 集群聚齐（host 角色，仅多机部署）

```
slave 节点的 _handle_cycle_end → cluster_collector.report_cycle_end → HTTP POST 到 host
                                                                                ↓
host 节点的 cluster.py 路由收 POST                                              │
                                ↓                                              │
                                cluster_collector.on_slave_report(payload)     │
                                  ↓                                            │
                                  写 BoxAggregation 表 (box_serial, station_id, status)
                                  ↓
                                  if 该 box_serial 所有 station 都 reported:
                                    self._on_box_complete(box_serial, summary)
                                      ↓
                                      写 BoxSummary 表
                                      ↓
                                      ★ 触发 MES Gateway 推送
                                        for gw in MESConnection.bound_to_cluster:
                                          gw.push(payload=summary)
                                      ↓
                                      ★ 触发实时规则 trigger_event='box_complete'
                                        (注: 当前 export_realtime 标 [todo] 还没接)
```

**超时策略**：`ClusterConfig.timeout_push=True` 时，超时未聚齐也强制按已收集到的部分推 MES（避免某 station 离线把整批数据卡死）。

---

## 十、阶段 7：实时规则 / 报警合成 / MES 推送

### 10.1 实时导出规则（export_realtime）

```
ExportRealtimeRule:
  id, name, template_id, filter_config (JSON), trigger_event, is_enabled
  
filter_config 格式:
  {
    "channels": [0, 1],
    "project_ids": [3],
    "is_good": [false],   # 只导 NG
    "event_names": ["缺角", "破损"],
  }

trigger_event 枚举:
  'cycle_end'     ✅ 已接 (services/mes_hooks.py:_handle_cycle_end 末)
  'session_end'   ❌ [todo] 未接, 见 services/export_realtime.py:13
  'box_complete'  ❌ [todo] 未接, host 聚齐时本应触发
```

### 10.2 报警合成（多通道共享灯柱）

```
alarm_router.trigger_alarm("ng", channel_id=2)  channel 2 触发 NG
  ↓
  alarm_router.get(2)  ← 看 channel 2 是否在 shared_with 列表
                        ← 如果是 (shared_with: [1, 2, 3]), 复用 channel 1 的 manager
  ↓
  AlarmManager.trigger_alarm("ng", channel_id=2)
    ↓ self._channel_events[2] = {"event": "ng", "ts": now, "duration": 3.0}
    ↓ self._recompose_and_apply()         ← 按优先级重算
        priority order: ng > weight_no_barcode > event2 > event1 > none
        winning_event = max(events, key=priority)
    ↓ self._send_command(commands_for[winning_event])  ← 单路 Modbus 写
    ↓ idle_light timer (3s 后回归到 idle 模式)
```

### 10.3 MES Gateway 推送（HTTP / Modbus）

```
mes_gateway.push(connection, payload)        services/mes_gateway.py
  ↓
  adapter = get_adapter(connection.adapter_type)   # rest / form-data / ...
  ↓
  rendered = adapter.render_template(connection.payload_template, payload)
      # 注: 不是 Jinja2! 自研 {key.path} 替换 + _array_source 数组展开
  ↓
  for attempt in range(1 + connection.retry_count):
      try:
          response = adapter.send(connection, rendered, headers=headers, auth=auth)
          if response.ok:
              break
      except Exception:
          time.sleep(retry_delay)
  ↓
  写 MESCommLog (request, response, duration, status)
```

**5 种 adapter 注册名 → 类**：

| 注册名 | 类 | 对外协议 |
|---|---|---|
| `rest` | `RESTAdapter` | HTTP JSON |
| `form-data` | `FormDataAdapter` | multipart/form-data，整 payload 序列化进 form 字段 |
| `form-urlencoded` | `FormUrlencodedAdapter` | application/x-www-form-urlencoded，顶层键值展平 |
| `query-string` | `QueryStringAdapter` | 全部走 URL query |
| `modbus_rtu` | `ModbusRTUAdapter` | RTU/TCP 写 Holding Register（pymodbus 3.13+） |

---

## 十一、阶段 8：前端展示（6.7Hz 轮询）

### 11.1 轮询主循环

```javascript
// frontend/src/views/Monitor/index.vue:3093-3237
const startPolling = () => {
  pollingTimer = setInterval(async () => {
    if (pollingInProgress) return;        // 重叠保护
    pollingInProgress = true;
    
    streamSwapCounter++;
    if (streamSwapCounter >= STREAM_SWAP_INTERVAL) {  // 600 次 = 5min
      // 双缓冲交换释放 Chromium 解码器内存
      streamSwapCounter = 0;
      // ... 切换 <img> 的 src 标记
    }
    
    try {
      const res = await getDetectionResults();   // GET /api/v1/source/detection/results?channel=0
      const data = res.data;
      
      // === 关键字段处理 ===
      // data.source_type / data.is_running / data.is_detecting / data.is_paused
      // data.fps_capture / data.fps_inference / data.frame_count
      // data.current_step / data.step_history
      // data.counters
      // data.recent_events  ← 新事件数组 (含未消费的)
      // data.recent_results ← 最近 N 帧的检测结果
      // data.session_stats  ← {total, good, ng, avg_cycle_time, ...}
      // data.workpiece      ← 当前 _inspecting 工件
      // data.scan_pair_active ← scan_pair 模式状态
      // data.cluster_status ← 集群心跳/角色
      
      // === 事件 toast ===
      data.recent_events.forEach(evt => {
        if (!shownEventIds.value.has(evt.id)) {
          shownEventIds.value.add(evt.id);
          ElMessage[evt.type === 'ng' ? 'error' : 'success'](evt.message);
          if (evt.audio_url) playAudio(evt.audio_url);  // 语音播报
        }
      });
      
      // === ECharts 更新 (节流 2s) ===
      if (now - lastChartUpdate > CHART_UPDATE_INTERVAL) {
        chartInstance.setOption({...});
      }
      
      // === 截图 (每秒) ===
      if (now - lastScreenshotUpdate > SCREENSHOT_UPDATE_INTERVAL) {
        // 拉 /snapshot 给截图组件
      }
      
    } catch {
      // 静默处理轮询错误
    } finally {
      pollingInProgress = false;
    }
  }, 150);  // ~6.7Hz
};
```

### 11.2 多通道并行轮询（多工位模式）

```javascript
// frontend/src/views/Monitor/index.vue:1648-...
const startMultiPolling = () => {
  multiPollingTimer = setInterval(async () => {
    if (multiPollingInProgress) return;
    multiPollingInProgress = true;
    const promises = [];
    for (let ch = 0; ch < channelCount.value; ch++) {
      promises.push(
        getDetectionResults(ch)
          .then(res => processChannelResult(ch, res.data))
          .catch(() => {})
      );
    }
    await Promise.all(promises);
    multiPollingInProgress = false;
  }, /* 间隔, 比单通道更长 */);
};
```

### 11.3 后端 `/detection/results` 数据装配

```python
# backend/api/source_routes.py:851
@router.get("/detection/results")
def get_detection_results(channel: int = Query(0)):
    mgr = _get_mgr(channel)  # ChannelManager.get(channel)
    return {
        "source_type": mgr.source_type,
        "is_running": mgr.is_running,
        "is_detecting": mgr.is_detecting,
        "is_paused": mgr.is_paused,
        "is_standby": mgr.is_standby,
        "fps_capture": mgr.fps_counter.get_capture_fps(),
        "fps_inference": mgr.fps_counter.get_inference_fps(),
        "frame_count": mgr.frame_count,
        "inference_count": mgr.inference_count,
        "current_step": mgr.current_step_idx,
        "step_history": mgr.step_history,
        "counters": mgr.counters.snapshot(),
        "recent_events": mgr.recent_events[-20:],
        "recent_results": mgr.latest_results,
        "session_stats": _aggregate_session(mgr.current_session_id),
        "workpiece": _query_inspecting_workpiece(channel),
        "scan_pair_active": mgr._mes_hook.is_scan_pair_active(channel) if mgr._mes_hook else False,
        "cluster_status": _query_cluster_status() if cluster_collector.role != 'standalone' else None,
        "operator": _query_current_operator(),
        "tracking": mgr.tracking_state if mgr.tracking_enabled else None,
        ...更多字段...
    }
```

### 11.4 前端到屏幕的最后一公里

```
Monitor/index.vue 收到 res.data
  ↓ 各 ref 更新 (Vue 响应式)
  ↓
  画面叠加层:
    <img src="/video_feed?channel=0&_v={swapCounter}"> ← MJPEG, 双缓冲
    <canvas ref="detectionCanvas"></canvas>           ← 前端再画一层（高亮 / 中文标签）
  ↓
  指标 panel:
    {{ data.fps_capture }} / {{ data.fps_inference }}
    {{ data.session_stats.total }} / good / ng
    {{ data.counters }}
  ↓
  ECharts 趋势图 (每 2s 更新)
  ↓
  事件 toast (新事件 ID 去重)
  ↓
  语音播报 (browser <audio>)
  ↓
  侧边状态条 (BottomBar.vue)
```

---

## 十二、关键时序约束 + 异常分支汇总

### 12.1 强时序约束（违反会导致 bug）

| 约束 | 来自 | 违反后果 |
|---|---|---|
| `OPENCV_FFMPEG_CAPTURE_OPTIONS=threads;1` 在 cv2 import 前 setdefault | v3.1.3 | libavcodec 断言 abort 整个 worker |
| `_inspecting[channel_id]` 写入与 cycle_end 取出之间不能有 promote race | v3.4.2 hotfix-2 | scan_pair 模式下结果错写到下一码 |
| `channel_manager.set_channel_count` 必须配套 `mes_hook.on_channel_removed` + `alarm_router.on_channel_removed` | 永久不变量 | MES dict 残留 / 串口未释放 / 灯塔常亮 |
| 关机时必须按 8 步顺序：stop_detection → save_counters → end_cycle → end_session → stop_recording → release_camera → release_model → cleanup | electron/main.js + main.py:shutdown_step | 灯塔不灭 / 录像断尾 / 计数器丢 |
| `_handle_scan_received` 入队 critical=True | v3.x | 关键事件队列满时丢失 |

### 12.2 异常分支汇总

| 阶段 | 异常 | 处理 |
|---|---|---|
| 扫码 | 设备断网 | listen 线程 retry，连接对象保持 |
| 扫码 | 重码 | dedup_interval_sec 内静默丢弃 |
| 扫码 | 测试态 | testing_ips 集合命中静默丢弃 |
| 扫码 | 工位禁用 | `is_channel_scan_disabled` 二次防御 |
| 推理 | 模型 OOM | `inference_executor` 抛异常被捕获，跳本帧 |
| 推理 | TRT engine imgsz 不匹配 | `ModelLoadMixin._detect_engine_imgsz` 多级探测 |
| 步骤 | 超时 | 触发 NG（event_id=2） |
| Cycle 结束 | DB 写失败 | retry 一次后让用户重启检测 |
| MES Hook | 队列满 | critical=True 等待入队，否则丢 |
| MES Hook | worker 异常 | spill 到 disk，下次启动 `_drain_spill_once` 回放 |
| 集群 slave 上报 | host 不可达 | 重试 N 次后 spill |
| 集群 host 聚齐 | 部分站点未上报 | timeout_push=True 时强制推 |
| 实时规则触发 | 渲染失败 | 写 ExportRunLog status=failed，不抛 |
| MES Gateway | 推送失败 | retry 1+retry_count，最后写 mes_comm_logs |
| 报警串口 | 写失败 | watchdog 重连 |
| 前端轮询 | 网络错 | 静默重试下个 tick |
| 前端 MJPEG | 流断 | 占位 "No Source" 帧 + 5min 双缓冲交换 |

---

## 十三、★ 给插件系统看的"插入点 13 个具体位置"

> 这一节直接告诉你：插件想做 X，应该挂到哪里。和 `01_module_map.md` 的"挂载点候选"互补——那边是**类型分档**，这里是**具体业务流位置**。

### 插入点 P1：扫码事件后处理（`_on_data_received` 后）

- **位置**：`services/scanner.py:1719` `_on_data_received` 末尾
- **可挂事件**：解析后的条码（含 `serial_no`, `channel_id`, `device_id`, `scan_mode`）
- **典型用例**：客户要把扫码事件实时推第三方 ERP / 自定义 deduplication / 扫码统计
- **现状**：硬编码调用 `mes_hook.on_scan_received` + `_inject_barcode_to_external_devices`，无 listener registry
- **改造代价**：低（在调用前后加 `for listener in self._scan_listeners: listener(event)`）

### 插入点 P2：MESHook 入队前过滤（`_enqueue` 之前）

- **位置**：`services/mes_hooks.py:381` `on_scan_received` / `on_cycle_end` 等
- **可挂事件**：所有要进 worker 队列的任务
- **典型用例**：客户要拒绝特定项目的事件 / 加优先级 / 去重
- **现状**：硬编码 `_enqueue`，无 pre-hook
- **改造代价**：低（加 `if not self._allow(event): return`）

### 插入点 P3：MESHook worker 处理前后（critical event hook）

- **位置**：`services/mes_hooks.py:230` `_worker_loop` 内 task 拿到后
- **可挂事件**：每个被消费的任务
- **典型用例**：插件要在工件落库前修改字段 / 工件落库后做副作用
- **现状**：硬编码 `func(*args, **kwargs)`
- **改造代价**：中（要给 task 类型分类，pre/post 分开）

### 插入点 P4：模型推理 runner 注册（DetectRunner registry）

- **位置**：`api/source_detect_runners_mixin.py` `_detect_only` / `_detect_and_track` / `_detect_segment`
- **可挂事件**：每帧推理选择 runner
- **典型用例**：客户用自家专用模型（非 YOLO），需要自定义推理路径
- **现状**：硬编码 if/elif 三种 runner
- **改造代价**：中（改成 `RUNNERS = {"detect": ..., "track": ..., "segment": ...}` registry）

### 插入点 P5：步骤判定结果（`_check_current_mode` 后）

- **位置**：`source_check_modes_mixin.py` `_check_current_mode` 末尾
- **可挂事件**：每帧步骤判定的 (step_idx, label, hit, confidence) 元组
- **典型用例**：客户要做"工艺过程实时记录" / 自家步骤逻辑覆盖
- **现状**：硬编码 4 种检查模式 + 5 种结算模式
- **改造代价**：高（步骤判定与结算耦合紧）

### 插入点 P6：`_trigger_event` 中心 hook（事件触发前后）

- **位置**：`source_event_trigger_mixin.py:19` `_trigger_event(event_id, reason)`
- **可挂事件**：所有事件触发（含 OK / NG / 自定义）
- **典型用例**：插件想"记录事件 / 联动外部 / 拒绝事件 / 修改 reason"
- **现状**：硬编码触发 → 写库 → 联动报警
- **改造代价**：低（加 `for h in self._event_hooks: h(event_id, reason)` 即可）

### 插入点 P7：cycle 开始前（`start_cycle`）

- **位置**：`source_session_lifecycle_mixin.py: start_cycle`
- **可挂事件**：每周期开始时
- **典型用例**：插件要"周期前自动校对工件 / 抓配置快照 / 生产计数"
- **现状**：硬编码 + 录像启动
- **改造代价**：低

### 插入点 P8：cycle 结束（`_handle_cycle_end` 8 个子步骤）

- **位置**：`services/mes_hooks.py:1181` `_handle_cycle_end`
- **可挂事件**：周期结束时 8 个步骤每一个都可挂
- **典型用例**：插件要"按客户协议自定义工单计数规则 / 把 cycle 结果推自家系统"
- **现状**：8 步硬编码顺序
- **改造代价**：中（把 8 步拆成 phase 列表，phase 之间允许 plugin 挂前后）

### 插入点 P9：实时规则的 trigger_event 扩展

- **位置**：`services/export_realtime.py:13` (todo 标记的 `session_end`/`box_complete`)
- **可挂事件**：插件想加新触发器名（如 `defect_threshold`, `shift_change`）
- **典型用例**：客户要"班次结束按规则导出 / 缺陷率超阈值导出"
- **现状**：注释里 [todo]，只有 `cycle_end` 实际接入
- **改造代价**：低（加 `register_trigger("name", dispatcher_fn)` registry）

### 插入点 P10：MES Adapter 注册（已有机制）

- **位置**：`services/mes_adapters/__init__.py: _REGISTRY`
- **可挂事件**：客户要新协议
- **典型用例**：MQTT / OPC UA / 客户自家 RPC
- **现状**：5 种 adapter，已经是 registry 模式
- **改造代价**：极低（直接 `_REGISTRY["my_proto"] = MyAdapter`）

### 插入点 P11：cluster `box_complete` 后处理

- **位置**：`services/cluster_collector.py: _on_box_complete`
- **可挂事件**：host 聚齐 box 时
- **典型用例**：插件要在 box 完成时做"汇总写自家系统 / 触发实时规则 / 报警"
- **现状**：硬编码推 MES Gateway，未触发实时规则（[todo]）
- **改造代价**：中（要顺便补 P9 的 trigger 扩展）

### 插入点 P12：报警事件路由（`alarm_router.trigger_alarm` 拦截）

- **位置**：`api/alarm.py:678` `AlarmRouter.trigger_alarm`
- **可挂事件**：所有报警触发
- **典型用例**：插件加自家声光设备 / 微信/钉钉推送
- **现状**：硬编码走 Modbus
- **改造代价**：低（加 `for h in self._alarm_hooks: h(event_type, ch)`）

### 插入点 P13：前端 `/detection/results` 字段扩展

- **位置**：`api/source_routes.py:851`（后端字段装配）+ `views/Monitor/index.vue:3093`（前端处理）
- **可挂事件**：每次 6.7Hz 轮询返回的字段
- **典型用例**：插件想在 Monitor 页加自家面板
- **现状**：字段硬编码 dict 返回
- **改造代价**：中（前端字段处理也要插件能写 Vue 组件，对应**前端档位 2** 动态组件）

---

## 十四、用一张图总结

```
[硬件层]         [扫码 LON/WMax]   [USB/RTSP/海康]   [Modbus 串口]   [外部MES]
   ↓                  ↓                  ↓                ↑              ↑
   ↓             ScannerService      VideoSourceMgr    AlarmRouter   MESGateway
   ↓               线程×N             capture_loop       共享灯柱      5 适配器
   ↓                  ↓                inference_loop      ↑              ↑
   ↓                  ↓             _trigger_event ────────┘              │
   ↓                  ↓                  ↓                                │
   ↓             dedup/parse         settle_*/end_cycle                   │
   ↓                  ↓                  ↓                                │
   ↓                  ↓ ◄──────── P1 P6 P7 P8 P11 P12                    │
   ↓                  │                                                  │
   ↓             mes_hook.on_scan_received  /  on_cycle_end             │
   ↓                  │      ↓ enqueue _task_queue                      │
   ↓                  │      ↓                                          │
   ↓                  │   [worker line]                                  │
   ↓                  │      ↓                                          │
   ↓                  └→  _handle_cycle_end ─────────┬────────────────┘
   ↓                          ↓ 8 phases             │
   ↓                          ├ Workpiece.set_result │
   ↓                          ├ DefectService        │
   ↓                          ├ WorkOrderService     │
   ↓                          ├ ClusterCollector ────┼─ slave→host→box_complete (P11)
   ↓                          ├ dispatch_realtime ───┴─ ExportRealtimeRule (P9)
   ↓                          └ MESGateway.push      ⮕ MES adapter registry (P10)
   ↓                          
[DB 层]      detection_sessions / cycles / step_records / video_clips
            workpieces / defect_records / work_orders / scan_logs
            mes_connections / mes_comm_logs / cluster_config / box_aggregations
            export_templates / export_realtime_rules / export_run_logs
            
[前端层]                  [6.7Hz polling]
                               ↓ /api/v1/source/detection/results?channel=N
                          Monitor/index.vue
                               ↓ res.data 字段处理 (P13)
                          ECharts + ElMessage + canvas + <img video_feed>
```

---

**本文最后更新**：2026-05-08
**事实校验**：基于 v3.6.0 源码（含 source_event_trigger_mixin / mes_hooks / cluster_collector / scanner / Monitor.vue）+ AGENTS.md Hub 关系图交叉验证
