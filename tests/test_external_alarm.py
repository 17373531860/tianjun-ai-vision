"""在途报警台账 + 报警消除入站 单测 (纯逻辑 + 内存 DB)。

覆盖: 登记 / 唯一键去重 / 按四要素消除 / 未匹配回 40007 / 监控页查询。
"""
import time

import pytest

from backend.services.external_alarm import (
    record_active_alarm, clear_alarms, list_active_alarms,
)
from backend.services.mes_inbound import MESInbound


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.db.database import Base
    from backend.models import models as _m  # noqa: F401
    from backend.models import auth_models as _a  # noqa: F401
    from backend.models import mes_models as _mes  # noqa: F401
    from backend.models import export_models as _ex  # noqa: F401
    from backend.models import plugin_models as _p  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _key(**over):
    f = {"task_no": "TASK-1", "product_code": "PROD-X9",
         "step_code": "1.1", "operator": "张三", "warning_text": "装配顺序错误"}
    f.update(over)
    return f


def _cfg(**over):
    cfg = MESInbound._with_defaults({"enabled": True})
    cfg.update(over)
    return cfg


# ==================== 登记 + 去重 ====================
def test_record_then_listed_active(db):
    res = record_active_alarm(db, _key(), event_type="warning", channel_id=0)
    db.commit()
    assert res["recorded"] is True
    active = list_active_alarms(db)
    assert len(active) == 1
    assert active[0]["task_no"] == "TASK-1"
    assert active[0]["status"] == "active"


def test_dedup_same_key_within_window_skips(db):
    record_active_alarm(db, _key(), dedup_sec=5)
    db.commit()
    res2 = record_active_alarm(db, _key(), dedup_sec=5)
    db.commit()
    assert res2["skipped"] is True
    assert len(list_active_alarms(db)) == 1


def test_dedup_off_records_every_time(db):
    record_active_alarm(db, _key(), dedup_sec=0)
    record_active_alarm(db, _key(), dedup_sec=0)
    db.commit()
    assert len(list_active_alarms(db)) == 2


def test_different_key_not_deduped(db):
    record_active_alarm(db, _key(), dedup_sec=99)
    record_active_alarm(db, _key(step_code="2.2"), dedup_sec=99)
    db.commit()
    assert len(list_active_alarms(db)) == 2


# ==================== 去重口径跟随配置的匹配字段 (A8 bug 修复) ====================
def test_dedup_honors_custom_match_fields_collapses(db):
    """匹配字段配成只看 task_no: 仅 step_code 不同的两条应被去重为同一条。

    旧 bug: 去重硬用四要素 → step_code 不同就当两条, 与"只按 task_no 消除"口径不一致。
    """
    mf = ["task_no"]
    record_active_alarm(db, _key(), dedup_sec=99, match_fields=mf)
    record_active_alarm(db, _key(step_code="9.9", operator="李四"),
                        dedup_sec=99, match_fields=mf)
    db.commit()
    assert len(list_active_alarms(db)) == 1  # 同 task_no → 去重


def test_dedup_honors_custom_match_fields_distinguishes(db):
    """匹配字段含 task_no: task_no 不同必须算两条 (不被错误折叠)。"""
    mf = ["task_no", "step_code"]
    record_active_alarm(db, _key(), dedup_sec=99, match_fields=mf)
    record_active_alarm(db, _key(task_no="TASK-2"), dedup_sec=99, match_fields=mf)
    db.commit()
    assert len(list_active_alarms(db)) == 2


def test_dedup_and_clear_use_same_custom_key(db):
    """去重与消除共用同一套匹配字段: 按该字段登记的报警能被同字段消除。"""
    mf = ["task_no", "product_code"]
    record_active_alarm(db, _key(), dedup_sec=0, match_fields=mf)
    db.commit()
    # 只带 task_no+product_code 的消除命令应命中 (口径一致)
    res = clear_alarms(db, {"task_no": "TASK-1", "product_code": "PROD-X9"},
                       match_fields=mf)
    db.commit()
    assert res["matched"] is True and res["cleared"] == 1


def test_dedup_illegal_match_fields_falls_back(db):
    """传入空匹配字段 → 回落四要素, 不报错。"""
    record_active_alarm(db, _key(), dedup_sec=99, match_fields=[])
    res2 = record_active_alarm(db, _key(), dedup_sec=99, match_fields=[])
    db.commit()
    assert res2["skipped"] is True  # 回落四要素后同键去重生效
    assert len(list_active_alarms(db)) == 1


# ==================== 扩展维度 (A8b: JSON extra_data 通用维度) ====================
def test_extra_dim_recorded_and_serialized(db):
    """非原生字段 (如 batch_no/line_id) 应存进 extra_data.ext 并回显。"""
    f = _key(batch_no="B-2026", line_id="L3")
    record_active_alarm(db, f, dedup_sec=0)
    db.commit()
    active = list_active_alarms(db)
    assert len(active) == 1
    assert active[0]["extra"] == {"batch_no": "B-2026", "line_id": "L3"}


def test_extra_dim_dedup_collapses_same(db):
    """匹配键含扩展维度 batch_no: 同 batch_no 在窗口内去重。"""
    mf = ["task_no", "batch_no"]
    record_active_alarm(db, _key(batch_no="B1"), dedup_sec=99, match_fields=mf)
    res2 = record_active_alarm(db, _key(batch_no="B1", operator="李四"),
                               dedup_sec=99, match_fields=mf)
    db.commit()
    assert res2["skipped"] is True
    assert len(list_active_alarms(db)) == 1


def test_extra_dim_dedup_distinguishes(db):
    """匹配键含扩展维度 batch_no: batch_no 不同算两条。"""
    mf = ["task_no", "batch_no"]
    record_active_alarm(db, _key(batch_no="B1"), dedup_sec=99, match_fields=mf)
    record_active_alarm(db, _key(batch_no="B2"), dedup_sec=99, match_fields=mf)
    db.commit()
    assert len(list_active_alarms(db)) == 2


def test_extra_dim_clear_matches(db):
    """按含扩展维度的唯一键消除: 同 batch_no 的在途报警能被消除。"""
    mf = ["task_no", "batch_no"]
    record_active_alarm(db, _key(batch_no="B1"), dedup_sec=0, match_fields=mf)
    record_active_alarm(db, _key(batch_no="B2"), dedup_sec=0, match_fields=mf)
    db.commit()
    res = clear_alarms(db, {"task_no": "TASK-1", "batch_no": "B1"}, match_fields=mf)
    db.commit()
    assert res["matched"] is True and res["cleared"] == 1
    remaining = list_active_alarms(db)
    assert len(remaining) == 1
    assert remaining[0]["extra"]["batch_no"] == "B2"  # 只清了 B1


def test_extra_dim_illegal_key_ignored(db):
    """非法维度键 (含点/引号) 不进 extra_data, 防 json 路径注入。"""
    record_active_alarm(db, _key(**{"bad.key": "x", "ok_key": "y"}), dedup_sec=0)
    db.commit()
    active = list_active_alarms(db)
    assert active[0]["extra"] == {"ok_key": "y"}  # bad.key 被丢弃


# ==================== 消除 ====================
def test_clear_by_four_keys(db):
    record_active_alarm(db, _key())
    db.commit()
    res = clear_alarms(db, _key())
    db.commit()
    assert res["matched"] is True
    assert res["cleared"] == 1
    assert list_active_alarms(db) == []


def test_clear_not_found_returns_unmatched(db):
    record_active_alarm(db, _key())
    db.commit()
    res = clear_alarms(db, _key(operator="李四"))
    db.commit()
    assert res["matched"] is False
    assert res["cleared"] == 0
    assert len(list_active_alarms(db)) == 1  # 原报警仍在


def test_clear_empty_match_does_not_wipe_all(db):
    record_active_alarm(db, _key())
    db.commit()
    res = clear_alarms(db, {})  # 全空匹配键 → 拒绝, 不误清
    db.commit()
    assert res["matched"] is False
    assert len(list_active_alarms(db)) == 1


# ==================== handle_alarm_clear 入站编排 ====================
def test_handle_alarm_clear_success(db):
    record_active_alarm(db, _key())
    db.commit()
    svc = MESInbound()
    body = {"TaskNo": "TASK-1", "ProductCode": "PROD-X9",
            "StepCode": "1.1", "Operator": "张三"}
    res = svc.handle_alarm_clear(db, body, _cfg())
    db.commit()
    assert res["ok"] is True
    assert res["response"]["code"] == 0


def test_handle_alarm_clear_not_found_40007(db):
    svc = MESInbound()
    body = {"TaskNo": "NOPE", "ProductCode": "X", "StepCode": "9", "Operator": "无"}
    res = svc.handle_alarm_clear(db, body, _cfg())
    db.commit()
    assert res["ok"] is False
    assert res["code_key"] == "alarm_not_found"
    assert res["response"]["code"] == 40007


def test_handle_alarm_clear_disabled(db):
    svc = MESInbound()
    res = svc.handle_alarm_clear(db, {"TaskNo": "T"}, _cfg(enabled=False))
    assert res["code_key"] == "disabled"


# ==================== 网关台账登记钩子 (gating) ====================
def _save_alarm_cfg(db, **over):
    from backend.services.mes_inbound import get_mes_inbound
    cfg = {"enabled": True, "alarm_event_name": "cycle_end", "alarm_dedup_sec": 0}
    cfg.update(over)
    get_mes_inbound().save_config(db, cfg)
    db.commit()


def _ctx(result="NG", reason="装配顺序错误"):
    return {
        "cycle": {"result": result, "ng_reason": reason},
        "order": {
            "order_no": "TASK-1", "product_code": "PROD-X9",
            "extra_data": {"inbound": {"step_code": "1.1", "operator": "张三"}},
        },
    }


def test_hook_records_on_ng_cycle_end(db):
    from backend.services.mes_gateway import get_mes_gateway
    _save_alarm_cfg(db)
    get_mes_gateway()._record_active_alarm_if_alarm(db, "cycle_end", _ctx("NG"), 0)
    db.commit()
    active = list_active_alarms(db)
    assert len(active) == 1
    assert active[0]["task_no"] == "TASK-1"
    assert active[0]["step_code"] == "1.1"
    assert active[0]["operator"] == "张三"
    assert active[0]["warning_text"] == "装配顺序错误"


def test_hook_skips_ok_result(db):
    from backend.services.mes_gateway import get_mes_gateway
    _save_alarm_cfg(db)
    get_mes_gateway()._record_active_alarm_if_alarm(db, "cycle_end", _ctx("OK"), 0)
    db.commit()
    assert list_active_alarms(db) == []


def test_hook_skips_unconfigured_event(db):
    from backend.services.mes_gateway import get_mes_gateway
    _save_alarm_cfg(db, alarm_event_name="cycle_end")
    get_mes_gateway()._record_active_alarm_if_alarm(db, "session_end", _ctx("NG"), 0)
    db.commit()
    assert list_active_alarms(db) == []


def test_hook_multi_event_names_list(db):
    from backend.services.mes_gateway import get_mes_gateway
    _save_alarm_cfg(db, alarm_event_name=["cycle_end", "box_timeout"])
    gw = get_mes_gateway()
    gw._record_active_alarm_if_alarm(db, "box_timeout", _ctx("NG", "超时"), 0)
    db.commit()
    assert len(list_active_alarms(db)) == 1


def test_hook_inert_when_no_alarm_event(db):
    from backend.services.mes_gateway import get_mes_gateway
    _save_alarm_cfg(db, alarm_event_name="")
    get_mes_gateway()._record_active_alarm_if_alarm(db, "cycle_end", _ctx("NG"), 0)
    db.commit()
    assert list_active_alarms(db) == []


# ==================== 报警推送去重 (出站节流) ====================
def test_alarm_push_dedup_skips_within_window(db):
    from backend.services.mes_gateway import get_mes_gateway
    _save_alarm_cfg(db, alarm_dedup_sec=99)
    gw = get_mes_gateway()
    gw._record_active_alarm_if_alarm(db, "cycle_end", _ctx("NG"), 0)
    db.commit()
    # 同唯一键报警仍在途 → 窗口内应跳过整次推送
    assert gw._alarm_dedup_should_skip(db, "cycle_end", _ctx("NG")) is True


def test_alarm_push_dedup_off_never_skips(db):
    from backend.services.mes_gateway import get_mes_gateway
    _save_alarm_cfg(db, alarm_dedup_sec=0)
    gw = get_mes_gateway()
    gw._record_active_alarm_if_alarm(db, "cycle_end", _ctx("NG"), 0)
    db.commit()
    assert gw._alarm_dedup_should_skip(db, "cycle_end", _ctx("NG")) is False


def test_alarm_push_dedup_different_key_no_skip(db):
    from backend.services.mes_gateway import get_mes_gateway
    _save_alarm_cfg(db, alarm_dedup_sec=99)
    gw = get_mes_gateway()
    gw._record_active_alarm_if_alarm(db, "cycle_end", _ctx("NG"), 0)
    db.commit()
    other = _ctx("NG")
    other["order"]["order_no"] = "TASK-2"
    assert gw._alarm_dedup_should_skip(db, "cycle_end", other) is False


def test_alarm_push_dedup_non_alarm_event_no_skip(db):
    from backend.services.mes_gateway import get_mes_gateway
    _save_alarm_cfg(db, alarm_dedup_sec=99)
    gw = get_mes_gateway()
    gw._record_active_alarm_if_alarm(db, "cycle_end", _ctx("NG"), 0)
    db.commit()
    # session_end 未被配成报警事件 → 不当报警, 不去重
    assert gw._alarm_dedup_should_skip(db, "session_end", _ctx("NG")) is False


# ==================== 健康检查响应格式 (川南约定 {code:0,message:ok}) ====================
def test_health_response_shape():
    cfg = _cfg()
    resp = MESInbound.build_response(cfg, "success", "ok")
    assert resp == {"code": 0, "message": "ok"}


# ==================== 产品码 → 项目 按名自动匹配 ====================
def _mk_project(db, name, active=False):
    from backend.models.models import Project
    p = Project(name=name, is_active=active)
    db.add(p)
    db.commit()
    return p


def test_switch_project_by_name_match(db):
    # 项目命名 == 产品代号 + 已激活 → 按名匹配命中 (早返回, 不触发重载)
    _mk_project(db, "PROD-X9", active=True)
    svc = MESInbound()
    cfg = _cfg(switch_project_on_task=True, product_project_map={})
    ok, key, msg = svc._switch_project(db, {"product_code": "PROD-X9"}, cfg)
    assert ok is True
    assert "已激活" in msg


def test_switch_project_name_no_match_returns_message(db):
    svc = MESInbound()
    cfg = _cfg(switch_project_on_task=True, product_project_map={})
    ok, key, msg = svc._switch_project(db, {"product_code": "PROD-NONE"}, cfg)
    assert ok is False
    assert key == "unknown_product"
    assert msg == "未查询到当前产品代号检测模型"


def test_switch_project_name_match_disabled(db):
    # 关掉按名匹配 → 即使有同名项目也不认 (仅认 product_project_map)
    _mk_project(db, "PROD-X9", active=True)
    svc = MESInbound()
    cfg = _cfg(switch_project_on_task=True, product_project_map={},
               match_project_by_name=False)
    ok, key, msg = svc._switch_project(db, {"product_code": "PROD-X9"}, cfg)
    assert ok is False
    assert key == "unknown_product"


# ==================== 无匹配产品话术对齐 ====================
def test_unknown_product_message_aligned():
    svc = MESInbound()
    cfg = _cfg(switch_project_on_task=True, product_project_map={},
               match_project_by_name=False)
    ok, key, msg = svc._switch_project(
        None, {"product_code": "PROD-UNKNOWN"}, cfg)
    assert ok is False
    assert key == "unknown_product"
    assert msg == "未查询到当前产品代号检测模型"
