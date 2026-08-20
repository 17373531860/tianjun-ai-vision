"""录像归档 二~四期 单元测试 (v3.53)。

覆盖：
  二期: NG 关键帧落盘/反查/跟随归档、sidecar 伴随报告、证据包 zip (规则级 +
        手动批量)、事件切片 (ffmpeg 可用时)
  三期: cycle.archived_* 字段填充、归档成功联动 (插件 hook + 网关事件) 触发
  四期: 凭据加密 (加密/解密/打码/回传合并)、时间窗判定与 defer 不耗重试预算、
        插件 adapter 注册与分发、限速读、历史回补、归档后删源、API 扩展面
        (adapter-types / dest_config 打码 / 时间窗校验 / evidence-pack)
"""
import io
import json
import os
import time
import zipfile
from datetime import datetime

import pytest

from backend.db.database import SessionLocal
from backend.models.models import DetectionCycle, DetectionSession
from backend.models.archive_models import VideoArchiveLog, VideoArchiveRule
from backend.services import video_archive as va
from backend.services import archive_adapters as aa
from backend.services import archive_media as am
from backend.services import archive_secrets as sec


# ============================================================
# 工具 (与 test_video_archive.py 同款)
# ============================================================

def _mk_cycle(db, tmp_path, *, is_good=False, channel_id=0, suffix="",
              content=b"FAKE_MP4_CONTENT" * 64):
    now = datetime.now()
    sess = DetectionSession(
        session_uuid=f"vae-s-{time.time_ns()}{suffix}",
        start_time=now, channel_id=channel_id,
    )
    db.add(sess)
    db.flush()
    src = os.path.join(str(tmp_path), f"cycle_src_{time.time_ns()}{suffix}.mp4")
    with open(src, "wb") as f:
        f.write(content)
    cyc = DetectionCycle(
        cycle_uuid=f"vae-c-{time.time_ns()}{suffix}",
        session_id=sess.id,
        start_time=now, end_time=now,
        is_good=is_good,
        video_path=src,
    )
    db.add(cyc)
    db.commit()
    db.refresh(cyc)
    return cyc, src


def _mk_rule(db, dest_dir, **kw):
    defaults = dict(
        name="证据测试规则", enabled=True, result_filter="all",
        dest_dir=str(dest_dir), subdir_by_date=False,
        filename_template="{{ cycle.id }}_{{ 'OK' if cycle.is_good else 'NG' }}.mp4",
        overwrite_policy="rename",
    )
    defaults.update(kw)
    r = VideoArchiveRule(**defaults)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        s = SessionLocal()
        try:
            s.query(VideoArchiveRule).delete()
            s.query(VideoArchiveLog).delete()
            s.commit()
        finally:
            s.close()
        try:
            if os.path.exists(va._SPOOL_FILE):
                os.remove(va._SPOOL_FILE)
        except OSError:
            pass
        va.refresh_rules_cache()
    _wipe()
    yield
    va.stop_worker(timeout=2)
    _wipe()


# ============================================================
# 四期: 凭据加密
# ============================================================

class TestSecrets:
    def test_roundtrip(self):
        enc = sec.encrypt_value("my_password_123")
        assert enc.startswith("enc:")
        assert sec.decrypt_value(enc) == "my_password_123"

    def test_encrypt_idempotent(self):
        enc = sec.encrypt_value("abc")
        assert sec.encrypt_value(enc) == enc  # 已加密不再套一层

    def test_plaintext_passthrough_on_decrypt(self):
        assert sec.decrypt_value("plain") == "plain"

    def test_config_encrypt_only_sensitive(self):
        cfg = sec.encrypt_config({"host": "1.2.3.4", "password": "pw",
                                  "secret_key": "sk", "port": 21})
        assert cfg["host"] == "1.2.3.4"
        assert cfg["port"] == 21
        assert cfg["password"].startswith("enc:")
        assert cfg["secret_key"].startswith("enc:")
        dec = sec.decrypt_config(cfg)
        assert dec["password"] == "pw" and dec["secret_key"] == "sk"

    def test_mask_and_merge(self):
        stored = sec.encrypt_config({"host": "h", "password": "pw"})
        masked = sec.mask_config(stored)
        assert masked["password"] == "******"
        # 前端回传 ****** = 不改 → 合并回旧值
        merged = sec.merge_masked_config({"host": "h2", "password": "******"},
                                         stored)
        assert merged["host"] == "h2"
        assert merged["password"] == stored["password"]


# ============================================================
# 四期: 时间窗
# ============================================================

class TestWindow:
    def test_validate(self):
        assert va.validate_window(None) is None
        assert va.validate_window("22:00-06:00") is None
        assert va.validate_window("25:00-06:00") is not None
        assert va.validate_window("abc") is not None

    def test_in_window_normal_and_overnight(self):
        dt = datetime(2026, 8, 19, 23, 30)
        assert va._in_window("22:00-06:00", dt) is True
        assert va._in_window("00:00-06:00", dt) is False
        dt2 = datetime(2026, 8, 19, 12, 0)
        assert va._in_window("22:00-06:00", dt2) is False
        assert va._in_window(None, dt2) is True
        assert va._in_window("08:00-08:00", dt2) is True  # 起止相同=全天

    def test_window_defers_without_consuming_attempts(self, db, tmp_path):
        cyc, src = _mk_cycle(db, tmp_path)
        # 造一个此刻必然在窗外的窗口 (当前时刻 +2h ~ +3h)
        now = datetime.now()
        h1, h2 = (now.hour + 2) % 24, (now.hour + 3) % 24
        _mk_rule(db, tmp_path / "wd", active_window=f"{h1:02d}:00-{h2:02d}:00")
        va.refresh_rules_cache()
        va.stop_worker(timeout=2)
        va._stop_event.clear()  # 直调 _run_task 需要等待循环可运行
        task = {"kind": "cycle", "cycle_uuid": cyc.cycle_uuid,
                "filepath": src, "channel_id": 0, "attempts": 5}
        va._run_task(task)
        spooled = va._spool_take_all()
        assert len(spooled) == 1
        assert spooled[0]["attempts"] == 5, "窗外等待不应消耗重试预算"

    def test_test_run_ignores_window(self, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path)
        now = datetime.now()
        h1, h2 = (now.hour + 2) % 24, (now.hour + 3) % 24
        rule = _mk_rule(db, tmp_path / "wt",
                        active_window=f"{h1:02d}:00-{h2:02d}:00")
        results = va.archive_cycle_now(cyc.id, rule_id=rule.id)
        assert results[0]["status"] == "success", "指定规则试归档应无视时间窗"


# ============================================================
# 四期: adapter 注册与分发 / 限速读
# ============================================================

class TestAdapters:
    def test_unknown_type_permanent(self):
        with pytest.raises(aa.PermanentDeliveryError):
            aa.get_adapter("carrier_pigeon")

    def test_plugin_adapter_roundtrip(self, db, tmp_path):
        calls = []

        def fake_deliver(src, subdir, filename, cfg, throttle):
            calls.append({"src": src, "subdir": subdir, "filename": filename,
                          "cfg": cfg, "throttle": throttle})
            return f"fake://dest/{filename}"

        aa.register_archive_adapter("utest", fake_deliver)
        try:
            assert "plugin:utest" in aa.list_adapter_types()
            cyc, src = _mk_cycle(db, tmp_path)
            _mk_rule(db, tmp_path / "unused", dest_type="plugin:utest",
                     dest_config={"password": sec.encrypt_value("pw"),
                                  "host": "h"},
                     bandwidth_limit_kbps=64)
            results = va.archive_cycle_now(cyc.id)
            assert results[0]["status"] == "success"
            assert results[0]["dest_path"].startswith("fake://dest/")
            assert len(calls) == 1
            # dest_config 传给 adapter 前敏感字段已解密
            assert calls[0]["cfg"]["password"] == "pw"
            assert calls[0]["throttle"] == 64
            # 台账落了最终地址
            log = db.query(VideoArchiveLog).filter(
                VideoArchiveLog.cycle_id == cyc.id).first()
            assert log.status == "success"
            assert log.dest_path.startswith("fake://dest/")
        finally:
            aa.unregister_archive_adapter("utest")

    def test_unregistered_plugin_adapter_is_permanent_failure(self, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path)
        _mk_rule(db, tmp_path / "x", dest_type="plugin:ghost")
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "failed"
        assert not results[0]["transient"]
        assert "未注册" in results[0]["error"]

    def test_throttled_reader_correctness(self, tmp_path):
        data = os.urandom(600 * 1024)
        p = tmp_path / "t.bin"
        p.write_bytes(data)
        with open(p, "rb") as f:
            r = aa._ThrottledReader(f, None)
            assert r.read() == data
        with open(p, "rb") as f:
            r = aa._ThrottledReader(f, 100000)  # 限速但只验证数据完整
            chunks = []
            while True:
                c = r.read(7000)
                if not c:
                    break
                chunks.append(c)
            assert b"".join(chunks) == data

    def test_local_throttle_copies_intact(self, db, tmp_path):
        cyc, src = _mk_cycle(db, tmp_path, content=os.urandom(300 * 1024))
        dest = tmp_path / "thr"
        _mk_rule(db, dest, bandwidth_limit_kbps=100000)
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        out = dest / f"{cyc.id}_NG.mp4"
        assert out.read_bytes() == open(src, "rb").read()


# ============================================================
# 二期: 关键帧
# ============================================================

class TestKeyframe:
    def _frame(self):
        import numpy as np
        return np.zeros((120, 160, 3), dtype=np.uint8)

    def test_save_and_find(self):
        uuid = f"kf-{time.time_ns()}"
        path = am.save_ng_keyframe(self._frame(), uuid, channel_id=1,
                                   watermark=True)
        assert path and os.path.isfile(path)
        assert am.find_keyframe(uuid) == path
        assert am.find_keyframe("nonexistent-uuid") is None

    def test_keyframe_follows_archive(self, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path, is_good=False)
        am.save_ng_keyframe(self._frame(), cyc.cycle_uuid, watermark=False)
        dest = tmp_path / "kfdest"
        _mk_rule(db, dest, attach_keyframe=True)
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        assert (dest / f"{cyc.id}_NG.mp4").exists()
        assert (dest / f"{cyc.id}_NG.jpg").exists(), "关键帧应随录像交付"

    def test_ok_cycle_never_gets_keyframe(self, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path, is_good=True)
        dest = tmp_path / "kfok"
        _mk_rule(db, dest, attach_keyframe=True)
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        assert not (dest / f"{cyc.id}_OK.jpg").exists()

    def test_keyframe_gate_flags(self, db, tmp_path):
        assert va.keyframe_wanted() is False
        _mk_rule(db, tmp_path / "g", attach_keyframe=True,
                 keyframe_watermark=False)
        va.refresh_rules_cache()
        assert va.keyframe_wanted() is True
        assert va.keyframe_watermark_wanted() is False

    def test_save_keyframe_async_writes_file(self):
        uuid = f"kfa-{time.time_ns()}"
        am.save_keyframe_async(
            self._frame(),
            [{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3,
              "label": "箱子", "confidence": 0.9}],
            {"cycle_uuid": uuid, "channel_id": 0}, drawer=None)
        deadline = time.time() + 5
        while time.time() < deadline and not am.find_keyframe(uuid):
            time.sleep(0.1)
        assert am.find_keyframe(uuid), "异步线程应把关键帧落盘"


# ============================================================
# 二期: sidecar 伴随报告
# ============================================================

class TestSidecar:
    def _mk_template(self, db):
        from backend.models.export_models import ExportTemplate
        tpl = ExportTemplate(
            name=f"归档sidecar测试_{time.time_ns()}", format="txt",
            content="cycle={{ cycle.id }} result={{ cycle.result }}",
        )
        db.add(tpl)
        db.commit()
        db.refresh(tpl)
        return tpl

    def test_sidecar_rendered_next_to_video(self, db, tmp_path):
        tpl = self._mk_template(db)
        cyc, _ = _mk_cycle(db, tmp_path, is_good=False)
        dest = tmp_path / "scdest"
        _mk_rule(db, dest, sidecar_template_id=tpl.id)
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        sidecar = dest / f"{cyc.id}_NG.txt"
        assert sidecar.exists(), "sidecar 报告应与录像同 basename 落位"
        body = sidecar.read_text(encoding="utf-8")
        assert f"cycle={cyc.id}" in body and "result=NG" in body

    def test_missing_template_does_not_block_video(self, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path)
        dest = tmp_path / "scmiss"
        _mk_rule(db, dest, sidecar_template_id=999999)
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success", "模板缺失不推翻录像归档"
        assert (dest / f"{cyc.id}_NG.mp4").exists()


# ============================================================
# 二期: 证据包 (规则级 bundle_zip + 手动批量)
# ============================================================

class TestEvidencePack:
    def test_bundle_zip_rule(self, db, tmp_path):
        import numpy as np
        cyc, src = _mk_cycle(db, tmp_path, is_good=False)
        am.save_ng_keyframe(np.zeros((60, 80, 3), dtype=np.uint8),
                            cyc.cycle_uuid, watermark=False)
        dest = tmp_path / "zipdest"
        _mk_rule(db, dest, bundle_zip=True, attach_keyframe=True)
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        zpath = dest / f"{cyc.id}_NG.zip"
        assert zpath.exists()
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            assert f"{cyc.id}_NG.mp4" in names
            assert f"{cyc.id}_NG.jpg" in names
            assert f"{cyc.id}_NG_meta.json" in names
            meta = json.loads(zf.read(f"{cyc.id}_NG_meta.json"))
            assert meta["cycle_id"] == cyc.id and meta["result"] == "NG"
            assert zf.read(f"{cyc.id}_NG.mp4") == open(src, "rb").read()

    def test_manual_pack_service(self, db, tmp_path):
        c1, _ = _mk_cycle(db, tmp_path, is_good=False, suffix="a")
        c2, _ = _mk_cycle(db, tmp_path, is_good=False, suffix="b")
        out = str(tmp_path / "pack.zip")
        result = va.build_evidence_pack(db, [c1.id, c2.id], out)
        assert result["ok"] and result["included"] == 2
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            assert f"cycle_{c1.id}_NG/video.mp4" in names
            assert f"cycle_{c1.id}_NG/meta.json" in names
            assert f"cycle_{c2.id}_NG/video.mp4" in names

    def test_manual_pack_empty(self, db, tmp_path):
        result = va.build_evidence_pack(db, [99999999], str(tmp_path / "e.zip"))
        assert not result["ok"]


# ============================================================
# 二期: 事件切片 (真 ffmpeg, 不可用时 skip)
# ============================================================

def _ffmpeg_available():
    try:
        from backend.api.source import get_cached_ffmpeg_path
        import subprocess
        p = get_cached_ffmpeg_path()
        return subprocess.run([p, "-version"], capture_output=True,
                              timeout=10).returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _ffmpeg_available(), reason="本机无可用 ffmpeg")
class TestClipTail:
    def _mk_real_video(self, tmp_path, seconds=4):
        """用 ffmpeg 造一个真实短视频 (-g 10 每秒一个关键帧, 让 -c copy 可切)。"""
        import subprocess
        from backend.api.source import get_cached_ffmpeg_path
        out = str(tmp_path / f"real_{time.time_ns()}.mp4")
        subprocess.run(
            [get_cached_ffmpeg_path(), "-y", "-f", "lavfi",
             "-i", f"testsrc=duration={seconds}:size=160x120:rate=10",
             "-g", "10", "-pix_fmt", "yuv420p", out],
            capture_output=True, timeout=60, check=True)
        return out

    def test_clip_tail_shorter_than_source(self, db, tmp_path):
        real = self._mk_real_video(tmp_path, seconds=4)
        out = str(tmp_path / "clip.mp4")
        am.clip_tail(real, out, seconds=1)
        assert os.path.isfile(out)
        assert 0 < os.path.getsize(out) < os.path.getsize(real)

    def test_clip_rule_end_to_end(self, db, tmp_path):
        real = self._mk_real_video(tmp_path, seconds=4)
        now = datetime.now()
        sess = DetectionSession(session_uuid=f"clip-s-{time.time_ns()}",
                                start_time=now, channel_id=0)
        db.add(sess)
        db.flush()
        cyc = DetectionCycle(cycle_uuid=f"clip-c-{time.time_ns()}",
                             session_id=sess.id, start_time=now, end_time=now,
                             is_good=False, video_path=real)
        db.add(cyc)
        db.commit()
        db.refresh(cyc)
        dest = tmp_path / "clipdest"
        _mk_rule(db, dest, transform="clip_tail", clip_seconds=1)
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        out = dest / f"{cyc.id}_NG.mp4"
        assert out.exists()
        assert out.stat().st_size < os.path.getsize(real)


# ============================================================
# 三期: archived_* 字段 + 联动触发
# ============================================================

class TestEcosystem:
    def test_archived_fields_in_cycle_context(self, db, tmp_path):
        from backend.services.export_context import build_cycle_context
        cyc, _ = _mk_cycle(db, tmp_path, is_good=False)
        ctx = build_cycle_context(db, cyc.id)
        assert ctx["cycle"]["archived_video_path"] is None
        assert ctx["cycle"]["archive_status"] is None
        dest = tmp_path / "fdest"
        _mk_rule(db, dest)
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        ctx = build_cycle_context(db, cyc.id)
        assert ctx["cycle"]["archived_video_path"] == str(dest / f"{cyc.id}_NG.mp4")
        assert ctx["cycle"]["archive_status"] == "success"
        assert ctx["cycle"]["archived_at"]

    def test_registry_has_new_fields(self):
        from backend.services.export_field_registry import ALL_FIELDS
        paths = {f.path for f in ALL_FIELDS}
        for p in ("cycle.archived_video_path", "cycle.archived_at",
                  "cycle.archive_status", "aggregations.archive_success",
                  "aggregations.archive_failed",
                  "aggregations.archive_last_dest"):
            assert p in paths, p

    def test_range_context_counts_archives(self, db, tmp_path):
        from backend.services.export_context import build_range_context
        cyc, _ = _mk_cycle(db, tmp_path, is_good=False)
        _mk_rule(db, tmp_path / "rc")
        va.archive_cycle_now(cyc.id)
        today = datetime.now().strftime("%Y-%m-%d")
        ctx = build_range_context(db, start_date=today, end_date=today)
        assert ctx["aggregations"]["archive_success"] >= 1
        assert ctx["aggregations"]["archive_last_dest"]

    def test_hooks_fired_on_success(self, db, tmp_path, monkeypatch):
        from backend.services import archive_ecosystem as eco
        fired = {"plugin": None, "gateway": None}
        monkeypatch.setattr(eco, "_fire_plugin_hook",
                            lambda payload: fired.__setitem__("plugin", payload))
        monkeypatch.setattr(eco, "_fire_gateway_event",
                            lambda db_, cyc, payload: fired.__setitem__("gateway", payload))
        cyc, _ = _mk_cycle(db, tmp_path, is_good=False)
        _mk_rule(db, tmp_path / "hk")
        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        assert fired["plugin"] is not None
        assert fired["plugin"]["cycle_id"] == cyc.id
        assert fired["plugin"]["dest_path"] == results[0]["dest_path"]
        assert fired["gateway"] is not None

    def test_sms_summary_archive_params(self, db, tmp_path):
        from backend.services.sms_service import (SummaryPayload,
                                                  SummaryChannelMetrics)
        cyc, _ = _mk_cycle(db, tmp_path, is_good=False)
        _mk_rule(db, tmp_path / "sms")
        va.archive_cycle_now(cyc.id)
        now = datetime.now()
        payload = SummaryPayload(
            window_start=now.replace(hour=0, minute=0, second=0),
            window_end=now.replace(hour=23, minute=59, second=59),
            channels=(SummaryChannelMetrics(channel_id=0, ok_count=1,
                                            ng_count=1),),
        )
        params = payload.template_params()
        assert int(params["archive_success"]) >= 1


# ============================================================
# 四期: 历史回补 + 归档后删源
# ============================================================

class TestGovernance:
    def test_backfill_enqueues_and_archives(self, db, tmp_path):
        c1, _ = _mk_cycle(db, tmp_path, is_good=False, suffix="bf1")
        c2, _ = _mk_cycle(db, tmp_path, is_good=True, suffix="bf2")
        dest = tmp_path / "bfdest"
        rule = _mk_rule(db, dest, result_filter="ng_only",
                        overwrite_policy="skip")
        result = va.backfill_rule(rule.id)  # 自动拉起 worker 异步执行
        assert result["enqueued"] >= 2
        deadline = time.time() + 15
        target = dest / f"{c1.id}_NG.mp4"
        while time.time() < deadline and not (
                target.exists() and va._task_queue.qsize() == 0):
            time.sleep(0.2)
        assert target.exists(), "NG 周期应被回补"
        assert not (dest / f"{c2.id}_OK.mp4").exists(), "OK 周期不命中 ng_only"

    def test_backfill_unknown_rule(self):
        assert va.backfill_rule(999999).get("error")

    def test_delete_source_after(self, db, tmp_path):
        cyc, src = _mk_cycle(db, tmp_path, is_good=False, suffix="del")
        dest = tmp_path / "deldest"
        _mk_rule(db, dest, delete_source_after=True)
        va.refresh_rules_cache()
        va.stop_worker(timeout=2)
        va._stop_event.clear()
        task = {"kind": "cycle", "cycle_uuid": cyc.cycle_uuid,
                "filepath": src, "channel_id": 0, "attempts": 0}
        va._run_task(task)
        assert (dest / f"{cyc.id}_NG.mp4").exists()
        assert not os.path.exists(src), "归档成功后本地源应被删除"
        db.expire_all()
        row = db.query(DetectionCycle).filter(
            DetectionCycle.id == cyc.id).first()
        assert row.video_path is None, "video_path 应回写为空 (回放入口消失)"

    def test_delete_source_not_triggered_on_failure(self, db, tmp_path):
        cyc, src = _mk_cycle(db, tmp_path, is_good=False, suffix="del2")
        _mk_rule(db, tmp_path / "ok_dest", delete_source_after=True)
        bad = _mk_rule(db, tmp_path, delete_source_after=False, name="坏规则")
        bad.dest_dir = "/etc"  # 绕过 API 校验模拟坏数据
        db.commit()
        va.refresh_rules_cache()
        va.stop_worker(timeout=2)
        va._stop_event.clear()
        task = {"kind": "cycle", "cycle_uuid": cyc.cycle_uuid,
                "filepath": src, "channel_id": 0, "attempts": 0}
        va._run_task(task)
        assert os.path.exists(src), "任一规则失败时绝不删源"


# ============================================================
# API 扩展面
# ============================================================

class TestApiV2:
    def test_adapter_types_endpoint(self, client):
        r = client.get("/api/v1/export/video-archive/adapter-types")
        assert r.status_code == 200
        types = r.json()["types"]
        for t in ("local_dir", "ftp", "sftp", "s3", "http"):
            assert t in types

    def test_dest_config_masked_and_preserved(self, client, tmp_path):
        r = client.post("/api/v1/export/video-archive/rules", json={
            "name": "远端规则", "dest_dir": "质量部FTP", "dest_type": "ftp",
            "dest_config": {"host": "1.2.3.4", "password": "topsecret"},
        })
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        assert r.json()["dest_config"]["password"] == "******"
        assert r.json()["dest_config"]["host"] == "1.2.3.4"
        # 库里是密文
        s = SessionLocal()
        try:
            row = s.query(VideoArchiveRule).filter(
                VideoArchiveRule.id == rid).first()
            assert row.dest_config["password"].startswith("enc:")
            old_cipher = row.dest_config["password"]
        finally:
            s.close()
        # 回传 ****** 不改密码
        r = client.put(f"/api/v1/export/video-archive/rules/{rid}", json={
            "dest_config": {"host": "5.6.7.8", "password": "******"},
        })
        assert r.status_code == 200
        s = SessionLocal()
        try:
            row = s.query(VideoArchiveRule).filter(
                VideoArchiveRule.id == rid).first()
            assert row.dest_config["host"] == "5.6.7.8"
            assert row.dest_config["password"] == old_cipher
        finally:
            s.close()

    def test_remote_dest_skips_dir_guard(self, client):
        r = client.post("/api/v1/export/video-archive/rules", json={
            "name": "远端不查目录", "dest_dir": "随便一个标签",
            "dest_type": "sftp", "dest_config": {"host": "h"},
        })
        assert r.status_code == 200, r.text

    def test_window_validation(self, client, tmp_path):
        r = client.post("/api/v1/export/video-archive/rules", json={
            "name": "坏窗口", "dest_dir": str(tmp_path / "w"),
            "active_window": "25:99-06:00",
        })
        assert r.status_code == 400
        assert "时间窗" in r.json()["detail"]

    def test_new_fields_roundtrip(self, client, tmp_path):
        r = client.post("/api/v1/export/video-archive/rules", json={
            "name": "全字段", "dest_dir": str(tmp_path / "full"),
            "attach_keyframe": True, "bundle_zip": True,
            "transform": "clip_tail", "clip_seconds": 30,
            "active_window": "22:00-06:00", "bandwidth_limit_kbps": 512,
            "delete_source_after": True,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["attach_keyframe"] is True
        assert body["transform"] == "clip_tail"
        assert body["clip_seconds"] == 30
        assert body["active_window"] == "22:00-06:00"
        assert body["bandwidth_limit_kbps"] == 512
        assert body["delete_source_after"] is True

    def test_evidence_pack_endpoint(self, client, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path, is_good=False)
        r = client.post("/api/v1/export/video-archive/evidence-pack",
                        json={"cycle_ids": [cyc.id], "ng_only": True})
        assert r.status_code == 200, r.text
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert any(n.endswith("video.mp4") for n in zf.namelist())

    def test_evidence_pack_no_match(self, client):
        r = client.post("/api/v1/export/video-archive/evidence-pack",
                        json={"cycle_ids": [99999999]})
        assert r.status_code == 404

    def test_backfill_endpoint(self, client, db, tmp_path):
        cyc, _ = _mk_cycle(db, tmp_path, is_good=False)
        dest = tmp_path / "bfapi"
        rule = _mk_rule(db, dest, overwrite_policy="skip")
        va.stop_worker(timeout=2)
        r = client.post("/api/v1/export/video-archive/backfill",
                        json={"rule_id": rule.id, "limit": 100})
        assert r.status_code == 200, r.text
        assert r.json()["enqueued"] >= 1
