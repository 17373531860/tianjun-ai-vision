# 10 — 工位组（ChannelGroup）主程序原生 RFC

> 适用版本：基于 v3.12.x 主线
> 本文目的：把"单机内多工位联动结算"这件事作为主程序**原生概念**落地。这是客户本轮需求 4 的承载，也是 RFC 09（插件平台升级）里"工位组留口"的下游 RFC。
>
> **关键边界**：
> - 工位组是**单机内多 channel 的联动**，跟集群（cluster）的"跨机 box 聚齐"是**不同维度**
> - 工位组的客户级变种走 RFC 09 的 hook + 主动 API（插件），主程序只做基础设施
>
> 阅读前置：`design/00_overview.md`、`design/09_v3_13_platform_upgrade_rfc.md`、AGENTS.md 第六·四节（多通道管理）+ 第六·九节（集群汇总）。

---

## 一、动机与现状

### 1.1 这次的真实需求

客户需求 4：**双工位场景下，A 站如果判 NG，B 站也要同步判 NG（同一物件在两个角度同时被检测）**。

延伸场景（一并考虑，但本 RFC 不全部落地）：
- 双工位"或"逻辑：A 或 B 任一 NG → 整组 NG
- 双工位"与"逻辑：A 和 B 都 OK → 整组 OK
- 双工位"主从"逻辑：B 站作为 A 站的复核工位，B 判定权重大
- 4 工位"多角度"逻辑：4 个角度齐 OK 才放行

### 1.2 现有"工位"概念盘点（必须先看清楚再设计）

| 概念 | 维度 | 主要文件 | 说明 |
|---|---|---|---|
| **ChannelManager.set_channel_count** | 单机内通道数（1/2/4） | `backend/api/channel_manager.py` | 每个通道一个独立 `VideoSourceManager` 实例 + 独立 Project + 独立 cycle |
| **VideoSourceManager.channel_id** | 单个通道的运行时实例 | `backend/api/source.py` | 持有所有结算状态、当前 cycle、step 序列 |
| **cluster_config（多机集群）** | **跨机**，工位 = "station_id" | `backend/services/cluster_collector.py` + `mes_models.ClusterConfig` | host 收 slave 数据，按 `box_serial` 聚齐后推 MES。**跟单机多通道完全不同的概念** |
| **simultaneous_groups** | **同周期内**多步骤组 | `pipeline_config.simultaneous_groups` | "A+B+C 三个步骤同时出现算一组" — 是步骤组，不是工位组 |
| **scanner.broadcast_channels** | **一扫码器服务多工位** | `mes_models.ScannerDevice.broadcast_channels` | 输入侧的广播，不是结算侧的联动 |
| **AlarmManager 的 shared_with** | 多工位共享报警灯柱 | `backend/api/alarm.py` | 输出侧的合并，也不是结算侧的联动 |

**结论**：当前主程序**没有任何**"多通道结算联动"的机制。每个通道的 cycle 结算完全独立，互不感知。

### 1.3 客户级 workaround 评估

不做工位组、纯走插件能不能解决？答：**可以但不优雅**：
- 插件可以在 RFC 09 的 `cycle_end` hook 里查另一个通道的 last cycle 结果，然后用 `PluginHost.write_*` 改回另一通道的 cycle 记录
- 但这是**事后追改**：A NG 时 B 可能还没结算 / 已经结算成 OK 了，跨通道时序混乱
- 改写另一通道 cycle 记录涉及 channel scope 越权，违反 G1 的命名空间隔离原则

所以工位组必须做成**主程序原生的状态机内建联动**，不能甩给插件。

---

## 二、目标与反目标

### 2.1 目标

| 目标 | 说明 |
|---|---|
| **支持单机内 2/4 通道的结算联动** | 满足客户需求 4 |
| **不破坏现有 5 种 settlement_mode** | sequential / detection / custom / tracking / per_item / last_first 全部仍能独立运行 |
| **不破坏 cluster 跨机聚齐** | cluster 是跨机维度，工位组是单机维度，两者正交（但要明确互操作边界） |
| **给插件留 hook + 主动 API** | 工位组的客户变种走插件（RFC 09 第七节已埋桩） |
| **零差异默认**：项目不开工位组时，所有通道仍独立结算（与 v3.12 完全一致） |

### 2.2 反目标

| 反目标 | 原因 |
|---|---|
| ❌ 不做"跨机+跨通道"双层组（即一台 host 上的工位组合并 slave 上的工位组） | 工程复杂度爆炸，客户场景没出现 |
| ❌ 不做工位组动态调整（运行时改成员） | 必须停机改配置后重启 |
| ❌ 不做工位组级 per_item / last_first 等高阶模式 | 见第四节互斥关系 |
| ❌ 不做"工位组级别的录像合并 / 视频拼接" | 录像还是按 channel 各自走 |
| ❌ 不内建"主从工位"权重 | 客户级变种走插件 |

---

## 三、数据模型

### 3.1 新增：`channel_groups` 表

```python
# backend/models/models.py
class ChannelGroup(Base):
    """单机内多通道结算联动组（v3.13+）。

    与 cluster_config 的差异:
    - cluster_config 是 "跨机聚齐 box", 每台机一行配置
    - channel_groups 是 "同机内 channel 联动结算", 一台机可以有 0~N 个组
    """
    __tablename__ = "channel_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False, unique=True)  # 例 "Group-A" / "front-and-back"
    member_channel_ids = Column(JSON, nullable=False)  # 例 [0, 1] 或 [0, 1, 2, 3]
    settle_strategy = Column(String(32), nullable=False, default="synchronized_any_ng")
    # 见第四节策略枚举
    timeout_ms = Column(Integer, nullable=False, default=5000)
    # 组内成员结算等待对方的最长时间，超时按 timeout_action 处理
    timeout_action = Column(String(32), nullable=False, default="fallback_independent")
    # 超时行为: fallback_independent / force_ng
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

### 3.2 现有表的小改动

| 表 | 字段 | 改动 |
|---|---|---|
| `detection_cycles` | 新增 `channel_group_id: Integer, nullable=True` | 标记该 cycle 属于哪个工位组（独立通道为 NULL） |
| `detection_cycles` | 新增 `group_settled_with: JSON, nullable=True` | 记录"同组联动结算"涉及的其它 cycle_id 列表（用于审计 / 回溯） |
| `detection_cycles` | 新增 `group_settle_result: String(8), nullable=True` | 组级最终结果（OK/NG），与原 `is_good` 同时存在但语义不同 |

### 3.3 迁移路径

- 在 `backend/main.py: migrate_database()` 加：
  - `CREATE TABLE IF NOT EXISTS channel_groups (...)`
  - `ALTER TABLE detection_cycles ADD COLUMN channel_group_id INTEGER` （SQLite/PG 都兼容）
  - 同理另外两个字段
- 默认 channel_group_id = NULL → 老数据完全无影响

---

## 四、业务规则：结算策略

### 4.1 `settle_strategy` 枚举（首批 4 种）

| 策略 | 触发时机 | 联动逻辑 |
|---|---|---|
| `synchronized_any_ng` | 组内任一通道 cycle 结算 NG | 立即广播给其它成员：把对方当前 cycle 也判 NG（不等结算完成）。**满足客户需求 4** |
| `synchronized_all_ok` | 组内所有通道 cycle 都结算 OK 才放行 | 组内任一 NG → 全 NG；全 OK 才整组 OK |
| `independent`（默认） | 不做联动 | 等价于当前行为（每通道独立结算） |
| `master_slave` | 指定主通道 | 主通道结算决定整组结果，其它从通道**只录像不判定** |

每种策略附带超时机制：
- 等待时间窗 = `timeout_ms`
- 超时后按 `timeout_action` 执行：
  - `fallback_independent`：放弃联动，各 cycle 按原结算保留
  - `force_ng`：超时的成员强制判 NG

### 4.2 与现有 settlement_mode 的互斥关系

工位组激活的通道，其 `pipeline_config.settlement_mode` 的可选范围**受限**：

| settlement_mode | 工位组兼容性 | 原因 |
|---|---|---|
| `first_step` | ✅ 兼容（默认） | 行为可预测 |
| `last_step` | ✅ 兼容 | 行为可预测 |
| `no_anchor` | ⚠️ 受限：组超时窗口与无锚超时叠加，需测试 | 谨慎放开 |
| `last_first` | ❌ 互斥 | last_first 双锚状态机自带跨周期语义，加联动会冲突 |
| `per_item`（逐件覆盖） | ❌ 互斥 | per_item 结算节奏与组内同步窗口冲突 |
| `simultaneous_groups`（同周期组）| ✅ 兼容（同周期组是周期内的，工位组是跨通道的） | 不冲突 |
| `cross_cycle`（跨周期组） | ❌ 互斥 | 双独立状态机，叠加联动会出歧义 |

强制校验位置：
- **前端**：Project 编辑页保存时拒绝
- **后端**：`source_project_config_apply.py` 兜底校验，违规直接拒绝激活

### 4.3 与 cluster 集群的关系

- 工位组（channel_groups）：**单机内**通道联动
- cluster：**跨机**站点聚齐
- 两者**正交**：一台机可以同时是 cluster slave + 内部有工位组
- 工位组的"组级结算结果"按 `channel_station_map` 投影到 cluster 的 `station_id` 上后参与 box 聚齐
  - 例：本机 channel 0+1 组成 Group-A，station_id="B"，则组级结算结果作为 station=B 的 BoxAggregation 数据上报 host

---

## 五、运行时架构

### 5.1 新增组件：`ChannelGroupCoordinator`

新文件 `backend/services/channel_group_coordinator.py`：

```python
class ChannelGroupCoordinator:
    """单机内工位组运行时协调器（单例）。

    挂在 ChannelManager 之下, 持有所有 active 工位组的当前 "等待结算" 状态.

    职责:
    1. 接 VideoSourceManager 的 cycle 结算信号 (通过新 hook on_cycle_settled)
    2. 按 settle_strategy 决定是 "等待对方" / "立即广播" / "超时处理"
    3. 把组级结算结果回写到组内所有成员的 detection_cycles 记录
    4. fire RFC 09 第七节的两个 hook (channel_group_settle_start / done)
    """

    def __init__(self, channel_manager): ...
    def reload_groups(self, db): ...  # 配置变化时重新加载
    def on_cycle_settled(self, channel_id, cycle_id, is_good): ...  # VSM 调
    def _broadcast_to_group(self, group, trigger_channel_id, trigger_cycle_id, result): ...
    def _wait_with_timeout(self, group, ...): ...
    def get_group_state(self, group_id) -> dict: ...  # 供 API 查询
```

### 5.2 改造点：`VideoSourceManager` cycle 结算末尾

`source_session_lifecycle_mixin.py` 中 `end_cycle` 写库后：

```python
# 现有: fire_plugin_hook("cycle_end", ...) / fire_plugin_hook("pre_cycle_end", ...)
# 新增: 通知 ChannelGroupCoordinator
from backend.services.channel_group_coordinator import get_coordinator
coordinator = get_coordinator()
if coordinator:
    coordinator.on_cycle_settled(
        channel_id=self.channel_id,
        cycle_id=cycle.id,
        is_good=cycle.is_good,
    )
```

**注意**：Coordinator 的 `on_cycle_settled` 是同步调用但不阻塞主流程（内部用线程池调度联动逻辑），结算线程立刻返回。

### 5.3 改造点：`ChannelManager.set_channel_count`

- 通道数变化时，必须调用 `ChannelGroupCoordinator.reload_groups()` 重新加载组配置
- 同时遵守 AGENTS.md 第八·四条不变量：`mes_hook.on_channel_removed(cid) + alarm_router.on_channel_removed(cid)` 旁边再加 `coordinator.on_channel_removed(cid)`

### 5.4 改造点：`detection_cycles` 写回逻辑

组级结算结果回写时，新增 3 个字段：
- `channel_group_id`：标记该 cycle 属于哪个组
- `group_settled_with`：[cycle_id_of_partner_1, cycle_id_of_partner_2, ...]
- `group_settle_result`：组级最终 OK/NG

**写回时机**：
- `synchronized_any_ng`：trigger cycle 写库后立即广播；被广播的成员 cycle 还在进行中 → 设置 `_pending_group_override = "NG"` 标志，下次结算时强制采用
- `synchronized_all_ok`：等所有成员都结算完才回写组级结果
- `master_slave`：从通道结算时直接读主通道当前 cycle 决定

---

## 六、API 设计

### 6.1 新增端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/v1/channel-groups/` | GET | 列出所有工位组 |
| `/api/v1/channel-groups/` | POST | 创建工位组（含成员校验：不能跨已激活的不兼容 settlement_mode 通道） |
| `/api/v1/channel-groups/{id}` | PUT | 更新工位组（含 reload_groups 联动） |
| `/api/v1/channel-groups/{id}` | DELETE | 删除工位组（仅在所有成员 cycle 都结束时允许） |
| `/api/v1/channel-groups/{id}/state` | GET | 查组运行时状态（等待中的成员 / 已结算的成员 / timeout 剩余时间） |

挂载位置：新增 `backend/api/channel_groups.py`，在 `api/__init__.py` 注册到 `/api/v1` 前缀。

### 6.2 前端

新增页面：`frontend/src/views/Settings/ChannelGroupPanel.vue`，作为 Settings 页的一个 tab（与"账号鉴权 Tab"同级）。

Pinia store：新增 `useChannelGroupStore.js`。

---

## 七、给插件留的 hook（与 RFC 09 第七节衔接）

| Hook | 触发位置 | ctx 字段 |
|---|---|---|
| `channel_group_settle_start` | `Coordinator._broadcast_to_group` 开始时 | `group_id / group_name / trigger_channel_id / trigger_cycle_id / member_channel_ids / strategy` |
| `channel_group_settle_done` | 所有成员结算落库 + group_settle_result 写回后 | `group_id / cycle_ids (list) / aggregated_result / member_results (dict)` |

**Returnable 部分**（与 RFC 09 M1.2 一致）：
- `channel_group_settle_start` 接受 `{"override_strategy": "synchronized_any_ng" | "synchronized_all_ok" | "independent"}` 临时改本次结算策略
- 客户级"主从权重 / 多角度复核"等高阶逻辑都走这个 hook 实现，**不进主程序**

### 7.1 PluginHost 主动 API

- `PluginHost.broadcast_to_channel_group(group_id, message)` — 给组内成员发广播消息（落到所有成员通道的 `_plugin_broadcast_queue`，由插件自行消费）
- `PluginHost.query_channel_group(group_id) -> dict` — 查组配置 + 当前运行状态
- `PluginHost.list_channel_groups() -> list[dict]` — 列出所有组

---

## 八、与 RFC 09 的耦合关系

| RFC 09 子任务 | RFC 10 依赖 |
|---|---|
| M1.1 新 hook 接入点 | 新增 `channel_group_settle_start / done` 两个 hook，复用同一 `fire_plugin_hook` 入口 |
| M1.2 Returnable hook | 工位组 hook 复用 returnable 契约 |
| M1.3 PluginHost 主动 API | 工位组贡献 3 个新 API：`broadcast_to_channel_group / query_channel_group / list_channel_groups` |
| M2 UI 扩展 | 工位组 Settings 页 tab 走主程序原生（不是 plugin tab 注入） |
| M3 配置扩展 | 工位组的客户级附加配置（如"主从权重"）走 `plugin_data` 字段挂在 `channel_groups.plugin_data` |

**这意味着**：RFC 10 的实施依赖 RFC 09 的 M1.1 + M1.3 先落地，否则 hook 和主动 API 没载体。

---

## 九、兼容性与迁移

### 9.1 老客户升级路径

- 不创建工位组 → 默认 channel_groups 表为空 → 所有 `detection_cycles.channel_group_id = NULL` → 行为与 v3.12 **位级一致**
- 单工位客户 → ChannelManager 只有 1 个通道 → 工位组功能不可用（前端 Settings 页 tab 灰显）
- v3.12 → v3.13 升级 migrate：仅加 1 张新表 + 3 个新列（含默认 NULL），无破坏性 SQL

### 9.2 与现有功能的兼容矩阵

| 功能 | 工位组兼容性 | 处理 |
|---|---|---|
| MES 工单 / 工件 / 缺陷 | ✅ 兼容 | 组级 NG 时按主程序现有 NG 流程触发 `workpiece_inspection` |
| 扫码器 broadcast_channels | ✅ 兼容 | 扫码器服务多工位 + 工位组结算联动是正交逻辑 |
| 报警 shared_with | ✅ 兼容 | 报警共享灯柱 vs 工位组结算联动是不同维度 |
| cluster 集群 | ✅ 兼容（见 4.3） | 组级结算结果按 station_id 上报 host |
| 自定义导出（v3.5.0+） | ✅ 兼容 | 导出字段新增 `cycle.channel_group_id / .group_settle_result` 进字段中央仓库 |
| 实时规则 cycle_end | ✅ 兼容 | 实时规则可以基于 `group_settle_result` 触发，不依赖单 cycle `is_good` |

---

## 十、测试策略

### 10.1 单元测试

新增文件 `tests/channel_group/test_coordinator.py`：
- `synchronized_any_ng` 触发广播
- 超时按 `fallback_independent` / `force_ng` 行为
- 与 cluster 配置共存场景
- reload_groups 在 channel_count 变化时

### 10.2 集成测试

`tests/channel_group/test_e2e.py`：双通道虚拟源 + 工位组配置 → 制造 A NG → 验证 B cycle 也被标 NG → 验证 MES 推送也按组级结果。

### 10.3 BDD

`tests/features/channel_group.feature`：
- `Given 双工位 + Group-A synchronized_any_ng`
- `When A 站结算 NG`
- `Then B 站当前 cycle 也被标 NG`
- `Then 组级结算结果 = NG`
- `Then MES Gateway 收到一次 box_complete 推送（如启用 cluster）`

### 10.4 回归

- 不开工位组场景下，跑 `tests/plugin_system/test_*.py` 全部 — 必须保持现有 141 通过

---

## 十一、验收标准

| # | 验证 |
|---|---|
| AC-CG-1 | 创建 Group-A 含 channel 0+1，settle_strategy=synchronized_any_ng → 制造 A 站 NG → B 站当前 cycle 在 timeout_ms 内被联动判 NG |
| AC-CG-2 | `detection_cycles.group_settled_with` 字段正确记录联动伙伴 cycle_id |
| AC-CG-3 | 通道 0 用 `per_item` 模式时，前后端拒绝把通道 0 加入工位组 |
| AC-CG-4 | 通道 0 的 settlement_mode 改成 `last_first` 时，若已在工位组内，激活流程报错 |
| AC-CG-5 | 工位组超时 fallback：A 结算 NG 但 B 在 timeout_ms 内未结算 → 按 `fallback_independent` 时 B 保持原行为 |
| AC-CG-6 | 删除工位组的端点在组内任一成员正在 cycle 进行中时返回 409 |
| AC-CG-7 | RFC 09 hook 触发：`channel_group_settle_start` 在 ctx 中带 trigger_channel_id；`channel_group_settle_done` 在 ctx 中带 cycle_ids list |
| AC-CG-8 | cluster 启用场景下，组级结算结果作为 station 数据上报 host，host 的 BoxAggregation 收到 |
| AC-CG-9 | channel_count 从 4 减到 2，被剔除通道所在组自动 disable（不删除配置但 enabled=False） |
| AC-CG-10 | 没创建任何工位组时，所有结算行为与 v3.12 完全一致（pytest 全量跑通） |

---

## 十二、风险与权衡

| 风险 | 缓解 |
|---|---|
| 联动状态机本身的死锁 / 竞态（A 等 B，B 等 A） | Coordinator 用单线程事件循环，所有状态变更串行化 |
| 超时窗口配错 → 客户场景结算混乱 | 默认 5000ms 偏保守；前端配置时强制弹提示"超时窗口不建议小于平均 cycle 时长的 0.5 倍" |
| 通道数动态变化时组内成员悬空 | `set_channel_count` 时强制 reload_groups + 越界成员所在组自动 disable |
| 与 cluster timeout_sec 叠加 → 跨机时延膨胀 | 组级超时 + cluster 超时是串行加总，文档明确建议 group timeout < cluster timeout / 2 |
| 数据迁移失败（老 detection_cycles 多）| ALTER TABLE 加列 SQLite/PG 都是 O(1) 操作，不会卡 |
| 插件通过 Returnable hook 改组级结果 → 跟主程序判定不一致 | `group_settle_result` 字段始终是主程序决定的"原始结果"；插件改的字段挂在 `plugin_data` 子树，与主程序字段分离 |

---

## 十三、实施工作量

| 子任务 | 人天 |
|---|---|
| **CG.1** 数据模型 + migrate | 1 |
| **CG.2** ChannelGroupCoordinator 单线程协调器 | 4 |
| **CG.3** 4 种 settle_strategy 实现 + 超时 | 3 |
| **CG.4** VSM end_cycle 接入 + ChannelManager 联动 | 2 |
| **CG.5** API + 前端 Settings tab + Pinia store | 3 |
| **CG.6** 与 RFC 09 hook 衔接（channel_group_settle_start/done） | 1 |
| **CG.7** PluginHost.broadcast_to_channel_group + query_channel_group + list_channel_groups | 1 |
| **CG.8** 与 cluster 互操作（station_id 映射） | 1 |
| **CG.9** 测试（单元 + 集成 + BDD + 回归） | 3 |
| **CG.10** 文档（用户手册 + 操作手册 + AGENTS.md 第六·五节扩写） | 1 |

合计约 **20 人天**。

依赖：RFC 09 的 M1.1（hook 接入点）+ M1.3（PluginHost 主动 API）必须先落地，否则 CG.6 + CG.7 没载体。

---

## 十四、开放问题

1. **`master_slave` 策略要不要进 v3.13 首批**？倾向 v3.13 只做 `synchronized_any_ng` + `synchronized_all_ok` + `independent`，`master_slave` 推到 v3.14
2. **工位组与 per_item 真的全互斥**？还是后续做"per_item 组级"？倾向 v3.13 互斥，等真客户场景再放
3. **`detection_cycles.group_settle_result` 跟 `is_good` 同时存在 → 报表统计算哪个**？倾向"组内通道 → 算 group_settle_result；独立通道 → 算 is_good"。报表层加 `effective_result` 计算列
4. **配置变更（如改 timeout_ms）要不要等所有 active cycle 结束才生效**？倾向"是" — 改完立即 reload 但当前正在等待中的组状态保持，下次新 cycle 起用新配置
5. **支持运行时切换 enabled** 还是必须重启？倾向运行时切换（reload_groups 已经设计成可重入）

---

## 十五、与 RFC 09 的协同里程碑

| 时间线 | 动作 |
|---|---|
| v3.13.0 | RFC 09 M1.1 + M1.3 + M3.3 落地，RFC 10 工位组**字段 + Coordinator 骨架 + synchronized_any_ng + UI**（约 17 人天） |
| v3.13.1 | RFC 09 M2.1 + M2.2 + M2.3 落地，RFC 10 `synchronized_all_ok` + timeout 边界完善 |
| v3.13.2 | RFC 09 M3 全部落地，RFC 10 `master_slave` 推到 v3.14 |
| v3.14.x | 视客户反馈扩展工位组高阶策略 |

---

**本文件最后更新**：2026-05-28（RFC 10 初稿，与 RFC 09 同批起草）
**作者**：项目主作者 + AI agents
**状态**：草案，待主作者评审 + 客户场景演练
