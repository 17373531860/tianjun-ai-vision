"""设置页「集中管控枢纽接入」开关 CI E2E (RFC 15 边缘侧入口)。

覆盖: 卡片渲染 → 开开关 → GET /hub/config 落库 true + /hub/handshake 守门
翻 200 → 关回 → 守门回 404。测试自恢复初始状态, 不污染环境。
"""
from __future__ import annotations

import requests


def _hub_enabled(api_url: str) -> bool:
    r = requests.get(f"{api_url}/api/v1/hub/config", timeout=5)
    r.raise_for_status()
    return bool(r.json().get("enabled"))


def _set_hub(api_url: str, enabled: bool):
    requests.put(f"{api_url}/api/v1/hub/config",
                 json={"enabled": enabled}, timeout=5)


def test_hub_access_switch_roundtrip(page, base_url, api_url):
    original = _hub_enabled(api_url)
    _set_hub(api_url, False)  # 从关闭态开测
    try:
        page.goto(f"{base_url}/#/settings", wait_until="domcontentloaded",
                  timeout=15000)
        page.click("text=账号鉴权")
        page.wait_for_selector('[data-test="hub-access-card"]', timeout=10000)
        page.locator('[data-test="hub-access-card"]').scroll_into_view_if_needed()

        # 初始: 关 + 守门 404
        cls = page.get_attribute('[data-test="hub-access-switch"]', "class") or ""
        assert "is-checked" not in cls
        assert requests.get(f"{api_url}/api/v1/hub/handshake",
                            timeout=5).status_code == 404

        # 开 → 落库 + 守门 200
        page.click('[data-test="hub-access-switch"]')
        page.wait_for_timeout(1200)
        assert _hub_enabled(api_url) is True
        assert requests.get(f"{api_url}/api/v1/hub/handshake",
                            timeout=5).status_code == 200

        # 关回 → 守门 404
        page.click('[data-test="hub-access-switch"]')
        page.wait_for_timeout(1200)
        assert _hub_enabled(api_url) is False
        assert requests.get(f"{api_url}/api/v1/hub/handshake",
                            timeout=5).status_code == 404
    finally:
        _set_hub(api_url, original)