"""v3.7.2 FIX-381-D — 扫码器旁路 cycle_end 实时导出 集成链路测试

模拟客户真实场景:
  1. 客户扫码器在固定目录 (我们用 tmp_path 模拟) 写一个 txt 文件 (含序列号)
  2. 检测结束 cycle_end 时, 自动:
     - 读该目录里 mtime 最新的 txt
     - 用其文件名作为输出文件名 (latest_input_filename helper)
     - 把序列号 + 检测结果 + 步骤时长 + 软件版本号写到客户指定输出目录

走真实 DB + 真实文件系统 + 真实 Jinja2 + builtin 预设模板.
"""
from __future__ import annotations

import os
import uuid as _uuid
from datetime import datetime, timedelta

import pytest

from backend.models.models import (
    Project, Model, DetectionSession, DetectionCycle, StepRecord
)
from backend.models.export_models import ExportTemplate, ExportRealtimeRule, ExportRunLog
from backend.services.export_realtime import dispatch_cycle_end_export
from backend.services.export_seed import seed_builtin_templates


# ============================================================
# Fixtures — 复用 conftest 提供的 db_session
# ============================================================

@pytest.fixture(autouse=True)
def _clean_realtime_rules(db_session):
    """每个测试前清空 ExportRealtimeRule + ExportRunLog (rule 不带回滚, 不清会跨测试污染)."""
    db_session.query(ExportRunLog).delete()
    db_session.query(ExportRealtimeRule).delete()
    db_session.commit()
    yield
    # tearDown 也清一下, 避免给下面更广的回归测试遗留脏数据
    db_session.query(ExportRunLog).delete()
    db_session.query(ExportRealtimeRule).delete()
    db_session.commit()


@pytest.fixture
def scanner_dirs(tmp_path):
    """模拟客户扫码器输入目录 + 我们的输出目录"""
    scan_in = tmp_path / "scanner_input"
    scan_in.mkdir()
    out = tmp_path / "scanner_output"
    out.mkdir()
    return {"scan_in": str(scan_in), "out": str(out)}


@pytest.fixture
def real_project_with_steps(db_session, tmp_path):
    fake_model_path = str(tmp_path / "fake.pt")
    open(fake_model_path, "wb").write(b"fake")
    model = Model(
        name="m", file_path=fake_model_path, file_name="fake.pt",
        file_size=4, labels=["取件", "装配", "检查"],
    )
    db_session.add(model)
    db_session.commit()

    project = Project(
        name=f"扫码旁路测试_{int(datetime.now().timestamp())}",
        task_type="detection",
        logic_mode="sequential",
        default_model_id=model.id,
        steps_config=[
            {"id": "s1", "label": "取件", "enabled": True},
            {"id": "s2", "label": "装配", "enabled": True},
            {"id": "s3", "label": "检查", "enabled": True},
        ],
        events_config=[
            {"id": 1, "name": "OK", "show_notification": True},
            {"id": 2, "name": "NG", "show_notification": True},
        ],
        counters_config=[],
        pipeline_config={},
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def cycle_with_3_steps(db_session, real_project_with_steps):
    """OK cycle + 3 个步骤"""
    session = DetectionSession(
        session_uuid=_uuid.uuid4().hex,
        project_id=real_project_with_steps.id,
        start_time=datetime.now() - timedelta(minutes=2),
        end_time=None,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    cycle = DetectionCycle(
        cycle_uuid=_uuid.uuid4().hex,
        session_id=session.id,
        cycle_number=1,
        start_time=datetime.now() - timedelta(seconds=12),
        end_time=datetime.now(),
        duration=12.0,
        is_good=True,
        event_id=1,
        event_name="OK",
        result_reason="顺序正确",
        step_sequence=["取件", "装配", "检查"],
    )
    db_session.add(cycle)
    db_session.commit()
    db_session.refresh(cycle)

    # 3 个真实 StepRecord (字段以 backend/models/models.py StepRecord 为准)
    for idx, (label, dur) in enumerate([("取件", 2.34), ("装配", 5.67), ("检查", 1.89)]):
        sr = StepRecord(
            record_uuid=_uuid.uuid4().hex[:12],
            cycle_id=cycle.id,
            step_id=f"s{idx+1}",
            step_label=label,
            step_order=idx,
            start_time=cycle.start_time + timedelta(seconds=idx * 4),
            end_time=cycle.start_time + timedelta(seconds=idx * 4 + dur),
            duration=dur,
            confidence=0.95,
            is_valid=True,
        )
        db_session.add(sr)
    db_session.commit()
    return cycle, session


@pytest.fixture
def builtin_scanner_template(db_session):
    """从 export_seed 加载系统预设, 返回 builtin_scanner_bypass_3line_txt 模板"""
    seed_builtin_templates()
    tpl = db_session.query(ExportTemplate).filter(
        ExportTemplate.builtin_id == "builtin_scanner_bypass_3line_txt"
    ).first()
    assert tpl is not None, "系统预设 builtin_scanner_bypass_3line_txt 未注册"
    return tpl


# ============================================================
# 主测试 — 客户场景全链路
# ============================================================

def test_scanner_bypass_full_chain_with_builtin_preset(
    db_session, scanner_dirs, real_project_with_steps,
    cycle_with_3_steps, builtin_scanner_template,
):
    """客户场景: 扫码 txt 投到输入目录 → cycle_end 触发 → 输出三行 txt 落地"""
    cycle, _session = cycle_with_3_steps

    # 1) 客户扫码器在输入目录里"扫码生成"一个 txt
    scan_file_name = "WP20260513_001.txt"
    scan_path = os.path.join(scanner_dirs["scan_in"], scan_file_name)
    with open(scan_path, "w", encoding="utf-8") as f:
        f.write("WP20260513_001")

    # 2) 用系统预设建实时规则
    rule = ExportRealtimeRule(
        name="扫码器旁路-集成测试",
        enabled=True,
        template_id=builtin_scanner_template.id,
        output_dir=scanner_dirs["out"],
        # 输出文件名跟扫码 txt 同名 (latest_input_filename 自动从 rule.input_dir 取)
        filename_template="{{ latest_input_filename() }}",
        input_file_mode="none",  # 不用 append, 内容靠 helper 取
        input_dir=scanner_dirs["scan_in"],
        trigger_event="cycle_end",
        encoding="utf-8",
        newline="crlf",  # Windows 客户
    )
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)

    # 3) 触发 cycle_end
    results = dispatch_cycle_end_export(
        db_session, channel_id=0,
        cycle_id=cycle.id, project_id=real_project_with_steps.id,
    )

    # 4) 验证规则执行结果
    rule_result = next((r for r in results if r.get("rule_id") == rule.id), None)
    assert rule_result is not None, f"返回结果未含规则 {rule.id}: {results}"
    assert rule_result.get("status") == "success", \
        f"规则未成功: {rule_result}"

    # 5) 验证输出文件存在 + 跟扫码 txt 同名
    out_files = os.listdir(scanner_dirs["out"])
    assert scan_file_name in out_files, \
        f"输出文件名应跟扫码 txt 同名 ({scan_file_name}), 实际: {out_files}"
    out_path = os.path.join(scanner_dirs["out"], scan_file_name)

    # 6) 验证内容 (默认换行 crlf)
    content = open(out_path, "r", encoding="utf-8", newline="").read()
    # CRLF 必须存在
    assert "\r\n" in content, f"换行应为 CRLF, content={content!r}"

    lines = content.splitlines()
    # 关键断言:
    assert lines[0] == "WP20260513_001", f"第1行应为序列号, got {lines[0]!r}"
    assert lines[1] == "", f"第2行应为空行, got {lines[1]!r}"
    assert lines[2] == "合格", f"第3行应为合格 (cycle.is_good=True), got {lines[2]!r}"
    assert "取件: 2.34s" in lines[3], f"第4行应含步骤时长, lines={lines}"
    assert "装配: 5.67s" in lines[3]
    assert "检查: 1.89s" in lines[3]
    assert " | " in lines[3], "步骤间应用 | 分隔"
    # 第5行版本号 (实际 app.version 取自 backend, 不假设具体值; 但不能为空)
    assert lines[4].strip() != "", f"第5行应为版本号, got {lines[4]!r}"

    # 7) 验证扫码 txt 没被改 (客户自己清理, 我们只读)
    assert os.path.exists(scan_path)
    assert open(scan_path, "r", encoding="utf-8").read() == "WP20260513_001"

    # 8) 验证 ExportRunLog 写入
    db_session.expire_all()
    log = db_session.query(ExportRunLog).filter(
        ExportRunLog.rule_id == rule.id
    ).order_by(ExportRunLog.id.desc()).first()
    assert log is not None
    assert log.status == "success"
    assert log.cycle_id == cycle.id


def test_scanner_bypass_ng_cycle_renders_buhege(
    db_session, scanner_dirs, real_project_with_steps,
    cycle_with_3_steps, builtin_scanner_template,
):
    """NG cycle → 第3行渲染为「不合格」"""
    cycle, _session = cycle_with_3_steps
    cycle.is_good = False
    cycle.event_name = "NG"
    cycle.event_id = 2
    db_session.commit()

    with open(os.path.join(scanner_dirs["scan_in"], "WP_NG.txt"), "w", encoding="utf-8") as f:
        f.write("WP_NG_001")

    rule = ExportRealtimeRule(
        name="NG 测试", enabled=True,
        template_id=builtin_scanner_template.id,
        output_dir=scanner_dirs["out"],
        filename_template="{{ latest_input_filename() }}",
        input_file_mode="none", input_dir=scanner_dirs["scan_in"],
        trigger_event="cycle_end", encoding="utf-8", newline="lf",
    )
    db_session.add(rule)
    db_session.commit()

    dispatch_cycle_end_export(
        db_session, channel_id=0, cycle_id=cycle.id,
        project_id=real_project_with_steps.id,
    )

    out_path = os.path.join(scanner_dirs["out"], "WP_NG.txt")
    assert os.path.exists(out_path), f"输出应存在, dir={os.listdir(scanner_dirs['out'])}"
    content = open(out_path, "r", encoding="utf-8").read()
    assert "WP_NG_001" in content
    assert "不合格" in content


def test_scanner_bypass_picks_latest_txt(
    db_session, scanner_dirs, real_project_with_steps,
    cycle_with_3_steps, builtin_scanner_template,
):
    """目录里多个 txt 时, 取 mtime 最新那个"""
    import time
    cycle, _ = cycle_with_3_steps

    # 老 txt
    with open(os.path.join(scanner_dirs["scan_in"], "OLD.txt"), "w", encoding="utf-8") as f:
        f.write("OLD_SN")
    time.sleep(0.05)
    # 新 txt
    with open(os.path.join(scanner_dirs["scan_in"], "NEW.txt"), "w", encoding="utf-8") as f:
        f.write("NEW_SN")

    rule = ExportRealtimeRule(
        name="多 txt 测试", enabled=True,
        template_id=builtin_scanner_template.id,
        output_dir=scanner_dirs["out"],
        filename_template="{{ latest_input_filename() }}",
        input_file_mode="none", input_dir=scanner_dirs["scan_in"],
        trigger_event="cycle_end", encoding="utf-8", newline="lf",
    )
    db_session.add(rule)
    db_session.commit()

    dispatch_cycle_end_export(
        db_session, channel_id=0, cycle_id=cycle.id,
        project_id=real_project_with_steps.id,
    )

    # 输出应只用 NEW.txt
    out_files = os.listdir(scanner_dirs["out"])
    assert "NEW.txt" in out_files
    assert "OLD.txt" not in out_files
    content = open(os.path.join(scanner_dirs["out"], "NEW.txt"), "r", encoding="utf-8").read()
    assert "NEW_SN" in content
    assert "OLD_SN" not in content


def test_scanner_bypass_empty_dir_still_succeeds(
    db_session, scanner_dirs, real_project_with_steps,
    cycle_with_3_steps, builtin_scanner_template,
):
    """扫码目录空 (客户还没扫码就开始检测) → 渲染照样成功, 首行空,
    其他三行该有的还有, cycle_end 链路不能崩."""
    cycle, _ = cycle_with_3_steps

    rule = ExportRealtimeRule(
        name="空目录测试", enabled=True,
        template_id=builtin_scanner_template.id,
        output_dir=scanner_dirs["out"],
        # filename_template 用 cycle_id 兜底 (latest_input_filename 会返空导致渲染空文件名)
        filename_template="cycle_{{ cycle.id }}.txt",
        input_file_mode="none", input_dir=scanner_dirs["scan_in"],
        trigger_event="cycle_end", encoding="utf-8", newline="lf",
    )
    db_session.add(rule)
    db_session.commit()

    results = dispatch_cycle_end_export(
        db_session, channel_id=0, cycle_id=cycle.id,
        project_id=real_project_with_steps.id,
    )
    rule_result = next((r for r in results if r.get("rule_id") == rule.id), None)
    assert rule_result is not None
    assert rule_result.get("status") == "success", \
        f"空目录不应让 cycle_end 失败, got {rule_result}"

    out_files = os.listdir(scanner_dirs["out"])
    assert len(out_files) == 1
    content = open(os.path.join(scanner_dirs["out"], out_files[0]),
                   "r", encoding="utf-8").read()
    lines = content.splitlines()
    # 首行空 (扫码内容空)
    assert lines[0] == ""
    # OK/NG / 步骤时长 / 版本 该有的还有
    assert "合格" in content
    assert "取件: 2.34s" in content
