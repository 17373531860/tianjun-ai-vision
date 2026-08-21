# -*- coding: utf-8 -*-
"""v3.54 长录像治理单测: fMP4 录制 + 收尾 remux + 回放编码探测。

治的问题: 24h 会话录像"视频加载失败" —— 老 +faststart 收尾要整文件重写
moov, release() 3s 超时就 kill, 大文件必坏; 回放又无条件全量重转码,
300s 转不完回退坏原片。

三层防线各一组断言 (全部用真 ffmpeg, 无 ffmpeg 环境自动跳过):
  1. 录制中被 SIGKILL (模拟断电/强杀) → fMP4 文件仍可解码出帧
  2. 正常 release → 后台 remux 成 faststart (moov 前置) 且帧数完整
  3. 编码探测: 自录 h264 → 直出; mp4v/垃圾文件 → False 走转码
"""
import os
import shutil
import struct
import subprocess
import time

import numpy as np
import pytest

def _find_ffmpeg():
    """项目内捆绑 ffmpeg (与 get_ffmpeg_path 同一落点) → PATH。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(root, "ffmpeg", "ffmpeg"),
                 os.path.join(root, "ffmpeg", "ffmpeg.exe")):
        if os.path.isfile(cand):
            return cand
    return shutil.which("ffmpeg")


FFMPEG = _find_ffmpeg()

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="环境无 ffmpeg")


@pytest.fixture()
def recorder_factory(monkeypatch, tmp_path):
    """构造 FFmpegRecorder, ffmpeg 路径钉到 which 结果 (绕开 source.py 重导入)。"""
    from backend.api import source_recorder
    monkeypatch.setattr(source_recorder, "_get_ffmpeg_path_cached", lambda: FFMPEG)

    def _make(name="rec.mp4", width=640, height=360, fps=25):
        path = str(tmp_path / name)
        rec = source_recorder.FFmpegRecorder(path, width, height, fps)
        assert rec.open(), f"ffmpeg 启动失败: {rec.last_error}"
        return rec, path
    return _make


def _write_frames(rec, n, width=640, height=360):
    """写 n 帧带变化的画面 (纯色渐变, 避免全黑被 x264 压成 0 字节)。"""
    for i in range(n):
        frame = np.full((height, width, 3), (i * 3) % 255, dtype=np.uint8)
        frame[:, : (i * 7) % width] = 200
        assert rec.write(frame), f"第 {i} 帧写入失败: {rec.last_error}"


def _count_decodable_frames(path):
    """用真 ffmpeg 解码统计帧数 (比 cv2 更接近浏览器行为, 且不吃 cv2 兼容坑)。"""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", path, "-map", "0:v:0",
         "-f", "null", "-"],
        capture_output=True, timeout=60)
    stderr = (proc.stderr or b"").decode("utf-8", "ignore")
    # 末尾统计行形如: frame=  200 fps=...
    frames = 0
    for line in stderr.splitlines():
        if line.startswith("frame="):
            try:
                frames = int(line.split("=", 1)[1].split()[0])
            except (ValueError, IndexError):
                pass
    return frames


def _top_level_boxes(path, limit=6):
    """解析 MP4 顶层 box 类型序列 (faststart 判定: moov 在 mdat/moof 之前)。"""
    boxes = []
    with open(path, "rb") as f:
        while len(boxes) < limit:
            head = f.read(8)
            if len(head) < 8:
                break
            size, btype = struct.unpack(">I4s", head)
            boxes.append(btype.decode("latin1"))
            if size == 1:  # 64 位大 box
                size = struct.unpack(">Q", f.read(8))[0]
                f.seek(size - 16, 1)
            elif size == 0:  # 到文件尾
                break
            else:
                f.seek(size - 8, 1)
    return boxes


# ---------- 1. 崩溃容忍: 录制中强杀仍可播 ----------

def test_kill_mid_recording_file_still_decodable(recorder_factory):
    """SIGKILL 模拟断电/强杀: fMP4 已落盘 fragment 仍可解码 (老格式=整段全废)。"""
    rec, path = recorder_factory("killed.mp4")
    _write_frames(rec, 200)  # 200 帧 @25fps=8s 时间轴, GOP=125 → 至少 1 个完整 fragment
    time.sleep(1.0)  # 给编码器排空管道缓冲
    rec.process.kill()  # 不走 release, 模拟最恶劣的强杀
    rec.process.wait(timeout=5)
    rec.process = None
    rec._is_open = False

    assert os.path.getsize(path) > 0, "强杀后文件为空"
    frames = _count_decodable_frames(path)
    assert frames >= 1, "强杀后 fMP4 应至少能解出已落盘 fragment 的帧"
    print(f"强杀后可解码帧数: {frames}/200")


# ---------- 2. 正常收尾: 后台 remux 成 faststart 且帧数完整 ----------

def test_release_remuxes_to_faststart(recorder_factory):
    rec, path = recorder_factory("normal.mp4")
    _write_frames(rec, 100)
    rec.release()  # 内部异步 remux

    # 等 remux 完成: moov 出现在 mdat 之前 (fMP4 是 ftyp+moov(空)+moof/mdat…,
    # remux 后是 ftyp+moov(完整)+mdat, 判据为顶层无 moof)
    deadline = time.time() + 30
    while time.time() < deadline:
        boxes = _top_level_boxes(path)
        if boxes and "moof" not in boxes and "moov" in boxes:
            break
        time.sleep(0.3)
    boxes = _top_level_boxes(path)
    assert "moof" not in boxes, f"remux 未完成/未生效, 顶层 box: {boxes}"
    assert boxes.index("moov") < boxes.index("mdat"), f"moov 未前置: {boxes}"
    assert not os.path.exists(path + ".remux.tmp"), "残留 remux 半成品"

    frames = _count_decodable_frames(path)
    assert frames == 100, f"remux 后帧数不完整: {frames}/100"


def test_remux_failure_keeps_original(tmp_path, monkeypatch):
    """remux 对垃圾文件失败: 原文件原样保留, 无 tmp 残留。"""
    from backend.api import source_recorder
    monkeypatch.setattr(source_recorder, "_get_ffmpeg_path_cached", lambda: FFMPEG)
    p = tmp_path / "garbage.mp4"
    p.write_bytes(b"not a video at all")
    ok = source_recorder.remux_to_faststart(str(p))
    assert ok is False
    assert p.read_bytes() == b"not a video at all", "失败不得动原文件"
    assert not os.path.exists(str(p) + ".remux.tmp")


# ---------- 3. 回放编码探测: h264 直出 / 老编码走转码 ----------

def _probe(monkeypatch, path):
    from backend.api import sessions as sessions_api
    monkeypatch.setattr(sessions_api, "get_cached_ffmpeg_path", lambda: FFMPEG)
    sessions_api._VIDEO_PROBE_CACHE.clear()
    return sessions_api._is_browser_compatible_h264(path)


def test_probe_h264_recording_is_compatible(recorder_factory, monkeypatch):
    rec, path = recorder_factory("probe_h264.mp4")
    _write_frames(rec, 30)
    rec.release(remux=False)  # 探测对 fMP4 与 remux 后文件都应为 True
    assert _probe(monkeypatch, path) is True


def test_probe_mp4v_and_garbage_incompatible(tmp_path, monkeypatch):
    # 用真 ffmpeg 造一个 mp4v (老编码) 文件
    mp4v = str(tmp_path / "legacy_mp4v.mp4")
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x240:d=1",
         "-c:v", "mpeg4", mp4v],
        capture_output=True, timeout=30, check=True)
    assert _probe(monkeypatch, mp4v) is False, "mp4v 必须走转码路径"

    garbage = tmp_path / "garbage.bin"
    garbage.write_bytes(b"xx")
    assert _probe(monkeypatch, str(garbage)) is False


def test_probe_direct_serve_skips_transcode(recorder_factory, monkeypatch):
    """兼容文件直出: convert_video_for_browser 原样返回, 不起转码。"""
    from backend.api import sessions as sessions_api
    rec, path = recorder_factory("direct.mp4")
    _write_frames(rec, 30)
    rec.release(remux=False)

    monkeypatch.setattr(sessions_api, "get_cached_ffmpeg_path", lambda: FFMPEG)
    sessions_api._VIDEO_PROBE_CACHE.clear()
    out = sessions_api.convert_video_for_browser(path)
    assert out == path, "h264 录像应直出原文件, 不转码"
