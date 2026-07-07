# -*- coding: utf-8 -*-
"""m0000: 存量迁移基线（原 main.py:migrate_database 原样平移, 2026-07 冻结）。

- 109 条补列清单 + 方言归一 + v3.10 阶段 5 operators 表清理
- **本模块永远幂等执行**（runner 的 _ALWAYS_RUN）：inspector 逐列探查、
  缺列才 ALTER，任意历史版本老库跑完即到 v3.31 schema 基线
- ⚠️ 本文件已冻结：不要再往 migrations 列表追加新条目——
  新 schema 变更请新建 m<编号>_<语义名>.py（见包 __init__.py 说明）
"""
from __future__ import annotations

from sqlalchemy import text

MIGRATION_ID = "m0000_legacy"


def apply(engine):
    """添加新增的数据库列（如果不存在）——逐字平移, 不做任何"顺手优化"。"""
    migrations = [
        # (表名, 列名, 列类型)
        ("step_records", "interval_to_next", "FLOAT"),
        # v3.13 M3.3: 插件命名空间字段 (PluginHost.write_plugin_step_field 落地点)
        ("step_records", "plugin_data", "JSON"),
        # 周期录像 OK/NG 标记 (供"OK/NG 分开存 + 分别保留期"清理用, 老库补列默认 NULL)
        ("video_clips", "result", "VARCHAR(8)"),
        # v3.13 RFC 10: 工位组联动字段 (老库升级时补列, 默认 NULL = 独立结算)
        ("detection_cycles", "channel_group_id", "INTEGER"),
        ("detection_cycles", "group_settled_with", "JSON"),
        ("detection_cycles", "group_settle_result", "VARCHAR(8)"),
        ("detection_cycles", "interval_to_next", "FLOAT"),
        ("data_export_settings", "record_cycle_interval", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_step_duration", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_step_interval", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_step_event", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_cycle_duration", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_cycle_interval", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_cycle_result", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_counters", "BOOLEAN DEFAULT 1"),
        ("data_export_settings", "export_session_info", "BOOLEAN DEFAULT 1"),
        ("projects", "alarm_config", "JSON"),
        ("projects", "detection_config", "JSON"),
        ("projects", "data_config", "JSON"),
        ("detection_sessions", "channel_id", "INTEGER DEFAULT 0"),
        ("detection_sessions", "shift_label", "VARCHAR(20)"),
        ("detection_sessions", "name", "VARCHAR(64)"),
        ("projects", "model_format", "VARCHAR(50) DEFAULT 'pytorch_fp32'"),
        # MES: 现有表扩展字段 (可空, 安全迁移)
        ("detection_cycles", "order_id", "INTEGER"),
        ("detection_sessions", "order_id", "INTEGER"),
        ("mes_connections", "extra_fields_schema", "JSON"),
        ("detection_sessions", "operator_id", "INTEGER"),
        ("detection_cycles", "operator_id", "INTEGER"),
        ("scanner_devices", "scan_required", "BOOLEAN DEFAULT 0"),
        ("scanner_devices", "duplicate_scan_action", "VARCHAR(20) DEFAULT 'overwrite'"),
        ("scanner_devices", "warn_no_barcode", "BOOLEAN DEFAULT 0"),
        ("scanner_devices", "rebind_mode", "VARCHAR(20) DEFAULT 'rescan'"),
        ("scanner_devices", "bind_timing", "VARCHAR(20) DEFAULT 'mid_cycle'"),
        ("scanner_devices", "broadcast_channels", "JSON"),
        ("scanner_devices", "device_type", "VARCHAR(20) DEFAULT 'auto'"),
        ("scanner_devices", "external_only", "BOOLEAN DEFAULT 0"),
        ("scanner_devices", "pairing_group", "VARCHAR(32)"),
        ("external_devices", "pairing_group", "VARCHAR(32)"),
        ("cluster_config", "channel_station_map", "JSON"),
        ("mes_connections", "bound_channels", "JSON"),
        # v3.20: 外部 MES 工单主动拉取 — 复用连接表, 拉取专属配置全存 config.pull JSON.
        # 这两列 ORM 早有声明("第三期预留")但历史迁移漏补, 老库升级时补上(默认关).
        ("mes_connections", "pull_enabled", "BOOLEAN DEFAULT 0"),
        ("mes_connections", "pull_interval_sec", "INTEGER DEFAULT 60"),
        ("cluster_config", "timeout_push", "BOOLEAN DEFAULT 0"),
        # v2.7.5: 外部设备稳定值判定与有重无码告警
        ("external_devices", "stable_enabled", "BOOLEAN DEFAULT 1"),
        ("external_devices", "stable_delta", "FLOAT DEFAULT 0.05"),
        ("external_devices", "stable_count", "INTEGER DEFAULT 5"),
        ("external_devices", "zero_threshold", "FLOAT DEFAULT 0.05"),
        ("external_devices", "weight_no_barcode_alarm_enabled", "BOOLEAN DEFAULT 0"),
        ("external_devices", "weight_no_barcode_alarm_delay_sec", "INTEGER DEFAULT 10"),
        ("scanner_devices", "ok_rescan_cooldown_sec", "INTEGER DEFAULT 0"),
        # v2.7.16 迟到扫码补绑窗口（秒），0 关闭
        ("scanner_devices", "late_scan_bind_window_sec", "INTEGER DEFAULT 3"),
        # v2.7.16 扫描模式 + B 模式间隔
        ("scanner_devices", "scan_mode", "VARCHAR(32) DEFAULT 'continuous'"),
        ("scanner_devices", "throttle_idle_ms", "INTEGER DEFAULT 500"),
        # v3.1.0 工单绑定范围 (project / channels / cluster)
        ("work_orders", "binding_scope", "VARCHAR(20) DEFAULT 'project'"),
        ("work_orders", "target_channels", "TEXT"),
        ("work_orders", "target_stations", "TEXT"),
        # v3.1.1 称重器配对模式 (stable / instant)
        ("external_devices", "pairing_mode", "VARCHAR(16) DEFAULT 'stable'"),
        # v3.1.2 多工位广播结算联动: 主工位结算时强制带动其他广播工位
        ("scanner_devices", "broadcast_settle_mode", "VARCHAR(20) DEFAULT 'independent'"),
        ("scanner_devices", "primary_settle_channel", "INTEGER"),
        ("scanner_devices", "primary_settle_min_items", "INTEGER DEFAULT 1"),
        # v3.1.2 集群站点结果合并策略 (latest / ok_lock)
        ("cluster_config", "station_result_strategy", "VARCHAR(20) DEFAULT 'latest'"),
        # v3.3.0 码-码闭环结算: bind_timing="scan_pair" 模式下扫 A 后等待扫 B 的最大秒数
        ("scanner_devices", "scan_pair_max_wait_sec", "INTEGER DEFAULT 0"),
        # v3.4.0 D 容器跨线/区域触发扫码 (scan_mode='D')
        ("scanner_devices", "scan_d_geometry", "VARCHAR(8) DEFAULT 'line'"),
        ("scanner_devices", "scan_d_line", "JSON"),
        ("scanner_devices", "scan_d_zone", "JSON"),
        ("scanner_devices", "scan_d_gone_confirm_frames", "INTEGER DEFAULT 30"),
        # v3.7.2 扫码器旁路 — DetectionCycle 加 external_meta 存 cycle_start 锁定的快照
        ("detection_cycles", "external_meta", "JSON"),
        # v3.7.2 模板自带"推荐规则配置" (扫码器旁路等预设带默认 rule 字段)
        ("export_templates", "default_rule_config", "JSON"),
        # v3.7.2 扫码器旁路 — ExportRealtimeRule 加策略 + 去重重试
        ("export_realtime_rules", "latest_file_strategy",
         "VARCHAR(32) DEFAULT 'cycle_start_snapshot'"),
        ("export_realtime_rules", "latest_file_wait_stable_ms",
         "INTEGER DEFAULT 100"),
        ("export_realtime_rules", "latest_file_max_age_sec",
         "INTEGER DEFAULT 0"),
        ("export_realtime_rules", "dedupe_same_filename",
         "BOOLEAN DEFAULT 0"),
        ("export_realtime_rules", "dedupe_retry_max_sec",
         "INTEGER DEFAULT 5"),
        ("export_realtime_rules", "dedupe_retry_interval_ms",
         "INTEGER DEFAULT 100"),
        ("export_realtime_rules", "last_used_input_filename",
         "VARCHAR(256)"),
        # v3.21 M3 包装结算 — 组⑤ 收尾与回推 (老库已建该表但缺新列时补上)
        ("packaging_flow_configs", "on_forced_stop",
         "VARCHAR(8) DEFAULT 'settle'"),
        ("packaging_flow_configs", "forced_settle_on_standby",
         "BOOLEAN DEFAULT 1"),
        ("packaging_flow_configs", "push_on_complete",
         "BOOLEAN DEFAULT 0"),
        ("packaging_flow_configs", "push_event_type",
         "VARCHAR(32) DEFAULT 'packaging_complete'"),
        # v3.21 M6 包装结算 — 组⑥ 异常 → 项目事件映射 (全可选, NULL=默认通用报警)
        ("packaging_flow_configs", "event_short_box", "INTEGER"),
        ("packaging_flow_configs", "event_over_box", "INTEGER"),
        ("packaging_flow_configs", "event_tray_ng", "INTEGER"),
        ("packaging_flow_configs", "event_box_ng", "INTEGER"),
        ("packaging_flow_configs", "event_label_mismatch", "INTEGER"),
        ("packaging_flow_configs", "event_label_len", "INTEGER"),
        ("packaging_flow_configs", "event_mes_fail", "INTEGER"),
        # v3.22 上银 MES 闭环 — 组⑦ 滑块口径 + 尾箱 + 自动切项目 + 塞工单 gate (全可选默认关)
        ("packaging_flow_configs", "count_unit", "VARCHAR(8) DEFAULT 'trays'"),
        ("packaging_flow_configs", "items_per_box_source", "VARCHAR(8) DEFAULT 'project'"),
        ("packaging_flow_configs", "items_per_box_fixed", "INTEGER DEFAULT 0"),
        ("packaging_flow_configs", "slider_total_field", "VARCHAR(64) DEFAULT 'dispatch_qty'"),
        ("packaging_flow_configs", "auto_switch_project", "BOOLEAN DEFAULT 0"),
        ("packaging_flow_configs", "spec_to_project", "JSON"),
        ("packaging_flow_configs", "match_project_by_name", "BOOLEAN DEFAULT 0"),
        ("packaging_flow_configs", "name_match_strict_boundary", "BOOLEAN DEFAULT 0"),
        ("packaging_flow_configs", "tail_paper_order_required", "BOOLEAN DEFAULT 0"),
        ("packaging_flow_configs", "tail_paper_step_label", "VARCHAR(64)"),
        ("packaging_flow_configs", "event_missing_paper", "INTEGER"),
        # v3.23 缺油嘴视觉 gate: 每箱封箱前"放油嘴"步骤必须 covered
        ("packaging_flow_configs", "oil_nozzle_required", "BOOLEAN DEFAULT 0"),
        ("packaging_flow_configs", "oil_nozzle_step_label", "VARCHAR(64)"),
        ("packaging_flow_configs", "event_missing_nozzle", "INTEGER"),
        # v3.22 insert_char 模式: 扫码枪丢符号时把 '-' 等补回固定位置
        ("packaging_flow_configs", "hyphen_pos", "INTEGER DEFAULT 0"),
        # v3.22 PackagingFlowRun 滑块口径 + 尾箱运行态
        ("packaging_flow_runs", "count_unit", "VARCHAR(8) DEFAULT 'trays'"),
        ("packaging_flow_runs", "slider_total", "INTEGER DEFAULT 0"),
        ("packaging_flow_runs", "items_per_box", "INTEGER DEFAULT 0"),
        ("packaging_flow_runs", "tail_target", "INTEGER DEFAULT 0"),
        ("packaging_flow_runs", "current_box_sliders", "INTEGER DEFAULT 0"),
        ("packaging_flow_runs", "paper_order_done", "BOOLEAN DEFAULT 0"),
        # v3.23 强制结案审计留痕 (管理员/主管手动强制收尾)
        ("packaging_flow_runs", "forced_reason", "VARCHAR(512)"),
        ("packaging_flow_runs", "forced_by", "VARCHAR(64)"),
    ]
    
    from sqlalchemy import inspect
    from backend.db.database import get_dialect

    dialect = get_dialect()

    def _normalize_type(sql_type: str) -> str:
        """把 SQLite 风格的列类型翻译成当前 dialect 的合法 DDL。"""
        t = sql_type.strip()
        if dialect == "postgresql":
            t = t.replace("BOOLEAN DEFAULT 1", "BOOLEAN DEFAULT TRUE")
            t = t.replace("BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE")
            if t.upper().startswith("JSON") and not t.upper().startswith("JSONB"):
                t = "JSONB" + t[4:]
        return t

    try:
        insp = inspect(engine)
        existing_tables = set(insp.get_table_names())
        with engine.connect() as conn:
            for table, column, col_type in migrations:
                if table not in existing_tables:
                    continue
                cols = {c["name"] for c in insp.get_columns(table)}
                if column in cols:
                    continue
                ddl = _normalize_type(col_type)
                print(f"[DB] 添加 {table}.{column} ({ddl}) ...")
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))
                conn.commit()
    except Exception as e:
        print(f"数据库迁移检查: {e}")

    # =====================================================
    # v3.10+ 阶段 5: 删除旧 operators 表 (用户/角色系统接管)
    # detection_sessions.operator_id / detection_cycles.operator_id 列保留,
    # 语义已重定向到 users.id (阶段 4 完成). 历史 operator_id 指向不存在 user 时,
    # 代码层 (api/sessions.py / services/*) 已做 None 兜底.
    # =====================================================
    try:
        insp = inspect(engine)
        if "operators" in set(insp.get_table_names()):
            print("[DB] 阶段 5 清理: DROP TABLE operators (旧操作员表)")
            with engine.connect() as conn:
                conn.execute(text("DROP TABLE IF EXISTS operators"))
                conn.commit()
    except Exception as e:
        print(f"[DB] 删除旧 operators 表失败 (忽略): {e}")
