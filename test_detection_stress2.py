"""
贴近实际程序的检测压力测试 v2
包含：推理 + 截图编码(base64) + MJPEG编码 + 模拟数据库写入
用法: conda activate tianjun && python test_detection_stress2.py
"""
import cv2
import time
import psutil
import os
import sys
import threading
import traceback
import base64
import numpy as np

VIDEO_PATH = os.path.join(os.path.dirname(__file__), "01_2K17411_03.avi")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "best(5).pt")

DURATION_SECONDS = 180
REPORT_INTERVAL = 5


def get_resource_usage():
    proc = psutil.Process(os.getpid())
    mem = proc.memory_info()
    cpu = proc.cpu_percent(interval=None)
    threads = proc.num_threads()
    return {
        "rss_mb": round(mem.rss / 1024 / 1024, 1),
        "cpu_percent": cpu,
        "threads": threads,
        "system_cpu": psutil.cpu_percent(interval=None),
        "system_mem_percent": psutil.virtual_memory().percent,
    }


def main():
    print("=" * 70)
    print("检测压力测试 v2（含截图编码 + MJPEG + 模拟DB写入）")
    print(f"视频: {VIDEO_PATH}")
    print(f"模型: {MODEL_PATH}")
    print(f"测试时长: {DURATION_SECONDS}s")
    print("=" * 70)

    from ultralytics import YOLO
    model = YOLO(MODEL_PATH)
    print(f"模型加载完成, 类别: {model.names}")

    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30

    psutil.Process(os.getpid()).cpu_percent(interval=None)
    psutil.cpu_percent(interval=None)

    start_time = time.time()
    frame_count = 0
    inference_count = 0
    screenshot_count = 0
    mjpeg_count = 0
    last_report = start_time
    peak_rss = 0
    running = [True]
    lock = threading.Lock()
    latest_frame = [None]
    latest_result = [None]
    step_screenshots = {}
    last_screenshot_time = [0]

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
            time.sleep(1.0 / fps)

    def inference_loop():
        nonlocal inference_count, screenshot_count
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
                        xyxy = box.xyxy[0].cpu().numpy()
                        detections.append({
                            "label": label,
                            "confidence": conf,
                            "x1": int(xyxy[0]), "y1": int(xyxy[1]),
                            "x2": int(xyxy[2]), "y2": int(xyxy[3])
                        })

                # 截图编码（节流：每秒一次）
                now = time.time()
                if now - last_screenshot_time[0] >= 1.0:
                    for det in detections:
                        x1, y1, x2, y2 = det["x1"], det["y1"], det["x2"], det["y2"]
                        pad = 20
                        h, w = frame.shape[:2]
                        cx1 = max(0, x1 - pad)
                        cy1 = max(0, y1 - pad)
                        cx2 = min(w, x2 + pad)
                        cy2 = min(h, y2 + pad)
                        if cx2 > cx1 and cy2 > cy1:
                            crop = frame[cy1:cy2, cx1:cx2]
                            _, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                            step_screenshots[det["label"]] = base64.b64encode(buf).decode('utf-8')
                            screenshot_count += 1
                    last_screenshot_time[0] = now

                with lock:
                    latest_result[0] = [d["label"] for d in detections]
            except Exception as e:
                traceback.print_exc()
            time.sleep(0.01)

    def mjpeg_loop():
        """模拟 MJPEG 流（修复后：带节流）"""
        nonlocal mjpeg_count
        min_interval = 1.0 / 30
        while running[0]:
            t0 = time.time()
            frame = None
            with lock:
                if latest_frame[0] is not None:
                    frame = latest_frame[0].copy()
            if frame is not None:
                cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                mjpeg_count += 1
            elapsed = time.time() - t0
            time.sleep(max(0.001, min_interval - elapsed))

    def polling_loop():
        """模拟前端每秒轮询 detection results"""
        while running[0]:
            with lock:
                _ = latest_result[0]
                _ = dict(step_screenshots)
            time.sleep(1.0)

    threads = [
        threading.Thread(target=capture_loop, daemon=True, name="capture"),
        threading.Thread(target=inference_loop, daemon=True, name="inference"),
        threading.Thread(target=mjpeg_loop, daemon=True, name="mjpeg"),
        threading.Thread(target=polling_loop, daemon=True, name="polling"),
    ]
    for t in threads:
        t.start()

    print(f"{'时间':>6} | {'帧':>6} | {'推理':>6} | {'截图':>5} | {'MJPEG':>6} | {'RSS':>8} | {'CPU%':>6} | {'线程':>4} | {'sysCPU':>7} | {'sysMEM':>7} | 检测")
    print("-" * 120)

    try:
        while time.time() - start_time < DURATION_SECONDS:
            time.sleep(1)
            now = time.time()
            if now - last_report >= REPORT_INTERVAL:
                elapsed = now - start_time
                usage = get_resource_usage()
                peak_rss = max(peak_rss, usage["rss_mb"])
                result_str = ""
                with lock:
                    if latest_result[0]:
                        result_str = ", ".join(latest_result[0][:4])

                print(f"{elapsed:6.0f}s | {frame_count:6d} | {inference_count:6d} | {screenshot_count:5d} | {mjpeg_count:6d} | {usage['rss_mb']:6.1f}MB | {usage['cpu_percent']:5.1f}% | {usage['threads']:4d} | {usage['system_cpu']:5.1f}% | {usage['system_mem_percent']:5.1f}% | {result_str}")

                if usage["system_cpu"] > 95:
                    print("  *** 系统 CPU 过高!")
                if usage["rss_mb"] > 4000:
                    print("  *** 进程内存过高!")
                last_report = now

    except KeyboardInterrupt:
        print("\n手动中断")

    running[0] = False
    time.sleep(1)
    cap.release()

    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print("测试结果")
    print("=" * 70)
    print(f"  运行时间: {elapsed:.1f}s")
    print(f"  推理 FPS: {inference_count/elapsed:.1f}")
    print(f"  MJPEG 帧: {mjpeg_count} ({mjpeg_count/elapsed:.1f} fps)")
    print(f"  截图编码: {screenshot_count}")
    print(f"  峰值 RSS: {peak_rss:.1f} MB")

    final = get_resource_usage()
    print(f"  最终系统 CPU: {final['system_cpu']:.1f}%")
    print(f"  最终系统内存: {final['system_mem_percent']:.1f}%")

    if final["system_cpu"] > 90 or peak_rss > 4000:
        print("\n结论: *** 发现潜在问题 ***")
    else:
        print("\n结论: 测试通过")


if __name__ == "__main__":
    main()
