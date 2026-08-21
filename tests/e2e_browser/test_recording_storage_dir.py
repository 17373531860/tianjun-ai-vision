# -*- coding: utf-8 -*-
"""录像存储位置 (v3.54) e2e: 数据中心 → 记录设置 → 录像存储位置。

覆盖:
  1. 卡片可见, 默认态显示"默认"标签
  2. 填合法目录保存 → 生效标签变"自定义" + 后端 GET 落库一致 (双向验证)
  3. 填非法目录 (系统目录) → 后端 400, 前端报错, 配置不被污染
  4. 清空保存 → 恢复默认
"""
from __future__ import annotations

import tempfile
import os

import requests
from playwright.sync_api import expect

from .pages import DataPage

API = "/api/v1/data/storage/recording-dir"


def _backend_state(api_url: str) -> dict:
    r = requests.get(f"{api_url}{API}", timeout=5)
    r.raise_for_status()
    return r.json()


def _open_record_tab(page, base_url) -> DataPage:
    dp = DataPage(page, base_url).goto()
    assert dp.has_data_center()
    dp.switch_tab("记录设置")
    page.wait_for_selector("[data-testid='recording-dir-input']",
                           state="visible", timeout=8000)
    # 等初始状态 GET 渲染完再交互, 避免与保存后的状态更新竞态
    page.wait_for_selector("[data-testid='recording-dir-status']",
                           state="visible", timeout=8000)
    return dp


def test_recording_dir_card_visible_default(page, base_url, api_url):
    # 起点确保干净
    requests.put(f"{api_url}{API}", json={"dir": ""}, timeout=5)
    _open_record_tab(page, base_url)
    status = page.locator("[data-testid='recording-dir-status']")
    status.wait_for(state="visible", timeout=8000)
    assert "当前生效" in (status.text_content() or "")
    assert "默认" in (status.text_content() or "")


def test_recording_dir_save_and_backend_roundtrip(page, base_url, api_url):
    custom = os.path.join(tempfile.gettempdir(), "tj_e2e_rec_dir")
    try:
        _open_record_tab(page, base_url)
        inp = page.locator("input[data-testid='recording-dir-input'], [data-testid='recording-dir-input'] input").first
        inp.fill(custom)
        page.locator("[data-testid='recording-dir-save']").click()
        page.wait_for_selector(".el-message--success", timeout=8000)

        # UI 状态翻自定义 (toast 先于组件重渲染出现, 用轮询断言等最终一致)
        status = page.locator("[data-testid='recording-dir-status']")
        expect(status).to_contain_text("自定义", timeout=5000)

        # 双向验证: 后端 GET 与 UI 一致
        state = _backend_state(api_url)
        assert state["using_custom"] is True
        assert os.path.abspath(state["effective_root"]) == os.path.abspath(custom)

        # 清空恢复默认
        inp.fill("")
        page.locator("[data-testid='recording-dir-save']").click()
        page.wait_for_timeout(1200)
        state = _backend_state(api_url)
        assert state["using_custom"] is False
    finally:
        requests.put(f"{api_url}{API}", json={"dir": ""}, timeout=5)


def test_recording_dir_rejects_system_dir(page, base_url, api_url):
    try:
        _open_record_tab(page, base_url)
        inp = page.locator("input[data-testid='recording-dir-input'], [data-testid='recording-dir-input'] input").first
        inp.fill("/etc")
        page.locator("[data-testid='recording-dir-save']").click()
        page.wait_for_selector(".el-message--error", timeout=8000)

        # 后端配置未被污染
        state = _backend_state(api_url)
        assert state["using_custom"] is False
        assert state["custom_dir"] == ""
    finally:
        requests.put(f"{api_url}{API}", json={"dir": ""}, timeout=5)
