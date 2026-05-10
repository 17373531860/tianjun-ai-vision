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

跑完测试要给用户一句话总结：**`<层级>: N passed / M failed / K skipped (耗时 Xs)`**，然后才能往下走。

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

适用：没有摄像头与模型，但要验证「后端真实 pipeline → 前端轮询结果」或回归 Monitor/API；关键词：**虚拟检测**、**剧本**、**synthetic**、**无模型测功能**。

### 原理（简述）

环境变量 `RUNTIME_MODE=test` 时挂载 `POST /api/v1/test/synthetic/*`。后端使用 `source_type=synthetic` 合成帧 + 按 JSON 剧本注入检测框，推理线程仍调用 `_update_step_stats`（与真实摄像头同源）。**无模型**时可 `POST /api/v1/source/detection/start` 且 body 省略 `model_path`。

`synthetic` 下 `_get_confirmed_detections` **直通**剧本框（跳过步骤帧计数门槛），便于在无项目配置时仍能断言 API/UI。

### 命令速查

```bash
# pytest 默认已 set RUNTIME_MODE=test（见 tests/conftest.py）
pytest tests/test_synthetic_detection_api.py -v

# 手动起后端（需显式 export RUNTIME_MODE=test）
export RUNTIME_MODE=test
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
```

### 剧本文件

目录：`tests/scenarios/*.json`。字段：`name`、`fps`、`timeline`（每项 `from`/`to` 帧号闭区间 + `detections` 列表，`bbox` 为归一化 xywh）。

示例：`tests/scenarios/smoke_static_label.json`。

### HTTP

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/test/synthetic/start` | body: `scenario` 文件名 或 `scenario_json` 内联字典 |
| POST | `/api/v1/test/synthetic/stop?channel=0` | 停止 synthetic 源 |
| GET | `/api/v1/test/synthetic/state?channel=0` | 调试：帧序号与剧本名 |

与路径 F（现场验收）关系：F 面向真机；G 面向 CI/开发机的**确定性功能链路**。

---

## 外部测试 Skills 参考

本项目安装了以下外部测试 skills，在写测试时可参考：

| Skill | 触发场景 | 提供 |
|---|---|---|
| `playwright-best-practices` | 写 Playwright 测试、修 flaky test、POM 架构 | 50+ 参考文档（POM/等待策略/CI/Vue框架/Electron） |
| `python-testing-patterns` | 写 pytest 测试、fixture/mock/TDD | pytest 全套模式（fixture/参数化/async/property-based） |
| `e2e-testing-patterns` | E2E 测试架构决策 | Playwright 通用模式（Page Object/Network Mock/Visual Regression） |
