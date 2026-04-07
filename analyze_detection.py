"""
全面分析模型在视频上的检测行为：
- 每个标签的连续检测帧数分布（了解闪烁模式）
- 检测间隙（gap）分布（了解"消失"后多久再出现）
- 置信度分布
- 自然周期识别
"""
import time, cv2, json
import numpy as np
from collections import defaultdict
from ultralytics import YOLO

MODEL_PATH = '/home/qianqian/1.py/output/oppo/model/oppo1.pt'
VIDEO_PATH = '/home/qianqian/1.py/video/oppo/Video_20260320203958253.avi'
CONF = 0.50

model = YOLO(MODEL_PATH)
cap = cv2.VideoCapture(VIDEO_PATH)
fps = cap.get(cv2.CAP_PROP_FPS)
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
print(f"FPS: {fps:.2f}, 总帧: {total}, 时长: {total/fps:.1f}s")

# --- 跟踪变量 ---
consecutive_runs = defaultdict(list)     # label → [连续检测的帧数列表]
gap_durations = defaultdict(list)        # label → [间隙帧数列表]
confidences = defaultdict(list)          # label → [置信度列表]

current_run = {}       # label → 当前连续检测的帧数
current_gap = {}       # label → 当前间隙的帧数
was_detected = {}      # label → 上一帧是否检测到

# 时间线: 每帧哪些标签被检测到
timeline = []

t0 = time.time()
frame_idx = 0

while True:
    ret, frame = cap.read()
    if not ret:
        break
    frame_idx += 1

    results = model.predict(frame, conf=CONF, verbose=False)
    frame_labels = set()
    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            label = model.names[cls_id]
            frame_labels.add(label)
            confidences[label].append(conf)

    timeline.append(frame_labels)

    all_labels = set(model.names.values())
    for label in all_labels:
        detected = label in frame_labels
        prev = was_detected.get(label, False)

        if detected:
            if not prev:
                # 刚出现: 记录之前的 gap
                if label in current_gap and current_gap[label] > 0:
                    gap_durations[label].append(current_gap[label])
                current_gap[label] = 0
                current_run[label] = 1
            else:
                current_run[label] = current_run.get(label, 0) + 1
        else:
            if prev:
                # 刚消失: 记录连续检测帧数
                if label in current_run and current_run[label] > 0:
                    consecutive_runs[label].append(current_run[label])
                current_run[label] = 0
                current_gap[label] = 1
            else:
                current_gap[label] = current_gap.get(label, 0) + 1

        was_detected[label] = detected

    if frame_idx % 2000 == 0:
        print(f"  [{frame_idx}/{total}] {time.time()-t0:.0f}s")

# 结束时记录残留
for label in model.names.values():
    if current_run.get(label, 0) > 0:
        consecutive_runs[label].append(current_run[label])

cap.release()
elapsed = time.time() - t0
print(f"\n处理完成: {frame_idx} 帧, 耗时 {elapsed:.0f}s\n")

# --- 分析输出 ---
print("=" * 70)
print("                    检测行为分析报告")
print("=" * 70)

for label in sorted(model.names.values()):
    runs = consecutive_runs.get(label, [])
    gaps = gap_durations.get(label, [])
    confs = confidences.get(label, [])

    if not runs:
        print(f"\n【{label}】 未检测到")
        continue

    runs_arr = np.array(runs)
    gaps_arr = np.array(gaps) if gaps else np.array([0])
    confs_arr = np.array(confs)

    print(f"\n{'='*50}")
    print(f"【{label}】")
    print(f"{'='*50}")
    print(f"  检测次数(出现→消失): {len(runs)} 次")
    print(f"  总检测帧数: {sum(runs)}")

    print(f"\n  连续检测帧数分布:")
    print(f"    最小: {runs_arr.min()}, 最大: {runs_arr.max()}")
    print(f"    平均: {runs_arr.mean():.1f}, 中位: {np.median(runs_arr):.1f}")
    for thresh in [1, 2, 3, 5, 10, 20, 50]:
        pct = (runs_arr >= thresh).sum() / len(runs_arr) * 100
        print(f"    >= {thresh}帧: {(runs_arr >= thresh).sum()}/{len(runs_arr)} ({pct:.1f}%)")

    # 区分"短闪烁"和"真实出现"
    short_flicker = (runs_arr <= 2).sum()
    medium = ((runs_arr > 2) & (runs_arr <= 10)).sum()
    long_real = (runs_arr > 10).sum()
    print(f"\n  出现类型分类:")
    print(f"    短闪烁(<=2帧): {short_flicker} 次 ({short_flicker/len(runs)*100:.1f}%)")
    print(f"    中等(3-10帧):  {medium} 次 ({medium/len(runs)*100:.1f}%)")
    print(f"    真实(>10帧):   {long_real} 次 ({long_real/len(runs)*100:.1f}%)")

    if gaps:
        print(f"\n  检测间隙(gap)分布:")
        print(f"    最小: {gaps_arr.min()}帧 ({gaps_arr.min()/fps:.2f}s)")
        print(f"    最大: {gaps_arr.max()}帧 ({gaps_arr.max()/fps:.2f}s)")
        print(f"    平均: {gaps_arr.mean():.1f}帧 ({gaps_arr.mean()/fps:.2f}s)")
        print(f"    中位: {np.median(gaps_arr):.1f}帧 ({np.median(gaps_arr)/fps:.2f}s)")

        # 区分"短暂闪断"和"真正消失"
        short_gap = (gaps_arr <= 3).sum()
        medium_gap = ((gaps_arr > 3) & (gaps_arr <= int(fps))).sum()
        long_gap = (gaps_arr > int(fps)).sum()
        print(f"\n    短暂闪断(<=3帧):     {short_gap} ({short_gap/len(gaps)*100:.1f}%)")
        print(f"    中等间隙(4-{int(fps)}帧):   {medium_gap} ({medium_gap/len(gaps)*100:.1f}%)")
        print(f"    真正消失(>{int(fps)}帧/1s): {long_gap} ({long_gap/len(gaps)*100:.1f}%)")

    print(f"\n  置信度分布:")
    print(f"    最小: {confs_arr.min():.3f}, 最大: {confs_arr.max():.3f}")
    print(f"    平均: {confs_arr.mean():.3f}, 中位: {np.median(confs_arr):.3f}")
    for thresh in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        pct = (confs_arr >= thresh).sum() / len(confs_arr) * 100
        print(f"    >= {thresh:.2f}: {pct:.1f}%")

# --- 推荐参数 ---
print(f"\n{'='*70}")
print("                    推荐参数")
print(f"{'='*70}")
for label in sorted(model.names.values()):
    runs = consecutive_runs.get(label, [])
    gaps = gap_durations.get(label, [])
    if not runs:
        continue
    runs_arr = np.array(runs)
    gaps_arr = np.array(gaps) if gaps else np.array([0])
    confs_arr = np.array(confidences.get(label, []))

    # min_frames: 过滤掉短闪烁。选择能过滤95%+短闪烁但保留真实检测的值
    # 找到一个阈值使得 <=阈值的"出现"大部分是噪声
    real_runs = runs_arr[runs_arr > 10]  # "真实"检测
    flicker_runs = runs_arr[runs_arr <= 5]  # "闪烁"检测
    if len(real_runs) > 0:
        # min_frames 设为：能过滤掉大部分闪烁但不影响真实检测
        # 真实检测的最小帧数
        real_min = real_runs.min() if len(real_runs) > 0 else 10
        rec_min_frames = max(3, min(int(np.percentile(runs_arr, 25)), real_min - 1))
    else:
        rec_min_frames = 3

    # max_interval: 同一步骤两次"出现"间隔多大算"重新出现"
    # 大于这个间隔 = 新出现（可能是新周期开始）
    # 小于这个间隔 = 闪断恢复（同一次出现）
    if len(gaps) > 0:
        # 找到区分"闪断"和"真正消失"的阈值
        # 使用 gap 分布的某个百分位
        rec_max_interval_frames = max(int(np.percentile(gaps_arr, 85)), 5)
        rec_max_interval_sec = rec_max_interval_frames / fps
    else:
        rec_max_interval_sec = 2.0

    # confidence: 使用能保留95%真实检测的最低值
    if len(confs_arr) > 0:
        rec_conf = max(0.50, float(np.percentile(confs_arr, 5)))
    else:
        rec_conf = 0.50

    print(f"\n【{label}】")
    print(f"  推荐 min_frames: {rec_min_frames} (当前可能为2-3)")
    print(f"  推荐 max_interval: {rec_max_interval_sec:.2f}s ({rec_max_interval_frames}帧)")
    print(f"  推荐 confidence: {rec_conf:.2f}")
    print(f"  推荐 disappear_delay: {min(rec_max_interval_frames, 5)/fps:.2f}s ({min(rec_max_interval_frames, 5)}帧)")
