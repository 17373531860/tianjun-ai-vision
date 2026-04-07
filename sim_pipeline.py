"""
Full-pipeline simulation of source.py detection logic against the complete video.

Accurately models:
- Per-step confidence thresholds
- min_frames consecutive counting with frame-by-frame reset
- disappear_delay (delays step_last_seen deletion)
- max_interval dedup (is_new_appearance logic)
- accept_once (prevents re-adding to cycle_steps)
- Sequential last_step settlement (_check_sequential_mode split logic)
"""

import time
import cv2
import numpy as np
from collections import Counter
from ultralytics import YOLO

MODEL_PATH = '/home/qianqian/1.py/output/oppo/model/oppo1.pt'
VIDEO_PATH = '/home/qianqian/1.py/video/oppo/Video_20260320203958253.avi'

EXPECTED_LABELS = ['撕膜', '顶卡拖', '点亮屏幕']
LAST_STEP = '点亮屏幕'
SETTLEMENT_MODE = 'last_step'

STEP_CONFIG = {
    '撕膜':   {'confidence': 0.50, 'min_frames': 2, 'disappear_delay': 3.0, 'max_interval': 5.0, 'accept_once': False, 'strict_order': False},
    '顶卡拖': {'confidence': 0.40, 'min_frames': 1, 'disappear_delay': 3.0, 'max_interval': 5.0, 'accept_once': False, 'strict_order': False},
    '点亮屏幕': {'confidence': 0.65, 'min_frames': 3, 'disappear_delay': 0.5, 'max_interval': 1.5, 'accept_once': False, 'strict_order': False},
}


class PipelineSimulator:
    def __init__(self):
        self.step_last_seen = {}
        self.step_start_time = {}
        self.step_consecutive_frames = {}
        self.step_frame_confirmed = {}
        self._step_raw_start = {}

        self.current_cycle_steps = []
        self.last_added_step = None
        self.cycle_start_time = None
        self._last_step_added_time = None
        self.last_step_completed_time = None

        self.cycle_number = 0
        self.events = []
        self.step_counts = {}
        self._just_settled = False

    def process_frame(self, frame_labels_with_conf: list, current_time: float):
        detected_labels = set()
        frame_detected_labels = set()

        for label, conf in frame_labels_with_conf:
            cfg = STEP_CONFIG.get(label)
            if not cfg:
                continue
            if conf < cfg['confidence']:
                continue
            frame_detected_labels.add(label)

        for label in frame_detected_labels:
            prev_count = self.step_consecutive_frames.get(label, 0)
            if prev_count == 0:
                self._step_raw_start[label] = current_time
            self.step_consecutive_frames[label] = prev_count + 1

            min_frames = STEP_CONFIG[label]['min_frames']
            if self.step_consecutive_frames[label] >= min_frames:
                detected_labels.add(label)
                if not self.step_frame_confirmed.get(label):
                    self.step_frame_confirmed[label] = True

        all_configured = set(STEP_CONFIG.keys())
        for label in all_configured:
            if label not in frame_detected_labels:
                self.step_consecutive_frames[label] = 0
                self.step_frame_confirmed[label] = False

        for label in detected_labels:
            self._check_events(label, current_time)

        pending_event_checks = []
        for label, last_time in list(self.step_last_seen.items()):
            if label not in detected_labels:
                cfg = STEP_CONFIG.get(label, {})
                disappear_delay = cfg.get('disappear_delay', 0)

                if current_time - last_time > disappear_delay:
                    start_time = self.step_start_time.get(label, last_time)
                    duration = last_time - start_time

                    del self.step_last_seen[label]
                    if label in self.step_start_time:
                        del self.step_start_time[label]

                    if label not in self.step_counts:
                        self.step_counts[label] = 0
                    self.step_counts[label] += 1
                    self.last_step_completed_time = last_time

                    pending_event_checks.append(label)

        for completed_label in pending_event_checks:
            self._on_step_disappeared(completed_label, current_time)

    def _check_events(self, label: str, current_time: float):
        old_last_seen = self.step_last_seen.get(label)

        cfg = STEP_CONFIG.get(label, {})
        if cfg.get('accept_once') and label in self.current_cycle_steps:
            self.step_last_seen[label] = current_time
            if label not in self.step_start_time:
                self.step_start_time[label] = self._step_raw_start.get(label, current_time)
            return

        if cfg.get('strict_order'):
            idx = EXPECTED_LABELS.index(label) if label in EXPECTED_LABELS else -1
            if idx > 0:
                predecessors = EXPECTED_LABELS[:idx]
                cycle_set = set(self.current_cycle_steps)
                if not all(p in cycle_set for p in predecessors):
                    return

        self.step_last_seen[label] = current_time

        max_interval = cfg.get('max_interval', 1.0)

        if old_last_seen is not None:
            time_since_last = current_time - old_last_seen
            is_new_appearance = time_since_last > max_interval
        else:
            is_new_appearance = True

        if is_new_appearance:
            if len(self.current_cycle_steps) == 0:
                if self._just_settled:
                    first_step = EXPECTED_LABELS[0]
                    if label != first_step:
                        self.step_last_seen.pop(label, None)
                        self.step_frame_confirmed[label] = False
                        self.step_consecutive_frames[label] = 0
                        return
                    self._just_settled = False

            raw_start = self._step_raw_start.get(label, current_time)
            self.step_start_time[label] = raw_start

            if len(self.current_cycle_steps) == 0:
                self.cycle_start_time = current_time
                self.cycle_number += 1

        if is_new_appearance and label in set(EXPECTED_LABELS):
            if label not in self.current_cycle_steps:
                self.current_cycle_steps.append(label)
                self.last_added_step = label
                self._last_step_added_time = current_time

    def _on_step_disappeared(self, completed_step: str, current_time: float):
        if completed_step == LAST_STEP:
            if completed_step in self.current_cycle_steps:
                self._check_sequential_mode()
            else:
                pass  # step not in cycle, skip

    def _check_sequential_mode(self):
        self._just_settled = True

        last_step_label = EXPECTED_LABELS[-1]

        if last_step_label in self.current_cycle_steps:
            split_idx = self.current_cycle_steps.index(last_step_label)
            this_cycle = self.current_cycle_steps[:split_idx + 1]
            next_carry = self.current_cycle_steps[split_idx + 1:]
        else:
            this_cycle = list(self.current_cycle_steps)
            next_carry = []

        if not this_cycle:
            self.current_cycle_steps = []
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None
            return

        step_counter = Counter(this_cycle)
        unexpected = [s for s in this_cycle if s not in set(EXPECTED_LABELS)]
        duplicated = [s for s, cnt in step_counter.items() if cnt > 1]

        if len(this_cycle) > len(EXPECTED_LABELS):
            reason = f'重复步骤: {duplicated}' if duplicated else f'多余步骤: {unexpected}'
            event_type = 'NG'
        elif unexpected or duplicated:
            reasons = []
            if unexpected:
                reasons.append(f'多余步骤: {list(dict.fromkeys(unexpected))}')
            if duplicated:
                reasons.append(f'重复步骤: {duplicated}')
            reason = ', '.join(reasons)
            event_type = 'NG'
        elif not all(lbl in this_cycle for lbl in EXPECTED_LABELS):
            missing = [l for l in EXPECTED_LABELS if l not in this_cycle]
            reason = f'缺少: {missing}'
            event_type = 'NG'
        else:
            cycle_order_correct = True
            last_pos = -1
            for lbl in EXPECTED_LABELS:
                pos = this_cycle.index(lbl)
                if pos < last_pos:
                    cycle_order_correct = False
                    break
                last_pos = pos
            if cycle_order_correct:
                reason = '顺序正确完成'
                event_type = 'OK'
            else:
                reason = '顺序错误'
                event_type = 'NG'

        cycle_time = 0
        if self.cycle_start_time is not None:
            cycle_time = time.time() - self.cycle_start_time

        self.events.append({
            'cycle': self.cycle_number,
            'type': event_type,
            'reason': reason,
            'steps': list(this_cycle),
        })

        print(f"  周期 #{self.cycle_number}: {event_type} | 步骤={this_cycle} | {reason}")

        self.cycle_start_time = None

        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()

        if next_carry:
            self.current_cycle_steps = next_carry
            self.last_added_step = next_carry[-1]
            self._last_step_added_time = time.time()
        else:
            self.current_cycle_steps = []
            self.last_added_step = None
            self._last_step_added_time = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None


def main():
    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"FPS: {fps:.2f}, 总帧: {total}, 时长: {total/fps:.1f}s")
    print(f"预期步骤序列: {EXPECTED_LABELS}")
    print(f"结算模式: {SETTLEMENT_MODE} (最后一步={LAST_STEP})")
    print(f"\n步骤配置:")
    for label, cfg in STEP_CONFIG.items():
        print(f"  {label}: conf={cfg['confidence']}, min_frames={cfg['min_frames']}, "
              f"disappear_delay={cfg['disappear_delay']}s, max_interval={cfg['max_interval']}s, "
              f"accept_once={cfg.get('accept_once', False)}")
    print()

    sim = PipelineSimulator()
    t0 = time.time()
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        current_time = frame_idx / fps

        results = model.predict(frame, conf=0.50, verbose=False)
        frame_detections = []
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                label = model.names[cls_id]
                frame_detections.append((label, conf))

        sim.process_frame(frame_detections, current_time)

        if frame_idx % 2000 == 0:
            elapsed = time.time() - t0
            print(f"  [{frame_idx}/{total}] {elapsed:.0f}s | 已结算周期: {len(sim.events)}, "
                  f"当前步骤: {sim.current_cycle_steps}")

    cap.release()
    elapsed = time.time() - t0

    if sim.current_cycle_steps:
        print(f"\n  [残留] 未结算周期: 步骤={sim.current_cycle_steps}")

    print(f"\n{'='*70}")
    print(f"                    模拟结果")
    print(f"{'='*70}")
    print(f"处理: {frame_idx} 帧, 耗时 {elapsed:.0f}s")
    print(f"总周期数: {len(sim.events)}")

    ok_count = sum(1 for e in sim.events if e['type'] == 'OK')
    ng_count = sum(1 for e in sim.events if e['type'] == 'NG')
    print(f"OK: {ok_count}, NG: {ng_count}")

    dup_ng = sum(1 for e in sim.events if '重复' in e['reason'])
    missing_ng = sum(1 for e in sim.events if '缺少' in e['reason'])
    order_ng = sum(1 for e in sim.events if '顺序错误' in e['reason'])
    print(f"\nNG 分类:")
    print(f"  重复步骤: {dup_ng}")
    print(f"  缺少步骤: {missing_ng}")
    print(f"  顺序错误: {order_ng}")

    print(f"\n逐周期详情:")
    for e in sim.events:
        print(f"  #{e['cycle']}: {e['type']} | {e['steps']} | {e['reason']}")


if __name__ == '__main__':
    main()
