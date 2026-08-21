"""v3.48.1 数据中心录像三修 的单元测试。

覆盖：
  1. GET /data/sessions/{id}/cycles?result=ok|ng 按判定结果过滤（前端"只看NG录像"）
  2. convert_video_for_browser 缓存原子性——半成品 .tmp 不会被当缓存命中；
     转码失败时 .tmp 清理干净并回退原文件
  3. 完整 v2 缓存直接命中不重转
"""
import os
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.db.database import SessionLocal
from backend.core.config import settings
from backend.models.models import DetectionSession, DetectionCycle
from backend.api import sessions as sessions_api


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


@pytest.fixture
def seeded_session():
    """一个会话 + 2 OK + 3 NG 周期，测完清理。"""
    db = SessionLocal()
    token = uuid.uuid4().hex[:8]
    sess = DetectionSession(session_uuid=f"vfix-{token}", start_time=datetime.now())
    db.add(sess)
    db.flush()
    for i, good in enumerate([True, True, False, False, False]):
        db.add(DetectionCycle(
            cycle_uuid=f"vfix-c{i}-{token}", session_id=sess.id,
            cycle_number=i + 1, start_time=datetime.now(), is_good=good,
        ))
    db.commit()
    sid = sess.id
    yield sid
    db.query(DetectionCycle).filter(DetectionCycle.session_id == sid).delete()
    db.query(DetectionSession).filter(DetectionSession.id == sid).delete()
    db.commit()
    db.close()


def test_cycles_result_filter_ng(client, seeded_session):
    r = client.get(f"/api/v1/data/sessions/{seeded_session}/cycles", params={"result": "ng"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert all(item["is_good"] is False for item in body["items"])


def test_cycles_result_filter_ok(client, seeded_session):
    r = client.get(f"/api/v1/data/sessions/{seeded_session}/cycles", params={"result": "ok"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all(item["is_good"] is True for item in body["items"])


def test_cycles_result_filter_default_all(client, seeded_session):
    r = client.get(f"/api/v1/data/sessions/{seeded_session}/cycles")
    assert r.status_code == 200
    assert r.json()["total"] == 5


# ---------- convert_video_for_browser 缓存原子性 ----------

def _mk_input(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"fake-not-a-real-video")
    return str(p)


def test_convert_failure_cleans_tmp_and_falls_back(tmp_path, monkeypatch):
    """ffmpeg 失败: 回退原文件, 且不留 .tmp 半成品、不留坏缓存。"""
    monkeypatch.setattr(settings, "RECORDING_DIR", str(tmp_path / "rec"))

    class _FailResult:
        returncode = 1
        stderr = "boom"

    monkeypatch.setattr(sessions_api.subprocess, "run", lambda *a, **k: _FailResult())
    monkeypatch.setattr(sessions_api, "get_cached_ffmpeg_path", lambda: "ffmpeg", raising=False)
    # v3.54: 本测试测的是转码路径, 探测强制不兼容 (真探测会被上面的 fake run 干扰)
    monkeypatch.setattr(sessions_api, "_is_browser_compatible_h264", lambda p: False)

    src = _mk_input(tmp_path)
    out = sessions_api.convert_video_for_browser(src)
    assert out == src, "转码失败应回退原文件"
    cache_dir = os.path.join(settings.RECORDING_DIR, "cache")
    leftovers = [f for f in os.listdir(cache_dir)] if os.path.isdir(cache_dir) else []
    assert not any(f.endswith(".tmp.mp4") for f in leftovers), f"残留半成品: {leftovers}"
    assert not any(f.endswith("_h264v2.mp4") for f in leftovers), f"失败不应产出缓存: {leftovers}"


def test_convert_success_atomic_rename(tmp_path, monkeypatch):
    """ffmpeg 成功: tmp 原子落位为 v2 缓存, 二次调用直接命中不再跑 ffmpeg。"""
    monkeypatch.setattr(settings, "RECORDING_DIR", str(tmp_path / "rec"))
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        with open(cmd[-1], "wb") as f:  # 最后一个参数是 tmp 输出路径
            f.write(b"converted")

        class _Ok:
            returncode = 0
            stderr = ""
        return _Ok()

    monkeypatch.setattr(sessions_api.subprocess, "run", _fake_run)
    monkeypatch.setattr(sessions_api, "get_cached_ffmpeg_path", lambda: "ffmpeg", raising=False)
    monkeypatch.setattr(sessions_api, "_is_browser_compatible_h264", lambda p: False)

    src = _mk_input(tmp_path)
    out1 = sessions_api.convert_video_for_browser(src)
    assert out1.endswith("_h264v2.mp4") and os.path.exists(out1)
    assert not os.path.exists(out1 + ".tmp.mp4")

    out2 = sessions_api.convert_video_for_browser(src)
    assert out2 == out1
    assert len(calls) == 1, "完整缓存应直接命中, 不重跑 ffmpeg"


def test_partial_tmp_never_served(tmp_path, monkeypatch):
    """预置一个半成品 .tmp（模拟历史超时被杀）: 不会被当缓存回传。"""
    monkeypatch.setattr(settings, "RECORDING_DIR", str(tmp_path / "rec"))
    cache_dir = os.path.join(settings.RECORDING_DIR, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    src = _mk_input(tmp_path)
    stale = os.path.join(cache_dir, "clip_h264v2.mp4.tmp.mp4")
    with open(stale, "wb") as f:
        f.write(b"partial")

    class _FailResult:
        returncode = 1
        stderr = "boom"

    monkeypatch.setattr(sessions_api.subprocess, "run", lambda *a, **k: _FailResult())
    monkeypatch.setattr(sessions_api, "get_cached_ffmpeg_path", lambda: "ffmpeg", raising=False)
    monkeypatch.setattr(sessions_api, "_is_browser_compatible_h264", lambda p: False)

    out = sessions_api.convert_video_for_browser(src)
    assert out == src
    assert not os.path.exists(stale), "失败路径应清掉半成品"
