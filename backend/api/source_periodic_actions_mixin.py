"""v3.5.0 周期性强制动作 Mixin

业务场景：
  正常步骤序列 A-B-C-D，但每做完 N 轮还需要执行某个动作 E（如清洁治具、
  上油、换刀、校准等）。少于 N 轮做了 E 就重置，超过 N 轮没做就触发告警事件。

通用模型：
  pipeline_config.periodic_actions: List[Rule]
    Rule = {
      id, name, enabled,
      trigger_step_ids:        List[step_id]      多个 step 算 OR — 任一出现都算
      interval:                int                强制周期 N
      count_basis:             "all"|"good_only"|"ng_only"
      reset_policy:            "always"|"only_when_due"
      due_warning_event_id:    Optional[int]      counter == N 时弹一次提醒
      overdue_event_id:        Optional[int]      counter > N 时按 overdue_repeat 触发
      overdue_repeat:          "every_cycle"|"once"|"cooldown:N"
      channel_filter:          Optional[List[int]] 多通道时限定
    }

接入点：
  - apply_project_config 时调 _apply_periodic_actions(config)
  - end_cycle() commit 后调 _check_periodic_actions(step_sequence, is_good)

事件分发：
  本 mixin 不直接调 _trigger_event（那个会 end_cycle，会和已经结束的 cycle 冲突），
  而是用轻量的 _emit_periodic_notification — 弹 toast / 报警 / 计数器，
  但不结算 cycle。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from backend.core.config import DATA_DIR


class PeriodicActionsMixin:

    # ============================================================
    # 配置应用 + 持久化恢复
    # ============================================================

    def _apply_periodic_actions(self, config: Dict[str, Any]) -> None:
        """从 pipeline_config.periodic_actions 解析规则 + 恢复持久化计数"""
        pipeline_config = (config or {}).get('pipeline_config', {}) or {}
        raw_actions = pipeline_config.get('periodic_actions') or []

        steps_config = (config or {}).get('steps_config', []) or []
        id_to_label: Dict[Any, str] = {}
        for s in steps_config:
            sid = s.get('id')
            label = s.get('label', '')
            if sid is not None and label:
                id_to_label[sid] = label
                # 历史项目 step_id 可能用字符串保存，做双向兼容
                id_to_label[str(sid)] = label

        parsed: List[Dict[str, Any]] = []
        for raw in raw_actions:
            if not raw.get('enabled', True):
                continue
            rule_id = raw.get('id')
            if not rule_id:
                continue
            trigger_ids = raw.get('trigger_step_ids', []) or []
            trigger_labels = set()
            for sid in trigger_ids:
                lab = id_to_label.get(sid) or id_to_label.get(str(sid))
                if lab:
                    trigger_labels.add(lab)
            # 兼容前端可能直接传 trigger_step_labels
            for lab in raw.get('trigger_step_labels', []) or []:
                if lab:
                    trigger_labels.add(lab)
            if not trigger_labels:
                print(f"[PeriodicActions] 规则 '{raw.get('name')}' 无有效 trigger 步骤，跳过")
                continue

            try:
                interval = max(1, int(raw.get('interval', 20)))
            except (TypeError, ValueError):
                interval = 20

            parsed.append({
                'id': rule_id,
                'name': raw.get('name', f'规则_{rule_id}'),
                'trigger_labels': trigger_labels,
                'interval': interval,
                'count_basis': raw.get('count_basis', 'all'),
                'reset_policy': raw.get('reset_policy', 'always'),
                'due_warning_event_id': raw.get('due_warning_event_id'),
                'overdue_event_id': raw.get('overdue_event_id'),
                'overdue_repeat': raw.get('overdue_repeat', 'every_cycle'),
                'channel_filter': raw.get('channel_filter'),
                # v3.5.2: 是否在每次"开始检测"时立刻触发一次到期提醒
                # (典型场景: 开机首检 = 必须先做一次清洁/标定 才能正式投产).
                'run_on_start': bool(raw.get('run_on_start', False)),
                # 运行时状态（每条 rule 独立）
                'last_overdue_count': -1,
            })

        # v3.5.2: 临时诊断
        prev_counters = dict(getattr(self, '_periodic_counters', {}) or {})
        self._periodic_actions = parsed
        self._periodic_counters: Dict[str, int] = {r['id']: 0 for r in parsed}
        # v3.5.2: 开机首检 — 一组待判定的 rule_id 集合, 在第一个步骤完成时清算.
        # _run_periodic_actions_on_start 时填充, _check_periodic_actions_on_first_step 时清算并触发事件.
        if not hasattr(self, '_run_on_start_pending') or not isinstance(getattr(self, '_run_on_start_pending', None), set):
            self._run_on_start_pending = set()

        self._restore_periodic_counters(config)
        # v3.5.2: 临时诊断 — apply 前后 counter 变化
        if parsed:
            print(f"[PeriodicActions/DBG] _apply_periodic_actions ch{getattr(self, 'channel_id', 0)}: "
                  f"prev={prev_counters} → reset → restored={self._periodic_counters}")

        if parsed:
            print(f"[PeriodicActions] ch{getattr(self, 'channel_id', 0)} "
                  f"已加载 {len(parsed)} 条规则: " +
                  ", ".join(f"{r['name']}(每{r['interval']}轮)" for r in parsed))

    def _restore_periodic_counters(self, config: Dict[str, Any]) -> None:
        project_id = (config or {}).get('id')
        if not project_id:
            return
        path = self._periodic_counter_path(project_id)
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                saved = json.load(f) or {}
            for rule_id in list(self._periodic_counters.keys()):
                if rule_id in saved:
                    try:
                        self._periodic_counters[rule_id] = int(saved[rule_id])
                    except (TypeError, ValueError):
                        pass
            if self._periodic_counters:
                print(f"[PeriodicActions] ch{getattr(self, 'channel_id', 0)} "
                      f"恢复持久化计数: {self._periodic_counters}")
        except Exception as e:
            print(f"[PeriodicActions] 恢复计数失败: {e}")

    def _persist_periodic_counters(self) -> None:
        if not getattr(self, 'project_config', None):
            return
        project_id = self.project_config.get('id')
        if not project_id:
            return
        path = self._periodic_counter_path(project_id)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._periodic_counters, f, ensure_ascii=False)
        except Exception as e:
            print(f"[PeriodicActions] 持久化失败: {e}")

    def _periodic_counter_path(self, project_id) -> str:
        ch = getattr(self, 'channel_id', 0)
        return os.path.join(
            DATA_DIR, 'counters',
            f'project_{project_id}_ch{ch}_periodic.json'
        )

    # ============================================================
    # 主判定 — cycle_end 后调用
    # ============================================================

    def _check_periodic_actions(self, cycle_steps: List[str], is_good: bool) -> None:
        """每 cycle_end 后调一次。遍历所有规则 → 计数 / 重置 / 触发事件"""
        rules = getattr(self, '_periodic_actions', None)
        # v3.5.2: 临时诊断日志
        print(f"[PeriodicActions/DBG] _check_periodic_actions called: "
              f"rules_count={len(rules) if rules else 0}, "
              f"cycle_steps={cycle_steps}, is_good={is_good}, "
              f"counters={getattr(self, '_periodic_counters', None)}")
        if not rules:
            return

        cycle_step_set = set(cycle_steps or [])
        changed = False

        for rule in rules:
            channel_filter = rule.get('channel_filter')
            if channel_filter and getattr(self, 'channel_id', 0) not in channel_filter:
                continue

            # count_basis 决定本轮是否参与累加
            cb = rule['count_basis']
            counts_this_cycle = (
                cb == 'all'
                or (cb == 'good_only' and is_good)
                or (cb == 'ng_only' and not is_good)
            )

            did_trigger = bool(cycle_step_set & rule['trigger_labels'])

            counter = self._periodic_counters.get(rule['id'], 0)
            interval = rule['interval']

            # ---- 重置判定 ----
            if did_trigger:
                if rule['reset_policy'] == 'always':
                    do_reset = True
                else:  # only_when_due — 必须到期了做才算
                    do_reset = counter >= interval

                if do_reset:
                    self._periodic_counters[rule['id']] = 0
                    rule['last_overdue_count'] = -1  # 重置 cooldown 状态
                    changed = True
                    print(f"[PeriodicActions] '{rule['name']}' 检测到完成动作，"
                          f"counter {counter} → 0 (policy={rule['reset_policy']})")
                    continue  # 抑制本次告警

            # ---- 累加 ----
            if counts_this_cycle:
                counter += 1
                self._periodic_counters[rule['id']] = counter
                changed = True
                # v3.5.2: 临时诊断
                print(f"[PeriodicActions/DBG] '{rule['name']}' counter += 1 → {counter} "
                      f"(interval={interval}, did_trigger={did_trigger})")

            # ---- 通知触发 ----
            if counter == interval and rule.get('due_warning_event_id'):
                self._emit_periodic_notification(
                    rule['due_warning_event_id'],
                    f"{rule['name']} 已到期 ({counter}/{interval})，请尽快执行"
                )
            elif counter > interval and rule.get('overdue_event_id'):
                if self._should_trigger_overdue(rule, counter):
                    self._emit_periodic_notification(
                        rule['overdue_event_id'],
                        f"{rule['name']} 已超期 {counter - interval} 轮 "
                        f"(累计 {counter}/{interval})"
                    )
                    rule['last_overdue_count'] = counter

        if changed:
            self._persist_periodic_counters()

    # ============================================================
    # 开机首检 — start_detection 时调用
    # ============================================================

    def _run_periodic_actions_on_start(self) -> None:
        """每次 start_detection / resume / resume_inference 调一次.

        把所有 `run_on_start=true` 的规则 counter 推到 interval (= 到期),
        让 Monitor 进度条立刻显示"已到期 20/20"红黄状态. 但 **不立即触发**
        toast / 报警 / 计数器事件 — 等开机后**第一个步骤完成**时, 在
        `_check_periodic_actions_on_first_step(label)` 里清算:

        - 第一个完成的步骤 ∈ trigger_labels → 静默 reset, 表示"客户记得做首件".
        - 第一个完成的步骤 ∉ trigger_labels → 立刻 emit overdue (或 due) 事件,
          告诉客户"先做首件再继续".

        这就是 v3.5.2 调整后的语义 — 客户做的"第一个动作"不是 trigger_step
        就立即提醒. 多次 start (比如停了又开) 都会重新把规则填回 pending 集合,
        重新让 Monitor 进入"已到期"状态等待下一次判定.
        """
        rules = getattr(self, '_periodic_actions', None)
        if not rules:
            return

        on_start_rules = [r for r in rules if r.get('run_on_start')]
        if not on_start_rules:
            return

        if not hasattr(self, '_run_on_start_pending') or not isinstance(self._run_on_start_pending, set):
            self._run_on_start_pending = set()

        changed = False
        for rule in on_start_rules:
            channel_filter = rule.get('channel_filter')
            if channel_filter and getattr(self, 'channel_id', 0) not in channel_filter:
                continue
            counter_before = self._periodic_counters.get(rule['id'], 0)
            interval = rule['interval']
            if counter_before < interval:
                self._periodic_counters[rule['id']] = interval
                rule['last_overdue_count'] = -1  # 重置 cooldown 让首次 overdue 能弹
                changed = True

            self._run_on_start_pending.add(rule['id'])
            print(f"[PeriodicActions] '{rule['name']}' run_on_start: "
                  f"counter {counter_before} → {self._periodic_counters[rule['id']]}, "
                  f"等待首个步骤判定 (开机首检, pending={list(self._run_on_start_pending)})")

        if changed:
            try:
                self._persist_periodic_counters()
            except Exception as e:
                print(f"[PeriodicActions] run_on_start 持久化失败: {e}")

    def _check_periodic_actions_on_first_step(self, step_label: str) -> None:
        """开机首检判定 — 在每个步骤完成时调用.

        遍历 `_run_on_start_pending` 中的规则:
        - step_label ∈ rule.trigger_labels → 静默 reset (counter=0)
        - 否则 → emit overdue / due 事件
        无论哪条路径, 该 rule 都从 pending 集合移除 (一次性判定).
        """
        if not step_label:
            return
        pending = getattr(self, '_run_on_start_pending', None)
        if not pending:
            return

        rules = getattr(self, '_periodic_actions', None) or []
        # 用 dict 加速查找
        rules_by_id = {r['id']: r for r in rules}
        changed = False

        for rid in list(pending):
            rule = rules_by_id.get(rid)
            if not rule:
                pending.discard(rid)
                continue

            channel_filter = rule.get('channel_filter')
            if channel_filter and getattr(self, 'channel_id', 0) not in channel_filter:
                pending.discard(rid)
                continue

            if step_label in rule['trigger_labels']:
                # 客户做了首件 trigger_step — 静默重置
                self._periodic_counters[rid] = 0
                rule['last_overdue_count'] = -1
                changed = True
                print(f"[PeriodicActions] '{rule['name']}' 开机首检通过: "
                      f"客户做了 '{step_label}', counter → 0")
            else:
                # 不是 trigger_step — 立即触发提醒
                target_event = rule.get('overdue_event_id') or rule.get('due_warning_event_id')
                if target_event:
                    self._emit_periodic_notification(
                        target_event,
                        f"{rule['name']} 开机首检：第一个动作是 '{step_label}', "
                        f"请先执行首件检"
                    )
                else:
                    print(f"[PeriodicActions] '{rule['name']}' 首检失败 ('{step_label}' "
                          f"非 trigger), 但未配事件, 仅静默")
            pending.discard(rid)

        if changed:
            try:
                self._persist_periodic_counters()
            except Exception as e:
                print(f"[PeriodicActions] on_first_step 持久化失败: {e}")

    def _should_trigger_overdue(self, rule: Dict[str, Any], counter: int) -> bool:
        """根据 overdue_repeat 决定是否触发本次 overdue 事件"""
        repeat = (rule.get('overdue_repeat') or 'every_cycle').strip()
        last = rule.get('last_overdue_count', -1)

        if repeat == 'every_cycle':
            return True
        if repeat == 'once':
            return last < 0
        if repeat.startswith('cooldown:'):
            try:
                gap = int(repeat.split(':', 1)[1])
            except (IndexError, ValueError):
                return True
            return last < 0 or (counter - last) >= max(1, gap)
        return True

    # ============================================================
    # 轻量事件分发 — 不调 end_cycle，避免与已结算的 cycle 冲突
    # ============================================================

    def _emit_periodic_notification(self, event_id: Any, reason: str) -> None:
        """弹 toast + 触发报警 + 执行 counter actions

        与 _trigger_event 的差别：
        - 不调 self.end_cycle()（cycle 已经结束）
        - 不动 cycle_times / NG步骤计数 / NG TOP3
        - 标记 source='periodic_action'，前端 toast 可以据此区分样式
        """
        if not getattr(self, 'project_config', None):
            return
        events_config = self.project_config.get('events_config', []) or []

        event = None
        for e in events_config:
            eid = e.get('id')
            if eid == event_id or str(eid) == str(event_id):
                event = e
                break
        if not event:
            print(f"[PeriodicActions] event_id={event_id} 未在 events_config 中找到")
            return

        # 写入 events_log（前端轮询拿 → toast / 语音）
        self._event_seq = getattr(self, '_event_seq', 0) + 1
        if not hasattr(self, 'events_log') or self.events_log is None:
            self.events_log = []
        self.events_log.append({
            'seq': self._event_seq,
            'event_id': str(event.get('id', event_id)),
            'event_name': event.get('name', ''),
            'reason': reason,
            'timestamp': time.time(),
            'show_notification': event.get('show_notification', True),
            'toast_id': event.get('toast_id', 'ng'),
            'had_workpiece': False,
            # v3.5.2: 周期性强制动作和扫码绑定无关, 后端权威告知前端"不要弹未绑码 toast"
            'should_warn_no_barcode': False,
            'source': 'periodic_action',
        })

        # 执行该事件配的 counter actions
        actions = event.get('actions', []) or []
        counters_changed = False
        for action in actions:
            counter_name = action.get('counter_name', '')
            value = action.get('delta', action.get('value', 1))
            if counter_name and counter_name in self.counters:
                try:
                    self.counters[counter_name] += value
                    counters_changed = True
                except Exception:
                    pass
        if counters_changed and hasattr(self, '_persist_counters'):
            try:
                self._persist_counters()
            except Exception:
                pass

        # 触发报警器（沿用 event{N} 路由）
        try:
            from backend.api.alarm import alarm_router
            alarm_router.trigger_alarm(
                f"event{event.get('id')}",
                channel_id=getattr(self, 'channel_id', 0),
            )
        except Exception as e:
            print(f"[PeriodicActions] 触发报警失败: {e}")

        print(f"[PeriodicActions] 触发事件 #{event.get('id')} "
              f"({event.get('name', '')}): {reason}")

    # ============================================================
    # 暴露给前端 Monitor 的状态查询
    # ============================================================

    def get_periodic_actions_status(self) -> List[Dict[str, Any]]:
        """返回每条规则的当前进度 — 给 get_detection_results 用"""
        rules = getattr(self, '_periodic_actions', None)
        if not rules:
            return []
        out = []
        for rule in rules:
            counter = self._periodic_counters.get(rule['id'], 0)
            interval = rule['interval']
            if counter < interval:
                state = 'ok'
                remaining = interval - counter
            elif counter == interval:
                state = 'due'
                remaining = 0
            else:
                state = 'overdue'
                remaining = -(counter - interval)
            out.append({
                'id': rule['id'],
                'name': rule['name'],
                'counter': counter,
                'interval': interval,
                'state': state,            # ok | due | overdue
                'remaining': remaining,    # 距离到期还有几轮（负数=已超期几轮）
                'count_basis': rule['count_basis'],
                'reset_policy': rule['reset_policy'],
                'trigger_labels': sorted(rule['trigger_labels']),
            })
        return out
