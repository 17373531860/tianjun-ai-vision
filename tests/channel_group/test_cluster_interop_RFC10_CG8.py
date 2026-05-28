"""RFC 10 CG.8 — 工位组与 cluster 互操作测试.

测试: build_context_from_cycle 输出的 cycle_context 应包含 channel_group 字段,
让 cluster collector 跨机聚 box 时能自动透传工位组的结算结果.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def isolated_db(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "test_cluster_interop.db"
    engine = create_engine(f"sqlite:///{db_path}")

    from backend.db.database import Base
    from backend.models import models, plugin_models, mes_models, export_models, auth_models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    Session = sessionmaker(bind=engine)
    db = Session()
    yield db
    db.close()


def test_cycle_context_includes_channel_group_when_in_group(isolated_db):
    """通道在组里 + cycle.group_settle_result 已写 → cycle_context 应包含 channel_group 子字段."""
    from backend.models.models import Project, DetectionSession, DetectionCycle, ChannelGroup
    from datetime import datetime

    # 建组
    grp = ChannelGroup(
        name="G-CL",
        member_channel_ids=[0, 1],
        settle_strategy="synchronized_any_ng",
        enabled=True,
    )
    isolated_db.add(grp)

    # 建 project / session / cycle (cycle 标 group 字段)
    proj = Project(name="P")
    isolated_db.add(proj)
    isolated_db.commit()

    sess = DetectionSession(
        session_uuid="test-uuid-1",
        project_id=proj.id,
        start_time=datetime.now(),
    )
    isolated_db.add(sess)
    isolated_db.commit()

    cycle = DetectionCycle(
        cycle_uuid="test-cycle-uuid-1",
        session_id=sess.id,
        cycle_number=1,
        start_time=datetime.now(),
        is_good=False,
        channel_group_id=grp.id,
        group_settle_result="NG",
        group_settled_with=[200],
    )
    isolated_db.add(cycle)
    isolated_db.commit()

    # 调 build_context_from_cycle
    from backend.services.mes_gateway import get_mes_gateway
    gw = get_mes_gateway()
    ctx = gw.build_context_from_cycle(
        isolated_db,
        cycle_id=cycle.id,
        is_good=False,
        project_id=proj.id,
    )

    # 顶层 channel_group 应存在
    assert "channel_group" in ctx, "cycle_context 缺 channel_group 顶层字段"
    cg = ctx["channel_group"]
    assert cg["id"] == grp.id
    assert cg["settle_result"] == "NG"
    assert cg["settled_with"] == [200]

    # cycle 子树也应有便利字段
    assert ctx["cycle"]["channel_group_id"] == grp.id
    assert ctx["cycle"]["group_settle_result"] == "NG"
    assert ctx["cycle"]["group_settled_with"] == [200]


def test_cycle_context_channel_group_nullable_when_not_in_group(isolated_db):
    """通道不在任何组 → channel_group 字段全 None / [], 不影响 MES 默认 payload."""
    from backend.models.models import Project, DetectionSession, DetectionCycle
    from datetime import datetime

    proj = Project(name="P")
    isolated_db.add(proj)
    isolated_db.commit()

    sess = DetectionSession(session_uuid="test-uuid-2", project_id=proj.id, start_time=datetime.now())
    isolated_db.add(sess)
    isolated_db.commit()

    # cycle 不挂任何组 (channel_group_id=None)
    cycle = DetectionCycle(
        cycle_uuid="test-cycle-uuid-2",
        session_id=sess.id,
        cycle_number=1,
        start_time=datetime.now(),
        is_good=True,
    )
    isolated_db.add(cycle)
    isolated_db.commit()

    from backend.services.mes_gateway import get_mes_gateway
    gw = get_mes_gateway()
    ctx = gw.build_context_from_cycle(
        isolated_db,
        cycle_id=cycle.id,
        is_good=True,
        project_id=proj.id,
    )

    # 没组时字段应该为 None / []
    cg = ctx["channel_group"]
    assert cg["id"] is None
    assert cg["settle_result"] is None
    assert cg["settled_with"] == []
