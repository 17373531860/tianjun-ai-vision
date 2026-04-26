"""容器分组 + per-box 结算 (从 CheckModesMixin 拆出)"""
import time
import traceback
import cv2
import numpy as np


class ContainerGroupingMixin:
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
