"""Fleet Hub M5 前端 CI E2E: 数据中心三 Tab。

拓扑: 1 个 fake_edge uvicorn + 枢纽 (poller 开, 拉取/重算节奏调到 1s),
Playwright 真浏览器: 登录 → 纳管 → 注入周期流 (OK+NG) → 数据中心 KPI/
趋势/工位排行 → NG 分析 Pareto/交叉表/原因筛选样本 → 周期明细过滤 →
预设切换空态。dist 不存在时 skip。
"""
from __future__ import annotations

import socket
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parents[2] / "hub" / "frontend" / "dist"

pytestmark = pytest.mark.skipif(
    not DIST.is_dir(), reason="hub/frontend/dist 未构建")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_uvicorn(app, port: int):
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return server
        except OSError:
            time.sleep(0.1)
    pytest.fail(f"uvicorn :{port} 未起来")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    import os

    import hub.backend.poller as poller_mod
    from tests.hub.fake_edge import create_fake_edge

    edge_app = create_fake_edge(node_id="edge-m5-e2e-01", station_count=2)
    edge_port = _free_port()
    edge_server = _start_uvicorn(edge_app, edge_port)

    data = str(tmp_path_factory.mktemp("hub_m5_e2e"))
    old_env = {k: os.environ.get(k)
               for k in ("HUB_DATA_DIR", "HUB_ENABLE_POLLER")}
    os.environ["HUB_DATA_DIR"] = data
    os.environ["HUB_ENABLE_POLLER"] = "1"
    # 节奏调快: config 常量已被 poller from-import, 直接改 poller 命名空间
    old_speed = (poller_mod.CYCLE_PULL_INTERVAL_S, poller_mod.ROLLUP_INTERVAL_S)
    poller_mod.CYCLE_PULL_INTERVAL_S = 1.0
    poller_mod.ROLLUP_INTERVAL_S = 1.0

    try:
        from hub.backend.main import create_app
        hub_app = create_app()
        hub_port = _free_port()
        hub_server = _start_uvicorn(hub_app, hub_port)

        yield {"hub_url": f"http://127.0.0.1:{hub_port}",
               "edge": {"app": edge_app, "url": f"http://127.0.0.1:{edge_port}"}}
        hub_server.should_exit = True
        edge_server.should_exit = True
    finally:
        poller_mod.CYCLE_PULL_INTERVAL_S, poller_mod.ROLLUP_INTERVAL_S = old_speed
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _login(page, hub_url: str, username: str, password: str):
    page.goto(f"{hub_url}/#/login", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector('[data-test="login-submit"]', timeout=8000)
    page.fill('[data-test="login-username"]', username)
    page.fill('[data-test="login-password"]', password)
    page.click('[data-test="login-submit"]')


def _ev(eid, result="OK", ch=0, reason=None, event_name=None,
        duration_ms=2000):
    return {"id": eid, "kind": "cycle", "channel_id": ch, "result": result,
            "event_name": event_name, "reason": reason,
            "ts": datetime.now().isoformat(),
            "duration_ms": duration_ms, "project_id": 1,
            "project_name": "装配检测A"}


def test_data_center_three_tabs(page, stack):
    hub_url = stack["hub_url"]
    edge = stack["edge"]

    # ---- 登录 + 纳管 (首拉"从现在订阅"落底游标, 之后注入的才被拉走) ----
    _login(page, hub_url, "admin", "admin123")
    page.wait_for_selector('[data-test="wall-empty"]', timeout=8000)
    page.click('[data-test="enroll-open"]')
    page.fill('[data-test="enroll-name"]', "统计演示机")
    page.fill('[data-test="enroll-url"]', edge["url"])
    page.fill('[data-test="enroll-key"]', "tk_fake")
    page.click('[data-test="enroll-submit"]')
    page.wait_for_selector('[data-test^="station-tile-"]', timeout=10000)
    time.sleep(2.5)   # 等首拉落底 (1s 节奏 ×2 保险)

    # ---- 注入周期流: 工位0 = 4 OK + 1 NG(缺步骤); 工位1 = 2 NG ----
    edge["app"].state.edge["cycle_events"] += [
        _ev(1, "OK", ch=0), _ev(2, "OK", ch=0), _ev(3, "OK", ch=0),
        _ev(4, "OK", ch=0, duration_ms=4000),
        _ev(5, "NG", ch=0, reason="缺步骤: 拧紧螺丝"),
        _ev(6, "NG", ch=1, reason="扫码失败"),
        _ev(7, "NG", ch=1, reason="扫码失败"),
    ]

    # ---- 数据中心: KPI 就绪 (拉取 1s + 重算 1s, 前端 60s 慢刷 → 主动等) ----
    page.click('[data-test="data-link"]')
    page.wait_for_selector('[data-test="kpi-cards"]', timeout=10000)
    deadline = time.time() + 20
    toggle = True
    while time.time() < deadline:
        if page.inner_text('[data-test="kpi-total"]') == "7":
            break
        # 交替切预设触发重拉 (watch 只对值变化响应; 两窗口都含今天数据)
        page.click('[data-test="preset-7d"]' if toggle
                   else '[data-test="preset-today"]')
        toggle = not toggle
        time.sleep(1)
    page.click('[data-test="preset-today"]')
    time.sleep(0.5)
    assert page.inner_text('[data-test="kpi-total"]') == "7"
    assert page.inner_text('[data-test="kpi-ng"]') == "3"
    assert page.inner_text('[data-test="kpi-yield"]') == "57.1%"

    # 趋势图 canvas 渲染
    assert page.locator('[data-test="trend-chart"] canvas').count() >= 1

    # 工位排行: 最差在上 (工位1 合格率 0%)
    rows = page.locator('[data-test="stations-table"] tbody tr')
    assert rows.count() == 2
    first_row = rows.nth(0).inner_text()
    assert "0.0%" in first_row

    out = Path(__file__).resolve().parents[2] / "test-results"
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / "hub_m5_overview.png"), full_page=True)

    # ---- NG 分析: Pareto + 交叉表 + 点行筛选样本 ----
    page.click('[data-test="tab-ng"]')
    page.wait_for_selector('[data-test="ng-matrix"]', timeout=8000)
    matrix = page.inner_text('[data-test="ng-matrix"]')
    assert "扫码失败" in matrix and "缺步骤" in matrix
    samples = page.locator('[data-test="ng-samples"] tbody tr')
    assert samples.count() == 3, "未筛选时全部 NG 样本"

    # 点交叉表"扫码失败"行 → 样本表只剩 2 条
    page.locator('[data-test="ng-matrix"] tbody tr',
                 has_text="扫码失败").click()
    page.wait_for_selector('[data-test="reason-tag"]', timeout=8000)
    page.wait_for_function(
        """() => document.querySelectorAll(
            '[data-test="ng-samples"] tbody tr').length === 2""",
        timeout=8000)
    page.screenshot(path=str(out / "hub_m5_ng_analysis.png"), full_page=True)

    # 清筛选
    page.click('[data-test="reason-clear"]')

    # ---- 周期明细: 全量 7 行, result 过滤 NG → 3 行 ----
    page.click('[data-test="tab-cycles"]')
    page.wait_for_selector('[data-test="cycles-table"]', timeout=8000)
    page.wait_for_function(
        """() => document.querySelectorAll(
            '[data-test^="cy-row-"]').length === 7""", timeout=8000)
    page.select_option('[data-test="result-filter"]', "NG")
    page.wait_for_function(
        """() => document.querySelectorAll(
            '[data-test^="cy-row-"]').length === 3""", timeout=8000)
    # tab 高亮必须跟内容一致 (截图审计疑点回归)
    assert "active" in page.get_attribute('[data-test="tab-cycles"]', "class")
    assert "active" not in page.get_attribute('[data-test="tab-ng"]', "class")
    page.screenshot(path=str(out / "hub_m5_cycles.png"), full_page=True)

    # ---- 预设切换: 昨天无数据 → 空态 ----
    # "全部结果" :value=null → DOM value 回退为文本, 按 label 选
    page.select_option('[data-test="result-filter"]', label="全部结果")
    page.click('[data-test="preset-yesterday"]')
    page.wait_for_selector('[data-test="cycles-empty"]', timeout=8000)
