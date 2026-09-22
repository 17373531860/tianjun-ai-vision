"""Fleet Hub M2 前端 CI E2E: 登录 → 空墙 → 退出。

自起枢纽 HTTP (uvicorn 线程 + dist 静态), 不依赖主程序 6001/8001。
dist 不存在时 skip (先 `cd hub/frontend && npm run build`)。
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


@pytest.fixture(scope="module")
def hub_url(tmp_path_factory):
    import os
    data = str(tmp_path_factory.mktemp("hub_e2e"))
    old_dir = os.environ.get("HUB_DATA_DIR")
    old_poller = os.environ.get("HUB_ENABLE_POLLER")
    os.environ["HUB_DATA_DIR"] = data
    os.environ["HUB_ENABLE_POLLER"] = "0"
    try:
        from hub.backend.main import create_app
        import uvicorn

        app = create_app()
        port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("枢纽 e2e 服务未起来")
        yield f"http://127.0.0.1:{port}"
        server.should_exit = True
    finally:
        if old_dir is None:
            os.environ.pop("HUB_DATA_DIR", None)
        else:
            os.environ["HUB_DATA_DIR"] = old_dir
        if old_poller is None:
            os.environ.pop("HUB_ENABLE_POLLER", None)
        else:
            os.environ["HUB_ENABLE_POLLER"] = old_poller


def test_login_empty_wall_logout(page, hub_url):
    page.goto(f"{hub_url}/#/login", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_selector('[data-test="login-submit"]', timeout=8000)

    page.fill('[data-test="login-username"]', "admin")
    page.fill('[data-test="login-password"]', "wrong")
    page.click('[data-test="login-submit"]')
    page.wait_for_selector('[data-test="login-error"]', timeout=5000)

    page.fill('[data-test="login-password"]', "admin123")
    page.click('[data-test="login-submit"]')
    page.wait_for_selector('[data-test="wall-empty"]', timeout=8000)
    assert page.locator('[data-test="enroll-open"]').count() == 1

    page.click('[data-test="enroll-open"]')
    page.wait_for_selector('[data-test="enroll-dialog"]', timeout=5000)
    page.click('[data-test="enroll-cancel"]')
    page.click('[data-test="logout-btn"]')
    page.wait_for_selector('[data-test="login-submit"]', timeout=5000)
