"""
扫描不同 min_duration 阈值，不用同时出现组，看纯 min_duration 过滤效果
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
SETTLEMENT_STEP = "装袋"
MAX_INTERVAL = 1.0
DISAPPEAR_DELAY = 0.0

label_to_expected_idx = {l: i for i, l in enumerate(EXPECTED_ORDER)}


def run_test(model, frames_data, fps, min_dur_map, tag=""):
    step_raw_start = {}
    step_consecutive = {}
    step_confirmed = {}
    last_seen = {}
    current_cycle_steps = []
    cycle_number = 0
    ok_count = 0
    ng_count = 0
    ng_details = []

    def get_md(label):
        return min_dur_map.get(label, min_dur_map.get("__default__", 0))

    def settle():
        nonlocal cycle_number, ok_count, ng_count
        cycle_number += 1
        ok = (current_cycle_steps == EXPECTED_ORDER)
        if ok:
            ok_count += 1
        else:
            ng_count += 1
            missing = [s for s in EXPECTED_ORDER if s not in current_cycle_steps]
            wrong = []
            if not missing:
                for i, s in enumerate(current_cycle_steps):
                    if i < len(EXPECTED_ORDER) and s != EXPECTED_ORDER[i]:
                        wrong.append(s)
            info = ""
            if missing:
                info += f"缺少:{missing}"
            if wrong:
                info += f"顺序错:{wrong}"
            if not missing and not wrong and len(current_cycle_steps) != len(EXPECTED_ORDER):
                info += f"步数:{len(current_cycle_steps)}"
            ng_details.append(f"#{cycle_number}: {info}")

    def reset():
        step_raw_start.clear(); step_consecutive.clear(); step_confirmed.clear()
        last_seen.clear(); current_cycle_steps.clear()

    reset()
    for frame_idx, t, detected in frames_data:
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
            md = get_md(label)
            if md > 0:
                raw_st = step_raw_start.get(label)
                if raw_st and (t - raw_st) < md:
                    continue
            last_seen[label] = t
            if label not in current_cycle_steps:
                current_cycle_steps.append(label)

        pending_settle = False
        for label in list(last_seen.keys()):
            if label in detected:
                continue
            if (t - last_seen[label]) > DISAPPEAR_DELAY:
                start_t = step_raw_start.get(label, last_seen[label])
                dur = last_seen[label] - start_t
                md = get_md(label)
                if md > 0 and dur < md:
                    current_cycle_steps[:] = [s for s in current_cycle_steps if s != label]
                if label == SETTLEMENT_STEP and (md <= 0 or dur >= md):
                    pending_settle = True
                del last_seen[label]

        if pending_settle and len(current_cycle_steps) > 0:
            settle()
            reset()

    return ok_count, ng_count, ng_details, cycle_number


def main():
    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"预加载检测结果... ({total_frames} 帧)")
    frames_data = []
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
        frames_data.append((frame_idx, t, detected))
    cap.release()
    print(f"预加载完成: {len(frames_data)} 帧, 耗时 {time.time()-t_start:.0f}s")

    test_values = [0, 0.08, 0.16, 0.24, 0.32, 0.5, 0.8, 1.0]

    print(f"\n{'='*60}")
    print(f"  全局统一 min_duration 扫描 (装袋固定0.5s)")
    print(f"  去重间隔={MAX_INTERVAL}s, 消失确认={DISAPPEAR_DELAY}s, 无同时出现组")
    print(f"{'='*60}")
    print(f"{'min_dur':>8s} | {'周期数':>6s} | {'OK':>4s} | {'NG':>4s} | {'OK率':>6s} | NG详情")
    print(f"{'-'*8}-+-{'-'*6}-+-{'-'*4}-+-{'-'*4}-+-{'-'*6}-+{'-'*30}")

    for md_val in test_values:
        min_dur_map = {"__default__": md_val, "装袋": 0.5}
        ok, ng, details, total = run_test(model, frames_data, fps, min_dur_map)
        rate = f"{ok/total*100:.0f}%" if total > 0 else "N/A"
        detail_str = "; ".join(details[:3])
        if len(details) > 3:
            detail_str += f" ...+{len(details)-3}"
        print(f"{md_val:>7.2f}s | {total:>6d} | {ok:>4d} | {ng:>4d} | {rate:>6s} | {detail_str}")

    print(f"\n{'='*60}")
    print(f"  针对高误检步骤单独设 min_duration")
    print(f"{'='*60}")

    high_misdet = ["划定位线", "划定位线2", "划定位线3", "亮绿灯", "亮蓝灯", "亮白灯"]
    for md_val in [0.2, 0.3, 0.5, 0.8]:
        min_dur_map = {"__default__": 0, "装袋": 0.5}
        for label in high_misdet:
            min_dur_map[label] = md_val
        ok, ng, details, total = run_test(model, frames_data, fps, min_dur_map)
        rate = f"{ok/total*100:.0f}%" if total > 0 else "N/A"
        detail_str = "; ".join(details[:3])
        if len(details) > 3:
            detail_str += f" ...+{len(details)-3}"
        labels_str = ",".join([l[:2] for l in high_misdet])
        print(f"  [{labels_str}]={md_val}s, 其余=0 | 周期={total} OK={ok} NG={ng} ({rate}) | {detail_str}")


if __name__ == "__main__":
    main()
