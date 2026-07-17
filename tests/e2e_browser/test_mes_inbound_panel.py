"""工单接收(入站对接)面板 CI E2E —— 守住「面板有 UI + 保存真落库」这条回归线。

背景: 历史上"自定义导出数据路径"后端做了、前端没加按钮, 单测+build 全绿却漏了入口。
本用例就是那一类的护栏: 真开浏览器渲染「工单接收」面板, 断言关键控件都在,
再点保存验证后端真落库。任何人误删面板/改坏保存链路 → 这条 CI 用例立刻红。

需后端+前端在跑(conftest 未起会自动 skip)。可用 E2E_API_URL/E2E_BASE_URL 指到隔离实例。
"""
import requests


def test_inbound_panel_renders_all_controls(page, base_url):
    """面板常驻控件全部渲染(上次漏测 UI 的同类护栏)。"""
    page.goto(f"{base_url}/#/mes", wait_until="networkidle")
    page.get_by_text("工单接收", exact=True).first.click()
    page.wait_for_selector("text=字段映射", timeout=8000)
    body = page.evaluate("document.body.innerText")

    required_labels = [
        "字段映射", "必填字段", "按产品码切项目", "开工即建工单",
        "拒绝重复任务", "最新开工为准", "完工信号字段",
        "响应体格式", "业务码字段", "各类失败码", "自定义响应模板",
    ]
    missing = [x for x in required_labels if x not in body]
    assert not missing, f"工单接收面板缺控件: {missing}"


def test_inbound_terminal_policy_persists(page, base_url, api_url):
    """v3.42「终态工单再开工」下拉: 渲染存在 → 切到'拒收' → 保存 → 后端落库。"""
    page.goto(f"{base_url}/#/mes", wait_until="networkidle")
    page.get_by_text("工单接收", exact=True).first.click()
    page.wait_for_selector("text=终态工单再开工", timeout=8000)

    lab = page.get_by_text("终态工单再开工", exact=True).first
    fi = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
    fi.locator(".el-select").first.click()
    page.locator(".el-select-dropdown__item:has-text('拒收并提示')").first.click()

    page.get_by_role("button", name="保存配置").first.click()
    page.wait_for_selector(".el-message--success", timeout=6000)

    r = requests.get(f"{api_url}/api/v1/mes/inbound/config", timeout=10)
    assert r.status_code == 200, f"读配置失败 http={r.status_code}"
    assert r.json().get("terminal_order_policy") == "reject", "保存后后端未落库 terminal_order_policy"

    # 还原默认 revive, 不给后续测试留状态
    try:
        requests.put(f"{api_url}/api/v1/mes/inbound/config",
                     json={"enabled": False, "terminal_order_policy": "revive"}, timeout=10)
    except Exception:
        pass


def test_inbound_supersede_toggle_persists(page, base_url, api_url):
    """开『最新开工为准』→ 保存 → 后端 GET 配置确实落库(UI→后端双向验证)。"""
    page.goto(f"{base_url}/#/mes", wait_until="networkidle")
    page.get_by_text("工单接收", exact=True).first.click()
    page.wait_for_selector("text=最新开工为准", timeout=8000)

    # 用精确 label 定位 el-form-item, 避免子串误命中(别项说明里含本项 label 文字)
    lab = page.get_by_text("最新开工为准", exact=True).first
    fi = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
    sw = fi.locator(".el-switch").first
    if "is-checked" not in (sw.get_attribute("class") or ""):
        sw.click()

    # 开了顶替, 条件控件应出现
    page.wait_for_selector("text=顶替范围", timeout=4000)

    page.get_by_role("button", name="保存配置").first.click()
    page.wait_for_selector(".el-message--success", timeout=6000)

    r = requests.get(f"{api_url}/api/v1/mes/inbound/config", timeout=10)
    assert r.status_code == 200, f"读配置失败 http={r.status_code}"
    assert r.json().get("supersede_previous_task") is True, "保存后后端未落库 supersede_previous_task"

    # 还原, 不给后续测试留状态
    try:
        requests.put(f"{api_url}/api/v1/mes/inbound/config",
                     json={"enabled": False, "supersede_previous_task": False}, timeout=10)
    except Exception:
        pass
