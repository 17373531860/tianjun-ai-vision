"""监控页清零二选一弹窗 CI E2E。

覆盖: 点「清零」弹出「清理所有数据 / 仅清理本周期」；点后者走
POST /source/detection/reset-stats?scope=cycle 并成功返回。
"""
from __future__ import annotations

import time

import requests


def test_清零二选一弹窗_仅清理本周期_调接口(page, base_url, api_url):
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector("button:has-text('清零')", timeout=10000)
    page.locator("button:has-text('清零')").first.click()
    time.sleep(0.4)
    body = page.evaluate("document.body.innerText")
    assert "清理所有数据" in body, "弹窗应有「清理所有数据」"
    assert "仅清理本周期" in body, "弹窗应有「仅清理本周期」"
    page.screenshot(path="/tmp/t4_reset_dialog.png")

    with page.expect_response(
        lambda r: "/source/detection/reset-stats" in r.url and r.request.method == "POST"
    ) as resp_info:
        page.locator("button:has-text('仅清理本周期')").first.click()
    resp = resp_info.value
    assert resp.ok, f"仅清理本周期应 200, 实际 {resp.status}"
    data = resp.json()
    assert data.get("status") == "success", f"接口应成功, 实际 {data}"
    assert "本周期" in (data.get("message") or ""), f"文案应提到本周期, 实际 {data}"

    # 接口也可直接打（T5 双向：UI 点过之后再 GET/POST 复核契约）
    r = requests.post(
        f"{api_url}/api/v1/source/detection/reset-stats",
        params={"channel": 0, "scope": "cycle"}, timeout=8)
    r.raise_for_status()
    assert r.json().get("status") == "success"
