---
name: debug-mes
description: "诊断MES系统问题：工单/工件/缺陷/扫码器/Hook/Gateway/集群/外设。当 MES 相关功能异常（数据不流转、扫码不绑、外推失败、集群汇不齐、有重无码不报警等）时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__sequential-thinking, mcp__sentry"
---

# debug-mes: MES 子系统诊断（v3.5.x 主线）

> 阅前先看 `AGENTS.md` 第六节 6.2（模块 6/7/8/9）+ 第七节扩展点表 + 第八节不变量 1/4/6。
> 这份 skill 覆盖**内置 MES 业务**（工单/工件/缺陷）+ **扫码器**（LON/WMax/虚拟）
> + **外部 MES Gateway**（5 个适配器） + **集群汇总**（standalone/host/slave）
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
| `backend/services/mes_adapters/__init__.py` | 31 | `_REGISTRY` 注册 5 种适配器 |
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

> ⚠️ 旧版 SKILL/产品交接手册写"10 张"是过时数据。

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

**`_enqueue` 设计**：critical=True 拒绝丢弃（队列满则阻塞 `block=True, timeout`），非 critical 才丢；
丢弃 / 阻塞时增 `_queue_drop_count` / `_queue_block_count`，可经 `_spill_task` 落盘续命。

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
- `ok_rescan_cooldown_sec`（v2.7.12）：A 扫完 OK，距完成 < N 秒再扫到 A → 静默丢弃；NG 不受冷却
- `late_scan_bind_window_sec`（默认 3）：cycle 已结但晚到的扫码事件 ≤N 秒可补绑

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

## 五、外部 MES Gateway（`mes_gateway.py` + 5 适配器）

### 5.1 5 个适配器（注册名 → 类）

| `adapter_type` | 类 | 协议 | 典型场景 |
|---|---|---|---|
| `rest` | `RESTAdapter` | HTTP JSON | 现代 REST API |
| `form-data` | `FormDataAdapter` | `application/x-www-form-urlencoded`，整 payload 序列化进 `param` 字段 | 客户说"参数叫 param" |
| `form-urlencoded` | `FormUrlencodedAdapter` | 顶层键值平铺到 form 字段 | 客户说"字段分开不要打包" |
| `query-string` | `QueryStringAdapter` | URL query，body 空 | 客户说"参数放 URL 里" |
| `modbus_rtu` | `ModbusRTUAdapter` | RTU/TCP 写 Holding（pymodbus 3.13+） | PLC/工控 |

注册表：`backend/services/mes_adapters/__init__.py:_REGISTRY`。新增协议直接 `register_adapter(name, cls)`。

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
   不匹配 → 写 skip 日志，**不调 adapter**。`/test` 故意绕过此过滤
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
6. **`MESConnection.adapter_type` 必须 ∈ 5 个注册名**（rest / form-data / form-urlencoded / query-string / modbus_rtu），否则 dispatch 进 `_REGISTRY` lookup 抛 ValueError 写 log 不抛错
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
│   │   ├── __init__.py        _REGISTRY: rest/form-data/form-urlencoded/query-string/modbus_rtu
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
- 排障开调试中心 `backend.pull` 开关（拉取发起/HTTP/解析/入库每步）+ 前端 `mes.pull`

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
`frontend/src/views/Settings/PackagingFlowPanel.vue`（配置）+ `frontend/src/views/Monitor/PackagingFlowCard.vue`（监控卡）。

**两种计数口径**：`count_unit='trays'`（v3.21 原行为）/ `'sliders'`（v3.22，一个检测周期=一个箱，
进箱滑块数由检测层 `slider_count` 带入）。`get_state(config_id)` 直接返回内存 run dict，run 里加什么字段前端就能看到什么。

**run.status 状态**：`order_loaded` / `running` / `pending_remediation`（v3.23 少装挂起）/ `completed` / `aborted`。

### v3.23 现场处置三件套

| 能力 | 入口 / 权限 | 行为 | 关键点 |
|---|---|---|---|
| 强制结案 | `POST /packaging-flows/{id}/force-settle`，权限 `system.packaging_flow.force_settle`（admin/engineer，操作员 403）| `force_settle_manual` 强制走 settle 收尾（忽略配置 keep/abort），必填理由 | `forced_reason`/`forced_by` 落 `PackagingFlowRun` 审计 |
| 待机不结算 | 配置 `forced_settle_on_standby`（默认开）| 关掉后 `on_forced_settle_by_channel(is_standby=True)` 直接 return，工单保留可恢复 | 停止（非待机）才收尾 |
| 缺油嘴 gate | 配置 `oil_nozzle_required`+`oil_nozzle_step_label`+`event_missing_nozzle`| 每箱结算前 `_probe_oil_nozzle` 判放油嘴步骤是否 covered，未过暂不收尾+报警 | 未配置/无探测器退化放行（每箱 gate 误卡会卡死线）|

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

**改动 MES 子系统前必读**：本 skill 第 3/5/8/11 节 + AGENTS.md 第六节 6.2、第八节不变量 1/4/6。
