# 逐件漏打检测 · 实时看板 + 离场快照方案（内部 RFC）

> ⚠️ **本文已归档（v3.28.0 落地，2026-06-27）**：离场快照判定 + 漏打挂起待补已实现于
> `backend/api/source_per_item_mixin.py`（默认全关，老项目零差异）。
> 现状以代码与 `debug-per-item` skill 为准，本文仅供决策考古。

> 面向：项目主作者 / 后端 / 前端开发
> 归位：**主程序原生**（多客户通用的逐件模式能力补强，扩展点 #2）
> 状态：已实施（原文保留如下）
> 关联代码：`backend/api/source_per_item_mixin.py`、`backend/api/source_event_trigger_mixin.py`
> 关联客户文档：`docs/逐件漏打检测_方案说明_客户版.md`

---

## 〇、TL;DR

客户要"漏哪颗报哪颗 + 补打撤警 + NG 不放行"。原始设想依赖一个"打完一圈"的时刻触发判定，但**该时刻不可靠**（见 §一推理证据）。

最终方案：**判定与取件信号解耦**——

1. 周期全程：实时点位看板（每颗螺丝绿/灰），工人自己看进度防漏，**不报警**。
2. 工件离场（工件标签持续消失）→ 取覆盖快照判 OK/NG。
3. NG → 红灯 + 高亮漏点 + 进入「待补/待确认」中间态（**不立即结案**）：
   - 工件放回补满 → 撤红转绿 → OK 落账；
   - 没补就走 / 人工确认 NG → 计 NG + 人工确认放行（复用 v3.22/v3.23 既有机制）。

核心新增 = 在现有「判定即结案 reset」之间插入一个「待补/待确认」态 + 一条「工件归零」离场触发。

---

## 一、问题与推理证据

### 1.1 死结：拿取标签 = 整件端走，太晚

ZJ 项目（`projects.id=3`，`logic_mode=per_item`）当前用收尾标签「拿取结算」+ `finish_requires_no_items=true` 结算。但"拿取" = 把整个工件端离画面，**等这个信号出现时工件和螺丝都没了**，无法停在原地显示漏点、无法补打。

### 1.2 推理实测（ZJ3 模型跑客户视频 589s，采样 5.25fps，3093 帧）

| 观测项 | 结果 | 结论 |
|--------|------|------|
| 螺丝检出（5N×14 / 7N×4） | 工件在场期（7.8s–330s）**每帧稳定 14+4** | 判"漏哪颗"基础可靠 |
| 工序动作标签（扭5N/扭7N） | 仅 7.8s–284.6s 稀疏出现 | — |
| 动作停顿 ≥2s | **17 次**（含 22.3s、53.9s 超大停顿） | **"停手 N 秒 → 打完"不可行**：2s 误判 17 次；要跨过 53.9s 那次得 >54s，"2 秒内判定"落空 |
| 「拿取结算」标签 | 2.5s 即误触发，末次 329s | 该标签不可靠，离场改用"螺丝归零"判定 |

> 一句话：**除了"工件端走"，没有任何可靠信号标记"这一圈打完了"**。所以放弃"打完时刻判定"，改用"离场快照判定 + 全程实时看板"。

---

## 二、方案选型

排除项与采用项：

- ❌ 停手触发：数据证明停顿天然频繁，不可用。
- ❌ 全覆盖触发判 OK：漏件时永不全覆盖，这条对 NG 场景无效（但保留作 OK 快路径）。
- ❌ 加完成手势/按钮新信号：客户当前不接受改产线/重标注（用户选项已排除）。
- ✅ **实时看板 + 离场快照**：用"工件在不在画面"当干净边界，工人全程看屏自检，离场判 OK/NG。

### 关键语义改变（必须客户签字）

原设想"打完→停 2 秒→红灯→补打"中的"打完时刻"消失，替换为：

```
全程实时看板(工人自检) → 工件离场 → 快照判定 → NG 则待补/待确认
```

后果：实时看板是**防呆进度提示**，不是"漏件报警"；真正的红灯报警只在**离场快照判 NG**时发生。

---

## 三、状态机改造（核心）

### 3.1 现状（`_update_step_stats_per_item` + `_per_item_settle_cycle`）

```
周期开始(稳定锁定) → 周期内逐帧 apply_coverage(覆盖单调 false→true)
   → 结算触发(settle_after_all_done / finish_label / 超时)
   → _per_item_settle_cycle: 判 OK/NG → _trigger_event(1/2) → 立即 reset 周期
```

### 3.2 改造后（插入「待补/待确认」中间态）

```
周期开始 → 周期内逐帧覆盖(看板实时刷新, 不报警)
   → [新] 工件归零持续 leave_confirm_frames 帧 = 离场
        ├ 全覆盖 → OK → _trigger_event(1) → reset           (沿用现有 OK 路径)
        └ 有漏  → NG → 红灯+高亮漏点 → 进入 AWAIT_REMEDIATION 态(不 reset)
                   ├ 工件放回(item 标签重现) + 漏件补满 → 撤红 → OK → reset
                   ├ 人工确认 NG → _trigger_event(2, require_ack) → 定格 → 确认后 reset
                   └ 超时(remediation_timeout_sec) → 按 NG 落账
```

新增 session 状态（`_PerItemSession.__slots__` 扩展）：

| 字段 | 含义 |
|------|------|
| `phase` | `'running' / 'await_remediation'`（替代单一 `cycle_active` 的隐式语义） |
| `leave_consec_frames` | 工件标签连续消失帧数（离场确认计数） |
| `ng_snapshot` | 离场判 NG 时的漏件快照（step_label / missing_item_ids / 覆盖数） |
| `await_since` | 进入待补态的时刻（配 remediation_timeout_sec 用） |

### 3.3 覆盖快照语义（读代码后的简化）

因不变量 §一.1「覆盖单调 false→true 不可回滚」，**离场那一刻每个 `_PerItemItemState.covered` 即周期累积结果**。所以快照 = 直接读当前 `step.items[*].covered`，**无需回溯历史帧**。这比初版设想简化一大块。

> 注意遮挡：锁定模式（`lock_count_on_start=true`）下 `cleanup_stale_items` 是 no-op（行 288-289），被手挡住的未覆盖件不会被清掉，covered 状态稳定保留。✅ 与本方案天然契合。

### 3.4 「补满撤红」如何不违反单调性

补打 = 漏件的 `covered` 从 false→true，**符合单调**。难点是：离场后周期还没 reset，工件放回时 item 标签重现，要继续在原 `items` 上 `apply_coverage`。实现：`await_remediation` 态下仍跑覆盖更新逻辑（复用 `apply_coverage`），全部 covered 后自动转 OK。

---

## 四、离场触发设计

新增「工件归零」判定，**不依赖 finish_label**：

```python
# 伪代码, 放在 _update_step_stats_per_item 周期内分支
all_item_labels = union(step.item_label for step in self._per_item_steps)
any_item_present = any(boxes_by_label.get(lbl) for lbl in all_item_labels)
if not any_item_present:
    sess.leave_consec_frames += 1
    if sess.leave_consec_frames >= cfg['leave_confirm_frames']:
        self._per_item_enter_leave_judgement(current_time)   # 取快照判定
else:
    sess.leave_consec_frames = 0
```

- `leave_confirm_frames` 默认建议 ≥ 10 帧（防手部短暂遮挡全部螺丝误判离场）。
- 与现有 finish_label 路径**互斥**：开 `judge_on_workpiece_leave` 时关 finish_label 分支，避免双触发。

---

## 五、实时点位看板（前端）

后端已现成：`get_per_item_state()` 已逐颗返回 `{id, bbox, covered, covered_at}`（行 107-113 / 342）。前端只需增强展示：

| 文件 | 改动 |
|------|------|
| `frontend/src/views/Monitor/PerItemPanel.vue` | 把抽象格子改成**按 bbox 真实空间坐标布点**的点位图：绿=已覆盖、灰=待打、红=离场判定后的漏点（高亮闪烁） |
| `frontend/src/views/Monitor/index.vue` | 离场判定/待补态时，右侧卡显示"漏 N 件 · 点位 #x/#y · 待补打或确认" |

> 可参考跟踪模式已有的位置小地图 `TrackingMinimap.vue`（技术债扩展点 #3 提到过）。

---

## 六、报警灯柱联动（灯全部事件驱动，可配）

> **设计原则（客户明确要求）**：灯**不写死**，全部做成"事件"，由用户在报警配置里把事件映射到灯/蜂鸣。代码只负责"在正确时机触发正确的事件"。

- **OK → 绿** / **NG → 红+蜂鸣**：走标准事件 1 / 2（`_trigger_event(1/2)` 已联动 `alarm_router`，用户在报警配置里映射 event1/event2 的灯色）。
- **"场上还有没扭的螺丝"（待补态）→ 可配事件**：进待补态时触发 `remediation_event_id` 指定事件的报警（`alarm_router.trigger_alarm(f'event{id}')`，**只点灯不落账**）。`remediation_event_id=0` 时不主动触发，纯靠待补状态联动。**不再写死 event2**（已改）。
- **补满撤灯**：补满 → `_trigger_event(1)`（OK）→ 报警系统 `_recompose_and_apply` 自动切到 event1 视觉（绿）。已确认报警系统具备 `light_off/all_off/stop_alarm/restore_idle_light` + 事件重组能力，**无需改报警子系统**。

---

## 七、人工确认 / NG 保护（复用，不新写）

v3.22/v3.23 既有机制（`source_event_trigger_mixin.py`）：

- `_pending_ack`（行 73-75）：阻塞态，整条检测线定格，后续事件丢弃 → 实现"NG 件不处理不运行下步骤"。
- `require_ack=True`（行 409-412）：事件标"需人工确认"→ 进入定格。
- `_enter_step_remediation_hold`（行 542）+ `resolve_step_remediation`（行 596）：NG 补做挂起，保留缺项快照，`'supplement_step'` 信任人工补做后判 OK 落账。

**接法**：给 ZJ 的 NG 事件配 `require_ack=True`；per_item 的 NG 走 `_trigger_event(2, ...)` 时自动进定格。需验证 per_item 路径下定格 + 解除链路完整（现有机制主要在 sequential 验证过）。

---

## 八、新增配置字段（全部可配，默认关 = 老项目零差异）

> **铁律（客户明确要求）**：本对话提出的每一项改动都必须是**可配置项**，默认全关，开关一关则与改前行为一字不差。已落实——下表默认值全为 false/0，且 `judge_on_workpiece_leave=false` 时新代码整条分支 `return` 跳过。

### 项目级 `pipeline_config.per_item`（已实现）

| 字段 | 类型 | 默认 | 含义 |
|------|------|------|------|
| `judge_on_workpiece_leave` | bool | false | 启用"工件离场快照判定"（开则关 finish_label / settle_after_all_done / idle 分支） |
| `leave_confirm_frames` | int | 10 | 工件标签连续消失 N 帧算离场（防遮挡误判） |
| `ng_hold_for_remediation` | bool | false | 离场判 NG 后进入待补态而非立即结案 |
| `remediation_timeout_sec` | float | 0 | 待补态超时（>0 时超时按 NG 落账；0=只等人工确认） |
| `remediation_event_id` | int | 0 | "场上还有没扭螺丝"（待补态）触发哪个事件的报警（灯由该事件配置决定，**不写死**）；0=不主动触发 |

### 检测框颜色可配（前端阶段实现，配置位先设计）

客户要求：扭完 / 没扭完的螺丝检测框颜色可自定义。设计：

| 字段 | 类型 | 默认 | 含义 |
|------|------|------|------|
| `per_item.box_color_covered` | str | `#10b981`(绿) | 已扭螺丝的检测框颜色 |
| `per_item.box_color_uncovered` | str | `#9ca3af`(灰) | 待扭螺丝的检测框颜色 |
| `per_item.box_color_missing` | str | `#ef4444`(红) | 离场判定后漏点的高亮色 |

> 实现要点：视频叠加绘制路径需按每颗螺丝的覆盖状态上色。逐件状态已逐颗带 `covered` + `bbox`，前端/绘制层按位置交叉匹配检测框着色（draw 路径，归前端阶段）。

> 以上均为**模式内参数演进**（类似已有的 `require_exact_count` / `disable_auto_settle`），非"客户开关污染"。

---

## 九、影响分析

### 改动文件清单

| 文件 | 改动职责 | 风险 |
|------|----------|------|
| `backend/api/source_per_item_mixin.py` | 新增离场触发 / 待补态 / 快照判定；扩展 `_PerItemSession` 字段 | 🔴 核心状态机，碰不变量 §一（覆盖单调要保住） |
| `backend/api/source_project_config_apply.py` | 解析 4 个新配置字段 | 🟢 末尾追加 |
| `backend/api/source_event_trigger_mixin.py` | 验证 per_item NG 走 `require_ack` 定格链路（大概率无需改） | 🟡 验证为主 |
| `backend/api/alarm.py`（待定） | 若需"实时状态灯/主动灭灯"则补通道 | 🟡 取决于 §六核实结果 |
| `frontend/src/views/Monitor/PerItemPanel.vue` | 点位图（绿/灰/红高亮） | 🟡 碰 .vue → T4/T5/T6 强制 |
| `frontend/src/views/Monitor/index.vue` | 待补态右侧卡文案 | 🟡 碰 .vue → 同上 |
| ZJ 项目配置（DB） | 删「涂黑」步骤；开新字段；NG 事件标 require_ack | 🟢 数据配置 |

### 不变量核对

- ✅ §一.1 覆盖单调：补打 = false→true，不破坏。
- ✅ §一.4 个体表锁定：待补态沿用锁定 items，不重锁。
- ⚠️ AGENTS.md 核心不变量 #11（settlement_mixin 守门）：per_item 是独立路径，不碰 settlement_mixin，但改 `_per_item_settle_cycle` 前要确认不影响 ZJ 已用的 `settle_after_all_done_sec=2` OK 快路径。
- ⚠️ 走 `modify-source` skill 做完整调用链影响分析（VSM 多继承、has-a 兼容层）。

### 兼容性

- 老 per_item 项目（新字段默认 false）行为不变。
- ZJ 需改配置（删涂黑、开新字段、NG require_ack）——属配置迁移，非代码强制。

---

## 十、测试矩阵

| 编号 | 场景 | 预期 |
|------|------|------|
| T-A | 18 颗全打 → 端走 | 离场判 OK，绿灯，正常计数 |
| T-B | 漏 1 颗 → 端走 | 离场判 NG，红灯+高亮该点，进待补态 |
| T-C | T-B 后工件放回 + 补满 | 撤红转绿，OK 落账，无 NG 计数 |
| T-D | T-B 后没补直接拿走/人工确认 NG | 计 NG + 定格，确认后放行 |
| T-E | 打到一半手遮全部螺丝 <leave_confirm_frames 帧 | **不**误判离场 |
| T-F | 打螺丝间停顿 22s/54s（复刻视频） | 周期不误结算（不再依赖停手） |
| T-G | 老 per_item 项目（新字段关） | 行为与改前完全一致（回归） |

- 单测/合成：扩 `tests/test_per_item_v39_features.py` / `test_synthetic_per_item.py`。
- 可见浏览器 UAT（碰 .vue 强制 T4-T7）：用 ZJ3 + 客户视频回放跑 T-A~T-F，录视频+截图+run.log。

---

## 十一、待核实清单

1. ✅ **报警系统能主动灭灯**（§六）——已确认 `light_off/all_off/stop_alarm/restore_idle_light` + 事件重组，无需改 `alarm.py`。灯已全部事件化。
2. ⏳ **per_item NG 走 require_ack 定格链路**（§七）——待补态本身已"冻结"（不开新周期），require_ack 用于确认 NG 后的整线定格；UAT 阶段验证完整性。
3. ⏳ **ZJ「涂黑」删除**后判定步骤只剩 扭5N(14)+扭7N(4)=18 ——ZJ 配置阶段处理。
4. ✅ **离场触发抖动**——`leave_consec_frames` 在工件重现时清零（已实现 + T-E 验证）。

## 十二、已实现 / 待办

- ✅ 后端：session 字段、4+1 配置、离场判定、待补态、补满转 OK、人工确认、灯事件化。测试 9 个（T-A~T-G + 2 个事件化报警）+ 既有 35 个回归全绿。
- ⏳ 前端：PerItemPanel 点位图 + 检测框颜色可配 + 待补态卡（碰 .vue，T4-T7 强制）。
- ⏳ ZJ 配置 + 可见浏览器 UAT。

---

**最后更新**：2026-06-26 · 待评审
