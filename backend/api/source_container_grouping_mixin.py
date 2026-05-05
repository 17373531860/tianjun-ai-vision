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

        v3.2.0 container_id_drift_merge_iou (可选, 默认 0=关闭):
        ByteTrack 在工人遮挡时切换 box 的 track_id, 即使 source_tracking_mixin 的
        phase2 id_match (recently_lost re-id, 5 秒窗口) 也接不住跨 5 秒的接管时,
        新 track 在 _update_container_grouping 这一层会进入新条目, 老条目空着等
        gone-confirm 后被 _settle_box 当 NG 入账. 本配置 > 0 时, 创建新 _box_objects
        条目前先扫已存在的 gone-confirm 中老 did, 找到 IoU >= 阈值的就复用老 did
        继续累计装件, 不开新条目. 推荐值 0.5 (位置明显重合即合并). 0.7+ 更保守.
        """
        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items() if k != container_label}

        # v2.7.17: 单箱模式 - 默认开启, 由 pipeline_config.container_box_mode 控制
        pcfg = (self.project_config or {}).get('pipeline_config', {}) if self.project_config else {}
        single_box_mode = (pcfg.get('container_box_mode', 'single') or 'single') == 'single'

        # v3.2.0: ID 漂移合并阈值. 0 = 关闭(默认, 兼容老项目). 0.5 推荐.
        # 当一个新 box did 即将进入 _box_objects 时, 先扫已存在的 gone-confirm 中的老
        # did, 如果同位置 IoU >= 此阈值, 视为同一物理箱继续累计装件 (复用老 did,
        # 重置 gone_frames=0, 改写 _tracking_objects[tid].display_id 让后续帧对齐).
        # 这是 source_tracking_mixin phase2 id_match 之后的最后一道兜底,
        # 对付 ByteTrack 切 ID 又超过 max_lost_sec 已被 phase2 释放的边界场景.
        try:
            _miou = pcfg.get('container_id_drift_merge_iou', 0)
            id_drift_merge_iou = float(_miou) if _miou not in (None, '') else 0.0
        except (TypeError, ValueError):
            id_drift_merge_iou = 0.0
        try:
            _mgf = pcfg.get('container_id_drift_merge_max_gone_frames', 30)
            id_drift_max_gone = int(_mgf) if _mgf not in (None, '') else 30
        except (TypeError, ValueError):
            id_drift_max_gone = 30
        id_drift_merge_enabled = id_drift_merge_iou > 0

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
                if did not in self._box_objects:
                    # v3.2.0 ID 漂移合并: 在新建 _box_objects 条目前, 看老条目里
                    # 有没有 gone-confirm 中且同位置 IoU >= 阈值的, 找到就复用老 did.
                    # 这样 ByteTrack 切了 box 的 track_id 也不会让一个真实物理箱
                    # 被切成两个独立 _box_objects 条目, 避免老条目被 settle 成幽灵 NG.
                    merged_to = None
                    if id_drift_merge_enabled:
                        new_bbox = obj['bbox']
                        best_old_did, best_iou = None, id_drift_merge_iou
                        for old_did, _bs in self._box_objects.items():
                            if _bs.get('gone_frames', 0) <= 0 \
                                    or _bs['gone_frames'] > id_drift_max_gone:
                                continue
                            try:
                                _iou = self._bbox_iou(_bs['bbox'], new_bbox)
                            except Exception:
                                continue
                            if _iou >= best_iou:
                                best_iou = _iou
                                best_old_did = old_did
                        if best_old_did:
                            print(f"[Container] ID drift merge: new did={did} → "
                                  f"复用 老 did={best_old_did} (IoU={best_iou:.2f}, "
                                  f"old.gone={self._box_objects[best_old_did]['gone_frames']}f)")
                            obj['display_id'] = best_old_did
                            self._tracking_display_map[tid] = best_old_did
                            self._box_objects[best_old_did]['bbox'] = new_bbox
                            self._box_objects[best_old_did]['last_seen'] = current_time
                            self._box_objects[best_old_did]['gone_frames'] = 0
                            merged_to = best_old_did
                            did = best_old_did

                    if merged_to is None:
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
                            # v3.3.0: sticky 'was_complete' — 一旦曾经齐过永久 True,
                            # 直到 _settle_box() 清空。供 scan_pair 模式判 OK/NG。
                            'was_complete': False,
                        }
                else:
                    self._box_objects[did]['bbox'] = obj['bbox']
                    self._box_objects[did]['last_seen'] = current_time
                active_box_dids.add(did)
                box_bboxes[did] = obj['bbox']
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
        
        # v3.3.0 scan_pair (码-码闭环结算) 模式判定: 由 mes_hooks 在扫码事件里
        # 维护 cycle 节奏, 这里关掉 gone-confirm 触发的自动 _settle_box. 仅维护
        # _was_complete sticky flag, 后续 settle_for_scan_pair() 时按此判 OK/NG.
        scan_pair_active = False
        try:
            from backend.services.mes_hooks import get_mes_hook
            _mes_mgr = get_mes_hook()
            if _mes_mgr and getattr(_mes_mgr, 'enabled', False):
                scan_pair_active = _mes_mgr.is_scan_pair_mode(getattr(self, 'channel_id', 0))
        except Exception:
            scan_pair_active = False

        # Recalculate completeness for all active boxes
        for box_did in active_box_dids:
            if box_did in self._box_objects:
                bs = self._box_objects[box_did]
                bs['is_complete'] = (not expected_no_container) or all(
                    bs['item_class_counts'].get(cls, 0) >= exp
                    for cls, exp in expected_no_container.items()
                )
                # v3.3.0: sticky 'was_complete' — 一旦曾经 is_complete=True 永久置 True,
                # 直到本 box 被 _settle_box() 清空。供 scan_pair 模式按"曾齐过"判 OK/NG。
                if bs['is_complete']:
                    bs['was_complete'] = True
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
                    if scan_pair_active:
                        # v3.3.0: 扫码闭环模式下, 物理消失不结算, 由下一码事件触发.
                        # 这里只刷 last_seen / 保留 was_complete, 并把 gone_frames 卡在阈值
                        # 防止反复打印日志. (重新出现还能 reset 到 0)
                        bs['gone_frames'] = gone_confirm_frames
                    else:
                        print(f"[Container] {box_did} confirmed gone ({bs['gone_frames']}/{gone_confirm_frames})")
                        _sd = self.project_config.get('pipeline_config', {}).get('settle_dedup', False) if self.project_config else False
                        if _sd and not self.current_cycle_id:
                            self.start_cycle()
                        self._settle_box(box_did, expected_items)
            else:
                if bs.get('gone_frames', 0) > 0:
                    print(f"[Container] {box_did} reappeared, reset ({bs['gone_frames']}/{gone_confirm_frames})")
                bs['gone_frames'] = 0

    def _settle_box(self, box_display_id: str, expected_items: dict, *,
                    via_scan_pair: bool = False,
                    scan_pair_force_ng: bool = False,
                    suppress_event: bool = False):
        """Settle a single box: record its items and completeness.

        v3.1.4: 加"幽灵箱跳过"前置过滤 — 当 box.item_class_counts 的总件数
        < pipeline_config.container_settle_min_items (默认 1) 时, 直接 pop 不入账,
        既不计 OK 也不计 NG。

        触发场景: 多箱模式 (container_box_mode='multi') 下, 工人遮挡瞬间真箱被
        ByteTrack 切了 track_id, 后端把同一物理箱当成新 box_did 启动新条目, 而
        老条目里 item_class_counts 永远是空 ({}), 之后等够 30 帧 gone-confirm
        被结算时, 因为 expected_no_container 整列缺失, is_ok=False, 直接入账 NG。
        客户机表象就是 NG TOP3 几个步骤 NG 率完全相同 = 53.x%, 50 秒批量结算 5 个 NG。

        阈值默认 1 = 至少装 1 件才视为真箱; 设 0 退化到 v3.1.3 行为 (空箱也入账)。

        v3.3.0 scan_pair (码-码闭环) 模式参数:
          - via_scan_pair=True: 由 settle_for_scan_pair() 调用; 此时 OK/NG 判定改用
            box_state['was_complete'] (sticky 历史齐过) 而非当前帧 is_complete.
            因为扫码闭环允许工人扫前从箱里拿件再扫下一码, 最后帧件数可能不齐,
            但只要"曾齐过"就算 OK. 老路径 (gone-confirm 触发) 不传此参数, 行为不变.
          - scan_pair_force_ng=True: 超时强制结算 (must with via_scan_pair=True),
            不论 was_complete 一律判 NG.
        """
        box_state = self._box_objects.pop(box_display_id, None)
        if box_state is None:
            return

        item_counts = box_state['item_class_counts']

        # v3.1.4 幽灵箱前置过滤
        # 注意 None/''/[] 都 fallback 到默认 1 (而不是 0), 否则用户清空配置反而关闭过滤
        try:
            pcfg = (self.project_config or {}).get('pipeline_config', {}) if self.project_config else {}
            _val = pcfg.get('container_settle_min_items', 1)
            if _val is None or _val == '':
                _val = 1
            min_items_threshold = int(_val)
        except (TypeError, ValueError):
            min_items_threshold = 1
        if min_items_threshold > 0:
            total_items = sum(int(v) for v in item_counts.values())
            if total_items < min_items_threshold:
                print(f"[Container] {box_display_id} 跳过结算 "
                      f"(装件 {total_items} < 阈值 {min_items_threshold}, 视为幽灵箱)")
                return

        container_label = self._container_label
        expected_no_container = {k: v for k, v in expected_items.items() if k != container_label}

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

        # v3.3.0 scan_pair: 判定基准从"当前 is_complete (current_is_ok)"切换到
        # "曾齐过 was_complete"; 超时 force_ng 时直接 NG. 老路径行为不变.
        current_is_ok = not missing and not extra
        if via_scan_pair and scan_pair_force_ng:
            is_ok = False
            if not missing and not extra:
                missing.append("scan_pair_timeout")
        elif via_scan_pair:
            is_ok = bool(box_state.get('was_complete', False)) or current_is_ok
        else:
            is_ok = current_is_ok
        
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

        # v3.4.2 scan_pair 聚合结算: 调用方 (settle_for_scan_pair 容器分支) 用
        # suppress_event=True 让本 box 只写记录, 最后由调用方按"周期内任一齐过 → OK"
        # 聚合后只 trigger 一次, 计数器只 +1. 老路径 (gone-confirm) 默认行为不变.
        if suppress_event:
            return is_ok

        if is_ok:
            self._trigger_event(1, f'{box_display_id} OK: {item_counts}')
        else:
            reasons = []
            if missing:
                reasons.append(f'missing: {missing}')
            if extra:
                reasons.append(f'extra: {extra}')
            self._trigger_event(2, f'{box_display_id} NG: {", ".join(reasons) if reasons else "no items"}')
        return is_ok

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

    # ============== v3.4.0 scan_mode='D' 容器跨线/区域触发扫码 ==============

    def _scan_d_update(self):
        """v3.4.0 D 模式状态机. 仅在容器模式 + scan_mode='D' 时启用.

        状态机 (per box display_id):
            idle      → 跨线方向匹配 / 进区域 → armed (发 LON, set _scan_d_armed_box)
            armed     → mes_hooks 收到扫码 → scanned (发 LOFF, 清 armed_box)
                      → 异常 (box 离开未扫到码) → 强制 LOFF + reset
            scanned   → box gone-confirm 完成 → reset (允许下一个 box)

        约束: 同一时刻只允许一个 armed_box (产线节奏保证串行); 若并发跨线只
        log warning 不重复发 LON.
        """
        if not getattr(self, '_container_mode', False):
            return
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            if not svc.is_scan_d_for_channel(self.channel_id):
                return
            cfg = svc.get_scan_d_config_for_channel(self.channel_id)
        except Exception:
            return
        if not cfg:
            return

        geometry = cfg.get('geometry', 'line')
        gone_threshold = int(cfg.get('gone_confirm_frames', 30) or 30)

        # v3.4.2 修坐标系 bug: bbox 在 _tracking_objects 里是 normalized [0,1]
        # (source_detect_runners_mixin: det['x']=x1/w 等), 几何配置也按 [0,1] 存,
        # 所以这里直接在 normalized 空间做计算, 不再乘 frame_w/frame_h.
        # 之前乘像素分辨率 + cx/cy 实际是 normalized → 全部塌缩到 (0,0)/(1,1)
        # 永远不跨线, D 模式无法触发.
        # 叉积阈值: normalized 空间下数值范围 [-1,1], 用 1e-6 表示"几乎在线上";
        # 0.5 在 [0,1]² 几乎不可能达到 → 会把所有点判为线上, 是原 bug 之一.

        line_check = None
        zone_polygon = None
        side_a_to_b = True

        if geometry == 'line':
            line_cfg = cfg.get('line') or {}
            need = ('x1', 'y1', 'x2', 'y2')
            if not all(k in line_cfg and line_cfg[k] is not None for k in need):
                return  # 几何未配置, 静默跳过
            x1 = float(line_cfg['x1'])
            y1 = float(line_cfg['y1'])
            x2 = float(line_cfg['x2'])
            y2 = float(line_cfg['y2'])
            side_a_to_b = bool(line_cfg.get('side_a_to_b', True))

            def _side_of(cx, cy):
                # 叉积符号: > 0 = A 侧 (line LHS), < 0 = B 侧 (line RHS)
                # 接近 0 视为线上 (未知方向). normalized 空间 |cross| 上界 ≈ 1,
                # 1e-6 足以避开浮点抖动且不会把大部分点误判为线上.
                cross = (x2 - x1) * (cy - y1) - (y2 - y1) * (cx - x1)
                if cross > 1e-6:
                    return 1
                if cross < -1e-6:
                    return -1
                return 0
            line_check = _side_of
        elif geometry == 'zone':
            zone_norm = cfg.get('zone') or []
            if len(zone_norm) < 3:
                return
            zone_polygon = [
                [float(p[0]), float(p[1])]
                for p in zone_norm
                if isinstance(p, (list, tuple)) and len(p) >= 2
            ]
            if len(zone_polygon) < 3:
                return
        else:
            return

        seen_box_dids = set()
        for box_did, bs in (self._box_objects or {}).items():
            seen_box_dids.add(box_did)
            bbox = bs.get('bbox') or {}
            cx = bbox.get('x', 0) + bbox.get('w', 0) / 2.0
            cy = bbox.get('y', 0) + bbox.get('h', 0) / 2.0

            st = self._scan_d_box_states.get(box_did)
            first_seen = st is None
            if first_seen:
                st = {"side": 0, "in_zone": None, "armed": False,
                      "scanned": False, "gone_frames": 0}
                self._scan_d_box_states[box_did] = st
                # v3.4.1 诊断: 新 box 首次进入 D 状态机, 打印初始几何关系
                if geometry == 'line':
                    init_side = line_check(cx, cy)
                    side_label = 'A(蓝)' if init_side == 1 else (
                        'B(橙)' if init_side == -1 else '线上/未知')
                    need_dir = 'A→B' if side_a_to_b else 'B→A'
                    print(f"[ScanD] ch{getattr(self,'channel_id','?')} {box_did} 首次"
                          f"出现 (cx={cx:.3f},cy={cy:.3f}) side={side_label}, "
                          f"配置方向={need_dir}, 等待跨线...", flush=True)
                else:
                    in_zone = self._point_in_polygon(cx, cy, zone_polygon)
                    print(f"[ScanD] ch{getattr(self,'channel_id','?')} {box_did} 首次"
                          f"出现 (cx={cx:.3f},cy={cy:.3f}) in_zone={in_zone}, "
                          f"等待进入区域...", flush=True)
            st['gone_frames'] = 0  # 还在画面里, 重置

            if st.get('armed') or st.get('scanned'):
                # 已在 D 流程里, 状态推进交给扫码事件 (LOFF) / gone (reset)
                if geometry == 'line':
                    st['side'] = line_check(cx, cy)
                else:
                    st['in_zone'] = self._point_in_polygon(cx, cy, zone_polygon)
                continue

            triggered = False
            if geometry == 'line':
                cur_side = line_check(cx, cy)
                prev_side = st.get('side', 0)
                if prev_side and cur_side and prev_side != cur_side:
                    if side_a_to_b and prev_side == 1 and cur_side == -1:
                        triggered = True
                    elif (not side_a_to_b) and prev_side == -1 and cur_side == 1:
                        triggered = True
                    else:
                        # v3.4.1 诊断: 跨线了, 但方向反 → 提示用户配置可能选反
                        prev_label = 'A(蓝)' if prev_side == 1 else 'B(橙)'
                        cur_label = 'A(蓝)' if cur_side == 1 else 'B(橙)'
                        need_dir = 'A→B' if side_a_to_b else 'B→A'
                        print(f"[ScanD] ch{getattr(self,'channel_id','?')} {box_did} "
                              f"跨线方向={prev_label}→{cur_label}, 但配置要求={need_dir}, "
                              f"未触发 (是不是把方向选反了?)", flush=True)
                st['side'] = cur_side
            else:
                cur_in = self._point_in_polygon(cx, cy, zone_polygon)
                prev_in = st.get('in_zone')
                if prev_in is False and cur_in:
                    triggered = True
                st['in_zone'] = cur_in

            if not triggered:
                continue

            if self._scan_d_armed_box is not None:
                # 健壮性: 同时刻只允许一个 armed; 后到的 box 跨线只 log
                print(f"[ScanD] ch{getattr(self,'channel_id','?')} {box_did} 跨"
                      f"{geometry}, 但 armed_box={self._scan_d_armed_box} 仍未扫到码, "
                      f"忽略本次 (产线节奏异常?)")
                continue

            st['armed'] = True
            self._scan_d_armed_box = box_did
            try:
                n = svc.send_lon_for_channel(
                    self.channel_id, reason=f"box={box_did} {geometry}"
                )
                print(f"[ScanD] ch{getattr(self,'channel_id','?')} {box_did} 跨"
                      f"{geometry} → LON 已发 ({n} 个 text_lon 扫码器)")
            except Exception as e:
                print(f"[ScanD] LON 发送异常 ch{getattr(self,'channel_id','?')}: {e}")

        # 不在画面的 box → gone_frames 累加, 超阈值 reset 状态
        for box_did in list(self._scan_d_box_states.keys()):
            if box_did in seen_box_dids:
                continue
            st = self._scan_d_box_states[box_did]
            st['gone_frames'] = st.get('gone_frames', 0) + 1
            if st['gone_frames'] < gone_threshold:
                continue
            was_armed = st.get('armed', False)
            was_scanned = st.get('scanned', False)
            del self._scan_d_box_states[box_did]
            if self._scan_d_armed_box == box_did:
                self._scan_d_armed_box = None
                if was_armed and not was_scanned:
                    # 异常: armed 但 box 离开未扫到码, 主动 LOFF 防止 LON 残留
                    try:
                        svc.send_loff_for_channel(
                            self.channel_id,
                            reason=f"abandon armed box={box_did}",
                        )
                        print(f"[ScanD] ch{getattr(self,'channel_id','?')} "
                              f"{box_did} armed 但离开未扫到码, 已强制 LOFF")
                    except Exception:
                        pass
            print(f"[ScanD] ch{getattr(self,'channel_id','?')} "
                  f"{box_did} gone-confirm 完成 (>={gone_threshold}f), reset")
            # v3.4.2: 任何 box gone-confirm 都尝试 resume_after_cycle, 用于解开
            # _wait_cycle_resume=True 的 LOFF 死锁. resume_after_cycle 内部检查
            # _wait_cycle_resume, 不在等的连接会跳过, 故安全幂等.
            # 用户场景: 扫码可能先于 box 跨线 (此时 _scan_d_armed_box=None,
            # scan_d_on_scan_received return False, 但 _emit 已设
            # _wait_cycle_resume=True). was_scanned=False, 但 box 离开时也得
            # 释放扫码器, 否则下个 box 来时灯还是灭着.
            try:
                resumed = svc.resume_after_cycle(self.channel_id)
                if resumed:
                    print(f"[ScanD] ch{getattr(self,'channel_id','?')} "
                          f"{box_did} 离开 → 已 resume LON ({len(resumed)} 个扫码器), "
                          f"等下一箱 (was_scanned={was_scanned})")
            except Exception as e:
                print(f"[ScanD] resume_after_cycle 异常 "
                      f"ch{getattr(self,'channel_id','?')}: {e}")

    def scan_d_on_scan_received(self) -> bool:
        """v3.4.0 由 mes_hooks._handle_scan 在 D 模式扫到码后调用. 返回是否真正
        发了 LOFF (即当前是否有 armed box 等待此码).

        把 _scan_d_armed_box 切到 scanned 状态, 主动发 LOFF 关灯, 等 box 真正
        离开 (gone-confirm) 才允许下一个 box 触发 LON.
        """
        armed_did = getattr(self, '_scan_d_armed_box', None)
        if not armed_did:
            return False
        st = self._scan_d_box_states.get(armed_did)
        if not st:
            self._scan_d_armed_box = None
            return False
        st['armed'] = False
        st['scanned'] = True
        self._scan_d_armed_box = None
        try:
            from backend.services.scanner import get_scanner_service
            svc = get_scanner_service()
            n = svc.send_loff_for_channel(
                getattr(self, 'channel_id', 0),
                reason=f"scan received for box={armed_did}",
            )
            print(f"[ScanD] ch{getattr(self,'channel_id','?')} {armed_did} "
                  f"扫到码 → LOFF 已发 ({n} 个扫码器), 等 box 离开 reset")
        except Exception as e:
            print(f"[ScanD] LOFF 发送异常: {e}")
        return True
