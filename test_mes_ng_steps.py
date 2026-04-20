"""验证 MESGateway.build_context_from_cycle 生成的 ng_steps 与 missing_step_count"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.services.mes_gateway import MESGateway


class _FakeQuery:
    def __init__(self, objs):
        self._objs = objs

    def filter(self, *a, **kw):
        return self

    def order_by(self, *a, **kw):
        return self

    def first(self):
        return self._objs[0] if self._objs else None

    def all(self):
        return list(self._objs)


class _FakeDB:
    """按 model 返回预置对象列表的最小假 Session"""
    def __init__(self, mapping):
        self._mapping = mapping

    def query(self, model):
        return _FakeQuery(self._mapping.get(model, []))


def _make_cycle(total=5, completed=3):
    return SimpleNamespace(
        id=1, total_steps=total, completed_steps=completed,
        start_time=datetime(2026, 4, 13, 10, 0, 0),
        end_time=datetime(2026, 4, 13, 10, 0, 12),
        operator_id=None,
    )


def _make_step(label, idx, ok, dur=1.0, conf=0.9):
    return SimpleNamespace(
        step_label=label, step_index=idx, is_good=ok,
        duration_seconds=dur, confidence=conf,
        start_time=datetime(2026, 4, 13, 10, 0, idx),
        end_time=datetime(2026, 4, 13, 10, 0, idx + 1),
    )


def test_ng_steps_and_missing_count():
    from backend.models.models import DetectionCycle, StepRecord, Operator

    steps = [
        _make_step('贴标签', 1, True),
        _make_step('装电池', 2, False),
        _make_step('盖上盒', 3, True),
        _make_step('贴封条', 4, False),
    ]
    db = _FakeDB({
        DetectionCycle: [_make_cycle(total=5, completed=3)],
        StepRecord: steps,
        Operator: [],
    })

    gw = MESGateway()
    ctx = gw.build_context_from_cycle(
        db, cycle_id=1, is_good=False, event_name='漏步骤',
        result_reason='缺失 装电池/贴封条',
        project_id=None, workpiece_id=None, order_id=None,
    )

    assert ctx['cycle']['result'] == 'NG'
    assert len(ctx['steps']) == 4
    assert len(ctx['ng_steps']) == 2
    names = [s['label'] for s in ctx['ng_steps']]
    assert names == ['装电池', '贴封条']
    assert ctx['cycle']['missing_step_count'] == 2  # total=5, completed=3


def test_all_good_no_missing():
    from backend.models.models import DetectionCycle, StepRecord, Operator

    steps = [_make_step('A', 1, True), _make_step('B', 2, True)]
    db = _FakeDB({
        DetectionCycle: [_make_cycle(total=2, completed=2)],
        StepRecord: steps,
        Operator: [],
    })

    gw = MESGateway()
    ctx = gw.build_context_from_cycle(db, cycle_id=2, is_good=True, project_id=None)
    assert ctx['cycle']['result'] == 'OK'
    assert ctx['ng_steps'] == []
    assert ctx['cycle']['missing_step_count'] == 0


def test_missing_count_fallback_when_totals_missing():
    from backend.models.models import DetectionCycle, StepRecord, Operator

    cycle = SimpleNamespace(
        id=3, total_steps=None, completed_steps=None,
        start_time=None, end_time=None, operator_id=None,
    )
    steps = [
        _make_step('X', 1, False),
        _make_step('Y', 2, True),
        _make_step('Z', 3, False),
    ]
    db = _FakeDB({DetectionCycle: [cycle], StepRecord: steps, Operator: []})

    gw = MESGateway()
    ctx = gw.build_context_from_cycle(db, cycle_id=3, is_good=False)
    # total/completed 缺失时，回退到 ng_steps 长度
    assert ctx['cycle']['missing_step_count'] == 2


if __name__ == '__main__':
    tests = [
        test_ng_steps_and_missing_count,
        test_all_good_no_missing,
        test_missing_count_fallback_when_totals_missing,
    ]
    fail = 0
    for t in tests:
        try:
            t()
            print(f'PASS  {t.__name__}')
        except Exception as e:
            fail += 1
            print(f'FAIL  {t.__name__}: {type(e).__name__}: {e}')
    print(f'\n{len(tests) - fail}/{len(tests)} passed')
    sys.exit(0 if fail == 0 else 1)
