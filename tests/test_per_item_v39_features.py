"""per_item v3.9+ 新机制单元测试.

覆盖范围:
1. 补丁 2: 锁定模式下 cleanup_stale_items 不清未覆盖件 (含锁 v3.8 之前是会清的回归)
2. 补丁 3: 周期超时 + 空闲超时强制结算
3. 补丁 4: 所有步骤完成 + settle_after_all_done_sec → 立即 OK
4. 补丁 1: expected_count 固定数量模式 + 周期内补锁定 (lock_lookahead)
5. 补丁 5: item_label 多标签 OR (数组配置)

设计原则:
- 不依赖摄像头/真实推理, 直接喂 detections
- 每个新机制独立用例, 失败时定位清晰
- 用 monkeypatch 操控 time.time 实现"虚拟时间", 避免真 sleep
"""
from __future__ import annotations

import pytest
import numpy as np

from backend.api.source import VideoSourceManager
import backend.api.source_per_item_mixin as per_item_mixin


# ==================== 测试工具 ====================
SCREW_POSITIONS_14 = [
    (0.05 + i * 0.06, 0.50, 0.04, 0.04) for i in range(14)
]
SCREW_POSITIONS_7N_4 = [
    (0.10 + i * 0.15, 0.20, 0.05, 0.05) for i in range(4)
]
_DUMMY_FRAME = np.zeros((100, 100, 3), dtype=np.uint8)


def _det(label, x, y, w, h, conf=0.85):
    return {'label': label, 'x': x, 'y': y, 'w': w, 'h': h,
            'confidence': conf, 'class_name': label}


def _capture_events(vsm):
    captured = []
    def fake_trigger(event_id, reason):
        captured.append((event_id, reason))
        return True
    vsm._trigger_event = fake_trigger
    return captured


class VirtualClock:
    """虚拟时钟. 替换 per_item_mixin.time.time, 让测试用例显式推进时间."""
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, sec):
        self.now += sec


@pytest.fixture
def vclock(monkeypatch):
    """所有测试默认用虚拟时钟"""
    clock = VirtualClock()
    monkeypatch.setattr(per_item_mixin.time, 'time', clock)
    return clock


# ==================== 配置工厂 ====================
def _cfg_with_expected_count(expected_5n=14, expected_7n=4,
                              cycle_max=0, idle_timeout=0,
                              settle_after_all_done=0, lock_lookahead=5,
                              count_tolerance=1, count_ratio=0.85):
    """生成一个"5N + 7N 两个步骤 + 涂黑多标签"的项目配置 (本视频场景).

    v3.9+ 起 cycle_max / idle_timeout 都从 pipeline_config.per_item 读 (专属字段),
    与其他模式 (sequential 等) 的项目级 cycle_max_duration / idle_timeout_seconds 解耦.
    """
    return {
        'id': 9999, 'name': 'TEST_V39',
        'task_type': 'detection', 'logic_mode': 'per_item',
        'steps_config': [
            {
                'id': 1, 'label': '扭5N', 'displayLabel': '扭5N螺丝',
                'enabled': True, 'threshold': 50,
                'per_item': {
                    'item_label': '5N螺丝',
                    'action_label': '扭5N螺丝',
                    'expected_count': expected_5n,
                    'item_tracking_iou': 0.3,
                    'coverage_iou': 0.3,
                    'sustain_frames': 3,
                },
            },
            {
                'id': 2, 'label': '扭7N', 'displayLabel': '扭7N螺丝',
                'enabled': True, 'threshold': 50,
                'per_item': {
                    'item_label': '7N螺丝',
                    'action_label': '扭7N螺丝',
                    'expected_count': expected_7n,
                    'item_tracking_iou': 0.3,
                    'coverage_iou': 0.3,
                    'sustain_frames': 3,
                },
            },
            {
                'id': 3, 'label': '涂黑', 'displayLabel': '涂黑标记',
                'enabled': True, 'threshold': 50,
                'per_item': {
                    # 多标签 OR: 涂黑要覆盖 5N + 7N 全部
                    'item_label': ['5N螺丝', '7N螺丝'],
                    'action_label': '涂黑',
                    'expected_count': expected_5n + expected_7n,
                    'item_tracking_iou': 0.3,
                    'coverage_iou': 0.3,
                    'sustain_frames': 3,
                },
            },
        ],
        'events_config': [],
        'pipeline_config': {
            # 项目级 cycle_max_duration / idle_timeout_seconds 在 per_item 模式下不再被使用,
            # 这里故意留默认值 (0) 来确保 per_item 只读 per_item.* 字段.
            'per_item': {
                'stability_window_frames': 5,
                'stability_iou_threshold': 0.5,
                'stability_count_tolerance': count_tolerance,
                'stability_count_ratio': count_ratio,
                'item_timeout_seconds': 0,
                'lock_count_on_start': True,
                'finish_label': '',
                'finish_sustain_frames': 3,
                'settle_after_all_done_sec': settle_after_all_done,
                'lock_lookahead_seconds': lock_lookahead,
                # v3.9+ per_item 专属超时
                'cycle_max_duration_sec': cycle_max,
                'idle_timeout_sec': idle_timeout,
            },
        },
    }


def _make_vsm(config):
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config(config)
    return vsm


def _frame_18_screws():
    """完整工件: 14 颗 5N + 4 颗 7N"""
    return ([_det('5N螺丝', *p) for p in SCREW_POSITIONS_14]
            + [_det('7N螺丝', *p) for p in SCREW_POSITIONS_7N_4])


def _frame_partial_screws(n5=14, n7=4):
    return ([_det('5N螺丝', *p) for p in SCREW_POSITIONS_14[:n5]]
            + [_det('7N螺丝', *p) for p in SCREW_POSITIONS_7N_4[:n7]])


def _feed(vsm, dets, repeat=1, vclock=None, frame_dt=0.05):
    """喂 repeat 帧, 每帧推进虚拟时钟 frame_dt 秒 (默认 50ms, 模拟 ~20fps)"""
    for _ in range(repeat):
        vsm._update_step_stats(list(dets), _DUMMY_FRAME)
        if vclock is not None:
            vclock.advance(frame_dt)


# ==================== 1. 补丁 2: 锁定模式禁清未覆盖件 ====================
class TestPatch2_NoCleanupInLockedMode:
    def test_uncovered_items_survive_long_obscuration(self, vclock):
        """工人手挡住部分螺丝长达 10 秒, 锁定的未覆盖件不应被清"""
        # 用 v3.8 老路径 (无 expected_count) 跑, 显式开 item_timeout=3 来验证不清
        cfg = _cfg_with_expected_count(expected_5n=0, expected_7n=0)
        cfg['pipeline_config']['per_item']['item_timeout_seconds'] = 3.0
        # 拿掉第三步, 简化场景
        cfg['steps_config'] = cfg['steps_config'][:1]
        cfg['steps_config'][0]['per_item']['item_label'] = '5N螺丝'
        del cfg['steps_config'][0]['per_item']['expected_count']
        vsm = _make_vsm(cfg)

        # 稳定窗口 5 帧 → 锁定 14 颗
        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active
        step = vsm._per_item_steps[0]
        assert len(step.items) == 14

        # 10 秒空帧 (画面里完全看不到螺丝, 工人手挡住一切)
        for _ in range(200):
            vsm._update_step_stats([], _DUMMY_FRAME)
            vclock.advance(0.05)

        # 锁定模式下未覆盖件也不清
        assert len(step.items) == 14, \
            f"锁定模式下被遮挡 10s 不应清个体, 实际剩 {len(step.items)}"


# ==================== 2. 补丁 3: 周期超时 / 空闲超时 ====================
class TestPatch3_CycleAndIdleTimeout:
    def test_cycle_max_duration_forces_settle(self, vclock):
        """周期开始后超过 cycle_max_duration 秒未结算 → 强制 NG 结算"""
        cfg = _cfg_with_expected_count(cycle_max=20)  # 20 秒超时
        vsm = _make_vsm(cfg)
        events = _capture_events(vsm)

        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active

        # 推进 25 秒 (>20), 每秒喂一帧"完整工件"但不做任何工序覆盖
        for _ in range(25):
            _feed(vsm, _frame_18_screws(), repeat=1, vclock=vclock, frame_dt=1.0)
            if not vsm._per_item_session.cycle_active:
                break

        ng_events = [e for e in events if e[0] == 2]
        assert ng_events, f"周期超时应触发 NG, 实际 events={events}"
        assert not vsm._per_item_session.cycle_active

    def test_idle_timeout_forces_settle(self, vclock):
        """周期内连续 idle_timeout 秒无任何 item/action 标签 → 强制结算"""
        cfg = _cfg_with_expected_count(idle_timeout=5)
        vsm = _make_vsm(cfg)
        events = _capture_events(vsm)

        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active

        # 工件被搬走 → 喂 8 秒 (>5s) 的空帧
        for _ in range(160):    # 160 帧 × 0.05 = 8s
            vsm._update_step_stats([], _DUMMY_FRAME)
            vclock.advance(0.05)
            if not vsm._per_item_session.cycle_active:
                break

        assert not vsm._per_item_session.cycle_active, \
            "空闲超时后周期应已结算"
        # NG (因为没扭螺丝)
        assert any(e[0] == 2 for e in events)

    def test_idle_timeout_triggers_even_with_residual_item_label(self, vclock):
        """关键回归: 工件静置画面 (持续有 item 标签出现) + 工人停手 → idle_timeout 应触发.

        这是真实视频暴露的 bug 场景: 模型在工件搬走后误检出残留"7N螺丝", 
        如果用"任意标签=活动"的语义, idle_timeout 永远等不到 → 周期永远不结算.

        v3.9 决策: last_activity_time 只由 action 标签刷新, 不由 item 刷新.
        """
        cfg = _cfg_with_expected_count(idle_timeout=4)
        vsm = _make_vsm(cfg)
        events = _capture_events(vsm)

        # 周期开始
        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active

        # 工人停手 → 连续 6 秒 (>4s idle_timeout) 都只看到 item 标签 (工件还在桌上, 或模型误检残留)
        for _ in range(120):   # 120 帧 × 0.05 = 6s
            vsm._update_step_stats(list(_frame_18_screws()), _DUMMY_FRAME)
            vclock.advance(0.05)
            if not vsm._per_item_session.cycle_active:
                break

        assert not vsm._per_item_session.cycle_active, \
            "工人停手 (无 action 标签) 即使有 item 残留, 也应触发 idle_timeout"
        assert any(e[0] == 2 for e in events), "应触发 NG 事件"


# ==================== 3. 补丁 4: 完成即结算 OK ====================
class TestPatch4_SettleAfterAllDone:
    def test_all_steps_done_triggers_ok_after_hold(self, vclock):
        """所有步骤覆盖完后, 保持 1.5 秒 → 立即 OK (不需要收尾标签)"""
        cfg = _cfg_with_expected_count(settle_after_all_done=1.5)
        # 简化: 只留 1 个步骤 (5N), 跳过 7N + 涂黑
        cfg['steps_config'] = cfg['steps_config'][:1]
        vsm = _make_vsm(cfg)
        events = _capture_events(vsm)

        # 周期开始
        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active

        # 逐颗扭完 14 颗 (每颗工序框持续 5 帧, 间隔 2 帧)
        for i in range(14):
            screw_pos = SCREW_POSITIONS_14[i]
            base_frame = _frame_18_screws()
            action_frame = base_frame + [_det('扭5N螺丝', *screw_pos)]
            _feed(vsm, action_frame, repeat=5, vclock=vclock)
            _feed(vsm, _frame_18_screws(), repeat=2, vclock=vclock)

        # 此时步骤应已完成
        step = vsm._per_item_steps[0]
        assert step.completed, \
            f"步骤应已完成, covered={step.covered_count()}/{len(step.items)}"

        # 再喂 1.5 秒 (>1.5s)
        for _ in range(35):     # 35 * 0.05 = 1.75s
            _feed(vsm, _frame_18_screws(), repeat=1, vclock=vclock)
            if not vsm._per_item_session.cycle_active:
                break

        ok_events = [e for e in events if e[0] == 1]
        assert ok_events, f"应触发 OK 事件, events={events}"
        assert not vsm._per_item_session.cycle_active


# ==================== 4. 补丁 1: expected_count 固定数量模式 ====================
class TestPatch1_ExpectedCount:
    def test_cycle_starts_with_count_tolerance(self, vclock):
        """配了 expected_count=14, count_tolerance=2 → 检出 12 颗也能进周期"""
        cfg = _cfg_with_expected_count(count_tolerance=2)
        vsm = _make_vsm(cfg)

        # 持续 6 帧, 只检出 12 颗 5N + 4 颗 7N (5N 缺 2 颗模拟漏检)
        _feed(vsm, _frame_partial_screws(n5=12, n7=4), repeat=6, vclock=vclock)

        assert vsm._per_item_session.cycle_active, \
            "配 expected_count + tolerance=2 时, 检出 12 颗应能进周期"
        step5n = next(s for s in vsm._per_item_steps if '5N' in s.step_label)
        # 锁定数量等于检出数 12 (后续靠 lookahead 补)
        assert len(step5n.items) == 12

    def test_lookahead_absorbs_late_items(self, vclock):
        """周期开始时锁了 12 颗, lookahead 窗口内出现新位置 → 补到 14 颗"""
        cfg = _cfg_with_expected_count(count_tolerance=2, lock_lookahead=5)
        vsm = _make_vsm(cfg)

        # 阶段 1: 6 帧 (~0.3s), 检出 12 颗 → 周期开始, 锁定 12 颗
        _feed(vsm, _frame_partial_screws(n5=12, n7=4), repeat=6, vclock=vclock)
        step5n = next(s for s in vsm._per_item_steps if '5N' in s.step_label)
        assert len(step5n.items) == 12

        # 阶段 2: 周期内 (1 秒后, 仍在 5s lookahead 窗口内) 检出全 14 颗
        _feed(vsm, _frame_18_screws(), repeat=5, vclock=vclock)

        assert len(step5n.items) == 14, \
            f"lookahead 窗口内应补满 14 颗, 实际 {len(step5n.items)}"

    def test_lookahead_expires_and_no_more_absorb(self, vclock):
        """超过 lookahead 窗口后, 新位置不再被吸收"""
        cfg = _cfg_with_expected_count(count_tolerance=2, lock_lookahead=2)
        vsm = _make_vsm(cfg)

        _feed(vsm, _frame_partial_screws(n5=12, n7=4), repeat=6, vclock=vclock)
        step5n = next(s for s in vsm._per_item_steps if '5N' in s.step_label)
        assert len(step5n.items) == 12

        # 推进 3 秒 (> lookahead=2s) — 注意要持续喂 idle_timeout 默认 0 不会触发
        for _ in range(60):
            _feed(vsm, _frame_partial_screws(n5=12, n7=4), repeat=1, vclock=vclock)

        # 现在再出现 14 颗也不补
        _feed(vsm, _frame_18_screws(), repeat=5, vclock=vclock)

        assert len(step5n.items) == 12, \
            f"超 lookahead 后不应再补, 实际 {len(step5n.items)}"


# ==================== 5. 补丁 5: item_label 多标签 OR ====================
class TestPatch5_MultiLabelItem:
    def test_item_label_array_or_logic(self, vclock):
        """步骤 item_label=['5N螺丝','7N螺丝'] → 两种螺丝都算个体"""
        cfg = _cfg_with_expected_count()
        # 只看第三步 (涂黑) 的锁定情况
        vsm = _make_vsm(cfg)

        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active

        step_tuhei = next(s for s in vsm._per_item_steps if s.action_label == '涂黑')
        # 涂黑步骤的个体表应锁定 18 颗 (14 + 4)
        assert len(step_tuhei.items) == 18, \
            f"多标签 OR 应锁定 18 颗, 实际 {len(step_tuhei.items)}"

    def test_item_label_string_still_works(self, vclock):
        """单字符串配置不受多标签改动影响"""
        cfg = _cfg_with_expected_count()
        vsm = _make_vsm(cfg)
        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)

        step5n = next(s for s in vsm._per_item_steps if s.action_label == '扭5N螺丝')
        # 内部归一化成 tuple
        assert isinstance(step5n.item_label, tuple)
        assert step5n.item_label == ('5N螺丝',)
        assert len(step5n.items) == 14


# ==================== 6. 综合 E2E: 模拟本视频完整周期 ====================
class TestE2E_FullVideoScenario:
    def test_full_cycle_ok_with_all_features(self, vclock):
        """完整模拟本视频: 14 5N + 4 7N + 涂黑全部, 用 settle_after_all_done 自动 OK"""
        cfg = _cfg_with_expected_count(
            settle_after_all_done=1.5,
            cycle_max=300,
            idle_timeout=8,
            lock_lookahead=5,
        )
        vsm = _make_vsm(cfg)
        events = _capture_events(vsm)

        # 阶段 1: 工件放上, 稳定 6 帧 → 周期开始
        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active

        # 阶段 2: 逐颗扭 5N (14 颗)
        for i in range(14):
            action_frame = _frame_18_screws() + [_det('扭5N螺丝', *SCREW_POSITIONS_14[i])]
            _feed(vsm, action_frame, repeat=4, vclock=vclock)
            _feed(vsm, _frame_18_screws(), repeat=1, vclock=vclock)

        # 阶段 3: 逐颗扭 7N (4 颗)
        for i in range(4):
            action_frame = _frame_18_screws() + [_det('扭7N螺丝', *SCREW_POSITIONS_7N_4[i])]
            _feed(vsm, action_frame, repeat=4, vclock=vclock)
            _feed(vsm, _frame_18_screws(), repeat=1, vclock=vclock)

        # 阶段 4: 涂黑 18 颗 (顺序: 14 颗 5N 位置 + 4 颗 7N 位置)
        all_positions = list(SCREW_POSITIONS_14) + list(SCREW_POSITIONS_7N_4)
        for pos in all_positions:
            action_frame = _frame_18_screws() + [_det('涂黑', *pos)]
            _feed(vsm, action_frame, repeat=4, vclock=vclock)
            _feed(vsm, _frame_18_screws(), repeat=1, vclock=vclock)

        # 阶段 5: 保持 1.5s 全部完成 → OK
        _feed(vsm, _frame_18_screws(), repeat=40, vclock=vclock)

        ok_events = [e for e in events if e[0] == 1]
        assert ok_events, f"完整流程应 OK, events={events}"

    def test_missed_screws_then_idle_timeout_ng(self, vclock):
        """漏 2 颗 5N + 工件搬走 → 8s 空闲 → NG"""
        cfg = _cfg_with_expected_count(idle_timeout=5, lock_lookahead=5)
        vsm = _make_vsm(cfg)
        events = _capture_events(vsm)

        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active

        # 只扭 12 颗 5N + 4 颗 7N + 涂黑 16 颗 (漏 5N 第 13-14 颗, 涂黑漏 2 颗)
        for i in range(12):
            f = _frame_18_screws() + [_det('扭5N螺丝', *SCREW_POSITIONS_14[i])]
            _feed(vsm, f, repeat=4, vclock=vclock)
            _feed(vsm, _frame_18_screws(), repeat=1, vclock=vclock)

        # 工件搬走 (空帧)
        for _ in range(150):  # 7.5 秒
            vsm._update_step_stats([], _DUMMY_FRAME)
            vclock.advance(0.05)
            if not vsm._per_item_session.cycle_active:
                break

        assert not vsm._per_item_session.cycle_active
        ng_events = [e for e in events if e[0] == 2]
        assert ng_events, f"漏件 + 空闲超时应触发 NG, events={events}"

        # 检查 NG 详情
        detail = vsm._per_item_last_ng_detail
        assert detail is not None
        assert detail['missing_total'] > 0


# ==================== 7. 字段独立性 (per_item 与其他模式解耦) ====================
class TestFieldIsolation:
    """v3.9+ per_item 专属超时字段必须与项目级老字段隔离, 老项目兼容回落."""

    def test_per_item_reads_own_idle_timeout_field(self, vclock):
        """per_item 优先读 per_item.idle_timeout_sec, 与项目级 idle_timeout_seconds 解耦"""
        cfg = _cfg_with_expected_count(idle_timeout=5)
        # 故意把项目级 idle_timeout_seconds 调成天文数字, per_item 不应该读它
        cfg['pipeline_config']['idle_timeout_seconds'] = 99999
        cfg['pipeline_config']['cycle_max_duration'] = 99999
        vsm = _make_vsm(cfg)
        events = _capture_events(vsm)

        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active

        # 8 秒空帧 (> per_item.idle_timeout_sec=5, < 项目级 99999s)
        for _ in range(160):
            vsm._update_step_stats([], _DUMMY_FRAME)
            vclock.advance(0.05)
            if not vsm._per_item_session.cycle_active:
                break

        assert not vsm._per_item_session.cycle_active, \
            "per_item 应按自己的 idle_timeout_sec=5 触发, 不受项目级 99999 影响"
        assert any(e[0] == 2 for e in events)

    def test_legacy_config_fallback_to_project_level(self, vclock):
        """老 per_item 项目 (没配 per_item.idle_timeout_sec) → 从项目级 idle_timeout_seconds 兜底"""
        # 构造一个老式配置: per_item 内不带新字段, 但项目级配了 idle_timeout_seconds
        cfg = _cfg_with_expected_count()
        # 删掉 per_item 专属字段, 模拟老项目
        del cfg['pipeline_config']['per_item']['idle_timeout_sec']
        del cfg['pipeline_config']['per_item']['cycle_max_duration_sec']
        cfg['pipeline_config']['idle_timeout_seconds'] = 5    # 项目级老字段
        cfg['pipeline_config']['cycle_max_duration'] = 0
        vsm = _make_vsm(cfg)
        events = _capture_events(vsm)

        _feed(vsm, _frame_18_screws(), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active

        # 8 秒空帧
        for _ in range(160):
            vsm._update_step_stats([], _DUMMY_FRAME)
            vclock.advance(0.05)
            if not vsm._per_item_session.cycle_active:
                break

        assert not vsm._per_item_session.cycle_active, \
            "老 per_item 项目应能从项目级 idle_timeout_seconds 兜底"

    def test_stability_count_ratio_configurable(self, vclock):
        """stability_count_ratio 应能调节"宽严": ratio=0.5 时 8/14 也能开周期"""
        cfg = _cfg_with_expected_count(count_tolerance=0, count_ratio=0.5)
        vsm = _make_vsm(cfg)

        # 只检出 8 颗 5N (8/14 = 0.57 > ratio 0.5)
        _feed(vsm, _frame_partial_screws(n5=8, n7=4), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active, \
            "ratio=0.5 时, 8/14 检出应能开周期"

    def test_stability_count_ratio_strict(self, vclock):
        """stability_count_ratio=1.0 时, 必须全部检出才开周期"""
        cfg = _cfg_with_expected_count(count_tolerance=0, count_ratio=1.0)
        vsm = _make_vsm(cfg)

        # 检出 13 颗 5N (13/14 < ratio 1.0)
        _feed(vsm, _frame_partial_screws(n5=13, n7=4), repeat=6, vclock=vclock)
        assert not vsm._per_item_session.cycle_active, \
            "ratio=1.0 时, 13/14 不应开周期"

        # 检出 14 颗 (全部)
        _feed(vsm, _frame_partial_screws(n5=14, n7=4), repeat=6, vclock=vclock)
        assert vsm._per_item_session.cycle_active, \
            "ratio=1.0 时, 14/14 应开周期"


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s', '-x'])
