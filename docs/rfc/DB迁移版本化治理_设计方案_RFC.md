# RFC：数据库迁移版本化治理（设计方案，待评审）

- 状态：**草案（评审通过前不动代码）**
- 日期：2026-07-03
- 关联：第五期治理计划第 4 项；PG 迁移前置工程
- 评审人：主作者

---

## 1. 问题：现状为什么撑不住了

启动时迁移逻辑集中在 `backend/main.py: migrate_database()`（约 200 行），机制是：

1. 一张 109 条的补列清单 `(表, 列, 类型)`，每次启动全量遍历，用 SQLAlchemy inspector 逐表逐列探查"不存在才 ALTER"。
2. 一段方言归一（PG 上 `BOOLEAN DEFAULT 1→TRUE`、`JSON→JSONB`）。
3. 一个特例块（v3.10 阶段 5 `DROP TABLE operators`）。
4. 建表本身依赖 `Base.metadata.create_all()`（只建新表，不改旧表）。

**三个真实痛点**：

| 痛点 | 后果 |
|---|---|
| 无版本概念，109 条无序堆积在一个 list 里 | 看不出"哪个版本引入了哪些列"；每次发版往里追加，list 只增不减；review 时 diff 淹没在 200 行函数里 |
| 每次启动全量探查 109 项 | 启动变慢（37+ 张表 × inspector 反射）；且只能表达"补列"这一种操作——改类型 / 建索引 / 回填数据 / 删列都塞不进去，只能写特例块（operators 那种），特例块会越积越多 |
| 与 Alembic 双轨割裂 | `alembic/` 已有 PG baseline（v3.7.0，db-matrix CI 在用），但 SQLite 客户机走的是 `migrate_database()`——同一个 schema 变更要维护两处，漂移只是时间问题 |

**约束（不可妥协）**：

- 客户机是 Windows 工控机一键安装升级，**没有人工执行迁移命令的机会**——迁移必须仍在后端启动时自动完成。
- 老客户可能从任意历史版本直接跳到最新版（v3.1 → v3.31 都要能升），**不能丢数据**。
- SQLite（现役全部客户）与 PostgreSQL（规划中）都要走通。
- CI 编译后 `main.py` 可能是 `.pyd`，迁移代码要能被编译、不依赖读自身源码。

## 2. 备选方案

### 方案 A：全面切 Alembic（SQLite + PG 统一，启动时程序化 `upgrade head`）

- 优点：业界标准、版本链天然、autogenerate 省手写。
- 缺点：SQLite 改列要走 batch_alter（建影子表拷数据），对 37+ 张表的老库风险高；Alembic 的 `alembic_version` 单头链与"老客户任意版本起跳"要额外兜底；运行时引入 alembic 依赖 + 目录（编译/打包面扩大）；**迁移成本最高、动的是所有客户的升级路径**。

### 方案 B（推荐）：仓库内轻量版本化迁移注册表，Alembic 保留给 PG 工程

保持"启动时自动迁移"的机制不变，只把**组织方式**从"一个大 list"改成"按版本编号的迁移模块目录"：

```
backend/db/migrations/
├── __init__.py          # 注册表 + runner（apply_pending(engine)）
├── m0000_legacy.py      # 现有 109 条补列 + operators 特例，原样冻结（幂等语义不变）
├── m0001_v3_32_xxx.py   # 从下一个版本起，每个 schema 变更一个文件
└── ...
```

- 每个迁移模块暴露 `MIGRATION_ID`（文件名前缀数字）+ `apply(conn, dialect)`；可以做补列，也可以建索引 / 回填数据 / 删表——**特例块从此有正式归宿**。
- 新增 `schema_migrations` 表（一列 `migration_id` + `applied_at`）记录已应用项；runner 启动时按编号升序应用缺失项，事务包裹，失败即停并保留原库。
- **老库兼容关键**：`m0000_legacy` 不看 `schema_migrations`，永远按现状的 inspector 幂等探查跑一遍（任意历史版本的库跑完 m0000 就到达 v3.31 基线），跑完记账；之后的 m0001+ 才走"记账跳过"。这样任意老版本起跳都安全，且新迁移不再每次启动全量探查。
- 方言归一函数原样搬进 runner，SQLite / PG 同一套迁移模块跑通；**Alembic 保留现有角色**（PG baseline + db-matrix CI + 未来 SQLite→PG 一次性数据搬迁工程），不进客户机运行时。

### 方案 C：维持现状，只给 list 加版本注释分节

- 成本最低，但三个痛点一个都没解：不解决特例块、不解决全量探查、不解决双轨。**否决**。

## 3. 推荐结论与理由

**选方案 B。** 理由一句话：它把"版本化、可表达任意操作、记账跳过"三个收益拿全，同时**完全不改变客户升级的外部行为**（仍是装新包→启动→自动迁移），风险面远小于方案 A；Alembic 不下放到客户机，双轨职责从"重叠"变为"分工"（B 管运行时升级，Alembic 管 PG 工程）。

## 4. 落地步骤（评审通过后执行，估 2 天）

1. 建 `backend/db/migrations/` + runner + `schema_migrations` 表（0.5 天）。
2. `migrate_database()` 的 109 条 + operators 特例**原样平移**进 `m0000_legacy.py`，`main.py` 改调 `apply_pending(engine)`；行为逐字对齐，不做任何"顺手优化"（0.5 天）。
3. 测试矩阵（0.5 天）：全新空库 / v3.20 时代样例库 / v3.30 样例库 三档升级到 HEAD，SQLite + PG 双方言（复用 db-matrix CI 骨架），比对 `inspect` 出的最终 schema 与 `Base.metadata` 一致。
4. 文档与护栏同步（0.5 天）：
   - AGENTS.md 不变量第 8 条改为「改 ORM Schema 后必须新增 `backend/db/migrations/mXXXX_*.py` 迁移文件」；
   - `modify-model` skill 迁移章节同步；
   - CORE_FILES 评估是否纳入 runner（迁移逻辑也是核心 IP）。

## 5. 风险与开放问题（评审时定）

| 风险/问题 | 应对 |
|---|---|
| m0000 与新 ORM 声明漂移（有人又直接改 list） | 平移后在 `migrate_database()` 原位置留断言桩：函数体替换为 `raise RuntimeError("迁移已版本化, 去 backend/db/migrations/")`，防止旧习惯回流 |
| `schema_migrations` 表本身建失败（磁盘满/只读） | runner 建表失败 → 降级为"仅跑 m0000 幂等路径"并打显著日志，不阻断启动（与现状容错一致） |
| 回滚 | 与现状一致不提供 down（客户机升级前安装器已备份 DB）；是否要求每个迁移写 `rollback()` 存根，评审定 |
| 迁移文件要不要进 Nuitka 白名单 | 倾向进（防止客户改动迁移逻辑），评审定 |
