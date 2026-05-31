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
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timedelta

from backend.db.database import SessionLocal
from backend.models.models import DetectionSession, DetectionCycle, StepRecord, DataExportSetting
from sqlalchemy import func


# ============================================================
# v3.13 M1.2b: 业务侧消费 returnable hook 返回值的纯函数辅助
# 抽成纯函数让测试可独立验证 (不需要起完整 VideoSourceManager).
# ============================================================


def _resolve_pre_cycle_end_overrides(
    original_is_good: bool,
    original_reason: Optional[str],
    plugin_result: Any,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """解析 pre_cycle_end returnable, 算出最终 (is_good, reason, extra_counters).

    返回:
        final_is_good:        被插件覆盖后的判定 (override_result 缺位 → 原值)
        final_reason:         result_reason; override 发生时追加 ``[plugin override: A → B]``
                              痕迹便于客户现场追溯
        plugin_extra_counters: extra_counters 字典 (M1.2b 仅日志, 待 M3.3 落 DB)

    契约:
        - plugin_result 非 dict / 缺 override_result → 返回原值
        - override_result 取值仅 "OK" / "NG", 其它值忽略 (含小写 / None)
        - extra_counters 必须 dict, 否则视为缺失 (None / 任意非 dict 都被丢弃)
    """
    final_is_good = bool(original_is_good)
    final_reason = original_reason
    extra: Optional[Dict[str, Any]] = None

    if not isinstance(plugin_result, dict):
        return final_is_good, final_reason, extra

    override = plugin_result.get("override_result")
    if override in ("OK", "NG"):
        new_is_good = (override == "OK")
        if new_is_good != bool(original_is_good):
            final_is_good = new_is_good
            tag = f"[plugin override: {('OK' if original_is_good else 'NG')} → {override}]"
            if original_reason:
                final_reason = f"{original_reason} {tag}"
            else:
                final_reason = tag

    ec = plugin_result.get("extra_counters")
    if isinstance(ec, dict) and ec:
        extra = ec

    return final_is_good, final_reason, extra


def _resolve_step_change_warn(
    plugin_result: Any,
) -> Tuple[bool, str]:
    """解析 step_change returnable, 算出 (warn_violated, warn_label).

    返回:
        warn_violated: bool, 是否被插件标记为警告
        warn_label:    警告标签 (例 ``"yellow"`` / ``"near_limit"`` / ``""``)

    契约:
        - plugin_result 非 dict → (False, "")
        - warn_threshold_violated falsy → (False, "") 即使 warn_label 有值
        - warn_label 非 None 强转 str
    """
    if not isinstance(plugin_result, dict):
        return False, ""

    warn_violated = bool(plugin_result.get("warn_threshold_violated"))
    if not warn_violated:
        return False, ""

    raw_label = plugin_result.get("warn_label")
    label = str(raw_label) if raw_label else ""
    return True, label


# 会话标识合法性: 1~64 字符, 不允许文件路径/通配符等
_SESSION_NAME_FORBIDDEN = set('/\\:*?"<>|\r\n\t')


def _clean_session_name(raw):
    """规整客户输入的会话标识. 返回 None 或干净字符串.

    规则:
      - 去首尾空白
      - 空字符串视为 None (不写入 DB)
      - 含禁用字符直接抛 ValueError (调用方决定 400 返回)
      - 截断到 64 字符
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raw = str(raw)
    cleaned = raw.strip()
    if not cleaned:
        return None
    bad = [c for c in cleaned if c in _SESSION_NAME_FORBIDDEN]
    if bad:
        raise ValueError(f"会话标识不能包含字符: {''.join(sorted(set(bad)))}")
    return cleaned[:64]


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

    def start_session(self, project_id: int, name: str = None) -> dict:
        """开始新的检测会话

        参数:
            project_id: 项目 ID
            name: 客户自定义会话标识 (可选, 用于文件名/筛选). 不填则为 None.
        """
        db = None
        try:
            db = self._get_db_session()
            session_uuid = str(uuid.uuid4())[:8]
            current_shift = self._get_current_shift()
            # v3.10+ 阶段 4: 数据归属重定向到 user_id (字段名 operator_id 保留不变)
            from backend.core.auth import get_current_user_id
            current_op_id = get_current_user_id()
            cleaned_name = _clean_session_name(name) if name else None
            session = DetectionSession(
                session_uuid=session_uuid,
                name=cleaned_name,
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
            self.current_session_name = cleaned_name
            self.current_cycle_number = 0
            self.recording_enabled = True
            self._session_start_date = datetime.now().date()
            self._session_start_shift = current_shift
            
            print(f"检测会话已创建: uuid={session_uuid} name={cleaned_name or '<未设置>'}")

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
            
            return {
                "session_id": session.id,
                "session_uuid": session_uuid,
                "session_name": cleaned_name,
            }
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
            
            # v3.13: session_end 插件 hook — session 已写库 + 统计已聚合, db 即将关闭.
            # 在 MES Hook 之前触发, 让插件能拿到完整统计快照 (与 cycle_end 时序对齐).
            # 字段名为契约一部分: 改名要进 changelog + 升级 plugin SDK 测试.
            session_ctx_total = session.total_cycles if (session and session.total_cycles is not None) else 0
            session_ctx_good = session.good_cycles if (session and session.good_cycles is not None) else 0
            session_ctx_ng = session.ng_cycles if (session and session.ng_cycles is not None) else 0
            try:
                from backend.plugin_system.hook_dispatch import fire_plugin_hook
                fire_plugin_hook("session_end", "post_session", "post", {
                    "channel_id": self.channel_id,
                    "session_id": session_id,
                    "session_uuid": session_uuid,
                    "total_cycles": session_ctx_total,
                    "good_cycles": session_ctx_good,
                    "ng_cycles": session_ctx_ng,
                    "avg_cycle_time": session.avg_cycle_time if session else None,
                    "min_cycle_time": session.min_cycle_time if session else None,
                    "max_cycle_time": session.max_cycle_time if session else None,
                    "counters_snapshot": dict(session.counters_snapshot) if (session and session.counters_snapshot) else {},
                    "project_id": self.project_config.get("id") if self.project_config else None,
                })
            except Exception as e:
                print(f"[Plugin] session_end hook 触发异常 (已隔离, 主流程继续): {e}")

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
        """超时强制NG：触发NG事件并清理当前周期状态.

        v3.8.x: 统一调清空函数, 把"静态触发标记 / 同时出现组缓冲 / pending 队列"
        这三项以前漏清的字段一起清掉.
        """
        self._trigger_event(2, reason)
        if hasattr(self, '_clear_step_runtime_state'):
            self._clear_step_runtime_state()

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
            
            # v3.10+ 阶段 4: cycle.operator_id 改写当前登录 user_id (字段名保留)
            from backend.core.auth import get_current_user_id
            cycle = DetectionCycle(
                cycle_uuid=cycle_uuid,
                session_id=self.current_session_id,
                cycle_number=self.current_cycle_number,
                start_time=now,
                operator_id=get_current_user_id(),
            )
            db.add(cycle)
            db.commit()
            db.refresh(cycle)
            
            self.current_cycle_id = cycle.id
            self.current_cycle_uuid = cycle_uuid
            self.cycle_step_records = []
            self.step_order_counter = 0

            # v3.8.x: PT 累计字典在"新周期开始"清，而不是"周期结束"清。
            # 客户语义：上一周期 D 完成 OK 后，到下一周期 A 步骤到来之前，
            # 应该保留 D 的 PT + 已检测/OK 视觉反馈。
            # 旧实现把清空放在 end_cycle.finally，导致周期结束瞬间 PT 列就变 '--'，
            # 与"已检测"状态不一致（前端反馈期 1.2s 内能看见状态保留但 PT 已丢）。
            # 现在挪到这里：周期间隙保留累计，新周期起步再清，自然衔接前端 reset 时机。
            # 第一道守门（label in current_cycle_steps 才写入）继续防止跨周期污染。
            self.step_cycle_durations = {}
            self.step_durations = {}
            # v3.10.x: 分段历史与 cycle_sum_step_durations 同步生命周期
            if hasattr(self, 'step_cycle_segments'):
                self.step_cycle_segments = {}

            # v3.13 M1.2b: 清上一周期插件 step_change warn 缓存. cache key 是 step_record_id
            # (全局唯一), 跨周期不会撞 key, 但仍然清掉避免内存无界增长 (持续运行的工控机).
            if hasattr(self, '_plugin_step_warn_cache'):
                self._plugin_step_warn_cache = {}

            # v3.9.x D 方案: 累计可见时长字典也在新周期起步时清, 跟 step_cycle_durations 同步
            # 周期间隙保留 (前端展示期内还能看见上一周期 PT), 新周期起立刻清进入下一轮.
            if hasattr(self, 'step_visible_seconds'):
                self.step_visible_seconds = {}

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

            # v3.13 M1.1: cycle_start 插件 hook — cycle 已落库 + MES Hook 已通知, 此时
            # "新周期开始"事件已完整发生; 录像启动放在 hook 之后 (录像失败不影响 cycle 已开始).
            from backend.plugin_system.hook_dispatch import fire_plugin_hook
            fire_plugin_hook("cycle_start", "post_cycle_start", "post", {
                "channel_id": self.channel_id,
                "cycle_id": cycle.id,
                "cycle_uuid": cycle_uuid,
                "session_id": self.current_session_id,
                "cycle_number": self.current_cycle_number,
                "project_id": self.project_config.get("id") if self.project_config else None,
                "start_time": now.isoformat() if now else None,
            })

            # v3.14 RFC 11: 通知 WorkpieceFlowCoordinator 本通道 cycle_start.
            # 若本通道属于某串行流水线 → 把 cycle 绑到队列首个 in-flight run 对应工位.
            # 不属于任何 flow 时直接 return, 零差异. 任何异常隔离.
            try:
                from backend.services.workpiece_flow_coordinator import get_coordinator as _get_wfc_coord2
                _wfc_db = self._get_db_session()
                try:
                    _get_wfc_coord2().on_cycle_started(
                        channel_id=self.channel_id,
                        cycle_id=cycle.id,
                        db=_wfc_db,
                    )
                finally:
                    _wfc_db.close()
            except Exception as _e_wfc:
                print(f"[WorkpieceFlow] on_cycle_started 异常 (隔离, 不影响主流程): {_e_wfc}")

            # 开始周期视频录制
            self.start_cycle_recording()
        except Exception as e:
            print(f"创建周期失败: {e}")
    
    def _flush_active_steps_pt(self):
        """v3.8.x: 周期结算前，把还在画面里的、已被本周期接纳的步骤的 PT 主动写入累计字典。

        客户反馈："NG 周期最后一步 PT 一直不显示，直接结算"。
        根因：D 步骤还在画面里就触发 settle → end_cycle，而 disappear_delay 还没到，
        正常的"步骤完成"日志在 end_cycle 之后才触发，PT 写入滞后于 history 快照。

        本方法在 end_cycle 入口调用，把 step_last_seen 里仍存活的、且已经在 current_cycle_steps
        里的标签，用 (last_seen - start_time) 算出 duration 并写入 step_durations/step_cycle_durations，
        同时清掉 step_last_seen/step_start_time，避免 disappear_delay 路径再次累加。
        """
        try:
            last_seen = getattr(self, 'step_last_seen', {})
            start_map = getattr(self, 'step_start_time', {})
            if not last_seen:
                return
            for label, last_t in list(last_seen.items()):
                if label not in self.current_cycle_steps:
                    continue
                # v3.9.x: 严格顺序 PT 起点夹到 max(start, 上一步完成时刻).
                raw_start = start_map.get(label, last_t)
                start_t = self._resolve_step_pt_anchor(label, raw_start)
                # v3.10.x B方案v2: 视频源用帧号差/fps 算耗时
                _raw_start_fr = self.step_start_frame_pos.get(label, 0)
                _start_fr = self._resolve_step_pt_anchor_frame_pos(label, _raw_start_fr)
                _last_fr = self.step_last_frame_pos.get(label, 0)
                duration = self._compute_duration_sec(
                    _start_fr, _last_fr,
                    fallback_start_wall=start_t, fallback_end_wall=last_t,
                )
                if duration <= 0:
                    continue
                rounded = round(duration, 2)
                self.step_durations[label] = rounded
                self.step_durations_history.setdefault(label, []).append(rounded)
                self._accumulate_step_pt(label, rounded)
                # 清掉 step_last_seen / step_start_time，避免 disappear 路径再来一次累加（重复计数）
                del last_seen[label]
                if label in start_map:
                    del start_map[label]
                print(f"[flush] 结算前补写 {label} PT={rounded}s（避免 D 步骤直接结算时 PT 缺失）")
        except Exception as e:
            print(f"[flush] _flush_active_steps_pt 异常: {e}")

    def end_cycle(self, is_good: bool, event_id: int = None, event_name: str = None, reason: str = None):
        """结束当前检测周期"""
        if not self.current_cycle_id or not self.recording_enabled:
            return

        # v3.13 RFC 10: 工位组联动 — pending override 强制改写本次结算结果.
        # 客户场景: 双工位 A 站先结算 NG, 通过 coordinator 给 B 站设了 "NG" pending.
        # B 站 end_cycle 走到这里, 拿到 NG override → 强制把 is_good 改 False.
        # 通道不在任何组时, get_pending_override 直接返回 None, 零差异.
        #
        # ⚠️ v3.13.0 已知边界 (留待 v3.13.1):
        #   当前仅改 is_good (DB 字段 + cycle.event_name 保留原值 + group_settle_result
        #   写 "NG_BY_GROUP" 让分析侧可区分本机 NG 与联动 NG). MES Hook on_cycle_end /
        #   cycle_end plugin hook 都用 final_is_good, 已经传 NG.
        #
        #   但 alarm_router / 语音 / Toast / event_fire 链路是由 _trigger_event 在结算前
        #   触发的, 这里改写 is_good 不会重放事件链, 因此 B 通道的报警灯/语音/MES 工件
        #   状态不会自动联动 NG. 后续 RFC 10 v3.13.1 需要决断:
        #     a) Coordinator 持有 AlarmRouter 引用直接驱动 B 通道亮灯 (跳过事件链)
        #     b) 约定系统级虚拟事件 id (如 __channel_group_ng__) 走 _trigger_event
        #     c) 仅做 DB 标记 + 客户自定义插件订阅 channel_group_settle_start hook 自己实现
        #   v3.13.0 选 c (零侵入) — 客户可以写一个简单插件订阅 channel_group_settle_start,
        #   调 PluginHost.trigger_alarm(channel_id) 自己驱动 B 灯.
        try:
            from backend.services.channel_group_coordinator import get_coordinator as _get_cg_coord
            _override = _get_cg_coord().get_pending_override(self.channel_id)
            if _override is not None:
                _override_is_good = (_override == "OK")
                if _override_is_good != is_good:
                    print(
                        f"[ChannelGroup] ch{self.channel_id} 结算被组级联动覆盖: "
                        f"{'OK' if is_good else 'NG'} → {_override}",
                        flush=True,
                    )
                    is_good = _override_is_good
        except Exception as _e:
            print(f"[ChannelGroup] get_pending_override 异常 (隔离, 不影响主流程): {_e}")

        # v3.13 M1.2b: pre_cycle_end 插件 hook — 结算结果已定 (is_good 入参), 但写库 +
        # 副作用 (PT flush / 录像收尾 / MES 推送 / 报警联动) 都还没发生. M1.2a 起返回的
        # returnable dict 让插件可强制改写结算结果 (override_result OK/NG). 这里消费它,
        # 所有下游 (DB / MES / 周期性强制动作 / cycle_end hook ctx) 都看 final_*.
        original_is_good = bool(is_good)
        final_is_good = original_is_good
        final_reason = reason
        plugin_extra_counters: Optional[Dict[str, Any]] = None
        try:
            from backend.plugin_system.hook_dispatch import fire_plugin_hook
            plugin_result = fire_plugin_hook("pre_cycle_end", "pre_cycle", "pre", {
                "channel_id": self.channel_id,
                "cycle_id": self.current_cycle_id,
                "session_id": self.current_session_id,
                "is_good": original_is_good,
                "result": "OK" if original_is_good else "NG",
                "judgement": "OK" if original_is_good else "NG",
                "event_id": event_id,
                "event_name": event_name,
                "reason": reason,
                "project_id": self.project_config.get("id") if self.project_config else None,
                "step_sequence": list(self.current_cycle_steps) if hasattr(self, "current_cycle_steps") else [],
            })

            # 抽到纯函数让测试可独立验证消费契约 (见本文件顶部 _resolve_pre_cycle_end_overrides).
            final_is_good, final_reason, plugin_extra_counters = _resolve_pre_cycle_end_overrides(
                original_is_good=original_is_good,
                original_reason=reason,
                plugin_result=plugin_result,
            )

            if final_is_good != original_is_good:
                print(
                    f"[Plugin] pre_cycle_end override 生效: cycle_id={self.current_cycle_id} "
                    f"{('OK' if original_is_good else 'NG')} → {('OK' if final_is_good else 'NG')}",
                    flush=True,
                )
            if plugin_extra_counters:
                # DetectionCycle 没有 plugin_data 字段, 这里仅日志; M3.3 落字段后再写 DB.
                print(
                    f"[Plugin] pre_cycle_end extra_counters (M1.2b 仅日志, 待 M3.3 落库): "
                    f"cycle_id={self.current_cycle_id} keys={sorted(plugin_extra_counters.keys())}",
                    flush=True,
                )
        except Exception as e:
            print(f"[Plugin] pre_cycle_end hook 触发异常 (已隔离, 主流程继续): {e}")

        # v3.8.x: 在 stop_recording / history 快照 之前，把还在画面里的步骤 PT 主动写入累计字典。
        # 解决 NG 周期下 D 步骤直接结算时 PT 显示 '--' 的客户报障（详见 _flush_active_steps_pt 注释）。
        self._flush_active_steps_pt()

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
                # M1.2b: 走 final_* (可能被 pre_cycle_end 插件 override). event_id /
                # event_name 暂不在 returnable 白名单, 维持原值; 真要改"是哪个事件触发"
                # 需要 M2 / M3 单独设计 (会牵连前端展示语义).
                cycle.is_good = final_is_good
                cycle.event_id = event_id
                cycle.event_name = event_name
                cycle.result_reason = final_reason
                cycle.step_sequence = self.current_cycle_steps.copy()
                
                # 记录周期结束时间，用于计算下一周期的间隔
                self.last_cycle_end_time = cycle.end_time
                
                db.commit()
                print(f"周期结束: #{self.current_cycle_number}, 结果: {'OK' if final_is_good else 'NG'}, 耗时: {cycle.duration:.2f}s")
                
                # MES Hook: Cycle 结束 — M1.2b: 走 final_*
                if self._mes_hook:
                    try:
                        project_id = self.project_config.get('id') if self.project_config else None
                        self._mes_hook.on_cycle_end(
                            channel_id=self.channel_id,
                            cycle_id=cycle.id,
                            is_good=final_is_good,
                            event_name=event_name,
                            result_reason=final_reason,
                            duration=cycle.duration,
                            step_sequence=cycle.step_sequence,
                            project_id=project_id,
                        )
                    except Exception as e:
                        print(f"[MES] cycle_end hook 异常: {e}")

                # v3.5.0: 周期性强制动作判定（每 N 轮做 E）— M1.2b: 走 final_is_good
                # 独立 try/except，不影响 MES Hook / Scanner resume / Container 清理
                try:
                    if hasattr(self, '_check_periodic_actions'):
                        self._check_periodic_actions(
                            cycle.step_sequence or [], bool(final_is_good)
                        )
                except Exception as e:
                    print(f"[PeriodicActions] cycle_end 判定异常: {e}")

                # v3.7 / G1.5: 触发 active 插件的 cycle_end/post_cycle/post hook.
                # 独立 try/except — 插件抛错绝不影响主程序后续步骤 (Scanner resume / Container 清理 / 多工位联动).
                # M1.2b: ctx.is_good / result / reason 走 final_* — 让 post_cycle 插件看到
                # pre_cycle_end override 后的最终判定 (链式插件契约).
                try:
                    from backend.plugin_system.manager import plugin_manager
                    if plugin_manager.registry is not None:
                        plugin_ctx = {
                            "channel_id": self.channel_id,
                            "cycle_id": cycle.id,
                            "cycle_uuid": cycle.cycle_uuid,
                            "session_id": cycle.session_id,
                            "is_good": bool(final_is_good),
                            "result": "OK" if final_is_good else "NG",
                            "judgement": "OK" if final_is_good else "NG",
                            "event_id": event_id,
                            "event_name": event_name,
                            "reason": final_reason,
                            "duration": cycle.duration,
                            "step_sequence": cycle.step_sequence or [],
                            "project_id": self.project_config.get("id") if self.project_config else None,
                        }
                        plugin_manager.registry.hooks.fire(
                            "cycle_end", "post_cycle", "post", plugin_ctx
                        )
                except Exception as e:
                    print(f"[Plugin] cycle_end hook 触发异常 (已隔离, 主流程继续): {e}")

                # v3.13 RFC 10: 通知 ChannelGroupCoordinator 本次结算.
                # 触发同组联动 (synchronized_any_ng: NG → 其它成员设 pending override).
                # 通道不在任何组时直接 return, 零差异; 任何异常都隔离, 不影响主流程.
                try:
                    from backend.services.channel_group_coordinator import get_coordinator as _get_cg_coord
                    _get_cg_coord().on_cycle_settled(
                        channel_id=self.channel_id,
                        cycle_id=cycle.id,
                        is_good=bool(final_is_good),
                        db=db,
                    )
                except Exception as _e:
                    print(f"[ChannelGroup] on_cycle_settled 异常 (隔离, 不影响主流程): {_e}")

                # v3.14 RFC 11: 通知 WorkpieceFlowCoordinator 本次结算.
                # 推进串行流水线状态机 (最后一站 → COMPLETED; 任一 NG + short_circuit → SHORT_CIRCUITED).
                # 通道不在任何 flow 时直接 return, 零差异; 任何异常都隔离, 不影响主流程.
                try:
                    from backend.services.workpiece_flow_coordinator import get_coordinator as _get_wfc_coord
                    _get_wfc_coord().on_cycle_settled(
                        channel_id=self.channel_id,
                        cycle_id=cycle.id,
                        is_good=bool(final_is_good),
                        db=db,
                    )
                except Exception as _e:
                    print(f"[WorkpieceFlow] on_cycle_settled 异常 (隔离, 不影响主流程): {_e}")

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
            # v3.5.x: 把当前周期的 step SUM 快照到 history（供 PT 合并档"最近一轮"/"平均"使用）。
            # 仅在 cycle 真正结束时执行；_discard_empty_cycle 不快照（周期作废，数据不进入历史）。
            #
            # v3.8.x: 清空 step_cycle_durations / step_durations 的动作**不再**在这里做，
            # 已移到 start_cycle（新周期开始时清）。
            # 原因：客户报障"D 完成 OK 后短暂间隙 PT 列就变 '--'"。旧实现在 end_cycle 立即清，
            # 把前端 1.2s 视觉反馈期里"已检测/OK + PT 数字"的展示连带消除。
            # 现在 history 入库后保留 cycle_sum/step_durations，让前端在周期间隙继续显示，
            # 直到新周期 start_cycle 起步时一次性清干净。
            try:
                if self.step_cycle_durations:
                    for _lbl, _total in self.step_cycle_durations.items():
                        if _total > 0:
                            self.step_cycle_durations_history.setdefault(_lbl, []).append(round(_total, 2))
            except Exception as _e:
                print(f"[PT-Sum] 周期 SUM 快照异常: {_e}")
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
            # v3.5.x: 周期作废时清空本周期 SUM 累加（不进入 history）
            self.step_cycle_durations = {}
            # v3.7.x: 同步清 step_durations，对齐周期结束清零行为
            self.step_durations = {}
            # v3.10.x: 同步清分段历史
            if hasattr(self, 'step_cycle_segments'):
                self.step_cycle_segments = {}
            self.current_cycle_id = None
            self.current_cycle_uuid = None
            if self.current_cycle_number > 0:
                self.current_cycle_number -= 1
            # v3.7.5: 旁路保养观察账本也清, 防作废的周期里观察到的 trigger 串到下一周期
            obs = getattr(self, '_periodic_triggers_observed', None)
            if isinstance(obs, set):
                obs.clear()
    
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

            # v3.13 M1.1: step_change 插件 hook — step_record 已落库 + 本地缓存已更新.
            # 客户需求 1 (步骤耗时三档颜色) 在这里判定 warn 阈值.
            # ctx.duration 已经是秒, 与 step_records.duration 一致.
            #
            # M1.2b: 消费 returnable warn_threshold_violated / warn_label 字段, 缓存到
            # VSM 实例字典 self._plugin_step_warn_cache. cycle 结束时清空.
            # 落 DB 等 M3.3 (StepRecord.plugin_data JSON 字段) 解锁.
            from backend.plugin_system.hook_dispatch import fire_plugin_hook
            step_plugin_result = fire_plugin_hook("step_change", "post_step", "post", {
                "channel_id": self.channel_id,
                "cycle_id": self.current_cycle_id,
                "step_record_id": record.id,
                "record_uuid": record_uuid,
                "step_id": step_id,
                "step_label": step_label,
                "step_name": step_name or step_label,
                "step_order": order_to_use,
                "duration": duration,
                "interval_from_prev": interval,
                "confidence": confidence,
                "is_valid": is_valid,
            })

            # M1.2b: 解析 returnable warn 字段 (见本文件顶部 _resolve_step_change_warn).
            warn_violated, warn_label = _resolve_step_change_warn(step_plugin_result)
            if warn_violated:
                # 兼容: VSM 老实例可能没这字段, 用 getattr + 兜底初始化.
                cache = getattr(self, "_plugin_step_warn_cache", None)
                if cache is None:
                    cache = {}
                    self._plugin_step_warn_cache = cache
                cache[record.id] = {
                    "warn_label": warn_label,
                    "step_id": step_id,
                    "step_label": step_label,
                    "step_order": order_to_use,
                    "duration": duration,
                    "record_uuid": record_uuid,
                }
                print(
                    f"[Plugin] step_change warn 生效: cycle_id={self.current_cycle_id} "
                    f"step_record_id={record.id} label={warn_label!r} step={step_label!r}",
                    flush=True,
                )
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

    def _ensure_cycle_for_scan_pair_settle(self) -> bool:
        """v3.4.2 hotfix: scan_pair 模式不走 start_cycle 路径, current_cycle_id
        永远是 None → end_cycle 早返回 → 不调 on_cycle_end → 工件 set_result /
        外部 MES 推送 / 集群汇总 全都不发生. 这里在 settle 触发 _trigger_event
        之前手动 create cycle 行 + 把 _inspecting_workpiece (=上一码 wp) link
        到 cycle, 让下游 on_cycle_end 拿得到 cycle_id + wp_id, 走完整 MES 路径.

        cycle.start_time 取上一码 _scan_pair_active.scanned_at (即 ScanPair 真实
        起点), 让 cycle 表里时长跟 _trigger_event log 的 "周期时间(OK)" 一致.
        """
        if getattr(self, "current_cycle_id", None):
            return True  # 已有 cycle (其他路径起的) 不重复建
        if not getattr(self, "current_session_id", None):
            return False  # 没 session 起不了 cycle
        if not getattr(self, "_mes_hook", None):
            return False
        ch = getattr(self, "channel_id", 0)
        wp_id = self._mes_hook._inspecting_workpiece.get(ch)
        if not wp_id:
            # 没绑工件就不起 cycle, 让 _trigger_event/end_cycle 走原 fallback
            # 路径 (counters +1, 不写 cycle 行, 不发集群 — 跟修前一样).
            return False

        try:
            from backend.models.models import DetectionCycle
            # v3.10+ 阶段 4: cycle.operator_id 改写当前登录 user_id
            from backend.core.auth import get_current_user_id
            from datetime import datetime as _dt

            db = self._get_db_session()
            now = _dt.now()
            self.current_cycle_number += 1
            cycle_uuid = str(uuid.uuid4())[:8]

            scanned_at = None
            try:
                sp = self._mes_hook._scan_pair_active.get(ch) or {}
                scanned_at = sp.get("scanned_at")
            except Exception:
                scanned_at = None
            start_dt = _dt.fromtimestamp(scanned_at) if scanned_at else now

            cycle = DetectionCycle(
                cycle_uuid=cycle_uuid,
                session_id=self.current_session_id,
                cycle_number=self.current_cycle_number,
                start_time=start_dt,
                operator_id=get_current_user_id(),
            )
            db.add(cycle)
            db.commit()
            db.refresh(cycle)
            # v3.4.2 hotfix-2: cycle.id 在 db.close() 后再 print 会触发
            # DetachedInstanceError, 整个函数被 except 吞掉, 上层 settle
            # 流程拿不到正确的 cycle_id ↔ wp_id 关联. 先存到局部变量, 后面
            # 所有引用都走 _cid 不再碰 ORM.
            _cid = cycle.id
            self.current_cycle_id = _cid
            self.current_cycle_uuid = cycle_uuid

            try:
                self._mes_hook._workpiece_svc.link_to_cycle(
                    db, wp_id, _cid,
                    session_id=self.current_session_id, channel_id=ch,
                )
                db.commit()
            except Exception as e:
                print(f"[ScanPairSettle] link_to_cycle 失败: {e}", flush=True)

            db.close()
            print(
                f"[ScanPairSettle] ch{ch} 同步 create Cycle#{_cid} + "
                f"关联工件#{wp_id} (start_time={start_dt.isoformat(timespec='seconds')})",
                flush=True,
            )
            return True
        except Exception as e:
            print(f"[ScanPairSettle] _ensure_cycle 异常: {e}\n{traceback.format_exc()}",
                  flush=True)
            return False

    def settle_for_scan_pair(self, *, force_ng: bool = False, reason: str = "") -> int:
        """v3.3.0 由 mes_hooks 在扫码 B 到达 (或超时) 时调用, 结算当前周期 / 容器.

        与 force_settle_pending_cycle 的区别:
          - 不依赖 min_items 件数门槛, 完全按"曾齐过"sticky flag 判 OK/NG.
          - 容器模式下结算 *所有* _box_objects (不分件数多少), 然后清空.
            空箱仍会被 v3.1.4 的 container_settle_min_items 过滤掉避免冤判.
          - 非容器模式下: 整盘按 _tracking_was_complete 判 OK/NG (sticky), 然后结算.
          - force_ng=True: 用于超时分支, 一律判 NG.

        防重入: 复用 _force_settling_in_progress 标志.

        v3.4.2 hotfix: 触发事件前先 _ensure_cycle_for_scan_pair_settle, 让
        cycle 行存在 + wp 绑上 → on_cycle_end 才能正常分发集群.
        """
        if not getattr(self, "project_config", None):
            return 0
        if getattr(self, "_force_settling_in_progress", False):
            return 0

        self._force_settling_in_progress = True
        settled_count = 0
        try:
            # v3.4.2 hotfix: 确保有 cycle 行 (用上一码扫码时间作 start_time),
            # 否则下游 end_cycle → on_cycle_end → 集群分发 整条链路全断.
            self._ensure_cycle_for_scan_pair_settle()
            pcfg = (self.project_config.get("pipeline_config") or {}) if self.project_config else {}
            expected_items = pcfg.get("counting_expected_items", {}) or {}

            # ------- 容器模式 -------
            # v3.4.2 用户语义: scan_pair 模式下"一码一结算, 不管周期内多少个箱子,
            # 只要其中至少一个曾装齐过 → OK; 否则 NG". 计数器只 +1 (整周期 1 次事件).
            # 实现:
            #   1) 遍历所有 _box_objects 调 _settle_box(suppress_event=True), 写每个 box
            #      到 _box_settled_results / 写 StepRecord (前端数据中心展开能看到明细),
            #      但不调 _trigger_event.
            #   2) 聚合: any(was_complete) → OK, force_ng → 一律 NG.
            #   3) 调一次 _trigger_event 让 _end_cycle 写 cycle 行 + 计数器 +1.
            if getattr(self, "_container_mode", False) and getattr(self, "_box_objects", None):
                box_dids = list(self._box_objects.keys())
                ok_results = []
                ng_results = []
                # v3.4.2 hotfix-2: 收集每个 NG box 的 missing/extra 明细, 给整周期
                # _trigger_event 拼出像非容器模式那样的"缺少 X: 0/N"信息. 不然
                # 集群/数据中心只看到"0/1 箱齐过"不知道是缺哪个件 → 没法追溯.
                ng_missing_per_box: list[dict] = []
                # 记录 _settle_box append 进 _box_settled_results 之前的尾部位置,
                # 以便从中精确取出本周期内 settle 的 result 行 (含 missing/extra/items).
                _prev_settled_len = len(getattr(self, "_box_settled_results", []) or [])

                for box_did in box_dids:
                    if box_did not in self._box_objects:
                        continue
                    # 抓 was_complete 在 pop 前 (suppress 路径里 _settle_box 会 pop)
                    was_complete_before = bool(
                        self._box_objects[box_did].get('was_complete', False)
                    )
                    try:
                        is_ok = self._settle_box(
                            box_did, expected_items,
                            via_scan_pair=True,
                            scan_pair_force_ng=force_ng,
                            suppress_event=True,
                        )
                        if is_ok is None:
                            # 幽灵箱过滤掉, 不计
                            continue
                        settled_count += 1
                        if is_ok:
                            ok_results.append(box_did)
                        else:
                            ng_results.append(box_did)
                    except Exception as e:
                        print(
                            f"[ScanPairSettle] ch{getattr(self, 'channel_id', '?')} "
                            f"{box_did} 结算失败: {e}",
                            flush=True,
                        )

                # 从本次 settle append 进 _box_settled_results 的尾段, 抓 NG 详情
                _new_settled = (
                    list(self._box_settled_results[_prev_settled_len:])
                    if getattr(self, "_box_settled_results", None) else []
                )
                # v3.4.2 hotfix-3: 用户语义 — 容器模式下整周期 NG 只关心"有没有
                # 哪个件从来没出现过". 单个箱子缺件不重要 — 只要别的箱子里出现过
                # (max(item_counts) >= expected), 就不算"真缺".
                # 所以 missing 用"跨 box 聚合 max" 算, 而不是按 box 拆.
                _agg_max_counts: dict = {}  # label → max(item_counts across boxes)
                for r in _new_settled:
                    items_cnt = r.get('item_counts') or {}
                    for k, v in items_cnt.items():
                        _agg_max_counts[k] = max(_agg_max_counts.get(k, 0), int(v or 0))
                    # 仍然把按 box 的明细收着, force_ng/超时分支可能要用
                    if r.get('is_complete'):
                        continue
                    ng_missing_per_box.append({
                        "box": r.get('display_id'),
                        "missing": list(r.get('missing') or []),
                        "extra": list(r.get('extra') or []),
                        "items": dict(items_cnt),
                    })

                # 跨 box 聚合后真正的"缺"清单 (任意一箱出现过 → 算齐, 不计入)
                _container_label = getattr(self, '_container_label', None)
                _agg_missing: list[str] = []
                for k, exp_n in (expected_items or {}).items():
                    if k == _container_label:
                        continue
                    actual = _agg_max_counts.get(k, 0)
                    if actual < int(exp_n or 0):
                        disp = self.step_display_names.get(k, k) \
                            if hasattr(self, 'step_display_names') else k
                        _agg_missing.append(f"{disp}: {actual}/{exp_n}")

                # v3.4.2 hotfix-5: 用户原则"件齐就 OK, 不管几箱、不管 force_ng".
                #   - 跨 box 聚合后所有 expected 件都出现过 → OK
                #   - 任意 expected 件 max(item_counts)==0 (从没出现过) → NG
                #   - force_ng (超时) 也走同一判定, 只在文案前加"超时"前缀提示
                #   - 全幽灵箱 (settled_count==0) 仍跳过 (无意义)
                if settled_count == 0:
                    print(
                        f"[ScanPairSettle] ch{getattr(self, 'channel_id', '?')} 容器, "
                        f"无真箱 (全幽灵), 跳过事件 ({reason})",
                        flush=True,
                    )
                    return 0

                cycle_is_ok = not _agg_missing
                _prefix = "超时" if force_ng else ""

                # 触发一次整周期事件 → 计数器 +1
                try:
                    if cycle_is_ok:
                        self._trigger_event(
                            1, f'{_prefix}合格(OK)' if _prefix else '合格(OK)'
                        )
                    else:
                        self._trigger_event(
                            2,
                            f'{_prefix}NG: 缺 [{", ".join(_agg_missing)}]'
                            if _prefix else
                            f'NG: 缺 [{", ".join(_agg_missing)}]',
                        )
                except Exception as e:
                    print(
                        f"[ScanPairSettle] ch{getattr(self, 'channel_id', '?')} "
                        f"trigger_event 失败: {e}",
                        flush=True,
                    )

                print(
                    f"[ScanPairSettle] ch{getattr(self, 'channel_id', '?')} 容器, "
                    f"周期聚合 {'OK' if cycle_is_ok else 'NG'} "
                    f"({len(ok_results)} 齐过 / {len(ng_results)} 未齐, "
                    f"force_ng={force_ng}, {reason})",
                    flush=True,
                )
                return settled_count

            # ------- 非容器跟踪模式 -------
            # v3.4.2: scan_pair 模式下 tracking 工位完全不依赖 _start_cycle /
            # current_cycle_id (没人会调). 这里直接按 _tracking_was_complete
            # (sticky 标志) + force_ng 判 OK/NG, 调 _trigger_event 让计数器 +1.
            # 即便 cycle_id=None 也工作: end_cycle 内部会因 cycle_id=None 而早返
            # 回不写 db cycle 行, 但 counters / events_log 仍会更新, 前端能看到.
            ch_for_log = getattr(self, "channel_id", "?")
            was_complete = bool(getattr(self, "_tracking_was_complete", False))
            counters = dict(getattr(self, "_tracking_class_counters", {}) or {})

            # v3.4.2 hotfix-5: 跟容器分支同样原则 — "件齐就 OK, 不管 force_ng".
            #   - was_complete (期间曾齐过) → OK
            #   - 否则按 counters 算 missing; 列表空 → 件其实齐了 → OK
            #   - 列表非空 → NG
            #   - force_ng (超时) 不再硬判 NG, 只在文案前加"超时"前缀
            missing = [
                f"{self.step_display_names.get(k, k) if hasattr(self, 'step_display_names') else k}: "
                f"{counters.get(k, 0)}/{v}"
                for k, v in (expected_items or {}).items()
                if counters.get(k, 0) < v
            ]
            is_ok = was_complete or not missing
            _prefix = "超时" if force_ng else ""
            if is_ok:
                event_reason = f"{_prefix}合格(OK)" if _prefix else "合格(OK)"
            else:
                event_reason = (
                    f"{_prefix}NG: 缺 [{', '.join(missing)}]"
                    if _prefix else
                    f"NG: 缺 [{', '.join(missing)}]"
                )

            self._scan_pair_settle_hint = True
            try:
                self._trigger_event(1 if is_ok else 2, event_reason)
                settled_count = 1
                print(
                    f"[ScanPairSettle] ch{ch_for_log} 非容器, "
                    f"{'OK' if is_ok else 'NG'} (was_complete={was_complete}, "
                    f"counters={counters}, force_ng={force_ng}, {reason})",
                    flush=True,
                )
            except Exception as e:
                print(
                    f"[ScanPairSettle] ch{ch_for_log} 非容器结算失败: {e}",
                    flush=True,
                )
            finally:
                self._scan_pair_settle_hint = False

            # 重置 sticky 状态 / counters, 给下一个 scan_pair 周期让位.
            try:
                if hasattr(self, "_reset_counting_cycle"):
                    self._reset_counting_cycle()
            except Exception as e:
                print(
                    f"[ScanPairSettle] ch{ch_for_log} reset 失败: {e}",
                    flush=True,
                )
            return settled_count
        finally:
            self._force_settling_in_progress = False

