"""
验证跟踪模式时间延迟修复效果.

直接仿真 VideoSourceManager 的"采集 + 推理"双线程架构:
- 采集线程: 按视频帧率读帧, 计 fps_actual
- 推理线程: 独立跑 YOLO 模型, 计 fps_inference
- 每秒打印: 采集 FPS / 推理 FPS / 两者比值, 以及"遮挡容忍 1 秒"在
  新/旧公式下各会换成多少帧

用法:
    python tools/test_tracking_fps.py <model.pt> <video.mp4> [--duration 15]

不需要启动后端, 纯离线验证换算逻辑是否从根因上修好.
"""
import argparse
import sys
import threading
import time
from collections import deque

import cv2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model", help="模型 .pt 路径")
    ap.add_argument("video", help="视频 .mp4 路径")
    ap.add_argument("--duration", type=float, default=12.0,
                    help="跑多少秒后停, 默认 12 秒")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--tolerance-sec", type=float, default=1.0,
                    help="用户界面上的 '遮挡容忍(秒)', 用来演示换算差异")
    ap.add_argument("--throttle-ms", type=float, default=0,
                    help="每帧推理强制最少多少毫秒, 模拟弱 GPU/大模型")
    args = ap.parse_args()

    print(f"[init] 加载模型: {args.model}")
    from ultralytics import YOLO
    model = YOLO(args.model)

    # 预热 (避免第一秒推理慢拉低统计)
    print("[init] 预热...")
    cap_probe = cv2.VideoCapture(args.video)
    ok, frame = cap_probe.read()
    cap_probe.release()
    if not ok:
        print(f"[error] 读不到视频: {args.video}")
        sys.exit(1)
    for _ in range(3):
        model(frame, imgsz=args.imgsz, verbose=False)
    print("[init] 预热完成\n")

    cap = cv2.VideoCapture(args.video)
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"[init] 视频帧率 = {video_fps:.1f} fps  分辨率 = "
          f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
          f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}")

    # 模拟 VideoSourceManager 的双线程共享变量
    state = {
        "latest_frame": None,
        "latest_frame_id": None,
        "is_running": True,
        "fps_actual": 0,
        "fps_inference": 0,
        "_fps_counter": 0,
        "_fps_time": time.time(),
        "_fps_inference_counter": 0,
        "_fps_inference_time": time.time(),
        # 给每次推理消耗时间, 看看 variance
        "inference_times_ms": deque(maxlen=200),
    }
    lock = threading.Lock()

    def capture_loop():
        """模拟 _capture_loop"""
        target_interval = 1.0 / video_fps if video_fps > 0 else 0.04
        frame_counter = 0
        next_tick = time.time()
        while state["is_running"]:
            now = time.time()
            if now < next_tick:
                time.sleep(max(0, next_tick - now))
            next_tick += target_interval

            ok, frame = cap.read()
            if not ok:
                # 循环播放, 保证测试时长足够
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            frame_counter += 1
            with lock:
                state["latest_frame"] = frame
                state["latest_frame_id"] = frame_counter

            state["_fps_counter"] += 1
            if time.time() - state["_fps_time"] >= 1.0:
                state["fps_actual"] = state["_fps_counter"]
                state["_fps_counter"] = 0
                state["_fps_time"] = time.time()

    def inference_loop():
        """模拟 _inference_loop"""
        last_id = None
        while state["is_running"]:
            with lock:
                frame = state["latest_frame"]
                fid = state["latest_frame_id"]
            if frame is None or fid == last_id:
                time.sleep(0.001)
                continue
            last_id = fid

            t0 = time.time()
            _ = model(frame, imgsz=args.imgsz, verbose=False)
            t1 = time.time()
            state["inference_times_ms"].append((t1 - t0) * 1000)

            # v2.7.13: 推理 FPS 统计
            state["_fps_inference_counter"] += 1
            if t1 - state["_fps_inference_time"] >= 1.0:
                state["fps_inference"] = state["_fps_inference_counter"]
                state["_fps_inference_counter"] = 0
                state["_fps_inference_time"] = t1

            # 节流
            elapsed = t1 - t0
            min_s = max(0.005, args.throttle_ms / 1000.0)
            if elapsed < min_s:
                time.sleep(min_s - elapsed)

    th_cap = threading.Thread(target=capture_loop, daemon=True)
    th_inf = threading.Thread(target=inference_loop, daemon=True)
    th_cap.start()
    th_inf.start()

    # 每秒打印一次
    print("=" * 96)
    print(f"{'秒':>3} | {'采集FPS':>7} | {'推理FPS':>7} | {'比值':>5} | "
          f"{'旧公式帧数':>10} | {'新公式帧数':>10} | {'旧实际延迟':>10} | {'新实际延迟':>10}")
    print("-" * 96)
    start = time.time()
    for sec in range(1, int(args.duration) + 1):
        time.sleep(1.0)
        fa = state["fps_actual"]
        fi = state["fps_inference"]
        ratio = (fa / fi) if fi > 0 else 0
        # 模拟代码里 "max(fps, 10)" 保底
        old_frames = int(args.tolerance_sec * max(fa, 10))
        new_frames = int(args.tolerance_sec * max(fi, 10))
        # 实际秒数 = 阈值帧数 ÷ 真实推理速度
        old_real_sec = old_frames / max(fi, 1)
        new_real_sec = new_frames / max(fi, 1)
        print(f"{sec:>3} | {fa:>7} | {fi:>7} | {ratio:>5.1f}x | "
              f"{old_frames:>10} | {new_frames:>10} | "
              f"{old_real_sec:>8.2f}s | {new_real_sec:>8.2f}s")

    state["is_running"] = False
    time.sleep(0.2)
    cap.release()

    # 汇总
    times = list(state["inference_times_ms"])
    if times:
        print("\n[stat] 推理耗时样本数 =", len(times))
        print(f"[stat] 推理耗时 min/mean/max = "
              f"{min(times):.1f} / {sum(times)/len(times):.1f} / {max(times):.1f} ms")

    print("\n[总结]")
    print(f"  用户界面 设置 '遮挡容忍 = {args.tolerance_sec} 秒'")
    print(f"  推理 FPS ≈ {state['fps_inference']}")
    print(f"  旧代码实际生效 ≈ {old_real_sec:.2f} 秒 "
          f"(按采集 FPS {state['fps_actual']} 算, 放大 {ratio:.1f}x)")
    print(f"  新代码实际生效 ≈ {new_real_sec:.2f} 秒 (按推理 FPS 算, 所见即所得)")


if __name__ == "__main__":
    main()
