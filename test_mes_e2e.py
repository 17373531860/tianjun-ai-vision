"""
端到端集成测试 (v2.7.16)

验证三个核心改动在真实 SQLite + 真实异步 worker 队列下的行为：
  - 改进 B：迟到扫码补绑（_handle_cycle_end 兜底分支）
  - B7 修复：mid_cycle + queue 模式下队列同步
  - clear_pending_scan：force=True 作废 / race lost 防御

跟 test_mes_late_bind.py（mock 单测）的区别：
  - 用临时 sqlite 文件 + 真实 ORM 模型 + 真实 SessionLocal
  - 真实异步 worker 线程消费 task_queue
  - 调用真实的 WorkpieceService（真写 DB）
  - clear_pending_scan(force=True) 真删 WorkpieceInspection 行
  - 不依赖 FastAPI 路由（避免 channel_manager / source.py 的复杂启动开销）

运行：
    python test_mes_e2e.py
"""
import os
import sys
import tempfile
import time
import threading

# ---- step 1: 隔离 DB —— 在 import 任何 backend 模块前先 patch 数据库 URI -----
_TMP_DB_FD, _TMP_DB_PATH = tempfile.mkstemp(prefix="tj_e2e_", suffix=".db")
os.close(_TMP_DB_FD)

os.environ["TJ_E2E_DB"] = _TMP_DB_PATH

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.core import config as _cfg  # noqa: E402

_cfg.settings.SQLALCHEMY_DATABASE_URI = f"sqlite:///{_TMP_DB_PATH}"

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.db import database as _db_module  # noqa: E402

_test_engine = create_engine(
    f"sqlite:///{_TMP_DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 15},
)
_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)

_db_module.engine = _test_engine
_db_module.SessionLocal = _TestSession

# ---- step 2: 注册所有 ORM 模型并 create_all 到临时 DB ---------------------
import backend.models.models  # noqa: E402,F401
import backend.models.mes_models  # noqa: E402,F401
from backend.db.database import Base  # noqa: E402

Base.metadata.create_all(bind=_test_engine)

# ---- step 3: import mes_hooks 并 patch 它绑住的 SessionLocal -------------
from backend.services import mes_hooks as _mh  # noqa: E402

_mh.SessionLocal = _TestSession

# ---- step 4: 准备最小的 scanner_service stub（让 _get_*_window 能拿配置） --
class _FakeConn:
    def __init__(self, channel_id, late_window=3, cooldown=0,
                 bind_timing="mid_cycle", duplicate_action="overwrite",
                 rebind_mode="rescan", broadcast_channels=None,
                 scan_required=False, warn_no_barcode=False):
        self.channel_id = channel_id
        self.late_scan_bind_window_sec = late_window
        self.ok_rescan_cooldown_sec = cooldown
        self.bind_timing = bind_timing
        self.duplicate_scan_action = duplicate_action
        self.rebind_mode = rebind_mode
        self.broadcast_channels = broadcast_channels or []
        self.scan_required = scan_required
        self.warn_no_barcode = warn_no_barcode


class _FakeScannerService:
    def __init__(self):
        self._connections = {}

    def add(self, conn):
        self._connections[len(self._connections)] = conn


_fake_scanner = _FakeScannerService()


def _stub_get_scanner_service():
    return _fake_scanner


from backend.services import scanner as _scanner_module  # noqa: E402
_scanner_module.get_scanner_service = _stub_get_scanner_service

# ---- step 5: 准备种子数据 ------------------------------------------------
from backend.models.models import (  # noqa: E402
    Project, DetectionSession, DetectionCycle,
)
from backend.models.mes_models import (  # noqa: E402
    Workpiece, WorkpieceInspection,
)

PROJECT_ID = None
SESSION_ID = None


def _seed():
    global PROJECT_ID, SESSION_ID
    db = _TestSession()
    try:
        proj = Project(name="E2E_PROJECT", task_type="detection")
        db.add(proj)
        db.flush()
        PROJECT_ID = proj.id

        sess = DetectionSession(
            session_uuid="e2e-session-1",
            project_id=proj.id,
            start_time=_now(),
        )
        db.add(sess)
        db.commit()
        SESSION_ID = sess.id
    finally:
        db.close()


def _now():
    from datetime import datetime
    return datetime.now()


def _make_cycle(start_offset_s=-2.0, end_offset_s=0.0, cycle_uuid=None):
    """在 DB 里直接创建一条 DetectionCycle（模拟 source.py 已经 commit 了）"""
    from datetime import datetime, timedelta
    db = _TestSession()
    try:
        now = datetime.now()
        cyc = DetectionCycle(
            cycle_uuid=cycle_uuid or f"e2e-cyc-{int(time.time()*1000)}",
            session_id=SESSION_ID,
            cycle_number=1,
            start_time=now + timedelta(seconds=start_offset_s),
            end_time=now + timedelta(seconds=end_offset_s),
            duration=abs(end_offset_s - start_offset_s),
            is_good=True,
        )
        db.add(cyc)
        db.commit()
        return cyc.id, cyc.start_time, cyc.end_time
    finally:
        db.close()


def _wait_worker_idle(hook, timeout=5.0, settle_ms=150):
    """等异步 worker 把队列里的 task 全消费完。

    注意：mes_hooks._worker_loop 不调 task_done()，所以不能用 queue.join()。
    退而求其次：轮询 empty() + settle_ms 静默期，确保最后一个 task 真正跑完。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if hook._task_queue.empty():
            time.sleep(settle_ms / 1000.0)
            if hook._task_queue.empty():
                return True
        time.sleep(0.01)
    return False


def _fresh_hook():
    """每个场景重新建一个 hook，避免状态串扰"""
    h = _mh.MESHookManager()
    h.start()
    return h


# =============== 测试场景 ===============================================

def test_scenario_a_baseline_mid_cycle_bind():
    """基线：scan 已到 → cycle_start → mid_cycle 立即绑定 → cycle_end 正常结算"""
    print("\n=== 场景 A: mid_cycle 正常绑定（baseline）===")
    _fake_scanner._connections.clear()
    _fake_scanner.add(_FakeConn(channel_id=0))

    h = _fresh_hook()
    try:
        cycle_id, _, _ = _make_cycle(start_offset_s=-1.0, end_offset_s=2.0)

        # source.py 的真实顺序：先 cycle_start，再 scan（mid_cycle 模式补绑）
        # 但这里我们模拟"扫码先到、随后周期开始"的常见情况
        h._enqueue(h._handle_scan, 0, "SN_A_001", "SN_A_001", PROJECT_ID, None)
        time.sleep(0.05)
        h._enqueue(h._handle_cycle_start, 0, cycle_id, SESSION_ID, PROJECT_ID)
        time.sleep(0.05)
        h._enqueue(h._handle_cycle_end, 0, cycle_id, True,
                   "OK_event", "ok", 3.0, [], PROJECT_ID)
        assert _wait_worker_idle(h), "worker 没空闲"

        db = _TestSession()
        try:
            wp = db.query(Workpiece).filter(
                Workpiece.serial_no == "SN_A_001"
            ).first()
            assert wp is not None, "工件没注册成功"
            assert wp.status == "ok", f"应为 ok 实际 {wp.status}"
            insp = db.query(WorkpieceInspection).filter(
                WorkpieceInspection.workpiece_id == wp.id,
                WorkpieceInspection.cycle_id == cycle_id,
            ).first()
            assert insp is not None, "WorkpieceInspection 应该存在"
            assert insp.result == "ok", f"insp.result 应为 ok 实际 {insp.result}"
        finally:
            db.close()
        print("[PASS] 场景 A: mid_cycle 正常绑定")
    finally:
        h.stop()


def test_scenario_b_late_scan_bind_in_window():
    """改进 B：cycle 已结束 + scan 在窗口内到达 → 迟到补绑生效"""
    print("\n=== 场景 B: 迟到扫码补绑（窗口内）===")
    _fake_scanner._connections.clear()
    _fake_scanner.add(_FakeConn(channel_id=0, late_window=3))

    h = _fresh_hook()
    try:
        # 关键：cycle 在 _make_cycle 时 end_time 已经是"刚刚结束"
        cycle_id, cyc_start, cyc_end = _make_cycle(
            start_offset_s=-3.0, end_offset_s=-0.5
        )

        # 模拟 worker 先消费 cycle_start（_pending 空 → 不绑）
        h._enqueue(h._handle_cycle_start, 0, cycle_id, SESSION_ID, PROJECT_ID)
        assert _wait_worker_idle(h)

        # 此时 _pending / _inspecting 都空
        assert 0 not in h._inspecting_workpiece
        assert 0 not in h._pending_workpiece

        # 紧接着扫码到达，但模拟"_get_current_cycle_id 返回 None"
        # (cycle 已经结束) → mid_cycle 跳过 → _pending 设
        h._get_current_cycle_id = lambda ch: None
        h._enqueue(h._handle_scan, 0, "SN_B_001", "SN_B_001", PROJECT_ID, None)
        assert _wait_worker_idle(h)

        assert 0 in h._pending_workpiece, "scan 应把 _pending 设上"
        assert 0 in h._last_scan_event, "scan 应记录 _last_scan_event"

        # cycle_end 触发 → 进入迟到补绑分支
        # 设 _get_current_cycle_id 让 mid_cycle 可以查到 session_id
        h._get_current_session_id = lambda ch: SESSION_ID
        h._enqueue(h._handle_cycle_end, 0, cycle_id, True,
                   "OK_event", "ok", 2.5, [], PROJECT_ID)
        assert _wait_worker_idle(h)

        db = _TestSession()
        try:
            wp = db.query(Workpiece).filter(
                Workpiece.serial_no == "SN_B_001"
            ).first()
            assert wp is not None
            assert wp.status == "ok", f"补绑后应为 ok，实际 {wp.status}"
            insp = db.query(WorkpieceInspection).filter(
                WorkpieceInspection.workpiece_id == wp.id,
                WorkpieceInspection.cycle_id == cycle_id,
            ).first()
            assert insp is not None, "迟到补绑应该创建 WorkpieceInspection"
            assert insp.result == "ok"
        finally:
            db.close()
        print("[PASS] 场景 B: 迟到补绑（窗口内）DB 落盘正确")
    finally:
        h.stop()


def test_scenario_c_late_scan_out_of_window():
    """改进 B 反向：scan 距 cycle_end 超过窗口 → 不补绑"""
    print("\n=== 场景 C: 迟到扫码超出窗口（不补绑）===")
    _fake_scanner._connections.clear()
    _fake_scanner.add(_FakeConn(channel_id=0, late_window=3))

    h = _fresh_hook()
    try:
        # cycle 结束于 30 秒前，超出 3 秒窗口
        cycle_id, _, _ = _make_cycle(
            start_offset_s=-32.0, end_offset_s=-30.0
        )

        h._get_current_cycle_id = lambda ch: None
        h._get_current_session_id = lambda ch: SESSION_ID
        h._enqueue(h._handle_scan, 0, "SN_C_001", "SN_C_001", PROJECT_ID, None)
        assert _wait_worker_idle(h)

        h._enqueue(h._handle_cycle_end, 0, cycle_id, True,
                   "OK_event", "ok", 2.0, [], PROJECT_ID)
        assert _wait_worker_idle(h)

        db = _TestSession()
        try:
            wp = db.query(Workpiece).filter(
                Workpiece.serial_no == "SN_C_001"
            ).first()
            assert wp is not None
            # 注意：新 wp 默认 status="registered"，不是 "queued"。
            # mark_inspecting 才会设 inspecting，set_result 才会设 ok/ng。
            # 超窗不补绑 = 不调 mark_inspecting → status 保持 register 时的初值。
            assert wp.status == "registered", \
                f"超窗不应补绑，应保持 registered，实际 {wp.status}"
            insp = db.query(WorkpieceInspection).filter(
                WorkpieceInspection.workpiece_id == wp.id
            ).first()
            assert insp is None, "超窗不应创建 WorkpieceInspection"
        finally:
            db.close()
        print("[PASS] 场景 C: 超窗不补绑，DB 状态正确")
    finally:
        h.stop()


def test_scenario_d_clear_force_cancels_inspecting():
    """clear_pending_scan(force=True) 真实作废：DB 里 wp.status 回退、insp 行被删"""
    print("\n=== 场景 D: clear force=True 作废 inspecting 工件 ===")
    _fake_scanner._connections.clear()
    _fake_scanner.add(_FakeConn(channel_id=0))

    h = _fresh_hook()
    try:
        cycle_id, _, _ = _make_cycle(start_offset_s=-1.0, end_offset_s=10.0)

        h._enqueue(h._handle_scan, 0, "SN_D_001", "SN_D_001", PROJECT_ID, None)
        time.sleep(0.05)
        h._enqueue(h._handle_cycle_start, 0, cycle_id, SESSION_ID, PROJECT_ID)
        assert _wait_worker_idle(h)

        # 此时应该已绑：_inspecting 有值，DB 里 wp.status=inspecting，insp 已建
        assert h._inspecting_workpiece.get(0) is not None
        db = _TestSession()
        try:
            wp = db.query(Workpiece).filter(
                Workpiece.serial_no == "SN_D_001"
            ).first()
            wp_id = wp.id
            assert wp.status == "inspecting"
            insp_before = db.query(WorkpieceInspection).filter(
                WorkpieceInspection.workpiece_id == wp_id,
                WorkpieceInspection.cycle_id == cycle_id,
            ).first()
            assert insp_before is not None, "cycle_start 应建 insp 行"
        finally:
            db.close()

        # 让 clear 路径能拿到当前 cycle_id 用于删 insp
        h._get_current_cycle_id = lambda ch: cycle_id

        cleared = h.clear_pending_scan(0, force=True)
        assert cleared.get("force_canceled_inspecting") is True, \
            f"应作废成功，cleared={cleared}"
        assert cleared.get("force_race_lost") is not True
        assert cleared.get("deleted_inspections") == 1, \
            f"应删一行 insp，实际 {cleared.get('deleted_inspections')}"

        # 验证 DB
        db = _TestSession()
        try:
            wp = db.query(Workpiece).filter(Workpiece.id == wp_id).first()
            assert wp.status == "queued", \
                f"force 作废后应回退 queued，实际 {wp.status}"
            insp_after = db.query(WorkpieceInspection).filter(
                WorkpieceInspection.workpiece_id == wp_id,
                WorkpieceInspection.cycle_id == cycle_id,
            ).first()
            assert insp_after is None, "WorkpieceInspection 应被删"
        finally:
            db.close()

        # 验证内存状态
        assert 0 not in h._inspecting_workpiece
        assert 0 not in h._pending_workpiece
        assert 0 not in h._last_scan_event

        print("[PASS] 场景 D: force 作废，DB + 内存状态都正确")
    finally:
        h.stop()


def test_scenario_e_clear_force_race_lost():
    """模拟竞态：HTTP 进入 force 路径前 worker 已经 pop 走 _inspecting → race lost"""
    print("\n=== 场景 E: clear force=True 竞态输给 worker（race lost）===")
    _fake_scanner._connections.clear()
    _fake_scanner.add(_FakeConn(channel_id=0))

    h = _fresh_hook()
    try:
        # 模拟：_inspecting 是空的（worker 已经 pop 走了，正在做 set_result）
        # 注意：不要预设 _inspecting
        assert 0 not in h._inspecting_workpiece

        cleared = h.clear_pending_scan(0, force=True)
        assert cleared.get("force_canceled_inspecting") is False, \
            f"应识别为 race lost，cleared={cleared}"
        assert cleared.get("force_race_lost") is True, \
            f"应标记 race_lost=True，cleared={cleared}"
        # 关键：DB 没动 → 工件不可能被 wp.status=queued 覆盖
        # （这里我们只能间接验证：cleared 里没有 deleted_inspections / workpiece_reverted_to）
        assert "deleted_inspections" not in cleared
        assert "workpiece_reverted_to" not in cleared

        print("[PASS] 场景 E: race lost 路径不动 DB，前端可提示用户")
    finally:
        h.stop()


def test_scenario_f_clear_no_force_keeps_inspecting():
    """clear_pending_scan(force=False) 不能动正在检测的工件，只清未绑定的扫码状态"""
    print("\n=== 场景 F: clear force=False 不动 inspecting，只清前置 ===")
    _fake_scanner._connections.clear()
    _fake_scanner.add(_FakeConn(channel_id=0))

    h = _fresh_hook()
    try:
        cycle_id, _, _ = _make_cycle(start_offset_s=-1.0, end_offset_s=10.0)

        h._enqueue(h._handle_scan, 0, "SN_F_001", "SN_F_001", PROJECT_ID, None)
        time.sleep(0.05)
        h._enqueue(h._handle_cycle_start, 0, cycle_id, SESSION_ID, PROJECT_ID)
        assert _wait_worker_idle(h)

        wp_id = h._inspecting_workpiece.get(0)
        assert wp_id is not None

        # 再扫一个 wp（_pending 又被设）
        h._enqueue(h._handle_scan, 0, "SN_F_002", "SN_F_002", PROJECT_ID, None)
        assert _wait_worker_idle(h)
        # mid_cycle 进入条件是 _inspecting 不在 → 这里 _inspecting 已有 wp_id，跳过
        # → SN_F_002 应进 _pending
        assert h._pending_workpiece.get(0) is not None
        assert h._pending_workpiece.get(0) != wp_id

        cleared = h.clear_pending_scan(0, force=False)
        # _pending 应被清，_inspecting 不动
        assert cleared.get("pending_workpiece_id") is not None
        assert cleared.get("inspecting_workpiece_id") == wp_id
        assert cleared.get("force_canceled_inspecting") is not True
        assert cleared.get("force_race_lost") is not True

        # DB 验证：wp_id 工件仍 inspecting，insp 仍在
        db = _TestSession()
        try:
            wp = db.query(Workpiece).filter(Workpiece.id == wp_id).first()
            assert wp.status == "inspecting", \
                f"force=False 不应改 inspecting，实际 {wp.status}"
            insp = db.query(WorkpieceInspection).filter(
                WorkpieceInspection.workpiece_id == wp_id,
                WorkpieceInspection.cycle_id == cycle_id,
            ).first()
            assert insp is not None
        finally:
            db.close()
        print("[PASS] 场景 F: force=False 只清前置，DB 不动")
    finally:
        h.stop()


# =============== main =================================================

def main():
    print(f"[E2E] 临时数据库：{_TMP_DB_PATH}")
    _seed()
    print(f"[E2E] 种子完成 project_id={PROJECT_ID} session_id={SESSION_ID}")

    failures = []
    for fn in [
        test_scenario_a_baseline_mid_cycle_bind,
        test_scenario_b_late_scan_bind_in_window,
        test_scenario_c_late_scan_out_of_window,
        test_scenario_d_clear_force_cancels_inspecting,
        test_scenario_e_clear_force_race_lost,
        test_scenario_f_clear_no_force_keeps_inspecting,
    ]:
        try:
            fn()
        except AssertionError as e:
            failures.append((fn.__name__, str(e)))
            print(f"[FAIL] {fn.__name__}: {e}")
        except Exception as e:
            import traceback
            failures.append((fn.__name__, repr(e)))
            print(f"[ERROR] {fn.__name__}: {e}")
            traceback.print_exc()

    print("\n" + "=" * 60)
    if not failures:
        print("PASS  全部 6 个端到端场景通过")
        rc = 0
    else:
        print(f"FAIL  {len(failures)}/6 失败:")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        rc = 1

    try:
        os.unlink(_TMP_DB_PATH)
    except Exception:
        pass

    sys.exit(rc)


if __name__ == "__main__":
    main()
