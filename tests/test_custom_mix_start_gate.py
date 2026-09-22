"""单元测试: 混合逐件「开始判定」(v3.60.2, backend/api/source_custom_mix.py).

中捷独立逐件"稳定窗口才开周期"语义的混合版: 可选组合「稳定窗口 (位置数齐)」
与「开始标签 (逐标签闩锁)」两个条件, 通过前不锁账/不记覆盖/不产活动脉冲,
通过后闩锁到周期结束。两条件都不配 = 无门 (存量零差异)。

场景矩阵 (与 plan 对齐):
  默认关零差异 / 仅稳定窗口 / 仅开始标签(单标签) / 仅开始标签(四标签逐个闩锁)
  / 双条件 / 通过前误报"已完成"不记账 / 通过后标签消失不影响 / 未通过超时 NG
  原因 / 复位语义 / 独占消费 (start 标签进剥离集、不刷空闲活动) / conf 门槛
  / runner _get_enabled_labels 豁免
"""
from __future__ import annotations

from backend.api.source import VideoSourceManager


# ==================== 共用工装 ====================
_SLIDER_XS = (0.10, 0.30, 0.50)     # 个体中心 x = 0.15 / 0.35 / 0.55


def _slider_dets(n=3):
    return [{"label": "滑块", "confidence": 0.95,
             "x": _SLIDER_XS[i], "y": 0.60, "w": 0.10, "h": 0.10}
            for i in range(n)]


def _action_det(width=0.60):
    """打螺丝动作框: y 0.55-0.75; width=0.60 盖满 3 个中心."""
    return {"label": "打螺丝", "confidence": 0.95,
            "x": 0.05, "y": 0.55, "w": width, "h": 0.20}


def _start_det(label, conf=0.9):
    return {"label": label, "confidence": conf,
            "x": 0.70, "y": 0.10, "w": 0.15, "h": 0.15}


def _make_vsm(per_item_pipeline=None, *, expected_count=3, sustain_frames=3):
    """真 VSM + custom(基于顺序) + 混合逐件 + 可注入 pipeline.per_item 开始判定配置."""
    pipeline = {"custom_based_on": "sequential",
                "custom_mixed_with": "per_item",
                "custom_sequence_order": [{"step_id": "s1"}, {"step_id": "s2"}]}
    if per_item_pipeline:
        pipeline["per_item"] = per_item_pipeline
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99461,
        "name": "混合逐件开始判定单测",
        "logic_mode": "custom",
        "pipeline_config": pipeline,
        "steps_config": [
            {"id": "s1", "label": "贴标", "enabled": True},
            {"id": "s2", "label": "封箱", "enabled": True},
            {"id": "s3", "label": "打滑块", "enabled": True,
             "detect_role": "item",
             "per_item": {"item_label": "滑块", "action_label": "打螺丝",
                          "coverage_use_center": True,
                          "sustain_frames": sustain_frames,
                          "expected_count": expected_count}},
        ],
        "events_config": [], "counters_config": [], "data_config": {},
    })
    assert vsm._custom_mix is not None and vsm._custom_mix.mix_type == "per_item"
    return vsm


def _feed(vsm, dets, t=0.0, n=1):
    for _ in range(n):
        vsm._custom_mix.feed(vsm, dets, t)


# ==================== 默认关: 零差异 ====================

def test_default_off_zero_diff():
    """不配开始判定 → 无门恒 started, 第一帧就锁账 (与 v3.60 行为逐帧等价)."""
    vsm = _make_vsm()
    eng = vsm._custom_mix._engine
    assert eng.start_gate_enabled is False and eng.started is True
    vsm.current_cycle_uuid = "c1"
    _feed(vsm, _slider_dets(3), 0.0, n=3)
    assert len(eng.steps[0].items) == 3          # 立即锁定, 无门无差异
    _feed(vsm, _slider_dets(3) + [_action_det()], 0.1, n=4)
    ok, reasons = vsm._custom_mix.verdict()
    assert ok and not reasons, reasons
    # 未配置时 state 不带 start_gate 键 (前端零差异)
    assert "start_gate" not in vsm._custom_mix.to_state()


# ==================== 仅稳定窗口 ====================

def test_stability_only_blocks_until_count_stable():
    """位置数不齐连续帧不足 → 不锁账; 攒满窗口帧 → 通过并开始锁定."""
    vsm = _make_vsm({"start_by_stability": True, "stability_window_frames": 5})
    eng = vsm._custom_mix._engine
    assert eng.start_gate_enabled and not eng.started
    vsm.current_cycle_uuid = "c1"
    # 只出 1 件 (required = max(1, min(3, int(3*0.85)=2, 3)) = 2) → 永不达标
    _feed(vsm, _slider_dets(1), 0.0, n=10)
    assert not eng.started and len(eng.steps[0].items) == 0
    # 出 3 件但只给 4 帧 (< 窗口 5) → 仍未通过
    _feed(vsm, _slider_dets(3), 0.1, n=4)
    assert not eng.started
    # 第 5 帧 → 通过; 通过帧起正常锁账
    _feed(vsm, _slider_dets(3), 0.2, n=1)
    assert eng.started
    _feed(vsm, _slider_dets(3), 0.3, n=3)
    assert len(eng.steps[0].items) == 3


def test_stability_streak_resets_on_break():
    """稳定窗口中途断一帧 → 连续计数清零重攒 (防手挡半盘的脏帧)."""
    vsm = _make_vsm({"start_by_stability": True, "stability_window_frames": 5})
    eng = vsm._custom_mix._engine
    vsm.current_cycle_uuid = "c1"
    _feed(vsm, _slider_dets(3), 0.0, n=4)        # 攒 4 帧
    _feed(vsm, _slider_dets(1), 0.1, n=1)        # 断 (1 < required 2)
    assert eng._start_stable_streak == 0 and not eng.started
    _feed(vsm, _slider_dets(3), 0.2, n=5)        # 重攒满
    assert eng.started


# ==================== 仅开始标签 ====================

def test_start_label_single_sustain():
    """单开始标签: 连续帧不足不闩锁; 攒满 sustain 帧 → 通过."""
    vsm = _make_vsm({"start_labels": ["工件就位"], "start_sustain_frames": 3})
    eng = vsm._custom_mix._engine
    vsm.current_cycle_uuid = "c1"
    # 位置在场也不锁账 (门未过)
    _feed(vsm, _slider_dets(3) + [_start_det("工件就位")], 0.0, n=2)
    assert not eng.started and len(eng.steps[0].items) == 0
    _feed(vsm, _slider_dets(3), 0.1, n=1)        # 标签断 1 帧 → 计数清零
    assert eng._start_label_streaks.get("工件就位", 0) == 0
    _feed(vsm, _slider_dets(3) + [_start_det("工件就位")], 0.2, n=3)
    assert eng.started
    _feed(vsm, _slider_dets(3), 0.3, n=3)
    assert len(eng.steps[0].items) == 3


def test_start_labels_four_latch_independently():
    """四角标签各自独立闩锁: 不要求同帧齐, 全部到位过即通过."""
    corners = ["就位-左上", "就位-右上", "就位-左下", "就位-右下"]
    vsm = _make_vsm({"start_labels": corners, "start_sustain_frames": 2})
    eng = vsm._custom_mix._engine
    vsm.current_cycle_uuid = "c1"
    # 逐角出现 (每角单独连续 2 帧, 出现时其他角不在场)
    for i, c in enumerate(corners):
        _feed(vsm, [_start_det(c)], 0.1 * i, n=2)
        if i < 3:
            assert not eng.started
            assert c in eng._start_labels_latched   # 已闩锁的不回退
    assert eng.started


def test_start_conf_threshold():
    """低置信度检出不计入开始标签确认."""
    vsm = _make_vsm({"start_labels": ["工件就位"], "start_sustain_frames": 2,
                     "start_conf": 0.6})
    eng = vsm._custom_mix._engine
    vsm.current_cycle_uuid = "c1"
    _feed(vsm, [_start_det("工件就位", conf=0.4)], 0.0, n=5)
    assert not eng.started
    _feed(vsm, [_start_det("工件就位", conf=0.7)], 0.1, n=2)
    assert eng.started


# ==================== 双条件 ====================

def test_dual_condition_requires_both():
    """双条件: 只满足标签不通过, 稳定窗口补齐才通过."""
    vsm = _make_vsm({"start_by_stability": True, "stability_window_frames": 3,
                     "start_labels": ["工件就位"], "start_sustain_frames": 2})
    eng = vsm._custom_mix._engine
    vsm.current_cycle_uuid = "c1"
    _feed(vsm, [_start_det("工件就位")], 0.0, n=2)   # 标签闩锁, 位置不在场
    assert "工件就位" in eng._start_labels_latched and not eng.started
    _feed(vsm, _slider_dets(3), 0.1, n=3)            # 稳定窗口补齐
    assert eng.started


# ==================== 通过前不记账 / 通过后闩锁 ====================

def test_pre_start_fake_coverage_not_booked():
    """通过前模型误报动作框 (摆料期"已完成"假盖章) → 一分不记."""
    vsm = _make_vsm({"start_labels": ["工件就位"], "start_sustain_frames": 3},
                    sustain_frames=1)
    eng = vsm._custom_mix._engine
    vsm.current_cycle_uuid = "c1"
    _feed(vsm, _slider_dets(3) + [_action_det()], 0.0, n=10)   # 误报盖满全部
    assert len(eng.steps[0].items) == 0 and eng.steps[0].covered_count() == 0
    assert eng.last_progress_time == 0.0         # 不产活动脉冲
    ok, _ = vsm._custom_mix.verdict()
    assert not ok


def test_latched_after_start_label_disappears():
    """通过后开始标签消失/常驻均无影响 (一次性闩锁)."""
    vsm = _make_vsm({"start_labels": ["工件就位"], "start_sustain_frames": 2},
                    sustain_frames=2)
    eng = vsm._custom_mix._engine
    vsm.current_cycle_uuid = "c1"
    _feed(vsm, [_start_det("工件就位")], 0.0, n=2)
    assert eng.started
    # 标签消失, 正常锁账+覆盖 → OK
    _feed(vsm, _slider_dets(3), 0.1, n=3)
    _feed(vsm, _slider_dets(3) + [_action_det()], 0.2, n=3)
    ok, reasons = vsm._custom_mix.verdict()
    assert ok, reasons


# ==================== 未通过被结算: NG 原因 ====================

def test_verdict_ng_reason_lists_missing():
    """未通过即被结算 (空闲/周期超时) → 原因明示"逐件未开始"并列缺失条件."""
    vsm = _make_vsm({"start_by_stability": True, "stability_window_frames": 5,
                     "start_labels": ["就位-左上", "就位-右下"],
                     "start_sustain_frames": 2})
    vsm.current_cycle_uuid = "c1"
    _feed(vsm, [_start_det("就位-左上")], 0.0, n=2)   # 只闩一角
    ok, reasons = vsm._custom_mix.verdict()
    assert not ok and len(reasons) == 1
    assert "逐件未开始" in reasons[0]
    assert "稳定窗口" in reasons[0] and "就位-右下" in reasons[0]
    assert "就位-左上" not in reasons[0]              # 已闩锁的不列缺失
    detail = vsm._custom_mix._engine.last_ng_detail
    assert detail and "逐件未开始" in detail["reason_summary"]


# ==================== 复位语义 ====================

def test_reset_on_new_cycle():
    """新周期 (uuid 变化) → 闩锁复位重新判定; 位置仍在场则数帧内重新通过."""
    vsm = _make_vsm({"start_by_stability": True, "stability_window_frames": 3})
    eng = vsm._custom_mix._engine
    vsm.current_cycle_uuid = "c1"
    _feed(vsm, _slider_dets(3), 0.0, n=3)
    assert eng.started
    vsm.current_cycle_uuid = "c2"                # 步骤侧开新周期
    _feed(vsm, _slider_dets(3), 1.0, n=1)        # feed 内周期跟随 reset
    assert not eng.started and eng._start_stable_streak == 1
    _feed(vsm, _slider_dets(3), 1.1, n=2)        # 位置仍在场 → 数帧内重过
    assert eng.started


# ==================== 独占消费 ====================

def test_start_labels_in_strip_set():
    """开始标签进剥离集 (步骤侧不再消费), 启用步骤标签不受影响."""
    vsm = _make_vsm({"start_labels": ["工件就位"]})
    m = vsm._custom_mix
    assert "工件就位" in m.strip_labels
    assert "贴标" not in m.strip_labels and "封箱" not in m.strip_labels
    # 物品/动作标签照旧剥离
    assert "滑块" in m.strip_labels and "打螺丝" in m.strip_labels


def test_runner_enabled_labels_exempts_start_labels():
    """runner 标签过滤豁免: 开始标签不是步骤也不会在检测出口被丢弃."""
    vsm = _make_vsm({"start_labels": ["工件就位", "就位-右下"]})
    labels = vsm._get_enabled_labels()
    assert "工件就位" in labels and "就位-右下" in labels


# ==================== state 透出 ====================

def test_state_exposes_start_gate():
    """配置了条件 → state 带 start_gate {started, missing} (前端徽标数据源)."""
    vsm = _make_vsm({"start_by_stability": True, "stability_window_frames": 3,
                     "start_labels": ["工件就位"], "start_sustain_frames": 2})
    vsm.current_cycle_uuid = "c1"
    _feed(vsm, [], 0.0, n=1)
    st = vsm._custom_mix.to_state()
    gate = st.get("start_gate")
    assert gate and gate["started"] is False
    assert any("稳定窗口" in x for x in gate["missing"])
    assert "工件就位" in gate["missing"]
    # 通过后 missing 清空
    _feed(vsm, _slider_dets(3) + [_start_det("工件就位")], 0.1, n=3)
    gate = vsm._custom_mix.to_state()["start_gate"]
    assert gate["started"] is True and gate["missing"] == []
