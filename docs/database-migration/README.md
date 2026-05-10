# 数据库迁移与多客户部署（v3.7.0）

本目录是 Tianjun v3.7.0 把 **SQLite → PostgreSQL** 切换的完整运维手册。
适用对象：现场实施工程师、客户 IT、私有化部署 SRE。

---

## 1. 为什么从 SQLite 切到 PostgreSQL

| 维度 | SQLite | PostgreSQL |
|---|---|---|
| 部署 | 单文件，零依赖 | 需要常驻服务 |
| 并发写 | 全库锁，多线程下出 `database is locked` | MVCC，读不阻写 |
| 多客户隔离 | 一份产品 = 一个 DB 文件，扩展费劲 | schema 隔离，一台 PG 管 N 个客户 |
| 备份/恢复 | `cp file.db` | `pg_dump`/`pg_restore`，热备 |
| 字段类型 | 弱类型，TEXT-everything | 严格类型 + JSONB + 索引丰富 |

切换后：
- 单客户单机仍可跑（直接连本地 PG 即可）
- 多客户集中部署只需要一台 PG 实例
- 集群主从模式下 `box_aggregations` 等高并发写表性能直线上升

---

## 2. 起一个 PostgreSQL（开发/单机部署）

```bash
cp .env.example .env       # 必要时改用户名/密码/端口
docker compose up -d postgres
docker compose ps          # 看到 healthy 才算就绪
```

健康检查 / 端口冲突：
```bash
ss -ltn | grep 5433        # 默认对外暴露 5433（避免占用本机 5432）
docker exec tianjun_pg pg_isready -U tianjun -d tianjun
```

---

## 3. 后端连 PG 的最小配置

只需要一个环境变量：

```bash
export DATABASE_URL='postgresql+psycopg2://tianjun:tianjun_dev_pwd@127.0.0.1:5433/tianjun'
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
```

启动日志会打印：
```
[DIAG] main.py: dialect = postgresql
[DIAG] main.py: DB URI = postgresql+psycopg2://...
```

不设 `DATABASE_URL` 时回退到 SQLite（`sqlite:///{TIANJUN_DATA_DIR}/sql_app.db`），便于老用户平滑过渡。

可选连接池调参（默认基本够用）：
```bash
export DB_POOL_SIZE=10           # 持久连接数
export DB_MAX_OVERFLOW=20        # 高峰临时连接
export DB_POOL_RECYCLE=1800      # 连接复用秒数
```

---

## 4. 建表（两条路）

### 4.1 推荐：Alembic（生产）

```bash
DATABASE_URL='postgresql+psycopg2://...' alembic upgrade head
```

输出：
```
INFO  [alembic.runtime.migration] Running upgrade  -> 24693b55b51d, baseline_v3_7_0
```

baseline 之后所有 schema 变更都走标准 alembic 流程：
```bash
DATABASE_URL='...' alembic revision --autogenerate -m "feat_xxx"
DATABASE_URL='...' alembic upgrade head
```

### 4.2 简化：让 backend 启动时自动 `create_all`

后端 `main.py` 顶层会跑 `Base.metadata.create_all(bind=engine)`，幂等，缺啥建啥。
配合启动时的 `migrate_database()`（用 `inspect` 检测列、自动 `ALTER TABLE ADD COLUMN`），
**单机/小型部署可以完全不用 Alembic**，直接启动后端就把 schema 拉起来。

但生产建议用 Alembic，便于回滚和多机统一版本。

---

## 5. 老数据搬迁：SQLite → PG

```bash
# 1. PG 端先把 schema 建好
DATABASE_URL='postgresql+psycopg2://...' alembic upgrade head

# 2. 干跑预览每张表的行数，不写库
python scripts/db/sqlite_to_pg.py \
    --sqlite /path/to/old/sql_app.db \
    --pg postgresql+psycopg2://tianjun:pwd@127.0.0.1:5433/tianjun \
    --dry-run

# 3. 正式搬（--truncate-first 会清空 PG 端再灌，避免重复）
python scripts/db/sqlite_to_pg.py \
    --sqlite /path/to/old/sql_app.db \
    --truncate-first
```

脚本会：
- 按 ORM 外键依赖顺序逐表搬（父表先，子表后）
- 完成后自动 `setval` 重置 PG 序列，避免后续 INSERT 主键冲突
- 失败的表打印警告但继续搬剩下的（带 `(KeyName)` 标记跳过原因）

回滚：直接清空 PG 端 schema 再重跑 `alembic upgrade head` 即可，源 SQLite 文件未被触碰。

---

## 6. 多客户部署：schema 隔离方案

一台 PG 服务多个客户的最简方式是 **每个客户一个 schema**：

```sql
-- 客户 A
CREATE SCHEMA tianjun_acme;
GRANT ALL ON SCHEMA tianjun_acme TO tianjun;

-- 客户 B
CREATE SCHEMA tianjun_globex;
GRANT ALL ON SCHEMA tianjun_globex TO tianjun;
```

后端启动时通过 DSN 的 `options` 参数把 search_path 注入：

```bash
# 客户 A 实例
export DATABASE_URL='postgresql+psycopg2://tianjun:pwd@pg-host:5432/tianjun?options=-csearch_path%3Dtianjun_acme%2Cpublic'

# 客户 B 实例
export DATABASE_URL='postgresql+psycopg2://tianjun:pwd@pg-host:5432/tianjun?options=-csearch_path%3Dtianjun_globex%2Cpublic'
```

每个客户用独立的 alembic 升级路径（同一个 baseline，schema 隔离）：

```bash
DATABASE_URL='postgresql+...?options=-csearch_path%3Dtianjun_acme%2Cpublic' alembic upgrade head
```

### 命名约定

| 场景 | schema 名 | 备注 |
|---|---|---|
| 主生产 | `public` | 默认 schema |
| 单客户私有 | `tianjun_<customer_code>` | `customer_code` = 插件系统里的客户码（小写、无下划线开头）|
| 测试/E2E | `tianjun_test` / `tianjun_e2e` | conftest 自动 wipe+rebuild |
| Alembic dev | `tianjun_alembic` | 仅本地开发生成迁移用 |

### 为什么用 schema 不是数据库

- `pg_dump` 单 schema 备份恢复都比单 DB 灵活（`--schema=tianjun_acme`）
- 客户跨 schema 共享系统级表（如 license/plugin 公钥）方便
- 一台 PG 维护成本低于多 DB instance

如果某客户对隔离要求更高（合规审计），可以升级到 **每客户一个 PG 数据库**，
DSN 改为不同 `database` 路径即可，本套代码无需修改。

---

## 7. 测试在 PG 上跑

```bash
DATABASE_URL='postgresql+psycopg2://tianjun:pwd@127.0.0.1:5433/tianjun' \
RUNTIME_MODE=test TIANJUN_TEST_MODE=1 \
python -m pytest tests/
```

`tests/conftest.py` 自动会：
1. 在 DSN 后追加 `options=-csearch_path%3Dtianjun_test%2Cpublic`
2. 用一条独立 raw psycopg2 连接 `DROP SCHEMA tianjun_test CASCADE; CREATE SCHEMA tianjun_test`
3. `Base.metadata.create_all(bind=engine)` 在测试 schema 里建表
4. seed 一条 dummy project 让"项目列表非空"

不设置 `DATABASE_URL` 时回退到临时 SQLite 文件，老用法照样工作。

---

## 8. 监控与运维要点

- **连接数**：默认 pool_size=10 + overflow=20 = 单实例最高 30 个连接。多客户实例汇总不要超 PG `max_connections`（默认 100）。
- **慢查询**：建议在 PG 端打开 `log_min_duration_statement=500ms`。
- **JSONB 索引**：若发现 `projects.steps_config` / `events_config` 查询变慢，可建 `CREATE INDEX ... USING gin`（已预装 `pg_trgm` / `btree_gin` 扩展）。
- **备份**：`pg_dump -U tianjun -F c -d tianjun --schema=tianjun_acme > acme.dump`
- **恢复**：`pg_restore -U tianjun -d tianjun --clean --if-exists acme.dump`

---

## 9. 回滚到 SQLite

`unset DATABASE_URL` 后重启后端就回到 SQLite 模式。SQLite 文件路径仍是 `${TIANJUN_DATA_DIR}/sql_app.db`，老数据原封不动。

---

## 10. 常见问题

**Q：搬迁后 INSERT 报 `duplicate key violates unique constraint "...id_seq"`**
A：序列没重置好。手工重置：
```sql
SELECT setval('projects_id_seq', (SELECT MAX(id) + 1 FROM projects), false);
```

**Q：`relation "xxx" does not exist`**
A：search_path 没指对。检查 DSN 是否带 `options=-csearch_path%3D...%2Cpublic`，或在 PG 端：
```sql
SHOW search_path;
SET search_path TO tianjun_acme, public;
```

**Q：alembic 报 `Can't locate revision identified by 'xxx'`**
A：`alembic_version` 表里 stamp 的 revision 不在 `alembic/versions/` 里。要么补回那个 revision 文件，要么 `alembic stamp head` 重新校准。

**Q：JSON 列在 PG 上能用吗？**
A：能。SQLAlchemy 的 `Column(JSON)` 在 PG 上自动映射为 `JSON` 类型（不是 `JSONB`，因为旧 SQLite 数据迁移时不需要 JSONB 的额外开销）。需要 GIN 索引时手工建：
```sql
ALTER TABLE projects ALTER COLUMN steps_config TYPE jsonb USING steps_config::jsonb;
CREATE INDEX idx_projects_steps_gin ON projects USING gin (steps_config);
```
