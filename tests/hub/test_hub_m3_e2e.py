"""Fleet Hub M3 前端 CI E2E: 纳管假边缘 → 墙 → 工位操作台 → 远程启停/切项目。

拓扑: uvicorn 起 fake_edge (契约级假边缘) + 枢纽 (poller 开启, 真轮询假边缘),
Playwright 走真浏览器: 登录 → 纳管 → 工位 tile → 下钻 → 确认框 → 执行 →
断言假边缘状态真的被改。dist 不存在时 skip。
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
    """假边缘 + 枢纽 (poller 开) 双服务栈"""
    import os

    from tests.hub.fake_edge import create_fake_edge

    edge_app = create_fake_edge()
    edge_port = _free_port()
    edge_server = _start_uvicorn(edge_app, edge_port)

    data = str(tmp_path_factory.mktemp("hub_m3_e2e"))
    old_env = {k: os.environ.get(k)
               for k in ("HUB_DATA_DIR", "HUB_ENABLE_POLLER")}
    os.environ["HUB_DATA_DIR"] = data
    os.environ["HUB_ENABLE_POLLER"] = "1"     # 真轮询假边缘 → status=online

    try:
        from hub.backend.main import create_app
        hub_app = create_app()
        hub_port = _free_port()
        hub_server = _start_uvicorn(hub_app, hub_port)

        yield {
            "hub_url": f"http://127.0.0.1:{hub_port}",
            "edge_url": f"http://127.0.0.1:{edge_port}",
            "edge_state": edge_app.state.edge,
        }
        hub_server.should_exit = True
        edge_server.should_exit = True
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_m3_full_ops_flow(page, stack):
    hub_url = stack["hub_url"]
    edge_state = stack["edge_state"]

    # ---- 登录 ----
    page.goto(f"{hub_url}/#/login", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector('[data-test="login-submit"]', timeout=8000)
    page.fill('[data-test="login-username"]', "admin")
    page.fill('[data-test="login-password"]', "admin123")
    page.click('[data-test="login-submit"]')
    page.wait_for_selector('[data-test="wall-empty"]', timeout=8000)

    # ---- 纳管假边缘 ----
    page.click('[data-test="enroll-open"]')
    page.wait_for_selector('[data-test="enroll-dialog"]', timeout=5000)
    page.fill('[data-test="enroll-name"]', "E2E假边缘")
    page.fill('[data-test="enroll-url"]', stack["edge_url"])
    page.fill('[data-test="enroll-key"]', "tk_fake_key")
    page.click('[data-test="enroll-submit"]')

    # 墙上出现工位 tile (纳管成功 + 轮询建 twin)
    page.wait_for_selector('[data-test^="station-tile-"]', timeout=10000)

    # ---- 下钻工位操作台 (点格先放大, 放大层内进入工位 —— 电视墙惯例) ----
    page.click('[data-test^="station-tile-"]')
    page.wait_for_selector('[data-test="zoom-overlay"]', timeout=5000)
    page.click('[data-test="zoom-enter"]')
    page.wait_for_selector('[data-test="station-panel"]', timeout=8000)

    # ---- 生产实况面板 (M7.5): 计数/步骤/最近事件上屏 ----
    page.wait_for_selector('[data-test="live-panel"]', timeout=8000)
    assert page.inner_text('[data-test="live-ok"]') == "128"
    assert "拧紧螺丝" in page.inner_text('[data-test="live-steps"]')
    assert "步骤缺失" in page.inner_text('[data-test="live-events"]')

    # poller 0.5s 一轮, 等 status=online 后操作按钮解禁
    page.wait_for_selector(
        '[data-test="op-stop_detection"]:not([disabled])', timeout=10000)

    # ---- 远程开始检测 (normal 确认) ----
    assert edge_state["detecting"][0] is False
    page.click('[data-test="op-start_detection"]')
    page.wait_for_selector('[data-test="confirm-dialog"]', timeout=5000)
    page.click('[data-test="confirm-go"]')
    page.wait_for_selector('[data-test="op-notice"]', timeout=8000)
    assert edge_state["detecting"][0] is True, "假边缘应真的开始检测"
    # 孪生 reported 回流: 面板状态变"检测中"
    page.wait_for_selector('[data-test="field-detecting"]:has-text("检测中")',
                           timeout=8000)

    # ---- 远程停止检测 (danger 确认 + 可取消) ----
    page.click('[data-test="op-stop_detection"]')
    page.wait_for_selector('[data-test="confirm-dialog"]', timeout=5000)
    page.click('[data-test="confirm-cancel"]')   # 先取消一次: 不应有副作用
    assert edge_state["detecting"][0] is True
    page.click('[data-test="op-stop_detection"]')
    page.wait_for_selector('[data-test="confirm-dialog"]', timeout=5000)
    page.click('[data-test="confirm-go"]')
    page.wait_for_selector('[data-test="field-detecting"]:has-text("待机")',
                           timeout=8000)
    assert edge_state["detecting"][0] is False

    # ---- 切项目 (下拉 + danger 确认) ----
    page.click('[data-test="op-activate_project"]')
    page.wait_for_selector('[data-test="project-select"]', timeout=5000)
    page.select_option('[data-test="project-select"]', "2")
    page.click('[data-test="confirm-go"]')
    page.wait_for_selector('[data-test="op-notice"]', timeout=8000)
    assert edge_state["active_project_id"] == 2, "假边缘应切到项目2"

    # ---- 锁徽章: 操作后本人持锁 ----
    page.wait_for_selector('[data-test="lock-tag"]', timeout=8000)
    assert "我在操作" in page.inner_text('[data-test="lock-tag"]')

    # 留证 (CI artifact / 本地核验)
    out = Path(__file__).resolve().parents[2] / "test-results"
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / "hub_m3_station_ops.png"), full_page=True)
