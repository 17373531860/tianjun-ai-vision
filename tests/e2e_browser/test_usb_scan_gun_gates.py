# -*- coding: utf-8 -*-
"""USB 扫码枪弹窗「绑定防呆」开关 E2E（v3.46 先扫后检/无码告警对 USB 枪生效）。

验证面:
  - 用途=拉工单 时不显示防呆开关（与周期绑定无关）
  - 切到 绑工件 后出现「先扫后检 / 无码告警」两开关, 默认关
  - 打开两开关保存 → 后端设备记录 scan_required / warn_no_barcode 落库为 true
  - v3.46 第二批: 绑工件用途露出与网络扫码器一致的绑定行为参数
    (重复扫码策略/去重间隔/OK冷却/迟到补绑), 改值保存后同样落库
资源用 __e2e_ 前缀命名, 结束通过 API 删除。
"""
from __future__ import annotations

import time
import uuid

import requests


def test_usb_gun_gate_switches_save_to_db(page, base_url, api_url):
    name = "__e2e_usb枪防呆开关"
    created_id = None
    try:
        page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        page.get_by_role("button", name="扫码器", exact=True).click(timeout=8000)
        page.wait_for_timeout(1500)
        page.locator("button:has-text('添加 USB 扫码枪')").first.click(timeout=8000)
        page.wait_for_timeout(800)

        dlg = page.locator(".el-dialog:visible").first
        dlg.locator("input").first.fill(name)

        # 默认用途=拉工单 → 防呆开关不应出现
        assert dlg.locator("text=先扫后检").count() == 0, "拉工单用途不应显示防呆开关"

        # 切到 绑工件 → 开关出现且默认关
        dlg.locator("label:has-text('绑工件')").first.click()
        page.wait_for_timeout(500)
        row = dlg.locator(".el-form-item:has-text('绑定防呆')")
        switches = row.locator(".el-switch")
        assert switches.count() == 2, f"防呆开关数量应为2, 实际 {switches.count()}"
        for i in range(2):
            assert "is-checked" not in (switches.nth(i).get_attribute("class") or ""), \
                "防呆开关默认应为关"

        switches.nth(0).click()
        switches.nth(1).click()
        page.wait_for_timeout(300)

        # v3.46 第二批: 绑定行为参数应随"绑工件"用途出现, 改值应落库
        assert dlg.locator(".el-form-item:has-text('重复扫码')").count() >= 1, \
            "绑工件用途应显示重复扫码策略"
        dlg.locator(".el-form-item:has-text('重复扫码') .el-select").click()
        page.wait_for_timeout(500)
        page.locator(".el-select-dropdown:visible li:has-text('排队')").click()
        page.wait_for_timeout(300)
        dedup_inp = dlg.locator(
            ".el-form-item:has-text('去重间隔') .el-input-number input").first
        dedup_inp.fill("7")
        page.wait_for_timeout(300)

        dlg.locator("button:has-text('保存')").click()
        page.wait_for_timeout(1500)

        # 后端落库双向核对
        devs = requests.get(f"{api_url}/api/v1/scanner/devices", timeout=10).json()
        dev = next((d for d in devs if d.get("name") == name), None)
        assert dev is not None, "新建的 USB 枪未落库"
        created_id = dev["id"]
        assert dev["scan_required"] is True, "先扫后检未落库"
        assert dev["warn_no_barcode"] is True, "无码告警未落库"
        assert dev["duplicate_scan_action"] == "queue", "重复扫码策略未落库"
        assert dev["dedup_interval_sec"] == 7, "去重间隔未落库"
        assert (dev.get("parse_config") or {}).get("usb", {}).get("usage") == "bind"
    finally:
        if created_id is not None:
            requests.delete(f"{api_url}/api/v1/scanner/devices/{created_id}",
                            timeout=10)


def test_usb_gun_dialog_test_button_drives_scan_chain(page, base_url, api_url):
    """面向功能: 弹窗「真跑一次」→ 真实扫码链路 → 后端产生扫码事件。

    锁定 v3.46 对齐后的行为: USB 枪的码注入后按工位解析到 USB 枪配置载体,
    走 ScannerService 完整处理链进 MES, 最终 detection/results 的 mes.scan_event
    能查到这枚码 (即操作员在监控页能看到"已扫码")。
    需要通道 0 有激活项目 (工位无项目时扫码在服务层被丢弃) — 没有则跳过。
    """
    import pytest

    r = requests.get(f"{api_url}/api/v1/source/detection/results?channel=0",
                     timeout=10)
    if r.status_code != 200 or not (r.json() or {}).get("project_config"):
        pytest.skip("通道 0 无激活项目, 扫码链路无法登记工件")

    name = "__e2e_usb枪扫码链路"
    code = f"__E2E-{uuid.uuid4().hex[:8]}"
    created_id = None
    try:
        # 建一把绑工件用途的 USB 枪 (API 直建, UI 建枪已有用例覆盖)
        resp = requests.post(f"{api_url}/api/v1/scanner/devices", json={
            "name": name, "ip": "", "port": 0, "channel_id": 0,
            "enabled": True, "device_type": "usb_hid",
            "parse_config": {"usb": {"usage": "bind", "order_pattern": "",
                                     "pull_conn_id": None}},
        }, timeout=10)
        assert resp.status_code in (200, 201), resp.text[:200]
        created_id = resp.json()["id"]

        # UI: 编辑弹窗里输测试码 → 点「真跑一次」
        page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded",
                  timeout=15000)
        page.wait_for_timeout(2000)
        page.get_by_role("button", name="扫码器", exact=True).click(timeout=8000)
        page.wait_for_timeout(1500)
        card = page.locator(f"div:has-text('{name}')")
        card.locator("button:has-text('编辑')").first.click(timeout=8000)
        page.wait_for_timeout(1000)
        dlg = page.locator(".el-dialog:visible").first
        dlg.locator(".el-form-item:has-text('模拟扫一个码') input").first.fill(code)
        page.wait_for_timeout(300)
        dlg.locator("button:has-text('真跑一次')").click()
        page.wait_for_timeout(500)
        assert page.locator(".el-notification:has-text('已注入绑定链路')") \
            .count() >= 1, "注入成功通知未出现"

        # 后端行为核对: 扫码进 MES 是异步队列 → 轮询扫码事件
        deadline = time.time() + 6
        evt = None
        while time.time() < deadline:
            rr = requests.get(
                f"{api_url}/api/v1/source/detection/results?channel=0",
                timeout=10)
            evt = ((rr.json() or {}).get("mes") or {}).get("scan_event")
            if evt and evt.get("serial_no") == code:
                break
            time.sleep(0.3)
        assert evt and evt.get("serial_no") == code, \
            f"扫码事件未出现在检测结果里: {evt}"
    finally:
        if created_id is not None:
            requests.delete(f"{api_url}/api/v1/scanner/devices/{created_id}",
                            timeout=10)
        # 清掉本用例登记的待检工件与去重缓存, 不污染用户工位状态
        requests.post(f"{api_url}/api/v1/source/detection/clear_pending_scan"
                      f"?channel=0&force=true", timeout=10)
