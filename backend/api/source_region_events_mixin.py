"""区域事件模式 (logic_mode='region_events') —— VSM 侧执行层.

纯逻辑引擎 (source_region_events.RegionEventEngine) 每帧吐出的事件动作在这里
翻译成主程序副作用, 映射关系 (立项决策: 复用周期/步骤体系, 零新表):

    confirmed (非结算)   → 确保周期已开 + 事件名计入当前周期步骤序列 + 步骤计数;
                           配了 event_id 则借事件响应面 (Toast/语音/计数器, 不结算)
    closed               → 步骤落库 record_step (事件名=步骤名, 携带完整起止时间)
    confirmed (结算)     → 结算规则自身也落一条步骤记录, 然后 _trigger_event
                           走标准结算链 (end_cycle 写库 + 计数器 + 报警 + MES cycle_end)
    sequence_violation   → 借事件响应面产出"乱序"过程事件 (挂不挂报警由事件配置决定),
                           不影响周期结算结果

一个工位循环 = 一个检测周期: 首个确认事件开周期, 结算规则 (如"下工件") 收口。
"""
from __future__ import annotations

import time


class RegionEventsMixin:
    """宿主: VideoSourceManager。依赖宿主提供 start_cycle / record_step /
    _trigger_event / fire_external_event_response / step_counts 等步骤统计字典。
    """

    def _update_region_events(self, detections: list):
        """区域事件模式的每帧入口 (替代 _update_step_stats)。

        Context: 推理线程内被调用, 单线程访问引擎状态; 不持锁;
                 副作用 (落库/事件) 与其他模式的步骤统计同路径, 允许阻塞时长同级。
        """
        engine = getattr(self, '_region_event_engine', None)
        if engine is None:
            return
        for ev in engine.process_frame(detections, time.time()):
            try:
                action = ev.get('action')
                if action == 'confirmed':
                    self._region_on_confirmed(ev)
                elif action == 'closed':
                    self._region_on_closed(ev)
                elif action == 'sequence_violation':
                    self._region_on_sequence_violation(ev)
            except Exception as e:
                print(f"[RegionEvents] 执行动作 {ev.get('action')} 失败 (已隔离): {e}")
                import traceback
                traceback.print_exc()

    # ---------- 动作翻译 ----------
    def _region_on_confirmed(self, ev: dict):
        name = ev['rule_name']

        # 首个确认事件开周期 (与 sequential 首步开周期同语义)
        if not self.current_cycle_steps:
            self.cycle_start_time = ev.get('start_ts') or time.time()
            self.cycle_start_frame_pos = self._video_frame_pos()
            self.start_cycle()

        self.current_cycle_steps.append(name)
        self.last_added_step = name
        self._last_step_added_time = time.time()
        self.step_counts[name] = self.step_counts.get(name, 0) + 1
        print(f"[RegionEvents] 动作确认: {name} (本周期序列 {self.current_cycle_steps}, "
              f"累计 {self.step_counts[name]})")

        if ev.get('settle'):
            self._region_settle(ev)
        elif ev.get('event_id') is not None:
            # 过程事件: 借事件响应面 (Toast/语音/计数器), 不结算周期
            self.fire_external_event_response(
                ev['event_id'], f"{name}", source='region_events')

    def _region_on_closed(self, ev: dict):
        """overlap 事件 episode 闭合: 携带完整起止时间, 落步骤记录。"""
        name = ev['rule_name']
        start_ts, end_ts = ev['start_ts'], ev['end_ts']
        duration = round(max(0.0, end_ts - start_ts), 2)
        self.step_durations[name] = duration
        self.step_durations_history.setdefault(name, []).append(duration)
        self.record_step(
            step_label=name, step_name=name,
            start_time=start_ts, end_time=end_ts, duration=duration,
        )
        print(f"[RegionEvents] 动作闭合: {name}, 持续 {duration:.2f}s ({ev['frames']} 帧)")

    def _region_settle(self, ev: dict):
        """结算规则 (如下工件) 确认: 自身落步骤记录 + 走标准结算链收口周期。

        结算事件优先级: 结算判定命中 (settle_event_id, 缺步骤/重复/序列匹配)
        > 结算规则自身 event_id > 缺省合格事件 (id=1)。
        """
        name = ev['rule_name']
        start_ts = ev.get('start_ts') or time.time()
        end_ts = ev.get('ts') or time.time()
        self.record_step(
            step_label=name, step_name=name,
            start_time=start_ts, end_time=end_ts,
            duration=round(max(0.0, end_ts - start_ts), 2),
        )
        if ev.get('settle_event_id') is not None:
            settle_event_id = ev['settle_event_id']
            reason_suffix = f" ({ev.get('settle_reason') or '结算判定命中'})"
        else:
            settle_event_id = ev.get('event_id') if ev.get('event_id') is not None else 1
            reason_suffix = ''
        steps_desc = '→'.join(self.current_cycle_steps)
        self._trigger_event(settle_event_id, f"区域事件结算: {steps_desc}{reason_suffix}")
        # _trigger_event → end_cycle 已写库; 清运行时序列开启下一循环
        self.current_cycle_steps = []
        self.last_added_step = None

    def _region_on_sequence_violation(self, ev: dict):
        actual = '→'.join(ev.get('actual') or [])
        expected = '→'.join(ev.get('expected') or [])
        reason = f"动作顺序异常: 实际 {actual}, 期望 {expected}"
        print(f"[RegionEvents] {reason}")
        if ev.get('event_id') is not None:
            # 乱序默认只记录: 挂不挂报警/Toast 由该事件在项目事件设置里的动作决定
            self.fire_external_event_response(
                ev['event_id'], reason, source='region_events')
