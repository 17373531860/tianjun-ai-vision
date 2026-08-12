# Project 配置字段字典

> **类型**：reference（生成物勿手改）
> **生成命令**：`python scripts/docgen/gen_config_dict.py`（2026-08-12）
> **单一事实源**：`Project` ORM 七 JSON 字段 + `logic_mode` 列；语义详解见 `modify-project-config` skill。
> **应用链**：前端 Project 页 → POST /projects → DB → activate → `source_project_config_apply.apply_project_config` → VSM

## 顶层字段

| 字段 | 存储 | 说明 |
|---|---|---|
| logic_mode | 表列 | sequential | detection | custom | tracking | per_item | weighing |
| pipeline_config | JSON | 管线/结算/tracking/称重/per_item 等 |
| steps_config | JSON | 每步参数 |
| events_config | JSON | 事件定义 |
| counters_config | JSON | 计数器 |
| alarm_config | JSON | 项目报警 |
| detection_config | JSON | 检测展示 |
| data_config | JSON | 数据/导出 |

## pipeline_config 常用键

| 键 | 说明 |
|---|---|
| `sequence_order` | 顺序模式步骤序列（必填否则顺序检查早退） |
| `detection_steps` | 检测模式步骤定义 |
| `custom_based_on` | custom 模式基于 sequential|detection |
| `custom_conditions` | 自定义条件子序列 [{id,priority,sequence,event_id}] |
| `custom_sequence_order` | custom 顺序表 |
| `custom_detection_steps` | custom 检测步 |
| `settlement_mode` | first_step | last_step | last_first（默认 first_step） |
| `idle_timeout_seconds` | 空闲超时结算 |
| `cycle_max_duration` | 周期最大时长 |
| `ng_cycle_protect_seconds` | NG 周期保护 |
| `settle_dedup` | 结算去重 |
| `accumulate_repeats` | 重复步累积 |
| `simultaneous_groups` | 同时出现组 [{enabled,cross_cycle,labels,priority_order,time_window,...}] |
| `tracking_*` | tracking 模式：tracking_cycle_strategy, tracking_container_label, container_* , tracking_roi, counting_expected_items 等 |
| `rod_companion_filter` | 误判过滤：伴生标签 IOU |
| `rod_session_gate` | 误判过滤：session 门控 |
| `per_item` | {require_exact_count, disable_auto_settle, leave_judgement, ...} |
| `ng_remediation` | v3.23 通用 NG 补做策略 |
| `weighing` | v3.31 称重投料专用（logic_mode=weighing） |
| `custom_mixed_with` | v3.19 custom 混合 per_item|tracking |
| `label_splits` | v3.32 同标签区域拆分 [{id,enabled,source_label,mode(fixed|anchor),anchor_label,anchor_ref,anchor_hold_seconds,unmatched(drop|keep|map),unmatched_label,regions:[{name,polygon,color}],rounds:{enabled,trigger_label,count,prefixes,trigger_gap_seconds,region_overrides}}]，区域名=steps_config 虚拟步骤(split_origin)；rounds 启用时虚拟步骤=前缀+区域名，切换标签重新出现即切下一轮(满轮回绕，周期结算+离场归零)，当前轮次经 /detection/results.label_split_rounds 透出；region_overrides={"轮次":[{name,polygon,color}]} 给某轮换一批独立区域(翻面后位置不重叠场景)，缺省轮沿用共享 regions |
| `placement_guide` | v3.32 工件就位提示 {enabled,anchor_label,polygon,mode(hint),display(always|fade_on_ready|hide_on_ready)}，display=就位后引导框显示策略(常驻/淡化/隐藏，未就位永远完整显示)，运行态经 /detection/results.placement_guide 透出 |
| `strict_order_violation_event_id` | v3.32 严格顺序违序即时事件 id（null=关）：严格步骤在错误时机出现→照旧拦截不计入，同时当场触发所配事件（同一标签+周期进度 5s 节流；last_first 模式无效因严格顺序被强制清空） |

## 其它 JSON 字段

### steps_config

数组，每步：label, name, threshold, min_frames, disappear_delay, strict_order, roi, ...

### events_config

事件定义：id, name, type(ok/ng/warn/custom), alarm, counter, toast, voice, ...

### counters_config

计数器定义与初始值

### alarm_config

项目级报警映射（与全局 alarm 设备配置配合）

### detection_config

检测框/置信度/ROI 等推理展示配置

### data_config

导出/录像/保留策略等项目级数据配置

## 代码位置

- ORM：`backend/models/models.py` `Project`
- Schema：`backend/schemas/project.py`
- 应用：`backend/api/source_project_config_apply.py`
- 前端编辑：`frontend/src/views/Project/`
