"""combo 判型表「位置去重计数」引擎 (count_mode='positional', v3.48.x).

一比一移植外部工程师工具 (screw_detection_qt.py) 的算法 3「IoU 目标跟踪计数」:
  - 新检测框先与已确认 ROI 做 IoU 匹配 (>iou 阈值) → 视为同一位置, EMA 平滑
    跟随, **不重复计数** (同位置返工/补装不加数);
  - 匹配不上的进候选池, 连续 min_consecutive 个推理 tick 都匹配到 → 确认为
    新 ROI, 该标签计数 +1;
  - 候选超过 pending_ttl 个 tick 没再匹配到 → 丢弃 (对齐原工具 10 帧清理);
  - 已确认 ROI 默认永不消失 (perish_ticks=0), 周期结算时整池清空 (对齐原
    工具"清除步骤"语义)。

与默认 count_mode='steps' (按步骤时间分次计数) 的区别: steps 数的是"动作发生
了几次", positional 数的是"动作发生在几个不同位置" —— 打螺丝/装瓦这类
固定位点场景要判型时用 positional 才不会被返工动作干扰。

未配置 count_mode='positional' 时引擎为 None, 热路径一次 getattr 早退零开销。
"""
from __future__ import annotations

from collections import defaultdict


def _iou(a, b):
    """xyxy IoU (归一化坐标)。"""
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    iw = max(0.0, x2 - x1)
    ih = max(0.0, y2 - y1)
    inter = iw * ih
    a1 = (a[2] - a[0]) * (a[3] - a[1])
    a2 = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (a1 + a2 - inter + 1e-9)


class ComboPositionalCounter:
    """每个 combo 标签维护 已确认 ROI 池 + 候选池, 按位置去重计数。

    tick 语义: 每个推理帧 feed 一次 = 1 tick (对齐原工具"帧"概念;
    原工具逐帧离线处理, 我们实时推理条数≈视频帧率, 量纲一致)。
    """

    def __init__(self, labels, iou=0.4, ema_alpha=0.6, min_consecutive=3,
                 pending_ttl=10, perish_ticks=0, idle_reset_ticks=0,
                 per_label=None):
        self.labels = set(labels)
        self.iou = float(iou)
        self.ema_alpha = float(ema_alpha)
        self.min_consecutive = max(1, int(min_consecutive))
        self.pending_ttl = max(1, int(pending_ttl))
        self.perish_ticks = max(0, int(perish_ticks))
        self.idle_reset_ticks = max(0, int(idle_reset_ticks))
        # v3.49 二期: 追踪参数按标签覆盖 {label: {六参数任意子集}}, 缺省回落全局
        self.per_label = per_label if isinstance(per_label, dict) else {}
        self.reset()

    def _p(self, label, key):
        """标签级参数查找: per_label 覆盖 > 全局值 (解析层已 clamp)。"""
        ov = self.per_label.get(label)
        if ov is not None and key in ov:
            return ov[key]
        return getattr(self, key)

    def reset(self):
        """周期结算 / 项目切换时清池 (对齐原工具清除步骤)。"""
        self._tick = 0
        self._tracked = defaultdict(list)   # label → [{box, last_seen}]
        self._pending = defaultdict(list)   # label → [{box, seen, last_seen}]
        self._counts = defaultdict(int)     # label → 已确认 ROI 累计
        self._last_label_seen = {}          # label → 最后有检测的 tick

    def counts(self) -> dict:
        return dict(self._counts)

    def rois(self) -> list:
        """锁定/候选 ROI 快照 (Monitor 画常驻锁框用, 归一化 xyxy)。

        locked: 已计数位置 (seq=该标签第几个, 从 1 起); pending: 候选确认中
        (seen/need 供前端画虚线+进度)。GIL 下浅读, 与 feed 并发安全。
        """
        out = []
        for label, tlist in self._tracked.items():
            for trk in tlist:
                out.append({'label': label, 'box': list(trk['box']),
                            'state': 'locked', 'seq': trk.get('seq', 0)})
        for label, plist in self._pending.items():
            for pnd in plist:
                out.append({'label': label, 'box': list(pnd['box']),
                            'state': 'pending', 'seen': pnd['seen'],
                            'need': self._p(label, 'min_consecutive')})
        return out

    @staticmethod
    def _to_xyxy(det):
        """检测框 → 归一化 xyxy。兼容两种格式 (非法返回 None):
        - 真实 runner: 扁平键 {x, y, w, h} (归一化)
        - synthetic 剧本/测试: {bbox: [x, y, w, h]}
        """
        bbox = det.get('bbox')
        if bbox is None:
            bbox = (det.get('x'), det.get('y'), det.get('w'), det.get('h'))
        try:
            x, y, w, h = (float(bbox[0]), float(bbox[1]),
                          float(bbox[2]), float(bbox[3]))
        except (TypeError, ValueError, IndexError):
            return None
        if w <= 0 or h <= 0:
            return None
        return (x, y, x + w, y + h)

    def feed(self, detections):
        """一个推理 tick 的全部检测结果 (显示坐标系, 步骤阈值已过滤)。"""
        self._tick += 1
        tick = self._tick

        for det in detections or ():
            label = det.get('label')
            if label not in self.labels:
                continue
            box = self._to_xyxy(det)
            if box is None:
                continue
            self._last_label_seen[label] = tick
            iou_th = self._p(label, 'iou')
            a = self._p(label, 'ema_alpha')
            min_consec = self._p(label, 'min_consecutive')

            # 1) 已确认 ROI 匹配 → 同位置, EMA 跟随, 不重计
            tlist = self._tracked[label]
            best_i, best_v = -1, 0.0
            for i, trk in enumerate(tlist):
                v = _iou(box, trk['box'])
                if v > best_v:
                    best_i, best_v = i, v
            if best_i >= 0 and best_v > iou_th:
                ob = tlist[best_i]['box']
                tlist[best_i]['box'] = tuple(
                    a * box[k] + (1 - a) * ob[k] for k in range(4))
                tlist[best_i]['last_seen'] = tick
                continue

            # 2) 候选池匹配 → 连续确认计数
            plist = self._pending[label]
            best_i, best_v = -1, 0.0
            for i, pnd in enumerate(plist):
                v = _iou(box, pnd['box'])
                if v > best_v:
                    best_i, best_v = i, v
            if best_i >= 0 and best_v > iou_th:
                pnd = plist[best_i]
                ob = pnd['box']
                pnd['box'] = tuple(
                    a * box[k] + (1 - a) * ob[k] for k in range(4))
                pnd['seen'] += 1
                pnd['last_seen'] = tick
                if pnd['seen'] >= min_consec:
                    self._counts[label] += 1
                    tlist.append({'box': pnd['box'], 'last_seen': tick,
                                  'seq': self._counts[label]})
                    del plist[best_i]
                continue

            # 3) 全新候选 (min_consecutive=1 时首帧即确认)
            if min_consec <= 1:
                self._counts[label] += 1
                tlist.append({'box': box, 'last_seen': tick,
                              'seq': self._counts[label]})
            else:
                plist.append({'box': box, 'seen': 1, 'last_seen': tick})

        # 候选过期清理 (ttl 按标签取)
        for label, plist in self._pending.items():
            expire = tick - self._p(label, 'pending_ttl')
            plist[:] = [p for p in plist if p['last_seen'] > expire]

        # 可选: 消失超时 / 间隔重置 (缺省 0 = 原工具默认配置, 池常驻)
        if (self.perish_ticks or self.idle_reset_ticks or
                any(('perish_ticks' in ov or 'idle_reset_ticks' in ov)
                    for ov in self.per_label.values())):
            for label, tlist in self._tracked.items():
                idle = self._p(label, 'idle_reset_ticks')
                if idle:
                    last = self._last_label_seen.get(label, 0)
                    if tick - last > idle:
                        tlist.clear()
                        continue
                perish = self._p(label, 'perish_ticks')
                if perish:
                    tlist[:] = [t for t in tlist
                                if tick - t['last_seen'] <= perish]


def build_combo_positional(combo_table):
    """按 _parse_combo_table 归一化结果构建引擎; 非 positional 返回 None。"""
    if not combo_table or combo_table.get('count_mode') != 'positional':
        return None
    tr = combo_table.get('tracking') or {}
    return ComboPositionalCounter(
        labels=combo_table['labels'],
        iou=tr.get('iou', 0.4),
        ema_alpha=tr.get('ema_alpha', 0.6),
        min_consecutive=tr.get('min_consecutive', 3),
        pending_ttl=tr.get('pending_ttl', 10),
        perish_ticks=tr.get('perish_ticks', 0),
        idle_reset_ticks=tr.get('idle_reset_ticks', 0),
        per_label=combo_table.get('tracking_per_label') or None,
    )


# 切步数量门 (step_guard) 引擎在 source_combo_guard.py (v3.49)。
