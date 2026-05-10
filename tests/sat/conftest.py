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


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_SAT") != "1":
        skip_marker = pytest.mark.skip(reason="设置 RUN_SAT=1 才会跑 SAT 套件")
        for item in items:
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
        for item in items:
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def sat_api_url():
    return SAT_API
