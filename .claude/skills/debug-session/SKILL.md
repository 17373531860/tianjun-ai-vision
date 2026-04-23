---
name: debug-session
description: "诊断 Session/Cycle/Step 数据问题：记录丢失、数据不一致、孤立记录、录像文件关联失败、CSV导出异常。当数据页面显示异常或统计不对时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent"
---

# debug-session: 会话数据诊断

你正在诊断天军AI视觉检测系统的 **Session/Cycle/Step 三级数据体系**。

用户问题: $ARGUMENTS

## 数据模型关系 (backend/models/models.py)

```
DetectionSession (一次开机运行)
  ├── id, project_id, channel
  ├── start_time, end_time, status (running/completed/interrupted)
  ├── total_cycles, good_cycles, ng_cycles
  ├── counters_snapshot (JSON, 结束时保存)
  ├── operator_id (外键 → Operator, 可为NULL；与当前操作员/设置页管理一致)
  ├── order_id (v2.3.0+, 关联MES工单, 可为NULL)
  │
  ├── DetectionCycle[] (一个产品周期)
  │   ├── id, session_id, cycle_number
  │   ├── start_time, end_time, duration_seconds
  │   ├── is_good (bool), ng_reason
  │   ├── operator_id (外键 → Operator, 可为NULL)
  │   ├── order_id (v2.3.0+, 关联MES工单, 可为NULL)
  │   ├── completed_steps, total_steps
  │   ├── result_summary (JSON)
  │   │
  │   └── StepRecord[] (单个步骤)
  │       ├── id, cycle_id, step_label, step_index
  │       ├── start_time, end_time, duration_seconds
  │       ├── confidence, is_good
  │       ├── interval_to_next (秒, 到下一步的间隔)
  │       └── interval_from_prev (旧字段, 保持兼容)
  │
  └── VideoClip[] (录像文件)
      ├── id, session_id, cycle_id (可选)
      ├── file_path, file_size
      ├── start_time, end_time, duration
      └── channel
```

## 数据写入链路

### 写入端 (backend/api/source.py - VideoSourceManager)
```
start_detection()
  → start_session()    [INSERT DetectionSession, status='running'；写入 operator_id（当前操作员）]
  
检测到第一个步骤
  → start_cycle()      [INSERT DetectionCycle；写入 operator_id]
  → record_step()      [INSERT StepRecord] (每个步骤完成时)
  
周期结束
  → end_cycle()        [UPDATE DetectionCycle: is_good, duration, ng_reason]
    → _reconcile_step_records()  [修正步骤记录的时间和顺序]
  
停止检测
  → end_session()      [UPDATE DetectionSession: end_time, counters_snapshot, status='completed']
```

### 读取端 (backend/api/sessions.py ~1803行)

**关键端点：**
| 端点 | 功能 | 行号范围 |
|------|------|----------|
| `GET /sessions` | 分页列表，支持项目/日期/通道筛选 | |
| `GET /sessions/by-date/{date}` | 按日期+班次概览 | |
| `GET /sessions/{id}/cycles` | 分页周期列表 | |
| `GET /cycles/{id}/steps` | 步骤记录 | |
| `GET /videos/{id}` | 视频播放（自动H.264转码） | |
| `GET /export/csv` | CSV导出 | |
| `GET /stats/step-averages` | 步骤平均时长/间隔统计 | |
| `GET /stats/cycle-averages` | 周期统计 | |

**辅助函数：**
- `_get_step_order_map()` — 从项目配置映射步骤标签到序号
- `_filter_valid_steps()` — 过滤掉幻影步骤（<0.1秒的步骤记录）
- `convert_video_for_browser()` — FFmpeg转H.264给浏览器播放

## 常见数据问题诊断

### 1. Session 状态异常
- **"interrupted" 太多：** `main.py:fix_orphan_sessions()` 在启动时把所有 `running` 改为 `interrupted`。如果后端频繁崩溃重启，就会积累大量 interrupted session
- **Session 不结束：** 检查 `end_session()` 是否被调用，搜索 `end_session` 的所有调用点

### 2. Cycle 数据不一致
- **completed_steps != 实际步骤数：** `_reconcile_step_records()` 可能修正失败
- **duration_seconds 为 0 或 None：** Cycle 被 `_discard_empty_cycle()` 处理前已记录
- **is_good 判断错误：** 检查 `end_cycle()` 中的判定逻辑（与检测模式相关）

### 3. Step 记录丢失
- **cycle_id 为 None：** `record_step()` 在 cycle 未启动时被调用
- **重复步骤：** 去重逻辑 `dedup_interval` 配置不当
- **interval_to_next 异常：** `_reconcile_step_records()` 计算错误

### 4. 录像问题
- **VideoClip 存在但文件不在：** 自动清理删了文件但没删DB记录
- **视频无法播放：** `convert_video_for_browser()` 转码失败，检查 FFmpeg
- **录像与Cycle不关联：** `cycle_id` 未正确写入

### 5. CSV导出异常
- **字段缺失：** 检查 `sessions.py` 中 CSV 生成逻辑（~350行函数）
- **编码问题：** CSV 默认 UTF-8 with BOM

## 诊断步骤

1. **先确定数据层级：** Session级、Cycle级还是Step级问题？
2. **读取相关代码：**
   - 写入端: `backend/api/source.py` 搜索 `start_session`, `end_session`, `start_cycle`, `end_cycle`, `record_step`
   - 读取端: `backend/api/sessions.py` 搜索对应端点
   - 模型定义: `backend/models/models.py` 确认字段类型和约束
3. **检查DB数据：** 项目用 SQLite，DB文件在 `DATA_DIR` 下
4. **追踪前端调用：** `frontend/src/api/data.js` 和 `frontend/src/views/Data/index.vue`

## MES 关联 (v2.3.0+)

Session 和 Cycle 的 `order_id` 字段由 `MESHookManager` 在 `on_session_start` / `on_cycle_start` 时写入。
如果 MES 有活跃工单，cycle 结束时会自动创建 `WorkpieceInspection` 和 `DefectRecord`（NG时）。
MES 数据使用独立 DB session，不影响检测数据链路。

**MES 相关诊断:** 如果 `order_id` 为 NULL 但有活跃工单 → 检查 `mes_hooks.py` 是否正常启动。

## 操作员溯源

- **模型：** `Operator` 表（`backend/models/models.py`）；`DetectionSession`、`DetectionCycle` 通过 `operator_id` 关联。
- **写入：** `source.py` 在 `start_session`、`start_cycle` 时写入当前操作员 ID（来自 `POST /operators/set-current` 所设状态）；若未选择操作员则可能为 NULL。
- **读取与筛选：** `sessions.py` 列表/按日期/周期接口支持 `operator_id` 过滤；响应中含 `operator_id`、`operator_name`（或项目内字段名），供 Data 页筛选与列展示。
- **实时展示：** `get_detection_results` 返回操作员信息；Monitor 选择器与 Navbar 显示需与 `operators/current` 一致。
- **外部 MES：** `mes_gateway.py` 的 `build_context_from_cycle` 在上下文中包含 `operator.*`，推送缺字段时检查周期是否带 `operator_id`、Operator 是否被软删除或停用。
- **诊断步骤：** 核对 `operators/current` → 新开 session/cycle 的 DB 记录 → Data 筛选与导出是否同一 `operator_id`；NULL 时区分「未选择操作员」与「历史数据无此字段」。

## 关键依赖
- `backend/api/source.py` — 数据写入（VideoSourceManager）
- `backend/api/sessions.py` — 数据读取和导出
- `backend/models/models.py` — ORM模型定义
- `backend/models/mes_models.py` — MES 数据模型 (v2.3.0+)
- `backend/services/mes_hooks.py` — MES Hook（写入 order_id, 创建检验记录）(v2.3.0+)
- `backend/core/config.py` — DATA_DIR, UPLOAD_DIR 路径
- `frontend/src/api/data.js` — 前端API封装（含按 `operatorId` 拉取会话列表等）
- `frontend/src/api/operators.js` — 操作员 CRUD 与当前操作员
- `backend/api/operators.py` — 操作员 API
- `backend/services/mes_gateway.py` — 周期上下文中的 `operator.*`（外部推送）
- `frontend/src/views/Data/index.vue` — 数据展示页面（含 MES 工单筛选、操作员筛选与列）

## 工件码关联 (v2.5.0+)

- `CycleResponse` 增加了 `serial_no` 字段
- `get_session_cycles` API 通过 LEFT JOIN `WorkpieceInspection` + `Workpiece` 获取工件码
- Data 页面周期表新增「工件码」列
- 如果工件码为空，说明该周期未绑定条码（可能扫码器未扫或未启用）

## 已知陷阱
- `get_ffmpeg_path()` 在 source.py 和 sessions.py 各有一份（重复代码）
- `_perform_auto_cleanup()` 清理文件时可能与正在录制的文件冲突
- `backup_database()` 路径拼接相对于 UPLOAD_DIR 的父目录，不是 DATA_DIR（脆弱）
- `_filter_valid_steps()` 过滤 <0.1秒步骤可能误删合法记录
- Cycle 的 serial_no 通过 JOIN 查询获取，如果 WorkpieceInspection 记录缺失则为 null

## 集群 BoxAggregation 与 BoxSummary 一致性 (v2.7.13)

**两张表的分工**：
- `BoxAggregation` — 每工位一条记录，每次上报都 upsert；`status` 从 `received`（刚到）→ `dispatched`（已纳入一次齐发推送）
- `BoxSummary` — 整箱一条记录，聚合级别；`status` 从 `complete`（齐全待推）→ `pushed`（已推 MES）

**v2.7.13 前的问题**：
- `_check_and_dispatch` 只查 `status='received'` 的 BoxAggregation；首次齐发后全部工位变 `dispatched`
- 工人重扫某一工位 → 这工位状态回到 `received`，其它工位还是 `dispatched`
- 再调 `_check_and_dispatch`，只能看到 1 条 received 记录 → 误判整箱缺齐 → BoxSummary 完全不更新
- 结果：**前端"最近完成"读 BoxSummary (NG 旧值)，"目标明细"读 BoxAggregation (OK 新值)**，显示互相矛盾
- `get_pending_boxes` 同样只看 received → 齐全的箱被算作"还缺几个工位"，"待汇总"假未齐

**v2.7.13 方案 B 修复**：
- `_check_and_dispatch`：用 `all_records`（含 dispatched）判齐全；但需至少 1 条 `received` 才触发逻辑，防止定时器空转
- 二次齐发按 `_key_fields = (overall_result, sorted(ng_items))` 比对：
  - 变化 → BoxSummary.status 回退 `complete` 并重推 MES（`is_recovery=True`）
  - 无变化 → 只刷 summary 字段不骚扰 MES（`needs_repush=False`）
- `get_pending_boxes` 的 missing/received 全部换 `all_records`；齐全的箱直接从"待汇总"列表剔除

**排查思路**：
- 看到"待汇总 vs 最近完成"同条码结果不一致？先看 `BoxAggregation` 表里这条 box_serial 的所有记录 status：
  - 全 `dispatched`、没有 `received` → 本次上报根本没进去，看 `cluster_collector.upsert_aggregation` 日志
  - 有 `received` → 本次理论上会触发，看 `_check_and_dispatch` 返回的 `reason`
    - `reason=no_new_data` → 所有新数据都已经 dispatched（不应该到这里）
    - `reason=missing` → 真的缺工位
    - `reason=no_change_after_recovery` → 已 pushed 且结果无变化（正常跳过）
    - 正常分发 → 看 `is_recovery` 字段确认是首发还是二次校正
- MES 客户端必须对 `box_serial` 幂等，因为二次校正时会再推一次（这是特性，不是 bug）

**相关代码**：
- `backend/services/cluster_collector.py::_check_and_dispatch` (~L475-700)
- `backend/services/cluster_collector.py::get_pending_boxes` (处理"待汇总" API)
- `tools/test_cluster_recovery.py` — 三轮场景端到端验证脚本
