---
name: debug-channel
description: "诊断多工位/多通道问题：ChannelManager 通道隔离、GPU分配、模型独立实例、通道间串扰、多通道视频推流。当多工位模式下某个通道异常或通道间数据串扰时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__sequential-thinking, mcp__sentry"
---

> **设计深潜**：`docs/dev/internals/cluster-collector.md`（集群主从聚齐/心跳/超时）  
> 本 skill = 多工位/通道隔离 how-to/debug。

# debug-channel: 多工位/多通道诊断（v3.5.x）

你正在诊断天军 AI 视觉检测系统的 **多通道管理模块（ChannelManager）**。

用户问题: $ARGUMENTS

## 一、模块定位

| 项 | 值 |
|---|---|
| 后端单例 | `backend/api/channel_manager.py`（374 行，单例 `channel_manager`） |
| 后端实例（每通道一个） | `VideoSourceManager`（`backend/api/source.py`，15 mixin + 6 has-a 组件） |
| 路由文件 | `backend/api/source_routes.py`（1279 行，所有 `/source/*` 端点都接受 `?channel=N`） |
| 工位 API | `/api/v1/workstations/*`（`channel_manager.py` 内 router） |
| 视频流端点 | `/video_feed?channel=N`（注意：**挂在 `backend/main.py`，不带 `/api/v1` 前缀**） |
| 持久化文件 | `backend/data/workstation_config.json` |
| 前端入口 | `frontend/src/views/Source/index.vue`（配置）+ `Monitor/index.vue`（运行/显示） |
| 前端 store | **无独立 channel store**；多通道运行时 state 直接在 `Monitor/index.vue` 的 `multiChannelData` ref（数组，下标=channel_id） |

> 核心不变量（AGENTS.md 第八节 #4）：`channel_manager.set_channel_count()` 在降工位时**必须**调 `mes_hook.on_channel_removed(cid)` + `alarm_router.on_channel_removed(cid)`，否则 MES dict 残留 / 报警串口被占。

---

## 二、ChannelManager 真相（v3.5.x）

```python
class ChannelManager:
    channel_count: int = 1                          # 1..MAX_CHANNELS=64（v3.47 起不限于 1/2/4，防呆上界见 channel_manager.py:22）
    channels: Dict[int, VideoSourceManager]         # 默认 {0: VSM(channel_id=0)}
    _lock: threading.Lock                           # 通道增删锁
    _model_lock: threading.Lock                     # 模型加载锁

    # 通道访问
    get(channel_id) -> VideoSourceManager           # 找不到抛 ValueError
    get_default() -> channels[0]
    active_channels() -> List[int]                  # sorted

    # 生命周期
    set_channel_count(count)                        # 1..64（v3.47），含降级清理 + 升级新建
    stop_all()

    # 模型加载（v3.5.x 关键改动）
    load_shared_model(path, device='auto')          # 给所有通道各加载一份独立实例
    load_model_for_channel(ch, path, device='auto') # 单通道加载独立实例
    _propagate_model(channel_id)                    # 已变成 no-op（return 直接退出）

    # GPU
    get_gpu_allocation() -> {ch_id: device}         # 每通道 mgr.device 字符串

    # 持久化
    _save_config()                                  # 仅 set_channel_count 调；只写 channel_count
    save_channel_source(ch_id, cfg, merge=True)     # 写 channels.{id}.* 字段，不写 channel_count
    get_channel_sources() -> dict                   # 读 channels 字典
```

**模型语义（关键纠错）**：旧文档说 `_shared_model` / `_model_cache` 是共享对象 — **不存在**。当前实现是：

- `load_shared_model("a.pt")` 实际是"路径相同、对象独立"：循环为每个通道调 `_load_model_for_channel_locked` 各加载一份。
- 每通道独立 `mgr.model` 实例，避免推理线程跨通道竞争。
- `_propagate_model` 已是 no-op，源码注释明确：*"model instances are no longer propagated/shared across channels"*。
- 因此**不存在"一个通道释放模型影响其他通道"的问题**（反过来，**省显存的方式只能靠 FP16 / TRT engine / 限制工位数**）。

---

## 三、通道隔离机制

每个通道是独立的 `VideoSourceManager`，`__init__(channel_id=N)` 时：

| 独立 | 共享 |
|---|---|
| 视频采集线程 / 队列 / 帧缓存 | FastAPI 进程 |
| 推理线程池（`source_inference_executor`）| SQLite 数据库（按 `channel` 字段隔离记录） |
| 模型实例 `mgr.model`（含 device） | `MESHookManager` 单例（按 channel_id 分 dict） |
| Session/Cycle/Step 状态机 | `ScannerService._connections`（key 是 device_id，不是 channel_id） |
| 计数器 / 事件 log（`events_log`，30 秒窗口） | `ExternalDeviceService._connections`（同上） |
| FFmpeg 录像管道 | `ClusterCollector`（按 station_id；多通道副机自动加 `-{channel_id}` 后缀） |
| 画面变换/卡尔曼配置（`per_channel[str(ch)]`） | `AlarmRouter` 的串口（共享模式下多通道共一台灯柱，详见第六节） |

**关键巧妙点**：`set_channel_count` 创建新通道时会把 channel 0 的 `_mes_hook` 引用赋给新 VSM（`new_mgr._mes_hook = self.channels[0]._mes_hook`），保证 4 个通道用同一个 MESHook 单例。

---

## 四、GPU 分配

| 设备字符串 | 含义 |
|---|---|
| `auto` | `_resolve_device` → `torch.cuda.is_available()` 时返回 `cuda:0`，否则 `cpu` |
| `cuda:0` / `cuda:1` / ... | 显式绑定到该 GPU index |
| `cpu` | 强制 CPU 推理（调试/无显卡用） |

设置方式：

1. `POST /api/v1/workstations/{ch}/gpu` body `{ "device": "cuda:1" }` → 写 `mgr.device`，下次 `load_model` 生效。
2. `POST /api/v1/source/detection/start?channel=N` 时，`source_routes.py:start_detection` 调 `channel_manager.load_model_for_channel(ch, path, device)`，会用当前 `mgr.device`（或 req 里指定的）加载到对应 GPU。
3. `GET /api/v1/workstations/gpu-allocation` 看映射。

**显存预算**：4 通道 × YOLOv8s FP32 ≈ 4×400MB；多卡时务必把 ch0/ch1 → cuda:0、ch2/ch3 → cuda:1。**单卡 8G 跑 4 工位会爆**，建议 FP16 + 限制 stream FPS。

---

## 五、多通道视频推流（MJPEG）

- 端点：`GET /video_feed?channel=N`（在 `backend/main.py:929`，**不在 `/api/v1`**）。
- 实现：`channel_manager.get(channel).generate_mjpeg()`（在 `source.py` 或 `source_streaming_mixin.py`，两者代码重叠是历史拆分残留）。
- 通道未启动任何源时，不返回 404，而是吐黑底白字"Ch{N} - No Source"占位帧（避免前端 `<img>` 反复闪重连）。
- `mgr._mjpeg_active_streams` 计数活跃连接，日志带 `ch={channel_index}`。
- 前端 `Monitor/index.vue` 用 `<img :src="\`${baseURL}/video_feed?channel=${ch}\`">` 直接消费；多通道 + 双缓冲在 v2.6.0/v3.0.0/v3.1.3 多次修过（AGENTS.md 第八节 #7）。

---

## 六、报警共享：`shared_with` 机制（v2.7.3）

多工位**共享同一灯柱**（一根 Modbus 灯柱挂多工位）的实现：

- 配置：在 owner 通道的 alarm 配置里写 `shared_with: [1, 2]`。
- `AlarmRouter._load_all`（`backend/api/alarm.py:582`）解析 `shared_with` → 在 owner `AlarmManager` 上调 `set_shared_mode(channels=all_chs, ...)` → 把这些 ch 的 `self.managers[ch]` 指向**同一个 manager 实例**。
- 多通道触发时：每通道调 `alarm_router.trigger_alarm(event_type, channel_id=N)` → 共享 manager 把 `(channel_id, event_type)` 记进 `_channel_states` → `_recompose_and_apply` 按 `priority_order`（默认 `['ng','warn','ok','idle']`）选出最高优事件，发一次串口指令。
- 降工位时，`AlarmRouter.on_channel_removed(ch)` 区分两种情况：
  - 共享组里：仅 `mgr._shared_channels.discard(ch)` + 重新合成（不断串口）。
  - 非共享：完整 `stop_alarm` + `_idle_light_active=False` + `all_off` + `disconnect` + 从 `managers` pop。
- 序列化：`_save_all` 共享 manager 只按 owner 写一次（`id(mgr)` 去重）。

> 修改报警 / 共享逻辑前必读 `debug-alarm` skill。

---

## 七、配置持久化（workstation_config.json）

```json
{
  "channel_count": 4,
  "channels": {
    "0": {
      "source_type": "video",
      "video_file": "/path/to/x.avi",
      "gpu_device": "cuda:0",
      "project_id": 12,
      "was_detecting": true
    },
    "1": { ... }
  }
}
```

| 触发点 | 写入字段 | 备注 |
|---|---|---|
| `set_channel_count(n)` → `_save_config` | `channel_count` | **独占写 channel_count**；保留已有 `channels` 字典 |
| `save_channel_source(ch, cfg, merge=True)` | `channels.{ch}.*` | merge=True 浅合并；merge=False **整段替换该通道**（调用方必须传完整配置）；**不写 channel_count** |
| `save_splash_config` / `save_window_config` / `save_auto_resume_config`（v3.9.x+） | 顶层 `splash` / `window` / `auto_resume` 段 | 各自只替换自己的段，不动 channels / channel_count |
| `cleanup_on_exit`（`backend/main.py:654`） | `channels.{ch}.was_detecting` | 进程退出 atexit 钩子，记录"上次是否在检测" |
| `auto_restore_video_sources`（启动 `main.py:472`） | 读 channels 还原源 + 模型 + 检测状态 | 仅 `was_detecting=true` 的通道自动 start_detection |
| `auto_load_active_project`（启动 `main.py:401`） | 按通道 project_id 加载项目 | |

**写入权铁律（AGENTS.md 不变量 #17）**：每段有且只有上表列出的专属写函数，**任何新代码禁止
自己 `json.dump` 整写该文件**——文件无 schema 校验，旁路整写会把别段 key 静默抹掉且无报错
（新人常见事故：手写 channels 时把 splash / window 段一起冲掉）。

**v2.7.2 之后的关键修正**：
- 旧版 `_save_config` 用 `max(file_count, self.channel_count)` 防热重载覆盖，副作用是工位数只能升不能降。
- 新版：`_save_config` 仅由 `set_channel_count` 调一次，直接写 `self.channel_count`；`save_channel_source` 不再触碰 `channel_count`。
- 降工位后 `channels.{1,2,3}` 残留条目不清理，但 `get_channel_sources()` 仅取激活通道用，无副作用。

---

## 八、API 速查

### `/api/v1/workstations/*`（`channel_manager.py`）

| 端点 | 作用 |
|---|---|
| `GET /workstations/` | 全工位状态 + `source_configs`（持久化 channels 字典） |
| `POST /workstations/mode` | `{ channel_count, channels[] }` 改工位数 + 可选每通道 gpu |
| `POST /workstations/{id}/gpu` | `{ device }` 设单通道 GPU |
| `GET /workstations/gpu-allocation` | 当前 GPU 映射 |
| `GET /workstations/{id}/status` | 单通道详细状态 |
| `PUT /workstations/channel-config` | 持久化单通道源配置（任意字段，merge=False） |

### `/api/v1/source/*`（`source_routes.py`）

**所有端点都接 `?channel=N`**（默认 0），内部走 `_get_mgr(channel)` → `channel_manager.get(channel)`。覆盖：

- `/camera/start`, `/camera/stop`, `/rtsp/start`, `/hikvision/start`, `/hcnetsdk/start`, `/video/start`, `/image/set` ...
- `/detection/start`, `/detection/stop`, `/detection/pause`, `/detection/resume`, `/detection/standby`
- `/transform/config`, `/kalman/config`（**按通道独立配置**，写到 `per_channel[str(ch)]`）
- `/detection/results`（实时状态聚合，前端 200ms 轮询）

### 视频流（不在 `/api/v1`）

- `GET /video_feed?channel=N` — MJPEG 流
- `GET /snapshot?channel=N` — 单帧 JPEG

### 调试

- `GET /api/v1/debug/channels` — 每通道 source_type / 运行状态
- `POST /api/v1/debug/test_cluster_flow?cycle_id=&channel_id=` — 集群链路回放

---

## 九、降工位清理矩阵（v2.7.2 后已接入的）

`set_channel_count(new_count)` 在降级循环里逐 `cid` 调用：

| 模块 | 清理方法 | 清理内容 |
|---|---|---|
| `VideoSourceManager` | `mgr.end_session()` + `mgr.stop()` | session 结尾、采集线程、推理线程 |
| `MESHookManager` | `on_channel_removed(cid)` | 7 个 dict（`_pending_workpiece` / `_pending_queue` / `_active_orders` / `_inspecting_workpiece` / `_last_scan_event` / `_rebind_prompt` / `_scan_pair_active`）按 channel_id 弹出 |
| `AlarmRouter` | `on_channel_removed(cid)` | 共享：从 `_shared_channels` 摘；非共享：`stop_alarm` + `all_off` + `disconnect` + pop manager |

**故意不接入清理的**（按 channel_id 清会误伤硬件层）：

| 模块 | 为什么不清 |
|---|---|
| `ScannerService._connections` | key 是 **device_id**（扫码器 DB 主键），跨工位切换应保留 |
| `ExternalDeviceService._connections` | key 是 **device_id**（称重/PLC），断开后用户要手动重连 |
| `ClusterCollector._connected_slaves` | key 是 **station_id**（机器实例），与 channel_id 正交 |

**前端同步清理**（`Monitor/index.vue` 的 `initMultiChannelData(count)`）：删除 `multiChannelData` / `multiLastSeenSeq` / `multiFrameNaturalSize` 中超出 `count` 的 key；对 0..count-1 强制重置 seq 基线。

---

## 十、常见问题诊断

### 1. 某通道异常（不出图 / 不检测 / 报错）

1. `GET /api/v1/debug/channels` 看 channel 是否真在 `channels` 字典里。
2. 看后端日志 `[ChannelManager]` / `[MJPEG] ch=N` 行；检查 `set_channel_count` 是否报错。
3. `channel_manager.get(N).device` 是不是预期 GPU；如果是 `auto` 但 `torch.cuda.is_available()=False` 会 fallback 到 CPU 看上去"很慢"。
4. 模型有没有加载到该通道：`mgr.model is not None`，看 `_load_model_for_channel_locked` 日志 `[ChannelManager] chN loaded model instance on cuda:X`。
5. 前端 `Monitor/index.vue` 的轮询 / MJPEG 是否传了正确 `channel=N`。

### 2. 通道间数据串扰（A 通道事件出现在 B 通道）

1. **后端**：检查 API 调用是否传了 `?channel=`；DB 写入是否带 `channel` 字段；Session/Cycle/StepRecord 的 `channel` 是否正确写入。
2. **共享对象**：模型是独立实例（OK）；MES Hook 是单例**按 channel_id 分 dict**（看 `mes_hooks._pending_workpiece[channel_id]`），如果新加字段忘了按 channel_id 隔离会串。
3. **报警共享模式**：如果配了 `shared_with`，trigger_alarm 必须传正确 `channel_id`（`_recompose_and_apply` 按 channel_id 路由）。
4. **集群副机**：多通道副机自动用 `station_id-{channel_id}` 区分，host 端按这个 key 入 `BoxAggregations`。
5. **前端**：`multiChannelData[ch]` 数组下标是否对齐；`startDetectionForChannel(ch)` 必须用 `multiChannelData[ch].project`，**不能**用全局 `currentProject`（v2.6.0 修过）。

### 3. GPU 显存爆 / OOM

1. v3.5.x 模型实例**不再跨通道复用**（每通道独立 `model`），4 工位 = 4 份显存。
2. 减分担：`POST /workstations/{id}/gpu`{ device: cuda:1 } 把工位分到第二张卡。
3. FP16 / TRT engine 在模型管理页转换；同时把 `target_stream_fps` 调到 15。
4. `ChannelManager._load_model_for_channel_locked` 调 `mgr.load_model(path)` — 如果 load 失败会打 `[ChannelManager] chN 独立模型加载失败`，日志看实际是 OOM 还是路径错。

### 4. 多通道前端 UI 不同步

1. `getDetectionResults(channel)` 必须按通道单独调，多通道下 4 路 200ms 轮询 = 20 req/s，不能合一路。
2. `multiChannelData[ch].mes` 单通道模式下要在 `startPolling` 单通道分支里手动 `multiChannelData.value[0].mes = data.mes`，否则 MES banner 不显示（v2.5.0 修过）。
3. 切工位后老 seq 残留 → `initMultiChannelData(count)` 清掉 `multiLastSeenSeq` / `_processedEventIds`。
4. `getWorkstations()` 仅在页面加载调一次；动态改工位数后端**不会主动推送**前端，需重刷或主动 `fetchChannelCount()`。

### 5. 通道串口/报警残留（升降工位反复后蜂鸣不停）

1. 用 `rg on_channel_removed backend/` 确认 `set_channel_count` 调用链齐全。
2. 看是否走到 `AlarmRouter.on_channel_removed(cid)`，注意共享模式只摘 channel 不断串口（设计如此）。
3. 串口被占：先 `stop_all()` → `alarm_router.disconnect_all()` → 再降级。

### 6. 视频流卡顿 / Electron 黑屏

1. 4 路 MJPEG 长连接 + 200ms 轮询 + GPU 解码，Electron 默认 GPU 内存可能不够（看 `electron/main.js` 的 `disable-gpu-memory-buffer-video-frames` / `--max-old-space-size`）。
2. `target_stream_fps`（`/source/stream/config`）调小。
3. 后端 `[MJPEG]` 日志看活跃连接数，单通道泄漏会涨到 10+。

---

## 十一、修改前必读 / 关联 skill

- 改 ChannelManager 本身：本 skill + `modify-source`（VSM 内部状态变量依赖链）
- 改报警共享：`debug-alarm`
- 改 MES 跨通道隔离：`debug-mes`
- 改前端多通道显示：`modify-frontend` + `debug-frontend`
- 改通道持久化字段：`modify-model`（`workstation_config.json` 不入 ORM，但启动恢复链路要测）
- 集群多通道：`debug-mes`（含 `cluster_collector`）

---

## 十二、参考文件路径

| 文件 | 行数 | 作用 |
|---|---|---|
| `backend/api/channel_manager.py` | 374 | `ChannelManager` 单例 + `/workstations/*` |
| `backend/api/source.py` | 1573 | `VideoSourceManager` 主类 + `_get_mgr` + `/video_feed` 帮助函数 |
| `backend/api/source_routes.py` | 1279 | 所有 `/source/*` 端点（带 `?channel=`）|
| `backend/api/alarm.py` | 1009 | `AlarmRouter` + `shared_with` + `_recompose_and_apply` |
| `backend/api/debug.py` | 118 | `/debug/channels` + 集群链路诊断 |
| `backend/services/mes_hooks.py` | 1505 | `MESHookManager.on_channel_removed`（7 个 dict） |
| `backend/main.py` | — | `/video_feed` `/snapshot` + `auto_load_active_project` + `auto_restore_video_sources` + `cleanup_on_exit` |
| `backend/data/workstation_config.json` | — | 持久化（无并发写保护） |
| `frontend/src/views/Source/index.vue` | — | 工位模式选择 + 每通道源/项目配置（双工位/四工位需 `systemStore.developerMode`） |
| `frontend/src/views/Monitor/index.vue` | 4051 | 多通道运行/显示（`multiChannelData` 数组） |
| `frontend/src/api/detection.js` | — | `getWorkstations` / `setWorkstationMode` / `setChannelGpu` / `getGpuAllocation` |

---

**最后更新**：2026-05-07（v3.5.x 主线 / 实测代码核对）
