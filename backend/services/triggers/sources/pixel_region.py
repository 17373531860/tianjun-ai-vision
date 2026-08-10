"""pixel_region — 画面标定区像素差分触发源 (虚拟按钮/光电指示灯/屏幕信号灯)。

params:
    channel        int    取帧工位 (默认 0)
    region         [x1,y1,x2,y2]  标定区 (原始帧像素坐标, 与 /snapshot 1:1)
    mode           "ref_diff"    与参考帧均值差分 (默认; 手遮虚拟按钮)
                   "brightness"  区域平均亮度过阈值 (指示灯亮)
                   "color_match" 与目标色 BGR 距离小于阈值 (色块出现)
    threshold      float  触发阈值 (ref_diff/color_match: 0~441 色距; brightness: 0~255)
    sample_ms      int    采样周期 (默认 150; 快照读帧缓存, 不进推理循环)
    ref_bgr        [b,g,r] 参考值; ref_diff 模式启动时无参考则自动取首帧标定
    ref_drift      bool   参考帧缓漂 (EMA 抗光照渐变, 默认 true; 仅未触发时更新)
    ref_drift_alpha float EMA 步长 (默认 0.02; 越大跟随光照越快, 但手遮太慢会被漂掉)
    invert         bool   反相 (低于阈值算触发)

信号语义: 电平型 — 差分超阈值期间持续 True。引擎侧做防抖 + 边沿。
标定: API /calibrate 调 recalibrate() 取当前区域均值存为参考 (并回写 DB)。
"""
import threading
import time
from typing import Optional

import numpy as np

from backend.services.triggers.sources.base import BaseTriggerSource

_DRIFT_ALPHA = 0.02   # 参考缓漂 EMA 步长 (每采样 2%, 秒级光照渐变可跟随)


class PixelRegionSource(BaseTriggerSource):
    type_name = "pixel_region"
    kind = "level"

    def __init__(self, params, emit_level, emit_pulse):
        super().__init__(params, emit_level, emit_pulse)
        err = self.validate_params(self.params)
        if err:
            raise ValueError(err)
        self.channel = int(self.params.get("channel") or 0)
        self.region = [int(v) for v in self.params["region"]]
        self.mode = (self.params.get("mode") or "ref_diff").lower()
        self.threshold = float(self.params.get("threshold") or 40)
        self.sample_ms = max(50, int(self.params.get("sample_ms") or 150))
        self.ref: Optional[np.ndarray] = None
        if self.params.get("ref_bgr"):
            self.ref = np.array(self.params["ref_bgr"], dtype=np.float64)
        self.ref_drift = bool(self.params.get("ref_drift", True))
        self.ref_drift_alpha = float(
            self.params.get("ref_drift_alpha") or _DRIFT_ALPHA)
        self.invert = bool(self.params.get("invert", False))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_metric: Optional[float] = None
        self._frame_missing = False

    @classmethod
    def validate_params(cls, params: dict) -> Optional[str]:
        region = (params or {}).get("region")
        if not (isinstance(region, (list, tuple)) and len(region) == 4):
            return "pixel_region 需要 region [x1,y1,x2,y2]"
        x1, y1, x2, y2 = region
        if not (x2 > x1 and y2 > y1):
            return "region 坐标须满足 x2>x1 且 y2>y1"
        mode = (params.get("mode") or "ref_diff").lower()
        if mode not in ("ref_diff", "brightness", "color_match"):
            return f"未知 mode: {mode}"
        if mode == "color_match" and not params.get("target_bgr"):
            return "color_match 模式需要 target_bgr [b,g,r]"
        return None

    # ---------------- 线程 ----------------

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name=f"trigger-pixel-ch{self.channel}")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._sample()
            except Exception:
                # 单次采样异常不退线程 (工位重配/换源瞬间帧形状可能变)
                pass
            self._stop.wait(self.sample_ms / 1000.0)

    # ---------------- 采样 ----------------

    def _grab_region_mean(self) -> Optional[np.ndarray]:
        """取标定区 BGR 均值。工位停流/无帧返回 None (挂起, 恢复自动继续)。"""
        from backend.api.channel_manager import channel_manager
        mgr = channel_manager.channels.get(self.channel)
        if mgr is None:
            return None
        frame = mgr.get_frame()
        if frame is None:
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = self.region
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        roi = frame[y1:y2, x1:x2]
        return roi.reshape(-1, roi.shape[-1]).mean(axis=0).astype(np.float64)

    def _sample(self):
        mean = self._grab_region_mean()
        if mean is None:
            self._frame_missing = True
            return  # 挂起: 不发电平, 保持引擎侧现状
        self._frame_missing = False

        if self.mode == "brightness":
            metric = float(mean.mean())
            triggered = metric > self.threshold
        elif self.mode == "color_match":
            target = np.array(self.params["target_bgr"], dtype=np.float64)
            metric = float(np.linalg.norm(mean - target))
            triggered = metric < self.threshold
        else:  # ref_diff
            if self.ref is None:
                self.ref = mean  # 首帧自动标定
                self._last_metric = 0.0
                return
            metric = float(np.linalg.norm(mean - self.ref))
            triggered = metric > self.threshold
            if self.ref_drift and not triggered:
                a = self.ref_drift_alpha
                self.ref = (1 - a) * self.ref + a * mean

        if self.invert:
            triggered = not triggered
        self._last_metric = metric
        self.emit_level(bool(triggered), {"metric": round(metric, 1)})

    # ---------------- 标定 (API 调) ----------------

    def recalibrate(self) -> dict:
        """取当前区域均值作参考帧。返回 ref_bgr 供 API 回写 DB 持久化。"""
        mean = self._grab_region_mean()
        if mean is None:
            raise ValueError(f"工位 {self.channel} 当前无画面, 无法标定")
        self.ref = mean
        return {"ref_bgr": [round(float(v), 1) for v in mean]}

    def snapshot(self):
        return {
            "channel": self.channel, "region": self.region, "mode": self.mode,
            "threshold": self.threshold, "metric": self._last_metric,
            "ref_bgr": [round(float(v), 1) for v in self.ref] if self.ref is not None else None,
            "frame_missing": self._frame_missing,
        }
