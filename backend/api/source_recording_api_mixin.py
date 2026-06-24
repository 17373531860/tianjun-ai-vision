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
import uuid
import traceback
from datetime import datetime
import cv2

# v2.7.17: mixin 方法运行时使用本模块 globals 解析名字, 不会借宿主模块的 import,
# 之前漏了下面这些 import 导致触发录制时 NameError ("settings is not defined" 等),
# session/cycle/step 三种录制都会偶发掉到 except 里只打"开始XX录制失败".
from backend.core.config import settings, DATA_DIR  # noqa: F401  DATA_DIR 备用
from backend.models.models import (  # noqa: F401
    DetectionSession,
    DetectionCycle,
    StepRecord,
    VideoClip,
)
from backend.api.source_recorder import FFmpegRecorder


def _ensure_dated_dir(base_dir):
    """在 base_dir 下按当天日期建子目录 (YYYY-MM-DD) 并返回其完整路径。

    录像物理上按天分目录存放, 便于按日期定位与清理回收空目录; DB 仍存完整
    file_path, 回放/转码读 file_path 不受影响, 老的平铺录像也照常被扫到。
    建目录失败时回退到 base_dir 本身——绝不因建子目录失败而丢录像。
    """
    day = datetime.now().strftime('%Y-%m-%d')
    dated = os.path.join(base_dir, day)
    try:
        os.makedirs(dated, exist_ok=True)
        return dated
    except Exception:
        return base_dir


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
            filepath = os.path.join(_ensure_dated_dir(settings.SESSION_VIDEO_DIR), filename)
            
            fps = min(self.export_settings.get('video_fps', 30), 25)
            
            width = self.width if self.width > 0 else 1280
            height = self.height if self.height > 0 else 720
            
            writer = FFmpegRecorder(filepath, width, height, fps)
            if not writer.open():
                print("[Recording/Warn] failed to create session video recorder")
                self._append_recording_failure(
                    "session",
                    "open_failed",
                    writer=writer,
                    error=getattr(writer, "last_error", "open_failed"),
                )
                return
            with self._writer_lock:
                self.video_writer = writer
            print(f"[Recording] session video started: {filepath}")
            
            # 记录到数据库 (完整 uuid 防唯一约束撞号; try/finally 兜底关连接防泄漏)
            video_uuid = uuid.uuid4().hex
            db = self._get_db_session()
            try:
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
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        except Exception as e:
            print(f"[Recording] start session recording failed: {e}")
            self._append_recording_failure("session", "open_exception", error=str(e))
    
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
                    print("[Recording] session video stopped (drained)")
                    db = self._get_db_session()
                    try:
                        session = db.query(DetectionSession).filter(DetectionSession.id == sid).first()
                        if session and session.video_path:
                            video = db.query(VideoClip).filter(VideoClip.file_path == session.video_path).first()
                            if video:
                                video.end_time = datetime.now()
                                if os.path.exists(session.video_path):
                                    video.file_size = os.path.getsize(session.video_path)
                                db.commit()
                    except Exception:
                        db.rollback()
                        raise
                    finally:
                        db.close()
                except Exception as e:
                    print(f"[Recording] stop session recording failed: {e}")

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
            filepath = os.path.join(_ensure_dated_dir(settings.CYCLE_VIDEO_DIR), filename)
            
            fps = min(self.export_settings.get('video_fps', 30), 25)
            
            width = self.width if self.width > 0 else 1280
            height = self.height if self.height > 0 else 720
            
            writer = FFmpegRecorder(filepath, width, height, fps)
            if not writer.open():
                print("[Recording/Warn] failed to create cycle video recorder")
                self._append_recording_failure(
                    "cycle",
                    "open_failed",
                    writer=writer,
                    error=getattr(writer, "last_error", "open_failed"),
                )
                return
            with self._writer_lock:
                self.cycle_video_writer = writer
            print(f"[Recording] cycle video started: {filename}")
            
            # 记录到数据库 (完整 uuid 防唯一约束撞号; try/finally 兜底关连接防泄漏)
            video_uuid = uuid.uuid4().hex
            db = self._get_db_session()
            try:
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
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        except Exception as e:
            print(f"[Recording] start cycle recording failed: {e}")
            self._append_recording_failure("cycle", "open_exception", error=str(e))
    
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
                    print("[Recording] cycle video stopped (drained)")
                except Exception as e:
                    print(f"[Recording] stop cycle recording failed: {e}")
                finally:
                    # C6 监控: 释放完成, 存活延迟释放计数 -1
                    with self._writer_lock:
                        self._draining_release_count = max(
                            0, getattr(self, '_draining_release_count', 1) - 1)

            import threading
            # C6 监控版(零风险, 不改释放逻辑): 统计同时存活的延迟释放线程,
            # 每个都持着一个 cv2/FFmpeg writer 子进程, 快节拍周期会叠加。
            # 只在叠加超阈值时告警, 帮现场定位"多个 FFmpeg 子进程堆积"。
            with self._writer_lock:
                self._draining_release_count = getattr(
                    self, '_draining_release_count', 0) + 1
                _draining_n = self._draining_release_count
            if _draining_n > 5:
                print(f"[Recording/Monitor] cycle recording delayed-release backlog {_draining_n} "
                      f"(fast-cadence cycles may pile up FFmpeg subprocesses, channel "
                      f"{getattr(self, 'channel_id', '?')})", flush=True)
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
                    print(f"[Recording/Warn] step recording at limit(1), skipped: {step_label}")
                    return None
                
                video_uuid = uuid.uuid4().hex  # 完整 uuid 防唯一约束撞号
                # 使用 .mp4 格式
                filename = f"step_{step_label}_{video_uuid}_{datetime.now().strftime('%H%M%S')}.mp4"
                filepath = os.path.join(_ensure_dated_dir(settings.STEP_VIDEO_DIR), filename)
                
                fps = min(self.export_settings.get('video_fps', 30), 25)  # 限制FPS
                
                # 确保宽高有效
                width = self.width if self.width > 0 else 1280
                height = self.height if self.height > 0 else 720
                
                # 使用 FFmpegRecorder
                writer = FFmpegRecorder(filepath, width, height, fps)
                if not writer.open():
                    print(f"[Recording/Warn] failed to create step video recorder: {step_label}")
                    self._append_recording_failure(
                        "step",
                        "open_failed",
                        writer=writer,
                        error=f"{step_label}: {getattr(writer, 'last_error', 'open_failed')}",
                    )
                    return None
                    
                self.step_video_writers[step_label] = {
                    'writer': writer,
                    'filepath': filepath,
                    'filename': filename,
                    'video_uuid': video_uuid,
                    'start_time': datetime.now(),
                    'frame_size': (width, height)
                }
                print(f"[Recording] step video started: {step_label} -> {filename}")
                return video_uuid
        except Exception as e:
            print(f"[Recording] start step recording failed: {e}")
            self._append_recording_failure("step", "open_exception", error=f"{step_label}: {e}")
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
            
            # 保存视频信息到数据库 (try/finally 兜底关连接防泄漏)
            db = self._get_db_session()
            try:
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
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
            
            print(f"[Recording] step video stopped: {step_label}")
            return {
                'video_uuid': step_video['video_uuid'],
                'filepath': step_video['filepath']
            }
        except Exception as e:
            print(f"[Recording] stop step recording failed: {e}")
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
        print("[Recording] all recorders closed")
    
