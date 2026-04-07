"""
验证缓冲排序层对周期结算的影响。

对比两种模式：
1. 无缓冲（原始）：检测到什么就直接处理
2. 有缓冲（新）：同时出现组的成员先收集，全员到齐后按配置顺序输出

配置（与 JT-SOP2 一致）：
- 步骤顺序: 拿取产品→调节螺丝→检查内框活动性→转动滑轮→检查外观2→确认内框有无脱落→放计数板
- 同时出现组: [放计数板, 拿取产品], 时间窗口 4.0s, 优先顺序: 放计数板 → 拿取产品
"""
import cv2
import os

VIDEO_PATH = os.path.join(os.path.dirname(__file__), "01_2K17411_03.avi")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "best(5).pt")

STEP_ORDER = ["拿取产品", "调节螺丝", "检查内框活动性", "转动滑轮", "检查外观2", "确认内框有无脱落", "放计数板"]
FIRST_STEP = STEP_ORDER[0]
LAST_STEP = STEP_ORDER[-1]
CONF_THRESHOLD = 0.5
MAX_INTERVAL = 3.0

SIM_GROUP = {
    'labels': [LAST_STEP, FIRST_STEP],
    'priority_order': [LAST_STEP, FIRST_STEP],
    'time_window': 4.0,
}


def settle_cycle(cycle_number, current_cycle_steps, cycle_start_time, current_time, mode_label):
    cycle_number += 1
    duration = current_time - cycle_start_time if cycle_start_time else 0
    missing = set(STEP_ORDER) - set(current_cycle_steps)
    extra = [s for s in current_cycle_steps if current_cycle_steps.count(s) > 1]
    complete = len(set(current_cycle_steps)) == len(STEP_ORDER) and not extra
    status = "OK" if complete else "NG"
    
    marker = " *** " if 13 <= cycle_number <= 16 else "     "
    print(f"{marker}[{mode_label}] 周期 {cycle_number:3d} | {cycle_start_time:.1f}s-{current_time:.1f}s | {duration:5.1f}s | {status} | {current_cycle_steps}")
    if missing:
        print(f"       缺少: {missing}")
    if extra:
        print(f"       重复: {set(extra)}")
    
    return cycle_number, complete


def run_without_buffer(all_detections, fps):
    """原始模式：没有缓冲层"""
    step_last_seen = {}
    current_cycle_steps = []
    cycle_number = 0
    cycle_start_time = None
    ok_count = 0
    ng_count = 0

    for frame_idx, detected_labels in all_detections:
        current_time = frame_idx / fps

        for label in detected_labels:
            if label in step_last_seen:
                is_new = (current_time - step_last_seen[label]) > MAX_INTERVAL
            else:
                is_new = True

            if label == FIRST_STEP and is_new and len(current_cycle_steps) > 0:
                cn, complete = settle_cycle(cycle_number, current_cycle_steps, cycle_start_time, current_time, "无缓冲")
                cycle_number = cn
                if complete:
                    ok_count += 1
                else:
                    ng_count += 1
                current_cycle_steps = []

            if is_new:
                if len(current_cycle_steps) == 0:
                    cycle_start_time = current_time
                current_cycle_steps.append(label)

            step_last_seen[label] = current_time

        for label in list(step_last_seen.keys()):
            if label not in detected_labels:
                if current_time - step_last_seen[label] > MAX_INTERVAL:
                    del step_last_seen[label]

    if current_cycle_steps:
        cn, complete = settle_cycle(cycle_number, current_cycle_steps, cycle_start_time, all_detections[-1][0] / fps, "无缓冲")
        cycle_number = cn
        if complete:
            ok_count += 1
        else:
            ng_count += 1

    return cycle_number, ok_count, ng_count


def run_with_buffer(all_detections, fps):
    """新模式：带缓冲排序层"""
    step_last_seen = {}
    current_cycle_steps = []
    cycle_number = 0
    cycle_start_time = None
    ok_count = 0
    ng_count = 0

    group_labels = set(SIM_GROUP['labels'])
    priority_order = SIM_GROUP['priority_order']
    time_window = SIM_GROUP['time_window']
    
    buf_collecting = False
    buf_start_time = None
    buf_collected = set()

    def process_label(label, current_time):
        nonlocal cycle_number, current_cycle_steps, cycle_start_time, ok_count, ng_count

        if label in step_last_seen:
            is_new = (current_time - step_last_seen[label]) > MAX_INTERVAL
        else:
            is_new = True

        if label == FIRST_STEP and is_new and len(current_cycle_steps) > 0:
            cn, complete = settle_cycle(cycle_number, current_cycle_steps, cycle_start_time, current_time, "有缓冲")
            cycle_number = cn
            if complete:
                ok_count += 1
            else:
                ng_count += 1
            current_cycle_steps = []

        if is_new:
            if len(current_cycle_steps) == 0:
                cycle_start_time = current_time
            current_cycle_steps.append(label)

        step_last_seen[label] = current_time

    for frame_idx, detected_labels in all_detections:
        current_time = frame_idx / fps
        
        present_members = group_labels & detected_labels
        pending = set()
        ready_ordered = []

        if not buf_collecting:
            if present_members:
                if len(current_cycle_steps) == 0:
                    pass
                else:
                    has_potential_new = False
                    for lbl in present_members:
                        if lbl not in step_last_seen:
                            has_potential_new = True
                            break
                        if current_time - step_last_seen[lbl] > MAX_INTERVAL:
                            has_potential_new = True
                            break
                    
                    if not has_potential_new:
                        pass
                    elif present_members >= group_labels:
                        ready_ordered = [l for l in priority_order if l in group_labels]
                    else:
                        buf_collecting = True
                        buf_start_time = current_time
                        buf_collected = set(present_members)
                        pending = set(present_members)
        else:
            buf_collected.update(present_members)
            elapsed = current_time - buf_start_time
            
            if buf_collected >= group_labels:
                ready_ordered = [l for l in priority_order if l in group_labels]
                buf_collecting = False
                buf_collected = set()
            elif elapsed > time_window:
                ready_ordered = [l for l in priority_order if l in buf_collected]
                buf_collecting = False
                buf_collected = set()
            else:
                pending = set(buf_collected)

        # 缓冲中的标签：不更新 step_last_seen，释放时保留原始值以正确判断 is_new

        for label in ready_ordered:
            process_label(label, current_time)

        for label in detected_labels:
            if label in pending or label in set(ready_ordered):
                continue
            process_label(label, current_time)

        for label in list(step_last_seen.keys()):
            if label in pending:
                continue
            if label not in detected_labels:
                if current_time - step_last_seen[label] > MAX_INTERVAL:
                    del step_last_seen[label]

    if current_cycle_steps:
        cn, complete = settle_cycle(cycle_number, current_cycle_steps, cycle_start_time, all_detections[-1][0] / fps, "有缓冲")
        cycle_number = cn
        if complete:
            ok_count += 1
        else:
            ng_count += 1

    return cycle_number, ok_count, ng_count


def main():
    print("=" * 100)
    print("缓冲排序层 vs 无缓冲 对比测试")
    print(f"同时出现组: {SIM_GROUP['labels']}, 优先顺序: {SIM_GROUP['priority_order']}, 窗口: {SIM_GROUP['time_window']}s")
    print("=" * 100)

    from ultralytics import YOLO
    model = YOLO(MODEL_PATH)

    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"视频: {total_frames} 帧, {fps:.1f} FPS, {total_frames/fps:.1f}s")
    print("正在预处理所有帧的检测结果...")

    all_detections = []
    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1
        
        results = model(frame, verbose=False, conf=CONF_THRESHOLD)
        detected = set()
        if results and len(results) > 0:
            for box in results[0].boxes:
                cls_id = int(box.cls[0])
                label = model.names.get(cls_id, str(cls_id))
                if label in STEP_ORDER:
                    detected.add(label)
        
        all_detections.append((frame_count, detected))
        
        if frame_count % 500 == 0:
            pct = frame_count / total_frames * 100
            print(f"  已处理 {frame_count}/{total_frames} ({pct:.0f}%)")

    cap.release()
    print(f"预处理完成: {len(all_detections)} 帧\n")

    print("=" * 100)
    print("模式 1: 无缓冲（原始逻辑）")
    print("=" * 100)
    total1, ok1, ng1 = run_without_buffer(all_detections, fps)

    print(f"\n{'='*100}")
    print("模式 2: 有缓冲（新逻辑）")
    print("=" * 100)
    total2, ok2, ng2 = run_with_buffer(all_detections, fps)

    print(f"\n{'='*100}")
    print("对比结果")
    print("=" * 100)
    print(f"  无缓冲: {total1} 周期, OK={ok1}, NG={ng1}")
    print(f"  有缓冲: {total2} 周期, OK={ok2}, NG={ng2}")
    diff = ok2 - ok1
    if diff > 0:
        print(f"  改善: 有缓冲比无缓冲多 {diff} 个 OK 周期")
    elif diff < 0:
        print(f"  退化: 有缓冲比无缓冲少 {abs(diff)} 个 OK 周期")
    else:
        print(f"  持平: OK 周期数相同")


if __name__ == "__main__":
    main()
