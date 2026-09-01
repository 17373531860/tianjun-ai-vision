"""v3.48 计数组合判定表 (纯视觉判型, RFC 14 配套项) —— 单元 + synthetic 端到端回归.

场景原型: 三区打螺丝, 数量组合 (5,5,4) 判"4缸" / (6,6,4) 判"6缸", 未知组合一律 NG。
本测试用缩小版 (区A, 区B) 走真实 pipeline:

  1. _parse_combo_table 归一化 (禁用/空表/坏行/非法计数)
  2. _apply_combo_verdict 查表语义 (命中 OK / 命中 NG / 未命中防呆 NG / 未配表零差异)
  3. synthetic 端到端: 命中行 → OK + reason 带机型 tag + combo_verdict.last_tag 透出
  4. synthetic 端到端: 未命中组合 → NG (防呆)
  5. 零差异: 不配表时同剧本重复标签照旧 NG (让位逻辑只在配表后生效)
"""
from __future__ import annotations

import time
from collections import Counter

from backend.api.source_project_config_apply import _parse_combo_table
from backend.api.source_settlement_mixin import SettlementMixin


CH = 0


# ================= 1. _parse_combo_table 归一化 =================

def test_parse_disabled_or_empty_returns_none():
    assert _parse_combo_table(None) is None
    assert _parse_combo_table({}) is None
    assert _parse_combo_table({"enabled": False, "labels": ["a"], "rows": [
        {"counts": [1], "verdict": "OK", "tag": "x"}]}) is None
    assert _parse_combo_table({"enabled": True, "labels": [], "rows": [
        {"counts": [], "verdict": "OK"}]}) is None
    # 启用但全部行非法 → 整表禁用
    assert _parse_combo_table({"enabled": True, "labels": ["a", "b"], "rows": [
        {"counts": [1], "verdict": "OK"},           # 长度不符
        {"counts": [1, "x"], "verdict": "OK"},      # 非整数
        "not-a-dict",
    ]}) is None


def test_parse_normalizes_rows():
    cfg = _parse_combo_table({
        "enabled": True,
        "labels": [" 区A ", "区B", "区A"],  # 去空白 + 去重
        "rows": [
            {"counts": [5, 4], "verdict": "ok", "tag": "4缸"},
            {"counts": ["6", 4.0], "verdict": "NG", "tag": " 坏组合 "},
            {"counts": [-1, 2], "verdict": "什么都不是", "tag": ""},
        ],
    })
    assert cfg["labels"] == ["区A", "区B"]
    assert cfg["rows"][0] == {"counts": [5, 4], "verdict": "OK", "tag": "4缸", "plc_code": ""}
    assert cfg["rows"][1] == {"counts": [6, 4], "verdict": "NG", "tag": "坏组合", "plc_code": ""}
    # 负数钳到 0, 未知 verdict 按 OK
    assert cfg["rows"][2] == {"counts": [0, 2], "verdict": "OK", "tag": "", "plc_code": ""}


# ============ 1b. count_mode='positional' 位置去重计数引擎 ============

def _det_at(label, x, y, w=0.1, h=0.1):
    return {"label": label, "confidence": 0.9, "bbox": [x, y, w, h]}


def test_parse_count_mode_positional():
    cfg = _parse_combo_table({
        "enabled": True, "labels": ["a"], "count_mode": "positional",
        "tracking": {"iou": 0.3, "min_consecutive": 2},
        "rows": [{"counts": [1], "verdict": "OK"}],
    })
    assert cfg["count_mode"] == "positional"
    assert cfg["tracking"]["iou"] == 0.3
    assert cfg["tracking"]["min_consecutive"] == 2
    assert cfg["tracking"]["ema_alpha"] == 0.6  # 默认
    # 缺省 = steps (零差异)
    cfg2 = _parse_combo_table({
        "enabled": True, "labels": ["a"],
        "rows": [{"counts": [1], "verdict": "OK"}],
    })
    assert cfg2["count_mode"] == "steps" and cfg2["tracking"] == {}


def test_parse_show_lock_overlay_default_on_and_off():
    """锁定框显示开关: 缺省/非 False 一律 True; 显式 False 才关 (只影响显示)。"""
    base = {"enabled": True, "labels": ["a"],
            "rows": [{"counts": [1], "verdict": "OK"}]}
    assert _parse_combo_table(base)["show_lock_overlay"] is True
    assert _parse_combo_table({**base, "show_lock_overlay": True})["show_lock_overlay"] is True
    assert _parse_combo_table({**base, "show_lock_overlay": False})["show_lock_overlay"] is False


def test_positional_counts_distinct_positions_once():
    """同位置反复出现只计 1 次 (返工不重计), 不同位置各计 1 次。"""
    from backend.api.source_combo_positional import ComboPositionalCounter
    eng = ComboPositionalCounter(["a"], min_consecutive=3)
    for _ in range(10):
        eng.feed([_det_at("a", 0.1, 0.1)])
    assert eng.counts() == {"a": 1}
    # 同位置"离场后返工": 中间空 20 tick 再回来 → 仍是同一 ROI, 不加数
    for _ in range(20):
        eng.feed([])
    for _ in range(5):
        eng.feed([_det_at("a", 0.1, 0.1)])
    assert eng.counts() == {"a": 1}
    # 新位置 (无重叠) → 计到 2
    for _ in range(3):
        eng.feed([_det_at("a", 0.6, 0.6)])
    assert eng.counts() == {"a": 2}


def test_positional_needs_min_consecutive():
    """未凑满连续确认帧的闪现不计数; 候选过期后重新累计。"""
    from backend.api.source_combo_positional import ComboPositionalCounter
    eng = ComboPositionalCounter(["a"], min_consecutive=3, pending_ttl=5)
    eng.feed([_det_at("a", 0.1, 0.1)])
    eng.feed([_det_at("a", 0.1, 0.1)])   # 只连续 2 tick
    for _ in range(6):                   # 候选过期
        eng.feed([])
    assert eng.counts() == {}
    for _ in range(3):
        eng.feed([_det_at("a", 0.1, 0.1)])
    assert eng.counts() == {"a": 1}


def test_positional_reset_clears_pool():
    from backend.api.source_combo_positional import ComboPositionalCounter
    eng = ComboPositionalCounter(["a"], min_consecutive=1)
    eng.feed([_det_at("a", 0.1, 0.1)])
    assert eng.counts() == {"a": 1}
    eng.reset()
    assert eng.counts() == {}
    # 清池后同位置再次出现 → 重新计数 (新周期新工件)
    eng.feed([_det_at("a", 0.1, 0.1)])
    assert eng.counts() == {"a": 1}


def test_positional_ignores_non_combo_labels():
    from backend.api.source_combo_positional import ComboPositionalCounter
    eng = ComboPositionalCounter(["a"], min_consecutive=1)
    eng.feed([_det_at("b", 0.1, 0.1), _det_at("a", 0.5, 0.5)])
    assert eng.counts() == {"a": 1}


def test_positional_rois_expose_locked_seq_and_pending_progress():
    """v3.48.x Monitor 锁框可视化: rois() 透出锁定 ROI (带 #seq 编号) 与
    候选 (带 seen/need 进度); 断检后锁框仍常驻; reset 后清空。"""
    from backend.api.source_combo_positional import ComboPositionalCounter
    eng = ComboPositionalCounter(["a"], min_consecutive=3)
    # 第 1 个位置锁定
    for _ in range(3):
        eng.feed([_det_at("a", 0.1, 0.1)])
    # 第 2 个位置确认中 (2/3)
    for _ in range(2):
        eng.feed([_det_at("a", 0.6, 0.6)])
    rois = eng.rois()
    locked = [r for r in rois if r["state"] == "locked"]
    pending = [r for r in rois if r["state"] == "pending"]
    assert len(locked) == 1 and locked[0]["seq"] == 1
    assert len(locked[0]["box"]) == 4
    assert len(pending) == 1 and pending[0]["seen"] == 2 and pending[0]["need"] == 3
    # 锁定第 2 个 → seq=2; 断检 5 tick 后锁框仍常驻 (perish_ticks=0)
    eng.feed([_det_at("a", 0.6, 0.6)])
    for _ in range(5):
        eng.feed([])
    locked = [r for r in eng.rois() if r["state"] == "locked"]
    assert sorted(r["seq"] for r in locked) == [1, 2]
    eng.reset()
    assert eng.rois() == []


def test_verdict_uses_positional_counts_when_engine_present():
    """结算读位置计数而非步骤计数; 命中行带 tag。"""
    from backend.api.source_combo_positional import ComboPositionalCounter

    class _PosMgr(_FakeMgr):
        pass

    mgr = _PosMgr({
        "labels": ["a", "b"],
        "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "机型P"}],
    })
    eng = ComboPositionalCounter(["a", "b"], min_consecutive=1)
    eng.feed([_det_at("a", 0.1, 0.1), _det_at("a", 0.6, 0.6),
              _det_at("b", 0.3, 0.8)])
    mgr._combo_positional = eng
    # 步骤计数故意给错的向量 (a×5) — 引擎在场时必须被无视
    event_id, reason = SettlementMixin._apply_combo_verdict(mgr, Counter(["a"] * 5))
    assert event_id == 1 and "机型P" in reason and "a×2" in reason


# ================= 2. _apply_combo_verdict 查表语义 =================

class _FakeMgr:
    channel_id = 0
    _combo_last_tag = None

    def __init__(self, combo):
        self._combo_table = combo


_TABLE = {
    "labels": ["区A", "区B"],
    "rows": [
        {"counts": [2, 1], "verdict": "OK", "tag": "机型X"},
        {"counts": [3, 1], "verdict": "NG", "tag": "禁产组合"},
    ],
}


def _verdict(combo, counts):
    mgr = _FakeMgr(combo)
    return mgr, SettlementMixin._apply_combo_verdict(mgr, Counter(counts))


def test_verdict_no_table_zero_diff():
    _, (event_id, reason) = _verdict(None, ["区A"])
    assert (event_id, reason) == (1, "检测完成")


def test_verdict_hit_ok_row_carries_tag():
    mgr, (event_id, reason) = _verdict(_TABLE, ["区A", "区B", "区A"])
    assert event_id == 1
    assert "机型X" in reason and "区A×2" in reason and "区B×1" in reason
    assert mgr._combo_last_tag == "机型X"


def test_verdict_hit_ng_row():
    mgr, (event_id, reason) = _verdict(_TABLE, ["区A"] * 3 + ["区B"])
    assert event_id == 2
    assert "禁产组合" in reason
    assert mgr._combo_last_tag == "禁产组合"


def test_verdict_unmatched_is_ng():
    mgr, (event_id, reason) = _verdict(_TABLE, ["区A"])
    assert event_id == 2
    assert "未匹配" in reason
    assert mgr._combo_last_tag is None


# ============ 2b. 手动结算 (触发中心虚拟按钮 → 步骤类模式, v3.49) ============
#
# 背景: manual_settle 动作原先仅 per_item 模式生效, 缸体判型 (detection +
# combo_table) 现场按虚拟按钮无反应 (2026-08-13 反馈)。新链路: 动作线程
# request_manual_settle 打标志 → 推理线程 _consume_manual_settle_request 消费
# → 按 logic_mode 走既有结算函数 (OK/NG 仍按真实步骤判定)。

class _SettleReqMgr:
    channel_id = 0
    # 真实 VSM 经 SettlementMixin 继承本方法; 桩类直接借用同一实现
    request_manual_settle = SettlementMixin.request_manual_settle

    def __init__(self, lm="detection", steps=None, detecting=True, based_on=None):
        pc = {"custom_based_on": based_on} if based_on else {}
        self.project_config = {"logic_mode": lm, "pipeline_config": pc}
        self.is_detecting = detecting
        self.current_cycle_steps = list(steps or [])
        self.settled = []

    def _settle_detection_cycle(self):
        self.settled.append("detection")

    def _settle_sequential_cycle(self):
        self.settled.append("sequential")

    def _settle_custom_cycle(self):
        self.settled.append("custom")


def test_manual_settle_request_detection_mode():
    mgr = _SettleReqMgr(steps=["区A"])
    ret = SettlementMixin.request_manual_settle(mgr, source="test")
    assert ret["ok"] is True
    SettlementMixin._consume_manual_settle_request(mgr)
    assert mgr.settled == ["detection"]
    assert mgr._manual_settle_request is None
    # 再消费一次 = 无请求, 不重复结算
    SettlementMixin._consume_manual_settle_request(mgr)
    assert mgr.settled == ["detection"]


def test_manual_settle_request_dispatch_by_mode():
    seq = _SettleReqMgr(lm="sequential", steps=["a"])
    assert SettlementMixin.request_manual_settle(seq)["ok"]
    SettlementMixin._consume_manual_settle_request(seq)
    assert seq.settled == ["sequential"]

    cus = _SettleReqMgr(lm="custom", steps=["a"], based_on="sequential")
    assert SettlementMixin.request_manual_settle(cus)["ok"]
    SettlementMixin._consume_manual_settle_request(cus)
    assert cus.settled == ["custom"]


def test_manual_settle_request_refused_cases():
    # 不支持的模式 (tracking / 普通 custom)
    assert not SettlementMixin.request_manual_settle(
        _SettleReqMgr(lm="tracking", steps=["a"]))["ok"]
    assert not SettlementMixin.request_manual_settle(
        _SettleReqMgr(lm="custom", steps=["a"]))["ok"]
    # 检测未运行 / 无打开的周期
    assert not SettlementMixin.request_manual_settle(
        _SettleReqMgr(steps=["a"], detecting=False))["ok"]
    assert not SettlementMixin.request_manual_settle(_SettleReqMgr(steps=[]))["ok"]


def test_manual_settle_request_stale_cycle_ignored():
    """请求打了标志后周期先被正常收尾 → 消费时忽略, 不结算空周期。"""
    mgr = _SettleReqMgr(steps=["区A"])
    assert SettlementMixin.request_manual_settle(mgr)["ok"]
    mgr.current_cycle_steps = []          # 收尾结算抢先跑掉
    SettlementMixin._consume_manual_settle_request(mgr)
    assert mgr.settled == []
    assert mgr._manual_settle_request is None


def test_manual_settle_action_routes_to_step_mode(monkeypatch):
    """触发中心 manual_settle 动作: 非 per_item 工位改走 request_manual_settle
    (旧行为是直接报错返回 —— 反向验证: 标志必须被打上)。"""
    from backend.services.triggers.actions import ACTION_REGISTRY
    from backend.api.channel_manager import channel_manager

    mgr = _SettleReqMgr(steps=["区A"])
    mgr._per_item_config = None
    monkeypatch.setitem(channel_manager.channels, 0, mgr)

    logs = []

    class _Eng:
        name = "T-虚拟按钮"
        options = {"default_channel": 0}

        def _log(self, direction, detail):
            logs.append((direction, detail))

    ACTION_REGISTRY["manual_settle"](_Eng(), {}, {}, {})
    assert getattr(mgr, "_manual_settle_request", None), \
        "动作应打结算请求标志而不是直接放弃"
    assert any(d == "event" for d, _ in logs), logs
    # 推理帧消费后真结算
    SettlementMixin._consume_manual_settle_request(mgr)
    assert mgr.settled == ["detection"]


# ================= 3-5. synthetic 端到端 =================

def _det(label):
    return {"label": label, "confidence": 0.92, "bbox": [0.4, 0.4, 0.2, 0.2]}


def _scenario(region_a_times, name):
    """区A 出现 region_a_times 次 (出现→消失各一段) → 区B 一次 → 收尾 一次。60fps。"""
    timeline = [{"from": 0, "to": 9, "detections": []}]
    frame = 10
    for _ in range(region_a_times):
        timeline.append({"from": frame, "to": frame + 39, "detections": [_det("区A")]})
        timeline.append({"from": frame + 40, "to": frame + 59, "detections": []})
        frame += 60
    timeline.append({"from": frame, "to": frame + 39, "detections": [_det("区B")]})
    timeline.append({"from": frame + 40, "to": frame + 59, "detections": []})
    frame += 60
    timeline.append({"from": frame, "to": frame + 39, "detections": [_det("收尾")]})
    timeline.append({"from": frame + 40, "to": frame + 900, "detections": []})
    return {"name": name, "fps": 60, "timeline": timeline}


def _project(name, with_combo=True, accept_once=False):
    steps = [
        {"id": f"cmb-{i}", "label": lbl, "threshold": 0.3, "min_frames": 1,
         "accept_once": accept_once, "color": "#1976d2"}
        for i, lbl in enumerate(["区A", "区B", "收尾"], start=1)
    ]
    pipeline = {
        "settlement_mode": "last_step",
        "detection_steps": [s["id"] for s in steps],
    }
    if with_combo:
        pipeline["combo_table"] = {
            "enabled": True,
            "labels": ["区A", "区B"],
            "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "机型X"}],
        }
    return {
        "project_id": -1,
        "name": name,
        "task_type": "detect",
        "logic_mode": "detection",
        "steps_config": steps,
        "pipeline_config": pipeline,
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [
                {"counter_name": "合格总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [
                {"counter_name": "不良总数", "delta": 1},
                {"counter_name": "总产量", "delta": 1}]},
        ],
        "counters_config": [
            {"name": "合格总数", "value": 0},
            {"name": "不良总数", "value": 0},
            {"name": "总产量", "value": 0},
        ],
    }


def _start(client, scenario, cfg):
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario_json": scenario, "channel": CH, "with_project": False,
    })
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/set-project?channel={CH}", json=cfg)
    assert r.status_code == 200, r.text[:300]
    r = client.post(f"/api/v1/source/detection/start?channel={CH}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, r.text[:300]


def _cleanup(client):
    client.post(f"/api/v1/source/detection/stop?channel={CH}")
    client.post(f"/api/v1/test/synthetic/stop?channel={CH}")


def _poll(client, pred, timeout=25.0):
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        r = client.get(f"/api/v1/source/detection/results?channel={CH}")
        if r.status_code == 200:
            body = r.json()
            try:
                if pred(body):
                    return body
            except Exception:
                pass
        time.sleep(0.3)
    return body


def _baseline(client):
    """counters 按 project 持久化 (三用例共用 project_id=-1), 断言必须用增量。"""
    body = _poll(client, lambda b: isinstance(b.get("counters"), dict), timeout=10.0)
    assert body is not None, "/detection/results 无返回"
    c = body.get("counters") or {}
    return c.get("不良总数", 0), c.get("合格总数", 0)


def test_e2e_combo_hit_ok_with_tag(client):
    """区A×2 + 区B×1 命中 (2,1) 行 → OK, 机型 tag 进事件 reason + combo_verdict 透出。"""
    _start(client, _scenario(2, "combo_hit"), _project("__combo_hit__"))
    try:
        base_ng, base_ok = _baseline(client)
        body = _poll(client, lambda b: (
            (b.get("counters") or {}).get("合格总数", 0) >= base_ok + 1))
        counters = body.get("counters") or {}
        assert counters.get("合格总数", 0) == base_ok + 1, f"命中行应判 OK: {counters}"
        assert counters.get("不良总数", 0) == base_ng, f"不该出 NG: {counters}"
        cv = body.get("combo_verdict") or {}
        assert cv.get("enabled") is True
        assert cv.get("last_tag") == "机型X", f"combo_verdict 透出错误: {cv}"
        # tag 经 reason 进事件记录
        events = body.get("events_log") or body.get("recent_events") or []
        joined = str(events)
        assert "机型X" in joined, f"事件 reason 应带机型 tag: {joined[:400]}"
    finally:
        _cleanup(client)


def test_e2e_combo_unmatched_is_ng(client):
    """区A×1 + 区B×1 = (1,1) 未命中任何行 → 防呆 NG。"""
    _start(client, _scenario(1, "combo_miss"), _project("__combo_miss__"))
    try:
        base_ng, base_ok = _baseline(client)
        body = _poll(client, lambda b: (
            (b.get("counters") or {}).get("不良总数", 0) >= base_ng + 1))
        counters = body.get("counters") or {}
        assert counters.get("不良总数", 0) == base_ng + 1, f"未命中组合应判 NG: {counters}"
        assert counters.get("合格总数", 0) == base_ok, f"不该出 OK: {counters}"
        cv = body.get("combo_verdict") or {}
        assert cv.get("last_tag") is None, f"未命中不该有 tag: {cv}"
    finally:
        _cleanup(client)


def test_e2e_combo_bypasses_accept_once(client):
    """UAT 挖出的真实路径: 前端在检测模式给全部步骤自动置 accept_once=true。
    combo 判型标签必须豁免 accept_once 周期内去重, 否则第二次出现被静默拦截,
    计数永远到不了 2 → 未命中防呆 NG (客户感知"配了表全 NG")。"""
    _start(client, _scenario(2, "combo_accept_once"),
           _project("__combo_ao__", accept_once=True))
    try:
        base_ng, base_ok = _baseline(client)
        body = _poll(client, lambda b: (
            (b.get("counters") or {}).get("合格总数", 0) >= base_ok + 1))
        counters = body.get("counters") or {}
        assert counters.get("合格总数", 0) == base_ok + 1, \
            f"accept_once 下 combo 标签仍应累计命中 OK: {counters}"
        assert (body.get("combo_verdict") or {}).get("last_tag") == "机型X"
    finally:
        _cleanup(client)


def test_e2e_no_combo_duplicate_still_ng(client):
    """零差异守门: 不配表时, 区A 出现 2 次在检测模式照旧 NG (首步重现结算/重复步骤)。"""
    _start(client, _scenario(2, "combo_off"),
           _project("__combo_off__", with_combo=False))
    try:
        base_ng, _ = _baseline(client)
        body = _poll(client, lambda b: (
            (b.get("counters") or {}).get("不良总数", 0) >= base_ng + 1))
        counters = body.get("counters") or {}
        assert counters.get("不良总数", 0) >= base_ng + 1, f"重复步骤应照旧 NG: {counters}"
        cv = body.get("combo_verdict") or {}
        assert cv.get("enabled") is False
    finally:
        _cleanup(client)


# ================= 6. v3.49 切步数量门 (ComboStepGuard) =================

def _guard_cfg(**kw):
    base = {
        "enabled": True, "check_under": True, "check_over": True,
        "narrow_by_progress": True, "action": "hint", "event_id": 3,
        "expected_source": "table", "plc_unavailable": "table",
        "plc_connection_id": None, "plc_point": "",
    }
    base.update(kw)
    return base


def _mk_guard(rows=None, plc_reader=None, **cfg_kw):
    from backend.api.source_combo_guard import ComboStepGuard
    labels = ["区A", "区B"]
    rows = rows or [
        {"counts": [2, 1], "verdict": "OK", "tag": "X", "plc_code": "4"},
        {"counts": [3, 1], "verdict": "OK", "tag": "Y", "plc_code": "6"},
        {"counts": [9, 9], "verdict": "NG", "tag": "坏", "plc_code": ""},  # NG 行不作目标
    ]
    return ComboStepGuard(labels, rows, _guard_cfg(**cfg_kw), plc_reader=plc_reader)


def test_parse_step_guard_default_off_and_options():
    """未启用 step_guard → None; 启用后归一化全字段 + 行级 plc_code。"""
    base = {"enabled": True, "labels": ["a", "b"],
            "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "X", "plc_code": "4"}]}
    assert _parse_combo_table(base)["step_guard"] is None
    cfg = _parse_combo_table({**base, "step_guard": {
        "enabled": True, "action": "instant_ng", "expected_source": "auto",
        "event_id": "3", "plc_connection_id": "7", "plc_point": " cyl_type ",
        "check_under": False, "plc_unavailable": "skip",
    }})
    sg = cfg["step_guard"]
    assert sg["enabled"] is True and sg["action"] == "instant_ng"
    assert sg["expected_source"] == "auto" and sg["event_id"] == 3
    assert sg["plc_connection_id"] == 7 and sg["plc_point"] == "cyl_type"
    assert sg["check_under"] is False and sg["check_over"] is True
    assert sg["narrow_by_progress"] is True and sg["plc_unavailable"] == "skip"
    assert cfg["rows"][0]["plc_code"] == "4"


def test_guard_transition_undercount_and_dedup():
    """区A=1 切到区B → transition; 同 (kind,label,actual) 只报一次。"""
    g = _mk_guard()
    assert g.tick({"区A": 1, "区B": 0}) == []
    v = g.tick({"区A": 1, "区B": 1})
    assert len(v) == 1 and v[0]["kind"] == "transition"
    assert v[0]["label"] == "区A" and v[0]["actual"] == 1
    assert v[0]["expected"] == [2, 3]
    assert "切步数量不符" in v[0]["message"]
    assert g.tick({"区A": 1, "区B": 1}) == []  # 去重


def test_guard_overcount_immediate():
    """区A 第 4 个立刻 over, 不等切步; 上限来自候选行 2/3。"""
    g = _mk_guard()
    assert g.tick({"区A": 3, "区B": 0}) == []
    v = g.tick({"区A": 4, "区B": 0})
    assert len(v) == 1 and v[0]["kind"] == "over"
    assert v[0]["actual"] == 4 and v[0]["expected"] == [2, 3]
    assert "超装" in v[0]["message"]


def test_guard_narrow_by_progress_tightens_over_limit():
    """座瓦装完 2 后收窄到 (2,1) 行, 盖瓦上限 1; 第 2 个盖瓦即超装。"""
    g = _mk_guard()
    assert g.tick({"区A": 2, "区B": 0}) == []
    assert g.tick({"区A": 2, "区B": 1}) == []  # 切步时区A=2 合法
    v = g.tick({"区A": 2, "区B": 2})
    assert len(v) == 1 and v[0]["kind"] == "over" and v[0]["label"] == "区B"
    assert v[0]["expected"] == [1]


def test_guard_plc_source_locks_candidate_row():
    """PLC 缸型=4 → 只认 (2,1) 行; 区A=3 超装 (纯视觉本可到 3)。"""
    g = _mk_guard(expected_source="plc", plc_reader=lambda: 4)
    assert g.tick({"区A": 2, "区B": 0}) == []
    v = g.tick({"区A": 3, "区B": 0})
    assert len(v) == 1 and v[0]["kind"] == "over" and v[0]["actual"] == 3
    assert "PLC缸型=4" in v[0]["message"]


def test_guard_plc_unavailable_skip_vs_table():
    """plc + skip: 读不到值本帧不查; plc + table: 退纯视觉仍可报超装。"""
    g_skip = _mk_guard(expected_source="plc", plc_unavailable="skip",
                       plc_reader=lambda: None)
    assert g_skip.tick({"区A": 9, "区B": 0}) == []
    g_fb = _mk_guard(expected_source="plc", plc_unavailable="table",
                     plc_reader=lambda: None)
    v = g_fb.tick({"区A": 4, "区B": 0})
    assert len(v) == 1 and v[0]["kind"] == "over"
    assert "判定表推断" in v[0]["message"]


def test_guard_plc_value_unregistered_explicit():
    """PLC 读到值但判定表没登记 (现场 cyl_type=11 vs 登记 4/6):
    行为与旧版一致 (table 档退推断 / skip 档跳过), 但消息必须明示未登记。"""
    g = _mk_guard(expected_source="plc", plc_unavailable="table",
                  plc_reader=lambda: 11)
    v = g.tick({"区A": 4, "区B": 0})
    assert len(v) == 1 and v[0]["kind"] == "over"
    assert "PLC缸型=11未登记" in v[0]["message"]
    assert g.last_plc_value == 11
    g_skip = _mk_guard(expected_source="plc", plc_unavailable="skip",
                       plc_reader=lambda: 11)
    assert g_skip.tick({"区A": 9, "区B": 0}) == []  # 未登记 + skip = 不查
    # 判定表全员没配 plc_code → 谈不上"未登记", 走普通推断口径
    rows_no_code = [{"counts": [2, 1], "verdict": "OK", "tag": "X", "plc_code": ""}]
    g_nc = _mk_guard(rows=rows_no_code, expected_source="plc",
                     plc_reader=lambda: 11)
    v = g_nc.tick({"区A": 4, "区B": 0})
    assert len(v) == 1 and "判定表推断" in v[0]["message"] \
        and "未登记" not in v[0]["message"]


def test_guard_switches_off_and_count_reset():
    """check_under/over 关 → 不报; 计数回退后去重账自动复位, 可再报。"""
    g = _mk_guard(check_under=False, check_over=False)
    assert g.tick({"区A": 1, "区B": 1}) == []
    assert g.tick({"区A": 9, "区B": 0}) == []
    g2 = _mk_guard()
    g2.tick({"区A": 4, "区B": 0})  # 报一次 over
    assert g2.tick({"区A": 4, "区B": 0}) == []
    g2.tick({"区A": 0, "区B": 0})  # 计数回退 → reset
    v = g2.tick({"区A": 4, "区B": 0})
    assert len(v) == 1 and v[0]["kind"] == "over"


def test_build_combo_step_guard_none_when_off():
    from backend.api.source_combo_guard import build_combo_step_guard
    assert build_combo_step_guard(None) is None
    tbl = _parse_combo_table({
        "enabled": True, "labels": ["a"],
        "rows": [{"counts": [1], "verdict": "OK"}],
        "step_guard": {"enabled": False},
    })
    assert tbl["step_guard"] is None
    assert build_combo_step_guard(tbl) is None
    tbl2 = _parse_combo_table({
        "enabled": True, "labels": ["a"],
        "rows": [{"counts": [1], "verdict": "OK"}],
        "step_guard": {"enabled": True, "action": "hint"},
    })
    assert build_combo_step_guard(tbl2) is not None


# ============ 7. v3.49 二期: 新配置解析 (全默认关 = 零差异) ============

def test_parse_phase2_new_keys_defaults():
    """二期新键缺省: check_order 关 / resolved 无事件 / 结算 NG 档 /
    大字卡关 / 无独立缸型点位 / 无按标签覆盖。"""
    base = {"enabled": True, "labels": ["a", "b"],
            "rows": [{"counts": [2, 1], "verdict": "OK"}]}
    cfg = _parse_combo_table({**base, "step_guard": {"enabled": True}})
    sg = cfg["step_guard"]
    assert sg["check_order"] is False
    assert sg["resolved_event_id"] is None
    assert sg["on_settle_mismatch"] == "ng"
    assert sg["hold_timeout_s"] == 120.0
    assert cfg["live_display"] is None
    assert cfg["plc_display"] is None
    assert cfg["tracking_per_label"] == {}


def test_parse_phase2_new_keys_values():
    """二期新键显式配置: 全部归一化 + clamp + 非法标签/空覆盖剔除。"""
    cfg = _parse_combo_table({
        "enabled": True, "labels": ["a", "b"], "count_mode": "positional",
        "rows": [{"counts": [2, 1], "verdict": "OK"}],
        "step_guard": {"enabled": True, "check_order": True,
                       "resolved_event_id": "5", "on_settle_mismatch": "hold",
                       "hold_timeout_s": "60"},
        "live_display": {"enabled": True, "size": "normal",
                         "show_plc_type": False, "position": "bottom"},
        "plc_display": {"connection_id": "3", "point": " cyl_type "},
        "tracking_per_label": {
            "a": {"min_consecutive": 1, "iou": "0.2", "ema_alpha": None},
            "b": {},                      # 空覆盖 → 不存
            "不存在": {"iou": 0.5},        # 非参与标签 → 剔除
        },
    })
    sg = cfg["step_guard"]
    assert sg["check_order"] is True
    assert sg["resolved_event_id"] == 5
    assert sg["on_settle_mismatch"] == "hold"
    assert sg["hold_timeout_s"] == 60.0
    assert cfg["live_display"] == {"enabled": True, "size": "normal",
                                   "show_plc_type": False, "position": "bottom"}
    assert cfg["plc_display"] == {"connection_id": 3, "point": "cyl_type"}
    assert cfg["tracking_per_label"] == {"a": {"min_consecutive": 1, "iou": 0.2}}


def test_parse_plc_display_requires_both_fields():
    """独立缸型点位: 连接或点位任一缺失 → None (回落 step_guard 点位)。"""
    base = {"enabled": True, "labels": ["a"],
            "rows": [{"counts": [1], "verdict": "OK"}]}
    assert _parse_combo_table({**base, "plc_display": {"connection_id": 3}})["plc_display"] is None
    assert _parse_combo_table({**base, "plc_display": {"point": "x"}})["plc_display"] is None


# ============ 8. v3.49 二期: positional 追踪参数按标签覆盖 ============

def test_positional_per_label_overrides():
    """a 覆盖 min_consecutive=1 首帧即计; b 用全局 3 帧确认。"""
    from backend.api.source_combo_positional import ComboPositionalCounter
    eng = ComboPositionalCounter(["a", "b"], min_consecutive=3,
                                 per_label={"a": {"min_consecutive": 1}})
    eng.feed([_det_at("a", 0.1, 0.1), _det_at("b", 0.6, 0.6)])
    assert eng.counts() == {"a": 1}
    eng.feed([_det_at("b", 0.6, 0.6)])
    assert eng.counts() == {"a": 1}
    eng.feed([_det_at("b", 0.6, 0.6)])
    assert eng.counts() == {"a": 1, "b": 1}
    # rois 的 need 按标签取: b 的候选进度用全局 3
    eng.feed([_det_at("b", 0.3, 0.3)])
    pend = [r for r in eng.rois() if r["state"] == "pending"]
    assert pend and pend[0]["label"] == "b" and pend[0]["need"] == 3


def test_positional_per_label_pending_ttl():
    """a 覆盖 pending_ttl=2 (快清), b 用全局 10 (慢清)。"""
    from backend.api.source_combo_positional import ComboPositionalCounter
    eng = ComboPositionalCounter(["a", "b"], min_consecutive=3, pending_ttl=10,
                                 per_label={"a": {"pending_ttl": 2}})
    eng.feed([_det_at("a", 0.1, 0.1), _det_at("b", 0.6, 0.6)])
    for _ in range(3):   # 断检 3 tick: a 候选过期, b 候选还在
        eng.feed([])
    labels = {r["label"] for r in eng.rois() if r["state"] == "pending"}
    assert labels == {"b"}


def test_build_positional_passes_per_label():
    from backend.api.source_combo_positional import build_combo_positional
    tbl = _parse_combo_table({
        "enabled": True, "labels": ["a"], "count_mode": "positional",
        "rows": [{"counts": [1], "verdict": "OK"}],
        "tracking_per_label": {"a": {"min_consecutive": 1}},
    })
    eng = build_combo_positional(tbl)
    assert eng.per_label == {"a": {"min_consecutive": 1}}
    eng.feed([_det_at("a", 0.1, 0.1)])
    assert eng.counts() == {"a": 1}


# ============ 9. v3.49 二期: 顺序检查 + 补齐消警 ============

def test_guard_check_order_default_off():
    """默认不开顺序检查: 区B 开始后区A 再加件不报乱序。"""
    g = _mk_guard()
    assert g.tick({"区A": 2, "区B": 0}) == []
    assert g.tick({"区A": 2, "区B": 1}) == []
    assert g.tick({"区A": 3, "区B": 1}) == []   # (3,1) 也是合法行, 无 over


def test_guard_check_order_fires_on_backtrack():
    """区B 已开始后区A 又新增计数 (无少装活跃) → order 违规; 去重只报一次。"""
    g = _mk_guard(check_order=True)
    assert g.tick({"区A": 2, "区B": 0}) == []
    assert g.tick({"区A": 2, "区B": 1}) == []
    v = g.tick({"区A": 3, "区B": 1})
    assert len(v) == 1 and v[0]["kind"] == "order" and v[0]["label"] == "区A"
    assert "乱序" in v[0]["message"] and "区B" in v[0]["message"]
    assert g.tick({"区A": 3, "区B": 1}) == []   # 去重


def test_guard_order_exempt_during_undercount_remediation():
    """少装违规活跃中回头补件 = 补救不算乱序; 补到允许值 → resolved 消警。"""
    g = _mk_guard(check_order=True)
    assert g.tick({"区A": 1, "区B": 0}) == []
    v = g.tick({"区A": 1, "区B": 1})
    assert len(v) == 1 and v[0]["kind"] == "transition"
    # 回头补件: 处于少装活跃 → 豁免乱序; 且补到 2 ∈ 允许值 → 消警入队
    assert g.tick({"区A": 2, "区B": 1}) == []
    res = g.pop_resolved()
    assert len(res) == 1
    assert res[0]["kind"] == "resolved" and res[0]["orig_kind"] == "transition"
    assert res[0]["label"] == "区A" and res[0]["actual"] == 2
    assert "已补齐" in res[0]["message"]
    assert g.pop_resolved() == []   # 取走即清
    # 消警之后再加件 → 不再是补救, 是乱序
    v = g.tick({"区A": 3, "区B": 1})
    assert len(v) == 1 and v[0]["kind"] == "order"


def test_guard_resolved_not_for_over_that_stays_over():
    """over 违规补不回来 (计数只增不减) → 不产生 resolved。"""
    g = _mk_guard()
    v = g.tick({"区A": 4, "区B": 0})
    assert len(v) == 1 and v[0]["kind"] == "over"
    g.tick({"区A": 5, "区B": 0})
    assert g.pop_resolved() == []


def test_guard_reset_clears_active_and_resolved():
    g = _mk_guard()
    g.tick({"区A": 1, "区B": 1})   # transition 进活跃池
    g.reset()
    assert g.tick({"区A": 2, "区B": 1}) == []
    assert g.pop_resolved() == []   # reset 后无幽灵消警


# ============ 10. v3.49 二期: 结算挂起等补 (on_settle_mismatch='hold') ============

class _HoldMgr:
    """挂起档单测桩: 只提供 SettlementMixin hold 方法用到的属性。"""
    channel_id = 0

    def __init__(self, combo, cycle=("区A",)):
        self._combo_table = combo
        self._combo_settle_hold = None
        self.current_cycle_steps = list(cycle)
        self._combo_positional = None
        self._combo_guard_last = None
        self.events = []
        self.settle_calls = 0

    def fire_external_event_response(self, event_id, reason,
                                     source="external", remind_only=False):
        """响应面通道 (不结算) — 提示/挂起提示/消警必须走这条。"""
        self.events.append((event_id, reason))
        return True

    def _trigger_event(self, event_id, reason):
        raise AssertionError(
            "提示/挂起提示/消警不得走结算通道 _trigger_event "
            "(会无条件 end_cycle, 2026-08-12 现场事故)")

    def _settle_detection_cycle(self):
        self.settle_calls += 1


def _hold_table(timeout=120, mismatch="hold", resolved_event_id=None):
    return _parse_combo_table({
        "enabled": True, "labels": ["区A", "区B"],
        "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "X"}],
        "step_guard": {"enabled": True, "event_id": 3,
                       "resolved_event_id": resolved_event_id,
                       "on_settle_mismatch": mismatch,
                       "hold_timeout_s": timeout},
    })


def test_settle_hold_enter_alarms_once():
    """首次进挂起: 记状态 + 触发提示事件; 再进不重复报警。"""
    mgr = _HoldMgr(_hold_table())
    assert SettlementMixin._combo_settle_hold_enter(mgr, "差1个") is True
    assert mgr._combo_settle_hold is not None
    assert len(mgr.events) == 1 and mgr.events[0][0] == 3
    assert "挂起" in mgr.events[0][1]
    assert SettlementMixin._combo_settle_hold_enter(mgr, "差1个") is True
    assert len(mgr.events) == 1


def test_settle_hold_default_ng_zero_diff():
    """默认 ng 档: enter 返回 False = 走现状查表防呆 NG。"""
    mgr = _HoldMgr(_hold_table(mismatch="ng"))
    assert SettlementMixin._combo_settle_hold_enter(mgr, "x") is False
    assert mgr._combo_settle_hold is None and mgr.events == []


def test_settle_hold_tick_resettles_on_match():
    """挂起中补齐到命中行 → tick 驱动重新结算; 未补齐不动。"""
    mgr = _HoldMgr(_hold_table())
    SettlementMixin._combo_settle_hold_enter(mgr, "差1个")
    mgr.current_cycle_steps = ["区A"]          # (1,0) 未命中
    SettlementMixin._combo_settle_hold_tick(mgr)
    assert mgr.settle_calls == 0
    mgr.current_cycle_steps = ["区A", "区A", "区B"]   # (2,1) 命中
    SettlementMixin._combo_settle_hold_tick(mgr)
    assert mgr.settle_calls == 1


def test_settle_hold_timeout_falls_to_ng():
    """挂起超时: tick 驱动再结算, enter 检出超时返回 False → NG 落账。"""
    mgr = _HoldMgr(_hold_table(timeout=0.01))
    SettlementMixin._combo_settle_hold_enter(mgr, "差1个")
    time.sleep(0.05)
    SettlementMixin._combo_settle_hold_tick(mgr)
    assert mgr.settle_calls == 1   # 超时驱动重结算
    assert SettlementMixin._combo_settle_hold_enter(mgr, "差1个") is False
    assert mgr._combo_settle_hold is None


def test_settle_hold_timeout_zero_means_unlimited():
    mgr = _HoldMgr(_hold_table(timeout=0))
    SettlementMixin._combo_settle_hold_enter(mgr, "差1个")
    time.sleep(0.02)
    mgr.current_cycle_steps = ["区A"]   # 未命中且不限时 → 一直挂
    SettlementMixin._combo_settle_hold_tick(mgr)
    assert mgr.settle_calls == 0
    assert SettlementMixin._combo_settle_hold_enter(mgr, "差1个") is True


def test_guard_system_ok_ng_event_rejected_at_parse():
    """2026-08-12 产品决策 (现场事故后收紧): 提示/已补齐事件禁止借用系统
    合格(1)/不合格(2) 结算事件 — parse 时洗成 None (仅横幅+日志)。
    背景: 现场配 提示事件=不良(NG) 曾因走错结算通道当场 NG 落账; 修通道后
    进一步把"借系统结算事件的面"整个焊死, 防污染 OK/NG 统计与主逻辑。"""
    combo = _parse_combo_table({
        "enabled": True, "labels": ["区A", "区B"],
        "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "X"}],
        "step_guard": {"enabled": True, "action": "hint",
                       "event_id": 2, "resolved_event_id": 1},
    })
    sg = combo["step_guard"]
    assert sg["event_id"] is None and sg["resolved_event_id"] is None
    # hint 触发: 只挂横幅走日志, 两条事件通道都不碰
    mgr = _HoldMgr(combo)
    SettlementMixin._fire_combo_guard_violation(
        mgr, {"kind": "transition", "label": "区A", "expected": [2],
              "actual": 1, "message": "[区A] 切步数量不符: 应 2 个, 实际 1 个"})
    assert mgr.events == []
    assert mgr._combo_guard_last["action"] == "hint"
    assert mgr.settle_calls == 0


def test_guard_hint_custom_event_fires_response_face_not_settlement():
    """提示档配自定义事件 (id>=3): 走响应面通道 (灯/蜂鸣/Toast/计数),
    绝不结算 (_HoldMgr 的结算通道 _trigger_event 被调即 raise)。"""
    combo = _parse_combo_table({
        "enabled": True, "labels": ["区A", "区B"],
        "rows": [{"counts": [2, 1], "verdict": "OK", "tag": "X"}],
        "step_guard": {"enabled": True, "action": "hint", "event_id": 3},
    })
    mgr = _HoldMgr(combo)
    SettlementMixin._fire_combo_guard_violation(
        mgr, {"kind": "transition", "label": "区A", "expected": [2],
              "actual": 1, "message": "[区A] 切步数量不符: 应 2 个, 实际 1 个"})
    assert len(mgr.events) == 1 and mgr.events[0][0] == 3
    assert "切步数量不符" in mgr.events[0][1]
    assert mgr._combo_guard_last["action"] == "hint"
    assert mgr.settle_calls == 0


def test_resolve_combo_guard_violation_swaps_banner_and_event():
    """消警收口: 横幅换成 resolved 快照 + 触发可配已补齐事件。"""
    mgr = _HoldMgr(_hold_table(resolved_event_id=5))
    mgr._combo_guard_last = {"kind": "transition", "label": "区A", "message": "少装"}
    SettlementMixin._resolve_combo_guard_violation(
        mgr, {"kind": "resolved", "orig_kind": "transition",
              "label": "区A", "actual": 2, "message": "[区A] 已补齐"})
    assert mgr._combo_guard_last["kind"] == "resolved"
    assert mgr._combo_guard_last["action"] == "resolved"
    assert mgr.events[-1][0] == 5 and "已补齐" in mgr.events[-1][1]
    # 默认 resolved_event_id=None: 只换横幅不触发事件
    mgr2 = _HoldMgr(_hold_table())
    mgr2._combo_guard_last = {"kind": "over", "label": "区B", "message": "超装"}
    SettlementMixin._resolve_combo_guard_violation(
        mgr2, {"kind": "resolved", "orig_kind": "over",
               "label": "区B", "actual": 1, "message": "[区B] 已补齐"})
    assert mgr2._combo_guard_last["kind"] == "resolved"
    assert mgr2.events == []


def test_clear_step_runtime_state_resets_positional_engine():
    """2026-08-24 现场恶性循环回归: 周期超时强制 NG (_force_timeout_ng) /
    停止-启动 / 切项目都走 _clear_step_runtime_state —— 位置计数引擎必须随之
    清池。此前只在正常结算路径 (_settle_*_cycle) 清, 超时强制结算后旧工件的
    残留计数会累加到新工件头上 (现场视频: 盖瓦 5→9 报乱序, 逐件恶化)。"""
    from backend.api.source import VideoSourceManager
    from backend.api.source_combo_positional import ComboPositionalCounter
    vsm = VideoSourceManager(channel_id=0)
    eng = ComboPositionalCounter(["盖瓦"], min_consecutive=1)
    eng.feed([_det_at("盖瓦", 0.1, 0.1)])
    assert eng.counts() == {"盖瓦": 1}
    vsm._combo_positional = eng
    vsm._clear_step_runtime_state()
    assert eng.counts() == {}, "强制结算/启停清场必须清位置计数池, 否则旧账压新件"
