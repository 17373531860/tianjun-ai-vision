"""区域事件模式 (logic_mode='region_events') 判定引擎 —— 纯逻辑层.

场景 (TP 工位流程监测立项, 2026-07): 固定机位俯拍工位, 模型只检测"物"
(扫码枪/测硬度笔/工件/手), 不检测动作; 动作事件由本层用时序+空间规则产出:

  - overlap 规则      : 主体类别框与目标类别框重叠 (可与"主体中心在区域内"做
                        and/or 组合, 可加"与第三类别框相交"约束), 连续满足
                        ≥ min_frames 帧 → 事件确认; 条件消失超过消失确认时长
                        (规则级 gone_seconds, 未配回退全局中断容忍帧数) 后
                        episode 闭合 (闭合动作携带起止时间, 供步骤落库)。
                        例: 测硬度 = 笔中心在 A 区 且 笔∩工件, N1 帧;
                            扫码   = 枪∩工件 或 枪中心在 B 区, N2 帧。
                        可选 min_overlap_ratio (重叠深度 = 交叠面积/主体面积):
                        工具框要有一定比例压在目标框上才算, 治"静置工具贴边误判"
                        (TP 现场: 扫码枪立在枪座上紧挨工件位, 2026-07 实测)。
                        可选 min_move (位移门槛, 画面归一化距离): 主体自 episode
                        起点累计位移 ≥ 该值才允许确认——真动作工具必然被拿起移动
                        (TP 实测位移 ~0.1), 静置工具只有检测抖动 (~0.01), 这是
                        区分"工具在用"和"工具搁着"最干净的特征。
  - region_enter 规则 : 主体类别中心进入区域并持续 ≥ min_frames 帧 → 事件确认
                        (episode 语义同 overlap)。min_frames 给小 = "进区即触发",
                        给大 = "区域驻留超时告警" (event_id 挂警告类事件)。
  - region_exit 规则  : 主体类别对象 (帧间 IoU 关联跟踪) 在区域内出现
                        ≥ min_frames 帧后消失 ≥ gone_frames 帧 → 事件确认,
                        默认结算周期。例: 下工件 = 工件进 C 区后消失 N3 帧。
                        从未进过区域的对象 (如打码位夹具上的常驻工件) 自然排除。

结算判定 (settlement_rules, 可选): 结算时对"本周期确认事件序列 (含结算事件)"
按配置顺序逐条匹配, 先匹配先赢, 命中即用该条的事件结算 (自定义模式同款语义):
  - exact    : 序列与给定序列完全一致 (标准流程 → 合格)
  - missing  : 序列缺某事件 (没测硬度就下料 → 不良)
  - repeated : 某事件出现 ≥ min_count 次 (重复扫码 → 不良)
  - always   : 无条件命中 (放列表末尾当兜底, 收编乱序等"不缺不重但非标准"序列)
全部不命中时回退结算规则自身的 event_id (再缺省 = 合格事件)。

区域锚点跟随 (rule.anchor, 可选): 区域多边形按锚点类别当前位置平移+缩放
(label_split 锚点模式同款), 锚点被遮挡时沿用最近位置 ≤ hold_seconds。

连续同动作去重 (dedup_consecutive, 默认开): 同一动作连续再次确认时静默吸收
(不计步骤/不进序列), 被"另一个动作确认"隔开后才允许重计——对齐 sequential
模式"下个步骤打断上个步骤"的语义, 治"一个真动作被遮挡切成两段确认两次"。
关掉则回到逐段确认的老语义 (连续重复也逐次计入, repeated 判定按次数抓)。

设计原则 (对齐 weighing_engine / source_label_split 范式):
  - 本层不做任何主程序副作用 (开周期/记步骤/触发事件/落库), 只吃逐帧检测框、
    推进状态、返回事件动作列表; 副作用由 VSM 侧执行层翻译。
  - 纯 Python 无 cv2/numpy 依赖, 解析期做完全部校验, 热路径只有查表+数值比较。
  - 所有阈值 (每类置信度 / N 帧 / 中断容忍 / 区域多边形) 都是运行时配置,
    调整不需要重训模型。

配置来源: pipeline_config.region_events (结构见 parse_region_events)。
"""
from __future__ import annotations

from typing import Optional

from backend.api.source_label_split import _parse_bbox, _parse_polygon, _point_in_polygon


# ==================== 几何辅助 ====================

def _intersect_area(a: dict, b: dict) -> float:
    """两个 {x,y,w,h} 归一化框的相交面积, 不相交返回 0。"""
    iw = min(a['x'] + a['w'], b['x'] + b['w']) - max(a['x'], b['x'])
    ih = min(a['y'] + a['h'], b['y'] + b['h']) - max(a['y'], b['y'])
    return iw * ih if iw > 0 and ih > 0 else 0.0


def _bbox_iou(a: dict, b: dict) -> float:
    """两个 {x,y,w,h} 归一化框的 IoU。"""
    inter = _intersect_area(a, b)
    if inter <= 0:
        return 0.0
    union = a['w'] * a['h'] + b['w'] * b['h'] - inter
    return inter / union if union > 0 else 0.0


def _bbox_center(d: dict):
    return d['x'] + d['w'] / 2.0, d['y'] + d['h'] / 2.0


def _pair_overlaps(a: dict, b: dict, min_iou: float) -> bool:
    """两框是否"重叠": min_iou<=0 时任意相交即算, 否则要求 IoU 达标。"""
    if min_iou > 0:
        return _bbox_iou(a, b) >= min_iou
    return _intersect_area(a, b) > 0


# ==================== 配置解析 ====================

class RegionEventRule:
    """一条事件规则的运行时形态 (解析期完成全部校验)。"""

    __slots__ = ('rule_id', 'name', 'rule_type', 'subject_label', 'object_label',
                 'region', 'region_mode', 'min_frames', 'min_iou', 'require_label',
                 'gone_frames', 'match_iou', 'settle', 'event_id',
                 'anchor_label', 'anchor_ref', 'anchor_hold',
                 'gone_seconds', 'min_overlap_ratio', 'min_move')

    def __init__(self, rule_id, name, rule_type, subject_label, object_label,
                 region, region_mode, min_frames, min_iou, require_label,
                 gone_frames, match_iou, settle, event_id,
                 anchor_label=None, anchor_ref=None, anchor_hold=3.0,
                 gone_seconds=None, min_overlap_ratio=0.0, min_move=0.0):
        self.rule_id = rule_id
        self.name = name                    # 事件名 = 步骤落库/流水显示名, 全局唯一
        self.rule_type = rule_type          # 'overlap' | 'region_enter' | 'region_exit'
        self.subject_label = subject_label  # 主体类别 (工具 / 被跟踪对象)
        self.object_label = object_label    # overlap: 目标类别 (通常是工件)
        self.region = region                # 归一化多边形; overlap 可为 None
        self.region_mode = region_mode      # 'and' | 'or' (overlap 的区域组合方式)
        self.min_frames = min_frames        # overlap/enter: 连续满足帧数; exit: 区域内最少观察帧数
        self.min_iou = min_iou              # overlap: 重叠判定 IoU 下限 (0=任意相交)
        self.require_label = require_label  # overlap/enter 可选: 主体须与该类别框相交 (如"手")
        self.gone_frames = gone_frames      # exit: 消失确认帧数
        self.match_iou = match_iou          # exit: 帧间关联 IoU 下限
        self.settle = settle                # 事件确认后是否结算周期
        self.event_id = event_id            # 可选: 映射 events_config 的事件 id
        self.anchor_label = anchor_label    # 可选: 区域跟随的锚点类别
        self.anchor_ref = anchor_ref        # 锚点标定框 {x,y,w,h} (标定坐标系)
        self.anchor_hold = anchor_hold      # 锚点丢失沿用最近位置的秒数
        self.gone_seconds = gone_seconds    # overlap/enter: 消失确认秒数
        #                                     (None=回退全局 gap_tolerance 帧数)
        self.min_overlap_ratio = min_overlap_ratio  # overlap: 重叠深度下限
        #                                     (交叠面积/主体面积, 0=贴边即算)
        self.min_move = min_move            # overlap/enter: 位移门槛
        #                                     (episode 内累计位移, 0=不要求移动)


class SettlementRule:
    """一条结算判定规则: 结算时对确认事件序列做匹配, 命中即用 event_id 结算。"""

    __slots__ = ('match', 'sequence', 'target', 'min_count', 'event_id')

    def __init__(self, match, sequence, target, min_count, event_id):
        self.match = match          # 'exact' | 'missing' | 'repeated' | 'always'
        self.sequence = sequence    # exact: 期望事件名序列 (含结算事件)
        self.target = target        # missing/repeated: 目标事件名
        self.min_count = min_count  # repeated: 触发所需出现次数 (默认 2)
        self.event_id = event_id    # 命中时的结算事件 id

    def hit(self, seq: list) -> bool:
        if self.match == 'exact':
            return seq == self.sequence
        if self.match == 'missing':
            return self.target not in seq
        if self.match == 'repeated':
            return seq.count(self.target) >= self.min_count
        return True  # always: 兜底 (乱序等"不缺不重但非标准"的序列也能自定义结算)


class RegionEventsConfig:
    """pipeline_config.region_events 的解析结果。"""

    __slots__ = ('class_conf', 'gap_tolerance', 'rules',
                 'seq_enabled', 'seq_order', 'seq_event_id', 'settlement_rules',
                 'dedup_consecutive')

    def __init__(self, class_conf, gap_tolerance, rules,
                 seq_enabled, seq_order, seq_event_id, settlement_rules,
                 dedup_consecutive=True):
        self.class_conf = class_conf        # {类别: 置信度下限}, 缺省类别不过滤
        self.gap_tolerance = gap_tolerance  # 连续计数容忍的漏检帧数 M
        self.rules = rules
        self.seq_enabled = seq_enabled      # 流程顺序校验开关 (乱序只记录不告警)
        self.seq_order = seq_order          # 期望事件名顺序 (含结算事件本身)
        self.seq_event_id = seq_event_id
        self.settlement_rules = settlement_rules  # 结算判定, 先匹配先赢; 空=全走默认
        self.dedup_consecutive = dedup_consecutive  # 连续同动作去重 (默认开)


def _parse_anchor(i: int, name: str, raw) -> tuple:
    """规则级锚点跟随配置: (label, ref, hold)。未配置返回 (None, None, 3.0)。

    锚点缺件时降级为固定区域并打日志 (对齐 label_split 的宽松语义,
    保存半截配置不至于整个引擎拒载)。
    """
    if not isinstance(raw, dict) or not raw.get('enabled', True):
        return None, None, 3.0
    label = str(raw.get('label') or '').strip()
    ref = _parse_bbox(raw.get('ref'))
    if not label or ref is None:
        print(f"[RegionEvents] 规则#{i} ({name}) 锚点跟随缺锚点类别/标定框, 降级为固定区域")
        return None, None, 3.0
    try:
        hold = max(0.0, float(raw.get('hold_seconds', 3.0)))
    except (TypeError, ValueError):
        hold = 3.0
    return label, ref, hold


def _parse_rule(i: int, raw: dict) -> RegionEventRule:
    if not isinstance(raw, dict):
        raise ValueError(f"region_events.rules[{i}] 不是对象")
    rule_type = raw.get('type')
    if rule_type not in ('overlap', 'region_enter', 'region_exit'):
        raise ValueError(f"region_events.rules[{i}].type 非法: {rule_type!r}")
    name = str(raw.get('name') or '').strip()
    if not name:
        raise ValueError(f"region_events.rules[{i}] 缺少事件名 name")
    subject = str(raw.get('subject_label') or '').strip()
    if not subject:
        raise ValueError(f"region_events.rules[{i}] ({name}) 缺少 subject_label")

    region = None
    if raw.get('region') is not None:
        region = _parse_polygon(raw.get('region'))
        if region is None:
            raise ValueError(f"region_events.rules[{i}] ({name}) region 多边形非法")

    object_label = str(raw.get('object_label') or '').strip() or None
    if rule_type == 'overlap':
        if not object_label:
            raise ValueError(f"region_events.rules[{i}] ({name}) overlap 规则缺少 object_label")
        default_min_frames, default_settle = 10, False
    elif rule_type == 'region_enter':
        if region is None:
            raise ValueError(f"region_events.rules[{i}] ({name}) region_enter 规则必须配置 region")
        default_min_frames, default_settle = 3, False
    else:
        if region is None:
            raise ValueError(f"region_events.rules[{i}] ({name}) region_exit 规则必须配置 region")
        default_min_frames, default_settle = 3, True

    region_mode = raw.get('region_mode', 'and')
    if region_mode not in ('and', 'or'):
        raise ValueError(f"region_events.rules[{i}] ({name}) region_mode 非法: {region_mode!r}")

    anchor_label, anchor_ref, anchor_hold = _parse_anchor(i, name, raw.get('anchor'))

    gone_seconds = None
    if raw.get('gone_seconds') is not None:
        try:
            gone_seconds = float(raw['gone_seconds'])
        except (TypeError, ValueError):
            raise ValueError(f"region_events.rules[{i}] ({name}) gone_seconds 非数值")
        if gone_seconds <= 0:
            gone_seconds = None  # 0/负值视为未配置, 回退全局中断容忍

    try:
        min_overlap_ratio = float(raw.get('min_overlap_ratio') or 0.0)
    except (TypeError, ValueError):
        raise ValueError(f"region_events.rules[{i}] ({name}) min_overlap_ratio 非数值")
    min_overlap_ratio = min(1.0, max(0.0, min_overlap_ratio))

    try:
        min_move = max(0.0, float(raw.get('min_move') or 0.0))
    except (TypeError, ValueError):
        raise ValueError(f"region_events.rules[{i}] ({name}) min_move 非数值")

    settle = raw.get('settle')
    return RegionEventRule(
        rule_id=str(raw.get('id') or f'r{i + 1}'),
        name=name,
        rule_type=rule_type,
        subject_label=subject,
        object_label=object_label,
        region=region,
        region_mode=region_mode,
        min_frames=max(1, int(raw.get('min_frames') or default_min_frames)),
        min_iou=float(raw.get('min_iou') or 0.0),
        require_label=str(raw.get('require_label') or '').strip() or None,
        gone_frames=max(1, int(raw.get('gone_frames') or 8)),
        match_iou=float(raw.get('match_iou') or 0.3),
        settle=default_settle if settle is None else bool(settle),
        event_id=raw.get('event_id'),
        anchor_label=anchor_label,
        anchor_ref=anchor_ref,
        anchor_hold=anchor_hold,
        gone_seconds=gone_seconds,
        min_overlap_ratio=min_overlap_ratio,
        min_move=min_move,
    )


def _parse_settlement_rules(raw_list, rule_names: list) -> list:
    """结算判定规则解析 (顺序即优先级)。配置非法抛 ValueError。"""
    if not isinstance(raw_list, list):
        raise ValueError("region_events.settlement_rules 不是数组")
    parsed = []
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            raise ValueError(f"settlement_rules[{i}] 不是对象")
        match = raw.get('match')
        if match not in ('exact', 'missing', 'repeated', 'always'):
            raise ValueError(f"settlement_rules[{i}].match 非法: {match!r}")
        event_id = raw.get('event_id')
        if event_id is None:
            raise ValueError(f"settlement_rules[{i}] 缺少 event_id")
        sequence, target, min_count = None, None, 2
        if match == 'exact':
            sequence = [str(n) for n in (raw.get('sequence') or [])]
            unknown = [n for n in sequence if n not in rule_names]
            if not sequence or unknown:
                raise ValueError(f"settlement_rules[{i}] exact 序列非法 (未知事件名 {unknown})")
        elif match in ('missing', 'repeated'):
            target = str(raw.get('target') or '').strip()
            if target not in rule_names:
                raise ValueError(f"settlement_rules[{i}] {match} 目标事件名非法: {target!r}")
            if match == 'repeated':
                min_count = max(2, int(raw.get('min_count') or 2))
        parsed.append(SettlementRule(match, sequence, target, min_count, event_id))
    return parsed


def parse_region_events(pipeline_config: dict) -> Optional[RegionEventsConfig]:
    """解析 pipeline_config.region_events, 无配置/未启用/无规则返回 None。

    结构:
        region_events:
          enabled: true
          class_conf: {扫码枪: 0.45, 测硬度笔: 0.25, 工件: 0.5, 手: 0.35}
          gap_tolerance_frames: 3        # 连续计数容忍 ≤M 帧漏检不清零
          dedup_consecutive: true        # 连续同动作去重 (默认开)
          rules:
            - {name: 测硬度, type: overlap, subject_label: 测硬度笔,
               object_label: 工件, region: [[..],..], region_mode: and,
               min_frames: 15, require_label: null, min_iou: 0,
               min_overlap_ratio: 0.2,   # 可选: 重叠深度 (交叠/主体面积)
               min_move: 0.04,           # 可选: 位移门槛 (episode 内轨迹跨度)
               gone_seconds: 1.5,        # 可选: 消失确认秒数 (缺省用全局帧容忍)
               settle: false, event_id: null}
            - {name: 下工件, type: region_exit, subject_label: 工件,
               region: [[..],..], min_frames: 3, gone_frames: 8,
               match_iou: 0.3, settle: true}
            # 可选: 进区/驻留规则 + 区域锚点跟随
            - {name: 上料, type: region_enter, subject_label: 工件,
               region: [[..],..], min_frames: 3, settle: false,
               anchor: {enabled: true, label: 工件, ref: {x,y,w,h}, hold_seconds: 3}}
          sequence_check: {enabled: false, order: [测硬度, 扫码, 下工件], event_id: null}
          settlement_rules:            # 可选: 结算判定 (顺序即优先级, 先匹配先赢)
            - {match: exact, sequence: [测硬度, 扫码, 下工件], event_id: 1}
            - {match: missing, target: 测硬度, event_id: 2}
            - {match: repeated, target: 扫码, min_count: 2, event_id: 2}

    配置非法时抛 ValueError (由 apply_project_config 侧捕获打日志, 引擎置 None)。
    """
    raw = (pipeline_config or {}).get('region_events')
    if not isinstance(raw, dict) or not raw.get('enabled', True):
        return None
    rules_raw = raw.get('rules')
    if not isinstance(rules_raw, list) or not rules_raw:
        return None

    rules = [_parse_rule(i, r) for i, r in enumerate(rules_raw)]
    names = [r.name for r in rules]
    if len(names) != len(set(names)):
        raise ValueError(f"region_events 规则事件名重复: {names}")
    ids = [r.rule_id for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError(f"region_events 规则 id 重复: {ids}")

    class_conf = {}
    for label, conf in (raw.get('class_conf') or {}).items():
        try:
            class_conf[str(label)] = float(conf)
        except (TypeError, ValueError):
            raise ValueError(f"region_events.class_conf[{label!r}] 非数值: {conf!r}")

    seq = raw.get('sequence_check') or {}
    seq_enabled = bool(seq.get('enabled', False))
    seq_order = [str(n) for n in (seq.get('order') or [])]
    if seq_enabled:
        unknown = [n for n in seq_order if n not in names]
        if not seq_order or unknown:
            raise ValueError(f"region_events.sequence_check.order 非法 (未知事件名 {unknown})")

    settlement_rules = _parse_settlement_rules(
        raw.get('settlement_rules') or [], names)

    return RegionEventsConfig(
        class_conf=class_conf,
        gap_tolerance=max(0, int(raw.get('gap_tolerance_frames') or 3)),
        rules=rules,
        seq_enabled=seq_enabled,
        seq_order=seq_order,
        seq_event_id=seq.get('event_id'),
        settlement_rules=settlement_rules,
        dedup_consecutive=bool(raw.get('dedup_consecutive', True)),
    )


# ==================== 规则状态 ====================

class _OverlapState:
    """overlap 规则的 episode 状态: 连续命中计数 + 中断容忍 + 确认标记。"""

    __slots__ = ('hit', 'miss', 'start_ts', 'last_hit_ts', 'confirmed', 'total',
                 'suppressed', 'ext')

    def __init__(self):
        self.hit = 0            # 本 episode 累计命中帧
        self.miss = 0           # 当前连续未命中帧 (≤ gap_tolerance 不清零)
        self.start_ts = 0.0
        self.last_hit_ts = 0.0
        self.confirmed = False  # 本 episode 是否已产出确认事件
        self.total = 0          # 累计确认次数 (快照展示用)
        self.suppressed = False  # 本 episode 确认被连续去重吸收 (静默, 不落步骤)
        self.ext = None         # 主体中心轨迹包络 [min_x,min_y,max_x,max_y]
        #                         (min_move 位移门槛用; 未开门槛不维护)

    def reset_episode(self):
        self.hit = 0
        self.miss = 0
        self.start_ts = 0.0
        self.last_hit_ts = 0.0
        self.confirmed = False
        self.suppressed = False
        self.ext = None


class _Track:
    """region_exit 规则的单对象轨迹 (帧间 IoU 关联)。"""

    __slots__ = ('bbox', 'in_frames', 'entered', 'miss', 'first_ts', 'last_ts')

    def __init__(self, bbox, in_region: bool, ts: float):
        self.bbox = bbox
        self.in_frames = 1 if in_region else 0  # 区域内累计观察帧
        self.entered = False                     # 区域内待满 min_frames 后置位
        self.miss = 0                            # 连续消失帧
        self.first_ts = ts
        self.last_ts = ts


class _ExitState:
    __slots__ = ('tracks', 'total')

    def __init__(self):
        self.tracks = []
        self.total = 0


# ==================== 判定引擎 ====================

class RegionEventEngine:
    """区域事件判定引擎: 每帧喂检测框, 返回事件动作列表。

    Context: process_frame 在检测帧循环 (推理线程) 内被调用, 单线程访问,
             不持锁、不阻塞、不做 IO; 配置变更时由 apply_project_config
             整体重建引擎实例 (引用替换), 不做原地修改。

    返回的动作字典 (由 VSM 执行层翻译成副作用):
        {action: 'confirmed', rule_id, rule_name, start_ts, ts, settle, event_id}
            某事件达到持续帧数, 确认产出 (settle=True 表示应结算周期)。
        {action: 'closed', rule_id, rule_name, start_ts, end_ts, frames}
            overlap 事件 episode 闭合 (工具离开), 携带完整起止时间供步骤落库。
        {action: 'sequence_violation', expected, actual, event_id}
            结算时事件顺序与配置不符 (可选开关, 乱序只记录不告警的语义由
            event_id 挂的事件动作决定)。
    """

    def __init__(self, cfg: RegionEventsConfig):
        self.cfg = cfg
        self._overlap_states = {r.rule_id: _OverlapState()
                                for r in cfg.rules
                                if r.rule_type in ('overlap', 'region_enter')}
        self._exit_states = {r.rule_id: _ExitState()
                             for r in cfg.rules if r.rule_type == 'region_exit'}
        self._confirmed_seq = []  # 自上次结算以来确认的事件名序列 (顺序校验/结算判定用)
        # 锚点缓存: {锚点类别: (bbox, ts)} —— 锚点短暂被遮挡时沿用最近位置
        self._anchor_labels = {r.anchor_label for r in cfg.rules if r.anchor_label}
        self._anchor_cache = {}

    # ---------- 入口 ----------
    def process_frame(self, detections: list, ts: float) -> list:
        """推进一帧, 返回本帧产出的事件动作列表 (可能为空)。"""
        by_label = {}
        for d in detections or []:
            label = d.get('label')
            conf_min = self.cfg.class_conf.get(label)
            if conf_min is not None and float(d.get('confidence') or 0.0) < conf_min:
                continue  # 按类别置信度过滤 (全局 conf 之上的第二道门)
            by_label.setdefault(label, []).append(d)

        if self._anchor_labels:
            self._update_anchor_cache(by_label, ts)

        events = []
        for rule in self.cfg.rules:
            if rule.rule_type == 'region_exit':
                self._step_exit(rule, by_label, ts, events)
            else:
                self._step_overlap(rule, by_label, ts, events)
        return events

    # ---------- 锚点跟随 ----------
    def _update_anchor_cache(self, by_label: dict, ts: float):
        for lbl in self._anchor_labels:
            dets = by_label.get(lbl)
            if not dets:
                continue
            best = max(dets, key=lambda d: float(d.get('confidence') or 0.0))
            self._anchor_cache[lbl] = (best, ts)

    def _rule_region(self, rule: RegionEventRule, ts: float):
        """本帧该规则生效的区域多边形。

        未配锚点 → 标定区域原样; 配了锚点 → 按锚点当前位置平移+缩放
        (标定坐标系 → 当前坐标系, 不做旋转); 锚点丢失超过 hold → None
        (区域条件按不满足处理, 与 label_split 锚点丢失语义一致)。
        """
        if rule.region is None:
            return None
        if not rule.anchor_label:
            return rule.region
        entry = self._anchor_cache.get(rule.anchor_label)
        if entry is None or ts - entry[1] > rule.anchor_hold:
            return None
        cur, ref = entry[0], rule.anchor_ref
        sx = cur['w'] / ref['w']
        sy = cur['h'] / ref['h']
        return [(cur['x'] + (px - ref['x']) * sx,
                 cur['y'] + (py - ref['y']) * sy) for px, py in rule.region]

    def reset(self):
        """清空全部运行时状态 (项目切换/重新开始检测)。累计计数一并归零。"""
        self.__init__(self.cfg)

    def snapshot(self) -> dict:
        """当前各规则状态摘要 (Monitor/调试用)。"""
        rules = []
        for r in self.cfg.rules:
            if r.rule_type in ('overlap', 'region_enter'):
                st = self._overlap_states[r.rule_id]
                rules.append({'name': r.name, 'type': r.rule_type, 'total': st.total,
                              'hit_frames': st.hit, 'confirmed': st.confirmed})
            else:
                st = self._exit_states[r.rule_id]
                rules.append({'name': r.name, 'type': r.rule_type, 'total': st.total,
                              'active_tracks': len(st.tracks),
                              'entered_tracks': sum(1 for t in st.tracks if t.entered)})
        return {'rules': rules, 'pending_sequence': list(self._confirmed_seq)}

    # ---------- overlap / region_enter 规则 (共用 episode 状态机) ----------
    def _overlap_condition(self, rule: RegionEventRule, by_label: dict,
                           ts: float) -> Optional[dict]:
        """本帧满足规则的主体框 (无则 None)。

        overlap      : 主体∩目标 (可与"中心在区域内"and/or 组合)
        region_enter : 主体中心在区域内 (无目标类别)
        """
        subjects = by_label.get(rule.subject_label) or []
        if not subjects:
            return None
        helpers = by_label.get(rule.require_label) or [] if rule.require_label else None
        region = self._rule_region(rule, ts)
        objects = by_label.get(rule.object_label) or []
        for s in subjects:
            if helpers is not None and not any(
                    _intersect_area(s, h) > 0 for h in helpers):
                continue
            if rule.rule_type == 'region_enter':
                if region is None:
                    continue  # 锚点丢失时区域条件不满足
                cx, cy = _bbox_center(s)
                cond = _point_in_polygon(cx, cy, region)
            else:
                overlaps = any(self._deep_overlap(rule, s, o) for o in objects)
                if rule.region is None:
                    cond = overlaps
                elif region is None:
                    # 配了区域但锚点丢失: and 模式无法满足; or 模式退化为纯重叠
                    cond = overlaps if rule.region_mode == 'or' else False
                else:
                    cx, cy = _bbox_center(s)
                    in_region = _point_in_polygon(cx, cy, region)
                    cond = (overlaps and in_region) if rule.region_mode == 'and' \
                        else (overlaps or in_region)
            if cond:
                return s
        return None

    @staticmethod
    def _deep_overlap(rule: RegionEventRule, s: dict, o: dict) -> bool:
        """主体-目标重叠判定: min_iou 之上再叠可选的重叠深度门槛。

        深度 = 交叠面积/主体面积。静置工具贴边时深度接近 0, 真动作
        (工具压在工件上) 深度显著——用于滤掉"枪立在枪座上紧挨工件"的误判。
        """
        if not _pair_overlaps(s, o, rule.min_iou):
            return False
        if rule.min_overlap_ratio <= 0:
            return True
        area = s['w'] * s['h']
        if area <= 0:
            return False
        return _intersect_area(s, o) / area >= rule.min_overlap_ratio

    @staticmethod
    def _moved_enough(rule: RegionEventRule, st: _OverlapState) -> bool:
        """位移门槛: episode 内主体中心轨迹包络对角线 ≥ min_move 才允许确认。

        用包络跨度而非逐帧位移累加——静置工具的检测抖动逐帧累加会攒出假位移,
        包络跨度只看"真的到过多远的地方", 静置时恒小 (~0.01), 真动作拿起/
        按压/挪动必然拉开 (TP 实测 ≥0.08)。
        """
        if rule.min_move <= 0:
            return True
        if st.ext is None:
            return False
        dx = st.ext[2] - st.ext[0]
        dy = st.ext[3] - st.ext[1]
        return (dx * dx + dy * dy) ** 0.5 >= rule.min_move

    def _episode_gone(self, rule: RegionEventRule, st: _OverlapState,
                      ts: float) -> bool:
        """episode 是否达到消失确认: 规则配了秒数按时间, 否则按全局帧容忍。"""
        if rule.gone_seconds is not None:
            return ts - st.last_hit_ts > rule.gone_seconds
        return st.miss > self.cfg.gap_tolerance

    def _step_overlap(self, rule, by_label, ts, events):
        st = self._overlap_states[rule.rule_id]
        subject = self._overlap_condition(rule, by_label, ts)
        if subject is not None:
            if st.hit == 0:
                st.start_ts = ts
            st.hit += 1
            st.miss = 0
            st.last_hit_ts = ts
            if rule.min_move > 0:
                cx, cy = _bbox_center(subject)
                if st.ext is None:
                    st.ext = [cx, cy, cx, cy]
                else:
                    e = st.ext
                    if cx < e[0]: e[0] = cx
                    if cy < e[1]: e[1] = cy
                    if cx > e[2]: e[2] = cx
                    if cy > e[3]: e[3] = cy
            if (not st.confirmed and st.hit >= rule.min_frames
                    and self._moved_enough(rule, st)):
                st.confirmed = True
                if self._dedup_hit(rule):
                    st.suppressed = True  # 连续同动作: 静默吸收本 episode
                else:
                    st.total += 1
                    self._emit_confirmed(rule, st.start_ts, ts, events)
            return
        if st.hit == 0:
            return
        st.miss += 1
        if self._episode_gone(rule, st, ts):
            # episode 闭合: 已确认的补一条带起止时间的闭合动作 (步骤落库用);
            # 被去重吸收的静默丢弃 (没确认过, 自然也不落步骤)
            if st.confirmed and not st.suppressed:
                events.append({
                    'action': 'closed', 'rule_id': rule.rule_id,
                    'rule_name': rule.name, 'start_ts': st.start_ts,
                    'end_ts': st.last_hit_ts, 'frames': st.hit,
                })
            st.reset_episode()

    # ---------- region_exit 规则 ----------
    def _step_exit(self, rule, by_label, ts, events):
        st = self._exit_states[rule.rule_id]
        dets = by_label.get(rule.subject_label) or []
        region = self._rule_region(rule, ts)  # 锚点丢失时 None → 本帧不累计区域内观察

        # 贪心 IoU 关联: 每条轨迹取与之重叠最大的未占用检测框
        unclaimed = list(range(len(dets)))
        for track in st.tracks:
            best_i, best_iou = -1, rule.match_iou
            for i in unclaimed:
                iou = _bbox_iou(track.bbox, dets[i])
                if iou >= best_iou:
                    best_i, best_iou = i, iou
            if best_i >= 0:
                unclaimed.remove(best_i)
                self._track_update(rule, track, dets[best_i], ts, region)
            else:
                track.miss += 1

        # 未关联上的检测框开新轨迹
        for i in unclaimed:
            cx, cy = _bbox_center(dets[i])
            in_region = region is not None and _point_in_polygon(cx, cy, region)
            st.tracks.append(_Track(dets[i], in_region, ts))

        # 消失确认: 进过区域的产出事件, 没进过的静默清理
        survivors = []
        for track in st.tracks:
            if track.miss < rule.gone_frames:
                survivors.append(track)
                continue
            if track.entered:
                if self._dedup_hit(rule):
                    continue  # 连续同动作去重: 静默吸收
                st.total += 1
                self._emit_confirmed(rule, track.first_ts, ts, events)
        st.tracks = survivors

    def _track_update(self, rule, track, det, ts, region):
        track.bbox = det
        track.miss = 0
        track.last_ts = ts
        if region is None:
            return
        cx, cy = _bbox_center(det)
        if _point_in_polygon(cx, cy, region):
            track.in_frames += 1
            if track.in_frames >= rule.min_frames:
                track.entered = True

    # ---------- 事件产出 ----------
    def _dedup_hit(self, rule: RegionEventRule) -> bool:
        """连续同动作去重: 上一个确认的就是同名动作 → 本次确认应被静默吸收。

        结算规则不去重 (每次都要收口周期); 被其他动作隔开后允许重计,
        所以交错重做 (测硬度→扫码→测硬度→扫码) 的复检场景不受影响。
        """
        return (self.cfg.dedup_consecutive and not rule.settle
                and bool(self._confirmed_seq)
                and self._confirmed_seq[-1] == rule.name)

    def _flush_open_episodes(self, events):
        """结算前冲账: 已确认但未闭合的 overlap episode 立即产出闭合动作。

        没这一步, 结算 (end_cycle) 之后才闭合的 episode 步骤记录会掉在周期外。
        未确认的半截 episode / 被去重吸收的 episode 直接作废 (跨周期不接续)。
        """
        for r in self.cfg.rules:
            if r.rule_type == 'region_exit':
                continue
            st = self._overlap_states[r.rule_id]
            if st.confirmed and not st.suppressed:
                events.append({
                    'action': 'closed', 'rule_id': r.rule_id, 'rule_name': r.name,
                    'start_ts': st.start_ts, 'end_ts': st.last_hit_ts, 'frames': st.hit,
                })
            if st.hit:
                st.reset_episode()

    def _emit_confirmed(self, rule, start_ts, ts, events):
        if rule.settle:
            self._flush_open_episodes(events)
        self._confirmed_seq.append(rule.name)
        action = {
            'action': 'confirmed', 'rule_id': rule.rule_id, 'rule_name': rule.name,
            'start_ts': start_ts, 'ts': ts, 'settle': rule.settle,
            'event_id': rule.event_id,
        }
        if rule.settle and self.cfg.settlement_rules:
            hit = self._match_settlement(self._confirmed_seq)
            if hit is not None:
                action['settle_event_id'] = hit.event_id
                action['settle_reason'] = self._settle_reason(hit)
        events.append(action)
        if rule.settle:
            self._on_settle(events)

    def _match_settlement(self, seq: list):
        """结算判定: 按配置顺序先匹配先赢; 全不中返回 None (走默认结算事件)。"""
        for sr in self.cfg.settlement_rules:
            if sr.hit(seq):
                return sr
        return None

    @staticmethod
    def _settle_reason(sr: SettlementRule) -> str:
        if sr.match == 'exact':
            return f"序列匹配 {'→'.join(sr.sequence)}"
        if sr.match == 'missing':
            return f"缺事件 {sr.target}"
        if sr.match == 'repeated':
            return f"事件 {sr.target} 重复≥{sr.min_count}次"
        return "兜底判定"

    def _on_settle(self, events):
        """结算点: 校验事件顺序 (可选) 并重置本轮序列。"""
        if self.cfg.seq_enabled and self._confirmed_seq != self.cfg.seq_order:
            events.append({
                'action': 'sequence_violation',
                'expected': list(self.cfg.seq_order),
                'actual': list(self._confirmed_seq),
                'event_id': self.cfg.seq_event_id,
            })
        self._confirmed_seq = []
