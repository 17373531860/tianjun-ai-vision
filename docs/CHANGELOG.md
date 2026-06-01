# Changelog

## v3.16.0 (2026-06-01)

> 补齐福建金龙双工位定制 R1–R5 中缺失的**配置 UI**（后端能力 v3.15.5 已具备，缺前端入口致客户找不到、误以为没做）。

- [FEAT-001] 新增: 步骤设置表「步骤耗时三档」配置 UI（R1/R2）— 主程序步骤表加「步骤耗时三档」列并留插槽 `project.step-cell.durations`，插件 v1.1.9 填充三输入框配置单元格写后端 `/durations/step-durations`；后端判定逻辑 v3.15.5 已有
- [FEAT-002] 新增: 设置页「工位组互通」配置面板（R4）— 工位组 CRUD 封装 + 管理面板 + 设置页 Tab；后端工位组路由/表/协调器 v3.15.5 已有，本版补前端入口
- [PLUGIN-001] 插件: 福建金龙双工位插件 v1.1.8 → v1.1.9（仅前端增量三档配置单元格），重新打包 + 主签名私钥重签（指纹 d1f2fcb6 与客户机白名单一致），后端验签器客户机同款环境验 5/5 全通过；插件不随包内置，客户需界面单独上传
- 注意: R3（双工位布局）/R5（权威 OK/NG 统计）v3.15.5 + 插件已实现，本版无改动；客户验证需装 v3.16.0 主程序 + 上传 v1.1.9 插件包

---

## v3.15.5 (2026-06-01)

> 修复前端插件加载**抢在后端就绪之前**执行导致的 Network Error 一次性放弃。现场 v3.15.4 客户机日志（由 v3.15.4 新增的诊断日志双通道精准抓到）显示：前端页面加载远早于后端冷启动完成（CUDA 预热 + 模型加载好几秒），旧逻辑一上来拉插件清单就 Network Error、然后直接放弃，插件前端定制全程不加载。这是 v3.15.4 `file://` 三级 import 修复**更靠前的一环**（现场根本没走到 import 就卡在拉清单）。

- [BUG-001] 修复(P0): 前端插件加载抢跑后端就绪 → Network Error 一次性放弃 — `main.js` 启动 fire-and-forget 调主题/ESM 加载，既不等后端就绪也不重试，而前端 `file://` 页面加载远早于后端冷启动完成；修法：插件 bootstrap 先 `waitBackendReady()` 轮询探活（用 `/plugins/active/manifest`，无插件也返回 200）等后端就绪、最多 90s，再拉主题 + 加载 ESM，全程带 `[⬛ PluginBootstrap]` 日志
- [IMP-001] 验证: 本次根因完全靠 v3.15.4 诊断日志双通道 + renderer 转发定位（终端直出 `[Renderer:WARN] [⬛ PluginLoader] ... Network Error`），无需 F12 / 转发补丁 / 敲命令；同时确认 v3.15.4 版本号读取修复生效（日志 `主程序版本 3.15.4` 不再 0.0.0）

---

## v3.15.4 (2026-06-01)

> 彻底根治插件系统现场连环事故并把所有热补丁固化进源码：修复打包后 `file://` 环境下前端插件代码动态加载失败（双工位定制 UI 不生效、本地 http 不复现）的 P0；插件加载全链路诊断日志双通道（终端 + 落盘），排障无需 F12 / 不再转发补丁 / 不再敲命令；验签每步打期望值 vs 实际；安装器覆盖安装清旧前端产物根治新旧 bundle 混叠。

- [BUG-001] 修复(P0): 打包后前端插件代码动态加载失败 — `file://` 下浏览器拦 blob 动态 import 致插件 ESM 静默加载失败、前端定制全不生效（本地 `http` 不复现）；import 改 blob→data→http 三级兜底 + 插槽注册 `markRaw` 改静态 import
- [FEAT-001] 新增: 插件加载全链路诊断日志双通道 — 新增 `POST /plugins/client-log` 接收前端回传，前端每步 `console`(`[⬛`)+回传后端 logger(终端 + 落盘)，Electron 转发扩展含 Plugin 全量；告别 F12 黑盒
- [IMP-001] 改进: 验签链路每步打期望值 vs 实际值（摘要 / 公钥指纹 / HMAC），任何 `PLUGIN_*_FAIL` 后端日志一眼定位；加载器开头打主程序版本（0.0.0 误拒可见）
- [IMP-002] 改进: 双工位插件已激活但工位数 ≠ 2 时监控页给明确可见提示
- [IMP-003] 改进: 安装器覆盖安装前清旧前端产物（根治新旧 bundle 混叠）+ `start_backend.sh` 密钥文件容错
- [HARD-001] 加固: v3.15.x 已发热补丁全部固化进源码（摘要排序 / cryptography / 内置密钥 / 版本读 env / 密钥文件容错），下次打包不复发

---

## v3.15.2 (2026-06-01)

> 紧急热修 v3.15.1 现场 P0：所有 Windows 客户装插件报「插件文件摘要不一致」（跨平台路径排序导致摘要漂移）+ 根治手动从 cmd 启动时日志整屏乱码。配套热补丁可让现存 v3.15.1 客户不重装即修插件问题。

- [BUG-001] 修复(P0): 插件安装报 PLUGIN_FILES_DIGEST_FAIL「插件文件摘要不一致」— 摘要计算按 Path 对象排序，Windows 大小写不敏感把 `README.md` 排错位致摘要漂移；排序键改 POSIX 字符串 `as_posix()`，与打包端/zip 算法对齐
- [BUG-002] 修复: 手动从 cmd 启动日志整屏乱码 — cmd 默认 GBK，Electron 主进程启动最早 `chcp 65001` 切控制台 UTF-8
- [TEST-001] 加跨平台摘要排序回归测试（`PureWindowsPath` 模拟两平台分裂 + 大写文件名目录/zip 一致契约）
- [HOTFIX-001] 配套热补丁 `patch_v3.15.2a`：替换源码 `_plugin_common.py`，现存 v3.15.1 客户重启即修插件摘要 bug，无需重装（仅修 BUG-001）

---

## v3.15.1 (2026-05-31)

> 热修 v3.15.0 现场反馈三个问题（客户两段视频实证）+ CI 打包资源自检防复发。

- [BUG-001] 修复: 插件安装失败 PLUGIN_MANIFEST_SCHEMA_FAIL — `plugin.schema.json` + `customer-codes.md` 没打包，extraResources 加 `docs/plugin-system`
- [BUG-002] 修复: 启动无任何动画 — `splash.html` 漏进打包白名单致旧版 splash 透明空窗，files 加 `splash.html`
- [BUG-003] 修复: 启动后偶发"获取GPU列表失败" — 健康检查太浅 + CUDA 冷初始化(5秒)撞前端并发，后端启动预热 CUDA + 前端静默重试
- [BUG-004] 修复: 插件安装临时文件清理 `WinError 32` 掩盖真错，unlink 包 try/except
- [FEAT-001] 新增: CI 打包资源自检 step，缺关键资源(schema/splash.html)红灯阻断发版，防漏打包复发

---

## v3.15.0 (2026-05-31)

> RFC 12「步骤进行中计时广播」平台能力 + 前端 `host.api` 已鉴权 axios 暴露, 为「步骤耗时三档实时报警」类插件铺基础设施。同步把主程序版本 bump 到 3.15.0 (与福建金龙双工位插件 v1.1.8 声明的 `main_version_min=3.15.0` 对齐 — 低于此版本的主程序会被插件版本校验硬拒绝加载)。无破坏性改动, 未装插件场景字节级零差异。

- [FEAT-001] **RFC 12 `step_tick` 计时广播 hook** — 主程序在推理热路径里对每个正在计时的步骤按 ~1Hz 节流 fire, ctx 携带 `elapsed_sec` + 步骤身份 + 主程序侧 `min/max_duration` (只读参考)。**只读 observe hook**, 主程序不内嵌任何阈值策略; "步骤进行中实时警告/超时报警"完全挂插件侧 (阈值判定 + trigger_alarm + 经 pre_cycle_end 改写 NG)。ctx 字段契约见 `tests/plugin_system/test_step_tick_hook.py`。
- [FEAT-002] **前端 `host.api` 暴露已鉴权 axios 实例** — 插件前端可直接调自有后端路由 (复用主程序登录 token / baseURL), 不必自己拼鉴权头。
- [FEAT-003] **福建金龙双工位插件 v1.1.8 配套** — 三档耗时 (未达最短 / 超警告 / 超最长) 步骤进行中当场报警 + 判 NG; 检测页 OK/NG 卡改用插件权威 live-stats (按库内 `is_good` 重算), 被插件判废周期不再被错记成合格。纯插件不动主程序。
- [CHORE-001] **版本号 bump 3.14.0 → 3.15.0** — `electron/package.json` 唯一权威源, 后端 `backend/_version.py` 运行时读取供插件 `main_version_min/max` 校验。

---

## v3.14.0 (2026-05-29)

> RFC 11「串行流水线结算」一次性闭环 (M0-M8 全部交付)。新增「单机内多工位串行流水线」原生能力: 同一工件依次走过 N 个摄像头工位, 全过才算合格。架构上与已有 ChannelGroup (并行) / cluster (跨机汇总) 完全独立、不冲突, 同工位互斥。三种触发模式 (扫码 / 时间窗 FIFO / 物理 GPIO) 配齐, 福建金龙现场无扫码流水线场景跑通。详细 changelog 见 `docs/plugin-system/design/11_workpiece_flow_rfc.md`。

- [FEAT-001] **WorkpieceFlowConfig + WorkpieceFlowRun ORM** — 2 张新表 + `Base.metadata.create_all` 自动注册 + 启动迁移把上次进程残留的 in_progress run 自动标 aborted。
- [FEAT-002] **WorkpieceFlowCoordinator 单例 + 状态机** — `CREATED → STATION_RUNNING → STATION_DONE → COMPLETED/SHORT_CIRCUITED/TIMEOUT` 五态闭环, FIFO 队列 + max_in_flight 守门 + threading.Timer 超时, 失败完全隔离 (db/alarm/hooks/mes 异常永远不打断主流程)。
- [FEAT-003] **3 种触发器策略** — `TimeWindowTrigger` 监听入口 cycle_start 自动 FIFO 入队 (适合无扫码节拍稳定场景); `ScanTrigger` 复用 mes_hooks.on_scan_received + `entry` / `each_station` 两种绑定策略 + scan_pair 互斥; `PhysicalTrigger` 接 external_device GPIO/Modbus 信号 + dedup 去抖 + 自动序号生成。
- [FEAT-004] **REST API + Pydantic schema + 权限** — `/api/v1/workpiece-flows/` 8 个端点 (列表/CRUD/state/runs/run详情), `system.workpiece_flow.view` + `system.workpiece_flow.manage` 两个新权限, 创建/启用时与 ChannelGroup 互斥校验, 启用状态下禁止删除 / 改 stations。
- [FEAT-005] **5 个插件 hook + 2 个 PluginHost 主动 API** — `workpiece_flow_enter` / `_station_done` / `_completed` (returnable `override_final_result`) / `_timeout` (returnable `override_timeout_action`) / `_short_circuit`; PluginHost 新增 `list_workpiece_flows` / `query_workpiece_flow_state` (需 manifest 声明 `runtime.workpiece_flow_observe` capability)。
- [FEAT-006] **Monitor + Settings UI** — Monitor 加 `monitor.workpiece-flow.indicator` slot (默认实现展示 in-flight 工件列表, 客户插件可全覆盖); Settings 加「流水线串行」Tab (3 种 trigger_mode 表单切换 + 启用前预检 + 运行时 state 查看对话框)。
- [FEAT-007] **集成现有事件管线** — VSM `start_cycle` / `end_cycle` 接 Coordinator.on_cycle_started/settled; mes_hooks.\_handle_scan 在 channel 属于 flow 时短路给 Coordinator.on_scan_received (与 scan_pair 严格互斥); channel_manager.set_channel_count 减少通道时清理 Coordinator 内 channel_to_flow 残留。
- [TEST-001] **77 个测试零回归** — `tests/workpiece_flow/` 73 (TimeWindow 18 + Scan 15 + API 19 + Hook 8 + Physical 13) + `tests/step_defs/test_workpiece_flow_v314.py` 4 个 BDD 场景 (福建金龙 demo + 短路 + 删除前必须禁用 + 与工位组互斥), 全部 14 秒内跑完。
- [DOC-001] **完整 RFC + plugin manifest 文档更新** — `docs/plugin-system/design/11_workpiece_flow_rfc.md` 落 RFC 全文; `01_manifest_schema.md` 加 `runtime.workpiece_flow_observe` capability。

已知边界 (留待 v3.14.1+): Flow → cluster Box 联动 (工件流完成后挂到 box_serial 汇总); 工件返工流程 (Workpiece.status='rework' 重走 flow); 多 worker / 多机 Coordinator (状态搬 Redis); WorkpieceFlowRun 软删 + 数据保留策略。

---

## v3.13.1 (2026-05-29)

> 跳过 v3.13.0 骨架直接发完整闭环 (与 v3.12.0 跳过 v3.11 风格一致)。一次性落 `RFC 09 插件平台 v3.13 升级` (M1 业务 / M2 UI / M3 配置) 与 `RFC 10 工位组` 两大主线, 把客户「双工位 A NG → B NG 联动」「步骤耗时三档显示」「双工位左右半屏 + 共用底栏」「隐藏步骤级红色」四大需求一次性闭环。无破坏性改动, 老项目 JSON / DB / 接口、未装插件场景字节级零差异。详细 changelog 见 `docs/changelog/v3.13.1_2026-05-29.md`。

- [FEAT-001] **工位组 ChannelGroup ORM + CRUD API** — 新表 `channel_groups` + `detection_cycles` 加 3 列, 5 个 CRUD endpoint, 启动迁移自动 ALTER TABLE。
- [FEAT-002] **ChannelGroupCoordinator 单例 + 4 种结算策略** — `synchronized_any_ng` 即时广播 + 直驱 alarm_router, `synchronized_all_ok` 用 threading.Timer 等齐超时, `timeout_action` 支持 `fallback_independent` / `force_ng`, VSM `end_cycle` 用 take-once pending_override 强制改写 is_good。
- [FEAT-003] **工位组与 cluster 互操作** — `build_context_from_cycle` 输出加顶层 `channel_group` + cycle 子树便利字段, 跨机透传到 `BoxSummary.aggregated_context.stations[i].channel_group`, 客户 MES 模板可直接引用 `{channel_group.settle_result}`。
- [FEAT-004] **M1 插件业务流程双向打通** — 10 个新 hook 点 (含 returnable hook) + 10 个 PluginHost 主动 API (trigger_alarm / mes_push / write_plugin_step_field / list_channel_groups 等), 每个都带 capability 守门 + audit log。
- [FEAT-005] **M2 UI 扩展平台化** — TjSlot 全局组件 + 7 个主程序 slot 全接入 (settings.tab.* / project.tab.* / cycle-result.indicator / monitor.step-cell.duration / monitor.step-cell.status / monitor.layout.body / monitor.layout.footer), 没插件时字节级零差异。
- [FEAT-006] **M3 配置扩展系统** — Project / SystemConfig / StepRecord 加 plugin_data 命名空间, 卸载支持 `?purge_data=true` 清理, `registry.tabs.register` 编程式注入配置面板 Tab。
- [BUG-001] **SQLite JSON.contains substring match 误报** — `_check_member_uniqueness` 改 Python 应用层校验, 避开 SQLite JSON 文本子串匹配 bug。
- [BUG-002] **PUT 端点漏 cross-group 唯一性校验** — PUT 时 `member_channel_ids` / `enabled` 改动必调唯一性校验, 含 `exclude_group_id=self`。
- [DOC-001] **完整设计文档** — RFC 09 + RFC 10 + AGENTS.md 产品决策原则 + feature-placement skill。
- [TEST-001] **新增 395 测试零回归** — `tests/plugin_system/` 328 + `tests/channel_group/` 67 全过零回归 (vs v3.12.x baseline)。

已知边界 (留待真实客户驱动): BDD e2e 场景 / 工位组 Settings 前端 Tab / channel_group_ng 自定义事件 / 多 active 插件并存。

Skill 更新: 新增 `feature-placement` skill 操作化产品决策原则; `debug-channel` / `debug-mes` / `add-event-type` 各加 v3.13 章节同步工位组 + 插件 hook 新能力。

---

## v3.12.1 (2026-05-29)

> v3.12.0 客户使用反馈快速修复 + per_item 业务场景四件套补丁。无破坏性改动, 老配置 / DB / 接口全部兼容。详细 changelog 见 `docs/changelog/v3.12.1_2026-05-29.md`。（注: 本版修复内容已随 v3.13.x → v3.14.0 主线合并一并交付, 未单独发布安装包。）

- [BUG-001] **关闭手势启动动画后, 主窗 UI 在后端就绪前就显示, API 全部 404** — `splash.enabled=false` 时 `splashFinishedByRenderer=true` 被立刻设, 主窗 ready-to-show 立刻 show 而后端还在装 24 个模型。恢复 v3.8.1 旧版简单 splash + 加 `legacySplashWindow` + 后端 ready 后才放行主窗。
- [BUG-002] **启动动画手势相机 specific 模式选 OBS 实际拿到 Todesk 虚拟相机** — Chromium 按 origin 哈希 deviceId, 主前端 webContents 跟 splash webContents 是不同 origin → deviceId 不通。splash 改用 `device_label` 反查 splash 端真实 deviceId 再 `exact` 锁定 (老 deviceId 匹配作 fallback)。
- [BUG-003] **per_item "严格等量"勾上后开周期门槛过高, 模型识别不稳永远等不到** — 把"严格等量"跟"开周期门槛"两个语义解耦, 每步达标用 `max(1, expected - tol, expected × ratio)` 折算。
- [BUG-004] **per_item 中心点判定坐标格式 bug, 涂黑覆盖率恒为 0** — YOLO 输出是 `xywh` 但判定代码按 `xyxy` 算, 笔尖 100×100 vs 螺丝 16×22 永远算不到覆盖。改回 xywh, 测试 14/18 vs 老 IoU 8/18。
- [BUG-005] **底栏 / 顶栏 modeLabel 漏 tracking + per_item 映射, 新模式显示"模式: 未设置"** — 5 种语言同步补 `mode.tracking` / `mode.per_item` / `mode.undefined` 文案。
- [FEAT-001] **右上角齿轮菜单加「最小化到任务栏」项** — Navbar dropdown 在 5 种语言之后、开发者模式之前插入 (`v-if="isElectronEnv"` 守门, 浏览器预览态自动隐藏), 5 种语言 i18n 同步。
- [FEAT-002] **per_item 虚拟漏件 NG** — 配 14 颗实际只锁 12 颗时即便扭满 12 颗也判 NG, 符合"配 14 就要扭满 14"的业务语义。
- [FEAT-003] **per_item 工件离场互斥校验 (`finish_requires_no_items`)** — "拿取结算"同帧若画面里还有任何工件标签视为没真拿走, 不算结算累积。治"扭螺丝中拿取手势被误识别为'拿取结算'"的误触发。
- [FEAT-004] **per_item 小物件中心点判定 (步骤级 `coverage_use_center`)** — 涂黑 / 喷漆 / 扫码贴标 场景 IoU 算不到 0.3 永远判 0 覆盖, 改用"目标中心点落动作框内即算覆盖"。步骤级独立开关。
- [FEAT-005] **旧版简单 splash 回归 (splash.enabled=false 时使用)** — 见 BUG-001 修复, 用户视角看到的是 "loading 转圈 + 等待后端 ready" 的连贯启动体验。

已知问题: BUG-002 修复需 Windows 工控机打包验证 (Linux dev 环境模拟不了 Chromium webContents 隔离); 旧版 splash 透明背景在 Win10 1803 之前版本可能掉色; v3.12.0 提到的 9 个测试串污染未修。

---

## v3.12.0 (2026-05-27)

> 合并 `chore/feature-verify` + `feat/per-item-enhance` 两条主线分支正式发版 (跳过 v3.11.x 直接到 v3.12.0, 用户决定)。无破坏性改动, 老项目 JSON/DB/接口全部兼容。详细 changelog 见 `docs/changelog/v3.12.0_2026-05-27.md`。

- [FEAT-001] **per_item 严格等量周期触发** — `pipeline_config.per_item.require_exact_count = True` 时改成"稳定窗口里每帧 == expected_count"+ "次步同时刻 ≥ 各自 expected_count"才开周期, 治打螺丝场景漏件被算 NG。
- [FEAT-002] **per_item 手动结算模式** — `disable_auto_settle = True` 时跳过所有自动收尾, 仅手动 `/api/v1/source/detection/per-item-control settle` 或 `force_start` 才动周期状态, 给师傅手动按按钮的现场用。
- [FEAT-003] **box 尺寸过滤 (post-YOLO 守门)** — 新增 `_passes_box_size_limit`, 按 `steps_config[i].per_item.box_max_width / box_max_height` (归一化坐标) 过滤超大检测框 (整个料盒 / 操作员手臂误识别)。
- [FEAT-004] **per_item 检测短路放行** — `_get_confirmed_detections` 在 logic_mode == per_item 时直接放行, 跟 synthetic 模式同等待遇, 治 per_item 首次检出永远凑不齐 expected_count。
- [FEAT-005] **SOP 卡片"图永不空"** — Monitor 步骤缩略图按 label 跨周期继承上一周期同 label 的最后一帧, 治新周期开始那一瞬间灰白闪一下的视觉跳变。
- [BUG-001] **conftest.py 漏 import auth_models** — v3.10.0 用户系统引入的 users 表没在 conftest 显式 import, `Base.metadata.create_all` 解析 ForeignKey 时炸 → 全套测试无法跑。补一行 import 修复。
- [BUG-002] **test_pt_ct_modes_exposure MagicMock 自动属性问题** — MagicMock 默认对未显式设属性返新实例, 让路由 `_compute_dur is not None` 误为真, `round(MagicMock, 2)` 返 dict → 测试断言炸。fixture 显式设 None 走 wall-clock fallback。
- [BUG-003] **test_periodic_actions_v352 字段名漂移** — 测试 fixture 给 `trigger_labels`, 生产代码读 `trigger_step_labels` (没 _step 前缀的不读) → rule 解析后空集合 → 旁路账本永远空。fixture 字段名对齐生产代码。
- [BUG-004] **scanner_bypass 集成测试没跟上模板英文化** — v3.7.3 按客户固定英文要求把内置扫码器旁路模板从 "合格/不合格" 改成 "Pass/Fail", 集成测试没跟上。5 个断言批改 Pass/Fail + 第 4 行只断言时长数值。
- [BUG-005] **测试环境缺 jsonschema 依赖** — plugin_system 67 个细粒度测试因 fallback 模式集体降级。`pip install jsonschema` 后 81/81 全 PASS。
- [TEST-001] **新增 tests/test_per_item_v310_features.py** — 7 个 synthetic 剧本驱动的端到端测试, 覆盖 per-item-control API + require_exact_count + disable_auto_settle + force_start + box_max + SOP 字段契约。
- [TEST-002] **新增 tests/uat/uat_v3102_merge_verify.py** — 21 步 Playwright UAT (headless=False + 视频录制 + 关键步骤截图), Phase A API 契约 + Phase B 前端 UI + Phase C 清理。

Skill 更新: `debug-per-item` 补 v3.12 章节, `run-tests` 补 jsonschema 缺依赖 / 测试 fixture 字段漂移踩坑。

已知问题 (暂不修): 9 个测试串污染 (单跑全绿, 全套批跑 fail), 根因 MESHook / ChannelManager module-level 单例没清, 修需给 conftest 加 autouse reset fixture, 风险大留作 v3.12.x 整理。

---

## v3.10.2 (2026-05-26)

> **紧急修复版**：v3.10.1 / v3.10.0 / 之前所有版本存在 **machineId 多机撞 ID 漏洞**（已确认现场出现 7-8 台机器同 ID）。本版重做硬件指纹采集逻辑，**不影响老客户在用机器的 license**，但新装机会拿到唯一 ID。详细 changelog 见 `docs/changelog/v3.10.2_2026-05-26.md`。

- [BUG-001] **修复 machineId 多机撞 ID 漏洞** — 老算法只用 `wmic baseboard get product`（主板型号不是序列号，同型号工控机批量出货时值完全一样）+ `wmic csproduct get uuid`（厂家常忘刷 BIOS UUID）+ `os.cpus()[0].model`（同款 CPU 必相同）。雪上加霜 Win11 24H2 起 wmic 被 Microsoft 默认移除，新机器上前两个调用直接返回空，最后只剩 CPU 型号一个来源，同款 CPU 必撞 ID。`execSafe` 失败兜底返回空 + `parts.filter(Boolean)` 过滤掉，没任何报错日志，所以一直没人发现。重写 `getStableFingerprint` 为 5 强标识源 + 2 弱标识源（PowerShell `Get-CimInstance` 优先 + wmic 兜底 + 黑名单过滤占位值 + 强度守门 + 老 cache 兼容）。
- [FEAT-001] **现场 machineId 诊断脚本** — 新增 `electron/scripts/diagnose-machine-id.bat`，客户机现场双击就能跑，输出每个硬件源的返回值 + 哪些被黑名单拒收 + 最终 machineId + 强标识源数量，自动写报告文件方便发邮件。
- [FEAT-002] **machineId 指纹诊断 IPC** — 新增 `get-machine-id-report` IPC，前端可读 `electronAPI.getMachineIdReport()`，后续可在"关于"/"设置"页加诊断面板。

现场处置：
- **老机器（已激活）零影响**：升级 v3.10.2 后 `machine_id.txt` 缓存还在，沿用老 ID，老 license 继续有效。
- **现场已撞 ID 的 7-8 台机器需要重发 license**：远程让客户删 `%APPDATA%\tianjun-ai-vision\{machine_id.txt, hw_verify.txt, license.lic}` 三个文件 → 重启软件 → 拿到新 machineId → 用 `generate_license.py` 重发 license。
- **新装机**：自动用新算法，强标识源 ≥ 1 即可生成稳定唯一 ID。

Skill 更新：`debug-operator-license` 补 v3.10.2 章节（machineId 撞 ID 历史 bug、新指纹算法、现场处置指引、诊断脚本用法）。

---

## v3.10.1 (2026-05-26)

> v3.10.0 用户系统主线之后的"客户机视觉行为"补丁版。无破坏性改动，老项目 JSON/DB/接口全部兼容。详细 changelog 见 `docs/changelog/v3.10.1_2026-05-26.md`。

- [BUG-001] **修复 PT 多段策略"仅首段"显示 0.0s / "--"** — `_supplement_step_durations` 用 `duration>=0` 放过 0.0 段, 污染 `step_cycle_segments[label]` 历史, `first_only=segments[0]=0.0` 触发前端 `--`。`_accumulate_step_pt` 入口加 `<=0.005` 零段守门, 一处出口挡掉 5 个调用点的兜底污染。`sum` / `max` 不受影响 (加 0 / 最大值跟 0 无关), 只有 first_only 暴露此 bug。
- [BUG-002] **修复严格顺序模式 PT 负 `interval`** — 新增 `_resolve_step_pt_anchor` 把 PT 起点夹到 `max(raw_start, 上一步完成时刻)`, 帧号版 `_resolve_step_pt_anchor_frame_pos` 跟 B 方案 v2 同口径。
- [BUG-003] **修复"防重复结算"开关 UI 打开但不落库** — `saveProject` 漏写 `settle_dedup`, UI 开关一直空转, MES 重推 / 计数翻倍。前端补字段持久化, 后端重写冷却窗口模式覆盖所有方向 (OK→NG / 同节拍重发 / 自定义事件)。
- [FEAT-001] **PT/CT 显示口径重做 — B 方案 v2 (视频源帧号锚)** — 视频源用 `(last_fr - start_fr) / 视频原 fps` 算秒, 客户机解码速度 / 视频快进慢放完全解耦。非视频源 fallback 回 wall-clock, 行为完全不变。所有 `step_start_time/last_seen/cycle_start_time` 赋值点同步镜像写帧号字段 (state_init / session_lifecycle / settlement / sequential / step_stats / per_item / tracking 全覆盖)。
- [FEAT-002] **PT 多段合并策略三档可切 (sum / max / first_only)** — 后端 `_accumulate_step_pt` 统一出口, 同时维护 `step_cycle_durations` (sum 累加, 老接口语义不变) 与 `step_cycle_segments` (完整分段列表)。前端 `Settings` 加三档下拉, `Monitor.formatStepPT` 在 sum+current 时根据策略对 segments 现算。默认 `sum` 保持客户体验。
- [FEAT-003] **防重复结算 → 时间窗口冷却模式** — `_trigger_event` 入口加冷却窗口, 任意事件触发后在 `settle_dedup_window_seconds` (默认 2.0s) 内所有事件被吃, 跟 `ng_cycle_protect_seconds` 共存互补。
- [FEAT-004] **Splash 行为可配 + 默认关闭** — `workstation_config.splash.enabled` 默认 `false`, Electron 启动早期读到关 → 走无 splash 路径直接建主窗 + 后台预热后端, ready 即 show。`true` 走老 splash 完整流程 (摄像头 / 手动跳过 / 闲置超时不动)。
- [FEAT-005] **窗口模式 — 默认窗口模式 + 全屏热切 + 最小化按钮** — `workstation_config.window.fullscreen` 默认 `false` (1600×900 居中带 Windows 原生标题栏), 客户能正常最小化 / 切别的程序。新增 `window:minimize` / `window:set-fullscreen` IPC, `Settings` 加窗口模式卡片。

Skill 更新：`modify-source` / `debug-source` / `debug-electron` 三个 skill 补本版新事实和踩坑。

已知问题（暂不修，待 v3.10.2 / v3.11.0）：
- YOLO 把"正面涂黑"识别成 4-6 秒连续段（模型边界判定问题，10 个周期历史比例 1.6×~5.1×）— 真测确认软件没吞段，单帧只输出一个标签。三种显示策略都救不了，要调高 confidence / 配 max_duration / 加负样本重训。视频回放问题，现场实际可接受，暂不动。
- `step_start_frame_pos / step_last_frame_pos` 跨周期不显式 clear — 实际无害（每个赋值点都同步镜像写, 新周期被新值覆盖）, 留作下次整理。

## v3.10.0 (2026-05-25)

> 上一发布版本 **v3.9.1**（含 v3.9.1a 两个 hotfix）。本版**完整吸收** v3.9.1 release + v3.9.1a hotfix（HOTFIX-001 + HOTFIX-002）+ splash idle 自动跳过特性，并新增用户系统主线。详细 changelog 见 `docs/changelog/v3.10.0_2026-05-25.md`。

- [FEAT-001] **用户系统数据模型 + 后端鉴权内核**：5 张新表（users / roles / user_roles / session_tokens / api_keys）+ 4 个 core 模块（auth / auth_deps / api_key / permissions）+ 3 个内置角色（admin / engineer / operator）+ bcrypt 密码 + token 持久化。**总开关默认关闭**，老客户体验零差异。
- [FEAT-002] **后端用户系统 API + 前端登录页 + 路由守卫**：`/auth/login` / `/logout` / `/me` / `/status` / `/enable-auth` / `/disable-auth` / `/permissions/catalog` + users CRUD + roles CRUD；前端 `useAuthStore` + 登录页 + `router.beforeEach` 权限过滤。
- [FEAT-003] **端点级 require_perm R1-R4**：约 25 个 API 模块、~160 个端点 4 轮渐进式标记；R4 单独处理 M2M 端点走 API Key 体系。
- [FEAT-004] **M2M API Key 系统**：`api_keys` 表 + `tj_<scope>_<32_random>` 格式 + `require_api_key(scope)` 依赖 + 一次性查看；cluster.report / heartbeat / license-cache / mes.receive 4 个 M2M 端点改造。
- [FEAT-005] **Settings AuthPanel 综合 UI**：启用对话框 / 用户 CRUD / 角色权限编辑器（分组权限树）/ API Key 管理（创建后明文展示一次）/ 当前操作员引导。
- [FEAT-006] **Navbar / BottomBar 身份显示统一切换**：鉴权关 → 老 inspectorName / 鉴权开+登录 → display_name + 角色徽章 / 鉴权开+未登录 → 引导登录；登录/登出按钮独立位（运行中禁用登出）。
- [BREAK-001] **删除旧 operators 系统**：5 阶段 strangle 模式（隐藏 UI → 清污染 → 410 端点 → 用户归属 → DROP TABLE），有 `IF EXISTS` + try/except 守门，老客户升级零数据丢失。

合并冲突解决（feat/user-system 回流 main）：
- `backend/api/source_routes.py`：v3.9.1 加的 `/detection/ack-event` 端点补 `require_perm("monitor.detection.control")`（operator/engineer/admin 都能确认，跟开始/停止/待机同级）
- `backend/api/channel_manager.py`：v3.9.1 加的 `PUT /splash-camera` 补 `require_perm("settings.edit")`（跟同模块的 `/mode` `/channel-config` 对齐）；GET 保持公开。

真测验证：
- 后端端点探针 10/10、前端 headless UAT 26/27、可见浏览器 + 录像 UAT 23/27（剩 4 NG 都是测试脚本细节，产品本身全通）
- 关键链路：enable-auth → admin 登录 → AuthPanel 4 子卡 → 创建 operator → operator 调写端点 4/4 全 403（disable-auth / reset-periodic / PUT splash-camera）+ ack-event 正确放行 → 复原干净
- 证据：`/tmp/v3.10.0_uat/visible/`（2 个 webm 视频 + 20 张截图）

## v3.9.1a hotfix (2026-05-25)
- [HOTFIX-001] **Splash 跳过手势路径卡死 EXPLOSION 末态** — 客户工厂触摸屏机器启动 v3.9.1 后触摸屏幕想跳过启动动画，splash 跑完塌缩/爆炸卡在满屏粒子辐射星空 + 中央橙黄光球，永远进不去主程序。根因：v3.9.1 加的鼠标/触摸点击跳过和 v3.8.2 加的 ESC 跳过都走同一个 `skipToReady` 函数，但只触发了视觉阶段切换，没把"虚拟后端 ready 信号"`backendReady` 打开 → 爆炸跑完没人打开标志 → 永远卡 EXPLOSION 末帧。修：`skipToReady` 补一行 `backendReady = true`，三条跳过路径（ESC/鼠标/触摸）统一修好。
- [HOTFIX-002] **Splash 进度条 99% 收敛 + 完成兜底**（v3.9.1a 内迭代补强）— HOTFIX-001 回归测试发现：即使进了 READY 阶段，进度条也可能因为浮点收敛 edge case（`displayPct` 卡 99.95 floor 后显示 99）+ rAF 节流卡 99% 永远不到 100%，导致 `splashAPI.notifySplashFinished()` 永远不触发，splash 永远关不掉。修两层兜底：(A) progress tick 加 `targetPct=100 && displayPct>=99 && <100` 时强制 snap 100；(B) 抽 `forceSplashFinish` 统一完成入口 + `splashFinished` flag 防重入，在 `onBackendReady` 后加 8 秒 hard deadline timer——后端真 ready 后最多再等 8 秒，进度条还没自然冲到 100% 就强制走 IPC finish 路径。**注意必须在 backendReadyFromIPC=true 之后才开始计时**（不是 splash 启动就计时），否则后端模型加载慢的工控机会过早被强制关 splash 进入"后端没就位"状态。
- 补丁发布产物（HOTFIX-001 + HOTFIX-002 合并发布）：中转仓 v3.9.1 Release 附件 `patch_v3.9.1a.bat` + `app.asar`（20 MB）。客户操作（推荐）：把两个文件直接拖进软件安装根目录（能看到 `resources\` 子目录的那一层），双击 bat → "Drop-in mode" 自动打完 → 重启软件。

## v3.9.1 (2026-05-25)
- [FEAT-001] **事件手动确认** — 事件配置加 `require_ack` / `ack_timeout_sec` / `ack_resets_periodic`，触发后冻结主推流 + Monitor 全屏 overlay 等工人按确认；周期性强制动作统一接入同一套，`ack_resets_periodic` 解决"未压墨提示框关不掉一直弹"。
- [FEAT-002] **PT 计算口径可配 span / visible 累计可见时长** — 后端永远算两份 PT 字段，前端 Settings 下拉切换显示口径；解决"标签从早到晚都被识别 → 老的跨度 PT 算成 18 秒、实际操作只用 2 秒"问题。
- [FEAT-003] **结算后强制保留显示** — `resultHoldEnabled` / `resultHoldSeconds`（默认关），周期结算后冻结 OK + PT 数字 N 秒不被下一周期立刻覆盖，给产线工人多看一会儿。
- [FEAT-004] **启动动画手势相机配置** — `workstation_config.json` 顶层加 `splash` 段（auto/specific/disabled 三档），Settings 加新卡片自选 USB 摄像头。客户工厂 Todesk / 向日葵等虚拟相机不再被 splash 误用；工业相机方案下手势相机跟生产线相机解耦。
- [FEAT-005] **Splash 鼠标点击跳过手势** — 抽 `skipToReady(reason)` 统一入口，ESC + window click 共用一条路径；排除已有 UI 按钮避免误触发。工厂工控机没键盘也能跳过手势。
- [BUG-001] **严格顺序 PT 起点 anchor 修正** — 修复 CT < PT 之和、步骤 interval 负值。新增 `_resolve_step_pt_anchor` 助手函数，严格顺序步骤的 PT 起点强制 `max(start, 上一步完成时刻)`，5 个 PT 写入点全部接入（含 step_inflight_durations 同 anchor，避免步骤完成时 PT 大数字跳到小数字闪一下）。
- [BUG-002] **步骤状态机锁定** — 修复"已检测 ↔ 待检测"反复切换 + PT 涨一点的视觉闪烁。状态机优先级调整：步骤一旦 join 周期就锁 `'completed'`，画面残留识别不再回退到 `'active'`。
- [BUG-003] **删除 cycleResetTimer 1.2s 强制清屏** — 修复周期切换时"翻转 待检测 PT 10.6s OK"这种 status/PT 不一致快照。清屏路径统一由 `_newCycleStarted` 驱动，一次清空所有字段。
- [CONFIG-001] 版本号 `3.9.0 → 3.9.1`（patch: 没新增 logic_mode，纯粹是对现有功能的增强 + bug 修复）。

## v3.9.0 (2026-05-23)
- [FEAT-001] **新增结算模式 last_first（末步结算 + 首步开周期）** — 客户产线 ABCD 4 步流水末步即结算 + 首步即开新周期 + 跳 D 重做 A 走 R3 fallback NG。`source_settlement_mixin.py` 新增 `_process_last_first_mode` 状态机（6 条规则 R1-R6），作为 `_update_step_stats` 前置过滤层，仅 settlement_mode=last_first 时生效。前后端双层互斥校验：与 strict_mode/cross_cycle/per_item/detection/tracking 等模式严格互斥；前端 Project 页选中后弹约束提示卡 + 自动取消 strict_order；后端 `_apply_pipeline_config` 兜底覆盖。隔离性证据：first_step 模式跑同剧本 R3 触发=0。
- [FEAT-002] **跨周期组（类二 cross_cycle）独立状态机** — 新增 `_process_cross_cycle_groups`，处理上下周期成员先后到达 + 屏蔽集合 + 等待超时 7 种状态迁移。和类一同帧组的 `_process_simultaneous_groups` 完全分离，box 时序 D 余像 + A 新周期场景不再窜逻辑。
- [FEAT-003] **per_item 逐件覆盖 5 项补丁（v3.9 增强）** — (1) expected_count 固定数量 + lock_lookahead 周期内补锁定 (2) 锁定模式下 cleanup_stale_items 不清未覆盖件 (3) 周期/空闲超时强制结算 (4) settle_after_all_done_sec 立即 OK (5) item_label 数组多标签 OR。
- [BUG-001] **last_first 模式下基于消失的旧结算路径误触发** — `_check_events` 对所有模式都跑'末步消失结算'，导致 R1 已结算后 D 消失再结算一次。修：first_step / last_first 两种基于出现的模式跳过基于消失路径。
- [TEST-001] 新增测试矩阵：24 个 last_first 单测 + cross_cycle BDD + per_item v3.9 单测 + 2 条 synthetic E2E + 2 份 UAT 脚本。
- [UAT-001] last_first 可见浏览器 UAT 4 阶段 31/31 通过（API 契约 14 + 可见浏览器 9 + UI CRUD 6 + 先红后绿 2），三件套：1.3MB webm 视频 + 6 张截图 + 31 行 JSON 日志。
- [SKILL-001] 更新 debug-source / modify-project-config 两个 skill：新增 last_first 章节 + settlement_mode 枚举 + 互斥踩坑条目。
- [CONFIG-001] 版本号 `3.8.2 → 3.9.0`（minor: 三个新功能模块）。

## v3.8.2 (2026-05-22)
- [FEAT-382-SPLASH-CYBER] **全新赛博 splash 启动界面（取代旧 400×300 小弹窗）** — 新增 `electron/splash/` 目录: Three.js + WebGL 粒子球 + UnrealBloomPass + 5 阶段状态机（IDLE→HOVER→COLLAPSE→EXPLOSION→READY）+ 8 道全屏代码瀑布 + 四角 cyber HUD（SYS/VER/UID/CHN/GPU/MEM/MODEL/FPS/TIME）+ 底部 BOOT 序列叙事 + 程序化 Web Audio 音效。资源全本地化（Three.js / MediaPipe / 6 个 ttf 字体），离线工厂可跑，安装包多 ~19 MB。
- [FEAT-382-SPLASH-PROGRESS] **进度条由真实后端 stdout 行数驱动（不再假 6 秒模拟）** — 主进程监听 `BackendManager` 的 stdout/stderr/ready 事件通过 IPC `splash:backend-log` / `splash:backend-ready` 转发给 splash；每行日志推进 +0.5% 封顶 95%，后端 ready 强制补 100%；满 100% 后 IPC `splash:finished` 关 splash + 显示主窗。沙盒模式（浏览器直开）退回 5 秒假进度兼容调试。
- [FEAT-382-SPLASH-HAND] **接入工位 1 摄像头做手势触发（MediaPipe Hands）** — Splash 启动时读 `workstation_config.json.channels."1".usb_device_id`，`getUserMedia({deviceId: {ideal}})` 精确锁同一台 USB 摄像头；握拳 → 粒子塌缩，张开 → 爆炸 → logo 揭示。MediaPipe `locateFile` 改本地；摄像头不可用走 `scheduleAutoplayFallback()` 等后端 ready 自动播完；ESC 随时跳过手势。
- [FEAT-382-FULLSCREEN-FRAMELESS] **主窗与 splash 双双全屏 + 杀菜单栏 + 杀标题栏（工业部署形态）** — `electron/main.js` 顶部 `Menu.setApplicationMenu(null)` 全局禁菜单（DevTools 仍可 Ctrl+Shift+I）；主窗 `fullscreen + frame:false + backgroundColor #02060c`（跟 splash 同色过场无闪烁），`show:false` 后台预热，`splash:finished` 才显示；splash 同样 fullscreen + frame:false + alwaysOnTop。
- [FEAT-382-NAVBAR-IPC-QUIT] **Navbar "退出" 按钮改走 IPC 优雅关机** — 全屏无边框后没有窗口 × 按钮，"退出"必须能触发 8 步关机。`electron/preload.js` 暴露 `electronAPI.gracefulQuit()` → `ipcMain.handle('app:graceful-quit')` → `mainWindow.close` 触发已注册的 `startGracefulShutdown`；Navbar 检测 electronAPI 存在走 IPC 否则退回浏览器调试模式 `router.push('/login')`。
- [FEAT-382-USB-DEVICE-PERSIST] **USB 摄像头 deviceId 持久化（思路 B：前端 enumerateDevices）** — 后端 `channel_manager.py` 新增 `PUT /api/v1/workstations/{channel_id}/usb-device` + `UsbDeviceBindRequest` schema (`device_id` / `device_label`)，`save_channel_source(merge=True)` 仅追加这两个字段。前端 `Source/index.vue` `tryPersistUsbDeviceId` helper：USB 启动成功后 `enumerateDevices()` 拿浏览器 `MediaDeviceInfo.deviceId`，按 OpenCV index 顺序匹配 videoinput，PUT 回写后端；失败静默不阻塞主流程。多工位 + 单工位两条路径都加。
- [FEAT-380-MEDIAPIPE-CONFIG] **MediaPipe 手部骨架"完美渲染"配置化（v3.8.0 起本次合并发布）** — 逆向定罪友商完美骨架密码: `mp.solutions.hands` 整帧调用 + `model_complexity=1` + `min_detection_confidence=0.5`。300 帧实测米色手套平视场景命中率 12.3% → 47.7%（×3.9），工业黑手套俯视场景双方均 0%（MediaPipe 训练分布外死区）。`source.py` 加 `mediapipe_model_complexity` / `mediapipe_track_confidence` 默认 complexity=0 保守不动老客户；`source_routes.py` `StreamConfigRequest` Optional 字段 default=None 防误重置；`source_mediapipe.py` baseline 初始化读 host 字段（顺手修 baseline 分支 path/kind 没落到 `_hand_detector_path_loaded` 导致 apply_overlay 每帧误 reload 的 bug）；Settings UI 加一键预设按钮（省 CPU / 高精度）+ 高级折叠 + 工业死区提示。
- [FEAT-380-TWO-STAGE-PIPELINE] **二段管线集成框架 + Settings 工业模型配置面板（v3.8.0 起本次合并发布）** — 后端 `source_mediapipe.py` 支持配 `hand_detector_path` 走 "YOLO bbox → ROI → MediaPipe HandLandmarker (Tasks API)" 二段流程，加载失败优雅 fallback baseline。`source_routes.py` 加 7 个新字段（path/kind/conf/iou/imgsz/class/roi_pad/task）+ `_compute_two_stage_status()` 返回 5 态（baseline / path_invalid / pending / active / load_failed）。`useSystemStore.performance` 加 6 个字段镜像。`Settings/index.vue` MediaPipe 卡片新增"工业专用手部模型"折叠面板：状态徽章实时刷新 + 路径输入 + v8/v5 单选 + 4 个高级参数（conf/iou/imgsz/ROI pad）+ 横幅说明。
- [DOCS-382-TRAIN-HAND-DETECTOR-SKILL] **新增 `train-hand-detector` skill** — 落地 v3.8.0 二段管线配套训练 SOP：现场录像策略（5 个不可省略覆盖维度）→ 抽帧 (ffmpeg fps=2) → 标注 (X-AnyLabeling + SAM) → 训练 (yolo train + yolov11s, epochs=80, RTX 3060 单卡 2-3h) → 验证 (`tools/diag_hand_two_stage.py`，黑手套场景 0% → 60%+) → 部署 → 持续维护。AGENTS.md 第四节登记触发关键词。
- [CONFIG-382-001] 版本号 `3.8.1 → 3.8.2`（`electron/package.json`）；`build.files` 把 `splash.html` 替换为 `splash/**`（带 vendor 全套约 19 MB）。
- **历史长期红灯 CI 仍未修** — `Virtual Functional Tests` (cryptography) 与 `DB Matrix (SQLite + PostgreSQL)` (PG SUM(boolean) dialect) 自 v3.7.4 起持续红灯，SE9 分支已修待 cherry-pick。

## v3.8.1 (2026-05-22)
- [BUG-001] **严格+单次接受 PT 位置守门** — 仅拦多余位置的新出现，修复翻转 PT 虚高/0.0s。
- [BUG-002] **TRT 预热失败自动回退 PyTorch** — engine 版本不兼容时不再静默 0 检测。
- [BUG-003] **Monitor 中间步闪动 + OK/PT 不同步** — 已走过步骤锁定、结算步 OK 回填前面、PT=0 显示 `--`。
- [FEAT-004] **步骤统计表格列可单独隐藏** — 设置页检测中心显示下可逐列开关（序号/步骤/状态/PT/结果），默认全开。
- [CI-001] **Actions 储空间清理 + build 磁盘 >70% 深度清理** — 清旧 caches/artifacts；CI 加阈值告警与 extra cleanup。

## v3.8.0 (2026-05-21)
- [FEAT-001] **定时导出 — 按 cron 周期性自动出报表**：新建 `ExportScheduledRule` + APScheduler 后台调度，7 种数据窗口预设（含早晚班/跨日截断，跟手动按钮零差异）、5 种输出格式、全局默认目录 + 单规则覆盖、运行日志回查；新组件 `ScheduledRulesDialog`（cron 简化模式 + 高级 cron + 下次触发预览 + 端到端表单）。
- [FEAT-002] **数据页 4 个快捷按钮 + 定时导出支持多格式 (csv/txt/xlsx/docx/pdf)**：抽出 `build_csv_string`，新模块 `export_scheduled_writers` 集中实现；DOCX 走 XML fast-path 解决 O(n²) 性能（386 行 >2min → <1s）；PDF 用 reportlab 内建 STSong-Light CID 字体免外部依赖；前端加输出格式下拉。
- [FEAT-003] **自定义计数器显示开关**（默认隐藏）：`Project.counters_config[i].show_in_monitor`，Project 页加勾选 + tooltip，Monitor 统计板块按字段过滤；系统三计数器恒显示。
- [FEAT-004] **周期性强制动作 — 多触发频率 / 时间触发 / 自定义事件 / 立即清零**：`notify_mode` (once/every_cycle/interval) + `notify_interval_sec` + `notify_event`，强制动作命中后立即清零并停止间隔通知。
- [BUG-001] **PT/CT 显示时机 + 周期切换视觉残留修复**：后端透出 `current_cycle_id` 让前端做边界检测；累计字典清空挪到 `start_cycle` 而非 `end_cycle`；新增 `_flush_active_steps_pt` 结算前主动写入仍在画面的 PT；前端 status 放宽三态，cycleResult 继续严格等 authoritative。
- [BUG-002] **检测框越界 — 三层 clip 防御**：geometry / inference_loop / drawer 三处分别加 clip 兜底，避免文字外溢黑屏。
- [BUG-003] **海康相机 NameError 修复**：v3.7.x mixin 拆分回归，补全 `MV_CC_DEVICE_INFO_LIST` 等 SDK 常量 import。
- [BUG-004] **扫码器列表为空时不再报"未绑码"**：settlement mixin 加守门，没装扫码器时静默不报警。
- [BUG-005] **顺序模式新一轮开始时不结算上一轮修复**：A-B-C 没 D 紧跟下一轮 A，新增"结算步骤即将到来时新一轮第一步出现 → 结算上一轮 NG + 启动新一轮"逻辑（按钮配置默认关）。
- [BUG-006] **检测停止/重启后画面残留修复**：stop 路径加 `clear_filters()` + `last_annotated_frame = None`。
- [CONFIG-001] 版本号 3.7.6 → 3.8.0 (`electron/package.json` + `electron/splash.html`)。
- [DEPS-001] `backend/requirements.txt` 新增 `APScheduler>=3.11.0` + `croniter>=2.0.0` + `tzlocal>=5.0.0`。

## v3.7.6 (2026-05-18)
- [BUG-376-SEQ-SUPPLEMENT-PT] **顺序模式 cycle 末尾步骤"OK 已出 PT 仍 --"修复** — 客户「步骤详情」表里某一步状态=已检测、结果=OK，但 PT/s 列一直是 `--`，最典型出现在 cycle 末尾那一步（A→B→C→D 的 D）。根因：顺序模式 / 自定义顺序模式 cycle 判定结尾的"补计"路径只写 `step_counts` + `step_durations` + `step_durations_history`，**漏写 `step_cycle_durations`**；前端 `step_counts +1` → `actualPosByLabel` 含该 label → 标 OK，但 PT 合并档（`cycle_sum_*` / `avg_cycle_sum_*` / `last_cycle_sum_*`）都没该 label → PT 列永远 `--`。修复：两处补计路径补齐 `step_cycle_durations` 累加，跟主路径 `source_step_stats_mixin.py:298-303` 对齐。
- [BUG-376-PT-NO-RESET] **PT 时间周期结束不归零修复**（"当前周期内 + 最后一次"组合） — 客户切到该组合时周期结束后 PT 列停在上一周期值。根因：双端不对齐 — 后端 `step_durations` 字段只在 `reset_stats()` 清，cycle 结束时不清；前端 `Monitor/index.vue:3663` 用 `Object.assign` merge 而非整体替换，即便后端清了前端字典也清不掉。修复：后端 `end_cycle` / `_discard_empty_cycle` finally 块追加 `self.step_durations = {}`（位置在主体兜底引用之后安全）；前端 `stepDurations` 改成整体替换 `stepDurations.value = data.step_durations || {}`。
- [FEAT-376-STEP-TABLE-UI] **步骤详情表"列顺序调整 + 结果列硬性守门"** — 客户要求"先出时间再显示 OK/NG"。两步走：① 列顺序 `No / 步骤 / **PT/s** / 状态 / 结果`，PT 前移到状态前；② 结果列加 `v-if` 守门 — 非跟踪模式下 PT 必须有真实数值才显示 OK/NG（堵 accept_once / 跟踪类场景中间窗口的视觉错位），跟踪模式（`isTrackingMode`）跳过守门保持原 `counted>0→OK` 语义。
- [FEAT-376-PT-DEFAULT-CURRENT] **PT 显示口径默认值从「平均」改为「当前周期内」** — 客户希望出厂默认就归零。`useSystemStore.js` 默认 `ptMode: 'avg' → 'current'`（配合默认 `ptAggregate='sum'` 组合为「当前周期内 + 合并」，数据源 `cycleSumStepDurations`，cycle 结束自动归零）；`Monitor/index.vue: formatStepPT` 兜底默认对齐。已手动调过设置的老客户 localStorage 选择保留不动。
- [CONFIG-376-001] 版本号 3.7.5 → 3.7.6 (`electron/package.json` + `electron/splash.html`).
- **历史长期红灯 CI 仍未修** — `Virtual Functional Tests` (cryptography 依赖缺失) 和 `DB Matrix (SQLite + PostgreSQL)` (PG SUM(boolean) dialect 不兼容) 自 v3.7.4 起持续红灯，SE9 分支已修但未 cherry-pick 到 main，留待后续版本。
- **SE9 (Sophon BM1688 ARM64) 适配未随版发布** — 相关代码隔离在 `feature/se9-arm64` 分支，Windows 客户不受影响。

## v3.7.5 (2026-05-18)
- [BUG-375-PERIODIC-RESET] **顺序模式下做了周期性强制动作仍然不清零修复（FIX-381 副作用）** — 客户配主流程顺序 A-B-C-D 同时配每 20 轮/每 600 秒做一次保养动作 E，做完 E 之后周期强制动作的未完成轮数/时间继续累加不清零，保养报警一直挂. 根因: v3.7.2 (FIX-381) 在 `process_step_detection` 顺序模式分支拦截了 `expected_seq` 外的步骤直接 return，但保养类 trigger_step 本来就在主序列外 → E 永远进不了 `cycle.step_sequence` → `_check_periodic_actions` 永远看不见 → 永远不清零. 修复"小账本"方案: 新增 `_periodic_triggers_observed` 旁路观察账本独立于 cycle.step_sequence, `_observe_periodic_trigger` 在 `process_step_detection` 早于 FIX-381 拦截调一次记账, `_check_periodic_actions` 入口合并 cycle_steps + 旁路账本算 did_trigger, 判定后清空, `reset_stats` / `_discard_empty_cycle` 同步清账本防串轮. 完全向后兼容老项目零开销. 新增 8 个测试覆盖 observe 记账/过滤/去重/核心场景/清空/全局重置/通道过滤/时间维度同步.
- [BUG-375-AUX-COLOR] **副模型检测框始终是默认色 客户在步骤列表配的颜色失效修复** — 客户用副模型识别错误放置（默认 `display_color = #f59e0b` 琥珀黄），在步骤列表"检测框颜色"列单独配了红色但 Monitor 仍然黄色，跟 tooltip 写"任何模式都生效"自相矛盾. 根因: v3.7.2 (FIX-381-B) 设计 `pickDetColor` 时把副模型 `display_color` 放最高位 → 客户对单个 label 配色的明确意图被忽视, 步骤 `box_color` 形同摆设. 修复: 翻转优先级 步骤级 `box_color` > 副模型 `display_color` > OK/NG 兜底; Project 页 tooltip 文案改清说明新行为. 副模型不配 `box_color` 时行为与 v3.7.4 一致不破坏现有项目视觉.
- [CONFIG-375-001] 版本号 3.7.4 → 3.7.5 (`electron/package.json` + `electron/splash.html`).
- **历史长期红灯 CI 仍未修** — `Virtual Functional Tests` (cryptography 依赖缺失) 和 `DB Matrix (SQLite + PostgreSQL)` (PG SUM(boolean) dialect 不兼容) 自 v3.7.4 起持续红灯, 跟 feature/plugin-system 引入相关. SE9 分支已修但未 cherry-pick 到 main, 留待 v3.7.6.
- **SE9 (Sophon BM1688 ARM64) 适配未随版发布** — 相关代码隔离在 `feature/se9-arm64` 分支, Windows 客户不受影响.

## v3.7.4 (2026-05-15)
- [FEAT-374-PERIODIC-TIME] **周期性强制动作支持按时间触发** — 客户产线生产间断（吃饭/换班/换工序）整个 cycle 不推进但 detection 仍在跑, 原按次数触发的强制保养动作不报警, 客户希望按时间也能报. 新增 `pipeline_config.periodic_actions[*].time_interval_seconds` 字段（=0 关闭，>0 启用, 与 `interval` 是 OR 关系）. 后端新增 `_check_periodic_actions_time_only` + `inference_loop` 每 5s throttle 调用, 完成动作时同步重置 counter + last_done_ts; 持久化 JSON 升级为 `{counters, last_done_ts}` 结构兼容老格式; `get_periodic_actions_status` 返回新增 `time_state/count_state/state` 取更严重那个; 前端 Project 页加超时秒数输入, Monitor 页加 `⏱ Xs/Ys` 时间维度展示与 reset 同步; 完全向后兼容老项目. 11/11 单元测试 + 11/11 UAT(可见浏览器) PASS, 红绿双向验证通过.
- [FIX-374-START-FRONTEND] **`./start_frontend.sh` 在 Node 18 下报 `crypto.hash is not a function` 修复** — Vite 7.x 要求 Node ≥ v20.19, 但 Ubuntu 24.04 默认 node v18.19.1. 脚本加 `ensure_node_version`: 探测当前 node 主版本 < 20 时自动 `nvm use` 已装最高 v20/22/24, 用 `--delete-prefix` 兼容 .npmrc 干扰, 切换后用 `node -v` 重校验是否真生效. 客户机走 Electron 打包 dist 与本修复无关.
- [TEST-374-001] 归档 `tests/uat/uat_20260515_periodic_time_trigger.py` 作为长期回归护栏（不带 test_ 前缀避免 pytest 自动收集）.
- [SKILL-374-001] `modify-project-config` 追加 `time_interval_seconds` 字段说明 + 双维度 OR 关系语义.
- [CONFIG-374-001] 版本号 3.7.3 → 3.7.4 (`electron/package.json` + `electron/splash.html`).
- **SE9 (Sophon BM1688 ARM64) 适配未随版发布** — 相关代码隔离在 `feature/se9-arm64` 分支, sophon-sail 集成 / bmodel 转换 / 装机脚本仍在调试中, Windows 客户不受影响.

## v3.7.3 (2026-05-14)
- [FEAT-373-AUX-CONVERT] **副模型支持 TensorRT/ONNX 转换全链路** — Project 页副模型卡片加"切换格式"按钮 + 格式 tag + `model_format` 字段进 pipeline_config.models[i]; Monitor 单/多通道分支 `_resolveModelPath` 都改读 `e.model_format||'pytorch_fp32'`, 老项目兜底. 副模型现可与主模型同速跑 TRT FP16.
- [FEAT-373-PROJECT-UX] **Project 页副模型参数 UX 简化** — `conf`/`iou`/`priority`/`FP16` 四列并排改成"置信度 + 高级参数 ▾"折叠; 4 个参数加详细中文 tooltip (人话, 不是术语); 非 `pytorch_fp32` 格式时 FP16 自动 disable + 解释 tooltip.
- [FEAT-373-PERIODIC-RESET] **周期性强制动作支持单独重置** — 新增 `PeriodicActionsMixin.reset_periodic_counter(rule_id=None)` + `POST /api/v1/source/detection/reset-periodic`, 允许检测运行中调用, 只动 counter/last_overdue_count/run_on_start_pending 不动产量统计; 前端 `detection.js` 加 `resetPeriodicAction`; 测试 `test_periodic_actions_v352.py` 锁定行为.
- [FEAT-373-SCANNER-BYPASS-CUSTOMIZE] **预设模板"扫码器旁路三行 TXT"客户文案微调** — L3 `合格/不合格` → `Pass/Fail`; L4 去掉 `step.label`, 按 `step_order` 顺序只输出耗时 ` | ` 分隔; description 同步更新 Pass 完整 5 列示例 + Fail 截断 3 列示例 + Fail 不补 0 占位说明 + app.version env 注入修复说明; `seed_builtin_templates` 启动按 `builtin_id` 刷新不动自建副本.
- [FIX-373-APP-VERSION] **客户机导出模板 `{{ app.version }}` 显示 "0.0.0" 修复** — 根因: `electron/package.json` 被打进 `resources/app.asar`, Python `open()` 完全读不到, fall back "0.0.0" 还被缓存. 修复: `electron/backend-manager.js` 顶部 `require('./package.json')`, spawn 后端时通过 env 注入 `TIANJUN_APP_VERSION` + 5 个辅助 env; `export_context._read_app_info` 改"env 优先 → fs 多候选兜底 → 默认值不缓存"三段式.
- [FIX-373-AUX-LOAD] **副模型配完后 Monitor 仍显示"未加载"修复** — 双重 bug: A `syncProjectConfig` 把 axios response 当 project 用 (真 project 在 `.data`), store 永远不更新 → `extraSlots=[]`; B 后端预加载主模型后 `isPaused=true`, 启动走"resume"分支跳过 `pipeline_config.models` 解析. 修 A 取 `resp?.data`; 修 B 检测到 `_hasExtraSlots` 强制 `apiStopDetection()` + 全启动路径 (release + load 主+副).
- [FIX-373-AUX-DELETE] **副模型删除后 step label 残留修复** — `removeExtraModel` 只 splice extra_models, 不动 `steps_config` / `sequence_order` / `detection_steps` / `custom_*` / `simultaneous_groups`. 修复: 加 ElMessageBox.confirm 列出影响 step, 确认后清各引用 + 删 step; `initProjectDefaults` 加孤儿步骤自动清理应对历史脏数据.
- [FIX-373-VIDEO-SEEK] **视频源拖动进度条后开始检测回到起点修复** — `start_detection` 无脑 `cv2.CAP_PROP_POS_FRAMES=0` 忽略用户 seek 位置. 修复: 只有 `video_ended` 才 reset; 否则读 capture 位置同步到 `video_current_frame`.
- [FIX-373-SETTLEMENT-UI] **"压墨"被误标为结算步骤修复** — custom 模式 + sequential 基底时 `isSettlementStep` 硬读 `sequence_order` 而非 `custom_sequence_order`. 修复: 加 `_activeSequenceOrder()` 助手按 logic_mode 选对应数组, 与后端行为对齐.
- [FIX-373-MODEL-CONVERT-STUCK] **模型转换僵死任务永远卡 'converting' 修复** — ultralytics/TRT C 扩展卡 GIL 时 `t.join(timeout=600)` 不一定按时返回, 子线程僵死. 加 `_reap_stale_conversions` 在所有查询入口做懒回收, 阈值 900s 落库改 failed.
- [FIX-373-ACTIVATE-PROJECT] **激活项目后阈值改动不生效修复** — `activate_project` 只重载模型不调 `mgr.set_project_config`, VSM 状态机还跑旧值. 修复: `_sync_project_config_to_channels` 同步配置到所有未绑定其它项目的 channel, 与启动期 `auto_load_active_project` fallback 行为同源.
- [FIX-373-SEQ-DUP-LABEL] **顺序模式重复 label 误判 NG (0% OK 率) 修复** — `sequence_order` 含重复元素 (A-B-C-B-D, B×2) 时, 旧 `unique_steps + zip` 截断 + `unique_steps.index()` 总返首次位置. 修复: `_check_sequential_completion` 改 Counter 区分多次出现; `_settle_sequential_cycle` / `_finalize_sequential_cycle` 用 multiset 相同 + 逐位比较.
- [FIX-373-GHOST-CYCLE] **结算后"鬼周期"启动修复** — 上一周期最后一步 settle 后, 模型对它仍连续识别 → ghost cycle (1 step) → 后续步骤被 strict_order 拦截. 加 `_post_settle_ignore_labels` 集合, 要求 label 必须先彻底 disappear 一次才能再 add-to-cycle.
- [FIX-373-ACCEPT-ONCE] **`accept_once` 阻挡周期内同 label N 次修复** — sequence_order 含重复 label (检查外观×2), 第 2 次被 accept_once 拦下不变绿. 修复: 按 `expected_seq.count(label)` 配额放行.
- [FIX-373-MJPEG-LEAK] **MJPEG 旧连接累积导致黑屏修复** — Chrome keep-alive 不立即关旧 socket, 旧 generator hold frame_lock. 修复: 每条 generator 分配 `connection_id`, 同 channel 后来者上位旧的主动 break.
- [CHORE-373-001] export_custom / export_realtime 行结束符 CRLF → LF (无逻辑变更).
- [CONFIG-373-001] 版本号 3.7.2 → 3.7.3 (`electron/package.json` + `electron/splash.html`).

## v3.7.2 (2026-05-12)
- [FEAT-372-SCANNER-BYPASS] **扫码器旁路 三行 TXT 完整路径** — 客户外部扫码器每次扫码生成 TXT (只一行序列号), 周期结束复制该 TXT 写入"序列号 / 空行 / 合格不合格 / 步骤时长 / 软件版本"五行结构, 输出到指定目录. 三种策略: `cycle_start_snapshot` (默认, 周期开始锁快照彻底防串号) / `mtime` (取 mtime 最新) / `mtime_stable` (`wait_stable_ms` 等稳定后再取); `max_age_sec` 默认 60s 过滤旧文件; 同名去重命中 → 主线程立即跳过 + 异步线程池轮询 (默认 100ms 间隔最长 5s) → 超时按"跳过本规则"处理不阻塞主线程. Jinja2 helper: `latest_input_filename()` / `latest_input_text()` / `now()`. 系统预设 `builtin_scanner_bypass_3line_txt` 带 `default_rule_config` 12 字段, 客户下拉一选自动填 (只需填 input_dir/output_dir).
- [FEAT-381-B] 每个标签可单独设置检测框颜色 — 项目页步骤设置表加 `box_color` 列 + `el-color-picker`, 默认空走全局设置. Monitor 页 `pickDetColor` 三级优先级: 副模型 `display_color` > `steps_config.box_color` > 全局 OK/NG. 锁定: 6 个单测.
- [FEAT-381-C] 副模型与主模型权限对等 — 添加副模型时, 其标签自动 append 到 `steps_config` 携 `from_model` 标识, 每个 step 可独立配 conf / box_color / sequence_order. 步骤设置表加 `from_model` 徽章.
- [FIX-381-A] 顺序/自定义模式守卫: 不在 `expected_sequence` 的标签不入 cycle 判定 — 之前场景: 项目配 A→B→D 序列, 但 E/F 启用且检出 → 加入 `current_cycle_steps` → 错判. 修复在 `_process_single_step` 增加守卫, **不入 cycle 但仍画检测框**. 锁定: `test_unexpected_labels_dont_join_cycle.py` 6 个单测.
- [DB-372-001] 数据库迁移 9 个新字段 (启动自动 ALTER TABLE): `detection_cycles.external_meta` JSON / `export_realtime_rules` 7 字段 (latest_file_strategy/wait_stable_ms/max_age_sec/dedupe_same_filename/dedupe_retry_max_sec/dedupe_retry_interval_ms/last_used_input_filename) / `export_templates.default_rule_config` JSON.
- [DOC-372-001] `docs/客户操作-扫码器旁路导出.md` 客户面向操作文档 (扫码器目录、输出目录、策略选择、常见问题).
- [UAT-372-001] 完整路径 UAT 全 PASS (`evidence/v372_full_path_<时间>/verdict.json`): C1 snapshot 锁文件防串号 / C2 mtime / C3 mtime_stable + max_age=30 过滤 100s 前旧文件 / D box_color #ff00cc 持久化往返 + from_model=main / E dedupe_queued → dedupe_timeout → 跳过 / F 前端立即触发 → 后端真实执行 → 三行 TXT 落盘.
- [CONFIG-372-001] 版本号 3.7.1 → 3.7.2 (`electron/package.json` + `electron/splash.html`).

## v3.7.1 (2026-05-12)
- [BLOCKER-371-A] **客户机启动崩溃修复** — v3.7.0 分包安装包客户机崩溃 `ModuleNotFoundError: No module named '_plugin_common'`. 根因 Nuitka 不复制 scripts/, 老 verifier.py 用 sys.path hack 找 _plugin_common.py 在客户机失败. 修复: 权威实现搬到 `backend/plugin_system/_plugin_common.py`, verifier 改绝对路径 import, CLI shim 兜底, 7 个 CLI 脚本不用动一行. 双向验证已确认.
- [FEAT-371-G1] 后端 PluginRegistry — 路由 / 钩子 / ORM 表挂载. `register_plugin(app, registry, license_payload, host)` 4 参数标准签名. 锁定: 10 个单元测试.
- [FEAT-371-G1.5] cycle_end 钩子在 `source_session_lifecycle_mixin.end_cycle()` 真触发, MES 后 scanner cleanup 前. `RUNTIME_MODE=test` 下挂调试触发端点. 锁定: 4 个单元测试.
- [FEAT-371-G2] 前端 ESM Loader (ADR-0002 host 注入模式) — `usePluginLoader` composable + `loadActivePluginFrontend(router)`, Tier 2/3 demo 重写. 锁定: Playwright UAT 10/10.
- [FEAT-371-G3] Tier 1 主题钩子 — `usePluginThemeStore` 套 CSS 变量 / title / favicon / logo / 隐藏菜单. 锁定: Playwright UAT 10/10.
- [FEAT-371-PRESET] 自定义导出新增系统预设「session 5 步产线 CSV」— 已踩平 4 个坑 (字段名 / |default vs or '' / 浮点格式化 / 步骤名按位置查表), 客户下拉一选直接用. 锁定: Playwright 真开浏览器 11/11 + 12/12.
- [CONFIG-371-001] 版本号 3.7.0 → 3.7.1 (`electron/package.json` + `electron/splash.html`).

## v3.7.0 (2026-05-11)
- [FEAT-370-001] 插件系统落地（白标 / 客户定制能力）— 三层 tier 粒度（Theme / UI / 全栈）+ RSA-PSS-SHA256 签名 + HMAC-SHA256 客户码绑定。`docs/plugin-system/` 全套设计 + `scripts/plugin/{pack,sign,verify,install,inject-public-key,lint-docs}.py` CLI + `plugins-examples/` 三层示例 + `backend/plugin_system/{verifier,manager}.py` + `backend/api/plugins.py`（`/api/v1/plugins/*` REST API）+ `backend/models/plugin_models.py`（4 张表 plugins/plugin_state/plugin_audit_log/plugin_config_versions）+ `frontend/src/api/plugins.js` + `frontend/src/store/usePluginStore.js`（Pinia store 抽象，含 `licenseMismatch` getter）+ Settings 新"插件管理" tab 全部走 store
- [FEAT-370-002] 数据库主线切到 PostgreSQL（保留 SQLite 单机回退）— `backend/db/database.py` dialect-aware（`DATABASE_URL` 前缀决定，连接池/PRAGMA 分别处理）+ `backend/db/sql_compat.py`（`hour_minute()`/`date_str()` 替代 `func.strftime`，已替换 sessions/sessions_export/reports）+ `backend/services/external_device.py` `datetime('now')` 改 Python timedelta + Alembic 全套（`alembic.ini` + `env.py` + baseline `2026_05_11_0040`，走 `Base.metadata.create_all` 规避 FK 顺序坑）+ `docker-compose.yml`（PG 16-alpine on 5433）+ `.env.example` + `scripts/db/initdb/01-extensions.sql`（pg_trgm + btree_gin）+ `scripts/db/sqlite_to_pg.py`（按 FK 排序、TRUNCATE RESTART IDENTITY、setval 重置序列、--dry-run/--truncate-first）+ 多客户 schema 隔离走 DSN `options=-csearch_path` + `tests/conftest.py` 自动检测 PG 模式独立 raw psycopg2 重置 schema
- [FEAT-370-003] 测试态兼容路由 `backend/api/test_compat_routes.py`（仅 `RUNTIME_MODE=test` 挂）— shim mes/config、alarm/state、sessions、source/project_config 等已变更 API 路径，配合 conftest seed dummy project + step_defs polling timeout，把原本 14 个 conditional skip 的 BDD 全转为真实断言
- [TEST-370-001] 三层全绿（SQLite 162/162 + PG 211/211 + Plugin 79/79）— 8 个插件单元 + 3 个插件 BDD（install_api / signing / cli）+ Playwright Page Object（plugin_page.py）+ E2E（test_plugin_page.py）
- [CI-370-001] GitHub Actions 双 workflow 守护 — `plugin-tooling.yml` 重构 3 job（static-checks / signing-roundtrip 含篡改包必拒+错公钥必拒 / bdd-plugin）+ `db-matrix.yml` 新增（matrix.db = [sqlite, postgres]，PG 任务 services.postgres 起 PG16 + alembic upgrade head + 3 张核心表存在性校验 + sqlite_to_pg --dry-run）+ `workflows/README.md` 4 个 workflow 索引 + 本地复刻命令清单
- [DOC-370-001] `docs/database-migration/README.md` 11 节运维手册（含第 11 节端到端 Runbook：首次部署/已有 SQLite 升级到 PG/Schema 升级/systemd unit/故障排查决策树）+ `START_HERE.md` 重写（反映已落地能力 + 端口表加 PG 列 + 分支提交历史 + 合主线 checklist 加 PG 回归）
- [ENG-370-001] `scripts/plugin/sign-plugin.py` 加 `PLUGIN_KEY_PASSWORD` 环境变量旁路 `getpass()` tty（CI 友好），默认仍走交互式 getpass 保护主作者隔离机器工作流
- [CONFIG-370-001] 版本号升级到 3.7.0（`electron/package.json` + `electron/splash.html`）；不设 `DATABASE_URL` 仍回落 SQLite 老用户零迁移；现有 18 组 API 路由完全保留；老 SQLite 部署如想接入 alembic 管控需先 `alembic stamp head`

## v3.6.1 (2026-05-09)
- [FEAT-361-001] 步骤耗时（PT）新增「计算方式」维度：合并 / 最后一次（默认合并） — 客户场景中同一步骤一周期内多次连续出现时，原有 PT 仅取最后那段不直观；新增"合并(SUM)"档作为默认，旧行为切回"最后一次"即可。后端在周期/步骤耗时统计层加 `step_cycle_durations`（当前周期 SUM）+ `step_cycle_durations_history`（历史周期 SUM 列表，封顶 100），段消失结算 + `_supplement_step_durations` 两路同步累加，`end_cycle` 快照入历史并重置，`_discard_empty_cycle` 仅重置不入历史，`reset_stats` 全清；`/api/v1/source/detection/results` 新增 3 个字段（`cycle_sum_step_durations` / `last_cycle_sum_step_durations` / `avg_cycle_sum_step_durations`）；前端 `useSystemStore.display.monitor` 加 `ptAggregate`（默认 'sum'）；Settings 页加「PT 计算方式」下拉；Monitor `formatStepPT` 改造为（aggregate × mode）二维选数据源；单/多工位轮询同步填充新字段；备用步骤 `default_pt` 兜底覆盖新字段
- [TEST-361-001] PT 合并档暴露测试 + 既有 mock fixture 兼容修复 — 新增 `test_PT合并档_暴露三个字段_v3_5_x` 验证当前/最近/平均三档；现有 mock fixture 显式补 2 个新属性为空 dict 防 MagicMock 序列化爆炸；空 history 用例追加新字段必须 {} 的断言；相关测试 13/13 全过
- [DOC-361-001] 操作手册新增 3.2 节「PT 计算方式」+ FAQ Q16.1 — 合并/最后一次语义对照、与 PT 显示口径正交的 6 档组合、每周期单次出现两档等价、CSV 导出兼容性说明、SQL 端 `SUM(duration) GROUP BY cycle_id, step_label` 自助提示
- [CONFIG-361-001] 版本号升级到 3.6.1（package.json + splash.html）；StepRecord 表结构不变；CSV 导出"耗时(平均/秒)"列暂不联动新开关保持段级语义；老 localStorage 自动深合并默认值

## v3.6.0 (2026-05-08)
- [FEAT-360-001] 周期性强制动作能力增强 + 语义修正 — `pipeline_config.run_on_start` 新开关；`resume()/resume_inference()` 把 run_on_start=true 的规则计数推到 interval 并加入 `_run_on_start_pending`（不立即发事件）；新增 `_check_periodic_actions_on_first_step(step_label)` 钩入步骤完成流程（首步=触发步骤→静默清零；首步≠触发步骤→立即发漏检告警）；`reset_stats()` 显式保留周期计数器与 `_run_on_start_pending`；`_emit_periodic_notification` 标 `should_warn_no_barcode=False`；前端 Project 页加 run_on_start switch + 事件选择器旁加"+ 新建事件"按钮直跳事件管理对话框
- [FIX-360-001] 检测框越界三层防御性 clipping — 后端推理出口 `source_geometry.py::clip_bbox_normalized`（_detect_only / _detect_and_track / _detect_segment 全接入）+ 后端 Kalman 输出 `apply_kalman_filter` 对 smoothed_pos clip + 前端 Monitor `clipNormalizedBox` 助手（drawMultiDetections / drawDetections / mask polygon 全覆盖），任一层独立兜底
- [FIX-360-002] MES 无扫码器时不再误报 ⚠ 未绑码 (85c351c) — `services/mes_hooks.py::has_any_scanner_present()` + `is_warn_no_barcode()` early-exit；`source_event_trigger_mixin.py` 在每个 event 写 `should_warn_no_barcode` 字段，前端只消费这个布尔值
- [FIX-360-003] 海康摄像头 NameError 永久修复 (df7ce2c) — `source_camera_start_mixin.py` 顶部补全 SDK / ctypes import，不再依赖临时热补丁
- [DOC-360-001] 文档体系大重构 — 新建 `AGENTS.md`（827 行，14 章项目地图）；27 个 skill 全面整治：8 大修 / 6 中修 / 2 小修 / 3 新建（debug-export / debug-cluster / debug-operator-license）/ 8 保留；事实修正 14→**15** mixin、12→**10** 视图、24→**27** skill 总数
- [DOC-360-002] 操作手册 PDF 化 + UTF-8 BOM 修复 — 解决 Windows / Android 乱码问题；新增 `tools/build_manual_pdf.py` (reportlab + 中文字体)；交付物 `docs/软件操作手册.pdf` (~2.3MB) 入仓
- [TEST-360-001] 周期性动作测试套件按新语义重写 — `tests/features/periodic_actions.feature` + `tests/step_defs/test_periodic_actions.py` 重写；新增 `tests/test_periodic_actions_v352.py` 集成测试覆盖 run_on_start / 首步触发分支 / reset 不清零 / `should_warn_no_barcode` 标记
- [CONFIG-360-001] `.gitignore` 收紧 — 排除 `.tmp_audit/` / `test (2)/` / `tools/diag_*.py` / `tools/test_scan_pair_*.py` / `docs/*.html`
- [CONFIG-360-002] 版本号升级到 3.6.0；feat/plugin-system 分支清理（无独立提交）

## v3.5.1 (2026-05-06)
- [FEAT-351-001] 新增: Monitor 页 PT/CT 三档显示口径 — 后端 `/api/v1/source/detection/results` 暴露 `last_cycle_time / last_cycle_time_with_ng / current_cycle_time / last_step_durations` 4 个新字段; 前端 systemStore 加 `ptMode/ctMode` (默认 'avg', 三档 avg/last/current); Settings 页两个独立下拉支持 9 种组合; `formatStepPT / getDisplayCT / displayCT` 全面改造跟随 mode + ctIncludeNg 二维组合; multiChannelData 缓存新字段支持多工位
- [FEAT-351-002] 新增: 数据中心快捷 CSV 导出与显示口径联动 — `/data/export/csv` 接 `pt_mode/ct_mode` 参数; 新增 `_calc_aggregates(cycles, steps)` 工具 (按 step_label 分组求平均); `_write_session_export / _write_cycle_export / _write_range_export` 三个 builder 全部接受 mode; `ct_mode=avg` 时周期详情区在"耗时(秒)"旁多一列"耗时(平均/秒)" (每行填全局平均); `pt_mode=avg` 时步骤详情区同样多一列; last/current/None 时 CSV 保持原列结构 (老脚本兼容); 前端 4 个快捷按钮 (当日/某周/某月/日期范围) 全覆盖
- [DOC-351-001] 操作手册大幅扩充 — 690 行 → 1579 行 (+880 行); 4.2 加周期性强制动作 / 4.5 数据导出 4 类彻底重写 / 4.7 PT/CT 三档说明 / 4.8 MES 完整章节 (~330 行覆盖 7 个 Tab) / 5.4-5.9 共 6 个常见配方 (SN.txt / 清洁治具 / Word 占位符 / 日报 / 跟踪校准 / MES 闭环) / FAQ Q9-Q19 共 11 项 v3.5.x 相关
- [TEST-351-001] 测试套件扩至 95 项全过 — 新增 `tests/test_pt_ct_modes_exposure.py` (5) + `tests/test_csv_export_pt_ct_modes.py` (5); BDD/集成/Pairwise 75 项 (14.69s) + Playwright 浏览器 E2E 20 项 (83.83s)
- [CONFIG-351-001] 配置: 版本号升级到 3.5.1; 完全兼容 v3.5.0 数据结构与 API, 无需 migration

## v3.5.0 (2026-05-06)
- [FEAT-350-001] 新增: 自定义导出 / 客户模板系统 — Data 页"数据导出"tab 加"自定义导出"+ "实时规则"按钮; 5 种格式 (txt/csv/docx/xlsx/pdf) + 3 种 input_file_mode (none/read_template/append) + 路线 A (Jinja2 自动样式) / 路线 B (占位符模板上传); 308 字段中央仓库 (`export_field_registry.py`) 含拖拽编辑器; 新增 ORM `ExportTemplate / ExportRealtimeRule / ExportRunLog / SystemConfig` + manual migration; 解决客户场景"从固定文件夹提取 SN.txt → 检测后改写测试结果/各项检测值/程序版本号"
- [FEAT-350-002] 新增: 实时规则 — cycle 结束自动渲染落盘. `services/export_realtime.py::dispatch_cycle_end_export` 入口扫描启用规则、按 channel/project filter 匹配后渲染; `mes_hooks._handle_cycle_end` 末尾调用, 独立 try/except 不影响 MES Hook / Scanner / Container 清理; ExportRunLog 记录每次执行 + 错误堆栈 + 前端日志面板; 规则 CRUD 含 test-run 干跑能力
- [FEAT-350-003] 新增: 周期性强制动作 (Periodic Required Actions) — 解决场景"动作 A-B-C-D 每做完 20 轮必须做动作 E, 超期触发事件". 新增 `PeriodicActionsMixin` (count_basis ∈ {all/good_only/ng_only} × reset_policy ∈ {always/only_when_due} × overdue_repeat ∈ {every_cycle/once/cooldown:N} × interval 全可配); VSM MRO 加 mixin; `apply_project_config` 末尾调用 `_apply_periodic_actions`; `end_cycle()` 在 commit + MES Hook 后调用 `_check_periodic_actions`; `get_detection_results` 暴露 periodic_actions 状态; Project 页"逻辑设置"加独立卡片 + Monitor 页加进度区块 (counter 进度条 + 状态色 ok/due/overdue); 计数器 JSON 持久化
- [FEAT-350-004] 新增: SystemConfig KV + License 缓存 — `/api/v1/system/display` 读写 brand_name / app_name / inspector_name / device_number / factory_name / line_name; 前端从 Electron IPC 推 license 给后端 (`/api/v1/system/license-cache`), 模板可用 `{{ license.customer }} {{ display.factory_name }}` 等
- [FEAT-350-005] 新增: Step 置信度聚合 (`sessions_stats.py` 加 avg/min/max_confidence) + 内部跟踪状态暴露 (`_stack_state / max_recognized` 通过 `get_detection_results.tracking` 子树暴露)
- [TEST-350-001] 测试框架建立 — 4 层 86 个测试 1 分 9 秒跑完: L1 BDD/集成 (pytest-bdd 29 个) + L2 Pairwise 矩阵 (allpairspy 减枝, 周期性 4 维 11 个 / 导出 3 维 9 个 / input_modes 9+3 个) + L3 集成链路 (5 个真 DB+真 Jinja2+真磁盘 IO) + 真实视频 sanity (1 个 @slow, 启真 cv2 + VSM) + L4 Playwright 浏览器 E2E (sanity 5 + Project 4 + Monitor 3 + Data 5 + 实时规则 3 = 20 个); 全 conftest 隔离; e2e_browser 用 `__e2e_` 前缀清理保证不留垃圾
- [CONFIG-350-001] 配置: 版本号升级到 3.5.0; 新依赖 `allpairspy / pytest-bdd / playwright / pytest-playwright / docxtpl / reportlab` 写入 `backend/requirements.txt`

## v3.4.1 (2026-04-30)
- [BUG-341-001] 修复: D 模式扫码器灯一直闪 (像 continuous 持续扫码) — `services/scanner.py` 的 ERROR 自动续 LON 路径只把日志加了个 `mode=D` 标签, 没真正排除 D 模式; listen 主循环也照样按 `_lon_sent=False` 逻辑续 LON. 改动 3 处: `start_scanning` D 模式不发 LON (灯一开始保持灭, 等 box 跨线触发); ERROR 收到 D 模式只 log 不动 `_lon_sent / _next_lon_after`; listen 主循环 D 模式跳过自动续 LON. 现在 D 模式 LON 唯一发送源是 `source._scan_d_update.send_lon_for_channel`, 真正实现"按 box 物理位置脉冲式发"
- [BUG-341-002] 修复: 前端 D 模式校验工位拿错 — 切到 D 模式时校验的是 `broadcast_channels[0]` (广播工位的第一个) 而不是 `channel_id` (绑定工位); 几何编辑器加载 snapshot 也是同样错误. 用户场景"绑定工位=工位2 / 广播=[工位1, 工位2]"时报"工位 1 不是容器模式"被回退. D 模式状态机跑在扫码器**绑定工位**的检测帧循环里 (用绑定工位的摄像头看 box), 广播工位与 D 触发判定无关. 改成只看 `channel_id`
- [FEAT-341-001] D 模式诊断日志增强: `_scan_d_update` 加首次出现 box / 跨线方向反 / 跨线触发 LON 三类调试 log, 让用户拿到日志一眼看出"为什么没触发" (是 box 没跨过线 / 还是配的方向反了 / 还是 box 一直在线同侧). 客户场景 v3.4.0 测试时画线后 LON 没触发, 加这日志后能直接定位
- [TOOL-341-001] tools/test_scan_d_trigger.py 8 用例全过 (含新增诊断 log 不破坏判定逻辑)
- [CONFIG-341-001] 配置: 版本号升级到 3.4.1 (hotfix)

## v3.4.0 (2026-04-30)
- [FEAT-340-001] 新增: 扫码模式 D — 容器跨线/区域触发 LON/LOFF 闭环 (仅容器模式项目可启用). 扫码器编辑加 D 选项 + 几何弹窗 (画线 + A/B 两侧染色 + 选触发方向, 或画 polygon 区域); 容器中心点跨线方向匹配 / 进区域 → 后端发 LON, 扫到码自动 LOFF + 等 box 离开 reset 允下一个; 同时刻只允许一个 armed box (产线节奏串行假设, 异常并发 log 不重复发); armed 但 box 离开未扫到码 → 主动 LOFF 防 LON 残留
- [API-340-001] 新增: `GET /scanner/check-container-mode?channel_id=N` — 切换 D 模式时前端校验绑定工位是否容器项目, 否则弹警告自动回退. `services/scanner.py` 加 `send_lon_for_channel / send_loff_for_channel / is_scan_d_for_channel / get_scan_d_config_for_channel` helper
- [TOOL-340-001] 新增: `tools/test_scan_d_trigger.py` 8 用例 (line A→B 触发 / 反向不触发 / 扫码 LOFF / scanned 后 gone 重置 / 同 box 不重 LON / zone 进入触发 / armed 离开未扫码强制 LOFF / 并发 box 不重发); v3.3.0 / v3.2.x / v3.1.x 回归 87/87 全过
- [CONFIG-340-001] 配置: scanner_devices 加 `scan_d_geometry / scan_d_line / scan_d_zone / scan_d_gone_confirm_frames` 4 个字段 (自动 migration); 版本号升级到 3.4.0

## v3.3.0 (2026-04-30)
- [FEAT-330-001] 新增: 扫码闭环结算 (`bind_timing='scan_pair'`) — 扫码 A 起新窗口, 扫码 B (≠A) 结算 A 周期 + 起 B 窗口, 同码二次扫软忽略 + 推 dup_warning toast, 多工位广播共享窗口同步开/同步结算; 容器模式按 sticky `was_complete` 判 OK/NG (允许工人扫前从箱里拿件后扫下一码), 非容器模式按 `_tracking_was_complete` 判定; 新增 `scan_pair_max_wait_sec` 超时兜底 (0=不超时, >0=强制 NG); 切到此模式自动锁定 `scan_required=on / scan_mode≠C / late_bind=0`; 停止/待机时弹窗 [结算 (默认)] / [丢弃] 收尾最后一码窗口; container_grouping 在 scan_pair 激活时关掉 gone-confirm 自动 _settle_box (扫码节拍接管)
- [TOOL-330-001] 新增: `tools/test_scan_pair_settle.py` 8 用例覆盖 (首扫 / 同码软忽略 / 不同码切窗口 / sticky OK / 从未齐过 NG / 超时强制 NG / 多工位广播共享 / 停止双分支); v3.2.0 / v3.2.1 / v3.1.2 回归 6+8+11 全过
- [CONFIG-330-001] 配置: scanner_devices 加 `scan_pair_max_wait_sec INTEGER DEFAULT 0` (自动 migration); bind_timing 字符串字段加新枚举值 `'scan_pair'`; 版本号升级到 3.3.0

## v3.2.0 (2026-04-30)
- [BUG-320-001] 修复: 容器 ID 漂移幽灵箱深层兜底 — `_update_container_grouping` 加 active 接管 (gone-confirm 期间同位置 IoU≥阈值复用老 did, 不开新条目); JC1 测试视频离线仿真 17→12 settle, 合并 5 次 ByteTrack 切 ID
- [FEAT-320-001] 新增: 项目设置 → 跟踪选项 → 容器策略行加 "ID 漂移合并 IoU" `el-input-number` (默认 0=关闭, 0.5=推荐, 0.7+=保守; 老项目升级行为不变)
- [TOOL-320-001] 新增: `tools/test_id_drift_merge.py` 6 用例覆盖 (核心 / 边界 / 配置兼容); v3.1.4 回归 11/11 全过
- [CONFIG-320-001] 配置: 版本号升级到 3.2.0

## v3.1.4 (2026-04-29)
- [BUG-314-001] 修复: 容器多箱模式幽灵箱误报 NG (客户机合格率 30% → 真实合格率), `_settle_box` 入口加 `container_settle_min_items` 阈值过滤
- [FEAT-314-001] 新增: 项目设置 → 跟踪选项 → 容器策略下"结算最少件数" UI (默认 1, 设 0 关闭过滤)
- [TOOL-314-001] 新增: `tools/test_ghost_box_fix.py` 11 用例覆盖 (含 Negative 复刻 v3.1.3 bug 现象的反证)
- [CONFIG-314-001] 配置: 版本号升级到 3.1.4

## v3.1.3 (2026-04-29)
- [BUG-313-001] 修复: ffmpeg 多线程解码偶发断言 SIGABRT 导致 uvicorn worker 崩溃 / 8001 永久卡死 (强制单线程解码兜底)
- [BUG-313-002] 修复: 跟踪模式下"步骤统计"表永远停在 `--/待检测`, 改用 `tracking.item_checklist` 翻 OK
- [FEAT-313-001] 新增: 多工位 Monitor per-channel MES 信息条 (工件号/未绑码/等待扫码/清除按钮 + OK/NG 3.5s hold), 与单工位行为对齐
- [CONFIG-313-001] 配置: 版本号升级到 3.1.3

## v3.1.2 (2026-04-29)
- [BUG-312-001] 修复: 容器分组迭代过程中并发删除导致 `KeyError: '泡沫槽3'`
- [BUG-312-002] 修复: 客户机录制视频全是 0 帧 / 播放失败 (NameError 被吞 + FFmpeg stderr 屏蔽 三层叠加)
- [BUG-312-003] 修复: USB 摄像头光线变暗后 FPS 从 30 跌到 10 之后再也不回 (auto exposure 被驱动锁定)
- [FEAT-312-001] 新增: 多工位广播扫码"主工位带动结算"模式 + 件数门槛
- [FEAT-312-002] 新增: 集群汇总 "OK 不可被 NG 覆盖" 合并策略 (`ok_lock`)
- [FEAT-312-003] 新增: ByteTrack `match_thresh` 暴露到项目设置
- [TOOL-312-001] 新增: v3.1.2 全量仿真测试套件 + 一键汇总 (75/75 通过, 13.2s)
- [CONFIG-312-001] 配置: 版本号升级到 3.1.2

## v3.1.1 (2026-04-29)
- [FEAT-311-001] 新增: 称重器"即时快照"配对模式 (pairing_mode=instant) — 解决"流水线工件不回零、1 工件 1 工位不允许丢数据"
- [CONFIG-311-001] 配置: 版本号升级到 3.1.1
- [TOOL-311-001] 新增: tools/test_weight_pairing.py 配对模式端到端仿真 (7 PASS / 0 FAIL)

## v3.1.0 (2026-04-28)
- [FEAT-310-001] 新增: 工单按项目/工位/集群三选一计件 (binding_scope) — 解决"集群已完成但工单数量一直 0"
- [FEAT-310-002] 简化: 集群模式工单不再选目标站点 (主机即代表集群,按 priority 取首条)
- [BUG-310-001] 修复: cluster-only 部署下 _handle_session_start "没找到工单"日志噪声
- [CONFIG-310-001] 配置: 版本号升级到 3.1.0
- [TOOL-310-001] 新增: tools/test_workorder_binding.py 工单绑定端到端仿真 (32 PASS / 0 FAIL)

## v3.0.0 (2026-04-27)
- [BUG-300-001] 修复: 检测启动卡住、开始按钮不可用、TensorRT 引擎不兼容
- [BUG-300-002] 修复: 检测停止/待机接口被离线扫码器拖慢约 20 秒
- [BUG-300-003] 修复: 会话启动时导出设置加载失败
- [BUG-300-004] 修复: 离线扫码器/外设反复打印大段 traceback
- [BUG-300-005] 修复: 数据中心 MES 工单筛选缺少刷新函数
- [BUG-300-006] 修复: MES 工件状态标签类型警告
- [BUG-300-007] 修复: 报表跨天班次统计边界和平均耗时单位误读
- [BUG-300-008] 修复: 录像写入失败对用户不可见
- [BUG-300-009] 修复: Electron 开发模式前端端口与计时器清理问题
- [BUG-300-010] 修复: 多通道 Monitor 局部状态串扰
- [FEAT-300-001] 新增: 开发者模式下的模拟扫码入口
- [FEAT-300-002] 新增: 开发者模式下的模拟外设/称重数据入口
- [FEAT-300-003] 新增: MES 刷新/测试按钮 loading 与可见反馈
- [CONFIG-300-001] 配置: 开发机后端启动环境切换为 `tianjun-runtime`
- [CONFIG-300-002] 配置: 版本号升级到 3.0.0
- [TOOL-300-001] 新增: 全量路由烟测与导入检查工具
- [TOOL-300-002] 新增: MES / Source 端到端回归测试脚本
- [TEST-300-001] 验证: 全前后端功能 QA 复测
- [TEST-300-002] 验证: 检测运行环境与发版元数据一致性
- [SKILL-300-001] 更新: 10 个 v3.0 相关 skill（API、source、检测、MES、前端、Session、打包发版）

## v2.7.15 (2026-04-25)
- [BUG-015-001] 修复: 工单管理表头修改了模板没生效, 一直是硬编码中文 (改成 v-for 渲染 visibleColOrder + colLabel 动态解析)
- [BUG-015-002] 修复: 新建工单弹窗字段标签变成数字 (formItems 过滤掉 type=system, 表单只渲染表单字段)
- [BUG-015-003] 修复: 字段配置"显示开关"默认 OFF 关闭后表格变全空白 (一次性迁移 visible=true + openTemplate 强制 normalize)
- [FEAT-015-001] 新增: 工单管理表头全动态化 (SYSTEM_COL_META + colLabel, 系统列也能改显示名)
- [FEAT-015-002] 新增: 字段"显示"开关 - 任意列可隐藏 (system / preset / custom 全可显隐)
- [FEAT-015-003] 新增: 自定义字段也能进表格 (从 row.extra_data 取值, 默认列宽 120)
- [FEAT-015-004] 新增: system 列"类型"显示具体类型 (标签/进度条/按钮组等), 加(不可改)后缀

## v2.7.14 (2026-04-24)
- [BUG-014-001] 修复: 跟踪模式数据中心 OK/NG 偶尔空白 + 显示非清单物品 (_settle_counting_cycle / _settle_box 严格按 expected_items 过滤 + 虚拟补齐 + 容器模式补 record_step)
- [BUG-014-002] 修复: 摄像头检测帧率不稳定 29-31 vs 10 fps (A: _bench_fps 无条件实测一次; B: 全后端候选 CAP_PROP_BUFFERSIZE=1; C: MJPEG generator try/finally 捕获断开)
- [FEAT-014-001] 新增: 只翻显示不翻推理 (推理用 raw_frame 保持训练精度, 显示/录像/快照用 display_frame, bbox 坐标做映射)
- [TEST-014-001] 新增: tools/test_display_transform.py / test_tracking_settle_filter.py / test_mjpeg_disconnect.py 三套验证脚本

## v2.7.13 (2026-04-24)
- [BUG-001] 修复: 跟踪模式"秒→帧"阈值按 fps_actual 换算导致实际延迟约设定值的 5 倍 (改用 fps_inference 新统计量, 去掉 min_cycle_age 的 1 秒兜底)
- [BUG-002] 修复: 集群同条码二次校正后"待汇总/最近完成"两栏不一致 (方案 B: 判齐全用 all_records, 二次齐发按 overall_result+ng_items 决定是否重推 MES; 假未齐箱从 pending 列表剔除)
- [FEAT-001] 新增: get_detection_results / get_source_status / get_manager_config 暴露 fps_inference 字段
- [TEST-001] 新增: tools/test_tracking_fps.py 跟踪模式 FPS 换算验证, 实测 1 秒阈值旧 5.29s → 新 1.43s
- [TEST-002] 新增: tools/test_cluster_recovery.py 集群方案 B 端到端回归 (独立 tempdir 数据库)

## v2.7.12 (2026-04-23)
- [BUG-001] 修复: 工单新建对话框 7 个输入框全部隐形 (DynamicFieldInput h('el-input', ...) 字符串名 Vite+ElementPlus auto-import 不解析，改显式 import ElInput 等对象)
- [BUG-002] 修复: 计划数量显示 0.00 (DynamicFieldInput number 类型固定 precision:2，planned_qty 单独走整数精度)
- [BUG-003] 修复: 集群目标明细同通道多行、同条码 OK/NG 并存 (sub_reports 按 (channel_id, source_address) 去重保留最新)
- [BUG-004] 修复: 扫码器重复扫码留脏 queued 记录 (新增 ok_rescan_cooldown_sec 冷却, OK 后冷却窗口内同码静默, NG 不受影响)
- [BUG-005] 修复: Monitor 结果一闪而过 1.2s 就清 (延长到 3.5s, 新条码到达直接覆盖)
- [FEAT-001] 新增: MES 适配器支持 4 种参数形式 (新增 form-urlencoded 字段平铺 + query-string URL 参数; GatewayPanel 4 选 1, 每种一句话说明)
- [FEAT-002] 新增: 集群箱数据按条码删除 + 批量清理 (DELETE /cluster/box/{barcode}, POST /cluster/boxes/clear scope=all/pending/recent/older)
- [DOC-001] 升级: 客户 MES 对接文档 v1.1 (4 种参数形式并列 + 各自 curl 示例; docx/pdf 重生成)
- [TEST-001] 扩展: test_mes_gateway.py 到 9 场景+3 API (新增 8806 form-urlencoded / 8807 query-string mock 端口, 9/9+3/3 全绿)
- [TEST-002] 新增: test_fixes_simulation.py (扫码冷却 / 集群去重 / 批量清理 API 端到端仿真)

## v2.7.11 (2026-04-23)
- [FEAT-001] 新增: MES Gateway 统一鉴权 (bearer / api_key / custom_header 三种方式合并进 headers)
- [FEAT-002] 新增: MES Gateway 按结果过滤 (push_on_result 支持只推 OK / 只推 NG / 全推)
- [FEAT-003] 新增: MES Gateway 物料名称映射 (label_mapping 把中文步骤名转成客户物料代码)
- [FEAT-004] 新增: 集群 aggregated 顶层便利字段 (order_no / workpiece_id / ng_items / result)，模板不再要嵌套取值
- [FEAT-005] 新增: 前端 GatewayPanel UI 图形化 (鉴权下拉 + 额外 headers + 过滤 + 映射 + 4 个预设模板)
- [FEAT-006] 新增: /test API 按 event_type 分路生成真实 context (cycle_end / box_complete / box_timeout)
- [DOC-001] 新增: docs/客户MES对接数据格式.md + docx + pdf (含 OK/NG/超时 3 个完整示例 + 10 项贵方确认清单)
- [TOOL-001] 新增: tools/test_mes_gateway.py MES 端到端测试 (5 个 mock 客户 MES, 10/10 场景全绿)
- [CI-001] 变更: CI 双通道分卷 (Action Artifact 和 GitHub Release 都走 1.9GB 分卷, main push 也跑 Split)
- [SKILL-001] debug-mes 追加 4 章 (push_on_result / label_mapping / 统一鉴权 / test event_type)

## v2.7.10 (2026-04-21)
- [BUG-001] 修复: 扫码器 device_type=text_lon 对现场 WMax 固件完全无效 (连上但永远扫不到码；_start_device 自动升级为 auto + 新增 _trigger_wmax_discover_once 合并触发一次 UDP 发现+激活 RPT)
- [BUG-002] 修复: WMax 扫码器连接后必须 activate_rpt_reporting 才识别 (v2.7.7 改 ondemand 是误判；现场固件不响应单次 Trigger 只认常开 RPT；wmax/manager.py 两处 auto_discover + scanner.py 三处 _ensure_wmax_connected 统一激活；test_connection 改 activate_rpt+收码5s)
- [BUG-003] 修复: 容器模式同一 label 跨帧遮挡重现 track_id 变更导致累积超额 NG (_update_container_grouping 按 steps_config.max_recognized 做 box 内上限检查，到上限只刷 last_seen 不再 +1)
- [BUG-004] 修复: 称重器稳定后 set_barcode 不立即派发 (SN-0016 汇总丢失；set_barcode 注入时如外部设备已是稳定称重器直接 _dispatch)
- [CLEAN-001] 清理: hotfix.py::apply() 去掉 v2.7.7c 三块运行时补丁调用 (已并入源码；函数定义保留作回退)
- [FEAT-001] 新增: 集群聚合 cycle_context.sub_reports (每个通道原始快照) + ClusterPanel.vue 子报告卡片
- [FEAT-002] 新增: 扫码器/外部设备 pairing_group + external_only (只喂外部设备的扫码器按分组号匹配，UI 隐藏绑定工位)

## v2.7.9 (2026-04-21)
- [BUG-001] 修复: 集群汇总页「已连接副机」切页后消失 (副机心跳原仅由前端 ClusterPanel setInterval 发送，页面 onUnmounted 即停；ClusterCollector.start() 新增后台线程 _heartbeat_sender_loop 每 5s 自主 POST /cluster/heartbeat，脱离前端页面状态)
- [BUG-002] 修复: Windows 串口 PermissionError 13 拒绝访问 (三个连接 loop 统一用新增的 _open_serial_with_retry 辅助，失败 1s 间隔重试 3 次；解决上次 close 未完全释放和 test_connection 竞争两种残留)
- [SKILL-001] debug-mes 追加「集群汇总 / 副机心跳」「外部设备串口 PermissionError 13」两章

## v2.7.8 (2026-04-21)
- [BUG-001] 修复: v2.7.6 传动杆误判过滤两个开关从未生效 (read_rod_filter_config 只读顶层但 _build_project_config 从不把字段放顶层，改为优先读 pipeline_config 兼容回退)
- [FEAT-001] 新增: Project 配置页「误判过滤（高级）」卡片，两层通用开关 UI (按 label 字符串匹配、可选或手输入 label、默认全关、老项目零影响)
- [SKILL-001] modify-source 更新 rod_filter 章节，记录 v2.7.6 静默失效坑
- [SKILL-002] modify-project-config 补充 pipeline_config 两个新子字段和数据流
- 已知遗留（v2.7.9 解）: 工位 3 称重器 COM20 权限拒绝；集群汇总无数据；副机 A 数据切页丢失

## v2.7.7 (2026-04-21)
- [BUG-001] 修复: WMax 扫码器三端口协议对齐 (DataLen 默认 4B→3B；encode_get_config_opt config_id<0 省略 field1；删除 HandShake 命令；activate_rpt_reporting 改为 GetConfigOpt+TurnOnOffVideo 序列)
- [BUG-002] 修复: scanner.py device_type='auto' 被误降级为 text_lon (保留原值，auto/wmax 统一走 WMaxDeviceManager 三端口)
- [BUG-003] 修复: _listen_loop 为 auto/wmax 建本地 TCP 抢占 WMax CMD 端口 (改为 pending_wmax 等待循环，完全交给 WMaxDeviceManager)
- [BUG-004] 修复: UDP 自动发现成功后 scanner conn.status 未更新 connected (统一处理 connected_mgmt_ips)
- [BUG-005] 修复: 测试按钮对 WMax 扫码器无效 (改走 WMaxDevice.flash_and_scan + 同步采集条码)
- [FEAT-001] 变更: 扫码器改为触发式 (ondemand) 工作模式——启动只建 TCP 不激活 RPT；start_scanning 发 LON，stop_scanning 发 LOFF；trigger_on/off 同时发 TurnOnOffVideo+Trigger+SendTermCmd 三命令兜底
- [FEAT-002] 新增: 测试按钮闪光 5 秒 + 同步返回扫到的条码 (ElMessage 直接显示"扫到 X 条: XXXXX")
- [DOC-001] 新增: WMax 协议文档 (docs/wmax_protocol.md) + IDManager 反编译资料 + wmax_known_devices.json 持久化
- [KNOWN] 已知: 扫码器 LOFF 后灯不熄灭 (三种关灯命令都发了但扫码器固件保持扫描模式，待抓包 IDManager 关灯行为后对齐，不影响扫码功能)

## v2.7.6 (2026-04-21)
- [BUG-001] 修复: 扫码器连上但"开始检测"不发 LON 命令 (resume/standby/resume_inference 补上 start/stop_scanning + _resolve_bound_channels 超界降级 + skip 原因显式日志)
- [FEAT-001] 新增: 称重器稳定值判定 + 抖动/空载过滤 (stable_delta/stable_count/zero_threshold 状态机 + 中位数上报)
- [FEAT-002] 新增: "有重无码"告警 (默认关闭 + 可配置延迟；MESGateway.dispatch + alarm_router 双通道)
- [FEAT-003] 新增: 传动杆误判过滤 (filter_rod_by_companion 空间共现 + RodSessionGate 软时序；默认关；实测砍 97% 误判保留 97.5% 真阳)
- [CONFIG-001] 配置: UI 工位编号统一 1-indexed (ScannerPanel/ExternalDevicePanel 动态生成选项 + 设备卡片 +1 显示)
- [SKILL-001] 更新: modify-source（传动杆误判过滤整章）

## v2.7.5 (2026-04-20)
- [BUG-001] 修复: 外部设备清空日志 int_parsing / [object Object] (FastAPI 路由顺序调整 + synchronize_session=False + 重试 + 原生 SQL 兜底 + 前端 detail 规范化)
- [BUG-002] 修复: 外部设备串口路径含前后空格导致保存失败 (前端 trim + 后端 _sanitize_device_payload + 串口打不开时改返回 200+warning)
- [BUG-003] 修复: 称重器 Modbus ASCII 粘包无法解析 (_parse_modbus_ascii_response 按 : 分段 + 逐段 LRC 校验)
- [FEAT-001] 新增: 画面旋转 / 镜像按通道独立配置 (VideoSourceManager 新增 video_rotation/flip_h/flip_v + _apply_frame_transform 在 _capture_loop 帧拷贝后应用 + per_channel 持久化 + GET/POST /source/transform/config API)
- [FEAT-002] 新增: MES 工单表单模板编辑器 (OrderPanel 重写：字段配置弹窗可改名/删除/重排预设字段 + 添加 8 种类型自定义字段 + DynamicFieldInput 动态渲染 + localStorage 持久化 + 必填字段兜底)
- [FEAT-003] 新增: MES 推送 context 便利字段 ng_steps[] + cycle.missing_step_count (build_context_from_cycle 追加，不破坏旧字段)
- [CONFIG-001] 配置: 外部设备 UI 暴露串口子参数 (数据位 5/6/7/8 + 校验位 N/E/O/M/S + 停止位 1/1.5/2)
- [SKILL-001] 更新: modify-source / api-sync / debug-mes / modify-frontend 四个 skill

## v2.7.4 (2026-04-20)
- [FEAT-001] 新增: 物品标注框可视化隐藏 (Project 步骤表加 hide_in_view 开关；Monitor 实时画面/SOP/步骤详情过滤；后端 0 改动)
- [FEAT-002] 新增: 堆叠模式 (跟踪计数模式下同 label 消失 N 秒后再现算下一层；新增 stack_enabled/stack_reappear_seconds/stack_required_count；独立状态机 + max 合并避免与 ByteTrack 计数双算)
- [FEAT-003] 新增: 最大识别数 (跟踪计数模式下同 label 同时只保留 Top-N 个 track_id，按置信度选 keeper 其余按距离归并；不动 ByteTrack 内部状态)

## v2.7.3 (2026-04-19)
- [BUG-001] 修复: 双工位 TensorRT 推理 imgsz 不传播报 AssertionError (engine 元数据直读 + channel_manager 三处属性传播)
- [BUG-002] 修复: 报警灯停检测/关软件不熄灭、副机检测时不亮 (pause/standby/resume 联动 stop/start_idle_light + shutdown 遍历所有通道 + start_idle_light 详细日志)
- [BUG-003] 修复: 扫码器允许同 IP+端口重复保存 (前后端 IP+port 唯一性校验，重复返回 409)
- [FEAT-001] 新增: 共享报警灯——一个物理报警灯多工位共用 + 优先级合成 (NG>警告>OK>待机，可配置；运行时 reload 无需重启)
- [FEAT-002] 新增: 共享报警灯示例配置文件 alarm_config.shared.example.json
- [SKILL-001] 更新: debug-alarm skill 增加共享报警灯模式整章

## v2.7.2 (2026-04-18)
- [BUG-001] 修复: 切换工位后 Toast 重复弹出 (events_log + _event_seq 清理 + 前端 initMultiChannelData 强制重置)
- [BUG-002] 修复: 降工位时 MESHook 残留工单/扫码状态 (新增 on_channel_removed 清理 6 个 dict)
- [BUG-003] 修复: 降工位时 AlarmRouter 串口/蜂鸣器残留 (新增 on_channel_removed: stop_alarm + all_off + disconnect + pop)
- [BUG-004] 修复: 数据中心 CSV 导出不区分项目和工位 (后端加 project_id/channel_id 参数 + CSV 加工位列 + 分组标题带 [项目名/工位N])
- [FEAT-001] 新增: 数据中心导出范围可见化提示条 + "导出全部项目"开关 + 文件名编码 proj{id}_ch{N}
- [FEAT-002] 新增: 降工位清理钩子公共接口 (on_channel_removed 统一入口)
- [TEST-001] 新增: 降工位清理测试 test_mes_hook_cleanup.py (5 用例)
- [TEST-002] 新增: 导出 CSV 全路径测试 test_export_csv.py (13 用例)

## v2.7.1 (2026-04-17)
- [BUG-001] 修复: 扫码器手动添加设备覆盖已有设备 (editingId 未重置)
- [BUG-002] 修复: 顺序检测步骤回退误判OK (A-B-A-B-C 全局去重→连续去重+回退标记)
- [BUG-003] 修复: 后端日志噪音 (_get_mgr 失败和循环# 调试日志移除)
- [BUG-004] 修复: 报警灯串口连接失败无详细错误 (返回具体错误+Linux自动chmod)
- [FEAT-001] 新增: 集群副机心跳与主机在线副机显示 (10秒心跳+已连接副机卡片)
- [FEAT-002] 新增: 虚拟扫码器/称重器 tkinter 版 (bat一键启动，无需额外安装)

## v2.7.0 (2026-04-16)
- [FEAT-001] 新增: 扫码器 LON/LOFF 模式 (TCP 55256 替代 WMax 逆向协议，自动连接+检测联动)
- [FEAT-002] 新增: 防重复结算开关 (跟踪模式同批次 OK+NG 重复结算可选抑制)
- [FEAT-003] 新增: TensorRT 转换超时保护 (600s 超时 + GPU/CUDA 环境诊断)
- [FEAT-004] 新增: 集群超时推送选项 (等齐模式超时可选推送 TIMEOUT 到 MES)
- [FEAT-005] 新增: 前端数值限制放开 (移除所有 :max，支持长时间场景)
- [FEAT-006] 新增: 前端术语通用化 (箱子→目标 等通用词替换)
- [FEAT-007] 新增: 称重器 Modbus ASCII + 连续发送双模式
- [FEAT-008] 新增: 扫码器条码自动注入外部设备
- [BUG-001] 修复: 未扫码 Toast 在扫码后仍然显示
- [BUG-002] 修复: MES 前端操作无反馈 (全面添加 ElMessage + loading)
- [BUG-003] 修复: cluster_config.timeout_push 列缺失启动报错

## v2.6.0 (2026-04-14)
- [FEAT-001] 新增: 多工位/多通道架构 (1-4 工位，每通道独立项目/模型/GPU)
- [FEAT-002] 新增: 集群汇总系统 (Master/Slave 多机互联，箱子数据汇总推送)
- [FEAT-003] 新增: 外部设备集成框架 (TCP/Modbus TCP/串口/HTTP 轮询 + 5 种解析)
- [FEAT-004] 新增: MES Gateway 工位绑定 (每个 MES 连接可绑定指定工位)
- [FEAT-005] 新增: 扫码器广播 (单扫码器触发多通道检测)
- [FEAT-006] 新增: 多工位通知/报警隔离 (独立指示灯/语音/Toast)
- [FEAT-007] 新增: 多工位计数器独立持久化
- [FEAT-008] 新增: 自动保存/恢复全流程 (视频源+项目+模型+GPU+检测状态)
- [FEAT-009] 新增: 每通道独立检测/语音/Toast 设置
- [BUG-001] 修复: 多工位项目配置被全局覆盖导致 GPU 工位检测不到东西
- [BUG-002] 修复: channel_count 热重载被覆盖为 1 导致多工位黑屏
- [BUG-003] 修复: MJPEG 四通道视频卡顿 (CPU 争抢)
- [BUG-004] 修复: TensorRT imgsz 不匹配导致推理静默失败
- [BUG-005] 修复: 跟踪模式步骤截图不显示 (cv2 未导入)
- [BUG-006] 修复: 视频文件源不自动恢复
- [BUG-007] 修复: 推理连续报错导致 CPU 100%
- [BUG-008] 修复: 报警串口不自动重连

## v2.5.0 (2026-04-09)
- [FEAT-001] 新增: 报警灯空闲常亮模式（检测中常亮，OK/NG闪烁后恢复）
- [FEAT-002] 新增: 工件条码绑定系统（中途扫码绑定、去重、误检重绑）
- [FEAT-003] 新增: Data 页工件码列显示
- [FEAT-004] 新增: 扫码成功提示框（可配置 Toast）
- [FEAT-005] 新增: 未绑码告警（Toast + 闪烁横幅）
- [FEAT-006] 新增: 扫码记录管理增强（清空、快速去重、日期筛选）
- [FEAT-007] 新增: Modbus RTU/TCP MES 适配器（RS485 串口写寄存器）
- [FEAT-008] 新增: 扫码器模拟器（PyQt5 TCP 调试工具）
- [BUG-001] 修复: 改步骤设置后返回 Monitor 视频黑屏
- [BUG-002] 修复: Tracking 模式非预期物品导致误判 NG
- [BUG-003] 修复: 未绑码告警不触发（import 路径错误被静默吞掉）
- [BUG-004] 修复: 单通道模式 MES 数据不传递到 Monitor
- [BUG-005] 修复: 设置页 Toast 配置卡消失（detection_config null 导致）

## v2.4.0 (2026-04-07)
- [BUG-001] 修复: TensorRT 模型切换后无检测框（imgsz 检测优先级错误）
- [BUG-002] 修复: 视频重新检测后 FPS 飙升至 60+（高分辨率帧预缩小）
- [BUG-003] 修复: 第一步结算模式有两个触发点（最后一步消失不再触发）
- [BUG-004] 修复: 第一步结算后新周期丢失第一步（raw_start 回填）
- [BUG-005] 修复: 自定义提示框计数器重启后清零（持久化到 DB）
- [FEAT-001] 新增: 高分辨率推理性能优化（预缩小帧架构）
- [FEAT-002] 新增: 外部 MES 工单双向推送（receive + extra-data）
- [FEAT-003] 新增: MES 适配器框架（gateway + 可插拔 adapters）
- [FEAT-004] 新增: 作业员管理系统（登录/登出 + Session/Cycle 关联）
- [FEAT-005] 新增: 工单管理页面增强（来源/产品编码/自动刷新/extra_data）
- [FEAT-006] 新增: 离线授权更新工具（read_machine_id + update_license bat）

## v2.3.0 (2026-04-02)
- [FEAT-001] 新增: 内置 MES 系统 — 工单全生命周期管理（状态机/计数/良率/批次）
- [FEAT-002] 新增: 单件追溯系统（扫码登记→检测→缺陷→返工，全链路）
- [FEAT-003] 新增: 缺陷自动分类（检测标签→缺陷代码映射 + Pareto 统计）
- [FEAT-004] 新增: MES Hook 异步集成引擎（不影响检测帧率）
- [FEAT-005] 新增: VS600 扫码器 TCP 通讯服务（自动重连/去重/三种解析模式）
- [FEAT-006] 新增: MES REST API（30 条端点，完整 CRUD + 追溯 + 统计）
- [FEAT-007] 新增: MES 前端管理页面（工单/工件追溯/缺陷分析/扫码器 4 标签页）
- [FEAT-008] 新增: Monitor 检测页 MES 信息条（实时工件状态 + 工单进度）
- [FEAT-009] 新增: 数据中心 MES 工单筛选
- [FEAT-010] 新增: 开发者模式（密码保护，控制多工位选项可见性）

## v2.2.1 (2026-04-02)
- [BUG-001] 修复: _just_settled 机制导致 NG 后所有后续周期无法计数
- [BUG-002] 修复: 替补步骤在新周期开始时被清空导致 NG
- [BUG-003] 修复: 切换项目后点"开始"不加载新模型导致无检测结果
- [BUG-004] 修复: 页面导航离开 Monitor 再返回后视频黑屏
- [SKILL-001~004] 更新: debug-source, debug-video, debug-frontend, modify-source
- [SKILL-005] 新增: update-release skill（更新发版全流程）
- [SKILL-006] 新增: tune-params skill（检测参数调优全流程）
- [TOOL-001] 新增: USB 摄像头后端测试脚本 (test_camera_backend.py)

### 补丁 v2.2.1a (2026-04-02)
- [BUG-005] 修复: OpenCV/NumPy ABI 不兼容导致推理崩溃 (opencv-contrib-python 降级到 4.10.0)
- [BUG-006] 修复: MSMF 摄像头后端资源竞争导致 can't grab frame
- [HOTFIX-001] hotfix.py v2.0.5 — RTSP H.265 + 摄像头 MSMF 优化 + generate_frames 崩溃防护
- [TOOL-002] 新增: Windows 一键补丁脚本 (patch_v2.2.1a.bat)

## v2.2.0
- 初始版本（本 changelog 体系建立前的版本）
