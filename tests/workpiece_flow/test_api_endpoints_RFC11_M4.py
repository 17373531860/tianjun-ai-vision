"""RFC 11 M4 — Workpiece Flow REST API.

测试矩阵:
  A. CRUD 基础: GET list / GET id / POST / PUT / DELETE (6)
  B. 校验: 名称重复 / 工位数 < 2 / channel 重复 / 非法 trigger_mode (4)
  C. 与 channel_groups 互斥 (2)
  D. 流水线间互斥 (1)
  E. enabled 状态保护: 不能直接删 enabled / 有 in-flight 不能改 (2)
  F. 历史 runs 查询 (2)
  G. state 端点 (2)

合计 ~19 测试.
"""
from __future__ import annotations

from typing import Optional

import pytest
from fastapi.testclient import TestClient


# =============================================================
# Fixtures
# =============================================================

@pytest.fixture
def client(monkeypatch, tmp_path):
    """启动一个独立的 FastAPI app, DB 用 file 存 tmp_path 下避免互相污染."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("TIANJUN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BACKEND_SKIP_INIT", "1")

    # 重置 coordinator 防 fixture 污染
    from backend.services.workpiece_flow_coordinator import reset_coordinator_for_testing
    reset_coordinator_for_testing()
    from backend.services.channel_group_coordinator import (
        reset_coordinator_for_testing as cg_reset,
    )
    cg_reset()

    # 重建 engine 指向 tmp_path
    import importlib
    import sqlalchemy
    from sqlalchemy.orm import sessionmaker

    # 创建独立 engine
    engine = sqlalchemy.create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )

    # patch db module
    import backend.db.database as db_mod
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "SessionLocal", sessionmaker(bind=engine))

    # 注册全部表
    from backend.db.database import Base
    from backend.models import models as _m  # noqa: F401
    from backend.models import auth_models as _a  # noqa: F401
    from backend.models import mes_models as _mes  # noqa: F401
    from backend.models import export_models as _ex  # noqa: F401
    from backend.models import plugin_models as _p  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # 构造 FastAPI app
    from fastapi import FastAPI
    from backend.api import api_router

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    # 替换 get_db 依赖
    def _override_get_db():
        s = sessionmaker(bind=engine)()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[db_mod.get_db] = _override_get_db

    yield TestClient(app)

    reset_coordinator_for_testing()
    cg_reset()


# =============================================================
# A. CRUD 基础
# =============================================================

class TestCRUD:
    def test_list_empty(self, client):
        r = client.get("/api/v1/workpiece-flows")
        assert r.status_code == 200
        assert r.json() == {"items": []}

    def test_create_and_get(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "line-A",
            "station_channel_ids": [0, 1, 2],
            "trigger_mode": "time_window",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "line-A"
        assert data["station_channel_ids"] == [0, 1, 2]
        assert data["trigger_mode"] == "time_window"
        assert data["enabled"] is False

        # GET by id
        r2 = client.get(f"/api/v1/workpiece-flows/{data['id']}")
        assert r2.status_code == 200
        assert r2.json()["name"] == "line-A"

    def test_list_after_create(self, client):
        client.post("/api/v1/workpiece-flows", json={
            "name": "line-A", "station_channel_ids": [0, 1],
        })
        client.post("/api/v1/workpiece-flows", json={
            "name": "line-B", "station_channel_ids": [2, 3],
        })
        r = client.get("/api/v1/workpiece-flows")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 2
        names = {x["name"] for x in items}
        assert names == {"line-A", "line-B"}

    def test_get_nonexistent(self, client):
        r = client.get("/api/v1/workpiece-flows/9999")
        assert r.status_code == 404

    def test_update(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "line-A", "station_channel_ids": [0, 1],
        })
        flow_id = r.json()["id"]

        r2 = client.put(f"/api/v1/workpiece-flows/{flow_id}", json={
            "fifo_max_in_flight": 5,
            "workpiece_timeout_ms": 120000,
        })
        assert r2.status_code == 200
        assert r2.json()["fifo_max_in_flight"] == 5
        assert r2.json()["workpiece_timeout_ms"] == 120000

    def test_delete_disabled(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "line-A", "station_channel_ids": [0, 1],
        })
        flow_id = r.json()["id"]

        r2 = client.delete(f"/api/v1/workpiece-flows/{flow_id}")
        assert r2.status_code == 204

        r3 = client.get(f"/api/v1/workpiece-flows/{flow_id}")
        assert r3.status_code == 404


# =============================================================
# B. 校验
# =============================================================

class TestValidation:
    def test_duplicate_name(self, client):
        client.post("/api/v1/workpiece-flows", json={
            "name": "dup", "station_channel_ids": [0, 1],
        })
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "dup", "station_channel_ids": [2, 3],
        })
        assert r.status_code == 400
        assert "已存在" in r.json()["detail"]

    def test_too_few_stations(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "few", "station_channel_ids": [0],  # 只 1 个
        })
        assert r.status_code == 400
        assert "至少需要 2 个" in r.json()["detail"]

    def test_duplicate_channel(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "dup-ch", "station_channel_ids": [0, 0],
        })
        assert r.status_code == 400
        assert "重复" in r.json()["detail"]

    def test_invalid_trigger_mode(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "x", "station_channel_ids": [0, 1],
            "trigger_mode": "invalid",
        })
        assert r.status_code == 400
        assert "trigger_mode" in r.json()["detail"]


# =============================================================
# C. 与 channel_groups 互斥
# =============================================================

class TestChannelGroupExclusivity:
    def _make_group(self, client, name, channels):
        r = client.post("/api/v1/channel-groups", json={
            "name": name,
            "member_channel_ids": channels,
            "settle_strategy": "synchronized_any_ng",
            "enabled": True,
        })
        return r

    def test_cannot_create_flow_overlapping_group(self, client):
        r = self._make_group(client, "G1", [0, 1])
        assert r.status_code == 201

        # 试图建一个 flow 含 channel 0 → 应被互斥拒绝
        r2 = client.post("/api/v1/workpiece-flows", json={
            "name": "line-X", "station_channel_ids": [0, 2],
            "enabled": True,
        })
        assert r2.status_code == 400
        assert "工位组" in r2.json()["detail"]

    def test_disabled_flow_doesnt_check_exclusivity(self, client):
        self._make_group(client, "G1", [0, 1])
        # 建一个 enabled=False 的 flow 含 channel 0 → 应该允许 (因为不会跑)
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "line-X", "station_channel_ids": [0, 2],
            "enabled": False,
        })
        assert r.status_code == 201


# =============================================================
# D. 流水线间互斥
# =============================================================

class TestFlowExclusivity:
    def test_two_enabled_flows_cannot_share_channel(self, client):
        r1 = client.post("/api/v1/workpiece-flows", json={
            "name": "L1", "station_channel_ids": [0, 1], "enabled": True,
        })
        assert r1.status_code == 201
        r2 = client.post("/api/v1/workpiece-flows", json={
            "name": "L2", "station_channel_ids": [1, 2], "enabled": True,
        })
        assert r2.status_code == 400
        assert "流水线" in r2.json()["detail"]


# =============================================================
# E. enabled 状态保护
# =============================================================

class TestEnabledProtection:
    def test_delete_enabled_rejected(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "live", "station_channel_ids": [0, 1], "enabled": True,
        })
        flow_id = r.json()["id"]

        r2 = client.delete(f"/api/v1/workpiece-flows/{flow_id}")
        assert r2.status_code == 409
        assert "enabled" in r2.json()["detail"].lower() or "false" in r2.json()["detail"].lower()

    def test_disable_then_delete(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "live", "station_channel_ids": [0, 1], "enabled": True,
        })
        flow_id = r.json()["id"]

        client.put(f"/api/v1/workpiece-flows/{flow_id}", json={"enabled": False})
        r2 = client.delete(f"/api/v1/workpiece-flows/{flow_id}")
        assert r2.status_code == 204


# =============================================================
# F. 历史 runs
# =============================================================

class TestRunsHistory:
    def test_runs_empty_initially(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "L", "station_channel_ids": [0, 1],
        })
        flow_id = r.json()["id"]

        r2 = client.get(f"/api/v1/workpiece-flows/{flow_id}/runs")
        assert r2.status_code == 200
        assert r2.json()["total"] == 0
        assert r2.json()["items"] == []

    def test_runs_appear_after_settlement(self, client):
        """Coordinator 执行一次完整流水线 → run 应该出现在历史里."""
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "L", "station_channel_ids": [0, 1], "enabled": True,
        })
        flow_id = r.json()["id"]

        # 直接调 coordinator + db
        from backend.services.workpiece_flow_coordinator import get_coordinator
        coord = get_coordinator()
        # 测试用 fixture 提供 db 通过 dependency_overrides, 这里直接拿 SessionLocal
        import backend.db.database as db_mod
        db = db_mod.SessionLocal()
        try:
            coord.on_cycle_started(0, 100, db)
            coord.on_cycle_settled(0, 100, True, db)
            coord.on_cycle_started(1, 200, db)
            coord.on_cycle_settled(1, 200, True, db)
        finally:
            db.close()

        r2 = client.get(f"/api/v1/workpiece-flows/{flow_id}/runs")
        assert r2.status_code == 200
        body = r2.json()
        assert body["total"] == 1
        run = body["items"][0]
        assert run["status"] == "completed"
        assert run["final_result"] == "OK"


# =============================================================
# G. state
# =============================================================

class TestState:
    def test_state_for_enabled_flow(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "L", "station_channel_ids": [0, 1], "enabled": True,
        })
        flow_id = r.json()["id"]
        r2 = client.get(f"/api/v1/workpiece-flows/{flow_id}/state")
        assert r2.status_code == 200
        body = r2.json()
        assert body["in_flight_count"] == 0
        assert body["flow"]["name"] == "L"

    def test_state_for_disabled_flow_404(self, client):
        r = client.post("/api/v1/workpiece-flows", json={
            "name": "Ldis", "station_channel_ids": [0, 1], "enabled": False,
        })
        flow_id = r.json()["id"]
        r2 = client.get(f"/api/v1/workpiece-flows/{flow_id}/state")
        # disabled flow 不在 coordinator 内存里 → 404
        assert r2.status_code == 404
