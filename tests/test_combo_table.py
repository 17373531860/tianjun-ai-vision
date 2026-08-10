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
    assert cfg["rows"][0] == {"counts": [5, 4], "verdict": "OK", "tag": "4缸"}
    assert cfg["rows"][1] == {"counts": [6, 4], "verdict": "NG", "tag": "坏组合"}
    # 负数钳到 0, 未知 verdict 按 OK
    assert cfg["rows"][2] == {"counts": [0, 2], "verdict": "OK", "tag": ""}


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
