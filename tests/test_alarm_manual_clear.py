"""v3.39 川南反馈: 在途报警软件内消除出口 + 开工自动开始检测原因回带。

覆盖:
  1. 服务层 clear_all_active_alarms: 只清 active、工位过滤、留 clear_source
  2. 端点 /mes/inbound/active-alarms/clear-manual: 配置默认关 → 403; 开启 → 全清
  3. 清零联动: alarm_banner.clear_on_counter_reset 开启时 reset-stats 顺带清报警
  4. 开工自动开始检测: 没拉起时原因 (视频源未运行 / 模型未就绪) 带回响应消息
"""
from unittest.mock import MagicMock

import pytest

from backend.db.database import SessionLocal
from backend.models.mes_models import ExternalActiveAlarm
from backend.services import external_alarm
from backend.services.mes_inbound import MESInbound, get_mes_inbound


PREFIX = "AMCT-"  # 本文件专属任务号前缀, 前后清扫不碰别组数据


@pytest.fixture(autouse=True)
def _wipe():
    def _do():
        db = SessionLocal()
        try:
            db.query(ExternalActiveAlarm).filter(
                ExternalActiveAlarm.task_no.like(f"{PREFIX}%")).delete(
                synchronize_session=False)
            db.commit()
        finally:
            db.close()
    _do()
    yield
    _do()


def _seed_alarm(task_no, channel_id=0):
    db = SessionLocal()
    try:
        external_alarm.record_active_alarm(
            db, {"task_no": task_no, "product_code": "P1",
                 "step_code": "S1", "operator": "op",
                 "warning_text": "缺件"},
            event_type="ng", channel_id=channel_id)
        db.commit()
    finally:
        db.close()


def _active_count(prefix=PREFIX):
    db = SessionLocal()
    try:
        return (db.query(ExternalActiveAlarm)
                .filter(ExternalActiveAlarm.task_no.like(f"{prefix}%"),
                        ExternalActiveAlarm.status == "active").count())
    finally:
        db.close()


def _set_banner_cfg(**banner_over):
    svc = get_mes_inbound()
    db = SessionLocal()
    try:
        svc.save_config(db, {"alarm_banner": banner_over})
        db.commit()
    finally:
        db.close()


@pytest.fixture
def _reset_banner_cfg():
    yield
    _set_banner_cfg(allow_manual_clear=False, clear_on_counter_reset=False)


# ============================================================
# 1. 服务层
# ============================================================
def test_clear_all_only_touches_active_rows():
    _seed_alarm(f"{PREFIX}T1")
    _seed_alarm(f"{PREFIX}T2")
    db = SessionLocal()
    try:
        # 预先消除一条, 全清不该重复计数
        external_alarm.clear_alarms(db, {"task_no": f"{PREFIX}T1"},
                                    match_fields=["task_no"])
        db.commit()
        res = external_alarm.clear_all_active_alarms(db, clear_source="manual_ui")
        db.commit()
        assert res["cleared"] == 1
        row = (db.query(ExternalActiveAlarm)
               .filter(ExternalActiveAlarm.task_no == f"{PREFIX}T2").first())
        assert row.status == "cleared"
        assert row.clear_source == "manual_ui"
        assert row.cleared_at is not None
    finally:
        db.close()


def test_clear_all_channel_filter():
    _seed_alarm(f"{PREFIX}C0", channel_id=0)
    _seed_alarm(f"{PREFIX}C1", channel_id=1)
    db = SessionLocal()
    try:
        res = external_alarm.clear_all_active_alarms(db, channel_id=1)
        db.commit()
        assert res["cleared"] == 1
    finally:
        db.close()
    assert _active_count() == 1  # ch0 的还在


# ============================================================
# 2. 手动消除端点 (配置闸门)
# ============================================================
def test_manual_clear_endpoint_forbidden_by_default(client, _reset_banner_cfg):
    _set_banner_cfg(allow_manual_clear=False)
    _seed_alarm(f"{PREFIX}E1")
    r = client.post("/api/v1/mes/inbound/active-alarms/clear-manual")
    assert r.status_code == 403
    assert _active_count() == 1  # 一条都没动


def test_manual_clear_endpoint_clears_when_enabled(client, _reset_banner_cfg):
    _set_banner_cfg(allow_manual_clear=True)
    _seed_alarm(f"{PREFIX}E2")
    _seed_alarm(f"{PREFIX}E3")
    r = client.post("/api/v1/mes/inbound/active-alarms/clear-manual")
    assert r.status_code == 200
    assert r.json()["cleared"] == 2
    assert _active_count() == 0


# ============================================================
# 3. 清零联动
# ============================================================
def test_reset_stats_clears_alarms_when_linked(client, _reset_banner_cfg):
    _set_banner_cfg(clear_on_counter_reset=True)
    _seed_alarm(f"{PREFIX}R1")
    r = client.post("/api/v1/source/detection/reset-stats", params={"channel": 0})
    assert r.status_code == 200
    assert "在途报警已消除" in r.json()["message"]
    assert _active_count() == 0


def test_reset_stats_leaves_alarms_by_default(client, _reset_banner_cfg):
    _set_banner_cfg(clear_on_counter_reset=False)
    _seed_alarm(f"{PREFIX}R2")
    r = client.post("/api/v1/source/detection/reset-stats", params={"channel": 0})
    assert r.status_code == 200
    assert _active_count() == 1


# ============================================================
# 4. 开工自动开始检测: 原因回带
# ============================================================
def _fake_cm(monkeypatch, mgr):
    fake_cm = MagicMock()
    fake_cm.channels = {0: mgr}
    monkeypatch.setattr("backend.api.channel_manager.channel_manager", fake_cm)
    return fake_cm


def test_auto_start_reports_source_not_configured(monkeypatch):
    """本次启动从没配置过视频源 → 无从拉起, 原因回带。"""
    mgr = MagicMock(is_running=False)
    mgr.source_type = None
    _fake_cm(monkeypatch, mgr)
    started, skipped = MESInbound()._auto_start_detection()
    assert started == []
    assert skipped == ["ch0: 未配置视频源"]
    mgr.start_detection.assert_not_called()


def test_auto_start_revives_paused_source(monkeypatch):
    """川南事故链 (2026-07-15 日志): 点过"停止"后源暂停, 开工必须能借
    start_detection 的复活路径重新拉起, 而不是因"源未运行"放弃。"""
    mgr = MagicMock(is_running=False, is_detecting=False, source_type="hikvision")
    mgr.model = object()
    _fake_cm(monkeypatch, mgr)
    started, skipped = MESInbound()._auto_start_detection()
    assert started == ["ch0"]
    assert skipped == []
    mgr.start_detection.assert_called_once()


def test_auto_start_reports_model_not_ready(monkeypatch):
    mgr = MagicMock(is_running=True, is_detecting=False, source_type="hikvision")
    mgr.model = None
    _fake_cm(monkeypatch, mgr)
    started, skipped = MESInbound()._auto_start_detection()
    assert started == []
    assert len(skipped) == 1 and "模型未就绪" in skipped[0]
    mgr.start_detection.assert_not_called()


def test_auto_start_reason_lands_in_task_response(monkeypatch):
    """开工响应消息里能直接看到没拉起的原因 (客户不开调试日志即可自诊)。"""
    mgr = MagicMock(is_running=False)
    mgr.source_type = None  # 未配置视频源 → 唯一无从拉起的跳过分支
    _fake_cm(monkeypatch, mgr)
    cfg = MESInbound._with_defaults({
        "enabled": True, "start_detection_on_task": True,
        "switch_project_on_task": False, "create_work_order_on_task": False,
    })
    svc = MESInbound()
    db = SessionLocal()
    try:
        res = svc.handle_task_start(
            db, {"TaskNo": f"{PREFIX}MSG", "ProductCode": "P1"}, cfg)
        db.commit()
    finally:
        db.close()
    msg = (res.get("response") or {}).get("message", "")
    assert "自动开始检测未执行" in msg
    assert "未配置视频源" in msg
