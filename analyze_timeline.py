"""找到最佳 disappear_delay 值"""
import cv2
from ultralytics import YOLO

VIDEO = '/home/qianqian/1.py/output/oppo/装电池/video/Video_20260331083930218.avi'
MODEL = '/home/qianqian/1.py/output/oppo/装电池/model/best.pt'

CONF = {'撕璃形纸': 0.90, '翻电池': 0.65, '安装电池': 0.65, '翻手机': 0.71}
EXPECTED_SEQ = ['撕璃形纸', '翻电池', '安装电池', '翻手机']
MIN_DUR = {'撕璃形纸': 1.00, '翻电池': 0.15, '安装电池': 0.20, '翻手机': 0.05}

model = YOLO(MODEL)
cap = cv2.VideoCapture(VIDEO)
fps = cap.get(cv2.CAP_PROP_FPS)

print(f"FPS={fps:.1f}\n正在分析视频...")
frame_detections = []
frame_idx = 0
while True:
    ret, frame = cap.read()
    if not ret:
        break
    frame_idx += 1
    if frame_idx % 3 != 0:
        continue
    vt = frame_idx / fps
    results = model.predict(frame, conf=0.25, verbose=False)
    detected = set()
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf_val = float(box.conf[0])
            name = model.names[cls_id]
            if conf_val >= CONF.get(name, 0.25):
                detected.add(name)
    frame_detections.append((vt, detected))
cap.release()
print(f"完成，{len(frame_detections)} 帧\n")


def simulate(disappear_delay_val, min_dur_override=None):
    md = min_dur_override or MIN_DUR
    step_last_seen = {}
    step_start_time = {}
    step_raw_start = {}
    current_cycle_steps = []
    cycles = []
    first_step = EXPECTED_SEQ[0]

    for vt, detected in frame_detections:
        for label in list(step_last_seen.keys()):
            if label not in detected:
                gap = vt - step_last_seen[label]
                if gap > disappear_delay_val:
                    start = step_start_time.get(label, step_last_seen[label])
                    dur = step_last_seen[label] - start
                    min_d = md.get(label, 0)
                    if dur < min_d:
                        if label in current_cycle_steps:
                            current_cycle_steps.remove(label)
                    del step_last_seen[label]
                    step_start_time.pop(label, None)

        for label in EXPECTED_SEQ:
            if label not in detected:
                continue
            old_last = step_last_seen.get(label)
            if label == first_step and label in current_cycle_steps and len(current_cycle_steps) > 1:
                if old_last is None:
                    raw_s = step_raw_start.get(label, vt)
                    dur = vt - raw_s
                    min_d = md.get(label, 0)
                    if dur >= min_d:
                        cycles.append(list(current_cycle_steps))
                        current_cycle_steps = []
                        step_last_seen.clear()
                        step_start_time.clear()
                        step_raw_start.clear()
                        old_last = None

            is_new = (old_last is None)
            if is_new:
                step_start_time[label] = vt
                step_raw_start[label] = vt
                if len(current_cycle_steps) == 0 and label != first_step:
                    step_last_seen[label] = vt
                    continue
                if label not in current_cycle_steps:
                    current_cycle_steps.append(label)
            step_last_seen[label] = vt

    if current_cycle_steps:
        cycles.append(list(current_cycle_steps))
    return cycles


print("=" * 60)
print("第一轮：只调 disappear_delay，其他参数不变")
print("=" * 60)
for d in [0, 0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0]:
    cycles = simulate(d)
    ok = sum(1 for c in cycles if c == EXPECTED_SEQ)
    pct = ok / len(cycles) * 100 if cycles else 0
    ng_types = {}
    for c in cycles:
        if c != EXPECTED_SEQ:
            k = ' -> '.join(c)
            ng_types[k] = ng_types.get(k, 0) + 1
    top_ng = sorted(ng_types.items(), key=lambda x: -x[1])[:2]
    ng_str = ', '.join(f"{k}({v})" for k, v in top_ng) if top_ng else "无"
    print(f"  delay={d:4.1f}s → 周期={len(cycles):>2}, OK={ok:>2} ({pct:>4.0f}%), 主要NG: {ng_str}")

print()
print("=" * 60)
print("第二轮：delay=1.0s + 调翻手机 min_dur")
print("=" * 60)
for fan_min in [0.05, 0.10, 0.15, 0.20, 0.30]:
    md = dict(MIN_DUR)
    md['翻手机'] = fan_min
    cycles = simulate(1.0, md)
    ok = sum(1 for c in cycles if c == EXPECTED_SEQ)
    pct = ok / len(cycles) * 100 if cycles else 0
    ng_types = {}
    for c in cycles:
        if c != EXPECTED_SEQ:
            k = ' -> '.join(c)
            ng_types[k] = ng_types.get(k, 0) + 1
    top_ng = sorted(ng_types.items(), key=lambda x: -x[1])[:2]
    ng_str = ', '.join(f"{k}({v})" for k, v in top_ng) if top_ng else "无"
    print(f"  翻手机 min={fan_min:.2f}s → 周期={len(cycles):>2}, OK={ok:>2} ({pct:>4.0f}%), 主要NG: {ng_str}")

print()
print("=" * 60)
print("第三轮：delay=1.0s + 翻手机0.20s + 调翻电池 min_dur")
print("=" * 60)
for dc_min in [0.05, 0.10, 0.12, 0.15, 0.20]:
    md = dict(MIN_DUR)
    md['翻手机'] = 0.20
    md['翻电池'] = dc_min
    cycles = simulate(1.0, md)
    ok = sum(1 for c in cycles if c == EXPECTED_SEQ)
    pct = ok / len(cycles) * 100 if cycles else 0
    ng_types = {}
    for c in cycles:
        if c != EXPECTED_SEQ:
            k = ' -> '.join(c)
            ng_types[k] = ng_types.get(k, 0) + 1
    top_ng = sorted(ng_types.items(), key=lambda x: -x[1])[:2]
    ng_str = ', '.join(f"{k}({v})" for k, v in top_ng) if top_ng else "无"
    print(f"  翻电池 min={dc_min:.2f}s → 周期={len(cycles):>2}, OK={ok:>2} ({pct:>4.0f}%), 主要NG: {ng_str}")
