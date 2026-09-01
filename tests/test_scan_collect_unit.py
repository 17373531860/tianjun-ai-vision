# -*- coding: utf-8 -*-
"""v3.56 周期多码采集（scan collect）单元/接口测试。

覆盖:
  引擎: 正则分类 / 顺序填坑 / 收尾码结算 OK / 少扫 NG / 组内重复 ng_alarm /
        超量 reject / 跨工件历史去重 / 纠错删码 / 清空重扫 / get_state 视图 /
        通道裁撤清理 / all_filled 结算模式
  接口: /scan-collect/config PUT-GET 往返 + 校验(收尾码必配/坏正则/重复key) /
        /scan-collect/records 追溯查询
  路由: mes_hooks._handle_scan 优先路径守门 (handles False 时不拦截)
"""
import uuid

import pytest


# 标准 9 码规格: 6 芯子 + 1 母排 + 1 盖板 + 1 工件码(收尾, 字母开头+数字)
def _cfg_9(**overrides):
    cfg = {
        "slots": [
            {"key": "busbar", "label": "母排码", "count": 1, "regex": "", "role": ""},
            {"key": "cover", "label": "盖板码", "count": 1, "regex": "", "role": ""},
            {"key": "chip", "label": "芯子码", "count": 6, "regex": "", "role": ""},
            {"key": "wp", "label": "工件码", "count": 1,
             "regex": "^[A-Za-z]+[0-9]+$", "role": "closing"},
        ],
        "sequence_fallback": True,
        "dedup_in_group": "ng_alarm",
        "dedup_cross_group": "off",
        "settle_on": "closing",
        "on_overflow": "reject",
        "on_unmatched": "reject",
        "timeout_sec": 0,
        "event_ok_id": 1,
        "event_ng_id": 2,
    }
    cfg.update(overrides)
    return cfg


@pytest.fixture
def project_id(app):
    """建一个真实项目行（工件注册 project_id 指它）"""
    from backend.db.database import SessionLocal
    from backend.models.models import Project
    db = SessionLocal()
    try:
        p = Project(name=f"sc-test-{uuid.uuid4().hex[:8]}", task_type="detection")
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


@pytest.fixture
def engine(app, project_id, monkeypatch):
    """独立引擎实例：配置直灌缓存, 事件/警告打桩记录"""
    from backend.services.scan_collect import ScanCollectEngine
    eng = ScanCollectEngine()
    eng._cfg_cache[project_id] = {"enabled": True, "config": _cfg_9()}
    eng.fired_events = []
    eng.warns = []
    monkeypatch.setattr(
        eng, "_fire_event",
        lambda ch, event_id, reason: eng.fired_events.append(
            {"ch": ch, "event_id": event_id, "reason": reason}) or True)
    monkeypatch.setattr(
        eng, "_warn",
        lambda ch, code, reason: eng.warns.append(
            {"ch": ch, "code": code, "reason": reason}))
    monkeypatch.setattr(eng, "_dispatch_export", lambda *a, **k: None)
    return eng


@pytest.fixture
def db(app):
    from backend.db.database import SessionLocal
    s = SessionLocal()
    yield s
    s.close()


def _scan(eng, db, pid, code, ch=0):
    return eng.on_scan(db, ch, code, code, pid)


def _scan_full_ok(eng, db, pid, ch=0, wp_code="A123456"):
    """扫满一组 9 码并收尾"""
    for c in ["BUS-001", "COV-001"] + [f"CHIP-{i:03d}" for i in range(6)]:
        r = _scan(eng, db, pid, c, ch)
        assert r["accepted"], r
    return _scan(eng, db, pid, wp_code, ch)


# ==================== 引擎: 分类 + 结算 ====================

def test_ok_settle_full_group(engine, db, project_id):
    r = _scan_full_ok(engine, db, project_id)
    assert r["accepted"]
    # 结算后组清空, 上一件摘要保留
    assert 0 not in engine._groups
    last = engine._last_settled[0]
    assert last["result"] == "ok" and last["is_good"] is True
    assert last["workpiece_sn"] == "A123456"
    assert last["total"] == 9
    # OK 事件触发
    assert engine.fired_events[-1]["event_id"] == 1
    # 工件已注册且状态 ok
    from backend.models.mes_models import Workpiece
    wp = db.query(Workpiece).filter(Workpiece.serial_no == "A123456",
                                    Workpiece.project_id == project_id).first()
    assert wp is not None and wp.status == "ok"
    # 逐码记录回填
    from backend.models.scan_collect_models import ScanCollectRecord
    recs = (db.query(ScanCollectRecord)
            .filter(ScanCollectRecord.workpiece_id == wp.id).all())
    assert len(recs) == 9
    assert all(x.group_result == "ok" for x in recs)


def test_sequence_fallback_fills_in_order(engine, db, project_id):
    """无正则的码按槽位顺序填坑: 第1个进母排, 第2个进盖板, 后6个进芯子"""
    codes = ["X-1", "X-2", "X-3", "X-4", "X-5", "X-6", "X-7", "X-8"]
    for c in codes:
        assert _scan(engine, db, project_id, c)["accepted"]
    st = engine.get_state(db, 0, project_id)
    by_key = {s["key"]: s for s in st["slots"]}
    assert by_key["busbar"]["got"] == 1
    assert by_key["cover"]["got"] == 1
    assert by_key["chip"]["got"] == 6
    assert by_key["wp"]["got"] == 0  # 有正则的槽位不吃非匹配码


def test_ng_missing_on_early_closing(engine, db, project_id):
    """只扫 3 个芯子就扫工件码收尾 → ng_missing + 缺扫明细"""
    for c in ["BUS-001", "CHIP-A", "CHIP-B", "CHIP-C"]:
        _scan(engine, db, project_id, c)
    r = _scan(engine, db, project_id, "B99999")
    assert r["accepted"]
    last = engine._last_settled[0]
    assert last["result"] == "ng_missing" and last["is_good"] is False
    # 顺序填坑: BUS-001→母排, CHIP-A→盖板(无正则槽位按顺序吃码), B/C→芯子
    missing = {m["slot_key"]: m for m in last["missing"]}
    assert missing["chip"]["got"] == 2 and missing["chip"]["expected"] == 6
    assert "cover" not in missing
    assert engine.fired_events[-1]["event_id"] == 2
    # 工件登记为 ng
    from backend.models.mes_models import Workpiece
    wp = db.query(Workpiece).filter(Workpiece.serial_no == "B99999",
                                    Workpiece.project_id == project_id).first()
    assert wp is not None and wp.status == "ng"


def test_dup_in_group_ng_alarm(engine, db, project_id):
    _scan(engine, db, project_id, "BUS-001")
    r = _scan(engine, db, project_id, "BUS-001")
    assert not r["accepted"]
    assert "重复" in r["msg"]
    # ng_alarm 策略: 借 NG 事件报警 + 警告 toast, 但组不结算
    assert engine.fired_events[-1]["event_id"] == 2
    assert engine.warns
    assert 0 in engine._groups  # 组还在, 可以继续扫


def test_overflow_reject(engine, db, project_id):
    _scan(engine, db, project_id, "BUS-001")
    _scan(engine, db, project_id, "COV-001")
    for i in range(6):
        _scan(engine, db, project_id, f"CHIP-{i}")
    n_events = len(engine.fired_events)
    r = _scan(engine, db, project_id, "EXTRA-1")  # 第 9 个非收尾码: 全部无正则槽位已满
    assert not r["accepted"]
    assert len(engine.fired_events) == n_events  # reject 策略不报 NG
    assert engine.warns[-1]["code"] == "EXTRA-1"


def test_cross_group_dedup(engine, db, project_id):
    engine._cfg_cache[project_id]["config"]["dedup_cross_group"] = "ng_alarm"
    _scan_full_ok(engine, db, project_id, wp_code="A111111")
    # 新一组里重扫上一件用过的芯子码
    r = _scan(engine, db, project_id, "CHIP-000")
    assert not r["accepted"]
    assert "历史工件" in r["msg"]


def test_all_filled_settle_mode(engine, db, project_id):
    """settle_on=all_filled: 不需要收尾码角色, 全部槽位满即 OK"""
    cfg = _cfg_9(settle_on="all_filled")
    cfg["slots"] = [
        {"key": "chip", "label": "芯子码", "count": 2, "regex": "", "role": ""},
        {"key": "wp", "label": "工件码", "count": 1,
         "regex": "^[A-Za-z]+[0-9]+$", "role": "closing"},
    ]
    engine._cfg_cache[project_id] = {"enabled": True, "config": cfg}
    _scan(engine, db, project_id, "C-1")
    _scan(engine, db, project_id, "C-2")
    assert 0 in engine._groups
    _scan(engine, db, project_id, "A123")
    assert 0 not in engine._groups
    assert engine._last_settled[0]["result"] == "ok"


# ==================== 引擎: 纠错 + 生命周期 ====================

def test_remove_code_correction(engine, db, project_id):
    _scan(engine, db, project_id, "BUS-001")
    _scan(engine, db, project_id, "COV-001")
    st = engine.get_state(db, 0, project_id)
    rec_id = next(s for s in st["slots"] if s["key"] == "busbar")["codes"][0]["record_id"]
    ok, msg = engine.remove_code(db, 0, rec_id)
    assert ok, msg
    st2 = engine.get_state(db, 0, project_id)
    assert next(s for s in st2["slots"] if s["key"] == "busbar")["got"] == 0
    # DB 留痕 deleted
    from backend.models.scan_collect_models import ScanCollectRecord
    rec = db.query(ScanCollectRecord).get(rec_id)
    assert rec.status == "deleted"
    # 删掉后可重扫同码
    assert _scan(engine, db, project_id, "BUS-001")["accepted"]


def test_clear_group(engine, db, project_id):
    _scan(engine, db, project_id, "BUS-001")
    ok, msg = engine.clear_group(db, 0)
    assert ok
    assert 0 not in engine._groups
    ok2, _ = engine.clear_group(db, 0)
    assert not ok2  # 没组可清


def test_channel_removed_cleanup(engine, db, project_id):
    _scan(engine, db, project_id, "BUS-001")
    engine.on_channel_removed(0)
    assert 0 not in engine._groups
    assert 0 not in engine._last_settled


def test_get_state_disabled_returns_none(engine, db):
    assert engine.get_state(db, 0, 987654321) is None


def test_channel_isolation(engine, db, project_id):
    _scan(engine, db, project_id, "BUS-001", ch=0)
    _scan(engine, db, project_id, "BUS-777", ch=1)
    st0 = engine.get_state(db, 0, project_id)
    st1 = engine.get_state(db, 1, project_id)
    assert st0["total_got"] == 1 and st1["total_got"] == 1
    codes0 = [c["code"] for s in st0["slots"] for c in s["codes"]]
    assert codes0 == ["BUS-001"]


# ==================== 接口 ====================

def test_config_put_get_roundtrip(client, project_id):
    payload = {
        "enabled": True,
        "slots": _cfg_9()["slots"],
        "sequence_fallback": True,
        "dedup_in_group": "ng_alarm",
        "dedup_cross_group": "off",
        "settle_on": "closing",
        "on_overflow": "reject",
        "on_unmatched": "reject",
        "timeout_sec": 0,
        "event_ok_id": 1,
        "event_ng_id": 2,
    }
    r = client.put(f"/api/v1/scan-collect/config?project_id={project_id}",
                   json=payload)
    assert r.status_code == 200, r.text
    g = client.get(f"/api/v1/scan-collect/config?project_id={project_id}").json()
    assert g["enabled"] is True
    assert len(g["slots"]) == 4
    assert g["slots"][3]["role"] == "closing"


def test_config_validation(client, project_id):
    base = {"enabled": True, "settle_on": "closing", "slots": []}
    # 启用但无槽位
    assert client.put(f"/api/v1/scan-collect/config?project_id={project_id}",
                      json=base).status_code == 400
    # closing 模式没有收尾码
    base["slots"] = [{"key": "a", "label": "A", "count": 1, "regex": "", "role": ""}]
    assert client.put(f"/api/v1/scan-collect/config?project_id={project_id}",
                      json=base).status_code == 400
    # 坏正则
    base["slots"] = [
        {"key": "a", "label": "A", "count": 1, "regex": "([", "role": "closing"}]
    assert client.put(f"/api/v1/scan-collect/config?project_id={project_id}",
                      json=base).status_code == 400
    # 重复 key
    base["slots"] = [
        {"key": "a", "label": "A", "count": 1, "regex": "", "role": "closing"},
        {"key": "a", "label": "B", "count": 1, "regex": "", "role": ""}]
    assert client.put(f"/api/v1/scan-collect/config?project_id={project_id}",
                      json=base).status_code == 400
    # 项目不存在
    ok_payload = {"enabled": False, "slots": []}
    assert client.put("/api/v1/scan-collect/config?project_id=987654321",
                      json=ok_payload).status_code == 404


def test_records_query(client, engine, db, project_id):
    _scan_full_ok(engine, db, project_id, wp_code="Z777777")
    from backend.models.mes_models import Workpiece
    wp = db.query(Workpiece).filter(Workpiece.serial_no == "Z777777",
                                    Workpiece.project_id == project_id).first()
    r = client.get(f"/api/v1/scan-collect/records?workpiece_id={wp.id}")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 9
    assert rows[-1]["code"] == "Z777777"
    assert all(x["group_result"] == "ok" for x in rows)
    # 无参数 400
    assert client.get("/api/v1/scan-collect/records").status_code == 400


# ==================== mes_hooks 路由守门 ====================

def test_handles_gate(engine, db, project_id):
    """未启用项目 handles=False（_handle_scan 不拦截, 走原单码路径）"""
    assert engine.handles(db, 0, project_id) is True
    assert engine.handles(db, 0, 987654321) is False
    assert engine.handles(db, 0, None) is False


def test_switch_project_voids_stale_group(engine, db, project_id, app):
    """通道上残留其他项目的组 → 切项目后新扫码作废旧组重开"""
    from backend.db.database import SessionLocal
    from backend.models.models import Project
    s = SessionLocal()
    try:
        p2 = Project(name=f"sc-test2-{uuid.uuid4().hex[:8]}", task_type="detection")
        s.add(p2)
        s.commit()
        s.refresh(p2)
        pid2 = p2.id
    finally:
        s.close()
    engine._cfg_cache[pid2] = {"enabled": True, "config": _cfg_9()}

    _scan(engine, db, project_id, "BUS-001")
    old_group = engine._groups[0].group_id
    r = _scan(engine, db, pid2, "BUS-002")
    assert r["accepted"]
    assert engine._groups[0].group_id != old_group
    assert engine._groups[0].project_id == pid2


# ==================== v3.56.1 现场确认单增补特性 ====================
# 三类码现场范式: 母排(M前缀33位)×1 + 芯子(13位数字)×6 + 工装(H-C前缀, 收尾, 治具循环用)

def _cfg_field(**overrides):
    """现场三类码规格 (芯子 count=2 提速)"""
    cfg = _cfg_9()
    cfg["slots"] = [
        {"key": "busbar", "label": "母排码", "count": 1,
         "regex": "^M.{29,32}$", "role": "", "on_overflow": "reject"},
        {"key": "chip", "label": "芯子码", "count": 2,
         "regex": r"^\d{13}$", "role": "", "on_overflow": "ng_alarm"},
        {"key": "fixture", "label": "工装码", "count": 1,
         "regex": "^H-C", "role": "closing", "dedup_cross_group": "off"},
    ]
    cfg["dedup_cross_group"] = "reject"
    cfg.update(overrides)
    return cfg


BUS1 = "M" + "0" * 31
BUS2 = "M" + "1" * 31
CHIPS = ["9260000144908", "9260000145631", "9260000146072", "9260000146312"]
FIX1 = "H-C035-527-5"


@pytest.fixture
def field_engine(app, project_id, monkeypatch):
    from backend.services.scan_collect import ScanCollectEngine
    eng = ScanCollectEngine()
    eng._cfg_cache[project_id] = {"enabled": True, "config": _cfg_field()}
    eng.fired_events = []
    eng.warns = []
    monkeypatch.setattr(
        eng, "_fire_event",
        lambda ch, event_id, reason: eng.fired_events.append(
            {"ch": ch, "event_id": event_id, "reason": reason}) or True)
    monkeypatch.setattr(
        eng, "_warn",
        lambda ch, code, reason: eng.warns.append(
            {"ch": ch, "code": code, "reason": reason}))
    monkeypatch.setattr(eng, "_dispatch_export", lambda *a, **k: None)
    return eng


def _set_cfg(eng, pid, cfg):
    eng._cfg_cache[pid] = {"enabled": True, "config": cfg}


def test_slot_cross_dedup_exempt_fixture(field_engine, db, project_id):
    """工装码(治具)跨工件重复被槽位豁免; 芯子跨工件重复照全局 reject 拦"""
    eng = field_engine
    # 第一件: 全扫齐 + 工装收尾
    for c in [BUS1, CHIPS[0], CHIPS[1]]:
        assert _scan(eng, db, project_id, c)["accepted"]
    assert _scan(eng, db, project_id, FIX1)["accepted"]
    assert eng._last_settled[0]["result"] == "ok"
    # 第二件: 芯子重复历史码 → 拦 (全局 reject)
    assert _scan(eng, db, project_id, BUS2)["accepted"]
    r = _scan(eng, db, project_id, CHIPS[0])
    assert not r["accepted"] and "历史工件" in r["msg"]
    # 同一个工装码再次收尾 → 豁免放行, 组正常结算 (缺芯子判 ng_missing 不影响豁免验证)
    assert _scan(eng, db, project_id, CHIPS[2])["accepted"]
    assert _scan(eng, db, project_id, CHIPS[3])["accepted"]
    r = _scan(eng, db, project_id, FIX1)
    assert r["accepted"], r
    assert eng._last_settled[0]["result"] == "ok"


def test_slot_overflow_policy_override(field_engine, db, project_id):
    """芯子槽 on_overflow=ng_alarm → 多扫芯子响 NG 铃; 母排槽 reject → 只拦提示"""
    eng = field_engine
    assert _scan(eng, db, project_id, BUS1)["accepted"]
    assert _scan(eng, db, project_id, CHIPS[0])["accepted"]
    assert _scan(eng, db, project_id, CHIPS[1])["accepted"]
    # 第 3 个芯子 (count=2) → ng_alarm
    n_events = len(eng.fired_events)
    r = _scan(eng, db, project_id, CHIPS[2])
    assert not r["accepted"] and "超出应扫数量" in r["msg"]
    assert len(eng.fired_events) == n_events + 1
    assert eng.fired_events[-1]["event_id"] == 2
    # 多扫母排 (=忘收尾开新件) → reject 只警告不响铃
    n_events = len(eng.fired_events)
    r = _scan(eng, db, project_id, BUS2)
    assert not r["accepted"] and "超出应扫数量" in r["msg"]
    assert len(eng.fired_events) == n_events


def test_ng_pending_resupply_to_ok(field_engine, db, project_id):
    """ng_pending: 少扫收尾→挂起响一次NG铃; 补扫缺码→自动转 OK 结算"""
    eng = field_engine
    _set_cfg(eng, project_id, _cfg_field(ng_pending=True))
    assert _scan(eng, db, project_id, BUS1)["accepted"]
    assert _scan(eng, db, project_id, CHIPS[0])["accepted"]
    # 缺 1 芯子直接收尾 → 挂起不结算
    assert _scan(eng, db, project_id, FIX1)["accepted"]
    state = eng._groups.get(0)
    assert state is not None and state.pending_ng, "应进入挂起态而不是结算"
    assert 0 not in eng._last_settled
    assert eng.fired_events[-1]["event_id"] == 2  # 挂起即响 NG 铃
    st_view = eng.get_state(db, 0, project_id)
    assert st_view["pending_ng"] and "缺" in st_view["pending_ng"]["reason"]
    # 补扫缺的芯子 → 自动转 OK
    r = _scan(eng, db, project_id, CHIPS[1])
    assert r["accepted"]
    last = eng._last_settled[0]
    assert last["result"] == "ok" and "补扫" in last["reason"]
    assert eng.fired_events[-1]["event_id"] == 1


def test_ng_pending_manual_resolve(field_engine, db, project_id):
    """ng_pending: 挂起后人工按 NG 放行 → ng_missing 结算且不二次响铃"""
    eng = field_engine
    _set_cfg(eng, project_id, _cfg_field(ng_pending=True))
    for c in [BUS1, CHIPS[0], FIX1]:
        _scan(eng, db, project_id, c)
    assert eng._groups[0].pending_ng
    n_ng = sum(1 for e in eng.fired_events if e["event_id"] == 2)
    ok, msg = eng.resolve_ng(db, 0)
    assert ok, msg
    last = eng._last_settled[0]
    assert last["result"] == "ng_missing" and last["is_good"] is False
    # 挂起进入时已响过铃, 放行不再二次响
    assert sum(1 for e in eng.fired_events if e["event_id"] == 2) == n_ng
    assert 0 not in eng._groups
    # 没挂起时调放行 → 报错
    ok, msg = eng.resolve_ng(db, 0)
    assert not ok


def test_ng_pending_blocks_next_workpiece(field_engine, db, project_id):
    """ng_pending: 挂起期间扫新工件的母排 → 母排槽已满被拦, 提示带挂起上下文"""
    eng = field_engine
    _set_cfg(eng, project_id, _cfg_field(ng_pending=True))
    for c in [BUS1, CHIPS[0], FIX1]:
        _scan(eng, db, project_id, c)
    assert eng._groups[0].pending_ng
    r = _scan(eng, db, project_id, BUS2)
    assert not r["accepted"] and "挂起" in r["msg"]
    # 组仍在挂起, 未被新码冲掉
    assert eng._groups[0].pending_ng


def test_vision_gate_combined_verdict(field_engine, db, project_id):
    """vision_gate: 扫码齐但视觉 NG → ng_vision; 视觉 OK → ok; 无视觉按策略"""
    eng = field_engine
    _set_cfg(eng, project_id, _cfg_field(vision_gate=True))
    # 视觉 NG → 整组 NG
    eng.on_vision_cycle(0, False, "缺件：芯子少装")
    for c in [BUS1, CHIPS[0], CHIPS[1], FIX1]:
        _scan(eng, db, project_id, c)
    last = eng._last_settled[0]
    assert last["result"] == "ng_vision" and "视觉" in last["reason"]
    assert last["vision"]["is_good"] is False
    # 视觉 OK → 整组 OK
    eng.on_vision_cycle(0, True, "")
    for c in [BUS2, CHIPS[2], CHIPS[3], "H-C035-527-9"]:
        _scan(eng, db, project_id, c)
    assert eng._last_settled[0]["result"] == "ok"


def test_vision_gate_missing_policy(field_engine, db, project_id):
    """vision_gate: 窗口内无视觉结果 — ignore 按扫码判 / ng 判 NG"""
    eng = field_engine
    # ignore (默认): 无视觉结果按扫码 OK
    _set_cfg(eng, project_id, _cfg_field(vision_gate=True, vision_missing="ignore"))
    for c in [BUS1, CHIPS[0], CHIPS[1], FIX1]:
        _scan(eng, db, project_id, c)
    assert eng._last_settled[0]["result"] == "ok"
    assert eng._last_settled[0]["vision"]["is_good"] is None
    # ng: 无视觉结果判 NG
    eng2_cfg = _cfg_field(vision_gate=True, vision_missing="ng")
    _set_cfg(eng, project_id, eng2_cfg)
    for c in [BUS2, CHIPS[2], CHIPS[3], "H-C035-527-9"]:
        _scan(eng, db, project_id, c)
    assert eng._last_settled[0]["result"] == "ng_vision_missing"


def test_vision_gate_window_expiry(field_engine, db, project_id, monkeypatch):
    """vision_gate: 视觉结果超窗视为缺失"""
    from datetime import datetime, timedelta
    eng = field_engine
    _set_cfg(eng, project_id,
             _cfg_field(vision_gate=True, vision_window_sec=5, vision_missing="ng"))
    eng.on_vision_cycle(0, True, "")
    eng._vision_last[0]["ts"] = datetime.now() - timedelta(seconds=60)  # 人为过期
    for c in [BUS1, CHIPS[0], CHIPS[1], FIX1]:
        _scan(eng, db, project_id, c)
    assert eng._last_settled[0]["result"] == "ng_vision_missing"


def test_config_roundtrip_new_fields(client, project_id):
    """PUT/GET 配置往返: v3.56.1 新增字段 (槽位覆盖 + ng_pending + vision_gate)"""
    payload = {
        "enabled": True,
        "slots": [
            {"key": "busbar", "label": "母排码", "count": 1,
             "regex": "^M.{29,32}$", "role": "", "on_overflow": "reject"},
            {"key": "fixture", "label": "工装码", "count": 1, "regex": "^H-C",
             "role": "closing", "dedup_cross_group": "off"},
        ],
        "sequence_fallback": True,
        "dedup_in_group": "ng_alarm",
        "dedup_cross_group": "reject",
        "settle_on": "closing",
        "on_overflow": "reject",
        "on_unmatched": "reject",
        "timeout_sec": 0,
        "event_ok_id": 1,
        "event_ng_id": 2,
        "ng_pending": True,
        "vision_gate": True,
        "vision_window_sec": 120,
        "vision_missing": "ng",
    }
    r = client.put(f"/api/v1/scan-collect/config?project_id={project_id}",
                   json=payload)
    assert r.status_code == 200, r.text
    got = client.get(
        f"/api/v1/scan-collect/config?project_id={project_id}").json()
    assert got["ng_pending"] is True
    assert got["vision_gate"] is True
    assert got["vision_window_sec"] == 120
    assert got["vision_missing"] == "ng"
    slots = {s["key"]: s for s in got["slots"]}
    assert slots["fixture"]["dedup_cross_group"] == "off"
    assert slots["busbar"]["on_overflow"] == "reject"
