# AGENTS.md — 天军 AI 视觉检测系统

> **给 AI 助手（Claude Code / Cursor / 其他 LLM agent）的项目说明书**
>
> 这份文档只做三件事：**地图（项目是什么 / 模块在哪）+ 守则（怎么干活）+ 不变量（不能违反什么）**。
> **细节一律不进本文件**——模块详解在对应 `.claude/skills/<name>/SKILL.md`，版本历史在 `docs/changelog/`，插件资料在 `docs/plugin-system/`，按需读取。
>
> 阅读顺序：第一节 → 第三节工作守则 → 第四节 skill 指针表 → 任务相关章节。

---

## 一、项目快速档案

| 项 | 值 |
|---|---|
| 项目名 | 天军 AI 视觉检测系统（TianJun AI Vision） |
| 性质 | **商业项目，客户已在用** — 工厂工控机部署 |
| 客户场景 | 装配线视觉检测 / 包装线 / MES 数据回传 / 多工位集群 |
| 部署模式 | Windows 工控机本地安装（Inno Setup 一键包，约 1.5 GB），Electron 桌面壳套 FastAPI 后端 + Vue3 前端 |
| 当前线上版本 | **v3.31.0**（2026-06-29）— 主程序原生「称重投料模式」(`logic_mode='weighing'`) 落地：电子秤配料防错全流程（选型号/扫码绑件→放件自动去皮→投料对比标准量→缺料/超量报警→逐件逐料记录持久化落库）+ 设备读数驱动的状态机引擎（与 sequential/detection/custom/tracking 并列，配置全进 `pipeline_config.weighing`）+ PL2303 USB 转串口驱动内置（Windows 免装）+ mock_weight 无硬件模拟 + bestar 示例插件退役（逻辑上提主线）。**逐版变更详见 `docs/changelog/`，本文件不再记版本流水账** |
| 主仓库 | `17373531860/tianjun-ai-vision`（**PRIVATE**） |
| 中转仓库 | `xu-yanzhi32/tianjun-releases` + `tianjun-releases-2`（Gitee 公开 release，给客户下载用） |
| 母语 | **中文**（用户和注释主语言；技术术语保留英文） |

---

## 二、技术栈

### 后端
- **Python 3.10**（Anaconda 环境 `tianjun`）
- **FastAPI**（`uvicorn` runtime）+ **SQLAlchemy ORM**
- **SQLite**（单文件 `sql_app.db`，开 WAL + busy_timeout=15s；**计划迁 PostgreSQL**）
- **OpenCV** + **PyTorch（CUDA）** + **Ultralytics YOLO**
- **Jinja2 SandboxedEnvironment**（仅用于自定义导出渲染；MES Gateway **不**用 Jinja2）
- 视频编解码：FFmpeg（GPL Win64）
- 第三方 SDK：海康 HCNetSDK（NVR）、海康 MvCamera（工业相机）、ISAPI

### 前端
- **Vue 3** + Composition API + **Pinia** + **Vue Router**
- **Element Plus** UI 库
- **Tailwind CSS** + **ECharts**
- **Vite** 构建（前端独立 npm 包；项目根**无** `package.json`，前端与 Electron 是两个独立包）

### 桌面壳
- **Electron** 32+ + 8 步关机流程 + Splash + License 验证 + 三层崩溃恢复

### 编译/打包
- **Nuitka** 把核心 `.py` 编成 `.pyd`（CI 用 `--module`，按白名单替换源码）
- **conda-pack** 打 Python 环境
- **Inno Setup**（Windows 安装包）
- **GitHub Actions** CI（`.github/workflows/build.yml`）→ GitHub Release → Gitee Release（客户下载）
- **Runtime 仍 spawn python**：`python -m uvicorn backend.main:app`，加载 `.pyd`（编译过的）+ `.py`（源码）混合

---

## 三、工作守则（必须遵守）

### 唯一硬性规则
- ✅ **一次只做一件事，做完汇报再接下一件。** 不要闷头连干 3-5 个改动让用户审一堆。

### 完成定义（Definition of Done · 每个功能任务收尾前强制自检）

> ⚠️ 这条不在测试 skill 里、而在工作守则里，是故意的：**UI bug 是写功能时埋下的，不是"测一下"时埋的**。
> 历史教训：自定义导出"数据路径"后端做了、前端没加按钮，单测+build 全绿，结果客户才发现没入口（详见血泪史）。
> 所以**任何功能任务**收尾前，必须**按下面这条有序流水线一步一步走完**，再向用户报"做完了"。打不齐就写「⚠️ 未完成 Tn，原因 X」，**禁止伪报"全过"**。

**这是一条有先后顺序的流水线，不是并列勾选项——从 T0 走到 T8，跳步=不合格，前一步没过不进下一步。** 完整版（含命令模板/踩坑）见 `run-tests` skill 第 -2 节，此处是常驻速查：

| 序 | 步骤 | 何时强制 |
|----|------|----------|
| T0 | 写现场叙事（操作员→前端→后端→UI 四句话） | 总是 |
| T1 | 影响分析（列改动文件；碰 `.vue` 在这步登记"含 UI"；主线过 modify-* skill） | 总是 |
| T2 | 写改动 + lint 0 错误 | 总是 |
| T3 | 后端 CI 回归（`tests/test_*.py` / BDD 跑绿） | 有后端改动 |
| T4 | 开真浏览器看 UI（`headless=False` + 截图，build/单测绿都不算） | ⚠️ 碰 `.vue` 强制 |
| T5 | UI→后端落库双向验证（GET/查 DB，不是只看 toast） | ⚠️ 碰 `.vue` 强制 |
| T6 | 留 CI E2E（`tests/e2e_browser/test_*.py` 跑绿） | ⚠️ 碰 `.vue` 强制 |
| T7 | 路径 H 可见浏览器 UAT + 三件套证据（视频+截图+run.log） | 客户反馈/验收类强制 |
| T8 | 收尾把本表逐行打勾贴回复 | 总是 |

> **触发判据**：T1 一旦登记"含 UI" → T4/T5/T6 自动转强制，不依赖用户说没说"测一下"。
> **高频纠偏**：T7 单文件 UAT(`tests/uat/`)是人眼证据、不进 CI；T6 才是 CI 回归——**两者缺一不可，别用 UAT 冒充回归**。

### 隐含约定
- **多 worktree 纪律**：本机长期并存 `tianjun-main` / `tianjun副本` / `tianjun-se9` 三棵树，同名文件各一份。用户说"主程序/main"就**必须**在 `tianjun-main` 操作——动手前 `pwd` + 核文件路径前缀，别因为副本在手边就顺手改副本（真实事故：被要求改 main 却改了副本，白干一轮）。端口归属以用户**本次**指令为准，不照默认表想当然。详见 `start-dev-servers` skill 第一节铁律 + 故障 I/J
- 商业项目客户在用，**主线改动要先做影响分析**（用 modify-* 系列 skill）
- **不要主动 commit / push**，等用户明确指令
- **破坏性操作必须先问**：`rm -rf`、`drop table`、`force push`、改 schema、删 commit 历史
- **改动后必须验证**：lint 0 错误 + 至少跑一次冒烟（启动后端 / 单元测试 / 手测）
- **中文回复**，技术术语保留英文（如 `monkey patch`、`hotfix`、`mixin`）
- **Commit 信息中文为主**，格式 `<type>(<scope>): <短描述>` + 可选正文写"为什么"
- 插件挂了不能让主程序起不来 → **错误隔离是底线**
- 老客户已装的版本升级要无损 → **数据迁移谨慎，老 schema 兼容**

### 产品决策原则（功能分层归位）

收到任何"加功能"诉求，**动手前必须先做这个判定**：

> **这是给所有客户的基础设施，还是给这一家的小众功能？**
>
> - 基础设施 → **进主程序**（且要给插件平台留出可扩展钩子）
> - 小众变种 → **进客户专属插件**（绝不污染主程序）

**判定细则**：

| 场景 | 归位 | 例子 |
|---|---|---|
| 多客户都会用、影响检测/MES/数据等核心通路 | 主程序原生 + 插件平台留 hook 给变种 | 工位组（Channel Group）、新视频源类型、新结算模式 |
| 客户级业务变种、参数定制、UI 个性化 | 客户专属插件 | 三段时间警告档、客户自定义 NG 抑制、客户特有界面布局 |
| 客户对接自家 IT 系统（MES / BI / ClickHouse / Webhook 等） | 客户专属插件 | 推自家 ClickHouse、企业微信通知、自定义导出格式 |
| 主程序底层架构改动（数据模型 / 状态机 / 配置 schema） | 主程序原生（必须） | 新表、新字段、状态机分支、跨工位联动 |
| 客户要求"主程序 UI 隐藏 / 调整某块" | 客户专属插件（Tier 1 主题或 Tier 2 视图覆盖） | 客户嫌某模块碍眼想隐藏 |

**反模式（坚决禁止）**：

- ❌ "客户 X 急要这功能，先在主程序改，反正其他客户用不到也不影响" — **永远说不**。这是污染主程序的开端
- ❌ "插件平台还没接入这能力，先绕过去在主程序临时加" — 应该**先升级插件平台能力面**，再做客户插件
- ❌ "这功能小众但通用，主程序加个开关默认关" — 默认关的开关也是污染。改插件
- ✅ "插件平台缺 X 能力 → 先给平台补 X（基础设施）→ 客户插件用 X 实现需求" — **正解**

**例外**：主程序底层架构本身确实需要演进（新概念、新表、新状态机分支）→ 走主程序原生，但**同时**给插件平台留对应 hook，让以后这个新架构的客户级变种也能走插件。

**这条原则的目的**：让主程序保持精简、稳定、面向所有客户；让插件平台承担"客户百花齐放"的成本。详细决策流程见 `.claude/skills/feature-placement/SKILL.md`。

---

## 四、必读 skill 触发表（核心导航）

**这是 AGENTS.md 最重要的一节**。看到对应场景，**先读对应 skill 再动手**。各模块的详细说明（文件清单、踩坑、扩展点）都在对应 skill 里，不在本文件。

| 场景 | 必读 skill |
|---|---|
| 改 `backend/api/source.py` 或任意 `source_*_mixin.py` / has-a 组件 | `modify-source` |
| 改 ORM 模型 / 加表 / 改字段（含全部数据库表清单与字段） | `modify-model` |
| 改后端 API 端点（路由、Schema、参数）| `modify-api` |
| 改前端 Vue 组件 / Pinia store | `modify-frontend` |
| 改项目配置结构（pipeline_config / steps_config / events_config）| `modify-project-config` |
| 新增后端 API 端点 | `add-api-endpoint` |
| 新增视频源类型 | `add-source-type` |
| 新增检测模式 | `add-detection-mode` |
| 新增事件类型（OK/NG/警告之外）| `add-event-type` |
| 排查检测异常（不出目标 / FPS 低 / 置信度问题）| `debug-detection` |
| 排查 source 状态机问题（Cycle/Step 生命周期、结算模式）| `debug-source` |
| 排查多通道异常（GPU、ChannelManager、串扰）| `debug-channel` |
| 排查视频采集 / 推流 / 录像问题 | `debug-video` |
| 排查报警不响应 | `debug-alarm` |
| 排查 MES 异常（工单 / 工件 / 缺陷 / 扫码器 / Hook / Gateway / 外设）| `debug-mes` |
| 排查集群主从（box 不齐 / 副机心跳 / box_complete 不推 MES）| `debug-cluster` |
| 排查 Session/Cycle/Step 数据问题 | `debug-session` |
| 排查 Electron 桌面壳问题 | `debug-electron` |
| 排查前端问题 | `debug-frontend` |
| 排查自定义导出 / 实时规则 / 模板 / 字段中央仓库（v3.5.0+）| `debug-export` |
| 排查用户系统 / License 授权（machineId / RSA 验签 / token 鉴权 / 权限 / API Key）| `debug-operator-license` |
| 排查 per_item 逐件覆盖模式（打螺丝场景 / 周期不开始 / 漏件不报 / 严格等量 / 手动结算 / box 尺寸过滤）| `debug-per-item` |
| 数据问题修复 | `fix-data` |
| 前后端 API 对齐检查 | `api-sync` |
| 调参（视频 + 模型）| `tune-params` |
| 训工业 hand-detector（客户手套/俯视等 MediaPipe 死区救场）| `train-hand-detector` |
| 跑测试 / 写测试 / 端到端冒烟 / 测试影响分析 | `run-tests` |
| 本地启动前后端 / 起开发服务器 / 端口被占 / 副本 worktree 环境隔离 / baseURL 调错后端 | `start-dev-servers` |
| 出新版本（version bump / changelog / tag / CI）| `update-release` |
| 打包流程问题排查 | `build-release` |
| 给客户出热补丁 | `create-hotfix` |
| 分支合并 / 多 agent 并行协作 / 解决合并冲突 | `merge-branch` |
| 接到新功能诉求 / 评估"这是主程序还是插件" / 立项前归位决策 | `feature-placement` |
| 开发客户插件 / 找扩展点 / 插件平台能力 | `docs/plugin-system/`（非 skill，整套设计+实现+清单在此） |

---

## 五、系统架构

```
┌────────────────────────────────────────────────────────────────┐
│  Electron 桌面壳 (electron/main.js)                              │
│  - spawn python uvicorn 后端 → 健康检查 → 加载前端 → Splash 撤场 │
│  - License 验证（RSA SHA256 + machineId） / 8 步关机 / 三层崩溃恢复│
└──────────────┬───────────────────────────────────┬─────────────┘
       ┌───────▼────────┐                  ┌──────▼──────────┐
       │ Vue3 前端       │  HTTP /api/v1   │ FastAPI 后端     │
       │ (Vite dev:5173) │ ◄─────────────► │ (端口 8001)      │
       │                │   MJPEG 流       │                 │
       └────────────────┘                  └──────┬──────────┘
                          ┌──────────────────────┼─────────────────────┐
                   ┌──────▼──────┐        ┌──────▼─────┐        ┌──────▼──────┐
                   │  SQLite     │        │ 视频源/推理  │        │ MES / 扫码 │
                   │ sql_app.db  │        │ Source/Det  │        │ Gateway   │
                   │ (WAL)       │        │ 多通道+集群  │        │ 5 种适配器 │
                   └─────────────┘        └─────────────┘        └─────┬───────┘
                                                              ┌────────▼─────────┐
                                                              │ 硬件 IO           │
                                                              │ 摄像头/GPU推理/   │
                                                              │ 报警串口/扫码器/  │
                                                              │ 称重等外部设备     │
                                                              └──────────────────┘
```

> Hub 集成关系（VideoSourceManager → MESHookManager → WorkOrder/Workpiece/Defect/Cluster/Gateway/Scanner）的详细图见 `debug-mes` skill；数据库 37+ 张表的逐表字段见 `modify-model` skill。

### 后端真实路由前缀（**全部** `/api/v1/` 下）

| 前缀 | 文件 | 一句话 |
|---|---|---|
| `/source/*` | `source_routes.py` ⚠️ | **视频源 + 检测核心** |
| `/data/*` | `sessions*.py`（4 个文件） | session/cycle/step 记录 + 导出 |
| `/projects/*` | `projects.py` | 项目 CRUD + 激活 |
| `/models/*` | `models.py` | 模型上传/转换/标签 |
| `/tasks/*` | `tasks.py` | 离线推理任务 |
| `/reports/*` | `reports.py` | 趋势/日报/导出 |
| `/cameras/*` | `cameras.py` | **旧式相机表**（与 `/source/*` 并存，**勿混淆为主路径**）|
| `/system/*` | `system_display.py` | KV 配置 + license 缓存 |
| `/alarm/*` | `alarm.py` ⚠️ | 灯塔/蜂鸣器/共享灯柱 |
| `/workstations/*` | `channel_manager.py` | 多工位 + GPU 分配 |
| `/scanner/*` | `scanner.py` | 扫码器 CRUD + scan_pair + 禁用 |
| `/scanner/wmax/*` | `wmax.py` ⚠️ | WMax 协议（35+ endpoint） |
| `/external-devices/*` | `external_device.py` | 称重器/串口外设 |
| `/weighing/*` | `weighing.py` | v3.31.0 称重投料模式（前置选择/扫码/去皮/逐件记录/虚拟喂重） |
| `/cluster/*` | `cluster.py` | 集群主从 + 副机心跳 |
| `/mes/*` | `mes.py` ⚠️ | 工单/工件/缺陷/缺陷码 |
| `/mes/gateway/*` | `mes_gateway.py` | MES 推送连接 + 测试 + 工单拉取 |
| `/auth/*` | `auth.py` | v3.10.0 登录/登出/启用-关闭鉴权/me/权限目录 |
| `/users/*` | `users.py` | v3.10.0 用户 CRUD + 角色绑定 |
| `/roles/*` | `roles.py` | v3.10.0 角色 CRUD + 权限编辑 |
| `/api-keys/*` | `api_keys.py` | v3.10.0 M2M API Key 管理（SHA256 + scope） |
| `/operators/*` | `operators.py` | ⚠️ **v3.10.0 已废弃** — 全部 410 Gone，重定向 `/api/v1/users` |
| `/export/*` | `export_custom.py` + `export_realtime.py` + `export_scheduled.py` | v3.5.0 自定义导出 + v3.8 定时导出（共用前缀） |
| `/channel-groups/*` | `channel_groups.py` | v3.13.1 单机内多工位**并行**联动 (RFC 10) |
| `/workpiece-flows/*` | `workpiece_flows.py` | v3.14.0 单机内多工位**串行**结算 (RFC 11) |
| `/packaging-flows/*` | `packaging_flows.py` | v3.21+ 包装箱结算（上银包装线） |
| `/mes/inbound/*` | `mes_inbound.py` | v3.26+ 外部生产管控系统入站 REST（开工/完工/报警） |
| `/plugins/*` | `plugins.py` | 插件安装/激活/清单/client-log |
| `/debug/*` | `debug.py` | 通道诊断 + 调试日志中心 |

> **常见误解**：路径前缀是 **`/api/v1/`** 不是 `/api/`；旧手册写的 `/api/detection/*` 已删，等价端点在 `/api/v1/source/detection/*`。
> **ORM 提醒**：类名是 `Model`（**不**是 `MLModel`）；表名是 `models`（**不**是 `ml_models`）。

---

## 六、模块详解

> 已下放。每个业务模块的"做什么 / 关键文件 / 踩坑 / 扩展点"都在**第四节触发表**对应的 skill 里。不要再在本文件维护模块详解——那是 skill 的职责，避免双份维护漂移。
>
> 速记主线：视频源采集(Source) → 检测推理(Detection) → 项目配置(Project 7 个 JSON 字段) → 周期/步骤/事件状态机(5 种 logic_mode × 4 种 settlement_mode) → 多通道(ChannelManager) → MES 子系统(工单/工件/缺陷/扫码器/Gateway/集群) → 数据记录与导出 → 报警/录像/系统设置 → Electron 桌面壳 → 用户系统+License。任一模块要动手，先查上表进对应 skill。

---

## 七、关键扩展点（给插件系统 / 加新功能看）

> **全量扩展点清单已下放**到 `docs/plugin-system/inventory/03_extension_points.md`（含每个扩展点的类型/用途/所在文件）。做插件或加功能前先读那里 + `feature-placement` skill，**复用现有配置化能力，不要重造**。

最高频的几个扩展点（速记，细节进上面的清单）：
- `Project` 的 7 个 JSON 配置字段（`pipeline_config` / `steps_config` / `events_config` / `alarm_config` / `data_config` / `counters_config` / `detection_config`）——新增需求**优先往 JSON 加键**，不要新加 Column
- `SystemConfig` KV 表——最适合存"客户级全局配置"
- `_trigger_event` hook（所有事件中心触发点）/ `_handle_cycle_end` hook（cycle 结束中心）——插件挂这里
- MES 适配器注册表（5 种推送协议）/ 自定义导出字段注册表（308 字段）
- 插件平台：10 个 hook（含 returnable）+ 10 个 PluginHost 主动 API + `<TjSlot>` UI 槽位 + `plugin_data` JSON 命名空间（详见 `docs/plugin-system/`）

---

## 八、关键不变量（永远不能违反）

修改任何代码前，先确认你**没有**违反这些约定：

1. **API 前缀必须 `/api/v1/`**（不是 `/api/`）
2. **`OPENCV_FFMPEG_CAPTURE_OPTIONS=threads;1` 必须在 cv2 import 前 setdefault**（`backend/main.py:line 4`，v3.1.3 关键修复，否则 libavcodec 断言）
3. **修改 `source.py` 主类前必须看 `__getattr__/setattr__` 兼容层**（在 `source.py` 内）— 老代码访问 `self._kalman_enabled` 等会被路由到 has-a 组件
4. **`channel_manager.set_channel_count` 必须完成全部 4 处 `on_channel_removed` 配套清理**（`mes_hook` / `alarm_router` / channel-group 协调器 / workpiece-flow 协调器），否则 MES dict 残留 + 报警串口未释放 + 协调器幽灵工位。**插件如维护 channel 维度的状态，同样必须挂 `on_channel_removed` 清理**，不能只加不清
5. **测试 fixture 必须独立 DB / unique uuid，不要 reload uvicorn**（v3.5.0 BDD 框架痛过）
6. **修改 `mes_hooks.py` / `services/scanner.py` 前先读对应 changelog**（debug-mes 是项目最大踩坑区）
7. **改前端 `views/Monitor` 前**：双缓冲 MJPEG + 多通道 state 隔离（v2.6.0 / v3.0.0 / v3.1.3 多次修过）。其中 `STREAM_SWAP_INTERVAL=600`（600 个 150ms 轮询拍 ≈ 90 秒）的定期换流是为释放 Chromium 原生解码器内存增长，**勿删勿大改间隔**
8. **改 ORM Schema 后必须在 `backend/db/migrations/` 新建 `mXXXX_*.py` 迁移并注册**（2026-07 起版本化，老 SQLite 升级路径；旧 `migrate_database()` 已是断言桩，别再往里塞 ALTER，详见 `modify-model` skill 第 3 节）
9. **bat 热补丁必须 CRLF 换行符**（LF 在 Windows 上闪退）。插件分发包同理：包内任何 `.bat` 必须 CRLF；`plugin.json` / manifest 等 JSON 用标准 LF 即可
10. **不要在 `OPENCV_FFMPEG_CAPTURE_OPTIONS` 之前 import cv2**（顺序敏感）。这条对**插件 backend 模块和测试 conftest.py 同样生效**——任何会间接 import cv2 的代码都不得早于该环境变量设置执行
11. **改 `source_settlement_mixin.py` 时不要把 `_process_last_first_mode` / `_process_cross_cycle_groups` 之间的守门去掉**（v3.9.0 起两个状态机都依赖 `settlement_mode == 'last_first'` / `cross_cycle == true` 严格守门，否则污染其他模式的 cycle_steps）— 修改前必读 `debug-source` skill
12. **前端插件代码动态加载不能只依赖 `import(blob:...)`**（v3.15.4 血泪教训）：打包后主窗口走 `file://`，Chromium 拦 `file://` 源下的 blob 动态 import → 插件 ESM 静默加载失败、前端定制完全不生效，本地 `http://localhost` 不复现。`usePluginLoader.js` 必须保留 **blob → data:URL → 后端 http URL** 三级兜底；`markRaw` 标记 Vue Component 用**顶部静态 import**。前端插件加载有疑问先看后端日志（已通过 `POST /api/v1/plugins/client-log` 回传），不要开 F12
13. **前端插件 bootstrap 必须先等后端就绪再拉清单**（v3.15.5 血泪教训）：打包后 `file://` 页面加载远早于后端冷启动（CUDA 预热+模型加载好几秒）。`main.js` 插件 bootstrap **不能**裸调 `themeStore.apply()`——必须先 `waitBackendReady()` 轮询探活（`/plugins/active/manifest` 无插件也返回 200）最多 90s，否则一上来 `Network Error` 一次性放弃、插件前端定制全程不加载。这是比第 12 条更靠前的一环
14. **"当前在检工件"映射的取-放时序不许乱动**（`mes_hooks.py` 内 `_inspecting_workpiece[channel_id]`）：放入只在 cycle 绑定 / scan_pair promote 两处，取出只在结算完成 / cycle_end / session_end / 通道移除 / 人工强制作废五处，**放入方和取出方必须严格配对**。多加一处 pop 会导致工件绑错周期、前端"当前工件"卡住或漏绑（v2.7.16 / v3.4.2 均为此修过补丁），改前必读 `debug-mes` skill 与 `docs/plugin-system/inventory/02_data_flow.md` 第十二节
15. **MES Hook 队列的 critical 语义**（`mes_hooks.py:_enqueue`）：`critical=True`（scan/cycle/session 五个核心业务事件，全部现有调用点都是）队列满时**落盘补偿文件、worker 恢复后回放，业务不丢**；`critical=False` 队列满时直接丢弃。两条铁律：① 队列满时**绝不允许阻塞调用方**（结算/检测热路径，v3.x B1 修过"每周期卡 0.8s"）；② 新增 hook 事件若关系业务数据完整性必须走 critical=True 且把 handler 加进可落盘白名单，纯 UI 通知类才可用 False
16. **共享灯柱的事件优先级是可配置的合成算法**（`alarm.py:_recompose_and_apply`）：多工位共用一个物理报警灯时，各通道状态按优先级序 `['ng','warn','ok','idle']`（默认，事件→类别映射默认 event1=ok/event2=ng/event3=event4=warn，均可按报警器配置覆盖）合成最终显示，取优先级最高的活跃事件；视觉无变化不重发串口指令。新增事件类型必须归入这四个类别之一，别绕过合成器直接写串口
17. **`workstation_config.json` 各段写入权独占**：`channel_count` 只由 `set_channel_count()` 写；`channels.<id>` 只由 `save_channel_source()` 写（`merge=False` 时**整段替换该通道配置**，调用方必须传完整配置而不是增量）；顶层 `splash` / `window` / `auto_resume` 段各有专属读写函数、只替换自己的段。任何新代码**禁止手写整个 JSON 文件**——旁路写入会把别段的 key 静默抹掉（该文件无 schema 校验，丢 key 无报错）

---

## 九、已知架构 bug / 死代码

> **完整索引（带 ID / 严重度 / 工时估，2026-07 销账刷新）已下放**到 `docs/plugin-system/inventory/05_tech_debt.md`。新 PR 不要复活、不要扩展这些已知坑。

最该留意的几条（细节进上面的技术债文档）：
- 🟡 CI 编译白名单 2026-07 重审后扩到 17 项（source_routes + Top5 mixin + scanner/mes_hooks/weighing_engine，缺失文件硬 fail），残余小 mixin / has-a 组件仍源码出厂，按需扩列；**新增 >800 行核心文件记得同步 CORE_FILES**
- ✅ 已修销账（2026-07 治理批次）：`_fix_db_paths` 表名 bug 已修；CI CORE_FILES 3 个失效项已剔除；前端死代码四文件（`views/Report/index.vue`、`api/task.js` / `camera.js` / `report.js`）已删；孤儿 mixin 两文件（`source_recording_mixin.py` 546 行、`source_industrial_camera_mixin.py` 438 行）已删，同名方法 MRO 冲突随之解除

---

## 十、关键环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `TIANJUN_DATA_DIR` | `BASE_DIR` | 用户数据目录（DB / uploads / recordings） |
| `BACKEND_SKIP_INIT` | 否 | 跳过启动初始化（测试/调试用） |
| `ENABLE_API_DOCS` | `1` | `0` 关闭 `/docs` `/redoc` |
| `ENABLE_DEV_MOCKS` | `0` | `1` 放开 `/source/detection/per-item-mock` 等开发 mock 端点（v3.8.0+，**出厂版必须保持 0**） |
| `CORS_ALLOW_ORIGINS` | — | CORS 白名单 |
| `OPENCV_FFMPEG_CAPTURE_OPTIONS` | `threads;1` | 必须 cv2 import 前 setdefault |
| `MVCAM_COMMON_RUNENV` | — | 海康 SDK 路径 |
| `VITE_API_BASE_URL` | `http://localhost:8001/api/v1` | 前端 axios baseURL |
| `CONDA_PREFIX` | `~/anaconda3/envs/tianjun` | 开发模式 Python 路径 |

> 文件路径速查（后端/前端/Electron 目录树）已删除——直接看仓库或问对应 skill，避免目录树与代码漂移。

---

## 十一、版本里程碑

> **逐版完整变更已下放**到 `docs/changelog/`（每版 `.md` + `.json`）。本文件**只记最近几版一句话定位**，发版时不再往这里堆 changelog。

| 版本 | 日期 | 一句话 |
|---|---|---|
| v3.31.0 | 2026-06-29 | 主程序原生「称重投料模式」落地（电子秤配料防错全流程：去皮+对比标准量+缺料/超量报警+逐件记录持久化落库）+ 设备读数驱动状态机引擎 + PL2303 驱动内置 + mock_weight 模拟 + bestar 插件退役 |
| v3.30.0 | 2026-06-28 | 规格→项目自动切换升级为统一匹配器（对照表精确→通配符→自动同名子串，入站与包装共用，与位置/分隔符解耦，按名匹配默认关+严格边界可选档）+ custom_mix 容器进箱确认多方式（仅帧/仅动作/OR/AND+放托盘动作状态机+屏蔽窗口）+ 频闪修复与逐帧诊断 |
| v3.29.0 | 2026-06-27 | 外部 MES/中控双向对接全闭环（川南火工范式：开工切项目+四要素上屏+在途报警台账闭环+最新开工顶替回推完工+健康检查+自定义接收路径）+ 去硬编码可配置化（集群计时/面板刷新间隔/日志条数）+ 后端崩溃自愈看门狗 + 开机自启可选 |
| v3.28.0 | 2026-06-27 | 逐件覆盖模式离场快照判定+漏打挂起待补+判定时机解耦 + 插件配置统一保存 + 传感器清洁插件 v1.2.0（选择性抑制提示框+棉签使用记录数据页） |
| v3.27.0 | 2026-06-25 | 插件平台「插件主动触发主程序事件」桥接 + 传感器清洁插件三判定接入事件体系 |
| v3.23.0 | 2026-06-22 | 上银包装线现场闭环增强 + 通用 NG 补做策略（人工确认定格 / 强制结案审计 / 缺油嘴 gate） |
| v3.20.0 | 2026-06-15 | 外部 MES 工单主动拉取 + USB 键盘扫码枪 + 调试埋点 |
| v3.19.0 | 2026-06-11 | 全局调试日志系统 + NG 原因可解释性 |
| v3.14.0 | 2026-05-29 | RFC 11 串行流水线结算（Workpiece Flow Coordinator） |
| v3.13.1 | 2026-05-29 | RFC 09 插件平台升级 + RFC 10 工位组主程序原生 |
| v3.10.0 | 2026-05-25 | 用户系统完整闭环（多角色账号 + token 鉴权 + 端点级权限 + M2M API Key） |

（更早版本见 `docs/changelog/`）

---

## 十二、遇到不一致时的优先级

当 AGENTS.md / changelog / 代码注释 互相矛盾时：

1. **代码 > 注释 > changelog > AGENTS.md**
2. 发现矛盾**立刻通知主作者**并提议更新 AGENTS.md
3. skill 文档与代码冲突时同样以代码为准，并回头修 skill

---

**本文件最后更新**：2026-07-05（技术债 HIDDEN-1~8 隐式约定文档化：第八节不变量 2/4/7/9/10 条补插件视角与细节，新增 14~17 条——在检工件映射取放时序 / MES Hook 队列 critical 语义 / 共享灯柱优先级合成 / workstation_config.json 分段写入权。上次发版更新 2026-06-29 v3.31.0）
**维护者**：项目主作者 + AI agents
**维护铁律**：本文件只放"地图 + 守则 + 不变量"。模块细节进 skill，版本变更进 `docs/changelog/`，扩展点/技术债进 `docs/plugin-system/inventory/`。**发版时务必同步更新本文件第一节版本号 + 文件尾日期**（详见 `update-release` skill）。
