"""v3.3.0 码-码闭环结算 (bind_timing="scan_pair") 仿真测试.

本测试覆盖:
  case_1_first_scan_opens_window         首扫 → 起新窗口, 不结算
  case_2_dup_scan_soft_ignore            同码二次扫 → 软忽略 + dup_warning toast
  case_3_next_scan_settles_prev          扫 B (≠A) → 结算 A 周期 + 起 B 窗口
  case_4_was_complete_sticky_ok          扫 A → 装齐 → 拿走 1 件 → 扫 B 应判 OK
  case_5_never_complete_ng               扫 A → 一直不齐 → 扫 B 应判 NG
  case_6_force_ng_on_timeout             扫 A → 超时未扫 B → 强制 NG
  case_7_multi_channel_broadcast_share   一码多工位广播 → 共享窗口同步开/同步结算
  case_8_stop_settle_or_discard          停止时 settle_scan_pair_for_stop 双分支

mock 范围:
  - 不依赖 backend 数据库 / scanner socket / channel_manager
  - 仅复刻 mes_hooks scan_pair 状态机 + container/tracking 判定核心逻辑
"""
import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _MockScannerConn:
    def __init__(self, channel_id, broadcast_channels=None,
                 scan_pair_max_wait_sec=0):
        self.channel_id = channel_id
        self.broadcast_channels = list(broadcast_channels or [])
        self.bind_timing = "scan_pair"
        self.scan_pair_max_wait_sec = scan_pair_max_wait_sec


class _MockScannerService:
    def __init__(self, conns):
        self._connections = {i: c for i, c in enumerate(conns)}


class _MockSourceManager:
    """复刻 settle_for_scan_pair 的最小语义.

    box_objects: list of dicts {'was_complete': bool, 'item_count': int}
    每次 settle_for_scan_pair 把所有 box pop 到 settled_results 并记 OK/NG.
    """

    def __init__(self, *, container_mode=True):
        self.channel_id = 0
        self.container_mode = container_mode
        self.box_objects = []
        self.tracking_was_complete = False
        self.tracking_cycle_active = False
        self.settled_results = []  # [{is_ok, force_ng, reason}]
        self.settle_calls = 0

    def settle_for_scan_pair(self, *, force_ng=False, reason=""):
        self.settle_calls += 1
        if self.container_mode:
            for box in self.box_objects:
                if force_ng:
                    is_ok = False
                else:
                    is_ok = bool(box.get("was_complete", False))
                self.settled_results.append({
                    "is_ok": is_ok,
                    "force_ng": force_ng,
                    "reason": reason,
                    "item_count": box.get("item_count", 0),
                })
            count = len(self.box_objects)
            self.box_objects = []
            return count
        else:
            if not self.tracking_cycle_active:
                return 0
            if force_ng:
                is_ok = False
            else:
                is_ok = self.tracking_was_complete
            self.settled_results.append({
                "is_ok": is_ok,
                "force_ng": force_ng,
                "reason": reason,
            })
            self.tracking_cycle_active = False
            self.tracking_was_complete = False
            return 1


class _MockChannelManager:
    def __init__(self):
        self._mgrs = {}

    def get(self, ch):
        return self._mgrs.get(ch)


def _setup_mes(conns):
    """创建一个 MESHookManager 并把外部依赖 patch 掉."""
    from backend.services import mes_hooks as mh_mod
    from backend.services import scanner as scanner_mod
    mes = mh_mod.MESHookManager()
    mes.enabled = True
    return mes, mh_mod, scanner_mod


class TestScanPairSettle(unittest.TestCase):

    def _scan(self, mes, channel_id, serial_no, wp_id):
        """模拟一个 _handle_scan 调用 (只走 scan_pair 路径)."""
        mes._handle_scan_pair_event(MagicMock(), channel_id, serial_no, wp_id)

    def test_case_1_first_scan_opens_window(self):
        """首扫 → 起新窗口, 没结算任何 box."""
        conns = [_MockScannerConn(0, broadcast_channels=[0])]
        mes, mh_mod, scanner_mod = _setup_mes(conns)
        cm = _MockChannelManager()
        cm._mgrs[0] = _MockSourceManager()
        with patch.object(scanner_mod, 'get_scanner_service', return_value=_MockScannerService(conns)), \
             patch('backend.api.channel_manager.channel_manager', cm):
            self._scan(mes, 0, "SN_A", 100)
        active = mes.get_scan_pair_active_serial(0)
        self.assertEqual(active, "SN_A")
        self.assertEqual(cm._mgrs[0].settle_calls, 0)

    def test_case_2_dup_scan_soft_ignore(self):
        """同码二次扫 → 不结算 + 写 dup_warning."""
        conns = [_MockScannerConn(0, broadcast_channels=[0])]
        mes, mh_mod, scanner_mod = _setup_mes(conns)
        cm = _MockChannelManager()
        cm._mgrs[0] = _MockSourceManager()
        with patch.object(scanner_mod, 'get_scanner_service', return_value=_MockScannerService(conns)), \
             patch('backend.api.channel_manager.channel_manager', cm):
            self._scan(mes, 0, "SN_A", 100)
            self._scan(mes, 0, "SN_A", 101)
        self.assertEqual(mes.get_scan_pair_active_serial(0), "SN_A")
        self.assertEqual(cm._mgrs[0].settle_calls, 0)
        evt = mes._last_scan_event.get(0)
        self.assertTrue(evt and evt.get("scan_pair_dup_warning"))

    def test_case_3_next_scan_settles_prev(self):
        """扫 B (≠A) → 结算 A 一次 + 窗口切换为 B."""
        conns = [_MockScannerConn(0, broadcast_channels=[0])]
        mes, mh_mod, scanner_mod = _setup_mes(conns)
        cm = _MockChannelManager()
        mgr = _MockSourceManager()
        mgr.box_objects = [{"was_complete": True, "item_count": 5}]
        cm._mgrs[0] = mgr
        with patch.object(scanner_mod, 'get_scanner_service', return_value=_MockScannerService(conns)), \
             patch('backend.api.channel_manager.channel_manager', cm):
            self._scan(mes, 0, "SN_A", 100)
            self._scan(mes, 0, "SN_B", 101)
        self.assertEqual(mes.get_scan_pair_active_serial(0), "SN_B")
        self.assertEqual(mgr.settle_calls, 1)
        self.assertEqual(len(mgr.settled_results), 1)
        self.assertTrue(mgr.settled_results[0]["is_ok"])

    def test_case_4_was_complete_sticky_ok(self):
        """箱子曾装齐过, 之后被拿走一件, 扫 B 时应判 OK."""
        conns = [_MockScannerConn(0, broadcast_channels=[0])]
        mes, mh_mod, scanner_mod = _setup_mes(conns)
        cm = _MockChannelManager()
        mgr = _MockSourceManager()
        mgr.box_objects = [{"was_complete": True, "item_count": 4}]
        cm._mgrs[0] = mgr
        with patch.object(scanner_mod, 'get_scanner_service', return_value=_MockScannerService(conns)), \
             patch('backend.api.channel_manager.channel_manager', cm):
            self._scan(mes, 0, "SN_A", 100)
            self._scan(mes, 0, "SN_B", 101)
        self.assertTrue(mgr.settled_results[0]["is_ok"], "曾齐过应判 OK")

    def test_case_5_never_complete_ng(self):
        """箱子从未齐过, 扫 B 时应判 NG."""
        conns = [_MockScannerConn(0, broadcast_channels=[0])]
        mes, mh_mod, scanner_mod = _setup_mes(conns)
        cm = _MockChannelManager()
        mgr = _MockSourceManager()
        mgr.box_objects = [{"was_complete": False, "item_count": 2}]
        cm._mgrs[0] = mgr
        with patch.object(scanner_mod, 'get_scanner_service', return_value=_MockScannerService(conns)), \
             patch('backend.api.channel_manager.channel_manager', cm):
            self._scan(mes, 0, "SN_A", 100)
            self._scan(mes, 0, "SN_B", 101)
        self.assertFalse(mgr.settled_results[0]["is_ok"], "未齐过应判 NG")

    def test_case_6_force_ng_on_timeout(self):
        """扫 A 后 0.2 秒超时 → 强制 NG, 窗口清空."""
        conns = [_MockScannerConn(0, broadcast_channels=[0],
                                   scan_pair_max_wait_sec=1)]
        # max_wait_sec 我们写 1, 但用 mock 把 wait 改小到 0.2 加速
        mes, mh_mod, scanner_mod = _setup_mes(conns)
        cm = _MockChannelManager()
        mgr = _MockSourceManager()
        mgr.box_objects = [{"was_complete": True, "item_count": 5}]
        cm._mgrs[0] = mgr
        with patch.object(scanner_mod, 'get_scanner_service', return_value=_MockScannerService(conns)), \
             patch('backend.api.channel_manager.channel_manager', cm), \
             patch.object(mes, '_get_scan_pair_max_wait_sec', return_value=0.2):
            self._scan(mes, 0, "SN_A", 100)
            time.sleep(0.5)
        self.assertIsNone(mes.get_scan_pair_active_serial(0), "超时后窗口应清空")
        self.assertEqual(mgr.settle_calls, 1)
        self.assertTrue(mgr.settled_results[0]["force_ng"], "超时应强制 NG")
        self.assertFalse(mgr.settled_results[0]["is_ok"])

    def test_case_7_multi_channel_broadcast_share(self):
        """1扫码器 → 2工位广播: 同一码事件应同步开窗口、同步结算."""
        # 一扫码器 broadcast=[0,1] —— 一个事件会被 dispatch 两次
        conns = [_MockScannerConn(0, broadcast_channels=[0, 1])]
        mes, mh_mod, scanner_mod = _setup_mes(conns)
        cm = _MockChannelManager()
        mgr0 = _MockSourceManager()
        mgr0.box_objects = [{"was_complete": True, "item_count": 5}]
        mgr1 = _MockSourceManager()
        mgr1.channel_id = 1
        mgr1.box_objects = [{"was_complete": True, "item_count": 3}]
        cm._mgrs[0] = mgr0
        cm._mgrs[1] = mgr1
        with patch.object(scanner_mod, 'get_scanner_service', return_value=_MockScannerService(conns)), \
             patch('backend.api.channel_manager.channel_manager', cm):
            # 第一次扫码 (主 ch=0): 起共享窗口
            self._scan(mes, 0, "SN_A", 100)
            # 一码广播会再调一次兄弟 ch=1 (no-op, 状态已写)
            self._scan(mes, 1, "SN_A", 100)
            # 两个工位都看到开始码
            self.assertEqual(mes.get_scan_pair_active_serial(0), "SN_A")
            self.assertEqual(mes.get_scan_pair_active_serial(1), "SN_A")
            # 扫下一码 (再次先 ch=0 后 ch=1 dispatch)
            self._scan(mes, 0, "SN_B", 101)
            self._scan(mes, 1, "SN_B", 101)
        self.assertEqual(mgr0.settle_calls, 1, "主工位结算 1 次")
        self.assertEqual(mgr1.settle_calls, 1, "广播工位也结算 1 次")
        self.assertTrue(mgr0.settled_results[0]["is_ok"])
        self.assertTrue(mgr1.settled_results[0]["is_ok"])

    def test_case_8_stop_settle_or_discard(self):
        """settle_scan_pair_for_stop 双分支."""
        conns = [_MockScannerConn(0, broadcast_channels=[0])]
        mes, mh_mod, scanner_mod = _setup_mes(conns)
        cm = _MockChannelManager()
        mgr_settle = _MockSourceManager()
        mgr_settle.box_objects = [{"was_complete": True, "item_count": 5}]
        cm._mgrs[0] = mgr_settle
        with patch.object(scanner_mod, 'get_scanner_service', return_value=_MockScannerService(conns)), \
             patch('backend.api.channel_manager.channel_manager', cm):
            self._scan(mes, 0, "SN_A", 100)
            n = mes.settle_scan_pair_for_stop(0, discard=False)
        self.assertEqual(n, 1)
        self.assertEqual(mgr_settle.settle_calls, 1)
        self.assertTrue(mgr_settle.settled_results[0]["is_ok"])
        self.assertIsNone(mes.get_scan_pair_active_serial(0), "结算后窗口清空")

        # 丢弃分支
        mes2, mh_mod2, scanner_mod2 = _setup_mes(conns)
        cm2 = _MockChannelManager()
        mgr_discard = _MockSourceManager()
        mgr_discard.box_objects = [{"was_complete": False, "item_count": 1}]
        cm2._mgrs[0] = mgr_discard
        with patch.object(scanner_mod2, 'get_scanner_service', return_value=_MockScannerService(conns)), \
             patch('backend.api.channel_manager.channel_manager', cm2):
            self._scan(mes2, 0, "SN_A", 100)
            n = mes2.settle_scan_pair_for_stop(0, discard=True)
        self.assertEqual(n, 0, "discard 不计产能")
        self.assertEqual(mgr_discard.settle_calls, 0, "discard 不调 settle")
        self.assertIsNone(mes2.get_scan_pair_active_serial(0), "discard 后窗口也清空")


if __name__ == "__main__":
    unittest.main(verbosity=2)
