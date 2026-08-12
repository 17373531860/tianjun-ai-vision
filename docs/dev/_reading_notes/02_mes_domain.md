# MES 域读码笔记

> 读码时间：2026-07-05。行号以当日工作区代码为准。
> 用途：撰写开发者文档（MES 链路深潜篇 + 集群深潜篇 + 架构总览）的唯一原料。
>
> **完成度：全部完成（2026-07-05）**：services 层（mes_hooks / scanner / mes_gateway / mes_inbound / mes_puller / cluster_collector / packaging_flow_coordinator / workpiece_flow_coordinator / wmax 目录 7 文件 / external_device 族 5 文件 + external_alarm）与 api 层 10 个 MES 路由全部精读落盘，并附「二、MES 域综合」与「三、疑点清单」（15 条）。深潜正文 `internals/mes-hook-pipeline.md` 与 `internals/cluster-collector.md` 已从源码直接撰写，不依赖本笔记全文。
>
> **v3.41 补账（2026-07-17）**：按 `git diff a23a8d2..HEAD` 回写 v3.33~v3.41 变更——mes_hooks（v3.38 运行中开工回填）、mes_gateway（v3.38 推送熔断器 + 逐连接独立 commit）、mes_inbound（v3.37 开工自动开始检测 / v3.38 回填 / v3.39 报警软件内消除出口与响应明细 / v3.41 复用工单重绑）、packaging_flow_coordinator（v3.35 复合条码取段 + 工单号识别 + 放工单=尾箱收尾动作）、external_alarm（v3.39 一键消除）、mock 秤墙钟计时（v3.41），并新建 database_adapter（v3.35）与 scanner_bypass_monitor（v3.38）条目；api 层三节同步刷新（11.2 熔断器状态面 / 11.3 报警手动消除端点 / 11.10 复合条码配置面）。标注「v3.41 复核」的小节行号已按当日工作区刷新。weighing_engine 的 v3.35 融合 + v3.39 两阶段流水线全量扩写在 `01_backend_core.md`；mes_models（PackagingFlowConfig 复合条码字段）增量在 `03_data_plugin.md`。
>
> **v3.43 补账（2026-07-20）**：packaging_flow_coordinator 大改（放工单=工单收尾语义重构 + 缺工单判定二选一 + 提前放工单报警 + 已完成工单重扫拦截 + 确认框闪退修复），见其条目「v3.43 大改」节；PackagingFlowConfig 新增 4 列见 `03_data_plugin.md`（迁移 m0002/m0003）。
>
> **v3.44 补账（2026-07-22）**：
> - `packaging_flow_coordinator.py`：①NG 箱账挂起——`on_cycle_settled(_sliders)` 加 steps_ok/hold_for_ack 双参，NG+需人工确认时箱账进 `pending_remediation(reason=ng_ack)` 不落账不翻页，`resolve_channel_hold_on_ack`（认NG落账 `book_pending_ng`/重做本箱）由 ack 接口收口；②补数量死分支修复——少装挂起入口改判 steps_ok（步骤全对数量不足）而非整体 is_good；③工单收尾快照——完成/作废存 `_last_done`，新增 `get_display_state`（UI 轮询用，快照保留至新单顶掉），`get_state` 语义不变（在途判定用）。
> - `api/packaging_flows.py`：state 端点改走 `get_display_state`。
> - `external_device_protocols.py`：串口读"有多少收多少"（`in_waiting`）+ `_read_serial_frame` 见帧尾立即交货（无分隔符 ~60ms 静默兜底），治秤读数 2~3s 延迟（萍乡）；`external_device_pipeline.py` 负重量直读符号位解析。
>
> **v3.45 补账（2026-07-29）**：
> - `packaging_flow_coordinator.py`：①箱标签扫码授权（组⑧，FEAT-002）——「等扫箱标签」态 waiting_label / `_extract_label_qty` / `_authorize_box_by_label` / `on_cycle_started`（未扫开做报警）/ 收尾对账 label_total_check，见其条目「v3.45 变更」节；②包装工单镜像进工单管理（FEAT-003）——`_sync_work_order` upsert work_orders 表，默认开、异常隔离。
> - `api/packaging_flows.py`：Pydantic 面加组⑧ 10 字段 + `sync_work_orders`。
> - `database_adapter.py`：`_dm_bind_safe` 达梦 32 位整型溢出降级字符串绑定 + 连接端口守门（BUG-009）。
>
> **v3.48 补账（2026-08-10，SY9 + tianyong + dev-qing 三分支汇合发版）**：
> - **`external_device_pulse.py`（新，400 行，dev-qing）**：外设新协议 `modbus_pulse`——「Modbus 完成脉冲」设备（PLC 控气阀等）。设备线程持 Modbus TCP/RTU 连接，暴露 `enqueue_pulse()` 只入队即返回（调用方在推理线程，见 01 册 `source_per_item_mixin`）；脉冲=写线圈 ON→保持 pulse_ms→写 OFF，`cooldown_ms` 冷却窗内丢弃重复请求；断线自动重连、失败落 `last_error` 不外抛。触发模式 `trigger_mode`：`all_covered`（逐件首次全覆盖）/ `cycle_ok`（周期判 OK）。
> - `external_device.py` / `external_device_models.py` / `external_device_protocols.py`：注册 `modbus_pulse` 协议 + `notify_per_item_complete(channel_id, trigger_mode)` 分发入口（按工位绑定 + trigger_mode 同名匹配，无配置纯 no-op）；面板试发端点。对接文档 `docs/PLC完成脉冲对接指南_台达ES3.md`（+附录A PLC 侧程序）。
> - `mes_hooks.py`（+12 行，tianyong）：cycle_start / cycle_end 等触点挂 `dispatch_plc_event()`（RFC 13 事件写回，enqueue 级非阻塞、内部全兜底永不抛——遵守"Hook 队列不阻塞热路径"不变量）。
> - `mes_models.py`：`PackagingFlowConfig` 新增 `tail_paper_only_after_awaiting`（Boolean 默认 False，迁移 **m0009**）——放工单只认"等收尾之后"的出现（SY9，治尾箱周期里误检一次就当场收尾）。
> - `packaging_flow_coordinator.py`（SY9）：尾箱落账先挂「等放工单收尾」，开关开时历史检出一概不算、非等待态检出整条丢弃（连提前放工单报警也不报）；`api/packaging_flows.py` Pydantic 面同步透出。
> - PackagingFlowConfig 新 11 列与迁移 m0004/m0005 见 `03_data_plugin.md`。
>
> **v3.47 补账（2026-08-07）**：MES 域本轮仅一处触碰——`mes_models.py` `DefectRecord.cycle_id` 补索引（`index=True`，老库走迁移 `m0007_defect_cycle_index`），为开机首启的孤儿缺陷扫描按 cycle_id 关联探查提速（feat/system-optimize 六项之一）。services/api 层零变更。
>
> **v3.49 补账（2026-08-12，捷昌整改批次 WS1~WS4）**：
> - `mes_hooks.py`（→2104 行）：①**WS1 外推并发派发**——B1② 执行器族从"每工位"重构为**每网关连接**独立 `ThreadPoolExecutor(1)`（同连接 FIFO 保序、连接间并发，慢/挂连接不再互堵），积压超上限落盘 **`gateway_spool.jsonl`** 恢复后补发（与 `mes_hook_spool.jsonl` 分文件），`mes_async_dispatch` 默认值改**开**；box_complete 集群汇总推送同走异步。②**WS3 scan_pair 新码先上屏**——`_handle_scan_pair_event` 新序：先 `_scan_pair_promote_pending` 顶替上屏+提交扫码状态，再用**显式 `prev_wp_id`/`prev_scanned_at`** 调 `_dispatch_scan_pair_settle` 异步结算旧窗口（顶替后 `_inspecting_workpiece` 已是新工件，结算身份必须走显式传参不能回读）；开关 `scan_pair_new_code_first`（SystemConfig，默认开，关=回退旧序）。③**广播兄弟通道结算串身份修复**——广播超时/停止结算身份改 `_inspecting_workpiece.get(channel_id) or entry.get("wp_id")`，每通道各归各账（老代码统一拿主通道 wp_id）。④**WS4 耗时埋点**——扫码处理/窗口结算/外推派发关键路径打 `backend.timing` 分段耗时点（debug_center 新类目，见 05 册）。
> - `mes_gateway.py`（→881 行）：每连接**重试预算** `retry_budget_sec`（读自连接 **config JSON**，非顶层列；0=不限），重试循环按预算钳制总耗时，防单条推送重试黑洞。
> - `cluster_collector.py`（→1428 行）：**WS2 副机上报异步化**——`report_to_master` 改独立发送线程+内存队列（结算路径只入队即返回），失败落盘 **`cluster_report_spool.jsonl`** 恢复后按序重放；`report_timeout_sec`/`report_async` 进 ClusterConfig 可配（默认异步开/10s）；新增 `get_report_queue_status()`（queued/spooled/spool_replayed_total）。
> - api 层：`mes_gateway.py` 新增 `GET/PUT /async-dispatch`；`scanner.py` 新增 `GET/PUT /scan-pair/new-code-first`；`cluster.py` 新增 `GET /report-status` + `/config` 面扩 `report_timeout_sec`/`report_async`。
> - 回归：`test_mes_async_dispatch_b1b.py`（扩）/ `test_mes_gateway_dispatch_isolation.py`（扩）/ `test_cluster_report_async.py`（新）/ `test_scan_pair_new_first.py`（新）/ `test_scan_pair_three_window_gold.py`（新，三窗金标准含双通道广播）/ `test_timing_probe_ws4.py`（新）。
>
> **v3.50 补账（2026-08-12，捷昌二期：扫码器生命周期"码-合格-码"闭环）**：
> - `services/scanner.py`（→约 2100 行）：①`ScannerConnection` 加 4 字段——配置面 `resume_on`（'cycle_end' 默认 / 'ok_only'）/ `rearm_forget_last` / `strict_ok_dedup` + 运行时 `_resume_blocked`；`_start_device` 从 ORM 装填。②`resume_after_cycle(channel_id, is_good=None, manual=False)` 重构——ok_only 且非人工且 `is_good is not True`（NG/未知一视同仁）→ 置 `_resume_blocked` 保持灭灯 continue；成功恢复清 `_resume_blocked` 并按 `rearm_forget_last` 调 `_rearm_forget`。③`_rearm_forget`：清 `conn.last_scan/last_scan_time`（物理去重缓存）+ `mes_hook.clear_pending_scan(ch, force=False)` 作废未绑定旧码。④新增 `resume_scanning_manual(ch)`（= manual=True 转发）与 `is_resume_blocked(ch)`（工位级查询，source_routes 的数据源）。⑤`_resume_blocked` 与 `_wait_cycle_resume` 同步清理点：`_catch_up_lons` / `apply_channel_disable_change` / `start_scanning` / `stop_scanning`。
> - `services/mes_hooks.py`：①`_get_strict_ok_dedup(ch)` 从扫码器连接取配置；②`_emit_scan_warning(ch, sn, reason)` 统一警告事件（`_last_scan_event` 带 `scan_warning=True + warn_reason`）；③`_handle_scan` 三条拒绝路径改发警告不再静默——strict_ok_dedup 查库已 OK 永久拒（新增，含 ScanLog 落账）/ OK 冷却拒 / duplicate_scan_action=reject 拒；④scan_pair 重复码 toast 并入统一字段（旧 `scan_pair_dup_warning` 保留兼容）。
> - `models/mes_models.py`：`ScannerDevice` 加 `resume_on / rearm_forget_last / strict_ok_dedup` 三列（迁移 **m0010**，SQLite 默认 0 / PG FALSE 分道，默认值=现状零差异）。
> - `api/scanner.py`：Create/Update Schema + `_serialize_device` 透出三字段；新增 **`POST /scanner/resume?channel_id=N`**（人工恢复，无条件解除灭灯锁）。
> - `services/triggers/actions.py`：全局动作注册表加 **`resume_scanner`**（= `resume_scanning_manual`，脚踏板/PLC 的 NG 灭灯出口，见 05 册动作表）。
> - 上游联动：`end_cycle` 传 `is_good`、`source_routes` 透出 `scanner_resume_blocked`（见 01 册 v3.50 补账）；前端 ScannerPanel/Monitor 见 04 册。
> - 回归：`test_scanner_lifecycle.py`（新 16 用例）/ `test_settle_on_complete.py`（新 21 用例）/ BDD `settle_scan_lifecycle.feature`（8 场景）/ e2e `test_settle_scan_lifecycle.py`（6 用例）。

## 一、逐文件档案

### 1. backend/services/mes_hooks.py（2104 行，v3.49 复核）

**职责一句话**：MES 检测引擎回调管理器——检测状态机（source.py）与 MES 子系统（工件/工单/缺陷/外推/集群）之间的唯一异步桥，5 个 hook 全部经内存队列由单条后台线程消费，保证不阻塞检测帧率。

**核心类·函数**（类 `MESHookManager`，L34；全局单例 `get_mes_hook()` L1735）：
- `__init__` L53：初始化全部 channel 维度状态 dict + 任务队列（`queue.Queue(maxsize=500)` L85）+ 落盘补偿文件 `mes_hook_spool.jsonl`（L90）。
- `start()` L124 / `stop()` L136：起/停 `mes-hook-worker` 守护线程；stop 时顺带关停 B1② 每工位外推执行器（不等在途任务）。
- `_worker_loop` L369：单线程消费循环——每轮先 `_drain_spill_once(max_items=20)` 回放落盘任务，再 `get(timeout=1.0)` 取任务；每任务独立 `SessionLocal()`，成功 commit、异常 rollback 只记日志。
- `_enqueue` L490：入队总闸（详细语义见二.3）。
- `_spill_task` L404 / `_drain_spill_once` L425 / `_is_spillable_handler` L394：critical 任务落盘/回放；白名单 = `_handle_scan / _handle_cycle_start / _handle_cycle_end / _handle_session_start / _handle_session_end` 五个。
- 5 个对外 hook：`on_scan_received` L519（ScannerService 调）、`on_cycle_start` L534、`on_cycle_end` L542、`on_session_start` L555、`on_session_end` L562（后四个都由 source.py 在 commit 成功后调）。全部 critical=True 入队。
- `on_external_order_changed` L568（**v3.38 变更**，川南反馈）：入站开工建单/顶替后给"检测已在跑"的工位回填活跃工单——没有它，会话开始早于建单的工位永远挂不上新单（监控页四要素不显示、周期不计入该任务，要停一次检测才生效）。critical 入队 `_handle_rebind_active_order`。
- `_handle_rebind_active_order` L1697（v3.38，工作线程执行）：运行中工位活跃工单回填，与 session_start 绑定同一套匹配。规则对齐"最新开工为准"已有语义、不引入新开关：工位没挂单→无条件补挂；已挂旧单且 allow_replace=False→保守不动；allow_replace=True（顶替开）→切最新在产单。顺带把 DetectionSession.order_id 一起改。上游调用点在 mes_inbound `_rebind_running_channels`（§4）。
- `on_channel_removed` L578：降工位清理 7 个 dict + scan_pair 定时器（对应 AGENTS.md 不变量 4）。
- `_handle_scan` L1181：扫码事件核心处理（WorkpieceFlow 优先路径 L1191→OK 冷却过滤 L1218→duplicate_scan_action reject/queue/overwrite→注册工件→ScanLog→scan_mode='D' LOFF L1289→scan_pair 分流 L1301→mid_cycle 即时绑定 L1306）。
- `_handle_cycle_start` L1329：拍导出快照（v3.7.2 cycle_start_snapshot）→ pop pending 工件 → `mark_inspecting` + `link_to_cycle` → 写 `_inspecting_workpiece[ch]` → cycle 关联工单。
- `_handle_cycle_end` L1379：**全文件最重的方法**——反查 wp_id（优先 WorkpieceInspection by cycle_id，v3.4.2 hotfix-2）→ 迟到扫码补绑兜底（v2.7.16 B）→ 无码周期仍推 MES（v3.7.0）→ set_result/缺陷 auto_record/工单 +1 → **预 commit 释放 SQLite 写锁（v2.7.9，L1522）** → `_cluster_dispatch` → gateway 推送（可 async）→ 实时导出 → rebind 策略。
- `_cluster_dispatch` L1595：集群分发（master 本地存 / slave 上报主机；返回 True=wait_all 模式跳过本机直接推送）。channel→station 映射：优先 `channel_station_map`，否则多通道拼 `-{channel_id}` 后缀（L1622-1629）。
- `_handle_session_start` L1669：查活跃工单绑 `_active_orders[ch]`（cluster scope 工单不在这里绑，box_complete 时直接 +1）。
- `_handle_session_end` L1732：清 channel 状态 + session_end 外推 + session_end 实时导出。
- scan_pair 家族（v3.3.0 码-码闭环）：`is_scan_pair_mode` L743、`_handle_scan_pair_event` L885、`_scan_pair_promote_pending` L967、`_dispatch_scan_pair_settle` L833（反向调 source 的 `settle_for_scan_pair`）、`_arm/_cancel_scan_pair_timer` L797/L788、`_on_scan_pair_timeout` L812（超时强制 NG 结算）、`settle_scan_pair_for_stop` L862（前端停止/待机弹窗收尾）。
- 禁用扫码家族（v3.4.2 按工位开关）：`set_channel_disabled` L311、`_compute_linked_channels` L286（联动闭包=覆盖该工位的所有扫码器的全部 broadcast 工位并集）、`is_channel_scan_disabled` L276；状态持久化到 `scanner_runtime_state.json`（L120）。禁用后 4 个守门点全部短路：`on_scan_received` 丢码 L525、`has_pending_workpiece` 返 True L606、`is_scan_required` 返 False L617、`is_warn_no_barcode` 返 False L632、`get_current_workpiece` 返 None L674、`is_scan_pair_mode` 返 False L747。
- B1② 异步外推家族（默认关）：`_load_async_dispatch_config` L156（SystemConfig key=`mes_async_dispatch`）、`set_async_dispatch` L177、`_submit_gateway_dispatch` L198（每工位 `ThreadPoolExecutor(1)`，同工位 FIFO 保序，积压上限 `_GATEWAY_MAX_PENDING=200` 超了丢最新+告警）。
- 状态查询（供 detection results 轮询）：`get_current_workpiece` L670、`get_active_order` L700、`get_last_scan_event` L666、`get_rebind_prompt` L1065、`has_any_scanner_present` L648（v3.5.2：无任何扫码器则不弹"未绑码"）。
- `clear_pending_scan` L1069（v2.7.16 五场景清码；force=True 作废 inspecting + 回退工件 queued + 删 WorkpieceInspection；原子 pop 防与 worker 竞争，输给了返回 `force_race_lost`）。
- `resolve_rebind` L1161：manual rebind 弹窗的 continue/new 选择处理。

**线程·锁·队列**：
- 单条 `mes-hook-worker` 守护线程（L130）消费 `_task_queue`（`queue.Queue(maxsize=500)`）。
- 每工位一条 `mes-gw-ch{cid}` 外推线程（B1② 开关开时才用，`ThreadPoolExecutor(max_workers=1)`）。
- scan_pair 每工位一个 `threading.Timer` 超时定时器（daemon）。
- 锁：`_scan_pair_lock`（Lock，护 `_scan_pair_active`）、`_spill_lock`（护落盘文件）、`_gateway_exec_lock`（护执行器 dict）、`_disable_state_lock`（RLock，护禁用集合）。
- **注意：`_pending_workpiece` / `_inspecting_workpiece` / `_pending_queue` 等核心 dict 无锁**——依赖"写基本都发生在单条 worker 线程"这一约定；HTTP 侧 `clear_pending_scan(force)` 用"原子 pop"对冲竞争（L1097 注释明说）。

**上下游**：
- 上游：ScannerService（扫码）、source.py VideoSourceManager（cycle/session hook）、前端 API（禁用扫码/清码/rebind/scan_pair 停机结算）、mes_inbound `_rebind_running_channels`（v3.38 运行中开工回填，调 `on_external_order_changed`）。
- 下游：WorkOrderService / WorkpieceService / DefectService（DB 写）、mes_gateway（外推）、cluster_collector（集群）、export_realtime + export_snapshot（自定义导出）、workpiece_flow_coordinator（RFC11 优先路径）、channel_manager（反查 cycle/session id、scan_pair settle、scan_mode D 的 LOFF）。

**关键状态变量（dict 键 = channel_id）**：
| 变量 | 值 | 语义 |
|---|---|---|
| `_pending_workpiece` | wp_id | 最近扫码但尚未开始检测的工件 |
| `_pending_queue` | [wp_id...] | duplicate_scan_action='queue' 的待检队列（FIFO 上限 500，`_enqueue_pending` L38） |
| `_active_orders` | order_id | 当前活跃工单 |
| `_inspecting_workpiece` | wp_id | 当前正在检测的工件（AGENTS.md 不变量 14：放入/取出必须严格配对） |
| `_last_scan_event` | {serial_no, workpiece_id, timestamp[, scan_pair_dup_warning]} | 最近扫码事件（前端轮询去重；迟到补绑依据） |
| `_rebind_prompt` | {workpiece_id, cycle_id, timestamp} | manual rebind 弹窗数据 |
| `_scan_pair_active` | {serial_no, wp_id, scanned_at} | scan_pair 当前窗口的"开始码" |
| `_scan_pair_timeout_timers` | Timer | scan_pair 超时定时器 |
| `_gateway_executors` / `_gateway_pending` | executor / 计数 | B1② 每工位外推执行器与积压数 |
| `_disabled_channels` | set[int]（非 dict） | 禁用扫码的工位联动闭包 |

**_inspecting_workpiece 取放点清单（不变量 14 交叉标注）**：
- 放入：`_handle_cycle_start` L1363、`_handle_scan` mid_cycle 分支 L1318、`_scan_pair_promote_pending` L985。
- 取出：`_handle_cycle_end` L1409-1417（优先按 cycle 反查后条件清除，fallback pop）、`_handle_scan_pair_event` settle 后 L943、`_scan_pair_promote_pending` 防御性 pop L978、`clear_pending_scan(force)` L1111、`_handle_session_end` L1737、`set_channel_disabled` L348、`on_channel_removed` L587。

**注释里的坑（原样摘录）**：
- L96-101（B1② 背景）："cycle_end 的外推 gw.dispatch() 自带阻塞式重试…跑在单条 mes-hook-worker 线程上; 客户 MES 慢/挂时这一下就把整个 hook 队列(扫码配对/绑工件/...)全堵住"。
- L502-505（B1①）："队列满时绝不阻塞调用方(结算/检测热路径)。原来 critical 会 put(timeout=0.8) 阻塞最多 0.8s, 队列长期满时每个周期都卡 0.8s, 直接拖垮结算节拍。改为: 关键任务立刻落盘…非关键直接丢弃"——即 AGENTS.md 不变量 15 的出处。
- L1386-1395（v3.4.2 hotfix-2）："ScanPair 模式下, settle_for_scan_pair 触发 end_cycle 是同步链, 但本方法被丢进 worker queue 异步跑. 等 worker 拿到 _inspecting 时, _handle_scan_pair_event 已经 promote 把 _inspecting 改成'新码 wp'了 → pop 出来的是新码 wp_id, 上一码的结算结果就被错写到新码头上"。
- L1518-1521（v2.7.9）："在调集群分发之前，先 commit 释放 SQLite 写锁。否则…receive_station_report() 在新 session 里写 box_aggregations 会 locked 30s+ 重试失败"。
- L1107-1110（clear_pending_scan force）："原子 pop：避免与 worker 线程的 _handle_cycle_end 抢同一个 wp_id…返回 'race_lost'，前端提示用户'操作来不及，本次工件已结算完成'"。
- L1469-1474（v3.7.0）：无码周期原来直接 return 导致"MES 数据不是实时上传"，修复为无绑定也推 cycle_end、仅跳过工件写操作。
- L1186-1189（RFC11）："若本通道属于某串行流水线 → 走 flow 路径…跳过本通道的 scan_pair / pending_workpiece 状态机 (互斥, 文档明示)"。
- L570-573（v3.38 on_external_order_changed docstring）："没有它, 会话开始早于建单的工位永远挂不上新单: 监控页四要素不显示、周期也不计入该任务, 要停一次检测才生效"。

### 2. backend/services/scanner.py（2003 行）

**职责一句话**：扫码器 TCP 通讯服务——管理多台扫码器（text_lon 文本 LON/LOFF、WMax 三端口二进制、USB 键盘枪、auto 自动探测）的连接/重连/去重/LON 灯控状态机，收到条码后广播到绑定工位并调 `MESHookManager.on_scan_received`，同时注入配对的外部设备（秤）。

**核心类·函数**：
- 模块级 WMax RPT 激活（v2.7.7c hotfix 合并）：`_rpt_activated_devs` set + `_rpt_activate_lock` L38-39；`_activate_rpt_once` L42（同一 dev 按 `id(dev)` 只激活一次，后台线程跑 `activate_rpt_reporting`，挂 `on_disconnected` 回调断开即清理）；`_ensure_wmax_connected` L97（已连→激活 RPT；未连→主动 `mgr.connect` 一次）。
- `_is_expected_connection_error` L128：设备离线类网络错误不打全堆栈。
- `@dataclass ScannerConnection` L140：单台扫码器运行态（配置字段 + `status/_socket/_thread/_stop_event/_scanning/_lon_sent/_wait_cycle_resume/_next_lon_after`）。scan_mode 四值语义见 L163-167 注释：continuous/throttled/once_per_cycle/D。
- 类 `ScannerService` L208；全局单例 `get_scanner_service()` L1999。
- `start_all` L233：DB 加载 enabled 设备逐个 `_start_device`；有 wmax/auto 类型才跑 `_auto_discover_wmax_bg`。
- `_trigger_wmax_discover_once` L255：2s 去抖合并多次发现触发。
- `_auto_discover_wmax_bg` L280：后台线程 UDP 发现 + DB 已知 IP 直连 WMax 管理口（55266）；发现成功后自动注入 55256 文本口虚拟连接（`device_id` 从 -9000 递减，L352-392）。
- `stop_all` L403 / `add_device` L409 / `remove_device` L412 / `get_all_status` L417。
- `_known_channel_count` L435 / `_resolve_bound_channels` L449：解析实际绑定工位列表；超界降级到 ch0 并按 `(name, bad_ch, ch_count)` 快照去重告警（L444-447 注释：实测终端 500 行里 498 行是同一条告警）。
- `_text_lon_send` L481：LON/LOFF 直发（v2.7.16 弃状态机改直接 sendall）。
- `_wmax_trigger` L509：auto/wmax 类型 trigger_on/off（fire-and-forget；trigger_off 不为停止动作同步重连 L531）。
- `_catch_up_lon_for_detecting_channels` L550：v3.4.2 修 reload 时序——扫码器后连上时回扫 detecting 中的工位补发 LON。
- `apply_channel_disable_change` L599：v3.4.2 禁用/启用扫码的物理执行（LOFF+_scanning=False / 逐 ch start_scanning 复活）。
- `start_scanning` L659（检测开始发 LON；守禁用；清 `_wait_cycle_resume`）/ `stop_scanning` L824（LOFF + 清残留 flag）。
- D 模式家族（v3.4.0 容器跨线触发）：`send_lon_for_channel` L718（帧循环脉冲式 LON，不动 `_scanning`）、`send_loff_for_channel` L761（LOFF 不守门，安全兜底）、`is_scan_d_for_channel` L782（ch 禁用→False 让 D 状态机休眠）、`get_scan_d_config_for_channel` L805（返回几何配置给 source 帧循环）。
- `trigger_scan` L851 / `trigger_scan_by_ip` L865 / `_do_trigger_once_wmax` L878 / `_do_trigger_once` L909：手动单次触发（LON→扫到或 10s 超时→LOFF，后台线程轮询 `last_scan_time`）。
- `get_latest_scan` L934。
- `resume_after_cycle` L944：v2.7.16 cycle_end 解锁 once_per_cycle/D 模式的 `_wait_cycle_resume`（v3.4.2 补 D 模式，否则灯永熄）。
- `notify_cycle_settled` L978 / `_dispatch_force_settle` L1010：v3.1.2 广播主工位结算带动其他工位 `force_settle_pending_cycle`（大小件同步结算）。
- `clear_last_scan` L1042：清扫码器侧去重缓存（配合前端"清除本次扫码"，否则重扫同码被 dedup 吞）。
- `test_connection` L1061 / `_test_text_lon` L1172：连通性测试；测试期间 IP 进 `_testing_ips`，测试码不进 MES；WMax 路径结束必须 `turn_on_video(off)` 关灯（L1127-1131）。
- `_detect_device_type` L1216：发 WMax 握手帧，回 0x5A5A 头判 wmax，否则 text。
- `_start_device` L1256：usb_hid 类型直接 return（前端键盘捕获，后端不建连接，L1261）；其余建 `ScannerConnection` + 起监听线程；v2.7.8 起协议选择交还用户不再自动升级（L1264-1272 注释）。注意 `upgraded_from_text_lon` 恒为 False（L1273），L1321-1338 升级分支实际死代码。
- `_stop_connection` L1340。
- `_listen_loop` L1350：主监听循环（指数退避 1→30s 重连；连接稳定 >10s 重置退避 L1447）；auto/wmax 类型不建本地 TCP、交 WMaxDeviceManager 管（L1359-1365）；按类型分流到 4 个子循环。
- `_text_lon_listen_loop` L1457：核心灯控状态机——80ms 短轮询兼做 idle 切帧（某些 WMax 型号 55256 回裸字节无 \r\n，L1460-1466）；`ERROR` 字符串=本轮无码，续 LON 不上报（L1509）；`_schedule_next_lon` L1474 按 scan_mode 决定扫到真码后的续发策略。
- `_text_listen_loop` L1584（普通文本被动监听）/ `_wmax_scan_listen_loop` L1617（55256 被动监听，60s 空闲心跳）/ `_wmax_listen_loop` L1668（二进制 DataReceiver 拆帧 + `decode_rpt_code`，15s 空闲发 LON 当心跳）。
- `_on_data_received` L1732：收码总入口——测试 IP 丢弃 L1743→dedup（命中**滑动时间戳**，L1756-1760）→ BarcodeParser 解析→external_only 只喂外设 L1779→逐 ch 取 project_id（空则跳过）→ `on_scan_received` →注入外设→fire 插件 hook `scan_received`（L1834，早返回路径不 fire）。
- `simulate_scan` L1880：调试注入（无真实硬件走完整链路）。
- `inject_scan_result` L1919：WMax RPT 端口转发注入；来源 IP 未匹配时 0.8s 重试一次（`threading.Timer`，按 (ip,barcode) 去抖）；仍未匹配→单工位降级注入、多工位保护不广播（L1963-1969）。

**线程·锁·队列·定时器**：
- 每台设备一条 `scanner-{id}` 监听线程（daemon）；WMax 自动发现线程 `wmax-auto-discover`；RPT 激活线程 `scanner-rpt-activate-{ip}`；手动触发线程 `trigger-{id}` / `trigger-wmax-{id}`；测试闪灯线程 `scanner-test-{ip}`；未匹配注入重试 `threading.Timer(0.8)`。
- 锁：模块级 `_rpt_activate_lock`（RPT 激活去重 set）、`_wmax_discover_pending["lock"]`（发现去抖）、`_unmatched_retry_lock`（注入重试去抖）、`_testing_ips_lock`（测试 IP 集合）。
- **`_connections` dict 本身无锁**，靠"启动期写、运行期基本只读 + `list()` 快照遍历"约定。

**上下游**：
- 上游：main.py 启动调 `start_all`；ChannelManager/source 调 `start_scanning/stop_scanning/resume_after_cycle/send_lon_for_channel`（D 模式帧循环）；mes_hooks 调 `apply_channel_disable_change`、scan_mode='D' 的 `send_loff_for_channel`；API 层 scanner.py 调 CRUD/测试/模拟；wmax.manager RPT 回调调 `inject_scan_result`。
- 下游：`MESHookManager.on_scan_received`（唯一 MES 入口）、BarcodeParser、external_device 服务（`set_barcode`）、wmax.manager/device（三端口协议）、channel_manager（反查 detecting 状态、强制结算）、插件 hook `scan_received`。

**关键状态变量**：
| 变量 | 语义 |
|---|---|
| `ScannerConnection._scanning` | service 层意图"应处于扫码状态"（LON 续发的总开关） |
| `_lon_sent` | 当前 LON 已发出未消费（收到码/ERROR 后清零触发续发） |
| `_wait_cycle_resume` | once_per_cycle/D：扫到真码后 LOFF 等周期结束（`resume_after_cycle` 解锁）——**多处残留清理是 v3.4.2 修灯永熄的关键** |
| `_next_lon_after` | throttled 模式下次允许续 LON 的 monotonic 时间戳 |
| `_testing_ips` | 正在"测试连通性"的 IP 集合，期间收码全丢弃不进 MES |
| `_rpt_activated_devs` | 已激活 RPT 的 WMaxDevice id 集合（断开自动清） |

**注释里的坑（原样摘录）**：
- L30-35（RPT 激活）："v2.7.7 曾误以为这会让扫码器持续闪光, 改为 ondemand 单次 trigger_on, 结果现场设备根本不识别 (固件不支持 Trigger 单次触发); v2.7.7c 回到'常开'策略, 现场验证扫码器并不会闪, 与 IDManager 行为一致"。
- L484-488（`_text_lon_send`）："v2.7.16: 修复'_lon_sent 状态残留导致 LON 发不出去'的 bug…线程重启时 `_scanning=True` 但 `_lon_sent=True` 残留 → 永远不发 LON…抓包确认零字节出去"。
- L700-703（start_scanning）："必须清掉残留的 _wait_cycle_resume…否则上一次会话扫到码后设的'等周期' flag 会拦本次会话的 ERROR 续 LON, 导致扫码器 LON 5s → LOFF → 永不再亮的死锁"。
- L951-954（resume_after_cycle）："之前漏了 D 模式 → 调用无效, _wait_cycle_resume 死锁导致灯永熄, 这是 D 模式'扫到码后再也不亮'的根因"。
- L1071-1072（test_connection）："v2.7.7c 改成默认走 WMax, 但**没在测试结束时关视频流**, 导致 WMax 设备点了一次'测试'后灯就一直亮, 必须重启设备/断网才能恢复"。
- L1756-1759（dedup 滑动窗口）："dedup 命中时**滑动**时间戳, 否则同码一直在视野里的话, 每隔 dedup_interval_sec 就会'漏放一次'重复触发 MES, 引发 workpiece_inspections UNIQUE 冲突"。
- L1505-1508（D 模式 ERROR 续 LON）："之前 v3.4.1 设计意图是'按 box 跨线脉冲触发', 但实际产线用户期望'开始检测扫码器立即并持续工作', 所以放弃精细脉冲式控制"。
- L1963-1965（未匹配注入）："单工位：允许注入该唯一工位，避免单机场景漏码；多工位：不再广播，避免串工位"。

#### 2.1 backend/services/scanner_bypass_monitor.py（310 行，v3.38 新增）

**职责一句话**：扫码器旁路 SN 监控守护线程（v3.38，扫码器不接软件、只往固定目录丢 `SN123456.txt` 的客户）——定期扫描实时导出规则配置的输入目录，把每通道"当前最新 txt 的 SN"缓存在内存，供前端 Monitor 显示"当前 SN"与 cycle_start 锁快照时"内存优先"（满足"cycle_start 只读内存、不扫目录"的诉求）；是 v3.7.2 扫码器旁路能力（cycle_start 锁定最新 txt→external_meta→导出沿用）的实时侧补齐。**模块级函数式设计，无类无单例**；不依赖 ScannerService，条码也不进 MES hook 链（纯展示/导出侧）。

**核心函数**：
- 配置来源复用 `ExportRealtimeRule`（enabled + input_dir 非空），**不新造表/字段**（模块头 L20）；轮询间隔存 SystemConfig key=`export.scanner_bypass.poll_interval_sec`（默认 1.0s，钳在 [0.2, 60]，每轮重读支持热改，`_read_poll_interval` L57）。
- `_collect_rule_targets` L77：扫启用规则按规范化目录去重；同目录多规则时 wait_stable_ms/max_age_sec 取最严（"宁严勿松"，与 export_snapshot 口径一致，L103）；channel_filter 为空的规则作 default 兜底。
- `_scan_dir` L140：单目录取最新 txt（复用 export_renderer 的 `latest_input_filename/latest_input_text`），SN=文件名去扩展名；任何异常归到 entry status=error **不外抛**。
- `_build_state` L196：跑一轮组装完整状态 dict（by_dir/by_channel/default/entries），交 `_poll_loop` L226 **整体原子替换**（`_STATE_LOCK` 护），整轮级异常只记 error 字段线程绝不退出。
- 生命周期：`start_monitor` L250（幂等）/ `stop_monitor` L263（`_STOP_EVENT` + join 2s）。
- 只读查询：`get_status_snapshot` L280（API `GET /export/scanner-bypass/status` 用）、`get_current_for_channel` L294（无专属规则回退 default）、`get_current_for_dir` L304（export_snapshot 内存优先入口）。

**线程·锁**：单条 daemon 线程 `scanner-bypass-monitor`；`_STATE_LOCK`（护状态整体替换与读取）+ `_THREAD_LOCK`（护启停）。目录扫描全在本线程做，API/cycle_start 只读内存（模块头 L14 设计约束）。

**上下游**：
- 上游：main.py 启动 start_monitor；`backend/api/export_custom.py` 的 scanner-bypass 状态端点；export_snapshot 拍 cycle_start 快照时内存优先。
- 下游：ExportRealtimeRule（配置）、SystemConfig（轮询间隔）、export_renderer（latest_input_* 复用）、文件系统目录。

**关键状态变量**：模块级 `_STATE` dict（running/polled_at/by_dir/by_channel/default/entries/error）——进程内存态，重启即清、下一轮扫描重建。

**注释里的坑（原样摘录）**：
- L14-18（设计约束）："不阻塞检测热路径: 目录扫描全在本守护线程里做, API / cycle_start 只读内存…出错必须隔离: 目录不存在 / 权限 / 文件名非法 / 无 SN 都只记状态和日志, 线程本身永不因单目录异常而退出"。
- L103（同目录多规则合并）："mtime 策略不加保险, 其余取最严 (宁严勿松), 与 export_snapshot 口径一致"。
- L209（同通道命中多目录）："后者覆盖前者 (罕见配置, 取扫描顺序最后一个)"。

### 3. backend/services/mes_gateway.py（881 行，v3.49 复核）

**职责一句话**：MES 外部对接网关（出站推送侧）——把 cycle/session/box 等事件按连接配置（push_events 过滤 + bound_channels 过滤）分发到对应适配器，带重试/退避/4xx 策略、推送熔断器（v3.38）、截图注入、物料名映射、鉴权头合成、报警去重与在途报警台账登记，每次通讯写 MESCommLog。注意：**6 种推送适配器本身不在本文件**（v3.35 起含 database 直写，见 §3.1），在 `backend/services/mes_adapters/` 子包，本文件通过 `get_adapter(conn.adapter_type)`（L442）取用。

**核心类·函数**（类 `MESGateway` L121；全局单例 `get_mes_gateway()` L856）：
- 模块级截图压缩：`_SNAPSHOT_DEFAULTS` L24（降质阶梯 [60,45,30,20,12] + 缩边 0.75×4 轮）、`_snapshot_compress_params` L33（连接配置覆盖默认，非法值忽略）、`_recompress_jpeg_under` L59（先逐级降质再逐级缩边，压不下返回 None 放弃）、`_reencode_jpeg_quality` L102。
- **推送熔断器家族（v3.38 变更**，川南"框冻结"整改配套）：端点不在线时旧行为是每个周期傻等完整超时（默认 30s×重试）白耗调用线程；现按连接 id 记内存态 `_cb_state`（连续失败数/熔断截止时间/累计熔断次数），连续失败达阈值→冷却期内直接跳过推送，到点放一次半开探测，成功自动恢复。默认开（阈值 5 / 冷却 30s），连接 config 的 cb_enabled/cb_fail_threshold/cb_cooldown_sec 可改可关。`_cb_config` L135、`_cb_should_skip` L143（熔断中跳过；冷却到期放行当探测）、`_cb_record` L163（按真实推送结果推进状态机，打开/恢复各落一条 MESCommLog `circuit_breaker` 留证）、`reset_circuit` L200（人工复位：测试连接成功/改连接配置后 API 层调）、`get_circuit_state` L206（健康状态快照，连接列表序列化带出）。
- `set_extra_fields` L219 / `get_extra_fields` L223：Monitor 页实时输入的额外字段（如 weight），dict 键=channel_id。
- `dispatch` L226：**分发总入口**（同步阻塞，调用方线程直接执行）——报警去重判断 L235→查全部 enabled MESConnection→按 push_events / bound_channels 过滤→逐连接 `_send_to_connection`→任一成功则登记在途报警 L279。**v3.38 变更（川南"框冻结"根因修复）**：①逐连接错误隔离（单连接任何异常 rollback 后继续其余连接 L249-273）；②**每推完一条连接立刻 `db.commit()`**（L268）——原来统一循环外提交，前一条连接失败落日志（`_log` 内 flush）时 SQLite 写锁已被握住，下一条连接的 HTTP 超时等待（不在线端点可达 30s）期间锁一直不放，推理线程写步骤记录被堵到 busy_timeout 边缘（实测 8~15s）→前端检测框冻结、后续类别漏检误判 NG；逐连接提交把锁窗口收敛回毫秒级，网络等待期间绝不持锁。
- `_resolve_alarm_event` L288：判本次事件是否为入站配置的"报警事件"（`alarm_event_name` 支持字符串/列表；结果过滤默认只认 NG L307-313；台账字段按 `alarm_ledger_field_map` 从 context 提取）。返回三元组 (fields, dedup_sec, match_fields)（v3.38 起 docstring 已修正为三元组，旧"docstring 说二元组"的不一致已消）。
- `_alarm_dedup_should_skip` L330：去重窗口内已报过→跳过整次推送；判断出错一律放行。
- `_record_active_alarm_if_alarm` L344：报警事件成功推送后登记在途报警（external_alarm.record_active_alarm），全程错误隔离。
- `_send_to_connection` L255：**单连接推送核心**——static_fields 点号路径注入 L267→push_on_result 结果过滤（被过滤记 success=True 的 skip 日志但返回 False，L286-290；**v3.41 川南修复**：结果三级取值 `overall_result`→`result`→`cycle.result` 嵌套兜底——cycle_end 事件结果在嵌套层，老代码只读顶层取到空串被无条件放行，"仅 NG"对周期推送从未生效）→attach_snapshot 截图注入 L296→label_mapping 物料名映射（replace/keep_both 两模式）L310→鉴权合成 L319→`adapter.build_payload`→重试循环 L344（fixed/exponential 退避可配 L337；retry_on_4xx 默认 True，川南 §5.1 要求仅 5xx/超时重试时设 False L339）→成功/失败写 MESCommLog。
- `_capture_snapshot_base64` L526：抓工位当前帧→按 snapshot_quality 重编码→snapshot_max_bytes 超限自动压缩（压不下放弃，防客户 MES 413）→base64。
- `_apply_auth_to_headers` L569：auth.type ∈ {none, basic(留给 adapter), bearer, api_key, custom_header} 统一合并进 headers；顶层 custom_headers 列表/字典也合并。
- `_log` L623：写 MESCommLog（flush 不 commit，由调用方统一 commit）。
- `build_context_from_cycle` L640：从 cycle 构建标准化上下文（cycle/project/steps/defects/workpiece/order/operator/channel_group 子树 + ng_steps / missing_step_count 便利字段 L746-752）。v3.10+ operator_id 语义=user_id 查 User 表（L674-675、L706-707）；v3.13.1 工位组字段透传 L690-705。
- `build_context_from_session` L785：session 汇总上下文（total/good/ng_cycles + order summary）。
- `manual_push` L818：API 手动推送，靠回查最新一条 MESCommLog 拿结果。**v3.38 变更**：手动推送是操作员显式动作，熔断中也放行（把 open_until 拉到当前时刻充当探测，成功即恢复，L826-829）。

**线程·锁·队列**：本文件自身**无线程无锁无队列**——`dispatch` 在调用方线程同步执行（重试 sleep 也阻塞调用方），这正是 mes_hooks B1② 每工位外推执行器存在的原因（客户 MES 慢时 dispatch 会卡住 mes-hook-worker）。v3.38 的熔断状态 `_cb_state` 也是无锁 dict（写点集中在推送调用线程；进程内存态，重启即清）。

**上下游**：
- 上游：mes_hooks `_handle_cycle_end`/`_handle_session_end`（主推送路径）、cluster_collector（box_complete 聚齐后推送）、packaging/workpiece flow 协调器、weighing_engine（v3.35+ `weighing_product_done` 事件，见 01_backend_core 笔记）、mes_inbound（顶替完工回传）、API 层 mes_gateway.py（连接 CRUD/测试/手动推送/熔断复位）、Monitor 前端（set_extra_fields）。
- 下游：`mes_adapters.get_adapter` 适配器注册表（build_payload/send/check_response 三段协议，v3.35 起 6 种含 database 直写）、mes_inbound（读报警台账配置）、external_alarm（去重查询+台账登记）、channel_manager（抓帧）、WorkOrderService（工单 summary）、MESConnection/MESCommLog ORM。

**关键状态变量**：
- `_extra_fields`：dict[channel_id → dict]，Monitor 实时输入字段，进 full_context["extra"]。
- `_cb_state`：dict[conn_id → {fails, open_until, episodes}]，v3.38 推送熔断器内存态（重启即清=重启后所有连接立即重试）。
- `enabled`：网关总开关（默认 True，代码里无人置 False，形同常开）。

**注释里的坑（原样摘录）**：
- L678-680（build_context_from_cycle）："DetectionCycle ORM 模型本身没有 completed_steps/total_steps 字段，直接属性访问会 AttributeError 把 cycle_end → MES → 集群分发整条链路冲崩。用 getattr 兜底"。
- L720-723（StepRecord 字段名）："历史上这里写过 step_index / duration_seconds / is_good，会触发 AttributeError 并导致整个 cycle_end 构造 context 失败 → 进而 _cluster_dispatch 不被触发。用 getattr 防御式访问"。
- L743-745（便利字段）："Gateway 模板是自研 {key.path} 占位符替换，不是 Jinja2"——与 AGENTS.md "MES Gateway 不用 Jinja2" 相互印证。
- L413-416（截图注入）："放在 push_on 过滤之后, 被过滤掉的推送不浪费抓帧; 抓帧失败静默跳过, 绝不阻断推送. 默认关 → full_context 无 snapshot 字段, 与历史字节级一致"。
- L533-534（snapshot_max_bytes）："客户 MES 常有 413 请求体上限, 超大截图直接放弃而不是发了被拒"。
- L706-707（operator 字段）："operator_id 列保留字段名 (SQLite 无法 rename), 值改为 user.id…MES payload key 'operator' 字段名稳定 (向后兼容客户模板)"。
- L263-268（v3.38 每连接独立 commit）："原来统一在循环外提交 → 前一条连接失败落日志 (_log 内 flush) 时 SQLite 写锁已被本会话握住, 下一条连接的 HTTP 超时等待 (对不在线端点可达 30s) 期间锁一直不放; 推理线程此刻写步骤记录被堵到 busy_timeout 边缘 (实测 8~15s) → 前端检测框冻结、后续类别漏检误判 NG"。
- L126-130（v3.38 熔断器动机）："端点不在线时旧行为是每个周期傻等完整超时 (默认 30s×重试), 白耗 MES 工作线程、拖慢工件统计落库。熔断后冷却期内直接跳过, 到点放一次探测请求, 成功自动恢复"。

#### 3.1 backend/services/mes_adapters/database_adapter.py（175 行，v3.45 复核；v3.35 新增）

**职责一句话**：数据库直写适配器（v3.35，达梦优先，兼容 MySQL/PostgreSQL/SQL Server/SQLite）——客户 IT 不开 HTTP 接口、直接给一张中间表的对接场景，把事件数据按模板渲染结果直接 INSERT 进客户库；与 HTTP 适配器共用同一套 {key.path} 模板体系，**模板顶层键名 = 目标表列名**。已在 `mes_adapters/__init__.py` 注册表登记 `"database"`（该文件 L12/L20，注册表从 5 种扩为 6 种）。

**核心类·函数**（类 `DatabaseAdapter(BaseAdapter)` L54）：
- 模块级 `_DRIVER_HINTS` L35：db_type→(驱动包, pip 安装名) 提示表（达梦 dmPython/mysql pymysql/pg psycopg2/sqlserver pymssql/sqlite 内置）；`_DEFAULT_PORTS` L43（dm 5236 等）。
- `_safe_ident` L46：**表名/列名白名单校验**（仅字母数字下划线、可带 schema 点分），防 SQL 注入——列名来自模板键，值全部走参数绑定。
- `_connect` L58：按 db_type 懒加载驱动建连（未安装时报错信息直接写清要装哪个包，"现场排障省一轮" L26）；返回 (conn, 占位符风格)——dm/sqlite 用 `?`，其余 `%s`。
- `send` L110：payload 是 dict 插 1 行、list[dict] 逐行插入**同一事务**（行展开约定与 HTTP 模板 `_array_source` 语义一致，L22-24）；逐行拼 `INSERT INTO`（表/列名过 `_safe_ident`）→commit，异常 rollback；返回与 HTTP 适配器同构的 {status_code, body, success, duration_ms}（成功造 status_code=200 供上层统一判定）。
- `check_response` L155："直写没有 HTTP 语义: 以 send 内部的 success 标志为准"。
- **v3.45 变更（BUG-009，萍乡达梦现场）**：① 模块级 `_dm_bind_safe`（L47）——dmPython 在 Windows 上把 Python int 绑成 C 32 位 unsigned long，超范围直接抛 OverflowError，毫秒级时间戳（~1.7e12）必踩（weighing_product_done 推达梦失败）；**仅 db_type='dm'** 时超 32 位整数降级为字符串绑定，交达梦库端隐式转换（数值/日期列均可收），bool 豁免。② `_connect` 加端口守门——已知网络型数据库端口须在 1~65535（端口框误粘贴出超长数字时，报错直接指向端口配置而不是一句看不懂的 C 溢出）。

**线程·锁**：无——每次 send 短连接建/用/关。模块头 L27："连接不做池化: 网关本身有重试 + 事件频率低 (每周期/每件一次), 短连接最稳"。

**上下游**：上游 `mes_gateway._send_to_connection`（经注册表取用，adapter_type='database'）；下游客户关系库（dmPython/pymysql/psycopg2/pymssql/sqlite3 驱动直连）。

**关键状态变量**：无实例状态（config 每次随调用传入）。

**注释里的坑（原样摘录）**：
- L2-5（模块头）："对接场景: 客户 IT 不开 HTTP 接口, 直接给一张中间表 (达梦/MySQL/PostgreSQL/SQL Server/SQLite 皆可)。与 HTTP 适配器共用同一套模板体系 —— template 的顶层键名 = 目标表列名"。
- L12（config 注释）："database: dm 可留空 (走默认库), sqlite 填文件路径"。
- L45（_safe_ident）："表名/列名白名单校验: 只允许字母数字下划线 (可带 schema 点分), 防注入"。

### 4. backend/services/mes_inbound.py（1007 行，v3.42 复核）

**职责一句话**：外部生产管控系统「入站对接」接收器（v3.26+，川南范式）——外部主动 POST 开工/完工/报警消除报文，完全配置驱动（配置整体存 SystemConfig key=`mes_inbound_config`，无新表、零客户特异分支），字段映射→校验→切项目/建工单/顶替/收工单→按配置组装客户要求的业务响应码。

**核心类·函数**（类 `MESInbound` L283"无状态, 单例复用"；全局单例 `get_mes_inbound()` L793）：
- `DEFAULT_TRUE_WORDS` L39：完工信号默认真值词表。
- `DEFAULT_INBOUND_CONFIG` L43-202：**全量默认配置即文档**——field_map/required_fields/response（含川南 40001-40007 业务码 + json/xml 格式 + messages 文案覆盖）/auth（密钥头+IP 白名单，默认关）/max_body_bytes(1MB)/input_format(auto→json→form→query)/switch_project_on_task（默认关，L95-97 注释："外部 HTTP 触发的自动切项目会重载模型/打断产线, 属敏感动作, 要的人显式开"）/**start_detection_on_task（v3.37 变更，默认关，L105-109）**：开工后对"视频源在跑+模型就绪+未在检测"的工位自动拉起检测/match_project_by_name + name_match_strict_boundary（v3.30 统一匹配器档位）/create_work_order_on_task/order_binding(project|channel)/reject_duplicate_task（与"最新开工为准"相反取向二选一，L118-120）/**terminal_order_policy（v3.42 新增，L126）**：同号工单已完结（completed/cancelled）再收开工的处理——revive（默认）=自动复活为在产、reject=拒收回 duplicate 码/complete_field 完工信号族/supersede_previous_task 顶替族/receive_paths 自定义接收路径别名（L141-144，path 不允许 /api/ 前缀防劫持）/http_status_map/报警台账族（alarm_event_name/alarm_record_on_results/alarm_ledger_field_map/alarm_dedup_sec/alarm_clear_match_fields）/alarm_banner 前端横幅配置（**v3.39 变更**：新增 allow_manual_clear 横幅"手动消除"按钮 + clear_on_counter_reset 监控页清零顺带消除，两出口都默认关——保持"只能外部消除"的对接契约，L185-188）/task_info_display 四要素上屏开关（**v3.39 变更**：新增 show_order_chip 工单徽标开关 + two_line_layout 表格式两行布局，治"信息条一行挤不下被截断"，L198-200）。
- 模块级工具：`http_status_for` L205（业务码→HTTP 状态码，默认一律 200）、`to_xml` L216（响应 dict 序列化 XML，SOAP 用）、`check_inbound_auth` L241（密钥头+IP 白名单，返回 None=通过）、`_ip_allowed` L271（CIDR 支持；非标准 IP 如 TestClient 'testclient' 退化字符串精确匹配 L278）。
- 配置读写：`get_config` L302 / `save_config` L312 / `_with_defaults` L325（浅合并 + response/codes/auth/alarm_banner/task_info_display 五处深合并）。
- `handle_task_start` L354：开工处理主入口——enabled 守门→`_map_fields` 映射→required_fields 校验→`_apply_task_action`→`_result` 组装。**v3.39 变更**：处理明细附在成功文案后（业务码不变，上游按 code 判成败不受影响，L389-393）——"开工自动开始检测"没拉起时原因直接可见，不用开调试日志。
- `handle_alarm_clear` L396：报警消除命令——按 alarm_clear_match_fields（默认四要素：任务号/产品号/工序工步/操作员）匹配在途报警并消除；匹配不到回 alarm_not_found(40007)。
- `_apply_task_action` L430：编排——完工信号优先（complete_field 真值→`_complete_work_order`）→reject_duplicate_task 判重→`_switch_project`→**开工自动开始检测（v3.37 变更**，start_detection_on_task 开时；先 `db.commit()` 再拉检测——start_detection 内部另开 DB 会话写检测记录，不先提交会撞满 SQLite busy_timeout 15s，上游中控超时短于它就误判"连接失败"，2026-07-13 UAT 逼出的真锁 L466-476）→`_ensure_work_order`→**运行中开工回填（v3.38 变更**，create_work_order_on_task 开时先 commit 落盘再 `_rebind_running_channels` L485-495）。
- `_auto_start_detection` L527（v3.37 变更；v3.39 契约升级）：开工后自动拉起检测。门槛：配置过视频源 + 模型就绪 + 未在检测。**v3.39 两处升级**：①暂停中的源不再因"源未运行"放弃——只要配置过视频源就交给 start_detection 的"暂停续播/相机重连"复活路径（川南现场事故链：点过"停止"后每次开工都拉不起来，2026-07-15 日志钉死）；②返回 (started, skipped) 二元组，没拉起的工位及原因（未配视频源/模型未就绪"请在项目管理配默认模型"）随开工响应回给上游，一次请求即可自诊。任何失败只记调试日志，绝不影响开工响应。
- `_rebind_running_channels` L497（**v3.38 变更**，川南反馈"检测已在跑时收到开工，新单挂不上运行中的工位"）：遍历有活跃会话的工位，把最新在产单投递给 mes_hooks `on_external_order_changed` 回填（§1）；只投内存绑定任务给 hook 工作线程，不在入站请求里做任何 DB 写；allow_replace 复用 supersede_previous_task 已有语义不新增开关；刚被自动拉起检测的工位走 session_start 原生绑定，回填与之幂等。
- `_switch_project` L578：复用 `project_match.resolve_project_id_by_spec`（对照表精确→通配符→自动同名子串，与上银包装线完全一致，L589-590 注释）→已激活项目跳过重载 L606-609→`activate_project_core`（404 回 unknown_product）。
- `_ensure_work_order` L622：建/激活工单（order_no=task_no，source='external'，四要素存 extra_data["inbound"] 留痕）；order_binding=channel 时按 channel_field 绑工位；然后 `_ensure_in_progress` + 可选顶替。**复用分支（同任务号再开工）v3.41 起调 `_refresh_reused_order`**。**v3.42 两处**：①复用分支入口先按 terminal_order_policy 守门（L672）——终态单 + policy=reject 时直接回 duplicate 拒收不复活；②响应透明化（L700-707）——工单最终没进 in_progress 时不再谎报"已就绪"而是回"状态异常, 检测数据将不会计入该任务"，终态复活成功则在成功文案后缀"已从完结状态重新开工"（07-17 教训：task-pro 被顶替成终态后每次开工都回"已就绪"但工位永远挂不上单，现场只能靠猜）。
- `_refresh_reused_order`（v3.41 川南修复）：复用工单按本次开工报文"最新开工为准"刷新——重绑当前激活项目（工位路由则重绑报文工位）+ 覆盖 extra_data.inbound 四要素 + 产品码/操作员。老代码复用分支只打日志不动工单 → 绑定过期的复用单挂不上工位 → 四要素不上屏、出站推送工单字段全 null。JSON 列整体重赋值（原地改 dict 不触发 SQLAlchemy 变更追踪）；失败只记日志不阻断开工；终态单复活仍归 `_ensure_in_progress`。回归 `tests/test_cn_fixes_20260717.py`。
- `_supersede_previous_tasks` L687：按 supersede_scope（project/same_project/same_channel/external）查在产外部单收尾。**注意：same_channel 分支在代码里实现了（L708-715）但 DEFAULT 配置注释（L135）只列了 project/same_project/external 三档**。
- `_finish_superseded` L722：逐单 change_status(completed)→**先 commit 释放 SQLite 写锁再网络回传**（L732 注释："与 mes_hooks cycle_end 同模式, 避免锁等待"）→report_complete_on_supersede 时逐单 `_report_order_complete`。
- `_report_order_complete` L744：对被顶替工单回传"完工"出站事件（gateway.dispatch(complete_event_type)）。
- `_complete_work_order` L759：完工信号收工单（complete_match_column=order_no 按工单号 / product_code 取该产品最新在产单）；找不到/已终态静默放行（返回 ok）。
- `_ensure_in_progress` L854：状态机安全迁移 draft→pending→in_progress、paused→in_progress。**v3.42 变更**：终态（completed/cancelled）改为**绕过状态机直接复活**为 in_progress（清 actual_end），返回是否发生复活——外部中控的开工报文是权威指令，终态单收到开工=同一任务新一轮生产；老行为"终态不复活+响应仍回已就绪"是静默死路（工位永远挂不上单、四要素不上屏、推送字段全 null）。UI 手工路径仍走状态机不受影响；policy=reject 时上游 `_ensure_work_order` 已提前拒收不会走到这里。回归 `tests/test_cn_fixes_20260717.py`。
- `_truthy` L857：布尔/非零数字恒真，字符串查词表（大小写不敏感）。
- `_map_fields` L875：外部报文→我们字段（`_get_nested` 点路径，复用拉取器同款）。
- `_result` L888 / `error_response` L897 / `_set_nested` L902 / `build_response` L915：响应组装——有 response.template 时走自研 {key.path} 模板（复用出站 render_template，支持 SOAP 嵌套信封）；无 template 时 code_field/message_field 支持点路径嵌套写入；echo_fields 回显。

**线程·锁·队列**：无——纯同步无状态服务，调用方（API 路由）线程直接执行；"副作用时序"有三处：`_finish_superseded` 的先 commit 后网络、v3.37 拉检测前先 commit（防 busy_timeout 死锁）、v3.38 回填前先 commit（hook 工作线程另开会话查单必须先落盘）。

**上下游**：
- 上游：`backend/api/mes_inbound.py` 路由（内置 /api/v1/mes/inbound/* + receive_paths 动态注册的根路径别名）；mes_gateway `_resolve_alarm_event` 读它的配置（报警台账三键）。
- 下游：SystemConfig KV、WorkOrderService（建单/改状态/summary）、project_match（统一规格匹配器）、projects.activate_project_core（切项目，与手动激活同一入口）、external_alarm（clear_alarms）、mes_gateway（顶替完工回传 dispatch）、mes_adapters.base（_get_nested/render_template 复用）、channel_manager（v3.37 自动拉检测 / v3.38 回填遍历活跃会话）、mes_hooks（v3.38 on_external_order_changed）。

**关键状态变量**：无实例状态（配置每次从 DB 读）。配置字典本身即状态，键义见 DEFAULT_INBOUND_CONFIG 逐行注释。

**注释里的坑（原样摘录）**：
- L7（模块头）："完全配置驱动, 零客户特异分支 (川南 = 填一份字段映射 + 响应码; 下一家 = 填另一份)"。
- L95-97（switch_project_on_task 默认关）："外部 HTTP 触发的自动切项目会重载模型/打断产线, 属敏感动作, 要的人显式开"。
- L118-120（reject_duplicate_task）："默认关 (幂等复用)。与'最新开工为准/顶替'是相反取向, 二选一"。
- L131-132（supersede_previous_task）："(川南: 无配对完工信号, 以最新开工为准, 必须开)"。
- L142-143（receive_paths）："path 必须以 / 开头, 不允许 /api/ 前缀 (防劫持主程序 API)"。
- L732（_finish_superseded）："先提交释放 SQLite 写锁, 再做网络回传 (与 mes_hooks cycle_end 同模式, 避免锁等待)"。
- L606（_switch_project）："已经是激活项目 → 跳过重载, 避免无谓切模型打断正在跑的产线"。
- L467-470（v3.37 拉检测前先 commit）："start_detection 内部会另开 DB 会话写检测记录, 与本请求未提交的工单写事务互斥, 不先 commit 会撞满 SQLite busy_timeout (15s), 上游中控超时短于它就会误判'连接失败' (2026-07-13 UAT 逼出的真锁)"。
- L544-549（v3.39 暂停源复活）："点过'停止'后视频源暂停, 老逻辑在这里因'源未运行'直接放弃, 之后每次开工都拉不起来。而 start_detection 本就有'暂停续播/相机重连'的复活路径 (手动点'开始'走的就是它), 所以只要配置过视频源就放行交给它拉起"。

### 5. backend/services/mes_puller.py（570 行）

**职责一句话**：外部 MES 工单主动拉取服务（v3.20，上银范式）——我们主动调外部 MES 查询接口拉工单回来落库；配置驱动（复用 MESConnection 表 + `config.pull` JSON 子树，无新表），含请求体模板渲染、成功判定、数组提取、字段映射、三种导入模式 upsert，外加一个 15s tick 的后台定时拉取调度器。

**核心类·函数**：
- `DEFAULT_FIELD_MAPPING` L55：上银 HIWIN 预设映射（前端模板一键填充用，"主程序不依赖它做任何分支判断"）——但注意 `_pull_with_config` L195 在 field_mapping 未配时会 fallback 到它。
- 类 `MESPuller` L64（"无状态, 可单例复用"；持有一个 WorkOrderService 实例）；单例 `get_mes_puller()` L483。
- `pull_for_connection` L73：主入口——按 conn.config.pull 执行一次拉取 + 写 MESCommLog(direction='pull') + 成功且非 dry_run 时更新 last_sync_at；内部字段 `_request_body/_response_snippet` 不外泄 L111。
- `test_connection` L115：只发一次请求不映射不落库，返回 `structure_guess`（自动猜数组路径+字段候选）供前端点选映射。
- `_pull_with_config` L138：核心流程——渲染 body→HTTP→`_is_success` 判定（失败时通用探测 errorInfo/detail_message/message/msg/errorMsg 拼错误详情 L175-185）→`_extract_array`→逐条 `_map_item`+`_upsert_order`→统计 created/updated/validated/skipped。
- `_render_body` L226：`{job_no}` 占位符字符串替换（只有这一个变量）；非 JSON 模板原样发。
- `_http_request` L248：requests 重试循环；**默认绕过系统代理**（`proxies={"http":None,"https":None}`，L256-259 注释：工控机常开 clash 全局代理会劫持内网 MES 地址；use_proxy=true 才跟随系统代理）；鉴权复用 `MESGateway._apply_auth_to_headers` L264；非 2xx 时通用探测 body 内错误字段拼 hint（L295-297："只回 'HTTP 500' 现场无从下手…不硬编码任何厂商"）。
- `_build_basic_auth` L322 / `_is_success` L328（success_path 未配=HTTP 200 即成功；`str(actual)==str(expect)` 容忍 "200" vs 200）/ `_extract_array` L341（单条对象包成列表）/ `_map_item` L353。
- `_upsert_order` L367：import_mode = validate（只报 matched/remote_only）/ upsert（更新 customer_name/product_spec/product_name，**不动 planned_qty**）/ upsert_with_planned（连 planned_qty 一起覆盖）；新建时 source='external'、binding_scope='project'（L414 注释：strict=False 允许不绑项目入库）；product_name 兜底链 product_name→product_spec→order_no L400-402。
- `_find_object_array` L429：深度优先找第一个非空对象数组（优先 resultData/data/list/rows/items/records，最多下探 4 层——上银把数组埋在 response.resultData 两层）。
- `_guess_structure` L455 / `_snippet` L469（响应截 2000 字符入日志）。
- 类 `PullScheduler` L493；单例 `get_pull_scheduler()` L566：单后台 daemon 线程 `mes-pull-scheduler`，`TICK_SEC=15` 扫全部连接；触发条件=conn.pull_enabled（**列级开关**）+ triggers.scheduled + pull.url 齐备；interval 取 triggers.interval_sec 或 conn.pull_interval_sec，下限钳 10s（L549）；`_last_run` 初值 0 → **启动即先拉一次**（L500 注释："满足客户'开机就同步当班工单'"）；单连接错误隔离 rollback 不崩线程。

**线程·锁·队列·定时器**：仅 PullScheduler 一条 daemon 线程（`threading.Event` stop + `wait(15)` 节拍）；`_last_run` dict[conn_id→ts] 无锁（仅调度线程访问）。MESPuller 本体无线程。

**上下游**：
- 上游：API 层 `mes_gateway.py` 路由（手动拉取/试连/试同步端点）、PullScheduler（定时）、main.py 启动调 `get_pull_scheduler().start()`。
- 下游：外部 MES HTTP 接口（requests 直发）、WorkOrderService（get_order_by_no/create_order）、MESGateway._apply_auth_to_headers（鉴权复用）、MESCommLog（direction='pull'）、mes_adapters.base._get_nested。

**关键状态变量**：`PullScheduler._last_run`（dict[connection_id → 上次拉取 time.time()]，进程内存态，重启即清 → 重启后必然立即拉一轮）。

**注释里的坑（原样摘录）**：
- L256-259（_http_request 代理）："工控机 / 开发机常开 clash 等全局代理, 会把发往 MES 内网地址(如 10.x.x.x)的请求劫持到外网导致连不上。MES 对接绝大多数是内网直连, 故默认 proxies 显式置空(覆盖环境变量代理)"。
- L295-297（错误详情）："外部 MES 常把真实原因写在 body (上银是 error.errorInfo), 只回 'HTTP 500' 现场无从下手。通用探测常见错误字段, 不硬编码任何厂商"。
- L433-435（_find_object_array）："上银把工单数组埋在 response.resultData (两层), 所以不能只扫顶层——本层找不到就下探子对象…最多下探 4 层防深递归"。
- L497-500（PullScheduler 设计要点）："错误隔离: 单条连接拉取失败不影响其他连接…启动即先拉一次 (last_run=0), 满足客户'开机就同步当班工单'"。
- 注意：模块头注释 L34 写 triggers 支持 `"on_scan": false`，但全文件搜不到 on_scan 的消费代码（调度器只看 scheduled）——疑似规划了未实现，记入疑点清单。

### 6. backend/services/cluster_collector.py（1428 行，v3.49 复核）

**职责一句话**：集群数据汇总服务——主机接收本地/远程工位的 cycle 结果（BoxAggregation 表），按 box_serial 聚齐 expected_stations 后生成 BoxSummary 并推 MES `box_complete`；副机侧提供上报主机 + 定时心跳；超时未齐按策略推 `box_timeout` 或仅标记。

**核心类·函数**（类 `ClusterCollector` L140；单例 `get_cluster_collector()` L1201）：
- 模块级：`_build_sub_report` L29（从 cycle_context 摘"本次上报快照"供前端分路展示）、`_merge_cycle_context` L52（同站点多路视觉上报的深合并：列表去重累加、字典浅合并、cycle.result 按 merged_is_good 覆写）、`_run_with_retry` L102（**SQLite 写锁退避重试的关键设计**：每轮重新执行 build_fn 再 commit——见坑摘录）。
- `__init__` L142：全局 `_lock`（护 `_connected_slaves`）+ 三个计时默认（slave_timeout 20s / heartbeat 5s / box 扫描 30s，均可 KV 覆盖）+ **per-box 进程内锁** `_box_locks`（L153-155 注释："SQLite 不支持 SELECT FOR UPDATE，这是最稳妥的进程内串行手段"）。
- `_acquire_box_lock` L159 / `_release_box_lock` L168（箱子 pushed/timeout 后清理，防 dict 无界增长）。
- `start` L173 / `stop` L189：起停 `cluster-timeout-checker` + `cluster-heartbeat-sender` 两条 daemon 线程。
- `get_config` L196：读 ClusterConfig(id=1)，**5 秒缓存**；无记录回落 standalone 默认；同时从 SystemConfig 刷新三个计时参数。`invalidate_config_cache` L244。
- `_TIMING_KEYS` L249 / `_read_timing_config` L255 / `save_timing_config` L272：v3.29 去硬编码——三个计时参数存 SystemConfig KV（cluster.heartbeat_interval_sec 等），白名单+越界忽略。
- `register_slave` L296：副机心跳注册/更新（内存 dict，含 detecting/project/channel_count）。
- `get_connected_slaves` L317：列在线副机并**顺手剔除**超时的。
- `receive_station_report` L338：**主机收上报总入口**——per-box 锁内执行 `_receive_station_report_locked` L358：upsert BoxAggregation（同站点重复上报走 v2.8.0 合并语义：两路都 OK 才 OK / NG 事件名优先 / sub_reports 按 (channel_id, source_address) 去重同路最新覆盖 L385-411 / 基于 sub_reports 重推导 is_good L413-427 / **v3.1.2 ok_lock 策略：OK 站点拒绝被 NG 覆盖但 NG 仍写审计** L429-443）；IntegrityError（跨进程 UNIQUE 竞态）回滚重跑一轮改走 UPDATE L472-489；最后 `_check_and_dispatch`。
- `report_to_master` L505：副机 POST `{master_url}/api/v1/cluster/report`（timeout 10s，失败仅记日志返回 error）。
- `_match_records_to_expected` L534：expected 的 "B" 前缀匹配 station_id=="B" 或 "B-*"（主机多通道 mes_hooks 拼 "B-0"/"B-1"，expected 写裸 B 即覆盖）。
- `_check_and_dispatch` L560：**聚齐判定核心**（v2.7.13 方案 B 二次校正）——拉该箱**全部**记录（含 dispatched）判齐；无新 received 记录直接返回 no_new_data L582-585；缺站返回 missing；齐了构建 aggregated（顶层便利字段 order_no/workpiece_id/ng_items L615-637）→ build_summary（首发必推；已 pushed 后仅 overall_result 或 ng_items 变化才重推 L687-709；matched_records 逐条 `db.merge(r).status="dispatched"` L710-713 防重试时 ORM 实例 expired）→ 推 `box_complete` L755 → fire 插件 hook `box_complete` L762 → mark_pushed → **非 recovery 才工单计件** L779（v3.1.0：校正重推不再 +1）。
- `_increment_cluster_orders` L815：binding_scope=cluster 的 in_progress 工单按 station_ids 交集匹配、各自 +1，达 planned_qty 自动切 completed。
- `_push_timeout_result` L849：超时推送——缺失工位以 `event_name="timeout_missing"` 假站条目进 stations，ng_items 追加 `MISSING-{station}` 标签，overall_result="TIMEOUT" 且 result="NG"，推 `box_timeout` 事件（**不计件**）。
- `get_pending_boxes` L965：前端"待汇总"列表（v2.7.13：齐不齐基于全部 status 记录算，齐了的不进列表）。
- `get_recent_summaries` L1019：BoxSummary 分页。
- `_timeout_checker` L1040：主机后台线程——仅 role=master 且 enabled 且 timeout_sec>0 时工作；先用只读短 session 列箱子 L1059-1070，再**每箱独立 session**（L1077-1078 注释：缩短持锁时间避免顶掉上报路径）；超时后 timeout_push=True 走 `_push_timeout_result`，否则仅标记 status=timeout。
- `_heartbeat_sender_loop` L1130：副机后台线程——仅 role=slave 且 enabled 且有 master_url 时每 `_heartbeat_interval` 发 POST /cluster/heartbeat（带 hostname/project/channel_count/detecting）；每轮重读 config 支持运行时切角色。

**线程·锁·队列·定时器**：
- 两条 daemon 线程：`cluster-timeout-checker`（主机箱超时扫描，节拍=box_scan_interval）、`cluster-heartbeat-sender`（副机心跳，节拍=heartbeat_interval）。两条线程**无论角色都启动**，靠循环内 role 判断空转。
- 锁：`_lock`（副机注册表）、`_box_locks_guard` + per-box `_box_locks[box_serial]`（同箱上报串行化）。
- 无队列；DB 写全部走 `_run_with_retry`（6 次、0.2s 线性退避）。

**上下游**：
- 上游：mes_hooks `_cluster_dispatch`（master 本地直接调 receive_station_report；slave 调 report_to_master）、API 层 cluster.py（/report /heartbeat /pending /summaries /config 等）、main.py 启动 start()。
- 下游：mes_gateway.dispatch（box_complete / box_timeout）、WorkOrderService（cluster scope 计件）、插件 hook box_complete、ClusterConfig/BoxAggregation/BoxSummary ORM、channel_manager（心跳里读 detecting 状态）。

**关键状态变量**：
| 变量 | 语义 |
|---|---|
| `_connected_slaves` | dict[station_id → {ip,port,hostname,project,channel_count,detecting,last_seen,first_seen}]，主机侧内存心跳表 |
| `_config_cache` / `_config_ts` | 集群配置 5s 缓存 |
| `_box_locks` | dict[box_serial → Lock]，同箱上报串行化，pushed/timeout 后清理 |
| `_slave_timeout` / `_heartbeat_interval` / `_box_scan_interval` | 三个计时参数（KV 可配，get_config 时刷新） |

**注释里的坑（原样摘录）**：
- L104-110（_run_with_retry）："SQLite 遇到 locked 时 commit 会抛；如果我们此时 rollback，session 里 add 过的对象也会被清空。之前那版 `_commit_with_retry` 只重试 `db.commit()` 就等于 no-op（pending 对象已没了），导致 HTTP 返回 success=True 但数据**根本没入库**"。
- L344-350（receive_station_report 并发模型）："用 per-box_serial 进程内锁把整段查+写+_check_and_dispatch 串行化，避免 MES box_complete 被重复推送、BoxSummary/BoxAggregation UNIQUE 冲突…SQLite WAL 下仍只允许单写，与 _timeout_checker 会抢锁 → locked，由 _run_with_retry 兜底"。
- L563-568（v2.7.13 方案 B）："原先只拿 status='received' 的记录, 导致首轮已 dispatched 的工位不再参与齐检…已 pushed 再次齐发 → 只有 overall_result 或 ng_items 有变化才重推 MES, 避免无差异数据的重复骚扰"。
- L429-430（ok_lock）："v3.1.2 OK 锁定模式: 已合格的站点不再被 NG 覆盖, NG 仍写入审计. 反向 (NG 站点收到 OK 数据) 走默认逻辑允许翻盘"。
- L711-712（build_summary）："matched_records 是外层查到的 ORM 实例，重试时可能已 expired；通过 merge 确保每轮都挂到当前 session 上"。
- L969-972（get_pending_boxes）："之前按 status=received 过滤, 同码重扫场景下: 已 dispatched 的工位被漏掉, 左下角显示'缺 A/C/D' 但详情里 A/C/D 是有数据的, 误导用户"。
- L1072（_timeout_checker）："now = datetime.utcnow()  # 与 received_at(server_default func.now()) 的 UTC 对齐"。
- L776-778（计件）："一个 box_serial 完成一次算 1 件. 校正重推 (is_recovery=True) 不再 +1…超时未齐的 box 走 _push_timeout_result, 那条路径不计件"。
- 另注意：`_check_and_dispatch` L655 的 `early_return = {}` 声明后在 L733 检查 `early_return.get("payload")`，但**全程无人写入** `early_return`——疑似历史重构残留死代码，记入疑点清单。

### 7. backend/services/packaging_flow_coordinator.py（2086 行，v3.45 复核）

**职责一句话**：包装箱结算协调器（v3.21+ 上银包装线）——扫码驱动的"工单→箱→托盘/滑块"三层结算状态机：扫工单标签拉 MES 得应做箱数，视觉周期数托盘（trays 口径）或一周期一箱（sliders 口径），封箱/漏箱/多箱/标签错/少装各有报警与处置策略；六个外部依赖全部走可注入钩子（拉单/报警/回推/箱目标/切项目/步骤探测），默认 None 只 print。

**核心类·函数**（类 `PackagingFlowCoordinator` L95；单例 `get_coordinator()` L70 双检锁；测试重置 `reset_coordinator_for_testing()` L80）：
- `compute_box_plan` L38：滑块总数+每箱数→(应做箱数, 尾箱目标)；非整除尾箱=余数，整除尾箱=每箱数；非法入参→(0,0) 走"箱数未知"兜底。
- `_pkg_dbg` L60：debug_center 埋点（backend.packaging 类别，默认关零开销）。
- `_TERMINAL_STATES` L92 = {completed, aborted, short}。
- 六个依赖注入 setter：`set_mes_fetcher/set_alarm_sink/set_mes_pusher` L126-133 + v3.22 三钩子 `set_box_target_setter/set_project_activator/set_paper_order_probe` L135-142。
- `reload_configs` L148：从 PackagingFlowConfig 表加载 enabled 行进内存（`_configs` + `_channel_to_config` 反向索引）。
- `_row_to_dict` L167：**配置全字段快照**（含 v3.35 复合条码取段 5 键 + 工单号识别规则 L187-195；组⑥ 异常→项目事件映射 event_short_box 等 7 键 L206-213；组⑦ v3.22 sliders 口径 + auto_switch_project + spec_to_project + 尾箱塞工单 gate + **v3.35 tail_paper_as_close_action** L214-227；v3.23 缺油嘴 gate L228-231）。
- `_order_code_ok` L239（**v3.35 变更**，代码内注记 v3.30.1，工单号识别规则）：仅在"无在途工单、准备开第一单"时校验——归一化后的码须从头匹配配置正则才放行，挡掉开机后第一枪误扫的数量/物料条码（如 '80.00' 开出垃圾工单）；规则为空=不过滤（存量零差异），正则写错=视为不过滤并留调试痕迹不吞码。拒绝时报 order_code_reject 报警（on_scan L397-403）。
- `_extract_composite` L255（**v3.35 变更**，代码内注记 v3.30.1，复合条码取段）：正式产线箱标签常是多段拼接码（如 订单|工单|数量|校验串），整串比对必不符——启用后按分隔符拆段，prefix 模式取指定前缀段（多段命中取最长=最具体）/ index 模式取第 N 段，再交 `_normalize` 走原有归一化；无分隔符的码（工单纸直扫）原样返回→**两种码可混扫**；取段失败原样返回不吞码。默认关零差异。
- `_normalize` L290：条码归一化——**复合码先取段再归一**（v3.35）→insert_char（上银：扫码枪丢 '-'，按 hyphen_pos 补回固定位置，幂等 L296-300）/ strip_hyphen / digits_only / exact。
- `_trays_per_box` L316 / `_tray_qty` L322（fixed 或 by_spec 查表）/ `_resolve_box_total` L327（field 直取 / formula=字段÷(每箱托盘×每托数量)）。
- `_raise_alarm` L343 / `_pull_mes` L353（无 fetcher 视为拉单未接入走 on_mes_fail）/ `_push_mes` L362（push_on_complete 开关）。
- `on_scan` L379：**扫码总入口**（锁内）——无配置零差异 return；`_resolve_config_for_scan` L506（scan_device_id 优先→channel→唯一配置兜底）；无 run→工单号识别规则守门（v3.35）→`_open_order`；sliders 口径分流 `_on_scan_sliders` L458；trays 口径：同号第 2 扫=开第 1 箱、以后同号扫=结箱+开新箱（满箱报 over_box）；不同号且未开箱→label_mismatch 判定（off/warn/block 三策略 L427-440）；不同号且做过箱→先结当前箱再判漏箱（short_box；redo=不切工单等补做 / 否则 abort），完整则 complete，最后开新工单。
- `_on_scan_sliders` L458：sliders 口径扫码只管工单层——同号仅刷新落库；不同号未做完箱走 label_mismatch；已做过箱走漏箱/完成+换单。**v3.35 变更（尾箱挂起等放工单时的扫码出口，L461-475）**：探到放工单→快照原成绩收尾；扫新单且仍没放→尾箱判 NG 收尾再开新单；同号重扫且仍没放→提醒继续等。
- `on_cycle_settled` L522：**周期结算入口**（锁内）——通道未参与零差异；`status=pending_remediation` 时忽略新周期 L533-536；sliders 分流；trays：is_good→current_box_trays+1，NG→报 tray_ng 不计入。
- `on_forced_settle` L560：强制停止/待机/管理员强制结案——策略 settle（未满箱按 on_forced_stop_partial 判）/abort/keep；reason/operator 落库审计；**v3.35 变更**：settle 分支若有尾箱挂起快照=管理员豁免塞工单 gate，按快照原成绩落账收尾（L591-595）；`force_settle_manual` L603（强制走 settle 忽略配置）；`on_forced_settle_by_channel` L612（source 停止/待机触发，待机受 forced_settle_on_standby 控制）。
- v3.23 补做族：`supplement_sliders` L633（挂起少装箱补滑块，clamp 到 [0,目标]，remediated 审计写进 box_details）/ `redo_pending` L673（丢弃本箱同箱号等重测）。
- `_open_order` L695：label_len 校验→拉 MES（失败按 on_mes_fail block/offline）→sliders 分流 `_open_order_sliders` L727（可选按规格自动切项目→算 items_per_box→compute_box_plan→**立即开第 1 箱**不等第 2 次扫码 L757）。
- `_resolve_items_per_box` L760 / `_read_active_project_item_target` L766（读激活项目 pipeline_config.custom_mix_container_item_target）。
- `_current_box_target` L792（尾箱切余数）/ `_apply_box_target` L799（反向设回检测层容器累加器）。
- `_open_box_sliders` L809 / `_probe_paper_order` L821（**无探测器→gate 退化为不阻断**）/ `_probe_oil_nozzle` L833（每箱重判不缓存；未配置放行，L837-838："每箱 gate 若误卡会卡死整条线"）。
- `_close_pending_paper` L850（**v3.35 变更**，代码内注记 v3.34.1，"放工单=尾箱收尾动作"闭环核心）：消化尾箱「等放工单」挂起快照——ok_paper=True（放工单到位/管理员强制豁免）按快照原成绩收尾（滑块数/合格性用被 gate 拦下那次的）；ok_paper=False（没放就扫新工单/弃单）尾箱判 NG 收尾并写 remediated 审计；收尾即工单完成（尾箱是最后一箱），在途移除。仅 tail_paper_as_close_action 子开关开时进此路径（默认关=老行为"只报警等着"）。
- `_on_cycle_settled_sliders` L879：**v3.35 变更**：尾箱已挂起等放工单时后续周期只用来探测放工单动作、不当新箱结算（L883-891）→没开箱自动开第 1 箱→缺油嘴 gate→尾箱塞工单 gate（**v3.35 分两种等放模式**：子开关关=老行为下周期重走 gate；开=挂起本箱成绩快照 pending_paper_box 等收尾，L917-937）→合格判定（is_good 且滑块数==目标）→v3.23 少装挂起（仅"检测步骤齐+滑块少+项目开补数量策略"时 pending_remediation）→`_finalize_box_sliders` L961（落账+推进下一箱或收尾工单）。
- `_open_box` L991 / `_settle_current_box` L1004（trays 结箱；force_partial 覆盖判定；防重复结算 L1006）。
- `_complete_order` L1029（收尾前结算最后半箱；box_ng>0 或漏箱→final=NG；可选回推 MES；从 `_runs` 移除）/ `_abort_order` L1051。
- `_new_run_dict` L1058：run 运行态全字段（含 v3.22 sliders 字段 + v3.23 pending_box + forced_reason/forced_by 审计 + **v3.35 pending_paper_box** 尾箱等放工单挂起快照）。
- `_persist_run` L1093：内存 run 同步到 PackagingFlowRun 行（断电恢复+历史；异常隔离不阻断状态机；终态写 completed_at）。
- `abort_in_progress_on_startup` L1153：启动把 order_loaded/running 的历史 run 全标 aborted。
- 查询：`get_config` L1175 / `get_state` L1180 / `resolve_config_id` L1185 / `list_loaded_config_ids` L1191；`cleanup_for_testing` L1199。
- 模块级真实钩子（M3，`wire_real_hooks` L1416 启动注入）：`_real_mes_fetcher` L1217（借 puller.test_connection 拉单，取数组第一条原始字段，规格兜底归一到 'spec' 键；**在扫码线程持锁内含一次同步 HTTP**）、`_KIND_TO_EVENT_FIELD` L1264（异常→配置事件字段映射）、`_real_alarm_sink` L1277（命中配置事件走 VSM `fire_external_event_response`，否则退回默认通用报警 event2 不静默）、`_real_mes_pusher` L1300（走网关 dispatch，事件默认 packaging_complete，模板可引用 {packaging.*}）、`_real_box_target_setter` L1325（设 `_custom_mix.set_container_item_target`）、`_real_project_activator` L1343（resolve_project_id_by_spec 统一匹配器+模型重载+配置同步）、`_real_paper_order_probe` L1390（调工位 `is_packaging_paper_order_covered`；拿不到工位→False gate 守住；**v3.35 变更**：该探测优先读"上一个已结算周期"的步骤缓存会遮蔽当前未结算周期，而尾箱挂起期间补放动作恰恰落在当前周期——命不中时补查实时步骤集 current_cycle_steps L1403-1408）。

**v3.43 大改（放工单=工单收尾语义重构 + 三个新守门，本文件本轮最大改动，上述 v3.35 行号已漂移）**：

- **语义重构（v3.43 核心）**：v3.34.1 的"挂起快照"闭环整体替换为"**箱归周期结算、放工单归工单收尾**"——尾箱在周期结算时照常按自身成绩当场落账（`_on_cycle_settled_sliders` 的 gate 分支只对老模式拦箱；as_close_action 开时不拦不报警），收口移到 `_finalize_box_sliders` 的工单完成点：各箱全落账但放工单没探到 → run 挂 `awaiting_paper = {"since", "alarmed"}`、status=awaiting_paper，不完成工单。`_close_pending_paper`/`run["pending_paper_box"]` 已删，替代者 `_close_awaiting_paper`——ok_paper=True 工单正常收尾；False 报警 + 工单层判 NG（`_complete_order` 增 `paper_missing` 参数，final=NG + forced_reason 留痕，**箱成绩不回改**）。
- **缺工单判定二选一**（`_paper_judge_mode`，migration m0003 两列）：`tail_paper_scan_alarm=True`（默认）= 扫新单判定——下一单扫码进来发现没放 → 报警 + 旧单 NG 收尾再开新单，无时限；`scan_alarm=False 且 timeout_s>0` = 时限判定——挂等待态时 `_arm_paper_timer` 布防一次性 `threading.Timer`（非常驻线程，不违背"全事件驱动"），到点 `_on_paper_timeout` 终局判定（最后探一次，到位 OK / 没到位报警+NG）；时限内扫新单被拒收提示稍候。组合非法兜底按扫新单。Timer 在收尾/清理时 `_cancel_paper_timer` 撤防；重启后等待态作废（`abort_in_progress_on_startup` 的 or_ 条件补 awaiting_paper，内存 Timer 已丢防幽灵在途）。
- **`on_step_detected`（v3.42.1 新入口，检测线程调入需错误隔离）**：结算 mixin `_record_out_of_seq_step` 探到序列外步骤即时通知——只认"放工单"标签，两个消费方：① 等放工单收尾态出现即完成工单（不必等下一次扫码/周期结算）；② **提前放工单报警（v3.43.1）**——非尾箱作业期间（或名义尾箱但实时进箱数=0，靠 `set_box_progress_getter` 注入的 `_real_box_progress_getter` 读 `_custom_mix.container_settled_item_total()` 区分"尾箱正做着/还没开始"，拿不到保守放行）出现放工单动作 → 每箱一次报警提醒纠正（`early_paper_alarm_box` 防轰炸），不改箱成绩不拦结算。
- **已完成(OK)工单重扫拦截（v3.42.1，migration m0002 两列，默认关）**：`on_scan` 单点守门——`block_completed_order_rescan` 开 + 扫的号不是在途单 + `_latest_run_completed_ok`（查该配置该单号 id 最大一条 run，status=completed 且 final_result=OK；查库异常按不拦处理不卡产线）→ 报 completed_order_rescan 报警不重新录入；完成但 NG 的单不拦（允许重扫补做）。
- **确认框闪退修复（v3.43.1，两处配套）**：① `_real_project_activator` 命中项目已是激活态 → 原地不动直接返回 True（重激活=重载模型几秒卡顿+重置检测运行时，会把同一次扫码链路里刚立起的人工确认定格抹掉）；② 扫新单判定路径的缺工单报警**延后**——`_close_awaiting_paper(defer_alarm=True)` 返回报警文案，调用方在 `_open_order`（含按规格切项目）之后再触发，定格立在切换之后。
- `_KIND_TO_EVENT_FIELD` 增 `early_paper`（复用 event_missing_paper 档）与 `completed_order_rescan`（event_completed_order_rescan）；`cleanup_for_testing` 撤全部 Timer + 清 `_box_progress_getter`。
- 回归：`tests/test_packaging_flow_coordinator.py` 扩到 91 例（含等待态/两种判定/提前放工单/重扫拦截/重启作废）+ BDD `packaging_flow_sliders.feature` 扩场景。

**v3.45 变更（箱标签扫码授权 + 包装工单同步，上述行号已漂移）**：

- **箱标签扫码授权（组⑧，默认关零差异）**：`box_label_scan_required` 开时每箱开箱进「等扫箱标签」态（run.status=waiting_label，`label_authorized`=False），扫到本工单标签才授权开做——两种放行模式：**取量模式**（`label_qty_enabled`，`_authorize_box_by_label` L1137）必须从复合串取到"本箱数量"才放行（扫到裸条形码/数量段缺失 → 报 label_qty_missing"请扫二维码勿扫条形码"继续等），取到的数量写 `current_box_scan_qty` 并 `_apply_box_target` 反设检测层容器目标（**逐箱扫码定数量**，尾箱不再靠整除算）；**纯凭证模式**扫到即放行、箱目标仍按工单级计划。
- `_extract_label_qty`（L326，staticmethod）：取段规则可配服务"段序不固定/格式变化不改代码"——配了 `label_qty_pattern` 正则在各段里找第一个**整段**命中的，否则按固定段号 `label_qty_segment`（1-based，按 composite_delimiter 拆）；float 解析取整（"54.00"→54），≤0 非法；无分隔符裸码天然取不出返回 None；正则无效退回按段号（不吞码留调试痕迹）。
- `on_cycle_started`（L1162，**新对外入口**，检测线程经 step_stats mixin `_notify_packaging_cycle_started` 调入、调用点错误隔离）：等扫态下工人没扫标签就开做（周期收进第一个步骤）→ 报 box_not_scanned 提醒，`unscanned_alarm_box` 每箱只报一次防轰炸；非等扫态/通道不参与 → 零开销返回。
- 未授权箱处置 `unauthorized_cycle_action`（默认 hold）：等扫态下来了周期结算按配置挂起/放行；重扫已授权标签按 `label_rescan_action`（默认 ignore）。收尾对账 `label_total_check`（需与取量模式同开）：`_complete_order` 收尾点核对"Σ各箱标签声明量（box_details.scan_qty） vs 工单排产量 slider_total"，不平报 label_total_mismatch **只提醒不改成绩**（两边都>0 才比，`_KIND_TO_EVENT_FIELD` 增 label_qty_missing / label_total_mismatch / box_not_scanned 三个事件映射键）。
- **包装工单镜像进工单管理**（`_sync_work_order` L1659，**默认开**，老库 NULL 视为开）：扫码开工的工单此前只存包装运行记录、工单管理页看不见没处清理——开工 upsert work_orders 为"生产中"，收尾推"已完成"、中止推"已取消"；数量按**箱口径**回填（planned=应做箱数，completed=已结算箱数，good/ng=OK箱/NG箱），滑块口径细节进 extra_data.packaging；同号已存在（如外部 MES 先推送过）不重建只刷状态与数量（单号唯一约束天然防重、原 source 保留），新建行 source='packaging'（前端工单页显示"包装扫码"来源标签）；**异常隔离**：同步失败不阻断包装状态机。
- `_row_to_dict` 快照扩组⑧ 11 键（含 3 个事件映射）+ `sync_work_orders`；run dict 增 `label_authorized` / `current_box_scan_qty` / `unscanned_alarm_box` 等字段。

**线程·锁·队列**：v3.43 前**不开后台线程**（模块头："全部事件驱动"）；v3.43 起时限判定模式有**一次性 `threading.Timer`**（`_paper_timers` config_id→Timer，挂等待态布防/收尾撤防，回调锁内跑并校验 run_uuid 防串单）；单把 `RLock`（`on_forced_settle_by_channel` 内再调 `on_forced_settle` 依赖可重入）；单例双检锁。

**上下游**：
- 上游：mes_hooks/扫码链（on_scan）、source 结算链（on_cycle_settled / on_forced_settle_by_channel）、API 层 packaging_flows.py（配置 CRUD/状态查询/手动扫码/强制结案/补做）、main.py 启动（reload_configs + wire_real_hooks + abort_in_progress_on_startup）。
- 下游（全部经钩子）：mes_puller（拉单）、channel_manager/VSM（事件报警、容器目标、步骤探测）、alarm_router（默认报警 event2）、mes_gateway（完成回推）、projects 内部函数（切项目）、PackagingFlowConfig/PackagingFlowRun ORM。

**关键状态变量**（均锁内）：
| 变量 | 语义 |
|---|---|
| `_configs` | config_id → 配置 dict（enabled 行快照） |
| `_channel_to_config` | channel_id → config_id 反向索引 |
| `_runs` | config_id → 进行中运行态（一个配置同时只追一张工单）；run 内 status ∈ order_loaded/running/pending_remediation/completed/aborted |
| run["pending_box"] | v3.23 少装挂起箱快照（box/sliders/target/is_tail/cycle/reason） |
| run["awaiting_paper"] | v3.43 工单「等放工单收尾」等待态（None=没在等；替代已删的 v3.35 pending_paper_box 快照）：各箱已全部落账只差放工单动作完成工单，{"since": 挂起时刻, "alarmed": 时限报警是否已报}；run.status 同步置 awaiting_paper |
| `_paper_timers` | v3.43 config_id → 一次性 threading.Timer（时限判定模式布防；扫新单模式不布防） |

**注释里的坑（原样摘录）**：
- L23-26（零差异底线）："没有任何 packaging_flow_configs 启用时, on_scan / on_cycle_settled 入口直接 return, 与不配置时字节级一致"。
- L296-300（insert_char）："上银: 标签是 JOB150700114-1, 但扫码枪丢了 '-' 扫成 JOB1507001141…先去掉已有同种符号再插, 保证扫到带符号的码也归一到同一结果(幂等)"。
- L442-443（漏箱判定时序）："先把当前正在进行的箱封箱结算 (扫'下一个标签'=上一箱封箱时刻), 再判漏箱 — 否则最后一箱 (靠扫新工单收尾) 会被误判为漏箱"。
- L1224-1225（_real_mes_fetcher）："此函数在扫码线程持锁内被调, 内含一次同步 HTTP. 包装节拍是秒级 (人工扫码), 短暂阻塞可接受; 若未来节拍变快需把 HTTP 移到锁外"。
- L837-838（缺油嘴 gate）："每箱 gate 若误卡会卡死整条线, 故未配置时退化为放行"——对比塞工单 gate 的 `_real_paper_order_probe` 拿不到工位返回 False（gate 守住），两个 gate 的失败取向相反，是有意设计。
- L533-536（pending_remediation）："已挂起等补做时, 检测层又来一个周期 → 忽略, 由人工补做/重做接口推进, 避免阻塞态被新周期覆盖"。
- L257-260（v3.35 复合条码取段）："正式产线箱标签常是多段拼接码 (如 ORD260300050-2|JOB260600268-13|54.00|<校验串>), 整串比对必不符…无分隔符的码 (工单纸直扫) 原样返回 → 两种码可混扫; 取段失败也原样返回不吞码"。
- L883-885（v3.35 尾箱挂起后的周期语义）："尾箱已挂起等放工单: 后续周期只用来探测放工单动作, 不当新箱结算 (尾箱之后本单没有下一箱, 该周期里只该有放工单/封箱这类收尾动作)"。
- L1403-1405（v3.35 探测遮蔽）："上面的探测优先读'上一个已结算周期'的步骤缓存, 会遮蔽当前还没结算的周期 — 尾箱挂起等放工单期间, 补放动作恰恰落在当前周期里, 这里补查实时步骤集"。
- 注意：`_TERMINAL_STATES` L92 包含 "short" 状态，但全文件没有任何代码把 status 置为 "short"（`_persist_run` L1138 引用该集合判终态）——疑似规划未用，记入疑点清单。

### 8. backend/services/workpiece_flow_coordinator.py（1085 行）

**职责一句话**：单机内多工位**串行**流水线协调器（RFC 11 v3.14.0）——同一工件依次过 N 个工位、全 OK 才合格；三种入口触发（scan/time_window/physical 策略对象）、FIFO 多件在途、NG 短路提前终态、超时 Timer 三策略处置；四个插件 hook（enter/station_done/short_circuit/completed，completed 可 override 最终结果）。

**核心类·函数**（类 `WorkpieceFlowCoordinator` L78；单例 `get_coordinator()` L49 双检锁；`reset_coordinator_for_testing` L59）：
- `_TERMINAL_STATES` L75 = {completed, timeout, short_circuited, aborted}。
- `__init__` L120：`_flows`（配置快照）/`_channel_to_flow`（反向索引，L99 注释"一个 channel 只能属于一个 flow…这里 last-write-wins"）/`_in_flight_runs`（flow_uuid→运行态）/`_flow_to_in_flight`（FIFO 队列）/`_triggers`（flow_id→策略对象）；单把 Lock。
- `reload_flows` L133：加载 enabled 配置；**in-flight runs 不动**（L137-139："配置变更 API 层应该已阻止有 in-flight 时修改"）；<2 工位的配置跳过 L160-161；detach 老 trigger 再 attach 新 trigger。
- `_build_trigger` L199：time_window / scan（M3）/ physical（M7）三策略类按需 import（ImportError 返回 None 容忍未实现）。
- `on_channel_removed` L224：只清 `_channel_to_flow`（AGENTS.md 不变量 4 的 workpiece-flow 一角）；flow 配置与 in-flight 不动（走 reload/timeout 自然终态）。
- `cleanup_for_testing` L234：取消全部 timer + detach trigger（防 Timer 线程跨用例污染）。
- `abort_in_progress_on_startup` L262：重启时把 in_progress run 全标 aborted（防孤儿 run 在前端假装 in-flight）。
- `on_workpiece_enter` L295：**入口主函数**——1 秒内同 serial_no 幂等去重 L319-328；FIFO 满拒绝并报警 event2（L340："FIFO 满是工件流速过快或上游卡住"）；建 state + 启 daemon `threading.Timer`（workpiece_timeout_ms）→**锁外落库**（L377 注释"避免长事务卡住 lock"）→fire `workpiece_flow_enter` hook。
- `on_cycle_started` L406：VSM start_cycle 后调——不属于 flow 的 channel 直接 return（独立工位）；锁外询问 trigger `evaluate_cycle_start`（time_window 模式：入口工位 cycle_start 即自动新工件入口）；锁内 FIFO 绑 cycle（取队列第一个 in_progress 且该站位空的 run）；没匹配 run 静默忽略 L467-469。
- `on_cycle_settled` L485：VSM end_cycle 后调——按 cycle_id 找 run，写 station_results；最后一站→all_ok 且无跳站（station_results 无 None）才 OK L532-539；中间站 NG 且 short_circuit_on_ng→short_circuited；否则推进 current_station_index；中间站完成 fire `workpiece_flow_station_done`；终态走 `_finalize_run`。
- `on_scan_received` L585：mes_hooks 扫码钩子点调（RFC11 优先路径）——不在 flow 返回 None 让 mes_hooks 走原 scan_pair 路径 L601；trigger `evaluate_scan` 决定是否触发；`_register_workpiece_safe` 复用 WorkpieceService.register 落库工件后 enter。
- `on_physical_signal` L637：物理 GPIO/Modbus 信号——无 channel 概念，遍历所有 physical 模式 flow 分别评估，返回首个触发的 flow_uuid。
- `_register_workpiece_safe` L688（db/project_id/serial 任一缺→None；异常隔离）。
- `_finalize_run` L725：**终态收尾（锁外调）**——取消 timer；timeout 时 fire `workpiece_flow_timeout` hook 且插件可 `override_timeout_action`（force_ng→NG / drop、alarm_only→final_result=None 不推 MES L766-772）；short_circuited fire 专用 hook；所有终态 fire `workpiece_flow_completed`（returnable，插件可 `override_final_result` 改判 L800-803）→写 DB 终态→NG 报警（取第一个 NG 站的 channel，找不到取末站 L816-826）→`_push_workpiece_result_safe`（复用 WorkpieceService.set_result 现有 MES 推送链）→从 in-flight 与 FIFO 清出。
- `_on_workpiece_timeout` L849：Timer 回调（后台线程，自取 SessionLocal）；已终态/已清出直接 return。
- DB 辅助（全部吞错防主流程崩）：`_insert_flow_run` L879 / `_update_run_station_cycles` L914 / `_update_run_station_results` L939 / `_update_run_terminal` L964。
- 集成辅助：`_push_workpiece_result_safe` L1001、`_fire_alarm_safe` L1010（alarm_router.trigger_alarm event2）、`_fire_hook_safe` L1017、`_merge_hook_overrides` L1028。
- 查询（PluginHost/API 用）：`list_flows` L1052 / `get_flow` L1056 / `list_in_flight` L1061（Monitor UI 诊断快照）/ `is_channel_in_flow` L1079 / `get_flow_id_for_channel` L1083。

**线程·锁·队列·定时器**：不开常驻后台线程（模块头 L23"全部事件驱动"）；每个 in-flight run 一个 daemon `threading.Timer`（超时）；单把 Lock（注意与 packaging 协调器不同，这里是普通 Lock 非 RLock——所有终态处理都刻意放锁外）；FIFO 队列为 `_flow_to_in_flight` 的 list。

**上下游**：
- 上游：mes_hooks `_handle_scan`（RFC11 优先路径调 on_scan_received）、source/VSM（on_cycle_started / on_cycle_settled，commit 成功后调）、外设信号（on_physical_signal）、API 层 workpiece_flows.py（配置 CRUD/reload/in-flight 查询）、channel_manager（on_channel_removed）、main.py 启动（reload_flows + abort_in_progress_on_startup）。
- 下游：flow_triggers 策略包（time_window/scan/physical）、WorkpieceService（register / set_result→带出既有 MES 推送链）、alarm_router、插件 hook×4、WorkpieceFlowConfig/WorkpieceFlowRun ORM。

**关键状态变量**：
| 变量 | 语义 |
|---|---|
| `_in_flight_runs` | flow_uuid → run 状态（station_cycle_ids/station_results 定长占位数组、current_station_index、timeout_timer、status） |
| `_flow_to_in_flight` | flow_config_id → [flow_uuid...] 按入队顺序（FIFO 绑 cycle 的依据） |
| `_channel_to_flow` | channel_id → flow_config_id（判"该工位是否走 flow 路径"的守门索引，mes_hooks 也间接依赖） |
| `_triggers` | flow_id → Trigger 策略实例（reload 时整体重建） |

**注释里的坑（原样摘录）**：
- L28-30（零差异默认）："没有任何 workpiece_flow_configs 启用时, 所有入口都直接返回, 与 v3.13 字节级一致"。
- L99（_channel_to_flow）："一个 channel 只能属于一个 flow (启用时 API 校验, 这里 last-write-wins)"。
- L309-310（on_workpiece_enter docstring）："FIFO 满 → 拒绝, 返回 None. 重复扫码 (同 serial_no 1 秒内) → 幂等忽略, 返回老的 flow_uuid"。
- L418（on_cycle_started）："若没有匹配的 in-flight run → 静默忽略 (扫码/物理模式下工件未到先开始 cycle)"。
- L536-537（跳站判 NG）："检查是否还有空位 (跳工位的情况)…final_result = OK if (all_ok and not has_skipped) else NG"。
- L749（hook）："workpiece_flow_completed 是 returnable, 插件可改 final_result"。
- 细节留意：`abort_in_progress_on_startup` L275 定义了 `now = time.time()` 但未使用（用的是 datetime.now(timezone.utc)）——无害小残留。

### 9. backend/services/wmax/ 目录（7 文件）

**职责一句话**：WMax IDManager 扫码器私有协议的完整逆向实现——二进制帧协议、手写 protobuf 编解码、三端口（55266 CMD / 55276 IMG / 55286 RPT）设备管理、UDP 发现、虚拟设备；整个协议是从 Wireshark 抓包 + 反编译 .NET 官方软件逆向而来，**不依赖 protoc**。

#### 9.1 `__init__.py`（1 行）
仅 docstring："WMax IDManager 扫码器协议实现"。

#### 9.2 protocol.py（419 行）
**职责**：二进制帧层——`[0x5A 0x5A][Flag:4B][SN:10B?][CmdType][CmdIdx][DataLen:4B?][HeadChk][Payload][DataChk:2B?][0xA5]` 的 pack/parse + TCP 粘包缓冲 + 图像载荷解析。
- Flag 位定义 L29-36（HasSN/HasData/IsProtobuf/IsResponse/DevClass）；`DEFAULT_FLAG` = IsProtobuf+FixedMount L36。
- `CmdType` L39：从 C# CommandType_E 完整映射的 60+ 命令码（HandShake=3、TurnOnOffVideo=7、SetConfigOpt=15、GetConfigOpt=16、Trigger=30、SendImageNew=49、RptCode=200 等）。
- `Command` dataclass L97 / `_checksum` L128（简单字节求和）/ `pack` L133（**data_len_size 参数支持 4B 标准与 3B 设备变体**）。
- `parse_modified` L204：流式解析——找帧头跳垃圾字节；**3B DataLen 变体自动探测** L253-266（4B 校验失败时按 3B 重算校验，命中则记 `detected_data_len_size=3`）；帧不完整记 needed_payload_len 等更多数据。
- `DataReceiver` L323：粘包/分包缓冲；缓冲区超 512KB 视为异常清空 L355-358。
- `ZImage` L365 / `parse_new_image` L376（CmdType=49，19B 头）/ `parse_old_image` L399（CmdType=14，15B 头）。

#### 9.3 messages.py（1279 行）
**职责**：手写 protobuf wire format 编解码（varint/tag/length-delim + google wrapper 类型），覆盖 WMax 配置树全部消息；字段号来源"反编译 Ntj.Reader.* 中 *FieldNumber 常量 + InternalWriteTo WriteRawTag 验证"（L13）。
- 底层：`_encode_varint/_decode_varint/_encode_tag/_decode_tag` L26-56；encode_field_* / encode_wrapper_*（Int32Value/DoubleValue/BoolValue/StringValue/BytesValue）L61-109。
- `_parse_raw_fields` L114 + `patch_message` L145：**字段级 patch 核心**——保留原始字段顺序，替换指定字段号，None=删除；这是"改设备参数不破坏其余 28K 配置"的基石。
- `decode_message` L169 + get_varint/get_bytes/get_string/get_wrapper_* 家族 L197-270。
- 命令编码：`encode_get_config_opt` L277（**坑注释 L280-283**："IDManager 抓包显示 config_id<0 时省略 field 1…之前带 field1 varint=-1 会编码 10 字节大负数, 设备不认"）、`encode_turn_on_off_video` L291、`encode_run_mode` L303、`encode_trigger` L318、`encode_force_ip` L339 等。
- 配置子树编解码（多数标注"基于 xxx.pcapng 逆向"）：SensorOpt L406-434（曝光/增益）、LightOpt L439-453、CommonOpt L463-507（解码超时/重试/倾角/镜像）、CodeOpt L525-579 + `BARCODE_TYPES` 18 种码制表 L515、Rect/ROI/ReadingOpt L589-665、MultiData/DataEditOpt L683-738（前后缀/无码字符串 NOREAD）、DataOutputFormat L741-784（分隔符，data_format.pcapng 逆向）、InputOpt/OutputOpt L815-906（**signal_mask 位掩码注释 L801-803**：1=OK, 4=错误, 1024=触发器忙，OK/错误与触发器忙互斥）、IndicatorOpt L917-959（mode 1=手动亮灯 3=仅扫描时自动亮灯）。
- `decode_rpt_code` L964：扫码上报解码，**两种帧变体**（完整帧/简短帧，L967-969）。
- `decode_handshake_resp` L1035 / `decode_rpt_read_rate` L1050。
- 配置树组装：`encode_bank_channel_opt` L1091 / `encode_bank_opt` L1106 / `encode_normal_config_opt` L1114 / `encode_set_config_opt` L1138 / `decode_config_opt_resp` L1155（解 GetConfigOpt 28K 响应为结构化 dict；只解析第一个 bank，L1215 `break`）。完整配置树结构注释 L1064-1088。

#### 9.4 device.py（1120 行）
**职责**：单台 WMax 设备的三端口 TCP 连接、命令收发（同步 fire-and-forget + async send_and_wait Future 配对）、参数读写（patch 优先）、图像/扫码回调。
- 常量 L38-44：DEFAULT_PORT=55266、IMG_PORT=55276、RPT_PORT=55286、心跳 10s。
- `WMaxDevice.__init__` L80：**`_data_len_size = 3` 默认**（L92-95 坑注释："抓包证实真实 WMax 设备使用 3B DataLen 变体. 发 4B DataLen 设备头校验通不过会 silent drop 整个命令, 导致所有请求超时…我们主动发送先假定 3B"）；`_pending_responses` dict[cmd_index→asyncio.Future]；三个回调 on_code_received/on_image_received/on_disconnected。
- `connect` L147：CMD→IMG→RPT 依次连（按抓包时序）；CMD 连上后 MSG_PEEK 探测"设备拒绝连接（冷却期）" L160-170；IMG/RPT 失败降级仅 CMD 模式；起三条 daemon 接收线程 `wmax-cmd/img/rpt-{ip}`。
- `disconnect` L245：shutdown→join(3s)→SO_LINGER(1,0) 强制关闭；清 pending futures。
- `_recv_loop` L289：CMD 口收发循环；空闲 >10s 发 HandShake 心跳；断开时触发 `on_disconnected`。
- `_send_img_ack` L345（10 字节 ACK 流控帧）/ `_img_recv_loop` L363（SendImageNew/SendImagePtcol→parse→回调→ACK）/ `_rpt_recv_loop` L415（RptCode→**dlen<80 的短帧直接丢弃** L432-434→decode→回调）。
- `_flash_good_read` L457：扫到新码短暂关/开视频制造灯闪（同码 3s 冷却）。
- `_dispatch_command` L487：**发送格式自适应**——receiver 探测到设备 DataLen 宽度变化就切换 `_data_len_size` L488-491；响应帧按 cmd_index 配对 Future（`loop.call_soon_threadsafe` 跨线程置结果 L552-553）。
- `send_command` L575（同步）/ `send_and_wait` L593（async+timeout）。
- 高层 API：`handshake` L627、`activate_rpt_reporting` L639（**坑注释 L642-645**："按抓包时序先发 GetConfigOpt, 再发 TurnOnOffVideo…省略 IDManager 也没发的 HandShake (设备对 CmdType=3 无响应, 只会超时)"）、`load_config` L662（**L664 坑**："必须带 payload: 抓包证实不带 payload 或带 field1=-1 设备都不响应"；缓存 `_raw_config_data` 供 patch）。
- 参数写入三层：`_build_config_payload` L693（有缓存走 patch，无缓存 from scratch）→ `_patch_config_payload` L731（normal_config 字段级 patch）→ `_patch_bank_opt` L789（第一个 bank 的子字段 patch，其余 bank 原样保留）；`_build_config_from_scratch` L827 兜底。
- `set_params` L870（下发不保存，成功后重新 load_config 刷新缓存）/ `save_config` L898（SetConfigOpt 带 startup_cfg_id 保存 Flash；失败回退 SaveConfig(55)，L909-912："部分设备不支持 CmdType.SaveConfig(55) 但支持此方式"）。
- `trigger_on` L975 / `trigger_off` L997：**三命令兜底**（TurnOnOffVideo + Trigger + SendTermCmd "LON"/"LOFF"，L976-980："三个都发, 哪个被扫码器接受就哪个生效"）。
- `flash_and_scan` L1015（测试用：打光 N 秒收码，tap 原回调不丢）；reboot/reset_to_default/indicate_device/set_run_mode/read_rate_test 等运维 API L1067-1100。

#### 9.5 manager.py（483 行）
**职责**：WMaxDeviceManager 全局单例——多台设备生命周期、已知设备持久化（`DATA_DIR/data/wmax_known_devices.json` L22-27）、UDP 发现+自动连接、给 API 层的异步包装。
- `get_wmax_manager` L30 双检锁单例。
- `_load/_save_known_devices` L50-68 / `_remember_device` L70 / `_forget_device` L83。
- `_register_scan_callback` L108：**RPT 扫码→ScannerService 的桥**——`dev.on_code_received` 转 `svc.inject_scan_result(dev.ip, barcode)`（scanner.py §2 的注入入口）。
- `connect` L125（已存在但断开先清理再重建）/ `disconnect` L150 / `get_device` L161（**只返回 connected 的设备**，断开返回 None）/ `get_all_status` L170。
- 参数操作异步包装 L179-368：handshake/load_config/set_params/save_params/auto_focus/tune/trigger/turn_on_video/get_image(base64)/reboot/reset/indicate/run_mode/read_rate_test——全部"找 dev→未连接报错→转发"。
- `auto_discover_and_connect` L374：UDP 发现→逐台 connect→**连接后立即 activate_rpt_reporting**（L413-416 坑注释："之前担心'灯一直闪'其实是误判, 而 ondemand/trigger_on 在现场 WMax 固件上根本触发不了识别, 导致扫码器完全静默"）→再恢复 known_devices 里 UDP 没发现的设备（直连+激活）。
- `shutdown` L478：关全部设备。

#### 9.6 discovery.py（209 行）
**职责**：UDP 设备发现——文本（`4f3d-FND` 广播到 10000 口）与二进制（FindDevice 帧到 55266 口）双路并行（ThreadPoolExecutor 跑阻塞 recvfrom，asyncio.gather 汇合）。
- `_get_subnets` L32：`ip -4 -o addr show` 枚举网卡广播地址；失败兜底 192.168.0/1.255 L64。
- `discover` L71：二进制结果优先，文本仅在二进制为空时兜底 L97-104。
- `_discover_binary` L141：目标=全局广播+各子网广播+**常见尾号单播扫描**（.1/.100/.200/.50/.150/.2/.10/.254，L162）；响应用 DataReceiver 拆帧后 `_parse_device` L196（按 sn 去重入 dict）。

#### 9.7 virtual_device.py（244 行）
**职责**：VirtualWMaxDevice——模拟 WMaxDevice 全部接口（UI 预览/功能演示用），无 TCP；固定 IP 192.168.1.200 / SN WMAX-VIRTUAL-001。
- `_default_config` L45：逼真默认配置树（16 种码制、曝光/增益、ROI、data_edit 等）。
- `trigger_on` L168：生成 `DEMO-{5位随机}` 模拟码；`get_last_code` L220：读码率测试运行中会顺手递增模拟统计（95% 成功率）L221-231。
- `get_status` L234：带 `"virtual": True` 标记。

**线程·锁（目录汇总）**：每台真实设备 3 条接收线程（cmd/img/rpt）+ 发送锁 `WMaxDevice._lock` + 闪灯 Timer；manager 仅单例锁（`_devices` dict 本身无锁）；discovery 每次 2 个临时线程池 worker。

**上下游（目录汇总）**：
- 上游：scanner.py（`_ensure_wmax_connected`/`_wmax_trigger`/自动发现触发）、API 层 `backend/api/wmax.py`（35+ endpoint 直调 manager）。
- 下游：ScannerService.inject_scan_result（唯一业务出口——条码进 MES 的桥）；其余是纯设备 IO。

**注释里的坑（目录级，原样摘录）**：
- device.py L92-95："抓包 (1111.pcapng/222.pcapng/333.pcapng) 证实真实 WMax 设备使用 3B DataLen 变体. 发 4B DataLen 设备头校验通不过会 silent drop 整个命令, 导致所有请求超时"。
- messages.py L280-283："config_id<0 时省略 field 1, 只发 field 2 (BoolValue true) = 4B `12 02 08 01`, 设备立即回 28K 配置. 之前带 field1 varint=-1 会编码 10 字节大负数, 设备不认"。
- device.py L645："省略 IDManager 也没发的 HandShake (设备对 CmdType=3 无响应, 只会超时)"。
- manager.py L413-416："之前担心'灯一直闪'其实是误判, 而 ondemand/trigger_on 在现场 WMax 固件上根本触发不了识别, 导致扫码器完全静默"（与 scanner.py L30-35 的 v2.7.7c 教训同源）。
- device.py L976-980（trigger_on 三命令兜底）："三个都发, 哪个被扫码器接受就哪个生效"。

### 10. external_device 外设/称重服务族（5 文件 + external_alarm.py）

**职责一句话**：通用外部设备（称重器/传感器）接入——8 种协议驱动线程收原始数据 → 4 种解析模式出结构化字段 → 稳定值判定/瞬时配对 → 校验 → 按 data_target 派发到 ClusterCollector 或 MES extra_fields；v3.31 起同时逐帧喂给称重投料引擎与插件 hook。

#### 10.1 external_device_models.py（58 行）
**职责**：`DeviceConnection` dataclass（从 external_device.py 抽出避免 mixin 循环 import，L1）。
- 配置字段 L10-38：device_role（weight/…）、protocol、parse_mode/parse_config、station_id/channel_id、data_target（cluster/extra_fields/both）、validation_rules；v2.7.5 稳定判定四参数（stable_enabled/stable_delta=0.05/stable_count=5/zero_threshold=0.05）+ 有重无码告警两参数；v3.1.1 `pairing_mode`（"stable" 老逻辑 / "instant" 扫码瞬间绑最近读数）。
- 运行态字段 L40-58：status/last_data/last_parsed/last_error、`_thread`+`_stop_event`、**`_command_queue`（deque，L48-50 注释："外部(去皮/置零等)控制指令排入此队列, 由设备线程在下一轮轮询时取出发送, 避免多线程并发写同一串口"）**、稳定判定运行态（_stable_samples/_stable_state(idle|stabilizing|stable)/_stable_value/_last_reported_value/_weight_onset_time/_no_barcode_alarm_fired）。

#### 10.2 external_device.py（413 行）
**职责**：ExternalDeviceService 核心（设备管理/启停/状态/条码注入/主动指令），协议循环、pipeline、连通测试拆进三个 mixin（L38-42）。
- `start_all` L51（启动全部 enabled 设备）/ `stop_all` L63 / `add_device` L126 / `remove_device` L129 / `get_all_status` L134。
- `cleanup_old_logs` L69：v2.7.9 定时清理过程日志——**只删 box_serial 为 NULL 的记录**（有扫码绑定的业务日志保留），locked 3 次退避。
- `set_barcode` L155：**扫码器→外设的条码注入口**（scanner.py 配对派发调这里）；docstring L158-170 讲透两种配对模式——stable="扫码迟到→用缓存稳定值补发一次 dispatch，否则'先上称再扫码'场景永远卡住"；instant="扫码瞬间用 last_parsed 最近读数（不论稳定）派发, **派发后立即清 buffer** 防止下一帧误用同一条码；秤没出过读数则留 buffer 等下一帧兜底"。
- `_instant_dispatch_on_scan` L207：instant 派发实现（weight 取 last_parsed.weight 或 _raw_value，非数字跳过）。
- `send_command` L248：去皮 T/置零 Z 指令排入 `_command_queue`（仅 serial_command 与 mock_weight 支持）。
- `test_connection` L266：按协议分发到 Test mixin。
- `simulate_raw_data` L286：调试入口——无真实外设模拟一条数据；full_chain=True 走真实 `_on_raw_data` 链路，False 只解析+写日志不派发。
- `_start_device` L364：ORM → DeviceConnection → 起 daemon 线程 `extdev-{id}` 跑 `_device_loop`。
- 单例 `get_external_device_service` L409（**无锁裸单例**，与其它服务的双检锁不同）。
- 疑点：`self._lock = threading.Lock()` L48 全族 grep 无任何使用——死变量；`_connections`/`_barcode_buffer` 实际无锁并发读写。

#### 10.3 external_device_protocols.py（803 行，v3.41 复核）
**职责**：协议接入循环 mixin——8 种协议每设备一线程，收到原始数据统一走 `_on_raw_data`。
- `_device_loop` L30：按 protocol 分发（tcp/modbus_tcp/serial/serial_modbus_ascii/serial_continuous/serial_command/http_poll/mock_weight）；异常→断线→指数退避重连（2s 起、封顶 30s）L63-66。
- `_tcp_loop` L68：TCP 文本流+分隔符拆行（delimiter 经 unicode_escape 还原 L83-84）。
- `_modbus_loop` L107：pymodbus TCP 轮询 holding/input 寄存器。
- `_open_serial_with_retry` L154：**串口打开带 3 次重试**——L158-165 坑注释："解决 Windows 下 PermissionError(拒绝访问)…上次连接或测试关闭后 Windows 串口资源未完全释放…间隔 1 秒后再试往往就能成功。v2.7.17: 漏 @staticmethod 导致 self.xxx(port=...) 调用时 self 落到 port 位置参数 → 'got multiple values for argument port'"。
- `_serial_loop` L198：串口被动收+分隔符拆行。
- Modbus ASCII 工具：`_modbus_ascii_lrc` L247 / `_build_modbus_ascii_read` L252（**L255-256 地址约定**："说明书地址 4xxxx → 实际寄存器地址 = 4xxxx-40001"；**L262-263 坑**："v2.7.17: 之前误用宿主 service 类名(本模块没 import 它), 触发 NameError"）/ `_parse_modbus_ascii_response` L269（容错多帧粘包：按":"拆片段取第一个 LRC 通过的）。
- `_serial_modbus_ascii_loop` L320：主从轮询模式；**L340-344 大坑注释**："v2.7.17: 把 open+主循环包成'外层重连循环'. 之前 open 在 while 外, 内层 except 只 log 不重连, 导致 USB 拔插后句柄失效就 EIO 死刷, 重新拔插也救不回来(新设备号变 ttyUSB1 了)"；EIO/ENODEV/ENXIO/EBADF/EACCES 判定致命→关串口→5s backoff 重连 L433-443；字节序四档（H4H3L2L1/L2L1H4H3/H3H4L1L2）+ 有符号补码 L411-424。
- `_serial_continuous_loop` L473：设备主动推送模式；data_format=modbus_ascii 时帧内解析、否则按分隔符拆 L516-542。
- `_mock_weight_loop` L579：**模拟称重协议**（v3.31）——按 mock_script 时间轴（空盘→皮重→爬升→到量稳定）生成读数，复用 `_on_raw_data`，"下游解析与真秤完全一致"；响应 `_command_queue` 的 T/Z 指令模拟去皮（L614-628 emit 闭包）；播完可 loop 且复位去皮基准 L648。**v3.41 变更（段播放按墙钟计时，L639-644）**：之前按"睡眠次数×间隔"累计段时长、不含 emit 处理耗时，长脚本越播越慢（注释实测 4 分钟漂约 20s），与外部时间轴（如视频源）同步的仿真场景整体错位——改为 `time.time()` 墙钟判段结束；"真秤无此问题, 仅模拟协议受影响"。
- `_serial_command_loop` L650：**指令应答模式**（安衡台秤等）——每轮先发队列里的控制指令（T/Z，应答丢弃不当重量 L718-723），再发查询指令 R 读一帧；重连结构沿用 modbus_ascii loop。
- `_http_poll_loop` L770：HTTP GET/POST 轮询。

#### 10.4 external_device_pipeline.py（517 行）
**职责**：原始数据 pipeline mixin——解析/稳定判定/校验/派发/日志。
- `_on_raw_data` L18（**全族数据总入口**）：解析 → 插件 hook 广播 L29 → **weighing 引擎喂数 L33-47**（"只有 logic_mode='weighing' 的通道会被引擎消费…引擎自己做稳定判定, 故喂原始每帧(不经下方稳定门控)" L31-32）→ instant 模式分支 L52-68（"不走稳定状态机, 也不主动每帧 dispatch. 只更新 last_parsed…仅在 buffer 里有待消费条码时才派发一次然后清掉; 这是'扫码先到, 称重后到'的兜底通道" L49-51）→ stable 门控 L73-76 → 校验+有重无码告警+日志+派发 L78-88（**校验失败仍发送**，只 warning L86-87）。
- `_fire_external_device_hook` L90：`post_extdev_data` 非 returnable hook，"插件挂了不能影响外设数据主链路"。
- `_extract_weight` L118：weight 缺失时兜底扫第一个数值字段。
- `_handle_weight_stability` L136：**稳定值状态机**（docstring L140-143：idle→stabilizing→连续 stable_count 次 max-min<=delta→stable 首次上报；漂移>delta 回 stabilizing；<zero_threshold 回 idle）；空载清 buffer+全状态 L153-164；稳定取**窗口中位数**为上报值 L187-190；节流："稳定窗口内只对下游上报一次, 除非值变化超过 delta"，但 **v2.7.9 决策：日志表不节流**（"静置 stable 日志每帧照写, 方便前端实时观察" L196-203）。
- `_check_weight_no_barcode_alarm` L212：有重无码告警——稳定非零且无条码持续 N 秒→一次性告警→MES Gateway `weight_no_barcode` 事件 + alarm_router 报警灯。
- 解析四模式：`_parse_direct` L285（weight 角色正则抠第一个数字）/ `_parse_regex` L293 / `_parse_split` L312 / `_parse_json` L328。
- `_validate` L346：validation_rules 逐字段 min/max。
- `_dispatch` L367 → `_dispatch_to_cluster` L376（**无 station_id 或无条码不注入**；按 validation_rules 算 is_good，伪造 cycle_context{cycle/workpiece/device_data} 调 `collector.receive_station_report`——外设走的是与视觉工位同一条集群汇总链）/ `_dispatch_to_extra` L436（合并进 MES Gateway 的 per-channel extra_fields）。
- `_log_data_sampled` L449：**B2 优化坑注释 L452-456**："称重器每秒推多帧, 原本每帧写一条 external_device_logs, 高频抢 SQLite 锁拖慢检测引擎写库。这里只对'过程噪声'节流到 ~1 条/秒, 业务记录一律不节流"。
- `_log_data` L465：**坑注释 L469-471**："v2.7.9: 原本 except Exception: pass 直接吞错导致前端'数据日志'永远空。现在做 3 次 locked 退避重试"。

#### 10.5 external_device_test.py（150 行）
**职责**：连通性测试 mixin——每协议一个 `_test_*`，只开一次连接/发一次查询即回结果。
- `_test_tcp` L18 / `_test_modbus` L28 / `_test_serial` L45 / `_test_serial_modbus` L56（serial_continuous 分支：开口静听 2 秒 L67-76）/ `_test_serial_command` L104（发一次查询指令看是否回帧）/ `_test_http` L143。
- 特点：串口类"打开成功但无响应"也返回 success=True 带提示语（L75-76、L98-99、L133-135）——**success 语义是"链路可用"而非"设备确认在线"**。

#### 10.6 external_alarm.py（252 行，v3.41 复核）〔外设族邻接：在途报警台账〕
**职责**：外部生产管控系统报警闭环的通用台账——出站推送时登记（record_active_alarm）、外部回推消除命令按唯一键匹配消除（clear_alarms）、软件内一键消除（clear_all_active_alarms，v3.39）、监控页轮询未消除报警（list_active_alarms）；"唯一区分键参照客户约定 = task_no+product_code+step_code+operator，匹配字段与去重窗口由调用方(入站配置)传入, 本模块不含任何客户分支"（L9-10）。
- 5 原生列 + extra_data.ext 命名空间扩展维度（键名正则防注入 L33）；`_match_col` L52：原生列走列等值、扩展维度走 json_extract。
- `record_active_alarm` L74：dedup_sec 窗口去重，"与消除 clear_alarms 用同一套 match_fields, 避免去重口径与消除口径不一致"（L81-82）；**L105 坑注释**："显式落本地时间: 去重窗口与 datetime.now() 同口径, 不被 SQLite func.now()(UTC) 错开"。
- `clear_alarms` L117：只对有值字段加条件；**"一个匹配字段都没有 → 视为无效消除 (避免空条件误清全表)"** L135-137。
- `clear_all_active_alarms` L152（**v3.39 变更**，川南反馈"上游不回推消除命令时报警一直挂着，软件内没有任何出口"）：软件内手动消除全部在途报警的运维出口——与 clear_alarms 的按键匹配语义**刻意分开**，不做字段匹配（L155-156 注释："这是'人在界面上一键清空'的运维出口"）；channel_id 给定只清该工位；clear_source 落 "manual_ui" 等来源标记留审计。上游两个入口：入站 API `POST /mes/inbound/active-alarms/clear-manual`（守门 alarm_banner.allow_manual_clear，默认关）与监控页"清零"顺带消除（clear_on_counter_reset，默认关）。
- `find_recent_active_alarm` L202：供出站网关推送前做"同任务同标签 N 秒去重"（川南 v4 第 4 条）。
- 上下游：mes_gateway（登记+推送前去重）、mes_inbound（消除命令 + v3.39 手动消除端点）、监控页横幅 API。

**线程·锁（族汇总）**：每设备 1 条 daemon 线程 `extdev-{id}`；指令下发经 `_command_queue`（deque，单消费者=设备线程）避免并发写串口；`ExternalDeviceService._lock` 声明但从未使用（疑点）；DB 写入以 locked 退避重试代偿。

**上下游（族汇总）**：
- 上游：真实硬件（TCP/串口/HTTP）、scanner.py 的配对派发（set_barcode）、API 层 `backend/api/external_device.py`（CRUD/测试/模拟/指令）。
- 下游：weighing_engine.feed_weight（v3.31 原生称重模式）、cluster_collector.receive_station_report（data_target=cluster）、mes_gateway extra_fields / weight_no_barcode 事件、alarm_router 报警灯、external_device_logs 表、插件 hook post_extdev_data。

### 11. backend/api/ MES 相关路由（10 文件）

> 路由档案从简：前缀 + 端点清单 + 特殊逻辑/坑。鉴权统一 `require_perm(...)`（读端点多数不鉴权）。

#### 11.1 api/mes.py（691 行）— 前缀 `/mes`
**职责**：工单/批次/工件/缺陷/缺陷代码字典的 CRUD 门面，业务全部委托 WorkOrderService/WorkpieceService/DefectService（模块级单例 L20-22）。
- 工单：GET/POST `/orders` L213/L237、**POST `/orders/receive` L253（M2M 外部推送工单 upsert，`require_api_key("mes.receive")`——"auth=off 放行, auth=on 必须 X-API-Key" L252；extra_data 合并不覆盖 L265-268）**、GET/PUT/DELETE `/orders/{id}`、POST `/orders/{id}/status` L321、GET `/orders/{id}/summary` L355、PUT `/orders/{id}/extra-data` L364（合并模式）。
- 批次：POST/GET `/orders/{id}/batches` L389/L408。
- 工件：GET/POST `/workpieces` L427/L453、GET `/workpieces/{id}` + `/trace` L481（工件+检次+缺陷全链追溯）、POST `/workpieces/{id}/action` L497（rework/scrap/confirm 三动作）、GET `/workpieces/search/{keyword}` L522、DELETE L533（v2.7.17 级联清 Inspection+DefectRecord）。
- 缺陷：GET/POST `/defects`、GET `/defects/pareto` L597、DELETE `/defects/{id}` L608；缺陷代码字典 CRUD `/defect-codes` L628-691。
- 特点：每端点自管 SessionLocal + try/finally close；v3.1.0 工单绑定范围三字段（binding_scope=project/channels/cluster + target_channels/target_stations）贯穿 Create/Update/serialize L46-48。

#### 11.2 api/mes_gateway.py（577 行，v3.41 复核）— 前缀 `/mes/gateway`
**职责**：出站 MES 连接 CRUD + 测试 + 手动推送 + 健康探测 + 拉取入口 + extra_fields + 通讯日志 + 熔断器状态面（v3.38）。
- **B1② 并发派发开关**：GET/PUT `/async-dispatch` L82/L88——"开 → cycle_end 外推甩到'每工位一条'的执行器, 慢/挂的客户 MES 不再堵住整个 hook 队列; 关(默认) → 原内联派发"（L79-81，存 SystemConfig 即时生效）。
- 连接 CRUD：`/connections` L140-252 + GET `/connections/by-channel` L179（bound_channels 反查）。
- **v3.38 变更（熔断器状态面）**：`_serialize_conn` 附带 `circuit` 快照（L111，`_circuit_state_safe` L115 兜底 `{open:False}`，前端连接列表可提示"端点疑似离线"）；两处**复位口**——PUT `/connections/{id}` 保存后 `reset_circuit`（L219-224，"改过配置(可能换了地址/修了鉴权) → 立即按新配置重试"）、POST `/connections/{id}/test` 成功后 `reset_circuit`（L384-390，"测试成功 = 端点已恢复"）。
- POST `/connections/{id}/test` L329：**三种事件的逼真测试上下文**（`_build_test_context_cycle_end/box_complete/box_timeout` L255-327，box 结构"完全模拟 cluster_collector._check_and_dispatch 产出"）；docstring 坑 L330-337："走和实时推送完全一致的预处理管道: label_mapping → _apply_auth_to_headers, 确保测试通过 == 真实推送一定通过。push_on_result 在测试里**故意不应用**: 测试就是为了看请求能否发到对端"。
- POST `/connections/{id}/push` L415：手动重推指定 cycle/session。
- A2 健康探测：GET `/health-status` L442（不鉴权轮询）+ POST `/connections/{id}/probe-now` L451。
- v3.20 拉取：POST `/pull-test` L463（未保存的编辑中配置也能测）+ POST `/connections/{id}/pull` L472（dry_run+max_items=1="试同步 1 条看看"）。
- extra_fields：POST/GET `/extra-fields` L508/L516（Monitor 页实时输入）+ GET `/extra-fields-schema` L521（聚合所有启用连接的 schema 并按 key 去重）。
- GET `/logs` L552：MESCommLog 分页查询。

#### 11.3 api/mes_inbound.py（414 行，v3.41 复核）— 前缀 `/mes/inbound`
**职责**：入站接收端点（无鉴权 M2M）+ 配置管理 + 动态路径别名注册 + 在途报警查询/手动消除（v3.39）。
- 接收：POST/GET `/task` L48/L53（"少数系统用 GET+URL 参数推任务" L55）、POST/GET `/alarm/clear` L59/L65；统一走 `_handle_inbound` L77（来源校验→body 限长 413 L93-94→解析→handler→按 code_key 映射 HTTP 状态→按 response.format 组 JSON/XML→落 MESCommLog）。
- **`register_inbound_aliases` L149**：按配置 receive_paths 在**根路径**动态注册别名路由（客户可自定义接收 URL）；"先移除旧别名再重建(支持增删改)"；护栏：path 必须以 / 开头、**禁 /api/ 前缀**、与已有路由冲突跳过 L180-188；PUT `/config` 保存后即时重建 L332-339，无需重启。
- `_parse_inbound_body` L207：json/form/query/xml + auto 兜底链（json→form→query）；merge_query_params 默认并入 URL 参数（body 同名优先）L260-264。
- `_xml_to_dict` L270：stdlib 极简 XML→dict，剥命名空间、同名子标签聚 list（SOAP 按信封路径映射）。
- GET/POST `/health` L343（川南协议探活，格式沿用入站配置）；GET `/active-alarms` L358（监控页横幅轮询，不鉴权）；GET `/logs` L393（direction='inbound'）。
- **POST `/active-alarms/clear-manual` L372（v3.39 变更）**：软件内手动消除全部在途报警（川南反馈：上游不回推消除命令时报警一直挂着）——双守门：入站配置 `alarm_banner.allow_manual_clear` 未开直接 403（默认关，"保持'只能由外部系统回推消除'的对接契约"）+ 权限 `monitor.detection.advanced`（"对齐监控页'清零', 操作员级别不可误触"）；转 `clear_all_active_alarms(clear_source="manual_ui")` 留审计。
- 细节：`_log_inbound` L300 的 url 字段按 event_type 写死两个路径、method 恒写 "POST"（即使实际是 GET/别名路径）——日志口径与真实请求可能不符。

#### 11.4 api/scanner.py（546 行）— 前缀 `/scanner`
**职责**：扫码器 CRUD + 发现 + 测试/触发/模拟 + 扫码日志 + scan_pair 收尾 + 按工位禁用开关。
- Schema L18-83：ScannerCreate/Update 覆盖服务层全部配置面（scan_mode/broadcast_settle_mode/primary_settle_channel/scan_d_* 容器跨线参数等 30+ 字段）。
- POST `/discover` L136：**纯 TCP 端口扫描**（"不走 WMax 协议，不会触发闪光" L140），线程池 50 并发探 x.x.x.1-254。
- POST `/devices` L166：**同 IP+port 去重 409**——坑注释 L171-174："现场常见: 自动发现保存了一次, 又点'保存到设备列表'再保存一次。USB 键盘扫码枪 (usb_hid) 无网络地址不参与地址唯一校验, 否则多把 USB 枪会互相撞 ':0'"；PUT L209 改地址同样查冲突；增/改/删都同步 ScannerService add/remove_device（热生效）。
- POST `/devices/test` L280（text_lon 省电路径 vs auto/wmax 三端口）/ POST `/trigger` L293（手动 LON）/ POST `/simulate` L306（模拟扫码——**额外落一条 ScanLog 标记 AUTO_QA_SIMULATED**，"真实扫码日志通常在 MES Hook 中异步补齐。模拟入口额外落一条可见日志, 让前端点'刷新'能立刻看到按钮确实生效" L322-323）。
- GET `/status` L341 / `/latest/{channel_id}` L347 / GET+DELETE `/logs` L356/L394。
- GET `/check-container-mode` L413：v3.4.0 scan_mode='D' 前端校验（非容器模式项目弹警告回退 A）。
- v3.3.0 scan_pair：GET `/scan-pair/active` L450（Monitor 轮询开窗状态）+ POST `/scan-pair/stop` L478（停止/待机时收尾最后一码窗口，弹窗[丢弃][结算]）。
- v3.4.2 禁用扫码：GET `/disable-status` L504 + POST `/disable-toggle` L519——docstring L522-534 完整语义（联动闭包 LOFF + 4 个守门点短路 + 持久化 scanner_runtime_state.json）。

#### 11.5 api/wmax.py（699 行）— 前缀 `/scanner/wmax`
**职责**：WMax 设备全功能面板 API（35+ endpoint），基本是 manager 的薄转发层 + 虚拟设备注入 + protobuf 调试端点。
- 发现：GET `/discover` L90、**POST `/auto-discover` L104**——发现+连接后**把 WMax 连接以负数 device_id（-9000 递减）直接注入 `svc._connections`**（让扫码器页出 Tab，L120-143），跳过已存在地址。
- 连接：POST `/connect`/`/disconnect`、GET `/status` L183。
- 参数：POST `/handshake`/`/load-config`/`/device-features`；PUT `/params` L233（下发不存 Flash）/ POST `/save-params` L264（存 Flash）；PUT `/output-config` L297（signal_mask 语义注释 L300）/ PUT `/indicator-config` L319（mode 1=手动 3=仅扫描亮灯）；预设组 POST `/preset/load`/`/preset/save` L340/L349（4 组）。
- 调焦：POST `/autofocus`/`/autotune`/`/cancel-tune`。
- 图像：POST `/video`/`/trigger-image`；GET `/image`（base64 dict）/`/image/raw`（二进制）/**GET `/stream` L425（MJPEG 流——async generator 轮询 dev.state.last_image，浏览器 img 原生支持）**。
- 触发/读码：POST `/trigger` L457、GET `/last-code` L470、run-mode PUT L481、读码率测试三端点 L493-518。
- 控制：POST `/reboot`/`/reset`/`/indicate` L523-544。
- 虚拟设备：POST `/virtual/create`/`/virtual/delete` L549/L570——同时向 ScannerService 注入/移除 device_id=-999 的假连接（`_inject_virtual_scanner_status` L632）。
- 调试：GET `/debug/config-raw` L590（递归解 protobuf 字段树）/ GET `/debug-config` L663（NormalConfig 字段清单，直接用 `msg._parse_raw_fields` 私有函数）。
- 特点：该文件多处直接操作 `mgr._devices` / `svc._connections` 私有 dict（L557-561、L578、L641-644）——API 层越过服务封装。

#### 11.6 api/cluster.py（314 行）— 前缀 `/cluster`
**职责**：集群配置 + 从机→主机数据上报/心跳 + box 汇总查询/清理。
- GET/PUT `/config` L57/L63——**计时三参数（heartbeat_interval_sec/slave_timeout_sec/box_scan_interval_sec）走 SystemConfig KV 不是 ClusterConfig 列，单独剥离**（L73-77，v3.29 去硬编码）；保存后 `invalidate_config_cache` L85。
- **POST `/report` L97（M2M，`require_api_key("cluster")`）**：副机推 cycle 结果到主机；非 master/standalone 拒收 400 L104-105；转 `collector.receive_station_report`。
- POST `/heartbeat` L176（M2M 同 scope）：副机心跳→`register_slave`；GET `/slaves` L190。
- GET `/boxes` L124（pending 内存态 + recent BoxSummary 分页）/ GET `/boxes/{serial}` L138（逐工位明细+summary）。
- DELETE `/boxes/{serial}` L200：删两表记录 + **"顺便释放进程内 box 锁"**（L215-219 调私有 `_release_box_lock`）；DELETE `/boxes` L229 批量清理四档 scope（all/pending/recent/older，older 必须 before_days>0）。
- GET `/health` L305：角色/station_id/enabled 探活。

#### 11.7 api/external_device.py（386 行）— 前缀 `/external-devices`
**职责**：外设 CRUD + 测试 + 条码注入 + 控制指令 + 模拟数据 + 数据日志。
- CRUD：GET `/` L127、POST `/` L146（**保存与连接解耦**——设备落库成功但连接失败时返回 payload 带 warning，"设备已保存但连接失败(可稍后在设备卡片上重试)" L161-168）、PUT/DELETE `/{device_id}` L328/L369（改后 remove+add 热重连）。
- `_sanitize_device_payload` L137：**"去掉字符串字段首尾空格, 避免 ' /dev/ttyUSB0' 这种肉眼看不见的坑"**。
- POST `/test` L185 / POST `/barcode` L199（**扫码注入口——权限用的是 `mes.scanner.edit` 而非 `mes.external.edit`**）/ POST `/command` L208（去皮 T/置零 Z/读数 R）/ POST `/simulate` L216（full_chain 可选走真实链路）。
- GET `/logs` L236 / DELETE `/logs` L265——**三层容错**（docstring L273-275）：synchronize_session=False 避免并发锁冲突、ORM 失败回退 raw SQL、locked 3 次重试 0.3s。
- **路由顺序坑（原样摘录 L324-325）**："动态路径必须放在所有静态路径之后，否则会抢先匹配。例如 DELETE /logs 会被 DELETE /{device_id} 误吞（device_id="logs" 解析失败 422）"。

#### 11.8 api/channel_groups.py（318 行）— 前缀 `/channel-groups`（RFC 10 CG.5）
**职责**：工位组（单机内多工位并行联动结算）CRUD + 运行时状态；"零差异默认: 没创建任何工位组时, 所有结算行为与 v3.12 完全一致"（L21）。
- 策略枚举 L78-84：synchronized_any_ng / synchronized_all_ok / independent / **master_slave（"配置位仅, 不实现"，POST 时显式 400 "暂未实现, 计划 v3.14" L107-111）**；timeout_action：fallback_independent / force_ng。
- `_validate_group_config` L87：至少 2 成员、id 不重复、非负。
- **`_check_member_uniqueness` L119——SQLite JSON 大坑（原样摘录 L124-128）**："SQLAlchemy `JSON.contains(int)` 在 SQLite 上退化成文本子串匹配, 比如组 [10, 11] 用 `contains(1)` 会命中 (子串 '1' 在 '[10, 11]' 里)。所以这里把所有 enabled=True 的组捞回来, 用 Python `in` 判断"。
- CRUD L176-299：POST 名字唯一+成员跨组唯一；PUT 部分更新取 merged 值再校验；**DELETE 仅 enabled=False 允许（409）** L289-294；每次 CRUD 后 `_reload_coordinator`（reload_groups 立即生效）。
- GET `/{id}/state` L302：查协调器内存（"当前简化版: 仅返回配置 + 是否在内存中" L314）。

#### 11.9 api/workpiece_flows.py（506 行）— 前缀 `/workpiece-flows`（RFC 11 M4）
**职责**：串行流水线配置 CRUD + in-flight 状态 + 历史 run 查询；结构与 channel_groups 同构（校验→唯一性→CRUD→reload→state）。
- 枚举 L114-117：trigger_mode（time_window/scan/physical）、scan_bind_strategy（entry/each_station）、settle_strategy（**仅 all_ok_required 一种**）、timeout_action（force_ng/drop/alarm_only）；fifo∈[1,20]、timeout∈[100ms,600s] L159-166。
- `_check_station_exclusivity` L169：**双重互斥**——flow 间互斥 + **与 channel_groups 互斥**（"工位组(并行)与流水线(串行)互斥, 同一通道不能同时属于两者" L203；channel_groups 查不到时静默放过，向后兼容 L208-210）。
- **PUT L363 前置检查 in-flight**："流水线有 in-flight 工件, 请等所有工件走完再修改"（409，L378-382）——与服务层 reload_flows "不动 in-flight run" 的假设配套。
- DELETE 仅 enabled=False（409）L439-443；GET `/{id}/state` L451（in-flight 快照）；GET `/{id}/runs` L471（分页历史）+ GET `/runs/{run_id}` L499。

#### 11.10 api/packaging_flows.py（621 行，v3.45 复核）— 前缀 `/packaging-flows`（v3.21+ 上银包装线）
**职责**：包装结算配置 CRUD（8 组 55+ 字段）+ 状态快照 + **扫码 HTTP 入口** + 强制结案/补滑块/重做三个现场操作端点。
- **v3.45 变更**：Pydantic 面加**组⑧箱标签扫码授权 10 字段**（`box_label_scan_required` / `label_qty_enabled` / `label_qty_segment` / `label_qty_pattern` / `label_rescan_action` / `unauthorized_cycle_action` / `label_total_check` + 事件映射 `event_box_not_scanned` / `event_label_qty_missing` / `event_label_total_mismatch`，全默认关零差异）+ `sync_work_orders`（包装工单镜像进工单管理，**默认 True**），Create/Update 双 Schema 同步；语义全在协调器（见其条目 v3.45 节）。
- Schema L44-112 即配置面全景：组①工单箱数来源 / 组②数量规格（fixed/by_spec 对照表）/ 组③标签校验（exact/strip_hyphen/digits_only/insert_char；**v3.35 变更**：新增复合条码取段 5 字段 `composite_label_enabled/delimiter/pick_mode/prefix/index` + 工单号识别规则 `order_code_pattern`，均默认关=存量零差异）/ 组④异常策略（on_mes_fail=block|offline、on_short_box=redo|void 等）/ 组⑤收尾回推 / 组⑥异常→项目事件映射 / 组⑦滑块口径+尾箱+自动切项目+塞工单 gate（v3.22）+ 缺油嘴 gate（v3.23）+ **v3.35 `tail_paper_as_close_action`（放工单=尾箱收尾动作，挂起快照闭环，默认关=老行为）**。枚举校验表 `_ENUMS` L182-194。
- `_check_channel_exclusivity` L214：一工位只能属一个启用配置。
- CRUD L304-423：与前两者同构（名字唯一、DELETE 仅 disabled、reload_configs 即时生效）；`_validate` L197 只校验出现的字段（支持部分更新）。
- **POST `/scan` L425**：USB 扫码枪的 HTTP 扫码入口——"前端捕获原始码后 POST 进来驱动状态机。没有任何启用配置时返回 handled=False, 前端据此回退到默认的'扫码拉单/绑件'行为 — 与不配置时零差异"（L429-433）；注意先 `on_scan` 再 `resolve_config_id`，无匹配配置也已喂过 on_scan。
- **POST `/{id}/force-settle` L465**：强制结案——必填理由+审计留痕（forced_by/forced_reason 落 run），**独立权限位 `system.packaging_flow.force_settle`**（"admin/engineer; 操作员无 → 403"）；"强制走 settle 收尾(忽略配置 keep/abort), 未满箱按 on_forced_stop_partial 判合格性"（L472-474）。
- POST `/{id}/supplement-sliders` L516（少装箱补数量直接落账，不重置周期）+ POST `/{id}/remediation-redo` L548（丢弃本箱等下周期重结算）——两者权限用 `monitor.detection.ack`（操作员可点）。

## 二、MES 域综合

### 2.1 扫码→绑定→结算→推送全链接力点

条码有四条进入通道，最终都汇入同一条主链：

1. **文本 TCP 扫码器**（LON/LOFF 协议）：`scanner.py` 每设备一条监听线程收码。
2. **WMax 二进制扫码器**：`wmax/device.py` RPT 端口（55286）收 RptCode → `wmax/manager.py::_register_scan_callback`（L108）转 `ScannerService.inject_scan_result`——wmax 目录唯一的业务出口。
3. **USB 键盘扫码枪**：后端不建连接（`scanner.py::_start_device` L1256 usb_hid 直接 return），前端捕获键盘流后走 HTTP（`POST /scanner/simulate` 或包装线 `POST /packaging-flows/scan`）。
4. **入站/API 注入**：`POST /external-devices/barcode`（外设配对）、`simulate_scan`（调试）。

主链接力（每个箭头都是一次文件间交接）：

```
scanner.py（解析/去重/scan_mode 门控/配对派发）
  ├─→ external_device.py::set_barcode（配对组：称重器 stable/instant 绑码）
  │      └─→ external_device_pipeline::_dispatch → cluster_collector（receive_station_report）或 gateway extra_fields
  ├─→ packaging_flow_coordinator::on_scan（工位/设备归属某启用包装配置时：拉单开工单/箱标签校验）
  └─→ mes_hooks.py::on_scan_received（queue 入队, 唯一异步桥）
         └─ _handle_scan：WorkpieceFlow 优先 → 注册工件 → pending
             cycle_start：pop pending → inspecting → link_to_cycle
             cycle_end：set_result/缺陷/工单+1 → 预 commit 释放写锁
                ├─→ workpiece_flow_coordinator::on_station_cycle_end（串行流水线台账）
                ├─→ packaging_flow_coordinator::on_cycle_settled（包装线进箱计数）
                ├─→ cluster_collector（standalone/master 本地聚齐; slave HTTP 上报）
                │      └─ box 聚齐/超时 → gateway.dispatch("box_complete"/"box_timeout")
                └─→ mes_gateway.dispatch("cycle_end")（B1② 开关决定内联或每工位线程）
                       └─ mes_adapters 6 种协议（v3.35 起含 database 直写）→ 外部 MES（MESCommLog 留痕）
```

结算权的归属是互斥切分的：channel_groups（并行联动）/ workpiece_flows（串行流水线）/ packaging_flows（包装箱）三者在 API 层做跨表工位唯一性校验（`workpiece_flows.py::_check_station_exclusivity` L169 同时查 flow 间与 group 间互斥；`packaging_flows.py::_check_channel_exclusivity` L214 查自身），保证一个 channel 的 cycle_end 只被一个协调器消费或走默认独立结算。

### 2.2 集群三角色数据流

- **standalone**：`_cluster_dispatch` 判定本机即聚合点，cycle_end 直接 `receive_station_report` 本地落 BoxAggregation；聚齐/超时逻辑与 master 完全一致（等价于"单机集群"）。
- **master（host）**：本地工位走内存直调；远端工位经 `POST /cluster/report`（M2M API Key scope=cluster）进来；per-box `threading.Lock` 串行化同箱并发；聚齐 → `_check_and_dispatch` 组 aggregated → gateway 推 box_complete；后台超时线程按 box_scan_interval 扫描未齐箱 → box_timeout。副机在线状态由 `POST /cluster/heartbeat` 维护（内存 dict，slave_timeout_sec 判离线）。
- **slave**：cycle_end 时 `mes_hooks::_cluster_dispatch` 把 cycle_context 序列化 HTTP POST 到 master_url；心跳线程周期上报 hostname/project/detecting；**slave 本机不推 MES**（返回 True 让 gateway 分支跳过），推送权集中在 master。
- channel→station 映射：优先 `channel_station_map`（cluster 配置），否则多通道自动拼 `-{channel_id}` 后缀（mes_hooks L1622-1629）。

### 2.3 wmax 协议在域内的位置

wmax 目录是纯**设备接入层**：帧协议（protocol.py）→ 手写 protobuf（messages.py）→ 单设备三端口管理（device.py）→ 多设备单例（manager.py）→ UDP 发现（discovery.py）→ 虚拟设备（virtual_device.py）。它对 MES 域的**全部**业务贡献收敛为一行调用：`svc.inject_scan_result(dev.ip, barcode)`（manager.py L118）；反向依赖也只有 scanner.py 的连接保障（`_ensure_wmax_connected`）与触发灯控（`_wmax_trigger`）。参数面板（`api/wmax.py` 35+ endpoint）是独立的设备管理 UI，不参与检测/结算链。协议知识全部来自抓包+反编译逆向，3B DataLen 变体、GetConfigOpt 省略 field1、activate_rpt_reporting 时序三处是历史血泪集中地。

### 2.4 入站与拉取两条外部对接路径的分工

| 维度 | 入站 mes_inbound（推给我） | 拉取 mes_puller（我去拉） |
|---|---|---|
| 方向 | 外部系统 → 本机 HTTP POST/GET | 本机 → 外部系统 HTTP 轮询 |
| 载荷 | 开工任务/完工信号/报警消除命令 | 工单列表（数组提取+字段映射） |
| 配置落点 | SystemConfig 单键 JSON（全局一份） | MESConnection.config.pull（每连接一份） |
| 驱动 | 被动（对方调用即处理），可自定义根路径别名 | PullScheduler 后台线程按 pull_interval 轮询 + 手动/dry_run |
| 副作用 | 切项目/建工单/顶替在产单/清报警台账（敏感动作默认关） | 工单 upsert 三档 import_mode |
| 响应协商 | 业务码/格式(json/xml)/模板全可配（川南协议范式） | 自定义 success 判定 + 错误信息启发式提取 |

另有第三条轻量通道：`POST /mes/orders/receive`（api/mes.py L253，M2M API Key）——外部直接推单条工单 upsert，早于 inbound/puller 体系，语义最简。出站方向的"报警闭环"由 external_alarm.py 台账串联：gateway 推报警时登记在途 → inbound 收消除命令按唯一键清除 → 监控页横幅轮询 `/mes/inbound/active-alarms`。

## 三、疑点清单

读码中发现的可疑设计 / 疑似 bug / 文档与代码不一致（按文件序，行号为 2026-07-05 工作区实测；v3.41 补账时对涉改文件复核过仍成立，行号已刷新）：

1. **services/scanner.py L1273 / L1321-1338**：`upgraded_from_text_lon` 恒为 False（v2.7.8 起协议选择交还用户），其后整段"text_lon 升级 wmax"分支成死代码，未删。
2. **services/mes_inbound.py L135 vs L699-715**：`supersede_scope` 的 DEFAULT 配置注释只列 project/same_project/external 三档，但实现里还有 `same_channel` 分支——文档（配置注释）落后于代码（v3.41 复核仍未补）。
3. **services/mes_puller.py L34**：模块头注释写 `triggers` 支持 `"on_scan": false`，全文件无任何消费该键的代码（调度器只看 scheduled）——规划未实现的残留。
4. **services/cluster_collector.py L655 / L733**：`_check_and_dispatch` 里 `early_return = {}` 声明后全程无人写入，L733 的 `early_return.get("payload") is not None` 永假——疑似历史重构残留死代码。
5. **services/packaging_flow_coordinator.py L92**：`_TERMINAL_STATES` 含 "short"，但全文件没有任何代码把 run.status 置为 "short"——未用状态或规划残留（v3.41 复核仍在）。
6. **services/workpiece_flow_coordinator.py L275**：`abort_in_progress_on_startup` 定义 `now = time.time()` 后未使用（实际用 `datetime.now(timezone.utc)`）——无害残留。
7. **services/external_device.py L48**：`self._lock = threading.Lock()` 全族（含三个 mixin）grep 无任何使用——死变量；`_connections` / `_barcode_buffer` 两个 dict 实际被设备线程 + API 线程 + 扫码回调线程无锁并发读写（CPython dict 原子性兜底，但非显式设计）。
8. **services/external_device.py L409-413**：`get_external_device_service` 是无锁裸单例，与 wmax manager（双检锁 L30-37）、其它服务的单例风格不一致——并发首调可能创建两个实例（FastAPI 启动序基本单线程，实害低）。
9. **services/wmax/manager.py L44**：`_devices` dict 无锁，而 `api/wmax.py` 的 connect/disconnect/auto-discover 可并发调用——竞态窗口小但存在。
10. **api/mes_inbound.py L300-304**：`_log_inbound` 的 url 按 event_type 写死两个标准路径、method 恒写 "POST"——经 GET 或自定义别名路径进来的请求，日志里的 url/method 与真实请求不符，排障时可能误导。
11. **api/wmax.py L117/L141/L557-561/L578/L641-644**：API 层直接读写 `svc._connections` / `mgr._devices` 私有 dict，并自造负数 device_id 约定（-999=虚拟设备、-9000 起=自动发现注入）——越过服务封装，约定散落在 API 文件里。
12. **api/external_device.py L199-200**：`POST /barcode`（条码注入）权限位用 `mes.scanner.edit`，同文件其余写端点全用 `mes.external.edit`——若非有意（"注入条码语义归扫码器"），是权限口径不一致。
13. **api/packaging_flows.py L441-445**：`POST /scan` 先无条件 `coord.on_scan(...)` 再 `resolve_config_id`，无匹配配置时返回 `handled=False`，但码其实已经喂进过协调器（on_scan 内部自行判归属，通常无害）——返回语义与实际执行顺序有错位。
14. **api/channel_groups.py L107-111**：master_slave 策略拒绝文案写"v3.13.0 暂未实现, 计划 v3.14"，当前已 v3.41 仍未实现——过期承诺文案。
15. **services/mes_hooks.py（§1 已注）**：`_pending_workpiece` / `_inspecting_workpiece` / `_pending_queue` 等核心 dict 无锁，依赖"写都在单条 worker 线程"的约定，HTTP 侧 `clear_pending_scan(force)` 以原子 pop 对冲——约定式线程安全，扩展新写点时极易踩雷（此为设计风险提示，非现行 bug）。
