"""采集线程主循环 (_capture_loop, v2.7.16 P6 阶段一从 source.py 整体搬出)。

把 323 行的 _capture_loop 直接整体搬到独立文件。后续如需细分,
可在 mixin 内继续按职责拆 _capture_grab_frame / _capture_process_frame /
_capture_handle_no_frame / _capture_recover_from_errors。

宿主必须提供的属性: self.cap / self.source_type / self.running / self.fps_capture /
                  self.latest_frame / self._frame_lock / self.video_speed /
                  self.is_recording / self.video_writer / 海康相机句柄等
宿主必须提供的方法: self._apply_frame_transform / self._update_mediapipe_overlays
                  (实际方法名以 v2.7.14 源代码为准)
"""
from __future__ import annotations

import platform
import time
import traceback

import cv2

from backend.api.source_sdk_loader import debug_log
from backend.core import debug_center


class CaptureLoopMixin:
    def _capture_loop(self):
        """
        摄像头/视频捕获循环（主线程）
        
        双线程架构：
        - 主线程：读帧 → 取结果 → 滤波 → 显示（不阻塞）
        - 推理线程：推理 → 帧计数 → 步骤判断 → 事件触发（独立运行）
        """
        debug_log("========== 捕获线程开始 ==========", "CAPTURE")
        print("[Capture] thread started")
        frame_start_time = time.time()
        consecutive_errors = 0  # 连续错误计数
        max_consecutive_errors = 10  # 最大连续错误次数
        last_heartbeat_log = time.time()
        loop_count = 0  # 循环计数
        last_debug_time = time.time()  # 上次调试日志时间
        # backend.capture 采集摘要 (每 2s): 采集fps / 帧序号推进 / 读帧均耗时 / 推流冻结
        cap_win_start = time.time()
        cap_frames = 0           # 本窗口成功采集帧数
        cap_read_total = 0.0     # 读帧累计耗时 (ms), 仅开关开时累计
        cap_read_n = 0
        
        # 如果正在检测且模型已加载，启动推理线程
        if self.is_detecting and (self.model is not None or getattr(self, 'source_type', None) == 'synthetic'):
            debug_log("启动推理线程...", "CAPTURE")
            self._start_inference_thread()
            debug_log("推理线程已启动", "CAPTURE")
        
        while self.is_running and (
            self.capture is not None
            or self.source_type in ('hikvision', 'hcnetsdk', 'synthetic')
        ):
            try:
                loop_count += 1
                loop_start = time.time()
                
                if loop_start - last_debug_time > 5.0:
                    last_debug_time = loop_start

                # backend.capture 采集摘要 (每 2s 节流, 关闭时仅 dict 查询)
                if loop_start - cap_win_start >= 2.0:
                    if debug_center.is_on("backend.capture"):
                        _win = loop_start - cap_win_start
                        _cap_fps = cap_frames / _win if _win > 0 else 0
                        _read_avg = (cap_read_total / cap_read_n) if cap_read_n else -1
                        _frozen = getattr(self, '_pending_ack', False)
                        debug_center.dbg(
                            "backend.capture", "采集摘要",
                            f"channel={getattr(self, 'channel_id', '?')} 采集={_cap_fps:.1f}fps "
                            f"帧序号={getattr(self, '_frame_seq', '?')} 读帧均耗="
                            f"{('%.1fms' % _read_avg) if _read_avg >= 0 else 'n/a'} "
                            f"推流冻结(人工确认)={_frozen} "
                            f"(采集fps骤降/帧序号不推进 → 画面卡住或闪)")
                    cap_win_start = loop_start
                    cap_frames = 0
                    cap_read_total = 0.0
                    cap_read_n = 0

                # 更新捕获线程心跳
                self._last_capture_heartbeat = time.time()
                
                speed = getattr(self, 'video_speed', 1.0)
                
                # 根据输入源类型读取帧
                frame = None
                ret = False
                
                if self.source_type == 'synthetic':
                    frame = self._synthetic_next_frame()
                    ret = frame is not None
                elif self.source_type == 'hcnetsdk':
                    # 海康设备网络SDK
                    frame = self._get_hcnetsdk_frame()
                    ret = frame is not None
                elif self.source_type == 'hikvision':
                    # 海康工业相机
                    t_hik_start = time.time()
                    frame = self._get_hikvision_frame()
                    t_hik_end = time.time()
                    ret = frame is not None
                    
                    # 如果帧获取耗时超过200ms，记录警告
                    hik_time = (t_hik_end - t_hik_start) * 1000
                    if hik_time > 200:
                        debug_log(f"!!! 海康帧获取慢: {hik_time:.1f}ms, ret={ret}", "CAPTURE")
                else:
                    # 普通摄像头或视频文件
                    # 对于视频输入源，如果倍速大于1，通过跳帧实现
                    if self.source_type == 'video' and speed > 1:
                        # 跳过一些帧来实现倍速
                        frames_to_skip = int(speed) - 1
                        for _ in range(frames_to_skip):
                            ret = self.capture.grab()  # 只抓取不解码，更快
                            if not ret:
                                break
                    
                    try:
                        t_read_start = time.time()
                        ret, frame = self.capture.read()
                        t_read_end = time.time()
                        read_time = (t_read_end - t_read_start) * 1000
                        if read_time > 200:
                            debug_log(f"!!! 帧读取慢: {read_time:.1f}ms", "CAPTURE")
                        if debug_center.is_on("backend.capture"):
                            cap_read_total += read_time
                            cap_read_n += 1
                    except Exception as e:
                        debug_log(f"帧读取异常: {e}", "CAPTURE")
                        ret = False
                
                if ret and frame is not None:
                    cap_frames += 1
                    # Single copy from OpenCV's internal buffer (which may be
                    # reused on the next capture.read()).  This copy is then
                    # shared read-only across inference, streaming, and recording
                    # threads — no further copies are needed in the capture loop.
                    raw_frame = frame.copy()

                    # ========== v2.7.14: "只翻显示, 不翻推理" ==========
                    # raw_frame 保留原始摄像头视角, 仅给模型推理使用 → 检测精度不受翻转影响;
                    # display_frame 是变换后的帧, 给 MJPEG / 录像 / 快照 / stats_screenshot;
                    # 推理输出的 bbox 会在 _inference_loop 里通过
                    # _map_detections_original_to_display 映射到显示坐标系, 下游 ROI/容器/前端
                    # 画框等全部基于显示坐标系, 完全对齐。
                    if self._has_display_transform():
                        display_frame = self._apply_frame_transform(raw_frame.copy())
                    else:
                        display_frame = raw_frame
                    # original_frame 作为历史命名保留, 一律指向 display_frame
                    # (下游大量代码用 original_frame 做 MJPEG/录像/screenshot)
                    original_frame = display_frame

                    # 更新视频当前帧位置
                    if self.source_type == 'video' and self.capture is not None:
                        self.video_current_frame = int(self.capture.get(cv2.CAP_PROP_POS_FRAMES))
                    
                    # ========== 预缩小帧：推理基于 raw_frame, 录制/stats 基于 display_frame ==========
                    small_frame = None          # 显示坐标系的缩小帧 (给录制 / stats 截图)
                    raw_small_frame = None      # 原图坐标系的缩小帧 (给模型推理)
                    if self.is_detecting and (self.model is not None or self.source_type == 'synthetic'):
                        target_sz = getattr(self, '_model_imgsz', 640)
                        # 显示帧缩小
                        oh, ow = original_frame.shape[:2]
                        if max(oh, ow) > target_sz * 1.2:
                            scale = target_sz / max(oh, ow)
                            nw = int(ow * scale) // 2 * 2
                            nh = int(oh * scale) // 2 * 2
                            small_frame = cv2.resize(original_frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
                        else:
                            small_frame = original_frame
                        # 原图帧缩小 (喂模型)
                        if self._has_display_transform():
                            rh, rw = raw_frame.shape[:2]
                            if max(rh, rw) > target_sz * 1.2:
                                scale_r = target_sz / max(rh, rw)
                                rnw = int(rw * scale_r) // 2 * 2
                                rnh = int(rh * scale_r) // 2 * 2
                                raw_small_frame = cv2.resize(raw_frame, (rnw, rnh), interpolation=cv2.INTER_LINEAR)
                            else:
                                raw_small_frame = raw_frame
                        else:
                            # 无变换时两者指向同一个缓冲, 零额外开销
                            raw_small_frame = small_frame
                    
                    # ========== 双线程架构：异步推理 ==========
                    if self.is_detecting and (self.model is not None or self.source_type == 'synthetic'):
                        t_lock1_start = time.time()
                        with self._inference_frame_lock:
                            self._latest_frame_for_inference = raw_small_frame
                            self._latest_display_small_for_stats = small_frame
                            self._latest_frame_original_size = raw_frame.shape[:2]
                            if self.source_type == 'synthetic':
                                self._latest_synthetic_inference_idx = int(
                                    getattr(self, '_synthetic_last_published_idx', -1)
                                )
                        t_lock1_end = time.time()
                        if (t_lock1_end - t_lock1_start) > 0.1:
                            debug_log(f"!!! inference_frame_lock 耗时: {(t_lock1_end-t_lock1_start)*1000:.1f}ms", "CAPTURE")
                        
                        # 获取已确认的检测结果并应用滤波（非阻塞）
                        t_lock2_start = time.time()
                        with self._confirmed_detections_lock:
                            confirmed_detections = self._confirmed_detections.copy()
                        t_lock2_end = time.time()
                        if (t_lock2_end - t_lock2_start) > 0.1:
                            debug_log(f"!!! confirmed_detections_lock 耗时: {(t_lock2_end-t_lock2_start)*1000:.1f}ms", "CAPTURE")
                        
                        # 应用卡尔曼滤波平滑
                        t_kalman_start = time.time()
                        smoothed_detections = self._apply_kalman_filter(confirmed_detections)
                        t_kalman_end = time.time()
                        if (t_kalman_end - t_kalman_start) > 0.1:
                            debug_log(f"!!! 卡尔曼滤波耗时: {(t_kalman_end-t_kalman_start)*1000:.1f}ms", "CAPTURE")
                        
                        # 更新当前检测结果（供前端获取）
                        t_lock3_start = time.time()
                        with self.detection_lock:
                            self.current_detections = smoothed_detections
                        t_lock3_end = time.time()
                        if (t_lock3_end - t_lock3_start) > 0.1:
                            debug_log(f"!!! detection_lock 耗时: {(t_lock3_end-t_lock3_start)*1000:.1f}ms", "CAPTURE")
                    
                    # 写入视频录制队列 — 用缩小帧（已接近录制分辨率）
                    if self.is_detecting and self.recording_enabled:
                        self._enqueue_frame_for_recording(small_frame if small_frame is not None else original_frame)
                    
                    # MediaPipe 骨架叠加（仅在推理时显示，不影响录制）
                    display_frame = original_frame
                    if self.mediapipe_enabled and self.is_detecting:
                        display_frame = original_frame.copy()
                        self._apply_mediapipe_overlay(display_frame)
                    
                    # v3.9.x 人工确认阻塞门 (推流冻结):
                    # 阻塞期间不更新 current_frame / 不递增 _frame_seq → MJPEG generator
                    # 看到 seq 不变, 不 yield 新帧, 浏览器维持触发瞬间最后一帧 (含检测框).
                    # 实际效果: 工人看到 NG 提示框 + 当时的画面定格, 一目了然知道哪件需要重做.
                    if not getattr(self, '_pending_ack', False):
                        t_lock4_start = time.time()
                        with self.frame_lock:
                            self.current_frame = display_frame
                            self._frame_seq += 1
                        t_lock4_end = time.time()
                        if (t_lock4_end - t_lock4_start) > 0.1:
                            debug_log(f"!!! frame_lock 耗时: {(t_lock4_end-t_lock4_start)*1000:.1f}ms", "CAPTURE")
                    
                    # FPS 计算
                    self._fps_counter += 1
                    if time.time() - self._fps_time >= 1.0:
                        self.fps_actual = self._fps_counter
                        self._fps_counter = 0
                        self._fps_time = time.time()
                    
                else:
                    if self.source_type == 'rtsp':
                        consecutive_errors += 1
                        if consecutive_errors >= 5:
                            print(f"[RTSP] {consecutive_errors} consecutive frame failures, reconnecting...")
                            try:
                                if self.capture is not None:
                                    self.capture.release()
                                time.sleep(2.0)
                                self.capture = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                                self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                                if self.capture.isOpened():
                                    print("[RTSP] reconnect ok")
                                    consecutive_errors = 0
                                else:
                                    print("[RTSP] reconnect failed, retrying later...")
                                    time.sleep(3.0)
                            except Exception as e:
                                print(f"[RTSP] reconnect error: {e}")
                                time.sleep(3.0)
                        else:
                            time.sleep(0.05)
                    elif self.source_type == 'video' and self.video_path:
                        print("[Video] playback finished, stopped")
                        self.video_ended = True
                        _before_running, _before_detecting = True, self.is_detecting
                        self.is_running = False
                        if self.is_detecting:
                            self.stop_detection()
                        self._fire_source_status_change(
                            _before_running, _before_detecting, "capture_loop_video_ended"
                        )
                        break
                    else:
                        time.sleep(0.01)
                
                # 计算帧处理耗时，动态调整 sleep 时间
                frame_elapsed = time.time() - frame_start_time
                target_interval = 1.0 / max(self.fps * speed, 1)
                sleep_time = max(0, target_interval - frame_elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                frame_start_time = time.time()
                
                # 重置连续错误计数（成功处理一帧）
                consecutive_errors = 0
                
                # 定期打印心跳日志（每30秒）
                if time.time() - last_heartbeat_log > 30:
                    print(f"[Capture/Heartbeat] running, FPS={self.fps_actual}, source={self.source_type}")
                    last_heartbeat_log = time.time()
                    
            except Exception as e:
                consecutive_errors += 1
                print(f"[Capture] error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                import traceback
                traceback.print_exc()
                
                # 如果连续错误太多，尝试恢复
                if consecutive_errors >= max_consecutive_errors:
                    print(f"[Capture] {max_consecutive_errors} consecutive errors, attempting recovery...")
                    try:
                        if self.source_type == 'hcnetsdk':
                            print("[HCNetSDK] reconnecting...")
                            try:
                                self._release_hcnet_session()
                                time.sleep(2.0)
                                self._reconnect_hcnetsdk()
                                print("[HCNetSDK] reconnect OK")
                                consecutive_errors = 0
                            except Exception as re_err:
                                print(f"[HCNetSDK] reconnect failed: {re_err}")
                        elif self.source_type == 'hikvision':
                            # 海康相机：尝试重新连接
                            self._release_hik_camera()
                            time.sleep(0.5)
                            # 重新初始化海康相机
                            try:
                                self.start_hikvision_camera(
                                    device_index=self.hik_device_index,
                                    width=self.width,
                                    height=self.height,
                                    fps=self.fps
                                )
                                print("[Capture] Hikvision camera reconnect ok")
                                consecutive_errors = 0
                            except:
                                print("[Capture] Hikvision camera reconnect failed")
                        else:
                            if self.capture is not None:
                                self.capture.release()
                            if self.source_type == 'rtsp':
                                print("[RTSP] attempting reconnect...")
                                time.sleep(2.0)
                                self.capture = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                                self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                                if self.capture.isOpened():
                                    print("[RTSP] reconnect ok")
                                    consecutive_errors = 0
                                else:
                                    print("[RTSP] reconnect failed")
                            elif self.source_type == 'camera':
                                backend = getattr(self, '_camera_backend', None)
                                if backend is not None:
                                    self.capture = cv2.VideoCapture(self.camera_index, backend)
                                elif platform.system() == "Windows":
                                    self.capture = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
                                else:
                                    self.capture = cv2.VideoCapture(self.camera_index)
                                if self.capture.isOpened():
                                    self.capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M','J','P','G'))
                                    self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                                    self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                                    self.capture.set(cv2.CAP_PROP_FPS, self.fps)
                                    print(f"[Capture] camera reopened ok (backend={backend})")
                                    consecutive_errors = 0
                                else:
                                    print("[Capture] camera reopen failed")
                            elif self.source_type == 'video' and self.video_path:
                                self.capture = cv2.VideoCapture(self.video_path)
                                if self.capture.isOpened():
                                    print("[Capture] video reopened ok")
                                    consecutive_errors = 0
                                else:
                                    print("[Capture] video reopen failed, stopping")
                                    _before_running_ce = self.is_running
                                    _before_detecting_ce = self.is_detecting
                                    self.is_running = False
                                    self._fire_source_status_change(
                                        _before_running_ce, _before_detecting_ce,
                                        "capture_loop_reopen_failed",
                                    )
                    except Exception as recover_error:
                        print(f"[Capture] recovery failed: {recover_error}")
                        _before_running_re = self.is_running
                        _before_detecting_re = self.is_detecting
                        self.is_running = False
                        self._fire_source_status_change(
                            _before_running_re, _before_detecting_re,
                            "capture_loop_recover_failed",
                        )
                
                time.sleep(0.1)  # 错误后短暂等待
        
        print("[Capture] thread stopped")
        # 停止推理线程
        self._stop_inference_thread()
        # 停止录制线程
        self._stop_recording_thread()
