# -*- coding: utf-8 -*-
"""E2E — v3.44.4 数量门升级放行配置 UI + 工单收尾快照文案 (真浏览器点击).

铁律「带 UI 的功能改动必须走 E2E 点击验证」, 覆盖两块前端改动:
  1. 项目页逻辑设置: 数量门开启时出现「拦下时升级放行」多选, 选项来自
     已选门内步骤; 改动 → 保存 → GET 项目断言 gate_escalate_steps 真落库.
  2. Monitor 包装卡收尾快照: 工单结案后 (a) 快照横幅出现"扫码开始下一张
     工单"引导 (b) "正在装第N箱"进度区隐藏 (完成态还挂着装箱进度是客户
     现场反馈的矛盾信息).

前置: 后端 8001 + 前端 (E2E_BASE_URL, 默认 6001) 已起; 依赖项目 SY6 (id=37,
数量门开) 存在 — 没有则跳过, 不动用户其他项目.
"""
import re
import time
import uuid

import pytest
import requests

PROJECT_ID = 37  # SY6


def _get_project(api_url):
    r = requests.get(f"{api_url}/api/v1/projects/{PROJECT_ID}", timeout=10)
    if r.status_code != 200:
        return None
    return r.json()


def test_gate_escalate_select_roundtrip(page, base_url, api_url):
    """逻辑设置 → 数量门「拦下时升级放行」控件可见可改, 保存真落库."""
    proj = _get_project(api_url)
    if not proj:
        pytest.skip("SY6 (id=37) 不存在")
    ngh = (proj.get("pipeline_config") or {}).get("ng_handling") or {}
    if not ngh.get("gate_enabled"):
        pytest.skip("SY6 数量门未开 (前置条件变了)")

    page.goto(f"{base_url}/#/project", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_load_state("networkidle")
    # 打开 SY6 项目卡 (项目列表点名字)
    page.get_by_text("SY6", exact=True).first.click()
    page.wait_for_timeout(1000)
    page.get_by_role("tab", name="逻辑设置").click()
    page.wait_for_timeout(800)

    row = page.locator("div", has_text=re.compile("拦下时升级放行")).last
    assert row.count() > 0, "升级放行控件未渲染"
    row.scroll_into_view_if_needed()
    page.screenshot(path="/tmp/uat_shots/e2e_gate_escalate_ui.png", full_page=True)

    # 该行 select 应显示已选「封箱」tag (SY6 配置)
    sel = page.locator(".el-form-item, div").filter(
        has_text=re.compile("拦下时升级放行")).last.locator(".el-select").first
    assert "封箱" in sel.inner_text(), f"升级放行应显示封箱: {sel.inner_text()!r}"

    # 往返: 清空 → 保存 → 落库为空; 再选回封箱 → 保存 → 落库 ['封箱']
    sel.hover()
    clear_btn = sel.locator(".el-select__clear")
    if clear_btn.count():
        clear_btn.click()
    else:  # 没有 clear 图标时逐 tag 关
        for tag in sel.locator(".el-tag__close").all():
            tag.click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name=re.compile("^保存")).first.click()
    page.wait_for_timeout(1500)
    got = ((_get_project(api_url).get("pipeline_config") or {})
           .get("ng_handling") or {}).get("gate_escalate_steps")
    assert got == [], f"清空后应落库 []: {got}"

    sel.click()
    page.wait_for_timeout(300)
    page.locator(".el-select-dropdown:visible").get_by_text("封箱", exact=True).click()
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.get_by_role("button", name=re.compile("^保存")).first.click()
    page.wait_for_timeout(1500)
    got = ((_get_project(api_url).get("pipeline_config") or {})
           .get("ng_handling") or {}).get("gate_escalate_steps")
    assert got == ["封箱"], f"重选封箱后应落库 ['封箱']: {got}"


def test_done_snapshot_text_and_no_progress_row(page, base_url, api_url):
    """收尾快照: 扫单开工 → UI 点「强制结案」→ 快照横幅有扫码引导文案,
    且"正在装第N箱"进度区隐藏."""
    # 找绑通道 0 的启用中包装配置 (生产配置「上银SY包装线」)
    r = requests.get(f"{api_url}/api/v1/packaging-flows", timeout=10)
    cfgs = [c for c in (r.json() or {}).get("items", [])
            if c.get("enabled") and c.get("channel_id") == 0]
    if not cfgs:
        pytest.skip("通道 0 无启用中的包装配置")
    cfg = cfgs[0]
    order_no = f"JOB26E2E{uuid.uuid4().hex[:4].upper()}"

    # 扫码开工 (扫码本是外设键盘输入, 走真实 scan 入口)
    r = requests.post(f"{api_url}/api/v1/packaging-flows/scan",
                      json={"code": order_no, "channel_id": 0}, timeout=10)
    assert r.status_code == 200 and r.json().get("handled") is not False, \
        f"扫码开工失败: {r.text[:200]}"

    try:
        page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=20000)
        # 监控页有 150ms 常驻轮询, networkidle 永远等不到 (30s 必超时);
        # 下方 wait_for_selector 自带等待, 这里只给渲染一个短缓冲
        page.wait_for_timeout(1500)
        card = page.locator(".packaging-flow-card, [class*=packaging]").first
        # 等卡片显示本单号
        page.wait_for_selector(f"text={order_no}", timeout=15000)
        assert page.get_by_text("正在装第").count() > 0, "开工态应显示装箱进度区"

        # UI 点强制结案 → 弹窗填原因 → 确认
        page.get_by_role("button", name=re.compile("强制结案")).first.click()
        box = page.locator(".el-message-box")
        box.wait_for(state="visible", timeout=5000)
        ta = box.locator("textarea, input").first
        if ta.count():
            ta.fill("E2E 快照文案验证")
        box.get_by_role("button", name=re.compile("确认强制结案|确定")).first.click()

        # 快照断言: 引导文案出现 + 进度区消失
        page.wait_for_selector("text=扫码开始下一张工单", timeout=10000)
        page.wait_for_timeout(500)
        assert page.get_by_text("正在装第").count() == 0, \
            "收尾快照期间不应再显示「正在装第N箱」"
        page.screenshot(path="/tmp/uat_shots/e2e_done_snapshot.png", full_page=True)
    finally:
        # 清理: 该测试单已被结案 (aborted), 删历史 run 防污染完成单重扫拦截
        try:
            import sqlite3
            con = sqlite3.connect(
                "/home/qianqian/桌面/word/tianjun-main/backend/sql_app.db")
            con.execute("DELETE FROM packaging_flow_runs WHERE order_no=?", (order_no,))
            con.commit()
        except Exception:
            pass
