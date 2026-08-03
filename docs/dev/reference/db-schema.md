# 数据库表参考

> **类型**：reference（生成物勿手改）
> **生成命令**：`python scripts/docgen/gen_db_schema.py`（生成日 2026-08-03）
> **单一事实源**：SQLAlchemy ORM（Base.metadata）。字段含义看模型源文件行内注释；
> 迁移历史看 backend/db/migrations/ 与 backend/main.py 的 migrate_database。

共 **47** 张表。

## 表索引

| 表名 | ORM 类 | 定义文件 |
|---|---|---|
| [`api_keys`](#api_keys) | `APIKey` | `backend/models/auth_models.py` |
| [`batches`](#batches) | `Batch` | `backend/models/mes_models.py` |
| [`box_aggregations`](#box_aggregations) | `BoxAggregation` | `backend/models/mes_models.py` |
| [`box_summaries`](#box_summaries) | `BoxSummary` | `backend/models/mes_models.py` |
| [`cameras`](#cameras) | `Camera` | `backend/models/models.py` |
| [`channel_groups`](#channel_groups) | `ChannelGroup` | `backend/models/models.py` |
| [`cluster_config`](#cluster_config) | `ClusterConfig` | `backend/models/mes_models.py` |
| [`daily_stats`](#daily_stats) | `DailyStat` | `backend/models/models.py` |
| [`data_export_settings`](#data_export_settings) | `DataExportSetting` | `backend/models/models.py` |
| [`defect_codes`](#defect_codes) | `DefectCode` | `backend/models/mes_models.py` |
| [`defect_records`](#defect_records) | `DefectRecord` | `backend/models/mes_models.py` |
| [`detection_cycles`](#detection_cycles) | `DetectionCycle` | `backend/models/models.py` |
| [`detection_sessions`](#detection_sessions) | `DetectionSession` | `backend/models/models.py` |
| [`export_realtime_rules`](#export_realtime_rules) | `ExportRealtimeRule` | `backend/models/export_models.py` |
| [`export_run_logs`](#export_run_logs) | `ExportRunLog` | `backend/models/export_models.py` |
| [`export_scheduled_rules`](#export_scheduled_rules) | `ExportScheduledRule` | `backend/models/export_models.py` |
| [`export_templates`](#export_templates) | `ExportTemplate` | `backend/models/export_models.py` |
| [`external_active_alarms`](#external_active_alarms) | `ExternalActiveAlarm` | `backend/models/mes_models.py` |
| [`external_device_logs`](#external_device_logs) | `ExternalDeviceLog` | `backend/models/mes_models.py` |
| [`external_devices`](#external_devices) | `ExternalDevice` | `backend/models/mes_models.py` |
| [`mes_comm_logs`](#mes_comm_logs) | `MESCommLog` | `backend/models/mes_models.py` |
| [`mes_connections`](#mes_connections) | `MESConnection` | `backend/models/mes_models.py` |
| [`model_conversions`](#model_conversions) | `ModelConversion` | `backend/models/models.py` |
| [`models`](#models) | `Model` | `backend/models/models.py` |
| [`packaging_flow_configs`](#packaging_flow_configs) | `PackagingFlowConfig` | `backend/models/mes_models.py` |
| [`packaging_flow_runs`](#packaging_flow_runs) | `PackagingFlowRun` | `backend/models/mes_models.py` |
| [`plugin_audit_log`](#plugin_audit_log) | `PluginAuditLog` | `backend/models/plugin_models.py` |
| [`plugin_config_versions`](#plugin_config_versions) | `PluginConfigVersion` | `backend/models/plugin_models.py` |
| [`plugin_state`](#plugin_state) | `PluginState` | `backend/models/plugin_models.py` |
| [`plugins`](#plugins) | `PluginRecord` | `backend/models/plugin_models.py` |
| [`projects`](#projects) | `Project` | `backend/models/models.py` |
| [`roles`](#roles) | `Role` | `backend/models/auth_models.py` |
| [`scan_logs`](#scan_logs) | `ScanLog` | `backend/models/mes_models.py` |
| [`scanner_devices`](#scanner_devices) | `ScannerDevice` | `backend/models/mes_models.py` |
| [`session_tokens`](#session_tokens) | `SessionToken` | `backend/models/auth_models.py` |
| [`step_records`](#step_records) | `StepRecord` | `backend/models/models.py` |
| [`system_configs`](#system_configs) | `SystemConfig` | `backend/models/models.py` |
| [`tasks`](#tasks) | `Task` | `backend/models/models.py` |
| [`user_roles`](#user_roles) | `UserRole` | `backend/models/auth_models.py` |
| [`users`](#users) | `User` | `backend/models/auth_models.py` |
| [`video_clips`](#video_clips) | `VideoClip` | `backend/models/models.py` |
| [`weighing_records`](#weighing_records) | `WeighingRecord` | `backend/models/weighing_models.py` |
| [`work_orders`](#work_orders) | `WorkOrder` | `backend/models/mes_models.py` |
| [`workpiece_flow_configs`](#workpiece_flow_configs) | `WorkpieceFlowConfig` | `backend/models/mes_models.py` |
| [`workpiece_flow_runs`](#workpiece_flow_runs) | `WorkpieceFlowRun` | `backend/models/mes_models.py` |
| [`workpiece_inspections`](#workpiece_inspections) | `WorkpieceInspection` | `backend/models/mes_models.py` |
| [`workpieces`](#workpieces) | `Workpiece` | `backend/models/mes_models.py` |

## api_keys

ORM 类 `APIKey`，定义于 `backend/models/auth_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `key_prefix` | VARCHAR(16) | INDEX NOT NULL |  |
| `key_hash` | VARCHAR(64) | UNIQUE INDEX NOT NULL |  |
| `name` | VARCHAR(128) | NOT NULL |  |
| `scope` | VARCHAR(64) | NOT NULL | '*' |
| `description` | VARCHAR(255) |  |  |
| `enabled` | BOOLEAN | INDEX NOT NULL | True |
| `created_at` | DATETIME | NOT NULL | server |
| `last_used_at` | DATETIME |  |  |
| `use_count` | INTEGER | NOT NULL | 0 |
| `expires_at` | DATETIME | INDEX |  |

## batches

ORM 类 `Batch`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `batch_no` | VARCHAR(64) | INDEX NOT NULL |  |
| `order_id` | INTEGER | FK→work_orders.id NOT NULL |  |
| `material_lot` | VARCHAR(128) |  |  |
| `planned_qty` | INTEGER |  | 0 |
| `completed_qty` | INTEGER |  | 0 |
| `good_qty` | INTEGER |  | 0 |
| `ng_qty` | INTEGER |  | 0 |
| `status` | VARCHAR(20) |  | 'pending' |
| `created_at` | DATETIME |  | server |

## box_aggregations

ORM 类 `BoxAggregation`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `box_serial` | VARCHAR(128) | INDEX NOT NULL |  |
| `station_id` | VARCHAR(32) | NOT NULL |  |
| `source_address` | VARCHAR(128) |  |  |
| `channel_id` | INTEGER |  |  |
| `cycle_context` | JSON |  |  |
| `is_good` | BOOLEAN |  |  |
| `event_name` | VARCHAR(100) |  |  |
| `status` | VARCHAR(20) | NOT NULL | 'received' |
| `received_at` | DATETIME |  | server |

## box_summaries

ORM 类 `BoxSummary`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `box_serial` | VARCHAR(128) | UNIQUE INDEX NOT NULL |  |
| `total_stations` | INTEGER | NOT NULL | 0 |
| `completed_stations` | INTEGER | NOT NULL | 0 |
| `overall_result` | VARCHAR(10) |  |  |
| `aggregated_context` | JSON |  |  |
| `status` | VARCHAR(20) | NOT NULL | 'pending' |
| `pushed_at` | DATETIME |  |  |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## cameras

ORM 类 `Camera`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(200) | NOT NULL |  |
| `source` | VARCHAR(500) | NOT NULL |  |
| `camera_type` | VARCHAR(50) |  | 'usb' |
| `resolution_width` | INTEGER |  | 1280 |
| `resolution_height` | INTEGER |  | 720 |
| `fps` | INTEGER |  | 30 |
| `exposure` | FLOAT |  |  |
| `is_active` | BOOLEAN |  | False |
| `status` | VARCHAR(20) |  | 'offline' |
| `created_at` | DATETIME |  | server |

## channel_groups

ORM 类 `ChannelGroup`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(64) | UNIQUE NOT NULL |  |
| `member_channel_ids` | JSON | NOT NULL |  |
| `settle_strategy` | VARCHAR(32) | NOT NULL | 'synchronized_any_ng' |
| `timeout_ms` | INTEGER | NOT NULL | 5000 |
| `timeout_action` | VARCHAR(32) | NOT NULL | 'fallback_independent' |
| `enabled` | BOOLEAN |  | True |
| `plugin_data` | JSON |  |  |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## cluster_config

ORM 类 `ClusterConfig`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK | 1 |
| `role` | VARCHAR(20) | NOT NULL | 'standalone' |
| `master_url` | VARCHAR(256) |  |  |
| `station_id` | VARCHAR(32) | NOT NULL | 'A' |
| `expected_stations` | JSON |  |  |
| `sync_mode` | VARCHAR(20) | NOT NULL | 'wait_all' |
| `timeout_sec` | INTEGER | NOT NULL | 300 |
| `timeout_push` | BOOLEAN |  | False |
| `enabled` | BOOLEAN |  | False |
| `channel_station_map` | JSON |  |  |
| `station_result_strategy` | VARCHAR(20) |  | 'latest' |
| `updated_at` | DATETIME |  | server |

## daily_stats

ORM 类 `DailyStat`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `date` | VARCHAR(10) | INDEX NOT NULL |  |
| `project_id` | INTEGER | FK→projects.id |  |
| `good_count` | INTEGER |  | 0 |
| `bad_count` | INTEGER |  | 0 |
| `total_count` | INTEGER |  | 0 |
| `total_duration` | INTEGER |  | 0 |
| `yield_rate` | FLOAT |  | 0.0 |

## data_export_settings

ORM 类 `DataExportSetting`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `record_step_duration` | BOOLEAN |  | True |
| `record_step_interval` | BOOLEAN |  | True |
| `record_cycle_duration` | BOOLEAN |  | True |
| `record_cycle_interval` | BOOLEAN |  | True |
| `record_avg_step_time` | BOOLEAN |  | True |
| `record_avg_cycle_time` | BOOLEAN |  | True |
| `record_counters` | BOOLEAN |  | True |
| `record_step_video` | BOOLEAN |  | False |
| `record_cycle_video` | BOOLEAN |  | False |
| `record_session_video` | BOOLEAN |  | False |
| `video_quality` | VARCHAR(20) |  | 'medium' |
| `video_fps` | INTEGER |  | 30 |
| `export_step_duration` | BOOLEAN |  | True |
| `export_step_interval` | BOOLEAN |  | True |
| `export_step_event` | BOOLEAN |  | True |
| `export_cycle_duration` | BOOLEAN |  | True |
| `export_cycle_interval` | BOOLEAN |  | True |
| `export_cycle_result` | BOOLEAN |  | True |
| `export_counters` | BOOLEAN |  | True |
| `export_session_info` | BOOLEAN |  | True |
| `updated_at` | DATETIME |  | server |

## defect_codes

ORM 类 `DefectCode`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `code` | VARCHAR(32) | UNIQUE INDEX NOT NULL |  |
| `name` | VARCHAR(128) | NOT NULL |  |
| `category` | VARCHAR(32) | NOT NULL | 'other' |
| `severity` | VARCHAR(16) | NOT NULL | 'minor' |
| `project_id` | INTEGER | FK→projects.id |  |
| `detection_labels` | JSON |  |  |
| `description` | TEXT |  |  |
| `is_active` | BOOLEAN |  | True |
| `created_at` | DATETIME |  | server |

## defect_records

ORM 类 `DefectRecord`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `defect_uuid` | VARCHAR(32) | UNIQUE INDEX NOT NULL |  |
| `workpiece_id` | INTEGER | FK→workpieces.id INDEX NOT NULL |  |
| `inspection_id` | INTEGER | FK→workpiece_inspections.id |  |
| `cycle_id` | INTEGER | FK→detection_cycles.id |  |
| `step_record_id` | INTEGER | FK→step_records.id |  |
| `defect_code` | VARCHAR(32) | INDEX NOT NULL |  |
| `defect_name` | VARCHAR(128) | NOT NULL |  |
| `defect_category` | VARCHAR(32) | NOT NULL | 'other' |
| `severity` | VARCHAR(16) | NOT NULL | 'minor' |
| `confidence` | FLOAT |  |  |
| `detection_label` | VARCHAR(64) |  |  |
| `bbox_x` | FLOAT |  |  |
| `bbox_y` | FLOAT |  |  |
| `bbox_w` | FLOAT |  |  |
| `bbox_h` | FLOAT |  |  |
| `screenshot_path` | VARCHAR(512) |  |  |
| `description` | TEXT |  |  |
| `source` | VARCHAR(20) |  | 'auto' |
| `created_at` | DATETIME |  | server |

## detection_cycles

ORM 类 `DetectionCycle`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `cycle_uuid` | VARCHAR(50) | UNIQUE INDEX NOT NULL |  |
| `session_id` | INTEGER | FK→detection_sessions.id |  |
| `cycle_number` | INTEGER |  | 1 |
| `start_time` | DATETIME | NOT NULL |  |
| `end_time` | DATETIME |  |  |
| `duration` | FLOAT |  |  |
| `interval_to_next` | FLOAT |  |  |
| `is_good` | BOOLEAN |  | True |
| `event_id` | INTEGER |  |  |
| `event_name` | VARCHAR(100) |  |  |
| `result_reason` | TEXT |  |  |
| `step_sequence` | JSON |  |  |
| `video_path` | VARCHAR(500) |  |  |
| `video_id` | VARCHAR(50) |  |  |
| `operator_id` | INTEGER | FK→users.id |  |
| `order_id` | INTEGER | INDEX |  |
| `external_meta` | JSON |  |  |
| `channel_group_id` | INTEGER | FK→channel_groups.id INDEX |  |
| `group_settled_with` | JSON |  |  |
| `group_settle_result` | VARCHAR(8) |  |  |

## detection_sessions

ORM 类 `DetectionSession`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `session_uuid` | VARCHAR(50) | UNIQUE INDEX NOT NULL |  |
| `name` | VARCHAR(64) | INDEX |  |
| `project_id` | INTEGER | FK→projects.id |  |
| `start_time` | DATETIME | NOT NULL |  |
| `end_time` | DATETIME |  |  |
| `total_cycles` | INTEGER |  | 0 |
| `good_cycles` | INTEGER |  | 0 |
| `ng_cycles` | INTEGER |  | 0 |
| `counters_snapshot` | JSON |  |  |
| `avg_cycle_time` | FLOAT |  | 0 |
| `min_cycle_time` | FLOAT |  |  |
| `max_cycle_time` | FLOAT |  |  |
| `video_path` | VARCHAR(500) |  |  |
| `video_id` | VARCHAR(50) |  |  |
| `status` | VARCHAR(20) |  | 'running' |
| `channel_id` | INTEGER | INDEX | 0 |
| `shift_label` | VARCHAR(20) | INDEX |  |
| `operator_id` | INTEGER | FK→users.id |  |
| `order_id` | INTEGER | INDEX |  |

## export_realtime_rules

ORM 类 `ExportRealtimeRule`，定义于 `backend/models/export_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(128) | NOT NULL |  |
| `enabled` | BOOLEAN | INDEX NOT NULL | True |
| `description` | TEXT |  |  |
| `template_id` | INTEGER | FK→export_templates.id INDEX NOT NULL |  |
| `output_dir` | VARCHAR(500) | NOT NULL |  |
| `filename_template` | VARCHAR(256) | NOT NULL | '{{ cycle.id }}.txt' |
| `input_file_mode` | VARCHAR(16) | NOT NULL | 'none' |
| `input_dir` | VARCHAR(500) |  |  |
| `trigger_event` | VARCHAR(32) | INDEX NOT NULL | 'cycle_end' |
| `channel_filter` | JSON |  |  |
| `project_filter` | JSON |  |  |
| `overwrite_policy` | VARCHAR(16) | NOT NULL | 'overwrite' |
| `encoding` | VARCHAR(16) | NOT NULL | 'utf-8' |
| `newline` | VARCHAR(8) | NOT NULL | 'lf' |
| `latest_file_strategy` | VARCHAR(32) | NOT NULL | 'cycle_start_snapshot' |
| `latest_file_wait_stable_ms` | INTEGER | NOT NULL | 100 |
| `latest_file_max_age_sec` | INTEGER | NOT NULL | 0 |
| `dedupe_same_filename` | BOOLEAN | NOT NULL | False |
| `dedupe_retry_max_sec` | INTEGER | NOT NULL | 5 |
| `dedupe_retry_interval_ms` | INTEGER | NOT NULL | 100 |
| `last_used_input_filename` | VARCHAR(256) |  |  |
| `last_run_time` | DATETIME |  |  |
| `last_run_status` | VARCHAR(16) |  |  |
| `last_run_error` | TEXT |  |  |
| `last_output_file` | VARCHAR(500) |  |  |
| `success_count` | INTEGER | NOT NULL | 0 |
| `failed_count` | INTEGER | NOT NULL | 0 |
| `skipped_count` | INTEGER | NOT NULL | 0 |
| `created_at` | DATETIME | NOT NULL | server |
| `updated_at` | DATETIME | NOT NULL | server |

## export_run_logs

ORM 类 `ExportRunLog`，定义于 `backend/models/export_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `rule_id` | INTEGER | FK→export_realtime_rules.id INDEX |  |
| `template_id` | INTEGER | FK→export_templates.id INDEX |  |
| `source_type` | VARCHAR(16) | INDEX NOT NULL | 'realtime' |
| `cycle_id` | INTEGER | INDEX |  |
| `session_id` | INTEGER | INDEX |  |
| `box_serial` | VARCHAR(128) | INDEX |  |
| `triggered_at` | DATETIME | INDEX NOT NULL | server |
| `status` | VARCHAR(16) | INDEX NOT NULL | 'success' |
| `output_file` | VARCHAR(500) |  |  |
| `file_size` | INTEGER |  |  |
| `duration_ms` | INTEGER |  |  |
| `error_msg` | TEXT |  |  |
| `skip_reason` | VARCHAR(64) |  |  |

## export_scheduled_rules

ORM 类 `ExportScheduledRule`，定义于 `backend/models/export_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(128) | NOT NULL |  |
| `enabled` | BOOLEAN | INDEX NOT NULL | True |
| `description` | TEXT |  |  |
| `cron_expression` | VARCHAR(64) | NOT NULL | '0 0 * * *' |
| `data_window_type` | VARCHAR(32) | NOT NULL | 'yesterday' |
| `data_window_config` | JSON |  |  |
| `project_id` | INTEGER | INDEX |  |
| `channel_id` | INTEGER | INDEX |  |
| `output_format` | VARCHAR(16) | NOT NULL | 'csv' |
| `output_dir` | VARCHAR(500) |  |  |
| `filename_template` | VARCHAR(256) | NOT NULL | '{rule_name}_{date}.{format}' |
| `use_standard_daily_report` | BOOLEAN | NOT NULL | True |
| `template_id` | INTEGER | FK→export_templates.id INDEX |  |
| `encoding` | VARCHAR(16) | NOT NULL | 'utf-8-sig' |
| `newline` | VARCHAR(8) | NOT NULL | 'lf' |
| `overwrite_policy` | VARCHAR(16) | NOT NULL | 'overwrite' |
| `pt_mode` | VARCHAR(16) |  |  |
| `ct_mode` | VARCHAR(16) |  |  |
| `last_run_time` | DATETIME |  |  |
| `last_run_status` | VARCHAR(16) |  |  |
| `last_run_error` | TEXT |  |  |
| `last_output_file` | VARCHAR(500) |  |  |
| `next_run_time` | DATETIME | INDEX |  |
| `success_count` | INTEGER | NOT NULL | 0 |
| `failed_count` | INTEGER | NOT NULL | 0 |
| `skipped_count` | INTEGER | NOT NULL | 0 |
| `created_at` | DATETIME | NOT NULL | server |
| `updated_at` | DATETIME | NOT NULL | server |

## export_templates

ORM 类 `ExportTemplate`，定义于 `backend/models/export_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(128) | INDEX NOT NULL |  |
| `description` | TEXT |  |  |
| `format` | VARCHAR(16) | INDEX NOT NULL | 'txt' |
| `content` | TEXT | NOT NULL | '' |
| `template_file_path` | VARCHAR(500) |  |  |
| `scope` | VARCHAR(16) | NOT NULL | 'both' |
| `is_system` | BOOLEAN | INDEX NOT NULL | False |
| `builtin_id` | VARCHAR(64) | UNIQUE INDEX |  |
| `default_rule_config` | JSON |  |  |
| `created_at` | DATETIME | NOT NULL | server |
| `updated_at` | DATETIME | NOT NULL | server |

## external_active_alarms

ORM 类 `ExternalActiveAlarm`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `task_no` | VARCHAR(128) | INDEX |  |
| `product_code` | VARCHAR(64) |  |  |
| `step_code` | VARCHAR(64) |  |  |
| `operator` | VARCHAR(64) |  |  |
| `warning_text` | TEXT |  |  |
| `channel_id` | INTEGER |  |  |
| `event_type` | VARCHAR(64) |  |  |
| `status` | VARCHAR(16) | INDEX NOT NULL | 'active' |
| `clear_source` | VARCHAR(20) |  |  |
| `raised_at` | DATETIME |  | server |
| `cleared_at` | DATETIME |  |  |
| `extra_data` | JSON |  |  |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## external_device_logs

ORM 类 `ExternalDeviceLog`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `device_id` | INTEGER | FK→external_devices.id |  |
| `raw_data` | VARCHAR(1024) |  |  |
| `parsed_data` | JSON |  |  |
| `box_serial` | VARCHAR(128) |  |  |
| `is_valid` | BOOLEAN |  | True |
| `error_msg` | VARCHAR(256) |  |  |
| `created_at` | DATETIME | INDEX | server |

## external_devices

ORM 类 `ExternalDevice`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(64) | NOT NULL |  |
| `device_role` | VARCHAR(20) | NOT NULL | 'weight' |
| `protocol` | VARCHAR(20) | NOT NULL | 'tcp' |
| `ip` | VARCHAR(45) |  |  |
| `port` | INTEGER |  |  |
| `serial_port` | VARCHAR(32) |  |  |
| `serial_baud` | INTEGER |  | 9600 |
| `protocol_config` | JSON |  |  |
| `parse_mode` | VARCHAR(20) | NOT NULL | 'direct' |
| `parse_config` | JSON |  |  |
| `station_id` | VARCHAR(32) |  |  |
| `channel_id` | INTEGER |  |  |
| `pairing_group` | VARCHAR(32) |  |  |
| `data_target` | VARCHAR(20) | NOT NULL | 'cluster' |
| `validation_rules` | JSON |  |  |
| `enabled` | BOOLEAN |  | True |
| `stable_enabled` | BOOLEAN |  | True |
| `stable_delta` | FLOAT |  | 0.05 |
| `stable_count` | INTEGER |  | 5 |
| `zero_threshold` | FLOAT |  | 0.05 |
| `weight_no_barcode_alarm_enabled` | BOOLEAN |  | False |
| `weight_no_barcode_alarm_delay_sec` | INTEGER |  | 10 |
| `pairing_mode` | VARCHAR(16) |  | 'stable' |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## mes_comm_logs

ORM 类 `MESCommLog`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `connection_id` | INTEGER | FK→mes_connections.id |  |
| `direction` | VARCHAR(10) | NOT NULL |  |
| `event_type` | VARCHAR(32) |  |  |
| `method` | VARCHAR(10) |  |  |
| `url` | VARCHAR(512) |  |  |
| `request_body` | TEXT |  |  |
| `response_body` | TEXT |  |  |
| `status_code` | INTEGER |  |  |
| `success` | BOOLEAN | NOT NULL | True |
| `error_msg` | TEXT |  |  |
| `duration_ms` | INTEGER |  |  |
| `created_at` | DATETIME | INDEX | server |

## mes_connections

ORM 类 `MESConnection`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(64) | NOT NULL |  |
| `adapter_type` | VARCHAR(32) | NOT NULL | 'rest' |
| `enabled` | BOOLEAN |  | False |
| `config` | JSON |  |  |
| `push_events` | JSON |  |  |
| `pull_enabled` | BOOLEAN |  | False |
| `pull_interval_sec` | INTEGER |  | 60 |
| `retry_count` | INTEGER |  | 3 |
| `retry_interval_sec` | INTEGER |  | 5 |
| `extra_fields_schema` | JSON |  |  |
| `bound_channels` | JSON |  |  |
| `last_sync_at` | DATETIME |  |  |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## model_conversions

ORM 类 `ModelConversion`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `model_id` | INTEGER | FK→models.id NOT NULL |  |
| `format` | VARCHAR(50) | NOT NULL |  |
| `file_path` | VARCHAR(500) | NOT NULL |  |
| `file_size` | INTEGER |  | 0 |
| `gpu_name` | VARCHAR(200) |  |  |
| `gpu_arch` | VARCHAR(50) |  |  |
| `status` | VARCHAR(20) |  | 'queued' |
| `error_msg` | TEXT |  |  |
| `created_at` | DATETIME |  | server |

## models

ORM 类 `Model`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `project_id` | INTEGER | FK→projects.id |  |
| `name` | VARCHAR(200) | INDEX NOT NULL |  |
| `file_path` | VARCHAR(500) | NOT NULL |  |
| `file_name` | VARCHAR(200) | NOT NULL |  |
| `file_size` | INTEGER |  | 0 |
| `framework` | VARCHAR(50) |  | 'PyTorch' |
| `labels` | JSON |  |  |
| `description` | TEXT |  |  |
| `version` | VARCHAR(50) |  |  |
| `status` | VARCHAR(20) |  | 'idle' |
| `upload_time` | DATETIME |  | server |

## packaging_flow_configs

ORM 类 `PackagingFlowConfig`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(64) | UNIQUE NOT NULL |  |
| `enabled` | BOOLEAN |  | False |
| `channel_id` | INTEGER |  | 0 |
| `scan_device_id` | INTEGER | FK→scanner_devices.id |  |
| `pull_conn_id` | INTEGER |  |  |
| `box_count_source` | VARCHAR(16) |  | 'field' |
| `box_count_field` | VARCHAR(64) |  | 'dispatch_qty' |
| `tray_qty_mode` | VARCHAR(16) |  | 'fixed' |
| `tray_qty_fixed` | INTEGER |  | 0 |
| `tray_qty_table` | JSON |  |  |
| `trays_per_box_mode` | VARCHAR(16) |  | 'fixed' |
| `trays_per_box_fixed` | INTEGER |  | 4 |
| `trays_per_box_table` | JSON |  |  |
| `label_match` | VARCHAR(16) |  | 'strip_hyphen' |
| `label_len` | INTEGER |  | 0 |
| `hyphen_template` | VARCHAR(32) |  |  |
| `hyphen_pos` | INTEGER |  | 0 |
| `composite_label_enabled` | BOOLEAN |  | False |
| `composite_delimiter` | VARCHAR(8) |  | '|' |
| `composite_pick_mode` | VARCHAR(8) |  | 'prefix' |
| `composite_prefix` | VARCHAR(32) |  |  |
| `composite_index` | INTEGER |  | 1 |
| `order_code_pattern` | VARCHAR(128) |  |  |
| `on_mes_fail` | VARCHAR(16) |  | 'block' |
| `on_label_mismatch` | VARCHAR(16) |  | 'warn' |
| `on_short_box` | VARCHAR(16) |  | 'redo' |
| `on_forced_stop_partial` | VARCHAR(8) |  | 'fail' |
| `on_forced_stop` | VARCHAR(8) |  | 'settle' |
| `forced_settle_on_standby` | BOOLEAN |  | True |
| `push_on_complete` | BOOLEAN |  | False |
| `push_event_type` | VARCHAR(32) |  | 'packaging_complete' |
| `event_short_box` | INTEGER |  |  |
| `event_over_box` | INTEGER |  |  |
| `event_tray_ng` | INTEGER |  |  |
| `event_box_ng` | INTEGER |  |  |
| `event_label_mismatch` | INTEGER |  |  |
| `event_label_len` | INTEGER |  |  |
| `event_mes_fail` | INTEGER |  |  |
| `count_unit` | VARCHAR(8) |  | 'trays' |
| `items_per_box_source` | VARCHAR(8) |  | 'project' |
| `items_per_box_fixed` | INTEGER |  | 0 |
| `slider_total_field` | VARCHAR(64) |  | 'dispatch_qty' |
| `auto_switch_project` | BOOLEAN |  | False |
| `spec_to_project` | JSON |  |  |
| `match_project_by_name` | BOOLEAN |  | False |
| `name_match_strict_boundary` | BOOLEAN |  | False |
| `tail_paper_order_required` | BOOLEAN |  | False |
| `tail_paper_step_label` | VARCHAR(64) |  |  |
| `tail_paper_as_close_action` | BOOLEAN |  | False |
| `event_missing_paper` | INTEGER |  |  |
| `tail_paper_scan_alarm` | BOOLEAN |  | True |
| `tail_paper_timeout_s` | INTEGER |  | 0 |
| `oil_nozzle_required` | BOOLEAN |  | False |
| `oil_nozzle_step_label` | VARCHAR(64) |  |  |
| `event_missing_nozzle` | INTEGER |  |  |
| `block_completed_order_rescan` | BOOLEAN |  | False |
| `event_completed_order_rescan` | INTEGER |  |  |
| `sync_work_orders` | BOOLEAN |  | True |
| `box_label_scan_required` | BOOLEAN |  | False |
| `label_qty_enabled` | BOOLEAN |  | False |
| `label_qty_segment` | INTEGER |  | 3 |
| `label_qty_pattern` | VARCHAR(128) |  |  |
| `label_rescan_action` | VARCHAR(8) |  | 'ignore' |
| `unauthorized_cycle_action` | VARCHAR(8) |  | 'hold' |
| `label_total_check` | BOOLEAN |  | False |
| `event_box_not_scanned` | INTEGER |  |  |
| `event_label_qty_missing` | INTEGER |  |  |
| `event_label_total_mismatch` | INTEGER |  |  |
| `plugin_data` | JSON |  |  |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## packaging_flow_runs

ORM 类 `PackagingFlowRun`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `flow_config_id` | INTEGER | FK→packaging_flow_configs.id INDEX NOT NULL |  |
| `run_uuid` | VARCHAR(32) | UNIQUE INDEX NOT NULL |  |
| `order_no` | VARCHAR(128) | INDEX |  |
| `spec` | VARCHAR(256) |  |  |
| `box_total` | INTEGER |  | 0 |
| `box_done` | INTEGER |  | 0 |
| `box_ng` | INTEGER |  | 0 |
| `status` | VARCHAR(16) | NOT NULL | 'order_loaded' |
| `current_box_index` | INTEGER |  | 0 |
| `current_box_trays` | INTEGER |  | 0 |
| `count_unit` | VARCHAR(8) |  | 'trays' |
| `slider_total` | INTEGER |  | 0 |
| `items_per_box` | INTEGER |  | 0 |
| `tail_target` | INTEGER |  | 0 |
| `current_box_sliders` | INTEGER |  | 0 |
| `paper_order_done` | BOOLEAN |  | False |
| `box_details` | JSON |  |  |
| `final_result` | VARCHAR(8) |  |  |
| `mes_pushed` | BOOLEAN |  | False |
| `forced_reason` | VARCHAR(512) |  |  |
| `forced_by` | VARCHAR(64) |  |  |
| `started_at` | DATETIME |  | server |
| `completed_at` | DATETIME |  |  |

## plugin_audit_log

ORM 类 `PluginAuditLog`，定义于 `backend/models/plugin_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `customer_code` | VARCHAR(32) | INDEX |  |
| `action` | VARCHAR(64) | NOT NULL |  |
| `status` | VARCHAR(32) | NOT NULL |  |
| `message` | TEXT |  |  |
| `created_at` | DATETIME |  | server |

## plugin_config_versions

ORM 类 `PluginConfigVersion`，定义于 `backend/models/plugin_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `customer_code` | VARCHAR(32) | INDEX NOT NULL |  |
| `plugin_version` | VARCHAR(64) | NOT NULL |  |
| `config_digest` | VARCHAR(80) |  |  |
| `applied_at` | DATETIME |  | server |

## plugin_state

ORM 类 `PluginState`，定义于 `backend/models/plugin_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `customer_code` | VARCHAR(32) | UNIQUE INDEX NOT NULL |  |
| `runtime_status` | VARCHAR(32) | NOT NULL | 'stopped' |
| `health` | VARCHAR(32) | NOT NULL | 'unknown' |
| `last_error_code` | VARCHAR(64) |  |  |
| `last_error_message` | TEXT |  |  |
| `last_loaded_at` | DATETIME |  |  |
| `hook_success_count` | INTEGER | NOT NULL | 0 |
| `hook_error_count` | INTEGER | NOT NULL | 0 |
| `updated_at` | DATETIME |  | server |

## plugins

ORM 类 `PluginRecord`，定义于 `backend/models/plugin_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `customer_code` | VARCHAR(32) | UNIQUE INDEX NOT NULL |  |
| `name` | VARCHAR(128) | NOT NULL |  |
| `plugin_version` | VARCHAR(64) | NOT NULL |  |
| `tier` | INTEGER | NOT NULL | 1 |
| `status` | VARCHAR(32) | NOT NULL | 'installed' |
| `is_active` | BOOLEAN | NOT NULL | False |
| `install_path` | VARCHAR(500) | NOT NULL |  |
| `manifest_json` | TEXT | NOT NULL |  |
| `files_digest` | VARCHAR(80) | NOT NULL |  |
| `signed_by` | VARCHAR(64) |  |  |
| `signed_at` | VARCHAR(64) |  |  |
| `public_key_fingerprint` | VARCHAR(64) |  |  |
| `installed_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## projects

ORM 类 `Project`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(200) | INDEX NOT NULL |  |
| `task_type` | VARCHAR(50) |  | 'detection' |
| `pipeline_config` | JSON |  |  |
| `default_model_id` | INTEGER | FK→models.id |  |
| `logic_mode` | VARCHAR(50) |  | 'sequential' |
| `steps_config` | JSON |  |  |
| `events_config` | JSON |  |  |
| `counters_config` | JSON |  |  |
| `alarm_config` | JSON |  |  |
| `detection_config` | JSON |  |  |
| `data_config` | JSON |  |  |
| `model_format` | VARCHAR(50) |  | 'pytorch_fp32' |
| `is_active` | BOOLEAN |  | False |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## roles

ORM 类 `Role`，定义于 `backend/models/auth_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `code` | VARCHAR(32) | UNIQUE INDEX NOT NULL |  |
| `name` | VARCHAR(64) | NOT NULL |  |
| `description` | VARCHAR(255) |  |  |
| `permissions` | JSON | NOT NULL | list() |
| `is_builtin` | BOOLEAN | NOT NULL | False |
| `created_at` | DATETIME | NOT NULL | server |
| `updated_at` | DATETIME | NOT NULL | server |

## scan_logs

ORM 类 `ScanLog`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `device_id` | INTEGER | FK→scanner_devices.id |  |
| `channel_id` | INTEGER |  |  |
| `raw_data` | VARCHAR(512) | NOT NULL |  |
| `parsed_serial` | VARCHAR(128) |  |  |
| `parsed_order` | VARCHAR(64) |  |  |
| `parsed_batch` | VARCHAR(64) |  |  |
| `workpiece_id` | INTEGER | FK→workpieces.id |  |
| `success` | BOOLEAN | NOT NULL | True |
| `error_msg` | VARCHAR(256) |  |  |
| `created_at` | DATETIME | INDEX | server |

## scanner_devices

ORM 类 `ScannerDevice`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(64) | NOT NULL |  |
| `ip` | VARCHAR(45) | NOT NULL |  |
| `port` | INTEGER | NOT NULL | 55256 |
| `channel_id` | INTEGER |  |  |
| `enabled` | BOOLEAN |  | True |
| `parse_mode` | VARCHAR(20) |  | 'direct' |
| `parse_config` | JSON |  |  |
| `dedup_interval_sec` | INTEGER |  | 2 |
| `auto_create_workpiece` | BOOLEAN |  | True |
| `auto_link_order` | BOOLEAN |  | True |
| `scan_required` | BOOLEAN |  | False |
| `duplicate_scan_action` | VARCHAR(20) |  | 'overwrite' |
| `warn_no_barcode` | BOOLEAN |  | False |
| `rebind_mode` | VARCHAR(20) |  | 'rescan' |
| `bind_timing` | VARCHAR(20) |  | 'mid_cycle' |
| `broadcast_channels` | JSON |  |  |
| `device_type` | VARCHAR(20) |  | 'text_lon' |
| `external_only` | BOOLEAN |  | False |
| `pairing_group` | VARCHAR(32) |  |  |
| `ok_rescan_cooldown_sec` | INTEGER |  | 0 |
| `late_scan_bind_window_sec` | INTEGER |  | 3 |
| `scan_mode` | VARCHAR(32) |  | 'continuous' |
| `throttle_idle_ms` | INTEGER |  | 500 |
| `broadcast_settle_mode` | VARCHAR(20) |  | 'independent' |
| `primary_settle_channel` | INTEGER |  |  |
| `primary_settle_min_items` | INTEGER |  | 1 |
| `scan_pair_max_wait_sec` | INTEGER |  | 0 |
| `scan_d_geometry` | VARCHAR(8) |  | 'line' |
| `scan_d_line` | JSON |  |  |
| `scan_d_zone` | JSON |  |  |
| `scan_d_gone_confirm_frames` | INTEGER |  | 30 |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## session_tokens

ORM 类 `SessionToken`，定义于 `backend/models/auth_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `token` | VARCHAR(64) | UNIQUE INDEX NOT NULL |  |
| `user_id` | INTEGER | FK→users.id INDEX NOT NULL |  |
| `created_at` | DATETIME | NOT NULL | server |
| `expires_at` | DATETIME | INDEX |  |
| `last_used_at` | DATETIME | NOT NULL | server |

## step_records

ORM 类 `StepRecord`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `record_uuid` | VARCHAR(50) | UNIQUE INDEX NOT NULL |  |
| `cycle_id` | INTEGER | FK→detection_cycles.id INDEX |  |
| `step_id` | VARCHAR(50) |  |  |
| `step_label` | VARCHAR(100) | NOT NULL |  |
| `step_name` | VARCHAR(100) |  |  |
| `step_order` | INTEGER |  | 0 |
| `start_time` | DATETIME | NOT NULL |  |
| `end_time` | DATETIME |  |  |
| `duration` | FLOAT |  |  |
| `interval_from_prev` | FLOAT |  |  |
| `interval_to_next` | FLOAT |  |  |
| `confidence` | FLOAT |  |  |
| `is_valid` | BOOLEAN |  | True |
| `screenshot_path` | VARCHAR(500) |  |  |
| `video_path` | VARCHAR(500) |  |  |
| `video_id` | VARCHAR(50) |  |  |
| `plugin_data` | JSON |  |  |

## system_configs

ORM 类 `SystemConfig`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `key` | VARCHAR(100) | UNIQUE NOT NULL |  |
| `value` | TEXT |  |  |
| `description` | VARCHAR(500) |  |  |

## tasks

ORM 类 `Task`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `project_id` | INTEGER | FK→projects.id |  |
| `model_id` | INTEGER | FK→models.id |  |
| `input_file` | VARCHAR(500) |  |  |
| `result_file` | VARCHAR(500) |  |  |
| `result_data` | JSON |  |  |
| `is_good` | BOOLEAN |  | True |
| `confidence` | FLOAT |  |  |
| `duration` | INTEGER |  | 0 |
| `step_name` | VARCHAR(100) |  |  |
| `timestamp` | DATETIME |  | server |
| `error_msg` | TEXT |  |  |

## user_roles

ORM 类 `UserRole`，定义于 `backend/models/auth_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `user_id` | INTEGER | FK→users.id INDEX NOT NULL |  |
| `role_id` | INTEGER | FK→roles.id INDEX NOT NULL |  |
| `created_at` | DATETIME | NOT NULL | server |

## users

ORM 类 `User`，定义于 `backend/models/auth_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `username` | VARCHAR(64) | UNIQUE INDEX NOT NULL |  |
| `password_hash` | VARCHAR(128) | NOT NULL |  |
| `display_name` | VARCHAR(64) |  |  |
| `active` | BOOLEAN | INDEX NOT NULL | True |
| `must_change_password` | BOOLEAN | NOT NULL | False |
| `last_login_at` | DATETIME |  |  |
| `created_at` | DATETIME | NOT NULL | server |
| `updated_at` | DATETIME | NOT NULL | server |

## video_clips

ORM 类 `VideoClip`，定义于 `backend/models/models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `video_uuid` | VARCHAR(50) | UNIQUE INDEX NOT NULL |  |
| `clip_type` | VARCHAR(20) | NOT NULL |  |
| `related_id` | INTEGER | INDEX |  |
| `result` | VARCHAR(8) |  |  |
| `file_path` | VARCHAR(500) | NOT NULL |  |
| `file_name` | VARCHAR(200) |  |  |
| `file_size` | INTEGER |  | 0 |
| `duration` | FLOAT |  |  |
| `start_time` | DATETIME |  |  |
| `end_time` | DATETIME |  |  |
| `created_at` | DATETIME |  | server |
| `extra_info` | JSON |  |  |

## weighing_records

ORM 类 `WeighingRecord`，定义于 `backend/models/weighing_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `channel_id` | INTEGER | INDEX NOT NULL | 0 |
| `product_sn` | VARCHAR(128) | INDEX |  |
| `model_name` | VARCHAR(128) |  |  |
| `operator` | VARCHAR(64) |  |  |
| `material` | VARCHAR(64) |  |  |
| `standard` | FLOAT |  |  |
| `initial` | FLOAT |  | 0.0 |
| `net` | FLOAT | NOT NULL | 0.0 |
| `verdict` | VARCHAR(16) | INDEX NOT NULL | '' |
| `ts` | FLOAT |  |  |
| `created_at` | DATETIME | INDEX | server |

## work_orders

ORM 类 `WorkOrder`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `order_no` | VARCHAR(64) | UNIQUE INDEX NOT NULL |  |
| `external_id` | VARCHAR(128) | INDEX |  |
| `product_name` | VARCHAR(128) | NOT NULL |  |
| `product_code` | VARCHAR(64) |  |  |
| `product_spec` | VARCHAR(256) |  |  |
| `planned_qty` | INTEGER | NOT NULL | 0 |
| `completed_qty` | INTEGER | NOT NULL | 0 |
| `good_qty` | INTEGER | NOT NULL | 0 |
| `ng_qty` | INTEGER | NOT NULL | 0 |
| `rework_qty` | INTEGER | NOT NULL | 0 |
| `scrap_qty` | INTEGER | NOT NULL | 0 |
| `yield_rate` | FLOAT |  |  |
| `project_id` | INTEGER | FK→projects.id |  |
| `priority` | INTEGER | NOT NULL | 3 |
| `status` | VARCHAR(20) | INDEX NOT NULL | 'draft' |
| `source` | VARCHAR(20) | NOT NULL | 'manual' |
| `binding_scope` | VARCHAR(20) | INDEX NOT NULL | 'project' |
| `target_channels` | JSON |  |  |
| `target_stations` | JSON |  |  |
| `planned_start` | DATETIME |  |  |
| `planned_end` | DATETIME |  |  |
| `actual_start` | DATETIME |  |  |
| `actual_end` | DATETIME |  |  |
| `customer_name` | VARCHAR(128) |  |  |
| `remark` | TEXT |  |  |
| `extra_data` | JSON |  |  |
| `created_by` | VARCHAR(64) |  |  |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## workpiece_flow_configs

ORM 类 `WorkpieceFlowConfig`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `name` | VARCHAR(64) | UNIQUE NOT NULL |  |
| `enabled` | BOOLEAN |  | False |
| `station_channel_ids` | JSON | NOT NULL |  |
| `trigger_mode` | VARCHAR(16) | NOT NULL | 'time_window' |
| `scan_device_id` | INTEGER | FK→scanner_devices.id |  |
| `scan_bind_strategy` | VARCHAR(16) |  | 'entry' |
| `fifo_max_in_flight` | INTEGER |  | 3 |
| `cycle_to_cycle_window_ms` | INTEGER |  | 15000 |
| `physical_trigger_config` | JSON |  |  |
| `settle_strategy` | VARCHAR(16) |  | 'all_ok_required' |
| `short_circuit_on_ng` | BOOLEAN |  | True |
| `workpiece_timeout_ms` | INTEGER |  | 60000 |
| `timeout_action` | VARCHAR(16) |  | 'force_ng' |
| `plugin_data` | JSON |  |  |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

## workpiece_flow_runs

ORM 类 `WorkpieceFlowRun`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `flow_config_id` | INTEGER | FK→workpiece_flow_configs.id INDEX NOT NULL |  |
| `workpiece_id` | INTEGER | FK→workpieces.id INDEX |  |
| `flow_uuid` | VARCHAR(32) | UNIQUE INDEX NOT NULL |  |
| `serial_no` | VARCHAR(128) | INDEX |  |
| `status` | VARCHAR(16) | NOT NULL | 'in_progress' |
| `station_cycle_ids` | JSON |  |  |
| `station_results` | JSON |  |  |
| `final_result` | VARCHAR(8) |  |  |
| `trigger_mode` | VARCHAR(16) |  |  |
| `trigger_source_id` | INTEGER |  |  |
| `started_at` | DATETIME |  | server |
| `completed_at` | DATETIME |  |  |

## workpiece_inspections

ORM 类 `WorkpieceInspection`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `workpiece_id` | INTEGER | FK→workpieces.id INDEX NOT NULL |  |
| `cycle_id` | INTEGER | FK→detection_cycles.id INDEX |  |
| `session_id` | INTEGER | FK→detection_sessions.id |  |
| `inspection_seq` | INTEGER | NOT NULL | 1 |
| `result` | VARCHAR(10) | NOT NULL | 'pending' |
| `event_name` | VARCHAR(100) |  |  |
| `result_reason` | TEXT |  |  |
| `channel_id` | INTEGER |  |  |
| `duration` | FLOAT |  |  |
| `created_at` | DATETIME |  | server |

## workpieces

ORM 类 `Workpiece`，定义于 `backend/models/mes_models.py`。

| 字段 | 类型 | 约束 | 默认 |
|---|---|---|---|
| `id` | INTEGER | PK INDEX |  |
| `serial_no` | VARCHAR(128) | INDEX NOT NULL |  |
| `raw_barcode` | VARCHAR(512) |  |  |
| `order_id` | INTEGER | FK→work_orders.id |  |
| `batch_id` | INTEGER | FK→batches.id |  |
| `project_id` | INTEGER | FK→projects.id NOT NULL |  |
| `status` | VARCHAR(20) | NOT NULL | 'registered' |
| `inspection_count` | INTEGER |  | 0 |
| `latest_cycle_id` | INTEGER |  |  |
| `final_result` | VARCHAR(20) |  |  |
| `channel_id` | INTEGER |  |  |
| `operator` | VARCHAR(64) |  |  |
| `scan_source` | VARCHAR(20) |  | 'manual' |
| `scan_device_id` | INTEGER | FK→scanner_devices.id |  |
| `registered_at` | DATETIME |  | server |
| `first_inspect_at` | DATETIME |  |  |
| `last_inspect_at` | DATETIME |  |  |
| `extra_data` | JSON |  |  |
| `created_at` | DATETIME |  | server |
| `updated_at` | DATETIME |  | server |

