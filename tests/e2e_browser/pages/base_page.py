"""所有 Page Object 的基类。"""
from __future__ import annotations

import time
from typing import Optional


class BasePage:
    PATH: str = "/"

    def __init__(self, page, base_url: str):
        self.page = page
        self.base_url = base_url

    def goto(self, *, wait_networkidle: bool = True, timeout_ms: int = 15000):
        url = f"{self.base_url}/#{self.PATH}" if self.PATH.startswith("/") else f"{self.base_url}/#/{self.PATH}"
        self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_networkidle:
            try:
                self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass
        return self

    def wait_for_text(self, text: str, *, timeout_ms: int = 8000) -> bool:
        try:
            self.page.locator(f"text={text}").first.wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:
            return False

    def body_text(self, *, max_chars: int = 4000) -> str:
        return self.page.locator("body").inner_text(timeout=3000)[:max_chars]

    def screenshot(self, path: str, *, full_page: bool = True) -> str:
        self.page.screenshot(path=path, full_page=full_page)
        return path

    def click_text(self, text: str, *, exact: bool = False, timeout_ms: int = 5000):
        loc = self.page.get_by_text(text, exact=exact).first
        loc.wait_for(state="visible", timeout=timeout_ms)
        loc.click()
        return self

    def sleep(self, seconds: float):
        time.sleep(seconds)
        return self

    def el_present(self, selector: str) -> bool:
        try:
            return self.page.locator(selector).count() > 0
        except Exception:
            return False
