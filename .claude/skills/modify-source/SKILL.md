---
name: modify-source
description: "安全修改 source.py 的前置分析：列出所有调用者、线程访问点、状态变量依赖链、hotfix/patch覆盖点。修改VideoSourceManager前必须先用这个skill进行影响分析。"
argument-hint: "[计划修改的功能或方法名]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# modify-source: source.py 安全修改分析

你正在帮用户安全修改 `backend/api/source.py`（v2.7.x 全修后 **~1525 行**，曾经 8600+ 行，已通过 P6 mixin + P7 组合拆分）。
**这仍是整个系统最危险的文件，任何修改前必须完成以下分析。**

## 当前拆分结构（v2.7.x 全修后）

```
source.py (1525L)                       -- VSM 主类骨架 + 共享方法
├── source_capture_loop_mixin.py        -- _capture_loop 主循环
├── source_camera_start_mixin.py        -- start_camera/rtsp/hikvision/hcnetsdk/video/image
├── source_lifecycle_mixin.py           -- 启停/资源清理
├── source_session_lifecycle_mixin.py   -- start/end_session, start/end_cycle, record_step
├── source_inference_loop_mixin.py      -- 推理线程主循环 + fps_inference 统计
├── source_detect_runners_mixin.py      -- _detect_only/_detect_and_track/_detect_segment
├── source_recording_api_mixin.py       -- 录像公共 API
├── source_recording_thread_mixin.py    -- 录像后台线程
├── source_recording_mixin.py           -- 录像绘图/Kalman 辅助
├── source_check_modes_mixin.py         -- 聚合 mixin（4 个子 mixin 多继承）
│   ├── source_container_grouping_mixin.py
│   ├── source_checklist_mixin.py
│   ├── source_events_check_mixin.py
│   └── source_sequential_mixin.py
└── 组合（不是 mixin，是 self.xxx 组件）：
    ├── source_drawer.py                -- Drawer (画框/文字)
    ├── source_mediapipe.py             -- MediaPipeOverlay
    ├── source_counters.py              -- Counters
    ├── source_video_transform.py       -- VideoTransform (旋转/翻转)
    └── source_inference_executor.py    -- InferenceExecutor (推理线程池)
```

**修改前先用 Grep 在所有 `source_*.py` 中搜索方法名，定位到真正的实现文件。**

计划修改: $ARGUMENTS

## 强制分析流程

### 第1步: 定位要修改的代码

1. 用 Grep 搜索相关函数名/变量名在 source.py 中的位置
2. 用 Read 读取该函数的完整代码（包括周围上下文）
3. 确认该代码属于哪个子系统:
   - 视频采集 (start_xxx, _capture_loop)
   - 推理管线 (load_model, inference相关)
   - 状态机 (步骤判定, cycle管理)
   - 会话管理 (session/cycle/step CRUD；**`start_session()` / `start_cycle()` 会写入当前操作员的 `operator_id`**，与 `operators` 当前操作员状态一致)
   - 录像 (FFmpegRecorder)
   - MediaPipe
   - 设备配置 (device_config)

### 第2步: 追踪所有调用者

在以下文件中搜索被修改函数/变量的所有引用:

```
必查文件:
  backend/api/source*.py         -- VSM 主类 + 11 个 mixin/组件文件，全部 grep
  backend/api/channel_manager.py -- 多通道管理器调用
  backend/main.py                -- 启动/关机/video_feed/MES初始化（含 video_feed/snapshot 直接挂在 app 上）
  backend/hotfix.py              -- 运行时猴子补丁！
  patches/session_fix_patch.py   -- 会话修复补丁！
  backend/services/mes_hooks.py  -- MES Hook 回调；周期/会话结束处调用 MESGateway.dispatch 推送外部 MES

可能相关:
  backend/api/sessions*.py       -- 如果涉及Session/Cycle（已拆 4 个文件）
  backend/services/scanner.py    -- 扫码器（通过 mes_hooks 间接依赖 source）
  frontend/src/api/detection.js  -- 前端检测API
  frontend/src/views/Monitor/index.vue -- 监控页（含 MES 信息条）

❌ 已删除（看到引用立刻清理）:
  backend/api/detection.py       -- 已删
  backend/services/detector.py   -- 已删
```

### 第3步: 检查线程安全

确认要修改的变量在哪些线程中被访问:

| 线程 | 入口函数 | 主要变量 |
|------|----------|----------|
| 采集线程 | `_capture_loop()` | current_frame, is_streaming |
| 推理(在采集线程内) | 推理分支 | latest_detections, current_cycle_steps, counters |
| API handler | FastAPI async | is_detecting, session_id, cycle_id |
| 录像线程 | FFmpegRecorder | 录像状态 |
| 报警线程 | AlarmManager | 报警触发 |
| MES Hook 线程 | MESHookManager._worker_loop | _pending_workpiece, _inspecting_workpiece, _active_orders |

**规则:** 如果变量跨线程访问且不是简单的 bool/引用替换，需要加锁或使用线程安全的数据结构。

### 第4步: 检查 hotfix/patch 覆盖

**关键！以下方法被运行时补丁替换:**
- `start_rtsp()` → 被 `hotfix.py:_patch_start_rtsp()` 替换
- `resume_inference()` → 被 `patches/session_fix_patch.py` 包装
- `start_detection()` → 被 `patches/session_fix_patch.py` 包装
- `resume()` → 被 `patches/session_fix_patch.py` 包装

如果修改了这些方法的签名或语义，补丁可能静默失效。

### 第5步: 检查 _init_inference_vars

如果添加/修改了状态变量，**必须确认** `_init_inference_vars()` 是否需要同步更新。
遗漏初始化 = 上一次的状态残留 = 难以复现的 bug。

### 第6步: 检查 MES Hook 集成点

source.py 中有 5 个 MES Hook 调用点（v2.3.0+），修改以下方法时必须确认 Hook 不会受影响:
- `start_session()` 后 → `self._mes_hook.on_session_start()`
- `end_session()` 后 → `self._mes_hook.on_session_end()`
- `start_cycle()` 后 → `self._mes_hook.on_cycle_start()`
- `end_cycle()` 后 → `self._mes_hook.on_cycle_end()`
- `get_detection_results()` 返回前 → 注入 `result['mes']`，并返回当前周期/会话关联的 **操作员信息**（供 Monitor 等展示）

**外部 MES（Gateway）：** `mes_hooks.py` 在 `_handle_cycle_end`、`_handle_session_end` 处理完内置 MES 逻辑后，会调用 `MESGateway.dispatch()`（若网关已初始化且配置满足），经 `mes_adapters` 向外部系统推送。修改 `end_cycle`/`end_session` 的时序或 Hook 触发条件时，需评估外部推送上下文是否仍完整。

**规则:** MES Hook 全在 try/except 中，不会影响检测流程，但修改函数签名/返回值时需注意。

### 第7步: 影响范围评估

生成影响报告:
```
修改内容: [具体描述]
影响的线程: [列出]
影响的调用链: [列出每个调用者]
hotfix/patch 兼容性: [是否需要同步修改]
_init_inference_vars 影响: [是否需要更新]
DB 影响: [是否影响 Session/Cycle/Step 写入]
MES Hook 影响: [是否影响 5 个 Hook 调用点]
前端影响: [是否影响API返回格式]
风险等级: [低/中/高/极高]
建议测试: [具体测试步骤]
```

## 修改原则

1. **最小改动原则:** 只改必要的代码，不做顺手重构
2. **保持接口兼容:** 不改函数签名，除非所有调用者同步修改
3. **注意补丁兼容:** 修改被补丁覆盖的方法时，同步检查补丁
4. **初始化同步:** 新增状态变量必须加入 `_init_inference_vars()`
5. **线程安全:** 跨线程变量使用原子操作或加锁
6. **DB事务:** 涉及多个DB操作时使用事务
7. **异常处理:** 不要用 bare except，至少 log 异常信息
8. **mixin 顶层 import 自查:** 改动 `source_*_mixin.py` 后，必须搜该文件中用到的 `os/cv2/threading/uuid/datetime/platform/hik_log/debug_log` 等是否在顶部 `import`。mixin 拆分后曾多次出现 `NameError: name 'xxx' is not defined`（v2.7.x 修了 5 个）。可跑 `python tools/check_imports.py` 静态扫一遍

## 高危操作清单

以下修改需要**特别谨慎**:
- 修改 `_capture_loop()` → 影响所有视频源和推理
- 修改 `set_project_config()` → 影响所有检测模式
- 修改 `start_cycle()` → 影响数据记录完整性。⚠ 不要在此函数中清空 `backup_steps_seen_in_cycle`，替补步骤可能在周期正式开始前就被检测到
- 修改 `end_cycle()` / 结算函数 → 影响数据记录完整性。结算函数负责清空 `backup_steps_seen_in_cycle`
- 修改 `_update_step_stats()` → 影响替补步骤检测（backup_in_detected 处理在此函数内）
- 修改 `_inject_backup_steps()` → 影响替补步骤注入到周期的逻辑
- 修改 `record_step()` → 影响步骤统计
- 修改 `load_model()` → 影响模型加载链路
- 添加/删除实例变量 → 必须同步 `_init_inference_vars()`
- 修改任何被补丁覆盖的方法 → 必须同步检查补丁

## 步骤去重规则（绝对不可违反！）

**去重间隔（max_interval）只允许"上一步和这一步相同"时才生效。**

- A-A（连续相同）→ 去重，不重复记录
- A-B-A（中间有其他有效步骤）→ 不去重，第二个 A 必须记录，结算时判 NG（步骤回退）
- **禁止使用 `if label not in self.current_cycle_steps` 做全局去重！** 此 bug 多次复发，必须杜绝
- 非连续重复时，步骤消失由 disappear_delay 确认
- 正确做法：只拦截 `last_added_step == label`（连续重复），其余必须进入周期

## 已移除的机制（不要恢复）

- **`_just_settled` 标志**（2026-04移除）: 原本在结算后阻止非首步启动新周期，但会导致 NG 后所有步骤被永久拒绝。现在依靠 `min_duration`/`min_frames` 过滤误检

## 传动杆误判过滤（v2.7.6 新增，`backend/api/rod_filter.py` + `source.py` 6 处 hook）

- **背景**：`best(7).pt` 把空箱+泡沫槽中间的黑色横缝稳定识别成"传动杆" conf 0.88~0.92，per-class conf 阈值方案走不通（误判分布比真阳性更高）。推理端两层过滤是主力解，和后续重训模型叠加使用，不撤。
- **模块**：`backend/api/rod_filter.py`
  - `filter_rod_by_companion(dets, iou_thr, rod_label, companion_labels)`：同帧 rod 必须和任一 companion (`大/小框架/侧板`) 满足 `center-in-bbox OR IoU≥thr`
  - `RodSessionGate`：当前周期从未见过 `大/小框架` 时抑制所有 rod；`update_and_filter()` + `reset()`
  - `read_rod_filter_config(project_config)`：兼容两种写法。**推荐：`project_config["pipeline_config"]["rod_companion_filter"]`**（v2.7.8 起前端 Project 页保存路径）；**兼容：`project_config["rod_companion_filter"]`**（v2.7.6 顶层写法，直接调 `/detection/set-project` 时可用）。优先读 pipeline_config，再回退到顶层。**缺字段全按 OFF**，老项目零影响
  - ⚠ **v2.7.6 坑**：当时只支持顶层读取，但 `main.py::_build_project_config` 根本不把这俩字段放到顶层（只放 pipeline_config / data_config / ...），所以 v2.7.6 上即使手动改 DB 也读不到开关。v2.7.8 改 `read_rod_filter_config` 同时兼容 pipeline_config 后才真正生效
- **在 source.py 的 6 处 hook**：
  1. `__init__` 里挂 `self._rod_gate` + `self._rod_filter_cfg`
  2. `set_project_config` 里调用 `read_rod_filter_config` 重新读开关并重建 gate
  3. `_apply_rod_filters(detections)` helper 统一封装两层调用（企业级过滤异常都捕获，**绝不挡主流程**）
  4. `_detect_only` / `_detect_and_track` / `_detect_segment` 三个推理入口 `return detections` 前一律 `return self._apply_rod_filters(detections)`
  5. `start_cycle` 的"真正落地点"（`self.current_cycle_id = cycle.id` 之后）调 `self._rod_gate.reset()`
  6. `stop_detection` 开头调 `self._rod_gate.reset()`
- **修改规则**：
  - 过滤按 **label 字符串** 匹配，不硬编码 class_id。改模型 / 类名重映射时**不会炸**
  - dets 坐标必须是归一化 (0-1)；`filter_rod_by_companion` 内部用归一化 xyxy 计算 IoU 和中心点
  - 新增"其他类别也要做类似过滤" → 复用 `filter_rod_by_companion` 的思路做另一层，**不要塞进 rod_filter.py**，防止 label 耦合
  - 默认全关是强约束；若要改默认值先考虑向后兼容影响
- **验证产物**：`tune_rod_conf/` 目录（analyze_rod.py / verify_filter.py / out/samples/）

## 画面变换（v2.7.5 新增）

- `VideoSourceManager` 三个字段：`video_rotation (0/90/180/270)` / `video_flip_h` / `video_flip_v`
- `_apply_frame_transform(frame)`：`cv2.rotate` + `cv2.flip` 组合，仅支持 90°倍数旋转
- **应用位置**：`_capture_loop` 里 `original_frame = frame.copy()` **紧跟下一行**。必须在所有消费者（推理 / MJPEG / 录像 / 快照）之前，否则检测框坐标和画面会错位
- **坐标对齐**：因为变换发生在所有消费者之前，下游拿到的都是变换后帧，检测框直接正确，**严禁**在前端或推理后再对检测框做二次旋转/翻转
- **per_channel 持久化**：`_save_device_config` 写 `device_config.json` 的 `per_channel[str(channel_id)]` 子键，读写时**必须先读旧配置再合并**，否则会覆盖其它通道
- **修改规则**：
  - 添加新的变换（如缩放、任意角度旋转）→ 先评估坐标映射成本，如仍能保持 OpenCV 零拷贝优先
  - `_apply_frame_transform` 调用频次 = 帧率，必须快；如果要 GPU 加速要走 `cv2.cuda`
  - 对 None frame 必须安全（返回 None）

## fps_inference 与"秒→帧"换算 (v2.7.13 新增)

- `self.fps_actual` = **采集线程**的 FPS（`_capture_loop` 每读一帧 +1），典型 25~37
- `self.fps_inference` = **推理线程**的 FPS（`_inference_loop` 每跑一次模型 +1），受 GPU/模型限制常 5~8
- 两者**独立统计**，不要混用
- **强制规则**：任何在 `_inference_loop` 里累加的帧计数（tracking lost/gone、event tolerance、step dedup 等）所对应的"秒→帧"换算，**必须**用 `max(self.fps_inference, 10)`
  - 用 `fps_actual` 会放大 `fps_actual / fps_inference` 倍，典型 3~6 倍延迟
  - 必须加 `max(..., 10)` 兜底，`fps_inference` 启动瞬间为 0
- 历史错误：`item_lost_frames` / `tolerance_frames` / `track_buffer` 曾全部用 `fps_actual`，v2.7.13 全部改为 `fps_inference`
- **不要**为 tracking 模式加类似 `min_cycle_age = max(max_lost_sec, 1.0)` 的秒级硬兜底，这等于把用户小于 1 秒的配置强行拉到 1 秒
- API 层已在 `get_detection_results` / `get_source_status` / `get_manager_config` 透出 `fps_inference`，前端可展示
- 新增"秒"类参数时，在 UI 文案里可以标注"按推理速度换算"，让用户理解慢 GPU 下的表现
