"""检测主页分区字号回归：真实 Vue 页面，业务 API 全拦截，不访问生产数据。"""
from collections import Counter
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect


MODULES = ("videoHud", "sop", "stats", "charts", "stepTable", "sessionBar", "controls", "mesBar")
LABELS = ["检查正面", "检查背面", "不良贴标"]
REORDERED = [LABELS[2], LABELS[0], LABELS[1]]


@pytest.fixture(autouse=True)
def cleanup_e2e_resources():
    # 覆盖父目录真实 CRUD fixture；所有接口只返回本模块确定性数据。
    yield


def _project(ident=1, reordered=False, labels=None):
    labels = labels or LABELS
    return {
        "id": ident, "project_id": ident, "name": f"__e2e_module_fonts_{ident}",
        "project_name": f"__e2e_module_fonts_{ident}", "is_active": ident == 1,
        "logic_mode": "region_events" if reordered else "sequential",
        # 原始 id 故意不从 1 起，证明卡片编号来自当前展示顺序。
        "steps_config": [{"id": 20 + i, "label": label, "enabled": True} for i, label in enumerate(labels)],
        "pipeline_config": {"region_events": {
            "rules": [{"id": 70 + i, "name": label} for i, label in enumerate(labels)],
            "sequence_check": {"enabled": True, "display_order": REORDERED},
        }} if reordered else {},
        "events_config": [], "counters_config": [
            {"name": name, "value": value} for name, value in (("总产量", 10), ("合格总数", 8), ("不良总数", 2), ("NG步骤", 2))
        ], "detection_config": {},
    }


def _install_routes(page, count=1, reordered=False, scales=None, labels=None):
    labels = labels or LABELS
    projects = [_project(i + 1, reordered, labels) for i in range(count)]
    states = [{
        "is_running": True, "is_detecting": True, "source_type": "camera",
        "project_config": proj, "detections": [], "current_cycle_steps": labels,
        "current_cycle_id": 1, "current_cycle_uuid": "module-font-cycle",
        "counters": {"总产量": 10, "合格总数": 8, "不良总数": 2},
        "step_counts": dict(Counter(labels)), "step_screenshots": {}, "recent_events": [],
        "cycle_sum_step_durations": {label: 0.8 for label in labels},
        "region_events": {"rules": [{"name": label} for label in labels]},
    } for proj in projects]
    intercepted = []

    def handle(route):
        intercepted.append((route.request.method, route.request.url))
        parsed = urlparse(route.request.url)
        path = parsed.path.split("/api/v1/", 1)[-1].rstrip("/")
        if path == "source/detection/results":
            ch = int(parse_qs(parsed.query).get("channel", [0])[0])
            body = states[ch]
        elif path == "projects":
            body = {"items": projects, "total": len(projects)}
        elif path.startswith("projects/"):
            part = path.split("/")[1]
            body = next((p for p in projects if str(p["id"]) == part), projects[0])
        elif path == "workstations":
            body = {"channel_count": count, "source_configs": {
                str(ch): {"project_id": projects[ch]["id"]} for ch in range(count)
            }}
        elif path == "source/status":
            body = {"is_running": True, "is_detecting": True, "source_type": "camera", "model_loaded": True}
        elif path.startswith("auth/"):
            body = {"auth_enabled": False, "user": {"is_superuser": True, "permissions": ["*"]}}
        elif path in ("scanner/devices", "mes/inbound/active-alarms", "triggers", "plugins/active"):
            body = []
        else:
            body = {}
        route.fulfill(status=200, json=body)

    page.route("**/api/v1/**", handle)
    page.route("**/video_feed*", lambda route: route.abort())
    page.route("**/snapshot*", lambda route: route.abort())
    # 防止页面 WS 脱离 HTTP mock 与真实后端建立连接。
    if hasattr(page, "route_web_socket"):
        page.route_web_socket("**/ws*", lambda ws: ws.close())
    page.set_viewport_size({"width": 1920, "height": 1080})
    monitor = {"showBypassSn": True}
    if scales is not None:
        monitor["moduleFontScale"] = scales
    page.add_init_script(f"""if (!localStorage.getItem('display_settings')) {{
        localStorage.setItem('display_settings', {json.dumps(json.dumps({'monitor': monitor}))});
    }}""")
    return intercepted


def _shot(page, name):
    directory = os.environ.get("E2E_SCREENSHOT_DIR")
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(directory) / f"{name}.png"))


def _navigate(page, route):
    page.evaluate("route => { window.location.hash = route }", route)


def _set_slider(page, key, value):
    slider = page.get_by_test_id(f"module-font-slider-{key}").get_by_role("slider")
    slider.scroll_into_view_if_needed()
    slider.focus()
    current = int(slider.get_attribute("aria-valuenow"))
    direction = "ArrowRight" if value > current else "ArrowLeft"
    for _ in range(abs(value - current) // 10):
        slider.press(direction)
    expect(page.get_by_test_id(f"module-font-value-{key}")).to_have_text(f"{value}%")
    expect(slider).to_have_attribute("aria-valuenow", str(value))


def _metrics(page):
    return page.evaluate("""() => {
      const selectors = {
        videoHud: '[data-font-module="videoHud"]',
        sop: '[data-font-module="sop"] .text-lg',
        stats: '[data-font-module="stats"] .text-sm',
        charts: '[data-font-module="charts"] h3',
        stepTable: '[data-font-module="stepTable"] table',
        sessionBar: '[data-font-module="sessionBar"] input',
        controls: '[data-font-module="controls"] button',
        mesBar: '[data-font-module="mesBar"]',
      };
      return Object.fromEntries(Object.entries(selectors).map(([key, selector]) => {
        const el = document.querySelector(selector);
        return [key, el ? parseFloat(getComputedStyle(el).fontSize) : null];
      }));
    }""")


def _chart_fonts(page, gauge_id="primary-yield-rate-gauge", pie_id="primary-good-bad-chart"):
    return page.evaluate("""async ids => {
      const entry = performance.getEntriesByType('resource').find(e => /\/deps\/echarts\.js/.test(e.name));
      const echarts = await import(entry.name);
      const gauge = echarts.getInstanceByDom(document.querySelector(`[data-testid="${ids[0]}"]`));
      const pie = echarts.getInstanceByDom(document.querySelector(`[data-testid="${ids[1]}"]`));
      return { gauge: gauge.getOption().series[0].detail.fontSize, pie: pie.getOption().textStyle.fontSize };
    }""", [gauge_id, pie_id])


def _assert_default_matches_existing_tailwind(page):
    differences = page.evaluate("""() => {
      const roots = [...document.querySelectorAll('.tj-monitor-module')];
      const nodes = [...new Set(roots.flatMap(root => [root, ...root.querySelectorAll('*')]))]
        .filter(el => el.tagName === 'INPUT' || [...el.childNodes].some(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim()));
      const measure = el => {
        const css = getComputedStyle(el);
        return {fontSize: css.fontSize, lineHeight: css.lineHeight};
      };
      const before = nodes.map(measure);
      roots.forEach(el => el.classList.remove('tj-monitor-module'));
      try {
        return nodes.flatMap((el, index) => {
          const after = measure(el);
          return JSON.stringify(before[index]) === JSON.stringify(after) ? [] : [{
            text: el.textContent.trim().slice(0, 50), className: el.className,
            before: before[index], oldTailwind: after,
          }];
        });
      } finally {
        roots.forEach(el => el.classList.add('tj-monitor-module'));
      }
    }""")
    assert differences == [], differences


def _assert_numbered_cards(cards, labels):
    expect(cards).to_have_count(len(labels), timeout=15000)
    for index, label in enumerate(labels):
        # 名字仍可独立 has_text 命中，编号节点不收缩，类别名继续截断。
        header = cards.nth(index).locator("div").first
        expect(cards.filter(has_text=label)).to_have_count(1)
        expect(header).to_have_text(f"{index + 1} {label}", use_inner_text=True)
        expect(header.locator(".flex-shrink-0")).to_have_text(str(index + 1))
        expect(header.locator(".truncate")).to_have_text(label)


def test_settings_sliders_persist_reset_and_module_isolation(page, base_url):
    intercepted = _install_routes(page)
    page.goto(f"{base_url}/#/monitor")
    expect(page.get_by_test_id("sop-step-panel").locator("div.w-32")).to_have_count(3, timeout=15000)
    _assert_default_matches_existing_tailwind(page)
    baseline = _metrics(page)
    assert all(value is not None for value in baseline.values()), baseline
    root_font = page.locator("html").evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
    # 100% 保留既有 rem 计算值；不会改动 html 根字号。
    assert baseline["sop"] == pytest.approx(root_font * 1.125)
    assert baseline["stats"] == pytest.approx(root_font * 0.875)
    assert baseline["charts"] == pytest.approx(root_font)
    assert baseline["stepTable"] == pytest.approx(root_font * 0.875)
    assert baseline["controls"] == pytest.approx(root_font * 1.125)
    assert _chart_fonts(page) == {"gauge": 24, "pie": 12}
    _shot(page, "module-fonts-single-default")

    _navigate(page, "/settings")
    block = page.get_by_test_id("monitor-module-font-settings")
    expect(block).to_be_visible(timeout=15000)
    expect(block.get_by_role("slider")).to_have_count(8)
    for key in MODULES:
        slider = page.get_by_test_id(f"module-font-slider-{key}").get_by_role("slider")
        expect(slider).to_have_attribute("aria-valuemin", "80")
        expect(slider).to_have_attribute("aria-valuemax", "200")
        _set_slider(page, key, 200)
    saved = page.evaluate("() => JSON.parse(localStorage.getItem('display_settings')).monitor.moduleFontScale")
    assert saved == dict.fromkeys(MODULES, 200)
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("module-font-value-sop")).to_have_text("200%")
    page.get_by_test_id("monitor-module-font-reset").click()
    assert page.evaluate("() => JSON.parse(localStorage.getItem('display_settings')).monitor.moduleFontScale") == dict.fromkeys(MODULES, 100)
    _shot(page, "module-fonts-settings-eight-sliders")

    for key in MODULES:
        _set_slider(page, key, 160)
        _navigate(page, "/monitor")
        expect(page.get_by_test_id("sop-step-panel").locator("div.w-32")).to_have_count(3, timeout=15000)
        metrics = _metrics(page)
        for other in MODULES:
            assert metrics[other] == pytest.approx(baseline[other] * (1.6 if other == key else 1)), (key, other, metrics, baseline)
        assert page.locator("html").evaluate("e => parseFloat(getComputedStyle(e).fontSize)") == root_font
        expected_charts = {"gauge": 38.4, "pie": 19.2} if key == "charts" else {"gauge": 24, "pie": 12}
        chart_fonts = _chart_fonts(page)
        for chart_name, font in expected_charts.items():
            assert chart_fonts[chart_name] == pytest.approx(font)
        _shot(page, f"module-fonts-single-only-{key}-160")
        _navigate(page, "/settings")
        expect(page.get_by_test_id("monitor-module-font-settings")).to_be_visible(timeout=15000)
        _set_slider(page, key, 100)
    # 确认设置保存的全部写操作仍被 mock 截断，没有请求穿透到真实 API。
    assert intercepted


@pytest.mark.parametrize("count,query,variant", [
    (1, "", "single"), (2, "", "dual"), (3, "", "triple"),
    (4, "?station_view=1&channel=0&readonly=0", "zoom"),
    (4, "?kiosk=1&channel=0", "kiosk"),
])
def test_numbering_display_order_and_sop_scale_in_all_forms(page, base_url, count, query, variant):
    _install_routes(page, count, reordered=True, scales={"sop": 160})
    page.goto(f"{base_url}/#/monitor{query}")
    if variant in ("single", "zoom", "kiosk"):
        panel = page.get_by_test_id("sop-step-panel")
        cards = panel.locator("div.w-32")
        table = page.locator("[data-layout-slot='step-table'] table") if variant == "single" else page.get_by_test_id("single-channel-step-table").locator("table")
        expected_rem = 0.75
    else:
        panel = page.get_by_test_id(f"{variant}-sop-0")
        cards = panel.locator("div.w-28")
        table = panel.locator("table") if variant == "dual" else page.get_by_test_id("triple-steptable-0").locator("table")
        expected_rem = 0.75 if variant == "dual" else 0.625
    _assert_numbered_cards(cards, REORDERED)
    expect(table.locator("tbody tr")).to_have_count(3)
    for index, label in enumerate(REORDERED):
        cells = table.locator("tbody tr").nth(index).locator("td")
        # 双工位旧紧凑表本身只有步骤/状态两列，保持现有布局契约。
        expect(cells.nth(0 if variant == "dual" else 1)).to_have_text(label)
        if variant != "dual":
            expect(cells.first).to_have_text(str(index + 1))
    root_font = page.locator("html").evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
    header_font = cards.first.locator("div").first.evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
    assert header_font == pytest.approx(root_font * expected_rem * 1.6)
    table_scale = table.evaluate("e => getComputedStyle(e.closest('[data-font-module]')).getPropertyValue('--tj-module-scale').trim()")
    assert float(table_scale) == 1
    _shot(page, f"module-fonts-{variant}-reordered-sop-160")


def test_sop_200_two_digit_numbers_keep_long_names_truncated(page, base_url):
    labels = [f"检查超长类别名称用于验证截断第{i + 1}项" for i in range(12)]
    _install_routes(page, scales={"sop": 200}, labels=labels)
    page.goto(f"{base_url}/#/monitor")
    cards = page.get_by_test_id("sop-step-panel").locator("div.w-32")
    _assert_numbered_cards(cards, labels)
    cards.nth(11).scroll_into_view_if_needed()
    sizes = cards.nth(11).evaluate("""card => {
      const header = card.firstElementChild;
      const number = header.firstElementChild;
      const name = header.lastElementChild;
      return {card: card.clientWidth, header: header.clientWidth, height: header.clientHeight,
        number: number.clientWidth, name: name.clientWidth, nameFull: name.scrollWidth,
        textOverflow: getComputedStyle(name).textOverflow,
        whiteSpace: getComputedStyle(name).whiteSpace};
    }""")
    assert sizes["header"] <= sizes["card"]
    assert sizes["number"] > 0 and sizes["name"] > 0
    assert sizes["nameFull"] > sizes["name"]
    assert sizes["textOverflow"] == "ellipsis" and sizes["whiteSpace"] == "nowrap"
    _shot(page, "module-fonts-sop-200-two-digit-long-name")
