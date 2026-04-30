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
from backend.models.models import DetectionSession, DetectionCycle, StepRecord, DataExportSetting
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
        db = None
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
            
            print(f"检测会话已创建: {session_uuid}")

            # 后续步骤按“尽力执行”处理，避免出现 DB 已创建成功却返回失败
            try:
                self._load_export_settings()
            except Exception as e:
                print(f"[session] 加载导出设置失败（不影响会话）: {e}")

            # MES Hook: Session 开始
            if self._mes_hook:
                try:
                    hook_project_id = self.project_config.get('id') if self.project_config else None
                    if hook_project_id:
                        self._mes_hook.on_session_start(
                            channel_id=self.channel_id,
                            session_id=session.id,
                            project_id=hook_project_id,
                        )
                except Exception as e:
                    print(f"[MES] session_start hook 异常: {e}")
            
            # 启动录制线程（独立于 CUDA）
            if self.is_detecting:
                try:
                    self._start_recording_thread()
                except Exception as e:
                    print(f"[session] 启动录制线程失败（不影响会话）: {e}")
            
            # 开始会话视频录制
            try:
                self.start_session_recording()
            except Exception as e:
                print(f"[session] 启动会话录制失败（不影响会话）: {e}")
            
            return {"session_id": session.id, "session_uuid": session_uuid}
        except Exception as e:
            print(f"创建会话失败: {e}")
            return None
        finally:
            if db:
                db.close()
    
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

            # v2.7.17: once_per_cycle 死锁兜底 - 如果上一轮扫码发生在 cycle 间隙
            # (扫码器 LOFF 了但当时已经没有进行中的 cycle, end_cycle 的 resume 是 no-op),
            # 在新 cycle 起步时再 resume 一次, 解开 _wait_cycle_resume=True 的死锁.
            try:
                from backend.services.scanner import get_scanner_service
                get_scanner_service().resume_after_cycle(self.channel_id)
            except Exception as e:
                print(f"[Scanner] cycle_start resume 异常 (ch={self.channel_id}): {e}",
                      flush=True)

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

                # v2.7.16: once_per_cycle 模式下, 周期结束 (无论 OK/NG) 都让扫码器
                # 恢复扫描, 等下一个工件的码. 模式不匹配时是 no-op, 不需要额外判断.
                try:
                    from backend.services.scanner import get_scanner_service
                    get_scanner_service().resume_after_cycle(self.channel_id)
                except Exception as e:
                    print(f"[Scanner] resume_after_cycle 异常 (ch={self.channel_id}): {e}",
                          flush=True)

                # v2.7.17: 容器模式杠"野生 settle" - cycle 已经结束, 任何残留在
                # _box_objects 里没及时 confirmed_gone 的 box, 后面才超时 settle 时
                # 因为 cycle 已不在跑, 走 _trigger_event 又会 end_cycle + 计数 +1,
                # 形成"多算一个 NG". 这里强制清空, 让残留 box 被丢弃, 等真正进入下一
                # 个 cycle 才能再 settle.
                try:
                    if getattr(self, '_container_mode', False) and self._box_objects:
                        dropped = list(self._box_objects.keys())
                        self._box_objects.clear()
                        if dropped:
                            print(f"[Container] cycle_end 清残留 box: {dropped} "
                                  f"(避免野生 settle 重复计数)", flush=True)
                except Exception as e:
                    print(f"[Container] cycle_end 清 _box_objects 异常: {e}", flush=True)

                # v3.1.2 多工位广播结算联动: 主工位结算时带动其他广播工位强制结算.
                # 仅当本次 end_cycle 不是"被联动"触发的, 才向外通知 (防止循环).
                if not getattr(self, '_force_settling_in_progress', False):
                    try:
                        from backend.services.scanner import get_scanner_service
                        get_scanner_service().notify_cycle_settled(self.channel_id)
                    except Exception as e:
                        print(f"[Scanner] notify_cycle_settled 异常 (ch={self.channel_id}): {e}",
                              flush=True)

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

    # ==================== v3.1.2: 多工位广播结算联动 ====================

    def force_settle_pending_cycle(self, min_items: int = 1, reason: str = "") -> int:
        """被扫码器联动外部强制结算当前未完成的周期 / 容器.

        约定:
          - 容器模式: 逐个检查 _box_objects, 已检出件数 >= min_items 的 box 立刻 settle;
                      件数不足的 box 留着不动 (避免上游空箱子被冤判 NG).
          - 非容器模式: 当前已检出"在期望清单内"的物品总数 >= min_items 时, 走
                       _settle_counting_cycle 整盘结算; 否则跳过.

        防重入:
          通过 self._force_settling_in_progress 标志, 阻止 _settle_box → end_cycle →
          notify_cycle_settled → 又回来调本方法的循环.

        返回真正被强制结算的 box 数 (容器模式) 或 1/0 (非容器模式).
        """
        if not getattr(self, "project_config", None):
            return 0

        # 重入检测放入口而非外面, 兼容直接被 API 调用的情形.
        if getattr(self, "_force_settling_in_progress", False):
            return 0

        self._force_settling_in_progress = True
        settled_count = 0
        try:
            pcfg = (self.project_config.get("pipeline_config") or {}) if self.project_config else {}
            expected_items = pcfg.get("counting_expected_items", {}) or {}
            container_label = getattr(self, "_container_label", None)

            # ------- 容器模式 -------
            if getattr(self, "_container_mode", False) and getattr(self, "_box_objects", None):
                for box_did in list(self._box_objects.keys()):
                    if box_did not in self._box_objects:
                        continue
                    bs = self._box_objects[box_did]
                    item_count = sum((bs.get("item_class_counts") or {}).values())
                    if item_count < max(0, int(min_items or 0)):
                        print(
                            f"[ForceSettle] ch{getattr(self, 'channel_id', '?')} {box_did} "
                            f"件数 {item_count} < 阈值 {min_items}, 跳过 ({reason})",
                            flush=True,
                        )
                        continue
                    try:
                        self._settle_box(box_did, expected_items)
                        settled_count += 1
                        print(
                            f"[ForceSettle] ch{getattr(self, 'channel_id', '?')} {box_did} "
                            f"已强制结算 (件数 {item_count}, {reason})",
                            flush=True,
                        )
                    except Exception as e:
                        print(
                            f"[ForceSettle] ch{getattr(self, 'channel_id', '?')} {box_did} "
                            f"结算失败: {e}",
                            flush=True,
                        )
                return settled_count

            # ------- 非容器 -------
            if not getattr(self, "current_cycle_id", None):
                return 0
            if not getattr(self, "_tracking_cycle_active", False):
                return 0

            def _in_checklist(cn: str) -> bool:
                if not expected_items:
                    return True
                return cn in expected_items

            tracking_objs = getattr(self, "_tracking_objects", {}) or {}
            total_items = sum(
                1
                for obj in tracking_objs.values()
                if _in_checklist(obj.get("class_name", "")) and obj.get("class_name") != container_label
            )
            if total_items < max(0, int(min_items or 0)):
                print(
                    f"[ForceSettle] ch{getattr(self, 'channel_id', '?')} 非容器, "
                    f"件数 {total_items} < 阈值 {min_items}, 跳过 ({reason})",
                    flush=True,
                )
                return 0

            try:
                check_order = pcfg.get("tracking_check_order", False)
                expected_order = pcfg.get("tracking_expected_order", []) or []
                self._settle_counting_cycle(expected_items, check_order, expected_order)
                settled_count = 1
                print(
                    f"[ForceSettle] ch{getattr(self, 'channel_id', '?')} 非容器周期已强制结算 "
                    f"(件数 {total_items}, {reason})",
                    flush=True,
                )
            except Exception as e:
                print(
                    f"[ForceSettle] ch{getattr(self, 'channel_id', '?')} 非容器结算失败: {e}",
                    flush=True,
                )
            return settled_count
        finally:
            self._force_settling_in_progress = False

    # ============== v3.3.0 码-码闭环结算 (bind_timing="scan_pair") ==============

    def settle_for_scan_pair(self, *, force_ng: bool = False, reason: str = "") -> int:
        """v3.3.0 由 mes_hooks 在扫码 B 到达 (或超时) 时调用, 结算当前周期 / 容器.

        与 force_settle_pending_cycle 的区别:
          - 不依赖 min_items 件数门槛, 完全按"曾齐过"sticky flag 判 OK/NG.
          - 容器模式下结算 *所有* _box_objects (不分件数多少), 然后清空.
            空箱仍会被 v3.1.4 的 container_settle_min_items 过滤掉避免冤判.
          - 非容器模式下: 整盘按 _tracking_was_complete 判 OK/NG (sticky), 然后结算.
          - force_ng=True: 用于超时分支, 一律判 NG.

        防重入: 复用 _force_settling_in_progress 标志.
        """
        if not getattr(self, "project_config", None):
            return 0
        if getattr(self, "_force_settling_in_progress", False):
            return 0

        self._force_settling_in_progress = True
        settled_count = 0
        try:
            pcfg = (self.project_config.get("pipeline_config") or {}) if self.project_config else {}
            expected_items = pcfg.get("counting_expected_items", {}) or {}

            # ------- 容器模式 -------
            if getattr(self, "_container_mode", False) and getattr(self, "_box_objects", None):
                for box_did in list(self._box_objects.keys()):
                    if box_did not in self._box_objects:
                        continue
                    try:
                        self._settle_box(
                            box_did, expected_items,
                            via_scan_pair=True,
                            scan_pair_force_ng=force_ng,
                        )
                        settled_count += 1
                    except Exception as e:
                        print(
                            f"[ScanPairSettle] ch{getattr(self, 'channel_id', '?')} "
                            f"{box_did} 结算失败: {e}",
                            flush=True,
                        )
                print(
                    f"[ScanPairSettle] ch{getattr(self, 'channel_id', '?')} 容器, "
                    f"结算 {settled_count} 个箱子 (force_ng={force_ng}, {reason})",
                    flush=True,
                )
                return settled_count

            # ------- 非容器跟踪模式 -------
            if not getattr(self, "current_cycle_id", None):
                return 0
            if not getattr(self, "_tracking_cycle_active", False):
                return 0

            if force_ng:
                # 超时强制 NG: 不走 _settle_counting_cycle (它按 expected 计数判),
                # 直接调 end_cycle(False, ...) 收尾, _tracking_was_complete 不影响.
                try:
                    self._end_cycle(
                        is_ok=False,
                        event_name="scan_pair_timeout",
                        result_reason="scan_pair_timeout",
                    )
                    settled_count = 1
                    print(
                        f"[ScanPairSettle] ch{getattr(self, 'channel_id', '?')} 非容器, "
                        f"超时强制 NG ({reason})",
                        flush=True,
                    )
                except Exception as e:
                    print(
                        f"[ScanPairSettle] ch{getattr(self, 'channel_id', '?')} "
                        f"非容器 force_ng 失败: {e}",
                        flush=True,
                    )
                return settled_count

            # 正常 (扫码 B 触发): _settle_counting_cycle 内部把 OK 判定切到
            # _tracking_was_complete (在 source_checklist_mixin 改造里实现).
            # 这里设置一个 hint 标志位让结算逻辑识别 scan_pair 路径.
            self._scan_pair_settle_hint = True
            try:
                check_order = pcfg.get("tracking_check_order", False)
                expected_order = pcfg.get("tracking_expected_order", []) or []
                self._settle_counting_cycle(expected_items, check_order, expected_order)
                settled_count = 1
                print(
                    f"[ScanPairSettle] ch{getattr(self, 'channel_id', '?')} 非容器, "
                    f"按曾齐过结算 ({reason})",
                    flush=True,
                )
            except Exception as e:
                print(
                    f"[ScanPairSettle] ch{getattr(self, 'channel_id', '?')} "
                    f"非容器结算失败: {e}",
                    flush=True,
                )
            finally:
                self._scan_pair_settle_hint = False
            return settled_count
        finally:
            self._force_settling_in_progress = False

