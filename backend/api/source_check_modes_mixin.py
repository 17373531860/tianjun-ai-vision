"""判定模式 + 容器分组 (v2.7.16 P6 阶段一第七刀, 从 source.py 整体搬出)。

包含 8 个方法 (合计 ~970 行):
  _update_container_grouping     : 把追踪到的 item 按空间包含关系归到父 box
  _settle_box                    : per-box 结算 (容器多 box 模式)
  _rebuild_checklist             : 重建 cycle 的 checklist
  _settle_counting_cycle         : counting 模式 cycle 结算
  _check_events                  : 跑事件 FSM
  _check_sequential_mode         : 内置顺序模式判定
  _check_custom_sequential_mode  : 自定义顺序模式判定
  _check_custom_detection_mode   : 自定义检测模式判定

依赖宿主 (VideoSourceManager):
  - 状态: project_config / cycle_strategy / current_cycle_steps / backup_steps_seen_in_cycle /
          last_added_step / boxes / box_inventory / detected_items_per_box / current_cycle_id /
          step_first_seen_time / first_step_label / sequential_states / 等
  - 方法: _trigger_event / _settle_custom_cycle / _settle_sequential_cycle /
          _settle_detection_cycle / _add_step_record / _broadcast_*  / 等
"""
import time
import traceback
import cv2
import numpy as np


class CheckModesMixin:
    def _update_container_grouping(self, expected_items: dict, current_time: float,
                                    gone_confirm_frames: int = 30,
                                    cycle_strategy: str = 'all_gone'):
        """Group tracked items into their parent boxes by spatial containment.
        Per-box settlement uses the same gone-confirm logic as the cycle level.

        v2.7.7c 合并: 同一个 box 里同一 label 达到 step 配置的 max_recognized 后,
        新出现的 track_id 不再计数 (只刷新老条目的 last_seen). 这解决了帧内 top-N
        之后跨帧出现新 track_id (遮挡/重新出现) 导致的超额误判.
        """
        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items() if k != container_label}

        # 从 steps_config 读每个 label 的 max_recognized 上限 (只处理 count_mode='track')
        max_recognized_per_label: dict = {}
        try:
            steps_config = (self.project_config or {}).get('steps_config', []) if self.project_config else []
            for step in steps_config:
                if not step.get('enabled', True):
                    continue
                if step.get('count_mode', 'track') != 'track':
                    continue
                lbl = step.get('label', '')
                if not lbl:
                    continue
                try:
                    mr = int(step.get('max_recognized', 0) or 0)
                    if mr > 0:
                        max_recognized_per_label[lbl] = mr
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass

        # Collect active boxes and items from _tracking_objects
        active_box_dids = set()
        box_bboxes = {}  # {display_id: bbox}
        item_entries = []  # [(track_id, label, display_id, bbox)]
        
        for tid, obj in self._tracking_objects.items():
            if obj['class_name'] == container_label:
                did = obj['display_id']
                active_box_dids.add(did)
                box_bboxes[did] = obj['bbox']
                if did not in self._box_objects:
                    self._box_counter += 1
                    self._box_objects[did] = {
                        'display_id': did,
                        'bbox': obj['bbox'],
                        'first_seen': obj.get('first_seen', current_time),
                        'last_seen': current_time,
                        'gone_frames': 0,
                        'had_roi': False,
                        'items_ever_seen': {},
                        'item_class_counts': {},
                        'is_complete': False,
                    }
                else:
                    self._box_objects[did]['bbox'] = obj['bbox']
                    self._box_objects[did]['last_seen'] = current_time
            else:
                item_entries.append((
                    tid, obj['class_name'],
                    obj.get('display_id', ''), obj['bbox']
                ))
        
        # Also consider boxes in recently_lost as "still present" (within tolerance)
        for tid, obj in self._tracking_recently_lost.items():
            if obj['class_name'] == container_label:
                did = obj['display_id']
                if did in self._box_objects and did not in active_box_dids:
                    active_box_dids.add(did)
                    box_bboxes[did] = obj['bbox']
        
        # Assign items to boxes: item center inside box bbox, pick smallest box
        for item_tid, item_label, item_did, item_bbox in item_entries:
            item_cx = item_bbox['x'] + item_bbox['w'] / 2
            item_cy = item_bbox['y'] + item_bbox['h'] / 2
            
            best_box_did = None
            best_box_area = float('inf')
            for box_did, bb in box_bboxes.items():
                bx1, by1 = bb['x'], bb['y']
                bx2, by2 = bx1 + bb['w'], by1 + bb['h']
                if bx1 <= item_cx <= bx2 and by1 <= item_cy <= by2:
                    area = bb['w'] * bb['h']
                    if area < best_box_area:
                        best_box_area = area
                        best_box_did = box_did
            
            if best_box_did is not None:
                box_state = self._box_objects[best_box_did]
                if item_tid in box_state['items_ever_seen']:
                    box_state['items_ever_seen'][item_tid]['last_seen'] = current_time
                    continue

                # v2.7.7c 合并: 本 box 里该 label 已达上限 → 新 track_id 不再计数,
                # 只把最早进入的那条的 last_seen 刷新 (避免"找不到最新活跃 id"导致误判 gone)
                limit = max_recognized_per_label.get(item_label, 0)
                cur_count = box_state['item_class_counts'].get(item_label, 0)
                if limit > 0 and cur_count >= limit:
                    for _prev_tid, _info in box_state['items_ever_seen'].items():
                        if _info.get('label') == item_label:
                            _info['last_seen'] = current_time
                            break
                    continue

                box_state['items_ever_seen'][item_tid] = {
                    'label': item_label,
                    'display_id': item_did,
                    'first_seen': current_time,
                    'last_seen': current_time,
                }
                box_state['item_class_counts'][item_label] = \
                    box_state['item_class_counts'].get(item_label, 0) + 1
        
        # Recalculate completeness for all active boxes
        for box_did in active_box_dids:
            if box_did in self._box_objects:
                bs = self._box_objects[box_did]
                bs['is_complete'] = (not expected_no_container) or all(
                    bs['item_class_counts'].get(cls, 0) >= exp
                    for cls, exp in expected_no_container.items()
                )
                if cycle_strategy == 'roi_exit':
                    det = {'x': bs['bbox']['x'], 'y': bs['bbox']['y'],
                           'w': bs['bbox']['w'], 'h': bs['bbox']['h']}
                    if self._is_in_roi(det):
                        bs['had_roi'] = True
        
        # Per-box gone confirmation (same pattern as cycle-level settlement)
        for box_did in list(self._box_objects.keys()):
            bs = self._box_objects[box_did]
            box_visible = box_did in active_box_dids
            
            should_count_gone = False
            if cycle_strategy == 'roi_exit':
                should_count_gone = bs['had_roi'] and not box_visible
            else:
                should_count_gone = not box_visible
            
            if should_count_gone:
                bs['gone_frames'] = bs.get('gone_frames', 0) + 1
                if bs['gone_frames'] == 1:
                    print(f"[Container] {box_did} gone, confirming: {gone_confirm_frames} frames")
                if bs['gone_frames'] >= gone_confirm_frames:
                    print(f"[Container] {box_did} confirmed gone ({bs['gone_frames']}/{gone_confirm_frames})")
                    _sd = self.project_config.get('pipeline_config', {}).get('settle_dedup', False) if self.project_config else False
                    if _sd and not self.current_cycle_id:
                        self.start_cycle()
                    self._settle_box(box_did, expected_items)
            else:
                if bs.get('gone_frames', 0) > 0:
                    print(f"[Container] {box_did} reappeared, reset ({bs['gone_frames']}/{gone_confirm_frames})")
                bs['gone_frames'] = 0
    
    def _settle_box(self, box_display_id: str, expected_items: dict):
        """Settle a single box: record its items and completeness."""
        box_state = self._box_objects.pop(box_display_id, None)
        if box_state is None:
            return
        
        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items() if k != container_label}
        
        item_counts = box_state['item_class_counts']
        missing = []
        extra = []
        for cls, exp in expected_no_container.items():
            actual = item_counts.get(cls, 0)
            if actual < exp:
                display = self.step_display_names.get(cls, cls)
                missing.append(f"{display}: {actual}/{exp}")
            elif actual > exp:
                display = self.step_display_names.get(cls, cls)
                extra.append(f"{display}: {actual}/{exp}")
        # 不在期望清单中的类别不参与判定（允许画框但不影响 OK/NG）
        
        is_ok = not missing and not extra
        
        result = {
            'display_id': box_display_id,
            'first_seen': box_state['first_seen'],
            'last_seen': box_state['last_seen'],
            'item_counts': dict(item_counts),
            'expected': dict(expected_no_container),
            'is_complete': is_ok,
            'missing': missing,
            'extra': extra,
            'items_detail': [
                {'label': v['label'], 'display_id': v['display_id']}
                for v in box_state['items_ever_seen'].values()
            ],
        }
        self._box_settled_results.append(result)
        
        status = "OK" if is_ok else "NG"
        print(f"[Container] {box_display_id} settled: {status}, "
              f"items={item_counts}, expected={expected_no_container}")

        # v2.7.14: 容器模式下也往 StepRecord 写一条一条物品, 让数据中心展开 cycle 能看到
        # ——之前只触发 _trigger_event, 没 record_step, 前端数据中心永远是空的。
        # 严格过滤: 只写在"物品清单"(expected_no_container) 里的类别。
        # 容器类(箱子)不算 item, 本身不进 StepRecord。
        current_time = time.time()

        def _in_item_checklist(cls_name: str) -> bool:
            if cls_name == container_label:
                return False
            if not expected_no_container:
                return True
            return cls_name in expected_no_container

        items_to_record = []
        for info in box_state.get('items_ever_seen', {}).values():
            if not _in_item_checklist(info.get('label', '')):
                continue
            items_to_record.append({
                'label': info.get('label', ''),
                'display_id': info.get('display_id', ''),
                'first_seen': info.get('first_seen', current_time),
                'last_seen': info.get('last_seen', current_time),
            })
        # 按 display_id 去重, 保留最早/最晚时间
        dedup = {}
        for it in items_to_record:
            did = it['display_id']
            if did not in dedup:
                dedup[did] = it
            else:
                dedup[did]['first_seen'] = min(dedup[did]['first_seen'], it['first_seen'])
                dedup[did]['last_seen'] = max(dedup[did]['last_seen'], it['last_seen'])
        items_to_record = list(dedup.values())

        # 兜底: items_ever_seen 已被清 / 丢失, 但 item_class_counts 有累计 → 虚拟补齐
        if not items_to_record and item_counts:
            virtual = []
            for cls_name, cnt in sorted(item_counts.items(), key=lambda kv: kv[0]):
                if cnt <= 0 or not _in_item_checklist(cls_name):
                    continue
                prefix = self._get_display_prefix(cls_name)
                for i in range(1, int(cnt) + 1):
                    virtual.append({
                        'label': cls_name,
                        'display_id': f"{prefix}{i}",
                        'first_seen': box_state.get('first_seen', current_time),
                        'last_seen': box_state.get('last_seen', current_time),
                    })
            if virtual:
                print(f"[Container] {box_display_id} items_ever_seen 空, "
                      f"按计数器虚拟补齐 {len(virtual)} 条")
                items_to_record = virtual

        # 维护 current_cycle_steps 供 end_cycle 写入 cycle.step_sequence
        if items_to_record:
            self.current_cycle_steps = [it['display_id'] for it in items_to_record]
            step_order = 0
            for it in items_to_record:
                step_order += 1
                dur = max(0.0, (it['last_seen'] or current_time) - (it['first_seen'] or current_time))
                step_name = it['display_id']
                self.record_step(
                    step_label=it['label'],
                    step_name=step_name,
                    start_time=it['first_seen'] or current_time,
                    end_time=it['last_seen'] or current_time,
                    duration=round(dur, 2),
                    step_order=step_order,
                    is_valid=True,
                )

        if is_ok:
            self._trigger_event(1, f'{box_display_id} OK: {item_counts}')
        else:
            reasons = []
            if missing:
                reasons.append(f'missing: {missing}')
            if extra:
                reasons.append(f'extra: {extra}')
            self._trigger_event(2, f'{box_display_id} NG: {", ".join(reasons) if reasons else "no items"}')
    
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
    
    def _rebuild_container_checklist(self, expected_items: dict):
        """Build per-box checklist for container mode."""
        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items() if k != container_label}
        
        boxes_info = {}
        for box_did, bs in self._box_objects.items():
            items_info = {}
            for cls, exp in expected_no_container.items():
                display_name = self.step_display_names.get(cls, cls)
                actual = bs['item_class_counts'].get(cls, 0)
                items_info[cls] = {
                    'expected': exp,
                    'counted': actual,
                    'display_name': display_name,
                }
            for cls, cnt in bs['item_class_counts'].items():
                if cls not in items_info:
                    display_name = self.step_display_names.get(cls, cls)
                    items_info[cls] = {
                        'expected': 0,
                        'counted': cnt,
                        'display_name': display_name,
                    }
            boxes_info[box_did] = {
                'items': items_info,
                'complete': bs['is_complete'],
            }
        
        self._tracking_item_checklist = {
            '_container_mode': True,
            '_boxes': boxes_info,
            '_settled_count': len(self._box_settled_results),
            '_settled_ok': sum(1 for r in self._box_settled_results if r['is_complete']),
            '_settled_ng': sum(1 for r in self._box_settled_results if not r['is_complete']),
        }
    
    def _settle_counting_cycle(self, expected_items: dict, check_order: bool = False, expected_order: list = None):
        """Validate tracking-mode cycle and trigger OK or NG event."""
        print(f"[Tracking] Settling cycle: counters={self._tracking_class_counters}, events={self._event_counters}, expected={expected_items}")
        
        # In container mode, settle remaining boxes then reset (skip cycle-level count validation)
        if self._container_mode:
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
        
        if not expected_items:
            self._trigger_event(1, f'Counting complete: {dict(merged_counters)}')
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
    
    def _check_events(self, completed_step: str):
        """检查是否触发事件"""
        if not self.project_config:
            return
        
        events_config = self.project_config.get('events_config', [])
        logic_mode = self.project_config.get('logic_mode', 'detection')
        pipeline_config = self.project_config.get('pipeline_config', {})
        steps_config = self.project_config.get('steps_config', [])
        
        # 创建步骤ID到标签的映射
        id_to_label = {}
        label_to_id = {}
        for step in steps_config:
            step_id = step.get('id')
            label = step.get('label', '')
            if step_id and label:
                id_to_label[step_id] = label
                label_to_id[label] = step_id
        
        # 获取启用的步骤标签和ID集合
        enabled_step_labels = [s.get('label') for s in steps_config if s.get('enabled', True)]
        enabled_step_ids = {s.get('id') for s in steps_config if s.get('enabled', True)}
        
        # 自定义模式
        if logic_mode == 'custom':
            custom_based_on = pipeline_config.get('custom_based_on')  # 'sequential', 'detection', 或 None
            custom_conditions = pipeline_config.get('custom_conditions', [])
            
            self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
            
            # 先检查自定义条件（按优先级排序）
            condition_matched = False
            if custom_conditions:
                # 按优先级排序（数字越小优先级越高）
                sorted_conditions = sorted(custom_conditions, key=lambda c: c.get('priority', 999))
                
                for cond in sorted_conditions:
                    cond_sequence = cond.get('sequence', [])
                    cond_event_id = cond.get('event_id')
                    
                    if not cond_sequence or not cond_event_id:
                        continue
                    
                    # 将条件中的步骤ID转换为标签（只包含启用的步骤）
                    cond_labels = [id_to_label.get(sid) for sid in cond_sequence if sid in id_to_label and sid in enabled_step_ids]
                    
                    if not cond_labels:
                        continue
                    
                    print(f"自定义条件检查: 条件序列={cond_labels}, 当前周期={self.current_cycle_steps}")
                    
                    # 检查是否完全匹配（数量和顺序都要相同）
                    if self.current_cycle_steps == cond_labels:
                        print(f"  → 条件匹配！触发事件 {cond_event_id}")
                        self._trigger_event(cond_event_id, f'自定义条件匹配: {cond_labels}')
                        self.current_cycle_steps = []
                        self.backup_steps_seen_in_cycle = set()
                        self.last_added_step = None
                        return  # 匹配后不再检查其他条件和基础模式
            
            # 没有自定义条件匹配，回退到基础模式
            # first_step 模式下跳过"最后一步消失"触发，只由第一步重现触发结算
            if self.settlement_mode == 'first_step':
                pass
            elif custom_based_on == 'sequential':
                last_step_label = self._get_last_sequence_step_label()
                if last_step_label and completed_step == last_step_label:
                    if completed_step in self.current_cycle_steps:
                        self._check_custom_sequential_mode(pipeline_config, id_to_label)
                    else:
                        print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
            elif custom_based_on == 'detection':
                if completed_step in self.current_cycle_steps:
                    self._check_custom_detection_mode(pipeline_config, id_to_label, enabled_step_labels)
                else:
                    print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
        
        # 顺序模式
        elif logic_mode == 'sequential':
            if self.settlement_mode != 'first_step':
                last_step_label = self._get_last_sequence_step_label()
                if last_step_label and completed_step == last_step_label:
                    if completed_step in self.current_cycle_steps:
                        self._check_sequential_mode(pipeline_config, id_to_label)
                    else:
                        print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
        
        # 检测模式
        elif logic_mode == 'detection':
            if self.settlement_mode != 'first_step':
                last_det_label = self._get_last_detection_step_label()
                if last_det_label and completed_step == last_det_label:
                    if completed_step in self.current_cycle_steps:
                        self._settle_detection_cycle()
                    else:
                        print(f"  → 步骤 [{completed_step}] 不在当前周期中，跳过判定（可能是上一周期的残留）")
        
        # 检查步骤特定事件
        for step in steps_config:
            if step.get('label') == completed_step:
                trigger_event = step.get('trigger_event')
                if trigger_event:
                    self._trigger_event(trigger_event, f'步骤 {completed_step} 触发')
    
    def _check_sequential_mode(self, pipeline_config: dict, id_to_label: dict):
        """检查顺序模式（由 last_step 消失触发）
        
        关键设计：在 current_cycle_steps 中以 last_step 首次出现为界拆分，
        拆分后的前半部分为本周期判定依据，后半部分保留给下一周期。
        """
        sequence_order = pipeline_config.get('sequence_order', [])
        if not sequence_order:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            # 周期结算后重置步骤时序状态，确保下一轮的相同步骤可被视为“新出现”
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        # 获取启用的步骤ID集合
        steps_config = self.project_config.get('steps_config', []) if self.project_config else []
        enabled_step_ids = {s.get('id') for s in steps_config if s.get('enabled', True)}
        
        # 将步骤ID转换为标签名（只包含启用的步骤）
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label and step_id in enabled_step_ids:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            # 周期结算后重置步骤时序状态，确保下一轮的相同步骤可被视为“新出现”
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        last_step_label = expected_labels[-1]
        
        # Split at last_step's first occurrence: before = this cycle, after = carry to next
        if last_step_label in self.current_cycle_steps:
            split_idx = self.current_cycle_steps.index(last_step_label)
            this_cycle = self.current_cycle_steps[:split_idx + 1]
            next_carry = self.current_cycle_steps[split_idx + 1:]
        else:
            this_cycle = list(self.current_cycle_steps)
            next_carry = []
        
        this_cycle = self._inject_backup_steps(this_cycle, expected_labels)
        this_cycle = self._filter_cycle_by_duration(this_cycle)
        self.current_cycle_steps = this_cycle
        
        if not this_cycle:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        self._reconcile_step_records()
        
        print(f"顺序模式检查: 期望={expected_labels}, 本周期={this_cycle}, 下周期残留={next_carry}")
        
        # ── 判定 ──
        if len(this_cycle) > len(expected_labels):
            from collections import Counter
            step_counter = Counter(this_cycle)
            duplicated = [s for s, cnt in step_counter.items() if cnt > 1]
            print(f"  → 序列长度({len(this_cycle)})超过预期({len(expected_labels)}) → NG")
            self._trigger_event(2, f'重复步骤: {duplicated}')
        elif not all(lbl in this_cycle for lbl in expected_labels):
            missing = [l for l in expected_labels if l not in this_cycle]
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
        else:
            cycle_order_correct = True
            order_error_labels = []
            last_pos = -1
            prev_lbl = None
            for lbl in expected_labels:
                pos = this_cycle.index(lbl)
                if pos < last_pos:
                    cycle_order_correct = False
                    order_error_labels = [prev_lbl, lbl]
                    break
                last_pos = pos
                prev_lbl = lbl
            if cycle_order_correct:
                print(f"  → 顺序正确 → OK")
                self._trigger_event(1, '顺序正确完成')
            else:
                print(f"  → 顺序错误 → NG: {order_error_labels}")
                self._trigger_event(2, f'顺序错误，期望[{order_error_labels[0]}]在前 实际[{order_error_labels[1]}]在前')
        
        # ── 补计：对本周期中尚未被计数的步骤进行补计 ──
        for label in this_cycle:
            if label in self.step_last_seen:
                start_time = self.step_start_time.get(label, self.step_last_seen[label])
                last_time = self.step_last_seen[label]
                duration = last_time - start_time
                
                time_config = self.step_time_config.get(label, {})
                min_duration = time_config.get('min_duration')
                max_duration = time_config.get('max_duration')
                
                is_valid = True
                if min_duration is not None and duration < min_duration:
                    is_valid = False
                if max_duration is not None and duration > max_duration:
                    is_valid = False
                
                if is_valid:
                    if label not in self.step_counts:
                        self.step_counts[label] = 0
                    self.step_counts[label] += 1
                    rounded_dur = round(duration, 2)
                    self.step_durations[label] = rounded_dur
                    self.step_durations_history.setdefault(label, []).append(rounded_dur)
                    print(f"  顺序判定补计: {label}, 耗时 {duration:.2f}s, 累计: {self.step_counts[label]}")
                
                del self.step_last_seen[label]
                if label in self.step_start_time:
                    del self.step_start_time[label]
        
        # ── 重置 / 承接下一周期 ──
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        if next_carry:
            self.current_cycle_steps = next_carry
            self.last_added_step = next_carry[-1]
            self.cycle_start_time = self.step_start_time.get(next_carry[0], time.time())
            self._last_step_added_time = time.time()
            self.start_cycle()
        else:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self._last_step_added_time = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None

    def _check_custom_sequential_mode(self, pipeline_config: dict, id_to_label: dict):
        """检查自定义模式（基于顺序模式）的判定
        
        关键设计：在 current_cycle_steps 中以 last_step 首次出现为界拆分。
        使用独立的 custom_sequence_order 配置进行判定。
        """
        # 使用自定义模式独立的顺序配置
        sequence_order = pipeline_config.get('custom_sequence_order', [])
        if not sequence_order:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        # 获取启用的步骤ID集合
        steps_config = self.project_config.get('steps_config', []) if self.project_config else []
        enabled_step_ids = {s.get('id') for s in steps_config if s.get('enabled', True)}
        
        # 将步骤ID转换为标签名（只包含启用的步骤）
        expected_labels = []
        for item in sequence_order:
            step_id = item.get('step_id')
            if step_id in id_to_label and step_id in enabled_step_ids:
                expected_labels.append(id_to_label[step_id])
        
        if not expected_labels:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        last_step_label = expected_labels[-1]
        
        # Split at last_step's first occurrence: before = this cycle, after = carry to next
        if last_step_label in self.current_cycle_steps:
            split_idx = self.current_cycle_steps.index(last_step_label)
            this_cycle = self.current_cycle_steps[:split_idx + 1]
            next_carry = self.current_cycle_steps[split_idx + 1:]
        else:
            this_cycle = list(self.current_cycle_steps)
            next_carry = []
        
        this_cycle = self._inject_backup_steps(this_cycle, expected_labels)
        this_cycle = self._filter_cycle_by_duration(this_cycle)
        self.current_cycle_steps = this_cycle
        
        if not this_cycle:
            self._discard_empty_cycle()
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.step_consecutive_frames.clear()
            self.step_frame_confirmed.clear()
            self._step_gap_count.clear()
            self.last_step_completed_time = None
            return
        
        self._reconcile_step_records()
        
        print(f"自定义模式（基于顺序）检查: 期望={expected_labels}, 本周期={this_cycle}, 下周期残留={next_carry}")
        
        # ── 判定 ──
        if len(this_cycle) > len(expected_labels):
            from collections import Counter
            step_counter = Counter(this_cycle)
            duplicated = [s for s, cnt in step_counter.items() if cnt > 1]
            print(f"  → 序列长度({len(this_cycle)})超过预期({len(expected_labels)}) → NG")
            self._trigger_event(2, f'重复步骤: {duplicated}')
        elif not all(lbl in this_cycle for lbl in expected_labels):
            missing = [l for l in expected_labels if l not in this_cycle]
            print(f"  → 周期不完整，缺少: {missing} → NG")
            self._trigger_event(2, f'周期不完整，缺少: {missing}')
        else:
            cycle_order_correct = True
            order_error_labels = []
            last_pos = -1
            prev_lbl = None
            for lbl in expected_labels:
                pos = this_cycle.index(lbl)
                if pos < last_pos:
                    cycle_order_correct = False
                    order_error_labels = [prev_lbl, lbl]
                    break
                last_pos = pos
                prev_lbl = lbl
            if cycle_order_correct:
                print(f"  → 顺序正确 → OK")
                self._trigger_event(1, '顺序正确完成')
            else:
                print(f"  → 顺序错误 → NG: {order_error_labels}")
                self._trigger_event(2, f'顺序错误，期望[{order_error_labels[0]}]在前 实际[{order_error_labels[1]}]在前')
        
        # ── 补计：对本周期中尚未被计数的步骤进行补计 ──
        for label in this_cycle:
            if label in self.step_last_seen:
                start_time = self.step_start_time.get(label, self.step_last_seen[label])
                last_time = self.step_last_seen[label]
                duration = last_time - start_time
                
                time_config = self.step_time_config.get(label, {})
                min_duration = time_config.get('min_duration')
                max_duration = time_config.get('max_duration')
                
                is_valid = True
                if min_duration is not None and duration < min_duration:
                    is_valid = False
                if max_duration is not None and duration > max_duration:
                    is_valid = False
                
                if is_valid:
                    if label not in self.step_counts:
                        self.step_counts[label] = 0
                    self.step_counts[label] += 1
                    rounded_dur = round(duration, 2)
                    self.step_durations[label] = rounded_dur
                    self.step_durations_history.setdefault(label, []).append(rounded_dur)
                    print(f"  自定义顺序判定补计: {label}, 耗时 {duration:.2f}s, 累计: {self.step_counts[label]}")
                
                del self.step_last_seen[label]
                if label in self.step_start_time:
                    del self.step_start_time[label]
        
        # ── 重置 / 承接下一周期 ──
        self.step_consecutive_frames.clear()
        self.step_frame_confirmed.clear()
        self._step_gap_count.clear()
        if next_carry:
            self.current_cycle_steps = next_carry
            self.last_added_step = next_carry[-1]
            self.cycle_start_time = self.step_start_time.get(next_carry[0], time.time())
            self._last_step_added_time = time.time()
            self.start_cycle()
        else:
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
            self._last_step_added_time = None
            self.step_last_seen.clear()
            self.step_start_time.clear()
            self.last_step_completed_time = None

    def _check_custom_detection_mode(self, pipeline_config: dict, id_to_label: dict, enabled_step_labels: list):
        """检查自定义模式（基于检测模式）
        
        使用独立的 custom_detection_steps 配置
        """
        detection_step_ids = pipeline_config.get('custom_detection_steps', [])
        
        # 将步骤ID转换为标签名
        if detection_step_ids:
            detection_labels = [id_to_label.get(sid) for sid in detection_step_ids if sid in id_to_label]
        else:
            detection_labels = enabled_step_labels
        
        if not detection_labels:
            return
        
        self.current_cycle_steps = self._filter_cycle_by_duration(self.current_cycle_steps)
        
        print(f"自定义模式（基于检测）检查: 需要={detection_labels}, 当前周期={self.current_cycle_steps}")
        
        if all(label in self.current_cycle_steps for label in detection_labels):
            self._trigger_event(1, '检测完成')  # 事件1: 合格
            self.current_cycle_steps = []
            self.backup_steps_seen_in_cycle = set()
            self.last_added_step = None
    
