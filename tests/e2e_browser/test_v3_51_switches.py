"""v3.51.0 两个新开关的 E2E 测试。

覆盖:
  1. 设置页「启用项目时自动接管未绑定工位」开关 (adopt-unbound-switch)
     UI 点击 → 后端 SystemConfig 落盘 → 刷新回填。
  2. 工位组「统一播报」unified_ok_report:
     API 创建/更新往返 (plugin_data 持久化) + 设置页工位组互通 tab
     列表里「统一播报」tag 可见。

前提: backend(8001) + frontend(6001) 已启动 (否则 conftest 自动 skip)。
清理: finally 恢复开关初值 / 删除测试工位组, 不留副作用。
"""
from __future__ import annotations

_ACTIVATE_CFG_API = "/api/v1/projects/activate-config"
_GROUP_API = "/api/v1/channel-groups"
_ADOPT_SW = '[data-testid="adopt-unbound-switch"]'
_E2E_GROUP_NAME = "__e2e_统一播报组"


def _ui_on(locator) -> bool:
    """el-switch 打开时根元素带 is-checked class。"""
    return "is-checked" in (locator.get_attribute("class") or "")


def _delete_e2e_groups(api_helper):
    r = api_helper.get(_GROUP_API)
    if r.status_code != 200:
        return
    body = r.json()
    items = body.get("items") if isinstance(body, dict) else body
    for g in items or []:
        if (g.get("name") or "").startswith("__e2e_"):
            api_helper.delete(f"{_GROUP_API}/{g['id']}")


# ---------------------------------------------------------------------------
# 1) 激活收养开关
# ---------------------------------------------------------------------------

def test_adopt_unbound_api_default_true(api_helper):
    """端点可达且返回布尔 adopt_unbound (默认 true = 存量行为)。"""
    r = api_helper.get(_ACTIVATE_CFG_API)
    assert r.status_code == 200, f"端点不可达: {r.status_code}"
    body = r.json()
    assert isinstance(body.get("adopt_unbound"), bool), f"非布尔: {body}"


def test_adopt_unbound_toggle_persists(page, base_url, api_helper):
    """开关点一下 → 后端取反落盘 → 刷新后 UI 回填新状态。"""
    init = api_helper.get(_ACTIVATE_CFG_API).json()
    init_enabled = bool(init.get("adopt_unbound", True))
    try:
        page.goto(f"{base_url}/#/settings", wait_until="domcontentloaded")
        sw = page.locator(_ADOPT_SW)
        sw.wait_for(state="visible", timeout=10000)
        sw.scroll_into_view_if_needed()

        assert _ui_on(sw) == init_enabled, \
            f"UI 初始状态({_ui_on(sw)})与后端({init_enabled})不一致"

        sw.click()
        page.wait_for_timeout(800)

        after = api_helper.get(_ACTIVATE_CFG_API).json()
        assert after.get("adopt_unbound") == (not init_enabled), \
            f"切换后后端未取反: {after}"

        page.reload(wait_until="domcontentloaded")
        sw2 = page.locator(_ADOPT_SW)
        sw2.wait_for(state="visible", timeout=10000)
        sw2.scroll_into_view_if_needed()
        assert _ui_on(sw2) == (not init_enabled), \
            "刷新后 UI 未回填切换后的状态"
    finally:
        api_helper.put(_ACTIVATE_CFG_API, json={"adopt_unbound": init_enabled})


# ---------------------------------------------------------------------------
# 2) 工位组统一播报
# ---------------------------------------------------------------------------

def test_unified_ok_report_api_roundtrip(api_helper):
    """POST 带 unified_ok_report → 回读 true → PUT 关掉 → 回读 false。"""
    _delete_e2e_groups(api_helper)
    gid = None
    try:
        r = api_helper.post(_GROUP_API, json={
            "name": _E2E_GROUP_NAME,
            "member_channel_ids": [0, 1],
            "settle_strategy": "synchronized_all_ok",
            "timeout_ms": 5000,
            "timeout_action": "fallback_independent",
            "enabled": False,
            "unified_ok_report": True,
        })
        assert r.status_code == 201, f"创建失败: {r.status_code} {r.text}"
        gid = r.json()["id"]
        assert r.json().get("unified_ok_report") is True

        got = api_helper.get(_GROUP_API).json()
        mine = [g for g in got.get("items", []) if g["id"] == gid][0]
        assert mine["unified_ok_report"] is True, f"回读丢失: {mine}"

        r2 = api_helper.put(f"{_GROUP_API}/{gid}",
                            json={"unified_ok_report": False})
        assert r2.status_code == 200, f"更新失败: {r2.status_code} {r2.text}"
        assert r2.json().get("unified_ok_report") is False
    finally:
        if gid is not None:
            api_helper.delete(f"{_GROUP_API}/{gid}")


def test_unified_ok_report_tag_visible_in_panel(page, base_url, api_helper):
    """API 建带开关的组 → 设置页工位组互通 tab 列表出现「统一播报」tag。"""
    _delete_e2e_groups(api_helper)
    gid = None
    try:
        r = api_helper.post(_GROUP_API, json={
            "name": _E2E_GROUP_NAME,
            "member_channel_ids": [0, 1],
            "settle_strategy": "synchronized_all_ok",
            "enabled": False,
            "unified_ok_report": True,
        })
        assert r.status_code == 201, f"创建失败: {r.status_code} {r.text}"
        gid = r.json()["id"]

        # 工位组互通已从系统设置迁到「工位与输入源」页
        page.goto(f"{base_url}/#/source", wait_until="domcontentloaded")
        page.get_by_role("tab", name="工位组互通").click()
        row = page.locator(".el-table__row", has_text=_E2E_GROUP_NAME)
        row.wait_for(state="visible", timeout=10000)
        tag = row.locator(".el-tag", has_text="统一播报")
        tag.wait_for(state="visible", timeout=5000)
        assert "el-tag--warning" in (tag.get_attribute("class") or ""), \
            "「统一播报」tag 不是警告色"
    finally:
        if gid is not None:
            api_helper.delete(f"{_GROUP_API}/{gid}")
