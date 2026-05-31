"""福建金龙 — 插件后端 /live-stats 权威实时统计路由测试.

为什么有这条路由 (背景):
  主程序检测页那张实时「合格/不良」计数卡走事件计数动作, 结算时序是"先记合格 →
  再由本插件 pre_cycle_end 把判定改写成 NG", 计数没回头看改写结果, 于是被插件判废的
  周期在那张卡上仍被记成合格 (主程序刻意把改写只作用于库/MES). 插件前端覆盖层照搬了
  主程序透传的 ok/ng, 同样偏. /live-stats 直接从库里 is_good (改写已落库) 按"运行中
  会话"重算, 供插件前端覆盖那张卡, 不动主程序.

本测试确凿验证该路由口径:
  - 每通道取 start_time 最新的会话 (停止检测后该会话变 completed 仍要计数, 与主程序
    计数卡"停止后不清空"对齐 — 否则停止态前端回退到错的主计数)
  - 只统计 end_time 非空 (已结算) 的周期 (进行中的不算)
  - is_good=False 的周期计入 ng (这正是插件判废落库后的权威值)
  - 多通道各算各的, 合格率四舍五入
"""
from __future__ import annotations

import uuid
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
PLUGIN_BACKEND = REPO / "plugins-examples" / "fujian-jinlong" / "backend" / "__init__.py"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("fjjl_plugin_livestats", PLUGIN_BACKEND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _DbHost:
    """只实现 live-stats 用到的 get_db_session, 回真测试库 SessionLocal."""

    def get_db_session(self):
        from backend.db.database import SessionLocal
        return SessionLocal()


def _mk_session(db, channel_id, status, start_offset_min=0):
    from backend.models.models import DetectionSession
    s = DetectionSession(
        session_uuid=uuid.uuid4().hex[:12],
        project_id=None,
        start_time=datetime(2026, 5, 31, 9, start_offset_min % 60, tzinfo=timezone.utc),
        status=status,
        channel_id=channel_id,
    )
    db.add(s)
    db.flush()
    return s


def _mk_cycle(db, session_id, is_good, completed=True, n=1):
    from backend.models.models import DetectionCycle
    c = DetectionCycle(
        cycle_uuid=uuid.uuid4().hex[:12],
        session_id=session_id,
        cycle_number=n,
        start_time=datetime(2026, 5, 31, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 5, 31, 9, 1, tzinfo=timezone.utc) if completed else None,
        is_good=is_good,
    )
    db.add(c)


@pytest.fixture
def client_and_seed(clean_db):
    """装插件 router + 注入 _DbHost, seed 一批会话/周期, 返回 TestClient."""
    from backend.db.database import SessionLocal

    mod = _load_plugin()
    mod._HOST = _DbHost()

    app = FastAPI()
    # 与主程序一致: 挂到 /api/v1/plugins/internal-demo/durations
    app.include_router(
        mod._build_router(),
        prefix="/api/v1/plugins/internal-demo/durations",
    )
    client = TestClient(app)

    db = SessionLocal()
    try:
        # 通道 0: 最近会话, 2 OK + 1 NG (NG = 插件判废已落库) + 1 进行中(不算)
        s0 = _mk_session(db, channel_id=0, status="running", start_offset_min=10)
        _mk_cycle(db, s0.id, is_good=True, n=1)
        _mk_cycle(db, s0.id, is_good=True, n=2)
        _mk_cycle(db, s0.id, is_good=False, n=3)            # 被插件判废
        _mk_cycle(db, s0.id, is_good=True, completed=False, n=4)  # 进行中, 应被忽略

        # 通道 0: 一条更早的会话, 全 OK — 不该被算进来 (因为 start_time 更早, 不是最近)
        s0_old = _mk_session(db, channel_id=0, status="completed", start_offset_min=1)
        _mk_cycle(db, s0_old.id, is_good=True, n=1)
        _mk_cycle(db, s0_old.id, is_good=True, n=2)

        # 通道 1: 最近会话, 1 NG
        s1 = _mk_session(db, channel_id=1, status="running", start_offset_min=20)
        _mk_cycle(db, s1.id, is_good=False, n=1)

        db.commit()
    finally:
        db.close()

    return client


def test_live_stats_counts_overridden_ng_authoritatively(client_and_seed):
    """通道0: 总3(忽略进行中) ok2 ng1 合格率67; 通道1: 总1 ok0 ng1 合格率0."""
    resp = client_and_seed.get("/api/v1/plugins/internal-demo/durations/live-stats")
    assert resp.status_code == 200
    ch = resp.json()["channels"]

    assert ch["0"] == {"total": 3, "ok": 2, "ng": 1, "yield_rate": 67}
    assert ch["1"] == {"total": 1, "ok": 0, "ng": 1, "yield_rate": 0}
    # 更早的会话不冒泡: 通道0 取最新那条, 不会变成 5 周期
    assert "2" not in ch


def test_live_stats_counts_after_stop_completed_session(clean_db):
    """停止检测后会话变 completed 仍要计数 (与主程序计数卡停止后不清空对齐).

    这是运行时 E2E 发现的真实坑: 早先只统计 running 会话, 停止后路由返回空 →
    前端回退到错的主程序计数 (把判废周期记成合格). 改成"每通道最近会话"后修复.
    """
    from backend.db.database import SessionLocal
    mod = _load_plugin()
    mod._HOST = _DbHost()
    app = FastAPI()
    app.include_router(mod._build_router(),
                       prefix="/api/v1/plugins/internal-demo/durations")
    client = TestClient(app)

    db = SessionLocal()
    try:
        s = _mk_session(db, channel_id=0, status="completed", start_offset_min=5)
        _mk_cycle(db, s.id, is_good=True, n=1)
        _mk_cycle(db, s.id, is_good=False, n=2)   # 判废
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/v1/plugins/internal-demo/durations/live-stats")
    assert resp.status_code == 200
    assert resp.json()["channels"]["0"] == {"total": 2, "ok": 1, "ng": 1, "yield_rate": 50}


def test_live_stats_empty_when_no_session(clean_db):
    """完全没有会话 → channels 空, 不报错 (前端会回退主程序计数)."""
    mod = _load_plugin()
    mod._HOST = _DbHost()
    app = FastAPI()
    app.include_router(mod._build_router(),
                       prefix="/api/v1/plugins/internal-demo/durations")
    client = TestClient(app)
    resp = client.get("/api/v1/plugins/internal-demo/durations/live-stats")
    assert resp.status_code == 200
    assert resp.json() == {"channels": {}}


def test_live_stats_degrades_to_empty_without_host():
    """_HOST 为 None (插件没正常注入) → 降级空, 不抛."""
    mod = _load_plugin()
    mod._HOST = None
    app = FastAPI()
    app.include_router(mod._build_router(),
                       prefix="/api/v1/plugins/internal-demo/durations")
    client = TestClient(app)
    resp = client.get("/api/v1/plugins/internal-demo/durations/live-stats")
    assert resp.status_code == 200
    assert resp.json() == {"channels": {}}
