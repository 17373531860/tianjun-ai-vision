"""
分析视频中每个周期各步骤的首次检测时间，找出乱序原因
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
MAX_INTERVAL = 1.0
SETTLEMENT_STEP = "装袋"

cycle_number = 0
first_seen = {}
last_seen = {}
cycles_data = []
current_steps_order = []

def settle():
    global cycle_number
    cycle_number += 1
    sorted_steps = sorted(first_seen.items(), key=lambda x: x[1])
    order = [s for s, _ in sorted_steps]
    ok = (order == EXPECTED_ORDER)
    cycles_data.append({
        "num": cycle_number,
        "ok": ok,
        "first_seen": dict(first_seen),
        "order": order,
    })
    print(f"\n{'='*60}")
    print(f"周期 #{cycle_number}: {'OK' if ok else 'NG'}  ({len(order)}/{len(EXPECTED_ORDER)} 步)")
    base_t = min(first_seen.values()) if first_seen else 0
    for i, (step, t) in enumerate(sorted_steps):
        exp_idx = EXPECTED_ORDER.index(step) if step in EXPECTED_ORDER else -1
        marker = " " if (i < len(EXPECTED_ORDER) and step == EXPECTED_ORDER[i]) else "×"
        print(f"  {marker} {i+1:2d}. {step:10s}  T+{t-base_t:6.2f}s  (期望位置{exp_idx+1})")
    missing = [s for s in EXPECTED_ORDER if s not in first_seen]
    if missing:
        print(f"  缺少: {missing}")

def reset():
    first_seen.clear()
    last_seen.clear()
    current_steps_order.clear()

def main():
    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"视频: {fps:.1f}fps, {total_frames} 帧, {total_frames/fps:.0f}s")

    reset()
    frame_idx = 0
    t_start = time.time()
    settlement_disappeared_at = None

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

        for label in detected:
            if label not in first_seen:
                old_last = last_seen.get(label)
                if old_last is None or (t - old_last) > MAX_INTERVAL:
                    first_seen[label] = t
            last_seen[label] = t

        if SETTLEMENT_STEP in first_seen and SETTLEMENT_STEP not in detected:
            if settlement_disappeared_at is None:
                settlement_disappeared_at = t
            elif (t - settlement_disappeared_at) > MAX_INTERVAL:
                settle()
                reset()
                settlement_disappeared_at = None
        else:
            settlement_disappeared_at = None

        if frame_idx % 5000 == 0:
            elapsed = time.time() - t_start
            pct = frame_idx / total_frames * 100
            print(f"  [{pct:.0f}%] 帧={frame_idx}, 视频={t:.0f}s, 耗时={elapsed:.0f}s, 周期={cycle_number}")

    if first_seen:
        print(f"\n  (末尾未结算: {list(first_seen.keys())})")

    cap.release()

    print(f"\n{'='*60}")
    ok_count = sum(1 for c in cycles_data if c["ok"])
    ng_count = sum(1 for c in cycles_data if not c["ok"])
    print(f"总计: {len(cycles_data)} 个周期, OK={ok_count}, NG={ng_count}")

    print(f"\n常见乱序模式:")
    swap_counts = defaultdict(int)
    for c in cycles_data:
        if not c["ok"]:
            order = c["order"]
            for i, step in enumerate(order):
                exp_idx = EXPECTED_ORDER.index(step) if step in EXPECTED_ORDER else -1
                if i != exp_idx and exp_idx >= 0:
                    swap_counts[f"{step}(期望{exp_idx+1},实际{i+1})"] += 1
    for pattern, count in sorted(swap_counts.items(), key=lambda x: -x[1]):
        if count >= 3:
            print(f"  {pattern}: {count}次")


if __name__ == "__main__":
    main()
