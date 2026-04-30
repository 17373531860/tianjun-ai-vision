"""v3.4.0 D 容器跨线/区域触发扫码 (scan_mode='D') 仿真测试.

本测试直接驱动 ContainerGroupingMixin._scan_d_update 的状态机, 验证:
  case_1_line_cross_a_to_b_triggers_lon       line A→B, box 跨线 → 发 LON, armed
  case_2_line_cross_b_to_a_no_trigger         line A→B, box B→A 跨线 → 不发 LON
  case_3_scan_received_sends_loff             armed → on_scan → LOFF + scanned
  case_4_scanned_box_gone_resets              scanned box 离开 30 帧 → reset 允下一个
  case_5_same_box_no_double_lon               已 armed, box 仍在画面 → 不重 LON
  case_6_zone_enter_triggers_lon              zone 模式, box 进入 → LON
  case_7_zone_exit_unscanned_force_loff       armed 但 box 离开未扫码 → 强制 LOFF
  case_8_concurrent_box_no_double_lon         armed box_A 时 box_B 跨线 → 忽略

mock 范围:
  - 不依赖 backend channel_manager / scanner socket
  - 直接给 VideoSourceManager 实例填 _box_objects + 调 _scan_d_update
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _MockScannerSvc:
    def __init__(self, scan_d_config=None):
        self.lon_calls = []  # [(channel_id, reason), ...]
        self.loff_calls = []
        self._scan_d_config = scan_d_config

    def is_scan_d_for_channel(self, ch):
        return self._scan_d_config is not None

    def get_scan_d_config_for_channel(self, ch):
        return self._scan_d_config

    def send_lon_for_channel(self, ch, reason=""):
        self.lon_calls.append((ch, reason))
        return 1

    def send_loff_for_channel(self, ch, reason=""):
        self.loff_calls.append((ch, reason))
        return 1


def _make_mgr():
    """创建一个 minimal manager 实例, 只填 _scan_d_update 需要的属性."""
    from backend.api.source_container_grouping_mixin import ContainerGroupingMixin

    mgr = ContainerGroupingMixin.__new__(ContainerGroupingMixin)
    mgr.channel_id = 0
    mgr.width = 1280
    mgr.height = 720
    mgr._container_mode = True
    mgr._container_label = 'box'
    mgr._box_objects = {}
    mgr._scan_d_armed_box = None
    mgr._scan_d_box_states = {}
    # _point_in_polygon 是 staticmethod 在 source.py, 但 mixin 调 self._point_in_polygon
    # 我们直接挂上 ray-casting 实现
    mgr._point_in_polygon = staticmethod(_point_in_polygon).__get__(mgr)
    return mgr


def _point_in_polygon(px, py, polygon):
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _set_box(mgr, box_did, cx, cy, w=100, h=100):
    """以中心点 (cx, cy) 设置 box bbox."""
    mgr._box_objects[box_did] = {
        'bbox': {'x': cx - w / 2, 'y': cy - h / 2, 'w': w, 'h': h},
    }


def _del_box(mgr, box_did):
    mgr._box_objects.pop(box_did, None)


def _line_cfg(x1n, y1n, x2n, y2n, side_a_to_b=True, gone=30):
    """归一化坐标 line 配置."""
    return {
        "geometry": "line",
        "line": {
            "x1": x1n, "y1": y1n, "x2": x2n, "y2": y2n,
            "side_a_to_b": side_a_to_b,
        },
        "zone": None,
        "gone_confirm_frames": gone,
    }


def _zone_cfg(polygon_norm, gone=30):
    return {
        "geometry": "zone",
        "line": None,
        "zone": polygon_norm,
        "gone_confirm_frames": gone,
    }


class TestScanDLineMode(unittest.TestCase):
    """line 模式状态机测试."""

    def test_case_1_line_cross_a_to_b_triggers_lon(self):
        # 竖直线 x=640 (0.5 * 1280); A 侧 = 线左 (x<640), B 侧 = 线右 (x>640)
        # 用 (x1,y1)=(0.5,0) (x2,y2)=(0.5,1) 即从画面顶部到底部一条竖线
        # 叉积 = (x2-x1)*(cy-y1) - (y2-y1)*(cx-x1) = 0*(cy-0) - 720*(cx-640)
        #     当 cx<640: cross > 0 → side=1 (A); cx>640: cross < 0 → side=-1 (B)
        cfg = _line_cfg(0.5, 0.0, 0.5, 1.0, side_a_to_b=True)
        svc = _MockScannerSvc(cfg)
        mgr = _make_mgr()
        with patch('backend.services.scanner.get_scanner_service', return_value=svc):
            # 帧 1: box 在 A 侧 (x=400)
            _set_box(mgr, 'box_1', 400, 360)
            mgr._scan_d_update()
            self.assertEqual(len(svc.lon_calls), 0, "首次出现不该触发, 还没记录 prev side")
            self.assertIsNone(mgr._scan_d_armed_box)
            self.assertEqual(mgr._scan_d_box_states['box_1']['side'], 1)
            # 帧 2: box 还在 A 侧 (x=500)
            _set_box(mgr, 'box_1', 500, 360)
            mgr._scan_d_update()
            self.assertEqual(len(svc.lon_calls), 0)
            # 帧 3: box 跨到 B 侧 (x=800) → A→B 触发
            _set_box(mgr, 'box_1', 800, 360)
            mgr._scan_d_update()
            self.assertEqual(len(svc.lon_calls), 1, "A→B 跨线应触发 LON")
            self.assertEqual(mgr._scan_d_armed_box, 'box_1')
            self.assertTrue(mgr._scan_d_box_states['box_1']['armed'])

    def test_case_2_line_cross_b_to_a_no_trigger(self):
        # 同一条线但要求 A→B 方向; box 反向 (B→A) 不触发
        cfg = _line_cfg(0.5, 0.0, 0.5, 1.0, side_a_to_b=True)
        svc = _MockScannerSvc(cfg)
        mgr = _make_mgr()
        with patch('backend.services.scanner.get_scanner_service', return_value=svc):
            _set_box(mgr, 'box_1', 800, 360)  # B 侧
            mgr._scan_d_update()
            _set_box(mgr, 'box_1', 400, 360)  # 跨到 A 侧
            mgr._scan_d_update()
            self.assertEqual(len(svc.lon_calls), 0,
                             "B→A 跨线不应触发 (配置为 A→B)")
            self.assertIsNone(mgr._scan_d_armed_box)

    def test_case_3_scan_received_sends_loff(self):
        cfg = _line_cfg(0.5, 0.0, 0.5, 1.0, side_a_to_b=True)
        svc = _MockScannerSvc(cfg)
        mgr = _make_mgr()
        with patch('backend.services.scanner.get_scanner_service', return_value=svc):
            _set_box(mgr, 'box_1', 400, 360)
            mgr._scan_d_update()
            _set_box(mgr, 'box_1', 800, 360)
            mgr._scan_d_update()
            self.assertEqual(mgr._scan_d_armed_box, 'box_1')
            # 模拟扫到码
            handled = mgr.scan_d_on_scan_received()
            self.assertTrue(handled)
            self.assertEqual(len(svc.loff_calls), 1, "扫到码应发 LOFF")
            self.assertIsNone(mgr._scan_d_armed_box)
            self.assertTrue(mgr._scan_d_box_states['box_1']['scanned'])
            self.assertFalse(mgr._scan_d_box_states['box_1']['armed'])

    def test_case_4_scanned_box_gone_resets(self):
        cfg = _line_cfg(0.5, 0.0, 0.5, 1.0, side_a_to_b=True, gone=5)
        svc = _MockScannerSvc(cfg)
        mgr = _make_mgr()
        with patch('backend.services.scanner.get_scanner_service', return_value=svc):
            _set_box(mgr, 'box_1', 400, 360)
            mgr._scan_d_update()
            _set_box(mgr, 'box_1', 800, 360)
            mgr._scan_d_update()
            mgr.scan_d_on_scan_received()
            # box 离开画面
            _del_box(mgr, 'box_1')
            for _ in range(4):
                mgr._scan_d_update()
            # 不到 5 帧, 状态还在
            self.assertIn('box_1', mgr._scan_d_box_states)
            mgr._scan_d_update()  # 第 5 帧
            self.assertNotIn('box_1', mgr._scan_d_box_states,
                             "gone-confirm 后应 reset")
            # 没二次 LOFF (scanned 后 reset 不需要)
            self.assertEqual(len(svc.loff_calls), 1)
            # 下一个 box 可触发
            _set_box(mgr, 'box_2', 400, 360)
            mgr._scan_d_update()
            _set_box(mgr, 'box_2', 800, 360)
            mgr._scan_d_update()
            self.assertEqual(mgr._scan_d_armed_box, 'box_2')
            self.assertEqual(len(svc.lon_calls), 2)

    def test_case_5_same_box_no_double_lon(self):
        cfg = _line_cfg(0.5, 0.0, 0.5, 1.0, side_a_to_b=True)
        svc = _MockScannerSvc(cfg)
        mgr = _make_mgr()
        with patch('backend.services.scanner.get_scanner_service', return_value=svc):
            _set_box(mgr, 'box_1', 400, 360)
            mgr._scan_d_update()
            _set_box(mgr, 'box_1', 800, 360)
            mgr._scan_d_update()
            self.assertEqual(len(svc.lon_calls), 1)
            # box 抖动: 跨回去再跨过来; armed 状态下不应再发 LON
            _set_box(mgr, 'box_1', 400, 360)
            mgr._scan_d_update()
            _set_box(mgr, 'box_1', 800, 360)
            mgr._scan_d_update()
            self.assertEqual(len(svc.lon_calls), 1, "已 armed 期间不重复发 LON")
            self.assertEqual(mgr._scan_d_armed_box, 'box_1')


class TestScanDZoneMode(unittest.TestCase):
    def test_case_6_zone_enter_triggers_lon(self):
        # 中间矩形区域 [0.4, 0.4] - [0.6, 0.6] (像素 512..768 x 288..432)
        zone = [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]
        cfg = _zone_cfg(zone)
        svc = _MockScannerSvc(cfg)
        mgr = _make_mgr()
        with patch('backend.services.scanner.get_scanner_service', return_value=svc):
            # 帧 1: box 在 zone 外 (x=200)
            _set_box(mgr, 'box_1', 200, 360)
            mgr._scan_d_update()
            self.assertFalse(mgr._scan_d_box_states['box_1']['in_zone'])
            self.assertEqual(len(svc.lon_calls), 0)
            # 帧 2: box 进入 zone (x=640)
            _set_box(mgr, 'box_1', 640, 360)
            mgr._scan_d_update()
            self.assertEqual(len(svc.lon_calls), 1, "进入 zone 应发 LON")
            self.assertEqual(mgr._scan_d_armed_box, 'box_1')

    def test_case_7_zone_exit_unscanned_force_loff(self):
        # box 进了 zone armed 但没扫到码就离开画面 → 强制 LOFF
        zone = [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]
        cfg = _zone_cfg(zone, gone=3)
        svc = _MockScannerSvc(cfg)
        mgr = _make_mgr()
        with patch('backend.services.scanner.get_scanner_service', return_value=svc):
            _set_box(mgr, 'box_1', 200, 360)
            mgr._scan_d_update()
            _set_box(mgr, 'box_1', 640, 360)
            mgr._scan_d_update()
            self.assertEqual(mgr._scan_d_armed_box, 'box_1')
            self.assertEqual(len(svc.lon_calls), 1)
            # box 离开画面, 没扫到码
            _del_box(mgr, 'box_1')
            for _ in range(3):
                mgr._scan_d_update()
            self.assertIsNone(mgr._scan_d_armed_box, "armed box 离开应被 reset")
            self.assertEqual(len(svc.loff_calls), 1,
                             "armed 但未扫码就走应主动发 LOFF 防止 LON 残留")
            self.assertNotIn('box_1', mgr._scan_d_box_states)


class TestScanDConcurrent(unittest.TestCase):
    def test_case_8_concurrent_box_no_double_lon(self):
        """产线节奏异常: 一个 box armed 时第二个 box 跨线 → 第二个被忽略."""
        cfg = _line_cfg(0.5, 0.0, 0.5, 1.0, side_a_to_b=True)
        svc = _MockScannerSvc(cfg)
        mgr = _make_mgr()
        with patch('backend.services.scanner.get_scanner_service', return_value=svc):
            # box_A 跨线 → armed
            _set_box(mgr, 'box_A', 400, 360)
            mgr._scan_d_update()
            _set_box(mgr, 'box_A', 800, 360)
            mgr._scan_d_update()
            self.assertEqual(mgr._scan_d_armed_box, 'box_A')
            self.assertEqual(len(svc.lon_calls), 1)
            # box_B 出现在 A 侧
            _set_box(mgr, 'box_B', 300, 360)
            mgr._scan_d_update()
            # box_B 跨到 B 侧 (但 box_A 还 armed)
            _set_box(mgr, 'box_B', 900, 360)
            mgr._scan_d_update()
            self.assertEqual(len(svc.lon_calls), 1, "armed 时第二个 box 跨线不重发 LON")
            self.assertEqual(mgr._scan_d_armed_box, 'box_A', "armed 仍是第一个 box")
            # box_B 状态没被标 armed
            self.assertFalse(mgr._scan_d_box_states.get('box_B', {}).get('armed', False))


def _run():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestScanDLineMode, TestScanDZoneMode, TestScanDConcurrent]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    if res.wasSuccessful():
        print(f"\n[PASS] scan_d_trigger: {res.testsRun}/{res.testsRun} 全部通过")
        return 0
    else:
        print(f"\n[FAIL] {len(res.failures)} 失败 + {len(res.errors)} 错")
        return 1


if __name__ == '__main__':
    sys.exit(_run())
