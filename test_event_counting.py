#!/usr/bin/env python3
"""Test script for tracking mode event counting (动作计数) feature.

Validates:
1. Backend code structure: state variables, FSM logic, checklist, settle
2. Frontend code structure: step config UI, defaults, migration
3. Frontend-backend field consistency
"""
import sys
import os
import json

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


def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


# ===== Backend Tests =====
print("=" * 60)
print("Backend: backend/api/source.py")
print("=" * 60)

src = read_file('backend/api/source.py')

print("\n--- 1. Event counting state variables ---")
check("_event_counters initialized", "self._event_counters = {}" in src)
check("_event_state initialized", "self._event_state = {}" in src)
check("_event_visible_frames initialized", "self._event_visible_frames = {}" in src)
check("_event_gone_frames_count initialized", "self._event_gone_frames_count = {}" in src)
check("_event_first_seen initialized", "self._event_first_seen = {}" in src)
check("_event_last_seen initialized", "self._event_last_seen = {}" in src)

print("\n--- 2. _reset_counting_cycle clears event state ---")
check("_event_counters.clear()", "_event_counters.clear()" in src)
check("_event_state.clear()", "_event_state.clear()" in src)
check("_event_visible_frames.clear()", "_event_visible_frames.clear()" in src)
check("_event_gone_frames_count.clear()", "_event_gone_frames_count.clear()" in src)
check("_event_first_seen.clear()", "_event_first_seen.clear()" in src)
check("_event_last_seen.clear()", "_event_last_seen.clear()" in src)

print("\n--- 3. Step config parsing: count_mode / event ---")
check("count_mode == 'event' check", "step.get('count_mode') == 'event'" in src)
check("event_required_count read", "event_required_count" in src)
check("event_min_visible_frames read", "event_min_visible_frames" in src)
check("event_gone_frames read from config", "event_gone_frames" in src)
check("event_steps dict built", "event_steps[lbl]" in src or "event_steps = {}" in src)

print("\n--- 4. Event labels filtered from normal tracking ---")
check("event labels skip track_id flow", "if label in event_steps:" in src)
check("event_labels_seen set used", "event_labels_seen.add(label)" in src)
check("ROI check for event labels", "self._is_in_roi(det)" in src and "event_labels_seen" in src)

print("\n--- 5. Event FSM states ---")
check("FSM idle -> visible transition", "self._event_state[label] = 'visible'" in src)
check("FSM visible -> gone transition", "self._event_state[label] = 'gone'" in src)
check("FSM gone -> idle (event confirmed)", "self._event_counters[label] = self._event_counters.get(label, 0) + 1" in src)
check("FSM gone -> visible (reappeared)", "'visible'" in src and "'gone'" in src)
check("min_visible_frames threshold", "cfg['min_visible_frames']" in src)
check("gone_frames threshold", "cfg['gone_frames']" in src)

print("\n--- 6. Cycle start from event ---")
check("Event triggers cycle start", "self._tracking_cycle_active = True" in src and "event_steps" in src)

print("\n--- 7. _rebuild_checklist includes event counters ---")
rebuild_section_start = src.find("def _rebuild_checklist")
rebuild_section_end = src.find("def ", rebuild_section_start + 10)
rebuild_section = src[rebuild_section_start:rebuild_section_end]
check("Checklist merges event counters", "_event_counters" in rebuild_section)
check("Event-only items added to checklist", "for cls_name, count in self._event_counters.items():" in rebuild_section)

print("\n--- 8. _settle_counting_cycle includes event counters ---")
settle_section_start = src.find("def _settle_counting_cycle")
settle_section_end = src.find("\n    def ", settle_section_start + 10)
settle_section = src[settle_section_start:settle_section_end]
check("merged_counters includes event counts", "merged_counters" in settle_section)
check("Event items recorded as steps", "event_counters" in settle_section.lower())
check("Event step_name format", "×" in settle_section or "\\u00d7" in settle_section)
check("Validation uses merged_counters", "merged_counters.get(cls_name, 0)" in settle_section)

print("\n--- 9. set_project_config logs event labels ---")
check("Event labels logged", "动作计数标签" in src)

print("\n--- 10. expected_items updated with event counts ---")
check("event required_count merged into expected_items",
      "expected_items[lbl] = cfg['required_count']" in src)

# ===== Frontend Tests =====
print("\n" + "=" * 60)
print("Frontend: frontend/src/views/Project/index.vue")
print("=" * 60)

vue_src = read_file('frontend/src/views/Project/index.vue')

print("\n--- 11. Table headers for event counting ---")
check("计数模式 header exists", "计数模式" in vue_src)
check("需要次数 header exists", "需要次数" in vue_src)
check("消失确认帧 header exists", "消失确认帧" in vue_src)

print("\n--- 12. Table body cells ---")
check("count_mode select exists", 'v-model="step.count_mode"' in vue_src)
check("event_required_count input exists", 'v-model="step.event_required_count"' in vue_src)
check("event_gone_frames input exists", 'v-model="step.event_gone_frames"' in vue_src)
check("Options: 跟踪计数 / 动作计数", "跟踪计数" in vue_src and "动作计数" in vue_src)
check("event_required_count disabled when not event mode",
      'step.count_mode !== \'event\'' in vue_src or "step.count_mode !== 'event'" in vue_src)

print("\n--- 13. Step defaults (new model) ---")
check("count_mode default 'track'", "count_mode: 'track'" in vue_src)
check("event_required_count default 1", "event_required_count: 1" in vue_src)
check("event_gone_frames default 8", "event_gone_frames: 8" in vue_src)

print("\n--- 14. Migration for existing steps ---")
check("count_mode migration", "step.count_mode === undefined" in vue_src)
check("event_required_count migration", "step.event_required_count === undefined" in vue_src)
check("event_gone_frames migration", "step.event_gone_frames === undefined" in vue_src)

# ===== Consistency Tests =====
print("\n" + "=" * 60)
print("Frontend-Backend Consistency")
print("=" * 60)

print("\n--- 15. Field name consistency ---")
fields = ['count_mode', 'event_required_count', 'event_gone_frames']
for field in fields:
    in_be = field in src
    in_fe = field in vue_src
    check(f"'{field}' in both backend and frontend", in_be and in_fe)

check("'event' value used consistently",
      "count_mode') == 'event'" in src and "value=\"event\"" in vue_src)
check("'track' value used consistently",
      "value=\"track\"" in vue_src)

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
