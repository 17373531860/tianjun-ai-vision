"""区域事件模式 (logic_mode='region_events') 判定引擎 —— 纯逻辑层.

场景 (TP 工位流程监测立项, 2026-07): 固定机位俯拍工位, 模型只检测"物"
(扫码枪/测硬度笔/工件/手), 不检测动作; 动作事件由本层用时序+空间规则产出:

  - overlap 规则      : 主体类别框与目标类别框重叠 (可与"主体中心在区域内"做
                        and/or 组合, 可加"与第三类别框相交"约束), 连续满足
                        ≥ min_frames 帧 → 事件确认; 条件消失超过消失确认时长
                        (规则级 gone_seconds, 未配回退全局中断容忍帧数) 后
                        episode 闭合 (闭合动作携带起止时间, 供步骤落库)。
                        可选 min_seconds (确认时长秒, 2026-07 秒基门槛): 配了
                        则确认改按"episode 命中跨度 ≥ 该秒数"判定, min_frames
                        退化为防杂散噪声的 3 帧硬下限——现场相机帧率 (24/30fps)
                        和推理帧率 (随 GPU 负载 15~30fps) 都会漂, 帧数门槛在
                        不同机器上语义不一致, 秒基与帧率解耦。0/不配 = 老语义。
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

监控类规则 (2026-09, 出厂规则模板批次; 与预置行人/车辆模型配套):
  - region_count 规则 : 区域内主体类别数量 ≥ min_count 并持续达标 → 事件确认
                        (聚集告警: 危险区同时超过 N 人)。
  - region_empty 规则 : 区域内主体类别数量 = 0 并持续达标 → 事件确认
                        (离岗告警: 值守区超过 N 秒无人, 建议配 min_seconds)。
  - proximity 规则    : 任一主体与任一目标的中心归一化距离 ≤ max_distance
                        并持续达标 → 事件确认 (人车距离预警: 人靠近叉车)。
  - cross_count 规则  : 主体类别对象 (帧间 IoU 关联跟踪, region_exit 同款) 进入
                        区域满 min_frames 帧 → 逐对象计一次 (过线/人流计数)。
                        与 region_enter 的差别: enter 是规则级 episode (两人同时
                        进只算一次), cross_count 按轨迹逐对象计数; 同一对象在区
                        内持续在场只计一次, 消失 ≥ gone_frames 后再进算新一次。
  - facing_dwell 规则 : 朝向驻留 (2026-09 蒸镀点检批次): 主体框携带的朝向角
                        ('facing' 字段, VSM 层经 person_orientation 注入) 与
                        "主体中心→仪表点 (target_point)"连线的夹角 ≤ tolerance_deg
                        并持续达标 → 事件确认 ("面向仪表确认了 N 秒", 点检台账);
                        alert_on_absent=true 时条件取反 ("持续 min_seconds 无人
                        面向仪表" → 超时未点检告警, 与 region_empty 反向语义同款)。
                        可选 region 限定站位区; 朝向角与 bearing 同为归一化空间角
                        (坐标畸变一致抵消), 无朝向字段的主体框视为不满足。
  region_count/region_empty/proximity/facing_dwell 共用 overlap 的 episode 状态机
  (min_frames/min_seconds/gone_seconds 同义),
  但语义是"持续观测告警"而非"工序动作": 非结算确认时不进动作序列、不参与连续
  同动作去重、不打断其他 episode、也不被其他动作确认打断——否则单规则监控项目
  第二次告警会被去重静默吞掉 (seq 尾部永远是同名), 混合项目里告警会撕碎工序序列。

结算判定 (settlement_rules, 可选): 结算时对"本周期确认事件序列 (含结算事件)"
按配置顺序逐条匹配, 先匹配先赢, 命中即用该条的事件结算 (自定义模式同款语义):
  - exact    : 序列与给定序列完全一致 (标准流程 → 合格)
  - missing  : 序列缺某事件 (没测硬度就下料 → 不良)
  - repeated : 某事件出现 ≥ min_count 次 (重复扫码 → 不良)
  - complete : 序列按期望模板走完 (2026-09, 依附 sequence_check.order): 抽掉
               无序组成员后的子序列与 order 完全一致, 且每个组成员恰好出现
               count 次——一条规则同时收口 少拿(不足)/多拿(超额)/模板缺步乱序,
               典型编排 [complete→合格, missing/repeated 诊断→NG, always→NG兜底]
  - always   : 无条件命中 (放列表末尾当兜底, 收编乱序等"不缺不重但非标准"序列)
全部不命中时回退结算规则自身的 event_id (再缺省 = 合格事件)。

区域锚点跟随 (rule.anchor, 可选): 区域多边形按锚点类别当前位置平移+缩放
(label_split 锚点模式同款), 锚点被遮挡时沿用最近位置 ≤ hold_seconds。

连续同动作去重 (dedup_consecutive, 默认开): 同一动作连续再次确认时静默吸收
(不计步骤/不进序列), 被"另一个动作确认"隔开后才允许重计——对齐 sequential
模式"下个步骤打断上个步骤"的语义, 治"一个真动作被遮挡切成两段确认两次"。
关掉则回到逐段确认的老语义 (连续重复也逐次计入, repeated 判定按次数抓)。

动作互斥打断 (常开, 非配置): 一个动作确认的瞬间, 其他规则进行中的 episode
立即收尾——已确认的产出闭合动作 (真实起止时间落步骤), 未确认的半截命中作废。
没有它, 消失确认秒数会把"断开 < N 秒"的两段命中桥接成一次动作: TP 复检场景
(测硬度→扫码→测硬度→扫码) 两次扫码中间夹着测硬度、断开仅 ~1 秒, 曾被并成
一次导致序列少一步, 复检误落兜底 NG (2026-07 实测)。

严格模式期望位置守门 (sequence_check.strict, 2026-09, 默认关):
参考 sequential 模式严格前缀语义——非结算动作型规则确认时, 若其名字不等于
期望序列 (sequence_check.order) 的下一位, 该确认被静默吸收: 不进序列、不计
步骤、不触发事件、也**不打断其他 episode** (吸收的是噪声, 不能让噪声切碎
真动作的进行中命中)。吸收后 episode 立即重置, 条件仍满足会重新累计——期望
位置推进后同一动作可再次确认 (关键: 不吸收掉"悬停→放入"这类条件不断链场景
里迟到的真确认)。立项动机 (2026-09 拿料装盘工位): "放入"类动作的条件在完成
后残留恒真 (已放入的料一直在盘内), 每次互斥打断后残料重新累计又产出假确认,
把序列搅乱——严格档吸收错位假确认, 同时让真缺步在结算时暴露 (期望序列走不完
→ exact 不匹配)。边界语义:
  - 结算规则永不吸收 (周期必须能收口, 乱序周期靠 settlement_rules 判 NG);
  - 监控类规则不受守门 (本就不进序列);
  - 未列入 order 的动作规则不受守门 (行为与非严格一致);
  - 周期未开时 (序列为空) 非 order[0] 的确认也被吸收 → 顺带防幽灵开周期;
  - region_exit 型动作被吸收时轨迹已消亡, 该次事件不可追回 (episode 型可以);
  - strict 仅在 sequence_check.enabled 且 order 非空时生效, 否则静默忽略;
  - 期望位置指针显式维护 (2026-09-24): 只被"命中骨架当前位的确认"推进,
    组成员/组外规则确认进序列不推指针 (此前用序列长度当指针, 组外规则一确认
    期望位就错移一格, 后续全被误吸收)。
  - 守门/对账骨架 seq_walk_order (2026-09-24 下午): order 允许穿插按序组成员
    当"完整人类可读序列"的显示锚点 (监控页 SOP/步骤表按 order 全展开建卡),
    引擎抽掉组成员位得到骨架, 指针与 complete 对账只认骨架——组成员是并行
    轨道, 数量/组内顺序由组逻辑收账。老配置 (order 不含成员) 骨架==order 零差异。

无序组 (sequence_check.groups, 2026-09-24, 依附 sequence_check.enabled):
"顺序自由但数量要严"的并行轨道动作 (立项场景: 四料盒拿料顺序不固定, 但每盒
必须恰好拿一次; 双手流水作业时拿下一件料与上一件的检查/放入在时间上交错,
固定位置放不进 order)。组成员语义:
  - 不受严格守门 (永不吸收), 正常进序列/计步骤/触发事件 (首个确认可开周期);
  - 不打断其他 episode、也不被其他确认打断 (并行轨道互不干扰——没有这条,
    拿下一件料的确认会切碎上一件正在进行的检查命中, 反之亦然);
  - 无序组成员不允许出现在 order 里 (按序组成员可以, 见上文骨架条目——写就
    必须逐位等于成员表展开)、不允许是结算/监控规则、跨组不允许重名 (解析期拒载);
  - 本周期超额确认 (第 count+1 次) 产出 group_repeat 可观测动作 (执行层记
    日志), 结算侧由 complete 不中/repeated 命中收账判 NG——超额是真实缺陷,
    必须进序列留痕, 不能静默吸收 (吸收会把"多放一件"洗成合格)。
  - ordered=true (组内按序, 2026-09-24 下午): 成员保持并行轨道全部豁免
    (确认时点仍可与 order 侧动作自由交错), 但 complete 结算额外要求"序列中
    该组成员的子序列 == 成员表顺序 (每成员重复 count 次)"——立项场景升级:
    四料盒拿料顺序被工艺定死 5→6→4→2, 但双手流水使拿料确认早于上一件的
    查/放收口 (真实回放 17 周期中 2 个早到 1.4s/5.3s), 把拿料直接写进 order
    会被守门吸收后追不回 (手已离开列区) 而误 NG; 组内按序把"顺序强制"从
    实时守门挪到结算对账, 时序交错免疫、顺序违规照样 NG。

设计原则 (对齐 weighing_engine / source_label_split 范式):
  - 本层不做任何主程序副作用 (开周期/记步骤/触发事件/落库), 只吃逐帧检测框、
    推进状态、返回事件动作列表; 副作用由 VSM 侧执行层翻译。
  - 纯 Python 无 cv2/numpy 依赖, 解析期做完全部校验, 热路径只有查表+数值比较。
  - 所有阈值 (每类置信度 / N 帧 / 中断容忍 / 区域多边形) 都是运行时配置,
    调整不需要重训模型。

配置来源: pipeline_config.region_events (结构见 parse_region_events)。
"""
from __future__ import annotations

import math
from typing import Optional

from backend.api.source_label_split import (
    _parse_bbox, _parse_polygon, _point_in_regions,
)


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


def tag_operator_uniforms(detections: list, frame) -> None:
    """给检测框打 is_operator (蓝工装启发式)。现场「只认操作员」用。

    黄背心外协 / 深色便服 → False。frame 缺失时不打标 (规则侧视为非操作员)。
    任何异常隔离, 不改主链路。
    """
    if frame is None or not detections:
        return
    try:
        import cv2
        h, w = frame.shape[:2]
        if h < 8 or w < 8:
            return
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        for d in detections:
            try:
                x1 = int(max(0, float(d['x']) * w))
                y1 = int(max(0, float(d['y']) * h))
                x2 = int(min(w, (float(d['x']) + float(d['w'])) * w))
                y2 = int(min(h, (float(d['y']) + float(d['h'])) * h))
            except (KeyError, TypeError, ValueError):
                d['is_operator'] = False
                continue
            if x2 - x1 < 6 or y2 - y1 < 6:
                d['is_operator'] = False
                continue
            crop = hsv[y1:y2, x1:x2]
            blue = cv2.inRange(crop, (90, 40, 40), (130, 255, 255))
            yellow = cv2.inRange(crop, (18, 80, 80), (40, 255, 255))
            br = float(blue.mean()) / 255.0
            yr = float(yellow.mean()) / 255.0
            d['is_operator'] = bool(br >= 0.08 and yr < 0.12)
    except Exception:
        pass


def _pair_overlaps(a: dict, b: dict, min_iou: float) -> bool:
    """两框是否"重叠": min_iou<=0 时任意相交即算, 否则要求 IoU 达标。"""
    if min_iou > 0:
        return _bbox_iou(a, b) >= min_iou
    return _intersect_area(a, b) > 0


# 监控类规则: 持续观测告警/计数, 不是工序动作 (语义差异见模块 docstring)
_MONITORING_TYPES = ('region_count', 'region_empty', 'proximity', 'cross_count',
                     'facing_dwell')


# ==================== 配置解析 ====================

class RegionEventRule:
    """一条事件规则的运行时形态 (解析期完成全部校验)。"""

    __slots__ = ('rule_id', 'name', 'rule_type', 'subject_label', 'object_label',
                 'region', 'region_mode', 'min_frames', 'min_iou', 'require_label',
                 'gone_frames', 'match_iou', 'settle', 'event_id',
                 'anchor_label', 'anchor_ref', 'anchor_hold',
                 'gone_seconds', 'min_overlap_ratio', 'min_move', 'min_seconds',
                 'object_margin', 'min_count', 'max_distance',
                 'target_point', 'tolerance_deg', 'alert_on_absent',
                 'require_operator')

    def __init__(self, rule_id, name, rule_type, subject_label, object_label,
                 region, region_mode, min_frames, min_iou, require_label,
                 gone_frames, match_iou, settle, event_id,
                 anchor_label=None, anchor_ref=None, anchor_hold=3.0,
                 gone_seconds=None, min_overlap_ratio=0.0, min_move=0.0,
                 min_seconds=0.0, object_margin=0.0, min_count=3,
                 max_distance=0.15, target_point=None, tolerance_deg=35.0,
                 alert_on_absent=False, require_operator=False):
        self.rule_id = rule_id
        self.name = name                    # 事件名 = 步骤落库/流水显示名, 全局唯一
        self.rule_type = rule_type          # 'overlap' | 'region_enter' | 'region_exit'
        #                                     | 'region_count' | 'region_empty' | 'proximity'
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
        self.min_seconds = min_seconds      # overlap/enter: 确认时长秒基门槛
        #                                     (>0 按命中跨度秒判定, 帧数退化为
        #                                      3 帧硬下限; 0=按 min_frames 帧数)
        self.min_count = min_count          # region_count: 区域内最少目标数
        self.max_distance = max_distance    # proximity: 两类中心归一化距离上限
        self.target_point = target_point    # facing_dwell: 仪表点 (归一化 x, y)
        self.tolerance_deg = tolerance_deg  # facing_dwell: 朝向夹角容差 (度)
        self.alert_on_absent = alert_on_absent  # facing_dwell: True=条件取反
        #                                     ("持续无人面向仪表"超时告警)
        self.require_operator = bool(require_operator)  # 只认蓝工装操作员
        #                                     (黄背心外协/参观不计入; 由 VSM
        #                                      在帧上打 is_operator 标记)
        self.object_margin = object_margin  # overlap: 目标框虚拟扩边 (归一化,
        #                                     0=不扩)。真动作发生在目标框边缘
        #                                     外侧几个百分点时 (如扫工件下沿
        #                                     条码, 枪不压进工件框) 用它桥接;
        #                                     纯空间几何量, 与帧率无关


class SettlementRule:
    """一条结算判定规则: 结算时对确认事件序列做匹配, 命中即用 event_id 结算。"""

    __slots__ = ('match', 'sequence', 'target', 'min_count', 'event_id')

    def __init__(self, match, sequence, target, min_count, event_id):
        self.match = match          # 'exact' | 'missing' | 'repeated' | 'complete' | 'always'
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
        if self.match == 'complete':
            return False  # 需要期望模板+无序组上下文, 由引擎 _match_settlement 特判
        return True  # always: 兜底 (乱序等"不缺不重但非标准"的序列也能自定义结算)


class SequenceGroup:
    """一个无序组: 成员顺序自由、每周期各恰好 count 次 (语义见模块 docstring)。

    ordered=True 升级为"组内按序": 成员仍是并行轨道 (确认不受守门/互斥),
    但 complete 结算要求成员子序列按成员表顺序走 (顺序强制在结算对账)。
    """

    __slots__ = ('name', 'members', 'count', 'ordered')

    def __init__(self, name, members, count, ordered=False):
        self.name = name        # 显示名 (快照/日志用)
        self.members = members  # 成员事件名列表 (跨组不重名; 按序组成员可穿插进
        #                         order 当显示锚点, 无序组成员不允许)
        self.count = count      # 每成员每周期的期望确认次数 (默认 1)
        self.ordered = ordered  # 组内按序 (成员子序列须 == 成员表顺序)


class RegionEventsConfig:
    """pipeline_config.region_events 的解析结果。"""

    __slots__ = ('class_conf', 'gap_tolerance', 'rules',
                 'seq_enabled', 'seq_order', 'seq_event_id', 'seq_strict',
                 'seq_groups', 'seq_walk_order',
                 'settlement_rules', 'dedup_consecutive')

    def __init__(self, class_conf, gap_tolerance, rules,
                 seq_enabled, seq_order, seq_event_id, settlement_rules,
                 dedup_consecutive=True, seq_strict=False, seq_groups=None):
        self.class_conf = class_conf        # {类别: 置信度下限}, 缺省类别不过滤
        self.gap_tolerance = gap_tolerance  # 连续计数容忍的漏检帧数 M
        self.rules = rules
        self.seq_enabled = seq_enabled      # 流程顺序校验开关 (乱序只记录不告警)
        self.seq_order = seq_order          # 期望事件名顺序 (含结算事件本身; 按序组
        #                                     成员可穿插其中当显示锚点, 见 walk_order)
        self.seq_event_id = seq_event_id
        self.seq_strict = seq_strict        # 严格模式: 期望位置守门+非期望吸收 (默认关)
        self.seq_groups = seq_groups or []  # 无序组 (顺序自由但数量要严的并行轨道)
        # 守门/对账骨架: order 抽掉组成员后的子序列。组成员位只是"完整人类可读
        # 序列"的显示锚点 (SOP/步骤表按 order 全展开), 引擎的期望位置指针、
        # complete 对账都只认骨架——成员是并行轨道, 数量/组内顺序由组逻辑收账。
        member_names = {m for g in self.seq_groups for m in g.members}
        self.seq_walk_order = [n for n in seq_order if n not in member_names]
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
    if rule_type not in ('overlap', 'region_enter', 'region_exit',
                         'region_count', 'region_empty', 'proximity',
                         'cross_count', 'facing_dwell'):
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
    elif rule_type == 'proximity':
        if not object_label:
            raise ValueError(f"region_events.rules[{i}] ({name}) proximity 规则缺少 object_label")
        default_min_frames, default_settle = 5, False
    elif rule_type == 'region_enter':
        if region is None:
            raise ValueError(f"region_events.rules[{i}] ({name}) region_enter 规则必须配置 region")
        default_min_frames, default_settle = 3, False
    elif rule_type in ('region_count', 'region_empty'):
        if region is None:
            raise ValueError(f"region_events.rules[{i}] ({name}) {rule_type} 规则必须配置 region")
        default_min_frames, default_settle = 8, False
    elif rule_type == 'cross_count':
        if region is None:
            raise ValueError(f"region_events.rules[{i}] ({name}) cross_count 规则必须配置 region")
        default_min_frames, default_settle = 2, False
    elif rule_type == 'facing_dwell':
        default_min_frames, default_settle = 3, False  # region 可选 (站位区限定)
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
    if rule_type in ('region_empty', 'facing_dwell'):
        min_move = 0.0  # 区域无人/朝向驻留 (驻留本就要求站定) 时位移门槛无意义, 强制忽略

    try:
        min_seconds = max(0.0, float(raw.get('min_seconds') or 0.0))
    except (TypeError, ValueError):
        raise ValueError(f"region_events.rules[{i}] ({name}) min_seconds 非数值")

    try:
        object_margin = float(raw.get('object_margin') or 0.0)
    except (TypeError, ValueError):
        raise ValueError(f"region_events.rules[{i}] ({name}) object_margin 非数值")
    # 上限 0.2: 扩边是"桥接目标框边缘几个百分点"的微调, 更大就该重画区域了
    object_margin = min(0.2, max(0.0, object_margin))

    try:
        min_count = max(1, int(raw.get('min_count') or 3))
    except (TypeError, ValueError):
        raise ValueError(f"region_events.rules[{i}] ({name}) min_count 非整数")

    try:
        max_distance = float(raw.get('max_distance') or 0.15)
    except (TypeError, ValueError):
        raise ValueError(f"region_events.rules[{i}] ({name}) max_distance 非数值")
    max_distance = min(1.0, max(0.0, max_distance))

    # facing_dwell: 仪表点 (target_point [x,y]) / 兼容 target_region 小框取质心
    # (前端复用 ROI 编辑器画小框标仪表位置, 解析期收敛为一个点)
    target_point = None
    if rule_type == 'facing_dwell':
        raw_pt = raw.get('target_point')
        if (isinstance(raw_pt, (list, tuple)) and len(raw_pt) == 2):
            try:
                target_point = (min(1.0, max(0.0, float(raw_pt[0]))),
                                min(1.0, max(0.0, float(raw_pt[1]))))
            except (TypeError, ValueError):
                raise ValueError(f"region_events.rules[{i}] ({name}) target_point 非数值")
        elif raw.get('target_region') is not None:
            polys = _parse_polygon(raw.get('target_region'))
            if polys is None:
                raise ValueError(f"region_events.rules[{i}] ({name}) target_region 多边形非法")
            _pts = [p for blk in polys for p in blk]  # 多块形态: 全部顶点取质心
            target_point = (sum(p[0] for p in _pts) / len(_pts),
                            sum(p[1] for p in _pts) / len(_pts))
        if target_point is None:
            raise ValueError(
                f"region_events.rules[{i}] ({name}) facing_dwell 规则必须标定仪表点 "
                f"(target_point 或 target_region)")

    tolerance_deg = 35.0
    if raw.get('tolerance_deg') is not None:
        try:
            tolerance_deg = float(raw['tolerance_deg'])
        except (TypeError, ValueError):
            raise ValueError(f"region_events.rules[{i}] ({name}) tolerance_deg 非数值")
        tolerance_deg = min(90.0, max(5.0, tolerance_deg))

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
        min_seconds=min_seconds,
        object_margin=object_margin,
        min_count=min_count,
        max_distance=max_distance,
        target_point=target_point,
        tolerance_deg=tolerance_deg,
        alert_on_absent=bool(raw.get('alert_on_absent', False)),
        require_operator=bool(raw.get('require_operator', False)),
    )


def _parse_settlement_rules(raw_list, rule_names: list,
                            has_seq_order: bool = False) -> list:
    """结算判定规则解析 (顺序即优先级)。配置非法抛 ValueError。

    complete 匹配依附期望模板 (sequence_check enabled + order 非空), 没有模板
    的 complete 永远不可能命中, 属配置错误, 解析期硬拒比运行期静默不中更早暴露。
    """
    if not isinstance(raw_list, list):
        raise ValueError("region_events.settlement_rules 不是数组")
    parsed = []
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            raise ValueError(f"settlement_rules[{i}] 不是对象")
        match = raw.get('match')
        if match not in ('exact', 'missing', 'repeated', 'complete', 'always'):
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
        elif match == 'complete' and not has_seq_order:
            raise ValueError(
                f"settlement_rules[{i}] complete 匹配需要开启流程顺序校验并配置期望顺序")
        parsed.append(SettlementRule(match, sequence, target, min_count, event_id))
    return parsed


def _parse_sequence_groups(raw_list, rules: list, seq_order: list) -> list:
    """无序组解析 (依附 sequence_check.enabled)。配置非法抛 ValueError。

    校验从严 (与 settlement 同哲学, 半截组配置早暴露):
    成员必须是已配的动作型非结算规则、不得进 order、跨组不重名。
    """
    if not isinstance(raw_list, list):
        raise ValueError("sequence_check.groups 不是数组")
    by_name = {r.name: r for r in rules}
    seen_members = set()
    parsed = []
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            raise ValueError(f"sequence_check.groups[{i}] 不是对象")
        members = [str(n) for n in (raw.get('members') or [])]
        if not members:
            raise ValueError(f"sequence_check.groups[{i}] 成员为空")
        ordered = bool(raw.get('ordered', False))
        try:
            count = max(1, int(raw.get('count') or 1))
        except (TypeError, ValueError):
            raise ValueError(f"sequence_check.groups[{i}] count 非整数")
        for m in members:
            rule = by_name.get(m)
            if rule is None:
                raise ValueError(f"sequence_check.groups[{i}] 成员未知: {m!r}")
            if rule.settle:
                raise ValueError(
                    f"sequence_check.groups[{i}] 成员 {m!r} 是结算规则, 不能入组")
            if rule.rule_type in _MONITORING_TYPES:
                raise ValueError(
                    f"sequence_check.groups[{i}] 成员 {m!r} 是监控类规则, 不能入组")
            if m in seq_order and not ordered:
                raise ValueError(
                    f"sequence_check.groups[{i}] 成员 {m!r} 已在期望顺序 order 里, "
                    f"无序组成员是顺序自由轨道, 二者互斥 (按序组成员才允许进 order)")
            if m in seen_members:
                raise ValueError(f"sequence_check.groups 成员重复: {m!r}")
            seen_members.add(m)
        # 按序组成员允许写进 order 当"完整人类可读序列"的载体 (2026-09-24 下午,
        # 监控页 SOP/步骤表按 order 全展开显示): 引擎守门/对账仍用抽掉组成员后的
        # 骨架 (seq_walk_order), 成员位只是显示锚点。写就必须写全且逐位等于成员表
        # 展开 (半截穿插一定是配错, 早暴露)。不写 (老骨架风格) 也合法。
        member_set = set(members)
        in_order = [n for n in seq_order if n in member_set]
        if ordered and in_order:
            expansion = [m for m in members for _ in range(count)]
            if in_order != expansion:
                raise ValueError(
                    f"sequence_check.groups[{i}] 成员在期望顺序中的出现序列 "
                    f"{in_order} 与成员表展开 {expansion} 不一致 "
                    f"(按序组成员要么不进 order, 要么按成员表顺序每成员 {count} 次写全)")
        parsed.append(SequenceGroup(
            name=str(raw.get('name') or f'组{i + 1}').strip() or f'组{i + 1}',
            members=members, count=count, ordered=ordered))
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
               min_seconds: 0.3,         # 可选: 确认时长秒 (>0 时替代 min_frames,
               #                           帧数退化为 3 帧硬下限, 与帧率解耦)
               object_margin: 0.02,      # 可选: 目标框虚拟扩边 (归一化 0~0.2,
               #                           动作发生在目标框边缘外侧时桥接)
               gone_seconds: 1.5,        # 可选: 消失确认秒数 (缺省用全局帧容忍)
               settle: false, event_id: null}
            - {name: 下工件, type: region_exit, subject_label: 工件,
               region: [[..],..], min_frames: 3, gone_frames: 8,
               match_iou: 0.3, settle: true}
            # 可选: 进区/驻留规则 + 区域锚点跟随
            - {name: 上料, type: region_enter, subject_label: 工件,
               region: [[..],..], min_frames: 3, settle: false,
               anchor: {enabled: true, label: 工件, ref: {x,y,w,h}, hold_seconds: 3}}
          sequence_check: {enabled: false, order: [测硬度, 扫码, 下工件], event_id: null,
                           strict: false,  # strict: 期望位置守门+非期望吸收
                           #                 (依附 enabled+order, 语义见模块 docstring)
                           groups: [       # 可选: 无序组 (顺序自由但数量要严的
                           #                 并行轨道, 成员不得进 order; 语义见
                           #                 模块 docstring)。ordered: 组内按序
                           #                 (complete 结算要求成员按表序走,
                           #                 确认时点仍可与 order 侧交错)
                             {name: 拿料, members: [拿料2号, 拿料4号],
                              count: 1, ordered: false}]}
          settlement_rules:            # 可选: 结算判定 (顺序即优先级, 先匹配先赢)
            - {match: complete, event_id: 1}   # 模板走完+组成员各恰好 count 次
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
    # 严格模式依附于顺序校验: 未开 enabled/无 order 时静默忽略 (宽松语义,
    # 半截配置不拒载, 与锚点缺件降级同哲学)
    seq_strict = bool(seq.get('strict', False)) and seq_enabled and bool(seq_order)
    # 无序组同样依附顺序校验 (未开 enabled 时静默忽略; 开了则校验从严)
    seq_groups = []
    if seq_enabled and seq.get('groups'):
        seq_groups = _parse_sequence_groups(seq['groups'], rules, seq_order)

    settlement_rules = _parse_settlement_rules(
        raw.get('settlement_rules') or [], names,
        has_seq_order=seq_enabled and bool(seq_order))

    return RegionEventsConfig(
        class_conf=class_conf,
        gap_tolerance=max(0, int(raw.get('gap_tolerance_frames') or 3)),
        rules=rules,
        seq_enabled=seq_enabled,
        seq_order=seq_order,
        seq_event_id=seq.get('event_id'),
        settlement_rules=settlement_rules,
        dedup_consecutive=bool(raw.get('dedup_consecutive', True)),
        seq_strict=seq_strict,
        seq_groups=seq_groups,
    )


# ==================== 规则状态 ====================

class _OverlapState:
    """overlap 规则的 episode 状态: 连续命中计数 + 中断容忍 + 确认标记。"""

    __slots__ = ('hit', 'miss', 'start_ts', 'last_hit_ts', 'confirmed', 'total',
                 'suppressed', 'ext', 'centers')

    def __init__(self):
        self.hit = 0            # 本 episode 累计命中帧
        self.miss = 0           # 当前连续未命中帧 (≤ gap_tolerance 不清零)
        self.start_ts = 0.0
        self.last_hit_ts = 0.0
        self.confirmed = False  # 本 episode 是否已产出确认事件
        self.total = 0          # 累计确认次数 (快照展示用)
        self.suppressed = False  # 本 episode 确认被连续去重吸收 (静默, 不落步骤)
        self.ext = None         # 平滑后中心轨迹包络 [min_x,min_y,max_x,max_y]
        #                         (min_move 位移门槛用; 未开门槛不维护)
        self.centers = []       # 最近 5 帧原始中心 (中位数平滑用)

    def reset_episode(self):
        self.hit = 0
        self.miss = 0
        self.start_ts = 0.0
        self.last_hit_ts = 0.0
        self.confirmed = False
        self.suppressed = False
        self.ext = None
        self.centers = []


class _Track:
    """region_exit / cross_count 规则的单对象轨迹 (帧间 IoU 关联)。"""

    __slots__ = ('bbox', 'in_frames', 'entered', 'miss', 'first_ts', 'last_ts',
                 'counted')

    def __init__(self, bbox, in_region: bool, ts: float):
        self.bbox = bbox
        self.in_frames = 1 if in_region else 0  # 区域内累计观察帧
        self.entered = False                     # 区域内待满 min_frames 后置位
        self.miss = 0                            # 连续消失帧
        self.first_ts = ts
        self.last_ts = ts
        self.counted = False                     # cross_count: 本轨迹已计数


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
        {action: 'absorbed', rule_id, rule_name, ts, expected}
            严格模式 (seq_strict) 吸收的非期望位置确认——纯可观测性动作,
            执行层只记日志, 不产生任何主程序副作用。
        {action: 'group_repeat', rule_id, rule_name, ts, group, count, limit}
            无序组成员本周期超额确认 (第 count 次 > limit)——伴随正常 confirmed
            动作一起产出 (超额是真实动作, 照常进序列留痕), 执行层只记日志;
            结算侧由 complete 不中/repeated 命中收账。
    """

    def __init__(self, cfg: RegionEventsConfig):
        self.cfg = cfg
        self._overlap_states = {r.rule_id: _OverlapState()
                                for r in cfg.rules
                                if r.rule_type not in ('region_exit', 'cross_count')}
        self._exit_states = {r.rule_id: _ExitState()
                             for r in cfg.rules
                             if r.rule_type in ('region_exit', 'cross_count')}
        self._confirmed_seq = []  # 自上次结算以来确认的事件名序列 (顺序校验/结算判定用)
        # 严格模式 (seq_strict): 期望位置守门用的序列名集合 + 吸收计数 (可观测性)
        self._seq_name_set = set(cfg.seq_walk_order) if cfg.seq_strict else frozenset()
        self._absorbed_total = 0
        # 严格模式期望位置指针: 走 seq_walk_order 骨架 (order 抽掉组成员位),
        # 只被"命中骨架当前位的确认"推进——组成员/组外规则进序列不推指针
        # (不能用序列长度当指针, 见模块 docstring)
        self._strict_pos = 0
        # 无序组: {成员名: 所属组} 反查表 (守门豁免/互斥豁免/模板匹配抽取用)
        self._group_of = {m: g for g in cfg.seq_groups for m in g.members}
        # 锚点缓存: {锚点类别: (bbox, ts)} —— 锚点短暂被遮挡时沿用最近位置
        self._anchor_labels = {r.anchor_label for r in cfg.rules if r.anchor_label}
        self._anchor_cache = {}
        # facing_dwell: 帧高宽比 (H/W, VSM 注帧时更新)。朝向角在像素平面,
        # 归一化坐标的方位角必须按纵横比还原, 否则 16:9 下最差偏 ~20°
        self.frame_aspect = 0.5625

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

        by_label_op = None
        if any(r.require_operator for r in self.cfg.rules):
            by_label_op = {
                k: [d for d in v if d.get('is_operator')]
                for k, v in by_label.items()
            }

        events = []
        for rule in self.cfg.rules:
            src = by_label_op if rule.require_operator else by_label
            if rule.rule_type == 'region_exit':
                self._step_exit(rule, src, ts, events)
            elif rule.rule_type == 'cross_count':
                self._step_cross(rule, src, ts, events)
            else:
                self._step_overlap(rule, src, ts, events)
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
        # region 是多块形态 (_parse_polygon 返回值), 逐块变换
        return [[(cur['x'] + (px - ref['x']) * sx,
                  cur['y'] + (py - ref['y']) * sy) for px, py in poly]
                for poly in rule.region]

    def reset(self):
        """清空全部运行时状态 (项目切换/重新开始检测)。累计计数一并归零。"""
        self.__init__(self.cfg)

    def snapshot(self) -> dict:
        """当前各规则状态摘要 (Monitor/调试用)。

        in_progress / episode_start_ts / suppressed 给 Monitor 步骤面板驱动
        "进行中"高亮与 in-flight PT (v3.32 SOP 卡片打通)。
        """
        rules = []
        for r in self.cfg.rules:
            if r.rule_id in self._overlap_states:
                st = self._overlap_states[r.rule_id]
                rules.append({'name': r.name, 'type': r.rule_type, 'total': st.total,
                              'hit_frames': st.hit, 'confirmed': st.confirmed,
                              'in_progress': st.hit > 0,
                              'suppressed': st.suppressed,
                              'episode_start_ts': st.start_ts if st.hit > 0 else None})
            else:  # region_exit / cross_count (轨迹型状态)
                st = self._exit_states[r.rule_id]
                marked = sum(1 for t in st.tracks
                             if (t.counted if r.rule_type == 'cross_count' else t.entered))
                rules.append({'name': r.name, 'type': r.rule_type, 'total': st.total,
                              'in_progress': False,
                              'active_tracks': len(st.tracks),
                              'entered_tracks': marked})
        snap = {'rules': rules, 'pending_sequence': list(self._confirmed_seq)}
        if self.cfg.seq_strict:
            snap['strict'] = {
                'expected_next': self._expected_next(),
                'absorbed_total': self._absorbed_total,
            }
        if self._group_of:
            # 无序组消费状态 (Monitor/调试: "本周期各料盒拿了几次"一眼可查)
            snap['groups'] = [
                {'name': g.name, 'count': g.count, 'ordered': g.ordered,
                 'counts': {m: self._confirmed_seq.count(m) for m in g.members}}
                for g in self.cfg.seq_groups]
        return snap

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
                cond = _point_in_regions(cx, cy, region)
            else:
                overlaps = any(self._deep_overlap(rule, s, o) for o in objects)
                if rule.region is None:
                    cond = overlaps
                elif region is None:
                    # 配了区域但锚点丢失: and 模式无法满足; or 模式退化为纯重叠
                    cond = overlaps if rule.region_mode == 'or' else False
                else:
                    cx, cy = _bbox_center(s)
                    in_region = _point_in_regions(cx, cy, region)
                    cond = (overlaps and in_region) if rule.region_mode == 'and' \
                        else (overlaps or in_region)
            if cond:
                return s
        return None

    def _monitor_condition(self, rule: RegionEventRule, by_label: dict,
                           ts: float):
        """监控类规则的本帧条件: (是否满足, 代表主体框|None)。

        region_count : 区域内主体数 ≥ min_count (代表主体取区域内第一个, 供截图)
        region_empty : 区域内主体数 = 0 (无主体框, 截图走整帧兜底)
        proximity    : 任一主体与任一目标中心归一化距离 ≤ max_distance
        facing_dwell : 任一主体的朝向角与"主体中心→仪表点"连线夹角 ≤ tolerance_deg
                       (alert_on_absent=True 时取反: 无任何主体面向仪表)
        锚点丢失时区域为 None → 条件不满足 (与 region_enter 语义一致)。
        """
        if rule.rule_type == 'facing_dwell':
            return self._facing_condition(rule, by_label, ts)
        if rule.rule_type == 'proximity':
            subjects = by_label.get(rule.subject_label) or []
            objects = by_label.get(rule.object_label) or []
            limit_sq = rule.max_distance * rule.max_distance
            for s in subjects:
                scx, scy = _bbox_center(s)
                for o in objects:
                    ocx, ocy = _bbox_center(o)
                    dx, dy = scx - ocx, scy - ocy
                    if dx * dx + dy * dy <= limit_sq:
                        return True, s
            return False, None
        region = self._rule_region(rule, ts)
        if region is None:
            return False, None
        inside = []
        for s in by_label.get(rule.subject_label) or []:
            cx, cy = _bbox_center(s)
            if _point_in_regions(cx, cy, region):
                inside.append(s)
        if rule.rule_type == 'region_count':
            return len(inside) >= rule.min_count, (inside[0] if inside else None)
        return not inside, None  # region_empty: 区域内一个主体都没有

    def _facing_condition(self, rule: RegionEventRule, by_label: dict,
                          ts: float):
        """facing_dwell 本帧条件: (是否满足, 代表主体框|None)。

        主体框携带 'facing' 字段 (图像平面 yaw 度, VSM 层经 person_orientation
        节流注入; 无 pose 结果的框视为朝向未知, 不参与判定)。
        判定: 主体中心→仪表点连线的方位角 与 facing 的最小角差 ≤ tolerance_deg。
        方位角按像素平面算 (归一化增量 × 帧宽高还原), 与 yaw 口径一致。
        可选站位区 region: 只考察中心在区域内的主体 (锚点丢失=条件不满足)。
        alert_on_absent=True 语义取反: "无任何人面向仪表"持续成立 → 告警
        (配 min_seconds 即"超过 N 秒没人来点检"巡检哨兵)。
        """
        region = self._rule_region(rule, ts) if rule.region is not None else None
        tx, ty = rule.target_point
        aspect = self.frame_aspect or 0.5625
        hit = None
        for s in by_label.get(rule.subject_label) or []:
            cx, cy = _bbox_center(s)
            if rule.region is not None:
                if region is None or not _point_in_regions(cx, cy, region):
                    continue
            yaw = s.get('facing')
            if yaw is None:
                continue
            # 像素平面方位角: dy 乘 H/W 还原纵横比 (dx 已按 W 归一)
            bearing = math.degrees(math.atan2((ty - cy) * aspect, tx - cx))
            d = abs(float(yaw) - bearing) % 360.0
            if (d if d <= 180.0 else 360.0 - d) <= rule.tolerance_deg:
                hit = s
                break
        if rule.alert_on_absent:
            return hit is None, None
        return hit is not None, hit

    @staticmethod
    def _deep_overlap(rule: RegionEventRule, s: dict, o: dict) -> bool:
        """主体-目标重叠判定: min_iou 之上再叠可选的重叠深度门槛。

        深度 = 交叠面积/主体面积。静置工具贴边时深度接近 0, 真动作
        (工具压在工件上) 深度显著——用于滤掉"枪立在枪座上紧挨工件"的误判。

        object_margin > 0 时目标框先四边虚拟外扩 (TP #35 实测: 扫工件下沿
        条码, 枪框在工件框下缘外 ~3% 画面高, 物理接触但框不相交)。扩边只
        参与"是否相交"判定; 深度门槛仍按原始目标框算, 避免扩边稀释深度。
        """
        if rule.object_margin > 0:
            m = rule.object_margin
            o_inflated = {'x': o['x'] - m, 'y': o['y'] - m,
                          'w': o['w'] + 2 * m, 'h': o['h'] + 2 * m}
            if not _pair_overlaps(s, o_inflated, rule.min_iou):
                return False
            if rule.min_overlap_ratio <= 0:
                return True
            # 深度按原始框: 相交才有深度, 扩边命中但原框不交时深度记 0
            area = s['w'] * s['h']
            if area <= 0:
                return False
            return _intersect_area(s, o) / area >= rule.min_overlap_ratio
        if not _pair_overlaps(s, o, rule.min_iou):
            return False
        if rule.min_overlap_ratio <= 0:
            return True
        area = s['w'] * s['h']
        if area <= 0:
            return False
        return _intersect_area(s, o) / area >= rule.min_overlap_ratio

    @staticmethod
    def _track_motion(st: _OverlapState, subject: dict):
        """维护位移门槛的轨迹包络: 对原始中心做 5 帧中位数平滑后再进包络。

        为什么要平滑 (TP 现场 2026-07 实测教训): 手从静置的枪前划过时, 遮挡
        会把检测框"切"小 → 框中心单帧跳变 ~0.06, 直接进包络就是一次假位移,
        位移门槛形同虚设。中位数对 1~2 帧的瞬态跳变完全免疫; 真动作 (拿起
        工具持续挪动) 的位移会持续多帧, 平滑后照样进包络。
        """
        st.centers.append(_bbox_center(subject))
        if len(st.centers) > 5:
            st.centers.pop(0)
        if len(st.centers) < 3:
            return  # 样本不足不进包络 (episode 头两帧, 等平滑窗口成形)
        xs = sorted(c[0] for c in st.centers)
        ys = sorted(c[1] for c in st.centers)
        cx, cy = xs[len(xs) // 2], ys[len(ys) // 2]
        if st.ext is None:
            st.ext = [cx, cy, cx, cy]
        else:
            e = st.ext
            if cx < e[0]: e[0] = cx
            if cy < e[1]: e[1] = cy
            if cx > e[2]: e[2] = cx
            if cy > e[3]: e[3] = cy

    @staticmethod
    def _moved_enough(rule: RegionEventRule, st: _OverlapState) -> bool:
        """位移门槛: episode 内 (平滑后) 主体中心轨迹包络对角线 ≥ min_move 才确认。

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

    @staticmethod
    def _held_long_enough(rule: RegionEventRule, st: _OverlapState,
                          ts: float) -> bool:
        """确认时长判定: 秒基门槛优先, 未配回退帧数门槛。

        为什么秒基 (2026-07): min_frames 的实际时长 = 帧数/推理帧率, 而现场
        相机帧率 (24/30fps) 和推理帧率 (随 GPU 负载 15~30fps) 都会漂, 同一份
        配置在不同机器上门槛松紧不一致。配了 min_seconds 后按"episode 命中
        跨度 ≥ 秒数"判定, 与帧率解耦; min_frames 退化为 3 帧硬下限, 防止
        零星 1~2 帧的杂散框靠时间跨度蒙混过关。
        """
        if rule.min_seconds > 0:
            return st.hit >= 3 and (ts - st.start_ts) >= rule.min_seconds
        return st.hit >= rule.min_frames

    def _step_overlap(self, rule, by_label, ts, events):
        st = self._overlap_states[rule.rule_id]
        if rule.rule_type in _MONITORING_TYPES:
            cond, subject = self._monitor_condition(rule, by_label, ts)
        else:
            subject = self._overlap_condition(rule, by_label, ts)
            cond = subject is not None
        if cond:
            if st.hit == 0:
                st.start_ts = ts
            st.hit += 1
            st.miss = 0
            st.last_hit_ts = ts
            if rule.min_move > 0 and subject is not None:
                self._track_motion(st, subject)
            if (not st.confirmed and self._held_long_enough(rule, st, ts)
                    and self._moved_enough(rule, st)):
                if self._strict_absorb(rule):
                    # 严格模式吸收: 不闩锁 confirmed, 整个 episode 重置——条件仍
                    # 满足会重新累计, 期望位置推进后同一动作可再确认 (不重置的话
                    # "悬停→放入"条件不断链场景里迟到的真确认会被吞掉, 误 NG)
                    self._emit_absorbed(rule, ts, events)
                    st.reset_episode()
                    return
                st.confirmed = True
                if self._dedup_hit(rule):
                    st.suppressed = True  # 连续同动作: 静默吸收本 episode
                else:
                    st.total += 1
                    self._emit_confirmed(rule, st.start_ts, ts, events,
                                         subject=subject)
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
            in_region = region is not None and _point_in_regions(cx, cy, region)
            st.tracks.append(_Track(dets[i], in_region, ts))

        # 消失确认: 进过区域的产出事件, 没进过的静默清理
        survivors = []
        for track in st.tracks:
            if track.miss < rule.gone_frames:
                survivors.append(track)
                continue
            if track.entered:
                if self._strict_absorb(rule):
                    # 严格模式吸收: 轨迹已消亡, 该次事件不可追回 (与 episode 型
                    # 不同, 无法重置重来; 边界语义见模块 docstring)
                    self._emit_absorbed(rule, ts, events)
                    continue
                if self._dedup_hit(rule):
                    continue  # 连续同动作去重: 静默吸收
                st.total += 1
                self._emit_confirmed(rule, track.first_ts, ts, events,
                                     subject=dict(track.bbox) if track.bbox else None)
        st.tracks = survivors

    def _track_update(self, rule, track, det, ts, region):
        track.bbox = det
        track.miss = 0
        track.last_ts = ts
        if region is None:
            return
        cx, cy = _bbox_center(det)
        if _point_in_regions(cx, cy, region):
            track.in_frames += 1
            if track.in_frames >= rule.min_frames:
                track.entered = True

    # ---------- cross_count 规则 (过线/人流计数, 逐对象轨迹) ----------
    def _step_cross(self, rule, by_label, ts, events):
        """进区计次: IoU 关联跟踪 (region_exit 同款), 每条轨迹进区满
        min_frames 计一次, 在场期间不重复计; 消失 ≥ gone_frames 清轨迹,
        对象离开后再进算新一次。锚点丢失时区域为 None → 本帧不累计。
        """
        st = self._exit_states[rule.rule_id]
        dets = by_label.get(rule.subject_label) or []
        region = self._rule_region(rule, ts)

        unclaimed = list(range(len(dets)))
        for track in st.tracks:
            best_i, best_iou = -1, rule.match_iou
            for i in unclaimed:
                iou = _bbox_iou(track.bbox, dets[i])
                if iou >= best_iou:
                    best_i, best_iou = i, iou
            if best_i >= 0:
                unclaimed.remove(best_i)
                track.bbox = dets[best_i]
                track.miss = 0
                track.last_ts = ts
                if region is not None:
                    cx, cy = _bbox_center(dets[best_i])
                    if _point_in_regions(cx, cy, region):
                        track.in_frames += 1
                        self._maybe_count_cross(rule, st, track, ts, events)
                    # 中心暂时出区不清 in_frames/counted: 边界抖动不重复计数
            else:
                track.miss += 1

        for i in unclaimed:
            cx, cy = _bbox_center(dets[i])
            in_region = region is not None and _point_in_regions(cx, cy, region)
            track = _Track(dets[i], in_region, ts)
            st.tracks.append(track)
            if in_region:
                self._maybe_count_cross(rule, st, track, ts, events)

        st.tracks = [t for t in st.tracks if t.miss < rule.gone_frames]

    def _maybe_count_cross(self, rule, st, track, ts, events):
        if track.counted or track.in_frames < rule.min_frames:
            return
        track.counted = True
        st.total += 1
        self._emit_confirmed(rule, track.first_ts, ts, events,
                             subject=dict(track.bbox) if track.bbox else None)

    # ---------- 事件产出 ----------
    def _strict_absorb(self, rule: RegionEventRule) -> bool:
        """严格模式期望位置守门: 本次确认是否应被吸收 (语义见模块 docstring)。

        吸收判定必须发生在互斥打断/进序列/触发事件之前——被吸收的确认是噪声,
        噪声不允许产生任何副作用 (尤其不能打断其他规则进行中的真命中)。
        """
        if not self.cfg.seq_strict or rule.settle:
            return False
        if rule.rule_type in _MONITORING_TYPES:
            return False  # 监控类本就不进序列, 不受守门
        if rule.name in self._group_of:
            return False  # 无序组成员: 顺序自由轨道, 永不吸收 (超额留痕给结算收账)
        if rule.name not in self._seq_name_set:
            return False  # 未列入期望序列的辅助动作: 行为与非严格一致
        walk = self.cfg.seq_walk_order
        pos = self._strict_pos
        return pos >= len(walk) or walk[pos] != rule.name

    def _expected_next(self):
        """严格模式下"完整期望序列 (含组成员位) 的下一待完成条目" (纯可观测性)。

        骨架位按守门指针消费; 组成员位按该成员已确认次数消费 (成员是并行
        轨道, 实际可比显示位提前/滞后完成——这里只求给监控页一个符合人类
        阅读顺序的"下一步"提示, 不参与任何判定)。
        """
        member_done = {}
        for n in self._confirmed_seq:
            if n in self._group_of:
                member_done[n] = member_done.get(n, 0) + 1
        walk_used = 0
        occ = {}
        for entry in self.cfg.seq_order:
            if entry in self._group_of:
                k = occ.get(entry, 0)
                occ[entry] = k + 1
                if member_done.get(entry, 0) <= k:
                    return entry
            else:
                if walk_used >= self._strict_pos:
                    return entry
                walk_used += 1
        return None

    def _emit_absorbed(self, rule: RegionEventRule, ts: float, events: list):
        """产出吸收动作 (仅供执行层记日志/可观测性, 无任何主程序副作用)。"""
        self._absorbed_total += 1
        events.append({
            'action': 'absorbed', 'rule_id': rule.rule_id, 'rule_name': rule.name,
            'ts': ts,
            'expected': self._expected_next(),
        })

    def _dedup_hit(self, rule: RegionEventRule) -> bool:
        """连续同动作去重: 上一个确认的就是同名动作 → 本次确认应被静默吸收。

        结算规则不去重 (每次都要收口周期); 被其他动作隔开后允许重计,
        所以交错重做 (测硬度→扫码→测硬度→扫码) 的复检场景不受影响。
        """
        return (self.cfg.dedup_consecutive and not rule.settle
                and bool(self._confirmed_seq)
                and self._confirmed_seq[-1] == rule.name)

    def _interrupt_others(self, confirming_rule, events):
        """动作互斥打断: 一个动作确认时, 其他进行中的 episode 立即收尾。

        为什么必须打断 (TP 现场 2026-07 教训): 消失确认秒数会把"断开 < N 秒"
        的两段命中桥接成同一次动作。复检场景 (测硬度→扫码→测硬度→扫码) 里两次
        扫码只隔 1 秒多, 中间还夹着一次测硬度 —— 不打断的话两次扫码被并成一次,
        序列少一步, 结算从"复检"错落到兜底 NG。与其他模式"下个步骤到来即打断
        上个步骤"的语义对齐:
          - 已确认的 episode → 产出闭合动作 (步骤落库带真实起止时间)
          - 未确认的半截命中 → 作废 (人已切到下个动作, 残段不是有效动作)
        """
        for r in self.cfg.rules:
            if (r.rule_id not in self._overlap_states
                    or r.rule_id == confirming_rule.rule_id
                    or r.rule_type in _MONITORING_TYPES
                    or r.name in self._group_of):
                continue  # 轨迹型规则无 episode; 监控类是独立观测, 不被打断;
                #           无序组成员是并行轨道, 进行中的命中不被模板动作切碎
            st = self._overlap_states[r.rule_id]
            if st.hit == 0:
                continue
            if st.confirmed and not st.suppressed:
                events.append({
                    'action': 'closed', 'rule_id': r.rule_id, 'rule_name': r.name,
                    'start_ts': st.start_ts, 'end_ts': st.last_hit_ts, 'frames': st.hit,
                })
            st.reset_episode()

    def _flush_open_episodes(self, events):
        """结算前冲账: 已确认但未闭合的 overlap episode 立即产出闭合动作。

        没这一步, 结算 (end_cycle) 之后才闭合的 episode 步骤记录会掉在周期外。
        未确认的半截 episode / 被去重吸收的 episode 直接作废 (跨周期不接续)。
        """
        for r in self.cfg.rules:
            if r.rule_id not in self._overlap_states:
                continue  # region_exit / cross_count 是轨迹型状态, 无 episode 可冲
            st = self._overlap_states[r.rule_id]
            if st.confirmed and not st.suppressed:
                events.append({
                    'action': 'closed', 'rule_id': r.rule_id, 'rule_name': r.name,
                    'start_ts': st.start_ts, 'end_ts': st.last_hit_ts, 'frames': st.hit,
                })
            if st.hit:
                st.reset_episode()

    def _emit_confirmed(self, rule, start_ts, ts, events, subject=None):
        if rule.rule_type in _MONITORING_TYPES and not rule.settle:
            # 监控类告警 (非结算): 不进动作序列 (免被连续同动作去重吞掉第二次
            # 告警)、不打断其他 episode (与工序动作正交), 只产出确认动作
            events.append({
                'action': 'confirmed', 'rule_id': rule.rule_id,
                'rule_name': rule.name, 'start_ts': start_ts, 'ts': ts,
                'settle': False, 'event_id': rule.event_id, 'subject': subject,
            })
            return
        if rule.settle:
            self._flush_open_episodes(events)
        elif rule.name not in self._group_of:
            # 无序组成员不打断别人 (并行轨道: 拿下一件料时上一件的检查/放入
            # 正在进行, 打断会切碎真命中; 反向豁免见 _interrupt_others)
            self._interrupt_others(rule, events)
        self._confirmed_seq.append(rule.name)
        # 严格模式期望位置指针: 命中骨架当前位才推进 (组成员/组外规则不推)
        walk = self.cfg.seq_walk_order
        if (self.cfg.seq_strict and self._strict_pos < len(walk)
                and rule.name == walk[self._strict_pos]):
            self._strict_pos += 1
        # 无序组超额确认: 实时留痕 (执行层记日志; 结算由 complete/repeated 收账)
        grp = self._group_of.get(rule.name)
        if grp is not None:
            cnt = self._confirmed_seq.count(rule.name)
            if cnt > grp.count:
                events.append({
                    'action': 'group_repeat', 'rule_id': rule.rule_id,
                    'rule_name': rule.name, 'ts': ts,
                    'group': grp.name, 'count': cnt, 'limit': grp.count,
                })
        action = {
            'action': 'confirmed', 'rule_id': rule.rule_id, 'rule_name': rule.name,
            'start_ts': start_ts, 'ts': ts, 'settle': rule.settle,
            'event_id': rule.event_id,
            # 确认瞬间的主体框 (归一化 x/y/w/h) —— 执行层裁步骤截图用, 可为 None
            'subject': subject,
        }
        if rule.settle and self.cfg.settlement_rules:
            hit = self._match_settlement(self._confirmed_seq)
            if hit is not None:
                action['settle_event_id'] = hit.event_id
                action['settle_reason'] = self._settle_reason(hit)
        events.append(action)
        if rule.settle:
            self._on_settle(events)

    def _order_matches(self, seq: list) -> bool:
        """序列是否按期望模板走完 (complete 匹配 / 顺序校验的组感知口径)。

        无组: 与 order 完全一致 (老语义逐字节)。
        有组: 抽掉组成员后的子序列须与骨架 seq_walk_order 完全一致
        (order 里的组成员位只是显示锚点, 对账不认), 且逐组判定:
          - 无序组: 每个成员恰好出现 count 次 (顺序自由);
          - 按序组 (ordered): 该组成员的子序列 == 成员表顺序每成员重复
            count 次 (子序列相等蕴含次数相等, 少/多/乱序一条全收)。
        """
        if not self._group_of:
            return seq == self.cfg.seq_order
        walk = []
        sub = {g.name: [] for g in self.cfg.seq_groups}
        for name in seq:
            grp = self._group_of.get(name)
            if grp is not None:
                sub[grp.name].append(name)
            else:
                walk.append(name)
        if walk != self.cfg.seq_walk_order:
            return False
        for g in self.cfg.seq_groups:
            if g.ordered:
                expect = [m for m in g.members for _ in range(g.count)]
                if sub[g.name] != expect:
                    return False
            elif any(sub[g.name].count(m) != g.count for m in g.members):
                return False
        return True

    def _match_settlement(self, seq: list):
        """结算判定: 按配置顺序先匹配先赢; 全不中返回 None (走默认结算事件)。

        complete 需要期望模板+无序组上下文, 在这里特判 (SettlementRule.hit
        是纯序列匹配, 拿不到引擎状态)。
        """
        for sr in self.cfg.settlement_rules:
            if (self._order_matches(seq) if sr.match == 'complete'
                    else sr.hit(seq)):
                return sr
        return None

    def _settle_reason(self, sr: SettlementRule) -> str:
        if sr.match == 'exact':
            return f"序列匹配 {'→'.join(sr.sequence)}"
        if sr.match == 'missing':
            return f"缺事件 {sr.target}"
        if sr.match == 'repeated':
            return f"事件 {sr.target} 重复≥{sr.min_count}次"
        if sr.match == 'complete':
            if self._group_of:
                return "完整流程 (期望序列+组完备)"
            return "完整流程 (期望序列走完)"
        return "兜底判定"

    def _on_settle(self, events):
        """结算点: 校验事件顺序 (可选, 组感知口径) 并重置本轮序列与期望位指针。"""
        if self.cfg.seq_enabled and not self._order_matches(self._confirmed_seq):
            events.append({
                'action': 'sequence_violation',
                'expected': list(self.cfg.seq_order),
                'actual': list(self._confirmed_seq),
                'event_id': self.cfg.seq_event_id,
            })
        self._confirmed_seq = []
        self._strict_pos = 0
