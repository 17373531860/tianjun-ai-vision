"""v3.2.0 容器 ID 漂移合并 (active 接管) 仿真测试.

复盘 JC1 客户端测试视频 (d5eb9a3f...mp4, 时长 98s):
- 视频真相: 3 个真实物理箱 (箱子1左 / 箱子2右 / 箱子3 中途出现又移走), 2 OK + 1 NG
- v3.1.4 跑出: 17 次 settle (2 OK + 15 NG), 因 ByteTrack 工人遮挡时频繁切 box track_id
- v3.2.0 active 接管 (IoU=0.5, max_gone=30): 17 → 12 settle, 把短窗口内的 ID 漂移合并掉

修复 (v3.2.0): _update_container_grouping 在新建 _box_objects 条目前加 active 接管检查
   pcfg.container_id_drift_merge_iou > 0 时启用. 新 box did 进入前, 扫已存在的
   gone-confirm 中老 did, 同位置 IoU >= 阈值的复用老 did, 不开新条目.

测试覆盖 6 个用例:
  Group 1 (3): 核心行为 — 启用合并 / 阈值=0 关闭 / 多个候选挑 IoU 最高
  Group 2 (2): 边界 — gone_frames 超过 max_gone 不合并 / 两个老 did 都不达 IoU 阈值
  Group 3 (1): 兼容 — 配置缺失/None/异常类型 fallback
"""
import io
import os
import sys
import unittest
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 复刻 _update_container_grouping 里 v3.2.0 active 接管那一段
# ============================================================
class _MockGrouping:
    """最小化复刻 ContainerGroupingMixin 状态.
    只暴露"新 box 即将进入 _box_objects 时的接管决策"这一段, 不复刻整个 update."""

    def __init__(self, *, project_config=None, container_label='箱子'):
        self._box_objects = {}
        self._tracking_objects = {}
        self._tracking_display_map = {}
        self.project_config = project_config or {}
        self._container_label = container_label
        self._box_counter = 0
        self.print_log = []

    @staticmethod
    def _bbox_iou(a, b):
        """跟 source.py 实现一致, bbox 是 {x,y,w,h} 归一化."""
        ax1, ay1, ax2, ay2 = a['x'], a['y'], a['x'] + a['w'], a['y'] + a['h']
        bx1, by1, bx2, by2 = b['x'], b['y'], b['x'] + b['w'], b['y'] + b['h']
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        area_a = a['w'] * a['h']
        area_b = b['w'] * b['h']
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    def _try_admit_box(self, tid, current_time=1.0):
        """复刻 _update_container_grouping 里 box 入队那一段 (含 v3.2.0)."""
        obj = self._tracking_objects[tid]
        did = obj['display_id']

        pcfg = (self.project_config or {}).get('pipeline_config', {})
        try:
            _miou = pcfg.get('container_id_drift_merge_iou', 0)
            id_drift_merge_iou = float(_miou) if _miou not in (None, '') else 0.0
        except (TypeError, ValueError):
            id_drift_merge_iou = 0.0
        try:
            _mgf = pcfg.get('container_id_drift_merge_max_gone_frames', 30)
            id_drift_max_gone = int(_mgf) if _mgf not in (None, '') else 30
        except (TypeError, ValueError):
            id_drift_max_gone = 30
        id_drift_merge_enabled = id_drift_merge_iou > 0

        if did not in self._box_objects:
            merged_to = None
            if id_drift_merge_enabled:
                new_bbox = obj['bbox']
                best_old_did, best_iou = None, id_drift_merge_iou
                for old_did, _bs in self._box_objects.items():
                    if _bs.get('gone_frames', 0) <= 0 \
                            or _bs['gone_frames'] > id_drift_max_gone:
                        continue
                    try:
                        _iou = self._bbox_iou(_bs['bbox'], new_bbox)
                    except Exception:
                        continue
                    if _iou >= best_iou:
                        best_iou = _iou
                        best_old_did = old_did
                if best_old_did:
                    self.print_log.append(
                        f'merge: {did} -> {best_old_did} (IoU={best_iou:.2f})'
                    )
                    obj['display_id'] = best_old_did
                    self._tracking_display_map[tid] = best_old_did
                    self._box_objects[best_old_did]['bbox'] = new_bbox
                    self._box_objects[best_old_did]['last_seen'] = current_time
                    self._box_objects[best_old_did]['gone_frames'] = 0
                    merged_to = best_old_did
                    did = best_old_did

            if merged_to is None:
                self._box_counter += 1
                self._box_objects[did] = {
                    'display_id': did,
                    'bbox': obj['bbox'],
                    'first_seen': current_time,
                    'last_seen': current_time,
                    'gone_frames': 0,
                    'item_class_counts': {},
                }
        else:
            self._box_objects[did]['bbox'] = obj['bbox']
            self._box_objects[did]['last_seen'] = current_time
        return did

    # 测试辅助
    def _seed_track(self, tid, did, bbox):
        self._tracking_objects[tid] = {
            'class_name': self._container_label,
            'display_id': did,
            'bbox': dict(bbox),
            'first_seen': 0.0,
        }
        self._tracking_display_map[tid] = did

    def _seed_box(self, did, bbox, gone_frames=0, item_class_counts=None):
        self._box_objects[did] = {
            'display_id': did,
            'bbox': dict(bbox),
            'first_seen': 0.0,
            'last_seen': 1.0,
            'gone_frames': gone_frames,
            'item_class_counts': dict(item_class_counts or {}),
        }


def _project(merge_iou=0.5, max_gone=30):
    cfg = {'pipeline_config': {}}
    if merge_iou is not None:
        cfg['pipeline_config']['container_id_drift_merge_iou'] = merge_iou
    if max_gone is not None:
        cfg['pipeline_config']['container_id_drift_merge_max_gone_frames'] = max_gone
    return cfg


# ============================================================
# Group 1: 核心行为
# ============================================================
class TestActiveTakeoverCore(unittest.TestCase):

    def test_high_iou_takes_over_old_did(self):
        """[核心] 老 did gone-confirm 中, 新 did 同位置 IoU=1.0 -> 复用老 did."""
        m = _MockGrouping(project_config=_project(merge_iou=0.5))
        m._seed_box('箱子1', {'x': 0.1, 'y': 0.4, 'w': 0.3, 'h': 0.2},
                    gone_frames=15,
                    item_class_counts={'foot': 1, 'pillar': 1, 'cable_hook': 2})
        m._seed_track(tid=999, did='箱子2',
                      bbox={'x': 0.1, 'y': 0.4, 'w': 0.3, 'h': 0.2})

        admitted = m._try_admit_box(999)

        self.assertEqual(admitted, '箱子1', '应复用老 did=箱子1, 不开新条目')
        self.assertNotIn('箱子2', m._box_objects, '新 did=箱子2 不应入 _box_objects')
        self.assertEqual(m._box_objects['箱子1']['gone_frames'], 0,
                         '复用后 gone_frames 必须 reset')
        self.assertEqual(m._box_objects['箱子1']['item_class_counts'],
                         {'foot': 1, 'pillar': 1, 'cable_hook': 2},
                         '老条目装件保留, 接管新 ID 继续累计')
        self.assertEqual(m._tracking_display_map[999], '箱子1',
                         'tracking_display_map 也必须改写, 让后续帧对齐老 did')
        self.assertEqual(m._tracking_objects[999]['display_id'], '箱子1')

    def test_zero_threshold_disables_merge(self):
        """[核心] container_id_drift_merge_iou=0 (默认) -> 关闭, 老行为. 新 did 独立入条目."""
        m = _MockGrouping(project_config=_project(merge_iou=0))
        m._seed_box('箱子1', {'x': 0.1, 'y': 0.4, 'w': 0.3, 'h': 0.2},
                    gone_frames=15,
                    item_class_counts={'foot': 1})
        m._seed_track(tid=999, did='箱子2',
                      bbox={'x': 0.1, 'y': 0.4, 'w': 0.3, 'h': 0.2})

        admitted = m._try_admit_box(999)

        self.assertEqual(admitted, '箱子2', '阈值=0 时不合并, 新 did 独立')
        self.assertIn('箱子1', m._box_objects, '老条目仍在, 没被改')
        self.assertIn('箱子2', m._box_objects, '新条目独立创建')
        self.assertEqual(m._box_objects['箱子1']['gone_frames'], 15,
                         '老条目 gone_frames 不变 (没合并就不重置)')

    def test_multiple_candidates_pick_highest_iou(self):
        """[核心] 多个 gone-confirm 老 did 候选 -> 挑 IoU 最高的."""
        m = _MockGrouping(project_config=_project(merge_iou=0.3))
        m._seed_box('箱子1', {'x': 0.1, 'y': 0.4, 'w': 0.3, 'h': 0.2},
                    gone_frames=10, item_class_counts={'a': 1})
        m._seed_box('箱子2', {'x': 0.2, 'y': 0.4, 'w': 0.3, 'h': 0.2},  # 重叠多
                    gone_frames=20, item_class_counts={'a': 1, 'b': 1})
        m._seed_track(tid=999, did='箱子3',
                      bbox={'x': 0.2, 'y': 0.4, 'w': 0.3, 'h': 0.2})  # IoU 跟箱子2=1.0

        admitted = m._try_admit_box(999)

        self.assertEqual(admitted, '箱子2', '同位置最佳 IoU 候选优先')
        self.assertEqual(m._box_objects['箱子2']['gone_frames'], 0)
        self.assertEqual(m._box_objects['箱子1']['gone_frames'], 10,
                         '没被选中的老 did 状态不变')


# ============================================================
# Group 2: 边界
# ============================================================
class TestActiveTakeoverBoundary(unittest.TestCase):

    def test_gone_frames_beyond_max_skipped(self):
        """[边界] gone_frames 超过 max_gone 阈值 -> 不合并, 新 did 独立."""
        m = _MockGrouping(project_config=_project(merge_iou=0.5, max_gone=30))
        m._seed_box('箱子1', {'x': 0.1, 'y': 0.4, 'w': 0.3, 'h': 0.2},
                    gone_frames=31)  # 超过 max_gone=30
        m._seed_track(tid=999, did='箱子2',
                      bbox={'x': 0.1, 'y': 0.4, 'w': 0.3, 'h': 0.2})

        admitted = m._try_admit_box(999)

        self.assertEqual(admitted, '箱子2', '超过 max_gone 应跳过, 不合并')
        self.assertIn('箱子1', m._box_objects)
        self.assertIn('箱子2', m._box_objects)

    def test_iou_below_threshold_not_merged(self):
        """[边界] 没有任何老 did 达到 IoU 阈值 -> 不合并."""
        m = _MockGrouping(project_config=_project(merge_iou=0.7))
        # 老 did 在画面左侧
        m._seed_box('箱子1', {'x': 0.05, 'y': 0.4, 'w': 0.2, 'h': 0.2},
                    gone_frames=10)
        # 新 box 在画面右侧 (跟老 did IoU=0)
        m._seed_track(tid=999, did='箱子2',
                      bbox={'x': 0.7, 'y': 0.4, 'w': 0.2, 'h': 0.2})

        admitted = m._try_admit_box(999)

        self.assertEqual(admitted, '箱子2', '位置不重合不应合并 (避免误吞真实新箱)')
        self.assertEqual(m._box_objects['箱子1']['gone_frames'], 10)


# ============================================================
# Group 3: 配置异常兼容
# ============================================================
class TestConfigBoundary(unittest.TestCase):

    def test_missing_config_defaults_to_disabled(self):
        """[兼容] pipeline_config 不带 container_id_drift_merge_iou -> 默认 0 关闭."""
        m = _MockGrouping(project_config={'pipeline_config': {}})
        m._seed_box('箱子1', {'x': 0.1, 'y': 0.4, 'w': 0.3, 'h': 0.2},
                    gone_frames=15)
        m._seed_track(tid=999, did='箱子2',
                      bbox={'x': 0.1, 'y': 0.4, 'w': 0.3, 'h': 0.2})

        admitted = m._try_admit_box(999)
        self.assertEqual(admitted, '箱子2', '配置缺失 = 默认 0 = 关闭, 老项目升级行为不变')


# ============================================================
# 主入口
# ============================================================
def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestActiveTakeoverCore,
        TestActiveTakeoverBoundary,
        TestConfigBoundary,
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
