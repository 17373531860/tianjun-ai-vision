---
name: run-tests
description: "测试相关任务的统一入口，覆盖：(1) 跑现有测试套件并分析失败 (2) 给新功能/bug 修复写测试 (3) 启动后端+前端做端到端冒烟 (4) 改动后的测试影响分析 (5) Playwright 自动化前端验证（截图/DOM检查/交互测试）(6) 虚拟 synthetic 剧本源（无摄像头/无模型跑真实 pipeline）(7) **可见浏览器 UAT — 用 headless=False 真开浏览器手点+脚本驱动+视频录像，是面向功能验证的金标准**。当用户说『测一下』『跑测试』『跑一下』『写测试』『补测试』『mock 报错/失败』『fixture 怎么写』『影响哪些测试』『冒烟』『端到端验证』『客户场景模拟』『测试 X 通不通』『截图看看』『页面对不对』『UI 验证』『虚拟检测』『剧本』『无模型测试』『手点一遍』『真开浏览器』『眼睛看一眼』『面向功能测试』『UAT』『验收』『客户反馈』『客户报的 bug』『修了 bug 测一下』『改了 X 跑一下』时触发。包括项目特有的 pytest / pytest-bdd / allpairspy / pytest-playwright 四层框架命令模板，Playwright 自动化前端验证工具包，可见浏览器 UAT 模板（含项目特有的 ROI/sequential/event/counter 验证矩阵 + 对话框点击/卡片点击/input 值读取等踩坑），以及 conftest 隔离 / MagicMock 串污染 / e2e 清理前缀等踩坑点。**任何客户反馈类任务，必须先读第 -1 节「硬约束」再选路径。**"
allowed-tools: "Read, Grep, Glob, Shell, Write, StrReplace, Agent, mcp__playwright, mcp__context7, mcp__sequential-thinking"
---

# run-tests: 测试任务统一入口

> 用户提到测试相关任务时，**先读第 -1 节「硬约束」，再走第 0 步识别诉求**。
> 硬约束是发版的最低门槛，违反任意一条都不能向用户报告"验收完成"。

---

## 第 -2 节：有序流水线（按 T0→T8 一步步做完，这就是"门"）

> 这一页是整份 skill 的"门"。下面 1700 行是"按需查的索引"，不是每次都读。
> **别把下面当成一堆并列的"必须"挑着做——它是一条有先后顺序的流水线，从 T0 走到 T8，一步一步勾掉。**
> 跳步 = 不合格。前一步没过不准进下一步。收尾把整张表贴回复里逐行打勾(✅/⬜/⚠️)。

| 序 | 步骤 | 何时强制 | 算过的标准 |
|----|------|----------|------------|
| **T0** | **写现场叙事** | 总是 | 4 句话讲清"操作员做了什么→前端看到什么→后端判什么→UI 怎么显示"。写不出=没收敛，**不准进 T1** |
| **T1** | **影响分析** | 总是 | 列出要改哪些文件；动主线先过 modify-* 系列 skill；碰 `.vue` 在这步就登记"本任务含 UI" |
| **T2** | **写改动 + lint 清零** | 总是 | 代码落地，ReadLints 0 错误 |
| **T3** | **后端 CI 回归** | 有后端改动 | 新增/补 `tests/test_*.py` 并跑绿(含 BDD `tests/step_defs/*` 如适用) |
| **T4** | **开真浏览器看 UI** | ⚠️ 碰 `.vue` 强制 | `headless=False` 真渲染 + 截图。build 绿/单测绿**都不算**看过 UI（上次"数据路径没 UI 却报完成"就死在跳过这步） |
| **T5** | **UI→后端落库双向验证** | ⚠️ 碰 `.vue` 强制 | 点按钮后 GET 接口/查 DB 证明真生效，不是只看 toast 弹了 |
| **T6** | **留 CI E2E** | ⚠️ 碰 `.vue` 强制 | 新增 `tests/e2e_browser/test_*.py` 并跑绿。**这步和 T4/T7 不是一回事**（见下纠偏） |
| **T7** | **路径 H 可见浏览器 UAT + 三件套证据** | 客户反馈/验收类强制 | 视频 `/tmp/uat_video/*.webm` + 截图 + run.log(`failed:0`)，齐了才算验收 |
| **T8** | **收尾打勾 + 报告** | 总是 | 把本表逐行打勾贴回复；任一步没做写「⚠️ 未完成 Tn，原因 X」，**禁止伪报"全过"** |

**⚠️ 高频纠偏（上次就栽这）：T7 的单文件 UAT ≠ T6 的 CI 回归**

- `tests/uat/*.py`（T7 / 路径 H）是**人眼证据**，故意不被 pytest 收集 → **下次回归不会自动跑它**。
- 所以带 UI 的改动：T6（CI E2E）和 T7（UAT 证据）**都要做，缺一不可**，别用 UAT 脚本冒充 CI 回归。
- 这也是 AGENTS.md「完成定义」的落地：单文件 UAT 算证据不算回归。

**触发判据**：T1 一旦登记"本任务含 UI" → T4/T5/T6 自动转强制，**不依赖用户有没有说"测一下"**（功能开发收尾即触发，见 AGENTS.md 三·完成定义）。纯后端任务只走 T0-T3 + T8。

---

## 第 -1 节：硬约束 / 验收铁律（最高优先级，七条 0/1 判定）

> 这一节优先级高于所有路径 A~H。
> 任何一条没满足 → 不允许写"验收通过 / 测试已完成 / 可以发版"这种结论。
> 每次结束任务前**必须**在回复里逐条对照打勾。

### 铁律 1：动代码前先写"客户现场叙事"

收到**任何**「测试 / 修 bug / 验收 / 客户反馈」类任务，第一步**不是**写代码、跑测试或读文件。**第一步是用 ≤200 字 + ≤5 句话**复述客户现场：

> 现场叙事模板（必须四句话齐全）：
> 1. 操作员做了什么动作（按几次按钮、做几步、用多少秒）
> 2. 摄像头/前端看到什么（哪些标签/卡片/弹窗按什么顺序出现-消失）
> 3. 后端应该判什么结果（合格/不合格、缺哪步、报什么事件）
> 4. 前端 UI 应该如何展示（哪个步骤卡变绿/变灰/变红、计数器走到几）

写完发给用户确认对不对。**用户没确认前禁止动任何代码**。

写不出来 = 你对问题理解还没收敛，硬动代码就是赌。

### 铁律 2：客户反馈类一律走「路径 H 可见浏览器 UAT」，自动化测试只是补充

只要用户的诉求来自「客户报上来」「现场反馈」「产线问题」「修客户的 bug」「验证一下新功能符不符合客户预期」，**默认路径必须是 H**，不允许只跑 BDD/单元/E2E 自动化就报告"验收完成"。

具体执行：

- 必须自己起后端 + 起前端（用项目脚本，或非默认端口避免误伤客户进程）
- 必须 `headless=False` 开真浏览器
- 必须 `record_video_dir=...` 录视频
- 必须按客户复现路径**操作一遍**（不是只跑断言）
- 必须每个关键节点截图、文件名带预期对照
- 必须把视频路径、截图目录、运行日志路径都写进给用户的总结里

**自动化测试（BDD/E2E pytest）允许同时跑，但只能作为"补充"而不是"替代"。**

### 铁律 3：双向验证 — Bug 修复必须经过"先红后绿"

修任意一个 bug，必须按这个顺序：

1. 在**没修复**的代码版本上，写好端到端剧本/UAT 脚本，跑一次，必须 **FAIL**。
   - 这一步证明：测试真的盯住了这个 bug，不是绕过它的假绿灯。
   - 如果在未修复版本上跑也 PASS，**说明测试本身有问题**，不是 bug 不存在，**回头改测试**。
2. 改代码修复 bug。
3. 重跑同一个剧本，必须 **PASS**。
4. 在给用户的总结里**显式说明**："已验证：未修复版本 FAIL → 修复后 PASS"。

**没经过这个转换的"测试通过"，等同于"假绿灯"，不允许报"验收完成"。**

如果时间上来不及在 git 上回滚验证，至少要用"临时把修复代码段注释掉"的方式让 bug 重现，然后恢复。

### 铁律 4：单元测试不能替代验收测试，矩阵绿灯不等于验收完成

| 测试类型 | 目的 | 输入来源 | 替代关系 |
|---|---|---|---|
| 单元测试（pytest test_*.py） | 防止代码内部回归 | 测试代码里手工构造的中间数据 | **不能**替代验收 |
| BDD 行为测试 | 锁定跨模块业务流程 | TestClient 调 API | **不能**替代验收（绕过前端） |
| E2E pytest-playwright headless | CI 回归 / Smoke | 自动化浏览器 + DOM 断言 | **不能**替代验收（人眼看不到） |
| **可见浏览器 UAT（路径 H）** | **客户验收** | **真起前后端 + 真开浏览器 + 真视频** | **唯一**可作"已验收"的证据 |

「跑了 N 个测试全过」**不**等于「已验收」。
「测试矩阵全绿」**不**等于「客户场景被覆盖」。
违反这条 = "测试矩阵绿灯崇拜"反模式。

### 铁律 5：每条客户反馈必须沉淀一条"客户场景剧本"作为回归护栏

修完 bug、跑完 UAT 之后，**必须**把这次客户反馈做成一条永久剧本归档：

- 优先用 `tests/scenarios/*.json`（虚拟检测剧本）+ `tests/uat/*.py`（UAT 脚本，命名不带 `test_` 前缀）成对存档
- ⚠️ **但 `tests/uat/*.py` 不进 CI（故意不被 pytest 收集）**，只算人眼证据。要让它真成"回归护栏"，**带 UI 的还必须另留一条 `tests/e2e_browser/test_*.py`**、纯后端的另留 `tests/test_*.py`——CI 自动跑的那条才是护栏。见第 -2 节纠偏。
- 文件名加客户反馈日期 + 问题摘要，例如 `tests/uat/uat_20260511_dup_label_in_sequence.py`
- 这条剧本必须能在 CI / 发版前重复跑，作为"这个 bug 不会再回归"的护栏
- 满足铁律 3 的"先红后绿"——把它丢回到 bug 未修复的版本能立刻复现 FAIL

**没归档 = 修了等于没修，下次有人改同区域代码这个 bug 会原样复活**。

### 铁律 6：UAT 报告必须有三件套证据，缺一不算完成

每次报"验收完成"必须附带：

| 证据 | 路径 | 检查方式 |
|---|---|---|
| 视频回放 | `/tmp/uat_video/*.webm` 或仓库归档目录 | 文件大小 > 100KB，可以播放 |
| 节点截图 | `/tmp/uat_shots/*.png` | 至少有"起始状态"+"关键操作中"+"最终状态" 3 张 |
| 运行日志 | `/tmp/uat_run.log` 或类似 | 写明每个步骤 OK/FAIL，最后一行 `failed: 0` |

三件套**齐了**才能向用户报告"验收完成"。
任意一件缺 → 报告时显式说"我跑了 X/Y，缺 Z 项"，不允许写"全过了"。

### 铁律 7：写代码前先反问 6 个问题（自检卡）

动代码、动测试、动配置之前，必须先在内心或笔记里回答这 6 个问题（任何一个答不出来就停下问用户）：

1. **客户场景**：用 4 句话能复述客户现场吗？（铁律 1）
2. **现行行为**：当前代码不修，跑客户场景会得到什么结果？（必须能预测）
3. **预期行为**：修复后，跑客户场景应该得到什么结果？（与上一条要不同）
4. **观察点**：用什么具体的观察方式（API 字段、DB 行、UI 上某像素、视频里某帧）来判断是否符合预期？
5. **回归保护**：哪条剧本/测试归档了这个观察点，下次有人改同区域代码能立刻看到红？
6. **撤回方案**：万一改坏了，最快撤回路径是什么（git revert 哪个 commit、回退到哪个版本）？

这 6 个问题答不齐 = 你对问题没收敛，**不允许动代码**。

---

## 完成判定（写在所有路径之上）

任务结束前**必须**逐条对照下表打勾：

- [ ] 铁律 1：客户现场叙事已写出、用户已确认
- [ ] 铁律 2：客户反馈类已走路径 H，视频已录、截图已存
- [ ] 铁律 3：双向验证已完成（未修复版本 FAIL → 修复后 PASS）
- [ ] 铁律 4：没有把单元/BDD/headless E2E 的"绿"当作"已验收"
- [ ] 铁律 5：客户反馈剧本已归档到 `tests/scenarios/` 或 `tests/uat/`
- [ ] 铁律 6：UAT 三件套（视频 / 截图 / 运行日志）齐全
- [ ] 铁律 7：6 个自检问题全部能回答

**任意一条没打勾 → 给用户的总结里必须写「⚠️ 未完成：铁律 N 未满足，原因 X」**，不允许跳过、不允许伪报"全过"。

---

## 第 0 步：识别用户诉求

按用户语句中的关键信号选路径：

| 关键信号 | 路径 |
|---|---|
| 「跑一下」「测一下」「测试通不通」「pytest 跑过没」 | **A：跑测试 + 分析** |
| 「写测试」「补测试」「给 X 加测试」「mock 怎么写」 | **B：写新测试** |
| 「冒烟」「端到端」「启动后端验证」「跑起来看看 X 通不通」 | **C：端到端冒烟** |
| 「改了 X 影响哪些测试」「哪些测试要更新」「fixture 失效」 | **D：影响分析** |
| 「截图看看」「页面对不对」「UI 验证」「前端自动化」「Playwright 跑一下」 | **E：Playwright 自动化前端验证** |
| 「验收」「现场测试」「部署后验证」「客户场景」「换产测试」「全流程跑一遍」 | **F：现场验收测试** |
| 「虚拟检测」「synthetic」「剧本」「无模型测试」「功能链路」「确定性注入」 | **G：虚拟功能测试模式** |
| 「面向功能测试」「真开浏览器」「眼睛看一遍」「手点一遍」「UAT」「人工验收」「视频录一段」「不要 headless」 | **H：可见浏览器 UAT（金标准）** |

含糊时（仅说"测试一下"无上下文）→ 先反问："你是想 (a) 跑现有测试 (b) 给新代码写测试 (c) 启动后端+前端做端到端冒烟 (d) 看改动会破坏哪些老测试 (e) 用 Playwright 自动截图/验证前端页面 (f) 现场验收/客户场景全流程验证 (g) 虚拟剧本源（无模型跑 pipeline） (h) **真开浏览器手点一遍录视频（面向功能测试金标准）**？"

> ⚠️ **重要原则**：当用户说「面向功能测试」「真的测过」「我要看页面对不对」时，**必须走 H 而不是 E**。
> Path E 跑的是 headless + DOM 断言（"按设定路径点过一遍"），用户**看不到**任何东西；
> Path H 是 `headless=False` + 视频录像 + API 验证后端契约，**人眼能复核**+**脚本能回放**，是真正"面向功能"的验收。

---

## 项目测试基建速查（所有路径都要先知道这些）

### 目录结构

```
tests/
├── conftest.py              # 必读：在 import backend 前隔离 TIANJUN_DATA_DIR + 建空 DB
├── features/*.feature       # BDD 场景（pytest-bdd, 中文 # language: zh-CN）
├── step_defs/test_*.py      # BDD 步骤定义（与 .feature 同名）
├── e2e_browser/             # Playwright 浏览器 E2E（独立 conftest）
│   └── conftest.py
├── test_*.py                # 单元 / 集成 / Pairwise（同目录扁平放）
└── test_pairwise_*.py       # allpairspy 笛卡尔积减枝（命名约定）
```

### 测试框架分层

| 层级 | 工具 | 文件位置 | 适合 |
|---|---|---|---|
| L1 BDD / 集成 | `pytest-bdd` | `tests/features/` + `tests/step_defs/` | 业务流程、跨模块行为、客户场景模拟 |
| L1 单元 / 集成 | `pytest` | `tests/test_*.py` | 单函数 / 单 mixin / API 端点暴露 |
| L2 Pairwise 矩阵 | `allpairspy` + `pytest` | `tests/test_pairwise_*.py` | 多维度配置组合（3+ 维笛卡尔积爆炸时） |
| L3 真实视频 sanity | `pytest -m slow` | `tests/test_real_video_smoke.py` | 启真 cv2 + VSM 跑视频，确认推理整链路通 |
| L4 浏览器 E2E | `pytest-playwright` | `tests/e2e_browser/` | 前端按钮点击、页面跳转、表单提交 |

### pytest 配置要点（`pytest.ini`）

- `testpaths = tests` — 默认扫 `tests/` 目录
- `bdd_features_base_dir = tests/features` — pytest-bdd 找 .feature 的根
- `markers`：`bdd`（BDD 场景）、`slow`（慢测试，CI 不默认跑）
- `addopts = -v --tb=short --strict-markers -p no:cacheprovider` — 关 cache 避免跨 session 串

---

## 路径 A：跑测试 + 分析失败

### 命令模板（按用户描述选）

```bash
# 默认全跑（含 BDD/单元/集成/Pairwise，排除 slow 和 e2e_browser）
pytest tests/ --ignore=tests/e2e_browser -m "not slow"

# 只跑某个文件
pytest tests/test_pt_ct_modes_exposure.py -v

# 按名字过滤（关键词模糊匹配，跨文件）
pytest tests/ -k "PT合并档 or pt_ct"

# 只跑 BDD
pytest tests/step_defs/ -v

# 只跑 Pairwise 矩阵
pytest tests/test_pairwise_*.py

# 跑慢测试（真实视频 sanity, 通常 30s-2min）
pytest -m slow

# 跑前端浏览器 E2E（需要先启动后端 + 前端 dev 服务器！见路径 C）
pytest tests/e2e_browser/ -v

# 失败时只 rerun 失败用例（结合 -x 在第一个失败处停）
pytest tests/ -x --tb=long
```

### 失败分析三段式

跑出失败后**不要**立刻往 SKILL.md 上找答案，按以下顺序：

1. **看 traceback 顶部 5 行 + 底部 10 行** — 顶部是测试入口，底部是真正抛出异常的代码位置
2. **隔离单条失败用例**：`pytest tests/test_X.py::test_func_name -v --tb=long`
3. **对照本 skill「常见失败模式」表 → 直接给答案 / 按表里的诊断步骤跑**

### 常见失败模式（按出现频率排）

| 报错关键词 | 根因 | 修复 |
|---|---|---|
| `MagicMock object is not subscriptable` 或 JSON 序列化失败 | mock fixture 没补全属性，MagicMock 自动生成的属性不是 dict | 在 `_make_mock_mgr` 里显式 `mgr.<attr> = {}`（参考 `tests/test_pt_ct_modes_exposure.py` 的 fixture 风格） |
| `RuntimeError: ... TIANJUN_DATA_DIR` / DB 找不到 | conftest 没在 import backend 前设环境变量；或测试自己 import 顺序不对 | 用例文件里**不要**直接 `from backend.X import Y`，要 `from backend.X import Y` 放在 `def test_...` 函数体内，或全部依赖 `tests/conftest.py` 已经做的隔离 |
| `playwright._impl._errors.TargetClosedError` | 前端 dev 服务器没起来，或起来了但端口不对（默认 5173） | 跑 e2e 前先 `cd frontend && npm run dev`（路径 C 第 2 步），并确认 `BASE_URL` 环境变量或 conftest 里的 base_url |
| `sqlite3.OperationalError: database is locked` | 测试间没用独立 DB，并发跑互锁；或 conftest 隔离漏了某个 sub fixture | 检查 fixture scope，单测试默认 `function` scope；session-scope fixture 必须搭配 cleanup 钩子 |
| BDD `step is undefined` | `.feature` 里写的步骤跟 `step_defs/test_*.py` 里的 `@given/@when/@then` 装饰器字符串不匹配（中英文标点常坑） | 复制 `.feature` 里那一行原文到装饰器里，**包括标点和空格**；或者跑 `pytest --generate-missing` 让 pytest-bdd 帮你生成空模板 |
| `OPENCV_FFMPEG_CAPTURE_OPTIONS ... assertion failed` | cv2 在某个 import 之前就被 import 了，绕过了 backend/main.py:line 4 的 setdefault | 测试入口确保**先**走 conftest 的环境变量设置；不要在测试模块顶部 `import cv2` |

### 测试通过的判定

`exit_code == 0` 且 stdout 末尾出现 `N passed` 才算过。`N passed, M skipped` 也算过（skipped 是 `-k` 过滤或 `@pytest.mark.skip` 主动跳过的，不是失败）。

---

## 路径 B：写新测试

### 决定测试层级（决策树）

按改动性质从上往下匹配第一个命中的：

0. **客户现场验收/部署后验证**（如"部署完跑一遍确认通不通"）→ **路径 F**，跑 SAT 测试套件
1. **改了某个客户可感知的端到端工作流**（如"创建项目到看到检测结果全链路"）→ **L1 BDD + L4 E2E 组合**，先写 .feature 描述客户视角行为，再写 E2E 验证 UI 可观测结果
2. **改了某个业务流程，跨多个模块**（如"扫码后绑工件后推 MES 全链路"）→ **L1 BDD**，加 scenario 到 `tests/features/`
3. **改了多维度配置组合**（≥3 个互相影响的开关 / 模式）→ **L2 Pairwise**，新建 `tests/test_pairwise_<功能>.py`
4. **改了单个函数/方法/API 端点的输入输出**→ **L1 单元/集成**，新建或追加 `tests/test_<功能>.py`
5. **改了前端页面交互**（按钮、表单、跳转）→ **L4 Playwright**，加到 `tests/e2e_browser/`
6. **改了视频处理 / 推理链路**（且 mock 不靠谱）→ **L3 真实视频 sanity**，标 `@pytest.mark.slow`

### 功能测试 vs 单元测试 选择指南

| 问自己 | 答案 | 选择 |
|---|---|---|
| 客户能直接感知这个行为吗？ | 是 | BDD .feature（客户语言描述） |
| 需要跨 2+ 个 API 端点协作？ | 是 | BDD 或集成测试 |
| 需要验证 UI 上的视觉反馈？ | 是 | E2E Playwright |
| 只是一个函数的输入输出？ | 是 | 单元测试 |
| 配置组合爆炸（3+ 维度）？ | 是 | Pairwise |

### 项目特有的 6 个写测试坑点

每条都源自历史踩坑，**不要复活**：

#### 坑 1：MagicMock 自动生成属性会污染序列化

`MagicMock()` 对未显式设置的属性返回新的 `MagicMock` 实例（不是空 dict / None）。这些 MagicMock 在 JSON 序列化或 `dict.copy()` 时会爆。

**正确做法**：mock fixture 里**显式列出所有需要的属性**，给 dict / list / scalar 默认值。参考样板：

```python
def _make_mock_mgr(*, ...):
    mgr = MagicMock()
    mgr.step_durations = {}
    mgr.step_durations_history = {}
    mgr.step_cycle_durations = {}        # 新加属性别忘补
    mgr.step_cycle_durations_history = {}
    mgr.events_log = []
    mgr.is_detecting = False
    # ... 全列
    return mgr
```

ORM 模型加新字段时，所有引用它的 mock fixture **都要加**，否则跑老测试会报奇怪错。

#### 坑 2：测试 fixture 必须独立 DB，不要 reload uvicorn

v3.5.0 BDD 框架痛过：用 reload 让后端重新加载新的 fixture DB，结果 SQLAlchemy session pool 还指着老 DB，测试间串数据。

**正确做法**：每个测试**直接调 `TestClient`**（同一 FastAPI app 实例），不要 spawn uvicorn 子进程。`tests/conftest.py` 已经把 `TIANJUN_DATA_DIR` 锁到临时目录了，所有测试共享一个干净的临时 DB。

#### 坑 3：浏览器 E2E 数据必须用 `__e2e_` 前缀清理

`tests/e2e_browser/conftest.py` 在 session 结束时会扫 DB 删掉名字以 `__e2e_` 开头的项目/工件/规则。**测试创建数据时务必用这个前缀**，否则会留垃圾。

```python
project_name = f"__e2e_pt_合并档_测试_{uuid.uuid4().hex[:8]}"
```

#### 坑 4：BDD 中文场景的步骤匹配带标点和空格

`.feature` 文件里写的「假设 配置一条规则 interval=2」，`step_defs/test_*.py` 里 `@given('配置一条规则 interval=2')` 必须**字符级对齐**——多一个空格、半角变全角都会报 `step undefined`。

实操技巧：写 `.feature` 时复制旧 scenario 的步骤，**只改变量值不改文字结构**，最不容易出错。

#### 坑 5：FFmpeg / cv2 / SDK import 顺序敏感

`backend/main.py` 第 4 行有 `os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")`，必须在 `import cv2` 之前。测试模块**不要**在文件顶部直接 `import cv2`——`tests/conftest.py` 已经处理了这件事，让测试通过 `from backend.X import Y` 间接拿到。

涉及海康 SDK / TensorRT 等更重的模块时，把 import 放到 `def test_...` 函数体内（lazy import），让 conftest 先把环境准备好。

#### 坑 6：周期性测试不要清 counter

`reset_stats()` 显式保留了 `_periodic_counters` 和 `_run_on_start_pending`。测试要 reset 状态时，对周期性动作的 counter **手动清零**（直接赋值字典），不要靠 `reset_stats()`。

### 样板测试（直接照抄改）

| 想写 | 照抄哪个文件 |
|---|---|
| API 响应字段暴露测试（验证某 endpoint 返回了某新字段） | `tests/test_pt_ct_modes_exposure.py` 或 `tests/test_detection_results_exposure.py` |
| Pairwise 矩阵 | `tests/test_pairwise_periodic.py` 或 `tests/test_pairwise_export.py` |
| BDD 业务场景 | `tests/features/periodic_actions.feature` + `tests/step_defs/test_periodic_actions.py` |
| 集成链路（真 DB + 真 Jinja2 + 真磁盘 IO） | `tests/test_integration_cycle_end_chain.py` |
| 浏览器 E2E | `tests/e2e_browser/test_monitor_page.py` 或 `test_data_export_dialog.py` |
| 真实视频 sanity（@slow） | `tests/test_real_video_smoke.py` |

---

## 真实测试资产清单 (本机绝对路径)

> 当需要用真实视频 + 真实模型跑端到端（不走 synthetic mock）时, 用这里登记的资产, **不要再现编路径**或猜测试目录。
> 这部分内容是 agent 跑真实推理 UAT 时的事实清单, 路径以本机为准 (Linux qianqian 工作机).

### 顺序模式 (sequential) — 金龙串行流水线 (RFC 11 v3.14.0 demo 数据)

真实客户现场两工位串行流水线场景, **唯一一组**可以用来跑「串行流水线 (Workpiece Flow)」端到端的真实视频+模型组合.

**目录结构**:
```
/home/qianqian/1.py/output/金龙/
├── GW1/
│   ├── video/064b34b762c1b4e75ad338c6e519030c.mp4   # 1280x720 30fps 84s
│   └── model/bestGW1.pt                              # YOLO 7 类
└── GW2/
    ├── video/a4b8088d80fa2d7f42ada3e74220d878.mp4   # 960x544 29fps 135s
    └── model/bestGW2.pt                              # YOLO 2 类
```

**模型标签 (model.names)**:
- **GW1 (工位 1)** 7 类: `热水槽 / 下料 / 热水浸泡 / 浸泡结束 / 甩干 / 甩干结束 / 吹干`
- **GW2 (工位 2)** 2 类: `吹干 / 接触产品`

**为何是串行流水线的完美 demo 数据**:
- GW1 末步 `吹干` 和 GW2 首步 `吹干` 重叠 — 工件在 GW1 经过整套热水处理→甩干→吹干, 然后传到 GW2 继续吹干+接触产品检测.
- pipeline_config 自然按 sequential 顺序模式跑: GW1 上 7 步走完一个 cycle → 物料传到 GW2 → GW2 上 2 步走完一个 cycle → WorkpieceFlowCoordinator 把两个 cycle 串成同一工件流转记录.
- **强烈推荐**用 `WorkpieceFlowConfig.trigger_mode = "time_window"` (无扫码 FIFO 自动入队), 因为这俩视频也没扫码.

**典型用法**:
```python
GW1_VIDEO = "/home/qianqian/1.py/output/金龙/GW1/video/064b34b762c1b4e75ad338c6e519030c.mp4"
GW1_MODEL = "/home/qianqian/1.py/output/金龙/GW1/model/bestGW1.pt"
GW2_VIDEO = "/home/qianqian/1.py/output/金龙/GW2/video/a4b8088d80fa2d7f42ada3e74220d878.mp4"
GW2_MODEL = "/home/qianqian/1.py/output/金龙/GW2/model/bestGW2.pt"

# 配两个项目:
#   project_gw1: steps_config = 7 步, label 严格按 GW1.names 顺序
#   project_gw2: steps_config = 2 步, label 严格按 GW2.names 顺序
# 通道 0 跑 GW1, 通道 1 跑 GW2
# 创建 WorkpieceFlow: station_channel_ids=[0,1], trigger_mode=time_window
```

### 其他真实资产位置 (历史项目, 非串行流水线场景)

- 当前仓库 `backend/uploads/`: 只有 `放置、压墨-标注 (1).avi` + 2 个 `.pt` (OPPO 项目), **不适用串行流水线**
- 副本仓库 `/home/qianqian/桌面/word/tianjun副本/backend/uploads/`: 9+ 视频 + 多个模型 (QG_holder / QG_tuhei / 各种 best), 主要是 OPPO 单工位场景
- 测试 fixture `tests/test_real_video_smoke.py`: 硬编码引用副本仓库的视频 (跨仓库引用), 跑 `@slow` mark 时用

### 真实推理测试的写法约定

1. **永远用 `@pytest.mark.slow` 标记** — 默认不跑, 避免 CI / 单元测试套件被卡
2. **路径常量放文件顶部** — 不要散在函数里
3. **路径不存在时 `pytest.skip`** — 别人机器上没这些资产时不算 fail
4. **GPU 推理用 `RUNTIME_MODE=test` + 真实模型加载** — 不要走 synthetic mock, 否则没意义
5. **录视频 + 截图三件套** — 真实推理 UAT 必须有 `record_video_dir` + 关键步骤 `page.screenshot`

---

## 路径 C：端到端冒烟（启动后端 + 前端）

适用：用户说"启动起来看看通不通"、"页面能不能用"，或要跑路径 A 中的浏览器 E2E。

### 标准冒烟流程

```bash
# Terminal 1：启动后端（开发模式）
cd /home/qianqian/桌面/word/tianjun副本
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload

# 等输出 "Uvicorn running on http://0.0.0.0:8001"，约 3-8 秒（GPU 初始化 + 数据库迁移）

# Terminal 2：启动前端 dev 服务器
cd /home/qianqian/桌面/word/tianjun副本/frontend
npm run dev

# 等输出 "Local: http://localhost:6001/"，约 2-3 秒
```

### 健康检查

```bash
# 后端是否就绪
curl -s http://127.0.0.1:8001/api/v1/source/status | head -c 200

# 前端是否就绪
curl -sI http://127.0.0.1:6001/ | head -1

# 看实时日志（后端启动报错通常会卡在某行）
# 看 backend.main:app 启动 12 步是否走完
```

### 用户描述了具体功能时怎么手测

1. **先回想该功能在哪个页面**（参考 `AGENTS.md` 第六节「模块详解」找视图位置）
2. **走最短路径触发**：从默认初始状态 → 点 N 次按钮 → 看到结果
3. **观察 3 处**：浏览器 console（前端报错）、后端 stdout（异常 traceback）、目标元素的 DOM 状态
4. **对比预期**：用户最初描述的"应该看到 X"和当前 DOM/数据是否一致

---

## 路径 D：影响分析（改了 X，哪些测试要重跑）

按改动文件类型查表：

| 改了什么 | 必跑测试 |
|---|---|
| `backend/api/source.py` 或任意 `source_*_mixin.py` | `tests/test_pt_ct_modes_exposure.py` + `tests/test_detection_results_exposure.py` + `tests/test_integration_cycle_end_chain.py` + `tests/step_defs/test_periodic_actions.py` |
| `backend/api/source_periodic_actions_mixin.py` | `tests/test_periodic_actions_v352.py` + `tests/test_pairwise_periodic.py` + `tests/step_defs/test_periodic_actions.py` |
| `backend/services/export_*.py` 或 `backend/api/export_*.py` | `tests/step_defs/test_custom_export.py` + `tests/step_defs/test_realtime_rules.py` + `tests/test_pairwise_export.py` + `tests/test_integration_cycle_end_chain.py` |
| `backend/api/sessions_export.py` 或任何 CSV 导出 | `tests/test_csv_export_pt_ct_modes.py` |
| `backend/services/scanner.py` / `wmax.py` / `mes_hooks.py` | 暂无单元测试覆盖 — **必须**手测路径 C，或写新 BDD scenario 覆盖 |
| `backend/models/*.py`（ORM 模型） | **全部 mock fixture 都要扫一遍**（grep `MagicMock` + 改的字段名）；`tests/test_*_exposure.py` 全跑 |
| `backend/services/weighing_engine.py` / `backend/api/weighing.py` / `pipeline_config.weighing` 相关 | `tests/test_weighing_engine.py`（13 例：状态机/去皮/判定/落库）+ `tests/test_mock_weight_source.py`（无硬件模拟源） |
| `backend/services/external_device*.py`（外设协议/管线/稳态机） | `tests/test_weight_stabilizing_throttle_b2.py` + `tests/test_mock_weight_source.py` + `tests/test_fire_external_event.py`；碰称重业务再加跑上一行 |
| 前端 `Settings/index.vue` 或 `useSystemStore.js` | 暂无单元测试 — 跑 `tests/e2e_browser/test_monitor_page.py` 看用户配置变更是否被前端正确读取 |
| 前端 `Monitor/index.vue` | `tests/e2e_browser/test_monitor_page.py` |
| 前端 `Data/index.vue` 或导出对话框 | `tests/e2e_browser/test_data_export_dialog.py` + `tests/e2e_browser/test_realtime_rules.py` |
| 前端 `Project/index.vue` | `tests/e2e_browser/test_project_page.py` |
| 后端任意 API 端点新增/改字段 | 找对应 `tests/test_*_exposure.py`（命名约定：`test_<api名>_exposure.py`），没有就**新建**一个 |

### 改 ORM 模型时的特别提醒

加新字段（如 `step_cycle_durations`）时：

1. `grep -r "MagicMock" tests/` 找所有 mock fixture
2. 每个 fixture **手动**给新字段赋默认值（`{}` / `[]` / `None`）
3. 新字段如果出现在某个 API response 里，对应的 `test_*_exposure.py` 要加断言

**坑历史**：v3.6.1 PT 合并档发版时，加了 `step_cycle_durations` / `step_cycle_durations_history` 两个属性，最初忘了在 2 个 test fixture 里补，导致跑老测试时 MagicMock 自动生成的属性把 JSON 序列化炸了。

---

## 完成标志清单

不论走哪条路径，结束前自检：

- [ ] **路径 A**：`pytest exit_code == 0`，且失败用例已诊断/修复，没有"虚弱跳过"（用 `@pytest.mark.skip` 绕过未修的失败）
- [ ] **路径 B**：新代码至少 1 个测试覆盖（或显式说明"为何不需要"，比如纯 UI 调整）；新加 ORM 字段的话，所有 mock fixture 已扫过
- [ ] **路径 C**：用户描述的功能在 UI 上能跑通；浏览器 console 和后端 stdout 都没新增红色异常
- [ ] **路径 D**：影响分析表里标出的所有测试都已重跑过；新加 fixture / 改 fixture 都验证过
- [ ] **铁律 — UI 改动验收**（见下节）：所有带 UI 入口的功能改动，必须有 E2E 用例真实点过那个按钮 / 输入框，并断言后端落库或 GET 接口反映出预期变化

跑完测试要给用户一句话总结：**`<层级>: N passed / M failed / K skipped (耗时 Xs)`**，然后才能往下走。

---

## 铁律：带 UI 的功能改动必须走 E2E 点击验证

**规则**：只要新功能在前端有任何一个按钮 / 输入框 / 弹窗 / 下拉，**API 层 BDD/单测过不算验收完成**。必须再加一条 E2E 用例完成"端到端 UI→后端"链路：

```
真实点击 UI → 后端日志/数据库/GET 接口 应有预期变化 → 才算验收
```

### 为什么 BDD/单测不能替代 E2E

| 风险 | BDD 能拦下 | E2E 能拦下 |
|---|---|---|
| 后端逻辑错 | ✅ | ✅ |
| API schema/参数错 | ✅ | ✅ |
| 前端 axios 客户端没传新字段 | ❌ | ✅ |
| 前端按钮 disabled 条件写错（点不动） | ❌ | ✅ |
| 前端表单校验阻断了提交 | ❌ | ✅ |
| 前端 store/响应式没绑对 | ❌ | ✅ |
| 弹窗 selector 漂移（el-message-box DOM 变了） | ❌ | ✅ |
| 中文按钮文案改了导致 click_text 失效 | ❌ | ✅ |
| el-select option 渲染异步导致 first-render 抓不到 | ❌ | ✅ |

### 强制检查清单（开发任意 UI 改动后必填）

| UI 改动类型 | 必须有的 E2E 步骤 |
|---|---|
| 新增输入框 | 1) 用 POM `fill_xxx()` 填值 2) GET `input_value()` 读回 3) 点关联按钮 4) **后端 GET / DB 查询验证已落** |
| 新增按钮 | 1) `click_text()` 或 POM 方法点它 2) 验证产生的页面状态变化（toast/路由/列表项） 3) **后端 API 验证副作用** |
| 新增弹窗 | 1) 触发弹窗 2) `wait_for_selector` 弹窗内元素 3) 填值/选择 4) 点确认/取消 5) 弹窗消失 6) 后端验证 |
| 新增下拉/select 选项 | 1) 点开 dropdown 2) 验证选项可见 + **未被 disabled** 3) 选中 4) 验证 v-model 绑定值正确 5) 提交后后端验证 |
| 解锁原本 disabled 的选项 | **必须** E2E 验证 disabled 属性已移除 + 选中后能正常提交（之前我犯过：把 `disabled` 删了但没测，结果客户那边因为缓存没生效） |
| 列表项 inline 操作（重命名 / 删除等） | 1) E2E 进列表页 2) 真实点该项的按钮 3) 弹窗交互完成 4) 列表项 UI 已刷新 5) **后端 GET 验证已变** |

### POM 写法约定

- **每个 UI 元素都要在 `pages/*.py` 里有 selector 常量**，不要在 test 里硬写 selector
- **每个 UI 操作都要有 POM 方法**，不要在 test 里直接调 `page.locator(...)`
- **断言"UI 显示了什么"** 用 POM `get_xxx()` / `has_xxx()`，**断言"后端落库了什么"** 用 `requests.get(api_url, ...)` 或直接读 SQLAlchemy session
- 弹窗类用 ElMessageBox 时，selectors 用 `.el-message-box__input input` / `.el-message-box__btns button.el-button--primary` —— 已被项目验证可工作

### 反例（这次档 3 验收时差点漏掉的 4 个）

| 改动 | API/BDD 测了 | E2E UI 测了 | 危险？ |
|---|---|---|---|
| Monitor《会话 ID》输入框 | ✅ 9 个 BDD | ❌（只测了输入框可填，没测点"开始"链路） | **是**：可能输入框值没传给 axios |
| Data《重命名》黄色按钮 | ✅ PATCH 端点 BDD | ❌ 完全没测 | **是**：按钮可能根本点不开弹窗 |
| RealtimeRulesDialog session_end 选项 | ✅ POST 规则 BDD | ❌ 完全没测 | **是**：可能 `disabled` 没去干净 |
| CustomExportDialog Session 下拉 | ❌ 也没 BDD | ❌ 完全没测 | **是**：双盲，唯一保险靠手测 |

**所以**：发完后端 + BDD 后**不要就报"验收通过"**，必须先看一眼自己改了哪些 .vue 文件，按上面表格补 E2E。否则等于把 UI bug 推给客户测。

### 反例（v3.7.0 重复 label 序列 — 测试假阳性教训）

**客户反馈**：序列设为「撕膜-顶卡拖-撕膜-顶卡拖-点亮屏幕」（同一动作名出现两次），完整做完一圈 5 步后，系统判 NG。

**当时我做了什么**：

1. 在结算判定那一段加了"按次数对比"的逻辑
2. 写了 9 个单元测试，**直接构造"已经包含重复 label 的步骤序列"喂给结算函数**
3. 9 个全绿，跟用户报"验收通过"

**实际暴露的真相**：

- 在结算之前，还有一道**上游的"周期内步骤记录清理"**会**先把同 label 多次出现合并成一次**
- 真实数据流走到结算函数时，重复 label 已经看不见了，加的判定逻辑根本没机会发挥作用
- **9 个单元测试是测试设计错误的产物**：它们绕过了真实数据流的上游环节，构造的输入跟生产路径不一致 → 表面"测了"，实际"没盖到"

**根因分类**：

| 维度 | 错在哪 | 对应铁律 |
|---|---|---|
| 理解层面 | 没追完"动作出现 → 帧累积 → 步骤记录 → 周期清理 → 结算判定 → 写库" 整条链路就动手 | 铁律 1 / 铁律 7 |
| 测试设计 | 单元测试构造好"上游已经处理过"的输入，绕过了真实上游 | 铁律 3 / 铁律 4 |
| 验收路径 | 自动化全绿就报"完成"，没用虚拟剧本走完整链路、没开真浏览器看 UI 上 5 个步骤卡是不是真的全绿 | 铁律 2 / 铁律 6 |

**正确做法（从今往后）**：

1. 写一段虚拟剧本：60fps 时间线先让"顶卡拖"出现 1 秒、消失、再"撕膜"出现 1 秒、消失，再重复一次顶卡拖+撕膜，最后"点亮屏幕"
2. 用这段剧本启 synthetic 检测，跑完一圈，断言：cycle 应该判合格、5 个步骤卡都应该是绿色
3. 在 bug 未修复的代码上跑 → 必须 FAIL（合格变 NG 或步骤卡只亮 3 个）
4. 改代码（包括上游清理逻辑 + 结算判定逻辑两处）
5. 再跑同一段剧本 → 必须 PASS
6. 把这段剧本归档到 `tests/scenarios/dup_label_in_sequence.json` + `tests/uat/uat_20260511_dup_label.py`

**这次为什么没做到**：

- 把"修了 bug 测一下"做窄成"给改动的函数加单元测试"
- 没有用既有的虚拟剧本能力跑端到端
- 没有开浏览器看一眼 5 个步骤卡的颜色
- 没有先反问"这条客户场景的整条链路上还有几个分支节点"

---

## v3.7.0 新增验收点（合并 feat/plugin-config + feat/multi-model-roi-link 后）

> 这一节是**当晚合并并修复 3 个客户 bug 后沉淀的踩坑**。下次跑测试 / 验收时优先扫一遍。

### 必跑场景

| 场景 | 入口 / 覆盖文件 | 期望 |
|---|---|---|
| **MES 实时上传 (cycle_end 无扫码场景)** | `tests/features/mes_scan_workflow.feature` 的 `cycle_end 即使未绑定 workpiece 也应能进入 MES 分发链路` 场景 | gateway.dispatch("cycle_end") 被调一次 |
| **A-B-C-B-D 期望序列重复 label** | ⚠️ 已知**测试假阳性**：单元测试 9 个全绿但**没盖到上游清理路径**。正确验收要走 path G + path H：用虚拟剧本「连做 5 步含重复 label」+ 真浏览器看 5 个步骤卡是否真的全绿。详情见上文「反例（v3.7.0 重复 label 序列 — 测试假阳性教训）」 | 单元测试全绿 **+** 端到端剧本未修版 FAIL → 修复后 PASS **+** 浏览器看到 5 卡全绿 |
| **cycle.steps 在 session 范围 export 里非空** | `tests/features/session_naming.feature` 的 `session 范围导出 stats.cycles 里每个 cycle 都带 steps 列表` 场景 | stats.cycles[*].steps + interval + event 都有 |
| **会话 ID 自定义** | Monitor 输入框 → 后端 detection_sessions.name 落盘 + Data 页能改名 | `tests/e2e_browser/test_sat_session_naming_ui.py` 全 4 条绿 |
| **插件系统三层** | `tests/plugin_system/` + `tests/step_defs/test_plugin_*.py` | 签名往返 / 安装 API / CLI 工具全绿 |
| **多模型 ROI 路由** | `tests/test_inference_router.py` + `tests/test_source_routes_multi.py` | 多模型 dispatch + ROI 中心点裁剪都对 |

### 已知陷阱（这次掉过的坑）

1. **`tests/sat/conftest.py` 的 `pytest_collection_modifyitems` 必须只动 SAT items**。
   v3.7.0 前一个版本的 hook 用 `for item in items` 扫整个 root 收集集合，
   导致跑 `pytest tests/` 时 382 全 skip。**必须**用 `fspath` 过滤再加 marker。
   现版本已加 `_is_sat_item()` filter。

2. **MES BDD background 不能 ping `/api/v1/mes/config`（这条路由不存在）**，
   要 ping `/api/v1/mes/orders`。否则所有 MES 场景静默 skip。

3. **synthetic + 单测批跑会 pollution**（`tests/test_synthetic_*` 在 batch 末尾偶发 fail，
   单独跑 6/6 绿）。怀疑是 `MESHook`/`ChannelManager` 模块级单例没被前面测试干净复位。
   **临时方案**：发版前单独再跑 `pytest tests/test_synthetic_*.py` 确认绿，
   长远要给这三类组件加 conftest function-scope reset fixture。
   **v3.12 实测**：合并 `feat/per-item-enhance` 后新加的 `tests/test_per_item_v310_features.py`
   也吃这一份毒: 单跑 7/7 全绿, 跟其他 600+ 测试一起跑 2 个 fail (require_exact_count /
   disable_auto_settle). 同样属于 module-level 单例污染家族, 不阻塞发版。

4. **测试 fixture 与生产代码字段名漂移**（v3.12 修过 `test_periodic_actions_v352`）：
   测试 fixture 给 `trigger_labels: [...]`, 但生产代码 `_apply_periodic_actions` 读的是
   `trigger_step_ids` 或 `trigger_step_labels` (没 _step 前缀的不读)。结果 rule 解析后
   `trigger_labels` 集合空 → 触发器被跳过 → 旁路账本永远空 → 6 个测试全 fail。
   **常见踩坑**：写测试 fixture 时一定对照生产代码 `_apply_*` 解析逻辑里的 `raw.get('xxx')` 字段名,
   别凭印象写。新加生产字段时搜全测试 fixture 是否要同步。

5. **MagicMock 自动属性导致路由走错路径**（v3.12 修过 `test_pt_ct_modes_exposure`）：
   `MagicMock()` 默认对未显式设置的属性返新 `MagicMock` 实例 (不是 `None`/`AttributeError`)。
   生产代码 `getattr(mgr, 'method', None)` 永远拿到一个新 mock → `is not None` 误为真 →
   `round(MagicMock, 2)` 返 dict → 测试断言 `<` 报 `TypeError`。
   **修法**：测试 fixture 把不想被路由调到的方法 / 属性显式设 `None`, 让 `is not None` 守门生效。
   长远建议用 `spec=RealClass` 限定 MagicMock 的属性集。

6. **测试环境缺 `jsonschema` 等可选依赖**（v3.12 装过）：
   `backend/plugin_system/_plugin_common.py: validate_manifest` 用 jsonschema 库做 schema 严格校验,
   缺依赖时静默走 fallback 模式只校验 3 个粗粒度字段 (required / customer_code / plugin_version) →
   stores prefix / table prefix / adapter prefix / hook enum 等 67 个细粒度测试集体降级 fail。
   **必装清单**: `jsonschema` (plugin_system 用), `allpairspy` (test_pairwise_*.py 用)。
   建议在 `requirements-dev.txt` 显式声明。

7. **合并后必须冒烟 `python -c "from backend.main import app"`**：
   两条 v3.6.0 分支并行改动 mes_hooks / source_settlement / migrate_database，
   合上来可能引入隐式 NameError / ImportError 只有 import 时才会暴露。

### 跑全套测试的标准命令（v3.7.0 之后）

```bash
# Layer 1: 单元 + BDD
pytest tests/ --ignore=tests/e2e_browser --ignore=tests/plugin_system -q

# Layer 2: 插件系统（独立）
pytest tests/plugin_system/ -q

# Layer 3: E2E (Playwright, 需要前端已 build / 在 dev)
pytest tests/e2e_browser/ -q

# Layer 4: synthetic 单独再跑一次防 pollution
pytest tests/test_synthetic_detection_api.py tests/test_synthetic_full_flow.py tests/test_synthetic_project_restore.py -v
```

---

## 一些不要做的事

- ❌ **跑全套测试不带 `-m "not slow"` 默认**（`@slow` 真实视频测试需要本地有 `tests/sample_video.mp4` 等资产，CI 没装会大量 SKIP，不是 fail 但浪费时间）
- ❌ **直接编辑 `pytest.ini` 改 marker / addopts**（除非用户明确要求；这是项目根级配置）
- ❌ **写测试时 mock 整个 `VideoSourceManager`**（用 `_Stub(MixinName)` 风格——继承 mixin 拼最小 stub，mock 整个类会让测试失去信号）
- ❌ **跑 e2e_browser 时不启前端 dev 服务器**（直接报 TargetClosedError，浪费 15 分钟才发现）
- ❌ **新写的测试默认放 `tests/` 根**（按层级分目录：BDD 进 `step_defs/` + `features/`、E2E 进 `e2e_browser/`、Pairwise 命名 `test_pairwise_*.py`）

---

## 路径 E：Playwright 自动化前端验证

适用：用户说"截图看看页面对不对"、"UI 改了帮我验证一下"、"前端自动化测试"，或需要在不手动打开浏览器的情况下验证前端渲染结果。

### 决策树：选择验证方式

```
用户任务 → 是静态 HTML 吗？
    ├─ 是 → 直接读 HTML 文件找选择器
    │        ├─ 成功 → 写 Playwright 脚本验证
    │        └─ 失败/不完整 → 当动态应用处理（下方）
    │
    └─ 否（动态 Web 应用）→ 服务器已经在跑了吗？
        ├─ 否 → 先启动后端 + 前端（见路径 C 标准冒烟流程）
        │        然后写 Playwright 脚本验证
        │
        └─ 是 → 侦察-再-行动模式：
            1. 导航到目标页面，等待 networkidle
            2. 截图 或 检查 DOM
            3. 从渲染结果中识别选择器
            4. 用发现的选择器执行操作验证
```

### 核心原则：侦察再行动

**绝对不要**在动态应用上跳过 `networkidle` 就检查 DOM。Vue3 SPA 需要 JS 执行完毕才有真实内容。

### 启动服务器 + 运行自动化脚本

**单服务器（仅前端 dev）：**

```bash
# 前端已启动的情况下：可在 /tmp 写临时 playwright_verify.py 快速验证，
# 稳定后迁入 tests/e2e_browser/ 纳入回归。
```

**双服务器（后端 + 前端）：**

```bash
# Terminal 1: 后端
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload

# Terminal 2: 前端
cd frontend && npm run dev

# Terminal 3: 自动化验证（临时脚本放 /tmp，稳定后迁入 tests/e2e_browser/）
```

### Playwright 脚本模板（项目适配版）

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # 导航到目标页面（本项目前端 dev 端口 6001）
    page.goto('http://localhost:6001')
    page.wait_for_load_state('networkidle')  # 关键：等 Vue3 渲染完成

    # --- 侦察阶段 ---
    # 截图查看当前页面状态
    page.screenshot(path='/tmp/page_current.png', full_page=True)

    # 获取页面 DOM 内容
    content = page.content()

    # 发现所有按钮
    buttons = page.locator('button').all()

    # --- 行动阶段 ---
    # 示例：点击导航到 Monitor 页
    page.locator('text=Monitor').click()
    page.wait_for_load_state('networkidle')
    page.screenshot(path='/tmp/monitor_page.png', full_page=True)

    # 示例：验证某个元素存在
    assert page.locator('.detection-status').is_visible()

    browser.close()
```

### 项目页面路由速查

| 页面 | URL 路径 | 验证重点 |
|---|---|---|
| Monitor | `/#/monitor` | 检测状态卡片、视频流、FPS 显示 |
| Project | `/#/project` | 配置表单、步骤列表、模型选择 |
| Data | `/#/data` | Session 列表、导出按钮、统计图表 |
| Source | `/#/source` | 视频源配置、连接状态 |
| Settings | `/#/settings` | 系统设置、GPU 分配、报警配置 |

### 常用验证模式

#### 1. 截图对比验证

```python
# 改动前截图（基线）
page.screenshot(path='/tmp/before.png', full_page=True)

# 执行操作...

# 改动后截图
page.screenshot(path='/tmp/after.png', full_page=True)
```

#### 2. DOM 元素断言

```python
# 验证元素可见
assert page.locator('.el-card__header').first.is_visible()

# 验证文本内容
text = page.locator('.cycle-count').text_content()
assert '0' in text

# 验证元素数量（如多工位卡片）
cards = page.locator('.channel-card').count()
assert cards >= 1
```

#### 3. 表单交互验证

```python
# ElementPlus Select 组件
page.locator('.el-select').first.click()
page.locator('.el-select-dropdown__item >> text=模型A').click()

# ElementPlus Input
page.locator('input[placeholder="请输入项目名称"]').fill('__e2e_test_project')

# ElementPlus Button
page.locator('.el-button--primary >> text=保存').click()
page.wait_for_load_state('networkidle')
```

#### 4. 浏览器控制台日志捕获

```python
console_messages = []
page.on('console', lambda msg: console_messages.append(f"{msg.type}: {msg.text}"))

page.goto('http://localhost:6001')
page.wait_for_load_state('networkidle')

# 检查是否有错误
errors = [m for m in console_messages if 'error' in m.lower()]
if errors:
    print(f"前端控制台错误: {errors}")
```

### 与现有 E2E 测试的关系

- `tests/e2e_browser/` 下的 pytest-playwright 测试是**正式回归测试**，跑 `pytest tests/e2e_browser/ -v`
- 路径 E 的 Playwright 脚本是**快速验证工具**，用于改动后即时确认 UI 状态，不一定要入库
- 如果验证脚本稳定且有复用价值，应迁移到 `tests/e2e_browser/` 作为正式 E2E 用例

### 选择器优先级（本项目）

1. `data-testid` 属性（最稳定，如果组件有的话）
2. ElementPlus 组件类名：`.el-button`, `.el-card`, `.el-table`
3. 文本选择器：`text=保存`, `text=Monitor`
4. Role 选择器：`role=button[name="提交"]`
5. CSS 选择器：`.channel-card`, `#app`

### 常见坑点

| 问题 | 原因 | 解决 |
|---|---|---|
| DOM 为空 / 只有 `<div id="app"></div>` | 没等 `networkidle`，Vue 还没挂载 | 加 `page.wait_for_load_state('networkidle')` |
| ElementPlus 下拉选项点不到 | 下拉面板是 teleport 到 `body` 的，不在原组件 DOM 树内 | 用 `page.locator('.el-select-dropdown__item >> text=XXX')` 全局找 |
| 截图全白 | headless 模式下页面尺寸为 0 | `browser.new_page(viewport={'width': 1920, 'height': 1080})` |
| 页面跳转后元素找不到 | 路由切换有过渡动画 | 跳转后加 `page.wait_for_load_state('networkidle')` 或 `page.wait_for_selector('.target-element')` |
| `net::ERR_CONNECTION_REFUSED` | 后端或前端 dev 服务器没启动 | 先走路径 C 启动服务器 |

---

## 路径 F：现场验收测试（Site Acceptance Testing）

适用：部署到客户现场后的功能验收、换产后确认、客户投诉后复现。用户说"验收"、"现场测试"、"部署后验证"、"客户场景"、"换产测试"、"全流程跑一遍"时触发。

### 验收模式选择

| 场景 | 模式 | 工具 |
|---|---|---|
| 有显示器 + 键鼠 | 全自动 Playwright | `pytest tests/e2e_browser/test_sat_*.py` |
| 仅 SSH 远程 | API 半自动 | `pytest tests/sat/ -v` |
| 无网络 | 人工引导清单 | 打印 SAT checklist |

### 全自动验收命令

```bash
# 前置：后端 8001 + 前端 6001 已启动，至少一个项目已配置

# 跑全部验收场景（约 3-5 分钟）
pytest tests/e2e_browser/test_sat_full_workflow.py -v

# 只跑视频源验收
pytest tests/e2e_browser/test_sat_full_workflow.py -k "source" -v

# 只跑检测流程验收
pytest tests/e2e_browser/test_sat_full_workflow.py -k "detection" -v

# 换产验收
pytest tests/e2e_browser/test_sat_full_workflow.py -k "changeover" -v
```

### 验收检查清单（7 大项）

| # | 验收项 | 通过标准 | 对应测试 |
|---|---|---|---|
| 1 | 视频源连接 | 画面出现 + FPS > 0 | `test_sat_source_connected` |
| 2 | 项目激活 | 激活后 Monitor 显示项目名 | `test_sat_project_active` |
| 3 | 检测启动 | 点开始后状态变"检测中" + 计数器开始跑 | `test_sat_detection_running` |
| 4 | OK/NG 判定 | 至少 1 个 cycle 完成 + 结果正确 | `test_sat_judgment_correct` |
| 5 | 数据记录 | Data 页能看到刚才的 session | `test_sat_data_recorded` |
| 6 | 导出功能 | 自定义导出生成文件 + 内容非空 | `test_sat_export_works` |
| 7 | 报警联动 | 触发 NG 事件 → 报警设备响应 | `test_sat_alarm_triggers` |

### API 半自动验收模板（无浏览器场景）

```python
# tests/sat/test_sat_api.py
import requests
API = "http://localhost:8001"

def test_sat_01_source_status():
    """验收项1：视频源已连接"""
    r = requests.get(f"{API}/api/v1/source/status")
    assert r.status_code == 200
    data = r.json()
    assert data.get("is_running") is True, "视频源未运行"
    assert data.get("fps", 0) > 0, "FPS 为 0，画面可能卡住"

def test_sat_02_active_project():
    """验收项2：有激活项目"""
    r = requests.get(f"{API}/api/v1/projects/active/current")
    assert r.status_code == 200
    assert r.json() is not None, "无激活项目"

def test_sat_03_detection_running():
    """验收项3：检测正在运行"""
    r = requests.get(f"{API}/api/v1/source/status")
    assert r.json().get("is_detecting") is True, "检测未启动"

def test_sat_04_cycles_produced():
    """验收项4：有检测结果"""
    r = requests.get(f"{API}/api/v1/sessions?limit=1")
    sessions = r.json().get("items", [])
    assert len(sessions) > 0, "无 session 记录"
```

### 换产验收要点

换产 = 切换激活项目。验收重点：
1. 旧项目的 MES 扫码状态已清除
2. 新项目的模型已加载（Monitor 显示新项目名）
3. 检测启动后步骤列表与新项目一致
4. 计数器归零

### 常见验收失败及处理

| 失败项 | 常见原因 | 快速修复 |
|---|---|---|
| 视频源未连接 | 摄像头 USB 松动 / IP 变了 | 重新配置 Source 页 |
| 检测不启动 | 模型文件缺失 / GPU 驱动问题 | 检查 Model 页模型状态 |
| 计数器不跑 | project_config 未正确下发 | 重新激活项目 |
| 导出文件为空 | 模板语法错误 | 用 preview API 调试模板 |
| 报警不响 | 串口未连接 / 权限不足 | 检查 /dev/ttyUSB* 权限 |

---

## BDD 场景库扩展指南

### 待建 .feature 文件规划

| 文件 | 优先级 | 场景数 | 客户工作流 |
|---|---|---|---|
| `core_detection_flow.feature` | P1 | 10 | 创建项目→激活→检测→步骤流转→OK/NG |
| `source_connection.feature` | P1 | 8 | 连接各类视频源→验证→断开重连 |
| `mes_scan_workflow.feature` | P1 | 10 | 扫码→绑工件→检测→推MES |
| `alarm_event_chain.feature` | P2 | 7 | 事件触发→报警匹配→设备动作 |
| `product_changeover.feature` | P2 | 6 | 换产→切项目→模型重载→计数器清零 |

### 中文 BDD 模板

```gherkin
# language: zh-CN
功能: [客户工作流名称]
  作为 [角色：操作员/质量工程师/产线主管]
  我希望 [期望行为]
  以便 [业务价值]

  背景:
    假设 系统已启动且视频源已连接

  场景: [用客户语言描述的具体场景]
    假设 [前置状态]
    当 [用户操作]
    那么 [可观测结果]
    并且 [附加验证]
```

### step_defs 实现模式选择

| 场景类型 | 实现方式 | 示例 |
|---|---|---|
| 纯后端逻辑（状态机/计数器） | StubHost + mixin 直接调用 | `test_periodic_actions.py` |
| API 级别（CRUD/状态查询） | TestClient HTTP 调用 | `test_realtime_rules.py` |
| 跨模块集成（检测→导出→报警） | TestClient + DB 验证 | 新建 |

### 核心场景列表

**core_detection_flow.feature（P1）：**
1. 创建新项目并选择模型后项目出现在列表中
2. 激活项目后 source/status 返回该项目 ID
3. 视频源运行中启动检测后 is_detecting 变为 true
4. 顺序模式下步骤按配置顺序流转
5. 所有步骤 OK 时 cycle 判定为 OK
6. 任一步骤 NG 时 cycle 判定为 NG
7. cycle 完成后 total/ok/ng 计数器正确递增
8. 停止检测后 session 正确关闭且 end_time 非空
9. 检测模式下无步骤流转直接出 OK/NG
10. 暂停检测后画面冻结但 session 不关闭

**mes_scan_workflow.feature（P1）：**
1. 扫码器扫入条码后 workpiece 状态变为 registered
2. 检测启动后 workpiece 状态变为 inspecting
3. cycle 完成 OK 后 workpiece 状态变为 ok
4. cycle 完成 NG 后 workpiece 状态变为 ng
5. 未扫码就开始检测时触发 warn_no_barcode 事件
6. 容器模式下多个工件绑同一 box_serial
7. box 内所有工件检完后触发 box_complete
8. 工单完成数量达到 planned_qty 后工单状态变 completed
9. 换产时旧工件的 pending 状态被清除
10. 模拟扫码 API 能正确触发绑定流程

---

## E2E 覆盖扩展指南

### Page Object 模式（Python pytest-playwright 版）

目录结构：
```
tests/e2e_browser/
├── pages/                    # Page Object 类
│   ├── base_page.py         # 基类：navigate + wait_ready
│   ├── monitor_page.py      # Monitor 页
│   ├── project_page.py      # Project 页
│   ├── source_page.py       # Source 页
│   ├── data_page.py         # Data 页
│   └── settings_page.py     # Settings 页
├── test_sat_full_workflow.py # SAT 全流程验收
├── test_source_page.py      # Source 页测试
├── test_settings_page.py    # Settings 页测试
└── test_alarm_page.py       # Alarm 页测试
```

### BasePage 模板

```python
class BasePage:
    route = ""

    def __init__(self, page, base_url: str):
        self.page = page
        self.base_url = base_url

    def navigate(self):
        self.page.goto(f"{self.base_url}/#{self.route}")
        self.page.wait_for_load_state("networkidle")

    def wait_ready(self):
        pass
```

### 待建 E2E 测试文件

| 文件 | 覆盖页面 | 测试数 |
|---|---|---|
| `test_source_page.py` | Source 页 - 视频源配置 | 5-6 |
| `test_settings_page.py` | Settings 页 - 系统配置 | 4-5 |
| `test_alarm_page.py` | Alarm 页 - 报警配置 | 3-4 |
| `test_sat_full_workflow.py` | 跨页面全流程验收 | 8-10 |

### 检测状态处理策略

| 策略 | 适用场景 | 方式 |
|---|---|---|
| 视频文件模式 | CI / 确定性测试 | 上传预录视频，循环播放，结果可预测 |
| Mock 状态 API | UI-only 验证 | 只检查前端是否正确反映后端状态 |
| 真实推理 | 现场验收 | 真实摄像头 + 真实模型，验证完整链路 |

---

## 客户工作流 → 测试覆盖映射表

| # | 客户工作流 | BDD | E2E | 单元/集成 | 状态 |
|---|---|---|---|---|---|
| 1 | 创建项目→选模型→激活→检测→看结果 | `core_detection_flow` | `test_sat_full_workflow` | `test_pt_ct_modes_exposure` | **待建** |
| 2 | 连接摄像头/视频→验证画面 | `source_connection` | `test_source_page` | — | **待建** |
| 3 | 检测运行→步骤流转→OK/NG | `core_detection_flow` | `test_sat_full_workflow` | `test_integration_cycle_end_chain` | **部分** |
| 4 | 配置报警→事件触发→设备响应 | `alarm_event_chain` | `test_alarm_page` | — | **待建** |
| 5 | 扫码→绑工件→推MES | `mes_scan_workflow` | — | — | **待建** |
| 6 | Data页→筛选→导出 | `custom_export` ✅ | `test_data_export_dialog` ✅ | `test_csv_export` ✅ | **已有** |
| 7 | 多工位→通道隔离 | — | `test_source_page` | — | **待建** |
| 8 | 操作员登录→License验证 | — | — | — | **待建** |
| 9 | 换产→切项目→新配置生效 | `product_changeover` | `test_sat_full_workflow` | — | **待建** |
| 10 | 周期性保养→到期提醒 | `periodic_actions` ✅ | — | `test_periodic_actions_v352.py` ✅ | **已有** |
| 11 | 实时规则→触发→文件生成 | `realtime_rules` ✅ | `test_realtime_rules` ✅ | `test_integration_cycle_end_chain` ✅ | **已有** |

---

## 路径 G：虚拟功能测试模式（synthetic 剧本源）

> **触发关键词**：虚拟检测 / 虚拟模式 / 模拟检测 / 没视频测一下 / 没模型测一下 / 真的跑一下 / 合成数据 / 剧本 / synthetic / 无模型测功能 / 客户场景模拟 / 改了代码后帮我跑一下功能。

### G.0 路径选择（先于动手）

```
用户改了功能/修了 bug，要"看看通不通"
    │
    ├── 有真摄像头 + 真模型？
    │       └─ 是 → 路径 F（现场验收）
    │
    ├── 没硬件，但只想验证 UI 渲染？
    │       └─ 是 → 路径 E（Playwright + Mock /detection/results）
    │
    └── 没硬件，但要看"后端真业务流 → 前端真显示"？
            └─ 是 → **路径 G（本节）**
```

### G.1 这个 synthetic 源是什么（必读，给后续 AI/人）

`backend/api/source_synthetic_mixin.py` 给 `VideoSourceManager` 加了一种新的 `source_type='synthetic'`：

- **不需要摄像头、不需要模型**：捕获线程出黑底帧（带帧号 OSD），推理线程在选择推理函数时**短路掉真实模型**，直接按 JSON 剧本返回 detections
- **走完整 pipeline**：`_update_step_stats` → `_settle_*_cycle` → `_trigger_event` → `end_cycle` → 实时导出/MES Hook 与真实路径**完全相同**
- **生产默认禁用**：测试控制 API `/api/v1/test/synthetic/*` 仅在 `RUNTIME_MODE=test` 时挂载（见 `backend/main.py`）；客户机不设环境变量等同没有这个能力
- **可派别用途**：销售演示（无产线设备）、客户培训、烤机脚本、客户问题复现

> 改这个能力会动 6 个文件：`source.py / source_capture_loop_mixin.py / source_inference_loop_mixin.py / source_lifecycle_mixin.py / source_state_init.py / source_routes.py`。修改前先读 `modify-source` skill 做影响分析。

### G.2 一次完整功能测试的 5 步操作手册

每次走路径 G，**严格按以下 5 步**：

#### 第 1 步：启动后端（带测试模式）

```bash
cd /home/qianqian/桌面/word/tianjun副本
RUNTIME_MODE=test python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
# 看到日志 "[RUNTIME_MODE=test] mounted /api/v1/test/synthetic/*" 即就绪
```

启动失败排查：见 G.5 踩坑表第 1 行。

#### 第 2 步：选剧本

```bash
ls tests/scenarios/
# smoke_static_label.json     ← 最简：单标签持续出现，纯 API 冒烟
# ok_sequential_cycle.json    ← 顺序模式 OK 周期（step_a → step_b → step_c）
# ng_missing_step.json        ← 顺序模式 NG（缺 step_b）
# alarm_event.json            ← 持续触发同一标签（配合 events_config 验证报警链）
```

不知道选哪个？看 G.4 推荐表（按改动文件→剧本映射）。

#### 第 3 步：启动剧本 + 启动检测

```bash
# 用文件
curl -sX POST 'http://127.0.0.1:8001/api/v1/test/synthetic/start' \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"ok_sequential_cycle.json","channel":0}'

# 或用内联字典（不用落盘）
curl -sX POST 'http://127.0.0.1:8001/api/v1/test/synthetic/start' \
  -H 'Content-Type: application/json' \
  -d '{"scenario_json":{"name":"adhoc","fps":60,"timeline":[{"from":0,"to":120,"detections":[{"label":"X","confidence":0.9,"bbox":[0.2,0.2,0.2,0.2]}]}]}}'

# 推荐：with_project=true 让后端自动按剧本里的 label 配最小项目
# 这样 cycle 才会真结算 (step_counts/cycle_count 才会动)
curl -sX POST 'http://127.0.0.1:8001/api/v1/test/synthetic/start' \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"ok_sequential_cycle.json","with_project":true}'

# 启动检测（model_path 可以省略，因为 source_type=synthetic）
curl -sX POST 'http://127.0.0.1:8001/api/v1/source/detection/start?channel=0' \
  -H 'Content-Type: application/json' \
  -d '{"conf":0.25,"iou":0.45}'
```

#### 第 4 步：观察前端 / API 反馈

**A. 浏览器（首选）**：
```
http://127.0.0.1:6001/#/monitor   # cycle 计数器、步骤列表、检测框
http://127.0.0.1:6001/#/data      # session 与 cycle 历史
```

**B. API 直读（无前端时）**：
```bash
curl -s 'http://127.0.0.1:8001/api/v1/source/detection/results?channel=0' | jq '.detections,.step_counts,.is_detecting'
curl -s 'http://127.0.0.1:8001/api/v1/test/synthetic/state?channel=0'
```

**C. Playwright 自动化截图 + 断言**：

```bash
# 演示脚本（已带 5 步流程：启剧本 → 启检测 → 截图 → 断言 → 清理）
python tests/playwright_demo/synthetic_demo.py tests/scenarios/ok_sequential_cycle.json
# 截图默认落 /tmp/synthetic_demo_shots/<scenario>.png
```

写新断言时直接照抄 `tests/playwright_demo/synthetic_demo.py` 的模板（已包含 viewport 1920×1080、networkidle 等待、剧本标签拉取重试）。

#### 第 5 步：清理

```bash
curl -sX POST 'http://127.0.0.1:8001/api/v1/source/detection/stop?channel=0'
curl -sX POST 'http://127.0.0.1:8001/api/v1/test/synthetic/stop?channel=0'
# 后端进程不用退；下次直接换剧本走第 3 步
```

### G.3 剧本格式（给 AI 写新剧本时用）

```jsonc
{
  "name": "<剧本名>",
  "description": "<一句话用途>",
  "fps": 60,
  "expected": { "ok_cycle_increment": 1, "ng_cycle_increment": 0 },  // 可选，给断言/文档参考
  "timeline": [
    {
      "from": 0,        // 起始帧号（含）
      "to": 30,         // 结束帧号（含）
      "detections": [   // 该帧区间持续返回这些检测框（推理线程每跑一次都查表）
        {
          "label": "step_a",      // 与项目 steps_config 的 label 对齐
          "confidence": 0.95,     // 0~1
          "bbox": [0.1, 0.1, 0.2, 0.2]   // 归一化 xywh，左上角 + 宽高
        }
      ]
    }
  ]
}
```

要 cycle 真正结束并写库 → 剧本要包含「步骤出现 → 步骤消失」完整一组（看 `ok_sequential_cycle.json`）。仅"持续出现一个标签"不会触发 cycle 结束（看 `smoke_static_label.json`）。

### G.4 改了什么 → 推荐跑哪个剧本（半自动）

> **快速做法**：直接跑 `python scripts/recommend_scenarios.py [origin/main]`，会按下表给出推荐清单。

skill 触发后**先看 git diff 头部**，按下表给用户列推荐清单（让用户点确认再跑，避免无关改动浪费时间）：

| `git diff --name-only` 命中 | 建议剧本 | 理由 |
|---|---|---|
| `backend/api/source_step_stats_mixin.py` | `ok_sequential_cycle` + `ng_missing_step` | 步骤判定核心，OK/NG 两条都覆盖 |
| `backend/api/source_settlement_mixin.py` 或 `source_session_lifecycle_mixin.py` | `ok_sequential_cycle` | 周期结算与写库 |
| `backend/api/source_event_trigger_mixin.py` 或 `source_events_check_mixin.py` | `alarm_event` | 事件链路 |
| `backend/services/mes_hooks.py` 或 `backend/api/sessions*.py` | `ok_sequential_cycle` | cycle 完成 → MES Hook 与导出 |
| `backend/services/export_*.py` 或 `backend/api/export_*.py` | `ok_sequential_cycle`（多跑几轮） | 实时导出依赖完整 cycle |
| `frontend/src/views/Monitor/index.vue` | `smoke_static_label` + `ok_sequential_cycle` | 视觉与计数器分别覆盖 |
| `frontend/src/views/Data/index.vue` | `ok_sequential_cycle`（再去 Data 页验证 session） | 落库后展示 |

新增剧本时同步在本表加一行（保持触发表是"实时索引"）。

### G.5 踩坑表

| 现象 | 根因 | 处理 |
|---|---|---|
| 后端启动了但 `curl /api/v1/test/synthetic/start` 返回 404 | 漏设 `RUNTIME_MODE=test`；测试路由没挂 | `export RUNTIME_MODE=test` 后重启 backend |
| `detection/start` 报"未加载模型" | DetectionStartRequest 的 `model_path` 历史是必填，前端代码可能没改 | curl 时省略 `model_path`；前端走时确认 source_type 已是 synthetic |
| `detection/results` 里 `detections` 一直为空 | (a) synthetic 没启动；(b) 当前帧号不在任何 timeline 区间 | 看 `/api/v1/test/synthetic/state` 的 `frame_seq`，对照剧本 `from/to` |
| cycle 计数器不涨 | 剧本只让步骤"出现"未让其"消失"，pipeline 不结算 | 给每个 step 后留一段空白（看 `ok_sequential_cycle.json`） |
| Playwright 截图全黑 / DOM 空 | 没等 `networkidle`；viewport 为 0 | 用 `tests/playwright_demo/synthetic_demo.py` 的模板（已处理） |
| 前端 Monitor 不显示检测框 | synthetic 走非项目配置路径，`current_detections` 有但无 `display_id`；与 tracking 模式不兼容 | 对纯展示验证够用；如要 tracking 校验，需在剧本对应项目里设 `logic_mode=detection` |
| pytest 测试结束打印 `[MES] Hook 管理器已停止` | conftest 默认设 `RUNTIME_MODE=test`，MES 子系统也启动 | 可接受副作用；CI 慢时可改成 fixture 级别按需打开 |
| 插件双工位 UI 有视频、步骤统计在动，但画面上没有检测框 | **Tier 2 `monitor.layout.body` 接管后**，原生 Monitor 的 canvas overlay 不在 DOM；插件 VideoCell 只用 `<img>` 吃 MJPEG（后端流不含 baked-in 框），且未调用主程序 `renderDetectionOverlay` | 插件 v1.1.4+ 在 `<img>` 上叠 canvas + 调 `layoutBodyActions.renderDetectionOverlay` / `setFrameNaturalSize`；**UAT 必测**：装插件 + 开检测 → 两工位 canvas 非空或 DOM 有 `.fjjl-det-overlay` 且框可见 |
| 项目页选了「最后一步结算」但末步仍能开严格顺序/单次接受 | **API/脚本建项目**只传 `steps_config`、不传 `pipeline_config.sequence_order`；检测能跑（引擎读 steps_config）但项目页 UI 识别不出结算步；金龙 UAT 2026-05-31 踩坑 | ① 创建/更新项目 API 自动补全 `sequence_order`（`project_config_normalize.py`）② 项目页加载兜底 ③ **UAT 必测**：`POST /projects` 不带 sequence_order → 项目页选 last_step → 末步两个 switch 必须 disabled；回归 `tests/uat/uat_20260531_settlement_step_switches.py` + `tests/test_project_sequence_order_normalize.py` |

### G.5b 进阶：Export / Cluster / Tracking 三块功能契约怎么验

每条都验「**真出文件 / 真双机通信 / 真位置过滤**」，**不**是只看 API 200 或对话框打开。

#### G.5b-1 自定义导出真出 docx/xlsx 验内容

```python
# 1) 先用 G.2 跑一遍 OK cycle，让 DB 里有 session/cycle/StepRecord 行
# 2) POST /api/v1/export/render 拿 bytes，自己解 zip 验字段
body = {"cycle_id": cid,
        "template_content": "Cycle: {{ cycle.id }}\n"
                            "Steps: {% for s in steps %}{{ s.label }}({{ s.duration }}s) {% endfor %}",
        "filename_template": "uat_{{ cycle.id }}.docx",
        "fmt": "docx"}
r = requests.post(f"{API}/api/v1/export/render", json=body, timeout=20)
# 3) docx 本质是 zip — 解 word/document.xml 抽 <w:t> 文本
import zipfile, io, xml.etree.ElementTree as ET
with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
    xml = zf.read("word/document.xml").decode()
ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
text = "\n".join(t.text or "" for t in ET.fromstring(xml).iter(f"{ns}t"))
assert f"Cycle: {cid}" in text and "step_a" in text
```

**关键陷阱**：

- 模板里 `cycle.steps` **不存在**！steps 是在**顶层** ctx，写 `{{ steps | length }}` / `{% for s in steps %}` 才能拿到 `_fill_cycle_steps_defects` 填进来的 StepRecord 列表
- xlsx 路线 A 用 inline string（`<c t="inlineStr"><is><t>...</t></is></c>`）**不**用 sharedStrings.xml，所以解析就读 `xl/worksheets/sheet1.xml` 即可
- docx/xlsx 路线 A = python-docx 自动样式；路线 B = `template_file_path` 必填走 docxtpl，需先 POST `/templates/{id}/upload-template-file` 上传占位符文件

#### G.5b-2 Cluster 主从双后端 + 聚合判定

ZeroMQ 是 v3.x 早期的设计，**v3.5.x 主从通信已经全面用 HTTP REST**（`POST /cluster/report` + `POST /cluster/heartbeat`）；ZeroMQ 仅遗留在某些 collector 内部解耦。所以双开后端验主从就是两个 uvicorn + 互相 HTTP 调用。

```bash
# 起主机（默认 DATA_DIR）
RUNTIME_MODE=test uvicorn backend.main:app --host 127.0.0.1 --port 8011

# 起副机（必须独立 DATA_DIR，不然两边写同一个 sql_app.db 抢锁）
mkdir -p /tmp/uat_slave_data
RUNTIME_MODE=test TIANJUN_DATA_DIR=/tmp/uat_slave_data \
  uvicorn backend.main:app --host 127.0.0.1 --port 8021
```

```python
# 1) 配主从角色
PUT  /api/v1/cluster/config @ 8011  {role:"master", expected_stations:["st_main","st_slave"]}
PUT  /api/v1/cluster/config @ 8021  {role:"slave", master_url:"http://127.0.0.1:8011", station_id:"st_slave"}

# 2) 心跳：副机上报后主机 GET /cluster/slaves 应能列出 st_slave
POST /api/v1/cluster/heartbeat @ 8011  {station_id:"st_slave", port:8021, ...}

# 3) 关键：主+副都给"同一 box_serial"上报 → 主机自动聚合 box_complete
#    生产里副机的 cluster_collector 完成 cycle 后会调主机 /cluster/report；
#    自动化测试里直接对主机连发两次（station_id 不同）等价。
POST /api/v1/cluster/report @ 8011  {station_id:"st_main",  box_serial:"BOX1", is_good:True}
POST /api/v1/cluster/report @ 8011  {station_id:"st_slave", box_serial:"BOX1", is_good:True}
# 4) 验：GET /cluster/boxes/BOX1 → summary.overall_result == "OK"
```

**关键陷阱**：

- 副机 `/cluster/report` 路由有 `if config["role"] not in ("master","standalone"): raise 400`。**直接对副机发 report 会 400**——生产里副机本身不接收报告，它只 push 到 master
- **任一工位 NG → 整盒 NG**（`StationReport.is_good=False` 会让 summary.overall_result="NG"）
- 第二个后端必须设 `TIANJUN_DATA_DIR=/tmp/uat_slave_data`，否则两个 uvicorn 抢同一个 SQLite，主机 cluster_collector 的 INSERT 会 `database is locked`（备注：项目已开 WAL + busy_timeout=15s 兜底，但仍建议物理隔离）
- 双后端跑久了 Cursor 终端可能给后端发 SIGTERM 误杀（看终端日志「收到信号 15」），重跑前先 `curl /api/v1/system/version` 各端口 sanity 一下

#### G.5b-3 Tracking-style 动态 ROI（移动目标穿过 ROI）

> **synthetic 自身的限制**：`source_inference_loop_mixin.py` 给 synthetic 帧硬编码 `is_tracking=False`，所以**注入的 detections 不会真正经过 ByteTracker 拿 track_id**。但 ROI 闸门照常工作（走 `step_stats_mixin._update_step_stats` 的 `is_normalized_bbox_center_in_polygon`）。我们用「单标签 + bbox 中心位置随帧变化」**等价模拟** track_id 在 ROI 内外移动的场景。

```python
def moving_target_scenario():
    # 60 fps 时间线：
    #   0-29   bbox 中心 (0.10, 0.50)  ROI 外（左）
    #   30-89  bbox 中心 (0.50, 0.50)  ROI 内（中央，连续 60 帧）
    #   90-119 bbox 中心 (0.90, 0.50)  ROI 外（右）
    #   120+   detections=[] → step disappear → 触发结算
    timeline = []
    def seg(a, b, cx, cy):
        bw = bh = 0.06
        timeline.append({"from": a, "to": b,
            "detections": [{"label":"step_a", "confidence":0.95,
                            "bbox":[cx-bw/2, cy-bh/2, bw, bh]}]})
    for i in range(0, 30, 5): seg(i, i+4, 0.10, 0.50)
    for i in range(30, 90, 5): seg(i, i+4, 0.50, 0.50)
    for i in range(90, 120, 5): seg(i, i+4, 0.90, 0.50)
    timeline.append({"from": 120, "to": 240, "detections": []})
    return {"name":"moving_target", "fps":60,
            "step_rois":{"step_a":[[0.40,0.40],[0.60,0.40],[0.60,0.60],[0.40,0.60]]},
            "timeline": timeline}

# 期望 step_counts['step_a'] == 1（中段连续帧形成 1 个 step）
# 反例：把 step_rois['step_a'] 改成 [[0.01,0.01],[0.05,0.01],...] 角落 → step_counts['step_a'] == 0
```

**关键陷阱**：

- step_roi polygon **必须归一化**（[0,1]）且 ≥3 点；polygon 校验在 `apply_project_config:127` 一行里就丢，配错就静默不限制
- synthetic 走 `with_project=True` 才会把 `step_rois` 自动注入到 `step_roi_polygons`；走 `with_project=False` 则要先 push 一个含 `steps_config[*].roi` 的 project payload
- `min_frames` 太大会把"在 ROI 内"的帧吃成 0；调试 step_count=0 时先把 `min_frames=1`
- 真要测 ByteTracker + tracking_roi（**全局** roi，存在 `pipeline_config.tracking_roi`，是另一套），synthetic 不够用，需要走真视频 / 真模型路径（路径 F）

### G.6 与路径 E / F 的边界

| 维度 | 路径 E | 路径 F | **路径 G** |
|---|---|---|---|
| 是否需要硬件 | 否 | **是**（真摄像头+真模型） | 否 |
| 后端业务流是否真跑 | 否（mock API） | 是 | **是** |
| 前端 UI 反馈是否真发生 | 是 | 是 | **是** |
| 适合场景 | UI 改动 | 现场验收/客户问题复现 | **CI / 改后端业务逻辑后的功能回归** |

### G.7 完成判定

- 剧本对应的 `expected.*` 计数器在 `/detection/results` / Monitor 上**与预期相符**
- Playwright 截图在 `/tmp/synthetic_demo_shots/` 内，截图里能看见**剧本对应步骤的视觉反馈**
- `[MES] Hook 管理器已停止` 这类副作用日志正常出现 → 说明完整 pipeline 走完
- 在结束前**调用 stop 两个 API**，否则下次启动新剧本会与旧 source_type 冲突

### G.8 配套测试基建文件索引

新加一条 BDD 场景或 E2E 场景时，按以下索引找到对应文件：

| 用途 | 文件 |
|---|---|
| 剧本仓库 | `tests/scenarios/*.json` |
| BDD 场景：核心检测流 | `tests/features/core_detection_flow.feature` + `tests/step_defs/test_core_detection_flow.py` |
| BDD 场景：视频源连接 | `tests/features/source_connection.feature` + `tests/step_defs/test_source_connection.py` |
| BDD 场景：MES 扫码工作流 | `tests/features/mes_scan_workflow.feature` + `tests/step_defs/test_mes_scan_workflow.py` |
| BDD 场景：报警链路 | `tests/features/alarm_event_chain.feature` + `tests/step_defs/test_alarm_event_chain.py` |
| BDD 场景：换产 | `tests/features/product_changeover.feature` + `tests/step_defs/test_product_changeover.py` |
| BDD 共用 helper | `tests/step_defs/_synthetic_helpers.py` |
| E2E Page Object 基类 | `tests/e2e_browser/pages/base_page.py` |
| E2E 页面对象 | `tests/e2e_browser/pages/{monitor,project,source,data,settings}_page.py` |
| E2E 测试 | `tests/e2e_browser/test_{source,settings,alarm,sat_full_workflow}_page.py` |
| pytest 端到端 | `tests/test_synthetic_full_flow.py`（不需要前端） |
| Playwright 演示模板 | `tests/playwright_demo/synthetic_demo.py` |
| SAT 现场验收（自动） | `tests/sat/test_sat_api.py`（设 `RUN_SAT=1` 才跑） |
| SAT 人工清单 | `tests/sat/checklist.md` |
| 自动推荐剧本 | `scripts/recommend_scenarios.py` |

### G.9 with_project 模式说明

`/api/v1/test/synthetic/start` body 加 `"with_project": true` 后，后端会：

1. 从剧本 `timeline[].detections[].label` 收集所有 label
2. 自动构造 `steps_config`（每个 label 对应一个步骤，threshold=0.3, min_frames=1）
3. 调 `mgr.set_project_config(...)` 应用到当前通道

这样**不需要前端配项目**，cycle 也会真结算。如果你想强制 logic_mode：

```jsonc
{
  "scenario": "ok_sequential_cycle.json",
  "with_project": true,
  "project_steps": ["step_a", "step_b", "step_c"],   // 可选，默认从剧本提取
  "logic_mode": "sequential"                          // sequential / detection / tracking
}
```

---

## 路径 H：可见浏览器 UAT（面向功能测试的金标准）

> **核心信条**：headless 跑出来的"全过"≠真的过。
> 客户问"真的测过吗"，唯一能拍胸口说"测过"的，是开了**可见浏览器**(`headless=False`)+**录像**+**API 后端契约校验**全跑一遍且**人眼复核**过的 UAT。

### H.0 什么时候必须走 H 而不是 E

| 用户说的话 | 该走 | 原因 |
|---|---|---|
| 「跑下 E2E 测试」「测一下 UI」 | E | 自动化即可 |
| 「写个 E2E 覆盖这个新页面」 | E | CI 用 |
| 「我看 ROI 真的生效了吗」「你怎么知道功能对的」「真测过吗」「面向功能测试」 | **H** | 必须人眼+API 双证据 |
| 「上线前我心里没底，跑一遍我看着」「客户要演示前先验一遍」 | **H** | UAT 场景 |
| 「截个图看看」 | E（screenshot 即可） | 单次定格 |
| 「录个 8-10 分钟的视频回放看」 | **H** | 必须 video on |

### H.1 H 的本质：三条 UAT 证据链

任何一条 UAT 报告都要包含**三类证据**，缺一条就不算"面向功能测试"：

| 证据 | 来源 | 作用 | 工具 |
|---|---|---|---|
| **API 后端契约证据** | 直接 POST `/api/v1/...` + 校验返回值 | 证明 backend 业务逻辑正确（ROI 闸门、sequential 排序、events→counters） | `requests` |
| **UI 前端反馈证据** | 可见 Chromium 操作 + 视频录像 + 截图 | 证明 frontend 真把 backend 数据渲染对了 | `playwright sync_api`，`headless=False`，`video_on` |
| **数据落库证据** | GET `/api/v1/data/sessions/.../cycles` 后比对 DB 行 | 证明跨边界一致（API 说有的，DB 真存了；前端显示的，DB 真有） | `requests` + 解析 |

**三个证据齐了才算"全过"**。少 API 证据 → 你只看到了 toast 弹出，没看到 DB 真有这条 cycle；少 UI 证据 → 你证明了 backend 对，但客户看到的可能是空白；少 DB 证据 → counter 涨了，但下次重启就丢了。

### H.2 一份 UAT 脚本的固定 5 段结构

> **v3.31 起：样板代码一律用共用库 `tests/uat/_common.py`，不要再复制粘贴。**
> 提供 `UatRun`（step 记录 + 安全截图 + 三件套证据目录 + run.json 汇总 + 退出码）、
> `launch_browser`（headless=False 金标准参数 + 视频录制 + 控制台 error 收集）、
> `filter_console_errors`（剔除视频流噪声）、`login`（v3.10.0 账号鉴权登录页）。
> 存量 90+ 个 `uat_*.py` 不回改；新脚本示例见 `_common.py` 文件头 docstring。

```python
"""UAT 脚本骨架（新脚本用 _common；下面展开等价逻辑便于理解）"""
import requests, time, uuid
from playwright.sync_api import sync_playwright
from _common import UatRun, launch_browser, filter_console_errors  # tests/uat/ 下运行

API = "http://127.0.0.1:8011"   # 用非默认端口避免和客户机的 8001 撞
run = UatRun("my_feature")       # 证据统一落 tests/uat/evidence_<日期>_my_feature/
step = run.step                  # 老脚本的 step()/safe_shot() 均由 _common 提供

# ──────── 第 1 段：环境准备 ────────
def setup():
    """检查 8001/6001 占用 → 不冲突就用，冲突就退到 8011/6011；
    清理上轮 UAT 产物；准备 shots/video 目录。"""

# ──────── 第 2 段：Phase A — API 契约证据（不开浏览器，只验后端） ────────
def phase_a_api_contracts():
    """逐个跑后端业务规则的 ROI / sequential / counter 链路验证：
       A1: ROI inside  → step_counts 三步全 +1
       A2: ROI outside → step_b 因不在 ROI 内 = 0
       A3: 反向 c→b→a → 触发 NG 事件 → 不良总数=1
       A4: 正向 a→b→c → 触发 OK 事件 → 合格总数=1, 总产量=1"""

# ──────── 第 3 段：Phase B — 可见浏览器人眼复核（headless=False + video） ────────
def phase_b_visible_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 1000},
        )
        page = ctx.new_page()
        # 页面在加载时 await page.wait_for_load_state("networkidle") + 给前端 polling 留 2s
        # 每个页面 page.screenshot(path=f"{SHOTS}/B1_monitor.png", full_page=True)
        # 关键交互结束后 time.sleep(1.5) 让用户眼睛跟得上
        ctx.close()
        browser.close()

# ──────── 第 4 段：Phase C — UI CRUD 真操作（创建-激活-修改-删除）────────
def phase_c_ui_crud():
    """以"新建项目"为例：
       C1: 点"新建项目"按钮 → 输入名称 → 提交
       C2: 验列表里出现 + 点中弹详情面板
       C3: 改某字段 → 保存 → 重新进来字段确实变了
       C4: 删除（含确认对话框） → 列表里消失"""

# ──────── 第 5 段：清理 + 总结 ────────
def cleanup_and_report():
    """清掉以 __uat_ 前缀创建的所有 project / template / 其他副产物。
    汇总打印 OK/!! 表，写入 /tmp/uat_run.log"""
```

### H.3 启动可见浏览器的标准启法

```python
browser = p.chromium.launch(
    headless=False,               # ★ 必须 False，让人眼能看
    slow_mo=250,                  # 每个动作放慢 250ms，否则跟不上
    args=["--disable-blink-features=AutomationControlled"],   # 防部分前端判定为爬虫
)
ctx = browser.new_context(
    viewport={"width": 1600, "height": 1000},   # 必须够大，否则项目 sidebar 折起来
    record_video_dir="/tmp/uat_video",          # ★ 视频证据
    record_video_size={"width": 1600, "height": 1000},
    ignore_https_errors=True,
)
ctx.tracing.start(screenshots=True, snapshots=True, sources=True)   # 可选：trace
```

跑完后：

```python
ctx.close(); browser.close()      # ← close ctx 才会把视频 flush 到磁盘
# /tmp/uat_video/<uuid>.webm  ← 这是给客户/产品的"我真测过"凭证
```

### H.4 项目特有的 7 个可见浏览器踩坑（必看）

| 坑 | 症状 | 根因 | 处理 |
|---|---|---|---|
| **点项目卡片不进详情** | `page.get_by_text("项目名").click()` 后右侧详情面板没出 | Vue `@click` 绑在外层 `div.cursor-pointer`，点 `<span>` 文字不冒泡到 handler | 用 `page.locator("div.cursor-pointer", has=page.get_by_text(NAME, exact=True)).first.click()` |
| **events 标签页断言失败** | UI 上明明看到"合格(OK)"几个字，`inner_text()` 抓不到 | 这些值在 `<input value="合格(OK)">` 里，`inner_text` 只读 textContent | `page.locator("input").evaluate_all("els => els.map(e => e.value)")` 直接读 value 属性 |
| **"项目页是空的"** | Project / Data / Alarm 页一片空白 | 这些页有"无激活项目占位"的条件渲染，没激活项目时所有 tab/按钮都不渲染 | UAT 第一步用 API 创建并激活一个 `__uat_` 前缀项目，phase_b 再开浏览器 |
| **删除按钮按了没反应** | `page.click("删除")` 后项目还在 | 删除走 ElementPlus `MessageBox` 二次确认（不是 `<confirm>` 标签） | 等 `page.get_by_role("dialog")` 出来后点里面的"确定" |
| **新建项目对话框找不到输入框** | `get_by_label("项目名称")` 报 strict mode violation | ElementPlus `<el-form-item>` 把 label 包了一层非标 `for` 关联 | 用 `page.locator("el-dialog input").first` 或 `placeholder` 文案定位 |
| **Monitor 上 step_counts 不动** | API 已经返回 step_counts={a:1,b:1,c:1}，前端一直显示 0 | Monitor 走 polling，默认间隔 1s，前端 `polling_interval` 也可能被休眠 | `time.sleep(2.5)` 给前端两个 poll 周期；或主动触发 `page.evaluate("window.__triggerPoll && __triggerPoll()")` |
| **视频文件 0 字节 / 无法播放** | 跑完产物里的 .webm 打不开 | 没 `ctx.close()` 直接 `browser.close()`，视频没 flush | 先 `ctx.close()` 再 `browser.close()` |

### H.5 必跑的 6 大可见浏览器验收页面（项目特化）

依顺序跑，前后有依赖：

```python
def visible_walkthrough(page):
    # 1) Monitor — 确认健康指示器、画面区域、stats 区都渲染
    page.goto(f"{FRONTEND}/monitor")
    page.wait_for_load_state("networkidle"); time.sleep(1.5)
    page.screenshot(path=f"{SHOTS}/B1_monitor.png", full_page=True)
    body = page.evaluate("document.body.innerText")[:5000]
    step("B1 Monitor 渲染", "项目" in body or "FPS" in body)

    # 2) Project — 创建-激活-编辑-保存-删除全套 CRUD
    page.goto(f"{FRONTEND}/project")
    # ... 见 H.6

    # 3) Source — 视频源类型 6 种是否全列出来
    # 4) Settings — 至少 PT/CT 模式切换 + 显示设置存在
    # 5) Data — Session 列表能进 + cycle 详情能开
    # 6) Alarm — 串口/协议/触发条件三个 tab 都能切
```

### H.6 UI True CRUD 标准手法（以项目页为例）

```python
def ui_create_select_delete_project(page):
    # B1 创建
    page.get_by_role("button", name="新建项目").click()
    page.locator("el-dialog input").first.fill(f"__uat_ui_{uuid.uuid4().hex[:6]}")
    page.locator("el-dialog button:has-text('确定')").first.click()
    page.wait_for_load_state("networkidle"); time.sleep(1.5)

    # B2 列表里能看到 + 点中
    name = page.locator("text=__uat_ui_").first.inner_text()
    page.locator("div.cursor-pointer", has=page.get_by_text(name, exact=True)).first.click()
    time.sleep(1.0)
    step("B2 选中后详情面板出现", page.locator("text=基本信息").is_visible())

    # B3 删除（注意 ElementPlus 二次确认）
    page.get_by_role("button", name="删除").click()
    page.get_by_role("dialog").get_by_role("button", name="确定").click()
    time.sleep(1.0)
    step("B3 删除后列表里消失", not page.get_by_text(name, exact=True).is_visible())
```

### H.7 UAT 报告产物清单

跑完一次 UAT 必须产出（路径都建议 /tmp，不要污染仓库）：

| 产物 | 路径 | 用途 |
|---|---|---|
| 视频回放 | `/tmp/uat_video/*.webm` | 给产品/客户看「我真做过」 |
| 全页截图 | `/tmp/uat_shots/*.png` | 关键节点定格证据 |
| run.log（JSON） | `/tmp/uat_run.log` | 自动化判定 OK/FAIL 列表 |
| 导出文件证物 | `/tmp/uat_v3_out/*.{txt,docx,xlsx}` | 真出文件，可双击打开 |
| Trace（可选） | `/tmp/uat_trace.zip` | 排查时用 `playwright show-trace` |

### H.8 端口 / 服务管理铁律（避免误杀客户机）

跑 UAT 之前**必须**先确认：

```bash
# 1) 默认端口（客户工作端）状态
for p in 8001 6001; do
  ss -tln 2>/dev/null | grep -q ":$p " && echo "$p BUSY (有人在用，你不能动)" || echo "$p free (可用)"
done

# 2) 占用 → 切到非默认端口
if [客户机正在用 8001]; then
  BACKEND_PORT=8011; FRONT_PORT=6011
  RUNTIME_MODE=test uvicorn backend.main:app --host 127.0.0.1 --port 8011 &
  cd frontend && DEV_SERVER_PORT=6011 \
    DEV_API_ORIGIN=http://127.0.0.1:8011 \
    DEV_WS_ORIGIN=ws://127.0.0.1:8011 \
    npm run dev &
fi

# 3) 跑完一定按 PID 精准 kill，不要 pkill -f node 一把梭
PIDS=$(ss -tlnp 2>/dev/null | awk '/:8011 |:6011 /{print $NF}' | grep -oP 'pid=\K[0-9]+' | sort -u)
for pid in $PIDS; do kill -9 "$pid" 2>/dev/null; done
```

**铁律**：
1. 永远先 `ss -tln | grep ":8001"` 确认端口归属，**别盲启** 8001
2. UAT 后**逐 PID kill**，不要 `pkill -f` 误伤客户其他 node 进程
3. 中途 Cursor 终端可能给 backend 发 SIGTERM 误杀（看 terminal log "收到信号 15"），跑前先 `curl /api/v1/system/version` 一下

### H.9 Path E vs Path H 决策表（再次明确）

| 问题 | Path E（headless 自动化） | **Path H（可见浏览器 UAT）** |
|---|---|---|
| 适用 | CI / 回归 / Smoke | **客户验收 / 上线前心里没底 / 复现客户问题** |
| headless | True | **False，必须看得见** |
| video | 不录 | **必须录，是证据** |
| screenshot | 失败时偶尔 | **每个关键步定格** |
| API 后端契约 | 偶尔（test 文件就近 mock） | **必须，phase_a 全跑一遍** |
| 跑完时长 | 30s-3min | **8-15min，让人眼跟得上** |
| 触发关键词 | "跑测试" "回归" | **"真的测过吗" "面向功能" "我看一遍" "UAT"** |
| 报告交付 | pytest stdout | **视频 + 截图 + run.log 三件套** |

### H.10 完成判定（用户问"测过没"时回答的标准）

只有以下 6 条全过，才能说"已经做过 UAT"：

- [ ] Phase A（API 契约）N 项全 OK，stdout 里有具体的 `step_counts={'step_a':1,...}` 和 `counters={'合格总数':1,...}`
- [ ] Phase B（可见浏览器）至少跑过 6 大页面，每页有截图
- [ ] Phase C（UI CRUD）至少创建-选中-删除一个 `__uat_` 前缀实体并且最终列表里消失
- [ ] 视频 .webm 已 flush 到 `/tmp/uat_video/`，能播放
- [ ] cleanup 干净：`__uat_` 前缀的 project/template 都删了；`ss -tln` 看 UAT 用的端口都释放
- [ ] run.log 写入 `/tmp/`，里面 `failed: 0`

少任何一条 → 回答"我跑了 N/M，剩 M-N 项还没过"，**不要**说"全过了"。

### H.11 执行参考（实战代码）

实战代码已落仓库（直接 cp 改，**不是** pytest 收集的测试**，是单文件 UAT 脚本**）：

| 文件 | 用途 |
|---|---|
| `tests/uat/uat_v2_functional.py` | API 契约 4 项（ROI in/out + 反向序列 + 计数器联动）+ UI CRUD 3 项的标准模板（V2，7/7 全过） |
| `tests/uat/uat_v3_advanced.py` | 进阶：Export docx/xlsx 解 zip 验字段、Cluster 主从双后端聚合、Tracking 动态 ROI 移动目标（V3，13/13 全过） |
| `tests/uat/uat_20260522_simultaneous_groups_v38.py` | v3.9.0 跨周期组类二状态机 UAT（同时出现组重构验收） |
| `tests/uat/uat_20260523_last_first_settlement.py` | **v3.9.0 last_first 结算模式 UAT（4 阶段 31 项全过）** — 含"先红后绿"对照（Phase D 跑同剧本切换 first_step 模式验 R3=0），是结算模式新加 / 改的最佳模板。日志事实统计：直接 grep 后端日志统计 `[last_first R3]` 触发次数 + `周期结束: ... OK/NG` 行数，比 HTTP `counters` 字段更可靠（counters 需要 events_config 上挂 `updates_counter`）。 |

跑法：

```bash
# 1) 端口检查（永远先做）
for p in 8001 6001 8011 8021; do
  ss -tln 2>/dev/null | grep -q ":$p " && echo "$p BUSY" || echo "$p free"
done

# 2) 起后端 8011（V2/V3 都需要）
RUNTIME_MODE=test uvicorn backend.main:app --host 127.0.0.1 --port 8011 &

# 3) 起 slave 8021（仅 V3 的 Phase E 需要，必须独立 DATA_DIR）
mkdir -p /tmp/uat_slave_data
RUNTIME_MODE=test TIANJUN_DATA_DIR=/tmp/uat_slave_data \
  uvicorn backend.main:app --host 127.0.0.1 --port 8021 &

# 4) 起前端 6011（仅 V2 的 Phase B 浏览器走查需要）
cd frontend && DEV_SERVER_PORT=6011 \
  DEV_API_ORIGIN=http://127.0.0.1:8011 \
  DEV_WS_ORIGIN=ws://127.0.0.1:8011 npm run dev &

# 5) 跑 UAT
python tests/uat/uat_v3_advanced.py                              # 进阶 13 项
python tests/uat/uat_v2_functional.py                            # 基础 7 项
python tests/uat/uat_20260523_last_first_settlement.py           # v3.9.0 last_first (4 阶段 31 项)
python tests/uat/uat_20260522_simultaneous_groups_v38.py         # v3.9.0 跨周期组类二
```

> 💡 **Linux UAT 启后端的关键一点**（v3.9.0 实测）：Cursor `Shell` tool 的 background 命令可能被发 SIGTERM 误杀，**用 `setsid bash -c '...exec uvicorn...' < /dev/null > log 2>&1 & disown`** 完全脱离会话才不会被中断。日志要 flush 配合 `PYTHONUNBUFFERED=1`，否则后端的 `print` 会缓冲，UAT grep 不到关键字。

> 这两个脚本**不是** pytest 用例（命名故意不带 `test_` 前缀），所以 `pytest tests/` **不会**自动跑它们 — 必须显式 `python tests/uat/...` 调起来。这是有意的：UAT 应该是"我现在就要看一眼"的人工触发，不应混进 CI 无差别跑。

---

## 外部测试 Skills 参考

本项目安装了以下外部测试 skills，在写测试时可参考：

| Skill | 触发场景 | 提供 |
|---|---|---|
| `playwright-best-practices` | 写 Playwright 测试、修 flaky test、POM 架构 | 50+ 参考文档（POM/等待策略/CI/Vue框架/Electron） |
| `python-testing-patterns` | 写 pytest 测试、fixture/mock/TDD | pytest 全套模式（fixture/参数化/async/property-based） |
| `e2e-testing-patterns` | E2E 测试架构决策 | Playwright 通用模式（Page Object/Network Mock/Visual Regression） |
