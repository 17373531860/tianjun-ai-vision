"""v3.23 NG 补做 (缺步骤延迟落账) 状态机单测.

覆盖唯一收口点 `_trigger_event` 的 defer 守门 + `resolve_step_remediation` 三条解析路径:
  - 默认关 → 缺步骤 NG 立刻 end_cycle 落账 (零差异)
  - 开启   → 缺步骤 NG 挂起 (不 end_cycle / 报警提示 / 进阻塞态)
  - 补步骤 → 缺的步骤补进序列, 判 OK 落账
  - 认 NG  → 现在才 end_cycle 落 NG (绕 defer 守门一次, 不自锁)
  - 重做   → 丢弃在制周期 (_discard_empty_cycle), 不 end_cycle

不依赖真实 DB: 桩掉 end_cycle / 报警 / 丢弃 / 清运行时 / 计数持久化, 只验状态迁移.
"""
import os
os.environ.setdefault("BACKEND_SKIP_INIT", "1")

import pytest
from backend.api.source import VideoSourceManager


def _make_vsm(rem_enabled=True, allow_step=True):
    v = VideoSourceManager(0)
    v.project_config = {
        'id': 1,
        'logic_mode': 'sequential',
        'events_config': [
            {'id': 1, 'name': '合格'},
            {'id': 2, 'name': '不合格', 'require_ack': False},
        ],
        'steps_config': [
            {'label': 'A'}, {'label': 'B'},
        ],
        'pipeline_config': {},
    }
    v._ng_remediation = {'enabled': rem_enabled, 'allow_step': allow_step, 'allow_count': True}
    v.current_cycle_id = 123
    # v3.38: "周期进行中"守门看 uuid (defer 守门也改成了 uuid), 桩上必须给
    v.current_cycle_uuid = 'rem-uuid1'
    v.current_cycle_number = 1
    v.cycle_start_time = None  # 跳过 CT 记录
    v.current_cycle_steps = ['A']  # 缺 B
    v._mes_hook = None

    # 桩掉 DB / 物理副作用
    calls = {'end_cycle': [], 'alarm': [], 'discard': 0, 'clear': 0}

    def fake_end_cycle(is_good, event_id=None, event_name=None, reason=None):
        calls['end_cycle'].append({'is_good': is_good, 'reason': reason})
        # 模拟真实 end_cycle: 周期结算后周期句柄清零
        v.current_cycle_id = None
        v.current_cycle_uuid = None

    def fake_alarm(eid):
        calls['alarm'].append(eid)

    def fake_discard():
        calls['discard'] += 1
        v.current_cycle_id = None
        v.current_cycle_uuid = None

    def fake_clear():
        calls['clear'] += 1

    v.end_cycle = fake_end_cycle
    v._dispatch_event_alarm = fake_alarm
    v._discard_empty_cycle = fake_discard
    v._clear_step_runtime_state = fake_clear
    v._persist_counters = lambda: None
    return v, calls


# ============================================================
# 默认关 = 零差异
# ============================================================

def test_remediation_off_settles_ng_immediately():
    v, calls = _make_vsm(rem_enabled=False)
    ret = v._trigger_event(2, "周期不完整，缺少: ['B']")
    assert ret is True
    assert len(calls['end_cycle']) == 1
    assert calls['end_cycle'][0]['is_good'] is False
    assert v._pending_ack is False
    assert v._pending_remediation is None


def test_remediation_on_but_not_missing_reason_settles_immediately():
    # 开了补步骤, 但 NG 原因不是缺步骤 (顺序错误) → 不挂起, 立刻落 NG
    v, calls = _make_vsm(rem_enabled=True)
    ret = v._trigger_event(2, "顺序错误，期望[A]在前 实际[B]在前")
    assert ret is True
    assert len(calls['end_cycle']) == 1
    assert v._pending_remediation is None


def test_ok_event_never_deferred():
    v, calls = _make_vsm(rem_enabled=True)
    ret = v._trigger_event(1, "全部完成")
    assert ret is True
    assert len(calls['end_cycle']) == 1
    assert calls['end_cycle'][0]['is_good'] is True
    assert v._pending_remediation is None


# ============================================================
# 开启 → 缺步骤挂起
# ============================================================

def test_missing_step_holds_when_remediation_on():
    v, calls = _make_vsm(rem_enabled=True)
    ret = v._trigger_event(2, "周期不完整，缺少: ['B']")
    assert ret is False  # 挂起, 事件未"触发完成"
    assert len(calls['end_cycle']) == 0  # 延迟落账: 不写库
    assert v._pending_ack is True  # 进阻塞态
    assert v._pending_remediation is not None
    assert v._pending_remediation['kind'] == 'missing_step'
    assert v._pending_remediation['missing'] == ['B']
    assert v.current_cycle_id == 123  # 在制周期保留
    assert len(calls['alarm']) == 1  # 报警提示工人


def test_hold_blocks_subsequent_events():
    # 挂起后, 后续事件被 _pending_ack 入口拦掉
    v, calls = _make_vsm(rem_enabled=True)
    v._trigger_event(2, "周期不完整，缺少: ['B']")
    ret2 = v._trigger_event(2, "周期不完整，缺少: ['B']")
    assert ret2 is False
    assert v._pending_remediation['missing'] == ['B']  # 首个挂起不被刷掉


# ============================================================
# 三条解析路径
# ============================================================

def test_resolve_supplement_step_settles_ok():
    v, calls = _make_vsm(rem_enabled=True)
    v._trigger_event(2, "周期不完整，缺少: ['B']")
    res = v.resolve_step_remediation('supplement_step', operator='admin')
    assert res['resolved'] is True
    assert res['action'] == 'supplement_step'
    assert 'B' in v.current_cycle_steps  # 缺的步骤补进序列
    assert len(calls['end_cycle']) == 1
    assert calls['end_cycle'][0]['is_good'] is True  # 判 OK 落账
    assert v._pending_ack is False
    assert v._pending_remediation is None


def test_resolve_confirm_ng_settles_ng_without_selflock():
    v, calls = _make_vsm(rem_enabled=True)
    v._trigger_event(2, "周期不完整，缺少: ['B']")
    res = v.resolve_step_remediation('confirm_ng', operator='admin')
    assert res['resolved'] is True
    assert res['action'] == 'confirm_ng'
    # 关键: confirm_ng 重发 NG 没有被 defer 守门重新挂起 (旁路生效)
    assert len(calls['end_cycle']) == 1
    assert calls['end_cycle'][0]['is_good'] is False  # 落 NG
    assert v._pending_ack is False
    assert v._pending_remediation is None
    assert v._remediation_bypass is False  # 一次性旁路已复位


def test_resolve_redo_discards_no_settle():
    v, calls = _make_vsm(rem_enabled=True)
    v._trigger_event(2, "周期不完整，缺少: ['B']")
    res = v.resolve_step_remediation('redo', operator='admin')
    assert res['resolved'] is True
    assert res['action'] == 'redo'
    assert len(calls['end_cycle']) == 0  # 不落账
    assert calls['discard'] == 1  # 丢弃在制周期
    assert v._pending_ack is False
    assert v._pending_remediation is None


def test_resolve_no_pending_returns_false():
    v, calls = _make_vsm(rem_enabled=True)
    res = v.resolve_step_remediation('supplement_step')
    assert res['resolved'] is False
    assert len(calls['end_cycle']) == 0


# ============================================================
# 解析辅助
# ============================================================

def test_parse_missing_steps_variants():
    v, _ = _make_vsm()
    # 顺序模式文案
    assert v._parse_missing_steps("周期不完整，缺少: ['B']") == ['B']
    assert v._parse_missing_steps("缺少: ['B', 'C']") == ['B', 'C']
    assert v._parse_missing_steps("缺少：[放油嘴，封箱]") == ['放油嘴', '封箱']
    # 检测模式文案 "缺少步骤: [...]" (原正则漏掉的格式)
    assert v._parse_missing_steps("缺少步骤: ['B']") == ['B']
    assert v._parse_missing_steps("自定义(基于检测)结算 缺少步骤: ['A', 'B']") == ['A', 'B']


def test_parse_missing_steps_fallback_from_config():
    # 正则抠不到 → 兜底用 steps_config(A,B) - 已出现(['A']) = ['B']
    v, _ = _make_vsm()
    v.current_cycle_steps = ['A']
    assert v._parse_missing_steps("顺序错误") == ['B']
    # 全出现 → 兜底为空
    v.current_cycle_steps = ['A', 'B']
    assert v._parse_missing_steps("顺序错误") == []
