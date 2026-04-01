---
name: update-release
description: "更新发版全流程：回顾对话提取变更、生成 changelog（MD+JSON）、git commit、触发打包。当用户说'更新'、'发版'、'打包'时使用。"
argument-hint: "[版本号或留空自动决定]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Shell, Write, StrReplace, Agent"
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

## 第5步: 更新版本号

更新以下文件中的版本号：
- `electron/package.json` → `"version"` 字段
- `electron/splash.html` → 版本显示文本（如果存在）

## 第6步: Git Commit

```bash
git add docs/changelog/ docs/CHANGELOG.md
git add -A  # 包含所有代码变更
git commit -m "release: vX.X.X - 简短描述"
```

## 第7步: 打包发版

读取并执行 `.claude/skills/build-release/SKILL.md` 中的打包流程。

## 重要提醒

- 每个变更都要有清晰的 症状→根因→修复 链路，这是训练 AI 助手的核心数据
- JSON 中的 keywords 字段要全面，包含中英文关键词，方便后续检索
- 如果对话中有调参记录，不要放在 changelog 里，用 `tune-params` skill 单独记录
- 不要遗漏任何变更，包括 skill 文件的修改
