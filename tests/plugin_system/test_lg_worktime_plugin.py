"""LG 工时看板插件 — 后端单元测试。

现场叙事:
  领导在 LG 产线工控机上打开监控页 (被插件整页覆盖成工时看板):
  操作员每完成一轮装配, 主程序 sequential 状态机结算一轮 cycle;
  插件 cycle_end hook 按项目步骤配置里的 VA/BVA/NVA 动作价值把各步骤耗时归类、
  步骤间等待归 NVA, 冻结写入 p_lg_worktime_cycle_lean;
  看板 1s 轮询 /dashboard/live 显示当前步骤与实时 LEAN 分解,
  /dashboard/summary 显示今日产量/良率/CT/LEAN 占比。

覆盖:
  1. lean.py 纯逻辑 (归类口径 / 等待归 NVA / 占比 / 配置提取)
  2. hook 状态机 (cycle_start/step_change/step_tick → live 快照)
  3. on_cycle_end 真 DB 落库 (读 step_records → 写 lean 表)
  4. register_plugin 全量注册 + dashboard 路由 TestClient 走通
  5. F8 导出字段 provider (cycle/session 双口径)
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins-examples" / "lg-worktime"


def _load_plugin_pkg(name: str = "tianjun_plugin_lg_worktime_test"):
    """按 PluginManager 同款方式把插件后端加载成包 (支持相对 import)。"""
    entry = PLUGIN_DIR / "backend" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        name, entry,
        submodule_search_locations=[str(PLUGIN_DIR / "backend")],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def plugin_mod():
    return _load_plugin_pkg()


@pytest.fixture(autouse=True)
def _reset_plugin_state(plugin_mod):
    def _reset():
        plugin_mod._LIVE.clear()
        plugin_mod._STEP_VALUES_CACHE.clear()
        plugin_mod._HOST = None
        importlib.import_module(f"{plugin_mod.__name__}.settings").invalidate_cache()
    _reset()
    yield
    _reset()


class FakeHost:
    """get_db_session 直连测试隔离库 (conftest 已把 DATA_DIR 指到 tmp)。"""

    customer_code = "lg-worktime"

    def get_db_session(self):
        from backend.db.database import SessionLocal
        return SessionLocal()


def _submodule(plugin_mod, name: str):
    return importlib.import_module(f"{plugin_mod.__name__}.{name}")


def _ensure_lean_table(plugin_mod):
    from backend.db.database import engine
    models_mod = _submodule(plugin_mod, "models")
    models_mod.PluginLgWorktimeCycleLean.__table__.create(engine, checkfirst=True)
    return models_mod.PluginLgWorktimeCycleLean


# ============================================================
# 1. lean.py 纯逻辑
# ============================================================

def _lean(plugin_mod):
    return _submodule(plugin_mod, "lean")


def test_normalize_value_type(plugin_mod):
    lean = _lean(plugin_mod)
    assert lean.normalize_value_type("VA") == "VA"
    assert lean.normalize_value_type("bva") == "BVA"
    assert lean.normalize_value_type(" nva ") == "NVA"
    assert lean.normalize_value_type(None) == "VA"       # 未配置默认 VA
    assert lean.normalize_value_type("whatever") == "VA"  # 非法值退默认


def test_step_values_from_steps_config(plugin_mod):
    """v1.5.0 契约: 只收录显式且合法的配置 — 未配置/别家插件/非法值不进映射,
    让消费方按全局 default_value_type 兜底 (否则全局默认永远不生效)。"""
    lean = _lean(plugin_mod)
    steps_config = [
        {"label": "tighten", "plugin_data": {"lg-worktime": {"value_type": "VA"}}},
        {"label": "fetch_tool", "plugin_data": {"lg-worktime": {"value_type": "BVA"}}},
        {"label": "walk_around", "plugin_data": {"lg-worktime": {"value_type": "NVA"}}},
        {"label": "no_config"},                                  # 未配置 → 不进映射
        {"label": "other_plugin", "plugin_data": {"someone-else": {"value_type": "NVA"}}},
        {"label": "bad_value", "plugin_data": {"lg-worktime": {"value_type": "???"}}},
        {"no_label": True},                                      # 容错: 缺 label 跳过
    ]
    values = lean.step_values_from_steps_config(steps_config)
    assert values == {"tighten": "VA", "fetch_tool": "BVA", "walk_around": "NVA"}
    # 容错: 非 list 输入
    assert lean.step_values_from_steps_config(None) == {}
    assert lean.step_values_from_steps_config({"not": "a list"}) == {}


def test_compute_lean_classification_and_wait(plugin_mod):
    lean = _lean(plugin_mod)
    steps = [
        {"label": "tighten", "duration": 10.0, "interval_from_prev": None},
        {"label": "fetch_tool", "duration": 4.0, "interval_from_prev": 2.0},
        {"label": "walk_around", "duration": 3.0, "interval_from_prev": 1.5},
        {"label": "unknown_step", "duration": 5.0, "interval_from_prev": 0.5},  # 默认 VA
    ]
    values = {"tighten": "VA", "fetch_tool": "BVA", "walk_around": "NVA"}
    out = lean.compute_lean(steps, values)
    assert out["va"] == pytest.approx(15.0)       # 10 + 5(默认VA)
    assert out["bva"] == pytest.approx(4.0)
    assert out["nva"] == pytest.approx(3.0)       # 步骤级 NVA
    assert out["wait"] == pytest.approx(4.0)      # 2 + 1.5 + 0.5 等待
    assert out["nva_total"] == pytest.approx(7.0)  # 等待归 NVA
    assert out["total"] == pytest.approx(26.0)


def test_compute_lean_tolerates_bad_input(plugin_mod):
    lean = _lean(plugin_mod)
    out = lean.compute_lean(
        [{"label": "a", "duration": "oops", "interval_from_prev": "bad"}, None, "junk"],
        {},
    )
    assert out["total"] == 0.0


def test_lean_ratios(plugin_mod):
    lean = _lean(plugin_mod)
    r = lean.lean_ratios(60.0, 20.0, 20.0)
    assert r == {"va_ratio": 60.0, "bva_ratio": 20.0, "nva_ratio": 20.0}
    r0 = lean.lean_ratios(0, 0, 0)
    assert r0 == {"va_ratio": None, "bva_ratio": None, "nva_ratio": None}


# ============================================================
# 1b. v1.5.0 LG 口径全参数可配 (默认价值 / 等待归类 / 设置规整)
# ============================================================

def test_compute_lean_default_value_type_configurable(plugin_mod):
    """未配置步骤按全局默认价值归类 (冻结期参数)。"""
    lean = _lean(plugin_mod)
    steps = [{"label": "unknown", "duration": 6.0, "interval_from_prev": None}]
    assert lean.compute_lean(steps, {})["va"] == pytest.approx(6.0)          # 出厂 VA
    out = lean.compute_lean(steps, {}, default_value_type="NVA")
    assert out["nva"] == pytest.approx(6.0) and out["va"] == 0.0
    out = lean.compute_lean(steps, {}, default_value_type="BVA")
    assert out["bva"] == pytest.approx(6.0)
    # 非法默认值退 VA, 不炸
    assert lean.compute_lean(steps, {}, default_value_type="???")["va"] == pytest.approx(6.0)


def test_apply_wait_all_modes(plugin_mod):
    """等待归类四档折算 (统计口径参数, 查询/展示时生效)。"""
    lean = _lean(plugin_mod)
    raw = {"va": 10.0, "bva": 4.0, "nva": 3.0, "wait": 2.0}

    nva = lean.apply_wait(raw, "NVA")   # 出厂: 等待归 NVA
    assert (nva["va"], nva["bva"], nva["nva_total"], nva["total"]) == (10.0, 4.0, 5.0, 19.0)

    bva = lean.apply_wait(raw, "BVA")
    assert (bva["va"], bva["bva"], bva["nva_total"], bva["total"]) == (10.0, 6.0, 3.0, 19.0)

    va = lean.apply_wait(raw, "VA")
    assert (va["va"], va["bva"], va["nva_total"], va["total"]) == (12.0, 4.0, 3.0, 19.0)

    ex = lean.apply_wait(raw, "EXCLUDE")  # 等待不计入任何桶, total 也不含
    assert (ex["va"], ex["bva"], ex["nva_total"], ex["total"]) == (10.0, 4.0, 3.0, 17.0)

    # wait/nva 键恒为原始值 (KPI 单列展示)
    for out in (nva, bva, va, ex):
        assert out["wait"] == 2.0 and out["nva"] == 3.0
    # 非法归类退出厂 NVA
    assert lean.apply_wait(raw, "whatever")["nva_total"] == 5.0


def test_normalize_settings(plugin_mod):
    """设置规整: 缺失/非法项退出厂默认, trend_days 夹在 1~60。"""
    settings_mod = _submodule(plugin_mod, "settings")
    assert settings_mod.normalize_settings(None) == settings_mod.DEFAULTS
    out = settings_mod.normalize_settings({
        "default_value_type": "bva", "wait_value_type": "exclude",
        "lean_scope": "ALL", "trend_days": "14",
    })
    assert out == {"default_value_type": "BVA", "wait_value_type": "EXCLUDE",
                   "lean_scope": "all", "trend_days": 14}
    assert settings_mod.normalize_settings({"trend_days": 999})["trend_days"] == 60
    assert settings_mod.normalize_settings({"trend_days": -3})["trend_days"] == 1
    assert settings_mod.normalize_settings({"lean_scope": "junk"})["lean_scope"] == "good_only"


@pytest.fixture()
def _clean_settings_kv(plugin_mod):
    """改设置的测试收尾删 KV 行 + 失效缓存 (db_session 不回滚已提交数据, 防跨测试串味)。"""
    yield
    settings_mod = _submodule(plugin_mod, "settings")
    from backend.db.database import SessionLocal
    from backend.models.models import SystemConfig
    db = SessionLocal()
    try:
        db.query(SystemConfig).filter(SystemConfig.key == settings_mod.SETTINGS_KEY).delete()
        db.commit()
    finally:
        db.close()
    settings_mod.invalidate_cache()


def test_settings_roundtrip_and_isolation(plugin_mod, db_session, _clean_settings_kv):
    """save→get 走真 SystemConfig KV; host 缺失时降级出厂默认不炸。"""
    settings_mod = _submodule(plugin_mod, "settings")
    assert settings_mod.get_settings(None) == settings_mod.DEFAULTS  # 无 host 降级

    host = FakeHost()
    merged = settings_mod.save_settings(host, {"wait_value_type": "BVA", "trend_days": 30})
    assert merged["wait_value_type"] == "BVA" and merged["trend_days"] == 30
    assert merged["default_value_type"] == "VA"  # 未提及键保留
    got = settings_mod.get_settings(host, force=True)
    assert got == merged
    # 部分更新不丢已有值
    merged2 = settings_mod.save_settings(host, {"lean_scope": "all"})
    assert merged2["wait_value_type"] == "BVA" and merged2["lean_scope"] == "all"


# ============================================================
# 2. hook 状态机 → live 快照
# ============================================================

def test_live_state_machine(plugin_mod):
    plugin_mod.on_cycle_start({
        "channel_id": 0, "cycle_id": 101, "cycle_number": 7,
        "session_id": 1, "project_id": None,
    })
    plugin_mod.on_step_change({
        "channel_id": 0, "cycle_id": 101, "step_label": "tighten",
        "step_name": "打螺丝", "duration": 9.5, "interval_from_prev": None,
    })
    plugin_mod.on_step_tick({
        "channel_id": 0, "cycle_id": 101, "step_label": "fetch_tool",
        "step_name": "取工具", "elapsed_sec": 2.4,
    })

    snap = plugin_mod._live_snapshot()
    ch = snap["channels"]["0"]
    assert ch["in_cycle"] is True
    assert ch["cycle_id"] == 101
    assert ch["cycle_number"] == 7
    assert len(ch["steps_done"]) == 1
    assert ch["steps_done"][0]["label"] == "tighten"
    assert ch["steps_done"][0]["value_type"] == "VA"  # 无项目配置默认 VA
    assert ch["current_step"]["label"] == "fetch_tool"
    assert ch["current_step"]["elapsed"] == pytest.approx(2.4)
    # 实时 LEAN: 已完成 9.5 + 进行中 2.4 都按 VA 归类
    assert ch["lean_live"]["va"] == pytest.approx(11.9)


def test_stale_ctx_from_other_cycle_ignored(plugin_mod):
    """cycle_id 不匹配的迟到 tick/step 不得污染当前周期。"""
    plugin_mod.on_cycle_start({"channel_id": 0, "cycle_id": 200, "project_id": None})
    plugin_mod.on_step_change({
        "channel_id": 0, "cycle_id": 199, "step_label": "old", "duration": 5.0,
    })
    plugin_mod.on_step_tick({
        "channel_id": 0, "cycle_id": 199, "step_label": "old", "elapsed_sec": 3.0,
    })
    snap = plugin_mod._live_snapshot()
    ch = snap["channels"]["0"]
    assert ch["steps_done"] == []
    assert ch["current_step"] is None


# ============================================================
# 3. on_cycle_end 真 DB 落库
# ============================================================

def _mk_session_cycle_steps(db, *, channel_id=0, project_id=None, is_good=True,
                            end_time=None, steps=()):
    from backend.models.models import DetectionSession, DetectionCycle, StepRecord

    end_time = end_time or dt.datetime.now()
    s = DetectionSession(
        session_uuid=str(uuid.uuid4()), channel_id=channel_id, project_id=project_id,
        start_time=end_time - dt.timedelta(minutes=5), status="running",
    )
    db.add(s)
    db.flush()
    c = DetectionCycle(
        cycle_uuid=str(uuid.uuid4()), session_id=s.id, cycle_number=1,
        start_time=end_time - dt.timedelta(seconds=30), end_time=end_time,
        duration=26.0, is_good=is_good,
    )
    db.add(c)
    db.flush()
    for i, (label, duration, interval) in enumerate(steps):
        db.add(StepRecord(
            record_uuid=str(uuid.uuid4()), cycle_id=c.id,
            step_label=label, step_name=label, step_order=i,
            start_time=end_time - dt.timedelta(seconds=25 - i),
            duration=duration, interval_from_prev=interval,
        ))
    db.commit()
    return s, c


def _mk_project_with_values(db, values: dict):
    from backend.models.models import Project
    steps_config = [
        {"label": label, "plugin_data": {"lg-worktime": {"value_type": vt}}}
        for label, vt in values.items()
    ]
    p = Project(name=f"lg-test-{uuid.uuid4().hex[:6]}", steps_config=steps_config)
    db.add(p)
    db.commit()
    return p


def test_on_cycle_end_persists_frozen_lean(plugin_mod, db_session):
    Lean = _ensure_lean_table(plugin_mod)
    plugin_mod._HOST = FakeHost()

    proj = _mk_project_with_values(db_session, {
        "tighten": "VA", "fetch_tool": "BVA", "walk_around": "NVA",
    })
    s, c = _mk_session_cycle_steps(db_session, project_id=proj.id, steps=[
        ("tighten", 10.0, None),
        ("fetch_tool", 4.0, 2.0),
        ("walk_around", 3.0, 1.5),
    ])

    plugin_mod.on_cycle_end({
        "channel_id": 0, "cycle_id": c.id, "session_id": s.id,
        "project_id": proj.id, "is_good": True, "duration": 26.0,
    })

    row = db_session.query(Lean).filter(Lean.cycle_id == c.id).first()
    assert row is not None, "cycle_end 后 lean 表必须有冻结记录"
    assert row.va_seconds == pytest.approx(10.0)
    assert row.bva_seconds == pytest.approx(4.0)
    assert row.nva_seconds == pytest.approx(3.0)
    assert row.wait_seconds == pytest.approx(3.5)
    assert row.is_good is True
    assert row.session_id == s.id
    assert row.project_id == proj.id
    assert row.end_time is not None


def test_on_cycle_end_without_host_is_isolated(plugin_mod):
    """host 缺失 (加载半途失败) 时 hook 不抛异常。"""
    plugin_mod._HOST = None
    plugin_mod.on_cycle_end({"channel_id": 0, "cycle_id": 999999})


# ============================================================
# 4. register_plugin 全量注册 + dashboard 路由
# ============================================================

@pytest.fixture()
def mounted_app(plugin_mod):
    from fastapi import FastAPI
    from backend.db.database import engine
    from backend.plugin_system.registry import PluginRegistry
    from backend.services import export_field_registry as efr

    app = FastAPI()
    registry = PluginRegistry(app=app, engine=engine, customer_code="lg-worktime")
    plugin_mod.register_plugin(app, registry, {}, FakeHost())
    yield app, registry
    efr.unregister_plugin_fields("lg-worktime")
    plugin_mod._HOST = None


def test_register_plugin_registers_everything(mounted_app):
    app, registry = mounted_app
    snap = registry.snapshot()
    assert "p_lg_worktime_cycle_lean" in snap["tables"]
    hook_types = {h["hook_type"] for h in snap["hooks"]}
    assert hook_types == {"cycle_start", "step_tick", "step_change", "cycle_end"}
    assert any("/api/v1/plugins/lg-worktime/dashboard" in r["prefix"] for r in snap["routes"])
    assert any(p.startswith("plugin.lg_worktime.") for p in snap["export_fields"])


def test_dashboard_routes_end_to_end(mounted_app, plugin_mod, db_session):
    from fastapi.testclient import TestClient

    app, _ = mounted_app
    Lean = _ensure_lean_table(plugin_mod)

    proj = _mk_project_with_values(db_session, {"tighten": "VA", "fetch_tool": "BVA"})
    s, c = _mk_session_cycle_steps(db_session, project_id=proj.id, steps=[
        ("tighten", 10.0, None), ("fetch_tool", 4.0, 2.0),
    ])
    plugin_mod.on_cycle_end({
        "channel_id": 0, "cycle_id": c.id, "session_id": s.id,
        "project_id": proj.id, "is_good": True, "duration": 26.0,
    })
    # 再补一轮合格 — 防回归: sum(布尔表达式) 类型截断会把良品数钉死在 1
    s2, c2 = _mk_session_cycle_steps(db_session, project_id=proj.id, steps=[
        ("tighten", 9.0, None),
    ])
    plugin_mod.on_cycle_end({
        "channel_id": 0, "cycle_id": c2.id, "session_id": s2.id,
        "project_id": proj.id, "is_good": True, "duration": 9.0,
    })

    client = TestClient(app)
    base = "/api/v1/plugins/lg-worktime/dashboard"

    r = client.get(f"{base}/live")
    assert r.status_code == 200 and "channels" in r.json()

    r = client.get(f"{base}/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["total_cycles"] >= 2
    assert data["good_cycles"] >= 2, "良品数被 Boolean 类型截断 (sum(expr) 必须走 case)"
    assert data["lean"]["good_rounds"] >= 1
    assert data["lean"]["va"] >= 10.0
    assert data["lean"]["va_ratio"] is not None

    r = client.get(f"{base}/summary", params={"channel_id": 0, "project_id": proj.id})
    assert r.status_code == 200 and r.json()["total_cycles"] >= 1
    r = client.get(f"{base}/summary", params={"channel_id": 42})
    assert r.status_code == 200 and r.json()["total_cycles"] == 0

    r = client.get(f"{base}/trend", params={"days": 3})
    assert r.status_code == 200
    days = r.json()["days"]
    assert len(days) == 3
    assert days[-1]["total"] >= 1 and days[-1]["va"] >= 10.0

    r = client.get(f"{base}/step-values", params={"project_id": proj.id})
    assert r.status_code == 200
    assert r.json()["values"] == {"tighten": "VA", "fetch_tool": "BVA"}

    r = client.get(f"{base}/step-averages", params={"project_id": proj.id})
    assert r.status_code == 200
    steps = {x["label"]: x for x in r.json()["steps"]}
    assert steps["tighten"]["value_type"] == "VA"
    assert steps["tighten"]["avg_duration"] == pytest.approx(9.5)  # (10.0 + 9.0) / 2
    assert steps["fetch_tool"]["value_type"] == "BVA"
    assert steps["fetch_tool"]["avg_wait"] == pytest.approx(2.0)


def test_device_info_returns_real_values(mounted_app):
    """v1.5.1 设备卡真值: 开机时长为正整数; 温度要么 null (无传感器) 要么合理数值。"""
    from fastapi.testclient import TestClient

    app, _ = mounted_app
    r = TestClient(app).get("/api/v1/plugins/lg-worktime/dashboard/device-info")
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d["uptime_seconds"], int) and d["uptime_seconds"] > 0
    assert d["boot_time"]  # ISO 字符串
    t = d["temperature_c"]
    assert t is None or (0 < float(t) < 120), f"温度读数不合理: {t}"
    if t is not None:
        assert d["temperature_source"] in ("gpu", "cpu", "battery")


def test_settings_api_drives_dashboard_scope(mounted_app, plugin_mod, db_session,
                                             _clean_settings_kv):
    """v1.5.0: /settings 改口径 → summary/trend 立即按新口径出数。

    数据: 1 合格轮 (VA10 + BVA4 + 等待2) + 1 NG 轮 (VA5)。
    """
    from fastapi.testclient import TestClient

    app, _ = mounted_app
    _ensure_lean_table(plugin_mod)

    proj = _mk_project_with_values(db_session, {"tighten": "VA", "fetch_tool": "BVA"})
    s, c = _mk_session_cycle_steps(db_session, project_id=proj.id, is_good=True, steps=[
        ("tighten", 10.0, None), ("fetch_tool", 4.0, 2.0),
    ])
    plugin_mod.on_cycle_end({"channel_id": 0, "cycle_id": c.id, "session_id": s.id,
                             "project_id": proj.id, "is_good": True, "duration": 16.0})
    s2, c2 = _mk_session_cycle_steps(db_session, project_id=proj.id, is_good=False, steps=[
        ("tighten", 5.0, None),
    ])
    plugin_mod.on_cycle_end({"channel_id": 0, "cycle_id": c2.id, "session_id": s2.id,
                             "project_id": proj.id, "is_good": False, "duration": 5.0})

    client = TestClient(app)
    base = "/api/v1/plugins/lg-worktime/dashboard"

    # 出厂默认: 仅合格轮 + 等待归 NVA
    r = client.get(f"{base}/settings")
    assert r.status_code == 200
    assert r.json()["settings"] == {"default_value_type": "VA", "wait_value_type": "NVA",
                                    "lean_scope": "good_only", "trend_days": 7}
    L = client.get(f"{base}/summary", params={"project_id": proj.id}).json()["lean"]
    assert L["good_rounds"] == 1 and L["scope"] == "good_only"
    assert L["va"] == pytest.approx(10.0) and L["nva_total"] == pytest.approx(2.0)

    # 切: 全部轮 + 等待归 BVA + 趋势 3 天
    r = client.post(f"{base}/settings", json={
        "lean_scope": "all", "wait_value_type": "BVA", "trend_days": 3,
    })
    assert r.status_code == 200 and r.json()["status"] == "success"

    L = client.get(f"{base}/summary", params={"project_id": proj.id}).json()["lean"]
    assert L["good_rounds"] == 2 and L["scope"] == "all"          # NG 轮进统计
    assert L["va"] == pytest.approx(15.0)                          # 10 + NG 轮 5
    assert L["bva"] == pytest.approx(6.0)                          # 4 + 等待 2 折入
    assert L["nva_total"] == pytest.approx(0.0)
    assert L["wait"] == pytest.approx(2.0)                         # 原始等待仍单列

    # days 缺省走设置; project 过滤防同库其它测试的当日轮次串数
    t = client.get(f"{base}/trend", params={"project_id": proj.id}).json()
    assert t["trend_days"] == 3 and len(t["days"]) == 3
    assert t["days"][-1]["va"] == pytest.approx(15.0)
    assert t["days"][-1]["bva"] == pytest.approx(6.0)

    # 等待不计入: 桶里都没有等待
    client.post(f"{base}/settings", json={"wait_value_type": "EXCLUDE"})
    L = client.get(f"{base}/summary", params={"project_id": proj.id}).json()["lean"]
    assert L["bva"] == pytest.approx(4.0) and L["nva_total"] == pytest.approx(0.0)

    # 非法值静默规整回合法域
    r = client.post(f"{base}/settings", json={"lean_scope": "junk", "trend_days": 999})
    assert r.json()["settings"]["lean_scope"] == "good_only"
    assert r.json()["settings"]["trend_days"] == 60


def test_default_value_type_freezes_at_cycle_end(mounted_app, plugin_mod, db_session,
                                                 _clean_settings_kv):
    """默认价值是冻结期参数: 改设置只影响之后结算的周期, 不回写历史。"""
    from fastapi.testclient import TestClient

    app, _ = mounted_app
    Lean = _ensure_lean_table(plugin_mod)
    settings_mod = _submodule(plugin_mod, "settings")

    proj = _mk_project_with_values(db_session, {})  # 无任何步骤价值配置
    s, c = _mk_session_cycle_steps(db_session, project_id=proj.id, steps=[
        ("mystery", 8.0, None),
    ])
    plugin_mod.on_cycle_end({"channel_id": 0, "cycle_id": c.id, "session_id": s.id,
                             "project_id": proj.id, "is_good": True, "duration": 8.0})
    row = db_session.query(Lean).filter(Lean.cycle_id == c.id).first()
    assert row.va_seconds == pytest.approx(8.0)      # 出厂默认 VA

    settings_mod.save_settings(FakeHost(), {"default_value_type": "NVA"})
    s2, c2 = _mk_session_cycle_steps(db_session, project_id=proj.id, steps=[
        ("mystery", 6.0, None),
    ])
    plugin_mod.on_cycle_end({"channel_id": 0, "cycle_id": c2.id, "session_id": s2.id,
                             "project_id": proj.id, "is_good": True, "duration": 6.0})
    db_session.expire_all()
    row2 = db_session.query(Lean).filter(Lean.cycle_id == c2.id).first()
    assert row2.nva_seconds == pytest.approx(6.0) and row2.va_seconds == 0.0  # 新周期按新默认
    row = db_session.query(Lean).filter(Lean.cycle_id == c.id).first()
    assert row.va_seconds == pytest.approx(8.0)      # 历史冻结不回写


# ============================================================
# 5. F8 导出字段 provider
# ============================================================

def test_export_provider_cycle_and_session(plugin_mod, db_session):
    _ensure_lean_table(plugin_mod)
    plugin_mod._HOST = FakeHost()
    export_fields = _submodule(plugin_mod, "export_fields")

    proj = _mk_project_with_values(db_session, {"tighten": "VA", "walk_around": "NVA"})
    s, c = _mk_session_cycle_steps(db_session, project_id=proj.id, steps=[
        ("tighten", 12.0, None), ("walk_around", 3.0, 1.0),
    ])
    plugin_mod.on_cycle_end({
        "channel_id": 0, "cycle_id": c.id, "session_id": s.id,
        "project_id": proj.id, "is_good": True, "duration": 16.0,
    })

    out = export_fields.export_provider(db_session, {
        "cycle": {"id": c.id}, "session": {"id": s.id},
    })
    assert out["cycle_va_seconds"] == pytest.approx(12.0)
    assert out["cycle_nva_seconds"] == pytest.approx(3.0)
    assert out["cycle_wait_seconds"] == pytest.approx(1.0)
    assert out["cycle_nva_total_seconds"] == pytest.approx(4.0)
    assert out["cycle_va_ratio"] == pytest.approx(75.0)
    assert out["session_good_rounds"] == 1
    assert out["session_va_seconds"] == pytest.approx(12.0)
    assert out["session_nva_seconds"] == pytest.approx(4.0)   # 含等待
    assert out["session_lean_total_seconds"] == pytest.approx(16.0)


def test_export_provider_missing_history_gives_empty(plugin_mod, db_session):
    """插件启用前的历史周期没有 lean 行 — provider 不给键, 模板渲染为空。"""
    _ensure_lean_table(plugin_mod)
    export_fields = _submodule(plugin_mod, "export_fields")
    out = export_fields.export_provider(db_session, {"cycle": {"id": 987654321}})
    assert "cycle_va_seconds" not in out


def test_export_provider_cycles_map_for_range(plugin_mod, db_session):
    """范围导出: 无单 session 主体时兜 aggregations.sessions 给逐周期映射。"""
    _ensure_lean_table(plugin_mod)
    plugin_mod._HOST = FakeHost()
    export_fields = _submodule(plugin_mod, "export_fields")

    proj = _mk_project_with_values(db_session, {"tighten": "VA"})
    s, c = _mk_session_cycle_steps(db_session, project_id=proj.id, steps=[
        ("tighten", 8.0, None),
    ])
    plugin_mod.on_cycle_end({
        "channel_id": 0, "cycle_id": c.id, "session_id": s.id,
        "project_id": proj.id, "is_good": True, "duration": 8.0,
    })

    out = export_fields.export_provider(db_session, {
        "session": {"id": None},
        "aggregations": {"sessions": [{"id": s.id}]},
    })
    assert c.id in out["cycles"]
    assert out["cycles"][c.id]["va"] == pytest.approx(8.0)
    assert out["session_good_rounds"] == 1


# ============================================================
# 6. 导出模板 (LG 日明细 / LEAN 汇总) 渲染 + 植入
# ============================================================

def _read_template(fname: str) -> str:
    return (PLUGIN_DIR / "templates" / fname).read_text(encoding="utf-8")


@pytest.fixture()
def range_ctx_with_lean(plugin_mod, db_session):
    """建一轮真实数据 → 注册插件字段 → build_range_context 拿到含 plugin 子树的上下文。"""
    from backend.services import export_field_registry as efr
    from backend.services.export_context import build_range_context

    _ensure_lean_table(plugin_mod)
    plugin_mod._HOST = FakeHost()
    export_fields = _submodule(plugin_mod, "export_fields")

    proj = _mk_project_with_values(db_session, {"tighten": "VA", "fetch_tool": "BVA"})
    s, c = _mk_session_cycle_steps(db_session, project_id=proj.id, steps=[
        ("tighten", 10.0, None), ("fetch_tool", 4.0, 2.0),
    ])
    plugin_mod.on_cycle_end({
        "channel_id": 0, "cycle_id": c.id, "session_id": s.id,
        "project_id": proj.id, "is_good": True, "duration": 16.0,
    })
    from backend.plugin_system.registry import ExportFieldsRegistry
    ExportFieldsRegistry("lg-worktime").register(
        fields=export_fields.EXPORT_FIELDS, provider=export_fields.export_provider)
    ctx = build_range_context(db_session, session_id=s.id, include_cycles=True)
    yield ctx, c
    efr.unregister_plugin_fields("lg-worktime")


def test_lg_daily_detail_template_renders(range_ctx_with_lean):
    from backend.services.export_renderer import render_string, render_to_bytes

    ctx, c = range_ctx_with_lean
    tpl = _read_template("lg_daily_detail.csv.j2")
    text = render_string(tpl, ctx)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].startswith("轮次ID,轮次号,工位,开始时间")
    row = next(ln for ln in lines[1:] if ln.startswith(f"{c.id},"))
    cols = row.split(",")
    assert cols[5] == "26.00"          # CT (helper 固定 26s)
    assert cols[6] == "10.00"          # VA
    assert cols[7] == "4.00"           # BVA
    assert cols[9] == "2.00"           # 等待
    assert "tighten(10.0s)" in row and "fetch_tool(4.0s)" in row
    # xlsx 路线 A 出真 Excel (zip magic)
    raw, mime = render_to_bytes(tpl, ctx, fmt="xlsx")
    assert raw[:2] == b"PK" and "sheet" in mime


def test_lg_lean_summary_template_renders(range_ctx_with_lean):
    from backend.services.export_renderer import render_string, render_to_bytes

    ctx, _ = range_ctx_with_lean
    tpl = _read_template("lg_lean_summary.csv.j2")
    text = render_string(tpl, ctx)
    assert "合格轮数(计入LEAN),1" in text
    assert "累计VA(秒),10.0" in text
    assert "累计BVA(秒),4.0" in text
    assert "累计NVA(秒,含等待),2.0" in text
    assert "LEAN总计(秒),16.0" in text
    raw, _mime = render_to_bytes(tpl, ctx, fmt="xlsx")
    assert raw[:2] == b"PK"


def test_lg_templates_survive_plugin_inactive(plugin_mod, db_session):
    """插件停用 (字段未注册) 时模板渲染不崩, LEAN 列为空。"""
    from backend.services.export_context import build_range_context
    from backend.services.export_renderer import render_string

    _ensure_lean_table(plugin_mod)
    plugin_mod._HOST = FakeHost()
    proj = _mk_project_with_values(db_session, {"tighten": "VA"})
    s, c = _mk_session_cycle_steps(db_session, project_id=proj.id, steps=[
        ("tighten", 5.0, None),
    ])
    ctx = build_range_context(db_session, session_id=s.id, include_cycles=True)
    assert ctx["plugin"] == {}  # 未注册 → 空命名空间

    for fname in ("lg_daily_detail.csv.j2", "lg_lean_summary.csv.j2"):
        text = render_string(_read_template(fname), ctx)
        assert text  # 不崩即可; 明细行仍有 CT 列
    detail = render_string(_read_template("lg_daily_detail.csv.j2"), ctx)
    row = next(ln for ln in detail.splitlines() if ln.startswith(f"{c.id},"))
    assert ",26.00," in row  # CT 还在 (helper 固定 26s), LEAN 列空


def test_seed_export_templates_idempotent(plugin_mod, db_session):
    from backend.models.export_models import ExportTemplate

    plugin_mod._HOST = FakeHost()
    plugin_mod._seed_export_templates()
    names = [n for (n,) in db_session.query(ExportTemplate.name).all()]
    assert "LG 日明细 (lg-worktime)" in names
    assert "LG LEAN统计汇总 (lg-worktime)" in names

    # 幂等: 再跑一次不重复; 用户改过的内容不被覆盖
    row = db_session.query(ExportTemplate).filter(
        ExportTemplate.name == "LG 日明细 (lg-worktime)").first()
    row.content = "用户改过"
    db_session.commit()
    plugin_mod._seed_export_templates()
    db_session.expire_all()
    rows = db_session.query(ExportTemplate).filter(
        ExportTemplate.name == "LG 日明细 (lg-worktime)").all()
    assert len(rows) == 1 and rows[0].content == "用户改过"
    assert rows[0].format == "xlsx"
