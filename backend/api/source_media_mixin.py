"""媒体源接入 Mixin —— 本地摄像头 / RTSP / 视频文件 / 图片。

从 VideoSourceManager 抽出 10 个方法，覆盖：
  - 本地 USB 摄像头（含 Windows DirectShow / MSMF / V4L2 后端选优 + bench fps）
  - RTSP 网络流（NVR / IP Camera）
  - 视频文件（含倍速、进度跳转、读完一帧后预览）
  - 图片（含单帧推理）
  - pause/resume 时摄像头重开

刻意排除：海康设备网络 SDK (HCNetSDK) 和海康工业相机 (MvCamera) —— 这些方法依赖
20+ 个模块级 SDK 符号（MvCamera / MV_CC_* / HCNetSession 等），需要先把 SDK 加载
逻辑独立成 loader 模块才能干净拆走。留到 P4b 处理。

宿主类必须提供（已由 _init_inference_vars 等初始化）：
  - self.capture / self.source_type / self.is_running / self.is_detecting
  - self.width / self.height / self.fps
  - self.camera_index / self._camera_backend
  - self.rtsp_url
  - self.video_path / self.video_speed / self.video_total_frames /
    self.video_current_frame / self.video_ended
  - self.image_path
  - self.frame_lock / self.current_frame / self._frame_seq
  - self.detection_lock / self.current_detections
  - self._confirmed_detections_lock / self._confirmed_detections
  - self.capture_lock / self._progress_lock / self._setting_progress / self._pending_progress
  - self._thread / self._inference_thread / self._inference_running
  - self.model / self.project_config / self._tracking_display_map

宿主类必须提供的方法：
  - self.stop(release_model: bool)
  - self._capture_loop()
  - self._start_inference_thread()
  - self._detect_only / self._detect_and_track / self._detect_segment
"""
from __future__ import annotations

import os
import platform
import threading
import time

import cv2
import numpy as np


class MediaSourceMixin:
    # ============== 本地 USB 摄像头 ==============
    def start_camera(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 60):
        """启动摄像头"""
        self.stop(release_model=False)

        # 等待一小段时间确保之前的资源已释放
        time.sleep(0.2)

        # 尝试打开摄像头（支持重试）
        max_retries = 3
        for attempt in range(max_retries):
            # Windows 上使用 DirectShow，Linux 上使用 V4L2
            if platform.system() == "Windows":
                self.capture = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
            else:
                self.capture = cv2.VideoCapture(device_index)

            if self.capture.isOpened():
                break

            if attempt < max_retries - 1:
                print(f"[Camera] 打开摄像头失败，重试 {attempt + 2}/{max_retries}...")
                time.sleep(0.5)

        if not self.capture.isOpened():
            raise Exception(f"无法打开摄像头 {device_index}，请检查设备是否被其他程序占用")

        fourcc_mjpg = cv2.VideoWriter_fourcc('M', 'J', 'P', 'G')

        def _get_fourcc_str(cap):
            fc = int(cap.get(cv2.CAP_PROP_FOURCC))
            return "".join([chr((fc >> (8 * i)) & 0xFF) for i in range(4)])

        # v2.7.15 (A+B): _bench_fps 提到外层, 所有路径共享, 且打开后立即 bench 一次,
        # 避免"DirectShow 谎报 MJPG 但实际走 YUYV 10fps"的坑
        def _bench_fps(cap, n=10, timeout=5.0):
            """快速实测帧率，带超时防止慢摄像头阻塞过久"""
            try:
                cap.read()
                t0 = time.time()
                ok = 0
                for _ in range(n):
                    if time.time() - t0 > timeout:
                        break
                    if cap.read()[0]:
                        ok += 1
                elapsed = max(time.time() - t0, 0.001)
                return ok / elapsed
            except Exception:
                return 0

        # Strategy 1: Set FOURCC before resolution (standard approach)
        self.capture.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.capture.set(cv2.CAP_PROP_FPS, fps)
        # v2.7.15 (B): 默认关小缓冲区, 减少 bench 偏差 + 降采集延迟
        try:
            self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        cc_str = _get_fourcc_str(self.capture)

        # v2.7.15 (A): 无论 FOURCC 报告如何, 都实测一次真实帧率
        # 阈值 = max(5, fps*0.6), 低于阈值就强制进入后端选优
        bench_fps_threshold = max(5.0, fps * 0.6)
        initial_bench_fps = _bench_fps(self.capture)
        print(f"[Camera] 首次实测: {cc_str} @ {initial_bench_fps:.0f}fps (阈值 {bench_fps_threshold:.0f}fps)")

        # Strategy 2: 格式非 MJPG 或 实测 FPS 低于阈值, 实测对比各后端选最快
        need_backend_probe = (cc_str != 'MJPG') or (initial_bench_fps < bench_fps_threshold)
        if need_backend_probe and platform.system() == "Windows":
            dshow_fps = initial_bench_fps
            print(f"[Camera] DirectShow({cc_str}) 采用首次实测 {dshow_fps:.0f}fps")

            # 先释放 DirectShow 再测 MSMF（某些摄像头不支持同时被两个后端打开）
            self.capture.release()
            self.capture = None
            time.sleep(0.3)

            msmf_cap = cv2.VideoCapture(device_index, cv2.CAP_MSMF)
            msmf_fps = 0
            if msmf_cap.isOpened():
                msmf_cap.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                msmf_cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                msmf_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                msmf_cap.set(cv2.CAP_PROP_FPS, fps)
                try:
                    msmf_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                msmf_cc = _get_fourcc_str(msmf_cap)
                msmf_fps = _bench_fps(msmf_cap)
                print(f"[Camera] MSMF({msmf_cc}) 实测 {msmf_fps:.0f}fps")
            else:
                print(f"[Camera] MSMF 无法打开摄像头 {device_index}")

            if msmf_fps > dshow_fps:
                self.capture = msmf_cap
                cc_str = _get_fourcc_str(self.capture)
                print(f"[Camera] 选择 MSMF 后端 ({msmf_fps:.0f}fps > DirectShow {dshow_fps:.0f}fps)")
            else:
                if msmf_cap.isOpened():
                    msmf_cap.release()
                # 重新打开 DirectShow
                self.capture = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
                self.capture.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self.capture.set(cv2.CAP_PROP_FPS, fps)
                try:
                    self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                cc_str = _get_fourcc_str(self.capture)
                print(f"[Camera] 保留 DirectShow 后端 ({dshow_fps:.0f}fps >= MSMF {msmf_fps:.0f}fps)")

            # Strategy 4: 帧率极低时尝试 CAP_ANY 和降低缓冲区
            best_fps = max(dshow_fps, msmf_fps)
            if best_fps < 5:
                print(f"[Camera] ⚠ 帧率极低({best_fps:.0f}fps)，尝试 CAP_ANY 后端...")
                any_cap = cv2.VideoCapture(device_index)
                if any_cap.isOpened():
                    any_cap.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                    any_cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                    any_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                    any_cap.set(cv2.CAP_PROP_FPS, fps)
                    try:
                        any_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass
                    any_cc = _get_fourcc_str(any_cap)
                    any_fps = _bench_fps(any_cap)
                    print(f"[Camera] CAP_ANY({any_cc}) 实测 {any_fps:.0f}fps")
                    if any_fps > best_fps:
                        self.capture.release()
                        self.capture = any_cap
                        cc_str = any_cc
                        best_fps = any_fps
                        print(f"[Camera] 选择 CAP_ANY 后端 ({any_fps:.0f}fps)")
                    else:
                        any_cap.release()

                # Strategy 5: 降低分辨率减少带宽需求
                if best_fps < 5 and (width > 640 or height > 480):
                    print("[Camera] ⚠ 尝试降低分辨率到 640x480...")
                    self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    lowres_fps = _bench_fps(self.capture)
                    print(f"[Camera] 640x480 实测 {lowres_fps:.0f}fps")
                    if lowres_fps > best_fps * 1.5:
                        print(f"[Camera] 使用低分辨率 ({lowres_fps:.0f}fps > {best_fps:.0f}fps)")
                        best_fps = lowres_fps
                    else:
                        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                        print(f"[Camera] 低分辨率无改善，恢复 {width}x{height}")

                # 设置缓冲区大小为1减少延迟
                try:
                    self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass

        # Strategy 3: If still not MJPG on Linux, try without explicit backend
        if cc_str != 'MJPG' and platform.system() != "Windows":
            print(f"[Camera] V4L2 返回 {cc_str}，尝试重新打开...")
            self.capture.release()
            self.capture = cv2.VideoCapture(device_index, cv2.CAP_V4L2)
            if self.capture.isOpened():
                self.capture.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
                self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self.capture.set(cv2.CAP_PROP_FPS, fps)
                cc_str = _get_fourcc_str(self.capture)

        actual_fps = self.capture.get(cv2.CAP_PROP_FPS)
        actual_w = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[Camera] Capture format: {cc_str}, FPS: {actual_fps}, requested: {width}x{height}, actual: {actual_w}x{actual_h}")
        if cc_str != 'MJPG':
            print(f"[Camera] ⚠ 当前格式 {cc_str} (未压缩)，高分辨率下帧率通常只有 5-10fps")
            print(f"[Camera]   原因: 大多数 USB 摄像头在 {cc_str} 模式下硬件吞吐率有限")
            print("[Camera]   建议: 1) 确认摄像头支持 MJPG  2) 降低分辨率  3) 更换支持 MJPG 的摄像头")

        self.source_type = 'camera'
        self._camera_backend = int(self.capture.get(cv2.CAP_PROP_BACKEND)) if hasattr(cv2, 'CAP_PROP_BACKEND') else None
        self.camera_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.is_running = True

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        return True

    # ============== RTSP 网络流 ==============
    def start_rtsp(self, url: str, fps: int = 25):
        """启动 RTSP 网络视频流（NVR / IP Camera）"""
        self.stop(release_model=False)
        time.sleep(0.2)

        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|analyzeduration;5000000|probesize;5000000"
        )

        safe_url = url.split("@")[-1] if "@" in url else url
        print(f"[RTSP] 正在连接: {safe_url} ...")

        max_retries = 3
        for attempt in range(max_retries):
            print(f"[RTSP] 尝试 {attempt + 1}/{max_retries} ...")
            self.capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if self.capture.isOpened():
                break
            if self.capture:
                self.capture.release()
                self.capture = None
            if attempt < max_retries - 1:
                print(f"[RTSP] 连接失败，{3}秒后重试 ...")
                time.sleep(3.0)

        if self.capture is None or not self.capture.isOpened():
            raise Exception(f"无法连接 RTSP 流: {safe_url}，请检查地址/用户名/密码/网络连通性")

        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_w = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.capture.get(cv2.CAP_PROP_FPS) or fps
        fourcc = int(self.capture.get(cv2.CAP_PROP_FOURCC))
        codec = ''.join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)]) if fourcc else "未知"

        print(f"[RTSP] 已连接: {actual_w}x{actual_h}, FPS: {actual_fps}, 编码: {codec}, URL: {safe_url}")

        # 验证能否实际读取帧（RTSP 首帧可能需要等待 I 帧）
        frame_ok = False
        for i in range(30):
            ret, frame = self.capture.read()
            if ret:
                print(f"[RTSP] 验证读帧成功 (第{i+1}次尝试), 帧尺寸: {frame.shape}")
                frame_ok = True
                break
            time.sleep(0.3)

        if not frame_ok:
            self.capture.release()
            self.capture = None
            raise Exception(
                f"RTSP 已连接但无法读取视频帧 ({safe_url})。"
                f"当前编码: {codec}。"
                f"建议在 NVR 管理页面将该通道的视频编码改为 H.264"
            )

        self.source_type = 'rtsp'
        self.rtsp_url = url
        self.width = actual_w
        self.height = actual_h
        self.fps = fps
        self.is_running = True

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    # ============== 视频文件 ==============
    def start_video(self, video_path: str, speed: float = None):
        """启动视频文件播放"""
        # 保存当前倍速设置（如果有的话）
        current_speed = self.video_speed if self.video_speed else 1.0

        self.stop(release_model=False)

        if not os.path.exists(video_path):
            raise Exception(f"视频文件不存在: {video_path}")

        self.capture = cv2.VideoCapture(video_path)
        if not self.capture.isOpened():
            raise Exception(f"无法打开视频文件: {video_path}")

        self.source_type = 'video'
        self.video_path = video_path
        self.fps = self.capture.get(cv2.CAP_PROP_FPS) or 30
        self.width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # 视频帧信息
        self.video_total_frames = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.video_current_frame = 0
        self.video_ended = False
        # 使用传入的倍速，如果没有传入则保持之前的倍速
        self.video_speed = speed if speed is not None else current_speed
        print(f"视频总帧数: {self.video_total_frames}, 倍速: {self.video_speed}x")

        self.is_running = True

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        return True

    def set_video_speed(self, speed: float):
        """设置视频播放倍速"""
        if speed < 0.25 or speed > 16:
            raise ValueError("倍速必须在 0.25 到 16 之间")
        self.video_speed = speed
        print(f"[Video] 倍速已设置为: {speed}x")

    def set_video_progress(self, progress: float):
        """设置视频播放进度 (0-1) - 修复版：记住最新请求"""
        # debug_log 仅用于诊断，延迟 import 避免与 source.py 顶层互引循环
        from backend.api.source import debug_log

        if self.source_type != 'video':
            raise Exception("当前不是视频输入源")

        if progress < 0 or progress > 1:
            raise ValueError("进度必须在 0 到 1 之间")

        # 使用锁防止并发调用（拖动进度条可能触发多次请求）
        if not self._progress_lock.acquire(blocking=False):
            # 记住最新的进度请求，等当前处理完后执行
            self._pending_progress = progress
            debug_log(f"记住待处理进度: {progress*100:.1f}%", "PROGRESS")
            return

        try:
            self._setting_progress = True
            self._pending_progress = None  # 清除待处理请求
            self._do_set_video_progress(progress)

            # 检查是否有待处理的进度请求
            while self._pending_progress is not None:
                pending = self._pending_progress
                self._pending_progress = None
                debug_log(f"处理待处理进度: {pending*100:.1f}%", "PROGRESS")
                self._do_set_video_progress(pending)

        finally:
            self._setting_progress = False
            self._progress_lock.release()

    def _do_set_video_progress(self, progress: float):
        """实际执行进度设置"""
        # 1. 记住当前状态
        was_running = self.is_running
        was_detecting = self.is_detecting

        # 2. 停止线程
        self.is_running = False
        self._inference_running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        if self._inference_thread and self._inference_thread.is_alive():
            self._inference_thread.join(timeout=0.3)
            self._inference_thread = None

        # 3. 设置视频位置
        with self.capture_lock:
            if self.capture is None or not self.capture.isOpened():
                if self.video_path and os.path.exists(self.video_path):
                    self.capture = cv2.VideoCapture(self.video_path)

            if self.capture and self.capture.isOpened():
                target_frame = int(self.video_total_frames * progress)
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                self.video_current_frame = target_frame
                self.video_ended = False

                # 暂停状态下读一帧用于预览，让用户看到拖动后的画面
                if not was_running:
                    ret, frame = self.capture.read()
                    if ret:
                        with self.frame_lock:
                            self.current_frame = frame
                        self.capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

        # 4. 只有之前在运行状态时才重新启动线程
        if was_running:
            self.is_running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()

            if was_detecting and self.model is not None:
                self.is_detecting = True
                self._start_inference_thread()

        print(f"[Video] 进度: {progress*100:.1f}%, was_running={was_running}, was_detecting={was_detecting}")

    def get_video_info(self):
        """获取视频播放信息"""
        if self.source_type != 'video':
            return None

        return {
            "total_frames": self.video_total_frames,
            "current_frame": self.video_current_frame,
            "progress": self.video_current_frame / max(self.video_total_frames, 1),
            "speed": self.video_speed,
            "ended": self.video_ended,
            "fps": self.fps,
            "duration": self.video_total_frames / max(self.fps, 1),
            "current_time": self.video_current_frame / max(self.fps, 1)
        }

    # ============== 单张图片源 ==============
    def set_image(self, image_path: str):
        """设置图片为输入源"""
        self.stop(release_model=False)

        if not os.path.exists(image_path):
            raise Exception(f"图片文件不存在: {image_path}")

        frame = cv2.imread(image_path)
        if frame is None:
            raise Exception(f"无法读取图片: {image_path}")

        self.source_type = 'image'
        self.image_path = image_path

        with self.frame_lock:
            self.current_frame = frame
            self._frame_seq += 1
        self.is_running = True

        if self.is_detecting and self.model is not None:
            self._run_image_inference(frame)

        return True

    def _run_image_inference(self, frame: np.ndarray):
        """Run inference on a single image frame and update detection results."""
        try:
            _task_type = self.project_config.get('task_type', 'detection') if self.project_config else 'detection'
            _logic_mode = self.project_config.get('logic_mode', 'sequential') if self.project_config else 'sequential'
            _is_tracking = (_logic_mode == 'tracking')
            _is_seg = (_task_type == 'segmentation')

            if _is_tracking:
                detections = self._detect_and_track(frame)
            elif _is_seg:
                detections = self._detect_segment(frame)
            else:
                detections = self._detect_only(frame)

            if _is_tracking:
                for det in detections:
                    tid = det.get('track_id', -1)
                    if tid in self._tracking_display_map:
                        det['display_id'] = self._tracking_display_map[tid]
                confirmed = detections
            else:
                confirmed = detections

            with self.detection_lock:
                self.current_detections = confirmed
            with self._confirmed_detections_lock:
                self._confirmed_detections = confirmed

            print(f"[Image] 图片推理完成, 检测到 {len(confirmed)} 个目标")
        except Exception as e:
            print(f"[Image] 图片推理失败: {e}")
            import traceback
            traceback.print_exc()

    # ============== pause/resume 时摄像头重开 ==============
    def _reopen_camera(self):
        """Re-open USB camera that was released during pause"""
        try:
            if platform.system() == "Windows":
                self.capture = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            else:
                self.capture = cv2.VideoCapture(self.camera_index)
            if not self.capture.isOpened():
                print(f"[resume] 摄像头 {self.camera_index} 打开失败")
                self.capture = None
                return False
            self.capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
            self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.capture.set(cv2.CAP_PROP_FPS, self.fps)
            print(f"[resume] 摄像头已重新打开 (MJPG): index={self.camera_index}")
            return True
        except Exception as e:
            print(f"[resume] 重新打开摄像头失败: {e}")
            return False
