"""Fleet Hub M4 前端 CI E2E: 双边缘纳管 → 多节点墙 → 定向消警。

拓扑: 2 个 fake_edge uvicorn + 枢纽 (poller 开), Playwright 真浏览器:
登录 → 逐台纳管 → 墙上两组卡片与 totals → 下钻边缘 B → 消警 (normal 确认)
→ 断言只有 B 的报警被消、A 不受波及。dist 不存在时 skip。
"""
from __future__ import annotations

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

    edges = []
    for i in (1, 2):
        app = create_fake_edge(node_id=f"edge-m4e2e-{i:02d}")
        port = _free_port()
        server = _start_uvicorn(app, port)
        edges.append({"app": app, "port": port, "server": server,
                      "url": f"http://127.0.0.1:{port}"})

    data = str(tmp_path_factory.mktemp("hub_m4_e2e"))
    old_env = {k: os.environ.get(k)
               for k in ("HUB_DATA_DIR", "HUB_ENABLE_POLLER")}
    os.environ["HUB_DATA_DIR"] = data
    os.environ["HUB_ENABLE_POLLER"] = "1"

    try:
        from hub.backend.main import create_app
        hub_app = create_app()
        hub_port = _free_port()
        hub_server = _start_uvicorn(hub_app, hub_port)

        yield {"hub_url": f"http://127.0.0.1:{hub_port}", "edges": edges}
        hub_server.should_exit = True
        for e in edges:
            e["server"].should_exit = True
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _enroll(page, name: str, url: str):
    page.click('[data-test="enroll-open"]')
    page.wait_for_selector('[data-test="enroll-dialog"]', timeout=5000)
    page.fill('[data-test="enroll-name"]', name)
    page.fill('[data-test="enroll-url"]', url)
    page.fill('[data-test="enroll-key"]', "tk_fake")
    page.click('[data-test="enroll-submit"]')
    page.wait_for_selector('[data-test="enroll-dialog"]', state="detached",
                           timeout=10000)


def test_m4_multi_edge_wall_and_ack(page, stack):
    hub_url = stack["hub_url"]
    edge_a, edge_b = stack["edges"]

    # ---- 登录 ----
    page.goto(f"{hub_url}/#/login", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector('[data-test="login-submit"]', timeout=8000)
    page.fill('[data-test="login-username"]', "admin")
    page.fill('[data-test="login-password"]', "admin123")
    page.click('[data-test="login-submit"]')
    page.wait_for_selector('[data-test="wall-empty"]', timeout=8000)

    # ---- 纳管两台 ----
    _enroll(page, "边缘A", edge_a["url"])
    page.wait_for_selector('[data-test^="station-tile-"]', timeout=10000)
    _enroll(page, "边缘B", edge_b["url"])
    page.wait_for_selector('[data-test="group-card-边缘B"]', timeout=10000)

    # 墙: 两组卡片 + totals 含 2 节点 2 工位
    assert page.locator('[data-test="group-card-边缘A"]').count() == 1
    assert page.locator('[data-test="group-card-边缘B"]').count() == 1
    totals = page.inner_text('[data-test="wall-totals"]')
    assert "2" in totals

    # ---- 下钻边缘 B 的工位, 定向消警 ----
    tiles = page.locator(
        '[data-test="group-card-边缘B"] [data-test^="station-tile-"]')
    assert tiles.count() == 1
    tiles.first.click()
    page.wait_for_selector('[data-test="station-panel"]', timeout=8000)
    page.wait_for_selector('[data-test="op-ack_alarm"]:not([disabled])',
                           timeout=10000)

    assert edge_b["app"].state.edge["alarm_active"][0] is True
    page.click('[data-test="op-ack_alarm"]')
    page.wait_for_selector('[data-test="confirm-dialog"]', timeout=5000)
    page.click('[data-test="confirm-go"]')
    page.wait_for_selector('[data-test="op-notice"]', timeout=8000)

    assert edge_b["app"].state.edge["alarm_active"][0] is False, "B 应被消警"
    assert edge_a["app"].state.edge["alarm_active"][0] is True, "A 不应被波及"

    # 留证
    out = Path(__file__).resolve().parents[2] / "test-results"
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / "hub_m4_multi_edge.png"), full_page=True)