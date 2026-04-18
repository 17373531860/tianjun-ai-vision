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
{
  "channel_count": 4,
  "channels": {
    "0": {
      "source_type": "video",
      "gpu_device": "cuda:0",
      "project_id": 12,
      "video_file": "/path/to/video.avi",
      "was_detecting": false
    }
  }
}
```

### 自动保存/恢复 (v2.6.0)
- **启动时**: `auto_load_active_project()` 按通道 project_id 加载独立项目+模型
- **启动时**: `auto_restore_video_sources()` 恢复视频源+GPU+检测状态
- **关闭时**: `cleanup_on_exit()` 保存 was_detecting 标志
- **通道配置持久化**: `save_channel_source(ch_id, cfg, merge=True/False)`
- **channel_count 保护**: `_save_config()` 用 `max(file_count, self.channel_count)` 防止热重载覆盖

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

## 单通道模式 MES 数据传递 (v2.5.0 修复)

**问题**: 单通道模式下 `startPolling` 获取的 `data.mes` 没有赋值到 `multiChannelData.value[0].mes`，导致 `mesData` computed 属性为空。

**修复**: 在 `startPolling` 单通道分支中显式赋值：
```javascript
multiChannelData.value[0].mes = data.mes
```

**影响**: 未绑码 banner、scan toast、MES 信息条都依赖 `mesData`。

## 每通道独立项目 (v2.6.0)

**问题**: 前端 `startDetectionForChannel(ch)` 使用全局 `currentProject` 给所有通道设项目，导致模型类别与步骤不匹配、检测结果被丢弃。

**修复**: `startDetectionForChannel` 优先使用 `multiChannelData[ch].project`（从 `workstation_config` 加载的通道绑定项目），`syncProjectConfig(ch, explicitProject)` 支持传入指定项目。

**前端加载流程**:
1. `fetchChannelCount()` → `loadPerChannelDetectionSettings(sourceConfigs)` 
2. 每通道根据 `project_id` 从后端获取完整项目数据 → 存入 `multiChannelData[ch].project`
3. 启动检测时用该通道绑定的项目，非全局 `currentProject`

## 集群汇总 (v2.6.0)

- `backend/api/cluster.py` + `backend/services/cluster_collector.py`
- Master/Slave 角色，从机通过 `/cluster/report` 上报数据
- 多通道从机自动用 `station_id-{channel_id}` 后缀区分通道
- 主机等齐所有工位后触发 MES 推送

## 外部设备 (v2.6.0)

- `backend/api/external_device.py` + `backend/services/external_device.py`
- 支持 TCP/Modbus TCP/串口/HTTP 轮询
- 数据可分发到 ClusterCollector 或 MESGateway extra_fields

## 已知陷阱
- `_propagate_model` 方法的调用路径不完全清晰
- 共享模型模式下，一个通道释放模型可能影响其他通道
- `workstation_config.json` 是简单JSON文件，无并发写保护
- 前端 `getWorkstations()` 在页面加载时调用一次，后续通道变化不会自动刷新
- 单通道模式下 `startPolling` 必须手动将 `data.mes` 赋值到 `multiChannelData[0].mes`，否则 MES 相关 UI 全部失效
- **前端 startDetectionForChannel 必须用通道绑定项目，不能用全局 currentProject，否则所有通道的步骤配置会被覆盖**
- **channel_count 在 _save_config 中必须用 max(file_count, self.channel_count)，否则热重载时会被覆盖为 1**
- **MJPEG 四通道必须有 min_interval sleep，否则 CPU 100%**
- **v2.7.2 起移除 channel_count 的 max() 保护**：之前 `_save_config` 和 `save_channel_source` 都用 `max(file_count, self.channel_count)` 防止热重载覆盖，副作用是工位数永远只能升不能降（从四工位改单工位保存后下次启动仍恢复四工位）。新方案：`_save_config` 直接写 `self.channel_count`（只由 `set_channel_count` 调用一次，值一定正确）；`save_channel_source` 不再写 `channel_count` 字段，由 `set_channel_count` 独占该字段的写入权。
- **channel_count 降级场景**：从多工位改回单工位时，workstation_config.json 中 channels 字典里 ch1/ch2/ch3 的残留条目不会自动清理，但不影响功能（`get_channel_sources()` 只返回当前激活通道需要的数据）。

## 降工位残留清理（v2.7.2 新增）

**背景**：降工位时若不清理按 `channel_id` 缓存的状态，会导致下次升回工位时新通道被老数据污染（toast 重复弹出、MES 工单错乱、蜂鸣器不停等）。

**已接入清理（`ChannelManager.set_channel_count` 降工位循环自动调用）**：

| 模块 | 清理方法 | 清理内容 |
|---|---|---|
| `VideoSourceManager` | `stop_detection()` 里清 `events_log=[]` + `_event_seq=0` | 30 秒事件窗口，防止前端把老事件当新事件 |
| `MESHookManager` | `on_channel_removed(channel_id)` | 6 个按 channel_id 存的 dict：`_pending_workpiece` / `_pending_queue` / `_active_orders` / `_inspecting_workpiece` / `_last_scan_event` / `_rebind_prompt` |
| `AlarmRouter` | `on_channel_removed(channel_id)` | `stop_alarm()` + `_idle_light_active=False` + `all_off()` + `disconnect()` + 从 `managers` dict pop |

**前端同步清理**（`frontend/src/views/Monitor/index.vue` 的 `initMultiChannelData(count)`）：
- 删除 `multiChannelData` / `multiLastSeenSeq` / `multiFrameNaturalSize` 中超出 `count` 的 key
- 对 0..count-1 每个通道强制重置（避免切工位时 `_processedEventIds` 与 seq 基线残留）

**刻意不接入清理的 3 个模块（严禁按 channel_id 清理，会误伤硬件配置）**：

| 模块 | 为什么不清 |
|---|---|
| `ScannerService._connections` | dict 的 key 是 **device_id（扫码器 DB 主键）**，不是 channel_id。扫码器配置是硬件层，跨工位切换应保留 |
| `ExternalDeviceService._connections` | dict 的 key 是 **device_id**（称重、Modbus PLC）。断开后用户还要重新手动连接 |
| `ClusterCollector._connected_slaves` | dict 的 key 是 **station_id（机器实例 ID）**。与 channel_id 正交，动它会误触发"副机下线" |

**诊断要点**：如果遇到"切工位后新通道第一周期异常"，先用 `rg on_channel_removed backend/` 确认清理调用路径齐全；再看是否误把设备层清了。
