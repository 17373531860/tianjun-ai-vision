"""per_item 「逐件覆盖」模式 mixin (v3.6.0+)

==================== 模式定位 ====================
项目 logic_mode 第 5 个值: 'per_item'.

业务模式: 画面里有 N 个独立个体(按位置 IoU 锁定), 每个个体要被一组工序
覆盖到, 最后一个"收尾标签"出现 → 周期结算.

典型场景:
    - 打螺丝: 个体=螺丝 x 12, 工序=[打螺丝, 划螺丝], 收尾=翻面
    - 焊点检验: 个体=焊盘, 工序=[焊烙铁碰], 收尾=下件
    - 涂胶 / 喷涂 / 多工序质检 同模式

==================== 与其他模式正交 ====================
不动 sequential / detection / tracking / custom 的代码路径. 在
_update_step_stats 入口检查 logic_mode == 'per_item' → 进入本 mixin
独立路径, 其余 mixin 完全绕开.

==================== 不变量 ====================
1. 覆盖状态单调: 个体的 covered 字段只能 false→true, 任何情况不可回滚
2. 持续帧数计数: 任一帧不重叠则归零; 累积达标那一刻翻转 covered, 之后不
   再变化
3. 周期开始: 连续 stability_window 帧画面里"周期触发标签"的数量和位置
   都稳定(数量恒定 + 跨帧 IoU > stability_iou_threshold) 才算稳定
4. 个体表锁定: 周期开始那一瞬间锁定所有 per_item 步骤的个体表
5. 误检不兜底: 模型识别出虚假个体 → 步骤永远完不成 → 走现有空闲超时机制
   报 NG (Q7 决策)

==================== 配置 schema ====================
项目级 pipeline_config:
    per_item: {
        stability_window_frames: 10,           # 稳定窗口帧数, 默认 10
        stability_iou_threshold: 0.7,          # 同位置 box 跨帧 IoU 阈值
        item_timeout_seconds: 3.0,             # 个体超时清理时间 (Q4)
        lock_count_on_start: true,             # 周期开始时锁定个体数 (Q5)
        finish_label: "翻面",                  # 收尾标签 (出现即结算)
        # ── v3.56+ 新增 (默认全关/空 = 老项目零差异) ──
        absorb_new_items_sec: 0,               # >0: 周期开始后 N 秒内 auto 步骤无上限吸收新个体
                                               #     (治"工人逐个放件, 稳定窗口先锁了前几件")
        item_count_counter_name: "",           # 非空: 周期判 OK 时把本周期件数累加进该计数器
        remediation_event_notify: false,       # true: 进待补态借事件完整响应面 (Toast/语音/灯)
    }

步骤级 steps_config[i] (per_item 步骤新增字段):
    per_item: {
        item_label: "螺丝",                    # 个体识别标签 (str 或 [str,...] OR 合并)
        action_label: "打螺丝",                # 工序覆盖标签 (str 或 [str,...] OR 合并, v3.56+)
        item_tracking_iou: 0.3,                # 个体跨帧 IoU 阈值
        coverage_iou: 0.3,                     # 工序与个体的覆盖 IoU 阈值
        sustain_frames: 5,                     # 持续 N 帧重叠才算覆盖
        completion: "all_covered",             # 完成判定 (本版仅实现 all_covered)
        min_item_count: "auto",                # 最低个体数 ("auto" 或固定数字)
        # ── v3.56+ 单件超时未覆盖警告 (默认 0=关) ──
        warn_uncovered_after_sec: 0,           # >0: 个体出现 N 秒仍未被覆盖 → 报一次警
        warn_event_id: 0,                      # 警告借哪个事件的响应面 (Toast/语音/灯, 不结周期)
    }

==================== 兼容性 ====================
- 数据库 schema 不动
- 老项目一行配置不动, 新模式不启用任何新代码路径
- step_counts / counters 仍正常累加 (per_item 步骤每完成一次 +1, 等价于
  现有 sequential 步骤完成行为)
- _trigger_event(1, '...') / _trigger_event(2, '...') 直接复用作 OK/NG
"""
from __future__ import annotations

import math
import time
from collections import deque
from statistics import median
from typing import Optional

from backend.core import debug_center


# ==================== 几何工具 ====================
def _bbox_iou(a, b) -> float:
    """归一化坐标系的 IoU. box = (x, y, w, h), 左上角 + 宽高"""
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def _bbox_center(b):
    x, y, w, h = b
    return (x + w / 2.0, y + h / 2.0)


def _translate_bbox(bbox, dx: float, dy: float):
    """平移归一化 bbox，并把左上角限制在画面内。"""
    x, y, w, h = bbox
    return (
        min(max(0.0, x + dx), max(0.0, 1.0 - w)),
        min(max(0.0, y + dy), max(0.0, 1.0 - h)),
        w,
        h,
    )


def _one_to_one_iou_matches(items: dict, boxes, threshold: float):
    """按全局 IoU 降序做一对一匹配，避免检测数组顺序改变造成 ID 抢占。"""
    candidates = []
    for iid, state in items.items():
        for box_index, bbox in enumerate(boxes):
            iou = _bbox_iou(state.bbox, bbox)
            if iou > threshold:
                candidates.append((-iou, iid, box_index))
    candidates.sort()

    used_ids = set()
    used_boxes = set()
    matches = []
    for neg_iou, iid, box_index in candidates:
        if iid in used_ids or box_index in used_boxes:
            continue
        used_ids.add(iid)
        used_boxes.add(box_index)
        matches.append((iid, box_index, -neg_iou))
    return matches


def _spatial_sort_boxes(boxes):
    """把 boxes 按"从上到下、从左到右"空间顺序排序, 用于稳定编号 (#1..#N).

    box=(x,y,w,h) 左上+宽高(归一化). 先按行分带(行高×1.5 容差, 避免同排螺丝因
    y 微抖被打乱), 同一行内再按中心 x 从左到右. 固定工装下 #5 永远是同一个位置点.
    """
    boxes = list(boxes)
    if len(boxes) <= 1:
        return boxes
    hs = [b[3] for b in boxes if len(b) >= 4 and b[3] > 0]
    band = (sum(hs) / len(hs) * 1.5) if hs else 0.05
    if band <= 0:
        band = 0.05

    def _key(b):
        cx = b[0] + b[2] / 2.0
        cy = b[1] + b[3] / 2.0
        return (int(cy / band), cx)

    return sorted(boxes, key=_key)


# ==================== 个体状态 ====================
class _PerItemItemState:
    """单个个体在某一步骤里的状态"""
    __slots__ = (
        'item_id', 'bbox', 'last_seen_frame', 'last_seen_time',
        'covered', 'consecutive_overlap_frames', 'first_covered_at',
        'associated', 'last_aligned_frame',
        # ── 单件超时未覆盖警告 (v3.56+, warn_uncovered_after_sec>0 时才用到) ──
        'first_seen_time',             # 个体首次进入个体表的时刻 (超时警告计时起点)
        'warn_fired',                  # 本周期是否已对该件报过"超时未覆盖"警告 (防重)
        # ── 重复打同一颗螺丝防护 (covered 之后才用到, 默认全零 = 老行为) ──
        'released_after_cover',        # covered 后是否已有“目标重现/明确打到其他 ID”的抬枪正证据
        'post_cover_away_frames',      # covered 后抬枪正证据的连续帧数
        'redup_overlap_frames',        # 明确转移后重新关联回来的连续帧数
        'redup_warned',                # 本次“关联回来”是否已报过（再转移再返回可复位）
        'redup_count',                 # 本周期内被重复打的次数 (供前端标记/统计)
    )

    def __init__(self, item_id: int, bbox, frame_id: int, ts: float):
        self.item_id = item_id
        self.bbox = bbox                       # (x, y, w, h) 归一化
        self.last_seen_frame = frame_id
        self.last_seen_time = ts
        self.covered = False
        self.consecutive_overlap_frames = 0
        self.first_covered_at: Optional[float] = None
        # 当前帧是否有 item 检测框与该逻辑 ID 完成一对一关联。整板平移可继续
        # 推进 bbox，但不能把“只有预测位置”伪装成已关联目标。
        self.associated = True
        # 最近一次确认逻辑位置仍可信的帧。直接关联，或由其余锚点算出可靠的
        # 整板平移时都会刷新；它与“当前帧是否显示编号”是两个独立概念。
        self.last_aligned_frame = frame_id
        self.released_after_cover = False
        self.post_cover_away_frames = 0
        self.redup_overlap_frames = 0
        self.redup_warned = False
        self.redup_count = 0
        self.first_seen_time = ts
        self.warn_fired = False

    def to_dict(self):
        return {
            'id': self.item_id,
            'bbox': list(self.bbox),
            'covered': self.covered,
            'covered_at': self.first_covered_at,
            'associated': self.associated,
            'dup': self.redup_count,               # >0 表示本周期被重复打过 (前端可高亮)
            'warned': self.warn_fired,             # 超时未覆盖警告已发 (前端可高亮)
        }


# ==================== 单步骤状态 ====================
class _PerItemStep:
    """单个 per_item 步骤的运行时状态"""
    __slots__ = (
        'step_id', 'step_label', 'display_label',
        'item_label', 'action_label',
        'item_tracking_iou', 'coverage_iou', 'sustain_frames',
        'coverage_use_center',             # v3.12+: 小物件场景启用"中心点判定" (替代 IoU)
        'completion', 'min_item_count',
        'expected_count',                  # v3.9+: 已知固定个体数 (0=未配置, 走 auto 路径)
        'items', 'next_item_id', 'locked_count', 'completed',
        'completed_count_in_session',
        # ── 单件超时未覆盖警告 (v3.56+, 默认 0=关): 个体出现 N 秒仍未被本步骤动作
        #    覆盖 → 借 warn_event_id 事件的响应面报一次警 (不结周期不落账, 覆盖后自然恢复) ──
        'warn_uncovered_after_sec', 'warn_event_id',
    )

    def __init__(self, raw_step: dict):
        per = raw_step.get('per_item') or {}
        self.step_id = raw_step.get('id')
        self.step_label = raw_step.get('label', '')
        self.display_label = raw_step.get('displayLabel') or raw_step.get('display_name') or self.step_label

        # ── 必填: item_label / action_label ──
        # 兼容字符串 ('5N螺丝') 或数组 (['5N螺丝', '7N螺丝'], 表示 OR 关系)
        # 内部统一存为 tuple[str, ...], 空串过滤掉
        raw_item_label = per.get('item_label', '')
        if isinstance(raw_item_label, (list, tuple)):
            self.item_label: tuple = tuple(s for s in raw_item_label if isinstance(s, str) and s)
        elif isinstance(raw_item_label, str) and raw_item_label:
            self.item_label = (raw_item_label,)
        else:
            self.item_label = tuple()
        # v3.56+: action_label 与 item_label 对称支持数组 OR
        # (典型: 一行配对 产品1..5 ⟶ 工装1..5上打螺钉, 位置配对由 IoU/中心点判定天然完成)
        raw_action_label = per.get('action_label', '')
        if isinstance(raw_action_label, (list, tuple)):
            self.action_label: tuple = tuple(s for s in raw_action_label if isinstance(s, str) and s)
        elif isinstance(raw_action_label, str) and raw_action_label:
            self.action_label = (raw_action_label,)
        else:
            self.action_label = tuple()

        # ── 阈值与帧数 ──
        self.item_tracking_iou = float(per.get('item_tracking_iou', 0.3))
        self.coverage_iou = float(per.get('coverage_iou', 0.3))
        self.sustain_frames = int(per.get('sustain_frames', 5))
        # v3.12+ "小物件中心点判定": 适用涂黑 / 喷漆 / 扫码贴标 这类"动作 box 远大于物件 box"
        # 的场景. 启用后, 覆盖判定改为"物件中心点是否落在动作 box 内", 不再用 IoU.
        # 默认 False, 维持 IoU > coverage_iou 的老逻辑.
        self.coverage_use_center = bool(per.get('coverage_use_center', False))

        self.completion = per.get('completion', 'all_covered')
        self.min_item_count = per.get('min_item_count', 'auto')

        # ── 单件超时未覆盖警告 (v3.56+, 默认 0=关 → 老项目零差异) ──
        # 典型: 端子应随产品同时在位, 产品出现 N 秒仍无端子 → 立即报警提示缺件.
        try:
            self.warn_uncovered_after_sec = max(0.0, float(per.get('warn_uncovered_after_sec', 0) or 0))
        except (TypeError, ValueError):
            self.warn_uncovered_after_sec = 0.0
        try:
            self.warn_event_id = max(0, int(per.get('warn_event_id', 0) or 0))
        except (TypeError, ValueError):
            self.warn_event_id = 0

        # ── 固定数量模式 (v3.9+) ──
        # 配了 expected_count > 0 时:
        #   1. 周期开始判定改为"检出数 ≥ expected_count × tolerance_ratio"
        #   2. 周期开始锁定 min(expected_count, 当前检出数) 颗
        #   3. 周期内 lock_lookahead_seconds 秒内继续吸收新位置, 直到补满 expected_count
        try:
            ec = per.get('expected_count', 0)
            if isinstance(ec, str):
                ec = 0 if ec.strip().lower() in ('', 'auto') else int(ec)
            self.expected_count = max(0, int(ec))
        except (TypeError, ValueError):
            self.expected_count = 0

        # ── 运行时状态 ──
        self.items: dict[int, _PerItemItemState] = {}
        self.next_item_id = 1
        self.locked_count = 0
        self.completed = False
        self.completed_count_in_session = 0      # 本 session 内完成次数

    # ──── 锁定个体表 ────
    def lock_items_from_boxes(self, boxes, frame_id: int, ts: float):
        """周期开始时一次性锁定个体表 (按空间顺序编号: 上→下、左→右)"""
        self.items.clear()
        self.next_item_id = 1
        for bbox in _spatial_sort_boxes(boxes):
            iid = self.next_item_id
            self.next_item_id += 1
            self.items[iid] = _PerItemItemState(iid, bbox, frame_id, ts)
        self.locked_count = len(self.items)
        self.completed = False

    # ──── 周期开始/结算时重置 ────
    def reset_for_new_cycle(self):
        self.items.clear()
        self.next_item_id = 1
        self.locked_count = 0
        self.completed = False

    def tracking_displacements(self, boxes):
        """返回本步骤可靠 IoU 配对产生的中心位移候选。"""
        displacements = []
        for iid, box_index, _iou in _one_to_one_iou_matches(
                self.items, boxes, self.item_tracking_iou):
            old_cx, old_cy = _bbox_center(self.items[iid].bbox)
            new_cx, new_cy = _bbox_center(boxes[box_index])
            displacements.append((
                new_cx - old_cx,
                new_cy - old_cy,
                max(self.items[iid].bbox[2], self.items[iid].bbox[3]),
            ))
        return displacements

    # ──── 更新个体位置(跨帧匹配) ────
    def update_item_positions(self, boxes, frame_id: int, ts: float,
                              lock_count_on_start: bool,
                              global_translation=None):
        """用本帧 item_label box 更新个体表里的位置.

        锁定模式先把整板共同位移同步到所有逻辑框，再对预测框与本帧检测框做
        全局一对一最近邻关联；dynamic 模式保持原有 IoU 匹配/随见随建语义。
        """
        for state in self.items.values():
            state.associated = False

        if lock_count_on_start and global_translation is not None:
            dx, dy = global_translation
            for state in self.items.values():
                if dx or dy:
                    state.bbox = _translate_bbox(state.bbox, dx, dy)
                # (0, 0) 也是有效校准：其余至少两个锚点证明整板本帧未移动。
                state.last_aligned_frame = frame_id

        if not boxes:
            return

        if lock_count_on_start:
            # predicted bbox 已随整板移动。按中心距离做全局候选排序，并以 bbox
            # 尺寸作门限，既允许轻微残差，又不会跨到相邻螺丝。
            candidates = []
            for iid, state in self.items.items():
                scx, scy = _bbox_center(state.bbox)
                for box_index, bbox in enumerate(boxes):
                    bcx, bcy = _bbox_center(bbox)
                    x_tol = max(0.006, 1.5 * max(state.bbox[2], bbox[2]))
                    y_tol = max(0.006, 1.5 * max(state.bbox[3], bbox[3]))
                    dx_norm = abs(bcx - scx) / x_tol
                    dy_norm = abs(bcy - scy) / y_tol
                    distance = math.hypot(dx_norm, dy_norm)
                    iou = _bbox_iou(state.bbox, bbox)
                    if iou > self.item_tracking_iou or distance <= 1.0:
                        candidates.append((distance, -iou, iid, box_index))
            candidates.sort()
        else:
            candidates = [
                (1.0 - iou, -iou, iid, box_index)
                for iid, box_index, iou in _one_to_one_iou_matches(
                    self.items, boxes, self.item_tracking_iou)
            ]

        used_ids = set()
        used_boxes = set()
        for _distance, _neg_iou, iid, box_index in candidates:
            if iid in used_ids or box_index in used_boxes:
                continue
            state = self.items[iid]
            state.bbox = boxes[box_index]
            state.last_seen_frame = frame_id
            state.last_seen_time = ts
            state.associated = True
            state.last_aligned_frame = frame_id
            used_ids.add(iid)
            used_boxes.add(box_index)

        if not lock_count_on_start:
            for box_index, bbox in enumerate(boxes):
                if box_index in used_boxes:
                    continue
                # dynamic 模式: 没匹配上 → 创建新个体
                iid = self.next_item_id
                self.next_item_id += 1
                self.items[iid] = _PerItemItemState(iid, bbox, frame_id, ts)

    # ──── 应用工序覆盖 ────
    def apply_coverage(self, action_boxes, frame_id: int, ts: float,
                       dup_detect: bool = False, dup_sustain: int = 0,
                       dup_release: int = 1):
        """用本帧 action_label box 推进个体覆盖状态.

        默认逻辑 (IoU):
            对每个 action box, 找 IoU 最高的个体. IoU > 阈值 → 该个体本帧被覆盖.
        小物件中心点判定 (coverage_use_center=True):
            适用 涂黑 / 喷漆 / 扫码贴标 等"动作 box 远大于物件 box"的场景.
            物件中心点落在任一 action box 内 → 该物件本帧被覆盖.
            (因为 IoU 在小物件 vs 大动作框之间永远算不到 0.3, 但物理上确实"盖住了")

        被覆盖的个体: 连续重叠帧数 +1; 反之归零.
        累积达到 sustain_frames 那一刻翻转 covered=true.

        重复打防护 (dup_detect=True):
            个体已 covered 之后, 必须先连续观察到以下任一抬枪正证据 (released):
            1) 该螺丝本体重新完成一对一关联，且电枪不再覆盖它；
            2) 电枪明确关联到另一颗逻辑 ID。
            单纯动作框消失、目标仍被遮挡属于负证据，可能只是卡枪或模型漏检，不能开门。
            released 后电枪再次覆盖本 ID 并持续 dup_sustain 帧，才判定重复打；返回时允许
            使用由其余螺丝校准过的可信预测框，因为电枪通常会再次遮住目标本体。
            每个“关联回来”回合只报一次，再明确打到其他 ID 后可重新报.
        返回: 本帧新判定为"重复打"的 item_id 列表 (dup_detect=False 时恒为空).
        """
        # 1. 标记本帧哪些个体被某个 action box 覆盖到
        # 注意: bbox 是 xywh 格式 (左上角 + 宽高), 看 _bbox_iou 注释和 _bbox_center 实现.
        overlapping_ids = set()
        # 当前帧没有 item 检测框只影响 UI 编号，不等于逻辑位置失效。只要其余
        # 螺丝仍能校准整板，last_aligned_frame 会逐帧刷新；单目标/短遮挡场景则
        # 保留一个有上限的兼容窗口。长期失去全部锚点的陈旧位置仍不接受覆盖。
        alignment_grace_frames = max(30, self.sustain_frames * 3)
        eligible_ids = {
            iid for iid, state in self.items.items()
            if state.associated
            or (frame_id - state.last_aligned_frame) <= alignment_grace_frames
        }
        if self.coverage_use_center:
            for iid, st in self.items.items():
                if iid not in eligible_ids:
                    continue
                cx, cy = _bbox_center(st.bbox)
                for abox in action_boxes:
                    ax, ay, aw, ah = abox
                    if ax <= cx <= ax + aw and ay <= cy <= ay + ah:
                        overlapping_ids.add(iid)
                        break
        else:
            for abox in action_boxes:
                best_iid = None
                best_iou = self.coverage_iou
                for iid, st in self.items.items():
                    if iid not in eligible_ids:
                        continue
                    iou = _bbox_iou(st.bbox, abox)
                    if iou > best_iou:
                        best_iou = iou
                        best_iid = iid
                if best_iid is not None:
                    overlapping_ids.add(best_iid)

        # direct 集合只用于取得“电枪确实去了另一颗”的抬枪正证据。返回原目标时，
        # 电枪通常会遮住螺丝本体，因此在 released 已由正证据确认后，可使用由
        # 其余锚点校准过、仍在 alignment_grace 内的预测逻辑框。
        direct_overlapping_ids = {
            iid for iid in overlapping_ids
            if self.items[iid].associated
        }

        # 2. 推进/归零各个体的连续重叠帧数
        reoccur_ids = []
        for iid, st in self.items.items():
            if iid in overlapping_ids:
                st.consecutive_overlap_frames += 1
                if (not st.covered) and st.consecutive_overlap_frames >= self.sustain_frames:
                    st.covered = True
                    st.first_covered_at = ts
                elif st.covered and dup_detect:
                    # 电枪回到本 ID 会终止任何尚未完成的抬枪确认。
                    st.post_cover_away_frames = 0
                    if st.released_after_cover:
                        st.redup_overlap_frames += 1
                        if st.redup_overlap_frames >= max(1, dup_sustain) and not st.redup_warned:
                            st.redup_warned = True
                            st.redup_count += 1
                            reoccur_ids.append(iid)
                    else:
                        st.redup_overlap_frames = 0
            else:
                st.consecutive_overlap_frames = 0
                if st.covered and dup_detect:
                    st.redup_overlap_frames = 0
                    release_evidence = st.associated or bool(direct_overlapping_ids)
                    if release_evidence:
                        # 正证据 A：已打螺丝本体重新出现且不再被电枪覆盖；
                        # 正证据 B：电枪明确落在另一颗逻辑 ID。
                        st.post_cover_away_frames += 1
                        if st.post_cover_away_frames >= max(1, dup_release):
                            st.released_after_cover = True
                            st.redup_warned = False
                    else:
                        # 目标仍被遮挡且动作框漏检：这正是卡枪形态，连续证据归零。
                        st.post_cover_away_frames = 0
        return reoccur_ids

    # ──── 个体超时清理 ────
    def cleanup_stale_items(self, ts: float, timeout_sec: float, lock_count_on_start: bool):
        """清理超时未出现的个体.

        锁定模式 (lock_count_on_start=true):
            周期内一律不清 — 工人手/工具会遮挡未覆盖件长达数秒,
            清掉会导致后续工序 box 找不到匹配 → 永远 NG.
            个体表只在周期结算时整体 reset.
        dynamic 模式 (lock_count_on_start=false):
            按 timeout_sec 清, 0 = 不清.
        """
        if lock_count_on_start:
            return
        if timeout_sec <= 0:
            return
        stale = [
            iid for iid, st in self.items.items()
            if (ts - st.last_seen_time) > timeout_sec
        ]
        for iid in stale:
            self.items.pop(iid, None)

    # ──── 步骤完成判定 ────
    def check_completion(self) -> bool:
        if self.completion == 'all_covered':
            if not self.items:
                return False
            # v3.12+ 虚拟漏件 NG: 配了 expected_count 时,锁定数必须达到期望颗数才算完成.
            # 配 14 颗 5N 螺丝, 但模型只稳定看到 12 颗 → 锁了 12 → 工人扭满 12 颗也算 NG (差 2 颗虚拟漏件).
            # 通过设 expected_count=0 可关闭此严格检查 (回到老行为, 锁多少扭多少都算 OK).
            if self.expected_count > 0 and len(self.items) < self.expected_count:
                return False
            for st in self.items.values():
                if not st.covered:
                    return False
            return True
        # 未实现的完成类型: 兜底为 False
        return False

    def covered_count(self) -> int:
        return sum(1 for s in self.items.values() if s.covered)

    def to_state_dict(self, strict_display: bool = False):
        # v3.10.2+ display_total 计算: 取决于全局 require_exact_count (由 host 传入)
        #   - 严格等量 (strict_display=True) + 配了 expected_count → 显示 expected_count
        #     (触发瞬间就保证锁满, UI 显示分母 = 期望数, 视觉上一定整齐)
        #   - 否则 → 显示真实锁定数 len(items)
        #     (宽松模式 / 未配期望, UI 显示实际锁了多少, 模型漏检一眼能看出, 不假装"等量")
        if strict_display and self.expected_count > 0:
            display_total = self.expected_count
        else:
            display_total = len(self.items)
        return {
            'step_id': self.step_id,
            'label': self.step_label,
            'display_label': self.display_label,
            # item_label / action_label 内部是 tuple, 序列化时若仅 1 个还原为字符串 (兼容老前端)
            'item_label': self.item_label[0] if len(self.item_label) == 1 else list(self.item_label),
            'action_label': self.action_label[0] if len(self.action_label) == 1 else list(self.action_label),
            'warn_uncovered_after_sec': self.warn_uncovered_after_sec,
            'expected_count': self.expected_count,
            'total': len(self.items),                  # 真实锁定数
            'display_total': display_total,            # 前端展示用分母 (期望优先)
            'locked_count': self.locked_count,
            'covered_count': self.covered_count(),
            'completed': self.completed,
            'items': [self.items[iid].to_dict() for iid in sorted(self.items.keys())],
        }


# ==================== 项目 session ====================
class _PerItemSession:
    """整个 per_item 项目的运行时 session"""
    __slots__ = (
        'cycle_active', 'cycle_start_time', 'cycle_start_frame_id',
        'stability_buffer',
        'finish_label_seen_at', 'finish_label_consec_frames',
        'frame_id',
        'last_activity_time',           # 最近一次看到 item/action 标签的时刻 (空闲超时用)
        'all_done_first_at',            # 所有步骤首次全 completed 的时刻 (完成即结算用)
        'lock_lookahead_deadline',      # 周期开始后补锁定窗口的截止时刻
        # ── v3.x 工件离场快照判定 + 待补/待确认态 (打螺丝漏打场景) ──
        'awaiting_remediation',         # True = 离场判 NG 后挂起, 等补打/人工确认, 周期不结案
        'leave_consec_frames',          # 工件标签连续消失帧数 (离场确认计数)
        'workpiece_absent_frames',      # 工件全标签连续消失帧数 (换板兜底结算计数)
        'await_since',                  # 进入待补态的时刻 (待补超时用)
        'leave_finish_seen',            # 本周期内是否出现过拿取结算动作 (双条件离场 latch)
        'last_remediation_alarm_at',    # 上次触发待补报警的时刻 (持续报警节流用)
        # ── 判定层 (判定时机 ≠ 结算时机, 默认 on_settle 不启用 = 老项目零差异) ──
        'judged',                       # 本周期是否已由"判定层"判过 (绿/红已亮)
        'judged_ok',                    # 判定层暂存的合格结果 (True=绿灯待取走, False=红灯待补)
        'judge_done_first_at',          # all_done 判定: 首次全完成时刻 (确认保持秒数用)
        'judge_label_consec',           # label 判定: 判定标签连续出现帧数
        'plc_pulse_fired',              # 本周期是否已发过 PLC 完成脉冲 (all_covered 边沿锁)
    )

    def __init__(self):
        self.cycle_active = False
        self.cycle_start_time: Optional[float] = None
        self.cycle_start_frame_id: Optional[int] = None
        self.stability_buffer = deque(maxlen=10)        # 真值: 窗口帧数, 配置时改长度
        self.finish_label_seen_at: Optional[float] = None
        self.finish_label_consec_frames = 0
        self.frame_id = 0
        self.last_activity_time: Optional[float] = None
        self.all_done_first_at: Optional[float] = None
        self.lock_lookahead_deadline: Optional[float] = None
        self.awaiting_remediation = False
        self.leave_consec_frames = 0
        self.workpiece_absent_frames = 0
        self.await_since: Optional[float] = None
        self.leave_finish_seen = False
        self.last_remediation_alarm_at: Optional[float] = None
        self.judged = False
        self.judged_ok = False
        self.judge_done_first_at: Optional[float] = None
        self.judge_label_consec = 0
        self.plc_pulse_fired = False

    def reset_after_cycle(self):
        self.cycle_active = False
        self.cycle_start_time = None
        self.cycle_start_frame_id = None
        self.stability_buffer.clear()
        self.finish_label_seen_at = None
        self.finish_label_consec_frames = 0
        self.last_activity_time = None
        self.all_done_first_at = None
        self.lock_lookahead_deadline = None
        self.awaiting_remediation = False
        self.leave_consec_frames = 0
        self.workpiece_absent_frames = 0
        self.await_since = None
        self.leave_finish_seen = False
        self.last_remediation_alarm_at = None
        self.judged = False
        self.judged_ok = False
        self.judge_done_first_at = None
        self.judge_label_consec = 0
        self.plc_pulse_fired = False


# ==================== 主 Mixin ====================
class PerItemMixin:
    """per_item 模式入口. 由 VideoSourceManager 多继承.

    宿主依赖:
      - self.project_config (含 logic_mode / pipeline_config / steps_config)
      - self._trigger_event(event_id, reason)
      - self.step_counts (dict, step_label → 完成次数)
      - self.current_cycle_steps (list, 兼容现有 UI 输出)
      - self.step_screenshots (用于前端兼容, 可选填)
      - self.cycle_start_time (用于 detection/results 的 current_cycle_time)
    """

    # ──── lazy init ────
    def _per_item_ensure_initialized(self):
        """惰性初始化, 避免改动 _init_inference_vars / set_project_config"""
        if getattr(self, '_per_item_session', None) is None:
            self._per_item_session = _PerItemSession()
        if getattr(self, '_per_item_config', None) is None:
            self._per_item_config = None
        if getattr(self, '_per_item_steps', None) is None:
            self._per_item_steps: list[_PerItemStep] = []

    # ──── 配置加载入口(由 source_project_config_apply 调) ────
    def _per_item_apply_config(self, config: dict) -> bool:
        """从 project_config 解析 per_item 配置, 构建步骤运行时对象.

        返回 True 表示成功启用 per_item, False 表示当前项目非 per_item 模式
        或配置缺失 → 调用方无需做 per_item 任何后续操作.
        """
        self._per_item_ensure_initialized()
        if not config:
            self._per_item_config = None
            self._per_item_steps = []
            return False

        if config.get('logic_mode') != 'per_item':
            self._per_item_config = None
            self._per_item_steps = []
            return False

        pipeline = config.get('pipeline_config') or {}
        per_item_cfg = pipeline.get('per_item') or {}

        # ── 项目级参数 ──
        stability_window = int(per_item_cfg.get('stability_window_frames', 10))
        stability_iou = float(per_item_cfg.get('stability_iou_threshold', 0.7))
        stability_count_tol = int(per_item_cfg.get('stability_count_tolerance', 0))
        item_timeout = float(per_item_cfg.get('item_timeout_seconds', 3.0))
        lock_on_start = bool(per_item_cfg.get('lock_count_on_start', True))
        finish_label = per_item_cfg.get('finish_label', '')
        finish_sustain = int(per_item_cfg.get('finish_sustain_frames', 3))
        settle_after_all_done = float(per_item_cfg.get('settle_after_all_done_sec', 0.0))
        lock_lookahead = float(per_item_cfg.get('lock_lookahead_seconds', 5.0))

        # v3.9+ per_item 专属字段 (与其他模式隔离, 不读项目级 cycle_max_duration / idle_timeout_seconds)
        # 兼容老 per_item 项目: 若专属字段未配, 落回项目级老字段 (一次性数据迁移)
        cycle_max_sec = float(per_item_cfg.get('cycle_max_duration_sec', 0.0))
        if cycle_max_sec <= 0:
            cycle_max_sec = float(pipeline.get('cycle_max_duration', 0) or 0)
        idle_timeout_sec = float(per_item_cfg.get('idle_timeout_sec', 0.0))
        if idle_timeout_sec <= 0:
            idle_timeout_sec = float(pipeline.get('idle_timeout_seconds', 0) or 0)

        # 路径 A 检出比例 (替换之前硬编码 0.85)
        # 含义: 配了 expected_count 时, 检出数 ≥ expected_count × stability_count_ratio 即放行
        stability_count_ratio = float(per_item_cfg.get('stability_count_ratio', 0.85))

        # v3.10.2+ 严格等量触发开关
        # True 时: 触发条件改为"窗口里每一帧检出数 == expected_count" + 同时刻次步检出 ≥ 各自 expected_count
        # → 触发瞬间所有步骤都能锁满, 但 trigger 时机会显著延后 (需要场景里所有目标真的全部识到)
        # False (默认): 走 stability_count_ratio 宽松路径 (=0.85 即"够 85% 就触发, 后面靠 lookahead 补")
        require_exact_count = bool(per_item_cfg.get('require_exact_count', False))

        # v3.10.2+ 手动结算模式开关
        # True  : 所有自动结算路径全部禁用 (finish_label / settle_after_all_done / cycle_max / idle_timeout)
        #         周期开始仍自动 (画面稳定锁定), 但结算时机只能靠 per-item-control?action=settle 手动触发
        # False : (默认) 各自动结算路径按各自参数生效
        disable_auto_settle = bool(per_item_cfg.get('disable_auto_settle', False))

        # v3.12+ 工件离场互斥开关
        # True  : finish_label 触发结算时, 同帧画面里如果还有任何工件标签 (各 step.item_label)
        #         → 这一帧不算结算累积 (工件没真离场, 拿取手势误识别)
        # False : (默认) 老行为, 只看 finish_label 是否连续出现, 不管桌面是否还有工件
        finish_requires_no_items = bool(per_item_cfg.get('finish_requires_no_items', False))

        # ── 工件离场快照判定 + 待补/待确认态 (打螺丝漏打场景, 默认全关 = 老项目零差异) ──
        # judge_on_workpiece_leave=True 时:
        #   - 判定时机从"停手/收尾标签/全完成保持"改为"工件标签持续消失 (离场)"
        #   - 离场瞬间取覆盖快照 (覆盖单调, 即周期累积结果) 判 OK/NG
        #   - 全程实时看板靠 get_per_item_state 已有逐颗 covered, 前端展示
        #   - 开启后, settle_after_all_done / finish_label / idle_timeout 自动结算路径全部绕开
        #     (cycle_max_duration_sec 仍作超时兜底)
        judge_on_workpiece_leave = bool(per_item_cfg.get('judge_on_workpiece_leave', False))
        leave_confirm_frames = int(per_item_cfg.get('leave_confirm_frames', 10))
        # ng_hold_for_remediation=True 时, 离场判 NG 不立即落账, 进入待补/待确认态:
        #   提示漏点 → 工件放回补满则转 OK; 人工确认/超时则按 NG 落账
        ng_hold_for_remediation = bool(per_item_cfg.get('ng_hold_for_remediation', False))
        remediation_timeout_sec = float(per_item_cfg.get('remediation_timeout_sec', 0.0))
        # remediation_event_id: "场上还有没扭的螺丝"(待补态) 触发哪个事件的报警.
        #   灯做成事件 — 不写死, 由用户在报警配置里把该事件映射到红灯/蜂鸣等.
        #   0 = 不主动触发 (纯用待补状态, 由前端/用户自行联动). OK/NG 仍走标准事件 1/2.
        remediation_event_id = int(per_item_cfg.get('remediation_event_id', 0) or 0)
        # ── 待补态增强 (可选, 默认全关 = 老项目零差异) ──
        # 注: "离场判定双条件(拿取动作 + 工件离场)" 复用上面的 finish_requires_no_items 开关,
        #   不再单设字段 — 一个开关同时管"收尾标签结算"和"工件离场判定"两条路径, 避免重复按钮.
        # remediation_takeaway_ng=True 时: 待补态(红灯)期间若再次出现拿取结算动作却未补满,
        #   自动按 NG 落账 ("红灯内取件算 NG", 工人没补就把件带走 → 计 NG).
        remediation_takeaway_ng = bool(per_item_cfg.get('remediation_takeaway_ng', False))
        # remediation_alarm_mode: 待补报警触发形式. 'once'=进待补只触发一次;
        #   'sustained'=按 remediation_alarm_interval_sec 间隔持续重复触发, 直到补满/确认.
        #   撤报警靠 OK/NG 标准事件 (1/2) 由用户在报警配置里联动, 这里不写死灭灯.
        remediation_alarm_mode = str(per_item_cfg.get('remediation_alarm_mode', 'once') or 'once')
        if remediation_alarm_mode not in ('once', 'sustained'):
            remediation_alarm_mode = 'once'
        remediation_alarm_interval_sec = float(per_item_cfg.get('remediation_alarm_interval_sec', 2.0))

        # ── 视频检测框按"扭完/未扭"覆盖态上色 (打螺丝漏打场景, 默认关 = 老项目零差异) ──
        # color_by_coverage=True 时: 前端把 item_label 的检测框按逐颗覆盖态重新上色
        #   (已扭→box_color_covered, 未扭→box_color_uncovered). 颜色留空则回退全局 OK/NG 色.
        #   纯前端绘制行为, 不动后端检测/结算逻辑.
        color_by_coverage = bool(per_item_cfg.get('color_by_coverage', False))
        box_color_covered = str(per_item_cfg.get('box_color_covered', '') or '')
        box_color_uncovered = str(per_item_cfg.get('box_color_uncovered', '') or '')
        # show_item_numbers=True 时: 前端在每颗个体检测框上叠"#编号"(按空间序), 不同标签各自从 #1 起.
        #   纯前端绘制, 不动后端检测/结算. 编号 = 锁定时按空间排序分配的 item_id.
        show_item_numbers = bool(per_item_cfg.get('show_item_numbers', False))

        # ── 判定时机 (与"结算时机"解耦, 默认 'on_settle' = 结算那刻一次性判 = 老项目零差异) ──
        # judge_timing:
        #   'on_settle' (默认): 不单设判定, 取走/收尾/全完成那刻才判 OK/NG (老行为)
        #   'all_done'        : 所有件覆盖完(可+judge_all_done_sec确认) → 判定层亮绿(合格事件);
        #                       此后保持, 等"结算时机"(离场/收尾)才落账. 漏件时此触发不亮(全完成才触发),
        #                       漏件的红灯仍由结算那刻判 NG / 待补给出.
        #   'label'           : 指定 judge_label 标签连续出现 → 判定层拍快照: 全覆盖亮绿 / 有漏亮红+漏点,
        #                       工人补满→翻绿; 结算时机到了再落账.
        # 绿灯走可配 judge_ok_event_id (与落账"合格"事件 1 分开: 判定时亮绿, 落账时另算).
        # 红灯复用 remediation_event_id + ng_hold_for_remediation (与离场待补同一套).
        judge_timing = str(per_item_cfg.get('judge_timing', 'on_settle') or 'on_settle')
        if judge_timing not in ('on_settle', 'all_done', 'label', 'manual'):
            judge_timing = 'on_settle'
        judge_label = str(per_item_cfg.get('judge_label', '') or '')
        judge_all_done_sec = float(per_item_cfg.get('judge_all_done_sec', 0.0) or 0.0)
        judge_label_frames = int(per_item_cfg.get('judge_label_frames', 3) or 3)
        judge_ok_event_id = int(per_item_cfg.get('judge_ok_event_id', 0) or 0)

        # ── 重复打同一颗螺丝防护 (打螺丝漏打的反面, 默认关 = 老项目零差异) ──
        # 语义: 螺丝已 covered 后, 动作框"曾移开再压回来"并持续 duplicate_sustain_frames
        #   帧 → 判"重复打", 复用 NG 事件 (event2) 点报警灯 + PerItemPanel 黄条提示,
        #   不落账 / 不结束周期 / 不动覆盖状态 (覆盖单调).
        # 待补态天然覆盖: 待补时 cycle_active 仍 True, 已打的螺丝逻辑不变 → 待补态里回头
        #   重打已打的螺丝照样报, 而补打漏掉的 (covered=False) 螺丝是合法补打不报.
        duplicate_screw_alarm = bool(per_item_cfg.get('duplicate_screw_alarm', False))
        duplicate_sustain_frames = int(per_item_cfg.get('duplicate_sustain_frames', 2) or 2)
        # 目标螺丝重新出现，或动作框关联到其他逻辑 ID，连续足够久才确认抬枪；
        # 目标仍被遮挡时的纯动作漏检不累计。默认 8 帧。
        duplicate_release_frames = int(per_item_cfg.get('duplicate_release_frames', 8) or 8)
        duplicate_alarm_interval_sec = float(per_item_cfg.get('duplicate_alarm_interval_sec', 2.0) or 0.0)
        # 重复打提示横幅在前端的存在时间 (秒). 0 = 不自动撤 (持续到周期结束/下次重复打刷新).
        duplicate_warning_display_sec = float(per_item_cfg.get('duplicate_warning_display_sec', 3.0) or 0.0)

        # ── 换板兜底结算 (工件整体消失确认, 默认关 = 老项目零差异) ──
        # 修 bug: finish_label 单一结算时, 若换板动作没被检到"拿取结算", 上一板周期永不结算,
        #   其逐颗 covered 会经 update_item_positions 按位置泄漏到新板 (新板未打却变绿, 计数为 0).
        # workpiece_absent_settle_frames > 0 时: 周期进行中, 所有 item 标签连续消失该帧数
        #   → 视为工件被拿走/换板 → 自动按真实覆盖状态兜底结算 (OK/NG) 并 reset, 新板从零重锁.
        #   只在"全部件都不见"才累计, 手拧单颗不会让整板全消失, 误触发风险极低.
        #   离场判定模式 (judge_on_workpiece_leave) 自带离场结算, 不走此兜底.
        workpiece_absent_settle_frames = int(per_item_cfg.get('workpiece_absent_settle_frames', 0) or 0)

        # ── v3.56+ 三个新键 (默认全关/空 = 老项目零差异) ──
        # absorb_new_items_sec > 0: 周期开始后 N 秒窗口内, expected_count=0 (auto) 的步骤也
        #   无上限吸收"新位置"的个体 —— 治"工人逐个放件, 稳定窗口先锁了前几件"的场景
        #   (老 lock_lookahead 只对配了 expected_count 的步骤生效且有封顶, 语义保持不变).
        absorb_new_items_sec = float(per_item_cfg.get('absorb_new_items_sec', 0) or 0)
        # item_count_counter_name 非空: 周期判 OK 落账时, 把首个 per_item 步骤本周期
        #   已覆盖件数累加进该名字的计数器 (计数器需在 counters_config 里定义).
        #   给"按件计产量"场景用 (一周期 N 件, 客户要累计件数而非周期数).
        item_count_counter_name = str(per_item_cfg.get('item_count_counter_name', '') or '')
        # remediation_event_notify=True: 进待补态时借 remediation_event_id 事件的完整响应面
        #   (灯 + Toast + 语音, remind_only 不计数不定格), 替代"只点灯"的老行为.
        remediation_event_notify = bool(per_item_cfg.get('remediation_event_notify', False))

        self._per_item_config = {
            'stability_window_frames': max(1, stability_window),
            'stability_iou_threshold': stability_iou,
            'stability_count_tolerance': max(0, stability_count_tol),
            'stability_count_ratio': max(0.1, min(1.0, stability_count_ratio)),
            'require_exact_count': require_exact_count,
            'disable_auto_settle': disable_auto_settle,
            'finish_requires_no_items': finish_requires_no_items,
            'item_timeout_seconds': max(0.0, item_timeout),
            'lock_count_on_start': lock_on_start,
            'finish_label': finish_label,
            'finish_sustain_frames': max(1, finish_sustain),
            'settle_after_all_done_sec': max(0.0, settle_after_all_done),
            'lock_lookahead_seconds': max(0.0, lock_lookahead),
            'cycle_max_duration_sec': max(0.0, cycle_max_sec),
            'idle_timeout_sec': max(0.0, idle_timeout_sec),
            'judge_on_workpiece_leave': judge_on_workpiece_leave,
            'leave_confirm_frames': max(1, leave_confirm_frames),
            'ng_hold_for_remediation': ng_hold_for_remediation,
            'remediation_timeout_sec': max(0.0, remediation_timeout_sec),
            'remediation_event_id': max(0, remediation_event_id),
            'remediation_takeaway_ng': remediation_takeaway_ng,
            'remediation_alarm_mode': remediation_alarm_mode,
            'remediation_alarm_interval_sec': max(0.5, remediation_alarm_interval_sec),
            'color_by_coverage': color_by_coverage,
            'box_color_covered': box_color_covered,
            'box_color_uncovered': box_color_uncovered,
            'show_item_numbers': show_item_numbers,
            'judge_timing': judge_timing,
            'judge_label': judge_label,
            'judge_all_done_sec': max(0.0, judge_all_done_sec),
            'judge_label_frames': max(1, judge_label_frames),
            'judge_ok_event_id': max(0, judge_ok_event_id),
            'duplicate_screw_alarm': duplicate_screw_alarm,
            'duplicate_sustain_frames': max(1, duplicate_sustain_frames),
            'duplicate_release_frames': max(1, duplicate_release_frames),
            'duplicate_alarm_interval_sec': max(0.0, duplicate_alarm_interval_sec),
            'duplicate_warning_display_sec': max(0.0, duplicate_warning_display_sec),
            'workpiece_absent_settle_frames': max(0, workpiece_absent_settle_frames),
            'absorb_new_items_sec': max(0.0, absorb_new_items_sec),
            'item_count_counter_name': item_count_counter_name,
            'remediation_event_notify': remediation_event_notify,
        }

        # ── 步骤级解析 ──
        steps_config = config.get('steps_config') or []
        self._per_item_steps = []
        for raw in steps_config:
            if not raw.get('enabled', True):
                continue
            per = raw.get('per_item') or {}
            if not per:
                # 没填 per_item 字段的步骤被视为收尾步骤, 不进入 per_item 步骤列表
                # (典型: 翻面步骤, 仅靠 finish_label 触发结算)
                continue
            if not per.get('item_label') or not per.get('action_label'):
                print(f"[per_item] 步骤 [{raw.get('label')}] per_item 配置缺 item_label/action_label, 跳过")
                continue
            self._per_item_steps.append(_PerItemStep(raw))

        # ── session 重置 + buffer 长度调整 ──
        self._per_item_session = _PerItemSession()
        self._per_item_session.stability_buffer = deque(maxlen=self._per_item_config['stability_window_frames'])

        print(
            f"[per_item] 已启用 logic_mode=per_item: 步骤数={len(self._per_item_steps)}, "
            f"稳定窗口={stability_window}帧, item_timeout={item_timeout}s, "
            f"lock_on_start={lock_on_start}, finish_label='{finish_label}'"
        )
        for s in self._per_item_steps:
            cov_mode = "中心点判定" if s.coverage_use_center else f"IoU>{s.coverage_iou}"
            print(
                f"  · 步骤 [{s.step_label}]: item='{s.item_label}', action='{s.action_label}', "
                f"sustain={s.sustain_frames}帧, 覆盖={cov_mode}, min={s.min_item_count}"
            )
        return True

    @staticmethod
    def _estimate_board_translation(displacements):
        """用跨步骤可靠配对的中位数估算整块工件本帧平移量。

        至少需要两个独立配对；再用 bbox 尺寸相关的中位数残差门剔除误配。
        单帧超过画面 8% 的跳变不属于“轻微挪板”，直接拒绝。
        """
        if len(displacements) < 2:
            return None
        dx0 = median(item[0] for item in displacements)
        dy0 = median(item[1] for item in displacements)
        typical_size = median(item[2] for item in displacements)
        residual_limit = max(0.003, typical_size * 0.75)
        inliers = [
            item for item in displacements
            if math.hypot(item[0] - dx0, item[1] - dy0) <= residual_limit
        ]
        if len(inliers) < max(2, (len(displacements) + 1) // 2):
            return None
        dx = median(item[0] for item in inliers)
        dy = median(item[1] for item in inliers)
        if abs(dx) > 0.08 or abs(dy) > 0.08:
            return None
        return (dx, dy)

    # ──── 主入口: 替代 _update_step_stats ────
    def _update_step_stats_per_item(self, detections: list, original_frame):
        """per_item 模式每帧主循环. 由 step_stats_mixin 在 logic_mode='per_item'
        时调用. 与默认 _update_step_stats 并列, 互不影响.
        """
        self._per_item_ensure_initialized()
        if not self._per_item_config:
            return

        sess = self._per_item_session
        cfg = self._per_item_config
        sess.frame_id += 1
        current_time = time.time()

        # ──── 0. 重复打提示横幅到期自动撤下 ────
        # duplicate_warning_display_sec > 0 时, 横幅只存在配置的秒数 (方便现场调试报警框停留时长);
        # 0 = 不自动撤 (维持到周期结束 / 下次重复打刷新). 报警灯的亮灯时长另由 alarm 配置的 duration 管.
        _warn = getattr(self, '_per_item_last_warning', None)
        if _warn:
            _disp = cfg.get('duplicate_warning_display_sec', 0)
            if _disp > 0 and (current_time - (_warn.get('ts') or current_time)) > _disp:
                self._per_item_last_warning = None

        # ──── 1. 按 label 分组本帧检测 ────
        boxes_by_label: dict[str, list] = {}
        # DEBUG: 收集 finish_label 在本帧的 (conf, w, h) 列表
        finish_label_dbg = (cfg or {}).get('finish_label') or ''
        finish_label_dets_this_frame: list = []
        for det in detections:
            lbl = det.get('label', '')
            if not lbl:
                continue
            bbox = (
                float(det.get('x', 0)), float(det.get('y', 0)),
                float(det.get('w', 0)), float(det.get('h', 0)),
            )
            if bbox[2] <= 0 or bbox[3] <= 0:
                continue
            boxes_by_label.setdefault(lbl, []).append(bbox)
            if finish_label_dbg and lbl == finish_label_dbg:
                finish_label_dets_this_frame.append({
                    'conf': float(det.get('conf', det.get('confidence', 0))),
                    'w': bbox[2], 'h': bbox[3],
                    'x': bbox[0], 'y': bbox[1],
                })

        # ──── 2. 周期未开始: 尝试启动 ────
        if not sess.cycle_active:
            self._per_item_try_start_cycle(boxes_by_label, current_time)
            return

        # ──── 3. 周期内: 更新每个 per_item 步骤 ────
        # "活动" = action 标签出现 (工人在做工序), 不包括 item 标签 (静态工件)
        # 这样模型把桌面误检出残留 item 标签也不会刷新 idle 计时, idle_timeout 能正常兜底
        any_action_this_frame = False
        in_lookahead = (
            sess.lock_lookahead_deadline is not None
            and current_time <= sess.lock_lookahead_deadline
        )
        # 先从所有步骤仍可靠关联的螺丝汇总本帧共同位移。这样 7N 被电枪完全
        # 遮住时，也能借 5N 的位移同步推进 7N 锁定框。
        step_boxes = [
            (step, self._collect_item_boxes(boxes_by_label, step.item_label))
            for step in self._per_item_steps
        ]
        board_translation = None
        if cfg['lock_count_on_start']:
            displacements = []
            for step, item_boxes in step_boxes:
                if item_boxes:
                    displacements.extend(step.tracking_displacements(item_boxes))
            board_translation = self._estimate_board_translation(displacements)

        for step, item_boxes in step_boxes:
            # 3a. 更新个体位置 (多标签 OR 合并)。即使本帧无 box 也必须调用，
            # 以清除 associated；但仍会把可信整板位移同步给所有锁定框。
            step.update_item_positions(
                item_boxes, sess.frame_id, current_time,
                cfg['lock_count_on_start'],
                global_translation=board_translation,
            )
            if item_boxes:
                # 3a'. 补锁定窗口 (v3.9+): 配了 expected_count + 当前锁定数 < expected_count
                # + 仍在 lookahead 窗口内 → 吸收"新位置"的 box
                if (
                    in_lookahead
                    and step.expected_count > 0
                    and len(step.items) < step.expected_count
                ):
                    self._per_item_absorb_new_items(step, item_boxes, sess.frame_id, current_time)
                # 3a''. 自由吸收窗口 (v3.56+, absorb_new_items_sec>0 才启用):
                # auto 步骤 (expected_count=0) 在周期开始后 N 秒内无上限吸收新位置个体,
                # 治"工人逐个放件, 稳定窗口先锁了前几件"的场景.
                absorb_sec = cfg.get('absorb_new_items_sec', 0)
                if (
                    absorb_sec > 0
                    and step.expected_count <= 0
                    and sess.cycle_start_time is not None
                    and (current_time - sess.cycle_start_time) <= absorb_sec
                ):
                    self._per_item_absorb_new_items(
                        step, item_boxes, sess.frame_id, current_time, unbounded=True)
            # 3b. 应用工序覆盖 (按 action_label, 多标签 OR 合并)
            action_boxes = self._collect_item_boxes(boxes_by_label, step.action_label)
            if action_boxes:
                any_action_this_frame = True
            _dup_on = cfg.get('duplicate_screw_alarm', False)
            reoccur_ids = step.apply_coverage(
                action_boxes, sess.frame_id, current_time,
                dup_detect=_dup_on,
                dup_sustain=cfg.get('duplicate_sustain_frames', 2),
                dup_release=cfg.get('duplicate_release_frames', 8),
            )
            if _dup_on and reoccur_ids:
                self._per_item_fire_duplicate_alarm(step, reoccur_ids, current_time)
            # 3c. 超时清理 (锁定模式下已是 no-op)
            step.cleanup_stale_items(
                current_time, cfg['item_timeout_seconds'],
                cfg['lock_count_on_start'],
            )
            # 3d. 完成判定 (单步)
            if not step.completed and step.check_completion():
                step.completed = True
                step.completed_count_in_session += 1
                # 把 step.step_label 加进 current_cycle_steps + step_counts 累加,
                # 让 UI 现有显示路径(数据页/SOP 卡片)继续兼容
                try:
                    if step.step_label and step.step_label not in self.current_cycle_steps:
                        self.current_cycle_steps.append(step.step_label)
                    if step.step_label:
                        self.step_counts[step.step_label] = self.step_counts.get(step.step_label, 0) + 1
                except Exception as _e:
                    print(f"[per_item] 步骤完成时回写 cycle/counts 失败: {_e}")
                print(
                    f"[per_item] 步骤 [{step.display_label}] 完成 "
                    f"({step.covered_count()}/{len(step.items)})"
                )
            # 3e. 单件超时未覆盖警告 (v3.56+, 行级 warn_uncovered_after_sec>0 才启用):
            # 个体出现 N 秒仍未被本步骤动作覆盖 → 借 warn_event_id 事件报一次警
            # (不结周期不落账, 每件每周期只报一次; 覆盖为单调翻转, 补上后面板自然恢复绿).
            if step.warn_uncovered_after_sec > 0 and step.warn_event_id > 0:
                for _iid, _ist in step.items.items():
                    if _ist.covered or _ist.warn_fired:
                        continue
                    if (current_time - _ist.first_seen_time) >= step.warn_uncovered_after_sec:
                        _ist.warn_fired = True
                        _reason = (
                            f"[{step.display_label}] #{_iid} 超过 "
                            f"{step.warn_uncovered_after_sec:g} 秒未完成"
                        )
                        print(f"[per_item] 单件超时警告: {_reason}")
                        if debug_center.is_on("backend.per_item"):
                            debug_center.dbg("backend.per_item", "单件超时警告",
                                             f"channel={self.channel_id} {_reason}")
                        try:
                            self.fire_external_event_response(
                                step.warn_event_id, _reason, source='per_item_warn')
                        except Exception as _we:
                            print(f"[per_item] 单件超时警告事件触发失败: {_we}")

        # 刷新 last_activity_time (本帧出现 action 标签 = 工人在做工序 = 有活动)
        # 注意: 只看 action, 不看 item — 工件静置画面里有 item 标签不算"活动",
        # 否则工人放工件不操作时永远 idle=0, 触发不了 idle_timeout 兜底.
        if any_action_this_frame:
            sess.last_activity_time = current_time

        # ──── 3.5 PLC 完成脉冲 (触发模式 A: all_covered) ────
        # 本周期所有 per_item 步骤"首次全部 completed"的那一帧发一次, 不等结算 —— 给
        # 需要"打完立刻吹气"的现场用。同周期边沿锁, 冷却由外设 cooldown_ms 再兜一道。
        # 只对配了 trigger_mode='all_covered' 的外设生效; 没配就是纯 no-op。
        if (not sess.plc_pulse_fired and self._per_item_steps
                and all(s.completed for s in self._per_item_steps)):
            sess.plc_pulse_fired = True
            self._per_item_notify_plc_pulse('all_covered')

        # v3.10.2+ 手动结算模式: 所有自动结算路径全部禁用, 只能靠 manual_settle API 结算
        disable_auto_settle = cfg.get('disable_auto_settle', False)

        # ──── 3.8 判定层 (判定时机 ≠ 结算时机时启用) ────
        # 在结算之前先跑判定: 到判定时机就拍快照亮绿/红, 但不落账; 落账仍由下面 3.9-7 的结算时机负责.
        # judge_timing='on_settle' (默认) 时本调用直接返回, 老行为零差异.
        if cfg.get('judge_timing', 'on_settle') != 'on_settle':
            self._per_item_judge_layer_tick(boxes_by_label, current_time)

        # ──── 3.9 工件离场快照判定模式 (打螺丝漏打场景) ────
        # 开启后完全接管结算时机: 工件离场 → 取快照判 OK/NG; NG 可挂起待补.
        # 绕开下面 4/5/6/7 全部老结算路径 (cycle_max 在本分支内自带兜底).
        if cfg.get('judge_on_workpiece_leave', False):
            self._per_item_leave_mode_tick(boxes_by_label, current_time)
            return

        # ──── 3.95 换板兜底结算 (工件整体消失确认) ────
        # 修覆盖泄漏 bug: finish_label 没被检到时上一板永不结算, 覆盖态经位置匹配泄漏到新板.
        # 全部 item 标签连续消失 workpiece_absent_settle_frames 帧 → 工件已取走/换板 →
        # 按真实覆盖兜底结算 (OK/NG) + reset, 新板下一帧重新锁定从零开始.
        # 手拧单颗不会让整板全消失, 只在真正取走整板时才累计到阈值.
        absent_settle = cfg.get('workpiece_absent_settle_frames', 0)
        if not disable_auto_settle and absent_settle > 0:
            if self._per_item_any_item_present(boxes_by_label):
                sess.workpiece_absent_frames = 0
            else:
                sess.workpiece_absent_frames += 1
                if sess.workpiece_absent_frames >= absent_settle:
                    print(
                        f"[per_item] 工件整体消失 {sess.workpiece_absent_frames} 帧 "
                        f"(≥{absent_settle}), 判定已取走/换板, 兜底结算"
                    )
                    if debug_center.is_on("backend.per_item"):
                        debug_center.dbg("backend.per_item", "结算触发: 换板兜底", f"channel={self.channel_id} 全部工件标签连续消失{sess.workpiece_absent_frames}帧, 按真实覆盖结算避免泄漏到新板")
                    self._per_item_settle_cycle(current_time)
                    return

        # ──── 4. 完成即结算 (OK 路径, 无需收尾标签) ────
        # 所有 per_item 步骤都 completed → 保持 settle_after_all_done_sec 秒 → 立即结算 OK
        settle_after_all_done = cfg.get('settle_after_all_done_sec', 0)
        if not disable_auto_settle and settle_after_all_done > 0 and self._per_item_steps:
            all_done = all(s.completed for s in self._per_item_steps)
            if all_done:
                if sess.all_done_first_at is None:
                    sess.all_done_first_at = current_time
                elif (current_time - sess.all_done_first_at) >= settle_after_all_done:
                    print(f"[per_item] 所有步骤完成已保持 {settle_after_all_done:.1f}s, 立即结算 OK")
                    if debug_center.is_on("backend.per_item"):
                        debug_center.dbg("backend.per_item", "结算触发: 全部完成", f"channel={self.channel_id} 保持{settle_after_all_done:.1f}s后自动结算")
                    self._per_item_settle_cycle(current_time)
                    return
            else:
                sess.all_done_first_at = None

        # ──── 5. 周期超时强制结算 (NG 兜底, per_item 专属参数) ────
        cycle_max = cfg.get('cycle_max_duration_sec', 0)
        if not disable_auto_settle and cycle_max > 0 and sess.cycle_start_time is not None:
            cycle_elapsed = current_time - sess.cycle_start_time
            if cycle_elapsed > cycle_max:
                print(f"[per_item] 周期总时长超时 {cycle_elapsed:.1f}s > {cycle_max}s, 强制结算")
                if debug_center.is_on("backend.per_item"):
                    debug_center.dbg("backend.per_item", "结算触发: 周期超时", f"channel={self.channel_id} 已运行{cycle_elapsed:.1f}s > 上限{cycle_max}s, 未完成步骤将判 NG")
                self._per_item_settle_cycle(current_time)
                return

        # ──── 6. 空闲超时强制结算 (NG 兜底, per_item 专属参数) ────
        # 本场景核心兜底: 工人停手 → 持续 N 秒无 action 标签 → 强制结算
        # 注: 只看 action 标签, 不看 item, 见上面 last_activity_time 刷新规则
        idle_timeout = cfg.get('idle_timeout_sec', 0)
        if not disable_auto_settle and idle_timeout > 0 and sess.last_activity_time is not None:
            idle_elapsed = current_time - sess.last_activity_time
            if idle_elapsed > idle_timeout:
                print(f"[per_item] 空闲 {idle_elapsed:.1f}s > {idle_timeout}s, 强制结算")
                if debug_center.is_on("backend.per_item"):
                    debug_center.dbg("backend.per_item", "结算触发: 空闲超时", f"channel={self.channel_id} 无操作{idle_elapsed:.1f}s > 上限{idle_timeout}s, 未完成步骤将判 NG")
                self._per_item_settle_cycle(current_time)
                return

        # ──── 7. 收尾标签判定 (兼容原有路径, 配了 finish_label 仍然生效) ────
        # 注: box 尺寸过滤 (避免工件整体形态误识别为 finish_label) 已在 detection 出口
        # 完成 (source_detect_runners_mixin._passes_box_size_limit), 这里只看通过过滤
        # 后的 boxes_by_label 即可.
        finish_label = cfg.get('finish_label') or ''
        if not disable_auto_settle and finish_label and finish_label in boxes_by_label:
            # v3.12+ 工件离场互斥校验: 开关开启时, 同帧若画面里还有任何工件标签
            # (各 step.item_label) → 工件未离场 → 这一帧不算结算 (拿取手势误识别).
            workpiece_still_on_table_labels = []
            if cfg.get('finish_requires_no_items', False):
                seen = set()
                for step in self._per_item_steps:
                    for ilbl in step.item_label:
                        if ilbl in seen:
                            continue
                        seen.add(ilbl)
                        if ilbl in boxes_by_label and boxes_by_label.get(ilbl):
                            workpiece_still_on_table_labels.append(ilbl)

            if workpiece_still_on_table_labels:
                if sess.finish_label_consec_frames > 0:
                    print(
                        f"[per_item][DEBUG] finish_label='{finish_label}' 出现但桌面仍有工件 "
                        f"{workpiece_still_on_table_labels} → 工件未离场, 连续帧重置"
                    )
                sess.finish_label_consec_frames = 0
            else:
                sess.finish_label_consec_frames += 1
                dets_str = ", ".join(
                    f"conf={d['conf']:.3f} w={d['w']:.3f} h={d['h']:.3f}"
                    for d in finish_label_dets_this_frame
                )
                print(
                    f"[per_item][DEBUG] finish_label='{finish_label}' 出现 "
                    f"frame_id={sess.frame_id} 连续={sess.finish_label_consec_frames}/"
                    f"{cfg['finish_sustain_frames']} dets=[{dets_str}]"
                )
                if sess.finish_label_consec_frames >= cfg['finish_sustain_frames']:
                    print(f"[per_item][DEBUG] >>> finish_label 触发结算 <<<")
                    if debug_center.is_on("backend.per_item"):
                        debug_center.dbg("backend.per_item", "结算触发: 收尾标签", f"channel={self.channel_id} '{finish_label}' 连续{sess.finish_label_consec_frames}帧确认")
                    self._per_item_settle_cycle(current_time)
        else:
            if sess.finish_label_consec_frames > 0:
                print(
                    f"[per_item][DEBUG] finish_label 计数被打断 "
                    f"(连续 {sess.finish_label_consec_frames} 帧后断了)"
                )
            sess.finish_label_consec_frames = 0

    # ──── 调试: 周期不开始原因 (1s 节流, 答"为什么周期一直不开始") ────
    def _per_item_dbg_reject(self, current_time: float, reason: str):
        if not debug_center.is_on("backend.per_item"):
            return
        last = getattr(self, '_per_item_dbg_reject_ts', 0.0)
        if current_time - last < 1.0:
            return
        self._per_item_dbg_reject_ts = current_time
        debug_center.dbg("backend.per_item", "周期未开始", f"channel={self.channel_id} {reason}")

    # ──── 周期开始: 稳定窗口判定 ────
    def _per_item_try_start_cycle(self, boxes_by_label, current_time: float):
        """周期开始判定. 分两条路径:

        (A) 第一步配了 expected_count > 0 (固定数量模式, v3.9+):
            连续 K 帧检出数 ≥ expected_count - count_tolerance → 立刻锁定 + 进入周期.
            不要求"数量完全相同"、不要求"两两 IoU > 阈值" — 工件螺丝数已知, 抖动不影响.
        (B) 第一步未配 expected_count (auto 模式, 老路径):
            连续 stability_window 帧"数量完全恒定 + 两两 IoU > 阈值" → 进入周期.
        """
        if not self._per_item_steps:
            return
        cfg = self._per_item_config
        first_step = self._per_item_steps[0]

        # 多标签 OR: 把第一步所有 item_label 的 boxes 合并起来当触发集合
        boxes_now = self._collect_item_boxes(boxes_by_label, first_step.item_label)
        sess = self._per_item_session
        sess.stability_buffer.append(list(boxes_now))

        if len(sess.stability_buffer) < cfg['stability_window_frames']:
            return

        item_count = 0
        latest_boxes: list = []

        if first_step.expected_count > 0:
            # ──── 路径 A: 固定数量模式 ────
            target = first_step.expected_count
            require_exact = cfg.get('require_exact_count', False)
            window_frames = list(sess.stability_buffer)

            # v3.12+ 改造: 开周期阈值与 require_exact_count 解耦
            #   require_exact_count = True  → 所有步骤都要同时达标 (多步联合检查)
            #   require_exact_count = False → 仅首步要达标 (老宽松模式, 单步检查)
            # 但两种模式下"每步达标条件"都用 tolerance/ratio 折算 required, 不再要求严格 == expected.
            # 想严格 == expected? 把 tolerance=0 + ratio=1.0 即可.
            ratio = cfg.get('stability_count_ratio', 0.85)
            count_tol = cfg.get('stability_count_tolerance', 0)

            def _required_for(expected: int) -> int:
                """单步开周期阈值: ≥ max(1, expected - tolerance, expected × ratio)"""
                if expected <= 0:
                    return 0
                return max(1, min(expected, int(expected * ratio), expected - count_tol))

            first_required = _required_for(target)

            # 窗口里每帧首步检出数都要 ≥ first_required
            if any(len(fr) < first_required for fr in window_frames):
                _worst = min(len(fr) for fr in window_frames)
                self._per_item_dbg_reject(current_time, f"首步检出不足: 窗口最低{_worst}个 < 要求{first_required}个 (期望{target})")
                return

            if require_exact:
                # 严格等量: 同时刻次步检出也必须 ≥ 各自 required (保证触发瞬间所有步骤都满足阈值)
                for other_step in self._per_item_steps[1:]:
                    if other_step.expected_count > 0:
                        other_required = _required_for(other_step.expected_count)
                        other_now = self._collect_item_boxes(boxes_by_label, other_step.item_label)
                        if len(other_now) < other_required:
                            self._per_item_dbg_reject(current_time, f"严格等量未满足: 步骤[{other_step.step_label}]检出{len(other_now)}个 < 要求{other_required}个")
                            return

            # 锁定时挑窗口里"检出最多"的那一帧 (最接近真实数量), 截到 target 封顶
            best_fr = max(window_frames, key=lambda fr: len(fr))
            latest_boxes = list(best_fr)[:target]
            item_count = len(latest_boxes)
        else:
            # ──── 路径 B: auto 模式 (老路径) ────
            counts = [len(b) for b in sess.stability_buffer]
            if len(set(counts)) != 1:
                if max(counts) > 0:
                    self._per_item_dbg_reject(current_time, f"检出数量不恒定: 窗口内数量在{min(counts)}~{max(counts)}间跳动")
                return
            item_count = counts[0]
            if item_count <= 0:
                return
            # 位置稳定 (相邻帧 IoU > 阈值, 按 NN 匹配)
            prev = list(sess.stability_buffer[0])
            for fr in list(sess.stability_buffer)[1:]:
                if not self._per_item_frames_position_stable(prev, fr, cfg['stability_iou_threshold']):
                    self._per_item_dbg_reject(current_time, f"位置不稳定: 相邻帧 IoU 低于{cfg['stability_iou_threshold']} (画面抖动或目标在动)")
                    return
                prev = list(fr)
            # min_item_count 校验
            min_required = first_step.min_item_count
            if min_required != 'auto' and isinstance(min_required, int):
                if item_count < min_required:
                    self._per_item_dbg_reject(current_time, f"检出{item_count}个 < 最少要求{min_required}个")
                    return
            latest_boxes = list(sess.stability_buffer[-1])

        # ──── 进入周期 ────
        sess.cycle_active = True
        sess.cycle_start_time = current_time
        sess.cycle_start_frame_id = sess.frame_id
        sess.last_activity_time = current_time        # 周期开始即视为有活动
        sess.all_done_first_at = None
        # 补锁定窗口截止时刻 (v3.9+): 项目级 lock_lookahead_seconds (默认 5s)
        # 配 expected_count 后, 周期内此窗口期会持续吸收新位置
        lookahead = float(cfg.get('lock_lookahead_seconds', 5.0))
        sess.lock_lookahead_deadline = current_time + lookahead if lookahead > 0 else None
        try:
            self.cycle_start_time = current_time
            self.cycle_start_frame_pos = self._video_frame_pos()
        except Exception:
            pass

        # 锁定所有 per_item 步骤的个体表
        for step in self._per_item_steps:
            if step is first_step:
                step.lock_items_from_boxes(latest_boxes, sess.frame_id, current_time)
            else:
                # 不同步骤的 item_label 可能完全不同, 各自从本帧抽 boxes
                other_boxes = self._collect_item_boxes(boxes_by_label, step.item_label)
                # 若该步骤也配了 expected_count, 截顶
                if step.expected_count > 0:
                    other_boxes = other_boxes[:step.expected_count]
                step.lock_items_from_boxes(other_boxes, sess.frame_id, current_time)

        print(
            f"[per_item] 周期开始: 锁定首步个体数={item_count}, frame_id={sess.frame_id}, "
            f"路径={'expected_count' if first_step.expected_count > 0 else 'auto'}"
        )
        if debug_center.is_on("backend.per_item"):
            _locks = ", ".join(f"{s.step_label}={len(s.items)}/{s.expected_count if s.expected_count > 0 else 'auto'}" for s in self._per_item_steps)
            debug_center.dbg("backend.per_item", "周期开始", f"channel={self.channel_id} 锁定首步个体={item_count} 各步锁定[{_locks}] — 锁定数低于期望即埋下虚拟漏件NG")
        for s in self._per_item_steps:
            print(
                f"  · 步骤 [{s.step_label}]: 锁定={len(s.items)} "
                f"(expected={s.expected_count if s.expected_count > 0 else 'auto'})"
            )

    # ──── 多标签 OR 合并工具 ────
    @staticmethod
    def _collect_item_boxes(boxes_by_label: dict, labels) -> list:
        """把 step.item_label (tuple[str,...]) 涉及的所有 label 的 boxes 合并成单一列表.

        给"涂黑"这种"覆盖多种螺丝"的步骤用 (item_label=['5N螺丝','7N螺丝']).
        老配置 item_label=str 也兼容 (__init__ 已统一成 tuple).
        """
        out = []
        if isinstance(labels, str):
            labels = (labels,) if labels else ()
        for lbl in labels:
            out.extend(boxes_by_label.get(lbl, []) or [])
        return out

    # ──── 周期内补锁定 (v3.9+) ────
    @staticmethod
    def _per_item_absorb_new_items(step, item_boxes, frame_id: int, ts: float,
                                   unbounded: bool = False):
        """周期开始后 lookahead 窗口内, 用本帧 item_boxes 补充被遮挡漏锁的个体.

        策略: 本帧每个 box 与现有所有 items 计算 IoU, 都低于 item_tracking_iou
        视为"新位置" → 加入个体表 (covered=false), 直到达到 expected_count 上限.

        语义保证:
          - 已 covered 的个体永远不会被替换 (单调性, 见不变量 §一.1)
          - 不影响已锁定的 N 个个体, 仅追加缺失的
          - 一旦达到 expected_count 立即停止补锁

        unbounded=True (v3.56+, absorb_new_items_sec 自由吸收窗专用):
          不看 expected_count、无数量封顶 — auto 步骤在窗口内照单全收新位置.
        """
        if unbounded:
            room = len(item_boxes)
        else:
            if step.expected_count <= 0:
                return
            room = step.expected_count - len(step.items)
        if room <= 0:
            return
        added = 0
        for bbox in item_boxes:
            if added >= room:
                break
            # 与现有 items 计算最高 IoU
            max_iou = 0.0
            for st in step.items.values():
                iou = _bbox_iou(st.bbox, bbox)
                if iou > max_iou:
                    max_iou = iou
            if max_iou < step.item_tracking_iou:
                # 新位置 → 加入
                iid = step.next_item_id
                step.next_item_id += 1
                step.items[iid] = _PerItemItemState(iid, bbox, frame_id, ts)
                added += 1
        if added > 0:
            step.locked_count = len(step.items)
            print(
                f"[per_item] 步骤 [{step.step_label}] 补锁定 +{added} → "
                f"{len(step.items)}/{step.expected_count if step.expected_count > 0 else 'auto'}"
            )

    # ──── 位置稳定性辅助 ────
    @staticmethod
    def _per_item_frames_position_stable(prev_boxes, curr_boxes, iou_threshold: float) -> bool:
        """两帧 boxes 数量相同时, 每个 prev box 都能在 curr 里找到 IoU > 阈值 的对手"""
        if len(prev_boxes) != len(curr_boxes):
            return False
        used = [False] * len(curr_boxes)
        for pb in prev_boxes:
            best_idx = -1
            best_iou = iou_threshold
            for i, cb in enumerate(curr_boxes):
                if used[i]:
                    continue
                iou = _bbox_iou(pb, cb)
                if iou > best_iou:
                    best_iou = iou
                    best_idx = i
            if best_idx < 0:
                return False
            used[best_idx] = True
        return True

    # ──── 手动周期控制 (v3.10.2+) ────
    #
    # 设计原则: 手动控制只代替"时机判定", 不代替"结果判定".
    #   - manual_settle 等价于 finish_label 触发的那一刻, OK/NG 由 _per_item_settle_cycle
    #     按真实覆盖状态判 (覆盖全 → OK; 有缺 → NG, NG 详情含漏几件)
    #   - manual_force_start 等价于"画面稳定锁定"的那一刻, 后续覆盖 / 超时 / NG 全真实跑
    # ⚠ 不提供 "强制 OK / 强制 NG / 取消周期" 接口 — 不可人为伪造生产记录.
    def per_item_manual_settle(self) -> dict:
        """手动触发当前 per_item 周期结算 (等价 finish_label 那一刻).

        OK/NG 由系统按当前真实覆盖状态判定, 不接受外部指定.
        """
        sess = getattr(self, '_per_item_session', None)
        if sess is None:
            return {"ok": False, "msg": "未启用 per_item 模式"}
        if not sess.cycle_active:
            return {"ok": False, "msg": "当前没有 active 周期可结算"}

        self._per_item_settle_cycle(time.time())
        return {
            "ok": True,
            "msg": "已按当前覆盖状态触发结算 (OK/NG 由真实覆盖判定)",
        }

    def per_item_manual_force_start(self) -> dict:
        """强制立刻开启一个新周期 (即便 item/action 还没稳定出现).

        关键: 立刻从 mgr.current_detections 取当前帧 boxes 锁定 items,
        而不是开启一个空 items 的周期 (空 items 会让工人扭螺丝时 covered 永远 0).
        """
        sess = getattr(self, '_per_item_session', None)
        if sess is None:
            return {"ok": False, "msg": "未启用 per_item 模式"}
        if sess.cycle_active:
            return {"ok": False, "msg": "已存在 active 周期, 请先结算"}

        now = time.time()
        frame_id = getattr(self, 'inference_frame_count', 0) or 0

        # 从 mgr 最新检出快照, 按 label 分组重建 boxes_by_label
        boxes_by_label: dict[str, list] = {}
        locked_labels_count: dict[str, int] = {}
        try:
            dets = list(getattr(self, 'current_detections', None) or [])
            for d in dets:
                lbl = d.get('label')
                if not lbl:
                    continue
                try:
                    bbox = (float(d['x']), float(d['y']), float(d['w']), float(d['h']))
                except (KeyError, TypeError, ValueError):
                    continue
                boxes_by_label.setdefault(lbl, []).append(bbox)
        except Exception as e:
            print(f"[per_item][force_start] 读 current_detections 失败: {e}")

        sess.cycle_active = True
        sess.cycle_start_time = now
        sess.cycle_start_frame_id = frame_id
        sess.all_done_first_at = None
        sess.last_activity_time = now
        sess.finish_label_consec_frames = 0
        # force_start 关键: 强制开启补锁窗口, 不依赖单帧快照
        # 优先用 config.lock_lookahead_seconds, 配 0 时也强制 3 秒兜底.
        # 因为手动模式下 detection 在视频里是稀疏的, 按下那一瞬间常拿不到完整 boxes,
        # 给一个补锁窗口 → 让窗口内任何帧识别到的 item_label 都补锁进来.
        try:
            lookahead = float((self._per_item_config or {}).get('lock_lookahead_seconds', 0) or 0)
        except (TypeError, ValueError):
            lookahead = 0.0
        if lookahead <= 0:
            lookahead = 3.0
        sess.lock_lookahead_deadline = now + lookahead

        # 立刻锁定每个 step 的 items: 从 boxes_by_label 抽各自 item_label, 截到 expected_count
        for step in self._per_item_steps:
            step_boxes = self._collect_item_boxes(boxes_by_label, step.item_label)
            if step.expected_count > 0:
                step_boxes = step_boxes[:step.expected_count]
            step.lock_items_from_boxes(step_boxes, frame_id, now)
            step.completed = False
            locked_labels_count[step.step_label] = len(step.items)

        summary = ", ".join(
            f"[{lbl}] 锁 {n}" for lbl, n in locked_labels_count.items()
        ) or "(无 item 可锁)"
        print(f"[per_item][force_start] 周期开始, 立即锁定: {summary}")
        return {
            "ok": True,
            "msg": f"已手动开启新周期 ({summary})",
            "locked": locked_labels_count,
        }

    # ──── 周期结算 ────
    # ──── 结果计算 (settle / 离场判定 / 待补态共用) ────
    def _per_item_compute_result(self):
        """按当前所有步骤真实覆盖状态算结果.

        返回 (ok: bool, reason: str, ng_details: list[dict]).
        覆盖单调 (false→true 不可回滚), 所以任意时刻调用都是"截至当前的累积结果".
        """
        ok = True
        ng_reasons = []
        ng_details = []
        for step in self._per_item_steps:
            if not step.completed:
                ok = False
                missing = [iid for iid, st in step.items.items() if not st.covered]
                ng_reasons.append(
                    f"[{step.display_label}] 未完成({step.covered_count()}/{len(step.items)})"
                )
                ng_details.append({
                    'step_label': step.step_label,
                    'display_label': step.display_label,
                    'covered_count': step.covered_count(),
                    'total': len(step.items),
                    'missing_item_ids': missing,
                })
        reason = '; '.join(ng_reasons) or '逐件覆盖未完成'
        return ok, reason, ng_details

    # ──── PLC 完成脉冲 (外部设备 modbus_pulse) ────
    def _per_item_notify_plc_pulse(self, trigger_mode: str):
        """给绑定本工位的「Modbus 完成脉冲」外设排一次脉冲 (PLC 控气阀)。

        本方法跑在推理线程上, 所以只允许"入队"这一个动作: 外设服务把请求塞进设备
        线程的队列就返回, 真正的 Modbus 写在外设线程执行。PLC 断线/网络不通只落到
        该设备的 last_error, 不拖帧、不抛穿热路径 (整体再包一层 try 兜底)。

        trigger_mode: 'cycle_ok' (周期判 OK 落账) / 'all_covered' (首次全覆盖).
        只有外设自己配了同名 trigger_mode 才会响应, 没配外设时是纯 no-op。
        """
        try:
            from backend.services.external_device import get_external_device_service
            fired = get_external_device_service().notify_per_item_complete(
                self.channel_id, trigger_mode)
            if fired:
                print(f"[per_item] PLC 完成脉冲已排队 (模式={trigger_mode}, 设备数={fired})")
                if debug_center.is_on("backend.per_item"):
                    debug_center.dbg(
                        "backend.per_item", "PLC 完成脉冲",
                        f"channel={self.channel_id} 触发模式={trigger_mode} 排队设备数={fired}")
        except Exception as e:
            print(f"[per_item] PLC 完成脉冲下发失败 (模式={trigger_mode}): {e}")

    def _per_item_settle_cycle(self, current_time: float):
        """收尾标签稳定出现 → 检查所有 per_item 步骤完成情况 → OK/NG"""
        sess = self._per_item_session
        if not sess.cycle_active:
            return

        cycle_duration = current_time - (sess.cycle_start_time or current_time)
        ok, reason, ng_details = self._per_item_compute_result()
        # 按件计数要在 reset 前取数 (首个 per_item 步骤本周期已覆盖件数)
        settled_item_count = (
            self._per_item_steps[0].covered_count() if self._per_item_steps else 0
        )

        if ok:
            print(f"[per_item] 周期结算 OK: 步骤数={len(self._per_item_steps)}, 耗时{cycle_duration:.2f}s")
            if debug_center.is_on("backend.per_item"):
                debug_center.dbg("backend.per_item", "周期结算 OK", f"channel={self.channel_id} 步骤数={len(self._per_item_steps)} 耗时={cycle_duration:.2f}s 全部个体已覆盖")
            try:
                self._trigger_event(1, '逐件覆盖全部完成')
            except Exception as _e:
                print(f"[per_item] _trigger_event(1) 失败: {_e}")
            # v3.56+ 按件累计计数 (item_count_counter_name 非空才启用):
            # 周期判 OK 落账时把本周期件数累加进指定计数器 — 一周期 N 件的现场
            # 用它拿"累计件数"口径 (标准事件计数器只能按周期 +1).
            _cname = (getattr(self, '_per_item_config', None) or {}).get('item_count_counter_name') or ''
            if _cname and settled_item_count > 0:
                try:
                    if _cname in self.counters:
                        self.counters[_cname] += settled_item_count
                        print(f"[per_item] 按件计数: {_cname} += {settled_item_count} => {self.counters[_cname]}")
                        from backend.services.counter_daily import record_for_host
                        record_for_host(self, _cname, settled_item_count)
                        self._persist_counters()
                    else:
                        print(f"[per_item] 按件计数跳过: 计数器 '{_cname}' 未在 counters_config 定义")
                except Exception as _ce:
                    print(f"[per_item] 按件计数失败: {_ce}")
            # PLC 完成脉冲 (触发模式 B: cycle_ok) —— 只有判 OK 落账才发,
            # NG / 超时强制结算一律不发。所有 OK 结算路径都汇到这里, 一处挂接即全覆盖。
            self._per_item_notify_plc_pulse('cycle_ok')
            # OK 时清掉上次 NG 详情, 避免前端误以为还在 NG 状态
            self._per_item_last_ng_detail = None
        else:
            print(f"[per_item] 周期结算 NG: {reason}")
            if debug_center.is_on("backend.per_item"):
                _miss = sum(len(d.get('missing_item_ids') or []) for d in ng_details)
                debug_center.dbg("backend.per_item", "周期结算 NG", f"channel={self.channel_id} 原因={reason} 共漏{_miss}件 耗时={cycle_duration:.2f}s")
            # 结构化 NG 详情 (前端 PerItemPanel 用 reason_summary / missing_total / steps_failed 组合展示)
            missing_total = sum(len(d.get('missing_item_ids') or []) for d in ng_details)
            self._per_item_last_ng_detail = {
                'reason_summary': reason,
                'cycle_duration_sec': round(cycle_duration, 2),
                'settled_at': current_time,
                'missing_total': missing_total,
                'steps_failed': ng_details,
            }
            try:
                self._trigger_event(2, reason)
            except Exception as _e:
                print(f"[per_item] _trigger_event(2) 失败: {_e}")

        # ──── 重置周期 ────
        for step in self._per_item_steps:
            step.reset_for_new_cycle()
        sess.reset_after_cycle()
        # 清重复打警告 (新周期从零开始; 个体表 reset 已连带清各颗 dup 状态)
        self._per_item_last_warning = None
        self._per_item_last_dup_alarm_at = 0.0
        try:
            self.current_cycle_steps = []
            self.cycle_start_time = None
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════════
    # 工件离场快照判定 + 待补/待确认态 (打螺丝漏打场景)
    # ════════════════════════════════════════════════════════════════

    def _per_item_any_item_present(self, boxes_by_label: dict) -> bool:
        """本帧画面里是否还有任何工件标签 (各 step.item_label 之一)."""
        seen = set()
        for step in self._per_item_steps:
            for ilbl in step.item_label:
                if ilbl in seen:
                    continue
                seen.add(ilbl)
                if boxes_by_label.get(ilbl):
                    return True
        return False

    def _per_item_fire_remediation_alarm(self):
        """进待补态时触发 NG 报警响应 (只点灯, 不落账 — 落账等补打/人工确认).

        灯做成事件: 优先触发可配 remediation_event_id 对应事件的报警映射,
        不写死红灯 — 由用户在报警配置里决定该事件亮什么灯/响不响蜂鸣.
        remediation_event_id=0 时回退到标准 NG 事件 event2，避免现场必须等人工确认
        NG 落账后报警灯才响应。
        """
        cfg = self._per_item_config or {}
        eid = int(cfg.get('remediation_event_id', 0) or 0)
        if eid <= 0:
            eid = 2
        # v3.56+ remediation_event_notify=True: 借事件完整响应面 (灯 + Toast + 语音,
        # remind_only=True 不计数不定格不结周期). 默认 False = 老行为只点灯, 零差异.
        if cfg.get('remediation_event_notify', False):
            nd = getattr(self, '_per_item_last_ng_detail', None) or {}
            reason = nd.get('reason_summary') or '离场判定存在漏件, 请放回补做'
            try:
                self.fire_external_event_response(
                    eid, reason, source='per_item_remediation', remind_only=True)
                return
            except Exception as e:
                print(f"[per_item] 待补报警事件响应面触发失败, 回退只点灯: {e}")
        try:
            from backend.api.alarm import alarm_router
            alarm_router.trigger_alarm(f'event{eid}', channel_id=self.channel_id)
        except Exception as e:
            print(f"[per_item] 触发待补报警事件 event{eid} 失败: {e}")

    def _per_item_fire_duplicate_alarm(self, step, item_ids, current_time: float):
        """重复打同一颗螺丝: 复用 NG 事件 (event2) 点报警灯 + 置 PerItemPanel 黄条警告.

        只报警 + 提示, 不落账 / 不结束周期 / 不动覆盖状态 (覆盖单调). 物理报警按
        duplicate_alarm_interval_sec 节流, 避免连发; 前端警告横幅每次都刷新最新漏点.
        """
        cfg = self._per_item_config or {}
        ids_str = "/".join(f"#{i}" for i in item_ids)
        reason = f"[{step.display_label}] 重复打螺丝 {ids_str}"

        # 警告横幅 (前端 PerItemPanel 读 last_warning 显示黄条; 不落账 / 不弹 toast / 不播语音)
        self._per_item_last_warning = {
            'reason_summary': reason,
            'step_label': step.step_label,
            'display_label': step.display_label,
            'item_ids': list(item_ids),
            'ts': current_time,
        }
        print(f"[per_item] 重复打警告: {reason}")
        if debug_center.is_on("backend.per_item"):
            debug_center.dbg("backend.per_item", "重复打螺丝", f"channel={self.channel_id} {reason}")

        # 物理报警灯 (复用 NG event2), 按间隔节流
        interval = cfg.get('duplicate_alarm_interval_sec', 2.0)
        last = getattr(self, '_per_item_last_dup_alarm_at', 0.0) or 0.0
        if interval <= 0 or (current_time - last) >= interval:
            self._per_item_last_dup_alarm_at = current_time
            try:
                from backend.api.alarm import alarm_router
                alarm_router.trigger_alarm('event2', channel_id=self.channel_id)
            except Exception as e:
                print(f"[per_item] 触发重复打报警 event2 失败: {e}")

    def _per_item_fire_judge_ok_event(self):
        """判定层判合格时点"绿灯"——触发可配 judge_ok_event_id 对应事件的报警映射.

        与落账"合格"事件(_trigger_event(1)) 分开: 这里只是"判定时亮绿"预告,
        不带结算语义(不落账/不计数). 灯色由用户在报警配置里把该事件映射到绿灯.
        judge_ok_event_id=0 时不主动触发.
        """
        cfg = self._per_item_config or {}
        eid = int(cfg.get('judge_ok_event_id', 0) or 0)
        if eid <= 0:
            return
        try:
            from backend.api.alarm import alarm_router
            alarm_router.trigger_alarm(f'event{eid}', channel_id=self.channel_id)
        except Exception as e:
            print(f"[per_item] 触发判定合格事件 event{eid} 失败: {e}")

    def _per_item_judge_layer_tick(self, boxes_by_label: dict, current_time: float):
        """判定层: 在结算之前, 到"判定时机"就拍覆盖快照亮绿/红, 但不落账.

        判定时机与结算时机解耦 (judge_timing != 'on_settle' 时才被调用):
          - all_done: 全部覆盖完(可+judge_all_done_sec确认) → 判合格亮绿(judge_ok_event), 保持等结算.
                      (漏件时此触发不亮 → 漏件红灯仍由结算那刻判 NG / 待补给出)
          - label   : judge_label 连续出现 judge_label_frames 帧 → 拍快照:
                      全覆盖→亮绿; 有漏→亮红+暴露漏点(复用 ng_hold 待补), 补满→翻绿.
          - manual  : 不自动触发, 只由检测主页"手动判定"按钮 (per_item_judge_now) 触发;
                      本 tick 仅维持"补满翻绿"状态机.
        落账 (计数/进下一轮) 仍由下面的结算时机 (离场/收尾/全完成) 负责.
        """
        sess = self._per_item_session
        cfg = self._per_item_config
        timing = cfg.get('judge_timing', 'on_settle')

        # 已判合格(绿灯保持): 覆盖单调不会回退, 不重复判
        if sess.judged and sess.judged_ok:
            return

        # 红灯待补中: 工人补满 → 翻绿 (不落账, 等结算)
        if sess.judged and not sess.judged_ok:
            if self._per_item_steps and all(s.completed for s in self._per_item_steps):
                sess.judged_ok = True
                sess.awaiting_remediation = False
                self._per_item_last_ng_detail = None
                self._per_item_fire_judge_ok_event()
                print("[per_item] 判定层: 漏件补满, 翻绿(等取走落账)")
                if debug_center.is_on("backend.per_item"):
                    debug_center.dbg("backend.per_item", "判定层翻绿", f"channel={self.channel_id} 漏件补满, 亮绿等结算")
            return

        # 尚未判定 → 检查自动判定触发条件 (manual 模式不在此触发, 只走手动按钮)
        triggered = False
        if timing == 'all_done':
            if self._per_item_steps and all(s.completed for s in self._per_item_steps):
                if sess.judge_done_first_at is None:
                    sess.judge_done_first_at = current_time
                elif (current_time - sess.judge_done_first_at) >= cfg.get('judge_all_done_sec', 0.0):
                    triggered = True
            else:
                sess.judge_done_first_at = None
        elif timing == 'label':
            jl = cfg.get('judge_label') or ''
            if jl and boxes_by_label.get(jl):
                sess.judge_label_consec += 1
                if sess.judge_label_consec >= cfg.get('judge_label_frames', 3):
                    triggered = True
            else:
                sess.judge_label_consec = 0

        if triggered:
            self._per_item_perform_judge(current_time)

    def _per_item_perform_judge(self, current_time: float):
        """拍覆盖快照判定: 全覆盖→亮绿(judge_ok_event); 有漏→亮红+暴露漏点(可挂起待补).

        判定层自动触发与"手动判定"按钮共用本方法 — 只代替"判定时机", 不代替"结果"
        (结果永远按真实覆盖快照算, 不伪造). 不落账.
        """
        sess = self._per_item_session
        cfg = self._per_item_config
        ok, reason, ng_details = self._per_item_compute_result()
        sess.judged = True
        sess.judged_ok = ok
        if ok:
            self._per_item_last_ng_detail = None
            self._per_item_fire_judge_ok_event()
            print("[per_item] 判定层: 判合格, 亮绿等取走结算")
            if debug_center.is_on("backend.per_item"):
                debug_center.dbg("backend.per_item", "判定层判合格", f"channel={self.channel_id} 亮绿等结算")
        else:
            missing_total = sum(len(d.get('missing_item_ids') or []) for d in ng_details)
            cycle_duration = current_time - (sess.cycle_start_time or current_time)
            self._per_item_last_ng_detail = {
                'reason_summary': reason,
                'cycle_duration_sec': round(cycle_duration, 2),
                'settled_at': current_time,
                'missing_total': missing_total,
                'steps_failed': ng_details,
                'awaiting_remediation': bool(cfg.get('ng_hold_for_remediation', False)),
            }
            if cfg.get('ng_hold_for_remediation', False):
                sess.awaiting_remediation = True
                sess.await_since = current_time
                sess.last_remediation_alarm_at = current_time
            self._per_item_fire_remediation_alarm()
            print(f"[per_item] 判定层: 判 NG, 亮红提示漏{missing_total}件 (等补打/结算)")
            if debug_center.is_on("backend.per_item"):
                debug_center.dbg("backend.per_item", "判定层判NG", f"channel={self.channel_id} {reason} 漏{missing_total}件, 亮红")

    def per_item_judge_now(self) -> dict:
        """手动判定: 检测主页"手动判定"按钮触发, 立刻拍覆盖快照亮绿/红 (不落账).

        设计同 manual_settle: 只代替"判定时机", 不代替"结果". 结果按真实覆盖算.
        重复点 (已判过) 不重复触发; 已判合格保持绿; 待补中点等同"再看一眼"(补满则翻绿).
        """
        sess = getattr(self, '_per_item_session', None)
        if sess is None:
            return {"ok": False, "msg": "未启用 per_item 模式"}
        if not sess.cycle_active:
            return {"ok": False, "msg": "当前没有 active 周期可判定"}
        if sess.judged and sess.judged_ok:
            return {"ok": True, "msg": "本周期已判合格(绿灯保持)"}
        # 待补中再点: 走补满翻绿检查 (不强制改结果)
        if sess.judged and not sess.judged_ok:
            if self._per_item_steps and all(s.completed for s in self._per_item_steps):
                sess.judged_ok = True
                sess.awaiting_remediation = False
                self._per_item_last_ng_detail = None
                self._per_item_fire_judge_ok_event()
                return {"ok": True, "msg": "漏件已补满, 翻绿(等取走结算)"}
            return {"ok": True, "msg": "仍有漏件未补满, 维持红灯待补"}
        self._per_item_perform_judge(time.time())
        return {
            "ok": True,
            "msg": "已手动判定 (OK/NG 由真实覆盖判定; 落账仍由结算时机触发)",
            "judged_ok": sess.judged_ok,
        }

    def _per_item_leave_mode_tick(self, boxes_by_label: dict, current_time: float):
        """工件离场判定模式每帧 tick. 接管全部结算时机.

        running 态: 工件标签持续消失 leave_confirm_frames 帧 → 取快照判定.
        await 态  : section 3 已重算覆盖, 补满则撤红转 OK; 超时则按 NG 落账;
                    人工确认走 per_item_confirm_ng.
        """
        sess = self._per_item_session
        cfg = self._per_item_config
        any_item = self._per_item_any_item_present(boxes_by_label)

        finish_label = cfg.get('finish_label') or ''
        finish_present = bool(finish_label and boxes_by_label.get(finish_label))

        # ── 待补/待确认态 ──
        if sess.awaiting_remediation:
            # 补满 (工件放回后补打 → 覆盖单调翻 true) → 撤红转 OK
            if self._per_item_steps and all(s.completed for s in self._per_item_steps):
                print("[per_item] 待补态补满, 撤红转 OK")
                if debug_center.is_on("backend.per_item"):
                    debug_center.dbg("backend.per_item", "待补完成", f"channel={self.channel_id} 漏件已补满, 撤红转 OK")
                self._per_item_settle_cycle(current_time)   # 全完成 → 判 OK + reset
                return
            # 红灯内取件算 NG (可选): 待补期间再次出现拿取结算动作却未补满 → 自动 NG 落账
            if cfg.get('remediation_takeaway_ng', False) and finish_present:
                print("[per_item] 待补态再次拿取且未补满, 按 NG 落账 (红灯内取件算 NG)")
                if debug_center.is_on("backend.per_item"):
                    debug_center.dbg("backend.per_item", "待补取件NG", f"channel={self.channel_id} 红灯内再次拿取未补满, 按 NG 落账")
                self._per_item_settle_cycle(current_time)
                return
            # 超时按 NG 落账 (可选)
            rem_timeout = cfg.get('remediation_timeout_sec', 0)
            if rem_timeout > 0 and sess.await_since is not None and (current_time - sess.await_since) > rem_timeout:
                print(f"[per_item] 待补态超时 {rem_timeout}s, 按 NG 落账")
                if debug_center.is_on("backend.per_item"):
                    debug_center.dbg("backend.per_item", "待补超时", f"channel={self.channel_id} 超时{rem_timeout}s 未补满, 按 NG 落账")
                self._per_item_settle_cycle(current_time)
                return
            # 持续报警 (可选): 'sustained' 模式按间隔重复触发待补报警事件, 直到补满/确认
            if cfg.get('remediation_alarm_mode', 'once') == 'sustained':
                interval = cfg.get('remediation_alarm_interval_sec', 2.0)
                last = sess.last_remediation_alarm_at
                if last is None or (current_time - last) >= interval:
                    self._per_item_fire_remediation_alarm()
                    sess.last_remediation_alarm_at = current_time
            return

        # ── running 态: 检测工件离场 (可选叠加"拿取结算动作"双条件) ──
        # 双条件复用 finish_requires_no_items 开关 (与"收尾标签结算"路径同一个开关):
        #   开启且配了 finish_label 时, 要求本周期出现过拿取结算动作 + 工件离场, 缺一不算离场.
        #   防"手大面积遮挡/工件被短暂移动 → 螺丝瞬间全消失"被误判为离场.
        # 本周期内出现过拿取结算动作 → latch (拿取动作可能早于工件完全离场几帧).
        if finish_present:
            sess.leave_finish_seen = True

        leave_gate = (not any_item)
        if cfg.get('finish_requires_no_items', False) and finish_label:
            leave_gate = leave_gate and sess.leave_finish_seen

        if leave_gate:
            sess.leave_consec_frames += 1
            if sess.leave_consec_frames >= cfg.get('leave_confirm_frames', 10):
                self._per_item_judge_on_leave(current_time)
                return
        else:
            sess.leave_consec_frames = 0

        # 周期超时兜底 (离场模式下仍保留, 防工件长期不离场卡死)
        cycle_max = cfg.get('cycle_max_duration_sec', 0)
        if cycle_max > 0 and sess.cycle_start_time is not None and (current_time - sess.cycle_start_time) > cycle_max:
            print(f"[per_item] (离场模式) 周期超时 {cycle_max}s, 强制判定")
            if debug_center.is_on("backend.per_item"):
                debug_center.dbg("backend.per_item", "离场模式周期超时", f"channel={self.channel_id} 超时{cycle_max}s 强制判定")
            self._per_item_judge_on_leave(current_time)

    def _per_item_judge_on_leave(self, current_time: float):
        """工件离场瞬间取覆盖快照判定. 全覆盖→OK; 有漏→NG (可挂起待补)."""
        sess = self._per_item_session
        cfg = self._per_item_config
        ok, reason, ng_details = self._per_item_compute_result()

        if ok:
            print(f"[per_item] 工件离场, 快照判 OK")
            if debug_center.is_on("backend.per_item"):
                debug_center.dbg("backend.per_item", "离场判定 OK", f"channel={self.channel_id} 全部覆盖")
            self._per_item_settle_cycle(current_time)       # 判 OK + reset
            return

        # 有漏
        if cfg.get('ng_hold_for_remediation', False):
            # 进待补/待确认态: 不落账, 只点红灯 + 暴露漏点给前端
            cycle_duration = current_time - (sess.cycle_start_time or current_time)
            missing_total = sum(len(d.get('missing_item_ids') or []) for d in ng_details)
            sess.awaiting_remediation = True
            sess.await_since = current_time
            sess.last_remediation_alarm_at = current_time   # 持续报警节流起点 (进待补已响一次)
            self._per_item_last_ng_detail = {
                'reason_summary': reason,
                'cycle_duration_sec': round(cycle_duration, 2),
                'settled_at': current_time,
                'missing_total': missing_total,
                'steps_failed': ng_details,
                'awaiting_remediation': True,
            }
            self._per_item_fire_remediation_alarm()
            print(f"[per_item] 工件离场判 NG, 进入待补态: {reason} (漏{missing_total}件)")
            if debug_center.is_on("backend.per_item"):
                debug_center.dbg("backend.per_item", "离场判定 NG 待补", f"channel={self.channel_id} {reason} 漏{missing_total}件, 红灯提示等补打/确认")
        else:
            # 不挂起 → 直接 NG 落账
            print(f"[per_item] 工件离场, 快照判 NG (不挂起): {reason}")
            self._per_item_settle_cycle(current_time)

    def per_item_confirm_ng(self) -> dict:
        """人工确认当前待补态为 NG, 按真实覆盖状态落账 (不伪造结果).

        设计同 manual_settle: 只代替"时机", 不代替"结果". 若此刻工人其实已补满,
        则落账为 OK (不强制 NG).
        """
        sess = getattr(self, '_per_item_session', None)
        if sess is None:
            return {"ok": False, "msg": "未启用 per_item 模式"}
        if not getattr(sess, 'awaiting_remediation', False):
            return {"ok": False, "msg": "当前不在待补/待确认状态"}
        self._per_item_settle_cycle(time.time())
        return {"ok": True, "msg": "已确认并按真实覆盖状态落账"}

    def _per_item_reset_runtime(self):
        """整体复位 per_item 运行时状态 (用户点「清零」时调用).

        清: 逐颗覆盖 / 周期态 / 待补态(awaiting_remediation) / 上次 NG 详情.
        不清: 项目配置 (_per_item_config) 与步骤定义 (步骤的 item_label/期望数等保留,
              只把本周期的覆盖与个体表清空). 非 per_item 项目静默跳过.
        """
        steps = getattr(self, '_per_item_steps', None)
        if not steps:
            return
        for step in steps:
            try:
                step.reset_for_new_cycle()
            except Exception:
                pass
        sess = getattr(self, '_per_item_session', None)
        if sess is not None:
            try:
                sess.reset_after_cycle()
            except Exception:
                pass
        self._per_item_last_ng_detail = None
        self._per_item_last_warning = None
        self._per_item_last_dup_alarm_at = 0.0

    # ──── 给 detection/results 用的 state 快照 ────
    def get_per_item_state(self) -> Optional[dict]:
        """返回当前 per_item 模式的运行时状态. 非 per_item 模式返回 None.
        前端 Monitor 用这个画"哪几个个体没被覆盖到".
        """
        if not getattr(self, '_per_item_config', None):
            return None
        sess = getattr(self, '_per_item_session', None)
        cfg = self._per_item_config

        # v3.10.2+ strict_display 控制 step.display_total 的语义
        strict_display = bool(cfg.get('require_exact_count', False))
        steps_state = [s.to_state_dict(strict_display=strict_display) for s in self._per_item_steps]

        return {
            'enabled': True,
            'cycle_active': bool(sess.cycle_active) if sess else False,
            'awaiting_remediation': bool(getattr(sess, 'awaiting_remediation', False)) if sess else False,
            'judged': bool(getattr(sess, 'judged', False)) if sess else False,
            'judged_ok': bool(getattr(sess, 'judged_ok', False)) if sess else False,
            'cycle_start_time': sess.cycle_start_time if sess else None,
            'frame_id': sess.frame_id if sess else 0,
            'config': {
                'stability_window_frames': cfg['stability_window_frames'],
                'stability_iou_threshold': cfg.get('stability_iou_threshold', 0.5),
                'stability_count_tolerance': cfg.get('stability_count_tolerance', 0),
                'stability_count_ratio': cfg.get('stability_count_ratio', 0.85),
                'require_exact_count': cfg.get('require_exact_count', False),
                'disable_auto_settle': cfg.get('disable_auto_settle', False),
                'item_timeout_seconds': cfg['item_timeout_seconds'],
                'lock_count_on_start': cfg['lock_count_on_start'],
                'finish_label': cfg['finish_label'],
                'finish_sustain_frames': cfg.get('finish_sustain_frames', 3),
                'settle_after_all_done_sec': cfg.get('settle_after_all_done_sec', 0.0),
                'lock_lookahead_seconds': cfg.get('lock_lookahead_seconds', 5.0),
                # per_item 专属超时 (与其他模式隔离)
                'cycle_max_duration_sec': cfg.get('cycle_max_duration_sec', 0.0),
                'idle_timeout_sec': cfg.get('idle_timeout_sec', 0.0),
                # 工件离场快照判定 + 待补态
                'judge_on_workpiece_leave': cfg.get('judge_on_workpiece_leave', False),
                'leave_confirm_frames': cfg.get('leave_confirm_frames', 10),
                'ng_hold_for_remediation': cfg.get('ng_hold_for_remediation', False),
                'remediation_timeout_sec': cfg.get('remediation_timeout_sec', 0.0),
                'remediation_event_id': cfg.get('remediation_event_id', 0),
                'remediation_takeaway_ng': cfg.get('remediation_takeaway_ng', False),
                'remediation_alarm_mode': cfg.get('remediation_alarm_mode', 'once'),
                'remediation_alarm_interval_sec': cfg.get('remediation_alarm_interval_sec', 2.0),
                'color_by_coverage': cfg.get('color_by_coverage', False),
                'box_color_covered': cfg.get('box_color_covered', ''),
                'box_color_uncovered': cfg.get('box_color_uncovered', ''),
                'show_item_numbers': cfg.get('show_item_numbers', False),
                'judge_timing': cfg.get('judge_timing', 'on_settle'),
                # 重复打同一颗螺丝防护
                'duplicate_screw_alarm': cfg.get('duplicate_screw_alarm', False),
                'duplicate_sustain_frames': cfg.get('duplicate_sustain_frames', 2),
                'duplicate_release_frames': cfg.get('duplicate_release_frames', 8),
                'duplicate_alarm_interval_sec': cfg.get('duplicate_alarm_interval_sec', 2.0),
                'duplicate_warning_display_sec': cfg.get('duplicate_warning_display_sec', 3.0),
                # 换板兜底结算 (工件整体消失确认)
                'workpiece_absent_settle_frames': cfg.get('workpiece_absent_settle_frames', 0),
                # v3.56+ 自由吸收窗 / 按件计数 / 待补事件通知
                'absorb_new_items_sec': cfg.get('absorb_new_items_sec', 0.0),
                'item_count_counter_name': cfg.get('item_count_counter_name', ''),
                'remediation_event_notify': cfg.get('remediation_event_notify', False),
            },
            'steps': steps_state,
            'last_ng_detail': getattr(self, '_per_item_last_ng_detail', None),
            'last_warning': getattr(self, '_per_item_last_warning', None),
        }
