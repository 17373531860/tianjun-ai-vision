"""v3.59 容器定界周期 (custom_cycle_owner='container') 单元测试.

覆盖:
  - build_container_gate 构建守门 (零差异: 缺省/缺标签/非 custom 全部 None)
  - apply_project_config 挂载 (_container_cycle_gate + _custom_cycle_owner)
  - ContainerCycleGate FSM: 到位确认 / 闪现不开周期 / 遮挡桥接 / 离场确认结算
  - _settle_container_cycle 完备判定 (based_on='detection'): 全齐 OK / 缺步 NG /
    重复容忍 / 空周期判 NG 缺全部
  - _settle_container_cycle 纯 custom 分支: 条件命中走条件事件
  - _check_events 让位: 容器主权下条件全等命中不再当场结算

只驱动新部件本身, 不接触真实推理/DB/报警。其他模式测试不动。
"""
import time

import pytest


# ============================================================
# 公共构造器
# ============================================================

def _project_cfg(pipeline_extra=None, steps=None, logic_mode='custom'):
    pipeline = {
        'custom_based_on': 'detection',
        'custom_cycle_owner': 'container',
        'container_gate_label': 'BOX',
        'container_gate_appear_seconds': 1.0,
        'container_gate_gone_seconds': 3.0,
    }
    if pipeline_extra:
        pipeline.update(pipeline_extra)
    if steps is None:
        steps = [
            {'id': 1, 'label': 'A', 'enabled': True, 'min_frames': 1},
            {'id': 2, 'label': 'B', 'enabled': True, 'min_frames': 1},
            {'id': 3, 'label': 'BOX', 'enabled': False},
        ]
    return {
        'id': 99590,
        'name': '容器定界单测项目',
        'task_type': 'detection',
        'logic_mode': logic_mode,
        'steps_config': steps,
        'events_config': [
            {'id': 1, 'name': 'OK', 'actions': [], 'show_notification': False},
            {'id': 2, 'name': 'NG', 'actions': [], 'show_notification': False},
            {'id': 3, 'name': '自定义', 'actions': [], 'show_notification': False},
        ],
        'counters_config': [],
        'pipeline_config': pipeline,
    }


def _make_vsm(**kw):
    from backend.api.source import VideoSourceManager
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config(_project_cfg(**kw))
    vsm._test_events = []

    def _capture_trigger(event_id, reason=''):
        vsm._test_events.append((event_id, reason))
    vsm._trigger_event = _capture_trigger
    return vsm


def _det(label, conf=0.9):
    return {'label': label, 'confidence': conf}


class _FakeHost:
    """gate FSM 专用假宿主: 只提供 gate 依赖的四个成员."""

    def __init__(self):
        self.current_cycle_uuid = None
        self.cycle_start_time = None
        self.cycle_start_frame_pos = 0
        self.settle_calls = 0

    def _video_frame_pos(self):
        return 0

    def start_cycle(self):
        self.current_cycle_uuid = 'fake-uuid'

    def _settle_container_cycle(self):
        self.settle_calls += 1
        self.current_cycle_uuid = None


# ============================================================
# 1. build_container_gate 构建守门 (零差异)
# ============================================================

class TestBuildGate:
    def _build(self, cfg):
        from backend.api.source_custom_mix import build_container_gate
        return build_container_gate(cfg)

    def test_default_none(self):
        cfg = _project_cfg()
        cfg['pipeline_config'].pop('custom_cycle_owner')
        assert self._build(cfg) is None

    def test_owner_steps_none(self):
        cfg = _project_cfg(pipeline_extra={'custom_cycle_owner': 'steps'})
        assert self._build(cfg) is None

    def test_missing_label_falls_back(self):
        cfg = _project_cfg(pipeline_extra={'container_gate_label': ''})
        assert self._build(cfg) is None

    def test_non_custom_mode_none(self):
        cfg = _project_cfg(logic_mode='detection')
        assert self._build(cfg) is None

    def test_built_with_params(self):
        gate = self._build(_project_cfg())
        assert gate is not None
        assert gate.label == 'BOX'
        assert gate.appear_seconds == 1.0
        assert gate.gone_seconds == 3.0


# ============================================================
# 2. apply_project_config 挂载
# ============================================================

class TestApplyWiring:
    def test_mounted(self):
        vsm = _make_vsm()
        assert vsm._container_cycle_gate is not None
        assert vsm._custom_cycle_owner == 'container'

    def test_default_unmounted(self):
        vsm = _make_vsm(pipeline_extra={'custom_cycle_owner': 'steps'})
        assert vsm._container_cycle_gate is None
        assert vsm._custom_cycle_owner == 'steps'


# ============================================================
# 3. ContainerCycleGate FSM
# ============================================================

class TestGateFsm:
    def _gate(self, appear=1.0, gone=3.0):
        from backend.api.source_custom_mix import ContainerCycleGate
        return ContainerCycleGate('BOX', appear_seconds=appear, gone_seconds=gone)

    def test_appear_confirm_opens_cycle(self):
        gate, host = self._gate(), _FakeHost()
        gate.feed(host, [_det('BOX')], 0.0)
        assert not gate.present and host.current_cycle_uuid is None
        gate.feed(host, [_det('BOX')], 0.5)
        assert not gate.present
        gate.feed(host, [_det('BOX')], 1.0)
        assert gate.present and host.current_cycle_uuid == 'fake-uuid'
        assert host.cycle_start_time == 1.0

    def test_blip_no_open(self):
        gate, host = self._gate(), _FakeHost()
        gate.feed(host, [_det('BOX')], 0.0)
        gate.feed(host, [], 0.5)                 # 闪现即断: 候选清零
        gate.feed(host, [_det('BOX')], 0.6)
        gate.feed(host, [_det('BOX')], 1.2)      # 距新候选起点仅 0.6s
        assert not gate.present and host.current_cycle_uuid is None

    def test_low_conf_ignored(self):
        gate, host = self._gate(), _FakeHost()
        for t in (0.0, 0.6, 1.2):
            gate.feed(host, [_det('BOX', conf=0.2)], t)
        assert not gate.present

    def test_occlusion_bridged(self):
        gate, host = self._gate(), _FakeHost()
        for t in (0.0, 1.0):
            gate.feed(host, [_det('BOX')], t)
        assert gate.present
        gate.feed(host, [], 2.0)
        gate.feed(host, [], 3.5)                 # 缺席 2.5s < 3.0s
        gate.feed(host, [_det('BOX')], 4.0)      # 回来了
        assert gate.present and host.settle_calls == 0

    def test_departure_settles(self):
        gate, host = self._gate(), _FakeHost()
        for t in (0.0, 1.0):
            gate.feed(host, [_det('BOX')], t)
        assert host.current_cycle_uuid is not None
        gate.feed(host, [], 2.0)
        gate.feed(host, [], 4.5)                 # 距 last_seen=1.0 已 3.5s ≥ 3.0
        assert host.settle_calls == 1
        assert not gate.present

    def test_reopen_after_settle(self):
        gate, host = self._gate(), _FakeHost()
        for t in (0.0, 1.0):
            gate.feed(host, [_det('BOX')], t)
        gate.feed(host, [], 4.5)
        assert host.settle_calls == 1
        # 新箱子来
        gate.feed(host, [_det('BOX')], 10.0)
        gate.feed(host, [_det('BOX')], 11.0)
        assert gate.present and host.current_cycle_uuid == 'fake-uuid'


# ============================================================
# 4. _settle_container_cycle 完备判定 (based_on='detection')
# ============================================================

class TestSettleDetectionBased:
    def test_all_present_ok(self):
        vsm = _make_vsm()
        vsm.current_cycle_steps = ['B', 'A']       # 无序
        vsm._settle_container_cycle()
        assert vsm._test_events == [(1, '检测完成')]
        assert vsm.current_cycle_steps == []

    def test_missing_ng(self):
        vsm = _make_vsm()
        vsm.current_cycle_steps = ['A']
        vsm._settle_container_cycle()
        assert len(vsm._test_events) == 1
        event_id, reason = vsm._test_events[0]
        assert event_id == 2 and 'B' in reason and '缺少步骤' in reason

    def test_duplicates_tolerated(self):
        vsm = _make_vsm()
        vsm.current_cycle_steps = ['A', 'B', 'A']  # 重复不判 NG (容器定界刻意语义)
        vsm._settle_container_cycle()
        assert vsm._test_events == [(1, '检测完成')]

    def test_empty_cycle_ng_all_missing(self):
        vsm = _make_vsm()
        vsm.current_cycle_steps = []
        vsm._settle_container_cycle()
        event_id, reason = vsm._test_events[0]
        assert event_id == 2 and 'A' in reason and 'B' in reason

    def test_custom_detection_steps_subset(self):
        vsm = _make_vsm(pipeline_extra={'custom_detection_steps': [1]})
        vsm.current_cycle_steps = ['A']            # 只要求步骤 1 (A)
        vsm._settle_container_cycle()
        assert vsm._test_events == [(1, '检测完成')]


# ============================================================
# 5. _settle_container_cycle 纯 custom 分支
# ============================================================

class TestSettlePureCustom:
    def test_condition_matched(self):
        vsm = _make_vsm(pipeline_extra={
            'custom_based_on': None,
            'custom_conditions': [
                {'id': 1, 'priority': 1, 'sequence': [1, 2], 'event_id': 3},
            ],
        })
        vsm.current_cycle_steps = ['A', 'B']
        vsm._settle_container_cycle()
        assert len(vsm._test_events) == 1
        assert vsm._test_events[0][0] == 3        # 条件事件

    def test_condition_unmatched_falls_to_ng(self):
        vsm = _make_vsm(pipeline_extra={
            'custom_based_on': None,
            'custom_conditions': [
                {'id': 1, 'priority': 1, 'sequence': [1, 2], 'event_id': 3},
            ],
        })
        vsm.current_cycle_steps = ['A']
        vsm._settle_container_cycle()
        assert len(vsm._test_events) == 1
        assert vsm._test_events[0][0] == 2        # 未命中 → 中断事件缺省 2


# ============================================================
# 6. _check_events 让位 (容器主权下条件命中不当场结算)
# ============================================================

class TestEventsCheckYields:
    def test_condition_match_not_settled_midcycle(self):
        vsm = _make_vsm(pipeline_extra={
            'custom_based_on': None,
            'custom_conditions': [
                {'id': 1, 'priority': 1, 'sequence': [1, 2], 'event_id': 3},
            ],
        })
        vsm.current_cycle_steps = ['A', 'B']       # 恰好全等命中条件
        vsm._check_events('B')
        assert vsm._test_events == []              # 让位: 不结算
        assert vsm.current_cycle_steps == ['A', 'B']

    def test_steps_owner_still_settles(self):
        vsm = _make_vsm(pipeline_extra={
            'custom_cycle_owner': 'steps',
            'custom_based_on': None,
            'custom_conditions': [
                {'id': 1, 'priority': 1, 'sequence': [1, 2], 'event_id': 3},
            ],
        })
        vsm.current_cycle_steps = ['A', 'B']
        vsm._check_events('B')
        assert len(vsm._test_events) == 1          # 步骤主权: 现状行为不变
