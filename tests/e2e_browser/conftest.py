"""Playwright E2E 浏览器测试配置。

约定：
  - 假设用户已经启动了 backend (8001) 和 frontend (6001)
  - 浏览器测试通过 BASE_URL 访问 frontend
  - 所有测试创建的资源用 _E2E_PREFIX 前缀，结束统一清理
  - 不动用户已有的项目 / 模板 / 规则
"""
from __future__ import annotations

import os
import socket
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

import pytest
import requests


BASE_URL = os.environ.get("E2E_BASE_URL") or os.environ.get("TIANJUN_FRONTEND_URL", "http://localhost:6001")
API_URL = os.environ.get("E2E_API_URL") or os.environ.get("TIANJUN_BACKEND_URL", "http://localhost:8001")
E2E_PREFIX = "__e2e_"


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _host_port(url: str, default_port: int) -> tuple[str, int]:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.hostname or "localhost", parsed.port or default_port


def pytest_collection_modifyitems(config, items):
    """如果 backend/frontend 没启动，全部跳过 e2e 测试。

    端口取自 E2E_API_URL / E2E_BASE_URL（默认 8001/6001），
    隔离环境跑 8002/6002 时门卫跟着环境变量走，不再写死。
    """
    api_host, api_port = _host_port(API_URL, 8001)
    if not _port_open(api_host, api_port):
        skip = pytest.mark.skip(reason=f"backend ({api_host}:{api_port}) 未启动")
        for item in items:
            item.add_marker(skip)
        return
    front_host, front_port = _host_port(BASE_URL, 6001)
    if not _port_open(front_host, front_port):
        skip = pytest.mark.skip(reason=f"frontend ({front_host}:{front_port}) 未启动")
        for item in items:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def api_url():
    return API_URL


@pytest.fixture
def page_with_app(page, base_url):
    """打开应用首页，等待主布局加载"""
    page.goto(f"{base_url}/#/monitor", wait_until="domcontentloaded", timeout=15000)
    # 等到 Vue 主布局渲染（左侧导航出现）
    try:
        page.wait_for_selector(".el-menu, .layout-sidebar, [class*='sidebar']",
                                timeout=10000)
    except Exception:
        pass  # 即使没有 menu 也允许继续
    return page


@pytest.fixture(autouse=True)
def cleanup_e2e_resources(api_url):
    """每个测试准备一个激活项目，结束清理所有 __e2e_ 资源。"""
    _cleanup_resources(api_url)
    _ensure_active_project(api_url)
    yield
    _cleanup_resources(api_url)


def _list_field(payload):
    """适配 API 返回 {items:[...]} 或裸 list"""
    if isinstance(payload, dict) and "items" in payload:
        return payload["items"]
    if isinstance(payload, list):
        return payload
    return []


def _cleanup_resources(api_url: str):
    """清理所有以 __e2e_ 开头的导出模板/规则/项目"""
    try:
        r = requests.get(f"{api_url}/api/v1/export/realtime-rules", timeout=5)
        if r.status_code == 200:
            for rule in _list_field(r.json()):
                if (rule.get("name") or "").startswith(E2E_PREFIX):
                    requests.delete(
                        f"{api_url}/api/v1/export/realtime-rules/{rule['id']}",
                        timeout=5,
                    )
    except Exception as e:
        print(f"[cleanup] rules 清理失败: {e}")

    try:
        r = requests.get(f"{api_url}/api/v1/export/templates", timeout=5)
        if r.status_code == 200:
            for tpl in _list_field(r.json()):
                if (tpl.get("name") or "").startswith(E2E_PREFIX):
                    requests.delete(
                        f"{api_url}/api/v1/export/templates/{tpl['id']}",
                        timeout=5,
                    )
    except Exception as e:
        print(f"[cleanup] templates 清理失败: {e}")

    try:
        r = requests.get(f"{api_url}/api/v1/projects", timeout=5)
        if r.status_code == 200:
            for project in _list_field(r.json()):
                if (project.get("name") or "").startswith(E2E_PREFIX):
                    requests.delete(
                        f"{api_url}/api/v1/projects/{project['id']}",
                        timeout=5,
                    )
    except Exception as e:
        print(f"[cleanup] projects 清理失败: {e}")


def _ensure_active_project(api_url: str):
    """创建并激活一个最小项目，让 Data/Alarm 等依赖当前项目的页面可渲染主内容。"""
    name = f"{E2E_PREFIX}project_{uuid.uuid4().hex[:8]}"
    payload = {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {},
        "steps_config": [
            {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
            {"id": 2, "label": "step_b", "name": "步骤B", "enabled": True},
        ],
        "events_config": [
            {"id": 1, "name": "合格", "type": "ok", "enabled": True},
            {"id": 2, "name": "NG", "type": "ng", "enabled": True},
        ],
        "counters_config": [],
        "alarm_config": {},
        "detection_config": {},
        "data_config": {},
        "default_model_id": None,
        "model_format": "pytorch_fp32",
    }
    r = requests.post(f"{api_url}/api/v1/projects", json=payload, timeout=10)
    r.raise_for_status()
    project = r.json()
    activate = requests.post(f"{api_url}/api/v1/projects/{project['id']}/activate", timeout=10)
    activate.raise_for_status()
    return project


@pytest.fixture
def api_helper(api_url):
    """直接对 backend 发 HTTP，不通过 UI（用于 setup 测试数据）"""
    class _ApiHelper:
        def __init__(self):
            self.url = api_url

        def get(self, path):
            return requests.get(f"{self.url}{path}", timeout=10)

        def post(self, path, json=None):
            return requests.post(f"{self.url}{path}", json=json, timeout=10)

        def put(self, path, json=None):
            return requests.put(f"{self.url}{path}", json=json, timeout=10)

        def delete(self, path):
            return requests.delete(f"{self.url}{path}", timeout=10)

        def create_template(self, name_suffix, fmt="txt", content="hello {{ app.version }}"):
            r = self.post("/api/v1/export/templates", json={
                "name": f"{E2E_PREFIX}{name_suffix}",
                "format": fmt,
                "scope": "both",
                "content": content,
            })
            r.raise_for_status()
            return r.json()

    return _ApiHelper()
