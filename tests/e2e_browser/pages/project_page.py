"""Project 项目页。"""
from __future__ import annotations

from .base_page import BasePage


class ProjectPage(BasePage):
    PATH = "/project"

    class Sel:
        ADD_BTN = "text=新增项目"
        ACTIVATE_BTN = "text=激活"
        PROJECT_LIST = ".project-list, .el-table"

    def has_project_list(self) -> bool:
        return self.el_present(self.Sel.PROJECT_LIST)

    def click_add(self):
        return self.click_text("新增项目", timeout_ms=5000)

    def click_activate(self):
        return self.click_text("激活", timeout_ms=5000)
