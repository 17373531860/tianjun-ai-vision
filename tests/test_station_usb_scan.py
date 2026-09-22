"""工位账号 USB 扫码权限回归：限定设备和工位，保留原调试接口权限。"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def station_usb(client, monkeypatch):
    from backend.api import scanner
    from backend.core.auth_deps import CurrentUser, get_current_user
    from backend.db.database import SessionLocal
    from backend.models.mes_models import ScannerDevice

    with SessionLocal() as db:
        device = ScannerDevice(name="__test_station_usb", ip="", port=0,
                               channel_id=1, enabled=True, device_type="usb_hid")
        db.add(device)
        db.commit()
        device_id = device.id
    connection = SimpleNamespace(enabled=True, channel_id=1, external_only=False, broadcast_channels=[])
    service = SimpleNamespace(_usb_devices={device_id: connection}, simulate_scan=Mock(return_value={
        "success": True, "device_id": device_id, "channel_id": 1,
        "barcode": "PART-001", "serial_no": "PART-001",
    }))
    monkeypatch.setattr(scanner, "get_scanner_service", lambda: service)
    user = CurrentUser(id=77, username="station-1", display_name="工位2", roles=["station"],
                       permissions=["monitor.view", "monitor.detection.control"],
                       is_anonymous=False, is_superuser=False, allowed_channels=[1])
    previous = client.app.dependency_overrides.copy()
    client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield {"barcode": "PART-001", "device_id": device_id, "channel_id": 1}, service, user
    finally:
        client.app.dependency_overrides.clear()
        client.app.dependency_overrides.update(previous)
        with SessionLocal() as db:
            db.query(ScannerDevice).filter_by(id=device_id).delete()
            db.commit()


def test_station_can_submit_own_usb_without_scanner_edit(client, station_usb):
    payload, service, _ = station_usb
    r = client.post("/api/v1/scanner/usb-scan", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["channel_id"] == 1
    service.simulate_scan.assert_called_once_with(**payload)
    assert client.post("/api/v1/scanner/simulate", json=payload).status_code == 403


@pytest.mark.parametrize("changes,status", [
    ({"channel_id": 0}, 403), ({"device_id": 999999}, 404),
    ({"barcode": "   "}, 400), ({"channel_id": -1}, 422),
])
def test_station_rejects_invalid_usb_submission(client, station_usb, changes, status):
    payload, service, _ = station_usb
    assert client.post("/api/v1/scanner/usb-scan", json={**payload, **changes}).status_code == status
    service.simulate_scan.assert_not_called()


@pytest.mark.parametrize("changes,status", [
    ({"enabled": False}, 404), ({"device_type": "text_lon"}, 404),
    ({"channel_id": 0}, 404), ({"broadcast_channels": [0, 1]}, 403),
    ({"external_only": True}, 403),
])
def test_station_rejects_unsuitable_device(client, station_usb, changes, status):
    from backend.db.database import SessionLocal
    from backend.models.mes_models import ScannerDevice
    payload, service, _ = station_usb
    with SessionLocal() as db:
        db.query(ScannerDevice).filter_by(id=payload["device_id"]).update(changes)
        db.commit()
    assert client.post("/api/v1/scanner/usb-scan", json=payload).status_code == status
    service.simulate_scan.assert_not_called()


def test_station_rejects_unloaded_device_and_missing_control_permission(client, station_usb):
    payload, service, user = station_usb
    service._usb_devices.clear()
    assert client.post("/api/v1/scanner/usb-scan", json=payload).status_code == 409
    user.permissions = ["monitor.view"]
    assert client.post("/api/v1/scanner/usb-scan", json=payload).status_code == 403
    service.simulate_scan.assert_not_called()
