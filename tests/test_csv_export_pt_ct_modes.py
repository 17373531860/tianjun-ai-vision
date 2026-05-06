"""验证 /data/export/csv 接受 pt_mode/ct_mode 参数后, CSV 多输出"耗时(平均/秒)"列。

策略：
  - 真实落库 1 个 Session + 3 个 Cycle (duration: 10, 12, 8) + 每 cycle 2 个 step
  - ct_mode='avg' → CSV 应有"耗时(平均/秒)"列, 周期行该列填全局平均 (10+12+8)/3=10.00
  - ct_mode='last'/None → CSV 不应有"耗时(平均/秒)"列
  - pt_mode='avg' → 步骤详情区应有"耗时(平均/秒)"列
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest


@pytest.fixture
def real_session_with_cycles(db_session):
    """落 1 session + 3 cycle + 6 step 到测试 DB; 用唯一 uuid 避免跨测试冲突"""
    from backend.models.models import (
        Project, DetectionSession, DetectionCycle, StepRecord,
    )

    db = db_session
    tag = uuid.uuid4().hex[:8]

    p = Project(name=f"PT/CT Test Proj {tag}", task_type="detect")
    db.add(p)
    db.flush()

    s = DetectionSession(
        project_id=p.id,
        channel_id=0,
        session_uuid=f"sess-{tag}",
        start_time=datetime(2025, 1, 1, 10, 0, 0),
        end_time=datetime(2025, 1, 1, 10, 5, 0),
        total_cycles=3, good_cycles=2, ng_cycles=1,
        avg_cycle_time=10.0,
    )
    db.add(s)
    db.flush()

    cycle_durations = [10.0, 12.0, 8.0]
    for i, dur in enumerate(cycle_durations):
        c = DetectionCycle(
            session_id=s.id,
            cycle_number=i + 1,
            cycle_uuid=f"cyc-{tag}-{i}",
            start_time=datetime(2025, 1, 1, 10, i, 0),
            end_time=datetime(2025, 1, 1, 10, i, int(dur)),
            duration=dur,
            is_good=(i != 1),
            event_name="auto_ok" if i != 1 else "auto_ng",
            step_sequence=["A", "B"],
        )
        db.add(c)
        db.flush()
        # 每 cycle 2 个 step, A 时长 = dur*0.4, B 时长 = dur*0.6
        for j, lbl in enumerate(["A", "B"]):
            step = StepRecord(
                record_uuid=f"step-{tag}-{i}-{lbl}",
                cycle_id=c.id,
                step_label=lbl,
                step_name=f"步骤{lbl}",
                step_order=j + 1,
                start_time=datetime(2025, 1, 1, 10, i, j * 2),
                end_time=datetime(2025, 1, 1, 10, i, j * 2 + 1),
                duration=dur * (0.4 if lbl == "A" else 0.6),
                confidence=0.9,
                is_valid=True,
            )
            db.add(step)

    db.commit()
    return {"project_id": p.id, "session_id": s.id, "session_uuid": s.session_uuid}


def _csv_text(client, params):
    resp = client.get("/api/v1/data/export/csv", params=params)
    assert resp.status_code == 200, f"export failed: {resp.text[:300]}"
    return resp.content.decode("utf-8-sig")


def test_默认无_avg_列(client, real_session_with_cycles):
    """没传 pt_mode/ct_mode 时不应输出 (平均/秒) 列"""
    txt = _csv_text(client, {
        "export_type": "all",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
    })
    assert "耗时(秒)" in txt, "原列必须保留"
    assert "(平均/秒)" not in txt, "未指定 mode 时不应有平均列"


def test_ct_mode_avg_周期添加平均列(client, real_session_with_cycles):
    """ct_mode=avg 时 周期详情 应多一列 耗时(平均/秒)"""
    txt = _csv_text(client, {
        "export_type": "all",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "ct_mode": "avg",
    })
    assert "耗时(秒)" in txt
    assert "耗时(平均/秒)" in txt, "ct_mode=avg 应输出平均列"
    # 全局平均 = (10+12+8)/3 = 10.00 — 在周期数据行里至少出现一次
    assert "10.00" in txt


def test_pt_mode_avg_步骤添加平均列(client, real_session_with_cycles):
    """pt_mode=avg 时 步骤详情 也应多一列 耗时(平均/秒)"""
    txt = _csv_text(client, {
        "export_type": "all",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "pt_mode": "avg",
    })
    assert "耗时(秒)" in txt
    # 步骤详情区表头有这列
    assert "耗时(平均/秒)" in txt
    # 步骤 A 平均时长 = (10+12+8)*0.4/3 = 4.00
    assert "4.00" in txt
    # 步骤 B 平均时长 = (10+12+8)*0.6/3 = 6.00
    assert "6.00" in txt


def test_pt_last_ct_avg_独立组合(client, real_session_with_cycles):
    """PT=last + CT=avg → 周期有平均列, 步骤无"""
    txt = _csv_text(client, {
        "export_type": "all",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "pt_mode": "last",
        "ct_mode": "avg",
    })
    # 周期详情应有"耗时(平均/秒)"
    cycle_section = txt.split("步骤详情")[0]
    assert "耗时(平均/秒)" in cycle_section, "周期详情应有平均列"
    # 步骤详情不应有
    if "步骤详情" in txt:
        step_section_header_line = [
            line for line in txt.splitlines()
            if "步骤序号" in line and "耗时" in line
        ]
        for line in step_section_header_line:
            assert "(平均/秒)" not in line, \
                f"pt_mode=last 时步骤行不应有平均列, 实际: {line}"


def test_current_模式_等同_last(client, real_session_with_cycles):
    """ct_mode=current 在导出场景退化为 last (不输出平均列)"""
    txt = _csv_text(client, {
        "export_type": "all",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "ct_mode": "current",
    })
    assert "耗时(秒)" in txt
    assert "耗时(平均/秒)" not in txt, "current 模式应等同 last (无平均列)"
