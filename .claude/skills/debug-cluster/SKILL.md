---
name: debug-cluster
description: "诊断集群主从模式：ClusterCollector 心跳 / box_serial 聚齐 / 工位上报 / 超时策略 / 多机汇总后推送 MES。当主从节点失联、box 数据不齐、box_complete 不触发 MES 推送、副机心跳超时时使用。"
argument-hint: "[问题现象]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, mcp__sequential-thinking, mcp__sentry"
---

# debug-cluster: 集群主从模式诊断

> 集群（Cluster）= 多台机器各自跑天军，通过一台主机汇总每个 box（一个产品箱体）的所有工位数据，全到齐后才一次性推送 MES Gateway 的 `box_complete` 事件。
> 单机多通道**不是**集群，那是 ChannelManager（用 `debug-channel` skill）。

问题现象: $ARGUMENTS

---

## 一、子系统全貌

| 角色 | 文件 | 行数 | 职责 |
|---|---|---:|---|
| API | `backend/api/cluster.py` | 295 | 配置 / 上报 / 状态 / 心跳，10 端点 |
| 服务核心 | `backend/services/cluster_collector.py` | ~1100+ | 单例 collector，所有汇总逻辑都在这里（**最大文件之一**）|
| ORM | `backend/models/mes_models.py` 中 `ClusterConfig` / `BoxAggregation` / `BoxSummary` | — | 三张表 |
| MES Gateway 集成 | `backend/services/mes_hooks.py` | — | box_complete 触发后调 MES Gateway |
| 前端 | `frontend/src/views/MES/cluster/*` 或 `frontend/src/api/cluster.js` | — | 配置面板 / 状态展示 |

> **路由前缀 `/api/v1/cluster/*`**

---

## 二、关键概念

### 2.1 主从角色

| role | 行为 |
|---|---|
| `master`（主） | 接收所有副机 + 自己的工位 cycle 上报，按 box_serial 聚合，全到齐后调 MES Gateway box_complete |
| `slave`（副） | 周期性向 master_url 发心跳；自己的 cycle 不直接推 MES，而是通过 `/api/v1/cluster/report` 上报到主机 |
| 单机模式 | role 留空 / `enabled=false`，cluster 路径完全不参与 |

### 2.2 ClusterConfig 表（id=1，全局唯一）

| 字段 | 含义 |
|---|---|
| `enabled` | 集群开关 |
| `role` | `master` / `slave` |
| `master_url` | 副机用，指向主机 `http://host:8001` |
| `station_id` | 本机站点编号（全集群唯一） |
| `expected_stations` | 主机配置：列出所有期望站点 ID（数组） |
| `sync_mode` | `wait_all`（等齐推） / `timeout_push`（超时推不齐数据） |
| `timeout_sec` | box 等齐的超时秒数 |
| `timeout_push` | bool；超时是否仍推 MES |
| `channel_station_map` | 一台机器多通道时，channel_id → station_id 的映射（dict） |
| `station_result_strategy` | 多路上报合并：`worst`（任一 NG 即 NG）等 |

### 2.3 box_serial 与 BoxAggregation

- 每个 box（产品箱）有唯一 `box_serial`（来自扫码或 MES 工单分配）
- 每个工位 cycle 结束时，会以 `(box_serial, station_id)` 作为 key 写入 `BoxAggregation`
- 主机聚合：当某 `box_serial` 下所有 `expected_stations` 都到齐 → 写入 `BoxSummary` → 触发 MES Gateway box_complete

### 2.4 数据流

```
[副机] cycle_end → 本机 mes_hooks → 检查 cluster.enabled
                                           ↓ slave
                                  POST <master_url>/api/v1/cluster/report
                                           ↓
[主机] /api/v1/cluster/report → cluster_collector.handle_station_report
                                           ↓
                                  写 BoxAggregation
                                           ↓
                                  检查全部 expected_stations 到齐？
                                           ↓ 是
                                  写 BoxSummary
                                           ↓
                                  调 MES Gateway box_complete
```

### 2.5 关键函数（cluster_collector.py）

| 函数 | 作用 |
|---|---|
| `get_cluster_collector()` | 单例入口 |
| `get_config(db)` | 读 ClusterConfig.id=1 |
| `invalidate_config_cache()` | PUT 配置后必须调 |
| `handle_station_report(...)` | 主机收到上报后处理 |
| `_check_and_dispatch_box(box_serial, ...)` | 主聚合判断 |
| `record_slave_heartbeat(...)` | 主机维护副机活性 |
| `get_aggregation_status()` | 状态查询，前端轮询 |
| `_build_sub_report(ctx, ...)` | 把 cycle_context 摘成"分路展示快照"，前端用 |
| `_merge_cycle_context(old, new, merged_is_good)` | 同站点多路视觉合并（如 B 工位有两路相机） |

---

## 三、API 端点（10 个，前缀 `/api/v1/cluster/`）

| Method | Path | 作用 |
|---|---|---|
| GET | `/config` | 读配置 |
| PUT | `/config` | 写配置（前端配置面板） |
| POST | `/report` | **副机推送 cycle 上报** |
| POST | `/heartbeat` | **副机心跳** |
| GET | `/status` | 主机查所有副机状态 + 当前未齐 box 列表 |
| GET | `/aggregations` | 历史聚合列表 |
| GET | `/aggregations/{box_serial}` | 单 box 详情 |
| GET | `/summaries` | 历史 box_summary 列表 |
| POST | `/test-push` | 测试 MES Gateway 连通 |
| GET | `/report-status` | **v3.49+ 副机上报链路状态**（内存队列深度/落盘积压/累计补发，ClusterPanel 状态区数据源） |
| ... | （以代码为准 grep `^@router.`） | |

---

## 四、常见问题排查

### 4.1 副机心跳显示离线

**现象**：主机的 `/api/v1/cluster/status` 看不到副机，或副机标 `offline`。

**排查**：

1. 副机能 ping 通 master_url？
   ```bash
   curl -v <master_url>/api/v1/cluster/heartbeat -X POST -H "Content-Type: application/json" \
     -d '{"station_id":"<id>","port":8001}'
   ```
2. 副机 ClusterConfig.role 是否 `slave`？master_url 是否对？
3. 副机后端日志：grep `heartbeat` 看是否在调
4. 主机防火墙是否开 8001？
5. 心跳频率默认（看 cluster_collector.py 常量）通常 5-10 秒，超过 3 倍周期判离线

### 4.2 box 数据不齐 / 不触发 MES

**现象**：box 在主机的 `/aggregations` 里能看到，但 expected_stations 长期不齐，box_summary 不生成。

**排查**：

1. 看 `BoxAggregation` 表当前 box_serial 下有哪几个 station_id
   ```sql
   SELECT station_id, channel_id, is_good, ts FROM box_aggregations
   WHERE box_serial = '<serial>' ORDER BY ts;
   ```
2. 对照 `expected_stations`：缺哪个？
3. 缺的工位是否：副机离线 / 副机的 box_serial 不一致 / 该工位还没生产到这个 box
4. **常见坑**：`expected_stations` 写的是 `["A","B","C"]`，但副机 station_id 写的是 `"a"`（大小写不一致）→ 永远不齐
5. 超时检查：是否 `sync_mode=timeout_push` 但 `timeout_sec` 设太大（如 3600 秒）

### 4.3 box_complete 触发了但 MES 未收到

- 看 `cluster_collector.py` 中调 `mes_gateway.dispatch_box_complete`（或类似）的位置
- 看 `mes_connections` 表的 connection 配置 + `MESComm Logs`
- 看 MES Gateway 的 `_apply_auth_to_headers` 是否生效
- 用 `POST /api/v1/cluster/test-push` 测连通

### 4.4 通道-站点映射不对

**场景**：一台机器双通道，分别属于 A 工位和 B 工位。

**配置**：
```json
"channel_station_map": {"0": "A", "1": "B"}
```

**常见坑**：
- 写成 `{"0": "A"}` 漏配通道 1 → 通道 1 的 cycle 不会上报
- 通道 ID 用 int 还是 string？JSON 里是 string，代码里读出来要 cast
- 多路视觉同 station：`station_result_strategy="worst"` 表示任一 NG 算 NG

### 4.5 副机 cycle 既上报集群又推自己 MES（重复推送）

**应当**：当 `cluster.enabled=true` 且 `role=slave` 时，副机 mes_hooks 应**不直接调 MES Gateway**，而是 cluster 上报。

**排查**：
- grep `cluster.enabled` 在 mes_hooks 中的判断点
- 检查 `MESHookManager._handle_cycle_end` 中是否有 `if cluster_role == 'slave': return after report`
- 看 `mes_comm_logs` 是否有从副机直接发 cycle_end 给 MES 的记录（应该没有）

---

## 五、配置面板（前端）

`frontend/src/views/MES/*/cluster*.vue` 或在 MES 管理页一个标签

- 切换 master/slave/disabled
- 配 master_url（仅 slave）
- 配 station_id
- 配 expected_stations（仅 master）
- 配 channel_station_map / 同步模式 / 超时

---

## 六、调试入口（按问题类型）

| 现象 | 第一步看 | 第二步看 |
|---|---|---|
| 副机离线 | 网络连通性 + master_url | 心跳后端日志 |
| box 不齐 | BoxAggregation 表当前 box | expected_stations vs station_id 大小写 |
| box_summary 不生成 | sync_mode + timeout_sec | _check_and_dispatch_box 逻辑 |
| MES 未收到 | mes_comm_logs | mes_connections 配置 |
| 通道映射错 | channel_station_map JSON | 通道 ID 类型 |
| 数据合并错 | _merge_cycle_context 调用 | station_result_strategy |
| 主机重启后状态丢 | BoxAggregation 表是否落盘 | get_aggregation_status |

---

## 七、关键文件速查

```
backend/api/cluster.py                 (295 行 / 10 端点)
backend/services/cluster_collector.py   单例核心 (~1100+ 行)
backend/models/mes_models.py 中:
  ClusterConfig / BoxAggregation / BoxSummary
backend/services/mes_hooks.py           cycle_end hook 集成点
frontend/src/api/cluster.js             前端 API 客户端
```

---

## 八、相关 skill

- `debug-mes` — Gateway / Hook 系统问题先看那里
- `debug-channel` — 单机多通道问题
- `add-api-endpoint` — 加新 cluster 端点
- `modify-model` — 改 BoxAggregation / BoxSummary 字段

---

## 九、已知坑

1. **station_id 大小写敏感**：`expected_stations` 与各副机的 `station_id` 严格相等比较
2. **channel_station_map JSON 类型**：JSON 标准下 key 必须是 string，不要写成 `{0: "A"}`（无效）
3. **副机模式仍要本机配置 MES Gateway**：因为 master 主推 box_complete，但**部分事件**（如 cycle_end 单工件实时）按设计仍可能从副机推（看具体 mes_hooks 实现，慎判）
4. **cluster 单例**：进程重启后 `BoxAggregation` 仍在 DB，可恢复，但内存中的活性状态会重置——`record_slave_heartbeat` 重新建立后才知道副机存活
5. **超时未齐的 box**：`timeout_push=true` 时会推不完整数据；`timeout_push=false` 时只产生 BoxSummary 但不推 MES

---

## 十、v3.49.0 补充：副机上报异步化 + 落盘重放（捷昌整改 WS2）

**动机**：老路径副机→主机上报是结算路径内联 HTTP——主机慢/断网直接拖垮副机结算节奏，断网期间上报数据丢失。

**机制**（`cluster_collector.py`）：

- 上报走**独立发送线程 + 内存队列**，结算路径只入队即返回；`report_async`（默认开）配置进 `/cluster/config`，显式关=回退旧内联同步
- 发送超时 `report_timeout_sec` 可配（默认 10s，旧版硬编码）
- 发送失败落盘 **`cluster_report_spool.jsonl`**（数据目录下），主机恢复后按序重放，重放计数进 `spool_replayed_total`
- 可观测：`GET /api/v1/cluster/report-status` 返回 `queued`（内存队列深度）/ `spooled`（落盘积压条数）/ `spool_replayed_total`（累计补发）；ClusterPanel「上报链路状态」区展示同口径三数字

**排查口径**：

- 副机"结算又开始卡" → 先查 `report_async` 是否被关回同步；再看 `report-status` 的 `queued` 是否持续增长（主机吞吐跟不上）
- 断网恢复后主机数据缺一段 → 看副机 `spooled` 是否清零、`spool_replayed_total` 是否增长；spool 文件残留=重放失败，看副机日志
- 回归测试：`tests/test_cluster_report_async.py`（异步入队/落盘/重放）+ `tests/test_cluster_timing_b2.py`
