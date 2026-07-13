"""同标签区域拆分（虚拟步骤）+ 工件就位提示 —— 纯函数层单元测试.

覆盖 backend/api/source_label_split.py:
  - 配置解析: 合法规则 / 非法多边形 / 重复 source_label / anchor 缺标定降级 / unmatched 校验
  - fixed 模式改写: 四象限命中、三档未命中策略 (drop/keep/map)
  - anchor 模式: 平移+缩放仿射、锚点保持窗口、锚点丢失回退未命中
  - display_name 重挂
  - 就位提示: 框内/框外/锚点消失
"""
from __future__ import annotations

from backend.api.source_label_split import (
    LabelSplitEngine,
    PlacementGuideState,
    parse_label_splits,
    parse_placement_guide,
    _point_in_polygon,
)


# 四象限模板 (归一化坐标, 每块 0.5x0.5)
Q1 = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]  # 左上
Q2 = [[0.5, 0.0], [1.0, 0.0], [1.0, 0.5], [0.5, 0.5]]  # 右上
Q3 = [[0.0, 0.5], [0.5, 0.5], [0.5, 1.0], [0.0, 1.0]]  # 左下
Q4 = [[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]]  # 右下


def _rule(**overrides):
    base = {
        "id": "ls_1",
        "enabled": True,
        "source_label": "打螺丝",
        "mode": "fixed",
        "unmatched": "drop",
        "regions": [
            {"name": "螺丝1", "polygon": Q1},
            {"name": "螺丝2", "polygon": Q2},
            {"name": "螺丝3", "polygon": Q3},
            {"name": "螺丝4", "polygon": Q4},
        ],
    }
    base.update(overrides)
    return base


def _det(label="打螺丝", cx=0.25, cy=0.25, w=0.1, h=0.1, conf=0.9):
    return {"label": label, "confidence": conf,
            "x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h}


# ============================================================
# 解析
# ============================================================

def test_parse_valid_rule():
    rules = parse_label_splits({"label_splits": [_rule()]})
    assert len(rules) == 1
    r = rules[0]
    assert r.source_label == "打螺丝"
    assert [n for n, _ in r.regions] == ["螺丝1", "螺丝2", "螺丝3", "螺丝4"]
    assert r.mode == "fixed" and r.unmatched == "drop"


def test_parse_skips_disabled_and_invalid():
    rules = parse_label_splits({"label_splits": [
        _rule(enabled=False),
        _rule(source_label=""),                              # 缺原始标签
        _rule(regions=[{"name": "坏", "polygon": [[0, 0]]}]),  # 多边形 <3 点
        _rule(regions=[]),                                    # 无区域
        _rule(),                                              # 唯一合法
    ]})
    assert len(rules) == 1


def test_parse_duplicate_source_label_keeps_first():
    rules = parse_label_splits({"label_splits": [
        _rule(id="a", regions=[{"name": "R1", "polygon": Q1}]),
        _rule(id="b", regions=[{"name": "R2", "polygon": Q2}]),
    ]})
    assert len(rules) == 1
    assert rules[0].rule_id == "a"


def test_parse_anchor_without_ref_downgrades_to_fixed():
    rules = parse_label_splits({"label_splits": [
        _rule(mode="anchor", anchor_label="前罩"),  # 缺 anchor_ref
    ]})
    assert rules[0].mode == "fixed"


def test_parse_unmatched_map_requires_label():
    rules = parse_label_splits({"label_splits": [_rule(unmatched="map")]})
    assert rules[0].unmatched == "drop"  # 缺 unmatched_label → 降级 drop
    rules = parse_label_splits({"label_splits": [
        _rule(unmatched="map", unmatched_label="位置外")]})
    assert rules[0].unmatched == "map"


def test_parse_empty_config():
    assert parse_label_splits({}) == []
    assert parse_label_splits({"label_splits": []}) == []
    assert parse_label_splits(None) == []


# ============================================================
# fixed 模式改写
# ============================================================

def _engine(**rule_overrides):
    rules = parse_label_splits({"label_splits": [_rule(**rule_overrides)]})
    return LabelSplitEngine(rules)


def test_fixed_four_quadrants_rewrite():
    eng = _engine()
    dets = [
        _det(cx=0.25, cy=0.25),
        _det(cx=0.75, cy=0.25),
        _det(cx=0.25, cy=0.75),
        _det(cx=0.75, cy=0.75),
    ]
    out = eng.apply(dets, now=100.0)
    assert [d["label"] for d in out] == ["螺丝1", "螺丝2", "螺丝3", "螺丝4"]
    # 原始标签保留在 split_from 里 (导出/调试可溯源)
    assert all(d["split_from"] == "打螺丝" for d in out)
    # 原 det dict 不被就地修改
    assert dets[0]["label"] == "打螺丝"


def test_fixed_other_labels_pass_through():
    eng = _engine()
    other = {"label": "拿力矩", "confidence": 0.8, "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}
    out = eng.apply([other], now=100.0)
    assert out == [other]


def test_unmatched_drop():
    # 区域收缩到四个角落小块, 中心点命不中 → drop
    small = [{"name": "螺丝1", "polygon": [[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1]]}]
    eng = _engine(regions=small, unmatched="drop")
    out = eng.apply([_det(cx=0.5, cy=0.5)], now=100.0)
    assert out == []


def test_unmatched_keep():
    small = [{"name": "螺丝1", "polygon": [[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1]]}]
    eng = _engine(regions=small, unmatched="keep")
    out = eng.apply([_det(cx=0.5, cy=0.5)], now=100.0)
    assert len(out) == 1 and out[0]["label"] == "打螺丝"


def test_unmatched_map():
    small = [{"name": "螺丝1", "polygon": [[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1]]}]
    eng = _engine(regions=small, unmatched="map", unmatched_label="位置外打螺丝")
    out = eng.apply([_det(cx=0.5, cy=0.5)], now=100.0)
    assert len(out) == 1 and out[0]["label"] == "位置外打螺丝"
    assert out[0]["split_from"] == "打螺丝"


def test_display_name_rewrite():
    rules = parse_label_splits({"label_splits": [_rule()]})
    eng = LabelSplitEngine(rules, display_names={"螺丝1": "1号螺丝(左上)"})
    det = _det(cx=0.25, cy=0.25)
    det["display_name"] = "打螺丝的显示名"  # runner 按原始标签注入的旧显示名
    out = eng.apply([det], now=100.0)
    assert out[0]["display_name"] == "1号螺丝(左上)"
    out2 = eng.apply([_det(cx=0.75, cy=0.25)], now=100.0)
    assert "display_name" not in out2[0]  # 无配置显示名 → 不残留旧值


# ============================================================
# anchor 模式
# ============================================================

def _anchor_engine(**overrides):
    kw = dict(
        mode="anchor", anchor_label="前罩",
        anchor_ref={"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5},
        anchor_hold_seconds=3.0,
        # 标定坐标系下: 区域 = 标定锚点框的左上小块
        regions=[{"name": "螺丝1",
                  "polygon": [[0.25, 0.25], [0.5, 0.25], [0.5, 0.5], [0.25, 0.5]]}],
        unmatched="drop",
    )
    kw.update(overrides)
    return _engine(**kw)


def _anchor_det(cx, cy, w=0.5, h=0.5):
    return {"label": "前罩", "confidence": 0.95,
            "x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h}


def test_anchor_translation():
    eng = _anchor_engine()
    # 锚点整体右移 0.2 (同尺寸): 区域应跟着右移
    anchor = _anchor_det(cx=0.7, cy=0.5)  # ref 中心 0.5,0.5 → 右移 0.2
    hit = _det(cx=0.575, cy=0.375)        # 标定区域中心 (0.375,0.375) 右移 0.2
    miss = _det(cx=0.375, cy=0.375)       # 原始标定位置 → 已不在区域内
    out = eng.apply([anchor, hit, miss], now=100.0)
    labels = [d["label"] for d in out]
    assert "螺丝1" in labels          # 平移后的命中
    assert labels.count("打螺丝") == 0  # miss 被 drop, 不以原名保留
    assert "前罩" in labels           # 锚点自身原样透传


def test_anchor_scaling():
    eng = _anchor_engine()
    # 锚点放大 2 倍且平移: ref(0.25,0.25,0.5,0.5) → cur(0.0,0.0,1.0,1.0)
    anchor = {"label": "前罩", "confidence": 0.95, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    # 标定区域 [0.25,0.25]~[0.5,0.5] 映射到 [0.0,0.0]~[0.5,0.5]
    hit = _det(cx=0.25, cy=0.25)
    out = eng.apply([anchor, hit], now=100.0)
    assert [d["label"] for d in out if d["label"] != "前罩"] == ["螺丝1"]


def test_anchor_hold_window():
    eng = _anchor_engine()
    anchor = _anchor_det(cx=0.5, cy=0.5)  # 与标定一致
    hit = _det(cx=0.375, cy=0.375)
    # 第 1 帧: 锚点在场
    out = eng.apply([anchor, hit], now=100.0)
    assert any(d["label"] == "螺丝1" for d in out)
    # 第 2 帧: 锚点被手遮挡 (hold=3s 内) → 沿用最近锚点位置
    out = eng.apply([hit], now=102.0)
    assert any(d["label"] == "螺丝1" for d in out)
    # 第 3 帧: 超过 hold 窗口 → 锚点失效, unmatched=drop
    out = eng.apply([hit], now=104.0)
    assert out == []


def test_anchor_missing_from_start_unmatched_keep():
    eng = _anchor_engine(unmatched="keep")
    out = eng.apply([_det(cx=0.375, cy=0.375)], now=100.0)
    assert len(out) == 1 and out[0]["label"] == "打螺丝"


# ============================================================
# 就位提示
# ============================================================

GUIDE_POLY = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]


def _guide():
    cfg = parse_placement_guide({"placement_guide": {
        "enabled": True, "anchor_label": "前罩", "polygon": GUIDE_POLY, "mode": "hint",
    }})
    assert cfg is not None
    return PlacementGuideState(cfg)


def test_placement_guide_in_position():
    g = _guide()
    g.update([_anchor_det(cx=0.5, cy=0.5)], now=100.0)
    snap = g.snapshot(now=100.1)
    assert snap["in_position"] is True and snap["anchor_visible"] is True
    assert snap["mode"] == "hint"


def test_placement_guide_out_of_position():
    g = _guide()
    g.update([_anchor_det(cx=0.05, cy=0.05, w=0.08, h=0.08)], now=100.0)
    snap = g.snapshot(now=100.1)
    assert snap["in_position"] is False and snap["anchor_visible"] is True


def test_placement_guide_anchor_gone():
    g = _guide()
    g.update([_anchor_det(cx=0.5, cy=0.5)], now=100.0)
    g.update([], now=101.0)  # 锚点消失, 但在 HOLD 窗口内
    assert g.snapshot(now=101.0)["anchor_visible"] is True
    snap = g.snapshot(now=103.5)  # 超过 HOLD_SECONDS=2.0
    assert snap["anchor_visible"] is False and snap["in_position"] is False


def test_parse_placement_guide_disabled_or_invalid():
    assert parse_placement_guide({}) is None
    assert parse_placement_guide({"placement_guide": {"enabled": False}}) is None
    assert parse_placement_guide({"placement_guide": {
        "enabled": True, "anchor_label": "", "polygon": GUIDE_POLY}}) is None
    assert parse_placement_guide({"placement_guide": {
        "enabled": True, "anchor_label": "前罩", "polygon": [[0, 0]]}}) is None


# ============================================================
# 多轮次 (v3.32): 同一批区域按工序轮次映射不同虚拟步骤
# ============================================================

ROUNDS = {"enabled": True, "trigger_label": "盖罩", "count": 2,
          "prefixes": ["前罩", "后罩"], "trigger_gap_seconds": 3.0}


def _rounds_engine():
    rules = parse_label_splits({"label_splits": [_rule(rounds=dict(ROUNDS))]})
    assert rules and rules[0].rounds_enabled
    return LabelSplitEngine(rules)


def _cover_det(conf=0.95):
    return {"label": "盖罩", "confidence": conf, "x": 0.3, "y": 0.3, "w": 0.4, "h": 0.4}


def test_parse_rounds_valid():
    rules = parse_label_splits({"label_splits": [_rule(rounds=dict(ROUNDS))]})
    r = rules[0]
    assert r.round_count == 2
    assert r.round_trigger_label == "盖罩"
    assert r.round_prefixes == ["前罩", "后罩"]


def test_parse_rounds_invalid_falls_back_disabled():
    for bad in (
        {**ROUNDS, "trigger_label": ""},               # 缺切换标签
        {**ROUNDS, "count": 1},                        # 轮数 < 2
        {**ROUNDS, "prefixes": ["前罩"]},              # 前缀数量不齐
        {**ROUNDS, "prefixes": ["同", "同"]},          # 前缀重复
        {**ROUNDS, "enabled": False},                  # 未启用
    ):
        rules = parse_label_splits({"label_splits": [_rule(rounds=bad)]})
        assert rules and not rules[0].rounds_enabled, f"应视为未启用: {bad}"


def test_rounds_before_trigger_falls_back_round1():
    """切换标签还没出现过 → 按第 1 轮前缀兜底改写。"""
    eng = _rounds_engine()
    out = eng.apply([_det(cx=0.25, cy=0.25)], now=100.0, cycle_len=0)
    assert out[0]["label"] == "前罩螺丝1"


def test_rounds_trigger_advances_and_wraps():
    """盖罩每次重新出现轮次 +1, 满轮回绕: 1→2→1。"""
    eng = _rounds_engine()
    # 第一次盖罩出现 → 第1轮
    eng.apply([_cover_det()], now=100.0, cycle_len=0)
    out = eng.apply([_cover_det(), _det(cx=0.25, cy=0.25)], now=100.5, cycle_len=1)
    assert [d["label"] for d in out if d["label"] != "盖罩"] == ["前罩螺丝1"]
    # 盖罩离场 4s (> gap=3) 后重现 → 第2轮
    eng.apply([_cover_det()], now=105.0, cycle_len=4)
    out = eng.apply([_cover_det(), _det(cx=0.75, cy=0.75)], now=105.5, cycle_len=5)
    assert [d["label"] for d in out if d["label"] != "盖罩"] == ["后罩螺丝4"]
    # 再离场重现 → 回绕到第1轮
    eng.apply([_cover_det()], now=110.0, cycle_len=8)
    out = eng.apply([_cover_det(), _det(cx=0.25, cy=0.25)], now=110.5, cycle_len=9)
    assert [d["label"] for d in out if d["label"] != "盖罩"] == ["前罩螺丝1"]


def test_rounds_continuous_presence_no_advance():
    """盖罩持续在画面里(间隔 < gap)不会重复切轮。"""
    eng = _rounds_engine()
    for t in (100.0, 100.5, 101.0, 101.5, 102.0):
        eng.apply([_cover_det()], now=t, cycle_len=1)
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 1


def test_rounds_idle_reset_after_cycle_settled():
    """周期装载过步骤且已结算(cycle_len=0)、盖罩离场超过 gap → 轮次归零。"""
    eng = _rounds_engine()
    eng.apply([_cover_det()], now=100.0, cycle_len=0)
    eng.apply([_cover_det()], now=104.0, cycle_len=3)   # 离场后重现 → 第2轮
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 2
    eng.apply([_cover_det()], now=104.5, cycle_len=3)   # 第2轮内周期确实有步骤
    # 结算后空闲: 无盖罩 + cycle_len=0 + 超过 gap
    eng.apply([], now=110.0, cycle_len=0)
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 0
    # 下一工件盖罩出现 → 从第1轮重新开始
    eng.apply([_cover_det()], now=111.0, cycle_len=0)
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 1


def test_rounds_no_reset_before_first_step_enters_cycle():
    """开工空窗守门: 切轮后、首个步骤进周期前, 不因"标签离场+周期空"误归零。

    真实产线上盖罩是瞬时动作(盖完标签就消失), 第一颗螺丝要几秒后才进周期,
    这段"离场 > gap 且 cycle_len=0"的空窗不是工件下线(真实模型 UAT 踩过:
    轮次刚推到 1 就被打回 0, 后续前后罩全部错位)。
    """
    eng = _rounds_engine()
    eng.apply([_cover_det()], now=100.0, cycle_len=0)   # 盖罩 → 第1轮
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 1
    # 盖罩离场 5s(> gap=3), 周期还空着 —— 不许归零
    eng.apply([], now=105.0, cycle_len=0)
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 1
    # 首颗螺丝进周期后再结算+离场 → 才允许归零
    eng.apply([_det(cx=0.25, cy=0.25)], now=106.0, cycle_len=1)
    eng.apply([], now=112.0, cycle_len=0)
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 0


def test_rounds_no_reset_while_cycle_open():
    """周期未结算(cycle_len>0)时即使盖罩离场很久也不归零(工序中途遮挡不丢轮次)。"""
    eng = _rounds_engine()
    eng.apply([_cover_det()], now=100.0, cycle_len=0)
    eng.apply([], now=120.0, cycle_len=3)   # 盖罩离场 20s 但周期还开着
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 1


def test_rounds_unmatched_map_label_not_prefixed():
    """unmatched=map 的改写标签是跨轮次统一概念, 不挂轮次前缀。"""
    rules = parse_label_splits({"label_splits": [
        _rule(unmatched="map", unmatched_label="位置外打螺丝", rounds=dict(ROUNDS)),
    ]})
    eng = LabelSplitEngine(rules)
    out = eng.apply([_det(cx=1.5, cy=1.5)], now=100.0, cycle_len=0)  # 画面外 → 未命中
    assert out[0]["label"] == "位置外打螺丝"


def test_rounds_trigger_min_blocks_single_frame_blip():
    """trigger_min_seconds: 切换标签单帧闪现不切轮, 持续在场满时长才确认切换。

    真实模型实测: 视频里出现过 2 帧的假「盖罩」误检, 见帧即切会把轮次
    多推一拍导致前后罩全部错位。
    """
    rules = parse_label_splits({"label_splits": [
        _rule(rounds={**ROUNDS, "trigger_min_seconds": 0.5}),
    ]})
    assert rules[0].round_trigger_min == 0.5
    eng = LabelSplitEngine(rules)
    # 单帧闪现(下一帧即消失) → 不满 0.5s, 不切轮
    eng.apply([_cover_det()], now=100.0, cycle_len=1)
    eng.apply([], now=100.1, cycle_len=1)
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 0
    # 4s 后(> gap)重新出现并持续 0.6s → 确认切换到第1轮, 且同一次在场只切一次
    eng.apply([_cover_det()], now=104.5, cycle_len=1)
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 0   # 刚出现还没确认
    eng.apply([_cover_det()], now=105.1, cycle_len=1)
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 1   # 满 0.5s 确认
    eng.apply([_cover_det()], now=106.0, cycle_len=1)
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 1   # 不重复切


def test_rounds_trigger_min_default_zero_is_legacy():
    """缺省 trigger_min_seconds=0 → 见帧即切(零差异老行为)。"""
    rules = parse_label_splits({"label_splits": [_rule(rounds=dict(ROUNDS))]})
    assert rules[0].round_trigger_min == 0.0
    eng = LabelSplitEngine(rules)
    eng.apply([_cover_det()], now=100.0, cycle_len=1)
    assert eng.snapshot_rounds()["打螺丝"]["round"] == 1


def test_rounds_trigger_min_clamped_below_gap():
    """确认时长必须压在消失间隔以下, 否则同一次在场会先被判离场。"""
    rules = parse_label_splits({"label_splits": [
        _rule(rounds={**ROUNDS, "trigger_gap_seconds": 1.0,
                      "trigger_min_seconds": 5.0}),
    ]})
    assert rules[0].round_trigger_min <= 1.0 - 0.1 + 1e-9


def test_rounds_snapshot_shape():
    eng = _rounds_engine()
    snap = eng.snapshot_rounds()
    assert snap == {"打螺丝": {"round": 0, "count": 2, "prefix": "前罩",
                              "trigger_label": "盖罩"}}
    # 无多轮规则的引擎返回 None
    eng2 = LabelSplitEngine(parse_label_splits({"label_splits": [_rule()]}))
    assert eng2.snapshot_rounds() is None


# ============================================================
# 每轮独立区域 (region_overrides): 翻面后位置不重叠场景
# ============================================================

# 第2轮区域整体右移 0.25 (模拟翻面后位置偏移): 螺丝1 挪到 [0.25, 0]~[0.75, 0.5]
SHIFTED_Q1 = [[0.25, 0.0], [0.75, 0.0], [0.75, 0.5], [0.25, 0.5]]


def _rounds_with_override(**extra):
    rounds = dict(ROUNDS)
    rounds["region_overrides"] = {"2": [{"name": "螺丝1", "polygon": SHIFTED_Q1}]}
    rounds.update(extra)
    return rounds


def test_parse_round_region_overrides():
    rules = parse_label_splits({"label_splits": [_rule(rounds=_rounds_with_override())]})
    r = rules[0]
    assert 2 in r.round_regions
    assert r.round_regions[2][0][0] == "螺丝1"
    # 第1轮没配 override → 不在 dict 里 (运行时回退共享区域)
    assert 1 not in r.round_regions


def test_parse_round_region_overrides_invalid_dropped():
    """轮次越界 / 多边形非法的 override 丢弃, 不影响轮次本身。"""
    rules = parse_label_splits({"label_splits": [_rule(rounds=_rounds_with_override(
        region_overrides={
            "2": [{"name": "螺丝1", "polygon": SHIFTED_Q1}],
            "9": [{"name": "越界", "polygon": SHIFTED_Q1}],       # 轮次越界
            "1": [{"name": "坏", "polygon": [[0, 0]]}],           # 多边形不足3点
            "abc": [{"name": "键非法", "polygon": SHIFTED_Q1}],
        }))]})
    r = rules[0]
    assert r.rounds_enabled
    assert set(r.round_regions.keys()) == {2}


def test_round_override_used_in_its_round_only():
    """第1轮用共享区域, 切到第2轮后用独立区域判定。"""
    rules = parse_label_splits({"label_splits": [_rule(rounds=_rounds_with_override())]})
    eng = LabelSplitEngine(rules)
    # 第1轮 (盖罩首现): 共享区域 → (0.25,0.25) 命中 Q1
    eng.apply([_cover_det()], now=100.0, cycle_len=0)
    out = eng.apply([_det(cx=0.25, cy=0.25)], now=100.5, cycle_len=1)
    assert out[0]["label"] == "前罩螺丝1"
    # 盖罩离场 4s 后重现 → 第2轮, 用右移后的独立区域
    eng.apply([_cover_det()], now=105.0, cycle_len=4)
    # 老位置 (0.15,0.25) 不在 SHIFTED_Q1 里 → 未命中 (drop)
    out = eng.apply([_det(cx=0.15, cy=0.25)], now=105.5, cycle_len=5)
    assert out == []
    # 新位置 (0.5,0.25) 在 SHIFTED_Q1 里 → 命中并挂第2轮前缀
    out = eng.apply([_det(cx=0.5, cy=0.25)], now=106.0, cycle_len=5)
    assert out[0]["label"] == "后罩螺丝1"


def test_round_override_missing_round_falls_back_shared():
    """只配了第2轮 override, 轮次归零(未开始, 按第1轮兜底)时仍用共享区域。"""
    rules = parse_label_splits({"label_splits": [_rule(rounds=_rounds_with_override())]})
    eng = LabelSplitEngine(rules)
    out = eng.apply([_det(cx=0.25, cy=0.25)], now=100.0, cycle_len=0)
    assert out[0]["label"] == "前罩螺丝1"


def test_anchor_combined_with_rounds_and_override():
    """锚点跟随 × 多轮次 × 每轮独立区域三者叠加:
    先按当轮取区域(共享/独立), 再按锚点当前位置平移, 最后挂轮次前缀。"""
    anchor_ref = {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6}
    rules = parse_label_splits({"label_splits": [_rule(
        mode="anchor", anchor_label="前罩", anchor_ref=anchor_ref,
        rounds={**ROUNDS,
                "region_overrides": {"2": [{"name": "螺丝1", "polygon": SHIFTED_Q1}]}},
    )]})
    r = rules[0]
    assert r.mode == "anchor" and r.rounds_enabled and 2 in r.round_regions
    eng = LabelSplitEngine(rules)
    anchor = {"label": "前罩", "confidence": 0.97,
              "x": 0.3, "y": 0.25, "w": 0.6, "h": 0.6}   # 平移 (+0.1, +0.05)
    cover = _cover_det()
    # 第1轮: 共享 Q1 平移后中心 (0.35, 0.30) 命中 → 前罩螺丝1
    eng.apply([anchor, cover], now=100.0, cycle_len=0)
    out = eng.apply([anchor, cover, _det(cx=0.35, cy=0.30)], now=100.5, cycle_len=1)
    assert [d["label"] for d in out if d.get("split_from")] == ["前罩螺丝1"]
    # 盖罩离场 4s 重现 → 第2轮: 独立区域 SHIFTED_Q1 平移后 x∈[0.35,0.85]
    eng.apply([anchor, cover], now=105.0, cycle_len=4)
    out = eng.apply([anchor, cover, _det(cx=0.6, cy=0.30)], now=105.5, cycle_len=5)
    assert [d["label"] for d in out if d.get("split_from")] == ["后罩螺丝1"]
    # 第2轮打在平移后的共享 Q1 老位置 (0.30, 0.30) → 不在独立区域内 → drop
    out = eng.apply([anchor, cover, _det(cx=0.30, cy=0.30)], now=106.0, cycle_len=5)
    assert [d["label"] for d in out if d.get("split_from")] == []


# ============================================================
# 零差异回归 (v3.32 新选项全不选 → 老配置行为逐位不变)
# ============================================================

def _legacy_rule():
    """v3.32 之前落库的规则形态: 没有 rounds 段(以及一切新键)。"""
    return {
        "id": "ls_legacy", "enabled": True, "source_label": "打螺丝",
        "mode": "fixed", "unmatched": "drop",
        "regions": [
            {"name": "螺丝1", "polygon": Q1}, {"name": "螺丝2", "polygon": Q2},
            {"name": "螺丝3", "polygon": Q3}, {"name": "螺丝4", "polygon": Q4},
        ],
    }


def test_zero_diff_legacy_rule_parses_with_rounds_disabled():
    """老配置(无 rounds 键)解析后: 轮次/每轮区域全部处于关闭态。"""
    rules = parse_label_splits({"label_splits": [_legacy_rule()]})
    r = rules[0]
    assert r.round_count == 0
    assert r.rounds_enabled is False
    assert r.round_prefixes == []
    assert r.round_regions == {}


def test_zero_diff_legacy_rule_rewrite_identical():
    """老配置的改写结果与轮次功能加入前逐位一致: 不挂前缀、无轮次运行态。"""
    eng = LabelSplitEngine(parse_label_splits({"label_splits": [_legacy_rule()]}))
    # 新签名传 cycle_len 与不传, 结果必须一致 (调用方已改为传 cycle_len)
    out_new = eng.apply([_det(cx=0.25, cy=0.25), _det(cx=0.75, cy=0.75)],
                        now=100.0, cycle_len=0)
    out_old = eng.apply([_det(cx=0.25, cy=0.25), _det(cx=0.75, cy=0.75)],
                        now=100.0)
    assert [d["label"] for d in out_new] == ["螺丝1", "螺丝4"]
    assert [d["label"] for d in out_new] == [d["label"] for d in out_old]
    # 无多轮规则 → 运行态不透出 (前端不会画轮次角标)
    assert eng.snapshot_rounds() is None


def test_zero_diff_rounds_ignore_unknown_region_overrides_when_disabled():
    """rounds.enabled=False 时即使残留 region_overrides 也整段忽略。"""
    rules = parse_label_splits({"label_splits": [_rule(rounds={
        "enabled": False,
        "region_overrides": {"2": [{"name": "螺丝1", "polygon": SHIFTED_Q1}]},
    })]})
    r = rules[0]
    assert r.rounds_enabled is False and r.round_regions == {}
    eng = LabelSplitEngine(rules)
    out = eng.apply([_det(cx=0.25, cy=0.25)], now=100.0, cycle_len=0)
    assert out[0]["label"] == "螺丝1"          # 共享区域, 无前缀


def test_zero_diff_placement_guide_parse_ignores_display():
    """display 是纯前端显示策略: 后端解析结果与没有该键时完全一致。"""
    base = {"placement_guide": {
        "enabled": True, "anchor_label": "前罩", "polygon": GUIDE_POLY}}
    with_display = {"placement_guide": {
        "enabled": True, "anchor_label": "前罩", "polygon": GUIDE_POLY,
        "display": "hide_on_ready"}}
    assert parse_placement_guide(base) == parse_placement_guide(with_display)


# ============================================================
# 几何 helper
# ============================================================

def test_point_in_polygon_basic():
    poly = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert _point_in_polygon(0.5, 0.5, poly) is True
    assert _point_in_polygon(1.5, 0.5, poly) is False
    # 非凸多边形 (L 形)
    l_poly = [(0, 0), (1, 0), (1, 0.5), (0.5, 0.5), (0.5, 1), (0, 1)]
    assert _point_in_polygon(0.25, 0.75, l_poly) is True
    assert _point_in_polygon(0.75, 0.75, l_poly) is False
