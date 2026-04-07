"""
综合测试：班次拆分 + 超时NG 两个新功能的前后端连通性验证
测试内容：
  1. 数据库迁移 — shift_label 列存在
  2. API 层 — 项目 CRUD 能正确读写 data_config / pipeline_config
  3. 运行时 — VideoSourceManager 正确解析班次和超时配置
  4. 班次判定逻辑 — _get_current_shift 在各种时间配置下的正确性
  5. 步骤超时NG — timeout_ng 开关触发 _force_timeout_ng
  6. 周期超时NG — cycle_max_duration 触发 _force_timeout_ng

用法：  python test_new_features.py
"""
import sys, os, time
from datetime import datetime
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(__file__))

passed = 0
failed = 0

def ok(name):
    global passed
    passed += 1
    print(f"  ✓ {name}")

def fail(name, detail=""):
    global failed
    failed += 1
    print(f"  ✗ {name}  — {detail}")

def check(name, condition, detail=""):
    if condition:
        ok(name)
    else:
        fail(name, detail)


# ═══════════════════════════════════════════
# 1. 数据库模型：DetectionSession.shift_label
# ═══════════════════════════════════════════
print("\n[1] 数据库模型检查")
try:
    from backend.models.models import DetectionSession
    col_names = [c.name for c in DetectionSession.__table__.columns]
    check("DetectionSession 有 shift_label 列", "shift_label" in col_names, f"实际列: {col_names}")
    shift_col = DetectionSession.__table__.c.shift_label
    check("shift_label 可为空", shift_col.nullable is True)
    check("shift_label 有索引", shift_col.index is True)
except Exception as e:
    fail("加载 DetectionSession 模型", str(e))

# ═══════════════════════════════════════════
# 2. 数据库迁移列表包含 shift_label
# ═══════════════════════════════════════════
print("\n[2] 数据库迁移检查")
try:
    import importlib
    main_mod = importlib.import_module("backend.main")
    src = open(main_mod.__file__, encoding="utf-8").read()
    check("迁移列表包含 shift_label", '"shift_label"' in src and '"detection_sessions"' in src)
except Exception as e:
    fail("读取 main.py 迁移列表", str(e))

# ═══════════════════════════════════════════
# 3. Pydantic Schema 检查
# ═══════════════════════════════════════════
print("\n[3] Pydantic Schema 检查")
try:
    from backend.schemas.project import ProjectUpdate, ProjectResponse, ProjectCreate
    pu_fields = set(ProjectUpdate.model_fields.keys())
    check("ProjectUpdate 包含 data_config", "data_config" in pu_fields)
    check("ProjectUpdate 包含 pipeline_config", "pipeline_config" in pu_fields)
    pr_fields = set(ProjectResponse.model_fields.keys())
    check("ProjectResponse 包含 data_config", "data_config" in pr_fields)
except Exception as e:
    fail("加载 Pydantic Schema", str(e))

# ═══════════════════════════════════════════
# 4. ProjectConfigRequest 包含 data_config
# ═══════════════════════════════════════════
print("\n[4] ProjectConfigRequest (set-project API) 检查")
try:
    src_file = os.path.join(os.path.dirname(__file__), "backend", "api", "source.py")
    src_text = open(src_file, encoding="utf-8").read()
    check("ProjectConfigRequest 有 data_config 字段",
          "data_config: dict" in src_text or "data_config:" in src_text)
    check("set_project_config 传递 data_config",
          "'data_config': req.data_config" in src_text)
except Exception as e:
    fail("检查 source.py", str(e))

# ═══════════════════════════════════════════
# 5. VideoSourceManager 配置解析
# ═══════════════════════════════════════════
print("\n[5] VideoSourceManager 配置解析")
try:
    from backend.api.source import VideoSourceManager
    vm = VideoSourceManager.__new__(VideoSourceManager)
    vm._init_inference_vars()
    vm.channel_id = 0

    check("初始 cycle_max_duration = 0", vm.cycle_max_duration == 0)
    check("初始 idle_timeout_seconds = 0", vm.idle_timeout_seconds == 0)

    test_config = {
        'id': 999, 'name': 'test', 'task_type': 'detection',
        'logic_mode': 'sequential',
        'steps_config': [
            {'id': 1, 'label': 'step_a', 'enabled': True, 'threshold': 50,
             'max_duration': 10, 'timeout_ng': True, 'min_frames': 1},
            {'id': 2, 'label': 'step_b', 'enabled': True, 'threshold': 50,
             'max_duration': None, 'timeout_ng': False, 'min_frames': 1},
        ],
        'pipeline_config': {
            'settlement_mode': 'first_step',
            'idle_timeout_seconds': 30,
            'cycle_max_duration': 120,
            'sequence_order': [{'step_id': 1}, {'step_id': 2}],
        },
        'events_config': [
            {'id': 1, 'name': 'OK', 'actions': []},
            {'id': 2, 'name': 'NG', 'actions': []},
        ],
        'counters_config': [],
        'data_config': {
            'shift_split_enabled': True,
            'day_shift_start': '07:30',
            'night_shift_start': '19:30',
        }
    }
    vm.set_project_config(test_config)

    check("cycle_max_duration 被正确加载", vm.cycle_max_duration == 120,
          f"实际值: {vm.cycle_max_duration}")
    check("idle_timeout_seconds 被正确加载", vm.idle_timeout_seconds == 30)
    check("settlement_mode 被正确加载", vm.settlement_mode == 'first_step')

    step_a_cfg = vm.step_time_config.get('step_a', {})
    check("step_a.max_duration = 10", step_a_cfg.get('max_duration') == 10)
    check("step_a.timeout_ng = True", step_a_cfg.get('timeout_ng') is True)

    step_b_cfg = vm.step_time_config.get('step_b', {})
    check("step_b.timeout_ng = False", step_b_cfg.get('timeout_ng') is False)
    check("step_b.max_duration = None", step_b_cfg.get('max_duration') is None)

    check("data_config 被保存到 project_config",
          vm.project_config.get('data_config', {}).get('shift_split_enabled') is True)
except Exception as e:
    import traceback
    traceback.print_exc()
    fail("VideoSourceManager 配置解析", str(e))

# ═══════════════════════════════════════════
# 6. _get_current_shift 班次判定
# ═══════════════════════════════════════════
print("\n[6] 班次判定逻辑 (_get_current_shift)")
try:
    vm2 = VideoSourceManager.__new__(VideoSourceManager)
    vm2._init_inference_vars()
    vm2.channel_id = 0

    # 测试 shift_split_enabled = False → 返回 None
    vm2.project_config = {'data_config': {'shift_split_enabled': False}}
    check("shift 关闭时返回 None", vm2._get_current_shift() is None)

    # 测试 无 data_config → 返回 None
    vm2.project_config = {}
    check("无 data_config 返回 None", vm2._get_current_shift() is None)

    # 测试 标准两班制 08:00-20:00
    vm2.project_config = {'data_config': {
        'shift_split_enabled': True, 'day_shift_start': '08:00', 'night_shift_start': '20:00'
    }}

    with patch('backend.api.source.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 17, 10, 0, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        check("10:00 → day (08-20)", vm2._get_current_shift() == 'day',
              f"got: {vm2._get_current_shift()}")

        mock_dt.now.return_value = datetime(2026, 3, 17, 22, 0, 0)
        check("22:00 → night (08-20)", vm2._get_current_shift() == 'night',
              f"got: {vm2._get_current_shift()}")

        mock_dt.now.return_value = datetime(2026, 3, 17, 7, 59, 0)
        check("07:59 → night (08-20)", vm2._get_current_shift() == 'night',
              f"got: {vm2._get_current_shift()}")

        mock_dt.now.return_value = datetime(2026, 3, 17, 8, 0, 0)
        check("08:00 → day (08-20)", vm2._get_current_shift() == 'day',
              f"got: {vm2._get_current_shift()}")

        mock_dt.now.return_value = datetime(2026, 3, 17, 19, 59, 0)
        check("19:59 → day (08-20)", vm2._get_current_shift() == 'day',
              f"got: {vm2._get_current_shift()}")

        mock_dt.now.return_value = datetime(2026, 3, 17, 20, 0, 0)
        check("20:00 → night (08-20)", vm2._get_current_shift() == 'night',
              f"got: {vm2._get_current_shift()}")

    # 测试 自定义班次 07:30-19:30
    vm2.project_config = {'data_config': {
        'shift_split_enabled': True, 'day_shift_start': '07:30', 'night_shift_start': '19:30'
    }}
    with patch('backend.api.source.datetime') as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 17, 7, 30, 0)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        check("07:30 → day (07:30-19:30)", vm2._get_current_shift() == 'day',
              f"got: {vm2._get_current_shift()}")

        mock_dt.now.return_value = datetime(2026, 3, 17, 19, 30, 0)
        check("19:30 → night (07:30-19:30)", vm2._get_current_shift() == 'night',
              f"got: {vm2._get_current_shift()}")

except Exception as e:
    import traceback
    traceback.print_exc()
    fail("班次判定逻辑", str(e))

# ═══════════════════════════════════════════
# 7. _force_timeout_ng 方法存在且能清理状态
# ═══════════════════════════════════════════
print("\n[7] _force_timeout_ng 方法检查")
try:
    check("_force_timeout_ng 方法存在", hasattr(VideoSourceManager, '_force_timeout_ng'))

    vm3 = VideoSourceManager.__new__(VideoSourceManager)
    vm3._init_inference_vars()
    vm3.channel_id = 0
    vm3.set_project_config({
        'id': 999, 'name': 'test', 'task_type': 'detection',
        'logic_mode': 'sequential',
        'steps_config': [],
        'pipeline_config': {'settlement_mode': 'first_step'},
        'events_config': [
            {'id': 1, 'name': 'OK', 'actions': []},
            {'id': 2, 'name': 'NG', 'actions': []},
        ],
        'counters_config': [],
        'data_config': {}
    })
    vm3.current_cycle_steps = ['step_a', 'step_b']
    vm3.last_added_step = 'step_b'
    vm3.step_last_seen = {'step_a': time.time()}
    vm3.step_start_time = {'step_a': time.time() - 5}
    vm3._last_step_added_time = time.time()
    vm3.last_step_completed_time = time.time()
    vm3.cycle_start_time = time.time() - 10
    vm3.current_cycle_id = None
    vm3.recording_enabled = False

    triggered_events = []
    original_trigger = vm3._trigger_event
    def mock_trigger(event_id, reason):
        triggered_events.append((event_id, reason))
    vm3._trigger_event = mock_trigger

    vm3._force_timeout_ng("测试超时")

    check("_trigger_event 被调用且 event_id=2",
          len(triggered_events) == 1 and triggered_events[0][0] == 2,
          f"triggered: {triggered_events}")
    check("reason 正确传递", "测试超时" in triggered_events[0][1])
    check("current_cycle_steps 被清空", vm3.current_cycle_steps == [])
    check("step_last_seen 被清空", len(vm3.step_last_seen) == 0)
    check("step_start_time 被清空", len(vm3.step_start_time) == 0)
    check("_last_step_added_time 被置 None", vm3._last_step_added_time is None)
    check("last_step_completed_time 被置 None", vm3.last_step_completed_time is None)

except Exception as e:
    import traceback
    traceback.print_exc()
    fail("_force_timeout_ng 方法", str(e))

# ═══════════════════════════════════════════
# 8. 步骤超时NG 触发路径
# ═══════════════════════════════════════════
print("\n[8] 步骤超时NG 触发路径")
try:
    src_text = open(os.path.join(os.path.dirname(__file__), "backend", "api", "source.py"),
                    encoding="utf-8").read()
    check("代码中有 timeout_ng 检查",
          "time_cfg.get('timeout_ng')" in src_text)
    check("步骤超时NG 调用 _force_timeout_ng",
          "self._force_timeout_ng(f'步骤" in src_text)
    check("步骤超时NG 后 return (阻止后续处理)",
          "self._force_timeout_ng(f'步骤" in src_text and
          "return" in src_text.split("self._force_timeout_ng(f'步骤")[1][:120])
except Exception as e:
    fail("步骤超时NG 代码检查", str(e))

# ═══════════════════════════════════════════
# 9. 周期超时NG 触发路径
# ═══════════════════════════════════════════
print("\n[9] 周期超时NG 触发路径")
try:
    check("代码中有 cycle_max_duration 检查",
          "self.cycle_max_duration > 0" in src_text)
    check("周期超时NG 调用 _force_timeout_ng",
          "self._force_timeout_ng(f'周期总时长超时" in src_text)
    # 确认周期超时检查在空闲超时之前
    idx_cycle = src_text.index("周期总时长超时NG")
    idx_idle = src_text.index("空闲超时结算")
    check("周期超时检查在空闲超时之前", idx_cycle < idx_idle,
          f"cycle@{idx_cycle}, idle@{idx_idle}")
except Exception as e:
    fail("周期超时NG 代码检查", str(e))

# ═══════════════════════════════════════════
# 10. 前端字段一致性
# ═══════════════════════════════════════════
print("\n[10] 前端字段一致性")
try:
    proj_vue = open(os.path.join(os.path.dirname(__file__),
        "frontend", "src", "views", "Project", "index.vue"), encoding="utf-8").read()

    check("前端有 timeout_ng 开关", "step.timeout_ng" in proj_vue)
    check("前端有 cycle_max_duration 输入", "cycle_max_duration" in proj_vue)
    check("前端 initProjectDefaults 读取 cycle_max_duration",
          "pipelineConfig.cycle_max_duration" in proj_vue)
    check("前端保存 cycle_max_duration 到 pipeline_config",
          "cycle_max_duration: activeProject.value.cycle_max_duration" in proj_vue)

    check("前端有 shift_split_enabled 开关", "shift_split_enabled" in proj_vue)
    check("前端有 day_shift_start 时间选择器", "day_shift_start" in proj_vue)
    check("前端有 night_shift_start 时间选择器", "night_shift_start" in proj_vue)
    check("前端保存 data_config (shift)",
          "shift_split_enabled: activeProject.value.shift_split_enabled" in proj_vue)

    monitor_vue = open(os.path.join(os.path.dirname(__file__),
        "frontend", "src", "views", "Monitor", "index.vue"), encoding="utf-8").read()
    check("Monitor 传递 data_config",
          "data_config: currentProject.value.data_config" in monitor_vue or
          "data_config: proj.data_config" in monitor_vue)

    data_vue = open(os.path.join(os.path.dirname(__file__),
        "frontend", "src", "views", "Data", "index.vue"), encoding="utf-8").read()
    check("Data 页面使用项目 shift 时间",
          "dc.day_shift_start" in data_vue)
    check("Data 页面传递 shift 参数",
          "shiftParam" in data_vue)

    data_js = open(os.path.join(os.path.dirname(__file__),
        "frontend", "src", "api", "data.js"), encoding="utf-8").read()
    check("data.js getSessionsByDate 有 shift 参数",
          "shift" in data_js and "params.shift" in data_js)

except Exception as e:
    import traceback
    traceback.print_exc()
    fail("前端字段一致性", str(e))

# ═══════════════════════════════════════════
# 11. sessions API 支持 shift 参数
# ═══════════════════════════════════════════
print("\n[11] sessions API shift 参数")
try:
    sessions_src = open(os.path.join(os.path.dirname(__file__),
        "backend", "api", "sessions.py"), encoding="utf-8").read()
    check("API 有 shift 参数", "shift: Optional[str]" in sessions_src)
    check("API 过滤 shift_label",
          "DetectionSession.shift_label == shift" in sessions_src)
except Exception as e:
    fail("sessions API 检查", str(e))

# ═══════════════════════════════════════════
# 12. start_session 写入 shift_label
# ═══════════════════════════════════════════
print("\n[12] start_session 写入 shift_label")
try:
    check("start_session 设置 shift_label",
          "shift_label=current_shift" in src_text)
    check("start_session 设置 _session_start_shift",
          "self._session_start_shift = current_shift" in src_text)
except Exception as e:
    fail("start_session 检查", str(e))

# ═══════════════════════════════════════════
# 13. start_cycle 班次变更检查
# ═══════════════════════════════════════════
print("\n[13] start_cycle 班次变更检查")
try:
    check("start_cycle 检查班次变更",
          "current_shift != self._session_start_shift" in src_text)
    check("班次变更调用 _auto_split_session",
          '班次变更' in src_text and '_auto_split_session' in src_text)
except Exception as e:
    fail("start_cycle 班次变更检查", str(e))

# ═══════════════════════════════════════════
# 14. end_session 重置 _session_start_shift
# ═══════════════════════════════════════════
print("\n[14] end_session 清理检查")
try:
    check("end_session 重置 _session_start_shift",
          "self._session_start_shift = None" in src_text)
except Exception as e:
    fail("end_session 清理检查", str(e))


# ═══════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════
print(f"\n{'='*50}")
total = passed + failed
print(f"测试完成: {passed}/{total} 通过", end="")
if failed:
    print(f"，{failed} 失败")
else:
    print("，全部通过!")
print(f"{'='*50}")
sys.exit(1 if failed else 0)
