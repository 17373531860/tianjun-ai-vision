# MES Hook 链路深潜

> **类型**：explanation（internals 深潜）
> **本文不讲**：Gateway 六种适配器协议细节（→ `debug-mes` skill）；WMax 35+ 端点（→ `wmax` 路由）
> **与代码冲突时**：以代码为准。
> **读码依据**：`docs/dev/_reading_notes/02_mes_domain.md`（2026-07-05 精读 mes_hooks/scanner/gateway 等）

Disclaimer：给维护者看；不是规范；版本间会变。

## 架构一句话

检测状态机（source）与 MES 子系统之间的**唯一异步桥** = `MESHookManager`（`backend/services/mes_hooks.py`）。5 个对外 hook 全部经内存队列由**单条** `mes-hook-worker` 消费，保证不阻塞检测帧率。

## 数据结构（channel_id 为键）

| 状态 dict | 语义 | 写入 | 读出 |
|---|---|---|---|
| `_pending_workpiece` | 已扫码未开检 | `_handle_scan` | `_handle_cycle_start` pop |
| `_inspecting_workpiece` | 当前在检工件 | cycle_start / scan_pair promote | cycle_end / session_end / 通道移除（**严格配对，不变量 #14**） |
| `_scan_pair_active` | 码-码窗口当前码 | scan_pair 首扫 | 次扫 settle + promote |
| `_active_orders` | 通道活跃工单 | session_start；v3.38 起入站开工也可经 `on_external_order_changed` 对**运行中**通道回填/顶替（"最新开工为准"，治先开检测后开工时工单字段推空） | cycle 关联 |
| `_disabled_channels` | 禁用扫码联动闭包 | set_channel_disabled | 各守门点短路 |

## 链路：扫码 → 绑定 → 结算 → 推送

```
ScannerService 收到条码
  → on_scan_received (critical 入队)
  → _handle_scan:
       RFC11 workpiece_flow 优先 → 若命中则跳过本通道 scan_pair 状态机
       → duplicate_scan_action / pending 队列
       → 注册 Workpiece + ScanLog
       → scan_mode D → LOFF；scan_pair → _handle_scan_pair_event
  → on_cycle_start → mark_inspecting + link_to_cycle + _inspecting_workpiece[ch]=wp
  → on_cycle_end → set_result / 缺陷 / 预 commit 释锁 → _cluster_dispatch → gateway.dispatch
  → export_realtime / export_snapshot 触发器
```

### scan_pair 码-码闭环（要点）

- 首码开窗口 + promote pending → `_inspecting_workpiece`
- 次码：先 settle 上一窗口（调 source `settle_for_scan_pair`）再 promote 新码
- 超时：`threading.Timer` → 强制 NG 结算
- v3.4.2 hotfix：worker 异步与 promote 竞态 → cycle_end 反查 WorkpieceInspection by cycle_id

## `_enqueue` 语义（不变量 #15）

队列满时**绝不阻塞**调用方：

- `critical=True`：立刻 spill 到 `mes_hook_spool.jsonl`，worker 空闲时回放
- 非 critical：直接丢弃
- 白名单 spillable：五个 `_handle_*` 核心 handler

## 与集群的衔接

`_cluster_dispatch`（L1595+）：

- master：本地写 box_aggregations
- slave：HTTP 上报主机
- `wait_all` 模式：本机跳过直接 gateway 推送，等 box_complete

深潜见 [cluster-collector.md](cluster-collector.md)。

## Gateway 外推（摘要）

- 模板：自研 `{key.path}` 占位符替换（**不是 Jinja2**）
- 6 种适配器（v3.35 起）：HTTP 三种 + Modbus RTU + **database 数据库直写**（达梦/MySQL/SQLServer/PostgreSQL/SQLite，注册于 `mes_adapters/__init__.py` 的 `_REGISTRY`，给没有 HTTP 接口、只给库表的客户 IT；复用同一套模板系统产 payload）
- B1② 异步外推（默认关）：每工位 `ThreadPoolExecutor(1)`，积压上限 200
- **v3.38 每连接独立 commit**：`dispatch` 循环里每推完一个连接立即 `db.commit()`，单连接失败不再波及同事件其余连接，也不让通讯日志攒在长事务里与检测热路径抢 SQLite 写锁（川南"框冻结"根因之一）
- **v3.38 推送熔断器**：按连接在内存里计连续失败，达到阈值后一段窗口内**跳过**该连接的自动推送（离线的客户 MES 不再每周期白等超时）；复位口三个——保存连接配置、测试连接成功、手动推送（手动推送**绕过**熔断器，本身就是恢复探路）；熔断状态随连接序列化给前端提示"端点疑似离线"

## 概念 → 文件 → 入口

| 概念 | 文件 | 入口 |
|---|---|---|
| Hook 管理器 | `mes_hooks.py` | `get_mes_hook()` |
| 扫码服务 | `scanner.py` | `ScannerService.on_barcode` |
| 外推 | `mes_gateway.py` | `MESGatewayService.dispatch` |
| 入站 MES | `mes_inbound.py` | REST 开工/完工/报警 |
| 串行流水线 | `workpiece_flow_coordinator.py` | RFC11 优先于 scan_pair |

## 改前必读注释坑（摘录）

- L1518：cycle_end 前预 commit，否则集群写 box_aggregations SQLite 锁 30s+
- L1107：`clear_pending_scan(force)` 原子 pop，防与 worker 抢 wp_id
- L1186：WorkpieceFlow 通道与 scan_pair **互斥**
