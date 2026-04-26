"""Session/Cycle 生命周期 + 步骤记录 (v2.7.16 P6 阶段一第十一刀)。

包含 16 个方法 (合计 ~620 行) , 涵盖一次检测会话从 start_session →
start_cycle → record_step → end_cycle → end_session 的完整数据持久化:
  _get_db_session, _load_export_settings, _ensure_session_active,
  start_session, end_session,
  _get_counter_file, _persist_counters, _get_current_shift, _auto_split_session,
  _force_timeout_ng,
  start_cycle, end_cycle, _discard_empty_cycle, _reconcile_step_records,
  record_step

依赖宿主 (VideoSourceManager):
  - 状态: current_session_id / current_cycle_id / current_cycle_steps /
          stats / counters / project_id / source_type / 等
  - 方法: _broadcast_* / debug_log / 等

依赖 import (引入到 source.py 的): SessionLocal, DetectionSession,
  DetectionCycle, StepRecord, EventRecord, models 等. 这些已在 source.py
  顶部 import; mixin 引用 self.* 时不需要再 import.
"""
import os
import time
import uuid
import traceback
from typing import Optional
from datetime import datetime, timedelta

from backend.db.database import SessionLocal
from backend.models.models import DetectionSession, DetectionCycle, StepRecord
from sqlalchemy import func


class SessionLifecycleMixin:
    def _get_db_session(self):
        """获取数据库会话"""
        return SessionLocal()
    
    def _load_export_settings(self):
        """加载导出设置"""
        try:
            db = self._get_db_session()
            setting = db.query(DataExportSetting).first()
            if not setting:
                setting = DataExportSetting()
                db.add(setting)
                db.commit()
                db.refresh(setting)
            self.export_settings = {
                'record_step_duration': setting.record_step_duration,
                'record_step_interval': setting.record_step_interval,
                'record_cycle_duration': setting.record_cycle_duration,
                'record_counters': setting.record_counters,
                'record_step_video': setting.record_step_video,
                'record_cycle_video': setting.record_cycle_video,
                'record_session_video': setting.record_session_video,
                'video_quality': setting.video_quality,
                'video_fps': setting.video_fps
            }
            db.close()
        except Exception as e:
            print(f"加载导出设置失败: {e}")
            self.export_settings = {
                'record_step_duration': True,
                'record_step_interval': True,
                'record_cycle_duration': True,
                'record_counters': True,
                'record_step_video': False,
                'record_cycle_video': False,
                'record_session_video': False,
                'video_quality': 'medium',
                'video_fps': 30
            }
    
    def _ensure_session_active(self):
        """If a project is loaded but no session is recording, auto-create one."""
        if self.recording_enabled and self.current_session_id:
            return
        project_id = self.project_config.get('id') if self.project_config else None
        if not project_id:
            return
        print(f"[自动会话] 检测到项目已加载但无活跃会话，自动创建会话 (project_id={project_id})")
        self.start_session(project_id)

    def start_session(self, project_id: int) -> dict:
        """开始新的检测会话"""
        try:
            db = self._get_db_session()
            session_uuid = str(uuid.uuid4())[:8]
            current_shift = self._get_current_shift()
            from backend.api.operators import get_current_operator_id
            current_op_id = get_current_operator_id(self.channel_id)
            session = DetectionSession(
                session_uuid=session_uuid,
                project_id=project_id,
                start_time=datetime.now(),
                status="running",
                channel_id=self.channel_id,
                shift_label=current_shift,
                operator_id=current_op_id,
            )
            db.add(session)
            db.commit()
            db.refresh(session)
            
            self.current_session_id = session.id
            self.current_session_uuid = session_uuid
            self.current_cycle_number = 0
            self.recording_enabled = True
            self._session_start_date = datetime.now().date()
            self._session_start_shift = current_shift
            
            # 加载导出设置
            self._load_export_settings()
            
            db.close()
            print(f"检测会话已创建: {session_uuid}")
            
            # MES Hook: Session 开始
            if self._mes_hook:
                try:
                    project_id = self.project_config.get('id') if self.project_config else None
                    if project_id:
                        self._mes_hook.on_session_start(
                            channel_id=self.channel_id,
                            session_id=session.id,
                            project_id=project_id,
                        )
                except Exception as e:
                    print(f"[MES] session_start hook 异常: {e}")
            
            # 启动录制线程（独立于 CUDA）
            if self.is_detecting:
                self._start_recording_thread()
            
            # 开始会话视频录制
            self.start_session_recording()
            
            return {"session_id": session.id, "session_uuid": session_uuid}
        except Exception as e:
            print(f"创建会话失败: {e}")
            return None
    
    def end_session(self):
        """结束当前检测会话"""
        if not self.current_session_id:
            print("end_session: 没有活动的会话")
            return
        
        session_id = self.current_session_id
        session_uuid = self.current_session_uuid
        print(f"end_session: 正在结束会话 {session_uuid} (ID: {session_id})")
        
        # Discard any open (unsettled) cycle before closing the session
        if self.current_cycle_id:
            print(f"end_session: discarding unsettled cycle #{self.current_cycle_number} (id={self.current_cycle_id})")
            self._discard_empty_cycle()
        
        try:
            db = self._get_db_session()
            session = db.query(DetectionSession).filter(
                DetectionSession.id == session_id
            ).first()
            
            if session:
                session.end_time = datetime.now()
                session.status = "completed"
                
                # 计算统计 — only count properly settled cycles (end_time is not None)
                cycles = db.query(DetectionCycle).filter(
                    DetectionCycle.session_id == session_id,
                    DetectionCycle.end_time != None
                ).all()
                
                print(f"end_session: 找到 {len(cycles)} 个已结算周期")
                
                if cycles:
                    session.total_cycles = len(cycles)
                    session.good_cycles = len([c for c in cycles if c.is_good])
                    session.ng_cycles = session.total_cycles - session.good_cycles
                    
                    durations = [c.duration for c in cycles if c.duration]
                    if durations:
                        session.avg_cycle_time = sum(durations) / len(durations)
                        session.min_cycle_time = min(durations)
                        session.max_cycle_time = max(durations)
                
                # Clean up any remaining orphan cycles (end_time is NULL)
                orphans = db.query(DetectionCycle).filter(
                    DetectionCycle.session_id == session_id,
                    DetectionCycle.end_time == None
                ).all()
                if orphans:
                    orphan_ids = [o.id for o in orphans]
                    print(f"end_session: removing {len(orphans)} orphan cycle(s): {orphan_ids}")
                    db.query(StepRecord).filter(StepRecord.cycle_id.in_(orphan_ids)).delete(synchronize_session=False)
                    db.query(DetectionCycle).filter(DetectionCycle.id.in_(orphan_ids)).delete(synchronize_session=False)
                
                # 保存计数器快照
                session.counters_snapshot = self.counters.copy() if self.counters else {}
                
                print(f"end_session: 保存数据 - 周期数: {session.total_cycles}, 合格: {session.good_cycles}, 不良: {session.ng_cycles}, 计数器: {session.counters_snapshot}")
                
                db.commit()
                print(f"会话已结束: {session_uuid}, 周期数: {session.total_cycles}")
            else:
                print(f"end_session: 未找到会话 ID={session_id}")
            
            db.close()
            
            # MES Hook: Session 结束
            if self._mes_hook:
                try:
                    self._mes_hook.on_session_end(
                        channel_id=self.channel_id,
                        session_id=session_id,
                    )
                except Exception as e:
                    print(f"[MES] session_end hook 异常: {e}")
        except Exception as e:
            print(f"结束会话失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._persist_counters()
            # 停止视频录制
            self.stop_session_recording()
            self.stop_cycle_recording()
            
            self.current_session_id = None
            self.current_session_uuid = None
            self.recording_enabled = False
            self._session_start_date = None
            self._session_start_shift = None
    
    # _get_counter_file / _persist_counters 已迁至 source_counters.py (P7 第三刀)
    # 历史调用 self._persist_counters() 通过 VSM.__getattr__ 转发到 counters_mgr

    def _get_current_shift(self) -> Optional[str]:
        """Return 'day' or 'night' based on current time and project data_config.
        Returns None when shift splitting is disabled."""
        if not self.project_config:
            return None
        data_cfg = self.project_config.get('data_config') or {}
        if not data_cfg.get('shift_split_enabled'):
            return None
        day_start = data_cfg.get('day_shift_start', '08:00')
        night_start = data_cfg.get('night_shift_start', '20:00')
        now_str = datetime.now().strftime('%H:%M')
        if day_start <= night_start:
            return 'day' if day_start <= now_str < night_start else 'night'
        else:
            return 'night' if night_start <= now_str < day_start else 'day'

    def _auto_split_session(self, reason: str = "date_change"):
        """自动拆分：结束旧会话，开启新会话，保持计数器不清零"""
        project_id = self.project_config.get('id') if self.project_config else None
        if not project_id:
            return
        print(f"[自动拆分] {reason}，自动结束旧会话 {self.current_session_uuid}")
        self.end_session()
        new_info = self.start_session(project_id)
        if new_info:
            print(f"[自动拆分] 新会话已创建: {new_info.get('session_uuid')}")
    
    def _force_timeout_ng(self, reason: str):
        """超时强制NG：触发NG事件并清理当前周期状态"""
        self._trigger_event(2, reason)
        self._cycle_regression = False
        self.current_cycle_steps = []
        self.backup_steps_seen_in_cycle = set()
        self.last_added_step = None
        self.step_last_seen.clear()
        self.step_start_time.clear()
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        if hasattr(self, '_step_raw_start'):
            self._step_raw_start.clear()
        self._last_step_added_time = None
        self.last_step_completed_time = None

    def start_cycle(self):
        """开始新的检测周期"""
        if not self.current_session_id or not self.recording_enabled:
            return
        
        if self._mes_hook and self._mes_hook.is_scan_required(self.channel_id):
            if not self._mes_hook.has_pending_workpiece(self.channel_id):
                print(f"[扫码绑定] 工位{self.channel_id} 要求先扫码，当前无待检工件，跳过开周期")
                return
        
        if self._session_start_date and datetime.now().date() != self._session_start_date:
            self._auto_split_session(reason="日期变更")
            if not self.current_session_id:
                return
        
        current_shift = self._get_current_shift()
        if self._session_start_shift and current_shift and current_shift != self._session_start_shift:
            self._auto_split_session(reason=f"班次变更 {self._session_start_shift}->{current_shift}")
            if not self.current_session_id:
                return
        
        try:
            db = self._get_db_session()
            now = datetime.now()
            self.current_cycle_number += 1
            cycle_uuid = str(uuid.uuid4())[:8]
            
            # 更新上一周期的间隔时间
            if self.last_cycle_end_time is not None:
                interval_from_last = (now - self.last_cycle_end_time).total_seconds()
                # 查找上一周期并更新
                last_cycle = db.query(DetectionCycle).filter(
                    DetectionCycle.session_id == self.current_session_id,
                    DetectionCycle.cycle_number == self.current_cycle_number - 1
                ).first()
                if last_cycle:
                    last_cycle.interval_to_next = round(interval_from_last, 2)
                    db.commit()
                    print(f"上一周期间隔: {interval_from_last:.2f}s")
            
            from backend.api.operators import get_current_operator_id
            cycle = DetectionCycle(
                cycle_uuid=cycle_uuid,
                session_id=self.current_session_id,
                cycle_number=self.current_cycle_number,
                start_time=now,
                operator_id=get_current_operator_id(self.channel_id),
            )
            db.add(cycle)
            db.commit()
            db.refresh(cycle)
            
            self.current_cycle_id = cycle.id
            self.current_cycle_uuid = cycle_uuid
            self.cycle_step_records = []
            self.step_order_counter = 0
            
            # 新周期开始：重置传动杆 SessionGate（上一个周期见过的框架记忆不应跨周期）
            if getattr(self, "_rod_gate", None) is not None:
                try:
                    self._rod_gate.reset()
                except Exception:
                    pass

            db.close()
            print(f"新周期开始: #{self.current_cycle_number} ({cycle_uuid})")
            
            # MES Hook: Cycle 开始
            if self._mes_hook:
                try:
                    project_id = self.project_config.get('id') if self.project_config else None
                    if project_id:
                        self._mes_hook.on_cycle_start(
                            channel_id=self.channel_id,
                            cycle_id=cycle.id,
                            session_id=self.current_session_id,
                            project_id=project_id,
                        )
                except Exception as e:
                    print(f"[MES] cycle_start hook 异常: {e}")
            
            # 开始周期视频录制
            self.start_cycle_recording()
        except Exception as e:
            print(f"创建周期失败: {e}")
    
    def end_cycle(self, is_good: bool, event_id: int = None, event_name: str = None, reason: str = None):
        """结束当前检测周期"""
        if not self.current_cycle_id or not self.recording_enabled:
            return
        
        # 停止周期视频录制
        self.stop_cycle_recording()
        
        try:
            db = self._get_db_session()
            cycle = db.query(DetectionCycle).filter(
                DetectionCycle.id == self.current_cycle_id
            ).first()
            
            if cycle:
                cycle.end_time = datetime.now()
                cycle.duration = (cycle.end_time - cycle.start_time).total_seconds()
                cycle.is_good = is_good
                cycle.event_id = event_id
                cycle.event_name = event_name
                cycle.result_reason = reason
                cycle.step_sequence = self.current_cycle_steps.copy()
                
                # 记录周期结束时间，用于计算下一周期的间隔
                self.last_cycle_end_time = cycle.end_time
                
                db.commit()
                print(f"周期结束: #{self.current_cycle_number}, 结果: {'OK' if is_good else 'NG'}, 耗时: {cycle.duration:.2f}s")
                
                # MES Hook: Cycle 结束
                if self._mes_hook:
                    try:
                        project_id = self.project_config.get('id') if self.project_config else None
                        self._mes_hook.on_cycle_end(
                            channel_id=self.channel_id,
                            cycle_id=cycle.id,
                            is_good=is_good,
                            event_name=event_name,
                            result_reason=reason,
                            duration=cycle.duration,
                            step_sequence=cycle.step_sequence,
                            project_id=project_id,
                        )
                    except Exception as e:
                        print(f"[MES] cycle_end hook 异常: {e}")
            
            db.close()
        except Exception as e:
            print(f"结束周期失败: {e}")
        finally:
            self.current_cycle_id = None
            self.current_cycle_uuid = None
    
    def _discard_empty_cycle(self):
        """Discard the current cycle when step_sequence is empty after filtering."""
        if not self.current_cycle_id:
            return
        self.stop_cycle_recording()
        try:
            db = self._get_db_session()
            db.query(StepRecord).filter(
                StepRecord.cycle_id == self.current_cycle_id).delete()
            db.query(DetectionCycle).filter(
                DetectionCycle.id == self.current_cycle_id).delete()
            db.commit()
            db.close()
            print(f"Discarded empty cycle #{self.current_cycle_number}")
        except Exception as e:
            print(f"Failed to discard empty cycle: {e}")
        finally:
            self.current_cycle_id = None
            self.current_cycle_uuid = None
            if self.current_cycle_number > 0:
                self.current_cycle_number -= 1
    
    def _reconcile_step_records(self):
        """Align StepRecords with self.current_cycle_steps using a
        keep-and-fix strategy: keep existing records that match, delete
        excess ones, and create missing ones.  This avoids losing timing
        data from records already written by the disappearance handler.
        """
        if not self.current_cycle_id or not self.recording_enabled:
            return
        if not self.current_cycle_steps:
            return

        try:
            db = self._get_db_session()
            existing = db.query(StepRecord).filter(
                StepRecord.cycle_id == self.current_cycle_id
            ).order_by(StepRecord.step_order).all()

            avail = {}
            for rec in existing:
                avail.setdefault(rec.step_label, []).append(rec)

            keep_ids = set()
            missing_indices = []
            dur_fixed = 0

            for i, label in enumerate(self.current_cycle_steps):
                candidates = avail.get(label, [])
                # Prefer the candidate with the longest duration
                candidates.sort(key=lambda r: (r.duration or 0), reverse=True)
                matched = None
                for rec in candidates:
                    if rec.id not in keep_ids:
                        matched = rec
                        keep_ids.add(rec.id)
                        break
                if matched:
                    matched.step_order = i + 1
                    # Fix kept records that have very short / zero duration
                    if (matched.duration or 0) < 0.1:
                        better_dur = self.step_durations.get(label)
                        if better_dur and better_dur >= 0.1:
                            matched.duration = better_dur
                            dur_fixed += 1
                        else:
                            st = self.step_start_time.get(label)
                            et = self.step_last_seen.get(label)
                            if st and et and (et - st) >= 0.1:
                                matched.duration = round(et - st, 2)
                                dur_fixed += 1
                else:
                    missing_indices.append(i)

            for rec in existing:
                if rec.id not in keep_ids:
                    db.delete(rec)

            now = time.time()

            for idx in missing_indices:
                label = self.current_cycle_steps[idx]
                start_t = self.step_start_time.get(label)
                end_t = self.step_last_seen.get(label)
                if start_t and end_t:
                    dur = max(0, round(end_t - start_t, 2))
                else:
                    start_t = start_t or now
                    end_t = end_t or now
                    dur = max(0, round(end_t - start_t, 2))

                # Use step_durations fallback when computed duration is too small
                if dur < 0.1:
                    better = self.step_durations.get(label)
                    if better and better >= 0.1:
                        dur = better

                step_id = None
                if self.project_config:
                    for step in self.project_config.get('steps_config', []):
                        if step.get('label') == label:
                            step_id = step.get('id')
                            break

                new_rec = StepRecord(
                    record_uuid=str(uuid.uuid4())[:8],
                    cycle_id=self.current_cycle_id,
                    step_id=step_id,
                    step_label=label,
                    step_name=self.step_display_names.get(label, label),
                    step_order=idx + 1,
                    start_time=datetime.fromtimestamp(start_t),
                    end_time=datetime.fromtimestamp(end_t),
                    duration=dur,
                    is_valid=True,
                )
                db.add(new_rec)

            db.commit()
            db.close()
            print(f"[reconcile] cycle {self.current_cycle_id}: kept {len(keep_ids)}, "
                  f"deleted {len(existing) - len(keep_ids)}, created {len(missing_indices)}"
                  f"{f', dur_fixed {dur_fixed}' if dur_fixed else ''}")
        except Exception as e:
            print(f"Failed to reconcile StepRecords: {e}")
            import traceback
            traceback.print_exc()

    def record_step(self, step_label: str, step_name: str, start_time: float, end_time: float, 
                    duration: float, interval: float = None, confidence: float = None, is_valid: bool = True,
                    video_info: dict = None, step_order: int = None):
        """记录步骤信息
        
        注意：interval 现在表示"到下一步骤的间隔"，在下一步骤开始时计算并更新
        video_info: 视频信息字典，包含 video_uuid 和 filepath
        step_order: 可选，指定步骤在本周期内的顺序号（用于结算时补写缺失步骤）
        """
        if not self.current_cycle_id or not self.recording_enabled:
            return
        
        if not self.export_settings or not self.export_settings.get('record_step_duration', True):
            return
        
        db = None
        try:
            db = self._get_db_session()
            if step_order is not None:
                self.step_order_counter = max(self.step_order_counter, step_order)
            else:
                self.step_order_counter += 1
            order_to_use = step_order if step_order is not None else self.step_order_counter
            
            # 更新上一个步骤的"到下一步间隔"
            if self.cycle_step_records:
                last_record = self.cycle_step_records[-1]
                last_end_time = last_record.get('end_time')
                last_uuid = last_record.get('record_uuid')
                if last_end_time and last_uuid:
                    interval_to_next = start_time - last_end_time
                    last_step = db.query(StepRecord).filter(
                        StepRecord.record_uuid == last_uuid
                    ).first()
                    if last_step:
                        last_step.interval_to_next = round(interval_to_next, 2)
                        db.commit()
            record_uuid = str(uuid.uuid4())[:8]
            
            # 获取步骤ID（从项目配置）
            step_id = None
            if self.project_config:
                for step in self.project_config.get('steps_config', []):
                    if step.get('label') == step_label:
                        step_id = step.get('id')
                        break
            
            # 视频信息
            video_id = None
            video_path = None
            if video_info:
                video_id = video_info.get('video_uuid')
                video_path = video_info.get('filepath')
            
            record = StepRecord(
                record_uuid=record_uuid,
                cycle_id=self.current_cycle_id,
                step_id=step_id,
                step_label=step_label,
                step_name=step_name or step_label,
                step_order=order_to_use,
                start_time=datetime.fromtimestamp(start_time),
                end_time=datetime.fromtimestamp(end_time),
                duration=duration,
                interval_from_prev=interval,
                confidence=confidence,
                is_valid=is_valid,
                video_id=video_id,
                video_path=video_path
            )
            db.add(record)
            db.commit()
            
            # 保存到本地记录用于间隔计算
            self.cycle_step_records.append({
                'step_label': step_label,
                'end_time': end_time,
                'record_uuid': record_uuid
            })
        except Exception as e:
            print(f"记录步骤失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if db:
                db.close()
        
