"""测试全局 fixtures + 环境隔离。

关键：
  1. 必须在 import 任何 backend 模块之前，把 TIANJUN_DATA_DIR 指到临时目录
     —— config.py 读环境变量后会把 SQLALCHEMY_DATABASE_URI / 各种 UPLOAD/RECORDING_DIR
     固化到 settings 单例上，无法事后改。
  2. 每个 session 用一个独立临时目录，session 结束清理。
  3. FastAPI app 实例 + TestClient 提供给所有 BDD step。
"""
from __future__ import annotations

import os
import shutil
import tempfile

# ============================================================
# Step 1: 在 import backend 之前隔离 DATA_DIR
# ============================================================
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="tianjun_test_")
os.environ["TIANJUN_DATA_DIR"] = _TEST_DATA_DIR
# 预放空的 uploads/videos 占位目录: config._migrate_old_data 会把 BASE_DIR 下已存在
# 的数据整体拷进 DATA_DIR (dst 已存在则跳过)。开发机 uploads/videos 里的调参视频
# 好几个 GB, 每个测试 session 拷一遍既慢又把 /tmp 撑爆 (Errno 28 血泪)。测试不依赖
# 这些大视频 — 用空目录占位让拷贝跳过, 其余 (models/images) 照常迁移。
os.makedirs(os.path.join(_TEST_DATA_DIR, "uploads", "videos"), exist_ok=True)
# 关掉打包/license 检查相关副作用
os.environ.setdefault("TIANJUN_TEST_MODE", "1")
# 挂载 synthetic 虚拟检测 API（backend/main.py）；不影响生产默认（未设则无路由）
os.environ.setdefault("RUNTIME_MODE", "test")

# v3.7.0: 如果设置了 DATABASE_URL（外部 PG），则 schema 隔离用一个独立 schema
# 避免开发库被 wipe；conftest 启动时清空再建表，session 结束 drop schema。
_PG_DSN = os.environ.get("DATABASE_URL", "").strip()
_PG_SCHEMA = os.environ.get("TIANJUN_TEST_PG_SCHEMA", "tianjun_test").strip() if _PG_DSN.startswith("postgresql") else None

# 关键：把 search_path 通过 libpq options 注入到 DSN，
# 这样每个新连接（含后台线程）天生就在测试 schema 里，无需 listener 时序协调。
if _PG_SCHEMA and "options=" not in _PG_DSN:
    sep = "&" if "?" in _PG_DSN else "?"
    os.environ["DATABASE_URL"] = (
        f"{_PG_DSN}{sep}options=-csearch_path%3D{_PG_SCHEMA}%2Cpublic"
    )

import sys  # noqa: E402

# 把项目根加进 sys.path，让 backend.* 可导入
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ============================================================
# Step 2: import 后端依赖（顺序敏感）
# 容错: 轻量测试环境(只装 pytest/jsonschema/cryptography, 无 fastapi/backend)下优雅降级,
# 让不依赖后端的子目录测试(如 tests/plugin_system/)不被根 conftest 顶层重依赖连坐。
# 装了后端 → 照常 import + 后面守卫块建表 seed; 没装 → _BACKEND_AVAILABLE=False 整段跳过。
# ============================================================
import pytest  # noqa: E402

try:
    from fastapi.testclient import TestClient  # noqa: E402, F401
    from backend.core.config import DATA_DIR, settings  # noqa: E402, F401  必须先 import 让 DATA_DIR 锁定
    from backend.db.database import Base, engine, SessionLocal  # noqa: E402
    from backend.models import models as _orm_models  # noqa: F401, E402
    from backend.models import mes_models as _mes_models  # noqa: F401, E402  MES/扫码/外设/日志表
    from backend.models import export_models as _export_models  # noqa: F401, E402
    from backend.models import plugin_models as _plugin_models  # noqa: F401, E402
    from backend.models import auth_models as _auth_models  # noqa: F401, E402
    from backend.models import notify_models as _notify_models  # noqa: F401, E402  短信日报三表
    _BACKEND_AVAILABLE = True
except ImportError:
    _BACKEND_AVAILABLE = False


def _pg_reset_schema() -> None:
    """PG 模式：清空测试 schema 后重建（隔离开发库）。

    用一个**绕过 search_path 的独立连接**在 public 上下文里 drop+create schema。
    之后所有 engine.connect()/SessionLocal() 都通过 DSN 里的 options 自动落在测试 schema。
    """
    if not _PG_SCHEMA:
        return
    import psycopg2
    base_dsn = _PG_DSN  # 不带 options 的原始 DSN
    raw = psycopg2.connect(base_dsn.replace("postgresql+psycopg2://", "postgresql://"))
    try:
        raw.autocommit = True
        cur = raw.cursor()
        cur.execute(f'DROP SCHEMA IF EXISTS "{_PG_SCHEMA}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{_PG_SCHEMA}"')
        cur.close()
    finally:
        raw.close()
    # 把可能预热过的池清掉，确保后续连接读新的 DSN options
    engine.dispose()


# (移到文件后 _BACKEND_AVAILABLE 守卫块统一执行: 清理临时 DB + _pg_reset_schema + 建表 + seed)


def _seed_dummy_project() -> None:
    """给测试库 seed 一个最小项目，让 GET /api/v1/projects 至少有 1 条。

    避免某些 BDD 在"项目列表为空"时 skip。
    """
    from backend.models.models import Project
    session = SessionLocal()
    try:
        # 按名字判断而非 count==0: clean_db teardown 补种时表里可能残留
        # 测试自己的项目, 只看 count 会漏补种子
        _has_seed = session.query(Project).filter(
            Project.name == "__bdd_seed_project__").count() > 0
        if not _has_seed:
            session.add(Project(
                name="__bdd_seed_project__",
                task_type="detection",
                logic_mode="sequential",
                pipeline_config={},
                steps_config=[
                    {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
                ],
                events_config=[],
                counters_config=[],
                alarm_config={},
                detection_config={},
                data_config={},
                is_active=False,
            ))
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


# ============================================================
# 仅在后端可用时执行: 清理临时 DB + PG schema 重置 + 建表 + seed
# (轻量测试环境 _BACKEND_AVAILABLE=False 时整段跳过, 完全不碰 backend)
# ============================================================
if _BACKEND_AVAILABLE:
    # config.py 的 _migrate_old_data 会把生产 DB 复制到临时目录，把它清掉换成空的
    _test_db = os.path.join(_TEST_DATA_DIR, "sql_app.db")
    if os.path.exists(_test_db):
        os.remove(_test_db)
    # 同时清空 counters 等持久化目录（避免测试间串扰）
    for _sub in ("counters", "uploads", "recordings", "exports"):
        _p = os.path.join(_TEST_DATA_DIR, _sub)
        if os.path.exists(_p):
            shutil.rmtree(_p, ignore_errors=True)
    # 重建空的 uploads/videos 占位: backend.main 启动迁移 (migrate_data_to_external_dir)
    # 会把 BASE_DIR/uploads 下 dst 不存在的条目整体拷进来, 开发机 videos 里的调参
    # 视频好几个 GB — 每个 session 拷一遍既慢又把 /tmp 撑爆 (Errno 28 血泪)。
    # 测试不依赖这些大视频, 空目录占位让该条目被跳过 (models/images 照常迁移)。
    os.makedirs(os.path.join(_TEST_DATA_DIR, "uploads", "videos"), exist_ok=True)
    _pg_reset_schema()
    # 建表（在干净的临时 DB 上 / PG 测试 schema 上）
    Base.metadata.create_all(bind=engine)
    _seed_dummy_project()


# ============================================================
# Session-level fixtures
# ============================================================
@pytest.fixture(scope="session")
def test_data_dir():
    """临时数据目录的绝对路径"""
    return _TEST_DATA_DIR


@pytest.fixture(scope="session")
def app():
    """完整的 FastAPI app（注册了所有 routers）"""
    from backend.main import app as fastapi_app
    return fastapi_app


@pytest.fixture(scope="session")
def client(app):
    """共享的 TestClient — 所有 HTTP scenarios 都走它"""
    from fastapi.testclient import TestClient  # 惰性: 仅请求该 fixture 的 HTTP 测试才需要 fastapi
    return TestClient(app)


# ============================================================
# Function-level fixtures（每个 scenario 一个干净的 DB session）
# ============================================================
@pytest.fixture
def db_session():
    """单事务的 DB session，结束自动回滚（保持表数据干净）"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def clean_db():
    """彻底清空所有表（个别需要从零开始的场景用）。

    teardown 补回 __bdd_seed_project__ 种子：清表把 session 级种子一起抹掉，
    不补回会让后面依赖种子的测试（mes_inbound / BDD 等）串挂——只在
    单独跑时绿、全量跑时红，极难排查（2026-08 实锤案例）。
    """
    session = SessionLocal()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            try:
                session.execute(table.delete())
            except Exception:
                pass
        session.commit()
        yield
    finally:
        session.close()
    _seed_dummy_project()


@pytest.fixture
def tmp_output_dir(tmp_path):
    """每个 test 一个临时输出目录（导出文件落这里）"""
    out = tmp_path / "out"
    out.mkdir()
    return str(out)


# ============================================================
# 共享上下文（BDD 步骤之间传值）
# ============================================================
@pytest.fixture
def ctx():
    """每个 scenario 独立的上下文字典 — Given/When/Then 之间传变量"""
    return {}


# ============================================================
# Cleanup
# ============================================================
def pytest_sessionfinish(session, exitstatus):
    """整个 test session 结束清理临时目录"""
    try:
        shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
    except Exception:
        pass
