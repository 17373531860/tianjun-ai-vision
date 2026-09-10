"""v3.7.2 ExportTemplate.default_rule_config 验收测试

覆盖:
  1. 扫码器旁路预设带上推荐 rule 配置 (C 策略 + 100ms 稳定 + 60s 最大年龄)
  2. 其他预设 default_rule_config 留 None (不影响他们)
  3. 客户 clone 系统预设后, default_rule_config 跟着拷贝过去
  4. 客户修改 clone 后的模板, default_rule_config 不丢
  5. seed 重跑 — 系统预设的 default_rule_config 会被覆盖回最新版
     (代表"客户改 rule 字段时是去改 Rule 行, 不是模板)
"""
from __future__ import annotations

import pytest

from backend.models.export_models import ExportTemplate
from backend.services.export_seed import (
    seed_builtin_templates, _DEFAULT_RULE_SCANNER_BYPASS, _BUILTIN_TEMPLATES,
)


@pytest.fixture(autouse=True)
def _clean_templates(db_session):
    """每个测试前清掉所有 ExportTemplate, 避免跨测试污染."""
    db_session.query(ExportTemplate).delete()
    db_session.commit()
    yield
    db_session.query(ExportTemplate).delete()
    db_session.commit()


def test_scanner_bypass_preset_carries_default_rule_config(db_session):
    """扫码器旁路预设 seed 后 default_rule_config 应包含 12 个推荐字段"""
    seed_builtin_templates()
    tpl = db_session.query(ExportTemplate).filter(
        ExportTemplate.builtin_id == "builtin_scanner_bypass_3line_txt"
    ).first()
    assert tpl is not None
    cfg = tpl.default_rule_config
    assert isinstance(cfg, dict), f"default_rule_config 应为 dict, 实际: {type(cfg)}"

    # 关键场景字段
    assert cfg["latest_file_strategy"] == "cycle_start_snapshot"  # C 策略
    assert cfg["latest_file_wait_stable_ms"] == 100
    assert cfg["latest_file_max_age_sec"] == 60
    assert cfg["filename_template"] == "{{ latest_input_filename() }}"
    assert cfg["newline"] == "crlf"
    assert cfg["input_file_mode"] == "none"
    assert cfg["trigger_event"] == "cycle_end"
    assert cfg["dedupe_same_filename"] is False
    assert cfg["dedupe_retry_max_sec"] == 5
    assert cfg["dedupe_retry_interval_ms"] == 100


# 带推荐 rule 配置的预设白名单: 扫码器旁路 (v3.7.2) + 多码采集 txt (v3.56)
_PRESETS_WITH_DRC = {
    "builtin_scanner_bypass_3line_txt",
    "builtin_scan_group_txt",
}


def test_scan_group_preset_carries_default_rule_config(db_session):
    """v3.56 多码采集 txt 预设: scan_group_end 触发 + 工件码+时间命名 + Excel 兼容编码"""
    seed_builtin_templates()
    tpl = db_session.query(ExportTemplate).filter(
        ExportTemplate.builtin_id == "builtin_scan_group_txt"
    ).first()
    assert tpl is not None
    cfg = tpl.default_rule_config
    assert isinstance(cfg, dict)
    assert cfg["trigger_event"] == "scan_group_end"
    assert cfg["encoding"] == "utf-8-sig"      # Excel 打开不乱码 (确认单 6.6/6.7)
    assert cfg["newline"] == "crlf"
    assert "scan_collect.workpiece_sn" in cfg["filename_template"]  # 工件码+时间 (确认单 6.3)


def test_other_presets_have_no_default_rule_config(db_session):
    """其他预设 (白名单外) 不带 default_rule_config — 避免误用"""
    seed_builtin_templates()
    for tpl_spec in _BUILTIN_TEMPLATES:
        if tpl_spec["builtin_id"] in _PRESETS_WITH_DRC:
            continue
        tpl = db_session.query(ExportTemplate).filter(
            ExportTemplate.builtin_id == tpl_spec["builtin_id"]
        ).first()
        assert tpl is not None
        assert tpl.default_rule_config in (None, {}), \
            f"{tpl_spec['builtin_id']} 不该带 default_rule_config"


def test_clone_template_carries_default_rule_config(db_session):
    """clone 系统预设 (扫码器旁路) → 客户副本仍带 default_rule_config"""
    from backend.api.export_custom import clone_template
    seed_builtin_templates()
    src = db_session.query(ExportTemplate).filter(
        ExportTemplate.builtin_id == "builtin_scanner_bypass_3line_txt"
    ).first()

    result = clone_template(src.id, new_name="客户扫码器旁路", db=db_session)
    cloned = db_session.query(ExportTemplate).filter(
        ExportTemplate.id == result["id"]
    ).first()
    assert cloned.is_system is False
    assert cloned.builtin_id is None
    # 关键: 跟着复制了
    assert cloned.default_rule_config == _DEFAULT_RULE_SCANNER_BYPASS
    # 副本被修改不影响原系统预设
    cloned.default_rule_config["latest_file_strategy"] = "mtime"
    db_session.commit()
    db_session.refresh(src)
    assert src.default_rule_config["latest_file_strategy"] == "cycle_start_snapshot"


def test_seed_overwrites_drc_for_system_template(db_session):
    """seed 重跑 — 系统预设的 default_rule_config 总是被覆盖回最新版"""
    seed_builtin_templates()
    src = db_session.query(ExportTemplate).filter(
        ExportTemplate.builtin_id == "builtin_scanner_bypass_3line_txt"
    ).first()
    # 模拟"客户改了系统预设的 drc"
    src.default_rule_config = {"latest_file_strategy": "mtime", "junk": 1}
    db_session.commit()

    # 再 seed 一次 — 应该把 drc 覆盖回来
    seed_builtin_templates()
    db_session.refresh(src)
    assert src.default_rule_config == _DEFAULT_RULE_SCANNER_BYPASS


def test_create_template_with_drc(db_session):
    """POST /templates 允许客户自建模板时携带 default_rule_config"""
    from backend.api.export_custom import create_template, TemplateCreate
    payload = TemplateCreate(
        name="客户自建带配",
        format="txt",
        content="hello",
        scope="realtime",
        default_rule_config={
            "latest_file_strategy": "mtime_stable",
            "newline": "lf",
        },
    )
    result = create_template(payload, db=db_session)
    row = db_session.query(ExportTemplate).filter(
        ExportTemplate.id == result["id"]
    ).first()
    assert row.default_rule_config["latest_file_strategy"] == "mtime_stable"


def test_update_template_can_modify_drc(db_session):
    """PUT /templates/{id} 可以改自建模板的 drc"""
    from backend.api.export_custom import (
        create_template, update_template, TemplateCreate, TemplateUpdate
    )
    r = create_template(TemplateCreate(name="t", format="txt", content="x",
                                          scope="realtime"), db=db_session)
    update_template(r["id"], TemplateUpdate(
        default_rule_config={"newline": "crlf", "dedupe_same_filename": True}
    ), db=db_session)
    row = db_session.query(ExportTemplate).filter(
        ExportTemplate.id == r["id"]
    ).first()
    assert row.default_rule_config["newline"] == "crlf"
    assert row.default_rule_config["dedupe_same_filename"] is True
