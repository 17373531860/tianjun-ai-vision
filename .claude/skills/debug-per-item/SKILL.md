---
name: debug-per-item
description: "诊断 per_item「逐件覆盖」模式：周期不开始 / 不结算 / 漏件 NG 不触发 / 个体被误清理 / PerItemPanel 空白 / mock 注入失败。当客户反馈"打螺丝场景"类按件覆盖任务异常时使用，包括状态机、稳定窗口、个体表锁定、覆盖判定、ENABLE_DEV_MOCKS 守门、PerItemPanel 视觉链路。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__sequential-thinking"
---

# debug-per-item: 逐件覆盖模式诊断（v3.8.0 引入 / v3.9.0 五补丁扩展）

per_item 是 logic_mode 的**第 5 种**模式（前 4 种为 `sequential` / `detection` / `tracking` / `custom`），
为「画面里 N 个独立个体 → 工序逐件覆盖 → 收尾标签结算」场景设计（典型：打螺丝、点焊检验、涂胶）。

用户问题: $ARGUMENTS

---

## 一、模式架构地图

```
┌─────────────────────────────────────────────────────────┐
│ backend/api/source_per_item_mixin.py (~650 行, 独立路径)│
│                                                         │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ PerItemMixin (主 Mixin, VideoSourceManager 多继承)  │ │
│ │   _per_item_apply_config()      → 配置解析+建步骤   │ │
│ │   _update_step_stats_per_item() → 每帧推理后调      │ │
│ │   _per_item_try_start_cycle()   → 稳定窗口判断      │ │
│ │   _per_item_settle_cycle()      → 收尾标签触发结算  │ │
│ │   get_per_item_state()          → /detection/results│ │
│ └─────────────────────────────────────────────────────┘ │
│           │                  │                  │       │
│           ▼                  ▼                  ▼       │
│  _PerItemSession   _PerItemStep[]    _PerItemItemState  │
│  • cycle_active    • items: {id: …} • covered           │
│  • stability_buf   • completed       • consec_overlap_  │
│  • finish_label_…  • locked_count    • first_covered_at │
└─────────────────────────────────────────────────────────┘
        ▲
        │  入口分流（per_item 之外的代码路径完全绕开）
        │
┌───────┴─────────────────────────────────────────────────┐
│ source_step_stats_mixin.py:_update_step_stats           │
│   if logic_mode == 'per_item':                          │
│       return self._update_step_stats_per_item(...)      │
└─────────────────────────────────────────────────────────┘
```

**前端**：
- `frontend/src/views/Monitor/PerItemPanel.vue` — 视频下方全宽面板（仿 tracking 模式）
- `frontend/src/views/Monitor/index.vue` — `isPerItemMode` 计算属性 + 右侧"逐件实时反馈"卡（替换 NG TOP3）
- `frontend/src/views/Project/index.vue` — 项目级 + 步骤级配置 UI（"逐件模式"单选 + per_item 卡片）

**入口路径汇总**：

| 链路 | 文件 | 关键函数/字段 |
|------|------|---------------|
| 配置应用 | `source_project_config_apply.py` | 末尾调 `_per_item_apply_config(config)` |
| 状态机调度 | `source_step_stats_mixin.py` | `if logic_mode == 'per_item' → ...per_item` |
| 实时状态导出 | `source_routes.py:/detection/results` | 拼 `data['per_item_state'] = mgr.get_per_item_state()` |
| 开发 mock | `source_routes.py:/detection/per-item-mock` | `_dev_mocks_enabled()` 守门 |

---

## 二、配置 schema（schema 错最常见，先排查）

### 项目级 `pipeline_config.per_item`

| 字段 | 默认 | 引入版本 | 含义 |
|------|------|---------|------|
| `stability_window_frames` | 10 | v3.8 | 连续 N 帧画面稳定才算"周期开始" |
| `stability_iou_threshold` | 0.7 | v3.8 | **路径 B**: 跨帧同位置 box IoU 阈值（数量必须恒定） |
| `stability_count_ratio` | 0.85 | **v3.9** | **路径 A**: 检出数 ≥ `expected_count × ratio` 即放行（替换之前硬编码 0.85） |
| `stability_count_tolerance` | 0 | **v3.9** | **路径 A**: 与 `expected_count` 差几颗内允许启动 |
| `item_timeout_seconds` | 3.0 | v3.8 | 个体超时清理时间（**注意**：锁定模式 + 已覆盖件永远不清，见 §六.1） |
| `lock_count_on_start` | true | v3.8 | 周期开始锁定个体数 → 后续 dynamic 模式才会动态加 |
| `lock_lookahead_seconds` | 5.0 | **v3.9** | 配 `expected_count` 时，周期开始后 N 秒窗口内继续吸收漏锁个体（应对启动那一帧个别被遮挡） |
| `finish_label` | `""` | v3.8 | 收尾标签（出现 `finish_sustain_frames` 帧 → 触发结算）|
| `finish_sustain_frames` | 3 | v3.8 | 收尾标签需持续多少帧 |
| `settle_after_all_done_sec` | 0.0 | **v3.9** | > 0 时：所有 per_item 步骤都 completed 并保持 N 秒 → **立即 OK 结算**（不等收尾标签）|
| `cycle_max_duration_sec` | 0.0 | **v3.9** | per_item **专属**周期超时（替代项目级 `pipeline_config.cycle_max_duration`，老项目自动回落兼容）。> 0 + 周期总时长超此 → 强制结算（NG 兜底）|
| `idle_timeout_sec` | 0.0 | **v3.9** | per_item **专属**空闲超时（替代项目级 `pipeline_config.idle_timeout_seconds`，老项目自动回落兼容）。> 0 + 连续 N 秒无 **action 标签** → 强制结算（NG 兜底，**只看 action 不看 item**，否则工件静置永远 idle=0） |

### 步骤级 `steps_config[i].per_item`

| 字段 | 默认 | 引入版本 | 含义 |
|------|------|---------|------|
| `item_label` | — | v3.8 | 个体识别标签（如"螺丝"）。**v3.9 起兼容数组 OR**：`["5N螺丝","7N螺丝"]` 表示任一标签都算个体 |
| `action_label` | — | v3.8 | 工序覆盖标签（如"打螺丝"） |
| `expected_count` | 0 | **v3.9** | > 0 → 启用**路径 A 固定数量模式**（替代 auto 稳定窗口）；0 → 走老路径 B |
| `item_tracking_iou` | 0.3 | v3.8 | 个体跨帧 IoU（用于跟踪同一个个体） |
| `coverage_iou` | 0.3 | v3.8 | 工序框与个体框的覆盖 IoU 阈值 |
| `sustain_frames` | 5 | v3.8 | 工序与个体持续重叠 N 帧才算覆盖 |
| `completion` | `"all_covered"` | v3.8 | 完成判定（**本版仍仅实现 `all_covered`**；其他值会兜底 `False` 且**无报错日志**，配错会"周期永远不结算"——见 §八扩展点 #1） |
| `min_item_count` | `"auto"` | v3.8 | 路径 B 下最低个体数（`"auto"` 或固定数字）|

**步骤不填 `per_item` 字段** → 视为收尾步骤，**不进入 per_item 步骤列表**（典型：翻面步骤，仅靠 finish_label 触发）。

### v3.9 启动判定两条路径速查

```
┌─ 第 1 步配了 expected_count > 0 ──→ 路径 A (固定数量)
│   连续 stability_window 帧, 每帧检出数 ≥ max(1, target × ratio, target - tolerance)
│   → 立即锁定 + 进入周期 (锁定时挑窗口内"检出最多"的那一帧, 截顶到 expected_count)
│   优势: 抖动/数量轻微波动不影响; 工件螺丝数已知场景首选
│
└─ 第 1 步未配 expected_count ──→ 路径 B (auto 老路径)
    连续 stability_window 帧数量"完全恒定" + 相邻帧 NN-IoU > stability_iou_threshold
    → 锁定最末帧 + 进入周期
    适用: 不确定数量场景, 但抖动敏感
```

### v3.9 结算路径速查（按优先级）

```
1. settle_after_all_done_sec > 0 + 所有步骤已 completed 持续 N 秒 → OK 结算   (条件最严)
2. cycle_max_duration_sec    > 0 + 周期总时长 > N 秒                → 强制结算 (NG 兜底)
3. idle_timeout_sec          > 0 + 连续 N 秒无 action 标签           → 强制结算 (NG 兜底)
4. finish_label != ""        + 收尾标签持续 finish_sustain_frames 帧 → 结算
```

> 任一路径 1-4 触发后 `_per_item_settle_cycle()`，按当前所有步骤 `completed` 状态判 OK/NG。配置全 0 / 全空 → 周期永远不结算（v3.8 原行为）。

---

## 三、六大诊断流程（按频率排序）

### 故障 A：周期始终不开始（cycle_active 永远 false）

**症状**：前端 PerItemPanel 顶栏显示"等待稳定…"，进度条 0%。

**排查链**：

1. **set-project 是否真激活了 per_item**：
   ```bash
   curl -s "http://localhost:8001/api/v1/source/detection/results?channel=0" | jq '.per_item_state'
   ```
   - `null` → 当前项目根本没启用 per_item，检查 `pipeline_config.logic_mode`
   - 有但 `enabled=false` → 步骤 per_item 字段缺失，看日志 `[per_item] 步骤 [X] per_item 配置缺...`

2. **稳定窗口未填满（按路径分别排查，v3.9 起两条路径）**：

   先看后端日志开头的"周期开始"打印行确认走的哪条路径：

   ```bash
   grep "周期开始" /tmp/tianjun-backend.log | tail -3
   # 输出例: [per_item] 周期开始: 锁定首步个体数=12, frame_id=NNN, 路径=expected_count
   #        或 路径=auto
   ```

   **若走路径 A（`expected_count > 0`，v3.9）**：
   - 窗口内**每一帧**检出数 ≥ `max(1, expected_count × stability_count_ratio, expected_count - stability_count_tolerance)` 才放行
   - 调试技巧：把 `stability_count_ratio` 临时降到 `0.5`，看是否能启动 → 启动说明模型检出数普遍偏低
   - 配错 `expected_count`（例：写了 12 颗实际只能稳定检 9 颗）→ 永远启动不了

   **若走路径 B（`expected_count` 为 0 / 未配，v3.8 老路径）**：
   - 窗口里**数量必须完全恒定**（一帧出现一次抖动 → 整个窗口作废重来）
   - 跨帧 IoU > `stability_iou_threshold` 阈值
   - 模型置信度低 / 抖动大 → 永远不开周期
   - **临时解法**：调小 `stability_iou_threshold`（如 0.5）或缩 `stability_window_frames`（如 5），**或干脆配 `expected_count` 切到路径 A**

3. **触发标签不一致**：模型输出的 label 字符串和 `steps_config[i].per_item.item_label` 必须**完全一致**（含中文标点空格）。**v3.9 起 `item_label` 可以是数组**（`["5N螺丝","7N螺丝"]` 表 OR），但每个元素仍要与模型 label 精确一致。

### 故障 B：周期开始但永远不结算

**症状**：进度条卡在 X/N，后端日志没有 `[per_item] 周期结算` 行。

**v3.9 已新增 3 条结算路径（见 §二「结算路径速查」），故障 B 排查范围扩大**：

1. **4 条结算路径全部未配**（最常见配置错误）：
   - `settle_after_all_done_sec` = 0 ✗
   - `cycle_max_duration_sec` = 0 ✗
   - `idle_timeout_sec` = 0 ✗
   - `finish_label` = "" ✗
   - → 周期**永远不结算**。至少要配其中一条
   - **推荐组合**：`settle_after_all_done_sec=1.0` (OK 路径) + `idle_timeout_sec=15.0` (NG 兜底)

2. **`settle_after_all_done_sec` 配了但仍不结算**：
   - 检查是否真的所有步骤 `completed=True`：
     ```bash
     curl -s "http://localhost:8001/api/v1/source/detection/results?channel=0" | jq '.per_item_state.steps[] | {label:.step_label, completed, cov:.covered_count, total:.total}'
     ```
   - 任一步骤 `completed=False` → 这条路径不触发，需要靠其他兜底

3. **`idle_timeout_sec` 配了但仍不结算**：
   - **只看 action 标签，不看 item 标签**（v3.9 设计）—— 工件静置画面有 item 也不刷新 idle
   - 工人手离开但 action 标签是非"打螺丝"类**易误检的标签** → 模型仍持续输出 → idle 计时被刷新 → 不触发
   - 排查：`grep "action 标签" /tmp/tianjun-backend.log` 或看 `_per_item_session.last_activity_time`

4. **`finish_label` 收尾标签未触发**：
   - 配了但模型输出 label ≠ `finish_label` → 永远不触发
   - 收尾标签仅闪现 1-2 帧 < `finish_sustain_frames`（默认 3） → 不触发

5. **`completion` 配了 `all_covered` 以外的值**（v3.8 起未实现）：
   - `_PerItemStep.check_completion()` 仅认 `'all_covered'`，其他**兜底 False 且无日志**
   - 设置了 `"min_n_covered"` 之类 → 步骤永远 `completed=False` → `settle_after_all_done_sec` 路径永不触发
   - 见 §八扩展点 #1

### 故障 C：明明漏件却报 OK（或反之）

**症状**：结算后 event_id=1（OK），但实际漏件；或 event_id=2（NG）但视觉看像完成。

**排查链**：

1. **`step.completed` 判定**：
   - `_PerItemStep.check_completion()` 当前仅实现 `completion='all_covered'`
   - 锁定的 `items` 字典里**每一个**都要 `covered=true`
   - 检查 `mgr._per_item_steps[i].items` dump：

   ```python
   for iid, st in mgr._per_item_steps[0].items.items():
       print(iid, st.covered, st.consecutive_overlap_frames, st.bbox)
   ```

2. **误识别个体被锁定**：
   - 周期开始那一瞬间 `lock_count_on_start=true` 锁定了**N+1 颗虚假螺丝**
   - 工人只打了真实的 N 颗 → 虚假那颗永远 covered=false → NG
   - **设计决策**（Q7）：故意"误检不兜底"，要求改模型而不是改逻辑

3. **`sustain_frames` 设置过高**：
   - 工人快速划过 → 实际重叠帧 < `sustain_frames` → `covered` 翻不过去
   - 调小 `sustain_frames`（如从 5 降到 3）

### 故障 D：个体莫名其妙消失（items 变少）

**症状**：周期进行中 `_per_item_steps[i].items` 数量自动减少。

**排查链**：

1. **`item_timeout_seconds` 触发了未覆盖件清理**：
   - `cleanup_stale_items()` 在 dynamic 模式（`lock_count_on_start=false`）会清掉超时未出现的个体
   - 锁定模式下 **只清未覆盖件**（已覆盖永远保留 — 这是不变量 §一.4）
   - 解决：把 `lock_count_on_start=true`（默认），或把 timeout 调大

2. **画面遮挡导致 last_seen_time 没更新**：
   - 工人的手挡住某颗螺丝超过 `item_timeout_seconds` → 未覆盖件被清
   - 把 `item_timeout_seconds` 从 3s 拉到 8-10s 缓解

### 故障 E：前端 PerItemPanel 空白 / counters 全 0

**症状**：浏览器选了 per_item 项目，PerItemPanel 显示「未启用」或顶栏数据空。

**排查链**：

1. **前端项目 store 未刷新**（最高发踩坑）：
   - 后端激活了 per_item，但 Pinia `currentProject` 还是旧的
   - **修复**：Navbar 项目下拉里切走再切回来一次，强制 store 重新拉
   - 或浏览器 Ctrl+R 全刷

2. **counters_config 为空**：
   - 项目 `counters_config=[]` → 前端右上角"总产量/合格/不良"三卡显示 0
   - 修复：PUT /projects/{id} 写入：
     ```json
     "counters_config": [
       {"name":"总产量","mode":"event","event_id":3},
       {"name":"合格总数","mode":"event","event_id":1},
       {"name":"不良总数","mode":"event","event_id":2}
     ]
     ```

3. **`/detection/results` 返回 `per_item_state: null`**：
   - 后端 mgr 没启用 per_item（同故障 A.1）

### 故障 F：mock endpoint 返回 403

**症状**：调用 `~/per-item-mock.sh` 或 `/detection/per-item-mock` 返回 403。

**修复**：

```bash
# 后端必须用 ENABLE_DEV_MOCKS=1 启动:
kill <旧 uvicorn pid>
PYTHONPATH=/path/to/repo ENABLE_DEV_MOCKS=1 \
  python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload &
```

**出厂版默认 ENABLE_DEV_MOCKS 不设** → 端点永远 403（这是有意设计，防客户机被注入假数据）。

---

## 四、Mock endpoint（开发/演示用）

### 守门

`backend/api/source_routes.py:_dev_mocks_enabled()` 读 `ENABLE_DEV_MOCKS` 环境变量：
- `"1"` → 放开
- 其余（含未设）→ 端点 403

### 一键脚本

`~/per-item-mock.sh`（用户家目录）：

```bash
./per-item-mock.sh                          # 12 颗 5 覆盖 OK 流程
./per-item-mock.sh 10 6                     # 10 颗 6 覆盖
./per-item-mock.sh 10 6 23 3                # 自定义 OK/NG 计数
./per-item-mock.sh 10 6 23 3 ng             # 注入"上次 NG"红条
./per-item-mock.sh 10 6 23 3 ng 9.3         # 指定 NG 周期耗时
```

### 直接 curl

```bash
curl -X POST "http://localhost:8001/api/v1/source/detection/per-item-mock" \
  -G \
  -d channel=0 -d total_items=12 -d covered_items=7 \
  -d cycle_age_seconds=4.2 \
  -d ok_count=28 -d ng_count=5 \
  -d inject_ng=true -d ng_cycle_duration=11.4
```

### 注入的数据范围

- `mgr._per_item_steps[0]` 的 items（按 3 行 × N 列网格）
- `mgr._per_item_session.cycle_active=true`
- `mgr.counters` 三个 builtin（总产量/合格总数/不良总数）
- `mgr.cycle_times` / `mgr.ng_cycle_times`（前端历史 cycle 时间消费）
- `mgr._per_item_last_ng_detail`（仅 inject_ng=true 时）

---

## 五、`_per_item_last_ng_detail` 结构（v3.8.0+ 已升级为 dict）

```python
mgr._per_item_last_ng_detail = {
    'reason_summary': '[工序1-打螺丝] 未完成(6/10)',  # 文字摘要
    'cycle_duration_sec': 9.3,                         # 周期耗时秒
    'settled_at': 1779183633.5,                        # 结算时间戳
    'missing_total': 4,                                # 总漏件数
    'steps_failed': [
        {
            'step_label': '塞尺测缝隙',           # 配置里的 step.label
            'display_label': '工序1-打螺丝',      # UI 显示名
            'covered_count': 6,
            'total': 10,
            'missing_item_ids': [7, 8, 9, 10],
        },
        ...
    ],
}
```

**注意**：
- v3.8.0 早期是 list[step_detail]（已废弃但前端 `formatNgDetail` 仍兼容）
- OK 流程结算时**主动清成 None**（避免前端误以为还在 NG 状态）

前端 `PerItemPanel.vue:formatNgDetail` 单行输出：
> "漏 4 件 · 耗时 9.3s · 工序1-打螺丝 #7/8/9"

---

## 六、已知架构事实 / 注意点

1. **per_item 与其他模式正交**：
   - `_update_step_stats_per_item` 一旦命中直接 return，不会走 sequential/tracking 路径
   - sequential/tracking 修改不影响 per_item，反之亦然

2. **counters/cycle_times 完全复用**：
   - per_item 步骤完成时 `step_counts[step_label] += 1`，等价于 sequential 行为
   - `_trigger_event(1/2, reason)` 直接走老 hook，MES / 报警 / 实时导出全链路自然衔接

3. **粒度递进的"周期内强制结算"机制（v3.8 → v3.9 演化）**：
   - **v3.8.0 短板**：仅靠收尾标签触发整体 NG，工人放下不干就永远不结算
   - **v3.9.0 部分修复**：加 `cycle_max_duration_sec`（周期总时长上限）+ `idle_timeout_sec`（无 action 持续超时）→ 周期级 NG 兜底已可用
   - **仍未做**：**单件级**立即 NG（漏哪颗就立刻报哪颗，对应未覆盖件触发 `_trigger_event(2)`）
   - 如需此能力，要在 `_update_step_stats_per_item` 里加 per-item 倒计时 + 防重 set，见 §八扩展点 #2
   - **不要混淆**：`cleanup_stale_items` 用的 `item_timeout_seconds` 是**清理用**不是**报警用**；而且锁定模式下根本是 no-op（mixin 265-266）

4. **mock endpoint 仅注入第一个 per_item 步骤**：
   - `step = mgr._per_item_steps[0]` 写死的
   - 多步骤项目（如 "打螺丝 + 划螺丝"）只能看第一步效果
   - 真实模型运行时多步骤完整工作，仅 mock 简化

5. **PerItemPanel 与 SOP/Tracking 互斥**：
   - `Monitor/index.vue` 用 `isPerItemMode` / `isTrackingMode` 做 `v-if/v-else-if`
   - 加新模式时必须遵循同样互斥模式

6. **`_per_item_last_ng_detail` 的清空时机**：
   - OK 结算时清成 None
   - mock endpoint `inject_ng=false` 时清成 None
   - 周期开始时**不清**（让上一周期的 NG 红条保持到下次结算）

---

## 七、历史踩坑（v3.8.0 → 当前）

| 现象 | 根因 | 修复 |
|------|------|------|
| `last_ng_detail` 是 list 但前端按 dict 解析 → JSON.stringify 截断显示 | 早期返回 list[step_detail], 前端 formatNgDetail 仅兼容 dict | 后端升级 dict（含 reason_summary/missing_total/steps_failed），前端 formatNgDetail 同时兼容 string/list/dict |
| 浏览器选了 demo 项目但 PerItemPanel 仍空白 | Pinia currentProject 是前端本地状态, /set-project 不会推 | Navbar 切走再切回触发 store 重拉 |
| counters 三卡全 0 | DEMO 项目 counters_config 为空 | PUT /projects/{id} 写入三个 builtin |
| mock endpoint 403 | ENABLE_DEV_MOCKS 未设 | 重启后端带变量 |
| 前端切到 per_item 后 SOP 进度条还显示 | Monitor/index.vue 缺 v-else-if 互斥 | 加 isPerItemMode 计算属性，SOP/Tracking/PerItem 三路互斥 |

---

## 八、扩展点

如需为 per_item 加新能力，按优先级：

1. **❌ 未做 - 加 `completion` 类型**（如 `"min_n_covered"` / `"n_of_m"`）：
   - 客户已发现需求：例 "10 颗里覆盖到 8 颗即算完成"（容忍 2 颗漏件）
   - `_PerItemStep.check_completion()` 当前**仅认 `all_covered`，其他兜底 False 且无日志**（mixin 286 行）
   - 实现：
     - `check_completion()` 加 `min_n_covered` / `n_of_m` 分支
     - **顺手加未实现类型的 WARN 日志**（避免客户配错字段静默卡死，见 §三故障 B.5）
     - `steps_config[i].per_item.completion` 配置值 + 配套参数（如 `min_n` 字段）
     - 前端 `Project/index.vue` 加下拉 + 关联参数输入

2. **🟡 半做 - "周期内立即 NG"（漏件立即报警）**：
   - **v3.9 已做**：周期级 NG 兜底（`cycle_max_duration_sec` / `idle_timeout_sec`）
   - **仍未做**：**单件级**立即 NG（漏哪颗 → 触发哪颗的报警），需求来自"实时质量看板"
   - 实现：
     - `_update_step_stats_per_item` 末尾加遍历 items，对未覆盖件做 `current_time - item.first_seen_at > X` 判断
     - 触发 `_trigger_event(2, ...)` 但**不重置周期**（避免连发）
     - 标记位 `_per_item_session.warned_items: set[int]` 防重
     - 新增项目级配置 `per_item.single_item_warn_after_sec`

3. **❌ 未做 - PerItemPanel 加自定义可视化**（如 SVG 热力图 / minimap）：
   - 新建 `frontend/src/views/Monitor/components/PerItemMinimap.vue`
   - PerItemPanel 引入并按 step 渲染（参考 tracking 模式 `TrackingMinimap.vue` 已有实现）

4. **❌ 未做 - mock endpoint 多步骤支持**：
   - `source_routes.py:/detection/per-item-mock` 当前写死 `step = mgr._per_item_steps[0]`（见 §六.4）
   - 多步骤项目（"打螺丝 + 划螺丝 + 涂胶"）的演示场景受限
   - 实现：加 `?step_index=N` 或 `?step_label=xxx` 参数

---

## 九、关键文件速查

| 文件 | 行数 | 职责 |
|------|------|------|
| `backend/api/source_per_item_mixin.py` | 869 | 主 Mixin + 3 个状态类（v3.9.0 加五补丁） |
| `backend/api/source.py` | — | 引入 `PerItemMixin` 加进 VSM 继承链 |
| `backend/api/source_step_stats_mixin.py` | — | `_update_step_stats` 入口分流 |
| `backend/api/source_project_config_apply.py` | — | 末尾调 `_per_item_apply_config` |
| `backend/api/source_routes.py` | — | `/detection/results` 拼 per_item_state + `/per-item-mock` 端点 |
| `frontend/src/views/Monitor/PerItemPanel.vue` | ~260 | 视频下方全宽面板 |
| `frontend/src/views/Monitor/index.vue` | — | `isPerItemMode` + 右侧"逐件实时反馈"卡 |
| `frontend/src/views/Project/index.vue` | — | 项目级 + 步骤级配置 UI |
| `tests/test_per_item_smoke.py` | 363 | 10 个冒烟用例（OK/NG/sustain/timeout/重新打） |
| `tests/test_per_item_v39_features.py` | 539 | **v3.9 五补丁专属测试**（expected_count / lookahead / settle_after_all_done / item_label 数组 / idle_timeout / cycle_max） |
| `tests/test_synthetic_per_item.py` | 345 | 合成场景测试（多步骤组合 / 边界 case）|
| `~/per-item-mock.sh` | — | 一键 mock 脚本 |
