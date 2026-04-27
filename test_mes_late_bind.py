"""
v2.7.16 改进 B + B7 + clear_pending_scan 验证脚本。

覆盖：
  1. clear_pending_scan 能清 _pending_workpiece / _pending_queue /
     _last_scan_event / _rebind_prompt，且不动 _inspecting_workpiece。
  2. _handle_scan 的 mid_cycle 分支拿走 _pending_workpiece 时，
     同步从 _pending_queue remove 并把队列下一个塞回 _pending_workpiece (B7)。
  3. _handle_cycle_end 在 _inspecting 为空、_pending 已有待检工件、
     扫码事件时间戳落在 [cycle.start - W, cycle.end + W] 窗口内时，
     执行迟到补绑；否则不补绑保持旧行为。

运行：
    cd <repo 根目录>
    python test_mes_late_bind.py
"""

import os
import sys
import time
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 预加载所有 ORM 模型，避免 ScanLog 实例化时 SQLAlchemy 解析关系报错。
import backend.models.models  # noqa: E402,F401
import backend.models.mes_models  # noqa: E402,F401


def _new_hook(window=3, bind_timing="mid_cycle", duplicate_action="overwrite"):
    """造一个 MESHookManager 实例，stub 掉 scanner 配置 getter。"""
    from backend.services.mes_hooks import MESHookManager
    m = MESHookManager()
    m._get_late_bind_window = lambda ch: window
    m._get_bind_timing = lambda ch: bind_timing
    m._get_duplicate_scan_action = lambda ch: duplicate_action
    m._get_current_session_id = lambda ch: 100
    m._get_rebind_mode = lambda ch: "rescan"
    m._get_ok_rescan_cooldown = lambda ch: 0
    m._workpiece_svc = MagicMock()
    m._defect_svc = MagicMock()
    m._work_order_svc = MagicMock()
    m._work_order_svc.check_completion = MagicMock(return_value=False)
    return m


# =====================================================================
# 1. clear_pending_scan 三方面验证
# =====================================================================

def test_clear_pending_scan_full():
    m = _new_hook()
    ch = 0
    m._pending_workpiece[ch] = 1001
    m._pending_queue[ch] = [1001, 1002]
    m._last_scan_event[ch] = {"serial_no": "AB", "workpiece_id": 1001, "timestamp": 1.0}
    m._rebind_prompt[ch] = {"workpiece_id": 1001, "cycle_id": 5, "timestamp": 1.0}

    cleared = m.clear_pending_scan(ch)

    assert ch not in m._pending_workpiece, "_pending_workpiece 没清"
    assert ch not in m._pending_queue, "_pending_queue 没清"
    assert ch not in m._last_scan_event, "_last_scan_event 没清"
    assert ch not in m._rebind_prompt, "_rebind_prompt 没清"
    assert cleared.get("pending_workpiece_id") == 1001
    assert cleared.get("pending_queue") == [1001, 1002]
    print("[OK] test_clear_pending_scan_full")


def test_clear_pending_scan_keeps_inspecting():
    """已绑定到 cycle 的工件不会被 clear 动到。"""
    m = _new_hook()
    ch = 0
    m._inspecting_workpiece[ch] = 7777
    m._pending_workpiece[ch] = 1001

    cleared = m.clear_pending_scan(ch)

    assert m._inspecting_workpiece[ch] == 7777, "_inspecting 被错误清除"
    assert ch not in m._pending_workpiece, "_pending 应被清"
    assert cleared.get("inspecting_workpiece_id") == 7777
    print("[OK] test_clear_pending_scan_keeps_inspecting")


def test_clear_pending_scan_idempotent():
    """重复 clear 不会报错也不影响其它通道。"""
    m = _new_hook()
    m._pending_workpiece[1] = 9999
    cleared1 = m.clear_pending_scan(0)
    cleared2 = m.clear_pending_scan(0)
    assert cleared1 == {"inspecting_workpiece_id": None}
    assert cleared2 == {"inspecting_workpiece_id": None}
    assert m._pending_workpiece[1] == 9999, "误伤其它通道"
    print("[OK] test_clear_pending_scan_idempotent")


def test_clear_pending_scan_force_cancels_inspecting():
    """force=True 把 _inspecting 也清掉、回退 wp status、删 WorkpieceInspection。"""
    m = _new_hook()
    ch = 0
    m._inspecting_workpiece[ch] = 7777
    m._get_current_cycle_id = lambda c: 555 if c == ch else None

    # 用 fake DB session，验证调用了 delete + status 修改
    fake_db = MagicMock()
    deleted_count = 1
    fake_db.query.return_value.filter.return_value.delete.return_value = deleted_count
    fake_wp = MagicMock()
    fake_wp.status = "inspecting"
    fake_db.query.return_value.filter.return_value.first.return_value = fake_wp

    cleared = m.clear_pending_scan(ch, force=True, db=fake_db)

    assert ch not in m._inspecting_workpiece, "force=True 必须清 _inspecting"
    assert cleared.get("force_canceled_inspecting") is True
    assert cleared.get("inspecting_workpiece_id") == 7777
    assert cleared.get("deleted_inspections") == deleted_count
    assert cleared.get("workpiece_reverted_to") == "queued"
    assert fake_wp.status == "queued", "wp.status 必须回退到 queued"
    print("[OK] test_clear_pending_scan_force_cancels_inspecting")


def test_clear_pending_scan_force_no_inspecting_is_noop():
    """force=True 但本身没在检测，行为等同 force=False。"""
    m = _new_hook()
    ch = 0
    m._pending_workpiece[ch] = 1234
    cleared = m.clear_pending_scan(ch, force=True)
    assert ch not in m._pending_workpiece
    # force 路径的 pop 拿到 None 时算 race_lost；这里业务上是"原本就没 inspecting"，
    # 不是真的 race，但目前实现没区分（都是 pop 拿到 None）。语义上等价：操作没作废到任何东西。
    assert cleared.get("force_canceled_inspecting") is False
    assert cleared.get("force_race_lost") is True
    print("[OK] test_clear_pending_scan_force_no_inspecting_is_noop")


def test_clear_pending_scan_force_race_lost():
    """模拟并发竞态：HTTP 进入 force 路径前，worker 已经 pop 走了 _inspecting。
    HTTP 必须不能再回退 wp.status，避免覆盖 worker 的 set_result(ok/ng)。"""
    m = _new_hook()
    ch = 0
    # 不预设 _inspecting → 模拟 worker 已经 pop 走的状态
    fake_db = MagicMock()
    cleared = m.clear_pending_scan(ch, force=True, db=fake_db)

    assert cleared.get("force_canceled_inspecting") is False, "race 输了不能算作废成功"
    assert cleared.get("force_race_lost") is True
    # 关键：DB 操作必须没被调用（不能误删 inspection 也不能改 wp.status）
    fake_db.query.assert_not_called()
    fake_db.commit.assert_not_called()
    print("[OK] test_clear_pending_scan_force_race_lost")


# =====================================================================
# 2. mid_cycle 分支同步清 _pending_queue (B7)
# =====================================================================

def test_mid_cycle_queue_sync_b7():
    """duplicate=queue + bind_timing=mid_cycle，scan 触发 mid_cycle 绑定时
    应同时把 wp_id 从队列里 remove，并把队列下一个塞回 _pending_workpiece。"""
    m = _new_hook(bind_timing="mid_cycle", duplicate_action="queue")
    ch = 0
    # 模拟 cycle 已活跃
    m._get_current_cycle_id = lambda c: 555 if c == ch else None

    fake_wp_first = MagicMock()
    fake_wp_first.id = 100
    fake_wp_second = MagicMock()
    fake_wp_second.id = 101

    m._workpiece_svc.register = MagicMock(side_effect=[fake_wp_first, fake_wp_second])

    # 第一次扫码 → mid_cycle 分支立即绑 wp 100，队列剩空
    m._handle_scan(MagicMock(), ch, "SN1", "raw1", project_id=1)
    assert m._inspecting_workpiece[ch] == 100, f"首次 mid_cycle 没绑: {m._inspecting_workpiece}"
    assert ch not in m._pending_workpiece, "首次绑后 _pending 应清"
    assert m._pending_queue.get(ch, []) == [], f"队列残留: {m._pending_queue}"

    # 第二次扫码 → 因为已 inspecting, mid_cycle 不再触发；
    # 但 queue 模式仍会 append，并把队列头放进 _pending
    m._handle_scan(MagicMock(), ch, "SN2", "raw2", project_id=1)
    assert m._pending_workpiece[ch] == 101, f"第二次扫码应进 _pending: {m._pending_workpiece}"
    assert m._pending_queue[ch] == [101], f"队列应只有第二条: {m._pending_queue}"

    print("[OK] test_mid_cycle_queue_sync_b7")


def test_mid_cycle_with_queue_three_scans():
    """连扫三个码：第一个 mid_cycle 立即绑，剩下两个进队列且互不踩。"""
    m = _new_hook(bind_timing="mid_cycle", duplicate_action="queue")
    ch = 0
    m._get_current_cycle_id = lambda c: 555 if c == ch else None

    wps = []
    for i in range(3):
        w = MagicMock()
        w.id = 200 + i
        wps.append(w)
    m._workpiece_svc.register = MagicMock(side_effect=wps)

    for i in range(3):
        m._handle_scan(MagicMock(), ch, f"SN{i}", f"raw{i}", project_id=1)

    assert m._inspecting_workpiece[ch] == 200, "首件应在 inspecting"
    assert m._pending_queue[ch] == [201, 202], f"队列应剩 [201,202]: {m._pending_queue}"
    assert m._pending_workpiece[ch] == 201, f"_pending 应是队首 201: {m._pending_workpiece}"
    print("[OK] test_mid_cycle_with_queue_three_scans")


# =====================================================================
# 3. 迟到补绑（改进 B 核心）
# =====================================================================

def _make_fake_cycle(cycle_id, start_ts, end_ts):
    from datetime import datetime
    cyc = MagicMock()
    cyc.id = cycle_id
    cyc.start_time = datetime.fromtimestamp(start_ts)
    cyc.end_time = datetime.fromtimestamp(end_ts)
    return cyc


def _stub_db_with_cycle(cycle):
    """造个假 db，db.query(DetectionCycle).filter(...).first() 返回指定 cycle。"""
    db = MagicMock()
    q = MagicMock()
    q.first.return_value = cycle
    f = MagicMock()
    f.first.return_value = cycle
    q.filter.return_value = f
    db.query.return_value = q
    return db


def test_late_bind_in_window():
    """scan 在 cycle_end 之后 1.5s（窗口 3s 内）→ 补绑成功。"""
    m = _new_hook(window=3)
    ch = 0
    cycle_id = 999
    start_ts = time.time() - 10
    end_ts = time.time() - 1.5
    cyc = _make_fake_cycle(cycle_id, start_ts, end_ts)
    db = _stub_db_with_cycle(cyc)

    m._pending_workpiece[ch] = 1234
    m._last_scan_event[ch] = {"serial_no": "X", "workpiece_id": 1234, "timestamp": time.time()}
    # 不准 inspecting

    m._handle_cycle_end(
        db, channel_id=ch, cycle_id=cycle_id,
        is_good=True, event_name="OK", result_reason=None,
        duration=8.5, step_sequence=[], project_id=1,
    )

    m._workpiece_svc.mark_inspecting.assert_called_once_with(db, 1234)
    m._workpiece_svc.link_to_cycle.assert_called_once()
    m._workpiece_svc.set_result.assert_called_once_with(db, 1234, True, cycle_id)
    assert ch not in m._pending_workpiece, "补绑后 _pending 应清"
    print("[OK] test_late_bind_in_window")


def test_late_bind_out_of_window():
    """scan 在 cycle_end 之后 10s（超出窗口 3s）→ 不补绑，回到旧行为。"""
    m = _new_hook(window=3)
    ch = 0
    cycle_id = 999
    end_ts = time.time() - 10
    cyc = _make_fake_cycle(cycle_id, end_ts - 5, end_ts)
    db = _stub_db_with_cycle(cyc)

    m._pending_workpiece[ch] = 1234
    m._last_scan_event[ch] = {"serial_no": "X", "workpiece_id": 1234, "timestamp": time.time()}

    m._handle_cycle_end(
        db, channel_id=ch, cycle_id=cycle_id,
        is_good=True, event_name="OK", result_reason=None,
        duration=5.0, step_sequence=[], project_id=1,
    )

    m._workpiece_svc.mark_inspecting.assert_not_called()
    m._workpiece_svc.set_result.assert_not_called()
    assert m._pending_workpiece[ch] == 1234, "未补绑则 _pending 保留"
    print("[OK] test_late_bind_out_of_window")


def test_late_bind_disabled_when_window_zero():
    """window=0 → 直接走旧行为（不补绑）。"""
    m = _new_hook(window=0)
    ch = 0
    cycle_id = 999
    cyc = _make_fake_cycle(cycle_id, time.time() - 5, time.time() - 0.1)
    db = _stub_db_with_cycle(cyc)
    m._pending_workpiece[ch] = 1234
    m._last_scan_event[ch] = {"serial_no": "X", "workpiece_id": 1234, "timestamp": time.time()}

    m._handle_cycle_end(
        db, channel_id=ch, cycle_id=cycle_id,
        is_good=True, event_name="OK", result_reason=None,
        duration=5.0, step_sequence=[], project_id=1,
    )

    m._workpiece_svc.mark_inspecting.assert_not_called()
    assert m._pending_workpiece[ch] == 1234
    print("[OK] test_late_bind_disabled_when_window_zero")


def test_late_bind_scan_before_cycle_start_within_window():
    """scan 早于 cycle_start 1s（窗口 3s 内）→ 也接受。覆盖 worker queue 微竞争场景。"""
    m = _new_hook(window=3)
    ch = 0
    cycle_id = 888
    now = time.time()
    cyc = _make_fake_cycle(cycle_id, start_ts=now - 5, end_ts=now - 0.1)
    db = _stub_db_with_cycle(cyc)
    m._pending_workpiece[ch] = 4321
    m._last_scan_event[ch] = {"serial_no": "Y", "workpiece_id": 4321, "timestamp": now - 6}

    m._handle_cycle_end(
        db, channel_id=ch, cycle_id=cycle_id,
        is_good=False, event_name="NG", result_reason="missing",
        duration=4.9, step_sequence=[], project_id=1,
    )

    m._workpiece_svc.mark_inspecting.assert_called_once_with(db, 4321)
    m._workpiece_svc.set_result.assert_called_once_with(db, 4321, False, cycle_id)
    print("[OK] test_late_bind_scan_before_cycle_start_within_window")


def test_late_bind_skip_when_scan_event_workpiece_mismatch():
    """_pending 是 wp_a 但 _last_scan_event 是 wp_b → 不补绑（避免老 scan_event 误命中）。"""
    m = _new_hook(window=3)
    ch = 0
    cycle_id = 777
    cyc = _make_fake_cycle(cycle_id, time.time() - 3, time.time() - 0.1)
    db = _stub_db_with_cycle(cyc)
    m._pending_workpiece[ch] = 5555  # 当前待检
    m._last_scan_event[ch] = {"serial_no": "OLD", "workpiece_id": 9999, "timestamp": time.time()}  # 老事件

    m._handle_cycle_end(
        db, channel_id=ch, cycle_id=cycle_id,
        is_good=True, event_name="OK", result_reason=None,
        duration=2.9, step_sequence=[], project_id=1,
    )

    m._workpiece_svc.mark_inspecting.assert_not_called()
    assert m._pending_workpiece[ch] == 5555
    print("[OK] test_late_bind_skip_when_scan_event_workpiece_mismatch")


# =====================================================================

ALL_TESTS = [
    test_clear_pending_scan_full,
    test_clear_pending_scan_keeps_inspecting,
    test_clear_pending_scan_idempotent,
    test_clear_pending_scan_force_cancels_inspecting,
    test_clear_pending_scan_force_no_inspecting_is_noop,
    test_mid_cycle_queue_sync_b7,
    test_mid_cycle_with_queue_three_scans,
    test_late_bind_in_window,
    test_late_bind_out_of_window,
    test_late_bind_disabled_when_window_zero,
    test_late_bind_scan_before_cycle_start_within_window,
    test_late_bind_skip_when_scan_event_workpiece_mismatch,
]


def main():
    failed = []
    for t in ALL_TESTS:
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            import traceback
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"[ERROR] {t.__name__}: {e}\n{traceback.format_exc()}")

    print()
    print("=" * 60)
    if failed:
        print(f"FAIL  {len(failed)}/{len(ALL_TESTS)} 用例不通过")
        for n, e in failed:
            print(f"  - {n}: {e}")
        sys.exit(1)
    else:
        print(f"PASS  全部 {len(ALL_TESTS)} 用例通过")


if __name__ == "__main__":
    main()
