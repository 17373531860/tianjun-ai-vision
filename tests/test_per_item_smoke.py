"""per_item 「逐件覆盖」模式 — 冒烟测试 (v3.6.0+).

直接构造真实 VideoSourceManager 实例 (绕过摄像头/推理), 通过逐帧喂
detections 验证 PerItemMixin 的完整状态机:

1. 稳定窗口判定 → 周期开始
2. 每颗螺丝按位置 IoU 锁定个体表 (12 颗)
3. "打螺丝" 框逐颗覆盖, 持续 sustain_frames 后翻转 covered=true
4. 全部覆盖 → 步骤完成 → step_counts 累加 + current_cycle_steps 更新
5. "划螺丝" 步骤同理
6. "翻面" 标签稳定出现 → 周期结算 → 触发 _trigger_event(1, ...) OK
7. 漏打 2 颗 → 翻面 → 触发 _trigger_event(2, ...) NG, 详情挂在
   _per_item_last_ng_detail
"""
from __future__ import annotations

import pytest
import numpy as np

from backend.api.source import VideoSourceManager


# ==================== 测试工具 ====================
# 12 颗螺丝在画面里的固定位置 (归一化坐标)
# 横向一排, 间隔 0.07, 每颗 0.04 x 0.04
SCREW_POSITIONS = [
    (0.05 + i * 0.07, 0.50, 0.04, 0.04) for i in range(12)
]

# 占位 frame (per_item 路径里不会真用到, 因为 base64 截图被绕开)
_DUMMY_FRAME = np.zeros((100, 100, 3), dtype=np.uint8)


def _det(label: str, x: float, y: float, w: float, h: float, conf: float = 0.85):
    """构造一条标准的 detection dict"""
    return {
        'label': label,
        'x': x, 'y': y, 'w': w, 'h': h,
        'confidence': conf,
        'class_name': label,
    }


def _screws_frame():
    """画面里只有 12 颗静止螺丝, 没有工序动作"""
    return [_det('螺丝', *p) for p in SCREW_POSITIONS]


def _screws_frame_with_action(action_label: str, target_idx: int):
    """12 颗螺丝静止 + 一个工序框覆盖在第 target_idx 颗位置上.

    注意 'screw' box 类别仍是 '螺丝', 工序框是另一个类别 (打螺丝/划螺丝/翻面).
    用户的标注策略: 同一物理螺丝在被打的瞬间 label 切到 '打螺丝', 但模型仍可能
    并行输出 '螺丝' 框 — 这里两者都模拟出来, 让覆盖判定的 IoU 匹配走完整路径.
    """
    base = _screws_frame()
    # 在第 target_idx 颗位置叠加一个工序框 (IoU > 0.9, 覆盖判定会命中)
    base.append(_det(action_label, *SCREW_POSITIONS[target_idx]))
    return base


def _make_per_item_project_config(min_item_count='auto'):
    """构造一个最小可跑的 per_item 项目配置"""
    return {
        'id': 8888,
        'name': 'TEST_PER_ITEM',
        'task_type': 'detection',
        'logic_mode': 'per_item',
        'steps_config': [
            {
                'id': 1, 'label': '打螺丝', 'displayLabel': '打螺丝',
                'enabled': True, 'threshold': 50,
                'per_item': {
                    'item_label': '螺丝',
                    'action_label': '打螺丝',
                    'item_tracking_iou': 0.3,
                    'coverage_iou': 0.3,
                    'sustain_frames': 5,
                    'completion': 'all_covered',
                    'min_item_count': min_item_count,
                },
            },
            {
                'id': 2, 'label': '划螺丝', 'displayLabel': '划螺丝',
                'enabled': True, 'threshold': 50,
                'per_item': {
                    'item_label': '螺丝',
                    'action_label': '划螺丝',
                    'item_tracking_iou': 0.3,
                    'coverage_iou': 0.3,
                    'sustain_frames': 5,
                    'completion': 'all_covered',
                },
            },
            {
                'id': 3, 'label': '翻面', 'displayLabel': '翻面',
                'enabled': True, 'threshold': 50,
                # 不填 per_item, 由 finish_label 驱动
            },
        ],
        'events_config': [],
        'pipeline_config': {
            'per_item': {
                'stability_window_frames': 10,
                'stability_iou_threshold': 0.7,
                'item_timeout_seconds': 3.0,
                'lock_count_on_start': True,
                'finish_label': '翻面',
                'finish_sustain_frames': 3,
            },
        },
    }


def _make_vsm_per_item(min_item_count='auto'):
    """构造已加载 per_item 项目配置的 VSM 实例"""
    vsm = VideoSourceManager(channel_id=0)
    vsm.set_project_config(_make_per_item_project_config(min_item_count))
    return vsm


def _capture_events(vsm):
    """劫持 _trigger_event, 收集所有 (event_id, reason) 调用"""
    captured = []
    def fake_trigger(event_id, reason):
        captured.append((event_id, reason))
        print(f">>> _trigger_event({event_id}, '{reason}')")
        return True
    vsm._trigger_event = fake_trigger
    return captured


def _feed(vsm, dets_list, repeat=1):
    """喂 repeat 帧, 每帧 detections 相同"""
    for _ in range(repeat):
        vsm._update_step_stats(list(dets_list), _DUMMY_FRAME)


def _cover_one_screw(vsm, action_label: str, idx: int, frames: int = 8):
    """模拟工人打/划第 idx 颗螺丝 frames 帧, 然后离开 (再喂 2 帧只螺丝)"""
    for _ in range(frames):
        vsm._update_step_stats(
            _screws_frame_with_action(action_label, idx), _DUMMY_FRAME)
    # 工人手离开, 工序框消失 (让 consecutive_overlap_frames 归零, 但 covered 已锁定)
    for _ in range(2):
        vsm._update_step_stats(_screws_frame(), _DUMMY_FRAME)


# ==================== 1. 配置加载 ====================
class TestPerItemConfigApply:
    def test_per_item_config_loaded(self):
        """per_item 配置加载后, _per_item_steps 应该有 2 个步骤 (翻面不在里面)"""
        vsm = _make_vsm_per_item()
        assert vsm._per_item_config is not None
        assert vsm._per_item_config['finish_label'] == '翻面'
        assert len(vsm._per_item_steps) == 2

        # v3.56+: action_label 内部为 tuple (多标签 OR), 展开后应含两个动作
        labels = [lbl for s in vsm._per_item_steps for lbl in s.action_label]
        assert '打螺丝' in labels
        assert '划螺丝' in labels

    def test_non_per_item_config_disables_mixin(self):
        """sequential 模式项目应该不启用 per_item"""
        vsm = VideoSourceManager(channel_id=0)
        vsm.set_project_config({
            'id': 1, 'logic_mode': 'sequential',
            'steps_config': [], 'pipeline_config': {},
        })
        assert vsm._per_item_config is None
        assert vsm.get_per_item_state() is None


# ==================== 2. 稳定窗口 + 周期开始 ====================
class TestStabilityWindowAndCycleStart:
    def test_cycle_starts_after_stable_window(self):
        """连续 10 帧稳定的 12 颗螺丝 → 周期开始"""
        vsm = _make_vsm_per_item()
        events = _capture_events(vsm)

        # 喂 12 帧稳定螺丝 (略多于稳定窗口 10)
        _feed(vsm, _screws_frame(), repeat=12)

        assert vsm._per_item_session.cycle_active, \
            "稳定窗口达标后周期应已开始"
        # 每步个体表应锁定 12 颗
        for step in vsm._per_item_steps:
            assert len(step.items) == 12, \
                f"步骤 [{step.step_label}] 个体表应锁定 12 颗, 实际 {len(step.items)}"
        assert events == [], "周期开始不应触发任何事件"

    def test_cycle_not_started_with_unstable_count(self):
        """画面里螺丝数量在波动 (10/11/12 交替) → 不算稳定"""
        vsm = _make_vsm_per_item()
        for fr_idx in range(12):
            n = 10 + (fr_idx % 3)        # 10/11/12 循环
            dets = [_det('螺丝', *p) for p in SCREW_POSITIONS[:n]]
            vsm._update_step_stats(dets, _DUMMY_FRAME)
        assert not vsm._per_item_session.cycle_active

    def test_cycle_not_started_with_jitter(self):
        """画面里 12 颗螺丝数量稳定, 但位置抖动很大 (IoU 低) → 不算稳定"""
        vsm = _make_vsm_per_item()
        for fr_idx in range(12):
            # 偶数帧位置 A, 奇数帧位置 B (IoU = 0 完全不重叠)
            offset = 0.0 if (fr_idx % 2 == 0) else 0.5
            dets = [_det('螺丝', p[0] + offset, p[1], p[2], p[3])
                    for p in SCREW_POSITIONS]
            vsm._update_step_stats(dets, _DUMMY_FRAME)
        assert not vsm._per_item_session.cycle_active


# ==================== 3. 完整 OK 路径 ====================
class TestHappyPath:
    def test_full_cycle_ok(self):
        """打 12 颗 → 划 12 颗 → 翻面 → OK 事件"""
        vsm = _make_vsm_per_item()
        events = _capture_events(vsm)

        # 阶段 1: 稳定窗口
        _feed(vsm, _screws_frame(), repeat=12)
        assert vsm._per_item_session.cycle_active

        # 阶段 2: 逐颗打螺丝
        for i in range(12):
            _cover_one_screw(vsm, '打螺丝', i, frames=8)

        # 打螺丝步骤应该完成
        打_step = next(s for s in vsm._per_item_steps if '打螺丝' in s.action_label)
        assert 打_step.completed, f"打螺丝步骤应完成, covered_count={打_step.covered_count()}/{len(打_step.items)}"
        assert 打_step.covered_count() == 12

        # 兼容字段已回写
        assert vsm.step_counts.get('打螺丝', 0) >= 1
        assert '打螺丝' in vsm.current_cycle_steps

        # 阶段 3: 逐颗划螺丝
        for i in range(12):
            _cover_one_screw(vsm, '划螺丝', i, frames=8)

        划_step = next(s for s in vsm._per_item_steps if '划螺丝' in s.action_label)
        assert 划_step.completed

        # 阶段 4: 翻面 (连续 5 帧, 超过 finish_sustain_frames=3)
        _feed(vsm, [_det('翻面', 0.4, 0.1, 0.2, 0.1)], repeat=5)

        # OK 事件应被触发
        ok_events = [e for e in events if e[0] == 1]
        assert ok_events, f"应触发 OK 事件 (event_id=1), 实际 events={events}"
        # 周期应已重置
        assert not vsm._per_item_session.cycle_active

    def test_rework_supported(self):
        """工人补打场景: 第一次只打 10 颗 → 没结算 → 补打剩 2 颗 → 翻面 → OK"""
        vsm = _make_vsm_per_item()
        events = _capture_events(vsm)
        _feed(vsm, _screws_frame(), repeat=12)

        # 先打 10 颗
        for i in range(10):
            _cover_one_screw(vsm, '打螺丝', i, frames=8)

        打_step = next(s for s in vsm._per_item_steps if '打螺丝' in s.action_label)
        assert not 打_step.completed
        assert 打_step.covered_count() == 10

        # 补打剩 2 颗
        for i in [10, 11]:
            _cover_one_screw(vsm, '打螺丝', i, frames=8)
        assert 打_step.completed

        # 再划 12 颗
        for i in range(12):
            _cover_one_screw(vsm, '划螺丝', i, frames=8)

        _feed(vsm, [_det('翻面', 0.4, 0.1, 0.2, 0.1)], repeat=5)
        assert any(e[0] == 1 for e in events), f"补打后翻面应 OK, events={events}"


# ==================== 4. NG 路径 ====================
class TestNGPaths:
    def test_missed_screws_trigger_ng(self):
        """漏打 2 颗 → 翻面 → NG, 且 last_ng_detail 含未覆盖个体清单"""
        vsm = _make_vsm_per_item()
        events = _capture_events(vsm)
        _feed(vsm, _screws_frame(), repeat=12)

        # 只打 10 颗 (漏掉第 10 和第 11 颗)
        for i in range(10):
            _cover_one_screw(vsm, '打螺丝', i, frames=8)

        # 划全部 12 颗
        for i in range(12):
            _cover_one_screw(vsm, '划螺丝', i, frames=8)

        # 翻面
        _feed(vsm, [_det('翻面', 0.4, 0.1, 0.2, 0.1)], repeat=5)

        # 应触发 NG (event_id=2)
        ng_events = [e for e in events if e[0] == 2]
        assert ng_events, f"漏打应触发 NG, events={events}"

        # NG 详情应是结构化 dict, 内含 steps_failed 列出"打螺丝"未完成情况
        detail = vsm._per_item_last_ng_detail
        assert detail is not None and isinstance(detail, dict), \
            f"last_ng_detail 应为 dict, 实际={type(detail).__name__}"
        assert detail.get('missing_total') == 2
        assert detail.get('cycle_duration_sec') is not None
        assert isinstance(detail.get('steps_failed'), list)
        打_detail = next(
            (d for d in detail['steps_failed'] if d['step_label'] == '打螺丝'),
            None,
        )
        assert 打_detail is not None
        assert 打_detail['covered_count'] == 10
        assert 打_detail['total'] == 12
        assert len(打_detail['missing_item_ids']) == 2

    def test_sustain_frames_protection_against_glitch(self):
        """工序框只闪现 1-2 帧 (少于 sustain_frames=5) 不应覆盖"""
        vsm = _make_vsm_per_item()
        _capture_events(vsm)
        _feed(vsm, _screws_frame(), repeat=12)

        # 第 0 颗工序框只闪现 3 帧 (< sustain_frames=5)
        _feed(vsm, _screws_frame_with_action('打螺丝', 0), repeat=3)
        # 工序框消失
        _feed(vsm, _screws_frame(), repeat=2)

        打_step = next(s for s in vsm._per_item_steps if '打螺丝' in s.action_label)
        assert 打_step.covered_count() == 0, \
            f"闪现 3 帧不应触发覆盖, 实际 covered={打_step.covered_count()}"


# ==================== 5. state 快照 ====================
class TestStateSnapshot:
    def test_state_snapshot_shape(self):
        """get_per_item_state 返回的字段对前端契约"""
        vsm = _make_vsm_per_item()
        _feed(vsm, _screws_frame(), repeat=12)

        state = vsm.get_per_item_state()
        assert state is not None
        assert state['enabled'] is True
        assert state['cycle_active'] is True
        assert 'config' in state
        assert state['config']['finish_label'] == '翻面'
        assert len(state['steps']) == 2

        step = state['steps'][0]
        assert 'item_label' in step
        assert 'action_label' in step
        assert 'total' in step
        assert 'covered_count' in step
        assert 'items' in step
        assert len(step['items']) == 12
        item = step['items'][0]
        assert 'id' in item
        assert 'bbox' in item
        assert 'covered' in item


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s', '-x'])
