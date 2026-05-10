# 04 — 对外 API + 硬件 IO 出入口完整清单

> 适用版本：v3.6.0（HEAD `5c97791`）
> 本文目的：把整个系统**所有跨边界的 IO 通道**铺平——HTTP API、硬件 IO、跨进程 IPC、文件系统、网络端口、第三方 SDK、环境变量。让插件系统设计能精确知道"插件能够触达什么 / 不能动什么 / 哪些是稀缺资源"。
>
> 阅读顺序：第二节边界类型 → 第三节 HTTP API 完整表（最大头）→ 第四~六节硬件/IPC/文件 → 第七~九节端口/环境/第三方 → 第十节给插件看的注意事项。
>
> 配套：`01_module_map.md`（依赖拓扑）/ `03_extension_points.md`（扩展点接口契约）。

---

## 一、统计速查

| 类别 | 数量 | 备注 |
|---|---|---|
| HTTP API 端点（总） | **264** | 不含 4 个特殊端点 |
| HTTP API 端点（19 组路由前缀） | 260 | 见第三节 |
| 特殊 HTTP 端点（main.py 直挂） | 5 | `/`, `/health`, `/video_feed`, `/snapshot`, shutdown 系列 |
| StaticFiles 挂载 | 2 | `/uploads/*`, `/recordings/*` |
| Electron IPC channel | 11 | invoke 4 + on/send 7 |
| 硬件视频源类型 | 6 | USB / RTSP / 海康 NVR (HCNetSDK) / 海康工业相机 (MvCamera) / 视频文件 / 单图 |
| 硬件串口设备类型 | 2 | Modbus 灯柱蜂鸣器 / 称重器（USB Serial） |
| 网络外设端口 | 4+ | 扫码 LON 55256 / WMax 55266+76+86 / Cluster slave 上报 / MES Gateway |
| 监听端口 | 1 | uvicorn 8001 (release 时由 Inno Setup 默认；开发可自由) |
| 第三方 SDK | 5 | 海康 HCNetSDK / 海康 MvCamera / FFmpeg / pymodbus / Ultralytics YOLO |
| 环境变量 | 8+ | 见第八节 |

---

## 二、IO 边界类型分类（先看清"边界在哪儿"）

```
┌─────────────────────────────────────────────────────────────────────┐
│                          外部世界                                     │
│                                                                       │
│   工人/操作员 → Electron Renderer (Chromium) ←─┐                       │
│                       │ HTTP                  │                       │
│                       ▼                       │                       │
│                  uvicorn 8001                 │ IPC                   │
│                  ┌──────────────┐             │                       │
│                  │ FastAPI app  │       ┌─────┴─────┐                  │
│                  │ + SQLite DB  │       │ Electron   │                 │
│                  │ + 后台线程   │ ◄───── │ Main       │                 │
│                  │              │ stdout │ + License  │                 │
│                  └──┬──┬──┬──┬──┘        │ + Backend  │                 │
│                     │  │  │  │           │   Manager  │                 │
│      ┌──────────────┘  │  │  └────────┐  └────────────┘                 │
│      ▼                 ▼  ▼           ▼                                │
│  ┌────────┐  ┌──────┐ ┌────┐  ┌────────────┐                            │
│  │USB 摄像│  │RTSP  │ │海康│  │串口/Modbus  │                            │
│  │OpenCV  │  │ 网络 │ │SDK │  │报警 + 称重  │                            │
│  └────────┘  └──────┘ └────┘  └────────────┘                            │
│                                                                       │
│  ┌────────────────────┐                                                │
│  │ TCP 扫码器           │  ← LON 55256 / WMax 55266+76+86               │
│  └────────────────────┘                                                │
│                                                                       │
│  ┌────────────────────┐                                                │
│  │ 外部 MES 系统        │  ← HTTP / Modbus 推送                         │
│  └────────────────────┘                                                │
│                                                                       │
│  ┌────────────────────┐                                                │
│  │ 集群 host/slave     │  ← HTTP report + heartbeat                    │
│  └────────────────────┘                                                │
└─────────────────────────────────────────────────────────────────────┘

文件系统:
  - %APPDATA%/tianjun-ai-vision/data/sql_app.db (主 DB)
  - %APPDATA%/tianjun-ai-vision/data/uploads/  (模型、图片)
  - %APPDATA%/tianjun-ai-vision/data/recordings/ (FFmpeg 录像)
  - %APPDATA%/tianjun-ai-vision/data/exports/  (自定义导出)
  - %APPDATA%/tianjun-ai-vision/data/workstation_config.json
  - %APPDATA%/tianjun-ai-vision/data/current_operator.json
  - %APPDATA%/tianjun-ai-vision/data/counters_snapshot.json
```

边界类型 6 种：

| 类型 | 用途 | 数量 |
|---|---|---|
| **HTTP API（短连接）** | 业务 CRUD | 264 + 4 |
| **HTTP 流（长连接）** | MJPEG / SSE （SSE 实际无） | 1（video_feed） |
| **StaticFiles** | 文件下载 | 2 |
| **TCP 客户端** | 扫码器 / 集群 / MES 推送 | 4+ |
| **OS 硬件接口** | 摄像头 / 串口 / GPU | 4 类 |
| **进程间通信** | Electron IPC / signal / stdout | 13+ |

---

## 三、HTTP API 完整清单（19 组路由前缀，260 端点）

> 每个端点格式：`METHOD 路径 — 描述 / 调用方 / 速率`
>
> 速率说明：`once` 用户主动触发 / `poll-{秒}s` 周期轮询 / `event` 事件触发 / `boot` 启动一次 / `stream` 长连接

### 3.1 `/api/v1/projects/*` — 项目管理（7）

挂载于 `backend/api/__init__.py` → `api_router.include_router(projects.router, prefix="/projects")`

| Method | Path | 描述 | 调用方 / 速率 |
|---|---|---|---|
| GET | `/projects` | 项目列表（分页 skip/limit） | Project view, once |
| GET | `/projects/{project_id}` | 项目详情（含 7 个 JSON 字段） | Project view, once |
| POST | `/projects` | 创建项目 | Project view, once |
| PUT | `/projects/{project_id}` | 更新项目 | Project / Settings / Alarm view, once |
| DELETE | `/projects/{project_id}` | 删除项目 | Project view, once |
| POST | `/projects/{project_id}/activate` | 激活项目（设 is_active=True，全局唯一） | Project view, once |
| GET | `/projects/active/current` | 获取当前激活项目 | Monitor / 启动, boot+poll |

### 3.2 `/api/v1/models/*` — 模型管理（14）

| Method | Path | 描述 | 调用方 / 速率 |
|---|---|---|---|
| GET | `/models` | 模型列表 | Model view, once |
| GET | `/models/formats/available` | 可用格式 + 推荐 | Project view, once |
| GET | `/models/formats/diagnosis` | GPU/CUDA/TRT 诊断 | Project view, once |
| GET | `/models/conversions/{conv_id}/status` | 转换状态 | Project view, poll-2s |
| DELETE | `/models/conversions/{conv_id}` | 删除转换记录 | Project view, once |
| GET | `/models/{model_id}` | 模型详情 | Project / Source view, once |
| POST | `/models/upload` | 上传模型文件（multipart） | Model view, once |
| POST | `/models/{model_id}/parse-labels` | 重新解析标签 | Model view, once |
| PUT | `/models/{model_id}` | 更新模型信息 | Model view, once |
| DELETE | `/models/{model_id}` | 删除模型 | Model view, once |
| POST | `/models/{model_id}/set-active` | 设为当前使用 | Model view, once |
| POST | `/models/{model_id}/convert` | 发起格式转换（PT→TRT等） | Project view, once |
| GET | `/models/{model_id}/conversions` | 该模型所有转换版本 | Project view, once |
| POST | `/models/{model_id}/resolve-path` | 按 GPU 解析对应路径 | Source / Monitor / Project, once |

### 3.3 `/api/v1/cameras/*` — 旧式相机表（8）⚠️ 死代码

`backend/api/cameras.py` 有 8 个端点，但 `frontend/src/api/camera.js` 全前端**无 import** → **死代码**（详见 05 文档）。

| Method | Path | 状态 |
|---|---|---|
| GET | `/cameras` | 死代码 |
| GET | `/cameras/{camera_id}` | 死代码 |
| POST | `/cameras` | 死代码 |
| PUT | `/cameras/{camera_id}` | 死代码 |
| DELETE | `/cameras/{camera_id}` | 死代码 |
| GET | `/cameras/{camera_id}/stream` | 死代码（与 `/video_feed` 重复） |
| GET | `/cameras/{camera_id}/test` | 死代码 |
| GET | `/cameras/default/stream` | 死代码 |

### 3.4 `/api/v1/tasks/*` — 离线推理任务（7）⚠️ 半死代码

`api/task.js` 全前端**无 import**，但后端可能仍有 record API 被使用？需进一步确认。

| Method | Path | 描述 |
|---|---|---|
| GET | `/tasks` | 任务列表 |
| GET | `/tasks/{task_id}` | 任务详情 |
| POST | `/tasks` | 创建任务 |
| PUT | `/tasks/{task_id}` | 更新任务 |
| DELETE | `/tasks/{task_id}` | 删除任务 |
| POST | `/tasks/record` | 记录单次检测（multipart 含图片） |
| DELETE | `/tasks/batch/clear` | 批量清空 |

### 3.5 `/api/v1/reports/*` — 报表（5）⚠️ 部分死代码

`views/Report/index.vue` 路由未注册（死代码），但 `api/report.js: getSummary / getDailyStats / exportCsvReport` 仍在 Report view 内部用（封死的子图）。

| Method | Path | 描述 | 调用方 |
|---|---|---|---|
| GET | `/reports/summary` | 总体摘要 | Report 死视图 |
| GET | `/reports/records` | 记录列表 | Report 死视图 |
| GET | `/reports/trend` | 趋势图 | Report 死视图 |
| GET | `/reports/daily-stats` | 每日统计 | Report 死视图 |
| GET | `/reports/export` | 导出报表 | Report 死视图 |

### 3.6 `/api/v1/alarm/*` — 报警系统（12）

| Method | Path | 描述 | 调用方 / 速率 |
|---|---|---|---|
| GET | `/alarm/ports` | 列出可用串口（按通道） | Alarm view, once |
| POST | `/alarm/connect` | 连接报警器（指定串口） | Alarm view, once |
| POST | `/alarm/disconnect` | 断开报警器 | Alarm view, once |
| GET | `/alarm/status` | 报警器状态（channel=-1 全部） | Alarm view, poll-1s |
| POST | `/alarm/config` | 保存报警配置 | Alarm view, once |
| POST | `/alarm/reload` | v2.7.3 重新加载配置（共享/独立切换） | Alarm view, once |
| POST | `/alarm/test` | 测试报警 | Alarm view, once |
| POST | `/alarm/trigger/{event_type}` | 手动触发报警 | Alarm view, once |
| POST | `/alarm/stop` | 停止报警 | Alarm view, once |
| POST | `/alarm/idle-light/start` | 开工作指示灯 | Alarm view, once |
| POST | `/alarm/idle-light/stop` | 关工作指示灯 | Alarm view, once |
| GET | `/alarm/protocols` | 列出支持协议 | Alarm view, once |

### 3.7 `/api/v1/system/*` — 系统 KV 配置（4）

| Method | Path | 描述 | 调用方 / 速率 |
|---|---|---|---|
| GET | `/system/display` | display 字段集合 | useSystemStore boot, boot |
| PUT | `/system/display` | 更新 display 字段 | useSystemStore save, once |
| GET | `/system/license-cache` | 读 License 缓存 | 后端 export 模块, internal |
| PUT | `/system/license-cache` | 写 License 缓存 | renderer IPC 转发, once |

> **插件系统强烈推荐使用此前缀**——把 `/system/display` / `/system/license-cache` 模式扩展到 `/system/plugin/{customer_code}` 或直接复用 `SystemConfig` KV 表（见 03 文档 A8）。

### 3.8 `/api/v1/export/*` — 自定义导出（12 + 9 = 21）

`export_custom.py`（12 个）：

| Method | Path | 描述 |
|---|---|---|
| GET | `/export/fields` | 308 字段全集（带分组、类型） |
| GET | `/export/templates` | 模板列表（含 is_system 区分） |
| GET | `/export/templates/{tpl_id}` | 模板详情 |
| POST | `/export/templates` | 创建模板 |
| PUT | `/export/templates/{tpl_id}` | 更新模板 |
| DELETE | `/export/templates/{tpl_id}` | 删除模板（系统预设禁删） |
| POST | `/export/templates/{tpl_id}/clone` | 克隆模板 |
| POST | `/export/preview` | 预览渲染（不落盘） |
| POST | `/export/render` | 渲染并下载（一次性导出） |
| POST | `/export/templates/{template_id}/upload-template-file` | 上传 docx/xlsx 模板文件 |
| DELETE | `/export/templates/{template_id}/template-file` | 删除模板文件 |
| GET | `/export/templates/{template_id}/template-file` | 下载模板文件 |

`export_realtime.py`（9 个）：

| Method | Path | 描述 |
|---|---|---|
| GET | `/export/realtime-rules` | 实时规则列表 |
| GET | `/export/realtime-rules/{rule_id}` | 规则详情 |
| POST | `/export/realtime-rules` | 创建规则 |
| PUT | `/export/realtime-rules/{rule_id}` | 更新规则 |
| DELETE | `/export/realtime-rules/{rule_id}` | 删除规则 |
| POST | `/export/realtime-rules/{rule_id}/toggle` | 启停切换 |
| POST | `/export/realtime-rules/{rule_id}/test-run` | 立即触发测试 |
| GET | `/export/realtime-rules/{rule_id}/logs` | 规则执行日志 |
| GET | `/export/run-logs` | 全部 run-log（分页） |

### 3.9 `/api/v1/data/*` — Session/Cycle/Step 数据（24）

挂载于 `main.py` `app.include_router(sessions_router, prefix="/api/v1/data")`，`sessions.py` 内部又挂了 `sessions_stats.router` + `sessions_maintenance.router`。

`sessions.py` 主端点（15 个）：

| Method | Path | 描述 |
|---|---|---|
| POST | `/data/sessions` | 创建会话 |
| PUT | `/data/sessions/{session_id}/end` | 结束会话 |
| GET | `/data/sessions` | 会话列表 |
| GET | `/data/sessions/dates` | 有数据的日期列表 |
| GET | `/data/sessions/by-date/{date}` | 按日期看会话 |
| GET | `/data/sessions/{session_id}` | 会话详情 |
| GET | `/data/sessions/{session_id}/cycles` | 会话内 cycles |
| GET | `/data/cycles/by-serial/{serial_no}` | 按条码查 cycles |
| GET | `/data/cycles/{cycle_id}` | cycle 详情 |
| GET | `/data/cycles/{cycle_id}/steps` | cycle 内 steps |
| GET | `/data/videos/{video_id}` | 视频文件下载（自动转浏览器格式） |
| GET | `/data/videos` | 视频列表 |
| GET | `/data/export-settings` | data_export_settings 读 |
| PUT | `/data/export-settings` | data_export_settings 写 |
| GET | `/data/export/csv` | 4 种快捷 CSV 导出（pt_mode/ct_mode 联动） |

`sessions_stats.py`（2 个）：
| Method | Path | 描述 |
|---|---|---|
| GET | `/data/stats/step-averages` | 步骤平均（v3.5.0 置信度聚合） |
| GET | `/data/stats/cycle-averages` | 周期平均 |

`sessions_maintenance.py`（7 个）：
| Method | Path | 描述 |
|---|---|---|
| GET | `/data/backup/database` | 下载 sql_app.db 备份 |
| DELETE | `/data/clear/all` | 清空所有历史数据 |
| DELETE | `/data/clear/range` | 按日期范围清空 |
| GET | `/data/cleanup-settings` | 数据清理设置 |
| PUT | `/data/cleanup-settings` | 更新清理设置 |
| GET | `/data/storage-info` | 存储空间信息 |
| POST | `/data/cleanup/run` | 立即执行清理 |

### 3.10 `/api/v1/source/*` — 视频源 + 检测核心（42）

挂载于 `main.py` `app.include_router(source_router, prefix="/api/v1/source")`

**摄像头 / GPU / 配置（10 个）**：
| Method | Path | 描述 |
|---|---|---|
| GET | `/source/cameras?refresh=` | 列出可用摄像头 |
| GET | `/source/gpu/list` | 可用 GPU 列表 |
| GET | `/source/gpu/current` | 当前推理设备 |
| POST | `/source/gpu/set` | 设置推理设备 |
| GET | `/source/stream/config` | 视频流配置（fps/fp16/MediaPipe） |
| POST | `/source/stream/config` | 设置视频流配置 |
| GET | `/source/transform/config?channel=` | 旋转/镜像配置 |
| POST | `/source/transform/config?channel=` | 设置旋转/镜像 |
| GET | `/source/kalman/config` | 卡尔曼参数 |
| POST | `/source/kalman/config` | 设置卡尔曼 |

**视频源启停（6 类，14 个）**：
| Method | Path | 描述 |
|---|---|---|
| POST | `/source/camera/start?channel=` | 启动 USB 摄像头 |
| POST | `/source/camera/stop?channel=` | 停止 USB |
| POST | `/source/rtsp/start?channel=` | 启动 RTSP |
| GET | `/source/hikvision/cameras` | 海康工业相机列表 |
| POST | `/source/hikvision/start?channel=` | 启动海康工业相机 |
| POST | `/source/hikvision/stop?channel=` | 停止海康工业相机 |
| GET | `/source/hikvision/status` | 海康工业相机状态 |
| POST | `/source/hcnetsdk/start` | 启动海康 NVR |
| POST | `/source/hcnetsdk/stop` | 停止海康 NVR |
| GET | `/source/hcnetsdk/status` | 海康 NVR 状态 |
| POST | `/source/video/upload` | 上传视频文件 |
| POST | `/source/video/start` | 启动视频文件回放 |
| POST | `/source/video/stop` | 停止视频回放 |
| POST | `/source/video/speed` | 调节回放速度 |
| POST | `/source/video/progress` | 跳转进度 |
| GET | `/source/video/info` | 视频信息（时长/帧数） |
| POST | `/source/image/upload` | 上传单图 |
| POST | `/source/image/set` | 设为当前图源 |

**检测控制（11 个）**：
| Method | Path | 描述 | 调用速率 |
|---|---|---|---|
| POST | `/source/detection/start` | 启动检测 | once |
| POST | `/source/detection/stop` | 停止检测 | once |
| POST | `/source/detection/pause` | 暂停 | once |
| POST | `/source/detection/resume` | 恢复 | once |
| POST | `/source/detection/standby` | 待机（保画面） | once |
| POST | `/source/detection/resume-inference` | 恢复推理 | once |
| POST | `/source/detection/reset-stats` | 重置统计 | once |
| GET | `/source/detection/results?channel=` | **★ 核心轮询接口**（含 30+ 字段） | **poll-150ms (~6.7Hz)** |
| POST | `/source/detection/set-project` | 应用 Project 配置 | once |
| POST | `/source/detection/rebind` | 工件重新绑定 | once |
| POST | `/source/detection/clear_pending_scan` | 清待处理扫码 | once |
| POST | `/source/detection/recording-failures/clear` | 清录像失败 | once |

**通用状态（2 个）**：
| Method | Path | 描述 |
|---|---|---|
| GET | `/source/status?channel=` | 视频源状态 |
| GET | `/source/health` | 健康检查 |

### 3.11 `/api/v1/workstations/*` — 多工位（6）

挂载于 `main.py` 的 `channel_manager.router`，**注意此 router 自身的路由从 `/` 开始**，所以实际路径前缀是 `/api/v1/{spec}`：

| Method | Path | 描述 | 调用方 |
|---|---|---|---|
| GET | `/api/v1/` | 通道总览（含 channel_count + 各通道 status） | Source view, boot+once |
| POST | `/api/v1/mode` | 设置 channel_count + 各通道初始配置 | Source view, once |
| PUT | `/api/v1/channel-config` | 单通道配置覆盖（merge=False） | Source view, once |
| POST | `/api/v1/{channel_id}/gpu` | 单通道 GPU 配置 | Source view, once |
| GET | `/api/v1/gpu-allocation` | GPU 分配总览 | Source view, once |
| GET | `/api/v1/{channel_id}/status` | 单通道状态 | Source view, once |

> ⚠️ **API 命名歧义**：前端 `api/detection.js: getWorkstations` 实际请求的是 `GET /api/v1/`（顶层路径）。**对插件系统是个坑**：直接挂 `/api/v1/plugins` 可能与此冲突。**建议改用 `/api/v1/workstations` 显式前缀**作为插件系统先决条件。

### 3.12 `/api/v1/scanner/*` — 扫码器（17）

| Method | Path | 描述 |
|---|---|---|
| GET | `/scanner/devices` | 设备列表 |
| POST | `/scanner/discover?subnet=&port=&timeout=` | 子网扫描发现 |
| POST | `/scanner/devices` | 创建设备 |
| PUT | `/scanner/devices/{device_id}` | 更新设备 |
| DELETE | `/scanner/devices/{device_id}` | 删除设备 |
| POST | `/scanner/devices/test?ip=&port=&device_type=` | 测试连接 |
| POST | `/scanner/trigger?device_id=&ip=` | 手动触发扫码（发 LON） |
| POST | `/scanner/simulate` | 模拟扫码（无硬件） |
| GET | `/scanner/status` | 全部设备状态 |
| GET | `/scanner/latest/{channel_id}` | 最新扫码 |
| GET | `/scanner/logs?device_id=&...` | 扫码日志 |
| DELETE | `/scanner/logs` | 清空日志 |
| GET | `/scanner/check-container-mode?channel_id=` | v3.4.0 容器模式校验 |
| GET | `/scanner/scan-pair/active?channel_id=` | v3.3.0 scan_pair 活跃码 |
| POST | `/scanner/scan-pair/stop` | v3.3.0 收尾最后窗口 |
| GET | `/scanner/disable-status` | 工位禁用状态 |
| POST | `/scanner/disable-toggle` | v3.4.2 工位禁用切换 |

### 3.13 `/api/v1/scanner/wmax/*` — WMax 扫码器（36，**最庞杂**）

WMax 是康耐视专属协议，三端口（55266 RPC + 55276 视频 + 55286 图像）。

**发现/连接（4）**：`/wmax/discover` / `/wmax/auto-discover` / `/wmax/discovered` / `/wmax/connect` / `/wmax/disconnect` / `/wmax/status` / `/wmax/handshake`

**配置加载（4）**：`/wmax/load-config` / `/wmax/device-features` / `/wmax/params` / `/wmax/save-params`

**输出/指示灯（2）**：`/wmax/output-config` / `/wmax/indicator-config`

**预设（2）**：`/wmax/preset/load` / `/wmax/preset/save`

**自动调焦/调谐（3）**：`/wmax/autofocus` / `/wmax/autotune` / `/wmax/cancel-tune`

**视频/图像（5）**：`/wmax/video` / `/wmax/trigger-image` / `/wmax/image` / `/wmax/image/raw` / `/wmax/stream`

**触发/读码（4）**：`/wmax/trigger` / `/wmax/last-code` / `/wmax/run-mode` / `/wmax/read-rate/start` / `/wmax/read-rate/stop` / `/wmax/read-rate/result`

**控制（4）**：`/wmax/reboot` / `/wmax/reset` / `/wmax/indicate` / `/wmax/virtual/create` / `/wmax/virtual/delete`

**调试（2）**：`/wmax/debug/config-raw` / `/wmax/debug-config`

> 由于 WMax 端点繁多且专用性极强，**插件系统应避免修改这些端点**，仅通过 P1（Scanner _on_data_received hook）拿到扫码数据即可。

### 3.14 `/api/v1/external-devices/*` — 外部设备（10）

| Method | Path | 描述 |
|---|---|---|
| GET | `/external-devices/` | 列表 |
| POST | `/external-devices/` | 创建 |
| GET | `/external-devices/status` | 状态 |
| POST | `/external-devices/test` | 测试连接 |
| POST | `/external-devices/barcode` | 注入条码（无扫码器场景） |
| POST | `/external-devices/simulate` | 模拟数据 |
| GET | `/external-devices/logs` | 日志 |
| DELETE | `/external-devices/logs` | 清空日志 |
| PUT | `/external-devices/{device_id}` | 更新 |
| DELETE | `/external-devices/{device_id}` | 删除 |

### 3.15 `/api/v1/mes/*` — MES 业务（26）

工单（11）：
```
GET    /mes/orders
POST   /mes/orders
POST   /mes/orders/receive          # MES 反向接收工单
GET    /mes/orders/{order_id}
PUT    /mes/orders/{order_id}
POST   /mes/orders/{order_id}/status
DELETE /mes/orders/{order_id}
GET    /mes/orders/{order_id}/summary
PUT    /mes/orders/{order_id}/extra-data
POST   /mes/orders/{order_id}/batches
GET    /mes/orders/{order_id}/batches
```

工件（7）：
```
GET    /mes/workpieces
POST   /mes/workpieces
GET    /mes/workpieces/{workpiece_id}
GET    /mes/workpieces/{workpiece_id}/trace    # 工件全生命追溯
POST   /mes/workpieces/{workpiece_id}/action
GET    /mes/workpieces/search/{keyword}
DELETE /mes/workpieces/{workpiece_id}
```

缺陷（4）：
```
GET    /mes/defects
POST   /mes/defects
GET    /mes/defects/pareto                      # 帕累托图
DELETE /mes/defects/{defect_id}
```

缺陷码（4）：
```
GET    /mes/defect-codes
POST   /mes/defect-codes
PUT    /mes/defect-codes/{code_id}
DELETE /mes/defect-codes/{code_id}
```

### 3.16 `/api/v1/mes/gateway/*` — MES 推送网关（12）

> ✅ **2026-05-09 已验证**：`mes_gateway.py:15` 的 `APIRouter(prefix="/mes/gateway")` 与 `main.py:789` 的 `prefix=settings.API_V1_STR` 双层叠加，最终路径就是 `/api/v1/mes/gateway/*`，与 AGENTS.md 完全一致。当初 INCONSIST-2 是漏读 APIRouter 自带 prefix 导致的误判，已撤销该条目。

| Method | Path | 描述 |
|---|---|---|
| GET | `/connections` | MES 连接列表 |
| POST | `/connections` | 创建连接 |
| GET | `/connections/by-channel` | 按通道筛选 |
| GET | `/connections/{conn_id}` | 详情 |
| PUT | `/connections/{conn_id}` | 更新 |
| DELETE | `/connections/{conn_id}` | 删除 |
| POST | `/connections/{conn_id}/test` | 测试推送（不重试） |
| POST | `/connections/{conn_id}/push` | 手动推送 |
| POST | `/extra-fields` | 设额外字段 |
| GET | `/extra-fields` | 读额外字段 |
| GET | `/extra-fields-schema` | 读 schema 定义 |
| GET | `/logs` | MES 通信日志 |

### 3.17 `/api/v1/operators/*` — 操作员（6）

| Method | Path | 描述 | 调用方 |
|---|---|---|---|
| GET | `/operators` | 操作员列表 | Settings / Monitor view, once |
| POST | `/operators` | 创建 | Settings, once |
| PUT | `/operators/{op_id}` | 更新 | Settings, once |
| DELETE | `/operators/{op_id}` | 软删除（active=False） | Settings, once |
| POST | `/operators/set-current` | 设当前工位操作员（落 `current_operator.json`） | Monitor / Settings, once |
| GET | `/operators/current?channel_id=` | 读当前操作员 | Monitor view, boot+poll |

### 3.18 `/api/v1/cluster/*` — 集群主从（10）

| Method | Path | 描述 | 谁用 |
|---|---|---|---|
| GET | `/cluster/config` | 集群配置 | Settings cluster panel, once |
| PUT | `/cluster/config` | 设置 standalone/host/slave | once |
| POST | `/cluster/report` | **slave 上报 cycle**（slave→host） | slave 后端, event |
| GET | `/cluster/boxes` | 箱子聚合列表 | host UI, poll |
| GET | `/cluster/boxes/{box_serial}` | 箱子详情 | once |
| POST | `/cluster/heartbeat` | **slave 心跳上报** | slave 后端, poll-15s |
| GET | `/cluster/slaves` | 已连 slave 列表 | host UI, poll |
| DELETE | `/cluster/boxes/{box_serial}` | 删单条聚合 | host UI, once |
| DELETE | `/cluster/boxes` | 清空聚合 | host UI, once |
| GET | `/cluster/health` | 集群健康 | once |

### 3.19 `/api/v1/debug/*` — 诊断（2）

| Method | Path | 描述 |
|---|---|---|
| GET | `/debug/channels` | 通道诊断（每个 VSM 状态摘要） |
| POST | `/debug/test_cluster_flow` | 端到端集群回放（用真实 cycle_id） |

---

## 四、特殊端点（main.py 直挂，不走 router）

| Method | Path | 描述 | 速率 / 调用方 |
|---|---|---|---|
| GET | `/` | 欢迎页（API meta） | Electron 健康检查时偶尔, once |
| GET | `/health` | 健康检查（return `{status: "healthy"}`） | Electron 启动时 polling, poll-1s |
| POST | `/api/v1/source/shutdown/step/{step}` | 8 步关机的单步（`stop_detection`/`save_counters`/`end_cycle`/`end_session`/`stop_recording`/`release_camera`/`release_model`/`cleanup`） | Electron 关机, once × 8 |
| POST | `/api/v1/source/shutdown/complete` | 整体关机（spawn delayed_exit 0.5s 后 os._exit(0)） | Electron 关机, once |
| GET | `/video_feed?channel=N` | **★ MJPEG 长连接** | Renderer `<img>`, **stream** |
| GET | `/snapshot?channel=N` | 单帧 JPEG | Renderer 截图组件, poll-1s |

**StaticFiles**（mount at startup）：
| Path | 服务目录 | 用途 |
|---|---|---|
| `/uploads/*` | `settings.UPLOAD_DIR` (默认 `%APPDATA%/.../uploads/`) | 模型文件下载、上传图片回显 |
| `/recordings/*` | `settings.RECORDING_DIR` | 录像回放 |

**额外（开发模式开放）**：
| Path | 描述 | 控制 |
|---|---|---|
| `/docs` | Swagger UI | `ENABLE_API_DOCS=0` 关闭 |
| `/redoc` | ReDoc | 同上 |
| `/openapi.json` | OpenAPI schema | 同上 |

---

## 五、硬件 IO 出入口

### 5.1 视频源 6 种

| 类型 | 内部标识 | 接入方式 | OS 依赖 | 主要文件 |
|---|---|---|---|---|
| **USB 摄像头** | `camera` | `cv2.VideoCapture(device_index, cv2.CAP_DSHOW)` | DirectShow (Win) / V4L2 (Linux) | `source_camera_start_mixin.start_camera` |
| **RTSP 网络流** | `rtsp` | `cv2.VideoCapture(url)` (FFmpeg backend) | FFmpeg | `source_camera_start_mixin.start_rtsp` |
| **海康 NVR** | `hcnetsdk` | 海康 HCNetSDK DLL（`backend/hcnetsdk/wrapper.py`） | 海康 SDK + Win64 | `source_camera_start_mixin.start_hcnetsdk` ⚠️ 与 industrial 同名 |
| **海康工业相机** | `hikvision` | MvCamera SDK（`backend/api/MvImport/`） | 海康 MV-CL SDK | `source_industrial_camera_mixin.start_hikvision_camera` |
| **视频文件** | `video` | `cv2.VideoCapture(file_path)` | FFmpeg | `source_camera_start_mixin.start_video` |
| **单图** | `image` | `cv2.imread(path)` | OpenCV | `source_camera_start_mixin.start_image` |

**OpenCV 关键约束**（v3.1.3 起永久不变量）：
```python
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
# 必须在 backend/main.py 第 4 行（cv2 import 之前）
# 否则 libavcodec pthread_frame.c:175 会触发断言 abort 整个 worker
```

**海康 SDK 路径**：
- Linux 开发：`MVCAM_COMMON_RUNENV=/opt/MVS/lib/64`
- Windows 部署：随安装包

### 5.2 串口设备

| 类型 | 协议 | 默认 | 启动方式 |
|---|---|---|---|
| **报警灯柱蜂鸣器** | Modbus RTU（pymodbus 3.13+） | COM3 9600 8N1 | `AlarmManager._send_command(hex_command)` 写 holding register |
| **称重器/外部传感器** | 厂商专属（多种） | USB Serial / TCP | `ExternalDeviceService._connections[id].listen_loop` |

**Modbus 命令格式**（来自 alarm_config.triggers）：
```
"01 06 00 01 00 01 19 CA"  # 16 进制字符串，按空格切，写到 RTU
# 协议: addr=01 / func=06(write_single_register) / regaddr=0001 / value=0001 / CRC=19CA
```

**串口探测**：
```python
# alarm.py: list_ports
import serial.tools.list_ports
[p.device for p in serial.tools.list_ports.comports()]
# Windows: COM3 / COM4
# Linux:   /dev/ttyUSB0 / /dev/ttyACM0
```

### 5.3 网络外设（TCP 客户端）

| 设备 | 端口 | 协议 | 文件 |
|---|---|---|---|
| **LON 文本扫码器** | 55256 (TCP) | LON / LOFF 文本 | `services/scanner.py:_text_lon_listen_loop` |
| **WMax 扫码器（RPC）** | 55266 (TCP) | 二进制 + 心跳 | `services/wmax/manager.py` |
| **WMax 扫码器（视频）** | 55276 (TCP) | 视频帧 | 同上 |
| **WMax 扫码器（图像）** | 55286 (TCP) | 单帧图像 | 同上 |
| **集群 host** | 8001 (HTTP) | RESTful | slave 调用 `/api/v1/cluster/report` + `/heartbeat` |
| **客户 MES** | 客户配置 | HTTP / Modbus TCP | `services/mes_adapters/*` |

**LON 协议关键事实**：
- `_text_lon_listen_loop` 用 80ms idle-timeout 切帧（兼容老款带 `\r\n` + 新款裸字节）
- 设备返回 `'ERROR'` 字符串表示无码 → 不上报

### 5.4 GPU / CPU

| 资源 | 接口 | 文件 |
|---|---|---|
| **CUDA GPU** | `torch.cuda.is_available()` + `device_count()` | `source_routes.py:/source/gpu/list` |
| **TensorRT engine** | `.engine` 文件（`Ultralytics` YOLO 加载） | `source_model_load_mixin.py` |
| **GPU 分配** | `ChannelManager.get_gpu_allocation()` 按通道分配 device_id | `channel_manager.py` |
| **CPU 推理** | `device="cpu"`（性能差但稳） | 同上 |

**TRT engine imgsz 多级探测**（v3.x 重要修复）：
- Ultralytics 默认按 `model.imgsz` 推理，但 `.engine` 是按 export 时 `imgsz` 编译的
- 多级探测：`engine.imgsz` → `engine.metadata['imgsz']` → 文件名 hint → 默认 640

---

## 六、Electron 跨进程 IO

### 6.1 IPC channel 列表（11 个）

`electron/preload.js` 通过 `contextBridge.exposeInMainWorld('electronAPI', {...})`：

**`invoke`（Renderer → Main，请求-响应）**：

| Channel | Renderer 调用 | Main 实现 | 返回 |
|---|---|---|---|
| `get-app-info` | `electronAPI.getAppInfo()` | `main.js:registerIpcHandlers` | `{name, version, ...}` |
| `get-backend-url` | `electronAPI.getBackendUrl()` | 同上 | `"http://127.0.0.1:8001"` |
| `get-license-status` | `electronAPI.getLicenseStatus()` | `LicenseManager.getStatus()` | `{valid, expiresAt, ...}` |
| `import-license` | `electronAPI.importLicense(filePath)` | `LicenseManager.import(...)` | `{ok, error}` |

**`on/send`（Main → Renderer，单向通知）**：

| Channel | Main 发送 | Renderer 监听 |
|---|---|---|
| `license-activated` | License 验证通过后广播 | `electronAPI.onLicenseActivated(cb)` |
| `shutdown-progress` | 8 步关机进度 | shutdown.html 内监听 |
| `shutdown-error` | 关机出错 | 同上 |
| `shutdown-complete` | 关机完成 | 同上 |

**`send`（Renderer → Main）**：

| Channel | Renderer 发送 | Main 处理 |
|---|---|---|
| `shutdown-cancel` | 用户取消关机 | 中断关机流程 |
| `shutdown-force` | 用户强制关机 | 立即 SIGKILL backend |
| `shutdown-window-ready` | shutdown 窗已就绪 | 开始 8 步流程 |

### 6.2 后端进程 IO

**spawn 命令**（`backend-manager.js`，唯一启动方式）：
```bash
<conda_env>/python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --no-access-log
```

**stdout / stderr** → Electron Main 直接 pipe 监听（用于日志 + 启动就绪检测）。

**signal**：
- `SIGTERM`：Electron 主进程发，触发 `signal_handler` → `cleanup_on_exit` → `os._exit(0)`
- `SIGINT`：Ctrl+C，由 uvicorn 处理（不进 cleanup_on_exit）

---

## 七、文件系统 IO

### 7.1 用户数据目录（`TIANJUN_DATA_DIR`，默认 `%APPDATA%/tianjun-ai-vision/data`）

```
data/
├── sql_app.db                            ← 主 SQLite DB (WAL mode + busy_timeout=15s)
├── sql_app.db-wal / .db-shm              ← SQLite WAL 配套文件
├── workstation_config.json               ← 多通道视频源/GPU/项目绑定（启动时恢复用）
├── current_operator.json                 ← 当前工位操作员（无 token，按 channel_id 分键）
├── counters_snapshot.json                ← 计数器快照（每个 cycle_end 写一次）
├── alarm_config.json                     ← 报警配置（per-channel 落盘版）
├── rod_filter_config.json                ← 杆件过滤配置
├── periodic_actions_state.json           ← v3.5.0 周期性动作状态
├── scanner_disable.json                  ← v3.4.2 工位禁用扫码状态
├── plugins/                              ← ⚠️ 待引入：插件目录（命名约定）
│   └── <customer_code>/
│       ├── plugin.json                   ← manifest
│       ├── routes.py                     ← FastAPI router
│       ├── components/                   ← Vue 组件
│       └── ...
├── uploads/
│   ├── models/                           ← 上传的 .pt / .onnx / .engine
│   ├── images/                           ← 单图源
│   ├── videos/                           ← 上传视频文件源
│   ├── audio/                            ← 事件语音播报
│   └── ...
├── recordings/
│   ├── sessions/<session_uuid>/          ← session 全程录像
│   └── cycles/<session_uuid>/<cycle_id>/ ← 周期录像（VideoClip 表）
├── exports/
│   ├── realtime/                         ← 实时规则触发的导出
│   └── manual/                           ← 手动导出
└── logs/                                 ← 日志（不一定持久化）

mes_hooks_spill/                          ← critical 事件落盘兜底（重启时回放）
```

### 7.2 安装目录（`BASE_DIR`，默认 `%ProgramFiles%/tianjun-ai-vision`）

```
tianjun-ai-vision/
├── backend/                              ← Python 源码（部分被 Nuitka 编成 .pyd）
├── frontend/dist/                        ← Vite build 产物
├── electron/                             ← Electron 主进程 + preload
├── conda_env/                            ← conda-pack 产物（Python 解释器 + 依赖）
├── electron.exe + 资源
└── uninstall.exe
```

### 7.3 关键 JSON 配置文件 schema 速查

**`workstation_config.json`**（`channel_manager.save_channel_source` 写）：
```json
{
  "channel_count": 2,
  "channels": {
    "0": {
      "source_type": "camera",
      "device_index": 0,
      "resolution": "1920x1080",
      "fps": 60,
      "auto_exposure": true,
      "exposure_value": -6.0,
      "gpu_device": "cuda:0",
      "project_id": 3,
      "was_detecting": true
    },
    "1": {
      "source_type": "rtsp",
      "url": "rtsp://192.168.0.100:554/...",
      "rtsp_fps": 25,
      "gpu_device": "cuda:1",
      "project_id": 5
    }
  }
}
```

**`current_operator.json`**：
```json
{"0": 5, "1": 7}     // channel_id → operator_id
```

**`counters_snapshot.json`**（每 cycle_end 写）：
```json
{
  "session_uuid_xxx": {
    "总数": 100, "良品": 95, "不良": 5, "缺角": 3, "破损": 2
  }
}
```

---

## 八、环境变量

| 变量 | 默认 | 用途 | 类型 |
|---|---|---|---|
| `TIANJUN_DATA_DIR` | `BASE_DIR` | 用户数据目录 | path |
| `BACKEND_SKIP_INIT` | unset | `1` 跳过启动初始化（测试用） | bool |
| `ENABLE_API_DOCS` | `1` | `0` 关闭 `/docs` `/redoc` `/openapi.json` | bool |
| `CORS_ALLOW_ORIGINS` | unset | CORS 白名单（逗号分隔，`*`=全部） | csv |
| `OPENCV_FFMPEG_CAPTURE_OPTIONS` | `threads;1` | **永久不变量**，cv2 import 前 setdefault | string |
| `MVCAM_COMMON_RUNENV` | unset | 海康 MV-CL SDK 共享库路径 | path |
| `VITE_API_BASE_URL` | `http://localhost:8001/api/v1` | 前端 axios baseURL（dev 用） | url |
| `CONDA_PREFIX` | `~/anaconda3/envs/tianjun` | 开发模式 Python 路径 | path |

**插件系统建议新增**：
| 变量 | 用途 |
|---|---|
| `TIANJUN_PLUGIN_DIR` | 自定义插件目录（默认 `TIANJUN_DATA_DIR/plugins`） |
| `TIANJUN_PLUGIN_VERIFY_SIGNATURE` | `0` 跳过签名验证（仅开发用） |
| `TIANJUN_PLUGIN_DEBUG` | `1` 输出插件加载详细日志 |

---

## 九、网络端口表（最重要）

| 端口 | 协议 | 方向 | 用途 | 部署可改吗 |
|---|---|---|---|---|
| **8001** | HTTP | 后端监听 | uvicorn FastAPI（`backend.main:app`） | ✅ 改 main.py + Electron + Vite + Inno Setup |
| **8002** | HTTP | 后端监听 | **本 worktree 占用**（避开 8001） | 同上 |
| **5173** | HTTP | 前端 dev | Vite dev server（开发模式） | ✅ |
| **5174** | HTTP | 前端 dev | 本 worktree dev | ✅ |
| 6001 | HTTP | 前端 build preview | 偶尔用 | - |
| **55256** | TCP | 后端→外 | LON 扫码器协议端口 | ❌ 设备固定 |
| **55266** | TCP | 后端→外 | WMax RPC 端口 | ❌ |
| **55276** | TCP | 后端→外 | WMax 视频流端口 | ❌ |
| **55286** | TCP | 后端→外 | WMax 图像端口 | ❌ |
| 客户 MES | HTTP/Modbus | 后端→外 | 客户配置 | ✅ 客户决定 |
| 集群 host | HTTP（默认 8001） | slave→host | `/api/v1/cluster/report` `/heartbeat` | 受 host 端口限制 |

**Inno Setup 安装包**（`electron/build/installer.iss`）：
- AppId: `com.tianjun.ai-vision`
- 默认目录: `{pf}\tianjun-ai-vision`
- 用户数据: `%APPDATA%\tianjun-ai-vision`
- 输出文件: `TianJun-AI-Vision-{version}-Setup.exe`
- 大小：约 1.5 GB

**Windows 防火墙**：项目**没有**自动添加防火墙规则（出厂工控机一般不开公网）。

**端口冲突应对**（v3.5.x 实践）：
- backend 启动失败时 Electron 主进程会 retry 5 次再放弃
- 用户机器装了别的服务占了 8001 → 走 hotfix 改端口（详见 `create-hotfix` skill）

---

## 十、第三方 SDK 依赖

| SDK | 版本 | 用途 | 部署形式 |
|---|---|---|---|
| **OpenCV** | 4.11+ | 视频采集 / 图像处理 | pip install (conda) |
| **PyTorch** | CUDA 版（12.x） | 推理 | conda install |
| **Ultralytics YOLO** | latest | YOLO v8/v11 推理 | pip |
| **TensorRT** | 8/10 | engine 推理 | NVIDIA DLL |
| **FFmpeg** | GPL Win64 | RTSP 解码 + 录像 | 随安装包 (FFmpeg/bin/ffmpeg.exe) |
| **海康 HCNetSDK** | 6.x | NVR 接入 | 随安装包 (`backend/hcnetsdk/lib/HCNetSDK.dll`) |
| **海康 MvCamera** | 4.x | 工业相机接入 | 客户机自装（`MVCAM_COMMON_RUNENV` 指路径） |
| **pymodbus** | 3.13+ | 报警串口写 holding register | pip |
| **MediaPipe** | 0.10+ | 人体/手势叠加（detection_config 启用时） | pip |
| **FastAPI / uvicorn / SQLAlchemy** | latest | Web | pip |
| **Element Plus** | 2.4+ | 前端 UI 库 | npm |
| **Vue / Vite / Pinia / vue-i18n** | latest | 前端框架 | npm |
| **Electron** | 32+ | 桌面壳 | npm |
| **Jinja2** | 3.x | **仅自定义导出**（SandboxedEnvironment） | pip |

> **MES Gateway 的模板渲染不是 Jinja2**——是自研 `{key.path}` 替换，详见 `services/mes_adapters/base.py:render_template`。

---

## 十一、给插件系统看的"边界注意事项"

> 这一节是**写代码时必须记住的边界规则**。

### 11.1 插件能动 vs 不能动

| 边界 | 插件能动？ | 备注 |
|---|---|---|
| HTTP API 新建（`/api/v1/plugins/{customer_code}/*`） | ✅ 推荐 | 走动态 router 注册（C9） |
| HTTP API 现有端点（260 个） | ❌ 不能 | 插件不该改主程序端点行为 |
| `/video_feed` MJPEG 流 | ❌ 不能改 | 但可读（前端 `<img>` 复用） |
| StaticFiles `/uploads`、`/recordings` | ✅ 读 | 写要走 `/api/v1/system/*` 或文件系统 |
| Electron IPC channel | ⚠️ 谨慎 | 加新 channel 必须改 `preload.js` + `main.js`，**主进程层面要分包** |
| 串口资源（报警 / 称重） | ⚠️ 共享 | 用 alarm_router / extdev_svc 接口，**不要直接 open serial** |
| 网络端口 8001 | ❌ 不能改 | 主程序占用 |
| TCP 扫码端口 55256/55266+ | ❌ 不能 | 硬件协议固定 |
| GPU 资源 | ⚠️ 共享 | 走 `channel_manager.get_gpu_allocation()` |
| 用户数据目录 | ✅ 子目录 | 强制 `plugins/{customer_code}/` 命名空间 |
| DB（SystemConfig KV / 现有表） | ✅ 读写 | 强制 `plugin.{customer_code}.*` 命名 |
| 加新表 | ✅ 加 | 强制 `p_{customer_code}_<table>` 命名 |
| 改现有表 schema | ❌ 严禁 | 主程序 ORM 字段不能动 |

### 11.2 资源占用警示

**MJPEG 流是稀缺资源**：
- 每个 channel 一条 `<img src="/video_feed">` 长连接
- 多通道时 4 条 + 每条占一个 Chromium 解码器
- 每 5 分钟做一次 double-buffer 交换释放内存（**插件不要做更频繁的 swap**）

**串口资源是稀缺资源**：
- 一个 COM 口同一时刻只能一个进程占用
- 多通道共享灯柱时 `alarm_router` 自动复用同一 manager
- **插件不要 `pyserial.Serial(...)` 直接打开**

**HTTP polling 是常态**：
- 前端有 4 处 polling：detection/results 6.7Hz、cluster/slaves 慢轮询、MES status 慢轮询、export run-logs 慢轮询
- 后端总 polling QPS ≈ 7+1+1+1 = 10/s（多通道时倍增）
- **插件加新 polling 要节流（>= 1s 一次）**

### 11.3 错误隔离边界（再强调）

| 边界 | 原则 |
|---|---|
| 插件 HTTP 端点抛异常 | FastAPI 自动 500，不影响主程序 |
| 插件 hook 抛异常 | 必须 try/except 吃掉 |
| 插件占用串口失败 | 必须降级到"功能不可用"，不能崩 |
| 插件占用 GPU 失败 | 必须 fallback 到 CPU 或拒绝启动该插件 |
| 插件 import 时崩溃 | 不影响其他插件 + 主程序（`importlib` 独立 namespace） |
| 插件签名验证失败 | 拒绝加载，记日志 |

### 11.4 UI 资源边界

| 资源 | 上限建议 |
|---|---|
| Vue 路由插件加 1 项 | OK |
| Vue 路由插件加 >5 项 | 可能影响性能，需评估 |
| Layout 菜单加 1 项 | OK |
| CSS 变量覆盖 | OK |
| 完全替换 Layout / Navbar | ❌ 不允许（影响主功能） |
| 替换 Monitor / Project 视图 | ❌ 不允许 |

---

## 十二、已知 API 不一致 / 死代码索引

汇总到本文档的几处"路径文档不一致"（详见 `05_tech_debt.md`）：

| 位置 | 现象 | 影响 |
|---|---|---|
| `/api/v1/workstations/*` 实际前缀只是 `/api/v1/`（顶层） | 命名歧义 | 插件 prefix 选择需谨慎 |
| `/api/v1/mes/gateway/*` 实际可能不是这个前缀 | 与文档不符 | 需用 `gh` curl 验证或读 `api/gateway.js` |
| `/api/v1/cameras/*` 8 个端点全死代码 | `api/camera.js` 全前端无 import | 可清理 |
| `/api/v1/tasks/*` 7 个端点 `api/task.js` 全前端无 import | 半死代码 | 可清理 |
| `/api/v1/reports/*` 5 个端点 → `views/Report` 路由未注册 | 子图死代码 | 可清理 |
| `frontend/src/api/export.js` 注释 `fmt: 'txt'\|'csv'` | 实际后端允许 5 种格式 | 注释过时 |
| `backend/services/export_context.py` 模块注释 "302 字段" | 实际 `ALL_FIELDS = 308` | 注释过时 |

---

## 十三、TODO / 后续配套

- [ ] `05_tech_debt.md` — 把本文标 ⚠️ ❌ 的所有点 + 03 文档"现状无 registry"的 8 处 + AGENTS.md 第九节的 4 处 bug 全索引

---

**本文最后更新**：2026-05-08
**事实校验**：基于 v3.6.0 源码 grep 全量扫描（264 个 router 端点 + 5 个 main.py 直挂 + 2 StaticFiles + 11 Electron IPC channel）
