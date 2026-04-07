"""
完整视频三次播放压力测试
视频 4.5 分钟，完整跑完三次（不循环），每次之间暂停 10 秒
总计约 14 分钟

用法: conda activate tianjun && python test_full_video_stress.py
"""
import cv2
import time
import psutil
import os
import threading
import traceback
import base64
import gc

VIDEO_PATH = os.path.join(os.path.dirname(__file__), "01_2K17411_03.avi")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "best(5).pt")

REPORT_INTERVAL = 10


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


class FullVideoDetection:
    def __init__(self, model, video_path):
        self.model = model
        self.video_path = video_path
        self.cap = None
        self.fps = 30
        self.running = False
        self.detecting = False
        self.video_ended = False
        self.lock = threading.Lock()
        self.latest_frame = None
        self.step_screenshots = {}
        self._last_screenshot_time = 0
        self._threads = []
        self.stats = {"frames": 0, "inferences": 0, "screenshots": 0, "mjpeg_frames": 0}

    def start_round(self):
        """开始一轮完整视频检测"""
        self._cleanup_threads()
        if self.cap:
            self.cap.release()
        self.cap = cv2.VideoCapture(self.video_path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        self.running = True
        self.detecting = True
        self.video_ended = False
        self.step_screenshots.clear()
        self._last_screenshot_time = 0

        for target, name in [
            (self._capture_loop, "capture"),
            (self._inference_loop, "inference"),
            (self._mjpeg_loop, "mjpeg"),
        ]:
            t = threading.Thread(target=target, daemon=True, name=name)
            self._threads.append(t)
            t.start()

    def pause(self):
        self.running = False
        self.detecting = False
        self._cleanup_threads()
        self.step_screenshots.clear()
        with self.lock:
            self.latest_frame = None
        gc.collect()

    def _cleanup_threads(self):
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=3.0)
        self._threads = [t for t in self._threads if t.is_alive()]
        if self._threads:
            print(f"  [警告] {len(self._threads)} 个线程未停止!")

    def _capture_loop(self):
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                self.video_ended = True
                self.running = False
                break
            self.stats["frames"] += 1
            with self.lock:
                self.latest_frame = frame
            time.sleep(1.0 / self.fps)

    def _inference_loop(self):
        last_frame_id = None
        while self.running and self.detecting:
            frame = None
            fid = None
            with self.lock:
                if self.latest_frame is not None:
                    frame = self.latest_frame.copy()
                    fid = id(self.latest_frame)
            if frame is None or fid == last_frame_id:
                time.sleep(0.001)
                continue
            last_frame_id = fid
            loop_start = time.time()
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
                        cx1, cy1 = max(0, x1 - pad), max(0, y1 - pad)
                        cx2, cy2 = min(w, x2 + pad), min(h, y2 + pad)
                        if cx2 > cx1 and cy2 > cy1:
                            crop = frame[cy1:cy2, cx1:cx2]
                            _, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                            self.step_screenshots[det["label"]] = base64.b64encode(buf).decode('utf-8')
                            self.stats["screenshots"] += 1
                    self._last_screenshot_time = now
            except Exception:
                traceback.print_exc()
            elapsed = time.time() - loop_start
            if elapsed < 0.005:
                time.sleep(0.005 - elapsed)

    def _mjpeg_loop(self):
        min_interval = 1.0 / 30
        while self.running:
            t0 = time.time()
            with self.lock:
                frame = self.latest_frame
            if frame is not None:
                cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                self.stats["mjpeg_frames"] += 1
            elapsed = time.time() - t0
            time.sleep(max(0.001, min_interval - elapsed))


def main():
    print("=" * 80)
    print("完整视频三次播放压力测试")
    print(f"视频: {VIDEO_PATH}")
    print(f"模型: {MODEL_PATH}")
    print("=" * 80)

    from ultralytics import YOLO
    model = YOLO(MODEL_PATH)
    print(f"模型加载完成: {model.names}")

    cap_tmp = cv2.VideoCapture(VIDEO_PATH)
    total_frames = int(cap_tmp.get(cv2.CAP_PROP_FRAME_COUNT))
    video_fps = cap_tmp.get(cv2.CAP_PROP_FPS)
    video_duration = total_frames / video_fps
    cap_tmp.release()
    print(f"视频时长: {video_duration:.1f}s ({video_duration/60:.1f}分钟), {total_frames}帧, {video_fps:.1f}fps")

    sim = FullVideoDetection(model, VIDEO_PATH)
    peak_cpus = []
    peak_mems = []

    psutil.Process(os.getpid()).cpu_percent(interval=None)
    psutil.cpu_percent(interval=None)

    for round_num in range(1, 4):
        print(f"\n{'='*80}")
        print(f"第 {round_num}/3 轮: 完整播放视频 ({video_duration:.0f}s)")
        print(f"{'='*80}")
        sim.start_round()

        start_time = time.time()
        last_report = start_time
        round_peak_cpu = 0
        round_peak_mem = 0

        while not sim.video_ended and sim.running:
            time.sleep(1)
            now = time.time()
            if now - last_report >= REPORT_INTERVAL:
                elapsed = now - start_time
                usage = get_resource_usage()
                round_peak_cpu = max(round_peak_cpu, usage["system_cpu"])
                round_peak_mem = max(round_peak_mem, usage["rss_mb"])
                progress = sim.stats["frames"] / total_frames * 100 if total_frames else 0
                alive = len([t for t in sim._threads if t.is_alive()])
                print(f"  [{elapsed:5.0f}s] 进度={progress:5.1f}% | RSS={usage['rss_mb']:7.1f}MB | sysCPU={usage['system_cpu']:5.1f}% | sysMEM={usage['system_mem_percent']:5.1f}% | 线程={alive} | 推理={sim.stats['inferences']}")
                if usage["system_cpu"] > 90:
                    print(f"  *** 系统 CPU 过高!")
                last_report = now

        round_elapsed = time.time() - start_time
        print(f"  第 {round_num} 轮完成: 耗时 {round_elapsed:.1f}s, 峰值CPU={round_peak_cpu:.1f}%, 峰值内存={round_peak_mem:.1f}MB")
        peak_cpus.append(round_peak_cpu)
        peak_mems.append(round_peak_mem)

        if round_num < 3:
            print(f"\n  --- 暂停 10 秒 ---")
            sim.pause()
            time.sleep(10)
            usage = get_resource_usage()
            print(f"  暂停后: RSS={usage['rss_mb']:.1f}MB, sysCPU={usage['system_cpu']:.1f}%")

    sim.pause()
    time.sleep(2)
    final = get_resource_usage()

    print(f"\n{'='*80}")
    print("最终测试结果")
    print(f"{'='*80}")
    print(f"  总推理: {sim.stats['inferences']}")
    print(f"  总截图: {sim.stats['screenshots']}")
    print(f"  总MJPEG: {sim.stats['mjpeg_frames']}")
    for i in range(3):
        print(f"  第{i+1}轮: 峰值CPU={peak_cpus[i]:.1f}%, 峰值内存={peak_mems[i]:.1f}MB")
    print(f"  最终RSS: {final['rss_mb']:.1f}MB")
    print(f"  内存增量: {peak_mems[-1] - peak_mems[0]:.1f}MB (第1轮→第3轮)")

    max_cpu = max(peak_cpus)
    if max_cpu > 85:
        print(f"\n结论: *** 风险 - 峰值CPU {max_cpu:.1f}% ***")
    else:
        print(f"\n结论: 测试通过 - 峰值CPU {max_cpu:.1f}%")


if __name__ == "__main__":
    main()
