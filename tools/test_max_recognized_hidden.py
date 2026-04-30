"""v3.2.1: max_recognized 两条修复仿真测试.

复盘 JC1 客户端 (08:54): 画面上同时出现两个独立物理纸箱, 都被显示成 "箱子1"
(conf 分别 69%/72%). 用户用的是 container_box_mode='multi' 期望多箱并行,
但 box step 配了 max_recognized=1, 强制把两个真实箱合并到同一 track_id,
画面 + 容器清点都只剩一个"箱1", 物品被错归到同一个 _box_objects 条目。

v3.2.1 修复 (两条):
  1) 容器模式下 container_label 自动从 max_recognized 限制里剔除
     (跟 container_box_mode='multi' 语义冲突, 多箱并存交给 container_box_mode 管)。
  2) 非容器类被合并到 keeper 的多余 detection 打 hidden=True, 前端
     drawDetections / drawMultiDetections 跳过画框, 避免"同一物品被模型分裂识别
     -> 画面两个共享 display_id 的重影框"。
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Mock:
    """最小化复刻 _tracking_apply_max_recognized 实例方法, 不依赖 backend 上下文.
    container_label='' / container_mode=False 时跟非容器场景行为一致。"""

    def __init__(self, *, container_mode=False, container_label=''):
        self._container_mode = container_mode
        self._container_label = container_label
        self._warned_container_max_recognized = False

    def apply(self, detections: list, max_recognized_per_label: dict):
        """跟 source_tracking_mixin._tracking_apply_max_recognized 完全一致."""
        if not max_recognized_per_label:
            return

        container_label = ''
        if getattr(self, '_container_mode', False):
            container_label = getattr(self, '_container_label', '')
        if container_label and container_label in max_recognized_per_label:
            if not getattr(self, '_warned_container_max_recognized', False):
                self._warned_container_max_recognized = True
            max_recognized_per_label = {
                k: v for k, v in max_recognized_per_label.items() if k != container_label
            }
        if not max_recognized_per_label:
            return

        from collections import defaultdict as _dd
        _by_label = _dd(list)
        for _det in detections:
            _lbl = _det.get('label', '')
            if _lbl in max_recognized_per_label and _det.get('track_id', -1) >= 0:
                _by_label[_lbl].append(_det)
        for _lbl, _dets in _by_label.items():
            _N = max_recognized_per_label[_lbl]
            if len(_dets) <= _N:
                continue
            _dets_sorted = sorted(_dets, key=lambda d: -float(d.get('confidence', 0) or 0))
            _keepers = _dets_sorted[:_N]
            _keeper_ids = {id(k) for k in _keepers}
            for _d in _dets:
                if id(_d) in _keeper_ids:
                    continue
                _cx = _d.get('x', 0) + _d.get('w', 0) / 2
                _cy = _d.get('y', 0) + _d.get('h', 0) / 2
                _best_k = None
                _best_dist = float('inf')
                for _k in _keepers:
                    _kcx = _k.get('x', 0) + _k.get('w', 0) / 2
                    _kcy = _k.get('y', 0) + _k.get('h', 0) / 2
                    _dist = (_cx - _kcx) ** 2 + (_cy - _kcy) ** 2
                    if _dist < _best_dist:
                        _best_dist = _dist
                        _best_k = _k
                if _best_k is not None:
                    _d['track_id'] = _best_k.get('track_id', _d.get('track_id', -1))
                    _d['hidden'] = True


class TestContainerLabelExclusion(unittest.TestCase):
    """v3.2.1 修复 1: 容器模式下 container_label 自动剔除 max_recognized 限制."""

    def test_jc1_two_real_boxes_kept_independent(self):
        """[复盘 JC1] container_box_mode='multi' + 两个真实物理箱 + box.max_recognized=1.
           -> 自动剔除 box 限制, 两个箱保留各自独立 track_id, 不被强制合并。
        """
        m = _Mock(container_mode=True, container_label='box')
        dets = [
            {'label': 'box', 'track_id': 10, 'confidence': 0.72,
             'x': 0.55, 'y': 0.4, 'w': 0.15, 'h': 0.2},  # 右边箱
            {'label': 'box', 'track_id': 11, 'confidence': 0.69,
             'x': 0.10, 'y': 0.4, 'w': 0.15, 'h': 0.2},  # 左边箱 (不重叠)
        ]
        m.apply(dets, {'box': 1, 'foot': 1})

        for d in dets:
            self.assertNotIn('hidden', d,
                             '容器类被剔除, 多余 detection 不应被 hidden')
        self.assertEqual({d['track_id'] for d in dets}, {10, 11},
                         '两个箱保留各自独立 track_id, 不被强制合并')
        self.assertTrue(m._warned_container_max_recognized,
                        '应触发一次性 warning')

    def test_non_container_label_still_limited(self):
        """[修复 1] 容器模式下, 非容器 label 仍按 max_recognized 限制 (不受影响)."""
        m = _Mock(container_mode=True, container_label='box')
        dets = [
            {'label': 'foot', 'track_id': 1, 'confidence': 0.9,
             'x': 0.1, 'y': 0.1, 'w': 0.05, 'h': 0.05},
            {'label': 'foot', 'track_id': 2, 'confidence': 0.6,
             'x': 0.12, 'y': 0.12, 'w': 0.05, 'h': 0.05},
        ]
        m.apply(dets, {'box': 1, 'foot': 1})

        keeper = next(d for d in dets if d['confidence'] == 0.9)
        loser = next(d for d in dets if d['confidence'] == 0.6)
        self.assertNotIn('hidden', keeper)
        self.assertTrue(loser.get('hidden'), 'foot (非容器) 仍受 max_recognized=1 限制')

    def test_non_container_mode_box_still_limited(self):
        """[兼容] 非容器模式下 (container_mode=False), 'box' label 仍按
           max_recognized 限制 (老行为不变)。"""
        m = _Mock(container_mode=False)
        dets = [
            {'label': 'box', 'track_id': 1, 'confidence': 0.9,
             'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1},
            {'label': 'box', 'track_id': 2, 'confidence': 0.8,
             'x': 0.12, 'y': 0.1, 'w': 0.1, 'h': 0.1},
        ]
        m.apply(dets, {'box': 1})
        loser = next(d for d in dets if d['confidence'] == 0.8)
        self.assertTrue(loser.get('hidden'),
                        '非容器模式下 box 仍按用户配置走老逻辑')


class TestNonContainerHidden(unittest.TestCase):
    """v3.2.1 修复 2: 非容器类多余 detection 打 hidden=True."""

    def test_split_recognition_loser_hidden(self):
        """[修复 2] 非容器物品被模型分裂成两个 detection -> conf 低的打 hidden, 画面只剩 1 个."""
        m = _Mock()  # 非容器模式
        dets = [
            {'label': 'pillar_with_core', 'track_id': 10, 'confidence': 0.85,
             'x': 0.4, 'y': 0.4, 'w': 0.05, 'h': 0.1},
            {'label': 'pillar_with_core', 'track_id': 11, 'confidence': 0.55,
             'x': 0.42, 'y': 0.4, 'w': 0.05, 'h': 0.1},  # 重叠分裂
        ]
        m.apply(dets, {'pillar_with_core': 1})

        keeper = next(d for d in dets if d['confidence'] == 0.85)
        loser = next(d for d in dets if d['confidence'] == 0.55)
        self.assertNotIn('hidden', keeper)
        self.assertTrue(loser.get('hidden'))
        self.assertEqual(loser['track_id'], 10,
                         '多余 det.track_id 改成 keeper, 让 tracking 内部稳定')

    def test_n_equals_2_keep_top_2_hide_rest(self):
        """[修复 2] max_recognized=2 时保留 conf 最高的 2 个, 第 3 个被 hidden."""
        m = _Mock()
        dets = [
            {'label': 'cable_hook', 'track_id': 1, 'confidence': 0.9,
             'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.1},
            {'label': 'cable_hook', 'track_id': 2, 'confidence': 0.8,
             'x': 0.5, 'y': 0.5, 'w': 0.1, 'h': 0.1},
            {'label': 'cable_hook', 'track_id': 3, 'confidence': 0.6,
             'x': 0.55, 'y': 0.55, 'w': 0.1, 'h': 0.1},  # 距离 keeper#2 最近
        ]
        m.apply(dets, {'cable_hook': 2})

        kept = [d for d in dets if not d.get('hidden')]
        hidden = [d for d in dets if d.get('hidden')]
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0]['track_id'], 2)


class TestBoundary(unittest.TestCase):
    """边界与兼容."""

    def test_within_limit_no_hidden(self):
        """[边界] detection 数 ≤ max_recognized -> 不动, 不打 hidden."""
        m = _Mock()
        dets = [
            {'label': 'foot', 'track_id': 5, 'confidence': 0.9,
             'x': 0.1, 'y': 0.1, 'w': 0.05, 'h': 0.05},
        ]
        m.apply(dets, {'foot': 1})
        self.assertNotIn('hidden', dets[0])

    def test_other_labels_not_affected(self):
        """[边界] 没配 max_recognized 的 label 不受影响."""
        m = _Mock()
        dets = [
            {'label': 'foot', 'track_id': 1, 'confidence': 0.9,
             'x': 0.1, 'y': 0.1, 'w': 0.05, 'h': 0.05},
            {'label': 'foot', 'track_id': 2, 'confidence': 0.6,
             'x': 0.12, 'y': 0.1, 'w': 0.05, 'h': 0.05},
            {'label': 'cable_hook', 'track_id': 3, 'confidence': 0.7,
             'x': 0.5, 'y': 0.5, 'w': 0.05, 'h': 0.05},
            {'label': 'cable_hook', 'track_id': 4, 'confidence': 0.6,
             'x': 0.51, 'y': 0.51, 'w': 0.05, 'h': 0.05},
        ]
        m.apply(dets, {'foot': 1})  # 只限制 foot

        foot_hidden = [d for d in dets if d['label'] == 'foot' and d.get('hidden')]
        hook_hidden = [d for d in dets if d['label'] == 'cable_hook' and d.get('hidden')]
        self.assertEqual(len(foot_hidden), 1)
        self.assertEqual(len(hook_hidden), 0)

    def test_negative_track_id_skipped(self):
        """[边界] track_id < 0 (未跟踪上的 raw det) 不参与."""
        m = _Mock()
        dets = [
            {'label': 'foot', 'track_id': -1, 'confidence': 0.9,
             'x': 0.1, 'y': 0.1, 'w': 0.05, 'h': 0.05},
            {'label': 'foot', 'track_id': -1, 'confidence': 0.8,
             'x': 0.5, 'y': 0.5, 'w': 0.05, 'h': 0.05},
        ]
        m.apply(dets, {'foot': 1})
        self.assertFalse(any(d.get('hidden') for d in dets))


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestContainerLabelExclusion, TestNonContainerHidden, TestBoundary]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    buf = io.StringIO()
    runner = unittest.TextTestRunner(stream=buf, verbosity=2)
    result = runner.run(suite)
    print(buf.getvalue())
    print(f"\n{'='*60}\n总计: {result.testsRun} 测试, "
          f"成功: {result.testsRun - len(result.failures) - len(result.errors)}, "
          f"失败: {len(result.failures)}, 错误: {len(result.errors)}\n{'='*60}")
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
