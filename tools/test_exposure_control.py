"""v3.1.2 摄像头曝光控制冒烟测试.

验证 _apply_exposure_setting 在 4 种场景下行为正确:
  1. auto_exposure=True  → 不动相机参数 (向后兼容)
  2. auto_exposure=False / Windows → set 0.25 + 0 + log2(秒)
  3. auto_exposure=False / Linux → set 1 + 单位 100us
  4. set 抛异常 → 不会让 start_camera 挂掉

不依赖真实相机 (CI 友好), 用 MagicMock 替代 cv2.VideoCapture.
"""
import platform
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/home/qianqian/桌面/word/tianjun副本')


class TestExposureControl(unittest.TestCase):
    def setUp(self):
        from backend.api.source_camera_start_mixin import _apply_exposure_setting
        self.fn = _apply_exposure_setting
        self.cap = MagicMock()
        self.cap.set = MagicMock(return_value=True)

    def test_auto_exposure_true_skips_setup(self):
        """默认 auto=True → 不调 cap.set, 完全向后兼容"""
        self.fn(self.cap, auto_exposure=True, exposure_value=-6)
        self.assertEqual(self.cap.set.call_count, 0)

    @patch('backend.api.source_camera_start_mixin.platform')
    def test_windows_manual_exposure(self, mock_platform):
        """Windows + 手动 → AUTO=0.25 / AUTO=0 / EXPOSURE=-6"""
        mock_platform.system.return_value = "Windows"
        import cv2

        self.fn(self.cap, auto_exposure=False, exposure_value=-6.0)
        self.assertEqual(self.cap.set.call_count, 3)
        calls = [c.args for c in self.cap.set.call_args_list]
        self.assertIn((cv2.CAP_PROP_AUTO_EXPOSURE, 0.25), calls)
        self.assertIn((cv2.CAP_PROP_AUTO_EXPOSURE, 0), calls)
        self.assertIn((cv2.CAP_PROP_EXPOSURE, -6.0), calls)

    @patch('backend.api.source_camera_start_mixin.platform')
    def test_linux_manual_exposure(self, mock_platform):
        """Linux V4L2 + 手动 → AUTO=1 / EXPOSURE=单位 100us"""
        mock_platform.system.return_value = "Linux"
        import cv2

        self.fn(self.cap, auto_exposure=False, exposure_value=-6.0)
        self.assertEqual(self.cap.set.call_count, 2)
        calls = [c.args for c in self.cap.set.call_args_list]
        self.assertEqual(calls[0], (cv2.CAP_PROP_AUTO_EXPOSURE, 1))
        prop, val = calls[1]
        self.assertEqual(prop, cv2.CAP_PROP_EXPOSURE)
        # -6 → 1/64 秒 ≈ 156 (单位 100us)
        self.assertAlmostEqual(val, 156, delta=2)

    @patch('backend.api.source_camera_start_mixin.platform')
    def test_linux_with_positive_value_uses_raw(self, mock_platform):
        """Linux + 正值 → 当作绝对单位 (100us) 直接传"""
        mock_platform.system.return_value = "Linux"
        import cv2

        self.fn(self.cap, auto_exposure=False, exposure_value=100)
        calls = [c.args for c in self.cap.set.call_args_list]
        prop, val = calls[1]
        self.assertEqual(prop, cv2.CAP_PROP_EXPOSURE)
        self.assertEqual(val, 100)

    @patch('backend.api.source_camera_start_mixin.platform')
    def test_set_exception_does_not_propagate(self, mock_platform):
        """cap.set 抛异常时不能让外层崩溃"""
        mock_platform.system.return_value = "Windows"
        self.cap.set = MagicMock(side_effect=RuntimeError("camera not connected"))
        try:
            self.fn(self.cap, auto_exposure=False, exposure_value=-6)
        except Exception as e:
            self.fail(f"_apply_exposure_setting should not raise, but got: {e}")


if __name__ == '__main__':
    print(f"[host] platform={platform.system()}")
    unittest.main(verbosity=2)
