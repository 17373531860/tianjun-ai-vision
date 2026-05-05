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
# 关掉打包/license 检查相关副作用
os.environ.setdefault("TIANJUN_TEST_MODE", "1")

import sys  # noqa: E402

# 把项目根加进 sys.path，让 backend.* 可导入
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ============================================================
# Step 2: import 后端依赖（顺序敏感）
# ============================================================
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.core.config import DATA_DIR, settings  # noqa: E402  必须先 import 让 DATA_DIR 锁定

# config.py 的 _migrate_old_data 会把生产 DB 复制到临时目录，把它清掉换成空的
_test_db = os.path.join(_TEST_DATA_DIR, "sql_app.db")
if os.path.exists(_test_db):
    os.remove(_test_db)
# 同时清空 counters 等持久化目录（避免测试间串扰）
for _sub in ("counters", "uploads", "recordings", "exports"):
    _p = os.path.join(_TEST_DATA_DIR, _sub)
    if os.path.exists(_p):
        shutil.rmtree(_p, ignore_errors=True)

from backend.db.database import Base, engine, SessionLocal  # noqa: E402
from backend.models import models as _orm_models  # noqa: F401, E402
from backend.models import export_models as _export_models  # noqa: F401, E402

# 建表（在干净的临时 DB 上）
Base.metadata.create_all(bind=engine)


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
    """彻底清空所有表（个别需要从零开始的场景用）"""
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
