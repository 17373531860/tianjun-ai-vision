"""
Simulate detection on 0.mp4 with exact user parameters.
Traces cycle management logic to diagnose NG issues.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import cv2
import time
import numpy as np
from ultralytics import YOLO

VIDEO_PATH = "/home/qianqian/桌面/word/tianjun副本/0.mp4"
MODEL_PATH = "/home/qianqian/桌面/word/tianjun副本/best.pt"

# Exact parameters from user's screenshot
STEPS_CONFIG = [
    {"label": "擦试",   "enabled": True, "confidence": 0.70, "min_duration": 0.50, "max_duration": None, "max_interval": 10.0, "min_frames": 5, "detection_type": "dynamic", "accept_once": True},
    {"label": "正面检查", "enabled": True, "confidence": 0.50, "min_duration": None, "max_duration": None, "max_interval": 20.0, "min_frames": 10, "detection_type": "dynamic", "accept_once": False},
    {"label": "倾斜检查", "enabled": True, "confidence": 0.50, "min_duration": None, "max_duration": None, "max_interval": 5.0, "min_frames": 3, "detection_type": "dynamic", "accept_once": False},
    {"label": "反面检查", "enabled": True, "confidence": 0.50, "min_duration": None, "max_duration": None, "max_interval": 5.0, "min_frames": 3, "detection_type": "dynamic", "accept_once": False},
    {"label": "扫码",   "enabled": True, "confidence": 0.50, "min_duration": 0.10, "max_duration": None, "max_interval": 5.0, "min_frames": 1, "detection_type": "dynamic", "accept_once": False},
    {"label": "放置",   "enabled": True, "confidence": 0.44, "min_duration": None, "max_duration": None, "max_interval": 5.0, "min_frames": 1, "detection_type": "dynamic", "accept_once": False},
    {"label": "贴美容胶", "enabled": False, "confidence": 0.50, "min_duration": None, "max_duration": None, "max_interval": 2.0, "min_frames": 1, "detection_type": "dynamic", "accept_once": False},
]

EXPECTED_SEQUENCE = ["擦试", "正面检查", "倾斜检查", "反面检查", "扫码", "放置"]

def run_simulation():
    print("Loading model...")
    model = YOLO(MODEL_PATH)
    
    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"Video: {fps:.1f} fps, {total_frames} frames, {total_frames/fps:.1f}s")
    
    # Build config lookups
    conf_thresholds = {}
    min_frames_cfg = {}
    time_cfg = {}
    accept_once_cfg = {}
    enabled_labels = set()
    
    for step in STEPS_CONFIG:
        label = step["label"]
        if not step["enabled"]:
            continue
        enabled_labels.add(label)
        conf_thresholds[label] = step["confidence"]
        min_frames_cfg[label] = step["min_frames"]
        time_cfg[label] = {
            "min_duration": step["min_duration"],
            "max_duration": step["max_duration"],
            "max_interval": step["max_interval"],
        }
        accept_once_cfg[label] = step.get("accept_once", False)
    
    # State tracking (mirrors source.py logic)
    step_consecutive_frames = {}
    step_frame_confirmed = {}
    step_last_seen = {}
    step_start_time = {}
    step_raw_start = {}
    current_cycle_steps = []
    cycle_count = 0
    just_settled = False
    
    frame_idx = 0
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        current_time = frame_idx / fps
        frame_idx += 1
        
        # Run detection
        results = model(frame, verbose=False)
        
        # Extract detections that pass confidence
        frame_detected_labels = set()
        for r in results:
            for box in r.boxes:
                label = model.names[int(box.cls)]
                conf = float(box.conf)
                if label in conf_thresholds and conf >= conf_thresholds[label]:
                    frame_detected_labels.add(label)
        
        # Filter disabled labels
        frame_detected_labels &= enabled_labels
        
        # Min frames filtering
        detected_labels = set()
        just_confirmed = set()
        
        for label in frame_detected_labels:
            prev_count = step_consecutive_frames.get(label, 0)
            if prev_count == 0:
                step_raw_start[label] = current_time
            step_consecutive_frames[label] = prev_count + 1
            
            mf = min_frames_cfg.get(label, 1)
            if step_consecutive_frames[label] >= mf:
                detected_labels.add(label)
                if not step_frame_confirmed.get(label):
                    step_frame_confirmed[label] = True
                    just_confirmed.add(label)
        
        # Reset consecutive frames for non-detected
        for label in enabled_labels:
            if label not in frame_detected_labels:
                step_consecutive_frames[label] = 0
                step_frame_confirmed[label] = False
        
        # Process each confirmed label (simulating _process_single_step)
        for label in detected_labels:
            # Settle check - now respects min_duration
            if label in current_cycle_steps and len(current_cycle_steps) > 1:
                if label == EXPECTED_SEQUENCE[0]:
                    first_start = step_start_time.get(label)
                    first_min_dur = time_cfg.get(label, {}).get("min_duration") or 0
                    first_dur = (current_time - first_start) if first_start else 0
                    
                    if first_dur < first_min_dur:
                        # Skip settle - first step hasn't been present long enough
                        pass
                    else:
                        cycle_count += 1
                        missing = [s for s in EXPECTED_SEQUENCE if s not in current_cycle_steps]
                        result = "OK" if len(missing) == 0 else "NG"
                        
                        # Check duration filter (skip accept_once steps)
                        filtered_out = []
                        for sl in current_cycle_steps:
                            if accept_once_cfg.get(sl):
                                continue
                            if sl in step_last_seen and sl in step_start_time:
                                dur = step_last_seen[sl] - step_start_time[sl]
                                tc = time_cfg.get(sl, {})
                                md = tc.get("min_duration")
                                if md is not None and dur < md:
                                    filtered_out.append(f"{sl}({dur:.2f}s<{md}s)")
                        
                        print(f"\n{'='*60}")
                        print(f"  CYCLE {cycle_count} SETTLE at frame {frame_idx} (t={current_time:.2f}s)")
                        print(f"  Steps in cycle: {current_cycle_steps}")
                        if filtered_out:
                            print(f"  ⚠ Duration filter would remove: {filtered_out}")
                            remaining = [s for s in current_cycle_steps if not any(s in f for f in filtered_out)]
                            missing_after = [s for s in EXPECTED_SEQUENCE if s not in remaining]
                            result = "OK" if len(missing_after) == 0 else "NG"
                            print(f"  After filter: {remaining}")
                            print(f"  Missing after filter: {missing_after}")
                        else:
                            print(f"  Missing: {missing}")
                        print(f"  Result: {result}")
                        print(f"{'='*60}\n")
                        
                        # Reset
                        step_last_seen.clear()
                        step_start_time.clear()
                        current_cycle_steps = []
                        just_settled = True
            
            # Always update step_last_seen (THE FIX)
            old_last_seen = step_last_seen.get(label)
            step_last_seen[label] = current_time
            
            # Accept once check
            if accept_once_cfg.get(label) and label in current_cycle_steps:
                if label not in step_start_time:
                    step_start_time[label] = step_raw_start.get(label, current_time)
                continue
            
            # is_new_appearance
            max_interval = time_cfg.get(label, {}).get("max_interval") or 1.0
            if old_last_seen is not None:
                time_since_last = current_time - old_last_seen
                is_new = time_since_last > max_interval
            else:
                is_new = True
            
            if is_new:
                # Defer check
                if len(current_cycle_steps) == 0:
                    if just_settled:
                        if label != EXPECTED_SEQUENCE[0]:
                            continue
                        just_settled = False
                
                raw = step_raw_start.get(label, current_time)
                step_start_time[label] = raw
                
                if label not in current_cycle_steps:
                    current_cycle_steps.append(label)
                    print(f"  [{frame_idx:5d}] t={current_time:7.2f}s  +{label:8s}  cycle={current_cycle_steps}  (raw_start={raw:.2f}, confirmed={current_time:.2f})")
        
        # Check disappeared steps
        for label in list(step_last_seen.keys()):
            if label not in detected_labels:
                max_interval = time_cfg.get(label, {}).get("max_interval") or 1.0
                if current_time - step_last_seen[label] > max_interval:
                    start = step_start_time.get(label, step_last_seen[label])
                    dur = step_last_seen[label] - start
                    tc = time_cfg.get(label, {})
                    md = tc.get("min_duration")
                    mxd = tc.get("max_duration")
                    is_valid = True
                    if md is not None and dur < md:
                        is_valid = False
                    if mxd is not None and dur > mxd:
                        is_valid = False
                    
                    if not is_valid:
                        if accept_once_cfg.get(label) and label in current_cycle_steps:
                            print(f"  [{frame_idx:5d}] t={current_time:7.2f}s  ~{label:8s} invalid dur={dur:.2f}s but kept (accept_once)")
                        else:
                            old_cycle = list(current_cycle_steps)
                            current_cycle_steps = [s for s in current_cycle_steps if s != label]
                            print(f"  [{frame_idx:5d}] t={current_time:7.2f}s  ✗{label:8s} REMOVED (dur={dur:.2f}s, min={md}) cycle: {old_cycle} → {current_cycle_steps}")
                    else:
                        print(f"  [{frame_idx:5d}] t={current_time:7.2f}s  ✓{label:8s} disappeared OK (dur={dur:.2f}s)")
                    
                    del step_last_seen[label]
                    if label in step_start_time:
                        del step_start_time[label]
        
        # Print raw detections periodically for false positive tracking
        if frame_detected_labels and frame_idx % 30 == 0:
            extras = frame_detected_labels - detected_labels
            if extras:
                print(f"  [{frame_idx:5d}] t={current_time:7.2f}s  raw(unconfirmed): {extras}")
    
    cap.release()
    
    # Final cycle if any
    if current_cycle_steps:
        cycle_count += 1
        missing = [s for s in EXPECTED_SEQUENCE if s not in current_cycle_steps]
        result = "OK" if len(missing) == 0 else "NG"
        print(f"\n{'='*60}")
        print(f"  FINAL CYCLE {cycle_count} (video ended)")
        print(f"  Steps: {current_cycle_steps}")
        print(f"  Missing: {missing}")
        print(f"  Result: {result}")
        print(f"{'='*60}")
    
    print(f"\nTotal cycles: {cycle_count}")

if __name__ == "__main__":
    run_simulation()
