"""sqlite_to_pg.py 真数据迁移冒烟（db-matrix PG job 专属，SQLite 环境自动 skip）。

CI 里原来只有"空 SQLite dry-run"——枚举表不报错就算过，真迁移的三大坑
（JSON 双编码 / 孤儿 FK / 行数漂移）一个都测不到。本文件补齐：

  1. 造一个带真数据的源 SQLite（ORM 建表+插数：项目 JSON 配置、session/cycle、
     含孤儿 scan_device_id 的工件——SQLite 默认不强制 FK 的历史现状）
  2. 建独立目标 PG 库（不碰测试 schema），create_all 出 schema
  3. subprocess 跑迁移 → 断言行数一致、JSON 列读回结构化（防双编码回归）、
     孤儿引用照样落库且被报告
  4. --verify 校验模式跑绿
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_DB_URL = os.environ.get("DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _DB_URL.startswith("postgresql"),
    reason="真迁移冒烟需要 PostgreSQL（db-matrix PG job 跑）",
)

_SMOKE_DB = "tj_migrate_smoke"


def _admin_dsn() -> str:
    """去掉库名/参数, 连到默认 postgres 库做 CREATE/DROP DATABASE。"""
    from sqlalchemy.engine.url import make_url
    url = make_url(_DB_URL)
    return str(url.set(database="postgres").set(query={}))


def _target_dsn() -> str:
    from sqlalchemy.engine.url import make_url
    url = make_url(_DB_URL)
    return str(url.set(database=_SMOKE_DB).set(query={}))


@pytest.fixture(scope="module")
def migration_env(tmp_path_factory):
    """源 SQLite（带数据）+ 独立目标 PG 库（已 create_all）。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from backend.db.database import Base
    # 迁移工具同款：import 全部模型组, 注册全部表
    import backend.models.models          # noqa: F401
    import backend.models.mes_models      # noqa: F401
    from backend.models.models import Project
    from backend.models.mes_models import Workpiece

    # ---- 源 SQLite ----
    src_path = str(tmp_path_factory.mktemp("mig") / "src.db")
    src_engine = create_engine(f"sqlite:///{src_path}")
    Base.metadata.create_all(bind=src_engine)
    SrcSession = sessionmaker(bind=src_engine)
    s = SrcSession()
    try:
        proj = Project(
            name="__migrate_smoke_proj__",
            task_type="detection",
            logic_mode="sequential",
            pipeline_config={"sequence_order": [{"step_id": 1}], "nested": {"a": [1, 2]}},
            steps_config=[{"id": 1, "label": "A", "enabled": True}],
            events_config=[],
            counters_config=[],
            alarm_config={},
            detection_config={},
            data_config={},
            is_active=False,
        )
        s.add(proj)
        s.flush()
        # 孤儿 FK 现状复刻: scan_device_id 指向不存在的设备
        # (SQLite 默认 PRAGMA foreign_keys=0, 老客户库普遍存在)
        s.add(Workpiece(serial_no="SN-MIG-1", project_id=proj.id,
                        status="ok", scan_device_id=999))
        s.add(Workpiece(serial_no="SN-MIG-2", project_id=proj.id,
                        status="registered"))
        s.commit()
        proj_id = proj.id
    finally:
        s.close()
    src_engine.dispose()

    # ---- 目标 PG 库（独立于测试 schema）----
    admin = create_engine(_admin_dsn(), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_SMOKE_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{_SMOKE_DB}"'))
    tgt_engine = create_engine(_target_dsn())
    Base.metadata.create_all(bind=tgt_engine)   # _check_pg_ready 认 projects 表
    tgt_engine.dispose()

    yield {"sqlite": src_path, "pg_dsn": _target_dsn(), "project_id": proj_id}

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_SMOKE_DB}" WITH (FORCE)'))
    admin.dispose()


def _run_tool(env_dict, *args) -> subprocess.CompletedProcess:
    script = os.path.join(os.path.dirname(__file__), "..", "scripts", "db", "sqlite_to_pg.py")
    env = dict(os.environ)
    env["DATABASE_URL"] = env_dict["pg_dsn"]
    return subprocess.run(
        [sys.executable, script,
         "--sqlite", env_dict["sqlite"], "--pg", env_dict["pg_dsn"], *args],
        capture_output=True, text=True, timeout=300, env=env,
    )


def test_real_migration_roundtrip(migration_env):
    proc = _run_tool(migration_env)
    assert proc.returncode == 0, f"迁移失败:\n{proc.stdout}\n{proc.stderr}"
    # 孤儿引用要被晒出来, 不能静默
    assert "孤儿" in proc.stdout, f"未报告孤儿引用:\n{proc.stdout}"

    from sqlalchemy import create_engine, text
    engine = create_engine(migration_env["pg_dsn"])
    try:
        with engine.connect() as conn:
            n_proj = conn.execute(text(
                "SELECT COUNT(*) FROM projects WHERE name='__migrate_smoke_proj__'"
            )).scalar()
            assert n_proj == 1

            n_wp = conn.execute(text("SELECT COUNT(*) FROM workpieces")).scalar()
            assert n_wp == 2
            # 孤儿行照样落库 (replica 模式), 引用原样保留
            orphan = conn.execute(text(
                "SELECT scan_device_id FROM workpieces WHERE serial_no='SN-MIG-1'"
            )).scalar()
            assert orphan == 999

            # 防 JSON 双编码回归: PG 端 json 列必须是结构化对象, 不能是字符串
            raw = conn.execute(text(
                "SELECT pipeline_config FROM projects WHERE name='__migrate_smoke_proj__'"
            )).scalar()
            obj = raw if isinstance(raw, dict) else json.loads(raw)
            assert isinstance(obj, dict), f"pipeline_config 双编码: {type(obj)} {obj!r}"
            assert obj["nested"] == {"a": [1, 2]}
            steps_raw = conn.execute(text(
                "SELECT steps_config FROM projects WHERE name='__migrate_smoke_proj__'"
            )).scalar()
            steps = steps_raw if isinstance(steps_raw, list) else json.loads(steps_raw)
            assert steps and steps[0].get("label") == "A", f"steps_config 损坏: {steps!r}"
    finally:
        engine.dispose()

    # ORM 读回也必须是结构化 (api/source_project_config_apply 的现实读取路径)
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import sessionmaker
    from backend.models.models import Project
    eng2 = _ce(migration_env["pg_dsn"])
    S2 = sessionmaker(bind=eng2)
    s2 = S2()
    try:
        p = s2.query(Project).filter(Project.name == "__migrate_smoke_proj__").first()
        assert isinstance(p.steps_config, list)
        assert isinstance(p.pipeline_config, dict)
    finally:
        s2.close()
        eng2.dispose()


def test_migration_verify_mode(migration_env):
    """--verify: 迁移后校验模式必须绿 (行数 + ID 列 min/max/sum 一致)。"""
    proc = _run_tool(migration_env, "--verify")
    assert proc.returncode == 0, f"verify 失败:\n{proc.stdout}\n{proc.stderr}"
