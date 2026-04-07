---
name: debug-channel
description: "诊断多工位/多通道问题：ChannelManager 通道隔离、GPU分配、共享模型、通道间串扰、多通道视频推流。当多工位模式下某个通道异常或通道间数据串扰时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# debug-channel: 多工位/多通道诊断

你正在诊断天军AI视觉检测系统的 **多通道管理模块**。

用户问题: $ARGUMENTS

## 架构

```
ChannelManager (backend/api/channel_manager.py ~318行)
  └── channels: Dict[int, VideoSourceManager]
      ├── channel 0: VideoSourceManager 实例
      ├── channel 1: VideoSourceManager 实例
      ├── channel 2: VideoSourceManager 实例 (4通道模式)
      └── channel 3: VideoSourceManager 实例 (4通道模式)
```

## ChannelManager 核心逻辑

```python
class ChannelManager:
    channels: Dict[int, VideoSourceManager]
    _shared_model = None          # 共享模型实例
    _model_cache: Dict[str, Any]  # GPU级模型缓存
    
    # 通道管理
    get(channel_id) → VideoSourceManager
    get_default() → channels[0]
    active_channels() → list of active channel ids
    set_channel_count(count)  # 1, 2, 或 4
    
    # 模型管理
    load_shared_model(model_path)       # 一次加载，所有通道共享
    load_model_for_channel(ch, path)    # 每通道独立加载 + GPU缓存
    _propagate_model(model)             # 分发共享模型到各通道
```

## 配置存储

```json
// backend/data/workstation_config.json
{"channel_count": 2}
```

## API 端点 (挂载在 /api/v1/workstations)

| 端点 | 功能 |
|------|------|
| `GET /workstations/` | 列出所有工位状态 |
| `POST /workstations/mode` | 设置通道数 (1/2/4) |
| `POST /workstations/{id}/gpu` | 分配GPU给通道 |
| `GET /workstations/gpu-allocation` | GPU分配映射 |
| `GET /workstations/{id}/status` | 单通道状态 |

## 前端多通道逻辑

### Source 页面 (Source/index.vue)
- 工位模式选择器: 单机/双工位/四工位
- **双工位/四工位选项需要开发者模式 (v2.3.0+):** `systemStore.developerMode` 为 true 时才可选，否则禁用
- 多工位模式: 每通道独立配置视频源、项目、分辨率、FPS
- `setWorkstationMode(count)` → 后端创建/销毁 VideoSourceManager 实例

### Monitor 页面 (Monitor/index.vue)
- `getWorkstations()` 获取通道数 → 选择显示模式
- **双工位:** 每通道独立轮询 `getDetectionResults(channel)`
- **四工位:** 2x2网格，选中通道显示详情
- 每通道独立 MJPEG 流: `/video_feed?channel=N`
- 多通道 MJPEG 用 ReadableStream 解析而非直接 img.src

### Detection API (detection.js)
- 所有检测API都支持 `channel` 查询参数
- `startDetection(modelPath, conf, iou, channel)`
- `stopDetection(channel)`, `getDetectionResults(channel)` 等

## 通道隔离机制

每个通道是**独立的 VideoSourceManager 实例**，拥有独立的：
- 视频采集线程 (_capture_loop)
- 推理状态变量 (_init_inference_vars)
- Session/Cycle/Step 记录
- 计数器
- 录像管道

**但共享：**
- 同一个 FastAPI 进程
- 同一个 SQLite 数据库
- 可能共享 YOLO 模型实例（load_shared_model）
- 同一个 GPU（除非手动分配不同GPU）
- MESHookManager 实例（v2.3.0+, 按 channel_id 区分数据）
- ScannerService 实例（v2.3.0+, 每个扫码器绑定一个 channel_id）

## 常见问题诊断

### 通道数据串扰
1. 检查 API 调用是否传了正确的 `channel` 参数
2. 确认 `ChannelManager.get(channel)` 返回正确的实例
3. DB 查询是否按 `channel` 字段筛选
4. Session/Cycle 记录的 `channel` 字段是否正确写入

### 某个通道不工作
1. 确认该通道的 VideoSourceManager 已初始化
2. `set_channel_count()` 是否成功创建实例
3. 视频源是否独立启动（每通道需要单独 start_xxx）
4. 模型是否加载到该通道

### GPU 内存不足
1. 4通道 × 独立模型 = 4份GPU显存占用
2. 考虑用 `load_shared_model()` 共享模型
3. 检查 FP16 是否开启节省显存
4. 检查 GPU 分配: `/workstations/gpu-allocation`

### 多通道视频流卡顿
1. 前端 200ms 轮询 × 4通道 = 每秒20次API请求
2. MJPEG ReadableStream 解析的 `findBytes()` 是逐字节搜索
3. Electron GPU 内存限制可能不够4路解码
4. 每路 MJPEG 都是独立 HTTP 长连接

## 关键文件
- `backend/api/channel_manager.py` — ChannelManager + API
- `backend/api/source.py` — VideoSourceManager（每通道一个实例）
- `backend/data/workstation_config.json` — 通道数配置
- `backend/main.py` — /video_feed?channel=N 端点
- `frontend/src/views/Source/index.vue` — 工位模式配置
- `frontend/src/views/Monitor/index.vue` — 多通道显示
- `frontend/src/api/detection.js` — 带channel参数的API调用

## 已知陷阱
- `_propagate_model` 方法的调用路径不完全清晰
- 共享模型模式下，一个通道释放模型可能影响其他通道
- `workstation_config.json` 是简单JSON文件，无并发写保护
- 前端 `getWorkstations()` 在页面加载时调用一次，后续通道变化不会自动刷新
