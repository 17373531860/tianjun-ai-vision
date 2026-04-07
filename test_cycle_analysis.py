"""
模拟顺序模式检测，分析每个周期的步骤序列
重点观察第13-16周期

配置（与 JT-SOP2 一致）：
- 顺序模式
- 步骤顺序: 拿取产品→调节螺丝→检查内框活动性→转动滑轮→检查外观2→确认内框有无脱落→放计数板
- 置信度: 50%, 去重间隔: 3.0s
- 同时出现组: 放计数板+拿取产品, 时间窗口 4.0s（代码实际跳过了）

用法: conda activate tianjun && python test_cycle_analysis.py
"""
import cv2
import time
import os

VIDEO_PATH = os.path.join(os.path.dirname(__file__), "01_2K17411_03.avi")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "best(5).pt")

STEP_ORDER = ["拿取产品", "调节螺丝", "检查内框活动性", "转动滑轮", "检查外观2", "确认内框有无脱落", "放计数板"]
FIRST_STEP = STEP_ORDER[0]  # 拿取产品
LAST_STEP = STEP_ORDER[-1]  # 放计数板
CONF_THRESHOLD = 0.5
MAX_INTERVAL = 3.0  # 去重间隔（消失判定阈值）


def main():
    print("=" * 90)
    print("顺序模式周期分析测试")
    print(f"视频: {VIDEO_PATH}")
    print(f"模型: {MODEL_PATH}")
    print(f"步骤顺序: {' → '.join(STEP_ORDER)}")
    print(f"第一步: {FIRST_STEP}, 最后一步: {LAST_STEP}")
    print(f"置信度: {CONF_THRESHOLD}, 去重间隔: {MAX_INTERVAL}s")
    print("=" * 90)

    from ultralytics import YOLO
    model = YOLO(MODEL_PATH)

    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"视频: {total_frames} 帧, {fps:.1f} FPS, {total_frames/fps:.1f}s")

    # 状态变量
    step_last_seen = {}      # label -> last_seen_time
    step_start_time = {}     # label -> start_time
    current_cycle_steps = [] # 当前周期的步骤列表
    cycle_number = 0
    cycle_start_time = None
    last_added_step = None
    cycles = []              # 记录所有周期

    frame_count = 0
    video_time = 0.0

    print(f"\n{'='*90}")
    print("开始分析...")
    print(f"{'='*90}\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        frame_count += 1
        video_time = frame_count / fps

        # 推理
        results = model(frame, verbose=False, conf=CONF_THRESHOLD)
        
        detected_labels_this_frame = set()
        if results and len(results) > 0:
            for box in results[0].boxes:
                cls_id = int(box.cls[0])
                label = model.names.get(cls_id, str(cls_id))
                if label in STEP_ORDER:
                    detected_labels_this_frame.add(label)

        current_time = video_time
        detected_labels = set()

        # 帧数过滤（min_frames=1，所以直接通过）
        for label in detected_labels_this_frame:
            detected_labels.add(label)

        # 处理每个检测到的标签
        for label in detected_labels:
            # 判断是否"新出现"
            if label in step_last_seen:
                time_since_last = current_time - step_last_seen[label]
                # 顺序模式用纯时间判定
                is_new_appearance = time_since_last > MAX_INTERVAL
            else:
                is_new_appearance = True

            # 顺序模式：第一步重新出现时，结算上一周期
            if label == FIRST_STEP and is_new_appearance and len(current_cycle_steps) > 0:
                cycle_number += 1
                cycle_end_time = current_time
                cycle_duration = cycle_end_time - cycle_start_time if cycle_start_time else 0
                
                cycle_info = {
                    "number": cycle_number,
                    "steps": list(current_cycle_steps),
                    "start": cycle_start_time,
                    "end": cycle_end_time,
                    "duration": cycle_duration,
                    "complete": len(set(current_cycle_steps)) == len(STEP_ORDER),
                    "video_time": f"{cycle_start_time:.1f}s - {cycle_end_time:.1f}s",
                }
                cycles.append(cycle_info)

                # 判断完整性
                missing = set(STEP_ORDER) - set(current_cycle_steps)
                extra = [s for s in current_cycle_steps if current_cycle_steps.count(s) > 1]
                status = "完整" if cycle_info["complete"] and not extra else "不完整"
                
                marker = " *** " if 13 <= cycle_number <= 16 else ""
                print(f"{marker}周期 {cycle_number:3d} | {cycle_info['video_time']:>20s} | {cycle_duration:5.1f}s | {status:4s} | 步骤: {current_cycle_steps}")
                if missing:
                    print(f"         | 缺少: {missing}")
                if extra:
                    print(f"         | 重复: {extra}")

                current_cycle_steps = []
                last_added_step = None

            # 新出现的步骤
            if is_new_appearance:
                step_start_time[label] = current_time

                if len(current_cycle_steps) == 0:
                    cycle_start_time = current_time

                # 记录到当前周期（顺序模式记录完整序列包括重复）
                current_cycle_steps.append(label)
                last_added_step = label

            step_last_seen[label] = current_time

        # 检查消失的步骤
        for label in list(step_last_seen.keys()):
            if label not in detected_labels:
                if current_time - step_last_seen[label] > MAX_INTERVAL:
                    del step_last_seen[label]
                    if label in step_start_time:
                        del step_start_time[label]

    # 处理最后一个未结算的周期
    if current_cycle_steps:
        cycle_number += 1
        cycle_end_time = video_time
        cycle_duration = cycle_end_time - cycle_start_time if cycle_start_time else 0
        cycle_info = {
            "number": cycle_number,
            "steps": list(current_cycle_steps),
            "start": cycle_start_time,
            "end": cycle_end_time,
            "duration": cycle_duration,
            "complete": len(set(current_cycle_steps)) == len(STEP_ORDER),
            "video_time": f"{cycle_start_time:.1f}s - {cycle_end_time:.1f}s",
        }
        cycles.append(cycle_info)
        missing = set(STEP_ORDER) - set(current_cycle_steps)
        status = "完整" if cycle_info["complete"] and not (len(current_cycle_steps) != len(set(current_cycle_steps))) else "不完整"
        print(f"周期 {cycle_number:3d} | {cycle_info['video_time']:>20s} | {cycle_duration:5.1f}s | {status:4s} | 步骤: {current_cycle_steps} (视频结束)")

    cap.release()

    # 汇总
    print(f"\n{'='*90}")
    print("汇总")
    print(f"{'='*90}")
    print(f"总周期数: {len(cycles)}")
    complete = sum(1 for c in cycles if c["complete"])
    print(f"完整周期: {complete}")
    print(f"不完整周期: {len(cycles) - complete}")

    # 详细分析 13-16 周期
    print(f"\n{'='*90}")
    print("第 13-16 周期详细分析")
    print(f"{'='*90}")
    for c in cycles:
        if 13 <= c["number"] <= 16:
            print(f"\n周期 {c['number']}:")
            print(f"  时间: {c['video_time']}")
            print(f"  耗时: {c['duration']:.1f}s")
            print(f"  步骤序列: {c['steps']}")
            print(f"  完整性: {'完整' if c['complete'] else '不完整'}")
            missing = set(STEP_ORDER) - set(c['steps'])
            if missing:
                print(f"  缺少步骤: {missing}")
            duplicates = [s for s in c['steps'] if c['steps'].count(s) > 1]
            if duplicates:
                print(f"  重复步骤: {set(duplicates)}")
            
            # 检查步骤顺序是否正确
            unique_steps = []
            for s in c['steps']:
                if s not in unique_steps:
                    unique_steps.append(s)
            expected_indices = [STEP_ORDER.index(s) for s in unique_steps if s in STEP_ORDER]
            is_ordered = expected_indices == sorted(expected_indices)
            print(f"  顺序正确: {'是' if is_ordered else '否'}")
            if not is_ordered:
                print(f"  实际顺序索引: {expected_indices}")
                print(f"  期望顺序索引: {sorted(expected_indices)}")

    # 分析同时出现的情况（放计数板+拿取产品）
    print(f"\n{'='*90}")
    print(f"放计数板 + 拿取产品 交界分析")
    print(f"{'='*90}")
    for c in cycles:
        steps = c['steps']
        has_last = LAST_STEP in steps
        has_first = FIRST_STEP in steps
        if has_last and has_first and c['number'] < len(cycles):
            last_idx = max(i for i, s in enumerate(steps) if s == LAST_STEP)
            first_idx = min(i for i, s in enumerate(steps) if s == FIRST_STEP)
            if last_idx > first_idx:
                print(f"  周期 {c['number']}: 放计数板出现在拿取产品之后（正常）")
            else:
                print(f"  周期 {c['number']}: *** 放计数板在拿取产品之前! 步骤: {steps}")


if __name__ == "__main__":
    main()
