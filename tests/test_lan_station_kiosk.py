# -*- coding: utf-8 -*-
"""一拖多局域网工位屏：静态托管 / 局域网 CORS / 直播订阅槽 / 工位级权限。

现场叙事：一台工作站跑推理，八到十六台一体机用浏览器开
``http://<工作站IP>:8001/#/monitor?channel=N&kiosk=1&readonly=0``。
一体机不装软件、不配后端地址，页面与 API 同源；工位账号只能动自己那一路。
"""

from __future__ import annotations

import os
import re
import threading

import numpy as np
import pytest


# ============================================================
# 1. 前端 dist 定位与开关
# ============================================================

def _make_dist(tmp_path, name="dist"):
    dist = tmp_path / name
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=app></div>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("export default 1;", encoding="utf-8")
    return dist


def test_dist_env_override_points_at_custom_dir(monkeypatch, tmp_path):
    from backend.core import lan_station

    dist = _make_dist(tmp_path)
    monkeypatch.setenv("TIANJUN_WEB_DIST", str(dist))
    assert lan_station.resolve_web_dist_dir() == str(dist)


@pytest.mark.parametrize("off_value", ["off", "0", "false", "no", "OFF"])
def test_dist_env_off_disables_hosting(monkeypatch, off_value):
    """客户想让工作站只当纯 API 用时，一个环境变量关掉托管。"""
    from backend.core import lan_station

    monkeypatch.setenv("TIANJUN_WEB_DIST", off_value)
    assert lan_station.resolve_web_dist_dir() is None


def test_dist_missing_index_is_not_hosted(monkeypatch, tmp_path):
    """目录在但没 index.html（构建失败/半个包）→ 当作没有，不能半托管。"""
    from backend.core import lan_station

    empty = tmp_path / "half-built"
    empty.mkdir()
    monkeypatch.setenv("TIANJUN_WEB_DIST", str(empty))
    assert lan_station.resolve_web_dist_dir() is None


def test_dist_candidates_cover_source_and_packaged_layout():
    """源码形态 frontend/dist 与 Electron 打包形态 app/dist 都要在候选里。"""
    from backend.core import lan_station

    tails = [os.path.join(*os.path.normpath(c).split(os.sep)[-2:])
             for c in lan_station._DIST_CANDIDATES]
    assert os.path.join("frontend", "dist") in tails
    assert os.path.join("app", "dist") in tails


# ============================================================
# 2. 保留路径：打错的接口地址不能被 index.html 兜底成 200
# ============================================================

@pytest.mark.parametrize("path", [
    "api/v1/projects", "/api/v1/projects", "docs", "redoc",
    "openapi.json", "health", "uploads/a.png", "recordings/x.mp4",
    "video_feed", "snapshot", "ws/x", "API/v1/projects",
])
def test_reserved_paths_stay_with_backend(path):
    from backend.core.lan_station import is_reserved_path
    assert is_reserved_path(path) is True


@pytest.mark.parametrize("path", ["", "index.html", "assets/index-abc.js", "favicon.ico"])
def test_frontend_paths_are_not_reserved(path):
    from backend.core.lan_station import is_reserved_path
    assert is_reserved_path(path) is False


# ============================================================
# 3. 静态文件解析：目录穿越必须挡住
# ============================================================

def test_static_file_resolves_inside_dist(tmp_path):
    from backend.core.lan_station import resolve_static_file

    dist = _make_dist(tmp_path)
    assert resolve_static_file(str(dist), "index.html") == str(dist / "index.html")
    assert resolve_static_file(str(dist), "assets/index-abc123.js") == \
        str(dist / "assets" / "index-abc123.js")
    # 空路径 = 首页
    assert resolve_static_file(str(dist), "") == str(dist / "index.html")


@pytest.mark.parametrize("evil", [
    "../secret.txt",
    "../../secret.txt",
    "assets/../../secret.txt",
    "%2e%2e/secret.txt",
    "%2e%2e%2fsecret.txt",
    "/etc/passwd",
])
def test_static_file_blocks_directory_traversal(tmp_path, evil):
    from backend.core.lan_station import resolve_static_file

    dist = _make_dist(tmp_path)
    (tmp_path / "secret.txt").write_text("machine license", encoding="utf-8")
    assert resolve_static_file(str(dist), evil) is None


def test_static_file_missing_returns_none(tmp_path):
    from backend.core.lan_station import resolve_static_file
    dist = _make_dist(tmp_path)
    assert resolve_static_file(str(dist), "assets/not-built.js") is None


# ============================================================
# 4. 局域网 CORS：私网放行、公网不放
# ============================================================

@pytest.mark.parametrize("origin", [
    "http://localhost:6001", "http://127.0.0.1:8001",
    "http://192.168.1.10:8001", "http://192.168.1.10",
    "http://10.0.0.7:6001", "http://172.16.5.8:8001",
    "http://172.31.255.254:80", "https://192.168.20.30:8443",
])
def test_lan_origins_allowed(origin):
    from backend.core.lan_station import LAN_ORIGIN_REGEX
    assert re.match(LAN_ORIGIN_REGEX, origin), f"{origin} 应该放行"


@pytest.mark.parametrize("origin", [
    "http://8.8.8.8", "https://evil.example.com",
    "http://172.15.0.1", "http://172.32.0.1",
    "http://192.169.1.1", "http://11.0.0.1",
    "http://192.168.1.10.evil.com",
    "http://localhost.evil.com",
])
def test_public_origins_rejected(origin):
    from backend.core.lan_station import LAN_ORIGIN_REGEX
    assert not re.match(LAN_ORIGIN_REGEX, origin), f"{origin} 不该放行"


# ============================================================
# 5. 直播订阅槽：总览据此让出一体机正在看的那一路
# ============================================================

def test_active_viewer_slots_filters_by_heartbeat():
    from backend.api.source import VideoSourceManager

    now = 1000.0
    seen = {"station": now - 0.1, "main": now - 0.5, "legacy": now - 30.0}
    assert VideoSourceManager.active_viewer_slots(seen, now) == ["main", "station"]


def test_active_viewer_slots_empty_when_nobody_watching():
    from backend.api.source import VideoSourceManager
    assert VideoSourceManager.active_viewer_slots({}, 1000.0) == []
    assert VideoSourceManager.active_viewer_slots(None, 1000.0) == []


def test_get_stream_viewers_reports_station_occupancy():
    """一体机占着 station 槽 → 报 station=True，工作站总览据此降 snapshot。"""
    import time as _time

    from backend.api.source import VideoSourceManager
    from backend.api.source_state_init import _init_streaming_state

    mgr = VideoSourceManager.__new__(VideoSourceManager)
    _init_streaming_state(mgr)
    assert mgr.get_stream_viewers()["station"] is False

    mgr._mjpeg_slot_seen["station"] = _time.monotonic()
    viewers = mgr.get_stream_viewers()
    assert viewers["station"] is True
    assert viewers["main"] is False
    assert viewers["viewers"] == ["station"]


def test_mjpeg_yield_marks_slot_heartbeat(monkeypatch):
    """真的推一帧后槽位才算活着 —— 判活靠推帧，不靠连接表。"""
    from backend.api.channel_manager import channel_manager
    from backend.api.source import VideoSourceManager
    from backend.api.source_state_init import _init_fps_stats, _init_streaming_state

    mgr = VideoSourceManager.__new__(VideoSourceManager)
    mgr.channel_id = 1
    mgr.is_running = True
    mgr.current_frame = np.zeros((8, 8, 3), dtype=np.uint8)
    mgr.frame_lock = threading.Lock()
    mgr.frame_limit_enabled = False
    mgr.target_stream_fps = 30
    mgr._pending_ack = False
    _init_fps_stats(mgr)
    _init_streaming_state(mgr)
    mgr._encode_and_yield = lambda frame: b"--frame\r\n\r\nx\r\n"
    monkeypatch.setattr(channel_manager, "channel_count", 2)

    gen = mgr.generate_mjpeg(viewer="station")
    with mgr.frame_lock:
        mgr._frame_seq += 1
    next(gen)
    assert mgr.get_stream_viewers()["station"] is True

    gen.close()
    # 自己退场要清掉心跳，否则总览会一直以为一体机还在看
    assert mgr.get_stream_viewers()["station"] is False


# ============================================================
# 6. 工位级权限：工位账号只能动自己那一路
# ============================================================

@pytest.mark.parametrize("raw,expected", [
    (None, None), ([], None), ("", None), ({}, None),
    ([0], [0]), ([2, 1, 1], [1, 2]), (["0", "3"], [0, 3]),
    (3, [3]), ("2", [2]),
    ([-1], None),
    (["abc", 1], [1]),
    ("garbage", None),
])
def test_normalize_allowed_channels(raw, expected):
    from backend.core.auth_deps import normalize_allowed_channels
    assert normalize_allowed_channels(raw) == expected


def _user(channels, superuser=False):
    from backend.core.auth_deps import CurrentUser
    return CurrentUser(
        id=7, username="w01", display_name="工位1",
        roles=["station"], permissions=["monitor.view", "monitor.detection.control"],
        is_anonymous=False, is_superuser=superuser, allowed_channels=channels,
    )


def test_channel_scope_allows_own_channel_only():
    from backend.core.auth_deps import user_can_access_channel

    station = _user([1])
    assert user_can_access_channel(station, 1) is True
    assert user_can_access_channel(station, 0) is False
    assert user_can_access_channel(station, 3) is False


def test_channel_scope_unset_means_all_channels():
    """存量账号 / admin / 匿名 operator 一律不限工位 —— 升级零差异。"""
    from backend.core.auth_deps import user_can_access_channel

    for u in (_user(None), _user([]), _user([2], superuser=True)):
        assert all(user_can_access_channel(u, ch) for ch in range(4))


def test_require_channel_scope_rejects_other_station():
    from fastapi import HTTPException

    from backend.core.auth_deps import require_channel_scope

    assert require_channel_scope(channel=1, user=_user([1])).username == "w01"
    with pytest.raises(HTTPException) as exc:
        require_channel_scope(channel=2, user=_user([1]))
    assert exc.value.status_code == 403
    assert "2 号工位" in exc.value.detail


def test_station_builtin_role_has_no_project_or_settings_power():
    """工位屏账号绝不能带项目/模型/设置权限 —— 本期禁止一体机做项目管理。"""
    from backend.core.permissions import BUILTIN_ROLES, match_permission

    perms = BUILTIN_ROLES["station"]["permissions"]
    assert match_permission("monitor.view", perms)
    assert match_permission("monitor.detection.control", perms)
    for forbidden in ("project.view", "project.edit", "project.activate",
                      "model.upload", "source.edit", "settings.view",
                      "settings.edit", "system.users.manage",
                      "monitor.layout.edit", "monitor.detection.advanced"):
        assert not match_permission(forbidden, perms), f"工位屏不该有 {forbidden}"


def test_station_role_is_seeded_into_builtin_roles():
    """_seed_auth_builtin_roles 按 BUILTIN_ROLES 建角色，station 要能被种出来。"""
    from backend.core.permissions import BUILTIN_ROLES
    assert set(BUILTIN_ROLES) == {"admin", "engineer", "operator", "station"}
    assert BUILTIN_ROLES["station"]["name"] == "工位屏"


# ============================================================
# 7. 端到端: 同源托管 + 接口仍归后端
# ============================================================

def test_root_serves_frontend_when_dist_present(client):
    """一体机输 http://<工作站IP>:8001 就该直接看到系统，不是一坨 JSON。"""
    from backend.core.lan_station import resolve_web_dist_dir

    if not resolve_web_dist_dir():
        pytest.skip("本机没有 frontend/dist 构建产物")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_unknown_api_path_still_returns_json_404(client):
    """静态兜底不能把打错的接口地址喂成 200 + 一张 index.html。"""
    resp = client.get("/api/v1/definitely-not-an-endpoint")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def test_business_routes_not_shadowed_by_static_hosting(client):
    """静态托管不能盖住任何业务路由。"""
    assert client.get("/health").json() == {"status": "healthy"}
    assert client.get("/api/v1/projects").status_code == 200


def test_non_get_methods_keep_original_status(client):
    """回归守门: 捕获路由只登记 GET 时, POST 未知根路径会从 404 变 405。

    ``mes_inbound`` 的别名测试与 monitor-layout 的非法 key 用例都断言 404,
    静态托管绝不能改写这些方法的错误语义 —— 所以兜底只在 GET/HEAD 且已 404
    的响应上生效。
    """
    for method in ("post", "put", "delete"):
        resp = getattr(client, method)("/definitely-not-a-route")
        assert resp.status_code == 404, f"{method.upper()} 应回 404, 实际 {resp.status_code}"


def test_runtime_registered_route_is_not_shadowed(client, app):
    """回归守门: 运行时才注册的路由（客户自定义入站别名 / 运行中启用的插件）必须仍然优先。

    捕获路由匹配一切路径且排在这些后注册的路由之前, 会把它们整条吞掉。
    """
    path = "/__lan_station_runtime_probe__"
    app.get(path)(lambda: {"ok": True})
    try:
        resp = client.get(path)
        assert resp.status_code == 200, f"动态注册的路由被静态托管盖住了: {resp.status_code}"
        assert resp.json() == {"ok": True}
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", None) != path
        ]


def test_non_html_unknown_path_is_not_spa_fallback(client):
    """图片/字体这类明确不要 HTML 的请求, 找不到就该 404, 不能塞一张 index.html。"""
    resp = client.get("/__no_such_asset__.png", headers={"Accept": "image/png"})
    assert resp.status_code == 404


def test_stream_viewers_endpoint_shape(client):
    resp = client.get("/api/v1/source/stream/viewers")
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    assert channels, "至少要有 0 号工位"
    for payload in channels.values():
        assert set(payload) >= {"viewers", "station", "main", "legacy"}
        assert isinstance(payload["station"], bool)
