# 后端杂项读码笔记（兜底册）

> 读码时间：2026-07-05
> 用途：函数级源码索引兜底册——不属于 01-04 主域的全部后端文件（MES 周边服务 / 导出渲染器 / 杂项 API 路由 / core / db / hcnetsdk / schemas / scripts / 版本文件），共 44 个文件。
> 行号以当日代码为准，后续代码演进可能漂移。
> 2026-07-17 v3.41 复核：补账 v3.33~v3.41 变更（db 迁移域）——新增迁移 runner / m0001 / requirements.txt 三个条目，m0000 条目行号刷新。
>
> **短信补账（2026-08-03）**：新增短信服务族（见下方「短信通知」节）；路由 API 条目在 `01_backend_core.md` 的 `sms.py`。
>
> **v3.49 补账（2026-08-12，捷昌整改批次）**：
> - `core/debug_center.py`：新增 **`backend.timing`** 调试类目（WS4）——扫码处理/窗口结算/MES 外推的分段耗时埋点走此类目进调试日志中心，与既有调试开关联动、默认无常驻开销。回归 `tests/test_timing_probe_ws4.py`。
> - `db/sql_compat.py`（27→47 行）：新增三个方言助手——`hour_minute(col)`（PG `to_char(HH24:MI)` / SQLite `strftime('%H:%M')`）、`date_str(col)`（PG `to_char(YYYY-MM-DD)` / SQLite `date()`）、`sum_bool(expr)`（`sum(case when ...)` 双方言通用）。存量 SQLite 特有 SQL 的收编出口，消费方：export_context / reports 域。双方言回归 `tests/test_sql_compat_ws5.py`。
> - `services/external_alarm.py`：原生 `json_extract` 裸 SQL 改 SQLAlchemy JSON 索引表达式（双方言可用）。
> - `services/sms_offline_queue.py` / `services/interconnect/sample_queue.py`：文件头补**旁路队列决策注释**——两队列独立本地 SQLite，**不随 DATABASE_URL 迁 PG**（离线暂存语义本来就要求本地可用，主库断连时仍能落盘）。
> - `scripts/db/sqlite_to_pg.py`：迁移工具扩能——`--dry-run`（只探不写）/ `--verify`（逐表行数比对）/ JSON 列解码后直写（防 PG 存成转义字符串）/ 孤儿外键扫描报告（SQLite 不强制 FK 的历史遗留）/ 迁移期 `session_replication_role=replica` 关 FK + 序列重置。真迁移回归 `tests/test_sqlite_to_pg_migration.py`（需 PG 服务，SQLite 环境自动 skip）。

---

## 〇之二、训练平台互连（v3.47 新增家族）

> 与 YoloVision 训练平台双向互连（契约 interconnect-contract 1.1，共同事实源在训练平台仓库 `TIANJUN_INTERCONNECT_SPEC.md`）。默认关；配置存 SystemConfig KV `interconnect.config`（**不是**独立 json 文件）。诊断见 `debug-interconnect` skill。

### `backend/api/interconnect.py`（175 行）

| 维度 | 内容 |
|---|---|
| **职责** | `/api/v1/interconnect/*` 8 端点：health（免鉴权契约端点）/ models/push（接收 .yvmodel）/ config GET+PUT / status / test-connection / samples/recent / pull-now |
| **鉴权** | `_require_token`：除 health 外全部校验共享令牌（双方同一随机串 ≥32 字符） |
| **上下游** | 入库委托 `package_ingest`（push 与 puller 复用同一路径）；状态聚合 uploader/puller/sampler/identity |

### `backend/services/interconnect/`（7 文件）

| 文件 | 行数 | 一句话 |
|---|---|---|
| `config.py` | 171 | 配置存取（SystemConfig KV `interconnect.config`）+ 校验 |
| `identity.py` | 83 | device_id 稳定身份（KV `interconnect.device_id`，License machineId 同源，`tj-` 前缀持久化；随所有出站请求与 health 携带） |
| `package_ingest.py` | 257 | .yvmodel 包安全入库：逐产物 SHA-256 / 防路径穿越 / 防 zip 炸弹 / 违禁载荷拦截 → primary 产物入模型仓库 → `x-project-name` 精确匹配项目 → `x-analysis` 入 `Model.meta` |
| `puller.py` | 241 | 拉取分发线程：轮询对端 packages 游标增量列包 → 下载 → 复用 push 入库路径；**坏包跳过不卡游标，传输失败停轮保序重试**；`pull_now()` 手动触发 |
| `sampler.py` | 226 | 推理循环采样决策 `maybe_sample_frame`：置信度带 / 未检出周期内守门（空帧≠漏检）/ 检出闪断 detection_dropout / NG 事件帧；限流 `_pass_rate_limit`（最小间隔+每小时上限） |
| `sample_queue.py` | 215 | 磁盘队列（默认 max 500 / TTL 72h），进程重启可恢复 |
| `uploader.py` | 189 | 后台 worker 异步回传：指数退避、对端离线不丢帧、404 项目不存在丢弃不堵队列；`main.py::_start_interconnect_uploader()` 启动挂载 |

**不变量**：采样挂点在推理热路径（`source_inference_loop_mixin.py:423`），全程 try/except 隔离 + 关闭 O(1) 早退——**绝不允许把重活挪进推理线程**；拉取到的模型永不自动启用。

## 〇之三、PLC 连接器 + 统一触发中心（v3.48 新增家族，RFC 13/14）

> 两家族同一设计基调：**默认无实例零开销**（不建连接/触发源就零线程零轮询）、整表 JSON 配置化（加点位/规则/动作不动 schema）、共享全局动作注册表。诊断分别见 `debug-plc` / `debug-triggers` skill；ORM 两张新表见 `03_data_plugin.md`。

### `backend/services/plc/`（RFC 13，14 文件）

| 文件 | 行数 | 一句话 |
|---|---|---|
| `manager.py` | 208 | 连接管理单例：每启用连接一条轮询线程；`start_all`/`stop_all` 挂 `main.py` 启停；断线退避重连 |
| `point_engine.py` | 536 | 点位引擎：按点位表批量读 → `point_codec` 解码 → 值变化沿评估触发规则（防抖/min_interval）→ 执行动作（bind_sn 绑码建工件 / trigger_event / manual_settle 等） |
| `point_codec.py` | 198 | 点位编解码：int16/32/float/string × 字节序（ABCD/BADC/CDAB/DCBA）× scale/offset |
| `write_dispatcher.py` | 26 | `dispatch_plc_event(event, payload, channel_id)`——mes_hooks/结算侧唯一写回入口，enqueue 级非阻塞、内部全兜底永不抛 |
| `rule_actions.py` | 18 | 规则动作到全局动作注册表的桥接 |
| `presets.py` | 197 | 方案模板（s7_db_handshake V0.2：产品号+完成信号握手/结果码回写/心跳） |
| `drivers/`（8 文件） | ~700 | `base.py` 抽象 + modbus/s7/mc/fins/enip/opcua/mock 七驱动；依赖库全部懒 import（缺库只在启用该驱动时报错） |

### `backend/services/triggers/`（RFC 14，13 文件）

| 文件 | 行数 | 一句话 |
|---|---|---|
| `manager.py` | 196 | 触发源管理单例：每启用源一实例；`on_channel_removed` 挂 channel_manager 裁撤清理（第 5 处配套清理） |
| `engine.py` | 229 | 规则评估：防抖 debounce / min_interval / 生效时间窗拦截 → 动作链执行 |
| `actions.py` | 369 | **全局动作注册表**（PLC 规则与触发中心共用）：manual_settle / trigger_event / ack_alarm / start_detection 等；v3.50 加 `resume_scanner`（= `ScannerService.resume_scanning_manual`，`resume_on='ok_only'` 下 NG 灭灯的脚踏板/PLC 人工出口） |
| `presets.py` | 126 | 触发源模板（虚拟按钮遮挡结算等） |
| `sources/`（8 文件） | ~900 | `base.py` 抽象 + pixel_region（画面像素区域亮度/遮挡，参考帧缓漂 ref_drift）/ hid_key（脚踏板/USB 键）/ http_source（带 IP 白名单+变量提取）/ serial_pattern（串口报文正则）/ timer_source（间隔+每日定点）/ mock |

### API / schemas

- `api/plc.py`（242 行）：`/plc/*` 连接 CRUD + 点位实时值 + IO 日志 + 模板 + 试读试写；`api/triggers.py`（270 行）：`/triggers/*` 触发源 CRUD + 状态 + 触发历史 + 手动触发 + 模板；`schemas/plc.py` / `schemas/triggers.py` Pydantic 面。
- `requirements.txt` 新增 PLC 驱动依赖（python-snap7/pymcprotocol/pycomm3/asyncua 等，均懒加载）+ pynput（hid_key）。

**v3.48 短信增量（dev-qing）**：`sms_service.py`（+428/-98）汇总发送形态——`summary_form`：`merged_detail`（默认，一条短信内分列多工位明细）/ `per_channel`（逐工位逐条）；`summary_channel_ids` 限定参与汇总的工位（空=全部）；`sms_config.py`/`schemas/sms.py` 配置面同步，`sms_summary.py` 计数口径透传。UI 在 `views/Alarm/index.vue`。

## 〇、短信通知（新增）

### `backend/services/sms_service.py`（~935 行）

| 维度 | 内容 |
|---|---|
| **职责** | 短信门面：按 `provider` 选 AT / HTTP、入队发送、12h 汇总调度、离线队列、关机 |
| **核心类** | `SmsServiceConfig`、`SmsService`、`AlarmQueueReceipt` |
| **核心方法** | `queue_summary_sms`、`start_summary_scheduler`、`shutdown`、测试发送路径 |
| **线程** | daemon `sms-12h-summary`；发送工作线程；**不**持有检测/MES 锁 |
| **上下游** | 上游 `api/sms.py` / `main.py` 启停；下游 providers + `sms_summary` + `sms_offline_queue` |
| **注释坑** | 默认 `enabled=false`；汇总只读已结算周期；坏配置由 ConfigStore 回退 |

### 同族其它文件

| 文件 | 行数约 | 一句话 |
|---|---|---|
| `sms_config.py` | 287 | `sms_config.json` 读写与校验 |
| `sms_summary.py` | 169 | 窗口水位 + 周期 OK/NG 汇总读取 |
| `sms_offline_queue.py` | 202 | SQLite 离线重试 |
| `sms_at_client.py` / `sms_modem.py` | 285 / 241 | AT 会话与调制解调器 |
| `sms_utils.py` | 69 | 收件人解析等 |
| `sms_providers/base_provider.py` | 53 | Provider 抽象 |
| `sms_providers/at_modem_provider.py` | 123 | USB/AT 通道 |
| `sms_providers/generic_http_provider.py` | 256 | 厂商无关 HTTP JSON |
| `schemas/sms.py` | 237 | API Schema（含一期平铺兼容字段） |

独立调试工具：`tools/sms_4g/`（不进主程序运行时）。诊断见 `debug-sms` skill。

**v3.47 补账（2026-08-07，dev-qing 补账两提交）**：
- `sms_config.py`：保存时嵌套 `at_modem` 段优先于一期平铺脏字段（治前端保存后配置乱码/被平铺旧值覆盖）
- `sms_summary.py` + `schemas/sms.py`：NG 汇总数字口径可选 `summary_numeric_scope`（`panel`=面板会话累计（默认，与监控页对齐）/ `window`=12h 时间窗）；前端 Alarm 页文案随口径动态变化，e2e 断言只锁公共前缀「每滚动 12 小时按工位」

---

## 一、MES 周边服务（17 个文件）

### `backend/services/work_order.py`（357 行）

| 维度 | 内容 |
|---|---|
| **职责** | 工单全生命周期服务：CRUD、三种绑定域（project/channels/cluster）校验、状态流转、原子计数、批次管理、统计摘要 |
| **核心类/函数** | L14 `WorkOrderService`；L18-69 `_normalize_binding()` 三选一绑定校验（strict=False 允许外部 MES 推的工单不绑项目）；L71-95 `create_order()`（source=external 时走宽松校验）；L127-150 `change_status()` 按 `WorkOrder.VALID_TRANSITIONS` 校验状态迁移，in_progress 补 actual_start；L152-160 `delete_order()` 仅 draft/cancelled 可删；L170-207 `get_active_order()` 按 priority+created_at 取首条 in_progress，只筛 project/channels 两种域；L209-247 `find_cluster_orders()` 给 cluster_collector 用，当前最多返回 1 条；L275-289 `increment_completed()` SQL 原子递增 + 刷新良率；L305-310 `check_completion()` |
| **线程/锁** | 无（db session 由调用者管理事务，全部 `db.flush()` 不 commit） |
| **上下游** | 上游：`api/mes.py` 路由、MESHook 计件、cluster_collector；下游：`models/mes_models.py` 的 WorkOrder/Batch |
| **状态变量** | 无（无实例状态，纯 DB 操作） |
| **注释坑** | L57-61 cluster 模式"工单建在主机=全集群 box 都计件"，target_stations 字段保留作高级扩展位、UI 不暴露；L110-111 update 时只要请求里出现任一 binding 字段就整组重校验，否则老 binding 原样保留；L176-178 cluster 工单不在 get_active_order 返回（按 box 计件由 cluster_collector 触发）；L220 find_cluster_orders 返回 list 是给将来"多工单同时跑"留口子 |

### `backend/services/workpiece.py`（235 行）

| 维度 | 内容 |
|---|---|
| **职责** | 工件追溯与状态服务：登记（同项目同序列号幂等）、状态流转（inspecting/ok/ng/rework/scrapped）、工件-周期关联、完整追溯链查询 |
| **核心类/函数** | L14 `WorkpieceService`；L18-41 `register()` 已存在且状态 ok/ng 时重置为 queued（复检语义）；L64-74 `mark_inspecting()` 递增 inspection_count + 记首检/末检时间；L76-85 `set_result()`；L89-97 `mark_rework()` 仅 NG 可返工；L99-108 `mark_scrapped()` 仅 ng/rework 可报废；L120-135 `link_to_cycle()` 建 WorkpieceInspection 关联记录；L154-179 `get_full_trace()` 工件+全部检测+全部缺陷；L208-226 `delete_workpiece()` 级联删 Inspection/DefectRecord |
| **线程/锁** | 无 |
| **上下游** | 上游：mes_hooks（扫码绑定/周期结算时调）、`api/mes.py`；下游：Workpiece/WorkpieceInspection/DefectRecord 表 |
| **注释坑** | L211-212（v2.7.17）删除工件**不动** DetectionCycle/DetectionSession——检测原始数据与工件解耦，不应跟着删 |

### `backend/services/defect.py`（243 行）

| 维度 | 内容 |
|---|---|
| **职责** | 缺陷记录服务：Cycle NG 时从 result_reason 提取标签自动匹配缺陷码建档、手动录入、Pareto 统计、缺陷码字典 CRUD |
| **核心类/函数** | L14 `DefectService`；L18-59 `auto_record()` 三段策略（提标签→按 detection_labels 匹配缺陷码→未匹配用 `AUTO-<label>` 兜底）；L61-75 `_extract_labels()` 按中英文逗号切 result_reason 并剥"缺失/多余"前缀，空则取 step_sequence 末 3 项；L167-191 `get_pareto_data()` 缺陷码分组计数降序；L195-234 缺陷码字典 CRUD（支持 project 级+全局共存） |
| **线程/锁** | 无 |
| **上下游** | 上游：MESHook 在 Cycle NG 时调 auto_record、`api/mes.py` 缺陷路由；下游：DefectRecord/DefectCode 表 |
| **注释坑** | 无显式警告注释；但 L38-59 `matched_any` 标志位跨标签循环不复位（见疑点清单 #1） |

### `backend/services/barcode_parser.py`（99 行）

| 维度 | 内容 |
|---|---|
| **职责** | 可配置条码解析器：direct（原文即序列号）/ separator（分隔符拆字段）/ regex（命名组提取）三模式，产出 `ParseResult`（serial_no/order_no/batch_no/extra） |
| **核心类/函数** | L14 `ParseResult` dataclass；L24 `BarcodeParser`；L26-48 `parse()` 按 config.parse_mode 分发，无 config 走 direct；L53-71 `_parse_separator()` 按 field_mapping（索引→字段名）取值；L73-99 `_parse_regex()` 命名组支持 serial_no/serial、order_no/order、batch_no/batch 双别名，其余进 extra |
| **线程/锁** | 无（无状态） |
| **上下游** | 上游：mes_hooks 扫码处理链（扫码器 config 里带解析配置）；下游：无 |
| **注释坑** | 无 |

### `backend/services/mes_health_probe.py`（193 行）

| 维度 | 内容 |
|---|---|
| **职责** | 出站 MES 连接主动健康探测：APScheduler 后台调度器 15s 一个 tick，按每连接的 interval 到点探测，结果只存进程内缓存（不落库，重启清零） |
| **核心函数** | L40-60 `_probe_params()` 解析每连接探测配置（默认 60s 间隔/5s 超时/GET）；L63-93 `probe_connection()` 发一次探测并写缓存；L96-107 `_save_status()` 只在在线↔离线翻转时打日志防刷屏；L110-114 `get_health_status()`；L117-126 `probe_now()` API 立即探测；L129-162 `probe_due_connections()` tick 主体（先取字段再关库，探测不持库；顺带清理已停用连接的旧状态）；L165-180 `start_health_probe()` 幂等启动；L183-193 `stop_health_probe()` |
| **线程/锁/定时器** | L31 `_LOCK`（调度器启停）；L37 `_STATUS_LOCK`（状态缓存）；L30 `_SCHEDULER` BackgroundScheduler 守护线程，L33 `_TICK_SEC=15` |
| **上下游** | 上游：main.py 启动时调 start、`api/mes_gateway.py` 读状态/立即探测；下游：MESConnection 表（只读）、debug_center 日志 |
| **状态变量** | `_SCHEDULER`、`_STATUS`（{conn_id: {ok, checked_at, latency_ms, ...}}） |
| **注释坑** | L8 默认惰性——连接 config.health_probe_enabled 未开则完全不探测零开销；L100 只在翻转时打日志；L138 先取出需要的字段再关库，探测请求本身不持库 |

### `backend/services/mes_adapters/base.py`（79 行）

| 维度 | 内容 |
|---|---|
| **职责** | 适配器基类 + 自研 `{x.y}` 占位符模板渲染引擎（**非 Jinja2**），支持 `_array_source`/`_item_template` 数组展开 |
| **核心函数** | L13-22 `_get_nested()` 点号路径取值；L25-54 `render_template()` 递归渲染：整串占位符保留原类型、混合串转 str、dict 带 `_array_source` 键则按源列表展开 item 模板；L57 `BaseAdapter`：L60-63 `build_payload()`（config.template + context）、L65-67 `send()` 抽象、L69-79 `check_response()`（无 success_check 时 200/201 即成功，有则按 body 字段等值判断） |
| **线程/锁** | 无 |
| **上下游** | 上游：mes_gateway 按连接协议选适配器；下游：五个具体适配器继承 |
| **注释坑** | 无（注意 AGENTS.md 已强调 MES Gateway 不用 Jinja2，本文件即自研模板实现处） |

### `backend/services/mes_adapters/rest_adapter.py`（83 行）

| 维度 | 内容 |
|---|---|
| **职责** | REST/JSON 适配器：GET 参数化 / POST/PUT/PATCH JSON 体，支持连接/读取分离超时与 basic auth |
| **核心函数** | L15-74 `send()`：L21-27 配了 connect_timeout/read_timeout 任一即用 `(connect, read)` 元组超时否则单值（默认 30）；分类捕获 Timeout/ConnectionError/其他，统一返回 `{status_code, body, success, error, duration_ms}`；L76-83 `_build_auth()` 仅 basic 生效 |
| **线程/锁** | 无 |
| **上下游** | 同 base；被 mes_gateway 用于 REST 协议连接 |
| **注释坑** | L19-20 超时分离对齐"川南协议 §5.1 连接 5s/读取 10s，含图建议放大"；L82 bearer 不在此处理（`# bearer token handled in headers`，由 gateway 层合并进 headers）。注意 `success: True` 仅表示 HTTP 请求完成，业务成败由 `check_response` 判 |

### `backend/services/mes_adapters/form_data_adapter.py`（66 行）

| 维度 | 内容 |
|---|---|
| **职责** | Form-Data 适配器：整个 payload 序列化成一个 JSON 字符串塞进单个 form 字段（默认 key=`param`）POST 出去 |
| **核心函数** | L16-66 `send()`：L18 `form_key` 可配；仅支持 POST；仅 basic auth |
| **线程/锁** | 无 |
| **注释坑** | L4-5 适用于客户 MES 要求 `param={"macno":...}` 这类打包传参场景 |

### `backend/services/mes_adapters/form_urlencoded_adapter.py`（96 行）

| 维度 | 内容 |
|---|---|
| **职责** | x-www-form-urlencoded 适配器：payload 每个字段平铺成独立 form 字段（列表按 array_join 拼接、嵌套 dict 可转 JSON 串） |
| **核心函数** | L26-39 `_flatten_value()` 递归拍平（bool→"true"/"false"，list 按分隔符或 JSON，dict 按 dict_as_json）；L44-96 `send()` GET 走 params、其余走 data |
| **线程/锁** | 无 |
| **注释坑** | L7-8 与 form_data_adapter 的区别：后者打包成一个 JSON 塞固定字段，本适配器每字段独立 key，"客户说字段分开传、不要打包"用这个 |

### `backend/services/mes_adapters/query_string_adapter.py`（91 行）

| 维度 | 内容 |
|---|---|
| **职责** | URL Query String 适配器：不管什么 method 都把 payload 全展开成 URL query 参数、请求体为空 |
| **核心函数** | L25-38 `_flatten_value()`（与 form_urlencoded 版逐行相同，复制品）；L43-91 `send()` 统一 `params=` 发送 |
| **线程/锁** | 无 |
| **注释坑** | L7 适用于"参数全放 URL 里"的客户场景；`_flatten_value` 与 form_urlencoded_adapter 重复实现（见疑点清单 #3） |

### `backend/services/mes_adapters/modbus_adapter.py`（268 行）

| 维度 | 内容 |
|---|---|
| **职责** | Modbus RTU/TCP 适配器：把检测结果（result_code/计数/耗时等）写入保持寄存器，覆盖 build_payload 不走 JSON 模板 |
| **核心函数** | L46-56 `_slave_kwarg()` 运行时探测 pymodbus 3.13+ 的 `device_id` vs 旧版 `slave` 参数名；L59-70 `_modbus_addr()` 40001/30001/00001 段转 0-based；L73-116 `_resolve_source()` 内置 8 种数据源（result_code/const/ok_count/ng_count/total_count/cycle_id/duration_ms/inspection_count）+ dotted path 兜底；L119-126 `_split_int32()` 32 位拆双寄存器（大小端可配）；L131-161 `build_payload()` 构建寄存器写入列表；L186-265 `send()` 建连→逐寄存器写入→汇总；L267-268 `check_response()` 直接看 success |
| **线程/锁** | L43 模块级 `_serial_lock`（RTU 串口互斥）；L203 TCP 分支每次 new 一个临时锁——**无实际互斥**（见疑点清单 #2） |
| **上下游** | 上游：mes_gateway modbus 协议连接；下游：pymodbus（惰性 import，缺依赖返回友好错误） |
| **注释坑** | L47 pymodbus 3.13+ 改用 device_id 旧版用 slave；L232 结果回显 `addr + 40001` 硬编码按保持寄存器段还原地址，coil/input 段会显示错段（见疑点清单 #2） |

### `backend/services/flow_triggers/physical_trigger.py`（96 行）

| 维度 | 内容 |
|---|---|
| **职责** | RFC 11 M7 物理 GPIO/Modbus 信号触发器（策略对象，不持硬件循环）：判定外设信号是否应触发流水线新工件入口，通过则生成自动序号 |
| **核心类/函数** | L37 `PhysicalTrigger(FlowTriggerBase)`；L53-92 `evaluate_physical_signal(device_id, signal_key)` 四步：设备匹配→信号 key 匹配→dedup_window_ms 去抖（默认 500ms）→返回 `{prefix}-{毫秒时间戳}` 序号；L94-96 `_on_detach()` 清去抖表 |
| **线程/锁** | L43 `_lock`（保护 `_last_signal_at` 去抖表） |
| **上下游** | 上游：WorkpieceFlowCoordinator 的 on_physical_signal 统一分发；配置来自 `WorkpieceFlowConfig.physical_trigger_config` JSON |
| **状态变量** | `_last_signal_at: {signal_key: 毫秒时间戳}` |
| **注释坑** | L23-26 v1 简化：不真做硬件去抖（仅实例内同 key 500ms 忽略）、不做边沿过滤（上层 external_device 已做） |

### `backend/services/flow_triggers/scan_trigger.py`（87 行）

| 维度 | 内容 |
|---|---|
| **职责** | RFC 11 M3 扫码绑定触发器：入口扫一次码即工件序列号，判定"本 channel 是否入口工位 + 扫码器是否匹配"，支持 entry / each_station 两种绑定策略 |
| **核心类/函数** | L34 `ScanTrigger(FlowTriggerBase)`；L37-82 `evaluate_scan()`：先做 scan_device_id 匹配（配了才过滤），entry 模式仅 stations[0] 触发、each_station 模式任何本 flow 工位都触发（幂等去重在 Coordinator 层）；L84-86 `evaluate_cycle_start()` 恒返回 None（必须扫码触发） |
| **线程/锁** | 无（无状态） |
| **上下游** | 上游：Coordinator ← mes_hooks.on_scan_received 钩子点 |
| **注释坑** | L14-17 scan_pair 互斥：channel 属于某 flow 时 mes_hooks 的 scan_pair 路径会跳过，守门点在 `mes_hooks._handle_scan` 开头查 `is_channel_in_flow`；L23-25 each_station 同序列号 1 秒内幂等去重由 Coordinator.on_workpiece_enter 层处理 |

### `backend/services/flow_triggers/time_window_trigger.py`（41 行）

| 维度 | 内容 |
|---|---|
| **职责** | RFC 11 M2 无扫码器 FIFO 触发器：入口工位每次 cycle_start 即视为新工件，自动生成 `auto-<flow_id>-<8hex>` 序号 |
| **核心类/函数** | L24 `TimeWindowTrigger(FlowTriggerBase)`；L27-41 `evaluate_cycle_start()` 仅入口工位（stations[0]）返回序号，其余 None |
| **线程/锁** | 无（自身完全无状态，序号生成无副作用） |
| **注释坑** | L9-10 节拍抖动/跳工位时容易错绑——文档明示，强烈推荐升级硬件加扫码器；L14 fifo_max_in_flight 上限判断在 Coordinator.on_workpiece_enter 里做 |

### `backend/services/external_alarm.py`（252 行，v3.41 复核）

| 维度 | 内容 |
|---|---|
| **职责** | 在途报警台账（外部生产管控系统报警闭环通用能力）：出站推送时登记 active 报警、外部回推消除命令按唯一键匹配清除、软件内一键消除（v3.39）、监控页轮询未消除报警做持续横幅 |
| **核心函数** | L23 `DEFAULT_MATCH_FIELDS` 四要素（task_no/product_code/step_code/operator）；L36-49 `_split_fields()` 归一化为 5 原生列 + extra_data.ext 扩展维度；L52-56 `_match_col()` 原生列走列等值、扩展维度走 json_extract（键名过 `_SAFE_DIM_KEY` 正则防注入）；L74-114 `record_active_alarm()` 支持 dedup_sec 去重窗口；L117-149 `clear_alarms()` 只对有值字段加条件，0 个匹配字段直接 matched=False 防空条件清全表；L152-172 `clear_all_active_alarms()`（**v3.39**：上游不回推消除命令时的软件内运维出口，不做字段匹配、可按 channel 限定、clear_source 留审计）；L175 `list_active_alarms()`（limit 钳制 1-500）；L202 `find_recent_active_alarm()` 供出站网关推送前"同任务同标签 N 秒去重"（川南 v4 第 4 条） |
| **线程/锁** | 无（db 由调用方管理） |
| **上下游** | 上游：mes_gateway 出站推送、mes_inbound 消除命令 + v3.39 手动消除端点/监控页清零联动、监控页轮询 API；下游：ExternalActiveAlarm 表 |
| **注释坑** | L9-10 唯一键参照客户约定=四要素，匹配字段与去重窗口都由调用方（入站配置）传入，**本模块不含任何客户分支**；L81-82 去重口径与消除口径共用同一套 match_fields 配置，避免客户改了匹配维度后两边口径不一致；L105-106 raised_at 显式落本地时间——不用 SQLite func.now()（UTC）以免与 datetime.now() 去重窗口错开；L155-156（v3.39）"与 clear_alarms 的按键匹配语义刻意分开: 这是'人在界面上一键清空'的运维出口" |

### `backend/services/project_config_normalize.py`（45 行）

| 维度 | 内容 |
|---|---|
| **职责** | 项目 pipeline_config 归一化：API/脚本创建的项目常缺 sequence_order，顺序模式下从 steps_config 自动补全默认步骤顺序 |
| **核心函数** | L8-23 `build_default_sequence_order()`（跳过 disabled 与备用步骤，与前端 `_buildDefaultSequenceOrder` 对齐）；L26-45 `ensure_sequence_orders()`：sequential 补 `sequence_order`，custom 且 custom_based_on=sequential 补 `custom_sequence_order` |
| **线程/锁** | 无 |
| **上下游** | 上游：projects API 创建/更新项目时调；下游：无 |
| **注释坑** | L9 强调与前端逻辑对齐——改一边必须同步另一边 |

### `backend/services/project_match.py`（89 行）

| 维度 | 内容 |
|---|---|
| **职责** | v3.30 规格/产品码→检测项目统一匹配器：MES 入站（产品码）与上银包装线（规格）共用同一套取项目口径。三级优先：①对照表精确 ②对照表通配符（fnmatch，取最长键=最具体）③自动同名子串（默认关，取最长项目名、同长取 id 大） |
| **核心函数** | L22-34 `_boundary_ok()` 严格边界档：出现处左右须是串首/串尾或非字母数字，防 HG 误吞 HGH20；L37-89 `resolve_project_id_by_spec()` 返回 `(project_id, 命中方式文案)`，全未命中 `(None, None)` |
| **线程/锁** | 无 |
| **上下游** | 上游：mes_inbound 开工切项目、packaging_flows 规格匹配；下游：Project 表（仅③档查询） |
| **注释坑** | L7-8 设计取向：与位置（前/后/中）和分隔符（- _ 空格/无）完全解耦；L26-27 严格边界档代价：无分隔符场景（HGH20001 找 HGH20）右侧贴数字边界不成立命中不了，**故该档默认关**；L47 match_by_name 默认 False——存量客户零差异 |

---

## 二、导出渲染器（4 个文件）

### `backend/services/export_renderer.py`（680 行）

| 维度 | 内容 |
|---|---|
| **职责** | v3.5.0 自定义导出 Jinja2 渲染核心：SandboxedEnvironment 防 RCE、自定义过滤器族、外部文件桥接 globals、文件名渲染与非法字符清洗、三种 input_file_mode、overwrite_policy、编码/换行处理；docx/xlsx/pdf 转发专用渲染器 |
| **核心类/函数** | L40-51 `_SilentUndefined(ChainableUndefined)` 未定义变量安静渲染为空串；L54-116 过滤器族（round/format_dt/dt/pad/pass_fail/yn/yesno/csv_esc）；L136-168 `_glob_latest_file()` 取目录 mtime 最新文件（max_age_sec 防误取旧 txt）；L171-191 `_wait_for_stable()` size+mtime 双查判文件写完；L209-257 `latest_input_filename()`/`latest_input_text()` v3.7.2 扫码器旁路 helper；L260-274 `_resolve_locked_from_ctx()` C 策略——cycle_start 锁定快照优先于 mtime 路径；L291-341 `@pass_context` 装饰版 Jinja 入口（可省 directory 自动用 ctx.export.input_dir）；L352-377 `_build_env()` + 模块级单例 `_ENV`；L384-393 `render_string()`；L396-414 `render_filename()`（空白转下划线+去非法字符+补后缀）；L421-458 `_resolve_input_template()`（none/read_template/append）；L472-494 `_apply_overwrite_policy()`（overwrite/rename/skip）；L510-529 `RenderResult`；L532-634 `render_to_file()` 实时规则落盘入口；L637-680 `render_to_bytes()` HTTP 下载入口（docx/xlsx/pdf 惰性转发，utf-8-sig 手动加 BOM） |
| **线程/锁** | 无锁；`_ENV` 模块级单例（Jinja2 Environment 渲染线程安全） |
| **上下游** | 上游：export_custom（下载）、export_realtime（规则触发）、export_scheduled；下游：export_renderer_docx/xlsx/pdf、BASE_DIR、debug_center |
| **注释坑** | L16 沙盒防客户模板写 `{{ ''.__class__.__mro__ }}` RCE；L38-39 ChainableUndefined 让 `{{ a.b.c }}` 在 a 为 None 时不抛；L122-134 v3.7.2 扫码器旁路场景说明——任何异常安静返回 ''，不让 helper 把 cycle_end 链路带崩；L141-142 max_age_sec 防"很久没换码误取上一轮旧 txt"；L360 自定义 round 覆盖 Jinja 默认（允许 1 参数变 0）；L577-585 二进制格式不支持 input_file_mode；L20 `import io` 未使用（见疑点清单 #4） |

### `backend/services/export_renderer_docx.py`（163 行）

| 维度 | 内容 |
|---|---|
| **职责** | docx 双路线渲染：路线 A 纯文本→python-docx 自动样式（识别 markdown 标题 1-4 级 + 连续 CSV 行≥2 转表格）；路线 B 用户上传占位符 .docx 用 docxtpl 填值保留原样式 |
| **核心函数** | L32-89 `render_docx_route_a()`；L92-114 `_write_csv_table()` 首行做表头加粗；L121-144 `render_docx_route_b()`（相对路径基于 DATA_DIR）；L151-163 `render_docx_to_bytes()` 统一入口——template_file_path 非空走 B，否则渲染 content 走 A |
| **线程/锁** | 无；docx/docxtpl 均函数内惰性 import |
| **注释坑** | L156-157 路线 B 优先、路线 A 兜底 |

### `backend/services/export_renderer_xlsx.py`（159 行）

| 维度 | 内容 |
|---|---|
| **职责** | xlsx 双路线渲染：路线 A CSV 文本→openpyxl 自动表格（表头加粗蓝底、自适应列宽、数字自动转型、冻结首行）；路线 B 遍历上传模板每个 cell 渲染 `{{ }}` 占位符（保留样式/合并单元格，跳过公式） |
| **核心函数** | L26-78 `render_xlsx_route_a()`（sheet 名截 31 字符）；L81-85 `_split_csv_line()` 简易逗号拆分**不处理引号转义**；L91-100 `_coerce_value()` 正则识别数字转 int/float；L107-146 `render_xlsx_route_b()`（cell 渲染失败保留原占位符便于客户排查）；L153-159 统一入口 |
| **线程/锁** | 无 |
| **注释坑** | L82-84 上层模板若需"含逗号的字段"应用 `;` 之类替代分隔符或自己加双引号（与 export_renderer 的 csv_esc 过滤器语义冲突，见疑点清单 #5）；L134-137 只渲染含 `{{` 的 str cell 且跳过 `=` 开头公式 |

### `backend/services/export_renderer_pdf.py`（176 行）

| 维度 | 内容 |
|---|---|
| **职责** | pdf 渲染器（仅路线 A）：文本/markdown→reportlab PDF；中文用 CID 字体 STSong-Light，注册失败退 Helvetica |
| **核心函数** | L28-42 `_ensure_cn_font()` 一次性幂等注册；L49-153 `render_pdf_route_a()`（markdown 标题 1-3 级、CSV 表格识别同 docx 版、斑马纹表格样式）；L156-160 `_escape()` reportlab Paragraph 需转义 & < >；L167-176 `render_pdf_to_bytes()`——传了 template_file_path 只打日志并退回路线 A |
| **线程/锁** | 无锁；L24-25 模块级 `_FONT_REGISTERED`/`_CN_FONT` 状态（无锁保护，最坏重复注册幂等无害） |
| **注释坑** | L4-5 路线 B（用户上传 PDF 模板）实现复杂、字段定位难，本版不支持；L8-9 STSong-Light 不可用时中文显示为 ? |

---

## 三、杂项 API 路由（9 个文件）

### `backend/api/cameras.py`（160 行）

| 维度 | 内容 |
|---|---|
| **职责** | **旧式相机表** CRUD + 独立 MJPEG 流（`/api/v1/cameras/*`）；与 `/source/*` 主路径并存的历史遗留，每次拉流现开现关 cv2.VideoCapture，与 VSM 体系完全无关 |
| **核心函数** | L17-27 `get_camera_capture()`（int 索引→USB，否则 URL/路径）；L29-56 `generate_frames()` MJPEG 生成器；L58-160 CRUD + `/{id}/stream` + `/{id}/test`（测通后回写 status）+ `/default/stream` |
| **线程/锁** | 无；L15 `active_cameras = {}` 声明后**全文件无人读写**（死变量，见疑点清单 #6） |
| **上下游** | 上游：前端旧相机页（AGENTS.md 明示勿混淆为主路径）；下游：Camera 表、cv2 |
| **注释坑** | 无警告注释；写操作挂 `require_perm("source.edit")`，读与流不挂鉴权 |

### `backend/api/operators.py`（74 行）

| 维度 | 内容 |
|---|---|
| **职责** | v3.10.0 已废弃的操作员 API：仅保留 6 个 410 Gone 端点，响应头 `X-Deprecated-Replacement: /api/v1/users` 给老客户脚本迁移提示 |
| **核心函数** | L35-40 `_gone()` 统一抛 410；L47-74 六端点（list/create/update/delete/set-current/current）全部转 `_gone()` |
| **线程/锁** | 无 |
| **注释坑** | L7-8 ORM 类 Operator / current_operator.json 落盘 / get_current_operator_id() 已在阶段 5 删除，operators 表升级时 DROP；L10-11 老客户机上的 `backend/current_operator.json` 是僵尸文件——**不主动删除以避免误碰客户数据**，任何路径都不再触碰它 |

### `backend/api/reports.py`（345 行）

| 维度 | 内容 |
|---|---|
| **职责** | `/reports/*` 统计报表：摘要/历史记录/N 天趋势/每日统计/导出（CSV+PDF），数据源是**旧 Task 表**（非 DetectionCycle） |
| **核心函数** | L14-40 `_apply_datetime_and_hour_filters()` 支持跨夜班次窗口（start_hour>end_hour 时把 end_date+1 天）；L43-54 `_apply_hour_filter()` 用 sql_compat.hour_minute 做时段过滤（跨夜用 or_）；L57-90 `/summary`；L92-131 `/records`（每条记录单独查一次 Project，N+1 查询）；L133-172 `/trend` 近 N 天逐日 count（每天 2 次 count 查询）；L174-216 `/daily-stats`（case 把 Boolean 折成 0/1 再 sum，兼容 sqlite/mysql/pg）；L218-345 `/export` PDF（reportlab 惰性 import，缺依赖 501）/ CSV |
| **线程/锁** | 无 |
| **上下游** | 上游：前端报表页；下游：Task/Project 表、sql_compat、reportlab |
| **注释坑** | L21 跨夜班次窗口语义（如夜班 20:00-08:00）；L32 解析失败静默回落 legacy 过滤；L184 case 折 0/1 是为跨数据库兼容；PDF 导出用默认 Helvetica**未注册中文字体**（对比 export_renderer_pdf.py 有 STSong，见疑点清单 #7） |

### `backend/api/rod_filter.py`（212 行）

| 维度 | 内容 |
|---|---|
| **职责** | 传动杆（class=5）误判过滤后处理纯函数库（**无路由，虽在 api/ 目录下**）：两层独立可叠加过滤，默认全关需 project_config 显式开启 |
| **核心函数** | L66-115 `filter_rod_by_companion()` 第 1 层空间共现——rod 必须与任一 companion（大/小框架/侧板）center-in-bbox 或 IoU≥0.25 才保留；L118-162 `RodSessionGate` 第 2 层软时序门控——本周期没见过框架就抑制所有 rod，见过后放行直到 reset；L165-212 `read_rod_filter_config()` 兼容 pipeline_config 内（v2.7.8 推荐）与顶层（v2.7.6 遗留）两种配置写法 |
| **线程/锁** | 无锁；RodSessionGate 有实例状态 `_companion_seen`（每通道一个实例，新周期 reset） |
| **上下游** | 上游：source.py 推理后处理调用（L1009-1029 见 01 号索引）；下游：无 |
| **状态变量** | `RodSessionGate._companion_seen` |
| **注释坑** | L4-6 背景：best(7).pt 把"空箱+泡沫槽黑缝"稳定误识别成传动杆（conf 0.88-0.92），与真杆 conf 分布（median 0.69）相反，**单纯提高 conf 阈值无解**；L17 调用顺序推荐先 companion 再 gate；L31 dets 字段约定 x,y,w,h 归一化到 [0,1] |

### `backend/api/showcase_stats.py`（135 行）

| 维度 | 内容 |
|---|---|
| **职责** | RFC12 展会监控页高科技面板只读数据源：从已落库 detection_cycles 派生行为评分与合格率趋势，不侵入 150ms 检测热路径 |
| **核心端点** | L48-100 `GET /data/stats/behavior-score`（overall = 0.5×良率 + 0.3×节拍稳定度(100-变异系数×100) + 0.2×连续无NG比例）；L103-135 `GET /data/stats/yield-trend`（最近 window 个周期等分 points 桶各算良率） |
| **线程/锁** | 无 |
| **上下游** | 上游：前端监控页面板；下游：DetectionCycle/DetectionSession 表只读 |
| **注释坑** | L8-9 数据口径（展会语境）：评分是真实周期数据派生的综合指标，**非物理传感**；01 号索引已记：与 sessions 共用 `/data` 前缀，router_manifest 必须先注册 showcase_stats |

### `backend/api/tasks.py`（192 行）

| 维度 | 内容 |
|---|---|
| **职责** | 旧 Task 表 CRUD（`/tasks/*`）：离线检测任务记录、带图片上传的 `/record` 端点、批量清除 |
| **核心端点** | L15-40 列表（分页+项目/结果/日期过滤）；L50-69 创建（校验项目存在）；L71-103 更新结果字段；L116-174 `POST /record` multipart 上传图片（uuid 前缀防重名，存 IMAGE_UPLOAD_DIR）+ result_data JSON 解析失败按字符串保留；L176-192 `DELETE /batch/clear` 按项目/日期批量删 |
| **线程/锁** | 无 |
| **上下游** | 上游：离线推理链路/旧前端；下游：Task/Project 表 |
| **注释坑** | 无警告注释；全部端点无鉴权依赖（对比 cameras.py 写操作有 require_perm） |

### `backend/api/weighing.py`（147 行，v3.45 复核）

| 维度 | 内容 |
|---|---|
| **职责** | v3.31 称重投料模式控制/查询 API（`/api/v1/weighing/*`）：前置选择人员/型号、扫码开始一件、手动去皮/置零、复位、视觉料别标签喂入、逐件记录查询、虚拟喂重；v3.45 增作业员候选名单 |
| **核心端点** | L43-46 `GET /weighing/state` 工位实时快照；`POST /context`（6.5 前置选择）；`POST /scan` 扫码开始一件；`POST /tare` / `POST /zero`（给称重器发 T/Z）；`POST /reset` 放弃重来；`POST /material-label`（6.3 视觉料别）；`GET /records`（6.1 数据页）；`POST /feed` 虚拟喂一帧重量；**v3.45**：`GET /weighing/operators`（L57，响应模型 `OperatorsOut`）——称重配置开 `operator_from_users` 后监控页人员下拉的取数端点，返回用户系统「启用」状态账号的显示名列表（display_name 优先回退 username）；只暴露显示名无敏感字段故**不挂用户管理权限**（操作员工位也要能拉），查询失败返回空名单不抛错（前端下拉退化为空） |
| **线程/锁** | 无（全部委托 weighing_engine 单例，锁在引擎层） |
| **上下游** | 上游：前端称重页/Monitor；下游：`services/weighing_engine.get_weighing_engine()` |
| **注释坑** | L112 虚拟喂重等价真秤一帧读数（无真秤验逻辑用）；非称重通道统一返回 `{"ok": False, "message": "该通道未启用称重模式"}` |

### `backend/api/test_compat_routes.py`（169 行）

| 维度 | 内容 |
|---|---|
| **职责** | 仅 `RUNTIME_MODE=test` 挂载的 BDD/E2E 兼容层：把 feature 文件里的旧 API 路径（/mes/config、/alarm/state、/sessions 等）桥接到真实端点或返回 stub，不污染产品代码 |
| **核心函数** | L23-25 `_require_test_mode()` 非测试模式 404；L28-41 SystemConfig KV 读写工具（key 前缀 `test_compat.`）；L55-97 MES 兼容 4 端点（config 存 KV、scanner/adapters 纯 stub、workorders 真查表）；L105-135 Alarm 兼容 3 端点（state 真读 alarm_router ch0，devices 真枚举串口）；L143-148 `/source/project_config` 只回显 keys；L156-169 `/sessions` 旧路径真查 DetectionSession |
| **线程/锁** | 无 |
| **注释坑** | L4-5 所有 handler 都做最小占位/转发，绝不影响生产逻辑；L89 workorders 用 `getattr(r, "code", None)`——WorkOrder 实际字段是 order_no 无 code，永远 None（见疑点清单 #8） |

### `backend/api/test_runtime_routes.py`（284 行）

| 维度 | 内容 |
|---|---|
| **职责** | 仅 `RUNTIME_MODE=test` 挂载的 synthetic 虚拟源控制 API：启停剧本源、注入最小项目配置、模拟包装结算、协调器复位、直接触发插件 cycle_end hook |
| **核心函数** | L30-39 `_collect_labels_from_timeline()`；L42-88 `_build_min_project_config()` 构造能走通结算的最小配置；L91-143 `POST /start`（scenario_json/scenario 两入口，with_project 时注入临时项目配置，project_id 优先级 body > mgr 已有 > -1）；L146-154 `/stop`；L157-163 `/state`；L183-224 `POST /packaging-settle` 模拟检测层结算一个箱（probe_labels/paper_done 可覆盖视觉探测，finally 复位真探针不污染后续）；L227-247 `POST /packaging-reset` 清协调器内存在途 + 重接真实钩子 + 重载配置；L250-284 `POST /fire-plugin-cycle-end` G1.5 直接 fire 插件 hook |
| **线程/锁** | 无 |
| **注释坑** | L44-45 steps 必须带 id——steps_config 兜底只认有 id 的步骤，缺 id 首步标签解析不出周期永不结算；L56-57 sequence_order 必须显式给，缺了结算时**静默丢弃周期**永远不出 OK/NG（2026-06-11 排查实锤）；L66-67 events_config 里 id 1=OK、2=NG 是 `_trigger_event` 内置约定，缺事件定义会"事件未找到"早退周期变孤儿；L230-232 后端长驻进程跨 UAT 脚本时协调器 _runs 会残留上轮在途工单污染下一轮 |

---

## 四、core / db / hcnetsdk（8 个文件，v3.41 复核 +2）

### `backend/core/debug_center.py`（161 行）

| 维度 | 内容 |
|---|---|
| **职责** | 按类别开关的运行时调试日志总线：默认全关内存态重启归零；开启时写环形缓冲（deque maxlen=3000 自增 seq）+ stdout（进 Electron backend.log）+ DATA_DIR/logs/backend-debug.log（10MB 滚动）；前端埋点经 `/debug/client-log` 汇入同一缓冲 |
| **核心函数** | L23-41 `BACKEND_CATEGORIES` 17 个类别目录（检测核心/MES/系统三组）；L61-84 `_write_file()` UTF-8 滚动写盘，任何 IO 失败静默；L89-91 `is_on()` 热路径守门；L94-113 `dbg()` 类别未开启一次 dict 查询即返回；L116-132 `ingest_client()` 前端埋点入口（不查后端开关，前端已自行守门）；L139-144 `set_flags()` 只接受已注册类别防任意 key 注入；L147-155 `get_logs()` 按 since_seq 增量拉取 |
| **线程/锁** | L46 `_lock`（保护 `_seq`/`_buffer`）；`_flags` 读写无锁（bool 覆盖原子性可接受） |
| **上下游** | 上游：全后端各模块调 `dbg()`、`api/debug.py` 路由；下游：DATA_DIR 日志文件 |
| **状态变量** | `_flags`（17 类开关）、`_buffer`（环形缓冲）、`_seq`、`_file`/`_file_size` |
| **注释坑** | L5-7 关闭时 dbg() 只做一次 dict 查询即返回，热路径零字符串拼接由调用方 `if debug_center.is_on(cat):` 守门保证；L62 调试设施不能反噬主流程；L158-161 `clear_logs()` 声明 `global _seq` 但只清缓冲不重置 seq（增量轮询语义正确，global 声明冗余） |

### `backend/core/plugin_secret.py`（36 行）

| 维度 | 内容 |
|---|---|
| **职责** | 插件客户绑定 HMAC 密钥（PLUGIN_SECRET）全网统一固定值：主程序验 customer_hmac 用，模块 import 时即加载为 32 byte 常量 |
| **核心函数** | L24-33 `_load()` 优先环境变量 `PLUGIN_SECRET_HEX`（须 32 byte），否则内置 base64 默认值；L36 `PLUGIN_SECRET` 模块级常量 |
| **线程/锁** | 无 |
| **注释坑** | L5-7 安全模型已明确接受它可被反编译获取——真正防伪底线是 RSA 私钥（永不进主程序），HMAC 只是防中级攻击者换 customer_code 复用插件的第二道锁；L13 **更换本密钥会使所有已签插件失效（需重新签名），非必要不要改** |

### `backend/db/sql_compat.py`（27 行）

| 维度 | 内容 |
|---|---|
| **职责** | 跨数据库 SQL 表达式兼容工具：SQLite strftime vs PostgreSQL to_char，为 PostgreSQL 迁移铺路 |
| **核心函数** | L16-20 `hour_minute(col)` 返回 'HH:MM' 表达式；L23-27 `date_str(col)` 返回 'YYYY-MM-DD' 表达式 |
| **线程/锁** | 无 |
| **上下游** | 上游：`api/reports.py` 时段过滤等；下游：`db/database.get_dialect()` |
| **注释坑** | 无 |

### `backend/db/migrations/__init__.py`（113 行，v3.45 复核）

| 维度 | 内容 |
|---|---|
| **职责** | 版本化数据库迁移注册表 + runner（2026-07 治理批次落地，取代 `main.py` 只增不减的 `migrate_database()` 大列表——旧函数已改断言桩，别再往里塞 ALTER）。启动时 `apply_pending(engine)` 按编号升序应用缺失迁移，行为对外等价于旧函数 |
| **核心函数** | L25-32 `_MIGRATION_MODULES` 显式注册表（**不做目录扫描**——兼容 Nuitka 编译形态；v3.43 注册 m0002/m0003，v3.45 注册 m0004/m0005，v3.46 注册 m0006，**v3.47 注册 m0007/m0008**）；L35 `_ALWAYS_RUN={"m0000_legacy"}`；L34-42 `_load_migrations()` 断言 MIGRATION_ID 与文件名一致；L45-58 `_ensure_ledger()` 建 `schema_migrations` 记账表；L61-68 `_applied_ids()`；L71-80 `_record()` 记账；L83-109 `apply_pending()`：m0000 不看记账每次幂等跑、其余"记账跳过"，某个新迁移失败即停止应用后续迁移（保留原库、不阻断启动） |
| **线程/锁** | 无（启动时单线程跑） |
| **上下游** | 上游：`main.py` 启动调用；下游：各 `mXXXX_*.py` 模块 + `schema_migrations` 表 |
| **注释坑** | L10 记账表建不起来（磁盘满/只读）→ 降级为只跑 m0000 幂等路径，打显著日志不阻断启动；L13-16 **新增 schema 变更三步规约**（即 AGENTS.md 不变量 8）：①改 ORM 模型 ②本目录新建 `m<下一编号>_<语义名>.py::apply(engine)` 写 DDL/数据回填 ③模块名追加到 `_MIGRATION_MODULES` 显式注册；设计 RFC 见 `docs/rfc/DB迁移版本化治理_设计方案_RFC.md`；记账失败不致命（L79-80，下次启动幂等/重跑兜底） |

### `backend/db/migrations/m0000_legacy.py`（224 行，v3.41 复核）

| 维度 | 内容 |
|---|---|
| **职责** | 存量迁移基线（原 `main.py:migrate_database` 原样平移，2026-07 冻结）：约 109 条补列清单逐列探查缺列才 ALTER + 方言归一 + v3.10 阶段 5 DROP operators 表；runner 的 `_ALWAYS_RUN` 成员，永远幂等执行 |
| **核心函数** | L17-224 `apply(engine)`：L19-176 `migrations` 补列清单（从 v2.7.5 起全部历史补列，含 scanner/external_devices/packaging_flow_configs 大宗）；L183-191 `_normalize_type()` SQLite 风格 DDL 翻译成 PostgreSQL（BOOLEAN DEFAULT 1→TRUE、JSON→JSONB）；L210-224 阶段 5 清理 DROP TABLE operators |
| **线程/锁** | 无（启动时单线程跑） |
| **上下游** | 上游：`db/migrations/__init__.py` runner；下游：inspector + 原生 ALTER TABLE |
| **注释坑** | L7-8 **本文件已冻结**：不要再往 migrations 列表追加新条目，新 schema 变更新建 `m<编号>_<语义名>.py`；L18 逐字平移不做任何"顺手优化"；L67-68 pull_enabled/pull_interval_sec 两列 ORM 早有声明但历史迁移漏补；L211-214 detection_sessions/cycles 的 operator_id 列保留、语义已重定向到 users.id，历史指向不存在 user 时代码层已做 None 兜底 |

> v3.35.0 变更（v3.41 复核）：补列清单仍追加了 `packaging_flow_configs` 7 列（L156-165：v3.30.1 复合条码取段 5 列 composite_* + 工单号识别正则 order_code_pattern + v3.34.1 放工单=尾箱收尾 tail_paper_as_close_action）——发生在首个增量迁移 m0001 建立（v3.38）之前，与头部"已冻结"声明存在张力；此后新列应一律走新迁移模块。

### `backend/db/migrations/m0001_hot_path_indexes.py`（33 行，v3.38 新增）

| 维度 | 内容 |
|---|---|
| **职责** | 首个增量迁移（川南"框冻结"第三批优化）：补两个长期缺失的热路径查询索引——`step_records.cycle_id`（ix_step_records_cycle_id）与 `video_clips.related_id`（ix_video_clips_related_id）。周期收尾/自动清理/数据页按周期号查步骤、清理按归属周期/步骤找录像，无索引时均为全表扫描；v3.38 清理分批删除的每批锁窗口也被扫描时长放大 |
| **核心函数** | L20-23 `_INDEXES` 清单；L26-33 `apply(engine)` 逐条 `CREATE INDEX IF NOT EXISTS` + 打印耗时留痕 |
| **线程/锁** | 无（启动时单线程跑，记账后只跑一次） |
| **上下游** | 上游：runner；下游：原生 SQL。ORM 侧 `models.py` 两列同步声明 `index=True`（新库建表即带索引，老库靠本迁移补建，索引名两边一致） |
| **注释坑** | L9-10 CREATE INDEX 在大库上耗秒级（百万行 ≈ 数秒）属一次性成本，老客户升级首启动稍慢属预期 |

### `backend/db/migrations/m0004_pkg_box_label_scan.py`（46 行，v3.45 新增）

| 维度 | 内容 |
|---|---|
| **职责** | v3.45 包装结算「箱标签扫码授权 + 标签取本箱数量」配置列（组⑧，全可选默认关零差异）：给 `packaging_flow_configs` 补 10 列——box_label_scan_required（每箱开做前必须扫箱标签）/ label_qty_enabled + label_qty_segment（默认3）+ label_qty_pattern（从标签复合串取"本箱数量"当本箱滑块目标，逐箱可变）/ label_rescan_action（默认'ignore'）/ unauthorized_cycle_action（默认'hold'，未授权箱做完周期的处置）/ label_total_check（工单收尾 Σ各箱标签数量 vs 排产量对账）+ 三个可配报警事件列 event_box_not_scanned / event_label_qty_missing / event_label_total_mismatch |
| **核心函数** | L15-26 `_COLUMNS` 清单；L29-46 `apply(engine)` 逐列探查缺列才 ALTER（表不存在/列已存在跳过），PostgreSQL 方言 `BOOLEAN DEFAULT 0`→`FALSE` 归一 |
| **上下游** | 上游：runner（`_MIGRATION_MODULES` 已注册）；下游：`PackagingFlowConfig` ORM 同步声明（见 03 册）+ 协调器 `_row_to_dict` 快照（见 02 册） |

### `backend/db/migrations/m0005_pkg_sync_work_orders.py`（34 行，v3.45 新增）

| 维度 | 内容 |
|---|---|
| **职责** | v3.45 包装结算「工单同步进工单管理」开关列：给 `packaging_flow_configs` 补 `sync_work_orders`（**BOOLEAN DEFAULT 1 默认开**——只补数据可见性不改判定行为，关=老行为包装单只存运行记录）；开时扫码开工/收尾/中止把包装工单镜像到 `work_orders` 表（source='packaging'），工单管理页可见可管理 |
| **核心函数** | L12-14 `_COLUMNS` 单列清单；L17-34 `apply(engine)` 同 m0004 幂等补列模板（PostgreSQL `DEFAULT 1`→`TRUE`） |
| **上下游** | 上游：runner；下游：协调器 `_sync_work_order`（老库 NULL 在 `_row_to_dict` 侧视为开，见 02 册） |

### `backend/db/migrations/m0006_sms_report_content_template.py`（30 行，v3.46 新增）

| 维度 | 内容 |
|---|---|
| **职责** | 短信日报规则「正文模板」列：`sms_report_rules` 补 `content_template` TEXT（内容式通道 AT/HTTP/WxPusher 的 `${变量}` 正文模板，云模板通道忽略）。该表未随任何已发版本出厂，只兜底开发期已建表的库 |
| **核心函数** | 幂等补列模板（表不存在/列已存在跳过） |

### `backend/db/migrations/m0007_defect_cycle_index.py`（29 行，v3.47 新增）

| 维度 | 内容 |
|---|---|
| **职责** | `defect_records.cycle_id` 补索引（2026-08 启动提速批次）：启动孤儿扫描按 cycle_id 做 NOT EXISTS 关联探查，缺陷数据多的客户机每次开机全表扫描 |
| **核心函数** | `CREATE INDEX IF NOT EXISTS ix_defect_records_cycle_id`——索引名与 ORM `index=True` 的 SQLAlchemy 默认命名一致，新装库 create_all 与老库迁移收敛同一形态 |
| **注释坑** | ⚠️ 本编号曾与互连分支的迁移撞号——多分支并行各自领 `m0007`，合并时互连迁移改号 `m0008`（含文件名与 MIGRATION_ID 双改），撞号处置惯例见 `modify-model` skill |

### `backend/db/migrations/m0008_model_interconnect_meta.py`（29 行，v3.47 新增）

| 维度 | 内容 |
|---|---|
| **职责** | 训练平台互连：`models` 表补 `source` VARCHAR(50) DEFAULT 'local'（'local'/'yolovision'，老库存量 NULL 读取端视同 local）+ `meta` JSON（x-analysis 训练分析/包 provenance；PostgreSQL 用 JSONB 分道） |
| **核心函数** | inspect 探查缺列才 ALTER；`models` 表不存在直接返回 |
| **上下游** | 下游：`Model` ORM（见 03 册）+ `package_ingest`（写 meta）+ 前端模型仓库来源徽标 |

### `backend/hcnetsdk/types.py`（150 行）

| 维度 | 内容 |
|---|---|
| **职责** | HCNetSDK + PlayCtrl 最小 ctypes 结构定义：仅含登录/实时预览/解码所需结构与回调类型 |
| **核心定义** | L13-16 调用约定（Windows WINFUNCTYPE / Linux CFUNCTYPE）；L21-24 数据类型常量（SYSHEAD/STREAMDATA 等）；L31-64 `NET_DVR_DEVICEINFO_V30`；L67-71 `NET_DVR_LOCAL_SDK_PATH`；L74-92 `NET_DVR_PREVIEWINFO`；L95-109 `NET_DVR_LOCAL_GENERAL_CFG`；L116-124 `FRAME_INFO`（PlayCtrl）；L132-150 `REALDATACALLBACK`/`DECCBFUN` 回调签名 |
| **线程/锁** | 无 |
| **注释坑** | L4 只包含 login/preview/decode 所需结构（Minimal 定义，扩 SDK 功能需补结构） |

### `backend/hcnetsdk/wrapper.py`（389 行）

| 维度 | 内容 |
|---|---|
| **职责** | HCNetSDK + PlayCtrl 高层封装 `HCNetSession`：登录 NVR/IP 相机、实时预览、native 回调软解码 YV12/I420→BGR numpy 帧 |
| **核心类/函数** | L34 `HCNetSession`（DLL 类级加载一次）；L71-149 `_ensure_sdk_loaded()` 类方法：chdir 到 DLL 目录加载、SetSDKInitCfg 设路径+crypto/ssl 库（Win 路径 gbk 编码）、Init+SetReconnect(10s)；L151-172 `_resolve_dll_dir()` 候选链 hint→HCNETSDK_DIR 环境变量→包内 lib/→C:\HCNetSDK\lib；L182-208 `login()`（V30，失败抛 ConnectionError 带错误码）；L222-255 `start_preview()` PlayM4_GetPort→RealPlay_V40 挂实时数据回调；L257-270 `stop_preview()`；L276-286 `get_frame()`（事件等待）/`get_latest_frame()`；L300-326 `_on_real_data()` SYSHEAD 时开流挂解码回调、STREAMDATA 喂数据；L328-358 `_on_decode()` YV12(type=3)/I420(type=5)→cv2 转 BGR；L385-389 `__del__` 兜底 cleanup |
| **线程/锁** | L43 类级 `_init_lock`（SDK 一次性初始化）；L52 `_frame_lock` + L53 `_frame_event`（帧交接）；回调运行在 **native SDK 线程**上 |
| **上下游** | 上游：source 的海康 NVR 接入 mixin；下游：HCNetSDK.dll/PlayCtrl.dll（Linux .so）、cv2（解码回调内惰性 import） |
| **状态变量** | `_user_id`/`_play_handle`/`_play_port`/`_frame`/`_alive`/`_real_data_cb_ref`/`_decode_cb_ref`（回调对象必须持引用防 GC） |
| **注释坑** | L58-60（C8）`_alive` 存活标志：stop_preview 一进来先置 False，native 线程上仍在飞的回调据此提前返回，**防 stop 后竞态崩溃**；L254 预览成功后才放行回调处理；L110 Windows 下 SDK 路径必须 gbk 编码 |

---

## 五、schemas 简表（5 个文件）

| 文件 | 行数 | 职责 | 关键类（行号） |
|---|---|---|---|
| `backend/schemas/camera.py` | 34 | 旧式相机表 Pydantic Schema | L5 `CameraBase`；L14 `CameraCreate`；L17 `CameraUpdate`（全可选）；L27 `CameraResponse`（from_attributes） |
| `backend/schemas/model.py` | 81 | 模型上传/转换 Schema | L5 `ModelBase`（framework 默认 PyTorch）；L21 `ModelResponse`（含 labels 列表）；L39 `ConversionRequest`/L43 `ConversionResponse`（含 gpu_name/gpu_arch）；L68 `FormatInfo`/L77 `FormatsAvailableResponse`（可用格式探测+推荐） |
| `backend/schemas/project.py` | 78 | 项目 CRUD Schema + 插件数据补丁 | L5 `ProjectBase`（7 个 JSON 配置字段全在此）；L36 `ProjectResponse`（L45-48 注释：model_labels 是模型能识别的全部类别，steps_config 是纳入业务步骤的子集，**二者不必相等**）；L61 `ProjectPluginDataPatch`（v3.13 M3.1 精准 PATCH `<scope>.plugin_data.<customer_code>` 子树，浅合并、别的客户子键完全不动） |
| `backend/schemas/report.py` | 28 | 报表响应 Schema | L4 `ReportQuery`；L9 `ReportSummary`；L16 `DailyStatResponse`；L24 `TrendData`（四平行数组） |
| `backend/schemas/task.py` | 30 | 旧 Task 表 Schema | L5 `TaskCreate`；L11 `TaskResponse`（is_good 默认 True）；L28 `TaskListResponse` |

---

## 六、脚本 / 版本 / 依赖文件（4 个文件，v3.41 复核 +1）

### `backend/scripts/generate_license.py`（89 行）

| 维度 | 内容 |
|---|---|
| **职责** | License 生成 CLI（发商用授权用）：payload（machineId/customerName/expiresAt）JSON 紧凑序列化后 RSA PKCS1v15+SHA256 私钥签名，输出 `.lic` 文件（{data, signature} 结构）供客户导入 |
| **核心函数** | L28-59 `generate_license()`；L62-85 `main()` argparse（--machine-id/--customer 必填，--permanent 或 --expires，私钥默认 `<项目根>/keys/private.pem`） |
| **线程/锁** | 无 |
| **上下游** | 上游：手动运维执行；下游：cryptography 库；对端验签在 Electron License 验证（debug-operator-license skill 域） |
| **注释坑** | L39 payload 用 `separators=(',',':')` 紧凑序列化——**验签侧必须逐字节一致**，改此处序列化参数会使全部已发 license 验签失败 |

### `backend/scripts/run_offline_video_test.py`（111 行）

| 维度 | 内容 |
|---|---|
| **职责** | 离线回归脚本：用仓库根目录的 `best(5).pt` + `01_2K17411_03.avi` 在后端直接跑完整检测流程（set_project_config→start_video→start_detection→start_session→等视频跑完→end_session），输出周期数/OK/NG/步骤序列 |
| **核心函数** | L6-106 `main()`：优先当前激活项目否则最新项目；L68-69 conf=0.25/iou=0.45 硬编码；L81-82 轮询 `is_running` 等视频结束 |
| **线程/锁** | 无 |
| **上下游** | 上游：手动执行（须 tianjun conda env）；下游：`get_video_manager()`（**单通道 proxy，即 ch0**）、Project/DetectionSession/DetectionCycle 表 |
| **注释坑** | L10-12 模型与视频路径硬编码依赖仓库根目录两个文件——文件不在则直接抛 FileNotFoundError（这两个文件不进 git，脚本仅限有素材的开发机可跑） |

### `backend/_version.py`（51 行）

| 维度 | 内容 |
|---|---|
| **职责** | 主程序版本号唯一来源（运行时只读，`lru_cache` 进程级缓存）：权威源是 `electron/package.json`，供插件 `main_version_min` 兼容性校验 |
| **核心函数** | L27-51 `get_main_version()` 三级优先：①环境变量 `TIANJUN_APP_VERSION`（Electron 启动后端时注入）②回退读 electron/package.json（仅开发模式命中）③失败回退 "0.0.0" |
| **线程/锁** | 无（lru_cache 线程安全） |
| **注释坑** | L6-7 回退 "0.0.0" 是安全失败侧——让任何 main_version_min 检查都失败（插件不工作 > 误判兼容）；L32-34 **正式安装包必走环境变量**：打包后 package.json 进了 app.asar，Python fs 读不到，之前漏读此 env 导致客户机插件版本校验恒为 0.0.0 而误拒；L9-12 为什么不在 `backend/__init__.py` 写常量：后端再维护一份必然漂移 |

### `backend/requirements.txt`（70 行，v3.41 复核新增条目）

| 维度 | 内容 |
|---|---|
| **职责** | 后端 Python 依赖清单（CI 用 conda-pack 打环境时的安装依据；torch/mediapipe 等特殊依赖单独装，见文件内 Note 注释） |
| **线程/锁** | 无 |
| **上下游** | 上游：CI 打包流程 / 开发环境搭建；下游：pip |
| **注释坑** | L38 dmPython 官方只发 cp310 win_amd64 + manylinux wheel——升级 Python 版本前先确认驱动 wheel 覆盖；L32 opencv-contrib-python<4.12 钉住是因为 4.13+ 要 numpy>=2 与 mediapipe 冲突 |

> v3.35 变更（v3.41 复核）：L37-41 新增 MES 网关"数据库直写"适配器的客户关系库驱动——dmPython>=2.5.32（达梦）、pymysql>=1.1.0（MySQL 纯 Python）、pymssql>=2.2.11（SQL Server），PG 驱动 Database 段已有；随安装包内置、客户机零操作（v3.35.1"直写驱动内置"）。

---

## 七、疑点清单

读码中发现的可疑设计 / 死代码 / 文档与代码不一致（按文件顺序编号）：

1. **`backend/services/defect.py` L38-59 — `matched_any` 标志跨标签循环不复位**：`auto_record()` 的外层 for 逐标签匹配缺陷码，但 `matched_any` 在标签之间不重置——第一个标签命中后，后续未命中的标签既不会匹配缺陷码也不会走 `AUTO-<label>` 兜底，被**静默丢弃**。多标签 NG（如"缺失A，缺失B"且 B 无缺陷码映射）只落一条缺陷。疑似应为每标签独立的局部标志。
2. **`backend/services/mes_adapters/modbus_adapter.py` L203 — TCP 分支互斥失效**：`lock = _serial_lock if transport == "rtu" else threading.Lock()`——TCP 每次 new 一个临时锁，`with lock:` 无任何互斥效果（多线程可并发写同一 TCP 从站）。另 L232 结果回显 `addr + 40001` 硬编码按保持寄存器段还原，coil（1-9999）/input（30001 段）配置时回显地址错段（仅影响日志/回显，不影响实际写入）。
3. **`query_string_adapter.py` L25-38 与 `form_urlencoded_adapter.py` L26-39 — `_flatten_value` 逐行重复实现**：两份完全相同的私有函数，改拍平语义须记得同步两处（无单一事实源）。
4. **`backend/services/export_renderer.py` L20 — `import io` 未使用**：全文件无 `io.` 引用（已 grep 证实），死 import。
5. **`export_renderer_xlsx.py` L81-85 与 `export_renderer.py` L109-116 语义冲突**：渲染核心提供 `csv_esc` 过滤器（含逗号字段加双引号），但 xlsx 路线 A 的 `_split_csv_line()` 是"简单逗号拆分不处理引号转义"——客户在模板里用了 `csv_esc` 再导出 xlsx 时，带引号字段会被错误拆列且引号残留。docx（L96）/pdf（L123）的 CSV 表格识别同样不处理引号。
6. **`backend/api/cameras.py` L15 — `active_cameras = {}` 死变量**：声明后全仓库无任何读写（已 grep 证实），疑似早期"相机实例复用"设想的残留；当前实现每次拉流现开现关 VideoCapture。
7. **`backend/api/reports.py` L232-316 — PDF 导出未注册中文字体**：直接用 reportlab 默认样式（Helvetica），项目名等中文内容会显示为黑块/乱码；同仓库 `export_renderer_pdf.py` L28-42 已有 STSong-Light 注册方案未复用。另 L117-119 `/records` 每条记录单独查一次 Project 是 N+1 查询（limit 默认 100，量大时慢）。
8. **`backend/api/test_compat_routes.py` L89 — `getattr(r, "code", None)` 永远返回 None**：WorkOrder 模型的字段是 `order_no`（见 `services/work_order.py` L77），没有 `code` 属性，测试兼容端点 `/mes/workorders` 返回的 items 里 code 恒为 null（stub 语义下无害，但形同虚设）。
9. **`backend/api/rod_filter.py` — 纯函数库放在 api/ 目录**：全文件无 APIRouter/无路由，本质是检测后处理服务，按分层应归 `backend/services/`；放 api/ 下易被误认为有 HTTP 端点（router_manifest 中也无挂载）。
10. **`backend/services/work_order.py` L170-173 — `get_active_order()` 的 station_id 参数声明后未使用**：函数体只处理 project/channels 两种域（cluster 显式跳过），station_id 从未参与匹配，疑似 cluster 域早期设计残留的接口位。
11. **`backend/db/migrations/m0000_legacy.py` L193-208（v3.41 复核行号）— 补列循环整体包一个 try**：任一条 ALTER 抛异常（如列类型不兼容）会中断**其后全部**补列，仅 print 一行"数据库迁移检查"不抛出——老库升级若中途失败，后续缺列静默漏补，启动继续（后续 ORM 访问缺列时才炸）。冻结文件不宜改，但排查"升级后缺列"问题时应先查这里。
12. **`backend/scripts/run_offline_video_test.py` L25-26 — 素材路径硬编码**：依赖仓库根的 `best(5).pt` 与 `01_2K17411_03.avi`（均不入库），无参数化入口；其他机器上必抛 FileNotFoundError，实用性受限（对比 tests/ 下 synthetic 剧本源已可无素材跑 pipeline）。

---

**本文件最后更新**：2026-08-07（v3.47 补账：新增「训练平台互连」节（`api/interconnect.py` + `services/interconnect/` 7 文件）+ 短信节 v3.47 增量（config 嵌套优先/汇总数字口径）+ m0006/m0007/m0008 三迁移条目 + runner 注册表刷新；上一轮 2026-07-29 v3.45 补账）
