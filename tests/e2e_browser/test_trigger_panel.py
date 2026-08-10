# -*- coding: utf-8 -*-
"""RFC 14 统一触发中心 —— 系统设置页「触发中心」面板 CI E2E。

守住三条回归线:
  1. 面板真有 UI: 系统设置页有「触发中心」tab, 空态/模板/添加按钮都在
     (历史教训: 后端做了前端没入口, 单测+build 全绿客户才发现)
  2. UI 建触发源 → 后端真落库 (T5 双向验证: 模板载入 → 保存 → GET API 核对)
  3. mock 触发源启用后引擎真在跑: 卡片「运行中」, 注入脉冲 →
     实时状态触发计数 +1 + 触发历史出记录 (信号→规则→动作→前端轮询整条链路)

需后端+前端在跑(conftest 未起会自动 skip)。测试资源全部 __e2e_ 前缀, 结束清理。
"""
from __future__ import annotations

import time

import requests

from .conftest import E2E_PREFIX

TRG = "/api/v1/triggers"


def _cleanup_triggers(api_url: str):
    try:
        r = requests.get(f"{api_url}{TRG}/channels", timeout=5)
        for trg in (r.json() or {}).get("triggers", []):
            if (trg.get("name") or "").startswith(E2E_PREFIX):
                requests.delete(f"{api_url}{TRG}/channels/{trg['id']}", timeout=5)
    except Exception as e:
        print(f"[cleanup] trigger 清理失败: {e}")


def _make_mock_trigger(api_url: str, name: str, enabled: bool = True) -> int:
    payload = {
        "name": name, "type": "mock", "enabled": enabled,
        "params": {},
        "rules": [{
            "name": "观察",
            "when": [{"trigger": "pulse"}],
            "actions": [{"do": "set_var", "name": "seen", "value": "yes"}],
        }],
        "options": {"default_channel": 0},
    }
    r = requests.post(f"{api_url}{TRG}/channels", json=payload, timeout=10)
    assert r.status_code == 200, f"建 mock 触发源失败 http={r.status_code}: {r.text}"
    return r.json()["id"]


def _open_trigger_tab(page, base_url: str):
    page.goto(f"{base_url}/#/settings", wait_until="networkidle")
    page.get_by_text("触发中心", exact=True).first.click()
    page.wait_for_selector("text=触发源", timeout=8000)


def test_trigger_tab_renders_controls(page, base_url, api_url):
    """系统设置页有「触发中心」tab, 面板常驻控件全部渲染。"""
    _cleanup_triggers(api_url)
    _open_trigger_tab(page, base_url)
    body = page.evaluate("document.body.innerText")

    required = ["触发源", "方案模板", "导入配置", "添加触发源", "实时状态"]
    missing = [x for x in required if x not in body]
    assert not missing, f"触发中心面板缺控件: {missing}"


def test_trigger_template_dialog_save_persists(page, base_url, api_url):
    """T5 双向: 方案模板载入 mock 范式 → 改名保存 → GET API 核对真落库。"""
    _cleanup_triggers(api_url)
    name = f"{E2E_PREFIX}trg_tpl"
    try:
        _open_trigger_tab(page, base_url)

        # 方案模板下拉 (el-dropdown hover 触发) → 选「虚拟触发源 (联调/演示)」
        page.locator("button:has-text('方案模板')").first.hover()
        page.wait_for_selector(".el-dropdown-menu__item:has-text('虚拟触发源')",
                               timeout=6000)
        page.locator(".el-dropdown-menu__item:has-text('虚拟触发源')").first.click()
        page.wait_for_selector(".el-dialog:has-text('添加触发源')", timeout=6000)

        dialog = page.locator(".el-dialog:has-text('添加触发源')")
        name_input = dialog.locator(".el-form-item:has-text('名称') input").first
        name_input.fill(name)
        dialog.get_by_role("button", name="保存").click()
        page.wait_for_selector(".el-message--success", timeout=6000)

        # 后端落库核对: 名称/类型/模板规则原样入库
        r = requests.get(f"{api_url}{TRG}/channels", timeout=5)
        assert r.status_code == 200
        rows = [t for t in r.json().get("triggers", []) if t["name"] == name]
        assert rows, f"UI 保存后后端查不到触发源 {name}"
        trg = rows[0]
        assert trg["type"] == "mock", f"类型不对: {trg['type']}"
        assert trg.get("rules"), "模板规则没落库"
        assert trg["rules"][0]["actions"][0]["do"] == "trigger_event"

        # UI 列表出现新卡片
        page.wait_for_selector(f"text={name}", timeout=6000)
    finally:
        _cleanup_triggers(api_url)


def test_trigger_mock_engine_live_fire(page, base_url, api_url):
    """mock 触发源启用 → 卡片「运行中」→ 注入脉冲 → 触发计数/历史上屏。"""
    _cleanup_triggers(api_url)
    name = f"{E2E_PREFIX}trg_live"
    tid = _make_mock_trigger(api_url, name, enabled=True)
    try:
        # 等引擎起来 (状态 running)
        deadline = time.time() + 10
        status = None
        while time.time() < deadline:
            r = requests.get(f"{api_url}{TRG}/channels", timeout=5)
            row = next((t for t in r.json().get("triggers", [])
                        if t["id"] == tid), None)
            status = (row or {}).get("runtime", {}).get("status")
            if status == "running":
                break
            time.sleep(0.5)
        assert status == "running", f"mock 引擎 10s 未运行: status={status}"

        _open_trigger_tab(page, base_url)
        page.wait_for_selector(f"text={name}", timeout=8000)

        # 卡片状态徽标「运行中」
        card = page.locator(f".cursor-pointer:has-text('{name}')").first
        assert "运行中" in card.inner_text(), f"卡片没显示运行中: {card.inner_text()}"

        # 点卡片 → 右栏实时状态出现联调按钮
        card.click()
        page.wait_for_selector("text=现场联调", timeout=8000)

        # API 注入脉冲 → 前端轮询 (1.5s) 看到触发计数与历史
        r = requests.post(f"{api_url}{TRG}/channels/{tid}/mock-fire",
                          json={"meta": {}}, timeout=5)
        assert r.status_code == 200, f"mock-fire 失败: {r.text}"
        page.wait_for_selector("text=最近触发", timeout=8000)
        deadline = time.time() + 10
        seen = False
        while time.time() < deadline:
            body = page.evaluate("document.body.innerText")
            if "观察" in body and "pulse" in body:
                seen = True
                break
            time.sleep(0.5)
        assert seen, "注入脉冲后触发历史未上屏 (规则名『观察』/edge pulse)"
    finally:
        _cleanup_triggers(api_url)


def test_trigger_param_fields_full_coverage(page, base_url, api_url):
    """2026-08-10 参数补齐回归: 各触发源类型的声明式参数字段全部露出
    (pixel_region 缓漂/反相, serial 4 串口参数+编码, timer 每日定点,
    http IP 白名单/变量提取), 且 pixel_region 的 ref_drift 缺省水合为开。"""
    _cleanup_triggers(api_url)
    _open_trigger_tab(page, base_url)
    page.locator("button:has-text('添加触发源')").first.click()
    page.wait_for_selector(".el-dialog:has-text('添加触发源')", timeout=6000)
    dialog = page.locator(".el-dialog:has-text('添加触发源')")

    def _switch_type(label):
        dialog.locator(".el-form-item:has-text('类型') .el-select").first.click()
        page.wait_for_selector(f".el-select-dropdown__item:visible:has-text('{label}')",
                               timeout=5000)
        page.locator(f".el-select-dropdown__item:visible:has-text('{label}')").first.click()
        time.sleep(0.5)
        return dialog.inner_text()

    # 默认 pixel_region: 新参数 + ref_drift 缺省水合开
    body = dialog.inner_text()
    for k in ("参考帧缓漂", "缓漂步长", "反相触发"):
        assert k in body, f"pixel_region 缺参数入口「{k}」"
    drift_item = dialog.locator(".el-form-item:has-text('参考帧缓漂')").first
    assert drift_item.locator(".el-switch.is-checked").count() == 1, \
        "ref_drift 缺省语义为开, 开关应水合显示开"

    body = _switch_type("串口报文")
    for k in ("数据位", "校验位", "停止位", "行分隔符", "编码"):
        assert k in body, f"serial_pattern 缺参数入口「{k}」"
    body = _switch_type("定时")
    assert "每日定点" in body, "timer 缺「每日定点」入口"
    body = _switch_type("HTTP")
    for k in ("IP 白名单", "变量提取"):
        assert k in body, f"http 缺参数入口「{k}」"
    page.keyboard.press("Escape")


def test_trigger_csv_json_params_roundtrip(api_url, page, base_url):
    """csv/json 参数 UI↔库 双向: UI 建 http 触发源填 IP 白名单(逗号) +
    变量提取(JSON) → 落库为原生 list/dict → 重开编辑框回显文本。"""
    _cleanup_triggers(api_url)
    name = f"{E2E_PREFIX}trg_httpp"
    try:
        _open_trigger_tab(page, base_url)
        page.locator("button:has-text('添加触发源')").first.click()
        page.wait_for_selector(".el-dialog:has-text('添加触发源')", timeout=6000)
        dialog = page.locator(".el-dialog:has-text('添加触发源')")
        dialog.locator(".el-form-item:has-text('类型') .el-select").first.click()
        page.locator(".el-select-dropdown__item:visible:has-text('HTTP')").first.click()
        time.sleep(0.5)
        dialog.locator(".el-form-item:has-text('名称') input").first.fill(name)
        dialog.locator(".el-form-item:has-text('触发键') input").first.fill("e2e-key")
        dialog.locator(".el-form-item:has-text('IP 白名单') input").first.fill("192.168., 10.0.")
        dialog.locator(".el-form-item:has-text('变量提取') textarea").first.fill('{"sn": "data.sn"}')
        dialog.get_by_role("button", name="保存").click()
        page.wait_for_selector(".el-message--success", timeout=6000)

        rows = [t for t in requests.get(f"{api_url}{TRG}/channels", timeout=5)
                .json().get("triggers", []) if t["name"] == name]
        assert rows, "http 触发源没落库"
        p = rows[0].get("params") or {}
        assert p.get("ip_allow") == ["192.168.", "10.0."], f"ip_allow 应为 list, 实际 {p}"
        assert p.get("extract") == {"sn": "data.sn"}, f"extract 应为 dict, 实际 {p}"

        # 重开编辑框: list/dict 回显为文本
        card = page.locator(f".cursor-pointer:has-text('{name}')").first
        card.locator("button:has-text('编辑')").first.click()
        page.wait_for_selector(".el-dialog:has-text('编辑触发源'), .el-dialog:has-text('添加触发源')",
                               timeout=6000)
        dialog = page.locator(".el-dialog:visible").first
        got_ip = dialog.locator(".el-form-item:has-text('IP 白名单') input").first.input_value()
        got_ex = dialog.locator(".el-form-item:has-text('变量提取') textarea").first.input_value()
        assert got_ip == "192.168., 10.0.", f"ip_allow 回显应为逗号文本, 实际 {got_ip!r}"
        assert '"sn"' in got_ex and "data.sn" in got_ex, f"extract 回显应为 JSON 文本, 实际 {got_ex!r}"
        page.keyboard.press("Escape")
    finally:
        _cleanup_triggers(api_url)
