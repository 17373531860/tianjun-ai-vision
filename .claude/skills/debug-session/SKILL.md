---
name: debug-session
description: "诊断 Session/Cycle/Step 数据问题：记录丢失、数据不一致、孤儿记录、录像断链、CSV 导出异常、幽灵 cycle。当 Data 页统计不对、导出报错或周期记录可疑时使用。"
argument-hint: "[问题描述]"
model: opus
effort: high
allowed-tools: "Read, Grep, Glob, Bash, Agent, mcp__filesystem, mcp__sequential-thinking, mcp__sentry"
---

# debug-session: Session/Cycle/Step 数据诊断（v3.5.x）

诊断天军 AI 视觉检测系统的三层数据体系：**DetectionSession → DetectionCycle → StepRecord**，外加 **VideoClip** 录像关联。AGENTS.md 第十一节是事实源。

用户问题: $ARGUMENTS

---

## 一、三层数据模型（`backend/models/models.py`）

> 路由前缀统一 `/api/v1/data/*`（`backend/main.py: app.include_router(sessions_router, prefix=f"{API_V1_STR}/data")`）。

```
DetectionSession  一次开机会话（设备启动到关闭）
  ├── id, session_uuid (8-char, unique=True, index=True)
  ├── project_id (FK projects, ondelete=CASCADE)
  ├── start_time, end_time
  ├── status: running / completed / interrupted
  ├── total_cycles / good_cycles / ng_cycles
  ├── avg_cycle_time / min_cycle_time / max_cycle_time
  ├── counters_snapshot (JSON, end_session 时落盘)
  ├── video_path / video_id (整段会话视频，可空)
  ├── channel_id (0-based 工位)
  ├── shift_label (day/night, 可空)
  ├── operator_id (FK operators, SET NULL)
  └── order_id (MES 工单关联，可空)
        │
        └── DetectionCycle  一个工件周期
              ├── id, cycle_uuid (8-char, unique=True)
              ├── session_id (FK ondelete=CASCADE)
              ├── cycle_number (会话内序号)
              ├── start_time, end_time, duration
              ├── interval_to_next (到下一周期间隔)
              ├── is_good (bool；OK=True / NG=False)
              ├── event_id, event_name, result_reason
              ├── step_sequence (JSON, ['步骤1','步骤2',...])
              ├── video_path / video_id
              ├── operator_id, order_id
              │
              └── StepRecord  周期内每个步骤
                    ├── id, record_uuid (8-char, unique=True)
                    ├── cycle_id (FK ondelete=CASCADE)
                    ├── step_id (项目配置侧 ID, 可空)
                    ├── step_label (检测类别名)
                    ├── step_name (显示名)
                    ├── step_order (周期内顺序)
                    ├── start_time, end_time, duration
                    ├── interval_from_prev (旧字段，兼容保留)
                    ├── interval_to_next (新字段，主用)
                    ├── confidence
                    ├── is_valid (满足时间要求)
                    ├── screenshot_path / video_path / video_id

VideoClip  录像文件（独立表，clip_type 标识层级）
  ├── id, video_uuid (8-char, unique=True, URL 用)
  ├── clip_type: session / cycle / step
  ├── related_id (关联记录 id，无外键约束)
  ├── file_path, file_name, file_size, duration
  ├── start_time, end_time, created_at
  └── extra_info (JSON)
```

**重要**：`VideoClip` 与 Session/Cycle 之间**没有强外键**，只靠 `clip_type + related_id` 软关联，加上 `video_id` 字段反向链回 Session/Cycle。所以**录像断链不会触发外键级联**，要靠维护逻辑兜底。

---

## 二、写入链路（`backend/api/source_session_lifecycle_mixin.py`，1111 行）

```
detection 启动 / 项目加载
  → start_session(project_id)        INSERT detection_sessions (status='running')
                                     带入 channel_id / shift_label / operator_id
                                     调 mes_hook.on_session_start

第一帧检出步骤（容器/顺序/单步均可）
  → start_cycle()                    INSERT detection_cycles
                                     更新上一周期 interval_to_next
                                     带入 cycle_number / operator_id / order_id
                                     调 mes_hook.on_cycle_start

每检出一个步骤
  → record_step()                    INSERT step_records
                                     更新上一步骤 interval_to_next

周期结算
  → end_cycle(is_good, event, ...)   UPDATE 当前 cycle: end_time / duration / is_good /
                                                   event_name / result_reason / step_sequence
                                     调 mes_hook.on_cycle_end
                                     调 _check_periodic_actions (v3.5.0)
                                     调 scanner.notify_cycle_settled / resume_after_cycle
  → _reconcile_step_records()        keep-and-fix：保留命中的、删多余的、补缺失的
                                     使 step_records 与 cycle.step_sequence 严格对齐

session 关闭 / 检测停止 / 班次切换 / 跨日
  → end_session()                    丢弃未结算 cycle (_discard_empty_cycle)
                                     重新统计 total/good/ng_cycles + avg/min/max
                                     删除 end_time IS NULL 的孤儿 cycle
                                     落盘 counters_snapshot
                                     调 mes_hook.on_session_end
```

**自动拆分**：`_auto_split_session()` 触发条件 — 跨日 / 班次切换。先 `end_session()` 再 `start_session()`。

**8-char UUID**：所有 `*_uuid` 字段都是 `str(uuid.uuid4())[:8]`，列上有 `unique=True` 约束，**理论存在碰撞**（16^8 ≈ 4.3 亿空间），实战碰到要看 SQLAlchemy 抛 `IntegrityError`。诊断"插入失败"问题时，先怀疑 8 字符碰撞 + 重启迁移残留组合。

---

## 三、读取端 / 子模块拆分

`backend/api/sessions.py` 是主入口（1005 行），子路由通过 `router.include_router` 挂载（**前缀统一 `/api/v1/data`**）：

| 子文件 | 行数 | 职责 | 关键端点 |
|---|---:|---|---|
| `sessions.py` | ~1005 | 主入口 + Pydantic schema + 视频转码 | `/sessions*`, `/cycles*`, `/videos*`, `/export-settings`, `/export/csv` |
| `sessions_export.py` | 528 | CSV 导出三分支 | `_write_session_export` / `_write_cycle_export` / `_write_range_export` |
| `sessions_stats.py` | ~250 | 聚合统计 (v3.5.0 加 confidence) | `/stats/step-averages`, `/stats/cycle-averages` |
| `sessions_maintenance.py` | 404 | 备份 / 清理 / 容量 | `/backup/database`, `/clear/all`, `/clear/range`, `/cleanup-settings`, `/storage-info`, `/cleanup/run` |

**辅助函数**：
- `_get_step_order_map(db, project_id|session_id|cycle_id)` — `{step_label: idx}`，从 Project.steps_config 读
- `_filter_valid_steps(steps)` — 过滤 `duration < 0.1s` 的幻影步骤；**不在 `/cycles/{id}/steps` 里用**（与 `_reconcile_step_records` 已对齐过冲突）
- `convert_video_for_browser(input_path)` — FFmpeg 转 H.264 + faststart，结果落 `RECORDING_DIR/cache/`。**v3.48.1 起原子化**：先写 `.tmp.mp4` 成功后 `os.replace` 落位，缓存名 `_h264v2`（旧 `_h264` 一律不认，历史坏缓存自然失效），超时 60s→300s。排查「视频加载失败」：① 看 `cache/` 里有没有对应 `_h264v2.mp4`（size>0 才可信）；② 没有则看后端日志"视频转换失败/异常"；③ 转码失败会回退原始 mp4v 文件——Chromium 解不了但可下载（前端 v3.48.1 有「下载录像」按钮兜底）。周期列表 `GET /data/sessions/{id}/cycles` v3.48.1 新增 `result=ok|ng` 筛选参数（按 `DetectionCycle.is_good`）。

**改 sessions 相关功能时 grep 范围：`backend/api/sessions*.py`**（4 个文件全扫）。

---

## 四、孤儿数据清理（`backend/main.py`）

启动 `_run_startup_init()` 顺序：
```
_diag_db_health()
migrate_database()           # 60+ 条 ALTER TABLE 兜底老 schema
fix_orphan_sessions()        # status='running' 的全部改 'interrupted'
cleanup_orphan_inspections() # 把指向不存在 cycle/session 的 inspection.cycle_id/session_id 置 NULL
_seed_export_builtin_templates()
```

**1. `fix_orphan_sessions()`（`main.py:135`）**
- 后端启动时把 `status='running'` 的 session 全部判定为"上次崩溃 / 强杀残留"
- 重新统计 `total_cycles / good_cycles / ng_cycles / avg_cycle_time / min/max`
- `end_time` 取最后 cycle.end_time，没有就用 cycle.start_time，再没有用 session.start_time
- `status` 改成 `interrupted`
- **副作用**：频繁崩溃 → interrupted session 堆积；前端 Data 页看到状态多是 interrupted 不是 bug，是修复语义

**2. `cleanup_orphan_inspections()`（`main.py:189`）**
- WorkpieceInspection / DefectRecord 历史上**没有强外键**回 DetectionCycle/DetectionSession
- cycle 被删但 inspection.cycle_id 不会被级联清，越积越多
- 启动时 SQL 找出指向不存在 cycle/session 的引用，**置 NULL**（保留 inspection 痕迹但断错链）
- 打印 `[孤儿清理] 发现 inspection→cycle:N inspection→session:N defect→cycle:N`

**3. `_perform_auto_cleanup()`（`sessions_maintenance.py:43`）**
- 每 24h 跑一次（`main.py:_schedule_auto_cleanup` 后台 Timer）
- 读 `SystemConfig.retention_days` + `auto_cleanup`（默认 30 天 / true）
- 删 4 类东西：
  1. 过期 DetectionSession（连带 cycles / step_records / video_clips + 文件）
  2. 录制目录孤儿文件（不在 video_clips.file_path 集合里且 mtime 过期）
  3. `RECORDING_DIR/cache/` 转码缓存
  4. `VIDEO_UPLOAD_DIR` 过期上传视频（项目里"上传视频文件"作为视频源用的那批）
- **陷阱**：`_perform_auto_cleanup` 删录像文件时如果该文件正在被 FFmpeg 写，可能竞争（`debug-video` 也提到）

---

## 五、常见问题诊断

### 1. 数据丢失（cycle 没出现在 Data 页）

| 症状 | 排查路径 |
|---|---|
| `start_cycle` 未触发 | 看 source 日志 `新周期开始: #N (xxxx)`；扫码绑定模式下 `[扫码绑定] 工位N 要求先扫码，当前无待检工件，跳过开周期` 是常见原因（`debug-mes`） |
| `end_cycle` 未触发 → cycle.end_time = NULL | end_session 会兜底删孤儿；如果还在跑就要查结算逻辑（`debug-source` 容器/顺序/settle 模式） |
| commit 失败 | 看 `创建周期失败` / `结束周期失败` / `记录步骤失败` 日志；常见是 SQLite `database is locked`（`db.busy_timeout=15s` 也救不回） |
| 整段 session 不见了 | 已被 `_perform_auto_cleanup` 按保留天数清掉；`SystemConfig.retention_days` 设小了？ |

**验证 SQL**：
```sql
SELECT id, session_uuid, status, total_cycles, start_time, end_time
FROM detection_sessions ORDER BY id DESC LIMIT 20;

SELECT id, cycle_number, is_good, end_time, duration
FROM detection_cycles WHERE session_id = ? ORDER BY cycle_number;
```

### 2. 数据不一致

**症状**：Session.total_cycles ≠ 实际 DetectionCycle 行数。
- 老 session 的统计字段是 `end_session()` 时算的快照；之后改 schema / 手动删 cycle 不会反向更新
- 解决：用 `sessions_stats.py` 的 `/stats/cycle-averages` 实时聚合，不要直接信 `Session.total_cycles`

**症状**：Cycle.step_sequence (JSON 列表) 与 step_records 行数不匹配。
- `_reconcile_step_records()` 应该已对齐；不齐就看日志 `[reconcile] cycle X: kept N, deleted M, created K`
- v3.5.x 后 `/cycles/{id}/steps` **不再** apply `_filter_valid_steps`（注释里写的 `已 reconcile 过严格对齐，过滤会断掉对齐`）

**症状**：合格/不合格汇总 ≠ session 字段。
- `/sessions/by-date/{date}` 在**有班次过滤**时按 cycle.start_time 重算，会得到与 session 字段不同的数；这是预期行为
- 班次过滤跨日（start_hour > end_hour）时把第二天 00:00–end_hour 也算进来

### 3. 幽灵 cycle / 短 cycle（详见 `debug-source`）

**幽灵 cycle**：cycle 里只有一两条非常短的 step，`is_good=False`，`event_name="未识别"` 之类。

可能成因（**都在 debug-source 范围**，本 skill 只描述如何从数据侧识别）：
- 容器模式 ByteTrack 重 ID + `_box_objects` 老条目残留 → `_check_and_dispatch` 误判 NG（v3.1.4 加 `container_settle_min_items` 过滤）
- 多工位广播结算 `force_settle_pending_cycle` 把空箱也 settle 了（v3.1.2 之后有 `min_items` 防御）
- 扫码 LON D 模式跨线追踪卡住，下次开 cycle 立刻 settle（v3.4.x）

**数据侧识别 SQL**：
```sql
-- 找 duration < 1s 且 step_records 只有 1 行的 cycle
SELECT c.id, c.cycle_number, c.duration, c.is_good, c.event_name,
       (SELECT COUNT(*) FROM step_records s WHERE s.cycle_id = c.id) AS step_cnt
FROM detection_cycles c
WHERE c.duration IS NOT NULL AND c.duration < 1.0
ORDER BY c.id DESC LIMIT 50;
```
高频出现就跳到 `debug-source` 排查 settle 路径，**不要在 sessions 这层"删坏数据"了事**。

### 4. 录像文件断链 (VideoClip)

**症状**：Data 页点周期录像 → "视频不存在"。

`/api/v1/data/videos/{video_uuid}` 走两道 404：
- DB 没找到 `VideoClip.video_uuid` → "视频不存在"
- DB 有但 `os.path.exists(file_path) == False` → "视频文件不存在"

**断链来源**：
1. 自动清理只删了文件、漏删 DB 记录（不该出现，`_perform_auto_cleanup` 是"先删文件再 delete row"，但顺序里如果 commit 异常就可能出现）
2. 客户手动删 `recordings/` 下的文件
3. `extra_info` JSON 里的 `clip_type='session/cycle/step'` 与 `related_id` 对不上 cycle/session（无外键）
4. `convert_video_for_browser` 转码失败 → 回退原文件路径（不是断链，但播放可能不兼容）

**修复**（轻量）：
```sql
-- 查所有 file_path 不存在的 VideoClip
SELECT id, video_uuid, clip_type, related_id, file_path FROM video_clips;
-- 然后手动 DELETE 不存在的（先备份）
```
重大批量修复用 `fix-data` skill。

### 5. CSV 导出异常

`/api/v1/data/export/csv?export_type=session|cycle|all`，分派给 `sessions_export.py:build_csv_response`。

**典型错误**：

| 错误 | 原因 |
|---|---|
| `导出失败: 'NoneType' object has no attribute 'name'` | `Project` 被删但 session/cycle 还在；range 导出会查项目名 |
| `导出失败: 周期不存在` / `会话不存在` | 客户端拿了过期的 cycle_id/session_id，已被自动清理 |
| 中文乱码 | CSV 已加 `\ufeff` BOM；客户端用 GBK 打开就乱；建议用 Excel 导入向导选 UTF-8 |
| 列数对不上 | `pt_mode=avg` 时 step 块加 `耗时(平均/秒)` 列；`ct_mode=avg` 时 cycle 块加；前端没传 mode 就不加 |
| `IntegrityError` | UUID 碰撞（理论可能；实际看到先备份 DB 再分析） |

**v3.5.x 显示口径联动**：四个快捷按钮（按日 / 按周 / 按月 / 日期范围）都支持 `pt_mode` / `ct_mode`，`avg` 模式额外输出"耗时(平均/秒)"列。`_calc_aggregates(cycles, steps)` 是入口工具函数。

**`pt_mode` / `ct_mode` 来源**：前端 `SystemConfig` 的 `display.monitor.ptMode` / `display.monitor.ctMode`（`useSystemStore`），值域 `avg` / `last` / `current`。

### 6. 工件码（serial_no）关联

- `CycleResponse.serial_no` 来自 `WorkpieceInspection JOIN Workpiece` 的 LEFT 关联
- `/sessions/{id}/cycles` 一次性批查 `serial_map`，避免 N+1
- `/cycles/by-serial/{serial_no}` (v3.4.3) 跨 session/日期 全局检索（默认模糊匹配）；返回额外的 `session_id / session_uuid / project_name` 让前端知道来源
- **空值排查**：serial_no 为空 = 该 cycle 没有 inspection 记录 = 扫码器没扫上或未启用绑定（`debug-mes`）

### 7. 操作员（operator_id）溯源

- 写：`source_session_lifecycle_mixin.py` 在 `start_session` / `start_cycle` 调 `get_current_operator_id(channel_id)`，无人选则 NULL
- 读：sessions / cycles 列表接口都支持 `operator_id` 过滤；返回里附 `operator_name`
- MES Gateway `build_context_from_cycle` 上下文也取 `operator.*`，外部推送缺字段就先看周期是否有 operator_id
- 区分两类 NULL：「历史数据没此字段」（v2.x 早期）vs「当时没选操作员」

---

## 六、统计聚合（合格/不良/总数 / 平均时长）

**端点**：`/api/v1/data/stats/cycle-averages` / `/stats/step-averages`（`sessions_stats.py`）。

**口径汇总**：

```sql
-- 范围内 OK / NG / 总数
SELECT COUNT(*)                                   AS total,
       SUM(CASE WHEN is_good THEN 1 ELSE 0 END)   AS good,
       SUM(CASE WHEN NOT is_good THEN 1 ELSE 0 END) AS ng
FROM detection_cycles c
JOIN detection_sessions s ON s.id = c.session_id
WHERE date(s.start_time) BETWEEN ? AND ?
  AND s.project_id = ?
  AND s.channel_id = ?
  AND c.end_time IS NOT NULL;             -- 关键：过滤未结算 cycle
```

**v3.5.0 step 置信度聚合**（`sessions_stats.py`）：每个 step_label 求 `avg/min/max(confidence)`；自定义导出模板 (`step.avg_confidence` 等字段) 走这套。

**「待定」语义**：本系统**没有"待定"状态**——cycle 要么 `is_good=True/False`，要么 `end_time IS NULL`（孤儿，会被 end_session/启动清理删掉）。前端 Data 页如果看到"待定" tag，多半是某些插件加的扩展状态，先确认数据源。

---

## 七、诊断步骤（按顺序）

1. **先定位层级**：Session / Cycle / Step 哪一层出问题？
2. **看代码**：
   - 写：`source_session_lifecycle_mixin.py` 搜 `start_session` / `end_session` / `start_cycle` / `end_cycle` / `record_step` / `_reconcile_step_records` / `_discard_empty_cycle`
   - 读：`backend/api/sessions*.py` (4 个文件) 找对应端点
   - 模型：`backend/models/models.py:142-283`（DetectionSession / DetectionCycle / StepRecord / VideoClip）
3. **看日志**：后端 stdout 关键字 `检测会话已创建` / `新周期开始` / `周期结束` / `[reconcile]` / `[孤儿清理]` / `[自动清理]`
4. **查 DB**：`sql_app.db` 在 `TIANJUN_DATA_DIR`（默认 `BASE_DIR`）下；前端 Data 页有 `/data/backup/database` 一键下载
5. **前端追踪**：`frontend/src/api/data.js` → `frontend/src/views/Data/index.vue` (~1715 行)；SerialSearch 子组件单独负责工件码搜索

---

## 八、关键依赖速查

```
backend/
├── main.py                                        # 启动迁移 + 孤儿清理 + 自动清理 timer
├── api/
│   ├── source.py / source_session_lifecycle_mixin.py  # 写入端
│   ├── sessions.py                                # 读取主入口（含 export/csv 分派）
│   ├── sessions_export.py                         # CSV 三分支 (session/cycle/range)
│   ├── sessions_stats.py                          # 聚合统计
│   └── sessions_maintenance.py                    # 备份/清理/容量
├── models/
│   ├── models.py                                  # DetectionSession / DetectionCycle / StepRecord / VideoClip
│   └── mes_models.py                              # WorkpieceInspection (JOIN 出 serial_no)
└── services/mes_hooks.py                          # session_start/cycle_start/cycle_end 写 order_id

frontend/src/
├── api/data.js                                    # 前端封装（含 operatorId 拉取）
├── api/operators.js                               # 操作员 CRUD / 当前操作员
└── views/Data/index.vue                           # 数据页（~1715 行）+ 工件码列 + 自定义导出对话框
```

---

## 九、已知陷阱

- **8-char UUID**：`session_uuid / cycle_uuid / record_uuid / video_uuid` 全部是 `str(uuid.uuid4())[:8]`，列上 `unique=True`。压力大时不能完全排除碰撞，遇到 `IntegrityError` 时**先排查这个**而不是数据损坏。
- **VideoClip 无外键**：靠 `clip_type + related_id + video_id` 三件套软关联，cycle/session 删了不会级联清 VideoClip 行；要靠 `_perform_auto_cleanup` 的孤儿文件扫描兜底。
- **`_filter_valid_steps`**：仅在历史聚合（`sessions.py` 内部某些列表查询）使用；`/cycles/{id}/steps` 端点**不**调用，因为 `_reconcile_step_records` 已严格对齐 step_sequence。
- **`backup_database()` 路径**：`os.path.abspath(UPLOAD_DIR + "/../sql_app.db")` 拼接相对父目录，部署目录变化时容易拼错；如果客户改 `TIANJUN_DATA_DIR` 要确认 UPLOAD_DIR 同步迁移。
- **班次过滤跨夜**：`start_hour > end_hour` 走第二天 + 当天联合查询，sessions 列表会展示两天的日期；不是 bug 是设计。
- **操作员 NULL 含义有两种**：历史数据无此字段 vs 当时未选操作员；筛选时 `operator_id IS NULL` 把两类都吞了，看不出区别。
- **v3.38 起 cycle/step 落库是异步的**（`source_persist_worker.py` 每通道 FIFO 线程）：数据页记录比事件晚到亚秒级属正常；怀疑丢记录设 `TIANJUN_SYNC_PERSIST=1` 复跑对照，并 grep 后端日志 persist 关键字（落库线程单任务失败只记日志不倒线程）。清理走分批小事务 + 文件删除在 commit 后（`sessions_maintenance.py`）。
- **v3.38 起班次标签支持自定义**：不再只有 day/night，`sessions.py` 按项目配置班次列表过滤；班次解析入口 `resolve_shift_label`（跨零点支持），回归 `tests/test_shift_resolution.py`。

---

## 十、关联 skill

- `debug-source` — 状态机问题（cycle 没结束 / 幽灵 cycle / 计数不对 / settle 死锁）
- `debug-video` — 录像录不上 / 损坏 / FFmpeg 卡住
- `debug-mes` — `order_id` / `serial_no` / `WorkpieceInspection` 不写
- `fix-data` — 大批量孤儿清理 / DB 备份恢复 / 录像文件重新关联
- `modify-model` — 改 ORM schema 或加字段（必加 `migrate_database()` ALTER TABLE 兜底）
- `modify-api` — 改 sessions API 端点 / Schema
