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
import traceback
import cv2


class RecordingThreadMixin:
    # ========== 录制线程相关方法（独立于 CUDA，避免段错误） ==========
    
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
                    print(f"[录制线程] 已写入 {frame_count} 帧, 队列积压: {queue_size}, 丢帧: {self._recording_drop_count}")
                    last_log_time = current_time
                    
            except Exception as e:
                print(f"[录制线程] 写入错误: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(0.01)
        
        print(f"[录制线程] 结束运行, 共写入 {frame_count} 帧")
    
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
                    except:
                        pass
                    
        except Exception as e:
            print(f"[录制线程] 写入帧异常: {e}")
    
    
