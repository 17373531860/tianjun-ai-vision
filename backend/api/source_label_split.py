"""同标签区域拆分（虚拟步骤）+ 工件就位提示 —— 检测出口标签改写层.

场景: 模型只能输出一个动作标签（如「打螺丝」），但同一动作发生在不同位置
代表不同业务步骤（螺丝1~4 对角顺序）。本层在检测结果进入状态机之前，按
检测框中心命中的区域把原始标签改写成"虚拟步骤"标签；改写后下游状态机 /
计数 / 事件 / MES / 导出 / 前端画框全部按普通步骤零修改工作。

配置来源: pipeline_config.label_splits / pipeline_config.placement_guide
（结构定义见 docs/rfc/同标签区域拆分_虚拟步骤_设计方案_RFC.md）。

挂点: source_inference_loop_mixin._inference_select_and_run_model 的两条
返回路径（真实模型 + synthetic 剧本）统一过 host._apply_label_splits。

设计约束:
  - 纯 Python + 无 cv2 依赖（点在多边形用射线法，与前端 Monitor 同算法），
    单元测试不需要拉起重依赖。
  - 解析期做完全部校验，运行期热路径只做 dict 查表 + 数值比较。
  - 无规则时 host 上引擎为 None，热路径一次 getattr 早退，零开销。
"""
from __future__ import annotations

import time
from typing import Optional

from backend.core import debug_center


def _point_in_polygon(px: float, py: float, polygon) -> bool:
    """射线法: 归一化坐标点是否在多边形内（含边界近似）。polygon 已在解析期校验。"""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > py) != (yj > py):
            denom = (yj - yi) or 1e-18
            if px < (xj - xi) * (py - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def _parse_polygon(raw) -> Optional[list]:
    """归一化多边形校验: ≥3 点、每点 2 个 float。不合法返回 None。"""
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    parsed = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            return None
        try:
            parsed.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError):
            return None
    return parsed


def _parse_bbox(raw) -> Optional[dict]:
    """锚点标定框: dict{x,y,w,h} 归一化, w/h 必须 > 0。"""
    if not isinstance(raw, dict):
        return None
    try:
        b = {k: float(raw.get(k)) for k in ('x', 'y', 'w', 'h')}
    except (TypeError, ValueError):
        return None
    if b['w'] <= 0 or b['h'] <= 0:
        return None
    return b


class SplitRule:
    """一条拆分规则的运行时形态（解析期完成全部校验）。"""

    __slots__ = ('rule_id', 'source_label', 'mode', 'anchor_label', 'anchor_ref',
                 'anchor_hold_seconds', 'unmatched', 'unmatched_label', 'regions',
                 'round_count', 'round_trigger_label', 'round_prefixes',
                 'round_trigger_gap', 'round_regions', 'round_trigger_min',
                 'round_trigger_conf')

    def __init__(self, rule_id, source_label, mode, anchor_label, anchor_ref,
                 anchor_hold_seconds, unmatched, unmatched_label, regions,
                 round_count=0, round_trigger_label='', round_prefixes=None,
                 round_trigger_gap=3.0, round_regions=None,
                 round_trigger_min=0.0, round_trigger_conf=0.0):
        self.rule_id = rule_id
        self.source_label = source_label
        self.mode = mode                        # 'fixed' | 'anchor'
        self.anchor_label = anchor_label
        self.anchor_ref = anchor_ref            # anchor 模式: 标定时锚点框
        self.anchor_hold_seconds = anchor_hold_seconds
        self.unmatched = unmatched              # 'drop' | 'keep' | 'map'
        self.unmatched_label = unmatched_label
        self.regions = regions                  # [(name, polygon), ...]
        # ---- 多轮次（v3.32, 同一位置按工序轮次映射不同虚拟步骤）----
        # round_count=0 → 未启用。启用时: 每次 trigger_label"重新出现"
        # （消失超过 round_trigger_gap 秒后再出现）轮次 +1, 超过总轮数回绕;
        # 虚拟步骤名 = 当轮前缀 + 区域名（如 前罩+螺丝1）。
        self.round_count = round_count
        self.round_trigger_label = round_trigger_label
        self.round_prefixes = round_prefixes or []
        self.round_trigger_gap = round_trigger_gap
        # 每轮独立区域（翻面后位置不重叠时用）: {轮次序号1起: [(name, polygon), ...]}
        # 缺某轮 → 该轮沿用共享 regions。区域名仍与共享区域同一命名空间（前缀负责区分轮次）。
        self.round_regions = round_regions or {}
        # 切换确认时长(秒): 切换标签需持续在场这么久才算一次"重新出现"。
        # 0 = 见帧即切（老行为）。真实模型会有单帧误检闪现（实测 2 帧的假"盖罩"
        # 把轮次多推了一拍），生产环境建议 0.5s 左右。
        self.round_trigger_min = round_trigger_min
        # 切换标签置信度下限: 低于它的检测视为"不在场"。真实模型在非切换阶段
        # 会有零星低置信度误检 —— 它们不停刷新"最近在场时刻", 让真正的切换动作
        # 永远凑不满"离场 gap 秒", 轮次卡死不切(真实模型 UAT 实测)。0 = 不过滤。
        self.round_trigger_conf = round_trigger_conf

    @property
    def rounds_enabled(self) -> bool:
        return self.round_count >= 2


def parse_label_splits(pipeline_config: dict) -> list:
    """解析 pipeline_config.label_splits → [SplitRule]。

    非法项静默跳过（打印警告），同一 source_label 只取第一条启用规则。
    """
    raw_rules = (pipeline_config or {}).get('label_splits')
    if not isinstance(raw_rules, list) or not raw_rules:
        return []
    rules = []
    seen_sources = set()
    for idx, raw in enumerate(raw_rules):
        if not isinstance(raw, dict) or raw.get('enabled') is False:
            continue
        source_label = str(raw.get('source_label') or '').strip()
        if not source_label:
            continue
        if source_label in seen_sources:
            print(f"[LabelSplit] 规则#{idx} source_label={source_label} 重复, 跳过")
            continue
        mode = raw.get('mode') or 'fixed'
        if mode not in ('fixed', 'anchor'):
            mode = 'fixed'
        anchor_label = str(raw.get('anchor_label') or '').strip()
        anchor_ref = _parse_bbox(raw.get('anchor_ref'))
        if mode == 'anchor' and (not anchor_label or anchor_ref is None):
            print(f"[LabelSplit] 规则#{idx} anchor 模式缺锚点标签/标定框, 降级为 fixed")
            mode = 'fixed'
        try:
            hold = float(raw.get('anchor_hold_seconds', 3.0))
        except (TypeError, ValueError):
            hold = 3.0
        hold = max(0.0, min(60.0, hold))
        unmatched = raw.get('unmatched') or 'drop'
        if unmatched not in ('drop', 'keep', 'map'):
            unmatched = 'drop'
        unmatched_label = str(raw.get('unmatched_label') or '').strip()
        if unmatched == 'map' and not unmatched_label:
            unmatched = 'drop'
        regions = []
        region_names = set()
        for r in (raw.get('regions') or []):
            if not isinstance(r, dict):
                continue
            name = str(r.get('name') or '').strip()
            poly = _parse_polygon(r.get('polygon'))
            if not name or poly is None or name in region_names:
                if name:
                    print(f"[LabelSplit] 规则#{idx} 区域[{name}] 多边形非法或重名, 跳过")
                continue
            region_names.add(name)
            regions.append((name, poly))
        if not regions:
            print(f"[LabelSplit] 规则#{idx} source_label={source_label} 无有效区域, 跳过")
            continue
        (round_count, round_trigger, round_prefixes, round_gap,
         round_regions, round_trigger_min, round_trigger_conf) = _parse_rounds(raw, idx)
        seen_sources.add(source_label)
        rules.append(SplitRule(
            rule_id=str(raw.get('id') or f'ls_{idx}'),
            source_label=source_label, mode=mode,
            anchor_label=anchor_label if mode == 'anchor' else '',
            anchor_ref=anchor_ref if mode == 'anchor' else None,
            anchor_hold_seconds=hold,
            unmatched=unmatched, unmatched_label=unmatched_label,
            regions=regions,
            round_count=round_count, round_trigger_label=round_trigger,
            round_prefixes=round_prefixes, round_trigger_gap=round_gap,
            round_regions=round_regions, round_trigger_min=round_trigger_min,
            round_trigger_conf=round_trigger_conf,
        ))
    return rules


def _parse_rounds(raw: dict, idx: int):
    """解析规则里的 rounds 段, 非法/未启用返回 (0, '', [], 3.0, {})。

    结构: {"enabled": true, "trigger_label": "盖罩", "count": 2,
           "prefixes": ["前罩", "后罩"], "trigger_gap_seconds": 3.0,
           "trigger_min_seconds": 0.5, "trigger_conf": 0.7,
           "region_overrides": {"2": [{name, polygon, color}, ...]}}
    要求: trigger_label 非空, count>=2, prefixes 数量==count 且非空不重复。
    region_overrides 可选（翻面后位置不重叠时给某轮换一批区域, 缺省轮沿用共享区域）;
    某轮 override 全部非法 → 该轮回退共享区域（不整体禁用轮次）。
    trigger_min_seconds 可选（默认 0=见帧即切, 零差异）: 切换标签需持续在场
    这么久才算一次有效切换 — 真实模型的单帧误检闪现会把轮次多推一拍, 用它过滤。
    trigger_conf 可选（默认 0=不过滤, 零差异）: 切换标签的置信度下限 — 非切换
    阶段的零星低置信度误检会不停刷新在场时刻, 让轮次永远等不到"离场再出现"。
    """
    disabled = (0, '', [], 3.0, {}, 0.0, 0.0)
    rounds = raw.get('rounds')
    if not isinstance(rounds, dict) or not rounds.get('enabled'):
        return disabled
    trigger = str(rounds.get('trigger_label') or '').strip()
    try:
        count = int(rounds.get('count', 0))
    except (TypeError, ValueError):
        count = 0
    prefixes = [str(p or '').strip() for p in (rounds.get('prefixes') or [])]
    if not trigger or count < 2 or len(prefixes) != count \
            or any(not p for p in prefixes) or len(set(prefixes)) != count:
        print(f"[LabelSplit] 规则#{idx} 轮次配置非法(缺切换标签/轮数<2/前缀不齐), 忽略轮次")
        return disabled
    try:
        gap = float(rounds.get('trigger_gap_seconds', 3.0))
    except (TypeError, ValueError):
        gap = 3.0
    gap = max(0.5, min(60.0, gap))
    try:
        trig_min = float(rounds.get('trigger_min_seconds', 0.0))
    except (TypeError, ValueError):
        trig_min = 0.0
    # 上限压在 gap 以下: 确认时长若 ≥ 消失间隔, 同一次在场可能先被判"离场"再确认, 逻辑矛盾
    trig_min = max(0.0, min(trig_min, gap - 0.1, 10.0))
    try:
        trig_conf = float(rounds.get('trigger_conf', 0.0))
    except (TypeError, ValueError):
        trig_conf = 0.0
    trig_conf = max(0.0, min(1.0, trig_conf))
    overrides = {}
    raw_overrides = rounds.get('region_overrides')
    if isinstance(raw_overrides, dict):
        for key, region_list in raw_overrides.items():
            try:
                rnd = int(key)
            except (TypeError, ValueError):
                continue
            if not (1 <= rnd <= count) or not isinstance(region_list, list):
                continue
            parsed = []
            names = set()
            for r in region_list:
                if not isinstance(r, dict):
                    continue
                name = str(r.get('name') or '').strip()
                poly = _parse_polygon(r.get('polygon'))
                if not name or poly is None or name in names:
                    if name:
                        print(f"[LabelSplit] 规则#{idx} 第{rnd}轮区域[{name}] 非法或重名, 跳过")
                    continue
                names.add(name)
                parsed.append((name, poly))
            if parsed:
                overrides[rnd] = parsed
            else:
                print(f"[LabelSplit] 规则#{idx} 第{rnd}轮独立区域全部非法, 该轮回退共享区域")
    return count, trigger, prefixes, gap, overrides, trig_min, trig_conf


class LabelSplitEngine:
    """按帧改写 detections 标签。每通道一个实例, 只被推理线程访问（无锁）。"""

    def __init__(self, rules: list, display_names: Optional[dict] = None):
        self.rules = {r.source_label: r for r in rules}
        self.anchor_labels = {r.anchor_label for r in rules if r.anchor_label}
        # 锚点缓存: {anchor_label: (bbox_dict, ts)} —— 锚点短暂被手遮挡时沿用最近位置
        self._anchor_cache = {}
        # 虚拟步骤显示名（改写后重挂 display_name, 与 steps_config.displayLabel 对齐）
        self._display_names = display_names or {}
        # 轮次状态: {source_label: {'idx': 0起步, 'last_seen': 切换标签最近在场时刻,
        #   'streak_start': 本次连续在场的起点, 'streak_counted': 本次在场是否已计切换}}
        # idx=0 表示尚未见过切换标签(改写时按第1轮兜底); 到达总轮数后再触发回绕到第1轮
        # saw_cycle: 本轮次周期内是否见过"周期里有步骤"(cycle_len>0)。空闲归零
        # 必须以它为前提 — 真实产线上切换标签是瞬时动作(盖上罩子就消失), 第一颗
        # 螺丝进周期之前有几秒"标签已离场+周期还空"的窗口, 不加此守门会被误判
        # 为工件下线, 轮次刚推到 1 就被打回 0(真实模型 UAT 实测踩过)。
        self._round_state = {
            r.source_label: {'idx': 0, 'last_seen': 0.0,
                             'streak_start': 0.0, 'streak_counted': False,
                             'saw_cycle': False}
            for r in rules if r.rounds_enabled
        }
        self._round_trigger_labels = {
            r.round_trigger_label for r in rules if r.rounds_enabled
        }

    # ---------- 锚点 ----------

    def _update_anchor_cache(self, detections, now: float):
        best = {}
        for det in detections:
            lbl = det.get('label')
            if lbl in self.anchor_labels:
                prev = best.get(lbl)
                if prev is None or det.get('confidence', 0) > prev.get('confidence', 0):
                    best[lbl] = det
        for lbl, det in best.items():
            bbox = {'x': det.get('x', 0.0), 'y': det.get('y', 0.0),
                    'w': det.get('w', 0.0), 'h': det.get('h', 0.0)}
            self._anchor_cache[lbl] = (bbox, now)

    def _current_anchor(self, rule: SplitRule, now: float) -> Optional[dict]:
        entry = self._anchor_cache.get(rule.anchor_label)
        if entry is None:
            return None
        bbox, ts = entry
        if now - ts > rule.anchor_hold_seconds:
            return None
        return bbox

    @staticmethod
    def _transform_polygon(poly, ref: dict, cur: dict):
        """标定坐标系 → 当前锚点坐标系（平移 + 各向缩放, 不做旋转）。"""
        sx = cur['w'] / ref['w']
        sy = cur['h'] / ref['h']
        return [(cur['x'] + (px - ref['x']) * sx,
                 cur['y'] + (py - ref['y']) * sy) for px, py in poly]

    def _base_regions(self, rule: SplitRule):
        """当前轮次生效的区域集: 该轮配了独立区域用独立的, 否则用共享区域。"""
        if rule.rounds_enabled and rule.round_regions:
            state = self._round_state.get(rule.source_label)
            idx = state['idx'] if state else 0
            override = rule.round_regions.get(max(idx, 1))
            if override:
                return override
        return rule.regions

    def _resolve_regions(self, rule: SplitRule, now: float):
        """本帧该规则生效的区域多边形。anchor 模式锚点不可用时返回 None（全部按未命中处理）。"""
        base = self._base_regions(rule)
        if rule.mode != 'anchor':
            return base
        cur = self._current_anchor(rule, now)
        if cur is None:
            return None
        ref = rule.anchor_ref
        return [(name, self._transform_polygon(poly, ref, cur))
                for name, poly in base]

    # ---------- 轮次 ----------

    def _update_rounds(self, detections, now: float, cycle_len):
        """维护每条多轮规则的当前轮次。

        - 切换标签"重新出现"（消失超过 trigger_gap 后再入画）且持续在场满
          trigger_min 秒（默认 0 = 见帧即切）→ 轮次 +1, 满轮回绕。
          确认时长用于过滤真实模型的单帧误检闪现（闪现会把轮次多推一拍）。
        - 周期已结算且切换标签离场超过 gap（工件已下线空闲）→ 归零, 下一工件从第1轮起。
          归零以"本轮次内周期确实装载过步骤"(saw_cycle)为前提: 工件刚开工时切换
          标签先离场、首个步骤还没进周期, 这段空窗不是"下线", 不能归零。
        """
        present_conf = {}
        for det in (detections or []):
            lbl = det.get('label')
            c = det.get('confidence', 0) or 0
            if lbl is not None and c > present_conf.get(lbl, 0.0):
                present_conf[lbl] = c
        for src, state in self._round_state.items():
            rule = self.rules.get(src)
            if rule is None:
                continue
            if cycle_len > 0:
                state['saw_cycle'] = True
            conf = present_conf.get(rule.round_trigger_label)
            in_frame = conf is not None and conf >= rule.round_trigger_conf
            # 现场诊断(5s 节流, 调试开关守门): 切换标签每次"在场"都留痕 ——
            # 排查轮次不切时, 第一个要看的就是有没有幽灵检测把"离场间隔"刷没了
            if (conf is not None and debug_center.is_on("backend.settlement")
                    and now - state.get('_dbg_ts', 0.0) >= 5.0):
                state['_dbg_ts'] = now
                debug_center.dbg(
                    "backend.settlement", "轮次切换标签在场",
                    f"[{src}] 切换标签[{rule.round_trigger_label}]在场 conf={conf:.2f}"
                    f"{'(低于下限按不在场处理)' if not in_frame else ''} "
                    f"当前轮次={state['idx']}")
            if in_frame:
                gone_long = (now - state['last_seen']) > rule.round_trigger_gap
                if state['last_seen'] == 0.0 or gone_long:
                    state['streak_start'] = now
                    state['streak_counted'] = False
                state['last_seen'] = now
                if (not state['streak_counted']
                        and (now - state['streak_start']) >= rule.round_trigger_min):
                    prev_idx = state['idx']
                    state['idx'] = (prev_idx % rule.round_count) + 1
                    state['streak_counted'] = True
                    state['saw_cycle'] = False
                    print(f"[LabelSplit] [{src}] 轮次切换 {prev_idx} → {state['idx']} "
                          f"({rule.round_prefixes[state['idx'] - 1]}), "
                          f"切换标签[{rule.round_trigger_label}]确认在场 "
                          f"{now - state['streak_start']:.2f}s")
            elif (cycle_len == 0 and state['idx'] > 0 and state['saw_cycle']
                  and (now - state['last_seen']) > rule.round_trigger_gap):
                print(f"[LabelSplit] [{src}] 工件下线(周期空+切换标签离场"
                      f"{now - state['last_seen']:.1f}s), 轮次 {state['idx']} → 0")
                state['idx'] = 0
                state['streak_counted'] = False
                state['saw_cycle'] = False

    def _round_prefix(self, rule: SplitRule) -> str:
        """当前生效的轮次前缀（未见切换标签时按第1轮兜底）。"""
        state = self._round_state.get(rule.source_label)
        idx = state['idx'] if state else 0
        return rule.round_prefixes[max(idx, 1) - 1]

    def snapshot_rounds(self) -> Optional[dict]:
        """运行态透出给 /detection/results → Monitor 轮次角标。无多轮规则返回 None。"""
        if not self._round_state:
            return None
        out = {}
        for src, state in self._round_state.items():
            rule = self.rules.get(src)
            if rule is None:
                continue
            idx = state['idx']
            out[src] = {
                'round': idx,                       # 0 = 尚未开始
                'count': rule.round_count,
                'prefix': rule.round_prefixes[max(idx, 1) - 1],
                'trigger_label': rule.round_trigger_label,
            }
        return out or None

    # ---------- 主入口 ----------

    def apply(self, detections: list, now: Optional[float] = None,
              cycle_len: Optional[int] = None) -> list:
        """改写 detections（返回新 list, 命中的 det 为浅拷贝改写, 原对象不动）。

        cycle_len: 当前周期已记录步骤数, 供轮次空闲归零判定; 传 None 则不归零。
        """
        if not self.rules:
            return detections
        if now is None:
            now = time.time()
        if self._round_state:
            self._update_rounds(detections or [], now,
                                cycle_len if cycle_len is not None else -1)
        if not detections:
            return detections
        if self.anchor_labels:
            self._update_anchor_cache(detections, now)
        out = []
        region_cache = {}
        for det in detections:
            rule = self.rules.get(det.get('label'))
            if rule is None:
                out.append(det)
                continue
            regions = region_cache.get(rule.source_label, Ellipsis)
            if regions is Ellipsis:
                regions = self._resolve_regions(rule, now)
                region_cache[rule.source_label] = regions
            cx = float(det.get('x', 0)) + float(det.get('w', 0)) / 2
            cy = float(det.get('y', 0)) + float(det.get('h', 0)) / 2
            hit_name = None
            if regions:
                for name, poly in regions:
                    if _point_in_polygon(cx, cy, poly):
                        hit_name = name
                        break
            if hit_name is not None:
                if rule.rounds_enabled:
                    hit_name = self._round_prefix(rule) + hit_name
                out.append(self._rewrite(det, rule, hit_name))
            elif rule.unmatched == 'keep':
                out.append(det)
            elif rule.unmatched == 'map':
                # 改写标签不加轮次前缀: "位置外操作"是跨轮次的统一概念
                out.append(self._rewrite(det, rule, rule.unmatched_label))
            # 'drop' → 不追加
        return out

    def _rewrite(self, det: dict, rule: SplitRule, new_label: str) -> dict:
        new_det = dict(det)
        new_det['label'] = new_label
        new_det['split_from'] = rule.source_label
        # display_name 是 runner 按原始标签注入的, 改写后按虚拟步骤重挂
        new_det.pop('display_name', None)
        dn = self._display_names.get(new_label)
        if dn:
            new_det['display_name'] = dn
        return new_det


# ============================================================
# 工件就位提示（独立小功能, 与拆分正交）
# ============================================================

def parse_placement_guide(pipeline_config: dict) -> Optional[dict]:
    """解析 pipeline_config.placement_guide, 未启用/非法返回 None。"""
    raw = (pipeline_config or {}).get('placement_guide')
    if not isinstance(raw, dict) or not raw.get('enabled'):
        return None
    anchor_label = str(raw.get('anchor_label') or '').strip()
    polygon = _parse_polygon(raw.get('polygon'))
    if not anchor_label or polygon is None:
        print("[PlacementGuide] 配置缺锚点标签或引导框多边形, 忽略")
        return None
    mode = raw.get('mode') or 'hint'
    if mode not in ('hint', 'gate'):
        mode = 'hint'
    return {'anchor_label': anchor_label, 'polygon': polygon, 'mode': mode}


class PlacementGuideState:
    """逐帧维护"工件是否已放进引导框"状态, 供 /detection/results 暴露给前端。"""

    HOLD_SECONDS = 2.0  # 锚点短暂丢帧的消抖窗口

    def __init__(self, cfg: dict):
        self.anchor_label = cfg['anchor_label']
        self.polygon = cfg['polygon']
        self.mode = cfg['mode']
        self._last_seen_ts = 0.0
        self._last_in_position = False

    def update(self, detections: list, now: Optional[float] = None):
        if now is None:
            now = time.time()
        best = None
        for det in detections or []:
            if det.get('label') == self.anchor_label:
                if best is None or det.get('confidence', 0) > best.get('confidence', 0):
                    best = det
        if best is not None:
            cx = float(best.get('x', 0)) + float(best.get('w', 0)) / 2
            cy = float(best.get('y', 0)) + float(best.get('h', 0)) / 2
            self._last_in_position = _point_in_polygon(cx, cy, self.polygon)
            self._last_seen_ts = now

    def snapshot(self, now: Optional[float] = None) -> dict:
        if now is None:
            now = time.time()
        visible = (now - self._last_seen_ts) <= self.HOLD_SECONDS if self._last_seen_ts else False
        return {
            'enabled': True,
            'mode': self.mode,
            'anchor_label': self.anchor_label,
            'anchor_visible': visible,
            'in_position': bool(visible and self._last_in_position),
        }


__all__ = [
    'parse_label_splits', 'parse_placement_guide',
    'LabelSplitEngine', 'PlacementGuideState', 'SplitRule',
]
