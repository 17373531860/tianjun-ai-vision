# -*- coding: utf-8 -*-
"""扫码器列表为空时, 监控页不得再画「等待扫码 / 清除 / 禁用扫码」。

v3.5.2 只挡住了「⚠ 未绑码」; v3.47 布局重构把仅有的占位守门弄丢。
本用例把整条扫码 UI 跟 mes.scanner_present 钉死。
"""
from __future__ import annotations

import json
import time
import uuid

import requests


def _scan_ui_text(page) -> str:
    return page.evaluate("document.body.innerText") or ""


def _assert_scan_ui_hidden(page, ctx: str):
    wait = page.get_by_text("等待扫码...", exact=True)
    assert wait.count() == 0, f"{ctx}: 无扫码器不应出现「等待扫码...」"
    assert page.locator("button:has-text('禁用扫码')").count() == 0, \
        f"{ctx}: 无扫码器不应出现「禁用扫码」"
    assert page.locator("button:has-text('清除')").count() == 0, \
        f"{ctx}: 无扫码器不应出现「清除」按钮"


def test_scanner_present_false_hides_waiting_scan_bar(page, base_url, api_url):
    """轮询注入 scanner_present=false + 工位在跑 → 扫码栏整组不渲染。

    不依赖本机扫码器列表是否为空, CI 稳定。
    """

    def _inject(route):
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        mes = data.get("mes") or {}
        mes["scanner_present"] = False
        mes.pop("workpiece", None)
        mes.pop("order", None)
        mes.pop("warn_no_barcode", None)
        mes.pop("scanner_resume_blocked", None)
        data["mes"] = mes
        data["is_running"] = True
        data["is_detecting"] = True
        route.fulfill(
            status=resp.status,
            headers={"content-type": "application/json"},
            body=json.dumps(data),
        )

    def _fake_running(route):
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        data["is_running"] = True
        data["is_detecting"] = True
        route.fulfill(
            status=resp.status,
            headers={"content-type": "application/json"},
            body=json.dumps(data),
        )

    page.route("**/api/v1/source/status*", _fake_running)
    page.route("**/api/v1/source/detection/results*", _inject)
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2500)
    page.screenshot(path="tests/uat/artifacts/mes-bar-no-scanner.png", full_page=True)
    _assert_scan_ui_hidden(page, "注入 scanner_present=false")


def _route_inject_warn(page):
    """伪造工位在跑 + 注入 mes.warn_no_barcode, 让信息条确定性出现。

    「有扫码器时按钮应出现」不能赌工位恰好在跑 / 恰好有工单——
    空闲态信息条本来就不渲染 (设计如此), 且空闲态前端不轮询 results,
    CI 全新库上必闪断。scanner_present 保持后端真值不动。
    """
    def _inject(route):
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        mes = data.get("mes") or {}
        mes["warn_no_barcode"] = True
        data["mes"] = mes
        data["is_running"] = True
        data["is_detecting"] = True
        route.fulfill(
            status=resp.status,
            headers={"content-type": "application/json"},
            body=json.dumps(data),
        )

    def _fake_running(route):
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        data["is_running"] = True
        data["is_detecting"] = True
        route.fulfill(
            status=resp.status,
            headers={"content-type": "application/json"},
            body=json.dumps(data),
        )

    page.route("**/api/v1/source/status*", _fake_running)
    page.route("**/api/v1/source/detection/results*", _inject)


def test_empty_scanner_list_hides_then_create_shows(page, base_url, api_url):
    """真列表空 → 栏隐藏; 建一条 __e2e_ 扫码器 → 栏出现; 删掉 → 再隐藏。

    若环境里已有非 e2e 扫码器, 只验后端 scanner_present 真值, 不拆别人的设备
    (UI「有扫码器该显示」方向由本用例主分支 + test_task_info_layout 在 CI 守)。
    """
    devices = requests.get(f"{api_url}/api/v1/scanner/devices", timeout=10).json()
    foreign = [d for d in devices if not str(d.get("name") or "").startswith("__e2e_")]
    created_id = None
    try:
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)
        mes = (requests.get(
            f"{api_url}/api/v1/source/detection/results?channel=0", timeout=5
        ).json().get("mes") or {})
        present = mes.get("scanner_present")

        if not foreign:
            assert present is False, f"列表空时 scanner_present 应为 false, 实际 {present}"
            _assert_scan_ui_hidden(page, "真列表为空")

            name = f"__e2e_mesbar_{uuid.uuid4().hex[:6]}"
            r = requests.post(f"{api_url}/api/v1/scanner/devices", json={
                "name": name, "ip": "127.0.0.1", "port": 24117,
                "enabled": True, "device_type": "text_lon", "channel_id": 0,
            }, timeout=10)
            assert r.status_code == 200, r.text[:300]
            created_id = r.json()["id"]

            deadline = time.time() + 8
            while time.time() < deadline:
                mes = (requests.get(
                    f"{api_url}/api/v1/source/detection/results?channel=0", timeout=5
                ).json().get("mes") or {})
                if mes.get("scanner_present") is True:
                    break
                time.sleep(0.3)
            assert mes.get("scanner_present") is True, "新建扫码器后 scanner_present 应为 true"

            _route_inject_warn(page)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            txt = _scan_ui_text(page)
            assert "禁用扫码" in txt, "有扫码器时应出现「禁用扫码」"

            # 路由保持注入 (warn+running), 删枪后 scanner_present 真值翻 false,
            # 断言"即便有未绑码警告也整组不渲染"——与用例 1 相同的强断言
            requests.delete(f"{api_url}/api/v1/scanner/devices/{created_id}", timeout=10)
            created_id = None
            deadline = time.time() + 8
            while time.time() < deadline:
                mes = (requests.get(
                    f"{api_url}/api/v1/source/detection/results?channel=0", timeout=5
                ).json().get("mes") or {})
                if mes.get("scanner_present") is False:
                    break
                time.sleep(0.3)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            _assert_scan_ui_hidden(page, "删掉扫码器后")
        else:
            assert present is not False, "有扫码器时 scanner_present 不应为 false"
    finally:
        if created_id is not None:
            requests.delete(f"{api_url}/api/v1/scanner/devices/{created_id}", timeout=10)
