# 质量地图：这个项目怎么被测试

> **类型**：explanation（讲清体系与分工；具体命令模板去看 `run-tests` skill）
> **本文不讲**：怎么写某类测试、pytest 命令细节（→ `run-tests` skill）；怎么起开发服务器（→ `start-dev-servers` skill）。
> **与代码冲突时**：以 `tests/` 目录现状为准。

给新人和新 agent 的一页纸：改动交付前要过什么关、每层测试防什么、为什么 UAT 不能替代 CI 回归。

## 一、四层测试框架各防什么

| 层 | 位置 | 规模（2026-07） | 防什么 | 进 CI 吗 |
|---|---|---|---|---|
| 单元/集成（pytest） | `tests/test_*.py` | 99 个文件 | 代码内部回归：状态机分支、结算守门、队列语义、迁移幂等 | ✅ |
| 行为（pytest-bdd） | `tests/features/*.feature`（29 个文件）+ `tests/step_defs/` | 29 对 | 跨模块业务流程：扫码→绑定→结算→推送、包装流、插件签名安装 | ✅ |
| 浏览器 E2E（pytest-playwright，headless） | `tests/e2e_browser/test_*.py` | 27 个文件 | 前端真渲染回归：页面元素、交互、UI→后端落库 | ✅ |
| 可见浏览器 UAT | `tests/uat/*.py`（109 个，**不带 test_ 前缀**） | 109 个 | **人眼验收证据**：headless=False + 录像 + 截图，按客户复现路径操作 | ❌ 故意不收集 |

支撑设施：

- **虚拟剧本源**（`tests/scenarios/*.json` + `backend/api/source_synthetic_mixin.py`）：无摄像头无模型跑真实检测管线，是 BDD/E2E 的输入来源；`RUNTIME_MODE=test` 才挂载（见 `backend/api/router_manifest.py` 尾段）。
- **测试隔离**：fixture 用独立临时 DB（`TIANJUN_DATA_DIR` 指到 /tmp）+ unique uuid，禁止 reload uvicorn（AGENTS.md 不变量 #5）。
- **文档防腐**（2026-07 起）：`tests/test_doc_ci.py` 跑六项——路径校验/死链/端点文档基线门禁/表参考一致性/OpenAPI 快照一致性/配置字典一致性，详见 [conventions/](conventions/) 三份军规。

## 二、T0-T8 流水线为什么这样排

完整表在 AGENTS.md 第三节。设计逻辑一句话：**UI bug 是写功能时埋下的，不是"测一下"时埋的**——历史事故是"后端做了、前端没加按钮，单测+build 全绿，客户才发现没入口"。所以：

- T0（现场叙事）在最前：写不出四句话叙事 = 理解没收敛，动代码就是赌。
- T4/T5/T6 由"碰 .vue"自动触发，不依赖用户说"测一下"：build 绿和单测绿都证明不了"人能看到界面"。
- T7（UAT）只在客户反馈/验收类强制：它是证据不是回归（见下）。

## 三、最容易踩的分工误区：UAT ≠ CI 回归

| | T6 CI E2E（`tests/e2e_browser/`） | T7 UAT（`tests/uat/`） |
|---|---|---|
| 目的 | 下次有人改代码时自动拦回归 | 给人看的验收证据（视频+截图+run.log） |
| 是否被 pytest 收集 | 是 | **否**（命名故意不带 test_ 前缀） |
| headless | 是 | 否（真开浏览器） |
| 可替代对方吗 | 否 | 否 |

带 UI 的改动两者**都要留**。只交 UAT = 下次回归不会自动跑；只交 E2E = 没有人眼验收证据。

## 四、bug 修复的"先红后绿"铁律

修 bug 必须先在未修复代码上让测试 FAIL（证明测试真盯住了 bug），再修复转 PASS，并在汇报里写明。没经过红→绿转换的"测试通过"视为假绿灯。每条客户反馈修完必须沉淀：剧本 JSON + UAT 脚本（证据）+ 一条 CI 测试（护栏），缺护栏 = 修了等于没修。

## 五、改动前的测试影响速查

| 你改了 | 至少要跑 |
|---|---|
| `source*.py` 状态机/结算 | `tests/test_integration_cycle_end_chain.py` + 相关模式单测 + BDD `core_detection_flow` |
| MES 域（hooks/scanner/gateway） | `tests/test_*scan*` `tests/test_mes_*` + BDD `mes_scan_workflow` / `chuannan_mes_integration` |
| ORM/迁移 | `tests/test_db_migrations_versioned.py` + 空库启动冒烟 |
| 任何 `.vue` | 对应 `tests/e2e_browser/test_*.py`（没有就补，T6 强制） |
| 路由/端点 | `tests/test_doc_ci.py`（端点文档门禁+OpenAPI 快照会拦你） |
| 插件系统 | BDD `plugin_install_api` / `plugin_signing` / `plugin_cli` |

完整命令模板与踩坑（conftest 隔离、MagicMock 串污染、e2e 清理前缀）见 `run-tests` skill。
