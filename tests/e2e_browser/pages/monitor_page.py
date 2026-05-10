"""Monitor 监控页 Page Object（基于真 DOM 编写）。"""
from __future__ import annotations

import re
from typing import Optional

from .base_page import BasePage


class MonitorPage(BasePage):
    PATH = "/monitor"

    class Sel:
        FPS_LABEL = "text=FPS"
        VIDEO_AREA = "img, video, canvas"
        BTN_START = "button:has-text('开始')"
        BTN_STOP = "button:has-text('停止')"
        BTN_STANDBY = "button:has-text('待机')"
        BTN_CLEAR = "button:has-text('清零')"
        SELECT_PROJECT = ".el-select"
        OPERATOR_SELECT = ".el-select:has-text('选择操作员'), .el-select:has-text('操作员')"
        STEP_TABLE = "text=步骤统计"

    def has_fps_label(self) -> bool:
        return self.wait_for_text("FPS", timeout_ms=8000)

    def has_video_area(self) -> bool:
        return self.el_present(self.Sel.VIDEO_AREA)

    def status_summary(self) -> str:
        body = self.body_text()
        keys = ["FPS", "未运行", "运行中", "暂停", "已停止", "合格", "不合格", "OK", "NG"]
        return ", ".join(k for k in keys if k in body)

    def get_visible_step_labels(self) -> list[str]:
        body = self.body_text()
        out: list[str] = []
        for label in ("step_a", "step_b", "step_c", "SynthBox", "step_alarm",
                      "正面涂黑", "翻转", "反面涂黑", "放置"):
            if label in body:
                out.append(label)
        return out

    def get_active_project_name(self) -> Optional[str]:
        body = self.body_text()
        m = re.search(r"当前项目\s*\n([A-Za-z0-9_\-\u4e00-\u9fff]+)", body)
        return m.group(1) if m else None

    def get_counter(self, label: str) -> Optional[int]:
        body = self.body_text()
        for line in body.split("\n"):
            if label in line:
                idx = body.split("\n").index(line)
                lines = body.split("\n")
                if idx + 1 < len(lines) and lines[idx + 1].strip().isdigit():
                    return int(lines[idx + 1].strip())
        return None

    def get_cycle_count(self) -> Optional[int]:
        return self.get_counter("检测次数")

    def get_ok_count(self) -> Optional[int]:
        return self.get_counter("OK次数")

    def get_ng_count(self) -> Optional[int]:
        return self.get_counter("NG次数")

    def get_fps_value(self) -> Optional[int]:
        body = self.body_text()
        m = re.search(r"FPS:\s*(\d+)", body)
        return int(m.group(1)) if m else None

    def click_start(self):
        return self.click_text("开始", exact=True)

    def click_stop(self):
        return self.click_text("停止", exact=True)

    def click_standby(self):
        return self.click_text("待机", exact=True)

    def click_clear(self):
        return self.click_text("清零", exact=True)

    def has_step_table(self) -> bool:
        return self.wait_for_text("步骤统计", timeout_ms=4000)
