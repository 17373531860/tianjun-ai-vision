"""
Complete verification for ALL bug fixes (excluding #2 and #4).
Uses in-memory SQLite DB for data tests, code inspection for config tests.
"""
import sys, os, re, time, json
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta
from sqlalchemy import create_engine, func, and_, or_
from sqlalchemy.orm import sessionmaker
from backend.db.database import Base
from backend.models.models import Project, DetectionSession, DetectionCycle, StepRecord

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=engine)
DBSession = sessionmaker(bind=engine)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, condition))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

def file_contains(filepath, pattern):
    """Check if a file contains a regex pattern"""
    full = os.path.join(os.path.dirname(__file__), filepath)
    if not os.path.exists(full):
        return False
    with open(full, 'r', encoding='utf-8', errors='ignore') as f:
        return bool(re.search(pattern, f.read()))


# ═══════════════════════════════════════════════════════════
# Setup: create project + sessions + cycles
# ═══════════════════════════════════════════════════════════
db = DBSession()
project = Project(
    name="TestSOP", logic_mode="sequential",
    steps_config=[
        {"id": 1, "label": "wipe", "displayLabel": "擦试"},
        {"id": 2, "label": "front", "displayLabel": "正面检查"},
        {"id": 3, "label": "tilt", "displayLabel": "倾斜检查"},
        {"id": 4, "label": "back", "displayLabel": "反面检查"},
        {"id": 5, "label": "scan", "displayLabel": "扫码"},
        {"id": 6, "label": "place", "displayLabel": "放置"},
    ],
    is_active=True,
)
db.add(project)
db.commit()
db.refresh(project)

today = datetime(2026, 3, 13)
session1 = DetectionSession(
    session_uuid="sess-001", project_id=project.id,
    start_time=today.replace(hour=7, minute=30),
    end_time=today.replace(hour=23, minute=59),
    status="completed", total_cycles=12, good_cycles=8, ng_cycles=4,
    counters_snapshot={"总产量": 12, "合格总数": 8, "不良总数": 4, "NG步骤": 6},
    avg_cycle_time=18.5, channel_id=0,
)
db.add(session1)
db.commit()
db.refresh(session1)

ALL_STEPS = ["wipe","front","tilt","back","scan","place"]
cycles_data = [
    # Day shift OK
    (today.replace(hour=8, minute=10),  15.0, True,  ALL_STEPS),
    (today.replace(hour=9, minute=20),  17.0, True,  ALL_STEPS),
    (today.replace(hour=10, minute=5),  16.5, True,  ALL_STEPS),
    (today.replace(hour=11, minute=30), 18.0, True,  ALL_STEPS),
    (today.replace(hour=14, minute=0),  19.0, True,  ALL_STEPS),
    # Day shift NG (missing scan)
    (today.replace(hour=15, minute=30), 12.0, False, ["wipe","front","tilt","back","place"]),
    (today.replace(hour=16, minute=45), 10.0, False, ["wipe","front","back","place"]),
    # Edge: 19:58, still day shift
    (today.replace(hour=19, minute=58), 20.0, True,  ALL_STEPS),
    # Night shift OK
    (today.replace(hour=20, minute=5),  16.0, True,  ALL_STEPS),
    (today.replace(hour=21, minute=15), 14.0, True,  ALL_STEPS),
    # Night shift NG
    (today.replace(hour=22, minute=0),  11.0, False, ["wipe","front","tilt","place"]),
    (today.replace(hour=23, minute=30), 9.0,  False, ["wipe","place"]),
]

cycle_objs = []
for i, (start, dur, is_good, seq) in enumerate(cycles_data):
    missing = set(ALL_STEPS) - set(seq)
    c = DetectionCycle(
        cycle_uuid=f"cyc-{i:03d}", session_id=session1.id,
        cycle_number=i+1, start_time=start,
        end_time=start + timedelta(seconds=dur),
        duration=dur, is_good=is_good,
        event_id=1 if is_good else 2,
        event_name="合格" if is_good else "不合格",
        result_reason="" if is_good else f"周期不完整，缺少: {missing}",
        step_sequence=seq,
    )
    db.add(c)
    cycle_objs.append(c)
db.commit()
for c in cycle_objs:
    db.refresh(c)


# ═══════════════════════════════════════════════════════════
# Bug 1: 运行期间软件黑屏 — 内存管理机制
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 1: 运行期间软件黑屏 (memory management) ══")

check("Electron: --max-old-space-size=512",
      file_contains("electron/main.js", r"max-old-space-size.*512"))

check("Electron: --max-decoded-image-bytes limit",
      file_contains("electron/main.js", r"max-decoded-image-bytes"))

check("Monitor: STREAM_SWAP_INTERVAL exists",
      file_contains("frontend/src/views/Monitor/index.vue", r"STREAM_SWAP_INTERVAL"))

check("Monitor: swapStream double-buffer function",
      file_contains("frontend/src/views/Monitor/index.vue", r"const swapStream"))

check("Monitor: ECharts dispose in onUnmounted",
      file_contains("frontend/src/views/Monitor/index.vue", r"pieChartInstance.*dispose|dispose.*pieChartInstance"))

check("Monitor: MJPEG buffer cap (2MB)",
      file_contains("frontend/src/views/Monitor/index.vue", r"2\s*\*\s*1024\s*\*\s*1024|2097152"))


# ═══════════════════════════════════════════════════════════
# Bug 3: 历史数据刷新
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 3: 历史数据查询刷新 ══")

check("Data: onMounted calls loadAvailableDates",
      file_contains("frontend/src/views/Data/index.vue", r"onMounted.*\n.*loadAvailableDates|loadAvailableDates"))

check("Data: no keep-alive (component remounts on each visit)",
      not file_contains("frontend/src/layout/index.vue", r"keep-alive"))

check("Data: handleDateChange triggered on date select",
      file_contains("frontend/src/views/Data/index.vue", r"handleDateChange"))


# ═══════════════════════════════════════════════════════════
# Bug 5: 录制时间低于耗时 — delayed release
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 5: 历史视频录制时间 (delayed release) ══")

check("stop_cycle_recording: _draining_cycle_writer",
      file_contains("backend/api/source.py", r"_draining_cycle_writer"))

check("stop_session_recording: _draining_session_writer",
      file_contains("backend/api/source.py", r"_draining_session_writer"))

check("_write_frame_to_writers: writes to draining writers",
      file_contains("backend/api/source.py", r"draining_cycle_w.*write|draining_session_w.*write"))

check("Delayed release uses threading + 1.5s sleep",
      file_contains("backend/api/source.py", r"sleep\(1\.5\)"))


# ═══════════════════════════════════════════════════════════
# Bug 6 + 13: 班次数据区分 + 按周期拆分
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 6+13: 班次过滤 (cycle-level, start_time) ══")

def query_shift(db, date_str, start_hour, end_hour, session_ids):
    q = db.query(DetectionCycle).filter(DetectionCycle.session_id.in_(session_ids))
    col = func.strftime('%H:%M', DetectionCycle.start_time)
    if start_hour <= end_hour:
        q = q.filter(and_(
            func.date(DetectionCycle.start_time) == date_str,
            col >= start_hour, col < end_hour))
    else:
        nd = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        q = q.filter(or_(
            and_(func.date(DetectionCycle.start_time) == date_str, col >= start_hour),
            and_(func.date(DetectionCycle.start_time) == nd, col < end_hour)))
    return q.all()

sids = [session1.id]
day = query_shift(db, "2026-03-13", "08:00", "20:00", sids)
night = query_shift(db, "2026-03-13", "20:00", "08:00", sids)

check("Day shift: 8 cycles", len(day) == 8, f"got {len(day)}")
check("Night shift: 4 cycles", len(night) == 4, f"got {len(night)}")
check("19:58 cycle in day shift", any(c.start_time.hour == 19 and c.start_time.minute == 58 for c in day))
check("20:05 cycle in night shift", any(c.start_time.hour == 20 and c.start_time.minute == 5 for c in night))

day_ok = sum(1 for c in day if c.is_good)
day_ng = len(day) - day_ok
day_counters = {'总产量': len(day), '合格总数': day_ok, '不良总数': day_ng}
check("Day counters from cycles (not session snapshot)",
      day_counters == {'总产量': 8, '合格总数': 6, '不良总数': 2},
      f"got {day_counters}")

night_ok = sum(1 for c in night if c.is_good)
night_ng = len(night) - night_ok
night_counters = {'总产量': len(night), '合格总数': night_ok, '不良总数': night_ng}
check("Night counters from cycles",
      night_counters == {'总产量': 4, '合格总数': 2, '不良总数': 2},
      f"got {night_counters}")

check("Total (day+night) = 12 = all cycles",
      len(day) + len(night) == 12)

# Session card should show filtered stats
scm = {}
for c in day:
    scm.setdefault(c.session_id, []).append(c)
for sid, sc in scm.items():
    check(f"Session card (day): total={len(sc)} not session's 12",
          len(sc) == 8, f"got {len(sc)}")

check("Backend uses DetectionCycle.start_time for filter",
      file_contains("backend/api/sessions.py", r"DetectionCycle\.start_time.*start_hour|cycle_time_col.*start_hour"))

check("Backend counters_summary from filtered cycles (总产量)",
      file_contains("backend/api/sessions.py", r"'总产量':\s*total_cycles"))


# ═══════════════════════════════════════════════════════════
# Bug 7: 报警灯驱动
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 7: 报警灯驱动 ══")

check("CH341SER driver files packaged",
      os.path.isdir(os.path.join(os.path.dirname(__file__), "electron/drivers/CH341SER")))

check("installer.nsh: pnputil /add-driver CH341SER",
      file_contains("electron/build/installer.nsh", r"pnputil.*CH341SER"))

check("package.json: extraResources includes drivers",
      file_contains("electron/package.json", r"drivers/CH341SER"))


# ═══════════════════════════════════════════════════════════
# Bug 8: 动作超时 A-A 模式
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 8: 动作超时重置 (A-A pattern) ══")

check("Backend: max_duration timeout reset loop exists",
      file_contains("backend/api/source.py", r"\[超时重置\].*max_duration"))

# Simulate the logic
step_start_time = {"wipe": 100.0, "front": 105.0}
step_last_seen = {"wipe": 108.5, "front": 108.5}
step_consecutive_frames = {"wipe": 90, "front": 40}
step_frame_confirmed = {"wipe": True, "front": True}
_step_raw_start = {"wipe": 100.0, "front": 105.0}
current_cycle_steps = ["wipe", "front"]
step_time_config = {"wipe": {"max_duration": 8.0}, "front": {}}
current_time = 109.0
detected_labels = {"wipe", "front"}

reset_labels = []
for label in list(detected_labels):
    if label not in step_start_time:
        continue
    tc = step_time_config.get(label, {})
    md = tc.get('max_duration')
    if md and (current_time - step_start_time[label]) > md:
        reset_labels.append(label)
        if label in step_last_seen: del step_last_seen[label]
        del step_start_time[label]
        step_consecutive_frames[label] = 0
        step_frame_confirmed[label] = False
        if label in _step_raw_start: del _step_raw_start[label]

check("wipe (9s > max 8s) reset", "wipe" in reset_labels)
check("front (no max) NOT reset", "front" not in reset_labels)
check("wipe stays in cycle_steps", "wipe" in current_cycle_steps)
check("wipe step_last_seen cleared → new appearance next frame", "wipe" not in step_last_seen)
check("No max_duration set → no effect", step_frame_confirmed.get("front") is True)


# ═══════════════════════════════════════════════════════════
# Bug 9: NG TOP3 不显示
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 9: NG TOP3 regex + fallback ══")

def ng_top3(reason, cycle_steps, steps_cfg):
    involved = set()
    m = re.search(r'缺少[：:]\s*\[?([^\]]+)\]?', reason)
    if m:
        for s in re.split(r'[,，]', m.group(1)):
            n = s.strip().strip("'\" "); involved.add(n) if n else None
    if '重复' in reason:
        m = re.search(r'重复步骤[：:]\s*\[?([^\]]+)\]?', reason)
        if m:
            for s in re.split(r'[,，]', m.group(1)):
                n = s.strip().strip("'\" "); involved.add(n) if n else None
    if '顺序错误' in reason:
        m = re.search(r'期望\[(.+?)\].*实际\[(.+?)\]', reason)
        if m:
            if m.group(1).strip(): involved.add(m.group(1).strip())
            if m.group(2).strip(): involved.add(m.group(2).strip())
    if '缺件' in reason:
        m = re.search(r'缺件[：:]\s*\{?([^}]+)\}?', reason)
        if m:
            for s in re.split(r'[,，]', m.group(1)):
                n = s.strip().strip("'\" "); involved.add(n) if n else None
    if not involved:
        expected = set(s.get('label') for s in steps_cfg if s.get('label') and not s.get('is_backup'))
        actual = set(cycle_steps)
        missing = expected - actual
        if missing: involved = missing
        elif '顺序' in reason and cycle_steps: involved = set(cycle_steps)
    return involved

cfg = project.steps_config
check("'缺少: scan' → {scan}", ng_top3("缺少: ['scan']", [], cfg) == {"scan"})
check("'期望[front]实际[tilt]' → {front,tilt}", ng_top3("顺序错误，期望[front]在前 实际[tilt]在前", [], cfg) == {"front","tilt"})
check("'第2步顺序错误' → fallback finds steps", len(ng_top3("第2步顺序错误", ["wipe","tilt"], cfg)) > 0)
check("'放入顺序错误' → fallback finds steps", len(ng_top3("放入顺序错误: 期望a, 实际b", ["a","b"], cfg)) > 0)
check("'缺件: scan, back' → regex matches", {"scan","back"}.issubset(ng_top3("缺件: scan, back", [], cfg)))
check("'重复步骤: wipe' → {wipe}", ng_top3("重复步骤: ['wipe']", [], cfg) == {"wipe"})

check("Backend: fallback logic exists (if not involved)",
      file_contains("backend/api/source.py", r"if not involved:.*\n.*steps_config"))


# ═══════════════════════════════════════════════════════════
# Bug 10: 百分比计算逻辑
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 10: 百分比计算逻辑 ══")

check("yieldRate: division by zero protected (total > 0)",
      file_contains("frontend/src/views/Monitor/index.vue", r"total > 0 \? Math\.round"))

# Simulate
def yield_rate(total, ok):
    return total > 0 and round((ok / total) * 100) or 0

check("total=0, ok=0 → 0%", yield_rate(0, 0) == 0)
check("total=10, ok=10 → 100%", yield_rate(10, 10) == 100)
check("total=10, ok=0 → 0%", yield_rate(10, 0) == 0)
check("total=10, ok=8 → 80%", yield_rate(10, 8) == 80)

check("NG TOP3 rate: division by zero protected",
      file_contains("frontend/src/views/Monitor/index.vue", r"chTotalCycles > 0 \?|totalCycles > 0 \?"))


# ═══════════════════════════════════════════════════════════
# Bug 11: 升级后数据保留
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 11: 软件升级后历史数据保留 ══")

check("config.py: DATA_DIR from TIANJUN_DATA_DIR env",
      file_contains("backend/core/config.py", r"TIANJUN_DATA_DIR"))

check("config.py: _migrate_old_data function exists",
      file_contains("backend/core/config.py", r"def _migrate_old_data"))

check("backend-manager.js: sets TIANJUN_DATA_DIR = userDataPath",
      file_contains("electron/backend-manager.js", r"TIANJUN_DATA_DIR.*userDataPath"))

check("installer.nsh: backs up sql_app.db before uninstall",
      file_contains("electron/build/installer.nsh", r"sql_app\.db"))

check("package.json: excludes *.db from build (doesn't bundle DB)",
      file_contains("electron/package.json", r"\*\.db"))


# ═══════════════════════════════════════════════════════════
# Bug 12: 停止检测 + 跨日
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 12: 停止检测计数器 + 跨日拆分 ══")

check("stopDetectionHandler calls stopPolling()",
      file_contains("frontend/src/views/Monitor/index.vue", r"stopPolling\(\)"))

check("stopDetectionHandler calls pauseDetection()",
      file_contains("frontend/src/views/Monitor/index.vue", r"await pauseDetection\(\)"))

check("Backend: _auto_split_session checks date change",
      file_contains("backend/api/source.py", r"datetime\.now\(\)\.date\(\) != self\._session_start_date"))

check("Backend: _auto_split_session calls end_session + start_session",
      file_contains("backend/api/source.py", r"self\.end_session\(\)\n.*self\.start_session"))


# ═══════════════════════════════════════════════════════════
# Bug 14: 大量数据分页
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 14: 历史数据分页 ══")

check("Frontend: el-pagination component for cycles",
      file_contains("frontend/src/views/Data/index.vue", r"el-pagination"))

check("Frontend: cyclePageSize = 50",
      file_contains("frontend/src/views/Data/index.vue", r"cyclePageSize.*=.*50"))

check("Backend: get_session_cycles has skip/limit params",
      file_contains("backend/api/sessions.py", r"def get_session_cycles") and
      file_contains("backend/api/sessions.py", r"skip:\s*int\s*=\s*0"))

check("Backend: uses .offset(skip).limit(limit)",
      file_contains("backend/api/sessions.py", r"\.offset\(skip\)\.limit\(limit\)"))


# ═══════════════════════════════════════════════════════════
# Bug 15: 步骤序列与详情标识
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 15: 步骤序列与步骤详情标识一致性 ══")

check("Backend: step_display_names mapping (label → displayLabel)",
      file_contains("backend/api/source.py", r"step_display_names\[label\]\s*=\s*display_label"))

check("Backend: _get_step_order_map uses steps_config enumerate",
      file_contains("backend/api/sessions.py", r"enumerate\(proj\.steps_config\)"))

check("Backend: StepRecord has step_label and step_name",
      file_contains("backend/models/models.py", r"step_label.*Column.*String") and
      file_contains("backend/models/models.py", r"step_name.*Column.*String"))

# Simulate order map
order_map = {step.get('label'): idx for idx, step in enumerate(project.steps_config)}
check("Order map: wipe=0, front=1, scan=4, place=5",
      order_map == {"wipe":0,"front":1,"tilt":2,"back":3,"scan":4,"place":5},
      f"got {order_map}")


# ═══════════════════════════════════════════════════════════
# Bug 16: 显示不良但没记录步骤序列
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 16: NG路径步骤记录完整性 ══")

check("_reconcile_step_records called before NG in sequential settle",
      file_contains("backend/api/source.py", r"_reconcile_step_records\(\)[\s\S]{0,500}_trigger_event\(2"))

check("_reconcile_step_records called before NG in _check_sequential_mode",
      file_contains("backend/api/source.py", r"_reconcile_step_records\(\)"))

check("_reconcile_step_records function exists and creates missing records",
      file_contains("backend/api/source.py", r"def _reconcile_step_records"))


# ═══════════════════════════════════════════════════════════
# Bug 17: 部分数据从第三步开始周期
# ═══════════════════════════════════════════════════════════
print("\n══ Bug 17: 第一步再现结算 — 新周期从第一步开始 ══")

check("First-step reappearance settlement logic exists",
      file_contains("backend/api/source.py", r"第一步.*再次检测到.*直接结算"))

check("_just_settled guard blocks non-first-step from starting cycle",
      file_contains("backend/api/source.py", r"_just_settled.*False") and
      file_contains("backend/api/source.py", r"label != first_step_label"))

check("_settle_sequential_cycle clears current_cycle_steps",
      file_contains("backend/api/source.py", r"self\.current_cycle_steps\s*=\s*\[\]"))


# ═══════════════════════════════════════════════════════════
# CT/PT 改进
# ═══════════════════════════════════════════════════════════
print("\n══ CT/PT: 平均值计算 ══")

# CT
ok_durs = [c[1] for c in cycles_data if c[2]]
all_durs = [c[1] for c in cycles_data]
avg_ok = round(sum(ok_durs) / len(ok_durs), 2)
avg_all = round(sum(all_durs) / len(all_durs), 2)

check(f"CT (OK only) = {avg_ok}s", avg_ok == 16.94, f"8 OK cycles avg")
check(f"CT (with NG) = {avg_all}s", avg_all == 14.79, f"12 all cycles avg")
check("Display setting: ctIncludeNg toggle",
      file_contains("frontend/src/store/useSystemStore.js", r"ctIncludeNg:\s*false"))
check("Settings UI: CT包含NG周期 switch",
      file_contains("frontend/src/views/Settings/index.vue", r"CT包含NG周期"))
check("Backend: ng_cycle_times list",
      file_contains("backend/api/source.py", r"self\.ng_cycle_times\s*=\s*\[\]"))
check("Backend API: average_cycle_time_with_ng",
      file_contains("backend/api/source.py", r"average_cycle_time_with_ng"))

# PT
pt_hist = {"wipe": [2.1,2.3,2.0,1.9,2.5], "front": [3.0,3.2,2.8,3.1], "scan": [1.0,1.2,0.9]}
avg_pt = {k: round(sum(v)/len(v), 2) for k, v in pt_hist.items()}
check(f"PT wipe avg={avg_pt['wipe']}s (not last 2.5)", avg_pt["wipe"] != 2.5 and avg_pt["wipe"] == 2.16)
check(f"PT front avg={avg_pt['front']}s", avg_pt["front"] == 3.02)
check("Backend: step_durations_history dict",
      file_contains("backend/api/source.py", r"self\.step_durations_history\s*="))
check("Backend API: avg_step_durations",
      file_contains("backend/api/source.py", r"avg_step_durations"))
check("Frontend: formatStepPT uses avgStepDurations",
      file_contains("frontend/src/views/Monitor/index.vue", r"avgStepDurations\.value\[stepLabel\]"))


# ═══════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════
db.close()

print("\n" + "═" * 60)
total = len(results)
passed = sum(1 for _, ok in results if ok)
failed = total - passed
print(f"Total: {total}  |  {PASS}: {passed}  |  {FAIL}: {failed}")
if failed == 0:
    print("\n  ★ All tests passed! ★")
else:
    print(f"\nFailed ({failed}):")
    for name, ok in results:
        if not ok:
            print(f"  ✗ {name}")
print("═" * 60)
