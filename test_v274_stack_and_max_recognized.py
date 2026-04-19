"""
v2.7.4 集成测试：堆叠模式 + 最大识别数
直接复用 source.py 的 VideoSourceManager._update_tracking_stats 真实代码路径
不启动相机/模型，仅以伪检测列表驱动状态机。

运行：
    cd /home/qianqian/桌面/word/tianjun副本
    python test_v274_stack_and_max_recognized.py
"""
import sys
import os
import time
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from api.source import VideoSourceManager  # noqa: E402


def make_vsm(steps_config, pipeline_config=None):
    """构造一个最小可用的 VideoSourceManager 实例（不调用 __init__ 全流程）"""
    vsm = VideoSourceManager.__new__(VideoSourceManager)
    vsm._init_inference_vars()
    vsm.project_config = {
        'logic_mode': 'tracking',
        'steps_config': steps_config,
        'pipeline_config': pipeline_config or {
            'tracking_cycle_strategy': 'all_gone',
            'tracking_gone_threshold': 0,
            'tracking_gone_confirm_frames': 30,
        },
    }
    vsm.step_display_names = {s['label']: s.get('display_name', s['label']) for s in steps_config}
    vsm.start_cycle = MagicMock()
    vsm._trigger_event = MagicMock()
    vsm.cycle_start_time = None
    return vsm


def det(label, track_id, x=100, y=100, w=50, h=50, confidence=0.9):
    return {
        'label': label, 'track_id': track_id,
        'x': x, 'y': y, 'w': w, 'h': h,
        'confidence': confidence,
    }


class TestMaxRecognized(unittest.TestCase):
    """最大识别数 ID 后处理"""

    def test_no_limit_keeps_ids(self):
        steps = [{'label': 'glove', 'enabled': True, 'count_mode': 'track', 'max_recognized': 0}]
        vsm = make_vsm(steps)
        dets = [det('glove', 1), det('glove', 2), det('glove', 3)]
        vsm._update_tracking_stats(dets, None)
        self.assertEqual([d['track_id'] for d in dets], [1, 2, 3])

    def test_max_1_merges_to_highest_confidence(self):
        steps = [{'label': 'glove', 'enabled': True, 'count_mode': 'track', 'max_recognized': 1}]
        vsm = make_vsm(steps)
        dets = [
            det('glove', 1, x=100, y=100, confidence=0.5),
            det('glove', 2, x=200, y=200, confidence=0.95),  # winner
            det('glove', 3, x=300, y=300, confidence=0.7),
        ]
        vsm._update_tracking_stats(dets, None)
        self.assertEqual([d['track_id'] for d in dets], [2, 2, 2])

    def test_max_2_keeps_top_two_others_merged_to_nearest(self):
        steps = [{'label': 'glove', 'enabled': True, 'count_mode': 'track', 'max_recognized': 2}]
        vsm = make_vsm(steps)
        dets = [
            det('glove', 1, x=100, y=100, confidence=0.95),  # keeper #1 (high conf)
            det('glove', 2, x=500, y=500, confidence=0.90),  # keeper #2 (high conf)
            det('glove', 3, x=120, y=120, confidence=0.50),  # near keeper 1 -> 1
            det('glove', 4, x=480, y=480, confidence=0.40),  # near keeper 2 -> 2
        ]
        vsm._update_tracking_stats(dets, None)
        self.assertEqual([d['track_id'] for d in dets], [1, 2, 1, 2])

    def test_other_labels_unaffected(self):
        steps = [
            {'label': 'glove', 'enabled': True, 'count_mode': 'track', 'max_recognized': 1},
            {'label': 'box', 'enabled': True, 'count_mode': 'track', 'max_recognized': 0},
        ]
        vsm = make_vsm(steps)
        dets = [det('glove', 1, confidence=0.5), det('glove', 2, confidence=0.9),
                det('box', 10), det('box', 11)]
        vsm._update_tracking_stats(dets, None)
        glove_ids = [d['track_id'] for d in dets if d['label'] == 'glove']
        box_ids = [d['track_id'] for d in dets if d['label'] == 'box']
        self.assertEqual(glove_ids, [2, 2])
        self.assertEqual(box_ids, [10, 11])

    def test_event_mode_label_ignored(self):
        """count_mode=event 的 label 不参与最大识别数"""
        steps = [{'label': 'btn', 'enabled': True, 'count_mode': 'event',
                  'event_required_count': 1, 'max_recognized': 1}]
        vsm = make_vsm(steps)
        dets = [det('btn', 1, confidence=0.5), det('btn', 2, confidence=0.9)]
        vsm._update_tracking_stats(dets, None)
        # event 模式下不会改 track_id（这里 max_recognized 也不该生效）
        self.assertEqual([d['track_id'] for d in dets], [1, 2])

    def test_negative_track_id_skipped(self):
        steps = [{'label': 'glove', 'enabled': True, 'count_mode': 'track', 'max_recognized': 1}]
        vsm = make_vsm(steps)
        dets = [det('glove', -1, confidence=0.9), det('glove', 2, confidence=0.5)]
        vsm._update_tracking_stats(dets, None)
        # track_id<0 不参与合并；只剩 1 个有效，<=N，不变
        self.assertEqual([d['track_id'] for d in dets], [-1, 2])


class TestStackMode(unittest.TestCase):
    """堆叠模式状态机"""

    def _step(self, **overrides):
        s = {'label': 'item', 'enabled': True, 'count_mode': 'track',
             'stack_enabled': True, 'stack_reappear_seconds': 0.1,
             'stack_required_count': 3}
        s.update(overrides)
        return s

    def test_initial_appearance_counts_one(self):
        vsm = make_vsm([self._step()])
        vsm._update_tracking_stats([det('item', 1)], None)
        self.assertEqual(vsm._stack_counters['item'], 1)
        self.assertEqual(vsm._stack_state['item'], 'visible')

    def test_disappear_then_reappear_after_reappear_seconds_increments(self):
        vsm = make_vsm([self._step(stack_reappear_seconds=0.05)])
        vsm._update_tracking_stats([det('item', 1)], None)
        self.assertEqual(vsm._stack_counters['item'], 1)
        # 物品消失
        vsm._update_tracking_stats([], None)
        self.assertEqual(vsm._stack_state['item'], 'disappeared')
        self.assertEqual(vsm._stack_counters['item'], 1)
        # 等够秒数后再出现
        time.sleep(0.08)
        vsm._update_tracking_stats([det('item', 2)], None)
        self.assertEqual(vsm._stack_counters['item'], 2)
        self.assertEqual(vsm._stack_state['item'], 'visible')

    def test_disappear_then_reappear_too_fast_does_not_increment(self):
        vsm = make_vsm([self._step(stack_reappear_seconds=1.0)])
        vsm._update_tracking_stats([det('item', 1)], None)
        vsm._update_tracking_stats([], None)
        # 立刻再出现 — 0s < 1.0s
        vsm._update_tracking_stats([det('item', 2)], None)
        self.assertEqual(vsm._stack_counters['item'], 1)  # 仍是 1
        self.assertEqual(vsm._stack_state['item'], 'visible')

    def test_full_stack_three_layers(self):
        vsm = make_vsm([self._step(stack_reappear_seconds=0.02, stack_required_count=3)])
        # 第 1 层
        vsm._update_tracking_stats([det('item', 1)], None)
        self.assertEqual(vsm._stack_counters['item'], 1)
        # 第 2 层：消失再出现
        vsm._update_tracking_stats([], None)
        time.sleep(0.03)
        vsm._update_tracking_stats([det('item', 1)], None)
        self.assertEqual(vsm._stack_counters['item'], 2)
        # 第 3 层：再消失再出现
        vsm._update_tracking_stats([], None)
        time.sleep(0.03)
        vsm._update_tracking_stats([det('item', 1)], None)
        self.assertEqual(vsm._stack_counters['item'], 3)

    def test_checklist_uses_max_of_tracking_and_stack(self):
        """ByteTrack 同 ID 时，stack_counters 比 tracking_class_counters 大，应取 stack"""
        vsm = make_vsm([self._step(stack_reappear_seconds=0.02, stack_required_count=3)])
        # 第 1 层
        vsm._update_tracking_stats([det('item', 1)], None)
        # 第 2 层
        vsm._update_tracking_stats([], None)
        time.sleep(0.03)
        vsm._update_tracking_stats([det('item', 1)], None)
        # stack_counters=2, tracking_class_counters['item']=1（因 track_id 一直是 1）
        self.assertEqual(vsm._stack_counters['item'], 2)
        # 重建 checklist
        vsm._rebuild_checklist({'item': 3})
        self.assertEqual(vsm._tracking_item_checklist['item']['counted'], 2)
        self.assertEqual(vsm._tracking_item_checklist['item']['expected'], 3)

    def test_stack_disabled_does_not_count(self):
        steps = [self._step(stack_enabled=False)]
        vsm = make_vsm(steps)
        vsm._update_tracking_stats([det('item', 1)], None)
        vsm._update_tracking_stats([], None)
        time.sleep(0.05)
        vsm._update_tracking_stats([det('item', 1)], None)
        self.assertEqual(vsm._stack_counters.get('item', 0), 0)

    def test_stack_only_for_count_mode_track(self):
        """count_mode=event 下 stack 不应启用"""
        steps = [{'label': 'btn', 'enabled': True, 'count_mode': 'event',
                  'stack_enabled': True, 'stack_reappear_seconds': 0.05,
                  'stack_required_count': 2, 'event_required_count': 1}]
        vsm = make_vsm(steps)
        vsm._update_tracking_stats([det('btn', 1)], None)
        # stack_steps 在解析时被过滤掉
        self.assertEqual(vsm._stack_counters.get('btn', 0), 0)

    def test_required_count_min_2(self):
        """stack_required_count 最小为 2，配置 1 也会被强制为 2"""
        vsm = make_vsm([self._step(stack_required_count=1)])
        vsm._update_tracking_stats([det('item', 1)], None)
        vsm._rebuild_checklist({'item': 2})
        self.assertEqual(vsm._tracking_item_checklist['item']['expected'], 2)

    def test_reset_clears_stack_state(self):
        vsm = make_vsm([self._step()])
        vsm._update_tracking_stats([det('item', 1)], None)
        self.assertEqual(vsm._stack_counters['item'], 1)
        vsm._reset_counting_cycle()
        self.assertEqual(vsm._stack_counters, {})
        self.assertEqual(vsm._stack_state, {})


class TestCombined(unittest.TestCase):
    """组合：堆叠 + 最大识别数同时生效"""

    def test_stack_with_max_recognized_one(self):
        """同一 label 同时配置 stack + max_recognized=1。
        画面同时出现 2 个，被合并为同一 track_id；
        stack 状态机仍然把"该 label 可见"算作 visible，按正常堆叠节奏计数"""
        steps = [{
            'label': 'glove', 'enabled': True, 'count_mode': 'track',
            'stack_enabled': True, 'stack_reappear_seconds': 0.05,
            'stack_required_count': 2, 'max_recognized': 1,
        }]
        vsm = make_vsm(steps)
        # 同时出现 2 个 -> track_id 合并到高置信度的那个
        dets = [det('glove', 1, confidence=0.5), det('glove', 2, confidence=0.9)]
        vsm._update_tracking_stats(dets, None)
        self.assertEqual([d['track_id'] for d in dets], [2, 2])
        self.assertEqual(vsm._stack_counters['glove'], 1)
        # 消失再出现一次
        vsm._update_tracking_stats([], None)
        time.sleep(0.08)
        vsm._update_tracking_stats([det('glove', 5)], None)
        self.assertEqual(vsm._stack_counters['glove'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
