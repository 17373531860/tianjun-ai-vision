"""Phase 2 集成链路测试 — 端到端验证 cycle_end 后的完整链路。

测试链路：
  真实 DetectionCycle (DB)
    → dispatch_cycle_end_export
      → 查 ExportRealtimeRule (启用 + cycle_end + filters)
      → build_cycle_context (真实读 DB, 构造 cycle/project/stats)
      → render_to_file (真 Jinja2 渲染 + 真磁盘 IO)
      → 文件落地 ✓
      → ExportRunLog 记录 ✓

不依赖摄像头/视频/推理，但走真实 DB + 真实文件系统 + 真实 Jinja2。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from backend.db.database import SessionLocal
from backend.models.models import (
    Project, Model, DetectionSession, DetectionCycle, StepRecord
)
from backend.models.export_models import ExportTemplate, ExportRealtimeRule, ExportRunLog
from backend.services.export_realtime import dispatch_cycle_end_export


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def real_project(db_session, tmp_path):
    """创建真实项目（含 periodic_actions + 模型 + 步骤）"""
    # 模型记录
    fake_model_path = str(tmp_path / "fake_model.pt")
    open(fake_model_path, "wb").write(b"fake model bytes")

    model = Model(
        name="test_model",
        file_path=fake_model_path,
        file_name="fake_model.pt",
        file_size=len(b"fake model bytes"),
        labels=["A", "B", "C", "D", "E"],
    )
    db_session.add(model)
    db_session.commit()

    # 项目记录
    project = Project(
        name=f"集成测试项目_{int(datetime.now().timestamp())}",
        task_type="detection",
        logic_mode="sequential",
        default_model_id=model.id,
        steps_config=[
            {"id": "s_A", "label": "A", "enabled": True},
            {"id": "s_B", "label": "B", "enabled": True},
            {"id": "s_C", "label": "C", "enabled": True},
            {"id": "s_D", "label": "D", "enabled": True},
            {"id": "s_E", "label": "E", "enabled": True},
        ],
        events_config=[
            {"id": 1, "name": "OK", "actions": [], "show_notification": True},
            {"id": 2, "name": "NG", "actions": [], "show_notification": True},
            {"id": 10, "name": "清洁到期", "actions": [], "show_notification": True},
            {"id": 20, "name": "清洁超期", "actions": [], "show_notification": True},
        ],
        counters_config=[],
        pipeline_config={
            "periodic_actions": [{
                "id": "pa_int_1",
                "name": "每3轮清洁",
                "enabled": True,
                "trigger_step_ids": ["s_E"],
                "interval": 3,
                "count_basis": "all",
                "reset_policy": "always",
                "due_warning_event_id": 10,
                "overdue_event_id": 20,
                "overdue_repeat": "every_cycle",
            }],
        },
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def cycle_with_session(db_session, real_project):
    """创建一条真实 DetectionSession + DetectionCycle 记录"""
    import uuid as _uuid
    session = DetectionSession(
        session_uuid=_uuid.uuid4().hex,
        project_id=real_project.id,
        start_time=datetime.now() - timedelta(minutes=5),
        end_time=None,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    cycle = DetectionCycle(
        cycle_uuid=_uuid.uuid4().hex,
        session_id=session.id,
        cycle_number=1,
        start_time=datetime.now() - timedelta(seconds=10),
        end_time=datetime.now(),
        duration=10.0,
        is_good=True,
        event_id=1,
        event_name="OK",
        result_reason="顺序正确完成",
        step_sequence=["A", "B", "C", "D"],
    )
    db_session.add(cycle)
    db_session.commit()
    db_session.refresh(cycle)
    return cycle, session


@pytest.fixture
def template_and_rule(db_session, tmp_path):
    """建一个 Template + 一个启用的 Rule"""
    output_dir = str(tmp_path / "rt_output")
    os.makedirs(output_dir, exist_ok=True)

    tpl = ExportTemplate(
        name="集成测试模板",
        format="txt",
        scope="realtime",
        content=(
            "CYCLE_ID={{ cycle.id }}\n"
            "PROJECT={{ project.name }}\n"
            "RESULT={% if cycle.is_good %}Pass{% else %}Fail{% endif %}\n"
            "VERSION={{ app.version }}"
        ),
        is_system=False,
    )
    db_session.add(tpl)
    db_session.commit()
    db_session.refresh(tpl)

    rule = ExportRealtimeRule(
        name="集成测试实时规则",
        enabled=True,
        template_id=tpl.id,
        output_dir=output_dir,
        filename_template="cycle_{{ cycle.id }}.txt",
        input_file_mode="none",
        trigger_event="cycle_end",
    )
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)
    return tpl, rule, output_dir


# ============================================================
# 集成链路测试
# ============================================================
def test_dispatch_cycle_end_export_完整链路_文件落地(
    db_session, cycle_with_session, template_and_rule, real_project
):
    """完整链路：cycle_end → dispatch → 渲染 → 文件落地 + 日志写入"""
    cycle, _session = cycle_with_session
    tpl, rule, output_dir = template_and_rule

    # 端到端调用（模拟 mes_hooks._handle_cycle_end 末尾的调用）
    results = dispatch_cycle_end_export(
        db_session,
        channel_id=0,
        cycle_id=cycle.id,
        project_id=real_project.id,
    )

    # 验证 1: 返回结果含本规则
    assert len(results) >= 1
    rule_result = next((r for r in results if r.get("rule_id") == rule.id), None)
    assert rule_result is not None, f"返回结果未含规则 {rule.id}: {results}"
    assert rule_result.get("status") == "success", \
        f"规则未成功执行: {rule_result}"

    # 验证 2: 文件真的落地了
    files = os.listdir(output_dir)
    assert len(files) == 1, f"应生成 1 个文件, got {files}"
    output_file = os.path.join(output_dir, files[0])
    content = open(output_file, "r", encoding="utf-8").read()
    assert f"CYCLE_ID={cycle.id}" in content, f"文件内容缺 cycle.id, content={content}"
    assert "Pass" in content, f"is_good=True 应渲染为 Pass, content={content}"
    assert real_project.name in content, "应渲染 project.name"

    # 验证 3: ExportRunLog 写入了
    db_session.expire_all()  # 强制重新查询
    logs = db_session.query(ExportRunLog).filter(
        ExportRunLog.rule_id == rule.id
    ).all()
    assert len(logs) >= 1, "应写入至少 1 条 RunLog"
    last_log = logs[-1]
    assert last_log.status == "success"
    assert last_log.cycle_id == cycle.id


def test_channel_filter_skip_其他通道_不落文件(
    db_session, cycle_with_session, template_and_rule, real_project
):
    """规则 channel_filter=[1,2] 时, channel=0 的 cycle 不应触发"""
    cycle, _session = cycle_with_session
    tpl, rule, output_dir = template_and_rule

    rule.channel_filter = [1, 2]
    db_session.commit()

    results = dispatch_cycle_end_export(
        db_session, channel_id=0,
        cycle_id=cycle.id, project_id=real_project.id,
    )

    # 应当被跳过
    skipped = [r for r in results if r.get("status") == "skipped"]
    assert len(skipped) >= 1, f"channel_filter 不匹配应跳过, results={results}"

    # 不应有文件落地
    assert os.listdir(output_dir) == [], "channel_filter 不匹配应不落文件"


def test_disabled_rule_不被触发(
    db_session, cycle_with_session, template_and_rule, real_project
):
    """规则 enabled=False 时不应触发"""
    cycle, _session = cycle_with_session
    tpl, rule, output_dir = template_and_rule

    rule.enabled = False
    db_session.commit()

    results = dispatch_cycle_end_export(
        db_session, channel_id=0,
        cycle_id=cycle.id, project_id=real_project.id,
    )

    rule_result = next((r for r in results if r.get("rule_id") == rule.id), None)
    assert rule_result is None, f"禁用规则不应被处理, results={results}"
    assert os.listdir(output_dir) == []


def test_NG_cycle_渲染_Fail(
    db_session, cycle_with_session, template_and_rule, real_project
):
    """NG cycle 渲染时模板里 is_good=False 应输出 Fail"""
    cycle, _session = cycle_with_session
    tpl, rule, output_dir = template_and_rule

    cycle.is_good = False
    cycle.event_id = 2
    cycle.event_name = "NG"
    db_session.commit()

    dispatch_cycle_end_export(
        db_session, channel_id=0,
        cycle_id=cycle.id, project_id=real_project.id,
    )

    files = os.listdir(output_dir)
    assert files
    content = open(os.path.join(output_dir, files[0]), "r", encoding="utf-8").read()
    assert "Fail" in content, f"NG cycle 应渲染为 Fail, content={content}"


def test_periodic_actions_配置在项目里_完整解析(real_project):
    """验证项目配置真实落库后, periodic_actions 字段能被读出"""
    pa = real_project.pipeline_config["periodic_actions"]
    assert len(pa) == 1
    rule = pa[0]
    assert rule["name"] == "每3轮清洁"
    assert rule["interval"] == 3
    assert rule["count_basis"] == "all"
    assert "s_E" in rule["trigger_step_ids"]
