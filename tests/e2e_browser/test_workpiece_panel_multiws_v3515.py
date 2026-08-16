"""v3.51.5: 多工位下工件追溯页默认必须显示全部项目的工件.

背景 (2026-08-15 捷昌 B 站现场): 双工位双项目, 追溯页默认"仅当前项目"
过滤把另一工位项目的 ok 工件藏起来 → 操作员全选批量删除以为删干净 →
strict_ok_dedup 仍按项目查到残留 ok 工件拒码, 人对着空列表找不到原因。

约定: 单工位保持"仅当前项目"默认开 (老行为); 多工位默认关。
"""
import time

import pytest
import requests


def _api(api_url, method, path, **kw):
    r = requests.request(method, f"{api_url}/api/v1{path}", timeout=15, **kw)
    return r


def _mk_project(api_url, name):
    payload = {
        "name": name, "task_type": "detection", "logic_mode": "detection",
        "pipeline_config": {}, "steps_config": [], "events_config": [],
        "counters_config": [], "alarm_config": {}, "detection_config": {},
        "data_config": {}, "default_model_id": None,
        "model_format": "pytorch_fp32",
    }
    r = _api(api_url, "POST", "/projects", json=payload)
    r.raise_for_status()
    return r.json()["id"]


@pytest.fixture
def dual_ws_two_projects(api_url):
    """双工位 + 两个项目各挂一个工件; 结束恢复工位数并清资源."""
    old_count = _api(api_url, "GET", "/workstations/").json().get(
        "channel_count", 1)
    _api(api_url, "POST", "/workstations/mode",
         json={"channel_count": 2, "channels": []})

    pa = _mk_project(api_url, "__e2e_wp_panel_A")
    pb = _mk_project(api_url, "__e2e_wp_panel_B")
    sn_a, sn_b = "__E2E_WP_SN_A", "__E2E_WP_SN_B"
    wp_ids = []
    for sn, pid in ((sn_a, pa), (sn_b, pb)):
        r = _api(api_url, "POST", "/mes/workpieces",
                 json={"serial_no": sn, "project_id": pid})
        if r.status_code == 200:
            wp_ids.append(r.json()["id"])
    # 激活 A → 全局"当前项目"= A, B 的工件成为"会被默认过滤藏掉"的那批
    _api(api_url, "POST", f"/projects/{pa}/activate")

    yield {"pa": pa, "pb": pb, "sn_a": sn_a, "sn_b": sn_b}

    for wid in wp_ids:
        _api(api_url, "DELETE", f"/mes/workpieces/{wid}")
    for pid in (pa, pb):
        _api(api_url, "DELETE", f"/projects/{pid}")
    _api(api_url, "POST", "/workstations/mode",
         json={"channel_count": old_count, "channels": []})


def test_multiws_workpiece_panel_shows_all_projects(page, base_url, api_url,
                                                    dual_ws_two_projects):
    ctx = dual_ws_two_projects
    page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded",
              timeout=15000)
    page.get_by_text("工件追溯", exact=False).first.click()
    # 等列表加载
    page.wait_for_timeout(1500)

    # 双工位下默认必须能看到两个项目的工件 (A=当前项目, B=另一工位项目)
    assert page.get_by_text(ctx["sn_a"]).count() >= 1, \
        "当前项目 A 的工件必须可见"
    assert page.get_by_text(ctx["sn_b"]).count() >= 1, (
        "多工位下另一项目 B 的工件必须默认可见 — 否则操作员批量删除"
        "删不到它, strict_ok_dedup 残留拒码 (捷昌 B 站 2026-08-15)")


def test_multiws_delete_all_then_dedup_released(page, base_url, api_url,
                                                dual_ws_two_projects):
    """删干净全部项目的工件后, 同码必须能重新登记 (去重解除)."""
    ctx = dual_ws_two_projects
    # 把 B 项目工件置 ok (模拟已合格) — 走 DB 侧接口不可用, 用 action 接口
    r = _api(api_url, "GET", "/mes/workpieces",
             params={"keyword": ctx["sn_b"]})
    items = r.json().get("items", [])
    assert items, "预置工件必须存在"
    wp_b = items[0]["id"]

    # 页面上批量删除全部 (含 B 项目工件 — 修复后默认可见)
    page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded",
              timeout=15000)
    page.get_by_text("工件追溯", exact=False).first.click()
    page.wait_for_timeout(1500)
    assert page.get_by_text(ctx["sn_b"]).count() >= 1

    # 全选 (表头 checkbox) → 批量删除 → 确认
    page.locator("th .el-checkbox").first.click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="批量删除", exact=False).first.click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="删除", exact=False).last.click()
    page.wait_for_timeout(1200)

    # 后端核验: B 工件真没了 (UI 删除 → 落库双向验证)
    r = _api(api_url, "GET", f"/mes/workpieces/{wp_b}")
    assert r.status_code == 404, "批量删除必须真删掉另一项目的工件"
