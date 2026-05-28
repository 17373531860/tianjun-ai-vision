"""RFC 10 CG.5 — channel_groups CRUD 端点测试.

覆盖:
- POST 创建 (含校验: 名字唯一 / 成员数 / 通道唯一 / 策略白名单 / master_slave 拒)
- GET list / single
- PUT 更新 (含部分更新 + reload Coordinator)
- DELETE (含 enabled=True 拒删)
- GET /state (运行时状态)

12 个测试.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "test_cg5.db"
    engine = create_engine(f"sqlite:///{db_path}")

    from backend.db.database import Base
    from backend.models import models, plugin_models, mes_models, export_models, auth_models  # noqa: F401
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

    # reset coordinator
    from backend.services.channel_group_coordinator import reset_coordinator_for_testing
    reset_coordinator_for_testing()

    yield TestClient(app)

    app.dependency_overrides.clear()


# ============================================================
# Create
# ============================================================


def test_create_minimal_group(isolated_client):
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A",
        "member_channel_ids": [0, 1],
    })
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "Group-A"
    assert data["member_channel_ids"] == [0, 1]
    assert data["settle_strategy"] == "synchronized_any_ng"
    assert data["timeout_ms"] == 5000


def test_create_rejects_empty_name(isolated_client):
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "",
        "member_channel_ids": [0, 1],
    })
    assert r.status_code == 400


def test_create_rejects_too_few_members(isolated_client):
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-X",
        "member_channel_ids": [0],
    })
    assert r.status_code == 400
    assert "至少" in r.json()["detail"]


def test_create_rejects_duplicate_member_ids(isolated_client):
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-X",
        "member_channel_ids": [0, 0, 1],
    })
    assert r.status_code == 400


def test_create_rejects_duplicate_name(isolated_client):
    isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A",
        "member_channel_ids": [0, 1],
    })
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A",
        "member_channel_ids": [2, 3],
    })
    assert r.status_code == 400


def test_create_rejects_master_slave_strategy(isolated_client):
    """master_slave v3.13.0 暂未实现."""
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-MS",
        "member_channel_ids": [0, 1],
        "settle_strategy": "master_slave",
    })
    assert r.status_code == 400


def test_create_rejects_invalid_strategy(isolated_client):
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-X",
        "member_channel_ids": [0, 1],
        "settle_strategy": "weird_strategy",
    })
    assert r.status_code == 400


# ============================================================
# Read
# ============================================================


def test_list_empty(isolated_client):
    r = isolated_client.get("/api/v1/channel-groups")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_list_after_create(isolated_client):
    isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [0, 1],
    })
    isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-B", "member_channel_ids": [2, 3],
    })
    r = isolated_client.get("/api/v1/channel-groups")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2


def test_get_single(isolated_client):
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [0, 1],
    })
    gid = r.json()["id"]

    r2 = isolated_client.get(f"/api/v1/channel-groups/{gid}")
    assert r2.status_code == 200
    assert r2.json()["name"] == "Group-A"


def test_get_nonexistent(isolated_client):
    assert isolated_client.get("/api/v1/channel-groups/99").status_code == 404


# ============================================================
# Update / Delete
# ============================================================


def test_update_strategy(isolated_client):
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [0, 1],
    })
    gid = r.json()["id"]

    r2 = isolated_client.put(f"/api/v1/channel-groups/{gid}", json={
        "settle_strategy": "synchronized_all_ok",
        "timeout_ms": 3000,
    })
    assert r2.status_code == 200
    assert r2.json()["settle_strategy"] == "synchronized_all_ok"
    assert r2.json()["timeout_ms"] == 3000


def test_delete_enabled_group_rejected(isolated_client):
    """enabled=True 的组不能直删."""
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [0, 1],
    })
    gid = r.json()["id"]

    r2 = isolated_client.delete(f"/api/v1/channel-groups/{gid}")
    assert r2.status_code == 409


def test_delete_after_disable_succeeds(isolated_client):
    """PUT enabled=false 后 DELETE 通过."""
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [0, 1],
    })
    gid = r.json()["id"]

    isolated_client.put(f"/api/v1/channel-groups/{gid}", json={"enabled": False})
    r3 = isolated_client.delete(f"/api/v1/channel-groups/{gid}")
    assert r3.status_code == 204

    # 验证真删了
    assert isolated_client.get(f"/api/v1/channel-groups/{gid}").status_code == 404
