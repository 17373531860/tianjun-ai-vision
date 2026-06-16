"""v3.19.x 自定义模式混合子状态机 (custom_mixed_with = 'per_item' | 'tracking').

架构原则 (重构后): **混合 = 复用独立模式的真引擎, 不发明第三套语义**。
  - 混合跟踪: 直接驱动宿主 TrackingMixin 的真机械 — max_recognized 后处理 /
    Phase1 位置锁 / Phase2 ID 匹配+re-ID / 抗闪烁 (ID Lock·Swap·外观) /
    失帧过期 / 动作计数 FSM / 堆叠 FSM / 清单重建, 全部调用独立跟踪模式的
    同一份代码。唯一被替换的是"周期主权": 砍掉四种周期结束策略
    (_tracking_check_settlement) 与自动开周期 (host._tracking_external_cycle
    守门), 周期始末完全听步骤侧 (基础模式) 的。
  - 混合逐件: 直接复用 PerItemMixin 的真个体状态机 (_PerItemStep) —
    个体锁定/跨帧位置匹配/目标⟶动作覆盖配对/持续帧确认/补锁定吸收/
    虚拟漏件判定全套原班逻辑。砍掉的同样只是周期主权: 稳定窗口开周期/
    收尾标签/完成即结算/双超时这些"何时开始何时结算"的判定全部听步骤侧。

与主状态机的三条契约 (违反任何一条都会污染现有模式):
  1. 物品标签由本组件独占消费 — 在 _update_step_stats 中被剥离,
     永远不进入步骤侧状态机 (cross_cycle / last_first / 同时组 / _process_single_step)。
     注: 若动作标签同时是步骤行 (既推动序列又覆盖个体), 该标签不剥离, 两边共享。
  2. 周期生命周期完全跟随步骤侧 — current_cycle_uuid 变化即重置统计,
     本组件永不开/关周期, 永不直接 _trigger_event。
  3. 结算永远由步骤侧触发 — 各结算点经 compose_settle_event 合成:
     步骤侧 OK + 物品侧 OK 才是 OK, 任一 NG 即 NG (原因合并)。

配置位 (原生词汇, 与独立模式同名同义):
  pipeline_config.custom_mixed_with : 'per_item' | 'tracking' | 缺省(现状, 零差异)
  steps_config[i].detect_role       : 'step'(默认) | 'item' — 物品行不参与序列/检测步骤

  混合跟踪的物品行直接使用独立跟踪模式的步骤字段 (真 loader 直接读):
    count_mode ('track'|'event') / event_required_count / event_gone_frames /
    event_min_visible_frames / stack_enabled / stack_reappear_seconds /
    stack_required_count / max_recognized / tracking_max_lost_seconds /
    tracking_position_lock / roi
  外加行级期望数量:
    steps_config[i].expected_count  : 跟踪计数行的周期期望个体数
    (兼容旧存储 steps_config[i].mix_item.expected_count)
  抗闪烁开关沿用 pipeline 原生字段: tracking_swap_detection /
    tracking_appearance_match / tracking_id_lock / tracking_id_lock_frames

  混合逐件的物品行直接使用独立逐件模式的步骤字段 (steps_config[i].per_item):
    item_label (str | list, OR 合并) / action_label / item_tracking_iou /
    coverage_iou / coverage_use_center / sustain_frames / expected_count
  项目级仅 pipeline_config.per_item.item_timeout_seconds 生效 (auto 模式
  个体超时清理); 稳定窗口/收尾标签/双超时/手动结算等周期字段在混合下无效。
"""
from __future__ import annotations

import time

from backend.api.source_roi import is_normalized_bbox_center_in_polygon

MIX_TYPES = ('per_item', 'tracking')


# ============================================================
# 混合跟踪 · 托盘容器累加器 (周期主权外移 — 不结算只记账)
# ============================================================


class _ContainerAccumulator:
    """混合跟踪专用容器累加器: 物品按"当前主托盘"分组, 托盘进箱只记账。

    与独立容器模式 (_update_container_grouping) 的本质区别 — 周期主权归步骤侧:
      - 不调 _settle_box / _trigger_event / end_cycle (整箱结算听封箱步骤)
      - 托盘消失 (gone-confirm) 只 append 到本周期"已装托盘清单", 绝不开/关周期
      - 主托盘 = 未结算托盘里 first_seen 最早且仍在场的那个 (先进先出, 用户敲定)

    托盘身份独立轻量跟踪 (IoU 跨帧关联 + first_seen), 与宿主物品跟踪机械隔离,
    永不污染物品判定 / 步骤侧状态机。坐标全程归一化 [0,1]。
    """

    def __init__(self, container_label: str, item_expected: dict,
                 box_count: int, gone_frames: int, iou_match: float = 0.3,
                 count_mode: str = 'trays', item_target: int = 0):
        self.container_label = container_label
        self.item_expected = {k: int(v) for k, v in (item_expected or {}).items()}
        self.box_count = int(box_count or 0)
        self.gone_frames = max(1, int(gone_frames or 30))
        self.iou_match = float(iou_match)
        # 计数模式: 'trays' = 计托盘数 + 每盘门槛; 'items_total' = 累加进箱滑块总数, 整箱判目标
        self.count_mode = count_mode if count_mode in ('trays', 'items_total') else 'trays'
        self.item_target = int(item_target or 0)   # items_total 模式整箱滑块目标 (如 96)
        self.reset()

    def reset(self):
        self._trays = {}        # tid -> {bbox, first_seen, last_seen, gone, peak:{label:cnt}}
        self._seq = 0
        self._primary = None    # 当前主托盘 tid
        self._done = []         # [{label:peak}, ...] 本周期已装托盘
        self._cur_counts = {}   # 当前主托盘实时物品数 {label:cnt}

    def update(self, tray_dets: list, item_objs: list, current_time: float):
        from backend.api.source_per_item_mixin import _bbox_iou

        # 1) 托盘检测框关联到已有托盘 (IoU 最高), 否则新建身份
        matched = set()
        for det in tray_dets:
            box = (det['x'], det['y'], det['w'], det['h'])
            best_tid, best_iou = None, self.iou_match
            for tid, t in self._trays.items():
                if tid in matched:
                    continue
                tb = t['bbox']
                iou = _bbox_iou(box, (tb['x'], tb['y'], tb['w'], tb['h']))
                if iou >= best_iou:
                    best_iou, best_tid = iou, tid
            bbox = {'x': det['x'], 'y': det['y'], 'w': det['w'], 'h': det['h']}
            if best_tid is not None:
                t = self._trays[best_tid]
                t['bbox'] = bbox
                t['last_seen'] = current_time
                t['gone'] = 0
                matched.add(best_tid)
            else:
                self._seq += 1
                self._trays[self._seq] = {
                    'bbox': bbox, 'first_seen': current_time,
                    'last_seen': current_time, 'gone': 0, 'peak': {},
                }
                matched.add(self._seq)

        # 2) 本帧未出现的托盘累加消失帧
        for tid, t in self._trays.items():
            if tid not in matched:
                t['gone'] += 1

        # 3) 主托盘选取: 当前主托盘只要还在累积器里 (没被 gone-confirm 移除) 就一直保持,
        #    抗瞬时漏检 / 托盘ID抖动 —— 漏检一两帧不切主、不清峰值, 等真正进箱 (step5
        #    移除) 才按 FIFO 重选下一盘。只有从未选出 / 主托盘已被移除时才重新挑。
        #    重选时优先在场 (gone==0) 的最早托盘; 若全在短暂遮挡也允许挑 gone 最小者顶上。
        if self._primary is None or self._primary not in self._trays:
            in_place = [(t['first_seen'], tid)
                        for tid, t in self._trays.items() if t['gone'] == 0]
            if in_place:
                self._primary = min(in_place)[1]
            elif self._trays:
                self._primary = min(self._trays.items(),
                                    key=lambda kv: (kv[1]['gone'], kv[1]['first_seen']))[0]
            else:
                self._primary = None

        # 4) 主托盘内物品计数 (中心包含), 刷新 peak (峰值保持, 抗瞬时漏检)
        cur = {}
        if self._primary is not None:
            pb = self._trays[self._primary]['bbox']
            px1, py1 = pb['x'], pb['y']
            px2, py2 = px1 + pb['w'], py1 + pb['h']
            for obj in item_objs:
                lbl = obj.get('class_name', '')
                if lbl not in self.item_expected:
                    continue
                b = obj.get('bbox') or {}
                cx = b.get('x', 0) + b.get('w', 0) / 2.0
                cy = b.get('y', 0) + b.get('h', 0) / 2.0
                if px1 <= cx <= px2 and py1 <= cy <= py2:
                    cur[lbl] = cur.get(lbl, 0) + 1
            peak = self._trays[self._primary]['peak']
            for lbl, c in cur.items():
                if c > peak.get(lbl, 0):
                    peak[lbl] = c
        self._cur_counts = cur

        # 5) 托盘 gone-confirm → 进箱记账 (只记曾累计过 peak 的真托盘, 跳过幽灵)
        for tid in list(self._trays.keys()):
            t = self._trays[tid]
            if t['gone'] >= self.gone_frames:
                if t['peak']:
                    self._done.append(dict(t['peak']))
                    print(f"[MixContainer] 托盘进箱: {dict(t['peak'])}, "
                          f"已装 {len(self._done)}/{self.box_count or '?'}")
                del self._trays[tid]
                if self._primary == tid:
                    self._primary = None

        # 5b) 主托盘刚进箱被清空 → 本帧立即把下一盘 (FIFO 最早在场) 顶上,
        #     避免进箱当帧主托盘空窗一帧导致峰值闪 0。
        if self._primary is None:
            in_place = [(t['first_seen'], tid)
                        for tid, t in self._trays.items() if t['gone'] == 0]
            if in_place:
                self._primary = min(in_place)[1]

    def _trays_for_verdict(self):
        """封箱裁决用的托盘全集: 已装清单 + 当前主托盘 (最后一盘可能还没 gone-confirm)。"""
        trays = list(self._done)
        if self._primary is not None:
            peak = self._trays.get(self._primary, {}).get('peak')
            if peak:
                trays.append(dict(peak))
        return trays

    def verdict(self, display_map: dict):
        dm = display_map or {}
        reasons = []
        # 总数模式: 不卡盘数/每盘, 只判进箱滑块总数是否正好等于整箱目标。
        # 关键: 进箱总数 = 已 gone-confirm 进箱的各盘 (self._done)。
        # 当前在位主托盘"是否计入本箱"取决于本箱有没有装够:
        #   - 还没装够 (done_total < target): 末盘可能刚进箱还没确认, 把在位这盘补进来凑数;
        #   - 已经装够 (done_total >= target): 在位的是"下一箱"的第一盘, 绝不计入本箱,
        #     否则会把下一盘也算进来误判超出 (治现场"4盘96+下一盘已上桌→120 NG")。
        if self.count_mode == 'items_total':
            cur_peak = {}
            if self._primary is not None:
                cur_peak = self._trays.get(self._primary, {}).get('peak', {}) or {}
            for lbl in self.item_expected.keys():
                target = self.item_target
                done_total = sum(t.get(lbl, 0) for t in self._done)
                total = done_total
                if target > 0 and done_total < target:
                    total += cur_peak.get(lbl, 0)
                if target > 0 and total != target:
                    rel = '不足' if total < target else '超出'
                    reasons.append(f'箱内{dm.get(lbl, lbl)}{rel} {total}/{target}')
            return (not reasons), reasons
        # 盘计数模式 (现状): 盘数够 + 每盘达每盘门槛
        trays = self._trays_for_verdict()
        if self.box_count > 0 and len(trays) < self.box_count:
            reasons.append(f'托盘数不足 {len(trays)}/{self.box_count} 盘')
        for idx, tray in enumerate(trays, 1):
            for lbl, exp in self.item_expected.items():
                got = tray.get(lbl, 0)
                if got < exp:
                    reasons.append(f'第{idx}盘 {dm.get(lbl, lbl)} 不足 {got}/{exp}')
        return (not reasons), reasons

    def set_item_target(self, target: int):
        """外部 (包装结算协调器) 反向设"当前箱滑块目标" — 普通箱=每箱数 / 尾箱=余数。

        让 verdict 始终用当前箱的真实目标判合格 (尾箱不再被按整箱数误判 NG)。
        仅 items_total 模式有意义; trays 模式调了也无副作用。
        """
        self.item_target = max(0, int(target or 0))

    def settled_item_total(self) -> int:
        """本周期已进箱滑块总数 (跨标签求和), 口径与 verdict 完全一致。

        供包装结算协调器在周期结算时读取 (= 这一箱实际进了多少滑块)。
        与 verdict 同源: 已 gone-confirm 进箱各盘峰值之和; 若本箱还没装够,
        把在位主托盘 (可能末盘还没确认) 也补进来凑数。
        """
        total = 0
        cur_peak = {}
        if self._primary is not None:
            cur_peak = self._trays.get(self._primary, {}).get('peak', {}) or {}
        for lbl in self.item_expected.keys():
            done_total = sum(t.get(lbl, 0) for t in self._done)
            sub = done_total
            if self.item_target <= 0 or done_total < self.item_target:
                sub += cur_peak.get(lbl, 0)
            total += sub
        return total

    def to_state(self, display_map: dict):
        dm = display_map or {}
        cur = self._cur_counts or {}
        # 实时卡 = 当帧真实检测数 (检到几个显示几个, 没检到就掉, 不 hold);
        # 当前主托盘的峰值仅作参考下发, 真正"记峰值"发生在进箱那一刻 (见 update step5)。
        peak = {}
        if self._primary is not None:
            peak = self._trays.get(self._primary, {}).get('peak', {}) or {}
        cur_items = [{
            'label': lbl,
            'display_name': dm.get(lbl, lbl),
            'current_count': cur.get(lbl, 0),     # 当帧实时数 (展示主数字)
            'peak_count': peak.get(lbl, 0),       # 当前托盘在位峰值 (= 进箱将记的值, 参考)
            'expected_per_tray': exp,
        } for lbl, exp in self.item_expected.items()]
        state = {
            'enabled': True,
            'count_mode': self.count_mode,
            'container_label': self.container_label,
            'container_display': dm.get(self.container_label, self.container_label),
            'box_count': self.box_count,
            'trays_done': len(self._done),
            'current_tray_items': cur_items,
            'done_detail': self._done,
        }
        if self.count_mode == 'items_total':
            # 已进箱滑块总数 (按 label 累加各盘峰值) + 整箱目标
            totals = {}
            for t in self._done:
                for lbl, c in t.items():
                    totals[lbl] = totals.get(lbl, 0) + c
            state['item_total_done'] = totals
            state['item_target'] = self.item_target
        return state


# ============================================================
# 混合跟踪: 真跟踪引擎 (周期主权外移)
# ============================================================


class _TrackingMixEngine:
    """驱动宿主真跟踪机械的薄编排层。

    宿主 (VideoSourceManager) 的 MRO 已含 TrackingMixin / ChecklistMixin,
    所有状态字典 (_tracking_objects / _event_* / _stack_* ...) 在自定义模式下
    本就闲置 — 本引擎按帧调用真方法, 把作用域裁剪到物品行标签。
    不做的事 (周期主权归步骤侧):
      - 不调 _tracking_check_settlement (四种周期结束策略)
      - 不调 _update_container_grouping / _scan_d_update (容器=周期策略, 混合下无意义)
      - 自动开周期由 host._tracking_external_cycle 在真机械内守门跳过
    """

    def __init__(self, item_cfgs: list, container_cfg: dict = None):
        self.items = {}
        for cfg in item_cfgs:
            self.items[cfg['label']] = cfg
        self.item_labels = frozenset(self.items.keys())
        # 托盘容器累加器 (可选): 配了容器标签才启用, 否则 None = 走原满盘门/计数路径 (零差异)
        self._container = None
        if container_cfg and container_cfg.get('label'):
            self._container = _ContainerAccumulator(
                container_label=container_cfg['label'],
                item_expected=container_cfg.get('item_expected', {}),
                box_count=container_cfg.get('box_count', 0),
                gone_frames=container_cfg.get('gone_frames', 30),
                iou_match=container_cfg.get('iou_match', 0.3),
                count_mode=container_cfg.get('count_mode', 'trays'),
                item_target=container_cfg.get('item_target', 0),
            )
        # 静态期望清单 (verdict 用, 不依赖喂帧): 与真 loader 的注入规则一致 —
        # event 行 → event_required_count; 堆叠行 → stack_required_count;
        # 普通跟踪计数行 → expected_count (0 = 只展示不判定)
        self.expected_items = {}
        for label, cfg in self.items.items():
            mode = cfg.get('count_mode') or 'track'
            if mode == 'event':
                self.expected_items[label] = max(1, int(cfg.get('event_required_count') or 1))
            elif cfg.get('stack_enabled'):
                self.expected_items[label] = max(2, int(cfg.get('stack_required_count') or 2))
            else:
                exp = int(cfg.get('expected_count') or 0)
                if exp > 0:
                    self.expected_items[label] = exp

    # ---- 周期重置: 只清物品侧运行时状态, 步骤侧/容器/周期字段一概不碰 ----
    _HOST_DICT_ATTRS = (
        '_tracking_objects', '_tracking_class_counters', '_tracking_display_map',
        '_tracking_lost_frames', '_tracking_letter_map', '_tracking_item_checklist',
        '_tracking_recently_lost', '_tracking_transferred_ids',
        '_tracking_prev_positions', '_tracking_appearance',
        '_tracking_stable_frames', '_tracking_locked_ids',
        '_tracking_registered_positions',
        '_event_counters', '_event_state', '_event_visible_frames',
        '_event_gone_frames_count', '_event_first_seen', '_event_last_seen',
        '_stack_state', '_stack_counters', '_stack_disappeared_at',
        '_stack_visible_frames',
        '_stack_sat_frames', '_stack_latched', '_stack_phase_peak',
        '_stack_partials',
    )

    def reset_host_state(self, host):
        for attr in self._HOST_DICT_ATTRS:
            d = getattr(host, attr, None)
            if isinstance(d, dict):
                d.clear()
        host._tracking_letter_idx = 0
        host._tracking_order_seq = 0
        if self._container is not None:
            self._container.reset()

    def feed(self, host, detections: list, current_time: float, original_frame=None):
        # 幂等声明: 周期主权在外部 — 真机械内的自动开周期一律跳过
        host._tracking_external_cycle = True

        # 物品标签过滤 + 步骤置信度阈值守门 (与步骤侧同一套语义);
        # 逐行 ROI 守门交给真机械内部的 _det_passes_roi_for_label
        conf_map = getattr(host, 'step_conf_thresholds', None) or {}
        dets = []
        tray_dets = []  # 容器累加器用: 托盘检测框 (与物品流隔离, 不进跟踪机械)
        container_label = self._container.container_label if self._container else None
        for det in detections or []:
            label = det.get('label', '')
            if container_label and label == container_label:
                threshold = conf_map.get(label)
                if threshold is not None and det.get('confidence', 0) < threshold:
                    continue
                tray_dets.append({
                    'x': float(det.get('x', 0)), 'y': float(det.get('y', 0)),
                    'w': float(det.get('w', 0)), 'h': float(det.get('h', 0)),
                })
                continue
            if label not in self.item_labels:
                continue
            threshold = conf_map.get(label)
            if threshold is not None and det.get('confidence', 0) < threshold:
                continue
            dets.append(det)

        # 1) 真 loader 解析 steps_config (会把 event/stack 期望注入 expected),
        #    随后作用域裁剪到物品行 — 步骤行的任何残留配置不进入跟踪机械
        expected = dict(self.expected_items)
        cfg = host._tracking_load_step_config(expected)
        for key in ('per_class_lost_sec', 'per_class_position_lock',
                    'event_steps', 'stack_steps', 'max_recognized_per_label'):
            cfg[key] = {k: v for k, v in cfg[key].items() if k in self.item_labels}
        expected = {k: v for k, v in expected.items() if k in self.item_labels}

        pcfg = (host.project_config or {}).get('pipeline_config', {}) or {}
        swap_detection = pcfg.get('tracking_swap_detection', False)
        appearance_match = pcfg.get('tracking_appearance_match', False)
        id_lock = pcfg.get('tracking_id_lock', False)
        id_lock_frames = pcfg.get('tracking_id_lock_frames', 15)

        # 2) 最大识别数后处理 (改本帧 detections 的 track_id, 与独立模式一致)
        host._tracking_apply_max_recognized(dets, cfg['max_recognized_per_label'])

        # 3) 帧检测分类 (无 trigger 标签 — 周期策略已被砍掉)
        frame_detections, _trigger_visible, event_labels_seen = \
            host._tracking_collect_frame_dets(dets, '', 'all_gone', cfg['event_steps'])

        seen_track_ids = set()
        pos_lock_assigned_dids = set()

        # 4) Phase 1: 位置锁匹配
        pos_lock_handled_tids = host._tracking_phase1_position_lock(
            frame_detections, cfg['per_class_position_lock'], current_time,
            'all_gone', seen_track_ids, pos_lock_assigned_dids)

        # 5) Phase 2: track_id 匹配 (id_lock / recently_lost re-ID / 新分配)
        host._tracking_phase2_id_match(
            frame_detections, pos_lock_handled_tids, cfg['per_class_position_lock'],
            id_lock, id_lock_frames, appearance_match, cfg['max_lost_sec'],
            current_time, 'all_gone', seen_track_ids, pos_lock_assigned_dids,
            expected, original_frame)

        # 6) 抗闪烁: ID Lock + Swap Detection + Appearance Match
        host._tracking_apply_anti_flicker(
            seen_track_ids, cfg['per_class_position_lock'],
            id_lock, id_lock_frames, swap_detection, appearance_match, original_frame)

        # 7) 失帧累加 + 过期清理
        host._tracking_increment_lost_expire(
            seen_track_ids, cfg['per_class_lost_sec'], cfg['max_lost_sec'], current_time)

        # 8) 动作计数 FSM
        host._tracking_run_event_fsm(cfg['event_steps'], event_labels_seen, current_time)

        # 9) 堆叠 FSM
        host._tracking_run_stack_fsm(cfg['stack_steps'], dets, current_time)

        # 10) 物品截图 (限频 1Hz, 给前端清单卡片)
        if original_frame is not None:
            host._tracking_capture_screenshots(seen_track_ids, original_frame)

        # 11) 清单重建 (前端"物品清点"展示直接复用)
        host._rebuild_checklist(expected)

        # 12) 托盘容器累加: 物品按主托盘分组 + 托盘进箱记账 (不碰周期主权)
        #     用「当前帧原始检测框」计数 (= 同帧同时出现的滑块数), 取在位峰值;
        #     不用 _tracking_objects — 唯一ID跟踪在 24 个密集小目标上会塌缩成
        #     个位数 (ByteTrack 只保住几个稳定 ID), 与"同时最多那帧的数量"不是一回事。
        if self._container is not None:
            item_dets_for_container = [{
                'class_name': d.get('label', ''),
                'bbox': {
                    'x': float(d.get('x', 0)), 'y': float(d.get('y', 0)),
                    'w': float(d.get('w', 0)), 'h': float(d.get('h', 0)),
                },
            } for d in dets]
            self._container.update(tray_dets, item_dets_for_container, current_time)

    # ---- 合并计数: 与独立模式 _rebuild_checklist 同一公式 ----
    @staticmethod
    def _merged_counters(host):
        merged = {}
        track_counters = getattr(host, '_tracking_class_counters', {}) or {}
        stack_counters = getattr(host, '_stack_counters', {}) or {}
        event_counters = getattr(host, '_event_counters', {}) or {}
        for label in set(track_counters) | set(stack_counters) | set(event_counters):
            merged[label] = (max(track_counters.get(label, 0), stack_counters.get(label, 0))
                             + event_counters.get(label, 0))
        return merged

    def verdict(self, host):
        if host is None:
            return True, []
        display_map = getattr(host, 'step_display_names', {}) or {}
        # 容器模式: 裁决改为"每托盘是否数满 + 整箱托盘数是否够", 不卡全局累计总数
        if self._container is not None:
            return self._container.verdict(display_map)
        merged = self._merged_counters(host)
        reasons = []
        for label, exp in self.expected_items.items():
            cfg = self.items.get(label, {})
            actual = merged.get(label, 0)
            display = display_map.get(label, cfg.get('display', label))
            # 满盘门模式: 只验"每盘是否数满", 任一盘短即 NG, 不卡累计总数
            if cfg.get('stack_enabled') and cfg.get('stack_gate_only'):
                partials = self._stack_partials_with_live(host, label)
                if partials:
                    detail = ', '.join(f"{p['peak']}/{p['required']}" for p in partials)
                    reasons.append(f'[{display}] 有未数满的盘: {detail}')
                continue
            if actual < exp:
                msg = f'[{display}] 数量不足 {actual}/{exp}'
                # 堆叠批层模式: 附上"哪批没数够"的可解释明细
                partials = self._stack_partials_with_live(host, label)
                if partials:
                    detail = ', '.join(f"{p['peak']}/{p['required']}" for p in partials)
                    msg += f' (不完整批次: {detail})'
                reasons.append(msg)
            elif actual > exp:
                reasons.append(f'[{display}] 数量超出期望 ({actual} > {exp})')
        return (not reasons), reasons

    def set_container_item_target(self, target):
        """供包装结算反向设当前箱滑块目标 (无容器时 no-op)。"""
        if self._container is not None:
            self._container.set_item_target(target)

    def container_settled_item_total(self):
        """本周期已进箱滑块总数 (无容器时 None)。"""
        if self._container is not None:
            return self._container.settled_item_total()
        return None

    @staticmethod
    def _stack_partials_with_live(host, label):
        """已落账的不完整批次 + 当前在场/刚离场但还没闩锁的批次 (结算时最后一批
        不达标还没等到"下一批开始"落账, 这里折进去保证可解释性完整)。

        委托给宿主的 _stack_collect_partials — 与独立模式满盘门同一口径单点维护。"""
        if host is None:
            return []
        return host._stack_collect_partials(label)

    def to_state(self, host):
        merged = self._merged_counters(host) if host is not None else {}
        display_map = (getattr(host, 'step_display_names', {}) or {}) if host is not None else {}
        items = []
        for label, cfg in self.items.items():
            mode = cfg.get('count_mode') or 'track'
            role = 'event' if mode == 'event' else ('stack' if cfg.get('stack_enabled') else 'track')
            row = {
                'label': label,
                'display_name': display_map.get(label, cfg.get('display', label)),
                'role': role,
                'expected_count': self.expected_items.get(label, 0),
                'seen_count': merged.get(label, 0),
            }
            if role == 'stack' and host is not None:
                # 批层模式: 前端可显示 "N 批未达标 (峰值/要求)"
                row['partials'] = self._stack_partials_with_live(host, label)
            items.append(row)
        checklist = {}
        if host is not None:
            checklist = dict(getattr(host, '_tracking_item_checklist', {}) or {})
        state = {'mix_type': 'tracking', 'items': items, 'checklist': checklist}
        if self._container is not None:
            state['container'] = self._container.to_state(display_map)
        return state


# ============================================================
# 混合逐件: 真逐件引擎 (个体锁定 + 覆盖配对, 周期主权外移)
# ============================================================


class _PerItemMixEngine:
    """驱动真逐件个体状态机 (_PerItemStep) 的薄编排层。

    复用 (与独立逐件模式同一份代码):
      - _PerItemStep: 个体表/跨帧位置匹配 (IoU)/目标⟶动作覆盖配对/
        持续帧确认/覆盖单调性/虚拟漏件 (expected_count)/完成判定
      - PerItemMixin._per_item_absorb_new_items: 固定数量模式的补锁定吸收
      - PerItemMixin._collect_item_boxes: 多标签 OR 合并
    不做的事 (周期主权归步骤侧):
      - 不跑稳定窗口开周期 / 收尾标签 / 完成即结算 / 双超时 / 手动结算
      - 个体吸收语义: expected_count>0 行走"补锁定"路径 (整周期吸收新位置,
        到期望数封顶 — 抗误检幻影个体); auto 行走 dynamic 路径 (随见随建 +
        item_timeout 清理), 与独立模式的两种哲学一一对应。
    """

    def __init__(self, item_cfgs: list, item_timeout_seconds: float):
        # 延迟导入避免环 (per_item mixin 不依赖本模块)
        from backend.api.source_per_item_mixin import _PerItemStep
        self.steps = []
        for raw in item_cfgs:
            self.steps.append(_PerItemStep(raw))
        self.item_timeout_seconds = max(0.0, float(item_timeout_seconds or 0.0))
        # 引擎监听的标签全集 (行标签 + 个体标签 + 动作标签)
        watch = set()
        for s in self.steps:
            if s.step_label:
                watch.add(s.step_label)
            watch.update(s.item_label)
            if s.action_label:
                watch.add(s.action_label)
        self.watch_labels = frozenset(watch)
        self._frame_id = 0
        self.last_ng_detail = None

    def reset_host_state(self, host):
        for step in self.steps:
            step.reset_for_new_cycle()

    def feed(self, host, detections: list, current_time: float, original_frame=None):
        from backend.api.source_per_item_mixin import PerItemMixin
        self._frame_id += 1

        # 标签过滤 + 步骤置信度阈值 + 逐行 ROI (与步骤侧同一套守门语义)
        conf_map = getattr(host, 'step_conf_thresholds', None) or {}
        poly_map = getattr(host, 'step_roi_polygons', None) or {}
        boxes_by_label = {}
        for det in detections or []:
            label = det.get('label', '')
            if label not in self.watch_labels:
                continue
            threshold = conf_map.get(label)
            if threshold is not None and det.get('confidence', 0) < threshold:
                continue
            poly = poly_map.get(label)
            if poly and len(poly) >= 3 and not is_normalized_bbox_center_in_polygon(det, poly):
                continue
            bbox = (
                float(det.get('x', 0)), float(det.get('y', 0)),
                float(det.get('w', 0)), float(det.get('h', 0)),
            )
            if bbox[2] <= 0 or bbox[3] <= 0:
                continue
            boxes_by_label.setdefault(label, []).append(bbox)

        for step in self.steps:
            item_boxes = PerItemMixin._collect_item_boxes(boxes_by_label, step.item_label)
            fixed_count = step.expected_count > 0
            if item_boxes:
                # 固定数量: 只更新已有个体位置, 新位置走补锁定吸收 (封顶 expected)
                # auto: dynamic 随见随建
                step.update_item_positions(
                    item_boxes, self._frame_id, current_time,
                    lock_count_on_start=fixed_count)
                if fixed_count and len(step.items) < step.expected_count:
                    PerItemMixin._per_item_absorb_new_items(
                        step, item_boxes, self._frame_id, current_time)
            if not fixed_count:
                step.cleanup_stale_items(
                    current_time, self.item_timeout_seconds,
                    lock_count_on_start=False)
            action_boxes = boxes_by_label.get(step.action_label, [])
            step.apply_coverage(action_boxes, self._frame_id, current_time)
            if not step.completed and step.check_completion():
                step.completed = True
                print(f"[CustomMix] 逐件步骤 [{step.display_label}] 完成 "
                      f"({step.covered_count()}/{len(step.items)})")

    def verdict(self, host):
        reasons = []
        ng_details = []
        for step in self.steps:
            # 与独立模式结算同语义: 读粘性完成标志 (feed 中翻转, 永不回滚);
            # 兜底再查一次完成判定 (结算与最后一帧之间的竞态)
            done = step.completed or step.check_completion()
            if done:
                continue
            missing = [iid for iid, st in step.items.items() if not st.covered]
            total = len(step.items)
            if step.expected_count > 0 and total < step.expected_count:
                reasons.append(
                    f'[{step.display_label}] 未完成({step.covered_count()}/{total}, '
                    f'期望{step.expected_count}件)')
            else:
                reasons.append(
                    f'[{step.display_label}] 未完成({step.covered_count()}/{total})')
            ng_details.append({
                'step_label': step.step_label,
                'display_label': step.display_label,
                'covered_count': step.covered_count(),
                'total': total,
                'expected_count': step.expected_count,
                'missing_item_ids': missing,
            })
        if reasons:
            self.last_ng_detail = {
                'reason_summary': '; '.join(reasons),
                'settled_at': time.time(),
                'missing_total': sum(len(d['missing_item_ids']) for d in ng_details),
                'steps_failed': ng_details,
            }
        else:
            self.last_ng_detail = None
        return (not reasons), reasons

    def to_state(self, host):
        steps_state = [s.to_state_dict(strict_display=True) for s in self.steps]
        items = []
        for s, st in zip(self.steps, steps_state):
            items.append({
                'label': s.step_label,
                'display_name': s.display_label,
                'role': 'pair',
                'expected_count': s.expected_count,
                'covered_count': st['covered_count'],
                'total': st['total'],
                'completed': st['completed'],
            })
        return {
            'mix_type': 'per_item',
            'items': items,
            'steps': steps_state,
            'last_ng_detail': self.last_ng_detail,
        }


class CustomMixMachine:
    """混合子状态机外壳: 引擎分发 + 周期跟随重置 + 裁决快照。"""

    def __init__(self, mix_type: str, item_cfgs: list, *,
                 item_timeout_seconds: float = 3.0, step_labels=(),
                 extra_item_labels=(), container_cfg=None):
        self.mix_type = mix_type
        self._cycle_token = '__init__'
        self._host = None
        if mix_type == 'tracking':
            self._engine = _TrackingMixEngine(item_cfgs, container_cfg)
            # 跟踪混合: 物品行标签全部由本组件独占消费
            self.item_labels = self._engine.item_labels
        else:
            self._engine = _PerItemMixEngine(item_cfgs, item_timeout_seconds)
            # 逐件混合: 个体/动作标签被独占消费, 但与步骤行同名的标签除外
            # (动作标签可同时推动序列 — 两边共享, 不剥离)。
            # extra_item_labels: 配置不完整被跳过的物品行标签 — 仍要剥离,
            # 不允许半配置的物品流进步骤侧状态机。
            self.item_labels = frozenset(
                (self._engine.watch_labels | set(extra_item_labels or ()))
                - set(step_labels or ()))

    def reset(self):
        if self._host is not None:
            self._engine.reset_host_state(self._host)

    def feed(self, host, detections: list, current_time: float, original_frame=None):
        """每帧喂入 (在 _update_step_stats 剥离物品标签前调用)。"""
        self._host = host
        # 周期跟随: 步骤侧开了新周期 (uuid 变化) → 清零本周期物品统计
        token = getattr(host, 'current_cycle_uuid', None)
        if token != self._cycle_token:
            self.reset()
            self._cycle_token = token
        self._engine.feed(host, detections, current_time, original_frame)

    def verdict(self):
        """合成裁决: 所有物品都 OK 才 OK, NG 原因合并。"""
        return self._engine.verdict(self._host)

    def set_container_item_target(self, target):
        """包装结算反向设当前箱滑块目标 (透传跟踪引擎; 非容器/逐件混合 no-op)。"""
        fn = getattr(self._engine, 'set_container_item_target', None)
        if fn is not None:
            fn(target)

    def container_settled_item_total(self):
        """本周期已进箱滑块总数 (供包装结算累加; 非容器混合返回 None)。"""
        fn = getattr(self._engine, 'container_settled_item_total', None)
        return fn() if fn is not None else None

    def to_state(self):
        state = self._engine.to_state(self._host)
        # 步骤侧周期是否进行中 (前端面板"周期中/等待"显示用; 周期主权在步骤侧)
        state['cycle_active'] = bool(
            self._host is not None and getattr(self._host, 'current_cycle_id', None))
        return state


def build_custom_mix(config: dict):
    """从项目配置构建混合子状态机; 非 custom / 未混合 / 无物品行 → None (零差异)。"""
    if not config or config.get('logic_mode') != 'custom':
        return None
    pipeline = config.get('pipeline_config', {}) or {}
    mix_type = pipeline.get('custom_mixed_with')
    if mix_type not in MIX_TYPES:
        return None
    item_cfgs = []
    step_labels = set()
    skipped_item_labels = set()
    for step in config.get('steps_config', []) or []:
        if not step.get('enabled', True):
            continue
        label = step.get('label')
        if step.get('detect_role') != 'item':
            if label:
                step_labels.add(label)
            continue
        if not label:
            continue
        if mix_type == 'tracking':
            # 原生跟踪词汇: 行为字段 (count_mode/event_*/stack_*/max_recognized/...)
            # 由真 loader 直接从 steps_config 读, 这里只收 "身份 + 期望数量"
            expected = step.get('expected_count')
            if expected is None:
                expected = (step.get('mix_item') or {}).get('expected_count', 0)  # 旧存储兜底
            item_cfgs.append({
                'label': label,
                'display': step.get('displayLabel') or step.get('display_name') or label,
                'count_mode': step.get('count_mode', 'track'),
                'stack_enabled': bool(step.get('stack_enabled')),
                'stack_gate_only': bool(step.get('stack_gate_only', False)),
                'expected_count': expected,
                'event_required_count': step.get('event_required_count', 1),
                'stack_required_count': step.get('stack_required_count', 2),
            })
        else:
            # 原生逐件词汇: 物品行就是一条 "目标⟶动作" 配对, 字段与独立模式
            # steps_config[i].per_item 完全同名同义, 直接交给真 _PerItemStep 解析
            per = step.get('per_item') or {}
            if not per.get('item_label') or not per.get('action_label'):
                print(f"[CustomMix] 物品行 [{label}] 缺 per_item.item_label/action_label, 跳过")
                skipped_item_labels.add(label)
                continue
            item_cfgs.append(step)
    if not item_cfgs:
        print(f"[CustomMix] custom_mixed_with={mix_type} 但没有任何启用的物品行, 混合不生效")
        return None
    item_timeout = float(((pipeline.get('per_item') or {}).get('item_timeout_seconds', 3.0)) or 0.0)

    # 托盘容器累加器配置 (仅 tracking 混合 + 配了容器标签才启用; 否则 None = 零差异)
    container_cfg = None
    if mix_type == 'tracking':
        clabel = (pipeline.get('custom_mix_container_label') or '').strip()
        if clabel:
            item_expected = {}
            for ic in item_cfgs:
                lbl = ic.get('label')
                exp = int(ic.get('expected_count') or 0)
                if lbl and lbl != clabel and exp > 0:
                    item_expected[lbl] = exp
            count_mode = pipeline.get('custom_mix_container_count_mode', 'trays')
            container_cfg = {
                'label': clabel,
                'item_expected': item_expected,
                'box_count': int(pipeline.get('custom_mix_container_box_count', 0) or 0),
                'gone_frames': int(pipeline.get('custom_mix_container_gone_frames', 30) or 30),
                'iou_match': float(pipeline.get('custom_mix_container_iou_match', 0.3) or 0.3),
                'count_mode': count_mode,
                'item_target': int(pipeline.get('custom_mix_container_item_target', 0) or 0),
            }
            if count_mode == 'items_total':
                print(f"[CustomMix] 托盘容器累加器[总数模式]: 容器={clabel} "
                      f"整箱滑块目标={container_cfg['item_target']} 消失确认={container_cfg['gone_frames']}帧")
            else:
                print(f"[CustomMix] 托盘容器累加器[盘计数]: 容器={clabel} 每盘期望={item_expected} "
                      f"每箱={container_cfg['box_count']}盘 消失确认={container_cfg['gone_frames']}帧")

    machine = CustomMixMachine(mix_type, item_cfgs,
                               item_timeout_seconds=item_timeout,
                               step_labels=step_labels,
                               extra_item_labels=skipped_item_labels,
                               container_cfg=container_cfg)
    print(f"[CustomMix] 混合子状态机就绪: mix={mix_type} 物品={sorted(machine.item_labels)}")
    return machine


def compose_settle_event(host, event_id, reason):
    """步骤侧结算事件 × 物品侧裁决合成 (所有 custom 结算点统一经此函数)。

    规则 (用户敲定: 结算一定由步骤驱动, 两边都 OK 才 OK):
      - 未启用混合 → 原样透传 (零差异)
      - 步骤侧 OK(1) + 物品侧 NG → 降级为 NG(2), 原因合并
      - 步骤侧 NG(2) + 物品侧 NG → 仍 NG, 原因追加物品侧
      - 自定义事件 (id 非 1/2) → 不改判 (好坏语义无法推断), 仅透传
    每次合成即视为一次周期结算, 物品统计随之清零 (新周期 uuid 变化时也会兜底重置)。
    """
    mix = getattr(host, '_custom_mix', None)
    if mix is None:
        return event_id, reason
    try:
        ok, mix_reasons = mix.verdict()
        # 在 reset 前抓本周期进箱滑块总数, 缓存给包装结算协调器 (reset 后就归零).
        # 仅容器混合有值; 其它模式 None — on_cycle_settled 只在 sliders 口径读它.
        try:
            host._last_container_item_total = mix.container_settled_item_total()
            # 缓存本周期已检出步骤集 (尾箱塞工单 gate 探测用; end_cycle 后 current_cycle_steps 会清)
            host._last_cycle_steps = list(getattr(host, 'current_cycle_steps', []) or [])
        except Exception:
            host._last_container_item_total = None
        mix.reset()
    except Exception as e:
        print(f"[CustomMix] 裁决合成失败, 按步骤侧原判放行: {e}")
        return event_id, reason
    if ok or event_id not in (1, 2):
        return event_id, reason
    merged = '；'.join(mix_reasons)
    new_reason = f'{reason}；物品校验未通过: {merged}' if reason else f'物品校验未通过: {merged}'
    if event_id == 1:
        print(f"[CustomMix] 步骤侧 OK 但物品校验 NG → 降级为 NG: {merged}")
    return 2, new_reason
