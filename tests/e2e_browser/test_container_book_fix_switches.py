# -*- coding: utf-8 -*-
"""E2E — v3.46 容器装箱清点「记账修正」五个可选开关 (真浏览器点击).

背景 (2026-07-30 上银现场"主盘指针"诊断): 滑块/托盘重复框、空账幽灵占指针、
指针卡残数、三数两盘混排 — 五项修正全部做成项目级可选开关, 默认值 = v3.45
线上行为 (滑块去重开、其余关), 现场关掉即回退。本测试锁定:
  1. 装箱清点 Tab（信息架构重构后自逻辑设置外置）内渲染五个开关, 且回填默认值
  2. UI 翻转 → 保存 → GET 项目断言 pipeline_config 真落库
  3. 改回原值 → 保存 → 不污染 SY8 生产配置

前置: 后端/前端已起 (端口走 E2E_API_URL / E2E_BASE_URL); 依赖项目 SY8
(id=41, 容器混合模式 + 动作确认) 存在 — 没有则跳过。
"""
import re

import pytest
import requests

PROJECT_ID = 41  # SY8

SWITCHES = {  # 行首文案 → (pipeline_config 键, 出厂默认)
    "物品重复框去重": ("custom_mix_container_dedup_items", True),
    "容器重复框去重": ("custom_mix_container_dedup_trays", False),
    "空账容器清理": ("custom_mix_container_purge_empty_primary", False),
    "记账容器让位": ("custom_mix_container_yield_primary", False),
    "显示与记账同源": ("custom_mix_container_unified_book_source", False),
}


def _get_pc(api_url):
    r = requests.get(f"{api_url}/api/v1/projects/{PROJECT_ID}", timeout=10)
    if r.status_code != 200:
        return None
    return r.json().get("pipeline_config") or {}


def _row_switch(page, label_text):
    """按行文案定位该行的 el-switch."""
    row = page.locator("div.flex").filter(
        has_text=re.compile(label_text)).last
    row.scroll_into_view_if_needed()
    return row.locator(".el-switch").first


def _switch_on(sw) -> bool:
    return "is-checked" in (sw.get_attribute("class") or "")


def _set_switch(page, label_text, want_on: bool):
    sw = _row_switch(page, label_text)
    if _switch_on(sw) != want_on:
        sw.click()
        page.wait_for_timeout(200)


def _save(page):
    page.get_by_role("button", name=re.compile("^保存")).first.click()
    page.wait_for_timeout(1500)


def _put_pc(api_url, patch: dict):
    proj = requests.get(f"{api_url}/api/v1/projects/{PROJECT_ID}",
                        timeout=10).json()
    pc = proj.get("pipeline_config") or {}
    pc.update(patch)
    requests.put(f"{api_url}/api/v1/projects/{PROJECT_ID}",
                 json={"pipeline_config": pc}, timeout=10)


def test_book_fix_switches_roundtrip(page, base_url, api_url):
    pc = _get_pc(api_url)
    if pc is None:
        pytest.skip("SY8 (id=41) 不存在")
    if not pc.get("custom_mix_container_label"):
        pytest.skip("SY8 未启用托盘容器 (前置条件变了)")

    orig = {key: pc.get(key, default) if pc.get(key) is not None else default
            for _, (key, default) in SWITCHES.items()}

    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded",
              timeout=20000)
    page.wait_for_load_state("networkidle")
    page.get_by_text("SY8", exact=True).first.click()
    page.wait_for_timeout(1000)
    page.get_by_role("tab", name="装箱清点").click()
    page.wait_for_timeout(800)

    # 1. 五个开关都渲染, 且回填当前配置值
    for label, (key, default) in SWITCHES.items():
        sw = _row_switch(page, label)
        assert sw.count() > 0, f"「{label}」开关未渲染"
        expect_on = bool(pc.get(key) if pc.get(key) is not None else default)
        assert _switch_on(sw) == expect_on, \
            f"「{label}」应回填 {expect_on}: 实际 {_switch_on(sw)}"
    page.screenshot(path="/tmp/uat_shots/e2e_book_fix_switches_ui.png",
                    full_page=True)

    # 2. 全部翻转 → 保存 → 落库
    flipped = {key: not bool(orig[key]) for _, (key, _) in SWITCHES.items()}
    try:
        for label, (key, _) in SWITCHES.items():
            _set_switch(page, label, flipped[key])
        _save(page)
        got = _get_pc(api_url)
        for label, (key, _) in SWITCHES.items():
            assert bool(got.get(key)) == flipped[key], \
                f"「{label}」({key}) 未落库: {got.get(key)}"
    finally:
        # 3. 改回原值 (UI 路径失败时兜底走 API, 保证不污染生产配置)
        try:
            for label, (key, _) in SWITCHES.items():
                _set_switch(page, label, bool(orig[key]))
            _save(page)
        except Exception:
            pass
        got = _get_pc(api_url)
        if any(bool(got.get(k)) != bool(v) for k, v in orig.items()):
            _put_pc(api_url, orig)

    got = _get_pc(api_url)
    for key, val in orig.items():
        assert bool(got.get(key)) == bool(val), f"{key} 未复原"
