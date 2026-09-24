"""Fleet Hub M4 前端 CI E2E: 报警中心 + 用户管理 + 改密。

拓扑: 1 个 fake_edge uvicorn + 枢纽 (poller 开), Playwright 真浏览器:
登录 → 纳管 → 注入 NG 事件 → 墙顶栏徽标 → 报警中心确认处理 →
用户管理新建工程师 → 新账号登录改密 → 旧密失效新密可登。
dist 不存在时 skip。
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

    edge_app = create_fake_edge(node_id="edge-ev-e2e-01")
    edge_port = _free_port()
    edge_server = _start_uvicorn(edge_app, edge_port)

    data = str(tmp_path_factory.mktemp("hub_ev_e2e"))
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
               "edge": {"app": edge_app, "url": f"http://127.0.0.1:{edge_port}"}}
        hub_server.should_exit = True
        edge_server.should_exit = True
    finally:
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


def test_alarm_center_and_user_management(page, stack):
    hub_url = stack["hub_url"]
    edge = stack["edge"]

    # ---- admin 登录并纳管 ----
    _login(page, hub_url, "admin", "admin123")
    page.wait_for_selector('[data-test="wall-empty"]', timeout=8000)
    page.click('[data-test="enroll-open"]')
    page.fill('[data-test="enroll-name"]', "事件演示机")
    page.fill('[data-test="enroll-url"]', edge["url"])
    page.fill('[data-test="enroll-key"]', "tk_fake")
    page.click('[data-test="enroll-submit"]')
    page.wait_for_selector('[data-test^="station-tile-"]', timeout=10000)

    # ---- 等事件通道首拉订阅完成再注入 (否则事件被"从现在订阅"吞掉) ----
    # 竞态实录: 全量套跑机器负载高时, poller 首拉可能晚于下面的注入,
    # 首拉只落游标不回填历史 → id=1 永远拉不到 → 徽标 15s 超时。
    import httpx as _httpx
    tok = _httpx.post(f"{hub_url}/api/v1/auth/login", json={
        "username": "admin", "password": "admin123"}).json()["token"]
    deadline = time.time() + 10
    while time.time() < deadline:
        st = _httpx.get(f"{hub_url}/api/v1/nodes/1/status",
                        headers={"Authorization": f"Bearer {tok}"}).json()
        if (st.get("runtime") or {}).get("last_event_pull"):
            break
        time.sleep(0.3)
    else:
        pytest.fail("事件通道首拉 10s 未完成")

    # ---- 注入 NG 事件 (poller EVENT_PULL_INTERVAL_S=4s 内会拉到) ----
    edge["app"].state.edge["cycle_events"].append({
        "id": 1, "kind": "cycle", "channel_id": 0, "result": "NG",
        "event_name": "NG事件", "reason": "步骤缺失: 拧紧螺丝",
        "ts": "2026-09-19T14:00:00"})

    # 墙顶栏徽标出现
    page.wait_for_selector('[data-test="alarm-badge"]', timeout=15000)
    assert page.inner_text('[data-test="alarm-badge"]') == "1"

    # ---- 报警中心: 事件行 + 确认处理 ----
    page.click('[data-test="alarms-link"]')
    page.wait_for_selector('[data-test="alarms-table"]', timeout=8000)
    row = page.locator('[data-test^="ev-row-"]').first
    assert "步骤缺失" in row.inner_text()
    page.click('[data-test^="ack-"]')
    page.wait_for_selector('[data-test="acked-by"]', timeout=8000)
    assert "admin" in page.inner_text('[data-test="acked-by"]')
    counts = page.inner_text('[data-test="alarm-counts"]')
    assert "未处理 0" in counts

    out = Path(__file__).resolve().parents[2] / "test-results"
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / "hub_m4_alarm_center.png"), full_page=True)

    # ---- 用户管理: 新建工程师 ----
    page.click('[data-test="back-wall"]')
    page.wait_for_selector('[data-test="users-link"]', timeout=8000)
    page.click('[data-test="users-link"]')
    page.wait_for_selector('[data-test="users-table"]', timeout=8000)
    page.click('[data-test="user-create-open"]')
    page.fill('[data-test="user-username"]', "eng_e2e")
    page.fill('[data-test="user-display-name"]', "E2E工程师")
    page.select_option('[data-test="user-role"]', "engineer")
    page.fill('[data-test="user-password"]', "first123")
    page.click('[data-test="user-submit"]')
    page.wait_for_selector('[data-test="user-row-eng_e2e"]', timeout=8000)

    page.screenshot(path=str(out / "hub_m4_user_mgmt.png"), full_page=True)

    # ---- 新账号登录 → 改密 → 被引导重登 ----
    page.click('[data-test="back-wall"]')
    page.click('[data-test="logout-btn"]')
    page.wait_for_selector('[data-test="login-submit"]', timeout=8000)
    _login(page, hub_url, "eng_e2e", "first123")
    page.wait_for_selector('[data-test="pwd-open"]', timeout=8000)
    # 非 admin 不应看到用户管理/纳管入口
    assert page.locator('[data-test="users-link"]').count() == 0
    assert page.locator('[data-test="enroll-open"]').count() == 0

    page.click('[data-test="pwd-open"]')
    page.wait_for_selector('[data-test="pwd-dialog"]', timeout=5000)
    page.fill('[data-test="pwd-old"]', "first123")
    page.fill('[data-test="pwd-new"]', "second123")
    page.click('[data-test="pwd-submit"]')
    # 改密成功 → 会话吊销 → 回登录页
    page.wait_for_selector('[data-test="login-submit"]', timeout=8000)

    # 旧密失效, 新密可登
    _login(page, hub_url, "eng_e2e", "first123")
    page.wait_for_selector('[data-test="login-error"]', timeout=8000)
    _login(page, hub_url, "eng_e2e", "second123")
    page.wait_for_selector('[data-test="alarms-link"]', timeout=8000)