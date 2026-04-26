"""录制公共 API (start/stop session/cycle/step recording, v2.7.16 P6 阶段一第十四刀)。

把 8 个录制 API 集中到一处 (合计 ~295 行), 涵盖三个粒度的录像:
  start_session_recording / stop_session_recording  : 会话级 (一次开机)
  start_cycle_recording   / stop_cycle_recording    : 周期级 (单次产品)
  start_step_recording    / stop_step_recording     : 步骤级 (单步动作)
  write_frame_to_recorders                          : 给录制线程的入口
  _close_all_writers                                : 总清理

依赖宿主 (VideoSourceManager):
  - 状态: session_writer / cycle_writer / step_video_writers / current_session_id /
          current_cycle_id / video_codec / video_fps / etc.
"""
import os
import time
import traceback
import cv2


class RecordingApiMixin:
    # ========== 视频录制功能 ==========
    
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
                print(f"[录制警告] 无法创建会话视频录制器")
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

            import threading
            threading.Thread(target=_delayed_release, args=(writer, session_id), daemon=True).start()
    
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
                print(f"[录制警告] 无法创建周期视频录制器")
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

            import threading
            threading.Thread(target=_delayed_release, args=(writer,), daemon=True).start()
    
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
                        except:
                            pass
                
                if len(self.step_video_writers) >= 1:
                    print(f"[录制警告] 步骤视频录制已达上限(1)，跳过: {step_label}")
                    return None
                
                video_uuid = str(uuid.uuid4())[:8]
                # 使用 .mp4 格式
                filename = f"step_{step_label}_{video_uuid}_{datetime.now().strftime('%H%M%S')}.mp4"
                filepath = os.path.join(settings.STEP_VIDEO_DIR, filename)
                
                fps = min(self.export_settings.get('video_fps', 30), 25)  # 限制FPS
                
                # 确保宽高有效
                width = self.width if self.width > 0 else 1280
                height = self.height if self.height > 0 else 720
                
                # 使用 FFmpegRecorder
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
                except:
                    pass
            self.step_video_writers.clear()
        print("[资源清理] 所有录制器已关闭")
    
