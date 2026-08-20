"""Settings 设置页 Page Object（基于真 DOM 编写）。"""
from __future__ import annotations

from typing import Optional

from .base_page import BasePage


# 流水线串行 / 工位组互通已迁「工位与输入源」页 (信息架构重构)
SETTINGS_TABS = ("显示设置", "检测框设置", "性能设置", "插件管理",
                 "账号鉴权")


class SettingsPage(BasePage):
    PATH = "/settings"

    class Sel:
        BTN_SAVE = "button:has-text('保存此工位设置')"
        BTN_ADD_OPERATOR = ".el-button--success:has-text('添加')"
        BTN_REFRESH_CHANNEL = "button:has-text('刷新')"
        BRAND_INPUT = "input[placeholder*='天军']"
        SOFTWARE_INPUT = "input[placeholder*='视觉AI']"
        DEVICE_INPUT = "input[placeholder*='251011']"

    def has_tabs(self) -> bool:
        body = self.body_text()
        return all(t in body for t in SETTINGS_TABS)

    def get_visible_tabs(self) -> list[str]:
        body = self.body_text()
        return [t for t in SETTINGS_TABS if t in body]

    def switch_tab(self, name: str):
        if name not in SETTINGS_TABS:
            raise ValueError(f"未知 tab: {name}, 允许: {SETTINGS_TABS}")
        return self.click_text(name)

    def get_visible_section_titles(self) -> list[str]:
        body = self.body_text()
        return [k for k in ("PT", "CT", "合并", "最后一次", "品牌", "工厂",
                            "License", "通用", "外观", "操作员管理") if k in body]

    def has_pt_aggregate_options(self) -> bool:
        body = self.body_text()
        return ("PT 计算方式" in body) and ("合并" in body) and ("最后一次" in body)

    def get_pt_aggregate_current(self) -> Optional[str]:
        body = self.body_text()
        if "合并(SUM)" in body or "合并" in body:
            for line in body.splitlines():
                line = line.strip()
                if line in ("合并", "最后一次"):
                    return line
        return None

    def get_brand_input_value(self) -> Optional[str]:
        try:
            return self.page.locator(self.Sel.BRAND_INPUT).first.input_value(timeout=2000)
        except Exception:
            return None

    def fill_brand(self, value: str):
        loc = self.page.locator(self.Sel.BRAND_INPUT).first
        loc.wait_for(state="visible", timeout=4000)
        loc.fill(value)
        return self

    def click_save_workstation(self):
        return self.click_text("保存此工位设置")

    def has_operator_table(self) -> bool:
        body = self.body_text()
        return "操作员管理" in body and "工号" in body and "角色" in body

    def get_operator_names(self) -> list[str]:
        body = self.body_text(max_chars=6000)
        out: list[str] = []
        in_table = False
        for line in body.splitlines():
            s = line.strip()
            if s == "操作员管理":
                in_table = True
                continue
            if not in_table:
                continue
            if s in ("姓名", "工号", "角色", "状态", "操作", "添加", "编辑"):
                continue
            if s.startswith("AUTO_QA_") or (1 < len(s) < 40 and "\t" not in s):
                if s and not s.isdigit() and s not in out:
                    out.append(s)
            if "顶部导航栏" in s:
                break
        return out[:20]
