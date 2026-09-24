"""Fleet Hub 电视墙无人值守批次 E2E (M7)。

调研对标 (海康轮巡/大华报警上墙/Genetec kiosk/Andon 时钟) 后补齐的 P0:
顶栏时钟、全景网格自动轮巡翻页、NG 置顶条直达工位、电视墙模式
(隐藏管理入口 + 深链 #/?tv=1)、单格放大层本体 (m3/m4/m6 已回归点击链路)。
dist 不存在 skip。
"""
from __future__ import annotations

import re
import socket
import threading
import time
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

    from tests.hub.fake_edge import create_fake_edge

    # 两节点 4+3=7 工位: 2×2 网格 (每页 4) 必出翻页 → 轮巡可验证
    edge_a = create_fake_edge(node_id="edge-tv-a", station_count=4)
    edge_b = create_fake_edge(node_id="edge-tv-b", station_count=3)
    port_a, port_b = _free_port(), _free_port()
    srv_a = _start_uvicorn(edge_a, port_a)
    srv_b = _start_uvicorn(edge_b, port_b)

    data = str(tmp_path_factory.mktemp("hub_tv_e2e"))
    old_env = {k: os.environ.get(k)
               for k in ("HUB_DATA_DIR", "HUB_ENABLE_POLLER")}
    os.environ["HUB_DATA_DIR"] = data
    os.environ["HUB_ENABLE_POLLER"] = "1"

    try:
        from hub.backend.main import create_app
        hub_app = create_app()
        hub_port = _free_port()
        hub_server = _start_uvicorn(hub_app, hub_port)

        yield {"hub_url": f"http://127.0.0.1:{hub_port}",
               "edge_a": edge_a,
               "urls": [f"http://127.0.0.1:{port_a}",
                        f"http://127.0.0.1:{port_b}"]}
        hub_server.should_exit = True
        srv_a.should_exit = True
        srv_b.should_exit = True
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _login(page, hub_url: str):
    page.goto(f"{hub_url}/#/login", wait_until="domcontentloaded",
              timeout=15000)
    page.wait_for_selector('[data-test="login-submit"]', timeout=8000)
    page.fill('[data-test="login-username"]', "admin")
    page.fill('[data-test="login-password"]', "admin123")
    page.click('[data-test="login-submit"]')


def _enroll(page, name: str, url: str):
    page.click('[data-test="enroll-open"]')
    page.fill('[data-test="enroll-name"]', name)
    page.fill('[data-test="enroll-url"]', url)
    page.fill('[data-test="enroll-key"]', "tk_fake")
    page.click('[data-test="enroll-submit"]')
    page.wait_for_selector('[data-test="enroll-dialog"]', state="detached",
                           timeout=10000)


def test_tv_wall_unattended(page, stack):
    hub_url = stack["hub_url"]
    _login(page, hub_url)
    page.wait_for_selector('[data-test="wall-empty"]', timeout=8000)
    _enroll(page, "边缘TA", stack["urls"][0])
    _enroll(page, "边缘TB", stack["urls"][1])
    page.wait_for_function(
        """() => document.querySelectorAll(
            '[data-test^="station-tile-"]').length === 7""", timeout=15000)

    # ---- 顶栏时钟走秒 ----
    clock = page.inner_text('[data-test="wall-clock"]')
    assert re.search(r"\d{2}:\d{2}:\d{2}", clock), f"时钟未渲染: {clock}"

    # ---- 全景网格 2×2 → 有翻页 → 开轮巡 (5s) 自动翻页 ----
    page.click('[data-test="view-grid"]')
    page.click('[data-test="grid-2"]')
    page.wait_for_selector('[data-test="grid-pager"]', timeout=5000)
    page_info = page.inner_text('[data-test="grid-page-info"]')
    assert page_info.strip().startswith("1")

    page.click('[data-test="cycle-toggle"]')
    page.select_option('[data-test="cycle-sec"]', "5")
    page.wait_for_function(
        """() => !document.querySelector(
            '[data-test="grid-page-info"]').innerText.trim().startsWith('1')""",
        timeout=9000)   # 5s 间隔 + 余量: 自动翻到第 2 页
    page.click('[data-test="cycle-toggle"]')   # 关轮巡, 后续断言不被翻走

    # ---- 单格放大层本体: 标题/提帧画面/Esc 退出 ----
    page.click('[data-test="grid-3"]')
    tiles = page.locator('[data-test="flat-grid"] [data-test^="station-tile-"]')
    tiles.first.click()
    page.wait_for_selector('[data-test="zoom-overlay"]', timeout=5000)
    assert page.inner_text('[data-test="zoom-title"]').strip()
    page.keyboard.press("Escape")
    page.wait_for_selector('[data-test="zoom-overlay"]', state="detached",
                           timeout=5000)

    # ---- NG 置顶条: 注入未确认事件 → 甩眼条出现 → 点击直达工位 ----
    stack["edge_a"].state.edge["cycle_events"].append({
        "id": 1, "kind": "cycle", "channel_id": 1, "result": "NG",
        "event_name": "NG事件", "reason": "缺少步骤: 锁付A2",
        "ts": "2026-09-22T14:30:00"})
    page.wait_for_selector('[data-test="ng-ticker"]', timeout=15000)
    item = page.locator('[data-test="ticker-item"]').first
    assert "锁付A2" in item.inner_text()
    item.click()
    page.wait_for_selector('[data-test="station-panel"]', timeout=8000)
    page.click('[data-test="back-to-wall"]')
    page.wait_for_selector('[data-test="wall-totals"]', timeout=8000)

    # ---- 电视墙模式: 管理入口隐藏, 时钟放大, 未处理徽标保留 ----
    page.click('[data-test="tv-toggle"]')
    page.wait_for_selector('[data-test="data-link"]', state="detached",
                           timeout=5000)
    assert page.locator('[data-test="enroll-open"]').count() == 0
    assert "big" in page.get_attribute('[data-test="wall-clock"]', "class")
    assert "未处理" in page.inner_text('[data-test="alarm-badge"]')

    out = Path(__file__).resolve().parents[2] / "test-results"
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / "hub_m7_tvwall.png"), full_page=True)

    # 退出电视墙: 管理入口回归
    page.click('[data-test="tv-toggle"]')
    page.wait_for_selector('[data-test="data-link"]', timeout=5000)

    # ---- 深链 #/?tv=1 直接进墙态 (开机自启 kiosk 场景) ----
    page.goto(f"{hub_url}/#/?tv=1", wait_until="domcontentloaded",
              timeout=15000)
    page.wait_for_selector('[data-test="wall-clock"]', timeout=8000)
    assert page.locator('[data-test="data-link"]').count() == 0
    assert "big" in page.get_attribute('[data-test="wall-clock"]', "class")
