---
name: modify-model
description: "安全修改数据模型(ORM)：字段变更、SQLite migration、序列化影响、前端字段映射。修改models.py或数据库结构前先分析影响。"
argument-hint: "[要修改的模型或字段]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# modify-model: 数据模型安全修改分析

你正在帮用户安全修改 ORM 数据模型。

计划修改: $ARGUMENTS

## 核心 ORM 模型 (backend/models/models.py)

| 模型 | 主要写入者 | 主要读取者 | 前端对应 |
|------|-----------|-----------|----------|
| `Project` | projects.py | projects.py, source.py, Navbar | project.js, Project/index.vue |
| `Model` | models.py (API) | models.py, source.py | model.js, Model/index.vue |
| `ModelConversion` | models.py (转换队列) | models.py | model.js |
| `Task` | tasks.py | tasks.py, reports.py | task.js (废弃) |
| `Camera` | cameras.py | cameras.py | camera.js |
| `DailyStat` | (无写入 - 废弃) | (无读取) | 无 |
| `SystemConfig` | main.py | main.py | 无直接对应 |
| `DetectionSession` | source.py, mes_hooks.py | sessions.py, mes_hooks.py | data.js, Data/index.vue |
| `DetectionCycle` | source.py, mes_hooks.py | sessions.py, mes_hooks.py | data.js, Data/index.vue |
| `StepRecord` | source.py | sessions.py | data.js, Data/index.vue |
| `VideoClip` | source.py | sessions.py | data.js, Data/index.vue |
| `DataExportSetting` | sessions.py | sessions.py, source.py | data.js, Data/index.vue |

**注意:** `DetectionSession` 和 `DetectionCycle` 表在 v2.3.0+ 新增了 `order_id` 列，与 MES 工单关联。

**v3.1.0 工单绑定字段**: `WorkOrder` 新增 `binding_scope` (project/channels/cluster) + `target_channels` (JSON list) + `target_stations` (JSON list)。配合 `WorkOrderService._normalize_binding(data, strict)` 做三选一校验。SQLite 迁移走 `backend/main.py` 的 migrations 列表(`ALTER TABLE work_orders ADD COLUMN ...`)。**改这三个字段时**:
- 后端 `OrderCreate`/`OrderUpdate` Pydantic 跟着加;`_serialize_order` 跟着输出。
- 前端 `OrderPanel.vue` 创建/编辑表单 + 列表"绑定"列 跟着改。
- `get_active_order(project_id, channel_id, station_id)` 按 scope 路由;`find_cluster_orders` 给 cluster_collector 用。
- 计件触点: project/channels 走 `_handle_cycle_end → increment_completed`,cluster 走 `cluster_collector._check_and_dispatch → _increment_cluster_orders`。

## MES ORM 模型 (backend/models/mes_models.py) — v2.3.0+

| 模型 | 主要写入者 | 主要读取者 | 前端对应 |
|------|-----------|-----------|----------|
| `WorkOrder` | mes.py API, mes_hooks.py | mes.py, mes_hooks.py | mes.js, MES/OrderPanel.vue |
| `Batch` | mes.py API | mes.py | mes.js, MES/OrderPanel.vue |
| `Workpiece` | mes_hooks.py (扫码自动注册), mes.py | mes_hooks.py, mes.py | mes.js, MES/WorkpiecePanel.vue |
| `WorkpieceInspection` | mes_hooks.py (cycle_end时) | mes.py (追溯) | mes.js, MES/WorkpiecePanel.vue |
| `DefectRecord` | mes_hooks.py (NG自动分类), mes.py | mes.py (帕累托) | mes.js, MES/DefectPanel.vue |
| `DefectCode` | mes.py API | mes_hooks.py (标签→缺陷映射) | mes.js, MES/DefectPanel.vue |
| `ScannerDevice` | scanner.py API | scanner.py, scanner service | scanner.js, MES/ScannerPanel.vue |
| `ScanLog` | scanner service (扫码时) | scanner.py API | scanner.js, MES/ScannerPanel.vue |
| `MESConnection` | (预留外部MES配置) | (预留) | (预留) |
| `MESCommLog` | (预留外部MES通讯日志) | (预留) | (预留) |

**MES 模型使用独立 DB session（WAL模式），不与检测引擎争锁。**

## Project 模型的 JSON 字段（特殊处理）

Project 模型有多个 JSON 类型字段，存储复杂配置:
```python
class Project:
    steps_config = Column(Text)      # JSON: 步骤定义列表
    events_config = Column(Text)     # JSON: 事件配置
    counters_config = Column(Text)   # JSON: 计数器配置
    alarm_config = Column(Text)      # JSON: 报警配置
    detection_config = Column(Text)  # JSON: 检测参数(conf/iou/max_det)
    data_config = Column(Text)       # JSON: 数据记录配置
    pipeline_config = Column(Text)   # JSON: 检测管线配置(模式/条件/追踪)
```

这些字段被 `json.loads/dumps` 序列化，**不经过 Schema 验证**。
修改这些 JSON 结构时，需要同步:
1. `source.py: set_project_config()` — 解析器
2. `frontend/src/views/Project/index.vue` — 配置编辑器
3. `frontend/src/views/Monitor/index.vue` — 使用配置
4. `frontend/src/layout/Navbar.vue` — `handleProjectChange()` 中的默认值初始化

## SQLite Migration 机制

**没有 Alembic！使用手动 migration** (backend/main.py:migrate_database):
```python
def migrate_database():
    # 直接执行 ALTER TABLE ADD COLUMN
    # 如果列已存在则 catch OperationalError 忽略
    migrations = [
        "ALTER TABLE projects ADD COLUMN pipeline_config TEXT",
        "ALTER TABLE detection_cycles ADD COLUMN ng_reason TEXT",
        ...
    ]
```

**添加新列的步骤:**
1. 在 `models/models.py` 中添加字段定义
2. 在 `main.py:migrate_database()` 中添加 ALTER TABLE 语句
3. 设置合理的默认值（SQLite 不支持 ALTER COLUMN）
4. **SQLite 限制:** 不能删列、不能改列类型、不能加 NOT NULL 无默认值

## 强制分析流程

### 第1步: 确认修改的模型和字段

1. 读取 `backend/models/models.py`
2. 确认字段类型、约束、默认值
3. 检查是否是 JSON 字段（Text 类型存 JSON）

### 第2步: 追踪所有读写点

用 Grep 搜索模型类名和字段名:
```
搜索范围: backend/api/*.py, backend/services/*.py, backend/main.py
搜索关键词: 模型类名, 字段名
```

### 第3步: 检查前端映射

1. 搜索 API 响应中对应的字段名
2. 确认前端视图中的字段使用
3. 特别关注 JSON 字段的解析和构造

### 第4步: 确认 Migration

1. 新增字段 → 添加 ALTER TABLE 到 migrate_database()
2. 默认值 → 必须在 ALTER TABLE 和模型定义中都设置
3. **不要删除列** → SQLite 不支持 DROP COLUMN（3.35.0+ 支持但我们的版本可能不支持）

### 第5步: 生成影响报告

```
修改的模型: [模型名]
修改的字段: [字段名, 类型, 新增/修改/删除]
写入者: [所有写入该字段的代码位置]
读取者: [所有读取该字段的代码位置]
Migration: [需要的ALTER TABLE语句]
前端影响: [需要更新的API/视图文件]
JSON字段影响: [如果是JSON字段，列出所有解析点]
风险等级: [低/中/高]
```

## 修改原则

1. **只加不删:** SQLite 不方便删列，保留旧字段不会有副作用
2. **必须有默认值:** 新增列必须有 DEFAULT 或允许 NULL
3. **JSON字段向后兼容:** 新增 key 可以，删除/改名 key 需要全链路同步
4. **Migration 幂等:** ALTER TABLE 失败(列已存在)必须被 catch 忽略
5. **Schema 同步:** 如果有 Pydantic Schema (backend/schemas/)，需要同步更新

## v2.5.0 模型变更记录

### ScannerDevice (mes_models.py)
- 新增 `rebind_mode = Column(String(20), default="rescan")` — 误检重绑策略
- 新增 `bind_timing = Column(String(20), default="mid_cycle")` — 绑定时机
- 对应 migration 在 `main.py:migrate_database()`
- 前端: `ScannerPanel.vue` 编辑对话框、`scanner.py` API Schema

### MESConnection (mes_models.py)
- `adapter_type` 现在支持 `"modbus_rtu"` 值
- `config` JSON 字段新增 Modbus 专属配置 (port/baudrate/slave_id/registers 等)
- 前端: `GatewayPanel.vue` 根据 adapter_type 切换 config 表单
