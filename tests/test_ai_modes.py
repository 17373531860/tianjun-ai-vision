# -*- coding: utf-8 -*-
"""AI 采样模式 (logic_mode='ocr'|'anomaly') + facing_dwell 朝向驻留 单元测试 (2026-09)。

覆盖:
  - parse_ocr_config / parse_anomaly_config 解析与钳位
  - OCR 判定状态机 (稳定次数 / pattern 匹配 / on_change_only / 事件触发)
  - 异常检测判定状态机 (连续超阈值 / 冷却 / 恢复)
  - _ai_mode_active 守门判据
  - facing_dwell 引擎规则 (解析 / 朝向夹角判定 / alert_on_absent 取反 / episode 推进)
  - person_orientation 几何工具 (angle_diff / facing_alignment / YawSmoother)
"""
import os
import sys
import time
import types

os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'threads;1')

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.api.source_ai_modes_mixin import (   # noqa: E402
    AiModesMixin, parse_ocr_config, parse_anomaly_config, _norm_rect,
)
from backend.api.source_region_events import (    # noqa: E402
    parse_region_events, RegionEventEngine,
)
from backend.services.person_orientation import (  # noqa: E402
    angle_diff_deg, facing_alignment_deg, YawSmoother,
)


# ==================== 配置解析 ====================

class TestParseConfigs:
    def test_ocr_config_defaults(self):
        cfg = parse_ocr_config({})
        assert cfg['interval_s'] == 2.0
        assert cfg['min_score'] == 0.5
        assert cfg['stable_reads'] == 2
        assert cfg['rules'] == []

    def test_ocr_rule_parse(self):
        cfg = parse_ocr_config({'rules': [
            {'name': '序列号', 'pattern': r'^SN\d{6}$', 'roi': [0.1, 0.2, 0.5, 0.3]},
        ]})
        r = cfg['rules'][0]
        assert r['name'] == '序列号'
        assert r['_pattern'] is not None
        assert r['_pattern'].search('SN123456')
        assert not r['_pattern'].search('XX123')
        assert r['roi'] == [0.1, 0.2, 0.5, 0.3]
        assert r['ok_event_id'] == 1 and r['ng_event_id'] == 2
        assert r['on_change_only'] is True and r['settle'] is True

    def test_ocr_bad_pattern_degrades(self):
        cfg = parse_ocr_config({'rules': [{'name': 'x', 'pattern': '[unclosed'}]})
        assert cfg['rules'][0]['_pattern'] is None
        assert cfg['rules'][0]['pattern'] == ''

    def test_anomaly_config(self):
        cfg = parse_anomaly_config({'bank_id': 'b1', 'threshold': '',
                                    'consecutive': 0, 'ng_cooldown_s': -5})
        assert cfg['bank_id'] == 'b1'
        assert cfg['threshold'] is None      # 空串 = 用库值
        assert cfg['consecutive'] == 3       # 0 = 未配置, 回默认 3
        assert cfg['ng_cooldown_s'] == 0.0

    def test_norm_rect(self):
        assert _norm_rect([0.1, 0.1, 0.5, 0.5]) == [0.1, 0.1, 0.5, 0.5]
        assert _norm_rect(None) is None
        assert _norm_rect([0.1, 0.1]) is None
        assert _norm_rect([0.1, 0.1, 0, 0.5]) is None   # w<=0


# ==================== VSM 桩 ====================

class _StubVSM(AiModesMixin):
    """最小宿主桩: 只带 AiModesMixin 依赖的字段/方法。"""

    def __init__(self, logic_mode='ocr'):
        self.project_config = {'logic_mode': logic_mode}
        self.channel_id = 0
        self.is_detecting = True
        self.current_cycle_uuid = None
        self.current_cycle_steps = []
        self.cycle_start_time = None
        self.cycle_start_frame_pos = 0
        self._ai_rule_states = {}
        self._ai_mode_snapshot = None
        self.events = []          # (event_id, reason) 结算事件
        self.fired = []           # (event_id, reason) 非结算事件
        self.cycles_started = 0

    def _video_frame_pos(self):
        return 0

    def start_cycle(self):
        self.cycles_started += 1
        self.current_cycle_uuid = f'c{self.cycles_started}'

    def _trigger_event(self, event_id, reason):
        self.events.append((event_id, reason))
        self.current_cycle_uuid = None  # end_cycle 语义
        return True

    def fire_external_event_response(self, event_id, reason, source=None):
        self.fired.append((event_id, reason))


def _frame():
    return np.zeros((120, 160, 3), dtype=np.uint8)


# ==================== OCR 判定状态机 ====================

class TestOcrMode:
    def _run(self, vsm, cfg, texts):
        """按次序把 OCR 引擎读到的文本喂给状态机。"""
        from backend.services import ocr_engine
        for t in texts:
            items = [{'text': t, 'score': 0.9, 'box': []}] if t else []
            orig_read, orig_avail = ocr_engine.read_text, ocr_engine.is_available
            ocr_engine.read_text = lambda *a, **k: items
            ocr_engine.is_available = lambda: True
            try:
                vsm._ai_process_ocr(_frame(), cfg)
            finally:
                ocr_engine.read_text, ocr_engine.is_available = orig_read, orig_avail

    def test_stable_then_match_triggers_ok(self):
        vsm = _StubVSM('ocr')
        cfg = parse_ocr_config({'stable_reads': 2, 'rules': [
            {'name': 'sn', 'pattern': r'^SN\d+$'}]})
        self._run(vsm, cfg, ['SN100'])
        assert vsm.events == []          # 稳定 1 次不触发
        self._run(vsm, cfg, ['SN100'])
        assert vsm.events == [(1, 'OCR[sn] 读到 "SN100" 匹配')]
        assert vsm.cycles_started == 1   # 开周期 → 结算

    def test_mismatch_triggers_ng(self):
        vsm = _StubVSM('ocr')
        cfg = parse_ocr_config({'stable_reads': 1, 'rules': [
            {'name': 'sn', 'pattern': r'^SN\d+$'}]})
        self._run(vsm, cfg, ['BAD-1'])
        assert vsm.events and vsm.events[0][0] == 2

    def test_on_change_only_dedups(self):
        vsm = _StubVSM('ocr')
        cfg = parse_ocr_config({'stable_reads': 1, 'rules': [{'name': 'sn'}]})
        self._run(vsm, cfg, ['A', 'A', 'A'])
        assert len(vsm.events) == 1      # 同文本只触发一次
        self._run(vsm, cfg, ['B'])
        assert len(vsm.events) == 2      # 换文本再触发

    def test_no_pattern_any_text_ok(self):
        vsm = _StubVSM('ocr')
        cfg = parse_ocr_config({'stable_reads': 1, 'rules': [{'name': 'any'}]})
        self._run(vsm, cfg, ['hello'])
        assert vsm.events == [(1, 'OCR[any] 读到 "hello"')]

    def test_non_settle_fires_response_only(self):
        vsm = _StubVSM('ocr')
        cfg = parse_ocr_config({'stable_reads': 1, 'rules': [
            {'name': 'sn', 'settle': False}]})
        self._run(vsm, cfg, ['X1'])
        assert vsm.events == [] and len(vsm.fired) == 1

    def test_snapshot_shape(self):
        vsm = _StubVSM('ocr')
        cfg = parse_ocr_config({'stable_reads': 2, 'rules': [{'name': 'sn'}]})
        self._run(vsm, cfg, ['T'])
        snap = vsm._ai_mode_snapshot
        assert snap['mode'] == 'ocr' and snap['available'] is True
        assert snap['rules'][0]['last_text'] == 'T'
        assert snap['rules'][0]['stable'] == 1


# ==================== 异常检测判定状态机 ====================

class TestAnomalyMode:
    def _run(self, vsm, cfg, scores, threshold=1.0):
        from backend.services import anomaly_engine
        for s in scores:
            orig = anomaly_engine.score_image
            anomaly_engine.score_image = lambda *a, **k: {
                'bank_id': cfg['bank_id'], 'score': s, 'threshold': threshold,
                'is_anomaly': s > threshold, 'mean_patch_score': s,
                'backbone': 'resnet18'}
            try:
                vsm._ai_process_anomaly(_frame(), cfg)
            finally:
                anomaly_engine.score_image = orig

    def test_consecutive_gate(self):
        vsm = _StubVSM('anomaly')
        cfg = parse_anomaly_config({'bank_id': 'b', 'consecutive': 3})
        self._run(vsm, cfg, [2.0, 2.0])
        assert vsm.events == []                       # 2 次不够
        self._run(vsm, cfg, [2.0])
        assert len(vsm.events) == 1 and vsm.events[0][0] == 2

    def test_normal_resets_counter(self):
        vsm = _StubVSM('anomaly')
        cfg = parse_anomaly_config({'bank_id': 'b', 'consecutive': 2})
        self._run(vsm, cfg, [2.0, 0.5, 2.0])          # 中间恢复正常清零
        assert vsm.events == []

    def test_cooldown_suppresses(self):
        vsm = _StubVSM('anomaly')
        cfg = parse_anomaly_config({'bank_id': 'b', 'consecutive': 1,
                                    'ng_cooldown_s': 999})
        self._run(vsm, cfg, [2.0, 2.0, 2.0])
        assert len(vsm.events) == 1                   # 冷却窗内只报一次

    def test_recover_ok_event(self):
        vsm = _StubVSM('anomaly')
        cfg = parse_anomaly_config({'bank_id': 'b', 'consecutive': 2,
                                    'ng_cooldown_s': 0, 'recover_ok_event': True})
        self._run(vsm, cfg, [2.0, 2.0])               # 报警
        assert vsm.events[-1][0] == 2
        self._run(vsm, cfg, [0.1, 0.1])               # 恢复 → OK 收口
        assert vsm.events[-1][0] == 1

    def test_missing_bank_reports_error(self):
        vsm = _StubVSM('anomaly')
        cfg = parse_anomaly_config({})
        vsm._ai_process_anomaly(_frame(), cfg)
        assert vsm._ai_mode_snapshot['available'] is False


# ==================== 守门判据 ====================

class TestModeGate:
    def test_active_for_ai_modes(self):
        assert _StubVSM('ocr')._ai_mode_active() is True
        assert _StubVSM('anomaly')._ai_mode_active() is True

    def test_inactive_for_others(self):
        for m in ('sequential', 'detection', 'tracking', 'weighing',
                  'per_item', 'region_events', None):
            assert _StubVSM(m)._ai_mode_active() is False

    def test_apply_config_wiring(self):
        from backend.api.source_ai_modes_mixin import apply_ai_modes_config
        h = _StubVSM('ocr')
        apply_ai_modes_config(h, {'logic_mode': 'ocr'},
                              {'ocr': {'rules': [{'name': 'x'}]}})
        assert h._ai_ocr_cfg is not None and h._ai_anomaly_cfg is None
        apply_ai_modes_config(h, {'logic_mode': 'sequential'}, {})
        assert h._ai_ocr_cfg is None and h._ai_anomaly_cfg is None


# ==================== facing_dwell 引擎规则 ====================

def _facing_cfg(extra=None, **kw):
    rule = {'type': 'facing_dwell', 'name': '面向仪表', 'subject_label': '人',
            'target_point': [0.8, 0.3], 'tolerance_deg': 40,
            'min_frames': 3, **kw}
    rules = [rule] + (extra or [])
    return parse_region_events({'region_events': {'enabled': True, 'rules': rules}})


class TestFacingDwell:
    def test_parse_requires_target_point(self):
        with pytest.raises(ValueError, match='仪表点'):
            parse_region_events({'region_events': {'enabled': True, 'rules': [
                {'type': 'facing_dwell', 'name': 'x', 'subject_label': '人'}]}})

    def test_parse_target_region_centroid_fallback(self):
        cfg = parse_region_events({'region_events': {'enabled': True, 'rules': [
            {'type': 'facing_dwell', 'name': 'x', 'subject_label': '人',
             'target_region': [[0.7, 0.2], [0.9, 0.2], [0.9, 0.4], [0.7, 0.4]]}]}})
        tp = cfg.rules[0].target_point
        assert abs(tp[0] - 0.8) < 1e-6 and abs(tp[1] - 0.3) < 1e-6

    def test_condition_facing_hits(self):
        cfg = _facing_cfg()
        eng = RegionEventEngine(cfg)
        eng.frame_aspect = 0.5625
        # 人中心 (0.5, 0.5) → 仪表 (0.8, 0.3): bearing ≈ atan2(-0.2*0.5625, 0.3) ≈ -20.6°
        det = {'label': '人', 'x': 0.45, 'y': 0.4, 'w': 0.1, 'h': 0.2,
               'confidence': 0.9, 'facing': -25.0}
        ok, subj = eng._facing_condition(cfg.rules[0], {'人': [det]}, time.time())
        assert ok and subj is det

    def test_condition_facing_away_misses(self):
        cfg = _facing_cfg()
        eng = RegionEventEngine(cfg)
        det = {'label': '人', 'x': 0.45, 'y': 0.4, 'w': 0.1, 'h': 0.2,
               'confidence': 0.9, 'facing': 150.0}
        ok, _ = eng._facing_condition(cfg.rules[0], {'人': [det]}, time.time())
        assert not ok

    def test_condition_no_yaw_skipped(self):
        cfg = _facing_cfg()
        eng = RegionEventEngine(cfg)
        det = {'label': '人', 'x': 0.45, 'y': 0.4, 'w': 0.1, 'h': 0.2,
               'confidence': 0.9}   # 无 facing 字段
        ok, _ = eng._facing_condition(cfg.rules[0], {'人': [det]}, time.time())
        assert not ok

    def test_alert_on_absent_inverts(self):
        cfg = _facing_cfg(alert_on_absent=True)
        eng = RegionEventEngine(cfg)
        # 无人 → 条件成立 (哨兵告警路径)
        ok, subj = eng._facing_condition(cfg.rules[0], {'人': []}, time.time())
        assert ok and subj is None
        # 有人面向 → 条件不成立 (计时被重置)
        det = {'label': '人', 'x': 0.45, 'y': 0.4, 'w': 0.1, 'h': 0.2,
               'confidence': 0.9, 'facing': -20.0}
        eng.frame_aspect = 0.5625
        ok, _ = eng._facing_condition(cfg.rules[0], {'人': [det]}, time.time())
        assert not ok

    def test_episode_confirm_via_process_frame(self):
        """facing 连续满足 min_frames 帧 → confirmed 事件产出。"""
        cfg = _facing_cfg(min_frames=3, event_id=5)
        eng = RegionEventEngine(cfg)
        eng.frame_aspect = 0.5625
        det = {'label': '人', 'x': 0.45, 'y': 0.4, 'w': 0.1, 'h': 0.2,
               'confidence': 0.9, 'facing': -20.0}
        t = time.time()
        confirmed = []
        for i in range(5):
            for ev in eng.process_frame([det], t + i * 0.1):
                if ev.get('action') == 'confirmed':
                    confirmed.append(ev)
        assert len(confirmed) == 1
        assert confirmed[0]['rule_name'] == '面向仪表'
        assert confirmed[0]['event_id'] == 5

    def test_region_gate(self):
        """配了站位区: 区外的人不参与判定。"""
        cfg = _facing_cfg(region=[[0.0, 0.0], [0.3, 0.0], [0.3, 0.3], [0.0, 0.3]])
        eng = RegionEventEngine(cfg)
        eng.frame_aspect = 0.5625
        det = {'label': '人', 'x': 0.45, 'y': 0.4, 'w': 0.1, 'h': 0.2,
               'confidence': 0.9, 'facing': -20.0}   # 中心 (0.5,0.5) 在区外
        ok, _ = eng._facing_condition(cfg.rules[0], {'人': [det]}, time.time())
        assert not ok

    def test_min_move_forced_zero(self):
        cfg = _facing_cfg(min_move=0.5)
        assert cfg.rules[0].min_move == 0.0   # 驻留场景位移门槛强制忽略

    def test_snapshot_contains_rule(self):
        cfg = _facing_cfg()
        eng = RegionEventEngine(cfg)
        snap = eng.snapshot()
        assert snap['rules'][0]['name'] == '面向仪表'


# ==================== person_orientation 几何工具 ====================

class TestOrientationGeometry:
    def test_angle_diff(self):
        assert angle_diff_deg(10, 350) == 20
        assert angle_diff_deg(-170, 170) == 20
        assert angle_diff_deg(0, 180) == 180

    def test_facing_alignment(self):
        # 人在 (100,100), 仪表在 (200,100): 连线朝向 0°
        assert facing_alignment_deg((100, 100), 0.0, (200, 100)) == 0
        assert facing_alignment_deg((100, 100), 90.0, (200, 100)) == 90

    def test_yaw_smoother_converges(self):
        sm = YawSmoother(alpha=0.5)
        for i in range(10):
            sm.update(i * 0.1, (100, 100), obs_yaw_deg=45.0, obs_conf=1.0)
        assert abs(sm.yaw_deg - 45.0) < 1.0

    def test_yaw_smoother_motion_prior(self):
        sm = YawSmoother()
        # 无关键点观测, 仅靠行进方向: 向右移动 → yaw ≈ 0°
        for i in range(10):
            sm.update(i * 0.1, (100 + i * 20, 100))
        assert sm.yaw_deg is not None and abs(sm.yaw_deg) < 10


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
