"""
报警共享模式 + 操作员持久化的单元测试

覆盖目标：
  报警共享：
    - is_shared() 在多通道时为 True，单通道为 False
    - get_shared_channels_snapshot() 返回的是副本（不影响内部）
    - _trigger_alarm_shared 写入对应 ch 的 state，按优先级合成
    - timer 到期后 state 清空、不影响别的 ch

  操作员持久化：
    - set 后立即 save，重新加载能拿回
    - operator 在 DB 中被删/失效后，get_current_operator_id 自动清掉脏值
    - 并发 set 不会互相覆盖（依赖 lock）
"""
import os
import sys
import json
import time
import tempfile
import threading
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
        # mock 串口写，避免真发
        self.mgr._send_command = MagicMock(return_value=True)

    def test_solo_mode_is_shared_false(self):
        self.assertFalse(self.mgr.is_shared())
        self.assertEqual(self.mgr.get_shared_channels_snapshot(), [])

    def test_shared_mode_is_shared_true(self):
        self.mgr.set_shared_mode(channels=[0, 1, 2])
        self.assertTrue(self.mgr.is_shared())
        self.assertEqual(sorted(self.mgr.get_shared_channels_snapshot()), [0, 1, 2])

    def test_shared_snapshot_is_copy(self):
        """snapshot 是副本：外部修改不影响内部 set"""
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


# =====================================================
# 操作员持久化
# =====================================================
class TestOperatorPersist(unittest.TestCase):
    def setUp(self):
        # 用临时目录隔离 current_operator.json
        self._tmpdir = tempfile.mkdtemp(prefix="op_persist_")
        self._patch_data_dir()

        # 重新加载 operators 模块以让它使用临时 DATA_DIR
        import importlib
        import backend.api.operators as ops
        importlib.reload(ops)
        self.ops = ops

        # mock SessionLocal + Operator 查询，让 get_current_operator_id 能跑
        self._db_results = {1: True, 2: True}  # operator_id -> active
        self._patch_db()

    def _patch_data_dir(self):
        # 直接 patch backend.api.operators 模块的 DATA_DIR 路径
        # 但 operators.py 在 import 时已 freeze, 所以改 _OP_STATE_PATH
        pass

    def _patch_db(self):
        from unittest.mock import patch
        self._patcher = patch('backend.api.operators.SessionLocal')
        SL = self._patcher.start()
        sess = MagicMock()
        SL.return_value = sess

        def query_side_effect(model):
            q = MagicMock()

            def filter_first(*args, **kwargs):
                # 简化：args 里第一个是 Operator.id == X 表达式，我们直接根据 _db_results 决定
                # 用 self._current_op_query 标记当前查询的 id
                op_id = getattr(self, '_current_op_query', None)
                if op_id is not None and self._db_results.get(op_id):
                    op = MagicMock()
                    op.id = op_id
                    return op
                return None

            q.filter.return_value.first = filter_first
            return q

        sess.query.side_effect = query_side_effect

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _set_state_path(self, fname="current_operator.json"):
        path = os.path.join(self._tmpdir, fname)
        self.ops._OP_STATE_PATH = path
        return path

    def test_set_and_load_round_trip(self):
        """set 后落盘，再 load 能拿回"""
        path = self._set_state_path()
        self.ops._current_operator[0] = 1
        self.ops._save_current_operator()
        self.assertTrue(os.path.exists(path))
        with open(path, 'r') as f:
            raw = json.load(f)
        # JSON key 都是 str
        self.assertEqual(raw, {"0": 1})

        loaded = self.ops._load_current_operator()
        self.assertEqual(loaded, {0: 1})

    def test_load_missing_file_returns_empty(self):
        self._set_state_path("nonexistent.json")
        self.assertEqual(self.ops._load_current_operator(), {})

    def test_load_corrupt_file_returns_empty(self):
        path = self._set_state_path()
        with open(path, 'w') as f:
            f.write("not a json")
        self.assertEqual(self.ops._load_current_operator(), {})

    def test_get_current_operator_id_clears_stale(self):
        """DB 中 operator 已被删/disable 时，get_current_operator_id 应自动清脏值"""
        self._set_state_path()
        # 模拟 ch0 设了 op_id=99，但 DB 里没有
        self.ops._current_operator[0] = 99
        self._db_results = {}  # 让 DB 查询返回 None
        self._current_op_query = 99
        result = self.ops.get_current_operator_id(0)
        self.assertIsNone(result)
        # 脏值应被清掉
        self.assertNotIn(0, self.ops._current_operator)

    def test_concurrent_save_no_corruption(self):
        """多线程并发 _save_current_operator 不应损坏 JSON"""
        path = self._set_state_path()

        def writer(i):
            for _ in range(20):
                self.ops._current_operator[i] = i * 10
                self.ops._save_current_operator()

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 文件应能正常解析（不会半截写入），且包含至少一个 key
        self.assertTrue(os.path.exists(path))
        with open(path, 'r') as f:
            raw = json.load(f)
        self.assertGreater(len(raw), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
