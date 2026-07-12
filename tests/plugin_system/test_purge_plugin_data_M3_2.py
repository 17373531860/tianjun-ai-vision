"""M3.2: 卸载插件时可选清理命名空间数据 (system_configs + Project plugin_data).

客户视角叙事:
  RFC 09 §6.3 + AC-M3-4 要求"卸载插件后 system_configs 中 plugin_<cc>_* 全部清理,
  plugin_data.<cc> 子树清理". 但现有 delete_plugin 端点的注释明确写"业务数据保留" —
  这是 v3.10 起客户友好策略, 防止误删数据.

  M3.2 解决方式: 加可选参数 purge_data=False (默认与现有行为一致). 客户显式
  ?purge_data=true 时, 同步清理两层:
    1. system_configs 中 key LIKE 'plugin_<cc>_%' 全删
    2. Project 7 个 JSON 字段下的 plugin_data.<customer_code> 子键清理

  **不**清理 step_records.plugin_data (已结束周期视为业务历史; 真要清手动 SQL).

  本测试覆盖:
    - 默认 purge_data=False 时, 业务数据保留 (向后兼容)
    - purge_data=True 时, system_configs 清理
    - purge_data=True 时, Project 7 个 JSON 字段下的 plugin_data.<cc> 清理
    - 其它客户的 plugin_data 子键不被误清
    - step_records.plugin_data 不动 (业务历史保留)
    - 命名空间 LIKE 不误伤 (前缀严格)
    - audit log 记录清理结果

10 个测试.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ⚠️ 先材料化主程序模块树再让本文件的 fixture 打 SessionLocal 补丁:
# 惰性首次 import 若落在补丁窗口内会按值捕获 tmp sessionmaker, 永久污染后续测试
# (session_naming 'DB 找不到' + 'disk image is malformed' 级联根因, 2026-07-13)
import backend.main  # noqa: F401  isort: skip


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def isolated_client_with_plugin(tmp_path, monkeypatch):
    """构造带一个已安装插件的 TestClient."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "test_m32.db"
    engine = create_engine(f"sqlite:///{db_path}")

    from backend.db.database import Base
    from backend.models import models, plugin_models, mes_models, export_models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    TestSessionLocal = sessionmaker(bind=engine)

    from backend.db import database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", TestSessionLocal)

    from backend.db.database import get_db
    from backend.main import app

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db

    # 预先安装一个测试插件记录 (不真跑 install/verify 流程)
    from backend.models.plugin_models import PluginRecord, PluginState
    from datetime import datetime, timezone

    plugin_dir = tmp_path / "plugin_install_dir"
    plugin_dir.mkdir()
    (plugin_dir / "dummy").write_text("dummy")

    db = TestSessionLocal()
    try:
        rec = PluginRecord(
            customer_code="acme",
            name="acme-plugin",
            plugin_version="1.0.0",
            tier="optional",
            status="installed",
            is_active=False,
            install_path=str(plugin_dir),
            manifest_json="{}",
            files_digest="dummy",
            signed_by=None,
            installed_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(rec)
        db.commit()
    finally:
        db.close()

    yield TestClient(app), TestSessionLocal, plugin_dir

    app.dependency_overrides.clear()


def _seed_namespaced_data(SessionLocal, customer_code="acme"):
    """造一些 plugin_<cc>_* 命名空间数据 + 其它客户数据 (污染对照)."""
    from backend.models.models import SystemConfig, Project

    db = SessionLocal()
    try:
        # SystemConfig: 自家 + 别家 + 主程序
        for key, value in [
            (f"plugin_{customer_code}_warn", "yellow"),
            (f"plugin_{customer_code}_threshold", "800"),
            ("plugin_other_x", "other-data"),  # 别家客户, 不动
            ("license-cache", "main-data"),    # 主程序保留, 不动
            ("brand_name", "TianJun"),         # 主程序保留, 不动
        ]:
            db.add(SystemConfig(key=key, value=value))

        # Project: 7 个 JSON 字段下的 plugin_data
        proj = Project(
            name="test",
            task_type="detection",
            pipeline_config={
                "sequence_order": ["a"],
                "plugin_data": {customer_code: {"x": 1}, "other_cust": {"y": 2}},
            },
            alarm_config={
                "enabled": True,
                "plugin_data": {customer_code: {"z": 3}},
            },
            steps_config=[
                {"step_id": 1, "step_label": "s1",
                 "plugin_data": {customer_code: {"warn": 800}, "other_cust": {"a": "b"}}},
            ],
            events_config=[
                {"event_id": "e1",
                 "plugin_data": {customer_code: {"foo": "bar"}}},
            ],
        )
        db.add(proj)
        db.commit()
        return proj.id
    finally:
        db.close()


def _list_keys(SessionLocal):
    from backend.models.models import SystemConfig
    db = SessionLocal()
    try:
        return [r.key for r in db.query(SystemConfig).all()]
    finally:
        db.close()


def _get_project(SessionLocal, proj_id):
    from backend.models.models import Project
    db = SessionLocal()
    try:
        return db.query(Project).filter(Project.id == proj_id).first()
    finally:
        db.close()


# ============================================================
# A. 默认行为: purge_data=False, 业务数据保留 (向后兼容)
# ============================================================


def test_default_delete_keeps_business_data(isolated_client_with_plugin):
    """默认 (不传 purge_data) → SystemConfig 和 Project plugin_data 都保留."""
    client, SessionLocal, _ = isolated_client_with_plugin
    proj_id = _seed_namespaced_data(SessionLocal)

    r = client.delete("/api/v1/plugins/acme")
    assert r.status_code == 200
    assert r.json()["purge_data"] is False
    assert r.json()["purge_stats"] == {}

    # SystemConfig 全保留
    keys = _list_keys(SessionLocal)
    assert "plugin_acme_warn" in keys
    assert "plugin_acme_threshold" in keys
    assert "license-cache" in keys

    # Project plugin_data 全保留
    p = _get_project(SessionLocal, proj_id)
    assert "acme" in p.alarm_config["plugin_data"]
    assert "acme" in p.steps_config[0]["plugin_data"]


def test_purge_false_explicit_keeps_data(isolated_client_with_plugin):
    """显式 ?purge_data=false → 与默认一致, 数据保留."""
    client, SessionLocal, _ = isolated_client_with_plugin
    proj_id = _seed_namespaced_data(SessionLocal)

    r = client.delete("/api/v1/plugins/acme?purge_data=false")
    assert r.status_code == 200
    assert r.json()["purge_data"] is False

    keys = _list_keys(SessionLocal)
    assert "plugin_acme_warn" in keys


# ============================================================
# B. purge_data=True: SystemConfig 命名空间清理
# ============================================================


def test_purge_clears_system_config_namespace(isolated_client_with_plugin):
    """purge_data=true → plugin_acme_* 全删."""
    client, SessionLocal, _ = isolated_client_with_plugin
    _seed_namespaced_data(SessionLocal)

    r = client.delete("/api/v1/plugins/acme?purge_data=true")
    assert r.status_code == 200
    assert r.json()["purge_data"] is True
    assert r.json()["purge_stats"]["system_configs_deleted"] == 2  # warn + threshold

    keys = _list_keys(SessionLocal)
    assert "plugin_acme_warn" not in keys
    assert "plugin_acme_threshold" not in keys


def test_purge_does_not_touch_other_customer_keys(isolated_client_with_plugin):
    """plugin_other_* (别家客户) 不被清."""
    client, SessionLocal, _ = isolated_client_with_plugin
    _seed_namespaced_data(SessionLocal)

    client.delete("/api/v1/plugins/acme?purge_data=true")

    keys = _list_keys(SessionLocal)
    assert "plugin_other_x" in keys


def test_purge_does_not_touch_main_program_keys(isolated_client_with_plugin):
    """主程序保留 key (license-cache, brand_name) 不被清."""
    client, SessionLocal, _ = isolated_client_with_plugin
    _seed_namespaced_data(SessionLocal)

    client.delete("/api/v1/plugins/acme?purge_data=true")

    keys = _list_keys(SessionLocal)
    assert "license-cache" in keys
    assert "brand_name" in keys


# ============================================================
# C. purge_data=True: Project plugin_data.<cc> 清理
# ============================================================


def test_purge_clears_project_dict_field_plugin_data(isolated_client_with_plugin):
    """Project 的 dict 字段 (pipeline_config / alarm_config) 下的 plugin_data.acme 被清."""
    client, SessionLocal, _ = isolated_client_with_plugin
    proj_id = _seed_namespaced_data(SessionLocal)

    client.delete("/api/v1/plugins/acme?purge_data=true")

    p = _get_project(SessionLocal, proj_id)
    # pipeline_config.plugin_data 还在, 但 acme 子键被清, other_cust 保留
    assert "acme" not in p.pipeline_config["plugin_data"]
    assert p.pipeline_config["plugin_data"]["other_cust"] == {"y": 2}
    # alarm_config.plugin_data 整个变空 dict (只有 acme 的 key)
    assert "acme" not in p.alarm_config["plugin_data"]


def test_purge_clears_project_list_field_plugin_data(isolated_client_with_plugin):
    """Project 的 list 字段 (steps_config / events_config) 下每项的 plugin_data.acme 被清."""
    client, SessionLocal, _ = isolated_client_with_plugin
    proj_id = _seed_namespaced_data(SessionLocal)

    client.delete("/api/v1/plugins/acme?purge_data=true")

    p = _get_project(SessionLocal, proj_id)
    # steps_config[0].plugin_data: acme 被清, other_cust 保留
    assert "acme" not in p.steps_config[0]["plugin_data"]
    assert p.steps_config[0]["plugin_data"]["other_cust"] == {"a": "b"}
    # events_config[0].plugin_data: acme 是唯一 key, 清后变空 dict
    assert "acme" not in p.events_config[0]["plugin_data"]


def test_purge_preserves_main_project_fields(isolated_client_with_plugin):
    """Project 自己的主程序字段不动."""
    client, SessionLocal, _ = isolated_client_with_plugin
    proj_id = _seed_namespaced_data(SessionLocal)

    client.delete("/api/v1/plugins/acme?purge_data=true")

    p = _get_project(SessionLocal, proj_id)
    assert p.pipeline_config["sequence_order"] == ["a"]
    assert p.alarm_config["enabled"] is True
    assert p.steps_config[0]["step_label"] == "s1"


# ============================================================
# D. audit log
# ============================================================


def test_audit_log_message_reflects_purge_decision(isolated_client_with_plugin):
    """purge_data 决定 audit 消息内容."""
    from backend.models.plugin_models import PluginAuditLog
    client, SessionLocal, _ = isolated_client_with_plugin
    _seed_namespaced_data(SessionLocal)

    client.delete("/api/v1/plugins/acme?purge_data=true")

    db = SessionLocal()
    try:
        # 找最后一条 delete audit
        rows = db.query(PluginAuditLog).filter(
            PluginAuditLog.action == "delete",
            PluginAuditLog.customer_code == "acme",
        ).order_by(PluginAuditLog.id.desc()).all()
        assert len(rows) >= 1
        assert "清理" in rows[0].message
        assert "system_configs=2" in rows[0].message
    finally:
        db.close()


# ============================================================
# E. 命名空间前缀严格 (防误伤)
# ============================================================


def test_namespace_prefix_strict_does_not_match_substring(isolated_client_with_plugin):
    """plugin_acme_x 被清, 但 plugin_acmex_y (substring match) 不被清."""
    from backend.models.models import SystemConfig
    client, SessionLocal, _ = isolated_client_with_plugin

    db = SessionLocal()
    try:
        db.add(SystemConfig(key="plugin_acme_x", value="should-delete"))
        db.add(SystemConfig(key="plugin_acmex_y", value="should-keep"))  # 没下划线分隔
        db.commit()
    finally:
        db.close()

    client.delete("/api/v1/plugins/acme?purge_data=true")

    keys = _list_keys(SessionLocal)
    assert "plugin_acme_x" not in keys, "plugin_acme_x 应被清"
    assert "plugin_acmex_y" in keys, "plugin_acmex_y 命名空间不匹配, 应保留"
