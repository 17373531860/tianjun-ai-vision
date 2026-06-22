"""开机自动恢复检测开关 E2E 测试。

验证链路: 设置页「显示设置」tab 的开关 → 点击切换 → 后端落盘 → 刷新回填。
前提: backend(8001) + frontend(6001) 已启动 (否则 conftest 自动 skip)。

清理: 测试结束在 finally 里把开关恢复到测试开始前的状态, 不留副作用。
"""
from __future__ import annotations


_API = "/api/v1/workstations/auto-resume"
_SW = '[data-testid="auto-resume-switch"]'


def _ui_on(locator) -> bool:
    """el-switch 打开时根元素带 is-checked class。"""
    return "is-checked" in (locator.get_attribute("class") or "")


def test_auto_resume_toggle_persists(page, base_url, api_helper):
    """开关点一下 → 后端取反落盘 → 刷新后 UI 回填新状态。"""
    init = api_helper.get(_API).json()
    init_enabled = bool(init.get("enabled", True))
    try:
        page.goto(f"{base_url}/#/settings", wait_until="domcontentloaded")
        sw = page.locator(_SW)
        sw.wait_for(state="visible", timeout=10000)
        sw.scroll_into_view_if_needed()

        # 初始 UI 应与后端一致
        assert _ui_on(sw) == init_enabled, \
            f"UI 初始状态({_ui_on(sw)})与后端({init_enabled})不一致"

        # 点一下切换
        sw.click()
        page.wait_for_timeout(800)

        after = api_helper.get(_API).json()
        assert after.get("enabled") == (not init_enabled), \
            f"切换后后端未取反: {after}"

        # 刷新页面, 开关应回填为新状态
        page.reload(wait_until="domcontentloaded")
        sw2 = page.locator(_SW)
        sw2.wait_for(state="visible", timeout=10000)
        sw2.scroll_into_view_if_needed()
        assert _ui_on(sw2) == (not init_enabled), \
            "刷新后 UI 未回填切换后的状态"
    finally:
        # 恢复测试前状态
        api_helper.put(_API, json={"enabled": init_enabled})


def test_auto_resume_default_enabled_via_api(api_helper):
    """端点可达且返回布尔 enabled (默认应为 true)。"""
    r = api_helper.get(_API)
    assert r.status_code == 200, f"端点不可达: {r.status_code}"
    body = r.json()
    assert isinstance(body.get("enabled"), bool), f"enabled 非布尔: {body}"
