"""Fleet Hub 检测集群全景网格 E2E。

改名验收 (监控墙 → 检测集群) + 双视图切换: 按节点分组卡 / 全景网格
(跨节点摊平, N×N=每页 N² 格与主程序超多工位语义对齐, 超出翻页),
瓦片可点进工位下钻, 模式与规格 localStorage 记忆。dist 不存在 skip。
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

    # 两节点 4+3=7 工位: 2×2 网格 (每页 4) 必出翻页
    edge_a = create_fake_edge(node_id="edge-grid-a", station_count=4)
    edge_b = create_fake_edge(node_id="edge-grid-b", station_count=3)
    port_a, port_b = _free_port(), _free_port()
    srv_a = _start_uvicorn(edge_a, port_a)
    srv_b = _start_uvicorn(edge_b, port_b)

    data = str(tmp_path_factory.mktemp("hub_grid_e2e"))
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
    page.goto(f"{hub_url}/#/login", wait_until="domcontentloaded", timeout=15000)
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


def test_cluster_grid_view(page, stack):
    hub_url = stack["hub_url"]
    _login(page, hub_url)
    page.wait_for_selector('[data-test="wall-empty"]', timeout=8000)

    # 改名验收: 顶栏是"检测集群", 不再是"监控墙"
    assert "检测集群" in page.inner_text("header.hub-topbar")
    assert "监控墙" not in page.inner_text("header.hub-topbar")

    _enroll(page, "网格测试机A", stack["urls"][0])
    _enroll(page, "网格测试机B", stack["urls"][1])
    page.wait_for_function(
        """() => document.querySelectorAll(
            '[data-test^="station-tile-"]').length === 7""", timeout=15000)

    # ---- 默认: 按节点分组卡 ----
    assert page.locator('[data-test^="group-card-"]').count() == 2
    assert page.locator('[data-test="flat-grid"]').count() == 0

    # ---- 切全景网格: 7 瓦片摊平 + 瓦片带节点名 ----
    page.click('[data-test="view-grid"]')
    page.wait_for_selector('[data-test="flat-grid"]', timeout=5000)
    assert page.locator('[data-test^="group-card-"]').count() == 0
    tiles = page.locator('[data-test="flat-grid"] [data-test^="station-tile-"]')
    # 默认 3×3=9/页 ≥ 7 → 全部一页, 无翻页控件
    assert tiles.count() == 7
    assert page.locator('[data-test="grid-pager"]').count() == 0
    assert "网格测试机A" in tiles.first.inner_text()

    # ---- 2×2: 每页 4 格 → 2 页翻页 ----
    page.click('[data-test="grid-2"]')
    page.wait_for_function(
        """() => document.querySelectorAll(
            '[data-test="flat-grid"] [data-test^="station-tile-"]')
            .length === 4""", timeout=5000)
    assert page.inner_text('[data-test="grid-page-info"]').strip() == "1 / 2"
    page.click('[data-test="grid-next"]')
    page.wait_for_function(
        """() => document.querySelectorAll(
            '[data-test="flat-grid"] [data-test^="station-tile-"]')
            .length === 3""", timeout=5000)
    assert page.inner_text('[data-test="grid-page-info"]').strip() == "2 / 2"

    # ---- 9×9: 一页放下, 翻页消失 ----
    page.click('[data-test="grid-9"]')
    page.wait_for_function(
        """() => document.querySelectorAll(
            '[data-test="flat-grid"] [data-test^="station-tile-"]')
            .length === 7""", timeout=5000)
    assert page.locator('[data-test="grid-pager"]').count() == 0

    out = Path(__file__).resolve().parents[2] / "test-results"
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / "hub_m6_grid.png"), full_page=True)

    # ---- 瓦片点击先放大 (Esc 可退), 放大层进工位下钻, 返回后记忆保持 ----
    tiles.first.click()
    page.wait_for_selector('[data-test="zoom-overlay"]', timeout=5000)
    page.keyboard.press("Escape")           # Esc 关放大层回墙
    page.wait_for_selector('[data-test="zoom-overlay"]', state="detached",
                           timeout=5000)
    tiles.first.click()
    page.wait_for_selector('[data-test="zoom-overlay"]', timeout=5000)
    page.click('[data-test="zoom-enter"]')
    page.wait_for_selector('[data-test="back-to-wall"]', timeout=8000)
    assert "检测集群" in page.inner_text('[data-test="back-to-wall"]')
    page.click('[data-test="back-to-wall"]')
    page.wait_for_selector('[data-test="flat-grid"]', timeout=8000)
    assert "active" in page.get_attribute('[data-test="grid-9"]', "class")

    # ---- 切回按节点分组 ----
    page.click('[data-test="view-nodes"]')
    page.wait_for_selector('[data-test^="group-card-"]', timeout=5000)
    assert page.locator('[data-test="flat-grid"]').count() == 0
