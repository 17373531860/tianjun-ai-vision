# 开发文档体系重启 — 业界调研报告与设计方案（RFC）

> **状态**：Accepted → **已实施（2026-07-05）** — P0~P4 全部落地，见 `docs/dev/README.md`
> **日期**：2026-07-05
> **调研范围**：111 份真实抓取阅读的业界文档页面（五路并行调研 104 份 + 方法论基石精读 7 份），
> 覆盖 28 个项目/标准：Linux kernel、PostgreSQL、SQLite、Redis、CPython、LLVM、Git、
> Chromium、Firefox、VS Code、Electron、Blender、OBS、Godot、Django、Rails、Vue、React、
> FastAPI、rustc、Kubernetes、Stripe、Twilio、微软 .NET、Google AIP、PyTorch、boto3、
> Diátaxis / arc42 / C4 / ADR / llms.txt / AGENTS.md 标准 / Google & 微软风格指南 / Write the Docs。
> **本文回答三个问题**：业界怎么写好开发文档（§一）；天军现状缺什么（§二）；体系怎么设计、怎么分期落地（§三、§四）。
> **本 RFC 批准前不动笔写任何正文文档。**

---

## 一、调研结论：十条业界铁律

跨 28 个项目反复出现、且互相印证的做法（每条注明最强出处）：

1. **四类文档严格分离，混写是烂文档的病根**（Diátaxis / Django / FastAPI）。
   教程（学习）/ 操作指南（干活）/ 参考（查证）/ 原理阐释（弄懂）四象限各有写作纪律，
   执行机制不是画目录，而是**"越界内容搬家"**：每篇文档开头声明"本文不讲 X，X 去看 Y"
   （FastAPI 参考页首页直接劝退读者去读教程）。

2. **"细致到每个函数"的文档没有一家是手写的**（Stripe / 微软 .NET / Rails / boto3）。
   全部是"机器可读的单一事实源（OpenAPI spec / XML 注释 / docstring / service model）
   生成页面骨架，人只写语义描述，工具链强制校验一致性"。微软把"公开成员缺文档注释"
   做成编译器警告（CS1591），Stripe/Twilio 的参数表直接从 schema 长出来。
   **单独手维护一份函数手册 = 三个版本内必然腐烂。**

3. **文档进源码树、跟代码同 PR 同 review**（kernel / PostgreSQL / CPython / Chromium / Git）。
   7 个系统级项目里 6 个如此。CPython 近年把内部文档从独立仓库**搬回**主源码树，
   就是为了让改代码的 PR 能同 diff 改文档。

4. **防腐靠机制不靠自觉**（Blender / Chromium / Django）。
   Blender 用脚本校验"文档里的目录路径是否真实存在于源码树"（进 CI）；
   Chromium 规定"实现状态决定文档归属"——功能落地后 design doc 降级为历史档案、
   现状真相只在模块文档；Django 把拼写/格式/死链检查做成 `make check` 进 CI。

5. **架构文档必须做"概念 → 源文件 → 入口函数"三元组映射**（SQLite / rustc / Godot）。
   SQLite 架构页每个组件标注实现文件；rustc-dev-guide 每章末尾给
   "Guide 章节 + crate + 主入口函数"三元组。指针比代码摘录抗腐化，且文件改名时腐烂立刻可见。

6. **复杂子系统配"目录级设计意图书"，从数据结构讲起**（PostgreSQL / Redis / CPython）。
   PostgreSQL 优化器目录的 README 有 1608 行，讲"为什么这样设计"——这是注释放不下、
   手册不该放的中间层。Redis 明说"理解程序最快的方式是理解它的数据结构"。

7. **函数注释有成文军规，重点是"调用上下文"**（kernel / Rails / LLVM）。
   kernel-doc 强制 `Context:` 段（在哪个线程被调、持锁否、能否睡眠）；
   Rails 军规细到时态和示例前缀；LLVM 三禁令：不重复函数名、接口注释只写一份、
   能靠命名说清的不写。

8. **设计决策用固定模板沉淀，"为什么"永不腐**（Vue RFC / K8s KEP / ADR / Git）。
   三个不变量：准入判据前置（什么才配走 RFC）、强制写 Drawbacks/Alternatives/Non-Goals、
   提案与实现解耦但互链。Nygard ADR 四字段（Status/Context/Decision/Consequences）是事实标准。

9. **诚实是最好的防腐剂**（React / rustc / OBS / CPython）。
   react-reconciler 的参考标题直接叫 "An (Incomplete!) Reference"；CPython 内部文档开头
   三行免责（给维护者看/不是规范/版本间会变）；文档敢承认会过期，才有人报告过期。

10. **给 AI agent 的文档已是正式体裁，纪律是"短即是服从"**（AGENTS.md 标准 / Firefox / llms.txt）。
    AGENTS.md 2025 年成为 Linux 基金会开放标准；Firefox 把 "AI Agent Tools" 列为文档站
    一级主题。共识：根文件 150-300 行封顶（臃肿导致规则被静默忽略）、指针优先按需加载、
    规则必带理由、禁区必带原因、**agent 消费的是 git 里的 Markdown 源文件而非渲染站点**。

---

## 二、天军现状盘点：家底与缺口

### 已有资产（不推倒，全部纳入新体系归位）

| 资产 | 规模 | 在四象限里的位置 | 评价 |
|---|---|---|---|
| AGENTS.md | 348 行 | AI 根索引（地图+守则+不变量） | 结构正确，继续瘦身方向 |
| `.claude/skills/` 36 个 | 1.27 万行 | how-to + debug（agent 优先） | 项目最强资产，等价于业界 how-to 层 |
| `docs/plugin-system/` | 约 2 万行 | 插件域 SSOT（含 design、inventory、adr） | 已是子域样板，ADR 实践已萌芽 |
| `docs/changelog/` 81 版 | — | 变更史 | 完整 |
| `docs/rfc/` 8 篇 | — | 设计决策 | 有实践无模板 |
| FastAPI `/docs` | 400+ 端点 | API 参考（自动） | 骨架在，缺 summary/描述军规 |
| 操作手册/客户文档 | — | 面向客户（不在本 RFC 范围） | 不动 |

### 缺口（按业界铁律逐条对照）

| # | 缺口 | 对应铁律 | 痛点场景 |
|---|---|---|---|
| G1 | **端点/函数级参考军规缺失**：/docs 有骨架但大量端点缺 summary、错误响应未声明、Pydantic Field 缺 description | 铁律 2 | 插件开发者/客户 IT 对接时只能问人 |
| G2 | **核心模块"设计意图书"缺失**：视频源状态机、MES Hook、集群协调只有 debug skill（排障向），没有"为什么这样设计"的深潜文档 | 铁律 6 | 新人/新 agent 改核心模块前无处建立心智模型 |
| G3 | **架构总览缺失**：无系统上下文图、无容器图、无质量目标声明、无分层依赖规则 | 铁律 5 + arc42 | "为什么系统长这样"只在主作者脑中 |
| G4 | **docstring/注释军规缺失**：AGENTS.md 只有"注释最少化"，无 Context 段、无格式约定 | 铁律 7 | 多线程调用约束（历史重灾区）无处强制记录 |
| G5 | **文档 CI 防腐为零**：无路径校验、无死链检查、无端点文档覆盖率门禁 | 铁律 4 | 已发生：技术债清单陈旧两个月才被发现 |
| G6 | **RFC 无模板、实现后不归档**：RFC 09/10/11 落地后仍以设计稿形态存在，易被当现状读 | 铁律 8 + Chromium"实现状态决定归属" | agent 读到过时设计稿的风险真实存在 |
| G7 | **新人旅程缺失**：无"装环境→启动→跑通一次冒烟→看懂第一条数据流"的 Getting Started | 四象限之教程 | 只有 start-dev-servers skill（操作向，非教学向） |
| G8 | **内部 API 无遮蔽契约**：插件可依赖面（10 hook + PluginHost）之外的 public 方法无"内部 API 勿依赖"标记 | Rails `:nodoc:` | 未来重构的腾挪空间未被文档保护 |

---

## 三、体系设计

### 3.1 总原则（四条，凌驾于目录结构之上）

1. **纯 Markdown 进 git 仓库**，人和 agent 消费同一份源文件；渲染站点（MkDocs Material +
   mkdocstrings）是可选的第二步，内容不掺任何工具方言，随时可换工具。
2. **参考层自动生成，叙事层手写**：凡是"罗列型"内容（端点、表、配置键、hook 载荷）
   一律从代码/schema 生成或用脚本校验；人只写"为什么"和"怎么办"。
3. **每篇文档头部三行元信息**：`类型`（tutorial/how-to/reference/explanation）、
   `本文不讲什么 → 去哪看`、`与代码冲突时以代码为准 + 报错通道`。
4. **文档改动与代码同 PR**，进 T1 影响分析（"碰 X 模块 → 必须同步改 Y 文档"写进对应 skill）。

### 3.2 目录架构（Diátaxis × arc42 裁剪 × C4 × ADR 组合）

```
docs/dev/                          ← 新根（开发者文档，与面向客户的 docs/*.md 分离）
├── README.md                      ← 受众路由（三行分流：维护者/插件开发者/AI agent）
│                                     + llms.txt 式索引（一句话+链接+每链一行说明）
├── getting-started.md             ← G7：一条线走通（教程象限，只此一篇，不铺开）
├── architecture/                  ← G3（解释象限 · arc42 裁剪 7 节）
│   ├── 00_质量目标与干系人.md      ← arc42 §1：前三大质量目标（错误隔离/升级无损/现场可诊断）
│   ├── 01_系统上下文.md            ← arc42 §3 + C4 L1（Mermaid：系统↔MES↔硬件↔操作员）
│   ├── 02_容器视图.md              ← arc42 §5 + C4 L2（Electron壳/前端/后端/DB/推理/插件）
│   ├── 03_分层依赖规则.md          ← VS Code 式硬规则表（谁可以 import 谁 + 新代码放哪的判定）
│   ├── 04_横切概念.md              ← arc42 §8：错误隔离/线程模型/配置写入权/日志/鉴权
│   └── 05_运行时关键链路.md        ← 只画客户真实跑的 2-3 条（一个检测周期的一生等）
├── internals/                     ← G2（解释象限 · 深潜导游，只给最烧脑的 20%）
│   ├── source-state-machine.md    ←   5 种逻辑模式×4 种结算模式（档案卡文体：固定字段）
│   ├── mes-hook-pipeline.md       ←   扫码→绑定→结算→推送全链（从数据结构讲起）
│   ├── cluster-collector.md       ←   主从聚齐/心跳/超时
│   └── plugin-loading-chain.md    ←   三级兜底加载链/签名验证
│                                     （每篇开头三行免责声明；每节末尾"概念→文件→入口函数"三元组）
├── reference/                     ← 参考象限（自动生成/脚本校验为主）
│   ├── api/openapi.json           ←   CI 导出存档（diff 可见 API 面变化）+ 端点军规文档
│   ├── db-schema.md               ←   全部约 50 张表（从 ORM 生成脚本产出）
│   ├── config-dict.md             ←   Project 7 个 JSON 配置字段字典（脚本校验键名存在性）
│   └── plugin-sdk.md              ←   10 hook 载荷骨架（boto3 式全参数模板）+ 内部 API 遮蔽白名单（G8）
├── decisions/                     ← G6（ADR，Nygard 四字段，文件名=祈使动词短语）
│   ├── 0000-template.md           ←   模板（含"什么不值得写 ADR"负面判据）
│   └── （吸收 docs/rfc/ 已落地各篇的"为什么"，原 RFC 盖"已归档"戳）
├── conventions/                   ← G4（军规层，每篇一页纸）
│   ├── docstring军规.md            ←   Context 段强制/时态/示例前缀/LLVM 三禁令
│   ├── 端点文档军规.md             ←   summary+docstring+response_description+Field(description=…)
│   └── 文档写作军规.md             ←   头部三行元信息/一句一行排版/版本注记自包含/禁用词
└── quality-map.md                 ← 测试地图：四层框架各覆盖什么、T0-T8 为什么这样排
```

**与现有资产的分工边界**（关键，防止双份维护）：

- `.claude/skills/` **不动、不合并**——它是 agent 优先的 how-to/debug 层，`docs/dev/` 是
  解释+参考层；skill 里的"模块详解"段落逐步改为指向 `internals/` 对应篇的指针。
- `docs/plugin-system/` **保持子域 SSOT**，`docs/dev/reference/plugin-sdk.md` 只做入口指针。
- AGENTS.md **继续只做地图**，第四节触发表加一行指向 `docs/dev/README.md`。

### 3.3 防腐机制（G5，与写文档同等重要）

| 机制 | 做法 | 出处 |
|---|---|---|
| 路径校验 | 脚本校验 docs/dev + skills 里引用的文件路径存在性，进 CI（T3） | Blender |
| 端点文档门禁 | 脚本遍历路由清单，缺 summary/response_model 的端点 fail（先 warning 半个月再转 fail） | 微软 CS1591 |
| openapi.json 存档 | CI 导出进仓库，PR diff 即 API 面变更审计 | Stripe |
| 死链检查 | Markdown 内部链接校验，进 CI | Django |
| 实现状态归档 | RFC 落地后盖"已归档，现状以 XX 为准"戳（G6） | Chromium |
| 删除测试法 | agent 重复犯错→加一条；从不犯错的条目→删 | AGENTS.md 社区共识 |

### 3.4 工具链决定

- **第一阶段不建站点**：纯 Markdown in git，人用 IDE/GitHub 读，agent 用检索读。
- **第二阶段（可选）**：MkDocs + Material + mkdocstrings 建内网站点（中文搜索配 jieba；
  Vue 组件参考用 vue-docgen-api 前置生成）。已知风险：Material 2025 年底进维护模式——
  但本方案对工具绑定极浅（一个 YAML），最坏迁移成本以天计。
- **明确不选**：Sphinx（reST 学习税，对母语中文直写无收益；除非未来要交付 PDF 手册）、
  Docusaurus（无 Python autodoc，且给文档绑 Node 管线没必要）。

---

## 四、分期实施计划（批准后执行）

| 期 | 内容 | 工作量估 | 交付判据 |
|---|---|---|---|
| P0 | 军规三篇（conventions/）+ ADR 模板 + README 受众路由 + 文档 CI 三件套（路径校验/死链/端点门禁 warning 档） | 1-1.5 天 | CI 绿 + 军规可被引用 |
| P1 | architecture/ 六篇（arc42 裁剪版，C4 图用 Mermaid） | 2-3 天 | 主作者审读通过 |
| P2 | internals/ 四篇深潜（先试点 source-state-machine 一篇定文体，再铺其余三篇） | 每篇 2-3 天 | 新 agent 只读该篇能答出模块设计意图 |
| P3 | reference/ 自动生成管线（db-schema/config-dict/openapi 存档/plugin-sdk 骨架） | 2 天 | 生成脚本进 CI |
| P4 | getting-started + quality-map + 存量归位（RFC 归档戳、skill 指针化改造） | 2 天 | 分流表全链路可走通 |
| P5 | （可选）MkDocs 站点 + 端点门禁转 fail 档 | 1 天 | 内网可访问 |

节奏：每期完成即汇报，P2 试点篇先给你过目定调再铺开。总计约 8-12 天，可与新功能开发穿插。

---

## 五、需主作者拍板的四个问题（2026-07-05 已拍板）

1. **目录名**：`docs/dev/` ✅
2. **P2 深潜四篇**（状态机/MES Hook/集群/插件加载链）✅ 无增删
3. **端点文档门禁**：基线法 + 新增 fail 档 ✅（存量 395 项记账，ADR-0001）
4. **分期节奏**：一口气 P0–P4 ✅

---

## 六、实施完成清单（2026-07-05）

| 期 | 交付物 | 状态 |
|---|---|---|
| P0 | `conventions/` 三份军规 + ADR 模板 + `scripts/ci/check_doc_*.py` + `tests/test_doc_ci.py` + ADR-0001 | ✅ |
| P1 | `architecture/` 六篇 | ✅ |
| P2 | `internals/` 四篇深潜 | ✅ |
| P3 | `reference/` 生成管线（db-schema / config-dict / openapi.json / plugin-sdk） | ✅ |
| P4 | `getting-started.md` + `quality-map.md` + skill 指针化 + RFC 归档戳 + `AGENTS.md` 触发表 | ✅ |
| P5 | MkDocs 站点 + 端点门禁全量 fail | ⏸ 可选，未做 |

验证：`pytest tests/test_doc_ci.py -q` → **6 passed**。

---

## 附：五路调研报告存档

五路完整报告（每路含逐项目 mini-review + URL 清单 + 可迁移建议）由调研 agent 产出，
本 RFC 的 §一 §三是其综合。原始报告如需查阅可从本次对话存档提取；
111 份页面清单分布：系统级 21、桌面应用 19、Web 框架 23、企业 API 20、方法论 21、基石精读 7。
