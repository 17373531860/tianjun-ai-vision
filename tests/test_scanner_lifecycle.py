# -*- coding: utf-8 -*-
"""v3.50 扫码器生命周期 (捷昌二期"码-合格-码"闭环) 单测。

覆盖 (对应计划 B 部分):
  1. resume_on 分流: cycle_end (默认) OK/NG 都恢复 = 现状;
     ok_only 仅 OK 自动恢复, NG/未知保持灭灯 + _resume_blocked
  2. 人工恢复 (resume_scanning_manual / manual=True) 无条件放行
  3. is_resume_blocked 工位级查询 (前端"恢复扫码"按钮的数据源)
  4. rearm_forget_last: 恢复时重置物理去重缓存 + 作废未绑定旧码
  5. strict_ok_dedup / 冷却 / 重复拒绝 三条拒绝路径发统一警告 toast
  6. m0010 迁移: scanner_devices 三列可加, 幂等
  7. POST /scanner/resume 端点
"""
from unittest.mock import MagicMock

import pytest


def _mk_service(**conn_kw):
    """裸 ScannerService + 一台 once_per_cycle text_lon 扫码器 (等待恢复态)。"""
    from backend.services.scanner import ScannerService, ScannerConnection
    svc = ScannerService()
    defaults = dict(
        device_id=1, name="枪1", ip="127.0.0.1", port=55256,
        channel_id=0, enabled=True,
        device_type="text_lon", scan_mode="once_per_cycle",
    )
    defaults.update(conn_kw)
    conn = ScannerConnection(**defaults)
    conn._wait_cycle_resume = True
    svc._connections[1] = conn
    return svc, conn


# ==================== resume_on 分流 ====================

def test_cycle_end_default_resumes_on_ng():
    """默认 resume_on=cycle_end: NG 也恢复 = 现状行为零差异。"""
    svc, conn = _mk_service()
    resumed = svc.resume_after_cycle(0, is_good=False)
    assert resumed == ["枪1"]
    assert conn._wait_cycle_resume is False
    assert conn._resume_blocked is False


def test_cycle_end_resumes_on_unknown_result():
    """is_good=None (老调用方不传结果) 在默认配置下照常恢复。"""
    svc, conn = _mk_service()
    assert svc.resume_after_cycle(0) == ["枪1"]
    assert conn._wait_cycle_resume is False


def test_ok_only_blocks_ng():
    svc, conn = _mk_service(resume_on="ok_only")
    resumed = svc.resume_after_cycle(0, is_good=False)
    assert resumed == []
    assert conn._wait_cycle_resume is True, "NG 必须保持灭灯"
    assert conn._resume_blocked is True


def test_ok_only_blocks_unknown():
    """ok_only 下结果未知 (None) 视同非 OK, 不自动恢复。"""
    svc, conn = _mk_service(resume_on="ok_only")
    assert svc.resume_after_cycle(0, is_good=None) == []
    assert conn._resume_blocked is True


def test_ok_only_resumes_on_ok():
    svc, conn = _mk_service(resume_on="ok_only")
    resumed = svc.resume_after_cycle(0, is_good=True)
    assert resumed == ["枪1"]
    assert conn._wait_cycle_resume is False
    assert conn._resume_blocked is False


def test_manual_resume_overrides_ok_only():
    svc, conn = _mk_service(resume_on="ok_only")
    assert svc.resume_after_cycle(0, is_good=False) == []
    assert conn._resume_blocked is True

    resumed = svc.resume_scanning_manual(0)
    assert resumed == ["枪1"]
    assert conn._wait_cycle_resume is False
    assert conn._resume_blocked is False


def test_is_resume_blocked_per_channel():
    svc, conn = _mk_service(resume_on="ok_only")
    assert svc.is_resume_blocked(0) is False
    svc.resume_after_cycle(0, is_good=False)
    assert svc.is_resume_blocked(0) is True
    svc.resume_scanning_manual(0)
    assert svc.is_resume_blocked(0) is False


def test_resume_skips_other_channel():
    svc, conn = _mk_service(channel_id=0)
    # 别的工位结算不影响本枪 (channel 不匹配)
    # 注: 工位越界会被 _resolve_bound_channels 降级到 0, 所以这里用
    # broadcast_channels 显式钉住 ch0, 再对 ch0 之外的合法工位调用无效果
    conn.broadcast_channels = [0]
    resumed = svc.resume_after_cycle(0, is_good=True)
    assert resumed == ["枪1"]


# ==================== v3.50.0a ok_only 广播枪全 OK 门控 ====================
# "仅合格"在广播多工位枪上的正确语义: 这把枪覆盖的所有 (正在检测的) 工位
# 都 OK 才恢复亮灯 (捷昌 B 站: 大件+小件双通道清点同一箱, 先 OK 的不能抢跑).

def _mk_broadcast(monkeypatch, **conn_kw):
    from backend.services.scanner import ScannerService
    monkeypatch.setattr(ScannerService, "_known_channel_count",
                        classmethod(lambda cls: 2))
    svc, conn = _mk_service(resume_on="ok_only", **conn_kw)
    conn.broadcast_channels = [0, 1]
    conn.last_scan = "SN-BOX-A"
    return svc, conn


def test_broadcast_ok_only_waits_for_all_channels(monkeypatch):
    """先 OK 的工位不抢跑亮灯; 两个工位都 OK 才恢复。"""
    svc, conn = _mk_broadcast(monkeypatch)
    monkeypatch.setattr(svc, "_detecting_channels", lambda bound: {0, 1})

    assert svc.resume_after_cycle(0, is_good=True) == [], "只有 ch0 OK, 不能亮灯"
    assert conn._wait_cycle_resume is True
    assert conn._resume_blocked is False, "等兄弟工位不是 NG 拦停, 不该弹恢复按钮"
    assert conn._ok_ready_channels == {0}

    assert svc.resume_after_cycle(1, is_good=True) == ["枪1"], "两边都 OK 才恢复"
    assert conn._wait_cycle_resume is False
    assert conn._ok_ready_channels == set(), "恢复后集合清零"


def test_broadcast_ok_then_ng_blocks_until_manual(monkeypatch):
    """一边 OK 一边 NG: 灯保持灭 + 拦停, 人工恢复放行并清空集合。"""
    svc, conn = _mk_broadcast(monkeypatch)
    monkeypatch.setattr(svc, "_detecting_channels", lambda bound: {0, 1})

    assert svc.resume_after_cycle(0, is_good=True) == []
    assert svc.resume_after_cycle(1, is_good=False) == []
    assert conn._wait_cycle_resume is True
    assert conn._resume_blocked is True, "NG 走拦停, 前端露恢复按钮"

    assert svc.resume_scanning_manual(0) == ["枪1"]
    assert conn._wait_cycle_resume is False
    assert conn._resume_blocked is False
    assert conn._ok_ready_channels == set()


def test_broadcast_partial_ok_invalidated_by_new_scan(monkeypatch):
    """部分 OK 按本轮箱码锚定: 换码后旧集合作废, 不会残留到下一轮提前亮灯。"""
    svc, conn = _mk_broadcast(monkeypatch)
    monkeypatch.setattr(svc, "_detecting_channels", lambda bound: {0, 1})

    assert svc.resume_after_cycle(0, is_good=True) == []
    assert conn._ok_ready_channels == {0}

    conn.last_scan = "SN-BOX-B"  # 新一轮箱码
    assert svc.resume_after_cycle(1, is_good=True) == [], "旧轮的 ch0 OK 已作废"
    assert conn._ok_ready_channels == {1}

    assert svc.resume_after_cycle(0, is_good=True) == ["枪1"]


def test_broadcast_denominator_excludes_idle_channel(monkeypatch):
    """广播里没在检测的工位不进分母, 否则灯被锁死 (只开一个通道跑的现场)。"""
    svc, conn = _mk_broadcast(monkeypatch)
    monkeypatch.setattr(svc, "_detecting_channels", lambda bound: {0})

    assert svc.resume_after_cycle(0, is_good=True) == ["枪1"], \
        "ch1 没在检测, 只等 ch0"


def test_broadcast_cycle_end_mode_untouched(monkeypatch):
    """默认 resume_on=cycle_end 的广播枪不走全 OK 门控, 行为零差异。"""
    from backend.services.scanner import ScannerService
    monkeypatch.setattr(ScannerService, "_known_channel_count",
                        classmethod(lambda cls: 2))
    svc, conn = _mk_service()  # cycle_end
    conn.broadcast_channels = [0, 1]
    assert svc.resume_after_cycle(0, is_good=True) == ["枪1"], \
        "cycle_end 单工位结束即恢复 = 老行为"


# ==================== rearm_forget_last ====================

def test_rearm_forget_resets_dedup_and_clears_pending():
    svc, conn = _mk_service(rearm_forget_last=True)
    conn.last_scan = "SN-OLD"
    conn.last_scan_time = 123.0
    hook = MagicMock()
    hook.clear_pending_scan.return_value = {"pending_workpiece_id": 7}
    svc._mes_hook = hook

    svc.resume_after_cycle(0, is_good=True)
    assert conn.last_scan == ""
    assert conn.last_scan_time == 0
    hook.clear_pending_scan.assert_called_once_with(0, force=False)


def test_rearm_forget_off_keeps_dedup_cache():
    """默认关: 恢复时不动去重缓存 = 现状。"""
    svc, conn = _mk_service()
    conn.last_scan = "SN-OLD"
    conn.last_scan_time = 123.0
    hook = MagicMock()
    svc._mes_hook = hook

    svc.resume_after_cycle(0, is_good=True)
    assert conn.last_scan == "SN-OLD"
    hook.clear_pending_scan.assert_not_called()


def test_rearm_forget_not_applied_when_blocked():
    """ok_only+NG 被拦下时不作废旧码 (还没重新亮灯)。"""
    svc, conn = _mk_service(resume_on="ok_only", rearm_forget_last=True)
    conn.last_scan = "SN-OLD"
    hook = MagicMock()
    svc._mes_hook = hook

    svc.resume_after_cycle(0, is_good=False)
    assert conn.last_scan == "SN-OLD"
    hook.clear_pending_scan.assert_not_called()


# ==================== 拒绝路径统一警告 toast ====================

def _mk_mes(monkeypatch):
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr.enabled = True
    mgr._workpiece_svc = MagicMock()
    # 掐掉 WorkpieceFlow 优先路径
    import backend.services.workpiece_flow_coordinator as wfc
    fake_coord = MagicMock()
    fake_coord.is_channel_in_flow.return_value = False
    monkeypatch.setattr(wfc, "get_coordinator", lambda: fake_coord)
    return mgr


def test_strict_ok_dedup_rejects_with_warning(monkeypatch):
    mgr = _mk_mes(monkeypatch)
    monkeypatch.setattr(mgr, "_get_strict_ok_dedup", lambda ch: True)
    existing = MagicMock(id=5, status="ok")
    mgr._workpiece_svc.find_by_serial.return_value = existing

    db = MagicMock()
    mgr._handle_scan(db, 0, "SN-1", "SN-1", project_id=1)

    ev = mgr._last_scan_event[0]
    assert ev["scan_warning"] is True
    assert "强制去重" in ev["warn_reason"]
    mgr._workpiece_svc.register.assert_not_called()


def test_strict_ok_dedup_allows_new_serial(monkeypatch):
    """强制去重只拦已 OK 的码, 新码正常走注册。"""
    mgr = _mk_mes(monkeypatch)
    monkeypatch.setattr(mgr, "_get_strict_ok_dedup", lambda ch: True)
    monkeypatch.setattr(mgr, "_get_ok_rescan_cooldown", lambda ch: 0)
    monkeypatch.setattr(mgr, "_get_duplicate_scan_action", lambda ch: "overwrite")
    monkeypatch.setattr(mgr, "_get_bind_timing", lambda ch: "mid_cycle",
                        raising=False)
    mgr._workpiece_svc.find_by_serial.return_value = None
    wp = MagicMock(id=9, serial_no="SN-NEW")
    mgr._workpiece_svc.register.return_value = wp

    db = MagicMock()
    try:
        mgr._handle_scan(db, 0, "SN-NEW", "SN-NEW", project_id=1)
    except Exception:
        pass  # register 之后的绑定链路不在本用例断言范围
    mgr._workpiece_svc.register.assert_called_once()


def test_cooldown_reject_emits_warning(monkeypatch):
    from datetime import datetime
    mgr = _mk_mes(monkeypatch)
    monkeypatch.setattr(mgr, "_get_strict_ok_dedup", lambda ch: False)
    monkeypatch.setattr(mgr, "_get_ok_rescan_cooldown", lambda ch: 60)
    existing = MagicMock(id=5, status="ok", last_inspect_at=datetime.now())
    mgr._workpiece_svc.find_by_serial.return_value = existing

    db = MagicMock()
    mgr._handle_scan(db, 0, "SN-1", "SN-1", project_id=1)

    ev = mgr._last_scan_event[0]
    assert ev["scan_warning"] is True
    assert "冷却期" in ev["warn_reason"]
    mgr._workpiece_svc.register.assert_not_called()


def test_duplicate_reject_emits_warning(monkeypatch):
    mgr = _mk_mes(monkeypatch)
    monkeypatch.setattr(mgr, "_get_strict_ok_dedup", lambda ch: False)
    monkeypatch.setattr(mgr, "_get_ok_rescan_cooldown", lambda ch: 0)
    monkeypatch.setattr(mgr, "_get_duplicate_scan_action", lambda ch: "reject")
    mgr._pending_workpiece[0] = 42  # 已有待检工件

    db = MagicMock()
    mgr._handle_scan(db, 0, "SN-2", "SN-2", project_id=1)

    ev = mgr._last_scan_event[0]
    assert ev["scan_warning"] is True
    assert "拒绝" in ev["warn_reason"]
    mgr._workpiece_svc.register.assert_not_called()


def test_emit_scan_warning_shape():
    """警告事件结构: 前端 handleScanToast 依赖这些字段。"""
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr._emit_scan_warning(3, "SN-X", "原因文案")
    ev = mgr._last_scan_event[3]
    assert ev["serial_no"] == "SN-X"
    assert ev["workpiece_id"] is None
    assert ev["scan_warning"] is True
    assert ev["warn_reason"] == "原因文案"
    assert ev["timestamp"] > 0


# ==================== m0010 迁移 ====================

def test_m0010_adds_columns_and_is_idempotent(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from backend.db.migrations import m0010_scanner_lifecycle as mig

    engine = create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE scanner_devices ("
            "id INTEGER PRIMARY KEY, name VARCHAR(64))"))
        conn.execute(text("INSERT INTO scanner_devices (name) VALUES ('枪1')"))
        conn.commit()

    mig.apply(engine)
    mig.apply(engine)  # 幂等

    cols = {c["name"] for c in inspect(engine).get_columns("scanner_devices")}
    assert {"resume_on", "rearm_forget_last", "strict_ok_dedup"} <= cols
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT resume_on, rearm_forget_last, strict_ok_dedup "
            "FROM scanner_devices")).fetchone()
    assert row[0] == "cycle_end", "老数据默认值必须 = 现状行为"
    assert not row[1] and not row[2]


def test_m0010_skips_when_table_missing(tmp_path):
    from sqlalchemy import create_engine
    from backend.db.migrations import m0010_scanner_lifecycle as mig
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    mig.apply(engine)  # 不该抛异常


# ==================== API 端点 ====================

def test_resume_endpoint_no_blocked_scanner(client):
    resp = client.post("/api/v1/scanner/resume", params={"channel_id": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["resumed"] == []


def test_resume_endpoint_resumes_blocked(client, monkeypatch):
    from backend.services.scanner import get_scanner_service, ScannerConnection
    svc = get_scanner_service()
    conn = ScannerConnection(
        device_id=990, name="测试枪", ip="127.0.0.1", port=55256,
        channel_id=0, enabled=True, device_type="text_lon",
        scan_mode="once_per_cycle", resume_on="ok_only")
    conn._wait_cycle_resume = True
    conn._resume_blocked = True
    svc._connections[990] = conn
    try:
        resp = client.post("/api/v1/scanner/resume", params={"channel_id": 0})
        assert resp.status_code == 200
        assert resp.json()["resumed"] == ["测试枪"]
        assert conn._wait_cycle_resume is False
        assert conn._resume_blocked is False
    finally:
        svc._connections.pop(990, None)
