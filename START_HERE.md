# 插件配置 worktree —— 给 AI agent 看

> 你打开了这份文档，说明你被分到这个工作区干活。请先读完这份再动手。

---

## 一、你在哪儿

| 项 | 值 |
|---|---|
| 分支 | `feat/plugin-config` |
| 父分支 | `main`（走法 B，独立分支，做完直接合主线）|
| 主仓库地址 | `/home/qianqian/桌面/word/tianjun副本/`（用户在那个 cursor 窗口和我答疑）|
| 当前 worktree | `/home/qianqian/桌面/word/tianjun-plugin/`（就是你现在这里）|
| 已合并 main | 是（`origin/main` 已 merge 进来）|

---

## 二、本分支已落地的能力（v3.7.0 主题）

> ⚠️ 接手前先读：本分支已经积累了 **5 个有效 commit**，跨度从插件设计到 PG 迁移到 CI。
> 别再"重头开始"或"重新设计"，请以增量方式接力。

### 2.1 插件系统（已上线）

- **设计文档** `docs/plugin-system/`（manifest schema / 签名格式 / 三层 tier 定义 / 客户码登记表）
- **CLI 工具链** `scripts/plugin/`（pack / sign / verify / install / inject-public-key / lint-docs）
  - `sign-plugin.py` 支持 `PLUGIN_KEY_PASSWORD` 环境变量旁路 tty（CI 友好）
- **3 层示例插件** `plugins-examples/{tier1-theme,tier2-ui,tier3-fullstack}/`
- **后端**
  - `backend/plugin_system/{verifier,manager}.py`：签名校验 + 生命周期管理
  - `backend/api/plugins.py`：`/api/v1/plugins/*` REST API（list/upload/activate/deactivate/uninstall/audit）
  - `backend/models/plugin_models.py`：4 张 ORM 表（plugins / plugin_state / plugin_audit_log / plugin_config_versions）
- **前端**
  - `frontend/src/api/plugins.js`：axios wrapper
  - `Settings/index.vue` 已挂"插件管理" tab
- **测试**
  - 8 个单元 `tests/plugin_system/test_*.py`
  - 3 个 BDD `tests/features/plugin_*.feature` + `tests/step_defs/test_plugin_*.py`
  - 1 个 E2E `tests/e2e_browser/test_plugin_page.py`
- **CI** `.github/workflows/plugin-tooling.yml`（3 job：static-checks / signing-roundtrip / bdd-plugin）

### 2.2 数据库迁移（SQLite → PostgreSQL，已完成）

- **dialect-aware 引擎层** `backend/db/database.py`：根据 `DATABASE_URL` 前缀自动切 SQLite / PG，连接池/PRAGMA 分别处理
- **跨库 SQL 兼容层** `backend/db/sql_compat.py`：`hour_minute()` / `date_str()` 替代 `func.strftime`，已替换 `backend/api/{sessions,sessions_export,reports}.py` 三处调用点
- **Alembic** `alembic.ini` + `alembic/env.py` + `alembic/versions/2026_05_11_0040-baseline_v3_7_0.py`（走 `Base.metadata.create_all` 规避 FK 顺序坑）
- **Docker Compose** `docker-compose.yml`（PG 16-alpine on 5433）+ `.env.example` + `scripts/db/initdb/01-extensions.sql`
- **数据搬迁脚本** `scripts/db/sqlite_to_pg.py`（按 FK 排序、TRUNCATE 重置序列、dry-run）
- **多客户 schema 隔离** 走法和文档：`docs/database-migration/README.md`
- **测试** `tests/conftest.py` 自动检测 `DATABASE_URL` 切 PG，schema 隔离走 DSN options
- **CI** `.github/workflows/db-matrix.yml`（SQLite + PG 双库矩阵）

### 2.3 测试体系

- 三层测试基建已对齐 main 分支（synthetic 虚拟剧本、BDD、Playwright E2E）
- 测试态兼容路由 `backend/api/test_compat_routes.py`（`RUNTIME_MODE=test` 才挂）解决了原本 14 个条件性 skip 的 BDD
- **当前回归状态**：
  - SQLite：162 / 162 passed
  - PostgreSQL：211 / 211 passed
  - Plugin 单测+BDD：79 / 79 passed

---

## 三、必读 skill（按场景）

| 你要做的事 | 必读 skill |
|---|---|
| 加新的 KV 配置项到 SystemConfig | `modify-model`（改 ORM 字段）|
| 加 / 改后端 API | `add-api-endpoint` / `modify-api` |
| 改前端页面 / 加新视图 | `modify-frontend` |
| 涉及 Project 配置 7 个 JSON 字段 | `modify-project-config` |
| 做完想合回 main | `merge-branch` |
| 改 PG / Alembic / 跨库 SQL | 看 `docs/database-migration/README.md` 第 4-6 节 |
| 改插件签名/打包逻辑 | 看 `docs/plugin-system/` + `tests/plugin_system/test_sign_verify_roundtrip.py` |

skill 路径都在 `.claude/skills/<name>/SKILL.md`。

---

## 四、首次启动需要做的事

### 4.1 SQLite 模式（轻量本地开发）

```bash
cd /home/qianqian/桌面/word/tianjun-plugin/

# 1. Python 环境（worktree 共享同一个 conda env）
conda activate tianjun

# 2. 前端依赖（每个 worktree 都要单独装一次，因为 node_modules 不共享）
cd frontend && npm install && cd ..

# 3. 启后端（注意端口！主仓库可能在跑 8001，这里用 8002）
RUNTIME_MODE=test TIANJUN_TEST_MODE=1 \
  python -m uvicorn backend.main:app --host 0.0.0.0 --port 8002 --no-access-log

# 4. 启前端 dev server（新开 terminal，端口 5174 避免和主仓库冲突）
cd frontend
VITE_API_BASE_URL=http://localhost:8002/api/v1 npm run dev -- --port 5174
```

### 4.2 PostgreSQL 模式（多客户场景）

```bash
# 1. 起 PG 容器（首次会跑 scripts/db/initdb/01-extensions.sql）
docker compose up -d postgres

# 2. 跑 Alembic baseline
DATABASE_URL=postgresql+psycopg2://tianjun:tianjun_dev_pwd@127.0.0.1:5433/tianjun \
  alembic upgrade head

# 3. (可选) 把已有 SQLite 数据搬过去
DATABASE_URL=postgresql+psycopg2://tianjun:tianjun_dev_pwd@127.0.0.1:5433/tianjun \
  python scripts/db/sqlite_to_pg.py --sqlite backend/sql_app.db --truncate-first

# 4. 启后端（指向 PG）
DATABASE_URL=postgresql+psycopg2://tianjun:tianjun_dev_pwd@127.0.0.1:5433/tianjun \
RUNTIME_MODE=test TIANJUN_TEST_MODE=1 \
  python -m uvicorn backend.main:app --host 0.0.0.0 --port 8002 --no-access-log
```

详见 `docs/database-migration/README.md`。

### 4.3 端口分配表（避免和其它 worktree 抢端口）

| worktree | backend | frontend dev | PG |
|---|---|---|---|
| `tianjun副本`（main） | 8001 | 5173 | — |
| `tianjun-plugin`（这里） | **8002** | **5174** | **5433** |
| `tianjun-multimodel` | 8003 | 5175 | — |

> 启动前用 `ss -ltn | grep -E ':(8002\|5174\|5433)\b'` 检查端口是否空闲。

---

## 五、必须遵守的项目守则

1. **一次只做一件事**：写完一个原子改动就汇报，不要闷头连改 5 个让用户审一堆
2. **改前先做影响分析**：动 source.py / ORM / API / 前端都要先读对应 modify-* skill
3. **不主动 commit / push**：等用户明确指令
4. **中文回复**，技术术语保留英文
5. **回复格式**：`遵守协议：已确认` 开头 → 结论先行 → 关键理由 → 风险

---

## 六、Commit 信息建议

```
feat(plugin): 加插件启停 KV 配置
feat(plugin): 前端插件管理页框架
fix(plugin): 修复 SystemConfig 写入冲突
ci(plugin):  签名往返加 dry-run 烟测
feat(db):    Alembic 增量迁移 v3.7.1
```

---

## 七、想看主线最新改动

```bash
git fetch origin
git log --oneline origin/main..HEAD     # 你这条分支独有的提交
git log --oneline HEAD..origin/main     # 主线领先的提交
```

如果主线有重要修复要拉过来：
```bash
git merge origin/main
# 冲突解决参考 .claude/skills/merge-branch/SKILL.md
```

---

## 八、做完想合回主线

走 `.claude/skills/merge-branch/SKILL.md` 第三节"工作流 A：短命特性分支合回 main"。
**禁止你自己 push main**，等用户拍板。

合并前 checklist：
- [ ] `pytest tests/plugin_system/ tests/step_defs/ tests/test_synthetic_full_flow.py` 全绿
- [ ] PG 模式跑一遍同样的测试也全绿（参考 4.2 节起 PG 容器）
- [ ] `python scripts/plugin/lint-plugin-docs.py` 通过
- [ ] 至少一个示例插件能 pack→sign→verify→install 跑通
- [ ] `git log --oneline origin/main..HEAD` 看每个 commit 都有清晰的主题前缀

---

## 九、本分支提交历史（截至当前）

```
074c9ab ci(plugin/db): 插件签名往返 + SQLite/PG 双库矩阵 + workflow 索引
5af3b48 feat(db):     SQLite → PostgreSQL 迁移 + Alembic + 多客户 schema 隔离
45abe36 feat(plugin): 落地插件管理 API/UI + 测试基建对齐三层全绿
ddb2bbd Merge        origin/main into feat/plugin-config (测试基建)
e31b511 feat(plugin): 插件系统设计 + 签名/打包/校验工具链 + 8 单测
```

> 接手时如果看到比这更长的历史，说明已有人后续接力，请用 `git log --oneline origin/main..HEAD` 看真实最新提交。
