# -*- coding: utf-8 -*-
"""VLM 坐诊 (services/vlm_service + /api/v1/vlm) 测试 (2026-09 全量批次)。

- 配置 CRUD: 默认关闭 / 保存合并 / api_key 打码回显不回写
- 默认关闭时 ask 直接 503 (零网络请求)
- 启用后 ask 走 OpenAI 兼容协议 (mock requests.post), 组包与解析正确
- 端点探活 probe (mock requests.get)

不依赖真实大模型端点, 全部 mock HTTP。
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backend.services import vlm_service


@pytest.fixture(autouse=True)
def _fresh_config():
    """每例重置 KV 配置与缓存, 防串污染。"""
    vlm_service.save_config(dict(vlm_service.DEFAULT_CONFIG))
    yield
    vlm_service.save_config(dict(vlm_service.DEFAULT_CONFIG))


def _jpeg_bytes():
    import cv2
    img = np.full((64, 64, 3), 128, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


# ============================================================
# 配置
# ============================================================

def test_config_default_disabled(client):
    r = client.get("/api/v1/vlm/config")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert "chat" not in body["endpoint"]  # 端点是 base url


def test_config_save_and_mask(client):
    r = client.put("/api/v1/vlm/config", json={
        "enabled": True, "endpoint": "http://127.0.0.1:9999/v1",
        "model": "qwen2.5-vl:7b", "api_key": "sk-secret-123",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["api_key"].startswith("sk-") and "****" in body["api_key"]
    assert "secret" not in body["api_key"]
    # 打码值回传不得覆盖真实 key
    r2 = client.put("/api/v1/vlm/config", json={"api_key": body["api_key"]})
    assert r2.status_code == 200
    assert vlm_service.get_config(force=True)["api_key"] == "sk-secret-123"


def test_config_clamps_ranges(client):
    r = client.put("/api/v1/vlm/config", json={"timeout_s": 700})
    assert r.status_code == 422  # pydantic ge/le 校验


# ============================================================
# ask
# ============================================================

def test_ask_disabled_returns_503_without_network(client):
    with patch("requests.post") as mock_post:
        r = client.post("/api/v1/vlm/ask",
                        files={"file": ("a.jpg", io.BytesIO(_jpeg_bytes()),
                                        "image/jpeg")},
                        data={"question": "画面里有什么"})
        assert r.status_code == 503
        assert "未启用" in r.json()["detail"]
        mock_post.assert_not_called()


def test_ask_enabled_calls_openai_protocol(client):
    client.put("/api/v1/vlm/config", json={
        "enabled": True, "endpoint": "http://127.0.0.1:9999/v1",
        "model": "test-model", "api_key": "k1",
    })
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "model": "test-model",
        "choices": [{"message": {"content": "画面中有一台白色设备。"}}],
    }
    with patch("requests.post", return_value=fake) as mock_post:
        r = client.post("/api/v1/vlm/ask",
                        files={"file": ("a.jpg", io.BytesIO(_jpeg_bytes()),
                                        "image/jpeg")},
                        data={"question": "画面里有什么"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "画面中有一台白色设备。"
    assert body["model"] == "test-model"
    assert body["latency_ms"] >= 0

    # 组包检查: OpenAI 兼容 chat/completions + base64 图 + Bearer
    args, kwargs = mock_post.call_args
    assert args[0] == "http://127.0.0.1:9999/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer k1"
    msgs = kwargs["json"]["messages"]
    user_msg = msgs[-1]
    assert user_msg["role"] == "user"
    kinds = {c["type"] for c in user_msg["content"]}
    assert kinds == {"image_url", "text"}
    img_url = [c for c in user_msg["content"] if c["type"] == "image_url"][0]
    assert img_url["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_ask_endpoint_error_maps_503(client):
    client.put("/api/v1/vlm/config", json={
        "enabled": True, "endpoint": "http://127.0.0.1:9999/v1"})
    fake = MagicMock()
    fake.status_code = 500
    fake.text = "boom"
    with patch("requests.post", return_value=fake):
        r = client.post("/api/v1/vlm/ask",
                        files={"file": ("a.jpg", io.BytesIO(_jpeg_bytes()),
                                        "image/jpeg")},
                        data={"question": "q"})
    assert r.status_code == 503
    assert "500" in r.json()["detail"]


def test_ask_frame_channel_missing(client):
    r = client.post("/api/v1/vlm/ask-frame",
                    json={"channel_id": 99, "question": "q"})
    assert r.status_code in (404, 409)


# ============================================================
# probe
# ============================================================

def test_probe_reports_reachability(client):
    client.put("/api/v1/vlm/config", json={
        "enabled": True, "endpoint": "http://127.0.0.1:9999/v1"})
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": [{"id": "qwen2.5-vl:7b"}]}
    with patch("requests.get", return_value=fake):
        r = client.get("/api/v1/vlm/status")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True
    assert body["available_models"] == ["qwen2.5-vl:7b"]
