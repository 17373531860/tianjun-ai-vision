# -*- coding: utf-8 -*-
"""v3.47 训练平台互连回归: 模型包接收 / 配置 / 采样决策 / 离线队列.

覆盖面:
- /api/v1/interconnect/health 契约响应
- /api/v1/interconnect/models/push: 未启用 403 / 令牌错 401 / 正常入库 (labels 顺序、
  meta.analysis、项目同名对齐) / 重复 409 / SHA 篡改 400 / 违禁载荷 400
- /api/v1/interconnect/config: 保存回读 / 置信度带校验 400
- sampler: 置信度带命中 / 未检出 (周期内守门) / 检出闪断 dropout / NG 标志消费 /
  限流 / bbox 左上角→中心点换算
- identity: device_id 生成稳定 (多设备身份)
- puller: 列包→下载→入库→游标推进 (模型拉取分发)
- InterconnectSampleQueue: enqueue 去重 / next_due 租约 / 流水状态
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import uuid
import zipfile
import base64
import datetime as dt
from types import SimpleNamespace

import pytest

API = "/api/v1/interconnect"


# ============================================================
# 工具
# ============================================================
def _build_package(tmp_path, *, name, version="1.0.0", artifact=b"fake-onnx-bytes",
                   fmt="onnx", role="primary", x_project=None, analysis=None,
                   sha_override=None, extra_member=None, contract="1.0.0"):
    pkg = tmp_path / f"{uuid.uuid4().hex}.yvmodel"
    art_path = "artifacts/model.onnx"
    sha = sha_override or hashlib.sha256(artifact).hexdigest()
    manifest = {
        "contractVersion": contract,
        "packageId": uuid.uuid4().hex,
        "name": name,
        "version": version,
        "releaseChannel": "stable",
        "createdAtUtc": "2026-08-07T00:00:00+00:00",
        "task": {"type": "detection"},
        "classes": [
            {"id": 1, "key": "defect_b", "displayName": "缺陷B"},
            {"id": 0, "key": "defect_a", "displayName": "划痕A"},
        ],
        "artifacts": [{
            "id": "onnx-main", "path": art_path, "format": fmt,
            "precision": "fp32", "role": role,
            "size": len(artifact), "sha256": sha,
        }],
        "provenance": {"producer": "yolovision", "trainingJobId": "job-001"},
        "extensions": {},
    }
    if x_project is not None:
        manifest["extensions"]["x-project-name"] = x_project
    if analysis is not None:
        manifest["extensions"]["x-analysis"] = analysis
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr(art_path, artifact)
        if extra_member is not None:
            zf.writestr(extra_member, b"payload")
    return pkg


def _push(client, pkg, token):
    with open(pkg, "rb") as f:
        return client.post(
            f"{API}/models/push",
            headers={"X-Interconnect-Token": token},
            files={"package": (pkg.name, f, "application/zip")},
        )


@pytest.fixture
def interconnect_enabled():
    """启用互连 + 配一个随机令牌, 测试结束恢复默认关。"""
    from backend.services.interconnect import config as icfg
    token = uuid.uuid4().hex
    icfg.save_config({"enabled": True, "token": token})
    yield token
    icfg.save_config({"enabled": False, "token": ""})


# ============================================================
# 契约端点
# ============================================================
def test_health_contract(client):
    resp = client.get(f"{API}/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["product"] == "tianjun-ai-vision"
    assert body["contract"] == "1.1"
    assert body["device_id"].startswith("tj-")


def test_license_lease_helpers_verify_raw_envelope_and_replay(
    tmp_path,
    monkeypatch,
):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from backend.services.interconnect import license_lease

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    license_lease.PUBLIC_KEY = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    machine_id = "TJ-TEST12345678"
    data = json.dumps(
        {
            "machineId": machine_id,
            "customerName": "Test Factory",
            "expiresAt": "2099-01-01T00:00:00Z",
        },
        separators=(",", ":"),
    )
    signature = key.sign(
        data.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    path = tmp_path / "license.lic"
    path.write_text(
        json.dumps(
            {
                "data": data,
                "signature": base64.b64encode(signature).decode("ascii"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TIANJUN_LICENSE_PATH", str(path))
    monkeypatch.setenv("TIANJUN_MACHINE_ID", machine_id)
    raw, payload = license_lease.read_verified_envelope()
    assert json.loads(raw)["data"] == data
    assert payload["machineId"] == machine_id

    license_lease.reset_nonce_cache_for_tests()
    nonce = base64.urlsafe_b64encode(b"x" * 24).decode("ascii").rstrip("=")
    now = dt.datetime.now(dt.timezone.utc)
    license_lease.validate_and_consume_nonce(nonce, now, now)
    with license_lease._nonce_lock:
        license_lease._seen_nonces.clear()
    with pytest.raises(license_lease.LeaseError, match="already used"):
        license_lease.validate_and_consume_nonce(nonce, now, now)
    with pytest.raises(license_lease.LeaseError, match="loopback"):
        license_lease.require_loopback("192.168.1.9")
    license_lease.require_loopback("127.0.0.1")
    license_lease.require_loopback("::1")


def test_license_lease_endpoint_rejects_non_loopback_test_client(
    app,
    interconnect_enabled,
):
    from fastapi.testclient import TestClient
    client = TestClient(
        app,
        client=("192.168.1.9", 50000),
    )
    nonce = base64.urlsafe_b64encode(b"n" * 24).decode("ascii").rstrip("=")
    response = client.post(
        f"{API}/license/lease",
        headers={"X-Interconnect-Token": interconnect_enabled},
        json={
            "contract": "1.1",
            "nonce": nonce,
            "requested_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 403


def test_interconnect_config_is_loopback_only_and_masks_token(
    app,
    client,
    interconnect_enabled,
):
    local = client.get(f"{API}/config")
    assert local.status_code == 200
    assert local.json()["token"] == ""
    assert local.json()["token_configured"] is True
    saved = client.put(
        f"{API}/config",
        json={"enabled": True},
    )
    assert saved.status_code == 200
    assert saved.json()["token"] == ""
    assert saved.json()["token_configured"] is True
    from backend.services.interconnect import config as icfg
    assert icfg.get_config()["token"] == interconnect_enabled

    from fastapi.testclient import TestClient
    remote = TestClient(
        app,
        client=("10.20.30.40", 50000),
    )
    assert remote.get(f"{API}/config").status_code == 403
    assert remote.put(
        f"{API}/config",
        json={"enabled": False},
    ).status_code == 403


def test_push_rejected_when_disabled(client, tmp_path):
    from backend.services.interconnect import config as icfg
    icfg.save_config({"enabled": False, "token": ""})
    pkg = _build_package(tmp_path, name=f"m-{uuid.uuid4().hex[:6]}")
    resp = _push(client, pkg, "whatever")
    assert resp.status_code == 403


def test_push_rejected_with_wrong_token(client, tmp_path, interconnect_enabled):
    pkg = _build_package(tmp_path, name=f"m-{uuid.uuid4().hex[:6]}")
    resp = _push(client, pkg, "wrong-token")
    assert resp.status_code == 401


def test_push_model_success_with_project_match(client, tmp_path, db_session,
                                               interconnect_enabled):
    from backend.models.models import Model, Project
    proj_name = f"互连项目-{uuid.uuid4().hex[:6]}"
    db_session.add(Project(name=proj_name, task_type="detection"))
    db_session.commit()

    analysis = {
        "schema": "1.0",
        "metrics": {"map50": 0.91, "map50_95": 0.75},
        "curves": {"epochs": [1, 2], "map50": [0.5, 0.91]},
        "training": {"trigger": "auto_retrain"},
    }
    model_name = f"模型-{uuid.uuid4().hex[:6]}"
    pkg = _build_package(tmp_path, name=model_name, version="1.2.0",
                         x_project=proj_name, analysis=analysis)
    resp = _push(client, pkg, interconnect_enabled)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["project_matched"] is True
    assert body["project_name"] == proj_name
    assert body["warnings"] == []

    row = db_session.query(Model).filter(Model.id == body["model_id"]).first()
    assert row is not None
    assert row.source == "yolovision"
    assert row.framework == "ONNX"
    assert row.labels == ["划痕A", "缺陷B"]  # 按 class id 排序
    assert row.meta["analysis"]["metrics"]["map50"] == 0.91
    assert row.meta["project_matched"] is True
    assert row.project_id is not None


def test_push_duplicate_conflict(client, tmp_path, interconnect_enabled):
    name = f"重复-{uuid.uuid4().hex[:6]}"
    pkg = _build_package(tmp_path, name=name, version="2.0.0")
    assert _push(client, pkg, interconnect_enabled).status_code == 200
    resp = _push(client, pkg, interconnect_enabled)
    assert resp.status_code == 409
    assert "model_id" in resp.json()["detail"]


def test_push_no_project_match_warns(client, tmp_path, interconnect_enabled):
    pkg = _build_package(tmp_path, name=f"孤儿-{uuid.uuid4().hex[:6]}",
                         x_project=f"不存在的项目-{uuid.uuid4().hex[:6]}")
    resp = _push(client, pkg, interconnect_enabled)
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_matched"] is False
    assert body["warnings"]


def test_push_tampered_sha_rejected(client, tmp_path, interconnect_enabled):
    pkg = _build_package(tmp_path, name=f"篡改-{uuid.uuid4().hex[:6]}",
                         sha_override="0" * 64)
    resp = _push(client, pkg, interconnect_enabled)
    assert resp.status_code == 400
    assert "SHA-256" in resp.json()["detail"]


def test_push_blocked_executable_rejected(client, tmp_path, interconnect_enabled):
    pkg = _build_package(tmp_path, name=f"载荷-{uuid.uuid4().hex[:6]}",
                         extra_member="docs/evil.exe")
    resp = _push(client, pkg, interconnect_enabled)
    assert resp.status_code == 400
    assert "违禁" in resp.json()["detail"]


def test_push_unsupported_contract_rejected(client, tmp_path, interconnect_enabled):
    pkg = _build_package(tmp_path, name=f"契约-{uuid.uuid4().hex[:6]}",
                         contract="2.0.0")
    resp = _push(client, pkg, interconnect_enabled)
    assert resp.status_code == 400


# ============================================================
# 配置端点
# ============================================================
def test_config_roundtrip(client):
    from backend.services.interconnect import config as icfg
    resp = client.put(f"{API}/config", json={
        "enabled": False,
        "platform_url": "http://192.168.1.10:8080",
        "sampling": {"enabled": True, "conf_min": 0.3, "conf_max": 0.7},
    })
    assert resp.status_code == 200, resp.text
    got = client.get(f"{API}/config").json()
    assert got["platform_url"] == "http://192.168.1.10:8080"
    assert got["sampling"]["conf_min"] == 0.3
    assert got["sampling"]["conf_max"] == 0.7
    # 恢复默认
    icfg.save_config({"enabled": False, "platform_url": "", "token": "",
                      "sampling": {"enabled": False}})


def test_config_rejects_invalid_conf_band(client):
    resp = client.put(f"{API}/config", json={
        "sampling": {"conf_min": 0.8, "conf_max": 0.3},
    })
    assert resp.status_code == 400


def test_config_rejects_bad_url(client):
    resp = client.put(f"{API}/config", json={"platform_url": "not-a-url"})
    assert resp.status_code == 400


def test_status_endpoint(client):
    resp = client.get(f"{API}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "uploader" in body
    assert "sampling_active" in body


# ============================================================
# 采样决策 (纯逻辑, 不起推理线程)
# ============================================================
def _sampling_cfg(**over):
    base = {
        "enabled": True, "low_conf_enabled": True, "conf_min": 0.2,
        "conf_max": 0.6, "no_detection_enabled": False,
        "ng_event_enabled": False, "include_annotations": True,
        "min_interval_s": 0.0, "max_per_hour": 100, "jpeg_quality": 80,
    }
    base.update(over)
    return base


def test_decide_reason_confidence_band():
    from backend.services.interconnect.sampler import _decide_reason
    cfg = _sampling_cfg()
    assert _decide_reason(cfg, [{"confidence": 0.4, "label": "a"}], False) == "low_confidence"
    assert _decide_reason(cfg, [{"confidence": 0.9, "label": "a"}], False) is None
    assert _decide_reason(cfg, [{"confidence": 0.1, "label": "a"}], False) is None
    # 隐藏框 (备用步骤) 不参与判定
    assert _decide_reason(cfg, [{"confidence": 0.4, "label": "a", "hidden": True}],
                          False) is None


def test_decide_reason_no_detection_and_ng():
    from backend.services.interconnect.sampler import _decide_reason
    assert _decide_reason(_sampling_cfg(no_detection_enabled=True), [], False) == "no_detection"
    assert _decide_reason(_sampling_cfg(no_detection_enabled=False), [], False) is None
    assert _decide_reason(_sampling_cfg(ng_event_enabled=True), [], True) == "ng_event"
    # NG 优先于其他原因
    assert _decide_reason(_sampling_cfg(ng_event_enabled=True),
                          [{"confidence": 0.4, "label": "a"}], True) == "ng_event"


def test_decide_reason_no_detection_cycle_gating():
    """空帧≠漏检: 周期外 (没有工件在检) 的空帧默认不采。"""
    from backend.services.interconnect.sampler import _decide_reason
    cfg = _sampling_cfg(no_detection_enabled=True)
    # 周期进行中 → 采 (工件在检却什么都没检出, 可疑)
    assert _decide_reason(cfg, [], False, in_cycle=True) == "no_detection"
    # 周期外 → 不采 (空转输送带的合法空景)
    assert _decide_reason(cfg, [], False, in_cycle=False) is None
    # 显式关掉周期守门 (连续监控类场景) → 周期外也采
    cfg2 = _sampling_cfg(no_detection_enabled=True,
                         no_detection_in_cycle_only=False)
    assert _decide_reason(cfg2, [], False, in_cycle=False) == "no_detection"


def test_decide_reason_dropout_priority():
    from backend.services.interconnect.sampler import _decide_reason
    cfg = _sampling_cfg(dropout_enabled=True, no_detection_enabled=True)
    # 闪断优先于 no_detection / low_confidence
    assert _decide_reason(cfg, [], False, in_cycle=True,
                          dropped_labels=["螺丝"]) == "detection_dropout"
    assert _decide_reason(cfg, [{"confidence": 0.4, "label": "a"}], False,
                          in_cycle=True,
                          dropped_labels=["螺丝"]) == "detection_dropout"
    # NG 优先于闪断
    cfg_ng = _sampling_cfg(dropout_enabled=True, ng_event_enabled=True)
    assert _decide_reason(cfg_ng, [], True, in_cycle=True,
                          dropped_labels=["螺丝"]) == "ng_event"
    # 周期外闪断不采 (周期结束目标移出画面是合法消失)
    assert _decide_reason(cfg, [], False, in_cycle=False,
                          dropped_labels=["螺丝"]) is None


def test_update_streaks_dropout_detection():
    """标签连续 ≥N 帧出现后突然消失 → 报闪断; 不足 N 帧的闪现不报。"""
    from backend.services.interconnect import sampler
    sampler.reset_rate_limit_state()
    cfg = _sampling_cfg(dropout_enabled=True, dropout_min_frames=3)
    ch = 42
    det = [{"label": "螺丝"}]
    # 连续 3 帧出现
    for _ in range(3):
        assert sampler._update_streaks(cfg, ch, det) == []
    # 第 4 帧消失 → 闪断
    assert sampler._update_streaks(cfg, ch, []) == ["螺丝"]
    # 归零后立刻再消失不重复报
    assert sampler._update_streaks(cfg, ch, []) == []
    # 只出现 2 帧就消失 (低于门槛) → 不报
    sampler._update_streaks(cfg, ch, det)
    sampler._update_streaks(cfg, ch, det)
    assert sampler._update_streaks(cfg, ch, []) == []
    # dropout 关闭时不追踪
    cfg_off = _sampling_cfg(dropout_enabled=False)
    for _ in range(5):
        assert sampler._update_streaks(cfg_off, ch, det) == []
    assert sampler._update_streaks(cfg_off, ch, []) == []
    sampler.reset_rate_limit_state()


def test_detections_to_annotations_center_conversion():
    from backend.services.interconnect.sampler import detections_to_annotations
    anns = detections_to_annotations([
        {"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.4, "confidence": 0.55, "label": "划痕"},
        {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1, "confidence": 0.9,
         "label": "隐藏", "hidden": True},
    ])
    assert len(anns) == 1
    a = anns[0]
    assert a["class_name"] == "划痕"
    assert a["cx"] == pytest.approx(0.2)
    assert a["cy"] == pytest.approx(0.4)
    assert a["w"] == pytest.approx(0.2)
    assert a["h"] == pytest.approx(0.4)


def test_maybe_sample_frame_enqueues_and_clears_ng_flag(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from backend.services.interconnect import config as icfg
    from backend.services.interconnect import sampler, uploader

    icfg.save_config({
        "enabled": True, "platform_url": "", "token": "t",
        "sampling": _sampling_cfg(ng_event_enabled=True),
    })
    sampler.reset_rate_limit_state()
    q = uploader.get_queue()
    before = q.count()

    frame = np.zeros((32, 48, 3), dtype=np.uint8)
    mgr = SimpleNamespace(project_config={"name": f"采样项目-{uuid.uuid4().hex[:6]}"},
                          channel_id=0)
    setattr(mgr, sampler.NG_FLAG_ATTR, True)
    sampler.maybe_sample_frame(mgr, frame,
                               [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2,
                                 "confidence": 0.4, "label": "a"}])
    assert getattr(mgr, sampler.NG_FLAG_ATTR) is False  # 标志被消费
    assert q.count() == before + 1
    recent = q.log_recent(1)[0]
    assert recent["reason"] == "ng_event"
    assert recent["status"] == "pending"
    # 清场: 把入队的帧取出删掉 + 关配置
    rec = q.next_due(lease_seconds=1.0)
    if rec is not None:
        q.delete(rec.sample_id)
    icfg.save_config({"enabled": False, "token": "",
                      "sampling": {"enabled": False}})


def test_rate_limit_min_interval():
    from backend.services.interconnect import sampler
    sampler.reset_rate_limit_state()
    cfg = _sampling_cfg(min_interval_s=3600.0)
    assert sampler._pass_rate_limit(cfg, channel_id=7) is True
    assert sampler._pass_rate_limit(cfg, channel_id=7) is False   # 间隔内被限
    assert sampler._pass_rate_limit(cfg, channel_id=8) is True    # 其他通道不受影响
    sampler.reset_rate_limit_state()


def test_rate_limit_hourly_cap():
    from backend.services.interconnect import sampler
    sampler.reset_rate_limit_state()
    cfg = _sampling_cfg(min_interval_s=0.0, max_per_hour=2)
    assert sampler._pass_rate_limit(cfg, channel_id=1) is True
    assert sampler._pass_rate_limit(cfg, channel_id=2) is True
    assert sampler._pass_rate_limit(cfg, channel_id=3) is False
    sampler.reset_rate_limit_state()


# ============================================================
# 设备身份 (多设备)
# ============================================================
def test_device_id_stable(client):
    from backend.services.interconnect.identity import get_device_id, get_identity
    a = get_device_id()
    b = get_device_id()
    assert a == b
    assert a.startswith("tj-")
    ident = get_identity()
    assert ident["device_id"] == a
    assert ident["device_name"]  # 主机名兜底非空


def test_ingest_meta_carries_device_identity(client, tmp_path):
    """采样入队的 meta 必须带 device_id/device_name (多设备台账依据)。"""
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from backend.services.interconnect import config as icfg
    from backend.services.interconnect import sampler, uploader

    icfg.save_config({"enabled": True, "token": "t",
                      "sampling": _sampling_cfg()})
    sampler.reset_rate_limit_state()
    q = uploader.get_queue()
    mgr = SimpleNamespace(project_config={"name": f"身份-{uuid.uuid4().hex[:6]}"},
                          channel_id=3, current_cycle_uuid="abc12345")
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    sampler.maybe_sample_frame(mgr, frame,
                               [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2,
                                 "confidence": 0.4, "label": "a"}])
    rec = q.next_due(lease_seconds=1.0)
    assert rec is not None
    assert rec.meta["device_id"].startswith("tj-")
    assert rec.meta["device_name"]
    assert rec.meta["context"]["in_cycle"] is True
    q.delete(rec.sample_id)
    icfg.save_config({"enabled": False, "token": "",
                      "sampling": {"enabled": False}})


# ============================================================
# 模型拉取分发 (puller)
# ============================================================
def test_puller_check_once_pulls_and_advances_cursor(
        client, tmp_path, db_session, interconnect_enabled, monkeypatch):
    from backend.models.models import Model
    from backend.services.interconnect import config as icfg
    from backend.services.interconnect import puller

    icfg.save_config({"enabled": True, "token": interconnect_enabled,
                      "platform_url": "http://fake-platform:9999"})

    model_name = f"拉取-{uuid.uuid4().hex[:6]}"
    pkg = _build_package(tmp_path, name=model_name, version="2.1.0")
    pkg_bytes = pkg.read_bytes()
    pkg_sha = hashlib.sha256(pkg_bytes).hexdigest()

    class _Resp:
        def __init__(self, status_code, json_data=None, content=b""):
            self.status_code = status_code
            self._json = json_data
            self.content = content

        def json(self):
            return self._json

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/download"):
            return _Resp(200, content=pkg_bytes)
        return _Resp(200, json_data={
            "contract": "1.1",
            "items": [{"package_id": "pkg-001", "model_name": model_name,
                       "version": "2.1.0", "sha256": pkg_sha}],
            "next_cursor": "cursor-after-001",
        })

    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    result = puller._check_once()
    assert result["pulled"] == 1, result
    assert puller._load_cursor() == "cursor-after-001"
    row = db_session.query(Model).filter(Model.name == model_name).first()
    assert row is not None and row.source == "yolovision"

    # 再拉同一包 → 409 冲突语义 = skipped, 游标继续推进
    def fake_get2(url, **kwargs):
        if url.endswith("/download"):
            return _Resp(200, content=pkg_bytes)
        return _Resp(200, json_data={
            "contract": "1.1",
            "items": [{"package_id": "pkg-001", "sha256": pkg_sha}],
            "next_cursor": "cursor-after-001-again",
        })
    monkeypatch.setattr(requests, "get", fake_get2)
    result2 = puller._check_once()
    assert result2["pulled"] == 0 and result2["skipped"] == 1, result2
    assert puller._load_cursor() == "cursor-after-001-again"

    # 清场
    db_session.delete(row)
    db_session.commit()
    puller._save_cursor("")


def test_puller_sha_mismatch_stops_without_cursor_advance(
        client, tmp_path, interconnect_enabled, monkeypatch):
    from backend.services.interconnect import config as icfg
    from backend.services.interconnect import puller

    icfg.save_config({"enabled": True, "token": interconnect_enabled,
                      "platform_url": "http://fake-platform:9999"})
    puller._save_cursor("cursor-before")

    class _Resp:
        def __init__(self, status_code, json_data=None, content=b""):
            self.status_code = status_code
            self._json = json_data
            self.content = content

        def json(self):
            return self._json

    def fake_get(url, **kwargs):
        if url.endswith("/download"):
            return _Resp(200, content=b"corrupted-bytes")
        return _Resp(200, json_data={
            "contract": "1.1",
            "items": [{"package_id": "pkg-x", "sha256": "f" * 64}],
            "next_cursor": "cursor-must-not-advance",
        })

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    result = puller._check_once()
    assert result["pulled"] == 0
    assert "SHA-256" in result["error"]
    assert puller._load_cursor() == "cursor-before"  # 游标未推进, 下轮重试
    puller._save_cursor("")


# ============================================================
# 离线队列
# ============================================================
def test_sample_queue_lifecycle(tmp_path):
    from backend.services.interconnect.sample_queue import InterconnectSampleQueue
    q = InterconnectSampleQueue(tmp_path / "q.db", max_items=10, ttl_seconds=3600)
    sid = uuid.uuid4().hex
    meta = {"sample_id": sid, "project_name": "p", "reason": "low_confidence"}
    assert q.enqueue(sample_id=sid, meta=meta, image=b"\xff\xd8jpeg") is True
    assert q.enqueue(sample_id=sid, meta=meta, image=b"\xff\xd8jpeg") is False  # 幂等
    assert q.count() == 1

    rec = q.next_due(lease_seconds=60.0)
    assert rec is not None and rec.sample_id == sid
    assert rec.image == b"\xff\xd8jpeg"
    assert q.next_due() is None  # 租约期内不重复领取

    q.reschedule(sid, retry_count=1, next_attempt_at=time.time() - 1,
                 last_error="网络异常")
    rec2 = q.next_due()
    assert rec2 is not None and rec2.retry_count == 1
    q.delete(sid)
    assert q.count() == 0


def test_sample_queue_log(tmp_path):
    from backend.services.interconnect.sample_queue import InterconnectSampleQueue
    q = InterconnectSampleQueue(tmp_path / "q2.db", max_items=10, ttl_seconds=3600)
    sid = uuid.uuid4().hex
    q.log_add(sample_id=sid, project_name="p", reason="no_detection", channel_id=2)
    q.log_update(sid, status="sent")
    items = q.log_recent(10)
    assert items[0]["sample_id"] == sid
    assert items[0]["status"] == "sent"
    assert q.log_counts().get("sent") == 1
