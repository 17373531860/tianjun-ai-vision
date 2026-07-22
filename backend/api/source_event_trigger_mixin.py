"""事件触发 (_trigger_event, v2.7.16 P6 阶段一从 source.py 整体搬出)。

把 200 行的 _trigger_event 直接整体搬到独立文件。后续如需细分,
可在 mixin 内继续按职责拆 _event_should_suppress / _event_lookup_by_id /
_event_record_cycle_time / _event_count_ng_step_misses /
_event_count_ng_top3_steps / _event_apply_actions_and_log。

宿主必须提供的属性: self.project_config / self.alarm_manager / self.events_log /
                  counter 状态 / 周期时间统计 / NG TOP 字典等
宿主必须提供的方法: self._discard_empty_cycle / self.end_cycle / 等

v3.13 M1.2c: 重排尾部顺序让 event_fire hook 的 suppress_alarm 来得及作用.
  重构前: events_log → _pending_ack → alarm → router → _last_event_time → event_fire hook
  重构后: events_log → _pending_ack → event_fire hook → resolve suppress → [alarm?] → router → _last_event_time
  无插件场景 (registry is None / 无 handler / 默认空 dict): hook 返回 {}, suppress=False,
  alarm 照常触发 — 与重构前严格等价 (基线测试 test_trigger_event_baseline_M1_2c.py 守护).
"""
from __future__ import annotations

import time
import traceback
from typing import Any


def _rem_dbg(action: str, detail: str = "") -> None:
    """NG 补做 (缺步骤延迟落账) 调试埋点 → 调试中心「结算状态机」分类.

    缺步骤延迟落账本质是结算状态机的新分支, 归 backend.settlement; 类别未开时
    debug_center.dbg 一次 dict 查询即返回, 零开销. lazy import 防调试设施反噬主流程.
    """
    try:
        from backend.core import debug_center
        debug_center.dbg("backend.settlement", action, detail)
    except Exception:
        pass


# ============================================================
# v3.13 M1.2c: 业务侧消费 event_fire returnable 的纯函数辅助
# 抽成纯函数让测试可独立验证消费契约 (不需要起完整 VideoSourceManager).
# ============================================================


def _resolve_event_fire_suppress_alarm(plugin_result: Any) -> bool:
    """解析 event_fire returnable, 决定是否抑制本次 alarm 触发.

    安全侧默认: **任何不显式 True 的值都不抑制** (宁可误报警也不漏报警).
    严格 ``is True`` 而非 truthy: 防 ``"false"`` 字符串 / ``1`` 整数 / 任意非 bool truthy
    被误识为抑制. 这是与 M1.2b ``override_result`` 的细微差异 — 后者用枚举 "OK"/"NG"
    严格匹配, 这里用 ``is True`` 严格匹配.

    契约:
        - plugin_result 非 dict → False (默认不抑制)
        - ``suppress_alarm`` 缺省 / None / 非 True → False
        - 仅 ``plugin_result.get("suppress_alarm") is True`` 才抑制
        - handler 异常已被 fire 层吞 → 不会到达此处
    """
    if not isinstance(plugin_result, dict):
        return False
    return plugin_result.get("suppress_alarm") is True


class EventTriggerMixin:
    def _trigger_event(self, event_id, reason: str) -> bool:
        """触发事件。返回 True 表示事件已触发，False 表示被抑制或失败。"""
        if not self.project_config:
            return False

        # v3.9.x 事件人工确认阻塞门:
        # 已经在阻塞态时, 后续事件直接丢弃 — 工人还没确认上一件 NG, 状态机本来就被
        # 主循环守门拦下, 不应该再触发新事件 (即使被某条边路触发到, 计数 / MES Hook /
        # 报警的二次叠加都没意义, 反而会引起重复推送).
        if getattr(self, '_pending_ack', False):
            print(f"[_trigger_event] blocked (waiting manual ack), dropping event event={event_id}, reason={reason}")
            return False

        # v3.10.x: 防重复结算 - 时间窗口冷却
        # 任意事件触发后, 在窗口期 (settle_dedup_window_seconds, 默认 2s) 内,
        # 所有事件 (OK / NG / 自定义) 全部被吃, 解决 OK 结算后又来 NG / 同节拍多结算等问题.
        # 与 ng_cycle_protect_seconds 可共存: ng_protect 只针对 NG → NG; 本守门覆盖所有方向.
        # 守门通过且事件最终触发成功后, 在函数末尾记录 _last_event_time, 抑制本身不记锚点.
        pipeline_cfg = self.project_config.get('pipeline_config', {}) or {}
        settle_dedup = bool(pipeline_cfg.get('settle_dedup', False))
        current_time = time.time()
        if settle_dedup:
            dedup_window = float(pipeline_cfg.get('settle_dedup_window_seconds', 2.0) or 0)
            if dedup_window > 0:
                last_event = float(getattr(self, '_last_event_time', 0.0) or 0.0)
                if last_event and (current_time - last_event) < dedup_window:
                    print(f"[_trigger_event] dedup settle: {current_time - last_event:.2f}s since last event < {dedup_window:.2f}s, suppressed (event={event_id}, reason={reason})")
                    self._discard_empty_cycle()
                    return False

        # NG cycle protection: suppress rapid consecutive NG reports
        is_ng = (event_id == 2 or str(event_id) == '2')
        if is_ng:
            ng_protect_sec = self.project_config.get('pipeline_config', {}).get(
                'ng_cycle_protect_seconds', 0)
            if ng_protect_sec > 0:
                last_ng = getattr(self, '_last_ng_time', 0)
                if last_ng and (current_time - last_ng) < ng_protect_sec:
                    print(f"NG protect: only {current_time - last_ng:.1f}s since last NG < {ng_protect_sec}s, suppress this NG ({reason})")
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
            print(f"event not found: {event_id}")
            return False
        
        if settle_dedup:
            import traceback as _tb
            caller = _tb.extract_stack(limit=4)
            caller_info = ' <- '.join(f"{f.name}:{f.lineno}" for f in caller[:-1])
            print(f"触发事件: {event.get('name', event_id)} - {reason} "
                  f"[cycle_id={self.current_cycle_id}, caller={caller_info}]")
        else:
            print(f"[Event] trigger: {event.get('name', event_id)} - {reason}")
        
        # 判断是否为合格事件（事件ID为1或者名称包含"合格"）
        current_event_id = event.get('id')
        is_good = current_event_id == 1

        # v3.23 NG 补做 — 缺步骤延迟落账守门 (唯一收口点, 默认关=零差异):
        # 本周期判 NG 且原因是"缺步骤", 且项目开了补步骤策略 → 不 end_cycle / 不计数 /
        # 不推 MES, 改为挂起 (报警提示工人), 等人工补步骤(判OK) / 认NG(落账) / 重做(丢弃).
        # _remediation_bypass: confirm_ng 重发 NG 时一次性旁路, 防自锁.
        # _skip_remediation_defer: 缺步挂起超时落账的窄旁路 (v3.44) — 挂起本身
        # 已是补做窗口, 超时=放弃等待, 不再二次进补做挂账 (但人工确认定格照走).
        if (current_event_id == 2
                and getattr(self, 'current_cycle_uuid', None)
                and not getattr(self, '_remediation_bypass', False)
                and not getattr(self, '_skip_remediation_defer', False)
                and self._should_defer_for_remediation(reason)):
            self._enter_step_remediation_hold(event, reason)
            return False

        # Record cycle time for both OK and NG cycles
        if self.cycle_start_time is not None:
            # v3.10.x B方案v2: 视频源用帧号差/fps 算 CT, 跟客户机解码速度解耦
            _ct_start_fr = getattr(self, 'cycle_start_frame_pos', None) or 0
            _ct_end_fr = self._video_frame_pos()
            cycle_time = self._compute_duration_sec(
                _ct_start_fr, _ct_end_fr,
                fallback_start_wall=self.cycle_start_time,
                fallback_end_wall=time.time(),
            )
            if current_event_id == 1:
                self.cycle_times.append(cycle_time)
                if len(self.cycle_times) > 100:
                    self.cycle_times = self.cycle_times[-100:]
                print(f"  cycle time(OK): {cycle_time:.2f}s, avg: {sum(self.cycle_times)/len(self.cycle_times):.2f}s")
            else:
                self.ng_cycle_times.append(cycle_time)
                if len(self.ng_cycle_times) > 100:
                    self.ng_cycle_times = self.ng_cycle_times[-100:]
                print(f"  cycle time(NG): {cycle_time:.2f}s")
        
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

        # v3.13 M1.1: event_fire 插件 hook 在 end_cycle 后 fire, 但 cycle_id 在 end_cycle
        # 内会被清成 None, 这里先把"被结算的那个 cycle_id"缓存到局部变量供 hook ctx 用.
        _event_cycle_id = self.current_cycle_id

        # v3.44 NG 处置闭环: NG 事件带「需人工确认」→ 包装箱账挂起等处置
        # (补齐/照实/重做), 不先落账翻页. 标志必须在 end_cycle 之前置位 —
        # end_cycle 内同步快照后才异步入 FIFO 落库作业.
        # _remediation_bypass = 工人已在确认弹窗做过处置 (认NG重发), 不再挂账.
        _operator_resolved = bool(getattr(self, '_remediation_bypass', False))
        if (current_event_id == 2 and bool(event.get('require_ack', False))
                and not _operator_resolved):
            self._pkg_hold_for_ack = True

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
                    print(f"  NG step count += {ng_step_count} => {self.counters['NG步骤']} (reason: {reason})")
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
            # v3.32 区域事件模式结算判定文案: "(缺事件 X)" / "(事件 X 重复≥N次)"
            m_miss_ev = re.search(r'缺事件\s*([^\s,，)）]+)', reason)
            if m_miss_ev:
                involved.add(m_miss_ev.group(1))
            m_rep_ev = re.search(r'事件\s*([^\s,，)）]+)\s*重复', reason)
            if m_rep_ev:
                involved.add(m_rep_ev.group(1))
            if not involved:
                # 兜底: 期望全集 - 本周期实际 = 缺失者。
                # v3.32: 区域事件模式的"步骤"是动作规则名, steps_config 里是模型类别
                # (测硬度笔/工件/手) —— 拿类别当期望会把标签全记进 NG TOP3 (客户报障
                # "TOP3 出现的不是步骤而是标签"), 该模式期望集必须取引擎规则名。
                _re_engine = getattr(self, '_region_event_engine', None)
                if _re_engine is not None:
                    expected = {r.name for r in _re_engine.cfg.rules}
                else:
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
                print(f"  counter {counter_name} += {value} => {self.counters[counter_name]}")
        if counters_changed:
            self._persist_counters()
        
        # v3.9.x 事件人工确认开关:
        # - require_ack=True : 触发阻塞态, 主循环 / 推流 / 状态机全停 (本通道范围内),
        #                     直到 /api/v1/source/detection/ack-event 主动确认才解除
        # - ack_timeout_sec  : 超时阈值 (0 = 永不超时, 必须人工点击)
        # 阻塞态在事件正常入库 / 计数 / MES / 报警都执行完之后再设置, 这样:
        #   - NG 计数 / OK 计数已累加 (符合"已判定本周期 NG, 等工人重做这件"语义)
        #   - 报警闪光 / MES 推送照常出去 (现场设备和外部系统不能停)
        #   - 但状态机阻塞 → 工人重做时新周期不会被旧残影直接顶替
        require_ack = bool(event.get('require_ack', False))
        ack_timeout_sec = max(0, int(event.get('ack_timeout_sec', 0) or 0))

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
            'require_ack': require_ack,
            'ack_timeout_sec': ack_timeout_sec,
        })

        # v3.9.x 进入阻塞态 (事件正常落库 + 计数 + 报警 + MES Hook 都执行完之后)
        # v3.44: 认NG重发 (_remediation_bypass) 不再二次定格 — 工人刚在弹窗上
        # 处置完, 再弹一次确认框是客户报障的"双重确认"体验 bug.
        if require_ack and not _operator_resolved:
            self._pending_ack = True
            self._pending_ack_started_at = time.time()
            self._pending_ack_event_id = str(event.get('id', event_id))
            self._pending_ack_event_name = event.get('name', '')
            self._pending_ack_timeout_sec = ack_timeout_sec
            self._pending_ack_reason = reason
            print(f"[ack] entering manual-ack blocked state: event={self._pending_ack_event_name} "
                  f"(id={self._pending_ack_event_id}, timeout={ack_timeout_sec}s, "
                  f"channel_id={self.channel_id})")
        
        # v3.13 M1.2c: event_fire 插件 hook **上移到 alarm 之前**, 让 suppress_alarm
        # 来得及作用. event_kind 三档: 1=OK / 2=NG / 其它=CUSTOM.
        # cycle_id 用上面缓存的 _event_cycle_id (self.current_cycle_id 已被 end_cycle 清零).
        # 注: end_cycle / 计数 / MES Hub / events_log 已经在上面完成, "事件已基本完整发生";
        # 仅 alarm / router / _last_event_time 三个尾部副作用还没发生, 这正是 hook 能影响的部分.
        if current_event_id == 1:
            _event_kind = "OK"
        elif current_event_id == 2:
            _event_kind = "NG"
        else:
            _event_kind = "CUSTOM"
        suppress_alarm = False
        try:
            from backend.plugin_system.hook_dispatch import fire_plugin_hook
            plugin_result = fire_plugin_hook("event_fire", "post_event", "post", {
                "channel_id": self.channel_id,
                "cycle_id": _event_cycle_id,
                "event_id": current_event_id,
                "event_name": event.get('name', ''),
                "event_kind": _event_kind,
                "reason": reason,
                "had_workpiece": had_workpiece,
                "should_warn_no_barcode": should_warn_no_barcode,
                "require_ack": require_ack,
            })
            # M1.2c: 消费 suppress_alarm 字段. 见 _resolve_event_fire_suppress_alarm 契约.
            # 无插件 / 无 handler / handler 不返 suppress_alarm → 安全侧默认 False, alarm 照常触发.
            suppress_alarm = _resolve_event_fire_suppress_alarm(plugin_result)
            if suppress_alarm:
                print(
                    f"[Plugin] event_fire suppress_alarm 生效, 跳过 alarm 触发 "
                    f"(channel_id={self.channel_id}, event_id={current_event_id}, "
                    f"reason={reason!r})",
                    flush=True,
                )
        except Exception as e:
            # fire_plugin_hook 自身已 swallow, 这里兜一层 import 层异常.
            # 异常路径下 suppress_alarm 维持初值 False — alarm 照常触发 (安全侧默认).
            print(f"[Plugin] event_fire hook error (isolated, main flow continues): {e}")

        # M1.2c: 触发报警器 (按 suppress_alarm 决定). 抽到 _dispatch_event_alarm 让
        # 测试可静态扫描 + 单独覆盖 alarm 触发逻辑.
        # 无插件场景下 suppress_alarm=False, _dispatch_event_alarm 与 M1.2c 重构前的
        # 274-280 行严格等价 (基线测试 test_trigger_event_baseline_M1_2c.py 守护).
        if not suppress_alarm:
            self._dispatch_event_alarm(current_event_id)

        # feat/multi-model-roi-link: 通知 InferenceRouter, 让监听本事件的副模型下次跑一次.
        # 投递两个 key: 'event_<id>' (稳定, 推荐) + 事件名 (人类可读, 兜底).
        # 副模型 schedule.events 中只要任一命中即可被调度.
        # M1.2c 不变: router 调用仍在 alarm 之后 (维持 v3.10.1 settle_dedup 锚点契约).
        try:
            router = getattr(self, '_router', None)
            if router is not None:
                event_name = event.get('name')
                if current_event_id is not None:
                    router.trigger_event(f"event_{current_event_id}")
                if event_name:
                    router.trigger_event(str(event_name))
        except Exception as e:
            print(f"router.trigger_event failed: {e}")

        # v3.10.x: 防重复结算时间窗口锚 - 仅在事件实际触发成功后记录,
        # 抑制路径 (settle_dedup / ng_protect / _pending_ack / event 未找到) 不更新,
        # 避免反复触发反复延长窗口.
        # M1.2c 不变: anchor 仍在 alarm + router 之后, 维持 v3.10.1 契约.
        self._last_event_time = time.time()

        return True

    def fire_external_event_response(self, event_id, reason: str,
                                     source: str = "external") -> bool:
        """外部子系统 (如包装箱结算) 借用一次"事件响应"——复用该事件配好的
        报警 (灯/蜂鸣) + 语音/Toast + 计数器联动, **不结束检测周期、不动 OK/NG
        周期统计、不走防重复结算守门**.

        与 ``_trigger_event`` 的本质区别: ``_trigger_event`` 代表"一个检测周期判定完了"
        (会 end_cycle 写库); 本方法只借事件的"响应面", 不打断正在跑的托盘检测.

        v3.22: 若映射到的事件在「项目事件设置」里标了 ``require_ack=True`` (需人工确认),
        则外部触发 (如包装漏箱 / 多装 / 缺油嘴 / 缺工单) 也会进入人工确认阻塞态 —— 整条
        检测线定格, 直到操作员 (或借管理员密码提权) 主动确认才解除. 默认 (require_ack=False)
        行为与改前严格一致 (只报警不定格).

        返回 True = 找到事件并已联动; False = 无项目配置 / 事件未找到.
        """
        if not self.project_config:
            return False
        events_config = self.project_config.get('events_config', []) or []
        event = None
        for e in events_config:
            eid = e.get('id')
            if eid == event_id or str(eid) == str(event_id):
                event = e
                break
        if not event:
            print(f"[fire_external_event] event not found: {event_id} (source={source}, reason={reason})")
            return False

        current_event_id = event.get('id')
        require_ack = bool(event.get('require_ack', False))
        ack_timeout_sec = max(0, int(event.get('ack_timeout_sec', 0) or 0))

        # 计数器联动 (复用事件 actions)
        counters_changed = False
        for action in event.get('actions', []) or []:
            counter_name = action.get('counter_name', '')
            value = action.get('delta', action.get('value', 1))
            if counter_name in self.counters:
                self.counters[counter_name] += value
                counters_changed = True
        if counters_changed:
            try:
                self._persist_counters()
            except Exception:
                pass

        # 事件日志 (前端读 → 弹 Toast + 语音播报); 标 source 便于前端区分来源
        self._event_seq += 1
        self.events_log.append({
            'seq': self._event_seq,
            'event_id': str(current_event_id),
            'event_name': event.get('name', ''),
            'reason': reason,
            'timestamp': time.time(),
            'show_notification': event.get('show_notification', False),
            'toast_id': event.get('toast_id', 'ng'),
            'had_workpiece': False,
            'should_warn_no_barcode': False,
            'require_ack': require_ack,
            'ack_timeout_sec': ack_timeout_sec,
            'source': source,
        })

        # 物理报警联动 (复用本通道 alarm 配置, 与检测 NG 共用 eventN 配置)
        self._dispatch_event_alarm(current_event_id)

        # v3.22: 事件标了"需人工确认" → 外部触发也进入阻塞态 (整条检测线定格).
        # 已在阻塞态则不重置 (保留首个触发事件), 避免后续异常刷掉原始原因.
        if require_ack and not getattr(self, '_pending_ack', False):
            self._pending_ack = True
            self._pending_ack_started_at = time.time()
            self._pending_ack_event_id = str(current_event_id)
            self._pending_ack_event_name = event.get('name', '')
            self._pending_ack_timeout_sec = ack_timeout_sec
            self._pending_ack_reason = reason
            print(f"[ack] (external/{source}) entering manual-ack blocked state: "
                  f"event={event.get('name', '')} (id={current_event_id}, "
                  f"timeout={ack_timeout_sec}s, channel_id={self.channel_id})")
        return True

    def _dispatch_event_alarm(self, current_event_id) -> None:
        """触发本通道 alarm (v3.13 M1.2c 抽出).

        与 M1.2c 重构前 _trigger_event 中 274-280 行的 alarm 触发逻辑**严格等价**:
        - event_type 格式 ``f'event{current_event_id}'`` 不变
        - channel_id 来自 self.channel_id 不变
        - alarm 串口失败 swallow + print 不变

        抽出原因:
        - 让 M1.2c 的 ``if not suppress_alarm: self._dispatch_event_alarm(...)`` 语义清晰
        - 让基线测试可静态扫描 / 单独 monkey-patch 验证 alarm 调用
        """
        try:
            from backend.api.alarm import alarm_router
            event_type = f'event{current_event_id}'
            alarm_router.trigger_alarm(event_type, channel_id=self.channel_id)
        except Exception as e:
            print(f"trigger alarm failed: {e}")

    # ============================================================
    # v3.23 NG 补做 (缺步骤延迟落账) — 守门 / 解析 / 挂起 / 解析
    # ============================================================

    def _pending_ack_keeps_cycle(self) -> bool:
        """当前挂起的人工确认事件是否配了「确认后保留周期」(ack_keep_cycle).

        用途: 违序警告这类"中途拦截"事件, 工人确认后应从断点继续补做,
        而不是丢弃整个在制周期重来。默认 False = 老"确认重做"语义, 零差异。
        """
        ev_id = getattr(self, '_pending_ack_event_id', None)
        if ev_id is None:
            return False
        cfg = self.project_config or {}
        for e in (cfg.get('events_config') or []):
            if str(e.get('id')) == str(ev_id):
                return bool(e.get('ack_keep_cycle', False))
        return False

    def _ack_release_keep_cycle(self) -> None:
        """仅解除人工确认定格, 保留在制周期与全部步骤运行时 (断点补做)。"""
        self._pending_ack = False
        self._pending_ack_started_at = None
        self._pending_ack_event_id = None
        self._pending_ack_event_name = None
        self._pending_ack_timeout_sec = 0
        self._pending_ack_reason = None

    def _should_defer_for_remediation(self, reason: str) -> bool:
        """本次 NG 是否应走"缺步骤延迟落账"挂起 (而非立刻落 NG).

        条件: 项目开了补步骤策略 (_ng_remediation.enabled and allow_step) 且 NG 原因
        是"缺步骤" (reason 含 '缺少' 或 '周期不完整'). 默认关 → 永远返回 False = 零差异.
        """
        rem = getattr(self, '_ng_remediation', None) or {}
        if not (rem.get('enabled') and rem.get('allow_step')):
            return False
        r = reason or ''
        return ('缺少' in r) or ('周期不完整' in r)

    def _parse_missing_steps(self, reason: str) -> list:
        """从 NG 原因里抠出缺的步骤标签列表.

        兼容两种文案: 顺序模式 "周期不完整，缺少: ['B']" / 检测模式 "缺少步骤: ['B']".
        正则抠不到时兜底用 "期望步骤 − 本周期已出现步骤" 反推 (与 NG TOP3 fallback 同口径),
        保证弹窗缺项明细 + 补步骤落库不空.
        """
        import re
        r = reason or ''
        out = []
        m = re.search(r'缺少(?:步骤)?[：:]\s*\[?([^\]]+)\]?', r)
        if m:
            for s in re.split(r'[,，]', m.group(1)):
                n = s.strip().strip("'\" ")
                if n:
                    out.append(n)
        if out:
            return out
        # 兜底: steps_config 期望步骤 (非替补) - 本周期已出现步骤, 保留 steps_config 顺序
        try:
            steps_config = (self.project_config or {}).get('steps_config', []) or []
            actual = set(getattr(self, 'current_cycle_steps', []) or [])
            return [s.get('label') for s in steps_config
                    if s.get('label') and not s.get('is_backup') and s.get('label') not in actual]
        except Exception:
            return []

    def _enter_step_remediation_hold(self, event, reason: str) -> None:
        """缺步骤 NG 挂起: 报警 + 事件日志提示工人, 但不 end_cycle / 不计数 / 不推 MES.

        复用 _pending_ack 字段进阻塞态 (整条检测线定格 + 前端弹确认窗); 额外写
        _pending_remediation 快照供前端展示缺项明细 + 后续 resolve_step_remediation 用.
        """
        current_event_id = event.get('id')
        missing = self._parse_missing_steps(reason)
        ack_timeout_sec = max(0, int(event.get('ack_timeout_sec', 0) or 0))

        self._pending_remediation = {
            'kind': 'missing_step',
            'missing': missing,
            'reason': reason,
            'event_id': str(current_event_id),
            'event_name': event.get('name', ''),
            'cycle_id': self.current_cycle_id,
            'created_at': time.time(),
        }
        # 进阻塞态 (复用人工确认字段, 前端 ack 窗据此弹出)
        self._pending_ack = True
        self._pending_ack_started_at = time.time()
        self._pending_ack_event_id = str(current_event_id)
        self._pending_ack_event_name = event.get('name', '')
        self._pending_ack_timeout_sec = ack_timeout_sec
        self._pending_ack_reason = reason

        # 事件日志 (Toast / 语音提示工人来处理) — 标 remediation 让前端区分
        self._event_seq += 1
        self.events_log.append({
            'seq': self._event_seq,
            'event_id': str(current_event_id),
            'event_name': event.get('name', ''),
            'reason': reason,
            'timestamp': time.time(),
            'show_notification': event.get('show_notification', False),
            'toast_id': event.get('toast_id', 'ng'),
            'had_workpiece': False,
            'should_warn_no_barcode': False,
            'require_ack': True,
            'ack_timeout_sec': ack_timeout_sec,
            'remediation': True,
        })
        # 物理报警照常 (现场需要被提示有件待处理), 但落账 (DB cycle / 计数 / MES) 全延迟
        self._dispatch_event_alarm(current_event_id)
        _rem_dbg("缺步骤NG延迟落账挂起",
                 f"ch={getattr(self, 'channel_id', '?')} cycle={self.current_cycle_id} "
                 f"missing={missing} reason={reason!r} 等待:补步骤/认NG/重做")
        print(f"[Rework] missing-step NG deferred pending: missing={missing} reason={reason!r} "
              f"(channel_id={self.channel_id}) 等待 补步骤/认NG/重做")

    def resolve_step_remediation(self, action: str, operator: str = None) -> dict:
        """解析缺步骤挂起 (供 ack 端点调用).

        action:
          - 'supplement_step': 信任人工补做, 把缺的步骤补进本周期序列 → 判 OK 落账
          - 'confirm_ng'     : 认这个 NG → 现在才落账 NG (绕 defer 守门一次)
          - 'redo'           : 丢弃在制周期, 等下一检测周期重检 (不留 NG 记录)
        返回 {resolved: bool, action: str, ...}
        """
        pend = getattr(self, '_pending_remediation', None)
        if not pend:
            return {"resolved": False, "reason": "no pending remediation"}
        missing = list(pend.get('missing') or [])
        reason = pend.get('reason') or ''

        # 先清挂起 + 阻塞态 (否则 _trigger_event 入口被 _pending_ack / _pending_remediation 拦)
        self._pending_remediation = None
        self._pending_ack = False
        self._pending_ack_started_at = None
        self._pending_ack_event_id = None
        self._pending_ack_event_name = None
        self._pending_ack_timeout_sec = 0
        self._pending_ack_reason = None

        if action == 'supplement_step':
            for lbl in missing:
                if lbl not in self.current_cycle_steps:
                    self.current_cycle_steps.append(lbl)
            op = f" by {operator}" if operator else ""
            _rem_dbg("补步骤判OK落账",
                     f"ch={getattr(self, 'channel_id', '?')} missing={missing} operator={operator}")
            self._trigger_event(1, f"补步骤{missing}后合格{op}")
            print(f"[Rework] missing step completed -> OK: missing={missing} operator={operator}")
            return {"resolved": True, "action": "supplement_step", "missing": missing}

        if action == 'confirm_ng':
            _rem_dbg("认NG落账",
                     f"ch={getattr(self, 'channel_id', '?')} reason={reason!r} operator={operator}")
            self._remediation_bypass = True
            try:
                self._trigger_event(2, reason)
            finally:
                self._remediation_bypass = False
            print(f"[Rework] accept NG recorded: reason={reason!r} operator={operator}")
            return {"resolved": True, "action": "confirm_ng"}

        # redo: 丢弃在制周期 (删行 + 清运行时), 等下一周期重检
        _rem_dbg("重做丢弃在制周期",
                 f"ch={getattr(self, 'channel_id', '?')} missing={missing} operator={operator}")
        self._discard_empty_cycle()
        self._clear_step_runtime_state()
        print(f"[Rework] redo discards in-progress cycle operator={operator}")
        return {"resolved": True, "action": "redo"}
