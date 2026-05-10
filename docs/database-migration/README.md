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

---

## 11. 端到端 Runbook

### 11.1 首次部署（PG 模式）

```bash
# Step 0: 准备环境
git clone <repo> && cd tianjun-plugin
conda activate tianjun
pip install -r backend/requirements.txt
cp .env.example .env       # 编辑改密码/端口

# Step 1: 起 PG (首次会执行 scripts/db/initdb/01-extensions.sql 装 pg_trgm/btree_gin)
docker compose up -d postgres
until docker exec tianjun_pg pg_isready -U tianjun; do sleep 1; done

# Step 2: 多客户场景 → 提前为每个客户建 schema
docker exec -i tianjun_pg psql -U tianjun -d tianjun <<'SQL'
CREATE SCHEMA IF NOT EXISTS tianjun_acme;
GRANT ALL ON SCHEMA tianjun_acme TO tianjun;
SQL

# Step 3: Alembic baseline upgrade（每个 schema 独立 stamp）
DATABASE_URL='postgresql+psycopg2://tianjun:tianjun_dev_pwd@127.0.0.1:5433/tianjun?options=-csearch_path%3Dtianjun_acme%2Cpublic' \
  alembic upgrade head

# Step 4: 起后端（指向具体客户的 schema）
DATABASE_URL='postgresql+psycopg2://tianjun:tianjun_dev_pwd@127.0.0.1:5433/tianjun?options=-csearch_path%3Dtianjun_acme%2Cpublic' \
RUNTIME_MODE=production TIANJUN_DATA_DIR=/var/lib/tianjun \
  python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --workers 2

# Step 5: 健康检查
curl -s http://127.0.0.1:8001/api/v1/source/status | head -50
```

### 11.2 已有 SQLite 部署 → 升级到 PG

```bash
# Step 0: 后端先停（避免迁移时新写入丢失）
sudo systemctl stop tianjun-backend
# 或 pkill -f "uvicorn backend.main"

# Step 1: 备份 SQLite（无副作用，安全）
cp ${TIANJUN_DATA_DIR}/sql_app.db ${TIANJUN_DATA_DIR}/sql_app.backup.$(date +%F).db

# Step 2: 起 PG + 建 schema + Alembic baseline（同 11.1 Step 1-3）

# Step 3: 干跑预览迁移结果（不写库）
DATABASE_URL='postgresql+psycopg2://tianjun:pwd@127.0.0.1:5433/tianjun?options=-csearch_path%3Dtianjun_acme%2Cpublic' \
  python scripts/db/sqlite_to_pg.py --sqlite ${TIANJUN_DATA_DIR}/sql_app.db --dry-run

# Step 4: 正式搬（--truncate-first 清空目标 schema 后灌；不带它则增量 INSERT）
DATABASE_URL='postgresql+psycopg2://tianjun:pwd@127.0.0.1:5433/tianjun?options=-csearch_path%3Dtianjun_acme%2Cpublic' \
  python scripts/db/sqlite_to_pg.py --sqlite ${TIANJUN_DATA_DIR}/sql_app.db --truncate-first

# Step 5: 后端切到 PG（设置 systemd EnvironmentFile 或 .env）
echo 'DATABASE_URL=postgresql+psycopg2://tianjun:pwd@127.0.0.1:5433/tianjun?options=-csearch_path%3Dtianjun_acme%2Cpublic' \
    | sudo tee -a /etc/tianjun/backend.env

sudo systemctl start tianjun-backend

# Step 6: 跑业务 smoke（任一项目跑一个 cycle, 看 PG 端是否落 step_records）
docker exec tianjun_pg psql -U tianjun -d tianjun \
  -c "SET search_path TO tianjun_acme,public; SELECT COUNT(*) FROM step_records;"
```

### 11.3 Schema 升级（已有 PG 部署 → 后端发新版本）

```bash
sudo systemctl stop tianjun-backend

# 拉新代码 + 装新依赖
git pull && pip install -r backend/requirements.txt

# 跑增量迁移（多 schema 时挨个跑）
for schema in tianjun_acme tianjun_globex; do
  DATABASE_URL="postgresql+psycopg2://tianjun:pwd@127.0.0.1:5433/tianjun?options=-csearch_path%3D${schema}%2Cpublic" \
    alembic upgrade head
done

sudo systemctl start tianjun-backend
```

### 11.4 systemd service 示例

`/etc/systemd/system/tianjun-backend.service`：

```ini
[Unit]
Description=Tianjun AI Vision Backend
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
EnvironmentFile=/etc/tianjun/backend.env
WorkingDirectory=/opt/tianjun
ExecStart=/opt/tianjun/.venv/bin/python -m uvicorn backend.main:app \
          --host 0.0.0.0 --port 8001 --workers 2 --no-access-log
Restart=on-failure
RestartSec=5s
User=tianjun
Group=tianjun

[Install]
WantedBy=multi-user.target
```

`/etc/tianjun/backend.env`（权限 600）：

```bash
DATABASE_URL=postgresql+psycopg2://tianjun:CHANGE_ME@127.0.0.1:5433/tianjun?options=-csearch_path%3Dtianjun_acme%2Cpublic
TIANJUN_DATA_DIR=/var/lib/tianjun
RUNTIME_MODE=production
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_RECYCLE=1800
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tianjun-backend
sudo journalctl -u tianjun-backend -f
```

### 11.5 故障排查决策树

```
backend 启动失败
├─ ConnectionRefused on 5433
│  └─ docker compose ps  → 看 postgres 是否 healthy
│     └─ docker logs tianjun_pg → 看 PG 自身错误
├─ password authentication failed
│  └─ 核对 .env / EnvironmentFile 里的密码 vs docker-compose.yml POSTGRES_PASSWORD
├─ relation "xxx" does not exist
│  └─ 多半 search_path 没指对：psql 进去 SHOW search_path; 看
│     └─ 漏跑 alembic upgrade head ?
├─ duplicate key violates unique constraint "..._id_seq"
│  └─ 序列没重置：见第 10 节"常见问题"
└─ ImportError: psycopg2
   └─ pip install psycopg2-binary （已在 backend/requirements.txt）
```

