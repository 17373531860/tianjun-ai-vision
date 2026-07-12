"""TP 标准回放·第二步: 检测缓存 → 项目 22 真实判定配置 → 逐周期结算流水.

用法: python replay.py [缓存路径]   (缺省 /tmp/tp_v5_dets_cache.json)

前置: 后端 8001 在跑 (读项目配置); build_cache.py 已产出缓存。
地面真值 (0707 164759.mp4, 用户口径): 只有 0:22-0:41 一个真复检,
片头 #01 是残缺周期, 其余全合格。v5.1 到位后换缓存重跑, 对照本流水看:
真复检段是否恰好两组 测硬度+扫码; 假的多余组是否消失。

附带"使用态框连续性"指标 (训练侧建议, 2026-07-12): 每个动作 episode 窗口内,
主体使用态框的最长连续中断帧数 + 总缺帧率。v5 的失效模式是使用态框在动作
中途闪断/闪出 (撕裂 episode → 多余动作对), 单帧 AP 反映不出来, 这个指标
新旧模型同轴比才贴近真实失效。
"""
import json
import sys
from collections import Counter

sys.path.insert(0, '/home/qianqian/桌面/word/tianjun-main')
import requests

from backend.api.source_region_events import RegionEventEngine, parse_region_events

EV_NAMES = {1: '合格', 2: '不良NG', 1783299935644: '复检'}
CACHE_PATH = sys.argv[1] if len(sys.argv) > 1 else '/tmp/tp_v5_dets_cache.json'

p = requests.get('http://localhost:8001/api/v1/projects/22', timeout=5).json()
cfg = parse_region_events(p['pipeline_config'])
assert cfg is not None
eng = RegionEventEngine(cfg)

cache = json.load(open(CACHE_PATH))
fps = cache['fps']
frames = cache['frames']
print(f'回放 {CACHE_PATH}: {len(frames)} 帧 @ {fps}fps')

# 停放类出场统计
parked_frames = Counter()
for dets in frames:
    for lbl in {d['label'] for d in dets}:
        parked_frames[lbl] += 1
print('各类出场帧数:', dict(parked_frames))

rule_subject = {r.name: r.subject_label for r in cfg.rules}

cycle_seq = []
cycles = []
episodes = []   # (rule_name, start_ts, end_ts) — closed 事件携带完整起止
for i, dets in enumerate(frames):
    ts = i / fps
    for ev in eng.process_frame(dets, ts):
        if ev['action'] == 'closed':
            episodes.append((ev['rule_name'], ev['start_ts'], ev['end_ts']))
            continue
        if ev['action'] != 'confirmed':
            continue
        cycle_seq.append((ev['rule_name'], ts))
        if ev.get('settle'):
            eid = ev.get('settle_event_id')
            if eid is None:
                eid = ev.get('event_id') if ev.get('event_id') is not None else 1
            cycles.append({
                'start': cycle_seq[0][1], 'end': ts,
                'seq': [n for n, _ in cycle_seq],
                'result': EV_NAMES.get(eid, str(eid)),
                'reason': ev.get('settle_reason') or '',
            })
            cycle_seq = []

def mmss(t):
    return f'{int(t)//60}:{int(t)%60:02d}'

print(f'\n共 {len(cycles)} 个周期:')
tally = Counter()
for k, c in enumerate(cycles, 1):
    tally[c['result']] += 1
    print(f"#{k:02d} {mmss(c['start'])}-{mmss(c['end'])} "
          f"{'→'.join(c['seq'])} => {c['result']} {c['reason']}")
print('\n汇总:', dict(tally))
if cycle_seq:
    print('片尾未结算残留:', [(n, mmss(t)) for n, t in cycle_seq])

# ============================================================
# 使用态框连续性 (每个动作 episode 窗口内, 主体使用态框的中断情况)
#   最长中断 = 窗口内主体框连续缺失的最大帧数
#   缺帧率   = 窗口内主体框缺失帧数 / 窗口总帧数
# ============================================================
print('\n使用态框连续性 (逐动作窗口):')
agg = {}   # label -> [(max_gap, miss_rate, n_frames)]
for rule_name, st, et in episodes:
    label = rule_subject.get(rule_name)
    if not label:
        continue
    i0, i1 = int(st * fps), min(int(et * fps) + 1, len(frames))
    if i1 - i0 < 2:
        continue
    gaps, cur_gap, miss = [], 0, 0
    for i in range(i0, i1):
        present = any(d['label'] == label for d in frames[i])
        if present:
            if cur_gap:
                gaps.append(cur_gap)
                cur_gap = 0
        else:
            cur_gap += 1
            miss += 1
    if cur_gap:
        gaps.append(cur_gap)
    n = i1 - i0
    agg.setdefault(label, []).append((max(gaps) if gaps else 0, miss / n, n))

for label, rows in agg.items():
    rows.sort(key=lambda r: -r[0])
    max_gaps = [r[0] for r in rows]
    miss_rates = [r[1] for r in rows]
    med = sorted(max_gaps)[len(max_gaps) // 2]
    print(f'  {label}: {len(rows)} 个窗口 | 最长中断帧数 中位 {med} / 最差 {max_gaps[0]} '
          f'| 平均缺帧率 {sum(miss_rates)/len(miss_rates)*100:.1f}% '
          f'| 缺帧率>20% 的窗口 {sum(1 for r in miss_rates if r > 0.2)} 个')

# 撕裂口径: 同动作相邻 episode 间隔 <3s 视为同一次物理动作被撕开,
# 合并后统计缝隙 — episode 内部连续性再好, 缝隙大照样多出动作对,
# 这个口径才正面量化"闪断撕裂"失效 (v5.1 应显著下降)
print('\n动作撕裂 (同动作相邻窗口 <3s 合并):')
by_rule = {}
for rule_name, st, et in episodes:
    by_rule.setdefault(rule_name, []).append((st, et))
for rule_name, spans in by_rule.items():
    label = rule_subject.get(rule_name)
    if not label:
        continue
    spans.sort()
    tears = []   # (缝隙秒数, 缝隙内主体缺帧率, 发生时刻)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        gap = s2 - e1
        if 0 < gap < 3.0:
            i0, i1 = int(e1 * fps), int(s2 * fps) + 1
            n = max(1, i1 - i0)
            miss = sum(1 for i in range(i0, min(i1, len(frames)))
                       if not any(d['label'] == label for d in frames[i]))
            tears.append((gap, miss / n, e1))
    if tears:
        worst = max(tears)
        detail = ' '.join(f'{mmss(t)}({g:.1f}s)' for g, _, t in tears)
        print(f'  {rule_name}: {len(tears)} 处撕裂 | 最宽缝隙 {worst[0]:.1f}s '
              f'| 缝隙内主体缺帧率均值 {sum(m for _, m, _ in tears)/len(tears)*100:.0f}% '
              f'| 时点: {detail}')
    else:
        print(f'  {rule_name}: 无撕裂')

# ============================================================
# 使用态误标率 (v5 实测的真失效轴, 2026-07-12):
#   按训练侧定稿语义"没有手接触的工具 = 停放类", 逐帧统计
#   "使用态框存在但没有任何手框与之相交"的占比。
#   v5 上这类假使用态 (如静置枪被标使用态 conf 0.6-0.87) 会触发
#   互斥打断撕裂另一动作 → 多余动作对 → 假复检。v5.1 应显著下降。
# ============================================================
def _overlaps(a, b):
    return not (a['x'] + a['w'] < b['x'] or b['x'] + b['w'] < a['x']
                or a['y'] + a['h'] < b['y'] or b['y'] + b['h'] < a['y'])

print('\n使用态误标率 (使用态框存在但无手接触, 按定稿语义应为停放类):')
for label in ('扫码枪', '测硬度笔'):
    total = nohand = dual = 0
    for dets in frames:
        boxes = [d for d in dets if d['label'] == label]
        if not boxes:
            continue
        total += 1
        hands = [d for d in dets if d['label'] == '手']
        if not any(_overlaps(b, h) for b in boxes for h in hands):
            nohand += 1
        if any(d['label'] == f'停放的{label}' and _overlaps(d, b)
               for d in dets for b in boxes):
            dual += 1
    if total:
        print(f'  {label}: 出场 {total} 帧 | 无手接触 {nohand} 帧 '
              f'({nohand/total*100:.1f}%) | 与停放框同物双框 {dual} 帧 '
              f'({dual/total*100:.1f}%)')
