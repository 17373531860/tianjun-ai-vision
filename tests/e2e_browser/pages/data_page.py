"""Data 数据页 Page Object（基于真 DOM 编写）。"""
from __future__ import annotations

import re
from typing import Optional

from .base_page import BasePage


DATA_TABS = ("记录设置", "数据导出", "存储与清理")
EXPORT_BUTTONS = (
    "导出当日数据",
    "导出某周数据",
    "导出某月数据",
    "导出日期范围",
    "自定义导出 / 客户模板",
    "实时规则（扫码自动写入）",
)


class DataPage(BasePage):
    PATH = "/data"

    class Sel:
        WORKPIECE_INPUT = "input[placeholder*='工件码']"
        DATE_PICKER = "input[placeholder='选择日期']"
        # 实时规则编辑子弹窗里的「触发事件」select
        REALTIME_TRIGGER_SELECT = ".rt-rules-dialog .el-dialog .el-select:has(.el-input__inner)"
        # CustomExportDialog Session 下拉
        EXPORT_SESSION_SELECT = ".el-select[class*=''] :text('按 ID/标识/UUID 搜索')"

    def has_data_center(self) -> bool:
        return self.wait_for_text("数据中心", timeout_ms=5000)

    def pick_date(self, date_str: str):
        """date_str 形如 '2026-05-10'"""
        loc = self.page.locator(self.Sel.DATE_PICKER).first
        loc.wait_for(state="visible", timeout=4000)
        loc.click()
        loc.fill(date_str)
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(600)
        return self

    def get_tabs(self) -> list[str]:
        body = self.body_text()
        return [t for t in DATA_TABS if t in body]

    def switch_tab(self, name: str):
        if name not in DATA_TABS:
            raise ValueError(f"未知 tab: {name}, 允许: {DATA_TABS}")
        # 用 .el-tabs__item 精确定位，避免和按钮文案/注释串扰
        loc = self.page.locator(f".el-tabs__item:has-text('{name}')").first
        loc.wait_for(state="visible", timeout=8000)
        loc.click()
        self.page.wait_for_timeout(400)
        return self

    def search_workpiece(self, code: str):
        loc = self.page.locator(self.Sel.WORKPIECE_INPUT).first
        loc.wait_for(state="visible", timeout=4000)
        loc.fill(code)
        return self

    def click_export(self, kind: str):
        if kind not in EXPORT_BUTTONS:
            raise ValueError(f"未知导出按钮: {kind}, 允许: {EXPORT_BUTTONS}")
        return self.click_text(kind)

    def get_total_cycle_count(self) -> Optional[int]:
        body = self.body_text()
        m = re.search(r"总周期数\s*\n\s*(\d+)", body)
        return int(m.group(1)) if m else None

    def get_yield_rate_text(self) -> Optional[str]:
        body = self.body_text()
        m = re.search(r"良率\s*\n\s*([0-9.]+\s*%)", body)
        return m.group(1) if m else None

    def has_backup_button(self) -> bool:
        return self.wait_for_text("备份数据库", timeout_ms=3000)

    # ===== v3.6.2 会话标识 =====
    def has_session_card(self) -> bool:
        """会话列表至少有一项 (.session-card)"""
        return self.page.locator(".session-card").count() > 0

    def get_first_session_id_label(self) -> Optional[str]:
        """读出第一个 session-card 上的"标识:" 行，没有返回 None"""
        if not self.has_session_card():
            return None
        first = self.page.locator(".session-card").first
        text = first.text_content() or ""
        m = re.search(r"标识:\s*([^\s]+)", text)
        return m.group(1) if m else None

    def click_first_session_rename(self):
        """点击第一个 session-card 的「重命名」按钮（黄色 Edit 圆形按钮）"""
        first = self.page.locator(".session-card").first
        btn = first.locator("button.el-button--warning").first
        btn.click()
        return self

    def find_session_card_by_text(self, text_substr: str):
        """返回首个文本含 text_substr 的 session-card locator；找不到返回 None"""
        loc = self.page.locator(f".session-card:has-text('{text_substr}')").first
        if loc.count() == 0:
            return None
        return loc

    def click_rename_on_session_card_with_text(self, text_substr: str) -> bool:
        """在含指定文本的 session-card 上点击重命名按钮"""
        card = self.find_session_card_by_text(text_substr)
        if card is None:
            return False
        btn = card.locator("button.el-button--warning").first
        if btn.count() == 0:
            return False
        btn.click()
        return True

    def session_card_has_text(self, text_substr: str) -> bool:
        return self.page.locator(f".session-card:has-text('{text_substr}')").count() > 0

    def fill_rename_session_dialog(self, value: str, confirm: bool = True):
        """ElMessageBox.prompt 弹出后填值"""
        self.page.wait_for_selector(".el-message-box__input input", state="visible", timeout=4000)
        inp = self.page.locator(".el-message-box__input input").first
        inp.fill(value)
        if confirm:
            self.page.locator(".el-message-box__btns button.el-button--primary").click()
        else:
            self.page.locator(".el-message-box__btns button:not(.el-button--primary)").click()
        return self

    def get_first_session_card_text(self) -> str:
        if not self.has_session_card():
            return ""
        return self.page.locator(".session-card").first.text_content() or ""

    # ===== v3.6.2 实时规则弹窗 =====
    def open_realtime_rules_dialog(self):
        """点 '实时规则（扫码自动写入）' 按钮"""
        self.click_text("实时规则（扫码自动写入）")
        self.page.wait_for_selector(".rt-rules-dialog", state="visible", timeout=4000)
        return self

    def close_realtime_rules_dialog(self):
        """按 ESC 键关闭最外层 el-dialog"""
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(300)
        return self

    def click_realtime_create_rule(self):
        """规则列表里点 '新建规则' 按钮（弹出编辑子对话框）"""
        self.page.locator(".rt-rules-dialog button:has-text('新建规则')").first.click()
        self.page.wait_for_timeout(500)
        return self

    def _open_trigger_dropdown(self):
        """打开编辑子对话框里的 trigger_event 下拉。子对话框 append-to-body，
        定位用 dialog title '新建实时规则' / '编辑规则'。"""
        # 子弹窗根
        editor_root = self.page.locator(
            ".el-dialog:has-text('新建实时规则'), .el-dialog:has-text('编辑规则')"
        ).first
        editor_root.wait_for(state="visible", timeout=4000)
        # 触发事件 form-item 内的 el-select；ElementPlus 2.x 用 .el-form-item__label
        trigger_item = editor_root.locator(
            ".el-form-item:has(.el-form-item__label:has-text('触发事件'))"
        ).first
        sel = trigger_item.locator(".el-select").first
        sel.click()
        self.page.wait_for_timeout(500)

    def get_realtime_trigger_options(self) -> dict:
        """打开 trigger_event 下拉，返回 {选项文本: 是否可点}"""
        self._open_trigger_dropdown()
        options_loc = self.page.locator(
            ".el-select-dropdown:visible .el-select-dropdown__item"
        )
        result = {}
        n = options_loc.count()
        for i in range(n):
            opt = options_loc.nth(i)
            label = (opt.text_content() or "").strip()
            cls = opt.get_attribute("class") or ""
            disabled = "is-disabled" in cls
            result[label] = not disabled
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(200)
        return result

    def select_realtime_trigger(self, label_substr: str) -> bool:
        """在 trigger_event 下拉里选中含 label_substr 的项；返回是否成功"""
        self._open_trigger_dropdown()
        opt = self.page.locator(
            f".el-select-dropdown:visible .el-select-dropdown__item:has-text('{label_substr}')"
        ).first
        if opt.count() == 0:
            self.page.keyboard.press("Escape")
            return False
        cls = opt.get_attribute("class") or ""
        if "is-disabled" in cls:
            self.page.keyboard.press("Escape")
            return False
        opt.click()
        self.page.wait_for_timeout(300)
        return True

    # ===== v3.6.2 自定义导出弹窗 =====
    def open_custom_export_dialog(self):
        """点 '自定义导出 / 客户模板' 按钮"""
        self.click_text("自定义导出 / 客户模板")
        # 自定义导出对话框 root 没有特定 class，用 title 找
        self.page.wait_for_selector(".el-dialog:has-text('自定义导出')", state="visible", timeout=4000)
        return self

    def close_custom_export_dialog(self):
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(300)
        return self

    def select_custom_export_scope_session(self):
        """范围选「单 session (按 ID)」"""
        self.page.locator(
            ".el-dialog:has-text('自定义导出') .el-radio:has-text('单 session')"
        ).first.click()
        self.page.wait_for_timeout(400)
        return self

    def _export_session_form_item(self):
        """定位 CustomExportDialog 内 label='Session' 的 form-item"""
        dlg = self.page.locator(".el-dialog:has-text('自定义导出')").first
        return dlg.locator(
            ".el-form-item:has(.el-form-item__label:text-is('Session'))"
        ).first

    def open_export_session_select(self):
        """点开 Session 下拉，返回当前可见选项数量"""
        item = self._export_session_form_item()
        item.wait_for(state="visible", timeout=4000)
        sel = item.locator(".el-select").first
        sel.click()
        self.page.wait_for_timeout(800)
        return self.page.locator(
            ".el-select-dropdown:visible .el-select-dropdown__item"
        ).count()

    def get_export_session_options(self, max_n: int = 20) -> list[str]:
        opts = self.page.locator(
            ".el-select-dropdown:visible .el-select-dropdown__item"
        )
        n = min(max_n, opts.count())
        return [(opts.nth(i).text_content() or "").strip() for i in range(n)]

    def filter_export_session(self, keyword: str) -> int:
        item = self._export_session_form_item()
        sel_input = item.locator(".el-select input").first
        sel_input.fill(keyword)
        self.page.wait_for_timeout(500)
        return self.page.locator(
            ".el-select-dropdown:visible .el-select-dropdown__item"
        ).count()
