"""区域事件模式 (logic_mode='region_events') —— VSM 侧执行层.

纯逻辑引擎 (source_region_events.RegionEventEngine) 每帧吐出的事件动作在这里
翻译成主程序副作用, 映射关系 (立项决策: 复用周期/步骤体系, 零新表):

    confirmed (非结算)   → 确保周期已开 + 事件名计入当前周期步骤序列 + 步骤计数
                           + 步骤截图 (SOP 卡片) + 与上一动作间隔;
                           配了 event_id 则借事件响应面 (Toast/语音/计数器, 不结算)
    closed               → 步骤落库 record_step (事件名=步骤名, 携带完整起止时间)
                           + 周期 PT 合并档落账 (Monitor PT 列权威口径)
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

    def _update_region_events(self, detections: list, original_frame=None):
        """区域事件模式的每帧入口 (替代 _update_step_stats)。

        Context: 推理线程内被调用, 单线程访问引擎状态; 不持锁;
                 副作用 (落库/事件) 与其他模式的步骤统计同路径, 允许阻塞时长同级。
        """
        engine = getattr(self, '_region_event_engine', None)
        if engine is None:
            return
        # 供"动作确认"时裁步骤截图 (SOP 卡片缩略图); 引用不拷贝, 帧只在本轮循环内使用
        self._region_frame_for_shot = original_frame
        # 频闪诊断 (与 custom_mix 同一套环形缓冲 + 自动转储): 监视规则涉及的
        # 全部类别, 某类别 2s 内在场翻转过频 → 现场转储到 diag_flicker/ 供定位真因
        try:
            self._diag_region_flicker(engine, detections, time.time())
        except Exception:
            pass
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

    # ---------- 频闪诊断 ----------
    def _diag_region_flicker(self, engine, detections: list, current_time: float):
        """区域事件模式的频闪采样入口: 复用 custom_mix 的环形缓冲 + 自动转储。

        监视标签 = 全部规则涉及的类别 (主体/目标/伴随/锚点), 引擎实例更换时重算。
        采样体 / 翻转判定 / 转储文件格式与 custom_mix 完全一致 (source_step_stats_mixin)。
        """
        if getattr(self, '_diag_watched_token', None) is not id(engine):
            w = set()
            for rule in getattr(engine.cfg, 'rules', []):
                for lbl in (rule.subject_label, rule.object_label,
                            rule.require_label, rule.anchor_label):
                    if lbl:
                        w.add(lbl)
            self._diag_watched = w
            self._diag_watched_token = id(engine)
        watched = getattr(self, '_diag_watched', None)
        if not watched:
            return
        det_by_label = {}
        for d in detections or []:
            lbl = d.get('label')
            if lbl in watched:
                prev = det_by_label.get(lbl)
                if prev is None or d.get('confidence', 0) > prev.get('confidence', 0):
                    det_by_label[lbl] = d
        self._diag_flicker_sample(watched, det_by_label, detections, current_time)

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

        # Monitor 步骤面板补喂 (v3.32 SOP 卡片打通):
        # 与上一动作的间隔 (sequential 同语义: 上一动作结束 → 本动作开始;
        # 互斥打断场景两段动作在时间上可交叠, 负值一律夹 0)
        start_ts = ev.get('start_ts') or time.time()
        prev_end = getattr(self, 'last_step_completed_time', None)
        self.step_intervals[name] = (
            round(max(0.0, start_ts - prev_end), 2) if prev_end is not None else 0)
        # SOP 卡片缩略图: 确认瞬间裁主体框
        self._region_capture_screenshot(name, ev.get('subject'))

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
        # 周期 PT 合并档 (Monitor PT 列/已检测状态的权威口径)。守门与其他模式一致:
        # 只给本周期已接纳的动作落账, 防跨周期残段污染
        if name in self.current_cycle_steps:
            self._accumulate_step_pt(name, duration)
        self.last_step_completed_time = end_ts
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
        duration = round(max(0.0, end_ts - start_ts), 2)
        # 结算动作的 PT 也进合并档: 必须在 _trigger_event(end_cycle) 之前写,
        # end_cycle 会把 step_cycle_durations 快照进历史档
        self.step_durations[name] = duration
        self.step_durations_history.setdefault(name, []).append(duration)
        if name in self.current_cycle_steps:
            self._accumulate_step_pt(name, duration)
        self.last_step_completed_time = end_ts
        self.record_step(
            step_label=name, step_name=name,
            start_time=start_ts, end_time=end_ts,
            duration=duration,
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
        # 间隔基准跨周期不接续 (与 sequential 结算清零同语义): 下一循环首个动作间隔为 0
        self.last_step_completed_time = None

    def _region_capture_screenshot(self, name: str, subject: dict):
        """动作确认瞬间裁主体框 → 步骤截图 (SOP 卡片缩略图)。

        与步骤状态机的截图同格式 (JPEG q70 + base64, pad 20px), 前端零适配。
        无帧 (剧本源/单元桩) 或裁剪失败一律静默跳过, 不影响动作主流程。
        """
        frame = getattr(self, '_region_frame_for_shot', None)
        if frame is None:
            return
        try:
            import base64
            import cv2
            img_h, img_w = frame.shape[:2]
            if subject and all(k in subject for k in ('x', 'y', 'w', 'h')):
                pad = 20
                x1 = max(0, int(subject['x'] * img_w) - pad)
                y1 = max(0, int(subject['y'] * img_h) - pad)
                x2 = min(img_w, int((subject['x'] + subject['w']) * img_w) + pad)
                y2 = min(img_h, int((subject['y'] + subject['h']) * img_h) + pad)
            else:
                x1, y1, x2, y2 = 0, 0, img_w, img_h  # 无主体框: 整帧兜底
            if x2 <= x1 or y2 <= y1:
                return
            crop = frame[y1:y2, x1:x2]
            ok, buffer = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                self.step_screenshots[name] = base64.b64encode(buffer).decode('utf-8')
        except Exception as e:
            print(f"[RegionEvents] 步骤截图失败 (已隔离): {e}")

    def _region_on_sequence_violation(self, ev: dict):
        actual = '→'.join(ev.get('actual') or [])
        expected = '→'.join(ev.get('expected') or [])
        reason = f"动作顺序异常: 实际 {actual}, 期望 {expected}"
        print(f"[RegionEvents] {reason}")
        if ev.get('event_id') is not None:
            # 乱序默认只记录: 挂不挂报警/Toast 由该事件在项目事件设置里的动作决定
            self.fire_external_event_response(
                ev['event_id'], reason, source='region_events')
