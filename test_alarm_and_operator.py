"""
报警共享模式单元测试 (v3.10+ 阶段 5 修订)

历史:
- v3.9 之前本文件还测了"操作员持久化" (TestOperatorPersist), 现已删除:
  · 操作员系统在 v3.10 被用户/角色系统接管
  · backend/api/operators.py 已清空, 6 端点全 410 Gone
  · _current_operator / get_current_operator_id / current_operator.json 全删
  · 原代码备份在 .tmp_audit/test_alarm_and_operator.py.bak.phase5
- 文件名保留 (历史 CI 索引可能引用), 但内容仅剩 alarm 共享模式

覆盖目标 (alarm 共享模式):
- is_shared() 在多通道时为 True, 单通道为 False
- get_shared_channels_snapshot() 返回的是副本 (不影响内部)
- _trigger_alarm_shared 写入对应 ch 的 state, 按优先级合成
- timer 到期后 state 清空, 不影响别的 ch
"""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock

os.environ["BACKEND_SKIP_INIT"] = "1"

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import backend.models.models  # noqa: E402,F401
import backend.models.mes_models  # noqa: E402,F401
from backend.api.alarm import AlarmManager  # noqa: E402


# =====================================================
# 报警共享模式
# =====================================================
class TestAlarmShared(unittest.TestCase):
    def setUp(self):
        self.mgr = AlarmManager(config={
            'enabled': True,
            'protocol': 'modbus_4color',
            'triggers': {
                'event2': {'enabled': True, 'duration': 0.1, 'color': 'red', 'effect': 'on'},
            },
        })
        # mock 串口写, 避免真发
        self.mgr._send_command = MagicMock(return_value=True)

    def test_solo_mode_is_shared_false(self):
        self.assertFalse(self.mgr.is_shared())
        self.assertEqual(self.mgr.get_shared_channels_snapshot(), [])

    def test_shared_mode_is_shared_true(self):
        self.mgr.set_shared_mode(channels=[0, 1, 2])
        self.assertTrue(self.mgr.is_shared())
        self.assertEqual(sorted(self.mgr.get_shared_channels_snapshot()), [0, 1, 2])

    def test_shared_snapshot_is_copy(self):
        """snapshot 是副本: 外部修改不影响内部 set"""
        self.mgr.set_shared_mode(channels=[0, 1])
        snap = self.mgr.get_shared_channels_snapshot()
        snap.append(99)
        # 内部仍是 {0, 1}
        self.assertEqual(sorted(self.mgr.get_shared_channels_snapshot()), [0, 1])

    def test_trigger_writes_state_for_correct_channel(self):
        self.mgr.set_shared_mode(channels=[0, 1])
        self.mgr.trigger_alarm(event_type='event2', channel_id=1)
        state = self.mgr._channel_states.get(1)
        self.assertIsNotNone(state)
        self.assertEqual(state['event'], 'event2')
        self.assertEqual(state['category'], 'ng')
        # ch0 不应被影响
        st0 = self.mgr._channel_states.get(0, {})
        self.assertIsNone(st0.get('event'))

    def test_trigger_expires_after_duration(self):
        self.mgr.set_shared_mode(channels=[0, 1])
        self.mgr.trigger_alarm(event_type='event2', channel_id=1)
        self.assertEqual(self.mgr._channel_states[1]['event'], 'event2')
        time.sleep(0.25)  # > duration 0.1s
        self.assertIsNone(self.mgr._channel_states[1]['event'])

    def test_trigger_disabled_when_alarm_off(self):
        """enabled=False 时不应写入 state"""
        self.mgr.config['enabled'] = False
        self.mgr.set_shared_mode(channels=[0, 1])
        self.mgr.trigger_alarm(event_type='event2', channel_id=1)
        st = self.mgr._channel_states.get(1, {})
        self.assertIsNone(st.get('event'))


if __name__ == "__main__":
    unittest.main(verbosity=2)
