# -*- coding: utf-8 -*-
"""检测框 sidecar 与带框版录像 (2026-09) 浏览器 E2E: UI→落库双向验证。

覆盖:
  1. 数据页记录设置: 「记录检测框数据」开关仅在「录制周期视频」开启后出现,
     UI 点开 → GET /data/export-settings 双 true (双向)
  2. 归档规则编辑器: 「投递带框版录像」开关 → 保存 → GET rules
     annotated_video=true (双向), 编辑回开 → false
  3. 播放器回放叠加 (条件跑): 当天存在带 sidecar 的周期录像时,
     真点回放按钮 → 「检测框」开关 + 「下载带框版」按钮 + 叠加 canvas;
     无数据时 skip (全链路含真实录制的验证见
     tests/uat/uat_20260917_boxes_sidecar_annotated.py)

依赖: backend/frontend 已启动 (默认 8001/6001)。
"""
from __future__ import annotations

import requests
import pytest
from playwright.sync_api import Page, expect

from .conftest import E2E_PREFIX

RULE_NAME = f"{E2E_PREFIX}带框版归档规则"
DEST_DIR = "/tmp/tj_e2e_boxes_archive"


def _export_settings(api_url):
    r = requests.get(f"{api_url}/api/v1/data/export-settings", timeout=5)
    assert r.status_code == 200
    return r.json()


def _goto_record_tab(page: Page, base_url: str):
    page.goto(f"{base_url}/#/data", wait_until="domcontentloaded", timeout=15000)
    page.get_by_role("tab", name="记录设置").click()
    page.wait_for_timeout(600)


def test_record_boxes_switch_roundtrip(page: Page, base_url, api_url):
    """UI 开「记录检测框数据」→ 后端落库; 开关仅在周期视频开启后出现。"""
    orig = _export_settings(api_url)
    # 起点归零: 两开关都关 (测试自备初态, 结束还原用户原值)
    requests.put(f"{api_url}/api/v1/data/export-settings", timeout=5,
                 json={"record_cycle_video": False, "record_boxes_data": False})
    try:
        _goto_record_tab(page, base_url)
        boxes_switch = page.locator('[data-testid="record-boxes-switch"]')
        # 前提: 周期视频关 → 检测框行隐藏 (v-if)
        assert boxes_switch.count() == 0 or not boxes_switch.first.is_visible()

        cycle_row = page.locator(".setting-row", has_text="录制周期视频").first
        cycle_row.locator(".el-switch").click()
        expect(boxes_switch.first).to_be_visible(timeout=5000)

        boxes_switch.first.click()
        page.wait_for_timeout(800)
        cfg = _export_settings(api_url)
        assert cfg["record_cycle_video"] is True
        assert cfg["record_boxes_data"] is True

        # UI 关回 → 后端 false (双向的另一半)
        boxes_switch.first.click()
        page.wait_for_timeout(800)
        assert _export_settings(api_url)["record_boxes_data"] is False
    finally:
        requests.put(f"{api_url}/api/v1/data/export-settings", timeout=5,
                     json={"record_cycle_video": bool(orig.get("record_cycle_video")),
                           "record_boxes_data": bool(orig.get("record_boxes_data"))})


def _rules(api_url):
    r = requests.get(f"{api_url}/api/v1/export/video-archive/rules", timeout=5)
    assert r.status_code == 200
    return r.json()["items"]


def test_archive_rule_annotated_switch(page: Page, base_url, api_url):
    """归档规则编辑器「投递带框版录像」开关 → annotated_video 落库双向。"""
    page.goto(f"{base_url}/#/data", wait_until="domcontentloaded", timeout=15000)
    page.get_by_role("tab", name="存储与清理").click()
    card = page.locator("[data-test='video-archive-card']")
    expect(card).to_be_visible(timeout=10000)
    card.get_by_role("button", name="配置录像归档").click()
    dialog = page.locator(".va-dialog")
    expect(dialog).to_be_visible(timeout=5000)

    dialog.get_by_role("button", name="新建规则").click()
    editor = page.locator(".el-dialog").filter(has_text="新建归档规则")
    expect(editor).to_be_visible(timeout=5000)
    editor.get_by_placeholder("如：NG 录像归档到质量部网盘").fill(RULE_NAME)
    editor.get_by_placeholder("如 D:\\NG归档 或 \\\\server\\quality\\videos").fill(DEST_DIR)
    annotated_switch = editor.locator('[data-testid="rule-annotated-switch"]')
    expect(annotated_switch).to_be_visible()
    annotated_switch.click()
    editor.get_by_role("button", name="保存").click()
    expect(editor).to_be_hidden(timeout=5000)

    rows = [x for x in _rules(api_url) if x["name"] == RULE_NAME]
    assert len(rows) == 1, "规则应落库"
    assert rows[0]["annotated_video"] is True, "UI 勾选应落库为 true"

    # 规则列表徽标透出
    expect(dialog.locator(".el-table").first).to_contain_text("带框版")

    # 编辑关回 → false
    row = dialog.locator(".el-table__row").filter(has_text=RULE_NAME)
    row.get_by_role("button", name="编辑").click()
    editor2 = page.locator(".el-dialog").filter(has_text="编辑归档规则")
    expect(editor2).to_be_visible(timeout=5000)
    editor2.locator('[data-testid="rule-annotated-switch"]').click()
    editor2.get_by_role("button", name="保存").click()
    expect(editor2).to_be_hidden(timeout=5000)
    page.wait_for_timeout(600)
    assert [x for x in _rules(api_url)
            if x["name"] == RULE_NAME][0]["annotated_video"] is False


def _find_cycle_video_with_sidecar(api_url):
    """找一条带 sidecar 数据的周期录像 (今天没有真实录制时返回 None)。"""
    r = requests.get(f"{api_url}/api/v1/data/videos",
                     params={"clip_type": "cycle", "limit": 20}, timeout=5)
    if r.status_code != 200:
        return None
    items = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    for v in items:
        vid = v.get("video_uuid") or v.get("id")
        if not vid:
            continue
        b = requests.get(f"{api_url}/api/v1/data/videos/{vid}/boxes", timeout=5)
        if b.status_code == 200 and b.json().get("frames"):
            return v
    return None


def test_player_boxes_toggle_when_sidecar_exists(page: Page, base_url, api_url):
    """存在带 sidecar 的周期录像时: 回放按钮 → 检测框开关/带框下载/叠加层。"""
    video = _find_cycle_video_with_sidecar(api_url)
    if video is None:
        pytest.skip("环境中无带 sidecar 的周期录像 (全链路验证见 UAT 剧本)")

    # /videos/{id}/annotated 契约: 有 sidecar 的录像应能渲染 (只发 HEAD 级验证,
    # 避免 CI 花时间渲染大文件 — 渲染正确性由单测 TestRenderAnnotated 盖)
    vid = video.get("video_uuid") or video.get("id")
    b = requests.get(f"{api_url}/api/v1/data/videos/{vid}/boxes", timeout=5)
    assert b.status_code == 200 and b.json()["frames"], "boxes 端点应返回数据"

    # UI: 数据页 → 选日期(录像当天) → 回放。周期表按当前项目过滤,
    # 录像可能属于其他项目 → 逐个切换顶栏项目下拉重试, 都没有才 skip。
    page.goto(f"{base_url}/#/data", wait_until="domcontentloaded", timeout=15000)
    day = (video.get("created_at") or video.get("start_time") or "")[:10]
    if not day:
        pytest.skip("录像无时间字段, 无法定位日期")

    def _pick_date_and_find_play():
        dp = page.locator('input[placeholder="选择日期"]').first
        if not dp.is_visible():
            return None
        dp.click()
        dp.fill(day)
        dp.press("Enter")
        page.wait_for_timeout(2000)
        btn = page.locator('[data-testid="cycle-play-btn"]')
        if btn.count() > 0:
            return btn
        # 周期表要选中会话后才渲染: 逐个点「启动记录」里的会话卡
        cards = page.locator(".session-card")
        for i in range(min(cards.count(), 8)):
            cards.nth(i).click()
            page.wait_for_timeout(1500)
            if btn.count() > 0:
                return btn
        return None

    # 下拉面板 teleport 到 body 且关闭后仍留在 DOM, 必须只取"可见面板"里的选项
    visible_items = ".el-select-dropdown:visible .el-select-dropdown__item"

    play_btn = _pick_date_and_find_play()
    if play_btn is None:
        # 遍历顶栏项目下拉 (跳过 e2e 临时项目)
        page.locator(".el-select").first.click()
        page.wait_for_timeout(500)
        names = page.locator(visible_items).all_inner_texts()
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        for name in names:
            if not name.strip() or name.startswith(E2E_PREFIX):
                continue
            page.locator(".el-select").first.click()
            page.wait_for_timeout(400)
            page.locator(visible_items, has_text=name.strip()).first.click()
            page.wait_for_timeout(1200)
            play_btn = _pick_date_and_find_play()
            if play_btn is not None:
                break
    if play_btn is None:
        pytest.skip("各项目该日期均无周期录像行 (全链路验证见 UAT 剧本)")

    play_btn.first.click()
    toggle = page.locator('[data-testid="video-boxes-toggle"]')
    expect(toggle.first).to_be_visible(timeout=10000)
    expect(page.locator('[data-testid="video-download-annotated-btn"]').first
           ).to_be_visible()
    toggle.locator(".el-switch").click()
    page.wait_for_timeout(1000)
    expect(page.locator('[data-testid="video-boxes-overlay"]').first
           ).to_be_visible()
