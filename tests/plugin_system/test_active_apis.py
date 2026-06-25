"""M1.3a 主动 API 测试: PluginHost.trigger_alarm / mes_push / read_system_config / write_system_config + 2 stub

客户视角叙事:
  ACME 客户的 Tier 3 全栈插件需要在 cycle_end hook 里:
  1. 检测到关键步骤超时, 直接触发主程序的"严重 NG 灯柱" (走 AlarmRouter)
  2. 把检测结果立刻推到客户自建 MES (走 MES Gateway)
  3. 跨周期记住"上轮 NG 累计次数", 持久化到 SystemConfig

  改进前 (v3.7.0~v3.12.0):
    插件作者只能 from backend.api.alarm import alarm_router 直接 import + 调用,
    主程序无法约束 / 审计 / 撤销越权操作.

  改进后 (本测试守护 M1.3a):
    1. **capabilities 声明门槛**: 写 / 外推动作必须 manifest 声明能力
    2. **命名空间隔离**: write_system_config / mes_push 必须用 plugin_<cc>_ 前缀
    3. **审计落库**: 所有写动作进 plugin_audit_log
    4. **错误隔离**: 主动 API 异常 swallow + 返 False, 主程序流程不连带断裂
    5. **stub 报错**: write_plugin_step_field / broadcast_to_channel_group 抛 PluginNotImplementedError

关键设计:
- _require_capability: 防止 hook 里偷偷越权调 trigger_alarm
- _require_plugin_namespace: 防止插件踩主程序保留 key (license-cache / display.*)
- _audit_log 独立 session: 不依赖调用方 session, audit 失败也不影响主动 API 返回
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ----------------------- 隔离 DB fixture -----------------------


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """独立 sqlite + 注入 SessionLocal — 与父 conftest 完全隔离.

    需要建 3 套表:
    - core (SystemConfig)
    - mes (无直接用, 但 audit 落库前需要 plugin_audit_log 这张表存在)
    - plugin_models (PluginAuditLog)
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_url = f"sqlite:///{tmp_path}/active_apis.db"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    from backend.models.models import Base as CoreBase
    import backend.models.plugin_models  # noqa: F401 触发 plugin_audit_log 表注册

    CoreBase.metadata.create_all(engine)

    LocalSession = sessionmaker(bind=engine)

    import backend.db.database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", LocalSession, raising=True)

    return LocalSession


@pytest.fixture
def host_no_caps(isolated_db):
    """没声明任何 capabilities 的 host (用于"未声明拒绝调用"测试)."""
    from backend.plugin_system.registry import PluginHost
    return PluginHost(
        customer_code="acme",
        plugin_dir="/tmp",
        main_version="3.13.0",
        capabilities=[],
    )


@pytest.fixture
def host_full_caps(isolated_db):
    """声明所有主动 API capabilities 的 host (v3.13 M3.3 加 step_field_write)."""
    from backend.plugin_system.registry import PluginHost
    return PluginHost(
        customer_code="acme",
        plugin_dir="/tmp",
        main_version="3.13.0",
        capabilities=[
            "runtime.alarm_trigger",
            "runtime.event_trigger",
            "runtime.mes_push",
            "runtime.system_config_write",
            "runtime.step_field_write",
        ],
    )


def _audit_rows(SessionLocal):
    """取 plugin_audit_log 全部行 (顺序)."""
    from backend.models.plugin_models import PluginAuditLog
    db = SessionLocal()
    try:
        return db.query(PluginAuditLog).order_by(PluginAuditLog.id).all()
    finally:
        db.close()


# ============================================================
# A. 异常类基础契约
# ============================================================


def test_plugin_runtime_error_is_runtime_error():
    """PluginRuntimeError 必须是 RuntimeError 子类, 让上层 except RuntimeError 能捕到."""
    from backend.plugin_system.registry import PluginRuntimeError
    assert issubclass(PluginRuntimeError, RuntimeError)


def test_plugin_runtime_error_distinct_from_not_implemented():
    """与 PluginNotImplementedError 区分: 一个是"没做", 一个是"做了但你越权"."""
    from backend.plugin_system.registry import PluginNotImplementedError, PluginRuntimeError
    assert not issubclass(PluginRuntimeError, PluginNotImplementedError)
    assert not issubclass(PluginNotImplementedError, PluginRuntimeError)


# ============================================================
# B. capabilities 声明校验
# ============================================================


def test_trigger_alarm_rejects_when_capability_not_declared(host_no_caps, isolated_db):
    """没声明 runtime.alarm_trigger → 调 trigger_alarm 抛 PluginRuntimeError."""
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError, match="runtime.alarm_trigger"):
        host_no_caps.trigger_alarm(channel_id=0, event_type="event2", reason="test")


def test_mes_push_rejects_when_capability_not_declared(host_no_caps, isolated_db):
    """没声明 runtime.mes_push → 抛."""
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError, match="runtime.mes_push"):
        host_no_caps.mes_push(event_type="plugin_acme_x", payload={"a": 1})


def test_write_system_config_rejects_when_capability_not_declared(host_no_caps, isolated_db):
    """没声明 runtime.system_config_write → 抛."""
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError, match="runtime.system_config_write"):
        host_no_caps.write_system_config(key="plugin_acme_x", value="1")


def test_read_system_config_no_capability_required(host_no_caps, isolated_db):
    """read_system_config **不需要**声明 capability (只读无副作用)."""
    # 不存在的 key → None, 不抛
    assert host_no_caps.read_system_config("any_key") is None


# ============================================================
# C. 命名空间隔离
# ============================================================


def test_mes_push_rejects_wrong_namespace_event_type(host_full_caps, isolated_db):
    """event_type 不是 plugin_<cc>_ 前缀 → 拒绝 (即使 capability 声明了)."""
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError, match="event_type="):
        host_full_caps.mes_push(event_type="cycle_end", payload={})


def test_mes_push_rejects_other_customer_namespace(host_full_caps, isolated_db):
    """不能用别人的 customer_code 当 event_type 前缀."""
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError):
        host_full_caps.mes_push(event_type="plugin_other_evt", payload={})


def test_write_system_config_rejects_wrong_namespace_key(host_full_caps, isolated_db):
    """key 不是 plugin_<cc>_ 前缀 → 拒绝."""
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError, match="key="):
        host_full_caps.write_system_config(key="license-cache", value="hacked")


def test_write_system_config_rejects_other_customer_namespace(host_full_caps, isolated_db):
    """不能改别人客户的 key."""
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError):
        host_full_caps.write_system_config(key="plugin_other_x", value="1")


def test_customer_code_with_dash_uses_underscore_in_namespace(isolated_db):
    """customer_code = 'acme-prod' → 命名空间前缀 'plugin_acme_prod_'.

    破折号转下划线匹配 ORM 表前缀规则 (TablesRegistry 也是这么转的).
    """
    from backend.plugin_system.registry import PluginHost, PluginRuntimeError
    h = PluginHost(
        customer_code="acme-prod",
        plugin_dir="/tmp",
        main_version="3.13.0",
        capabilities=["runtime.system_config_write"],
    )
    # 正确前缀 (下划线版) 通过
    assert h.write_system_config(key="plugin_acme_prod_x", value="ok") is True
    # 带破折号的不通过
    with pytest.raises(PluginRuntimeError):
        h.write_system_config(key="plugin_acme-prod_x", value="bad")


# ============================================================
# D. 主动 API 实际效果
# ============================================================


def test_trigger_alarm_calls_alarm_router(host_full_caps, isolated_db, monkeypatch):
    """trigger_alarm 实际调到 alarm_router.trigger_alarm()."""
    from backend.api import alarm as alarm_mod
    called = []
    monkeypatch.setattr(
        alarm_mod.alarm_router,
        "trigger_alarm",
        lambda event_type, channel_id=0: called.append((event_type, channel_id)),
    )
    ret = host_full_caps.trigger_alarm(channel_id=2, event_type="event2", reason="NG超时")
    assert ret is True
    assert called == [("event2", 2)]


# ============================================================
# D2. trigger_event (v3.24: 借用主程序事件响应面, 不结算周期)
# ============================================================


class _FakeVSM:
    """伪 VideoSourceManager — 只实现 fire_external_event_response."""

    def __init__(self, ret=True):
        self._ret = ret
        self.calls = []

    def fire_external_event_response(self, event_id, reason, source=None):
        self.calls.append((event_id, reason, source))
        return self._ret


def test_trigger_event_rejects_when_capability_not_declared(host_no_caps, isolated_db):
    """没声明 runtime.event_trigger → 调 trigger_event 抛 PluginRuntimeError."""
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError, match="runtime.event_trigger"):
        host_no_caps.trigger_event(channel_id=0, event_id=2, reason="假擦拭")


def test_trigger_event_calls_fire_external_event_response(host_full_caps, isolated_db, monkeypatch):
    """trigger_event 转发到目标通道 VSM.fire_external_event_response, source 带 customer_code."""
    from backend.api import channel_manager as cm_mod
    fake = _FakeVSM(ret=True)
    monkeypatch.setattr(cm_mod.channel_manager, "channels", {1: fake})
    ret = host_full_caps.trigger_event(channel_id=1, event_id=2, reason="假擦拭")
    assert ret is True
    assert fake.calls == [(2, "假擦拭", "plugin:acme")]


def test_trigger_event_channel_not_registered_returns_false(host_full_caps, isolated_db, monkeypatch):
    """目标通道未注册 → 返 False, audit failed, 不抛."""
    from backend.api import channel_manager as cm_mod
    monkeypatch.setattr(cm_mod.channel_manager, "channels", {})
    ret = host_full_caps.trigger_event(channel_id=3, event_id=2, reason="x")
    assert ret is False

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].action == "trigger_event"
    assert rows[0].status == "failed"
    assert "未注册" in rows[0].message


def test_trigger_event_event_not_found_returns_false(host_full_caps, isolated_db, monkeypatch):
    """事件 id 在项目里不存在 (VSM 返 False) → trigger_event 返 False, audit rejected."""
    from backend.api import channel_manager as cm_mod
    fake = _FakeVSM(ret=False)
    monkeypatch.setattr(cm_mod.channel_manager, "channels", {0: fake})
    ret = host_full_caps.trigger_event(channel_id=0, event_id=999, reason="x")
    assert ret is False

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].action == "trigger_event"
    assert rows[0].status == "rejected"
    assert "matched=False" in rows[0].message


def test_trigger_event_writes_audit_success(host_full_caps, isolated_db, monkeypatch):
    """成功联动 → audit success, message 带 channel_id + event_id."""
    from backend.api import channel_manager as cm_mod
    monkeypatch.setattr(cm_mod.channel_manager, "channels", {2: _FakeVSM(ret=True)})
    host_full_caps.trigger_event(channel_id=2, event_id=5, reason="操作员离开")

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].action == "trigger_event"
    assert rows[0].status == "success"
    assert "channel_id=2" in rows[0].message
    assert "event_id=5" in rows[0].message


def test_trigger_event_swallow_exception_returns_false(host_full_caps, isolated_db, monkeypatch):
    """VSM 内部抛 → trigger_event 返 False, audit failed, 不上抛 (错误隔离)."""
    from backend.api import channel_manager as cm_mod

    class BoomVSM:
        def fire_external_event_response(self, event_id, reason, source=None):
            raise RuntimeError("simulated settlement failure")

    monkeypatch.setattr(cm_mod.channel_manager, "channels", {0: BoomVSM()})
    ret = host_full_caps.trigger_event(channel_id=0, event_id=2, reason="x")
    assert ret is False

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert "simulated settlement failure" in rows[0].message


def test_mes_push_calls_gateway_dispatch(host_full_caps, isolated_db, monkeypatch):
    """mes_push 实际调到 MESGateway.dispatch()."""
    from backend.services import mes_gateway as mg_mod
    captured = []

    class FakeGateway:
        def dispatch(self, event_type, context, channel_id=None):
            captured.append((event_type, context, channel_id))

    monkeypatch.setattr(mg_mod, "get_mes_gateway", lambda: FakeGateway())
    ret = host_full_caps.mes_push(
        event_type="plugin_acme_ng_alert",
        payload={"sn": "SN001", "ng_count": 3},
        channel_id=1,
    )
    assert ret is True
    assert len(captured) == 1
    et, ctx, cid = captured[0]
    assert et == "plugin_acme_ng_alert"
    assert ctx == {"sn": "SN001", "ng_count": 3}
    assert cid == 1


def test_mes_push_rejects_non_dict_payload(host_full_caps, isolated_db):
    """payload 必须是 dict, 其它类型抛 TypeError (不是 PluginRuntimeError, 调用方编程错)."""
    with pytest.raises(TypeError, match="必须是 dict"):
        host_full_caps.mes_push(event_type="plugin_acme_x", payload="not a dict")


def test_write_system_config_upserts_new_row(host_full_caps, isolated_db):
    """新 key 走 insert."""
    ret = host_full_caps.write_system_config(
        key="plugin_acme_ng_counter",
        value="42",
        description="累计 NG 次数",
    )
    assert ret is True

    from backend.models.models import SystemConfig
    db = isolated_db()
    try:
        row = db.query(SystemConfig).filter(SystemConfig.key == "plugin_acme_ng_counter").first()
        assert row is not None
        assert row.value == "42"
        assert row.description == "累计 NG 次数"
    finally:
        db.close()


def test_write_system_config_updates_existing_row(host_full_caps, isolated_db):
    """已存在 key 走 update, 不复建."""
    assert host_full_caps.write_system_config(key="plugin_acme_x", value="1") is True
    assert host_full_caps.write_system_config(key="plugin_acme_x", value="2") is True

    from backend.models.models import SystemConfig
    db = isolated_db()
    try:
        rows = db.query(SystemConfig).filter(SystemConfig.key == "plugin_acme_x").all()
        assert len(rows) == 1
        assert rows[0].value == "2"
    finally:
        db.close()


def test_write_system_config_none_value_falls_back_to_empty_string(host_full_caps, isolated_db):
    """value=None → 落库为 '' (避免 nullable 字段反复变化)."""
    assert host_full_caps.write_system_config(key="plugin_acme_y", value=None) is True
    assert host_full_caps.read_system_config("plugin_acme_y") == ""


def test_read_system_config_returns_value(host_full_caps, isolated_db):
    """读已写入的 key → 返回 value."""
    host_full_caps.write_system_config(key="plugin_acme_z", value="hello")
    assert host_full_caps.read_system_config("plugin_acme_z") == "hello"


def test_read_system_config_returns_none_for_missing(host_full_caps, isolated_db):
    """不存在的 key → None."""
    assert host_full_caps.read_system_config("plugin_acme_nope") is None


def test_read_system_config_can_read_any_key(host_full_caps, isolated_db):
    """read **不**做命名空间隔离, 可读主程序保留 key (license-cache 等).

    设计意图: 只读无副作用, 跨插件查主程序状态是合理需求.
    """
    from backend.models.models import SystemConfig
    db = isolated_db()
    try:
        db.add(SystemConfig(key="license-cache", value="some-cached-data"))
        db.commit()
    finally:
        db.close()
    # 读主程序保留 key 不抛
    assert host_full_caps.read_system_config("license-cache") == "some-cached-data"


# ============================================================
# E. audit log 落库
# ============================================================


def test_trigger_alarm_writes_audit(host_full_caps, isolated_db, monkeypatch):
    """成功调用 trigger_alarm → audit 落 success 行."""
    from backend.api import alarm as alarm_mod
    monkeypatch.setattr(alarm_mod.alarm_router, "trigger_alarm", lambda *a, **kw: None)
    host_full_caps.trigger_alarm(channel_id=0, event_type="event1", reason="OK")

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].customer_code == "acme"
    assert rows[0].action == "trigger_alarm"
    assert rows[0].status == "success"
    assert "channel_id=0" in rows[0].message
    assert "event_type=event1" in rows[0].message


def test_mes_push_writes_audit(host_full_caps, isolated_db, monkeypatch):
    """成功 mes_push → audit 写, payload 只摩要 key (不包含值, 避免敏感)."""
    from backend.services import mes_gateway as mg_mod

    class FakeGateway:
        def dispatch(self, event_type, context, channel_id=None):
            pass

    monkeypatch.setattr(mg_mod, "get_mes_gateway", lambda: FakeGateway())
    host_full_caps.mes_push(
        event_type="plugin_acme_alert",
        payload={"secret_token": "TOPSECRET", "ng_count": 3},
    )

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].action == "mes_push"
    assert rows[0].status == "success"
    # secret_token 这个 key 名进 audit (key 名通常不敏感, value 敏感才需要隐藏)
    # 但 payload 的值 'TOPSECRET' **不**进 audit
    assert "TOPSECRET" not in rows[0].message


def test_write_system_config_writes_audit(host_full_caps, isolated_db):
    """成功 write_system_config → audit 写, value_len 摩要不暴露 value 内容."""
    host_full_caps.write_system_config(key="plugin_acme_secret_token", value="abc123")

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].action == "write_system_config"
    assert rows[0].status == "success"
    assert "abc123" not in rows[0].message  # value 不暴露
    assert "value_len=6" in rows[0].message


def test_capability_rejection_writes_audit(host_no_caps, isolated_db):
    """capability 未声明 → 拒绝时也写 audit rejected 行."""
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError):
        host_no_caps.trigger_alarm(channel_id=0, event_type="event2")

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].action == "capability_check.runtime.alarm_trigger"
    assert rows[0].status == "rejected"


def test_namespace_rejection_writes_audit(host_full_caps, isolated_db):
    """命名空间越权 → audit 落 rejected."""
    from backend.plugin_system.registry import PluginRuntimeError
    with pytest.raises(PluginRuntimeError):
        host_full_caps.write_system_config(key="license-cache", value="x")

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].action == "namespace_check"
    assert rows[0].status == "rejected"
    assert "license-cache" in rows[0].message


def test_read_system_config_does_not_write_audit(host_full_caps, isolated_db):
    """read 高频, **不**写 audit (避免淹没 audit log)."""
    host_full_caps.read_system_config("plugin_acme_x")
    host_full_caps.read_system_config("license-cache")
    rows = _audit_rows(isolated_db)
    assert rows == []


# ============================================================
# F. 错误隔离 (主动 API 内部异常不上抛, 返回 False)
# ============================================================


def test_trigger_alarm_swallow_exception_returns_false(host_full_caps, isolated_db, monkeypatch):
    """alarm_router 内部抛 → trigger_alarm 返 False, audit 落 failed, 不上抛."""
    from backend.api import alarm as alarm_mod

    def boom(*a, **kw):
        raise RuntimeError("simulated serial port failure")

    monkeypatch.setattr(alarm_mod.alarm_router, "trigger_alarm", boom)
    ret = host_full_caps.trigger_alarm(channel_id=0, event_type="event2")
    assert ret is False

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert "simulated serial port failure" in rows[0].message


def test_mes_push_swallow_exception_returns_false(host_full_caps, isolated_db, monkeypatch):
    """Gateway.dispatch 抛 → mes_push 返 False."""
    from backend.services import mes_gateway as mg_mod

    class BadGateway:
        def dispatch(self, event_type, context, channel_id=None):
            raise ConnectionError("MES server unreachable")

    monkeypatch.setattr(mg_mod, "get_mes_gateway", lambda: BadGateway())
    ret = host_full_caps.mes_push(event_type="plugin_acme_x", payload={})
    assert ret is False

    rows = _audit_rows(isolated_db)
    assert len(rows) == 1
    assert rows[0].status == "failed"


# ============================================================
# G. 剩余 stub (NotImplementedError)
# ============================================================
# write_plugin_step_field 已在 v3.13 M3.3 升级为真实现, 见
# tests/plugin_system/test_write_plugin_step_field_M3_3.py.


# v3.13 RFC 10 CG.7: broadcast_to_channel_group / list_channel_groups /
# query_channel_group 已真实现, 不再抛 PluginNotImplementedError.
# 完整测试见 tests/channel_group/test_plugin_host_apis_RFC10_CG7.py.


# ============================================================
# H. 向后兼容
# ============================================================


def test_plugin_host_accepts_no_capabilities_kwarg():
    """老调用方不传 capabilities — PluginHost 仍能创建, capabilities=[].

    确保 G1 期老 manager / test 不破裂.
    """
    from backend.plugin_system.registry import PluginHost
    h = PluginHost(customer_code="acme", plugin_dir="/tmp", main_version="3.13.0")
    assert h.capabilities == []


def test_plugin_host_capabilities_is_copy_not_reference():
    """传入的 capabilities 列表被拷贝, 外部修改不影响 host 内部状态."""
    from backend.plugin_system.registry import PluginHost
    caps = ["runtime.alarm_trigger"]
    h = PluginHost(
        customer_code="acme",
        plugin_dir="/tmp",
        main_version="3.13.0",
        capabilities=caps,
    )
    caps.append("runtime.mes_push")  # 外部加
    assert "runtime.mes_push" not in h.capabilities  # host 不受影响


def test_plugin_host_capabilities_none_defaults_to_empty_list():
    """capabilities=None 也兼容 (退到空列表)."""
    from backend.plugin_system.registry import PluginHost
    h = PluginHost(
        customer_code="acme",
        plugin_dir="/tmp",
        main_version="3.13.0",
        capabilities=None,
    )
    assert h.capabilities == []
