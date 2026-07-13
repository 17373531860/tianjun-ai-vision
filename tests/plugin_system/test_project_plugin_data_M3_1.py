"""M3.1: Project 7 个 JSON 字段下的 plugin_data 透传 + 新 PATCH 端点.

客户视角叙事:
  RFC 09 §6.2 让 Project 的 7 个 JSON 配置字段下都允许一个 ``plugin_data: dict``
  保留键, 让客户专属插件存"步骤警告阈值"之类的小众配置, 不污染主程序 schema.

  实际上 Pydantic ``Optional[dict]`` / ``Optional[List[dict]]`` 对子键不做 schema
  校验, ``_apply_pipeline_config`` 也用 ``.get(key)`` 拿自家键, 所以 plugin_data
  **天然透传**. 本文件作守护契约, 防未来重构时不小心把未知键 pop / 过滤掉.

  RFC 09 §6.5 / AC-M3-1 要求新增端点 ``PUT /api/v1/projects/{id}/plugin-data``,
  让插件能精准只写 ``<scope>.plugin_data.<customer_code>`` 子树, 不动主 schema.

  本测试覆盖:
    - 透传守护 (5 个: 7 个 JSON 字段 CRUD 不丢 plugin_data)
    - 新端点 PATCH 写入 (4 个: dict/list scope + 浅合并 + 不污染其它客户)
    - 新端点参数校验 (5 个: scope 白名单 / index 互斥 / customer_code 字符 / data 类型 / 项目不存在)

14 个测试. 测试隔离: TestClient + in-memory SQLite override.
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
def isolated_client(tmp_path, monkeypatch):
    """构造一个独立 DB 的 FastAPI TestClient.

    - 用独立 SQLite 文件代替主 DB
    - 关掉 auth (默认 disabled, 与生产默认一致), 让 require_perm 直接放行
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "test_m31.db"
    engine = create_engine(f"sqlite:///{db_path}")

    from backend.db.database import Base
    from backend.models import models, plugin_models, mes_models, export_models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    TestSessionLocal = sessionmaker(bind=engine)

    from backend.db import database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", TestSessionLocal)

    # FastAPI 路由用 Depends(get_db), 覆盖它指向独立 DB
    from backend.db.database import get_db
    from backend.main import app

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db

    # 鉴权默认关闭, require_perm 直接放行 — 与客户出厂默认一致
    # (v3.10.0: auth_enabled=False 时所有 perm 隐式 super)
    yield TestClient(app)

    app.dependency_overrides.clear()


def _create_project(client, **extra):
    """创建一个基本项目, extra 可覆盖任意字段."""
    body = {
        "name": extra.get("name", "test_proj_m31"),
        "task_type": "detection",
        "pipeline_config": extra.get("pipeline_config", {"sequence_order": ["a", "b"]}),
        "logic_mode": "sequential",
        "steps_config": extra.get("steps_config", [
            {"step_id": 1, "step_label": "step_1", "max_frames": 100},
        ]),
        "events_config": extra.get("events_config", [
            {"event_id": "e1", "name": "ev1"},
        ]),
        "counters_config": extra.get("counters_config", [
            {"name": "总产量", "value": 0},
        ]),
        "alarm_config": extra.get("alarm_config", {"enabled": True}),
        "detection_config": extra.get("detection_config", {"box_color": "red"}),
        "data_config": extra.get("data_config", {"export_csv": True}),
    }
    r = client.post("/api/v1/projects", json=body)
    assert r.status_code == 201, f"create_project failed: {r.text}"
    return r.json()


# ============================================================
# A. 透传守护 — 7 个 JSON 字段下的 plugin_data 不被 CRUD 流程丢
# ============================================================


def test_pipeline_config_plugin_data_roundtrip(isolated_client):
    """POST + GET: pipeline_config.plugin_data 透传不丢."""
    p = _create_project(isolated_client, pipeline_config={
        "sequence_order": ["a"],
        "plugin_data": {"hb_foo": {"x": 1}},
    })
    r = isolated_client.get(f"/api/v1/projects/{p['id']}")
    assert r.status_code == 200
    data = r.json()
    assert data["pipeline_config"]["plugin_data"] == {"hb_foo": {"x": 1}}
    # 主程序自家键也不丢
    assert data["pipeline_config"]["sequence_order"] == ["a"]


def test_steps_config_plugin_data_roundtrip(isolated_client):
    """steps_config 是 list, 每项的 plugin_data 都要透传."""
    p = _create_project(isolated_client, steps_config=[
        {"step_id": 1, "step_label": "s1", "max_frames": 100,
         "plugin_data": {"hb_foo": {"warn_duration_ms": 800}}},
        {"step_id": 2, "step_label": "s2", "max_frames": 50,
         "plugin_data": {"other_cust": {"y": "z"}}},
    ])
    r = isolated_client.get(f"/api/v1/projects/{p['id']}")
    data = r.json()
    assert data["steps_config"][0]["plugin_data"] == {"hb_foo": {"warn_duration_ms": 800}}
    assert data["steps_config"][1]["plugin_data"] == {"other_cust": {"y": "z"}}


def test_all_seven_json_fields_plugin_data_passthrough(isolated_client):
    """7 个 JSON 字段每个都带 plugin_data, 全部要透传."""
    p = _create_project(isolated_client,
        pipeline_config={"plugin_data": {"cc": {"a": 1}}},
        steps_config=[{"step_id": 1, "step_label": "s",
                       "plugin_data": {"cc": {"b": 2}}}],
        events_config=[{"event_id": "e1",
                        "plugin_data": {"cc": {"c": 3}}}],
        counters_config=[{"name": "c1",
                          "plugin_data": {"cc": {"d": 4}}}],
        alarm_config={"plugin_data": {"cc": {"e": 5}}},
        detection_config={"plugin_data": {"cc": {"f": 6}}},
        data_config={"plugin_data": {"cc": {"g": 7}}},
    )
    r = isolated_client.get(f"/api/v1/projects/{p['id']}")
    data = r.json()
    assert data["pipeline_config"]["plugin_data"]["cc"]["a"] == 1
    assert data["steps_config"][0]["plugin_data"]["cc"]["b"] == 2
    assert data["events_config"][0]["plugin_data"]["cc"]["c"] == 3
    assert data["counters_config"][0]["plugin_data"]["cc"]["d"] == 4
    assert data["alarm_config"]["plugin_data"]["cc"]["e"] == 5
    assert data["detection_config"]["plugin_data"]["cc"]["f"] == 6
    assert data["data_config"]["plugin_data"]["cc"]["g"] == 7


def test_put_update_preserves_plugin_data(isolated_client):
    """PUT update 改其它字段, 不应丢 plugin_data."""
    p = _create_project(isolated_client, alarm_config={
        "enabled": True,
        "plugin_data": {"hb_foo": {"x": 1}},
    })
    # PUT 改其它字段 (注意: 这种调用模式不是 PATCH, 是整字段替换)
    # 用户必须把 plugin_data 也一起带回去 — 这是 RFC 09 §6.2 的约束.
    r = isolated_client.put(f"/api/v1/projects/{p['id']}", json={
        "alarm_config": {
            "enabled": False,
            "plugin_data": {"hb_foo": {"x": 1}},  # 必须显式带回
        },
    })
    assert r.status_code == 200
    assert r.json()["alarm_config"]["plugin_data"] == {"hb_foo": {"x": 1}}


def test_apply_pipeline_config_does_not_drop_plugin_data(isolated_client):
    """_apply_pipeline_config (VSM 应用项目) 用 .get(key) 拿键, plugin_data 子键
    保留在 dict 中 (虽然 VSM 不消费它). 这是与"客户插件能在 step_change hook
    通过 ctx 拿到 plugin_data"的契约前提.
    """
    from backend.api.source_project_config_apply import _apply_pipeline_config
    pipeline_config = {
        "sequence_order": ["a"],
        "settlement_mode": "first_step",
        "plugin_data": {"hb_foo": {"warn_duration_ms": 800}},
    }

    # 用一个简单 fake h
    class _FakeH:
        _simultaneous_groups = None
        _sim_group_buffers = None
        settlement_mode = None
        idle_timeout_seconds = None
        cycle_max_duration = None
        step_strict_order = {}
        def _get_first_sequence_step_label(self):
            return None
        def _get_last_sequence_step_label(self):
            return None
    h = _FakeH()
    _apply_pipeline_config(h, {}, pipeline_config)

    # 配置 dict 本身还保留 plugin_data (VSM 不动它, 让插件通过 hook ctx 拿)
    assert pipeline_config["plugin_data"] == {"hb_foo": {"warn_duration_ms": 800}}
    # 主程序自家键已应用
    assert h.settlement_mode == "first_step"


# ============================================================
# B. 新端点 PATCH /plugin-data — 写入语义
# ============================================================


def test_patch_dict_scope_writes_plugin_data(isolated_client):
    """PATCH dict 类型 scope (例: alarm_config) → 写入 plugin_data.<cc>."""
    p = _create_project(isolated_client)
    r = isolated_client.put(f"/api/v1/projects/{p['id']}/plugin-data", json={
        "customer_code": "hb_foo",
        "scope": "alarm_config",
        "data": {"warn_threshold": 800},
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["alarm_config"]["plugin_data"]["hb_foo"] == {"warn_threshold": 800}
    # 主程序原字段 (enabled=True) 保留
    assert data["alarm_config"]["enabled"] is True


def test_patch_list_scope_writes_plugin_data(isolated_client):
    """PATCH list 类型 scope (例: steps_config[0]) → 写入对应项的 plugin_data.<cc>."""
    p = _create_project(isolated_client)
    r = isolated_client.put(f"/api/v1/projects/{p['id']}/plugin-data", json={
        "customer_code": "hb_foo",
        "scope": "steps_config",
        "index": 0,
        "data": {"warn_duration_ms": 800},
    })
    assert r.status_code == 200, r.text
    step = r.json()["steps_config"][0]
    assert step["plugin_data"]["hb_foo"] == {"warn_duration_ms": 800}
    assert step["step_label"] == "step_1"  # 主程序字段保留


def test_patch_merges_shallowly(isolated_client):
    """同 customer_code 多次 PATCH 浅合并, 不删旧 key."""
    p = _create_project(isolated_client)
    isolated_client.put(f"/api/v1/projects/{p['id']}/plugin-data", json={
        "customer_code": "hb_foo",
        "scope": "alarm_config",
        "data": {"a": 1, "b": 2},
    })
    r = isolated_client.put(f"/api/v1/projects/{p['id']}/plugin-data", json={
        "customer_code": "hb_foo",
        "scope": "alarm_config",
        "data": {"b": 99, "c": 3},  # 改 b, 加 c
    })
    plugin_data = r.json()["alarm_config"]["plugin_data"]["hb_foo"]
    assert plugin_data == {"a": 1, "b": 99, "c": 3}


def test_patch_does_not_affect_other_customer(isolated_client):
    """A 客户的 PATCH 不动 B 客户的 plugin_data."""
    p = _create_project(isolated_client, alarm_config={
        "enabled": True,
        "plugin_data": {"cust_a": {"x": "a"}, "cust_b": {"y": "b"}},
    })
    isolated_client.put(f"/api/v1/projects/{p['id']}/plugin-data", json={
        "customer_code": "cust_a",
        "scope": "alarm_config",
        "data": {"x": "A_NEW"},
    })
    r = isolated_client.get(f"/api/v1/projects/{p['id']}")
    pd = r.json()["alarm_config"]["plugin_data"]
    assert pd["cust_a"] == {"x": "A_NEW"}
    assert pd["cust_b"] == {"y": "b"}  # 完全不动


# ============================================================
# C. 新端点参数校验
# ============================================================


def test_patch_rejects_invalid_scope(isolated_client):
    """scope 不在白名单 → 400."""
    p = _create_project(isolated_client)
    r = isolated_client.put(f"/api/v1/projects/{p['id']}/plugin-data", json={
        "customer_code": "hb_foo",
        "scope": "name",  # 主程序字段, 不是 JSON 配置字段
        "data": {"x": 1},
    })
    assert r.status_code == 400
    assert "scope" in r.json()["detail"]


def test_patch_rejects_list_scope_without_index(isolated_client):
    """list 字段 (steps_config) 不给 index → 400."""
    p = _create_project(isolated_client)
    r = isolated_client.put(f"/api/v1/projects/{p['id']}/plugin-data", json={
        "customer_code": "hb_foo",
        "scope": "steps_config",
        "data": {"x": 1},
    })
    assert r.status_code == 400
    assert "index" in r.json()["detail"]


def test_patch_rejects_dict_scope_with_index(isolated_client):
    """dict 字段给 index → 400."""
    p = _create_project(isolated_client)
    r = isolated_client.put(f"/api/v1/projects/{p['id']}/plugin-data", json={
        "customer_code": "hb_foo",
        "scope": "alarm_config",
        "index": 0,
        "data": {"x": 1},
    })
    assert r.status_code == 400


def test_patch_rejects_list_index_out_of_range(isolated_client):
    """list index 越界 → 400."""
    p = _create_project(isolated_client)
    r = isolated_client.put(f"/api/v1/projects/{p['id']}/plugin-data", json={
        "customer_code": "hb_foo",
        "scope": "steps_config",
        "index": 99,
        "data": {"x": 1},
    })
    assert r.status_code == 400
    assert "越界" in r.json()["detail"]


def test_patch_rejects_invalid_customer_code(isolated_client):
    """customer_code 含非法字符 (例: 句点 / 斜杠) → 400."""
    p = _create_project(isolated_client)
    r = isolated_client.put(f"/api/v1/projects/{p['id']}/plugin-data", json={
        "customer_code": "hb.foo",  # 句点非法
        "scope": "alarm_config",
        "data": {"x": 1},
    })
    assert r.status_code == 400


def test_patch_rejects_nonexistent_project(isolated_client):
    """项目不存在 → 404."""
    r = isolated_client.put("/api/v1/projects/99999/plugin-data", json={
        "customer_code": "hb_foo",
        "scope": "alarm_config",
        "data": {"x": 1},
    })
    assert r.status_code == 404
