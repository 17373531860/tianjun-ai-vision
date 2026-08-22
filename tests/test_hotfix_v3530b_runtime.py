# -*- coding: utf-8 -*-
"""v3.53.0b 累积 hotfix 的运行时回归。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend import hotfix


@pytest.fixture
def patched_scanner():
    """应用真实 class monkey patch；用例结束恢复，避免污染其他测试。"""
    from backend.services.scanner import ScannerService

    original_recv = ScannerService._on_data_received
    original_resume = ScannerService.resume_after_cycle
    hotfix._SCANNER_PATCHED = False
    hotfix._patch_scanner_rearm()
    try:
        yield ScannerService
    finally:
        ScannerService._on_data_received = original_recv
        ScannerService.resume_after_cycle = original_resume
        hotfix._SCANNER_PATCHED = False


def _scanner_service(ScannerService, *, raw="SN-NEW", external_only=False):
    from backend.services.scanner import ScannerConnection

    service = ScannerService()
    conn = ScannerConnection(
        device_id=3530,
        name="JC3530B",
        ip="127.0.0.1",
        port=55256,
        channel_id=0,
        enabled=True,
        device_type="text_lon",
        scan_mode="E",
        status="connected",
        external_only=external_only,
        auto_create_workpiece=True,
        dedup_interval_sec=30,
    )
    service._connections[conn.device_id] = conn
    service._loff_on_code_received = lambda _conn: True
    service._inject_barcode_to_external_devices = lambda *_args: None
    rearms = []
    service._rearm_after_full_reject = (
        lambda _conn, serial, reason="": rearms.append((serial, reason))
    )
    conn.last_scan = "SN-OLD"
    conn.last_scan_time = time.time() - 100
    return service, conn, rearms, raw


def test_scanner_physical_dedup_rearms_and_generation_advances(patched_scanner):
    service, conn, rearms, _ = _scanner_service(patched_scanner)
    conn.last_scan = "SN-DUP"
    conn.last_scan_time = time.time()

    service._on_data_received(conn, "SN-DUP")

    assert conn._jc353_e_scan_generation == 1
    assert rearms == [("SN-DUP", "物理去重命中")]


def test_scanner_dedup_real_loff_and_rearm_release_all_wait_state(patched_scanner):
    service, conn, _rearms, _ = _scanner_service(patched_scanner)
    service._loff_on_code_received = patched_scanner._loff_on_code_received.__get__(
        service, patched_scanner
    )
    service._rearm_after_full_reject = patched_scanner._rearm_after_full_reject.__get__(
        service, patched_scanner
    )
    sent = []
    service._text_lon_send = lambda _conn, payload, label="": sent.append(payload) or True
    conn.last_scan = "SN-DUP-REAL"
    conn.last_scan_time = time.time()
    conn._wait_cycle_resume = False
    conn._resume_blocked = True
    conn._lon_sent = True
    conn._next_lon_after = 999.0

    service._on_data_received(conn, "SN-DUP-REAL")

    assert sent == [b"LOFF\r\n"]
    assert conn._wait_cycle_resume is False
    assert conn._resume_blocked is False
    assert conn._lon_sent is False
    assert conn._next_lon_after == 0.0
    assert conn._last_dispatch is None
    assert conn._rearm_after_reject_serial == "SN-DUP-REAL"


def test_scanner_parse_failure_rearms(patched_scanner):
    service, conn, rearms, raw = _scanner_service(patched_scanner)
    service._parser = SimpleNamespace(
        parse=lambda *_args: SimpleNamespace(success=False, error="bad code")
    )

    service._on_data_received(conn, raw)

    assert rearms == [(raw, "条码解析失败")]


def test_scanner_external_only_rearms(patched_scanner):
    service, conn, rearms, raw = _scanner_service(
        patched_scanner, raw="  RAW-CODE  ", external_only=True
    )
    service._parser = SimpleNamespace(
        parse=lambda *_args: SimpleNamespace(success=True, serial_no="NORMAL-CODE")
    )

    service._on_data_received(conn, raw)

    assert rearms == [("NORMAL-CODE", "external_only 不进视觉")]


def test_scanner_external_only_parse_failure_keeps_parse_reason(patched_scanner):
    service, conn, rearms, raw = _scanner_service(
        patched_scanner, external_only=True
    )
    service._parser = SimpleNamespace(
        parse=lambda *_args: SimpleNamespace(success=False, error="bad external code")
    )

    service._on_data_received(conn, raw)

    assert rearms == [(raw, "条码解析失败")]


def test_scanner_successful_dispatch_does_not_false_rearm(patched_scanner):
    service, conn, rearms, raw = _scanner_service(patched_scanner)
    service._parser = SimpleNamespace(
        parse=lambda *_args: SimpleNamespace(success=True, serial_no=raw)
    )
    service._project_id_getter = lambda _channel: 17
    service._mes_hook = SimpleNamespace(on_scan_received=lambda **_kwargs: True)

    service._on_data_received(conn, raw)

    assert rearms == []
    assert conn._last_dispatch == {
        "serial": raw,
        "channels": {0},
        "rejected": set(),
    }


def test_r1_fast_ok_guard_clears_listener_tail_stale_relock(patched_scanner):
    service, conn, _rearms, _ = _scanner_service(patched_scanner)
    service._resolve_bound_channels = lambda _conn: [0]
    conn._wait_cycle_resume = True
    conn._jc353_e_scan_generation = 7
    conn.last_scan = "FAST-OK"
    conn.last_scan_time = 7.0

    assert service.resume_after_cycle(0, is_good=True) == [conn.name]
    # 模拟 _text_lon_listen_loop 在扫码回调返回后执行的 _schedule_next_lon。
    conn._wait_cycle_resume = True
    time.sleep(0.08)

    assert conn._wait_cycle_resume is False
    assert conn._lon_sent is False


def test_r1_old_guard_never_unlocks_new_scan_generation(patched_scanner):
    service, conn, _rearms, _ = _scanner_service(patched_scanner)
    service._resolve_bound_channels = lambda _conn: [0]
    conn._wait_cycle_resume = True
    conn._jc353_e_scan_generation = 11
    conn.last_scan = "OLD-CODE"
    conn.last_scan_time = 11.0

    assert service.resume_after_cycle(0, is_good=True) == [conn.name]
    conn._jc353_e_scan_generation = 12
    conn.last_scan = "NEW-CODE"
    conn.last_scan_time = 12.0
    conn._wait_cycle_resume = True
    time.sleep(0.08)

    assert conn._wait_cycle_resume is True


def test_scanner_runtime_patch_is_idempotent(patched_scanner):
    recv_once = patched_scanner._on_data_received
    resume_once = patched_scanner.resume_after_cycle
    hotfix._patch_scanner_rearm()
    assert patched_scanner._on_data_received is recv_once
    assert patched_scanner.resume_after_cycle is resume_once


def test_pg_datetime_failure_before_patch_and_aware_after(monkeypatch):
    from backend.api import source_session_lifecycle_mixin as lifecycle
    from backend.db import database

    start_aware = datetime.now().astimezone()
    with pytest.raises(TypeError, match="offset-naive|offset-aware"):
        _ = datetime.now() - start_aware

    original = lifecycle.datetime
    hotfix._PG_DATETIME_PATCHED = False
    monkeypatch.setattr(database, "get_dialect", lambda: "postgresql")
    try:
        assert hotfix._patch_pg_aware_datetime() is True
        # start_cycle 也走同一模块全局 datetime，启动后即写入 aware；PG 读取时
        # 常归一到 UTC。两端都 aware 时 duration 保持正值，不会出现 -8h。
        start_cycle_local = lifecycle.datetime.now() - timedelta(seconds=0.01)
        start_from_pg = start_cycle_local.astimezone(timezone.utc)
        end_aware = lifecycle.datetime.now()
        assert start_cycle_local.tzinfo is not None
        assert end_aware.tzinfo is not None
        assert (end_aware - start_from_pg).total_seconds() > 0
        assert lifecycle.datetime.fromtimestamp(time.time()).tzinfo is not None
        first = lifecycle.datetime
        assert hotfix._patch_pg_aware_datetime() is True
        assert lifecycle.datetime is first
    finally:
        lifecycle.datetime = original
        hotfix._PG_DATETIME_PATCHED = False


def test_sqlite_does_not_inject_datetime(monkeypatch):
    from backend.api import source_session_lifecycle_mixin as lifecycle
    from backend.db import database

    original = lifecycle.datetime
    hotfix._PG_DATETIME_PATCHED = False
    monkeypatch.setattr(database, "get_dialect", lambda: "sqlite")
    try:
        assert hotfix._patch_pg_aware_datetime() is False
        assert lifecycle.datetime is original
        assert hotfix._PG_DATETIME_PATCHED is False
    finally:
        lifecycle.datetime = original
        hotfix._PG_DATETIME_PATCHED = False


def test_real_postgresql_start_and_end_are_aware_after_roundtrip(db_session):
    """DATABASE_URL 可注入的真实 PG 探针；SQLite 会明确 skip。"""
    from sqlalchemy import text
    from backend.api import source_session_lifecycle_mixin as lifecycle
    from backend.db import database

    if database.get_dialect() != "postgresql":
        pytest.skip("requires isolated PostgreSQL DATABASE_URL")
    original = lifecycle.datetime
    hotfix._PG_DATETIME_PATCHED = False
    try:
        assert hotfix._patch_pg_aware_datetime() is True
        start_local = lifecycle.datetime.now()
        start_from_pg = db_session.execute(
            text("SELECT CAST(:start_time AS TIMESTAMPTZ) AS start_time"),
            {"start_time": start_local},
        ).mappings().one()["start_time"]
        time.sleep(0.005)
        end_local = lifecycle.datetime.now()
        duration = (end_local - start_from_pg).total_seconds()
        assert start_local.tzinfo is not None
        assert start_from_pg.tzinfo is not None
        assert end_local.tzinfo is not None
        assert duration >= 0
    finally:
        lifecycle.datetime = original
        hotfix._PG_DATETIME_PATCHED = False


def test_apply_is_idempotent(monkeypatch):
    calls = []
    patch_names = [
        "_patch_vsm_methods",
        "_patch_tick_consume",
        "_patch_settle_hint",
        "_patch_scanner_rearm",
        "_patch_work_order_scope",
        "_patch_pg_aware_datetime",
        "_register_trigger_action",
        "_register_pre_manual_pass_hook_whitelist",
    ]
    monkeypatch.setattr(hotfix, "_supported_runtime", lambda: True)
    for name in patch_names:
        monkeypatch.setattr(hotfix, name, lambda _name=name: calls.append(_name))
    hotfix._APPLY_COMPLETE = False
    try:
        assert hotfix.apply() is True
        assert hotfix.apply() is True
        assert calls == patch_names
    finally:
        hotfix._APPLY_COMPLETE = False
