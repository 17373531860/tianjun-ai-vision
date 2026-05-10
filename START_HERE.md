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

---

## 二、任务范围（暂未细化）

**插件系统配置层面的功能**——具体细分还需要用户给。可能涉及的方向：

- 插件启停开关
- 插件元数据上传 / 列表展示
- 客户级配置存哪儿（建议用 `SystemConfig` KV 表，AGENTS.md 第七节标了这是最适合插件用的扩展点）
- 插件配置面板的前端 UI

**让用户在这个 cursor 窗口里给你具体需求**，不要凭空猜。

---

## 三、必读 skill（按场景）

| 你要做的事 | 必读 skill |
|---|---|
| 加新的 KV 配置项到 SystemConfig | `modify-model`（改 ORM 字段）|
| 加 / 改后端 API | `add-api-endpoint` / `modify-api` |
| 改前端页面 / 加新视图 | `modify-frontend` |
| 涉及 Project 配置 7 个 JSON 字段 | `modify-project-config` |
| 做完想合回 main | `merge-branch` |

skill 路径都在 `.claude/skills/<name>/SKILL.md`。

---

## 四、首次启动需要做的事

```bash
cd /home/qianqian/桌面/word/tianjun-plugin/

# 1. Python 环境（worktree 共享同一个 conda env）
conda activate tianjun

# 2. 前端依赖（每个 worktree 都要单独装一次，因为 node_modules 不共享）
cd frontend && npm install && cd ..

# 3. 启后端（注意端口！主仓库可能在跑 8001，这里用 8002）
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8002 --no-access-log

# 4. 启前端 dev server（新开 terminal，端口 5174 避免和主仓库冲突）
cd frontend
VITE_API_BASE_URL=http://localhost:8002/api/v1 npm run dev -- --port 5174
```

**端口分配表（避免和其它 worktree 抢端口）**：

| worktree | backend | frontend dev |
|---|---|---|
| `tianjun副本`（main） | 8001 | 5173 |
| `tianjun-plugin`（这里） | **8002** | **5174** |
| `tianjun-multimodel` | 8003 | 5175 |

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
