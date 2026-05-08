# 多模型 / ROI / 多通道联动 worktree —— 给 AI agent 看

> 你打开了这份文档，说明你被分到这个工作区干活。**这个任务比一般特性危险得多，请仔细读完再动手。**

> **2026-05-08 状态更新**：子方向 A（ROI）+ 子方向 B（多模型）已完成 8 步原子 commit + e2e 集成测试。
> 详细使用文档见 [`docs/multi-model-roi-link.md`](docs/multi-model-roi-link.md)。
> 子方向 C（跨工位事件联动）**未做**，留给后续。

---

## 一、你在哪儿

| 项 | 值 |
|---|---|
| 分支 | `feat/multi-model-roi-link` |
| 父分支 | `main`（独立分支，但任务规模较大，可能演化为长期分支）|
| 主仓库地址 | `/home/qianqian/桌面/word/tianjun副本/` |
| 当前 worktree | `/home/qianqian/桌面/word/tianjun-multimodel/` |

---

## 二、任务范围

用户描述的需求（业务语义）：

1. **画 ROI** —— 给检测画面加可绘制的感兴趣区域 ✅ 已完成
2. **同一画面 + 两个模型** —— 一路视频源同时跑两个模型，互不干扰 ✅ 已完成（架构支持 N 模型，UI 上限 4 个）
3. **多画面联动** —— 原本互相独立的多个工位，需要做业务上的联动（比如 A 工位的检测结果影响 B 工位的处理逻辑） ⏳ **未做**

**已完成（A + B）**：
- ROI 绘制：复用主 ROI 编辑器画布；副模型推理前帧外区域置黑 + 中心点过滤双层防御
- 多模型架构：`InferenceRouter` 调度器 + 每模型 `ModelInstance` + 全局 `warmup_lock` 防多卡 OOM
- 跨通道（多工位 × 多模型）：每个通道独立 N 模型，`ChannelManager` 注入共享锁串行 GPU warmup
- 前端：`Project` 页"附加模型 (多模型 ROI)" 卡片 + `Monitor` 检测框分色 + per-model 性能小面板

**未做（C）**：
- 跨工位事件总线：A 工位 `on_event` 触发 B 工位副模型按事件调度 — 后端 schedule 字段已支持，事件总线本身没做
- 副模型检测结果入 Data 页 / 导出 / MES — 当前仅 UI 渲染

---

## 三、风险预警 ⚠️ 必读

按 AGENTS.md，这个任务**同时碰三个最高危区域**：

| 危险区 | 为什么危 | 不变量来源 |
|---|---|---|
| `backend/api/source.py` 主类 + 35 个 source_*_mixin | MRO 顺序敏感，has-a 兼容层会路由属性 | AGENTS.md §八(3) |
| `backend/api/channel_manager.py` | 当前设计就是"通道独立"，加联动会动核心假设 | AGENTS.md §六模块 5 |
| 检测推理路径（model load / inference loop / runner） | 单线程推理池，加双模型要重新设计调度 | AGENTS.md §六模块 2 |

**做任何改动前**，必须先读：

1. `.claude/skills/modify-source/SKILL.md` —— 改 source.py 的影响分析
2. `.claude/skills/debug-channel/SKILL.md` —— 多通道协作的踩坑
3. `.claude/skills/debug-detection/SKILL.md` —— 推理路径
4. `.claude/skills/add-detection-mode/SKILL.md` —— 新增检测模式（如果三选一里出现新模式）
5. `.claude/skills/modify-project-config/SKILL.md` —— 新增配置字段一定要走全链路对齐

**做改动前的硬性预检**：
- 在改的文件是不是地雷文件？（`merge-branch` skill 第五节有清单）
- 是不是改动了已有的不变量？（AGENTS.md §八的 10 条）
- 老客户升级会不会出问题？（这个项目客户在用 v3.6.0）

---

## 四、首次启动需要做的事

```bash
cd /home/qianqian/桌面/word/tianjun-multimodel/

# 1. Python 环境
conda activate tianjun

# 2. 前端依赖
cd frontend && npm install && cd ..

# 3. 启后端（端口 8003，避免冲突）
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8003 --no-access-log

# 4. 启前端 dev server（端口 5175）
cd frontend
VITE_API_BASE_URL=http://localhost:8003/api/v1 npm run dev -- --port 5175
```

**端口分配表**：

| worktree | backend | frontend dev |
|---|---|---|
| `tianjun副本`（main） | 8001 | 5173 |
| `tianjun-plugin` | 8002 | 5174 |
| `tianjun-multimodel`（这里） | **8003** | **5175** |

---

## 五、推荐的开发节奏

复杂特性建议**子任务级渐进**而不是一口气大改：

```
阶段 1：先做 ROI 绘制（前端 + 后端 ROI 配置 + 推理时简单裁剪）
        ──▶ 跑通整条链路 ──▶ 提交一个原子 commit ──▶ 让用户验收

阶段 2：再做双模型并行推理
        ──▶ 在阶段 1 基础上扩展 ──▶ 提交 ──▶ 验收

阶段 3：最后做跨工位联动
        ──▶ 在前两阶段稳定后再加 ──▶ 提交 ──▶ 验收
```

**每个阶段完成后**：
- 写一份子方向的设计简报（贴到对话里），让用户确认再进下个阶段
- 不要跳阶段
- 不要在一个 commit 里塞三个阶段的改动

---

## 六、必读项目守则

1. **一次只做一件事**：每个原子改动结束就汇报，不要闷头连改
2. **改前先做影响分析**：modify-* skill 不是装饰品
3. **不主动 commit / push**
4. **中文回复**
5. **回复格式**：`遵守协议：已确认` 开头

---

## 七、Commit 信息建议

```
feat(detection): 项目配置加 ROI 字段（pipeline_config.roi_polygons）
feat(detection): 推理前按 ROI 裁剪输入图（阶段 1 完成）
feat(detection): 双模型并行推理路径
feat(channel): 跨工位事件总线骨架
fix(channel): 修复双模型推理时 GPU 槽位竞争
```

---

## 八、合回主线之前

这个分支可能跑很久（按用户描述设计面广）。期间要做：

1. **每天至少一次** `git fetch origin && git merge origin/main` —— 吸收主线 hotfix
2. **每个阶段完成都做一次冒烟**（启后端 + 前端 + 跑一次完整检测流程）
3. **最终合回 main 必须由用户拍板** —— 走 `.claude/skills/merge-branch/SKILL.md` 工作流 C

### 当前状态（2026-05-08）

| 项 | 状态 |
|---|---|
| 后端单测 | **165 passed**（9 个 `test_*_multi*.py` 文件） |
| 后端 e2e | **11 passed**（`test_e2e_multi_model.py`） |
| 前端 lint | **0 error** |
| 前端 vite build | **通过**（2158 modules, 0 error） |
| 总测试 | **176 passed, 0 regression** |
| 真模型权重端到端验证 | ⏳ 用户自测（需真权重 + 摄像头） |
| 性能压测 | ⏳ 用户自测 |
| Nuitka 打包 | ⏳ 用户决定时机 |
| 子方向 C（跨工位联动） | ⏳ 未做，后续迭代 |

**合回主线前建议**：
1. 用户在真硬件上跑通 main + 副模型 ROI 流程 → ElMessage 不报错 → 检测框颜色正确
2. 实测显存峰值 ≤ 显卡上限的 80%
3. `git rebase main` 处理冲突（`pipeline_config` 字段共用，注意和插件 worktree 协调）
4. 走 `merge-branch` skill 工作流 C 提 PR

---

## 九、和其它 worktree 的协调

- **不要动同一个文件**：插件 worktree 主要碰 SystemConfig 和插件管理 UI；这个 worktree 主要碰 source.py / channel_manager / Monitor 视图。两边几乎不重叠，但有一个例外：**Project 配置的 7 个 JSON 字段**两边都可能动，每次改前先 fetch 看主线最新
- **Project 配置字段冲突预防**：插件那边可能加 `Project.plugins_config`；这边要加 `pipeline_config.roi_polygons` / `pipeline_config.dual_model_*`。**不同字段**就互不干扰，**同名字段**绝对不要碰
