# AGENTS.md — 天军 AI 视觉检测系统

> **给 AI 助手（Claude Code / Cursor / 其他 LLM agent）的项目说明书**
>
> 这份文档是 **地图 + 守则 + 不变量**：让你立刻明白"项目是什么 / 模块在哪 / 改动应该读哪个 skill"。
> **细节流程**在 `.claude/skills/<name>/SKILL.md`，按需读取。
>
> 阅读顺序：第一节 → 第三节工作守则 → 第四节 skill 指针表 → 任务相关章节。

---

## 一、项目快速档案

| 项 | 值 |
|---|---|
| 项目名 | 天军 AI 视觉检测系统（TianJun AI Vision） |
| 性质 | **商业项目，客户已在用** — 工厂工控机部署 |
| 客户场景 | 装配线视觉检测 / 包装线 / MES 数据回传 / 多工位集群 |
| 部署模式 | Windows 工控机本地安装（Inno Setup 一键包，约 1.5 GB），Electron 桌面壳套 FastAPI 后端 + Vue3 前端 |
| 当前线上版本 | v3.5.1（已发布安装包） |
| 主仓库 | `17373531860/tianjun-ai-vision`（**PRIVATE**） |
| 中转仓库 | `xu-yanzhi32/tianjun-releases` + `tianjun-releases-2`（Gitee 公开 release，给客户下载用） |
| 母语 | **中文**（用户和注释主语言；技术术语保留英文） |

---

## 二、技术栈

### 后端
- **Python 3.10**（Anaconda 环境 `tianjun`）
- **FastAPI**（`uvicorn` runtime）+ **SQLAlchemy ORM**
- **SQLite**（单文件 `sql_app.db`，开 WAL + busy_timeout=15s；**计划迁 PostgreSQL** — 见模块 18）
- **OpenCV** + **PyTorch（CUDA）** + **Ultralytics YOLO**
- **Jinja2 SandboxedEnvironment**（仅用于自定义导出渲染；MES Gateway **不**用 Jinja2）
- 视频编解码：FFmpeg（GPL Win64）
- 第三方 SDK：海康 HCNetSDK（NVR）、海康 MvCamera（工业相机）、ISAPI

### 前端
- **Vue 3** + Composition API + **Pinia** + **Vue Router**
- **Element Plus** UI 库
- **Tailwind CSS** + **ECharts**
- **Vite** 构建（前端独立 npm 包；项目根**无** `package.json`，前端与 Electron 是两个独立包）

### 桌面壳
- **Electron** 32+ + 8 步关机流程 + Splash + License 验证 + 三层崩溃恢复

### 编译/打包
- **Nuitka** 把 8 个核心 `.py` 编成 `.pyd`（CI 用 `--module`，按白名单替换源码）
- **conda-pack** 打 Python 环境
- **Inno Setup**（Windows 安装包）
- **GitHub Actions** CI（`.github/workflows/build.yml`）→ GitHub Release → Gitee Release（客户下载）
- **Runtime 仍 spawn python**：`python -m uvicorn backend.main:app`，加载 `.pyd`（编译过的）+ `.py`（源码）混合

---

## 三、工作守则（必须遵守）

### 唯一硬性规则
- ✅ **一次只做一件事，做完汇报再接下一件。** 不要闷头连干 3-5 个改动让用户审一堆。

### 隐含约定
- 商业项目客户在用，**主线改动要先做影响分析**（用 modify-* 系列 skill）
- **不要主动 commit / push**，等用户明确指令
- **破坏性操作必须先问**：`rm -rf`、`drop table`、`force push`、改 schema、删 commit 历史
- **改动后必须验证**：lint 0 错误 + 至少跑一次冒烟（启动后端 / 单元测试 / 手测）
- **中文回复**，技术术语保留英文（如 `monkey patch`、`hotfix`、`mixin`）
- **Commit 信息中文为主**，格式 `<type>(<scope>): <短描述>` + 可选正文写"为什么"
- 插件挂了不能让主程序起不来 → **错误隔离是底线**
- 老客户已装的版本升级要无损 → **数据迁移谨慎，老 schema 兼容**

---

## 四、必读 skill 触发表（核心导航）

**这是 AGENTS.md 最重要的一节**。看到对应场景，**先读对应 skill 再动手**。

| 场景 | 必读 skill |
|---|---|
| 改 `backend/api/source.py` 或任意 `source_*_mixin.py` / has-a 组件 | `modify-source` |
| 改 ORM 模型 / 加表 / 改字段 | `modify-model` |
| 改后端 API 端点（路由、Schema、参数）| `modify-api` |
| 改前端 Vue 组件 / Pinia store | `modify-frontend` |
| 改项目配置结构（pipeline_config / steps_config / events_config）| `modify-project-config` |
| 新增后端 API 端点 | `add-api-endpoint` |
| 新增视频源类型 | `add-source-type` |
| 新增检测模式 | `add-detection-mode` |
| 新增事件类型（OK/NG/警告之外）| `add-event-type` |
| 排查检测异常（不出目标 / FPS 低 / 置信度问题）| `debug-detection` |
| 排查 source 状态机问题 | `debug-source` |
| 排查多通道异常（GPU、ChannelManager、串扰）| `debug-channel` |
| 排查视频采集 / 推流问题 | `debug-video` |
| 排查报警不响应 | `debug-alarm` |
| 排查 MES 异常（工单 / 工件 / 缺陷 / 扫码器 / Hook / Gateway）| `debug-mes` |
| 排查集群主从（box 不齐 / 副机心跳 / box_complete 不推 MES）| `debug-cluster` |
| 排查 Session/Cycle/Step 数据问题 | `debug-session` |
| 排查 Electron 桌面壳问题 | `debug-electron` |
| 排查前端问题 | `debug-frontend` |
| 排查自定义导出 / 实时规则 / 模板 / 字段中央仓库（v3.5.0+）| `debug-export` |
| 排查操作员 / License 授权（machineId / RSA 验签 / 当前操作员落盘）| `debug-operator-license` |
| 数据问题修复 | `fix-data` |
| 前后端 API 对齐检查 | `api-sync` |
| 调参（视频 + 模型）| `tune-params` |
| 出新版本（version bump / changelog / tag / CI）| `update-release` |
| 打包流程问题排查 | `build-release` |
| 给客户出热补丁 | `create-hotfix` |

---

## 五、系统架构

```
┌────────────────────────────────────────────────────────────────┐
│  Electron 桌面壳 (electron/main.js, 737 行)                     │
│  - spawn python uvicorn 后端 → 健康检查 → 加载前端 → Splash 撤场 │
│  - License 验证（RSA SHA256 + machineId）                        │
│  - 8 步关机 / 三层崩溃恢复                                        │
└──────────────┬───────────────────────────────────┬─────────────┘
               │                                   │
       ┌───────▼────────┐                  ┌──────▼──────────┐
       │ Vue3 前端       │  HTTP /api/v1   │ FastAPI 后端     │
       │ (Vite dev:5173) │ ◄─────────────► │ (端口 8001)      │
       │                │   MJPEG 流       │                 │
       │ 10 个视图模块    │                  │ 25 个 API 文件   │
       │ + 4 个 Store    │                  │ + 22 个 service  │
       │ (Pinia)        │                  │ + 35 source mixin │
       └────────────────┘                  └──────┬──────────┘
                                                 │
                          ┌──────────────────────┼─────────────────────┐
                          │                      │                     │
                   ┌──────▼──────┐        ┌──────▼─────┐        ┌──────▼──────┐
                   │  SQLite     │        │ 视频源/推理  │        │ MES / 扫码 │
                   │ sql_app.db  │        │ Source/Det  │        │ Gateway   │
                   │ 31 张表     │        │ 多通道+集群  │        │ 5 种适配器 │
                   │ (WAL)       │        │              │        │           │
                   └─────────────┘        └─────────────┘        └─────────────┘
                                                 │
                                          ┌──────▼─────────────────────────┐
                                          │ 硬件 IO                          │
                                          │ - 摄像头 (USB/RTSP/海康/视频文件)│
                                          │ - PyTorch GPU 推理              │
                                          │ - 报警串口 (Modbus 灯塔/蜂鸣)    │
                                          │ - 扫码器 (LON/WMax/虚拟)        │
                                          │ - 称重设备等外部设备             │
                                          └────────────────────────────────┘
```

### 后端真实路由前缀（**全部** `/api/v1/` 下，19 组）

| 前缀 | 文件 | 一句话 |
|---|---|---|
| `/source/*` | `source_routes.py` (1279 ⚠️) | **视频源 + 检测核心** |
| `/data/*` | `sessions*.py` (4 个文件 / ~2086 行) | session/cycle/step 记录 + 导出 |
| `/projects/*` | `projects.py` (335) | 项目 CRUD + 激活 |
| `/models/*` | `models.py` (793) | 模型上传/转换/标签 |
| `/tasks/*` | `tasks.py` (193) | 离线推理任务 |
| `/reports/*` | `reports.py` (~340) | 趋势/日报/导出 |
| `/cameras/*` | `cameras.py` (152) | **旧式相机表**（与 `/source/*` 并存，**注意不要混淆为主路径**）|
| `/system/*` | `system_display.py` (136) | KV 配置 + license 缓存（v3.5.0） |
| `/alarm/*` | `alarm.py` (1009 ⚠️) | 灯塔/蜂鸣器/共享灯柱 |
| `/workstations/*` | `channel_manager.py` (374) | 多工位 + GPU 分配 |
| `/scanner/*` | `scanner.py` (533) | 扫码器 CRUD + scan_pair + 禁用 |
| `/scanner/wmax/*` | `wmax.py` (673 ⚠️) | WMax 协议（35+ endpoint） |
| `/external-devices/*` | `external_device.py` (366) | 称重器/串口外设 |
| `/cluster/*` | `cluster.py` (296) | 集群主从 + 副机心跳 |
| `/mes/*` | `mes.py` (673 ⚠️) | 工单/工件/缺陷/缺陷码 |
| `/mes/gateway/*` | `mes_gateway.py` (450) | MES 推送连接 + 测试 |
| `/operators/*` | `operators.py` (214) | 操作员（无 token，文件落盘） |
| `/export/*` | `export_custom.py` + `export_realtime.py` | v3.5.0 自定义导出（共用前缀） |
| `/debug/*` | `debug.py` (118) | 通道诊断 |

> **常见误解**：路径前缀是**`/api/v1/`** 不是 `/api/`；旧手册写的 `/api/detection/*` 已删，等价端点在 `/api/v1/source/detection/*`。

### 31 张数据库表（仅速查；详细字段见 modify-model skill）

**`backend/models/models.py` 13 张**（核心检测）：
`projects` / `models` / `model_conversions` / `tasks` / `cameras` / `daily_stats` / `system_configs` / `operators` / `detection_sessions` / `detection_cycles` / `step_records` / `video_clips` / `data_export_settings`

**`backend/models/mes_models.py` 15 张**（MES）：
`work_orders` / `batches` / `workpieces` / `workpiece_inspections` / `defect_records` / `defect_codes` / `scanner_devices` / `scan_logs` / `mes_connections` / `mes_comm_logs` / `cluster_config` / `box_aggregations` / `external_devices` / `external_device_logs` / `box_summaries`

**`backend/models/export_models.py` 3 张**（v3.5.0+）：
`export_templates` / `export_realtime_rules` / `export_run_logs`

> ⚠️ 产品交接手册 v2.4.0 说"22 张表"是**过时的**，以代码为准。
> ⚠️ ORM 类名是 `Model`（**不**是 `MLModel`）；表名是 `models`（**不**是 `ml_models`）。

### Hub 关系图（最大集成中心）

```
                   ┌──────────────────────────────────┐
                   │  VideoSourceManager (per channel) │   ← 15 mixin + 6 has-a
                   └─────────────┬────────────────────┘
                                 │ on_cycle_*/on_session_*
                                 ▼
                  ┌─────────────────────────────────┐
                  │  MESHookManager (singleton)     │  ← 项目最大 Hub
                  │  + 后台 mes-hook-worker 线程     │  ← 队列 + 独立 DB session
                  │  + scan_pair 状态机 (v3.3.0)    │
                  │  + _disabled_channels 落盘 JSON │  ← v3.4.2
                  └─┬───────┬───────┬───────┬───────┘
                    │       │       │       │
          ┌─────────┘       │       │       └──────────────────┐
          ▼                 ▼       ▼                          ▼
┌──────────────────┐  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐
│ WorkOrderService │  │Workpiece │ │ Defect   │ │ ClusterCollector     │
│ (binding_scope)  │  │Service   │ │ Service  │ │ (心跳/超时/box聚齐)   │
└──────────────────┘  └──────────┘ └──────────┘ └──────────────────────┘
                                                      │
                                                      ▼
                  ┌─────────────────────────────────┐
                  │  MESGateway (推送外部 MES)       │
                  │  └─ build_payload (非 Jinja2!)   │  ← 自研 {key.path}
                  │  └─ _apply_auth_to_headers       │
                  │  └─ retry (1+retry_count)        │
                  └────────────────┬────────────────┘
                                   ▼
                5 个适配器（注册名）：
                rest / form-data / form-urlencoded / query-string / modbus_rtu

       ┌──────────────────┐         _on_data_received
       │ ScannerService   │ ───────────────┐
       │ + _listen_loop/  │                ▼
       │   _text_lon_*    │         MESHook.on_scan_received
       │ + WMax 后台线程   │                │
       └────────┬─────────┘                │
                │ _inject_barcode          │
                ▼                          │
       ┌──────────────────────┐            │
       │ ExternalDeviceService│ ←──────────┘
       │ + Pipeline Mixin     │
       │ + 称重 idle/stable   │
       │ + 有重无码 → alarm   │
       └──────────────────────┘
                ↓
       cycle_end → mes_hooks._handle_cycle_end → dispatch_cycle_end_export
                                                      │
                                                      ▼
                                          export_context.build_cycle_context
                                          export_renderer.render_to_file
                                          ExportRunLog 写库
```

---

## 六、模块详解（20 个）

### 6.1 业务核心

#### 模块 1：视频源采集 (Source)

**做什么**：6 种视频源（USB / RTSP / 视频文件 / 海康工业相机 MvCamera / 海康 NVR HCNetSDK / 单图）的统一采集，作为 ChannelManager 的输入。

**关键文件**：`backend/api/source.py`（1573 行主类）+ **15 个继承式 mixin** + **6 个 has-a 组件** + 工具模块。详见 `modify-source` skill。

**对外接口**：`/api/v1/source/*`。

**改动它必读**：`modify-source` + `debug-source`

**已知架构事实**（详细踩坑见 `debug-video` / `debug-source`）：
- `OPENCV_FFMPEG_CAPTURE_OPTIONS=threads;1` **必须在 cv2 import 前 setdefault**（v3.1.3 起）
- `source_camera_start_mixin.py` 与 `source_industrial_camera_mixin.py` **同名方法（`start_hcnetsdk` / `start_hikvision_camera`）共存**，依赖 MRO 决出实际生效方
- `source_recording_mixin.py` (546) 与 `source_recording_thread_mixin.py + source_recording_api_mixin.py` **能力重叠**，历史拆分残留

---

#### 模块 2：检测推理 (Detection / Model)

**做什么**：YOLO 系列模型加载 + 推理，3 种 runner（纯检测、检测+跟踪、分割），支持 PyTorch FP32/FP16 / TensorRT 引擎切换。

**关键文件**：
- `backend/api/source_model_load_mixin.py` — 模型加载（含 TRT engine `imgsz` 多级探测）
- `backend/api/source_inference_executor.py` — 单线程推理池
- `backend/api/source_inference_loop_mixin.py` — 推理循环 + FPS 统计
- `backend/api/source_detect_runners_mixin.py` — 3 种 runner 路径（含 `clip_bbox_normalized`）
- `backend/api/source_drawer.py` — Drawer 组件（Kalman 平滑 + 中文字体 + 赛博朋克框）
- `backend/api/source_geometry.py` — IoU / 归一化 / 字体缓存
- `backend/api/models.py` — 模型管理 API
- `backend/services/defect.py` — NG 后缺陷分类

**改动它必读**：`modify-source` + `debug-detection`

---

#### 模块 3：项目配置 (Project)

**做什么**：每个客户项目（一组检测目标 + 一套步骤逻辑 + 一组事件 + 一组报警 + 一组数据导出）的全部配置。是整个系统的"客户脚本"。

**关键文件**：
- `backend/models/models.py: Project` 表（所有 7 个 JSON 字段）
- `backend/api/projects.py` — CRUD API
- `backend/api/source_project_config_apply.py` — 把 Project 应用到 VSM
- `frontend/src/views/Project/index.vue` (2925 ⚠️)
- `frontend/src/store/useProjectStore.js`

**Project 的 7 个 JSON 配置字段**：
- `pipeline_config` — 步骤序列、推理模式、容器分组、周期性强制动作
- `steps_config` — 每步的标签、最长/最短帧数、置信度阈值
- `events_config` — 自定义 OK/NG/警告事件
- `counters_config` — 计数器
- `alarm_config` — 报警规则
- `detection_config` — 检测框样式、语音、Toast
- `data_config` — 数据导出开关

**改动它必读**：`modify-project-config`（**必读，全链路影响**）

**关键扩展点**：所有 7 个字段都是 JSON，**新增需求优先往 JSON 加键**而不是新加 Column。

---

#### 模块 4：周期/步骤/事件 状态机

**做什么**：Cycle（一次完整生产周期）→ Step（步骤序列内的每一步）→ Event（每步触发的 OK/NG/自定义事件）。业务执行核心。

**关键文件**：
- `source_session_lifecycle_mixin.py` (1111 ⚠️) — Session/Cycle 生命周期 + scan_pair settle
- `source_check_modes_mixin.py` — 检查模式聚合器（仅 MRO）
- `source_settlement_mixin.py` (914 ⚠️) — 8 种结算逻辑
- `source_sequential_mixin.py` — 顺序模式
- `source_step_stats_mixin.py` — 步骤统计聚合
- `source_event_trigger_mixin.py` — `_trigger_event()` 中心 hook
- `source_periodic_actions_mixin.py` — v3.5.0 周期性强制动作
- `models/models.py` — `DetectionSession` / `DetectionCycle` / `StepRecord`

**对外接口**：`/api/v1/source/detection/results`（实时状态全集，含 PT/CT raw 数据）

**改动它必读**：`modify-source` + `debug-source` + `add-event-type`（如果新增事件类型）

**关键扩展点**：
- `_trigger_event` 是中心 hook，所有事件都从这里发出，插件可挂事件后处理
- `events_config` JSON 已支持自定义事件，新增需求优先扩此 JSON

---

#### 模块 5：多通道管理 (ChannelManager)

**做什么**：多工位（最多 4 个）独立运行，各通道自带 Project / 视频源 / 模型 / 周期，互不干扰。GPU 槽位分配 + 共享模型权重。

**关键文件**：
- `backend/api/channel_manager.py` — `ChannelManager` 单例（374 行）
- `backend/api/source.py` — 每个通道一个 `VideoSourceManager` 实例

**对外接口**：`/api/v1/workstations/*`

**改动它必读**：`debug-channel`

**关键不变量**：`channel_manager.set_channel_count(count)` 时**必须**调用 `mes_hook.on_channel_removed(cid)` + `alarm_router.on_channel_removed(cid)`，否则 MES dict 残留 + 报警串口未释放。

---

### 6.2 MES 子系统

#### 模块 6：MES 工单 / 工件 / 缺陷

**做什么**：完整 MES 业务模型：工单（生产任务）→ 工件（每个被检测的物理件）→ 缺陷（不良项）。

**关键文件**：
- `backend/api/mes.py` (673 ⚠️) — 工单/工件/缺陷 API
- `backend/services/work_order.py` (357) — 工单业务（含 `binding_scope`：project/channels/cluster）
- `backend/services/workpiece.py` (235) — 工件业务（含 `mark_inspecting` / `set_result`）
- `backend/services/defect.py` (243) — 缺陷分类
- `backend/services/mes_hooks.py` (1505 ⚠️) — **项目最大 Hub**，详见 `debug-mes`
- `backend/models/mes_models.py` (577) — 15 张 MES 表
- `frontend/src/views/MES/OrderPanel.vue` / `WorkpiecePanel.vue` / `DefectPanel.vue`

**改动它必读**：`debug-mes`（debug-mes 含 61 条历史 bug，覆盖此模块大半）

**关键扩展点**：`_handle_cycle_end` hook 已经联动 MES + 实时导出 + 报警 — 插件挂在此处即可。

---

#### 模块 7：扫码器 (Scanner)

**做什么**：3 大类扫码器：
- **LON 文本协议**（55256，4 种 scan_mode：A/B/C/D）
- **WMax 三端口协议**（55266+55276+55286，逆向）
- **虚拟扫码器**（device_id=-999，无硬件演示用）

**关键文件**：
- `backend/api/scanner.py` (533) — 扫码器 CRUD + scan_pair + 禁用
- `backend/api/wmax.py` (673 ⚠️) — WMax 35+ endpoint
- `backend/services/scanner.py` (1964 ⚠️⚠️ 项目第二大文件) — `ScannerService` + `ScannerConnection` + `_text_lon_listen_loop`
- `backend/services/wmax/` — WMax 协议子模块
- `backend/services/barcode_parser.py` (99) — 解析（**无 DataLen 校验**）
- `frontend/src/views/MES/ScannerPanel.vue` (1381 ⚠️) / `WMaxPanel.vue`

**改动它必读**：`debug-mes`

**关键扩展点**：
- `is_warn_no_barcode()` / `has_any_scanner_present()`（v3.5.2 加："没扫码器就静默"）
- LON 4 种扫描模式 (A/B/C/D)，**D 模式支持容器跨线/进区追踪**
- Scan-to-Scan 闭环（`scan_pair` 状态机）

---

#### 模块 8：MES Gateway + 外部设备

**做什么**：把检测结果推送给客户的 MES 系统。**5 种适配器**（注册名 → 类）：

| 注册名 | 类 | 协议 |
|---|---|---|
| `rest` | `RESTAdapter` | HTTP JSON |
| `form-data` | `FormDataAdapter` | 整 payload 序列化进 form 字段 |
| `form-urlencoded` | `FormUrlencodedAdapter` | 顶层键值展平 |
| `query-string` | `QueryStringAdapter` | 全部走 URL query |
| `modbus_rtu` | `ModbusRTUAdapter` | RTU/TCP 写 Holding（pymodbus 3.13+） |

**关键文件**：
- `backend/api/mes_gateway.py` (450) — Gateway API
- `backend/services/mes_gateway.py` (432) — `MESGateway` + 重试 + 鉴权
- `backend/services/mes_adapters/` — 5 个适配器 + `base.py`
- `backend/api/external_device.py` (366) — 外部设备 API
- `backend/services/external_device*.py` (5 个文件) — 实现 + 称重稳定状态机
- `frontend/src/views/MES/GatewayPanel.vue` (1073 ⚠️)

**改动它必读**：`debug-mes`

**关键架构事实**：
- payload 模板**不是 Jinja2**，是自研 `{key.path}` 替换 + `_array_source` 数组展开（在 `mes_adapters/base.py:render_template`）
- **重试在 `MESGateway._send_to_connection`**（不是 adapter 层），test endpoint 不重试
- 鉴权类型 5 种：`none` / `basic` / `bearer` / `api_key` / `custom_header`

---

#### 模块 9：集群汇总 (Cluster)

**做什么**：多机集群（standalone / host / slave）。slave 上报 host，host 按 `box_serial` 聚齐后推 MES。

**关键文件**：
- `backend/api/cluster.py` (296) — 集群 API
- `backend/services/cluster_collector.py` (1122 ⚠️) — host 汇总（含 timeout / heartbeat 后台线程）
- `backend/models/mes_models.py: ClusterConfig / BoxAggregations / BoxSummaries`
- `frontend/src/views/MES/ClusterPanel.vue`

**改动它必读**：`debug-cluster`（主从配置 / 心跳 / box 聚齐） + `debug-mes`（box_complete → Gateway 推送链路）

---

#### 模块 10：操作员 + License

**做什么**：操作员（current_operator 影响数据归属）+ 软件激活 / License 验证。

**关键文件**：
- `backend/api/operators.py` (214) — 操作员 API（**无 token，落盘 `current_operator.json`**）
- `backend/scripts/generate_license.py` — License 生成（私钥签名）
- `backend/models/models.py: Operator`
- `electron/license-manager.js` (203) — License 验证（RSA SHA256 + machineId）
- `frontend/src/views/Activation/`

**改动它必读**：`debug-operator-license`（machineId / 验签 / 当前操作员落盘） + `debug-electron`（Electron 主进程通用问题）

**License 算法（速查）**：
1. machineId = SHA256(stableFp) → `TJ-` + 12 位大写 hex
2. license.lic = JSON `{ data: stringPayload, signature: base64 }`
3. RSA + SHA256 验签（公钥嵌在 license-manager.js）
4. 校验 machineId + expiresAt

---

### 6.3 数据 / 导出

#### 模块 11：Session/Cycle/Step 数据记录

**做什么**：每次开机一个 Session，每次生产周期一个 Cycle，每个 Cycle 含多个 StepRecord。

**关键文件**：
- `backend/models/models.py: DetectionSession / DetectionCycle / StepRecord / VideoClip`
- `backend/api/sessions.py` (1005) — 数据 API（已分拆，含子路由挂载）
- `backend/api/sessions_stats.py` — 聚合统计（v3.5.0 置信度聚合）
- `backend/api/sessions_maintenance.py` (404) — 数据维护 / 清理
- `backend/api/sessions_export.py` (528) — CSV 导出实现
- `frontend/src/views/Data/index.vue` (~1715 ⚠️)

**对外接口**：`/api/v1/data/*`（含 `/export/csv?pt_mode=avg&ct_mode=avg`）

**改动它必读**：`debug-session` / `fix-data` / `modify-model`（改 schema 时）

---

#### 模块 12：自定义导出 (v3.5.0)

**做什么**：客户级"导出脚本"。5 种格式（txt/csv/docx/xlsx/pdf）+ Jinja2 模板 + 实时规则。

**关键文件**：
- `backend/models/export_models.py` (190) — 3 张表
- `backend/api/export_custom.py` (526) — 模板 CRUD
- `backend/api/export_realtime.py` (305) — 实时规则
- `backend/services/export_field_registry.py` (815 ⚠️) — **`ALL_FIELDS = 308 字段**
- `backend/services/export_context.py` (1131 ⚠️) — Jinja2 上下文构建
- `backend/services/export_renderer*.py` (4 个) — txt/csv/docx/xlsx/pdf 渲染
- `backend/services/export_seed.py` — 内置模板 seed
- `frontend/src/views/Data/components/CustomExportDialog.vue`

**对外接口**：`/api/v1/export/templates/*`、`/api/v1/export/realtime-rules/*`

**改动它必读**：`debug-export`（模板 / 渲染 / 字段中央仓库 / 实时规则） + `modify-model`（改 export_models.py 三张表）

**关键架构事实**（**给插件系统看**）：
- 字段注册表 308 字段是"系统数据全集" — 插件想做数据回写都靠这套（**注意**：`export_context.py` 模块注释写"302 字段骨架"是过时的）
- Jinja2 SandboxedEnvironment 已隔离 — 客户写模板不会污染主程序
- **实时规则 `trigger_event` 仅 3 种**：`cycle_end` / `session_end` / `box_complete`，**目前只有 `cycle_end` 实际接入**，其余 2 种在 `export_realtime.py:13` 标 `[todo]`

---

#### 模块 13：CSV 导出 / 备份

**做什么**：4 个快捷导出按钮（按日 / 按周 / 按月 / 日期范围）+ 自定义条件。备份是文件级（拷 `sql_app.db`）。

**v3.5.2 联动**：4 个快捷按钮都支持 `pt_mode` / `ct_mode`，`avg` 模式加"耗时(平均/秒)"列。

---

### 6.4 系统级

#### 模块 14：报警系统

**做什么**：事件触发后联动硬件：灯塔（Modbus 串口）+ 蜂鸣器 + 自定义动作。**支持多工位共享同一灯柱**（`shared_with` 配置）。

**关键文件**：
- `backend/api/alarm.py` (1009 ⚠️) — `AlarmManager` + `AlarmRouter`
- `frontend/src/views/Alarm/index.vue` (~670)

**改动它必读**：`debug-alarm`

**关键架构**：`AlarmRouter._load_all` 把 `shared_with` 列表合并进 `AlarmManager.set_shared_mode`，**多工位共享时所有 trigger_alarm/idle_light 都带 channel_id，由 `_recompose_and_apply` 按优先级合成单路串口输出**。

---

#### 模块 15：录像 (Recording)

**做什么**：每个 Cycle 关联 VideoClip，FFmpeg 后台进程编码。前端在 Data 页回放。

**关键文件**：
- `backend/api/source_recorder.py` — `FFmpegRecorder` + `KalmanFilter2D`
- `backend/api/source_recording_thread_mixin.py` + `source_recording_api_mixin.py` — 录制线程 + API
- `backend/api/source_recording_mixin.py` — **历史合并版（546 行，与上两者重叠）**
- `backend/models/models.py: VideoClip`

**改动它必读**：`debug-video`（含录像）

---

#### 模块 16：系统设置 (SystemConfig KV)

**做什么**：全局键值配置（display.monitor.ptMode / brand_name / inspector_name / license-cache 等）。

**关键文件**：
- `backend/api/system_display.py` (136)
- `backend/models/models.py: SystemConfig`
- `frontend/src/store/useSystemStore.js` (277)
- `frontend/src/views/Settings/index.vue` (1441 ⚠️)

**对外接口**：`/api/v1/system/*`

**关键扩展点**（**给插件系统看**）：这是**最适合存"客户级配置"的地方**，比 Project 配置粒度更全局。

---

#### 模块 17：Electron 桌面壳

**做什么**：主进程 = Electron。spawn 后端 → 等就绪 → 加载前端 → Splash 撤场。8 步关机 + 三层崩溃恢复。

**关键文件**：
- `electron/main.js` (737 ⚠️) — 主进程入口
- `electron/backend-manager.js` (616 ⚠️) — `python -m uvicorn backend.main:app`，**唯一启动方式**
- `electron/license-manager.js` (203) — License 验证
- `electron/preload.js` (13) — IPC 桥
- `electron/splash.html` / `shutdown.html`
- `electron/package.json` — **版本号唯一权威源**
- `electron/build/installer.iss` (145) — Inno Setup

**改动它必读**：`debug-electron`

**Electron IPC channel 速查**：
- `invoke`: `get-app-info` / `get-backend-url` / `get-license-status` / `import-license`
- `on`: `license-activated`
- 关机：`shutdown-progress` / `shutdown-error` / `shutdown-complete`（→ render）+ `shutdown-cancel` / `shutdown-force` / `shutdown-window-ready`（→ main）

---

### 6.5 工程基础

#### 模块 18：数据库 + 迁移

**做什么**：SQLite 单文件 + SQLAlchemy ORM + 简单 ALTER TABLE 迁移。

**关键文件**：
- `backend/db/database.py` (41) — engine + WAL + busy_timeout=15s
- `backend/main.py: migrate_database()` — 启动时 60+ ALTER TABLE
- `backend/models/*.py` — 31 张表

**改动它必读**：`modify-model`

**未来计划（feat/migrate-pg 分支）**：迁 PostgreSQL（已敲定）

---

#### 模块 19：打包发版

**关键文件**：
- `.github/workflows/build.yml` — 主 CI（Nuitka + conda-pack + Inno Setup）
- `.github/workflows/gitee-upload.yml` — Gitee 中转
- `electron/build/installer.iss` — Inno Setup
- `electron/package.json` — **版本号唯一权威源**

**改动它必读**：`build-release` + `update-release`

**Inno Setup 安装包关键参数**：
- AppId: `com.tianjun.ai-vision`
- 默认目录: `%ProgramFiles%\tianjun-ai-vision`
- 用户数据: `%APPDATA%\tianjun-ai-vision`
- 输出: `TianJun-AI-Vision-{version}-Setup.exe`

---

#### 模块 20：热补丁机制

**关键文件**：
- `.claude/skills/create-hotfix/SKILL.md` — 操作流程

**改动它必读**：`create-hotfix`

---

## 七、关键扩展点速查（给插件系统看）

> **如果你在做插件系统 / 客户定制 / 加新功能**，先看这一节：很多地方现有代码已"配置化"，应**复用**而不是重造。

| 扩展点 | 类型 | 用途 | 文件 |
|---|---|---|---|
| `Project.pipeline_config` | JSON | 步骤序列、推理模式 | `models/models.py` |
| `Project.steps_config` | JSON | 每步阈值/帧数 | 同上 |
| `Project.events_config` | JSON | 自定义 OK/NG/警告 | 同上 |
| `Project.alarm_config` | JSON | 报警规则 | 同上 |
| `Project.data_config` | JSON | 数据导出开关 | 同上 |
| `SystemConfig` 表 | KV | 全局配置（最适合插件用）| 同上 |
| `_trigger_event` hook | 函数 | 所有事件中心触发点 | `source_event_trigger_mixin.py` |
| `_handle_cycle_end` hook | 函数 | cycle 结束中心 hook | `services/mes_hooks.py` |
| MES 适配器注册 | `_REGISTRY` | 5 种推送协议 | `services/mes_adapters/__init__.py` |
| 自定义导出字段注册表 | `ALL_FIELDS` | **308 个字段** | `services/export_field_registry.py` |
| 实时导出规则 | JSON 配置 | 按工位/项目过滤 | `models/export_models.py` |
| 周期性强制动作 (v3.5.0) | JSON | 每 N 轮做 X | `source_periodic_actions_mixin.py` |
| 操作员系统 | 表 | current_operator 影响数据归属 | `api/operators.py` |
| Scanner `broadcast_channels` | JSON | 一扫码器服务多工位 | `models/mes_models.py:ScannerDevice` |

---

## 八、关键不变量（永远不能违反）

修改任何代码前，先确认你**没有**违反这些约定：

1. **API 前缀必须 `/api/v1/`**（不是 `/api/`）
2. **`OPENCV_FFMPEG_CAPTURE_OPTIONS=threads;1` 必须在 cv2 import 前 setdefault**（`backend/main.py:line 4`，v3.1.3 关键修复，否则 libavcodec 断言）
3. **修改 `source.py` 主类前必须看 `__getattr__/setattr__` 兼容层**（在 `source.py` 内）— 老代码访问 `self._kalman_enabled` 等会被路由到 has-a 组件
4. **`channel_manager.set_channel_count` 必须调用 `mes_hook.on_channel_removed` + `alarm_router.on_channel_removed`**，否则 MES dict / 报警串口残留
5. **测试 fixture 必须独立 DB / unique uuid，不要 reload uvicorn**（v3.5.0 BDD 框架痛过）
6. **修改 `mes_hooks.py` / `services/scanner.py` 前先读对应 changelog**（debug-mes 61 条历史 bug 是项目最大踩坑区）
7. **改前端 `views/Monitor` 前**：双缓冲 MJPEG + 多通道 state 隔离（v2.6.0 / v3.0.0 / v3.1.3 多次修过）
8. **改 ORM Schema 后必须在 `backend/main.py:migrate_database()` 加 ALTER TABLE**（老 SQLite 升级路径）
9. **bat 热补丁必须 CRLF 换行符**（LF 在 Windows 上闪退）
10. **不要在 `OPENCV_FFMPEG_CAPTURE_OPTIONS` 之前 import cv2**（顺序敏感）

---

## 九、已知架构 bug / 死代码索引

> 这些是已确认存在但还没修的事实。新 PR 不要复活、不要扩展。

### 真 bug（应被修）

| 位置 | 描述 | 影响 |
|---|---|---|
| `backend/core/config.py: _fix_db_paths` | SQL 用错表名 `ml_models`（实际叫 `models`） | DB 路径迁移逻辑对模型文件**永远不生效** |
| `.github/workflows/build.yml: CORE_FILES` | 11 个文件中 3 个不存在（`api/detection.py` / `api/websocket.py` / `services/detector.py`） | CI 实际只编译 8 个文件，跳过的会 WARNING 但不报错（潜在 IP 泄漏：很多 source_*_mixin 没被 .pyd 保护） |
| `source_recording_mixin.py` (546) | 与 `source_recording_thread_mixin.py + source_recording_api_mixin.py` **能力重叠** | 历史拆分残留，需决定保留哪份 |
| `source_camera_start_mixin.py` 与 `source_industrial_camera_mixin.py` | 都定义 `start_hcnetsdk` / `start_hikvision_camera` | 同名职责分裂，依赖 MRO 决出胜者 |

### 死代码（可清理）

| 文件 | 状态 |
|---|---|
| `frontend/src/views/Report/index.vue` (299) | **路由未注册**，但 `frontend/src/api/report.js` 仍部分被使用 |
| `frontend/src/api/task.js` (26) | 全前端**无 import** |
| `frontend/src/api/camera.js` (26) | 全前端**无 import** |
| `frontend/src/api/report.js` 中的 `getRecords` / `getTrend` / `exportPdfReport` | 局部死代码 |

### 文档不同步

| 位置 | 说 | 真相 |
|---|---|---|
| `frontend/src/api/export.js` 注释 | `fmt: 'txt'\|'csv'` | 后端允许 `txt/csv/docx/xlsx/pdf` |
| `backend/services/export_context.py` 模块注释 | "302 字段骨架" | `ALL_FIELDS = 308` |
| `backend/services/mes_gateway.py` 注释 | 提及 "Jinja" 过滤器 | 实际不是 Jinja2，是自研 `{key.path}` |

---

## 十、关键环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `TIANJUN_DATA_DIR` | `BASE_DIR` | 用户数据目录（DB / uploads / recordings） |
| `BACKEND_SKIP_INIT` | 否 | 跳过启动初始化（测试/调试用） |
| `ENABLE_API_DOCS` | `1` | `0` 关闭 `/docs` `/redoc` |
| `CORS_ALLOW_ORIGINS` | — | CORS 白名单 |
| `OPENCV_FFMPEG_CAPTURE_OPTIONS` | `threads;1` | 必须 cv2 import 前 setdefault |
| `MVCAM_COMMON_RUNENV` | — | 海康 SDK 路径 |
| `VITE_API_BASE_URL` | `http://localhost:8001/api/v1` | 前端 axios baseURL |
| `CONDA_PREFIX` | `~/anaconda3/envs/tianjun` | 开发模式 Python 路径 |

---

## 十一、文件路径速查

### 后端（25 个 api / 22 个 services）
```
backend/
├── main.py                        # FastAPI 入口 + 12 步启动 + 60+ ALTER TABLE 迁移
├── core/config.py                 # 配置 (DATA_DIR, BASE_DIR 等)
├── db/database.py                 # SQLAlchemy engine + WAL
├── models/                        # ORM (3 文件 31 表)
├── api/                           # 25 个文件
│   ├── source.py + 35 个 source_* (mixin/组件/工具/路由)
│   ├── projects.py / models.py / sessions*.py (4 个) / cameras.py
│   ├── tasks.py / reports.py / system_display.py / alarm.py
│   ├── operators.py / channel_manager.py / debug.py / rod_filter.py
│   ├── scanner.py / wmax.py / mes.py / mes_gateway.py / cluster.py / external_device.py
│   ├── export_custom.py / export_realtime.py    # v3.5.0
│   └── __init__.py (api_router 聚合)
├── services/                      # 22 个文件
│   ├── scanner.py (1964) / mes_hooks.py (1505) / mes_gateway.py
│   ├── work_order.py / workpiece.py / defect.py
│   ├── mes_adapters/  (5 个适配器 + base + __init__)
│   ├── external_device*.py (5 个)
│   ├── cluster_collector.py (1122)
│   ├── export_*.py (8 个，含 docx/pdf/xlsx renderer + field_registry + context + seed)
│   └── barcode_parser.py
└── scripts/generate_license.py
```

### 前端（4 store / 16 api / 12 view / 3 layout）
```
frontend/src/
├── views/    (12 个 .vue)
│   ├── Activation/   Source/   Project/   Model/
│   ├── Monitor/      ⭐ 检测中心 (4051 行 ⚠️ 项目最大 .vue)
│   ├── Data/         ⭐ 数据中心 + 自定义导出
│   ├── MES/          ⭐ 5 个子 Panel (Order / Workpiece / Defect / Scanner / Gateway)
│   ├── Alarm/        Settings/  Report/ ❌ 死代码
├── store/  (4 个 Pinia store)
│   ├── useSystemStore (277 行) / useProjectStore / useSourceStore / useScannerDisableStore
├── api/    (16 个 axios 封装，其中 task.js / camera.js 死代码)
├── layout/ (3 个：index.vue / Navbar.vue 550 行 ⚠️ / BottomBar.vue)
├── locales/ (5 种语言 zh-CN/zh-TW/en-US/ja-JP/ko-KR)
└── router/index.js (147 行 / 9 路由)
```

### Electron（双进程）
```
electron/
├── main.js                  # 入口 (737 行 ⚠️)
├── backend-manager.js       # spawn python uvicorn (616 行 ⚠️)
├── license-manager.js       # RSA SHA256 验签 (203)
├── preload.js               # IPC 桥 (13)
├── package.json             # ⭐ 版本号唯一权威源
├── splash.html / shutdown.html
└── build/installer.iss      # Inno Setup (145)
```

### CI / 文档
```
.github/workflows/
├── build.yml                # 主 CI (Nuitka + conda-pack + Inno Setup)
└── gitee-upload.yml         # Gitee 中转

.claude/skills/  (27 个本项目 skill)

docs/
├── 软件操作手册.md / .html / .pdf  # 客户用户手册
├── CHANGELOG.md
├── changelog/  (33 个历版 .md + .json)
├── 产品交接手册.md (⚠️ v2.4.0 已过时，参考要谨慎)
└── tuning/   (调参案例)
```

---

## 十二、版本里程碑（最近）

| 版本 | 日期 | 主要变更 |
|---|---|---|
| v3.5.1 | 2026-05-06 | PT/CT 三档显示 + CSV 导出联动 + 操作手册补完 |
| v3.5.0 | 2026-05-04 | 自定义导出 + 实时规则 + 周期性强制动作 + 完整测试框架 |
| v3.4.2 | hotfix | 禁用扫码守门 + reload 时序 + ERROR 不续 LON |
| v3.4.1 | hotfix | D 模式扫码器灯一直闪 + 校验工位拿错 |
| v3.4.0 | 2026-04-30 | LON D 模式 + 容器跨线追踪 |
| v3.3.0 | 2026-04-26 | scan_pair 状态机 |
| v3.1.3 | 2026-04-29 | FFmpeg 单线程强制（保命修复） |
| v2.7.16 | 2026-04-21 | source.py P5b 拆分（mixin 化起点） |
| v2.7.12 | 2026-04-22 | 版本号源改 package.json |

主线最新（**未发布**）：
- `df7ce2c` fix(camera): 海康相机 NameError
- `85c351c` feat(mes): 扫码器列表为空时静默"⚠ 未绑码"
- `f5b8c92`+ feat(periodic): 周期性强制动作 run-on-start + 不重置 + 自定义事件
- `??????` fix(geometry): 检测框越界 三层 clip 防御

下次 tag（v3.5.2 或 v3.6.0）会带上以上修复。

> **历史 bug 全档**：33 个 changelog × 184 条 BUG/FEAT/HOTFIX 已分类到 24 个旧 skill 内"历史踩坑"章节。统计：`debug-mes` 61 条 / `modify-frontend` 45 / `modify-source` 40 / `debug-source` 24 / `add-api-endpoint` 22 / `debug-video` 19。新增 3 个 skill（`debug-export` / `debug-cluster` / `debug-operator-license`）由 v3.5.x 真实代码反推编写，未追溯历史 changelog。

---

## 十三、当前在做的事（动态，看 git log 和分支名）

- 主分支 `main`：稳定版，发布给客户
- 分支 `feat/plugin-system`：**多客户定制插件系统 + 数据库迁 PG**（在做，长期分支）
  - 决策已敲定：迁 PG / 三档插件全做 / 签名机制 / 不做沙箱

---

## 十四、遇到不一致时的优先级

当 AGENTS.md / 产品交接手册 / changelog / 代码注释 互相矛盾时：

1. **代码 > 注释 > changelog > AGENTS.md > 产品交接手册**
2. 发现矛盾**立刻通知主作者**并提议更新 AGENTS.md
3. 永远**不要**信任 `docs/产品交接手册.md`（v2.4.0，已严重过时：表数 22 / `MLModel`类名 / API 前缀 `/api` 全错）

---

**本文件最后更新**：2026-05-07
**维护者**：项目主作者 + AI agents
**事实校验**：本版基于 33 个 changelog（184 条记录）+ 8 个 explore subagent 并行扫描的全盘扫描报告（`.tmp_audit/stage3_full_scan_report.md`）
