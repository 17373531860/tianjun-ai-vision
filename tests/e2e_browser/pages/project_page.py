"""Project 项目页 Page Object（基于真 DOM 编写）。"""
from __future__ import annotations

from typing import Optional

from .base_page import BasePage


class ProjectPage(BasePage):
    PATH = "/project"

    class Sel:
        BTN_NEW = "button:has-text('新建项目')"
        BTN_ACTIVATE = "button:has-text('启用当前项目')"
        SEARCH_INPUT = "input[placeholder*='搜索项目']"
        DETAIL_PLACEHOLDER = "text=请选择左侧项目或新建一个项目"

    def has_new_project_button(self) -> bool:
        return self.wait_for_text("新建项目", timeout_ms=5000)

    def has_activate_button(self) -> bool:
        return self.wait_for_text("启用当前项目", timeout_ms=4000)

    def click_new_project(self):
        return self.click_text("新建项目", exact=True)

    def click_activate(self):
        return self.click_text("启用当前项目", exact=True)

    def search_project(self, keyword: str):
        loc = self.page.locator(self.Sel.SEARCH_INPUT).first
        loc.wait_for(state="visible", timeout=4000)
        loc.fill(keyword)
        return self

    def get_project_names(self) -> list[str]:
        body = self.body_text(max_chars=8000)
        out: list[str] = []
        for line in body.splitlines():
            line = line.strip()
            if line and line.startswith("DETECTION"):
                continue
            if line.startswith("模型:") or line.startswith("2026/") or line == "运行中":
                continue
            if line in ("新建项目", "启用当前项目", "项目管理", ""):
                continue
            if 1 < len(line) <= 25 and ("/" not in line) and (":" not in line):
                out.append(line)
        return out[:30]

    def click_project(self, name: str):
        loc = self.page.get_by_text(name, exact=True).first
        loc.wait_for(state="visible", timeout=5000)
        loc.click()
        return self

    def get_running_project_name(self) -> Optional[str]:
        body = self.body_text(max_chars=8000)
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if line.strip() == "运行中" and i > 0:
                return lines[i - 1].strip() or None
        return None
