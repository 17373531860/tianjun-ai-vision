"""传动杆 (class=5) 误判过滤后处理

背景
----
best(7).pt 模型把"空箱 + 泡沫槽中间的黑缝"稳定误识别成传动杆 (conf 0.88~0.92)，
与真传动杆的 conf 分布 (median 0.69) 相反。单纯提高 conf 阈值无解。

两层过滤（默认都关，需要在 project_config 里显式打开）
----
1. 空间共现过滤 filter_rod_by_companion
   同一帧里，rod 必须和任一 companion (大/小框架/侧板) 有 center-in-bbox 或 IoU>=thr
   才保留；否则整个 rod 丢弃。
2. 软时序门控 RodSessionGate
   当前周期从未检出过 big/small 框架时，抑制所有 rod。见过之后放行。
   每个新周期 reset。

两层互相独立可叠加，调用顺序推荐：先 filter_rod_by_companion 再 gate。
"""
from __future__ import annotations

from typing import Iterable, List, Dict, Any, Set


DEFAULT_ROD_LABEL = "传动杆"
DEFAULT_COMPANION_LABELS = ("大框架", "小框架", "侧板")
DEFAULT_GATE_LABELS = ("大框架", "小框架")
DEFAULT_IOU_THR = 0.25


def _norm_to_xyxy(det: Dict[str, Any]) -> tuple:
    """dets 字段约定: x,y,w,h 归一化到 [0,1]."""
    x = float(det.get("x", 0.0))
    y = float(det.get("y", 0.0))
    w = float(det.get("w", 0.0))
    h = float(det.get("h", 0.0))
    return x, y, x + w, y + h


def _iou(a: tuple, b: tuple) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    if union <= 0.0:
        return 0.0
    return inter / union


def _center_in(rod_xyxy: tuple, comp_xyxy: tuple) -> bool:
    rx1, ry1, rx2, ry2 = rod_xyxy
    cx, cy = (rx1 + rx2) / 2.0, (ry1 + ry2) / 2.0
    bx1, by1, bx2, by2 = comp_xyxy
    return bx1 <= cx <= bx2 and by1 <= cy <= by2


def filter_rod_by_companion(
    detections: List[Dict[str, Any]],
    iou_thr: float = DEFAULT_IOU_THR,
    rod_label: str = DEFAULT_ROD_LABEL,
    companion_labels: Iterable[str] = DEFAULT_COMPANION_LABELS,
) -> List[Dict[str, Any]]:
    """第 1 层：同一帧里 rod 必须和任一 companion 空间共现才保留。

    companion 判据（任一满足即通过）：
      - rod bbox 的中心点落在 companion bbox 内
      - rod bbox 与 companion bbox IoU >= iou_thr

    约定 detections 里每个元素至少包含 label/x/y/w/h（归一化坐标）。
    其他字段（confidence/class_id/track_id/mask/display_name/hidden...）保留不动。
    """
    if not detections:
        return detections
    companion_set: Set[str] = set(companion_labels)
    if not companion_set:
        return detections

    rod_indices: List[int] = []
    companion_boxes: List[tuple] = []
    for idx, det in enumerate(detections):
        lbl = det.get("label")
        if lbl == rod_label:
            rod_indices.append(idx)
        elif lbl in companion_set:
            companion_boxes.append(_norm_to_xyxy(det))

    if not rod_indices:
        return detections

    if not companion_boxes:
        return [d for i, d in enumerate(detections) if i not in set(rod_indices)]

    drop = set()
    for idx in rod_indices:
        rod_xyxy = _norm_to_xyxy(detections[idx])
        keep = False
        for cb in companion_boxes:
            if _center_in(rod_xyxy, cb) or _iou(rod_xyxy, cb) >= iou_thr:
                keep = True
                break
        if not keep:
            drop.add(idx)

    if not drop:
        return detections
    return [d for i, d in enumerate(detections) if i not in drop]


class RodSessionGate:
    """第 2 层：软时序门控

    当前周期启动以来如果从未见过 gate_labels 中的任一类，就抑制所有 rod。
    见过之后一直放行，直到下一次 reset()。

    使用方式:
        gate = RodSessionGate()
        # 每帧:
        dets = gate.update_and_filter(dets)
        # 新周期开始时:
        gate.reset()
    """

    def __init__(
        self,
        rod_label: str = DEFAULT_ROD_LABEL,
        gate_labels: Iterable[str] = DEFAULT_GATE_LABELS,
    ) -> None:
        self.rod_label = rod_label
        self.gate_labels: Set[str] = set(gate_labels)
        self._companion_seen = False

    def reset(self) -> None:
        self._companion_seen = False

    @property
    def companion_seen(self) -> bool:
        return self._companion_seen

    def update_and_filter(
        self, detections: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not detections:
            return detections

        if not self._companion_seen:
            for det in detections:
                if det.get("label") in self.gate_labels:
                    self._companion_seen = True
                    break

        if self._companion_seen:
            return detections
        return [d for d in detections if d.get("label") != self.rod_label]


def read_rod_filter_config(project_config: Dict[str, Any] | None) -> Dict[str, Any]:
    """从 project_config 读两层过滤的开关，缺字段全部按 OFF/默认处理。

    约定结构:
      project_config = {
        ...,
        "rod_companion_filter": {
            "enabled": False,
            "iou_threshold": 0.25,
            "rod_label": "传动杆",
            "companion_labels": ["大框架", "小框架", "侧板"],
        },
        "rod_session_gate": {
            "enabled": False,
            "rod_label": "传动杆",
            "gate_labels": ["大框架", "小框架"],
        },
      }
    """
    pc = project_config or {}
    comp_cfg = pc.get("rod_companion_filter") or {}
    gate_cfg = pc.get("rod_session_gate") or {}
    return {
        "companion_enabled": bool(comp_cfg.get("enabled", False)),
        "companion_iou_thr": float(comp_cfg.get("iou_threshold", DEFAULT_IOU_THR)),
        "companion_rod_label": str(comp_cfg.get("rod_label", DEFAULT_ROD_LABEL)),
        "companion_labels": tuple(
            comp_cfg.get("companion_labels") or DEFAULT_COMPANION_LABELS
        ),
        "gate_enabled": bool(gate_cfg.get("enabled", False)),
        "gate_rod_label": str(gate_cfg.get("rod_label", DEFAULT_ROD_LABEL)),
        "gate_labels": tuple(gate_cfg.get("gate_labels") or DEFAULT_GATE_LABELS),
    }
