# 视频源状态机深潜

> **类型**：explanation（internals 深潜）
> **本文不讲**：怎么改状态机（→ `modify-source` / `debug-source` skill）；端点列表（→ OpenAPI 快照）
> **与代码冲突时**：以代码为准。本文描述 v3.31 实现骨架 + v3.41 设计层增补（收尾持久化 / 严格前缀守门 / weighing 三档驱动），重构可能过期。
> **读码依据**：`docs/dev/_reading_notes/01_backend_core.md` + `source*.py` 全族。

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
| 区域事件 | `source_region_events.py`（纯逻辑引擎）+ `source_region_events_mixin.py`（副作用翻译） | v3.32 时序+空间规则引擎替代步骤状态机 |
| 收尾持久化 | `source_persist_worker.py` | v3.38 每通道 FIFO 落库线程（决策留推理线程，写库出推理线程） |
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
| 区域事件（v3.32） | 动作×区域时序判定（测硬度/扫码类） | pipeline_config.region_events（logic_mode 不变） | `RegionEventEngine.process_frame` → `_update_region_events` 翻译副作用 | 复用周期/步骤/事件体系，零新表 | episode 语义与步骤状态机互斥（引擎非空即接管每帧入口） |

（`weighing` 为第 6 种原生模式，与视觉 5 种并列配置在 Project.logic_mode；区域事件模式不占 logic_mode 枚举、由配置存在性激活。）

> v3.35 / v3.39 起 weighing 分三档驱动模式（pipeline_config.weighing.drive_mode），是设计层的关键分叉：
> - `scale`（v3.31 缺省）：纯秤驱动，周期主权在称重引擎，视觉不参与。
> - `step_gate`（v3.35 融合）：**周期主权回归视觉顺序 SOP**（logic_mode 仍 sequential），秤降级为"步骤完成门控"——视觉确认的新出现先武装门控（去皮 / 标准量判定），秤条件满足才放行入周期（`source_settlement_mixin._device_gate_hold`）。顺序模式的违序/缺步/超时报警全部得以继承；引擎未登记通道时直接放行，绝不卡产线。
> - `pipeline`（v3.39 两阶段流水线）：同时最多两件在制——秤上件离秤瞬间冻结重量事实并判 OK/NG，随后进"待收尾 FIFO 队列"，视觉识别收尾动作时结案队头件；秤指令（去皮/清零）严格重量驱动，视觉标签只做加速/双确认佐证（模型漏检不影响称重主链路）。
> 后两档都要求推理热路径喂帧给引擎（`_weighing_visual_feed`）。

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

## 收尾持久化出推理线程（v3.38，收尾时序的设计层变化）

> 动机：cycle 收尾的 DB 提交原本在推理线程内同步执行，任何别处（MES 推送/清理/导出）握住 SQLite 写锁，推理线程就在 commit 干等（busy_timeout 上限 15s）→ 检测框冻结（川南现场事故链）。
> 代码位置：`backend/api/source_persist_worker.py` → `PersistWorker`；`backend/api/source_session_lifecycle_mixin.py` → `start_cycle` / `end_cycle` / `record_step` / `_reconcile_step_records` / `_discard_empty_cycle`。

设计（决策/持久化分离）：

- 判定、内存状态机、计数器全部留在推理线程；写库 + 依赖"已提交行"的副作用（MES on_cycle_end / 插件 cycle_end / 三协调器 / 扫码器联动）打包成携带值快照的作业，进每通道 FIFO 落库线程按提交顺序执行。
- **状态机收尾时序的语义变化**："周期进行中"的真相源从 `current_cycle_id` 换成 `current_cycle_uuid`——uuid 在同步段即置位/清空，id 由建行作业异步回填（回填前短暂为 None）。所有守门（end_cycle 入口 / settle_dedup 补开周期 / NG 补做挂起 / 扫码建周期 / 关机 end_cycle）一律看 uuid，行定位也用 uuid。
- 顺序不变量：同通道"步骤插入 → 对账 → 周期收尾"作业顺序 == 提交顺序（FIFO 天然保序）；对账/补落账因此改读同步段本地缓存而非查库（库里只有滞后快照）。
- 屏障与回退：`end_session` 统计前先 flush 排空队列；队列打满退化为调用方同步执行（数据安全优先于延迟）；`TIANJUN_SYNC_PERSIST=1` 全局回退同步（现场兜底 + 测试套件保住"提交后立刻可查"断言）。

## 顺序判定语义：严格前缀守门（v3.40）

> 代码位置：`backend/api/source_sequence_labels.py` → `is_legitimate_next_in_sequence`。

"连续合法重复"的位置索引判定（expected[len(current)] == label）隐含一个前提：**当前周期是期望序列的严格前缀**。v3.40 起把这个前提显式化——周期一旦跑偏（漏做/乱序），位置指针即失效，一律走常规去重；只有干净前缀才允许按位置认"合法重复"。语义后果：跑偏周期里滞留画面的末步余像不再被反复接纳入周期（此前会造成前端末步结果闪烁 + 结算多报"重复步骤"），全 OK 周期行为不变。

## 概念 → 文件 → 入口函数

| 概念 | 文件 | 入口 |
|---|---|---|
| 激活项目配置 | `source_project_config_apply.py` | `apply_project_config` |
| 帧循环 | `source_inference_loop_mixin.py` | `_inference_loop` / `_capture_loop` |
| 同标签区域拆分（v3.32，检测出口标签改写层，进状态机前生效） | `source_label_split.py` | `LabelSplitEngine.apply`（经 `_apply_label_splits` 挂在帧循环出口） |
| 结算分发 | `source_settlement_mixin.py` | `_settle_for_cross_cycle` L754 |
| 步骤外设门控（v3.35 融合） | `source_settlement_mixin.py` | `_device_gate_hold`（`_process_single_step` 内接线） |
| 区域事件引擎（v3.32） | `source_region_events.py` / `source_region_events_mixin.py` | `RegionEventEngine.process_frame` / `_update_region_events` |
| 周期结束 | `source_session_lifecycle_mixin.py` | `end_cycle`（同步段定案 → 落库作业） |
| 收尾落库（v3.38） | `source_persist_worker.py` | `PersistWorker.submit` / `flush` |
| 事件中心 | `source_event_trigger_mixin.py` | `_trigger_event` |

## 疑点 / 改前必读

- `__getattr__`/`__setattr__` 兼容层：老代码访问 `_kalman_enabled` 等会被路由到 has-a 组件（不变量 #3）
- 改 `source_settlement_mixin` 时 `_process_last_first_mode` 与 `_process_cross_cycle_groups` 的 settlement_mode 守门不可删（不变量 #11）
- Monitor 双缓冲 MJPEG：`STREAM_SWAP_INTERVAL=600` 勿删（不变量 #7）
- v3.38 起新增守门/落库代码：判"周期进行中"一律看 `current_cycle_uuid` 不看 `current_cycle_id`（id 异步回填）；给落库作业传值快照，不在作业里读推理线程活状态；不要在推理线程新增同步 DB 写（那正是本轮治掉的病）
