"""TP 参数矩阵: 扫码扩边 object_margin × 秒基确认 min_seconds 组合回放.

目的 (2026-07-13):
  1. #35 (10:25-10:34) 扫工件下沿条码, 枪框不压进工件框 → 扫码规则加
     object_margin 桥接 (纯空间量, 与帧率无关);
  2. 把 测硬度/扫码 的确认门槛从帧数 (min_frames=6/5, 只在 30fps 下等于
     0.2s/0.167s) 切到秒基 min_seconds — 现场相机 24fps 时帧数门槛会变紧,
     秒基与帧率解耦 (用户点名要求)。
基线 (v5.1 缓存, 帧数门槛+无扩边): 34 OK + 1 真复检 + #35 缺扫码 NG。
达标线: 35 OK + 1 复检 (#真复检在 0:22-0:41) + 0 NG。

用法: python matrix_margin_seconds.py [缓存路径]
"""
import copy
import json
import sys
from collections import Counter

sys.path.insert(0, '/home/qianqian/桌面/word/tianjun-main')
import requests

from backend.api.source_region_events import RegionEventEngine, parse_region_events

EV_NAMES = {1: '合格', 2: '不良NG', 1783299935644: '复检'}
CACHE = sys.argv[1] if len(sys.argv) > 1 else '/tmp/tp_v51_dets_cache.json'

base_pc = requests.get('http://localhost:8001/api/v1/projects/22',
                       timeout=5).json()['pipeline_config']
cache = json.load(open(CACHE))
fps, frames = cache['fps'], cache['frames']
print(f'缓存 {CACHE}: {len(frames)} 帧 @ {fps}fps\n')


def run(overrides: dict):
    """overrides: 规则名 -> {字段: 值}; 返回 (周期列表, 汇总 Counter)."""
    pc = copy.deepcopy(base_pc)
    for r in pc['region_events']['rules']:
        for k, v in (overrides.get(r['name']) or {}).items():
            r[k] = v
    eng = RegionEventEngine(parse_region_events(pc))
    seq, cycles = [], []
    for i, dets in enumerate(frames):
        ts = i / fps
        for ev in eng.process_frame(dets, ts):
            if ev['action'] != 'confirmed':
                continue
            seq.append((ev['rule_name'], ts))
            if ev.get('settle'):
                eid = ev.get('settle_event_id')
                if eid is None:
                    eid = ev.get('event_id') if ev.get('event_id') is not None else 1
                cycles.append({'start': seq[0][1], 'end': ts,
                               'seq': [n for n, _ in seq],
                               'result': EV_NAMES.get(eid, str(eid))})
                seq = []
    return cycles, Counter(c['result'] for c in cycles)


def mmss(t):
    return f'{int(t)//60}:{int(t)%60:02d}'


# 组合: margin ∈ {0, 0.02} × 门槛口径 ∈ {帧数(现状), 秒基}
# 秒基候选按 30fps 现值换算再前后各探一档:
#   测硬度 6帧=0.2s → {0.15, 0.2, 0.25}; 扫码 5帧≈0.167s → {0.1, 0.15, 0.2}
CASES = []
for margin in (0.0, 0.02):
    CASES.append((f'帧数门槛 margin={margin}', {'扫码': {'object_margin': margin}}))
for hs in (0.15, 0.2, 0.25):
    for ss in (0.1, 0.15, 0.2):
        CASES.append((
            f'秒基 硬度={hs}s 扫码={ss}s margin=0.02',
            {'测硬度': {'min_seconds': hs},
             '扫码': {'min_seconds': ss, 'object_margin': 0.02}},
        ))

for name, ov in CASES:
    cycles, tally = run(ov)
    flags = []
    recheck = [c for c in cycles if c['result'] == '复检']
    if tally.get('合格') == 35 and tally.get('复检') == 1 and not tally.get('不良NG') \
            and recheck and 20 < recheck[0]['start'] < 45:
        flags.append('★达标')
    bad = [c for c in cycles if c['result'] != '合格']
    detail = ' '.join(f"{c['result']}@{mmss(c['start'])}-{mmss(c['end'])}" for c in bad)
    print(f"{name:42s} 共{len(cycles):2d}周期 {dict(tally)} {detail} {' '.join(flags)}")
