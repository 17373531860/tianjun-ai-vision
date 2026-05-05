"""Checklist 维护 + Counting 模式结算 (从 CheckModesMixin 拆出)"""
import time
import traceback
import cv2
import numpy as np


class ChecklistMixin:
    def _rebuild_checklist(self, expected_items: dict):
        """Rebuild the item checklist from current tracking state."""
        if self._container_mode and self._container_label:
            self._rebuild_container_checklist(expected_items)
            return
        
        self._tracking_item_checklist = {}
        for cls_name, expected_count in expected_items.items():
            tracking_n = self._tracking_class_counters.get(cls_name, 0)
            event_n = self._event_counters.get(cls_name, 0)
            stack_n = self._stack_counters.get(cls_name, 0)
            # v2.7.4: 堆叠模式下取 max(tracking, stack)，避免重复计数
            actual = max(tracking_n, stack_n) + event_n
            display_name = self.step_display_names.get(cls_name, cls_name)
            prefix = self._tracking_letter_map.get(cls_name, display_name)
            self._tracking_item_checklist[cls_name] = {
                'expected': expected_count, 'counted': actual,
                'prefix': prefix, 'display_name': display_name
            }
        # v3.4.2: 严格按"期望物品清单"显示, 跳过非期望类的额外补充, 防止
        # 模型识别到的干扰类(如泡沫槽/备件)出现在前端"物品清点"面板里.
        # 用户语义: 物品清点 = 项目配置的期望物品清单, 不是"模型识别到的所有类".
        # 仅当 expected_items 为空 (老项目未配清单) 时才回退旧行为, 显示全部.
        if not expected_items:
            for cls_name, count in self._tracking_class_counters.items():
                if cls_name not in self._tracking_item_checklist:
                    stack_n = self._stack_counters.get(cls_name, 0)
                    actual = max(count, stack_n)
                    display_name = self.step_display_names.get(cls_name, cls_name)
                    prefix = self._tracking_letter_map.get(cls_name, display_name)
                    self._tracking_item_checklist[cls_name] = {
                        'expected': 0, 'counted': actual,
                        'prefix': prefix, 'display_name': display_name
                    }
            for cls_name, count in self._event_counters.items():
                if cls_name not in self._tracking_item_checklist:
                    display_name = self.step_display_names.get(cls_name, cls_name)
                    self._tracking_item_checklist[cls_name] = {
                        'expected': 0, 'counted': count,
                        'prefix': display_name, 'display_name': display_name
                    }
            # v2.7.4: 仅 stack 模式（无 tracking_class_counter 也无 event_counter）的 label
            for cls_name, count in self._stack_counters.items():
                if cls_name not in self._tracking_item_checklist:
                    display_name = self.step_display_names.get(cls_name, cls_name)
                    prefix = self._tracking_letter_map.get(cls_name, display_name)
                    self._tracking_item_checklist[cls_name] = {
                        'expected': 0, 'counted': count,
                        'prefix': prefix, 'display_name': display_name
                    }

    def _settle_counting_cycle(self, expected_items: dict, check_order: bool = False, expected_order: list = None):
        """Validate tracking-mode cycle and trigger OK or NG event."""
        # v3.4.2 race-condition guard: 推理线程 (all_gone settle confirmed) 和
        # mes_hooks 线程 (settle_for_scan_pair) 可能并行调本函数, 导致同一 cycle
        # 被 _trigger_event 跑两次 → 计数 +1+1, NG 多算. 用 RLock 互斥;
        # 后到的 try-acquire 失败 → 静默跳过, 由先到方完成结算.
        # RLock 允许同线程递归 (settle_for_scan_pair 内部又调 _settle_counting_cycle 不会死锁).
        _lk = getattr(self, '_settle_lock', None)
        if _lk is not None:
            if not _lk.acquire(blocking=False):
                print(f"[Settle] _settle_counting_cycle 跳过: 锁被占 (其他线程正在 settle 同一 cycle)", flush=True)
                return
        try:
            self._settle_counting_cycle_impl(expected_items, check_order, expected_order)
        finally:
            if _lk is not None:
                _lk.release()

    def _settle_counting_cycle_impl(self, expected_items: dict, check_order: bool = False, expected_order: list = None):
        """Internal: real settlement work, wrapped by _settle_counting_cycle for race protection."""
        print(f"[Tracking] Settling cycle: counters={self._tracking_class_counters}, events={self._event_counters}, expected={expected_items}")

        # In container mode, settle remaining boxes then reset (skip cycle-level count validation)
        if self._container_mode:
            # v3.4.2: scan_pair (码-码闭环) 模式下, 物品清空 / all_gone 也不直接结算,
            # 由下一码事件触发 settle_for_scan_pair. 这里只保留 _box_objects 状态
            # (already-complete 的 was_complete 不丢), 等扫码再判. 修 bug: 之前只挡了
            # gone-confirm 路径, all_gone (用户拿走箱子触发 settle_confirmed) 这条会
            # 强制 _settle_box → 结算 + 把 box 清掉 → "码丢了" 现象.
            scan_pair_active = False
            try:
                from backend.services.mes_hooks import get_mes_hook
                _mes_mgr = get_mes_hook()
                if _mes_mgr and getattr(_mes_mgr, 'enabled', False):
                    scan_pair_active = _mes_mgr.is_scan_pair_mode(
                        getattr(self, 'channel_id', 0)
                    )
            except Exception:
                scan_pair_active = False
            if scan_pair_active:
                # 不 settle, 不 reset; _box_objects / was_complete 保留, 等扫下一码.
                print(f"[Container] all_gone 但 scan_pair 模式 → 跳过 settle, "
                      f"等下一码触发 (active boxes={list(self._box_objects.keys())})",
                      flush=True)
                return
            box_list = list(self._box_objects.keys())
            _sd = self.project_config.get('pipeline_config', {}).get('settle_dedup', False) if self.project_config else False
            for i, box_did in enumerate(box_list):
                if _sd and i > 0 and not self.current_cycle_id:
                    self.start_cycle()
                self._settle_box(box_did, expected_items)
            total_ok = sum(1 for r in self._box_settled_results if r['is_complete'])
            total_ng = sum(1 for r in self._box_settled_results if not r['is_complete'])
            total = len(self._box_settled_results)
            print(f"[Container] Cycle end: {total} boxes settled (OK={total_ok}, NG={total_ng})")
            self._reset_counting_cycle()
            return
        
        current_time = time.time()
        
        # ===== Collect all item instances from active + recently_lost =====
        # v2.7.14: 只保留"物品清单"(expected_items) 里的类别。
        # 启用但未入清单的类(比如"箱子"、"泡沫槽") 允许模型识别/画框/跟踪, 但不写 StepRecord。
        # 仅当 expected_items 非空时过滤; 为空视为"任意类都算", 保留原行为。
        def _in_checklist(cls_name: str) -> bool:
            if not expected_items:
                return True
            return cls_name in expected_items

        all_items = []
        for tid, obj in self._tracking_objects.items():
            if not _in_checklist(obj['class_name']):
                continue
            all_items.append({
                'class_name': obj['class_name'],
                'display_id': obj['display_id'],
                'first_seen': obj.get('first_seen', 0),
                'last_seen': obj.get('last_seen', 0),
                'order_idx': obj.get('order_idx', 0),
            })
        for tid, obj in self._tracking_recently_lost.items():
            if not _in_checklist(obj['class_name']):
                continue
            all_items.append({
                'class_name': obj['class_name'],
                'display_id': obj['display_id'],
                'first_seen': obj.get('first_seen', 0),
                'last_seen': obj.get('lost_time', obj.get('first_seen', 0)),
                'order_idx': obj.get('order_idx', 0),
            })
        
        all_items.sort(key=lambda o: o['order_idx'])
        
        seen_display_ids = set()
        unique_items = []
        for item in all_items:
            if item['display_id'] not in seen_display_ids:
                seen_display_ids.add(item['display_id'])
                unique_items.append(item)

        # v2.7.14: 兜底——settle 触发时 _tracking_objects / _tracking_recently_lost 可能
        # 都已被清空(物品全离开 + 遮挡容忍短, 导致 recently_lost 过期)。此时 StepRecord
        # 会一条不写, 前端数据中心展开 cycle 全空。用 _tracking_class_counters / _event_counters
        # 里累计的数量虚拟补齐, 至少让用户看到本周期识别到了几个 A / 几个 B(没有精确时间戳)。
        if not unique_items:
            virtual_items = []
            virtual_order = 0
            for cls_name, cnt in sorted(
                self._tracking_class_counters.items(), key=lambda kv: kv[0]
            ):
                if cnt <= 0 or not _in_checklist(cls_name):
                    continue
                prefix = self._get_display_prefix(cls_name)
                for i in range(1, int(cnt) + 1):
                    virtual_order += 1
                    virtual_items.append({
                        'class_name': cls_name,
                        'display_id': f"{prefix}{i}",
                        'first_seen': self.cycle_start_time or current_time,
                        'last_seen': current_time,
                        'order_idx': virtual_order,
                    })
            if virtual_items:
                print(f"[Tracking] settle 时活动/丢失表均空, 用计数器虚拟补齐 "
                      f"{len(virtual_items)} 条: {[v['display_id'] for v in virtual_items]}")
                unique_items = virtual_items

        self.current_cycle_steps = [item['display_id'] for item in unique_items]
        
        step_order = 0
        for item in unique_items:
            start_t = item['first_seen']
            end_t = item['last_seen']
            duration = max(0, end_t - start_t) if start_t and end_t else 0
            step_name = item['display_id']
            step_label = item['class_name']
            step_order += 1
            self.record_step(
                step_label=step_label,
                step_name=step_name,
                start_time=start_t,
                end_time=end_t,
                duration=round(duration, 2),
                step_order=step_order,
                is_valid=True,
            )
        
        for cls_name, count in self._event_counters.items():
            if count > 0 and _in_checklist(cls_name):
                display_name = self.step_display_names.get(cls_name, cls_name)
                step_order += 1
                start_t = self._event_first_seen.get(cls_name, self.cycle_start_time or current_time)
                end_t = self._event_last_seen.get(cls_name, current_time)
                duration = max(0, end_t - start_t) if start_t and end_t else 0
                self.current_cycle_steps.append(f"{display_name}\u00d7{count}")
                self.record_step(
                    step_label=cls_name,
                    step_name=f"{display_name}\u00d7{count}",
                    start_time=start_t,
                    end_time=end_t,
                    duration=round(duration, 2),
                    step_order=step_order,
                    is_valid=True,
                )
        
        # ===== Validate counts (merge track counters + event counters) =====
        merged_counters = dict(self._tracking_class_counters)
        for cls_name, cnt in self._event_counters.items():
            merged_counters[cls_name] = merged_counters.get(cls_name, 0) + cnt
        
        missing = []
        extra = []
        for cls_name, exp in expected_items.items():
            actual = merged_counters.get(cls_name, 0)
            if actual < exp:
                missing.append(f"{cls_name}: {actual}/{exp}")
            elif actual > exp:
                extra.append(f"{cls_name}: {actual}/{exp}")
        # 不在期望清单中的类别不参与判定（允许画框但不影响 OK/NG）
        
        order_ok = True
        if check_order and expected_order:
            placed_classes = [item['class_name'] for item in unique_items]
            if placed_classes != expected_order:
                order_ok = False
        
        print(f"[Tracking] 判定: merged={merged_counters}, missing={missing}, extra={extra}, "
              f"cycle_id={self.current_cycle_id}, cycle_active={self._tracking_cycle_active}")

        # v3.3.0 scan_pair: 由 settle_for_scan_pair() 设置的提示标志, 此时把
        # OK/NG 判定切到 sticky 'was_complete', 而不是基于当前帧的 missing/extra.
        scan_pair_hint = bool(getattr(self, '_scan_pair_settle_hint', False))
        was_complete = bool(getattr(self, '_tracking_was_complete', False))

        # v3.4.2: tracking 模式 scan_pair: 非"扫码触发"路径(all_gone自动)也要跳过
        # 直接结算, 由下一码事件触发. _tracking_was_complete sticky 已在
        # source_tracking_mixin 里维护, 这里不能 _reset_counting_cycle 否则丢状态.
        if not scan_pair_hint:
            try:
                from backend.services.mes_hooks import get_mes_hook
                _mes_mgr = get_mes_hook()
                if (_mes_mgr and getattr(_mes_mgr, 'enabled', False)
                        and _mes_mgr.is_scan_pair_mode(getattr(self, 'channel_id', 0))):
                    print(f"[Tracking] all_gone 但 scan_pair 模式 → 跳过 settle, "
                          f"等下一码触发 (was_complete={was_complete}, "
                          f"merged={dict(merged_counters)})", flush=True)
                    return
            except Exception:
                pass

        if not expected_items:
            self._trigger_event(1, f'Counting complete: {dict(merged_counters)}')
        elif scan_pair_hint and was_complete:
            self._trigger_event(
                1, f'Scan-pair: was complete (final={dict(merged_counters)})'
            )
        elif missing or extra:
            reasons = []
            if missing: reasons.append(f'missing: {missing}')
            if extra: reasons.append(f'extra: {extra}')
            self._trigger_event(2, ', '.join(reasons))
        elif not order_ok:
            actual_seq = [item['class_name'] for item in unique_items]
            self._trigger_event(2, f'Order wrong: expected={expected_order}, actual={actual_seq}')
        else:
            self._trigger_event(1, f'All complete: {dict(merged_counters)}')
        
        self._reset_counting_cycle()
