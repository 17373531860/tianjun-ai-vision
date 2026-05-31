"""回归: API/脚本创建项目时自动补全 sequence_order + 结算步约束。

踩坑背景 (2026-05-31 金龙 UAT):
  - UAT 脚本 POST /projects 只传 steps_config, 不传 pipeline_config.sequence_order
  - 后端原样落库 → 项目页无法识别结算步 → 严格顺序/单次接受开关未禁用
  - 检测仍能跑 (引擎读 steps_config), 但 UI 配置矩阵未覆盖此路径
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "test_seq_order.db"
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
    yield TestClient(app)
    app.dependency_overrides.clear()


def _jinlong_like_steps():
    labels = ["上料", "压墨", "吹干"]
    return [
        {"id": f"s{i}", "label": lbl, "enabled": True, "threshold": 50, "min_frames": 1}
        for i, lbl in enumerate(labels, start=1)
    ]


def test_create_project_auto_fills_sequence_order_when_missing(isolated_client):
    """模拟金龙 UAT 脚本: 无 sequence_order → 创建后应自动补全。"""
    name = f"uat-seq-{uuid.uuid4().hex[:8]}"
    body = {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {
            "logic_mode": "sequential",
            "settlement_mode": "last_step",
        },
        "steps_config": _jinlong_like_steps(),
        "events_config": [
            {"id": 1, "name": "OK", "actions": [], "show_notification": True},
            {"id": 2, "name": "NG", "actions": [], "show_notification": True},
        ],
    }
    r = isolated_client.post("/api/v1/projects", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    seq = (data.get("pipeline_config") or {}).get("sequence_order") or []
    assert len(seq) == 3, f"expected 3 steps in sequence_order, got {seq}"
    assert seq[0]["step_id"] == "s1"
    assert seq[-1]["step_id"] == "s3"


def test_update_project_backfills_empty_sequence_order(isolated_client):
    """已有项目 sequence_order 为空 → PUT 更新时应补全。"""
    name = f"uat-seq-upd-{uuid.uuid4().hex[:8]}"
    create_body = {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {"settlement_mode": "first_step"},
        "steps_config": _jinlong_like_steps(),
    }
    r = isolated_client.post("/api/v1/projects", json=create_body)
    assert r.status_code == 201
    pid = r.json()["id"]

    r2 = isolated_client.put(
        f"/api/v1/projects/{pid}",
        json={"pipeline_config": {"settlement_mode": "last_step"}},
    )
    assert r2.status_code == 200, r2.text
    seq = (r2.json().get("pipeline_config") or {}).get("sequence_order") or []
    assert len(seq) == 3
    assert seq[-1]["step_id"] == "s3"


def test_build_default_sequence_order_skips_backup_steps():
    from backend.services.project_config_normalize import build_default_sequence_order

    steps = [
        {"id": 1, "label": "A", "enabled": True},
        {"id": 2, "label": "B", "enabled": False},
        {"id": 3, "label": "C", "enabled": True, "is_backup": True},
        {"id": 4, "label": "D", "enabled": True, "backup_for": 1},
    ]
    seq = build_default_sequence_order(steps)
    assert seq == [{"step_id": 1}]
