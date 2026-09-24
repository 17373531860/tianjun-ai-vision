"""Fleet Hub M9 批量操作 E2E (RFC §4.3 P1「批量切项目」+ 批量启停扩展)。

架构断言点: 批量 = 前端编排逐台调用现有唯一写网关 (零新写路径),
切项目按项目名跨机匹配 (各边缘 project_id 空间独立)。dist 不存在 skip。
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

    edge_a = create_fake_edge(node_id="edge-bt-a", station_count=2)
    edge_b = create_fake_edge(node_id="edge-bt-b", station_count=1)
    port_a, port_b = _free_port(), _free_port()
    srv_a = _start_uvicorn(edge_a, port_a)
    srv_b = _start_uvicorn(edge_b, port_b)

    data = str(tmp_path_factory.mktemp("hub_batch_e2e"))
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
               "edges": [edge_a, edge_b],
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
    page.wait_for_selector(f'text={name}', timeout=10000)


def _wait_all_online(hub_url: str, n: int, timeout: float = 15.0):
    """纳管后节点要等首轮轮询才转 online, 批量弹窗只放行在线节点。"""
    import requests
    tok = requests.post(f"{hub_url}/api/v1/auth/login", json={
        "username": "admin", "password": "admin123"}).json()["token"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        wall = requests.get(f"{hub_url}/api/v1/wall",
                            headers={"Authorization": f"Bearer {tok}"}).json()
        nodes = wall.get("nodes", [])
        if len(nodes) >= n and all(x["status"] == "online" for x in nodes):
            return
        time.sleep(0.3)
    pytest.fail("节点未在时限内全部转 online")


def test_batch_start_and_switch_project(page, stack):
    hub_url = stack["hub_url"]
    edge_a, edge_b = stack["edges"]

    _login(page, hub_url)
    page.wait_for_selector('[data-test="wall-empty"]', timeout=8000)
    _enroll(page, "批量甲", stack["urls"][0])
    _enroll(page, "批量乙", stack["urls"][1])
    page.wait_for_selector('[data-test="station-tile-2-0"]', timeout=10000)
    _wait_all_online(hub_url, 2)
    page.wait_for_timeout(1200)   # 等墙前端下一轮刷新拿到 online 状态

    # ---- 批量开始检测: 全选在线 → 预览 3 工位 → 执行 → 全 ✓ ----
    assert edge_a.state.edge["detecting"] == {0: False, 1: False}
    page.click('[data-test="batch-open"]')
    page.wait_for_selector('[data-test="batch-dialog"]', timeout=5000)
    page.click('[data-test="batch-all"]')
    page.wait_for_selector('[data-test="batch-row-2"]', timeout=5000)
    rows = page.locator('[data-test^="batch-row-"]')
    assert rows.count() == 3   # 甲 2 工位 + 乙 1 工位
    page.click('[data-test="batch-go"]')
    page.wait_for_selector('[data-test="batch-summary"]', timeout=15000)
    assert "3 成功" in page.inner_text('[data-test="batch-summary"]')
    assert edge_a.state.edge["detecting"] == {0: True, 1: True}
    assert edge_b.state.edge["detecting"] == {0: True}
    page.click('[data-test="batch-close"]')

    # ---- 批量切项目 (按名跨机匹配): 两台机都切到 包装检测B ----
    assert edge_a.state.edge["active_project_id"] == 1
    page.click('[data-test="batch-open"]')
    page.wait_for_selector('[data-test="batch-dialog"]', timeout=5000)
    page.select_option('[data-test="batch-action"]', "activate_project")
    page.click('[data-test="batch-all"]')
    # 节点级动作: 每机一行
    page.wait_for_selector('[data-test="batch-row-1"]', timeout=5000)
    assert page.locator('[data-test^="batch-row-"]').count() == 2
    page.fill('[data-test="batch-project"]', "包装检测B")
    page.click('[data-test="batch-go"]')
    page.wait_for_selector('[data-test="batch-summary"]', timeout=15000)
    assert "2 成功" in page.inner_text('[data-test="batch-summary"]')
    assert edge_a.state.edge["active_project_id"] == 2
    assert edge_b.state.edge["active_project_id"] == 2
    page.click('[data-test="batch-close"]')

    # ---- 无同名项目: 失败原因逐行可见, 边缘不受影响 ----
    page.click('[data-test="batch-open"]')
    page.select_option('[data-test="batch-action"]', "activate_project")
    page.click('[data-test="batch-all"]')
    page.fill('[data-test="batch-project"]', "不存在的项目")
    page.wait_for_selector('[data-test="batch-row-1"]', timeout=5000)
    page.click('[data-test="batch-go"]')
    page.wait_for_selector('[data-test="batch-summary"]', timeout=15000)
    assert "2 失败" in page.inner_text('[data-test="batch-summary"]')
    assert "本机无同名项目" in page.inner_text('[data-test="batch-plan"]')
    assert edge_a.state.edge["active_project_id"] == 2   # 未被误切

    out = Path(__file__).resolve().parents[2] / "test-results"
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / "hub_m9_batch_ops.png"), full_page=True)
