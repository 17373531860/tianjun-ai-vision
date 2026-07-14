---
name: debug-detection
description: "诊断检测推理问题：模型加载失败、推理结果异常、FPS 低、置信度配置不生效、TRT 引擎 imgsz 不匹配。当检测不出目标或结果不准时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__context7, mcp__sentry, mcp__sequential-thinking"
---

# debug-detection: 检测推理诊断 (v3.5.x)

诊断对象：天军 AI 视觉检测系统的 **YOLO 推理管线**。

用户问题: $ARGUMENTS

## 一、唯一推理路径

历史上的 `backend/api/detection.py` / `backend/services/detector.py` 已在 v2.7.x 删除。**当前唯一入口**：

```
前端 startDetection()
  → POST /api/v1/source/detection/start (channel=N, body: {model_path, conf, iou})
    → source_routes.start_detection()
        mgr.conf_threshold = req.conf
        mgr.iou_threshold  = req.iou
      → channel_manager.load_model_for_channel(N, model_path, device)
          → mgr.load_model(model_path)        # ModelLoadMixin
      → mgr.start_detection()                  # 拉起推理线程
        → _inference_loop()                    # InferenceLoopMixin
          → _inference_select_and_run_model()
            ├─ _detect_only         # 默认
            ├─ _detect_and_track    # logic_mode=tracking
            └─ _detect_segment      # task_type=segmentation
```

**关键文件速查**：

| 职责 | 文件 |
|---|---|
| 模型上传 / 转换 / 路径解析 | `backend/api/models.py` (793) |
| 模型加载（imgsz 多级探测 + warm-up） | `backend/api/source_model_load_mixin.py` |
| 推理线程池组件（has-a, max_workers=1） | `backend/api/source_inference_executor.py` |
| 推理主循环 | `backend/api/source_inference_loop_mixin.py` |
| **3 种 runner**（含 `clip_bbox_normalized`） | `backend/api/source_detect_runners_mixin.py` |
| 项目配置 → 步骤阈值落地 | `backend/api/source_project_config_apply.py` |
| 几何工具（含 `clip_bbox_normalized`） | `backend/api/source_geometry.py` |
| 通道间模型分配（独立实例，不共享 runtime） | `backend/api/channel_manager.py` |

> **没有 `services/model_service.py`**——模型相关逻辑全部在 `api/models.py` 和 `source_model_load_mixin.py`。

## 二、模型加载链路

### 1. 上传 / 转换（`api/models.py`）
- `models` 表（**不是 `ml_models`**，ORM 类 `Model`）记录原始 `.pt` 路径
- 7 种格式：`pytorch_fp32` / `pytorch_fp16` / `onnx` / `torchscript` / `tensorrt_fp32` / `tensorrt_fp16` / `tensorrt_int8`
- 转换走后台单线程队列 `_conversion_worker`，超时 600s；TensorRT 还会先做 GPU/CUDA/cuDNN/TRT 兼容性诊断
- 转换产物落 `uploads/models/conversions/{model_id}_{fmt}_{gpu_arch}{ext}`，写回 `model_conversions` 表

### 2. 路径解析（`POST /models/{id}/resolve-path?format=...`）
- `pytorch_fp32` → 直接返回原始 `.pt`
- 其他格式 → 查 `model_conversions`，TRT 还要匹配当前 `gpu_arch`；命中且 `status=ready` 用转换产物，否则回退原始 `.pt` 并 `fallback=true`

### 3. 加载到通道（`ModelLoadMixin.load_model`）
- **每个通道一个独立 `YOLO()` 实例**（`channel_manager._propagate_model` 已是 no-op；通道间不共享 runtime 对象，避免推理竞争）
- 转换模型加载失败 → 自动 fallback 回原始 `.pt`
- 设备选择：`device == 'auto'` → `cuda:0` / `cpu`；`.to(device)` 仅对原生 PyTorch 生效
- `model.task` 写入 `self.model_task`（`detect` / `segment`），下游决定走哪条 runner
- **`use_half`** 来自 `device_config.json`，仅在 `device.startswith('cuda') and is_native_pytorch` 时真正生效
- **CUDA warm-up** 用 `_model_imgsz` 跑一次 `np.zeros` 预热，命中 `AssertionError "model size (1, 3, H, W)"` 会自动改写 `_model_imgsz` 重试

## 三、推理参数生效路径（这是排查的核心）

| 参数 | 来源 | 落地字段 | 影响范围 |
|---|---|---|---|
| `conf` | 前端 `startDetection` body | `mgr.conf_threshold` | **全局**（所有类别 NMS 前过滤） |
| `iou` | 前端 `startDetection` body | `mgr.iou_threshold` | **全局** NMS IoU |
| 步骤阈值 | `Project.steps_config[i].threshold`（10-100 百分比） | `mgr.step_conf_thresholds[label]`（0.1-1.0） | **每步独立**，二次过滤 |
| `imgsz` | 模型加载时自动探测 | `mgr._model_imgsz` | TRT 必须等于引擎构建值 |
| `device` | `device_config.json` | `mgr.device` | `auto/cpu/cuda:0` |
| `use_half` | `device_config.json` | `mgr.use_half` | 仅原生 `.pt` + CUDA |

### 全局 conf/iou 的真相
**只有 `POST /detection/start` 时写入**。如果用户在 Project 页改了"全局阈值"但**没重启检测**，`conf_threshold` 不会更新。`set_project_config()` 不写全局 conf/iou，只写 `step_conf_thresholds`。

### 步骤阈值的真相
`source_project_config_apply._apply_steps_config`：
```python
threshold = step.get('threshold', 50)
if threshold > 1:
    threshold = threshold / 100.0   # 前端发百分比，后端折算
h.step_conf_thresholds[label] = threshold
```
runner 里 **二次过滤**：低于 `step_conf_thresholds[class_name]` 的 box 被丢弃（不参与显示也不参与状态机）。

> **不存在 `max_det` 参数**——旧文档曾写 300，但代码里 `model.predict()` 调用没传，也没字段。

## 四、TRT 引擎 imgsz 多级探测

`source_model_load_mixin.py` 按以下优先级探测 `_model_imgsz`：

1. `.engine` 文件头嵌入的 Ultralytics JSON metadata（最权威，加载前直接读文件）
2. `model.model.bindings`（list / dict 都兼容）
3. `model.model.input_shape`
4. `model.model.imgsz`（AutoBackend 已实例化时）
5. TRT context/engine 的 `get_tensor_shape`（按通道数 = 3 过滤输入张量）
6. `.pt` 模型走 `model.overrides['imgsz']` / `model.model.args['imgsz']`
7. 全部失败 → 默认 640
8. **CUDA warm-up 兜底**：`predict(np.zeros)` 报 `AssertionError model size (1, 3, X, Y)` → 自动修正
9. **AutoBackend 权威修正**：warm-up 后再读一次 `predictor.model.imgsz`，与当前值不同则覆盖

**TRT 不变量**：
- 转换 `.pt → .engine` 的 imgsz **在 export 时固定**（项目里没显式传 `imgsz`，跟随原模型 default 640；如需 1280 等需在 `FORMAT_EXPORT_ARGS` 加键）
- 检测时 `mgr.model.predict(imgsz=self._model_imgsz, ...)` **必须等于 engine 构建值**，否则 ultralytics 抛 AssertionError
- 换显卡架构（如 Ampere → Ada）TRT engine 不通用，必须重新转换；`resolve-path` 按 `gpu_arch` 匹配，不命中就回退 `.pt`

## 五、v3.5.x 新增：`clip_bbox_normalized`

`source_geometry.py` 提供，3 种 runner 都调用。把像素坐标 → 归一化 + clip 到 `[0, 1]`：

```
nx  = max(0, min(1, x1 / w))
ny  = max(0, min(1, y1 / h))
nw  = max(0, nx2 - nx)
nh  = max(0, ny2 - ny)
```

**为什么需要**：
- ultralytics 偶发输出 letterbox 边界外像素（理论上自己 clip，但兜底）
- 浮点除法误差导致 `x + w > 1.0` (例：1.000000001) → 前端画框越界
- Kalman 平滑 + 坐标变换链累积误差

排查检测框跑出画面时，先确认调用链上是否绕过了 `clip_bbox_normalized`。

## 六、诊断决策树

### A. 模型加载失败
1. 看 `[模型加载] ...` / `[模型预热] ...` 日志，定位失败步骤
2. 文件存在性：`uploads/models/{model_id}_*.pt` 与 `uploads/models/conversions/{model_id}_{fmt}_{gpu_arch}.engine`
3. CUDA 可用性：进 conda env 跑 `python -c "import torch;print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`
4. 转换状态：查 `model_conversions` 表 `status` 字段（`queued` / `converting` / `ready` / `failed`）+ `error_msg`
5. TRT 失败常见：`gpu_arch` 不匹配、显存不足、TRT/CUDA 版本错配——日志里 `[ModelConvert] 环境检查失败` 已打印诊断
6. 转换模型加载抛错时**会自动回退** `.pt`，注意 `[模型加载] 转换模型加载失败 (...)，回退到原始模型` 日志

### B. 推理无结果
1. **类别名对不上**：`self.model.names` 与 `steps_config[i].label` 大小写、空格、中英文必须完全一致；不在 `enabled_labels` 集合的 box 在 runner 第一道就被过滤
2. **全局 conf 太高**：检查 `mgr.conf_threshold`（默认 0.25）；前端"开始检测"时传的 `conf` 是不是被改高了
3. **步骤阈值太高**：`step_conf_thresholds[label]`，注意前端"35" 是 35%（写入 0.35），不是 0.35 写入 0.0035
4. **视频源无帧**：`mgr.current_frame is None` / `_latest_frame_for_inference is None` → 走 `debug-video` 排查
5. **`is_detecting=False` / `_inference_running=False`**：检测线程没起来，看 `start_detection` 返回值
6. **task_type 配错**：`segmentation` 模型走了 `_detect_only` 会丢 mask；`detect` 模型走了 `_detect_segment` 会因没 masks 跳过

### C. 置信度配置不生效（最常报）
按数据流逐段验证：
1. **前端发出**：浏览器 DevTools 看 `setProjectConfig` 请求 body 里 `steps_config[i].threshold` 值
2. **后端解析**：日志 `步骤阈值: {...}` （`source_project_config_apply._print_summary` 打印），看落地后字典是否对
3. **runner 读到**：`source_detect_runners_mixin._detect_only` 第 122 行附近，过滤逻辑：
   ```python
   step_threshold = self.step_conf_thresholds.get(class_name)
   if step_threshold is not None and confidence < step_threshold:
       continue
   ```
4. **是否需要重启检测**：步骤阈值通过 `set_project_config` 即时生效；**全局 `conf_threshold` 必须重启检测**（`POST /detection/start` 才会写）
5. **百分比 vs 小数陷阱**：前端 UI 给百分比 (10-100)，后端 `> 1` 即除 100；如果前端发了 `0.35` 会被当 0.35% 几乎全收

### D. FPS 低
1. **CPU/GPU 占用** 看 `current_device_info`：跑 CPU 上肯定慢
2. **`_model_imgsz` 过大**：1280 是 640 的 4 倍计算量；TRT 引擎 imgsz 固定，要降只能重转
3. **`use_half=False` 且原生 .pt + CUDA**：开 FP16 通常快 30-50%（device_config.json）
4. **通道数过多**：每通道一个独立 model + 独立线程池；`channel_count=4` 时 GPU 显存可能交换
5. **MediaPipe overlay**：`mediapipe_enabled=True` 给 CPU 加额外开销，调 `mediapipe_interval`（每 N 帧处理一次）
6. **推理超时计数**：日志 `推理超时 (Xs)` 出现频繁意味着 GPU 实际不可达；连续 `_max_consecutive_timeouts` 次会触发 `_emergency_gpu_reset`
7. **看 `_inference_loop` 日志阈值**：`model.predict内部耗时 > 150ms` / `推理总耗时 > 300ms` 自动打印

### E. 检测框越界 / 飘移
1. v3.5.x 起所有 runner 都过 `clip_bbox_normalized`；如果还越界，**对照 hotfix 是否覆盖了 runner 文件**
2. Kalman 平滑（`source_drawer.py` 的 `KalmanFilter2D`）状态异常 → 关闭后复现
3. 旋转/镜像（`video_transform`）后坐标系映射 `_map_detections_original_to_display` 是否漏掉

### F. 多通道行为不一致
- `channel_manager._propagate_model` 已是 no-op；任何通道间共享模型 runtime 的旧逻辑都不存在
- 通道间结果干扰多半是状态污染：见 `debug-channel`

## 七、常用诊断命令

```bash
# 查模型表 / 转换记录
sqlite3 backend/data/sql_app.db \
  "SELECT id,name,file_path FROM models;"
sqlite3 backend/data/sql_app.db \
  "SELECT id,model_id,format,gpu_arch,status,error_msg FROM model_conversions ORDER BY id DESC LIMIT 20;"

# 看通道当前推理状态
curl 'http://localhost:8001/api/v1/source/detection/status?channel=0'

# 看通道当前 GPU 分配
curl 'http://localhost:8001/api/v1/workstations/gpu-allocation'

# 实时盯关键日志
grep -E '\[模型加载\]|\[模型预热\]|\[ModelConvert\]|推理超时|步骤阈值' backend.log
```

## 七.五、"框卡死"自愈防线（v3.37.0，川南反馈）

**现象**：识别某类别后检测框冻结在画面、后续全不识别、周期照样超时 NG——现场极易误判为模型问题。
**机制**：推理循环（`source_inference_loop_mixin.py` 推理线程 except 分支）每帧异常时发布点走不到，上一次发布的检测结果被反复重发布 → 前端框永久定格。
**防线**：连续异常计数 `_infer_consec_errors` 达 **30 帧**时主动发布一次空检测结果（清空残留框，现场一眼看出是链路故障）+ 调试中心 `backend.detection` 类别留证（含最后一次异常详情）；每走通一轮发布即归零，阈值内偶发异常行为与老版完全一致。
**排查**：客户报"框冻结"→ 先开调试日志中心"检测推理"类别找"推理连续异常已清空画面框"记录，里面带最后错误信息，能直接定位异常根源（模型/CUDA/格式）。回归：`tests/test_inference_error_clear.py`（synthetic 源真跑推理线程注入每帧必炸）。

## 八、已知陷阱

- 修 source 主类前先看 `source.py` 的 `__getattr__/__setattr__` 兼容层——历史 `_inference_executor` 字段已搬到 has-a 组件，老路径透明转发
- `_release_model()` 在推理线程未停时调用可能崩溃；正确顺序是 `stop_detection` → 等线程退出 → 再 `_release_model`
- `models` ORM 表名（不是 `ml_models`），`backend/core/config.py:_fix_db_paths` 用错表名是已知 bug
- 修改 `source_model_load_mixin.py` / `source_detect_runners_mixin.py` 走 `modify-source` 的影响分析
- 多通道模型加载用 `channel_manager.load_shared_model`（"共享"是路径共享，每通道独立实例）
