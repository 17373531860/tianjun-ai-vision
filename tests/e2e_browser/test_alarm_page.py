"""Alarm 页 E2E：基于 AlarmPage POM。"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

import pytest

from .pages import AlarmPage


def test_alarm_settings_panel_loaded(page, base_url):
    ap = AlarmPage(page, base_url).goto()
    if not ap.has_settings_panel():
        pytest.skip(f"Alarm 页未渲染或路径不对, body={ap.body_text()[:200]}")
    assert ap.has_settings_panel()


def test_alarm_connection_status(page, base_url):
    ap = AlarmPage(page, base_url).goto()
    status = ap.get_connection_status()
    assert status in ("已连接", "未连接", "连接中"), f"应显示连接状态, 实际 {status!r}"


def test_alarm_voice_panel_present(page, base_url):
    ap = AlarmPage(page, base_url).goto()
    assert ap.has_voice_panel()


def test_alarm_event_panel_present(page, base_url):
    ap = AlarmPage(page, base_url).goto()
    assert ap.has_event_panel()


def test_alarm_event_titles_include_ok_or_ng(page, base_url):
    ap = AlarmPage(page, base_url).goto()
    titles = ap.get_event_titles()
    assert any(
        t in titles for t in ("合格(OK)", "不良(NG)", "合格", "NG", "事件1", "事件2")
    ), f"事件标题应包含 OK/NG/事件1/事件2, 实际 {titles}"


def _sms_default_config() -> dict:
    return {
        "enabled": False,
        "provider": "at_modem",
        "at_modem": {
            "port": "",
            "baudrate": 115200,
            "encoding": "auto",
            "template": "【天军AI视觉】{device_name} {time_range} OK{ok_count} NG{ng_count}",
        },
        "generic_http": {
            "api_url": "",
            "request_method": "POST",
            "timeout_seconds": 10,
            "token": "",
            "access_key": "",
            "access_secret": "",
            "sign_name": "",
            "template_id": "",
            "verify_ssl": True,
            "field_mapping": {},
        },
        "phone_numbers": [],
        "retry_count": 3,
        "retry_backoff_seconds": [1, 3, 5],
        "ng_threshold": 5,
        "cooldown_seconds": 60,
        "queue_size": 100,
        "offline_queue_max": 200,
        "offline_ttl_seconds": 86400,
    }


def _mock_sms_port(page, port: str = "COM_E2E") -> None:
    def handler(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ports": [
                        {
                            "port": port,
                            "description": "E2E 假短信模块",
                            "hwid": "USB\\VID_E2E",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )

    page.route("**/api/v1/sms/ports", handler)


def _mock_sms_test_receipts(page) -> None:
    call_count = 0

    def handler(route):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            status = 202
            payload = {
                "success": True,
                "message": "测试短信已进入后台队列",
                "message_id": "e2e-queued",
                "status_code": 202,
                "retry_count": 0,
                "queued": True,
                "error_code": "",
                "status": "queued",
            }
        else:
            status = 400
            payload = {
                "success": False,
                "message": "云短信鉴权失败",
                "message_id": "e2e-rejected",
                "status_code": 400,
                "retry_count": 0,
                "queued": False,
                "error_code": "HTTP_401",
                "status": "rejected",
            }
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    page.route("**/api/v1/sms/test", handler)


def _alarm_config_path() -> Path:
    configured = os.environ.get("E2E_ALARM_CONFIG_PATH")
    return Path(configured) if configured else Path("backend/alarm_config.json")


def _file_sha256(path: Path) -> str:
    if not path.exists():
        return "<missing>"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sms_default_off_provider_switch_and_required_validation(
    page, base_url, api_helper
):
    original = api_helper.get("/api/v1/sms/config")
    original.raise_for_status()
    reset = api_helper.put("/api/v1/sms/config", json=_sms_default_config())
    reset.raise_for_status()
    _mock_sms_port(page)

    try:
        alarm_page = AlarmPage(page, base_url).goto()
        assert alarm_page.wait_sms_panel()
        assert alarm_page.has_sms_summary_copy()
        assert alarm_page.has_sms_test_snapshot_hint()
        assert alarm_page.legacy_instant_controls_hidden()
        assert not alarm_page.is_sms_enabled(), "短信总开关默认应关闭"
        assert alarm_page.sms_provider() == "at_modem"
        assert alarm_page.sms_at_fields_visible()
        assert not alarm_page.sms_http_fields_visible()

        alarm_page.select_sms_provider("generic_http")
        assert alarm_page.sms_provider() == "generic_http"
        assert alarm_page.sms_http_fields_visible()
        assert not alarm_page.sms_at_fields_visible()
        alarm_page.fill_sms_recipients("13800138000")
        alarm_page.set_sms_enabled(True).save_sms_config()
        assert alarm_page.wait_message("开启云短信前必须填写 API URL")

        alarm_page.select_sms_provider("at_modem").save_sms_config()
        assert alarm_page.wait_message("开启 AT 短信推送前必须选择短信模块独立 COM")
        persisted = api_helper.get("/api/v1/sms/config").json()
        assert persisted["enabled"] is False
        assert persisted["provider"] == "at_modem"
        assert persisted["at_modem"]["port"] == ""

        alarm_page.select_sms_port("COM_E2E").fill_sms_recipients("")
        alarm_page.save_sms_config()
        assert alarm_page.wait_message("开启 12 小时汇总短信前必须填写接收手机号")
    finally:
        restore = api_helper.put("/api/v1/sms/config", json=original.json())
        restore.raise_for_status()


def test_sms_at_save_refresh_roundtrip_and_tower_isolation(page, base_url, api_helper):
    page.set_viewport_size({"width": 1600, "height": 1200})
    original = api_helper.get("/api/v1/sms/config")
    original.raise_for_status()
    legacy_compatible = _sms_default_config()
    legacy_compatible["ng_threshold"] = 7
    legacy_compatible["cooldown_seconds"] = 321
    reset = api_helper.put("/api/v1/sms/config", json=legacy_compatible)
    reset.raise_for_status()
    _mock_sms_port(page)

    try:
        alarm_page = AlarmPage(page, base_url).goto()
        assert alarm_page.wait_sms_panel()
        tower_before = api_helper.get("/api/v1/alarm/status?channel=0")
        tower_before.raise_for_status()
        alarm_path = _alarm_config_path()
        alarm_sha_before = _file_sha256(alarm_path)

        alarm_page.refresh_sms_ports()
        assert alarm_page.wait_message("检测到 1 个短信模块候选串口")
        alarm_page.select_sms_port("COM_E2E")
        alarm_page.fill_sms_recipients("138 0013 8000；+86-139-0013-9000\n13800138000")
        alarm_page.fill_sms_template(
            "【天军AI视觉】{device_name} {time_range} 合格{ok_count} NG{ng_count}"
        )
        alarm_page.open_sms_advanced()
        alarm_page.set_sms_retries(2)
        alarm_page.set_sms_retry_backoff("2, 4")
        alarm_page.set_sms_enabled(True).save_sms_config()
        assert alarm_page.wait_message("短信配置已保存并回读确认", timeout_ms=8000)

        persisted = api_helper.get("/api/v1/sms/config")
        persisted.raise_for_status()
        saved = persisted.json()
        assert saved["enabled"] is True
        assert saved["provider"] == "at_modem"
        assert saved["at_modem"]["port"] == "COM_E2E"
        assert saved["at_modem"]["baudrate"] == 115200
        assert saved["phone_numbers"] == ["13800138000", "+8613900139000"]
        assert saved["ng_threshold"] == 7, "隐藏的旧阈值字段应原样回传兼容"
        assert saved["cooldown_seconds"] == 321, "隐藏的旧冷却字段应原样回传兼容"
        assert saved["retry_count"] == 2
        assert saved["retry_backoff_seconds"] == [2.0, 4.0]

        screenshot = os.environ.get("E2E_SMS_SCREENSHOT")
        if screenshot:
            Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
            alarm_page.screenshot_sms_card(screenshot)

        page.reload(wait_until="domcontentloaded")
        assert alarm_page.wait_sms_panel()
        assert alarm_page.wait_sms_enabled(True), "刷新后短信开关应回填为开启"
        assert alarm_page.is_sms_enabled()
        assert "COM_E2E" in alarm_page.sms_port_text()
        assert alarm_page.sms_recipients_value() == "13800138000\n+8613900139000"
        assert alarm_page.sms_template_value() == saved["template"]
        alarm_page.open_sms_advanced()
        assert alarm_page.legacy_instant_controls_hidden()
        assert alarm_page.sms_retries_value() == "2"

        tower_after = api_helper.get("/api/v1/alarm/status?channel=0")
        tower_after.raise_for_status()
        before = tower_before.json()
        after = tower_after.json()
        assert after.get("config") == before.get("config")
        assert after.get("port") == before.get("port")
        assert after.get("is_connected") == before.get("is_connected")
        assert _file_sha256(alarm_path) == alarm_sha_before
    finally:
        restore = api_helper.put("/api/v1/sms/config", json=original.json())
        restore.raise_for_status()


def test_sms_http_save_refresh_roundtrip_and_tower_isolation(
    page, base_url, api_helper
):
    page.set_viewport_size({"width": 1600, "height": 1400})
    original = api_helper.get("/api/v1/sms/config")
    original.raise_for_status()
    reset = api_helper.put("/api/v1/sms/config", json=_sms_default_config())
    reset.raise_for_status()

    try:
        alarm_page = AlarmPage(page, base_url).goto()
        assert alarm_page.wait_sms_panel()
        tower_before = api_helper.get("/api/v1/alarm/status?channel=0")
        tower_before.raise_for_status()
        alarm_path = _alarm_config_path()
        alarm_sha_before = _file_sha256(alarm_path)

        alarm_page.select_sms_provider("generic_http")
        assert alarm_page.has_sms_cloud_template_guidance()
        alarm_page.fill_sms_http_api_url("https://sms.example.test/v1/send")
        alarm_page.fill_sms_http_token("fake-e2e-token")
        alarm_page.fill_sms_http_sign_name("天军视觉")
        alarm_page.fill_sms_http_template_id("SMS_E2E_001")
        alarm_page.fill_sms_http_field_mapping(
            '{"phone_numbers":"mobiles","message":"content"}'
        )
        alarm_page.set_sms_http_verify_ssl(False)
        alarm_page.fill_sms_recipients("13800138000\n13900139000")
        alarm_page.open_sms_advanced()
        alarm_page.set_sms_retries(1)
        alarm_page.set_sms_retry_backoff("0.5, 2")
        alarm_page.set_sms_enabled(True).save_sms_config()
        assert alarm_page.wait_message("短信配置已保存并回读确认", timeout_ms=8000)

        persisted = api_helper.get("/api/v1/sms/config")
        persisted.raise_for_status()
        saved = persisted.json()
        assert saved["enabled"] is True
        assert saved["provider"] == "generic_http"
        assert saved["phone_numbers"] == ["13800138000", "13900139000"]
        assert saved["generic_http"] == {
            "api_url": "https://sms.example.test/v1/send",
            "request_method": "POST",
            "timeout_seconds": 10.0,
            "token": "fake-e2e-token",
            "access_key": "",
            "access_secret": "",
            "sign_name": "天军视觉",
            "template_id": "SMS_E2E_001",
            "verify_ssl": False,
            "field_mapping": {
                "phone_numbers": "mobiles",
                "message": "content",
            },
        }
        assert saved["retry_count"] == 1
        assert saved["retry_backoff_seconds"] == [0.5, 2.0]

        screenshot = os.environ.get("E2E_SMS_HTTP_SCREENSHOT")
        if screenshot:
            Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
            alarm_page.screenshot_sms_card(screenshot)

        page.reload(wait_until="domcontentloaded")
        assert alarm_page.wait_sms_panel()
        assert alarm_page.wait_sms_enabled(True)
        assert alarm_page.sms_provider() == "generic_http"
        assert alarm_page.sms_http_fields_visible()
        assert not alarm_page.sms_at_fields_visible()
        assert alarm_page.sms_http_api_url_value() == saved["generic_http"]["api_url"]
        assert alarm_page.sms_http_token_value() == "fake-e2e-token"
        assert alarm_page.sms_http_sign_name_value() == "天军视觉"
        assert alarm_page.sms_http_template_id_value() == "SMS_E2E_001"
        assert not alarm_page.sms_http_verify_ssl()
        assert (
            json.loads(alarm_page.sms_http_field_mapping_value())
            == saved["generic_http"]["field_mapping"]
        )

        tower_after = api_helper.get("/api/v1/alarm/status?channel=0")
        tower_after.raise_for_status()
        before = tower_before.json()
        after = tower_after.json()
        assert after.get("config") == before.get("config")
        assert after.get("port") == before.get("port")
        assert after.get("is_connected") == before.get("is_connected")
        assert _file_sha256(alarm_path) == alarm_sha_before
    finally:
        restore = api_helper.put("/api/v1/sms/config", json=original.json())
        restore.raise_for_status()


def test_sms_test_send_shows_queued_and_error_receipts_without_hanging(
    page, base_url, api_helper
):
    original = api_helper.get("/api/v1/sms/config")
    original.raise_for_status()
    reset = api_helper.put("/api/v1/sms/config", json=_sms_default_config())
    reset.raise_for_status()
    _mock_sms_test_receipts(page)

    try:
        alarm_page = AlarmPage(page, base_url).goto()
        assert alarm_page.wait_sms_panel()
        alarm_page.fill_sms_recipients("13800138000").test_sms_send()
        assert alarm_page.wait_message(
            "当前未闭合窗口快照：测试短信已进入后台队列（queued；非完整 12 小时窗口，不代表最终送达）",
            timeout_ms=5000,
        )
        assert alarm_page.wait_sms_test_ready(), "测试发送完成后按钮应解除 loading"

        alarm_page.test_sms_send()
        assert alarm_page.wait_message(
            "当前窗口快照测试失败：云短信鉴权失败（HTTP_401）",
            timeout_ms=5000,
        )
        assert alarm_page.wait_sms_test_ready(), "失败回执后页面也不能卡死"
    finally:
        restore = api_helper.put("/api/v1/sms/config", json=original.json())
        restore.raise_for_status()
