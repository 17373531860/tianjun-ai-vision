"""
v2.7.2 降工位清理逻辑验证脚本。

测试点：
  1. MESHookManager.on_channel_removed() 能清空指定 channel_id 在 6 个 dict 里的全部残留。
  2. ChannelManager.set_channel_count(1) 从多工位降到单工位时，
     会对每个被移除的 channel 调用 on_channel_removed()。
  3. AlarmRouter.on_channel_removed() 能停止报警线程/熄灭灯塔/断串口/移除 manager。
  4. ChannelManager 降工位时，AlarmRouter 对应通道也会被清理。

运行：
    cd <repo 根目录>
    python test_mes_hook_cleanup.py
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_CONFIG_FILE = os.path.join(ROOT, "backend", "data", "workstation_config.json")
_BACKUP_FILE = _CONFIG_FILE + ".test_bak"


def _backup_config():
    if os.path.exists(_CONFIG_FILE):
        shutil.copy2(_CONFIG_FILE, _BACKUP_FILE)


def _restore_config():
    if os.path.exists(_BACKUP_FILE):
        shutil.move(_BACKUP_FILE, _CONFIG_FILE)


def test_on_channel_removed_clears_all_dicts():
    """单元级：直接塞假数据进 6 个 dict，调用 on_channel_removed 后应全清干净。"""
    from backend.services.mes_hooks import MESHookManager

    m = MESHookManager()

    # 塞 channel 0,1,2 三条假数据到每个 dict
    for cid in (0, 1, 2):
        m._pending_workpiece[cid] = 1000 + cid
        m._pending_queue[cid] = [1000 + cid, 2000 + cid]
        m._active_orders[cid] = 5000 + cid
        m._inspecting_workpiece[cid] = 7000 + cid
        m._last_scan_event[cid] = {"serial_no": f"SN{cid}", "workpiece_id": 1000 + cid, "timestamp": 123.0}
        m._rebind_prompt[cid] = {"workpiece_id": 1000 + cid, "cycle_id": cid, "timestamp": 123.0}

    # 移除 ch1 和 ch2（模拟降到单工位）
    m.on_channel_removed(1)
    m.on_channel_removed(2)

    for d_name in (
        "_pending_workpiece", "_pending_queue", "_active_orders",
        "_inspecting_workpiece", "_last_scan_event", "_rebind_prompt",
    ):
        d = getattr(m, d_name)
        assert 1 not in d, f"[FAIL] {d_name} 还残留 ch1: {d}"
        assert 2 not in d, f"[FAIL] {d_name} 还残留 ch2: {d}"
        assert 0 in d, f"[FAIL] {d_name} 误删 ch0: {d}"

    print("[OK] test_on_channel_removed_clears_all_dicts 通过")


def test_channel_manager_downgrade_triggers_cleanup():
    """集成级：用 stub 替换 VideoSourceManager，验证 set_channel_count(1) 会触发 on_channel_removed。"""
    # 先 stub VideoSourceManager，避免真实摄像头/模型初始化
    import backend.api.source as source_mod

    class StubVSM:
        current_session_id = None

        def __init__(self, channel_id=0):
            self.channel_id = channel_id
            self._mes_hook = None

        def stop(self):
            pass

        def end_session(self):
            pass

    original_vsm = source_mod.VideoSourceManager
    source_mod.VideoSourceManager = StubVSM

    try:
        # 必须在 stub 生效之后 import
        from backend.api.channel_manager import ChannelManager
        from backend.services.mes_hooks import get_mes_hook

        cm = ChannelManager()
        # 直接调用 set_channel_count(4) 升到 4 工位
        cm.set_channel_count(4)
        assert set(cm.channels.keys()) == {0, 1, 2, 3}, f"升 4 工位后 channels={list(cm.channels)}"

        hook = get_mes_hook()
        # 在每个通道塞一条假的待检工件
        for cid in (0, 1, 2, 3):
            hook._pending_workpiece[cid] = 9000 + cid
            hook._last_scan_event[cid] = {"serial_no": f"X{cid}"}

        # 降到 1 工位
        cm.set_channel_count(1)
        assert set(cm.channels.keys()) == {0}, f"降 1 工位后 channels={list(cm.channels)}"

        assert 0 in hook._pending_workpiece, "[FAIL] ch0 被误清"
        for cid in (1, 2, 3):
            assert cid not in hook._pending_workpiece, f"[FAIL] ch{cid} 残留 pending_workpiece"
            assert cid not in hook._last_scan_event, f"[FAIL] ch{cid} 残留 last_scan_event"

        print("[OK] test_channel_manager_downgrade_triggers_cleanup 通过")
    finally:
        source_mod.VideoSourceManager = original_vsm


def test_downgrade_then_upgrade_new_channel_clean():
    """回归场景：4 工位 → 1 工位 → 2 工位，新建的 ch1 不应被老数据污染。"""
    import backend.api.source as source_mod

    class StubVSM:
        current_session_id = None

        def __init__(self, channel_id=0):
            self.channel_id = channel_id
            self._mes_hook = None

        def stop(self):
            pass

        def end_session(self):
            pass

    original_vsm = source_mod.VideoSourceManager
    source_mod.VideoSourceManager = StubVSM

    try:
        from backend.api.channel_manager import ChannelManager
        from backend.services.mes_hooks import get_mes_hook

        # 每个测试独立单例
        import backend.services.mes_hooks as mes_mod
        mes_mod._mes_hook_instance = None

        cm = ChannelManager()
        cm.set_channel_count(4)

        hook = get_mes_hook()
        # 老 ch1 有一条"已绑定工件但未检测"的脏数据
        hook._pending_workpiece[1] = 88888
        hook._last_scan_event[1] = {"serial_no": "OLD_BARCODE"}

        # 降到 1
        cm.set_channel_count(1)
        # 再升到 2
        cm.set_channel_count(2)

        assert 1 not in hook._pending_workpiece, (
            f"[FAIL] 升回 2 工位后，ch1 仍残留 pending_workpiece={hook._pending_workpiece.get(1)}"
        )
        assert 1 not in hook._last_scan_event, (
            f"[FAIL] 升回 2 工位后，ch1 仍残留 last_scan_event={hook._last_scan_event.get(1)}"
        )
        print("[OK] test_downgrade_then_upgrade_new_channel_clean 通过")
    finally:
        source_mod.VideoSourceManager = original_vsm


def test_alarm_router_on_channel_removed():
    """AlarmRouter 降工位清理：pop manager + stop_alarm + all_off + disconnect。"""
    from backend.api.alarm import AlarmRouter, AlarmManager

    router = AlarmRouter()
    # 模拟已有 ch0/ch1/ch2 三个 AlarmManager（未连真实串口）
    for cid in (0, 1, 2):
        mgr = AlarmManager()
        mgr._idle_light_active = True  # 模拟"工作灯已亮"残留
        router.managers[cid] = mgr

    router.on_channel_removed(1)
    router.on_channel_removed(2)

    assert 0 in router.managers, "[FAIL] ch0 被误删"
    assert 1 not in router.managers, f"[FAIL] ch1 未被移除: {list(router.managers)}"
    assert 2 not in router.managers, f"[FAIL] ch2 未被移除: {list(router.managers)}"
    # 不崩溃 = on_channel_removed 对"未连接串口"的 manager 也是安全的
    print("[OK] test_alarm_router_on_channel_removed 通过")


def test_channel_manager_downgrade_cleans_alarm():
    """集成：ChannelManager.set_channel_count(N→1) 降工位时，AlarmRouter 也会被清理。"""
    import backend.api.source as source_mod

    class StubVSM:
        current_session_id = None

        def __init__(self, channel_id=0):
            self.channel_id = channel_id
            self._mes_hook = None

        def stop(self):
            pass

        def end_session(self):
            pass

    original_vsm = source_mod.VideoSourceManager
    source_mod.VideoSourceManager = StubVSM

    try:
        from backend.api.channel_manager import ChannelManager
        from backend.api.alarm import alarm_router, AlarmManager

        cm = ChannelManager()
        cm.set_channel_count(4)

        # 预置 ch1/ch2/ch3 的 AlarmManager（模拟之前多工位已建立的实例）
        for cid in (1, 2, 3):
            if cid not in alarm_router.managers:
                alarm_router.managers[cid] = AlarmManager()

        cm.set_channel_count(1)

        assert 0 in alarm_router.managers, "[FAIL] ch0 报警器被误删"
        for cid in (1, 2, 3):
            assert cid not in alarm_router.managers, (
                f"[FAIL] ch{cid} 降工位后报警器未被清理: {list(alarm_router.managers)}"
            )
        print("[OK] test_channel_manager_downgrade_cleans_alarm 通过")
    finally:
        source_mod.VideoSourceManager = original_vsm


if __name__ == "__main__":
    _backup_config()
    try:
        test_on_channel_removed_clears_all_dicts()
        test_channel_manager_downgrade_triggers_cleanup()
        test_downgrade_then_upgrade_new_channel_clean()
        test_alarm_router_on_channel_removed()
        test_channel_manager_downgrade_cleans_alarm()
        print("\n全部通过 ✅")
    finally:
        _restore_config()
        print("[cleanup] workstation_config.json 已还原")
