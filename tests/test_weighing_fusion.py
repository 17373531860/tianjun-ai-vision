"""v3.35 称重融合架构单测 (纯逻辑, 不连秤不连主程序)。

覆盖三块新增能力:
  1. StepGate 步骤外设门控 — 视觉 SOP 步骤的秤条件放行 (tare / weight_judge / 拦截等纠正)
  2. VisualGuardMatcher 视觉料源防错 — 动作 × 固定区域 → 料别映射 / 限区违规
  3. context_expired 前置选择有效期 — never / hours / daily / shift 四种策略
另验 merge_config 对嵌套默认值 (context_expiry / visual_guard) 的深合并零迁移。
"""
import datetime as dt
import time

from backend.services.weighing_engine import (
    StepGate, VisualGuardMatcher, WeighingStation,
    context_expired, merge_config, point_in_polygon,
)


def _wcfg(**over):
    base = {
        "materials": ["钢帽水泥", "钢脚水泥"],
        "models": {
            "型号A": {
                "钢帽水泥": {"standard": 0.500, "low_tol": 0.020, "high_tol": 0.020},
                "钢脚水泥": {"standard": 0.300, "low_tol": 0.020, "high_tol": 0.020},
            }
        },
        "tare_trigger_weight": 0.05,
        "stable_tol": 0.003,
        "stable_min_samples": 3,
        "measure_min_weight": 0.005,
    }
    base.update(over)
    return merge_config(base)


def _feed_gate(gate, st, weight, cfg, n=4, ts0=None):
    evs = []
    ts0 = ts0 if ts0 is not None else time.time()
    for i in range(n):
        evs += gate.on_weight(weight, ts0 + i * 0.1, cfg, st)
    return evs


def _actions(events):
    return [e.get("action") for e in events]


def _kinds(events):
    return [e.get("kind") for e in events if e.get("action") == "alarm"]


def _station(model="型号A"):
    st = WeighingStation(0, weight_device_id=9)
    st.set_context(operator="张三", model_name=model)
    return st


# ==================== 1. StepGate 步骤外设门控 ====================
def test_gate_tare_pass_on_stable_load():
    """去皮门控: 毛重超阈稳定 → 发去皮 + 放行 (对应"钢帽放秤"步骤)。"""
    cfg = _wcfg()
    gate = StepGate(0, "放钢帽", {"kind": "tare"}, weight_device_id=9)
    st = _station()
    events = _feed_gate(gate, st, 0.20, cfg)
    assert "send_tare" in _actions(events)
    assert "gate_passed" in _actions(events)
    assert gate.status == "passed"


def test_gate_tare_not_pass_below_trigger():
    """毛重没超阈值 (没放件) → 不放行。"""
    cfg = _wcfg()
    gate = StepGate(0, "放钢帽", {"kind": "tare"}, weight_device_id=9)
    events = _feed_gate(gate, _station(), 0.01, cfg)
    assert events == []
    assert gate.status == "armed"


def test_gate_weight_judge_ok_passes():
    """称重判定门控: 净重合格 → record + 放行, 无报警。"""
    cfg = _wcfg()
    gate = StepGate(0, "称重", {"kind": "weight_judge", "material": "钢帽水泥"},
                    weight_device_id=9)
    st = _station()
    events = _feed_gate(gate, st, 0.50, cfg)
    assert "record" in _actions(events)
    assert "gate_passed" in _actions(events)
    assert _kinds(events) == []
    rec = [e for e in events if e["action"] == "record"][0]["result"]
    assert rec["verdict"] == "ok" and rec["gate_label"] == "称重"


def test_gate_weight_judge_shortage_blocks_then_corrects():
    """缺料 + block=True → 报警拦截不放行; 工人补料后重新稳定 → 重判合格放行。"""
    cfg = _wcfg()
    gate = StepGate(0, "称重", {"kind": "weight_judge", "material": "钢帽水泥",
                                "block": True}, weight_device_id=9)
    st = _station()
    ev1 = _feed_gate(gate, st, 0.45, cfg)          # 0.45 < 0.48 缺料
    assert "shortage" in _kinds(ev1)
    assert gate.status == "armed"                   # 拦截等纠正
    ev1b = _feed_gate(gate, st, 0.45, cfg)          # 同一读数不重复判/刷报警
    assert _kinds(ev1b) == []
    ev2 = _feed_gate(gate, st, 0.50, cfg, ts0=time.time() + 10)  # 补料到位
    assert "gate_passed" in _actions(ev2)
    assert gate.status == "passed"


def test_gate_weight_judge_shortage_no_block_passes():
    """block=False → 记 NG 判定但放行 (客户只要报警不拦线)。"""
    cfg = _wcfg()
    gate = StepGate(0, "称重", {"kind": "weight_judge", "material": "钢帽水泥",
                                "block": False}, weight_device_id=9)
    events = _feed_gate(gate, _station(), 0.45, cfg)
    assert "shortage" in _kinds(events)
    assert "gate_passed" in _actions(events)
    assert gate.status == "passed"


def test_gate_weight_judge_needs_model():
    """没选型号 → precheck 报警 (节流) 且不放行。"""
    cfg = _wcfg()
    gate = StepGate(0, "称重", {"kind": "weight_judge", "material": "钢帽水泥"})
    st = WeighingStation(0)   # 未选型号
    events = _feed_gate(gate, st, 0.50, cfg)
    assert _kinds(events) == ["precheck"]           # 5s 节流只报一次
    assert gate.status == "armed"


def test_gate_weight_judge_visual_wrong_blocks():
    """视觉料别校验开着且识别到投错 → wrong 报警拦截。"""
    cfg = _wcfg(material_check="visual")
    gate = StepGate(0, "称重", {"kind": "weight_judge", "material": "钢帽水泥"})
    st = _station()
    st.set_material_label("钢脚水泥")
    events = _feed_gate(gate, st, 0.50, cfg)
    assert "wrong" in _kinds(events)
    assert gate.status == "armed"


def test_gate_unknown_kind_passes_never_blocks_line():
    """未知门控类型 → 直接放行, 绝不卡产线。"""
    cfg = _wcfg()
    gate = StepGate(0, "X", {"kind": "future_kind"})
    events = _feed_gate(gate, _station(), 0.0, cfg, n=1)
    assert "gate_passed" in _actions(events)
    assert gate.status == "passed"


# ==================== 2. VisualGuardMatcher 视觉料源防错 ====================
BIG_BASIN = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]     # 左半屏
SMALL_BASIN = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]   # 右半屏


def _det(label, cx, cy, w=0.1, h=0.1):
    return {"label": label, "x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h}


def _guard(rules, **over):
    cfg = {"enabled": True, "source": "region_action",
           "cooldown_sec": 5.0, "rules": rules}
    cfg.update(over)
    return VisualGuardMatcher(cfg)


def test_guard_map_hits_after_min_frames():
    """舀料动作命中大盆区域连续 3 帧 → 上报料别=钢帽水泥, 且只发一次。"""
    g = _guard([{"name": "钢帽料盆", "labels": ["舀料"], "polygon": BIG_BASIN,
                 "material": "钢帽水泥", "mode": "map", "min_frames": 3}])
    frame = [_det("舀料", 0.25, 0.5)]
    assert g.feed(frame) == []
    assert g.feed(frame) == []
    ev = g.feed(frame)
    assert ev == [{"action": "material_label", "material": "钢帽水泥", "rule": "钢帽料盆"}]
    assert g.feed(frame) == []          # 持续命中不重复发
    assert g.feed([]) == []             # 离场清零
    for _ in range(2):
        g.feed(frame)
    assert g.feed(frame) != []          # 再次攒满可再发


def test_guard_map_outside_region_no_hit():
    """舀料发生在区域外 → map 规则不命中。"""
    g = _guard([{"name": "钢帽料盆", "labels": ["舀料"], "polygon": BIG_BASIN,
                 "material": "钢帽水泥", "mode": "map", "min_frames": 1}])
    assert g.feed([_det("舀料", 0.75, 0.5)]) == []


def test_guard_restrict_alarm_with_cooldown():
    """限区规则: 动作出现在允许区域外 → 报警; 冷却期内不刷屏。"""
    g = _guard([{"name": "装杯只许小盆", "labels": ["装杯"], "polygon": SMALL_BASIN,
                 "mode": "restrict", "min_frames": 1}])
    t0 = 1000.0
    ev = g.feed([_det("装杯", 0.25, 0.5)], now=t0)          # 在大盆侧 → 违规
    assert _kinds(ev) == ["guard_restrict"]
    g.feed([], now=t0 + 1)                                   # 离场清零
    ev2 = g.feed([_det("装杯", 0.25, 0.5)], now=t0 + 2)     # 冷却 5s 内再违规
    assert ev2 == []
    g.feed([], now=t0 + 6)
    ev3 = g.feed([_det("装杯", 0.25, 0.5)], now=t0 + 7)     # 冷却过后可再报
    assert _kinds(ev3) == ["guard_restrict"]


def test_guard_restrict_inside_region_ok():
    g = _guard([{"name": "装杯只许小盆", "labels": ["装杯"], "polygon": SMALL_BASIN,
                 "mode": "restrict", "min_frames": 1}])
    assert g.feed([_det("装杯", 0.75, 0.5)]) == []


def test_guard_direct_label_maps_without_polygon():
    """direct_label 模式: 检测标签本身即料别, 不画区域。"""
    g = _guard([{"name": "钢帽桶", "labels": ["钢帽料桶"], "material": "钢帽水泥",
                 "mode": "map", "min_frames": 1}], source="direct_label")
    ev = g.feed([_det("钢帽料桶", 0.9, 0.9)])
    assert ev[0]["material"] == "钢帽水泥"


def test_guard_disabled_zero_output():
    g = VisualGuardMatcher({"enabled": False, "rules": [
        {"name": "x", "labels": ["舀料"], "mode": "map",
         "material": "钢帽水泥", "min_frames": 1}]})
    assert g.enabled is False
    assert g.feed([_det("舀料", 0.5, 0.5)]) == []


def test_point_in_polygon_basics():
    assert point_in_polygon(0.25, 0.5, BIG_BASIN) is True
    assert point_in_polygon(0.75, 0.5, BIG_BASIN) is False
    assert point_in_polygon(0.5, 0.5, None) is True     # 没画区域 = 不限


# ==================== 3. context_expired 前置选择有效期 ====================
def _ts(y, m, d, hh, mm=0):
    return dt.datetime(y, m, d, hh, mm).timestamp()


def test_expiry_never_default():
    assert context_expired({"mode": "never"}, _ts(2026, 7, 1, 8)) is False
    assert context_expired(None, _ts(2026, 7, 1, 8)) is False
    assert context_expired({"mode": "hours"}, None) is False   # 没选过 → 谈不上过期


def test_expiry_hours():
    cfg = {"mode": "hours", "hours": 8}
    set_at = _ts(2026, 7, 1, 8)
    assert context_expired(cfg, set_at, now_ts=_ts(2026, 7, 1, 15)) is False
    assert context_expired(cfg, set_at, now_ts=_ts(2026, 7, 1, 16)) is True


def test_expiry_daily_reset_time():
    """每天 08:00 失效: 昨天选的今天 8 点后就过期; 今天 8 点后选的当天有效。"""
    cfg = {"mode": "daily", "reset_time": "08:00"}
    yesterday = _ts(2026, 7, 1, 14)
    assert context_expired(cfg, yesterday, now_ts=_ts(2026, 7, 2, 9)) is True
    today = _ts(2026, 7, 2, 8, 30)
    assert context_expired(cfg, today, now_ts=_ts(2026, 7, 2, 15)) is False
    # 边界: 今天 07:00 现在 07:30 (都在今日界线前) → 界线取昨天 08:00, 未过期
    assert context_expired(cfg, _ts(2026, 7, 2, 7), now_ts=_ts(2026, 7, 2, 7, 30)) is False


def test_expiry_shift():
    """跨班次失效: 早班(08-16)选的, 中班(16-24)就过期; 同班次内不过期。"""
    cfg = {"mode": "shift", "shifts": [
        {"name": "早班", "start": "08:00", "end": "16:00"},
        {"name": "中班", "start": "16:00", "end": "24:00"},
    ]}
    morning = _ts(2026, 7, 1, 10)
    assert context_expired(cfg, morning, now_ts=_ts(2026, 7, 1, 14)) is False
    assert context_expired(cfg, morning, now_ts=_ts(2026, 7, 1, 17)) is True
    # 隔天同班次也算不同班 (锚含日期)
    assert context_expired(cfg, morning, now_ts=_ts(2026, 7, 2, 10)) is True


def test_expiry_shift_cross_midnight():
    """跨午夜夜班 (22:00-06:00): 23 点选的, 次日凌晨 3 点仍同班不过期。"""
    cfg = {"mode": "shift", "shifts": [
        {"name": "夜班", "start": "22:00", "end": "06:00"},
    ]}
    night = _ts(2026, 7, 1, 23)
    assert context_expired(cfg, night, now_ts=_ts(2026, 7, 2, 3)) is False
    assert context_expired(cfg, night, now_ts=_ts(2026, 7, 2, 23)) is True


# ==================== 4. merge_config 嵌套深合并零迁移 ====================
def test_merge_config_nested_partial():
    """老项目只配了部分子键 → 其余子键仍取默认, 不丢字段。"""
    cfg = merge_config({"context_expiry": {"mode": "daily"},
                        "visual_guard": {"enabled": True}})
    assert cfg["context_expiry"]["mode"] == "daily"
    assert cfg["context_expiry"]["reset_time"] == "08:00"      # 默认补齐
    assert cfg["visual_guard"]["enabled"] is True
    assert cfg["visual_guard"]["cooldown_sec"] == 5.0


def test_merge_config_v331_project_zero_diff():
    """v3.31 老称重项目 (没有任何 v3.35 新键) → 默认 scale 驱动 + 有效期永不失效。"""
    cfg = merge_config({"materials": ["钢帽水泥"], "models": {}})
    assert cfg["drive_mode"] == "scale"
    assert cfg["context_expiry"]["mode"] == "never"
    assert cfg["visual_guard"]["enabled"] is False
