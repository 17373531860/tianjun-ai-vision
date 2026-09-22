# -*- coding: utf-8 -*-
"""v3.60.2 多码采集「随视觉周期结算」(settle_on_vision_cycle, 六和现场反馈 2026-09-20).

背景: 现场少扫收尾码 (工装码) 时, closing 结算永远不来 — 视觉末步 (盖模具
盖板) 结算为 OK 翻篇进新周期, 少扫组还挂在面板上不结算 (真实录像
23:38:29→23:38:59 复现)。期望: 视觉末步结算瞬间强制收口扫码组 — 缺码则
本视觉周期直接判 NG (一件一账), 码齐组按 OK 收口, 面板随周期翻篇。

实现三件套 (全部默认关零差异):
  1. ScanCollectEngine.peek_vision_settle_gate  — 推理线程零阻塞探针
     (try-lock + 纯缓存, 不碰 DB), 返回缺码文案供周期 NG reason 融合
  2. ScanCollectEngine.settle_by_vision_cycle   — 视觉结算点后台线程收口:
     缺码 ng_missing / 码齐 ok, fire_events=False 不借事件面
  3. _settle_detection_cycle 融合探针: 缺码文案并入 ng_reasons → 周期 NG

v3.60.1c 增补 (2026-09-21 六和现场事故复盘, 见 backend.log 三链):
  4. 组归属锚 cycle_anchor_ts — 上一件末步消失确认的空档里操作员已在扫
     下一件母排码, 无锚探针/收口会拿下一件的缺码翻本件 NG、误收下一件组
  5. 结算主权归视觉 — settle_on_vision_cycle 开启时 closing/all_filled/
     挂起路径全部让位 (现场 NG 挂起把组占住 → 下一件码被拒收级联混码)
  6. 码齐组同样随周期收口 (has_group_for_vision_settle 守门)

测试不碰真 DB: 配置走 _cfg_cache 注入, 组状态手工构造, db 用 MagicMock
(_settle 内 DB 操作全部有隔离 try/except)。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from backend.services.scan_collect import (
    _GroupState,
    get_scan_collect_engine,
)

PID = 92001
CH = 0

SLOTS = [
    {"key": "busbar", "label": "母排码", "count": 1, "regex": "^M"},
    {"key": "chip", "label": "芯子码", "count": 2, "regex": "^C"},
    {"key": "fixture", "label": "工装码", "count": 1, "role": "closing",
     "regex": "^H-"},
]


def _seed(engine, *, on: bool, codes: list):
    """注入配置缓存 + 手工组状态 (codes: [(slot_key, code), ...])。"""
    cfg = {"slots": SLOTS, "settle_on_vision_cycle": on,
           "vision_gate": False, "ng_pending": False}
    with engine._lock:
        engine._cfg_cache[PID] = {"enabled": True, "config": dict(cfg)}
        state = _GroupState(PID)
        for i, (slot_key, code) in enumerate(codes):
            label = next(s["label"] for s in SLOTS if s["key"] == slot_key)
            state.codes.append({"record_id": None, "slot_key": slot_key,
                                "slot_label": label, "code": code,
                                "seq": i + 1, "ts": "00:00:00"})
        engine._groups[CH] = state
    return state


@pytest.fixture()
def engine():
    eng = get_scan_collect_engine()
    yield eng
    with eng._lock:
        eng._groups.pop(CH, None)
        eng._last_settled.pop(CH, None)
    eng.invalidate_config(PID)


# ============================================================
# 1. peek_vision_settle_gate (推理线程探针)
# ============================================================

def test_探针_开关关闭_零差异返回None(engine):
    _seed(engine, on=False, codes=[("busbar", "M1")])
    assert engine.peek_vision_settle_gate(CH) is None


def test_探针_开启且缺码_返回缺码文案(engine):
    _seed(engine, on=True, codes=[("busbar", "M1"), ("chip", "C1")])
    txt = engine.peek_vision_settle_gate(CH)
    assert txt is not None
    assert "少扫码" in txt
    assert "芯子码缺1" in txt
    assert "工装码缺1" in txt


def test_探针_码齐_返回None(engine):
    _seed(engine, on=True, codes=[("busbar", "M1"), ("chip", "C1"),
                                  ("chip", "C2"), ("fixture", "H-C1")])
    assert engine.peek_vision_settle_gate(CH) is None


def test_探针_无组_返回None(engine):
    with engine._lock:
        engine._groups.pop(CH, None)
    assert engine.peek_vision_settle_gate(CH) is None


def test_探针_组开于锚点之后_下一件的组不参与判定(engine):
    """v3.60.1a 现场事故: 上一件盖板消失确认的 2s 空档里操作员扫了下一件
    母排码 → 新组开在末步出现之后 → 无锚探针拿它的缺码把本件视觉 OK 翻 NG。
    带锚后: 开于锚点之后的组一律返回 None。"""
    state = _seed(engine, on=True, codes=[("busbar", "M-next")])
    anchor = state.started_at - timedelta(seconds=5)   # 锚(盖盖板)在开组之前
    assert engine.peek_vision_settle_gate(CH, anchor) is None
    # 本件的组 (开于锚点之前) 照常判定
    anchor2 = state.started_at + timedelta(seconds=5)
    assert "少扫码" in (engine.peek_vision_settle_gate(CH, anchor2) or "")


def test_探针_锁被占用_零阻塞返回None(engine):
    """扫码线程恰在持锁结算时, 推理线程探针不得阻塞 — 立即 None。"""
    import threading

    _seed(engine, on=True, codes=[("busbar", "M1")])
    got = {}
    hold = threading.Event()
    done = threading.Event()

    def _holder():
        with engine._lock:
            hold.set()
            done.wait(3)

    t = threading.Thread(target=_holder, daemon=True)
    t.start()
    hold.wait(3)
    try:
        got["v"] = engine.peek_vision_settle_gate(CH)
    finally:
        done.set()
        t.join(3)
    assert got["v"] is None


# ============================================================
# 2. settle_by_vision_cycle (异步收口)
# ============================================================

def test_收口_开关关闭_零差异不动组(engine):
    state = _seed(engine, on=False, codes=[("busbar", "M1")])
    assert engine.settle_by_vision_cycle(MagicMock(), CH) is False
    assert engine._groups.get(CH) is state, "开关关闭组必须原样保留"


def test_收口_缺码_按ng_missing结算关组且不借事件面(engine):
    _seed(engine, on=True, codes=[("busbar", "M1"), ("chip", "C1")])
    fired = []
    orig = engine._fire_event
    engine._fire_event = lambda *a, **k: fired.append((a, k))
    try:
        assert engine.settle_by_vision_cycle(MagicMock(), CH) is True
    finally:
        engine._fire_event = orig
    assert engine._groups.get(CH) is None, "组应已关闭翻篇"
    last = engine._last_settled.get(CH)
    assert last is not None and last["result"] == "ng_missing"
    assert "随视觉周期收口" in last["reason"]
    assert fired == [], "视觉周期已计数/报警, 收口不得再借事件面 (防一件双报)"


def test_收口_码齐_按OK结算(engine):
    _seed(engine, on=True, codes=[("busbar", "M1"), ("chip", "C1"),
                                  ("chip", "C2"), ("fixture", "H-C1")])
    assert engine.settle_by_vision_cycle(MagicMock(), CH) is True
    last = engine._last_settled.get(CH)
    assert last is not None and last["result"] == "ok" and last["is_good"] is True


def test_收口_挂起态组_同样强制出结果(engine):
    """ng_pending 挂起组不豁免: 视觉周期翻篇 = 本件必须出最终结果。"""
    state = _seed(engine, on=True, codes=[("busbar", "M1")])
    state.pending_ng = {"reason": "少扫挂起", "missing": [], "since": "00:00:00"}
    assert engine.settle_by_vision_cycle(MagicMock(), CH) is True
    assert engine._groups.get(CH) is None
    assert engine._last_settled[CH]["result"] == "ng_missing"


def test_收口_组开于锚点之后_不误收下一件的组(engine):
    """v3.60.1a 现场事故: 无锚收口把下一件刚开的组(只扫了母排码)误判
    ng_missing 关掉 → 下一件继续扫码又开新组, 码从此对应不上工件。"""
    state = _seed(engine, on=True, codes=[("busbar", "M-next")])
    anchor = state.started_at - timedelta(seconds=5)
    assert engine.settle_by_vision_cycle(MagicMock(), CH, anchor) is False
    assert engine._groups.get(CH) is state, "下一件的组必须原样保留"
    assert state.codes[0]["code"] == "M-next"


def test_守门_码齐组识别为待收口_开关关闭返回False(engine):
    """has_group_for_vision_settle: 码齐的组也要随周期收口翻篇 (探针只报
    缺码, v3.60.1b 的缺口是码齐组永远挂着不落库)。"""
    _seed(engine, on=True, codes=[("busbar", "M1"), ("chip", "C1"),
                                  ("chip", "C2"), ("fixture", "H-C1")])
    assert engine.has_group_for_vision_settle(CH) is True
    _seed(engine, on=False, codes=[("busbar", "M1")])
    assert engine.has_group_for_vision_settle(CH) is False
    with engine._lock:
        engine._groups.pop(CH, None)
    assert engine.has_group_for_vision_settle(CH) is False


# ============================================================
# 2b. 结算主权归视觉 (v3.60.1c: closing/挂起路径让位)
# ============================================================

def test_扫收尾码_结算主权归视觉_不触发结算不挂起(engine):
    """现场事故链一: settle_on=closing + ng_pending 时扫收尾码触发 NG 挂起,
    组占住 → 下一件母排码被"超出应扫数量"拒收 → 级联混码。
    v3.60.1c: settle_on_vision_cycle 开启时收尾码只入组, 组开着等视觉收口。"""
    cfg = {"slots": [
               {"key": "busbar", "label": "母排码", "count": 1, "regex": "^M"},
               {"key": "chip", "label": "芯子码", "count": 2, "regex": r"^\d"},
               {"key": "fixture", "label": "工装码", "count": 1, "role": "closing",
                "regex": "^H-"},
           ],
           "settle_on_vision_cycle": True,
           "settle_on": "closing", "ng_pending": True, "vision_gate": False}
    with engine._lock:
        engine._cfg_cache[PID] = {"enabled": True, "config": dict(cfg)}
        engine._groups.pop(CH, None)
    db = MagicMock()
    engine.on_scan(db, CH, "M1", "M1", PID)
    r = engine.on_scan(db, CH, "H-C035-527-6", "H-C035-527-6", PID)  # 收尾码
    assert r["accepted"] is True
    state = engine._groups.get(CH)
    assert state is not None, "组必须仍开着等视觉收口 (不得 closing 结算)"
    assert state.pending_ng is None, "不得进 NG 挂起"
    # 视觉收口后翻篇, 下一件母排码正常开新组不被拒收
    assert engine.settle_by_vision_cycle(db, CH) is True
    r2 = engine.on_scan(db, CH, "M2", "M2", PID)
    assert r2["accepted"] is True, "下一件母排码不得被旧组拒收"
    assert engine._groups[CH].codes[0]["code"] == "M2"


def test_扫收尾码_开关关闭_仍走closing结算零差异(engine):
    cfg = {"slots": [
               {"key": "busbar", "label": "母排码", "count": 1, "regex": "^M"},
               {"key": "chip", "label": "芯子码", "count": 2, "regex": r"^\d"},
               {"key": "fixture", "label": "工装码", "count": 1, "role": "closing",
                "regex": "^H-"},
           ],
           "settle_on_vision_cycle": False,
           "settle_on": "closing", "ng_pending": False, "vision_gate": False}
    with engine._lock:
        engine._cfg_cache[PID] = {"enabled": True, "config": dict(cfg)}
        engine._groups.pop(CH, None)
    db = MagicMock()
    engine.on_scan(db, CH, "M1", "M1", PID)
    engine.on_scan(db, CH, "H-C035-527-6", "H-C035-527-6", PID)
    assert engine._groups.get(CH) is None, "开关关闭时收尾码照常触发结算关组"
    assert engine._last_settled[CH]["result"] == "ng_missing"


def test_收口_不走视觉门_避免连坐上一件(engine):
    """现场事故: 收口线程早于 _handle_cycle_end 回喂, _vision_last 还是上一件
    NG → 开着 vision_gate 会把码齐的本件也打成 ng_vision。视觉周期收口跳过门。"""
    _seed(engine, on=True, codes=[("busbar", "M1"), ("chip", "C1"),
                                  ("chip", "C2"), ("fixture", "H-C1")])
    with engine._lock:
        engine._cfg_cache[PID]["config"]["vision_gate"] = True
    engine._vision_last[CH] = {
        "is_good": False, "reason": "上一件NG", "ts": datetime.now(),
    }
    assert engine.settle_by_vision_cycle(MagicMock(), CH) is True
    last = engine._last_settled[CH]
    assert last["result"] == "ok" and last["is_good"] is True


# ============================================================
# 3. 检测模式结算融合 (真 VSM 状态机单点)
# ============================================================

def _make_vsm():
    from backend.api.source import VideoSourceManager

    vsm = VideoSourceManager(channel_id=CH)
    vsm.set_project_config({
        'id': PID,
        'name': '随视觉周期结算单测项目',
        'task_type': 'detection',
        'logic_mode': 'detection',
        'steps_config': [
            {'id': 1, 'label': 'A', 'enabled': True, 'min_frames': 1},
            {'id': 2, 'label': 'B', 'enabled': True, 'min_frames': 1},
        ],
        'events_config': [
            {'id': 1, 'name': 'OK', 'actions': [], 'show_notification': False},
            {'id': 2, 'name': 'NG', 'actions': [], 'show_notification': False},
        ],
        'counters_config': [],
        'pipeline_config': {},
    })
    vsm.start_step_recording = lambda *a, **k: None
    vsm.stop_step_recording = lambda *a, **k: None
    vsm.record_step = lambda *a, **k: None
    vsm.start_cycle = lambda *a, **k: None
    vsm._test_events = []
    vsm._trigger_event = lambda eid, reason='': vsm._test_events.append((eid, reason))
    # v3.60.1c 组归属锚: 探针/收口只在末步(B)出现时间可取时参与 —
    # 模拟"刚盖上盖板"(锚=now), 此前 _seed 开的组都在锚点之前 = 本件的组
    vsm.step_start_time['B'] = time.time() + 0.5
    return vsm


def test_检测结算_步骤全齐但扫码缺码_周期判NG带缺码原因(engine):
    _seed(engine, on=True, codes=[("busbar", "M1"), ("chip", "C1")])
    vsm = _make_vsm()
    vsm.current_cycle_steps = ['A', 'B']
    vsm._settle_detection_cycle()
    assert vsm._test_events, "结算应触发事件"
    eid, reason = vsm._test_events[-1]
    assert eid == 2, f"扫码缺码应把本周期判 NG, got {vsm._test_events[-1]}"
    assert "少扫码" in reason and "工装码缺1" in reason


def test_检测结算_开关关闭_零差异仍OK(engine):
    _seed(engine, on=False, codes=[("busbar", "M1")])
    vsm = _make_vsm()
    vsm.current_cycle_steps = ['A', 'B']
    vsm._settle_detection_cycle()
    eid, reason = vsm._test_events[-1]
    assert eid == 1, f"开关关闭时扫码组不得影响视觉判定, got {vsm._test_events[-1]}"


def test_检测结算_码齐_周期照常OK(engine):
    _seed(engine, on=True, codes=[("busbar", "M1"), ("chip", "C1"),
                                  ("chip", "C2"), ("fixture", "H-C1")])
    vsm = _make_vsm()
    vsm.current_cycle_steps = ['A', 'B']
    vsm._settle_detection_cycle()
    eid, _ = vsm._test_events[-1]
    assert eid == 1


def test_检测结算_下一件组开于锚后_本件视觉OK不被翻转(engine, monkeypatch):
    """现场事故链二全链复刻: 本件码组已收口, 盖板消失确认的空档里操作员
    扫了下一件母排码(开新组) → 本件视觉结算不得拿下一件的缺码翻 NG,
    也不得把下一件的组收掉。"""
    import backend.db.database as _dbm
    monkeypatch.setattr(_dbm, "SessionLocal", lambda: MagicMock())
    vsm = _make_vsm()
    vsm.current_cycle_steps = ['A', 'B']
    # 下一件的组开在末步(B)出现之后
    state = _seed(engine, on=True, codes=[("busbar", "M-next")])
    state.started_at = datetime.now() + timedelta(seconds=2)
    vsm._settle_detection_cycle()
    eid, reason = vsm._test_events[-1]
    assert eid == 1, f"本件视觉 OK 不得被下一件缺码翻转, got {vsm._test_events[-1]}"
    time.sleep(0.3)   # 等可能误起的收口线程跑完
    assert engine._groups.get(CH) is state, "下一件的组必须原样保留"


def test_检测结算_末步未出现无锚_不翻不收(engine):
    """空闲超时等末步没出现的结算拿不到锚 → 保守不翻不收,
    组留给下个周期或人工出口 (宁可迟收不可误收)。"""
    _seed(engine, on=True, codes=[("busbar", "M1")])
    vsm = _make_vsm()
    vsm.step_start_time.clear()   # 末步 B 无出现记录
    vsm.current_cycle_steps = ['A', 'B']
    vsm._settle_detection_cycle()
    eid, _ = vsm._test_events[-1]
    assert eid == 1, "无锚时不得翻转"
    assert engine._groups.get(CH) is not None, "无锚时不得收口"


def test_检测结算_探针命中_自兜底收口关组_不依赖周期钩子(engine, monkeypatch):
    """v3.60.1b 六和现场复盘 (2026-09-21 backend.log): 扫码器开「须先扫码
    才开始周期」(scan_required) 时多码采集独占扫码不写单码绑定 → start_cycle
    恒被跳过 → 周期行不建 → on_cycle_end 钩子从不触发 → 挂在
    _handle_cycle_end 上的收口是死代码 (组只能靠补扫/人工清空)。

    修复: _settle_detection_cycle 探针命中时自兜底 — 后台线程自开会话收口。
    本测试完全不碰周期钩子, 组必须仍被关闭。
    """
    import time as _time

    import backend.db.database as _dbm
    monkeypatch.setattr(_dbm, "SessionLocal", lambda: MagicMock())
    _seed(engine, on=True, codes=[("busbar", "M1")])
    vsm = _make_vsm()
    vsm.current_cycle_steps = ['A', 'B']
    vsm._settle_detection_cycle()

    deadline = _time.time() + 3
    while _time.time() < deadline and engine._groups.get(CH) is not None:
        _time.sleep(0.05)
    assert engine._groups.get(CH) is None, "组应由视觉结算侧后台线程收口关闭"
    assert engine._last_settled[CH]["result"] == "ng_missing"
    assert "随视觉周期收口" in engine._last_settled[CH]["reason"]
