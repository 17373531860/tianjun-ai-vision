"""v3.5.0 周期性强制动作 Mixin

业务场景：
  正常步骤序列 A-B-C-D，但每做完 N 轮还需要执行某个动作 E（如清洁治具、
  上油、换刀、校准等）。少于 N 轮做了 E 就重置，超过 N 轮没做就触发告警事件。

通用模型：
  pipeline_config.periodic_actions: List[Rule]
    Rule = {
      id, name, enabled,
      trigger_step_ids:        List[step_id]      多个 step 算 OR — 任一出现都算
      interval:                int                强制周期 N (=0 关闭按次数触发)
      time_interval_seconds:   int                超时秒数 N (=0 关闭按时间触发, v3.7.4)
      count_basis:             "all"|"good_only"|"ng_only"
      reset_policy:            "always"|"only_when_due"
      due_warning_event_id:    Optional[int]      到期时弹一次提醒
      overdue_event_id:        Optional[int]      超期后按 overdue_repeat 触发
      overdue_repeat:          "every_cycle"|"once"|"cooldown:N"
      channel_filter:          Optional[List[int]] 多通道时限定
    }

  ★ v3.7.4: interval (按次数) 与 time_interval_seconds (按时间) 是 OR 关系,
    谁先到期谁先触发. 任一为 0 视为该维度关闭. 完成动作 (做了 trigger_step)
    会同时重置 counter=0 + last_done_ts=now.

接入点：
  - apply_project_config 时调 _apply_periodic_actions(config)
  - end_cycle() commit 后调 _check_periodic_actions(step_sequence, is_good)
  - inference_loop 主循环每 5 秒 throttle 调一次 _check_periodic_actions_time_only(now)
    (v3.7.4: 即使生产停了, 只要 detection 在跑, 时间到期也会主动触发)

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
                interval = max(0, int(raw.get('interval', 20)))
            except (TypeError, ValueError):
                interval = 20

            # v3.7.4: time_interval_seconds — 按时间触发 (0=关闭).
            # 注意 0 是合法的（关闭该维度），不要 max(1,...) 强制最小值.
            try:
                time_interval = max(0, int(raw.get('time_interval_seconds', 0) or 0))
            except (TypeError, ValueError):
                time_interval = 0

            # 至少要开一种维度, 否则规则等同于"什么都不做"
            if interval <= 0 and time_interval <= 0:
                print(f"[PeriodicActions] 规则 '{raw.get('name')}' interval 和 "
                      f"time_interval_seconds 都为 0, 跳过")
                continue

            parsed.append({
                'id': rule_id,
                'name': raw.get('name', f'规则_{rule_id}'),
                'trigger_labels': trigger_labels,
                'interval': interval,
                'time_interval_seconds': time_interval,  # v3.7.4
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
                # v3.7.4: 时间维度的"上次超期时秒数差", 用于 cooldown / once 节流
                'last_overdue_time_gap': -1.0,
                # v3.8.x: 'continuous:N' 模式专用 — 上次发 overdue 通知的绝对时间戳。
                # 持续触发模式按"现在 - last_overdue_emit_ts >= N" 判定要不要再发,
                # 跟次数/秒数 gap 那套独立。0.0 = 从未发过, 一旦超期立刻发首次。
                'last_overdue_emit_ts': 0.0,
            })

        # v3.5.2: 临时诊断
        prev_counters = dict(getattr(self, '_periodic_counters', {}) or {})
        self._periodic_actions = parsed
        self._periodic_counters: Dict[str, int] = {r['id']: 0 for r in parsed}
        # v3.7.4: 时间维度状态 — 上次"完成动作"的时间戳. 初始 = 当前时间,
        # 表示"刚开始, 还没超时". 持久化文件里如果存了就 _restore_periodic_counters
        # 覆盖回来 (跨重启保持). 节流标志 _last_periodic_time_check 给 inference loop 用.
        now_init = time.time()
        self._periodic_last_done_ts: Dict[str, float] = {r['id']: now_init for r in parsed}
        self._last_periodic_time_check = now_init
        # v3.5.2: 开机首检 — 一组待判定的 rule_id 集合, 在第一个步骤完成时清算.
        # _run_periodic_actions_on_start 时填充, _check_periodic_actions_on_first_step 时清算并触发事件.
        if not hasattr(self, '_run_on_start_pending') or not isinstance(getattr(self, '_run_on_start_pending', None), set):
            self._run_on_start_pending = set()

        # v3.7.5: 顺序模式下"旁路保养动作"独立观察账本 — 不进 cycle.step_sequence
        # 也能让 _check_periodic_actions 看到, 修复 FIX-381 副作用导致清不了零.
        # 详见 _observe_periodic_trigger / _check_periodic_actions 内的合并逻辑.
        if not hasattr(self, '_periodic_triggers_observed') or not isinstance(getattr(self, '_periodic_triggers_observed', None), set):
            self._periodic_triggers_observed = set()

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
        """从落盘文件恢复 counter + last_done_ts.

        v3.7.4 起持久化格式从 ``{rule_id: counter}`` 升级为
        ``{"counters": {rule_id: counter}, "last_done_ts": {rule_id: ts}}``,
        但向后兼容老格式 (老格式只恢复 counter).
        """
        project_id = (config or {}).get('id')
        if not project_id:
            return
        path = self._periodic_counter_path(project_id)
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                saved = json.load(f) or {}

            # 兼容判定: 新格式有 "counters" 键, 老格式直接是 {rule_id: int}
            if isinstance(saved, dict) and 'counters' in saved and isinstance(saved.get('counters'), dict):
                saved_counters = saved.get('counters') or {}
                saved_ts = saved.get('last_done_ts') or {}
            else:
                saved_counters = saved if isinstance(saved, dict) else {}
                saved_ts = {}

            for rule_id in list(self._periodic_counters.keys()):
                if rule_id in saved_counters:
                    try:
                        self._periodic_counters[rule_id] = int(saved_counters[rule_id])
                    except (TypeError, ValueError):
                        pass
                if rule_id in saved_ts:
                    try:
                        self._periodic_last_done_ts[rule_id] = float(saved_ts[rule_id])
                    except (TypeError, ValueError):
                        pass

            if self._periodic_counters:
                print(f"[PeriodicActions] ch{getattr(self, 'channel_id', 0)} "
                      f"恢复持久化计数: counters={self._periodic_counters} "
                      f"last_done_ts={ {k: round(v, 1) for k, v in self._periodic_last_done_ts.items()} }")
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
            # v3.7.4: 新格式同时存 counters + last_done_ts.
            payload = {
                'counters': dict(self._periodic_counters),
                'last_done_ts': dict(getattr(self, '_periodic_last_done_ts', {}) or {}),
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
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
        """每 cycle_end 后调一次。遍历所有规则 → 计数 / 重置 / 触发事件.

        v3.7.4: 同时检查时间维度. 完成动作 (做了 trigger_step) 会同时重置
        counter=0 + last_done_ts=now. 触发判定: 按次数 OR 按时间到期都会触发.
        """
        rules = getattr(self, '_periodic_actions', None)
        # v3.5.2: 临时诊断日志
        print(f"[PeriodicActions/DBG] _check_periodic_actions called: "
              f"rules_count={len(rules) if rules else 0}, "
              f"cycle_steps={cycle_steps}, is_good={is_good}, "
              f"counters={getattr(self, '_periodic_counters', None)}")
        if not rules:
            return

        # v3.7.5: 合并 cycle 记录 + 旁路观察账本一起算 did_trigger.
        # 旁路账本 (_periodic_triggers_observed) 由 _observe_periodic_trigger 在
        # process_step_detection 内填, 专门处理顺序模式下 FIX-381 拦截的保养动作.
        observed_triggers = set(getattr(self, '_periodic_triggers_observed', None) or set())
        cycle_step_set = set(cycle_steps or []) | observed_triggers
        now = time.time()
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
            time_interval = rule.get('time_interval_seconds', 0)
            last_done = self._periodic_last_done_ts.get(rule['id'], now)
            time_gap = now - last_done

            # ---- 重置判定 ----
            if did_trigger:
                # v3.7.4: only_when_due 现在要看"任一维度到期"
                if rule['reset_policy'] == 'always':
                    do_reset = True
                else:  # only_when_due — 必须到期了做才算
                    count_due = interval > 0 and counter >= interval
                    time_due = time_interval > 0 and time_gap >= time_interval
                    do_reset = count_due or time_due

                if do_reset:
                    self._periodic_counters[rule['id']] = 0
                    self._periodic_last_done_ts[rule['id']] = now  # v3.7.4
                    rule['last_overdue_count'] = -1  # 重置 cooldown 状态
                    rule['last_overdue_time_gap'] = -1.0  # v3.7.4
                    rule['last_overdue_emit_ts'] = 0.0  # v3.8.x: 'continuous:N' 节流
                    changed = True
                    print(f"[PeriodicActions] '{rule['name']}' 检测到完成动作，"
                          f"counter {counter} → 0, time_gap {time_gap:.0f}s → 0 "
                          f"(policy={rule['reset_policy']})")
                    continue  # 抑制本次告警

            # ---- 累加 ----
            if counts_this_cycle:
                counter += 1
                self._periodic_counters[rule['id']] = counter
                changed = True
                # v3.5.2: 临时诊断
                print(f"[PeriodicActions/DBG] '{rule['name']}' counter += 1 → {counter} "
                      f"(interval={interval}, did_trigger={did_trigger})")

            # ---- 通知触发: 按次数 OR 按时间, 谁先到期谁触发 ----
            self._maybe_emit_periodic(rule, counter, time_gap)

        if changed:
            self._persist_periodic_counters()

        # v3.7.5: 判定完, 清空本轮旁路观察账本 (下一轮重头记).
        # 这里清而不是 cycle_start 清, 是因为 _observe_periodic_trigger 在步骤
        # 出现的一瞬间就记, 而 cycle_start 时序晚于这个时刻.
        observed_book = getattr(self, '_periodic_triggers_observed', None)
        if isinstance(observed_book, set):
            observed_book.clear()

    def _check_periodic_actions_time_only(self, now: Optional[float] = None) -> None:
        """v3.7.4: 由 inference_loop 主循环每 1 秒 throttle 调一次 (v3.8.x 改为 1 秒).

        独立于 cycle_end, 解决两种场景:
        - v3.7.4 原意: "生产停了但仍在检测中" 时, 按时间到期触发提醒
          (例如客户工人下班吃饭半小时不开工)
        - v3.8.x 新增: continuous:N 模式 — 超期后持续按 N 秒间隔反复触发,
          不论"按次数超期"还是"按时间超期"都走

        本方法只触发 due/overdue 事件, **不动 counter**, 不动 last_done_ts —
        那些只在 _check_periodic_actions (cycle_end 路径) 或检测到 trigger_step 时变化.
        """
        rules = getattr(self, '_periodic_actions', None)
        if not rules:
            return
        if now is None:
            now = time.time()

        for rule in rules:
            channel_filter = rule.get('channel_filter')
            if channel_filter and getattr(self, 'channel_id', 0) not in channel_filter:
                continue

            interval = rule['interval']
            time_interval = rule.get('time_interval_seconds', 0)
            repeat = (rule.get('overdue_repeat') or 'every_cycle').strip()
            is_continuous = repeat.startswith('continuous:')

            counter = self._periodic_counters.get(rule['id'], 0)
            last_done = self._periodic_last_done_ts.get(rule['id'], now)
            time_gap = now - last_done

            count_overdue = interval > 0 and counter > interval
            time_overdue = time_interval > 0 and time_gap > time_interval

            # v3.8.x: continuous 模式 — 任一维度超期就进 _maybe_emit_periodic 走节流.
            # 这样客户只配了"按次数 20 轮 + continuous:2s" 时, counter 超 20 后
            # 每 2 秒响一次, 不再受"时间维度未开/未到"两道守门拦住.
            if is_continuous:
                if not (count_overdue or time_overdue):
                    continue
            else:
                # 老行为 (每 cycle/once/cooldown): 仅在"时间维度开启 + 已到期"时介入,
                # 避免每秒被无故触发. 这些模式的次数维度判定走 _check_periodic_actions
                # (cycle_end 路径).
                if time_interval <= 0:
                    continue
                if time_gap < time_interval:
                    continue

            # 复用 _maybe_emit_periodic — 它内部判定 cooldown / once / continuous 节流
            self._maybe_emit_periodic(rule, counter, time_gap)

    def _maybe_emit_periodic(self, rule: Dict[str, Any], counter: int, time_gap: float) -> None:
        """统一的 due/overdue 触发判定 — count + time 两维度 OR.

        优先级: overdue (任一维度超期) > due (任一维度刚到期, 且没超期)
        """
        interval = rule['interval']
        time_interval = rule.get('time_interval_seconds', 0)

        # 维度状态
        count_overdue = interval > 0 and counter > interval
        count_due = interval > 0 and counter == interval
        time_overdue = time_interval > 0 and time_gap > time_interval
        time_due = time_interval > 0 and time_gap >= time_interval and not time_overdue

        if (count_overdue or time_overdue) and rule.get('overdue_event_id'):
            # 优先取更"严重"的描述
            if count_overdue and time_overdue:
                reason = (f"{rule['name']} 已超期 — 次数维度 {counter - interval} 轮 / "
                          f"时间维度 {time_gap - time_interval:.0f} 秒 "
                          f"(累计 {counter}/{interval} 轮, {time_gap:.0f}/{time_interval} 秒)")
            elif count_overdue:
                reason = (f"{rule['name']} 已超期 {counter - interval} 轮 "
                          f"(累计 {counter}/{interval})")
            else:
                reason = (f"{rule['name']} 已超期 {time_gap - time_interval:.0f} 秒 "
                          f"(距上次 {time_gap:.0f}s, 阈值 {time_interval}s)")
            if self._should_trigger_overdue_v2(rule, counter, time_gap):
                self._emit_periodic_notification(rule['overdue_event_id'], reason)
                rule['last_overdue_count'] = counter
                rule['last_overdue_time_gap'] = time_gap
                # v3.8.x: 'continuous:N' 节流锚点 — 每次实际发完 overdue 才更新,
                # 让下次 _should_trigger_overdue_v2 用 now - 这个值 与 N 比.
                rule['last_overdue_emit_ts'] = time.time()
        elif (count_due or time_due) and rule.get('due_warning_event_id'):
            if count_due and time_due:
                reason = (f"{rule['name']} 已到期 (次数 {counter}/{interval}, "
                          f"时间 {time_gap:.0f}/{time_interval} 秒)，请尽快执行")
            elif count_due:
                reason = f"{rule['name']} 已到期 ({counter}/{interval})，请尽快执行"
            else:
                reason = f"{rule['name']} 已到期 ({time_gap:.0f}/{time_interval} 秒)，请尽快执行"
            # due 事件每个维度只在"刚到期"时弹一次, 节流走 last_overdue_count/time_gap
            already_signaled = (
                (count_due and rule.get('last_overdue_count', -1) >= counter) or
                (time_due and rule.get('last_overdue_time_gap', -1.0) >= time_gap - 1.0)
            )
            if not already_signaled:
                self._emit_periodic_notification(rule['due_warning_event_id'], reason)
                if count_due:
                    rule['last_overdue_count'] = counter
                if time_due:
                    rule['last_overdue_time_gap'] = time_gap

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
                # v3.7.4: 时间维度同步重置
                if hasattr(self, '_periodic_last_done_ts'):
                    self._periodic_last_done_ts[rid] = time.time()
                rule['last_overdue_count'] = -1
                rule['last_overdue_time_gap'] = -1.0
                rule['last_overdue_emit_ts'] = 0.0  # v3.8.x: 'continuous:N' 节流
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
        """根据 overdue_repeat 决定是否触发本次 overdue 事件 (历史 API, 仅看 counter).

        v3.7.4 起 _maybe_emit_periodic 走 _should_trigger_overdue_v2 (兼顾时间维度).
        本方法保留供老调用方使用.
        """
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

    def _should_trigger_overdue_v2(self, rule: Dict[str, Any], counter: int, time_gap: float) -> bool:
        """v3.7.4: overdue 节流判定 — 兼顾次数维度与时间维度.

        - every_cycle: 每次调都触发 (注意 _check_periodic_actions_time_only 由 inference loop
          每 1 秒调一次, v3.8.x 把 throttle 从 5s 降到 1s 以支持 continuous:N 小 N 值).
        - once:        只要任一维度还没触发过 overdue 就触发, 之后永久静默直到 reset.
        - cooldown:N:  任一维度的进度与 last 的差值 ≥ N 才触发. N 对次数 = 轮数,
                       对时间 = 秒数 (复用同一个 N, 简化配置).
        - continuous:N (v3.8.x): 超期后**每 N 秒持续触发一次**, 直到客户做了 trigger_step
          被 reset 路径清掉. 节流锚点是绝对时间戳 last_overdue_emit_ts (不是 time_gap),
          这样不论"按次数超期"还是"按时间超期"进来, 都按 N 秒固定间隔反复响.
          典型场景: 漏做"未压墨"等保养动作时, 客户希望红灯响个不停直到处理.
        """
        repeat = (rule.get('overdue_repeat') or 'every_cycle').strip()
        last_cnt = rule.get('last_overdue_count', -1)
        last_gap = rule.get('last_overdue_time_gap', -1.0)
        interval = rule['interval']
        time_interval = rule.get('time_interval_seconds', 0)

        if repeat == 'every_cycle':
            return True
        if repeat == 'once':
            count_first = (interval > 0 and counter > interval and last_cnt < 0)
            time_first = (time_interval > 0 and time_gap > time_interval and last_gap < 0)
            return count_first or time_first
        if repeat.startswith('cooldown:'):
            try:
                gap = int(repeat.split(':', 1)[1])
            except (IndexError, ValueError):
                return True
            gap = max(1, gap)
            count_ok = (interval > 0 and counter > interval
                        and (last_cnt < 0 or (counter - last_cnt) >= gap))
            time_ok = (time_interval > 0 and time_gap > time_interval
                       and (last_gap < 0 or (time_gap - last_gap) >= gap))
            return count_ok or time_ok
        if repeat.startswith('continuous:'):
            try:
                interval_sec = float(repeat.split(':', 1)[1])
            except (IndexError, ValueError):
                interval_sec = 5.0
            interval_sec = max(0.5, interval_sec)
            last_emit = float(rule.get('last_overdue_emit_ts', 0.0) or 0.0)
            now = time.time()
            # 首次超期 (last_emit==0) 立刻发; 之后每 interval_sec 秒发一次.
            return last_emit <= 0 or (now - last_emit) >= interval_sec
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

        v3.9.x: 也读 event 的 require_ack 字段, 进入与 _trigger_event 等价的人工
        确认阻塞态. 这条路径独立于主事件分发, 之前漏接了 require_ack 处理, 表现
        为"周期性强制动作触发的事件不弹确认框" (用户实际场景).
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

        # v3.9.x: 已经在阻塞态 → 丢弃后续周期性事件, 等工人确认完再说
        # (与 _trigger_event 入口同样的早退保护)
        if getattr(self, '_pending_ack', False):
            print(f"[PeriodicActions] 阻塞中 (等待人工确认), 丢弃事件 "
                  f"event_id={event_id}, reason={reason}")
            return

        # v3.9.x: 读 require_ack / ack_timeout_sec
        require_ack = bool(event.get('require_ack', False))
        ack_timeout_sec = max(0, int(event.get('ack_timeout_sec', 0) or 0))

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
            # v3.9.x: 把 require_ack 字段也带到日志里, 前端可据此区分需要确认的事件
            'require_ack': require_ack,
            'ack_timeout_sec': ack_timeout_sec,
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

        # v3.9.x: 进入人工确认阻塞态 (与 _trigger_event 末尾等价).
        # 阻塞门会在 _update_step_stats 入口和 _capture_loop 写帧前生效, 状态机
        # 与画面同步定格, 直到工人调 /detection/ack-event 主动解除.
        if require_ack:
            self._pending_ack = True
            self._pending_ack_started_at = time.time()
            self._pending_ack_event_id = str(event.get('id', event_id))
            self._pending_ack_event_name = event.get('name', '')
            self._pending_ack_timeout_sec = ack_timeout_sec
            self._pending_ack_reason = reason
            print(f"[ack] (周期性) 进入人工确认阻塞态: event={self._pending_ack_event_name} "
                  f"(id={self._pending_ack_event_id}, timeout={ack_timeout_sec}s, "
                  f"channel_id={getattr(self, 'channel_id', 0)})")

    # ============================================================
    # 暴露给前端 Monitor 的状态查询
    # ============================================================

    # ============================================================
    # 手动重置 — 任何时候都可调
    # ============================================================

    def reset_periodic_counter(self, rule_id: Optional[str] = None) -> Dict[str, Any]:
        """把周期性强制动作计数器清回 0。

        rule_id=None  → 重置所有规则
        rule_id=具体  → 只重置指定规则
        返回 {reset: [rule_id, ...]} 给路由层回写。
        与 reset_stats 不同：本方法不动产量计数器、step 统计、cycle 状态、
        events_log，只动周期性强制动作 counter / last_overdue_count /
        run_on_start_pending。允许在检测运行中调用。
        """
        counters = getattr(self, '_periodic_counters', None)
        if not isinstance(counters, dict):
            return {'reset': []}
        rules = getattr(self, '_periodic_actions', []) or []
        rules_by_id = {r['id']: r for r in rules}

        if rule_id is None:
            target_ids = list(counters.keys())
        else:
            if rule_id not in counters:
                return {'reset': []}
            target_ids = [rule_id]

        # v3.7.4: reset 时同步重置时间维度 — 视为"刚做完了一次"
        now = time.time()
        last_done_map = getattr(self, '_periodic_last_done_ts', None)
        if not isinstance(last_done_map, dict):
            self._periodic_last_done_ts = {}
            last_done_map = self._periodic_last_done_ts

        for rid in target_ids:
            counters[rid] = 0
            last_done_map[rid] = now
            rule = rules_by_id.get(rid)
            if rule is not None:
                rule['last_overdue_count'] = -1
                rule['last_overdue_time_gap'] = -1.0
                rule['last_overdue_emit_ts'] = 0.0  # v3.8.x: 'continuous:N' 节流
            pending = getattr(self, '_run_on_start_pending', None)
            if isinstance(pending, set):
                pending.discard(rid)

        try:
            self._persist_periodic_counters()
        except Exception as e:
            print(f"[PeriodicActions] reset_periodic_counter 持久化失败: {e}")

        print(f"[PeriodicActions] ch{getattr(self, 'channel_id', 0)} "
              f"手动重置: {target_ids}")
        return {'reset': target_ids}

    def get_periodic_actions_status(self) -> List[Dict[str, Any]]:
        """返回每条规则的当前进度 — 给 get_detection_results 用.

        v3.7.4 新增字段: time_interval_seconds / time_elapsed / time_remaining /
        time_state. 整体 state 取次数维度与时间维度中"更严重"的那个
        (overdue > due > ok), 让 Monitor UI 一眼看到最紧急的提示.
        """
        rules = getattr(self, '_periodic_actions', None)
        if not rules:
            return []
        now = time.time()
        last_done_map = getattr(self, '_periodic_last_done_ts', {}) or {}
        out = []
        for rule in rules:
            counter = self._periodic_counters.get(rule['id'], 0)
            interval = rule['interval']

            # 次数维度
            if interval <= 0:
                count_state = 'disabled'
                remaining = 0
            elif counter < interval:
                count_state = 'ok'
                remaining = interval - counter
            elif counter == interval:
                count_state = 'due'
                remaining = 0
            else:
                count_state = 'overdue'
                remaining = -(counter - interval)

            # v3.7.4: 时间维度
            time_interval = rule.get('time_interval_seconds', 0)
            last_done = last_done_map.get(rule['id'], now)
            time_elapsed = max(0.0, now - last_done)
            if time_interval <= 0:
                time_state = 'disabled'
                time_remaining = 0
            elif time_elapsed < time_interval:
                time_state = 'ok'
                time_remaining = int(time_interval - time_elapsed)
            elif int(time_elapsed) == time_interval:
                time_state = 'due'
                time_remaining = 0
            else:
                time_state = 'overdue'
                time_remaining = -int(time_elapsed - time_interval)

            # 整体 state 取更严重的
            severity = {'disabled': -1, 'ok': 0, 'due': 1, 'overdue': 2}
            if severity[count_state] >= severity[time_state]:
                overall_state = count_state if count_state != 'disabled' else time_state
            else:
                overall_state = time_state if time_state != 'disabled' else count_state
            if overall_state == 'disabled':
                overall_state = 'ok'

            out.append({
                'id': rule['id'],
                'name': rule['name'],
                'counter': counter,
                'interval': interval,
                'state': overall_state,             # ok | due | overdue (整体)
                'remaining': remaining,             # 距离次数到期还有几轮
                'count_state': count_state,         # v3.7.4: 单独的次数维度状态
                'time_interval_seconds': time_interval,  # v3.7.4
                'time_elapsed_seconds': int(time_elapsed),  # v3.7.4
                'time_remaining_seconds': time_remaining,   # v3.7.4: 距时间到期还有几秒 (负=超期)
                'time_state': time_state,           # v3.7.4: 单独的时间维度状态
                'count_basis': rule['count_basis'],
                'reset_policy': rule['reset_policy'],
                'trigger_labels': sorted(rule['trigger_labels']),
            })
        return out

    def _observe_periodic_trigger(self, step_label: str) -> None:
        """记录"本轮观察到的 trigger label" 到旁路账本 — 不进 cycle.step_sequence.

        意图: v3.7.2 (FIX-381) 在顺序模式拦截了"非序列内的步骤"进 cycle, 但
        保养类周期动作 (periodic_actions 的 trigger_step) 本来就在主序列外 —
        之前被拦了 = 永远进不了 cycle_steps = _check_periodic_actions 永远看不见 =
        永远清不了零. 修法是把这种"旁路 trigger" 独立记一笔, 周期判定时单独看.

        调用点: source_settlement_mixin.process_step_detection 里 is_new_appearance
        分支的最前面, 早于 FIX-381 拦截 return, 保证保养动作也能进账本.
        清空点: _check_periodic_actions 每次判定完清空, reset_stats /
        _discard_empty_cycle 也清, 防 reset 后或周期作废后还残留.

        参数:
            step_label: 当前判定为"新出现"的步骤标签.
        """
        if not step_label:
            return
        rules = getattr(self, '_periodic_actions', None) or []
        if not rules:
            return
        if not hasattr(self, '_periodic_triggers_observed') or not isinstance(self._periodic_triggers_observed, set):
            self._periodic_triggers_observed = set()
        cid = getattr(self, 'channel_id', 0)
        for rule in rules:
            channel_filter = rule.get('channel_filter')
            if channel_filter and cid not in channel_filter:
                continue
            trigger_labels = rule.get('trigger_labels') or set()
            if step_label in trigger_labels:
                self._periodic_triggers_observed.add(step_label)
                return
