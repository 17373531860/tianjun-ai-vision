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


# ==================== 托盘容器累加器 (_ContainerAccumulator) ====================
# 场景: 托盘=容器, 滑块=容器内物品, 每托盘 24, 一箱 4 托盘, FIFO 逐个装箱。
# 周期主权归封箱步骤 — 累加器只"记账", 封箱时整箱裁决。
from backend.api.source_custom_mix import _ContainerAccumulator


def _tray(x, w=0.45, y=0.0, h=1.0):
    return {"x": x, "y": y, "w": w, "h": h}


def _tray_items(n, x0, x1, label="滑块", y=0.5):
    """在 [x0,x1] 区间均匀放 n 个物品对象 (模拟 host._tracking_objects 值)."""
    objs = []
    span = max(1, n)
    for i in range(n):
        cx = x0 + (x1 - x0) * (i + 0.5) / span
        objs.append({"class_name": label,
                     "bbox": {"x": cx - 0.002, "y": y, "w": 0.004, "h": 0.004}})
    return objs


def test_container_single_tray_fill_and_record():
    """单托盘装满 24 → 当前数=24/峰值=24; 托盘消失够确认帧 → 记进已装清单."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=3)
    tray, items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    for _ in range(3):
        acc.update([tray], items, 0.0)
    assert acc._cur_counts.get("滑块") == 24
    assert acc._trays[acc._primary]["peak"]["滑块"] == 24
    for _ in range(3):           # 托盘进箱 (消失)
        acc.update([], [], 0.1)
    assert acc._done == [{"滑块": 24}]
    assert acc._primary is None


def test_container_fifo_primary_only_counts_current():
    """两托盘同框: 主托盘=最早出现的那个; 只数主托盘自己的滑块 (不混入第二盘)."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=2)
    t1, t1_items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    acc.update([t1], t1_items, 0.0)          # t1 先出现 → primary=t1
    p1 = acc._primary
    t2, t2_items = _tray(0.5), _tray_items(24, 0.5, 0.95)
    acc.update([t1, t2], t1_items + t2_items, 0.1)  # 两盘同框
    assert acc._primary == p1                # FIFO: 主托盘仍是 t1
    assert acc._cur_counts["滑块"] == 24      # 只数 t1 的 24, 没把 t2 的也算进来


def test_container_peak_survives_transient_drop():
    """托盘瞬时漏检 (gone < gone_frames): 主托盘不切、峰值不清零 (治"峰值闪 0")."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=30)
    t1, t1_items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    acc.update([t1], t1_items, 0.0)          # 峰值升到 24
    p1 = acc._primary
    assert acc._trays[p1]["peak"]["滑块"] == 24
    # 连续 5 帧托盘+物品全漏检 (远小于 gone_frames=30)
    for i in range(5):
        acc.update([], [], 0.1 + i * 0.01)
        assert acc._primary == p1            # 主托盘没切
        peak = acc._trays.get(acc._primary, {}).get("peak", {})
        assert peak.get("滑块") == 24         # 峰值保持 24, 没被清
    # to_state 下发的峰值也稳在 24
    st = acc.to_state({"滑块": "滑块"})
    assert st["current_tray_items"][0]["peak_count"] == 24


def test_container_dup_tray_box_dedup_no_shadow_identity():
    """v3.46 托盘重复框去重 (开关开): 同帧对同一盘输出两个高重叠框 (IoU≈0.9)
    不再另立影子身份 — 影子长期在位带跨盘旧峰值, 主位一释放就顶上污账."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=3,
                                dedup_trays=True)
    t_hi = {"x": 0.0, "y": 0.0, "w": 0.45, "h": 1.0, "confidence": 0.9}
    t_lo = {"x": 0.02, "y": 0.0, "w": 0.45, "h": 1.0, "confidence": 0.4}
    items = _tray_items(24, 0.0, 0.45)
    for _ in range(3):
        acc.update([t_hi, t_lo], items, 0.0)
    assert len(acc._trays) == 1              # 只有一个身份, 无影子
    assert acc._trays[acc._primary]["peak"]["滑块"] == 24
    # 保留的是置信度高的那个框 (bbox 取 t_hi 的坐标)
    assert acc._trays[acc._primary]["bbox"]["x"] == 0.0


def test_container_dup_tray_dedup_keeps_distinct_trays():
    """真实相邻两盘 (IoU≈0) 不受托盘去重影响, 照常各立身份."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=3,
                                dedup_trays=True)
    t1 = {"x": 0.0, "y": 0.0, "w": 0.45, "h": 1.0, "confidence": 0.9}
    t2 = {"x": 0.5, "y": 0.0, "w": 0.45, "h": 1.0, "confidence": 0.9}
    acc.update([t1, t2], [], 0.0)
    assert len(acc._trays) == 2


def test_container_dup_tray_dedup_without_confidence_backcompat():
    """老调用路径 tray_dets 不带 confidence: 去重仍生效 (保序留先到者), 不炸."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=3,
                                dedup_trays=True)
    acc.update([_tray(0.0), _tray(0.02)], _tray_items(24, 0.0, 0.45), 0.0)
    assert len(acc._trays) == 1


def test_container_dup_tray_dedup_off_by_default():
    """托盘去重默认关 = v3.45 老行为零差异: 重复框照样另立影子身份."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=3)
    t_hi = {"x": 0.0, "y": 0.0, "w": 0.45, "h": 1.0, "confidence": 0.9}
    t_lo = {"x": 0.02, "y": 0.0, "w": 0.45, "h": 1.0, "confidence": 0.4}
    acc.update([t_hi, t_lo], [], 0.0)
    assert len(acc._trays) == 2              # 老行为: 影子身份仍会产生


def test_container_item_dedup_can_be_disabled():
    """滑块去重可关 (dedup_items=False): 重复滑块框不再被滤, 回 v3.44 之前口径."""
    dup_items = [
        {"class_name": "滑块", "confidence": 0.9,
         "bbox": {"x": 0.10, "y": 0.5, "w": 0.05, "h": 0.05}},
        {"class_name": "滑块", "confidence": 0.5,       # 与上框 IoU≈0.8 的重复框
         "bbox": {"x": 0.105, "y": 0.5, "w": 0.05, "h": 0.05}},
    ]
    acc_on = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=3)
    acc_on.update([_tray(0.0)], dup_items, 0.0)
    assert acc_on._cur_counts.get("滑块") == 1           # 默认开: 去重后 1 个
    acc_off = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4,
                                    gone_frames=3, dedup_items=False)
    acc_off.update([_tray(0.0)], dup_items, 0.0)
    assert acc_off._cur_counts.get("滑块") == 2          # 关: 重复框照数


def _pointer_stuck_setup(**kw):
    """搭台: 仅动作确认模式, 旧身份带残数占指针后离场, 新身份 23 在位.

    返回 (acc, 旧指针tid). 复现 2026-07-30 现场 '大数字8/实时23' 的内部状态."""
    acc = _ContainerAccumulator(
        "托盘", {"滑块": 24}, box_count=4, gone_frames=20,
        confirm_by_frames=False, confirm_by_action=True, action_label="放托盘",
        action_min_frames=3, action_gone_frames=8, **kw)
    t_old = _tray(0.0)
    acc.update([t_old], _tray_items(8, 0.0, 0.45), 0.0)   # 旧身份数到 8
    old_tid = acc._primary
    assert acc._trays[old_tid]["peak"]["滑块"] == 8
    # 旧身份离场 (断检), 新身份在同帧区域外出现并数到 23
    t_new = _tray(0.5)
    for i in range(10):                                    # 超过动作消失帧 8
        acc.update([t_new], _tray_items(23, 0.5, 0.95), 0.1 + i * 0.03)
    return acc, old_tid


def test_container_yield_primary_hands_pointer_to_fuller_tray():
    """第二件·让位 (开): 指针身份离场超过动作消失帧且新身份账面更实 → 指针让位."""
    acc, old_tid = _pointer_stuck_setup(yield_primary=True)
    assert acc._primary != old_tid                        # 指针已让给 23 那盘
    assert acc._trays[acc._primary]["peak"]["滑块"] == 23


def test_container_yield_primary_off_keeps_stuck_pointer():
    """第二件·让位 (默认关): 指针停在残数旧身份上 = v3.45 老行为 (现场 8/23 怪相)."""
    acc, old_tid = _pointer_stuck_setup()
    assert acc._primary == old_tid                        # 老行为: 指针卡死
    assert acc._trays[old_tid]["peak"]["滑块"] == 8


def test_container_yield_primary_not_stolen_by_emptier_tray():
    """第二件·严格大于守门: 在途满盘(24)不被下一盘的半账(12)抢走指针."""
    acc = _ContainerAccumulator(
        "托盘", {"滑块": 24}, box_count=4, gone_frames=20,
        confirm_by_frames=False, confirm_by_action=True, action_label="放托盘",
        action_min_frames=3, action_gone_frames=8, yield_primary=True)
    t_full = _tray(0.0)
    acc.update([t_full], _tray_items(24, 0.0, 0.45), 0.0)
    full_tid = acc._primary
    t_half = _tray(0.5)
    for i in range(10):
        acc.update([t_half], _tray_items(12, 0.5, 0.95), 0.1 + i * 0.03)
    assert acc._primary == full_tid                       # 24 > 12, 不让


def _ghost_pointer_setup(gone_frames=5, **kw):
    """搭台: 仅动作确认模式 (现场配置) — 没有脉冲就永不结账, 空账幽灵占指针
    后离场, 老行为下清理豁免让它赖死 (2026-07-30 '大数字 0/实时 24' 现场)."""
    acc = _ContainerAccumulator(
        "托盘", {"滑块": 24}, box_count=4, gone_frames=gone_frames,
        confirm_by_frames=False, confirm_by_action=True, action_label="放托盘",
        action_min_frames=3, action_gone_frames=8, **kw)
    ghost = _tray(0.0)
    acc.update([ghost], [], 0.0)                          # 幽灵: 从没数到滑块
    ghost_tid = acc._primary
    t_real = _tray(0.5)
    for i in range(gone_frames * 4):                      # 幽灵离场远超确认帧
        acc.update([t_real], _tray_items(24, 0.5, 0.95), 0.1 + i * 0.03)
    return acc, ghost_tid


def test_container_purge_empty_primary_frees_pointer():
    """第一件·空账销掉 (开): 指针指着的空账身份离场满消失确认帧 → 清掉, 指针
    交给在位真盘 (治 '大数字长期 0、实时稳定 24')."""
    acc, ghost_tid = _ghost_pointer_setup(purge_empty_primary=True)
    assert ghost_tid not in acc._trays                    # 幽灵被销
    assert acc._primary is not None and acc._primary != ghost_tid
    assert acc._trays[acc._primary]["peak"]["滑块"] == 24


def test_container_purge_empty_primary_off_ghost_stays():
    """第一件 (默认关): 仅动作确认模式下空账幽灵占指针离场再久也不清 = 老行为."""
    acc, ghost_tid = _ghost_pointer_setup()
    assert acc._primary == ghost_tid                      # 老行为: 赖着不走
    st = acc.to_state({"滑块": "滑块"})
    assert st["current_tray_items"][0]["peak_count"] == 0  # 大数字 0/实时 24 怪相


def test_container_unified_book_source_three_numbers_same_tray():
    """第三件·三数同源 (开): 实时/峰值/预计进箱统一问结账候选, 不再两盘混排."""
    acc, old_tid = _pointer_stuck_setup(unified_book_source=True)
    # 老口径: 峰值问指针(8), 实时问在位盘(23) → 矛盾; 同源后指针还卡在旧身份
    # (让位没开), 三个数就统一问指针那张 — 自洽 (一致地反映指针, 不再混排)
    st = acc.to_state({"滑块": "滑块"})
    row = st["current_tray_items"][0]
    assert row["peak_count"] == 8
    assert row["current_count"] == 0                      # 指针盘已离场, 当帧 0
    assert row["book_preview"] == 8                       # 预计进箱=真会记的数


def test_container_unified_off_numbers_can_split():
    """第三件 (默认关): 峰值问指针(8)、实时退化问在位盘(23) = 现场混排怪相."""
    acc, old_tid = _pointer_stuck_setup()
    st = acc.to_state({"滑块": "滑块"})
    row = st["current_tray_items"][0]
    assert row["peak_count"] == 8
    assert row["current_count"] == 23                     # 两盘数字并排 (老行为)


def test_container_unified_verdict_folds_candidate_tray():
    """第三件·封箱凑数同源: 开让位+同源后, 凑数取真盘的账, 不再被残数骗."""
    acc = _ContainerAccumulator(
        "托盘", {"滑块": 24}, box_count=4, gone_frames=20,
        count_mode="items_total", item_target=96,
        confirm_by_frames=False, confirm_by_action=True, action_label="放托盘",
        action_min_frames=3, action_gone_frames=8,
        yield_primary=True, unified_book_source=True)
    acc._done = [{"滑块": 24}, {"滑块": 24}, {"滑块": 24}]  # 已进箱 72
    t_last = _tray(0.0)
    for _ in range(3):
        acc.update([t_last], _tray_items(24, 0.0, 0.45), 0.0)
    # 模拟脉冲在途 (末盘已放入箱, 待结账) → 凑数把在位/候选盘折进来
    acc._action_done_pending = True
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert ok, f"96/96 应判 OK, reasons={reasons}"


def test_container_fifo_switch_after_box():
    """主托盘进箱后, 画面里第二盘自动升级为新主托盘."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=2)
    t1, t1_items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    t2, t2_items = _tray(0.5), _tray_items(24, 0.5, 0.95)
    acc.update([t1], t1_items, 0.0)
    p1 = acc._primary
    acc.update([t1, t2], t1_items + t2_items, 0.1)
    for _ in range(2):                       # t1 端走进箱, 只剩 t2
        acc.update([t2], t2_items, 0.2)
    assert len(acc._done) == 1
    assert acc._primary is not None and acc._primary != p1


def _run_box(acc, counts, gone_frames=2):
    """依次装 len(counts) 盘, 每盘 counts[k] 个滑块, 逐个进箱."""
    for k, c in enumerate(counts):
        tray, items = _tray(0.0), _tray_items(c, 0.0, 0.45)
        for _ in range(gone_frames):
            acc.update([tray], items, float(k))
        for _ in range(gone_frames):
            acc.update([], [], float(k) + 0.5)


def test_container_verdict_box_ok():
    """4 盘各 24 → 整箱 OK."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=2)
    _run_box(acc, [24, 24, 24, 24])
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert ok, reasons
    assert len(acc._done) == 4


def test_container_verdict_insufficient_trays():
    """只装 3 盘就封箱 → NG (托盘数不足)."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=2)
    _run_box(acc, [24, 24, 24])
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert not ok
    assert any("托盘数不足" in r for r in reasons)


def test_container_verdict_one_tray_short():
    """某一盘只有 20 个 → NG, 原因点名第几盘不足."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=2)
    _run_box(acc, [24, 24, 20, 24])
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert not ok
    assert any("第3盘" in r and "不足" in r for r in reasons)


def test_container_verdict_folds_in_current_tray():
    """封箱时最后一盘还在主托盘位 (未 gone-confirm) → 也折进裁决, 凑够 4 盘 OK."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=3)
    _run_box(acc, [24, 24, 24], gone_frames=3)   # 前 3 盘已进箱
    tray, items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    for _ in range(2):                   # 第 4 盘装满但还没进箱
        acc.update([tray], items, 9.0)
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert ok, reasons


# ---- 总数模式 (items_total): 不计盘数, 累加进箱滑块总数, 整箱判正好 96 ----

def test_container_items_total_exact_ok():
    """4 盘各 24, 进箱总数 96 → OK."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=2,
                                count_mode="items_total", item_target=96)
    _run_box(acc, [24, 24, 24, 24])
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert ok, reasons


def test_container_items_total_short_ng():
    """进箱总数 95 (<96) → NG 不足."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=2,
                                count_mode="items_total", item_target=96)
    _run_box(acc, [24, 24, 24, 23])
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert not ok
    assert any("不足" in r and "95/96" in r for r in reasons)


def test_container_items_total_over_ng():
    """进箱总数 100 (>96) → NG 超出 (多装也算异常)."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=2,
                                count_mode="items_total", item_target=96)
    _run_box(acc, [24, 24, 24, 28])
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert not ok
    assert any("超出" in r and "100/96" in r for r in reasons)


def test_container_items_total_uneven_ok():
    """每盘不均 (30/20/26/20=96) 但总数正好 96 → OK (不卡每盘/盘数)."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=2,
                                count_mode="items_total", item_target=96)
    _run_box(acc, [30, 20, 26, 20])
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert ok, reasons


def test_container_items_total_folds_in_current():
    """封箱时最后一盘还在位 (未进箱), 也折进总数凑够 96 → OK."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=3,
                                count_mode="items_total", item_target=96)
    _run_box(acc, [24, 24, 24], gone_frames=3)   # 前 3 盘 = 72 已进箱
    tray, items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    for _ in range(2):                            # 第 4 盘 24 个在位未进箱
        acc.update([tray], items, 9.0)
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert ok, reasons


def test_container_items_total_next_tray_not_overcounted():
    """已装够 96 后下一箱第一盘已上桌在位: 不能把它算进本箱 (治现场 120/96 误判超出)."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=3,
                                count_mode="items_total", item_target=96)
    _run_box(acc, [24, 24, 24, 24], gone_frames=3)   # 4 盘进箱 = 96 已装够
    tray, items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    for _ in range(2):                               # 下一箱第 1 盘 24 个已上桌在位
        acc.update([tray], items, 9.0)
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert ok, reasons                               # 仍判 96 OK, 不是 120 超出


def test_container_items_total_to_state():
    """总数模式 to_state: 输出 count_mode + 已进箱滑块总数 + 整箱目标."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=2,
                                count_mode="items_total", item_target=96)
    _run_box(acc, [24, 24])                       # 2 盘进箱 = 48
    st = acc.to_state({"滑块": "滑块", "托盘": "托盘"})
    assert st["count_mode"] == "items_total"
    assert st["item_target"] == 96
    assert st["item_total_done"].get("滑块") == 48


def test_container_items_total_expected_blank_still_counts():
    """期望留空 (item_expected={}) + 总数模式: 滑块照样按总数计/判.
    锁死上银现场 bug — 此前每盘期望填 0 → item_expected 空 → 滑块完全不计、整箱永远 OK."""
    acc = _ContainerAccumulator("托盘", {}, box_count=0, gone_frames=2,
                                count_mode="items_total", item_target=96)
    _run_box(acc, [24, 24, 24, 24])
    assert acc._done == [{"滑块": 24}] * 4      # 滑块进箱被记账 (此前为空)
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert ok, reasons
    assert acc.settled_item_total() == 96
    # to_state 也要能展示出滑块行 (期望留空显示 0)
    _run_box(acc, [24])                          # 再装一盘让 _done 含滑块
    st = acc.to_state({"滑块": "滑块"})
    assert any(it["label"] == "滑块" for it in st["current_tray_items"])


def test_container_items_total_expected_blank_short_ng():
    """期望留空 + 总数模式: 不够整箱目标 → NG (此前会误判 OK)."""
    acc = _ContainerAccumulator("托盘", {}, box_count=0, gone_frames=2,
                                count_mode="items_total", item_target=96)
    _run_box(acc, [24, 24, 24])                  # 只 72
    ok, reasons = acc.verdict({"滑块": "滑块"})
    assert not ok
    assert acc.settled_item_total() == 72


def test_container_to_state_shape():
    """to_state 给前端: 已装托盘数 + 当前托盘实时滑块数/每盘期望."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=4, gone_frames=3)
    tray, items = _tray(0.0), _tray_items(18, 0.0, 0.45)
    acc.update([tray], items, 0.0)
    st = acc.to_state({"滑块": "滑块", "托盘": "托盘"})
    assert st["enabled"] and st["box_count"] == 4 and st["trays_done"] == 0
    cur = st["current_tray_items"][0]
    assert cur["current_count"] == 18 and cur["expected_per_tray"] == 24


def test_build_tracking_with_container():
    """配了容器标签 → 引擎挂上容器累加器, 期望/每箱托盘数正确装配."""
    cfg = _base_config(mixed_with="tracking")
    cfg["steps_config"][2]["expected_count"] = 24
    cfg["pipeline_config"]["custom_mix_container_label"] = "托盘"
    cfg["pipeline_config"]["custom_mix_container_box_count"] = 4
    m = build_custom_mix(cfg)
    assert m._engine._container is not None
    assert m._engine._container.box_count == 4
    assert m._engine._container.item_expected == {"滑块": 24}


def test_build_tracking_without_container_is_zero_diff():
    """不配容器标签 → 容器累加器为 None, 走原满盘门/计数路径 (零差异)."""
    m = build_custom_mix(_base_config(mixed_with="tracking"))
    assert m._engine._container is None


# ---- 放托盘动作确认 + 不应期 (v3.43.1): 治"一次动作断检拆两半、同一盘记两次账" ----

def _action_acc(cooldown=2.0):
    """仅动作确认 (关消失满帧), 出现≥2帧成立 / 消失≥3帧结束, 便于短序列模拟."""
    return _ContainerAccumulator(
        "托盘", {"滑块": 24}, box_count=0, gone_frames=30,
        count_mode="items_total", item_target=96,
        confirm_by_frames=False, confirm_by_action=True, action_label="放托盘",
        action_min_frames=2, action_gone_frames=3, action_cooldown_s=cooldown)


def _feed_action_pulse(acc, tray, items, t0, dt=0.033):
    """托盘全程在场, 放托盘标签出现 2 帧 → 消失 3 帧 = 一个完整动作脉冲. 返回结束时刻."""
    t = t0
    for _ in range(2):
        acc.update([tray], items, t, action_present=True)
        t += dt
    for _ in range(3):
        acc.update([tray], items, t, action_present=False)
        t += dt
    return t


def test_container_action_split_pulse_absorbed_by_cooldown():
    """一次动作被断检拆成两个脉冲 (间隔 < 不应期): 第二个脉冲吸收, 只记一次账.
    复现客户机现场: TRT 推理下放托盘置信度贴阈值抖动, 断检超消失帧 → 动作拆两半,
    托盘实体还在画面里被重新收养, 同一盘 24 支记成 48 支."""
    acc = _action_acc(cooldown=2.0)
    tray, items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    for _ in range(3):                            # 托盘上桌装满
        acc.update([tray], items, 100.0)
    t = _feed_action_pulse(acc, tray, items, 100.1)   # 脉冲1 → 进箱
    assert acc._done == [{"滑块": 24}]
    _feed_action_pulse(acc, tray, items, t + 0.2)     # 断检余波: 0.2s 后又一个脉冲
    # 账本仍只有一笔 (settled_item_total 会把画面里在位托盘折进去凑数, 属展示口径,
    # 不用它断言账本)
    assert acc._done == [{"滑块": 24}], "不应期内的余波脉冲不允许再记账"


def test_container_action_normal_cadence_not_absorbed():
    """正常节奏连续放盘 (间隔 > 不应期): 每盘各记一次账, 不误伤."""
    acc = _action_acc(cooldown=2.0)
    tray, items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    for _ in range(3):
        acc.update([tray], items, 100.0)
    _feed_action_pulse(acc, tray, items, 100.1)       # 第 1 盘进箱
    for _ in range(3):                                # 第 2 盘上桌 (4s 后, 现场实测节奏)
        acc.update([tray], items, 104.0)
    _feed_action_pulse(acc, tray, items, 104.1)       # 第 2 盘进箱
    assert acc.settled_item_total() == 48
    assert len(acc._done) == 2


def test_container_action_cooldown_zero_keeps_old_behavior():
    """不应期显式配 0 = 关闭: 拆分脉冲照旧各记一次账 (给需要极快节奏的现场留退路)."""
    acc = _action_acc(cooldown=0)
    tray, items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    for _ in range(3):
        acc.update([tray], items, 100.0)
    t = _feed_action_pulse(acc, tray, items, 100.1)
    _feed_action_pulse(acc, tray, items, t + 0.2)
    assert len(acc._done) == 2                        # 老行为: 两个脉冲两次账


def _pulse_no_tray(acc, t, dt=0.033):
    """托盘不在场(已被拿走在途)时的放托盘动作脉冲: 出现2帧+消失3帧. 返回结束时刻."""
    for _ in range(2):
        acc.update([], [], t, action_present=True)
        t += dt
    for _ in range(3):
        acc.update([], [], t, action_present=False)
        t += dt
    return t


def test_container_action_revealed_tray_not_adopted_by_departed_ghost():
    """v3.44.1 换盘围栏: 取料位布局 — 上层盘被拿走后, 下层盘在同一位置露出,
    不得被旧盘身份收养/计数; 动作记账记的是"被拿走那盘"自己的峰值.

    复现 SY3 现场 (2026-07-21 13-49-06.mkv 逐帧还原): 取料栈顶盘 A(24支) 被拿走
    → 动作放入箱 → 下层盘 D(19支) 同位置露出 → D 被拿走 → 动作 → 再下层 F(24) 露出.
    修前: D 露出被 A 的旧身份收养, 峰值只增不减, 19 被抹成 24, 账本 [24,24];
    修后: 账本必须是 [24,19] — 每盘按自己的真实数记账."""
    acc = _action_acc(cooldown=1.0)
    tray = _tray(0.0)
    items24, items19 = _tray_items(24, 0.0, 0.45), _tray_items(19, 0.0, 0.45)
    # 栈顶盘 A (24支) 在位
    t = 100.0
    for _ in range(5):
        acc.update([tray], items24, t); t += 0.033
    # A 被拿走(短暂离场) → 放托盘动作 → 记账应记 A 的 24
    for _ in range(5):
        acc.update([], [], t); t += 0.033
    t = _pulse_no_tray(acc, t)
    assert acc._done == [{"滑块": 24}]
    # 空窗后下层盘 D (19支) 同位置露出并停留
    for _ in range(35):
        acc.update([], [], t); t += 0.033
    for _ in range(10):
        acc.update([tray], items19, t); t += 0.033
    # D 被拿走 → 离场超过消失确认帧(30) → 围栏生效; 再下层 F(24) 同位置露出
    for _ in range(35):
        acc.update([], [], t); t += 0.033
    for _ in range(10):
        acc.update([tray], items24, t); t += 0.033
    # 放托盘动作 (放的是 D) → 必须按 D 自己的 19 记账, 不得被 F 的 24 顶掉
    t = _pulse_no_tray(acc, t)
    assert acc._done == [{"滑块": 24}, {"滑块": 19}], \
        f"在途盘必须按自己的 19 记账, 不得被同位置露出的下一盘顶成 24: {acc._done}"
    # F 上位成为新主托盘, 计数从自己的 24 开始
    acc.update([tray], items24, t)
    assert acc._trays[acc._primary]["peak"]["滑块"] == 24


def test_container_action_departed_primary_count_frozen():
    """v3.44.1 在途定格: 主托盘真离场(超过动作消失帧容忍)后峰值当即定格 —
    同一位置随后冒出的物品(下一盘逐渐露出)不得计到在途盘头上."""
    acc = _action_acc(cooldown=1.0)
    tray = _tray(0.0)
    t = 100.0
    for _ in range(5):
        acc.update([tray], _tray_items(19, 0.0, 0.45), t); t += 0.033
    assert acc._trays[acc._primary]["peak"]["滑块"] == 19
    # 盘被拿走: 离场 6 帧 (> action_gone_frames=3 容忍) 后, 同位置露出 24 个物品
    for _ in range(6):
        acc.update([], [], t); t += 0.033
    for _ in range(5):
        acc.update([], _tray_items(24, 0.0, 0.45), t); t += 0.033
    assert acc._trays[acc._primary]["peak"]["滑块"] == 19, "在途盘峰值必须定格在 19"
    # 动作记账 → 按定格的 19 落账
    _pulse_no_tray(acc, t)
    assert acc._done == [{"滑块": 19}]


def test_container_action_tray_stays_visible_both_pulses_booked():
    """盘全程在场连续跟踪 (装箱位布局: 盘直接放进箱里不离场) — 两次动作各记一次账,
    围栏不误杀连续在场的正常跟踪 (零差异保底)."""
    acc = _action_acc(cooldown=1.0)
    tray, items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    for _ in range(3):
        acc.update([tray], items, 100.0)
    t = _feed_action_pulse(acc, tray, items, 100.1)
    assert acc._done == [{"滑块": 24}]
    # 盘一直在场没断档, 4s 后直接来第二个动作脉冲 (间隔 > 不应期)
    for i in range(10):
        t += 0.033
        acc.update([tray], items, t)
    _feed_action_pulse(acc, tray, items, t + 4.0)
    assert len(acc._done) == 2 and acc._done[1] == {"滑块": 24}


def _guard_acc(item_target=96, cooldown=0.0):
    """每盘数量校验开 (items_total): 每盘期望 24, 整箱目标可调."""
    return _ContainerAccumulator(
        "托盘", {"滑块": 24}, box_count=0, gone_frames=30,
        count_mode="items_total", item_target=item_target,
        confirm_by_frames=False, confirm_by_action=True, action_label="放托盘",
        action_min_frames=2, action_gone_frames=3, action_cooldown_s=cooldown,
        per_tray_guard=True)


def _guard_place_tray(acc, n, t):
    """放一盘 n 支: 盘+滑块在场 → 动作脉冲 (盘随动作离场) → 返回结束时刻."""
    tray, items = _tray(0.0), _tray_items(n, 0.0, 0.45)
    for _ in range(3):
        acc.update([tray], items, t); t += 0.033
    for _ in range(2):
        acc.update([], [], t, action_present=True); t += 0.033
    for _ in range(3):
        acc.update([], [], t, action_present=False); t += 0.033
    return t


def test_container_per_tray_guard_rejects_wrong_tray():
    """v3.44.1 错盘拦截: 第3盘 19/24 → 不记账 + 抛错盘警报;
    重装正确的 24 后照常记账, 账本 [24,24,24,24] 总数 96 (哪盘不对从哪盘重做)."""
    acc = _guard_acc()
    t = 100.0
    t = _guard_place_tray(acc, 24, t)
    t = _guard_place_tray(acc, 24, t + 1.0)
    assert acc._done == [{"滑块": 24}] * 2
    # 第3盘只有 19 支: 脉冲结束时峰值不足 → 先挂账等满整窗 (v3.44.3 迟到峰值
    # 等待, 防连放场景半爬峰值误报); 挂满 3 倍消失确认帧仍 19 → 拒账 + 警报
    t = _guard_place_tray(acc, 19, t + 1.0)
    tray19, items19 = _tray(0.0), _tray_items(19, 0.0, 0.45)
    for _ in range(95):
        acc.update([tray19], items19, t); t += 0.033
    assert acc._done == [{"滑块": 24}] * 2, "错盘不得记账"
    alert = acc.consume_wrong_tray_alert()
    assert alert == {"index": 3, "count": 19, "expected": 24}
    assert acc.consume_wrong_tray_alert() is None, "警报取走即清"
    # 工人取出错盘重装 24 → 正常记账, 后面第4盘照常
    t = _guard_place_tray(acc, 24, t + 1.0)
    t = _guard_place_tray(acc, 24, t + 1.0)
    assert acc._done == [{"滑块": 24}] * 4
    assert acc.booked_item_total() == 96


def test_container_per_tray_guard_tail_tray_remainder_ok():
    """尾盘特殊处理: 整箱目标 91 → 第4盘期望 = 余数 19, 19 支不报错盘;
    若尾盘也放 24 (总数会超) → 报错盘拦截."""
    acc = _guard_acc(item_target=91)
    t = 100.0
    for _ in range(3):
        t = _guard_place_tray(acc, 24, t) + 1.0
    # 尾盘 19 = 91-72 的余数 → 放行
    t = _guard_place_tray(acc, 19, t)
    assert acc._done[-1] == {"滑块": 19}
    assert acc.consume_wrong_tray_alert() is None
    assert acc.booked_item_total() == 91
    # 对照: 尾盘放 24 (期望 19) → 拦
    acc2 = _guard_acc(item_target=91)
    t = 100.0
    for _ in range(3):
        t = _guard_place_tray(acc2, 24, t) + 1.0
    _guard_place_tray(acc2, 24, t)
    assert acc2.booked_item_total() == 72, "错尾盘不得记账"
    assert acc2.consume_wrong_tray_alert() == {"index": 4, "count": 24, "expected": 19}


def test_container_per_tray_guard_off_zero_diff():
    """默认关 (per_tray_guard=False) 零差异: 19 支的盘照旧记账, 无警报."""
    acc = _action_acc(cooldown=0.0)
    t = _guard_place_tray(acc, 19, 100.0)
    assert acc._done == [{"滑块": 19}]
    assert acc.consume_wrong_tray_alert() is None


def test_container_action_reveal_during_pulse_counted():
    """v3.44.2 动作尾窗口不再饥饿: 新盘的黄金可见窗口整个落在放托盘动作余像内
    (7-16 数据集视频周期3实测: 新盘满24清晰可见的0.8s全在动作尾内, 动作一结束
    就被下一次取盘遮挡) — 屏蔽窗口只冻结旧主盘, 动作期新生盘照常计数.

    修前: 一刀切停数, 新盘峰值只攒到动作结束后的残帧 (22) → 错盘误报 22/24;
    修后: 动作期攒满 24, 账本 [24,24], 全程无错盘警报."""
    acc = _guard_acc(item_target=96)
    tray = _tray(0.0)
    items24 = _tray_items(24, 0.0, 0.45)
    t = 100.0
    # 盘 A 在位满 24 (主托盘)
    for _ in range(5):
        acc.update([tray], items24, t); t += 0.033
    # 动作开始: A 被手遮挡离场 (gone 攒到动作期围栏门槛 action_gone_frames=3)
    for _ in range(4):
        acc.update([], [], t, action_present=True); t += 0.033
    # 动作仍进行中: 新盘 B 同位置露出、满 24 清晰可见 — 黄金窗口全在动作余像内
    for _ in range(5):
        acc.update([tray], items24, t, action_present=True); t += 0.033
    # 动作结束的瞬间 B 就被下一次取盘遮挡 (此后不再露面 = 窗口饥饿场景)
    for _ in range(3):
        acc.update([], [], t, action_present=False); t += 0.033
    assert acc._done == [{"滑块": 24}], f"动作结束应记 A 的 24: {acc._done}"
    assert acc.consume_wrong_tray_alert() is None
    # 下一个动作脉冲 (放第3盘): 记 B 的账 — 必须是动作期攒到的 24
    t += 1.5
    for _ in range(2):
        acc.update([], [], t, action_present=True); t += 0.033
    for _ in range(3):
        acc.update([], [], t, action_present=False); t += 0.033
    assert acc._done == [{"滑块": 24}] * 2, \
        f"动作期新生盘必须按黄金窗口攒到的 24 记账: {acc._done}"
    assert acc.consume_wrong_tray_alert() is None, "不得错盘误报"


def test_container_display_recovers_after_long_occlusion_no_action():
    """v3.44.2 卡死显示恢复: 托盘被工人长时间遮挡 (超过容器消失确认帧) 后重新露出,
    没有任何放托盘动作 — 换盘围栏拒绝旧身份吸附新框, 但旧身份仍占主盘位且被
    在途定格, 计数永久停摆 → 现场表现: 24 个滑块检测框都画出来了, 物品校验一直 0,
    且整箱滑块记 0 → 箱落不了账、工单 0/N 收尾 (2026-07-23 现场反馈).

    修后: 主盘定格时计数落到新露出的在场身份, 显示当场恢复 24."""
    acc = _guard_acc(item_target=96)
    tray = _tray(0.0)
    items24 = _tray_items(24, 0.0, 0.45)
    t = 100.0
    for _ in range(5):
        acc.update([tray], items24, t); t += 0.033
    assert acc._cur_counts == {"滑块": 24}
    # 工人趴在箱上整理: 托盘+滑块整体被遮挡, 超过容器消失确认帧 (30)
    for _ in range(32):
        acc.update([], [], t); t += 0.033
    # 遮挡结束, 托盘和 24 个滑块重新露出 (旧身份被围栏拒绝 → 新身份)
    for _ in range(5):
        acc.update([tray], items24, t); t += 0.033
    assert acc._cur_counts == {"滑块": 24}, \
        f"长遮挡后露出必须恢复计数显示, 不得永久卡 0: {acc._cur_counts}"
    assert acc.consume_wrong_tray_alert() is None
    # 随后正常放盘动作: 记账值必须仍是 24 (旧身份定格峰值本就是满盘)
    for _ in range(2):
        acc.update([], [], t, action_present=True); t += 0.033
    for _ in range(3):
        acc.update([], [], t, action_present=False); t += 0.033
    assert acc._done == [{"滑块": 24}], f"记账应为满盘 24: {acc._done}"
    assert acc.consume_wrong_tray_alert() is None


def test_container_action_settle_waits_for_late_peak():
    """v3.44.3 迟到峰值等待: 连放场景 (7-23 视频, 工人 ~3s 一盘) 最后一盘的放盘
    脉冲结束时, 新盘滑块还被手挡着没计入峰值 — 修前空峰值直接烧掉脉冲并删身份,
    这盘永远没进账 (72/96 → 数量门静默拦收尾 → 周期结不了 → 下一箱账全灌进来
    168/96 连锁崩). 修后: 脉冲挂账等峰值, 爬到每盘期望立即补记."""
    acc = _guard_acc(item_target=96)
    t = 100.0
    for _ in range(3):
        t = _guard_place_tray(acc, 24, t) + 0.5
    assert acc._done == [{"滑块": 24}] * 3
    # 第4盘: 脉冲成立并结束, 但结束时盘还没露出 (手/身体挡着)
    for _ in range(2):
        acc.update([], [], t, action_present=True); t += 0.033
    for _ in range(4):
        acc.update([], [], t, action_present=False); t += 0.033
    assert acc._done == [{"滑块": 24}] * 3, "峰值未就绪不得结空账"
    # 几帧后盘露出, 滑块逐步显形 12 → 24 (半爬阶段不得提前结账/错盘误报)
    tray = _tray(0.0)
    for _ in range(3):
        acc.update([tray], _tray_items(12, 0.0, 0.45), t); t += 0.033
    assert acc._done == [{"滑块": 24}] * 3, "半爬峰值不得提前结账"
    assert acc.consume_wrong_tray_alert() is None, "半爬峰值不得错盘误报"
    for _ in range(3):
        acc.update([tray], _tray_items(24, 0.0, 0.45), t); t += 0.033
    assert acc._done == [{"滑块": 24}] * 4, f"迟到峰值到位必须补记: {acc._done}"
    assert acc.consume_wrong_tray_alert() is None
    assert acc.booked_item_total() == 96


def test_container_ghost_primary_empty_settle_keeps_pulse():
    """v3.44.3 幽灵占主位不得烧脉冲: 动作期一个托盘误检框闪现 (无滑块) 抢到主位,
    脉冲结束时它以空峰值走结账分支 — 修前会把身份和脉冲一起消费掉 (7-23 视频
    第4盘 52 秒后才被下一箱脉冲错位补记的直接根因); 修后只清幽灵让位, 脉冲保留,
    真盘显形爬满 24 后照常补记."""
    acc = _guard_acc(item_target=96)
    t = 100.0
    for _ in range(3):
        t = _guard_place_tray(acc, 24, t) + 0.5
    # 第4盘脉冲: 动作期一个误检托盘框闪现两帧 (无滑块) 后消失
    ghost = _tray(0.6)
    for _ in range(2):
        acc.update([ghost], [], t, action_present=True); t += 0.033
    for _ in range(2):
        acc.update([], [], t, action_present=True); t += 0.033
    for _ in range(4):
        acc.update([], [], t, action_present=False); t += 0.033  # 脉冲结束, 幽灵占主位
    # 幽灵消失满 gone_frames(30) → 空峰值结账让位 (脉冲必须保留)
    for _ in range(32):
        acc.update([], [], t); t += 0.033
    assert acc._done == [{"滑块": 24}] * 3, "幽灵不得记账"
    # 真盘显形, 滑块满 24 → 用保留的脉冲补记第4盘
    tray = _tray(0.0)
    for _ in range(3):
        acc.update([tray], _tray_items(24, 0.0, 0.45), t); t += 0.033
    assert acc._done == [{"滑块": 24}] * 4, f"幽灵占位不得烧掉脉冲: {acc._done}"
    assert acc.consume_wrong_tray_alert() is None
    assert acc.booked_item_total() == 96


def test_container_pending_booking_peak_total():
    """v3.44.3 数量门竞态补丁口径: 末盘动作已成立、记账还在峰值就绪等待窗内时,
    pending_booking_peak_total() 应返回该盘峰值 (数量门把它计入"箱内已有");
    记账落地后归零; 无脉冲的备盘区闲置盘不算 (与 settled 凑数口径区分)."""
    acc = _guard_acc(item_target=96)
    t = 100.0
    for _ in range(3):
        t = _guard_place_tray(acc, 24, t) + 0.5
    assert acc.booked_item_total() == 72
    # 备盘区闲置盘 (无动作脉冲) 在场 → 不算在途
    idle_tray, idle_items = _tray(0.0), _tray_items(24, 0.0, 0.45)
    acc.update([idle_tray], idle_items, t); t += 0.033
    assert acc.pending_booking_peak_total() == 0, "无脉冲闲置盘不得计入在途"
    # 第4盘: 动作脉冲成立, 盘+滑块在场, 峰值爬到 24 — 记账落地前应可见在途峰值
    for _ in range(2):
        acc.update([idle_tray], idle_items, t, action_present=True); t += 0.033
    for _ in range(3):
        acc.update([idle_tray], idle_items, t, action_present=False); t += 0.033
    if acc.booked_item_total() == 72:
        # 记账尚未落地 (峰值就绪等待窗内) → 在途峰值 = 24, 门口径 72+24=96 放行
        assert acc.pending_booking_peak_total() == 24, \
            f"在途峰值缺失: {acc.pending_booking_peak_total()}"
    # 盘离场走完消失确认 → 记账落地, 在途归零
    for _ in range(35):
        acc.update([], [], t); t += 0.033
    assert acc.booked_item_total() == 96
    assert acc.pending_booking_peak_total() == 0, "记账落地后在途必须归零"


def test_container_action_empty_pulse_dropped_after_grace():
    """空动作脉冲 (误检/盘从未露出) 挂账到期 (3 倍消失确认帧) 后丢弃, 不偷记账;
    之后盘正常露出但没有新脉冲 → 也不得结账 (脉冲-账一一配对)."""
    acc = _guard_acc(item_target=96)
    t = 100.0
    for _ in range(2):
        acc.update([], [], t, action_present=True); t += 0.033
    for _ in range(95):  # 宽限 = gone_frames(30)*3 = 90 帧
        acc.update([], [], t, action_present=False); t += 0.033
    assert acc._action_done_pending is False, "到期空脉冲必须丢弃"
    assert acc._done == []
    tray = _tray(0.0)
    for _ in range(5):
        acc.update([tray], _tray_items(24, 0.0, 0.45), t); t += 0.033
    assert acc._done == [], "无脉冲不得偷记账"


def test_build_container_per_tray_guard_from_pipeline():
    """配置键 custom_mix_container_per_tray_guard 直通累加器; 缺省 False."""
    cfg = _base_config(mixed_with="tracking")
    cfg["pipeline_config"].update({
        "custom_mix_container_label": "托盘",
        "custom_mix_container_count_mode": "items_total",
        "custom_mix_container_item_target": 96,
    })
    m = build_custom_mix(cfg)
    assert m._engine._container.per_tray_guard is False
    cfg["pipeline_config"]["custom_mix_container_per_tray_guard"] = True
    m2 = build_custom_mix(cfg)
    assert m2._engine._container.per_tray_guard is True


def test_build_container_action_thresholds_from_pipeline():
    """动作门槛三参数可在进箱确认配置里直配, 优先于步骤字段; 不应期缺省 2s."""
    cfg = _base_config(mixed_with="tracking")
    cfg["pipeline_config"].update({
        "custom_mix_container_label": "托盘",
        "custom_mix_container_confirm_by_action": True,
        "custom_mix_container_action_label": "放托盘",
        "custom_mix_container_action_min_frames": 5,
        "custom_mix_container_action_gone_frames": 20,
    })
    m = build_custom_mix(cfg)
    c = m._engine._container
    assert c.action_min_frames == 5
    assert c.action_gone_frames == 20
    assert c.action_cooldown_s == 2.0                 # 没配 → 缺省 2s
    cfg["pipeline_config"]["custom_mix_container_action_cooldown_s"] = 0
    m2 = build_custom_mix(cfg)
    assert m2._engine._container.action_cooldown_s == 0.0   # 显式 0 = 关闭


def _make_container_vsm(box_count=2, per_tray=3, gone_frames=2,
                        slider_roi=None, tray_roi=None):
    slider_row = {"id": "s3", "label": "滑块", "enabled": True,
                  "detect_role": "item", "count_mode": "track",
                  "expected_count": per_tray}
    if slider_roi:
        slider_row["roi"] = slider_roi
    steps = [
        {"id": "s1", "label": "贴标", "enabled": True},
        {"id": "s2", "label": "封箱", "enabled": True},
        slider_row,
    ]
    if tray_roi:
        # 托盘要画 ROI 就得有自己的步骤行 (前端同构); 不进序列, 不参与结算
        steps.append({"id": "s4", "label": "托盘", "enabled": True,
                      "roi": tray_roi})
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config({
        "id": 99421, "name": "混合容器单测",
        "logic_mode": "custom",
        "pipeline_config": {
            "custom_based_on": "sequential",
            "custom_mixed_with": "tracking",
            "custom_sequence_order": [{"step_id": "s1"}, {"step_id": "s2"}],
            "custom_mix_container_label": "托盘",
            "custom_mix_container_box_count": box_count,
            "custom_mix_container_gone_frames": gone_frames,
        },
        "steps_config": steps,
        "events_config": [], "counters_config": [], "data_config": {},
    })
    assert vsm._custom_mix is not None
    assert vsm._custom_mix._engine._container is not None
    return vsm


def test_container_integration_via_vsm():
    """真 VSM 喂帧: 托盘框被累加器消费 (隔离物品流), to_state 暴露 container."""
    vsm = _make_container_vsm(box_count=2, per_tray=3, gone_frames=2)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    tray = {"label": "托盘", "confidence": 0.95, "x": 0.0, "y": 0.0, "w": 0.6, "h": 1.0}
    sliders = [{"label": "滑块", "confidence": 0.95, "track_id": 11 + i,
                "x": 0.1 + i * 0.12, "y": 0.5, "w": 0.05, "h": 0.05} for i in range(3)]
    for _ in range(5):
        m.feed(vsm, [tray] + sliders, 0.0)
    st = m.to_state()
    assert "container" in st
    assert st["container"]["box_count"] == 2
    cur = st["container"]["current_tray_items"][0]
    assert cur["current_count"] == 3


def test_container_integration_verdict_routes_through_container():
    """容器模式下 verdict 走整箱裁决 (而非全局累计); 装满 2 盘各 3 → 经合成 OK."""
    vsm = _make_container_vsm(box_count=2, per_tray=3, gone_frames=2)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    tray = {"label": "托盘", "confidence": 0.95, "x": 0.0, "y": 0.0, "w": 0.6, "h": 1.0}
    sliders = [{"label": "滑块", "confidence": 0.95, "track_id": 11 + i,
                "x": 0.1 + i * 0.12, "y": 0.5, "w": 0.05, "h": 0.05} for i in range(3)]
    for box in range(2):                       # 2 盘逐个装满进箱
        for _ in range(2):
            m.feed(vsm, [tray] + sliders, float(box))
        for _ in range(2):
            m.feed(vsm, [], float(box) + 0.5)
    ok, reasons = m.verdict()
    assert ok, reasons


# ==================== v3.44.4 末盘救账 (盘堆最后一盘, 7-23 视频箱1) ====================
# 现场轨迹: 拿走盘堆最后一盘后, 主位被"空峰值幽灵"占住 (gone 已满), 真盘身份
# (峰值24, 刚离场) 躺在非主位 — 老逻辑"让位保留脉冲"等新盘显形, 但箱已装完再无
# 新盘, 这盘的账永远丢了 (72/96 误NG)。修后: 脉冲在手 + 主位空峰值 → 改配
# "已离场且有峰值"的真盘记账 (取最近离场者)。


def test_container_action_last_tray_rescued_from_ghost_primary():
    acc = _action_acc(cooldown=1.0)
    # 白盒还原现场态: 真盘 A (峰值24, 离场 5 帧) + 幽灵主盘 G (空峰值, 离场满帧)
    acc._trays = {
        1: {'bbox': {'x': 0.0, 'y': 0.0, 'w': 0.45, 'h': 1.0},
            'first_seen': 90.0, 'last_seen': 100.0, 'gone': 5,
            'peak': {'滑块': 24}},
        2: {'bbox': {'x': 0.55, 'y': 0.0, 'w': 0.4, 'h': 1.0},
            'first_seen': 95.0, 'last_seen': 99.0, 'gone': 31,
            'peak': {}},
    }
    acc._seq = 2
    acc._primary = 2                    # 幽灵占主位
    acc._action_done_pending = True     # 放托盘脉冲已成立待配对
    acc.update([], [], 101.0)
    assert acc._done == [{"滑块": 24}], f"真盘的24必须被救回记账: {acc._done}"
    assert not acc._action_done_pending  # 脉冲已消费


def test_container_action_ghost_primary_no_candidate_keeps_pulse():
    """无可救的真盘时保持老行为: 让位保留脉冲, 等新盘显形."""
    acc = _action_acc(cooldown=1.0)
    acc._trays = {
        2: {'bbox': {'x': 0.55, 'y': 0.0, 'w': 0.4, 'h': 1.0},
            'first_seen': 95.0, 'last_seen': 99.0, 'gone': 31,
            'peak': {}},
    }
    acc._seq = 2
    acc._primary = 2
    acc._action_done_pending = True
    acc.update([], [], 101.0)
    assert acc._done == []
    assert acc._action_done_pending     # 脉冲保留给显形中的新盘


def test_container_action_in_box_mover_rescued_by_action_birth():
    """二档救账 (7-23 箱1取证还原): 被放进箱的盘还在箱里被检出 (gone=0),
    身份是围栏在本次动作期拆分出的 — 脉冲遇空峰值主位时认领记账。
    v3.44.4: 候选峰值(23)没爬满期望(24)时先挂账等真账显形, 满窗(3×消失满帧)
    仍没爬满 → 按现值 23 结账 (取候选中峰值最大者, 备盘 12 不被捡走)."""
    acc = _action_acc(cooldown=1.0)
    acc._trays = {
        # 幽灵主位: 空峰值, 离场满帧
        1: {'bbox': {'x': 0.0, 'y': 0.0, 'w': 0.45, 'h': 1.0},
            'first_seen': 90.0, 'last_seen': 99.0, 'gone': 31, 'peak': {}},
        # 动作前就摆着的下一箱备盘 (峰值小, 不得认领)
        2: {'bbox': {'x': 0.5, 'y': 0.0, 'w': 0.4, 'h': 1.0},
            'first_seen': 92.0, 'last_seen': 101.0, 'gone': 0,
            'peak': {'滑块': 12}},
        # 本次动作期新生的"移动中托盘" (在箱里, gone=0, 峰值23) → 该记它的账
        3: {'bbox': {'x': 0.3, 'y': 0.3, 'w': 0.4, 'h': 0.6},
            'first_seen': 100.2, 'last_seen': 101.0, 'gone': 0,
            'peak': {'滑块': 23}},
    }
    acc._seq = 3
    acc._primary = 1
    acc._action_started_ts = 100.0      # 动作成立于 100.0
    acc._action_done_pending = True
    # 挂账窗口 = 3×gone_frames(30) 帧; 喂帧保持两在位盘活着, 峰值不再增长
    t = 101.0
    keep = [dict(x=0.5, y=0.0, w=0.4, h=1.0), dict(x=0.3, y=0.3, w=0.4, h=0.6)]
    for _ in range(30 * 3 + 2):
        acc.update(keep, [], t)
        if acc._done:
            break
        t += 0.033
    assert acc._done == [{"滑块": 23}], f"该记动作期新生盘的23: {acc._done}"
    assert 2 in acc._trays              # 备盘身份保留, 等它自己的脉冲


# ==================== v3.44.4 每盘峰值封顶 (peak_cap, 可配默认关) ====================
# 7-23 视频实测: 模型偶发重复框瞬时数出 25/26, 峰值取存续期最大值会咬死这一帧
# → 整箱 97/96 被误判"超出"NG。开了封顶按配置值封每盘峰值; 默认关 = 零差异。


def test_peak_capped_when_configured():
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=2,
                                item_target=96, count_mode='items_total',
                                peak_cap=24)
    acc.update([_tray(0.5)], _tray_items(24, 0.5, 0.95), 1.0)
    acc.update([_tray(0.5)], _tray_items(26, 0.5, 0.95), 1.1)   # 重复框瞬时 26
    tid = acc._primary
    assert acc._trays[tid]['peak'] == {"滑块": 24}, acc._trays[tid]['peak']


def test_peak_uncapped_by_default():
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=2,
                                item_target=96, count_mode='items_total')
    acc.update([_tray(0.5)], _tray_items(26, 0.5, 0.95), 1.0)
    tid = acc._primary
    assert acc._trays[tid]['peak'] == {"滑块": 26}   # 默认关 = 老行为零差异


# ==================== v3.44.4 结算折算守门 (_primary_foldable) ====================
# 上银 7-27: 封箱结算时点位上摆着"下一箱已备好未动的首盘" (peak=24), 老折算
# 无条件把在位主盘折进本箱 → 90/96 不足被抹成 114/96 超出, NG 语义反了。
# 动作确认模式下只折"真在途"盘 (脉冲待配对/已消失满帧); 非动作模式零差异。


def test_verdict_fold_skips_staged_next_tray_in_action_mode():
    """动作确认模式: 在位未动的下一箱首盘不折进本箱 → 少装照报不足."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=2,
                                item_target=96, count_mode='items_total',
                                confirm_by_frames=True, confirm_by_action=True,
                                action_label="放托盘", confirm_combine='and')
    acc._done = [{"滑块": 24}, {"滑块": 22}, {"滑块": 24}, {"滑块": 20}]  # 90/96
    # 下一箱首盘已上台面: 在场 (gone=0)、无动作脉冲
    acc.update([_tray(0.5)], _tray_items(24, 0.5, 0.95), 1.0)
    assert acc._primary is not None
    ok, reasons = acc.verdict({})
    assert not ok and any('不足 90/96' in r for r in reasons), reasons
    assert acc.settled_item_total() == 90


def test_verdict_fold_keeps_in_transit_tray_in_action_mode():
    """动作确认模式: 脉冲待配对的在途末盘照折 (老语义保留)."""
    acc = _ContainerAccumulator("托盘", {"滑块": 24}, box_count=0, gone_frames=2,
                                item_target=96, count_mode='items_total',
                                confirm_by_frames=True, confirm_by_action=True,
                                action_label="放托盘", confirm_combine='and')
    acc._done = [{"滑块": 24}, {"滑块": 24}, {"滑块": 24}]  # 72/96
    acc.update([_tray(0.5)], _tray_items(24, 0.5, 0.95), 1.0)
    acc._action_done_pending = True                          # 末盘动作已成立待配对
    ok, reasons = acc.verdict({})
    assert ok, reasons                                       # 72+24 = 96 折进来才合格
    assert acc.settled_item_total() == 96


# ==================== v3.44.4 记账链 ROI 守门 (feed 入口) ====================
# 上银现场: 主操作区右侧是备盘堆, 备盘上的滑块/托盘会被模型照常检出。
# 此前记账链 (托盘/滑块/放托盘 → _ContainerAccumulator) 完全不吃步骤 ROI,
# 画了 ROI 也拦不住备盘污染箱账。本节锁定: 三类标签喂账前均过步骤 ROI,
# 未画 ROI 时零差异 (上面全部旧测试即零差异对照组)。
_LEFT_HALF_ROI = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]


def test_container_roi_filters_items_from_ledger():
    """滑块画左半 ROI: 主托盘横跨全屏, 盘内 3 个在 ROI 内 + 2 个在 ROI 外
    → 记账只数 3 (此前会数 5)."""
    vsm = _make_container_vsm(per_tray=5, slider_roi=_LEFT_HALF_ROI)
    assert vsm.step_roi_polygons.get("滑块"), "ROI 应从 steps_config 解析进 poly_map"
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    tray = {"label": "托盘", "confidence": 0.95, "x": 0.0, "y": 0.0, "w": 0.95, "h": 1.0}
    inside = [{"label": "滑块", "confidence": 0.95, "track_id": 11 + i,
               "x": 0.05 + i * 0.12, "y": 0.5, "w": 0.05, "h": 0.05} for i in range(3)]
    outside = [{"label": "滑块", "confidence": 0.95, "track_id": 21 + i,
                "x": 0.60 + i * 0.12, "y": 0.5, "w": 0.05, "h": 0.05} for i in range(2)]
    for _ in range(5):
        m.feed(vsm, [tray] + inside + outside, 0.0)
    cont = m._engine._container
    assert cont._cur_counts.get("滑块") == 3, cont._cur_counts


def test_container_roi_filters_tray_identity():
    """托盘画左半 ROI: 右侧备盘堆的托盘框不进累加器, 不产生托盘身份."""
    vsm = _make_container_vsm(tray_roi=_LEFT_HALF_ROI)
    m = vsm._custom_mix
    vsm.current_cycle_uuid = "c1"
    backup_tray = {"label": "托盘", "confidence": 0.95,
                   "x": 0.55, "y": 0.0, "w": 0.4, "h": 1.0}   # 中心 x=0.75, ROI 外
    for _ in range(5):
        m.feed(vsm, [backup_tray], 0.0)
    cont = m._engine._container
    assert cont._primary is None and not cont._trays
    work_tray = {"label": "托盘", "confidence": 0.95,
                 "x": 0.05, "y": 0.0, "w": 0.4, "h": 1.0}     # 中心 x=0.25, ROI 内
    for _ in range(3):
        m.feed(vsm, [work_tray], 0.1)
    assert cont._primary is not None                          # 工作区托盘照常建账


# ---- v3.44.5 "动作前稳定计数"快照记账 (stable_min_frames): 治连放合并/漏账 ----

def _stable_acc(stable=3, cooldown=1.0):
    """AND 组合 + 快照记账: 消失帧阈值故意设很大 (堆顶检测框永不消失场景),
    没有快照机制时 AND 永远等不齐、脉冲只能挂账."""
    return _ContainerAccumulator(
        "托盘", {"滑块": 24}, box_count=0, gone_frames=30,
        count_mode="items_total", item_target=96,
        confirm_by_frames=True, confirm_by_action=True, action_label="放托盘",
        confirm_combine="and", action_min_frames=2, action_gone_frames=3,
        action_cooldown_s=cooldown, stable_min_frames=stable)


def _feed_stable_then_pulse(acc, n_before, n_during, t0, frames=6, dt=0.1):
    """喂稳定段 (n_before 支, 满 frames 帧) → 动作脉冲 (手遮挡计数塌到 n_during),
    托盘检测框全程在场 (堆顶无缝接下一盘的现场形态). 返回结束时刻."""
    tray = _tray(0.0)
    t = t0
    for _ in range(frames):
        acc.update([tray], _tray_items(n_before, 0.0, 0.45), t)
        t += dt
    for _ in range(2):   # 动作成立 (快照在这一刻定格 n_before)
        acc.update([tray], _tray_items(n_during, 0.0, 0.45), t,
                   action_present=True)
        t += dt
    for _ in range(3):   # 动作结束 → 脉冲结算
        acc.update([tray], _tray_items(n_during, 0.0, 0.45), t,
                   action_present=False)
        t += dt
    return t


def test_container_stable_snapshot_books_pre_touch_count():
    """堆顶检测框永不消失 + 连放: 每次动作按"动作成立前的稳定计数"入账,
    少装盘 (22) 不被下一盘的 24 污染 (训练端"拿起前稳定计数"方案)."""
    acc = _stable_acc()
    t = _feed_stable_then_pulse(acc, 24, 10, 100.0)
    assert acc._done == [{"滑块": 24}], acc._done
    # 身份原地重开新账: 主位还在、峰值清零 (下一盘在同一身份上攒稳定值)
    assert acc._primary is not None
    assert not acc._trays[acc._primary]["peak"]
    # 第二盘是少装盘 22, 间隔 > 不应期
    t = _feed_stable_then_pulse(acc, 22, 8, t + 1.2)
    assert acc._done == [{"滑块": 24}, {"滑块": 22}], acc._done


def test_container_stable_snapshot_occlusion_dip_not_booked():
    """手遮挡骤降段 (连续同值但属动作期) 不污染稳定值: 动作前稳定 24,
    动作中一直看到 10, 入账仍是 24."""
    acc = _stable_acc()
    tray = _tray(0.0)
    t = 100.0
    for _ in range(6):
        acc.update([tray], _tray_items(24, 0.0, 0.45), t); t += 0.1
    # 手伸进来 (动作还没成立) 计数塌到 10 且持续 — 稳定值会被 10 覆盖吗?
    # 会 (连续同值满帧即稳定), 但动作成立时快照的是"最后一次稳定"= 10 之前
    # 已被覆盖 → 该场景由动作及时性兜底; 这里验证的是动作期(屏蔽/骤降)不覆盖:
    for _ in range(2):
        acc.update([tray], _tray_items(10, 0.0, 0.45), t, action_present=True); t += 0.1
    for _ in range(3):
        acc.update([tray], _tray_items(10, 0.0, 0.45), t, action_present=False); t += 0.1
    assert acc._done == [{"滑块": 24}], acc._done


def test_container_book_preview_follows_booking_rule():
    """v3.44.6 预计进箱值 (book_preview) 与记账规则同源:
    稳定计数开 → 显示稳定值 (瞬态 25 抬高峰值也不跟着跳);
    稳定计数关 → 退回峰值 (老行为)。操作员按这个数预判进箱结果."""
    acc = _stable_acc()
    tray = _tray(0.0)
    t = 100.0
    for _ in range(6):                        # 稳定段 24
        acc.update([tray], _tray_items(24, 0.0, 0.45), t); t += 0.1
    acc.update([tray], _tray_items(25, 0.0, 0.45), t); t += 0.1   # 瞬态 25
    st = acc.to_state({"滑块": "滑块"})
    it = st["current_tray_items"][0]
    assert it["peak_count"] == 25, it        # 峰值被瞬态抬高
    assert it["book_preview"] == 24, it      # 预计进箱按稳定值, 不跟着跳

    # 稳定计数关: 预计进箱 = 峰值 (零差异)
    acc2 = _stable_acc(stable=0)
    for i in range(3):
        acc2.update([tray], _tray_items(23, 0.0, 0.45), 200.0 + i * 0.1)
    st2 = acc2.to_state({"滑块": "滑块"})
    it2 = st2["current_tray_items"][0]
    assert it2["book_preview"] == it2["peak_count"] == 23, it2


def test_container_stable_snapshot_off_zero_diff():
    """stable_min_frames=0 (默认关): 同样的喂帧序列不走快照路径 —
    AND 组合下消失满帧等不齐, 账本保持为空 (老行为零差异)."""
    acc = _stable_acc(stable=0)
    _feed_stable_then_pulse(acc, 24, 10, 100.0)
    assert acc._done == [], acc._done


def test_container_stable_snapshot_takes_max_ignores_hover_dip():
    """7-27 实测坑: 结账后到下一动作之间手悬在堆顶只露 2 个, 也会攒出稳定段 →
    曾把下一盘记成 2。稳定值取本账期最大稳定段: 手悬停(2)不覆盖看全帧(24)."""
    acc = _stable_acc()
    t = _feed_stable_then_pulse(acc, 24, 10, 100.0)
    assert acc._done == [{"滑块": 24}]
    tray = _tray(0.0)
    t += 1.2                                     # 过不应期
    for _ in range(6):                           # 手悬停: 稳定地只露 2 个
        acc.update([tray], _tray_items(2, 0.0, 0.45), t); t += 0.1
    for _ in range(6):                           # 手离开: 看全 24
        acc.update([tray], _tray_items(24, 0.0, 0.45), t); t += 0.1
    for _ in range(2):
        acc.update([tray], _tray_items(8, 0.0, 0.45), t, action_present=True); t += 0.1
    for _ in range(3):
        acc.update([tray], _tray_items(8, 0.0, 0.45), t, action_present=False); t += 0.1
    assert acc._done == [{"滑块": 24}, {"滑块": 24}], acc._done


def test_container_stable_snapshot_frozen_during_action():
    """动作进行中冻结稳定值: 盘被拿走后堆顶接上的下一盘 (满 24) 提前曝光,
    不许污染当前少装盘 (22) 的账期 — 少装照记 22."""
    acc = _stable_acc()
    tray = _tray(0.0)
    t = 100.0
    for _ in range(6):                           # 当前盘 22 (少装)
        acc.update([tray], _tray_items(22, 0.0, 0.45), t); t += 0.1
    for _ in range(2):                           # 动作成立, 快照 22
        acc.update([tray], _tray_items(24, 0.0, 0.45), t, action_present=True); t += 0.1
    for _ in range(8):                           # 动作持续中下一盘满 24 曝光 → 冻结, 不覆盖
        acc.update([tray], _tray_items(24, 0.0, 0.45), t, action_present=True); t += 0.1
    for _ in range(3):
        acc.update([tray], _tray_items(24, 0.0, 0.45), t, action_present=False); t += 0.1
    assert acc._done == [{"滑块": 22}], acc._done


def test_container_stable_snapshot_wins_over_ghost_primary():
    """7-27 实测坑: 紧凑连放时主位被在途幽灵抢走 (手里的盘只看到 4 个),
    动作成立瞬间核准过 24 的备盘堆身份躺在非主位 → 记账认快照不认主位."""
    acc = _stable_acc()
    stack = _tray(0.0)
    t = 100.0
    for _ in range(6):                           # 备盘堆稳定 24
        acc.update([stack], _tray_items(24, 0.0, 0.45), t); t += 0.1
    mover = {"x": 0.6, "y": 0.6, "w": 0.3, "h": 0.3}
    for _ in range(2):                           # 动作成立 (快照 24 定格在堆身份上)
        acc.update([stack, mover], _tray_items(4, 0.6, 0.9, y=0.75), t,
                   action_present=True); t += 0.1
    # 白盒模拟主位churn: 在途幽灵 (peak=4) 抢到主位
    ghost_tid = [tid for tid, tr in acc._trays.items()
                 if tr['bbox']['x'] > 0.5]
    assert ghost_tid, list(acc._trays)
    acc._primary = ghost_tid[0]
    acc._primary_frames_ok = True                # 最坏情况: AND 条件也已凑齐
    for _ in range(3):                           # 动作结束 → 脉冲结算
        acc.update([stack, mover], _tray_items(4, 0.6, 0.9, y=0.75), t,
                   action_present=False); t += 0.1
    assert acc._done == [{"滑块": 24}], acc._done
