"""事件触发 (_trigger_event, v2.7.16 P6 阶段一从 source.py 整体搬出)。

把 200 行的 _trigger_event 直接整体搬到独立文件。后续如需细分,
可在 mixin 内继续按职责拆 _event_should_suppress / _event_lookup_by_id /
_event_record_cycle_time / _event_count_ng_step_misses /
_event_count_ng_top3_steps / _event_apply_actions_and_log。

宿主必须提供的属性: self.project_config / self.alarm_manager / self.events_log /
                  counter 状态 / 周期时间统计 / NG TOP 字典等
宿主必须提供的方法: self._discard_empty_cycle / self.end_cycle / 等
"""
from __future__ import annotations

import time
import traceback


class EventTriggerMixin:
    def _trigger_event(self, event_id, reason: str) -> bool:
        """触发事件。返回 True 表示事件已触发，False 表示被抑制或失败。"""
        if not self.project_config:
            return False
        
        # 防重复结算（仅在项目配置中开启 settle_dedup 时生效）
        settle_dedup = self.project_config.get('pipeline_config', {}).get('settle_dedup', False)
        if settle_dedup and not self.current_cycle_id and self.recording_enabled:
            print(f"[_trigger_event] 防重复结算: 跳过, 当前无活跃周期 (event={event_id}, reason={reason})")
            return False
        
        # NG cycle protection: suppress rapid consecutive NG reports
        current_time = time.time()
        is_ng = (event_id == 2 or str(event_id) == '2')
        if is_ng:
            ng_protect_sec = self.project_config.get('pipeline_config', {}).get(
                'ng_cycle_protect_seconds', 0)
            if ng_protect_sec > 0:
                last_ng = getattr(self, '_last_ng_time', 0)
                if last_ng and (current_time - last_ng) < ng_protect_sec:
                    print(f"NG保护: 距上次NG仅{current_time - last_ng:.1f}s < {ng_protect_sec}s，抑制本次NG ({reason})")
                    self._discard_empty_cycle()
                    return False
            self._last_ng_time = current_time
        
        events_config = self.project_config.get('events_config', [])
        
        # 支持数字ID和字符串ID（如 1, 2 或 'event_1', 'event_2'）
        event = None
        for e in events_config:
            eid = e.get('id')
            # 匹配数字ID或字符串ID
            if eid == event_id or str(eid) == str(event_id):
                event = e
                break
            # 也支持 event_1 格式匹配 id=1
            if isinstance(event_id, str) and event_id.startswith('event_'):
                try:
                    num_id = int(event_id.split('_')[1])
                    if eid == num_id:
                        event = e
                        break
                except:
                    pass
        
        if not event:
            print(f"事件未找到: {event_id}")
            return False
        
        if settle_dedup:
            import traceback as _tb
            caller = _tb.extract_stack(limit=4)
            caller_info = ' <- '.join(f"{f.name}:{f.lineno}" for f in caller[:-1])
            print(f"触发事件: {event.get('name', event_id)} - {reason} "
                  f"[cycle_id={self.current_cycle_id}, caller={caller_info}]")
        else:
            print(f"触发事件: {event.get('name', event_id)} - {reason}")
        
        # 判断是否为合格事件（事件ID为1或者名称包含"合格"）
        current_event_id = event.get('id')
        is_good = current_event_id == 1
        
        # Record cycle time for both OK and NG cycles
        if self.cycle_start_time is not None:
            cycle_time = time.time() - self.cycle_start_time
            if current_event_id == 1:
                self.cycle_times.append(cycle_time)
                if len(self.cycle_times) > 100:
                    self.cycle_times = self.cycle_times[-100:]
                print(f"  周期时间(OK): {cycle_time:.2f}s, 平均: {sum(self.cycle_times)/len(self.cycle_times):.2f}s")
            else:
                self.ng_cycle_times.append(cycle_time)
                if len(self.ng_cycle_times) > 100:
                    self.ng_cycle_times = self.ng_cycle_times[-100:]
                print(f"  周期时间(NG): {cycle_time:.2f}s")
        
        had_workpiece = False
        if self._mes_hook:
            had_workpiece = (self.channel_id in self._mes_hook._inspecting_workpiece
                             or self.channel_id in self._mes_hook._pending_workpiece)

        # v3.5.2: 后端权威判定"是否该弹未绑码 toast", 前端直接读, 不再做客户端守门.
        # 三种情况静默: (1) 事件已绑工件 (2) 该工位已禁用扫码 (3) 系统中根本没扫码器.
        should_warn_no_barcode = False
        try:
            if self._mes_hook is not None and not had_workpiece:
                scan_disabled = self._mes_hook.is_channel_scan_disabled(self.channel_id)
                has_scanner = self._mes_hook.has_any_scanner_present()
                should_warn_no_barcode = bool(has_scanner and not scan_disabled)
        except Exception:
            should_warn_no_barcode = False

        # 结束当前周期并记录到数据库
        self.end_cycle(
            is_good=is_good,
            event_id=current_event_id,
            event_name=event.get('name', ''),
            reason=reason
        )
        
        # 重置周期开始时间（无论是合格还是NG，都重置）
        self.cycle_start_time = None
        
        # NG步骤 计数逻辑（仅在顺序模式或基于顺序的自定义模式中）
        # 只在检测到漏做（缺少步骤）时计数，跳步本质上也是缺少步骤导致的
        if current_event_id == 2 and 'NG步骤' in self.counters:
            logic_mode = self.project_config.get('logic_mode', 'detection') if self.project_config else 'detection'
            pipeline_config = self.project_config.get('pipeline_config', {}) if self.project_config else {}
            custom_based_on = pipeline_config.get('custom_based_on')
            
            # 仅在顺序模式或基于顺序的自定义模式中计数
            is_sequential_mode = (logic_mode == 'sequential' or 
                                  (logic_mode == 'custom' and custom_based_on == 'sequential'))
            
            if is_sequential_mode:
                # 只在缺少步骤时计数
                is_missing_step = '缺少' in reason or '周期不完整' in reason
                
                if is_missing_step:
                    import re
                    # 尝试从原因中提取缺少的步骤列表，按数量计入
                    match = re.search(r"缺少[：:]\s*\[([^\]]+)\]", reason)
                    if match:
                        missing_steps = match.group(1).split(',')
                        ng_step_count = len([s.strip() for s in missing_steps if s.strip()])
                    else:
                        ng_step_count = 1  # 默认 +1
                    
                    self.counters['NG步骤'] += ng_step_count
                    print(f"  NG步骤计数 += {ng_step_count} => {self.counters['NG步骤']} (原因: {reason})")
                    self._persist_counters()
        
        # NG TOP3: count unique cycles per step (each step counted at most once per NG cycle)
        if current_event_id == 2 and reason:
            import re
            involved = set()
            m_miss = re.search(r'缺少[：:]\s*\[?([^\]]+)\]?', reason)
            if m_miss:
                for s in re.split(r'[,，]', m_miss.group(1)):
                    n = s.strip().strip("'\" ")
                    if n:
                        involved.add(n)
            if '重复' in reason:
                m_dup = re.search(r'重复步骤[：:]\s*\[?([^\]]+)\]?', reason)
                if m_dup:
                    for s in re.split(r'[,，]', m_dup.group(1)):
                        n = s.strip().strip("'\" ")
                        if n:
                            involved.add(n)
            if '顺序错误' in reason:
                m_ord = re.search(r'期望\[(.+?)\].*实际\[(.+?)\]', reason)
                if m_ord:
                    if m_ord.group(1).strip():
                        involved.add(m_ord.group(1).strip())
                    if m_ord.group(2).strip():
                        involved.add(m_ord.group(2).strip())
            if '缺件' in reason:
                m_lack = re.search(r'缺件[：:]\s*\{?([^}]+)\}?', reason)
                if m_lack:
                    for s in re.split(r'[,，]', m_lack.group(1)):
                        n = s.strip().strip("'\" ")
                        if n:
                            involved.add(n)
            if not involved:
                steps_config = self.project_config.get('steps_config', []) if self.project_config else []
                expected = set(s.get('label') for s in steps_config if s.get('label') and not s.get('is_backup'))
                actual = set(self.current_cycle_steps)
                missing = expected - actual
                if missing:
                    involved = missing
                elif '顺序' in reason and self.current_cycle_steps:
                    involved = set(self.current_cycle_steps)
            for step_name in involved:
                self.ng_step_cycle_counts[step_name] = self.ng_step_cycle_counts.get(step_name, 0) + 1
        
        # 执行计数器动作（支持 delta 和 value 两种字段名）
        actions = event.get('actions', [])
        counters_changed = False
        for action in actions:
            counter_name = action.get('counter_name', '')
            value = action.get('delta', action.get('value', 1))
            if counter_name in self.counters:
                self.counters[counter_name] += value
                counters_changed = True
                print(f"  计数器 {counter_name} += {value} => {self.counters[counter_name]}")
        if counters_changed:
            self._persist_counters()
        
        # 记录事件
        self._event_seq += 1
        self.events_log.append({
            'seq': self._event_seq,
            'event_id': str(event.get('id', event_id)),
            'event_name': event.get('name', ''),
            'reason': reason,
            'timestamp': time.time(),
            'show_notification': event.get('show_notification', False),
            'toast_id': event.get('toast_id', 'ok' if event.get('id') == 1 else 'ng' if event.get('id') == 2 else 'ok'),
            'had_workpiece': had_workpiece,
            'should_warn_no_barcode': should_warn_no_barcode,
        })
        
        # 触发报警器（如果已配置）— 按通道路由到对应工位的指示灯
        try:
            from backend.api.alarm import alarm_router
            event_type = f'event{current_event_id}'
            alarm_router.trigger_alarm(event_type, channel_id=self.channel_id)
        except Exception as e:
            print(f"触发报警失败: {e}")

        # feat/multi-model-roi-link: 通知 InferenceRouter, 让监听本事件的副模型下次跑一次.
        # 投递两个 key: 'event_<id>' (稳定, 推荐) + 事件名 (人类可读, 兜底).
        # 副模型 schedule.events 中只要任一命中即可被调度.
        try:
            router = getattr(self, '_router', None)
            if router is not None:
                event_name = event.get('name')
                if current_event_id is not None:
                    router.trigger_event(f"event_{current_event_id}")
                if event_name:
                    router.trigger_event(str(event_name))
        except Exception as e:
            print(f"router.trigger_event 失败: {e}")

        return True
