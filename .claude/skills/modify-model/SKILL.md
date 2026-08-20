---
name: modify-model
description: "安全修改数据模型(ORM)：字段变更、SQLite 手动迁移、序列化影响、前端字段映射。修改 backend/models/*.py 或数据库结构前先用本 skill 做影响分析。"
argument-hint: "[要修改的模型或字段]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__context7"
---

# modify-model: 数据模型安全修改

修改 ORM 前先读这一份。计划修改：$ARGUMENTS

## 0. 一眼看懂当前的数据库（v3.48 真相）

- **54 张表 = 13 + 20 + 4 + 5 + 4 + 1 + 3 + 1 + 1 + 2**，分布在 **十个** ORM 文件（models / mes_models / export_models / auth_models / plugin_models / weighing_models / notify_models / plc_models / trigger_models / archive_models）
- **迁移已版本化（2026-07 治理）**：运行时迁移在 `backend/db/migrations/`（注册表 + runner + `schema_migrations` 记账表）；alembic 仅服务 PG 工程（db-matrix CI），不进客户机运行时
- 数据库是单文件 SQLite (`sql_app.db`)，开 WAL + busy_timeout=15s
- 启动时序：`Base.metadata.create_all()` 建新表 → `apply_pending(engine)` 按序应用迁移（老库补列在 m0000 基线里） → `fix_orphan_*()` 清孤儿
- **旧版交接文档写的"22 张表 / `MLModel` 类名 / 表名 `ml_models`"全是过时信息，以代码为准**（该手册已于 2026-06-26 删除）
- `Operator`/`operators` 表已随 v3.10.0 用户系统移除（端点 410 Gone），别再引用

## 1. 十个 models 文件的 54 张表清单

### 1.1 `backend/models/models.py`（13 张，核心检测）

| ORM 类 | 表名 | 一句话 |
|---|---|---|
| `Project` | `projects` | 项目主表，承载 7 个 JSON 配置字段 |
| `Model` | `models` | 模型文件元信息（**类名是 `Model` 不是 `MLModel`，表名是 `models` 不是 `ml_models`**）；v3.47 加 `source`（'local'/'yolovision'，NULL 视同 local）+ `meta` JSON（训练分析/包 provenance，迁移 m0008）|
| `ModelConversion` | `model_conversions` | 模型格式转换（PyTorch→TRT 等），跨项目共享 |
| `Task` | `tasks` | 离线推理任务（前端 `task.js` 已死代码）|
| `Camera` | `cameras` | 旧式相机表，与 `/source/*` 并存 |
| `DailyStat` | `daily_stats` | 每日统计汇总（写入路径已废）|
| `SystemConfig` | `system_configs` | 全局 KV 配置（v3.5.0 加 license-cache）|
| `ChannelGroup` | `channel_groups` | v3.13.1 工位组（RFC 10 单机多工位并行联动）|
| `DetectionSession` | `detection_sessions` | 一次开机=一个 Session，含 `channel_id` `shift_label` `order_id` |
| `DetectionCycle` | `detection_cycles` | 一次生产周期，关联 Session/Order |
| `StepRecord` | `step_records` | Cycle 内每个步骤的检测记录 |
| `VideoClip` | `video_clips` | 录像文件元信息（`clip_type=session/cycle/step`）|
| `DataExportSetting` | `data_export_settings` | CSV 导出/录像开关单行配置 |

### 1.2 `backend/models/mes_models.py`（20 张，MES + 集群 + 外设 + 流水线）

| ORM 类 | 表名 | 一句话 |
|---|---|---|
| `WorkOrder` | `work_orders` | 工单主表，含 `binding_scope` (project/channels/cluster) + `target_channels/stations` JSON |
| `Batch` | `batches` | 批次（与工单一对多）|
| `Workpiece` | `workpieces` | 工件追溯主表（`serial_no` + `project_id` 唯一）|
| `WorkpieceInspection` | `workpiece_inspections` | 工件 ↔ Cycle 中间表，支持多次返工 |
| `DefectRecord` | `defect_records` | 缺陷记录（NG 自动分类）；v3.47 补 `cycle_id` 索引（迁移 m0007，治启动期大表扫描）|
| `DefectCode` | `defect_codes` | 缺陷代码字典 + `detection_labels` 自动映射 |
| `ScannerDevice` | `scanner_devices` | 扫码器配置（**字段最多的表**，含 v3.4.0 D 模式几何）|
| `ScanLog` | `scan_logs` | 扫码记录 |
| `MESConnection` | `mes_connections` | 外部 MES 推送连接配置 |
| `MESCommLog` | `mes_comm_logs` | MES 推送通讯日志 |
| `ExternalActiveAlarm` | `external_active_alarms` | v3.29 外部 MES 入站在途报警台账（川南范式）|
| `ClusterConfig` | `cluster_config` | 集群配置（id 固定为 1，单行表）|
| `BoxAggregation` | `box_aggregations` | 集群按 `box_serial` 聚齐的临时表 |
| `BoxSummary` | `box_summaries` | 箱子最终汇总结果 |
| `ExternalDevice` | `external_devices` | 外部设备（称重/PLC/传感器等）|
| `ExternalDeviceLog` | `external_device_logs` | 外设原始数据日志 |
| `WorkpieceFlowConfig` | `workpiece_flow_configs` | v3.14 RFC 11 串行流水线结算配置 |
| `WorkpieceFlowRun` | `workpiece_flow_runs` | v3.14 串行流水线单件运行实例 |
| `PackagingFlowConfig` | `packaging_flow_configs` | v3.21+ 包装线闭环配置（含 v3.30 `name_match_strict_boundary`）|
| `PackagingFlowRun` | `packaging_flow_runs` | 包装线单箱运行实例 |

### 1.3 `backend/models/export_models.py`（4 张，v3.5.0+ 自定义导出）

| ORM 类 | 表名 | 一句话 |
|---|---|---|
| `ExportTemplate` | `export_templates` | 模板中央仓库（`is_system` 区分内置/用户）|
| `ExportRealtimeRule` | `export_realtime_rules` | 实时导出规则（每个 cycle 自动写文件）|
| `ExportRunLog` | `export_run_logs` | 实时/批量导出运行日志（成功/失败/跳过原因）|
| `ExportScheduledRule` | `export_scheduled_rules` | v3.8.x 定时导出规则 |

### 1.4 `backend/models/auth_models.py`（5 张，v3.10.0 用户系统）

| ORM 类 | 表名 | 一句话 |
|---|---|---|
| `User` | `users` | 多角色账号（替代已废弃 operators）|
| `Role` | `roles` | 角色 + 权限 JSON |
| `UserRole` | `user_roles` | 用户 ↔ 角色多对多 |
| `SessionToken` | `session_tokens` | 登录 token（Bearer 鉴权）|
| `ApiKey` | `api_keys` | M2M API Key（SHA256 + scope）|

### 1.5 `backend/models/plugin_models.py`（4 张，v3.13+ 插件平台）

| ORM 类 | 表名 | 一句话 |
|---|---|---|
| `PluginRecord` | `plugins` | 已安装插件清单（签名/版本/激活状态）|
| `PluginState` | `plugin_state` | 插件 `plugin_data` JSON 命名空间持久化 |
| `PluginAuditLog` | `plugin_audit_log` | 插件主动 API 调用审计 |
| `PluginConfigVersion` | `plugin_config_versions` | v3.28 插件配置版本化统一保存 |

### 1.6 `backend/models/weighing_models.py`（1 张，v3.31.0 称重投料模式）

| ORM 类 | 表名 | 一句话 |
|---|---|---|
| `WeighingRecord` | `weighing_records` | 逐件逐料称重台账：一行 = 一件产品的一道料一次称量（channel_id / product_sn / model_name / operator / material / standard / initial / net / verdict(ok・shortage・over・no_spec) / ts）；字段与 `weighing_engine.py` 产出的 result dict 对齐，新表走 create_all 无需 ALTER；`main.py` 已显式 import `weighing_models` |

### 1.7 `backend/models/notify_models.py`（3 张，v3.46 每日短信日报）

| ORM 类 | 表名 | 一句话 |
|---|---|---|
| `SmsReportRule` | `sms_report_rules` | 日报规则（发送时刻/收件人/内容模板/开关） |
| `SmsSendLog` | `sms_send_logs` | 发送日志（渠道/状态/错误） |
| `CounterDailyStat` | `counter_daily_stats` | 计数器按日快照（供日报变量取数） |

### 1.8 `backend/models/plc_models.py`（1 张，v3.48 RFC 13 PLC 连接器）

| ORM 类 | 表名 | 一句话 |
|---|---|---|
| `PLCConnection` | `plc_connections` | 一行 = 一条 PLC 连接：driver + conn_params/points/read_rules/write_rules/options 五个 JSON 列，全配置驱动；create_all 自建无迁移 |

### 1.9 `backend/models/trigger_models.py`（1 张，v3.48 RFC 14 触发中心）

| ORM 类 | 表名 | 一句话 |
|---|---|---|
| `TriggerChannel` | `trigger_channels` | 一行 = 一个触发源实例：type（pixel_region/hid_key/http/serial_pattern/timer/mock）+ params/rules/options 三个 JSON 列；create_all 自建无迁移 |

### 1.10 `backend/models/archive_models.py`（2 张，v3.53 录像归档）

| ORM 类 | 表名 | 一句话 |
|---|---|---|
| `VideoArchiveRule` | `video_archive_rules` | 一行 = 一条归档规则：筛选（result_filter/channel_filter/project_filter）+ 目的地（dest_type/dest_dir/dest_config 敏感字段 Fernet 密文）+ 命名（filename_template Jinja2/subdir_by_date/overwrite_policy）+ 证据（attach_keyframe/keyframe_watermark/sidecar_template_id/bundle_zip/transform/clip_seconds）+ 治理（active_window/bandwidth_limit_kbps/delete_source_after）+ 运行统计快照；create_all 自建无迁移 |
| `VideoArchiveLog` | `video_archive_logs` | 归档台账：一行 = 一次搬运（cycle_id/src_path/dest_path/status success·failed·skipped/error/file_size/duration_ms）；export_context 反查 `cycle.archived_*` 字段的数据源 |

> ⚠️ 新 ORM 文件三处注册缺一不可（BUG-003 血泪）：`backend/main.py` 显式 import + `tests/conftest.py` try 块 import + `alembic/env.py` import。只靠 router import 链兜底会在测试库漏表。

## 2. 关键命名陷阱（别再写错了）

- ORM 类名是 **`Model`**，不是 `MLModel`。`backend/core/config.py: _fix_db_paths` 写的 `ml_models` 是 bug（已知未修，见 AGENTS.md 第九节）
- 表名是 **`models`**（一个普通英文复数），不是 `ml_models`
- 7 个 JSON 字段都在 `Project`：`pipeline_config / steps_config / events_config / counters_config / alarm_config / detection_config / data_config`
- 字段类型用 `Column(JSON, nullable=True)`，**不要**用 `Text` 然后自己 `json.loads/dumps`（旧代码有少量这种历史，新加字段一律用 `JSON`）

## 3. 版本化迁移机制（2026-07 起，替代旧 migrate_database 大列表）

**运行时迁移在 `backend/db/migrations/` 包**（RFC：`docs/rfc/DB迁移版本化治理_设计方案_RFC.md`）：

```
backend/db/migrations/
├── __init__.py       # _MIGRATION_MODULES 显式注册表 + apply_pending(engine) runner
├── m0000_legacy.py   # 存量 109 条补列 + operators 清理，已冻结 ⚠️ 不许再往里加
└── m0001_xxx.py ...  # 从 v3.32 起每个 schema 变更一个文件
```

要点：

1. **m0000 永远幂等重跑**（不看记账）：inspector 逐列探查缺列才 ALTER，保证任意历史版本老库（v3.1 起跳都行）升级安全；**m0001 起走 `schema_migrations` 记账跳过**，不再每次启动全量探查
2. **新增 schema 变更三步**：改 ORM → 新建 `mXXXX_语义名.py` 暴露 `MIGRATION_ID` + `apply(engine)` → 模块名追加进 `_MIGRATION_MODULES`（显式注册，无目录扫描，兼容 Nuitka 编译）
3. 迁移模块里能写任意操作（补列 / 建索引 / 回填数据 / 删表）——特例不再塞 main.py
4. 容错语义：记账表建失败 → 降级只跑 m0000；某迁移失败 → 事务回滚、停止后续、不阻断启动
5. **新加的表不用写迁移**：`Base.metadata.create_all(bind=engine)` 自动创建注册到 `Base` 的新表（见下一节）
6. `main.py::migrate_database()` 已改为断言桩（调用即 RuntimeError），**别再往那里塞 ALTER**
7. 方言归一（PG 的 `BOOLEAN DEFAULT 1→TRUE`、`JSON→JSONB`）在 m0000 内；新迁移写 DDL 时同样要过 `get_dialect()` 判断

## 4. 改字段（最常见操作）— 必须四步全做

### 步骤 A：`backend/models/<file>.py` 加字段

```python
class ScannerDevice(Base):
    ...
    new_field = Column(Integer, default=0, nullable=False)
```

约束：

- **必须有默认值或允许 NULL**（SQLite ALTER TABLE 不允许加 `NOT NULL` 而无默认值的列）
- **新建库**走 ORM 定义；**老客户库**走迁移模块的 ALTER → 默认值在两处都要一致

> 近期 schema 变更样例（v3.30.0）：`packaging_flow_configs` 新增 `name_match_strict_boundary BOOLEAN DEFAULT 0`（规格→项目自动同名匹配的"严格边界"开关；ORM 默认 `False` + 迁移 ALTER `DEFAULT 0` 两处对齐）。
> 版本化迁移样例（v3.43，m0002/m0003）：`packaging_flow_configs` 分两个迁移模块加 4 列——m0002 重扫拦截两列（`block_completed_order_rescan BOOLEAN DEFAULT 0` + `event_completed_order_rescan INTEGER`）、m0003 缺工单判定两列（`tail_paper_scan_alarm BOOLEAN DEFAULT 1` + `tail_paper_timeout_s INTEGER DEFAULT 0`）；均在 `migrations/__init__.py` 的 `_MIGRATION_MODULES` 登记，PostgreSQL 布尔默认值走 `BOOLEAN DEFAULT FALSE/TRUE` 分道。
> 版本化迁移样例（v3.45，m0004/m0005）：仍是 `packaging_flow_configs`——m0004 箱标签扫码授权 9 列（`box_label_scan_required` / `label_qty_enabled` / `label_qty_segment` / `label_qty_pattern` / `label_rescan_action` / `unauthorized_cycle_action` / `label_total_check` + 三个事件列）、m0005 工单同步开关 1 列（`sync_work_orders BOOLEAN DEFAULT 1`，**默认开**——PG 分道 `DEFAULT TRUE`，读取端老库 NULL 视为开：`getattr(row, ..., None) not in (False, 0)`）。默认开的新列这个"NULL 视为开"读法是惯例，别写成 `bool(getattr(...))`（NULL 会被误判关）。

### 步骤 B：`backend/db/migrations/` 新建迁移文件

```python
# backend/db/migrations/m0001_scanner_new_field.py
from sqlalchemy import inspect, text

MIGRATION_ID = "m0001_scanner_new_field"

def apply(engine):
    insp = inspect(engine)
    if "new_field" in {c["name"] for c in insp.get_columns("scanner_devices")}:
        return
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE scanner_devices ADD COLUMN new_field INTEGER DEFAULT 0"))
        conn.commit()
```

再把 `"m0001_scanner_new_field"` 追加进 `migrations/__init__.py::_MIGRATION_MODULES`。

> ⚠️ **多分支并行的迁移撞号坑（2026-08-07 实锤）**：两个并行分支各自新建了 `m0007_*` 迁移（defect 索引 vs models 互连列），合并时 `_MIGRATION_MODULES` 冲突 + 编号重复。**合并顺序在后的分支必须重编号**（改文件名 + 文件内 `MIGRATION_ID` + 注册表三处一致），因为 `schema_migrations` 按 ID 记账，改名后老库会当新迁移重跑——所以迁移必须幂等（inspector 探查缺列才 ALTER）。开新迁移前先 `ls backend/db/migrations/` 看所有分支已占用的最大号，并在 PR 描述里声明占号。
老客户的 SQLite 库靠这一步才能加上列。**忘了这一步 = 升级即崩**。

### 步骤 C：序列化对齐

项目里**没有统一的 `Model.to_dict()`**，序列化分散在两处：

1. **Pydantic Schema**（`backend/api/<module>.py` 顶部）
   - 例：`OrderCreate / OrderUpdate / OrderResponse`，加字段要在请求和响应两个 schema 都加
2. **手动构造 dict 的辅助函数**
   - 例：`backend/api/mes.py: _serialize_order(order)`、`scanner.py: _serialize_scanner(...)`、`mes_gateway.py: _serialize_*`、`export_realtime.py: _serialize_rule(...)`
   - 加字段要在这些函数的返回 dict 里加 key

用 Grep 找受影响的序列化函数：

```
rg "def _serialize_" backend/api/
rg "<模型类名>" backend/api backend/services
```

### 步骤 D：前端字段映射

按优先级查：

1. `frontend/src/api/*.js` axios 封装 — 检查请求/响应字段
2. 视图 — 主要是 `Project/index.vue`、`MES/*Panel.vue`、`Data/index.vue`、`Settings/index.vue`、`Source/index.vue`、`Model/index.vue`
3. Pinia store — `useProjectStore` / `useSystemStore` / `useSourceStore` / `useScannerDisableStore`

特别注意：JSON 字段的子键变更**不会**被 schema 校验拦下，只能靠人查 — 见第 6 节。

## 5. 加新表（步骤简单，但容易忘记一步）

### 步骤 A：在 `backend/models/<file>.py` 定义类

继承 `Base`，写 `__tablename__`。如果文件本身没被任何模块 import，**新建一个 models 子文件时务必让 `backend/main.py` 在 `Base.metadata.create_all` 之前显式 import 一次**：

```python
# backend/main.py
from backend.models import export_models  # noqa: F401  ← 关键
Base.metadata.create_all(bind=engine)
```

`export_models.py` 就是按这套接入的；如果忘了 import，新表**永远不会被创建**（`Base` 拿不到子类）。

### 步骤 B：什么都不用做，启动时 `create_all` 自动建

迁移模块**不需要**给新表加任何东西 —— 迁移只管"老表加新列/数据修正"。

### 步骤 C（可选）：种子数据

如果新表需要内置数据，参考 `backend/services/export_seed.py`（v3.5.0 模板内置 seed 模式），在 startup 钩子里跑一次 idempotent 插入。

## 6. JSON 字段的特别注意事项

### 7 个 JSON 字段都在 `Project`

`pipeline_config / steps_config / events_config / counters_config / alarm_config / detection_config / data_config`。

它们用 `Column(JSON, nullable=True)`，SQLAlchemy 会自动 `json.loads/dumps`，但是：

- **不经过 Pydantic 校验** —— 子键改名/删除不会被静态检查捕获
- **新增需求优先往 JSON 里加键**，不要轻易加新 Column
- 改 JSON 结构时要全链路同步（这是 `modify-project-config` skill 的领地，单独读那一份）：
  1. `backend/api/source_project_config_apply.py: set_project_config()` — 解析器
  2. `backend/services/source_*_mixin.py` — 真正用配置的状态机
  3. `frontend/src/views/Project/index.vue` — 配置编辑器（2925 行 ⚠️）
  4. `frontend/src/views/Monitor/index.vue` — 显示
  5. `frontend/src/layout/Navbar.vue: handleProjectChange()` — 默认值初始化

### 其他常用 JSON 字段

| 字段 | 表 | 用途 |
|---|---|---|
| `target_channels / target_stations` | `work_orders` | 工单绑定范围（v3.1.0）|
| `parse_config / broadcast_channels / scan_d_line / scan_d_zone` | `scanner_devices` | 解析配置 + D 模式几何 |
| `protocol_config / parse_config / validation_rules` | `external_devices` | 协议/解析/校验规则 |
| `expected_stations / channel_station_map` | `cluster_config` | 集群站点映射 |
| `cycle_context / aggregated_context` | `box_aggregations / box_summaries` | 集群上下文聚合 |
| `channel_filter / project_filter` | `export_realtime_rules` | 实时规则过滤 |
| `counters_snapshot` | `detection_sessions` | 计数器快照 |
| `extra_info` | `video_clips` | 录像元数据 |

### TypeDecorator？

项目里**没有自定义 TypeDecorator**，统一用 `sqlalchemy.JSON`（在 SQLite 上落地为 TEXT）。如果你需要落地后做加密/压缩，再考虑 TypeDecorator —— 但目前**没有先例可抄**，新加之前先和主作者商量。

## 7. SQLite 限制速查（写代码前对一遍）

| 想做的事 | SQLite 是否支持 | 项目里的现状 |
|---|---|---|
| 加列 + 默认值 | 支持 | `migrate_database()` 主用法 |
| 加 NOT NULL 列（无默认值）| **不支持** | 一定要 `nullable=True` 或 `DEFAULT xxx` |
| 删列 | 3.35.0+ 支持，**但项目部署的版本可能不支持** | 实践上 = 不能删，留着即可 |
| 改列类型 | 不支持 | 实践上 = 不能改，新加一列复制 + 切换写入点 |
| 改列名 | 3.25.0+ 支持 | 不要做，会破坏老客户库 |
| 加 FK 约束 | ALTER 不支持加 FK | 旧库只有逻辑约束，新库才有真 FK |
| 加唯一索引 | 支持，但要避开重复数据 | 用 `Index(..., unique=True)` 或 `UniqueConstraint(...)` |

**一句话：只加不删、只加不改类型、只加可空或带默认值的列。**

## 8. v3.5.0+ 改动注意（必看）

`backend/models/export_models.py` 的 3 张表是 v3.5.0 引入：

- `ExportTemplate` — `is_system` + `builtin_id` 是系统模板键，**不要随意改预设模板的 builtin_id**，会导致 4 个一键导出按钮找不到对应模板
- `ExportRealtimeRule` — `trigger_event` 字段值只支持 `cycle_end / session_end / box_complete`，**目前只有 `cycle_end` 真正接入**，其余两个在 `backend/api/export_realtime.py:13` 标 `[todo]`
- `ExportRunLog` — `source_type` 字段值 `realtime / batch / manual_test`

接入入口在 `backend/main.py`：

```python
from backend.models import export_models  # noqa: F401  ← v3.5.0 加的
Base.metadata.create_all(bind=engine)
```

字段变更同步：

1. ORM 定义 (`export_models.py`)
2. migration（如果是改老字段）
3. `backend/services/export_seed.py` 内置模板的字段映射
4. `backend/services/export_field_registry.py` 的 `ALL_FIELDS = 308 字段`（**注意**：`backend/services/export_context.py` 模块注释写"302 字段骨架"是过时的）
5. 前端 `frontend/src/views/Data/components/CustomExportDialog.vue` 字段树

## 9. 强制分析流程（动手前跑一遍）

### 第 1 步：确认目标
- 读对应 `backend/models/<file>.py`，确认字段类型 / 约束 / 默认值
- 是 JSON 字段？跳到第 6 节

### 第 2 步：查所有读写点

```
rg "<模型类名>" backend/api backend/services backend/main.py
rg "<字段名>" backend/api backend/services backend/main.py
```

### 第 3 步：查序列化函数

```
rg "def _serialize_" backend/api
```

逐一确认是否要加新 key。

### 第 4 步：查前端

```
rg "<驼峰字段名>|<下划线字段名>" frontend/src
```

### 第 5 步：写迁移
- 加列 → `backend/db/migrations/` 新建 `mXXXX_*.py` + 注册（见第 3 节）
- 加表 → 确保 `backend/main.py` 已 import 该 models 文件
- 不动老列

### 第 6 步：输出影响报告

```
模型: <类名> / 表名 <table>
变更: <字段名>, 类型 <type>, 新增/扩 JSON 子键
写入: <code 位置列表>
读取: <code 位置列表>
Migration: <ALTER 语句 或 "新表无需 ALTER">
序列化点: <_serialize_xxx 函数列表 + Pydantic schema>
前端影响: <api/*.js + views/*.vue + store>
JSON 子键链: <set_project_config + 视图 + Navbar 默认值> （仅 JSON）
风险: 低 / 中 / 高
```

## 10. 修改原则（给未来的自己）

1. **只加不删**：SQLite 不方便删列，留着零成本
2. **必须有默认值**：`DEFAULT` 或 `nullable=True`，二选一
3. **JSON 子键向后兼容**：新增 OK，删除/改名要全链路同步并跑完冒烟
4. **Migration 幂等**：探针 try/except 已经处理，不要写 `DROP COLUMN` / `MODIFY COLUMN`
5. **新表两处声明**：models 类 + main.py 显式 import；create_all 会接管建表
6. **同步 Pydantic Schema**：`backend/api/<module>.py` 的 `*Create / *Update / *Response`
7. **改完跑冒烟**：启动后端 → 打开对应页面 → 看日志没 OperationalError
8. **PG 迁移在路上**：长期分支 `feat/migrate-pg` 已敲定迁 PostgreSQL；新代码尽量少写 SQLite-only 语法（如 `INSERT OR IGNORE`），优先 ORM `merge / get_or_create` 风格

## 11. 历史变更速查

| 版本 | 变更 |
|---|---|
| v2.3.0 | `DetectionSession/DetectionCycle` 加 `order_id`（MES 工单关联）|
| v2.5.0 | `ScannerDevice` 加 `rebind_mode / bind_timing` |
| v2.7.5 | `ExternalDevice` 加稳定值判定 + 有重无码告警字段 |
| v2.7.16 | `ScannerDevice` 加 `late_scan_bind_window_sec / scan_mode / throttle_idle_ms` |
| v3.1.0 | `WorkOrder` 加 `binding_scope / target_channels / target_stations`（**改这三个时**：Pydantic + `_serialize_order` + `OrderPanel.vue` + `WorkOrderService._normalize_binding` + `get_active_order/find_cluster_orders` + 计件触点 `_handle_cycle_end` / `cluster_collector._check_and_dispatch`）|
| v3.1.1 | `ExternalDevice.pairing_mode` (stable / instant) |
| v3.1.2 | `ScannerDevice` 加广播结算字段；`ClusterConfig.station_result_strategy` |
| v3.3.0 | `ScannerDevice.scan_pair_max_wait_sec`（码-码闭环）|
| v3.4.0 | `ScannerDevice` 加 D 模式几何字段 `scan_d_geometry / scan_d_line / scan_d_zone / scan_d_gone_confirm_frames` |
| v3.5.0 | 新文件 `export_models.py` + 3 张表 + `SystemConfig` 接入 license-cache JSON |
| v3.10.0 | 新文件 `auth_models.py` 5 张表（用户系统）；`Operator`/`operators` 废弃移除 |
| v3.13.x | 新文件 `plugin_models.py` 4 张表；`models.py` 加 `ChannelGroup`（RFC 10）|
| v3.14.0 | `mes_models.py` 加 `workpiece_flow_configs/runs`（RFC 11 串行流水线）|
| v3.21+ | `mes_models.py` 加 `packaging_flow_configs/runs`（包装线闭环）|
| v3.29.0 | `mes_models.py` 加 `external_active_alarms`（入站在途报警台账）|
| v3.30.0 | `packaging_flow_configs` 加 `name_match_strict_boundary BOOLEAN DEFAULT 0` |
| v3.31.0 | 新文件 `weighing_models.py` + `weighing_records` 表（称重投料逐件台账，`main.py` 显式 import + create_all，无 ALTER）|
| v3.38.0 | `step_records.cycle_id` + `video_clips.related_id` 加索引（热路径查询，迁移 `m0001_hot_path_indexes.py`——版本化迁移体系第一号，新迁移照它抄）|
| v3.43.0 | `packaging_flow_configs` 加 4 列（重扫拦截 m0002 + 缺工单判定 m0003）|
| v3.45.0 | `packaging_flow_configs` 加 10 列（箱标签扫码授权 9 列 m0004 + 工单同步开关 m0005，后者默认开 NULL 视为开）|
| v3.50.0 | `ScannerDevice` 加生命周期 3 列（迁移 m0010）：`resume_on VARCHAR(16) DEFAULT 'cycle_end'`（周期结束亮灯时机，'ok_only'=NG 灭灯等人工恢复）、`rearm_forget_last BOOLEAN DEFAULT 0`（亮灯作废旧码）、`strict_ok_dedup BOOLEAN DEFAULT 0`（已 OK 条码永久拒绝）；PG 布尔默认值分道 `FALSE` |

> 完整 changelog 在 `docs/changelog/` 下，每个 .md 都标了 BUG/FEAT/HOTFIX。
