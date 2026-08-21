# -*- coding: utf-8 -*-
"""检测主页自定义布局 (v3.54) e2e: 编辑器进出 / 拖拽保存 / 落库生效 / 恢复默认。

覆盖:
  1. ?layout_edit=1 进入编辑模式 → 工具条 + slot 编辑框出现
  2. 拖拽 video 区块 → 保存 → 后端 GET 落库一致 (双向验证)
  3. 普通模式刷新 → 自定义位置已应用 (data-layout-applied + 坐标)
  4. 取消编辑 → 不落库
  5. 升级 reconcile: 布局含未知 slot / 缺已知 slot → 页面不崩, 已知位置照用
  6. 显示设置入口卡: 已定制形态标签 + 全部恢复默认清库
  7. 后端 API 校验护栏 (坏 form_key / 坏 slot 拒收)
"""
from __future__ import annotations

import requests
from playwright.sync_api import expect

API = "/api/v1/system/monitor-layouts"


def _clear_layouts(api_url: str):
    requests.delete(f"{api_url}{API}", timeout=5)


def _backend_layouts(api_url: str) -> dict:
    r = requests.get(f"{api_url}{API}", timeout=5)
    r.raise_for_status()
    return r.json()["layouts"]


def _open_editor(page, base_url):
    page.goto(f"{base_url}/#/monitor?layout_edit=1",
              wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("[data-testid='layout-editor']", timeout=15000)
    # 等初稿补测双帧完成 (编辑框位置稳定后再交互)
    page.wait_for_selector("[data-testid='layout-box-video']", timeout=8000)
    page.wait_for_timeout(400)


def _slot_pos(page, form_family: str, slot: str) -> dict:
    """读 slot 元素相对画布的百分比位置 (真实 DOM, 不是编辑框)"""
    return page.evaluate(
        """([family, slot]) => {
             const canvas = document.querySelector(`[data-layout-canvas='${family}']`);
             const el = canvas && canvas.querySelector(`[data-layout-slot='${slot}']`);
             if (!el) return null;
             const c = canvas.getBoundingClientRect();
             const r = el.getBoundingClientRect();
             return {
               x: (r.left - c.left) / c.width,
               y: (r.top - c.top) / c.height,
               applied: el.dataset.layoutApplied === '1',
             };
           }""",
        [form_family, slot],
    )


def test_enter_edit_mode_shows_editor(page, base_url, api_url):
    _clear_layouts(api_url)
    try:
        _open_editor(page, base_url)
        expect(page.locator("[data-testid='layout-toolbar']")).to_be_visible()
        # 单工位形态至少有 video / sop-row / step-table 编辑框
        assert page.locator("[data-testid='layout-box-video']").count() == 1
        assert page.locator("[data-testid^='layout-box-']").count() >= 3
        # 完成退出 → 编辑器消失, query 参数被清
        page.locator("[data-testid='layout-done']").click()
        expect(page.locator("[data-testid='layout-editor']")).to_have_count(0)
    finally:
        _clear_layouts(api_url)


def test_drag_save_roundtrip_and_apply(page, base_url, api_url):
    _clear_layouts(api_url)
    try:
        _open_editor(page, base_url)
        box = page.locator("[data-testid='layout-box-video']")
        bb = box.bounding_box()
        assert bb, "video 编辑框不可见"

        # 拖拽 video: 往右下移动约 1/12 画布 (吸附到 24 格)
        page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
        page.mouse.down()
        page.mouse.move(bb["x"] + bb["width"] / 2 + 120,
                        bb["y"] + bb["height"] / 2 + 60, steps=8)
        page.mouse.up()
        page.wait_for_timeout(300)

        # 撤销按钮激活 = 改动已入撤销栈
        expect(page.locator("[data-testid='layout-undo']")).to_be_enabled()

        page.locator("[data-testid='layout-save']").click()
        page.wait_for_selector(".el-message--success", timeout=8000)

        # 双向验证: 后端落库, video 坐标离开原点
        layouts = _backend_layouts(api_url)
        assert "single:default" in layouts
        video = layouts["single:default"]["slots"]["video"]
        assert video["x"] > 0.02 or video["y"] > 0.02

        # 普通模式刷新 → 布局已应用且坐标一致 (容差 2%)
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
        page.wait_for_selector("[data-layout-canvas='single']", timeout=15000)
        page.wait_for_timeout(1500)
        pos = _slot_pos(page, "single", "video")
        assert pos and pos["applied"], f"自定义布局未应用: {pos}"
        assert abs(pos["x"] - video["x"]) < 0.02
        assert abs(pos["y"] - video["y"]) < 0.02
    finally:
        _clear_layouts(api_url)


def test_cancel_does_not_persist(page, base_url, api_url):
    _clear_layouts(api_url)
    try:
        _open_editor(page, base_url)
        box = page.locator("[data-testid='layout-box-video']")
        bb = box.bounding_box()
        page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
        page.mouse.down()
        page.mouse.move(bb["x"] + bb["width"] / 2 + 100,
                        bb["y"] + bb["height"] / 2 + 50, steps=5)
        page.mouse.up()
        page.wait_for_timeout(300)

        page.locator("[data-testid='layout-cancel']").click()
        page.wait_for_timeout(500)
        expect(page.locator("[data-testid='layout-editor']")).to_have_count(0)
        assert _backend_layouts(api_url) == {}
    finally:
        _clear_layouts(api_url)


def test_upgrade_reconcile_unknown_and_missing_slots(page, base_url, api_url):
    """升级模拟: 存量布局含已删区块(unknown) + 缺新区块(missing) → 不崩不丢。"""
    _clear_layouts(api_url)
    try:
        # 直接 PUT 一份"旧版本"布局: video 有自定义位, 带一个未来不存在的 slot,
        # 且故意不含 sop-row / step-table 等其余已知 slot
        r = requests.put(f"{api_url}{API}/single:default", json={
            "version": 1, "snap": True,
            "slots": {
                "video": {"x": 0.25, "y": 0.2, "w": 0.5, "h": 0.5},
                "ghost_removed_block": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2},
            },
        }, timeout=5)
        assert r.status_code == 200

        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
        page.wait_for_selector("[data-layout-canvas='single']", timeout=15000)
        page.wait_for_timeout(1500)

        # video 用自定义位置
        pos = _slot_pos(page, "single", "video")
        assert pos and pos["applied"]
        assert abs(pos["x"] - 0.25) < 0.02 and abs(pos["y"] - 0.2) < 0.02

        # 布局里没有的已知区块 (step-table) 也被接管且可见 (兜底位, 不消失)
        pos2 = _slot_pos(page, "single", "step-table")
        assert pos2 and pos2["applied"]

        # 页面主体没崩: 视频区 + 控制按钮还在
        expect(page.get_by_role("button", name="开始")).to_be_visible()
    finally:
        _clear_layouts(api_url)


def test_settings_entry_card_and_restore_all(page, base_url, api_url):
    _clear_layouts(api_url)
    try:
        # 预置一份自定义布局
        requests.put(f"{api_url}{API}/single:default", json={
            "version": 1, "snap": True,
            "slots": {"video": {"x": 0.2, "y": 0.2, "w": 0.5, "h": 0.5}},
        }, timeout=5)

        page.goto(f"{base_url}/#/settings", wait_until="domcontentloaded")
        card = page.locator("[data-testid='layout-entry-card']")
        card.wait_for(state="visible", timeout=15000)
        expect(page.locator("[data-testid='layout-edit-entry']")).to_be_visible()

        # 已定制形态标签出现
        forms = page.locator("[data-testid='layout-custom-forms']")
        expect(forms).to_be_visible()
        expect(forms).to_contain_text("单工位")

        # 全部恢复默认 → 确认弹窗 → 后端清空
        page.locator("[data-testid='layout-restore-all']").click()
        page.locator(".el-message-box").get_by_role(
            "button", name="全部恢复默认").click()
        page.wait_for_timeout(800)
        assert _backend_layouts(api_url) == {}
    finally:
        _clear_layouts(api_url)


def test_backend_validation_guards(api_url):
    _clear_layouts(api_url)
    try:
        # 坏 form_key
        r = requests.put(f"{api_url}{API}/BAD KEY!", json={
            "version": 1, "slots": {"video": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}},
        }, timeout=5)
        assert r.status_code in (400, 404)

        # 缺坐标字段
        r = requests.put(f"{api_url}{API}/single:default", json={
            "version": 1, "slots": {"video": {"x": 0, "y": 0}},
        }, timeout=5)
        assert r.status_code == 400

        # 坐标越界被钳制而不是拒收 (w=5 → 1.0)
        r = requests.put(f"{api_url}{API}/single:default", json={
            "version": 1, "slots": {"video": {"x": -1, "y": 2, "w": 5, "h": 0.5}},
        }, timeout=5)
        assert r.status_code == 200
        video = _backend_layouts(api_url)["single:default"]["slots"]["video"]
        assert video["x"] == 0.0 and video["y"] == 1.0 and video["w"] == 1.0
    finally:
        _clear_layouts(api_url)
