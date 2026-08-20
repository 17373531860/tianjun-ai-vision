---
name: debug-mes
description: "诊断MES系统问题：工单/工件/缺陷/扫码器/Hook/Gateway/集群/外设。当 MES 相关功能异常（数据不流转、扫码不绑、外推失败、集群汇不齐、有重无码不报警等）时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__sequential-thinking, mcp__sentry"
---

> **设计深潜（为什么这样设计）**：`docs/dev/internals/mes-hook-pipeline.md` + `docs/dev/internals/cluster-collector.md`  
> 本 skill = how-to/debug；深潜 = explanation。冲突以代码为准。

# debug-mes: MES 子系统诊断（v3.5.x 主线）

> 阅前先看 `AGENTS.md` 第六节 6.2（模块 6/7/8/9）+ 第七节扩展点表 + 第八节不变量 1/4/6。
> 这份 skill 覆盖**内置 MES 业务**（工单/工件/缺陷）+ **扫码器**（LON/WMax/虚拟）
> + **外部 MES Gateway**（6 个适配器） + **集群汇总**（standalone/host/slave）
> + **外部设备 pipeline**（称重器等）。

---

## 一、组件全景图

```
                    Detection Engine (VideoSourceManager)
                                 │ on_scan_received / on_cycle_*/on_session_*
                                 ▼
                ┌─────────────────────────────────────────┐
                │  MESHookManager (singleton)             │
                │  + 后台线程 mes-hook-worker             │
                │  + 独立 SessionLocal (不与检测争锁)     │
                │  + queue (max=500) + 落盘 spool         │
                │  + scan_pair 状态机 (v3.3.0)            │
                │  + _disabled_channels 落盘 (v3.4.2)     │
                │  + has_any_scanner_present (v3.5.x)     │
                └────┬────────────┬─────────────┬─────────┘
                     │            │             │
            ┌────────▼──┐  ┌──────▼──────┐  ┌───▼──────────────────┐
            │WorkOrder  │  │ Workpiece + │  │ ClusterCollector     │
            │Service    │  │ Defect Svc  │  │ (心跳/超时/box聚齐)  │
            │binding_   │  │             │  │                      │
            │scope      │  │             │  └───────┬──────────────┘
            └───────────┘  └─────────────┘          │
                                                    │
                                            ┌───────▼──────────┐
                                            │ MESGateway       │
                                            │ + dispatch       │
                                            │ + auth headers   │
                                            │ + push_on_result │
                                            │ + label_mapping  │
                                            └───────┬──────────┘
                                                    │
                                  ┌─ rest ──────────┼──────────── form-data
                                  ├─ form-urlencoded┤
                                  ├─ query-string   ┤
                                  └─ modbus_rtu ────┘    (mes_adapters/_REGISTRY)

       ┌─────────────────────────┐         _on_data_received
       │ ScannerService          │ ───────────────┐
       │ + LON _listen_loop       │                ▼
       │ + WMax 三端口后台线程     │      MESHook.on_scan_received
       │ + Virtual 扫码器 (-999)  │                │
       └────────┬────────────────┘                │
                │ _inject_barcode_to_external      │
                ▼                                  │
       ┌────────────────────────────┐ ←────────────┘
       │ ExternalDeviceService      │
       │ (Mixin: Protocols/Pipeline │
       │  /Test)                    │
       │ + 称重 idle/stabilizing/   │
       │   stable 状态机             │
       │ + 有重无码 → alarm +       │
       │   gateway.dispatch         │
       │   ("weight_no_barcode")    │
       └────────────────────────────┘
```

> **关键不变量**（来自 AGENTS.md 第八节）：
> - `channel_manager.set_channel_count` 必须调用 `mes_hook.on_channel_removed(cid)`
>   + `alarm_router.on_channel_removed(cid)`，否则 `_active_orders` / `_pending_workpiece` 等 dict 残留。
> - 改 `mes_hooks.py` / `services/scanner.py` 前先看本 skill 的"已知坑位"。

---

## 二、文件地图（v3.5.x 实测行数）

| 文件 | 行数 | 职责 |
|---|---|---|
| `backend/models/mes_models.py` | 576 | **15 张** ORM 表（见下） |
| `backend/services/mes_hooks.py` | **1505** | 项目最大 Hub；singleton；worker 线程；scan_pair；_disabled_channels |
| `backend/services/scanner.py` | **1964** | 项目第二大文件；`ScannerService` + `ScannerConnection` + `_text_lon_listen_loop` + WMax 联动 |
| `backend/services/mes_gateway.py` | 432 | `MESGateway` 单例；dispatch/重试/鉴权/`_apply_auth_to_headers`/`build_context_from_cycle` |
| `backend/services/mes_adapters/__init__.py` | 31 | `_REGISTRY` 注册 6 种适配器 |
| `backend/services/mes_adapters/base.py` | 79 | **自研 `{key.path}` 模板引擎**（不是 Jinja2） + `_array_source` 数组展开 |
| `backend/services/mes_adapters/{rest,form_data,form_urlencoded,query_string,modbus}_adapter.py` | 各 ~100-300 | 5 种实际发送实现 |
| `backend/services/cluster_collector.py` | 1122 | host 汇总；per-box 锁；心跳后台线程；超时检查器；commit 重试 |
| `backend/services/work_order.py` | 357 | `WorkOrderService`；`binding_scope` 三选一；`find_cluster_orders` |
| `backend/services/workpiece.py` | 235 | `register/mark_inspecting/set_result` |
| `backend/services/defect.py` | 243 | `auto_record_from_cycle` + 帕累托 |
| `backend/services/external_device.py` | ~390 | `ExternalDeviceService` (Pipeline / Protocols / Test 三 mixin) |
| `backend/services/external_device_pipeline.py` | ~430 | 称重稳态机 + 有重无码告警 |
| `backend/services/wmax/{device,manager,discovery,messages,protocol,virtual_device}.py` | ~多文件 | WMax 三端口（55266 CMD / 55276 IMG / 55286 RPT）+ UDP 发现 |
| `backend/services/barcode_parser.py` | 99 | direct/regex/split/json，**无 DataLen 校验** |
| `backend/api/scanner.py` | 532 | `/scanner/*`：CRUD + simulate + scan_pair/* + disable-toggle |
| `backend/api/wmax.py` | 672 | `/scanner/wmax/*`：35 个 endpoint |
| `backend/api/mes.py` | 673 | `/mes/*`：26 个 endpoint（orders/workpieces/defects/defect-codes） |
| `backend/api/mes_gateway.py` | 450 | `/mes/gateway/*`：connections/test/push/extra-fields/logs |
| `backend/api/cluster.py` | 295 | `/cluster/*`：config/report/heartbeat/slaves/boxes/health |
| `backend/api/external_device.py` | 365 | `/external-devices/*` |
| `frontend/src/views/MES/*.vue` | OrderPanel / WorkpiecePanel / DefectPanel / ScannerPanel(1381) / WMaxPanel / GatewayPanel(1073) / ClusterPanel / ExternalDevicePanel | 5+ 子页面 |
| `frontend/src/api/{mes,scanner,gateway,cluster,external_device}.js` | — | axios 封装 |

### 15 张 MES 表（`backend/models/mes_models.py`）

`work_orders` / `batches` / `workpieces` / `workpiece_inspections` / `defect_records`
/ `defect_codes` / `scanner_devices` / `scan_logs` / `mes_connections`
/ `mes_comm_logs` / `cluster_config` / `box_aggregations` / `external_devices`
/ `external_device_logs` / `box_summaries`

> ⚠️ 旧版 SKILL 写"10 张"是过时数据，以代码为准。

---

## 三、MES Hook 架构（mes_hooks.py，必读）

### 3.1 关键属性

```python
class MESHookManager:
    _pending_workpiece:    dict[ch, wp_id]      # 扫了码、cycle 还没开
    _inspecting_workpiece: dict[ch, wp_id]      # 已绑入 cycle、检测中
    _active_orders:        dict[ch, order_id]   # session 当前活跃工单
    _last_scan_event:      dict[ch, dict]       # 最近扫码 (供轮询去重)
    _rebind_prompt:        dict[ch, dict]       # manual rebind 等待用户操作
    _scan_pair_active:     dict[ch, {serial_no, wp_id, scanned_at}]  # v3.3.0
    _scan_pair_timeout_timers: dict[ch, threading.Timer]              # v3.3.0
    _disabled_channels:    set[ch]                                    # v3.4.2 持久化
    _task_queue:           queue.Queue(maxsize=500)
    _worker_thread:        Thread(name="mes-hook-worker")
    _spill_file:           ${DATA_DIR}/mes_hook_spool.jsonl  # 队列满时落盘
    _disable_state_file:   ${DATA_DIR}/scanner_runtime_state.json
```

### 3.2 公开 Hook（被 source.py 在 commit 后调用）

| 方法 | 入口 | 队列项 |
|---|---|---|
| `on_scan_received(ch, serial, raw, scanner_id)` | scanner._listen_loop | `_handle_scan` |
| `on_cycle_start(ch, cycle_id, session_id)` | session_lifecycle commit 后 | `_handle_cycle_start` |
| `on_cycle_end(ch, cycle_id, is_good, ...)` | 同上 | `_handle_cycle_end` |
| `on_session_start(ch, session_id, project_id)` | 同上 | `_handle_session_start` |
| `on_session_end(ch, session_id)` | 同上 | `_handle_session_end` |
| `on_channel_removed(ch)` | channel_manager.set_channel_count | 同步清理 6 个 dict + 取消 timer |

**`_enqueue` 设计**（B1① 修订后，AGENTS.md 不变量 #15）：队列满时**绝不阻塞调用方**（旧版
critical 会 `put(timeout=0.8)`，队列长期满时每周期卡 0.8s 拖垮结算节拍，已废弃）。现行为：
- `critical=True`（scan/cycle/session 五个核心 hook 的现有调用点全部是）→ 满时经 `_spill_task`
  落盘 `mes_hook_spool.jsonl`，worker 恢复后 `_drain_spill_once` 回放，业务不丢
- `critical=False` → 满时直接丢弃（当前无调用点，纯预留档）
- 两者都只计数（`_queue_drop_count` / `_queue_block_count`）并立即返回
- 新增 hook 事件若关系业务数据完整性：必须 critical=True **且**把 handler 名加进
  `_is_spillable_handler` 白名单（否则满时既不落盘也不回放）；纯 UI 通知类才可用 False

**`_inspecting_workpiece` 取-放配对铁律**（AGENTS.md 不变量 #14）：放入仅 2 处（cycle 绑定
`_handle_cycle_start` / scan_pair promote），取出仅 5 处（scan_pair settle / cycle_end /
session_end / on_channel_removed / 人工强制作废 force=True）。放取两侧必须严格配对——
多加一处 pop 会导致工件绑错周期、前端"当前工件"卡住或漏绑（v2.7.16 迟到扫码补绑 /
v3.4.2 promote 均为此修过补丁）。

### 3.3 `_handle_cycle_end` 是项目最大 Hub

执行顺序（line 1181 起）：
1. `Workpiece.set_result(is_good)` + `inspection_count++`
2. `defect.auto_record_from_cycle()`（按 step 标签查 `DefectCode`）
3. 工单计件：`work_order.increment_completed()`，到量自动 `change_status('completed')`
4. **db.commit()**（v2.7.9 修：必须先 commit 释放 SQLite 写锁，否则 `receive_station_report` 抢锁 30s+）
5. `MESGateway.build_context_from_cycle(...)` 构建 context
6. `_cluster_dispatch(db, ctx, ch, ...)`（**注意函数名**：`_cluster_dispatch` 不是 `_dispatch_to_cluster`）
7. `wait_all` 模式且 cluster 路径走通 → `skip_cycle_push=True`，不再单工位推 `cycle_end`
8. 否则 `gw.dispatch("cycle_end", ctx, ch)` 推外部 MES
9. **独立 try**：`dispatch_cycle_end_export` 触发自定义实时导出（v3.5.0；与 MES 推送解耦，互不连坐）
10. `auto_rebind` / `manual` rebind 逻辑

> 任意一步异常都打 `traceback.format_exc()` 全栈（v2.7.9 改完，**新写 except 也要默认带 traceback**，否则 cycle_end 静默吞错是历史最大坑）。

---

## 四、扫码器（v3.5.x 三大类）

### 4.1 LON 文本协议（端口 55256）

- 4 种 `scan_mode`（`scanner_devices.scan_mode`）：
  - `continuous`（默认）：ERROR/扫到码后立刻续 LON，灯持续闪
  - `throttled`：ERROR/扫到码后等 `throttle_idle_ms` 才续 LON
  - `once_per_cycle`：扫到码后 LOFF，cycle 结束自动续 LON
  - `D`（v3.4.0）：容器跨线/进区域触发 LON，扫到码自动 LOFF（仅容器项目）
- D 模式几何：`scan_d_geometry=line|zone` + `scan_d_line/{x1,y1,x2,y2,side_a_to_b}` 或 `scan_d_zone=[[x,y],...]`
- 4 种 `bind_timing`：`mid_cycle`（默认）/ `pre_cycle` / `post_cycle` / `scan_pair`（v3.3.0 见下）
- `broadcast_channels`（JSON list）：一台扫码器服务多工位；空 → 仅 `channel_id`
- `broadcast_settle_mode=independent|primary` + `primary_settle_channel`：同一扫码器服务的多工位是否跟随主工位结算
- `ok_rescan_cooldown_sec`（v2.7.12）：A 扫完 OK，距完成 < N 秒再扫到 A → 拒绝；NG 不受冷却
- `late_scan_bind_window_sec`（默认 3）：cycle 已结但晚到的扫码事件 ≤N 秒可补绑
- **v3.50 生命周期三配置**（仅 `text_lon` 且 `scan_mode=once_per_cycle/D` 有意义，面板按此置灰）：
  - `resume_on`：周期结束亮灯时机。`cycle_end`（默认，OK/NG 都续 LON）/ `ok_only`
    （仅 OK 自动亮；NG/未知置 `conn._resume_blocked` 保持灭灯，出口三条：监控页
    "恢复扫码"按钮（`source_routes` 把 `is_resume_blocked` 写进 `mes.scanner_resume_blocked`
    驱动显示）、`POST /api/v1/scanner/resume`、触发中心 `resume_scanner` 动作。
    `end_cycle` 现在把结算结果 `is_good` 传给 `resume_after_cycle`；人工恢复
    `resume_scanning_manual` → `manual=True` 无条件放行）
  - `rearm_forget_last`：恢复亮灯时 `_rearm_forget` 清 `conn.last_scan`（物理去重缓存）
    + `mes_hook.clear_pending_scan(force=False)` 作废未绑定旧码，防旧码挂新周期
  - `strict_ok_dedup`：`_handle_scan` 入口查库，该条码已有 status='ok' 工件 → 永久拒绝。v3.51 起数据中心 clear/all、clear/range 会联动 `_unlock_ok_workpieces`（`sessions_maintenance.py`）把被删周期关联的 ok 工件重置回 registered——客户"删了记录还拒码"先确认版本 ≥3.51 且删的确实是该码关联周期
    （= ok_rescan_cooldown 的无限版）
- **v3.50 拒绝路径统一警告 toast**：强制去重拒绝 / OK 冷却拒绝 / duplicate_scan_action=reject
  三条路径不再静默丢码，走 `_emit_scan_warning(ch, sn, reason)` → `_last_scan_event`
  带 `scan_warning=True + warn_reason` → 前端 `handleScanToast` 弹警告；scan_pair 重复码
  警告并入同一字段体系（旧 `scan_pair_dup_warning` 字段保留兼容）
- **v3.51.1 拒码后重亮灯闭环**（治"扫了已 OK 码后灯永灭产线卡死"）：上面三条拒绝
  路径除弹警告外还调 `mes_hooks._notify_scan_rejected(device_id, ch, sn)` →
  `scanner.notify_scan_rejected` 按本次派发通道集合（`_last_dispatch`）聚合，
  **全部**派发通道都拒绝该码 → `_rearm_after_full_reject` 清 `_wait_cycle_resume`
  / `_ok_ready_marker` / `_lon_sent` 等等待态并置 `_rearm_after_reject_serial`，
  `_schedule_next_lon` 据此跳过周期等待立刻重发 LON；只要有任一通道接受了码就
  不 rearm（不干扰正常在检流程）。排查"拒码后灯不亮"先 grep `全通道拒绝` 日志

### 4.2 WMax 三端口逆向协议（55266 CMD / 55276 IMG / 55286 RPT）

- `device_type=wmax | wmax_scan | auto`
- **每台 WMax 设备 3 条后台线程**：`wmax-cmd-{ip}` / `wmax-img-{ip}` / `wmax-rpt-{ip}`
- API 35+ endpoint（`backend/api/wmax.py`）：discover/connect/handshake/load-config/params/output-config/indicator/preset/autofocus/autotune/trigger-image/stream/run-mode/read-rate/reboot/...
- 虚拟设备：`device_id=-999`，IP = `VIRTUAL_IP`，仅前端演示用
- 自动重连节奏：`trigger_on` 可同步重连；**`trigger_off` 不为停止动作同步重连**
  （v3.0：曾让 `/source/detection/standby` `/stop` 卡 20 秒，已修。**新改不要再加同步重连**）

### 4.3 scan_pair 状态机（v3.3.0，bind_timing="scan_pair"）

```
┌──────────────────────────────────────────────────────────┐
│ 扫码 A 到达 → _scan_pair_active[ch] = {serial=A, wp_id, ts}│
│ source 启 cycle 检测                                       │
│                                                           │
│ 扫码 B (≠A) 到达 →                                         │
│   1. settle_for_scan_pair(ch) → source 把 A 周期结算       │
│      (依据"窗口内物品/箱子是否曾齐过"判 OK/NG)             │
│   2. _scan_pair_active[ch] 替换成 B                        │
│   3. 启 B 周期                                             │
│                                                           │
│ 同码二次扫 (==A) → _scan_pair_emit_dup_toast，不动状态机    │
│                                                           │
│ scan_pair_max_wait_sec > 0 且超时 →                        │
│   _on_scan_pair_timeout → settle (force_ng=True)          │
│                                                           │
│ broadcast_channels 非空 → 联动多个 ch 同步替换/结算         │
└──────────────────────────────────────────────────────────┘
```

source 必须实现 `settle_for_scan_pair(channel_id, *, force_ng, reason)`；
没有该方法时 `_dispatch_scan_pair_settle` 打日志静默返回。

### 4.4 v3.4.2 `_disabled_channels` + 落盘

- 用户在某工位切"禁用扫码" → `set_channel_disabled(ch, True)`
- `ScannerService` 解析"所有 broadcast 覆盖 ch 的扫码器" → 把这些扫码器的所有
  broadcast 工位**联动闭包**全加进集合（保证联动一致）
- 立刻清空联动 ch 的 `_scan_pair_active` / `_pending_workpiece` / `_inspecting_workpiece`
- 落盘到 `${DATA_DIR}/scanner_runtime_state.json`（启动时回放）
- 守门点：禁用工位 → `is_warn_no_barcode`/`is_scan_pair_mode`/`has_pending_workpiece`/
  `get_current_workpiece` 全部短路返回 False/None，让 source 走"项目原生结算"

### 4.5 v3.5.x `has_any_scanner_present()` 全局守门

```python
def is_warn_no_barcode(ch):
    if self.is_channel_scan_disabled(ch): return False
    if not self.has_any_scanner_present(): return False  # v3.5.x 新增
    ...

def has_any_scanner_present():
    # ScannerService._connections 含 DB enabled 的扫码器 + 虚拟设备 (-999)
    return bool(svc._connections)
```

效果：客户**没装任何扫码器** → Monitor 永远不弹"⚠ 未绑码"
（之前会出现"我又没扫码器你警告什么"现场困惑）。

---

## 五、外部 MES Gateway（`mes_gateway.py` + 6 适配器）

### 5.1 6 个适配器（注册名 → 类）

| `adapter_type` | 类 | 协议 | 典型场景 |
|---|---|---|---|
| `rest` | `RESTAdapter` | HTTP JSON | 现代 REST API |
| `form-data` | `FormDataAdapter` | `application/x-www-form-urlencoded`，整 payload 序列化进 `param` 字段 | 客户说"参数叫 param" |
| `form-urlencoded` | `FormUrlencodedAdapter` | 顶层键值平铺到 form 字段 | 客户说"字段分开不要打包" |
| `query-string` | `QueryStringAdapter` | URL query，body 空 | 客户说"参数放 URL 里" |
| `modbus_rtu` | `ModbusRTUAdapter` | RTU/TCP 写 Holding（pymodbus 3.13+） | PLC/工控 |
| `database` | `DatabaseAdapter` | 关系库直写 INSERT（达梦/MySQL/PG/SQLServer/SQLite，v3.35+） | 客户 IT 不开 HTTP，只给中间表（萍乡百斯特达梦） |

注册表：`backend/services/mes_adapters/__init__.py:_REGISTRY`。新增协议直接 `register_adapter(name, cls)`。

`database` 适配器要点（v3.35，`database_adapter.py`）：与 HTTP 适配器**共用同一套模板体系**——template 顶层键名 = 目标表列名，值照常 `{key.path}` 取值；带 `_array_source` 的模板按数组逐行 INSERT（同一事务）。表/列名白名单校验（仅字母数字下划线，可带 schema 点分）防注入；驱动按 `db_type` 懒加载，未装时报错直接写清要装哪个包（达梦=dmPython）；短连接不池化（网关自带重试 + 事件频率低）。前端 GatewayPanel 对它隐藏 URL/鉴权/健康探测等 HTTP 语义字段。回归：`tests/test_mes_database_adapter.py`。

### 5.2 自研模板引擎（不是 Jinja2！）

`backend/services/mes_adapters/base.py:render_template`（79 行）：

```python
"{order.order_no}"           → context["order"]["order_no"]   # 取值
"日期 {timestamp}"           → "日期 2026-05-07T..."           # 字符串替换
{"_array_source":"steps",    → 把 context["steps"] 列表展开,
 "_item_template":{...}}        每项渲染 _item_template
```

> ⚠️ `mes_gateway.py` 顶部注释里写过 "Jinja"，那是过时注释（AGENTS.md 第九节"文档不同步"已记录）。**改造模板系统**前认准 `render_template`。

### 5.3 `_send_to_connection` 的 5 段处理（按序）

1. 合并 `extra_fields[ch]`（Monitor 提交） + `static_fields`（连接配置）→ `full_context`
2. **`push_on_result` 过滤**：配 `["NG"]` 只推 NG，`["OK","NG"]` 全推；
   不匹配 → 写 skip 日志，**不调 adapter**。`/test` 故意绕过此过滤。
   ⚠️ **结果字段三级取值（v3.41 川南修复）**：`overall_result` → `result` → `cycle.result`（嵌套）。
   老代码只读前两个顶层键，而 cycle_end 事件的结果在 `cycle.result` 里 → 取到空串被无条件放行，
   "仅 NG"对周期推送**从未生效**（合格周期照样从报警连接推出去）。改过滤逻辑别把嵌套兜底删了
3. **`label_mapping`**：把 `ng_items`（list[str]）按 dict 映射；
   `mode=replace` 直接替换，`keep_both` 新增 `ng_items_mapped`
4. **`_apply_auth_to_headers(config)`**：5 种 auth 类型
   - `none` / `basic`（保留给 `requests.auth`）
   - `bearer` → `Authorization: Bearer <token>`
   - `api_key` → `<header|X-API-Key>: <value>`
   - `custom_header` → `auth.headers: [{key,value}]` 全部并入
   - 顶层 `config.custom_headers` 列表/字典也并入（额外固定 header）
5. `adapter.build_payload(...)` → `adapter.send(...)`：**重试在 Gateway 层**
   `for attempt in range(1 + retry_count)` + `adapter.check_response`，
   每次成功/失败都写 `MESCommLog`

### 5.4 `build_context_from_cycle` 输出字段（v2.7.5+）

```text
cycle.{id, is_good, result, duration, event_name, ng_reason,
       completed_steps, total_steps, missing_step_count, start/end_time}
project.{id, name}
steps[].{label, index, duration, is_good, confidence, start/end_time}
ng_steps[]                       # steps 子集 (is_good=False)，按 step_record.id
defects[].{defect_code, defect_name, category, severity}
workpiece.{id, serial_no, status, inspection_count}
order.*
operator.{name, employee_no, id}
extra.{...}                      # Monitor 提交的 extra_fields
timestamp                        # 推送瞬间时间戳
overall_result / result          # 顶层便利字段
```

> 新加字段一律走 `getattr(x, 'new_name', None) or getattr(x, 'old_name', None)` 兜底
> （v2.7.9 教训：硬读 `completed_steps` 不存在 → AttributeError → 整段静默返回）。

### 5.5 集群 `aggregated` 顶层便利字段（v2.7.11）

`cluster_collector._check_and_dispatch` / `_push_timeout_result` 在 dispatch 前给 `aggregated` 加：
- `order_no`（取 stations[0].order.order_no）
- `workpiece_id`（取 stations[0].workpiece.serial_no）
- `ng_items`（所有 stations[i].ng_steps[j].label 去重）
- `result`（与 `overall_result` 同值）

超时分支：`missing_stations` 中每个工位作为 `MISSING-<工位名>` 追加到 `ng_items`。

模板写法对比：
```json
// 旧（嵌套，运维改不动）
{"order": "{stations[0].order.order_no}"}
// 新（顶层）
{"order_no": "{order_no}", "result": "{result}"}
```

---

## 六、工单 binding_scope（v3.1.0）

| `binding_scope` | 谁找工单 | +1 时机 |
|---|---|---|
| `project` | `_handle_session_start` 调 `get_active_order(project_id, ch)` | `_handle_cycle_end` → `increment_completed` |
| `channels` | 同上但按 `ch ∈ target_channels` 匹配，不看 project_id | 同上 |
| `cluster` | 不进 `_active_orders`，由 `cluster_collector` 直接 `find_cluster_orders` | `cluster_collector._check_and_dispatch` 推送后 + 非 `is_recovery` → `_increment_cluster_orders` |

> 校正重推不重复 +1：`is_recovery=True` 时 cluster 计件跳过。
> 工单中途切 `in_progress` 不会 hot bind（`_active_orders` 只在 session_start 时填一次，**已知设计**）。

---

## 七、集群（standalone / host / slave）

- 每路 cycle → POST `/cluster/report` 给 master
- master 按 `box_serial` 聚齐 `expected_stations` → push MES `box_complete`
- 副机靠 `_heartbeat_sender_loop` 后台线程定期 POST `/cluster/heartbeat`（v2.7.8）
- 主机 `_timeout_checker` 每箱独立 session、`_commit_with_retry` 指数退避（v2.7.9）
- per-box 锁（`_acquire_box_lock`）：同箱号串行、不同箱号并发；防 `UNIQUE(box_serial,station_id)` 冲突
- `station_result_strategy=latest|ok_lock`（v3.1.2）：多路上报合并策略

> 多工位模式下 `station_id` 默认拼后缀：`f"{station_id}-{ch}"`（`mes_hooks.py:539`），
> 配置 `expected_stations` 要带 `-0/-1`；可用 `channel_station_map` 显式覆盖。

---

## 八、外部设备（external_device.py + 3 mixin）

### 8.1 类组合
```python
class ExternalDeviceService(
    ExternalDeviceProtocolsMixin,   # serial / TCP / UDP / 串口 RTU/ASCII 协议循环
    ExternalDevicePipelineMixin,    # 解析 → 校验 → 称重稳态 → dispatch
    ExternalDeviceTestMixin,        # /test 连通性
):
```

> **v3.48 新协议 `modbus_pulse`（Modbus 完成脉冲，`external_device_pulse.py` 400 行）**：per_item 判定完成 → 给 PLC 线圈发脉冲（ON→pulse_ms→OFF，cooldown_ms 冷却窗丢重复）。触发入口 `notify_per_item_complete(channel_id, trigger_mode)`（推理线程只入队，Modbus 写在设备线程）；trigger_mode 二选一 `all_covered`/`cycle_ok`，与 per_item 侧同名匹配。诊断走 `debug-per-item` skill 第六节 #5；与 RFC 13 通用 PLC 连接器（`debug-plc`）互不相干——这是外设栏的专用轻量协议。

### 8.2 称重 idle/stabilizing/stable 状态机（`_handle_weight_stability`）

```
idle ──首次有效数据──> stabilizing
        |                  │
        │                  ├─连续 stable_count 次, max-min ≤ stable_delta
        │                  ▼
        │              stable (首次上报)
        │                  │
        │     值漂移 > delta → 回 stabilizing
        │     值 < zero_threshold → 回 idle
        ▼
    清空 _stable_samples / _no_barcode_alarm_fired
```

### 8.3 有重无码告警（`_check_weight_no_barcode_alarm`）

仅当 `device_role=="weight"` + `weight_no_barcode_alarm_enabled=True`：
- 稳定非零（`_stable_state=="stable"`）+ 无 barcode + 持续 `weight_no_barcode_alarm_delay_sec`
- 触发**一次性**告警：
  1. `gateway.dispatch("weight_no_barcode", {device_name, channel_id, value, elapsed_sec, ...})`
  2. `alarm_router.trigger_alarm("weight_no_barcode", channel_id=...)` 灯塔/蜂鸣
- 来码或值归零 → `_no_barcode_alarm_fired` 复位，下次还能再触发

### 8.4 `_open_serial_with_retry`（v2.7.8）

Windows `PermissionError(13)` 端口拒绝访问（`ser.close()` 释放有毫秒延迟 + 测试按钮 vs 连接线程并发）→ `max_retries=3, retry_delay=1.0`。`test_connection` 故意不重试避免 UI 卡顿。

### 8.5 ⚠ 两套"称重"别混淆（v3.31 起）

| | 外设层称重稳态（本节 8.2/8.3） | 原生称重投料引擎（v3.31） |
|---|---|---|
| 位置 | `external_device_pipeline.py: _handle_weight_stability` | `backend/services/weighing_engine.py` |
| 职责 | 读数稳定判定 + 有重无码告警 + dispatch | 配料防错业务状态机（去皮→投料→对比标准量→判定落库）|
| 归属 | 所有 `device_role=weight` 外设通用 | 仅 `logic_mode='weighing'` 项目，配置在 `pipeline_config.weighing` |
| 数据出口 | gateway dispatch / 插件 `external_device_data` 只读 hook | `weighing_records` 表 + `/api/v1/weighing/*` + Monitor WeighingPanel |

排查链路：秤读数不进来 → 查本节外设层（协议/串口/稳态机）；读数进来但投料判定不动 →
查引擎侧（通道是否登记：切项目后 `set_channel_config` 日志；型号/料别标准量是否配置）。
外设层还提供 `send_command`（串口指令应答协议，软件去皮 'T'/置零 'Z'）——引擎和插件
`send_device_command` API 都走它。mock_weight 无硬件模拟见 `tests/test_mock_weight_source.py`。

---

## 九、常见排查场景（按现象索引）

### 场景 A：MES Hook 不触发 / MES 数据全空

1. `main.py:_init_mes_services()` 是否执行？前端打开 Monitor 时 `mes` 字段是否始终空？
2. `mes_hook` 是否注入 VSM？grep `_mes_hook` in `backend/main.py` + `backend/api/source_*` 确认
3. **worker 线程活着吗？** `print` 看 `[MES] Hook 管理器已启动`；若 `_worker_thread.is_alive()=False` 是 stop 错误
4. `_task_queue.qsize() / _queue_drop_count / _queue_block_count` 看队列健康
5. `${DATA_DIR}/mes_hook_spool.jsonl` 是否在堆积（队列满落盘）
6. 检测确实经过了 `start_cycle/end_cycle`（不是只开了预览）
7. 看后端日志 `[MES]` 前缀：cycle_end 异常**默认带 traceback**（v2.7.9 后），看到 `AttributeError` 多半是 model 字段读法没用 getattr

### 场景 B：扫码后工件不绑 / 追溯断链

1. `[MES] _handle_scan` 日志：扫码器→Hook 链路通不通
2. `is_channel_scan_disabled(ch)`？v3.4.2 守门点会直接丢码 + 弹"工位禁用扫码"日志
3. `bind_timing`：`scan_pair` 模式下扫码 A 不会立刻进 `_pending`，而是先入 `_scan_pair_active`，等 B 才结算
4. `late_scan_bind_window_sec`：cycle 已结但扫码晚到 ≤N 秒可补绑
5. `auto_create_workpiece=False` + 扫到的 serial DB 里没 → 直接拒绝
6. `dedup_interval_sec`（默认 2 秒）+ `ok_rescan_cooldown_sec`（同码冷却）静默丢弃
7. 多通道：单通道模式 `data.mes` 必须赋到 `multiChannelData[0].mes`，否则前端不显示

### 场景 C：扫码器连接失败

| 症状 | 排查 |
|---|---|
| LON 连接拒绝 | `POST /scanner/devices/test` 看后端日志；端口 55256 通；防火墙 |
| WMax 三端口部分失败 | 查日志 `IMG 55276 连接失败 (仅CMD模式)` / `RPT 55286 连接失败`；CMD 必通，IMG/RPT 可降级；检查相机 SDK 是否被占用 |
| WMax 自动发现没结果 | `_auto_discover_wmax_bg` 仅设备 `device_type ∈ (auto, wmax, wmax_scan)` 触发；UDP 广播被路由器拦截 |
| `/source/detection/stop` 卡 20s | v3.0 修过：`trigger_off` 不为停止动作同步重连；若回归先检查 `_wmax_trigger` 里 `cause` 是否带 `"stop"`/`"trigger_off"` |
| 离线设备日志刷屏 | 期望行为是单行短提示，未知异常才打 traceback |

### 场景 D：Modbus RTU 不响应

1. `transport=rtu` → 检查 port/baudrate/parity/stop_bits 与 PLC 一致
2. `transport=tcp` → 检查 host/port、PLC 是否启用 Modbus TCP
3. `slave_id` 1-247 与 PLC 配置一致
4. 寄存器地址：UI 上配 `40001` 对应 0-based `0`（pymodbus 接受 0-based）
5. pymodbus 版本：`_slave_kwarg()` 自动检测 `slave` vs `device_id`（pymodbus 3.x ABI 切换）
6. 与报警灯**共用串口**：`_serial_lock` 互斥；长写入会让灯响应变慢
7. `data_type=uint16/uint32/float`：检查 `byte_order` 大小端是否反

### 场景 E：MES Gateway 推送失败

1. `MESCommLog`：`/mes/gateway/logs?connection_id=X` 看 `status_code` + `error_msg`
2. 前端 `GatewayPanel.vue` 点"测试"：和真实推送一致鉴权+映射，但**绕过 `push_on_result`**（看不到推不出去）
3. 鉴权丢失：`bearer/api_key` 改 headers 后被 adapter `requests(headers=...)` 整个替换 → 检查 adapter 是否合并而非覆盖
4. `label_mapping` 不生效：`ng_items` 必须是 `list[str]`；中文步骤名空格要严格一致
5. `/test` 200 但实际推送失败 → 多半是 `push_on_result` 误配
6. 模板渲染异常：检查 `{key.path}` 占位符的 namespace 是否在 context 里（`order.` / `cycle.` / `extra.` / `aggregated.`）
7. 集群超时分支漏 `MISSING-` → 检查 `_push_timeout_result` 是否被改坏

### 场景 F：集群 box 永远 pending / 工单计件 = 0

| 检查 | 命令 |
|---|---|
| 副机心跳通 | 副机 `GET /cluster/health` + 主机 `GET /cluster/slaves` |
| `expected_stations` 配置对 | 含 station_id 后缀（多通道 `-0/-1`），见模块 7 |
| `binding_scope` 红色"未绑定" | 前端 OrderPanel 列表"绑定"列；`project AND project_id IS NULL` |
| 工单状态 `in_progress` | `get_active_order` / `find_cluster_orders` 都按此过滤 |
| `is_recovery=True` 跳过计件 | 看后端日志，校正重推不 +1 是预期 |
| 后端日志 `UNIQUE constraint failed` | per-box 锁失效；检查 `_acquire_box_lock`/`IntegrityError` 兜底 |
| `database is locked` 30s+ | `_handle_cycle_end` 必须先 `db.commit()` 释放写锁再调 cluster |
| 调试接口 | `POST /api/v1/debug/test_cluster_flow` 端到端跑一遍 |

### 场景 G：缺陷分类不正确

1. `DefectCode` 表里有该步骤标签的 `label_mapping` 吗？
2. `defect.auto_record_from_cycle()` 用 step.label 查 DefectCode；查不到 → 入库 "未分类"
3. 前端面板：DefectPanel.vue → 维护 `category/severity`
4. 手动录入：`POST /mes/defects`

### 场景 H：Monitor 未绑码警告异常

| 现象 | 原因 |
|---|---|
| 没装扫码器还在弹"⚠ 未绑码" | 升级到 v3.5.x；`has_any_scanner_present()=False` 应屏蔽 |
| 工位刚切禁用还在弹 | `_disabled_channels` 守门点；前端 `useScannerDisableStore` 是否同步刷新 |
| 装了扫码器但 `warn_no_barcode=False` | `scanner_devices.warn_no_barcode` 字段没勾上 |
| Toast 不响 | `useSystemStore.detection.toasts.warn_no_barcode.enabled`；`detection_config` 不能是 null |
| 单通道 mes 字段空 | `Monitor/index.vue:processChannelResult` 是否把 `data.mes` 赋到 `multiChannelData[0].mes` |

### 场景 I：scan_pair 模式异常

1. `bind_timing="scan_pair"` 必填；其他 `bind_timing` 不会进 `_handle_scan_pair_event`
2. `_scan_pair_active[ch]` 应在扫到第一个 A 后立刻有值；没有 → 多半 `is_channel_scan_disabled` 守门
3. `scan_pair_max_wait_sec=0` → 永不超时（适合工人节拍稳定）；> 0 → 到时强制 NG 结算
4. source.py 必须有 `settle_for_scan_pair(channel_id, *, force_ng, reason)`，否则只打日志静默
5. `broadcast_channels` 联动：所有 broadcast ch 一起替换/结算（`_scan_pair_resolve_broadcast_channels`）
6. 同码二次扫只触发 toast (`_scan_pair_emit_dup_toast`)，不动状态机

### 场景 J：有重无码告警不响

1. `weight_no_barcode_alarm_enabled=True`？`device_role=="weight"`？
2. 看 `_stable_state`：必须先 `stable` 才进入告警判定（首次稳定无码起算 `_weight_onset_time`）
3. `weight_no_barcode_alarm_delay_sec` 默认是多少（5? 10?）—— 现场感觉"不响"多半是没到时
4. `_no_barcode_alarm_fired=True`：本轮已响过，必须值归零或来码才会复位
5. 报警灯不亮：`alarm_router.trigger_alarm("weight_no_barcode", channel_id=...)` 是否能在 alarm_router 里查到对应规则（看 `Alarm/index.vue` 配置）
6. `gateway.dispatch("weight_no_barcode", ...)` 推送到外部 MES：连接的 `push_events` 必须包含 `weight_no_barcode`

---

## 十、调试入口

### 10.1 无硬件模拟

| 端点 | body | 用途 |
|---|---|---|
| `POST /api/v1/scanner/simulate` | `{barcode, channel_id, external_only?, pairing_group?}` | 不连真实扫码器跑通 Hook 链路 |
| `POST /api/v1/external-devices/simulate` | `{raw_data, device_id?, barcode?, channel_id, full_chain, repeat_count}` | `full_chain=false` 只写日志；`true` 走真实 extra/cluster 分发 |
| `POST /api/v1/mes/gateway/connections/{id}/test` | `{event_type: cycle_end\|box_complete\|box_timeout}` | 走和真实推送一致的鉴权+映射；故意绕过 `push_on_result` |
| `POST /api/v1/mes/gateway/connections/{id}/push` | `{event_type, context}` | 手动构造 context 推送 |
| `POST /api/v1/debug/test_cluster_flow` | `{cycle_id}` | 端到端跑 build_context → receive_station_report，返回 steps 指明哪步挂了 |

### 10.2 实测观察

```bash
# Hook worker 是否活着
curl http://localhost:8001/api/v1/debug/   # 通道诊断

# 队列健康
grep "MES Hook" backend.log | tail -50

# 通讯日志
curl 'http://localhost:8001/api/v1/mes/gateway/logs?connection_id=1&limit=50'

# 集群箱状态
curl http://localhost:8001/api/v1/cluster/boxes
curl http://localhost:8001/api/v1/cluster/slaves
```

### 10.3 端到端自测脚本

`tools/test_mes_gateway.py`（如果存在）：本机起 5 个 mock MES，10 个场景全绿才算后端链路 OK。

`tools/test_workorder_binding.py`（如果存在）：独立 SQLite + Mock MES gateway，binding_scope 32 用例。

---

## 十一、已知限制 / 永远的坑

1. **SQLite WAL + busy_timeout=15s**（`backend/db/database.py`）+ cluster 写路径必须用 `_commit_with_retry`（指数退避，最多 6 次累计 ~4.2s）。新写 cluster 写入忘了用 → 现场 `database is locked` 30s
2. **scan dedup_interval_sec 默认 2 秒**：快速连续扫码会丢码（同码二次扫可由 `ok_rescan_cooldown_sec` 进一步抑制）
3. **缺陷自动分类**依赖 `DefectCode.label_mapping` 正确配置，否则全部"未分类"
4. **Modbus 与报警灯共用串口**：`_serial_lock` 互斥，长写入延迟报警响应
5. **mes_hooks.py 历史曾用 `except Exception: pass` 静默吞错** —— v2.7.9 全改 `traceback.format_exc()`，**新写 except 默认带 traceback**
6. **`MESConnection.adapter_type` 必须 ∈ 6 个注册名**（rest / form-data / form-urlencoded / query-string / modbus_rtu / database），否则 dispatch 进 `_REGISTRY` lookup 抛 ValueError 写 log 不抛错
7. **build_context 字段加新名**：必走 `getattr(x, 'new', None) or getattr(x, 'old', None)`；硬读会让 cycle_end 整段静默
8. **import 路径**：`SessionLocal` 在 `backend.db.database`（**不是** `backend.database`）；`scanner` 在 `backend.services.scanner`
9. **静默删除集群 BoxAggregation**：`/cluster/box/{barcode}` 删完同条码再扫被视为新条码，对应 `Workpiece` 不会被这两个 API 触及
10. **WMax `trigger_off` 不为停止动作同步重连**（v3.0 修）：再加同步重连会让 `/source/detection/stop` 卡 20s
11. **`channel_manager.set_channel_count` 必须**调 `mes_hook.on_channel_removed(ch)` + `alarm_router.on_channel_removed(ch)`（AGENTS.md 不变量 #4），否则 `_active_orders` / `_pending_workpiece` / `_inspecting_workpiece` 残留

---

## 十二、关键文件速查

```
后端
├── api/
│   ├── scanner.py (532)      /scanner/* 14 endpoint (含 scan-pair/, disable-toggle)
│   ├── wmax.py (672)         /scanner/wmax/* 35 endpoint
│   ├── mes.py (673)          /mes/* 26 endpoint (orders/workpieces/defects/defect-codes)
│   ├── mes_gateway.py (450)  /mes/gateway/* connections/test/push/extra-fields/logs
│   ├── cluster.py (295)      /cluster/* config/report/heartbeat/slaves/boxes/health
│   └── external_device.py (365)
├── services/
│   ├── mes_hooks.py (1505)   ★ MESHookManager 单例 + worker 线程
│   ├── scanner.py (1964)     ★ ScannerService + LON listener + WMax 联动
│   ├── mes_gateway.py (432)  MESGateway + dispatch + auth + build_context
│   ├── cluster_collector.py (1122)  host 汇总 + 心跳 + 超时 + per-box 锁
│   ├── work_order.py (357)   binding_scope (project/channels/cluster)
│   ├── workpiece.py (235)
│   ├── defect.py (243)
│   ├── external_device.py (~390)        三 mixin 拼装
│   ├── external_device_pipeline.py (~430)  称重状态机 + 有重无码告警
│   ├── mes_adapters/
│   │   ├── __init__.py        _REGISTRY: rest/form-data/form-urlencoded/query-string/modbus_rtu/database
│   │   ├── base.py (79)       自研 {key.path} 模板引擎 (不是 Jinja2!)
│   │   ├── rest_adapter.py / form_data_adapter.py / form_urlencoded_adapter.py
│   │   ├── query_string_adapter.py / modbus_adapter.py
│   └── wmax/                  device.py / manager.py / discovery.py / messages.py
├── models/
│   └── mes_models.py (576)   ★ 15 张表
└── main.py                   _init_mes_services + 60+ ALTER TABLE migration

前端
├── views/MES/
│   ├── OrderPanel.vue / WorkpiecePanel.vue / DefectPanel.vue
│   ├── ScannerPanel.vue (1381) / WMaxPanel.vue
│   ├── GatewayPanel.vue (1073) / ClusterPanel.vue / ExternalDevicePanel.vue
└── api/{mes,scanner,gateway,cluster,external_device}.js
```

---

## 第 14 节：外部 MES 工单主动拉取（v3.20.0+）

**做什么**：与 MES Gateway 推送相反方向——**主动去外部 MES 查询工单**回填本地。通用可配置，已对接上银 HIWIN（工单数组埋在 `response.resultData` 两层嵌套）。

**关键文件**：
- `backend/services/mes_puller.py` — `MESPuller`：`test_connection`（自动识别结构）/ `_pull_with_config`（核心流程）/ `_http_request`（发请求+重试+复用网关鉴权）/ `_extract_array` / `_map_fields` / upsert
- `backend/api/mes_gateway.py` — `/mes/gateway/pull-test`、`/mes/gateway/connections/{id}/pull`
- `backend/main.py` — `PullScheduler` 后台守护线程（定时同步）
- `frontend/src/views/MES/OrderPullPanel.vue` — 配置面板（小白点选 + 高级参数）

**配置位**（存在 `MESConnection.config.pull`）：`url / method / request_body_template（{job_no} 占位）/ success_path / success_value / array_path / field_mapping / import_mode(upsert) / retry_count / triggers(manual/scheduled+interval)`。

**常见故障**：
- 拉取失败只看到「HTTP 500」无详情 → v3.20.0 已修：非 2xx 时通用探测返回体 `error.errorInfo`/`message`/`msg`/`detail` 拼进错误信息。若仍无详情，看外部 MES 返回体结构是否用了非常见错误字段名
- 拉到数据但工单数为 0 → `array_path` 没对准（上银是 `response.resultData` 两层）。用 pull-test 看 `structure_guess.array_path` 自动识别值
- 字段全空 → `field_mapping` 的源字段名与返回体不符。pull-test 的 `structure_guess.fields` 给候选
- **报「外部MES返回非成功（statusCode=404）:请求的服务器不存在」→ 不是网络问题**（v3.23.2 现场）：上银这类 MES 是「单网关地址 + 请求体 `api` 字段选服务」结构，目标服务由 `request_body_template` 里的 `api` 值决定。能拿到带 `statusCode` 字段的返回体 = HTTP 已通（这个 `statusCode` 是**返回体业务码不是 HTTP 码**，代码 `success_path` 读的是 body 字段）。404「请求的服务器不存在」= `api` 值错（网关路由不到服务），多为**路径写错或带了空格**。注意上银官方文档示例本身有笔误：一处 `ai_error_prevention_job_info`（下划线）、另一处 `ai_error prevention_job_info`（空格），正确是全下划线。v3.23.2 前前端「上银 HIWIN」一键预设照抄了空格版 → 谁点一键导入谁中招；mock/UAT 都直填下划线串故测试盲区从未暴露。**另：工单不存在 ≠ 报错**，上银查无单返回 `statusCode=200` + `resultData=[]` 空数组，所以 404 绝不是「工单查不到」
- 排障开调试中心 `backend.pull` 开关（拉取发起/HTTP/解析/入库每步）+ 前端 `mes.pull`；pull-test 弹框回显实际发出的请求体 + 对方返回体片段，拿去对客户 API 文档

## 第 15 节：USB 键盘扫码枪（v3.20.0+）

**做什么**：USB 键盘式扫码枪（NT-1202W 等）即插即用。**做成扫码器设备的一种**（`device_type=usb_hid`），不是独立子系统。

**架构关键**：
- 插上即键盘 → **前端全局键盘捕获**（`frontend/src/composables/useScanGun.js`），速度启发式（字符间隔 `CHAR_GAP_MS` / 段最大时长 `SEG_MAX_MS` / 最小长度 `MIN_LEN`）区分扫码枪与手输
- 按用途路由：`pull`（扫码拉工单，调 pullOrders）/ `bind`（扫码绑工件，调 `POST /scanner/simulate` 注入 scan_pair 绑定）/ `both`（按 order_pattern 正则判断）
- **后端 `usb_hid` 设备不建网络连接**（`scanner.py:_start_device` 最前 return）、**不参与 IP:Port 唯一校验**（`api/scanner.py` create/update 跳过，否则多把枪撞 `:0`），仅作设备表记录供前端读
- 配置归入扫码器面板（`UsbScanGunDialog.vue`），不是独立 Tab；设备卡片隐藏网络枪的「测试/高级控制」，改「测试扫码」按用途真跑一次

**常见故障**：
- 扫码无反应 → 前端键盘监听是否启动（`layout/index.vue` 调 `startScanGun`）+ 设备表是否有启用的 usb_hid 设备（`useScanGun` 从设备表拉配置缓存）
- 手输被当扫码 → 调速度启发式阈值
- 多把枪只保存一把/报「地址 :0 已被占用」→ 确认 create/update 跳过了 usb_hid 地址校验
- 排障开 `mes.scanner` 调试分类（扫到码/路由决策/拉取或绑定结果）

---

## 十六、包装箱结算协调器（v3.21+ / v3.22 滑块口径 / v3.23 现场处置）

**做什么**：上银等包装线「工单 → 箱 → 滑块/托盘」三层结算。扫工单查 MES 拿滑块总数+规格 →
算箱数（含尾箱）→ 逐检测周期按进箱数判满 → 收尾回推 MES。单例，per channel/config。

**关键文件**：`backend/services/packaging_flow_coordinator.py`（状态机）+ `backend/api/packaging_flows.py`
（CRUD + 扫码 + 处置端点）+ `backend/models/mes_models.py: PackagingFlowConfig / PackagingFlowRun` +
`frontend/src/views/MES/PackagingFlowPanel.vue`（配置，信息架构重构自 Settings/ 迁入，入口=MES 管理页「包装结算」tab）+ `frontend/src/views/Monitor/PackagingFlowCard.vue`（监控卡）。

**两种计数口径**：`count_unit='trays'`（v3.21 原行为）/ `'sliders'`（v3.22，一个检测周期=一个箱，
进箱滑块数由检测层 `slider_count` 带入）。`get_state(config_id)` 直接返回内存 run dict，run 里加什么字段前端就能看到什么。

**run.status 状态**：`order_loaded` / `running` / `pending_remediation`（v3.23 少装挂起）/ `completed` / `aborted`。

### v3.23 现场处置三件套

| 能力 | 入口 / 权限 | 行为 | 关键点 |
|---|---|---|---|
| 强制结案 | `POST /packaging-flows/{id}/force-settle`，权限 `system.packaging_flow.force_settle`（admin/engineer，操作员 403）| `force_settle_manual` 强制走 settle 收尾（忽略配置 keep/abort），必填理由 | `forced_reason`/`forced_by` 落 `PackagingFlowRun` 审计 |
| 待机不结算 | 配置 `forced_settle_on_standby`（默认开）| 关掉后 `on_forced_settle_by_channel(is_standby=True)` 直接 return，工单保留可恢复 | 停止（非待机）才收尾 |
| 缺油嘴 gate | 配置 `oil_nozzle_required`+`oil_nozzle_step_label`+`event_missing_nozzle`| 每箱结算前 `_probe_oil_nozzle` 判放油嘴步骤是否 covered，未过暂不收尾+报警 | 未配置/无探测器退化放行（每箱 gate 误卡会卡死线）|

### v3.43 尾箱塞工单 gate 语义重构：箱归周期结算、放工单归工单收尾（替代 v3.34.1 挂起快照）

- **与老行为并存**：子开关 `tail_paper_as_close_action`（默认**关**=老行为零差异，仅 `tail_paper_order_required` 开时生效；上银一键预设开）。关 = 老行为：拦下只报警等着，下个周期结算按那个周期自己的数据重走 gate。开 = 下述工单收尾语义（v3.43 起；v3.34.1 的 `pending_paper_box` 挂起快照与 `_close_pending_paper` 已删）。
- **v3.43 语义（子开关开）**：尾箱在周期结算时**照常按自身成绩当场落账**（gate 不拦不报警）；各箱全落账但放工单没探到 → run 挂 `awaiting_paper`（status=awaiting_paper，监控卡横幅），**只差放工单动作完成工单**。出口：
  1. 检测层探到序列外"放工单"步骤 → `on_step_detected` 即时完成工单（不必等扫码/周期结算）；
  2. 后续周期结算/扫码时探到放工单 → 工单正常收尾；
  3. **缺工单判定二选一**（`_paper_judge_mode`，m0003 两列）：`tail_paper_scan_alarm=True`（默认）= 扫新单判定——下一单扫码发现没放 → 报警 + 旧单**工单层**判 NG 收尾（箱成绩不回改）再开新单；`scan_alarm=False 且 timeout_s>0` = 时限判定——挂等待态时布防一次性 `threading.Timer`，到点终局判定（最后探一次），时限内扫新单被**拒收**提示稍候；
  4. 管理员强制结案 → 豁免放工单，工单按各箱成绩收尾。
- 同号重扫且仍没放 → 只提醒继续等。重启后 awaiting_paper 一并作废（内存 Timer 已丢，防幽灵在途）。核心逻辑 `_close_awaiting_paper`；测试见 `test_packaging_flow_coordinator.py`（91 例含等待态/两判定/Timer）+ `packaging_flow_sliders.feature`。
- **v3.43.1 两个连带守门**（真实事故：确认框闪退）：扫新单判定路径的缺工单报警**延后**到开新单（含按规格切项目）之后触发（`defer_alarm`）；`_real_project_activator` 命中已激活项目原地不动不重载模型。
- **配套（v3.42.1/v3.43.1）**：已完成(OK)工单重扫拦截 `block_completed_order_rescan`（on_scan 单点守门，NG 单不拦，m0002）；提前放工单报警 `early_paper`（非尾箱/空尾箱期间出现放工单动作每箱一次提醒，进箱数探测钩子 `set_box_progress_getter` 读容器已结算件数，拿不到保守放行）。
- **gate 探测数据源（v3.42.1 修复）**：顺序型下"放工单"是序列外步骤进不了周期步骤集——`is_packaging_paper_order_covered` 扩查四处（周期两代 + `_oos_steps_seen` 旁路账本两代，账本随周期轮转、停止/切项目全清），否则 gate 永不放行。

### v3.45 箱标签扫码授权（组⑧）+ 包装工单同步进工单管理

- **箱标签扫码授权**（全可选默认关零差异，迁移 m0004 共 9 列）：开 `box_label_scan_required` 后每箱（含首箱）进「等扫箱标签」态，扫到本工单标签才放行开做；未扫就开做 → 报警（检测层周期开始通知钩子 `on_cycle_started`）。配套：`label_qty_enabled` 从复合串取"本箱数量"当本箱视觉目标（段号 `label_qty_segment` / 正则 `label_qty_pattern`，取不出 → 拒收报警请重扫二维码，裸一维条码同）；已授权重扫同号 `label_rescan_action`（ignore/update，update=贴错重贴场景用新数量更新目标）；未授权箱做完整周期 `unauthorized_cycle_action`（hold=挂账等人工/book=报警照记）；收尾对账 `label_total_check`（Σ标签数量 vs 排产量，不平只报警留痕不改成绩）。三类报警事件字段 `event_box_not_scanned` / `event_label_qty_missing` / `event_label_total_mismatch`，全走既有事件响应面。重做未授权箱回到「等扫箱标签」态；等扫态是内存态，重启一并作废。本箱目标取值优先级：标签取到的数量 > 工单级每箱数/尾数计划（`_current_box_target`）。
- **包装工单同步进工单管理**（`sync_work_orders`，默认开，老库 NULL 视为开，迁移 m0005）：扫码开工/收尾/中止时 `_sync_work_order` 把包装单镜像到 `work_orders` 表（来源=packaging，工单管理页绿标签"包装扫码"）；重启作废的在途包装单镜像同步推"已取消"；**镜像失败隔离不阻断包装状态机**（只打日志）。排查"工单管理页看不到包装单" → 先看开关，再看后端日志镜像失败行。

### v3.23 NG 补做之「补滑块」（延迟落账）

- **触发**：仅当「检测步骤齐（is_good=True）+ 仅滑块数不足（sc<target）」且项目开了
  `pipeline_config.ng_remediation.{enabled,allow_count}` 时，本箱进 `pending_remediation` 挂起，
  **不立即记 NG、不推进 box_done**，只报警。多装 / 步骤不齐 / 没开策略 → 原行为立即判 NG（零差异）。
- **策略来源**：cycle_end 由 `source_session_lifecycle_mixin.py` 把 VSM 的 `_ng_remediation` 传进
  `on_cycle_settled(..., remediation=)`。
- **推进**：`POST /packaging-flows/{id}/supplement-sliders`（补滑块，target_count 空=自动补齐到目标，
  传值=手动指定封顶不超目标）/ `POST /packaging-flows/{id}/remediation-redo`（重做本箱，丢弃等下一周期重测），
  均需 `monitor.detection.ack` 权限。补做留痕写进 `box_details[].remediated`。挂起期间新周期被忽略。
- **常见故障**：
  - 少装没挂起立即 NG → 项目没开 ng_remediation 或没开 allow_count；或多装/步骤不齐（不在挂起条件内）。
  - 补滑块 409「没有等待补做的少装箱」→ run.status 不是 pending_remediation 或 pending_box 为空。
  - 监控卡看不到补做按钮 → 当前账号无 `monitor.detection.ack`（提示提权）。
  - 排障开 `backend.packaging` 调试分类（少装挂起 / 补滑块落账 / 强制结案）。

### v3.23 外部触发事件人工确认定格

外部触发（包装漏箱/多装/缺油嘴/缺工单经事件映射）的事件若在「项目事件设置」标 `require_ack=True`，
也进入检测层人工确认阻塞态（`_pending_ack`，整线定格），由 `source_event_trigger_mixin.py` 处理；
操作员可走 `ack-event-elevated`（`source_routes.py`）借管理员密码提权确认。默认 `require_ack=False` 零差异。

---

## 外部 MES/中控「主动推」型双向对接全闭环（v3.29.0，川南火工范式）

针对"中控来推开工、我们把报警与完工推回去"的双向场景，主程序原生可配，**全部默认关 = 老项目零差异**。一进一出两块面板：入站对接（`backend/api/mes_inbound.py` + `backend/services/mes_inbound.py`）、出站网关（`backend/services/mes_gateway.py`）。

**入站链路关键决策点**（`mes_inbound.py`，开 `backend.mes` 调试类别能看到全程人话日志）：
- `handle_task_start` → `_apply_task_action`：完工信号优先 → 拒绝重复(可选) → `_switch_project`(按产品代号切检测项目：先查对照表 `product_project_map`，再兜底"项目名==产品代号") → `_ensure_work_order`(建工单 + 四要素留痕到 `extra_data.inbound`) → `_supersede_previous_tasks`(最新开工顶替 + 对被顶替单回推完工) → `_auto_start_detection`(v3.37.0 可选，见下)
- **开工后自动开始检测**（v3.37.0，川南问题 1b；v3.39 契约升级）：配置键 `start_detection_on_task` 默认**关**。开时任务处理成功后逐工位拉起检测；任何失败只记 `backend.mes` 调试日志、绝不影响开工响应。**v3.39 两处升级（川南现场事故链逼出）**：① 门槛从"视频源在跑"放宽为"配置过视频源"——点过"停止"后源暂停的工位交给 start_detection 自带的暂停续播/相机重连复活路径（与手动点"开始"完全一致），只有"本次启动从没配置过源"才跳过；② `_auto_start_detection` 改返回 `(started, skipped)`，全部未拉起原因（未配置源/模型未就绪指路到项目默认模型/异常）附在开工成功响应文案后回给上游（业务码不变），一次请求即可自诊，不用开调试日志。回归：`tests/test_mes_inbound_unit.py` + `tests/test_alarm_manual_clear.py`。⚠️ **拉检测前必须先 `db.commit()` 本请求事务**——start_detection 内部另开 DB 会话写检测记录，与未提交的工单写事务互斥，会撞满 SQLite busy_timeout(15s)，上游中控超时短于它就误判"连接失败"（2026-07-13 UAT 逼出的真锁）。配套：前端界面 5s 轮询后端激活项目自动跟随（外部切项目后不刷新页面也能跟上，`Navbar.vue: syncActiveProjectFromBackend`）。回归：BDD 场景 15/16。
- `handle_alarm_clear`：按 `alarm_clear_match_fields`（默认四要素）匹配在途报警消除，匹配不到回 `alarm_not_found`(40007)
- 错误码：`disabled`/`missing_field`(40004)/`unknown_product`(40002)/`duplicate`(40001)/`alarm_not_found`(40007)，话术可配

**在途报警台账**（`backend/services/external_alarm.py` + 表 `external_active_alarms`）：
- `record_active_alarm`：出站成功推送报警后登记（出站侧 `mes_gateway._record_active_alarm_if_alarm` 调用）；`dedup_sec` 去重窗口
- `clear_alarms`：按唯一键匹配消除；`list_active_alarms`：监控页横幅轮询取未消除
- **软件内消除出口**（v3.39，川南反馈"上游不回推消除就永远挂着"）：两个出口都**默认关**（保持"只能外部消除"的对接契约），入站配置 `alarm_banner` 下按需开：① `allow_manual_clear` 开 → 横幅出"手动消除"按钮，走 `POST /mes/inbound/active-alarms/clear-manual`（配置关时 403 + 权限对齐监控页清零 `monitor.detection.advanced`）；② `clear_on_counter_reset` 开 → 监控页"清零"（reset-stats）顺带清全部在途报警。服务层入口 `external_alarm.clear_all_active_alarms`（与按键匹配的 `clear_alarms` 语义刻意分开，`clear_source` 留审计：manual_ui / counter_reset）。回归：`tests/test_alarm_manual_clear.py`
- 匹配维度：5 个原生列（task_no/product_code/step_code/operator/warning_text）+ 任意自定义维度（存 `extra_data.ext`，走 `json_extract`）——**去重口径与消除口径共用同一套 `match_fields`，必须一致**
- 健康探测：`backend/services/mes_health_probe.py`（`backend.gateway` 类别，仅在线↔离线翻转打日志）

**排查口诀**：
- 横幅"等待消除"撤不掉 → 查中控消除报文的匹配字段是否与登记时一致（开 `backend.mes` 看"报警消除未命中"）
- 报警重复登记 → 看去重窗口 `dedup_sec` 配没配 + 匹配字段是否稳定
- 开工不切项目 → 看"切项目未命中产品码"，确认项目名==产品代号或对照表
- 顶替没回推完工 → 看 `report_complete_on_supersede` + 出站完工连接是否建

**规格/产品码 → 检测项目 统一匹配器（v3.30.0+，入站与上银包装共用）**：
- 单一入口 `backend/services/project_match.py: resolve_project_id_by_spec(db, spec, mapping, match_by_name, strict_boundary)`，取项目三级兜底：
  1. **对照表精确**：`mapping[spec]`（入站 = `product_project_map`，包装 = `spec_to_project`）
  2. **对照表通配符**：键含 `*`/`?` 走 `fnmatch`，取最长键（最具体）→ 吃下前缀变 `*-X` / 后缀变 `X-*` / 中间固定 `*X*` / 任意或无分隔符
  3. **自动同名子串**：`match_by_name=True` 时，项目名是 spec 的子串即命中，取最长项目名（不误吞短名）；`strict_boundary=True` 要求命中处贴串首/尾或分隔符（防 HG 吞 HGH20，但无分隔符场景会落空）
- 两个消费方：入站 `mes_inbound._switch_project`、包装 `packaging_flow_coordinator._real_project_activator`，**口径完全一致**（后期合并就靠这层）
- ⚠️ `match_by_name` / `strict_boundary` 默认全 **关**（存量客户零差异，要的人显式开）。包装侧落 `packaging_flow_configs.name_match_strict_boundary` 列；入站存 SystemConfig JSON
- 排查"规格切不对项目"：先确认开关是否打开 → 看日志"切项目命中/未命中"里的"经XX"（对照表/通配符对照表/项目名精确/项目名子串）判断走了哪级；回归看 `tests/test_project_match.py` + `tests/step_defs/test_spec_project_switch.py` + `tests/test_e2e_spec_project_switch.py`

> 完整 BDD（v3.38 起 17 场景，含运行中开工回填 17-19）：`tests/features/chuannan_mes_integration.feature`；可见浏览器全链路 UAT + 模拟中控：`tests/uat/uat_20260627_chuannan_full_loop.py` + `tests/uat/mock_chuannan_mcs.py`。

---

## v3.38.0 补充：网关熔断 / 运行中开工回填 / 扫码器旁路 SN

- **网关推送熔断器**（`services/mes_gateway.py`）：同一连接连续失败跳闸→冷却期跳过→半开试探恢复；"推送一直没到 MES"先查熔断状态（`get_circuit_state`），改连接配置或测试连接会自动复位。dispatch 已改每连接推完即 commit——别再把多连接推送包回一个大事务。
- **运行中开工回填**（`mes_hooks.on_external_order_changed` + `mes_inbound._rebind_running_channels`）：检测运行中收到外部开工，新工单回绑运行工位（在途工件不打断）、四要素上屏。"开工了监控页工单没换"先查这条链。
- **扫码器旁路 SN 监控**（`services/scanner_bypass_monitor.py`，dev-qing）：客户扫码器只往目录写 SN txt 时，后台线程定期扫描实时导出规则的输入目录缓存 SN；cycle_start 快照内存优先、glob 兜底；状态查询 `GET /api/v1/export/scanner-bypass/status`；监控页显示开关 showBypassSn 默认关。

---

## v3.41.0 补充：复用工单重绑 / 周期结果过滤 / 网关事件下拉 / 模拟秤墙钟

川南现场用 Postman + 推送记录截图钉死的两个 bug（回归：`tests/test_cn_fixes_20260717.py` 8 例；可见浏览器 UAT：`tests/uat/uat_20260717_cn_order_reuse_null.py`）：

- **同任务号重复开工必须刷新复用单的绑定**（`mes_inbound._refresh_reused_order`）：客户测试习惯是固定任务号反复开工——第二次起走"复用已有工单"分支，老代码只打日志不动工单，项目绑定停留在第一次创建时（甚至为空）→ 工位挂单按"工单绑定项目==工位当前项目"匹配永远落空 → 监控页四要素不显示 + 出站推送里工单字段全 null。修复按"最新开工为准"：复用时重绑当前激活项目（工位路由则重绑本次报文工位）+ 覆盖 `extra_data.inbound` 四要素留痕 + 产品码/操作员。终态单复活仍归 `_ensure_in_progress` 管；刷新失败只记日志不阻断开工。⚠️ JSON 列要**整体重赋值**（原地改 dict 不触发 SQLAlchemy 变更追踪）。"开工了但信息条不显示 / 推送字段 null"先想这条 + v3.38 的运行中回填链。
- **终态工单再开工必须有出路**（v3.42.0，07-17 现场钉死的第二层陷阱）：同号单被顶替/手工结案/完工报文收掉成 completed/cancelled 后再开工，老代码 `_ensure_in_progress`"终态不复活"+ 响应仍回"已就绪"= 静默死路（工位永远挂不上单、四要素不上屏、推送字段全 null）。v3.42 起配置键 `terminal_order_policy`：`revive`（默认）= 绕过状态机直接复活为 in_progress（开工报文是权威指令，视为同任务新一轮生产；UI 手工路径仍受状态机约束）、`reject` = 回 duplicate 码拒收提示换任务号。配套响应透明化：工单最终没进 in_progress 一律回"状态异常, 检测数据将不会计入该任务"，复活成功则文案追加"已从完结状态重新开工"。回归 `tests/test_cn_fixes_20260717.py`；UAT `tests/uat/uat_20260717_cn_order_reuse_null.py`（复刻"绑定过期+状态终态"双层叠加）。
- **`push_on_result` 周期事件结果在 `cycle.result`（嵌套）**：见第 5.3 节第 2 段——"报警连接勾了仅 NG 但合格周期也推出去（WarningText=顺序正确完成）"就是这条。
- **GatewayPanel 事件下拉补 `weighing_product_done`（称重成品结案）**：后端事件 v3.39 就有（`weighing_engine.py`，两阶段流水线正式结案专用），前端下拉一直没露出，配萍乡达梦直写用。⚠️ 手册勘误同款坑：两阶段流水线结案**推的是这个专用事件不是 cycle_end**，达梦连接订阅错事件一条都收不到（百斯特手册 v2.1 勘误的正是这条）。
- **模拟秤脚本按墙钟计时**（`external_device_protocols.py` mock 播放循环）：原按"睡眠次数×间隔"累计，不含 emit 处理耗时，长脚本越播越慢（实测 4 分钟漂约 20 秒），与视频时间轴对齐的仿真会整体错位。只影响模拟协议，真秤无关。全链路仿真剧本参考 `tests/uat/sim_bst_20260717.py`（真视频+真模型+秤脚本+达梦模拟）。

**改动 MES 子系统前必读**：本 skill 第 3/5/8/11 节 + AGENTS.md 第六节 6.2、第八节不变量 1/4/6。

---

## v3.44.0 补充：包装箱账挂起 + 收尾快照 + 秤串口延迟

- **NG 箱账挂起**（`packaging_flow_coordinator.py`）：NG 事件带「需人工确认」→ 箱账进 `pending_remediation(reason=ng_ack)` 不落账不翻页；确认弹窗二选一（认NG落账进下一箱 / 重做本箱不记NG），`resolve_channel_hold_on_ack` 由 ack 接口统一收口，超时自动确认按"重做"。与 v3.23 少装挂起共用状态位，**按 reason 分流**（前端包装卡已分横幅）。排"箱号翻早了/重做重错箱"先查这里。
- **工单收尾快照**：完成/作废的工单存 `_last_done`，`get_display_state` 供 UI 轮询返回快照直到新单顶掉；`get_state` 语义不变（在途才有值，扫码/结算判定用）。**别把内部判定改成 display 口径**——会把已收尾工单当在途。
- **秤串口延迟治本**（`external_device_protocols.py`）：读串口"有多少收多少"（`in_waiting`）+ 帧读取见帧尾立即交货（无分隔符 ~60ms 静默兜底）。现场再报"数值条慢 2 秒"先确认没人把 `ser.read(固定大块)` 改回去。回归：`tests/test_extdev_serial_latency.py`。

---

## v3.49.0 补充：外推并发派发 + scan_pair 新码先上屏（捷昌整改 WS1/WS3）

三个新开关全存 SystemConfig、默认开、即时生效，现场异常可一键回退不用回滚版本：

### 外推并发派发（WS1）

- **旧痛点**：网关外推在 hook 队列内联串行——一个慢/挂的客户 MES 连接堵住全部后续任务（含结算关键路径），"扫码后卡好几秒"的头号嫌疑
- **机制**（`mes_hooks.py` + `mes_gateway.py`）：开关 `mes_async_dispatch`（`GET/PUT /api/v1/mes/gateway/async-dispatch`）开时外推按**连接**甩独立执行器并发派发；单连接积压超上限落盘 **`gateway_spool.jsonl`** 恢复后补发；box_complete 集群汇总推送同样走异步
- **每连接重试预算** `retry_budget_sec`：存连接 **config JSON 内**（不是顶层字段！UAT 踩过），GatewayPanel 弹窗可编辑，0=不限预算
- 排查：外推没到客户 MES → 先看 spool 文件是否堆积（连接一直挂）；再看 `mes_comm_logs`。回归 `tests/test_mes_async_dispatch_b1b.py` + `tests/test_mes_gateway_dispatch_isolation.py`

### scan_pair 新码先上屏（WS3）

- **旧痛点**（捷昌第一工位）：旧序"先结算前一件（含慢 IO）→ 再顶替新码上屏"，前一件结算多慢新码就多久不上屏
- **新序**：新码到达**先顶替上屏 → 提交扫码状态 → 用显式 `prev_wp_id`/`prev_scanned_at` 异步结算旧窗口**。结算范围用显式身份钳制——顶替之后 `_inspecting_workpiece` 已是新工件，绝不能再从它取"上一件"身份（这是新序最大陷阱）
- 开关 `scan_pair_new_code_first`（`GET/PUT /api/v1/scanner/scan-pair/new-code-first`，Settings 显示设置页有 UI），关=回退旧序
- **广播兄弟通道结算串身份修复**（同批 BUG-001）：广播结算身份改 `_inspecting_workpiece.get(channel_id) or entry.get("wp_id")`——老代码统一拿主通道 wp_id，兄弟通道超时/停止结算会记错工件账。改广播结算路径必须保住 per-channel 取身份
- 金标准回归：`tests/test_scan_pair_three_window_gold.py`（A→B→C 三窗连续 + 新旧序对照 + 超时收窗 + 双通道广播 + 重复扫码/停止丢弃六剧本）+ `tests/test_scan_pair_new_first.py`

### 结算耗时埋点（WS4）

- `debug_center` 新增 **`backend.timing`** 类目：扫码处理/窗口结算/MES 外推分段耗时。现场"卡"不再靠猜——调试日志中心筛 `backend.timing` 直接看哪段吃掉的时间。回归 `tests/test_timing_probe_ws4.py`

## v3.51.5 补充：strict_ok_dedup"全删了还拒码"现场陷阱 + 详细拒码日志

**现场实录（捷昌 B 站 2026-08-15）**："MES 工单/追溯/扫码历史/集群记录全删了，为什么还去重拒码？"——根因不在去重逻辑：去重按 `(serial_no, project_id)` 查 `status='ok'` 的工件，而**前端追溯页默认"仅当前项目"过滤**，多工位下其它工位项目的 ok 工件被藏在列表外，"全选批量删除"删不到它们。v3.51.5 起多工位默认关闭该过滤（见 debug-frontend）。另注意**批量删除只删当前页**（默认 50 条/页），记录多要翻页删。

**详细拒码日志（v3.51.5，编译件随安装包生效）**：拒码行带全量依据——`workpiece#id、channel、project_id、登记时间、最后检测时间、解除方法（追溯页关过滤删工件 / 数据中心清理联动解封）`。排"为什么拒"直接搜 `strict_ok_dedup reject`，不用再对账 DB。

**广播"一拒一收"是设计行为**：去重按项目隔离，同码在 A 项目 ok、B 项目新建，广播枪下 A 工位拒 B 工位收，日志各自留痕。
