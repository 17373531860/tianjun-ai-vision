---
name: debug-plc
description: "诊断 RFC 13 通用 PLC 连接器：连接失败/状态不翻已连接、点位读不到值/值不对（字节序/编码/scale）、触发规则不触发或误触发、bind_sn 绑码不建工件、写回不生效（cycle_end 结果码/心跳/回执脉冲）、驱动依赖库缺失、mock 联调剧本。当客户说'PLC 连不上''产品号读出来乱码''读完成信号不触发绑码''结果码没写回 PLC'时使用。"
---

# debug-plc — 通用 PLC 连接器诊断

> 设计文档：`docs/plugin-system/design/13_plc_connector_rfc.md`（第五节有实施状态与文件落点全表）。
> 操作手册用户视角说明：`docs/软件操作手册.md` §4.8.10。

## 〇、一分钟定位表

| 症状 | 第一嫌疑 | 跳读 |
|---|---|---|
| 卡片一直「启动中」/「通讯异常」 | IP/rack/slot 不对、防火墙、驱动库缺失 | §2 |
| 卡片「配置错误」 | 点位地址方言写错 / 点位校验不过 | §3 |
| 值读到但乱码/数值离谱 | 字节序 byte_order / 编码 encoding / scale | §3 |
| 规则不触发 | 电平早已是 1 无边沿 / debounce / min_interval 拦截 | §4 |
| bind_sn 没建工件 | 工位无激活项目（扫码链路丢弃）/ channel 不对 | §4 |
| cycle_end 没写回 | write_rules 的 channels 过滤 / value_map 键大小写 | §5 |
| 主程序起不来疑似 PLC 拖累 | 不可能——设计上全兜底，先查别处 | §1 |

## 1. 架构速记（谁在哪个线程干什么）

```
PLCConnectorManager (单例, backend/services/plc/manager.py)
 ├─ 每条启用连接 → PLCPointEngine 一线程 (point_engine.py)
 │    轮询 driver.read_all() → 值缓存 → 边沿检测 → 规则命中 → submit_actions
 ├─ 动作执行线程 (全连接共享): rule_actions.py 注册表 (bind_sn/switch_project/...)
 └─ dispatch_event: mes_hooks.py 的 cycle_start/cycle_end → write_rules → 写队列
```

不变量：检测主流程永不直接碰 PLC socket（读走值缓存、写走队列）；驱动惰性 import，
缺库只影响对应驱动（`GET /api/v1/plc/drivers` 的 `available` 字段可查）；
无启用连接时 manager 零线程零开销。**主程序启动失败绝不可能是 PLC 连接器引起**（全兜底）。

## 2. 连接不上（状态不翻 connected）

1. `POST /api/v1/plc/connections/{id}/test` —— 不启用也能试连，报错信息直读
2. `GET /api/v1/plc/drivers` —— 看该驱动 `available`，False 说明依赖库没装
   （出厂包应齐装：python-snap7 / pymcprotocol / pycomm3 / asyncua；FINS 内置、Modbus 用既有 pymodbus）
3. S7 常见坑：S7-300/400 通常 rack 0 / slot 2；S7-1200/1500 是 slot 1 且要在 TIA 开
   "允许 PUT/GET 通讯" + 去优化块（绝对地址访问必须非优化 DB）
4. 断链后引擎自动指数退避重连（runtime.counters.reconnects 递增）；`options.on_disconnect.alarm_event` 可配断链报警

## 3. 值不对 / 配置错误

- 地址方言（写错直接 config_error）：S7 `DB1200.DBX0.2 / DBW2 / STRING@6`；
  Modbus `hr:100 / co:5 / ir:0 / di:2`（0 基）；MC `D100 / M10`；FINS `DM100 / CIO50`；
  EIP tag 名；OPC UA `ns=2;s=...`
- 数值离谱 → 换 `byte_order`（big/little/word_swap/byte_swap 四种全在 UI 下拉里）
- 字符串乱码 → `string_s7`（S7 带头字节）vs `string_fixed` vs `string_cstr` 选错，或 encoding 该用 gbk
- 编解码是纯函数 `backend/services/plc/point_codec.py`，可直接 REPL 复现：`decode(raw, point)`
- write-only 点位（dir=write）**按设计不进读值缓存**，实时监视显示 `—` 是正常的，验证写链路看 IO 日志

## 4. 触发规则不触发 / bind_sn 不生效

- **边沿要有"边"**：rising 需要先看到 0 再看到 1。信号早已是 1 → 永不触发（mock 联调同理：先 mock-set 0 再 1）
- debounce_ms 内抖动被吃掉；min_interval_ms 内重复触发被拦（IO 日志会写"触发"行，没有就是没命中）
- `runtime.counters.rule_fires` 是权威计数；`GET /connections/{id}/logs` 看「触发」条目
- bind_sn 走 `simulate_scan` 扫码链路：**工位必须有激活项目**，否则 ScannerService 静默丢弃
  （后端日志有 `project_id empty` 字样）。去重/防呆/MES 推送全继承扫码链路行为
- switch_project 复用 v3.30 统一匹配器 `project_match.resolve_project_id_by_spec`：对照表精确 → 通配符 → 可选同名匹配（`match_by_name`）。`mapping` 键是字符串（PLC 读出 int 自动转串）、值是 project_id；没命中走 `unknown` 策略（alarm/ignore）

## 5. 写回不生效（cycle_end / heartbeat / 回执）

- write_rules 的 `channels` 过滤：配了 `[0]` 则工位 1 的周期不写。不配 = 全工位都写
- `value_map` 匹配的是事件 ctx 的 `source` 路径值（如 `result` = "OK"/"NG"，大小写敏感）；没命中走 `default`
- `reset_after_ms`：写 1 后自动复位造脉冲——PLC 侧要在脉冲宽度内采到
- 挂点在 `mes_hooks.py` 的 `_handle_cycle_start/_handle_cycle_end`（enqueue 级非阻塞）；
  与 MES 推送解耦：gateway 熔断/集群 skip **不影响** PLC 写回
- 心跳：`{"on": "heartbeat", "period_ms": 1000, "writes": [{"point": "...", "value": "toggle"}]}`；
  反向 PLC 心跳监视在 `options.heartbeat_watch`（point/timeout_s/action）

## 6. mock 联调剧本（无硬件全链路）

```
1. 方案模板 → 「虚拟 PLC (联调/演示)」→ 保存并启用
2. mock-set read_done=0 → product_no=SN001 → read_done=1  (先 0 后 1 造上升沿!)
3. 看 rule_fires+1、IO 日志「触发 bind_sn → 工位0 编号 SN001」、回执位写 1 后自动复位
注意: mock store 按 store_id 进程级共享, 上一轮残值会吃掉边沿 → 剧本永远先复位
```

自动化资产：`tests/test_plc_connector.py`（39 单测）/ `tests/e2e_browser/test_plc_panel.py`（CI e2e）/
`tests/manual_uat/plc_connector_uat.py`（可见浏览器 UAT）。

## 7. 扩展点（插件）

- 新驱动：`backend/services/plc/drivers/__init__.py` 的 `register_plc_driver()`（插件可调，可覆盖内建实现做客户怪癖适配）
- 新动作：`rule_actions.py` 的 `ACTION_REGISTRY`（插件可注册自定义 `do`）
- 新客户范式沉淀：`presets.py` 的 `BUILTIN_TEMPLATES` 加一项（纯数据）
