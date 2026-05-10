"""Source 视频源页 Page Object（基于真 DOM 编写）。"""
from __future__ import annotations

from .base_page import BasePage


SOURCE_RADIO_LABELS = {
    "camera": "摄像头",
    "hcnetsdk": "海康SDK直连",
    "rtsp": "网络摄像头",
    "video": "本地视频文件",
    "image": "本地图片文件",
}


class SourcePage(BasePage):
    PATH = "/source"

    class Sel:
        BTN_REFRESH = "button:has-text('刷新设备列表')"
        BTN_SAVE_AND_START = "button:has-text('保存并启动检测')"

    def has_source_form(self) -> bool:
        return self.wait_for_text("选择输入源类型", timeout_ms=5000)

    def status_keywords(self) -> list[str]:
        body = self.body_text()
        return [k for k in ("摄像头", "海康", "RTSP", "视频文件", "图片", "USB",
                            "已检测", "请先选择", "保存并启动检测") if k in body]

    def select_source_type(self, kind: str):
        if kind not in SOURCE_RADIO_LABELS:
            raise ValueError(f"未知 source 类型: {kind}, 允许: {list(SOURCE_RADIO_LABELS)}")
        label = SOURCE_RADIO_LABELS[kind]
        loc = self.page.get_by_text(label, exact=False).first
        loc.wait_for(state="visible", timeout=5000)
        loc.click()
        return self

    def click_refresh_devices(self):
        return self.click_text("刷新设备列表")

    def click_save_and_start(self):
        return self.click_text("保存并启动检测")

    def get_resolution_summary(self) -> str:
        body = self.body_text()
        for line in body.splitlines():
            if "x" in line and "p" in line and len(line) < 30:
                return line.strip()
        return ""

    def device_count_hint(self) -> str:
        body = self.body_text()
        for line in body.splitlines():
            if "已检测" in line or "检测到" in line:
                return line.strip()
        return ""
