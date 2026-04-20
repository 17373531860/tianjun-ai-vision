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
