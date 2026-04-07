"""
独立检测压力测试脚本
模拟程序中的推理流程，监控 CPU/内存/线程，检查是否会导致资源耗尽
用法: conda activate tianjun && python test_detection_stress.py
"""
import cv2
import time
import psutil
import os
import sys
import threading
import traceback
from collections import deque

VIDEO_PATH = os.path.join(os.path.dirname(__file__), "01_2K17411_03.avi")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "best(5).pt")

DURATION_SECONDS = 60
REPORT_INTERVAL = 5


def get_resource_usage():
    proc = psutil.Process(os.getpid())
    mem = proc.memory_info()
    cpu = proc.cpu_percent(interval=None)
    threads = proc.num_threads()
    return {
        "rss_mb": round(mem.rss / 1024 / 1024, 1),
        "vms_mb": round(mem.vms / 1024 / 1024, 1),
        "cpu_percent": cpu,
        "threads": threads,
        "system_cpu": psutil.cpu_percent(interval=None),
        "system_mem_percent": psutil.virtual_memory().percent,
    }


def main():
    print("=" * 60)
    print("检测压力测试")
    print(f"视频: {VIDEO_PATH}")
    print(f"模型: {MODEL_PATH}")
    print(f"测试时长: {DURATION_SECONDS}s")
    print("=" * 60)

    if not os.path.isfile(VIDEO_PATH):
        print(f"视频文件不存在: {VIDEO_PATH}")
        return
    if not os.path.isfile(MODEL_PATH):
        print(f"模型文件不存在: {MODEL_PATH}")
        return

    print("\n[1/3] 加载模型...")
    from ultralytics import YOLO
    model = YOLO(MODEL_PATH)
    print(f"  模型加载完成, 类别: {model.names}")

    print("\n[2/3] 打开视频...")
    cap = cv2.VideoCapture(VIDEO_PATH)
    if not cap.isOpened():
        print("无法打开视频")
        return
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"  视频: {total_frames} 帧, {fps} FPS, 时长: {total_frames/fps:.1f}s")

    print(f"\n[3/3] 开始推理测试 ({DURATION_SECONDS}s)...")
    print("-" * 60)

    psutil.Process(os.getpid()).cpu_percent(interval=None)
    psutil.cpu_percent(interval=None)

    start_time = time.time()
    frame_count = 0
    inference_count = 0
    last_report = start_time
    peak_rss = 0
    peak_threads = 0
    errors = []

    # 模拟程序中的帧队列
    frame_queue = deque(maxlen=3)
    latest_frame = [None]
    latest_result = [None]
    running = [True]
    lock = threading.Lock()

    # 模拟捕获线程
    def capture_loop():
        nonlocal frame_count
        while running[0]:
            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
                if not ret:
                    break
            frame_count += 1
            with lock:
                latest_frame[0] = frame
            # 模拟程序中的 30fps 节流
            time.sleep(1.0 / fps)

    # 模拟推理线程
    def inference_loop():
        nonlocal inference_count
        while running[0]:
            frame = None
            with lock:
                if latest_frame[0] is not None:
                    frame = latest_frame[0].copy()

            if frame is None:
                time.sleep(0.01)
                continue

            try:
                results = model(frame, verbose=False, conf=0.5)
                inference_count += 1
                detections = []
                if results and len(results) > 0:
                    for box in results[0].boxes:
                        cls_id = int(box.cls[0])
                        conf = float(box.conf[0])
                        label = model.names.get(cls_id, str(cls_id))
                        detections.append(f"{label}({conf:.2f})")

                with lock:
                    latest_result[0] = detections
            except Exception as e:
                errors.append(str(e))
                traceback.print_exc()

            time.sleep(0.01)

    # 模拟 MJPEG 编码线程（读取帧 + 编码 JPEG）
    def mjpeg_loop():
        while running[0]:
            frame = None
            with lock:
                if latest_frame[0] is not None:
                    frame = latest_frame[0]
            if frame is not None:
                try:
                    cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                except Exception:
                    pass
            time.sleep(1.0 / 15)  # 15fps MJPEG

    # 启动所有线程
    t_capture = threading.Thread(target=capture_loop, daemon=True, name="test-capture")
    t_inference = threading.Thread(target=inference_loop, daemon=True, name="test-inference")
    t_mjpeg = threading.Thread(target=mjpeg_loop, daemon=True, name="test-mjpeg")

    t_capture.start()
    t_inference.start()
    t_mjpeg.start()

    print(f"{'时间':>6} | {'帧数':>6} | {'推理':>6} | {'RSS':>8} | {'CPU%':>6} | {'线程':>4} | {'系统CPU':>8} | {'系统MEM':>8} | 检测结果")
    print("-" * 110)

    try:
        while time.time() - start_time < DURATION_SECONDS:
            time.sleep(1)

            now = time.time()
            if now - last_report >= REPORT_INTERVAL:
                elapsed = now - start_time
                usage = get_resource_usage()
                peak_rss = max(peak_rss, usage["rss_mb"])
                peak_threads = max(peak_threads, usage["threads"])

                result_str = ""
                with lock:
                    if latest_result[0]:
                        result_str = ", ".join(latest_result[0][:5])
                        if len(latest_result[0]) > 5:
                            result_str += f"... +{len(latest_result[0])-5}"

                print(f"{elapsed:6.0f}s | {frame_count:6d} | {inference_count:6d} | {usage['rss_mb']:6.1f}MB | {usage['cpu_percent']:5.1f}% | {usage['threads']:4d} | {usage['system_cpu']:6.1f}% | {usage['system_mem_percent']:6.1f}% | {result_str}")

                if usage["system_cpu"] > 95:
                    print("  *** 警告: 系统 CPU 使用率过高!")
                if usage["system_mem_percent"] > 95:
                    print("  *** 警告: 系统内存使用率过高!")
                if usage["rss_mb"] > 4000:
                    print("  *** 警告: 进程内存超过 4GB!")

                last_report = now

    except KeyboardInterrupt:
        print("\n手动中断")

    running[0] = False
    time.sleep(1)
    cap.release()

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"  运行时间: {elapsed:.1f}s")
    print(f"  总帧数: {frame_count}")
    print(f"  总推理次数: {inference_count}")
    print(f"  平均推理 FPS: {inference_count/elapsed:.1f}")
    print(f"  峰值 RSS 内存: {peak_rss:.1f} MB")
    print(f"  峰值线程数: {peak_threads}")
    print(f"  错误数: {len(errors)}")
    if errors:
        print(f"  错误详情: {errors[:5]}")

    final = get_resource_usage()
    print(f"  最终系统 CPU: {final['system_cpu']:.1f}%")
    print(f"  最终系统内存: {final['system_mem_percent']:.1f}%")

    if final["system_cpu"] > 90 or peak_rss > 4000 or len(errors) > 0:
        print("\n结论: *** 发现潜在问题 ***")
    else:
        print("\n结论: 测试通过，未发现资源泄漏或异常")


if __name__ == "__main__":
    main()
