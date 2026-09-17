"""一拖多「工作站推理 + 一体机网页工位」E2E。

覆盖四件事：
  1. 工作站用 HTTP 把 frontend dist 送出去，一体机同源打开（/api、/video_feed、
     /snapshot 全同一主机），且 /api 下打错的地址仍回 JSON 404。
  2. 工位 URL 契约 ``?channel=N&kiosk=1&readonly=0``：无项目/模型/输入源/设置入口，
     只订阅自己那一路且走 station 槽，readonly=0 时本工位可操作，hash 被钉死。
  3. axios baseURL 必须是同源相对路径 —— 这条曾被 .env.production 里写死的
     ``VITE_API_BASE_URL=http://localhost:8001`` 静默打穿（一体机把请求打给自己）。
  4. 工作站总览不跟一体机抢同一路 MJPEG：一体机占着 station 槽的工位降快照，
     没接屏的工位照旧 MJPEG。

静态托管相关用例在后端未托管 dist（纯 API 模式 / dist 没构建）时自动 skip。
"""
from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from .conftest import API_URL


FORBIDDEN_NAV = ["/project", "/model", "/source", "/settings", "/interconnect"]


# ============================================================
# 工具
# ============================================================

def _hosts_frontend() -> bool:
    """后端是否在托管前端 dist。"""
    try:
        r = requests.get(f"{API_URL}/", timeout=5)
    except requests.RequestException:
        return False
    return r.status_code == 200 and "text/html" in r.headers.get("content-type", "")


requires_static_hosting = pytest.mark.skipif(
    not _hosts_frontend(),
    reason="后端未托管 frontend dist（纯 API 模式或 dist 未构建）",
)


def _channel_count() -> int:
    r = requests.get(f"{API_URL}/api/v1/workstations/", timeout=5)
    r.raise_for_status()
    return int(r.json()["channel_count"])


def _set_channel_count(count: int) -> None:
    r = requests.post(f"{API_URL}/api/v1/workstations/mode",
                      json={"channel_count": count}, timeout=15)
    r.raise_for_status()


def _get_multi_monitor() -> dict:
    r = requests.get(f"{API_URL}/api/v1/workstations/multi-monitor", timeout=5)
    r.raise_for_status()
    return r.json()


def _set_multi_monitor(config: dict) -> None:
    r = requests.put(f"{API_URL}/api/v1/workstations/multi-monitor",
                     json=config, timeout=15)
    r.raise_for_status()


def _stream_viewers() -> dict:
    r = requests.get(f"{API_URL}/api/v1/source/stream/viewers", timeout=5)
    r.raise_for_status()
    return r.json().get("channels", {})


def _wait(predicate, timeout_s: float = 10.0, interval_s: float = 0.3) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval_s)
    return False


def _wait_on_page(page, predicate, timeout_ms: int = 12_000, step_ms: int = 400) -> bool:
    """等条件成立，但必须用 page.wait_for_timeout 推进。

    纯 time.sleep 不会驱动 Playwright 的消息循环，``page.on("request")``
    回调一条都收不到，条件永远不成立（踩过一次）。
    """
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return True
        page.wait_for_timeout(step_ms)
        waited += step_ms
    return predicate()


def _query(url: str, key: str):
    values = parse_qs(urlparse(url).query).get(key)
    return values[0] if values else None


class _Recorder:
    """记录页面的 API / 视频流请求与 baseURL 日志。"""

    def __init__(self, page):
        self.api: list[str] = []
        self.feed: list[str] = []
        self.snapshot: list[str] = []
        self.base_url_log = ""
        page.on("console", self._on_console)
        page.on("request", self._on_request)

    def _on_console(self, msg):
        if "Final baseURL" in msg.text:
            self.base_url_log = msg.text

    def _on_request(self, request):
        url = request.url
        if "/video_feed" in url:
            self.feed.append(url)
        elif "/snapshot" in url:
            self.snapshot.append(url)
        elif "/api/v1/" in url:
            self.api.append(url)

    def reset(self) -> None:
        """清空已记录请求，用于只观察"稳态"的断言。"""
        self.api.clear()
        self.feed.clear()
        self.snapshot.clear()

    def feed_channels(self) -> set[int]:
        return {int(c) for c in (_query(u, "channel") for u in self.feed) if c is not None}

    def snapshot_channels(self) -> set[int]:
        return {int(c) for c in (_query(u, "channel") for u in self.snapshot) if c is not None}


def _open_kiosk(page, origin: str, channel: int, readonly: str = "0") -> _Recorder:
    recorder = _Recorder(page)
    page.goto(f"{origin}/#/monitor?channel={channel}&kiosk=1&readonly={readonly}",
              wait_until="domcontentloaded", timeout=20_000)
    page.wait_for_selector("[data-testid=single-channel-controls]", timeout=20_000)
    page.wait_for_timeout(1_200)
    return recorder


@pytest.fixture
def two_channel_workstation():
    """把工作站临时切到 2 工位并关多屏，收尾原样还原。"""
    original_count = _channel_count()
    original_multi = _get_multi_monitor()
    if original_count < 2:
        _set_channel_count(2)
    _set_multi_monitor({"enabled": False, "readonly": True, "mapping": {}})
    try:
        yield
    finally:
        _set_multi_monitor(original_multi)
        _set_channel_count(original_count)


_SYNTHETIC_SCENARIO = {
    "name": "lan-station-e2e",
    "fps": 25,
    "timeline": [
        {"from": 0, "to": 100_000, "detections": [
            {"label": "工件", "confidence": 0.9, "bbox": [0.3, 0.3, 0.4, 0.4]},
        ]},
    ],
}


@pytest.fixture
def synthetic_sources(two_channel_workstation):
    """两路虚拟剧本源。

    viewer 槽只在真有视频源时登记（无源走的是黑底占位流），所以"总览让流"
    这类用例必须自带源，不能指望跑测试的人手上刚好开着相机。
    """
    probe = requests.get(f"{API_URL}/api/v1/test/synthetic/state?channel=0", timeout=5)
    if probe.status_code == 404:
        pytest.skip("后端未开 RUNTIME_MODE=test，synthetic 剧本源不可用")

    for ch in (0, 1):
        r = requests.post(f"{API_URL}/api/v1/test/synthetic/start", timeout=20, json={
            "scenario_json": _SYNTHETIC_SCENARIO, "channel": ch, "with_project": True,
        })
        r.raise_for_status()
    try:
        yield
    finally:
        for ch in (0, 1):
            requests.post(f"{API_URL}/api/v1/source/detection/stop?channel={ch}", timeout=15)
            requests.post(f"{API_URL}/api/v1/test/synthetic/stop?channel={ch}", timeout=15)


# ============================================================
# 1. 静态托管（HTTP 层）
# ============================================================

@requires_static_hosting
def test_workstation_serves_frontend_at_root():
    """一体机输 http://<工作站IP>:8001 就该看到系统本身。"""
    r = requests.get(f"{API_URL}/", timeout=10)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # index.html 必须不缓存: 升级换了带 hash 的新资源名, 老 index 会指向已删文件
    assert "no-cache" in r.headers.get("cache-control", "")


@requires_static_hosting
def test_hashed_assets_served_with_correct_mime():
    """Windows 注册表会把 .js 映射成 text/plain, 浏览器会拒绝执行 ESM。"""
    import re

    index = requests.get(f"{API_URL}/", timeout=10).text
    match = re.search(r'src="\.?/?(assets/[^"]+\.js)"', index)
    assert match, "index.html 里没有 assets/*.js 引用"
    asset = requests.get(f"{API_URL}/{match.group(1)}", timeout=10)
    assert asset.status_code == 200
    assert "javascript" in asset.headers.get("content-type", "")


@requires_static_hosting
@pytest.mark.parametrize("path", [
    "/api/v1/definitely-not-an-endpoint",
    "/api/v1/source/not-real",
])
def test_unknown_api_path_returns_json_404_not_index_html(path):
    """静态兜底绝不能把打错的接口地址喂成 200 + 一张 index.html。"""
    r = requests.get(f"{API_URL}{path}", timeout=10)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


@requires_static_hosting
def test_static_hosting_does_not_shadow_business_routes():
    assert requests.get(f"{API_URL}/health", timeout=5).json() == {"status": "healthy"}
    assert requests.get(f"{API_URL}/api/v1/projects", timeout=10).status_code == 200


# ============================================================
# 2. 同源：一体机不能把请求打给自己
# ============================================================

@requires_static_hosting
def test_station_page_resolves_same_origin_api(page):
    """baseURL 写死 localhost 时，一体机整页 Network Error —— 钉死这条判据。"""
    recorder = _open_kiosk(page, API_URL, 0)

    assert "/api/v1" in recorder.base_url_log, recorder.base_url_log
    assert "localhost" not in recorder.base_url_log, recorder.base_url_log
    assert recorder.api, "页面没发出任何 API 请求"
    off_origin = [u for u in recorder.api if not u.startswith(API_URL)]
    assert not off_origin, f"API 请求跑到别的 origin: {off_origin[:3]}"
    off_origin_feed = [u for u in recorder.feed if not u.startswith(API_URL)]
    assert not off_origin_feed, f"视频流跑到别的 origin: {off_origin_feed[:3]}"


# ============================================================
# 3. 工位 URL 契约
# ============================================================

@requires_static_hosting
def test_kiosk_hides_project_and_settings_entries(page):
    """本期禁止一体机做项目管理：入口一个都不能露。"""
    _open_kiosk(page, API_URL, 0)

    html = page.content()
    leaked = [nav for nav in FORBIDDEN_NAV if f'href="#{nav}"' in html]
    assert not leaked, f"kiosk 泄漏了导航入口: {leaked}"
    assert page.locator('button[title="导航菜单"]').count() == 0, "kiosk 不该有侧边栏汉堡"


@requires_static_hosting
def test_kiosk_readonly_zero_keeps_local_controls_operable(page):
    """readonly=0 仍可本工位启停（只读副屏才是 readonly=1）。"""
    _open_kiosk(page, API_URL, 0, readonly="0")

    controls = page.locator("[data-testid=single-channel-controls]")
    assert controls.get_attribute("data-readonly") == "false"
    assert not page.locator("[data-testid=single-channel-reset]").is_disabled()


@requires_static_hosting
def test_kiosk_readonly_one_locks_controls(page):
    """吉田副屏那类只读形态必须仍然锁死控制。"""
    _open_kiosk(page, API_URL, 0, readonly="1")

    controls = page.locator("[data-testid=single-channel-controls]")
    assert controls.get_attribute("data-readonly") == "true"
    assert page.locator("[data-testid=single-channel-reset]").is_disabled()


@requires_static_hosting
def test_station_subscribes_only_its_own_channel_via_station_slot(
        page, two_channel_workstation):
    """每台一体机只连自己一路，且占 station 槽（与工作站 main 槽互不踢）。"""
    recorder = _open_kiosk(page, API_URL, 1)
    page.wait_for_timeout(1_500)

    watched = recorder.feed_channels() | recorder.snapshot_channels()
    assert watched == {1}, f"工位 1 的一体机订阅了别的工位: {watched}"
    viewers = {_query(u, "viewer") for u in recorder.feed}
    assert viewers <= {"station"}, f"一体机必须走 station 槽, 实际 {viewers}"


@requires_static_hosting
def test_kiosk_hash_is_pinned_to_its_own_station(page):
    """一体机现场没有键盘鼠标退路，漂到别的页面等于这台屏当场报废。"""
    _open_kiosk(page, API_URL, 0)

    page.evaluate("() => { window.location.hash = '#/project'; }")
    page.wait_for_timeout(1_200)
    assert "/monitor" in page.url and "kiosk=1" in page.url, page.url
    assert "project" not in page.url, page.url


# ============================================================
# 4. 总览不跟一体机抢同一路 MJPEG
# ============================================================

def test_stream_viewers_endpoint_reports_slots():
    channels = _stream_viewers()
    assert channels, "至少要报 0 号工位"
    for payload in channels.values():
        assert set(payload) >= {"viewers", "station", "main", "legacy"}


@requires_static_hosting
def test_overview_yields_mjpeg_to_connected_station(
        browser, page, synthetic_sources):
    """一体机占着工位 1 的直播 → 工作站总览这一格降快照，工位 0 照旧 MJPEG。"""
    station_ctx = browser.new_context()
    station_page = station_ctx.new_page()
    try:
        _open_kiosk(station_page, API_URL, 1)
        assert _wait_on_page(station_page,
                             lambda: _stream_viewers().get("1", {}).get("station") is True), \
            f"后端没看到工位 1 的 station 槽: {_stream_viewers()}"

        recorder = _Recorder(page)
        page.goto(f"{API_URL}/#/monitor", wait_until="domcontentloaded", timeout=20_000)

        # 首屏会先按常规起 MJPEG，等第一拍 station 槽探测回来（~2s）才让流；
        # 断言只看稳态，不追究这个开屏瞬间的重叠。
        assert _wait_on_page(page, lambda: 1 in recorder.snapshot_channels(),
                             timeout_ms=15_000), \
            f"总览没给一体机让流: feed={recorder.feed_channels()} snap={recorder.snapshot_channels()}"

        recorder.reset()
        page.wait_for_timeout(5_000)

        assert 1 not in recorder.feed_channels(), \
            f"稳态下总览仍在抢工位 1 的 MJPEG: {[u for u in recorder.feed if 'channel=1' in u][:2]}"
        assert 1 in recorder.snapshot_channels(), "让流后应持续用快照看工位 1"
        assert 0 in recorder.feed_channels(), \
            f"没接屏的工位 0 应该照旧 MJPEG: feed={recorder.feed_channels()}"
    finally:
        station_ctx.close()


@requires_static_hosting
def test_overview_keeps_mjpeg_when_no_station_connected(page, synthetic_sources):
    """没有一体机在看时，总览行为与以前完全一致（不平白降快照）。"""
    assert _wait(lambda: all(not v.get("station") for v in _stream_viewers().values()),
                 timeout_s=8.0), f"环境里还有 station 连接: {_stream_viewers()}"

    recorder = _Recorder(page)
    page.goto(f"{API_URL}/#/monitor", wait_until="domcontentloaded", timeout=20_000)
    page.wait_for_timeout(6_000)

    assert recorder.feed_channels(), "总览应该用 MJPEG 直播"
    viewers = {_query(u, "viewer") for u in recorder.feed}
    assert viewers <= {"main"}, f"工作站主窗必须走 main 槽, 实际 {viewers}"
