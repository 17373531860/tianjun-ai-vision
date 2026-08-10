"""短信配置一期 AT 文件到双 Provider canonical 结构的兼容回归。"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from backend.services.sms_config import (
    SmsConfigError,
    SmsConfigStore,
    config_from_dict,
)


def test_legacy_at_config_loads_and_is_canonical_after_next_save(tmp_path) -> None:
    path = tmp_path / "sms_config.json"
    path.write_text(
        json.dumps(
            {
                "enabled": False,
                "port": "COM8",
                "baudrate": 115200,
                "recipients": ["13800138000"],
                "template": "【天军AI视觉】{time} {event_name}：{message}",
                "encoding": "auto",
                "retries": 1,
                "retry_delay_seconds": 2,
                "cooldown_seconds": 60,
                "queue_size": 100,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = SmsConfigStore(path)

    config = store.load()
    store.save(config)

    assert config.provider == "at_modem"
    assert config.port == "COM8"
    assert config.recipients == ("13800138000",)
    canonical = json.loads(path.read_text(encoding="utf-8"))
    assert canonical["provider"] == "at_modem"
    assert canonical["at_modem"]["port"] == "COM8"
    assert canonical["phone_numbers"] == ["13800138000"]
    assert canonical["retry_count"] == 1
    assert canonical["retry_backoff_seconds"] == [2.0]
    assert canonical["ng_threshold"] == 5
    assert canonical["summary_send_mode"] == "merged_detail"
    assert canonical["summary_channel_ids"] == []
    assert "port" not in canonical
    assert "recipients" not in canonical


def test_invalid_config_falls_back_to_default_off_without_overwriting_file(
    tmp_path,
) -> None:
    path = tmp_path / "sms_config.json"
    path.write_text('{"enabled":true,"provider":"generic_http"}', encoding="utf-8")

    config = SmsConfigStore(path).load()

    assert config.enabled is False
    assert config.provider == "at_modem"
    assert path.read_text(encoding="utf-8") == '{"enabled":true,"provider":"generic_http"}'


def test_ng_threshold_roundtrips(tmp_path) -> None:
    path = tmp_path / "sms_config.json"
    store = SmsConfigStore(path)

    store.save(replace(store.load(), ng_threshold=7))

    assert store.load().ng_threshold == 7
    assert json.loads(path.read_text(encoding="utf-8"))["ng_threshold"] == 7


def test_summary_send_mode_and_channel_ids_roundtrip(tmp_path) -> None:
    path = tmp_path / "sms_config.json"
    store = SmsConfigStore(path)

    config = config_from_dict(
        {
            "summary_send_mode": "per_channel",
            "summary_channel_ids": [1, 0, 1],
        }
    )
    store.save(config)

    loaded = store.load()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert loaded.summary_send_mode == "per_channel"
    assert loaded.summary_channel_ids == (0, 1)
    assert saved["summary_send_mode"] == "per_channel"
    assert saved["summary_channel_ids"] == [0, 1]


@pytest.mark.parametrize("mode", ["", "by_channel", "merged"])
def test_invalid_summary_send_mode_is_rejected(mode: str) -> None:
    with pytest.raises(SmsConfigError, match="per_channel 或 merged_detail"):
        config_from_dict({"summary_send_mode": mode})


@pytest.mark.parametrize("ng_threshold", [0, 101])
def test_ng_threshold_rejects_out_of_range(ng_threshold: int) -> None:
    with pytest.raises(SmsConfigError, match="1~100"):
        config_from_dict({"ng_threshold": ng_threshold})


def test_wxpusher_config_roundtrip_and_enabled_validation(tmp_path) -> None:
    path = tmp_path / "sms_config.json"
    store = SmsConfigStore(path)

    config = config_from_dict(
        {
            "enabled": True,
            "provider": "wxpusher",
            "wxpusher": {
                "app_token": "AT_demo_token",
                "uids": ["UID_demo_1", "UID_demo_1"],
                "topic_ids": [12, 12],
                "content_type": 1,
            },
            "phone_numbers": [],
        }
    )
    store.save(config)
    loaded = store.load()
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert loaded.provider == "wxpusher"
    assert loaded.wxpusher_app_token == "AT_demo_token"
    assert loaded.wxpusher_uids == ("UID_demo_1",)
    assert loaded.wxpusher_topic_ids == (12,)
    assert saved["wxpusher"]["uids"] == ["UID_demo_1"]
    assert saved["provider"] == "wxpusher"
    # 旧通道块仍写出，避免前端缺字段
    assert "at_modem" in saved and "generic_http" in saved


def test_wxpusher_enabled_requires_token_and_target() -> None:
    with pytest.raises(SmsConfigError, match="appToken"):
        config_from_dict(
            {
                "enabled": True,
                "provider": "wxpusher",
                "wxpusher": {"uids": ["UID_x"]},
            }
        )
    with pytest.raises(SmsConfigError, match="UID 或 TopicId"):
        config_from_dict(
            {
                "enabled": True,
                "provider": "wxpusher",
                "wxpusher": {"app_token": "AT_x"},
            }
        )


def test_legacy_save_still_defaults_missing_wxpusher_block(tmp_path) -> None:
    path = tmp_path / "sms_config.json"
    path.write_text(
        json.dumps(
            {
                "enabled": False,
                "provider": "at_modem",
                "at_modem": {"port": "COM3", "baudrate": 115200},
                "phone_numbers": ["13800138000"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = SmsConfigStore(path)
    config = store.load()
    store.save(config)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert config.provider == "at_modem"
    assert saved["wxpusher"]["app_token"] == ""
    assert saved["wxpusher"]["uids"] == []
