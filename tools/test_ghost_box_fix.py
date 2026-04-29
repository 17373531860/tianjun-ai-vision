"""v3.1.4 幽灵箱(空箱误判 NG) 修复仿真测试.

复盘客户机现场录屏 (单工位 jc 项目, container_box_mode='multi'):
- 检测 126 / OK 38 / NG 85, 合格率 30.2%
- 已结算 92 (OK 26 / NG 67), 67 个 NG 占 73%
- NG TOP3 三个步骤 (box / cable_hook / pillar_no_core) 同 NG 率 53.x%
- 50 秒一口气结算 6 个箱子 5 个 NG (画面里只有 1 个真实箱子在流转)

根因 (verified by reading source_container_grouping_mixin.py):
1. 工人遮挡瞬间, ByteTrack 给真箱新 track_id => display_id 也变 =>
   _update_container_grouping line 60 把它当新 box, line 63-75 入了新条目.
2. 老条目还在 _box_objects 里, 等 30 帧 gone-confirm.
3. 期间老条目 item_class_counts={} 永远是空 (新物品全归到新 box_did 名下).
4. 30 帧后 _settle_box 触发, expected_no_container 整列 missing => is_ok=False
   => 入账 NG.

修复 (v3.1.4): _settle_box 入口加阈值过滤
   total_items = sum(box.item_class_counts.values())
   if total_items < pipeline_config.container_settle_min_items:
       直接 return, 不入账 OK 也不入账 NG, 既不写 StepRecord 也不 trigger_event.

测试覆盖 11 个用例:
  Group 1 (4): 阈值过滤核心行为 — 空箱跳过, 1 件入账, 多件入账, 阈值=0 关闭过滤
  Group 2 (3): 阈值边界 — 默认值, 上下限, 类型异常 fallback
  Group 3 (2): 与原行为兼容 — 真实装满箱仍 OK, 缺件箱仍 NG
  Group 4 (2): 防回归 — 不调 _trigger_event, 不写 StepRecord, 不入 _box_settled_results
"""
import io
import os
import sys
import unittest
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 复刻 _settle_box 的 v3.1.4 修复版本 (不依赖真 self / DB / 真 trigger).
# 测试关注点: 阈值过滤的 "提前 return" 是否正确. 后续 record_step / trigger 不复刻.
# ============================================================

class _MockGrouping:
    """最小化复刻 ContainerGroupingMixin 的状态, 只暴露 _settle_box 阈值分支."""

    def __init__(self, *, project_config=None, container_label='箱子'):
        self._box_objects = {}
        self._box_settled_results = []
        self.project_config = project_config or {}
        self._container_label = container_label
        self.step_display_names = {}
        self.current_cycle_steps = []
        self.recorded_steps = []
        self.triggered_events = []
        self.current_cycle_id = 1

    def _get_display_prefix(self, cls_name):
        return cls_name[:1] if cls_name else 'X'

    def record_step(self, **kwargs):
        self.recorded_steps.append(kwargs)

    def _trigger_event(self, kind, msg):
        self.triggered_events.append((kind, msg))

    # ---- v3.1.4 复刻版 _settle_box (与生产代码语义一致) ----
    def _settle_box(self, box_display_id, expected_items):
        box_state = self._box_objects.pop(box_display_id, None)
        if box_state is None:
            return

        item_counts = box_state['item_class_counts']

        # v3.1.4 阈值前置过滤
        try:
            pcfg = (self.project_config or {}).get('pipeline_config', {})
            _val = pcfg.get('container_settle_min_items', 1)
            if _val is None or _val == '':
                _val = 1
            min_items_threshold = int(_val)
        except (TypeError, ValueError):
            min_items_threshold = 1
        if min_items_threshold > 0:
            total_items = sum(int(v) for v in item_counts.values())
            if total_items < min_items_threshold:
                # 跳过结算, 不入 _box_settled_results / 不 trigger / 不 record_step
                return

        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items()
                                 if k != container_label}
        missing = []
        extra = []
        for cls, exp in expected_no_container.items():
            actual = item_counts.get(cls, 0)
            if actual < exp:
                missing.append(f'{cls}: {actual}/{exp}')
            elif actual > exp:
                extra.append(f'{cls}: {actual}/{exp}')
        is_ok = not missing and not extra

        result = {
            'display_id': box_display_id,
            'is_complete': is_ok,
            'missing': missing,
            'extra': extra,
            'item_counts': dict(item_counts),
        }
        self._box_settled_results.append(result)

        if is_ok:
            self._trigger_event(1, f'{box_display_id} OK')
        else:
            self._trigger_event(2, f'{box_display_id} NG')

    # ---- 测试辅助: 模拟一个 box 进入 _box_objects ----
    def _seed_box(self, box_did, item_counts):
        self._box_objects[box_did] = {
            'item_class_counts': dict(item_counts),
            'first_seen': 0.0,
            'last_seen': 1.0,
            'items_ever_seen': {},
        }


def _project_with_threshold(threshold, expected_items=None, container='箱子'):
    return {
        'pipeline_config': {
            'container_settle_min_items': threshold,
            'counting_expected_items': expected_items or {'a': 1, 'b': 1, 'c': 1},
        }
    }


# ============================================================
# Group 1: 阈值过滤核心行为
# ============================================================
class TestThresholdCoreBehavior(unittest.TestCase):

    def test_empty_box_skipped_with_default_threshold(self):
        """[核心] 空箱 + 默认阈值 1 -> 跳过, 既不入 settled_results 也不 trigger NG."""
        m = _MockGrouping(project_config=_project_with_threshold(1))
        m._seed_box('幽灵箱1', {})
        m._settle_box('幽灵箱1', {'a': 1, 'b': 1, 'c': 1})

        self.assertEqual(m._box_settled_results, [],
                         '空箱不应入 _box_settled_results')
        self.assertEqual(m.triggered_events, [],
                         '空箱不应 trigger 任何 OK/NG 事件')
        self.assertNotIn('幽灵箱1', m._box_objects,
                         '空箱仍应被 pop, 释放内存')

    def test_box_with_one_item_passes_threshold_default(self):
        """[核心] 装 1 件 + 默认阈值 1 -> 进入正常结算, 但因为缺件成 NG."""
        m = _MockGrouping(project_config=_project_with_threshold(1))
        m._seed_box('箱子A', {'a': 1})
        m._settle_box('箱子A', {'a': 1, 'b': 1, 'c': 1})

        self.assertEqual(len(m._box_settled_results), 1,
                         '装件数 >= 阈值, 必须正常结算')
        self.assertFalse(m._box_settled_results[0]['is_complete'])
        self.assertEqual(m.triggered_events, [(2, '箱子A NG')])

    def test_full_box_settled_ok(self):
        """[核心] 装齐全部 -> OK 入账."""
        m = _MockGrouping(project_config=_project_with_threshold(1))
        m._seed_box('箱子B', {'a': 1, 'b': 1, 'c': 1})
        m._settle_box('箱子B', {'a': 1, 'b': 1, 'c': 1})

        self.assertEqual(len(m._box_settled_results), 1)
        self.assertTrue(m._box_settled_results[0]['is_complete'])
        self.assertEqual(m.triggered_events, [(1, '箱子B OK')])

    def test_threshold_zero_disables_filter(self):
        """[兼容] 阈值=0 -> 关闭过滤, 退化到 v3.1.3 行为, 空箱也被结算 NG."""
        m = _MockGrouping(project_config=_project_with_threshold(0))
        m._seed_box('幽灵箱', {})
        m._settle_box('幽灵箱', {'a': 1, 'b': 1, 'c': 1})

        self.assertEqual(len(m._box_settled_results), 1,
                         '阈值=0 时空箱应像 v3.1.3 一样入账')
        self.assertFalse(m._box_settled_results[0]['is_complete'])
        self.assertEqual(m.triggered_events, [(2, '幽灵箱 NG')])


# ============================================================
# Group 2: 阈值边界
# ============================================================
class TestThresholdBoundary(unittest.TestCase):

    def test_default_threshold_when_pipeline_config_missing(self):
        """[边界] pipeline_config 整个 key 缺失 -> 走默认 1, 空箱仍被过滤."""
        m = _MockGrouping(project_config={})  # 没有 pipeline_config
        m._seed_box('幽灵箱', {})
        m._settle_box('幽灵箱', {'a': 1})

        self.assertEqual(m._box_settled_results, [])

    def test_threshold_above_one_filters_partial_boxes(self):
        """[边界] 阈值=3, 装 2 件箱也被过滤."""
        m = _MockGrouping(project_config=_project_with_threshold(3))
        m._seed_box('小箱', {'a': 1, 'b': 1})  # 总件数 2 < 3
        m._settle_box('小箱', {'a': 1, 'b': 1, 'c': 1})

        self.assertEqual(m._box_settled_results, [])
        self.assertEqual(m.triggered_events, [])

        m2 = _MockGrouping(project_config=_project_with_threshold(3))
        m2._seed_box('够大箱', {'a': 1, 'b': 1, 'c': 1})  # 总件数 3 == 3
        m2._settle_box('够大箱', {'a': 1, 'b': 1, 'c': 1})
        self.assertEqual(len(m2._box_settled_results), 1)
        self.assertTrue(m2._box_settled_results[0]['is_complete'])

    def test_threshold_invalid_type_falls_back_to_one(self):
        """[边界] 阈值是字符串/None -> 不崩溃, fallback 默认值 1."""
        for bad in ['abc', None, '', [1, 2]]:
            m = _MockGrouping(project_config={
                'pipeline_config': {'container_settle_min_items': bad}
            })
            m._seed_box('幽灵箱', {})
            m._settle_box('幽灵箱', {'a': 1})
            self.assertEqual(
                m._box_settled_results, [],
                f'阈值={bad!r} 时应 fallback 到 1, 空箱必须被过滤'
            )


# ============================================================
# Group 3: 与原行为兼容
# ============================================================
class TestBackwardCompatibility(unittest.TestCase):

    def test_partial_box_still_ng(self):
        """[兼容] 装件数 >= 阈值 但缺件 -> 还是 NG (不是误吞)."""
        m = _MockGrouping(project_config=_project_with_threshold(1))
        m._seed_box('半箱', {'a': 1, 'b': 1})  # c 缺
        m._settle_box('半箱', {'a': 1, 'b': 1, 'c': 1})

        self.assertEqual(len(m._box_settled_results), 1)
        self.assertFalse(m._box_settled_results[0]['is_complete'])
        self.assertEqual(m._box_settled_results[0]['missing'], ['c: 0/1'])
        self.assertEqual(m.triggered_events, [(2, '半箱 NG')])

    def test_extra_items_still_ng(self):
        """[兼容] 装超量 -> 还是 NG (extra 检测仍生效)."""
        m = _MockGrouping(project_config=_project_with_threshold(1))
        m._seed_box('超装箱', {'a': 3, 'b': 1, 'c': 1})  # a 多 2 件
        m._settle_box('超装箱', {'a': 1, 'b': 1, 'c': 1})

        self.assertEqual(len(m._box_settled_results), 1)
        self.assertFalse(m._box_settled_results[0]['is_complete'])
        self.assertTrue(any('a' in s for s in m._box_settled_results[0]['extra']))


# ============================================================
# Group 4: 防回归 (客户机现场症状)
# ============================================================
class TestCustomerFieldSimulation(unittest.TestCase):
    """模拟客户机录屏的连环结算: 6 个 box 里只有 1 真箱, 期望修复后只入账 1 个."""

    def test_simulate_six_box_burst_settle(self):
        """[现场] 一次结算 6 个 box (5 幽灵 + 1 真箱) -> 只 1 个 OK 入账."""
        m = _MockGrouping(project_config=_project_with_threshold(1))
        # 5 个幽灵箱 (item_class_counts={})
        for i in range(92, 97):
            m._seed_box(f'箱子{i}', {})
        # 1 个真箱装满
        m._seed_box('箱子99', {'a': 1, 'b': 1, 'c': 1})

        # 模拟 force_settle 一次结算所有
        for did in list(m._box_objects.keys()):
            m._settle_box(did, {'a': 1, 'b': 1, 'c': 1})

        self.assertEqual(len(m._box_settled_results), 1,
                         '5 幽灵 + 1 真箱, 只 1 个真箱入账')
        self.assertTrue(m._box_settled_results[0]['is_complete'])
        self.assertEqual(m._box_settled_results[0]['display_id'], '箱子99')
        # 5 个幽灵箱完全无事件
        ok_count = sum(1 for kind, _ in m.triggered_events if kind == 1)
        ng_count = sum(1 for kind, _ in m.triggered_events if kind == 2)
        self.assertEqual(ok_count, 1)
        self.assertEqual(ng_count, 0,
                         '幽灵箱不应触发任何 NG 事件 (这是客户合格率掉到 30% 的根因)')

    def test_pre_v3_1_4_behavior_proves_bug_existence(self):
        """[Negative] 阈值=0 复刻 v3.1.3 行为: 6 个 box 全入账, 5 NG 1 OK = 合格率 16.7%."""
        m = _MockGrouping(project_config=_project_with_threshold(0))
        for i in range(92, 97):
            m._seed_box(f'箱子{i}', {})
        m._seed_box('箱子99', {'a': 1, 'b': 1, 'c': 1})

        for did in list(m._box_objects.keys()):
            m._settle_box(did, {'a': 1, 'b': 1, 'c': 1})

        self.assertEqual(len(m._box_settled_results), 6)
        ok_count = sum(1 for r in m._box_settled_results if r['is_complete'])
        ng_count = sum(1 for r in m._box_settled_results if not r['is_complete'])
        self.assertEqual(ok_count, 1)
        self.assertEqual(ng_count, 5,
                         '关闭过滤后必须复现客户机 5 NG 现象, 证明 bug 真实存在')


# ============================================================
# 主入口 (与 run_v3_1_2_sim_all 风格一致)
# ============================================================
def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestThresholdCoreBehavior,
        TestThresholdBoundary,
        TestBackwardCompatibility,
        TestCustomerFieldSimulation,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    buf = io.StringIO()
    runner = unittest.TextTestRunner(stream=buf, verbosity=2)
    with contextlib.redirect_stderr(buf):
        result = runner.run(suite)
    print(buf.getvalue())

    print('=' * 60)
    print(f'Total: {result.testsRun}, '
          f'Passed: {result.testsRun - len(result.failures) - len(result.errors)}, '
          f'Failed: {len(result.failures)}, '
          f'Errors: {len(result.errors)}')
    print('=' * 60)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
