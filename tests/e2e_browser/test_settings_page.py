"""Settings 页 E2E：验证 PT/CT 设置区与 POM 业务方法。"""
from __future__ import annotations

from .pages import SettingsPage


def test_settings_page_loaded(page, base_url):
    sp = SettingsPage(page, base_url).goto()
    titles = sp.get_visible_section_titles()
    assert titles, f"Settings 页应包含 PT/CT/品牌等字段, 实际无匹配, body={sp.body_text()[:200]}"


def test_settings_pt_aggregate_words(page, base_url):
    sp = SettingsPage(page, base_url).goto()
    assert sp.has_pt_aggregate_options(), \
        "Settings 页应有 PT 计算方式 + 合并 + 最后一次 三组关键词"


def test_settings_three_tabs_present(page, base_url):
    """v3.14.0 起 Settings 含 6 个原生 Tab + 可选插件注入 Tab.
    断言改成"包含"语义, 防加 Tab 后回归挂掉 (v3.10 加账号鉴权, v3.14 加流水线串行).
    """
    sp = SettingsPage(page, base_url).goto()
    tabs = sp.get_visible_tabs()
    expected_native = {"显示设置", "检测框设置", "性能设置", "插件管理", "账号鉴权", "流水线串行"}
    missing = expected_native - set(tabs)
    assert not missing, f"Settings 页缺少原生 tab: {missing}, 实际 {tabs}"


def test_settings_auth_panel_present(page, base_url):
    """v3.10 起操作员管理已废弃, 账号体系并入「账号鉴权」Tab.

    老断言盯"操作员管理"字样, 该区块 v3.10 删除后一直红 — 随特性同步更新:
    点开账号鉴权 Tab 应看到鉴权状态卡 (启用/未启用两态均可);
    用户表仅鉴权启用后渲染, CI 环境默认关, 不强求表格。
    """
    sp = SettingsPage(page, base_url).goto()
    sp.switch_tab("账号鉴权")
    ok = (sp.wait_for_text("账号鉴权已启用", timeout_ms=8000)
          or sp.wait_for_text("账号鉴权未启用", timeout_ms=2000))
    assert ok, "账号鉴权 Tab 应显示鉴权状态卡"


def test_settings_save_button_present(page, base_url):
    sp = SettingsPage(page, base_url).goto()
    assert sp.wait_for_text("保存此工位设置", timeout_ms=4000)


def test_settings_navbar_logo_upload_roundtrip(page, base_url, tmp_path):
    """2026-07 导航栏 Logo 可上传: 上传→立即生效→刷新不丢→恢复默认。

    纯前端链路 (data URL 存 display_settings localStorage), 每测试独立
    context, 不污染用户浏览器数据。
    """
    import time

    from PIL import Image

    logo = tmp_path / "e2e_logo.png"
    Image.new("RGB", (300, 300), "#7c3aed").save(logo)

    def navbar_src():
        return page.locator(
            "header img.rounded-full[alt='logo']").first.get_attribute("src")

    SettingsPage(page, base_url).goto()
    page.wait_for_selector("text=导航栏 Logo", timeout=10000)
    assert (navbar_src() or "").endswith("app-icon.png"), "初始应为内置图"

    card = page.locator("div.bg-slate-900:has-text('导航栏 Logo')").first
    card.scroll_into_view_if_needed()
    card.locator("input[type='file']").set_input_files(str(logo))
    time.sleep(1.0)
    src = navbar_src() or ""
    assert src.startswith("data:image/"), f"上传后应立即生效, 实际 {src[:30]}"

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("text=导航栏 Logo", timeout=10000)
    assert navbar_src() == src, "刷新后自定义 Logo 应保持"

    card = page.locator("div.bg-slate-900:has-text('导航栏 Logo')").first
    card.scroll_into_view_if_needed()
    card.locator("button:has-text('恢复默认')").click()
    time.sleep(0.6)
    assert (navbar_src() or "").endswith("app-icon.png"), "恢复默认应回退内置图"
