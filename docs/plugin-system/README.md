# 天骏 AI 视觉系统 — 插件系统设计与盘点（feat/plugin-config）

> **本目录是插件系统的单一信息源（SSOT）**。任何与"插件 / customer_code / 三档分级 / 签名 / PG 迁移"相关的设计决策都必须在此存档。

---

## ⚡ 30 秒速览

天骏主程序当前对每个客户**硬改源码**（颜色、菜单、字段、Hook），导致维护成本随客户数线性爆炸。本目录把所有客户化需求收敛到**单一插件包 `.tjvplugin`**：

```
                       Tier 1 (主题)        Tier 2 (UI)         Tier 3 (全栈)
                       ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
.tjvplugin (ZIP)  ──→  │ CSS/Logo    │ →→→ │ + Vue 路由  │ →→→ │ + 后端 API  │
                       │ i18n/隐菜单 │     │ + Pinia store│    │ + Hook/Adapter│
                       └─────────────┘     └─────────────┘     │ + ORM 表/线程│
                                                                └─────────────┘
                       1~2h 出货           1~2d 出货            3~10d 出货
```

- **签名**：RSA-PSS-4096 + 客户码 HMAC + files_digest 三层（design 02）
- **隔离**：每个客户唯一 `customer_code`，license × manifest × 目录三方校验
- **错误**：5 层错误隔离，单插件崩溃不影响主程序
- **目标**：v3.7 上线骨架，v3.8 PG 双跑，v4.0 PG 默认，v5.0 移除 SQLite

---

## 📚 文档索引（17234 行）

### 🔍 [盘点 inventory/](./inventory/)（4757 行）

主程序现状的全景盘点。**实施前必读**——这是设计的事实基础。

| 文件 | 行 | 看点 |
|---|---|---|
| [01_module_map.md](./inventory/01_module_map.md) | 656 | 模块依赖图 / 启动顺序 / **10 个插件挂载点候选** |
| [02_data_flow.md](./inventory/02_data_flow.md) | 1051 | 扫码→检测→MES→显示完整链路 / **13 个插件插入点 P1~P13** |
| [03_extension_points.md](./inventory/03_extension_points.md) | 1520 | **30 个现有扩展点**接口契约（A=配置 18 / B=注册 7 / C=Hook 9 / D=前端 8）|
| [04_io_boundaries.md](./inventory/04_io_boundaries.md) | 914 | 264 HTTP 端点 / 11 IPC / 6 视频源 / 文件系统边界 |
| [05_tech_debt.md](./inventory/05_tech_debt.md) | 616 | **72 项技术债** + 6 严重 bug + 优先级表 |

### 🎨 [设计 design/](./design/)（11562 行）

按"自顶向下 + 由浅入深"组织。**第一次读建议按数字顺序通读**。

| 文件 | 行 | 看点 |
|---|---|---|
| [00_overview.md](./design/00_overview.md) | 777 | 总体设计骨架 / 三档分级 / 8 个开放问题（已选 B 全部 recommended） |
| [01_manifest_schema.md](./design/01_manifest_schema.md) | 1609 | `plugin.json` 22 字段 / 26 capabilities / 33 错误码 |
| [02_signature.md](./design/02_signature.md) | 1179 | 签名机制 / 私钥管理 / 6 攻击场景 |
| [03_database.md](./design/03_database.md) | 1031 | 4 主表 + `p_{cc}_*` 插件表 / SQLite vs PG / 3 段迁移 |
| [04_tier1_theme.md](./design/04_tier1_theme.md) | 1046 | 主题包加载（CSS var / Logo / i18n / 隐菜单）|
| [05_tier2_ui.md](./design/05_tier2_ui.md) | 1415 | UI 包（fetch+Blob+import / importmap / wrapPluginView） |
| [06_tier3_fullstack.md](./design/06_tier3_fullstack.md) | 1518 | 全栈包（PluginManager / 8 Hook / 5 层错误隔离 / **F1~F15 前置改造**） |
| [07_distribution.md](./design/07_distribution.md) | 1592 | 7 个 CLI 工具 / `.tjvplugin` 格式 / CI / dev mode |
| [08_examples.md](./design/08_examples.md) | 1395 | 三档 demo / 客户操作单页 / 作者上手 / FAQ |

### 📋 评审与运维资产

| 文件 | 用途 |
|---|---|
| [REVIEW_CHECKLIST.md](./REVIEW_CHECKLIST.md) | **主作者必读**——P0/P1/P2/P3 共 84 项决策点 + 三轮会议节奏 |
| [customer-codes.md](./customer-codes.md) | 客户码注册表 + 命名 SOP + 撤销流程 |
| `../scripts/plugin/` | CLI 工具（gen-master-keypair / pack / sign / verify ...） |

---

## 🧭 按角色阅读路线

### 角色 A — 主作者（你）

> 任务：拍板设计 → 生成密钥 → 实施 v3.7 → 签发首个客户

```
Day 0  ─ 通读  REVIEW_CHECKLIST.md §2 (P0 21 项)         60 min
Day 0  ─ 抽读  design/00, design/02 §八 §九               45 min
Day 0  ─ 决策  P0 拍板会议（建议 §6 第一轮 2h）
Day 1  ─ 跑    scripts/plugin/gen-master-keypair.py      30 min
Day 1  ─ 写    customer-codes.md §3.1 首批客户         15 min
Day 2~13 ─ 实施  按 design/06 §九 F1~F15 顺序推进        12.5d
Day 14   ─ 签发  scripts/plugin/sign-plugin.py 签首个    30 min
```

### 角色 B — 实施工程师

> 任务：F1~F15 主程序前置改造

```
1. 通读 inventory/03 (扩展点契约)                       60 min
2. 抽读 design/06 §六（Hook 接入点改造代码）            60 min
3. 抽读 design/03 §十 (4 张主表 ORM 模型)               30 min
4. 按 design/06 §九 表格挑认领单项 → 开 PR
```

> ⚠️ 涉及修改 `source.py` 必读 `.claude/skills/modify-source/SKILL.md`
> ⚠️ 涉及修改 ORM 表必读 `.claude/skills/modify-model/SKILL.md`

### 角色 C — 插件作者（外协 / 二次开发方）

> 任务：根据客户需求开发 `.tjvplugin`

```
1. 读 design/08 §四 "插件作者上手指南"                  30 min
2. 选档：Tier 1 / 2 / 3，参考 plugins-examples/ 对应 demo
3. 本地用 scripts/plugin/dev-plugin.py 跑（DEBUG_MODE）
4. 用 scripts/plugin/pack-plugin.py 出未签 ZIP
5. 提交主作者签名（送签流程见 design/07 §三）
```

### 角色 D — 工厂运维 / IT

> 任务：在客户工控机安装 `.tjvplugin`

```
读 design/08 §三 "客户操作单页"（5 分钟，纸面打印贴墙）
```

### 角色 E — 新加入团队成员（先把项目摸一遍）

```
1. 看 ../AGENTS.md (主项目手册)                          60 min
2. 看 inventory/01_module_map.md                         30 min
3. 看 inventory/02_data_flow.md                          30 min
4. 接到具体任务时再回来翻 design/对应章节
```

---

## 🎯 当前实施状态（2026-05-09）

| 阶段 | 进度 | 文件 |
|---|---|---|
| 盘点（v3.7 输入） | ✅ 完成 | inventory/01~05 |
| 设计（v3.7 输入） | ✅ 完成 | design/00~08 |
| 评审清单 | ✅ 完成 | REVIEW_CHECKLIST.md |
| 客户码注册表 | ✅ 初版 | customer-codes.md（仅 3 保留码，待主作者补真实客户）|
| 主作者密钥工具 | ✅ 完成 | scripts/plugin/gen-master-keypair.py |
| BUG-1 修复 | ✅ 2026-05-09 | backend/core/config.py:57 |
| INCONSIST-2 验证 | ✅ 2026-05-09 误报撤销 | inventory/05 |
| **P0 决策会议** | ⏳ 待主作者 | REVIEW_CHECKLIST §2 |
| 主作者生成密钥 | ⏳ 待主作者 | scripts/plugin/gen-master-keypair.py |
| F1~F15 主程序前置改造 | ⏳ 0 / 14（F14 已完成 / F15 撤销） | design/06 §九 |
| 三档 demo 插件 | ⏳ 仅设计 | plugins-examples/（目录骨架待建） |

---

## 📐 关键架构决策速查

| 议题 | 决策 | 来源 |
|---|---|---|
| 三档分级 | Theme / UI / Fullstack 渐进 | design 00 §第二节 |
| 签名 | RSA-PSS-4096 + HMAC-SHA256 + files_digest | design 02, Q1 |
| 包格式 | ZIP + `.tjvplugin` 后缀 | design 07, Q3 |
| 客户码 | 全小写, 3~20 字符, `^[a-z][a-z0-9-]{2,19}$` | design 01, Q5, customer-codes |
| 多插件激活 | v3.7 单插件 / v4.5 评估多插件 | design 00, M1 |
| 资源限制 | 软警告（不强 kill）| Q4 |
| GPU 默认 | CPU 默认 / manifest 声明才用 GPU | Q6 |
| 加载失败 | 静默 + Settings 错误显示 | Q8 |
| 主程序入侵度 | 通过 Hook，不改 mixin | Q7 |
| 隔离 / 错误 | 5 层（验签 / 模块 / Hook / 线程 / 路由）| design 06 §七 |
| DB 迁移 | v3.7 SQLite / v3.8 双跑 / v4.0 PG / v5.0 弃 SQLite | design 03 §七 |
| 私钥 | USB×2 + 1Password + 保险柜 | design 02 §八 |

---

## 🔗 相关文档（外部）

- 主项目地图 `../../AGENTS.md`
- 当前 worktree 起点 `../../START_HERE.md`
- Skills 索引 `../../.claude/skills/*`
  - 修改后端 API 必读 `add-api-endpoint/SKILL.md` / `modify-api/SKILL.md`
  - 修改前端必读 `modify-frontend/SKILL.md`
  - 修改 ORM 必读 `modify-model/SKILL.md`
  - 修改 source.py 必读 `modify-source/SKILL.md`
  - API 对齐排查 `api-sync/SKILL.md`

---

## 🆘 紧急联系

| 场景 | 文件 |
|---|---|
| 客户机插件加载失败 | design/08 §七（错误码表） |
| 主作者私钥丢失 | design/02 §九（应急 SOP） |
| 客户机 license 与 plugin 不匹配 | customer-codes §8 |
| 想撤销已签发的插件版本 | design/02 §九.4 |

---

**最后更新**：2026-05-09
**贡献规则**：在本目录新增/修改 `.md` 都要同步更新本 README 索引和"实施状态"表
