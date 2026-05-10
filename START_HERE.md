# 天军 AI 视觉检测 — 主仓入口

> 这份文档是 main 分支的入口指南。原 worktree 分支（`feat/plugin-config` /
> `feat/multi-model-roi-link`）已于 v3.7.0 合入主线，下方两段保留它们各自的
> 上手要点便于查阅历史。

## 当前主线版本: v3.7.0

合入要点：
- 插件系统 (Tier 1/2/3) + 插件签名 + SQLite/PG 双库矩阵
- 多模型 ROI 联动 (Step 1-8) + Monitor 副模型 fps 卡片 + Project 配色 UI
- 客户自定义会话 ID + session_end 自动导出 + cycle.steps Jinja 模板修复
- MES 无扫码场景实时上传 (cycle_end 不再静默早退)
- 期望序列允许同一 label 多次出现 (A-B-C-B-D 不再误判)
- UAT skill + e2e 浏览器自动化路径沉淀

详细变更见 `docs/changelog/v3.7.0_*.md`。

---

## 一、入手前必读

| 优先级 | 文件 | 用途 |
|---|---|---|
| 1 | `AGENTS.md` | 工程不变量、八节地雷清单、模块清单 |
| 2 | `.claude/skills/<task>/SKILL.md` | 各任务专属流程 (debug-mes / modify-source / run-tests …) |
| 3 | `docs/multi-model-roi-link.md` | 多模型 + ROI 子系统使用 |
| 4 | `docs/plugin-system/` | 插件三层架构 + 签名 + 安装 |
| 5 | `docs/DB_MIGRATION_RUNBOOK.md` | SQLite → PostgreSQL 迁移 runbook |

## 二、开发 / 测试 / 发版三个高频任务

- 跑测试 → 触发 `.claude/skills/run-tests/SKILL.md`
- 修一个 bug → 触发 `.claude/skills/debug-*/SKILL.md` 对应子系统
- 发新版本 → 触发 `.claude/skills/build-release/SKILL.md`

## 三、过去两条长期分支的"我读到的告示" (保留作历史参考)

### 插件配置 worktree 的告示 (来自 feat/plugin-config)

> 插件系统已落地三层 (Tier 1 主题 / Tier 2 UI / Tier 3 全栈)，签名工具 +
> 安装 API + 前端 Pinia store 已就位。新模块入口：`backend/api/plugin_*.py`、
> `backend/services/plugin_*.py`、`frontend/src/store/usePluginStore.js`。
> DB 矩阵：SQLite (默认) / PostgreSQL (生产) 双适配，Alembic 迁移已配置。

### 多模型 / ROI worktree 的告示 (来自 feat/multi-model-roi-link)

> 多模型管线 (`source_inference_router.py`) 把推理任务派发到 main / 副槽。
> 每个槽位独立 ROI 多边形、独立调度 (每帧 / 每 N 帧 / 事件触发)、独立配色。
> Monitor 卡片现在显示主+副模型各自 fps 快照。
> 重要不变量：channel_manager.warmup_lock 是全局锁，禁止并发热身。
