"""G1 后端 PluginRegistry / PluginManager 接口对齐回归护栏

客户视角叙事:
  ACME 运维上传 + 激活 Tier 3 全栈插件后重启工控机, 期待:
  1. 后端启动**不崩** (插件 register_plugin 抛错也不能让主程序停)
  2. 后端日志出现 [Plugin][internal-demo] router mounted / table created / hook registered
  3. ORM 表 p_internal_demo_notes 真建出来
  4. GET /api/v1/plugins/internal-demo/demo/health 真返回 200

本测试 = 单元层护栏:
  - PluginRegistry 三个核心子 registry (routes/hooks/tables) API 稳定
  - PluginManager._load_backend_module 严格用 4 参 (app, registry, license_payload, host)
  - register_plugin 抛异常时, 主程序日志记 audit + state=failed, 不抛给上层

如果有人改回老的 1 参 register(dict) 调用, 这条测试会立刻 FAIL.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, FastAPI


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.plugin_system.registry import (
    HooksRegistry,
    PluginHost,
    PluginRegistry,
    RoutesRegistry,
    TablesRegistry,
)


# ----------------------- RoutesRegistry -----------------------


def test_routes_registry_mounts_with_customer_prefix():
    app = FastAPI()
    reg = RoutesRegistry(app, customer_code="internal-demo")
    plugin_router = APIRouter()

    @plugin_router.get("/health")
    def _h():
        return {"ok": True}

    reg.include_router(plugin_router, subpath="demo")

    # 新版 FastAPI 把 include_router 挂成嵌套路由对象, app.routes 不再扁平;
    # 改用真实请求验证挂载 (行为断言, 跨版本稳定)
    from fastapi.testclient import TestClient
    resp = TestClient(app).get("/api/v1/plugins/internal-demo/demo/health")
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    mounted = reg.mounted()
    assert len(mounted) == 1
    assert mounted[0]["prefix"] == "/api/v1/plugins/internal-demo/demo"


def test_routes_registry_rejects_non_router():
    app = FastAPI()
    reg = RoutesRegistry(app, customer_code="internal-demo")
    with pytest.raises(TypeError):
        reg.include_router("not a router", subpath="demo")


# ----------------------- HooksRegistry -----------------------


def test_hooks_registry_register_and_fire_in_priority_order():
    reg = HooksRegistry(customer_code="internal-demo")
    calls = []

    reg.register("cycle_end", phase="post_cycle", when="post", priority=200, handler=lambda c: calls.append("low"))
    reg.register("cycle_end", phase="post_cycle", when="post", priority=50, handler=lambda c: calls.append("high"))

    reg.fire("cycle_end", "post_cycle", "post", {"cycle_id": 42})
    assert calls == ["high", "low"]


def test_hooks_registry_swallows_handler_exception():
    reg = HooksRegistry(customer_code="internal-demo")

    def bad(_ctx):
        raise RuntimeError("plugin bug")

    reg.register("cycle_end", phase="post_cycle", when="post", priority=100, handler=bad)
    results = reg.fire("cycle_end", "post_cycle", "post", {})
    assert len(results) == 1
    assert results[0]["_error"] == "plugin bug"


def test_hooks_registry_register_requires_callable():
    reg = HooksRegistry(customer_code="internal-demo")
    with pytest.raises(TypeError):
        reg.register("cycle_end", handler=None)


# ----------------------- TablesRegistry -----------------------


def test_tables_registry_creates_table(tmp_path):
    from sqlalchemy import Column, Integer, String, create_engine
    from sqlalchemy.orm import declarative_base

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base = declarative_base()

    class PluginNote(Base):
        __tablename__ = "p_internal_demo_notes"
        id = Column(Integer, primary_key=True)
        note = Column(String(64))

    reg = TablesRegistry(engine=engine, customer_code="internal-demo")
    reg.register(PluginNote)

    from sqlalchemy import inspect

    assert "p_internal_demo_notes" in inspect(engine).get_table_names()
    assert "p_internal_demo_notes" in reg.registered()


# ----------------------- PluginRegistry snapshot -----------------------


def test_plugin_registry_snapshot_includes_all_subregistries(tmp_path):
    from sqlalchemy import create_engine

    app = FastAPI()
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    reg = PluginRegistry(app=app, engine=engine, customer_code="internal-demo")

    plugin_router = APIRouter()

    @plugin_router.get("/x")
    def _x():
        return {}

    reg.routes.include_router(plugin_router, subpath="demo")
    reg.hooks.register("cycle_end", "post_cycle", "post", 100, lambda c: None)

    snap = reg.snapshot()
    assert snap["customer_code"] == "internal-demo"
    assert len(snap["routes"]) == 1
    assert len(snap["hooks"]) == 1
    # v3.13: 尚未接入的 registry 在 snapshot 里以 *_status 字段标记 (不再是 *_count).
    # 见 backend/plugin_system/registry.py _UnimplementedRegistry.
    # v3.46 F8: export_fields 已真实现, snapshot 改为已注册字段 path 列表.
    assert snap["export_templates_status"] == "not_implemented_F7"
    assert snap["export_fields"] == []
    assert snap["realtime_triggers_status"] == "not_implemented_F9"


# ----------------------- PluginManager 4 参调用契约 -----------------------


def _make_minimal_tier3_plugin(tmpdir: Path, customer_code: str = "internal-demo") -> Path:
    """落出最小可加载的 tier3 插件: 仅 backend/__init__.py 接 4 参 register_plugin."""
    plugin_root = tmpdir / customer_code
    backend_dir = plugin_root / "backend"
    backend_dir.mkdir(parents=True)

    (backend_dir / "__init__.py").write_text(
        """from fastapi import APIRouter
router = APIRouter()

@router.get('/probe')
def probe():
    return {'plugin': 'ok'}

def register_plugin(app, registry, license_payload, host):
    registry.routes.include_router(router, subpath='probe-sub')
    return {'name': 'min-tier3', 'customer_code': host.customer_code}
""",
        encoding="utf-8",
    )
    return plugin_root


def test_manager_load_backend_module_uses_four_args(tmp_path, monkeypatch):
    """如果有人改回 register({'plugin_dir':...}) 1 参调用, 本测试 FAIL."""
    from backend.plugin_system.manager import PluginManager

    install_dir = _make_minimal_tier3_plugin(tmp_path)
    app = FastAPI()
    pm = PluginManager()
    host = PluginHost(customer_code="internal-demo", plugin_dir=str(install_dir))

    module, registry = pm._load_backend_module(
        customer_code="internal-demo",
        install_dir=install_dir,
        app=app,
        license_payload={"customer": "internal-demo"},
        host=host,
    )

    assert module is not None
    assert registry is not None
    assert len(registry.routes.mounted()) == 1
    # 新版 FastAPI app.routes 不再扁平, 用真实请求验证路由已挂通
    from fastapi.testclient import TestClient
    resp = TestClient(app).get("/api/v1/plugins/internal-demo/probe-sub/probe")
    assert resp.status_code == 200 and resp.json() == {"plugin": "ok"}


def test_manager_raises_when_app_missing(tmp_path):
    """app=None 时显式抛 RuntimeError, 避免 register_plugin 拿不到 app 默默失败."""
    from backend.plugin_system.manager import PluginManager

    install_dir = _make_minimal_tier3_plugin(tmp_path)
    pm = PluginManager()
    host = PluginHost(customer_code="internal-demo", plugin_dir=str(install_dir))

    with pytest.raises(RuntimeError, match="必须传入 FastAPI app"):
        pm._load_backend_module(
            customer_code="internal-demo",
            install_dir=install_dir,
            app=None,
            license_payload={},
            host=host,
        )


def test_manager_handles_register_plugin_exception(tmp_path):
    """plugin register_plugin 抛错, _load_backend_module 把错传出来,
    上层 load_active() 兜底写 state=failed (本测试不模拟 DB, 只确认错传出来)."""
    from backend.plugin_system.manager import PluginManager

    install_dir = tmp_path / "buggy"
    (install_dir / "backend").mkdir(parents=True)
    (install_dir / "backend" / "__init__.py").write_text(
        "def register_plugin(app, registry, license_payload, host):\n"
        "    raise RuntimeError('plugin internal bug')\n",
        encoding="utf-8",
    )

    pm = PluginManager()
    app = FastAPI()
    host = PluginHost(customer_code="buggy", plugin_dir=str(install_dir))

    with pytest.raises(RuntimeError, match="plugin internal bug"):
        pm._load_backend_module(
            customer_code="buggy",
            install_dir=install_dir,
            app=app,
            license_payload={},
            host=host,
        )
