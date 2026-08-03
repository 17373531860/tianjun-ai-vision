# 后端核心读码笔记

> 阅读范围：2026-07-05 分段通读 `backend/main.py`、`backend/core/config.py`、`backend/api/source*.py` 全族（含 23 个 mixin + 14 个 has-a 组件）、`router_manifest.py`、`channel_manager.py`、`alarm.py`、`debug.py`、`system_display.py`、`services/weighing_engine.py`。**共 47 个文件**。
>
> **v3.41 增量复核（2026-07-17）**：source/检测核心域按 `git diff a23a8d2..HEAD` 补账 v3.33~v3.41 九个版本变更。各条目内新增「v3.3x 变更」行；1.4 / 1.5 表下补增量清单；新建 `source_persist_worker.py`（v3.38）完整条目并补录 `source_region_events.py` / `source_region_events_mixin.py`（v3.32 落地时漏收）。受影响文件的行数标注与漂移行号已按当前代码刷新。
>
> **v3.44 补账（2026-07-22）**：NG 处置整改批次，source 族 10 文件——
> - `source_project_config_apply.py`：新增 `resolve_ng_handling()` 统一解析器，`pipeline_config.ng_handling` 块归一四场景（violation/missing_step/short_count/gate），legacy 键（ng_remediation/closing_guard/instant_ng_on_violation/strict_order_violation_event_id）自动合成零差异；展开到既有运行时属性，状态机零改动。
> - `source_settlement_mixin.py`：新增收尾防呆五方法——`_closing_guard_blocks`（数量门：门步骤出现且已进箱数量未满→拒收+节流报警，空周期跳过）、`_maybe_enter_settle_hold`（纯缺步骤不判NG挂起等补做，首步缺=幽灵周期不挂）、`_maybe_resolve_settle_hold`（补齐重排判OK）、`_check_settle_hold_timeout`（超时按缺步NG落账，带 `_skip_remediation_defer` 防二次挂起）、`_fire_closing_guard_alarm`（借事件响应面，门/挂起事件分离）。
> - `source_custom_mix.py`：容器动作确认不应期 `action_cooldown_s`（默认2s，治闪断双结算）；`booked_item_total`（数量门"已确认进箱"口径，防备盘凑数骗门）；`compose_settle_event` 缓存步骤侧结论 `_last_settle_steps_ok`（治补数量死分支）。
> - `source_event_trigger_mixin.py`：NG+需确认 → 置 `_pkg_hold_for_ack` 传包装层挂账；`_remediation_bypass`/`_skip_remediation_defer` 防已处置重发再定格/二次挂起。
> - `source_session_lifecycle_mixin.py`：`end_cycle` 快照捎带 steps_ok/hold_for_ack 给包装协调器；结周期清 `_settle_hold`。
> - `source_step_stats_mixin.py`：每帧查挂起超时；挂起中空闲超时结算跳过；自动确认路径解包装挂账（按 redo）。
> - `source_sequential_mixin.py` / `source_events_check_mixin.py`：缺步结算入口接挂起；步骤消失时机触发挂起销结。
> - `source_state_init.py` / `source.py`：收尾防呆态初始化与运行时清理。
>
> **v3.45 补账（2026-07-29）**：上银 SY 热补丁 0a~0e 收编（滑块记账体系重做 + 待机保留周期/确认定格停推理停画面 + 数量门升级放行/少装挂起完善）+ 萍乡称重整改（清秤指令智能选择 / 过程提醒档 remind_only / 网关推送异步化）+ 海康 SDK 帧率可配 + dev-qing 逐件修复合入（9973d4c）。逐文件增量见各条目「v3.45 变更」行与 1.4/1.5 表下增量清单；行数标注已按当前代码刷新。
>
> **短信补账（2026-08-03）**：主程序原生短信通知——`router_manifest` 挂载 `/sms`；`main.py` 启停 12h 汇总调度；SMS 服务族见本节 `sms.py` 条目与 `05_backend_misc.md`。**不**在 `_trigger_event` 热路径发短信。

---

## 一、逐文件档案

### 1.1 启动与配置层

#### `backend/main.py`（~1330 行，短信复核）

| 维度 | 内容 |
|---|---|
| **职责** | FastAPI 应用入口：环境引导 → DB 建表/迁移/种子 → 多通道自动恢复 → MES/扫码/集群/外设初始化 → 定时清理 → 路由挂载 → 插件/入站别名/定时导出/健康探测 → 优雅关机 API + MJPEG 端点 |
| **核心函数** | L1-4 `OPENCV_FFMPEG_CAPTURE_OPTIONS` 必须在 cv2 前 setdefault；L77 `migrate_database()` 断言桩（禁止回流 ALTER）；L380 `_run_startup_init()` 统一启动初始化；L419 `auto_load_active_project()` 按工位配置加载项目；L565 `auto_restore_video_sources()` 恢复视频源+自动开始检测；L667 `_init_mes_services()` 注入 MES Hook 到 VSM；L848 `cleanup_on_exit()` 退出钩子；L1123 `shutdown_step()`（Electron 8 步关机 API） |
| **线程锁** | L845 `_cleanup_lock`（防重复清理）；L504 CUDA 预热 daemon 线程；L728/794/814/837 多个 `threading.Timer` 定时任务 |
| **上下游** | 上游：环境变量、`router_manifest.mount_all_routers`；下游：所有 VSM 通道、MES Hook、alarm_router、各 Coordinator、`SmsService` |
| **状态变量** | `_cleanup_done`、`_cleanup_timer`、`_daily_cleanup_timer`、`_extdev_cleanup_timer`、`_token_purge_timer` |
| **v3.3x 变更** | v3.38：扫码器旁路 SN 监控守护线程——客户扫码器不接软件、只往目录写 SN txt 时后台定期扫描实时导出规则输入目录缓存当前 SN（L1093-1103 `_start_scanner_bypass_monitor()` 启动，`cleanup_on_exit` L906-912 停止，异常隔离不拖主程序）；v3.38：`shutdown_step("end_cycle")` 的"周期进行中"守门改看周期 uuid（L1165，周期 id 由落库线程异步回填、可能短暂为 None，见 `source_persist_worker.py` 条目）；v3.45：`auto_restore_video_sources` 海康 SDK 分支按工位持久化配置读目标帧率（`hcnet_fps` 键，缺省 25）传给 `start_hcnetsdk`——此前硬编码 25，重启自动恢复必跌回 25fps（配套前端 Source 页目标帧率下拉）；**短信**：`_start_sms_summary_scheduler()` 启动 12h 汇总线程；`_shutdown_sms_notifications()` 在 cleanup / Electron 关机路径调用 `SmsService.shutdown`（异常隔离） |
| **注释坑** | L629-633 开机自动恢复检测**不再看** was_detecting，只要源在跑+模型就绪就自动开始；L397-399 插件加载必须在 app 创建**之后** |

#### `backend/core/config.py`（201 行）

| 维度 | 内容 |
|---|---|
| **职责** | 路径常量（BASE_DIR/DATA_DIR）、旧数据迁移、Settings 单例、上传/录制目录自动创建 |
| **核心函数** | L22-40 `_is_empty_db()`；L43-83 `_fix_db_paths()` 修正 models/video_clips 绝对路径（**表名是 models 不是 ml_models**）；L86-151 `_migrate_old_data()` 模块 import 时即执行 |
| **线程锁** | 无 |
| **上下游** | 被 main.py、database、所有需要 DATA_DIR 的模块引用 |
| **注释坑** | L56-59 历史文档误称 ml_models；L154 `_migrate_old_data()` import 即跑，测试需设 `TIANJUN_DATA_DIR` 隔离 |

#### `backend/api/router_manifest.py`（~112 行）

| 维度 | 内容 |
|---|---|
| **职责** | **主程序路由唯一登记处**（OVERLAP-3 治理）；`mount_all_routers(app)` 一次性挂载全部 `/api/v1/*` 路由 |
| **核心函数** | L24-109 `mount_all_routers()` — 分三段：CRUD 聚合段(L28-50)、重量级核心段(L52-90)、测试专用段(L92-109, `RUNTIME_MODE=test`) |
| **线程锁** | 无 |
| **上下游** | 仅被 main.py L1011 调用；import 放函数体内防 cv2 提前加载 |
| **注释坑** | L49-50 showcase_stats 与 sessions 共用 `/data` 前缀，**必须先注册 showcase_stats**；L73 Detection router 已删，走 source_routes |
| **短信变更** | CRUD 段 `import sms` 并 `include_router(..., prefix=f"{v1}/sms", tags=["sms"])` |

#### `backend/api/sms.py`（~136 行，新增）

| 维度 | 内容 |
|---|---|
| **职责** | 短信配置/串口列表/测试发送 API；进程级 `SmsService` 单例与配置热替换 |
| **端点** | `GET/PUT /config`、`GET /ports`、`POST /test`（保存需 `alarm.edit`） |
| **核心函数** | `get_sms_service()`、`_replace_sms_service()`（shutdown 旧实例后按需重启汇总线程） |
| **线程锁** | `_runtime_lock`（RLock） |
| **上下游** | → `SmsConfigStore` / `SmsService`；被 main 启停钩子调用 |
| **注释坑** | 坏配置回退默认关闭；测试发送不在检测热路径；诊断见 `debug-sms` skill |

---

### 1.2 多工位与外围 API

#### `backend/api/channel_manager.py`（885 行）

| 维度 | 内容 |
|---|---|
| **职责** | 多通道 VSM 容器；读写 `workstation_config.json`；GPU 分配；模型按通道独立实例加载 |
| **核心类/函数** | L39 `ChannelManager`；L82-87 `get(channel_id)`；L99-159 **`set_channel_count()`** 缩容时 4 处 on_channel_removed 清理链；L165-216 模型加载双签名（main vs slot）；L670+ FastAPI 路由 `/workstations/*` |
| **线程锁** | L44 `_lock`（通道 resize）；L50 `_global_warmup_lock`（跨通道 GPU warmup 串行）；L55 `_model_lock` |
| **上下游** | 持有 `Dict[int, VideoSourceManager]`；被 source_routes、main 启动恢复、alarm/MES 路由引用 |
| **状态变量** | `channel_count`、`channels`、`workstation_config.json` 各段（sources/gpu/auto_resume 等） |
| **注释坑** | L118-144 降工位必须调 4 个 on_channel_removed，缺一不可（AGENTS 不变量 4）；**禁止整写 workstation_config.json** |

#### `backend/api/alarm.py`（1067 行）

| 维度 | 内容 |
|---|---|
| **职责** | 串口报警灯/蜂鸣器；共享灯柱合成（ng/warn/ok/idle 四类优先级）；按通道路由 |
| **核心类** | L113 `AlarmManager`（单设备）；L620 `AlarmRouter`（channel→manager 映射）；L848 `alarm_router` 全局单例 |
| **核心方法** | L302 `trigger_alarm()`；L156 `set_shared_mode()`；L776 **`on_channel_removed()`** 共享模式只摘 ch、非共享断串口 |
| **线程锁** | L149 `_state_lock`（RLock）；L133 `_alarm_thread`；L151-152 延迟关灯 Timer |
| **上下游** | 被 `_trigger_event` → alarm_router.trigger_alarm；start/stop_detection → idle_light |
| **注释坑** | L776-805 共享模式降工位**不能** disconnect 物理设备；L16 新增报警事件必须归入四类优先级，禁止 bypass 合成器 |

#### `backend/api/debug.py`（177 行）

| 维度 | 内容 |
|---|---|
| **职责** | 调试中心 API：开关/日志/前端埋点回传；通道快照；集群 flow 回放测试 |
| **端点** | L30-72 `/debug/flags|logs|client-log`；L75-92 `/debug/channels`；L95-177 `/debug/test_cluster_flow` |
| **线程锁** | 无 |
| **注释坑** | L16-17 不挂鉴权，依赖开关默认全关；L99 验证的是 mes_hooks `_handle_cycle_end` 后半段 |

#### `backend/api/system_display.py`（371 行）

| 维度 | 内容 |
|---|---|
| **职责** | SystemConfig KV：display 品牌字段、polling 间隔、log-limits、device-status（RFC12）、license-cache |
| **端点** | L38 `/startup-ready` 深度就绪探针（**不鉴权**）；L84-109 display CRUD；L127-161 polling；L310 `/device-status` |
| **线程锁** | 无 |
| **注释坑** | L33-37 startup-ready 比 `/source/status` 更深，Electron 冷启动用 |

#### `backend/services/weighing_engine.py`（1864 行，v3.45 复核；v3.35 融合 + v3.39 两阶段流水线后从 648 行膨胀近 3 倍）

| 维度 | 内容 |
|---|---|
| **职责** | 原生称重投料检测引擎，**三种驱动模式**（模块头 L13-17 + L103-107）：`scale`（v3.31 默认，秤读数自主推进投料状态机，存量零差异）/ `step_gate`（v3.35 融合：视觉顺序 SOP 为周期主线，秤数据降级为"步骤完成门控"）/ `pipeline`（v3.39 萍乡百斯特两阶段流水线：秤上离秤冻结结算 + 秤下 FIFO 待收尾结案） |
| **配置面** | L42-157 `DEFAULT_WEIGHING_CONFIG`（不新增 ORM 列，全在 `pipeline_config.weighing`）：料别顺序/型号标准量/去皮方式/稳定判定；**v3.35** `context_expiry` L73-79（前置选择有效期 never/daily/shift/hours，默认 never 零差异）+ `visual_guard` L91-97（视觉料源防错，动作×固定区域→料别映射/限区违规，默认关）；**v3.39** `pipeline` 块 L108-117（单料判定 material + 三标签 label_onscale/fill/finalize + 秤台区 onscale_polygon + 皮重合法范围 + 队列深度）+ `timing` 块 L120-140（**秤指令时序 17 项全部现场可调**（v3.39 changelog 写 15 系旧口径，以代码 17 键为准）：去皮触发源 weight_first/dual_confirm/weight_only、皮重/净重稳定窗、缺料持续阈、离秤确认窗、Z 延迟/验证/重发、标签①③连续帧与冷却、装料/收尾超时）+ 报警事件映射补 5 项 L151-156（ok/tare_range/residue/takt/finalize_timeout） |
| **纯函数** | L160 `merge_config`；L218 `context_expired`（v3.35 有效期判定，shift 模式按班次表 L195 `_shift_key`）；L254 `get_model_spec`；L264 `judge_amount` 缺料/超量判定；L950 `point_in_polygon`（射线法，"纯 Python, 不依赖 cv2, 状态机可脱主程序单测"） |
| **核心类①** | L281 `WeighingStation`（scale 模式单通道状态机 idle/await_tare/filling/done，v3.31 主体未变）：L342 `start_product` 扫码开件、L410 `on_weight` 每帧读数推进、L385 `_check_wrong_material` 视觉料别防错、L499 `snapshot` |
| **核心类②** | L530 `PipelineStation(WeighingStation)`（**v3.39 两阶段流水线**，docstring L531-552 是全模式最完整的设计说明）——阶段一（秤上）相位 empty/filling/departing：L699 `_empty_tick`（清零调度/验证先行 + 皮重范围校验 + 去皮触发：weight_first 视觉佐证缩短稳定窗一半、dual_confirm 没标签不发 T；型号必选否决去皮，人员缺失只兜底报警不拦"登录把门" L722-727）→ L744 `_begin_fill`（发 T 进装料，SN 自动编 W月日-通道-序号）→ L804 `_filling_tick`（秤上装料/拿下装料两种习惯兼容、装料超时 takt 报警、净重稳定判定、缺料须持续 shortage_alarm_sec 防瞬时误报）→ L857 `_departing_tick`（读数骤降后空秤稳定 depart_confirm_ms 才算离秤，虚晃回 filling）→ L869 `_settle`（**确认离秤=冻结重量事实+OK/NG 结算+入待收尾队列+调度清零**，产 record/product_settled/alarm 事件）。阶段二（秤下）：L625 `feed_labels`（标签①③连续帧确认）→ L644 `_on_finalize_label`（**标签③"加钢脚水泥"到达 FIFO 结案队头件**，冷却期内"此刻的收尾动作属于更早(已结案)的件"不受理）；L919 `_check_finalize_timeout` 超时按「收尾未确认」结案+报警。L768 `_zero_tick`：Z 调度+归零验证+自动重发+残留报警（重试耗尽"接受漂移为新零点, 不卡生产" L799） |
| **核心类③④** | L968 `VisualGuardMatcher`（v3.35 视觉料源防错纯逻辑：region_action/direct_label 两来源、mode=map 命中区域映射料别 / restrict 限区违规报警、连续帧确认+按规则冷却）；L1068 `StepGate`（v3.35 融合模式单个"视觉步骤×外设条件"门控：kind='tare' 毛重超阈稳定→发 T→放行、kind='weight_judge' 净重稳定→按型号×料别判标准量→合格放行，不合格且 block=True 留在门控等纠正"重量变化后重新稳定会再判"；未知类型直接放行不卡生产 L1181） |
| **引擎** | L1189 `WeighingEngine`：L1213 `set_channel_config`（两种登记来源——weighing 项目 / sequential+step_gate 融合项目；按 drive_mode 选 Station 类；配置重载保留人员/型号上下文，**pipeline 待收尾队列"是已冻结的事实, 配置重载不能丢"** L1241-1245）；L1265 `_maybe_expire_context`（v3.35 有效期过期清上下文+节流报警）；门控四件套 L1297 `arm_step_gate`（视觉步骤确认后武装；新 tare 门控武装=新件上秤，清上件皮重残值）/ L1319 `consume_gate_if_passed`（放行即弹出，步骤此刻入周期）/ L1329 `reset_gates` / L1338 `gate_states`；L1344 `feed_detections`（推理线程每帧调，无守卫非 pipeline 零开销早退；pipeline 分支提取标签①③命中，标签①可按 onscale_polygon 过滤）；L1500 `feed_weight`（外设管线入口：scale/pipeline 走 `st.on_weight`，step_gate 只喂已武装门控+读数进缓冲供看板）；L1540 `_execute` 副作用执行层（send_tare/send_zero/alarm/record/product_done/product_settled/finalize 七种事件翻译，逐个 try/except 隔离）；L1773 `get_weighing_engine()` 单例 |
| **副作用层** | L1593 `_do_alarm`：**v3.35 修复**——之前调 `_trigger_event` 少传参必抛 TypeError 永远走兜底打灯；改 `fire_external_event_response`"只借事件响应面, **不**结束检测周期（融合模式下周期由视觉 SOP 管, 秤报警不能把在制周期切掉）"，事件 require_ack=True 自动进人工确认定格；拿不到 VSM 兜底直接打三色灯。L1624 `_do_record`：先内存台账再落 `WeighingRecord`（落库失败隔离；**v3.39** 未显式选人自动归属当前登录用户 L1631-1635）。**v3.39 pipeline 双段推送**：L1670 `_do_product_settled`（T2 离秤结算，仅合格件借 OK 事件响应面打绿灯/计数）+ L1692 `_do_finalize`（T3 正式结案，"此刻整件事实闭合 → 推 MES/达梦"——`gateway.dispatch("weighing_product_done", payload)`，payload 带 finalize_status=confirmed(标签结案)/unconfirmed(超时结案)+shift）。L1720 `_do_product_done`（scale 模式整件完成上传，同走 `weighing_product_done` 事件） |
| **线程锁** | L1201 `_lock`（RLock）；状态机本身单线程语义，feed_weight（外设线程）/feed_detections（推理线程）/控制面（API 线程）三方入口全部持锁 |
| **上下游** | 上游：`source_project_config_apply` 登记配置、`external_device_pipeline` 喂读数、推理线程喂检测、顺序状态机武装/消费门控、API `/weighing/*` 控制面；下游：`external_device.send_command`（T/Z 秤指令）、`channel_manager.fire_external_event_response`（报警/OK 事件链）、`WeighingRecord` 落库、`mes_gateway.dispatch("weighing_product_done")`（v3.39 专用结案事件，网关事件下拉 v3.41 起露出"称重成品结案"） |
| **v3.45 变更（萍乡整改四件，行号已漂移以当前代码为准）** | ① **清秤指令智能选择**（BUG-007，治"置零被量程限制静默拒绝、秤面卡负值/件重被吞"）：新增 `_clear_command_event`（L771）——读数在零点附近（阈值 `timing.zero_cmd_range_kg` 默认 0.5kg）才发 Z 置零（顺带校正真零漂移），偏离远改发 T 去皮（任何读数下都有效）；`_zero_tick`（L785）重构——正读数用皮重合法区间做**双解释合理性核对**（"清零已执行+新件上秤" vs "清零被拒+负基线+新件"两种解释，哪种算出的自重合法信哪种），重发前守门"新件疑似已上秤绝不再发清秤指令"（防把件重吞进秤内皮重寄存器）。② **过程提醒档**（FEAT-006）：`_alarm` 加 `remind` 形参——投料中缺料/超量改走提醒档周期性催料（`timing.remind_repeat_sec` 默认 10s），事件翻译层带 `remind_only=True` 调 VSM `fire_external_event_response`（只借灯/语音/Toast 不计数不定格），旧签名 TypeError 兜底兼容热补丁混装；NG 计数由离秤结算记一次（治"±3g 微抖 30 秒刷 NG +16"）。③ **网关推送异步化**（BUG-008，不变量 15）：新增 `_gateway_push_async`（L1756）+ 后台守护线程 `_push_loop`（L1773，线程名 weighing-gateway-push，队列有界 256 满挤最旧）——`weighing_product_done` 两处推送（结案/整件完成）全部出让后台串行投递，推理线程/外设数据线程绝不同步等网络（治达梦不可达时同步重试 3×5s 画面冻结 15s 误 NG）。④ **作业员名单**（FEAT-005）：`DEFAULT_WEIGHING_CONFIG` 加 `operator_from_users`（L68 默认 False=自由填写），snapshot 透出（L1514）供监控页决定人员输入形态（配套 `/weighing/operators` 新端点见 05 册） |
| **注释坑** | L19-23 设计原则：Station/Gate 不直接调主程序副作用，"只推进状态 + 返回事件列表"，副作用由 Engine 执行层翻译（"状态机能脱离主程序纯跑单测"）；L547 "秤指令**严格重量驱动**: 视觉标签①只作加速/双确认佐证, 模型漏检不影响称重主链路"；L549-551 零点基线：离秤未清零期间空秤显示=-上一件皮重，有效读数=读数-基线，**快手兜底**（新件在 Z 发出前上秤）跳过 Z 皮重按差值折算；L703-704 "Z 尚未发出而新件已上秤 → 取消清零调度（此刻发 Z 会把件重清掉）"；"正读数 = 清零已执行且新件已上秤 (快手竞态): 绝不能重发 Z 把件重清掉"（v3.45 起升级为双解释核对，见上）；L664 `reset` "保留待收尾队列 —— 队里是已冻结的事实, 不随复位丢"；非 weighing 通道 feed 直接忽略 |

---

### 1.3 VideoSourceManager 主类

#### `backend/api/source.py`（2155 行，v3.45 复核）

| 维度 | 内容 |
|---|---|
| **职责** | VSM 主类 + 全局 proxy；18 个 mixin 多继承；P7 has-a 兼容层；推理/检测控制入口 |
| **核心类** | L189 `VideoSourceManager(MRO: Tracking→…→PerItem→RegionEvents)`；L1990 `_VideoManagerProxy`；L2102 `get_video_manager(channel)` |
| **兼容层** | L263 `_COMPONENT_ROUTES` 六组件路由表；其后 `__getattr__`/`__setattr__` 转发 drawer/mp_overlay/counters_mgr/video_transform/inference_exec/sequence_labels |
| **保留在主类** | L549-745 PT 锚点/累加；L746 `_backfill_step_records()` 结算补落账；L910 `_reset_counting_cycle()`；L1111-1152 推理线程启停；L1395 `_clear_step_runtime_state()`；L1472/L1578 start/stop_detection；L1687 reset_stats；L1831+ generate_mjpeg（与 streaming_mixin 重复实现，主类版含 debug 埋点） |
| **线程锁** | `frame_lock` / `capture_lock` / `_progress_lock` / `detection_lock`（`__init__` 内创建）；v3.32 起另有 `_inference_start_lock`（见下） |
| **v3.3x 变更** | v3.32.0 收尾（TP 频闪真因）：`_start_inference_thread` L1111 加启动锁 + 线程代数——无锁的"检查-启动"两步被并发调用（采集自启 + resume 恢复）会各起一条推理线程，双线程交替发布"有结果/空结果"就是前端标注框逐帧频闪的真因；代数写进线程循环条件，`_stop_inference_thread` L1134 先作废代数再放倒运行标志，僵尸线程下轮自行退出。v3.32.0：MediaPipe 关键点颜色与连线颜色分开配（`mediapipe_pose_point_color` / `mediapipe_hands_point_color`，空 = 跟随连线色，L365-369）。v3.35：`_clear_step_runtime_state` 末尾清称重步骤门控残留（配了 `step_device_gates` 时调引擎 reset_gates，L1452-1459，防跨启停/强制结算残留卡步骤）。v3.38：`_backfill_step_records` 已记录步骤改读本地缓存 `cycle_step_records` 不再查库——step 插入进了落库线程，同步查库只能看到滞后快照、会重复补写；"周期进行中"守门统一改看 `current_cycle_uuid`（id 由落库作业回填）。v3.43：`is_packaging_paper_order_covered`（L1206）扩成查四处——`_last_cycle_steps` / `current_cycle_steps` / **`_oos_steps_seen` 序列外步骤旁路账本（当前代）** / `_last_oos_steps_seen`（上一代）；顺序/自定义-基于顺序模式下"放工单"是序列外检测步骤，永远进不了前两处（FIX-381 拦截），只有旁路账本看得见它——之前 gate 因此永不放行。旁路账本在 `_clear_step_runtime_state` 两代一起清（防跨启停残影放行）；`_pending_ack` 清理点配套清 `_pending_ack_reason`。v3.45（0a~0e 收编）：`__init__` 增 `_video_hold`（待机视频播放冻结标记，L377）；`_clear_step_runtime_state` 消费 `_ack_clear_runtime_after` 单次标记（L1441）并配套清缺步补做挂起 `_pending_remediation`（L1494，只清定格不清挂起会留"幽灵挂起"，下次无关确认被误路由到 redo 清掉在制周期）；`stop_detection` 先把收尾防呆挂起箱账按 NG 落账（`_finalize_settle_hold_ng('停止检测, 补做窗口关闭')`，L1618）再清运行态——治 7-27 视频 EOF 自动停检测时末箱 92/96 账目静默蒸发；`_clear_inference_caches` 加 `keep_cycle` 形参（L1685，待机专用：只清帧级缓存保留周期序列/箱内台账/挂起态/确认态，停止/切项目仍全清） |
| **注释坑** | proxy 仅 ch0 默认（L1990 附近）；多通道必须 `channel_manager.get(ch)`；RenderMixin/StreamingMixin 已抽出但主类仍保留部分方法 |

#### `backend/api/source_routes.py`（2352 行，v3.43 复核）

| 维度 | 内容 |
|---|---|
| **职责** | `/api/v1/source/*` 全部 HTTP 端点：摄像头/RTSP/海康/视频/图片/检测/项目配置/轮询结果/ack-event/per_item 控制 |
| **核心端点** | 视频源启停（L961 `start_video` 等）；检测启停/暂停/standby/reset（L1207 `reset_detection_stats`）；L1235 `_do_ack_pending`（人工确认公共体，含 elevated）；L1404+ **`get_detection_results`** 前端轮询核心；L1881 `set_project_config` |
| **线程锁** | 无（委托 VSM） |
| **v3.3x 变更** | v3.32.0 收尾：`get_detection_results` 补区域事件模式 in-flight（该模式不走步骤状态机字典，"动作进行中"以引擎 episode 起点算时长）+ 返回引擎快照 `region_events` + 项目配置摘要带规则身份（多工位建步骤行用）；MediaPipe 关键点颜色字段进 stream-config 读写（`_sanitize_optional_hex_color` 允许空串 = 跟随连线色）。v3.33（"显示与使用必须一致"修复）：`set_project_config` 成功后把工位持久化绑定同步写成同一项目（merge 只动 project_id 键，L1892+）、`start_video` 成功后落盘实际播放的视频路径——此前只改运行时，重启后 auto_restore 按旧绑定恢复"另一个项目+另一个模型"。v3.34：`_do_ack_pending` 支持「确认后保留周期」（事件配了 ack_keep_cycle 时只解除定格不清运行时，返回 `kept_cycle: true`，断点补做）。v3.39：`reset_detection_stats` 清零联动消除在途报警（入站配置 alarm_banner.clear_on_counter_reset 开启时，默认关）。v3.42：新增 `POST /detection/infer-once`（L1410 `detection_infer_once`，响应模型 `InferOnceResponse`）——标定用单帧推理，给项目页「从当前画面抓取锚点框」兜底（检测中锁菜单 × 停止/待机清实时结果，打包版标定死环）；委托 VSM `infer_once_for_calibration()`，RuntimeError → 400 中文提示。v3.43.1：`get_detection_results` 的 pending_ack 块增 `reason`（触发原因固化进阻塞态——之前前端从 recent_events 捞，事件超 30s 滚出窗口后弹窗原因变"—"）与 `keeps_cycle`（事件级 ack_keep_cycle 配置透出，前端弹窗据此明示"断点继续/整件重做"） |
| **注释坑** | `_dev_mocks_enabled()` 需 `ENABLE_DEV_MOCKS=1`；检测路由已统一到 source_routes，旧 `/api/detection/*` 已删 |

#### `backend/api/source_persist_worker.py`（136 行，v3.38 新增）

| 维度 | 内容 |
|---|---|
| **职责** | 每通道一条 FIFO 落库工作线程——川南"框冻结"治本刀：把帧循环里的 DB 写（cycle 建行/收尾/作废、step 插入、录像元数据）整体搬出推理线程，别处握 SQLite 写锁时卡的是本线程，检测出帧不受影响（RFC：`docs/rfc/收尾持久化出推理线程_设计方案_RFC.md`） |
| **核心类/函数** | L40 `PersistWorker`（作业 =（描述, 无参可调用））；L53 `submit()` 提交作业（同步模式/队列打满 → 原地执行，保数据不保延迟）；L69 `flush(timeout)` 用哨兵作业置事件实现 FIFO 语义排空（end_session 统计前用）；L90 `stats()`；L111 `_loop()` 线程体；L118 `_run_job()` 单作业执行 + 慢作业留痕（>3s 疑似锁竞争）；L35 `sync_mode()` 读 `TIANJUN_SYNC_PERSIST` |
| **线程锁队列** | L45 `queue.Queue(maxsize=2000)`（打满 → 退化调用方同步执行 + 大声留痕）；L47 `_start_lock`（懒起线程唯一性）；线程名 `persist-ch{N}` daemon，懒创建（`SessionLifecycleMixin._persist` property 首次访问时） |
| **上下游** | 上游：`source_session_lifecycle_mixin`（cycle_start/cycle_end/discard/reconcile/step 五类作业）、`source_recording_api_mixin`（录像元数据两类作业）；下游：SQLAlchemy `SessionLocal` + 作业内的 MES Hook / 插件 hook / 三协调器 / 扫码器联动（原有顺序打包进作业） |
| **关键不变量** | ① 同通道作业顺序 == 提交顺序（步骤插入 → 对账 → 周期收尾天然保序）；② 作业携带值快照，不读推理线程活状态；③ 单作业异常隔离（回滚 + 留痕 + 继续），线程永不退出；④ `TIANJUN_SYNC_PERSIST=1` 全局回退同步（现场兜底；测试套件靠它保住"提交后立刻可查"断言语义） |
| **注释坑** | 队列打满退化同步是"数据安全优先于延迟"的刻意选择；行定位一律用 `cycle_uuid`（`current_cycle_id` 由 cycle_start 作业回填、同步段可能为 None）；配套热路径索引迁移 m0001（step_records.cycle_id + video_clips.related_id） |

---

### 1.4 Mixin 档案（24 个，v3.45 复核）

| 文件 | 行数 | 职责 | 核心入口（行号） | 锁 |
|---|---:|---|---|---|
| `source_tracking_mixin.py` | 1106 | logic_mode=tracking 物品清点/track | `_update_tracking_stats` ~L200+ | 无专属 |
| `source_inference_loop_mixin.py` | 447 | 推理双线程主循环 | `_inference_loop` L257；`_inference_select_and_run_model` L53 | `_inference_frame_lock` |
| `source_step_stats_mixin.py` | 786 | 每帧步骤状态机主入口 | **`_update_step_stats` L60** | 无 |
| `source_capture_loop_mixin.py` | 447 | 采集线程循环（v3.41.1 相机重连后回放曝光设置，见表下注） | `_capture_loop` | capture_lock/frame_lock |
| `source_event_trigger_mixin.py` | 716 | 事件中心 | **`_trigger_event` L64** → end_cycle | 无 |
| `source_model_load_mixin.py` | 652 | YOLO/TRT 加载 | `load_model` L56+ | router.gpu_lock/warmup_lock |
| `source_check_modes_mixin.py` | 24 | 聚合 4 子 mixin | 空壳多继承 L17-23 | — |
| `source_container_grouping_mixin.py` | 755 | 容器分组 per-box 结算 | `_settle_box` L476/483 `_trigger_event(1/2)` | — |
| `source_checklist_mixin.py` | 309 | counting 模式 checklist | `_settle_counting_cycle` L60+ | `_settle_lock` RLock |
| `source_events_check_mixin.py` | 157 | 事件 FSM（settlement×logic 交叉） | `_check_events` L41+ | — |
| `source_sequential_mixin.py` | 432 | 顺序/自定义顺序结算 | `_settle_sequential_cycle` L27+ | — |
| `source_settlement_mixin.py` | 2081 | 结算总控+跨周期/last_first；v3.43 实时NG（违规即时结算）收口；v3.45 收尾防呆挂起/短拦长放/数量门在途口径 | **`_settle_for_cross_cycle` L760** 分发；`_process_last_first_mode` L780；`_fire_instant_ng` L1115 | — |
| `source_detect_runners_mixin.py` | 609 | YOLO 三种 runner；v3.42 增 `infer_once_for_calibration` L306（标定用单帧推理：对当前显示帧现推一帧，**刻意不过**步骤过滤/ROI/box尺寸——锚点标签通常不是步骤；不发布 current_detections、无状态机副作用；无模型但在检测=synthetic 时透传实时结果） | `_detect_only/_detect_and_track/_detect_segment` L136+ | `_gpu_lock_ctx` |
| `source_camera_start_mixin.py` | 875 | 6 种视频源启动（v3.41.1 曝光设置按 backend 语义精准下发 + set/get 可观测日志，见表下注） | `start_camera/rtsp/hcnetsdk/...` L114+；`_apply_exposure_setting` L136 | 启 `_capture_loop` 线程 |
| `source_session_lifecycle_mixin.py` | 1773 | Session/Cycle DB 生命周期 | **`end_cycle` L711**；`start_session` L229；`start_cycle` L497 | — |
| `source_recording_thread_mixin.py` | 305 | FFmpeg 录制线程 | `_recording_loop` L76 线程 | `_writer_lock` |
| `source_recording_api_mixin.py` | 417 | 录制 API | session/cycle/step 录制启停 | `_step_writers_lock` |
| `source_lifecycle_mixin.py` | 610 | pause/resume/stop/clear（v3.41.1 resume 重开相机沿用原 backend + 回放曝光，见表下注；v3.45 standby 待机保留周期+视频播放冻结） | `stop` L~100+；`_reopen_camera` L180 | — |
| `source_periodic_actions_mixin.py` | 888 | v3.5 周期性强制动作 | `_run_periodic_actions_on_start` | — |
| `source_synthetic_mixin.py` | 202 | 虚拟剧本源（测试） | `_SYNTHETIC_LOCK` L18 | class Lock |
| `source_per_item_mixin.py` | 1947 | logic_mode=per_item 逐件覆盖；v3.45 整板微移保持逻辑 ID + 防重复打抬枪正证据 | `_update_step_stats_per_item` L874 | — |
| `source_region_events_mixin.py` | 214 | v3.32 区域事件模式执行层：引擎动作 → 周期/步骤/事件副作用翻译（在 VSM MRO 末位，v3.41 补录） | `_update_region_events` L28（`_inference_loop` 分流入口）；`_region_settle` L140 → `_trigger_event` | 无（推理线程单线程访问） |
| `source_render_mixin.py` | 139 | 画框（**未接入 VSM MRO**，方法经 drawer 兼容层） | `_draw_box` L85 | — |

> **v3.41.1 USB 相机曝光保持（dev-qing 合入，技彩现场"锁帧"修复）**：症状是 Monitor 停止会 release 相机，重开（resume / 采集线程断线重连 / 前端 localStorage 恢复）后漏回写曝光 → 自动曝光复活把帧率压死。三处配套：① `source_camera_start_mixin._apply_exposure_setting` 重构——先探 backend（`_camera_backend_info`），MSMF 写 AE=0、DSHOW 写 0.25（老代码两个值都写、后写覆盖先写），每次 set 都记"请求值/set 返回/回读值"三元日志（`[Camera/Exposure]` 前缀），并把结果 dict 返回；启动成功后把 backend id 存 `_camera_backend`；② `source_capture_loop_mixin` 断线重连分支、`source_lifecycle_mixin._reopen_camera`（resume）都回放 `_auto_exposure/_exposure_value`（getattr 带默认，老源对象无属性也安全），resume 还沿用 `_camera_backend` 而不是硬编码 DSHOW；③ `main.py` bootstrap 新增 Windows 专属 `OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS=0`（**必须在 cv2 import 前**，与 FFMPEG threads;1 同段——技彩 UVC 相机开 HW transforms 时每次 set 分辨率/FPS 要重协商数秒）。回归：`tests/test_usb_camera_exposure_reopen.py` 6 例（⚠️ MSMF 语义用例必须 mock platform.system=Windows，Linux 开发机裸跑会走进 V4L2 分支断言失败）+ `tests/e2e_browser/test_usb_camera_exposure_restore.py`；UAT `tests/uat/uat_20260715_usb_camera_exposure_resume.py`。
| `source_streaming_mixin.py` | 165 | MJPEG（**未接入 VSM MRO**，主类 L1831 有完整版） | `generate_mjpeg` L48 | frame_lock |

**v3.33~v3.45 增量变更（mixin 族）**：

- `source_inference_loop_mixin.py`
  - v3.32.0 收尾：`_inference_loop` 带线程代数参数，代数与宿主对不上自行退出（推理线程唯一性，治 TP 频闪，见 source.py 条目）；区域事件分支改传原始帧给 `_update_region_events`（步骤截图用）。
  - v3.34：`_apply_label_splits`（L102）出口加 SplitOut 逐帧改写轨迹打点——排查"虚拟步骤时断时续"时这里是拆分层出口的唯一真相；仅在标签集合变化的帧打点防刷爆环形缓冲，`backend.settlement` 调试开关守门零开销。
  - v3.37（川南"框卡死"防线）：推理循环连续 30 帧异常时主动发布空检测结果清画面残留框 + 调试中心留证（`_infer_consec_errors`，走通一轮归零）——每帧异常时发布点永远走不到，上一次发布的框会永久定格，现场极易误判为模型问题。
  - **v3.45（0a 收编）**：新增 `_ack_freeze_active`——人工确认定格期间跳过模型推理并发布空检测（画面无框=一眼可见"线已定格"，整改动作绝不会被识别成放托盘/收尾步骤），tracking/区域事件模式豁免（无 require_ack 定格语义）；判定**必须在取帧之前**（视频源定格期间无新帧，放取帧后 frame=None 短路、超时自动确认永远轮不到——阻塞中仍每轮喂空检测给 `_update_step_stats` 驱动超时门）。
- `source_step_stats_mixin.py`
  - v3.34（幽灵起点修复）：时长门内"已确认但从未进入周期逻辑"的短暂滑过，离场超过本步骤消失等待时间后清确认态/连续帧/原始起点（新增 `_step_raw_last_seen`）——不清的话该标签下次真实出现带着陈旧起点直接越过时长门（实测 0.24s 滑过被当成"已持续 135s"触发第一步重现结算）；立即清会把时长门内的正常闪烁一并清掉，必须等离场确认。
  - v3.34（帧位口径修复）：新增 `_step_raw_start_frame_pos`，帧位起点与 wall 起点同刻记录——否则视频源按帧号差算耗时先被扣掉整个 min_duration 门槛、结算再比一次门槛 = 双重惩罚（1.0s 真实步骤被量成 0.38s 判无效）。
  - v3.34：`_dbg_step_rejected`（L45）节流键带原因前缀（同一步骤的"时长门"与"离场清理"两条线索互不遮蔽）；时长门内被拒补打点。ack 阻塞超时自动确认支持 ack_keep_cycle（保留周期，见 event_trigger）。
  - v3.35：`_weighing_visual_feed` 开启时每帧把检测结果喂称重引擎视觉守卫（料源防错，flag 守门零开销）；消失规则 B 对配了 disappear_uninterruptible 的步骤豁免（工具驻留画面的产线，"别的步骤出现"不代表本步骤结束；代码注释标 v3.34，实际随 v3.35.0 发布）。
  - v3.32.0 收尾：频闪诊断采样体抽出 `_diag_flicker_sample`（L627），custom_mix 与 region_events 共用。
  - **v3.45**：周期开始点调 `_notify_packaging_cycle_started()`（每周期只发一次，非包装通道零开销）——箱标签扫码授权场景"未扫标签就开做"当场通知包装协调器报警（方法本体在 settlement mixin，见 02 册协调器条目）。
- `source_event_trigger_mixin.py`
  - v3.32.0 收尾：NG TOP3 兜底期望集在区域事件模式取引擎规则名而非 steps_config 标签（客户报障"TOP3 出现的不是步骤而是标签"——该模式 steps_config 里是模型类别，"步骤"是动作规则名）；补"缺事件 X / 事件 X 重复"文案解析。
  - v3.34：新增 `_pending_ack_keeps_cycle`（L518）/ `_ack_release_keep_cycle`（L533）——事件配 ack_keep_cycle 时人工确认只解除定格、保留在制周期与步骤运行时（断点补做），默认关走老"确认重做"路径零差异。
  - v3.38：NG 补做挂起守门改看 `current_cycle_uuid`。
  - v3.43.1：四处进入人工确认定格的地方（`_trigger_event` 主路 / 外部事件 / NG 补做挂起 / 周期性动作）都把触发原因写进 `_pending_ack_reason`（弹窗原因不再依赖 30s 事件窗），解除定格三处配套清空。
  - **v3.45**：`fire_external_event_response` 加 `remind_only` 形参（L432）——"过程提醒"档只借灯/蜂鸣/Toast/语音，**跳过计数器联动与 require_ack 定格**（提醒不能把线停了；称重投料中缺料/超量周期性催料的接入点，见 weighing_engine 条目）；`_ack_release_keep_cycle` 消费 `_ack_clear_runtime_after` 单次标记（L570）——已结算落账的 NG 定格确认释放时**即使配了保留周期也强制清运行时**（陈账不清会毒化下一箱）。
  - **短信**：明确短信**不**挂 `_trigger_event` 热路径（注释锚点）；12h 汇总只读已落库结算周期，见 `debug-sms` / `sms_service.py`。
- `source_settlement_mixin.py`
  - v3.34（违序只报提前出现）：严格+单次守门只对"该步骤本周期还没做过"的提前出现报违序；已完成步骤的余像重现（补拧/笔迹残留/摆位调整是现场常态）维持静默拦截不入周期、不再报警（L1233-1240）。
  - v3.34：帧位起点两处取用点改走 `_step_raw_start_frame_pos`（与 step_stats 的口径修复配套）。
  - v3.35：新增 `_device_gate_hold`（L1120）步骤外设门控——融合模式核心接线点：视觉确认的新出现先"武装"秤门控（去皮/标准量判定），放行前不写 step_last_seen 不入周期、下帧重查；引擎未登记本通道直接放行绝不卡产线（`_process_single_step` 内 L1337-1344）。
  - v3.35：首步配 disappear_uninterruptible 且持续可见期间，重现不触发首步重现结算（只有真正走完消失结算后的再次出现才算新工件边界）。
  - **v3.43（实时NG·违规即时结算）**：`_fire_instant_ng`（L1109）统一收口——`instant_ng_on_violation` 开且周期已开时，违规确认点当场触发 NG 事件(2) 走完整结算链路（end_cycle/计数/报警/MES）；提示档/斩立决由事件2自身 require_ack 分流（定格时不清运行时，清理交确认端点）；返回 True=实时NG路径已消费（含被事件层守门抑制），调用方不再叠加提示事件。三个挂点：① `_fire_strict_order_violation`（L1168，重构）——严格守门拦下违序/缺前置时先节流（`_violation_throttle_pass` L1090，与提示事件共用一本账）再走实时NG，不适用才退回 v3.32 提示事件；② 步骤回退/非法重复入账点（L1520）——仅顺序型（sequential / custom 基于 sequential）放行，基于检测的自定义按出现次数判且不看回退标记，提前结会误杀；③ `_maybe_instant_ng_detection_duplicate`（L1144）——纯 detection 模式重复超次即时结，期望次数口径与 `_settle_detection_cycle` 同源，配了时长门的步骤跳过（结算前时长过滤可能把次数拉回去，中途判会误杀）。
  - v3.42.1：`_record_out_of_seq_step`（L1537）——FIX-381 把序列外启用步骤拦在周期外的同时记入 `_oos_steps_seen` 旁路账本 + 即时通知包装协调器 `on_step_detected`（错误隔离），"放工单"探测与"等放工单收尾即时完成工单"都靠它。
  - **v3.45（0a~0e 收编，本文件本轮最大改动 1707→2081 行）**：① 数量门在途口径——`_closing_guard_blocks` 的"已进箱数"改算上 `container_pending_peak_total()`（custom_mix 挂账在途峰值，L1290），治"最后一盘已拿起在途、封箱被门误拦"；② **gate_escalate_steps 短拦长放**——列表内的收尾步骤被门拦时不再静默吞掉，节流报警（`@gate_escalate:` 前缀共用违规节流账本）后**放行进周期**（L1309），让结算走挂起补做链（适合 min_frames 高、确认成立≈真人动作的步骤如封箱；噪声高发步骤别放）；③ `_maybe_enter_short_count_hold`——少装挂起（short_count=hold）：步骤全对仅箱内数量不足 → 摘下收尾步骤挂起等补数量，断点重做不判 NG 不清账（默认关零差异）；④ `_finalize_settle_hold_ng`——挂起超时/窗口关闭统一按缺步/少装原因落 NG，落账后置 `_ack_clear_runtime_after`（配保留周期也强制清运行时）；⑤ `_notify_packaging_cycle_started`——周期开始即时通知包装协调器（箱标签扫码授权"未扫就开做"报警入口）。
- `source_session_lifecycle_mixin.py`
  - **v3.38 RFC（本文件本轮最大改动）**：决策/持久化分离——`_persist` property（L168）懒建每通道 PersistWorker；`start_cycle`（L488）内存先行（uuid 即"周期进行中"标记）、建行进落库线程、提交后回填 `current_cycle_id`（仅 uuid 未变才写防串号）；`end_cycle`（L702）同步段定案 final_* 后把写库 + 后置链（MES on_cycle_end → 周期性动作 → 插件 cycle_end → 三协调器 → 扫码器联动）打包值快照作业进 FIFO **保持原顺序执行**，容器残留 box 清理动的是帧循环活状态、留同步段；`_discard_empty_cycle`（L1012）/ `_reconcile_step_records`（L1059，FIFO 保证排在本周期全部 step 插入之后）/ `record_step`（L1185，间隔用本地缓存算好、插件 step_change hook 在作业内紧跟 commit 保住"看到已提交行"契约）同款改造；`end_session` 统计前先 `_persist.flush(15s)` 排空防缺账。全文件"周期进行中"守门统一看 `current_cycle_uuid`。
  - v3.38（自定义班次，萍乡）：新增纯函数 `resolve_shift_label`（L42）——班次只定义"名字+开始时刻"，某时刻属于最近一个已开始的班次（天然无缝隙无重叠，跨零点与两班制边界语义一致）；`_get_current_shift`（L440）配了 data_config.shifts 列表（≥2 段）按列表判定返回班次名，未配保持 'day'/'night' 零差异。
  - **v3.45（0e）**：`end_session` 前置守门——收尾防呆挂起中的周期**不许当空周期丢**：会话结束=补做窗口关闭，先 `_finalize_settle_hold_ng('会话结束, 补做窗口关闭')` 按挂起原因落 NG（非致命 try/except 隔离）——治 7-27 UAT"末箱挂起等补做时视频放完就停，92/96 的账整个蒸发、四箱只结了三箱"。
- `source_recording_api_mixin.py`
  - v3.38：cycle/step 录像元数据写库进本通道落库线程；周期行由 cycle_start 作业建、FIFO 保证本作业执行时行已存在，行定位用 cycle_uuid（id 可能未回填）。
- `source_per_item_mixin.py`
  - v3.33（重复打防护，默认关零差异）：`_PerItemItemState` 增 5 个 dup 字段；`apply_coverage`（L265）增 dup 判定——已覆盖个体须先观察到动作框"连续离开 duplicate_release_frames 帧"（真正移开，滤拔枪卡顿/单帧闪断）再压回持续 duplicate_sustain_frames 帧才判"重复打"；`_per_item_fire_duplicate_alarm`（L1396）复用 NG 事件点灯 + PerItemPanel 黄条（按间隔节流），不落账/不结束周期/不动覆盖状态；待补态里回头重打照样报、补打漏掉的合法不报。
  - v3.33（换板兜底结算，也是修覆盖泄漏 bug）：workpiece_absent_settle_frames > 0 时周期进行中全部 item 标签连续消失该帧数 → 判工件已取走/换板 → 按真实覆盖状态兜底结算并复位（`_per_item_any_item_present` L1366）——否则 finish_label 没被检出时上一板永不结算，逐颗覆盖态按位置泄漏到新板（新板未打却变绿）。
  - v3.33：`_per_item_fire_remediation_alarm`（L1378）remediation_event_id=0 时回退标准 NG 事件 event2（此前不触发，现场要等人工确认落账灯才响）。`get_per_item_state` 快照带 dup 配置 + `last_warning`。
  - **v3.45（9973d4c dev-qing 合入，1782→1947 行）**：① **整板微移保持逻辑 ID**——模块级 `_translate_bbox`（L92）/ `_one_to_one_iou_matches`（L103，一对一贪心 IoU 配对），`tracking_displacements`（L276）收集配对位移、`_estimate_board_translation`（L849，中位数）估整板平移，`update_item_positions`（L291）把可信整板位移同步给**所有**锁定框（含本帧没检出的）——工人碰板/换手导致整板平移几个像素时，逻辑 ID 不再断链重编；item 增 `associated` 标记（本帧是否有真实检测框关联上），快照透出给 Monitor 过滤纯预测框（前端只画 `associated!==false`）。② **防重复打抬枪正证据**——个体 covered 后必须先连续 `post_cover_away_frames` 帧观察到"目标螺丝重现 或 动作框明确打到**其他**逻辑 ID"任一**正证据**（`released_after_cover`）才武装重复打判定；电枪回到本 ID 终止尚未完成的抬枪确认——治"拔枪卡顿/框抖动被误判成抬枪又压回=重复打"误报。
- `source_checklist_mixin.py` / `source_container_grouping_mixin.py`：v3.38 settle_dedup 补开周期的守门改看 `current_cycle_uuid`（id 由落库线程回填可能短暂为 None，看 id 会漏开/重复开周期）。
- `source_capture_loop_mixin.py`：**v3.45（0a 收编）** 新增 `_video_playback_held`——仅 `source_type='video'` 的播放冻结判定（待机 `_video_hold` 置位 或 检测中人工确认定格 `_pending_ack`），`_capture_loop` 命中则本轮不读帧、播放位置冻结——此前只冻结了 MJPEG 推流画面、文件仍在后台被消耗（待机 1 分钟=剧情静默流失 1 分钟，SY3 实测 3/4 盘装盘全程没人看见）；真实相机永不冻结（现实世界暂停不了）。
- `source_lifecycle_mixin.py`：**v3.45（0a 收编）** standby 待机改"临时暂停"语义——`_clear_inference_caches(keep_cycle=True)` 只清帧级缓存（卡尔曼/推理帧/已确认检测），**保留在制周期/箱内台账/挂起态/确认态**（收工全清走「停止」路径）；视频源待机同时置 `_video_hold` 冻结播放位置、恢复时复位从停住的帧继续；pause（收工路径）不留待机冻结。
- `source_sequential_mixin.py`：**v3.45（0e）** 缺步结算入口接少装挂起——步骤全对但箱内数量不足且非跨周期携带时，`_maybe_enter_short_count_hold` 摘收尾步骤挂起等补数量（断点重做，不判 NG 不清账，默认关零差异）。

---

### 1.5 Has-a 组件（16 个，v3.45 复核）

| 文件 | 行数 | 职责 | 核心 API | 锁 |
|---|---:|---|---|---|
| `source_state_init.py` | 437 | VSM 状态字段初始化 13 组 helper | `init_state(h)` L416 | 多处 Lock/RLock 创建 |
| `source_project_config_apply.py` | 751 | set_project_config 实现 | `apply_project_config` L643；写 settlement_mode | — |
| `source_drawer.py` | 303 | Kalman + 画框 | `Drawer.draw_box` L68+ | — |
| `source_mediapipe.py` | 720 | MediaPipe 二段 pipeline | `MediaPipeOverlay` L236；worker 线程 L425 | `_init_lock` `_pending_lock` |
| `source_counters.py` | 74 | 计数器持久化 | `persist/save_snapshot_to_db` | — |
| `source_video_transform.py` | 121 | 旋转/镜像/坐标映射 | `apply_to_frame/map_bbox_to_display` | — |
| `source_inference_executor.py` | 49 | 推理 ThreadPoolExecutor | `get/shutdown` | — |
| `source_inference_router.py` | 283 | 多模型 GPU 调度 | `InferenceRouter` L139；`gpu_lock` L155 | gpu/warmup/event Lock |
| `source_sequence_labels.py` | 220 | 步骤标签查询（无状态） | `get_expected_labels` L107 等 7 方法 | — |
| `source_geometry.py` | 106 | 几何/字体工具 | `point_in_polygon/get_chinese_font` | — |
| `source_roi.py` | 167 | 逐步骤 ROI mask | `ensure_roi_mask/apply_roi_mask` | — |
| `source_recorder.py` | 255 | FFmpeg 录制器 + KalmanFilter2D | `FFmpegRecorder` L28 | `_lock` |
| `source_sdk_loader.py` | 179 | HCNetSDK/海康工业相机/调试日志 | `debug_log/get_hikvision_device_list` | — |
| `source_custom_mix.py` | 1735 | custom 混合子状态机；v3.45 容器记账体系重做（0b~0e） | `_ContainerAccumulator` L59；`CustomMixMachine` L1448；`compose_settle_event` L1684 | — |
| `source_label_split.py` | 557 | v3.32 同标签区域拆分（虚拟步骤）+ 就位提示：检测出口标签改写层（fixed/anchor 两种定位 × 多轮次 × 每轮独立区域） | `parse_label_splits` L120；`LabelSplitEngine.apply` L435；`parse_placement_guide` L499；`PlacementGuideState` L515 | 无（每通道单实例仅推理线程访问） |
| `source_region_events.py` | 908 | v3.32 区域事件模式纯逻辑引擎（时序+空间规则：overlap / region_enter / region_exit，episode 状态机 + 序列结算判定；不做任何主程序副作用，v3.41 补录） | `parse_region_events` L341；`RegionEventEngine.process_frame` L507；`snapshot` L561 | 无（推理线程单线程访问） |

**v3.33~v3.45 增量变更（has-a 组件）**：

- `source_state_init.py`：v3.32.0 收尾 `_init_inference_threading`（L35）增 `_inference_start_lock` / `_inference_generation`（推理线程唯一性，见 source.py 条目）；v3.35 `_init_step_state`（L115）增 `step_device_gates`（空 dict = 未配置一次 get 早退零开销）；v3.43 增 `instant_ng_on_violation`（默认 False 零差异）、`_pending_ack_reason`（确认弹窗原因固化）；**v3.45** 增收尾防呆挂起族字段——`_closing_gate_escalate_steps`（短拦长放集合）、`_settle_hold`（挂起态 dict）、`_short_count_hold`（少装挂起开关）、`_ack_clear_runtime_after`（已落账 NG 定格释放时强制清运行时的单次标记），默认全零差异。
- `source_project_config_apply.py`
  - v3.35：steps_config 解析补 disappear_uninterruptible（代码注释标 v3.34，实际随 v3.35.0 发布）；称重引擎登记扩成两种来源——logic_mode='weighing'（秤驱动）或其它模式 + weighing.drive_mode='step_gate'（融合：视觉 SOP 主线 + 秤只做步骤门控），都不是则注销零残留（L629+）；解析 steps_config[].device_gate 到 `step_device_gates`（L650+）；visual_guard 开启时置 `_weighing_visual_feed`。
  - v3.38（NG top3 保持，dev-qing 合入）：`_reset_cycle_state`（L458）不再清 ng_step_cycle_counts——它与 step_counts 同属累计统计，待机→再开始会重放项目配置，在这里清会把当天 NG 步骤排名抹掉；归零点移到 `_reset_cumulative_step_stats`（L473，Monitor「清零」/切项目）。
  - v3.39：drive_mode='pipeline'（两阶段流水线称重）也要求视觉喂帧（标签驱动去皮加速与 FIFO 结案），`_weighing_visual_feed` 判定并入。
  - v3.43：解析 `pipeline_config.instant_ng_on_violation`（实时NG开关，L300）。
  - **v3.45**：`resolve_ng_handling` 扩两键——`short_count` 新增 `'hold'` 档（少装数量挂起补做，非法值回退默认）+ `gate_escalate_steps` 列表（短拦长放：门拦时报警放行的收尾步骤白名单，只收非空项），展开到 `h._closing_gate_escalate_steps`（set）/ `h._short_count_hold`；docstring 明示适用边界（min_frames 高、确认成立≈真人动作的步骤才放进来，噪声高发步骤别放）。
- `source_label_split.py`（v3.34 多轮次真实模型三防护，默认 0 零差异）：rounds 增 `trigger_min_seconds`（切换标签持续在场满该秒数才确认切换，滤单帧误检闪现把轮次多推一拍；解析钳位在 gap-0.1 以下防"同一次在场先判离场再确认"的逻辑矛盾）与 `trigger_conf`（切换标签专用置信度下限，低于按不在场——非切换阶段零星低置信度误检会不停刷新在场时刻，轮次永远等不到"离场再出现"卡死不切）；`_update_rounds`（L351）轮次归零加 saw_cycle 守门（"本轮次内周期确实装载过步骤"才允许空闲归零——工件刚开工、切换标签已离场而首个步骤还没进周期的空窗不是"下线"，不加守门轮次刚推到 1 就被打回 0）；切换标签每次在场留痕（conf + 是否低于下限，5s 节流，`backend.settlement` 开关守门）。
- `source_mediapipe.py`：v3.32.0 收尾——关键点/连线颜色分开配（`_custom_draw_specs` 返回双 spec，关键点色缺省跟随连线色老配置视觉不变）；二段管线渲染改把 ROI 关键点转 NormalizedLandmarkList 后复用 mp draw_landmarks（与 baseline 两条路完全一致，老版蓝线黄点手工渲染及 HAND_CONNECTIONS_21 常量删除）。
- `source_sequence_labels.py`：v3.40（川南反馈）`is_legitimate_next_in_sequence`（L152，changelog 里称 `_is_expected_repeat_position`，以代码实名为准）加严格前缀守门（L178-180）——位置索引判定隐含"当前周期是期望序列的严格前缀"前提，周期跑偏（漏做/乱序）后位置指针错位，滞留画面的末步余像被反复误认"合法重复"重新入周期（前端末步 OK/NG 闪烁 + 结算多报"重复步骤"）；偏离前缀时一律走常规去重，干净前缀的合法重复行为不变。
- `source_custom_mix.py`：v3.38 面板"周期中/等待"判定改看 `current_cycle_uuid`；v3.42.1 `compose_settle_event` 周期结算时旁路账本轮转——`_oos_steps_seen` 搬到 `_last_oos_steps_seen` 后清空（只留最近两代防陈旧"放工单"残影误放行尾箱 gate）。
  - **v3.45（0b~0e 容器记账体系重做，1080→1735 行，本版最大单文件改动）**：`_ContainerAccumulator`（L59，托盘身份独立轻量跟踪）系统性重做——① **在位身份各自累计峰值**：每个 IoU 关联出的托盘身份只数落在自己框内的滑块、峰值各记各的，唯一排除项是被"在途盘"定格的旧身份（上层盘拿走、下层盘同位露出被关联到旧身份时峰值当即定格不串账）；② **结算挂账等真账**：动作脉冲到来时若主位是"空峰值幽灵"，先在账里找"已离场≥动作消失帧、有峰值、未记账"的真盘改配记账（多候选取峰值最大），动作结账支持"峰值就绪等待"挂帧（`_settle_defer_frames`）——治 7-23 视频"一盘后主位被幽灵占住、真盘账永远丢"（72/96 误 NG）；③ **稳定计数快照**：`stable_min_frames`>0 时维护计数历史（最近 N 帧波动 ≤±2 取窗口众数）+ 动作前快照 `_fresh_snapshot`（L225），结账优先用快照、没有才退回峰值老口径；④ **IoU 去重**：计数前按 IoU>0.45 去重置信度高者保留（复用 per_item `_bbox_iou`，治 22 检出 25 的持续性重复框）；⑤ **峰值封顶** `peak_cap`（默认 0=关）：盘子物理槽位有上限，封每盘每标签峰值（治瞬时重复框 25/26 咬死峰值整箱 97/96 误判"超出"）；⑥ 主位选择"有峰值的真盘优先、同档 FIFO"（防空峰值幽灵抢主位）。`container_pending_peak_total`（`_TrackingMixEngine` L1253 / `CustomMixMachine` L1530 透传）给数量门"挂账在途也算已进箱"口径；配置入口 `build_custom_mix` 读 pipeline `custom_mix_container_peak_cap` / `custom_mix_container_stable_min_frames`（L1653+，默认 0 全关零差异）。
- `source_region_events.py`（补录 + 增量）
  - v3.32.0 收尾：动作互斥打断 `_interrupt_others`（L818，**常开非配置**）——一个动作确认瞬间其他进行中 episode 立即收尾（已确认的产出闭合动作、未确认的半截命中作废）；没有它，消失确认秒数会把"断开 < N 秒"的两段命中桥接成一次动作（TP 复检场景两次扫码被并成一次，序列少一步误落兜底 NG）。位移门槛轨迹改 5 帧中位数平滑 `_track_motion`（L657）——手划过静置工具时遮挡把框切小、中心单帧跳变 ~0.06 直接进包络就是假位移。`snapshot`（L561）补 in_progress / episode_start_ts / suppressed（Monitor 步骤面板"进行中"高亮与 in-flight PT）；`_emit_confirmed` 带确认瞬间主体框（执行层裁步骤截图用）。
  - v3.34：确认时长秒基门槛 `min_seconds`（`_held_long_enough` L706）——min_frames 的实际时长 = 帧数/推理帧率，相机与推理帧率都会漂，同一配置在不同机器松紧不一致；配了秒基后按 episode 命中跨度判定、min_frames 退化为 3 帧硬下限防杂散框蒙混。
  - v3.36.1：overlap 规则「目标框扩边」`object_margin`（归一化 0~0.2，解析钳位）——动作发生在目标框边缘外侧的几何盲区（扫工件下沿条码，物理接触但框不相交）用虚拟外扩桥接；扩边只参与"是否相交"，重叠深度门槛仍按原始目标框算，避免稀释"必须压进去"的语义；纯空间几何量与帧率无关。
- `source_region_events_mixin.py`（补录 + 增量，条目见 1.4 表）：v3.32.0 收尾——动作确认补喂 Monitor 步骤面板（与上一动作间隔、SOP 卡片截图 `_region_capture_screenshot` L176 裁确认瞬间主体框，JPEG q70 + base64 与步骤状态机同格式前端零适配）；closed/结算动作落周期 PT 合并档（`_accumulate_step_pt`，只给本周期已接纳的动作落账防跨周期残段污染）；频闪诊断 `_diag_region_flicker`（L60）复用 custom_mix 环形缓冲 + 自动转储，监视全部规则涉及类别。

---

## 二、模块级综合

### 2.1 VSM 生命周期（简图）

```
启动(main) → channel_manager 创建 VSM[ch]
         → auto_load_active_project → set_project_config
         → auto_restore_video_sources → start_camera/rtsp/...
         → (可选) start_detection → _start_inference_thread + _ensure_session_active

运行中:  capture_thread(_capture_loop) ──帧──→ inference_thread(_inference_loop)
                                              └──→ _update_step_stats / _update_tracking_stats / per_item / region_events
                                              └──→ 结算 → _trigger_event → end_cycle ──作业──→ persist-chN 落库线程(v3.38)
                                                                                              └──→ 写库 → MES Hook → 插件 → 协调器

停止: stop_detection → stop_inference/recording/scanner/alarm_idle
     stop/exit → end_session (先 persist.flush) → cleanup_on_exit
```

**Session/Cycle 关键路径**（`source_session_lifecycle_mixin.py`）：
- `start_detection`（source.py L1472）→ `_ensure_session_active()` 懒开 session
- `_trigger_event` L197 → **`end_cycle(is_good, event_id, ...)`**
- v3.38 起 end_cycle 内部分两段：同步段（推理线程）定案 final_* + 更新内存态；写库与后置链打包值快照作业进本通道 FIFO 落库线程（见 `source_persist_worker.py` 条目）。**"周期进行中"真相源是 `current_cycle_uuid`**（同步段即置位/清空），`current_cycle_id` 由建行作业异步回填、只做展示与关联
- MES 异步：cycle_end 作业内调 mes_hooks enqueue → worker **`_handle_cycle_end`**（mes_hooks，行号见 MES 域笔记）

### 2.2 6 logic_mode × 4 settlement_mode 分发点（v3.41 复核）

**logic_mode 枚举**（项目配置）：`sequential` | `detection` | `custom` | `tracking` | `per_item` | `weighing`（设备驱动，不走 YOLO 主循环）。另有 **区域事件模式**（v3.32，pipeline_config.region_events 配置驱动，`_region_event_engine` 非空时替代步骤状态机，见 1.4/1.5 补录条目）。

> v3.35/v3.39 起 weighing 有三档驱动模式（pipeline_config.weighing.drive_mode）：`scale`（v3.31 纯秤驱动）/ `step_gate`（v3.35 融合——logic_mode 仍为 sequential，视觉 SOP 为周期主线，秤只做步骤完成门控）/ `pipeline`（v3.39 两阶段流水线——秤上称重结算 + 秤下收尾动作结案）。后两档都要求视觉喂帧（`_weighing_visual_feed`）。

**settlement_mode 枚举**（pipeline_config）：`first_step` | `last_step` | `last_first` | `cross_cycle`（v3.8.x 类二，与 last_first 互斥）

| 分发点 | 行号 | 行为 |
|---|---|---|
| `_update_step_stats` 入口 | step_stats L60+ | per_item 分流；pending_ack 阻塞（v3.34 起支持 ack_keep_cycle 超时保留周期）；v3.35 起喂称重视觉守卫 |
| `_update_region_events` | region_events_mixin L28 | 区域事件模式每帧入口（`_inference_loop` L357 附近分流，引擎替代步骤状态机） |
| `_settle_for_cross_cycle` | settlement L754-772 | **核心分发**：custom+seq→`_settle_custom_cycle`；sequential→`_settle_sequential`；detection→`_settle_detection`；custom→`_settle_custom` |
| `_process_last_first_mode` | settlement L774+ | 仅 settlement=last_first + seq-like；R1 末步锚结算 |
| `_process_cross_cycle_groups` | settlement L548+ | 仅 cross_cycle=true 守门 |
| `_device_gate_hold` | settlement L1120 | v3.35 步骤外设门控：新出现步骤先"武装"秤门控，放行前不入周期 |
| `_check_events` | events_check L41-143 | 按 logic×settlement 决定何时触发结算事件 |
| `_inference_select_and_run_model` | inference_loop L41+ | tracking→track；seg→segment；其余 detect_only |
| `apply_project_config` | project_config_apply L555（L280 写 settlement_mode） | last_first 清 strict_order（L313+）；last_step 移除结算步；v3.35+ 称重登记/门控/视觉守卫解析 |

**weighing/per_item/tracking/区域事件** 各自有独立状态机，不经过 `_settle_for_cross_cycle`。

### 2.3 `_trigger_event` 触发点清单

**定义**：`source_event_trigger_mixin.py` L64 — 守门（pending_ack、settle_dedup、ng_protect）→ NG 补做 defer → **`end_cycle`** → 计数器/MES/报警/插件 hook

| 来源模块 | 典型行号（v3.41 复核） | 场景 |
|---|---|---|
| `source_settlement_mixin` | L95,188,247,331,464,501,1576 | 各模式结算 OK/NG |
| `source_sequential_mixin` | L118,132,311,421 | 顺序 NG/OK |
| `source_events_check_mixin` | L92,157 | 事件 FSM 步骤完成 |
| `source_checklist_mixin` | L293-302 | counting 模式齐套/顺序 |
| `source_container_grouping_mixin` | L476,483 | 单箱 OK/NG |
| `source_step_stats_mixin` | L236 | 静态步骤触发 |
| `source_session_lifecycle_mixin` | L484,1663,1723 | 强制超时/空闲 NG |
| `source_event_trigger_mixin` | L660,669 | NG 补做 resolve |
| `source_per_item_mixin` | L486,1325,1345 | 逐件完成/NG |
| `source_region_events_mixin` | L169（`_region_settle`） | v3.32 区域事件结算规则确认 |
| `weighing_engine._do_alarm` | weighing 引擎内 | 缺料/超量/前置校验 |

**`_handle_cycle_end`**：不在 source 族内，在 `backend/services/mes_hooks.py`（基线 L1369，行号以代码为准）— v3.38 起由落库线程内的 cycle_end 作业调 enqueue（此前在推理线程）；debug.py L95 可回放其后半段。

### 2.4 `__getattr__` 路由表（P7 兼容层）

```python
# source.py L262-269 _COMPONENT_ROUTES
('drawer',          '_DRAWER_FIELDS',   '_DRAWER_METHOD_ALIASES')      # kalman, draw_box
('mp_overlay',      '_MP_FIELDS',       '_MP_METHOD_ALIASES')        # _mp_*, init/release/overlay
('counters_mgr',    '_COUNTERS_FIELDS', '_COUNTERS_METHOD_ALIASES')  # counters, persist
('video_transform', '_VT_FIELDS',       '_VT_METHOD_ALIASES')        # rotation/flip, 坐标映射
('inference_exec',  '_IE_FIELDS',       '_IE_METHOD_ALIASES')        # ThreadPoolExecutor
('sequence_labels', '_SEQ_FIELDS',      '_SEQ_METHOD_ALIASES')       # 7 个标签查询
```

**规则**（L272-300）：组件实例名本身访问会 AttributeError；字段/方法别名透明转发；MediaPipe 4 个 public 配置字段留在 VSM 本体。

### 2.5 ChannelManager 清理链（`set_channel_count` 缩容）

顺序（channel_manager L109-144，每步 try/except 隔离）：

1. `mgr.end_session()` + `mgr.stop()`
2. `get_mes_hook().on_channel_removed(cid)`
3. **`alarm_router.on_channel_removed(cid)`**
4. `channel_group_coordinator.on_channel_removed(cid)`
5. `workpiece_flow_coordinator.on_channel_removed(cid)`

缺任一步 → MES dict 残留 / 报警串口未释放 / 协调器幽灵工位。

### 2.6 线程全景

| 线程 | 创建位置 | 职责 |
|---|---|---|
| `_thread` capture | camera_start L306+ / source | `_capture_loop` 读帧 |
| `_inference_thread` | source L1111 `_start_inference_thread`（v3.32 起加启动锁 + 代数，任意时刻至多一条存活） | `_inference_loop` GPU 推理 |
| `persist-chN` 落库线程 | persist_worker L100 `_ensure_thread`（懒创建，每通道一条，v3.38） | 帧循环写库作业 FIFO 执行（cycle/step/录像元数据 + MES/插件/协调器后置链） |
| `_recording_thread` | recording_thread L76 | FFmpeg 写盘 |
| `_alarm_thread` | alarm L133 | 蜂鸣器节奏 |
| MediaPipe worker | mediapipe L425 | 骨架推理 |
| `_inference_executor` pool | inference_executor L39 | 同步推理任务 max_workers=1 |
| 扫码器旁路 SN 监控 | main L1093 → `services/scanner_bypass_monitor`（v3.38） | 定期扫描实时导出规则输入目录缓存当前 SN |
| main 定时器 | main L728+ | 24h 清理/每日清理/外设日志/token |
| CUDA warmup | main L504 | 启动预热 |
| shutdown delayed | main 关机 API 内 | 延迟 os._exit |

**锁矩阵（VSM 级）**：frame_lock、capture_lock、detection_lock、_inference_frame_lock、_inference_start_lock（v3.32 推理线程唯一性）、_confirmed_detections_lock、_progress_lock、_settle_lock(RLock)、_step_writers_lock、_writer_lock、router.gpu_lock/warmup_lock（跨模型串行）。

---

## 三、疑点清单

| # | 疑点 | 依据 | 建议 |
|---|---|---|---|
| 1 | **RenderMixin / StreamingMixin 未进 VSM MRO**，但 source.py 主类仍保留 `generate_mjpeg`/`get_frame` 等，与 streaming_mixin 重复 | grep MRO vs 主类 L1745+ | 确认是否计划删除主类重复实现或把 mixin 接回 MRO |
| 2 | **TrackingMixin 在 MRO 最前**，其方法若与子 mixin 同名会优先 — 目前无冲突但新增方法需注意 | source.py L188 MRO 顺序 | 新 mixin 方法命名避免与 Tracking 前缀冲突 |
| 3 | **`_handle_cycle_end` 在 mes_hooks 不在 source 族**，读码笔记范围外但 `_trigger_event` 强依赖其异步语义 | mes_hooks L1379 | 读 MES 笔记时补全 enqueue/ critical 队列语义 |
| 4 | **weighing 模式与 VSM 并行存在**：logic_mode=weighing 时 VSM 仍可能跑 YOLO（若 start_detection），两者边界靠 project 配置 + engine.is_weighing_channel；v3.35 融合/v3.39 pipeline 后并行反而是设计前提（视觉 SOP/标签提取靠 VSM） | weighing_engine L1193-1195/L1257 | 确认 scale 模式项目是否应禁止 VSM start_detection |
| 5 | **settlement_mode 第四个值 cross_cycle** 与 last_first 互斥，前端+apply 双重校验，但 `_process_cross_cycle_groups` 与 `_process_last_first_mode` 共用 `_blocked_labels` | settlement L788-789 注释说互斥 | 配置脏数据时行为未实测 |
| 6 | **proxy `video_manager`** 默认 ch0，main.py shutdown_step 仍部分走 ch0 兜底路径 | main L1123+（v3.41 复核） | 多通道关机是否应全走 channel_manager 循环（已知 v2.7.3 已改大部分） |
| 7 | **source_routes get_detection_results 巨型函数**，轮询热路径（v3.32 后又并入区域事件 in-flight/快照，继续膨胀） | source_routes L1404 | 性能/可维护性风险，值得单独 profiling |
| 8 | **PeriodicActionsMixin 888 行** 与主程序计数器/事件交叉，reset_stats 需同步 `_periodic_*` 字段 | source.py L1652-1672 | 改 periodic 规则时检查 reset_stats 清单 |
| 9 | **InferenceRouter 多模型** Step 7-9 迁移中，老字段 `self.model` 与 `models['main']` 双写，property 虚拟化未完成 | state_init L77-94 注释 | 读 model_load_mixin 时注意 _mirror_host_to_main |
| 10 | **custom_mix 1076 行** 与 container_grouping/per_item 状态交叉，reset_stats 需 `_custom_mix.reset()` | source.py L1726-1731 | custom 项目切换/清零测试矩阵 |

---

*文档版本：2026-07-05 首轮通读 · 2026-07-17 v3.41 增量复核（v3.33~v3.41 source 域补账；weighing_engine.py 条目已由 MES 域补账同步扩写至 v3.39 两阶段流水线全貌）· 下一步建议：补 `mes_hooks.py` 逐行跟读*
