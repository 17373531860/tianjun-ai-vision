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


# ============================================================
# 跨组通道唯一性 (回归: SQLite JSON.contains 文本子串误判 + PUT 漏校验)
# ============================================================


def test_create_rejects_channel_already_in_other_enabled_group(isolated_client):
    """组 A 有 channel 0, 创建组 B 也带 channel 0 → 拒."""
    isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [0, 1],
    })
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-B", "member_channel_ids": [0, 2],  # 0 已被 A 占
    })
    assert r.status_code == 400
    assert "已属于工位组" in r.json()["detail"]


def test_create_NOT_rejected_by_substring_false_match(isolated_client):
    """回归坑: 组 A=[10, 11], 建组 B 用 channel 1 不能误判 (SQLite JSON 子串 bug)."""
    isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [10, 11],
    })
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-B", "member_channel_ids": [1, 2],  # 1 跟 [10, 11] 无关
    })
    assert r.status_code == 201, f"误命中 = SQLite JSON.contains 子串 bug 复活: {r.text}"


def test_disabled_group_doesnt_occupy_channels(isolated_client):
    """disabled 组占的 channel 可以被新 enabled 组重用."""
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [0, 1], "enabled": False,
    })
    assert r.status_code == 201
    r2 = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-B", "member_channel_ids": [0, 2],  # disabled 的 A 不占
    })
    assert r2.status_code == 201, r2.text


def test_put_member_change_rechecks_uniqueness(isolated_client):
    """PUT 改 members 时也要做跨组唯一性校验 (回归: 之前漏)."""
    isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [0, 1],
    })
    r_b = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-B", "member_channel_ids": [2, 3],
    })
    bid = r_b.json()["id"]

    # B 偷 A 的 channel 0
    r = isolated_client.put(f"/api/v1/channel-groups/{bid}", json={
        "member_channel_ids": [0, 3],
    })
    assert r.status_code == 400, "PUT 漏跨组唯一性校验"
    assert "已属于工位组" in r.json()["detail"]


def test_put_member_change_excludes_self(isolated_client):
    """PUT 自己改自己的 members (不偷别组) 不应自我冲突."""
    r = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [0, 1],
    })
    aid = r.json()["id"]

    # A 把 channel 集合换成 [0, 2] (跟自己的旧成员 0 重叠)
    r2 = isolated_client.put(f"/api/v1/channel-groups/{aid}", json={
        "member_channel_ids": [0, 2],
    })
    assert r2.status_code == 200, f"PUT 没排除 self 导致自冲突: {r2.text}"


def test_put_enable_rechecks_uniqueness(isolated_client):
    """PUT 从 disabled 变 enabled 时, 即便 members 没改也要做唯一性 (防绕过)."""
    isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-A", "member_channel_ids": [0, 1],
    })
    r_b = isolated_client.post("/api/v1/channel-groups", json={
        "name": "Group-B", "member_channel_ids": [0, 2], "enabled": False,
    })
    bid = r_b.json()["id"]

    # B 想从 disabled 翻到 enabled, 但 channel 0 已被 A 占
    r = isolated_client.put(f"/api/v1/channel-groups/{bid}", json={"enabled": True})
    assert r.status_code == 400, "PUT enable 漏唯一性校验"
