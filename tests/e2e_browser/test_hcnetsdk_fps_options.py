"""海康SDK直连目标帧率档位 E2E（2026-07-29 帧率上限放开到 60）。

背景：现场反馈海康 SDK 直连的帧率被前端 UI 锁死——
  - 单工位「目标帧率」下拉最高只有 30 FPS
  - 多工位面板根本没有帧率选择，启动请求写死 25 帧
治理后：
  - 单工位下拉补 50/60 档
  - 多工位海康SDK块新增「目标帧率」下拉（10~60）
  - 帧率随工位配置持久化（hcnet_fps），后端开机自动恢复不再写死 25
"""
from __future__ import annotations

import requests


def _visible_option_texts(page):
    """读取当前弹出的 el-select 下拉里所有选项文本"""
    page.wait_for_timeout(400)
    items = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
    return [items.nth(i).inner_text().strip() for i in range(items.count())]


def test_single_ws_hcnetsdk_fps_options_up_to_60(page, base_url):
    """单工位海康SDK直连：目标帧率下拉必须有 50/60 档"""
    page.goto(f"{base_url}/#/source", wait_until="domcontentloaded", timeout=15000)
    # Vite dev server 冷编译首次加载可能 >10s，显式等单选渲染出来
    page.wait_for_selector(".el-radio:has-text('海康SDK直连')", timeout=30000)

    page.locator(".el-radio:has-text('海康SDK直连')").first.click(timeout=8000)
    page.wait_for_timeout(500)

    card = page.locator(".el-card:has-text('海康SDK直连设置')").first
    fps_select = card.locator(".el-form-item:has-text('目标帧率') .el-select").first
    fps_select.click(timeout=5000)

    texts = _visible_option_texts(page)
    assert any("50" in t for t in texts), f"缺 50 FPS 档: {texts}"
    assert any("60" in t for t in texts), f"缺 60 FPS 档: {texts}"
    page.keyboard.press("Escape")


def test_multi_ws_hcnetsdk_has_fps_select(page, base_url, api_url):
    """多工位海康SDK块：必须出现「目标帧率」下拉，且含 60 档；结束恢复单工位"""
    page.add_init_script("localStorage.setItem('developer_mode', 'true')")
    page.goto(f"{base_url}/#/source", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector(".el-radio-button:has-text('双工位')", timeout=30000)

    try:
        page.locator(".el-radio-button:has-text('双工位')").first.click(timeout=8000)
        page.wait_for_timeout(1500)

        ws1 = page.locator(".el-card:has-text('工位 1')").first
        ws1.locator(".el-radio-button:has-text('海康SDK')").first.click(timeout=5000)
        page.wait_for_timeout(500)

        fps_item = ws1.locator(".el-form-item:has-text('目标帧率')")
        assert fps_item.count() > 0, "多工位海康SDK块缺「目标帧率」下拉"

        fps_item.first.locator(".el-select").first.click(timeout=5000)
        texts = _visible_option_texts(page)
        assert any("60" in t for t in texts), f"多工位帧率下拉缺 60 档: {texts}"
        page.keyboard.press("Escape")
    finally:
        # 恢复单工位，别把共享后端留在双工位状态
        requests.post(f"{api_url}/api/v1/workstations/mode",
                      json={"channel_count": 1}, timeout=10)


def test_channel_config_persists_hcnet_fps(api_url):
    """后端契约：hcnet_fps 随工位配置落盘，GET /workstations/ 能读回（保存并还原原配置）"""
    r = requests.get(f"{api_url}/api/v1/workstations/", timeout=10)
    r.raise_for_status()
    original = (r.json().get("source_configs") or {}).get("0")

    try:
        put = requests.put(f"{api_url}/api/v1/workstations/channel-config", json={
            "channel_id": 0,
            "source_type": "hcnetsdk",
            "hcnet_ip": "192.168.1.64",
            "hcnet_fps": 60,
        }, timeout=10)
        put.raise_for_status()

        r2 = requests.get(f"{api_url}/api/v1/workstations/", timeout=10)
        r2.raise_for_status()
        saved = (r2.json().get("source_configs") or {}).get("0") or {}
        assert saved.get("hcnet_fps") == 60, f"hcnet_fps 未持久化: {saved}"
    finally:
        restore = original if original else {"source_type": "camera"}
        restore = dict(restore)
        restore["channel_id"] = 0
        requests.put(f"{api_url}/api/v1/workstations/channel-config",
                     json=restore, timeout=10)
