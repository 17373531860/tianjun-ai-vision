# -*- coding: utf-8 -*-
"""RFC 13 通用 PLC 连接器 —— MES 页「PLC 对接」面板 CI E2E。

守住三条回归线:
  1. 面板真有 UI: MES 页有「PLC 对接」tab, 空态/模板/添加按钮都在
     (历史教训: 后端做了前端没入口, 单测+build 全绿客户才发现)
  2. UI 建连接 → 后端真落库 (T5 双向验证: 对话框保存 → GET API 核对 points/driver)
  3. mock 连接启用后引擎真在跑: 卡片状态「已连接」, 点卡片实时监视出点位值,
     PLC 侧写 bool → 实时值翻 TRUE (值缓存→前端轮询整条链路)

需后端+前端在跑(conftest 未起会自动 skip)。测试资源全部 __e2e_ 前缀, 结束清理。
"""
from __future__ import annotations

import time

import requests

from .conftest import E2E_PREFIX

PLC = "/api/v1/plc"


def _cleanup_plc(api_url: str):
    try:
        r = requests.get(f"{api_url}{PLC}/connections", timeout=5)
        for conn in (r.json() or {}).get("connections", []):
            if (conn.get("name") or "").startswith(E2E_PREFIX):
                requests.delete(f"{api_url}{PLC}/connections/{conn['id']}", timeout=5)
    except Exception as e:
        print(f"[cleanup] plc 连接清理失败: {e}")


def _make_mock_connection(api_url: str, name: str, enabled: bool = True) -> int:
    """API 直建一条 mock 连接 (bool 触发位 + string 产品号 + bool 回执位)。"""
    payload = {
        "name": name,
        "driver": "mock",
        "enabled": enabled,
        "conn_params": {"store_id": name, "poll_interval_ms": 100},
        "points": [
            {"key": "read_done", "addr": "m0", "type": "bool", "dir": "read"},
            {"key": "product_sn", "addr": "m1", "type": "string_fixed",
             "length": 8, "dir": "read"},
            {"key": "vision_ack", "addr": "m2", "type": "bool", "dir": "read_write"},
        ],
        "read_rules": [],
        "write_rules": [],
        "options": {"default_channel": 0},
    }
    r = requests.post(f"{api_url}{PLC}/connections", json=payload, timeout=10)
    assert r.status_code == 200, f"建 mock 连接失败 http={r.status_code}: {r.text}"
    return r.json()["id"]


def _open_plc_tab(page, base_url: str):
    page.goto(f"{base_url}/#/mes", wait_until="networkidle")
    page.get_by_text("PLC 对接", exact=True).first.click()
    page.wait_for_selector("text=PLC 连接", timeout=8000)


def test_plc_tab_renders_controls(page, base_url, api_url):
    """MES 页有「PLC 对接」tab, 面板常驻控件全部渲染。"""
    _cleanup_plc(api_url)
    _open_plc_tab(page, base_url)
    body = page.evaluate("document.body.innerText")

    required = ["PLC 连接", "方案模板", "导入配置", "添加连接", "实时监视"]
    missing = [x for x in required if x not in body]
    assert not missing, f"PLC 面板缺控件: {missing}"


def test_plc_template_dialog_save_persists(page, base_url, api_url):
    """T5 双向: 方案模板载入 mock 范式 → 改名保存 → GET API 核对真落库。"""
    _cleanup_plc(api_url)
    name = f"{E2E_PREFIX}plc_tpl"
    try:
        _open_plc_tab(page, base_url)

        # 方案模板下拉 (el-dropdown 默认 hover 触发) → 选「虚拟 PLC (联调/演示)」
        page.locator("button:has-text('方案模板')").first.hover()
        page.wait_for_selector(".el-dropdown-menu__item:has-text('虚拟 PLC')",
                               timeout=6000)
        page.locator(".el-dropdown-menu__item:has-text('虚拟 PLC')").first.click()
        page.wait_for_selector(".el-dialog:has-text('添加 PLC 连接')", timeout=6000)

        dialog = page.locator(".el-dialog:has-text('添加 PLC 连接')")
        name_input = dialog.locator(".el-form-item:has-text('名称') input").first
        name_input.fill(name)
        dialog.get_by_role("button", name="保存").click()
        page.wait_for_selector(".el-message--success", timeout=6000)

        # 后端落库核对: 名称/驱动/模板点位原样入库
        r = requests.get(f"{api_url}{PLC}/connections", timeout=5)
        assert r.status_code == 200
        rows = [c for c in r.json().get("connections", []) if c["name"] == name]
        assert rows, f"UI 保存后后端查不到连接 {name}"
        conn = rows[0]
        assert conn["driver"] == "mock", f"驱动不对: {conn['driver']}"
        keys = {p["key"] for p in conn.get("points", [])}
        assert {"read_done", "product_no"} <= keys, f"模板点位没落库: {keys}"
        assert conn.get("read_rules"), "模板触发规则没落库"

        # UI 列表出现新卡片
        page.wait_for_selector(f"text={name}", timeout=6000)
    finally:
        _cleanup_plc(api_url)


def test_plc_mock_engine_live_values(page, base_url, api_url):
    """mock 连接启用 → 卡片「已连接」→ 实时监视出点位 → PLC 侧写翻 TRUE。"""
    _cleanup_plc(api_url)
    name = f"{E2E_PREFIX}plc_live"
    cid = _make_mock_connection(api_url, name, enabled=True)
    try:
        # 等引擎起来 (状态 connected)
        deadline = time.time() + 10
        status = None
        while time.time() < deadline:
            r = requests.get(f"{api_url}{PLC}/connections", timeout=5)
            row = next((c for c in r.json().get("connections", [])
                        if c["id"] == cid), None)
            status = (row or {}).get("runtime", {}).get("status")
            if status == "connected":
                break
            time.sleep(0.5)
        assert status == "connected", f"mock 引擎 10s 未连上: status={status}"

        _open_plc_tab(page, base_url)
        page.wait_for_selector(f"text={name}", timeout=8000)

        # 卡片状态徽标「已连接」
        card = page.locator(f".cursor-pointer:has-text('{name}')").first
        assert "已连接" in card.inner_text(), f"卡片没显示已连接: {card.inner_text()}"

        # 点卡片 → 实时监视出点位行
        card.click()
        page.wait_for_selector("text=read_done", timeout=8000)
        page.wait_for_selector("text=product_sn", timeout=4000)

        # PLC 侧写 read_done=1 → 前端实时值 (1.5s 轮询) 翻 TRUE
        r = requests.post(f"{api_url}{PLC}/connections/{cid}/mock-set",
                          json={"point": "read_done", "value": 1}, timeout=5)
        assert r.status_code == 200, f"mock-set 失败: {r.text}"
        page.wait_for_selector(
            ".flex:has(span:text-is('read_done')) span:text-is('TRUE')",
            timeout=8000)
    finally:
        _cleanup_plc(api_url)
