"""v3.7.2 扫码器旁路 — 三策略 + 去重 + 异步重试 集成测试

测试矩阵:
  - 策略 A (mtime)                : 不开稳定 / 不限年龄
  - 策略 B (mtime_stable)         : wait_stable_ms + max_age_sec 加固
  - 策略 C (cycle_start_snapshot) : 周期开始锁快照, 渲染时直接用
  - 去重 (dedupe_same_filename)   : on/off, 命中重名异步重试 + 超时跳过
  - cycle.external_meta 验证      : C 策略写库可追溯

每个测试独立 fixture + autouse 清表, 避免跨测试污染.
"""
from __future__ import annotations

import os
import time
import uuid as _uuid
from datetime import datetime, timedelta

import pytest

from backend.models.models import (
    Project, Model, DetectionSession, DetectionCycle, StepRecord
)
from backend.models.export_models import ExportTemplate, ExportRealtimeRule, ExportRunLog
from backend.services.export_realtime import dispatch_cycle_end_export
from backend.services.export_snapshot import (
    snapshot_for_cycle_start, lookup_snapshot_from_cycle, _normalize_dir,
)
from backend.services.export_seed import seed_builtin_templates


# ============================================================
# 通用 fixtures
# ============================================================

@pytest.fixture(autouse=True)
def _clean_realtime(db_session):
    db_session.query(ExportRunLog).delete()
    db_session.query(ExportRealtimeRule).delete()
    db_session.commit()
    yield
    db_session.query(ExportRunLog).delete()
    db_session.query(ExportRealtimeRule).delete()
    db_session.commit()


@pytest.fixture
def dirs(tmp_path):
    scan = tmp_path / "scan_in"
    scan.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    return {"scan": str(scan), "out": str(out)}


@pytest.fixture
def project(db_session, tmp_path):
    fake = tmp_path / "fake.pt"
    fake.write_bytes(b"x")
    m = Model(name="m", file_path=str(fake), file_name="fake.pt",
              file_size=1, labels=["A", "B"])
    db_session.add(m)
    db_session.commit()
    p = Project(
        name=f"p_{int(datetime.now().timestamp() * 1000)}",
        task_type="detection", logic_mode="sequential",
        default_model_id=m.id,
        steps_config=[{"id": "s1", "label": "A", "enabled": True},
                       {"id": "s2", "label": "B", "enabled": True}],
        events_config=[{"id": 1, "name": "OK", "show_notification": True}],
        counters_config=[], pipeline_config={},
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def cycle(db_session, project):
    sess = DetectionSession(session_uuid=_uuid.uuid4().hex, project_id=project.id,
                             start_time=datetime.now() - timedelta(minutes=1))
    db_session.add(sess)
    db_session.commit()
    db_session.refresh(sess)
    c = DetectionCycle(
        cycle_uuid=_uuid.uuid4().hex, session_id=sess.id, cycle_number=1,
        start_time=datetime.now() - timedelta(seconds=10),
        end_time=datetime.now(), duration=10.0, is_good=True,
        event_id=1, event_name="OK", result_reason="ok",
        step_sequence=["A", "B"],
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    for idx, (lbl, d) in enumerate([("A", 3.45), ("B", 2.10)]):
        db_session.add(StepRecord(
            record_uuid=_uuid.uuid4().hex[:12], cycle_id=c.id,
            step_id=f"s{idx+1}", step_label=lbl, step_order=idx,
            start_time=c.start_time + timedelta(seconds=idx),
            end_time=c.start_time + timedelta(seconds=idx + d),
            duration=d, confidence=0.9, is_valid=True,
        ))
    db_session.commit()
    return c, sess


@pytest.fixture
def builtin_tpl(db_session):
    seed_builtin_templates()
    tpl = db_session.query(ExportTemplate).filter(
        ExportTemplate.builtin_id == "builtin_scanner_bypass_3line_txt"
    ).first()
    assert tpl is not None
    return tpl


def _mk_rule(db_session, builtin_tpl, dirs, **overrides):
    rule = ExportRealtimeRule(
        name=overrides.pop("name", "测试规则"),
        enabled=True,
        template_id=builtin_tpl.id,
        output_dir=dirs["out"],
        filename_template=overrides.pop(
            "filename_template", "{{ latest_input_filename() }}"
        ),
        input_file_mode="none",
        input_dir=dirs["scan"],
        trigger_event="cycle_end",
        encoding="utf-8",
        newline="lf",
        **overrides,
    )
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)
    return rule


# ============================================================
# 策略 A: mtime (默认行为, 不开保险)
# ============================================================

def test_strategy_mtime_basic(db_session, dirs, project, cycle, builtin_tpl):
    cyc, _ = cycle
    (open(os.path.join(dirs["scan"], "SN_A1.txt"), "w")).write("SN_A1_VAL")

    _mk_rule(db_session, builtin_tpl, dirs,
              latest_file_strategy="mtime")

    results = dispatch_cycle_end_export(db_session, channel_id=0,
                                          cycle_id=cyc.id, project_id=project.id)
    assert results and results[0]["status"] == "success"
    out_path = os.path.join(dirs["out"], "SN_A1.txt")
    assert os.path.exists(out_path)
    content = open(out_path).read()
    assert "SN_A1_VAL" in content
    assert "Pass" in content  # v3.7.3+ 客户需求 Pass/Fail


# ============================================================
# 策略 B: mtime_stable + max_age_sec
# ============================================================

def test_strategy_mtime_stable_max_age_filters_old(
    db_session, dirs, project, cycle, builtin_tpl
):
    """max_age_sec=2 → 3 秒前的文件应被忽略"""
    cyc, _ = cycle
    # 投一个"旧"文件 (mtime 设到 5 秒前)
    old = os.path.join(dirs["scan"], "OLD.txt")
    open(old, "w").write("OLD_VAL")
    old_mtime = time.time() - 5
    os.utime(old, (old_mtime, old_mtime))

    _mk_rule(db_session, builtin_tpl, dirs,
              latest_file_strategy="mtime_stable",
              latest_file_max_age_sec=2,
              latest_file_wait_stable_ms=0,
              filename_template="fallback_{{ cycle.id }}.txt")

    results = dispatch_cycle_end_export(db_session, channel_id=0,
                                          cycle_id=cyc.id, project_id=project.id)
    assert results and results[0]["status"] == "success"
    # 应走 fallback 名 — 因为 max_age 过滤掉了 OLD.txt
    fallback_path = os.path.join(dirs["out"], f"fallback_{cyc.id}.txt")
    assert os.path.exists(fallback_path)
    content = open(fallback_path).read()
    # OLD_VAL 不应被读到 (latest_input_text 也被 max_age 拒了)
    assert "OLD_VAL" not in content
    # 首行空, 后续 3 行该有的还有
    lines = content.splitlines()
    assert lines[0] == ""
    assert "Pass" in content  # v3.7.3+ 客户需求 Pass/Fail


def test_strategy_mtime_stable_accepts_fresh(
    db_session, dirs, project, cycle, builtin_tpl
):
    """新文件 (mtime 当前) max_age_sec=5 应该正常采纳"""
    cyc, _ = cycle
    fresh = os.path.join(dirs["scan"], "FRESH.txt")
    open(fresh, "w").write("FRESH_VAL")

    _mk_rule(db_session, builtin_tpl, dirs,
              latest_file_strategy="mtime_stable",
              latest_file_max_age_sec=5,
              latest_file_wait_stable_ms=0)

    dispatch_cycle_end_export(db_session, channel_id=0,
                                cycle_id=cyc.id, project_id=project.id)
    out = os.path.join(dirs["out"], "FRESH.txt")
    assert os.path.exists(out)
    assert "FRESH_VAL" in open(out).read()


# ============================================================
# 策略 C: cycle_start_snapshot
# ============================================================

def test_strategy_cycle_start_snapshot_locks_at_cycle_start(
    db_session, dirs, project, cycle, builtin_tpl
):
    """cycle_start 拍照 → 周期中扫码器又写新 txt → cycle_end 用的还是锁定那份"""
    cyc, _ = cycle
    # 1) 投第一个 txt 模拟扫码
    open(os.path.join(dirs["scan"], "LOCKED.txt"), "w").write("LOCKED_AT_START")

    _mk_rule(db_session, builtin_tpl, dirs,
              latest_file_strategy="cycle_start_snapshot")

    # 2) 模拟 cycle_start hook 调用 — 拍照
    summary = snapshot_for_cycle_start(db_session, channel_id=0,
                                         cycle_id=cyc.id, project_id=project.id)
    db_session.commit()
    assert summary["taken"] == 1, summary

    # 3) 验证写到 cycle.external_meta
    db_session.refresh(cyc)
    snap = lookup_snapshot_from_cycle(cyc.external_meta, dirs["scan"])
    assert snap is not None
    assert snap["filename"] == "LOCKED.txt"
    assert snap["text"] == "LOCKED_AT_START"

    # 4) 周期中扫码器又生成了一份新文件 (mtime 更新)
    time.sleep(0.05)
    open(os.path.join(dirs["scan"], "NEW_MID_CYCLE.txt"), "w").write("NEW_MID")

    # 5) cycle_end → 应该用 LOCKED 那份, 不是 NEW_MID
    dispatch_cycle_end_export(db_session, channel_id=0,
                                cycle_id=cyc.id, project_id=project.id)
    out_path = os.path.join(dirs["out"], "LOCKED.txt")
    assert os.path.exists(out_path), f"out dir={os.listdir(dirs['out'])}"
    content = open(out_path).read()
    assert "LOCKED_AT_START" in content
    assert "NEW_MID" not in content


def test_strategy_cycle_start_snapshot_falls_back_when_no_snapshot(
    db_session, dirs, project, cycle, builtin_tpl
):
    """规则后建 / cycle_start 时没拍照 → fallback 到 mtime"""
    cyc, _ = cycle
    open(os.path.join(dirs["scan"], "FB.txt"), "w").write("FB_VAL")
    # 不调 snapshot_for_cycle_start, 直接建 cycle_start_snapshot 规则

    _mk_rule(db_session, builtin_tpl, dirs,
              latest_file_strategy="cycle_start_snapshot")

    dispatch_cycle_end_export(db_session, channel_id=0,
                                cycle_id=cyc.id, project_id=project.id)
    out = os.path.join(dirs["out"], "FB.txt")
    assert os.path.exists(out)
    assert "FB_VAL" in open(out).read()


def test_snapshot_normalizes_dir_for_lookup(db_session, dirs, project, cycle, builtin_tpl):
    """input_dir 写法不一致也能查到同一份快照 (规范化路径)"""
    cyc, _ = cycle
    open(os.path.join(dirs["scan"], "X.txt"), "w").write("XVAL")

    _mk_rule(db_session, builtin_tpl, dirs,
              latest_file_strategy="cycle_start_snapshot")
    snapshot_for_cycle_start(db_session, channel_id=0,
                              cycle_id=cyc.id, project_id=project.id)
    db_session.commit()
    db_session.refresh(cyc)

    # 用末尾带斜杠 / 反斜杠的版本查询应该都命中
    assert lookup_snapshot_from_cycle(cyc.external_meta, dirs["scan"]) is not None
    assert lookup_snapshot_from_cycle(cyc.external_meta, dirs["scan"] + "/") is not None


def test_snapshot_with_no_eligible_rules(db_session, dirs, project, cycle):
    """目录里没有 cycle_start_snapshot 策略的规则 → 不写 external_meta"""
    cyc, _ = cycle
    open(os.path.join(dirs["scan"], "x.txt"), "w").write("x")
    # 不建规则
    summary = snapshot_for_cycle_start(db_session, channel_id=0,
                                         cycle_id=cyc.id, project_id=project.id)
    db_session.refresh(cyc)
    assert summary["taken"] == 0
    assert cyc.external_meta in (None, {})


# ============================================================
# 去重 (dedupe_same_filename)
# ============================================================

def test_dedupe_first_run_writes_and_records(
    db_session, dirs, project, cycle, builtin_tpl
):
    """开启去重的第一轮: 没有 last_used → 正常写, 并把当前 filename 回填"""
    cyc, _ = cycle
    open(os.path.join(dirs["scan"], "FIRST.txt"), "w").write("FIRST_VAL")

    rule = _mk_rule(db_session, builtin_tpl, dirs,
                     latest_file_strategy="mtime",  # 不走快照路径
                     dedupe_same_filename=True)

    dispatch_cycle_end_export(db_session, channel_id=0,
                                cycle_id=cyc.id, project_id=project.id)
    assert os.path.exists(os.path.join(dirs["out"], "FIRST.txt"))
    db_session.refresh(rule)
    assert rule.last_used_input_filename == "FIRST.txt"


def test_dedupe_hit_queues_async_retry(
    db_session, dirs, project, cycle, builtin_tpl
):
    """开启去重 + 同名命中 → 主线程立即 skipped(dedupe_queued), 异步线程处理"""
    cyc, _ = cycle
    open(os.path.join(dirs["scan"], "SAME.txt"), "w").write("SAME_VAL_1")

    rule = _mk_rule(db_session, builtin_tpl, dirs,
                     latest_file_strategy="mtime",
                     dedupe_same_filename=True,
                     dedupe_retry_max_sec=2,
                     dedupe_retry_interval_ms=100)
    # 模拟"上一轮已经用过 SAME.txt"
    rule.last_used_input_filename = "SAME.txt"
    db_session.commit()

    # 当前 latest 还是 SAME.txt → 命中重名 → 主线程 skipped queued
    results = dispatch_cycle_end_export(db_session, channel_id=0,
                                          cycle_id=cyc.id, project_id=project.id)
    assert results and results[0]["status"] == "skipped"
    assert "dedupe_queued" in (results[0].get("skip_reason") or "")

    # 等 0.3 秒后投一个新文件 → 异步线程应该捡起来
    time.sleep(0.3)
    open(os.path.join(dirs["scan"], "NEW.txt"), "w").write("NEW_VAL")

    # 给异步线程时间完成 (最多再等 2 秒)
    deadline = time.time() + 3.0
    while time.time() < deadline:
        time.sleep(0.1)
        if os.path.exists(os.path.join(dirs["out"], "NEW.txt")):
            break

    out_path = os.path.join(dirs["out"], "NEW.txt")
    assert os.path.exists(out_path), \
        f"异步线程应在新文件出现后落盘 NEW.txt, 实际 out={os.listdir(dirs['out'])}"
    assert "NEW_VAL" in open(out_path).read()


def test_dedupe_timeout_writes_skipped_log(
    db_session, dirs, project, cycle, builtin_tpl
):
    """开启去重 + 超时也没新文件 → 异步线程写 SKIPPED dedupe_timeout 日志"""
    cyc, _ = cycle
    open(os.path.join(dirs["scan"], "STUCK.txt"), "w").write("STUCK_VAL")

    rule = _mk_rule(db_session, builtin_tpl, dirs,
                     latest_file_strategy="mtime",
                     dedupe_same_filename=True,
                     dedupe_retry_max_sec=1,        # 1 秒就超时
                     dedupe_retry_interval_ms=100)
    rule.last_used_input_filename = "STUCK.txt"
    db_session.commit()

    dispatch_cycle_end_export(db_session, channel_id=0,
                                cycle_id=cyc.id, project_id=project.id)

    # 等待异步超时
    time.sleep(1.5)

    db_session.expire_all()
    logs = db_session.query(ExportRunLog).filter(
        ExportRunLog.rule_id == rule.id
    ).order_by(ExportRunLog.id.desc()).all()
    # 至少有 2 条日志: 主线程 queued + 异步 timeout
    statuses = [(l.status, l.skip_reason or "", l.error_msg or "") for l in logs]
    print("logs:", statuses)
    has_timeout = any("dedupe_timeout" in (l.skip_reason or "") for l in logs)
    assert has_timeout, f"应有 dedupe_timeout 日志, 实际: {statuses}"

    # 输出目录不应有新文件 (跳过本规则)
    out_files = os.listdir(dirs["out"])
    assert out_files == [], f"超时应跳过, 不写文件, 实际: {out_files}"
