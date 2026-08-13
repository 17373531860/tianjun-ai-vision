# -*- coding: utf-8 -*-
"""v3.50 齐件即结算 (捷昌二期) — 跟踪模式 ROI离开 / 容器模式单测。

覆盖 (对应计划 A 部分):
  1. steps_config 解析: settle_confirm_frames 只收 >1 的 track 步骤
  2. 确认放入帧数把关: 新物品连续 N 帧才入账, 断帧作废重数
  3. ROI离开: 账本凑齐当帧立即结算, 不等消失确认
  4. 防二次入账: 结算时在场物品进豁免名单, 离场前不开新周期; ID 漂移接管
  5. 容器模式: 箱内曾齐过即结该箱 + "已结算等离开"状态机 (禁重建/ID 漂移
     接管/gone 确认只清状态不二次结算)
  6. 扫码配对互斥: scan_pair 激活时齐件即结不触发, 只保留 sticky was_complete
  7. 开关关闭 (默认) = 现状行为零差异

v3.50.1 追加 (开关开启 = 取消原有全部结算路径):
  8. ROI离开: 没凑齐时原"消失确认结算"路径被取消, 账本挂起不出账
  9. ROI离开: 周期超时兜底强制 NG (配了秒数才生效; 0 = 永久挂起等码/人工)
  10. 容器模式: 未凑齐箱离开只挂账不判 NG; 箱龄超时兜底强制 NG
  11. mes_hooks 新码强制收旧账: 开关+策略守门 / scan_pair 互斥
"""
import time
import types

import pytest

from backend.api.source_tracking_mixin import TrackingMixin
from backend.api.source_container_grouping_mixin import ContainerGroupingMixin


def _iou(a: dict, b: dict) -> float:
    ax1, ay1 = a.get('x', 0), a.get('y', 0)
    ax2, ay2 = ax1 + a.get('w', 0), ay1 + a.get('h', 0)
    bx1, by1 = b.get('x', 0), b.get('y', 0)
    bx2, by2 = bx1 + b.get('w', 0), by1 + b.get('h', 0)
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


# ==================== ROI 离开 (TrackingMixin 全流程) ====================

class FakeVSM(TrackingMixin):
    """最小可跑 _update_tracking_stats 的宿主: 掐掉截图/ROI/结算落库副作用。"""

    def __init__(self, pipeline_cfg: dict, steps_cfg: list):
        self.project_config = {
            'pipeline_config': pipeline_cfg,
            'steps_config': steps_cfg,
        }
        self.channel_id = 0
        self.fps_inference = 15
        self._container_mode = False
        self._container_label = ''
        self._force_settling_in_progress = False
        self._tracking_external_cycle = False
        self._tracking_cycle_active = False
        self._tracking_was_complete = False
        self._tracking_gone_frames = 0
        self._tracking_order_seq = 0
        self.cycle_start_time = 0
        self.cycle_start_frame_pos = 0
        self.current_cycle_uuid = None
        self._tracking_objects = {}
        self._tracking_recently_lost = {}
        self._tracking_registered_positions = {}
        self._tracking_appearance = {}
        self._tracking_lost_frames = {}
        self._tracking_display_map = {}
        self._tracking_transferred_ids = {}
        self._tracking_locked_ids = {}
        self._tracking_stable_frames = {}
        self._tracking_class_counters = {}
        self._tracking_entry_pending = {}
        self._settle_complete_exempt = {}
        self._event_state = {}
        self._event_counters = {}
        self._event_visible_frames = {}
        self._event_gone_frames_count = {}
        self._event_first_seen = {}
        self._event_last_seen = {}
        self._stack_state = {}
        self._stack_counters = {}
        self.settle_calls = []
        self.start_cycle_calls = 0

    # --- 宿主桩 ---
    def start_cycle(self):
        self.start_cycle_calls += 1

    def _video_frame_pos(self):
        return 0

    def _det_passes_roi_for_label(self, det, lbl):
        return True

    def _get_display_prefix(self, class_name):
        return class_name

    @staticmethod
    def _bbox_iou(a, b):
        return _iou(a, b)

    def _try_reid_match(self, label, new_bbox, lost_bbox):
        return False

    def _boost_score_with_appearance(self, score, did, bbox, frame):
        return score

    def _rebuild_checklist(self, expected_items):
        pass

    def _tracking_capture_screenshots(self, seen_track_ids, original_frame):
        pass

    def _tracking_check_settlement(self, *a, **kw):
        # 常规消失确认结算与本测试无关, 恒不触发
        return False

    def _settle_counting_cycle(self, expected_items, check_order, expected_order):
        self.settle_calls.append(dict(self._tracking_class_counters))
        # 模拟真实 _reset_counting_cycle 的关键副作用
        self._tracking_cycle_active = False
        self._tracking_was_complete = False
        self._tracking_objects = {}
        self._tracking_display_map = {}
        self._tracking_lost_frames = {}
        self._tracking_class_counters = {}
        self._tracking_entry_pending = {}


def _mk_vsm(*, settle_on_complete=True, confirm_frames=None, expected=None):
    steps = [{'label': 'screw', 'enabled': True, 'count_mode': 'track'}]
    if confirm_frames:
        steps[0]['settle_confirm_frames'] = confirm_frames
    return FakeVSM({
        'tracking_settle_on_complete': settle_on_complete,
        'tracking_cycle_strategy': 'roi_exit',
        'counting_expected_items': expected or {'screw': 2},
    }, steps)


def _det(tid, x=0.1, y=0.1, w=0.2, h=0.2, label='screw'):
    return {'label': label, 'track_id': tid, 'x': x, 'y': y,
            'w': w, 'h': h, 'confidence': 0.9}


def test_step_config_parses_confirm_frames():
    vsm = FakeVSM({}, [
        {'label': 'a', 'enabled': True, 'count_mode': 'track',
         'settle_confirm_frames': 5},
        {'label': 'b', 'enabled': True, 'count_mode': 'track',
         'settle_confirm_frames': 1},          # 1 = 现状, 不进表
        {'label': 'c', 'enabled': False, 'count_mode': 'track',
         'settle_confirm_frames': 4},          # 禁用步骤不进表
        {'label': 'd', 'enabled': True, 'count_mode': 'event',
         'settle_confirm_frames': 4,
         'event_required_count': 1},           # event 步骤不进表
    ])
    cfg = vsm._tracking_load_step_config({})
    assert cfg['entry_confirm_frames'] == {'a': 5}


def test_roi_exit_settles_immediately_when_complete():
    vsm = _mk_vsm()
    vsm._update_tracking_stats([_det(1)], None)
    assert vsm._tracking_class_counters == {'screw': 1}
    assert vsm.settle_calls == [], "只凑到 1/2 不该结算"

    vsm._update_tracking_stats([_det(1), _det(2, x=0.5)], None)
    assert len(vsm.settle_calls) == 1, "凑齐当帧应立即结算"
    assert vsm.settle_calls[0] == {'screw': 2}
    # 在场 2 件全部进豁免名单
    assert set(vsm._settle_complete_exempt.keys()) == {1, 2}


def test_exempt_prevents_recount_until_gone():
    vsm = _mk_vsm()
    vsm._update_tracking_stats([_det(1), _det(2, x=0.5)], None)
    assert len(vsm.settle_calls) == 1

    # 结算后同两件还在画面里: 不入账 / 不开新周期 / 不二次结算
    for _ in range(3):
        vsm._update_tracking_stats([_det(1), _det(2, x=0.5)], None)
    assert vsm._tracking_class_counters == {}
    assert vsm._tracking_cycle_active is False
    assert len(vsm.settle_calls) == 1


def test_exempt_id_drift_transfer():
    vsm = _mk_vsm()
    vsm._update_tracking_stats([_det(1), _det(2, x=0.5)], None)
    assert set(vsm._settle_complete_exempt.keys()) == {1, 2}

    # ByteTrack 把 1 号换成 99 号 (同位置): 豁免应转移, 仍不入账
    vsm._update_tracking_stats([_det(99), _det(2, x=0.5)], None)
    assert 99 in vsm._settle_complete_exempt
    assert 1 not in vsm._settle_complete_exempt
    assert vsm._tracking_class_counters == {}
    assert len(vsm.settle_calls) == 1


def test_confirm_frames_gate():
    vsm = _mk_vsm(confirm_frames=3, expected={'screw': 1})
    # 第 1/2 帧: 缓冲中不入账
    vsm._update_tracking_stats([_det(1)], None)
    assert vsm._tracking_class_counters == {}
    assert vsm._tracking_entry_pending[1]['frames'] == 1
    vsm._update_tracking_stats([_det(1)], None)
    assert vsm._tracking_entry_pending[1]['frames'] == 2
    assert vsm.settle_calls == []
    # 第 3 帧: 达标入账 → 期望 1 件 → 齐件即结算
    vsm._update_tracking_stats([_det(1)], None)
    assert len(vsm.settle_calls) == 1
    assert 1 not in vsm._tracking_entry_pending


def test_confirm_frames_requires_consecutive():
    vsm = _mk_vsm(confirm_frames=3, expected={'screw': 1})
    vsm._update_tracking_stats([_det(1)], None)
    vsm._update_tracking_stats([_det(1)], None)
    assert vsm._tracking_entry_pending[1]['frames'] == 2
    # 断一帧: 缓冲作废
    vsm._update_tracking_stats([], None)
    assert 1 not in vsm._tracking_entry_pending
    # 重新出现: 从 1 数起, 不入账
    vsm._update_tracking_stats([_det(1)], None)
    assert vsm._tracking_entry_pending[1]['frames'] == 1
    assert vsm._tracking_class_counters == {}


def test_confirm_frames_ignored_when_switch_off():
    """开关关时 settle_confirm_frames 不生效 = 现状看见即入账。"""
    vsm = _mk_vsm(settle_on_complete=False, confirm_frames=3)
    vsm._update_tracking_stats([_det(1)], None)
    assert vsm._tracking_class_counters == {'screw': 1}
    assert vsm._tracking_entry_pending == {}


def test_switch_off_no_immediate_settle():
    """默认关: 凑齐只置 sticky was_complete, 不立即结算 (现状零差异)。"""
    vsm = _mk_vsm(settle_on_complete=False)
    vsm._update_tracking_stats([_det(1), _det(2, x=0.5)], None)
    assert vsm._tracking_was_complete is True
    assert vsm.settle_calls == []
    assert vsm._settle_complete_exempt == {}


def test_scan_pair_mutex_skips_immediate_settle(monkeypatch):
    vsm = _mk_vsm()
    fake_mes = types.SimpleNamespace(
        enabled=True, is_scan_pair_mode=lambda ch: True)
    import backend.services.mes_hooks as mh
    monkeypatch.setattr(mh, 'get_mes_hook', lambda: fake_mes)

    vsm._update_tracking_stats([_det(1), _det(2, x=0.5)], None)
    assert vsm.settle_calls == [], "scan_pair 激活时齐件即结必须让位"
    assert vsm._tracking_was_complete is True, "sticky 曾齐过仍要维护"


# ==================== 容器模式 (ContainerGroupingMixin) ====================

class FakeBoxHost(ContainerGroupingMixin):
    def __init__(self, steps_cfg=None, pipeline_extra=None):
        pcfg = {'tracking_settle_on_complete': True,
                'container_box_mode': 'single'}
        pcfg.update(pipeline_extra or {})
        self.project_config = {
            'pipeline_config': pcfg,
            'steps_config': steps_cfg or [
                {'label': 'screw', 'enabled': True, 'count_mode': 'track'}],
        }
        self.channel_id = 0
        self._container_label = 'box'
        self._container_mode = True
        self._force_settling_in_progress = False
        self.current_cycle_uuid = 'uuid-x'
        self._tracking_objects = {}
        self._tracking_recently_lost = {}
        self._tracking_display_map = {}
        self._box_objects = {}
        self._box_counter = 0
        self._box_settled_waiting_exit = {}
        self._container_entry_pending = {}
        self.settle_calls = []

    @staticmethod
    def _bbox_iou(a, b):
        return _iou(a, b)

    def _is_in_roi(self, det):
        return True

    def start_cycle(self):
        pass

    def _settle_box(self, box_did, expected_items, **kw):
        self.settle_calls.append(box_did)
        # 真实 _settle_box → _end_cycle 会清空 _box_objects
        self._box_objects.pop(box_did, None)


BOX_BBOX = {'x': 0.1, 'y': 0.1, 'w': 0.6, 'h': 0.6}
EXPECTED = {'box': 1, 'screw': 2}


def _put_box(host, tid=100, bbox=None):
    host._tracking_objects[tid] = {
        'class_name': 'box', 'display_id': '箱子1',
        'bbox': dict(bbox or BOX_BBOX), 'first_seen': 1.0, 'last_seen': 1.0,
    }


def _put_item(host, tid, x=0.2):
    host._tracking_objects[tid] = {
        'class_name': 'screw', 'display_id': f'screw{tid}',
        'bbox': {'x': x, 'y': 0.3, 'w': 0.05, 'h': 0.05},
        'first_seen': 1.0, 'last_seen': 1.0,
    }


def test_container_settles_box_when_complete():
    host = FakeBoxHost()
    _put_box(host)
    _put_item(host, 1, x=0.2)
    _put_item(host, 2, x=0.4)
    host._update_container_grouping(EXPECTED, 10.0, gone_confirm_frames=5)

    assert host.settle_calls == ['箱子1'], "箱内凑齐当帧应立即结该箱"
    assert 100 in host._box_settled_waiting_exit, "箱子应转入等待离场状态"


def test_settled_waiting_box_not_rebuilt_and_items_not_counted():
    host = FakeBoxHost()
    _put_box(host)
    _put_item(host, 1, x=0.2)
    _put_item(host, 2, x=0.4)
    host._update_container_grouping(EXPECTED, 10.0, gone_confirm_frames=5)
    assert len(host.settle_calls) == 1

    # 已结算箱 + 里面的物品还在画面: 不重建 ledger / 不再结算
    for t in (11.0, 12.0, 13.0):
        host._update_container_grouping(EXPECTED, t, gone_confirm_frames=5)
    assert host._box_objects == {}, "等待离场的箱子不得重建 _box_objects"
    assert len(host.settle_calls) == 1


def test_settled_waiting_box_id_drift_takeover():
    host = FakeBoxHost()
    _put_box(host, tid=100)
    _put_item(host, 1, x=0.2)
    _put_item(host, 2, x=0.4)
    host._update_container_grouping(EXPECTED, 10.0, gone_confirm_frames=5)

    # ByteTrack 换 ID: 同位置箱子换成 tid=200 → 等待状态接管, 不重建
    host._tracking_objects.pop(100)
    _put_box(host, tid=200)
    host._update_container_grouping(EXPECTED, 11.0, gone_confirm_frames=5)
    assert 200 in host._box_settled_waiting_exit
    assert 100 not in host._box_settled_waiting_exit
    assert host._box_objects == {}
    assert len(host.settle_calls) == 1


def test_settled_waiting_box_cleared_after_gone_confirm():
    host = FakeBoxHost()
    _put_box(host)
    _put_item(host, 1, x=0.2)
    _put_item(host, 2, x=0.4)
    host._update_container_grouping(EXPECTED, 10.0, gone_confirm_frames=3)
    assert 100 in host._box_settled_waiting_exit

    # 箱子(和物品)彻底离场: 连续 3 帧未见 → 清状态, 不二次结算
    host._tracking_objects.clear()
    for t in (11.0, 12.0, 13.0):
        host._update_container_grouping(EXPECTED, t, gone_confirm_frames=3)
    assert host._box_settled_waiting_exit == {}
    assert len(host.settle_calls) == 1


def test_container_confirm_frames_gate():
    host = FakeBoxHost(steps_cfg=[
        {'label': 'screw', 'enabled': True, 'count_mode': 'track',
         'settle_confirm_frames': 2}])
    _put_box(host)
    _put_item(host, 1, x=0.2)
    _put_item(host, 2, x=0.4)
    # 第 1 帧: 物品进缓冲, 未入账 → 未凑齐不结算
    host._update_container_grouping(EXPECTED, 10.0, gone_confirm_frames=5)
    assert host.settle_calls == []
    assert host._box_objects['箱子1']['item_class_counts'] == {}
    # 第 2 帧: 连续达标 → 入账 → 凑齐即结
    host._update_container_grouping(EXPECTED, 11.0, gone_confirm_frames=5)
    assert host.settle_calls == ['箱子1']


def test_container_scan_pair_mutex(monkeypatch):
    host = FakeBoxHost()
    fake_mes = types.SimpleNamespace(
        enabled=True, is_scan_pair_mode=lambda ch: True)
    import backend.services.mes_hooks as mh
    monkeypatch.setattr(mh, 'get_mes_hook', lambda: fake_mes)

    _put_box(host)
    _put_item(host, 1, x=0.2)
    _put_item(host, 2, x=0.4)
    host._update_container_grouping(EXPECTED, 10.0, gone_confirm_frames=5)
    assert host.settle_calls == [], "scan_pair 激活时齐件即结必须让位"
    assert host._box_objects['箱子1']['was_complete'] is True


def test_container_empty_expected_no_false_settle():
    """期望清单只有容器自己 → is_complete 恒 True, 不得建箱当帧误结算。"""
    host = FakeBoxHost()
    _put_box(host)
    host._update_container_grouping({'box': 1}, 10.0, gone_confirm_frames=5)
    assert host.settle_calls == []
    assert host._box_settled_waiting_exit == {}


def test_container_switch_off_zero_diff():
    host = FakeBoxHost(pipeline_extra={'tracking_settle_on_complete': False})
    _put_box(host)
    _put_item(host, 1, x=0.2)
    _put_item(host, 2, x=0.4)
    host._update_container_grouping(EXPECTED, 10.0, gone_confirm_frames=5)
    # 现状: 凑齐只置 sticky, 等 gone-confirm 才结
    assert host.settle_calls == []
    assert host._box_objects['箱子1']['was_complete'] is True
    assert host._box_settled_waiting_exit == {}


# ============ v3.50.1: 开关开启 = 取消原有全部结算路径 ============

def test_roi_incomplete_original_settle_path_cancelled():
    """开关开: 没凑齐时物品离开画面不出账 — 原消失确认结算路径整段跳过。

    把 _tracking_check_settlement 桩成恒 True (老语义下必然出账),
    开关开时仍不得结算; 开关关的对照组必须结算, 证明取消的确实是这条路径。
    """
    vsm = _mk_vsm()                       # expected screw:2
    vsm._tracking_check_settlement = lambda *a, **kw: True
    vsm._update_tracking_stats([_det(1)], None)   # 只凑到 1/2
    assert vsm._tracking_cycle_active is True
    # 物品离开, 跑很多帧: 老路径判定说"该结了", 新语义下依然挂起
    for _ in range(10):
        vsm._update_tracking_stats([], None)
    assert vsm.settle_calls == [], "齐件即结算开启时原结算路径必须整段取消"
    assert vsm._tracking_cycle_active is True, "账本应挂起继续等, 不得收周期"

    # 对照组: 开关关, 同样桩 → 老路径照常出账 (零差异)
    ctrl = _mk_vsm(settle_on_complete=False)
    ctrl._tracking_check_settlement = lambda *a, **kw: True
    ctrl._update_tracking_stats([_det(1)], None)
    assert len(ctrl.settle_calls) == 1


def test_roi_timeout_forces_settle():
    """开关开 + 配了周期超时: 到点强制结算 (未凑齐判 NG), 防账本永久挂死。"""
    vsm = _mk_vsm()
    vsm.cycle_max_duration = 5
    vsm._update_tracking_stats([_det(1)], None)   # 1/2, 周期激活
    assert vsm.settle_calls == []
    # 把周期起点拨回 100 秒前 → 超时
    vsm.cycle_start_time = time.time() - 100
    vsm._update_tracking_stats([_det(1)], None)
    assert len(vsm.settle_calls) == 1, "周期超时应强制结算"


def test_roi_no_timeout_hangs_indefinitely():
    """开关开 + 周期超时 0 (默认): 永久挂起, 只能等新码/人工收账。"""
    vsm = _mk_vsm()
    vsm.cycle_max_duration = 0
    vsm._update_tracking_stats([_det(1)], None)
    vsm.cycle_start_time = time.time() - 3600     # 挂了一小时也不动
    vsm._update_tracking_stats([_det(1)], None)
    assert vsm.settle_calls == []
    assert vsm._tracking_cycle_active is True


def test_container_incomplete_gone_hangs_not_ng():
    """开关开: 未凑齐的箱子离开画面只挂账 (不 OK 不 NG), 账本保留。"""
    host = FakeBoxHost()
    _put_box(host)
    _put_item(host, 1, x=0.2)                     # 只装 1/2
    host._update_container_grouping(EXPECTED, 10.0, gone_confirm_frames=3)
    assert host.settle_calls == []
    # 箱子带着物品彻底离场, 跑超过 gone_confirm 的帧数
    host._tracking_objects.clear()
    for t in (11.0, 12.0, 13.0, 14.0, 15.0):
        host._update_container_grouping(EXPECTED, t, gone_confirm_frames=3)
    assert host.settle_calls == [], "未凑齐箱离开不得判 NG"
    assert '箱子1' in host._box_objects, "箱账应挂起保留, 等新码/超时/人工"


def test_container_switch_off_gone_still_settles():
    """对照组: 开关关时未凑齐箱离开照旧 gone-confirm 后结算 (现状零差异)。"""
    host = FakeBoxHost(pipeline_extra={'tracking_settle_on_complete': False})
    _put_box(host)
    _put_item(host, 1, x=0.2)
    host._update_container_grouping(EXPECTED, 10.0, gone_confirm_frames=3)
    host._tracking_objects.clear()
    for t in (11.0, 12.0, 13.0, 14.0):
        host._update_container_grouping(EXPECTED, t, gone_confirm_frames=3)
    assert host.settle_calls == ['箱子1'], "开关关时原离开结算路径必须保留"


def test_container_timeout_forces_settle():
    """开关开 + 配了周期超时: 箱龄 (first_seen 起算) 超时强制结算判 NG。"""
    host = FakeBoxHost()
    host.cycle_max_duration = 5
    _put_box(host)
    _put_item(host, 1, x=0.2)
    # 箱账 first_seen 继承跟踪对象的 1.0 → 4.0 时箱龄 3s < 5s, 不结
    host._update_container_grouping(EXPECTED, 4.0, gone_confirm_frames=3)
    assert host.settle_calls == []
    # 拨到 100.0 → 箱龄 99s > 5s
    host._update_container_grouping(EXPECTED, 100.0, gone_confirm_frames=3)
    assert host.settle_calls == ['箱子1'], "箱龄超时应强制结算"


# ---------- mes_hooks._settle_stale_on_new_scan (新码强制收旧账) ----------

def _mk_stale_env(monkeypatch, *, switch=True, strategy='roi_exit',
                  bind_timing='scan_gate'):
    """搭最小假环境: fake mgr + fake channel_manager, 返回 (fake_self, calls)。"""
    from backend.services.mes_hooks import MESHookManager
    calls = []

    def force_fn(min_items=1, reason=""):
        calls.append({'min_items': min_items, 'reason': reason})
        return 1

    mgr = types.SimpleNamespace(
        project_config={'pipeline_config': {
            'tracking_settle_on_complete': switch,
            'tracking_cycle_strategy': strategy,
        }},
        force_settle_pending_cycle=force_fn,
    )
    import backend.api.channel_manager as cm
    monkeypatch.setattr(cm, 'channel_manager',
                        types.SimpleNamespace(get=lambda ch: mgr))
    fake_self = types.SimpleNamespace(
        _get_bind_timing=lambda ch: bind_timing)
    return (lambda: MESHookManager._settle_stale_on_new_scan(fake_self, 0)), calls


def test_stale_settle_fires_when_switch_on(monkeypatch):
    run, calls = _mk_stale_env(monkeypatch)
    run()
    assert len(calls) == 1 and calls[0]['min_items'] == 1


def test_stale_settle_skips_when_switch_off(monkeypatch):
    run, calls = _mk_stale_env(monkeypatch, switch=False)
    run()
    assert calls == []


def test_stale_settle_skips_unsupported_strategy(monkeypatch):
    run, calls = _mk_stale_env(monkeypatch, strategy='all_gone')
    run()
    assert calls == []


def test_stale_settle_scan_pair_mutex(monkeypatch):
    run, calls = _mk_stale_env(monkeypatch, bind_timing='scan_pair')
    run()
    assert calls == []


# ==================== v3.50.1 扫码后才计数 (tracking_scan_gate) ====================

class _FakeHook:
    """假 MESHookManager: 只提供守门要用的两个查询。"""

    def __init__(self, required=True, in_flight=False):
        self.required = required
        self.in_flight = in_flight

    def is_scan_required(self, channel_id):
        return self.required

    def has_workpiece_in_flight(self, channel_id):
        return self.in_flight


def _mk_gated_vsm(*, gate=True, required=True, in_flight=False, expected=None):
    steps = [{'label': 'screw', 'enabled': True, 'count_mode': 'track'}]
    vsm = FakeVSM({
        'tracking_scan_gate': gate,
        'tracking_cycle_strategy': 'roi_exit',
        'counting_expected_items': expected or {'screw': 2},
    }, steps)
    vsm._mes_hook = _FakeHook(required=required, in_flight=in_flight)
    return vsm


def test_scan_gate_blocks_all_accounting_without_code():
    """码不在位: 整帧不入账 — 不计数 / 不开周期 / 不建跟踪对象。"""
    vsm = _mk_gated_vsm()
    for _ in range(5):
        vsm._update_tracking_stats([_det(1), _det(2, x=0.5)], None)
    assert vsm._tracking_class_counters == {}
    assert vsm._tracking_objects == {}
    assert vsm.start_cycle_calls == 0
    assert vsm.settle_calls == []


def test_scan_gate_resumes_and_counts_current_frame_on_scan():
    """扫到码后从当前画面重新开始看: 此刻在场的物品当帧重新入账, 不丢件。"""
    vsm = _mk_gated_vsm()
    vsm._update_tracking_stats([_det(1), _det(2, x=0.5)], None)
    assert vsm._tracking_class_counters == {}

    vsm._mes_hook.in_flight = True          # 扫码, 工件在位
    vsm._update_tracking_stats([_det(1), _det(2, x=0.5)], None)
    assert vsm._tracking_class_counters == {'screw': 2}
    assert vsm.start_cycle_calls == 1


def test_scan_gate_reengages_after_settlement():
    """结算后 (工件离位) 守门重新合上: 之后的检测又不算数。"""
    vsm = _mk_gated_vsm(in_flight=True)
    vsm.project_config['pipeline_config']['tracking_settle_on_complete'] = True
    vsm._update_tracking_stats([_det(1), _det(2, x=0.5)], None)
    assert len(vsm.settle_calls) == 1        # 凑齐当帧结算

    vsm._mes_hook.in_flight = False          # 结算后码离位
    for _ in range(3):
        vsm._update_tracking_stats([_det(7, y=0.6), _det(8, x=0.5, y=0.6)], None)
    assert vsm._tracking_class_counters == {}
    assert len(vsm.settle_calls) == 1
    assert vsm.start_cycle_calls == 1


def test_scan_gate_inert_without_scan_required():
    """该工位没开"先扫后检" → 守门无锚点, 不拦 (照常计数)。"""
    vsm = _mk_gated_vsm(required=False)
    vsm._update_tracking_stats([_det(1)], None)
    assert vsm._tracking_class_counters == {'screw': 1}


def test_scan_gate_off_default_unchanged():
    """开关关闭 (默认): 码不在位也照常入账 (现状行为)。"""
    vsm = _mk_gated_vsm(gate=False)
    vsm._update_tracking_stats([_det(1)], None)
    assert vsm._tracking_class_counters == {'screw': 1}


def test_scan_gate_container_keeps_box_tracking():
    """容器模式守门: 只滤物品, 箱子照常跟踪, 容器分组/D 模式跨线照常驱动。"""

    class FakeContainerHost(FakeVSM):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._container_mode = True
            self._container_label = 'box'
            self.container_calls = 0
            self.scan_d_calls = 0

        def _update_container_grouping(self, *a, **kw):
            self.container_calls += 1

        def _scan_d_update(self):
            self.scan_d_calls += 1

    steps = [{'label': 'screw', 'enabled': True, 'count_mode': 'track'}]
    vsm = FakeContainerHost({
        'tracking_scan_gate': True,
        'tracking_cycle_strategy': 'container',
        'counting_expected_items': {'screw': 2},
    }, steps)
    vsm._mes_hook = _FakeHook(required=True, in_flight=False)

    for _ in range(3):
        vsm._update_tracking_stats(
            [_det(1), _det(50, x=0.4, w=0.5, h=0.5, label='box')], None)

    # 物品被滤掉, 箱子照常跟踪
    assert vsm._tracking_class_counters.get('screw', 0) == 0
    box_labels = {o.get('class_name') for o in vsm._tracking_objects.values()}
    assert box_labels == {'box'}
    # 容器分组与 D 模式跨线更新照常每帧驱动
    assert vsm.container_calls == 3
    assert vsm.scan_d_calls == 3


def test_scan_gate_hook_error_fails_open():
    """mes_hook 查询抛异常 → 守门放行 (fail-open), 不影响检测主线。"""
    vsm = _mk_gated_vsm()

    def _boom(_ch):
        raise RuntimeError("scanner service down")

    vsm._mes_hook.is_scan_required = _boom
    vsm._update_tracking_stats([_det(1)], None)
    assert vsm._tracking_class_counters == {'screw': 1}
