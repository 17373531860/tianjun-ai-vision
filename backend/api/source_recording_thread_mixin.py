"""录制线程 (recording thread) Mixin (v2.7.16 P6 阶段一第十二刀)。

把 5 个录制线程相关方法集中到一处 (合计 ~210 行), 解耦 main 推理线程
与录像 IO, 避免 CUDA 段错误:
  _start_recording_thread       : 启动独立录制 worker (11L)
  _stop_recording_thread        : 优雅停止 + drain queue (28L)
  _recording_loop               : worker 主循环 (50L)
  _enqueue_frame_for_recording  : 把帧入队, 含丢帧统计 (34L)
  _write_frame_to_writers       : 实际写入 session/cycle/step 的 cv2.VideoWriter (90L)

依赖宿主 (VideoSourceManager):
  - 状态: _recording_thread / _recording_running / _recording_queue /
          _recording_drop_count / session_writer / cycle_writer / step_writer
  - 方法: debug_log
"""
import time
import queue
import threading
import traceback
import cv2

from backend.api.source_recorder import FFmpegRecorder


class RecordingThreadMixin:
    # ========== 录制线程相关方法（独立于 CUDA，避免段错误） ==========

    def _append_recording_failure(self, recorder_type: str, reason: str,
                                  writer=None, error: str = ""):
        """统一记录录像通道失效事件，供前端二级详情查看。"""
        lock = getattr(self, "_recording_failure_lock", None)
        failures = getattr(self, "recording_failures", None)
        if lock is None or failures is None:
            return
        event = {
            "timestamp": time.time(),
            "channel_id": getattr(self, "channel_id", 0),
            "recorder_type": recorder_type,   # session / cycle / step
            "reason": reason,                 # open_failed / write_failed / write_exception
            "error": error or "",
            "file_path": getattr(writer, "filepath", None) if writer else None,
            "frame_count": getattr(writer, "_frame_count", None) if writer else None,
        }
        with lock:
            failures.append(event)
            if len(failures) > 30:
                del failures[:-30]

    def get_recording_failures(self, limit: int = 20):
        lock = getattr(self, "_recording_failure_lock", None)
        failures = getattr(self, "recording_failures", None)
        if lock is None or failures is None:
            return []
        with lock:
            if limit <= 0:
                return []
            return list(failures[-limit:])

    def clear_recording_failures(self):
        lock = getattr(self, "_recording_failure_lock", None)
        failures = getattr(self, "recording_failures", None)
        if lock is None or failures is None:
            return 0
        with lock:
            count = len(failures)
            failures.clear()
            return count
    
    def _start_recording_thread(self):
        """启动独立录制线程"""
        if self._recording_thread is not None and self._recording_thread.is_alive():
            return  # 已在运行
        
        self._recording_running = True
        self._recording_drop_count = 0
        self._recording_thread = threading.Thread(target=self._recording_loop, daemon=True)
        self._recording_thread.start()
        print("[RecThread] started")
    
    def _stop_recording_thread(self):
        """停止录制线程（线程安全，可被并发调用）"""
        self._recording_running = False
        
        thread = self._recording_thread
        if thread is not None:
            self._recording_thread = None
            thread.join(timeout=5.0)
            if thread.is_alive():
                print("[RecThread/Warn] thread did not finish within timeout")
        
        # 安全清空队列残留帧，防止线程卡住时内存泄漏
        dropped = 0
        while not self._recording_queue.empty():
            try:
                self._recording_queue.get_nowait()
                dropped += 1
            except Exception:
                break
        
        if dropped > 0:
            print(f"[RecThread] cleared {dropped} leftover frames in queue")
        
        if self._recording_drop_count > 0:
            print(f"[RecThread] dropped {self._recording_drop_count} frames total (queue full)")
        
        print("[RecThread] stopped")
    
    def _recording_loop(self):
        """
        独立录制线程 - 从队列取帧写入 VideoWriter
        与 CUDA/推理完全隔离，避免段错误
        """
        print("[RecThread] run loop started")
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
                    print(f"[RecThread/Heartbeat] frames={frame_count}, queue={queue_size}, step_rec={step_count}, dropped={self._recording_drop_count}")
                    last_heartbeat_time = current_time
                
                # 从队列取帧（带超时，避免阻塞）
                try:
                    frame = self._recording_queue.get(timeout=0.1)
                except:
                    # 队列空，继续等待
                    continue
                
                if frame is None:
                    continue
                
                # 实际写入 VideoWriter
                self._write_frame_to_writers(frame)
                frame_count += 1
                
                # 每30秒打印一次详细状态
                if current_time - last_log_time > 30:
                    queue_size = self._recording_queue.qsize()
                    print(f"[RecThread] written {frame_count} frames, queue_backlog={queue_size}, dropped={self._recording_drop_count}")
                    last_log_time = current_time
                    
            except Exception as e:
                print(f"[RecThread] write error: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(0.01)
        
        print(f"[RecThread] run loop ended, {frame_count} frames written total")
    
    def _enqueue_frame_for_recording(self, frame):
        """
        将帧放入录制队列（非阻塞）
        入队前缩小帧到录制分辨率，大幅减少队列内存占用
        """
        if frame is None or not self._recording_running:
            return

        # v3.1.2 防御: 程序性错误必须暴露, 否则会像 v3.1.1 那样 import 漏了导致全程 0 帧静默丢帧.
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
        except Exception as e:
            if not getattr(self, "_recording_prep_warned", False):
                print(f"[RecThread] frame preprocess failed (won't repeat): {type(e).__name__}: {e}")
                self._recording_prep_warned = True
            self._recording_drop_count += 1
            return

        try:
            self._recording_queue.put_nowait(small_frame)
        except queue.Full:
            try:
                self._recording_queue.get_nowait()
            except Exception:
                pass
            try:
                self._recording_queue.put_nowait(small_frame)
            except Exception:
                pass
            self._recording_drop_count += 1
        except Exception as e:
            if not getattr(self, "_recording_queue_warned", False):
                print(f"[RecThread] enqueue error (won't repeat): {type(e).__name__}: {e}")
                self._recording_queue_warned = True
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
            
            # v3.54: 会话录像到点换段 (廉价时间比较, 只在录制线程做,
            # 换段失败不影响本帧写入——老 writer 仍在位)
            try:
                self._maybe_rotate_session_recording()
            except Exception as e:
                print(f"[RecThread/Warn] session segment rotate failed: {e}")

            # 取 writer 引用时短暂加锁，write() 在锁外执行以防止管道阻塞导致死锁
            with self._writer_lock:
                session_w = self.video_writer
                cycle_w = self.cycle_video_writer
                draining_session_w = getattr(self, '_draining_session_writer', None)
                draining_cycle_w = getattr(self, '_draining_cycle_writer', None)
            
            if session_w:
                try:
                    if session_w.isOpened():
                        ok = session_w.write(frame)
                        if not ok:
                            raise RuntimeError(getattr(session_w, "last_error", "write_failed"))
                except Exception as e:
                    print(f"[RecThread/Warn] write session video failed: {e}")
                    self._append_recording_failure("session", "write_failed", writer=session_w, error=str(e))
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
                        ok = cycle_w.write(frame)
                        if not ok:
                            raise RuntimeError(getattr(cycle_w, "last_error", "write_failed"))
                except Exception as e:
                    print(f"[RecThread/Warn] write cycle video failed: {e}")
                    self._append_recording_failure("cycle", "write_failed", writer=cycle_w, error=str(e))
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
                            ok = writer.write(frame)
                            if not ok:
                                raise RuntimeError(getattr(writer, "last_error", "write_failed"))
                    except Exception as e:
                        print(f"[RecThread/Warn] write step video {step_label} failed: {e}")
                        self._append_recording_failure(
                            "step",
                            "write_failed",
                            writer=step_info.get('writer'),
                            error=f"{step_label}: {e}",
                        )
                        failed_steps.append(step_label)
                
                # 清理失败的步骤录制器
                for step_label in failed_steps:
                    try:
                        step_info = self.step_video_writers.pop(step_label, None)
                        if step_info and step_info.get('writer'):
                            step_info['writer'].release()
                    except:
                        pass
                    
        except Exception as e:
            print(f"[RecThread] write frame error: {e}")
    
    
