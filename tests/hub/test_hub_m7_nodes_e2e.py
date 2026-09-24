"""Fleet Hub 节点管理 + 审计台账页 E2E (M7)。

调研缺口补齐: 此前只有墙上"纳管"弹窗, 无节点管理列表页 (改名/换地址/
换 Key/移除/版本不一致高亮), 审计只有后端无查看 UI。dist 不存在 skip。
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

    edge_a = create_fake_edge(node_id="edge-nm-a", station_count=2)
    edge_b = create_fake_edge(node_id="edge-nm-b", station_count=1)
    port_a, port_b = _free_port(), _free_port()
    srv_a = _start_uvicorn(edge_a, port_a)
    srv_b = _start_uvicorn(edge_b, port_b)

    data = str(tmp_path_factory.mktemp("hub_nodes_e2e"))
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


def test_nodes_admin_and_audit(page, stack):
    hub_url = stack["hub_url"]
    _login(page, hub_url)
    page.wait_for_selector('[data-test="wall-empty"]', timeout=8000)
    _enroll(page, "节点甲", stack["urls"][0])
    _enroll(page, "节点乙", stack["urls"][1])
    page.wait_for_function(
        """() => document.querySelectorAll(
            '[data-test^="station-tile-"]').length === 3""", timeout=15000)

    # ---- 进节点管理页: 两行, 在线状态, 版本一致不出"版本不一" ----
    page.click('[data-test="nodes-link"]')
    page.wait_for_selector('[data-test="nodes-table"]', timeout=8000)
    rows = page.locator('[data-test^="node-row-"]')
    assert rows.count() == 2
    assert "节点甲" in rows.first.inner_text()
    assert page.locator('[data-test="version-outlier"]').count() == 0

    # ---- 编辑: 改显示名 (本地生效, 不连边缘) ----
    page.click('[data-test="node-edit-1"]')
    page.wait_for_selector('[data-test="edit-dialog"]', timeout=5000)
    page.fill('[data-test="edit-name"]', "节点甲-改")
    page.click('[data-test="edit-save"]')
    page.wait_for_selector('[data-test="edit-dialog"]', state="detached",
                           timeout=8000)
    page.wait_for_function(
        """() => document.querySelector('[data-test="node-row-1"]')
            .innerText.includes('节点甲-改')""", timeout=5000)

    # ---- 编辑防呆: 改成打不通的地址 → 报错不保存 ----
    page.click('[data-test="node-edit-1"]')
    page.fill('[data-test="edit-url"]', "http://127.0.0.1:1")
    page.click('[data-test="edit-save"]')
    page.wait_for_selector('[data-test="edit-error"]', timeout=10000)
    assert "未保存" in page.inner_text('[data-test="edit-error"]')
    page.click('button:has-text("取消")')

    # ---- 立即轮询 ----
    page.click('[data-test="node-poll-2"]')
    page.wait_for_selector('[data-test="node-notice"]', timeout=8000)

    # ---- 审计台账: 纳管/改名有账, 按动作过滤 ----
    page.click('[data-test="tab-audit"]')
    page.wait_for_selector('[data-test^="audit-row-"]', timeout=8000)
    audit_text = page.inner_text('[data-test="audit-table"]')
    assert "纳管节点" in audit_text
    assert "修改节点" in audit_text
    page.select_option('[data-test="audit-action"]', "node.enroll")
    page.wait_for_function(
        """() => {
          const rows = document.querySelectorAll('[data-test^="audit-row-"]');
          return rows.length >= 2 && [...rows].every(
            r => r.innerText.includes('纳管节点'));
        }""", timeout=5000)

    out = Path(__file__).resolve().parents[2] / "test-results"
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / "hub_m7_audit.png"), full_page=True)

    # ---- 资源列: fake_edge 摘要的 CPU/内存/盘 百分比上表 ----
    page.click('[data-test="tab-nodes"]')
    page.wait_for_function(
        """() => (document.querySelector('[data-test="node-res-1"]') || {})
            .innerText?.includes('CPU 12%')""", timeout=8000)

    # ---- 连接历史: 一直在线 → 空态文案 ----
    page.click('[data-test="tab-conn"]')
    page.wait_for_selector('[data-test="conn-table"]', timeout=5000)
    assert "暂无上下线记录" in page.inner_text('[data-test="conn-table"]')

    # ---- 通知设置: 保存配置 + 测试发送 (通道不可达 → 失败结果可见) ----
    page.click('[data-test="tab-notify"]')
    page.wait_for_selector('[data-test="notify-enabled"]', timeout=5000)
    page.check('[data-test="notify-enabled"]')
    page.fill('[data-test="notify-threshold"]', "3")
    page.click('[data-test="chan-add"]')
    page.fill('[data-test="chan-row-0"] .url', "http://127.0.0.1:1/hook")
    page.click('[data-test="notify-save"]')
    page.wait_for_selector('[data-test="notify-msg"]', timeout=8000)
    assert "已保存" in page.inner_text('[data-test="notify-msg"]')
    page.click('[data-test="notify-test"]')
    page.wait_for_selector('[data-test="test-results"] li', timeout=10000)
    assert "失败" in page.inner_text('[data-test="test-results"]')

    # 配置持久化: 切走再回来阈值还在
    page.click('[data-test="tab-nodes"]')
    page.click('[data-test="tab-notify"]')
    page.wait_for_function(
        """() => document.querySelector('[data-test="notify-threshold"]')
            ?.value === '3'""", timeout=5000)

    # ---- 移除节点乙: 确认弹窗 → 行消失 ----
    page.click('[data-test="tab-nodes"]')
    page.click('[data-test="node-del-2"]')
    page.wait_for_selector('[data-test="remove-dialog"]', timeout=5000)
    page.click('[data-test="remove-go"]')
    page.wait_for_function(
        """() => document.querySelectorAll(
            '[data-test^="node-row-"]').length === 1""", timeout=8000)
