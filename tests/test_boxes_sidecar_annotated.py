# -*- coding: utf-8 -*-
"""检测框 sidecar 与带框渲染 (2026-09) 单元测试。

覆盖:
- BoxesSidecarCollector: run-length 去重 / 脏数据丢弃 / 上限护栏 / flush 落盘往返
- render_annotated: 真 ffmpeg 渲染 roundtrip (小视频; ffmpeg 缺失时跳过)
- 归档集成: rule.annotated_video 命中渲染 / 渲染失败降级原片 / 无 sidecar 降级原片
- API 契约: /data/videos/{id}/boxes 200/404、export-settings record_boxes_data 往返、
  video-archive 规则 annotated_video 字段往返
"""
import json
import os
import subprocess
import time
import uuid as uuidlib
from datetime import datetime

import pytest

from backend.db.database import SessionLocal
from backend.services import detection_boxes_sidecar as sc
from backend.services.detection_boxes_sidecar import (
    BoxesSidecarCollector, load_sidecar, sidecar_path_for,
)


def _det(label="A", conf=0.9, x=0.1, y=0.2, w=0.3, h=0.4):
    return {"label": label, "confidence": conf, "x": x, "y": y, "w": w, "h": h}


# ============================================================
# 收集器
# ============================================================

class TestCollector:
    def test_run_length_dedupe(self, tmp_path):
        c = BoxesSidecarCollector(str(tmp_path / "v.mp4"), 25)
        c.observe(0, [_det()])
        c.observe(1, [_det()])          # 相同 → 不记
        c.observe(2, [_det()])          # 相同 → 不记
        c.observe(3, [_det(), _det(label="B")])  # 变化 → 记
        c.observe(7, [])                # 框消失 → 记 (空列表)
        c.observe(8, [])                # 仍为空 → 不记
        assert [e["f"] for e in c.entries] == [0, 3, 7]
        assert c.entries[2]["d"] == []

    def test_dirty_det_dropped(self, tmp_path):
        c = BoxesSidecarCollector(str(tmp_path / "v.mp4"), 25)
        c.observe(0, [{"label": "缺坐标"}, _det()])
        assert len(c.entries) == 1
        assert len(c.entries[0]["d"]) == 1  # 脏数据丢弃, 好的保留

    def test_entry_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sc, "MAX_ENTRIES", 3)
        c = BoxesSidecarCollector(str(tmp_path / "v.mp4"), 25)
        for i in range(10):
            c.observe(i, [_det(x=0.01 * i)])  # 每帧都变化
        assert len(c.entries) == 3
        assert c._overflowed

    def test_flush_roundtrip(self, tmp_path):
        video = str(tmp_path / "cycle_x.mp4")
        c = BoxesSidecarCollector(video, 25)
        c.observe(0, [_det(label="螺丝")])
        c.observe(5, [])
        path = c.flush()
        assert path == sidecar_path_for(video)
        data = load_sidecar(path)
        assert data["version"] == 1
        assert data["fps"] == 25
        assert data["video"] == "cycle_x.mp4"
        assert [e["f"] for e in data["frames"]] == [0, 5]
        assert data["frames"][0]["d"][0]["label"] == "螺丝"

    def test_flush_empty_no_file(self, tmp_path):
        c = BoxesSidecarCollector(str(tmp_path / "v.mp4"), 25)
        assert c.flush() is None
        assert not os.path.exists(sidecar_path_for(str(tmp_path / "v.mp4")))

    def test_load_missing_or_corrupt(self, tmp_path):
        assert load_sidecar(str(tmp_path / "nope.json")) is None
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert load_sidecar(str(bad)) is None


# ============================================================
# 带框渲染 (真 ffmpeg)
# ============================================================

def _ffmpeg_available():
    try:
        from backend.api.source import get_cached_ffmpeg_path
        p = get_cached_ffmpeg_path()
        subprocess.run([p, "-version"], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def _make_small_video(path, frames=20, size=64):
    """cv2 mp4v 写一个纯黑小视频; 编码器不可用返回 False。"""
    import cv2
    import numpy as np
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 25,
                        (size, size))
    if not w.isOpened():
        return False
    for _ in range(frames):
        w.write(np.zeros((size, size, 3), dtype=np.uint8))
    w.release()
    return os.path.getsize(path) > 0


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg 不可用")
class TestRenderAnnotated:
    def test_render_roundtrip(self, tmp_path):
        import cv2
        from backend.services.annotated_video import render_annotated

        video = str(tmp_path / "src.mp4")
        if not _make_small_video(video):
            pytest.skip("cv2 mp4v 编码器不可用")

        c = BoxesSidecarCollector(video, 25)
        c.observe(0, [_det(label="A", x=0.25, y=0.25, w=0.5, h=0.5)])
        c.observe(15, [])
        sp = c.flush()

        out = str(tmp_path / "out.mp4")
        assert render_annotated(video, sp, out) == out
        assert os.path.getsize(out) > 0

        cap = cv2.VideoCapture(out)
        assert cap.isOpened()
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, frame = cap.read()  # 第 0 帧应有绿框
        cap.release()
        assert abs(n - 20) <= 2, f"帧数漂移过大: {n}"
        assert ok
        g = frame[:, :, 1].astype(int)
        r = frame[:, :, 2].astype(int)
        assert ((g > 150) & (r < 100)).any(), "第 0 帧应画出绿色检测框"

    def test_render_without_sidecar_raises(self, tmp_path):
        from backend.services.annotated_video import render_annotated
        video = str(tmp_path / "src.mp4")
        if not _make_small_video(video):
            pytest.skip("cv2 mp4v 编码器不可用")
        with pytest.raises(RuntimeError):
            render_annotated(video, str(tmp_path / "no.json"),
                             str(tmp_path / "out.mp4"))


# ============================================================
# 归档集成 (fixture 照 test_video_archive_evidence 同款)
# ============================================================

from backend.models.models import DetectionCycle, DetectionSession, VideoClip  # noqa: E402
from backend.models.archive_models import VideoArchiveLog, VideoArchiveRule  # noqa: E402
from backend.services import video_archive as va  # noqa: E402


def _mk_cycle(db, tmp_path, *, is_good=False, suffix="",
              content=b"FAKE_MP4_CONTENT" * 64):
    now = datetime.now()
    sess = DetectionSession(
        session_uuid=f"bsa-s-{time.time_ns()}{suffix}",
        start_time=now, channel_id=0,
    )
    db.add(sess)
    db.flush()
    src = os.path.join(str(tmp_path), f"cycle_src_{time.time_ns()}{suffix}.mp4")
    with open(src, "wb") as f:
        f.write(content)
    cyc = DetectionCycle(
        cycle_uuid=f"bsa-c-{time.time_ns()}{suffix}",
        session_id=sess.id, start_time=now, end_time=now,
        is_good=is_good, video_path=src,
    )
    db.add(cyc)
    db.commit()
    db.refresh(cyc)
    return cyc, src


def _mk_rule(db, dest_dir, **kw):
    defaults = dict(
        name="带框投递规则", enabled=True, result_filter="all",
        dest_dir=str(dest_dir), subdir_by_date=False,
        filename_template="{{ cycle.id }}.mp4",
        overwrite_policy="overwrite", annotated_video=True,
    )
    defaults.update(kw)
    r = VideoArchiveRule(**defaults)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _write_sidecar(src):
    c = BoxesSidecarCollector(src, 25)
    c.observe(0, [_det()])
    return c.flush()


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clean_archive():
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


class TestArchiveAnnotated:
    def test_annotated_render_delivered(self, db, tmp_path, monkeypatch):
        """勾了带框版且 sidecar 在 → 投递的是渲染产物。"""
        cyc, src = _mk_cycle(db, tmp_path)
        _write_sidecar(src)
        dest = tmp_path / "dest1"
        dest.mkdir()
        _mk_rule(db, dest)

        def _fake_render(video_path, sidecar_path, out_path, **kw):
            assert video_path == src
            with open(out_path, "wb") as f:
                f.write(b"BOXED_RENDER_OUTPUT")
            return out_path

        import backend.services.annotated_video as av
        monkeypatch.setattr(av, "render_annotated", _fake_render)

        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        with open(results[0]["dest_path"], "rb") as f:
            assert f.read() == b"BOXED_RENDER_OUTPUT"

    def test_render_failure_falls_back_to_raw(self, db, tmp_path, monkeypatch):
        """渲染炸了 → 降级投递干净原片, 归档仍成功。"""
        content = b"RAW_ORIGINAL" * 32
        cyc, src = _mk_cycle(db, tmp_path, content=content)
        _write_sidecar(src)
        dest = tmp_path / "dest2"
        dest.mkdir()
        _mk_rule(db, dest)

        import backend.services.annotated_video as av
        monkeypatch.setattr(av, "render_annotated",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("渲染故障注入")))

        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        with open(results[0]["dest_path"], "rb") as f:
            assert f.read() == content

    def test_no_sidecar_falls_back_to_raw(self, db, tmp_path):
        """没开「记录检测框数据」(无 sidecar) → 投递原片。"""
        content = b"RAW_NO_SIDECAR" * 32
        cyc, src = _mk_cycle(db, tmp_path, content=content)
        dest = tmp_path / "dest3"
        dest.mkdir()
        _mk_rule(db, dest)

        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        with open(results[0]["dest_path"], "rb") as f:
            assert f.read() == content

    def test_rule_off_ships_raw_even_with_sidecar(self, db, tmp_path):
        """规则没勾带框版 → 有 sidecar 也投原片 (默认行为零差异)。"""
        content = b"RAW_RULE_OFF" * 32
        cyc, src = _mk_cycle(db, tmp_path, content=content)
        _write_sidecar(src)
        dest = tmp_path / "dest4"
        dest.mkdir()
        _mk_rule(db, dest, annotated_video=False)

        results = va.archive_cycle_now(cyc.id)
        assert results[0]["status"] == "success"
        with open(results[0]["dest_path"], "rb") as f:
            assert f.read() == content


# ============================================================
# 录制线程端到端集成 (真 FFmpegRecorder + 真录制线程 + 帧号对齐)
# ============================================================

@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg 不可用")
class TestRecordingIntegration:
    def test_cycle_recording_produces_aligned_sidecar(self):
        """开「记录检测框数据」→ 真录一段 → sidecar 帧号与检测变化对齐。"""
        import numpy as np
        from backend.api.source import VideoSourceManager

        vsm = VideoSourceManager(0)
        vsm.export_settings = {
            'record_cycle_video': True, 'record_boxes_data': True,
            'video_fps': 25,
        }
        vsm.width, vsm.height = 64, 64
        vsm.current_cycle_uuid = f"rec-int-{time.time_ns()}"
        vsm.current_cycle_id = None

        vsm.start_cycle_recording()
        try:
            assert vsm.cycle_video_writer is not None, "cycle writer 未打开"
            assert vsm._boxes_sidecar_active is True
            collector = getattr(vsm.cycle_video_writer, '_boxes_collector', None)
            assert collector is not None, "收集器未挂上 writer"
            filepath = vsm.cycle_video_writer.filepath

            vsm._start_recording_thread()
            frame = np.zeros((64, 64, 3), dtype=np.uint8)

            # 阶段1: 有一个检测框
            with vsm.detection_lock:
                vsm.current_detections = [_det(label="工件")]
            for _ in range(8):
                vsm._enqueue_frame_for_recording(frame)
                time.sleep(0.02)
            time.sleep(0.5)  # 让录制线程排空

            # 阶段2: 框消失
            with vsm.detection_lock:
                vsm.current_detections = []
            for _ in range(8):
                vsm._enqueue_frame_for_recording(frame)
                time.sleep(0.02)
            time.sleep(0.5)

            vsm.stop_cycle_recording()   # 延迟释放 1.5s 后 flush sidecar
            assert vsm._boxes_sidecar_active is False
            vsm._stop_recording_thread()
            time.sleep(2.5)

            sp = sidecar_path_for(filepath)
            assert os.path.isfile(sp), "sidecar 未落盘"
            data = load_sidecar(sp)
            frames = data["frames"]
            # run-length: 至少两条 (有框 → 无框), 帧号递增
            assert len(frames) >= 2
            assert frames[0]["d"] and frames[0]["d"][0]["label"] == "工件"
            empties = [e for e in frames if e["d"] == []]
            assert empties, "框消失应记一条空记录"
            fs = [e["f"] for e in frames]
            assert fs == sorted(fs) and len(set(fs)) == len(fs)
            # 消失点应在有框段之后 (帧号对齐语义)
            assert empties[0]["f"] > frames[0]["f"]
            assert os.path.getsize(filepath) > 0, "录像文件为空"
        finally:
            try:
                vsm._close_all_writers()
            except Exception:
                pass

    def test_disabled_by_default_zero_sidecar(self):
        """默认不开 record_boxes_data → 不挂收集器、不产 sidecar。"""
        from backend.api.source import VideoSourceManager
        vsm = VideoSourceManager(0)
        vsm.export_settings = {
            'record_cycle_video': True, 'video_fps': 25,
        }
        vsm.width, vsm.height = 64, 64
        vsm.current_cycle_uuid = f"rec-off-{time.time_ns()}"
        vsm.current_cycle_id = None
        vsm.start_cycle_recording()
        try:
            assert vsm.cycle_video_writer is not None
            assert vsm._boxes_sidecar_active is False
            assert getattr(vsm.cycle_video_writer, '_boxes_collector',
                           None) is None
        finally:
            vsm._close_all_writers()


# ============================================================
# API 契约
# ============================================================

class TestApiContracts:
    def test_export_settings_record_boxes_roundtrip(self, client):
        r = client.put("/api/v1/data/export-settings",
                       json={"record_boxes_data": True})
        assert r.status_code == 200
        assert r.json()["record_boxes_data"] is True
        r = client.get("/api/v1/data/export-settings")
        assert r.json()["record_boxes_data"] is True
        # 恢复默认
        r = client.put("/api/v1/data/export-settings",
                       json={"record_boxes_data": False})
        assert r.json()["record_boxes_data"] is False

    def test_video_boxes_endpoint(self, client, tmp_path):
        db = SessionLocal()
        try:
            # 带 sidecar 的录像 → 200
            v1 = str(tmp_path / "with_boxes.mp4")
            open(v1, "wb").write(b"x")
            _write_sidecar(v1)
            uid1 = uuidlib.uuid4().hex
            db.add(VideoClip(video_uuid=uid1, clip_type="cycle",
                             file_path=v1, file_name="with_boxes.mp4",
                             start_time=datetime.now()))
            # 无 sidecar 的录像 → 404
            v2 = str(tmp_path / "no_boxes.mp4")
            open(v2, "wb").write(b"x")
            uid2 = uuidlib.uuid4().hex
            db.add(VideoClip(video_uuid=uid2, clip_type="cycle",
                             file_path=v2, file_name="no_boxes.mp4",
                             start_time=datetime.now()))
            db.commit()

            r = client.get(f"/api/v1/data/videos/{uid1}/boxes")
            assert r.status_code == 200
            body = r.json()
            assert body["fps"] == 25 and body["frames"]

            r = client.get(f"/api/v1/data/videos/{uid2}/boxes")
            assert r.status_code == 404

            r = client.get(f"/api/v1/data/videos/{uuidlib.uuid4().hex}/boxes")
            assert r.status_code == 404
        finally:
            db.query(VideoClip).filter(
                VideoClip.video_uuid.in_([uid1, uid2])).delete(
                synchronize_session=False)
            db.commit()
            db.close()

    def test_archive_rule_annotated_field_roundtrip(self, client, tmp_path):
        dest = tmp_path / "rule_dest"
        dest.mkdir()
        r = client.post("/api/v1/export/video-archive/rules", json={
            "name": "带框字段往返", "dest_dir": str(dest),
            "annotated_video": True,
        })
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        assert r.json()["annotated_video"] is True
        try:
            r = client.get(f"/api/v1/export/video-archive/rules/{rid}")
            assert r.json()["annotated_video"] is True
            r = client.put(f"/api/v1/export/video-archive/rules/{rid}",
                           json={"annotated_video": False})
            assert r.json()["annotated_video"] is False
        finally:
            client.delete(f"/api/v1/export/video-archive/rules/{rid}")
