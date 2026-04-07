"""Quick diagnostic: for each cycle (defined by 点亮屏幕 appearances),
check which labels are detected at any confidence."""
import cv2, time
from ultralytics import YOLO

MODEL_PATH = '/home/qianqian/1.py/output/oppo/model/oppo1.pt'
VIDEO_PATH = '/home/qianqian/1.py/video/oppo/Video_20260320203958253.avi'

model = YOLO(MODEL_PATH)
cap = cv2.VideoCapture(VIDEO_PATH)
fps = cap.get(cv2.CAP_PROP_FPS)
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

MIN_CONF = 0.50
SCREEN_LABEL = '点亮屏幕'
SCREEN_CONF = 0.70
SCREEN_MIN_FRAMES = 5
SCREEN_DISAPPEAR_FRAMES = int(0.5 * fps)

screen_consecutive = 0
screen_confirmed = False
screen_last_confirmed_frame = -999
in_screen = False

cycle_labels = {}  # {frame_range: {label: max_conf}}
current_cycle_start = 0
current_cycle_detections = {}  # {label: (count, max_conf)}

cycles = []
t0 = time.time()

for frame_idx in range(1, total + 1):
    ret, frame = cap.read()
    if not ret:
        break

    results = model.predict(frame, conf=MIN_CONF, verbose=False)
    frame_labels = {}
    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            label = model.names[cls_id]
            frame_labels[label] = max(frame_labels.get(label, 0), conf)

    # Track 点亮屏幕 state
    if SCREEN_LABEL in frame_labels and frame_labels[SCREEN_LABEL] >= SCREEN_CONF:
        screen_consecutive += 1
        if screen_consecutive >= SCREEN_MIN_FRAMES:
            if not screen_confirmed:
                screen_confirmed = True
            screen_last_confirmed_frame = frame_idx
            in_screen = True
    else:
        screen_consecutive = 0
        if in_screen and (frame_idx - screen_last_confirmed_frame) > SCREEN_DISAPPEAR_FRAMES:
            # Screen disappeared -> cycle boundary
            cycles.append({
                'start': current_cycle_start,
                'end': frame_idx,
                'detections': dict(current_cycle_detections),
            })
            current_cycle_start = frame_idx
            current_cycle_detections = {}
            in_screen = False
            screen_confirmed = False

    # Record all detections for current cycle
    for label, conf in frame_labels.items():
        if label not in current_cycle_detections:
            current_cycle_detections[label] = [0, 0.0]
        current_cycle_detections[label][0] += 1
        current_cycle_detections[label][1] = max(current_cycle_detections[label][1], conf)

    if frame_idx % 3000 == 0:
        print(f"  [{frame_idx}/{total}] {time.time()-t0:.0f}s, cycles so far: {len(cycles)}")

cap.release()

if current_cycle_detections:
    cycles.append({
        'start': current_cycle_start,
        'end': total,
        'detections': dict(current_cycle_detections),
    })

print(f"\n{'='*80}")
print(f"总周期数: {len(cycles)} (由点亮屏幕消失分隔)")
print(f"{'='*80}")

has_all = 0
missing_labels = {}
for i, c in enumerate(cycles):
    duration = (c['end'] - c['start']) / fps
    det_summary = []
    for label in ['撕膜', '顶卡拖', '点亮屏幕']:
        d = c['detections'].get(label)
        if d:
            det_summary.append(f"{label}:{d[0]}帧(max={d[1]:.2f})")
        else:
            det_summary.append(f"{label}:无")
            missing_labels[label] = missing_labels.get(label, 0) + 1
    
    all_present = all(c['detections'].get(l) for l in ['撕膜', '顶卡拖', '点亮屏幕'])
    if all_present:
        has_all += 1
    status = "OK" if all_present else "MISS"
    print(f"  周期{i+1:2d} ({duration:5.1f}s): {status} | {', '.join(det_summary)}")

print(f"\n三步都有检测的周期: {has_all}/{len(cycles)}")
print(f"各标签缺失统计: {missing_labels}")
