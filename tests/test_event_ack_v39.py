"""v3.9.x 事件人工确认机制 单元测试.

覆盖闭环:
  1. 普通事件 (require_ack=False) 触发 → 不进入阻塞态, 状态机继续推进
  2. 需确认事件 (require_ack=True) 触发 → 进入阻塞态 + 状态机停摆 + 推流冻结标志生效
  3. 阻塞态下重复触发事件 → 第二个事件被守门拦下
  4. 调 _clear_step_runtime_state (相当于确认 API) → 阻塞态清, 运行时清, 计数保留
  5. 超时自动确认 → 阻塞超过 ack_timeout_sec 后, _update_step_stats 自动调清理函数
  6. 启停 / 项目切换 → 阻塞态自动重置 (走 _clear_step_runtime_state 的统一路径)
  7. events_log 末条带 require_ack=True / ack_timeout_sec 字段 → 前端可读
"""
import os
import time
import numpy as np
import pytest

os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'threads;1')
os.environ['BACKEND_SKIP_INIT'] = '1'

from backend.api.source import VideoSourceManager  # noqa: E402


@pytest.fixture
def vsm():
    """干净的 VSM 实例 + 配好两类事件: id=2 NG (require_ack=True), id=99 自定义 (timeout=3)."""
    m = VideoSourceManager(channel_id=0)
    m.project_config = {
        'logic_mode': 'sequential',
        'pipeline_config': {},
        'events_config': [
            {'id': 1, 'name': '合格', 'show_notification': True, 'require_ack': False},
            {'id': 2, 'name': '不合格', 'show_notification': True, 'require_ack': True, 'ack_timeout_sec': 0},
            {'id': 99, 'name': '自定义A', 'show_notification': True, 'require_ack': True, 'ack_timeout_sec': 3},
        ],
        'counters_config': [
            {'name': '合格总数', 'value': 0},
            {'name': '不良总数', 'value': 0},
            {'name': '总产量', 'value': 0},
        ],
    }
    m.counters = {'合格总数': 0, '不良总数': 0, '总产量': 0}
    return m


class TestEventAckBasics:
    """基础闭环: 触发 → 阻塞 → 守门 → 清理."""

    def test_normal_event_no_ack(self, vsm):
        """require_ack=False 的事件: 阻塞态保持 False."""
        ok = vsm._trigger_event(1, '正常合格')
        assert ok is True
        assert vsm._pending_ack is False
        assert vsm.events_log[-1]['require_ack'] is False

    def test_require_ack_event_blocks(self, vsm):
        """require_ack=True 的事件: 进入阻塞态 + events_log 带标记."""
        ok = vsm._trigger_event(2, '周期不完整 缺[D]')
        assert ok is True
        assert vsm._pending_ack is True
        assert vsm._pending_ack_event_id == '2'
        assert vsm._pending_ack_event_name == '不合格'
        assert vsm._pending_ack_timeout_sec == 0
        assert vsm._pending_ack_started_at is not None
        last = vsm.events_log[-1]
        assert last['require_ack'] is True
        assert last['ack_timeout_sec'] == 0

    def test_ack_blocked_state_drops_followup(self, vsm):
        """阻塞态下后续事件直接丢弃."""
        vsm._trigger_event(2, '第一次 NG')
        ok = vsm._trigger_event(1, '又一帧合格')
        assert ok is False, '阻塞态下事件应被守门'
        # events_log 不应再加新条目
        assert len([e for e in vsm.events_log if e['event_name'] == '合格']) == 0

    def test_state_machine_gate(self, vsm):
        """阻塞态下 _update_step_stats 应直接 return, 不推进状态机."""
        vsm._trigger_event(2, 'NG')
        vsm.is_detecting = True
        vsm.step_consecutive_frames = {}
        prev_seq = vsm._frame_seq
        # 喂一帧合法 detection — 阻塞态下应被丢弃
        detections = [{'label': 'A', 'confidence': 0.99, 'bbox': [0, 0, 0.1, 0.1]}]
        result = vsm._update_step_stats(detections, np.zeros((100, 100, 3), dtype=np.uint8))
        assert result is None
        # 帧累计帧数应保持 0 (没被推进)
        assert vsm.step_consecutive_frames.get('A', 0) == 0
        # _frame_seq 由 capture_loop 维护, 这里不会被 update_step_stats 改 — 只验状态机不推
        assert vsm._frame_seq == prev_seq


class TestAckClearsRuntime:
    """确认 API 等价行为: 清运行时 + 阻塞态, 但保留计数."""

    def test_clear_resets_pending_ack(self, vsm):
        vsm._trigger_event(2, 'NG')
        # 同时埋一些"运行时残影"
        vsm.current_cycle_steps = ['A', 'B']
        vsm.step_last_seen = {'A': time.time(), 'B': time.time()}
        vsm.step_consecutive_frames = {'A': 5}
        # 模拟"工人确认"
        vsm._clear_step_runtime_state()
        assert vsm._pending_ack is False
        assert vsm._pending_ack_started_at is None
        assert vsm._pending_ack_event_id is None
        assert vsm.current_cycle_steps == []
        assert vsm.step_last_seen == {}

    def test_counters_preserved_after_ack(self, vsm):
        """触发 NG → 计数器累加 → 确认 → 计数器**不动**."""
        # NG 事件配的 actions: 不良总数 +1 / 总产量 +1 — 但默认 events_config 里没设
        # 这里直接验"清运行时不会清计数器"
        vsm.counters['不良总数'] = 7
        vsm.counters['总产量'] = 10
        vsm._trigger_event(2, 'NG')
        vsm._clear_step_runtime_state()
        assert vsm.counters['不良总数'] == 7
        assert vsm.counters['总产量'] == 10


class TestAckTimeout:
    """超时自动确认."""

    def test_timeout_auto_ack_triggers_clear(self, vsm):
        """timeout=3, 假装已等待 5s → _update_step_stats 入口自动清."""
        vsm.is_detecting = True
        vsm._trigger_event(99, '自定义条件触发')
        assert vsm._pending_ack is True
        # 强制把 started_at 拨回 5 秒前
        vsm._pending_ack_started_at = time.time() - 5.0
        # 跑一帧 _update_step_stats — 应自动检测超时并清
        vsm._update_step_stats([], np.zeros((100, 100, 3), dtype=np.uint8))
        assert vsm._pending_ack is False, '超时未自动确认'
        assert vsm._pending_ack_started_at is None

    def test_timeout_zero_means_never(self, vsm):
        """timeout=0 (id=2 配的): 即使等很久, 也不应自动解除."""
        vsm.is_detecting = True
        vsm._trigger_event(2, 'NG')
        assert vsm._pending_ack_timeout_sec == 0
        vsm._pending_ack_started_at = time.time() - 9999.0
        vsm._update_step_stats([], np.zeros((100, 100, 3), dtype=np.uint8))
        assert vsm._pending_ack is True, 'timeout=0 不应自动解除'


class TestAckLifecycleHooks:
    """启停 / 项目切换 → 阻塞态自动重置 (清理函数统一管)."""

    def test_clear_runtime_resets_pending_ack(self, vsm):
        """无论啥路径调 _clear_step_runtime_state, 阻塞态都被重置."""
        vsm._trigger_event(2, 'NG')
        assert vsm._pending_ack is True
        vsm._clear_step_runtime_state()
        assert vsm._pending_ack is False
        assert vsm._pending_ack_event_name is None
        assert vsm._pending_ack_timeout_sec == 0
