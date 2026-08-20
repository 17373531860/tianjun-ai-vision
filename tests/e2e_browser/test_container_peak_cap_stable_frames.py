# -*- coding: utf-8 -*-
"""E2E — v3.44.0e 容器装箱清点两个新配置项 (真浏览器点击).

背景 (上银 7-28 现场): 模型偶发重复框把一盘 24 数成 25 后峰值卡住降不回来 —
止血配置「每盘峰值封顶」此前只有后端键没有 UI 入口, 现场没法设;
「动作前稳定计数」快照记账同理。本测试锁定两个入口:
  1. 装箱清点 Tab（信息架构重构后自逻辑设置外置）内渲染「每盘峰值封顶」「动作前稳定计数(帧)」
  2. UI 改值 → 保存 → GET 项目断言 pipeline_config 真落库
  3. 改回原值 → 保存 → 不污染 SY6 生产配置

前置: 后端 8001 + 前端 6001 已起; 依赖项目 SY6 (id=37, 容器混合模式) 存在 —
没有则跳过, 不动用户其他项目。
"""
import re

import pytest
import requests

PROJECT_ID = 37  # SY6


def _get_pc(api_url):
    r = requests.get(f"{api_url}/api/v1/projects/{PROJECT_ID}", timeout=10)
    if r.status_code != 200:
        return None
    return r.json().get("pipeline_config") or {}


def _row_input(page, label_text):
    """按行首文案定位该行的 el-input-number 输入框."""
    row = page.locator("div.flex").filter(
        has_text=re.compile(label_text)).last
    row.scroll_into_view_if_needed()
    return row.locator(".el-input-number input").first


def _set_number(page, label_text, value):
    inp = _row_input(page, label_text)
    inp.click()
    page.keyboard.press("Control+a")
    inp.fill(str(value))
    page.keyboard.press("Tab")  # 触发 el-input-number change
    page.wait_for_timeout(300)


def _save(page):
    page.get_by_role("button", name=re.compile("^保存")).first.click()
    page.wait_for_timeout(1500)


def test_peak_cap_and_stable_frames_roundtrip(page, base_url, api_url):
    pc = _get_pc(api_url)
    if pc is None:
        pytest.skip("SY6 (id=37) 不存在")
    if not pc.get("custom_mix_container_label"):
        pytest.skip("SY6 未启用托盘容器 (前置条件变了)")
    if not pc.get("custom_mix_container_confirm_by_action"):
        pytest.skip("SY6 未开动作确认 (稳定计数入口依赖它显示)")

    orig_cap = int(pc.get("custom_mix_container_peak_cap") or 0)
    orig_stable = int(pc.get("custom_mix_container_stable_min_frames") or 0)

    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_load_state("networkidle")
    page.get_by_text("SY6", exact=True).first.click()
    page.wait_for_timeout(1000)
    page.get_by_role("tab", name="装箱清点").click()
    page.wait_for_timeout(800)

    # 1. 两个入口都渲染, 且回填原值
    cap_inp = _row_input(page, "每盘峰值封顶")
    assert cap_inp.count() > 0, "「每盘峰值封顶」入口未渲染"
    assert cap_inp.input_value() == str(orig_cap), \
        f"峰值封顶应回填 {orig_cap}: {cap_inp.input_value()!r}"
    stable_inp = _row_input(page, "动作前稳定计数")
    assert stable_inp.count() > 0, "「动作前稳定计数(帧)」入口未渲染"
    assert stable_inp.input_value() == str(orig_stable), \
        f"稳定计数应回填 {orig_stable}: {stable_inp.input_value()!r}"
    page.screenshot(path="/tmp/uat_shots/e2e_peak_cap_stable_ui.png", full_page=True)

    # 2. 改成哨兵值 → 保存 → 落库
    try:
        _set_number(page, "每盘峰值封顶", orig_cap + 3)
        _set_number(page, "动作前稳定计数", orig_stable + 2)
        _save(page)
        got = _get_pc(api_url)
        assert got.get("custom_mix_container_peak_cap") == orig_cap + 3, \
            f"峰值封顶未落库: {got.get('custom_mix_container_peak_cap')}"
        assert got.get("custom_mix_container_stable_min_frames") == orig_stable + 2, \
            f"稳定计数未落库: {got.get('custom_mix_container_stable_min_frames')}"
    finally:
        # 3. 改回原值 (UI 路径失败时兜底走 API, 保证不污染生产配置)
        try:
            _set_number(page, "每盘峰值封顶", orig_cap)
            _set_number(page, "动作前稳定计数", orig_stable)
            _save(page)
        except Exception:
            pass
        got = _get_pc(api_url)
        if (got.get("custom_mix_container_peak_cap") != orig_cap
                or got.get("custom_mix_container_stable_min_frames") != orig_stable):
            proj = requests.get(f"{api_url}/api/v1/projects/{PROJECT_ID}",
                                timeout=10).json()
            proj_pc = proj.get("pipeline_config") or {}
            proj_pc["custom_mix_container_peak_cap"] = orig_cap
            proj_pc["custom_mix_container_stable_min_frames"] = orig_stable
            requests.put(f"{api_url}/api/v1/projects/{PROJECT_ID}",
                         json={"pipeline_config": proj_pc}, timeout=10)

    got = _get_pc(api_url)
    assert got.get("custom_mix_container_peak_cap") == orig_cap
    assert got.get("custom_mix_container_stable_min_frames") == orig_stable
