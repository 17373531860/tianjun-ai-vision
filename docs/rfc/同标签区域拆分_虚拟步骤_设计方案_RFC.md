# RFC: 同标签区域拆分（虚拟步骤）+ 工件就位提示

> ⚠️ 本文已归档（v3.32 落地，待发版）。现状以 `docs/dev/reference/config-dict.md` 的 `label_splits` / `placement_guide` / `strict_order_violation_event_id` 条目与代码 `backend/api/source_label_split.py` 为准，本文仅供决策考古。

- **状态**: 已实现（fixed 固定画面 + anchor 锚点跟随 + 多轮次 + 每轮独立区域 + 违序即时事件 + 就位提示三档显示策略）
- **日期**: 2026-07-05
- **场景来源**: 电机装配线客户——前/后罩各 4 颗螺丝要求对角顺序，模型只能出「打螺丝」一个标签，螺丝本体被罩子完全遮挡，只能靠"打的位置"区分是哪一颗。

---

## 一、问题定义

1. 模型输出单一动作标签（如「打螺丝」），但业务上同一动作在**不同位置**发生代表**不同步骤**（螺丝1~4）。
2. 现有逐步骤 ROI（`steps_config[].roi`）以 label 为键——**一个标签只能配一个 ROI**，无法表达"同标签 × 多区域 = 多步骤"。
3. 工件放置位置有偏差：需要 (a) 画面引导框让工人把工件放进框里；(b) 或区域跟随某个"锚点"目标（如已装好的罩子）平移/缩放。

## 二、方案总览：检测出口的"标签改写层"

在检测结果进入状态机**之前**加一层预处理（`backend/api/source_label_split.py`）：

```
模型/剧本 detections ──► 标签区域拆分层 ──► 状态机(_update_step_stats) / 前端画框 / MES / 导出
   label=打螺丝            按框中心命中哪个区域
                           改写 label=螺丝1/螺丝2/...
```

改写后下游全部现有机制（顺序模式、自定义模式、计数、事件、MES、导出、Monitor）**零修改**直接工作——虚拟步骤就是普通步骤（`steps_config` 里真实存在的行，label=区域名）。

**挂点**: `source_inference_loop_mixin._inference_select_and_run_model` 两条返回路径（模型路径 + synthetic 剧本路径）统一调 `h._apply_label_splits(detections)`。synthetic 也过这一层 → 全链路可用剧本回归。

## 三、配置结构（`pipeline_config` 新增两键）

```jsonc
"label_splits": [
  {
    "id": "ls_xxx",
    "enabled": true,
    "source_label": "打螺丝",          // 模型原始标签
    "mode": "fixed",                   // fixed=固定画面 | anchor=锚点跟随
    "anchor_label": "前罩",            // anchor 模式: 锚点目标标签
    "anchor_ref": {"x":0.3,"y":0.3,"w":0.4,"h":0.4},  // anchor 模式: 标定时锚点框(归一化)
    "anchor_hold_seconds": 3.0,        // 锚点短暂丢失时沿用最近位置的时长
    "unmatched": "drop",               // 未命中任何区域: drop=丢弃 | keep=保留原标签 | map=改写
    "unmatched_label": "位置外打螺丝",  // unmatched=map 时的改写目标(也可配成步骤/挂事件)
    "regions": [
      {"name": "螺丝1", "polygon": [[0.1,0.1],[0.4,0.1],[0.4,0.4],[0.1,0.4]], "color": "#f97316"},
      {"name": "螺丝2", "polygon": [[0.6,0.1],...], "color": "#22d3ee"}
    ]
  }
],
"placement_guide": {                   // 工件就位提示(独立功能, 与拆分正交)
  "enabled": false,
  "anchor_label": "前罩",              // 用哪个标签判定工件位置
  "polygon": [[0.2,0.2],...],          // 引导框(归一化多边形)
  "mode": "hint",                      // 一期仅 hint=提示; gate=拦截 留二期
  "display": "always"                  // v1.1 就位后显示策略(纯前端渲染, 后端不消费):
                                       //   always=常驻(变绿) | fade_on_ready=淡化(半透明细框无文字)
                                       //   | hide_on_ready=隐藏; 未就位时永远完整黄色提醒
}
```

**语义要点**：

- 区域 `name` 即虚拟步骤 label。前端保存拆分规则时自动在 `steps_config` 生成/同步同名步骤（带 `split_origin: rule_id` 标记，用于表格徽标 + 级联删除）。虚拟步骤的阈值/时长/min_frames/顺序等全部沿用普通步骤配置。
- `fixed` 模式：多边形是画面归一化坐标，工人须把工件放到引导框内（配合 placement_guide）。
- `anchor` 模式：多边形仍按**标定快照**的画面坐标存，另存标定时的锚点框 `anchor_ref`；运行时按"当前锚点框 vs 标定锚点框"的平移+缩放仿射把多边形移动到当前帧位置。旋转不支持（现场用定位销/托盘约束，见风险）。
- 同一个 `source_label` 只允许一条启用规则；不同 source_label 可多条规则并存。
- 置信度阈值时序：拆分前原始标签不做步骤阈值过滤（模型 conf 已过）；拆分后虚拟标签在状态机入口按各自步骤阈值过滤——与现状一致。

## 四、后端改动点

| 文件 | 改动 |
|---|---|
| `backend/api/source_label_split.py` | **新增**。纯函数解析 + 运行时改写：`parse_label_splits` / `parse_placement_guide` / `LabelSplitEngine.apply(detections, now)`（含锚点缓存、仿射变换、未命中策略、就位判定） |
| `backend/api/source_inference_loop_mixin.py` | `_inference_select_and_run_model` 两条 return 前调 `self._apply_label_splits(detections)` |
| `backend/api/source_project_config_apply.py` | `apply_project_config` 解析 `pipeline_config.label_splits` / `placement_guide` → 构建引擎实例挂 host |
| `backend/api/source_state_init.py` | 初始化 `_label_split_engine=None` / `_placement_state` |
| `backend/api/source.py` | `_get_enabled_labels` 追加拆分规则的 source_label / anchor_label / 就位 anchor_label（否则 runner 层会把原始标签当"非步骤标签"直接丢掉，拆分层永远收不到） |
| `backend/api/source_step_stats_mixin.py` | 状态机入口标签收集处加一行守门：项目有步骤配置时，非步骤标签（锚点、keep 保留的原始标签）不进入步骤统计/录像（还原 runner 过滤不变量） |
| `backend/api/source_routes.py` | `/detection/results` 暴露 `placement_guide` 运行态（enabled/in_position/anchor_visible） |

## 五、前端改动点

| 文件 | 改动 |
|---|---|
| `frontend/src/views/Project/LabelSplitDialog.vue` | **新增**。多区域编辑器：快照底图 + 区域列表（命名/颜色/重画/删除）+ 逐区域画多边形（交互与 RoiEditorDialog 一致）+ 四象限模板一键生成 + anchor 模式标定（从当前检测结果抓锚点框） |
| `frontend/src/views/Project/StepsConfigTab.vue` | 新增「同标签区域拆分」卡片（规则列表 + 新建/编辑/删除/启停）+「工件就位提示」卡片；步骤表格给 `split_origin` 步骤加"拆分"徽标 |
| `frontend/src/views/Project/index.vue` | `initProjectDefaults` 补 `label_splits`/`placement_guide` 默认值；`handleSaveProject` 白名单登记两键 + 保存时同步虚拟步骤；编辑器打开/保存编排 |
| `frontend/src/views/Monitor/index.vue` | 画布叠加：拆分区域多边形+区域名（anchor 模式按当前锚点框实时变换）、就位引导框（就位=绿/未就位=黄虚线）+ 顶部就位提示条；单工位 `drawDetections` 与多工位 `drawMultiDetections` 共用同一 helper |

## 六、通用性（不止这一个客户）

- 多穴位点胶/锁付/焊点（同一动作标签按位置拆步骤）
- 左右工位共用一个画面（同标签按左右半区拆成两个逻辑步骤）
- 未命中区域改写成"位置外操作"步骤 → 挂静态步骤触发事件 → 报警（乱打位置检出）
- 就位提示独立可用：任何"要求工件放在指定位置再开始"的场景

## 七、风险与边界

| 风险 | 处置 |
|---|---|
| 工件旋转 | 一期不支持（fixed 靠引导框物理约束；anchor 只做平移+缩放）。视觉旋转对齐留二期 |
| 边界抖动（框中心在区域边缘跳） | 沿用步骤 min_frames 消抖；建议区域画大一点、彼此留空隙 |
| 区域名与现有步骤重名 | 前端保存时校验：区域名不得与非本规则来源的步骤 label 冲突 |
| 锚点丢失 | anchor_hold_seconds 内沿用最近位置；超时按 unmatched 策略处理 |
| 原始标签/锚点标签进状态机产生副作用 | step_stats 入口守门（第四节最后一行），非步骤标签不进统计/录像 |
| keep 策略 + 原始标签本身也是步骤 | 允许（显式配置组合），命中区域的改写优先，未命中的保留原标签走原步骤 |

## 八、测试

- `tests/test_label_split.py`：解析/改写纯函数单测（fixed 命中、三档未命中、anchor 仿射、锚点保持、就位判定）
- `tests/test_label_split_pipeline.py`：synthetic 剧本走真实 pipeline——「打螺丝」在 4 个象限先后出现 → 顺序模式判 OK；乱序 → NG
- `tests/e2e_browser/test_label_split_ui.py`：项目页新建拆分规则 → 落库 GET 验证 → 虚拟步骤出现在步骤表
- `tests/uat/uat_20260705_label_split.py`：可见浏览器 UAT（三件套证据）

## 九、v1.1 增量：多轮次拆分 + 严格顺序违序即时事件（2026-07-06）

客户场景澄清后补的两块（电机装配线实测需求）：前罩/后罩各打 4 颗且**打的位置在画面里完全重叠**，
只能靠工序时序区分；打错对角顺序要**当场报警**，不能等周期结算。

### 9.1 多轮次拆分（rounds）

- 配置：拆分规则新增 `rounds` 段 `{enabled, trigger_label, count(2~8), prefixes[], trigger_gap_seconds,
  region_overrides?}`
- 语义：切换标签（如「盖罩」）每次**重新出现**（离场 > gap 秒后再入画）→ 轮次 +1，满轮回绕第 1 轮；
  虚拟步骤名 = 当轮前缀 + 区域名（前罩螺丝1 / 后罩螺丝1）
- **每轮独立区域**（`region_overrides: {"轮次": [{name,polygon,color}]}`，翻面后位置不重叠时用）：
  某轮配了独立区域就用独立的那批做命中判定/步骤展开，没配的轮沿用共享 `regions`；
  编辑器区域列表提供「共享区域 / 第N轮」作用域切换 + 「复制共享区域到本轮再调整」；
  某轮 override 全部非法 → 该轮回退共享区域（不整体禁用轮次），Monitor 叠加层按当前轮画对应那批区域
- 归零：周期已结算（cycle_len=0）且切换标签离场超过 gap → 轮次归 0，下一工件从第 1 轮起；
  周期未结算时切换标签离场多久都不归零（工序中途遮挡不丢轮次）
- 兜底：切换标签从未出现时按第 1 轮前缀改写（前后端同语义）
- 运行态：`GET /source/detection/results` 新增 `label_split_rounds`
  `{source_label: {round, count, prefix, trigger_label}}`，Monitor 叠加层区域名带当轮前缀 + 轮次角标
- 引擎侧改动全部在 `source_label_split.py`（`_parse_rounds` / `_update_rounds` / `snapshot_rounds`）；
  推理挂点把 `len(current_cycle_steps)` 传给 `engine.apply` 做归零判定；
  `_get_enabled_labels` 追加 rounds.trigger_label（否则 runner 过滤后引擎收不到、轮次永远不切）

### 9.2 严格顺序违序即时事件

- 配置：`pipeline_config.strict_order_violation_event_id`（null=关，零差异）；
  UI 在逻辑设置 → 结算方式卡片（last_first 模式隐藏并保存时置空——该模式严格顺序被强制清空）
- 语义：勾了「严格顺序」的步骤在错误时机出现 → **照旧拦截不计入周期**（既有语义不变），
  同时当场 `_trigger_event(event_id, reason)`（报警/语音/弹窗/计数走事件体系）
- 触发点：`source_settlement_mixin` 两处严格守门（前置步骤缺失 gate + 严格+单次错位 gate），
  收口到 `_fire_strict_order_violation`
- 节流：被拦步骤不记 last_seen → 每帧都算新出现，必须节流——同一 (标签, 周期进度) 5 秒一次，
  周期推进后可再报
- 建议配警告/自定义类事件；配 NG 类事件会走完整 NG 计数+MES 推送，慎用

### 9.3 就位引导框显示策略（display）

- 现场诉求：工件放好后绿框常亮碍眼 → 就位提示新增 `placement_guide.display` 三档
  `always`(常驻变绿, 默认=首发行为) / `fade_on_ready`(淡化: 半透明细框无文字) / `hide_on_ready`(隐藏)
- 边界：**未就位时永远完整黄色提醒**，策略只作用于"已就位"状态；纯前端渲染策略，
  后端 `parse_placement_guide` 不消费，运行态 `in_position` 照旧透出
- 改动：`StepsConfigTab.vue` 就位卡片加下拉（老配置无该字段自动补 always）、
  `Project/index.vue` 保存白名单收编、`Monitor/index.vue` 叠加层按档位画/淡/跳过

### 9.4 v1.1 测试

- 单测：`tests/test_label_split.py` 新增 9 条（rounds 解析非法回退 / 切轮 / 回绕 / 持续在场不重切 /
  空闲归零 / 周期未结算不归零 / map 标签不挂前缀 / snapshot 形状）
  + 4 条每轮独立区域（解析 / 非法 override 丢弃 / 本轮生效他轮回退 / 未开始按第1轮兜底）
- 管线：`tests/test_label_split_pipeline.py` 新增两轮 8 步 OK 结算 + 越序当场触发违序事件（拦截语义不变）
- CI E2E：`tests/e2e_browser/test_label_split_tab.py` 新增多轮次规则落库（8 虚拟步骤+rounds 段）
  + 违序事件配置落库 + 就位提示显示策略落库 + 每轮独立区域落库（复制共享→region_overrides 只落配了的轮）
- UAT：`tests/uat/uat_20260706_label_split_rounds.py`（20/20，三件套证据）
  + `tests/uat/uat_20260706_placement_guide_display.py`（10/10，三档显示策略画布像素级验证）
  + `tests/uat/uat_20260706_label_split_round_override.py`（16/16，第2轮位移剧本：
    老位置只在第1轮算数、切轮后只认独立区域、共享区域不误用）
