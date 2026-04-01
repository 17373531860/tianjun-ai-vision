---
name: debug-detection
description: "诊断检测推理问题：模型加载失败、推理结果异常、FPS低、两套检测系统冲突、置信度/IOU配置不生效。当检测不出目标或结果不准时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# debug-detection: 检测推理诊断

你正在诊断天军AI视觉检测系统的 **YOLO推理管线**。

用户问题: $ARGUMENTS

## 两套检测系统（重要！）

本项目存在 **两套独立的检测实现**，这是历史遗留问题：

### 系统A: VideoSourceManager 内置推理（主力，实际使用）
- **位置:** `backend/api/source.py` VideoSourceManager 类
- **模型加载:** `load_model()` 方法
- **推理执行:** `_capture_loop()` 内部的推理分支
- **特性:** Kalman滤波、帧计数、步骤状态机、4种检测模式、MediaPipe

### 系统B: DetectionService（基本废弃）
- **位置:** `backend/services/detector.py` (281行)
- **类:** `YOLODetector` + `DetectionService`
- **端点:** `backend/api/detection.py` 的 `/detection/start`, `/stop`, `/reset`
- **特性:** 简单推理循环，基础步骤匹配，无Kalman、无状态机

### 冲突风险
- `detection.py` 的端点同时操作两个系统：先调 `DetectionService` 再调 `VideoSourceManager`
- 如果只改了其中一个系统，另一个不会同步
- **Monitor页面实际使用的是系统A的端点** (`/source/detection/...`)

## 模型加载链路

```
前端 startDetection()
  → POST /api/v1/source/detection/start
    → VideoSourceManager.load_model(model_path)
      1. 尝试加载指定路径（可能是转换后的 ONNX/TensorRT）
      2. 失败则回退到原始 .pt 文件
      3. YOLO(model_path) 初始化 ultralytics
      4. 设置 device (auto/cpu/cuda:0)
      5. 可选: model.half() (FP16半精度)
      → 推理开始在 _capture_loop() 中
```

**模型路径解析 (backend/api/models.py):**
- `resolve-path` 端点：根据 model_id + format 查找实际文件
- 优先级: 转换后格式 → 原始 .pt
- 转换后文件位于: `uploads/models/conversions/`

## 推理参数

| 参数 | 来源 | 默认值 | 作用 |
|------|------|--------|------|
| `conf` | 项目配置 detection_config | 0.25 | 置信度阈值 |
| `iou` | 项目配置 detection_config | 0.45 | NMS IOU阈值 |
| `max_det` | 项目配置 detection_config | 300 | 最大检测数 |
| `device` | device_config.json | "auto" | 推理设备 |
| `use_half` | device_config.json | false | FP16半精度 |
| `imgsz` | 写死 | 640 | 输入尺寸 |

**参数生效路径:**
```
前端 Project 页配置 → updateProject API → DB
  → Navbar handleProjectChange → setProjectConfig API
    → VideoSourceManager.set_project_config()
      → 解析到 self.conf_threshold, self.iou_threshold 等
```

## Kalman 滤波 (KalmanFilter2D)

- 8状态：[x, y, w, h, vx, vy, vw, vh]
- 平滑检测框，减少抖动
- 每个检测目标一个滤波器实例
- 如果Kalman状态异常 → 检测框飘移

## 诊断步骤

### 模型加载失败
1. 检查模型文件是否存在: `uploads/models/` 目录
2. 检查 `load_model()` 的日志输出
3. 确认 PyTorch/CUDA 可用: `torch.cuda.is_available()`
4. 检查格式转换状态: `ModelConversion` 表

### 推理无结果
1. 确认模型标签与步骤配置匹配（大小写敏感）
2. 检查 `conf` 阈值是否过高
3. 确认视频源有帧输出（`current_frame` 不为 None）
4. 检查 `is_detecting` 状态

### FPS 低
1. 检查 `device_config.json` 的 device 设置
2. FP16 是否开启（对低端GPU重要）
3. MediaPipe 是否开启（额外开销）
4. `mediapipe_interval` 设置（每N帧处理一次）
5. 帧率限制: `frame_limit_enabled` + `target_stream_fps`

### 检测结果不准
1. 检查步骤标签映射: `set_project_config()` 中的标签解析
2. 确认 `min_frames` 配置（需要连续N帧才确认步骤）
3. 检查 `gap_tolerance`（允许的帧间断）
4. 去重间隔 `dedup_interval`

## 关键文件
- `backend/api/source.py` — 主推理逻辑（搜索 `load_model`, `_capture_loop`）
- `backend/services/detector.py` — 废弃的检测服务（但仍挂接）
- `backend/api/detection.py` — 检测控制端点
- `backend/api/models.py` — 模型管理和格式转换
- `backend/data/device_config.json` — 设备推理配置
- `frontend/src/api/detection.js` — 前端检测API
- `frontend/src/views/Monitor/index.vue` — 检测结果轮询和显示

## 已知陷阱
- `resetDetection` 前端用 `/detection/reset`，但其他检测端点用 `/source/detection/...`（路径不一致）
- `getDetectionStatus` 和 `getSourceStatus` 是完全重复的函数
- 模型格式转换是异步后台队列，转换中途查询状态需要轮询 `/conversions/{id}/status`
- `_release_model()` 释放GPU显存时如果推理线程还在运行，可能崩溃
