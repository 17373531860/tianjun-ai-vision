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
# v3.54 自定义录像存储位置: 三个开录点改为动态解析当前生效目录 (实时生效)
from backend.services.recording_storage import get_video_dirs

# v3.54 长录像治理: 会话录像按时长分段 (默认 1 小时/段)。
# 24h 连续录像单文件几 GB, 回放加载慢且单点故障域太大; 分段后每段独立
# VideoClip 落库、独立可播, 清理/统计按既有 related_id 查询天然兼容。
# 测试用环境变量加速 (如 30s/段), 生产不配置即 3600s, 无 UI 无配置面。
SESSION_SEGMENT_SECONDS = max(
    10, int(os.environ.get("TJ_SESSION_SEGMENT_SECONDS", "3600") or 3600))


def _finalize_session_clip_row(filepath: str, retries: int = 3):
    """按 file_path 补齐分段 VideoClip 的 end_time/file_size。

    行由 _persist 队列异步创建, 可能晚于本调用, 带小重试。
    """
    from backend.db.database import SessionLocal
    for i in range(retries):
        db = SessionLocal()
        try:
            video = db.query(VideoClip).filter(
                VideoClip.file_path == filepath).first()
            if video:
                video.end_time = datetime.now()
                if os.path.exists(filepath):
                    video.file_size = os.path.getsize(filepath)
                db.commit()
                return True
        except Exception:
            db.rollback()
        finally:
            db.close()
        time.sleep(1.0 * (i + 1))
    return False


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
            filepath = os.path.join(_ensure_dated_dir(get_video_dirs()["sessions"]), filename)
            
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
            # v3.54 分段状态: 到点由录制线程调 _maybe_rotate_session_recording 换段
            self._session_seg_index = 1
            self._session_seg_deadline = time.time() + SESSION_SEGMENT_SECONDS
            self._session_rec_params = (width, height, fps)
            print(f"[Recording] session video started: {filepath} "
                  f"(segment 1, {SESSION_SEGMENT_SECONDS}s/段)")
            
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
        self._session_seg_deadline = None  # v3.54: 停录即停分段轮转

        with self._writer_lock:
            writer = self.video_writer
            self.video_writer = None
            if writer:
                self._draining_session_writer = writer

        if writer:
            def _delayed_release(w):
                time.sleep(1.5)
                with self._writer_lock:
                    if getattr(self, '_draining_session_writer', None) is w:
                        self._draining_session_writer = None
                try:
                    w.release()
                    print("[Recording] session video stopped (drained)")
                    # v3.54: 按 writer 自己的 file_path 定位 VideoClip 行——
                    # 分段后 session.video_path 指首段, 最后在录的是末段
                    _finalize_session_clip_row(w.filepath)
                except Exception as e:
                    print(f"[Recording] stop session recording failed: {e}")

            import threading
            threading.Thread(target=_delayed_release, args=(writer,), daemon=True).start()

    def _maybe_rotate_session_recording(self):
        """会话录像到点换段 (仅录制线程调用; 无会话录像时零开销)。"""
        deadline = getattr(self, '_session_seg_deadline', None)
        if not deadline or time.time() < deadline:
            return
        self._rotate_session_recording()

    def _rotate_session_recording(self):
        """开新段 → 原子换 writer → 老段延迟释放收尾 + 新段 VideoClip 落库。

        v3.54 长录像治理。运行在录制线程内: 新 writer open (~百 ms 级) 期间
        帧在录制队列排队, 不丢帧; 换段瞬间老段进 draining 通道继续收 1.5s
        (与停录一致的排空语义, 两段之间宁重叠不缺帧)。
        开新段失败 → 老 writer 继续录 + 60s 后重试, 绝不让录像中断。
        """
        width, height, fps = getattr(
            self, '_session_rec_params',
            (self.width if self.width > 0 else 1280,
             self.height if self.height > 0 else 720, 25))
        seg_idx = getattr(self, '_session_seg_index', 1) + 1

        filename = (f"session_{self.current_session_uuid}_"
                    f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_p{seg_idx:03d}.mp4")
        # get_video_dirs 每段现解析: 中途改自定义存储位置, 下一段即落新目录
        filepath = os.path.join(
            _ensure_dated_dir(get_video_dirs()["sessions"]), filename)

        new_writer = FFmpegRecorder(filepath, width, height, fps)
        if not new_writer.open():
            print(f"[Recording/Warn] 会话分段开新段失败, 老段续录, 60s 后重试: "
                  f"{getattr(new_writer, 'last_error', '')}")
            self._append_recording_failure(
                "session", "rotate_open_failed", writer=new_writer,
                error=getattr(new_writer, "last_error", "rotate_open_failed"))
            self._session_seg_deadline = time.time() + 60
            return

        with self._writer_lock:
            old_writer = self.video_writer
            self.video_writer = new_writer
            if old_writer:
                self._draining_session_writer = old_writer

        self._session_seg_index = seg_idx
        self._session_seg_deadline = time.time() + SESSION_SEGMENT_SECONDS
        print(f"[Recording] session video rotated -> segment {seg_idx}: {filename}")

        # 老段: 排空 1.5s 后释放 (内部异步 remux 成 faststart) + 补 end_time/size
        if old_writer:
            def _drain_old(w):
                time.sleep(1.5)
                with self._writer_lock:
                    if getattr(self, '_draining_session_writer', None) is w:
                        self._draining_session_writer = None
                try:
                    w.release()
                    _finalize_session_clip_row(w.filepath)
                    print(f"[Recording] session segment sealed: "
                          f"{os.path.basename(w.filepath)}")
                except Exception as e:
                    print(f"[Recording] seal session segment failed: {e}")

            import threading
            threading.Thread(target=_drain_old, args=(old_writer,),
                             daemon=True).start()

        # 新段 VideoClip 落库 (走本通道落库线程, 不阻塞录制线程;
        # session.video_id/video_path 保持指首段, 分段列表按 related_id 查)
        video_uuid = uuid.uuid4().hex
        _sid = self.current_session_id
        _start_dt = datetime.now()

        def _persist_new_segment():
            from backend.db.database import SessionLocal
            db = SessionLocal()
            try:
                db.add(VideoClip(
                    video_uuid=video_uuid,
                    clip_type='session',
                    related_id=_sid,
                    file_path=filepath,
                    file_name=filename,
                    start_time=_start_dt,
                ))
                db.commit()
            finally:
                db.close()

        self._persist.submit(f"session_seg#{video_uuid[:8]}", _persist_new_segment)
    
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
            filepath = os.path.join(_ensure_dated_dir(get_video_dirs()["cycles"]), filename)
            
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
            
            # 记录到数据库 (完整 uuid 防唯一约束撞号)。
            # v3.38 RFC: 元数据写库进本通道落库线程 — 周期行由 cycle_start 作业建,
            # FIFO 保证本作业执行时行已存在; 行定位用 cycle_uuid (id 可能未回填)。
            video_uuid = uuid.uuid4().hex
            _cycle_uuid = getattr(self, 'current_cycle_uuid', None)
            _start_dt = datetime.now()

            # v3.53 录像归档: 把周期身份钉在 writer 上, 停止时的延迟释放线程
            # 在 release() 成功后凭它入队归档任务 (那时 self.current_cycle_uuid
            # 可能已经翻到下一个周期, 不能现取)。
            writer._archive_meta = {
                'cycle_uuid': _cycle_uuid,
                'channel_id': getattr(self, 'channel_id', 0),
            }

            def _persist_cycle_video_meta():
                from backend.db.database import SessionLocal
                db = SessionLocal()
                try:
                    cycle = db.query(DetectionCycle).filter(
                        DetectionCycle.cycle_uuid == _cycle_uuid).first() if _cycle_uuid else None
                    video = VideoClip(
                        video_uuid=video_uuid,
                        clip_type='cycle',
                        related_id=cycle.id if cycle else None,
                        file_path=filepath,
                        file_name=filename,
                        start_time=_start_dt
                    )
                    db.add(video)
                    # 更新周期的视频ID
                    if cycle:
                        cycle.video_id = video_uuid
                        cycle.video_path = filepath
                    db.commit()
                finally:
                    db.close()

            self._persist.submit(f"cycle_video#{video_uuid[:8]}", _persist_cycle_video_meta)
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
                    # v3.53 录像归档: release 返回 = FFmpeg 子进程已退出,
                    # 这是"文件完整可搬运"的唯一可靠信号点。只投递不阻塞
                    # (无启用规则时 notify 内部直接短路, 零开销)。
                    try:
                        meta = getattr(w, '_archive_meta', None)
                        if meta and meta.get('cycle_uuid'):
                            from backend.services.video_archive import (
                                notify_cycle_video_ready)
                            notify_cycle_video_ready(
                                meta.get('channel_id', 0),
                                meta['cycle_uuid'], w.filepath)
                    except Exception as _ae:
                        print(f"[VideoArchive] 归档任务入队失败(不影响录像): {_ae}")
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
                filepath = os.path.join(_ensure_dated_dir(get_video_dirs()["steps"]), filename)
                
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
            
            # 保存视频信息到数据库。
            # v3.38 RFC: 进本通道落库线程 (推理线程不再等写锁); 归属周期用
            # cycle_uuid 在作业内解析 (id 可能未回填)。
            _cycle_uuid = getattr(self, 'current_cycle_uuid', None)
            _vinfo = dict(step_video)
            _end_dt = datetime.now()

            def _persist_step_video_meta():
                from backend.db.database import SessionLocal
                db = SessionLocal()
                try:
                    cycle_row = db.query(DetectionCycle.id).filter(
                        DetectionCycle.cycle_uuid == _cycle_uuid).first() if _cycle_uuid else None
                    video = VideoClip(
                        video_uuid=_vinfo['video_uuid'],
                        clip_type='step',
                        related_id=cycle_row.id if cycle_row else None,
                        file_path=_vinfo['filepath'],
                        file_name=_vinfo['filename'],
                        start_time=_vinfo['start_time'],
                        end_time=_end_dt
                    )
                    db.add(video)
                    db.commit()
                finally:
                    db.close()

            self._persist.submit(f"step_video#{_vinfo['video_uuid'][:8]}", _persist_step_video_meta)
            
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
        self._session_seg_deadline = None  # v3.54: 总清理时停分段轮转
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
    
