# 13 — 通用 PLC 连接器（PLC Connector / 点位引擎）主程序原生 RFC

> 适用版本：基于 v3.47.0 主线
> 本文目的：把"视觉系统 ↔ PLC 数据交互"做成主程序**原生的、纯配置驱动的基础设施**。目标是：任何新客户的 PLC 对接方案（协议、地址、触发语义、握手方式、写回编码）都通过**软件内设置**完成，**不再改代码、不再发版**。
>
> **立项背景（决策留痕）**：天永机加线（S7-300 DB 交互，见 `docs/PLC对接协议_S7-300_DB交互_V0.2_2026-08-09.pdf`）是第一个落地实例。主作者明确要求："下次我不想改软件了……考虑所有可能，并且都是可配置的，下次只需要在软件中设置就可以。"
>
> **归位判定**（`feature-placement` A+D 类）：PLC 对接是多客户通用的核心通路（数据输入源 + 结果输出口），且涉及新表/新引擎 → **主程序原生**，同时给插件平台留 driver 注册 + 自定义动作 hook，客户级怪癖走插件。
>
> 阅读前置：AGENTS.md 第三节产品决策原则、`debug-mes` skill（Gateway 适配器）、`external_device.py`（外设协议循环先例）。

---

## 一、动机与现状

### 1.1 这次的真实需求（天永范式）

- PLC：西门子 S7-300，OPC 直连（实为 S7 ISO-on-TCP），IP `172.20.11.111`
- 读区 `DB1200`：PLC 心跳（1s 翻转）、工件到位、读完成信号（**PLC 在工件离站时自复位**）、产品号（ASCII 字符串）、缸体类型（INT）
- 写区 `DB1201`：视觉心跳、数据已接收（仅监视用）、检测完成、检测结果码
- 业务：读完成上升沿 → 取产品号绑定追溯 + 按缸体类型自动切项目 → 周期结束把结果写回

### 1.2 为什么"这次还要改软件"——教训分析

上一轮（RFID/扫码类对接）我们把**这一家的方案**做进了软件，而不是把**方案空间**做进软件。客户换一个信号复位方式（自复位 vs 握手复位）、换一个结果编码（OK=1 vs OK=0）、换一个品牌 PLC，就要重新开发。**根因是把"配置值"和"配置维度"混为一谈**：只参数化了 IP/地址，没参数化触发语义、握手模式、值编码、动作绑定。

本 RFC 的方法论：**先穷举方案空间的全部维度（第三节），每个维度做成枚举配置项；引擎只实现维度的笛卡尔组合，不实现任何一家客户的具体方案。** 客户方案 = 一份 JSON 配置。

### 1.3 现有基础设施盘点（能复用的都复用）

| 已有能力 | 位置 | 与本 RFC 的关系 |
|---|---|---|
| 外设协议循环（tcp / modbus_tcp / serial / http_poll / mock） | `backend/services/external_device_protocols.py` `_device_loop` | 连接线程模型先例；但其消费端只有 `feed_weight`（浮点重量），不通用 |
| 扫码统一注入口 `on_scan_received(channel_id, serial_no, ...)` | `backend/services/mes_hooks.py` L519 | **PLC 读到的产品号直接走这里**，复用去重/防呆/绑定/MES 全链路 |
| 规格→项目统一匹配器 `resolve_project_id_by_spec` | `backend/services/project_match.py` | **缸体类型 INT→项目**直接复用（精确→通配符→子串三档） |
| MES Gateway 适配器注册表（含 `modbus_rtu` 写寄存器先例） | `backend/services/mes_adapters/__init__.py` | 写回架构对照物；`register_adapter` 是插件注册先例 |
| `_handle_cycle_end` → `gw.dispatch("cycle_end")` | `mes_hooks.py` L1550 | 周期结果对外写出的最干净挂点 |
| `pymodbus>=3.5` | `backend/requirements.txt` | Modbus TCP/RTU driver 零新增依赖 |
| mock_weight 虚拟设备 | 外设家族 | mock PLC driver 先例（无硬件联调/测试/演示） |

**缺口**：S7/MC/FINS/EtherNet-IP/OPC UA 协议栈为零；"点位→语义动作"的通用规则引擎不存在；"事件→写点位"的通用写回引擎不存在。

---

## 二、目标与反目标

### 2.1 目标

| 目标 | 说明 |
|---|---|
| **零代码接入新客户 PLC** | 协议、地址、数据类型、触发、握手、写回、值编码全部软件内配置 |
| **主流 PLC 全覆盖** | 西门子 S7 全系 / 三菱 MC / 欧姆龙 FINS / AB EtherNet-IP / Modbus 系（台达/汇川/信捷等国产）/ OPC UA 兜底 |
| **复用下游全链路** | 产品号走扫码链路，项目切换走统一匹配器，事件走 `_trigger_event`，不另起一套 |
| **现场可联调** | 点位实时监视、手动写值、IO 日志、mock driver、配置导入导出 —— 现场工程师不开代码就能通 |
| **错误隔离** | PLC 断链/驱动异常绝不拖垮检测主流程（对齐不变量 15：热路径不阻塞） |
| **给插件留口** | `register_plc_driver` + `plc_rule_action` hook，未来真有覆盖不到的协议/动作走插件，仍不改主程序 |

### 2.2 反目标（本 RFC 不做）

- ❌ 不做 PLC 程序侧的组态/下装（我们只是通讯客户端）
- ❌ 不做通用 SCADA（点位数量设计上限 256/连接，够视觉对接场景）
- ❌ 不替代现有报警灯柱 Modbus 通路、称重外设通路（各走各的，正交并存）
- ❌ 不做 OPC DA（经典 COM 版，仅 Windows + DCOM 噩梦；客户真有则加 OPC UA 网关是行业标准做法）

---

## 三、方案空间穷举（核心节：每一行都是配置项，不是代码）

> 判断"考虑了所有可能"的标准：任何一家客户的对接方案，都能在下面六个维度里各选一个值组合出来。

### 3.1 维度一：协议 / 驱动（driver 注册表）

| driver | 覆盖 PLC | 依赖库 | 首版实现 |
|---|---|---|---|
| `s7` | 西门子 S7-200Smart/300/400/1200/1500 | `python-snap7`（含 dll，随安装包分发） | ✅ |
| `modbus_tcp` | 台达/汇川/信捷/施耐德/带 Modbus 模块的一切 | `pymodbus`（已有） | ✅ |
| `modbus_rtu` | 串口 Modbus 设备 | `pymodbus` + `pyserial`（已有） | ✅ |
| `mc` | 三菱 FX/Q/L/iQ-R（MC 协议 3E/1E 帧） | `pymcprotocol` | ✅ |
| `fins` | 欧姆龙 CJ/CS/CP/NX | `fins`（或自实现 UDP/TCP FINS，报文简单） | ✅ |
| `ethernet_ip` | AB CompactLogix/ControlLogix、欧姆龙 NJ | `pycomm3` | ✅ |
| `opcua` | 一切带 OPC UA Server 的新型 PLC / 网关 | `asyncua` | ✅ |
| `mock` | 无硬件联调/CI 测试/展会演示 | 无 | ✅ |
| （插件注册） | 私有协议怪癖 | 插件自带 | hook 留口 |

连接级参数（每个 driver 各自的 `conn_params` JSON）：IP/端口/rack/slot/unit_id/网络号/站号/CPU 类型/超时/重连退避（初始间隔、倍率、上限）/**轮询周期 poll_interval_ms**（默认 100，可 20~5000）。
**多连接并存**：一台工控机可同时连多台 PLC；一条连接可服务多工位。

### 3.2 维度二：点位（Tag）定义

每个点位 = `{key, 地址, 类型, 方向, 修饰}`：

| 配置项 | 可选值 |
|---|---|
| 地址方言 | S7: `DB1200.DBX0.0` / `DBW2` / `DBD4` / `STRING@6`；Modbus: 线圈/离散/保持/输入寄存器 + 地址；MC: `D100`/`M10`；FINS: `DM100`；EIP: tag 名；OPC UA: NodeId |
| 数据类型 | `bool` / `int16` / `uint16` / `int32` / `uint32` / `float32` / `float64` / `byte` / `word` / `dword` / `string_s7`（带 max/len 头）/ `string_fixed`（定长填充）/ `string_cstr`（\0 结尾）/ `bcd` / `bitfield`（一个字拆 16 个 bool） |
| 字节序 | `big`（S7 默认）/ `little` / `word_swap`（Modbus 32 位常见 CDAB） |
| 字符串编码 | `ascii` / `gbk` / `utf8` / `utf16`；`strip`（去空格/去 \0/去不可见字符）开关 |
| 数值修饰 | `scale` / `offset`（工程量换算）；`deadband`（死区，抖动过滤） |
| 方向 | `read`（进轮询）/ `write`（按需写）/ `read_write` |
| 覆盖轮询周期 | 点位级 `poll_interval_ms` 覆盖连接级（心跳点快轮、字符串慢轮） |

### 3.3 维度三：触发规则（读侧：点位变化 → 动作）

规则 = **条件 × 防抖 × 动作序列 × 握手**：

**条件**（可多条 AND）：

| 触发类型 | 语义 | 典型场景 |
|---|---|---|
| `rising` / `falling` | bool 上升/下降沿 | 读完成信号（天永）|
| `change` | 任意值变化 | 产品号变了就算新工件 |
| `equals` / `not_equals` / `in` / `range` | 值匹配 | 状态字=3 表示到位 |
| `level` | 电平保持 ≥N ms | 在位信号期间有效 |

防抖/节流：`debounce_ms`（信号稳定 N ms 才认）、`min_interval_ms`（两次触发最小间隔，防连击）。

**动作**（注册表制，序列可组合、每步可延时、可条件守门）：

| action | 语义 | 复用链路 |
|---|---|---|
| `bind_sn` | 读若干点位按模板拼编号（如 `{{产品号}}-{{缸体类型}}`）→ 注入扫码链路 | `on_scan_received`，去重/防呆/空码策略全继承 |
| `switch_project` | 值→项目映射 | `resolve_project_id_by_spec`（精确/通配/子串），未知值策略：报警 / 用默认项目 / 忽略（可配） |
| `start_detection` / `stop_detection` / `pause` / `resume` | 控制检测 | 现有 source 控制 API |
| `start_cycle` / `end_cycle` | PLC 信号驱动周期起止 | 现有周期 API（外部触发型客户） |
| `write_points` | 立即写一组点位（如"数据已接收"回执） | 本子系统写通道 |
| `trigger_event` | 进事件中心（报警/语音/计数/Toast） | `_trigger_event` |
| `alarm` | 指定级别+文案模板的报警 | 报警体系（归入共享灯柱四类，遵守不变量 16） |
| `set_var` | 存上下文变量供后续模板引用 | 引擎内 |
| （插件 hook `plc_rule_action`） | 客户怪癖动作 | 插件平台 |

### 3.4 维度四：握手 / 复位语义（`ack_mode`，枚举全部现场可能）

| ack_mode | 语义 | 场景 |
|---|---|---|
| `none` | 只读不回，PLC 自己管复位 | **天永：工件离站 PLC 自复位** |
| `write_ack` | 触发后视觉写确认点=V1，可配 `clear_after_ms` 后自动写回 V0 | 标准双向握手 |
| `self_clear` | 视觉直接把触发位写回 0 | PLC 侧懒得写复位逻辑的厂 |
| `level_follow` | 电平语义，跟随在位信号，无复位概念 | 在位=1 期间持续有效 |

兜底（所有模式通用）：`stuck_timeout_s` —— 触发位卡住超时 → 可配动作（报警 / 视为已复位 / 忽略）。

### 3.5 维度五：写回规则（写侧：主程序事件 → 写点位）

**事件源**（对齐 MES Gateway 的 push_events 面）：`cycle_start` / `cycle_end` / `step_ng` / `session_start` / `session_end` / `scan_bound` / `box_complete` / `detection_started` / `detection_stopped` / `system_alarm` / `connection_up` / `connection_down` / **`heartbeat`（周期性，周期可配，值模式：翻转 / 递增 / 常量）**。

每条写规则：

| 配置项 | 可选值 |
|---|---|
| 过滤 | 工位 / 项目 / 结果（仅 OK / 仅 NG / 全部） |
| 写目标 | 一组 `{point, value}` |
| 值来源 | 常量 / 模板变量（`{{result}}` `{{serial_no}}` `{{ng_count}}` `{{project_name}}` `{{cycle_duration}}` …，对齐自定义导出字段仓库的命名） / **值映射表**（`OK→1, NG→2` 完全可配——每家客户结果编码不同是最高频差异点） |
| 写后自动复位 | `reset_after_ms` + 复位值（脉冲语义："检测完成"置 1，500ms 后归 0） |
| 失败策略 | 重试次数/间隔；仍失败 → 报警 + 丢弃 或 落盘队列回放（critical 可配） |

**写回绝不阻塞结算热路径**：独立写队列 + 工作线程，对齐不变量 15。

### 3.6 维度六：异常 / 降级策略（全部可配）

| 异常 | 可配策略 |
|---|---|
| 连接断链 | 重连退避参数；断链报警级别（无/warn/ng 灯）；断链期间检测继续 or 暂停 |
| PLC 心跳丢失 | 心跳监视点 + 超时阈值 + 动作（报警/停检测/仅记录） |
| 读到空产品号 | 报警 or 允许无码放行（对齐 scanner `warn_no_barcode` 语义） |
| 重复产品号 | 继承扫码链路 `duplicate_scan_action`（第一把枪说了算的既有策略体系） |
| 未知机型值 | 报警 / 默认项目 / 忽略（3.3 已列） |
| 写失败 | 3.5 已列 |
| 驱动崩溃 | driver 线程异常自动重启 + 错误计数上报，**绝不影响主程序启动**（错误隔离底线） |

---

## 四、架构设计

```
backend/services/plc/
├── drivers/                    # driver 注册表（对照 mes_adapters）
│   ├── base.py                 # BaseDriver: connect/read_batch/write/close
│   ├── s7.py  modbus.py  mc.py  fins.py  enip.py  opcua.py  mock.py
│   └── __init__.py             # _REGISTRY + get_driver + register_driver(插件口)
├── point_codec.py              # 类型/字节序/编码/缩放 编解码（纯函数，单测友好）
├── point_engine.py             # 每连接一线程：批量轮询 → 值缓存 → 边沿检测 → 触发规则
├── rule_actions.py             # 动作注册表（bind_sn/switch_project/... + 插件 hook）
├── write_dispatcher.py         # 事件订阅（挂 mes_hooks 分发链）→ 写队列 → 写线程
└── manager.py                  # PLCConnectorManager：配置加载/热重载/状态汇总（单例）
```

**关键设计决策**：

1. **读侧独立线程、值缓存快照**：PointEngine 按连接批量读（S7 一次读整段 DB 再本地拆点位，降轮询开销），检测主流程永不直接碰 socket。
2. **写侧队列化**：所有写经 write_dispatcher 队列，事件回调只 enqueue（微秒级），遵守不变量 15。
3. **产品号注入走 `on_scan_received`**，PLC 相当于"一把虚拟扫码枪"——去重、防呆、绑定时机、MES 推送全部免费继承，`raw_data` 标注 `plc:{connection}:{rule}` 便于追溯。
4. **配置存储**：新表 `plc_connections`（一行一连接），字段：`name/driver/enabled/conn_params(JSON)/points(JSON)/read_rules(JSON)/write_rules(JSON)/options(JSON)`。点位/规则作 JSON 列而非子表——先例是 Project 的 7 个 JSON 配置字段；迁移 `m0009_plc_connections.py`。
5. **API**：`/api/v1/plc/*`（connections CRUD、`/test`连通测试、`/points/live` 实时值、`/points/write` 手动写、`/logs` IO 日志、`/export` `/import` 配置模板）。在 `router_manifest.py` 登记。
6. **前端**：MES 页新增「PLC 对接」Tab（`views/MES/PlcPanel.vue`）：连接卡片 + 点位表格编辑器 + 规则编辑器（条件/动作下拉拼装）+ **实时监视面板**（点位当前值/变化时间/读写错误计数）+ 手动写值 + 导入导出。API 客户端 `api/plc.js`。
7. **方案模板库**：内置预设（「西门子 S7-300 DB 交互（天永范式）」「Modbus 保持寄存器通用」…），一键载入改地址即用；客户配置可导出 JSON 在客户间复制。
8. **工位清理**：连接绑定工位时挂 `on_channel_removed` 清理（不变量 4）。
9. **插件 hook**：`register_plc_driver(name, cls)` + `plc_rule_action`（自定义动作）+ `plc_value_transform`（读值自定义变换）。

### 天永实例 = 一份配置（示意）

```json
{
  "name": "天永机加线-1号PLC", "driver": "s7",
  "conn_params": {"ip": "172.20.11.111", "rack": 0, "slot": 2, "poll_interval_ms": 100},
  "points": [
    {"key": "plc_heartbeat", "addr": "DB1200.DBX0.0", "type": "bool", "dir": "read"},
    {"key": "in_position",   "addr": "DB1200.DBX0.1", "type": "bool", "dir": "read"},
    {"key": "read_done",     "addr": "DB1200.DBX0.2", "type": "bool", "dir": "read"},
    {"key": "product_no",    "addr": "DB1200.STRING@2", "type": "string_s7", "encoding": "ascii", "strip": true, "dir": "read"},
    {"key": "cyl_type",      "addr": "DB1200.DBW24", "type": "int16", "dir": "read"},
    {"key": "vis_heartbeat", "addr": "DB1201.DBX0.0", "type": "bool", "dir": "write"},
    {"key": "data_received", "addr": "DB1201.DBX0.1", "type": "bool", "dir": "write"},
    {"key": "detect_done",   "addr": "DB1201.DBX0.2", "type": "bool", "dir": "write"},
    {"key": "result_code",   "addr": "DB1201.DBW2",   "type": "int16", "dir": "write"}
  ],
  "read_rules": [{
    "when": [{"point": "read_done", "trigger": "rising"}],
    "debounce_ms": 50, "ack_mode": "none", "stuck_timeout_s": 30,
    "actions": [
      {"do": "bind_sn", "template": "{{product_no}}", "channel": 0},
      {"do": "switch_project", "source": "cyl_type", "mapping": {"1": 12, "2": 13}, "unknown": "alarm"},
      {"do": "write_points", "writes": [{"point": "data_received", "value": 1, "reset_after_ms": 1000}]}
    ]
  }],
  "write_rules": [
    {"on": "heartbeat", "period_ms": 1000, "writes": [{"point": "vis_heartbeat", "value": "toggle"}]},
    {"on": "cycle_end", "writes": [
      {"point": "result_code", "value_map": {"OK": 1, "NG": 2}},
      {"point": "detect_done", "value": 1, "reset_after_ms": 500}
    ]}
  ]
}
```

客户换任何一项（复位方式、结果编码、换三菱 PLC、加一个报警位）——**改的都是这份 JSON，界面上点几下**。

---

## 五、实施分期与工作量估算

| 期 | 内容 | 估算 |
|---|---|---|
| M1 | 表+迁移 m0009、driver 框架 + `s7`/`modbus_tcp`/`modbus_rtu`/`mock`、point_codec 全类型、PointEngine 触发引擎（全部 trigger/ack_mode）、动作注册表全量、write_dispatcher 全事件 | 5~7 天 |
| M2 | API `/plc/*` + 前端 PlcPanel（CRUD/点位/规则/实时监视/手动写/导入导出/模板库） | 3~4 天 |
| M3 | `mc`/`fins`/`ethernet_ip`/`opcua` driver（都是薄封装，codec/引擎复用） | 2~3 天 |
| M4 | 测试（codec 单测 + mock driver BDD 全场景 + e2e UI）、打包（snap7.dll 进安装器）、文档（操作手册 + debug-plc skill + 本 RFC 收尾） | 3 天 |

天永交付只依赖 M1+M2 的 `s7` 路径，但 M1 的引擎按全维度实现（这正是"下次零代码"的保证）；M3 可与现场联调并行。

### 实施状态（2026-08-09 回填）

M1~M4 已全部落地，实际文件落点：

| 层 | 文件 |
|---|---|
| ORM | `backend/models/plc_models.py`（`plc_connections` 表；纯新表由启动 `create_all` 自建，无需 ALTER 迁移，m0009 计划取消） |
| 编解码 | `backend/services/plc/point_codec.py`（bool/byte/int16~float64/word/dword/bcd/string_s7/string_fixed/string_cstr × 4 种字节序 × scale） |
| 驱动 | `backend/services/plc/drivers/`：`base.py` + `s7.py` + `modbus.py`(TCP/RTU) + `mc.py` + `fins.py`(内置无依赖) + `enip.py` + `opcua.py` + `mock.py`，注册表惰性 import 缺库不炸 |
| 引擎 | `backend/services/plc/point_engine.py`（每连接一线程：轮询/边沿/防抖/min_interval/ack_mode/心跳/断链重连） |
| 动作 | `backend/services/plc/rule_actions.py`（bind_sn 走 simulate_scan 链路 / switch_project / start·stop_detection / write_points / alarm / set_var，插件可注册） |
| 写回 | `backend/services/plc/write_dispatcher.py`（cycle_start/cycle_end 等事件 → 写规则，enqueue 级非阻塞；挂点在 `mes_hooks.py`） |
| 管理器 | `backend/services/plc/manager.py`（单例；无启用连接零开销） |
| API | `backend/api/plc.py` + `backend/schemas/plc.py`，`router_manifest.py` 登记 `/api/v1/plc/*` |
| 模板 | `backend/services/plc/presets.py`（s7_db_handshake 天永范式 / modbus_generic / mock_demo） |
| 前端 | `frontend/src/api/plc.js` + `frontend/src/views/MES/PlcPanel.vue`（MES 页「PLC 对接」tab）；轮询间隔/日志条数进 设置页可配（`plc_status`/`plc_live`/`plc`） |
| 测试 | `tests/test_plc_connector.py`（39 项：codec/地址方言单测 + mock 端到端）+ `tests/e2e_browser/test_plc_panel.py`（CI e2e 3 项）+ `tests/manual_uat/plc_connector_uat.py`（可见浏览器 UAT 11 步全过，证据 `tests/manual_uat/evidence/plc_connector_2026-08-09/`） |
| 依赖 | `backend/requirements.txt`：python-snap7 / pymcprotocol / pycomm3 / asyncua（FINS 内置、Modbus 复用 pymodbus）；**打包时驱动库必须齐装** |

## 六、不变量自检

- 不变量 1（`/api/v1/` 前缀）✅；不变量 4（`on_channel_removed` 清理）✅ 第四节第 8 条
- 不变量 8（迁移版本化）✅ 纯新表 `plc_connections` 由启动 `create_all` 自建（老库升级同样自动补表），无 ALTER 故无需迁移模块；不变量 15（热路径不阻塞）✅ 写队列化
- 不变量 16（报警归四类优先级）✅ `alarm` 动作走报警体系不直写串口
- 错误隔离底线：driver 全部惰性 import + 异常兜底，缺库/崩溃不影响主程序启动 ✅

---

**本文件最后更新**：2026-08-09（v0.2 — M1~M4 全部落地，第五节回填实施状态与实际文件落点）
**预期演进**：新客户接入后在第三节补充未覆盖维度（若有）；新客户范式沉淀进 `presets.py` 模板库
