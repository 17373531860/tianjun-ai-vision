# 集群 Collector 深潜

> **类型**：explanation（internals 深潜）
> **本文不讲**：集群 UI 配置项（→ Settings/ClusterPanel）；副机部署脚本
> **与代码冲突时**：以代码为准。
> **读码依据**：`backend/services/cluster_collector.py`（1205 行）+ `mes_hooks._cluster_dispatch`

Disclaimer：给维护者看；不是规范；版本间会变。

## 职责

1. 接收本地 + 远程工位的 cycle 结果
2. 按 `box_serial` 汇总所有工位数据到 `box_aggregations`
3. 工位到齐 → 触发 MES Gateway `box_complete` 推送
4. 超时未齐 → 按策略推送不完整数据或告警

## 角色模型

| 角色 | 行为 |
|---|---|
| master（主机） | 收副机 HTTP 上报；维护聚齐状态；触发 box_complete |
| slave（副机） | cycle_end 后上报主机；本机可不直接推 MES（wait_all） |
| 单机 | cluster 关闭，cycle_end 直接 gateway |

配置持久化：`cluster_config` 表 + `ClusterPanel` UI。

## 数据流（与 MES Hook 衔接）

```
mes_hooks._handle_cycle_end
  → 预 commit 释放 SQLite 写锁（v2.7.9，否则副机写 aggregation 锁 30s+）
  → _cluster_dispatch(channel_id, cycle_context, ...)
       slave: requests.post → master /api/v1/cluster/report
       master: 本地 merge → BoxAggregation 行更新
  → 若本 box 工位齐 → assemble box_complete ctx → gateway.dispatch
  → 若 wait_all 且未齐 → 跳过本机 cycle_end 直推
```

## 关键函数

| 函数 | 行号区间 | 职责 |
|---|---|---|
| `_merge_cycle_context` | L52 | 同工位多路上报合并 |
| `_build_sub_report` | L29 | 前端分路展示快照 |
| `receive_station_report` | 文件中部 | 副机上报入口 |
| `check_box_complete` / 超时 | 文件后部 | 聚齐判定与超时策略 |

（精确行号以 `_reading_notes` 补全后为准；改代码后请更新本节指针。）

## 超时与健康

- 副机心跳超时：主机标记失联，策略见 `debug-cluster` skill
- 计时/面板刷新间隔：v3.29+ 去硬编码，进 SystemConfig

## 概念 → 文件 → 入口

| 概念 | 文件 | 入口 |
|---|---|---|
| 集群服务 | `cluster_collector.py` | 模块级 collector 单例 |
| HTTP 路由 | `backend/api/cluster.py` | `/cluster/*` |
| MES 侧分发 | `mes_hooks.py` | `_cluster_dispatch` L1585 |
| 数据模型 | `mes_models.py` | `ClusterConfig`, `BoxAggregation`, `BoxSummary` |

## 改前必读

- 与 mes_hooks 的 commit 顺序：必须先释锁再 cluster 写（注释 L1508）
- 插件 hook `box_complete` 见 `fire_plugin_hook` 接入点（REG-5 已由插件平台覆盖）
