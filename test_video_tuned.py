"""
参数调优测试：同时出现组 + min_duration
"""
import time, cv2
from ultralytics import YOLO
from collections import defaultdict

MODEL_PATH = "best.pt"
VIDEO_PATH = "D14_20260317091000.mp4"

EXPECTED_ORDER = [
    "扫码1", "安装隔音棉", "划定位线", "划定位线2",
    "扫码2", "贴标签", "扫码3", "连接",
    "亮白灯", "亮红灯", "亮绿灯", "亮蓝灯",
    "划定位线3", "装袋"
]

CONF_THRESHOLD = 0.5

SIM_GROUPS = [
    ["划定位线", "划定位线2", "扫码2", "贴标签", "扫码3", "连接"],
    ["亮白灯", "亮红灯", "亮绿灯", "亮蓝灯", "划定位线3"],
]
SIM_WINDOW = 3.0

STEP_MIN_DURATION = {
    "装袋": 0.5,
}
DEFAULT_MIN_DURATION = 0.0
MAX_INTERVAL = 10.0       # 去重间隔：只影响"是否同一次出现"
DISAPPEAR_DELAY = 0.0     # 消失确认：步骤消失后多久算真正消失（0=立即）

SETTLEMENT_STEP = "装袋"

label_to_group = {}
for g in SIM_GROUPS:
    for label in g:
        label_to_group[label] = g

label_to_expected_idx = {l: i for i, l in enumerate(EXPECTED_ORDER)}

cycle_number = 0
cycles_data = []

first_seen_raw = {}
last_seen = {}
step_consecutive = {}
step_raw_start = {}
step_confirmed = {}
current_cycle_steps = []
cycle_first_seen = {}


def reset():
    first_seen_raw.clear()
    last_seen.clear()
    step_consecutive.clear()
    step_raw_start.clear()
    step_confirmed.clear()
    current_cycle_steps.clear()
    cycle_first_seen.clear()


def get_min_duration(label):
    return STEP_MIN_DURATION.get(label, DEFAULT_MIN_DURATION)


def add_step_ordered(label, t):
    if label in current_cycle_steps:
        return
    group = label_to_group.get(label)
    if group:
        group_in_cycle = [s for s in current_cycle_steps if s in group]
        expected_indices = [label_to_expected_idx[s] for s in group_in_cycle]
        my_idx = label_to_expected_idx[label]
        insert_pos = None
        for i, s in enumerate(current_cycle_steps):
            if s in group and label_to_expected_idx[s] > my_idx:
                insert_pos = i
                break
        if insert_pos is not None:
            current_cycle_steps.insert(insert_pos, label)
        else:
            if group_in_cycle:
                last_group_pos = max(i for i, s in enumerate(current_cycle_steps) if s in group)
                current_cycle_steps.insert(last_group_pos + 1, label)
            else:
                current_cycle_steps.append(label)
    else:
        current_cycle_steps.append(label)


def settle(t):
    global cycle_number
    cycle_number += 1
    ok = (current_cycle_steps == EXPECTED_ORDER)
    missing = [s for s in EXPECTED_ORDER if s not in current_cycle_steps]
    wrong_order = []
    if not ok and not missing:
        for i, s in enumerate(current_cycle_steps):
            if i < len(EXPECTED_ORDER) and s != EXPECTED_ORDER[i]:
                wrong_order.append(s)

    result = "OK" if ok else "NG"
    info = ""
    if missing:
        info += f" 缺少:{missing}"
    if wrong_order:
        info += f" 顺序错:{wrong_order}"

    cycles_data.append({"num": cycle_number, "ok": ok, "steps": list(current_cycle_steps),
                         "missing": missing, "info": info})
    status = "OK" if ok else "NG"
    print(f"  周期 #{cycle_number}: {status} ({len(current_cycle_steps)}/{len(EXPECTED_ORDER)} 步){info}")
    if not ok:
        base_t = min(cycle_first_seen.values()) if cycle_first_seen else 0
        for i, step in enumerate(current_cycle_steps):
            exp_pos = label_to_expected_idx.get(step, -1) + 1
            ft = cycle_first_seen.get(step, 0) - base_t
            marker = " " if (i < len(EXPECTED_ORDER) and step == EXPECTED_ORDER[i]) else "×"
            print(f"    {marker} {i+1:2d}. {step:10s} T+{ft:5.1f}s (期望{exp_pos})")
    reset()


def process_frame(detected, t):
    for label in detected:
        was_consecutive = step_consecutive.get(label, 0) > 0
        if not was_consecutive:
            is_new = (label not in last_seen) or (t - last_seen.get(label, 0)) > MAX_INTERVAL
            if is_new or label not in step_raw_start:
                step_raw_start[label] = t
        step_consecutive[label] = step_consecutive.get(label, 0) + 1
        step_confirmed[label] = True

    for label in list(step_consecutive.keys()):
        if label not in detected:
            step_consecutive[label] = 0
            step_confirmed[label] = False

    for label in detected:
        if not step_confirmed.get(label):
            continue
        min_dur = get_min_duration(label)
        if min_dur > 0:
            raw_st = step_raw_start.get(label)
            if raw_st and (t - raw_st) < min_dur:
                continue
        last_seen[label] = t
        if label not in cycle_first_seen:
            cycle_first_seen[label] = t
        add_step_ordered(label, t)

    pending_settle = False
    for label in list(last_seen.keys()):
        if label in detected:
            continue
        if (t - last_seen[label]) > DISAPPEAR_DELAY:
            start_t = step_raw_start.get(label, last_seen[label])
            dur = last_seen[label] - start_t
            min_dur = get_min_duration(label)
            if min_dur > 0 and dur < min_dur:
                current_cycle_steps[:] = [s for s in current_cycle_steps if s != label]
            if label == SETTLEMENT_STEP and (min_dur <= 0 or dur >= min_dur):
                pending_settle = True
            del last_seen[label]

    if pending_settle and len(current_cycle_steps) > 0:
        settle(t)


def main():
    print(f"=== 参数调优测试 ===")
    print(f"同时出现组: {SIM_GROUPS}")
    print(f"同组窗口: {SIM_WINDOW}s")
    print(f"步骤min_duration: {STEP_MIN_DURATION}")
    print(f"默认min_duration: {DEFAULT_MIN_DURATION}s")
    print(f"去重间隔: {MAX_INTERVAL}s")
    print(f"消失确认: {DISAPPEAR_DELAY}s")
    print(f"置信度: {CONF_THRESHOLD}")
    print()

    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"视频: {fps:.1f}fps, {total_frames} 帧, {total_frames/fps:.0f}s")
    print("=" * 60)

    reset()
    frame_idx = 0
    t_start = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        if frame_idx % 2 != 0:
            continue

        t = frame_idx / fps
        results = model.predict(frame, conf=CONF_THRESHOLD, imgsz=640, verbose=False, device="cuda:0")

        detected = set()
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                label = model.names[cls_id]
                if label in EXPECTED_ORDER:
                    detected.add(label)

        process_frame(detected, t)

        if frame_idx % 5000 == 0:
            elapsed = time.time() - t_start
            pct = frame_idx / total_frames * 100
            print(f"  [{pct:.0f}%] 帧={frame_idx}, 视频={t:.0f}s, 耗时={elapsed:.0f}s, 周期={cycle_number}")

    if current_cycle_steps:
        print(f"  (末尾未结算: {current_cycle_steps})")

    cap.release()
    elapsed = time.time() - t_start

    print(f"\n{'='*60}")
    ok_count = sum(1 for c in cycles_data if c["ok"])
    ng_count = sum(1 for c in cycles_data if not c["ok"])
    print(f"总计: {len(cycles_data)} 个周期, OK={ok_count}, NG={ng_count}")
    print(f"处理耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
