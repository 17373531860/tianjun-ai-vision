"""区域事件模式 (region_events) 判定引擎 —— 纯逻辑层单元测试.

覆盖 backend/api/source_region_events.py:
  - 配置解析: 合法/缺段/关闭/规则字段校验/重名/顺序校验引用未知事件
  - overlap 规则: and/or 区域组合、连续 N 帧确认、中断容忍 M、episode 闭合、
    手部约束、按类别置信度过滤
  - region_exit 规则: 帧间 IoU 关联、进区满帧后消失确认、区域外常驻对象排除、
    闪现对象排除、多对象并存
  - 顺序校验: 正序无事、乱序产出 violation、开关关闭零产出
  - TP 真实标定多边形 smoke (非凸 C 区)
"""
from __future__ import annotations

import pytest

from backend.api.source_region_events import (
    RegionEventEngine,
    parse_region_events,
)


# 简化矩形区域 (归一化)
A_RECT = [[0.5, 0.4], [0.9, 0.4], [0.9, 1.0], [0.5, 1.0]]     # 主操作台面
C_RECT = [[0.85, 0.0], [1.0, 0.0], [1.0, 1.0], [0.85, 1.0]]   # 下料出口

# TP 现场真实标定 (客户带话原文, 非凸)
TP_AB = [[0.50, 0.44], [0.86, 0.40], [0.875, 0.72], [0.86, 0.97], [0.52, 0.97]]
TP_C = [[0.84, 0.02], [1.0, 0.02], [1.0, 0.98], [0.87, 0.98], [0.92, 0.60], [0.875, 0.30]]


def det(label, cx, cy, w=0.1, h=0.1, conf=0.9):
    return {'label': label, 'confidence': conf,
            'x': cx - w / 2, 'y': cy - h / 2, 'w': w, 'h': h}


def hardness_rule(**kw):
    base = {'id': 'r1', 'name': '测硬度', 'type': 'overlap',
            'subject_label': '测硬度笔', 'object_label': '工件',
            'region': A_RECT, 'region_mode': 'and', 'min_frames': 15}
    base.update(kw)
    return base


def scan_rule(**kw):
    base = {'id': 'r2', 'name': '扫码', 'type': 'overlap',
            'subject_label': '扫码枪', 'object_label': '工件',
            'region': A_RECT, 'region_mode': 'or', 'min_frames': 10}
    base.update(kw)
    return base


def unload_rule(**kw):
    base = {'id': 'r3', 'name': '下工件', 'type': 'region_exit',
            'subject_label': '工件', 'region': C_RECT,
            'min_frames': 3, 'gone_frames': 8}
    base.update(kw)
    return base


def build(rules, **section):
    cfg = {'enabled': True, 'gap_tolerance_frames': 3, 'rules': rules}
    cfg.update(section)
    parsed = parse_region_events({'region_events': cfg})
    assert parsed is not None
    return RegionEventEngine(parsed)


def feed(engine, frames, t0=0.0, dt=0.04):
    """逐帧喂入, 返回 [(帧序, 事件), ...]"""
    out = []
    for i, dets in enumerate(frames):
        for ev in engine.process_frame(dets, t0 + i * dt):
            out.append((i, ev))
    return out


# 常用画面元素
PEN_ON_WORK = [det('测硬度笔', 0.7, 0.7, w=0.04, h=0.04), det('工件', 0.7, 0.7, w=0.2, h=0.2)]
WORK_ONLY = [det('工件', 0.7, 0.7, w=0.2, h=0.2)]


# ============================================================
# 配置解析
# ============================================================

def test_parse_valid_full():
    cfg = parse_region_events({'region_events': {
        'enabled': True,
        'class_conf': {'扫码枪': 0.45, '测硬度笔': 0.25, '工件': 0.5, '手': 0.35},
        'gap_tolerance_frames': 3,
        'rules': [hardness_rule(), scan_rule(), unload_rule()],
        'sequence_check': {'enabled': True, 'order': ['测硬度', '扫码', '下工件']},
    }})
    assert cfg is not None
    assert len(cfg.rules) == 3
    assert cfg.class_conf['测硬度笔'] == 0.25
    assert cfg.gap_tolerance == 3
    # 默认值: overlap 不结算, exit 结算
    assert cfg.rules[0].settle is False
    assert cfg.rules[2].settle is True
    assert cfg.seq_enabled and cfg.seq_order == ['测硬度', '扫码', '下工件']


def test_parse_absent_or_disabled():
    assert parse_region_events({}) is None
    assert parse_region_events({'region_events': {'enabled': False,
                                                  'rules': [unload_rule()]}}) is None
    assert parse_region_events({'region_events': {'enabled': True, 'rules': []}}) is None


@pytest.mark.parametrize('bad_rule, msg', [
    (hardness_rule(type='fly'), 'type'),
    (hardness_rule(name=''), 'name'),
    (hardness_rule(object_label=None), 'object_label'),
    (unload_rule(region=None), 'region'),
    (hardness_rule(region=[[0.1, 0.1], [0.2, 0.2]]), '多边形'),
    (hardness_rule(region_mode='xor'), 'region_mode'),
])
def test_parse_invalid_rule(bad_rule, msg):
    with pytest.raises(ValueError, match=msg):
        parse_region_events({'region_events': {'enabled': True, 'rules': [bad_rule]}})


def test_parse_duplicate_names():
    with pytest.raises(ValueError, match='重复'):
        parse_region_events({'region_events': {
            'enabled': True, 'rules': [hardness_rule(), hardness_rule(id='r9')]}})


def test_parse_sequence_unknown_name():
    with pytest.raises(ValueError, match='sequence_check'):
        parse_region_events({'region_events': {
            'enabled': True, 'rules': [hardness_rule()],
            'sequence_check': {'enabled': True, 'order': ['测硬度', '幽灵事件']}}})


# ============================================================
# overlap 规则
# ============================================================

def test_hardness_confirm_and_close():
    eng = build([hardness_rule()])
    # 15 帧笔压工件 → 第 15 帧 (idx 14) 确认
    events = feed(eng, [PEN_ON_WORK] * 15)
    assert len(events) == 1
    idx, ev = events[0]
    assert idx == 14 and ev['action'] == 'confirmed'
    assert ev['rule_name'] == '测硬度' and ev['settle'] is False
    assert ev['start_ts'] == 0.0
    # 笔离开: 容忍 3 帧, 第 4 帧空缺时闭合
    events = feed(eng, [WORK_ONLY] * 5, t0=15 * 0.04)
    closed = [(i, e) for i, e in events if e['action'] == 'closed']
    assert len(closed) == 1
    idx, ev = closed[0]
    assert idx == 3 and ev['rule_name'] == '测硬度'
    assert ev['frames'] == 15
    assert ev['end_ts'] == pytest.approx(14 * 0.04)


def test_gap_tolerance_keeps_count():
    eng = build([hardness_rule()])
    # 10 帧命中 + 3 帧漏检 (≤M=3 不清零) + 5 帧命中 → 第 5 帧补足 15 帧确认
    frames = [PEN_ON_WORK] * 10 + [WORK_ONLY] * 3 + [PEN_ON_WORK] * 5
    events = feed(eng, frames)
    confirmed = [e for _, e in events if e['action'] == 'confirmed']
    assert len(confirmed) == 1
    assert events[0][0] == 17  # 10 + 3 + 5 帧处


def test_gap_exceeded_resets_before_confirm():
    eng = build([hardness_rule()])
    # 10 帧命中后漏 4 帧 (>M) → episode 作废且未确认, 不产出任何事件
    frames = [PEN_ON_WORK] * 10 + [WORK_ONLY] * 6
    assert feed(eng, frames) == []


def test_require_hand_constraint():
    eng = build([hardness_rule(require_label='手')])
    # 无手: 永不确认 (笔搁在工件上的误报被压掉)
    assert feed(eng, [PEN_ON_WORK] * 30) == []
    # 有手且与笔相交: 正常确认
    with_hand = PEN_ON_WORK + [det('手', 0.71, 0.71, w=0.1, h=0.1)]
    events = feed(build([hardness_rule(require_label='手')]), [with_hand] * 15)
    assert [e['action'] for _, e in events] == ['confirmed']


def test_region_and_vs_or():
    # 重叠发生在 A 区外 (0.2, 0.2)
    outside = [det('扫码枪', 0.2, 0.2), det('工件', 0.2, 0.2, w=0.2, h=0.2)]
    # and 模式: 区外重叠不算
    assert feed(build([scan_rule(region_mode='and')]), [outside] * 20) == []
    # or 模式: 重叠即可 (不要求进区)
    events = feed(build([scan_rule()]), [outside] * 10)
    assert [e['rule_name'] for _, e in events] == ['扫码']
    # or 模式: 不重叠但枪中心在区内也算
    gun_in_region = [det('扫码枪', 0.7, 0.7)]
    events = feed(build([scan_rule()]), [gun_in_region] * 10)
    assert [e['action'] for _, e in events] == ['confirmed']


def test_min_overlap_ratio_gate():
    """重叠深度门槛: 贴边浅交叠不算, 压上去 (深交叠) 才算 (治静置工具误判)。"""
    # 笔框 0.04 见方, 中心 (0.818, 0.7): 与工件 (右缘 0.8) 只贴到一条边 (深度 5%)
    shallow = [det('测硬度笔', 0.818, 0.7, w=0.04, h=0.04),
               det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    eng = build([hardness_rule(min_overlap_ratio=0.3)])
    assert feed(eng, [shallow] * 30) == []
    # 无门槛的老语义: 贴边即算 (确认产出)
    events = feed(build([hardness_rule()]), [shallow] * 15)
    assert [e['action'] for _, e in events] == ['confirmed']
    # 笔整支压在工件内: 深度 1.0 ≥ 0.3, 正常确认
    events = feed(build([hardness_rule(min_overlap_ratio=0.3)]), [PEN_ON_WORK] * 15)
    assert [e['action'] for _, e in events] == ['confirmed']


def test_object_margin_bridges_edge_gap():
    """目标框扩边: 动作发生在目标框边缘外一点点 (TP #35 扫工件下沿条码,
    枪框与工件框物理接触但几何不相交) → 扩边 0.02 桥接后正常确认。
    纯空间几何量, 与帧率无关。"""
    # 笔框 y:0.805~0.845, 工件框下缘 0.8 → 间隙 0.005 (框不相交)
    gap_pen = [det('测硬度笔', 0.7, 0.825, w=0.04, h=0.04),
               det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    # 不扩边的老语义: 框不相交 → 永不确认
    assert feed(build([hardness_rule()]), [gap_pen] * 30) == []
    # 扩边 0.02 ≥ 间隙 0.005: 桥接成功, 正常确认
    events = feed(build([hardness_rule(object_margin=0.02)]), [gap_pen] * 15)
    assert [e['action'] for _, e in events] == ['confirmed']
    # 间隙 0.03 > 扩边 0.02: 桥不过去, 不误伤远处工具
    far_pen = [det('测硬度笔', 0.7, 0.85, w=0.04, h=0.04),
               det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    assert feed(build([hardness_rule(object_margin=0.02)]), [far_pen] * 30) == []


def test_object_margin_depth_gate_uses_original_box():
    """扩边只参与"是否相交"; 深度门槛仍按原始目标框算 (扩边命中但原框
    不交 → 深度 0, 被深度门槛拦下), 避免扩边稀释"必须压进去"的语义。"""
    gap_pen = [det('测硬度笔', 0.7, 0.825, w=0.04, h=0.04),
               det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    eng = build([hardness_rule(object_margin=0.02, min_overlap_ratio=0.3)])
    assert feed(eng, [gap_pen] * 30) == []
    # 真压进去 (整支笔在工件内, 深度 1.0): 扩边+深度门槛同配也正常确认
    eng = build([hardness_rule(object_margin=0.02, min_overlap_ratio=0.3)])
    events = feed(eng, [PEN_ON_WORK] * 15)
    assert [e['action'] for _, e in events] == ['confirmed']


def test_object_margin_parse_validation():
    with pytest.raises(ValueError, match='object_margin'):
        parse_region_events({'region_events': {
            'enabled': True,
            'rules': [hardness_rule(object_margin='wide')],
        }})
    # 超上限收敛到 0.2, 负值收敛到 0
    cfg = parse_region_events({'region_events': {
        'enabled': True, 'rules': [hardness_rule(object_margin=0.5)]}})
    assert cfg.rules[0].object_margin == pytest.approx(0.2)
    cfg = parse_region_events({'region_events': {
        'enabled': True, 'rules': [hardness_rule(object_margin=-1)]}})
    assert cfg.rules[0].object_margin == 0.0


def test_gone_seconds_merges_split_action():
    """消失确认秒数: 真动作中途被遮挡断开 (超全局帧容忍但 ≤ gone_seconds),
    episode 不断、只确认一次, 闭合携带完整起止时间。"""
    # dt=0.04s: 断 10 帧 = 0.4s, 超全局容忍 3 帧, 但 < gone_seconds=1.0
    frames = [PEN_ON_WORK] * 15 + [WORK_ONLY] * 10 + [PEN_ON_WORK] * 5 \
        + [WORK_ONLY] * 30
    events = feed(build([hardness_rule(gone_seconds=1.0)]), frames)
    confirmed = [e for _, e in events if e['action'] == 'confirmed']
    closed = [e for _, e in events if e['action'] == 'closed']
    assert len(confirmed) == 1 and len(closed) == 1
    assert closed[0]['end_ts'] == pytest.approx(29 * 0.04)  # 第二段末帧
    # 不配 gone_seconds 的老语义: 同样帧序列断成两段、确认两次
    events = feed(build([hardness_rule(min_frames=5),
                         scan_rule(), unload_rule()],
                        dedup_consecutive=False),
                  [PEN_ON_WORK] * 5 + [WORK_ONLY] * 10 + [PEN_ON_WORK] * 5
                  + [WORK_ONLY] * 10)
    confirmed = [e for _, e in events if e['action'] == 'confirmed']
    assert len(confirmed) == 2


def test_min_move_gate():
    """位移门槛: 静置工具 (只有检测抖动) 不确认, 拿起来挪动过才确认。

    确认时机: 满帧数后位移仍不够则挂起, 后续帧位移达标即刻补确认。
    """
    def pen_at(cx):
        return [det('测硬度笔', cx, 0.7, w=0.04, h=0.04),
                det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    # 静置: 中心只在 ±0.005 抖动, 轨迹包络 ~0.01 < 0.04 → 永不确认
    jitter = [pen_at(0.7 + (0.005 if i % 2 else -0.005)) for i in range(60)]
    assert feed(build([hardness_rule(min_move=0.04)]), jitter) == []
    # 真动作: 满帧后继续挪动, 位移跨过 0.04 时补确认 (episode 只确认一次)
    moving = [pen_at(0.68 + i * 0.004) for i in range(30)] + [WORK_ONLY] * 10
    events = feed(build([hardness_rule(min_move=0.04)]), moving)
    kinds = [e['action'] for _, e in events]
    assert kinds == ['confirmed', 'closed']
    # 不配门槛的老语义: 静置抖动照样确认
    events = feed(build([hardness_rule()]), jitter[:15])
    assert [e['action'] for _, e in events] == ['confirmed']


def test_min_seconds_decouples_from_fps():
    """秒基确认门槛: 同一个 0.6s 的真动作, 30fps 和 10fps 下都要确认。

    帧数门槛 (min_frames=15) 在 10fps 下 0.6s 只有 6 帧, 会漏; 配了
    min_seconds=0.3 后按命中跨度判定, 帧率无关。
    """
    # 30fps (dt≈0.033): 0.6s ≈ 18 帧
    frames_30 = [PEN_ON_WORK] * 18 + [WORK_ONLY] * 10
    events = feed(build([hardness_rule(min_seconds=0.3)]), frames_30, dt=1 / 30)
    assert [e['action'] for _, e in events] == ['confirmed', 'closed']
    # 10fps (dt=0.1): 0.6s = 6 帧 < min_frames=15, 帧数语义会漏, 秒基不漏
    frames_10 = [PEN_ON_WORK] * 6 + [WORK_ONLY] * 10
    events = feed(build([hardness_rule(min_seconds=0.3)]), frames_10, dt=0.1)
    assert [e['action'] for _, e in events] == ['confirmed', 'closed']
    # 对照: 不配秒基, 10fps 下 6 帧 < 15 帧 → 确认不了
    events = feed(build([hardness_rule()]), frames_10, dt=0.1)
    assert [e['action'] for _, e in events] == []


def test_min_seconds_blocks_short_flash():
    """短于门槛的瞬时满足 (手扫过停放工具 0.15s) 不确认, 不论帧率多高。"""
    # 60fps 下 0.15s = 9 帧, 帧数够多但跨度不够 0.3s → 不确认
    flash = [PEN_ON_WORK] * 9 + [WORK_ONLY] * 30
    assert feed(build([hardness_rule(min_seconds=0.3)]), flash, dt=1 / 60) == []


def test_min_seconds_floor_three_hits():
    """秒基门槛下保留 3 帧硬下限: 零星 1~2 帧杂散框即使跨度够长也不确认。"""
    # 2 帧命中相隔 0.5s (> min_seconds=0.3), 中间靠 gone_seconds 桥接不断开,
    # 但命中帧数只有 2 < 3 → 不确认
    frames = [PEN_ON_WORK] + [WORK_ONLY] * 5 + [PEN_ON_WORK] + [WORK_ONLY] * 10
    events = feed(build([hardness_rule(min_seconds=0.3, gone_seconds=2.0)]),
                  frames, dt=0.1)
    assert [e['action'] for _, e in events] == []


def test_min_seconds_invalid_raises():
    with pytest.raises(ValueError, match='min_seconds'):
        parse_region_events({'region_events': {
            'enabled': True,
            'rules': [hardness_rule(min_seconds='fast')],
        }})


def test_interrupt_on_other_action_splits_episodes():
    """动作互斥打断 (TP 复检场景 82~90s 实测复刻): 测硬度→扫码→测硬度→扫码,
    两次扫码断开间隔 < 消失确认秒数, 不打断会被桥接成一次 → 序列少一步 →
    复检误判 NG。另一动作确认必须立即切断进行中的 episode。
    """
    pen = det('测硬度笔', 0.7, 0.7, w=0.04, h=0.04)
    gun = det('扫码枪', 0.62, 0.55, w=0.1, h=0.1)
    work = det('工件', 0.7, 0.7, w=0.2, h=0.2)
    frames = (
        [[work, pen]] * 20        # 测硬度#1 (min_frames=15)
        + [[work, gun]] * 12      # 扫码#1 (min_frames=10)
        + [[work, pen]] * 20      # 测硬度#2 — 其间扫码断开 0.8s < gone_seconds 1.5s
        + [[work, gun]] * 12      # 扫码#2 — 不打断会并进扫码#1 的 episode
        + [[work]] * 30
    )
    eng = build([hardness_rule(gone_seconds=1.5), scan_rule(gone_seconds=1.5)],
                dedup_consecutive=True)
    confirmed = [e['rule_name'] for _, e in feed(eng, frames)
                 if e['action'] == 'confirmed']
    assert confirmed == ['测硬度', '扫码', '测硬度', '扫码'], confirmed


def test_interrupt_closes_confirmed_episode_with_true_span():
    """打断时已确认的 episode 要产出闭合动作 (步骤落库), 起止时间取真实命中区间。"""
    pen = det('测硬度笔', 0.7, 0.7, w=0.04, h=0.04)
    gun = det('扫码枪', 0.62, 0.55, w=0.1, h=0.1)
    work = det('工件', 0.7, 0.7, w=0.2, h=0.2)
    # 扫码先确认, 随后测硬度确认 → 扫码被打断闭合
    frames = [[work, gun]] * 12 + [[work, gun, pen]] * 20
    eng = build([hardness_rule(), scan_rule()])
    closed = [(i, e) for i, e in feed(eng, frames) if e['action'] == 'closed']
    assert len(closed) == 1
    i, ev = closed[0]
    assert ev['rule_name'] == '扫码'
    assert ev['end_ts'] >= ev['start_ts']
    # 闭合动作发生在测硬度确认帧 (12+15-1=26), 而不是等消失容忍超时
    assert i == 26


def test_snapshot_exposes_in_progress_episode():
    """快照给 Monitor 步骤面板驱动"进行中"高亮 (v3.32): 命中累计中即在场,
    带 episode 起点 (in-flight PT 计算基准); 消失确认后复位。"""
    engine = build([hardness_rule(min_frames=10)])
    for i in range(4):
        engine.process_frame(PEN_ON_WORK, i * 0.04)
    r = engine.snapshot()['rules'][0]
    assert r['in_progress'] is True
    assert r['episode_start_ts'] == 0.0
    assert r['confirmed'] is False
    # 消失超过容忍帧 → episode 复位, 进行中态归零
    for i in range(4, 12):
        engine.process_frame(WORK_ONLY, i * 0.04)
    r = engine.snapshot()['rules'][0]
    assert r['in_progress'] is False
    assert r['episode_start_ts'] is None


def test_confirmed_event_carries_subject_bbox():
    """确认动作携带主体框 (执行层裁 SOP 卡片缩略图用, v3.32)。"""
    events = feed(build([hardness_rule(min_frames=5)]), [PEN_ON_WORK] * 6)
    confirmed = [e for _, e in events if e['action'] == 'confirmed']
    assert len(confirmed) == 1
    subj = confirmed[0]['subject']
    assert subj is not None and subj['label'] == '测硬度笔'
    assert all(k in subj for k in ('x', 'y', 'w', 'h'))


def test_min_move_immune_to_occlusion_jump():
    """遮挡形变免疫 (TP 现场 25.75s 实测复刻): 手划过静置工具, 检测框被切小
    → 框中心单帧跳变 0.06 → 不能算位移 (中位数平滑吸收瞬态)。

    对照组: 同样幅度的位移持续多帧 (真拿起挪动) → 正常确认。
    """
    def pen_at(cx, cy=0.7):
        return [det('测硬度笔', cx, cy, w=0.04, h=0.04),
                det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    # 静置 20 帧 + 遮挡瞬态跳变 2 帧 + 回位 20 帧 → 平滑后包络不动, 不确认
    occluded = [pen_at(0.7)] * 20 + [pen_at(0.76)] * 2 + [pen_at(0.7)] * 20
    assert feed(build([hardness_rule(min_move=0.04)]), occluded) == []
    # 同样跳到 0.76 但持续 10 帧 (真挪过去了) → 中位数跟上 → 确认
    moved = [pen_at(0.7)] * 20 + [pen_at(0.76)] * 10
    events = feed(build([hardness_rule(min_move=0.04)]), moved)
    assert [e['action'] for _, e in events] == ['confirmed']


def test_dedup_consecutive_default_on():
    """连续同动作去重 (默认开): 同名动作紧接着再次确认被静默吸收,
    被另一动作隔开后允许重计 (交错重做的复检场景不受影响)。"""
    pen = PEN_ON_WORK
    gun = [det('扫码枪', 0.7, 0.7, w=0.08, h=0.08),
           det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    blank = [WORK_ONLY] * 10  # 超全局容忍, episode 断开
    eng = build([hardness_rule(min_frames=5), scan_rule(min_frames=5),
                 unload_rule()])
    # 测硬度 ×2 连续 → 第二次被吸收; 扫码隔开后测硬度 #3 重计
    frames = ([pen] * 5 + blank + [pen] * 5 + blank
              + [gun] * 5 + blank + [pen] * 5 + blank)
    events = feed(eng, frames)
    confirmed = [e['rule_name'] for _, e in events if e['action'] == 'confirmed']
    assert confirmed == ['测硬度', '扫码', '测硬度']
    # 被吸收的 episode 不产出闭合动作 (不落步骤)
    closed = [e['rule_name'] for _, e in events if e['action'] == 'closed']
    assert closed == ['测硬度', '扫码', '测硬度']


def test_class_conf_filter():
    eng = build([hardness_rule()], class_conf={'测硬度笔': 0.25})
    weak_pen = [det('测硬度笔', 0.7, 0.7, w=0.04, h=0.04, conf=0.2),
                det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    assert feed(eng, [weak_pen] * 30) == []
    ok_pen = [det('测硬度笔', 0.7, 0.7, w=0.04, h=0.04, conf=0.3),
              det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    events = feed(build([hardness_rule()], class_conf={'测硬度笔': 0.25}), [ok_pen] * 15)
    assert [e['action'] for _, e in events] == ['confirmed']


# ============================================================
# region_exit 规则
# ============================================================

def test_unload_confirm():
    eng = build([unload_rule()])
    # 工件滑入 C 区 3 帧 (缓慢移动, 帧间 IoU 足够) → 消失 8 帧 → 确认+结算
    frames = [[det('工件', 0.90 + i * 0.005, 0.5, w=0.12, h=0.12)] for i in range(3)]
    frames += [[]] * 8
    events = feed(eng, frames)
    assert len(events) == 1
    idx, ev = events[0]
    assert idx == 10 and ev['action'] == 'confirmed'
    assert ev['rule_name'] == '下工件' and ev['settle'] is True


def test_resident_workpiece_outside_region_excluded():
    # 打码位夹具常驻工件 (区外) 一直可见 → 永不产出; 消失也不产出
    eng = build([unload_rule()])
    frames = [[det('工件', 0.27, 0.5)]] * 20 + [[]] * 10
    assert feed(eng, frames) == []


def test_flicker_in_region_excluded():
    # 区内闪现 1 帧 (< min_frames=3) 后消失 → 不产出
    eng = build([unload_rule()])
    frames = [[det('工件', 0.92, 0.5)]] + [[]] * 10
    assert feed(eng, frames) == []


def test_two_workpieces_independent_tracks():
    eng = build([unload_rule()])
    resident = det('工件', 0.27, 0.5)
    frames = [[resident, det('工件', 0.92, 0.5, w=0.12, h=0.12)] for _ in range(4)]
    frames += [[resident]] * 10  # 区内那件消失, 常驻件仍在
    events = feed(eng, frames)
    assert len(events) == 1 and events[0][1]['rule_name'] == '下工件'
    snap = eng.snapshot()
    unload_snap = next(r for r in snap['rules'] if r['name'] == '下工件')
    assert unload_snap['total'] == 1 and unload_snap['active_tracks'] == 1


# ============================================================
# 顺序校验 + 全流程
# ============================================================

def _full_engine(seq_enabled=True):
    return build(
        [hardness_rule(), scan_rule(), unload_rule()],
        sequence_check={'enabled': seq_enabled,
                        'order': ['测硬度', '扫码', '下工件'], 'event_id': 9},
    )


def _cycle_frames(scan_first=False):
    """一个完整工位循环的帧序列: 测硬度 → 扫码 → 下工件 (可交换前两步)."""
    work = det('工件', 0.7, 0.7, w=0.2, h=0.2)
    hardness = [[det('测硬度笔', 0.7, 0.7, w=0.04, h=0.04), work]] * 15
    idle = [[work]] * 5  # 超过容忍帧数, 让上一 episode 闭合
    scan = [[det('扫码枪', 0.7, 0.65), work]] * 10
    unload = [[det('工件', 0.90 + i * 0.005, 0.5, w=0.12, h=0.12)] for i in range(3)]
    gone = [[]] * 8
    steps = [scan, idle, hardness] if scan_first else [hardness, idle, scan]
    return steps[0] + steps[1] + steps[2] + idle + unload + gone


def test_full_cycle_in_order_no_violation():
    events = [e for _, e in feed(_full_engine(), _cycle_frames())]
    actions = [e['action'] for e in events]
    assert actions.count('sequence_violation') == 0
    confirmed = [e['rule_name'] for e in events if e['action'] == 'confirmed']
    assert confirmed == ['测硬度', '扫码', '下工件']
    # 两个 overlap 事件都留下带起止时间的闭合记录
    closed = [e['rule_name'] for e in events if e['action'] == 'closed']
    assert closed == ['测硬度', '扫码']


def test_full_cycle_out_of_order_violation():
    events = [e for _, e in feed(_full_engine(), _cycle_frames(scan_first=True))]
    violations = [e for e in events if e['action'] == 'sequence_violation']
    assert len(violations) == 1
    assert violations[0]['actual'] == ['扫码', '测硬度', '下工件']
    assert violations[0]['event_id'] == 9


def test_sequence_disabled_no_violation():
    events = [e for _, e in feed(_full_engine(seq_enabled=False),
                                 _cycle_frames(scan_first=True))]
    assert all(e['action'] != 'sequence_violation' for e in events)


def test_sequence_resets_between_cycles():
    eng = _full_engine()
    feed(eng, _cycle_frames())  # 第一循环正序结算
    events = [e for _, e in feed(eng, _cycle_frames(), t0=100.0)]
    assert all(e['action'] != 'sequence_violation' for e in events)


# ============================================================
# TP 真实标定多边形 smoke
# ============================================================

def test_tp_real_polygons():
    rules = [hardness_rule(region=TP_AB), scan_rule(region=TP_AB),
             unload_rule(region=TP_C)]
    eng = build(rules, sequence_check={'enabled': False, 'order': []})
    # 台面中心 (0.68, 0.7) 在 AB 区内: 测硬度确认
    work = det('工件', 0.68, 0.7, w=0.2, h=0.2)
    frames = [[det('测硬度笔', 0.68, 0.7, w=0.04, h=0.04), work]] * 15
    events = feed(eng, frames)
    assert [e['rule_name'] for _, e in events] == ['测硬度']
    # C 区带面点 (0.95, 0.1) 在非凸多边形内: 下工件确认
    # (上一段测硬度 episode 的 closed 动作在本段头部帧产出, 属正常, 只看 confirmed)
    frames = [[det('工件', 0.95, 0.1, w=0.06, h=0.08)]] * 3 + [[]] * 8
    events = feed(eng, frames, t0=10.0)
    assert [e['rule_name'] for _, e in events
            if e['action'] == 'confirmed'] == ['下工件']
    # 打码位夹具 (0.27, 0.5) 不在任何区: 永不产出
    frames = [[det('工件', 0.27, 0.5)]] * 20 + [[]] * 10
    assert feed(eng, frames, t0=20.0) == []


def test_reset_clears_state():
    eng = build([hardness_rule()])
    feed(eng, [PEN_ON_WORK] * 15)
    eng.reset()
    snap = eng.snapshot()
    assert snap['rules'][0]['total'] == 0
    assert snap['pending_sequence'] == []


# ============================================================
# 结算判定 (settlement_rules): 缺事件/重复事件/序列匹配 → 自定义结算事件
# ============================================================

SETTLE_RULES = [
    {'match': 'exact', 'sequence': ['测硬度', '扫码', '下工件'], 'event_id': 1},
    {'match': 'missing', 'target': '测硬度', 'event_id': 2},
    {'match': 'repeated', 'target': '扫码', 'min_count': 2, 'event_id': 2},
]


def _settle_engine(settlement_rules=SETTLE_RULES, **section):
    return build([hardness_rule(), scan_rule(), unload_rule()],
                 settlement_rules=settlement_rules, **section)


def _settle_action(events):
    settles = [e for e in events if e['action'] == 'confirmed' and e['settle']]
    assert len(settles) == 1
    return settles[0]


def test_settlement_exact_sequence_hits_ok():
    events = [e for _, e in feed(_settle_engine(), _cycle_frames())]
    act = _settle_action(events)
    assert act['settle_event_id'] == 1
    assert '序列匹配' in act['settle_reason']


def test_settlement_missing_step_hits_ng():
    """没测硬度就下料 → 缺事件规则命中 NG。"""
    work = [det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    gun = work + [det('扫码枪', 0.66, 0.6, w=0.08, h=0.08)]
    frames = [work] * 2 + [gun] * 10 + [work] * 5 \
        + [[det('工件', 0.92, 0.5, w=0.08, h=0.1)]] * 3 + [[]] * 8
    events = [e for _, e in feed(_settle_engine(), frames)]
    act = _settle_action(events)
    assert act['settle_event_id'] == 2
    assert '缺事件 测硬度' in act['settle_reason']


def test_settlement_repeated_step_hits_ng():
    """扫码两次 → 重复事件规则命中 NG (优先级在 exact 之后, 序列不等长自然让位)。

    连续同名重复默认会被去重吸收 (dedup_consecutive), 这里显式关掉,
    验证关掉后连续重复仍逐次计入、repeated 判定仍按次数抓。
    """
    work = [det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    pen = work + [det('测硬度笔', 0.7, 0.7, w=0.04, h=0.04)]
    gun = work + [det('扫码枪', 0.66, 0.6, w=0.08, h=0.08)]
    frames = ([work] * 2 + [pen] * 15 + [work] * 5      # 测硬度
              + [gun] * 10 + [work] * 5                 # 扫码 #1
              + [gun] * 10 + [work] * 5                 # 扫码 #2
              + [[det('工件', 0.92, 0.5, w=0.08, h=0.1)]] * 3 + [[]] * 8)
    events = [e for _, e in feed(_settle_engine(dedup_consecutive=False), frames)]
    act = _settle_action(events)
    assert act['settle_event_id'] == 2
    assert '重复' in act['settle_reason']


def test_settlement_priority_first_match_wins():
    """缺测硬度且重复扫码时: 排前面的 missing 规则先赢。"""
    work = [det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    gun = work + [det('扫码枪', 0.66, 0.6, w=0.08, h=0.08)]
    frames = ([work] * 2 + [gun] * 10 + [work] * 5 + [gun] * 10 + [work] * 5
              + [[det('工件', 0.92, 0.5, w=0.08, h=0.1)]] * 3 + [[]] * 8)
    events = [e for _, e in feed(_settle_engine(), frames)]
    act = _settle_action(events)
    assert act['settle_event_id'] == 2
    assert '缺事件' in act['settle_reason']


def test_settlement_no_rules_zero_diff():
    """未配置结算判定: 不带 settle_event_id, 走结算规则默认事件 (零差异)。"""
    events = [e for _, e in feed(_settle_engine(settlement_rules=[]),
                                 _cycle_frames())]
    act = _settle_action(events)
    assert 'settle_event_id' not in act


def test_settlement_always_catches_rest():
    """兜底规则: 乱序 (不缺不重但非标准) 也能自定义结算事件。"""
    with_always = SETTLE_RULES + [{'match': 'always', 'event_id': 3}]
    work = [det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    pen = work + [det('测硬度笔', 0.7, 0.7, w=0.04, h=0.04)]
    gun = work + [det('扫码枪', 0.66, 0.6, w=0.08, h=0.08)]
    # 先扫码后测硬度: 序列 [扫码,测硬度,下工件] — 不缺、不重、非标准序 → 兜底
    frames = ([work] * 2 + [gun] * 10 + [work] * 5 + [pen] * 15 + [work] * 5
              + [[det('工件', 0.92, 0.5, w=0.08, h=0.1)]] * 3 + [[]] * 8)
    events = [e for _, e in feed(_settle_engine(settlement_rules=with_always),
                                 frames)]
    act = _settle_action(events)
    assert act['settle_event_id'] == 3
    assert '兜底' in act['settle_reason']


def test_settlement_no_match_falls_back():
    """全不命中 (只配 exact 且序列不符): 回退默认, 不带 settle_event_id。"""
    only_exact = [{'match': 'exact', 'sequence': ['测硬度', '扫码', '下工件'],
                   'event_id': 1}]
    work = [det('工件', 0.7, 0.7, w=0.2, h=0.2)]
    gun = work + [det('扫码枪', 0.66, 0.6, w=0.08, h=0.08)]
    frames = [work] * 2 + [gun] * 10 + [work] * 5 \
        + [[det('工件', 0.92, 0.5, w=0.08, h=0.1)]] * 3 + [[]] * 8
    events = [e for _, e in feed(_settle_engine(settlement_rules=only_exact),
                                 frames)]
    act = _settle_action(events)
    assert 'settle_event_id' not in act


@pytest.mark.parametrize('bad, msg', [
    ([{'match': 'fuzzy', 'event_id': 1}], 'match'),
    ([{'match': 'missing', 'target': '测硬度'}], 'event_id'),
    ([{'match': 'missing', 'target': '幽灵', 'event_id': 2}], '目标事件名'),
    ([{'match': 'exact', 'sequence': ['幽灵'], 'event_id': 1}], '未知事件名'),
])
def test_settlement_parse_invalid(bad, msg):
    with pytest.raises(ValueError, match=msg):
        build([hardness_rule(), scan_rule(), unload_rule()],
              settlement_rules=bad)


# ============================================================
# region_enter 规则 (进区即触发 / 驻留超时告警)
# ============================================================

def enter_rule(**kw):
    base = {'id': 'r4', 'name': '上料', 'type': 'region_enter',
            'subject_label': '工件', 'region': A_RECT, 'min_frames': 3}
    base.update(kw)
    return base


def test_region_enter_confirm_and_close():
    eng = build([enter_rule()])
    frames = [[det('工件', 0.7, 0.7)]] * 5 + [[]] * 5
    events = feed(eng, frames)
    confirmed = [(i, e) for i, e in events if e['action'] == 'confirmed']
    assert len(confirmed) == 1 and confirmed[0][0] == 2  # 第 3 帧确认
    closed = [e for _, e in events if e['action'] == 'closed']
    assert len(closed) == 1 and closed[0]['rule_name'] == '上料'


def test_region_enter_outside_region_never_fires():
    eng = build([enter_rule()])
    assert feed(eng, [[det('工件', 0.2, 0.2)]] * 20) == []


def test_region_enter_as_dwell_alarm():
    """驻留超时: min_frames 给大 (50 帧), 不到不响, 到了确认一次。"""
    eng = build([enter_rule(name='滞留告警', min_frames=50, event_id=7)])
    assert feed(eng, [[det('工件', 0.7, 0.7)]] * 49) == []
    events = feed(eng, [[det('工件', 0.7, 0.7)]] * 1, t0=2.0)
    assert len(events) == 1
    ev = events[0][1]
    assert ev['action'] == 'confirmed' and ev['event_id'] == 7


def test_region_enter_requires_region():
    with pytest.raises(ValueError, match='region'):
        build([enter_rule(region=None)])


# ============================================================
# 区域锚点跟随 (anchor)
# ============================================================

ANCHOR_REF = {'x': 0.6, 'y': 0.6, 'w': 0.2, 'h': 0.2}


def test_anchor_region_follows_shift():
    """锚点整体右移 0.3 后, 老位置不再命中、新位置命中。"""
    rule = enter_rule(subject_label='测硬度笔',
                      anchor={'enabled': True, 'label': '工件',
                              'ref': ANCHOR_REF, 'hold_seconds': 3.0})
    eng = build([rule])
    anchor_shifted = det('工件', 0.4, 0.7, w=0.2, h=0.2)  # 中心 x: 0.7→0.4
    # 笔在标定区域老位置 (0.7,0.7): 锚点已左移, 区域也跟着左移 → 不命中
    assert feed(eng, [[anchor_shifted, det('测硬度笔', 0.95, 0.7, w=0.04, h=0.04)]] * 5) == []
    # 笔跟随锚点移到新位置 (0.4,0.7) → 命中
    events = feed(eng, [[anchor_shifted, det('测硬度笔', 0.4, 0.7, w=0.04, h=0.04)]] * 3,
                  t0=1.0)
    assert [e['action'] for _, e in events] == ['confirmed']


def test_anchor_lost_beyond_hold_no_hit():
    """锚点消失超过沿用时长后, 区域条件一律不满足。"""
    rule = enter_rule(subject_label='测硬度笔',
                      anchor={'enabled': True, 'label': '工件',
                              'ref': ANCHOR_REF, 'hold_seconds': 0.5})
    eng = build([rule])
    # 先喂 1 帧锚点建立缓存, 之后锚点消失, ts 超过 hold → 不命中
    feed(eng, [[det('工件', 0.6, 0.7, w=0.2, h=0.2)]])
    events = feed(eng, [[det('测硬度笔', 0.7, 0.7, w=0.04, h=0.04)]] * 10, t0=10.0)
    assert events == []


def test_anchor_incomplete_degrades_to_fixed():
    """锚点缺标定框: 降级为固定区域 (不抛错), 老位置照常命中。"""
    rule = enter_rule(subject_label='测硬度笔',
                      anchor={'enabled': True, 'label': '工件'})
    eng = build([rule])
    events = feed(eng, [[det('测硬度笔', 0.7, 0.7, w=0.04, h=0.04)]] * 3)
    assert [e['action'] for _, e in events] == ['confirmed']


def test_anchor_exit_rule_follows():
    """region_exit 也支持锚点: 出口区随锚点平移后, 新位置消失才结算。"""
    rule = unload_rule(anchor={'enabled': True, 'label': '夹具',
                               'ref': ANCHOR_REF, 'hold_seconds': 5.0})
    eng = build([hardness_rule(), rule])
    fixture = det('夹具', 0.3, 0.6, w=0.2, h=0.2)  # 锚点左移 0.4 → C 区跟着左移
    # 工件走到"老 C 区" (0.92) — 已不在跟随后的出口区, 消失不结算
    frames = [[fixture, det('工件', 0.92, 0.5, w=0.08, h=0.1)]] * 4 + [[fixture]] * 10
    events = feed(eng, frames)
    assert all(e['rule_name'] != '下工件' for _, e in events)
    # 工件走到跟随后的出口区 (0.85-0.4=0.45 起) 再消失 → 结算
    frames = [[fixture, det('工件', 0.52, 0.5, w=0.08, h=0.1)]] * 4 + [[fixture]] * 10
    events = feed(eng, frames, t0=20.0)
    assert [e['rule_name'] for _, e in events if e['action'] == 'confirmed'] == ['下工件']


# ============================================================
# 监控类规则 (2026-09 出厂模板批次): region_count / region_empty / proximity
# ============================================================

def crowd_rule(**kw):
    base = {'id': 'r5', 'name': '人员聚集', 'type': 'region_count',
            'subject_label': '人', 'region': A_RECT,
            'min_count': 2, 'min_frames': 3}
    base.update(kw)
    return base


def absence_rule(**kw):
    base = {'id': 'r6', 'name': '离岗检测', 'type': 'region_empty',
            'subject_label': '人', 'region': A_RECT, 'min_frames': 3}
    base.update(kw)
    return base


def proximity_rule(**kw):
    base = {'id': 'r7', 'name': '人车距离', 'type': 'proximity',
            'subject_label': '人', 'object_label': '叉车',
            'max_distance': 0.15, 'min_frames': 3}
    base.update(kw)
    return base


PERSON_A = det('人', 0.6, 0.7, w=0.08, h=0.2)
PERSON_B = det('人', 0.8, 0.7, w=0.08, h=0.2)


def test_parse_monitoring_defaults():
    cfg = parse_region_events({'region_events': {'enabled': True, 'rules': [
        {'id': 'r1', 'name': '聚集', 'type': 'region_count',
         'subject_label': '人', 'region': A_RECT},
        {'id': 'r2', 'name': '离岗', 'type': 'region_empty',
         'subject_label': '人', 'region': A_RECT, 'min_move': 0.5},
        {'id': 'r3', 'name': '接近', 'type': 'proximity',
         'subject_label': '人', 'object_label': '叉车'},
    ]}})
    r1, r2, r3 = cfg.rules
    assert r1.min_count == 3 and r1.min_frames == 8 and r1.settle is False
    # region_empty 无主体框, 位移门槛配了也强制归零 (否则永远确认不了)
    assert r2.min_move == 0.0
    assert r3.max_distance == 0.15 and r3.min_frames == 5 and r3.settle is False


@pytest.mark.parametrize('bad_rule, msg', [
    (crowd_rule(region=None), 'region'),
    (absence_rule(region=None), 'region'),
    (proximity_rule(object_label=None), 'object_label'),
])
def test_parse_monitoring_invalid(bad_rule, msg):
    with pytest.raises(ValueError, match=msg):
        build([bad_rule])


def test_region_count_confirms_and_realarm():
    """区域内达到 min_count 持续 N 帧告警; 散开后重聚 → 再次告警 (不被去重吞)。"""
    eng = build([crowd_rule()])
    one = [[PERSON_A]] * 10                       # 只有 1 人: 永不确认
    assert feed(eng, one) == []
    both = [[PERSON_A, PERSON_B]] * 5 + [[]] * 8  # 2 人聚集 → 确认; 散开 → 闭合
    events = feed(eng, both, t0=1.0)
    confirmed = [e for _, e in events if e['action'] == 'confirmed']
    assert len(confirmed) == 1 and confirmed[0]['rule_name'] == '人员聚集'
    assert confirmed[0]['subject'] is not None    # 代表主体框供截图
    assert [e['action'] for _, e in events if e['action'] == 'closed'] == ['closed']
    # 重聚 → 第二次告警 (监控类不进动作序列, 连续同名不会被 dedup 吸收)
    events2 = feed(eng, both, t0=10.0)
    assert len([e for _, e in events2 if e['action'] == 'confirmed']) == 1


def test_region_empty_alarm_and_recovery():
    """区域无人持续 N 帧 → 离岗告警; 人回来 episode 闭合; 再离开 → 再告警。"""
    eng = build([absence_rule()])
    # 人在岗: 不告警 (完全无检测框的空帧也算"无人", 属于告警条件)
    assert feed(eng, [[PERSON_A]] * 10) == []
    away = [[]] * 5 + [[PERSON_A]] * 8            # 离岗 5 帧 → 告警; 回岗 → 闭合
    events = feed(eng, away, t0=1.0)
    confirmed = [e for _, e in events if e['action'] == 'confirmed']
    assert len(confirmed) == 1 and confirmed[0]['rule_name'] == '离岗检测'
    assert confirmed[0]['subject'] is None        # 无人无主体框, 截图走整帧兜底
    assert len([e for _, e in events if e['action'] == 'closed']) == 1
    events2 = feed(eng, [[]] * 5, t0=10.0)        # 第二次离岗 → 再次告警
    assert len([e for _, e in events2 if e['action'] == 'confirmed']) == 1


def test_region_empty_min_seconds_gate():
    """秒基门槛: 无人跨度不足秒数不告警 (30s 离岗典型配置的缩尺验证)。"""
    eng = build([absence_rule(min_seconds=0.3)])
    # dt=0.04: 5 帧跨度 0.16s < 0.3s → 不确认; 10 帧跨度 0.36s → 确认
    assert feed(eng, [[]] * 5) == []
    events = feed(eng, [[]] * 5, t0=0.2)
    assert len([e for _, e in events if e['action'] == 'confirmed']) == 1


def test_proximity_distance_gate():
    """人车中心距离 ≤ max_distance 持续 N 帧才告警; 远处不响。"""
    eng = build([proximity_rule()])
    far = [[det('人', 0.2, 0.5, w=0.08, h=0.2), det('叉车', 0.8, 0.5, w=0.3, h=0.3)]]
    assert feed(eng, far * 10) == []
    near = [[det('人', 0.7, 0.5, w=0.08, h=0.2), det('叉车', 0.8, 0.5, w=0.3, h=0.3)]]
    events = feed(eng, near * 5, t0=1.0)
    confirmed = [e for _, e in events if e['action'] == 'confirmed']
    assert len(confirmed) == 1 and confirmed[0]['rule_name'] == '人车距离'
    assert confirmed[0]['subject']['label'] == '人'


def test_monitoring_orthogonal_to_action_rules():
    """监控告警与工序动作正交: 不进动作序列、不打断进行中的动作 episode。"""
    eng = build([hardness_rule(min_frames=15), crowd_rule()])
    # 测硬度进行中 (5 帧, 未到 15 帧确认门槛) 同时人员聚集确认
    frames = [[*PEN_ON_WORK, PERSON_A, PERSON_B]] * 5
    events = feed(eng, frames)
    assert [e['rule_name'] for _, e in events if e['action'] == 'confirmed'] \
        == ['人员聚集']
    snap = eng.snapshot()
    by_name = {r['name']: r for r in snap['rules']}
    # 测硬度 episode 未被聚集确认打断 (仍在累计中)
    assert by_name['测硬度']['in_progress'] is True
    assert by_name['测硬度']['hit_frames'] == 5
    # 监控确认不进动作序列 (否则会撕碎工序结算判定)
    assert snap['pending_sequence'] == []
    # 快照对监控类型不抛 KeyError 且带确认计数
    assert by_name['人员聚集']['total'] == 1


def test_action_confirm_does_not_interrupt_monitoring():
    """反向正交: 工序动作确认时, 进行中的监控 episode 不被打断。"""
    # 离岗计时进行中 (工位区无人) + 测硬度在区域外发生 → 测硬度确认
    pen_work_outside = [det('测硬度笔', 0.2, 0.2, w=0.04, h=0.04),
                        det('工件', 0.2, 0.2, w=0.2, h=0.2)]
    eng2 = build([hardness_rule(min_frames=3, region=None), absence_rule(min_frames=30)])
    events = feed(eng2, [pen_work_outside] * 5)
    assert [e['rule_name'] for _, e in events if e['action'] == 'confirmed'] \
        == ['测硬度']
    by_name = {r['name']: r for r in eng2.snapshot()['rules']}
    # 离岗 episode (工位区无人) 仍在累计, 未被测硬度确认打断
    assert by_name['离岗检测']['in_progress'] is True
    assert by_name['离岗检测']['hit_frames'] == 5


# ============================================================
# cross_count 规则 (过线/人流计数, 逐对象轨迹): 2026-09 全量批次
# ============================================================

def flow_rule(**kw):
    base = {'id': 'r8', 'name': '人流计数', 'type': 'cross_count',
            'subject_label': '人', 'region': A_RECT,
            'min_frames': 2, 'gone_frames': 4}
    base.update(kw)
    return base


def test_cross_count_parse_defaults_and_region_required():
    cfg = parse_region_events({'region_events': {'enabled': True, 'rules': [
        {'id': 'r1', 'name': '计数', 'type': 'cross_count',
         'subject_label': '人', 'region': A_RECT},
    ]}})
    r = cfg.rules[0]
    assert r.min_frames == 2 and r.settle is False
    with pytest.raises(ValueError, match='region'):
        build([flow_rule(region=None)])


def test_cross_count_once_per_track():
    """同一对象在区内持续在场只计一次 (与 region_enter 的 episode 语义等价场景)。"""
    eng = build([flow_rule()])
    events = feed(eng, [[PERSON_A]] * 10)
    confirmed = [e for _, e in events if e['action'] == 'confirmed']
    assert len(confirmed) == 1 and confirmed[0]['rule_name'] == '人流计数'
    assert confirmed[0]['subject']['label'] == '人'
    by_name = {r['name']: r for r in eng.snapshot()['rules']}
    assert by_name['人流计数']['total'] == 1
    assert by_name['人流计数']['entered_tracks'] == 1


def test_cross_count_per_object_not_per_episode():
    """两人同时进区 → 计 2 次 (region_enter 只会计 1 次, 这是本类型存在的意义)。"""
    eng = build([flow_rule()])
    events = feed(eng, [[PERSON_A, PERSON_B]] * 5)
    assert len([e for _, e in events if e['action'] == 'confirmed']) == 2
    assert {r['name']: r for r in eng.snapshot()['rules']}['人流计数']['total'] == 2


def test_cross_count_reentry_counts_again():
    """离开消失 ≥ gone_frames 后再进区 → 新轨迹再计一次。"""
    eng = build([flow_rule()])
    frames = [[PERSON_A]] * 3 + [[]] * 6 + [[PERSON_A]] * 3
    events = feed(eng, frames)
    assert len([e for _, e in events if e['action'] == 'confirmed']) == 2


def test_cross_count_outside_region_never_counts():
    """区域外对象 (跟踪中) 永不计数。"""
    eng = build([flow_rule()])
    outside = det('人', 0.2, 0.2, w=0.08, h=0.2)   # A_RECT 之外
    assert feed(eng, [[outside]] * 10) == []
    assert {r['name']: r for r in eng.snapshot()['rules']}['人流计数']['total'] == 0


def test_cross_count_orthogonal_to_actions():
    """计数确认不进动作序列、不打断工序 episode (监控语义)。"""
    eng = build([hardness_rule(min_frames=15), flow_rule()])
    frames = [[*PEN_ON_WORK, det('人', 0.6, 0.9, w=0.08, h=0.2)]] * 5
    events = feed(eng, frames)
    assert [e['rule_name'] for _, e in events if e['action'] == 'confirmed'] \
        == ['人流计数']
    snap = eng.snapshot()
    by_name = {r['name']: r for r in snap['rules']}
    assert by_name['测硬度']['in_progress'] is True
    assert by_name['测硬度']['hit_frames'] == 5
    assert snap['pending_sequence'] == []
