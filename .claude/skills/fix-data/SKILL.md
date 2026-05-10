---
name: fix-data
description: "诊断和修复数据问题：Session/Cycle/Step数据不一致、孤立记录清理、录像文件关联修复、DB备份恢复。当数据页面统计异常或记录丢失时使用。"
argument-hint: "[数据问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__filesystem, mcp__sequential-thinking"
---

# fix-data: 数据诊断与修复

你正在帮用户修复天军AI视觉检测系统的数据问题。

问题描述: $ARGUMENTS

## 数据库位置

```
开发模式: backend/ 目录下（BASE_DIR）
生产模式: TIANJUN_DATA_DIR 环境变量指定的目录
  Windows: %APPDATA%/tianjun-ai-vision/
  Linux: ~/.local/share/tianjun-ai-vision/

DB 文件: sql_app.db (SQLite3, WAL 模式)
配置:    backend/core/config.py 的 SQLALCHEMY_DATABASE_URI
```

> 启动时若发现 `BASE_DIR/sql_app.db` 但 `DATA_DIR/sql_app.db` 不存在，会自动迁移（参见 `backend/main.py` `_db_path` 初始化）。

## 数据模型关系（v3.5.x 真实结构，共 31 张表）

```
detection_sessions (1)
  ├── (N) detection_cycles
  │       └── (N) step_records
  └── (N) video_clips

-- MES 子库（同 sql_app.db，15 张表）:
work_orders (1)
  ├── (N) batches
  ├── (N) workpieces
  │       └── (N) workpiece_inspections → detection_cycles
  │       └── (N) defect_records → defect_codes
  ├── (N) box_aggregations / box_summaries
  └── 关联: detection_sessions.work_order_id, detection_cycles.work_order_id

-- 导出子库（v3.5.0+，3 张表）:
export_templates / export_realtime_rules / export_run_logs

-- 设备/扫码/集群/外设:
scanner_devices / scan_logs / mes_connections / mes_comm_logs
external_devices / external_device_logs / cluster_config

-- 业务/系统配置:
projects / models / model_conversions / tasks / cameras
daily_stats / system_configs / operators / data_export_settings
```

外键关系（关键）:
- `detection_cycles.session_id` → `detection_sessions.id`
- `step_records.cycle_id` → `detection_cycles.id`
- `video_clips.session_id` → `detection_sessions.id`
- `video_clips.cycle_id` → `detection_cycles.id` (可空)
- `workpiece_inspections.cycle_id` → `detection_cycles.id`

> ⚠️ ORM 类名是 `Model`（**不**是 `MLModel`），表名是 `models`（**不**是 `ml_models`）。三个 models 文件分别在 `backend/models/{models,mes_models,export_models}.py`。

## 常见数据问题及诊断SQL

### 1. 孤立的 Session（status='running'但实际已结束）

```sql
-- 查找所有 running 状态的 session
SELECT id, project_id, channel, start_time, status
FROM detection_sessions
WHERE status = 'running';

-- 修复: 标记为 interrupted
UPDATE detection_sessions SET status = 'interrupted', end_time = datetime('now')
WHERE status = 'running';
```

**注意:** `main.py:fix_orphan_sessions()` 在启动时自动执行此修复。

### 2. Cycle 计数与实际不符

```sql
-- 检查 session 的 cycle 计数 vs 实际
SELECT s.id, s.total_cycles,
       (SELECT COUNT(*) FROM detection_cycles WHERE session_id = s.id) as actual_cycles,
       s.good_cycles,
       (SELECT COUNT(*) FROM detection_cycles WHERE session_id = s.id AND is_good = 1) as actual_good
FROM detection_sessions s
WHERE s.total_cycles != (SELECT COUNT(*) FROM detection_cycles WHERE session_id = s.id);
```

### 3. Step 记录缺失或异常

```sql
-- 查找没有步骤记录的 cycle
SELECT c.id, c.session_id, c.cycle_number, c.completed_steps
FROM detection_cycles c
WHERE c.completed_steps > 0
  AND NOT EXISTS (SELECT 1 FROM step_records WHERE cycle_id = c.id);

-- 查找持续时间异常的步骤 (<0.1秒，可能是幻影步骤)
SELECT * FROM step_records WHERE duration_seconds < 0.1;

-- 查找 interval_to_next 异常的步骤
SELECT * FROM step_records WHERE interval_to_next < 0 OR interval_to_next > 3600;
```

### 4. 录像文件关联问题

```sql
-- 查找 DB 中有记录但文件不存在的录像
SELECT id, file_path, file_size FROM video_clips;
-- 然后逐个检查 file_path 是否存在

-- 查找没有 session 关联的录像
SELECT * FROM video_clips WHERE session_id IS NULL;

-- 查找文件系统中存在但 DB 中没有记录的录像
-- 需要对比 recordings/ 目录和 video_clips 表
```

### 5. 项目配置损坏

```sql
-- 检查 JSON 字段是否为合法 JSON
SELECT id, name,
  json_valid(steps_config) as steps_ok,
  json_valid(pipeline_config) as pipeline_ok,
  json_valid(counters_config) as counters_ok
FROM projects;
```

## 自动清理机制

### 系统自动清理 (sessions.py:_perform_auto_cleanup)
- **触发:** 24小时定时器 或 手动 POST /cleanup/run
- **清理内容:**
  1. 超过保留天数的 DB 记录（Session/Cycle/Step）
  2. 孤立的录像文件（无对应DB记录）
  3. 临时文件和缓存
  4. 上传的视频文件（可选）
- **配置:** GET/PUT /cleanup-settings

### 清理风险
- 清理可能与正在录制的文件冲突
- 清理孤立文件时可能删除正在使用的录像
- DB 删除级联: 删 Session → 不会自动删 Cycle（需手动处理）

## 数据备份与恢复

### 备份
```
GET /api/v1/backup/database
→ 返回 tianjun.db 文件下载
```

### 恢复
1. 停止后端
2. 替换 DB 文件
3. 重启后端（会自动执行 migration + 修复孤立session）

## 数据导出

### CSV 导出端点
```
GET /api/v1/export/csv?type=session&session_id=X
GET /api/v1/export/csv?type=cycle&cycle_id=X
GET /api/v1/export/csv?type=all&start_date=...&end_date=...
```

## 诊断步骤

1. **确定问题范围:** Session/Cycle/Step 哪一级？
2. **备份数据库:** 先 GET /backup/database 下载备份
3. **执行诊断SQL:** 使用上方的SQL查找异常数据
4. **定位原因:** 
   - 数据写入端: source.py 的 start/end_session/cycle, record_step
   - 数据读取端: sessions.py 的查询逻辑
5. **修复数据:** 直接SQL更新或通过API

## 关键文件
- `backend/models/{models,mes_models,export_models}.py` — 31 张表 ORM 定义
- `backend/api/source.py` (1573 行) + 14 个 `source_*_mixin.py` — 数据写入端
- `backend/api/sessions*.py`（4 个文件 ~2086 行） — 数据读取+清理+导出
- `backend/main.py` — `migrate_database()`（60+ ALTER TABLE）/ 启动时孤儿 session 清理
- `backend/core/config.py` — `DATA_DIR` 路径 + DB URI
- `frontend/src/views/Data/index.vue` — 数据展示页面

## 已知陷阱
- SQLite 不支持真正的并发写入，多线程写入可能导致 "database is locked"（生产建议用 WAL，已默认开启）
- `_filter_valid_steps()` 过滤 <0.1秒的步骤可能误删合法数据
- `_reconcile_step_records()` 在异常情况下可能覆盖正确的步骤时间
- 自动清理的保留天数计算基于 UTC 时间，可能与本地时间有偏差
- **"幽灵 cycle"问题**（v3.5.x 已知）：`last_step` 结算清除 `step_last_seen` 导致快速重检测，产生短周期单步骤记录；解法是设置 `min_duration` ≥ 0.5s。详见 `debug-source` skill
- 旧手册 v2.4.0 写"22 张表"是过时的，以代码为准（13 + 15 + 3 = 31）
