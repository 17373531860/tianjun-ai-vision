---
name: merge-branch
description: "分支合并与多 agent 并行协作：merge/rebase 选择、冲突解决决策树、项目特异地雷（source.py mixin / ALTER TABLE / Monitor 双缓冲）、长期分支吸收主线节奏、合并前后冒烟验证。当用户要合并分支、多个 agent 并行后汇总成果、解决合并冲突、或评估合并风险时使用。"
argument-hint: "[源分支] [目标分支]，或描述合并场景"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Shell, StrReplace, mcp__github"
---

# merge-branch: 分支合并与多 agent 协作

> **临时版本（v0.1）**：暂未追溯历史合并冲突案例（项目历史几乎是线性单作者），随多 agent 并行实战积累后会扩充"项目特异冲突案例库"。

天军 AI 视觉检测项目从单作者线性开发转向**多 agent 并行**时的合并指南。
核心命题：**让分支并行不破坏主线稳定**（客户已在用 v3.6.0）。

需求：$ARGUMENTS

---

## 一、合并 vs 变基：先选对策略

| 场景 | 推荐策略 | 命令 | 理由 |
|---|---|---|---|
| 短命特性分支（< 1 天，< 5 commit）合回 main | **rebase** | `git rebase main` 然后 `git merge --ff-only` | 历史干净，没必要留分支痕迹 |
| 长期分支（如 `feat/plugin-system`）合回 main | **merge --no-ff** | `git merge --no-ff feat/plugin-system` | 保留分支节点，便于整体回滚 |
| 长期分支吸收主线最新修复 | **merge** | 在 feat 分支上 `git merge main` | 保留双线，不重写 commit hash |
| 多 agent 并行做的小特性聚合 | **逐个 rebase 后线性合入** | 见第三节 | 历史好读，二分定位快 |
| 已 push 远程且别人在用的分支 | **绝不 rebase** | 只能 merge | rebase 会改 hash，别人 pull 报错 |

> 黄金法则：**没 push 出去的提交可以随便 rebase；push 出去过的就只能 merge**。

---

## 二、多 agent 并行工作的分支模型

> 这是本项目接下来要采用的协作方式，以下是推荐结构。

### 模型：主线 + 长期分支 + 短命子分支

```
main (客户在用)              ←—— 只接受合并完且冒烟通过的成果
  │
  ├─ feat/plugin-system      ←—— 长期分支，最终合回 main
  │    │
  │    ├─ feat/plugin-loader     ← agent A 在做：插件加载器
  │    ├─ feat/plugin-signing    ← agent B 在做：签名校验
  │    ├─ feat/plugin-config-ui  ← agent C 在做：前端配置界面
  │    └─ ...
  │
  └─ hotfix/v3.6.x-*         ←—— 紧急 bug 修复，直接合 main
```

### 核心原则

1. **单一职责分支**：每个 agent / 每个特性一个独立子分支，命名 `feat/<父域>-<具体功能>`
2. **永远不让两个 agent 直接动同一个文件**——这是冲突最大来源
3. **频繁同步主线**：长期分支每天至少一次 `git merge main`（或 hotfix 后立即同步）
4. **小步合回**：子分支做完一个原子特性（< 500 行 diff）就合回父分支（`feat/plugin-system`），不要堆几千行
5. **合回主线的最终合并由人类拍板**——agent 只准备 PR 不准自动合 main

### 多 agent 并行检查清单（开工前问自己）

- [ ] 这次任务是否会动 `source.py` 主类？→ 只能一个 agent 做，串行
- [ ] 是否会动 `migrate_database()` 的 ALTER TABLE 序列？→ 只能一个 agent 做
- [ ] 是否会动 `electron/package.json` 版本号？→ 只能一个 agent 做（避免冲突）
- [ ] 是否会动同一个 Vue 视图（如 `Monitor/index.vue`）？→ 拆成不同子组件，避免同文件
- [ ] 任务是否真的独立？两个 agent 改的文件**不重叠**才能真正并行

---

## 三、本项目合并的标准工作流

### 工作流 A：短命特性分支合回 main（最常见）

```bash
# 1. 在特性分支上确保工作区干净
git checkout feat/xxx
git status   # 必须 clean

# 2. 拉取主线最新
git fetch origin

# 3. rebase 到主线最新（重写自己分支的 commit）
git rebase origin/main
#   有冲突 → git status 看冲突文件 → 编辑解决 → git add → git rebase --continue
#   想取消 → git rebase --abort

# 4. 切回 main 做 fast-forward 合并
git checkout main
git pull --ff-only origin main
git merge --ff-only feat/xxx

# 5. 冒烟验证（见第六节）
# 6. push（等用户明确指令）
git push origin main

# 7. 删本地分支（远程让用户决定）
git branch -d feat/xxx
```

### 工作流 B：长期分支吸收主线（如 feat/plugin-system 拉 main 修复）

```bash
git checkout feat/plugin-system
git fetch origin
git merge origin/main      # 保留分支线，不 rebase
#   冲突解决见第四节
#   验证见第六节
git push origin feat/plugin-system   # 等用户指令
```

### 工作流 C：长期分支最终合回 main（开发周期结束）

```bash
# 1. 长期分支先彻底吸收主线
git checkout feat/plugin-system
git merge origin/main
# 解决冲突 + 冒烟验证 + 跑测试

# 2. 切回 main 做 --no-ff 合并（保留分支节点）
git checkout main
git pull --ff-only origin main
git merge --no-ff feat/plugin-system -m "merge: 插件系统主干合入（vX.Y.Z）"

# 3. 冒烟全量验证
# 4. 等用户明确指令再 push
```

### 工作流 D：多 agent 子分支聚合到长期父分支

```bash
# 假设父分支是 feat/plugin-system，子分支 feat/plugin-loader / feat/plugin-signing
git checkout feat/plugin-system

# 一次合一个，每合一个跑一次冒烟
git merge --no-ff feat/plugin-loader -m "feat(plugin): 加载器模块"
# 冒烟通过 →
git merge --no-ff feat/plugin-signing -m "feat(plugin): 签名校验模块"
# 冒烟通过 →
# ...
```

---

## 四、冲突解决决策树

```
git merge 报冲突
    │
    ▼
git status 看冲突文件分类
    │
    ├─→ 文件 A：纯文本冲突（README / changelog 之类） → 直接编辑保留两边内容
    │
    ├─→ 文件 B：代码逻辑冲突
    │      │
    │      ├─→ 是不是项目关键不变量文件？（见第五节"地雷文件清单"）
    │      │     ├─→ 是 → 立即停手，列出冲突点找用户拍板
    │      │     └─→ 否 → 继续
    │      │
    │      ├─→ 两边改的功能是否互斥（不能同时存在）？
    │      │     ├─→ 是 → 找用户拍板取舍
    │      │     └─→ 否 → 合并保留两边
    │      │
    │      └─→ 改的是同一函数的不同行？
    │            └─→ 大多可以机械合并，但合完必须重读整个函数确认语义
    │
    └─→ 文件 C：自动生成 / 锁文件（package-lock.json / SQLite WAL）
           └─→ 删掉冲突标记后重新生成（npm install / 重启服务）
```

### 冲突标记速读

```
<<<<<<< HEAD                    ← 你当前分支（要合并的目标）
当前分支的代码
=======                         ← 分割线
要合并进来的分支的代码
>>>>>>> feat/xxx                ← 源分支
```

编辑成最终想要的样子（删掉所有 `<<<<<<<` `=======` `>>>>>>>` 标记），保存。

### 冲突解决工具（按推荐度排序）

1. **VSCode/Cursor 内置**：打开冲突文件会显示 "Accept Current / Accept Incoming / Accept Both" 按钮，最直观
2. `git diff --merge` 看每个冲突的三方对比
3. `git mergetool` 启动配置好的可视化合并工具（如 meld）

---

## 五、项目特异地雷文件清单

> **冲突踩到这些文件时必须先停手**，因为合错了会破坏客户已部署版本。

### 后端核心（动到必须人类复核）

| 文件 | 不变量 | 来源 |
|---|---|---|
| `backend/main.py` 第 4 行 | `OPENCV_FFMPEG_CAPTURE_OPTIONS=threads;1` 必须在 cv2 import 前 setdefault | AGENTS.md §八(2) v3.1.3 保命修复 |
| `backend/main.py: migrate_database()` | 60+ ALTER TABLE 序列只能追加不能删，老 SQLite 升级路径 | AGENTS.md §八(8) |
| `backend/api/source.py` 主类 | `__getattr__/setattr__` 兼容层路由到 has-a 组件，删字段会炸 | AGENTS.md §八(3) |
| `backend/api/source_*_mixin.py` | MRO 顺序敏感（如 `start_hcnetsdk` 在两个 mixin 都有） | AGENTS.md §六模块 1 |
| `backend/api/channel_manager.py: set_channel_count` | 必须级联调 `mes_hook.on_channel_removed` + `alarm_router.on_channel_removed` | AGENTS.md §八(4) |
| `backend/services/mes_hooks.py` | 项目最大 Hub，1505 行，61 条历史 bug，改一行可能炸十处 | AGENTS.md §六模块 6 |
| `backend/services/scanner.py` | 项目第二大文件 1964 行，LON/WMax 双协议状态机 | AGENTS.md §六模块 7 |

### 前端核心

| 文件 | 不变量 | 来源 |
|---|---|---|
| `frontend/src/views/Monitor/index.vue` (4051 行) | 双缓冲 MJPEG + 多通道 state 隔离 | AGENTS.md §八(7) |
| `frontend/src/store/useSystemStore.js` | License 缓存 + 全局 KV 配置，多处依赖 | AGENTS.md §六模块 16 |

### 配置/版本

| 文件 | 注意点 |
|---|---|
| `electron/package.json` `.version` | **版本号唯一权威源**，多个分支同时改必冲突，谁先合谁定 |
| `electron/splash.html` 第 112 行 | 必须和 package.json 版本号一致，CI 会校验 |
| `docs/CHANGELOG.md` | 多分支都加新条目时合并要保留**两边都加**而不是二选一 |
| `docs/changelog/vX.Y.Z_*.md` + `.json` | 新版本 changelog 文件名带版本号，理论不冲突 |
| `.github/workflows/build.yml` | `CORE_FILES` 数组多 agent 同时加文件名会冲突 |
| `backend/models/*.py` | ORM 字段冲突 → 必须同步更新 `migrate_database()` 的 ALTER TABLE |

### 死代码/低风险（合并时无脑取一边即可）

| 文件 | 状态 |
|---|---|
| `frontend/src/views/Report/index.vue` | 死代码，路由未注册 |
| `frontend/src/api/task.js` / `camera.js` | 死代码，无 import |

---

## 六、合并前后的预检 + 验证

### 合并前预检（必跑）

```bash
# 1. 工作区干净
git status   # 必须 "干净的工作区"

# 2. 看清要合什么
git log --oneline main..feat/xxx       # 即将并入的提交
git diff --stat main..feat/xxx          # 改了哪些文件
git diff main..feat/xxx -- '*.py'       # Python 改动详情（按需）

# 3. 检查是否动到地雷文件（见第五节）
git diff --name-only main..feat/xxx | grep -E "(main\.py|source\.py|mes_hooks|scanner\.py|Monitor/index|package\.json|migrate_database)"
#   有命中 → 必须人类复核
```

### 合并后冒烟（必跑）

```bash
# 1. lint / 编译检查（前端）
cd frontend && npm run build && cd ..

# 2. 后端启动测试
cd backend && python -c "from backend.main import app; print('import ok')" && cd ..

# 3. 数据库迁移测试（如改了 ORM）
# 备份 DB → 启动后端 → 看 migrate_database 日志 → 确认无 ALTER TABLE 报错
cp sql_app.db sql_app.db.bak
python -m uvicorn backend.main:app --port 8001 &
sleep 5
# 看启动日志有无报错
kill %1
```

### 关键不变量复核清单（合并完最后一遍过）

- [ ] `backend/main.py` 第 1-10 行的 import 顺序没乱（cv2 不能在环境变量之前）
- [ ] `migrate_database()` 没有删条目，只有追加
- [ ] `set_channel_count` 仍调用 `mes_hook.on_channel_removed` + `alarm_router.on_channel_removed`
- [ ] `electron/package.json` 版本号合理（要发版的话）
- [ ] `docs/CHANGELOG.md` 顶部条目齐全（两边的都保留了）
- [ ] 没有 `<<<<<<<` `=======` `>>>>>>>` 残留（`grep -rn '<<<<<<<' .` 应该空）

---

## 七、合并出错的回滚

```bash
# 情况 1：merge 还没 commit，反悔
git merge --abort

# 情况 2：rebase 还没 continue 完，反悔
git rebase --abort

# 情况 3：merge 已经 commit，但还没 push
git reset --hard ORIG_HEAD            # ORIG_HEAD 是 merge 前的位置
git reflog                            # 万一 ORIG_HEAD 不对，从 reflog 找

# 情况 4：merge 已经 push 出去
# ⚠️ 不要 force push 主线。改用 revert
git revert -m 1 <merge_commit_hash>
# -m 1 表示保留主分支那一边，撤销并入分支的改动

# 情况 5：rebase 完发现搞砸了，且没 push
git reflog                            # 找到 rebase 前的 hash
git reset --hard <hash>
```

---

## 八、远程操作的安全护栏

### 永远禁止

- ❌ `git push --force` 推 `main`（AGENTS.md 第三节硬性规则）
- ❌ `git push --force-with-lease` 推 `main`（同上，软一点也禁止）
- ❌ 删除 `main` 分支
- ❌ 在 `main` 上做 `reset --hard`（除非 reflog 立刻能回退且没 push）

### 需要先问用户

- ⚠️ `git push --force` 推任何分支（哪怕是自己的特性分支，可能别的 agent 在用）
- ⚠️ 删远程分支 `git push origin --delete <branch>`
- ⚠️ 重写已 push 的提交历史

### 可以放心做

- ✅ 本地分支随便 rebase / reset / branch -d（reflog 兜底）
- ✅ 创建新分支
- ✅ fetch（永远只读）

---

## 九、Commit 信息规范（合并相关）

跟项目其它 commit 一样：`<type>(<scope>): <短描述>`

```
merge(plugin): 插件签名模块合入主干
merge(main): 长期分支吸收 v3.6.0 hotfix
fix(merge): 解决 source.py mixin MRO 冲突
revert(merge): 撤销 #abc123，导致 cycle_end hook 失效
```

`merge --no-ff` 默认生成的 "Merge branch 'xxx' into yyy" 信息**必须改写**成上面格式。

---

## 十、和其它 skill 的边界

- `update-release` — 版本号 bump、changelog 生成、tag 推送：合并完才轮到它
- `build-release` — CI 流程、Nuitka 编译：合并完触发 CI 时才看
- `create-hotfix` — 客户热补丁：和合并互补，hotfix 通常不需要合并流程，单独发 bat 脚本
- `modify-source` / `modify-frontend` / `modify-model` — 改动**前**的影响分析：合并冲突时遇到对应文件，先读这些 skill 重温不变量
- 本 skill — 只管"两条线如何合到一起 + 怎么避免炸"，不管单条线上怎么改

---

## 十一、后续完善方向（v0.2+）

随实战积累后扩充以下内容：

1. **真实冲突案例库**：每次踩到的合并冲突案例（哪些文件、什么场景、怎么解的）
2. **多 agent 协调表**：建一个 `docs/agent-coordination.md` 记录"哪个 agent 在动哪些文件"，避免冲突
3. **自动化预检脚本**：`scripts/pre-merge-check.sh`，跑完输出"动到 N 个地雷文件，需要人类复核"
4. **PG 迁移期的特殊合并规则**：`feat/migrate-pg` 分支引入后，ORM 双轨期的合并要怎么走
5. **CI 与合并联动**：何时让 CI 跑 merge 候选、何时直接合本地

> 用户已说"暂时性的先生成"，达到这五点中的任意一点就该升级到 v0.2。
