"""v3.23 ack 端点缺步骤挂起路由单测.

只验 _do_ack_pending 这一层: 挂起态下把 action/operator 正确透传给
mgr.resolve_step_remediation; 无挂起 / 无阻塞时的短路分支不在此覆盖
(resolve_step_remediation 本身行为见 test_ng_remediation_step_v323.py).
"""
import os
os.environ.setdefault("BACKEND_SKIP_INIT", "1")

from backend.api.source_routes import _do_ack_pending


class _FakeMgr:
    def __init__(self, pending_remediation=None):
        self._pending_ack = True
        self._pending_remediation = pending_remediation
        self.calls = []

    def resolve_step_remediation(self, action, operator=None):
        self.calls.append((action, operator))
        return {"resolved": True, "action": action}


def test_no_pending_ack_short_circuits():
    mgr = _FakeMgr()
    mgr._pending_ack = False
    res = _do_ack_pending(mgr, 0)
    assert res["acked"] is False
    assert mgr.calls == []


def test_remediation_routes_supplement_step():
    mgr = _FakeMgr(pending_remediation={"kind": "missing_step", "missing": ["B"]})
    res = _do_ack_pending(mgr, 0, action="supplement_step", operator="admin")
    assert res["acked"] is True
    assert res["remediation"]["action"] == "supplement_step"
    assert mgr.calls == [("supplement_step", "admin")]


def test_remediation_routes_confirm_ng():
    mgr = _FakeMgr(pending_remediation={"kind": "missing_step", "missing": ["B"]})
    res = _do_ack_pending(mgr, 0, action="confirm_ng", operator="sup")
    assert mgr.calls == [("confirm_ng", "sup")]


def test_remediation_defaults_to_redo_when_no_action():
    # 老"确认重做"按钮不带 action → 缺省 redo (保持语义)
    mgr = _FakeMgr(pending_remediation={"kind": "missing_step", "missing": ["B"]})
    res = _do_ack_pending(mgr, 0)
    assert mgr.calls == [("redo", None)]
    assert res["acked"] is True
