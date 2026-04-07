"""
精确模拟 source.py 的 last_step 结算逻辑，复现"NG后下一轮不触发事件"的bug。
参数完全对齐用户配置：
  - 步骤: 顶卡拖(min_frames=2), 撕膜(min_frames=3), 点亮屏幕(min_frames=2)
  - 置信度: 全部50%
  - 结算模式: last_step (最后一步消失触发结算)
  - 逻辑模式: sequential
  - 期望序列: 顶卡拖 → 撕膜 → 点亮屏幕
"""

import time
import cv2
from ultralytics import YOLO

MODEL_PATH = '/home/qianqian/1.py/output/oppo/model/oppo1.pt'
VIDEO_PATH = '/home/qianqian/1.py/video/oppo/Video_20260320203958253.avi'

CONF_THRESHOLD = 0.50
MIN_FRAMES = {'顶卡拖': 2, '撕膜': 3, '点亮屏幕': 2}
EXPECTED_SEQUENCE = ['顶卡拖', '撕膜', '点亮屏幕']
LAST_STEP = '点亮屏幕'
MAX_INTERVAL = 1.0  # 超过此秒数视为"重新出现"

# --- 状态变量(模拟 source.py) ---
step_consecutive_frames = {}
step_frame_confirmed = {}
step_last_seen = {}
step_start_time = {}
current_cycle_steps = []
last_added_step = None
cycle_number = 0
cycle_start_time = None
events_log = []


def reset_cycle_state():
    global current_cycle_steps, last_added_step, cycle_start_time
    current_cycle_steps = []
    last_added_step = None
    cycle_start_time = None
    step_last_seen.clear()
    step_start_time.clear()


def trigger_settlement(frame_idx, current_time):
    global cycle_number, current_cycle_steps, last_added_step, cycle_start_time
    cycle_number += 1

    last_step_label = EXPECTED_SEQUENCE[-1]
    if last_step_label in current_cycle_steps:
        split_idx = current_cycle_steps.index(last_step_label)
        this_cycle = current_cycle_steps[:split_idx + 1]
        next_carry = current_cycle_steps[split_idx + 1:]
    else:
        this_cycle = list(current_cycle_steps)
        next_carry = []

    # 判定
    if len(this_cycle) > len(EXPECTED_SEQUENCE):
        from collections import Counter
        dup = [s for s, c in Counter(this_cycle).items() if c > 1]
        result = 'NG'
        reason = f'重复步骤: {dup}'
    elif not all(lbl in this_cycle for lbl in EXPECTED_SEQUENCE):
        missing = [l for l in EXPECTED_SEQUENCE if l not in this_cycle]
        result = 'NG'
        reason = f'缺少: {missing}'
    else:
        order_ok = True
        last_pos = -1
        for lbl in EXPECTED_SEQUENCE:
            pos = this_cycle.index(lbl)
            if pos < last_pos:
                order_ok = False
                break
            last_pos = pos
        result = 'OK' if order_ok else 'NG'
        reason = '顺序正确' if order_ok else '顺序错误'

    event = {'cycle': cycle_number, 'frame': frame_idx, 'result': result,
             'reason': reason, 'this_cycle': this_cycle, 'next_carry': next_carry}
    events_log.append(event)
    print(f"\n{'='*70}")
    print(f"  周期 {cycle_number} 结算 (帧 {frame_idx}): {result} — {reason}")
    print(f"  本周期步骤: {this_cycle}")
    print(f"  下周期残留: {next_carry}")
    print(f"{'='*70}\n")

    # 重置 (使用全局引用，已在函数签名处声明)
    if next_carry:
        current_cycle_steps = next_carry
        last_added_step = next_carry[-1]
    else:
        reset_cycle_state()


def process_frame(frame_idx, detections, current_time):
    global current_cycle_steps, last_added_step, cycle_start_time, step_last_seen

    frame_detected = set()
    for det in detections:
        label = det['label']
        conf = det['confidence']
        if conf >= CONF_THRESHOLD:
            frame_detected.add(label)

    # --- 帧计数 ---
    all_labels = set(MIN_FRAMES.keys())
    for label in all_labels:
        if label in frame_detected:
            prev = step_consecutive_frames.get(label, 0)
            step_consecutive_frames[label] = prev + 1
            min_f = MIN_FRAMES.get(label, 1)
            if step_consecutive_frames[label] >= min_f:
                if not step_frame_confirmed.get(label, False):
                    step_frame_confirmed[label] = True
        else:
            was_confirmed = step_frame_confirmed.get(label, False)
            step_consecutive_frames[label] = 0
            step_frame_confirmed[label] = False

    confirmed_labels = {l for l in all_labels
                        if step_frame_confirmed.get(l, False)}

    # --- 步骤进入周期 ---
    for label in confirmed_labels:
        old_last_seen = step_last_seen.get(label)
        step_last_seen[label] = current_time

        if old_last_seen is not None:
            time_since = current_time - old_last_seen
            is_new = time_since > MAX_INTERVAL
        else:
            is_new = True

        if is_new:
            if label not in step_start_time:
                step_start_time[label] = current_time
            if len(current_cycle_steps) == 0:
                cycle_start_time = current_time
            current_cycle_steps.append(label)
            last_added_step = label

    # --- 步骤消失检测 ---
    pending_events = []
    for label in list(step_last_seen.keys()):
        if label not in confirmed_labels:
            gap = current_time - step_last_seen[label]
            if gap > 0:  # disappear_delay = 0
                start = step_start_time.get(label, step_last_seen[label])
                duration = step_last_seen[label] - start
                del step_last_seen[label]
                if label in step_start_time:
                    del step_start_time[label]
                pending_events.append(label)

    # --- 结算检查 ---
    for completed in pending_events:
        if completed == LAST_STEP:
            if completed in current_cycle_steps:
                trigger_settlement(frame_idx, current_time)
            else:
                print(f"  [帧{frame_idx}] {completed} 消失但不在当前周期中，跳过结算")


def main():
    print(f"加载模型: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)
    print(f"打开视频: {VIDEO_PATH}")
    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"FPS: {fps:.1f}, 总帧数: {total}, 时长: {total/fps:.1f}s")
    print(f"参数: conf={CONF_THRESHOLD}, min_frames={MIN_FRAMES}")
    print(f"期望序列: {EXPECTED_SEQUENCE}, 结算: {LAST_STEP} 消失")
    print(f"\n开始处理...\n")

    frame_idx = 0
    t0 = time.time()
    last_status_frame = -1

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1

        results = model.predict(frame, conf=CONF_THRESHOLD, verbose=False)
        detections = []
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                label = model.names[cls_id]
                detections.append({'label': label, 'confidence': conf})

        simulated_time = frame_idx / fps
        process_frame(frame_idx, detections, simulated_time)

        # 每100帧打印状态
        if frame_idx % 500 == 0:
            elapsed = time.time() - t0
            confirmed = {l for l in MIN_FRAMES if step_frame_confirmed.get(l)}
            print(f"[帧 {frame_idx}/{total}] 已用{elapsed:.0f}s, 当前周期: {current_cycle_steps}, 确认中: {confirmed}, 事件数: {len(events_log)}")

        # 只处理前3000帧 (~82秒) 看前几轮的行为
        if frame_idx >= 3000:
            print(f"\n--- 已处理 {frame_idx} 帧，提前结束 ---")
            break

    cap.release()
    elapsed = time.time() - t0

    print(f"\n{'='*70}")
    print(f"处理完成: {frame_idx} 帧, 耗时 {elapsed:.1f}s")
    print(f"共触发 {len(events_log)} 次结算:")
    for e in events_log:
        print(f"  周期{e['cycle']}: {e['result']} — {e['reason']}")
        print(f"    步骤: {e['this_cycle']}")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
