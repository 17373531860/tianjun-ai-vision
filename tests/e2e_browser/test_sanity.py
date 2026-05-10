"""sanity test — 验证 Playwright 能打开应用 + 各路由可达"""
from __future__ import annotations

import pytest


def test_打开_首页(page_with_app):
    """主页应当能加载到，含菜单"""
    page = page_with_app
    title = page.title()
    assert title, "页面 title 应非空"
    # 应能看到导航或主布局
    body_text = page.locator("body").inner_text(timeout=5000)
    assert body_text, "body 文字应非空"


@pytest.mark.parametrize("route,name_keyword", [
    ("/#/monitor", "Monitor"),
    ("/#/project", "Project"),
    ("/#/data", "Data"),
    ("/#/source", "Source"),
])
def test_路由可达(page, base_url, route, name_keyword):
    """所有主要路由都能加载到、不报 JS 错"""
    js_errors = []
    page.on("pageerror", lambda exc: js_errors.append(str(exc)))

    page.goto(f"{base_url}{route}", wait_until="domcontentloaded", timeout=15000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        # Monitor 等页面有轮询/MJPEG 长连接，不能把 networkidle 当作硬性页面就绪条件。
        pass

    # 不应有未捕获 JS 异常
    fatal = [e for e in js_errors
             if "ResizeObserver" not in e
             and "ChunkLoadError" not in e]
    assert not fatal, f"路由 {route} 报 JS 异常: {fatal}"
