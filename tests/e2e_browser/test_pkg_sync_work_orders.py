"""配置面板 E2E — 包装工单镜像进工单管理 (v3.45.1).

两条链路:
  1. 包装面板组⑤「同步到工单管理」开关: 默认开 → 关掉 → 保存 →
     GET 契约 sync_work_orders=False 真落库.
  2. MES 工单页: source=packaging 的工单来源标签显示「包装扫码」.

前置: 后端 8001 + 前端 6001 已起 (conftest 没起则整组 skip).
"""
import uuid

import requests


def _cleanup_pkg(api_url):
    try:
        r = requests.get(f"{api_url}/api/v1/packaging-flows", timeout=10)
        for c in (r.json() or {}).get("items", []):
            if str(c.get("name", "")).startswith("__e2e_"):
                if c.get("enabled"):
                    requests.put(f"{api_url}/api/v1/packaging-flows/{c['id']}",
                                 json={"enabled": False}, timeout=10)
                requests.delete(f"{api_url}/api/v1/packaging-flows/{c['id']}",
                                timeout=10)
    except Exception:
        pass


def _cleanup_wo(api_url, order_no):
    try:
        r = requests.get(f"{api_url}/api/v1/mes/orders",
                         params={"keyword": order_no}, timeout=10)
        for o in (r.json() or {}).get("items", []):
            if o.get("order_no") == order_no:
                requests.delete(f"{api_url}/api/v1/mes/orders/{o['id']}",
                                timeout=10)
    except Exception:
        pass


def test_sync_work_orders_switch_default_on_and_save_off(page, base_url, api_url):
    _cleanup_pkg(api_url)
    name = f"__e2e_wosync_{uuid.uuid4().hex[:6]}"

    # 包装结算已从系统设置迁到 MES 管理页（自绘按钮 tab）
    page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded",
              timeout=15000)
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="包装结算").click()
    page.wait_for_timeout(800)
    page.get_by_role("button", name="新建配置").click()
    page.wait_for_selector(".el-dialog", state="visible", timeout=5000)
    page.locator('input[placeholder="上银包装线-1"]').fill(name)

    # 展开组⑤ → 开关默认开 → 关掉
    dlg = page.locator(".el-dialog").last
    dlg.get_by_text("⑤ 收尾与回推").click()
    page.wait_for_timeout(400)
    sw = dlg.locator(".el-form-item", has_text="同步到工单管理") \
            .first.locator(".el-switch").first
    assert "is-checked" in (sw.get_attribute("class") or ""), \
        "「同步到工单管理」默认应为开"
    sw.click()
    page.wait_for_timeout(300)

    page.locator(".el-dialog__footer").get_by_role(
        "button", name="保存").first.click()
    page.wait_for_timeout(1200)

    r = requests.get(f"{api_url}/api/v1/packaging-flows", timeout=10)
    found = [c for c in (r.json() or {}).get("items", [])
             if c.get("name") == name]
    assert found, f"配置 {name} 未落库"
    assert found[0]["sync_work_orders"] is False, found[0]
    _cleanup_pkg(api_url)


def test_work_order_page_shows_packaging_source_tag(page, base_url, api_url):
    order_no = f"__E2E_PKGWO_{uuid.uuid4().hex[:6]}"
    _cleanup_wo(api_url, order_no)
    r = requests.post(f"{api_url}/api/v1/mes/orders", json={
        "order_no": order_no, "product_name": "包装工单",
        "planned_qty": 4, "source": "packaging",
        "binding_scope": "channels", "target_channels": [0],
    }, timeout=10)
    assert r.status_code in (200, 201), r.text

    try:
        page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded",
                  timeout=15000)
        page.wait_for_load_state("networkidle")
        page.get_by_text("工单管理", exact=True).first.click()
        page.wait_for_timeout(1000)
        # 关掉"仅当前项目"过滤 (conftest 每用例自动激活临时项目,
        # 包装镜像单不绑项目, 开着会被滤掉) + 按单号搜索避开分页
        scope_sw = page.locator(
            "xpath=//span[contains(text(),'仅当前项目')]"
            "/preceding-sibling::div[contains(@class,'el-switch')]").first
        if "is-checked" in (scope_sw.get_attribute("class") or ""):
            scope_sw.click()
            page.wait_for_timeout(500)
        page.locator('input[placeholder*="搜索"]').first.fill(order_no)
        page.get_by_role("button", name="查询").first.click()
        page.wait_for_timeout(800)
        row = page.locator("tr", has_text=order_no).first
        row.wait_for(timeout=10000)
        tag = row.locator(".el-tag").first.inner_text()
        assert "包装扫码" in tag, f"来源标签应显示'包装扫码', 实际: {tag!r}"
    finally:
        _cleanup_wo(api_url, order_no)
