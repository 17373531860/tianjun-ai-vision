# AGENTS.md — 天军 AI 视觉检测系统

> **给 AI 助手（Claude Code / Cursor / 其他 LLM agent）的项目说明书**
>
> 这份文档只做三件事：**地图（项目是什么 / 模块在哪）+ 守则（怎么干活）+ 不变量（不能违反什么）**。
> **细节一律不进本文件**——模块详解在对应 `.claude/skills/<name>/SKILL.md`，版本历史在 `docs/changelog/`，插件资料在 `docs/plugin-system/`，按需读取。
>
> 第三节是工作边界；其余内容按任务查阅。小改动不要求先读完整项目地图或无关文档。

---

## 一、项目快速档案

| 项 | 值 |
|---|---|
| 项目名 | 天军 AI 视觉检测系统（TianJun AI Vision） |
| 性质 | **商业项目，客户已在用** — 工厂工控机部署 |
| 客户场景 | 装配线视觉检测 / 包装线 / MES 数据回传 / 多工位集群 |
| 部署模式 | Windows 工控机本地安装（Inno Setup 一键包，约 1.5 GB），Electron 桌面壳套 FastAPI 后端 + Vue3 前端 |
| 当前线上版本 | **v3.56.0**（2026-09-01）— 周期多码采集与检测流程增强；完整变更见 `docs/changelog/`，版本权威源为 `electron/package.json`。 |
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

### 任务范围与授权
- **一次完成一个用户目标**：同一目标内的必要修改和验证连续推进，可并行处理独立子任务；不要顺手扩展无关功能，也不要每个文件改完都停下等确认。
- 用户明确指令和本轮已有授权优先于 skills 的默认流程。仅在缺少会改变结果的关键信息、超出授权范围或涉及尚未授权的破坏性操作时询问；可独立推进的工作继续做。
- 已确认使用独立临时数据且不访问生产的必要本地测试，可直接运行并修复本次改动导致的失败，无需逐步审批。

### 完成定义（按改动行为与风险选择验证）

先明确预期结果和影响，再完成适用检查。有依赖的步骤按序执行，独立检查可并行；不适用项记 N/A，不算失败。
同一份有效证据可满足多个检查，不要求为步骤编号重复运行。相关检查通过且目标已满足即可收尾；只有新改动、失败或未解决风险才扩大或重复验证。
纯文档、skill 或注释改动检查内容、引用和适用的文档门禁，不启动产品服务或跑业务 E2E。

| 序 | 步骤 | 何时强制 |
|----|------|----------|
| T0 | 明确目标；复现问题时描述必要操作、实际与预期结果 | 总是，表述长度按任务需要 |
| T1 | 确定影响文件、行为及适用检查；按所改模块读取 modify-* skill | 总是，纯文档/注释不触发代码影响分析 |
| T2 | 完成改动，运行适用的 lint / 文档检查 | 有文件改动 |
| T3 | 运行受影响的后端测试；覆盖不足时补行为回归 | 后端行为改动；全量回归用于跨模块风险或发版 |
| T4 | 真浏览器渲染并检查受影响 UI，留必要截图 | 视觉或交互有变化；纯注释不触发 |
| T5 | 操作后 GET / 查隔离 DB 验证真实生效 | UI 涉及数据写入、持久化或前后端状态同步 |
| T6 | 运行相关 CI E2E；现有覆盖不足时补用例 | 交互契约变化或 UI bug 回归；纯文案/样式不强制新增 |
| T7 | 路径 H 可见浏览器 UAT + 视频、截图、run.log | 客户 UI 流程反馈或明确要求可见验收；纯后端问题用对应集成证据 |
| T8 | 简述改动、验证结果和未完成的适用检查 | 总是；仅正式验收或用户要求时逐行贴表 |

> T4 的真实 UI 检查不能用 build 冒充；T7 的 `tests/uat/` 人眼证据不能冒充 T6 的 CI 回归。两项都适用时都须满足，但可复用已有覆盖和同一次运行的证据。

### 隐含约定
- **多 worktree 纪律**：本机长期并存 `tianjun-main` / `tianjun副本` / `tianjun-se9` 三棵树，同名文件各一份。用户说"主程序/main"就**必须**在 `tianjun-main` 操作——动手前 `pwd` + 核文件路径前缀，别因为副本在手边就顺手改副本（真实事故：被要求改 main 却改了副本，白干一轮）。端口归属以用户**本次**指令为准，不照默认表想当然。详见 `start-dev-servers` skill 第一节铁律 + 故障 I/J
- 商业项目客户在用，**主线改动要先做影响分析**（用 modify-* 系列 skill）
- **不要主动 commit / push**，等用户明确指令
- **破坏性操作必须先问**：`rm -rf`、`drop table`、`force push`、改 schema、删 commit 历史
- **改动后必须验证**：按上表选择能证明本次结果的检查，已有充分证据时不额外启动服务或重复冒烟。
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

按实际要执行的操作选择对应 skill，并只读相关章节；不是命中关键词就加载整套文档。纯文案、注释或文档修订不因提到模块名而触发其业务流程。各模块的文件清单、踩坑和扩展点在对应 skill 中。

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
| 排查短信通知（12h 汇总不发 / AT 失败 / 云 HTTP 失败 / 与灯塔串口冲突）| `debug-sms` |
| 排查训练平台互连（模型包不入库 / 采样不回传 / 队列堆积 / 拉取游标不动）| `debug-interconnect` |
| 排查 PLC 对接（连不上 / 点位值乱码 / 规则不触发 / 绑码不建工件 / 结果码不回写）| `debug-plc` |
| 排查触发中心（虚拟按钮不结算 / 脚踏板没反应 / HTTP 触发 403 / 串口不匹配 / 定时不发 / 动作不执行）| `debug-triggers` |
| 排查 MES 异常（工单 / 工件 / 缺陷 / 扫码器 / Hook / Gateway / 外设）| `debug-mes` |
| 排查集群主从（box 不齐 / 副机心跳 / box_complete 不推 MES）| `debug-cluster` |
| 排查 Session/Cycle/Step 数据问题 | `debug-session` |
| 排查 Electron 桌面壳问题 | `debug-electron` |
| 排查前端问题 | `debug-frontend` |
| 排查自定义导出 / 实时规则 / 模板 / 字段中央仓库（v3.5.0+）| `debug-export` |
| 排查录像归档 / NG 关键帧 / 证据包 / 远端投递 FTP·SFTP·S3·HTTP / 归档凭据（v3.53+）| `debug-video-archive` |
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
| 想弄懂"为什么这样设计"（架构总览 / 状态机·MES·集群·插件加载深潜）/ 查表结构 / 查 API 面 / 新人跑通环境 | `docs/dev/`（开发者文档：受众路由见其 README；表参考与 OpenAPI 快照是生成物） |
| 找某函数在哪 / 某状态变量谁读谁写 / 行号级源码定位 / 密钥·配置文件落点 | `docs/dev/_reading_notes/`（函数级源码索引，**翻源码前先查这里**，行号漂移以代码为准） |
| 写/改文档、docstring、端点文档 | 按对象读取 `docs/dev/conventions/` 对应规范：Markdown 看文档写作，代码注释看 docstring，API 文档看端点规范 |

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
                   │ (WAL)       │        │ 多通道+集群  │        │ 6 种适配器 │
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

> 挂载唯一登记处：`backend/api/router_manifest.py`（OVERLAP-3 治理，2026-07）。新增路由去那里登记，别在 `main.py` 直挂、别往 `api/__init__.py` 塞聚合。

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
| `/sms/*` | `sms.py` | 系统级统一短信通道 + NG 汇总通知（通道五选一 at_modem/generic_http/wxpusher/aliyun/tencent，默认关） |
| `/sms-report/*` | `sms_report.py` | v3.46 每日短信日报（规则/试发/预览/日志；通道配置共用 `/sms/config`） |
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
| `/interconnect/*` | `interconnect.py` | v3.47 YoloVision 训练平台互连（模型分发 + 帧采样回流，默认关） |
| `/plc/*` | `plc.py` | RFC 13 通用 PLC 连接器（8 种驱动 + 点位/规则全可配 + 方案模板，默认无连接零开销） |
| `/triggers/*` | `triggers.py` | RFC 14 统一触发中心（虚拟按钮/脚踏板/HTTP/串口/定时 6 种触发源 × 全局动作注册表，默认无实例零开销） |
| `/scan-collect/*` | `scan_collect.py` | v3.56 周期多码采集（一工件多码分类/去重/数量门/收尾结算 + NG 挂起 + 视觉双验，默认关） |
| `/ocr/*` | `ocr.py` | 2026-09 OCR 读字（模型仓库试一试抽屉 + ocr 逻辑模式） |
| `/anomaly/*` | `anomaly.py` | 2026-09 异常检测合格品记忆库（模型仓库试一试抽屉管理 + anomaly 逻辑模式） |
| `/vlm/*` | `vlm.py` | 2026-09 VLM 坐诊看图问答（默认关） |
| `/orientation/*` | `orientation.py` | 2026-09 朝向估计试用/装机标定（facing_dwell 推理侧探针，三层后端） |
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
- MES 适配器注册表（6 种推送协议，v3.35 起含数据库直写）/ 自定义导出字段注册表（308 字段）
- 插件平台：17 种 hook（含 returnable，v3.53 新增 `video_archived`）+ 18 个 PluginHost 主动 API（v3.53 新增 `register_archive_adapter`）+ `<TjSlot>` UI 槽位 + `plugin_data` JSON 命名空间（详见 `docs/plugin-system/`）

---

## 八、关键不变量（永远不能违反）

修改任何代码前，先确认你**没有**违反这些约定：

1. **API 前缀必须 `/api/v1/`**（不是 `/api/`）
2. **`OPENCV_FFMPEG_CAPTURE_OPTIONS=threads;1` 必须在 cv2 import 前 setdefault**（`backend/main.py:line 4`，v3.1.3 关键修复，否则 libavcodec 断言）
3. **修改 `source.py` 主类前必须看 `__getattr__/setattr__` 兼容层**（在 `source.py` 内）— 老代码访问 `self._kalman_enabled` 等会被路由到 has-a 组件
4. **`channel_manager.set_channel_count` 必须完成全部 5 处 `on_channel_removed` 配套清理**（`mes_hook` / `alarm_router` / channel-group 协调器 / workpiece-flow 协调器 / 触发中心 trigger_manager（v3.48+）），否则 MES dict 残留 + 报警串口未释放 + 协调器幽灵工位 + 像素触发源对着裁撤工位空采样。**插件如维护 channel 维度的状态，同样必须挂 `on_channel_removed` 清理**，不能只加不清
5. **测试 fixture 必须独立 DB / unique uuid，不要 reload uvicorn**（v3.5.0 BDD 框架痛过）
6. **修改 `mes_hooks.py` / `services/scanner.py` 前先读对应 changelog**（debug-mes 是项目最大踩坑区）
7. **改前端 `views/Monitor` 前**：双缓冲 MJPEG + 多通道 state 隔离（v2.6.0 / v3.0.0 / v3.1.3 多次修过）。其中 `STREAM_SWAP_INTERVAL=600`（600 个 150ms 轮询拍 ≈ 90 秒）的定期换流是为释放 Chromium 原生解码器内存增长，**勿删勿大改间隔**
8. **已有表补列、索引或数据回填等升级操作，必须在 `backend/db/migrations/` 新建 `mXXXX_*.py` 迁移并注册**。仅新增表且 `Base.metadata.create_all` 足以完成创建时，核对模型已注册及新旧库建表路径，无需空迁移；如涉及旧数据搬迁仍须迁移。旧 `migrate_database()` 已是断言桩，别再往里塞 ALTER，详见 `modify-model` skill 第 3 节。
9. **bat 热补丁必须 CRLF 换行符**（LF 在 Windows 上闪退）。插件分发包同理：包内任何 `.bat` 必须 CRLF；`plugin.json` / manifest 等 JSON 用标准 LF 即可
10. **不要在 `OPENCV_FFMPEG_CAPTURE_OPTIONS` 之前 import cv2**（顺序敏感）。这条对**插件 backend 模块和测试 conftest.py 同样生效**——任何会间接 import cv2 的代码都不得早于该环境变量设置执行
11. **改 `source_settlement_mixin.py` 时不要把 `_process_last_first_mode` / `_process_cross_cycle_groups` 之间的守门去掉**（v3.9.0 起两个状态机都依赖 `settlement_mode == 'last_first'` / `cross_cycle == true` 严格守门，否则污染其他模式的 cycle_steps）— 修改前必读 `debug-source` skill
12. **前端插件代码动态加载不能只依赖 `import(blob:...)`**（v3.15.4 血泪教训）：打包后主窗口走 `file://`，Chromium 拦 `file://` 源下的 blob 动态 import → 插件 ESM 静默加载失败、前端定制完全不生效，本地 `http://localhost` 不复现。`usePluginLoader.js` 必须保留 **blob → data:URL → 后端 http URL** 三级兜底；`markRaw` 标记 Vue Component 用**顶部静态 import**。前端插件加载有疑问先看后端日志（已通过 `POST /api/v1/plugins/client-log` 回传），不要开 F12
13. **前端插件 bootstrap 必须先等后端就绪再拉清单**（v3.15.5 血泪教训）：打包后 `file://` 页面加载远早于后端冷启动（CUDA 预热+模型加载好几秒）。`main.js` 插件 bootstrap **不能**裸调 `themeStore.apply()`——必须先 `waitBackendReady()` 轮询探活（`/plugins/active/manifest` 无插件也返回 200）最多 90s，否则一上来 `Network Error` 一次性放弃、插件前端定制全程不加载。这是比第 12 条更靠前的一环
14. **`_inspecting_workpiece[channel_id]` 的放入/取出必须严格配对**，多加一处 pop 会工件绑错周期、前端"当前工件"卡住（v2.7.16 / v3.4.2 修过）——取放点清单见 `debug-mes` skill 第三节
15. **MES Hook 队列满时绝不允许阻塞调用方**（结算/检测热路径，修过"每周期卡 0.8s"）：critical 任务落盘回放不丢业务、非关键直接丢——critical 语义与落盘白名单见 `debug-mes` skill 第三节
16. **新增报警事件必须归入共享灯柱四类优先级（ng/warn/ok/idle）之一，禁止绕过合成器直写串口**——合成算法见 `debug-alarm` skill 第六节
17. **`workstation_config.json` 各段写入权独占，禁止任何代码整写该 JSON**（无 schema 校验，旁路写会静默抹掉别段 key）——分段写入清单见 `debug-channel` skill 第七节

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
| `CORS_ALLOW_ORIGINS` | 未设=本机+RFC1918 私网段 | 显式设置则只认名单+本机，不自动放行私网 |
| `OPENCV_FFMPEG_CAPTURE_OPTIONS` | `threads;1` | 必须 cv2 import 前 setdefault |
| `MVCAM_COMMON_RUNENV` | — | 海康 SDK 路径 |
| `VITE_API_BASE_URL` | 空（生产/开发默认不设） | 覆盖前端 axios baseURL；空则走 `frontend/src/api/backendTarget.js`（Electron→localhost:8001，浏览器生产同源 `/api/v1`，Vite 开发按页面 hostname:8001）。副本 worktree 后端非 8001 时写 `frontend/.env.development.local` |
| `TIANJUN_WEB_DIST` | 自动定位 dist | `off` 关闭后端托管 `frontend/dist`（局域网一体机工位屏） |
| `CONDA_PREFIX` | `~/anaconda3/envs/tianjun` | 开发模式 Python 路径 |

> 文件路径速查（后端/前端/Electron 目录树）已删除——直接看仓库或问对应 skill，避免目录树与代码漂移。

---

## 十一、版本历史

逐版变更见 `docs/changelog/`，总览见 `docs/CHANGELOG.md`。仅排查相关版本回归或准备发版时读取对应条目，不在常驻指令中复制版本流水账。

---

## 十二、遇到不一致时的优先级

1. **行为现状**以当前代码和可复现证据为准，文档与注释用于解释；代码不能覆盖用户授权边界或安全不变量。
2. 本次范围内的文档漂移直接修正，收尾说明；无关漂移记录即可，不自动扩成清理任务。
3. 仅当冲突影响目标或安全且无法从现有证据判断时询问用户，并引用造成阻塞的具体文件和条款；其余工作继续推进。

---

**本文件最后更新**：2026-09-14（按 GPT-6 Astra 官方建议收窄流程、授权与验证范围；产品版本不变）
**维护者**：项目主作者 + AI agents
**维护铁律**：本文件只放"地图 + 守则 + 不变量"。模块细节进 skill，版本变更进 `docs/changelog/`，扩展点/技术债进 `docs/plugin-system/inventory/`。**发版时务必同步更新本文件第一节版本号 + 文件尾日期**（详见 `update-release` skill）。
