"""短信独立配置 API 的写盘、读回与默认关闭回归。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.api import sms as sms_api
from backend.services.sms_config import SmsConfigStore
from backend.services.sms_service import SmsService, SmsServiceConfig


@pytest.fixture
def isolated_sms_runtime(tmp_path, monkeypatch):
    """把短信 API 的文件与运行时服务隔离到当前测试临时目录。"""

    store = SmsConfigStore(tmp_path / "sms_config.json")
    service = SmsService(
        SmsServiceConfig(),
        summary_state_path=tmp_path / "sms_summary_state.json",
    )
    monkeypatch.setattr(sms_api, "_config_store", store)
    monkeypatch.setattr(sms_api, "_sms_service", service)
    yield store, tmp_path
    current = sms_api.get_sms_service()
    shutdown = getattr(current, "shutdown", None)
    if callable(shutdown):
        shutdown(timeout=0.2)
    if current is not service:
        service.shutdown(timeout=0.2)


def test_get_missing_config_returns_default_off_without_creating_file(
    client, isolated_sms_runtime
) -> None:
    store, _tmp_path = isolated_sms_runtime

    response = client.get("/api/v1/sms/config")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["port"] == ""
    assert body["baudrate"] == 115200
    assert body["recipients"] == []
    assert body["ng_threshold"] == 5
    assert not store.path.exists()


def test_put_config_writes_independent_file_and_get_reads_it_back(
    client, isolated_sms_runtime
) -> None:
    store, tmp_path = isolated_sms_runtime
    alarm_config = tmp_path / "alarm_config.json"
    alarm_config.write_text('{"sentinel":"tower-only"}', encoding="utf-8")
    payload = {
        "enabled": False,
        "port": " COM9 ",
        "baudrate": 115200,
        "recipients": ["138 0013 8000", "+86-139-0013-9000"],
        "template": "【天军AI视觉】{time} {event_name}：{message}",
        "encoding": "auto",
        "retries": 2,
        "retry_delay_seconds": 3,
        "ng_threshold": 7,
        "cooldown_seconds": 120,
        "queue_size": 50,
    }

    put_response = client.put("/api/v1/sms/config", json=payload)
    get_response = client.get("/api/v1/sms/config")

    assert put_response.status_code == 200
    assert get_response.status_code == 200
    expected = put_response.json()
    assert get_response.json() == expected
    assert expected["enabled"] is False
    assert expected["port"] == "COM9"
    assert expected["recipients"] == ["13800138000", "+8613900139000"]
    assert expected["retries"] == 2
    assert expected["retry_delay_seconds"] == 3
    assert expected["retry_backoff_seconds"] == [3.0, 3.0]
    assert expected["ng_threshold"] == 7
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["provider"] == "at_modem"
    assert on_disk["at_modem"]["port"] == "COM9"
    assert on_disk["phone_numbers"] == expected["recipients"]
    assert on_disk["ng_threshold"] == 7
    assert "recipients" not in on_disk
    assert "port" not in on_disk
    assert alarm_config.read_text(encoding="utf-8") == '{"sentinel":"tower-only"}'
    assert SmsConfigStore(store.path).load().port == "COM9"


def test_put_enabled_config_requires_com_and_recipient(
    client, isolated_sms_runtime
) -> None:
    store, _tmp_path = isolated_sms_runtime

    response = client.put(
        "/api/v1/sms/config",
        json={"enabled": True, "port": "", "recipients": []},
    )

    assert response.status_code == 422
    assert not store.path.exists()
    assert sms_api.get_sms_service().config.enabled is False


def test_list_ports_only_enumerates_without_opening_serial(
    client, isolated_sms_runtime, monkeypatch
) -> None:
    monkeypatch.setattr(
        sms_api.serial.tools.list_ports,
        "comports",
        lambda: [SimpleNamespace(device="COM7", description="USB 4G", hwid="USB\\VID")],
    )

    response = client.get("/api/v1/sms/ports")

    assert response.status_code == 200
    assert response.json() == {
        "ports": [{"port": "COM7", "description": "USB 4G", "hwid": "USB\\VID"}]
    }


def test_sms_endpoints_have_required_openapi_contract(app) -> None:
    schema = app.openapi()

    config_get = schema["paths"]["/api/v1/sms/config"]["get"]
    config_put = schema["paths"]["/api/v1/sms/config"]["put"]
    ports_get = schema["paths"]["/api/v1/sms/ports"]["get"]
    assert config_get["summary"] == "读取短信配置"
    assert config_put["summary"] == "保存短信配置"
    assert ports_get["summary"] == "列出短信串口"
    assert "200" in config_get["responses"]
    assert "requestBody" in config_put


def test_put_generic_http_config_writes_nested_canonical_file(
    client, isolated_sms_runtime
) -> None:
    store, _tmp_path = isolated_sms_runtime
    payload = {
        "enabled": True,
        "provider": "generic_http",
        "phone_numbers": ["13800138000", "+86-139-0013-9000"],
        "at_modem": {
            "port": "",
            "baudrate": 115200,
            "encoding": "auto",
            "template": "【天军AI视觉】{time} {event_name}：{message}",
        },
        "generic_http": {
            "api_url": "https://sms.example.test/v1/send",
            "request_method": "POST",
            "timeout_seconds": 8,
            "token": "test-token",
            "access_key": "",
            "access_secret": "",
            "sign_name": "智能检测系统",
            "template_id": "alarm_001",
            "verify_ssl": True,
            "field_mapping": {},
        },
        "retry_count": 2,
        "retry_backoff_seconds": [1, 3, 5],
        "ng_threshold": 9,
        "cooldown_seconds": 30,
        "queue_size": 50,
        "offline_queue_max": 200,
        "offline_ttl_seconds": 86400,
    }

    response = client.put("/api/v1/sms/config", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "generic_http"
    assert body["phone_numbers"] == ["13800138000", "+8613900139000"]
    assert body["recipients"] == body["phone_numbers"]
    assert body["generic_http"]["api_url"] == payload["generic_http"]["api_url"]
    assert body["ng_threshold"] == 9
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["provider"] == "generic_http"
    assert on_disk["phone_numbers"] == body["phone_numbers"]
    assert on_disk["generic_http"]["token"] == "test-token"
    assert on_disk["ng_threshold"] == 9
    assert "recipients" not in on_disk
    assert "port" not in on_disk


def test_put_enabled_generic_http_requires_url_auth_and_phone(
    client, isolated_sms_runtime
) -> None:
    response = client.put(
        "/api/v1/sms/config",
        json={
            "enabled": True,
            "provider": "generic_http",
            "phone_numbers": ["13800138000"],
            "generic_http": {"api_url": "https://sms.example.test/v1/send"},
        },
    )

    assert response.status_code == 422


def test_post_sms_test_queues_current_window_snapshot_in_background(
    client, isolated_sms_runtime, monkeypatch
) -> None:
    from backend.services.sms_service import AlarmQueueReceipt

    calls: list[dict[str, object]] = []

    class _FakeService:
        def queue_test_sms(self, **kwargs: object) -> AlarmQueueReceipt:
            calls.append(kwargs)
            return AlarmQueueReceipt(
                accepted=True,
                status="queued",
                detail="已进入短信异步队列",
                job_id="sms-test-42",
                success=True,
                message="已进入短信异步队列",
                message_id="sms-test-42",
                status_code=202,
                retry_count=0,
                queued=True,
                error_code="",
            )

    monkeypatch.setattr(sms_api, "get_sms_service", lambda: _FakeService())

    response = client.post(
        "/api/v1/sms/test",
        json={"phone_numbers": ["13800138000"]},
    )

    assert response.status_code == 202
    assert response.json()["message_id"] == "sms-test-42"
    assert response.json()["queued"] is True
    assert calls == [{"message": None, "recipients": ["13800138000"]}]


def test_sms_test_endpoint_is_in_openapi(app) -> None:
    operation = app.openapi()["paths"]["/api/v1/sms/test"]["post"]

    assert operation["summary"] == "后台测试短信通道"
    assert "requestBody" in operation
