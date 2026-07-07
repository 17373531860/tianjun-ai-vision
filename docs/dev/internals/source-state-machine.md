# 视频源状态机深潜

> **类型**：explanation（internals 深潜）
> **本文不讲**：怎么改状态机（→ `modify-source` / `debug-source` skill）；端点列表（→ OpenAPI 快照）
> **与代码冲突时**：以代码为准。本文描述 v3.31 实现，重构可能过期。
> **读码依据**：`docs/dev/_reading_notes/01_backend_core.md`（生成中）+ `source*.py` 全族。

Disclaimer（CPython InternalDocs 三行模板）：
- 给维护者/AI agent 看，不是对客户的功能承诺
- 描述的是实现不是规范
- 版本间会变，冲突以代码为准

## 核心数据结构（从数据结构讲起）

每通道一个 `VideoSourceManager` 实例（`backend/api/source.py`），通过 **mixin 组合** 能力：

| 组件/mixin | 文件 | 职责 |
|---|---|---|
| 主类 + 兼容层 | `source.py` | 聚合 mixin；`__getattr__`/`__setattr__` 路由旧属性到 has-a 组件 |
| 结算 | `source_settlement_mixin.py` | logic_mode × settlement_mode 分发、跨周期组、last_first |
| 周期/session | `source_session_lifecycle_mixin.py` | Session/Cycle/Step 创建与结束 |
| 逐件 | `source_per_item_mixin.py` | logic_mode=per_item 独立路径 |
| 跟踪/清点 | `source_tracking_mixin.py` | logic_mode=tracking |
| 自定义混合 | `source_custom_mix.py` | custom + per_item/tracking 子状态机 |
| 事件触发 | `source_event_trigger_mixin.py` | `_trigger_event` 中心 |
| 虚拟剧本 | `source_synthetic_mixin.py` | RUNTIME_MODE=test 无硬件检测 |
| 项目配置应用 | `source_project_config_apply.py` | 激活项目时写入 pipeline/steps 等到 mgr |

## 五种 logic_mode（档案卡）

| logic_mode | 适用场景 | 配置入口 | 结算入口 | 联动模块 | 已知限制 |
|---|---|---|---|---|---|
| `sequential` | 装配线逐步骤顺序防错 | Project.logic_mode + steps_config | `source_settlement_mixin._settle_sequential_cycle` | 标准步骤统计 | 与 per_item/last_first 互斥项见前端校验 |
| `detection` | 单帧/少步检测型 | 同上 | `_settle_detection_cycle` | 事件判定为主 | 多步时 settlement_mode 行为不同 |
| `custom` | 客户自定义序列/条件 | pipeline_config.custom_* | `_settle_custom_cycle` | 可 based_on sequential | 可与 tracking/per_item 混合（custom_mixed_with） |
| `tracking` | 物品清点/容器跟踪 | pipeline_config.tracking | tracking mixin 路径 | 容器分组 mixin | 与 scanner 容器模式联动 |
| `per_item` | 打螺丝逐件覆盖 | pipeline_config.per_item | `_update_step_stats_per_item` 绕开常规结算 | 离场快照 v3.28 | 与 last_first 互斥 |
| `weighing` | 称重投料 v3.31 | pipeline_config.weighing | `weighing_engine.py` 状态机 | 外设 pipeline | 独立于视觉帧结算 |

（`weighing` 为第 6 种原生模式，与视觉 5 种并列配置在 Project.logic_mode。）

## 四种 settlement_mode（档案卡）

| settlement_mode | 含义 | 激活条件 | 核心函数 | 备注 |
|---|---|---|---|---|
| `first_step` | 见到序列首步即开周期，末步或规则结束 | pipeline_config 默认 | `source_settlement_mixin` 主循环 | 与 simultaneous_groups 配合 |
| `last_step` | 以末步为锚 | apply_pipeline_config | 同上 | |
| `last_first` | 末步结算 + 首步开周期（v3.8+） | settlement_mode 显式设置 | `_process_last_first_mode` L774+ | **严格守门**：仅 sequential/custom-seq |
| （跨周期组） | pipeline simultaneous_groups.cross_cycle | cross_cycle=true | `_process_cross_cycle_groups` L548+ | **严格守门**：与 last_first 互斥 |

## 中心 hook：`_trigger_event` / `_handle_cycle_end`

- `_trigger_event`：所有 OK/NG/警告/自定义事件的统一出口（报警、计数器、Toast、插件 `event_fire` hook）
- `_handle_cycle_end`：周期结算后调用 `mes_hooks.on_cycle_end`、插件 `pre_cycle_end`/`cycle_end` hook
- 改这两处前必读 `debug-source` skill 与 AGENTS.md 不变量

## 概念 → 文件 → 入口函数

| 概念 | 文件 | 入口 |
|---|---|---|
| 激活项目配置 | `source_project_config_apply.py` | `apply_project_config` |
| 帧循环 | `source_inference_loop_mixin.py` | `_inference_loop` / `_capture_loop` |
| 同标签区域拆分（v3.32，检测出口标签改写层，进状态机前生效） | `source_label_split.py` | `LabelSplitEngine.apply`（经 `_apply_label_splits` 挂在帧循环出口） |
| 结算分发 | `source_settlement_mixin.py` | `_settle_for_cross_cycle` L754 |
| 周期结束 | `source_session_lifecycle_mixin.py` | `_handle_cycle_end` |
| 事件中心 | `source_event_trigger_mixin.py` | `_trigger_event` |

## 疑点 / 改前必读

- `__getattr__`/`__setattr__` 兼容层：老代码访问 `_kalman_enabled` 等会被路由到 has-a 组件（不变量 #3）
- 改 `source_settlement_mixin` 时 `_process_last_first_mode` 与 `_process_cross_cycle_groups` 的 settlement_mode 守门不可删（不变量 #11）
- Monitor 双缓冲 MJPEG：`STREAM_SWAP_INTERVAL=600` 勿删（不变量 #7）
