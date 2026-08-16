"""v3.51.5 开机恢复守门 E2E：多工位禁止 Monitor localStorage 单工位兜底。

背景（2026-08-15 捷昌 B 站现场）：
    双工位开机时，Monitor 页的 localStorage 单工位兜底会抢在后端逐工位恢复
    之前，把"最后一次单工位用过的相机"POST 到 ch0（不带 channel 参数）。
    ch0 被怼成别工位的相机后，后端恢复见 ch0 已在跑不纠正；真正配这台相机
    的工位撞"使用中"预检快速失败三轮全灭。现场表现：左工位显示右工位画面、
    右工位黑屏什么都没有。

验证面（对 Monitor/index.vue autoRestoreSource 的守门改动）：
    1. 多工位模式：兜底被拦 —— 页面加载后【不得】出现 POST /source/camera/start；
    2. 单工位模式：老兜底行为保留 —— 页面加载后【必须】看到 POST /source/camera/start
       （只断言请求发出，不要求成功 —— CI 机可能没有摄像头）。

断言用 playwright 网络请求监听，与有无真实相机硬件解耦。
"""
from __future__ import annotations

import json
import time

import pytest
import requests


STALE_SOURCE_CONFIG = {
    "sourceType": "camera",
    "cameraSettings": {
        "deviceIndex": 1,
        "resolution": "1280x720",
        "fps": 30,
        "autoExposure": True,
        "exposureValue": -6,
    },
}

PRIME_SCRIPT = (
    "try { localStorage.setItem('source_config', "
    + json.dumps(json.dumps(STALE_SOURCE_CONFIG))
    + "); } catch (e) {}"
)


@pytest.fixture
def workstation_mode_guard(api_url):
    """记录并恢复工位数，测试内切换不污染环境。"""
    old_count = 1
    try:
        r = requests.get(f"{api_url}/api/v1/workstations/", timeout=5)
        old_count = int((r.json() or {}).get("channel_count", 1))
    except Exception:
        pass

    def _set_mode(count: int):
        requests.post(
            f"{api_url}/api/v1/workstations/mode",
            json={"channel_count": count, "channels": []},
            timeout=10,
        ).raise_for_status()

    yield _set_mode
    try:
        _set_mode(old_count)
    except Exception:
        pass


def _stop_all_cameras(api_url: str, channels: int):
    for ch in range(channels):
        for ep in ("camera/stop", "video/stop", "hikvision/stop"):
            try:
                requests.post(
                    f"{api_url}/api/v1/source/{ep}?channel={ch}", timeout=5)
            except Exception:
                pass


def _collect_camera_start_posts(page, base_url: str, wait_seconds: float):
    """加载 Monitor 页并收集窗口期内发出的 camera/start POST 请求。"""
    seen: list[str] = []

    def _on_request(request):
        if request.method == "POST" and "/source/camera/start" in request.url:
            seen.append(request.url)

    page.on("request", _on_request)
    # 带时间戳 query 强制真实整页加载 (同 URL 导航不会重跑 mounted)
    page.goto(f"{base_url}/?e2e={int(time.time())}#/monitor",
              wait_until="domcontentloaded", timeout=15000)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        page.wait_for_timeout(250)
        if seen:
            break
    return seen


def test_multiws_blocks_localstorage_fallback(page, base_url, api_url,
                                              workstation_mode_guard):
    """多工位模式：陈旧 localStorage 齐全也不得往 ch0 发 camera/start。"""
    workstation_mode_guard(2)
    _stop_all_cameras(api_url, 2)
    page.add_init_script(PRIME_SCRIPT)

    seen = _collect_camera_start_posts(page, base_url, wait_seconds=6.0)

    assert not seen, (
        f"多工位模式下 Monitor 兜底仍然发了 camera/start (会把单工位相机怼上 ch0): {seen}")

    # ch0 应保持无源 (后端也没被要求开相机)
    r = requests.get(f"{api_url}/api/v1/source/status?channel=0", timeout=5)
    assert r.status_code == 200
    assert not r.json().get("is_running"), \
        "多工位模式下 ch0 不应被 localStorage 兜底拉起"


def test_singlews_fallback_still_fires(page, base_url, api_url,
                                       workstation_mode_guard):
    """单工位模式：localStorage 兜底保持老行为，camera/start 必须发出。"""
    workstation_mode_guard(1)
    _stop_all_cameras(api_url, 1)
    page.add_init_script(PRIME_SCRIPT)

    seen = _collect_camera_start_posts(page, base_url, wait_seconds=10.0)

    assert seen, "单工位模式下 localStorage 兜底应照常尝试恢复相机 (回归被误伤)"


def _try_start_source(api_url: str, channel: int) -> bool:
    """给指定工位起一个源: 优先 synthetic (RUNTIME_MODE=test), 回退相机 0。
    CI 机可能两者都没有 → 返回 False, 调用方 skip。"""
    try:
        r = requests.post(
            f"{api_url}/api/v1/test/synthetic/start",
            json={"channel": channel, "scenario": "ok_simple", "fps": 10},
            timeout=10,
        )
        if r.status_code == 200:
            return True
    except Exception:
        pass
    try:
        r = requests.post(
            f"{api_url}/api/v1/source/camera/start?channel={channel}",
            json={"device_index": 0, "resolution": "1280x720", "fps": 30},
            timeout=30,
        )
        return r.status_code == 200
    except Exception:
        return False


def test_multiws_stream_reconnects_on_late_source(page, base_url, api_url,
                                                  workstation_mode_guard):
    """v3.51.5 慢恢复闭环：工位源在页面打开后才起来 → 前端必须自动重新取流。

    现场背景 (捷昌 B 站): 开机恢复 30s+, 监控页早已挂载, 源起来后没人重连
    视频流, 画面黑到用户手动切页再切回。修复 = processChannelResult 里
    is_running false→true 转变时强制重连该路 MJPEG/快照。
    """
    workstation_mode_guard(2)
    _stop_all_cameras(api_url, 2)

    feed_requests: list[str] = []

    def _on_request(request):
        url = request.url
        if "/video_feed?channel=0" in url or "/snapshot?channel=0" in url:
            feed_requests.append(url)

    page.on("request", _on_request)
    page.goto(f"{base_url}/?e2e={int(time.time())}#/monitor",
              wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(4000)   # 页面挂载完, 初始取流尝试全部结束
    baseline = len(feed_requests)

    if not _try_start_source(api_url, 0):
        pytest.skip("本机起不了 synthetic/相机源, 跳过 (CI 无硬件)")

    # 数据轮询 150ms 一拍发现 is_running=true → 2s 内必须看到新的取流请求
    deadline = time.time() + 8.0
    while time.time() < deadline and len(feed_requests) <= baseline:
        page.wait_for_timeout(250)

    try:
        assert len(feed_requests) > baseline, \
            "源起来后前端没有重新取流 (慢恢复黑屏回归)"
    finally:
        _stop_all_cameras(api_url, 2)
