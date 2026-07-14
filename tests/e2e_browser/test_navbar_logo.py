"""导航栏右上角 Logo 回归 (v3.37.0)。

v3.36.0 把右上角 Logo 从静态地址改成运行时表达式 (自定义优先/内置回退) 后,
打包版 file:// + 相对 base 下写死的 '/app-icon.png' 会解析到盘根, 内置图标空白。
v3.37.0 改为 BASE_URL 拼接。本测试锁两件事:
  1. 未上传自定义 Logo 时内置图标真实加载成功 (naturalWidth > 0, 不是 404 空图)
  2. localStorage 里有自定义 Logo (data URL) 时优先生效
"""
from __future__ import annotations


def test_默认内置logo真实加载(page_with_app):
    page = page_with_app
    logo = page.locator("header img[alt='logo']").last
    logo.wait_for(state="visible", timeout=10000)
    natural_width = logo.evaluate("el => el.naturalWidth")
    assert natural_width > 0, (
        f"内置回退 logo 加载失败 (naturalWidth={natural_width}), "
        f"src={logo.get_attribute('src')} — 检查回退地址是否又被写死成 '/app-icon.png'"
    )


def test_自定义logo优先于内置回退(page, base_url):
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(1500)
    # 注入 1x1 红色 data URL 当自定义 logo (与设置页上传后的持久化格式一致)
    page.evaluate("""() => {
        const c = document.createElement('canvas');
        c.width = c.height = 8;
        const g = c.getContext('2d');
        g.fillStyle = '#f00'; g.fillRect(0, 0, 8, 8);
        const saved = localStorage.getItem('display_settings');
        const d = saved ? JSON.parse(saved) : {};
        d.logoDataUrl = c.toDataURL('image/png');
        localStorage.setItem('display_settings', JSON.stringify(d));
    }""")
    page.reload(wait_until="domcontentloaded")
    try:
        logo = page.locator("header img[alt='logo']").last
        logo.wait_for(state="visible", timeout=10000)
        src = logo.get_attribute("src") or ""
        assert src.startswith("data:image/png"), f"应优先显示自定义 logo, 实际 src={src[:50]}"
    finally:
        # 还原, 不污染同会话其他用例
        page.evaluate("""() => {
            const saved = localStorage.getItem('display_settings');
            if (saved) {
                const d = JSON.parse(saved);
                d.logoDataUrl = '';
                localStorage.setItem('display_settings', JSON.stringify(d));
            }
        }""")
