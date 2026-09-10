# 03 — 数据层 · 导出 · 鉴权 · 插件（读码笔记）

> 阅读范围（2026-07-05）：`backend/models/` 全部 · `backend/api/sessions*.py` · `projects.py` · `export_*` 全套 · `auth/users/roles/api_keys` · `backend/plugin_system/` 全部 · `plugins.py` · `database.py`  
> 行号锚定当前 `tianjun-main` 工作区源码，后续改动以代码为准。
> 2026-07-17 v3.41 复核：补账 v3.33~v3.41 变更（数据/导出/项目域），受影响小节的行数与行号已刷新；插件系统（`backend/plugin_system/`）自基线零变更，第六、七节原样有效。
>
> **v3.56 补账（2026-09-01，多码采集数据/导出面）**：
> - **新文件 `models/scan_collect_models.py`（2 表，库表总数 54→56）**：`ScanCollectConfig`（scan_collect_configs：project_id 唯一 + enabled + config JSON——slots 数组每槽 {key,label,count,regex,role,dedup_cross_group,on_overflow} + 全局策略 sequence_fallback/dedup_in_group/dedup_cross_group/settle_on/on_overflow/on_unmatched/timeout_sec/event_ok_id/event_ng_id/ng_pending/vision_gate/vision_window_sec/vision_missing）；`ScanCollectRecord`（scan_collect_records：group_id/channel_id/project_id/slot_key/slot_label/code/seq/status[scanned|deleted|void]/group_result[ok|ng_missing|ng_timeout|ng_vision|void]/workpiece_id/scanned_at/settled_at，索引 group_id/code/workpiece_id）。新表走 create_all 自动建，无迁移。
> - `services/export_realtime.py`：新增 `dispatch_scan_group_export`——查 `trigger_event == "scan_group_end"` 规则，上下文挂 `scan_collect` 段（结算摘要 + 按槽位分组视图 slots[].codes，模板可按扫码顺序或类别遍历），channel/project 过滤与 skip 台账同既有触发器。
> - `api/export_realtime.py` + `frontend Data/RealtimeRulesDialog.vue`：触发事件下拉露出 `scan_group_end`（多码采集码组结算）。
> - `services/export_context.py` / `export_field_registry.py`：字段中央仓库补 `scan_collect.*` 分组（group_id/result/workpiece_sn/total/missing/slots/codes 等）。
> - `alembic/env.py`：补 scan_collect 模型 import（PG 基线迁移含新表）。
>
> **v3.47 补账（2026-08-07）**：
> - `models/models.py` `Model` 表：加 `source`（'local'/'yolovision'，NULL 视同 local）+ `meta` JSON（训练分析 x-analysis / 包 provenance），迁移 `m0008_model_interconnect_meta`（PG 走 JSONB 分道）；`schemas/model.py` 同步透出
> - `plugin_system/registry.py`：**F8 插件导出字段 registry 落地**（代码注释标 v3.46，实际随 v3.47 发版）——`registry.export_fields.register(fields, provider)`，字段 path 强制 `plugin.<customer_code_snake>.` 前缀（连字符转下划线），provider 签名 `(db, ctx) -> dict`，重复 register 整体替换
> - `services/export_field_registry.py`：中央仓库新增「插件字段」分组（首个消费方 lg-worktime 18 个 Lean 字段）
> - `services/export_context.py`：`_fill_plugin_sections` 执行 provider(db, ctx)，值挂 `ctx["plugin"]["<cc_snake>"]`，异常隔离、缺字段静默空值
> - `api/export_custom.py`：范围导出未收尾会话回落实数（不再等收尾快照）
> - 测试锚点：`tests/plugin_system/test_export_fields_registry_F8.py`
>
> **v3.48 补账（2026-08-10）**：
> - **`models/plc_models.py`（新，47 行）**：`plc_connections` 表（RFC 13）——name/driver（8 选一）/conn_params JSON/points JSON（点位清单：地址+类型+字节序+scale）/rules JSON（触发规则）/write_backs JSON（事件写回）/enabled。整表 JSON 配置化，加点位/规则不动 schema。
> - **`models/trigger_models.py`（新，27 行）**：`trigger_channels` 表（RFC 14）——name/source_type（6 选一）/params JSON/actions JSON（动作链）/enabled/channel_id。
> - 迁移 **`m0009_pkg_tail_paper_only_after_awaiting`**：`packaging_flow_configs` 加 `tail_paper_only_after_awaiting` 列（SY9 分支原编号 m0006，吸收时因与主线 m0006_sms_report_content_template 撞号改 m0009）。plc/trigger 两张新表由 `Base.metadata.create_all` 建，无需迁移。
>
> **v3.49 补账（2026-08-12，捷昌整改批次；本版无 schema 迁移）**：
> - `api/sessions.py`：模糊匹配 `.like()` → `.ilike()`（PG 大小写敏感语义对齐 SQLite 旧行为）。
> - `api/sessions_maintenance.py`：备份链路 PG 化——dialect=postgresql 时走 **`pg_dump -Fc`** 出 `.dump`（子进程 + 临时文件原子落位），SQLite 保持文件级备份不变。
> - `api/projects.py`：`_channels_bound_to_other` **幽灵绑定 stale 忽略**——绑定判定前 `Project.id IN (...)` 查存活，已删项目的工位绑定不再算"被占用"（PG 外键暴露的历史脏数据路径：`detection_sessions_project_id_fkey` 违规拦激活）。
> - `api/source_session_lifecycle_mixin.py`：`settle_for_scan_pair` / `_ensure_cycle_for_scan_pair_settle` 加**显式 `prev_wp_id`/`prev_scanned_at`** 形参——WS3 新码先上屏后，结算范围用显式身份钳制（顶替后不能再从 `_inspecting_workpiece` 回读"上一件"），见 02 册 mes_hooks v3.49 条。
> - `services/export_context.py`：SQLite 特有 `func.strftime`/`func.date` 聚合改走 `sql_compat.hour_minute/date_str/sum_bool` 方言助手（见 05 册 sql_compat 条）。
> - `plugins-examples/sensor-clean/backend/hooks.py`：插件建表 DDL 方言化示范——`INTEGER PRIMARY KEY AUTOINCREMENT`（SQLite 专有）改方言安全 identity 写法，插件平台 PG 兼容样板。
> - `database.py` 生态补充：官方 PG 建库路径定论 **`create_all + apply_pending`**（alembic 仅做 CI 基线校验），决策记录在 `backend/db/migrations/__init__.py` 模块 docstring；短信离线队列/互连采样队列保持**本地 SQLite** 不随 DATABASE_URL 走（旁路解耦决策，见各文件头注释）。
>
> **v3.50 补账（2026-08-12，捷昌二期）**：
> - 迁移 **`m0010_scanner_lifecycle`**：`scanner_devices` 加 3 列——`resume_on VARCHAR(16) DEFAULT 'cycle_end'` / `rearm_forget_last BOOLEAN DEFAULT 0` / `strict_ok_dedup BOOLEAN DEFAULT 0`（PG 布尔默认值分道 FALSE；三列默认=现状零差异）。列语义与运行时行为见 02 册 v3.50 补账（scanner.py / mes_hooks.py）。
> - `pipeline_config` 新键 `tracking_settle_on_complete`（bool，默认缺省=关）+ `steps_config[]` 新键 `settle_confirm_frames`（int 默认 1）——JSON 配置扩展无 schema 迁移，config-dict 生成物已刷新。
>
> **v3.51 补账（2026-08-14，捷昌 B 站双工位整改；无 schema 迁移）**：
> - `api/projects.py`：激活收养开关 `activate.adopt_unbound`（SystemConfig KV，默认 '1'）+ `GET/PUT /projects/activate-config`——详见 02 册 v3.51 补账。
> - `api/sessions_maintenance.py`：clear/all、clear/range 联动 `_unlock_ok_workpieces` 解封 ok 工件回 registered——详见 02 册 v3.51 补账。
> - `channel_groups.plugin_data` 新用途：`unified_ok_report`（bool，工位组统一播报开关，v3.51 FEAT-009）——存 JSON 命名空间不动主 schema。
> - `pipeline_config` 新键（v3.50.0a 收编）：`tracking_scan_gate`（bool 默认关，「扫码后才计数」——码不在位不计数/不开周期/不进账本，仅工位扫码器先扫后检生效）。

---

## 一、逐文件档案

### 1.1 数据库基础设施

| 文件 | 行数 | 职责 |
|---|---:|---|
| `backend/db/database.py` | 83 | DSN 解析（`DATABASE_URL` 优先，否则 `settings.SQLALCHEMY_DATABASE_URI`）；SQLite 开 WAL + `busy_timeout=15s`（L49–65）；`SessionLocal` / `get_db()` / `Base` |

### 1.2 ORM 模型（`backend/models/`）

| 文件 | 行数 | 表数 | 职责 |
|---|---:|---:|---|
| `models.py` | 386 | 13 | 项目/模型/任务/相机/统计/系统 KV/检测 Session-Cycle-Step/工位组/录像/导出设置 |
| `auth_models.py` | 175 | 5 | 用户/角色/关联/登录 Token/M2M API Key |
| `export_models.py` | 330 | 4 | 导出模板/实时规则/运行日志/定时规则 |
| `plugin_models.py` | 69 | 4 | 插件安装元数据/运行状态/审计/配置版本 |
| `mes_models.py` | 964 | 20 | MES 工单工件缺陷扫码/外部对接/集群/外设/串行流水线/包装箱（v3.45 复核） |
| `weighing_models.py` | 33 | 1 | 称重投料逐件记录 |

> v3.38 变更（v3.41 复核）：`models.py` 的 `step_records.cycle_id`（L290）与 `video_clips.related_id`（L332）两列补 `index=True`——周期收尾/自动清理/数据页按周期号查步骤、清理按归属周期/步骤找录像，此前均为全表扫描热点（川南"框冻结"第三批优化）。
> 新库建表即带索引；老库由版本化迁移 `m0001_hot_path_indexes` 补建同名索引（`ix_step_records_cycle_id` / `ix_video_clips_related_id`，两边索引名一致，详见 05 号笔记第四节）。

### 1.3 数据 API（`/api/v1/data/*` 挂载）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `backend/api/sessions.py` | 1179 | 主路由：Session/Cycle/Step CRUD、视频、导出设置、CSV 导出入口；文件尾挂载 stats/maintenance 子路由（v3.41 复核；v3.54 变更见表下注） |
| `backend/api/sessions_export.py` | 584 | CSV/多格式导出 builder（session/cycle/range 三分支）；`build_csv_string` 供定时导出复用 |
| `backend/api/sessions_maintenance.py` | 1156 | 备份/清空/范围清理/自动清理/VACUUM/存储信息；`_perform_auto_cleanup` 被 `main.py` 启动调用（v3.41 复核；v3.54 变更见表下注） |
| `backend/api/sessions_stats.py` | 198 | 步骤/周期平均耗时统计 API |
| `backend/api/projects.py` | 759 | 项目 CRUD/激活/复制/插件数据 patch；激活时 `fire_plugin_hook("project_activated")`（L430 附近，v3.41 复核） |

> v3.32.1 变更：`projects.py` 激活防错位——抽出 `_channels_bound_to_other()`（L249）：单工位部署（channels 只有 1 个）不豁免任何旧绑定，多工位保持"各工位自绑项目优先"原语义；配置同步成功后把该通道的持久化绑定改指本项目（L305–315，仅旧绑定指向别的项目时才写）。动机：残留旧绑定会让重启后 `auto_load_active_project` 悄悄恢复另一个项目+模型，出现"项目页显示启用 A、工位实际跑 B"的持久错位。
> v3.35.1 变更：`sessions.py` 按日查询的班次过滤放开 day/night 硬编码（L443–445），支持自定义班次名（白班/午班/夜班…，配套萍乡自定义班次列表）。
> v3.48.1 变更：`sessions.py` 两处——① `get_session_cycles`（L629 附近）新增 `result=ok|ng` 查询参数按 `DetectionCycle.is_good` 过滤（数据中心「只看NG录像」）；② `convert_video_for_browser`（L881 附近）转码原子化：先写 `.tmp.mp4` 成功后 `os.replace` 落位、缓存名 `_h264` → `_h264v2`（历史坏缓存自然失效重转）、超时 60s→300s、失败半成品 finally 必清——治"转码超时留残缺缓存后该录像永远播放失败"。
> v3.54 变更：`sessions.py` 三处——① `_is_browser_compatible_h264`（模块级 probe 缓存 `_VIDEO_PROBE_CACHE`）：用 `ffmpeg -i` stderr 解析探测 h264+yuv420p，命中则 `convert_video_for_browser` **直出原文件免转码**（fMP4 收尾 remux 后的录像天然命中；老 mp4v 录像照旧转码，老测试 monkeypatch 该函数为 False 保持转码路径覆盖）；② 转码缓存目录改 `get_video_dirs()` 动态取（跟自定义录像根走）；③ 新端点 `GET /data/sessions/{id}/videos` 返回会话录像分段列表（v3.54 长会话按小时分段，见 01 册录制族注），按 start_time 排序含 `video_uuid/start_time/end_time/file_exists`。
> v3.54 变更：`sessions_maintenance.py` ——新增 `GET/PUT /data/storage/recording-dir`（自定义录像存储根目录，PUT 挂 `settings.edit` + `validate_recording_dir` 校验 + `refresh_cache`）；新增 `_all_video_scan_dirs()/_all_cache_dirs()` 聚合默认根+自定义根，清理/孤儿扫描/存储统计 6 处目录遍历全部改走聚合（自定义目录里的录像同样被自动清理管到）；`_ensure_dated_dir` 改在建段时实时调 `get_video_dirs()`。存储服务本体 `services/recording_storage.py` 见 05 册。
> v3.38 变更：`sessions_maintenance.py` 清理事务卫生（川南"框冻结"第二刀）——大事务握写锁秒级会堵推理线程写步骤/周期记录（撞 busy_timeout → 前端检测框冻结），三处整改：① `_delete_cycles_by_filter`（L129–178）按 `_CLEANUP_BATCH_CYCLES=200` 个周期/批分批删+逐批提交，录像文件删除（慢 I/O）移出事务、提交放锁后再动磁盘（进程崩溃留下的孤儿文件由孤儿扫描下一轮自愈）；② OK/NG 分开保留的录像清理逐结果提交、文件删除同样移出事务（L327–347）；③ 流水日志表（扫码/MES 通讯/外设日志）按主键 5000 行/批分批删（L392–406），整表条件删单事务会握锁数秒。

### 1.4 导出 API（`/api/v1/export/*`）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `backend/api/export_custom.py` | 548 | 字段树、模板 CRUD、预览/渲染、路线 B 模板文件上传 |
| `backend/api/export_realtime.py` | 385 | 实时规则 CRUD/toggle/test-run/日志；v3.38 起含旁路 SN 状态端点（v3.41 复核） |
| `backend/api/export_scheduled.py` | 299 | 定时规则 CRUD/cron 预览/默认输出目录 |

关联服务层（导出链路必读）：`services/export_context.py` · `export_field_registry.py` · `export_renderer*.py` · `export_realtime.py` · `export_scheduled.py` · `export_scheduled_writers.py` · `export_snapshot.py` · `export_seed.py` · `scanner_bypass_monitor.py`（v3.38 新增）

> v3.38 变更·扫码器旁路 SN 监控：客户扫码器不接软件、只往固定目录写 `SN.txt` 时，补齐"实时当前 SN"能力。
> - 新增 `services/scanner_bypass_monitor.py`（310 行）：后台守护线程默认 1s 轮询（`SystemConfig['export.scanner_bypass.poll_interval_sec']`，夹在 [0.2, 60]），配置源复用 `ExportRealtimeRule`（enabled + input_dir 非空，不新造表/字段），按 `channel_filter` 逐通道解析 input_dir、无通道过滤的规则作 default 兜底；每目录最新 txt 的 SN（文件名去扩展名）缓存在内存（`_STATE_LOCK` 保护整体替换 L40，`_poll_loop` L226，单目录异常只记状态、线程永不退出）。
> - `export_realtime.py` 新增只读端点 `GET /export/scanner-bypass/status`（L319–364）：读监控线程内存不触发目录扫描，可带 channel_id 取该通道当前条目；监控模块任何异常都返回 running=false + error、不 500。
> - `export_snapshot.snapshot_for_cycle_start` 改"内存优先"：先读监控缓存（`get_current_for_dir`，L136–149），监控无此目录/未预热/状态非 ok 再回退 glob 兜底（L150–162），满足"cycle_start 只读内存、不扫目录、不等文件稳定"的诉求；快照增补 serial_no/input_dir/locked_at/source/read_via 字段（L182–195），v3.7.2 起的 filename/text/mtime/snapshot_at/rule_ids 字段保持向后兼容（注释明示勿删）。

### 1.5 鉴权 API + 核心

| 文件 | 行数 | 职责 |
|---|---:|---|
| `backend/api/auth.py` | 344 | 登录/登出/启用鉴权/改密/权限目录；`set_current_active_user` 供检测写 operator_id |
| `backend/api/users.py` | 187 | 账号 CRUD（`system.users.manage`） |
| `backend/api/roles.py` | 137 | 角色 CRUD；内置三角色禁删 |
| `backend/api/api_keys.py` | 144 | M2M Key CRUD/toggle |
| `backend/core/auth_deps.py` | 332 | `get_current_user` / `require_perm` / `require_login`；auth 开关缓存 |
| `backend/core/auth.py` | — | token 生成/校验/落盘/内存缓存 |
| `backend/core/permissions.py` | — | 权限通配符匹配 + 内置角色种子 |
| `backend/core/api_key.py` | — | API Key 生成/sha256/校验 |

### 1.6 插件系统

| 文件 | 行数 | 职责 |
|---|---:|---|
| `backend/plugin_system/__init__.py` | 20 | `tianjun.plugin` logger 初始化 |
| `backend/plugin_system/manager.py` | 227 | `PluginManager.load_active`：版本校验 → import 后端 → `register_plugin` → 写 state/audit |
| `backend/plugin_system/registry.py` | 1179 | `PluginRegistry`（routes/hooks/tables）+ **`PluginHost` 全部主动/查询 API** |
| `backend/plugin_system/hook_dispatch.py` | 207 | `fire_plugin_hook` 统一入口 + returnable 白名单聚合 |
| `backend/plugin_system/verifier.py` | 314 | `.tjvplugin` 验签/解压/安装目录 |
| `backend/plugin_system/_plugin_common.py` | 424 | digest/RSA/HMAC/manifest 校验（CLI 与后端共用） |
| `backend/plugin_system/version_check.py` | — | `main_version_min/max` 兼容检查 |
| `backend/plugin_system/plugin_public_keys.py` | — | 内置公钥指纹表 |
| `backend/api/plugins.py` | 392 | 安装/激活/停用/卸载/前端 client-log/资源 manifest |

### 1.7 Session 写入热路径（非 API，检测运行时）

| 文件 | 关键方法 | 行号区间 |
|---|---|---|
| `backend/api/source_session_lifecycle_mixin.py` | `start_session` / `end_session` / `start_cycle` / `end_cycle` / `record_step` / `_reconcile_step_records` | L187–271 · L273–379 · L430–585 · L631–921 · L1062–1196 · L956–1060 |

---

## 二、全表清单（47 张）

> 格式：**表名** · ORM 类 · 主要字段 · 关系/备注

### 2.1 核心检测与项目（`models.py`）

| 表名 | ORM 类 | 字段概要 | 关系 |
|---|---|---|---|
| `projects` | `Project` | id, name, task_type, 7×JSON 配置, logic_mode, default_model_id, model_format, is_active, 时间戳 | → models/tasks/detection_sessions |
| `models` | `Model` | project_id, file_path, framework, labels, status | → project, conversions, tasks |
| `model_conversions` | `ModelConversion` | model_id+format+gpu_arch 唯一, file_path, status | → model |
| `tasks` | `Task` | project_id, model_id, input/result, is_good, duration | → project, model |
| `cameras` | `Camera` | source, camera_type, resolution, fps, status | 独立（旧式相机表） |
| `daily_stats` | `DailyStat` | date, project_id, good/bad/total, yield_rate | → project |
| `system_configs` | `SystemConfig` | key(unique), value, description | KV 全局配置（含 auth.enabled） |
| `detection_sessions` | `DetectionSession` | session_uuid, name, project_id, 统计字段, channel_id, shift_label, **operator_id→users**, order_id, video | → project, cycles |
| `detection_cycles` | `DetectionCycle` | cycle_uuid, session_id, 时间/结果, step_sequence, external_meta, channel_group_id, group_settled_with, group_settle_result, **operator_id→users** | → session, step_records |
| `channel_groups` | `ChannelGroup` | name(unique), member_channel_ids, settle_strategy, timeout_ms/action, plugin_data | RFC10 单机多通道联动 |
| `step_records` | `StepRecord` | record_uuid, cycle_id, step_label/name/order, 时间/置信度, **plugin_data** | → cycle |
| `video_clips` | `VideoClip` | video_uuid, clip_type, related_id, result(OK/NG), file_path | 录像索引 |
| `data_export_settings` | `DataExportSetting` | record_* / export_* / video_* 布尔开关 | 单行全局导出选项 |

### 2.2 鉴权（`auth_models.py`）

| 表名 | ORM 类 | 字段概要 | 关系 |
|---|---|---|---|
| `users` | `User` | username(unique), password_hash, display_name, active, must_change_password | ↔ roles via user_roles |
| `roles` | `Role` | code(unique), name, **permissions JSON**, is_builtin | ↔ users |
| `user_roles` | `UserRole` | user_id, role_id（唯一对） | → user, role |
| `session_tokens` | `SessionToken` | token(unique), user_id, expires_at, last_used_at | → user |
| `api_keys` | `APIKey` | key_prefix, key_hash(unique), name, **scope**, enabled, use_count | M2M，不绑 User |

### 2.3 自定义导出（`export_models.py`）

| 表名 | ORM 类 | 字段概要 | 关系 |
|---|---|---|---|
| `export_templates` | `ExportTemplate` | name, format, content, template_file_path, scope, is_system, builtin_id, default_rule_config | 中央模板仓库 |
| `export_realtime_rules` | `ExportRealtimeRule` | template_id, output_dir, filename_template, input_file_mode, trigger_event, filters, 去重/快照策略, 统计 | → template |
| `export_run_logs` | `ExportRunLog` | rule_id, template_id, source_type, cycle/session/box 关联, status, output_file | 导出台账 |
| `export_scheduled_rules` | `ExportScheduledRule` | cron, data_window_*, output_format, use_standard_daily_report, template_id | → template |

### 2.4 插件（`plugin_models.py`）

| 表名 | ORM 类 | 字段概要 |
|---|---|---|
| `plugins` | `PluginRecord` | customer_code(unique), tier, status, is_active, install_path, manifest_json, files_digest, 签名信息 |
| `plugin_state` | `PluginState` | runtime_status, health, hook_success/error_count, last_error_* |
| `plugin_audit_log` | `PluginAuditLog` | action, status, message |
| `plugin_config_versions` | `PluginConfigVersion` | plugin_version, config_digest, applied_at |

### 2.5 MES / 集群 / 流水线（`mes_models.py`，20 表）

| 表名 | ORM 类 | 一句话 |
|---|---|---|
| `work_orders` | `WorkOrder` | 工单全生命周期 + binding_scope |
| `batches` | `Batch` | 批次 → order |
| `workpieces` | `Workpiece` | 工件追溯核心 |
| `workpiece_inspections` | `WorkpieceInspection` | 工件↔cycle 中间表 |
| `defect_records` | `DefectRecord` | 缺陷记录 |
| `defect_codes` | `DefectCode` | 缺陷码字典 |
| `scanner_devices` | `ScannerDevice` | 扫码器配置（35+ 行为字段） |
| `scan_logs` | `ScanLog` | 扫码流水 |
| `mes_connections` | `MESConnection` | 外部 MES 连接 |
| `external_active_alarms` | `ExternalActiveAlarm` | 在途报警台账 |
| `mes_comm_logs` | `MESCommLog` | MES 通讯日志 |
| `cluster_config` | `ClusterConfig` | 集群单行配置 id=1 |
| `box_aggregations` | `BoxAggregation` | 副机上报聚合 |
| `external_devices` | `ExternalDevice` | 称重/PLC 等外设 |
| `external_device_logs` | `ExternalDeviceLog` | 外设数据流水 |
| `box_summaries` | `BoxSummary` | 箱子汇总结果 |
| `workpiece_flow_configs` | `WorkpieceFlowConfig` | RFC11 串行流水线配置 |
| `workpiece_flow_runs` | `WorkpieceFlowRun` | 单次工件流转 |
| `packaging_flow_configs` | `PackagingFlowConfig` | 包装箱结算配置；v3.35 增复合条码取段 5 字段（composite_*）+ 工单号识别 order_code_pattern + 尾箱"放工单=收尾动作" tail_paper_as_close_action，均默认关=存量零差异；v3.43 增缺工单判定二选一两列 tail_paper_scan_alarm（默认开=扫新单判定）/ tail_paper_timeout_s（默认0，>0 且 scan_alarm 关=时限判定，迁移 m0003）+ 已完成(OK)工单重扫拦截两列 block_completed_order_rescan / event_completed_order_rescan（默认关，迁移 m0002）；**v3.45** 增组⑧箱标签扫码授权 10 列 box_label_scan_required / label_qty_enabled / label_qty_segment（默认3）/ label_qty_pattern / label_rescan_action（默认'ignore'）/ unauthorized_cycle_action（默认'hold'）/ label_total_check + 事件映射 event_box_not_scanned / event_label_qty_missing / event_label_total_mismatch（全默认关零差异，迁移 m0004）+ 工单同步开关 sync_work_orders（**BOOLEAN DEFAULT 1 默认开**，老库 NULL 协调器侧视为开，迁移 m0005；开工/收尾/中止把包装工单镜像到 work_orders 表 source='packaging'，见 02 册协调器条目） |
| `packaging_flow_runs` | `PackagingFlowRun` | 包装运行记录 |

### 2.6 称重（`weighing_models.py`）

| 表名 | ORM 类 | 字段 |
|---|---|---|
| `weighing_records` | `WeighingRecord` | channel_id, product_sn, model_name, material, standard/initial/net, verdict, ts |

---

## 三、Session → Cycle → Step 写入链

### 3.1 总览（运行时主路径）

```
VideoSourceManager (source_session_lifecycle_mixin)
  │
  ├─ start_session (L187)     → INSERT detection_sessions
  ├─ start_cycle  (L430)      → INSERT detection_cycles + hook cycle_start
  ├─ record_step  (L1062)     → INSERT step_records（逐步）+ hook step_change
  ├─ _reconcile_step_records  → 结算前对齐 step 与 current_cycle_steps
  ├─ end_cycle    (L631)      → pre_cycle_end → UPDATE cycle → hook cycle_end → 协调器/MES/导出
  └─ end_session  (L273)      → UPDATE session 统计 → hook session_end → MES
```

Data 页 REST API（`sessions.py`）提供**只读/维护**能力；产线真实写入走 VSM mixin，不经 HTTP。

### 3.2 Session 创建（L187–271）

1. `DetectionSession`：`session_uuid`（8 hex）、`project_id`、`channel_id`、`shift_label`、`operator_id`←`get_current_user_id()`（L201–212）
2. `db.commit()` 后设内存态 `current_session_id` / `recording_enabled`
3. 副作用：加载 `DataExportSetting`、MES `on_session_start`、启动录像线程 + session 录像

### 3.3 Cycle 创建（L430–585）

1. `DetectionCycle`：`cycle_uuid`、`session_id`、`cycle_number`、`start_time`、`operator_id`（L430–500 区间）
2. `db.commit()` → MES `on_cycle_start` → **`fire_plugin_hook("cycle_start")`（L553）**
3. WorkpieceFlowCoordinator.on_cycle_started → 开始 cycle 录像

### 3.4 Step 写入（两条路径）

| 路径 | 触发 | 写入点 |
|---|---|---|
| **实时** | 步骤消失/完成 handler | `record_step`（L1062）：INSERT + 更新上一步 `interval_to_next` + **`step_change` hook（L1152）** |
| **结算对齐** | `end_cycle` 内 | `_reconcile_step_records`（L956）：keep/delete/create 策略，补缺失步骤 |

`record_step` 受 `export_settings.record_step_duration` 门控（L1074）；结算 reconcile 不受此门控。

### 3.5 Cycle 结束（L631–921，关键顺序）

1. 工位组 pending override 改写 `is_good`（L636–667）
2. **`pre_cycle_end` hook**（L679）→ 可 returnable 改写 `final_is_good` / reason
3. `_flush_active_steps_pt` → `stop_cycle_recording`
4. **UPDATE** `DetectionCycle`：end_time, duration, is_good, step_sequence, 录像 result 回写（L729–758）
5. `db.commit()`（L759）
6. MES `on_cycle_end`（L764）
7. **`registry.hooks.fire("cycle_end")`（L813）** — 注意此处直调 registry，非 `fire_plugin_hook`
8. ChannelGroup / WorkpieceFlow / PackagingFlow 协调器
9. 扫码器 resume、容器状态清理等

### 3.6 Session 结束（L273–379）

1. 丢弃未结算 cycle（`_discard_empty_cycle`）
2. 仅统计 `end_time != NULL` 的 cycle；删除 orphan cycle+step
3. `counters_snapshot` ← 内存 counters（L330）
4. **`session_end` hook（L347）** → MES `on_session_end`

### 3.7 operator_id 语义

- 列名保留 `operator_id`，FK 指向 `users.id`（`models.py` L172–173）
- 写入来源：`get_current_user_id()`（登录用户落盘），非旧 operators 表

---

## 四、导出全链路

### 4.1 三条消费线

```
                    ┌─────────────────────────────────────┐
                    │     ExportTemplate 中央仓库          │
                    │  (export_templates + export_seed)   │
                    └──────────┬──────────────────────────┘
                               │
     ┌─────────────────────────┼─────────────────────────┐
     ▼                         ▼                         ▼
 Data 页快捷按钮          实时规则                    定时规则
 sessions.py              export_realtime.py          export_scheduled.py
 GET /data/export/csv     trigger: cycle/session/    cron + data_window
 (L1030)                  box_complete                 → sessions_export builder
     │                         │                         │
     └──────── build_csv_string / render_to_file ───────┘
                               │
                    export_context.build_*_context
                    export_renderer (Jinja2 Sandboxed)
                    export_field_registry (308 字段)
                               │
                    ExportRunLog 台账 + 文件落盘
```

### 4.2 Data 页 CSV/多格式（即时下载）

| 环节 | 位置 | 说明 |
|---|---|---|
| HTTP 入口 | `sessions.py` L1031–1066 | `export_type=session/cycle/all` + 日期/班次/项目/工位过滤 |
| Builder | `sessions_export.py` L474–585 | `_load_export_opts` 读 `DataExportSetting`；三分支 writer |
| 多格式 | L564–577 | CSV 字符串 → `export_scheduled_writers.csv_string_to_format_bytes` |

### 4.3 自定义导出（模板驱动）

| 环节 | 位置 | 说明 |
|---|---|---|
| 字段树 | `export_custom.py` L67–79 | `GET /export/fields` → `export_field_registry` |
| 模板 CRUD | L139+ | `ExportTemplate`；`is_system=True` 不可删 |
| 预览/渲染 | `POST /export/preview` · `/render` | `build_cycle_context` / `build_range_context` + `render_string` |
| 路线 B | L55–57 | 上传 `.docx/.xlsx` 占位符模板 |

### 4.4 实时导出（事件触发）

| 环节 | 位置 | 说明 |
|---|---|---|
| 规则表 | `export_models.py` L88–178 | `ExportRealtimeRule` |
| 调度 | `services/export_realtime.py` L66+ | `dispatch_cycle_end_export` 等 |
| **触发点** | `mes_hooks._handle_cycle_end` 末尾 | cycle 结算后异步扫描 enabled 规则 |
| 输入文件策略 | `latest_file_strategy` | `cycle_start_snapshot` 读 `DetectionCycle.external_meta` |
| 旁路 SN 快照 | `services/export_snapshot.py` L136–162 | v3.38 起内存优先读旁路监控缓存，glob 兜底（快照 read_via 字段标记来源） |
| 日志 | `ExportRunLog` | rule_id + cycle_id + output_file |

### 4.5 定时导出（cron）

| 环节 | 位置 | 说明 |
|---|---|---|
| 规则表 | `export_models.py` L235–324 | `ExportScheduledRule` |
| 调度服务 | `services/export_scheduled.py` | APScheduler；`next_run_time` 索引 |
| 数据窗 | `data_window_type` | 复用 `sessions_export` 早晚班/跨日逻辑 |
| 标准报表 | `use_standard_daily_report=True` | 走 `build_csv_string`，与 Data 页四按钮同代码 |

### 4.6 导出设置与清理

- 读写：`sessions.py` L994–1026 · ORM `DataExportSetting`（单行）
- 自动清理导出台账：`sessions_maintenance.py` L413–456 · 按 `ExportRunLog` 精准删文件（v3.41 复核行号）

---

## 五、鉴权链路

### 5.1 身份模型（三种）

| 身份 | 条件 | 权限 |
|---|---|---|
| **SUPERUSER** | `auth.enabled=false`（出厂默认） | 全部放行，不读 Authorization |
| **匿名 operator** | auth 开 + 无 token + `allow_anonymous_operator=true` | 内置 operator 角色权限 |
| **登录用户** | Bearer token 有效 | 多角色 permissions 并集 |

> 开关存储：`system_configs.auth.enabled`（`auth_deps.py` L32–49）

### 5.2 请求链路

```
HTTP Request
  → get_current_user (auth_deps)
       ├─ auth 关 → CurrentUser(is_superuser=True)
       ├─ 有 Bearer → resolve_token_cached → User → 聚合 roles.permissions
       └─ 无 token → 匿名 operator 或 401（allow_anonymous=false）
  → require_perm("xxx")（可选）
       └─ match_permission(user.permissions, "xxx")  // 支持通配符 admin=*
  → 业务 handler
```

### 5.3 登录/session 持久化

| 步骤 | 文件 | 行号 |
|---|---|---|
| 登录 | `auth.py` L202–239 | 生成 token → 内存 cache → 可选落盘 `SessionToken` |
| 落盘开关 | `auth.session_persist` KV | 默认 true |
| 当前操作员 | `set_current_active_user` | 供 VSM 写 `operator_id` |
| 登出 | `auth.py` L242–252 | 删 token + 清空 active user |
| 改密 | L320–343 | 清该用户全部 token |

### 5.4 API Key（M2M）

| 项 | 说明 |
|---|---|
| 表 | `api_keys`（sha256，非 bcrypt） |
| scope | `*` / `cluster` / `mes.receive` / `license.cache` |
| 管理 API | `api_keys.py`，需 `system.apikey.manage` |
| 校验 | `core/api_key.py`，高频路径内存缓存 |

### 5.5 权限门控示例（本笔记范围）

| 端点 | 权限 key |
|---|---|
| `PUT /data/export-settings` | `data.export` |
| `DELETE /data/clear/*` | `data.cleanup` |
| `POST /plugins/install` | `system.plugin.manage` |
| `POST /auth/disable-auth` | `system.auth_toggle` |
| users/roles/api-keys 路由 | 整路由 `Depends(require_perm(...))` |

---

## 六、插件生命周期

### 6.1 状态机

```
[.tjvplugin 上传]
  → verify_package_to_temp (verifier.py)
  → copy_verified_to_install_dir → DATA_DIR/plugins/{customer_code}/
  → INSERT plugins + plugin_state=installed
  → POST /plugins/{cc}/activate → is_active=true, status=pending_restart
  → 重启 backend
  → load_active_plugin_on_startup (manager.py L213)
       ├─ check_main_version_compat
       ├─ import 插件包内 `backend/__init__.py`（非主仓库根目录）→ register_plugin(app, registry, license, host)
       ├─ mount routes / register hooks / create tables
       └─ plugin_state=loaded, audit startup_load success
  → 运行时 hooks.fire / PluginHost API
  → deactivate / delete (+ 可选 purge_data 清命名空间)
```

### 6.2 单 active 约束

- `PluginRecord.is_active`：全库至多一条 true（`plugins.py` L234–237）
- 停用/卸载不影响主程序启动（`main.py` try/except 包裹 `load_active`）

### 6.3 安装/激活 API（`plugins.py`）

| 端点 | 行号 | 行为 |
|---|---:|---|
| `POST /install` | L134 | 验签 → 写 DB → audit |
| `POST /{cc}/activate` | L230 | 切换 active，**需重启** |
| `POST /{cc}/deactivate` | L246 | is_active=false |
| `DELETE /{cc}` | L342 | 删目录+记录；`purge_data` 清 SystemConfig/Project.plugin_data |
| `GET /active/manifest` | L198 | 前端 bootstrap 探活（无插件也 200 `{}`） |
| `POST /client-log` | L103 | 前端加载诊断回传 |

### 6.4 卸载数据策略（L259–339）

- 清：`system_configs` 中 `plugin_{cc}_*`；Project 七 JSON 字段内 `plugin_data.{cc}`
- **不清**：`step_records.plugin_data`（历史业务数据）

---

## 七、10 Hook + PluginHost 真实清单

> RFC 09 M1 定义的 **10 个核心 hook** = 原有 4 + M1.1 新增 6。  
> 另有 RFC 10/11/12/v3.31 扩展 hook；下表分「核心 10」与「扩展」列出，**接入文件与行号均来自源码 grep/read**。

### 7.1 核心 10 Hook（RFC 09）

| # | hook_type | phase / when | 触发位置（文件:行） | returnable 字段 |
|---|---|---|---|---|
| 1 | `cycle_end` | post_cycle / post | `source_session_lifecycle_mixin.py:813`（直调 `registry.hooks.fire`） | 无（只读） |
| 2 | `pre_cycle_end` | pre_cycle / pre | `source_session_lifecycle_mixin.py:679` | `override_result`, `extra_counters` |
| 3 | `session_end` | post_session / post | `source_session_lifecycle_mixin.py:347` | 无 |
| 4 | `box_complete` | post_box / post | `cluster_collector.py:762` | 无 |
| 5 | `cycle_start` | post_cycle_start / post | `source_session_lifecycle_mixin.py:553` | 无 |
| 6 | `step_change` | post_step / post | `source_session_lifecycle_mixin.py:1152` | `warn_threshold_violated`, `warn_label` |
| 7 | `event_fire` | post_event / post | `source_event_trigger_mixin.py:344` | `suppress_alarm` |
| 8 | `scan_received` | post_scan / post | `scanner.py:1835` | 无 |
| 9 | `source_status_change` | post_status_change / post | `source_lifecycle_mixin.py:92` | 无 |
| 10 | `project_activated` | post_activate / post | `projects.py:430`（v3.41 复核行号） | 无 |

**统一入口**：除 #1 外均经 `fire_plugin_hook()`（`hook_dispatch.py:119`）；#1 历史原因直调 `plugin_manager.registry.hooks.fire`，语义等价。

**Returnable 白名单常量**：`hook_dispatch.py` L41–69 `RETURNABLE_HOOK_FIELDS`

### 7.2 扩展 Hook（已实现，非 RFC09 核心十）

| hook_type | 触发位置 |
|---|---|
| `step_tick` | `source_step_stats_mixin.py:674` |
| `detection_frame` | `source_inference_loop_mixin.py:318` |
| `external_device_data` | `external_device_pipeline.py:101` |
| `plugin_broadcast_received` | `registry.py:1091`（由 `broadcast_to_channel_group` 触发） |
| `channel_group_settle_start` | `channel_group_coordinator.py:286` |
| `channel_group_settle_done` | `channel_group_coordinator.py:366` |
| `workpiece_flow_enter` 等 5 个 | `workpiece_flow_coordinator.py:1023`（动态 hook_name） |

### 7.3 PluginHost 方法清单（`registry.py` L297–1178）

#### 查询 facade（只读，不需 capability）

| 方法 | 行号 | 返回 |
|---|---:|---|
| `get_db_session()` | L367 | ⚠️ 历史 API，新插件禁用 |
| `query_session(session_id)` | L382 | DetectionSession 快照 dict |
| `query_cycle(cycle_id)` | L417 | DetectionCycle 快照 |
| `query_step(step_id)` | L450 | StepRecord 快照 |
| `query_workpiece(workpiece_id)` | L484 | Workpiece 快照 |
| `read_system_config(key)` | L753 | SystemConfig.value |
| `list_channel_groups()` | L986 | 工位组列表 |
| `query_channel_group(group_id)` | L1012 | 单组配置 |
| `list_workpiece_flows()` | L1120 | 串行流水线列表 |
| `query_workpiece_flow_state(flow_id)` | L1146 | in-flight 状态 |

#### 主动 API（需 manifest.capabilities）

| 方法 | capability | 行号 |
|---|---|---:|
| `trigger_alarm(channel_id, event_type, reason)` | `runtime.alarm_trigger` | L598 |
| `trigger_event(channel_id, event_id, reason)` | `runtime.event_trigger` | L640 |
| `mes_push(event_type, payload, channel_id?)` | `runtime.mes_push` | L698 |
| `write_system_config(key, value, desc?)` | `runtime.system_config_write` | L786 |
| `write_plugin_step_field(step_record_id, key, value)` | `runtime.step_field_write` | L849 |
| `send_device_command(device_id, command)` | `runtime.device_command` | L941 |
| `broadcast_to_channel_group(group_id, message)` | `runtime.channel_group_broadcast` | L1038 |

**安全契约**（L341–349）：capabilities 门槛 · `plugin_{customer_code}_` 命名空间 · audit 落 `plugin_audit_log` · 异常 swallow 不拖垮主流程

#### 占位 / fail-fast registry（插件 register 时）

| registry | 状态 | 行号 |
|---|---|---:|
| `export_templates` | `_UnimplementedRegistry` → 抛 `PluginNotImplementedError` F7 | L272 |
| `export_fields` | 同上 F8 | L273 |
| `realtime_triggers` | 同上 F9 | L274 |

### 7.4 HooksRegistry 注册约定（`registry.py` L95–121）

```python
registry.hooks.register(
    hook_type="cycle_end",
    phase="post_cycle",
    when="post",      # "pre" | "post"
    priority=100,     # 越小越先执行；returnable 聚合时后者覆盖前者
    handler=callable,
)
```

---

## 八、交叉索引（改代码前先查）

| 我要改… | 先读 |
|---|---|
| Session/Cycle/Step 表结构 | `models.py` L136–320 |
| 产线写库时序 | `source_session_lifecycle_mixin.py` |
| Data 页 API | `sessions.py` + `sessions_export.py` |
| 导出模板/实时/定时 | `export_models.py` + `export_custom/realtime/scheduled.py` + `services/export_*` |
| 登录/权限 | `auth_deps.py` + `auth.py` + `permissions.py` |
| 插件 hook 接入 | `hook_dispatch.py` + 上表触发点 |
| 插件安装/激活 | `plugins.py` + `manager.py` + `verifier.py` |

---

*文档生成：读码笔记 03 · 数据+导出+鉴权+插件 · 2026-07-05；2026-07-17 v3.41 复核补账（v3.32.1 项目激活防错位 / v3.35.1 自定义班次过滤 / v3.38 清理事务卫生 + 热路径索引 + 旁路 SN 监控）*
