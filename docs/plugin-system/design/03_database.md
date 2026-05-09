# 03 — 数据库：plugins 表 + PG/SQLite 双跑

> 适用版本：基于 `feat/plugin-config` v0.1
> 本文目的：把"plugins 系统在数据库层的所有表"+ "PG/SQLite 兼容策略" + "插件自家表的生命周期"定义到**可建表 + 可迁移 + 可卸载**的程度。
>
> 阅读前置：design 00 第六节（PG 迁移确认）、design 01 §3.6（backend.tables 命名空间）、design 02（验签）。
>
> 配套：`design/06_tier3_fullstack.md`（加载器调用 `Base.metadata.create_all` + `migrate_plugin_db`）。

---

## 一、现状盘点（强制对齐）

### 1.1 当前主程序数据库结构

```
backend/db/database.py
├── engine = create_engine(SQLALCHEMY_DATABASE_URI)        # SQLite + WAL
│   PRAGMA: journal_mode=WAL / synchronous=NORMAL / busy_timeout=15000
├── Base = declarative_base()                              # 全局唯一
└── SessionLocal = sessionmaker(bind=engine)
```

### 1.2 当前启动时建表流程（`backend/main.py:44~`）

```python
# main.py
from backend.models import export_models  # noqa: F401  ← 强制 import 触发 ORM 注册
Base.metadata.create_all(bind=engine)     # 自动建任何注册到 Base 的表
migrate_database()                         # 手动 ALTER TABLE 给老库补列
```

**两个模式并存**：
- `create_all` 处理**新建表**（任何注册到 `Base.metadata` 的 ORM 类都会被建）
- `migrate_database()` 处理**老库加列**（SQLite 不支持完整的 ALTER）

### 1.3 现有 31 张表分类

来自 inventory/01：

```
projects, models, model_conversions, tasks,
detection_sessions, detection_cycles, step_records,
data_export_settings, system_configs,
mes_connections, mes_orders, mes_workpieces, mes_defects,
scanner_devices, scanner_records,
operators, operator_history,
cluster_machines, cluster_workstations, box_aggregations,
external_devices, external_device_records,
alarm_devices, alarm_records, alarm_routes,
export_templates, export_history, export_realtime_rules,
shifts (?), workstations (deprecated?), audit_log (?)
```

> ⚠️ inventory/05 已标识 BUG-1：`_fix_db_paths` 用了错误表名 `ml_models`（应为 `models`）

### 1.4 plugins 系统将新增的表

| 表名 | 用途 | 数量级 | 持久化要求 |
|---|---|---|---|
| `plugins` | 插件注册（一行=一个已检测到的插件） | 1~10 行 | 持久（重启不丢） |
| `plugin_state` | 插件运行时状态 | 1~10 行 | 持久（用于 Settings 显示） |
| `plugin_audit_log` | 验签 / 加载 / 卸载审计日志 | 100~10K 行 | 持久（90 天） |
| `plugin_config_versions` | 插件 default_config 应用历史 | 10~100 行 | 持久 |
| `p_{cc}_*` | **插件自家表**（命名空间 `p_{customer_code}_*`） | 由插件决定 | 由插件决定 |

---

## 二、4 张主程序拥有的表（详细定义）

### 2.1 `plugins` 表

```python
# backend/models/plugin_models.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON, Text, UniqueConstraint, Index
from sqlalchemy.sql import func
from backend.db.database import Base


class PluginInstall(Base):
    """已安装插件元数据（design 00 单插件激活）"""
    __tablename__ = "plugins"

    id = Column(Integer, primary_key=True, index=True)

    # ===== 身份字段 =====
    customer_code = Column(String(20), nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=False)
    plugin_version = Column(String(50), nullable=False)
    tier = Column(Integer, nullable=False)  # 1/2/3

    # ===== manifest 全文备份（JSON 文本） =====
    manifest_json = Column(JSON, nullable=False)  # 解析过的 dict, 用于 Settings 显示
    manifest_sha256 = Column(String(64), nullable=False)  # 加速比对

    # ===== 验签结果快照 =====
    signature_status = Column(String(20), nullable=False, default="unknown")  # ok | invalid | expired | revoked
    public_key_fingerprint = Column(String(16), nullable=True)  # 8 byte hex
    signed_by = Column(String(64), nullable=True)
    signed_at = Column(DateTime(timezone=True), nullable=True)

    # ===== 文件系统位置 =====
    install_path = Column(String(500), nullable=False)  # 绝对路径，e.g. /data/plugins/acme

    # ===== 状态机 =====
    state = Column(String(20), nullable=False, default="installed")
    # installed | active | failed | quarantined | disabled

    error_msg = Column(Text, nullable=True)  # state=failed/quarantined 时的错误描述
    error_code = Column(String(64), nullable=True)  # 对应 design 02 错误代码

    # ===== 时间戳 =====
    installed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    last_loaded_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # ===== 反规约索引 =====
    __table_args__ = (
        Index("ix_plugins_state", "state"),
        Index("ix_plugins_signature_status", "signature_status"),
    )
```

#### 字段说明

| 字段 | 必填 | 备注 |
|---|---|---|
| `customer_code` | ✅ | 全局唯一（design 00 单插件激活）→ UNIQUE 约束 |
| `manifest_json` | ✅ | JSON 字段（PG 用 JSONB / SQLite 用 TEXT） |
| `manifest_sha256` | ✅ | 与 design 02 §6.4 一致；快速判定"manifest 有没有变" |
| `signature_status` | ✅ | 5 状态枚举（用 String 不用 Enum，方便 PG/SQLite 兼容） |
| `state` | ✅ | 见 design 00 第七节状态机 |
| `error_code` | ⚪ | design 02 §十 33 错误代码之一 |
| `install_path` | ✅ | 落到磁盘的绝对路径（卸载时按此清理） |

#### state 状态机（与 design 00 第七节对齐）

```
                          ┌──────┐
       拷贝插件目录 ───→  │installed│
                          └──┬───┘
                             │ 验签 + 加载成功
                             ↓
                          ┌──────┐    用户禁用    ┌──────┐
                          │active │ ──────────→ │disabled│
                          └──┬───┘  ←──────────  └──────┘
                  加载失败 │
                  签名失败 │
                          ↓
                       ┌──────┐
                       │failed │
                       └──┬───┘
                  3 次连续失败 │
                          ↓
                    ┌────────────┐
                    │quarantined │  ← 隔离, 不再尝试加载
                    └────────────┘
```

### 2.2 `plugin_state` 表（运行时状态）

```python
class PluginState(Base):
    """插件运行时状态 — 与 PluginInstall 是 1:1 但分开存为了:
    1) 高频更新的字段（每次加载就改）不污染主表的 created_at
    2) 卸载时主表保留, state 表清空
    """
    __tablename__ = "plugin_state"

    plugin_id = Column(Integer, primary_key=True)  # FK→ plugins.id
    customer_code = Column(String(20), nullable=False, index=True)

    # ===== 加载诊断 =====
    last_load_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_load_ok_at = Column(DateTime(timezone=True), nullable=True)
    load_attempt_count = Column(Integer, nullable=False, default=0)
    load_failure_count = Column(Integer, nullable=False, default=0)
    last_load_duration_ms = Column(Integer, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_msg = Column(Text, nullable=True)

    # ===== 健康度（最近一次加载后采样） =====
    cpu_avg_pct = Column(Integer, nullable=True)
    memory_mb = Column(Integer, nullable=True)
    background_thread_count = Column(Integer, nullable=True)

    # ===== Hook 调用计数（design 06 错误隔离用） =====
    hook_invocation_count = Column(Integer, nullable=False, default=0)
    hook_failure_count = Column(Integer, nullable=False, default=0)
    hook_avg_duration_ms = Column(Integer, nullable=True)

    # ===== 注册的资源数量（自检用） =====
    registered_routes_count = Column(Integer, nullable=False, default=0)
    registered_adapters_count = Column(Integer, nullable=False, default=0)
    registered_hooks_count = Column(Integer, nullable=False, default=0)
    registered_tables_count = Column(Integer, nullable=False, default=0)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
```

**写入策略**：
- 主程序启动加载完插件后 INSERT or UPDATE
- 每次 hook 失败时**计数 +1**（用 `Session.query().update({...: ... + 1})`）
- Settings 页定时拉

### 2.3 `plugin_audit_log` 表（审计日志）

```python
class PluginAuditLog(Base):
    """插件操作审计日志 — 90 天自动清理"""
    __tablename__ = "plugin_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    customer_code = Column(String(20), nullable=False, index=True)
    plugin_version = Column(String(50), nullable=True)

    event_type = Column(String(40), nullable=False, index=True)
    # install | uninstall | activate | deactivate | load_ok | load_fail
    # | verify_fail | hook_error | quarantine | unquarantine | rotate_key

    event_detail = Column(JSON, nullable=True)
    # 例: {"error_code": "PLUGIN_SIGNATURE_FAIL", "step": "rsa_verify"}

    operator_id = Column(Integer, nullable=True)  # 操作员（installs / activate 时）
    occurred_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        Index("ix_audit_log_cc_time", "customer_code", "occurred_at"),
    )
```

**清理策略**：
- 启动时清理 90 天前的记录（与 export_history 同模式）
- DELETE FROM plugin_audit_log WHERE occurred_at < now() - interval '90 days'

### 2.4 `plugin_config_versions` 表（默认 KV 应用历史）

```python
class PluginConfigVersion(Base):
    """记录 default_config 应用历史

    场景: 插件 v1.0.0 写入 plugin.acme.theme_color="#FFEB3B"
          客户改成了 "#FF0000"
          插件升级到 v1.1.0, default_config 变成 "#00FF00"
          → 此时不应覆盖客户改过的, 但需要 record 历史
    """
    __tablename__ = "plugin_config_versions"

    id = Column(Integer, primary_key=True, index=True)
    customer_code = Column(String(20), nullable=False, index=True)
    plugin_version = Column(String(50), nullable=False)
    config_key = Column(String(200), nullable=False)  # plugin.acme.theme_color
    default_value = Column(JSON, nullable=True)  # 插件声明的默认值
    applied = Column(Boolean, nullable=False, default=False)  # 是否真的写到 system_configs
    applied_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("customer_code", "plugin_version", "config_key", name="uq_pcv_cc_pv_key"),
    )
```

---

## 三、SQLite vs PostgreSQL 兼容策略

> design 00 第六节确认：PG 迁移不阻塞插件，但插件设计**必须**对 PG 友好。

### 3.1 已知差异点（一份针对插件的最小集）

| 主题 | SQLite | PostgreSQL | 我们的策略 |
|---|---|---|---|
| **JSON 字段** | 实际存 TEXT | 有 `JSON` / `JSONB` | 用 SQLAlchemy 通用 `JSON`（不要 PG 专属 `JSONB`），插件查询用 `func.json_extract`（SQLite）+ `->>`（PG）→ **统一用 ORM，不写裸 SQL** |
| **DEFAULT CURRENT_TIMESTAMP** | 支持 | 支持 | 用 `server_default=func.now()` |
| **AUTOINCREMENT** | INTEGER PRIMARY KEY 自动 | SERIAL / GENERATED | SQLAlchemy `Integer, primary_key=True` 通用 |
| **BOOLEAN** | INTEGER 0/1 | 真 BOOLEAN | SQLAlchemy 抽象掉，**Python 侧拿到都是 bool** |
| **ALTER TABLE ADD COLUMN** | 支持有限 | 完整支持 | `migrate_database()` 用 `ALTER TABLE x ADD COLUMN y` 两边都能走 |
| **ALTER TABLE DROP COLUMN** | SQLite 3.35+ 才支持 | 支持 | 不要用，**用 SQLAlchemy 不动表**或 build new table |
| **PARTIAL INDEX** | 3.8+ 支持 | 支持 | 用 SQLAlchemy `Index(.., postgresql_where=...)` 时**两边走分支** |
| **RETURNING 子句** | 3.35+ | 一直支持 | 不依赖（用 `flush()` + `id` 拿） |
| **WAL 模式** | 必须 | n/a | `_sqlite_on_connect` 已用 `dialect.name == 'sqlite'` 守卫（design 03 §五代码示例） |
| **JSON path** | `->>` 不支持，用 `json_extract` | `->>` / `#>>` | 插件查询应通过 ORM, 不直接写 SQL |
| **大小写敏感** | 默认不敏感 LIKE | 默认敏感 | `ILIKE` PG 专属，用 `func.lower(x).contains(...)` |
| **空字符串 vs NULL** | 字符串 == "" 时仍可能视为空 | 严格区分 | 用 `nullable=False, default=""` 一致化 |
| **数据库锁** | 整库写锁 | 行锁 | 插件 hook 不要做长事务 |
| **生成列 GENERATED** | 3.31+ | 一直支持 | **不用**（兼容性差） |
| **DDL 事务** | 不在事务内 | 在事务内 | 插件迁移用 `engine.begin()` 包裹 |
| **PRAGMA** | 大量 | 没有 | `_sqlite_on_connect` 已用 if 守卫 |

### 3.2 双跑工程化的三个守卫

#### 守卫 1：dialect 检测

```python
# backend/db/database.py 现有
@event.listens_for(engine, "connect")
def _sqlite_on_connect(dbapi_connection, connection_record):
    """只在 SQLite 时设 PRAGMA"""
    if engine.dialect.name == "sqlite":
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        ...
```

> 当前代码用 try/except 包了，但事实上 PG 不会走到 `cursor.execute("PRAGMA ...")` 那行（dbapi 不一样）。**建议改成显式 if**——更清晰：

```python
@event.listens_for(engine, "connect")
def _on_connect(dbapi_connection, connection_record):
    if engine.dialect.name != "sqlite":
        return
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=15000;")
        cursor.close()
    except Exception as e:
        print(f"[DB] PRAGMA 设置失败: {e}", flush=True)
```

#### 守卫 2：迁移 SQL 跨 dialect 兼容

```python
# backend/db/migrate.py（建议新增）
def alter_add_column(table: str, column: str, type_def: str):
    """跨 SQLite + PG 的 ALTER TABLE ADD COLUMN

    type_def 写法注意:
    - SQLite: "FLOAT" / "INTEGER" / "VARCHAR(50)" / "BOOLEAN DEFAULT 1"
    - PostgreSQL: "DOUBLE PRECISION" / "INTEGER" / "VARCHAR(50)" / "BOOLEAN DEFAULT TRUE"
    """
    dialect = engine.dialect.name
    if dialect == "postgresql":
        # PG 严格区分大小写, 改写一些 SQLite 习惯
        type_def = (type_def
                    .replace("DEFAULT 1", "DEFAULT TRUE")
                    .replace("DEFAULT 0", "DEFAULT FALSE")
                    .replace("FLOAT", "DOUBLE PRECISION"))

    sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {type_def}"
    if dialect == "sqlite":
        # SQLite 不支持 ADD COLUMN IF NOT EXISTS, 自己判断
        if _column_exists(table, column):
            return
        sql = f"ALTER TABLE {table} ADD COLUMN {column} {type_def}"

    with engine.begin() as conn:
        conn.execute(text(sql))


def _column_exists(table: str, column: str) -> bool:
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            return any(r[1] == column for r in rows)
    else:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = :t AND column_name = :c
            """), {"t": table, "c": column}).first()
            return row is not None
```

#### 守卫 3：JSON 字段查询不写裸 SQL

插件代码**禁止**：

```python
# ❌ 不行：SQLite 风格
session.execute(text("SELECT * FROM mes_workpieces WHERE json_extract(extra_fields, '$.acme_id') = '123'"))

# ❌ 不行：PG 风格
session.execute(text("SELECT * FROM mes_workpieces WHERE extra_fields->>'acme_id' = '123'"))
```

插件代码**应该**：

```python
# ✅ 可移植：用 SQLAlchemy 表达式
from sqlalchemy import cast, String
session.query(MESWorkpiece).filter(
    MESWorkpiece.extra_fields["acme_id"].as_string() == "123"
).all()
```

### 3.3 PG-only 与 SQLite-only 字段（不允许）

我们的所有 plugin 表都**只用通用类型**（design 03 §二代码所示）。

**禁用清单**：
- `JSONB`（PG 专属）→ 用 `JSON`
- `UUID`（PG 专属）→ 用 `String(36)` 或 Integer
- `ARRAY`（PG 专属）→ 用 `JSON` 存数组
- `GENERATED ALWAYS AS`（兼容性差）→ 不用
- `INTEGER AUTOINCREMENT`（SQLite 专属语法）→ 用 SQLAlchemy 的 `Integer, primary_key=True`

---

## 四、迁移脚本（v3.6 老库 → 加 plugin 表）

### 4.1 启动时的迁移流程

```python
# backend/main.py 现有第 44 行后, 新增:

Base.metadata.create_all(bind=engine)   # 现有: 自动建任何注册的新表
migrate_database()                       # 现有: ALTER TABLE 加列

# v3.7 新增:
from backend.models import plugin_models  # noqa: F401  ← 让 Base 看到新表
from backend.core.plugin_migrate import migrate_plugin_tables
migrate_plugin_tables()                   # 老库 → 加 plugin 表 + 兼容数据
```

### 4.2 迁移函数

```python
# backend/core/plugin_migrate.py
import logging
from sqlalchemy import inspect, text
from backend.db.database import engine, Base

logger = logging.getLogger(__name__)


def migrate_plugin_tables() -> None:
    """v3.7: 加 4 张 plugin 表（幂等）"""
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    new_tables = ["plugins", "plugin_state", "plugin_audit_log", "plugin_config_versions"]
    missing = [t for t in new_tables if t not in existing]

    if not missing:
        logger.info("[plugin_migrate] all 4 plugin tables exist, skip")
        return

    logger.info("[plugin_migrate] creating tables: %s", missing)
    # Base.metadata.create_all 已经在前面跑过了, 但要保险:
    Base.metadata.create_all(bind=engine, tables=[
        Base.metadata.tables[t] for t in missing
    ])

    # 第一次升级: 没有任何插件, 不需要回填数据
    logger.info("[plugin_migrate] done")


def cleanup_plugin_audit_log() -> None:
    """启动时清理 90 天前审计日志"""
    if engine.dialect.name == "postgresql":
        sql = "DELETE FROM plugin_audit_log WHERE occurred_at < NOW() - INTERVAL '90 days'"
    else:  # sqlite
        sql = "DELETE FROM plugin_audit_log WHERE occurred_at < datetime('now', '-90 days')"
    with engine.begin() as conn:
        result = conn.execute(text(sql))
        logger.info("[plugin_migrate] cleaned %d audit log rows", result.rowcount)
```

### 4.3 老库回滚（万一升级失败）

```python
def rollback_plugin_tables() -> None:
    """灾难恢复用 - 用户手动调"""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS plugin_audit_log"))
        conn.execute(text("DROP TABLE IF EXISTS plugin_config_versions"))
        conn.execute(text("DROP TABLE IF EXISTS plugin_state"))
        conn.execute(text("DROP TABLE IF EXISTS plugins"))
```

---

## 五、插件自家表 `p_{cc}_*` 的生命周期

### 5.1 命名空间强约束

```
表名: p_{customer_code}_<arbitrary>
类名: Plugin{CustomerCode}<Arbitrary>

例:
  acme 客户的"班次表"
    name:  p_acme_shifts
    class: PluginAcmeShift
```

> 强制由 design 01 §3.6 + 加载器校验保证。

### 5.2 注册流程

```python
# 加载器伪代码 (design 06 详):
def load_plugin_tables(plugin_dir, manifest, plugin_module):
    """加载档位 3 插件的 ORM 表"""
    if "tables" not in manifest.get("backend", {}):
        return

    customer_code = manifest["customer_code"]
    new_tables = []

    for table_decl in manifest["backend"]["tables"]:
        # 1. 名称校验 (再次防御)
        if not table_decl["name"].startswith(f"p_{customer_code}_"):
            raise PluginLoadError("MANIFEST_TABLE_PREFIX_INVALID")
        if not table_decl["class_name"].startswith(f"Plugin{customer_code.title()}"):
            raise PluginLoadError("MANIFEST_TABLE_CLASS_PREFIX_INVALID")

        # 2. 加载 ORM 类（动态 import）
        cls = getattr(plugin_module, table_decl["class_name"], None)
        if cls is None:
            raise PluginLoadError(f"PLUGIN_TABLE_CLASS_NOT_FOUND: {table_decl['class_name']}")

        # 3. 校验 Base 一致（必须用同一个 Base!）
        if cls.__base__ is not Base and Base not in cls.__mro__:
            raise PluginLoadError("PLUGIN_TABLE_NOT_ON_GLOBAL_BASE")

        # 4. 校验表名一致
        if cls.__tablename__ != table_decl["name"]:
            raise PluginLoadError("MANIFEST_TABLE_NAME_INCONSISTENT")

        new_tables.append(cls.__table__)

    # 5. 批量建表（仅缺失的）
    Base.metadata.create_all(bind=engine, tables=new_tables)

    # 6. 记录到 plugin_state.registered_tables_count
```

### 5.3 升级（plugin_version 递增 → 老表加列）

> design 01 §3.6 已声明：**主程序不参与 ALTER**，**插件自己处理**。

插件需在 `register_plugin` 内调自己的 migrate：

```python
# plugins/acme/backend/__init__.py
def register_plugin(app, registry, license_payload):
    from .migrate import migrate_acme_tables
    migrate_acme_tables(engine)   # 插件自己 ALTER
    # ...
```

`migrate_acme_tables` 实现示例：

```python
# plugins/acme/backend/migrate.py
from sqlalchemy import text


def migrate_acme_tables(engine):
    """ACME 插件 v1.0 → v1.1 加列"""
    dialect = engine.dialect.name

    migrations = [
        # (table, column, type_def)
        ("p_acme_shifts", "shift_supervisor", "VARCHAR(100)"),
    ]

    for table, column, type_def in migrations:
        if dialect == "postgresql":
            type_def = type_def  # PG 兼容写法
        # 检测列是否已存在 (跨 dialect)
        if _column_exists_safe(engine, table, column):
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {type_def}"))
```

> **建议**：在主程序提供一个 `backend.core.plugin_db_helpers` 模块，把 `_column_exists_safe` / `alter_add_column_safe` 暴露给插件，**减少插件代码重复**。

### 5.4 卸载流程

design 00 第七节"卸载"步骤 5：清理插件自家表。

```python
def uninstall_plugin(customer_code: str, drop_tables: bool):
    """卸载插件

    Args:
        drop_tables: True = 删自家表 (数据丢失)
                     False = 保留表 (重新装能用)
    """
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    plugin_tables = [t for t in existing if t.startswith(f"p_{customer_code}_")]

    if drop_tables:
        with engine.begin() as conn:
            for t in plugin_tables:
                conn.execute(text(f"DROP TABLE IF EXISTS {t}"))

    # 清理 plugin.{cc}.* SystemConfig
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM system_configs WHERE key LIKE :prefix"),
                     {"prefix": f"plugin.{customer_code}.%"})

    # 删 plugins / plugin_state 行
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM plugin_state WHERE customer_code = :cc"), {"cc": customer_code})
        conn.execute(text("DELETE FROM plugins WHERE customer_code = :cc"), {"cc": customer_code})

    # plugin_audit_log 保留 (审计需要)
    # 写入 audit log
    audit_log_insert("uninstall", customer_code, {"drop_tables": drop_tables})
```

#### 卸载时的"保留 vs 删表"决策

| 场景 | 推荐 | 理由 |
|---|---|---|
| 用户主动卸载（Settings 点"卸载"） | drop_tables=False（默认）| 误操作可恢复 |
| 用户点"卸载并清空数据" | drop_tables=True（确认两次）| 用户明确表达 |
| 插件签名失效 → 隔离 → 卸载 | drop_tables=False | 数据可能还要查 |
| 主作者撤销客户授权 | drop_tables=True | 合规要求 |

### 5.5 边界：禁止插件接触主程序表

| 操作 | 是否允许 | 校验位置 |
|---|---|---|
| 插件 SELECT 主程序表（如 detection_cycles） | ✅ 允许（read-only） | n/a (代码自觉) |
| 插件 UPDATE 主程序表的某列 | ⚠️ 警告（不强禁） | 代码 review |
| 插件 INSERT 主程序表（如 mes_workpieces）| ❌ 禁止 | 不强禁，但官方文档明令 |
| 插件 DROP / TRUNCATE 主程序表 | ❌ 禁止 | 加载器无法防, 文档强警 |
| 插件 ADD COLUMN 主程序表 | ❌ 禁止 | 文档强警 |
| 插件 CREATE INDEX 主程序表的列 | ⚠️ 不建议 | 文档警告 |

> **加载器无法在 SQL 层强制隔离**（PG 可以用 `GRANT`，SQLite 不行）。
> **PG 迁移后**可以给插件单独的 `plugin_acme` schema + 角色，强制隔离。

---

## 六、与 SystemConfig KV 表的边界

> 现有表 `system_configs(key, value, value_type, updated_at)`。

### 6.1 命名空间约束（design 01 §九）

```
plugin.{customer_code}.* ← 插件可读可写
plugin.*                 ← 主程序保留（不允许任何插件用这个前缀）
其他键                   ← 主程序拥有, 插件可读, 写入会被加载器拒绝（design 06）
```

### 6.2 default_config 应用算法

```python
def apply_default_config(customer_code: str, plugin_version: str,
                         default_config: dict, db: Session):
    """加载插件时应用 default_config

    规则:
    1. 仅写入 SystemConfig 中"还不存在"的 key（不覆盖客户已修改）
    2. 记录到 plugin_config_versions（不论是否真写入）
    """
    for key, value in default_config.items():
        # a. 命名空间校验（再次防御）
        if not key.startswith(f"plugin.{customer_code}."):
            raise PluginLoadError("MANIFEST_DEFAULT_CONFIG_KEY_INVALID")

        # b. 看 SystemConfig 是否已有
        existing = db.query(SystemConfig).filter_by(key=key).first()
        applied = existing is None

        # c. 不存在就写
        if applied:
            db.add(SystemConfig(key=key, value=json.dumps(value),
                                 value_type=type(value).__name__))

        # d. 记录历史
        db.add(PluginConfigVersion(
            customer_code=customer_code,
            plugin_version=plugin_version,
            config_key=key,
            default_value=value,
            applied=applied,
        ))

    db.commit()
```

### 6.3 升级时的差异处理

```
plugin v1.0.0:
  default_config: { "plugin.acme.theme_color": "#FFEB3B" }
  → 写入 SystemConfig

客户在 UI 改 plugin.acme.theme_color = "#FF0000"

plugin v1.1.0:
  default_config: { "plugin.acme.theme_color": "#00FF00" }
  → 加载器看到已存在 → 不覆盖 (保留 #FF0000)
  → plugin_config_versions 记录 applied=False (有差异，但未应用)
  → Settings UI 提示 "插件默认值变了, 但你的自定义已保留, 是否恢复默认?"
```

---

## 七、PG 迁移路径（与本文紧耦合）

### 7.1 三阶段路线（design 00 第六节确认）

```
v3.6 → v3.7 (插件系统初版)
  依旧 SQLite-only
  但插件代码路径已经"PG-friendly"
  ↓
v3.7 → v3.8 (PG 双跑实验阶段)
  引入 SQLALCHEMY_DATABASE_URI 环境变量
  支持: sqlite:///... 或 postgresql://...
  内部测试: 同一份代码 + sqlite tests + pg tests
  ↓
v3.8 → v4.0 (PG 默认 + SQLite 仍可用)
  Inno Setup 打包内置 PG portable
  默认: PostgreSQL（端口 5435 内置）
  开关: 高级用户可改回 SQLite（不推荐）
  ↓
v4.5 → v5.0 (移除 SQLite)
  仅支持 PG
```

### 7.2 v3.7 阶段的 PG 兼容点

虽然 v3.7 还是 SQLite-only，但插件设计要保证：

- ✅ 所有 plugin 表用通用 SQL 类型（§3.3）
- ✅ 不写裸 SQL（用 ORM 表达式）
- ✅ `_sqlite_on_connect` 加 dialect 守卫
- ✅ 提供 `alter_add_column` helper（§3.2 守卫 2）
- ✅ DELETE 时间间隔 SQL 用 dialect 分支（§4.2）

### 7.3 v3.8 阶段的迁移工具（剧透 design 06）

```python
# backend/core/plugin_pg_migrate.py
def migrate_plugin_data_sqlite_to_pg(sqlite_path: str, pg_url: str):
    """把 SQLite 中的 plugins / plugin_state / plugin_audit_log /
    plugin_config_versions / 所有 p_*_* 表 → 复制到 PG"""
    sqlite_engine = create_engine(f"sqlite:///{sqlite_path}")
    pg_engine = create_engine(pg_url)

    # 1. 在 PG 建表（用 Base.metadata.create_all）
    Base.metadata.create_all(bind=pg_engine)

    # 2. 拷贝主程序拥有的 4 张 plugin 表
    for table in ["plugins", "plugin_state", "plugin_audit_log", "plugin_config_versions"]:
        copy_table(sqlite_engine, pg_engine, table)

    # 3. 拷贝所有 p_*_* 自家表（按表名前缀扫描）
    inspector = inspect(sqlite_engine)
    for t in inspector.get_table_names():
        if t.startswith("p_"):
            copy_table(sqlite_engine, pg_engine, t)

    # 4. 序列重置（PG 的 SERIAL）
    reset_sequences(pg_engine)
```

---

## 八、PG schema 隔离（v4.0 后启用，本文先备案）

### 8.1 概念

PG 支持 schema（命名空间）：

```sql
CREATE SCHEMA plugin_acme;
CREATE TABLE plugin_acme.shifts (...);

-- 给 plugin_acme 用户只授权访问 plugin_acme.* 和 read-only 主表
CREATE USER plugin_acme_user WITH PASSWORD 'xxx';
GRANT USAGE ON SCHEMA plugin_acme TO plugin_acme_user;
GRANT ALL ON ALL TABLES IN SCHEMA plugin_acme TO plugin_acme_user;
GRANT SELECT ON public.detection_cycles TO plugin_acme_user;  -- 仅读主表
```

### 8.2 SQLAlchemy 写法

```python
class PluginAcmeShift(Base):
    __tablename__ = "shifts"
    __table_args__ = {"schema": "plugin_acme"}  # PG 专属
    ...
```

> SQLite 不支持 schema → 我们在 SQLite 阶段**忽略 schema**，用 `p_{cc}_*` 命名空间替代。

### 8.3 v4.0 时的 schema 切换

```python
def get_schema(customer_code: str) -> dict:
    if engine.dialect.name == "postgresql":
        return {"schema": f"plugin_{customer_code}"}
    return {}

class PluginAcmeShift(Base):
    __tablename__ = "p_acme_shifts" if engine.dialect.name == "sqlite" else "shifts"
    __table_args__ = ({"schema": f"plugin_acme"} if engine.dialect.name == "postgresql" else {})
```

> **缺点**：表名要"双名"，迁移脚本复杂。
> **替代方案**：v4.0 也保持 `p_{cc}_*` 命名（不用 schema），换 schema 留给 v5.0 重构。

---

## 九、性能与运维

### 9.1 索引清单

| 表 | 索引 | 用途 |
|---|---|---|
| plugins | UNIQUE(customer_code) | 单插件激活校验 |
| plugins | INDEX(state) | Settings 列表过滤 |
| plugins | INDEX(signature_status) | 异常诊断 |
| plugin_state | PRIMARY KEY(plugin_id) | 1:1 查询 |
| plugin_state | INDEX(customer_code) | 跨 ID 查询 |
| plugin_audit_log | INDEX(customer_code, occurred_at) | 时序查询 |
| plugin_audit_log | INDEX(event_type) | 异常事件过滤 |
| plugin_config_versions | UNIQUE(customer_code, plugin_version, config_key) | upsert |

### 9.2 表大小预估

| 表 | 行数（10 客户上限）| 单行 | 总大小 | SQLite 索引 | PG |
|---|---|---|---|---|---|
| plugins | 10 | ~5 KB（含 manifest_json） | 50 KB | < 1 MB | < 1 MB |
| plugin_state | 10 | ~500 B | 5 KB | < 1 MB | < 1 MB |
| plugin_audit_log | 10K（90 天）| ~500 B | 5 MB | ~1 MB | ~5 MB |
| plugin_config_versions | 100 | ~300 B | 30 KB | < 1 MB | < 1 MB |

→ 整个插件元数据 < 10 MB，**对当前数据库无压力**。

### 9.3 运维建议

- 每天定时任务清 90 天前 audit_log
- 每次主程序重启时调 `cleanup_plugin_audit_log()`
- Settings 页提供"导出 audit_log"按钮（CSV）

---

## 十、ORM 模型完整代码（直接复制可用）

将 `backend/models/plugin_models.py` 完整定义如下：

```python
# backend/models/plugin_models.py
"""插件系统数据库表（v3.7+）

在 main.py 第 13 行后 import 触发注册:
    from backend.models import plugin_models  # noqa: F401
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, JSON, Text,
    UniqueConstraint, Index, ForeignKey
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from backend.db.database import Base


# ==========================================
# 1. 已安装插件
# ==========================================
class PluginInstall(Base):
    __tablename__ = "plugins"

    id = Column(Integer, primary_key=True, index=True)

    customer_code = Column(String(20), nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=False)
    plugin_version = Column(String(50), nullable=False)
    tier = Column(Integer, nullable=False)

    manifest_json = Column(JSON, nullable=False)
    manifest_sha256 = Column(String(64), nullable=False)

    signature_status = Column(String(20), nullable=False, default="unknown")
    public_key_fingerprint = Column(String(16), nullable=True)
    signed_by = Column(String(64), nullable=True)
    signed_at = Column(DateTime(timezone=True), nullable=True)

    install_path = Column(String(500), nullable=False)

    state = Column(String(20), nullable=False, default="installed")
    error_msg = Column(Text, nullable=True)
    error_code = Column(String(64), nullable=True)

    installed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    last_loaded_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    state_record = relationship("PluginState", uselist=False,
                                 cascade="all, delete-orphan", lazy="joined")

    __table_args__ = (
        Index("ix_plugins_state", "state"),
        Index("ix_plugins_signature_status", "signature_status"),
    )


# ==========================================
# 2. 运行时状态
# ==========================================
class PluginState(Base):
    __tablename__ = "plugin_state"

    plugin_id = Column(Integer, ForeignKey("plugins.id", ondelete="CASCADE"),
                       primary_key=True)
    customer_code = Column(String(20), nullable=False, index=True)

    last_load_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_load_ok_at = Column(DateTime(timezone=True), nullable=True)
    load_attempt_count = Column(Integer, nullable=False, default=0)
    load_failure_count = Column(Integer, nullable=False, default=0)
    last_load_duration_ms = Column(Integer, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_msg = Column(Text, nullable=True)

    cpu_avg_pct = Column(Integer, nullable=True)
    memory_mb = Column(Integer, nullable=True)
    background_thread_count = Column(Integer, nullable=True)

    hook_invocation_count = Column(Integer, nullable=False, default=0)
    hook_failure_count = Column(Integer, nullable=False, default=0)
    hook_avg_duration_ms = Column(Integer, nullable=True)

    registered_routes_count = Column(Integer, nullable=False, default=0)
    registered_adapters_count = Column(Integer, nullable=False, default=0)
    registered_hooks_count = Column(Integer, nullable=False, default=0)
    registered_tables_count = Column(Integer, nullable=False, default=0)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


# ==========================================
# 3. 审计日志
# ==========================================
class PluginAuditLog(Base):
    __tablename__ = "plugin_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    customer_code = Column(String(20), nullable=False, index=True)
    plugin_version = Column(String(50), nullable=True)

    event_type = Column(String(40), nullable=False, index=True)
    event_detail = Column(JSON, nullable=True)

    operator_id = Column(Integer, nullable=True)
    occurred_at = Column(DateTime(timezone=True), server_default=func.now(),
                          nullable=False, index=True)

    __table_args__ = (
        Index("ix_audit_log_cc_time", "customer_code", "occurred_at"),
    )


# ==========================================
# 4. default_config 应用历史
# ==========================================
class PluginConfigVersion(Base):
    __tablename__ = "plugin_config_versions"

    id = Column(Integer, primary_key=True, index=True)
    customer_code = Column(String(20), nullable=False, index=True)
    plugin_version = Column(String(50), nullable=False)
    config_key = Column(String(200), nullable=False)
    default_value = Column(JSON, nullable=True)
    applied = Column(Boolean, nullable=False, default=False)
    applied_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("customer_code", "plugin_version", "config_key",
                         name="uq_pcv_cc_pv_key"),
    )
```

---

## 十一、与现有 BUG-1 的协同（不阻塞，但要先修）

inventory/05 的 BUG-1：

```python
# backend/core/config.py:43~ 现状
def _fix_db_paths(db_path, old_base, new_base):
    ...
    cursor.execute("UPDATE ml_models SET file_path = ...")  # ← 表名错: 应为 models
    cursor.execute("UPDATE model_conversions SET file_path = ...")
```

**修复必要性**：
- 该函数在客户工控机首次升级到新数据目录时执行（`config.py:148`）
- 错误的表名导致**模型路径迁移静默失败**（功能错误但不报错）
- 客户表现：升级后所有 model.file_path 仍是旧绝对路径 → 模型加载 404

**修复时机**：
- 不阻塞 design/03 表的创建
- 但**v3.7 发版前必须修**（与新 plugin 表一并 ship）
- 修复方法：`ml_models` → `models`（一行字符串改动）

---

## 十二、本文决策摘要

| 决策点 | 值 |
|---|---|
| 主程序新增 plugin 相关表 | 4 张（plugins / plugin_state / plugin_audit_log / plugin_config_versions） |
| 插件自家表命名空间 | `p_{customer_code}_*` |
| 插件自家类命名空间 | `Plugin{CustomerCode}*` |
| ORM 注册方式 | 单一全局 `Base`（与主程序共用） |
| 启动建表方式 | 复用 `Base.metadata.create_all` + 新增 `migrate_plugin_tables()` |
| JSON 字段类型 | `JSON`（不用 PG 专属 JSONB） |
| 审计日志保留 | 90 天 |
| 卸载默认行为 | 保留自家表（drop_tables=False） |
| 插件升级 ALTER 责任 | 插件自己（主程序提供 helper） |
| PG 迁移路线 | v3.7 SQLite-only → v3.8 双跑 → v4.0 PG 默认 |
| PG schema 隔离 | v5.0 备选（v4.0 不强制） |
| BUG-1 修复时机 | v3.7 发版前必修（一行改） |

---

**本文最后更新**：2026-05-08
**事实校验**：基于现有 `backend/db/database.py` + `backend/main.py:44~` 启动流程 + design 00 PG 迁移确认
**下一文档**：design/04_tier1_theme.md（主题包加载器）
