"""VSM 轻量事件响应 fire_external_event_response 契约 (v3.21 M6).

外部子系统 (包装结算) 借用项目事件的"响应面"——报警 + 语音/Toast + 计数,
但**不结束检测周期**. 这里用最小 fake host 钉死契约, 不需起真 VideoSourceManager.
"""
from backend.api.source_event_trigger_mixin import EventTriggerMixin


class _FakeVSM(EventTriggerMixin):
    def __init__(self):
        self.project_config = {
            "events_config": [
                {
                    "id": 3,
                    "name": "漏箱报警",
                    "show_notification": True,
                    "toast_id": "ng",
                    "actions": [{"counter_name": "异常计数", "delta": 1}],
                }
            ]
        }
        self.counters = {"异常计数": 0}
        self.events_log = []
        self._event_seq = 0
        self.alarm_called = []
        # 若被误调到周期结算路径, 这两个标记会被翻动 → 用于反向断言"没碰周期"
        self.end_cycle_called = False

    def _dispatch_event_alarm(self, eid):
        self.alarm_called.append(eid)

    def _persist_counters(self):
        pass

    def end_cycle(self, *a, **k):  # 不该被轻量响应触达
        self.end_cycle_called = True


def test_fire_external_event_links_alarm_counter_log():
    vsm = _FakeVSM()
    ok = vsm.fire_external_event_response(3, "第2箱漏箱", source="packaging")
    assert ok is True
    assert vsm.counters["异常计数"] == 1          # 计数联动
    assert vsm.alarm_called == [3]                # 报警联动 (eventN)
    assert len(vsm.events_log) == 1               # Toast/语音日志
    log = vsm.events_log[0]
    assert log["source"] == "packaging"
    assert log["event_name"] == "漏箱报警"
    assert log["show_notification"] is True
    assert log["reason"] == "第2箱漏箱"


def test_fire_external_event_does_not_settle_cycle():
    """关键: 借用响应面绝不调 end_cycle, 不打断正在跑的托盘检测."""
    vsm = _FakeVSM()
    vsm.fire_external_event_response(3, "x", source="packaging")
    assert vsm.end_cycle_called is False


def test_fire_external_event_unknown_id_returns_false():
    vsm = _FakeVSM()
    assert vsm.fire_external_event_response(999, "找不到的事件") is False
    assert vsm.alarm_called == []
    assert vsm.events_log == []


def test_fire_external_event_no_project_config_returns_false():
    vsm = _FakeVSM()
    vsm.project_config = None
    assert vsm.fire_external_event_response(3, "x") is False


def test_fire_external_event_default_does_not_freeze():
    """默认事件 (未标 require_ack) 外部触发只报警不进阻塞态 — 改前行为字节级保持."""
    vsm = _FakeVSM()
    vsm._pending_ack = False
    vsm.fire_external_event_response(3, "漏箱", source="packaging")
    assert vsm._pending_ack is False
    assert vsm.events_log[0]["require_ack"] is False


def test_fire_external_event_require_ack_enters_block():
    """v3.22: 事件标了'需人工确认' → 外部 (包装漏箱/多装) 触发也进入阻塞态."""
    vsm = _FakeVSM()
    vsm.channel_id = 0
    vsm._pending_ack = False
    vsm.project_config["events_config"][0]["require_ack"] = True
    vsm.project_config["events_config"][0]["ack_timeout_sec"] = 0
    ok = vsm.fire_external_event_response(3, "第2箱漏箱", source="packaging")
    assert ok is True
    assert vsm._pending_ack is True                       # 进入定格
    assert vsm._pending_ack_event_id == "3"
    assert vsm._pending_ack_event_name == "漏箱报警"
    assert vsm.events_log[0]["require_ack"] is True


def test_fire_external_event_require_ack_no_double_reset():
    """已在阻塞态时再触发 → 不刷掉首个原因 (保留最先的人工确认事件)."""
    vsm = _FakeVSM()
    vsm.channel_id = 0
    vsm._pending_ack = True
    vsm._pending_ack_event_id = "first"
    vsm.project_config["events_config"][0]["require_ack"] = True
    vsm.fire_external_event_response(3, "后续异常", source="packaging")
    assert vsm._pending_ack_event_id == "first"           # 没被覆盖
