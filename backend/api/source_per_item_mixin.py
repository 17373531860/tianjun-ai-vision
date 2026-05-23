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
        'expected_count',                  # v3.9+: 已知固定个体数 (0=未配置, 走 auto 路径)
        'items', 'next_item_id', 'locked_count', 'completed',
        'completed_count_in_session',
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
        self.action_label = per.get('action_label', '')

        # ── 阈值与帧数 ──
        self.item_tracking_iou = float(per.get('item_tracking_iou', 0.3))
        self.coverage_iou = float(per.get('coverage_iou', 0.3))
        self.sustain_frames = int(per.get('sustain_frames', 5))

        self.completion = per.get('completion', 'all_covered')
        self.min_item_count = per.get('min_item_count', 'auto')

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
            # item_label 内部是 tuple, 序列化时若仅 1 个还原为字符串 (兼容老前端)
            'item_label': self.item_label[0] if len(self.item_label) == 1 else list(self.item_label),
            'action_label': self.action_label,
            'expected_count': self.expected_count,
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
        'last_activity_time',           # 最近一次看到 item/action 标签的时刻 (空闲超时用)
        'all_done_first_at',            # 所有步骤首次全 completed 的时刻 (完成即结算用)
        'lock_lookahead_deadline',      # 周期开始后补锁定窗口的截止时刻
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

        self._per_item_config = {
            'stability_window_frames': max(1, stability_window),
            'stability_iou_threshold': stability_iou,
            'stability_count_tolerance': max(0, stability_count_tol),
            'stability_count_ratio': max(0.1, min(1.0, stability_count_ratio)),
            'item_timeout_seconds': max(0.0, item_timeout),
            'lock_count_on_start': lock_on_start,
            'finish_label': finish_label,
            'finish_sustain_frames': max(1, finish_sustain),
            'settle_after_all_done_sec': max(0.0, settle_after_all_done),
            'lock_lookahead_seconds': max(0.0, lock_lookahead),
            'cycle_max_duration_sec': max(0.0, cycle_max_sec),
            'idle_timeout_sec': max(0.0, idle_timeout_sec),
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
        # "活动" = action 标签出现 (工人在做工序), 不包括 item 标签 (静态工件)
        # 这样模型把桌面误检出残留 item 标签也不会刷新 idle 计时, idle_timeout 能正常兜底
        any_action_this_frame = False
        in_lookahead = (
            sess.lock_lookahead_deadline is not None
            and current_time <= sess.lock_lookahead_deadline
        )
        for step in self._per_item_steps:
            # 3a. 更新个体位置 (多标签 OR 合并)
            item_boxes = self._collect_item_boxes(boxes_by_label, step.item_label)
            if item_boxes:
                step.update_item_positions(
                    item_boxes, sess.frame_id, current_time,
                    cfg['lock_count_on_start'],
                )
                # 3a'. 补锁定窗口 (v3.9+): 配了 expected_count + 当前锁定数 < expected_count
                # + 仍在 lookahead 窗口内 → 吸收"新位置"的 box
                if (
                    in_lookahead
                    and step.expected_count > 0
                    and len(step.items) < step.expected_count
                ):
                    self._per_item_absorb_new_items(step, item_boxes, sess.frame_id, current_time)
            # 3b. 应用工序覆盖 (按 action_label)
            action_boxes = boxes_by_label.get(step.action_label, [])
            if action_boxes:
                any_action_this_frame = True
            step.apply_coverage(action_boxes, sess.frame_id, current_time)
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

        # 刷新 last_activity_time (本帧出现 action 标签 = 工人在做工序 = 有活动)
        # 注意: 只看 action, 不看 item — 工件静置画面里有 item 标签不算"活动",
        # 否则工人放工件不操作时永远 idle=0, 触发不了 idle_timeout 兜底.
        if any_action_this_frame:
            sess.last_activity_time = current_time

        # ──── 4. 完成即结算 (OK 路径, 无需收尾标签) ────
        # 所有 per_item 步骤都 completed → 保持 settle_after_all_done_sec 秒 → 立即结算 OK
        settle_after_all_done = cfg.get('settle_after_all_done_sec', 0)
        if settle_after_all_done > 0 and self._per_item_steps:
            all_done = all(s.completed for s in self._per_item_steps)
            if all_done:
                if sess.all_done_first_at is None:
                    sess.all_done_first_at = current_time
                elif (current_time - sess.all_done_first_at) >= settle_after_all_done:
                    print(f"[per_item] 所有步骤完成已保持 {settle_after_all_done:.1f}s, 立即结算 OK")
                    self._per_item_settle_cycle(current_time)
                    return
            else:
                sess.all_done_first_at = None

        # ──── 5. 周期超时强制结算 (NG 兜底, per_item 专属参数) ────
        cycle_max = cfg.get('cycle_max_duration_sec', 0)
        if cycle_max > 0 and sess.cycle_start_time is not None:
            cycle_elapsed = current_time - sess.cycle_start_time
            if cycle_elapsed > cycle_max:
                print(f"[per_item] 周期总时长超时 {cycle_elapsed:.1f}s > {cycle_max}s, 强制结算")
                self._per_item_settle_cycle(current_time)
                return

        # ──── 6. 空闲超时强制结算 (NG 兜底, per_item 专属参数) ────
        # 本场景核心兜底: 工人停手 → 持续 N 秒无 action 标签 → 强制结算
        # 注: 只看 action 标签, 不看 item, 见上面 last_activity_time 刷新规则
        idle_timeout = cfg.get('idle_timeout_sec', 0)
        if idle_timeout > 0 and sess.last_activity_time is not None:
            idle_elapsed = current_time - sess.last_activity_time
            if idle_elapsed > idle_timeout:
                print(f"[per_item] 空闲 {idle_elapsed:.1f}s > {idle_timeout}s, 强制结算")
                self._per_item_settle_cycle(current_time)
                return

        # ──── 7. 收尾标签判定 (兼容原有路径, 配了 finish_label 仍然生效) ────
        finish_label = cfg.get('finish_label') or ''
        if finish_label and finish_label in boxes_by_label:
            sess.finish_label_consec_frames += 1
            if sess.finish_label_consec_frames >= cfg['finish_sustain_frames']:
                self._per_item_settle_cycle(current_time)
        else:
            sess.finish_label_consec_frames = 0

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
            ratio = cfg.get('stability_count_ratio', 0.85)
            count_tol = cfg.get('stability_count_tolerance', 0)
            required = max(1, min(target, int(target * ratio), target - count_tol))

            # 窗口里每帧的检出数都要 ≥ required
            window_frames = list(sess.stability_buffer)
            if any(len(fr) < required for fr in window_frames):
                return
            # 锁定时挑窗口里"检出最多"的那一帧 (最接近真实数量)
            best_fr = max(window_frames, key=lambda fr: len(fr))
            latest_boxes = list(best_fr)[:target]      # 截到 target 颗封顶
            item_count = len(latest_boxes)
        else:
            # ──── 路径 B: auto 模式 (老路径) ────
            counts = [len(b) for b in sess.stability_buffer]
            if len(set(counts)) != 1:
                return
            item_count = counts[0]
            if item_count <= 0:
                return
            # 位置稳定 (相邻帧 IoU > 阈值, 按 NN 匹配)
            prev = list(sess.stability_buffer[0])
            for fr in list(sess.stability_buffer)[1:]:
                if not self._per_item_frames_position_stable(prev, fr, cfg['stability_iou_threshold']):
                    return
                prev = list(fr)
            # min_item_count 校验
            min_required = first_step.min_item_count
            if min_required != 'auto' and isinstance(min_required, int):
                if item_count < min_required:
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
    def _per_item_absorb_new_items(step, item_boxes, frame_id: int, ts: float):
        """周期开始后 lookahead 窗口内, 用本帧 item_boxes 补充被遮挡漏锁的个体.

        策略: 本帧每个 box 与现有所有 items 计算 IoU, 都低于 item_tracking_iou
        视为"新位置" → 加入个体表 (covered=false), 直到达到 expected_count 上限.

        语义保证:
          - 已 covered 的个体永远不会被替换 (单调性, 见不变量 §一.1)
          - 不影响已锁定的 N 个个体, 仅追加缺失的
          - 一旦达到 expected_count 立即停止补锁
        """
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
                f"{len(step.items)}/{step.expected_count}"
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
                'stability_iou_threshold': cfg.get('stability_iou_threshold', 0.5),
                'stability_count_tolerance': cfg.get('stability_count_tolerance', 0),
                'stability_count_ratio': cfg.get('stability_count_ratio', 0.85),
                'item_timeout_seconds': cfg['item_timeout_seconds'],
                'lock_count_on_start': cfg['lock_count_on_start'],
                'finish_label': cfg['finish_label'],
                'finish_sustain_frames': cfg.get('finish_sustain_frames', 3),
                'settle_after_all_done_sec': cfg.get('settle_after_all_done_sec', 0.0),
                'lock_lookahead_seconds': cfg.get('lock_lookahead_seconds', 5.0),
                # per_item 专属超时 (与其他模式隔离)
                'cycle_max_duration_sec': cfg.get('cycle_max_duration_sec', 0.0),
                'idle_timeout_sec': cfg.get('idle_timeout_sec', 0.0),
            },
            'steps': steps_state,
            'last_ng_detail': getattr(self, '_per_item_last_ng_detail', None),
        }
