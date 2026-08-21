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
| 当前线上版本 | **v3.54.0**（2026-08-21）— 检测主页自定义布局 + 录像存储自定义与长录像治理 + 前端信息架构重构：① 检测主页自定义布局——五形态（单/双/三工位/总览/放大）区块拖拽排版，"样式接管"机制（`data-layout-canvas`/`data-layout-slot` 标注 + runtime 绝对定位百分比坐标，无布局零差异），编辑器覆盖层（八向手柄/24×24 吸附/撤销重做/16:9 锁/z 序/localStorage 草稿防崩溃），按形态键存 SystemConfig KV `monitor_layout.*`（升级备份不丢，**保留命名空间**），reconcile 升级安全（新区块兜底区/孤儿忽略/坏数据整体回默认主页永不挂），新权限点 `monitor.layout.edit`，显示设置入口卡+全部恢复默认；② 录像存储位置自定义——`recording_custom_root` KV + `services/recording_storage.get_video_dirs()` 三处录制点动态解析实时生效，校验从严（黑名单/可写探测/与归档目的地双向互斥），盘失联自动回退默认根，清理/孤儿扫描/存储统计聚合双根，软件内直接回放（治"C 盘易满、无法实时指定视频路径"）；③ 长录像治理三件套（治 24h 录像"视频加载失败"）——录制改 fragmented MP4（`+frag_keyframe+empty_moov+default_base_moof`+`-flush_packets 1`，崩溃仍可解码）+ release 后台 remux_to_faststart 流拷贝出常规 MP4、回放端 h264+yuv420p 探测直出免转码、会话录像按小时分段轮转（每段独立 VideoClip + `GET /data/sessions/{id}/videos` + 播放器分段切换条）；④ 前端 IA 重构——输入源页扩为「工位与输入源」四 tab（多屏/工位组/流水线串行迁入）、MES 页分组 tab（包装结算/触发中心迁入）、项目配置条件 tab「装箱清点」（混合跟踪时出现）、系统设置瘦身，手册/skill/e2e 全量同步；⑤ 全前端文案专业化+匿名化（滑块→物品/托盘→容器/去客户名）+「存为我的模板」本地模板；⑥ 前端巨石拆分五连（Data/Settings/Alarm/Monitor/Project 行为零差异）；⑦ 收编缸体判型二期（feat/tianyong：切步数量门顺序检查/补齐消警/结算挂起/锁框叠加层/手动结算/PLC 判型码）与三工位一屏等宽三列（dev-qing）；⑧ CI 新增 Windows e2e 跑道（起服务/等端口/pytest 单 step 治 Start-Process 活不过 step 边界）。上一版 v3.53.0（2026-08-20）— 录像归档与媒体证据体系（一~四期一次落地）：规则驱动的周期录像自动归档（`video_archive_rules/logs` 两表 + 录像收尾入队独立 worker + 结果/工位/项目三重过滤 + Jinja2 文件名模板 + tmp+rename 原子落位 + 重名策略 + spool 断点重放 8 次预算 + 目录黑名单护栏 + `/api/v1/export/video-archive/*` + Data 页归档卡/弹窗/台账）+ 证据能力（NG 结算瞬间推理线程同帧抽带框关键帧 keyframe_wanted 零开销守门 / sidecar 绑导出模板成对渲染 / bundle_zip 证据包 / evidence-pack 手动批量下载 / clip_tail FFmpeg 尾段秒切失败回退整段）+ 生态联动（字段库 archived_video_path/archived_at/archive_status + aggregations 归档成败数 + MES 网关 video_archived 事件 + 插件 hook + 短信 {archive_success} 变量）+ 远端与治理（FTP/SFTP/S3/HTTP adapter 统一 deliver 契约 + Fernet 凭据加密落库密文回显打码 + PluginHost.register_archive_adapter + 时间窗窗外 defer 不耗预算 + 带宽限速 + 历史回补 + 删源字节校验一致才删）；全部默认关零配置差异。随版修复：监控页无扫码器仍画"等待扫码/清除/禁用扫码"（v3.47 守门丢失，全渲染点挂 hasScannerFor，scanner_resume_blocked 视为在场证据）。上一版 v3.52.0（2026-08-18）— 多显示器一期（dev-qing 合入）：工位子窗映射与单通道监控页——Electron multi-monitor.js 按 display_id/bounds 为每工位开无边框 kiosk 子窗（License 守门/主窗所在屏保留总览操作权/同区去重/renderer 崩溃 60s 窗口 3 次熔断/关机三出口销毁）+ 后端 workstation_config.json 新增 multi_monitor 顶层段（GET/PUT /workstations/multi-monitor，PUT 挂 settings.edit）+ 前端 SingleChannelMonitor 共用单工位组件（主屏放大态与副屏 kiosk 共用，kiosk 屏蔽 Toast/人工确认/插件覆盖/扫码等全部写通路只轮询绑定工位）+ 设置页多屏配置卡热应用 + 多屏开启时主屏总览改快照轮询让出 MJPEG + kiosk 路由不污染主窗路由记忆；默认 enabled=false 零配置差异。随版收编：_save_config 整写抹别段改 merge（不变量 17）、start HTTP 迟返时轮询按后端 is_detecting 释放前端操作锁；合并审计拦下"网格总览整卡点击放大被误改为仅选中"回归（v3.47 行为守住，e2e 双向守门）。上一版 v3.51.5（2026-08-17）— 捷昌 B 站现场补丁收编版：8-15 现场实测 5 修复全量收编（相机/模型串位=前端单工位 localStorage 兜底与后端多通道恢复赛跑、启动黑屏要切页=模型双重加载+channelCount 锁死+is_running 跳变不重连、自动开始盖手动停止=capture 线程 id 判人为记 user_vetoed、绑项目抹相机配置=无 source_type 部分更新转 merge、"全删了还被去重拒码"=追溯页多工位默认关"仅当前项目"过滤）+ 详细调试日志（[ChannelCfg] 写盘留痕/拒码全量依据+解除方法/枚举回退留痕）+ 捷昌三通道打包线标注规范交付；升级零配置差异。上一版 v3.51.4（2026-08-15）— 交付补全补丁版：安装包收编 SQLite→PG 迁移工具 `sqlite_to_pg.py`（v3.49~v3.51.3 从未进包，客户勾 PG 组件也搬不了旧数据，成品解剖审计才发现；落位 `resources\scripts\db\` 零改动可跑）+ CI 打包资源自检加该文件缺失红灯 + run-tests/build-release skill 沉淀「交付审计逃逸复盘」（写了代码≠交付了/故障注入真样本/定稿回归冻结代码）；无代码行为变更。上一版 v3.51.3（2026-08-15）— 捷昌现场夜测反馈补丁版 + PG 组件交付缺口修复：摄像头枚举改 ffmpeg dshow 列设备 + USB (vid,pid,serial) 物理去重（治"2 USB + 1 内置列出 4 个、双工位同选超时"，带设备真名、不试开、全工位使用中标记，ffmpeg 不可用回退老试开法）+ 跨工位抢相机预检 14ms 快速失败 + SQLite 侧车损坏 disk I/O error 启动自愈（隔离 -wal/-shm 为 .corrupt-* 重试）与退出 wal_checkpoint(TRUNCATE) + CI 打包真正带出嵌入式 PG 16.15 可选组件（v3.49 只写了 #ifdef 从未传开关，v3.49~v3.51.2 安装包里根本没有 PG 选项）；四路真实 E2E（真相机/真坏库/真 PG/真浏览器）验证。**更早版本与逐版变更详见 `docs/changelog/`，本文件不再记版本流水账** |
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
| 写/改任何文档、docstring、端点文档 | `docs/dev/conventions/` 三份军规（文档 CI 会按此拦截，端点门禁走基线法） |

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
8. **改 ORM Schema 后必须在 `backend/db/migrations/` 新建 `mXXXX_*.py` 迁移并注册**（2026-07 起版本化，老 SQLite 升级路径；旧 `migrate_database()` 已是断言桩，别再往里塞 ALTER，详见 `modify-model` skill 第 3 节）
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
| v3.54.0 | 2026-08-21 | 检测主页自定义布局（五形态拖拽排版/样式接管/reconcile 升级安全/monitor.layout.edit 权限/显示设置入口）+ 录像存储位置自定义（recording_custom_root 动态解析实时生效/与归档互斥/清理聚合双根）+ 长录像治理三件套（fMP4 录制崩溃可解码 + 后台 remux faststart / h264 探测直出免转码 / 会话按小时分段+分段回放条，治 24h 录像加载失败）+ 前端 IA 重构（工位与输入源四 tab / MES 分组 tab 收包装结算与触发中心 / 装箱清点独立 Tab / 系统设置瘦身）+ 文案专业化匿名化与本地模板 + 巨石拆分五连 + 收编缸体判型二期（tianyong）与三工位一屏三列（dev-qing）+ Windows e2e 跑道 |
| v3.53.0 | 2026-08-20 | 录像归档与媒体证据体系一~四期：规则驱动周期录像自动归档（模板命名/原子落位/spool 重放/台账/Data 页配置弹窗）+ NG 结算瞬间带框关键帧/sidecar 报告/证据包 zip/事件切片 + archived_* 进字段库/video_archived 网关事件与插件 hook/短信变量 + FTP/SFTP/S3/HTTP adapter/Fernet 凭据加密/插件 adapter 注册口/时间窗/限速/历史回补/归档后删源；全部默认关零差异。随版修监控页无扫码器仍画等待扫码/扫码按钮（v3.47 守门丢失） |
| v3.52.0 | 2026-08-18 | 多显示器一期（dev-qing 合入）：Electron 工位子窗映射（kiosk 只读副屏 + 主屏保留操作权 + 崩溃熔断 + 关机清理）+ multi_monitor 配置段与设置页热应用 + SingleChannelMonitor 共用单工位组件 + 主屏总览快照让位 MJPEG；随版收编 _save_config 整写抹别段改 merge、start 迟返操作锁释放；合并审计拦下网格点击放大回归；默认关零配置差异 |
| v3.51.5 | 2026-08-17 | 捷昌 B 站现场补丁收编版：v3.51.3a/b/c 热补丁全量收编（相机/模型串位、启动黑屏要切页、自动开始盖手动停止、绑项目抹相机配置、多工位追溯页藏工件致去重误拒）+ 详细调试日志体系（写盘/拒码/枚举回退全留痕）+ 捷昌三通道打包线标注规范（二工位小件 v1.0 带正误示范图 PDF）；升级零配置差异 |
| v3.51.4 | 2026-08-15 | 交付补全补丁版：安装包收编 sqlite_to_pg 迁移工具（v3.49~v3.51.3 从未进包，成品解剖审计才发现）+ CI 打包资源自检缺失红灯 + run-tests/build-release 沉淀「交付审计逃逸复盘」；无代码行为变更 |
| v3.51.3 | 2026-08-15 | 捷昌现场夜测补丁版 + PG 交付缺口修复：摄像头枚举 ffmpeg dshow + USB (vid,pid,serial) 物理去重（治 4 列 3/双工位同选超时）+ 跨工位抢相机预检 14ms 快速失败 + SQLite disk I/O error 启动自愈（隔离坏侧车 .corrupt-* 重试）与退出 wal_checkpoint(TRUNCATE) + CI 真正带出嵌入式 PG 16.15 可选组件（v3.49~v3.51.2 安装包从没有过 PG 选项）；四路真实 E2E（真相机/真坏库/真 PG 710 测试/真浏览器）验证 |
| v3.51.2 | 2026-08-14 | 摄像头生命周期仿真战役补丁版：Mac 真机 67 断言仿真相机全路径（占用/抢相机/调参重开/开停竞态/强杀重启恢复/双工位异模型恢复），修相机格式探测 Strategy 3 macOS 误入 V4L2 重开留死句柄僵尸态（启动成功但画面永远 No Source）+ 全平台终检；剧本归档 tests/uat/mac_camera_sim/ |
| v3.51.1 | 2026-08-14 | 捷昌 B 站双工位虚拟战役补丁版：虚拟双工位环境（synthetic 剧本源 + 假 TCP 扫码器）60 断言复现现场，修 6 个新功能路径 bug（新码强制收旧账对挂账周期失效 / 拒码后扫码器灯不回亮 / force_ng 超时误播已合格 / 组级 NG 覆盖跨轮污染 / 豁免漂移吞新品种 / 统一播报弹错 Toast）+ synthetic 支持 tracking 模式 + UAT 剧本归档 tests/uat/virtual_dual_station/ |
| v3.51.0 | 2026-08-14 | 捷昌 B 站双工位整改批次：v3.50.0a 热补丁 17 条全量收编（堆损坏 0xC0000374 治本/E 码-合格-码/扫码后才计数/广播全 OK 亮灯/绑定保护/齐件即结算周期守门）+ 开机黑屏收尾兜底恢复轮 + 激活收养开关 activate.adopt_unbound（默认开=存量）+ 工位组统一播报 unified_ok_report（全员合格才一起报 OK，NG 永不抑制，默认关）+ 数据清理联动解封 ok 工件；新增项默认关或保持存量零差异 |
| v3.50.0 | 2026-08-12 | 捷昌二期批次：跟踪模式齐件即结算（仅 ROI离开/容器策略，凑齐并稳定 N 帧立即出结果 + 步骤级"确认放入帧数" + ROI 豁免名单/容器"已结算等离开"状态机防二次入账 + scan_pair 互斥守门）+ 扫码器生命周期码-合格-码闭环（resume_on 仅 OK 亮灯 NG 灭灯等人工恢复：监控页按钮/POST /scanner/resume/触发中心 resume_scanner 三出口 + rearm_forget_last 亮灯作废旧码 + strict_ok_dedup 强制去重 + 拒绝路径统一警告 toast，m0010）；全部默认关零差异 |
| v3.49.0 | 2026-08-12 | 捷昌整改批次：MES 外推并发派发（连接级执行器 + gateway_spool 落盘补发 + 重试预算可配）+ 集群副机上报异步化（独立线程 + cluster_report_spool 断网重放 + report-status 可观测）+ scan_pair 新码先上屏（显式 prev_wp_id 异步结算旧窗口，上屏与结算解耦；广播兄弟通道结算串身份修复）+ 结算耗时埋点 backend.timing + PostgreSQL 支持（sql_compat 方言收编 + pg_dump 备份 + DATABASE_URL 注入 + 数据库卡片 + 安装器 PG 组件 + sqlite_to_pg --verify；幽灵项目绑定 stale 忽略）+ 双方言 db-matrix 扩容与双后端可见 UAT |
| v3.48.1 | 2026-08-11 | 体验修复补丁版：多工位监控（>4 工位）视频加载不出/WebKit 黑屏治本（MJPEG 超 6 连接上限改快照轮询 + 零帧断流自动降级 + 取帧节奏按工位数自适应）+ 数据中心录像「视频加载失败」治本（转码 .tmp 原子落位 + 缓存 `_h264v2` + 超时 300s）+ NG 录像回看三件套（周期 仅OK/仅NG 筛选 + 0.5x~4x 倍速 + 下载录像）+ Electron 退出僵尸 python 兜底（race 8s + forceKillSync）+ alembic env 补 5 组模型 import（修 PG 基线迁移 CI） |
| v3.48.0 | 2026-08-10 | 三分支汇合发版：RFC 13 通用 PLC 连接器（8 协议驱动 + 点位引擎 + bind_sn 绑码/结果码写回 + s7_db_handshake 模板，默认无连接零开销）+ RFC 14 统一触发中心（虚拟按钮/脚踏板/HTTP/串口/定时 6 源 × 全局动作注册表，channel 裁撤第 5 处清理）+ 计数组合判定表（纯视觉判型 + positional 位置去重六参数）+ 上银 SY9（槽位完整性门 + 物品/托盘去重 IoU 可配 + 放工单只认收尾后，m0009；与 v3.47 五开关归一化桥接零差异）+ Modbus 完成脉冲外设协议（逐件覆盖 all_covered/cycle_ok 联动，台达 ES3 文档）+ 短信汇总合并分列/逐工位 + 工位筛选 |
| v3.47.0 | 2026-08-07 | 多分支汇合发版：多工位监控布局重构（三工位横排+网格分页总览+放大详情，工位上限 4→64）+ YoloVision 训练平台互连（模型双向分发+现场帧采样回流自学习闭环，默认关，m0008）+ 开机首启提速/授权激活治本六项（Defender 排除+startup-heavy-init 后台化+machineId 快路径，m0007）+ LG 工时看板插件 v1.5.2（F8 插件导出字段落地）+ custom_mix 记账五开关 + NG 汇总数字口径可选 + macOS MPS 并发串行锁 |
| v3.46.0 | 2026-08-05 | 主程序原生短信/微信通知栈（NG 12h 汇总 + 每日短信日报，五通道统一 sms_providers 工厂共享 sms_config，默认关，迁移 m0006，插件 hook daily_report_before_send）+ 推理设备 auto 档支持 Apple MPS（torch_device 统一出口，MPS 强制 FP32）+ tools/sms_4g 独立 AT 调试工具 + debug-sms skill |
| v3.45.0 | 2026-07-29 | 上银 SY 包装线热补丁收编（0a~0e 滑块记账体系重做：在位身份各自累计峰值+结算挂账等真账+动作前稳定计数快照，双真实视频回归零误判）+ 箱标签扫码授权（组⑧逐箱扫码定数量）+ 包装工单同步进工单管理（默认开）+ 萍乡称重整改（清秤 Z/T 智能选择+过程提醒档+网关推送异步化+达梦溢出）+ 海康 SDK 帧率可配 + dev-qing 逐件修复合入 |
| v3.44.0 | 2026-07-22 | 上银 SY3 NG 处置整改（箱账挂起等处置+工单收尾快照保留+收尾防呆数量门/缺步挂起，真实录像 UAT 验收）+ NG 处置配置统一模型（ng_handling 收敛五代开关，逻辑设置 6卡→2卡+事件页双向联动明示）+ 放托盘动作不应期（治闪断双结算）+ 电子秤串口延迟治本（萍乡） |
| v3.43.0 | 2026-07-20 | 上银包装线整改（放工单=工单收尾语义重构+缺工单判定二选一+重扫拦截+gate永不放行修复+提前放工单报警+确认框闪退修复）+ 实时NG违规即时结算开关 + 人工确认弹窗原因固化/处置明示 + 称重看板被SOP抢占修复 + 操作手册封面品牌重制 |
| v3.42.0 | 2026-07-17 | 「抓取锚点框」标定用单帧推理兜底（infer-once 端点，停止/待机可标定，治打包版标定死环）+ 川南终态工单再开工策略（revive 默认自动复活 / reject 拒收）与开工响应透明化 + 电机装配部署指南 v1.2 |
| v3.41.1 | 2026-07-17 | 技彩 USB 相机"锁帧"修复：三条相机重开路径回放曝光设置 + 曝光按后端语义精准写值（MSMF AE=0 / DSHOW AE=0.25 分道）+ Windows 关 MSMF 硬件变换探测 + 开发文档九版补账（阅读笔记×5 + 深潜×2 + 操作手册） |
| v3.41.0 | 2026-07-17 | 川南 MES 对接两修复：复用工单按"最新开工为准"重绑（治四要素不上屏+推送工单字段 null）+ 网关"仅 NG"周期过滤补嵌套结果取值（治合格周期误推报警接口）+ 网关事件下拉露出称重成品结案 + 模拟秤墙钟计时 + 百斯特手册 v2.1 勘误 |
| v3.40.0 | 2026-07-17 | 川南顺序检测两修复：周期跑偏末步余像误判"合法重复"严格前缀守门（治末步 OK/NG 闪烁+结算多报重复步骤）+ 开工报文自动拉起检测时空闲监控页自动接管（空闲看门狗+轮询真相源同步）+ 末步结果权威 PT 锁定 |
| v3.39.0 | 2026-07-16 | 萍乡百斯特两阶段流水线称重（离秤冻结结算 + 待收尾 FIFO 队列 + 秤指令自动驱动）+ 川南在途报警软件内消除出口 + 开工自动开始检测暂停源复活/原因回带 + 监控页信息条显示定制/扫码按钮可隐 |
| v3.38.0 | 2026-07-15 | 川南"框冻结"治本三刀（收尾持久化出推理线程 + MES 网关熔断/锁窗口收敛 + 清理事务卫生）+ 运行中开工工单回填 + 萍乡自定义班次/皮重看板/称重数值条 + 扫码器旁路 SN 监控 + NG top3 保持 |
| v3.37.0 | 2026-07-14 | 川南反馈整改（开工切项目界面自动跟随 + 开工自动开始检测 + 推理框卡死自愈 + 图像分割入口恢复）+ Logo 打包版回退回归修复 |
| v3.36.1 | 2026-07-13 | 区域事件 overlap 规则「目标框扩边」object_margin（TP 工件下沿扫码几何盲区补丁，与帧率解耦）+ 展会插件 v1.4.1 面板对齐 |
| v3.36.0 | 2026-07-12 | 导航栏 Logo 客户自定义上传（显示设置）+ 展会全应用插件 v1.4.0 全量对齐 v3.33~v3.35 功能面（主程序配置 schema 零改动）|
| v3.35.0 | 2026-07-12 | 萍乡百斯特称重融合架构（视觉 SOP × 秤步骤门控 + 前置选择有效期 + 视觉料源防错）+ USB 报警确认按钮 + MES 数据库直写适配器（达梦等 5 库）+ 包装线复合条码取段/工单号识别/放工单=尾箱收尾 + 步骤消失等待不被打断 |
| v3.34.0 | 2026-07-09 | 电机装配线真实模型上线打磨（多轮次拆分三防护 + 时长门幽灵起点/帧位口径修复 + 违序只报提前出现）+ 人工确认「确认后保留周期(断点补做)」+ 区域事件秒基确认时长 + 标注规范 v1.1/部署指南 + sensor-clean v1.4.2 |
| v3.33.0 | 2026-07-07 | 逐件覆盖重复打防护 + 换板兜底结算 + 安装器目录可选/升级搬家 + 显示与使用一致性修复（工位绑定回写）+ sensor-clean v1.4.1 |
| v3.32.0 | 2026-07-07 | 同标签区域拆分（虚拟步骤/多轮次）+ 区域事件模式（第 6 种逻辑模式，动作规则引擎+序列结算）+ MediaPipe 骨架样式可配 + 频闪自动诊断 + 推理线程单例守护 + 工程治理批次（视图拆分/迁移版本化/路由登记/开发者文档体系）+ showcase v1.3.1 / sensor-clean v1.3.0 |
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

**本文件最后更新**：2026-08-21（发版 v3.54.0：检测主页自定义布局 + 录像存储自定义与长录像治理三件套 + 前端 IA 重构与文案专业化 + 收编缸体判型二期/三工位三列 + Windows e2e 跑道；第一节版本号 + 第十一节里程碑同步。上一版 v3.53.0：录像归档与媒体证据体系）
**维护者**：项目主作者 + AI agents
**维护铁律**：本文件只放"地图 + 守则 + 不变量"。模块细节进 skill，版本变更进 `docs/changelog/`，扩展点/技术债进 `docs/plugin-system/inventory/`。**发版时务必同步更新本文件第一节版本号 + 文件尾日期**（详见 `update-release` skill）。
