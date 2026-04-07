#!/usr/bin/env python3
"""Simulate the event counting FSM to verify correctness.

Mimics the core logic from _update_tracking_stats for event-mode labels.
"""
import sys

PASS = 0
FAIL = 0

def check(desc, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {desc}")
    else:
        FAIL += 1
        print(f"  ✗ {desc}")


class EventFSM:
    """Standalone replica of the event counting state machine."""
    def __init__(self):
        self.counters = {}
        self.state = {}
        self.visible_frames = {}
        self.gone_frames_count = {}

    def reset(self):
        self.counters.clear()
        self.state.clear()
        self.visible_frames.clear()
        self.gone_frames_count.clear()

    def process_frame(self, event_steps, labels_seen):
        for label, cfg in event_steps.items():
            if label not in self.state:
                self.state[label] = 'idle'
                self.counters[label] = 0
                self.visible_frames[label] = 0
                self.gone_frames_count[label] = 0

            state = self.state[label]
            is_visible = label in labels_seen

            if state == 'idle':
                if is_visible:
                    self.state[label] = 'visible'
                    self.visible_frames[label] = 1
            elif state == 'visible':
                if is_visible:
                    self.visible_frames[label] += 1
                else:
                    if self.visible_frames[label] >= cfg['min_visible_frames']:
                        self.state[label] = 'gone'
                        self.gone_frames_count[label] = 1
                    else:
                        self.state[label] = 'idle'
                        self.visible_frames[label] = 0
            elif state == 'gone':
                if is_visible:
                    self.state[label] = 'visible'
                    self.visible_frames[label] += 1
                    self.gone_frames_count[label] = 0
                else:
                    self.gone_frames_count[label] += 1
                    if self.gone_frames_count[label] >= cfg['gone_frames']:
                        self.counters[label] = self.counters.get(label, 0) + 1
                        self.state[label] = 'idle'
                        self.visible_frames[label] = 0
                        self.gone_frames_count[label] = 0


# ===== Test Scenarios =====
cfg = {'螺丝包': {'required_count': 5, 'min_visible_frames': 3, 'gone_frames': 8}}

print("=" * 60)
print("Test 1: Simple event — appear 5 frames, disappear 10 frames")
print("=" * 60)
fsm = EventFSM()
for _ in range(5):
    fsm.process_frame(cfg, {'螺丝包'})
for _ in range(10):
    fsm.process_frame(cfg, set())
check("Count is 1 after one full cycle", fsm.counters['螺丝包'] == 1)
check("State returns to idle", fsm.state['螺丝包'] == 'idle')


print("\n" + "=" * 60)
print("Test 2: Too brief appearance — 2 frames (below min_visible=3)")
print("=" * 60)
fsm.reset()
for _ in range(2):
    fsm.process_frame(cfg, {'螺丝包'})
for _ in range(10):
    fsm.process_frame(cfg, set())
check("Count is 0 (too brief)", fsm.counters['螺丝包'] == 0)
check("State is idle", fsm.state['螺丝包'] == 'idle')


print("\n" + "=" * 60)
print("Test 3: Flicker during gone phase — disappear 3, reappear 1, disappear 10")
print("=" * 60)
fsm.reset()
for _ in range(5):
    fsm.process_frame(cfg, {'螺丝包'})
for _ in range(3):
    fsm.process_frame(cfg, set())
fsm.process_frame(cfg, {'螺丝包'})
for _ in range(10):
    fsm.process_frame(cfg, set())
check("Count is 1 (flicker reset doesn't double-count)", fsm.counters['螺丝包'] == 1)


print("\n" + "=" * 60)
print("Test 4: Five complete events (screw bag scenario)")
print("=" * 60)
fsm.reset()
for event_num in range(5):
    for _ in range(4):
        fsm.process_frame(cfg, {'螺丝包'})
    for _ in range(10):
        fsm.process_frame(cfg, set())
check("Count is 5 after 5 events", fsm.counters['螺丝包'] == 5)


print("\n" + "=" * 60)
print("Test 5: Rapid placement — appear 3, disappear 8 (minimum)")
print("=" * 60)
fsm.reset()
for event_num in range(5):
    for _ in range(3):
        fsm.process_frame(cfg, {'螺丝包'})
    for _ in range(8):
        fsm.process_frame(cfg, set())
check("Count is 5 with minimum timings", fsm.counters['螺丝包'] == 5)


print("\n" + "=" * 60)
print("Test 6: Incomplete gone — disappear only 5 frames (< 8), then reappear")
print("=" * 60)
fsm.reset()
for _ in range(5):
    fsm.process_frame(cfg, {'螺丝包'})
for _ in range(5):
    fsm.process_frame(cfg, set())
for _ in range(5):
    fsm.process_frame(cfg, {'螺丝包'})
check("Count is still 0 (gone not confirmed)", fsm.counters['螺丝包'] == 0)
check("State is visible (reappeared)", fsm.state['螺丝包'] == 'visible')
for _ in range(10):
    fsm.process_frame(cfg, set())
check("Count is 1 after eventual gone confirmation", fsm.counters['螺丝包'] == 1)


print("\n" + "=" * 60)
print("Test 7: Multiple labels — track-mode label ignored, event-mode counted")
print("=" * 60)
multi_cfg = {
    '放入动作': {'required_count': 3, 'min_visible_frames': 2, 'gone_frames': 5},
}
fsm.reset()
for _ in range(3):
    for _ in range(3):
        fsm.process_frame(multi_cfg, {'放入动作'})
    for _ in range(6):
        fsm.process_frame(multi_cfg, set())
check("放入动作 count is 3", fsm.counters['放入动作'] == 3)
check("No other labels in counters", len(fsm.counters) == 1)


print("\n" + "=" * 60)
print("Test 8: No detection at all — counter stays 0")
print("=" * 60)
fsm.reset()
for _ in range(20):
    fsm.process_frame(cfg, set())
check("Count is 0 with no detections", fsm.counters['螺丝包'] == 0)
check("State is idle", fsm.state['螺丝包'] == 'idle')


# ===== Summary =====
print("\n" + "=" * 60)
total = PASS + FAIL
print(f"Results: {PASS}/{total} passed, {FAIL} failed")
if FAIL > 0:
    print("SOME TESTS FAILED!")
    sys.exit(1)
else:
    print("ALL TESTS PASSED!")
    sys.exit(0)
