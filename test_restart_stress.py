"""
启停重启压力测试 - 模拟用户实际操作流程
1. 开始检测 → 运行60秒
2. 暂停检测 → 等待10秒（模拟查看数据）
3. 重新开始检测 → 运行60秒
4. 再次暂停 → 等待5秒
5. 再次开始 → 运行60秒
全程监控 CPU、内存、线程数

用法: conda activate tianjun && python test_restart_stress.py
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

REPORT_INTERVAL = 5


class DetectionSimulator:
    """模拟实际程序的检测流程"""
    
    def __init__(self, model, video_path):
        self.model = model
        self.video_path = video_path
        self.cap = None
        self.running = False
        self.detecting = False
        self.lock = threading.Lock()
        self.latest_frame = None
        self.latest_result = None
        self.step_screenshots = {}
        self._last_screenshot_time = 0
        self._threads = []
        self.stats = {
            "frames": 0,
            "inferences": 0,
            "screenshots": 0,
            "mjpeg_frames": 0,
        }
    
    def start_video(self):
        """启动视频源"""
        self.stop()
        self.cap = cv2.VideoCapture(self.video_path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        self.running = True
        t = threading.Thread(target=self._capture_loop, daemon=True, name="capture")
        self._threads.append(t)
        t.start()
    
    def start_detection(self):
        """开始检测"""
        if not self.running and self.cap and self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.running = True
            t = threading.Thread(target=self._capture_loop, daemon=True, name="capture")
            self._threads.append(t)
            t.start()
        
        self.detecting = True
        t = threading.Thread(target=self._inference_loop, daemon=True, name="inference")
        self._threads.append(t)
        t.start()
        t2 = threading.Thread(target=self._mjpeg_loop, daemon=True, name="mjpeg")
        self._threads.append(t2)
        t2.start()
    
    def pause(self):
        """暂停 - 模拟实际的 pause() 行为"""
        self.running = False
        self.detecting = False
        self._wait_threads(timeout=2.0)
        self.step_screenshots.clear()
        print("  [模拟] 已暂停")
    
    def stop(self):
        """完全停止"""
        self.running = False
        self.detecting = False
        self._wait_threads(timeout=2.0)
        if self.cap:
            self.cap.release()
            self.cap = None
        self.step_screenshots.clear()
        with self.lock:
            self.latest_frame = None
            self.latest_result = None
    
    def _wait_threads(self, timeout=2.0):
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=timeout)
        self._threads = [t for t in self._threads if t.is_alive()]
    
    def _capture_loop(self):
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self.cap.read()
                if not ret:
                    break
            self.stats["frames"] += 1
            with self.lock:
                self.latest_frame = frame
            time.sleep(1.0 / self.fps)
    
    def _inference_loop(self):
        last_frame_id = None
        while self.running and self.detecting:
            frame = None
            with self.lock:
                if self.latest_frame is not None:
                    frame = self.latest_frame.copy()
                    fid = id(self.latest_frame)
            
            if frame is None or (last_frame_id is not None and fid == last_frame_id):
                time.sleep(0.001)
                continue
            last_frame_id = fid
            
            try:
                results = self.model(frame, verbose=False, conf=0.5)
                self.stats["inferences"] += 1
                detections = []
                if results and len(results) > 0:
                    for box in results[0].boxes:
                        cls_id = int(box.cls[0])
                        conf = float(box.conf[0])
                        label = self.model.names.get(cls_id, str(cls_id))
                        xyxy = box.xyxy[0].cpu().numpy()
                        detections.append({
                            "label": label, "confidence": conf,
                            "x1": int(xyxy[0]), "y1": int(xyxy[1]),
                            "x2": int(xyxy[2]), "y2": int(xyxy[3])
                        })
                
                now = time.time()
                if now - self._last_screenshot_time >= 1.0:
                    for det in detections:
                        x1, y1, x2, y2 = det["x1"], det["y1"], det["x2"], det["y2"]
                        pad = 20
                        h, w = frame.shape[:2]
                        cx1, cy1 = max(0, x1-pad), max(0, y1-pad)
                        cx2, cy2 = min(w, x2+pad), min(h, y2+pad)
                        if cx2 > cx1 and cy2 > cy1:
                            crop = frame[cy1:cy2, cx1:cx2]
                            _, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                            self.step_screenshots[det["label"]] = base64.b64encode(buf).decode('utf-8')
                            self.stats["screenshots"] += 1
                    self._last_screenshot_time = now
                
                with self.lock:
                    self.latest_result = [d["label"] for d in detections]
            except Exception as e:
                traceback.print_exc()
            
            # 推理节流
            time.sleep(0.005)
    
    def _mjpeg_loop(self):
        min_interval = 1.0 / 30
        while self.running:
            t0 = time.time()
            frame = None
            with self.lock:
                if self.latest_frame is not None:
                    frame = self.latest_frame.copy()
            if frame is not None:
                cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                self.stats["mjpeg_frames"] += 1
            elapsed = time.time() - t0
            time.sleep(max(0.001, min_interval - elapsed))


def get_resource_usage():
    proc = psutil.Process(os.getpid())
    mem = proc.memory_info()
    return {
        "rss_mb": round(mem.rss / 1024 / 1024, 1),
        "cpu_percent": proc.cpu_percent(interval=None),
        "threads": proc.num_threads(),
        "system_cpu": psutil.cpu_percent(interval=None),
        "system_mem_percent": psutil.virtual_memory().percent,
    }


def monitor(sim, duration, phase_name):
    """监控一个阶段"""
    start = time.time()
    last_report = start
    peak_cpu = 0
    
    psutil.Process(os.getpid()).cpu_percent(interval=None)
    psutil.cpu_percent(interval=None)
    
    while time.time() - start < duration:
        time.sleep(1)
        now = time.time()
        if now - last_report >= REPORT_INTERVAL:
            elapsed = now - start
            usage = get_resource_usage()
            peak_cpu = max(peak_cpu, usage["system_cpu"])
            result_str = ""
            with sim.lock:
                if sim.latest_result:
                    result_str = ", ".join(sim.latest_result[:3])
            
            alive_threads = len([t for t in sim._threads if t.is_alive()])
            print(f"  [{phase_name}] {elapsed:5.0f}s | RSS={usage['rss_mb']:7.1f}MB | sysCPU={usage['system_cpu']:5.1f}% | sysMEM={usage['system_mem_percent']:5.1f}% | 活线程={alive_threads} | 推理={sim.stats['inferences']} | {result_str}")
            
            if usage["system_cpu"] > 90:
                print(f"  *** 警告: 系统 CPU 过高! {usage['system_cpu']:.1f}%")
            last_report = now
    
    return peak_cpu


def main():
    print("=" * 80)
    print("启停重启压力测试")
    print(f"视频: {VIDEO_PATH}")
    print(f"模型: {MODEL_PATH}")
    print("=" * 80)
    
    from ultralytics import YOLO
    model = YOLO(MODEL_PATH)
    print(f"模型加载完成, 类别: {model.names}")
    
    sim = DetectionSimulator(model, VIDEO_PATH)
    
    # 阶段 1: 首次检测 60 秒
    print("\n--- 阶段 1: 首次启动检测 (60s) ---")
    sim.start_video()
    sim.start_detection()
    peak1 = monitor(sim, 60, "检测中")
    
    # 阶段 2: 暂停 10 秒（模拟查看数据）
    print("\n--- 阶段 2: 暂停 (10s, 模拟查看数据) ---")
    sim.pause()
    peak2 = monitor(sim, 10, "已暂停")
    
    # 阶段 3: 重新开始检测 60 秒（关键！这是死机的场景）
    print("\n--- 阶段 3: 重新开始检测 (60s) --- *** 关键阶段 ***")
    sim.start_detection()
    peak3 = monitor(sim, 60, "重启检测")
    
    # 阶段 4: 再次暂停 5 秒
    print("\n--- 阶段 4: 再次暂停 (5s) ---")
    sim.pause()
    peak4 = monitor(sim, 5, "已暂停")
    
    # 阶段 5: 第三次开始 60 秒
    print("\n--- 阶段 5: 第三次开始检测 (60s) ---")
    sim.start_detection()
    peak5 = monitor(sim, 60, "三次检测")
    
    # 停止
    sim.stop()
    
    print("\n" + "=" * 80)
    print("测试结果汇总")
    print("=" * 80)
    print(f"  总推理次数: {sim.stats['inferences']}")
    print(f"  总截图编码: {sim.stats['screenshots']}")
    print(f"  总 MJPEG 帧: {sim.stats['mjpeg_frames']}")
    print(f"  各阶段峰值系统 CPU:")
    print(f"    阶段1 (首次检测): {peak1:.1f}%")
    print(f"    阶段2 (暂停):     {peak2:.1f}%")
    print(f"    阶段3 (重启检测): {peak3:.1f}%  *** 关键 ***")
    print(f"    阶段4 (再暂停):   {peak4:.1f}%")
    print(f"    阶段5 (三次检测): {peak5:.1f}%")
    
    final = get_resource_usage()
    print(f"  最终 RSS: {final['rss_mb']:.1f} MB")
    
    max_peak = max(peak1, peak3, peak5)
    if max_peak > 85:
        print(f"\n结论: *** 潜在风险 - 峰值CPU {max_peak:.1f}% ***")
    else:
        print(f"\n结论: 测试通过 - 峰值CPU {max_peak:.1f}%")


if __name__ == "__main__":
    main()
