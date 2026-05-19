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
    }

步骤级 steps_config[i] (per_item 步骤新增字段):
    per_item: {
        item_label: "螺丝",                    # 个体识别标签
        action_label: "打螺丝",                # 工序覆盖标签
        item_tracking_iou: 0.3,                # 个体跨帧 IoU 阈值
        coverage_iou: 0.3,                     # 工序与个体的覆盖 IoU 阈值
        sustain_frames: 5,                     # 持续 N 帧重叠才算覆盖
        completion: "all_covered",             # 完成判定 (本版仅实现 all_covered)
        min_item_count: "auto",                # 最低个体数 ("auto" 或固定数字)
    }

==================== 兼容性 ====================
- 数据库 schema 不动
- 老项目一行配置不动, 新模式不启用任何新代码路径
- step_counts / counters 仍正常累加 (per_item 步骤每完成一次 +1, 等价于
  现有 sequential 步骤完成行为)
- _trigger_event(1, '...') / _trigger_event(2, '...') 直接复用作 OK/NG
"""
from __future__ import annotations

import time
from collections import deque
from typing import Optional


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


# ==================== 个体状态 ====================
class _PerItemItemState:
    """单个个体在某一步骤里的状态"""
    __slots__ = (
        'item_id', 'bbox', 'last_seen_frame', 'last_seen_time',
        'covered', 'consecutive_overlap_frames', 'first_covered_at',
    )

    def __init__(self, item_id: int, bbox, frame_id: int, ts: float):
        self.item_id = item_id
        self.bbox = bbox                       # (x, y, w, h) 归一化
        self.last_seen_frame = frame_id
        self.last_seen_time = ts
        self.covered = False
        self.consecutive_overlap_frames = 0
        self.first_covered_at: Optional[float] = None

    def to_dict(self):
        return {
            'id': self.item_id,
            'bbox': list(self.bbox),
            'covered': self.covered,
            'covered_at': self.first_covered_at,
        }


# ==================== 单步骤状态 ====================
class _PerItemStep:
    """单个 per_item 步骤的运行时状态"""
    __slots__ = (
        'step_id', 'step_label', 'display_label',
        'item_label', 'action_label',
        'item_tracking_iou', 'coverage_iou', 'sustain_frames',
        'completion', 'min_item_count',
        'items', 'next_item_id', 'locked_count', 'completed',
        'completed_count_in_session',
    )

    def __init__(self, raw_step: dict):
        per = raw_step.get('per_item') or {}
        self.step_id = raw_step.get('id')
        self.step_label = raw_step.get('label', '')
        self.display_label = raw_step.get('displayLabel') or raw_step.get('display_name') or self.step_label

        # ── 必填: item_label / action_label ──
        self.item_label = per.get('item_label', '')
        self.action_label = per.get('action_label', '')

        # ── 阈值与帧数 ──
        self.item_tracking_iou = float(per.get('item_tracking_iou', 0.3))
        self.coverage_iou = float(per.get('coverage_iou', 0.3))
        self.sustain_frames = int(per.get('sustain_frames', 5))

        self.completion = per.get('completion', 'all_covered')
        self.min_item_count = per.get('min_item_count', 'auto')

        # ── 运行时状态 ──
        self.items: dict[int, _PerItemItemState] = {}
        self.next_item_id = 1
        self.locked_count = 0
        self.completed = False
        self.completed_count_in_session = 0      # 本 session 内完成次数

    # ──── 锁定个体表 ────
    def lock_items_from_boxes(self, boxes, frame_id: int, ts: float):
        """周期开始时一次性锁定个体表"""
        self.items.clear()
        self.next_item_id = 1
        for bbox in boxes:
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

    # ──── 更新个体位置(跨帧匹配) ────
    def update_item_positions(self, boxes, frame_id: int, ts: float, lock_count_on_start: bool):
        """用本帧 item_label box 更新个体表里的位置.

        匹配规则: 每个新 box 找已有个体里 IoU 最高的, 超过 item_tracking_iou
        阈值则更新该个体位置. 锁定模式下不创建新个体; dynamic 模式下创建.
        """
        if not boxes:
            return
        used_ids = set()
        for bbox in boxes:
            best_iid = None
            best_iou = self.item_tracking_iou
            for iid, st in self.items.items():
                if iid in used_ids:
                    continue
                iou = _bbox_iou(st.bbox, bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_iid = iid
            if best_iid is not None:
                self.items[best_iid].bbox = bbox
                self.items[best_iid].last_seen_frame = frame_id
                self.items[best_iid].last_seen_time = ts
                used_ids.add(best_iid)
            elif not lock_count_on_start:
                # dynamic 模式: 没匹配上 → 创建新个体
                iid = self.next_item_id
                self.next_item_id += 1
                self.items[iid] = _PerItemItemState(iid, bbox, frame_id, ts)

    # ──── 应用工序覆盖 ────
    def apply_coverage(self, action_boxes, frame_id: int, ts: float):
        """用本帧 action_label box 推进个体覆盖状态.

        对每个 action box, 找 IoU 最高的个体. IoU > 阈值 → 该个体的
        连续重叠帧数 +1; 反之归零. 累积达到 sustain_frames 那一刻翻转
        covered=true.
        """
        # 1. 标记本帧哪些个体被某个 action box 覆盖到
        overlapping_ids = set()
        for abox in action_boxes:
            best_iid = None
            best_iou = self.coverage_iou
            for iid, st in self.items.items():
                iou = _bbox_iou(st.bbox, abox)
                if iou > best_iou:
                    best_iou = iou
                    best_iid = iid
            if best_iid is not None:
                overlapping_ids.add(best_iid)

        # 2. 推进/归零各个体的连续重叠帧数
        for iid, st in self.items.items():
            if iid in overlapping_ids:
                st.consecutive_overlap_frames += 1
                if (not st.covered) and st.consecutive_overlap_frames >= self.sustain_frames:
                    st.covered = True
                    st.first_covered_at = ts
            else:
                st.consecutive_overlap_frames = 0

    # ──── 个体超时清理 ────
    def cleanup_stale_items(self, ts: float, timeout_sec: float, lock_count_on_start: bool):
        """清理超时未出现的个体.

        锁定模式: 已覆盖个体永远不清理(语义已完成); 未覆盖个体超时才清.
        dynamic 模式: 一律按超时清.
        """
        if timeout_sec <= 0:
            return
        stale = []
        for iid, st in self.items.items():
            if (ts - st.last_seen_time) > timeout_sec:
                if lock_count_on_start and st.covered:
                    continue
                stale.append(iid)
        for iid in stale:
            self.items.pop(iid, None)

    # ──── 步骤完成判定 ────
    def check_completion(self) -> bool:
        if self.completion == 'all_covered':
            if not self.items:
                return False
            for st in self.items.values():
                if not st.covered:
                    return False
            return True
        # 未实现的完成类型: 兜底为 False
        return False

    def covered_count(self) -> int:
        return sum(1 for s in self.items.values() if s.covered)

    def to_state_dict(self):
        return {
            'step_id': self.step_id,
            'label': self.step_label,
            'display_label': self.display_label,
            'item_label': self.item_label,
            'action_label': self.action_label,
            'total': len(self.items),
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
    )

    def __init__(self):
        self.cycle_active = False
        self.cycle_start_time: Optional[float] = None
        self.cycle_start_frame_id: Optional[int] = None
        self.stability_buffer = deque(maxlen=10)        # 真值: 窗口帧数, 配置时改长度
        self.finish_label_seen_at: Optional[float] = None
        self.finish_label_consec_frames = 0
        self.frame_id = 0

    def reset_after_cycle(self):
        self.cycle_active = False
        self.cycle_start_time = None
        self.cycle_start_frame_id = None
        self.stability_buffer.clear()
        self.finish_label_seen_at = None
        self.finish_label_consec_frames = 0


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
        item_timeout = float(per_item_cfg.get('item_timeout_seconds', 3.0))
        lock_on_start = bool(per_item_cfg.get('lock_count_on_start', True))
        finish_label = per_item_cfg.get('finish_label', '')
        finish_sustain = int(per_item_cfg.get('finish_sustain_frames', 3))

        self._per_item_config = {
            'stability_window_frames': max(1, stability_window),
            'stability_iou_threshold': stability_iou,
            'item_timeout_seconds': max(0.0, item_timeout),
            'lock_count_on_start': lock_on_start,
            'finish_label': finish_label,
            'finish_sustain_frames': max(1, finish_sustain),
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
            print(
                f"  · 步骤 [{s.step_label}]: item='{s.item_label}', action='{s.action_label}', "
                f"sustain={s.sustain_frames}帧, coverage_iou={s.coverage_iou}, min={s.min_item_count}"
            )
        return True

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

        # ──── 1. 按 label 分组本帧检测 ────
        boxes_by_label: dict[str, list] = {}
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

        # ──── 2. 周期未开始: 尝试启动 ────
        if not sess.cycle_active:
            self._per_item_try_start_cycle(boxes_by_label, current_time)
            return

        # ──── 3. 周期内: 更新每个 per_item 步骤 ────
        for step in self._per_item_steps:
            # 3a. 更新个体位置 (按 item_label)
            item_boxes = boxes_by_label.get(step.item_label, [])
            if item_boxes:
                step.update_item_positions(
                    item_boxes, sess.frame_id, current_time,
                    cfg['lock_count_on_start'],
                )
            # 3b. 应用工序覆盖 (按 action_label)
            action_boxes = boxes_by_label.get(step.action_label, [])
            step.apply_coverage(action_boxes, sess.frame_id, current_time)
            # 3c. 超时清理
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

        # ──── 4. 收尾标签判定 ────
        finish_label = cfg.get('finish_label') or ''
        if finish_label and finish_label in boxes_by_label:
            sess.finish_label_consec_frames += 1
            if sess.finish_label_consec_frames >= cfg['finish_sustain_frames']:
                self._per_item_settle_cycle(current_time)
        else:
            sess.finish_label_consec_frames = 0

    # ──── 周期开始: 稳定窗口判定 ────
    def _per_item_try_start_cycle(self, boxes_by_label, current_time: float):
        """连续 stability_window_frames 帧都满足: 第一步 item_label 数量 + 位置稳定 → 周期开始"""
        if not self._per_item_steps:
            return
        cfg = self._per_item_config
        first_step = self._per_item_steps[0]
        trigger_label = first_step.item_label

        boxes_now = boxes_by_label.get(trigger_label, [])
        sess = self._per_item_session
        sess.stability_buffer.append(list(boxes_now))

        if len(sess.stability_buffer) < cfg['stability_window_frames']:
            return

        # ──── 稳定性校验 ────
        # 1. 数量恒定
        counts = [len(b) for b in sess.stability_buffer]
        if len(set(counts)) != 1:
            return
        item_count = counts[0]
        if item_count <= 0:
            return

        # 2. 位置稳定 (相邻帧 IoU > 阈值, 按 NN 匹配)
        prev = list(sess.stability_buffer[0])
        for fr in list(sess.stability_buffer)[1:]:
            if not self._per_item_frames_position_stable(prev, fr, cfg['stability_iou_threshold']):
                return
            prev = list(fr)

        # 3. min_item_count 校验
        min_required = first_step.min_item_count
        if min_required != 'auto' and isinstance(min_required, int):
            if item_count < min_required:
                # 数量太少, 不算稳定也不进周期
                return

        # ──── 进入周期 ────
        sess.cycle_active = True
        sess.cycle_start_time = current_time
        sess.cycle_start_frame_id = sess.frame_id
        try:
            self.cycle_start_time = current_time      # 兼容现有 current_cycle_time 输出
        except Exception:
            pass

        # 锁定所有 per_item 步骤的个体表 (用最新帧的 boxes)
        latest_boxes = list(sess.stability_buffer[-1])
        for step in self._per_item_steps:
            if step.item_label == trigger_label:
                step.lock_items_from_boxes(latest_boxes, sess.frame_id, current_time)
            else:
                # 不同 item_label 的步骤: 用本帧实际识别的 boxes 锁定
                other_boxes = boxes_by_label.get(step.item_label, [])
                step.lock_items_from_boxes(other_boxes, sess.frame_id, current_time)

        print(
            f"[per_item] 周期开始: 触发标签='{trigger_label}', 锁定个体数={item_count}, "
            f"frame_id={sess.frame_id}"
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

    # ──── 周期结算 ────
    def _per_item_settle_cycle(self, current_time: float):
        """收尾标签稳定出现 → 检查所有 per_item 步骤完成情况 → OK/NG"""
        sess = self._per_item_session
        if not sess.cycle_active:
            return

        cycle_duration = current_time - (sess.cycle_start_time or current_time)

        ok = True
        ng_reasons = []
        ng_details = []      # 给前端的"未覆盖个体清单"
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

        if ok:
            print(f"[per_item] 周期结算 OK: 步骤数={len(self._per_item_steps)}, 耗时{cycle_duration:.2f}s")
            try:
                self._trigger_event(1, '逐件覆盖全部完成')
            except Exception as _e:
                print(f"[per_item] _trigger_event(1) 失败: {_e}")
            # OK 时清掉上次 NG 详情, 避免前端误以为还在 NG 状态
            self._per_item_last_ng_detail = None
        else:
            reason = '; '.join(ng_reasons) or '逐件覆盖未完成'
            print(f"[per_item] 周期结算 NG: {reason}")
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
        try:
            self.current_cycle_steps = []
            self.cycle_start_time = None
        except Exception:
            pass

    # ──── 给 detection/results 用的 state 快照 ────
    def get_per_item_state(self) -> Optional[dict]:
        """返回当前 per_item 模式的运行时状态. 非 per_item 模式返回 None.
        前端 Monitor 用这个画"哪几个个体没被覆盖到".
        """
        if not getattr(self, '_per_item_config', None):
            return None
        sess = getattr(self, '_per_item_session', None)
        cfg = self._per_item_config

        steps_state = [s.to_state_dict() for s in self._per_item_steps]

        return {
            'enabled': True,
            'cycle_active': bool(sess.cycle_active) if sess else False,
            'cycle_start_time': sess.cycle_start_time if sess else None,
            'frame_id': sess.frame_id if sess else 0,
            'config': {
                'stability_window_frames': cfg['stability_window_frames'],
                'item_timeout_seconds': cfg['item_timeout_seconds'],
                'lock_count_on_start': cfg['lock_count_on_start'],
                'finish_label': cfg['finish_label'],
            },
            'steps': steps_state,
            'last_ng_detail': getattr(self, '_per_item_last_ng_detail', None),
        }
