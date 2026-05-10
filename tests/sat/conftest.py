"""SAT 套件专用 conftest（不继承根 conftest 的影响）。

要求：
  - 默认全部 SKIP（除非设置 RUN_SAT=1）
  - 假设 backend 已经启动，验收脚本仅做黑盒 HTTP 调用
"""
from __future__ import annotations

import os
import socket

import pytest


SAT_API = os.environ.get("SAT_API_URL", "http://localhost:8001")


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_sat_item(item) -> bool:
    """v3.7.0 修复: 只对真正属于 tests/sat/ 下的 item 加 skip marker.
    原来的 hook 写法 `for item in items` 会扫整个 root 收集集合, 把
    全部 BDD/单元测试 (不只 SAT) 都 skip 掉, 导致跑 tests/ 时 382 全 skip.
    """
    path = str(getattr(item, "fspath", "") or "")
    return "/tests/sat/" in path or path.endswith("/tests/sat")


def pytest_collection_modifyitems(config, items):
    sat_items = [it for it in items if _is_sat_item(it)]
    if not sat_items:
        return

    if os.environ.get("RUN_SAT") != "1":
        skip_marker = pytest.mark.skip(reason="设置 RUN_SAT=1 才会跑 SAT 套件")
        for item in sat_items:
            item.add_marker(skip_marker)
        return

    host = SAT_API.replace("http://", "").replace("https://", "").split("/")[0]
    if ":" in host:
        h, p = host.split(":", 1)
        port = int(p)
    else:
        h, port = host, 80
    if not _port_open(h, port):
        skip_marker = pytest.mark.skip(reason=f"backend 未启动: {SAT_API}")
        for item in sat_items:
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def sat_api_url():
    return SAT_API
