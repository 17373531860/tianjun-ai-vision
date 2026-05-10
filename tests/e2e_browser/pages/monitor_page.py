"""Monitor 监控页。"""
from __future__ import annotations

from .base_page import BasePage


class MonitorPage(BasePage):
    PATH = "/monitor"

    class Sel:
        FPS_LABEL = "text=FPS"
        VIDEO_AREA = "img, video, canvas"
        CYCLE_COUNT_HINT = "text=/合格|不合格|OK|NG/"

    def has_fps_label(self) -> bool:
        return self.wait_for_text("FPS", timeout_ms=8000)

    def has_video_area(self) -> bool:
        return self.el_present(self.Sel.VIDEO_AREA)

    def status_summary(self) -> str:
        body = self.body_text()
        keys = ["FPS", "未运行", "运行中", "暂停", "合格", "不合格", "OK", "NG"]
        return ", ".join(k for k in keys if k in body)

    def get_visible_step_labels(self) -> list[str]:
        body = self.body_text()
        out: list[str] = []
        for label in ("step_a", "step_b", "step_c", "SynthBox", "step_alarm"):
            if label in body:
                out.append(label)
        return out
