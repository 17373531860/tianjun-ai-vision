"""验证跟踪模式 settle 时的两个修复:

1. 严格过滤: 只有在"物品清单"(expected_items) 里的类别才写 StepRecord;
   不在清单里的启用类 (比如箱子/泡沫槽) 不应出现在 current_cycle_steps。

2. 空兜底: 当 _tracking_objects / _tracking_recently_lost 都被清空时,
   用 _tracking_class_counters / _event_counters 的累计数量虚拟补齐。

测试覆盖:
- 非容器模式: _settle_counting_cycle
- 容器模式:   _settle_box

运行:
    python tools/test_tracking_settle_filter.py
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在 import source 前设置, 避免加载真正的 DB / 日志目录
os.environ.setdefault("TIANJUN_DATA_DIR", "/tmp/tianjun_test_settle")

from backend.api.source import VideoSourceManager  # noqa: E402


def _make_mgr() -> VideoSourceManager:
    """构造一个最小可用的 VideoSourceManager, 把对外副作用全部 mock 掉。"""
    mgr = VideoSourceManager.__new__(VideoSourceManager)
    # 手工初始化仅被测试路径用到的属性
    mgr.project_config = {
        'pipeline_config': {},
        'steps_config': [],
        'events_config': [],
    }
    mgr.step_display_names = {}
    mgr._tracking_letter_map = {}

    # 跟踪状态
    mgr._tracking_objects = {}
    mgr._tracking_recently_lost = {}
    mgr._tracking_class_counters = {}
    mgr._event_counters = {}
    mgr._event_first_seen = {}
    mgr._event_last_seen = {}
    mgr._tracking_item_checklist = {}

    # 容器模式
    mgr._container_mode = False
    mgr._container_label = ''
    mgr._box_objects = {}
    mgr._box_settled_results = []
    mgr._tracking_cycle_active = True

    # 周期
    mgr.current_cycle_id = None
    mgr.current_cycle_uuid = None
    mgr.current_cycle_number = 0
    mgr.current_cycle_steps = []
    mgr.cycle_start_time = 0.0
    mgr.cycle_times = []
    mgr.ng_cycle_times = []
    mgr.last_cycle_end_time = None
    mgr.recording_enabled = False  # 这样 record_step 直接 no-op, 不碰 DB
    mgr.events_log = []
    mgr._event_seq = 0
    mgr.counters = {}
    mgr.ng_step_cycle_counts = {}
    mgr.step_conf_thresholds = {}
    mgr.step_accept_once = {}
    mgr.channel_id = 0
    mgr._mes_hook = None
    mgr.export_settings = {}
    mgr._alarm = None

    # record_step / _trigger_event 都替换为"只记录调用, 不碰 DB"
    mgr.record_step = MagicMock()
    # _trigger_event 需要真跑 (它里面会更新 current_cycle_steps 等), 但要避开 DB。
    # 简化: 直接 mock 掉, 本测试不关心事件是否触发。
    mgr._trigger_event = MagicMock(return_value=True)
    mgr._reset_counting_cycle = MagicMock()
    mgr.start_cycle = MagicMock()
    mgr._discard_empty_cycle = MagicMock()

    return mgr


def test_non_container_filters_out_unexpected_class():
    """非容器模式: 箱子 / 泡沫槽 在启用类里但不在物品清单 → 不应进 current_cycle_steps。"""
    mgr = _make_mgr()
    mgr._tracking_letter_map = {'螺丝': '螺丝', '箱子': '箱子', '泡沫槽': '泡沫槽'}

    # 跟踪了 2 个螺丝 + 1 个箱子 + 1 个泡沫槽
    mgr._tracking_objects = {
        10: {'class_name': '螺丝', 'display_id': '螺丝1', 'first_seen': 100.0, 'last_seen': 101.0, 'order_idx': 1},
        11: {'class_name': '螺丝', 'display_id': '螺丝2', 'first_seen': 100.5, 'last_seen': 101.5, 'order_idx': 2},
        12: {'class_name': '箱子', 'display_id': '箱子1', 'first_seen': 99.0, 'last_seen': 102.0, 'order_idx': 0},
        13: {'class_name': '泡沫槽', 'display_id': '泡沫槽1', 'first_seen': 99.5, 'last_seen': 102.0, 'order_idx': 0},
    }
    mgr._tracking_class_counters = {'螺丝': 2, '箱子': 1, '泡沫槽': 1}

    expected_items = {'螺丝': 2}  # 物品清单只要螺丝
    mgr._settle_counting_cycle(expected_items)

    steps = mgr.current_cycle_steps
    assert steps == ['螺丝1', '螺丝2'], f"期望 ['螺丝1', '螺丝2'], 实际 {steps}"
    # record_step 也只会被调 2 次
    assert mgr.record_step.call_count == 2, f"期望 record_step 调 2 次, 实际 {mgr.record_step.call_count}"
    print(f"  [PASS] 非容器 + 过滤: current_cycle_steps={steps}")


def test_non_container_empty_fallback_virtual_fill():
    """非容器模式: settle 时 objects/recently_lost 都空但 class_counters 有 → 虚拟补齐。"""
    mgr = _make_mgr()
    mgr._tracking_letter_map = {'螺丝': '螺丝', '盖子': '盖子'}

    # 物品都离开 + recently_lost 也被 expire 清了
    mgr._tracking_objects = {}
    mgr._tracking_recently_lost = {}
    mgr._tracking_class_counters = {'螺丝': 3, '盖子': 1}
    mgr.cycle_start_time = 100.0

    expected_items = {'螺丝': 3, '盖子': 1}
    mgr._settle_counting_cycle(expected_items)

    steps = mgr.current_cycle_steps
    assert len(steps) == 4, f"期望虚拟补齐 4 条, 实际 {len(steps)}: {steps}"
    # 应该是 3 条螺丝 + 1 条盖子 (具体 display_id 顺序取决于 letter_map)
    labels_seen = []
    for call in mgr.record_step.call_args_list:
        labels_seen.append(call.kwargs.get('step_label'))
    assert labels_seen.count('螺丝') == 3, f"螺丝 record_step 应调 3 次, 实际 {labels_seen}"
    assert labels_seen.count('盖子') == 1, f"盖子 record_step 应调 1 次, 实际 {labels_seen}"
    print(f"  [PASS] 非容器 + 空兜底: current_cycle_steps={steps}")


def test_non_container_empty_fallback_still_filters():
    """非容器模式: 虚拟补齐时也要过滤 —— 清单外的计数不补齐。"""
    mgr = _make_mgr()
    mgr._tracking_letter_map = {'螺丝': '螺丝', '箱子': '箱子'}

    mgr._tracking_objects = {}
    mgr._tracking_recently_lost = {}
    mgr._tracking_class_counters = {'螺丝': 2, '箱子': 1}  # 箱子不在清单
    mgr.cycle_start_time = 100.0

    expected_items = {'螺丝': 2}
    mgr._settle_counting_cycle(expected_items)

    steps = mgr.current_cycle_steps
    assert len(steps) == 2, f"期望只补齐 2 条螺丝, 实际 {len(steps)}: {steps}"
    for call in mgr.record_step.call_args_list:
        assert call.kwargs.get('step_label') == '螺丝', \
            f"不应出现非清单类 record_step: {call.kwargs}"
    print(f"  [PASS] 非容器 + 空兜底 + 过滤: current_cycle_steps={steps}")


def test_container_mode_writes_step_records():
    """容器模式: _settle_box 应调 record_step, 且过滤掉箱子本身。"""
    mgr = _make_mgr()
    mgr._container_mode = True
    mgr._container_label = '箱子'

    mgr._box_objects = {
        '箱子1': {
            'first_seen': 100.0,
            'last_seen': 105.0,
            'had_roi': True,
            'gone_frames': 30,
            'is_complete': True,
            'items_ever_seen': {
                20: {'label': '螺丝', 'display_id': '螺丝1', 'first_seen': 100.5, 'last_seen': 104.5},
                21: {'label': '螺丝', 'display_id': '螺丝2', 'first_seen': 101.0, 'last_seen': 104.8},
                22: {'label': '盖子', 'display_id': '盖子1', 'first_seen': 102.0, 'last_seen': 104.9},
            },
            'item_class_counts': {'螺丝': 2, '盖子': 1},
        }
    }

    expected_items = {'箱子': 1, '螺丝': 2, '盖子': 1}
    mgr._settle_box('箱子1', expected_items)

    steps = mgr.current_cycle_steps
    assert '箱子1' not in steps, f"箱子作为容器类不应进 StepRecord: {steps}"
    assert '螺丝1' in steps and '螺丝2' in steps and '盖子1' in steps, \
        f"物品未齐: {steps}"
    # record_step 应被调 3 次
    assert mgr.record_step.call_count == 3, \
        f"期望 record_step 3 次 (2 螺丝 + 1 盖子), 实际 {mgr.record_step.call_count}"
    print(f"  [PASS] 容器模式 + record_step: current_cycle_steps={steps}")


def test_container_mode_empty_fallback():
    """容器模式: items_ever_seen 被清但 item_class_counts 还在 → 虚拟补齐。"""
    mgr = _make_mgr()
    mgr._container_mode = True
    mgr._container_label = '箱子'
    mgr._tracking_letter_map = {'螺丝': '螺丝', '盖子': '盖子'}

    mgr._box_objects = {
        '箱子1': {
            'first_seen': 100.0,
            'last_seen': 105.0,
            'had_roi': True,
            'gone_frames': 30,
            'is_complete': True,
            'items_ever_seen': {},  # 被清空
            'item_class_counts': {'螺丝': 2, '盖子': 1},  # 但 counter 还在
        }
    }

    expected_items = {'箱子': 1, '螺丝': 2, '盖子': 1}
    mgr._settle_box('箱子1', expected_items)

    steps = mgr.current_cycle_steps
    assert len(steps) == 3, f"期望虚拟补齐 3 条, 实际: {steps}"
    # record_step 应被调 3 次
    assert mgr.record_step.call_count == 3, \
        f"期望 record_step 3 次, 实际 {mgr.record_step.call_count}"
    print(f"  [PASS] 容器模式 + 空兜底: current_cycle_steps={steps}")


def main():
    tests = [
        test_non_container_filters_out_unexpected_class,
        test_non_container_empty_fallback_virtual_fill,
        test_non_container_empty_fallback_still_filters,
        test_container_mode_writes_step_records,
        test_container_mode_empty_fallback,
    ]
    fails = 0
    for t in tests:
        print(f"-- {t.__name__}")
        try:
            t()
        except AssertionError as e:
            fails += 1
            print(f"  [FAIL] {e}")
        except Exception as e:
            fails += 1
            import traceback
            traceback.print_exc()
            print(f"  [ERROR] {e}")
    print("----")
    if fails == 0:
        print(f"OK: {len(tests)}/{len(tests)} passed.")
    else:
        print(f"FAILED: {fails}/{len(tests)} failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
