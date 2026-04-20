---
name: api-sync
description: "前后端API对齐检查：端点路径、请求/响应字段、已知不一致、URL规范。当怀疑前后端数据不对或新增API后需要同步时使用。"
argument-hint: "[具体的API对齐问题，或 'full-check' 做全量检查]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# api-sync: 前后端API对齐检查

你正在帮用户检查和修复前后端API的一致性。

需求: $ARGUMENTS

## 已知的不一致问题

### 1. resetDetection URL 路径不一致
```
前端 detection.js: POST /detection/reset       ← 错误路径
其他检测API:       POST /source/detection/...   ← 正确路径前缀
```
**影响:** resetDetection 可能打到 detection.py 的端点而非 source.py 的端点。

### 2. 重复函数
```javascript
// detection.js 中:
getDetectionStatus(channel) → GET /source/detection/results
getSourceStatus(channel)    → GET /source/detection/results  // 完全相同
```

### 3. Vite 代理 vs 直连
```javascript
// api/index.js
getBaseURL()     → 'http://localhost:8001/api/v1'  // 所有API直连
getBackendHost() → 'http://localhost:8001' (桌面) 或 '' (浏览器, 走Vite代理)
```
MJPEG 流(`/video_feed`)走 getBackendHost()，API 走 getBaseURL()。

### 4. Electron 开发端口不匹配
```
electron/main.js:   loadURL('http://localhost:5173')  // 开发模式
vite.config.js:     server.port = 6001                // 实际端口
```

## 全量API对照表

### Source 模块 (/api/v1/source)
| 前端调用 (detection.js) | 后端端点 (source.py) | 状态 |
|------------------------|---------------------|------|
| `POST /source/detection/start` | source_router `/detection/start` | OK |
| `POST /source/detection/stop` | source_router `/detection/stop` | OK |
| `POST /source/detection/pause` | source_router `/detection/pause` | OK |
| `POST /source/detection/resume` | source_router `/detection/resume` | OK |
| `POST /source/detection/standby` | source_router `/detection/standby` | OK |
| `POST /source/detection/resume-inference` | source_router `/detection/resume-inference` | OK |
| `GET /source/detection/results` | source_router `/detection/results` | OK |
| `GET /source/status` | source_router `/status` | OK |
| `POST /source/detection/reset-stats` | source_router `/detection/reset-stats` | OK |
| **`POST /detection/reset`** | **detection_router?** | **路径不一致!** |
| `POST /source/camera/start` | source_router `/camera/start` | OK |
| `POST /source/rtsp/start` | source_router `/rtsp/start` | OK |
| `POST /source/video/upload` | source_router `/video/upload` | OK |
| `POST /source/video/start` | source_router `/video/start` | OK |
| `POST /source/image/upload` | source_router `/image/upload` | OK |
| `POST /source/image/start` | source_router `/image/start` | OK |
| `POST /source/hikvision/start` | source_router `/hikvision/start` | OK |
| `POST /source/hcnetsdk/start` | source_router `/hcnetsdk/start` | OK |

### Workstation 模块 (/api/v1/workstations)
| 前端调用 (detection.js) | 后端端点 (channel_manager.py) | 状态 |
|------------------------|-------------------------------|------|
| `GET /workstations/` | workstation_router `/workstations/` | OK |
| `POST /workstations/mode` | workstation_router `/workstations/mode` | OK |
| `POST /workstations/{id}/gpu` | workstation_router `/workstations/{id}/gpu` | OK |
| `GET /workstations/gpu-allocation` | workstation_router `/workstations/gpu-allocation` | OK |

### Session 模块 (/api/v1)
| 前端调用 (data.js) | 后端端点 (sessions.py) | 状态 |
|-------------------|----------------------|------|
| `GET /sessions` | sessions_router `/sessions` | OK |
| `GET /sessions/dates` | sessions_router `/sessions/dates` | OK |
| `GET /sessions/by-date/{date}` | sessions_router `/sessions/by-date/{date}` | OK |
| `GET /sessions/{id}` | sessions_router `/sessions/{id}` | OK |
| `GET /sessions/{id}/cycles` | sessions_router `/sessions/{id}/cycles` | OK |
| `GET /cycles/{id}` | sessions_router `/cycles/{id}` | OK |
| `GET /cycles/{id}/steps` | sessions_router `/cycles/{id}/steps` | OK |
| `GET /videos/{id}` | sessions_router `/videos/{id}` | OK |
| `GET /videos` | sessions_router `/videos` | OK |
| `GET /export-settings` | sessions_router `/export-settings` | OK |
| `PUT /export-settings` | sessions_router `/export-settings` | OK |
| `GET /export/csv` | sessions_router `/export/csv` | OK (v2.7.2 新增 project_id / channel_id 可选 query) |
| `GET /stats/step-averages` | sessions_router `/stats/step-averages` | OK |
| `GET /stats/cycle-averages` | sessions_router `/stats/cycle-averages` | OK |
| `GET /backup/database` | sessions_router `/backup/database` | OK |
| `DELETE /clear/all` | sessions_router `/clear/all` | OK |
| `DELETE /clear/range` | sessions_router `/clear/range` | OK |
| `GET /cleanup-settings` | sessions_router `/cleanup-settings` | OK |
| `PUT /cleanup-settings` | sessions_router `/cleanup-settings` | OK |
| `GET /storage-info` | sessions_router `/storage-info` | OK |
| `POST /cleanup/run` | sessions_router `/cleanup/run` | OK |

### Project 模块 (/api/v1/projects)
| 前端调用 (project.js) | 后端端点 (projects.py) | 状态 |
|----------------------|----------------------|------|
| `GET /projects` | projects_router `/` | OK |
| `GET /projects/{id}` | projects_router `/{id}` | OK |
| `POST /projects` | projects_router `/` | OK |
| `PUT /projects/{id}` | projects_router `/{id}` | OK |
| `DELETE /projects/{id}` | projects_router `/{id}` | OK |
| `POST /projects/{id}/activate` | projects_router `/{id}/activate` | OK |
| `GET /projects/active/current` | projects_router `/active/current` | ⚠️ 路由顺序 |

### Model 模块 (/api/v1/models)
| 前端调用 (model.js) | 后端端点 (models.py) | 状态 |
|--------------------|---------------------|------|
| `GET /models` | models_router `/` | OK |
| `GET /models/{id}` | models_router `/{id}` | OK |
| `POST /models/upload` | models_router `/upload` | OK |
| `PUT /models/{id}` | models_router `/{id}` | OK |
| `DELETE /models/{id}` | models_router `/{id}` | OK |
| `POST /models/{id}/set-active` | models_router `/{id}/set-active` | OK |
| `POST /models/{id}/parse-labels` | models_router `/{id}/parse-labels` | OK |
| `GET /models/formats/available` | models_router `/formats/available` | OK |
| `POST /models/{id}/convert` | models_router `/{id}/convert` | OK |
| `GET /models/conversions/{id}/status` | models_router `/conversions/{id}/status` | OK |
| `GET /models/{id}/conversions` | models_router `/{id}/conversions` | OK |
| `DELETE /models/conversions/{id}` | models_router `/conversions/{id}` | OK |
| `POST /models/{id}/resolve-path` | models_router `/{id}/resolve-path` | OK |

### Alarm 模块 (/api/v1/alarm)
前端 Alarm/index.vue 直接用 axios 调用，不经过封装的API文件。

### Report 模块 (/api/v1/reports)
| 前端调用 (report.js) | 后端端点 (reports.py) | 状态 |
|---------------------|---------------------|------|
| `GET /reports/summary` | reports_router `/summary` | OK |
| `GET /reports/records` | reports_router `/records` | OK |
| `GET /reports/trend` | reports_router `/trend` | OK |
| `GET /reports/daily-stats` | reports_router `/daily-stats` | OK |
| `GET /reports/export` | reports_router `/export` | OK |

### MES 模块 (/api/v1/mes) — v2.3.0+
| 前端调用 (mes.js) | 后端端点 (mes.py) | 状态 |
|-------------------|-------------------|------|
| `GET /mes/orders` | mes_router `/orders` | OK |
| `POST /mes/orders` | mes_router `/orders` | OK |
| `GET /mes/orders/{id}` | mes_router `/orders/{id}` | OK |
| `PUT /mes/orders/{id}` | mes_router `/orders/{id}` | OK |
| `POST /mes/orders/{id}/status` | mes_router `/orders/{id}/status` | OK |
| `DELETE /mes/orders/{id}` | mes_router `/orders/{id}` | OK |
| `GET /mes/orders/{id}/summary` | mes_router `/orders/{id}/summary` | OK |
| `POST /mes/orders/{id}/batches` | mes_router `/orders/{id}/batches` | OK |
| `GET /mes/orders/{id}/batches` | mes_router `/orders/{id}/batches` | OK |
| `GET /mes/workpieces` | mes_router `/workpieces` | OK |
| `POST /mes/workpieces` | mes_router `/workpieces` | OK |
| `GET /mes/workpieces/{id}` | mes_router `/workpieces/{id}` | OK |
| `GET /mes/workpieces/{id}/trace` | mes_router `/workpieces/{id}/trace` | OK |
| `POST /mes/workpieces/{id}/action` | mes_router `/workpieces/{id}/action` | OK |
| `GET /mes/workpieces/search/{kw}` | mes_router `/workpieces/search/{kw}` | OK |
| `GET /mes/defects` | mes_router `/defects` | OK |
| `POST /mes/defects` | mes_router `/defects` | OK |
| `GET /mes/defects/pareto` | mes_router `/defects/pareto` | OK |
| `GET /mes/defect-codes` | mes_router `/defect-codes` | OK |
| `POST /mes/defect-codes` | mes_router `/defect-codes` | OK |
| `PUT /mes/defect-codes/{id}` | mes_router `/defect-codes/{id}` | OK |
| `DELETE /mes/defect-codes/{id}` | mes_router `/defect-codes/{id}` | OK |

### Scanner 模块 (/api/v1/scanner) — v2.3.0+
| 前端调用 (scanner.js) | 后端端点 (scanner.py) | 状态 |
|-----------------------|-----------------------|------|
| `GET /scanner/devices` | scanner_router `/devices` | OK |
| `POST /scanner/devices` | scanner_router `/devices` | OK |
| `PUT /scanner/devices/{id}` | scanner_router `/devices/{id}` | OK |
| `DELETE /scanner/devices/{id}` | scanner_router `/devices/{id}` | OK |
| `POST /scanner/devices/test` | scanner_router `/devices/test` | OK |
| `GET /scanner/status` | scanner_router `/status` | OK |
| `GET /scanner/latest/{ch}` | scanner_router `/latest/{ch}` | OK |
| `GET /scanner/logs` | scanner_router `/logs` | OK |

### MES Gateway 模块 (/api/v1/mes/gateway) — 外部 MES 适配器
| 前端调用 (gateway.js) | 后端端点 (mes_gateway.py) | 状态 |
|------------------------|---------------------------|------|
| `GET /mes/gateway/connections` | `/mes/gateway/connections` | 对齐检查 |
| `POST /mes/gateway/connections` | `/mes/gateway/connections` | 对齐检查 |
| `GET /mes/gateway/connections/{id}` | `/mes/gateway/connections/{id}` | 对齐检查 |
| `PUT /mes/gateway/connections/{id}` | `/mes/gateway/connections/{id}` | 对齐检查 |
| `DELETE /mes/gateway/connections/{id}` | `/mes/gateway/connections/{id}` | 对齐检查 |
| `POST /mes/gateway/connections/{id}/test` | `/mes/gateway/connections/{id}/test` | 对齐检查 |
| `POST /mes/gateway/connections/{id}/push` | `/mes/gateway/connections/{id}/push` | 对齐检查 |
| `POST /mes/gateway/extra-fields` | `/mes/gateway/extra-fields` | 对齐检查 |
| `GET /mes/gateway/extra-fields` | `/mes/gateway/extra-fields` | 对齐检查 |
| `GET /mes/gateway/extra-fields-schema` | `/mes/gateway/extra-fields-schema` | 对齐检查 |
| `GET /mes/gateway/logs` | `/mes/gateway/logs` | 对齐检查 |

### Operators 模块 (/api/v1/operators) — 操作员管理
| 前端调用 (operators.js) | 后端端点 (operators.py) | 状态 |
|-------------------------|-------------------------|------|
| `GET /operators` | operators_router `/operators` | 对齐检查 |
| `POST /operators` | operators_router `/operators` | 对齐检查 |
| `PUT /operators/{id}` | operators_router `/operators/{id}` | 对齐检查 |
| `DELETE /operators/{id}` | operators_router `/operators/{id}` | 对齐检查 |
| `POST /operators/set-current` | operators_router `/operators/set-current` | 对齐检查 |
| `GET /operators/current` | operators_router `/operators/current` | 对齐检查 |

**与 Session/Data 同步：** `sessions.py` 的 `SessionResponse` / `CycleResponse` 含 `operator_id`、`operator_name`；`GET /sessions`、`GET /sessions/by-date/{date}`、`GET /sessions/{id}/cycles` 等支持查询参数 `operator_id`（或项目内等价命名）。前端 `data.js` 的 `getSessionsByDate` 等需传递 `operatorId` 与后端一致。`source.py` 的 `get_detection_results` 返回体中的操作员字段需与 Monitor 展示对齐。

## 对齐检查流程

### 快速检查（针对特定API）
1. Grep 前端调用点（api/*.js 和 views/**/*.vue）
2. Grep 后端端点定义（api/*.py）
3. 对比路径、方法、参数名、响应字段

### 全量检查（`$ARGUMENTS` = 'full-check'）
1. 读取所有前端API文件，列出所有调用
2. 读取所有后端路由定义
3. 逐条对比
4. 检查请求参数和响应字段的一致性

## 修复原则
1. **前端适配后端:** 如果后端已稳定，前端改路径
2. **后端不轻易改路由:** Electron 的关机流程也直接调用后端API
3. **路由顺序:** 具体路径放参数路径前面
4. **向后兼容:** 新增字段可以，不删旧字段

## v2.5.0 新增/修改 API

### scanner.py
- `DELETE /scanner/logs` — 清空所有扫码记录
- `ScannerCreate/ScannerUpdate` 新增 `rebind_mode`, `bind_timing` 字段

### sessions.py
- `CycleResponse` 新增 `serial_no` 字段（LEFT JOIN 工件表获取）

### source.py
- `get_detection_results` 返回的 `mes` 字段新增 `scan_event`, `rebind_prompt`, `warn_no_barcode`
- `POST /detection/rebind` — 手动重绑工件

### mes_gateway.py（已有，适配器扩展）
- `modbus_rtu` 适配器类型自动通过 `test_connection` 和 `dispatch` 工作
- 无需新增 API 端点

### wmax.py（新增模块）
- WMax 扫码器设备管理 API（发现、连接、配置、高级控制）

## v2.6.0 新增/修改 API

### cluster.py（新增模块，挂载 /api/v1/cluster）
- `GET /cluster/config` — 获取集群配置
- `PUT /cluster/config` — 更新集群配置
- `POST /cluster/report` — 从机上报检测数据
- `GET /cluster/boxes` — 获取待汇总/已完成箱子列表
- `GET /cluster/health` — 集群健康检查
- `POST /cluster/heartbeat` — 副机心跳注册（v2.7.1+, body: station_id/ip/project/channels/status）
- `GET /cluster/slaves` — 主机查询已连接副机列表（v2.7.1+, 20秒超时自动清理离线副机）
- 前端: `frontend/src/api/cluster.js`（含 `sendHeartbeat`, `getConnectedSlaves`）

### external_device.py（新增模块，挂载 /api/v1/external-devices）
- CRUD 外部设备 (TCP/Modbus TCP/串口/HTTP 轮询)
- `POST /external-devices/{id}/connect` — 连接设备
- `POST /external-devices/{id}/disconnect` — 断开设备
- `GET /external-devices/{id}/logs` — 获取设备数据日志
- 前端: `frontend/src/api/external_device.js`

### channel_manager.py（修改）
- `PUT /workstations/channel-config` — 持久化单通道配置（接受任意字段 dict）
- `GET /workstations` 响应新增 `source_configs` 字段
- `save_channel_source(ch_id, cfg, merge=True/False)` 方法

### mes_gateway.py（修改）
- `ConnectionCreate/ConnectionUpdate` 新增 `bound_channels: Optional[List[int]]`
- `GET /mes/gateway/connections/by-channel?channel=X` — 按通道查询绑定连接
- `_serialize_conn()` 响应新增 `bound_channels` 字段

### scanner.py（修改）
- `ScannerCreate/ScannerUpdate` 新增 `broadcast_channels` 字段
- 扫码结果可广播到多通道

### alarm.py（修改）
- `AlarmRouter` 替代单一 `AlarmManager`，管理多通道独立报警设备
- 所有报警 API 支持 `channel` 查询参数

## v2.7.2 新增/修改 API

### sessions.py（修改）
- `GET /export/csv` 新增两个可选 query 参数：
  - `project_id: Optional[int]` — 当 `export_type=all` 时按项目过滤 DetectionSession；不传或传 `null` 表示全部项目
  - `channel_id: Optional[int]` — 同上，按工位（0/1/2/3）过滤；不传表示全部工位
  - `session` 和 `cycle` 两种 `export_type` 不受该参数影响（已按 id 定位）
- CSV 输出变化（向后兼容，仅追加列/行）：
  - 元数据新增"项目 / 工位"两行（占位符 `[全部]` 表示未过滤）
  - "会话列表"表头追加"工位"列，行值格式为 `工位N`
  - "周期详情"和"步骤详情"分组标题改为 `[项目名 / 工位N] 会话 XXX`
- 前端对应 API（`frontend/src/api/data.js`）新增 `projectId` / `channelId` 形参，透传到后端

### 降工位清理钩子（新增公共接口）
- `backend/services/mes_hooks.py::MESHookManager.on_channel_removed(channel_id)` — 清 6 个按 channel_id 存的 dict
- `backend/api/alarm.py::AlarmRouter.on_channel_removed(channel_id)` — 停报警 + 灯全灭 + 断串口 + 从 managers pop
- 调用方：`ChannelManager.set_channel_count()` 降工位循环，其他模块可按需调用
- **注意**：`ScannerService` / `ExternalDeviceService` / `ClusterCollector` 的 key 分别是 `device_id` / `station_id`，不按 channel_id 清理，避免误伤硬件配置

### 画面变换 API（v2.7.5 新增）

| 前端调用 | 后端路由 | 说明 |
|---|---|---|
| `GET /source/transform/config?channel=N` | `source.py::get_transform_config` | 读取指定通道的旋转/镜像配置 |
| `POST /source/transform/config?channel=N` | `source.py::set_transform_config` | 写入 + 立即生效 + 保存到 `device_config.json` 的 `per_channel[str(N)]` |

Body（Pydantic `TransformConfigRequest`）：
```json
{ "rotation": 0|90|180|270, "flip_h": false, "flip_v": false }
```

前端调用点：`frontend/src/views/Settings/index.vue` 的「显示设置 → 画面变换」卡片，用 `transformChannel` 选择通道，`rotation/flip_h/flip_v` 绑定到表单。

**坐标对齐**：变换在 `_capture_loop` 里紧跟 `frame.copy()` 后执行，下游推理、MJPEG、录像拿到的都是变换后帧，检测框坐标直接对齐，前端无需二次换算。

### 外部设备 API 已知坑（v2.7.5 修复）
- **路由顺序**：`DELETE /external_device/{device_id}` 必须**放在**所有静态路径（`/logs`、`/scan` 等）之后注册，否则 FastAPI 会把 `/logs` 当成 `device_id="logs"` 报 `int_parsing`
- **错误返回**：`create_device/update_device` 如果设备立即连接失败（串口未插、IP 不通），不再返回 400，而是 200 + `warning` 字段，前端用 `ElMessage.warning` 展示
- **POST/PUT 入口**：`_sanitize_device_payload(data)` 对 `name/serial_port/ip/station_id` strip，防止前端拷贝粘贴带空格
