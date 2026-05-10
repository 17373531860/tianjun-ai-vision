---
name: run-tests
description: "测试相关任务的统一入口，覆盖：(1) 跑现有测试套件并分析失败 (2) 给新功能/bug 修复写测试 (3) 启动后端+前端做端到端冒烟 (4) 改动后的测试影响分析 (5) Playwright 自动化前端验证（截图/DOM检查/交互测试）(6) 虚拟 synthetic 剧本源（无摄像头/无模型跑真实 pipeline）。当用户说『测一下』『跑测试』『跑一下』『写测试』『补测试』『mock 报错/失败』『fixture 怎么写』『影响哪些测试』『冒烟』『端到端验证』『客户场景模拟』『测试 X 通不通』『截图看看』『页面对不对』『UI 验证』『虚拟检测』『剧本』『无模型测试』时触发。包括项目特有的 pytest / pytest-bdd / allpairspy / pytest-playwright 四层框架命令模板，Playwright 自动化前端验证工具包，以及 conftest 隔离 / MagicMock 串污染 / e2e 清理前缀等踩坑点。"
allowed-tools: "Read, Grep, Glob, Shell, Write, StrReplace, Agent, mcp__playwright, mcp__context7, mcp__sequential-thinking"
---

# run-tests: 测试任务统一入口

用户提到测试相关任务时，先**识别诉求**再走对应路径。

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

含糊时（仅说"测试一下"无上下文）→ 先反问："你是想 (a) 跑现有测试 (b) 给新代码写测试 (c) 启动后端+前端做端到端冒烟 (d) 看改动会破坏哪些老测试 (e) 用 Playwright 自动截图/验证前端页面 (f) 现场验收/客户场景全流程验证 (g) 虚拟剧本源（无模型跑 pipeline）？"

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

## 外部测试 Skills 参考

本项目安装了以下外部测试 skills，在写测试时可参考：

| Skill | 触发场景 | 提供 |
|---|---|---|
| `playwright-best-practices` | 写 Playwright 测试、修 flaky test、POM 架构 | 50+ 参考文档（POM/等待策略/CI/Vue框架/Electron） |
| `python-testing-patterns` | 写 pytest 测试、fixture/mock/TDD | pytest 全套模式（fixture/参数化/async/property-based） |
| `e2e-testing-patterns` | E2E 测试架构决策 | Playwright 通用模式（Page Object/Network Mock/Visual Regression） |
