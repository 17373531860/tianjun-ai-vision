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

        v2.7.17 single_box_mode: pipeline_config.container_box_mode 默认 'single', 此时
        只承认 first_seen 最早的"主箱", 其他 box 的 bbox 不入 _box_objects, 落进它们的
        物品也不被分组(直接丢). 主箱 confirmed gone → _settle_box → 通过 _trigger_event
        触发 end_cycle, 实现"一箱一 cycle 一工件号". 'multi' 是历史多箱兼容值, 行为
        和老版一样(可能出现一码多箱污染), 留给 Phase 2 重写.
        """
        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items() if k != container_label}

        # v2.7.17: 单箱模式 - 默认开启, 由 pipeline_config.container_box_mode 控制
        pcfg = (self.project_config or {}).get('pipeline_config', {}) if self.project_config else {}
        single_box_mode = (pcfg.get('container_box_mode', 'single') or 'single') == 'single'

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

        # v2.7.17 single_box_mode: 在画面里挑一个"主箱", 其他 box 全部从 _box_objects
        # 和 box_bboxes 里踢掉, 这样它们的 bbox 不参与物品分配, 落它们里的物品被丢弃,
        # 也不会对它们做 gone-confirm/settle. 主箱选取规则:
        #   1) 优先选已经在 _box_objects 里的(避免遮挡复活时主箱漂移)
        #   2) 多个候选时挑 first_seen 最早的
        # 主箱出去后 _box_objects 被清空, 下一帧 active_boxes 里挑下一个最早进入的
        # 升级为新主箱, 物品累积自然过渡到新 cycle.
        if single_box_mode and active_box_dids:
            existing_primary = [did for did in active_box_dids if did in self._box_objects]
            if existing_primary:
                primary_did = min(
                    existing_primary,
                    key=lambda d: self._box_objects[d].get('first_seen', current_time)
                )
            else:
                # 还没有任何 box 进 _box_objects, 从 active 里挑 first_seen 最早的
                def _box_first_seen(did: str) -> float:
                    for tid, obj in self._tracking_objects.items():
                        if obj.get('display_id') == did and obj['class_name'] == container_label:
                            return obj.get('first_seen', current_time)
                    for tid, obj in self._tracking_recently_lost.items():
                        if obj.get('display_id') == did and obj['class_name'] == container_label:
                            return obj.get('first_seen', current_time)
                    return current_time
                primary_did = min(active_box_dids, key=_box_first_seen)
            # 1) 屏蔽其他 box 的 bbox(物品不会分到它们里)
            box_bboxes = {primary_did: box_bboxes[primary_did]}
            active_box_dids = {primary_did}
            # 2) 清掉之前可能进过 _box_objects 但不再是主箱的条目
            #    本次循环里第二个 box 也会经历"进 _box_objects → 被踢"的流程,
            #    会让 _box_counter 虚高(只影响显示编号), 这里同步回退一次.
            removed = 0
            for box_did in list(self._box_objects.keys()):
                if box_did != primary_did:
                    self._box_objects.pop(box_did, None)
                    removed += 1
            if removed > 0 and self._box_counter >= removed:
                self._box_counter -= removed

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
        # 注意: _settle_box 会调 _end_cycle, _end_cycle 会清空 _box_objects,
        # 所以同一帧内若有先后多个箱子结算, 后续 box_did 已被清掉, 必须重判 key.
        for box_did in list(self._box_objects.keys()):
            if box_did not in self._box_objects:
                continue
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
