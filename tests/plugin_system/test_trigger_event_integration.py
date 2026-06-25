"""trigger_event 端到端集成 — 插件平台 API → 主程序真事件响应面.

与 test_active_apis.py 的契约测试 (mock VSM) 不同, 这里用**真**的
EventTriggerMixin.fire_external_event_response 跑整条链路:

  插件 host.trigger_event(channel, event_id)
    → channel_manager.channels[channel].fire_external_event_response
    → 计数器联动 + 报警联动(_dispatch_event_alarm) + Toast/语音日志(events_log)
    → **不结算检测周期** (借用响应面)

这是 sensor-clean 三判定接入主程序事件体系的"地基验证": 证明插件只要触发对
event_id, 主程序原生链路就把报警/计数器/主页显示全办了, 插件无需碰这些。

(可见浏览器 UAT 需要主作者私钥签名的插件包才能在真实前端验签装载, 那步留主作者;
 本集成测试用真响应面逻辑等价覆盖了"事件→报警/计数器/Toast"的功能闭环。)
"""
from __future__ import annotations

from backend.api.source_event_trigger_mixin import EventTriggerMixin
from backend.plugin_system.registry import PluginHost


class _RealResponseVSM(EventTriggerMixin):
    """轻量壳 + 真 fire_external_event_response 逻辑 (响应面方法不打桩)."""

    def __init__(self):
        self.project_config = {
            "events_config": [
                {
                    "id": 2,
                    "name": "不良(NG)",
                    "show_notification": True,
                    "toast_id": "ng",
                    "actions": [{"counter_name": "工艺告警次数", "delta": 1}],
                }
            ]
        }
        self.counters = {"工艺告警次数": 0}
        self.events_log = []
        self._event_seq = 0
        self.alarm_called = []
        self.channel_id = 0
        self.end_cycle_called = False

    def _dispatch_event_alarm(self, eid):
        self.alarm_called.append(eid)

    def _persist_counters(self):
        pass

    def end_cycle(self, *a, **k):  # 不该被借用响应面触达
        self.end_cycle_called = True


def _mk_host():
    return PluginHost(
        customer_code="sensor-clean",
        plugin_dir="/tmp",
        main_version="3.24.0",
        capabilities=["runtime.event_trigger"],
    )


def test_plugin_trigger_event_links_main_program_response(monkeypatch):
    """三判定命中 → 经平台 API → 真响应面联动计数器+报警+Toast, 标 plugin 来源."""
    vsm = _RealResponseVSM()
    from backend.api import channel_manager as cm
    monkeypatch.setattr(cm.channel_manager, "channels", {0: vsm})

    host = _mk_host()
    ok = host.trigger_event(channel_id=0, event_id=2, reason="假擦拭")

    assert ok is True
    assert vsm.counters["工艺告警次数"] == 1            # 计数器联动 (主页显示靠 show_in_monitor)
    assert vsm.alarm_called == [2]                       # 报警联动 (event2 → 灯/蜂鸣)
    assert len(vsm.events_log) == 1                      # Toast/语音日志
    assert vsm.events_log[0]["source"] == "plugin:sensor-clean"
    assert vsm.events_log[0]["reason"] == "假擦拭"
    assert vsm.events_log[0]["event_name"] == "不良(NG)"


def test_plugin_trigger_event_does_not_settle_cycle(monkeypatch):
    """关键不变量: 借用响应面不结算周期 (过程告警不打断正在跑的检测)."""
    vsm = _RealResponseVSM()
    from backend.api import channel_manager as cm
    monkeypatch.setattr(cm.channel_manager, "channels", {0: vsm})

    _mk_host().trigger_event(channel_id=0, event_id=2, reason="操作员离开")
    assert vsm.end_cycle_called is False


def test_plugin_trigger_event_unknown_event_returns_false(monkeypatch):
    """事件 id 不在项目 events_config → 返 False, 不联动 (错误隔离, 不影响检测)."""
    vsm = _RealResponseVSM()
    from backend.api import channel_manager as cm
    monkeypatch.setattr(cm.channel_manager, "channels", {0: vsm})

    assert _mk_host().trigger_event(channel_id=0, event_id=999, reason="x") is False
    assert vsm.counters["工艺告警次数"] == 0
    assert vsm.alarm_called == []
