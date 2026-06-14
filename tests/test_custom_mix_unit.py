"""单元测试: 自定义模式混合子状态机 (backend/api/source_custom_mix.py).

不起后端 (两条混合线都用真 VSM 实例驱动各自独立模式的真机械):
  - _PerItemMixEngine    真逐件引擎: 个体锁定/覆盖配对/持续帧/虚拟漏件, 周期主权外移
  - _TrackingMixEngine   真跟踪引擎: 唯一个体累积 / 动作计数 FSM / 周期主权外移
  - build_custom_mix     配置守门 (零差异路径)
  - compose_settle_event 结算裁决合成 (OK 降级 / NG 追加 / 自定义事件透传)
"""
from __future__ import annotations

from backend.api.source import VideoSourceManager
from backend.api.source_custom_mix import (
    CustomMixMachine,
    build_custom_mix,
    compose_settle_event,
)


# ==================== _PerItemMixEngine (真逐件个体状态机) ====================
_SLIDER_XS = (0.10, 0.30, 0.50)     # 个体中心 x = 0.15 / 0.35 / 0.55


def _slider_dets(n=3):
    return [{"label": "滑块", "confidence": 0.95,
             "x": _SLIDER_XS[i], "y": 0.60, "w": 0.10, "h": 0.10}
            for i in range(n)]


def _action_det(width):
    """打螺丝动作框: y 0.55-0.75; width=0.60 盖满 3 个中心, 0.30 只盖前 2 个."""
    return {"label": "打螺丝", "confidence": 0.95,
            "x": 0.05, "y": 0.55, "w": width, "h": 0.20}


def _make_per_item_vsm(*, expected_count=3, sustain_frames=3,
                       based_on="sequential"):
    """真 VSM + custom(基于顺序) + 混合逐件 + 原生配对行 (中心点判定)."""
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99420,
        "name": "混合逐件单测项目",
        "logic_mode": "custom",
        "pipeline_config": {"custom_based_on": based_on,
                            "custom_mixed_with": "per_item",
                            "custom_sequence_order": [{"step_id": "s1"}, {"step_id": "s2"}]},
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


def test_per_item_mix_full_coverage_ok():
    """个体锁定 3/3 + 动作盖满每个个体持续 N 帧 → 步骤完成 → OK."""
    vsm = _make_per_item_vsm()
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(3):
        m.feed(vsm, _slider_dets(3), 0.0)                       # 吸收锁定
    for _ in range(4):
        m.feed(vsm, _slider_dets(3) + [_action_det(0.60)], 0.1)  # 全覆盖 ≥3 帧
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons
    step = m._engine.steps[0]
    assert step.locked_count == 3 and step.covered_count() == 3 and step.completed


def test_per_item_mix_partial_coverage_ng():
    """动作只盖到 2/3 个体 → NG, 原因含未完成与缺件明细."""
    vsm = _make_per_item_vsm()
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(3):
        m.feed(vsm, _slider_dets(3), 0.0)
    for _ in range(4):
        m.feed(vsm, _slider_dets(3) + [_action_det(0.30)], 0.1)  # 只盖前 2 个
    ok, reasons = m.verdict()
    assert not ok
    assert any("未完成(2/3" in r for r in reasons), reasons
    detail = m._engine.last_ng_detail
    assert detail and detail["missing_total"] == 1
    assert len(detail["steps_failed"][0]["missing_item_ids"]) == 1


def test_per_item_mix_virtual_missing_ng():
    """虚拟漏件: 期望 3 件但画面只出现过 2 件 → 全盖满也 NG (真引擎语义)."""
    vsm = _make_per_item_vsm(expected_count=3)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(3):
        m.feed(vsm, _slider_dets(2), 0.0)
    for _ in range(4):
        m.feed(vsm, _slider_dets(2) + [_action_det(0.60)], 0.1)
    ok, reasons = m.verdict()
    assert not ok
    assert any("期望3件" in r for r in reasons), reasons


def test_per_item_mix_sustain_frames_gate():
    """覆盖持续帧不足 (1 帧 < sustain 3) → 不翻转 covered → NG."""
    vsm = _make_per_item_vsm(sustain_frames=3)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(3):
        m.feed(vsm, _slider_dets(3), 0.0)
    m.feed(vsm, _slider_dets(3) + [_action_det(0.60)], 0.1)   # 仅 1 帧
    m.feed(vsm, _slider_dets(3), 0.2)                          # 中断 → 归零
    ok, _ = m.verdict()
    assert not ok
    assert m._engine.steps[0].covered_count() == 0


def test_per_item_mix_absorb_caps_at_expected():
    """固定数量模式: 吸收封顶 expected_count, 误检幻影第 4 件不进个体表."""
    vsm = _make_per_item_vsm(expected_count=3)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    extra = {"label": "滑块", "confidence": 0.95,
             "x": 0.70, "y": 0.60, "w": 0.10, "h": 0.10}
    for _ in range(3):
        m.feed(vsm, _slider_dets(3) + [extra], 0.0)
    assert len(m._engine.steps[0].items) == 3


def test_per_item_mix_resets_on_new_cycle():
    """步骤侧开新周期 (uuid 变化) → 个体表清零重新锁定."""
    vsm = _make_per_item_vsm()
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(3):
        m.feed(vsm, _slider_dets(3) + [_action_det(0.60)], 0.0)
    assert len(m._engine.steps[0].items) == 3
    vsm.current_cycle_uuid = "c2"
    m.feed(vsm, [], 0.1)
    assert len(m._engine.steps[0].items) == 0
    assert not m._engine.steps[0].completed


def test_per_item_mix_never_touches_host_cycle():
    """周期主权外移: 逐件引擎不开周期/不写步骤侧周期状态."""
    vsm = _make_per_item_vsm()
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(5):
        m.feed(vsm, _slider_dets(3) + [_action_det(0.60)], 0.0)
    assert vsm.current_cycle_id is None
    assert vsm.current_cycle_steps == []


def test_per_item_mix_to_state_shape():
    """to_state 透出与独立模式 per_item_state.steps 同形快照 (前端面板复用)."""
    vsm = _make_per_item_vsm()
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(3):
        m.feed(vsm, _slider_dets(3), 0.0)
    state = m.to_state()
    assert state["mix_type"] == "per_item"
    item = state["items"][0]
    assert item["role"] == "pair" and item["expected_count"] == 3
    step = state["steps"][0]
    assert step["action_label"] == "打螺丝"
    assert step["display_total"] == 3 and len(step["items"]) == 3


def test_per_item_mix_shared_action_label_not_stripped():
    """动作标签同时是步骤行时不被剥离 (两边共享), 个体标签仍被独占消费."""
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99421, "name": "共享动作标签",
        "logic_mode": "custom",
        "pipeline_config": {"custom_based_on": "sequential",
                            "custom_mixed_with": "per_item",
                            "custom_sequence_order": [{"step_id": "s1"}]},
        "steps_config": [
            {"id": "s1", "label": "打螺丝", "enabled": True},   # 动作也是序列步骤
            {"id": "s3", "label": "打滑块", "enabled": True,
             "detect_role": "item",
             "per_item": {"item_label": "滑块", "action_label": "打螺丝",
                          "coverage_use_center": True, "sustain_frames": 1,
                          "expected_count": 1}},
        ],
        "events_config": [], "counters_config": [], "data_config": {},
    })
    m = vsm._custom_mix
    assert "滑块" in m.item_labels
    assert "打螺丝" not in m.item_labels    # 步骤行标签不剥离


# ==================== _TrackingMixEngine (真 VSM 驱动真跟踪机械) ====================
def _track_dets(ids, label="滑块"):
    return [{"label": label, "confidence": 0.95, "track_id": tid,
             "x": 0.05 + (tid % 10) * 0.09, "y": 0.55, "w": 0.08, "h": 0.08}
            for tid in ids]


def _make_tracking_vsm(*, expected_count=3, extra_item_rows=None,
                       based_on="sequential"):
    """真 VSM + custom(基于顺序) + 混合跟踪 + 滑块物品行 (原生跟踪词汇)."""
    steps = [
        {"id": "s1", "label": "贴标", "enabled": True},
        {"id": "s2", "label": "封箱", "enabled": True},
        {"id": "s3", "label": "滑块", "enabled": True,
         "detect_role": "item", "count_mode": "track",
         "expected_count": expected_count},
    ]
    if extra_item_rows:
        steps.extend(extra_item_rows)
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99410,
        "name": "混合跟踪单测项目",
        "logic_mode": "custom",
        "pipeline_config": {"custom_based_on": based_on,
                            "custom_mixed_with": "tracking",
                            "custom_sequence_order": [{"step_id": "s1"}, {"step_id": "s2"}]},
        "steps_config": steps,
        "events_config": [], "counters_config": [], "data_config": {},
    })
    assert vsm._custom_mix is not None and vsm._custom_mix.mix_type == "tracking"
    assert vsm._tracking_external_cycle is True
    return vsm


def test_tracking_mix_accumulates_unique_ids_ok():
    """跟踪计数 = 唯一个体累积: 2 个 + 后续新增 1 个 = 3 → OK."""
    vsm = _make_tracking_vsm(expected_count=3)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(3):
        m.feed(vsm, _track_dets([11, 12]), 0.0)
    for _ in range(3):
        m.feed(vsm, _track_dets([11, 12, 13]), 0.2)
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons
    assert vsm._tracking_class_counters.get("滑块") == 3


def test_tracking_mix_accumulation_survives_disappearance():
    """累积不减: 前一批离场后新个体 (不同位置) 继续累加.

    注: 真引擎的 re-ID 是位置门控的 — 同位置重现会被正确并回旧个体
    (见 test_tracking_mix_reid_merges_same_position), 这里新批次放在
    画面另一侧, 验证 "真新个体" 走累积。
    """
    vsm = _make_tracking_vsm(expected_count=4)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(3):
        m.feed(vsm, _track_dets([11, 12]), 0.0)
    for _ in range(5):
        m.feed(vsm, [], 0.1)            # 全部离场 (不应扣数)
    for _ in range(3):
        m.feed(vsm, _track_dets([15, 16]), 0.2)  # x≈0.50/0.59, 远离首批 0.14/0.23
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons
    assert vsm._tracking_class_counters.get("滑块") == 4


def test_tracking_mix_reid_merges_same_position():
    """真引擎 re-ID 语义保留: 同位置换 track_id 重现 = 同一个体, 不重复计数."""
    vsm = _make_tracking_vsm(expected_count=2)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(3):
        m.feed(vsm, _track_dets([11, 12]), 0.0)
    for _ in range(2):
        m.feed(vsm, [], 0.1)
    for _ in range(3):
        m.feed(vsm, _track_dets([21, 22]), 0.2)  # tid%10 相同 → 位置与首批重合
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons
    assert vsm._tracking_class_counters.get("滑块") == 2


def test_tracking_mix_insufficient_ng():
    vsm = _make_tracking_vsm(expected_count=3)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    m.feed(vsm, _track_dets([11, 12]), 0.0)
    ok, reasons = m.verdict()
    assert not ok
    assert any("数量不足 2/3" in r for r in reasons)


def test_tracking_mix_track_count_capped_at_expected():
    """真引擎语义: track 计数行达到期望数后, 多余检测复用空闲编号不超额计数
    (防模型分裂识别重复计数; 超数抑制的原生旋钮是 max_recognized)."""
    vsm = _make_tracking_vsm(expected_count=2)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    m.feed(vsm, _track_dets([11, 12, 13]), 0.0)
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons
    assert vsm._tracking_class_counters.get("滑块") == 2


def test_tracking_mix_event_count_can_exceed_ng():
    """动作计数行可以超次: 出现→消失次数 > 需要次数 → NG 超出期望."""
    vsm = _make_tracking_vsm(
        expected_count=0,
        extra_item_rows=[{
            "id": "s5", "label": "放油嘴包", "enabled": True,
            "detect_role": "item", "count_mode": "event",
            "event_required_count": 1,
            "event_min_visible_frames": 2, "event_gone_frames": 2,
        }])
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    bag = [{"label": "放油嘴包", "confidence": 0.95, "track_id": 9,
            "x": 0.6, "y": 0.4, "w": 0.15, "h": 0.15}]
    for _ in range(2):                      # 2 次完整 出现→消失
        for _ in range(3):
            m.feed(vsm, bag, 0.0)
        for _ in range(3):
            m.feed(vsm, [], 0.0)
    ok, reasons = m.verdict()
    assert not ok
    assert any("超出期望" in r for r in reasons)


def test_tracking_mix_ignores_dets_without_track_id():
    """无 track_id 的过渡帧不计数 (真机械只认 track_id >= 0)."""
    vsm = _make_tracking_vsm(expected_count=1)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    m.feed(vsm, [{"label": "滑块", "confidence": 0.95,
                  "x": 0.1, "y": 0.5, "w": 0.1, "h": 0.1}], 0.0)
    assert vsm._tracking_class_counters.get("滑块", 0) == 0
    m.feed(vsm, _track_dets([7]), 0.1)
    ok, _ = m.verdict()
    assert ok


def test_tracking_mix_event_count_mode():
    """动作计数行 (count_mode='event'): 出现→消失 4 次 = 需要次数 4 → OK.

    对应包装场景"托盘放入 4 次"这类动作语义 — 真 FSM 驱动。
    """
    vsm = _make_tracking_vsm(
        expected_count=0,
        extra_item_rows=[{
            "id": "s4", "label": "托盘", "enabled": True,
            "detect_role": "item", "count_mode": "event",
            "event_required_count": 4,
            "event_min_visible_frames": 2, "event_gone_frames": 2,
        }])
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    tray = [{"label": "托盘", "confidence": 0.95, "track_id": 5,
             "x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}]
    for _ in range(4):                      # 4 次 出现3帧→消失3帧
        for _ in range(3):
            m.feed(vsm, tray, 0.0)
        for _ in range(3):
            m.feed(vsm, [], 0.0)
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons
    assert vsm._event_counters.get("托盘") == 4


# ==================== 堆叠批层语义 (v3.19.x 上料位换盘场景) ====================
_GRID24 = [(0.10 + c * 0.12, 0.30 + r * 0.15) for r in range(4) for c in range(6)]


def _tray_dets(batch, n=24, drop=()):
    """一盘 n 个滑块, 固定网格位置 (模拟上料位: 每盘都在同一格位)."""
    dets = []
    for i, (x, y) in enumerate(_GRID24[:n]):
        if i in drop:
            continue
        dets.append({"label": "滑块", "confidence": 0.95,
                     "track_id": 1000 * batch + i,
                     "x": x, "y": y, "w": 0.08, "h": 0.08})
    return dets


def _make_stack_vsm(*, required=96, layer_min=24, layer_frames=3, reappear=0.3,
                    gate_only=False):
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99412, "name": "混合堆叠批层单测",
        "logic_mode": "custom",
        "pipeline_config": {"custom_based_on": "sequential",
                            "custom_mixed_with": "tracking",
                            "custom_sequence_order": [{"step_id": "s1"}]},
        "steps_config": [
            {"id": "s1", "label": "封箱", "enabled": True},
            {"id": "s3", "label": "滑块", "enabled": True,
             "detect_role": "item", "count_mode": "track",
             "stack_enabled": True,
             "stack_gate_only": gate_only,
             "stack_required_count": required,
             "stack_layer_min_count": layer_min,
             "stack_layer_min_frames": layer_frames,
             "stack_reappear_seconds": reappear},
        ],
        "events_config": [], "counters_config": [], "data_config": {},
    })
    vsm.current_cycle_uuid = "c1"
    vsm.fps_inference = 10
    return vsm


def _feed_batches(vsm, tray_counts, *, dwell_frames=60, gap_frames=10,
                  occlude_fn=None):
    """按 10fps 喂 4 盘节拍: 每盘 dwell_frames 帧在场, 盘间 gap_frames 帧空窗."""
    m = vsm._custom_mix
    t = 0.0
    for batch, n in enumerate(tray_counts, start=1):
        for f in range(dwell_frames):
            drop = occlude_fn(batch, f) if occlude_fn else ()
            if drop is None:                 # None = 整帧全遮挡
                m.feed(vsm, [], t)
            else:
                m.feed(vsm, _tray_dets(batch, n, drop), t)
            t += 0.1
        for _ in range(gap_frames):
            m.feed(vsm, [], t)
            t += 0.1
    return m


def test_tracking_mix_stack_layers_full_ok():
    """上料位换盘回归: 4 盘 x 24 同格位, 批层闩锁累积 96 → OK.

    钉死的事实: 唯一 ID 累积在同格位场景下被丢失重识别合并 (track 计数停在 24),
    批层堆叠是该场景的正确原语 (max 合并后取 stack=96)。
    """
    vsm = _make_stack_vsm()
    m = _feed_batches(vsm, [24, 24, 24, 24])
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons
    assert vsm._stack_counters.get("滑块") == 96
    assert vsm._tracking_class_counters.get("滑块") == 24  # 同格位 re-ID 合并 (预期内)


def test_tracking_mix_stack_partial_batch_ng():
    """第 3 盘只有 23 个: 该批永不闩锁 → 总数 72 + 可解释 NG 原因 (23/24)."""
    vsm = _make_stack_vsm()
    m = _feed_batches(vsm, [24, 24, 23, 24])
    ok, reasons = m.verdict()
    assert not ok
    assert any("数量不足 72/96" in r and "23/24" in r for r in reasons)
    partials = vsm._stack_partials.get("滑块") or []
    assert partials and partials[0]["peak"] == 23


def test_tracking_mix_stack_occlusion_no_double_count():
    """盘内遮挡 (部分挡 + 短于重现阈值的整帧闪断) 不重复计数也不漏计."""
    def occlude(batch, f):
        if 20 <= f < 30:
            return (0, 1, 2)                # 1s 部分遮挡 3 个
        if 40 <= f < 42:
            return None                     # 0.2s 整帧闪断 (< reappear 0.3s)
        return ()

    vsm = _make_stack_vsm()
    m = _feed_batches(vsm, [24, 24, 24, 24], occlude_fn=occlude)
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons
    assert vsm._stack_counters.get("滑块") == 96


def test_tracking_mix_stack_legacy_semantics_unchanged():
    """不配批层字段 (默认 1/1) = 历史行为: 出现即计一层, 短闪断不重复计."""
    vsm = _make_stack_vsm(required=3, layer_min=1, layer_frames=1, reappear=0.3)
    m = vsm._custom_mix
    one = [{"label": "滑块", "confidence": 0.95, "track_id": 1,
            "x": 0.3, "y": 0.3, "w": 0.1, "h": 0.1}]
    t = 0.0
    for layer in range(3):
        for _ in range(5):
            m.feed(vsm, one, t)
            t += 0.1
        # 层内短闪断 (0.1s < 0.3s): 不应多计
        m.feed(vsm, [], t); t += 0.1
        for _ in range(3):
            m.feed(vsm, one, t)
            t += 0.1
        for _ in range(6):                  # 0.6s 空窗 → 下一层
            m.feed(vsm, [], t)
            t += 0.1
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons
    assert vsm._stack_counters.get("滑块") == 3


def test_tracking_mix_stack_resets_on_new_cycle():
    """步骤侧开新周期 → 批层状态 (计数/闩锁/不完整批次) 全部清零."""
    vsm = _make_stack_vsm()
    m = _feed_batches(vsm, [24, 23])
    assert vsm._stack_counters.get("滑块") == 24
    # 最后一批 23 个未闩锁: 裁决时以"在场未达标批"折入可解释明细
    ok, reasons = m.verdict()
    assert not ok and any("23/24" in r for r in reasons)
    vsm.current_cycle_uuid = "c2"
    m.feed(vsm, [], 999.0)
    assert vsm._stack_counters.get("滑块", 0) == 0
    assert not vsm._stack_partials.get("滑块")
    ok2, reasons2 = m.verdict()
    assert not ok2 and any("数量不足 0/96" in r for r in reasons2)
    assert not any("23/24" in r for r in reasons2)  # 旧批明细不得跨周期残留


# ==================== 满盘门 (gate_only): 验每盘是否满, 盘数辅助 ====================
def test_stack_gate_only_full_plates_ok():
    """满盘门: 每盘都数满 24 → OK (无短盘明细)."""
    vsm = _make_stack_vsm(gate_only=True)
    m = _feed_batches(vsm, [24, 24, 24, 24])
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons


def test_stack_gate_only_short_plate_ng():
    """满盘门: 任一盘短一个 (23) → NG, 原因只报"未数满的盘", 不报"数量不足总数"."""
    vsm = _make_stack_vsm(gate_only=True)
    m = _feed_batches(vsm, [24, 23, 24])
    ok, reasons = m.verdict()
    assert not ok
    assert any("未数满的盘" in r and "23/24" in r for r in reasons), reasons
    assert not any("数量不足" in r for r in reasons), reasons


def test_stack_gate_only_extra_plates_not_blocked():
    """满盘门: 盘数当辅助 — 多于期望(5盘>4盘)且全满 → 仍 OK (不卡"数量超出")."""
    vsm = _make_stack_vsm(gate_only=True)        # required=96 (=4盘), 喂 5 盘全满
    m = _feed_batches(vsm, [24, 24, 24, 24, 24])
    ok, reasons = m.verdict()
    assert ok and not reasons, reasons
    assert vsm._stack_counters.get("滑块") == 120   # 5×24 累计, 但不参与裁决


def test_stack_gate_only_live_short_plate_ng():
    """满盘门: 末盘在场未离场就结算, 短盘 (23) 以"在场未达标批"折入 NG."""
    vsm = _make_stack_vsm(gate_only=True)
    m = vsm._custom_mix
    t = 0.0
    for _ in range(30):                          # 第1盘满 24, 闩锁
        m.feed(vsm, _tray_dets(1, 24), t); t += 0.1
    for _ in range(6):                           # 0.6s 空窗 (> reappear 0.3s)
        m.feed(vsm, [], t); t += 0.1
    for _ in range(30):                          # 第2盘短 23, 仍在场即裁决
        m.feed(vsm, _tray_dets(2, 23), t); t += 0.1
    ok, reasons = m.verdict()
    assert not ok
    assert any("未数满的盘" in r and "23/24" in r for r in reasons), reasons


def test_tracking_mix_never_starts_cycle():
    """周期主权外移: 物品出现绝不触发真机械的自动开周期."""
    vsm = _make_tracking_vsm(expected_count=3)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(5):
        m.feed(vsm, _track_dets([11, 12, 13]), 0.0)
    assert vsm._tracking_cycle_active is False
    assert vsm.current_cycle_id is None


def test_tracking_mix_resets_on_new_cycle():
    """步骤侧开新周期 (uuid 变化) → 物品累积清零."""
    vsm = _make_tracking_vsm(expected_count=3)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    m.feed(vsm, _track_dets([11, 12, 13]), 0.0)
    assert vsm._tracking_class_counters.get("滑块") == 3
    vsm.current_cycle_uuid = "c2"
    m.feed(vsm, [], 0.1)
    assert vsm._tracking_class_counters.get("滑块", 0) == 0
    ok, reasons = m.verdict()
    assert not ok and any("数量不足 0/3" in r for r in reasons)


def test_tracking_mix_to_state_shape():
    vsm = _make_tracking_vsm(expected_count=3)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    m.feed(vsm, _track_dets([11, 12]), 0.0)
    state = m.to_state()
    assert state["mix_type"] == "tracking"
    item = next(i for i in state["items"] if i["label"] == "滑块")
    assert item["role"] == "track"
    assert item["expected_count"] == 3
    assert item["seen_count"] == 2
    assert state["checklist"]["滑块"]["counted"] == 2


def test_tracking_mix_compose_downgrades_ok():
    """步骤侧 OK + 物品不足 → 降级 NG + 原因合并 + 状态清零."""
    vsm = _make_tracking_vsm(expected_count=3)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    m.feed(vsm, _track_dets([11, 12]), 0.0)
    event_id, reason = compose_settle_event(vsm, 1, "顺序正确完成")
    assert event_id == 2
    assert "物品校验未通过" in reason and "数量不足 2/3" in reason
    assert vsm._tracking_class_counters.get("滑块", 0) == 0  # 合成即结算, 已清零


def test_standalone_tracking_unaffected_by_flag():
    """独立 tracking 项目: 外部周期标志恒为 False (行为零差异)."""
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99411, "name": "独立跟踪回归",
        "logic_mode": "tracking",
        "pipeline_config": {"tracking_cycle_strategy": "all_gone",
                            "counting_expected_items": {"滑块": 2}},
        "steps_config": [{"id": "s1", "label": "滑块", "enabled": True,
                          "count_mode": "track"}],
        "events_config": [], "counters_config": [], "data_config": {},
    })
    assert vsm._custom_mix is None
    assert vsm._tracking_external_cycle is False


# ==================== build_custom_mix 守门 ====================
def _base_config(logic_mode="custom", mixed_with="per_item", with_item=True):
    steps = [
        {"id": "s1", "label": "贴标", "enabled": True},
        {"id": "s2", "label": "封箱", "enabled": True},
    ]
    if with_item:
        if mixed_with == "tracking":
            steps.append({"id": "s3", "label": "滑块", "enabled": True,
                          "detect_role": "item", "count_mode": "track",
                          "expected_count": 3})
        else:
            steps.append({"id": "s3", "label": "打滑块", "enabled": True,
                          "detect_role": "item",
                          "per_item": {"item_label": "滑块", "action_label": "打螺丝",
                                       "coverage_use_center": True,
                                       "sustain_frames": 2, "expected_count": 3}})
    return {
        "logic_mode": logic_mode,
        "pipeline_config": {"custom_based_on": "sequential",
                            "custom_mixed_with": mixed_with},
        "steps_config": steps,
    }


def test_build_none_for_non_custom():
    cfg = _base_config(logic_mode="sequential")
    assert build_custom_mix(cfg) is None


def test_build_none_without_mix():
    cfg = _base_config(mixed_with=None)
    assert build_custom_mix(cfg) is None
    cfg = _base_config(mixed_with="bogus")
    assert build_custom_mix(cfg) is None


def test_build_none_without_item_rows():
    cfg = _base_config(with_item=False)
    assert build_custom_mix(cfg) is None


def test_build_per_item_machine():
    m = build_custom_mix(_base_config())
    assert isinstance(m, CustomMixMachine)
    assert m.mix_type == "per_item"
    # 个体/动作标签 + 行标签 都被独占消费 (无同名步骤行)
    assert m.item_labels == frozenset({"打滑块", "滑块", "打螺丝"})
    step = m._engine.steps[0]
    assert step.item_label == ("滑块",)
    assert step.action_label == "打螺丝"
    assert (step.expected_count, step.sustain_frames, step.coverage_use_center) == (3, 2, True)


def test_build_per_item_skips_row_without_pairing():
    """缺 item_label/action_label 的物品行跳过 → 无有效行时混合不生效."""
    cfg = _base_config()
    cfg["steps_config"][2]["per_item"] = {"item_label": "滑块"}   # 缺 action_label
    assert build_custom_mix(cfg) is None


def test_build_tracking_machine():
    m = build_custom_mix(_base_config(mixed_with="tracking"))
    assert m.mix_type == "tracking"
    assert m._engine.expected_items == {"滑块": 3}
    assert m.item_labels == frozenset({"滑块"})


def test_build_tracking_legacy_mix_item_fallback():
    """旧存储 steps[i].mix_item.expected_count 兜底进真引擎期望清单."""
    cfg = _base_config(mixed_with="tracking")
    row = cfg["steps_config"][2]
    row.pop("expected_count")
    row["mix_item"] = {"expected_count": 5}
    m = build_custom_mix(cfg)
    assert m._engine.expected_items == {"滑块": 5}


def test_build_skips_disabled_item():
    cfg = _base_config()
    cfg["steps_config"][2]["enabled"] = False
    assert build_custom_mix(cfg) is None


# ==================== compose_settle_event (真 VSM, 逐件线) ====================
def _ready_vsm(ok: bool):
    """构建一个已喂数据的真 VSM: ok=True 全覆盖, False 个体一个没盖."""
    vsm = _make_per_item_vsm(sustain_frames=2)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    for _ in range(3):
        m.feed(vsm, _slider_dets(3), 0.0)
    if ok:
        for _ in range(3):
            m.feed(vsm, _slider_dets(3) + [_action_det(0.60)], 0.1)
    return vsm


def test_compose_passthrough_without_mix():
    vsm = VideoSourceManager(channel_id=0)
    vsm._custom_mix = None
    assert compose_settle_event(vsm, 1, "顺序正确完成") == (1, "顺序正确完成")


def test_compose_ok_plus_item_ok():
    vsm = _ready_vsm(ok=True)
    assert compose_settle_event(vsm, 1, "顺序正确完成") == (1, "顺序正确完成")


def test_compose_ok_downgraded_by_item_ng():
    vsm = _ready_vsm(ok=False)
    event_id, reason = compose_settle_event(vsm, 1, "顺序正确完成")
    assert event_id == 2
    assert "物品校验未通过" in reason and "未完成" in reason


def test_compose_ng_appends_item_reasons():
    vsm = _ready_vsm(ok=False)
    event_id, reason = compose_settle_event(vsm, 2, "周期不完整")
    assert event_id == 2
    assert reason.startswith("周期不完整") and "物品校验未通过" in reason


def test_compose_custom_event_untouched():
    """自定义事件 (id 非 1/2) 好坏语义不可推断 → 不改判."""
    vsm = _ready_vsm(ok=False)
    assert compose_settle_event(vsm, 5, "自定义条件匹配") == (5, "自定义条件匹配")


def test_compose_resets_machine():
    """合成即一次结算, 个体表应清零 (下个周期重新锁定)."""
    vsm = _ready_vsm(ok=True)
    compose_settle_event(vsm, 1, "ok")
    assert len(vsm._custom_mix._engine.steps[0].items) == 0
