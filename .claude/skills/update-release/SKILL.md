---
name: update-release
description: "更新发版全流程：回顾对话提取变更、生成 changelog（MD+JSON）、git commit、触发打包。当用户说'更新'、'发版'、'打包'时使用。"
argument-hint: "[版本号或留空自动决定]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Shell, Write, StrReplace, Agent, mcp__github, mcp__filesystem"
---

# update-release: 更新发版流程

用户要求更新发版。你需要完成以下全部步骤。

需求: $ARGUMENTS

## 第1步: 提取本轮对话中的所有变更

回顾整个对话历史，提取：
- **Bug 修复**: 发现了什么问题、根因是什么、怎么修的
- **功能变更**: 新增/修改了什么功能、为什么
- **配置变更**: 修改了什么配置项
- **Skill 更新**: 更新/新增了哪些 skill
- **影响文件**: 每个变更涉及的文件列表

对每个变更，记录：
- 症状（用户看到什么现象）
- 根因（代码层面的原因）
- 修复方式（具体改了什么）
- 关键词（用于后续 AI 训练检索）

## 第2步: 决定版本号

读取当前版本号：
```
electron/package.json → "version" 字段
```

版本号规则：
- **patch** (x.x.+1): 纯 bug 修复、参数调整
- **minor** (x.+1.0): 新功能、行为变更
- **major** (+1.0.0): 架构重构、不兼容变更

如果用户指定了版本号，使用用户指定的。

## 第3步: 生成 Changelog 文件

在 `docs/changelog/` 下生成两个文件：

### Markdown 文件: `docs/changelog/vX.X.X_YYYY-MM-DD.md`

```markdown
# vX.X.X (YYYY-MM-DD)

## Bug 修复
- [BUG-001] 简短标题
  - 症状: 用户看到的现象
  - 根因: 代码层面的原因
  - 修复: 具体改动描述
  - 影响文件: file1.py, file2.vue

## 功能变更
- [FEAT-001] 简短标题
  - 需求: 为什么要做这个
  - 实现: 怎么实现的
  - 影响文件: file1.py

## Skill 更新
- [SKILL-001] 简短标题
  - 变更内容: 具体改了什么

## 已知问题
- 问题描述（如有）
```

### JSON 文件: `docs/changelog/vX.X.X_YYYY-MM-DD.json`

```json
{
  "version": "X.X.X",
  "date": "YYYY-MM-DD",
  "previous_version": "X.X.X",
  "changes": [
    {
      "type": "bugfix|feature|config|skill",
      "id": "BUG-001",
      "title": "简短标题",
      "symptom": "用户看到的现象",
      "root_cause": "代码层面的原因",
      "fix": "具体改动描述",
      "files": ["backend/api/source.py"],
      "keywords": ["关键词1", "关键词2"],
      "breaking": false
    }
  ],
  "skills_updated": ["skill-name-1", "skill-name-2"],
  "files_changed": ["file1.py", "file2.vue"]
}
```

## 第4步: 更新汇总 CHANGELOG

读取 `docs/CHANGELOG.md`（如不存在则创建），将新版本追加到顶部。

格式：
```markdown
# Changelog

## vX.X.X (YYYY-MM-DD)
- [BUG-001] 修复: 简短描述
- [FEAT-001] 新增: 简短描述

## vX.X.X-1 (之前的日期)
- ...
```

## 第5步: 更新版本号（含 AGENTS.md — 不可漏！）

更新以下文件中的版本号：
- `electron/package.json` → `"version"` 字段（**版本号唯一权威源**）
- `electron/splash.html` → 版本显示文本（如果存在）
- `AGENTS.md` → 第一节"当前线上版本"那一格 + 文件尾"最后更新"日期

> ⚠️ **发版版本对齐铁律（历史教训）**：曾经发版只 bump `package.json`、忘了同步 `AGENTS.md`，导致 AGENTS 长期落后好几个版本（线上 v3.27 而 AGENTS 还写 v3.23），误导后续 AI 拿到错误的"当前版本"。
> **凡是写了版本号 / 版本日期的地方都必须在发版时一次性对齐**：`electron/package.json` / `electron/splash.html` / `AGENTS.md`（第一节 + 文件尾）/ 任何 skill 里标了"当前版本 / 适用版本"的位置。不允许只改一处。

**更新完必须跑自检脚本**（v3.31 起，package.json / AGENTS / changelog / tag 四处一致性）：

```bash
python scripts/ci/check_version_alignment.py --expect vX.X.X
# 退出码非 0 = 有地方没对齐, 修完再进下一步; tag 落后属正常（最后才打）
```

## 第5.5步: 强制检查并更新 Skill（不可跳过！）

**为什么这步是强制的：** v2.3.0 发版时漏掉了 skill 更新，导致后续 AI 操作缺乏 MES 上下文。

对本轮对话中的每个变更，检查以下问题：
1. 是否新增了后端 API 模块/路由？→ 更新 `modify-api`, `api-sync`, `add-api-endpoint`
2. 是否修改了 source.py 或新增了调用 source.py 的代码？→ 更新 `modify-source`, `debug-source`
3. 是否新增/修改了 ORM 模型？→ 更新 `modify-model`, `fix-data`
4. 是否新增了前端页面/Store/API？→ 更新 `modify-frontend`, `debug-frontend`
5. 是否影响了多通道逻辑？→ 更新 `debug-channel`
6. 是否影响了 Session/Cycle 数据？→ 更新 `debug-session`
7. 是否引入了全新子系统？→ **创建新的 debug-xxx skill**

**操作流程：**
1. 列出本轮变更涉及的所有文件
2. 对照上述 7 条规则，标出需要更新的 skill
3. 读取每个需要更新的 skill → 追加新增内容（不删旧内容）
4. 如果有全新子系统 → 用现有 skill 作为模板创建新 skill

**完成标志：** changelog 的 `skills_updated` 字段包含所有更新/新增的 skill 名称。

## 第5.6步: 开发文档查漏补缺（2026-07 起强制，不可跳过！）

**为什么这步是强制的：** 2026-07 建立了 `docs/dev/` 开发者文档体系（架构总览/深潜/参考生成物），文档 CI 会拦欠账——发版前不对账，CI 红或文档腐烂。

对本轮全部变更逐条过下面的表：

| 变更类型 | 要更新的文档 |
|---|---|
| 新增/修改 API 端点 | 端点必须有 summary + response_model + docstring（`docs/dev/conventions/端点文档军规.md`；缺了端点门禁直接红，**不要**往 `scripts/ci/doc_endpoint_baseline.json` 加豁免） |
| 改 ORM 模型/表 | 重跑 `python scripts/docgen/gen_db_schema.py` |
| 改 Project 7 个 JSON 配置字段 | 重跑 `python scripts/docgen/gen_config_dict.py` |
| 任何路由/Schema 变更 | 重跑 `python scripts/docgen/gen_openapi_snapshot.py` |
| 改了状态机/MES 链路/集群/插件加载的**设计** | 同步更新 `docs/dev/internals/` 对应深潜篇 |
| 架构级变更（分层/容器/依赖规则变了） | 同步更新 `docs/dev/architecture/` 对应篇 |
| 插件 hook/PluginHost 能力面变更 | 更新 `docs/dev/reference/plugin-sdk.md` + `docs/plugin-system/` |
| **改/增任何 backend/frontend 源文件（含新文件）** | **同步更新 `docs/dev/_reading_notes/` 对应域的函数级条目**（01 后端核心 / 02 MES 域 / 03 数据+插件 / 04 前端+Electron / 05 后端杂项）：新文件建完整条目，老文件在对应函数处补"vX.X 变更"一句话+机制；节标题行数刷新。⚠️ 2026-07-17 血泪教训：这层没有 CI 触发器兜底，v3.33~v3.41 连漏九版（weighing_engine +1181 行、PersistWorker 等 4 个新文件零条目），靠人工审计才发现——**发版时逐条过 changelog 的 files_changed，每个文件都要在笔记里找到落点** |
| **任何面向用户的功能/UI/配置变更** | **同步更新 `docs/软件操作手册.md`**（对应 4.x 功能小节或 5.x 配方；标注版本号；默认关的功能写明"默认关闭零差异"）+ 封面版本号 + `scripts/docgen/gen_软件操作手册.py` 里的 VERSION 常量 + **重跑该脚本出 PDF**。⚠️ 同 2026-07-17 教训：操作手册从 v3.33 停更到 v3.41 才被发现（更早一次是 v3.12→v3.29），发版时过一遍 changelog 里的 feature 条目，每条都要回答"手册要不要写" |

**完成标志（必须真跑，不许口头绿）：**

```bash
~/anaconda3/envs/tianjun/bin/python -m pytest tests/test_doc_ci.py -q
# 6 passed 才能进第6步 Git Commit
```

## 第6步: Git Commit

```bash
git add docs/changelog/ docs/CHANGELOG.md
git add -A  # 包含所有代码变更
git commit -m "release: vX.X.X - 简短描述"
```

## 第7步: 认证检查与推送

推送前必须完成以下检查（详见 `build-release` skill 的「Git 认证配置」章节）：

```bash
# 1. 检查 GitHub 认证
gh auth status
# 如果过期或报错:
#   gh auth login -h github.com -p https -w -s workflow
#   ↑ 必须带 -s workflow，否则无法推送 .github/workflows/ 文件
# 登录后必须执行:
#   gh auth setup-git

# 2. 确认 remote URL 干净（不含嵌入 token）
git remote -v
# 如有旧 token: git remote set-url origin https://github.com/17373531860/tianjun-ai-vision.git

# 3. 先设 public（CI free runners 需要 public repo）
gh repo edit --visibility public --accept-visibility-change-consequences

# 4. 推送代码和 tag
git push origin main
git tag vX.X.X
git push origin vX.X.X

# 5. 监控 CI（详见第 8 步，统一走 scripts/ci/watch_and_private.sh）
gh run list --limit 3

# 6. CI 完成后设回 private（第 8 步脚本会自动做）
```

**重要顺序**: public → push → tag → 等 CI 完 → private

## 第8步: 监控 CI 并恢复 private

**必须执行，不可跳过。** CI 使用 free runner，仅 public repo 可用，构建完必须立即改回 private。

**统一用仓库脚本 `scripts/ci/watch_and_private.sh`**（v3.31 后固定，不要再手写一次性 `_ci_watch_*.sh` 变体丢根目录）：

```bash
# 获取 tag 触发的 run ID
RUN_ID=$(gh run list --limit 1 --json databaseId -q '.[0].databaseId')

# 后台守护：轮询所有 in_progress run 直到全部结束，然后自动切回 private
# 参数: -r run_id  -t 超时小时(默认6)  -i 轮询秒(默认300)  -l 日志(默认 _ci_watch.log)  -n 只盯盘不切private
nohup scripts/ci/watch_and_private.sh -r "$RUN_ID" >/dev/null 2>&1 &

# 进度随时看日志
tail -f _ci_watch.log

# 如果 CI 失败，排查
gh run view "$RUN_ID" --log-failed
```

脚本行为（无须重复手写）：
- 每 5 分钟轮询本批全部 run（tag 构建 + main push 一起等，避免中途丢 runner）
- 上次构建耗时约 4 小时；6 小时超时兜底后仍强制切 private
- **无论成功失败，都自动改回 private**（重试 3 次），日志落 `_ci_watch.log`（已 gitignore）

## 第9步: 打包发版（如需本地构建）

读取并执行 `.claude/skills/build-release/SKILL.md` 中的打包流程。

## 重要提醒

- 每个变更都要有清晰的 症状→根因→修复 链路，这是训练 AI 助手的核心数据
- JSON 中的 keywords 字段要全面，包含中英文关键词，方便后续检索
- 如果对话中有调参记录，不要放在 changelog 里，用 `tune-params` skill 单独记录
- 不要遗漏任何变更，包括 skill 文件的修改
