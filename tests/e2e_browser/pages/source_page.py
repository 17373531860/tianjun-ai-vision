"""Source 视频源页。"""
from __future__ import annotations

from .base_page import BasePage


class SourcePage(BasePage):
    PATH = "/source"

    class Sel:
        SOURCE_TYPE_SELECT = ".el-select"
        START_BTN = "text=启动"
        STOP_BTN = "text=停止"

    def has_source_select(self) -> bool:
        return self.el_present(self.Sel.SOURCE_TYPE_SELECT)

    def status_keywords(self) -> list[str]:
        body = self.body_text()
        return [k for k in ("USB", "RTSP", "视频文件", "图片", "synthetic", "已连接", "未连接") if k in body]
