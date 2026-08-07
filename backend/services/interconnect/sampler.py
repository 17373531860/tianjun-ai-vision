# -*- coding: utf-8 -*-
"""现场帧采样决策 (推理线程热路径).

调用点: source_inference_loop_mixin._inference_loop 每帧一次。
纪律 (与 detection_frame 插件 hook 同级):
- 采样关闭时 O(1) 早退 (一个模块级 bool)
- 判定/限流是纯内存操作; JPEG 编码只发生在限流命中后 (频率 ≤ 每通道
  1/min_interval_s 且全局 ≤ max_per_hour), 不拖慢推理
- 任何异常由调用方 try/except 隔离, 绝不影响检测主链路

采样原因 (契约 reason 枚举, 按优先级):
- ng_event          : NG 事件刚触发 (由 _trigger_event 置 mgr 上的 pending 标志)
- detection_dropout : 检出闪断 — 某标签连续 ≥ dropout_min_frames 帧稳定出现后
                      当前帧突然消失且周期仍进行中。目标物理上不可能瞬移,
                      这是"目标还在而模型没检出"的漏检强证据
- low_confidence    : 任一检测框置信度落在 [conf_min, conf_max) 疑难带
- no_detection      : 本帧无检测结果。默认只在"周期进行中"(工件在检) 时采 —
                      产线大部分时间没有工件, 周期外空帧是合法空景不是漏检

"周期进行中"判据: mgr.current_cycle_uuid 非空 (v3.38 RFC: uuid 即周期进行中
标记, 所有守门看 uuid 不看 id)。
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from datetime import datetime

from backend.services.interconnect import config as icfg

_state_lock = threading.Lock()
_last_sample_ts: dict[int, float] = {}   # channel_id -> 上次采样时间
_hour_window_start = 0.0
_hour_count = 0

# 检出闪断追踪: channel_id -> {label -> 连续出现帧数}。
# 每个通道只被自己的推理线程读写 (通道间 key 不同), 不需要锁。
_label_streaks: dict[int, dict[str, int]] = {}

# NG 采帧 pending 标志属性名 (source_event_trigger_mixin 置位, 本模块消费)
NG_FLAG_ATTR = "_interconnect_ng_sample_pending"


def maybe_sample_frame(mgr, frame, detections) -> None:
    """每帧调用: 判定是否采样, 命中则编码入队。"""
    if not icfg.is_sampling_active():
        return
    if frame is None:
        return
    cfg = icfg.sampling_snapshot()

    channel_id = int(getattr(mgr, "channel_id", 0) or 0)
    in_cycle = bool(getattr(mgr, "current_cycle_uuid", None))
    visible = [d for d in (detections or []) if not d.get("hidden")]

    # 闪断追踪每帧都要更新 (不能只在其他条件命中时更新, 否则 streak 断档)
    dropped = _update_streaks(cfg, channel_id, visible)

    ng_pending = bool(getattr(mgr, NG_FLAG_ATTR, False))
    reason = _decide_reason(cfg, visible, ng_pending,
                            in_cycle=in_cycle, dropped_labels=dropped)
    if ng_pending:
        # 无论是否命中都清掉, 防止陈旧 NG 标志把后续无关帧误标 ng_event
        setattr(mgr, NG_FLAG_ATTR, False)
    if reason is None:
        return

    project_name = (getattr(mgr, "project_config", None) or {}).get("name") or ""
    if not project_name:
        return  # 无项目名无法与训练平台对齐, 不采

    if not _pass_rate_limit(cfg, channel_id):
        return

    context = {"in_cycle": in_cycle}
    if dropped:
        context["dropped_labels"] = dropped
        context["dropout_min_frames"] = int(cfg.get("dropout_min_frames", 5) or 5)
    _encode_and_enqueue(cfg, mgr, frame, detections, reason,
                        project_name, channel_id, context)


def _update_streaks(cfg: dict, channel_id: int, visible: list) -> list[str]:
    """更新本通道各标签的连续出现帧数; 返回本帧"闪断"的标签列表。

    闪断 = 该标签已连续出现 ≥ dropout_min_frames 帧, 本帧突然不在。
    """
    if not cfg.get("dropout_enabled"):
        _label_streaks.pop(channel_id, None)
        return []
    min_frames = int(cfg.get("dropout_min_frames", 5) or 5)
    streaks = _label_streaks.setdefault(channel_id, {})
    now_labels = {str(d.get("label", "")) for d in visible if d.get("label")}

    dropped = [label for label, n in streaks.items()
               if n >= min_frames and label not in now_labels]
    # 消失的标签 (含刚闪断的) 归零重计; 在场的累加
    for label in list(streaks.keys()):
        if label not in now_labels:
            del streaks[label]
    for label in now_labels:
        streaks[label] = streaks.get(label, 0) + 1
    return dropped


def _decide_reason(cfg: dict, detections, ng_pending: bool, *,
                   in_cycle: bool = True,
                   dropped_labels: list | None = None) -> str | None:
    if ng_pending and cfg.get("ng_event_enabled"):
        return "ng_event"
    if dropped_labels and cfg.get("dropout_enabled") and in_cycle:
        return "detection_dropout"
    visible = [d for d in (detections or []) if not d.get("hidden")]
    if not visible:
        if not cfg.get("no_detection_enabled"):
            return None
        if cfg.get("no_detection_in_cycle_only", True) and not in_cycle:
            return None
        return "no_detection"
    if cfg.get("low_conf_enabled"):
        lo = float(cfg.get("conf_min", 0.2))
        hi = float(cfg.get("conf_max", 0.6))
        for d in visible:
            conf = float(d.get("confidence", 0.0) or 0.0)
            if lo <= conf < hi:
                return "low_confidence"
    return None


def _pass_rate_limit(cfg: dict, channel_id: int) -> bool:
    global _hour_window_start, _hour_count
    now = time.time()
    min_interval = float(cfg.get("min_interval_s", 10.0) or 0.0)
    max_per_hour = int(cfg.get("max_per_hour", 60) or 0)
    with _state_lock:
        if now - _last_sample_ts.get(channel_id, 0.0) < min_interval:
            return False
        if now - _hour_window_start >= 3600.0:
            _hour_window_start = now
            _hour_count = 0
        if max_per_hour > 0 and _hour_count >= max_per_hour:
            return False
        _last_sample_ts[channel_id] = now
        _hour_count += 1
    return True


def _encode_and_enqueue(cfg: dict, mgr, frame, detections, reason: str,
                        project_name: str, channel_id: int,
                        context: dict) -> None:
    import cv2  # 延迟 import: 本模块可能被测试在无 cv2 场景 import

    quality = int(cfg.get("jpeg_quality", 85) or 85)
    ok, buf = cv2.imencode(".jpg", frame,
                           [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return
    jpg = buf.tobytes()
    h, w = frame.shape[:2]

    include_annotations = bool(cfg.get("include_annotations", True))
    annotations = []
    if include_annotations and reason != "no_detection":
        annotations = detections_to_annotations(detections)

    from backend.services.interconnect.identity import get_identity
    sample_id = uuid.uuid4().hex
    meta = {
        "contract": "1.1",
        "sample_id": sample_id,
        "project_name": project_name,
        "source_product": "tianjun-ai-vision",
        **get_identity(),
        "channel_id": channel_id,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reason": reason,
        "context": context,
        "model": None,
        "image": {"width": int(w), "height": int(h),
                  "sha256": hashlib.sha256(jpg).hexdigest()},
        "include_annotations": include_annotations,
        "annotations": annotations,
    }

    from backend.services.interconnect.uploader import get_queue, ensure_worker
    q = get_queue()
    if q.enqueue(sample_id=sample_id, meta=meta, image=jpg):
        q.log_add(sample_id=sample_id, project_name=project_name,
                  reason=reason, channel_id=channel_id)
        ensure_worker()


def detections_to_annotations(detections) -> list[dict]:
    """检测框 (归一化左上角 x,y + w,h) → 契约标注 (归一化中心点 cx,cy + w,h)。"""
    out = []
    for d in (detections or []):
        if d.get("hidden"):
            continue
        try:
            x = float(d.get("x", 0.0))
            y = float(d.get("y", 0.0))
            w = float(d.get("w", 0.0))
            h = float(d.get("h", 0.0))
            out.append({
                "class_name": str(d.get("label", "")),
                "cx": round(x + w / 2.0, 6),
                "cy": round(y + h / 2.0, 6),
                "w": round(w, 6),
                "h": round(h, 6),
                "confidence": round(float(d.get("confidence", 0.0) or 0.0), 4),
            })
        except (TypeError, ValueError):
            continue
    return out


def reset_rate_limit_state() -> None:
    """测试用: 清空限流与闪断追踪状态。"""
    global _hour_window_start, _hour_count
    with _state_lock:
        _last_sample_ts.clear()
        _hour_window_start = 0.0
        _hour_count = 0
    _label_streaks.clear()
