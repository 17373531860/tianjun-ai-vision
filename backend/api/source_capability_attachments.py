# -*- coding: utf-8 -*-
"""能力挂件运行时 (2026-09, RFC 内置能力模型入仓与能力选用体系).

项目 pipeline_config.capability_attachments 声明的能力挂件在这里执行:

    [{"capability": "pose",    "params": {"interval_s": 1.0}},
     {"capability": "ocr",     "params": {"roi": [x,y,w,h], "interval_s": 5}},
     {"capability": "anomaly", "params": {"bank_id": "...", "roi": [...],
                                          "interval_s": 5, "event_id": 2,
                                          "cooldown_s": 30}}]

语义 (与 YOLO 主/副模型解耦, 挂件不进 ModelInstance 加载器):
  - pose:    对画面最大 person 框做节流朝向估计, 输出实时朝向角
             (装机标定看板 / 无规则也能观察朝向层质量);
  - ocr:     按 ROI 定时读字, 输出最近一次文本列表 (读铭牌/屏幕数值);
  - anomaly: 按 ROI 定时对记忆库比对, 输出最近得分; 越限可借事件响应面
             (event_id + cooldown) 报异常。

线程模型: 判定在推理线程 (节流后零开销早退); 重推理 (ocr/anomaly/pose)
投递到即抛后台线程, busy 标志防堆积 (上一次没跑完就跳过本次), ROI 裁剪
copy() 后交线程 (帧缓冲会被复用)。事件触发回投推理线程下一拍执行
(fire_external_event_response 不允许跨线程调)。任何异常隔离, 不碰主链路。

无挂件配置 → _update_capability_attachments 一个属性判断早退, 全部存量项目零开销。
"""
from __future__ import annotations

import threading
import time

_VALID_CAPS = ("pose", "ocr", "anomaly")


def parse_capability_attachments(pipeline_config: dict) -> list:
    """解析并规范化挂件配置 (坏项丢弃, 不抛错)。"""
    raw = (pipeline_config or {}).get("capability_attachments") or []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cap = item.get("capability")
        if cap not in _VALID_CAPS:
            continue
        p = item.get("params") or {}
        att = {"capability": cap,
               "interval_s": max(0.5, float(p.get("interval_s") or
                                            (1.0 if cap == "pose" else 5.0)))}
        # 2026-09 多块化: roi 支持单块 [x,y,w,h] / 多块 [[x,y,w,h],...],
        # 统一存 canonical 多块形态 [[x,y,w,h], ...]
        from backend.api.source_geometry import normalize_rects
        rects = normalize_rects(p.get("roi"))
        if rects:
            att["roi"] = rects
        if cap == "anomaly":
            att["bank_id"] = p.get("bank_id") or None
            att["event_id"] = p.get("event_id")
            att["cooldown_s"] = max(1.0, float(p.get("cooldown_s") or 30.0))
        if cap == "ocr":
            att["subject_label"] = p.get("subject_label")  # 预留: 跟随框读字
        out.append(att)
    return out


class CapabilityAttachmentsMixin:
    """宿主: VideoSourceManager。"""

    def _apply_capability_attachments(self, pipeline_config: dict):
        """项目配置应用时重建挂件状态 (source_project_config_apply 调用)。"""
        atts = parse_capability_attachments(pipeline_config)
        self._cap_attachments = atts or None
        self._cap_outputs = {}
        self._cap_busy = {}
        self._cap_last_run = {}
        self._cap_pending_events = []
        self._cap_last_event_ts = {}
        if atts:
            print(f"[CapAttach] 能力挂件启用: {[a['capability'] for a in atts]}")

    # ---------- 推理线程每帧入口 ----------
    def _update_capability_attachments(self, detections: list, frame):
        atts = getattr(self, "_cap_attachments", None)
        if not atts:
            return
        now = time.time()
        # 先兑现后台线程回投的事件 (推理线程内触发, 线程模型与其他事件一致)
        pending = self._cap_pending_events
        while pending:
            ev_id, msg = pending.pop(0)
            try:
                self.fire_external_event_response(ev_id, msg,
                                                  source="capability_attachment")
            except Exception as e:
                print(f"[CapAttach] 事件触发失败 (已隔离): {e}")
        if frame is None:
            return
        for att in atts:
            cap = att["capability"]
            if now - self._cap_last_run.get(cap, 0.0) < att["interval_s"]:
                continue
            if self._cap_busy.get(cap):
                continue
            self._cap_last_run[cap] = now
            try:
                if cap == "pose":
                    self._cap_run_pose(att, detections, frame, now)
                elif cap == "ocr":
                    self._cap_run_ocr(att, frame, now)
                elif cap == "anomaly":
                    self._cap_run_anomaly(att, frame, now)
            except Exception as e:
                print(f"[CapAttach] {cap} 挂件异常 (已隔离): {e}")

    def capability_attachment_outputs(self) -> dict:
        """results 载荷出口 (source_routes 消费); 无挂件返回 {}。"""
        if not getattr(self, "_cap_attachments", None):
            return {}
        return dict(self._cap_outputs)

    # ---------- 各能力执行体 ----------
    def _cap_spawn(self, cap: str, fn):
        self._cap_busy[cap] = True

        def run():
            try:
                out = fn()
                if out is not None:
                    self._cap_outputs[cap] = out
            except Exception as e:
                self._cap_outputs[cap] = {"ts": time.time(), "error": str(e)[:200]}
            finally:
                self._cap_busy[cap] = False

        threading.Thread(target=run, daemon=True,
                         name=f"cap-attach-{cap}-{self.channel_id}").start()

    def _cap_crop(self, frame, roi):
        h, w = frame.shape[:2]
        if not roi:
            return frame.copy(), (0.0, 0.0, 1.0, 1.0)
        x, y, rw, rh = roi
        x1 = max(0, int(x * w)); y1 = max(0, int(y * h))
        x2 = min(w, int((x + rw) * w)); y2 = min(h, int((y + rh) * h))
        if x2 <= x1 or y2 <= y1:
            return frame.copy(), (0.0, 0.0, 1.0, 1.0)
        return frame[y1:y2, x1:x2].copy(), (x, y, rw, rh)

    def _cap_run_pose(self, att, detections, frame, now):
        """画面最大 person 框 → 朝向估计 (装机标定/朝向层观察)。"""
        persons = [d for d in detections or [] if d.get("label") == "person"]
        if not persons:
            return
        h, w = frame.shape[:2]
        best = max(persons, key=lambda d: (d.get("w") or 0) * (d.get("h") or 0))
        box_px = ((best.get("x") or 0) * w, (best.get("y") or 0) * h,
                  ((best.get("x") or 0) + (best.get("w") or 0)) * w,
                  ((best.get("y") or 0) + (best.get("h") or 0)) * h)
        img = frame.copy()

        def work():
            from backend.services.person_orientation import estimate_yaw
            r = estimate_yaw(img, box_px)
            if r is None:
                return {"ts": now, "yaw_deg": None}
            return {"ts": now, "yaw_deg": r.get("yaw_deg"),
                    "head_yaw_deg": r.get("head_yaw_deg"),
                    "conf": r.get("conf"), "backend": r.get("backend")}

        self._cap_spawn("pose", work)

    def _cap_crops(self, frame, rects):
        """多块矩形逐块裁剪 (rects=None 时整帧一块)。返回 [(crop, roi_used), ...]。"""
        return [self._cap_crop(frame, r) for r in (rects or [None])]

    def _cap_run_ocr(self, att, frame, now):
        crops = self._cap_crops(frame, att.get("roi"))

        def work():
            from backend.services import ocr_engine
            texts = []
            for crop, _ in crops:
                texts.extend(ocr_engine.read_text(crop) or [])
            # roi 透出: 单块保持 [x,y,w,h] (历史形态), 多块为 [[x,y,w,h],...]
            rois = [list(r) for _, r in crops]
            return {"ts": now, "roi": rois[0] if len(rois) == 1 else rois,
                    "texts": [{"text": t.get("text"), "score": t.get("score")}
                              for t in texts][:20]}

        self._cap_spawn("ocr", work)

    def _cap_run_anomaly(self, att, frame, now):
        bank_id = att.get("bank_id")
        if not bank_id:
            return
        crops = self._cap_crops(frame, att.get("roi"))
        event_id = att.get("event_id")
        cooldown = att.get("cooldown_s", 30.0)

        def work():
            from backend.services import anomaly_engine
            # 逐块评分取最坏块 (最高分); 任一块异常即整体异常
            best = None
            any_anomaly = False
            for crop, _ in crops:
                r = anomaly_engine.score_image(bank_id, crop)
                if best is None or float(r.get("score") or 0) > float(best.get("score") or 0):
                    best = r
                any_anomaly = any_anomaly or bool(r.get("is_anomaly"))
            rois = [list(r) for _, r in crops]
            out = {"ts": now, "roi": rois[0] if len(rois) == 1 else rois,
                   "score": best.get("score"), "threshold": best.get("threshold"),
                   "is_anomaly": any_anomaly}
            if out["is_anomaly"] and event_id is not None:
                last = self._cap_last_event_ts.get("anomaly", 0.0)
                if now - last >= cooldown:
                    self._cap_last_event_ts["anomaly"] = now
                    # 回投推理线程下一拍触发 (事件链路不允许跨线程直调)
                    self._cap_pending_events.append(
                        (event_id, f"异常检测越限: score={out['score']:.3f}"))
            return out

        self._cap_spawn("anomaly", work)
