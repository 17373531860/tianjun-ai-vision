"""多屏工位配置持久化与 API 合同测试。"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated_multi_monitor_config(monkeypatch, tmp_path):
    """把 workstation_config.json 指向测试临时目录，避免接触现场配置。"""
    import backend.api.channel_manager as module

    config_path = tmp_path / "workstation_config.json"
    monkeypatch.setattr(module, "_CONFIG_FILE", str(config_path))
    manager = object.__new__(module.ChannelManager)
    manager.channel_count = 4
    return module, manager, config_path


def test_multi_monitor_defaults_are_disabled_and_readonly(isolated_multi_monitor_config):
    _module, manager, _config_path = isolated_multi_monitor_config

    assert manager.get_multi_monitor_config() == {
        "enabled": False,
        "readonly": True,
        "mapping": {},
    }


def test_multi_monitor_roundtrip_normalizes_and_preserves_other_sections(
    isolated_multi_monitor_config,
):
    _module, manager, config_path = isolated_multi_monitor_config
    original = {
        "channel_count": 4,
        "channels": {"0": {"source_type": "rtsp"}},
        "splash": {"enabled": True},
        "window": {"fullscreen": False},
        "auto_resume": {"enabled": True},
    }
    config_path.write_text(json.dumps(original), encoding="utf-8")

    saved = manager.set_multi_monitor_config(
        enabled=True,
        readonly=True,
        mapping={
            "0": {
                "display_id": " 123 ",
                "bounds": {"x": -1920, "y": 0, "width": 1920, "height": 1080},
                "aux_display_id": " 124 ",
                "aux_bounds": {"x": -1920, "y": 1080, "width": 800, "height": 480},
                "aux_hands_enabled": True,
                "aux_view_mode": " follow ",
            },
            "1": {
                "display_id": "456",
                "bounds": {"x": 0, "y": 0, "width": 0, "height": 1080},
            },
            "2": {
                "display_id": "",
                "bounds": {"x": 1920, "y": 0, "width": 1920, "height": 1080},
                "aux_display_id": "457",
                "aux_bounds": {"x": 0, "y": 0, "width": -1, "height": 480},
                "aux_hands_enabled": "true",
                "aux_view_mode": "unexpected",
            },
            "bad": {"display_id": "789"},
            "64": {"display_id": "999"},
            "3": {"display_id": "", "bounds": {"width": -1, "height": 1}},
        },
    )

    assert saved == {
        "enabled": True,
        "readonly": True,
        "mapping": {
            "0": {
                "display_id": "123",
                "role": "monitor",
                "bounds": {"x": -1920, "y": 0, "width": 1920, "height": 1080},
                "aux_display_id": "124",
                "aux_bounds": {"x": -1920, "y": 1080, "width": 800, "height": 480},
                "aux_hands_enabled": True,
                "aux_view_mode": "follow",
            },
            "1": {"display_id": "456", "role": "monitor"},
            "2": {
                "display_id": "",
                "role": "monitor",
                "bounds": {"x": 1920, "y": 0, "width": 1920, "height": 1080},
                "aux_display_id": "457",
                "aux_hands_enabled": False,
                "aux_view_mode": "follow",
            },
        },
    }
    on_disk = json.loads(config_path.read_text(encoding="utf-8"))
    for key, value in original.items():
        assert on_disk[key] == value
    assert on_disk["multi_monitor"] == saved
    assert manager.get_multi_monitor_config() == saved


def test_legacy_display_id_mapping_stays_main_only(isolated_multi_monitor_config):
    module, _manager, _config_path = isolated_multi_monitor_config

    assert module.ChannelManager._normalize_multi_monitor_mapping({
        "0": {"display_id": " 1001 "},
    }) == {
        "0": {"display_id": "1001", "role": "monitor"},
    }


def test_aux_mapping_can_fall_back_to_bounds(isolated_multi_monitor_config):
    module, _manager, _config_path = isolated_multi_monitor_config

    assert module.ChannelManager._normalize_multi_monitor_mapping({
        "1": {
            "display_id": "main",
            "aux_display_id": "",
            "aux_bounds": {"x": 1920, "y": 0, "width": 800, "height": 480},
        },
    }) == {
        "1": {
            "display_id": "main",
            "role": "monitor",
            "aux_display_id": "",
            "aux_bounds": {"x": 1920, "y": 0, "width": 800, "height": 480},
            "aux_hands_enabled": False,
            "aux_view_mode": "follow",
        },
    }


def test_aux_switch_is_strict_and_requires_a_target(isolated_multi_monitor_config):
    module, _manager, _config_path = isolated_multi_monitor_config

    normalized = module.ChannelManager._normalize_multi_monitor_mapping({
        "0": {
            "display_id": "main-0",
            "aux_display_id": "aux-0",
            "aux_hands_enabled": True,
            "aux_view_mode": "fixed",
        },
        "1": {
            "display_id": "main-1",
            "aux_display_id": "aux-1",
            "aux_hands_enabled": 1,
        },
        "2": {
            "display_id": "main-2",
            "aux_hands_enabled": True,
        },
    })

    assert normalized["0"]["aux_hands_enabled"] is True
    assert normalized["0"]["aux_view_mode"] == "fixed"
    assert normalized["1"]["aux_hands_enabled"] is False
    assert normalized["1"]["aux_view_mode"] == "follow"
    assert "aux_hands_enabled" not in normalized["2"]


def test_save_channel_count_keeps_top_level_multi_monitor(isolated_multi_monitor_config):
    _module, manager, config_path = isolated_multi_monitor_config
    config_path.write_text(
        json.dumps({
            "channel_count": 2,
            "channels": {"0": {"project_id": 7}},
            "multi_monitor": {
                "enabled": True,
                "readonly": True,
                "mapping": {"0": {"display_id": "123"}},
            },
            "window": {"fullscreen": True},
        }),
        encoding="utf-8",
    )
    manager.channel_count = 3

    manager._save_config()

    on_disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert on_disk["channel_count"] == 3
    assert on_disk["channels"] == {"0": {"project_id": 7}}
    assert on_disk["multi_monitor"]["enabled"] is True
    assert on_disk["window"] == {"fullscreen": True}


def test_multi_monitor_role_normalizes_and_persists(isolated_multi_monitor_config):
    """v3.57 投影光引导: projection 角色保留、非法值归一 monitor、缺省 monitor。"""
    _module, manager, _config_path = isolated_multi_monitor_config

    saved = manager.set_multi_monitor_config(
        enabled=True,
        readonly=True,
        mapping={
            "0": {"display_id": "a", "role": "projection"},
            "1": {"display_id": "b", "role": "hologram"},
            "2": {"display_id": "c"},
        },
    )

    assert saved["mapping"]["0"]["role"] == "projection"
    assert saved["mapping"]["1"]["role"] == "monitor"
    assert saved["mapping"]["2"]["role"] == "monitor"
    assert manager.get_multi_monitor_config() == saved


def test_multi_monitor_api_roundtrip(isolated_multi_monitor_config, client):
    _module, _manager, _config_path = isolated_multi_monitor_config
    payload = {
        "enabled": True,
        "readonly": True,
        "mapping": {
            "0": {
                "display_id": "1001",
                "role": "projection",
                "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
            },
        },
    }

    put_response = client.put("/api/v1/workstations/multi-monitor", json=payload)
    get_response = client.get("/api/v1/workstations/multi-monitor")

    assert put_response.status_code == 200, put_response.text
    assert get_response.status_code == 200, get_response.text
    assert get_response.json() == payload



def test_multi_monitor_api_roundtrip_with_aux(isolated_multi_monitor_config, client):
    _module, _manager, _config_path = isolated_multi_monitor_config
    payload = {
        "enabled": True,
        "readonly": True,
        "mapping": {
            "0": {
                "display_id": "1001",
                "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "aux_display_id": "2001",
                "aux_bounds": {"x": 1920, "y": 0, "width": 800, "height": 480},
                "aux_hands_enabled": True,
                "aux_view_mode": "follow",
            },
        },
    }

    put_response = client.put("/api/v1/workstations/multi-monitor", json=payload)
    get_response = client.get("/api/v1/workstations/multi-monitor")

    assert put_response.status_code == 200, put_response.text
    assert get_response.status_code == 200, get_response.text
    # 旧 payload 不带 role, 后端归一化补缺省 monitor (v3.57 投影光引导, 升级零差异)
    expected = {
        **payload,
        "mapping": {"0": {**payload["mapping"]["0"], "role": "monitor"}},
    }
    assert put_response.json() == expected
    assert get_response.json() == expected


def test_legacy_aux_mapping_defaults_off_and_to_follow_view_mode(
    isolated_multi_monitor_config,
    client,
):
    _module, _manager, _config_path = isolated_multi_monitor_config
    payload = {
        "enabled": True,
        "readonly": True,
        "mapping": {
            "0": {
                "display_id": "1001",
                "aux_display_id": "2001",
            },
        },
    }

    put_response = client.put("/api/v1/workstations/multi-monitor", json=payload)

    assert put_response.status_code == 200, put_response.text
    assert put_response.json()["mapping"]["0"]["aux_hands_enabled"] is False
    assert put_response.json()["mapping"]["0"]["aux_view_mode"] == "follow"


def test_multi_monitor_api_rejects_unknown_aux_view_mode(
    isolated_multi_monitor_config,
    client,
):
    _module, _manager, _config_path = isolated_multi_monitor_config

    response = client.put(
        "/api/v1/workstations/multi-monitor",
        json={
            "enabled": True,
            "readonly": True,
            "mapping": {
                "0": {
                    "display_id": "1001",
                    "aux_display_id": "2001",
                    "aux_view_mode": "zoom",
                },
            },
        },
    )

    assert response.status_code == 422


def test_multi_monitor_routes_have_openapi_contract(isolated_multi_monitor_config):
    module, _manager, _config_path = isolated_multi_monitor_config
    routes = [
        route for route in module.router.routes
        if route.path == "/workstations/multi-monitor"
    ]

    assert {next(iter(route.methods)) for route in routes} == {"GET", "PUT"}
    assert {route.summary for route in routes} == {"读取多屏配置", "保存多屏配置"}
    assert all(route.response_model is module.MultiMonitorConfig for route in routes)



@pytest.mark.parametrize("legacy_fields", [
    {"visitor_display_id": "3001", "visitor_bounds": {"x": -1920, "y": 0, "width": 1920, "height": 1080}},
    {"visitor_display_id": {"invalid": True}, "visitor_bounds": "invalid old bounds"},
])
def test_removed_visitor_fields_are_ignored_on_read_and_write(
    isolated_multi_monitor_config, client, legacy_fields,
):
    module, manager, config_path = isolated_multi_monitor_config
    other_sections = {
        "channels": {"0": {"project_id": 7}}, "splash": {"enabled": True},
        "auto_resume": {"enabled": True}, "unowned": {"future_config": "untouched"},
    }
    expected = {
        "enabled": True, "readonly": False,
        "mapping": {"0": {"display_id": "1001", "role": "monitor"}},
    }
    original = {**other_sections, "multi_monitor": {**expected, **legacy_fields}}
    config_path.write_text(json.dumps(original), encoding="utf-8")

    response = client.get("/api/v1/workstations/multi-monitor")
    assert response.status_code == 200
    assert response.json() == expected
    assert manager.get_multi_monitor_config() == expected
    assert json.loads(config_path.read_text(encoding="utf-8")) == original
    assert not set(legacy_fields) & module.MultiMonitorConfig.model_json_schema()["properties"].keys()

    response = client.put("/api/v1/workstations/multi-monitor", json={**expected, **legacy_fields})
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert client.get("/api/v1/workstations/multi-monitor").json() == expected
    assert json.loads(config_path.read_text(encoding="utf-8")) == {**other_sections, "multi_monitor": expected}
