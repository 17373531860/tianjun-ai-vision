"""v3.1.2 杂项修复仿真测试.

把现有 5 个仿真脚本没覆盖的 3 处修复补齐:
  1. KeyError 防御 — _update_container_grouping 内 _settle_box → _end_cycle
     会清空 _box_objects, 下一轮迭代 box_did 必须重判 key, 否则 KeyError.
  2. tracking_match_thresh 注入 — _generate_custom_tracker_yaml 必须把
     pipeline_config['tracking_match_thresh'] 写进 yaml, 并 clamp 到 [0.1, 0.99].
  3. 录制入队异常不被吞 — _enqueue_frame_for_recording 必须:
        * frame=None 直接返回, 不抛
        * 程序性错误 (frame 没 .shape) 时, 首次打印, 后续静默, drop_count +1
        * 整体绝不向外抛异常 (否则会卡死捕获主循环)

合计 3 组共 9 个用例, 全部 mock 出来跑, 不依赖真相机/真模型/真 ffmpeg.
"""
import io
import os
import sys
import unittest
import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# Group 1: container_grouping KeyError 防御
# ============================================================
class TestContainerGroupingKeyErrorGuard(unittest.TestCase):
    """复刻 source_container_grouping_mixin.py L191-194 的防御性循环.

    生产代码:
        for box_did in list(self._box_objects.keys()):
            if box_did not in self._box_objects:
                continue       # ← v3.1.2 加的这一行
            bs = self._box_objects[box_did]
            ...
    去掉 'continue' 那行就会复现客户机日志里的 KeyError: '泡沫槽3'
    """

    def _safe_iterate(self, box_objects, settle_fn):
        """模拟修复后的 v3.1.2 循环."""
        for box_did in list(box_objects.keys()):
            if box_did not in box_objects:
                continue
            _ = box_objects[box_did]
            settle_fn(box_did, box_objects)

    def _unsafe_iterate(self, box_objects, settle_fn):
        """模拟 v3.1.1 的 buggy 循环 (没有重判 key)."""
        for box_did in list(box_objects.keys()):
            _ = box_objects[box_did]   # 这一行就是 v3.1.1 抛 KeyError 的地方
            settle_fn(box_did, box_objects)

    def test_unsafe_iterate_reproduces_keyerror(self):
        """[Negative] 不加 'continue' 的版本必然抛 KeyError, 证明 bug 真实存在."""
        boxes = {'箱子1': {}, '箱子2': {}, '箱子3': {}}

        def evil_settle(box_did, store):
            # 模拟 _settle_box -> _end_cycle 把整个 dict 清空
            store.clear()

        with self.assertRaises(KeyError):
            self._unsafe_iterate(boxes, evil_settle)

    def test_safe_iterate_survives_full_clear(self):
        """[Fix] _settle_box 清空整个 _box_objects, 后续循环必须 graceful 跳过."""
        boxes = {'箱子1': {}, '箱子2': {}, '箱子3': {}}
        settled = []

        def evil_settle(box_did, store):
            settled.append(box_did)
            store.clear()
        self._safe_iterate(boxes, evil_settle)
        self.assertEqual(settled, ['箱子1'])

    def test_safe_iterate_partial_pop(self):
        """前一轮 settle 只删掉自己的 key, 后续 box 仍能被处理."""
        boxes = {'a': {}, 'b': {}, 'c': {}}
        settled = []

        def selective_settle(box_did, store):
            settled.append(box_did)
            store.pop(box_did, None)
        self._safe_iterate(boxes, selective_settle)
        self.assertEqual(settled, ['a', 'b', 'c'])

    def test_safe_iterate_real_module_has_guard(self):
        """[源文件审计] 真源码必须包含 'if box_did not in self._box_objects: continue'."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'backend/api/source_container_grouping_mixin.py')
        with open(path, 'r', encoding='utf-8') as f:
            src = f.read()
        self.assertIn('if box_did not in self._box_objects:', src,
                      'v3.1.2 KeyError 防御 (continue) 不见了, 客户机日志会再次复现 KeyError!')


# ============================================================
# Group 2: tracking_match_thresh 注入到 bytetrack yaml
# ============================================================
class TestMatchThreshInjection(unittest.TestCase):
    """直接 import VideoSourceManager 并调 _generate_custom_tracker_yaml,
    验证 yaml 内容包含正确的 match_thresh."""

    def setUp(self):
        from backend.api.source import VideoSourceManager
        # 不走 __init__, 用 SimpleNamespace 模拟一个最小宿主, 然后把 method bind 上来
        self.method = VideoSourceManager._generate_custom_tracker_yaml

    def _build_stub(self, pipeline_config, steps_config=None):
        stub = SimpleNamespace()
        stub.project_config = {'steps_config': steps_config or []}
        stub.fps_inference = 15
        stub._custom_tracker_yaml = None
        return stub

    def _read_yaml(self, stub):
        with open(stub._custom_tracker_yaml, 'r') as f:
            return f.read()

    def test_default_match_thresh_is_0_8(self):
        """无配置时默认 0.8 (ByteTrack 官方默认)."""
        stub = self._build_stub({})
        with contextlib.redirect_stdout(io.StringIO()):
            self.method(stub, {})
        yaml = self._read_yaml(stub)
        self.assertIn('match_thresh: 0.8', yaml)

    def test_custom_match_thresh_passes_through(self):
        """用户填 0.5 必须直接生效."""
        stub = self._build_stub({})
        with contextlib.redirect_stdout(io.StringIO()):
            self.method(stub, {'tracking_match_thresh': 0.5})
        yaml = self._read_yaml(stub)
        self.assertIn('match_thresh: 0.5', yaml)

    def test_match_thresh_clamp_lower(self):
        """超低值 (0.05) 应 clamp 到 0.1."""
        stub = self._build_stub({})
        with contextlib.redirect_stdout(io.StringIO()):
            self.method(stub, {'tracking_match_thresh': 0.05})
        yaml = self._read_yaml(stub)
        self.assertIn('match_thresh: 0.1', yaml)

    def test_match_thresh_clamp_upper(self):
        """超高值 (1.5) 应 clamp 到 0.99."""
        stub = self._build_stub({})
        with contextlib.redirect_stdout(io.StringIO()):
            self.method(stub, {'tracking_match_thresh': 1.5})
        yaml = self._read_yaml(stub)
        self.assertIn('match_thresh: 0.99', yaml)

    def test_match_thresh_invalid_falls_back_to_default(self):
        """非法字符串 (例 'abc') 应 fallback 到 0.8, 不能崩."""
        stub = self._build_stub({})
        with contextlib.redirect_stdout(io.StringIO()):
            self.method(stub, {'tracking_match_thresh': 'abc'})
        yaml = self._read_yaml(stub)
        self.assertIn('match_thresh: 0.8', yaml)

    def tearDown(self):
        # 清掉测试中产生的临时 yaml
        try:
            import tempfile, glob
            for p in glob.glob(os.path.join(tempfile.gettempdir(), 'bytetrack_custom_*.yaml')):
                os.remove(p)
        except Exception:
            pass


# ============================================================
# Group 3: 录制入队异常不被吞
# ============================================================
class TestRecordingEnqueueExceptionVisibility(unittest.TestCase):
    """v3.1.1 那次 0-frame bug 复盘:
    程序性错误 (FFmpegRecorder 没 import → NameError) 被一个超大的 except Exception
    吞掉, 整 45 分钟全程 0 帧, 没人发现.
    v3.1.2 修复: 至少要打印一次警告, 后续也只是静默 drop, 但函数本身 *绝不* 向外抛.
    """

    def setUp(self):
        from backend.api.source_recording_thread_mixin import RecordingThreadMixin
        self.method = RecordingThreadMixin._enqueue_frame_for_recording
        # 最小 host stub
        self.stub = MagicMock()
        self.stub._recording_running = True
        self.stub._recording_drop_count = 0
        self.stub._recording_prep_warned = False
        self.stub._recording_queue_warned = False

    def test_none_frame_silent_return(self):
        """frame=None 应直接 return, 不计 drop."""
        self.method(self.stub, None)
        self.assertEqual(self.stub._recording_drop_count, 0)

    def test_disabled_recording_silent_return(self):
        """recording_running=False 时直接返回, 不入队不报错."""
        self.stub._recording_running = False
        self.method(self.stub, MagicMock())
        self.assertEqual(self.stub._recording_drop_count, 0)

    def test_bad_frame_does_not_propagate(self):
        """frame.shape 抛 AttributeError 时函数必须吞掉, 不能向外抛."""
        bad_frame = SimpleNamespace()  # 没 .shape
        try:
            self.method(self.stub, bad_frame)
        except Exception as e:
            self.fail(f"_enqueue_frame_for_recording 不应抛异常, 但抛了: {type(e).__name__}: {e}")

    def test_bad_frame_warns_once_and_counts_drop(self):
        """第一次坏 frame: 打印警告 + drop+1; 后续坏 frame 静默 drop."""
        bad_frame = SimpleNamespace()
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            self.method(self.stub, bad_frame)   # 第一次, 应打印
            self.method(self.stub, bad_frame)   # 第二次, 静默
            self.method(self.stub, bad_frame)   # 第三次, 静默
        out = captured.getvalue()
        # 只该出现一次警告
        self.assertEqual(out.count("帧预处理失败"), 1, f"应只警告一次, 实际:\n{out}")
        self.assertTrue(self.stub._recording_prep_warned)
        # 3 次都计 drop
        self.assertEqual(self.stub._recording_drop_count, 3)


# ============================================================
# Entry
# ============================================================
def main():
    suites = [
        unittest.TestLoader().loadTestsFromTestCase(TestContainerGroupingKeyErrorGuard),
        unittest.TestLoader().loadTestsFromTestCase(TestMatchThreshInjection),
        unittest.TestLoader().loadTestsFromTestCase(TestRecordingEnqueueExceptionVisibility),
    ]
    runner = unittest.TextTestRunner(verbosity=2)
    failures = 0
    for s in suites:
        result = runner.run(s)
        failures += len(result.failures) + len(result.errors)
    print()
    if failures:
        print(f"=== 失败 {failures} 个 ===")
        sys.exit(1)
    else:
        print("=== v3.1.2 杂项修复 全部通过 ===")


if __name__ == '__main__':
    main()
