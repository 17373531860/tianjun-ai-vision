"""录像 Mixin —— 从 VideoSourceManager 抽出的 13 个录像相关方法。

为什么独立：
  source.py 原 8000+ 行，VideoSourceManager 是上帝类。录像逻辑（FFmpeg 录像器
  + 录像线程 + session/cycle/step 三级录制 + 队列入帧 + 关闭清理）相对内聚、
  对状态机的依赖较弱（只读 export_settings / writer locks / queue / 当前 session/cycle 标识），
  适合作为第一波 mixin 拆分。

宿主类必须提供的实例属性：
  - self.export_settings: dict | None
  - self.video_writer / self.cycle_video_writer / self.step_video_writers
  - self._writer_lock / self._step_writers_lock (threading.Lock 实例)
  - self._recording_queue (queue.Queue) / self._recording_running / self._recording_thread / self._recording_drop_count
  - self.width / self.height
  - self.current_session_id / self.current_session_uuid
  - self.current_cycle_id / self.current_cycle_uuid

宿主类必须提供的方法：
  - self._get_db_session() -> Session

行为完全等价于原 source.py 中的实现，仅从 class VideoSourceManager 内挪到这里。
覆盖在 test_source_pipeline_e2e.py 的接口契约 + 状态机回归测试范围内。
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime

import cv2

from backend.core.config import settings
from backend.models.models import DetectionCycle, DetectionSession, VideoClip
from backend.api.source_recorder import FFmpegRecorder


class RecordingMixin:
    # ============== 录制线程：start / stop / loop ==============
    def _start_recording_thread(self):
        """启动独立录制线程"""
        if self._recording_thread is not None and self._recording_thread.is_alive():
            return  # 已在运行

        self._recording_running = True
        self._recording_drop_count = 0
        self._recording_thread = threading.Thread(target=self._recording_loop, daemon=True)
        self._recording_thread.start()
        print("[录制线程] 已启动")

    def _stop_recording_thread(self):
        """停止录制线程（线程安全，可被并发调用）"""
        self._recording_running = False

        thread = self._recording_thread
        if thread is not None:
            self._recording_thread = None
            thread.join(timeout=5.0)
            if thread.is_alive():
                print("[警告] 录制线程未能在超时内结束")

        # 安全清空队列残留帧，防止线程卡住时内存泄漏
        dropped = 0
        while not self._recording_queue.empty():
            try:
                self._recording_queue.get_nowait()
                dropped += 1
            except Exception:
                break

        if dropped > 0:
            print(f"[录制线程] 清理了队列中 {dropped} 帧残留数据")

        if self._recording_drop_count > 0:
            print(f"[录制线程] 本次录制共丢弃 {self._recording_drop_count} 帧（队列满）")

        print("[录制线程] 已停止")

    def _recording_loop(self):
        """
        独立录制线程 - 从队列取帧写入 VideoWriter
        与 CUDA/推理完全隔离，避免段错误
        """
        print("[录制线程] 开始运行")
        frame_count = 0
        last_log_time = time.time()
        last_heartbeat_time = time.time()

        while self._recording_running or not self._recording_queue.empty():
            try:
                current_time = time.time()

                # 每10秒打印心跳（调试用）
                if current_time - last_heartbeat_time > 10:
                    queue_size = self._recording_queue.qsize()
                    with self._step_writers_lock:
                        step_count = len(self.step_video_writers)
                    print(f"[录制线程心跳] 帧={frame_count}, 队列={queue_size}, 步骤录制={step_count}, 丢帧={self._recording_drop_count}")
                    last_heartbeat_time = current_time

                # 从队列取帧（带超时，避免阻塞）
                try:
                    frame = self._recording_queue.get(timeout=0.1)
                except Exception:
                    continue  # 队列空或关闭，继续等待

                if frame is None:
                    continue

                # 实际写入 VideoWriter
                self._write_frame_to_writers(frame)
                frame_count += 1

                # 每30秒打印一次详细状态
                if current_time - last_log_time > 30:
                    queue_size = self._recording_queue.qsize()
                    print(f"[录制线程] 已写入 {frame_count} 帧, 队列积压: {queue_size}, 丢帧: {self._recording_drop_count}")
                    last_log_time = current_time

            except Exception as e:
                print(f"[录制线程] 写入错误: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(0.01)

        print(f"[录制线程] 结束运行, 共写入 {frame_count} 帧")

    # ============== 队列入帧 + 实际写入 ==============
    def _enqueue_frame_for_recording(self, frame):
        """
        将帧放入录制队列（非阻塞）
        入队前缩小帧到录制分辨率，大幅减少队列内存占用
        """
        if frame is None or not self._recording_running:
            return

        try:
            max_w, max_h = FFmpegRecorder.MAX_RECORD_WIDTH, FFmpegRecorder.MAX_RECORD_HEIGHT
            h, w = frame.shape[:2]
            if w > max_w or h > max_h:
                scale = min(max_w / w, max_h / h)
                new_w = int(w * scale) // 2 * 2
                new_h = int(h * scale) // 2 * 2
                small_frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
            else:
                small_frame = frame

            try:
                self._recording_queue.put_nowait(small_frame)
            except Exception:
                try:
                    self._recording_queue.get_nowait()
                except Exception:
                    pass
                try:
                    self._recording_queue.put_nowait(small_frame)
                except Exception:
                    pass
                self._recording_drop_count += 1
        except Exception:
            self._recording_drop_count += 1

    def _write_frame_to_writers(self, frame):
        """
        实际写入帧到所有 VideoWriter（在录制线程中调用）
        帧已在入队时缩小到录制分辨率，FFmpegRecorder.write() 内部会
        自行 resize 到各自目标尺寸，这里不再做冗余缩放。
        """
        if frame is None:
            return

        try:
            # 确保帧是 BGR 格式（3通道）
            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # 取 writer 引用时短暂加锁，write() 在锁外执行以防止管道阻塞导致死锁
            with self._writer_lock:
                session_w = self.video_writer
                cycle_w = self.cycle_video_writer
                draining_session_w = getattr(self, '_draining_session_writer', None)
                draining_cycle_w = getattr(self, '_draining_cycle_writer', None)

            if session_w:
                try:
                    if session_w.isOpened():
                        session_w.write(frame)
                except Exception as e:
                    print(f"[录制警告] 写入会话视频失败: {e}")
                    with self._writer_lock:
                        if self.video_writer is session_w:
                            self.video_writer = None
                    try:
                        session_w.release()
                    except Exception:
                        pass

            if cycle_w:
                try:
                    if cycle_w.isOpened():
                        cycle_w.write(frame)
                except Exception as e:
                    print(f"[录制警告] 写入周期视频失败: {e}")
                    with self._writer_lock:
                        if self.cycle_video_writer is cycle_w:
                            self.cycle_video_writer = None
                    try:
                        cycle_w.release()
                    except Exception:
                        pass

            if draining_cycle_w and draining_cycle_w is not cycle_w:
                try:
                    if draining_cycle_w.isOpened():
                        draining_cycle_w.write(frame)
                except Exception:
                    pass

            if draining_session_w and draining_session_w is not session_w:
                try:
                    if draining_session_w.isOpened():
                        draining_session_w.write(frame)
                except Exception:
                    pass

            # 写入所有活动的步骤视频（需要加锁保护）
            failed_steps = []
            with self._step_writers_lock:
                for step_label, step_info in list(self.step_video_writers.items()):
                    try:
                        writer = step_info.get('writer')
                        if writer and writer.isOpened():
                            writer.write(frame)
                    except Exception as e:
                        print(f"[录制警告] 写入步骤视频 {step_label} 失败: {e}")
                        failed_steps.append(step_label)

                # 清理失败的步骤录制器
                for step_label in failed_steps:
                    try:
                        step_info = self.step_video_writers.pop(step_label, None)
                        if step_info and step_info.get('writer'):
                            step_info['writer'].release()
                    except Exception as _e:
                        print(f"[录制线程] 失败步骤 writer.release 失败 step={step_label} (已忽略): {_e}", flush=True)

        except Exception as e:
            print(f"[录制线程] 写入帧异常: {e}")

    # ============== 会话级录制 ==============
    def start_session_recording(self):
        """开始会话视频录制（使用 FFmpeg 进程）"""
        if not self.export_settings or not self.export_settings.get('record_session_video'):
            return

        with self._writer_lock:
            if self.video_writer:
                try:
                    self.video_writer.release()
                except Exception:
                    pass
                self.video_writer = None

        try:
            filename = f"session_{self.current_session_uuid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            filepath = os.path.join(settings.SESSION_VIDEO_DIR, filename)

            fps = min(self.export_settings.get('video_fps', 30), 25)

            width = self.width if self.width > 0 else 1280
            height = self.height if self.height > 0 else 720

            writer = FFmpegRecorder(filepath, width, height, fps)
            if not writer.open():
                print("[录制警告] 无法创建会话视频录制器")
                return
            with self._writer_lock:
                self.video_writer = writer
            print(f"开始录制会话视频: {filepath}")

            # 记录到数据库
            db = self._get_db_session()
            video_uuid = str(uuid.uuid4())[:8]
            video = VideoClip(
                video_uuid=video_uuid,
                clip_type='session',
                related_id=self.current_session_id,
                file_path=filepath,
                file_name=filename,
                start_time=datetime.now()
            )
            db.add(video)
            db.commit()

            # 更新会话的视频ID
            session = db.query(DetectionSession).filter(DetectionSession.id == self.current_session_id).first()
            if session:
                session.video_id = video_uuid
                session.video_path = filepath
                db.commit()

            db.close()
        except Exception as e:
            print(f"开始会话录制失败: {e}")

    def stop_session_recording(self):
        """停止会话视频录制 - delayed release to flush queued frames"""
        session_id = self.current_session_id

        with self._writer_lock:
            writer = self.video_writer
            self.video_writer = None
            if writer:
                self._draining_session_writer = writer

        if writer:
            def _delayed_release(w, sid):
                time.sleep(1.5)
                with self._writer_lock:
                    if getattr(self, '_draining_session_writer', None) is w:
                        self._draining_session_writer = None
                try:
                    w.release()
                    print("会话视频录制已停止(排空释放)")
                    db = self._get_db_session()
                    session = db.query(DetectionSession).filter(DetectionSession.id == sid).first()
                    if session and session.video_path:
                        video = db.query(VideoClip).filter(VideoClip.file_path == session.video_path).first()
                        if video:
                            video.end_time = datetime.now()
                            if os.path.exists(session.video_path):
                                video.file_size = os.path.getsize(session.video_path)
                            db.commit()
                    db.close()
                except Exception as e:
                    print(f"停止会话录制失败: {e}")

            threading.Thread(target=_delayed_release, args=(writer, session_id), daemon=True).start()

    # ============== 周期级录制 ==============
    def start_cycle_recording(self):
        """开始周期视频录制（使用 FFmpeg 进程）"""
        if not self.export_settings or not self.export_settings.get('record_cycle_video'):
            return

        with self._writer_lock:
            if self.cycle_video_writer:
                try:
                    self.cycle_video_writer.release()
                except Exception:
                    pass
                self.cycle_video_writer = None

        try:
            filename = f"cycle_{self.current_cycle_uuid}_{datetime.now().strftime('%H%M%S')}.mp4"
            filepath = os.path.join(settings.CYCLE_VIDEO_DIR, filename)

            fps = min(self.export_settings.get('video_fps', 30), 25)

            width = self.width if self.width > 0 else 1280
            height = self.height if self.height > 0 else 720

            writer = FFmpegRecorder(filepath, width, height, fps)
            if not writer.open():
                print("[录制警告] 无法创建周期视频录制器")
                return
            with self._writer_lock:
                self.cycle_video_writer = writer
            print(f"开始录制周期视频: {filename}")

            # 记录到数据库
            db = self._get_db_session()
            video_uuid = str(uuid.uuid4())[:8]
            video = VideoClip(
                video_uuid=video_uuid,
                clip_type='cycle',
                related_id=self.current_cycle_id,
                file_path=filepath,
                file_name=filename,
                start_time=datetime.now()
            )
            db.add(video)
            db.commit()

            # 更新周期的视频ID
            cycle = db.query(DetectionCycle).filter(DetectionCycle.id == self.current_cycle_id).first()
            if cycle:
                cycle.video_id = video_uuid
                cycle.video_path = filepath
                db.commit()

            db.close()
        except Exception as e:
            print(f"开始周期录制失败: {e}")

    def stop_cycle_recording(self):
        """停止周期视频录制 - delayed release to flush queued frames"""
        with self._writer_lock:
            writer = self.cycle_video_writer
            self.cycle_video_writer = None
            if writer:
                self._draining_cycle_writer = writer

        if writer:
            def _delayed_release(w):
                time.sleep(1.5)
                with self._writer_lock:
                    if getattr(self, '_draining_cycle_writer', None) is w:
                        self._draining_cycle_writer = None
                try:
                    w.release()
                    print("周期视频录制已停止(排空释放)")
                except Exception as e:
                    print(f"停止周期录制失败: {e}")

            threading.Thread(target=_delayed_release, args=(writer,), daemon=True).start()

    # ============== 步骤级录制 ==============
    def start_step_recording(self, step_label: str):
        """开始步骤视频录制（使用 FFmpeg 进程）"""
        if not self.export_settings or not self.export_settings.get('record_step_video'):
            return None

        try:
            with self._step_writers_lock:
                # 先关闭同标签的旧 writer，防止 FFmpeg 进程泄漏
                if step_label in self.step_video_writers:
                    old_info = self.step_video_writers.pop(step_label, None)
                    if old_info and old_info.get('writer'):
                        try:
                            old_info['writer'].release()
                        except Exception as _e:
                            print(f"[录制] 旧 writer.release 失败 step={step_label} (已忽略): {_e}", flush=True)

                if len(self.step_video_writers) >= 1:
                    print(f"[录制警告] 步骤视频录制已达上限(1)，跳过: {step_label}")
                    return None

                video_uuid = str(uuid.uuid4())[:8]
                filename = f"step_{step_label}_{video_uuid}_{datetime.now().strftime('%H%M%S')}.mp4"
                filepath = os.path.join(settings.STEP_VIDEO_DIR, filename)

                fps = min(self.export_settings.get('video_fps', 30), 25)  # 限制FPS

                width = self.width if self.width > 0 else 1280
                height = self.height if self.height > 0 else 720

                writer = FFmpegRecorder(filepath, width, height, fps)
                if not writer.open():
                    print(f"[录制警告] 无法创建步骤视频录制器: {step_label}")
                    return None

                self.step_video_writers[step_label] = {
                    'writer': writer,
                    'filepath': filepath,
                    'filename': filename,
                    'video_uuid': video_uuid,
                    'start_time': datetime.now(),
                    'frame_size': (width, height)
                }
                print(f"[调试] 开始录制步骤视频: {step_label} -> {filename}")
                return video_uuid
        except Exception as e:
            print(f"开始步骤录制失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def stop_step_recording(self, step_label: str) -> dict:
        """停止步骤视频录制，返回视频信息"""
        step_video = None

        # 在锁内获取并移除 writer
        with self._step_writers_lock:
            if step_label not in self.step_video_writers:
                return None
            step_video = self.step_video_writers.pop(step_label)

        # 在锁外释放 writer 和写数据库（避免长时间持锁）
        try:
            writer = step_video.get('writer')
            if writer:
                writer.release()

            # 保存视频信息到数据库
            db = self._get_db_session()
            video = VideoClip(
                video_uuid=step_video['video_uuid'],
                clip_type='step',
                related_id=self.current_cycle_id,
                file_path=step_video['filepath'],
                file_name=step_video['filename'],
                start_time=step_video['start_time'],
                end_time=datetime.now()
            )
            db.add(video)
            db.commit()
            db.close()

            print(f"[调试] 步骤视频录制已停止: {step_label}")
            return {
                'video_uuid': step_video['video_uuid'],
                'filepath': step_video['filepath']
            }
        except Exception as e:
            print(f"停止步骤录制失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    # ============== 公共入口 + 全部清理 ==============
    def write_frame_to_recorders(self, frame):
        """
        将帧写入录制队列（向后兼容方法）
        实际写入由独立的录制线程处理，避免与 CUDA 冲突
        """
        self._enqueue_frame_for_recording(frame)

    def _close_all_writers(self):
        """关闭所有 FFmpeg 录制进程，防止资源泄漏"""
        with self._writer_lock:
            if self.video_writer:
                try:
                    self.video_writer.release()
                except Exception:
                    pass
                self.video_writer = None
            if self.cycle_video_writer:
                try:
                    self.cycle_video_writer.release()
                except Exception:
                    pass
                self.cycle_video_writer = None
        with self._step_writers_lock:
            for step_label, step_info in list(self.step_video_writers.items()):
                try:
                    writer = step_info.get('writer')
                    if writer:
                        writer.release()
                except Exception as _e:
                    print(f"[资源清理] writer.release 失败 step={step_label} (已忽略): {_e}", flush=True)
            self.step_video_writers.clear()
        print("[资源清理] 所有录制器已关闭")
