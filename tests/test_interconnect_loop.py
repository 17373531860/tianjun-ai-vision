# -*- coding: utf-8 -*-
"""v3.47 训练平台互连闭环集成: 模拟 YoloVision 对端做真 HTTP 双通路联调.

与 test_interconnect.py (单元/端点) 的分工: 本文件起一个线程内 HTTP 服务扮演
YoloVision (契约 1.1), 覆盖:
- 样本通路: 采样器 → 磁盘队列 → uploader HTTP multipart 推送 → 对端收到
  meta/图片/令牌 (成功 200 / 项目不存在 404 丢弃 / 网络退避重排)
- 检出闪断全链路: 连续 N 帧出现 → 消失 → detection_dropout 入队且
  context.dropped_labels 正确
- 模型通路 (拉取): puller 真 HTTP 列包 → 下载 → 入库 → 游标推进
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

TOKEN = "loop-test-token"


# ============================================================
# 模拟 YoloVision 对端 (契约 1.1)
# ============================================================
class _PeerState:
    def __init__(self):
        self.ingested = []          # 收到的 (meta_dict, image_bytes, token)
        self.known_projects = set()
        self.packages = []          # [{"package_id":..., "bytes":..., "sha256":...}]
        self.cursor_value = "cur-0"


class _PeerHandler(BaseHTTPRequestHandler):
    state: _PeerState = None  # 类属性, 测试注入

    def log_message(self, *args):  # 静音
        pass

    def _json(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/v1/interconnect/health":
            return self._json(200, {"status": "ok", "product": "yolovision",
                                    "version": "0.9.0", "contract": "1.1"})
        if path == "/api/v1/interconnect/packages":
            items = [{"package_id": p["package_id"], "sha256": p["sha256"],
                      "model_name": p.get("model_name", "")}
                     for p in self.state.packages]
            return self._json(200, {"contract": "1.1", "items": items,
                                    "next_cursor": self.state.cursor_value})
        for p in self.state.packages:
            if path == f"/api/v1/interconnect/packages/{p['package_id']}/download":
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(p["bytes"])))
                self.end_headers()
                self.wfile.write(p["bytes"])
                return
        self._json(404, {"detail": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/v1/interconnect/frames/ingest":
            return self._json(404, {"detail": "not found"})
        token = self.headers.get("X-Interconnect-Token", "")
        if token != TOKEN:
            return self._json(401, {"detail": "令牌校验失败"})
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        # 简易 multipart 解析: 取 meta JSON 字段 + image 二进制段
        boundary = self.headers["Content-Type"].split("boundary=")[1].encode()
        meta, image = None, b""
        for part in raw.split(b"--" + boundary):
            if b'name="meta"' in part:
                meta = json.loads(part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0])
            elif b'name="image"' in part:
                image = part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
        if meta is None:
            return self._json(400, {"detail": "缺 meta"})
        if meta.get("project_name") not in self.state.known_projects:
            return self._json(404, {"detail": f"项目不存在: {meta.get('project_name')}"})
        self.state.ingested.append((meta, image, token))
        return self._json(200, {"contract": "1.1", "accepted": True,
                                "duplicate": False, "dataset": "现场回流",
                                "warnings": []})


@pytest.fixture
def peer():
    """线程内模拟对端, 测试结束关停。"""
    state = _PeerState()
    _PeerHandler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PeerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    yield SimpleNamespace(url=url, state=state)
    server.shutdown()
    server.server_close()


@pytest.fixture
def loop_env(client, peer):
    """互连指向模拟对端 + 采样全开 (无限流), 结束恢复默认关。"""
    from backend.services.interconnect import config as icfg
    from backend.services.interconnect import sampler
    icfg.save_config({
        "enabled": True, "platform_url": peer.url, "token": TOKEN,
        "sampling": {
            "enabled": True, "low_conf_enabled": True,
            "conf_min": 0.2, "conf_max": 0.6,
            "no_detection_enabled": True, "no_detection_in_cycle_only": True,
            "dropout_enabled": True, "dropout_min_frames": 3,
            "ng_event_enabled": True, "include_annotations": True,
            "min_interval_s": 0.0, "max_per_hour": 1000, "jpeg_quality": 80,
        },
    })
    sampler.reset_rate_limit_state()
    yield peer
    _drain_queue()
    sampler.reset_rate_limit_state()
    icfg.save_config({"enabled": False, "token": "", "platform_url": "",
                      "sampling": {"enabled": False}})


def _drain_queue():
    from backend.services.interconnect.uploader import get_queue
    q = get_queue()
    while True:
        rec = q.next_due(lease_seconds=0.001)
        if rec is None:
            break
        q.delete(rec.sample_id)


def _mgr(project: str, channel: int = 0, cycle: str | None = "c1"):
    return SimpleNamespace(project_config={"name": project},
                           channel_id=channel, current_cycle_uuid=cycle)


def _frame():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    return np.zeros((32, 48, 3), dtype=np.uint8)


def _push_all_due():
    """把队列里所有到期样本立即推给对端 (直接驱动, 不等 daemon 轮询)。"""
    from backend.services.interconnect import config as icfg
    from backend.services.interconnect import uploader
    cfg = icfg.get_config()
    pushed = 0
    while True:
        rec = uploader.get_queue().next_due(lease_seconds=30.0)
        if rec is None:
            return pushed
        uploader._push_record(cfg, rec)
        pushed += 1


# ============================================================
# 样本通路闭环
# ============================================================
def test_loop_sample_push_end_to_end(loop_env):
    """采样 → 队列 → HTTP multipart → 对端收到 meta/图/令牌。"""
    from backend.services.interconnect import sampler
    peer = loop_env
    project = f"闭环-{uuid.uuid4().hex[:6]}"
    peer.state.known_projects.add(project)

    frame = _frame()
    sampler.maybe_sample_frame(_mgr(project), frame,
                               [{"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.4,
                                 "confidence": 0.45, "label": "划痕"}])
    assert _push_all_due() == 1
    assert len(peer.state.ingested) == 1
    meta, image, token = peer.state.ingested[0]
    assert token == TOKEN
    assert meta["contract"] == "1.1"
    assert meta["project_name"] == project
    assert meta["reason"] == "low_confidence"
    assert meta["device_id"].startswith("tj-")
    assert meta["context"]["in_cycle"] is True
    assert meta["annotations"][0]["class_name"] == "划痕"
    assert image.startswith(b"\xff\xd8")  # JPEG 魔数
    assert hashlib.sha256(image).hexdigest() == meta["image"]["sha256"]
    # 对端确认成功后队列应已清空
    from backend.services.interconnect.uploader import get_queue
    assert get_queue().count() == 0


def test_loop_unknown_project_dropped_not_retried(loop_env):
    """对端 404 (项目不存在) = 永久失败, 丢弃不堵队列。"""
    from backend.services.interconnect import sampler
    from backend.services.interconnect.uploader import get_queue
    project = f"对端没有-{uuid.uuid4().hex[:6]}"  # 故意不注册到对端

    sampler.maybe_sample_frame(_mgr(project), _frame(),
                               [{"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1,
                                 "confidence": 0.3, "label": "a"}])
    assert _push_all_due() == 1
    assert loop_env.state.ingested == []
    assert get_queue().count() == 0  # 已丢弃
    logs = [x for x in get_queue().log_recent(10)
            if x["project_name"] == project]
    assert logs and logs[0]["status"] == "failed"


def test_loop_dropout_full_chain(loop_env):
    """连续 3 帧出现 → 消失 → detection_dropout 推到对端。"""
    from backend.services.interconnect import sampler
    peer = loop_env
    project = f"闪断-{uuid.uuid4().hex[:6]}"
    peer.state.known_projects.add(project)

    mgr = _mgr(project, channel=9)
    frame = _frame()
    det = [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2,
            "confidence": 0.95, "label": "螺丝"}]  # 高置信度, 不触发置信度带
    for _ in range(3):
        sampler.maybe_sample_frame(mgr, frame, det)
    # 高置信度且未消失 → 前 3 帧不采
    from backend.services.interconnect.uploader import get_queue
    assert get_queue().count() == 0
    # 第 4 帧突然消失 (周期仍进行中) → 闪断
    # (无检出同时命中 no_detection, 但闪断优先)
    sampler.maybe_sample_frame(mgr, frame, [])
    assert _push_all_due() == 1
    meta = peer.state.ingested[-1][0]
    assert meta["reason"] == "detection_dropout"
    assert meta["context"]["dropped_labels"] == ["螺丝"]


def test_loop_no_detection_outside_cycle_not_sampled(loop_env):
    """周期外空帧不采 (空转输送带), 周期内空帧才采。"""
    from backend.services.interconnect import sampler
    from backend.services.interconnect.uploader import get_queue
    peer = loop_env
    project = f"空帧-{uuid.uuid4().hex[:6]}"
    peer.state.known_projects.add(project)
    frame = _frame()

    sampler.maybe_sample_frame(_mgr(project, cycle=None), frame, [])
    assert get_queue().count() == 0  # 周期外不采
    sampler.maybe_sample_frame(_mgr(project, cycle="c9"), frame, [])
    assert _push_all_due() == 1
    meta = peer.state.ingested[-1][0]
    assert meta["reason"] == "no_detection"
    assert meta["annotations"] == []  # 未检出帧不带预标注


# ============================================================
# 模型通路 (拉取) 闭环
# ============================================================
def _build_pkg_bytes(name: str) -> tuple[bytes, str]:
    artifact = b"loop-onnx" * 128
    manifest = {
        "contractVersion": "1.0.0",
        "packageId": uuid.uuid4().hex,
        "name": name,
        "version": "9.0.0",
        "createdAtUtc": "2026-08-07T00:00:00+00:00",
        "task": {"type": "detection"},
        "classes": [{"id": 0, "key": "a", "displayName": "缺陷A"}],
        "artifacts": [{
            "id": "onnx-main", "path": "artifacts/model.onnx",
            "format": "onnx", "precision": "fp32", "role": "primary",
            "size": len(artifact),
            "sha256": hashlib.sha256(artifact).hexdigest(),
        }],
        "provenance": {"producer": "yolovision"},
        "extensions": {},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("artifacts/model.onnx", artifact)
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def test_loop_model_pull_end_to_end(loop_env, db_session):
    """puller 真 HTTP: 列包 → 下载 → 入库 → 游标推进 → 再拉幂等。"""
    from backend.models.models import Model
    from backend.services.interconnect import puller
    peer = loop_env

    name = f"拉取闭环-{uuid.uuid4().hex[:6]}"
    pkg_bytes, pkg_sha = _build_pkg_bytes(name)
    peer.state.packages.append({"package_id": "pkg-loop-1", "bytes": pkg_bytes,
                                "sha256": pkg_sha, "model_name": name})
    peer.state.cursor_value = "cur-1"
    puller._save_cursor("")

    result = puller.pull_now()
    assert result["pulled"] == 1, result
    assert puller._load_cursor() == "cur-1"
    row = db_session.query(Model).filter(Model.name == name).first()
    assert row is not None
    assert row.source == "yolovision"
    assert row.framework == "ONNX"

    # 幂等: 同包再拉 → skipped
    peer.state.cursor_value = "cur-2"
    result2 = puller.pull_now()
    assert result2["pulled"] == 0 and result2["skipped"] == 1, result2
    assert puller._load_cursor() == "cur-2"

    db_session.delete(row)
    db_session.commit()
    puller._save_cursor("")


def test_loop_test_peer_health(loop_env):
    """test_peer 探活模拟对端。"""
    from backend.services.interconnect.uploader import test_peer
    result = test_peer(loop_env.url)
    assert result["reachable"] is True
    assert result["product"] == "yolovision"
    assert result["contract"] == "1.1"
