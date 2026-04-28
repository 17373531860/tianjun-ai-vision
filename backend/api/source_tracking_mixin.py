"""跟踪/物品清点 (logic_mode='tracking') Mixin。

从 VideoSourceManager 中抽出 12 个相对独立的跟踪方法。这些方法构成了
"物品清点 + 计数 + 周期结算" 的完整状态机。

原 _update_tracking_stats 是 728 行单体怪物 (v2.7.16 P5b 拆成主流程 + 11 个子方法),
本次 P6 阶段一进一步把整族搬到独立文件, 让宿主 source.py 立刻瘦 ~880 行。

宿主类必须提供的实例属性 (在 __init__ / _init_tracking_vars 中初始化):
  - self.project_config (dict)
  - self._tracking_objects / _tracking_lost_frames / _tracking_recently_lost
  - self._tracking_transferred_ids / _tracking_display_map / _tracking_class_counters
  - self._tracking_order_seq / _tracking_registered_positions / _tracking_locked_ids
  - self._tracking_appearance / _tracking_stable_frames / _tracking_prev_positions
  - self._tracking_cycle_active / _tracking_gone_frames / _tracking_trigger_frames
  - self._tracking_prev_count / _tracking_had_roi_objects
  - self._event_state / _event_counters / _event_visible_frames / _event_gone_frames_count
  - self._event_first_seen / _event_last_seen
  - self._stack_state / _stack_counters / _stack_visible_frames / _stack_disappeared_at
  - self._container_mode / _container_label
  - self.cycle_start_time / self.fps_inference
  - self.step_screenshots / self._last_screenshot_time

宿主类必须提供的方法:
  - self.start_cycle()
  - self._is_in_roi(det) / self._try_reid_match() / self._bbox_iou()
  - self._boost_score_with_appearance() / self._get_display_prefix()
  - self._rebuild_checklist() / self._settle_counting_cycle()
  - self._update_container_grouping()
"""
from __future__ import annotations

import time
import cv2
import numpy as np


class TrackingMixin:
    # ===================== _update_tracking_stats 拆分 (v2.7.16 P5b) =====================
    # 原 728 行单体, 拆成主流程 (~80 行) + 11 个职责单一辅助方法:
    #   _tracking_load_step_config       : 解析 steps_config → per_class_*/event_steps/stack_steps/max_*
    #   _tracking_apply_max_recognized   : N 上限后处理, 多余 detection 的 track_id 改为最近 keeper
    #   _tracking_collect_frame_dets     : 一帧检测分类 → frame_detections / trigger_visible / event_labels_seen
    #   _tracking_phase1_position_lock   : Phase 1 位置锁匹配
    #   _tracking_phase2_id_match        : Phase 2 track_id 匹配 (含 id_lock / recently_lost / active 重定位 / 新分配)
    #   _tracking_apply_anti_flicker     : ID Lock + Swap Detection + Appearance Match
    #   _tracking_increment_lost_expire  : registered_positions 清理 + 失帧累加 + 过期清理
    #   _tracking_run_event_fsm          : Event counting FSM (动作计数)
    #   _tracking_run_stack_fsm          : Stack mode FSM (堆叠计数)
    #   _tracking_capture_screenshots    : 当前帧可见对象的截图 (限频 1Hz)
    #   _tracking_check_settlement       : 计算 active_count + 三种 cycle_strategy 判定 + min_cycle_age 守门
    def _tracking_load_step_config(self, expected_items: dict) -> dict:
        """解析 steps_config, 返回 dict 含 per_class_*/event_steps/stack_steps/max_*。

        副作用: 把 event_steps 与 stack_steps 的 required_count 注入 expected_items (inplace)。
        """
        per_class_lost_sec = {}
        per_class_position_lock = {}
        event_steps = {}
        # v2.7.4: 堆叠模式 + 最大识别数 (仅 count_mode=track 生效)
        stack_steps = {}              # {label: {'reappear_seconds': float, 'required_count': int}}
        max_recognized_per_label = {}  # {label: N>0}; 0 或缺失 = 无上限
        for step in self.project_config.get('steps_config', []):
            if not step.get('enabled', True):
                continue
            lbl = step.get('label', '')
            if step.get('tracking_max_lost_seconds') is not None:
                per_class_lost_sec[lbl] = step['tracking_max_lost_seconds']
            if step.get('tracking_position_lock'):
                per_class_position_lock[lbl] = True
            if step.get('count_mode') == 'event':
                event_steps[lbl] = {
                    'required_count': step.get('event_required_count', 1),
                    'min_visible_frames': step.get('event_min_visible_frames', 3),
                    'gone_frames': step.get('event_gone_frames', 8),
                }
            # v2.7.4: 堆叠模式与最大识别数仅在 count_mode=track 下生效
            if step.get('count_mode', 'track') == 'track':
                if step.get('stack_enabled'):
                    try:
                        stack_steps[lbl] = {
                            'reappear_seconds': float(step.get('stack_reappear_seconds', 1.0) or 1.0),
                            'required_count': max(2, int(step.get('stack_required_count', 2) or 2)),
                        }
                    except (TypeError, ValueError):
                        pass
                try:
                    mr = int(step.get('max_recognized', 0) or 0)
                    if mr > 0:
                        max_recognized_per_label[lbl] = mr
                except (TypeError, ValueError):
                    pass

        for lbl, cfg in event_steps.items():
            expected_items[lbl] = cfg['required_count']
        # v2.7.4: 堆叠模式期望数量也注入 expected_items
        for lbl, cfg in stack_steps.items():
            expected_items[lbl] = cfg['required_count']

        max_lost_sec = max(per_class_lost_sec.values()) if per_class_lost_sec else 5.0
        return {
            'per_class_lost_sec': per_class_lost_sec,
            'per_class_position_lock': per_class_position_lock,
            'event_steps': event_steps,
            'stack_steps': stack_steps,
            'max_recognized_per_label': max_recognized_per_label,
            'max_lost_sec': max_lost_sec,
        }

    def _tracking_apply_max_recognized(self, detections: list, max_recognized_per_label: dict):
        """v2.7.4: 同一 label 同时检测到 > N 个时, 保留置信度最高的 N 个原始 track_id;
        其余 detection 的 track_id 强制改为最近 keeper 的 track_id。

        注意: 只改输出 track_id, 不影响 ByteTrack 内部状态 (下一帧仍正常跟踪)。
        """
        if not max_recognized_per_label:
            return
        from collections import defaultdict as _dd
        _by_label = _dd(list)
        for _det in detections:
            _lbl = _det.get('label', '')
            if _lbl in max_recognized_per_label and _det.get('track_id', -1) >= 0:
                _by_label[_lbl].append(_det)
        for _lbl, _dets in _by_label.items():
            _N = max_recognized_per_label[_lbl]
            if len(_dets) <= _N:
                continue
            _dets_sorted = sorted(_dets, key=lambda d: -float(d.get('confidence', 0) or 0))
            _keepers = _dets_sorted[:_N]
            _keeper_ids = {id(k) for k in _keepers}
            for _d in _dets:
                if id(_d) in _keeper_ids:
                    continue
                _cx = _d.get('x', 0) + _d.get('w', 0) / 2
                _cy = _d.get('y', 0) + _d.get('h', 0) / 2
                _best_k = None
                _best_dist = float('inf')
                for _k in _keepers:
                    _kcx = _k.get('x', 0) + _k.get('w', 0) / 2
                    _kcy = _k.get('y', 0) + _k.get('h', 0) / 2
                    _dist = (_cx - _kcx) ** 2 + (_cy - _kcy) ** 2
                    if _dist < _best_dist:
                        _best_dist = _dist
                        _best_k = _k
                if _best_k is not None:
                    _d['track_id'] = _best_k.get('track_id', _d.get('track_id', -1))

    def _tracking_collect_frame_dets(self, detections, trigger_label, cycle_strategy, event_steps):
        """一帧检测分类: trigger 标签 / event 标签 / 普通 track_id 标签。

        返回 (frame_detections, trigger_visible, event_labels_seen)。
        - trigger_visible: cycle_strategy='trigger' 时, trigger_label 是否可见
        - event_labels_seen: count_mode=event 类标签里在 ROI 内可见的集合
        - frame_detections: 普通 track 类标签 (track_id >= 0) 的检测列表 (含 in_roi 预计算)
        """
        trigger_visible = False
        event_labels_seen = set()
        frame_detections = []
        for det in detections:
            label = det.get('label', '')
            track_id = det.get('track_id', -1)
            if not label:
                continue
            if label == trigger_label and cycle_strategy == 'trigger':
                trigger_visible = True
                continue
            if label in event_steps:
                if self._is_in_roi(det):
                    event_labels_seen.add(label)
                continue
            if track_id < 0:
                continue
            new_bbox = {'x': det['x'], 'y': det['y'], 'w': det['w'], 'h': det['h']}
            in_roi = self._is_in_roi(det)
            frame_detections.append({
                'label': label, 'track_id': track_id,
                'bbox': new_bbox, 'in_roi': in_roi,
            })
        return frame_detections, trigger_visible, event_labels_seen

    def _tracking_phase1_position_lock(self, frame_detections, per_class_position_lock,
                                        current_time, cycle_strategy,
                                        seen_track_ids, pos_lock_assigned_dids):
        """Phase 1: 位置锁匹配 (per-class opt-in)。

        - 已存在 obj: 更新位置 + EMA 更新注册位置
        - 不在 ROI 内的新 track: 仅标记 handled
        - ROI 内新 track: 找最近的 registered_position 顶替 (转移 display_id)
        - 找不到 registered_position: 标记 _pos_lock_new, 让 Phase 2 走新分配路径

        副作用: 修改 self._tracking_objects/display_map/lost_frames/transferred_ids/registered_positions/recently_lost,
                seen_track_ids, pos_lock_assigned_dids。
        返回: pos_lock_handled_tids (Phase 2 跳过)
        """
        pos_lock_handled_tids = set()
        for fd in frame_detections:
            label, track_id, new_bbox, in_roi = fd['label'], fd['track_id'], fd['bbox'], fd['in_roi']
            if not per_class_position_lock.get(label, False):
                continue

            bbox_cx = new_bbox['x'] + new_bbox['w'] / 2
            bbox_cy = new_bbox['y'] + new_bbox['h'] / 2

            if track_id in self._tracking_objects:
                obj = self._tracking_objects[track_id]
                did = obj['display_id']
                if did in self._tracking_registered_positions:
                    reg = self._tracking_registered_positions[did]
                    reg['cx'] = 0.8 * reg['cx'] + 0.2 * bbox_cx
                    reg['cy'] = 0.8 * reg['cy'] + 0.2 * bbox_cy
                    reg['stable_frames'] = reg.get('stable_frames', 0) + 1
                obj['bbox'] = new_bbox
                if in_roi or cycle_strategy != 'roi_exit':
                    obj['last_seen'] = current_time
                    self._tracking_lost_frames[track_id] = 0
                    seen_track_ids.add(track_id)
                pos_lock_handled_tids.add(track_id)
                pos_lock_assigned_dids.add(did)
                continue

            if not in_roi:
                pos_lock_handled_tids.add(track_id)
                continue

            best_reg_did = None
            best_reg_dist = float('inf')
            for reg_did, reg in self._tracking_registered_positions.items():
                if reg['class_name'] != label or reg_did in pos_lock_assigned_dids:
                    continue
                already_active = any(
                    o['display_id'] == reg_did and tid in seen_track_ids
                    for tid, o in self._tracking_objects.items()
                )
                if already_active:
                    continue
                dist = ((bbox_cx - reg['cx'])**2 + (bbox_cy - reg['cy'])**2)**0.5
                ref_size = max(new_bbox['w'], new_bbox['h'], 0.01)
                if dist < ref_size * 2.0 and dist < best_reg_dist:
                    best_reg_dist = dist
                    best_reg_did = reg_did

            if best_reg_did:
                for old_tid, old_obj in list(self._tracking_objects.items()):
                    if old_obj.get('display_id') == best_reg_did:
                        self._tracking_transferred_ids[old_tid] = current_time
                        del self._tracking_objects[old_tid]
                        self._tracking_display_map.pop(old_tid, None)
                        self._tracking_lost_frames.pop(old_tid, None)
                        break
                for old_lost_tid in list(self._tracking_recently_lost.keys()):
                    if self._tracking_recently_lost[old_lost_tid].get('display_id') == best_reg_did:
                        del self._tracking_recently_lost[old_lost_tid]
                        break

                self._tracking_objects[track_id] = {
                    'class_name': label, 'display_id': best_reg_did,
                    'first_seen': current_time, 'last_seen': current_time,
                    'bbox': new_bbox,
                    'order_idx': self._tracking_registered_positions[best_reg_did].get('order_idx', 0),
                }
                self._tracking_display_map[track_id] = best_reg_did
                self._tracking_lost_frames[track_id] = 0
                seen_track_ids.add(track_id)
                pos_lock_handled_tids.add(track_id)
                pos_lock_assigned_dids.add(best_reg_did)
                reg = self._tracking_registered_positions[best_reg_did]
                reg['cx'] = 0.8 * reg['cx'] + 0.2 * bbox_cx
                reg['cy'] = 0.8 * reg['cy'] + 0.2 * bbox_cy
                continue

            pos_lock_handled_tids.add(track_id)
            fd['_pos_lock_new'] = True
        return pos_lock_handled_tids

    def _tracking_phase2_id_match(self, frame_detections, pos_lock_handled_tids,
                                   per_class_position_lock, id_lock, id_lock_frames,
                                   appearance_match, max_lost_sec, current_time, cycle_strategy,
                                   seen_track_ids, pos_lock_assigned_dids,
                                   expected_items, original_frame):
        """Phase 2: 原始 track_id 匹配 (非 position-lock 物品 + position-lock 新增物品)。

        子阶段 (按顺序尝试):
          a) 跳过已被 Phase 1 处理 (除非标 _pos_lock_new)
          b) transferred_ids 节流: max_lost_sec 内的旧 ID 直接跳过
          c) 已在 _tracking_objects: 更新位置
          d) 不在 ROI: 跳过
          e) ID Lock 重连 (id_lock=True): 找最近 locked_id 顶替
          f) recently_lost re-id: IoU + 可选外观分匹配最佳丢失对象
          g) active 重定位: 同类目 lost_frames>0 的对象顶替
          h) 新分配 display_id (含 reuse 已超额 → 反向找空 ID 或复用最大 ID)
             并按 expected_items 自动开启周期。
        """
        for fd in frame_detections:
            label, track_id, new_bbox, in_roi = fd['label'], fd['track_id'], fd['bbox'], fd['in_roi']

            if track_id in pos_lock_handled_tids and not fd.get('_pos_lock_new'):
                continue

            if track_id in self._tracking_transferred_ids:
                if current_time - self._tracking_transferred_ids[track_id] < max_lost_sec:
                    continue
                del self._tracking_transferred_ids[track_id]

            if track_id in self._tracking_objects:
                obj = self._tracking_objects[track_id]
                # yolo tracker 会回收 track_id 给新对象复用, 类别可能跟之前的不同.
                # 类别不同时不能复用旧 display_id, 否则会出现 label/display_id 错位.
                # 这里把旧关联清掉, 走下面的"新对象"路径重新分配.
                if obj.get('class_name') != label:
                    old_did = obj.get('display_id', '')
                    old_class = obj.get('class_name', '')
                    self._tracking_objects.pop(track_id, None)
                    self._tracking_display_map.pop(track_id, None)
                    self._tracking_lost_frames.pop(track_id, None)
                    # 如果旧 obj 是容器(箱子), _box_objects 里的对应记录也是脏数据,
                    # 不清掉会让前端"容器清点"显示一个画面里其实不存在的"幽灵箱子"
                    # (要等 gone_confirm_frames 倒计时才会自然消失).
                    if (getattr(self, '_container_mode', False)
                            and old_class == getattr(self, '_container_label', '')
                            and old_did
                            and old_did in getattr(self, '_box_objects', {})):
                        self._box_objects.pop(old_did, None)
                else:
                    obj['bbox'] = new_bbox
                    if in_roi or cycle_strategy != 'roi_exit':
                        obj['last_seen'] = current_time
                        self._tracking_lost_frames[track_id] = 0
                        seen_track_ids.add(track_id)
                    continue

            if not in_roi:
                continue

            seen_track_ids.add(track_id)
            merged = False

            # e) ID Lock 重连
            if id_lock and not merged and not per_class_position_lock.get(label, False):
                bbox_cx = new_bbox['x'] + new_bbox['w'] / 2
                bbox_cy = new_bbox['y'] + new_bbox['h'] / 2
                best_lock_dist = float('inf')
                best_lock_did = None
                for locked_did, (lcx, lcy, locked_tid) in self._tracking_locked_ids.items():
                    if (locked_tid in self._tracking_objects
                            and self._tracking_objects[locked_tid].get('class_name') == label
                            and locked_tid not in seen_track_ids):
                        dist = ((bbox_cx - lcx)**2 + (bbox_cy - lcy)**2)**0.5
                        ref_size = max(new_bbox['w'], new_bbox['h'], 0.01)
                        if dist < ref_size * 1.5 and dist < best_lock_dist:
                            best_lock_dist = dist
                            best_lock_did = locked_did
                if best_lock_did:
                    for ltid_check, lobj in list(self._tracking_objects.items()):
                        if lobj.get('display_id') == best_lock_did:
                            old_display = lobj['display_id']
                            self._tracking_transferred_ids[ltid_check] = current_time
                            del self._tracking_objects[ltid_check]
                            self._tracking_display_map.pop(ltid_check, None)
                            self._tracking_lost_frames.pop(ltid_check, None)
                            self._tracking_objects[track_id] = {
                                'class_name': label, 'display_id': old_display,
                                'first_seen': lobj.get('first_seen', current_time),
                                'last_seen': current_time, 'bbox': new_bbox,
                                'order_idx': lobj.get('order_idx', 0),
                            }
                            self._tracking_display_map[track_id] = old_display
                            self._tracking_lost_frames[track_id] = 0
                            merged = True
                            bbox_cx_new = new_bbox['x'] + new_bbox['w'] / 2
                            bbox_cy_new = new_bbox['y'] + new_bbox['h'] / 2
                            self._tracking_locked_ids[old_display] = (bbox_cx_new, bbox_cy_new, track_id)
                            break

            # f) recently_lost re-id
            if not merged:
                best_lost_tid, best_lost_obj = None, None
                best_lost_score = -1
                for lost_tid, lost_obj in list(self._tracking_recently_lost.items()):
                    if (lost_obj['class_name'] == label
                            and lost_obj['display_id'] not in pos_lock_assigned_dids
                            and current_time - lost_obj['lost_time'] < max_lost_sec
                            and self._try_reid_match(label, new_bbox, lost_obj['bbox'])):
                        score = self._bbox_iou(lost_obj['bbox'], new_bbox)
                        if appearance_match and lost_obj['display_id'] in self._tracking_appearance:
                            score = self._boost_score_with_appearance(score, lost_obj['display_id'], new_bbox, original_frame)
                        if score > best_lost_score:
                            best_lost_score = score
                            best_lost_tid = lost_tid
                            best_lost_obj = lost_obj
                if best_lost_obj:
                    self._tracking_objects[track_id] = {
                        'class_name': label,
                        'display_id': best_lost_obj['display_id'],
                        'first_seen': best_lost_obj.get('first_seen', current_time),
                        'last_seen': current_time, 'bbox': new_bbox,
                        'order_idx': best_lost_obj['order_idx'],
                    }
                    self._tracking_display_map[track_id] = best_lost_obj['display_id']
                    self._tracking_lost_frames[track_id] = 0
                    del self._tracking_recently_lost[best_lost_tid]
                    merged = True

            # g) active 重定位 (同类 lost_frames>0)
            if not merged:
                best_active_tid, best_active_obj = None, None
                best_active_score = -1
                for active_tid, active_obj in list(self._tracking_objects.items()):
                    lost_f = self._tracking_lost_frames.get(active_tid, 0)
                    if (lost_f > 0
                            and active_obj['class_name'] == label
                            and active_obj['display_id'] not in pos_lock_assigned_dids
                            and self._try_reid_match(label, new_bbox, active_obj['bbox'])):
                        score = self._bbox_iou(active_obj['bbox'], new_bbox)
                        if appearance_match and active_obj['display_id'] in self._tracking_appearance:
                            score = self._boost_score_with_appearance(score, active_obj['display_id'], new_bbox, original_frame)
                        if score > best_active_score:
                            best_active_score = score
                            best_active_tid = active_tid
                            best_active_obj = active_obj
                if best_active_obj:
                    old_display = best_active_obj['display_id']
                    self._tracking_transferred_ids[best_active_tid] = current_time
                    del self._tracking_objects[best_active_tid]
                    if best_active_tid in self._tracking_display_map:
                        del self._tracking_display_map[best_active_tid]
                    if best_active_tid in self._tracking_lost_frames:
                        del self._tracking_lost_frames[best_active_tid]
                    self._tracking_objects[track_id] = {
                        'class_name': label, 'display_id': old_display,
                        'first_seen': best_active_obj['first_seen'],
                        'last_seen': current_time, 'bbox': new_bbox,
                        'order_idx': best_active_obj['order_idx'],
                    }
                    self._tracking_display_map[track_id] = old_display
                    self._tracking_lost_frames[track_id] = 0
                    merged = True

            # h) 新分配 display_id + 自动开启周期
            if not merged:
                prefix = self._get_display_prefix(label)
                current_count = self._tracking_class_counters.get(label, 0)
                expected_count = expected_items.get(label, 0)

                reuse_display = None
                if expected_count > 0 and current_count >= expected_count:
                    for lost_obj in self._tracking_recently_lost.values():
                        if lost_obj['class_name'] == label:
                            reuse_display = lost_obj['display_id']
                            break
                    if not reuse_display:
                        active_ids = {o['display_id'] for o in self._tracking_objects.values() if o['class_name'] == label}
                        for i in range(1, current_count + 1):
                            cand = f"{prefix}{i}"
                            if cand not in active_ids:
                                reuse_display = cand
                                break

                if expected_count > 0 and current_count >= expected_count and not reuse_display:
                    reuse_display = f"{prefix}{current_count}"

                if reuse_display:
                    display_id = reuse_display
                else:
                    current_count += 1
                    self._tracking_class_counters[label] = current_count
                    display_id = f"{prefix}{current_count}"

                self._tracking_order_seq += 1
                self._tracking_objects[track_id] = {
                    'class_name': label, 'display_id': display_id,
                    'first_seen': current_time, 'last_seen': current_time,
                    'bbox': new_bbox,
                    'order_idx': self._tracking_order_seq,
                }
                self._tracking_display_map[track_id] = display_id
                self._tracking_lost_frames[track_id] = 0

                if per_class_position_lock.get(label, False):
                    bbox_cx = new_bbox['x'] + new_bbox['w'] / 2
                    bbox_cy = new_bbox['y'] + new_bbox['h'] / 2
                    self._tracking_registered_positions[display_id] = {
                        'class_name': label, 'cx': bbox_cx, 'cy': bbox_cy,
                        'stable_frames': 1, 'order_idx': self._tracking_order_seq,
                    }

                if not self._tracking_cycle_active and label in expected_items:
                    self._tracking_cycle_active = True
                    self.cycle_start_time = current_time
                    self.start_cycle()

    def _tracking_apply_anti_flicker(self, seen_track_ids, per_class_position_lock,
                                      id_lock, id_lock_frames, swap_detection,
                                      appearance_match, original_frame):
        """三块抗抖动: ID Lock (累计稳定帧 → 锁定中心点) +
                       Swap Detection (检测两两位置交换 → 互换 display_id) +
                       Appearance Match (HSV 直方图 EMA 累积外观特征)。
        position-lock 类目的对象一律跳过 (它们走 Phase 1)。
        """
        # ID Lock
        if id_lock:
            for tid in seen_track_ids:
                if tid in self._tracking_objects:
                    obj_cls = self._tracking_objects[tid].get('class_name', '')
                    if per_class_position_lock.get(obj_cls, False):
                        continue
                    self._tracking_stable_frames[tid] = self._tracking_stable_frames.get(tid, 0) + 1
                    if self._tracking_stable_frames[tid] >= id_lock_frames:
                        obj = self._tracking_objects[tid]
                        bbox = obj['bbox']
                        cx = bbox['x'] + bbox['w'] / 2
                        cy = bbox['y'] + bbox['h'] / 2
                        self._tracking_locked_ids[obj['display_id']] = (cx, cy, tid)
            for tid in list(self._tracking_stable_frames.keys()):
                if tid not in seen_track_ids:
                    self._tracking_stable_frames[tid] = 0

        # Swap Detection
        if swap_detection:
            cur_positions = {}
            for tid, obj in self._tracking_objects.items():
                if tid in seen_track_ids and not per_class_position_lock.get(obj.get('class_name', ''), False):
                    bbox = obj['bbox']
                    cx = bbox['x'] + bbox['w'] / 2
                    cy = bbox['y'] + bbox['h'] / 2
                    cur_positions[obj['display_id']] = (cx, cy, obj['class_name'], tid)

            swapped = set()
            for did_a, (cx_a, cy_a, cls_a, tid_a) in cur_positions.items():
                if did_a in swapped:
                    continue
                prev_a = self._tracking_prev_positions.get(did_a)
                if not prev_a:
                    continue
                for did_b, (cx_b, cy_b, cls_b, tid_b) in cur_positions.items():
                    if did_b in swapped or did_b == did_a or cls_b != cls_a:
                        continue
                    prev_b = self._tracking_prev_positions.get(did_b)
                    if not prev_b:
                        continue
                    dist_a_to_prev_a = ((cx_a - prev_a[0])**2 + (cy_a - prev_a[1])**2)**0.5
                    dist_a_to_prev_b = ((cx_a - prev_b[0])**2 + (cy_a - prev_b[1])**2)**0.5
                    dist_b_to_prev_a = ((cx_b - prev_a[0])**2 + (cy_b - prev_a[1])**2)**0.5
                    dist_b_to_prev_b = ((cx_b - prev_b[0])**2 + (cy_b - prev_b[1])**2)**0.5
                    if dist_a_to_prev_b < dist_a_to_prev_a and dist_b_to_prev_a < dist_b_to_prev_b:
                        self._tracking_objects[tid_a]['display_id'] = did_b
                        self._tracking_objects[tid_b]['display_id'] = did_a
                        self._tracking_display_map[tid_a] = did_b
                        self._tracking_display_map[tid_b] = did_a
                        swapped.add(did_a)
                        swapped.add(did_b)
                        break

            self._tracking_prev_positions = {}
            for tid, obj in self._tracking_objects.items():
                if tid in seen_track_ids:
                    bbox = obj['bbox']
                    cx = bbox['x'] + bbox['w'] / 2
                    cy = bbox['y'] + bbox['h'] / 2
                    self._tracking_prev_positions[obj['display_id']] = (cx, cy, obj['class_name'])

        # Appearance Match (HSV 直方图 EMA 累积)
        if appearance_match and original_frame is not None:
            h_img, w_img = original_frame.shape[:2]
            for tid in seen_track_ids:
                if tid not in self._tracking_objects:
                    continue
                obj = self._tracking_objects[tid]
                if per_class_position_lock.get(obj.get('class_name', ''), False):
                    continue
                bbox = obj['bbox']
                x1 = max(0, int(bbox['x'] * w_img))
                y1 = max(0, int(bbox['y'] * h_img))
                x2 = min(w_img, int((bbox['x'] + bbox['w']) * w_img))
                y2 = min(h_img, int((bbox['y'] + bbox['h']) * h_img))
                if x2 - x1 > 5 and y2 - y1 > 5:
                    crop = original_frame[y1:y2, x1:x2]
                    try:
                        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
                        hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
                        cv2.normalize(hist, hist)
                        did = obj['display_id']
                        if did not in self._tracking_appearance:
                            self._tracking_appearance[did] = hist
                        else:
                            self._tracking_appearance[did] = 0.7 * self._tracking_appearance[did] + 0.3 * hist
                    except Exception:
                        pass

    def _tracking_increment_lost_expire(self, seen_track_ids, per_class_lost_sec,
                                         max_lost_sec, current_time):
        """registered_positions 清理 + 失帧累加 + 过期清理。

        - 清理 registered_positions: display_id 既不在 active 也不在 recently_lost
        - 失帧累加: 未见 track 累计 +1 帧, 超过 (item_lost_sec * fps_inference) 移到 recently_lost
        - 过期清理: recently_lost / transferred_ids 超过 2 * max_lost_sec 删除

        v2.7.13 注: 遮挡容忍 "秒 → 帧" 必须用推理 FPS, 因为 _tracking_lost_frames[tid] += 1
                    发生在推理线程每次调用时。
        """
        active_dids = {o['display_id'] for o in self._tracking_objects.values()}
        lost_dids = {o['display_id'] for o in self._tracking_recently_lost.values()}
        for reg_did in list(self._tracking_registered_positions.keys()):
            if reg_did not in active_dids and reg_did not in lost_dids:
                del self._tracking_registered_positions[reg_did]

        # 失帧累加 → 移到 recently_lost
        for tid in list(self._tracking_lost_frames.keys()):
            if tid not in seen_track_ids and tid in self._tracking_objects:
                self._tracking_lost_frames[tid] = self._tracking_lost_frames.get(tid, 0) + 1
                obj_label = self._tracking_objects[tid].get('class_name', '')
                item_lost_sec = per_class_lost_sec.get(obj_label, max_lost_sec)
                item_lost_frames = int(item_lost_sec * max(self.fps_inference, 10))
                if self._tracking_lost_frames[tid] >= item_lost_frames:
                    obj = self._tracking_objects[tid]
                    print(f"[Tracking] 遮挡容忍超时: {obj['display_id']}({obj_label}) 消失 {item_lost_sec:.1f}s ({item_lost_frames} 帧)，移除")
                    self._tracking_recently_lost[tid] = {
                        'class_name': obj['class_name'],
                        'display_id': obj['display_id'],
                        'bbox': obj['bbox'],
                        'lost_time': current_time,
                        'first_seen': obj.get('first_seen', current_time),
                        'order_idx': obj.get('order_idx', 0),
                    }
                    del self._tracking_objects[tid]
                    del self._tracking_lost_frames[tid]
                    if tid in self._tracking_display_map:
                        del self._tracking_display_map[tid]

        # 过期清理 (2 * max_lost_sec 兜底)
        expire_cutoff = current_time - max_lost_sec * 2
        for tid in list(self._tracking_recently_lost.keys()):
            if self._tracking_recently_lost[tid]['lost_time'] < expire_cutoff:
                del self._tracking_recently_lost[tid]
        for tid in list(self._tracking_transferred_ids.keys()):
            if current_time - self._tracking_transferred_ids[tid] > max_lost_sec * 2:
                del self._tracking_transferred_ids[tid]

    def _tracking_run_event_fsm(self, event_steps, event_labels_seen, current_time):
        """Event counting FSM (动作计数): idle → visible → gone → idle 三态机。

        - idle + visible: 进入 visible, 自动开启周期
        - visible + visible: visible_frames++
        - visible + gone: visible_frames 达 min 则进入 gone, 否则回 idle
        - gone + visible: 回 visible
        - gone + gone: gone_frames++ , 达阈值 → counter+1 + 回 idle
        """
        if not event_steps:
            return
        for label, cfg in event_steps.items():
            if label not in self._event_state:
                self._event_state[label] = 'idle'
                self._event_counters[label] = 0
                self._event_visible_frames[label] = 0
                self._event_gone_frames_count[label] = 0

            state = self._event_state[label]
            is_visible = label in event_labels_seen

            if state == 'idle':
                if is_visible:
                    self._event_state[label] = 'visible'
                    self._event_visible_frames[label] = 1
                    if label not in self._event_first_seen:
                        self._event_first_seen[label] = current_time
                    if not self._tracking_cycle_active:
                        self._tracking_cycle_active = True
                        self.cycle_start_time = current_time
                        self.start_cycle()
            elif state == 'visible':
                if is_visible:
                    self._event_visible_frames[label] += 1
                else:
                    if self._event_visible_frames[label] >= cfg['min_visible_frames']:
                        self._event_state[label] = 'gone'
                        self._event_gone_frames_count[label] = 1
                    else:
                        self._event_state[label] = 'idle'
                        self._event_visible_frames[label] = 0
            elif state == 'gone':
                if is_visible:
                    self._event_state[label] = 'visible'
                    self._event_visible_frames[label] += 1
                    self._event_gone_frames_count[label] = 0
                else:
                    self._event_gone_frames_count[label] += 1
                    if self._event_gone_frames_count[label] >= cfg['gone_frames']:
                        self._event_counters[label] = self._event_counters.get(label, 0) + 1
                        self._event_last_seen[label] = current_time
                        self._event_state[label] = 'idle'
                        self._event_visible_frames[label] = 0
                        self._event_gone_frames_count[label] = 0
                        print(f"[Tracking-Event] {label} event #{self._event_counters[label]}/{cfg['required_count']} confirmed")

    def _tracking_run_stack_fsm(self, stack_steps, detections, current_time):
        """Stack mode FSM (堆叠计数, v2.7.4): idle → visible → disappeared → visible 循环。

        - 第一次 idle→visible 直接计 1 (第一层)
        - visible → disappeared: 记下消失时刻, 等再次出现
        - disappeared → visible 且消失 >= reappear_seconds: 计 +1 (新一层)
        - 否则只重回 visible 不计数

        注: stack_counters 不直接覆盖 _tracking_class_counters (避免影响 ByteTrack display_id 分配),
        在 _rebuild_checklist 中用 max 策略合并。仅在 logic_mode=tracking + count_mode=track 下生效。
        """
        if not stack_steps:
            return
        stack_label_visible = {lbl: False for lbl in stack_steps}
        for _det in detections:
            _lbl = _det.get('label', '')
            if _lbl in stack_steps and self._is_in_roi(_det):
                stack_label_visible[_lbl] = True
        for label, cfg in stack_steps.items():
            if label not in self._stack_state:
                self._stack_state[label] = 'idle'
                self._stack_counters[label] = 0
                self._stack_visible_frames[label] = 0
            state = self._stack_state[label]
            is_visible = stack_label_visible[label]
            if state == 'idle':
                if is_visible:
                    self._stack_state[label] = 'visible'
                    self._stack_visible_frames[label] = 1
                    self._stack_counters[label] = self._stack_counters.get(label, 0) + 1
                    if not self._tracking_cycle_active:
                        self._tracking_cycle_active = True
                        self.cycle_start_time = current_time
                        try:
                            self.start_cycle()
                        except Exception:
                            pass
                    print(f"[Stack] {label} layer #{self._stack_counters[label]}/{cfg['required_count']} (initial)")
            elif state == 'visible':
                if is_visible:
                    self._stack_visible_frames[label] += 1
                else:
                    self._stack_state[label] = 'disappeared'
                    self._stack_disappeared_at[label] = current_time
            elif state == 'disappeared':
                if is_visible:
                    disappeared_for = current_time - self._stack_disappeared_at.get(label, current_time)
                    if disappeared_for >= cfg['reappear_seconds']:
                        self._stack_counters[label] = self._stack_counters.get(label, 0) + 1
                        print(f"[Stack] {label} layer #{self._stack_counters[label]}/{cfg['required_count']} (reappeared after {disappeared_for:.2f}s)")
                    self._stack_state[label] = 'visible'
                    self._stack_visible_frames[label] = 1

    def _tracking_capture_screenshots(self, seen_track_ids, original_frame):
        """为当前帧中可见的跟踪对象生成步骤截图 (限频每秒最多 1 次)。"""
        if original_frame is None or not seen_track_ids:
            return
        import base64
        _ss_now = time.time()
        if _ss_now - getattr(self, '_last_screenshot_time', 0) < 1.0:
            return
        self._last_screenshot_time = _ss_now

        img_h, img_w = original_frame.shape[:2]
        pad = 20
        for tid in seen_track_ids:
            obj = self._tracking_objects.get(tid)
            if not obj:
                continue
            label = obj['class_name']
            bbox = obj['bbox']
            cx1 = max(0, int(bbox['x'] * img_w) - pad)
            cy1 = max(0, int(bbox['y'] * img_h) - pad)
            cx2 = min(img_w, int((bbox['x'] + bbox['w']) * img_w) + pad)
            cy2 = min(img_h, int((bbox['y'] + bbox['h']) * img_h) + pad)
            if cx2 > cx1 and cy2 > cy1:
                crop = original_frame[cy1:cy2, cx1:cx2]
                _, buffer = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                self.step_screenshots[label] = base64.b64encode(buffer).decode('utf-8')

    def _tracking_check_settlement(self, expected_items, per_class_lost_sec, max_lost_sec,
                                    cycle_strategy, gone_threshold, gone_confirm_frames,
                                    trigger_visible, trigger_min_frames,
                                    seen_track_ids, current_time) -> bool:
        """统计 active_count + 三种 cycle_strategy 判定 + min_cycle_age 守门。

        cycle_strategy:
          - all_gone / container: 连续 gone_confirm_frames 帧 active<=gone_threshold → settle
          - trigger             : trigger_label 累计可见 trigger_min_frames 帧 → settle
          - roi_exit            : 周期内出现过 ROI 对象后, 连续 gone_confirm_frames 帧 active<=gone_threshold → settle

        v2.7.13 注: tolerance 帧数换算基于推理 FPS; 去掉了 1 秒最低等待兜底,
        只保留 max_lost_sec 这个业务门槛 (max_lost_sec=0 则完全靠帧数阈值)。
        """
        active_count = 0
        fps = max(self.fps_inference, 10)
        for tid in self._tracking_objects:
            obj_label = self._tracking_objects[tid].get('class_name', '')
            if self._container_mode and obj_label == self._container_label:
                continue
            if obj_label not in expected_items:
                continue
            item_sec = per_class_lost_sec.get(obj_label, max_lost_sec)
            item_tolerance_frames = int(item_sec * fps)
            lost_f = self._tracking_lost_frames.get(tid, 0)
            if cycle_strategy == 'roi_exit':
                if tid in seen_track_ids:
                    active_count += 1
                elif lost_f < item_tolerance_frames:
                    active_count += 1
            else:
                if lost_f < item_tolerance_frames:
                    active_count += 1

        if active_count > 0:
            self._tracking_had_roi_objects = True

        should_settle = False

        if cycle_strategy in ('all_gone', 'container'):
            if active_count <= gone_threshold:
                self._tracking_gone_frames += 1
                if self._tracking_gone_frames == 1:
                    print(f"[Tracking] all gone, confirming: {gone_confirm_frames} frames")
                if self._tracking_gone_frames >= gone_confirm_frames:
                    should_settle = True
            else:
                if self._tracking_gone_frames > 0:
                    print(f"[Tracking] objects reappeared, reset ({self._tracking_gone_frames}/{gone_confirm_frames})")
                self._tracking_gone_frames = 0
        elif cycle_strategy == 'trigger':
            if trigger_visible:
                self._tracking_trigger_frames += 1
                if self._tracking_trigger_frames >= trigger_min_frames:
                    should_settle = True
            else:
                self._tracking_trigger_frames = 0
        elif cycle_strategy == 'roi_exit':
            if self._tracking_had_roi_objects and active_count <= gone_threshold:
                self._tracking_gone_frames += 1
                if self._tracking_gone_frames == 1:
                    print(f"[Tracking] ROI objects gone, confirming: {gone_confirm_frames} frames")
                if self._tracking_gone_frames >= gone_confirm_frames:
                    should_settle = True
            else:
                if self._tracking_gone_frames > 0:
                    print(f"[Tracking] ROI objects reappeared, reset ({self._tracking_gone_frames}/{gone_confirm_frames})")
                self._tracking_gone_frames = 0

        self._tracking_prev_count = active_count

        # min_cycle_age 守门
        if should_settle and max_lost_sec > 0:
            cycle_age = current_time - (self.cycle_start_time or current_time)
            if cycle_age < max_lost_sec:
                should_settle = False

        return should_settle

    def _update_tracking_stats(self, detections: list, original_frame: np.ndarray):
        """Process detections in tracking mode (物品清点, v2.7.16 P5b 拆分版)。

        支持 3 种周期结束策略: all_gone / trigger / roi_exit。
        支持可选的顺序校验和 ROI 过滤; 含应用层 re-ID 处理 ByteTrack ID 跳变。
        本方法只剩 ~80 行调度, 实际工作分摊在 11 个职责单一的辅助方法。
        """
        current_time = time.time()
        if not self.project_config:
            return

        pcfg = self.project_config.get('pipeline_config', {})
        expected_items = pcfg.get('counting_expected_items', {})
        cycle_strategy = pcfg.get('tracking_cycle_strategy', 'all_gone')
        trigger_label = pcfg.get('tracking_trigger_label', '')
        trigger_min_frames = pcfg.get('tracking_trigger_min_frames', 15)
        gone_threshold = pcfg.get('tracking_gone_threshold', 0)
        gone_confirm_frames = pcfg.get('tracking_gone_confirm_frames', 30)
        check_order = pcfg.get('tracking_check_order', False)
        expected_order = pcfg.get('tracking_expected_order', [])
        swap_detection = pcfg.get('tracking_swap_detection', False)
        appearance_match = pcfg.get('tracking_appearance_match', False)
        id_lock = pcfg.get('tracking_id_lock', False)
        id_lock_frames = pcfg.get('tracking_id_lock_frames', 15)

        # 1) 解析 steps_config (会把 event/stack 期望数注入 expected_items)
        cfg = self._tracking_load_step_config(expected_items)

        # 2) 最大识别数 ID 后处理 (改本帧 detections 的 track_id)
        self._tracking_apply_max_recognized(detections, cfg['max_recognized_per_label'])

        # 3) 帧检测分类
        frame_detections, trigger_visible, event_labels_seen = \
            self._tracking_collect_frame_dets(detections, trigger_label, cycle_strategy, cfg['event_steps'])

        seen_track_ids = set()
        pos_lock_assigned_dids = set()

        # 4) Phase 1: 位置锁匹配
        pos_lock_handled_tids = self._tracking_phase1_position_lock(
            frame_detections, cfg['per_class_position_lock'], current_time,
            cycle_strategy, seen_track_ids, pos_lock_assigned_dids)

        # 5) Phase 2: track_id 匹配 (含 id_lock / recently_lost / active 重定位 / 新分配)
        self._tracking_phase2_id_match(
            frame_detections, pos_lock_handled_tids, cfg['per_class_position_lock'],
            id_lock, id_lock_frames, appearance_match, cfg['max_lost_sec'],
            current_time, cycle_strategy, seen_track_ids, pos_lock_assigned_dids,
            expected_items, original_frame)

        # 6) Anti-flicker: ID Lock + Swap + Appearance
        self._tracking_apply_anti_flicker(
            seen_track_ids, cfg['per_class_position_lock'],
            id_lock, id_lock_frames, swap_detection, appearance_match, original_frame)

        # 7) registered_positions 清理 + 失帧累加 + 过期清理
        self._tracking_increment_lost_expire(
            seen_track_ids, cfg['per_class_lost_sec'], cfg['max_lost_sec'], current_time)

        # 8) Event FSM (动作计数)
        self._tracking_run_event_fsm(cfg['event_steps'], event_labels_seen, current_time)

        # 9) Stack FSM (堆叠计数)
        self._tracking_run_stack_fsm(cfg['stack_steps'], detections, current_time)

        # 10) Container 分组
        if self._container_mode and self._container_label:
            self._update_container_grouping(
                expected_items, current_time,
                gone_confirm_frames=gone_confirm_frames,
                cycle_strategy=cycle_strategy,
            )

        # 11) 截图 (限频 1Hz)
        self._tracking_capture_screenshots(seen_track_ids, original_frame)

        self._rebuild_checklist(expected_items)

        if not self._tracking_cycle_active:
            return

        # 12) 结算判定 → 触发 _settle_counting_cycle
        should_settle = self._tracking_check_settlement(
            expected_items, cfg['per_class_lost_sec'], cfg['max_lost_sec'],
            cycle_strategy, gone_threshold, gone_confirm_frames,
            trigger_visible, trigger_min_frames,
            seen_track_ids, current_time)

        if should_settle:
            print(f"[Tracking] settle confirmed ({self._tracking_gone_frames}/{gone_confirm_frames} frames)")
            self._settle_counting_cycle(expected_items, check_order, expected_order)
