# 天军 AI 视觉检测系统 — 开发者文档

> **类型**：根索引（受众路由 + 文档地图）
> **本文不讲**：任何具体技术内容——只负责把你送到正确的文档。
> **与代码冲突时**：以代码为准，并请顺手修文档（见下方报错通道）。

---

## 你是谁？先分流

| 你是 | 先读 | 然后 |
|---|---|---|
| **主程序维护者（人类新人）** | [getting-started.md](getting-started.md) 一条线跑通环境 | [architecture/](architecture/) 建立全局心智模型 → 按模块进 [internals/](internals/) |
| **插件开发者** | `docs/plugin-system/README.md`（插件域完整文档在那里，不在本目录） | 本目录只需 [reference/plugin-sdk.md](reference/plugin-sdk.md)（能力面速查） |
| **AI agent** | 仓库根 `AGENTS.md`（地图+守则+不变量） | 按 AGENTS.md 第四节触发表进 `.claude/skills/`；需要"为什么这样设计"进 [internals/](internals/)；**翻源码前先查 `_reading_notes/` 函数级索引**（找函数/行号/状态变量/密钥与配置落点） |
| **软件开发写作者/实施顾问** | [architecture/01_系统上下文.md](architecture/01_系统上下文.md) | [quality-map.md](quality-map.md) 了解验证体系 |

## 文档地图（Diátaxis 四象限）

| 象限 | 目录 | 一句话 |
|---|---|---|
| 教程（学习） | [getting-started.md](getting-started.md) | 从零装环境到跑通一次检测冒烟，只此一篇 |
| 操作指南（干活） | `.claude/skills/`（36 个） | 排障/修改/新增的手把手流程，agent 优先但人也能读 |
| 参考（查证） | [reference/](reference/) | 表结构/配置字典/API 面/插件 SDK，自动生成或脚本校验 |
| 原理阐释（弄懂） | [architecture/](architecture/) + [internals/](internals/) | 为什么系统长这样；最烧脑模块的深潜导游 |
| 决策审计 | [decisions/](decisions/) | ADR：当时的处境、决定与后果（Nygard 四字段） |
| 军规 | [conventions/](conventions/) | docstring/端点文档/文档写作 三份一页纸规范 |

## 各目录详情

### architecture/ — 架构总览（arc42 裁剪版）
- [00_质量目标与干系人.md](architecture/00_质量目标与干系人.md) — 为什么系统长这样：前三大质量目标
- [01_系统上下文.md](architecture/01_系统上下文.md) — 系统边界：与 MES/硬件/操作员的关系（C4 L1）
- [02_容器视图.md](architecture/02_容器视图.md) — Electron 壳/前端/后端/DB/推理/插件 六容器（C4 L2）
- [03_分层依赖规则.md](architecture/03_分层依赖规则.md) — 谁可以 import 谁 + 新代码放哪的判定
- [04_横切概念.md](architecture/04_横切概念.md) — 错误隔离/线程模型/配置写入权/日志/鉴权
- [05_运行时关键链路.md](architecture/05_运行时关键链路.md) — 一个检测周期的一生等 2-3 条真实链路

### internals/ — 深潜导游（只写最烧脑的 20%）
- [source-state-machine.md](internals/source-state-machine.md) — 视频源状态机：5 种逻辑模式 × 4 种结算模式
- [mes-hook-pipeline.md](internals/mes-hook-pipeline.md) — 扫码→绑定→结算→推送全链
- [cluster-collector.md](internals/cluster-collector.md) — 集群主从聚齐/心跳/超时
- [plugin-loading-chain.md](internals/plugin-loading-chain.md) — 插件加载链与错误隔离

### reference/ — 参考（生成物勿手改）
- [db-schema.md](reference/db-schema.md) — 全部数据表（脚本生成，源：ORM 模型）
- [config-dict.md](reference/config-dict.md) — Project 7 个 JSON 配置字段字典
- [plugin-sdk.md](reference/plugin-sdk.md) — hook/PluginHost 能力面 + 内部 API 遮蔽白名单
- `api/openapi.json` — CI 导出的 API 面存档（diff 即变更审计）

### _reading_notes/ — 函数级源码索引（读源码前先查这里）

> **定位**：2026-07 全量读码的产物，按域记录**每个文件的职责、核心函数+行号、锁、线程、状态变量、上下游、注释里的坑**。
> 找"某函数在哪 / 某状态谁读谁写 / 密钥·配置文件落在哪"这类问题，**先查本目录再翻源码**——比全仓 grep 快且不漏。
> 行号以 2026-07-05 代码为准，大改后可能漂移；漂移了以代码为准并顺手更新。

- `01_backend_core.md` — 后端核心 47 文件（启动/路由/VSM 主类+23 mixin+14 has-a/多工位/报警/称重引擎）
- `02_mes_domain.md` — MES 域 30 文件（Hook 管理器/扫码器/网关/入站/拉取/集群/两个流水线协调器/wmax 全目录/外设称重/10 个 MES 路由）
- `03_data_plugin.md` — 47 张表全清单 + Session 写入链 + 导出/鉴权/插件生命周期 + 10 hook 行号
- `04_frontend_electron.md` — 前端 97 文件 + Electron 壳（路由/Store/API/Monitor 深读/8 步关机）
- `05_backend_misc.md` — 兜底册 41 文件（工单/工件/缺陷服务、5 个 MES 适配器、4 个导出渲染器、流触发器、杂项路由、core/db/schemas/海康 SDK 封装）
- `_backend_files.txt` — 后端 170 个 py 文件清单（读码分工底账；**已对账 170/170 全覆盖**，2026-07-05）

## 维护规则（三条）

1. **文档随代码同改**：改动碰到某模块，对应文档在同一次提交里更新——文档 CI（`scripts/ci/check_doc_*.py`）会拦新增欠账。
2. **每篇头部三行元信息**：类型 / 本文不讲什么→去哪看 / 冲突以代码为准。
3. **报错通道**：发现文档过期，小错顺手修；大偏差在 `docs/dev/_reading_notes/` 记一条或直接告诉主作者。

### 文档 CI 一键验证

```bash
# 六项全绿（与 CI 同口径）
~/anaconda3/envs/tianjun/bin/python -m pytest tests/test_doc_ci.py -q

# 生成物过期时重跑（不带 --check）
~/anaconda3/envs/tianjun/bin/python scripts/docgen/gen_db_schema.py
~/anaconda3/envs/tianjun/bin/python scripts/docgen/gen_openapi_snapshot.py
~/anaconda3/envs/tianjun/bin/python scripts/docgen/gen_config_dict.py
```

## 与既有文档的分工边界（防双份维护）

- `.claude/skills/` 是 how-to/debug 层，**不搬进本目录**；skill 里的原理段落逐步改为指向 internals/ 的指针。
- `docs/plugin-system/` 是插件域唯一事实源，本目录只放入口指针。
- `AGENTS.md` 只做地图；本目录是它第四节之外的"解释+参考"层。
- `docs/changelog/` 记版本流水；`docs/rfc/` 中已落地的设计稿盖"已归档"戳后，现状以本目录为准。
