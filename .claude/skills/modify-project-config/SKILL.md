---
name: modify-project-config
description: "安全修改项目配置结构：pipeline_config / steps_config / events_config 等 7 个 JSON 字段的全链路影响分析。配置从前端 Project 页 → POST /projects → DB → 激活后 set_project_config → VSM 状态机 → Monitor 展示，改任一环都要全链路对齐。"
argument-hint: "[要修改的配置项]"
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__context7, mcp__sequential-thinking"
---

> **配置字段字典（生成物）**：`docs/dev/reference/config-dict.md`  
> 本 skill = 改配置时的全链路影响分析（how-to）。

# modify-project-config: 项目配置安全修改分析（v3.5.x）

你正在帮用户安全修改项目配置结构。Project 是天军系统的"客户脚本"，
**贯穿前端编辑 → DB → 后端 VSM 状态机 → Monitor 显示**的核心数据流。
任何一环对不上都会出现"配了但不生效 / 切项目残留 / 老项目崩溃"。

计划修改: $ARGUMENTS

---

## 一、Project 的 7 个 JSON 字段（v3.5.x 现状）

**ORM** (`backend/models/models.py: Project`)

```python
class Project(Base):
    __tablename__ = "projects"
    id, name, task_type, default_model_id, model_format, is_active
    logic_mode      = Column(String, default="sequential")   # 顶层列, 不在 JSON 里!
    pipeline_config = Column(JSON)   # 管线/步骤序列/容器/周期性强制动作
    steps_config    = Column(JSON)   # 每步配置（label/阈值/帧/disappear_delay 等）
    events_config   = Column(JSON)   # 事件定义（OK/NG/自定义）
    counters_config = Column(JSON)
    alarm_config    = Column(JSON)
    detection_config= Column(JSON)
    data_config     = Column(JSON)
```

**Pydantic Schema** (`backend/schemas/project.py`)：`ProjectBase / ProjectCreate / ProjectUpdate / ProjectResponse`，
新增 JSON 顶层字段时 4 个 schema 都要加。

---

### 1. pipeline_config（管线 + 周期性动作 + 容器 + 误判过滤）

```jsonc
{
  // 顺序模式
  "sequence_order": ["step1", "step2", "step3"],
  // 检测模式
  "detection_steps": [...],
  // 自定义模式
  "custom_based_on": "sequential|detection|null",
  "custom_conditions": [
    { "id": 1, "priority": 1, "sequence": ["a","b"], "event_id": 3 }
  ],
  "custom_sequence_order": [...],
  "custom_detection_steps": [...],

  // 结算 / 周期超时（apply 在 _apply_pipeline_config）
  "settlement_mode": "first_step|last_step|last_first",
  "idle_timeout_seconds": 0,
  "cycle_max_duration": 0,
  "ng_cycle_protect_seconds": 0,
  "settle_dedup": false,
  "accumulate_repeats": false,
  // 同时出现组（v3.8.x 重构）— 详细 schema 见 §1.5
  "simultaneous_groups": [
    // 类一·周期内（顺序无关重排）
    { "enabled": true, "cross_cycle": false, "labels": ["B","C"],
      "priority_order": ["B","C"], "time_window": 2.0 },
    // 类二·跨周期（鬼周期治本）
    { "enabled": true, "cross_cycle": true, "labels": ["E","A"],
      "priority_order": ["E","A"], "prev_cycle_labels": ["E"],
      "next_cycle_labels": ["A"], "time_window": 3.0 }
  ],

  // tracking 模式
  "tracking_cycle_strategy": "all_gone|container",
  "tracking_container_label": "",
  "container_box_mode": "single",
  "container_settle_min_items": 1,         // v3.1.4
  "container_id_drift_merge_iou": 0,       // v3.2.0
  "container_id_drift_merge_max_gone_frames": 30,
  "tracking_trigger_label": "",
  "tracking_match_thresh": 0.8,
  "tracking_check_order": false,
  "tracking_swap_detection": false,
  "tracking_appearance_match": false,
  "tracking_id_lock": false,
  "tracking_id_lock_frames": 15,
  "tracking_roi": { "enabled": true, "polygon": [...] },
  "counting_expected_items": { "label1": 2 },

  // 误判过滤（v2.7.8 起走 pipeline_config，apply 在 _apply_rod_filter）
  "rod_companion_filter": { "enabled": false, "iou_threshold": 0.25,
                            "rod_label": "", "companion_labels": [] },
  "rod_session_gate":     { "enabled": false, "rod_label": "", "gate_labels": [] },

  // ★ v3.31.0 新增：称重投料模式（logic_mode='weighing' 专用，设备读数驱动）
  // apply 点不在 source.py：source_project_config_apply.py 末尾按 logic_mode
  // 登记/注销通道到 weighing_engine（改键后要看引擎 set_channel_config 日志）
  "weighing": {
    "weight_device_id": 1,                  // 绑定的外设 id（external_devices 表）
    "station_name": "",
    "require_operator": true,               // 前置：必须先选人员
    "require_model": true,                  // 前置：必须先选型号
    "materials": ["钢帽水泥", "钢脚水泥"],   // 料别顺序
    "models": {                             // 型号 → 料别 → 标准量/公差 (kg)
      "XX-1": { "钢帽水泥": { "standard": 0.5, "low_tol": 0.02, "high_tol": 0.02 } }
    },
    "tare_mode": "auto_stable",             // 放件自动去皮 / manual
    "tare_trigger_weight": 0.05,            // 触发去皮的放件重量阈值
    "tare_settle_samples": 3,
    "stable_min_samples": 3,                // 稳定读数判定样本数
    "stable_tol": 0.003,                    // 稳定读数容差
    "measure_min_weight": 0.005,            // 投料最小有效重量
    "material_check": "sequence|visual",    // 料别判定：按顺序 / 视觉标签
    "auto_zero_after_done": true,
    "alarm_event_shortage": null,           // 缺料/超量/错料/前置未选 → 事件 id
    "alarm_event_over": null,
    "alarm_event_wrong": null,
    "alarm_event_precheck": null,

    // ★ v3.39 新增：两阶段流水线模式（萍乡百斯特；drive_mode='pipeline' 时消费）
    // 秤上称重离秤冻结结算 + 秤下收尾动作 FIFO 结案，两件并行；秤指令严格重量驱动
    "drive_mode": "scale|step_gate|pipeline", // scale=逐道投料(默认) / step_gate=融合门控 / pipeline=两阶段流水线
    "pipeline": {                           // 标签绑定/皮重范围/队列（详见 weighing_engine.DEFAULT_WEIGHING_CONFIG 注释）
      "material": "钢帽水泥", "label_onscale": "工件上秤",
      "label_fill": "加水泥", "label_finalize": "加钢脚水泥",
      "onscale_polygon": null,              // 标签①有效域（秤台区，归一化多边形；null=不过滤）
      "tare_min_kg": 0.2, "tare_max_kg": 10.0, "queue_depth": 2
    },
    "timing": { /* 15 项秤指令时序参数：去皮触发源三档/稳定窗/离秤确认/清零延迟与重发/标签连续帧与冷却期等 */ },
    "alarm_event_ok": 1,                    // 离秤结算合格 → OK 事件
    "alarm_event_tare_range": 2,            // 上秤自重超皮重范围 / 清零残留 / 节拍异常 / 收尾超时
    "alarm_event_residue": 2, "alarm_event_takt": 2, "alarm_event_finalize_timeout": 2
  },

  // ★ v3.32 新增：同标签区域拆分（虚拟步骤）+ 工件就位提示
  // apply 在 source_project_config_apply._apply_label_splits →
  // 引擎 backend/api/source_label_split.py，挂点 = 推理出口 _apply_label_splits
  // （改写后下游状态机/画框/MES 只见虚拟步骤标签）；区域名 = steps_config 里
  // split_origin=<rule.id> 的虚拟步骤（前端 labelSplit.js syncSplitVirtualSteps 同步）。
  // 详见 docs/rfc/同标签区域拆分_虚拟步骤_设计方案_RFC.md
  "label_splits": [
    {
      "id": "ls_xxx", "enabled": true,
      "source_label": "打螺丝",               // 被拆分的模型原始标签
      "mode": "fixed|anchor",                 // 固定画面 / 锚点跟随
      "anchor_label": "前罩",                 // anchor 模式：跟随哪个检测目标
      "anchor_ref": {"x":0,"y":0,"w":0,"h":0},// anchor 模式：标定时锚点框（归一化）
      "anchor_hold_seconds": 3.0,             // 锚点丢失沿用最近位置秒数
      "unmatched": "drop|keep|map",           // 未命中区域的原始标签处理
      "unmatched_label": "",                  // map 时改写成的标签
      "regions": [ {"name": "螺丝1", "polygon": [[0.1,0.1],...], "color": "#f97316"} ],
      "rounds": {                             // ★ 多轮次：同一批位置按工序轮次映射不同虚拟步骤
        "enabled": false,                     //   （前罩/后罩各打4颗、打的位置在画面重叠的场景）
        "trigger_label": "盖罩",              // 切换标签"重新出现"(离场>gap 后再入画)→ 下一轮
        "count": 2,                           // 总轮数 2~8，满轮后回绕第1轮
        "prefixes": ["前罩", "后罩"],          // 每轮前缀，虚拟步骤名 = 前缀+区域名
        "trigger_gap_seconds": 3.0,           // 离场判定窗口（防短暂遮挡误切轮）
        "trigger_min_seconds": 0.5,           // ★ v3.34 切换确认时长：重新出现后需持续在场
        //   满该秒数才确认切换（过滤真实模型单帧误检闪现；0=见帧即切，老行为）
        "trigger_conf": 0,                    // ★ v3.34 切换标签专用置信度下限：低于它按
        //   "不在场"（挡零星低置信误检刷新在场时刻导致轮次卡死；0=不额外过滤）
        "region_overrides": {}                // 每轮独立区域(可选): {"2": [{name,polygon,color}]}
        //   翻面后位置不重叠时给某轮换一批区域，缺省轮沿用共享 regions；
        //   某轮 override 全部非法 → 该轮回退共享区域（不整体禁用轮次）
        // 周期已结算且切换标签离场 → 轮次归零；当前轮次经
        // GET /source/detection/results 的 label_split_rounds 透出给 Monitor 角标
      }
    }
  ],
  "placement_guide": {                        // 工件就位提示（与拆分正交的独立小功能）
    "enabled": false, "anchor_label": "", "polygon": [], "mode": "hint",
    "display": "always"                       // 就位后显示策略: always 常驻(变绿) |
    //   fade_on_ready 淡化(半透明细框无文字) | hide_on_ready 隐藏。未就位永远完整显示。
    //   纯前端渲染策略, 后端 parse 不消费; 运行态经 GET /source/detection/results 透出
  },
  // ★ v3.32：严格顺序违序即时事件（null=关）。严格步骤在错误时机出现时照旧拦截
  // 不计入周期，同时当场 _trigger_event 所配事件（建议配警告/自定义类，配 NG 会走
  // 完整 NG 计数+推送慎用）。同一(标签,周期进度) 5s 节流。触发点=source_settlement_mixin
  // 的两处严格守门(_fire_strict_order_violation)。last_first 模式下严格顺序被强制清空→无效。
  "strict_order_violation_event_id": null,
  // ★ v3.43：实时NG（违规即时结算，false=关零差异）。违序/缺前置（需严格顺序）、
  // 步骤回退（顺序型）、重复超次（检测模式）一经确认当场按 NG 事件(2) 结算当前周期；
  // 提示档/斩立决由事件2自身「需人工确认」分流。收口 source_settlement_mixin._fire_instant_ng；
  // 前端开关在 LogicConfigTab「实时NG」卡片。last_first 模式保存时被强制置 false。
  "instant_ng_on_violation": false,

  // ★ v3.32 新增：区域事件模式（logic_mode='region_events' 专用，TP 工位流程监测）
  // apply 在 source_project_config_apply → source_region_events.parse_region_events；
  // 引擎 RegionEventEngine 挂帧循环（source_region_events_mixin），步骤=动作规则名。
  // 前端编辑 UI 在 LogicConfigTab.vue（字段名与 parse 严格对齐，改键两头一起改）。
  "region_events": {
    "rules": [
      {
        "id": "re_xxx", "name": "测硬度",       // 动作名 = 步骤名（Monitor 面板/结算序列用它）
        "type": "overlap|region_enter|region_exit",
        "subject_label": "测硬度笔",            // 主体类别（工具/对象）
        "object_label": "工件",                 // overlap 专用：被作用目标
        "region": [[0.1,0.1], ...],             // 判定区域多边形（overlap 可空=不限区域）
        "region_mode": "and|or",                // overlap：重叠 且/或 主体中心在区域内
        "require_label": null,                  // 辅助约束：主体须与其相交（如"手"，压误报）
        "min_frames": 10,                       // 连续满足帧数（region_exit 为最少观察帧数）
        "gone_frames": 10, "match_iou": 0.3,    // region_exit：消失确认帧数/帧间关联 IoU
        "min_iou": 0.0,                         // overlap：重叠 IoU 下限（0=任意相交）
        "min_overlap_ratio": 0.0,               // overlap：重叠深度下限（压静置工具贴边）
        "object_margin": 0.0,                   // overlap：目标框虚拟扩边（归一化 0~0.2，动作
        //   发生在目标框边缘外侧几个百分点时桥接，如扫工件下沿条码；纯空间量与帧率无关；
        //   只参与"是否相交"，深度门槛仍按原始框算）
        "min_move": 0.0,                        // 位移门槛（归一化，0=不要求；中心 5 帧中位数平滑后进包络）
        "min_seconds": 0.0,                     // ★ v3.34 确认时长秒基（overlap/enter；>0 按
        //   episode 命中跨度秒判定，min_frames 退化为 3 帧硬下限——与帧率解耦；0=帧数老语义）
        "gone_seconds": null,                   // 消失确认秒（null=用全局 gap_tolerance_frames）
        "event_id": null,                       // 动作确认附加触发事件（不结算）
        "settle": false,                        // true = 该动作确认即结算周期
        "anchor": { "enabled": false, "label": "", "ref": {...}, "hold_seconds": 3.0 } // 区域跟随锚点
      }
    ],
    "gap_tolerance_frames": 5,                  // 全局漏检容忍
    "dedup_consecutive": true,                  // 连续相同动作去重
    "class_conf": { "测硬度笔": 0.5 },          // 每类置信度覆盖
    "sequence_check": { "enabled": true, "order": ["测硬度","扫码","下工件"], "event_id": null },
    "settlement_rules": [                       // 可选：特定确认序列 → 指定判定（复检序列合法化）
      { "sequence": ["测硬度","扫码","测硬度","扫码","下工件"], "result": "ok" }
    ]
    // 内建常开：动作互斥打断（一个动作确认瞬间其他 in-progress episode 立即收尾，
    // 防 gone_seconds 桥接复检两段命中）。排查问题读 debug-source skill 区域事件节
  },

  // ★ v3.5.0 新增：周期性强制动作（每 N 轮做 E）
  // ★ v3.5.2 新增：每条规则的 run_on_start（开机首检）
  // ★ v3.7.4 新增：time_interval_seconds（按时间触发，与 interval OR 关系）
  "periodic_actions": [
    {
      "id": "pa_1700000000_0",
      "name": "每20件清洁治具",
      "enabled": true,
      "trigger_step_ids": [3, 5],
      "interval": 20,                         // 按次数 (=0 关闭)
      "time_interval_seconds": 600,           // ★ v3.7.4: 按时间秒数 (=0 关闭, >0 启用)
      "count_basis": "all|good_only|ng_only",
      "reset_policy": "always|only_when_due",
      "due_warning_event_id": 4,
      "overdue_event_id": 5,
      "overdue_repeat": "every_cycle|once|cooldown:N",
      "channel_filter": [0, 1],
      "run_on_start": false                   // v3.5.2
    }
  ]
}
```

#### 1.5 simultaneous_groups（v3.8.x 重构，必读）

**两类同时出现组**（共用同一个数组，靠 `cross_cycle` 区分）：

| 类别 | `cross_cycle` | 作用 | 详细流程 |
|---|---|---|---|
| 类一·周期内同时组 | `false`（默认） | 解决周期内 B-C 谁先谁后顺序不稳定的问题 | 缓冲收集 + 全员到齐后按 `priority_order` 写入；缓冲被组外有意义步骤打断时立即输出已收集成员 |
| 类二·跨周期同时组 | `true` | 解决"上周期末步残影 + 下周期首步"造成的鬼周期 | 等待状态机：先到一侧 → 等另一侧 → 4 种终止路径（另一侧到 / 组外步骤 / 超时 / 残影屏蔽）|

**完整 schema**：

```jsonc
{
  "enabled": true,              // 默认 true；false 时彻底跳过该组
  "cross_cycle": false,         // ★ v3.8.x: 跨周期模式开关
  "labels": ["B", "C"],         // 组成员标签（YOLO label，与 steps_config.label 对应）
  "priority_order": ["B", "C"], // 写入 cycle_steps 的优先顺序（必填）
  "time_window": 2.0,           // 类一: 缓冲收集窗口（秒）；类二: 等待对侧到达的窗口

  // 仅 cross_cycle=true 时使用，前端保存时从 period_roles 推导出来
  "prev_cycle_labels": ["E"],   // 属于上一周期的成员（一般是结算步骤）
  "next_cycle_labels": ["A"],   // 属于下一周期的成员（一般是首步）

  // 仅前端编辑用，保存到后端时被 prev/next_cycle_labels 替代；
  // 后端拉回时 initProjectDefaults 反向推导回 period_roles
  "period_roles": { "E": "prev", "A": "next" }
}
```

**互斥规则**（前端保存时强校验）：
- 任一 `cross_cycle=true` 的组与 `settle_dedup=true` **互斥**
- `cross_cycle=true` 的组必须**同时**含至少一个 `prev` 和一个 `next` 归属的成员
- 跨周期组每个成员必须明确标记 `period_roles[label]`，不能为空

**配置生效链路**：
```
前端 Project/index.vue 保存
  → simultaneous_groups 写入 pipeline_config
  → projects.py POST /projects/ + activate
  → source_project_config_apply.set_project_config()
  → self._simultaneous_groups = pipeline.get('simultaneous_groups', [])
  → 主循环 source_step_stats_mixin._update_step_stats 每帧检查
    ├ 类一: source_settlement_mixin._process_simultaneous_groups
    └ 类二: source_settlement_mixin._process_cross_cycle_groups
```

**配置改动 / 排查请同时看**：
- `.claude/skills/debug-source/SKILL.md` §十二·六（状态机 / 排查模板）
- `backend/api/source_settlement_mixin.py`（实现）
- `backend/api/source_state_init.py`（状态字段 `_blocked_labels` / `_cross_cycle_waiting`）

#### v3.7.4 时间维度语义（必读）

- **OR 关系**：`interval` 与 `time_interval_seconds` 谁先到期谁先报警；做 `trigger_step` 时**两个维度同时重置**。两个都 =0 视为无效规则会被跳过。
- **典型场景**：客户产线生产间断（吃饭/换班/换工序），整个 cycle 不推进但 detection 仍在跑。`interval` 按次数维度永远不到期，新维度 `time_interval_seconds` 兜住。
- **运行期主动检查**：`source_inference_loop_mixin._inference_loop` 每 5 秒 throttle 调一次 `_check_periodic_actions_time_only(now)`，即使**没有新 cycle**也能触发时间维度报警（与现有 60s 缓存清理 / 600s GPU 清理同款机制）。
- **持久化升级**：`DATA_DIR/counters/project_X_chY_periodic.json` 从 `{rule_id: counter}` 升级为 `{"counters": {rule_id: counter}, "last_done_ts": {rule_id: ts}}`。`_restore_periodic_counters` 兼容老格式（自动识别后向兼容读）。
- **状态返回**：`get_periodic_actions_status` 在原 `count_state / counter / interval` 基础上新增 `time_state / time_elapsed_seconds / time_remaining_seconds / time_interval_seconds`，整体 `state` 字段取**更严重**那个（severity: ok < due < overdue），前端进度条按更接近爆表那个维度渲染。
- **完全向后兼容**：老项目 schema 缺 `time_interval_seconds` 时默认 0 = 行为完全等同 v3.7.3。

### 2. steps_config（每步配置）

```jsonc
[
  {
    "id": 1,
    "label": "step1",            // YOLO 标签名
    "displayLabel": "第一步",     // 别名 display_name 也兼容
    "enabled": true,
    "threshold": 50,             // ★ 前端发百分比, _apply_steps_config 自动 / 100
    "min_duration": 0,
    "max_duration": 0,
    "max_interval": 1.0,
    "disappear_delay": 0,
    "timeout_ng": false,
    "min_frames": 1,
    "gap_tolerance": 0,
    "strict_order": false,
    "accept_once": false,
    "detection_type": "static|dynamic",
    "static_trigger_frames": 30,
    "join_cycle": true,
    "triggerEvent": null,        // 静态步骤被触发后弹的 event_id
    "backup_for": null,          // primary step 的 id（替补步骤）
    // 追踪模式
    "count_mode": "track|event|total",
    "event_required_count": 1,
    "event_gone_frames": 8,
    "tracking_gone_confirm_frames": null,
    "tracking_max_lost_seconds": 5.0,
    "tracking_position_lock": false,
    // v2.7.4
    "hide_in_view": false,
    "stack_enabled": false,
    "stack_reappear_seconds": 1.0,
    "stack_required_count": 2,
    "max_recognized": 0
  }
]
```

`enabled=false` 的步骤会**整体被 `_apply_steps_config` 跳过**。

### 3. events_config（事件定义）

```jsonc
[
  { "id": 1, "name": "合格(OK)", "color": "#10b981",
    "actions": [{ "counter_name": "合格总数", "delta": 1 },
                { "counter_name": "总产量",   "delta": 1 }],
    "show_notification": true, "toast_id": "ok" },
  { "id": 2, "name": "不良(NG)", "color": "#ef4444",
    "actions": [{ "counter_name": "不良总数", "delta": 1 },
                { "counter_name": "总产量",   "delta": 1 }],
    "show_notification": true, "toast_id": "ng" },
  // v3.5.0 起：可加自定义事件，被 periodic_actions / custom_conditions / 静态步骤
  // 通过 id 引用；触发走同一条 _trigger_event 链路。
  { "id": 4, "name": "保养到期", "color": "#f59e0b",
    "actions": [], "show_notification": true, "toast_id": "ng" }
]
```

事件级人工确认字段（都默认 false/0 = 零差异）：`require_ack`（触发后定格等确认）、
`ack_timeout_sec`（超时自动确认）、`ack_resets_periodic`（确认同步清账周期性规则计数）、
**`ack_keep_cycle`（v3.34 断点补做）——确认后保留在制周期与已完成步骤，从被打断处继续
补做（典型：违序警告定格 → 确认 → 接着打漏掉的那颗螺丝，整件照常判定）；不勾走老
"确认重做"语义（丢弃在制周期）。超时自动确认遵循同一语义。** 后端收口：
`source_event_trigger_mixin.py: _pending_ack_keeps_cycle / _ack_release_keep_cycle`，
确认端点在 `source_routes.py: _do_ack_pending`，超时分支在 `source_step_stats_mixin.py`。

`_trigger_event` (`source_event_trigger_mixin.py`) 同时支持数字 id（`1`/`2`/`4`）
和字符串 id（`'event_1'`），逐项匹配 `events_config[*].id`。
`actions[*]` 同时兼容 `delta` 和 `value` 两种字段名。

### 4-7. 其他四个 JSON

- **counters_config**: `[{name, value}]`（apply 在 `_apply_counters`，写盘到
  `DATA_DIR/counters/project_{id}_ch{ch}.json`）
- **alarm_config**: 串口/协议/triggers，由 Navbar 切项目时 `POST /alarm/config` + `/alarm/connect` 自动应用
- **detection_config**: 检测框样式 / Toast / 语音；进 `useSourceStore.loadDetectionFromProject`
- **data_config**: 数据导出开关 + 班次拆分（`shift_split_enabled / day_shift_start / night_shift_start`）

---

## 二、配置数据流全链路（v3.5.x）

```
┌─ 前端 Project/index.vue (2925 行) ───────────────────┐
│  selectProject → getProjectDetail                    │
│  initProjectDefaults(project)   ← 必填默认值 + 把     │
│      pipeline_config 解包到 activeProject 顶层       │
│  handleSaveProject               ← 把顶层重新塞回    │
│      pipeline_config + data_config 再 PUT /projects  │
└──────┬───────────────────────────────────────────────┘
       │  PUT  /api/v1/projects/{id}
       ▼
┌─ backend/api/projects.py ────────────────────────────┐
│  update_project: setattr 到 ORM, db.commit           │
│  POST /projects/{id}/activate                        │
│    → 写 is_active + _reload_model_for_active_project │
│    → 清各通道 MES pending（避免旧码混入新项目）        │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  Navbar.handleProjectChange
┌─ frontend/src/layout/Navbar.vue ─────────────────────┐
│  activateProject + getProjectDetail                  │
│  填默认值（counters/steps/events/pipeline_config）    │
│  从 pipeline_config 解包 sequence_order/...到顶层    │
│  projectStore.setCurrentProject(project)             │
│  store.loadDetectionFromProject(project.detection_*) │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  Monitor.startDetection → syncProjectConfig
┌─ POST /api/v1/source/detection/set-project?channel=N─┐
│  ProjectConfigRequest 仅取 7 个 JSON 中 5 个          │
│  (steps/pipeline/events/counters/data) + logic_mode  │
└──────┬───────────────────────────────────────────────┘
       │
       ▼
┌─ backend/api/source.py: VSM.set_project_config ──────┐
│  薄 wrapper → apply_project_config(self, config) 在  │
│  source_project_config_apply.py (P7 第十一刀):       │
│    _apply_rod_filter           (rod_filter.py)       │
│    _reset_step_state_dicts                           │
│    _apply_steps_config         (threshold/100, 帧, 静态)│
│    _apply_backup_steps         (backup_for → 映射)    │
│    _apply_pipeline_config      (settlement / 超时)    │
│    _apply_counters             (默认 4 计数器 + 持久化)│
│    _reset_cycle_state                                │
│    _apply_tracking_mode        (tracker yaml + 容器)  │
│    _apply_periodic_actions ★   (v3.5.0)              │
│    _print_summary                                    │
└──────┬───────────────────────────────────────────────┘
       │  扁平 setattr → host (VideoSourceManager)
       ▼
┌─ 运行期消费点 ───────────────────────────────────────┐
│  source_inference_loop_mixin.py: 读 logic_mode       │
│      ★ v3.7.4: 每 5s throttle 调                     │
│      _check_periodic_actions_time_only(now) →        │
│      纯时间维度检查, 即使没新 cycle 也能报警          │
│  source_events_check_mixin.py:   按模式分发 OK/NG     │
│  source_step_stats_mixin.py:     调 _check_periodic_ │
│      actions_on_first_step (v3.5.2 开机首检)         │
│  source_session_lifecycle_mixin.py: cycle_end 后调   │
│      _check_periodic_actions(cycle_steps, is_good)   │
│      ★ v3.7.4: 完成动作时同时 reset counter +        │
│      _periodic_last_done_ts                          │
│  source_lifecycle_mixin.py: start_detection 时调     │
│      _run_periodic_actions_on_start (v3.5.2)         │
│  source_drawer.py / settlement_mixin.py / ...        │
└──────┬───────────────────────────────────────────────┘
       │
       ▼  GET /api/v1/source/detection/results?channel=N
┌─ 前端 Monitor/index.vue (4051 行) ───────────────────┐
│  pollDetectionResults → 写 stats / events / counters │
│  data.periodic_actions → periodicActions ref         │
│  渲染 SOP 卡片 / 计数器 / Tracking 进度 / 周期性强制  │
│  动作进度条（ok/due/overdue 三态）                    │
└──────────────────────────────────────────────────────┘
```

> 注意：set-project 路由的 `ProjectConfigRequest` **只发送 5 个 JSON**（`alarm_config / detection_config` 不发），
> 因为这两个由前端独立的 alarm.connect / sourceStore 处理，**不进 VSM 状态机**。

---

## 三、修改前必跑的 grep 矩阵

| 检查点 | 命令（按 key 名 grep）|
|---|---|
| 后端 apply 解析 | `rg 'config.get\(.<KEY>.\)' backend/api/source_project_config_apply.py backend/api/source_*_mixin.py backend/api/rod_filter.py` |
| 后端运行期读取 | `rg 'project_config.get\(.pipeline_config.\)\.get\(.<KEY>.\)' backend/` |
| Schema 是否漏 | `rg '<KEY>' backend/schemas/project.py` |
| Source route 透传 | `rg '<KEY>' backend/api/source_routes.py` |
| 前端默认值 | `rg '<KEY>' frontend/src/views/Project/index.vue` （看 initProjectDefaults + handleSaveProject）|
| 前端 Navbar 切换 | `rg '<KEY>' frontend/src/layout/Navbar.vue` |
| 前端 Monitor 显示 | `rg '<KEY>' frontend/src/views/Monitor/index.vue` |
| Monitor 同步入口 | `rg 'syncProjectConfig' frontend/src/views/Monitor/index.vue -n` |
| ORM Column | `rg '<KEY>' backend/models/models.py` |
| reset_stats 清零 | `rg '<状态变量>' backend/api/source.py`（看 reset_stats） |
| 持久化 | `rg 'DATA_DIR.*counters' backend/api/source_*.py` |

---

## 四、强制分析流程

### 第 1 步：确认归属

- 在哪个 JSON 字段下？（pipeline_config / steps_config / events_config / ...）
- 修改类型：新增 key / 改类型 / 改嵌套 / 删 key
- **跨字段引用**？（典型：`periodic_actions[*].due_warning_event_id` → `events_config[*].id`，
  `custom_conditions[*].event_id` → `events_config[*].id`，
  `step.triggerEvent` → `events_config[*].id`，
  `step.backup_for` → 另一 step 的 `id`）

### 第 2 步：跑 grep 矩阵确认全链路所有触点

新增 key 必须在下列**全部**位置闭环：

| 位置 | 文件 | 缺一会怎样 |
|---|---|---|
| ORM JSON Column | `models/models.py` | 已是 JSON Column 通常无需改；新增**顶层列**才需要加 + 写 ALTER TABLE 到 `backend/main.py: migrate_database()` |
| Pydantic Schema | `schemas/project.py`（4 处）| PUT /projects 验证不过 |
| Project 路由透传 | `api/projects.py`（5 处构造 ProjectResponse）| GET 拿不到字段 |
| set-project 路由 schema | `api/source_routes.py: ProjectConfigRequest` | Monitor 同步时丢字段 |
| set_project_config 解析 | `api/source_project_config_apply.py: apply_project_config` 或对应 mixin（如 `source_periodic_actions_mixin.py`）| 后端**完全不读** |
| 状态变量初始化 | `api/source_state_init.py` | reset_stats / 切项目时找不到属性 |
| reset_stats 清零 | `api/source.py: reset_stats` | 切项目残留旧数值 |
| 持久化（如有）| `DATA_DIR/counters/...` | 重启丢状态 |
| 前端 initProjectDefaults | `views/Project/index.vue`（line ~1971）| 老项目打开报错 / 字段消失 |
| 前端 handleSaveProject | `views/Project/index.vue`（line ~2273+）pipeline_config 组装段 | 编辑后**保存不上** |
| 前端 Navbar handleProjectChange | `layout/Navbar.vue`（line ~284）| 切项目后字段没解包到顶层 |
| 前端 syncProjectConfig | `views/Monitor/index.vue`（line ~2935）| 启动检测时字段没送到后端 |
| 前端 Monitor 渲染 | `views/Monitor/index.vue` 各 ref | UI 看不到 |
| 前端 Project UI | `views/Project/index.vue` 编辑组件 | 客户编不了 |

### 第 3 步：给出影响报告

```
修改的字段: pipeline_config.<KEY>
- 后端 apply: source_project_config_apply.py / source_*_mixin.py 改动点
- 后端 schema: schemas/project.py + source_routes.py: ProjectConfigRequest
- 后端运行期: 读取该字段的 mixin / executor
- VSM 状态: __init__ / state_init / reset_stats 三处
- 前端 Project: initProjectDefaults 默认值 + handleSaveProject 写回
- 前端 Navbar: handleProjectChange 默认值
- 前端 Monitor: syncProjectConfig 透传 + 渲染
- 持久化: DATA_DIR/counters/... 是否要落盘
- 老项目兼容: 缺 key 时 .get(key, default)
- 多通道: 是否要 channel_id / 是否要 channel_filter 限定
```

---

## 五、修改原则

1. **新增需求优先扩 JSON 而不是加 Column**（AGENTS.md 七节"关键扩展点"明示）。
2. **向后兼容**：所有 `.get(key, default)`，旧项目缺字段不能崩。
3. **默认值同步 4 处**：
   `initProjectDefaults` + `handleSaveProject` 写回 + `Navbar.handleProjectChange` + `apply_project_config`。
4. **threshold 单位**：前端百分比 (10-100)，后端 `_apply_steps_config` 自动 `/ 100`。**不要在前端手动除**。
5. **logic_mode 不在 JSON 里**：是顶层 String 列，帧驱动新值要在 `source_events_check_mixin.py` 的分发处加分支；
   设备驱动模式（如 v3.31 `weighing`）例外——分发点在 `source_project_config_apply.py` 末尾的引擎登记段，帧循环不感知。
6. **跨字段引用 by id**：`events_config[*].id`、`steps_config[*].id`、`periodic_actions[*].id` 都是稳定字符串/整数，
   修改时不要重新分配 id，否则 `_trigger_event` 找不到。
7. **新状态变量必须进 reset_stats**（v3.5.2 `_periodic_counters / _run_on_start_pending` 就是这么补上的）。
8. **多通道**：状态变量都要 per-channel（`_periodic_counter_path` 拼 `ch{channel_id}`），
   切通道数（`channel_manager.set_channel_count`）必须考虑残留落盘文件。
9. **激活时副作用**：`POST /projects/{id}/activate` 会**重载模型 + 清 MES pending**；
   增加新副作用要加 try/except，**绝不让 activate 主流程失败**。
10. **set_project_config 不重置已激活的视频源 / 模型**：只重置步骤 / 周期 / 计数器状态。
    新加状态变量如属"周期内"语义，必须在 `_reset_cycle_state` 或 `_reset_step_state_dicts` 里清零。

---

## 六、v3.5.x 常见踩坑（必查）

| 坑 | 症状 | 根因 |
|---|---|---|
| 前端加字段没到后端解析 | 编辑保存成功但运行不生效 | `apply_project_config` 漏 key |
| 后端解析没存到 host | apply 日志 OK 但运行时 `getattr(self,...)` 报错 | 没 `setattr(h, key, value)` |
| 状态没在 reset_stats 重置 | 用户点"清零"后字段还有旧值 | 漏在 `source.py: reset_stats` 加清零逻辑 |
| 新字段在 pipeline_config 里读不到 | rod_filter v2.7.6 旧坑 | `_build_project_config` 没把字段抬到顶层（v2.7.8 已经统一从 pipeline_config 读，不要再走顶层） |
| 老项目打开 Project 页崩 | `Cannot read property of undefined` | `initProjectDefaults` 漏给默认值 |
| 编辑后保存不上 | PUT /projects 缺字段 | `handleSaveProject` 的 pipeline_config 组装段漏 |
| 切项目后字段还在 | Navbar 没解包 → 顶层旧值 | `handleProjectChange` 漏处理 |
| 启动检测后后端无配置 | Monitor 没透传 | `syncProjectConfig` 漏 key（pipeline_config 整体透传通常 OK，但如果**抬到了顶层**就要单独发） |
| periodic_actions 切项目残留 | 老项目计数没归零 | 切项目走 `apply_project_config`，会从 `DATA_DIR/counters/project_{id}_ch{ch}_periodic.json` **按 project_id 重新装**，确认文件路径用了对的 project_id |
| run_on_start 不弹 | 配置开了但开机首检静默 | `_run_periodic_actions_on_start` 在 `start_detection` / `resume_inference` 才调；`_check_periodic_actions_on_first_step` 必须在第一个步骤 disappear 时调（`source_step_stats_mixin.py`） |
| 自定义事件触发不出来 | events_config 里有但不响应 | 检查 `id` 是不是数字/字符串混用，`_trigger_event` 双向匹配，但有些调用点（如 `due_warning_event_id`）保存的是 number，配出来是 string 会失配 |
| Schema 校验失败 | `pipeline_config` 整体被丢 | `ProjectUpdate` Optional[dict] 是顶层，新加**顶层列**才要改 schema；JSON 内嵌 key 不需要 |
| MES Hook 错乱 | 切项目后旧 workpiece 串到新项目 | 已由 `activate_project` 调 `hook.clear_pending_scan` 处理；新增类似副作用要参考此处 |
| **顺序模式不触发 OK/NG（高频踩坑）** | 走完 step_a/b/c 三步，cycle 永远不结算，OK/NG 事件永不触发，counters 全 0 | `pipeline_config.sequence_order` **必填**。`source_sequential_mixin.py:_check_sequential_mode` 第 15-27 行：`if not sequence_order: return`，没配就直接早退、连 step 都不计数（settle 走的是别的路径，counters 触发依赖此函数）。诊断：后端日志找不到「顺序模式检查/结算: 期望=...」就是这个 |
| **结算模式默认 first_step、不是 last_step（高频踩坑）** | step_a 只出现一次的剧本永远不结算（"第一步再次出现才结算"是 first_step 模式语义） | `pipeline_config.settlement_mode` 默认是 `'first_step'`，意思「第一步**再次被检测到**」才触发结算。要"最后一步消失就结算"得显式配 `'last_step'`。诊断：后端日志看到「[第一步结算] [step_a] 再次检测到 ... 结算当前周期」=first_step 模式；看到 `_check_sequential_mode` 由 `_check_events(last_step)` 触发 = last_step 模式 |
| **v3.8.x last_first 模式（末步出现立即结算 + 首步开新周期）** | 客户工艺：D 一出现就结算（不等消失），跳 D 直接 A 也算上周期 NG | `pipeline_config.settlement_mode = 'last_first'`。语义见 `debug-source` skill §十二·七。**强制约束**（前端 + 后端双重）：所有 step.strict_order 自动关；不能与跨周期同时出现组 / per_item 共存；只支持 sequential / custom-based-on-sequential。诊断：后端日志看到 `[last_first R1/R3/R4]` = 进入新模式；测试入口 `tests/test_settlement_last_first_v38.py` |
| **activate ≠ set-project**（写测试时高频踩坑） | `POST /projects/{id}/activate` 后 mgr.events_config 为空，事件触发不到 | `activate_project` 只重载模型 + 写 DB `is_active=True`，**不会** push events/counters/steps 进 `mgr.project_config`。前端是靠 Monitor 启动时 `syncProjectConfig` → `POST /source/detection/set-project` 才把 5 个 JSON 推进 mgr。脚本测试要么走完整链路，要么手动 POST `/source/detection/set-project` |
| **synthetic with_project=True 用的是「最小项目」** | 跑 synthetic 想看 OK/NG 触发，结果 `_trigger_event` 找不到事件直接 return False | `test_runtime_routes.py:_build_min_project_config` 只有 `steps_config`，`events_config: []`，`pipeline_config: {}` (无 `sequence_order` 也无 `settlement_mode`)。**要触发事件**：先 `create_activate_push` 全 payload + `set-project`，再 `start_synth(with_project=False)` 让 mgr 沿用我们推的 config |

---

## 七、自检清单

修改完后逐条核对：

- [ ] 跑了上面 grep 矩阵，确认所有触点都改了
- [ ] `apply_project_config` / 对应 mixin 增加了 `.get(key, default)` 解析
- [ ] host 上的状态变量在 `source_state_init.py` 里有初始化
- [ ] `reset_stats` 里清零（如属周期内/会话内状态）
- [ ] `initProjectDefaults` + `handleSaveProject` 双向闭环
- [ ] `Navbar.handleProjectChange` 默认值（如果字段被解包到顶层）
- [ ] `Monitor.syncProjectConfig` 透传（不挂 pipeline_config 内部时才需要）
- [ ] 4 个 Pydantic schema 同步（仅顶层列改动）
- [ ] `projects.py` 5 处 ProjectResponse 构造同步（仅顶层列改动）
- [ ] 老项目打开 / 编辑 / 保存 / 激活 / 启动检测 / cycle 跑通一轮
- [ ] 多通道场景：每通道独立状态 / 持久化文件路径带 `ch{channel_id}`
- [ ] 跨字段 id 引用没断（`due_warning_event_id` ↔ `events_config[*].id` 等）
- [ ] lint 0 错误，启动后端无 traceback

---

## 八、UAT 校验项目配置生效的最短链路（合成验证）

写自动化测试或客户复现时验"配了一个 N 步顺序模式 + 自定义事件 + 计数器" 端到端确实跑通：

```python
# 1) 整个 payload（必含 sequence_order + last_step）
payload = {
    "name": "uat",
    "task_type": "detection",
    "logic_mode": "sequential",
    "pipeline_config": {
        "sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}],
        "settlement_mode": "last_step",   # 没这行，OK 永远不触发
    },
    "steps_config": [...],          # threshold 用 0-100，后端会 /100
    "events_config": [...],         # OK/NG/自定义都在这
    "counters_config": [...],       # 默认 4 个不会自动建，要列出来
    ...
}

# 2) 顺序：建 → 激活 → set-project（必须三步全做）
pid = POST /api/v1/projects                      json=payload
POST /api/v1/projects/{pid}/activate
POST /api/v1/source/detection/set-project?channel=0  json={...payload, project_id:pid}
# ↑ activate 不 push project_config 进 mgr，必须再来一次 set-project

# 3) 跑 synthetic（with_project=False 才会用 mgr 已 push 的项目，而不是 min config）
POST /api/v1/test/synthetic/start  json={"scenario": "ok_sequential_cycle.json",
                                          "with_project": False}
POST /api/v1/source/detection/start?channel=0

# 4) 轮询 GET /api/v1/source/detection/results 直到
#    counters['合格总数'] == 1 且 step_counts 三步都 ≥ 1
```

**Verify 矩阵**（每条都一定要过）：

| 验证项 | 期望 | 失败定位 |
|---|---|---|
| 三步都计数 | `step_counts == {step_a:1, step_b:1, step_c:1}` | step_min_frames / step_conf_thresholds / step_roi_polygons 哪个把帧吃了 |
| OK 事件触发 | `counters['合格总数'] == 1` | `_check_sequential_mode` 没跑：`sequence_order` 没配 / `settlement_mode` 是 first_step 没结算 |
| NG（反向剧本） | reverse `c→b→a` 跑出来 `counters['不良总数'] == 1` | sequence_order 是不是数字（注意是 `{"step_id":1}` 不是字符串） |
| 步骤 ROI 限定 | bbox 中心在 polygon 外那帧不计 step | `step.roi` 必须是 [[nx,ny],...] 归一化、≥3 点 |
| 移动目标 + ROI | 中段在 ROI 内、两端在 ROI 外 → step 计数 == 1（只有中段被认） | step_stats_mixin line 60-65 严格的中心点-多边形测试 |

---

## 七、v3.12.0 新增 per_item 配置位（全链路对照）

v3.12.0 给「逐件覆盖」模式加了 4 个新配置字段，每个都跑通**前端 UI → POST /projects → DB → 激活后 set_project_config → VSM 状态机 → Monitor / 检测路径**：

### 7.1 `pipeline_config.per_item.require_exact_count`（项目级 bool）

| 链路环节 | 表现 |
|---|---|
| 前端 UI | 项目管理 → 逻辑设置 → 「严格等待检出数符合期望」开关（`frontend/src/views/Project/index.vue`）|
| POST /projects 字段 | `pipeline_config.per_item.require_exact_count: true/false` |
| DB | 落 `projects.pipeline_config` JSON 字段，不需要 ALTER TABLE |
| set_project_config | `source_project_config_apply.py: _per_item_apply_config` 把该字段拷到 VSM 自身的 per_item 配置 |
| VSM 状态机 | `source_per_item_mixin.py: _per_item_should_start_cycle` 读取，True 时改"严格等量" + 次步同步 ≥ |
| Monitor | `frontend/src/views/Monitor/PerItemPanel.vue` 提示当前是否启用 |

### 7.2 `pipeline_config.per_item.disable_auto_settle`（项目级 bool）

| 链路环节 | 表现 |
|---|---|
| 前端 UI | 项目管理 → 逻辑设置 → 「手动结算模式」开关 |
| POST /projects 字段 | `pipeline_config.per_item.disable_auto_settle` |
| DB | `projects.pipeline_config` JSON |
| set_project_config | 同上 _per_item_apply_config |
| VSM 状态机 | per_item mixin 内所有自动收尾路径都加守门 — 跳 sustain_frames 满 / cycle_max_seconds 到 / 收尾标签触发；只接受 `per_item_manual_settle / per_item_manual_force_start` |
| 配套 API | `POST /api/v1/source/detection/per-item-control` (`source_routes.py`) 守门 logic_mode + action 枚举 |
| Monitor | PerItemPanel 显示「手动结算」「强制开始」两个按钮 |

### 7.3 `steps_config[i].per_item.box_max_width / box_max_height`（步骤级 float, 0-1 归一化）

| 链路环节 | 表现 |
|---|---|
| 前端 UI | 项目管理 → 步骤设置 → 展开某步骤 → 「Box 尺寸上限」两个数字输入框 |
| POST /projects 字段 | `steps_config[i].per_item.box_max_width / height` |
| DB | `projects.steps_config` JSON 字段 |
| set_project_config | `source_project_config_apply.py` 把字段拷到 VSM 的步骤级 per_item 配置 dict |
| 检测路径 | `source_detect_runners_mixin.py: _passes_box_size_limit` 在 3 种 runner（detect / detect+track / segment）里都跑一遍，按归一化坐标过滤 |
| 影响范围 | post-YOLO 守门，**不影响推理本身**，只影响下游使用的检测列表 |

### 7.4 改这 4 个字段的全链路测试

```bash
pytest tests/test_per_item_v310_features.py -v
# 7 个端到端测试，覆盖：
#   - per-item-control API 拒非 per_item 模式 / 拒未知 action
#   - require_exact_count = True 漏件不开周期，凑齐立即开周期
#   - disable_auto_settle = True 时手动 settle 才结算
#   - force_start 立即开周期
#   - box_max_width/height 配置正确解析到 VSM
#   - SOP 字段 step_screenshot 在检测结果里如约暴露
```

### 7.6 `pipeline_config.ng_remediation`（v3.23 通用 NG 补做策略, 任意 logic_mode）

任意检测模式通用的「缺步骤 / 少装数量 NG 经人工确认后就地补做、不重置周期」开关。**嵌套在
pipeline_config 内, 不拍平到顶层**, 因此不动 ORM / Pydantic schema / projects.py / Navbar。

```jsonc
"ng_remediation": {
  "enabled": false,      // 总开关 (默认关 = 行为零差异)
  "allow_step": true,    // 允许补步骤 (缺某步时补做该步)
  "allow_count": true    // 允许补数量 (少装时补齐到目标, 如包装滑块)
}
```

| 链路环节 | 触点 |
|---|---|
| 前端 UI | 项目管理 → 逻辑设置 Tab 顶部「NG 补做策略」卡片 (所有模式可见) |
| 前端默认值 | `Project/index.vue: initProjectDefaults` 兜底建对象 |
| 前端保存 | `Project/index.vue: handleSaveProject` 写回 `pipeline_config.ng_remediation` |
| Monitor 透传 | `syncProjectConfig` 整 pipeline_config 展开自动带上 (无需单独发) |
| 后端解析 | `source_project_config_apply.py: _apply_pipeline_config` → `h._ng_remediation` |
| 状态初始化 | `source_state_init.py` 默认 `{enabled:False, allow_step:True, allow_count:True}` |
| 结果暴露 | `source_routes.py` 检测结果 `ng_remediation` 字段 (前端确认弹窗据此显示补做按钮) |
| 包装消费 | `source_session_lifecycle_mixin.py` cycle_end 把 `_ng_remediation` 传给包装协调器 |

> 注意: 此开关只是「策略位」。包装「补滑块」消费它 (见 debug-mes); 检测「补步骤」消费它的
> 实现在后续版本 (延迟落账点改核心状态机)。没开 = 任何模式行为零差异。

### 7.5 改 per_item 配置前的必读检查

1. **前端 UI 改了吗？** 不改的话客户只能手动 PUT JSON
2. **`source_project_config_apply.py` 解析了新字段吗？** 不解析配置进不了 VSM
3. **VSM 实际用了字段吗？** apply 后还要在 mixin 业务方法里读 — 漏一处就是死配置
4. **守门 / 模式互斥规则改了吗？** v3.12.0 新加的字段都属于 per_item 模式独占，非 per_item 时**应静默忽略**（不是报错）
5. **新加 per_item 字段时同步加测试**：参考 `tests/test_per_item_v310_features.py` 的 7 个用例模板

---

## v3.44.0：pipeline_config 新增 `ng_handling` 统一块（NG 判定与处置）

**取代四组 legacy 键**：`ng_remediation` / `closing_guard` / `instant_ng_on_violation` / `strict_order_violation_event_id`。前端保存只落新块（老键不再回写）；后端 `resolve_ng_handling` 读兼容——无新块时从 legacy 合成等价档位，**老项目零迁移零差异**。

结构（全默认 = 老行为）：
```json
"ng_handling": {
  "violation": "none|hint|instant_ng", "violation_event_id": null,
  "missing_step": "ng|ack|hold", "hold_timeout_s": 120, "hold_event_id": null,
  "short_count": "ng|ack",
  "gate_enabled": false, "gate_steps": [], "gate_event_id": null
}
```

**改这块的全链路**：前端 `LogicConfigTab.vue`「NG 判定与处置」卡（一行一场景）→ `index.vue` 加载合成/保存序列化（last_first 强制 violation=none）→ 后端 `resolve_ng_handling` 归一 → 展开到既有运行时属性（状态机零改动）。事件页 `EventsConfigTab.vue` 有反向联动明示（NG 事件的定格弹窗按钮来源）。迁移矩阵回归：`tests/test_ng_handling_resolver.py`。

⚠️ 加新处置场景时**别再起新顶层键**——往 `ng_handling` 里加行，前后端合成/序列化/e2e 三处同步。
