"""Monitor localStorage fallback must forward USB manual-exposure settings."""
from __future__ import annotations

import json


def test_monitor_localstorage_camera_restore_forwards_manual_exposure(page, base_url):
    captured = []

    def handle_status(route):
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps({
                'is_running': False,
                'is_detecting': False,
                'source_type': None,
            }),
        )

    def handle_camera_start(route, request):
        captured.append(request.post_data_json)
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps({'status': 'success'}),
        )

    page.route('**/api/v1/source/status*', handle_status)
    page.route('**/api/v1/source/camera/start*', handle_camera_start)
    page.add_init_script(
        """
        localStorage.setItem('source_config', JSON.stringify({
          sourceType: 'camera',
          cameraSettings: {
            deviceIndex: 1,
            resolution: '1280x720',
            fps: 60,
            autoExposure: false,
            exposureValue: -5
          }
        }));
        """
    )

    page.goto(f'{base_url}/#/monitor', wait_until='domcontentloaded', timeout=15000)
    page.wait_for_function('() => document.body.innerText.length > 0', timeout=10000)
    page.wait_for_timeout(2500)

    assert captured, 'Monitor 应从 source_config 发起摄像头恢复请求'
    payload = captured[-1]
    assert payload['device_index'] == 1
    assert payload['auto_exposure'] is False
    assert payload['exposure_value'] == -5
