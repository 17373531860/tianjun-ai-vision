---
name: debug-source
description: "诊断 source.py VideoSourceManager 的问题：检测状态机、线程安全、Cycle/Step 生命周期、推理管线。当遇到检测逻辑异常、步骤判定错误、周期不结束、计数器不对时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# debug-source: VideoSourceManager 诊断

你正在诊断 **天军AI视觉检测系统** 的核心文件 `backend/api/source.py`（约8600行）。
这是整个系统最复杂、最脆弱的文件，包含 `VideoSourceManager` 上帝对象。

用户问题: $ARGUMENTS

## 文件结构地图

```
backend/api/source.py (~8616 行)
├── 模块级工具函数
│   ├── debug_log(), hik_log()          -- 条件调试日志
│   ├── get_ffmpeg_path()               -- FFmpeg 路径解析
│   ├── get_hikvision_device_list()     -- 海康工业相机枚举
│   └── _decode_hik_string()            -- ctypes 字符串解码
├── FFmpegRecorder 类                    -- 视频录制（FFmpeg子进程管道）
├── KalmanFilter2D 类                    -- 8状态卡尔曼滤波（检测框平滑）
└── VideoSourceManager 类 (~8000 行)     -- 上帝对象
    ├── __init__()                       -- 基础初始化
    ├── _load_device_config()            -- 读 device_config.json
    ├── _init_inference_vars()           -- 初始化 ~100+ 推理状态变量
    ├── set_project_config()             -- 解析项目配置到内部状态
    ├── load_model()                     -- 加载 YOLO 模型
    ├── _capture_loop()                  -- 主采集线程（读帧→推理→录制）
    ├── _inference_thread               -- YOLO推理（在_capture_loop内部）
    ├── start_session() / end_session()  -- 会话生命周期
    ├── start_cycle() / end_cycle()      -- 周期生命周期
    ├── record_step()                    -- 写入步骤记录到DB
    ├── _reconcile_step_records()        -- 对齐DB步骤记录与实际检测
    ├── _force_timeout_ng()              -- 超时NG判定
    ├── _auto_split_session()            -- 自动分割长会话
    ├── 视频源启动方法
    │   ├── start_camera()              -- USB摄像头
    │   ├── start_hikvision()           -- 海康工业相机
    │   ├── start_rtsp()                -- RTSP流（被hotfix.py猴子补丁）
    │   ├── start_hcnetsdk()            -- 海康NVR SDK
    │   ├── start_video()               -- 视频文件
    │   └── start_image()               -- 图片
    ├── MediaPipe
    │   ├── _init_mediapipe()
    │   ├── _release_mediapipe()
    │   └── _apply_mediapipe_overlay()
    └── 模型管理
        └── _release_model()             -- 释放GPU显存
```

## 4种检测模式的状态机

### 1. 顺序模式 (sequential)
- `current_step_index` 追踪当前期望步骤
- 必须按 `step_sequence` 顺序完成
- `_get_expected_sequence_labels()` 返回当前期望标签
- 陷阱: `backup_step` 配置可以允许替代步骤

### 2. 检测模式 (detection)
- 无序，只检查所有步骤是否都出现过
- `current_cycle_steps` 记录已完成步骤
- 所有步骤完成 → 自动结束Cycle

### 3. 自定义模式 (custom)
- 基于 `custom_conditions` 优先级匹配
- 包含子序列匹配逻辑
- `base_mode` 指定底层模式（sequential/detection）

### 4. 追踪模式 (tracking)
- 物品计数，带 ID 跟踪
- `tracked_items` 字典存储每个物品状态
- `container_mode` 支持容器内物品管理
- `cycle_end_strategy` 控制何时结束周期

## 关键状态变量（线程安全风险）

以下变量从多个线程访问，**缺少锁保护**：

| 变量 | 写入线程 | 读取线程 | 风险 |
|------|----------|----------|------|
| `current_cycle_steps` | 推理线程 | API handler | dict 并发修改 |
| `counters` | 推理线程 | API handler, end_session | dict 并发读写 |
| `step_start_time` | 推理线程 | 推理线程(不同分支) | 竞态条件 |
| `is_detecting` | API handler | 采集线程 | bool, 较安全 |
| `current_frame` | 采集线程 | MJPEG handler | numpy 数组替换 |
| `latest_detections` | 推理线程 | API handler | list 替换 |
| `session_id` / `cycle_id` | 推理线程, API | 多处 | ID 不一致 |

## Cycle 生命周期关键路径

```
检测到第一个有效步骤
  → start_cycle() [创建DB记录, cycle_id赋值]
    ⚠ start_cycle() 不清空 backup_steps_seen_in_cycle
    （替补步骤可能在周期开始前被检测到，必须保留）
  → record_step() [每个步骤完成时]
  → 所有步骤完成 / 超时
    → _inject_backup_steps() [结算前注入替补步骤]
    → end_cycle() [计算OK/NG, 更新计数器, 写DB]
      → 清空 backup_steps_seen_in_cycle
      → 如果正在录制 → 分割录像文件
      → _reconcile_step_records() [对齐步骤记录]
  → 等待下一个周期开始
```

**替补步骤 (backup_steps) 生命周期：**
1. `_update_step_stats()` 中检测到替补标签 → 加入 `backup_steps_seen_in_cycle`，从 `detected_labels` 移除
2. 替补步骤不走 `_process_single_step()`，不触发 `start_cycle()`
3. 结算时 `_inject_backup_steps()` 检查：替补被看到 + 主步骤缺失 → 注入主步骤
4. 所有结算函数清空 `backup_steps_seen_in_cycle`

**常见bug模式：**
1. `cycle_id` 为 None 时 `record_step()` 被调用 → 步骤丢失
2. `end_cycle()` 中 DB 操作失败 → 计数器已更新但DB未写入
3. `_discard_empty_cycle()` 可能删除有效周期（如果步骤记录延迟写入）
4. 替补步骤在 `start_cycle()` 前被检测到 → 如果 `start_cycle()` 清空了 `backup_steps_seen_in_cycle` 则替补丢失（已修复）

## MES Hook 集成点 (v2.3.0+)

source.py 中注入了 `self._mes_hook` (MESHookManager)，在以下位置回调:
```
start_session() 后 → _mes_hook.on_session_start(channel_id, session_id, project_id)
end_session()   后 → _mes_hook.on_session_end(channel_id, session_id, ...)
start_cycle()   后 → _mes_hook.on_cycle_start(channel_id, cycle_id, session_id)
end_cycle()     后 → _mes_hook.on_cycle_end(channel_id, cycle_id, is_good, ...)
get_detection_results() → 注入 result['mes'] = {current_workpiece, active_order}
```
MES Hook 在独立线程中异步执行，使用独立 DB session，不会阻塞检测。
**如果看到 MES 数据不对但检测正常 → 问题在 mes_hooks.py 或 MES service 层，不在 source.py。**

## 诊断步骤

1. **先确定问题属于哪个子系统：**
   - 步骤判定错误 → 检查 `set_project_config()` 解析 + 对应检测模式的状态机逻辑
   - 周期不结束 → 检查 `end_cycle()` 触发条件 + `_force_timeout_ng()`
   - 计数器不对 → 检查 `counters` 读写 + `end_cycle()` 中的计数逻辑
   - 录像问题 → 检查 `FFmpegRecorder` + `_capture_loop()` 中的录制分支
   - 视频卡顿 → 检查对应 `start_xxx()` 方法 + `_capture_loop()` 的帧读取
   - MES 数据异常 → 检查 `backend/services/mes_hooks.py` + MES service 层（不在 source.py 内）

2. **读取关键文件：**
   - `backend/api/source.py` — 主逻辑（先搜索相关函数名，不要一次读全部）
   - `backend/hotfix.py` — 检查是否有运行时补丁覆盖了原方法
   - `backend/api/channel_manager.py` — 如果是多通道问题
   - `backend/models/models.py` — 数据模型定义
   - `backend/models/mes_models.py` — MES 数据模型（如涉及 MES 问题）
   - `backend/services/mes_hooks.py` — MES Hook 集成层（如涉及 MES 问题）

3. **检查 hotfix/patch 是否生效：**
   - `hotfix.py` 猴子补丁了 `start_rtsp()` 方法
   - `patches/session_fix_patch.py` 补丁了 `resume_inference()`, `start_detection()`, `resume()`

4. **排查线程问题：** 搜索变量的所有写入点，确认是否在同一线程内

## Tracking 模式物品过滤 (v2.5.0+)

- 只有 `expected_items` 中的物品才参与周期判定（触发新周期、计入 active_count、决定 OK/NG）
- 不在 `expected_items` 中的物品（如 box）可以启用检测框显示，但不影响任何计数或判定
- `_settle_counting_cycle` 中 `extra` 仅统计 expected_items 内的多余项
- 新周期只在检测到 expected_items 中的物品时触发

## MJPEG 流恢复 (v2.5.0+)

- `generate_mjpeg()` 不再因 `is_running=False` 立即退出，始终使用 `vm.generate_mjpeg()`
- 前端 `onStreamError` 重试阈值增到 50 次，防止改步骤设置后频繁断流导致黑屏
- 添加 stream health check 机制

## 已知陷阱

- `_just_settled` 机制已被移除（2026-04）。该标志原本在结算后阻止非首步启动新周期，但会导致 NG 后所有步骤被永久拒绝。现在依靠 `min_duration`/`min_frames` 过滤误检
- `start_cycle()` 不能清空 `backup_steps_seen_in_cycle`，否则结算前的替补检测会丢失
- `_init_inference_vars()` 初始化 ~100 个变量，reset 时如果遗漏任何一个，上一次的状态会残留
- `bare except:` 在多处静默吞掉异常（搜索 `except:` 定位）
- DB session 通过 `SessionLocal()` 手动创建而非 FastAPI 依赖注入，容易泄露
- `from ctypes import *` 污染命名空间
- `os._exit(0)` 绕过正常 Python 清理流程
- Tracking 模式：非 expected_items 中的物品不参与周期判定（v2.5.0 修复）
- `mes_hooks.py` 中 import 路径必须用 `from services.xxx` 而非 `from backend.services.xxx`，否则被静默吞掉
- **TensorRT imgsz 检测 (v2.6.0)**: `load_model` 使用4种方式探测 TRT 引擎的 imgsz（bindings、input_shape、get_tensor_shape、模型属性扫描），warm-up 捕获 `AssertionError` 自动修正 imgsz
- **推理连续错误保护 (v2.6.0)**: `_consecutive_detect_errors` 计数器，超过3次后 sleep(0.5) + 抑制日志，成功推理后重置
- **MJPEG 多通道节流 (v2.6.0)**: `generate_mjpeg` 即使 frame_limit_enabled=False 也要 min_interval sleep，否则4通道 CPU 100%
- **截图限频 (v2.6.0)**: `_update_tracking_stats` 截图每秒最多1次（`_last_screenshot_time`），且必须在代码块开头显式 `import cv2`
- **每通道独立计数器 (v2.6.0)**: 计数器文件保存为 `DATA_DIR/counters/project_{pid}_ch{ch_id}.json`
