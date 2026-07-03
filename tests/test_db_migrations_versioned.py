# -*- coding: utf-8 -*-
"""版本化迁移 runner 回归（2026-07 治理, RFC 方案 B）。

三档场景:
  1. 空库: create_all + apply_pending → 记账表就位、m0000 记账、schema 与 ORM 一致
  2. 老库升级: 手造缺列的历史表 → apply_pending 幂等补列（v3.1 老客户跳升级路径）
  3. runner 语义: 新迁移只应用一次; 失败迁移不记账不阻断; m0000 永远幂等重跑
独立临时 DB, 不碰 sql_app.db (项目 fixture 铁律)。
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

os.environ.setdefault("BACKEND_SKIP_INIT", "1")

# 注册全部 ORM 映射（Base.metadata 才能建全 37+ 张表）
from backend.db.database import Base  # noqa: E402
from backend.models import models  # noqa: F401, E402
from backend.models import mes_models  # noqa: F401, E402
from backend.models import export_models  # noqa: F401, E402
from backend.models import auth_models  # noqa: F401, E402
from backend.models import plugin_models  # noqa: F401, E402
from backend.models import weighing_models  # noqa: F401, E402

from backend.db import migrations as mig  # noqa: E402
from backend.db.migrations import m0000_legacy  # noqa: E402


@pytest.fixture()
def tmp_engine(tmp_path):
    db_file = tmp_path / f"mig_{uuid.uuid4().hex[:8]}.db"
    eng = create_engine(f"sqlite:///{db_file}")
    yield eng
    eng.dispose()


def _cols(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


# ==================== 1. 空库全新安装 ====================

def test_fresh_db_apply_pending(tmp_engine):
    Base.metadata.create_all(bind=tmp_engine)
    mig.apply_pending(tmp_engine)

    insp = inspect(tmp_engine)
    tables = set(insp.get_table_names())
    assert "schema_migrations" in tables, "记账表应被创建"

    with tmp_engine.connect() as conn:
        applied = {r[0] for r in conn.execute(
            text("SELECT migration_id FROM schema_migrations")).fetchall()}
    assert "m0000_legacy" in applied, "基线迁移应记账"

    # 空库由 create_all 直接建到位, m0000 应零 ALTER 通过; 抽查几列存在
    assert "interval_to_next" in _cols(tmp_engine, "step_records")
    assert "plugin_data" in _cols(tmp_engine, "step_records")
    assert "forced_reason" in _cols(tmp_engine, "packaging_flow_runs")


# ==================== 2. 老库升级（缺列补齐） ====================

def test_legacy_db_columns_backfilled(tmp_engine):
    # 手造"历史版本"的 step_records / scanner_devices：只有主键列
    with tmp_engine.connect() as conn:
        conn.execute(text("CREATE TABLE step_records (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE scanner_devices (id INTEGER PRIMARY KEY)"))
        # 老版本还留着 operators 表 → 应被阶段 5 清理
        conn.execute(text("CREATE TABLE operators (id INTEGER PRIMARY KEY)"))
        conn.commit()
    # 其余表按当前 ORM 建（create_all 不动已存在的表——正是老库升级的真实形态）
    Base.metadata.create_all(bind=tmp_engine)

    mig.apply_pending(tmp_engine)

    assert "interval_to_next" in _cols(tmp_engine, "step_records"), "老表缺列应被 ALTER 补上"
    sc = _cols(tmp_engine, "scanner_devices")
    for col in ("scan_mode", "broadcast_channels", "late_scan_bind_window_sec"):
        assert col in sc, f"scanner_devices 应补列 {col}"
    assert "operators" not in set(inspect(tmp_engine).get_table_names()), \
        "阶段 5 应 DROP 旧 operators 表"

    # 幂等重入：再跑一遍不炸、不重复记账
    mig.apply_pending(tmp_engine)
    with tmp_engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations WHERE migration_id='m0000_legacy'"
        )).scalar()
    assert n == 1


# ==================== 3. runner 语义（记账跳过 / 失败即停 / m0000 常跑） ====================

class _FakeMig:
    def __init__(self, mid, fail=False):
        self.MIGRATION_ID = mid
        self.fail = fail
        self.calls = 0

    def apply(self, engine):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")


def test_runner_semantics(tmp_engine, monkeypatch):
    Base.metadata.create_all(bind=tmp_engine)
    ok1 = _FakeMig("m0001_ok")
    bad = _FakeMig("m0002_bad", fail=True)
    ok2 = _FakeMig("m0003_never")
    monkeypatch.setattr(mig, "_load_migrations",
                        lambda: [m0000_legacy, ok1, bad, ok2])

    mig.apply_pending(tmp_engine)
    assert ok1.calls == 1
    assert bad.calls == 1
    assert ok2.calls == 0, "失败后应停止应用后续迁移"

    with tmp_engine.connect() as conn:
        applied = {r[0] for r in conn.execute(
            text("SELECT migration_id FROM schema_migrations")).fetchall()}
    assert "m0001_ok" in applied
    assert "m0002_bad" not in applied, "失败迁移不得记账"

    # 修好后重跑：ok1 跳过（记账）、bad/ok2 补齐、m0000 幂等再跑
    bad.fail = False
    mig.apply_pending(tmp_engine)
    assert ok1.calls == 1, "已记账迁移不应重复应用"
    assert bad.calls == 2 and ok2.calls == 1


def test_retired_stub_raises():
    """防旧习惯回流：main.migrate_database 必须是断言桩。"""
    import backend.main as bm
    with pytest.raises(RuntimeError, match="已退役"):
        bm.migrate_database()
