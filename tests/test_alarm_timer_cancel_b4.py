"""B4 报警定时器取消测试。

高 NG 率连续触发时, 关灯/过期定时器不应无限堆积; 新报警前取消旧 timer。
不开真实串口: 打桩 _send_command / _recompose_and_apply, 只验证 timer 引用管理。
"""
import threading
import time


def _make_manager():
    from backend.api.alarm import AlarmManager
    mgr = AlarmManager(config={"protocol": "modbus_4color"})
    # 打桩串口发送, 不碰硬件
    mgr._send_command = lambda *a, **k: None
    mgr._get_command = lambda *a, **k: b""
    return mgr


def test_b4_solo_off_timer_replaced_not_stacked():
    mgr = _make_manager()
    cfg = {"duration": 5, "color": "red", "effect": "on", "buzzer": False}

    mgr._trigger_alarm_solo("ng", cfg)
    first = mgr._solo_off_timer
    assert first is not None and first.is_alive()

    # 连续再触发多次: 每次都应取消旧 timer, 只留最新一个存活
    for _ in range(10):
        mgr._trigger_alarm_solo("ng", cfg)
    latest = mgr._solo_off_timer
    assert latest is not None and latest.is_alive()
    assert latest is not first
    # 旧的已被 cancel(不再存活)
    assert not first.is_alive()

    mgr.stop_alarm()
    assert mgr._solo_off_timer is None


def test_b4_shared_expire_timer_per_channel_replaced():
    mgr = _make_manager()
    mgr._recompose_and_apply = lambda *a, **k: None
    mgr.set_shared_mode([0, 1])

    cfg = {"duration": 5}
    mgr._trigger_alarm_shared("ng", cfg, channel_id=0)
    t0_first = mgr._shared_expire_timers.get(0)
    assert t0_first is not None and t0_first.is_alive()

    # 同通道连续触发: 旧 timer 被取消, 字典里只留最新
    for _ in range(8):
        mgr._trigger_alarm_shared("ng", cfg, channel_id=0)
    t0_latest = mgr._shared_expire_timers.get(0)
    assert t0_latest is not t0_first
    assert not t0_first.is_alive()
    assert t0_latest.is_alive()

    # 不同通道互不影响
    mgr._trigger_alarm_shared("ng", cfg, channel_id=1)
    assert mgr._shared_expire_timers.get(1) is not None

    mgr.stop_alarm()
    assert len(mgr._shared_expire_timers) == 0
