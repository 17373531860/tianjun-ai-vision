"""扫码器旁路 SN 监控 — 单元/集成测试

覆盖:
  1. _scan_dir: ok / dir_missing / no_file / 错误隔离 + serial_no 去扩展名
  2. _build_state + by_channel/default 解析 (来自 ExportRealtimeRule.channel_filter)
  3. get_current_for_channel: 专属规则优先, 无则回退 default
  4. snapshot_for_cycle_start 内存优先 (read_via=monitor) + external_meta 增补字段
"""
from __future__ import annotations

import os
import uuid as _uuid
from datetime import datetime, timedelta

import pytest

from backend.models.models import (
    Project, Model, DetectionSession, DetectionCycle,
)
from backend.models.export_models import (
    ExportTemplate, ExportRealtimeRule, ExportRunLog,
)
from backend.services import scanner_bypass_monitor as mon
from backend.services.export_snapshot import (
    snapshot_for_cycle_start, _normalize_dir,
)
from backend.services.export_seed import seed_builtin_templates


@pytest.fixture(autouse=True)
def _clean(db_session):
    db_session.query(ExportRunLog).delete()
    db_session.query(ExportRealtimeRule).delete()
    db_session.commit()
    # 每个测试前后清空监控内存状态, 避免跨测试串
    with mon._STATE_LOCK:
        mon._STATE.update({
            "running": False, "polled_at": None, "by_dir": {},
            "by_channel": {}, "default": None, "entries": [], "error": None,
        })
    yield
    db_session.query(ExportRunLog).delete()
    db_session.query(ExportRealtimeRule).delete()
    db_session.commit()


@pytest.fixture
def scan_dir(tmp_path):
    d = tmp_path / "scan_in"
    d.mkdir()
    return str(d)


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return str(d)


@pytest.fixture
def builtin_tpl(db_session):
    seed_builtin_templates()
    tpl = db_session.query(ExportTemplate).filter(
        ExportTemplate.builtin_id == "builtin_scanner_bypass_3line_txt"
    ).first()
    assert tpl is not None
    return tpl


def _mk_rule(db_session, tpl, scan_dir, out_dir, **overrides):
    rule = ExportRealtimeRule(
        name=overrides.pop("name", "旁路监控测试规则"),
        enabled=overrides.pop("enabled", True),
        template_id=tpl.id,
        output_dir=out_dir,
        filename_template="{{ latest_input_filename() }}",
        input_file_mode="none",
        input_dir=scan_dir,
        trigger_event="cycle_end",
        latest_file_strategy=overrides.pop("latest_file_strategy",
                                           "cycle_start_snapshot"),
        **overrides,
    )
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)
    return rule


# ============================================================
# 1. _scan_dir
# ============================================================

def test_scan_dir_ok(scan_dir):
    open(os.path.join(scan_dir, "SN123456.txt"), "w").write("SN123456")
    target = {"input_dir": scan_dir, "normalized_dir": _normalize_dir(scan_dir),
              "wait_stable_ms": 0, "max_age_sec": 0, "channels": {0}, "rule_ids": [1]}
    entry = mon._scan_dir(target)
    assert entry["status"] == "ok"
    assert entry["filename"] == "SN123456.txt"
    assert entry["serial_no"] == "SN123456"
    assert entry["source"] == "scanner_bypass"
    assert entry["channels"] == [0]


def test_scan_dir_missing(tmp_path):
    missing = str(tmp_path / "nope")
    target = {"input_dir": missing, "normalized_dir": _normalize_dir(missing),
              "wait_stable_ms": 0, "max_age_sec": 0, "channels": set(), "rule_ids": []}
    entry = mon._scan_dir(target)
    assert entry["status"] == "dir_missing"
    assert entry["serial_no"] is None


def test_scan_dir_no_file(scan_dir):
    target = {"input_dir": scan_dir, "normalized_dir": _normalize_dir(scan_dir),
              "wait_stable_ms": 0, "max_age_sec": 0, "channels": set(), "rule_ids": []}
    entry = mon._scan_dir(target)
    assert entry["status"] == "no_file"
    assert entry["serial_no"] is None


def test_scan_dir_error_isolated(scan_dir, monkeypatch):
    """底层 helper 抛异常时 _scan_dir 归 error, 不外抛."""
    open(os.path.join(scan_dir, "X.txt"), "w").write("X")

    def _boom(*a, **k):
        raise RuntimeError("boom")

    import backend.services.export_renderer as rnd
    monkeypatch.setattr(rnd, "latest_input_filename", _boom)
    target = {"input_dir": scan_dir, "normalized_dir": _normalize_dir(scan_dir),
              "wait_stable_ms": 0, "max_age_sec": 0, "channels": set(), "rule_ids": []}
    entry = mon._scan_dir(target)
    assert entry["status"] == "error"
    assert "boom" in (entry["error"] or "")


# ============================================================
# 2 + 3. _build_state / by_channel / default / get_current_for_channel
# ============================================================

def test_build_state_by_channel_and_default(db_session, builtin_tpl, scan_dir, out_dir):
    open(os.path.join(scan_dir, "SN_CH1.txt"), "w").write("SN_CH1")
    # channel_filter=[1] 专属规则
    _mk_rule(db_session, builtin_tpl, scan_dir, out_dir, channel_filter=[1])

    state = mon._build_state(1.0)
    with mon._STATE_LOCK:
        mon._STATE.update(state)

    assert "1" in state["by_channel"]
    assert state["by_channel"]["1"]["serial_no"] == "SN_CH1"
    # 无 channel_filter=None 规则时 default 为 None
    assert state["default"] is None

    # 专属通道命中, 其它通道回退 default(None)
    assert mon.get_current_for_channel(1)["serial_no"] == "SN_CH1"
    assert mon.get_current_for_channel(99) is None


def test_build_state_default_applies_all(db_session, builtin_tpl, scan_dir, out_dir):
    open(os.path.join(scan_dir, "SN_ALL.txt"), "w").write("SN_ALL")
    # channel_filter=None → default 兜底所有通道
    _mk_rule(db_session, builtin_tpl, scan_dir, out_dir, channel_filter=None)

    state = mon._build_state(1.0)
    with mon._STATE_LOCK:
        mon._STATE.update(state)

    assert state["default"] is not None
    assert state["default"]["serial_no"] == "SN_ALL"
    # 任意通道都回退到 default
    assert mon.get_current_for_channel(0)["serial_no"] == "SN_ALL"
    assert mon.get_current_for_channel(7)["serial_no"] == "SN_ALL"


def test_get_status_snapshot_readonly(db_session, builtin_tpl, scan_dir, out_dir):
    open(os.path.join(scan_dir, "SN_S.txt"), "w").write("SN_S")
    _mk_rule(db_session, builtin_tpl, scan_dir, out_dir, channel_filter=[0])
    state = mon._build_state(1.5)
    with mon._STATE_LOCK:
        mon._STATE.update(state)
    snap = mon.get_status_snapshot()
    assert snap["poll_interval_sec"] == 1.5
    assert snap["by_channel"]["0"]["serial_no"] == "SN_S"
    assert isinstance(snap["entries"], list) and len(snap["entries"]) == 1


# ============================================================
# 4. snapshot_for_cycle_start 内存优先 + 增补字段
# ============================================================

@pytest.fixture
def cycle(db_session, tmp_path):
    fake = tmp_path / "fake.pt"
    fake.write_bytes(b"x")
    m = Model(name="m", file_path=str(fake), file_name="fake.pt",
              file_size=1, labels=["A"])
    db_session.add(m)
    db_session.commit()
    p = Project(name=f"p_{int(datetime.now().timestamp()*1000)}",
                task_type="detection", logic_mode="sequential",
                default_model_id=m.id, steps_config=[], events_config=[],
                counters_config=[], pipeline_config={})
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    sess = DetectionSession(session_uuid=_uuid.uuid4().hex, project_id=p.id,
                            start_time=datetime.now() - timedelta(minutes=1))
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    c = DetectionCycle(cycle_uuid=_uuid.uuid4().hex, session_id=sess.id,
                       cycle_number=1, start_time=datetime.now(), is_good=True)
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    return c, p


def test_snapshot_enriched_fields_and_memory_first(
        db_session, builtin_tpl, scan_dir, out_dir, cycle):
    cyc, proj = cycle
    open(os.path.join(scan_dir, "SN999.txt"), "w").write("SN999")
    _mk_rule(db_session, builtin_tpl, scan_dir, out_dir,
             channel_filter=[0], latest_file_strategy="cycle_start_snapshot")

    # 预热监控内存 (使 cycle_start 走内存优先)
    state = mon._build_state(1.0)
    with mon._STATE_LOCK:
        mon._STATE.update(state)

    summary = snapshot_for_cycle_start(db_session, channel_id=0,
                                       cycle_id=cyc.id, project_id=proj.id)
    db_session.commit()
    db_session.refresh(cyc)
    assert summary["taken"] == 1, summary

    snaps = (cyc.external_meta or {}).get("scan_snapshots") or {}
    key = _normalize_dir(scan_dir)
    assert key in snaps
    snap = snaps[key]
    # 增补字段
    assert snap["serial_no"] == "SN999"
    assert snap["filename"] == "SN999.txt"
    assert snap["input_dir"] == scan_dir
    assert snap["source"] == "scanner_bypass"
    assert snap["locked_at"]
    assert snap["read_via"] == "monitor"
    # 向后兼容字段仍在
    assert snap["mtime"] is not None
    assert "snapshot_at" in snap


def test_snapshot_glob_fallback_when_monitor_cold(
        db_session, builtin_tpl, scan_dir, out_dir, cycle):
    """监控未预热 → 回退 glob, read_via=glob, 字段依旧完整."""
    cyc, proj = cycle
    open(os.path.join(scan_dir, "SN_GLOB.txt"), "w").write("SN_GLOB")
    _mk_rule(db_session, builtin_tpl, scan_dir, out_dir,
             channel_filter=[0], latest_file_strategy="cycle_start_snapshot")
    # 不预热监控内存

    summary = snapshot_for_cycle_start(db_session, channel_id=0,
                                       cycle_id=cyc.id, project_id=proj.id)
    db_session.commit()
    db_session.refresh(cyc)
    assert summary["taken"] == 1
    snap = (cyc.external_meta or {})["scan_snapshots"][_normalize_dir(scan_dir)]
    assert snap["serial_no"] == "SN_GLOB"
    assert snap["read_via"] == "glob"
    assert snap["source"] == "scanner_bypass"


# ============================================================
# 5. API 端点契约 (GET /export/scanner-bypass/status)
# ============================================================

def test_api_scanner_bypass_status(client, db_session, builtin_tpl,
                                   scan_dir, out_dir):
    open(os.path.join(scan_dir, "SN_API.txt"), "w").write("SN_API")
    _mk_rule(db_session, builtin_tpl, scan_dir, out_dir, channel_filter=[0])
    # 预热内存 (API 只读内存, 不触发扫描)
    with mon._STATE_LOCK:
        mon._STATE.update(mon._build_state(1.0))

    r = client.get("/api/v1/export/scanner-bypass/status")
    assert r.status_code == 200
    body = r.json()
    assert "by_channel" in body and "entries" in body
    assert body["by_channel"]["0"]["serial_no"] == "SN_API"

    # 带 channel_id → 返回 current
    r2 = client.get("/api/v1/export/scanner-bypass/status", params={"channel_id": 0})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["channel_id"] == 0
    assert body2["current"]["serial_no"] == "SN_API"
