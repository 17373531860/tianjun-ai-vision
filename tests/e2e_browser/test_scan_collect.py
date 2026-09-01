"""v3.56 周期多码采集 CI E2E 回归（真实链路, 不注入）。

覆盖:
  1. MES管理→扫码器→多码采集 tab: 项目选择器默认激活项目、填示例、
     启用、保存 → GET config 落库双向验证
  2. 检测主页(三工位紧凑态)面板渲染 + 模拟扫码逐码进度 + 顺序兜底分类
  3. 明细浮层删单码纠错 + 收尾码结算 OK + records 落库(默认排除已删码)
  4. kiosk 副屏只读: 面板可见但无清空/删码按钮

前置: backend 8001 + frontend 6001 已启动 (conftest 门卫自动跳过)。
配置读写走 UI, 扫码走 POST /scanner/simulate (虚拟扫码器兜底, 无需硬件),
断言 UI 与后端状态双向。收尾恢复原激活项目并删除测试项目。
"""
from __future__ import annotations

import time
import uuid

import pytest
import requests
from playwright.sync_api import expect

_PREFIX = "__e2e_sc_"


@pytest.fixture
def sc_project(api_url):
    """建测试项目并激活; 收尾停用配置、恢复原激活项目、删测试项目。"""
    orig_active = None
    r = requests.get(f"{api_url}/api/v1/projects/", timeout=10)
    for p in r.json().get("items", []):
        if p.get("is_active"):
            orig_active = p["id"]
            break

    name = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
    r = requests.post(f"{api_url}/api/v1/projects/", json={
        "name": name, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    assert r.status_code in (200, 201), r.text[:200]
    pid = r.json()["id"]
    r = requests.post(f"{api_url}/api/v1/projects/{pid}/activate", timeout=30)
    assert r.status_code == 200

    yield {"id": pid, "name": name}

    for ch in range(4):
        requests.post(f"{api_url}/api/v1/scan-collect/clear",
                      json={"channel_id": ch}, timeout=5)
    requests.put(f"{api_url}/api/v1/scan-collect/config?project_id={pid}",
                 json={"enabled": False, "slots": []}, timeout=5)
    if orig_active:
        requests.post(f"{api_url}/api/v1/projects/{orig_active}/activate",
                      timeout=30)
    requests.delete(f"{api_url}/api/v1/projects/{pid}", timeout=10)


def _scan(api_url, code, channel):
    r = requests.post(f"{api_url}/api/v1/scanner/simulate",
                      json={"barcode": code, "channel_id": channel}, timeout=5)
    assert r.status_code == 200, f"simulate {code}: {r.status_code}"
    time.sleep(0.5)


def _state(api_url, channel):
    return requests.get(
        f"{api_url}/api/v1/scan-collect/state?channel={channel}",
        timeout=5).json()


def _configure_via_ui(page, base_url, proj_name):
    """MES→扫码器→多码采集: 填示例(芯子改2) + 启用 + 保存。"""
    page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded")
    page.get_by_role("button", name="扫码器").click()
    page.get_by_role("tab", name="多码采集").click()
    sel = page.get_by_test_id("sc-project-select")
    expect(sel).to_be_visible(timeout=8000)
    expect(sel).to_contain_text(proj_name, timeout=8000)

    page.get_by_role("button", name="填入示例").click()
    rows = page.locator(".el-table__body tr")
    expect(rows).to_have_count(3, timeout=5000)
    # 名称列是 el-input, 文本在 value 里 has_text 匹配不到; 示例顺序固定芯子=第2行
    chip_count = rows.nth(1).locator(".el-input-number input")
    chip_count.fill("2")
    chip_count.press("Enter")
    page.get_by_test_id("sc-enabled-switch").click()
    page.get_by_test_id("sc-save-btn").click()
    expect(page.locator(".el-message--success").last).to_be_visible(timeout=5000)


def _find_panel_channel(page):
    """激活项目被哪个工位收养取决于现有绑定 — 从面板 testid 反解通道号。"""
    panel = page.locator('[data-testid^="triple-scan-"], [data-testid^="dual-scan-"]').first
    expect(panel).to_be_visible(timeout=15000)
    return panel, int(panel.get_attribute("data-testid").rsplit("-", 1)[-1])


def test_配置UI保存落库(page, base_url, api_url, sc_project):
    _configure_via_ui(page, base_url, sc_project["name"])
    cfg = requests.get(
        f"{api_url}/api/v1/scan-collect/config?project_id={sc_project['id']}",
        timeout=5).json()
    assert cfg["enabled"] is True
    assert len(cfg["slots"]) == 3
    chip = next(s for s in cfg["slots"] if s["key"] == "chip")
    assert chip["count"] == 2
    closing = [s for s in cfg["slots"] if s.get("role") == "closing"]
    assert len(closing) == 1 and closing[0]["key"] == "fixture"
    # 填入示例按现场确认单预设: NG 挂起补扫 + 视觉双重验证(缺视觉结果按扫码判)
    assert cfg["ng_pending"] is True
    assert cfg["vision_gate"] is True and cfg["vision_missing"] == "ignore"


def test_监控面板_扫码_纠错_结算(page, base_url, api_url, sc_project):
    _configure_via_ui(page, base_url, sc_project["name"])

    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
    panel, ch = _find_panel_channel(page)
    total = panel.get_by_test_id("scan-total")
    expect(total).to_have_text("0/4", timeout=8000)

    # 正则分类: 三类码均带正则 (母排^M/芯子13位数字/工装^H-C)
    _scan(api_url, "M010200519A100005036272608310061", ch)
    _scan(api_url, "9260000144908", ch)
    _scan(api_url, "9260000145631", ch)
    expect(total).to_have_text("3/4", timeout=8000)
    got = {s["key"]: s["got"] for s in _state(api_url, ch)["slots"]}
    assert got == {"busbar": 1, "chip": 2, "fixture": 0}

    # 芯子槽已满再扫芯子 → 槽位级 on_overflow=ng_alarm (示例预置): 拒收不入槽
    _scan(api_url, "9260000146072", ch)
    got = {s["key"]: s["got"] for s in _state(api_url, ch)["slots"]}
    assert got["chip"] == 2

    # 明细浮层删单码 (扫错纠正)
    panel.get_by_test_id("scan-detail-btn").click()
    pop = page.locator(".scan-slots-popover")
    expect(pop.get_by_test_id("scan-code-chip").first).to_be_visible(timeout=5000)
    pop.get_by_test_id("scan-code-chip").filter(has_text="9260000145631") \
        .get_by_test_id("scan-code-remove").click()
    expect(total).to_have_text("2/4", timeout=8000)
    page.keyboard.press("Escape")

    # 补扫 + 工装码(^H-C)收尾结算
    gid = _state(api_url, ch)["group_id"]
    _scan(api_url, "9260000146312", ch)
    _scan(api_url, "H-C035-527-5", ch)
    expect(total).to_have_text("0/4", timeout=8000)  # 结算后归零
    last = _state(api_url, ch).get("last_settled") or {}
    assert last.get("result") == "ok"
    assert last.get("workpiece_sn") == "H-C035-527-5"

    # records 落库: 默认排除纠错删掉的码
    recs = requests.get(
        f"{api_url}/api/v1/scan-collect/records?group_id={gid}",
        timeout=5).json()
    assert len(recs) == 4
    assert all(r["group_result"] == "ok" and r["workpiece_id"] for r in recs)
    with_deleted = requests.get(
        f"{api_url}/api/v1/scan-collect/records?group_id={gid}&include_deleted=true",
        timeout=5).json()
    assert len(with_deleted) == 5  # 纠错删掉的芯子码审计留痕

    # 确认单 7.4: 结算后上组码列表保留显示 (完整面板形态, 借 kiosk 单工位视图验证)
    page.goto(f"{base_url}/#/monitor?kiosk=1&channel={ch}",
              wait_until="domcontentloaded")
    expect(page.get_by_test_id("scan-last-settled")).to_be_visible(timeout=15000)
    retained = page.get_by_test_id("scan-last-codes")
    expect(retained).to_be_visible(timeout=8000)
    assert "H-C035-527-5" in retained.inner_text()

    # 确认单 7.5: MES 工件追溯详情反查组件码
    page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded")
    page.get_by_role("button", name="工件追溯").click()
    # 多工位下默认"仅当前项目"过滤按最后激活项目走, 直接搜工件码
    page.get_by_placeholder("搜索序列号/条码...").fill("H-C035-527-5")
    page.keyboard.press("Enter")
    row = page.locator(".el-table__body tr", has_text="H-C035-527-5").first
    expect(row).to_be_visible(timeout=8000)
    row.click()
    codes_block = page.get_by_test_id("wp-scan-codes")
    expect(codes_block).to_be_visible(timeout=8000)
    text = codes_block.inner_text()
    for code in ("M010200519A100005036272608310061", "9260000144908", "9260000146312"):
        assert code in text, f"组件码 {code} 未出现在追溯详情"
    assert "9260000145631" not in text  # 纠错删掉的码不进追溯


def test_kiosk只读无纠错按钮(page, base_url, api_url, sc_project):
    _configure_via_ui(page, base_url, sc_project["name"])
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded")
    _panel, ch = _find_panel_channel(page)
    _scan(api_url, "M010200519A100005036272608310061", ch)

    page.goto(f"{base_url}/#/monitor?kiosk=1&channel={ch}",
              wait_until="domcontentloaded")
    kpanel = page.get_by_test_id("scan-slots-panel").first
    expect(kpanel).to_be_visible(timeout=15000)
    expect(kpanel.get_by_test_id("scan-total")).to_have_text("1/4", timeout=8000)
    assert kpanel.get_by_test_id("scan-clear-btn").count() == 0
    assert kpanel.get_by_test_id("scan-code-remove").count() == 0
