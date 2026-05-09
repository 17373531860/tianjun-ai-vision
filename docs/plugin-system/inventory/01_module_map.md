# 01 — 模块依赖关系图（全工程级）

> 适用版本：v3.6.0（HEAD `5c97791`）
> 本文目的：把"谁依赖谁"的拓扑铺平，让插件系统设计能精确知道**改动会触发哪些上下游**。
>
> 阅读顺序：第二节进程架构 → 第三/四节后端/前端拓扑 → 第七节 Hub → 第八节插件挂载点候选。
> 配套：`02_data_flow.md`（一条业务流时序）/ `03_extension_points.md`（现有可复用扩展点）。

---

## 一、文件统计

| 区域 | 数量 | 说明 |
|---|---|---|
| 后端 Python | 119 .py | 含海康 SDK 6 个 `MvImport/`、海康 NVR `hcnetsdk/` 2 个、scripts 2 个、schemas 5 个 |
| 后端纯业务 | **104 .py** | 25 api + 22 services + 3 models + 5 schemas + main / config / db + 35 source_* + 4 misc |
| 前端 Vue/JS | 52 文件 | 12 view + 4 store + 16 api + 3 layout + 5 locales + main + router + utils + App + 子组件 |
| Electron | 9 文件 | main / backend-manager / license-manager / preload + 2 html + 1 iss + 2 json |

后端 35 个 `source_*` = `source.py` (1 主类) + 15 mixin + 6 has-a 组件 + 7 工具 + 1 路由 + 1 SDK 加载器 + 1 录制器 + 1 几何 + 1 sequence + 1 industrial 等。

---

## 二、整体进程架构（三进程 + N 后台线程）

```
┌──────────────────────────────────────────────────────────────────────────┐
│  进程 1: Electron Main (Node.js, electron/main.js)                        │
│  - BrowserWindow / ipcMain / app                                          │
│  - 子模块: BackendManager, LicenseManager                                 │
│  - 职责: 窗口、Splash、关机协调、License 验证                              │
└──────────┬─────────────────────────────────────┬─────────────────────────┘
           │ spawn(pid_python)                   │ loadURL(http://...:8001)
           │ + IPC channel (preload.js bridge)   │ + http://.../video_feed
           ▼                                     ▼
┌─────────────────────────────────┐   ┌──────────────────────────────────┐
│ 进程 2: Python Uvicorn          │   │ 进程 3: Renderer (Chromium)       │
│ (backend.main:app, port 8001)   │◄─►│ (Vue3 + Pinia + Vite build)      │
│                                 │   │                                   │
│ FastAPI app                     │   │ 入口: frontend/src/main.js        │
│ - 13 个 router (api_router 9   │   │ - createApp(App).use(Pinia, Router│
│   + main.py 直接挂 4)           │   │     ElementPlus, i18n)            │
│ - 1 个 ChannelManager 单例      │   │ - 挂载到 #app                      │
│ - 1 个 VideoSourceManager/通道  │   │                                   │
│ - 1 个 MESHookManager 单例      │   │ 路由: 9 个 (Activation +         │
│ - 1 个 ScannerService 单例      │   │   8 个走 Layout)                  │
│ - 1 个 ClusterCollector 单例    │   │ Stores: 4 个 Pinia                │
│ - 1 个 ExternalDeviceService    │   │ Views: 12 个 .vue                 │
│ - 1 个 AlarmRouter (按通道分配) │   │ APIs: 16 个 axios 封装            │
│                                 │   │                                   │
│ 后台线程（约 12+ 条）：           │   │ 长连接：                            │
│ - cleanup timer (24h)           │   │ - <img src="/video_feed?ch=N">    │
│ - extdev log cleanup (1h)       │   │   MJPEG 双缓冲                    │
│ - mes-hook-worker queue         │   │ - axios 短连接 RESTful            │
│ - cluster heartbeat 收集        │   │                                   │
│ - scanner LON listen loop ×N    │   │ Electron 桥：                      │
│ - scanner WMax UDP loop         │   │ window.electronAPI.* (preload)    │
│ - external_device pipeline ×N   │   │                                   │
│ - source.capture_loop / ch      │   │                                   │
│ - source.inference_loop / ch    │   │                                   │
│ - alarm watchdog (每个 mgr)      │   │                                   │
└─────────────────────────────────┘   └──────────────────────────────────┘
                  │                                     ▲
                  │ HTTP /api/v1/*                      │
                  │ HTTP /video_feed?channel=N          │
                  │ HTTP /snapshot?channel=N            │
                  │ Static /uploads/* /recordings/*     │
                  └─────────────────────────────────────┘
```

**关键事实**：
- **后端是单 Python 进程**，所有"通道隔离"靠 `ChannelManager` 内部 dict + 各通道独立线程实现，**不是多进程**。
- **3 个全局单例**：`ChannelManager` / `MESHookManager` / `ScannerService`（外加 `ClusterCollector` / `ExternalDeviceService` 各一个）。
- **Electron 主进程绝不直连后端业务**，只通过 `BackendManager` spawn 和 `http.get` 健康检查，UI 全部走 renderer 的 fetch。
- **License 验证在 Electron 主进程**（`license-manager.js`）做，后端只读取一份 license 缓存（`/api/v1/system/*` 的 KV），**后端本身不做加密验签**。

---

## 三、后端模块依赖拓扑

### 3.1 启动顺序图（`backend/main.py`）

```
backend/main.py
  ├─ os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
  │  ↑ 必须在 cv2 import 之前 (v3.1.3 关键修复)
  │
  ├─ from backend.core.config import settings, BASE_DIR, DATA_DIR
  ├─ from backend.db.database import engine, Base
  ├─ from backend.models import export_models  (注意：这里 import 是为了让表注册到 metadata)
  │
  ├─ from backend.api import api_router        (聚合 9 个子 router, 见 3.2)
  ├─ from backend.api.source import router as source_router, get_video_manager
  ├─ from backend.api.channel_manager import router as workstation_router
  ├─ from backend.api.sessions import router as sessions_router
  ├─ from backend.api.mes import router as mes_router
  ├─ from backend.api.scanner import router as scanner_router
  ├─ from backend.api.wmax import router as wmax_router
  ├─ from backend.api.mes_gateway import router as mes_gateway_router
  ├─ from backend.api.operators import router as operators_router
  ├─ from backend.api.cluster import router as cluster_router
  ├─ from backend.api.external_device import router as extdev_router
  └─ from backend.api.debug import router as debug_router
       (合计 13 条直接 router import; 其中 4 条是 main.py 直接挂载)

启动序列（受 BACKEND_SKIP_INIT 控制）：
  1. Base.metadata.create_all(bind=engine)         # 建表
  2. migrate_data_to_external_dir()                # 老安装包升级时迁移到 %APPDATA%
  3. _fixup_stale_paths()                          # 修旧绝对路径
  4. _diag_db_health()                             # 计数关键表
  5. migrate_database()                            # 60+ ALTER TABLE
  6. fix_orphan_sessions()                         # status=running → interrupted
  7. cleanup_orphan_inspections()                  # workpiece_inspections 孤儿置 NULL
  8. _seed_export_builtin_templates()              # v3.5.0 内置导出模板 upsert
  9. auto_load_active_project()                    # 按 workstation_config.json 装项目+模型
 10. auto_restore_video_sources()                  # 按 was_detecting 自动开摄像头+检测
 11. _init_mes_services()                          # MESHook + Scanner + Cluster + ExtDev
 12. atexit.register(cleanup_on_exit)              # 退出钩子（含报警熄灯）
 13. signal.signal(SIGTERM, signal_handler)
 14. threading.Thread(_start_auto_cleanup)         # 后台 24h 清理
 15. threading.Thread(_start_extdev_log_cleanup)   # 后台 1h 外设日志清理
```

**Router 注册分两批**：

| 批次 | 数量 | 通过谁挂 | 前缀 |
|---|---|---|---|
| 通过 `api_router` 聚合再挂载 | 9 | `backend/api/__init__.py` | `/api/v1/{projects,models,cameras,tasks,reports,alarm,system,export}` |
| 在 `main.py` 直接 `app.include_router` | 10 | `main.py` | `/api/v1/{source,data,workstations,mes,scanner,wmax,mes_gateway,operators,cluster,external-devices,debug}` |
| 不走 router，直接 `@app.get` | 4 | `main.py` | `/`, `/health`, `/video_feed`, `/snapshot`, `/api/v1/source/shutdown/*` |
| StaticFiles | 2 | `main.py` | `/uploads/*`, `/recordings/*` |

> ⚠️ **不一致**：`api/__init__.py` 通过 `api_router` 聚合的路由原本应是"所有 router 的统一聚合点"，但 `main.py` 又直接挂了 10 条。两条挂载路径并存意味着新增路由有两种写法，**插件系统设计动态路由注册时要一并兼容**。

### 3.2 后端模块依赖矩阵（按文件粒度）

下表是每个 backend 文件的**直接 import 来源**（仅本仓库内，第三方/标准库省略）：

| 文件 | 依赖来源（仅 backend.*） |
|---|---|
| `core/config.py` | — |
| `db/database.py` | `core.config` |
| `models/models.py` | `db.database` |
| `models/mes_models.py` | `db.database` |
| `models/export_models.py` | `db.database` |
| `schemas/*.py` (5 个) | — (全是 pydantic, 不 import 仓库内其他模块) |
| `hcnetsdk/wrapper.py` | `hcnetsdk.types` |
| `api/__init__.py` | `api.{projects, models, cameras, tasks, reports, alarm, system_display, export_custom, export_realtime}` |
| `api/projects.py` | `db.database`, `models.models`, `schemas.project` |
| `api/models.py` | `db.database`, `models.models`, `schemas.model`, `core.config` |
| `api/cameras.py` | `db.database`, `models.models`, `schemas.camera`, `core.config` |
| `api/tasks.py` | `db.database`, `models.models`, `schemas.task`, `core.config` |
| `api/reports.py` | `db.database`, `models.models`, `schemas.report` |
| `api/alarm.py` | `core.config` (DATA_DIR) |
| `api/system_display.py` | `db.database`, `models.models` |
| `api/export_custom.py` | `core.config`, `db.database`, `models.export_models`, `services.export_field_registry`, `services.export_context`, `services.export_renderer` |
| `api/export_realtime.py` | `db.database`, `models.export_models`, `services.export_realtime` |
| `api/sessions.py` | `db.database`, `models.models`, `core.config`, `api.source.{get_ffmpeg_path,get_cached_ffmpeg_path}`, **+ 子路由聚合** `api.sessions_stats` / `api.sessions_maintenance` |
| `api/sessions_stats.py` | `db.database`, `models.models` |
| `api/sessions_maintenance.py` | `core.config`, `db.database`, `models.models` |
| `api/sessions_export.py` | `models.models` |
| `api/mes.py` | `db.database`, `services.work_order`, `services.workpiece`, `services.defect` |
| `api/mes_gateway.py` | `db.database`, `models.mes_models`, `services.mes_gateway` |
| `api/scanner.py` | `db.database`, `models.mes_models`, `services.scanner` |
| `api/wmax.py` | `services.wmax.{manager, virtual_device}` |
| `api/cluster.py` | `db.database`, `models.mes_models`, `services.cluster_collector` |
| `api/external_device.py` | `db.database`, `models.mes_models`, `services.external_device` |
| `api/operators.py` | `db.database`, `core.config`, `models.models` |
| `api/channel_manager.py` | `core.config` (+ 内部新建 `VideoSourceManager` 实例) |
| `api/debug.py` | （无仓库内依赖，纯运行期诊断）|
| `api/source.py` (主类) | **见 3.4** — 15 mixin + 6 has-a 组件 |
| `api/source_routes.py` | `core.config`, `api.source.*` |

**Service 层依赖**：

| 文件 | 依赖 |
|---|---|
| `services/work_order.py` | `models.mes_models` |
| `services/workpiece.py` | `models.mes_models` |
| `services/defect.py` | `models.mes_models` |
| `services/mes_hooks.py` | `core.config`, `db.database`, `services.work_order`, `services.workpiece`, `services.defect` |
| `services/scanner.py` | `db.database`, `models.mes_models`, `services.barcode_parser` |
| `services/barcode_parser.py` | — |
| `services/wmax/{manager,protocol,messages,device,virtual_device,discovery}.py` | 子包内互引 |
| `services/mes_gateway.py` | `db.database`, `models.mes_models`, `services.mes_adapters` |
| `services/mes_adapters/__init__.py` | 子包内 5 个 adapter + base |
| `services/mes_adapters/{rest,form_data,form_urlencoded,query_string,modbus}_adapter.py` | `services.mes_adapters.base` |
| `services/cluster_collector.py` | `db.database`, `models.mes_models` |
| `services/external_device.py` | `db.database`, `models.mes_models`, `services.external_device_models` |
| `services/external_device_pipeline.py` | `services.external_device` (+ 反向被注入) |
| `services/external_device_protocols.py` | — |
| `services/external_device_test.py` | `db.database`, `models.mes_models`, `services.external_device_models` |
| `services/external_device_models.py` | — |
| `services/export_field_registry.py` | — (308 字段定义表) |
| `services/export_context.py` | `core.config` |
| `services/export_renderer.py` | `core.config` (+ docx/pdf/xlsx 由各子模块复用 `render_string`) |
| `services/export_renderer_docx.py` | `core.config`, `services.export_renderer` |
| `services/export_renderer_xlsx.py` | `core.config`, `services.export_renderer` |
| `services/export_renderer_pdf.py` | `services.export_renderer` |
| `services/export_realtime.py` | `models.export_models`, `services.export_context`, `services.export_renderer` |
| `services/export_seed.py` | `db.database`, `models.export_models` |

### 3.3 后端 API 单向依赖图（化简版）

```
                       ┌──────────────────┐
                       │   schemas/*.py    │  (纯 pydantic, 无仓库依赖)
                       └────────┬─────────┘
                                │
        ┌──────────┐    ┌───────▼────────┐    ┌────────────────┐
        │ core     │    │ models/*.py    │    │ services/*.py   │
        │ /config  │◄───┤ (3 文件 31 表) │◄───┤ (22 文件)       │
        │  /db     │    │                │    │                 │
        └──────────┘    └────────────────┘    └────────┬───────┘
              ▲                                          │
              │                                          │
              │           ┌──────────────────────────────┴───┐
              └───────────┤ api/*.py (25 文件)                │
                          │ + 35 个 source_* (mixin/has-a)    │
                          └─────────────┬────────────────────┘
                                        │
                                ┌───────▼────────┐
                                │ main.py        │
                                │ (FastAPI app)  │
                                └────────────────┘
```

**例外情况（双向依赖 / 跨层）**：

| 情况 | 文件 | 说明 |
|---|---|---|
| api → api（兄弟） | `sessions.py` → `source.py` | `sessions.py` 调 `get_ffmpeg_path` 拼接录像下载（**应抽到 utils**） |
| api → api（兄弟） | `sessions.py` 内挂 `sessions_stats` / `sessions_maintenance` 子 router | 拆出来后子 router 由 sessions.py 收集 |
| services → api（反向） | `services/mes_hooks.py` 间接通过 `set_mes_hook` 被注入到 `services/scanner.py` + 各通道的 `VideoSourceManager`（在 `main.py` 启动时人工绑） | 这是**人工 IoC**，没用 DI 框架 |
| **`main.py` 在每个步骤里 lazy-import 业务模块** | 整个 main.py | `_init_mes_services()` 等用 `from backend.services.mes_hooks import get_mes_hook` 局部 import，避免启动时循环引用 |

### 3.4 `source.py` 内部 mixin / has-a 拓扑（项目最复杂的依赖中心）

`VideoSourceManager` 类继承链（MRO 顺序，左优先）：

```python
class VideoSourceManager(
    TrackingMixin,            # source_tracking_mixin (12 个 _tracking_*)
    InferenceLoopMixin,       # source_inference_loop_mixin
    StepStatsMixin,           # source_step_stats_mixin
    CaptureLoopMixin,         # source_capture_loop_mixin
    EventTriggerMixin,        # source_event_trigger_mixin (_trigger_event 中心 hook)
    ModelLoadMixin,           # source_model_load_mixin
    CheckModesMixin,          # source_check_modes_mixin (再继承 4 个: ContainerGrouping/Checklist/EventsCheck/Sequential)
    SettlementMixin,          # source_settlement_mixin (914 行 ⚠️)
    DetectRunnersMixin,       # source_detect_runners_mixin
    CameraStartMixin,         # source_camera_start_mixin   ⚠️ 与 IndustrialCameraMixin 同名方法冲突
    SessionLifecycleMixin,    # source_session_lifecycle_mixin (1111 行 ⚠️)
    RecordingThreadMixin,     # source_recording_thread_mixin
    RecordingApiMixin,        # source_recording_api_mixin
    LifecycleMixin,           # source_lifecycle_mixin
    PeriodicActionsMixin,     # source_periodic_actions_mixin (v3.5.0)
)
```

**Has-a 组件（持久属性，组合方式注入）**：

| 字段 | 类型 | 文件 | 职责 |
|---|---|---|---|
| `self.drawer` | `Drawer` | `source_drawer.py` | Kalman 平滑 + 中文字体 + 赛博朋克检测框 |
| `self.mp_overlay` | `MediaPipeOverlay` | `source_mediapipe.py` | 人体/手势/骨骼叠加 |
| `self.counters` | `Counters` | `source_counters.py` | 计数器 dict + 持久化 |
| `self.video_transform` | `VideoTransform` | `source_video_transform.py` | 旋转/镜像/坐标映射 |
| `self.inference_executor` | `InferenceExecutor` | `source_inference_executor.py` | 单线程推理池 |
| `self.sequence_labels` | `SequenceLabels` | `source_sequence_labels.py` | 步骤标签查询（无状态） |

**未在继承链但被 source.py 直接 import 的**：

- `source_routes.py` — 路由注册（在 source.py 末尾 `import` 触发副作用）
- `source_sdk_loader.py` — 海康 SDK 启动（`debug_log` / `start_hcnetsdk` 等）
- `source_geometry.py` — 几何工具（IoU / 字体缓存 / `clip_bbox_normalized`）
- `source_recorder.py` — `FFmpegRecorder`
- `source_state_init.py` — 状态字段集中初始化（被 `__init__` 调用）
- `source_project_config_apply.py` — 把 Project 配置应用到 VSM
- `rod_filter.py` — 杆件过滤（`RodSessionGate` + 配置读取）

**未在继承链但同名方法冲突的**：

- `source_industrial_camera_mixin.py` 也定义 `start_hcnetsdk` / `start_hikvision_camera`，**和 `source_camera_start_mixin.py` 重名**。`VideoSourceManager` 的继承链里只列了 `CameraStartMixin`，意味着 `IndustrialCameraMixin` 现在**不在 MRO 里被实际继承**（验证：`source.py` import 它但 `class VideoSourceManager(...)` 不包含它）→ **死代码或半死代码风险**，详见 `05_tech_debt.md`。

**重叠 mixin（已知架构 bug）**：

- `source_recording_mixin.py` (546 行) ↔ `source_recording_thread_mixin.py` + `source_recording_api_mixin.py`
- 主类只继承后两个，前者**疑似死代码**（被旧代码导入但当前 MRO 不包含）。

### 3.5 ORM 模型层（3 文件 31 表）

| 文件 | 表数 | 主要表 |
|---|---|---|
| `models/models.py` | 13 | projects, models (类名 `Model` ⚠️ 不是 `MLModel`), model_conversions, tasks, cameras, daily_stats, system_configs, operators, detection_sessions, detection_cycles, step_records, video_clips, data_export_settings |
| `models/mes_models.py` | 15 | work_orders, batches, workpieces, workpiece_inspections, defect_records, defect_codes, scanner_devices, scan_logs, mes_connections, mes_comm_logs, cluster_config, box_aggregations, external_devices, external_device_logs, box_summaries |
| `models/export_models.py` | 3 | export_templates, export_realtime_rules, export_run_logs |

**外键关系密度**：

```
work_orders ──┬─< batches ──< workpieces ──< workpiece_inspections >── (cycle_id, session_id 弱关联)
              └─< (订单维度统计聚合到 batches/workpieces)
                                                ↓
                                    defect_records (cycle_id, workpiece_id 弱关联)

detection_sessions ──< detection_cycles ──< step_records
                                  ↓
                              video_clips (cycle_id 1:1)

scanner_devices ──< scan_logs   (无强外键到 cycle，靠时间戳/box_serial 关联)
external_devices ──< external_device_logs

cluster_config ──< box_aggregations ──< box_summaries

export_templates ──< export_run_logs
export_realtime_rules ──< export_run_logs
```

**v3.5.0 新增的 `export_models.py` 在 `main.py` 第 13 行单独 import**（`# noqa: F401`），目的是让 `Base.metadata.create_all` 看见这 3 张表。**插件系统如果要新加 ORM 表，必须复用这个套路**。

---

## 四、前端模块依赖拓扑

### 4.1 入口与构建

```
frontend/src/main.js
  ├─ App.vue (根组件)
  ├─ router (vue-router, hash mode, beforeEach 拦激活校验)
  ├─ Pinia
  ├─ ElementPlus + @element-plus/icons-vue (全量注册图标)
  ├─ vue-i18n + 5 个语言包 (zh-CN/zh-TW/en-US/ja-JP/ko-KR)
  ├─ initResponsive() (视口宽度 → html font-size, rem 等比缩放)
  └─ setInterval(performance.memory) (每 30 秒打内存日志)
```

**构建工具**：Vite（`frontend/` 内部独立 npm package；项目根**无** `package.json`）。

### 4.2 路由 → 视图

| 路由 path | name | 视图组件 |
|---|---|---|
| `/activation` | Activation | `views/Activation/index.vue` |
| `/monitor` | Monitor | `views/Monitor/index.vue` (4051 行 ⚠️ 项目最大 .vue) |
| `/project` | Project | `views/Project/index.vue` (2925 行 ⚠️) |
| `/model` | Model | `views/Model/index.vue` |
| `/data` | Data | `views/Data/index.vue` |
| `/source` | Source | `views/Source/index.vue` |
| `/settings` | Settings | `views/Settings/index.vue` (1441 行 ⚠️) |
| `/alarm` | Alarm | `views/Alarm/index.vue` |
| `/mes` | MES | `views/MES/index.vue` (内部 7 个 panel: Order/Workpiece/Defect/Scanner/Gateway/ExternalDevice/Cluster) |

> 🚨 **`views/Report/index.vue` (299 行) 路由未注册**，是死代码，但 `api/report.js` 仍部分被调用（被 `Report/index.vue` 自己用），形成"被孤立子图"。详见 `05_tech_debt.md`。

### 4.3 View ↔ Store ↔ API 调用矩阵

**4 个 Pinia Store**（极简，几乎全是状态 + axios）：

| Store | 依赖 | 主要状态 |
|---|---|---|
| `useSystemStore.js` (277) | `api/project.updateProject` | 全局 KV（display.monitor.*, brand_name, license-cache 等） |
| `useProjectStore.js` | — | 当前项目 + steps/events/counters/alarm 缓存 |
| `useSourceStore.js` | — | 视频源 sessionStorage 持久化 |
| `useScannerDisableStore.js` | — | 扫码器禁用状态（v3.4.2 加） |

**16 个 API 文件**（全部 axios 封装，命名约定 `api/<resource>.js`）：

```
api/index.js          ← 全局 axios 实例 + getBackendHost()  (11 个 view 都引用)
api/project.js        ← /api/v1/projects/*
api/model.js          ← /api/v1/models/*
api/detection.js      ← /api/v1/source/*  (注：命名与路径对应不齐，详见 api-sync skill)
api/data.js           ← /api/v1/data/*
api/mes.js            ← /api/v1/mes/*
api/scanner.js        ← /api/v1/scanner/*
api/wmax.js           ← /api/v1/scanner/wmax/*
api/gateway.js        ← /api/v1/mes/gateway/*
api/cluster.js        ← /api/v1/cluster/*
api/external_device.js← /api/v1/external-devices/*
api/operators.js      ← /api/v1/operators/*
api/export.js         ← /api/v1/export/*
api/report.js         ← /api/v1/reports/*  (部分死代码)
api/camera.js         ← /api/v1/cameras/*  (死代码：全前端无 import)
api/task.js           ← /api/v1/tasks/*    (死代码：全前端无 import)
```

**View → Store / API 依赖矩阵（重要的 7 个视图）**：

| View | imports stores | imports apis |
|---|---|---|
| `Monitor/index.vue` | useProjectStore, useSystemStore, useSourceStore, useScannerDisableStore | detection, model, index, gateway, operators |
| `Project/index.vue` | useProjectStore, useSystemStore | project, model, index |
| `Source/index.vue` | useSourceStore, useSystemStore | index, detection, model, project |
| `Data/index.vue` | useSystemStore, useProjectStore | detection, project, +CustomExportDialog/RealtimeRulesDialog 子组件 |
| `Settings/index.vue` | useSystemStore, useProjectStore | project, operators, index |
| `Alarm/index.vue` | useProjectStore, useSystemStore | index, project, detection |
| `MES/ScannerPanel.vue` | useSystemStore | index, detection, +WMaxPanel 子组件 |
| `MES/ExternalDevicePanel.vue` | useSystemStore | detection |
| `MES/OrderPanel.vue` | — | mes, project, cluster |
| `MES/DefectPanel.vue` | — | mes |
| `MES/WorkpiecePanel.vue` | — | mes |
| `Activation/index.vue` | — | (走 window.electronAPI, 不调后端 axios) |

**视图内嵌子组件**：

```
views/MES/index.vue
  ├─ OrderPanel.vue
  ├─ WorkpiecePanel.vue
  ├─ DefectPanel.vue
  ├─ ScannerPanel.vue
  │   └─ WMaxPanel.vue (内嵌)
  ├─ GatewayPanel.vue
  ├─ ExternalDevicePanel.vue
  └─ ClusterPanel.vue

views/Data/index.vue
  ├─ components/CustomExportDialog.vue
  └─ components/RealtimeRulesDialog.vue
```

### 4.4 Layout 与 i18n

```
layout/index.vue (路由 root, 包裹 8 个业务视图)
  ├─ layout/Navbar.vue (550 行 ⚠️ — 顶部导航 + 工位切换 + 操作员)
  └─ layout/BottomBar.vue (底部状态条)

locales/{zh-CN, zh-TW, en-US, ja-JP, ko-KR}.js  (5 种语言包)
utils/format.js  (格式化辅助, 仅日期/时长/字节)
```

### 4.5 Electron 桥（在 renderer 看到的）

```javascript
// electron/preload.js (13 行) 通过 contextBridge 暴露:
window.electronAPI = {
  isElectron: true,
  getAppInfo:     () => ipcRenderer.invoke('get-app-info'),
  getBackendUrl:  () => ipcRenderer.invoke('get-backend-url'),
  getLicenseStatus: () => ipcRenderer.invoke('get-license-status'),
  importLicense:  (path) => ipcRenderer.invoke('import-license', path),
  onLicenseActivated: (cb) => ipcRenderer.on('license-activated', cb),
  // 关机相关 5 个 channel: shutdown-progress / shutdown-error / shutdown-complete
  //                         + shutdown-cancel / shutdown-force / shutdown-window-ready
}
```

**前端业务代码使用情况**：
- `router/index.js` — 路由守卫调 `getLicenseStatus()`
- `views/Activation/index.vue` — 全部走 IPC（不调后端 axios）
- 其他视图基本不依赖 `window.electronAPI`（开发模式 / 浏览器都能跑）

---

## 五、Electron 模块依赖

```
electron/main.js (737 行)
  ├─ require('electron')           → app, BrowserWindow, ipcMain, dialog
  ├─ require('path')
  ├─ require('fs')
  ├─ require('http')                → 后端健康检查
  ├─ require('./backend-manager')  → BackendManager (616 行 ⚠️)
  └─ require('./license-manager')  → LicenseManager (203 行)

electron/backend-manager.js
  └─ child_process.spawn()
     │
     └─ 命令: <conda env>/python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
        │      ⚠️ 唯一启动方式 (开发/打包都走它)
        │      工作目录 = 安装目录 (含 backend/, frontend/dist/, electron/)
        │      环境变量: TIANJUN_DATA_DIR=%APPDATA%/tianjun-ai-vision/data,
        │                MVCAM_COMMON_RUNENV=...海康SDK路径...

electron/license-manager.js
  └─ require('crypto')              → RSA + SHA256 验签 (公钥嵌在源码)
  + 使用 machineId = SHA256(stableFp) → "TJ-" + 12 位大写 hex

electron/preload.js (13 行)
  └─ contextBridge.exposeInMainWorld('electronAPI', {...})

electron/{splash,shutdown}.html      纯静态 HTML, 由 main.js BrowserWindow 加载

electron/build/installer.iss (145 行)
  ├─ AppId: com.tianjun.ai-vision
  ├─ DefaultDir: {pf}\tianjun-ai-vision
  ├─ UserData:   %APPDATA%\tianjun-ai-vision (= TIANJUN_DATA_DIR 默认值)
  └─ Output: TianJun-AI-Vision-{version}-Setup.exe
```

---

## 六、跨进程通信通道（汇总）

| 通道 | 方向 | 协议 | 用途 |
|---|---|---|---|
| HTTP `/api/v1/*` | renderer → uvicorn | RESTful JSON | 全部业务 CRUD（19 组路由前缀） |
| HTTP `/video_feed?channel=N` | renderer → uvicorn | MJPEG (multipart/x-mixed-replace) | 视频流（`<img src>`，前端双缓冲） |
| HTTP `/snapshot?channel=N` | renderer → uvicorn | image/jpeg | 单帧快照 |
| HTTP `/uploads/*` `/recordings/*` | renderer → uvicorn | StaticFiles | 上传图片 + 录像回放 |
| HTTP `/health` `/api/v1/source/shutdown/*` | electron-main → uvicorn | JSON | 健康检查 / 8 步关机 |
| Electron IPC `invoke` | renderer → main | JSON | get-app-info / get-backend-url / get-license-status / import-license |
| Electron IPC `on/send` | main → renderer | JSON | license-activated / shutdown-progress 等 |
| stdout / stderr | uvicorn → electron-main | text 流 | 后端日志（被 main.js 读取） |
| signal SIGTERM | electron-main → uvicorn | OS 信号 | 强制关机（关机失败兜底） |

**没有的通道（注意）**：
- 后端没有 WebSocket（`websocket.py` 是死代码计划文件，不存在）
- 后端没有 SSE（探针都是 polling）
- Electron 没有反向把"用户点了什么"通过 IPC 发到后端的能力（renderer 直接走 HTTP）

---

## 七、模块依赖中心（Hub 排行）

按"被多少其他模块依赖" + "每秒被调用频度"综合排序：

| 排名 | Hub | 类型 | 依赖它的模块数 | 风险 |
|---|---|---|---|---|
| 1 | `services/mes_hooks.py: MESHookManager` | 单例 + 后台线程 | 30+（VSM、scanner、cluster、export 实时规则、external_device、报警等） | **改它必须读 `debug-mes` 的 61 条历史 bug** |
| 2 | `api/source.py: VideoSourceManager` | 类（每通道一实例） | 35 个 source_*，main 启动序列，channel_manager spawn | **改它前必须做 `modify-source` 影响分析** |
| 3 | `api/channel_manager.py: channel_manager` | 模块级单例 | source 的 import / main.py 启动 / shutdown / 报警 / MES hook | 单例污染高，全局状态点 |
| 4 | `db/database.py: SessionLocal / engine` | 工厂 | 几乎所有 service + api | DB 切到 PG 时所有这里要改 |
| 5 | `core/config.py: settings / DATA_DIR / BASE_DIR` | 模块级常量 | 30+ 文件 | 路径相关改动需扩散 |
| 6 | `services/scanner.py: ScannerService` | 单例 + 后台线程 | mes_hook、external_device、source 通过 hook 拿扫码事件 | LON 4 模式 + WMax 复杂 |
| 7 | `api/alarm.py: AlarmRouter` | 单例 + 多 manager | shutdown 步骤 / channel removal / source detection events | 串口资源不能漏释放 |
| 8 | `services/export_field_registry.py: ALL_FIELDS` | 全局表（308 字段） | 自定义导出 + 实时规则 + 模板渲染 | 字段是"系统数据全集" |
| 9 | `services/cluster_collector.py: ClusterCollector` | 单例 + 后台线程 | host 模式聚齐 box_serial / 推送 MES Gateway | 集群一致性 |
| 10 | `services/external_device.py: ExternalDeviceService` | 单例 + 后台线程 | 称重 + 配对 + 联动 MES hook | 状态机复杂 |

**中心化风险**：
- `mes_hooks.py` 1505 行，是项目第三大文件，几乎所有业务事件都要走它。**任何插件挂检测 / 扫码 / cycle_end 事件，都要经过它的 hook 接口**。
- `source.py` 主类 1573 行 + 35 个分文件，"上帝类"特征明显（mixin 化只是物理拆分，逻辑还都集中）。

---

## 八、给插件系统看的"挂载点候选"

> 这一节是本文档**最重要的产出之一**，回答："未来插件想挂在哪儿，现在已经有什么钩子可用？"

### 候选 A：FastAPI `app.include_router`（档位 3：全栈插件）

**已有机制**：`main.py` 直接 `app.include_router(...)` 共 11 次，每次绑一个 prefix。

**插件复用方式**：
1. 插件目录扫到一个 `manifest.json` + `routes.py`（导出 `router`）
2. 调 `app.include_router(plugin_router, prefix=f"/api/v1/plugins/{customer_code}", tags=[customer_code])`
3. 卸载时 `app.routes.remove(...)` —— 但 FastAPI 没原生支持热卸载，需要**重启** OR 用反向代理过滤

**风险**：
- 必须在 `app` 创建后才能挂；老 `app.include_router(api_router)` 已经定型
- FastAPI 的 router 注册是**单向的**，挂了就在那里 —— 卸载需要重建 app

### 候选 B：MES Hook 中心（`_handle_cycle_end`）

**已有机制**：`services/mes_hooks.py: MESHookManager` 已经把 `cycle_end` / `session_end` / `box_complete` 三种事件汇集为统一 hook。

**插件复用方式**：插件注册 callback 到 `mes_hook.register_listener(event_type, fn)`（**接口需要补**），插件代码就不用改 `mes_hooks.py`。

**风险**：当前 `mes_hooks.py` 是"硬编码各 service 调用"，没暴露 listener 注册接口 → **需要先做接口提取**。

### 候选 C：MES Adapter `_REGISTRY`（档位 3：MES 推送协议）

**已有机制**：`services/mes_adapters/__init__.py` 通过 `_REGISTRY = {"rest": RESTAdapter, ...}` 注册 5 种适配器，调用方走 `get_adapter(name)`。

**插件复用方式**：客户插件加一个新协议，导入时 `_REGISTRY["my_proto"] = MyAdapter`。

**风险**：低。这个机制已经是"开放扩展"模式。**唯一要补的是规范化插件注册时机**（不能在 `app.include_router` 之后才 import）。

### 候选 D：`SystemConfig` KV 表（档位 1/2/3 通用）

**已有机制**：`models/models.py: SystemConfig` 单表存 K=V 字符串，前端有 `useSystemStore`（277 行）做缓存。已用于：
- `display.monitor.ptMode` / `display.monitor.ctMode`（PT/CT 三档显示）
- `brand_name` / `inspector_name`（白标）
- `license-cache`（v3.5.0 后端持有 license 缓存）

**插件复用方式**：客户插件配置全部塞进 `SystemConfig`，键名约定 `plugin.{customer_code}.{key}`。

**风险**：当前 `SystemConfig` 没有"配置 schema"，插件用裸 KV 时无类型校验。

### 候选 E：Project 的 7 个 JSON 字段（档位 2/3）

**已有机制**：`Project.{pipeline_config, steps_config, events_config, counters_config, alarm_config, detection_config, data_config}` 全是 JSON，已通过 `set_project_config()` 应用到 VSM。

**插件复用方式**：插件加新功能时**优先往这 7 个 JSON 加键**，而不是新加 Column。

**风险**：JSON 没 schema，前端 Project 页是 2925 行手工拼的（不能动态扩展 UI）。**插件想新增配置项，前端 UI 得插件自己提供**（档位 2）。

### 候选 F：`_trigger_event` Hook（事件中心）

**已有机制**：`source_event_trigger_mixin.py: _trigger_event(event_type, ...)` 是所有事件的中心出口（OK / NG / 自定义事件）。

**插件复用方式**：插件想"在某事件后做点啥"，注册到 `_trigger_event` 后处理列表（**接口需要补**）。

**风险**：当前 `_trigger_event` 是"内部硬编码"，没暴露 listener 注册。需要补。

### 候选 G：前端动态路由（档位 2）

**已有机制**：`router/index.js` 是静态数组。需要改造成 "static + dynamic" 混合，留 `addRoute` 钩子。

**插件复用方式**：客户插件包内带 Vue 组件，前端运行时 `router.addRoute('plugin-XXX', component)` + Layout 菜单同步加项。

**风险**：Vite 编译期看不到插件代码 → 需要走 **运行期 import map / dynamic import URL**。Vue3 + Vite 这套生态下做动态组件，方案有但都需评估（详见后续设计文档）。

### 候选 H：Vite 主题包（档位 1）

**已有机制**：项目用 Tailwind + Element Plus，**目前没有 CSS 变量主题**，需要从零设计 `:root { --primary: ...; }` 那一套。

**插件复用方式**：插件包带 `theme.css`，前端动态加 `<link rel="stylesheet">`。

**风险**：Tailwind 是编译期生成的，主题变化要么用 CSS 变量，要么客户机本地有 PostCSS（**不可能**）。**结论：必须先把现有样式重构成 CSS 变量**，工作量不小。

### 候选 I：i18n 文案覆盖（档位 1）

**已有机制**：`vue-i18n` 5 个语言包（zh-CN/zh-TW/en-US/ja-JP/ko-KR）。

**插件复用方式**：插件包带 `messages.{lang}.json`，前端运行时 `i18n.global.mergeLocaleMessage(lang, customMessages)`。

**风险**：低。i18n API 已经支持 merge。

### 候选 J：Electron 主进程自动加载（极限档位）

**已有机制**：`electron/main.js` 启动时只 require 4 个本地模块。

**插件复用方式**：定义 `electron/plugins/<customer_code>/index.js`，主进程启动时扫目录 require。

**风险**：动 Electron 主进程要客户重启 Electron。需求暂时**不优先**。

---

## 九、循环依赖 / 紧耦合警示

扫描结果（**无致命循环依赖**，但有几处"间接耦合"）：

1. **`api/sessions.py` ⇄ `api/source.py`**：sessions 调 source 的 `get_ffmpeg_path` 拼接录像下载。**应抽到 utils**。
2. **`services/external_device.py` ⇄ `services/external_device_pipeline.py`**：双向 import（pipeline 写在 service 内部，service 转发给 pipeline）。问题不大但说明 ExternalDeviceService 是"瑞士军刀"。
3. **`source.py` 末尾 `import backend.api.source_routes`**（副作用）：注册路由依赖 source 主类已定义。**禁止在 source_routes.py 里 `import source`**（会循环）。
4. **`main.py` 第 13 行 `import export_models` 仅为副作用**：让 SQLAlchemy 元数据收集到这 3 张表。**插件加表必须复用此模式**。
5. **`api/__init__.py` 静态聚合 9 个 router** → 插件想加路由不能往这里塞，必须走 `main.py` 的 `app.include_router`。

---

## 十、TODO / 后续文档配套

- [ ] `02_data_flow.md` — 把扫码 → 检测 → MES → 显示一条线的实际依赖**按事件时序**画一遍（本文是静态依赖）
- [ ] `03_extension_points.md` — 把第八节的 10 个挂载点候选**详细写明每个的接口契约**
- [ ] `04_io_boundaries.md` — 把第六节的跨进程通道展开成"对外 API + 硬件 IO 出入口的完整清单"
- [ ] `05_tech_debt.md` — 把第三节末"重叠 mixin"+ AGENTS.md 第九节 + 本文标 ⚠️ 的所有点统一索引

---

**本文最后更新**：2026-05-08
**事实校验**：基于 `git ls-files` 全量扫描 + import 关系 grep + AGENTS.md 第三节交叉验证
