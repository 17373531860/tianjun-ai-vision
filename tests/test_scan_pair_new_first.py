# -*- coding: utf-8 -*-
"""v3.49 WS3: scan_pair 新码先上屏测试。

背景: 旧序是"扫码 B → 先结算上一窗口(DB写/事件/录像收尾都跑完) → 才 promote
新码上屏", 结算慢时工人看着旧码干等 (捷昌现场: 延迟够装完两箱)。

新序 (默认开): 扫码 B → 立即开新窗 + promote (前端马上看到新码) → 再结算
上一窗口。被结算窗口的身份 (wp_id / scanned_at) 显式传参钳制, 不再隐式读
_inspecting_workpiece / _scan_pair_active (此刻已指向新码)。

覆盖:
  1. 开关默认开 (无 SystemConfig 行) / 显式关尊重 / 落盘重读往返
  2. 新序: settle 时新码已上屏, 旧码身份走 prev_wp_id
  3. 旧序 (显式关): settle 时 inspecting 仍是旧码, 不传 prev_wp_id (隐式读)
  4. 超时路径: 显式带 entry 的 wp_id / scanned_at
  5. 停止路径: 同上
  6. _dispatch_scan_pair_settle 对老签名 settle_for_scan_pair 的 TypeError 兜底
"""
import time
from unittest.mock import MagicMock

import pytest


def _make_mgr(monkeypatch, *, new_first=True):
    """裸 MESHookManager：掐掉 broadcast 解析 / 定时器 / 落库副作用。"""
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr.enabled = True
    mgr._scan_pair_new_first = new_first

    monkeypatch.setattr(mgr, "_scan_pair_resolve_broadcast_channels", lambda ch: [ch])
    monkeypatch.setattr(mgr, "_arm_scan_pair_timer", lambda ch: None)
    monkeypatch.setattr(mgr, "_cancel_scan_pair_timer", lambda ch: None)
    mgr._workpiece_svc = MagicMock()

    settles = []

    def _fake_settle(channel_id, *, force_ng, reason,
                     prev_wp_id=None, prev_scanned_at=None):
        settles.append({
            "ch": channel_id, "force_ng": force_ng, "reason": reason,
            "prev_wp_id": prev_wp_id, "prev_scanned_at": prev_scanned_at,
            "inspecting_at_settle": mgr._inspecting_workpiece.get(channel_id),
            "active_serial_at_settle": (mgr._scan_pair_active.get(channel_id) or {}).get("serial_no"),
        })
        return 1

    monkeypatch.setattr(mgr, "_dispatch_scan_pair_settle", _fake_settle)
    return mgr, settles


# ==================== 开关持久化 ====================

def test_switch_default_on(app):
    from backend.db.database import SessionLocal
    from backend.models.models import SystemConfig

    db = SessionLocal()
    try:
        db.query(SystemConfig).filter(
            SystemConfig.key == "scan_pair_new_code_first").delete()
        db.commit()
    finally:
        db.close()

    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    assert mgr.get_scan_pair_new_first() is True
    mgr._load_scan_pair_new_first_config()
    assert mgr.get_scan_pair_new_first() is True, "缺省(无 SystemConfig 行)应默认开"


def test_switch_explicit_off_persists(app):
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr.set_scan_pair_new_first(False)
    assert mgr.get_scan_pair_new_first() is False

    mgr2 = MESHookManager()
    mgr2._load_scan_pair_new_first_config()
    assert mgr2.get_scan_pair_new_first() is False, "显式 false 必须尊重"

    mgr.set_scan_pair_new_first(True)  # 复位默认, 不污染其他用例
    mgr3 = MESHookManager()
    mgr3._load_scan_pair_new_first_config()
    assert mgr3.get_scan_pair_new_first() is True


# ==================== 新序 (默认开) ====================

def test_new_first_promotes_before_settle(monkeypatch):
    """新序核心契约: settle 时新码已上屏 + 旧码身份显式钳制。"""
    mgr, settles = _make_mgr(monkeypatch, new_first=True)
    ch = 0
    mgr._pending_workpiece[ch] = 101
    mgr._handle_scan_pair_event(MagicMock(), ch, "SN-A", 101)
    a_scanned_at = mgr._scan_pair_active[ch]["scanned_at"]

    mgr._pending_workpiece[ch] = 102
    mgr._handle_scan_pair_event(MagicMock(), ch, "SN-B", 102)

    assert len(settles) == 1
    s = settles[0]
    assert s["prev_wp_id"] == 101, "旧码 wp 必须显式带给 settle"
    assert s["prev_scanned_at"] == pytest.approx(a_scanned_at)
    assert s["inspecting_at_settle"] == 102, "settle 时新码必须已经 promote 上屏"
    assert s["active_serial_at_settle"] == "SN-B", "settle 时新窗口必须已开"
    assert mgr._inspecting_workpiece[ch] == 102


def test_legacy_order_when_off(monkeypatch):
    """显式关开关 → 旧序: 先 settle (隐式读旧码) 后 promote。"""
    mgr, settles = _make_mgr(monkeypatch, new_first=False)
    ch = 0
    mgr._pending_workpiece[ch] = 101
    mgr._handle_scan_pair_event(MagicMock(), ch, "SN-A", 101)

    mgr._pending_workpiece[ch] = 102
    mgr._handle_scan_pair_event(MagicMock(), ch, "SN-B", 102)

    assert len(settles) == 1
    s = settles[0]
    assert s["prev_wp_id"] is None, "旧序不传显式身份 (source 隐式读)"
    assert s["inspecting_at_settle"] == 101, "旧序 settle 时 inspecting 仍是旧码"
    assert s["active_serial_at_settle"] == "SN-A", "旧序 settle 时新窗口未开"
    assert mgr._inspecting_workpiece[ch] == 102, "最终仍落在新码"


# ==================== 超时 / 停止路径显式身份 ====================

def test_timeout_passes_prev_identity(monkeypatch):
    mgr, settles = _make_mgr(monkeypatch, new_first=True)
    ch = 0
    ts = time.time() - 30
    mgr._scan_pair_active[ch] = {"serial_no": "SN-A", "wp_id": 77, "scanned_at": ts}

    mgr._on_scan_pair_timeout(ch)

    assert len(settles) == 1
    s = settles[0]
    assert s["force_ng"] is True and s["reason"] == "scan_pair_timeout"
    assert s["prev_wp_id"] == 77
    assert s["prev_scanned_at"] == pytest.approx(ts)
    assert ch not in mgr._scan_pair_active


def test_stop_settle_passes_prev_identity(monkeypatch):
    mgr, settles = _make_mgr(monkeypatch, new_first=True)
    ch = 0
    ts = time.time() - 10
    mgr._scan_pair_active[ch] = {"serial_no": "SN-A", "wp_id": 88, "scanned_at": ts}

    n = mgr.settle_scan_pair_for_stop(ch, discard=False)

    assert n == 1
    s = settles[0]
    assert s["prev_wp_id"] == 88
    assert s["prev_scanned_at"] == pytest.approx(ts)


# ==================== 老签名兜底 ====================

def test_dispatch_falls_back_to_legacy_signature(monkeypatch):
    """settle_for_scan_pair 未升级新签名 (插件/测试替身) → TypeError 兜底两参重调。"""
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()

    calls = []

    class LegacySource:
        def settle_for_scan_pair(self, *, force_ng=False, reason=""):
            calls.append({"force_ng": force_ng, "reason": reason})
            return 1

    import backend.api.channel_manager as cm_mod
    monkeypatch.setattr(cm_mod.channel_manager, "get", lambda ch: LegacySource())

    n = mgr._dispatch_scan_pair_settle(
        0, force_ng=False, reason="scan_pair_next_code:SN-X",
        prev_wp_id=55, prev_scanned_at=123.0)

    assert n == 1
    assert calls == [{"force_ng": False, "reason": "scan_pair_next_code:SN-X"}]


def test_dispatch_passes_new_kwargs_to_upgraded_source(monkeypatch):
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()

    calls = []

    class NewSource:
        def settle_for_scan_pair(self, *, force_ng=False, reason="",
                                 prev_wp_id=None, prev_scanned_at=None):
            calls.append({"prev_wp_id": prev_wp_id,
                          "prev_scanned_at": prev_scanned_at})
            return 1

    import backend.api.channel_manager as cm_mod
    monkeypatch.setattr(cm_mod.channel_manager, "get", lambda ch: NewSource())

    n = mgr._dispatch_scan_pair_settle(
        0, force_ng=False, reason="r", prev_wp_id=55, prev_scanned_at=123.0)

    assert n == 1
    assert calls == [{"prev_wp_id": 55, "prev_scanned_at": 123.0}]
