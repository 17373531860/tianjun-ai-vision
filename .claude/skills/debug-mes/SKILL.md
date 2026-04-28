---
name: debug-mes
description: "诊断MES系统问题：工单状态异常、工件追溯断链、缺陷分类不对、扫码器不连接、MES Hook不触发、MES数据不与检测同步。当MES相关功能异常时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# debug-mes: MES 系统诊断

你正在诊断天军AI视觉检测系统的 **内置MES模块** (v2.3.0+)。

用户问题: $ARGUMENTS

## MES 架构总览

```
                         ┌──────────────────────┐
                         │   Frontend (Vue3)    │
                         │  MES/index.vue (Tab) │
                         │  ├─ OrderPanel       │
                         │  ├─ WorkpiecePanel   │
                         │  ├─ DefectPanel      │
                         │  └─ ScannerPanel     │
                         │  api/mes.js (22端点)  │
                         │  api/scanner.js (8)   │
                         └────────┬─────────────┘
                                  │ HTTP
                         ┌────────▼─────────────┐
                         │  FastAPI Backend      │
                         │  api/mes.py (Router)  │
                         │  api/scanner.py       │
                         └────────┬─────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
    ┌─────────▼──────┐  ┌────────▼────────┐  ┌───────▼────────┐
    │ Service Layer  │  │ MES Hook Layer  │  │ Scanner Service│
    │ work_order.py  │  │ mes_hooks.py    │  │ scanner.py     │
    │ workpiece.py   │  │ (异步队列+独立  │  │ (TCP Socket    │
    │ defect.py      │  │  DB session)    │  │  VS600通讯)    │
    └────────┬───────┘  └────────┬────────┘  └───────┬────────┘
             │                   │                    │
    ┌────────▼───────────────────▼────────────────────▼────┐
    │              SQLite (WAL mode)                        │
    │  mes_models.py: WorkOrder, Batch, Workpiece,         │
    │  WorkpieceInspection, DefectRecord, DefectCode,      │
    │  ScannerDevice, ScanLog, MESConnection, MESCommLog   │
    └──────────────────────────────────────────────────────┘
```

## 文件地图

| 文件 | 职责 | 行数 |
|------|------|------|
| `backend/models/mes_models.py` | 10个ORM模型 | ~250 |
| `backend/services/work_order.py` | 工单CRUD + 状态机 + 产量统计 | ~200 |
| `backend/services/workpiece.py` | 工件生命周期 + 追溯 | ~180 |
| `backend/services/defect.py` | 缺陷记录 + 帕累托 + 缺陷代码 | ~150 |
| `backend/services/mes_hooks.py` | 检测引擎→MES集成层 | ~250 |
| `backend/services/scanner.py` | VS600 TCP通讯 + 自动重连 | ~200 |
| `backend/services/barcode_parser.py` | 条码解析 (直接/分隔/正则) | ~80 |
| `backend/api/mes.py` | 22个REST端点 | ~400 |
| `backend/api/scanner.py` | 8个REST端点 | ~150 |
| `frontend/src/api/mes.js` | 前端API封装 | ~100 |
| `frontend/src/api/scanner.js` | 前端API封装 | ~50 |
| `frontend/src/views/MES/*.vue` | 前端页面 (含 GatewayPanel) | ~1200+ |
| `backend/services/mes_gateway.py` | 外部 MES 网关 | — |
| `backend/services/mes_adapters/*` | 外部适配器 | — |
| `backend/api/mes_gateway.py` | Gateway REST API | — |
| `frontend/src/api/gateway.js` | Gateway 前端 API | — |

## MES Hook 集成点 (关键!)

`MESHookManager` 通过 `source.py` 的 `_mes_hook` 属性与检测引擎集成:

```
source.py                          mes_hooks.py
──────────                         ────────────
start_session() 后 db.commit()  → on_session_start(ch, session_id, project_id)
end_session()   后 db.commit()  → on_session_end(ch, session_id, stats)
start_cycle()   后 db.commit()  → on_cycle_start(ch, cycle_id, session_id)
end_cycle()     后 db.commit()  → on_cycle_end(ch, cycle_id, is_good, ...)
get_detection_results() 返回前  → get_current_workpiece() + get_active_order()
```

**设计特点:**
- 所有 Hook 通过 `_enqueue()` 放入异步队列，不阻塞检测线程
- MES Hook 使用独立 `SessionLocal()` DB session，不与检测引擎争锁
- 所有 Hook 调用在 `try/except` 中，MES 故障不会影响检测

## WorkOrder 状态机

```
draft → pending → in_progress → completed
  ↓       ↓          ↓    ↑
  └──→ cancelled ←───┘  paused
```

`VALID_TRANSITIONS` 定义在 `WorkOrder` 模型中，`WorkOrderService.change_status()` 强制检查。

## Workpiece 状态流

```
registered (扫码注册)
  → inspecting (开始检测)
    → passed (OK) / failed (NG)
      → rework (返工) → inspecting → ...
        → scrapped (报废)
```

## 常见问题诊断

### 1. MES Hook 不触发 / MES 数据为空
1. 检查 `main.py:_init_mes_services()` 是否正常执行
2. 确认 `_mes_hook` 已注入到 VideoSourceManager: 搜索 `_mes_hook` in `main.py`
3. 检查 MES Hook 线程是否存活: `mes_hooks.py` 的 `_worker_loop`
4. 查看日志中是否有 `[MES Hook]` 前缀的错误
5. 确认检测确实经过了 `start_cycle/end_cycle`（不是只开了预览）

### 2. 工单状态转换失败
1. 检查 `VALID_TRANSITIONS` 字典是否允许该转换
2. `completed`/`cancelled` 是终态，不可再转换
3. API 返回 400 + 具体错误信息

### 3. 工件追溯断链
1. 确认扫码器正常工作（ScannerPanel 显示绿色状态）
2. 检查 `on_scan_received()` 是否正确创建/查找 Workpiece
3. 确认 `on_cycle_end()` 创建了 `WorkpieceInspection` 记录
4. 手动查询: `GET /api/v1/mes/workpieces/{id}/trace`

### 4. 缺陷分类不正确
1. 检查 `DefectCode` 表是否有对应的缺陷代码
2. `defect.py:auto_record_from_cycle()` 基于检测标签映射到 DefectCode
3. 如果标签与缺陷代码不匹配 → 记录为 "未分类" 缺陷
4. 手动录入: `POST /api/v1/mes/defects`

### 5. 扫码器不连接
1. 确认 VS600 IP/端口可达: `POST /api/v1/scanner/devices/test`
2. 检查 scanner.py 的 TCP 连接日志
3. VS600 协议: 连接后发送 `LON\r` 开启连续扫描模式
4. 如果断线 → scanner.py 有自动重连逻辑（5秒间隔）
5. 确认防火墙未阻止 TCP 连接

### 6. Monitor MES 信息条不显示
1. 确认 `get_detection_results()` 返回了 `mes` 字段
2. 检查 `Monitor/index.vue` 的 `processChannelResult` 是否解析 `d.mes`
3. 确认 MES 有活跃工单 + 当前工件

### 7. Data 页 MES 工单筛选无效
1. 确认 `Data/index.vue` 的 `loadMesOrders()` 正常加载
2. 检查 `sessions.py` 的 session 查询是否支持 `order_id` 筛选

## 外部 MES 对接调试（MES Gateway / 适配器）

内置 MES 之外，系统提供 **外部 MES 适配器框架**：在周期结束、会话结束时由 `MESHookManager` 调用 `MESGateway.dispatch()`，经注册的适配器（REST/JSON、form-data 等）向第三方系统推送数据。前端在 MES 页的「外部对接」Tab（`GatewayPanel.vue`）配置连接、字段映射模板与通讯日志。

### 1. 网关推送失败排查（MESGateway.dispatch → 适配器 → 外部 MES）

1. **确认链路：** `mes_hooks.py` 中 `_handle_cycle_end` / `_handle_session_end` 末尾是否执行了 `gateway.dispatch(...)`（推送在 Hook 处理逻辑之后，与内置工单/工件写入独立）。
2. **服务层：** 阅读 `backend/services/mes_gateway.py`：`dispatch` 的分发逻辑、重试、超时、错误记录到 `MESCommLog` 的路径。
3. **适配器：** `backend/services/mes_adapters/rest_adapter.py`（REST/JSON）、`form_data_adapter.py`（multipart/form-data）是否按连接配置选中；`base.py` 中请求构建是否抛错。
4. **网络与对端：** 用 `POST /api/v1/mes/gateway/connections/{id}/test` 或面板上的「测试」验证 URL、鉴权、TLS；对端 4xx/5xx 会在日志中体现。
5. **连接是否启用：** `MESConnection` 记录是否启用、URL/方法/Body 模板是否与对端约定一致。

### 2. 字段映射模板语法错误

1. 模板由 `backend/services/mes_adapters/base.py` 的 `render_template`（及上下文构建）渲染；语法错误通常表现为推送前异常或渲染结果为空。
2. 检查连接配置中的 JSON Body / Headers / URL 占位符是否与 **上下文变量名** 一致（参考网关构建的 context，见 `mes_gateway.py`）。
3. 在「外部对接」面板中逐项核对映射；可先用手动推送 `POST .../connections/{id}/push` 对比预期负载。

### 3. 重试机制与通讯日志检查

1. **重试：** `mes_gateway.py` 内对失败请求的重试策略（次数、间隔）；连续失败时应在日志与 `MESCommLog` 中有记录。
2. **日志：** `GET /api/v1/mes/gateway/logs` 或面板中的通讯日志；对照时间戳与 `cycle_id`/`session_id` 是否对应一次真实 `dispatch`。
3. 区分 **适配器异常**（渲染/序列化）与 **HTTP 失败**（状态码、超时），前者多为配置问题，后者多为网络或对端。

### 4. 额外字段（extra_fields）不生效的排查

1. **模型与迁移：** `MESConnection.extra_fields_schema`（`mes_models.py`）存储 schema；`main.py` 需包含对应 migration，数据库列存在。
2. **Monitor 输入：** `Monitor/index.vue` 根据 `extraFieldsSchema` 渲染额外输入框；若 schema 为空或未从后端拉取，界面上不会出现字段。
3. **API：** `GET/POST /api/v1/mes/gateway/extra-fields`、`GET .../extra-fields-schema` 与前端 `gateway.js` 是否一致；保存后是否重新加载了 schema。
4. **推送上下文：** 确认 `mes_gateway` 构建 dispatch 上下文时把 Monitor 提交的额外字段合并进模板变量；若模板未引用对应键，对端仍「看不到」。

### 5. 外部 MES 相关文件清单

| 文件 | 职责 |
|------|------|
| `backend/services/mes_adapters/__init__.py` | 适配器注册表 |
| `backend/services/mes_adapters/base.py` | 基类、`render_template` |
| `backend/services/mes_adapters/rest_adapter.py` | REST/JSON 适配器 |
| `backend/services/mes_adapters/form_data_adapter.py` | form-data 适配器 |
| `backend/services/mes_gateway.py` | 网关：分发、重试、日志、上下文构建 |
| `backend/api/mes_gateway.py` | Gateway REST API |
| `backend/services/mes_hooks.py` | 周期/会话结束处调用 `gateway.dispatch` |
| `backend/models/mes_models.py` | `MESConnection.extra_fields_schema` 等 |
| `frontend/src/api/gateway.js` | Gateway API 封装 |
| `frontend/src/views/MES/GatewayPanel.vue` | 外部对接配置 UI |
| `frontend/src/views/MES/index.vue` | 「外部对接」Tab |
| `frontend/src/views/Monitor/index.vue` | 额外字段输入（extraFieldsSchema） |

## 关键文件
- `backend/models/mes_models.py` — 所有MES数据模型
- `backend/services/mes_hooks.py` — 检测引擎集成（最常出问题）
- `backend/services/scanner.py` — 扫码器通讯
- `backend/api/mes.py` — MES REST API
- `backend/api/scanner.py` — 扫码器 REST API
- `backend/api/source.py` — MES Hook 注入点（搜索 `_mes_hook`）
- `backend/main.py` — MES 服务初始化（搜索 `_init_mes_services`）、`mes_gateway_router` 注册与 `extra_fields_schema` migration
- `backend/services/mes_gateway.py`、`backend/api/mes_gateway.py` — 外部 MES 网关与 API
- `backend/services/mes_adapters/` — 外部适配器实现

## Modbus RTU/TCP 适配器 (v2.5.0+)

通过 RS485 串口或 TCP 网络将检测结果写入 Modbus Holding Register，供客户 PLC/MES 仪表读取。

### 架构
```
cycle_end → MESHookManager → MESGateway.dispatch()
  → ModbusRTUAdapter.build_payload() → 解析寄存器映射
  → ModbusRTUAdapter.send() → pymodbus 写寄存器
```

### 关键文件
| 文件 | 职责 |
|------|------|
| `backend/services/mes_adapters/modbus_adapter.py` | Modbus 适配器（RTU+TCP） |
| `backend/services/mes_adapters/__init__.py` | `_REGISTRY` 注册 `modbus_rtu` |
| `frontend/src/views/MES/GatewayPanel.vue` | Modbus 配置 UI |

### 可配置项
- `transport`: rtu / tcp
- `port`/`host`: 串口路径或 TCP 地址
- `baudrate`, `parity`, `stop_bits`, `data_bits`: 串口参数
- `slave_id`: 从站地址 (1-247)
- `ok_value`/`ng_value`: OK/NG 写入值（默认 1/2，可自定义）
- `byte_order`: 大端/小端
- `registers[]`: 寄存器映射数组，每项 {address, source, data_type, const_value}
  - source: result_code, ok_count, ng_count, total_count, cycle_id, duration_ms, inspection_count, const

### 诊断要点
- 串口连接失败：检查路径、波特率、驱动、权限
- 写入无响应：检查从站地址、寄存器地址（40001→0-based）
- pymodbus 版本兼容：`_slave_kwarg()` 自动检测 `slave` vs `device_id`
- 与报警灯共用串口：`_serial_lock` 互斥锁防止同时访问

## 扫码绑定系统 (v2.5.0+)

### 绑定流程
```
扫码器扫到码 → ScannerService.inject_scan_result()
  → MESHookManager._handle_scan()
    → WorkpieceService.register() → 创建/查找工件
    → _pending_workpiece[channel_id] = workpiece_id
  
start_cycle() → on_cycle_start()
  → _inspecting_workpiece = _pending_workpiece.pop()

end_cycle() → on_cycle_end()
  → WorkpieceInspection 记录绑定
  → _inspecting_workpiece 清空
```

### 关键配置
- `bind_timing`: mid_cycle（默认，中途扫码立即绑定当前周期）
- `rebind_mode`: rescan（默认）/ auto_rebind / manual
- `dedup_interval_sec`: 去重间隔

### 诊断：绑定不生效
1. 检查 `_handle_scan` 是否被调用（看 `[MES]` 日志）
2. 检查 import 路径：`from services.scanner` 而非 `from backend.services.scanner`
3. 单通道模式需确认 `data.mes` 赋值到 `multiChannelData[0].mes`

### 诊断：warn_no_barcode 不显示
1. `is_warn_no_barcode()` 依赖正确 import `services.scanner`
2. 前端 `Monitor/index.vue` 中 `mesData.warn_no_barcode` 需要 `multiChannelData[0].mes` 有值
3. `useSystemStore` 的 `detection.toasts.warn_no_barcode.enabled` 需为 true
4. `detection_config` 不能为 null（需深拷贝初始化）

## 已知限制
- MES 数据使用 SQLite 同库不同表，高并发写入时 WAL 模式的锁等待可能增加
- 外部 MES 通过 Gateway + 适配器推送；对端协议差异大时需正确选择适配器并维护模板与 `extra_fields_schema`
- 扫码器每次扫码结果有 2 秒去重窗口（`_last_scan_time`），快速连续扫码可能丢弃
- 缺陷自动分类依赖 DefectCode 表中 `label_mapping` 字段的正确配置
- Modbus 适配器与报警灯共用 RS485 串口时使用 `_serial_lock` 互斥，但长时间写入可能延迟报警响应
- `mes_hooks.py` 中 import 错误容易被 `except Exception: pass` 静默吞掉，新增代码务必加 print 输出异常

## v3.0 MES/设备调试入口与日志降噪

### 1. 无硬件调试入口
- 扫码器：`POST /api/v1/scanner/simulate`，前端 `ScannerPanel.vue` 开发者模式显示"模拟扫码"。
  - body: `{ barcode, channel_id, external_only, pairing_group }`
  - 用途：不连真实扫码器也能验证扫码日志、工件注册、MES Hook 绑定链路。
- 外部设备：`POST /api/v1/external-devices/simulate`，前端 `ExternalDevicePanel.vue` 开发者模式显示"模拟数据"。
  - body: `{ raw_data, device_id?, barcode?, channel_id, full_chain, repeat_count }`
  - `full_chain=false` 只写日志；`true` 走真实 extra_fields/cluster 分发链路。

### 2. 停止/待机不能被离线扫码器阻塞
- 现象：`/source/detection/standby` 和 `/stop` 卡约 20 秒。
- 根因：`ScannerService.stop_scanning()` 对离线 WMax/auto 设备执行 `trigger_off` 时同步重连，5 次失败约 20 秒。
- 规则：`trigger_on` 可自动重连；`trigger_off` 不为停止动作同步重连，离线则打印 `SKIPPED: 设备离线，不为停止动作同步重连`。

### 3. 离线设备日志
- 扫码器/外设离线、端口未开、连接拒绝属于预期现场状态，日志应是一行短提示并继续重试。
- 只有未知异常才打印 traceback。否则客户机日志会被 `ConnectionRefusedError` 堆栈淹没，掩盖真正错误。

## MES 推送 Context 字段清单（v2.7.5 更新）

`MESGateway.build_context_from_cycle()` 返回的 context dict：

```text
cycle.id / cycle.is_good / cycle.result (OK|NG)
cycle.duration / cycle.event_name / cycle.ng_reason
cycle.completed_steps / cycle.total_steps / cycle.start_time / cycle.end_time
cycle.missing_step_count     # v2.7.5 新增
project.id / project.name
steps[]                      # 每项: label/index/duration/is_good/confidence/start_time/end_time
ng_steps[]                   # v2.7.5 新增：steps 中 is_good=False 的子集
defects[]                    # 每项: defect_code/defect_name/category/severity
workpiece.{id,serial_no,status,inspection_count}
order.*
operator.{name,employee_no,id}
```

**便利字段的取舍**：
- `missing_step_count` 优先用 `total - completed`；两者有 None 时回退到 `len(ng_steps)`
- `ng_steps` 顺序 = `step_record.id`（创建顺序），不是缺失顺序
- 这两个字段是**追加**，不破坏旧模板，所以现场升级直接重启后端即可

**JSON / 表单模板引用示例**（Jinja2）：
```json
{
  "order_no": "{{ order.order_no }}",
  "result": "{{ cycle.result }}",
  "missing_count": {{ cycle.missing_step_count }},
  "ng_step_names": [{% for s in ng_steps %}"{{ s.label }}"{% if not loop.last %},{% endif %}{% endfor %}]
}
```

## 工单表单模板（v2.7.5 新增）

- 存储：`localStorage['mes_order_form_template_v1']`，**仅前端**，不走后端
- 预设字段 key 必须用：`order_no / product_name / product_code / product_spec / planned_qty / priority / remark`（与 ORM 字段名一致）
- 非预设字段 → 保存到工单的 `extra_data` JSON 列
- 必填兜底（新建时）：
  - `order_no` 被删 → 前端自动补 `ORD-{Date.now()}`
  - `product_name` 被删 → 前端自动补 `未命名`
  - 编辑已有工单不做兜底（用户改不了 `order_no`）
- 编辑时：模板外的原 `extra_data` 字段会保留到 payload 里，避免破坏既有数据
- 故障诊断：新建工单出现 `order_no 必填` 之类后端 422 → 检查 `PRESET_META` 是否和后端 schema 字段对齐

## 工单绑定范围 binding_scope（v3.1.0 新增）

**字段**：`WorkOrder.binding_scope` (project / channels / cluster) + `target_channels` (JSON list, channels 模式必填) + `target_stations` (JSON list, cluster 模式留作扩展位 UI 不暴露)。

**计件链路对照表**：

| scope | 谁找工单 | 触发 +1 时机 | 备注 |
|---|---|---|---|
| `project` | `mes_hooks._handle_session_start` 调 `get_active_order(project_id, channel_id)` | `_handle_cycle_end` → `increment_completed` | 老逻辑;`project_id` 必填 |
| `channels` | 同上但按 `channel_id ∈ target_channels` 匹配,**不看 project_id** | 同上 | 同一台机分上下班 / 不同工位跑不同活 |
| `cluster` | 不进 `_active_orders`,由 `cluster_collector` 直接 `find_cluster_orders` | `cluster_collector._check_and_dispatch` 推送 `box_complete` 后 (且 `is_recovery=False`) → `_increment_cluster_orders` | **1 个 box_serial = 1 件**;校正重推不重复 +1 |

**客户报"集群已完成但工单数量 0"排查路径**：

1. **前端列表「绑定」列**：红色"未绑定"tag 直接定位——`binding_scope=project AND project_id IS NULL`(老工单或漏选项目)。`OrderPanel.vue` 创建表单顶部 radio 必填,用户必须三选一。
2. **状态不是 `in_progress`**:`get_active_order` / `find_cluster_orders` 都按 `status='in_progress'` 过滤,`draft/pending/paused` 都不会被找到。
3. **工单中途切 in_progress 不会 hot bind**:`_active_orders[channel_id]` 只在 `_handle_session_start` 时填一次,session 已经在跑时切工单状态对当前 session 无效,需要重启 session 或新 cycle。**这是已知设计**,v3.1.0 未改。
4. **cluster 模式下不会进 `_active_orders`**:正常的;`_handle_session_start` 找不到工单时**静默**(v3.1.0 BUG-310-001),不再打 INFO 日志。
5. **校正重推不重复 +1**:`is_recovery=True` 时 cluster +1 跳过,只有首次推送才计件。

**关键代码点**：
- `backend/services/work_order.py::WorkOrderService._normalize_binding(data, strict)`:三选一校验 + 互斥清空。`strict=False` 给 `receive_external_order` 走宽松路径(允许 `project_id` 空入库,前端红字提示)。
- `backend/services/work_order.py::WorkOrderService.find_cluster_orders(station_ids=None)`:默认按 priority + created_at 取首条,无 station 过滤;工单显式带 `target_stations` 时才走交集过滤。
- `backend/services/cluster_collector.py::_check_and_dispatch` → `_increment_cluster_orders` 在 `gw.dispatch("box_complete")` 后 + 非 `is_recovery` 才执行。
- 前端 `frontend/src/views/MES/OrderPanel.vue::handleSave`:绑定字段独立合并到 payload,**不走模板系统**。

**回归测试**：`tools/test_workorder_binding.py`(独立 SQLite + Mock MES gateway,32 PASS / 0 FAIL)。

## 集群汇总 / 副机心跳（v2.7.6 引入、v2.7.8 修正）

**架构**：多机协同时一台为 master，其它为 slave；每个工位产出一个 cycle 就 POST `/api/v1/cluster/report` 给 master，master 按 `box_serial` 聚合所有工位后推 MES。副机在线状态靠定时 `POST /api/v1/cluster/heartbeat` 维护，主机超时 `_slave_timeout=20s` 自动清除。

**核心组件**：
- `backend/api/cluster.py`：`/config`、`/report`、`/heartbeat`、`/slaves`、`/health`、`/boxes`
- `backend/services/cluster_collector.py::ClusterCollector`：`register_slave`、`receive_station_report`、`report_to_master`、`_check_and_dispatch`、`_heartbeat_sender_loop`（v2.7.8 新增）、`_timeout_checker`
- `backend/services/mes_hooks.py::_cluster_dispatch`：cycle 结束时根据 role 分发（master→本地 receive_station_report，slave→report_to_master）。注意：早期版本/文档里常写作 `_dispatch_to_cluster`，实际函数名是 `_cluster_dispatch`，grep 时别搜错。
- 前端 `frontend/src/views/MES/ClusterPanel.vue`：配置页 + 已连接副机列表 + 待汇总/最近完成 + 明细弹窗
- 前端 `frontend/src/api/cluster.js`：封装 5 个 cluster API

**v2.7.8 前的坑（已修）**：副机心跳**只由前端 ClusterPanel 的 `setInterval(doHeartbeat, 10000)` 发送**，仅在该页面挂载期间工作。副机切到其它页面 → `onUnmounted` 清 timer → 主机 20 秒后超时把副机清出 `_connected_slaves`。**表现**：主机「已连接副机」一片空白，切页面再回来数据丢失。

**v2.7.8 修法**（`cluster_collector.start()` 里新起 `_heartbeat_sender_loop` 线程）：
- 每 `_heartbeat_interval=5s` 读一次 config，`role=='slave' & enabled & master_url` 齐则 POST `/cluster/heartbeat`
- payload 从 `backend.api.channel_manager.channel_manager` 实时取 channel_count / detecting，project 从任一 running 通道的 project_config 取
- `socket.gethostname()` 作为 hostname
- HTTP 失败只日志不崩；配置运行时改动通过 `invalidate_config_cache()` + 下轮重读生效
- 前端的 `heartbeatTimer` 保留作双保险（主机 `register_slave` 是幂等 upsert，不会冲突）

**排查步骤**：
1. 副机 `GET /cluster/config` 看 `role / enabled / master_url / station_id`——缺一样心跳都发不出
2. 主机 `GET /cluster/slaves` 看当前在线列表；副机 `GET /cluster/health` 看主机可达性
3. 后端日志搜 `副机心跳`：失败会打 `副机心跳 HTTP xxx` 或 `副机心跳失败: xxx`
4. 主机端 `_connected_slaves` 通过 lock 访问是 in-memory，**后端重启丢失**，是刻意设计（副机会重新上报）

**`expected_stations` 配置要匹配 `station_id`**：`cluster_collector._check_and_dispatch` 用集合对齐，少一个工位 box 就永远处于 pending。多工位模式下 station_id 会附加通道号（`mes_hooks.py:539`：`f"{station_id}-{channel_id}"`），配置 expected_stations 时要记得带 `-0/-1`。

## 外部设备串口 PermissionError 13（v2.7.8 修）

**症状**：Windows 上称重器/扫码器串口连接失败，日志 `could not open port 'COMx': PermissionError(13, '拒绝访问。', None, 5)`。

**根因**：两种并发/残留路径
- a) 上次连接或测试关闭后 Windows 还锁着端口（`ser.close()` 返回到 Windows 实际释放有毫秒级延迟）
- b) 前端点「测试」按钮的 `test_connection` 和连接线程的 `_serial_loop / _serial_modbus_ascii_loop / _serial_continuous_loop` 短时间内都打开同一 port，后者被拒

**v2.7.8 修法**（`external_device.py::ExternalDeviceService._open_serial_with_retry` 静态辅助）：
- 三个 loop 的 `serial.Serial(...)` 调用统一换成 `self._open_serial_with_retry(port, baud, ..., timeout=...)`
- 失败后 `time.sleep(retry_delay=1.0)` 再试，最多 `max_retries=3` 次
- PermissionError 单独识别打日志 `打开被拒绝(PermissionError) 第 N 次尝试`
- 任何重试成功都会打 `第 N 次重试打开成功`
- 3 次全失败才 `raise`，上层 `conn.status='error'` 逻辑不变

**test_connection 没动**：用户点测试按钮是短操作，不加重试避免卡住 UI。如果稳态连接线程已经持有端口，测试按钮会失败——这是**预期行为**，未来若要在「已连接」状态禁用测试按钮需改前端。

## 集群 + MES 联调的 7 个坑（v2.7.9 大修）

上一版（v2.7.8）只改了副机心跳和 `expected_stations` 前缀匹配，**真正让集群汇总从头到尾跑通是 v2.7.9 这一轮**。排查时按这 7 点逐条对照：

### 1. `mes_hooks` 的 `cycle_end / session_end` 外层 except 曾静默吞错
- 旧代码：`except Exception as e: print(f"... {e}")`，丢掉 traceback。
- 结果：`build_context_from_cycle` 里一个小 `AttributeError`（比如 `DetectionCycle` 没 `completed_steps`、`StepRecord` 没 `step_index`）就能让 cycle_end 外部推送分支**整段静默返回**，前端集群汇总一直空白。
- v2.7.9：两处 `except` 都加 `import traceback; print(f"{e}\n{traceback.format_exc()}", flush=True)`。**新写 except 也要默认带 traceback**。

### 2. `MESGateway.build_context_from_cycle` 模型字段读法要兜底
- 历史上 `DetectionCycle` 没有 `completed_steps / total_steps`，`StepRecord` 没有 `step_index / duration_seconds / is_good`，但 context 构建硬读。
- v2.7.9：所有字段改走 `getattr(x, 'new_name', None) or getattr(x, 'old_name', None)` + 合理 fallback（`total_steps` 从 `step_sequence` 列表长度推算，`is_good` 从 `is_valid` 回退）。
- 加字段时**优先 getattr**，避免一次改表就把 MES 推送炸掉。

### 3. `resolve_rebind` 错 import：`backend.database` vs `backend.db.database`
- 实际 SessionLocal 在 `backend.db.database`，`backend.database` 不存在。触发手动重绑那一步（`action == "continue"`）才会崩，隐蔽度高。
- v2.7.9 修正。新增 import 时一律走 `backend.db.database`。

### 4. SQLite `database is locked` — WAL + busy_timeout + commit 重试三件套
后台 `_timeout_checker` 批量更新 `status='timeout'` 时会和前台 `receive_station_report` 抢写锁，原始代码直接 500。

- **`backend/db/database.py`**：`connect_args={"timeout": 15}` + `@event.listens_for(engine, "connect")` 设置 `PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=15000`。老 DB 文件也要一次性 `PRAGMA journal_mode=WAL`，WAL 是持久属性。
- **`cluster_collector.py`** 顶部统一 `_commit_with_retry(db, ctx, max_retries=6, base_sleep=0.2)`：只对 `OperationalError` 且 msg 含 `locked/busy` 做指数退避（0.2s→1.2s，累计约 4.2s），其他异常直接抛。
- 所有 cluster 写路径 commit 都必须用它：`receive_station_report`、`_check_and_dispatch`、`_push_timeout_result`、`_timeout_checker` 的 timeout 标记。

### 5. `_timeout_checker` 单 session 长事务 → 改成每箱独立 session
- 旧写法：一个 session 列出所有箱、循环里写 → 一次 checker 轮询可以把上报线程全顶住。
- v2.7.9：probe session 只读列箱子后立刻 close；循环里每个 box 新开一个 SessionLocal，单箱处理完 close。占锁时间缩到单箱粒度。

### 6. 并发竞态：BoxAggregation / BoxSummary 的 UNIQUE 冲突
同一箱号被 4 路（B-0/B-1 + A + C）并发上报时出现两种 500：
- `UNIQUE(box_serial, station_id)`：两个线程都查 existing=None 都 INSERT。
- `UNIQUE(box_serial)` on `box_summaries`：两个线程都进"全齐分支"都 INSERT summary。

v2.7.9 两层防线：
1. **进程内 per-box 锁**（`_acquire_box_lock` / `_release_box_lock` + dict + guard lock）：`receive_station_report` 整段包在 `with self._acquire_box_lock(box_serial):` 里，同箱号完全串行，不同箱号照样并发。推送/超时成功后 `_release_box_lock` 清 dict，避免无界增长。
2. **`IntegrityError` 捕获 + 回滚 + 重查 UPDATE**：万一有跨进程或极端场景，`receive_station_report` / `_check_and_dispatch` 都能兜底。
3. `_check_and_dispatch` 里额外加 `if summary.status in ('pushed','pushed_timeout'): return {"reason":"already_pushed_by_peer"}`，防止极端排序下重复推送 MES `box_complete`。

### 7. 调试接口 `/api/v1/debug/test_cluster_flow`
真正的端到端验证手段：传入一个已落库的 `cycle_id`，服务端按正式路径跑 `build_context_from_cycle → receive_station_report`，返回 `steps` 列表指明哪一步挂了。对应实现写在 `hotfix.py`，客户端不会看到这个接口（仅调试）。排查集群问题时**优先走它**，比改代码加 print 再等扫码快得多。

### 压测脚本（并发 + 竞态）
真实生产环境最好用类似脚本先自测再发版：
- 4 路并发同箱上报 → summary 必须恰好 1 条、status=pushed。
- 同 station_id 重复上报 + 全量工位并发（7+ 请求）→ 所有 HTTP 200，`dispatched:true` 恰好 1 次；后续响应应该出现 `already_pushed_by_peer`。
- 压测时看后端日志必须**没有** `UNIQUE constraint failed` 和 `database is locked`。

相关文件：`backend/services/cluster_collector.py`、`backend/services/mes_hooks.py`、`backend/services/mes_gateway.py`、`backend/db/database.py`、`backend/hotfix.py`。


---

## v2.7.11 外部 MES Gateway 能力增强（2026-04-23）

外部 MES 对接（`MESGateway` → external customer MES）这一轮大改，核心是**所有客户需求在前端点选即可满足，不需要改代码**。

### 1. 统一鉴权：`_apply_auth_to_headers`（静态方法）
位置：`backend/services/mes_gateway.py::MESGateway._apply_auth_to_headers(config) -> new_config`
- `auth.type=none` → 不改
- `auth.type=basic` → 留给 adapter 走 `requests.auth`（不改 headers）
- `auth.type=bearer` → `Authorization: Bearer <token>`
- `auth.type=api_key` → `<header|X-API-Key>: <value>`
- `auth.type=custom_header` → `auth.headers: [{key,value}]` 全部合并
- 顶层 `config.custom_headers` 列表/字典也会合并进 headers（鉴权之外的额外固定 header）

**调用点**：
- `_send_to_connection` 走实时推送：一定要用 `effective_config = _apply_auth_to_headers(config)` 给 `adapter.send` / `check_response`
- `api/mes_gateway.py::test_connection` 走 /test：同样要用
- 任何直接调 `adapter.send` 的新代码：记得也走一下，否则 bearer/api_key 全丢

### 2. 按结果过滤：`push_on_result`
- `config.push_on_result = ["OK"]` / `["NG"]` / `["OK","NG"]`（默认等价不设，全推）
- 在 `_send_to_connection` 里**鉴权之前**过滤，不符合只写一条 skip 日志不调 adapter
- **⚠ `/test` API 故意绕过这个过滤**：用户点"测试"时必须看到请求能不能发出去，不能因为过滤规则静默丢失
- 判定字段：`overall_result` 或 `result`（大小写不敏感）

### 3. 物料名称映射：`label_mapping`
- `config.label_mapping = {"螺丝A": "PART-001", ...}`：影响 `ng_items` 字段
- 两种模式：
  - `label_mapping_mode="replace"`（默认）：直接把 `ng_items` 替换成映射后的值
  - `label_mapping_mode="keep_both"`：原 `ng_items` 保留，新增 `ng_items_mapped`
- 只处理 `ng_items`（list of str），其他字段（如 `stations[].ng_steps`）不变
- `/test` API 也走这个映射，保证测试 payload 与真实推送一致

### 4. 集群 `aggregated` 顶层便利字段
`backend/services/cluster_collector.py::_check_and_dispatch` 和 `_push_timeout_result` 两分支都给 `aggregated` 加：
- `order_no`（从 `stations[0].order.order_no` 拿）
- `workpiece_id`（从 `stations[0].workpiece.serial_no` 拿）
- `ng_items`（所有 `stations[i].ng_steps[j].label` 去重）
- `result`（与 `overall_result` 同值，别名方便模板写 `{result}`）

超时分支特殊处理：`missing_stations` 里每个工位会作为 `MISSING-<工位名>` 追加到 `ng_items`，让客户 MES 也知道缺哪几站。

**模板写法对比**：
```json
// 旧（嵌套取值，运维改不动）
{"order": "{stations[0].order.order_no}", "result": "{overall_result}"}

// 新（顶层取值，客户文档示例就是这个）
{"order_no": "{order_no}", "result": "{result}"}
```

### 5. `/test` API 按 event_type 分路
`backend/api/mes_gateway.py::TestPayload` 加 `event_type` 字段；`test_connection` 根据它选择 test_context：
- `cycle_end` (默认) → `_build_test_context_cycle_end()`：单工位周期结束结构
- `box_complete` → `_build_test_context_box_complete()`：集群汇总结构（含顶层便利字段 + 2 个 NG 步骤便于测映射）
- `box_timeout` → `_build_test_context_box_timeout()`：超时结构（含 `MISSING-B`）

前端点"测试"按钮时传 event_type。如果没传，根据 `push_events` 里第一个事件自动选。

### 6. 前端 GatewayPanel 全面图形化
`frontend/src/views/MES/GatewayPanel.vue` 新增 UI：
- 鉴权方式下拉（none/basic/bearer/api_key/custom_header）+ 对应动态表单
- 额外请求头编辑表（鉴权之外的固定 header）
- 按结果过滤 checkbox（OK/NG）
- 物料名称映射表 + replace/keep_both 模式选择
- 一键填入预设模板下拉：4 个模板（box_complete·4字段 / box_complete·完整 / cycle_end·简化 / cycle_end·含步骤）
- 工具栏"测试用事件"下拉（与 push_events 独立，便于临时切换测试）

### 7. 诊断步骤（外部 MES 推送失败时）
1. **看 MESCommLog**：`/api/v1/mes/gateway/logs?connection_id=X`，有 `status_code` 和 `error_msg`
2. **点前端"测试"按钮**：会走和真实推送一致的鉴权+映射链路，出错会返回详细 payload_preview + response_body
3. **端到端自测**：`python3 tools/test_mes_gateway.py` 在本机起 5 个 mock MES，10 个场景全绿才算后端链路没问题；如果 mock 都过但客户那边失败，就是客户端点配置错
4. **常见坑**：
   - `bearer token` 改成 headers 后还被 adapter 覆盖 → 检查 adapter 有没有把 `config.headers` 整个替换
   - `label_mapping` 配了但不生效 → 检查 `ng_items` 字段是不是 list、key 是不是精确匹配（中文空格要一致）
   - `/test` 返回 HTTP 200 但实际推送到客户 failure → 检查是否 `push_on_result` 误配（test 绕过它，实时推送会用）
   - 超时分支漏 `MISSING-` → 检查 `_push_timeout_result` 没被手改破坏顶层字段填充

相关文件：
- `backend/services/mes_gateway.py`（核心：预处理管道 + 鉴权 + 过滤 + 映射）
- `backend/services/cluster_collector.py`（aggregated 顶层便利字段）
- `backend/api/mes_gateway.py`（/test API 按 event_type 分路）
- `backend/services/mes_adapters/{rest,form_data}_adapter.py`（adapter 只负责发请求，不处理 auth headers）
- `frontend/src/views/MES/GatewayPanel.vue`（全图形化配置）
- `tools/test_mes_gateway.py`（端到端自测）
- `docs/客户MES对接数据格式.md`（对外文档 + 10 项贵方确认清单）

## v2.7.12 MES/集群/扫码器增强（2026-04-23）

### 1. 适配器 4 选 1（新增 form-urlencoded / query-string）

注册表 `backend/services/mes_adapters/__init__.py`:

| `adapter_type` | 发送形式 | Content-Type | 典型客户场景 |
|---|---|---|---|
| `rest` | body = JSON | `application/json` | 现代 REST API |
| `form-data` | body = `param=<JSON 字符串>` | `application/x-www-form-urlencoded` | 客户说"参数叫 param" |
| `form-urlencoded` | body = `order_no=xxx&result=NG&...`（字段平铺） | `application/x-www-form-urlencoded` | 客户说"字段分开不要打包" |
| `query-string` | URL 带 query，body 空 | 无 | 客户说"参数放 URL 里" |
| `modbus_rtu` | Modbus 寄存器 | - | PLC/工控 |

**关键点**：
- `form-urlencoded` / `query-string` 两个适配器都支持 `array_join` 配置（默认 `,` 拼数组，可设 `"json"` 转 JSON 字符串）
- 这两种适配器会把 `missing_items: ["螺丝A","垫片B"]` 自动拼成 `"螺丝A,垫片B"` 发出
- 不需要改代码，客户答哪种前端直接选

**端到端测试**：`python3 tools/test_mes_gateway.py` 现在是 9 场景 + 3 API，全绿才算没问题。

### 2. 扫码器"OK 后同码冷却" (`ok_rescan_cooldown_sec`)

解决的问题：A 扫码 → A 完成 OK → 移走 A 时又扫到 A → 新物品 B 被记成 A 的延续；DB 留"queued 但永不消费"的脏记录。

配置位置：
- 后端 `ScannerConnection` dataclass 新增字段 `ok_rescan_cooldown_sec: int = 0`
- 前端 `ScannerPanel.vue` 扫码器编辑对话框"去重/冷却"区
- `MESHookManager._handle_scan` 在受理前检查：已 OK + 距结算 < 冷却秒数 + 同条码 → 静默忽略（返回不抛错）
- NG 不受冷却影响（便于现场纠错重扫）

默认 `0` 表示关闭（出厂保持旧行为），现场按扫码频率调：流水线建议 5~10 秒。

### 3. 集群 `sub_reports` 按 `(channel_id, source_address)` 去重

排查"同通道多行 / 同条码 OK+NG 并存" 时直接看：
- `cluster_collector._receive_station_report_locked`
- 逻辑：每次收到新 sub_report，先按 `(channel_id, source_address)` 找已有的，有就替换无就追加

被这段代码覆盖掉的旧数据**不会保留历史版本**——如果客户需要"看历史"，要额外写 CycleContext 归档。

### 4. 集群箱数据清理 API

- `DELETE /cluster/box/{barcode}` — 单条码全删（BoxAggregation + BoxSummary）
- `POST /cluster/boxes/clear` — `scope=all|pending|recent|older`，`older` 带 `before_days=N`
- 前端 ClusterPanel 有"清理"按钮进对话框

常见误解：删完 `BoxAggregation` 后条码重新扫会视为"新条码"，是预期行为；对应工件 `Workpiece` 表不会被这两个 API 触及，需要时单独调 workpiece 相关接口。
