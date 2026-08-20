"""combo 判型表「切步数量门」引擎 (combo_table.step_guard, v3.49).

现场诉求 (电机装配缸体判型): 操作员做完一步去做下一步时, 上一步的数量必须
"说得通" —— 4 缸机型座瓦该 5 个, 只装 4 个就切到装盖瓦, 要当场报警, 不能等
周期结算才发现; 超装 (第 6 个) 则不等切步, 一锁定立刻报。

三类检查 (各自可开关):
  - 切步检查 (check_under): 某标签计数首次 0→≥1 视为"切到该步", 回查所有
    更早标签的计数是否落在候选行允许值集合内 (少装 / 数量不符都在这里报);
  - 超装即时检查 (check_over): 任一标签计数一旦超过候选行最大允许值,
    当帧立刻报, 不等切步;
  - 顺序检查 (check_order, v3.49 二期, 默认关): 以 combo labels 配置顺序为
    工序顺序, 某更早标签在更后标签已开始后**再新增计数**即报 kind='order' ——
    但该标签当前处于少装违规活跃中时豁免 (回头补件是补救, 不是乱序)。
    报警不拦计数。非 combo 步骤间的顺序用 steps_config 严格顺序, 不归这里。

补救闭环 (v3.49 二期): transition/over 违规发出后进"活跃池", tick 每帧复查
计数是否已回到允许值集合内 —— 是则移入 resolved 队列, 宿主用 pop_resolved()
取走 (消横幅 + 可配"已补齐"事件)。order 是瞬时事件, 不驻留活跃池。

期望数量来源 (expected_source):
  - table: 纯视觉 —— 候选行 = 判定表全部 OK 行 (NG 行是"已知坏组合", 不作目标);
  - plc:   读 PLC 缸型点位 (RFC 13 点位引擎缓存值), 候选行 = plc_code 匹配的
           OK 行; 读不到/无匹配按 plc_unavailable 档回退 (table=退纯视觉, skip=不查);
  - auto:  PLC 值可用且有匹配行则用 PLC, 否则退纯视觉。

候选行收窄 (narrow_by_progress, 默认开): 检查标签 i 时, 用更早标签 (0..i-1)
的已定计数过滤候选行 —— 座瓦装完 5 个后, 候选 (5,5,0)/(5,5,4) 自然收窄,
盖瓦上限从 7 收到 5, 第 6 个盖瓦即报超装 (纯视觉判型推断)。收窄为空时回退
不收窄 (更早标签已错, 避免连锁误报)。

违规去重: 同一 (类别, 标签, 计数值) 只报一次; 计数回退 (周期结算清池)
自动整体复位, 无需外部挂清理点。处置 (提示事件 / 即时NG) 由 VSM 侧
_fire_combo_guard_violation 收口, 本引擎纯逻辑可单测。

未配置 step_guard 时引擎为 None, 热路径一次 getattr 早退零开销。
"""
from __future__ import annotations


def _loose_eq(a, b) -> bool:
    """PLC 缸型码宽松比对: 数值相等 (4 == "4" == 4.0) 或字符串精确相等。"""
    sa, sb = str(a).strip(), str(b).strip()
    if sa == sb:
        return True
    try:
        return float(sa) == float(sb)
    except (TypeError, ValueError):
        return False


class ComboStepGuard:
    """切步数量门状态机。tick(counts) 每推理帧调用一次, 返回本帧新违规列表。"""

    def __init__(self, labels, rows, cfg, plc_reader=None):
        self.labels = list(labels)
        # 候选行只认 OK 行: NG 行是"已知坏组合", 数量目标只能来自 OK 行
        self._ok_rows = [r for r in rows if r.get('verdict') != 'NG']
        self.check_under = cfg.get('check_under') is not False
        self.check_over = cfg.get('check_over') is not False
        self.check_order = cfg.get('check_order') is True
        self.expected_source = cfg.get('expected_source') or 'table'
        self.narrow_by_progress = cfg.get('narrow_by_progress') is not False
        self.plc_unavailable = cfg.get('plc_unavailable') or 'table'
        self._plc_reader = plc_reader
        self.last_plc_value = None   # 最近一次读到的缸型值 (诊断/透出)
        self.reset()

    def reset(self):
        """周期结算 / 即时NG后清账 (计数回退时 tick 内部也会自动调)。"""
        self._prev = {}
        self._fired = set()      # {(kind, label, actual)} 去重
        self._active = {}        # {(kind, label): violation} 活跃违规 (待补救)
        self._resolved = []      # 补齐消警队列, 宿主 pop_resolved() 取走

    def pop_resolved(self) -> list:
        """取走本帧已补齐的违规列表 (取后清空)。"""
        out, self._resolved = self._resolved, []
        return out

    # ---------------- 候选行 ----------------

    def _base_rows(self):
        """按期望来源取候选行 (PLC 过滤在这一层)。返回 (rows, source_tag)。"""
        src = self.expected_source
        if src in ('plc', 'auto') and self._plc_reader is not None:
            val = None
            try:
                val = self._plc_reader()
            except Exception:
                val = None
            self.last_plc_value = val
            if val is not None:
                matched = [r for r in self._ok_rows
                           if r.get('plc_code') and _loose_eq(r['plc_code'], val)]
                if matched:
                    return matched, f'PLC缸型={val}'
                # 读到值但判定表没有登记该 plc_code: 明示而非静默兜底
                # (2026-08-14 现场 cyl_type=11 vs 登记 4/6, 工程师无从察觉)
                if any(r.get('plc_code') for r in self._ok_rows):
                    if src == 'plc' and self.plc_unavailable == 'skip':
                        return [], f'PLC缸型={val}未登记(跳过检查)'
                    return self._ok_rows, f'PLC缸型={val}未登记→判定表推断'
            if src == 'plc' and self.plc_unavailable == 'skip':
                return [], 'PLC不可用(跳过检查)'
        return self._ok_rows, '判定表推断'

    def _allowed_values(self, idx, counts, base_rows):
        """标签 idx 的允许计数集合 (可选按更早标签实际计数收窄候选行)。"""
        rows = base_rows
        if self.narrow_by_progress and idx > 0:
            narrowed = [r for r in rows
                        if all(r['counts'][k] == counts.get(self.labels[k], 0)
                               for k in range(idx))]
            if narrowed:
                rows = narrowed
        return sorted({r['counts'][idx] for r in rows})

    # ---------------- 主循环 ----------------

    def tick(self, counts: dict) -> list:
        """counts = {label: 当前周期已计数}。返回本帧新违规:
        [{kind: 'transition'|'over'|'order', label, actual, expected: [int], message}]

        补齐消警走独立队列: 本帧有活跃违规回到允许值集合内时移入 resolved,
        宿主随后用 pop_resolved() 取走。"""
        cur = {l: int(counts.get(l, 0)) for l in self.labels}
        if any(cur[l] < self._prev.get(l, 0) for l in self.labels):
            self.reset()   # 计数回退 = 周期已清池, 整体复位
        prev = self._prev
        violations = []
        base_rows, source_tag = self._base_rows()

        if base_rows and self.check_over:
            for j, lbl in enumerate(self.labels):
                if cur[lbl] <= prev.get(lbl, 0):
                    continue
                allowed = self._allowed_values(j, cur, base_rows)
                if allowed and cur[lbl] > max(allowed):
                    self._emit(violations, 'over', lbl, cur[lbl], allowed,
                               source_tag)

        if base_rows and self.check_under:
            for j, lbl in enumerate(self.labels):
                if not (prev.get(lbl, 0) == 0 and cur[lbl] > 0):
                    continue   # 仅"该标签首次出现"这一帧视为切步
                for i in range(j):
                    li = self.labels[i]
                    allowed = self._allowed_values(i, cur, base_rows)
                    if allowed and cur[li] not in allowed:
                        self._emit(violations, 'transition', li, cur[li],
                                   allowed, source_tag)

        # 顺序检查: 更早标签在更后标签已开始 (上帧计数>0) 后再新增计数 = 乱序;
        # 该标签处于少装违规活跃中 (回头补件) 时豁免。报警不拦计数。
        if self.check_order:
            for j, lbl in enumerate(self.labels):
                if cur[lbl] <= prev.get(lbl, 0):
                    continue
                if ('transition', lbl) in self._active:
                    continue   # 补救豁免
                later = next((self.labels[k]
                              for k in range(j + 1, len(self.labels))
                              if prev.get(self.labels[k], 0) > 0), None)
                if later is not None:
                    self._emit(violations, 'order', lbl, cur[lbl], [],
                               source_tag, later=later)

        # 补齐消警复查: 活跃违规的计数回到允许值集合内 → 移入 resolved 队列
        if self._active and base_rows:
            for key in list(self._active.keys()):
                kind, lbl = key
                idx = self.labels.index(lbl)
                allowed = self._allowed_values(idx, cur, base_rows)
                if allowed and cur[lbl] in allowed:
                    v = self._active.pop(key)
                    exp_txt = '/'.join(str(x) for x in allowed)
                    self._resolved.append({
                        **v, 'kind': 'resolved', 'orig_kind': kind,
                        'actual': cur[lbl], 'expected': list(allowed),
                        'message': f'[{lbl}] 已补齐: 现 {cur[lbl]} 个 (应 {exp_txt} 个)',
                    })

        self._prev = cur
        return violations

    def _emit(self, violations, kind, label, actual, allowed, source_tag,
              later=None):
        key = (kind, label, actual)
        if key in self._fired:
            return
        self._fired.add(key)
        exp_txt = '/'.join(str(v) for v in allowed)
        if kind == 'over':
            msg = f'[{label}] 超装: 已 {actual} 个, 最多 {max(allowed)} 个 ({source_tag})'
        elif kind == 'order':
            msg = f'[{label}] 工序乱序: [{later}] 已开始后又新增 (第 {actual} 个)'
        else:
            msg = f'[{label}] 切步数量不符: 应 {exp_txt} 个, 实际 {actual} 个 ({source_tag})'
        v = {'kind': kind, 'label': label, 'actual': actual,
             'expected': list(allowed), 'message': msg}
        violations.append(v)
        if kind in ('transition', 'over'):
            self._active[(kind, label)] = v   # 进活跃池, 等补救复查


def _make_plc_reader(connection_id, point_key):
    """PLC 缸型点位读取闭包: 走 RFC 13 点位引擎的轮询缓存值, 不发起新通讯。"""
    def _read():
        from backend.services.plc.manager import get_plc_manager
        eng = get_plc_manager().get_engine(connection_id)
        if eng is None:
            return None
        return eng.values.get(point_key)
    return _read


def build_combo_step_guard(combo_table):
    """按 _parse_combo_table 归一化结果构建数量门; 未配置返回 None。"""
    if not combo_table:
        return None
    cfg = combo_table.get('step_guard')
    if not cfg:
        return None
    plc_reader = None
    if (cfg.get('expected_source') in ('plc', 'auto')
            and cfg.get('plc_connection_id') and cfg.get('plc_point')):
        plc_reader = _make_plc_reader(cfg['plc_connection_id'], cfg['plc_point'])
    return ComboStepGuard(labels=combo_table['labels'],
                          rows=combo_table['rows'],
                          cfg=cfg, plc_reader=plc_reader)
