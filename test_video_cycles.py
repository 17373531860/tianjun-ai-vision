"""
离线视频周期检测测试脚本
用法: python test_video_cycles.py
"""
import time, cv2
from ultralytics import YOLO

MODEL_PATH = "best.pt"
VIDEO_PATH = "D14_20260317091000.mp4"

EXPECTED_ORDER = [
    "扫码1", "安装隔音棉", "划定位线", "划定位线2",
    "扫码2", "贴标签", "扫码3", "连接",
    "亮白灯", "亮红灯", "亮绿灯", "亮蓝灯",
    "划定位线3", "装袋"
]

CONF_THRESHOLD = 0.5
MIN_FRAMES = 1
DEFAULT_MIN_DURATION = 0.0
LAST_STEP_MIN_DURATION = 0.5
DEFAULT_MAX_INTERVAL = 1.0
SETTLEMENT_STEP = "装袋"

# ── state ──
step_start_time = {}
step_last_seen = {}
step_consecutive = {}
step_raw_start = {}
step_confirmed = {}
current_cycle_steps = []
cycle_number = 0
cycles = []
last_step_completed_time = None
completed_durations = {}


def reset_cycle_state():
    global current_cycle_steps, last_step_completed_time
    step_start_time.clear()
    step_last_seen.clear()
    step_consecutive.clear()
    step_raw_start.clear()
    step_confirmed.clear()
    current_cycle_steps = []
    last_step_completed_time = None
    completed_durations.clear()


def get_min_duration(label):
    if label == SETTLEMENT_STEP:
        return LAST_STEP_MIN_DURATION
    return DEFAULT_MIN_DURATION


def settle_cycle(t):
    global cycle_number
    cycle_number += 1
    ok = (current_cycle_steps == EXPECTED_ORDER)
    missing = [s for s in EXPECTED_ORDER if s not in current_cycle_steps]
    extra = [s for s in current_cycle_steps if s not in EXPECTED_ORDER]
    wrong_order = []
    if not ok and not missing and not extra:
        for i, s in enumerate(current_cycle_steps):
            if s != EXPECTED_ORDER[i]:
                wrong_order.append(s)

    result = "OK" if ok else "NG"
    info = ""
    if missing:
        info += f" 缺少:{missing}"
    if extra:
        info += f" 多余:{extra}"
    if wrong_order:
        info += f" 顺序错:{wrong_order}"
    if not ok and not missing and not extra and not wrong_order:
        info += f" 实际:{current_cycle_steps}"

    durations = dict(completed_durations)
    for label in current_cycle_steps:
        if label not in durations:
            st = step_start_time.get(label)
            et = step_last_seen.get(label)
            if st and et:
                durations[label] = round(et - st, 2)
    cycles.append({"num": cycle_number, "result": result, "steps": list(current_cycle_steps),
                    "missing": missing, "info": info, "durations": durations})
    print(f"  周期 #{cycle_number}: {result} ({len(current_cycle_steps)}/{len(EXPECTED_ORDER)} 步){info}")
    reset_cycle_state()


def process_frame(detected_labels_set, t):
    global last_step_completed_time

    for label in detected_labels_set:
        prev = step_consecutive.get(label, 0)
        if prev == 0:
            step_raw_start[label] = t
        step_consecutive[label] = prev + 1
        if step_consecutive[label] >= MIN_FRAMES:
            if not step_confirmed.get(label):
                step_confirmed[label] = True

    confirmed_labels = set()
    for label in detected_labels_set:
        if step_confirmed.get(label):
            confirmed_labels.add(label)

    for label in list(step_consecutive.keys()):
        if label not in detected_labels_set:
            step_consecutive[label] = 0
            step_confirmed[label] = False

    for label in confirmed_labels:
        min_dur = get_min_duration(label)
        if min_dur > 0:
            raw_st = step_raw_start.get(label)
            if raw_st and (t - raw_st) < min_dur:
                continue

        old_last = step_last_seen.get(label)
        step_last_seen[label] = t

        if old_last is not None:
            is_new = (t - old_last) > DEFAULT_MAX_INTERVAL
        else:
            is_new = True

        if is_new:
            step_start_time[label] = step_raw_start.get(label, t)
            if label not in current_cycle_steps:
                current_cycle_steps.append(label)

    pending_events = []
    for label in list(step_last_seen.keys()):
        if label in confirmed_labels:
            continue
        last_t = step_last_seen[label]
        if t - last_t > DEFAULT_MAX_INTERVAL:
            start_t = step_start_time.get(label, last_t)
            dur = last_t - start_t
            min_dur = get_min_duration(label)
            if min_dur > 0 and dur < min_dur:
                current_cycle_steps[:] = [s for s in current_cycle_steps if s != label]
            if min_dur <= 0 or dur >= min_dur:
                completed_durations[label] = dur

            del step_last_seen[label]
            if label in step_start_time:
                del step_start_time[label]

            if label == SETTLEMENT_STEP and dur >= get_min_duration(label):
                pending_events.append("settle")

    for evt in pending_events:
        if evt == "settle" and len(current_cycle_steps) > 0:
            settle_cycle(t)


def main():
    print(f"模型: {MODEL_PATH}")
    print(f"视频: {VIDEO_PATH}")
    print(f"期望顺序 ({len(EXPECTED_ORDER)} 步): {EXPECTED_ORDER}")
    print(f"置信度: {CONF_THRESHOLD}, 去重间隔: {DEFAULT_MAX_INTERVAL}s")
    print(f"最后一步 [{SETTLEMENT_STEP}] min_duration: {LAST_STEP_MIN_DURATION}s")
    print()

    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"视频: {fps:.1f}fps, {total_frames} 帧, {total_frames/fps:.0f}s")
    print("=" * 60)

    reset_cycle_state()
    frame_idx = 0
    t_start = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        t = frame_idx / fps
        frame_idx += 1

        if frame_idx % 2 != 0:
            continue

        results = model.predict(frame, conf=CONF_THRESHOLD, imgsz=640, verbose=False, device="cuda:0")

        detected = set()
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                label = model.names[cls_id]
                if label in EXPECTED_ORDER:
                    detected.add(label)

        process_frame(detected, t)

        if frame_idx % 2500 == 0:
            elapsed = time.time() - t_start
            pct = frame_idx / total_frames * 100
            video_t = t
            print(f"  [{pct:.0f}%] 帧={frame_idx}, 视频时间={video_t:.0f}s, 处理耗时={elapsed:.0f}s, 已检测周期={cycle_number}")

    if len(current_cycle_steps) > 0:
        print(f"  (末尾未结算的步骤: {current_cycle_steps})")

    cap.release()
    elapsed = time.time() - t_start

    print()
    print("=" * 60)
    print(f"处理完成: {frame_idx} 帧, 耗时 {elapsed:.1f}s ({frame_idx/elapsed:.0f} fps)")
    print()

    ok_count = sum(1 for c in cycles if c["result"] == "OK")
    ng_count = sum(1 for c in cycles if c["result"] == "NG")
    print(f"总周期数: {len(cycles)}")
    print(f"  OK: {ok_count}")
    print(f"  NG: {ng_count}")

    if ng_count > 0:
        print(f"\nNG 周期详情:")
        for c in cycles:
            if c["result"] == "NG":
                print(f"  周期 #{c['num']}:{c['info']}")

    print(f"\n各步骤持续时间统计 (所有周期):")
    from collections import defaultdict
    dur_stats = defaultdict(list)
    for c in cycles:
        for label, dur in c.get("durations", {}).items():
            dur_stats[label].append(dur)
    for label in EXPECTED_ORDER:
        durs = dur_stats.get(label, [])
        if durs:
            print(f"  {label:10s}: 出现{len(durs)}次, 最短{min(durs):.2f}s, 最长{max(durs):.2f}s, 平均{sum(durs)/len(durs):.2f}s")
        else:
            print(f"  {label:10s}: 未检测到")


if __name__ == "__main__":
    main()
