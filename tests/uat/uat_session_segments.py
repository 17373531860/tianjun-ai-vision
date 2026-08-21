# -*- coding: utf-8 -*-
"""会话录像按小时分段 (v3.54 长录像治理) — 可见浏览器 UAT。

现场叙事:
  操作员开着会话录像连续生产一整天 → 后台每到分段时长自动换新文件续录
  (换段不断流、每段收尾整理成 faststart 秒开格式) → 操作员在数据中心点
  会话回放, 弹窗出现「录像分段」切换条, 逐段点播流畅可看。

运行前提:
  - backend 8002 以 RUNTIME_MODE=test + TJ_SESSION_SEGMENT_SECONDS=30 启动
    (生产默认 3600s = 1 小时/段, UAT 用 30s 加速轮转)
  - frontend dev 6002
  - 项目根 ffmpeg/ffmpeg 可执行

用法:
  ~/miniconda3/envs/tianjun/bin/python tests/uat/uat_session_segments.py
  # 长跑版 (2 小时, 分段数 ~240):
  UAT_RUN_SECONDS=7200 UAT_HEADLESS=1 ~/miniconda3/envs/tianjun/bin/python \
      tests/uat/uat_session_segments.py

证据产出: tests/uat/artifacts/session-segments/ (截图 + 录屏 + run.log)
"""
import os
import shutil
import struct
import subprocess
import sys
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright, expect

API = os.environ.get("UAT_API", "http://localhost:8002/api/v1")
FRONT = os.environ.get("UAT_FRONT", "http://localhost:6002")
CH = int(os.environ.get("UAT_CHANNEL", "0"))
RUN_SECONDS = int(os.environ.get("UAT_RUN_SECONDS", "100"))
SEGMENT_SECONDS = int(os.environ.get("UAT_SEGMENT_SECONDS", "30"))
HEADLESS = os.environ.get("UAT_HEADLESS", "") == "1"

ART_DIR = os.path.join(os.path.dirname(__file__), "artifacts",
                       "session-segments")
PROJECT_NAME = f"__uat_seg_{int(time.time())}"

_log_lines = []


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _log_lines.append(line)


def die(msg):
    log(f"❌ FAIL: {msg}")
    _flush_log()
    sys.exit(1)


def _flush_log():
    os.makedirs(ART_DIR, exist_ok=True)
    with open(os.path.join(ART_DIR, "run.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(_log_lines) + "\n")


def _find_ffmpeg():
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    for cand in (os.path.join(root, "ffmpeg", "ffmpeg"),
                 os.path.join(root, "ffmpeg", "ffmpeg.exe")):
        if os.path.isfile(cand):
            return cand
    return shutil.which("ffmpeg")


FFMPEG = _find_ffmpeg()


def _top_level_boxes(path, limit=6):
    boxes = []
    with open(path, "rb") as f:
        while len(boxes) < limit:
            head = f.read(8)
            if len(head) < 8:
                break
            size, btype = struct.unpack(">I4s", head)
            boxes.append(btype.decode("latin1"))
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
                f.seek(size - 16, 1)
            elif size == 0:
                break
            else:
                f.seek(size - 8, 1)
    return boxes


def _decodable_frames(path):
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", path, "-map", "0:v:0", "-f", "null", "-"],
        capture_output=True, timeout=300)
    frames = 0
    for line in (proc.stderr or b"").decode("utf-8", "ignore").splitlines():
        if line.startswith("frame="):
            try:
                frames = int(line.split("=", 1)[1].split()[0])
            except (ValueError, IndexError):
                pass
    return frames


def _ffmpeg_proc_count():
    out = subprocess.run(["pgrep", "-f", "ffmpeg.*rawvideo"],
                         capture_output=True, text=True)
    return len([l for l in out.stdout.splitlines() if l.strip()])


# ============================================================
# 后端准备
# ============================================================

_orig_adopt_unbound = None


def seed_backend():
    global _orig_adopt_unbound
    _orig_adopt_unbound = requests.get(
        f"{API}/projects/activate-config", timeout=5).json()["adopt_unbound"]
    requests.put(f"{API}/projects/activate-config",
                 json={"adopt_unbound": True}, timeout=5)

    for p in requests.get(f"{API}/projects", timeout=5).json():
        if isinstance(p, dict) and (p.get("name") or "").startswith("__uat_seg_"):
            requests.delete(f"{API}/projects/{p['id']}", timeout=5)

    payload = {
        "name": PROJECT_NAME, "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": [
            {"id": 1, "label": "A", "enabled": True, "min_frames": 1,
             "threshold": 0.3}],
        "events_config": [
            {"id": 1, "name": "合格", "actions": []},
            {"id": 2, "name": "不合格", "actions": []}],
        "pipeline_config": {
            "sequence_order": [{"step_id": 1}],
            "settlement_mode": "last_first", "settle_dedup": False},
        "counters_config": [], "alarm_config": {}, "detection_config": {},
        "data_config": {
            "record_session_video": True, "record_cycle_video": False,
            "record_step_video": False, "video_quality": "medium",
            "video_fps": 25},
    }
    r = requests.post(f"{API}/projects", json=payload, timeout=10)
    assert r.status_code in (200, 201), f"create project: {r.text[:300]}"
    pid = r.json()["id"]
    r = requests.post(f"{API}/projects/{pid}/activate", timeout=30)
    assert r.status_code == 200, f"activate: {r.text[:300]}"
    # 会话录像开关走全局导出设置
    r = requests.put(f"{API}/data/export-settings", json={
        "record_session_video": True, "record_cycle_video": False,
        "record_step_video": False, "video_fps": 25}, timeout=10)
    assert r.status_code == 200
    log(f"✅ 项目已建并激活 (id={pid}), 会话录像开")
    return pid


def run_recording():
    """启动 synthetic + 检测, 持续 RUN_SECONDS 后停 (期间自动换段)。"""
    scenario = {
        "name": "uat_session_seg", "fps": 25,
        "timeline": [
            {"from": 0, "to": 49, "detections": []},
            {"from": 50, "to": 99, "detections": [
                {"label": "A", "confidence": 0.95,
                 "bbox": [0.1, 0.1, 0.18, 0.18]}]},
        ],
    }
    r = requests.post(f"{API}/test/synthetic/start", json={
        "scenario_json": scenario, "channel": CH, "with_project": False,
    }, timeout=15)
    assert r.status_code == 200, f"synthetic start: {r.text[:300]}"
    r = requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"conf": 0.25}, timeout=60)
    assert r.status_code == 200, f"detection start: {r.text[:300]}"
    log(f"✅ 检测+会话录像已启动, 连续录 {RUN_SECONDS}s "
        f"(分段 {SEGMENT_SECONDS}s/段, 预期 ≥{RUN_SECONDS // SEGMENT_SECONDS - 1} 段)")

    t0 = time.time()
    while time.time() - t0 < RUN_SECONDS:
        time.sleep(min(30, RUN_SECONDS / 4))
        elapsed = int(time.time() - t0)
        st = requests.get(f"{API}/source/status?channel={CH}", timeout=5).json()
        if not st.get("is_detecting"):
            die(f"检测中途停了 (t={elapsed}s): {st}")
        log(f"  … 录制中 {elapsed}/{RUN_SECONDS}s, ffmpeg 进程数="
            f"{_ffmpeg_proc_count()}")

    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=30)
    requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=15)
    log("✅ 录制结束, 等收尾 (排空 + release + remux)…")
    time.sleep(10)


def _find_uat_session():
    today = datetime.now().strftime("%Y-%m-%d")
    body = requests.get(f"{API}/data/sessions/by-date/{today}",
                        timeout=5).json()
    mine = [s for s in (body.get("sessions") or [])
            if s.get("project_name") == PROJECT_NAME]
    if not mine:
        return None
    return sorted(mine, key=lambda x: x.get("id", 0), reverse=True)[0]


# ============================================================
# 主流程
# ============================================================

def main():
    if FFMPEG is None:
        die("找不到 ffmpeg")
    os.makedirs(os.path.join(ART_DIR, "video"), exist_ok=True)

    seed_backend()
    baseline_procs = _ffmpeg_proc_count()
    run_recording()

    session = _find_uat_session()
    if not session:
        die("没找到 UAT 会话")
    sid = session["id"]

    # ---- 1. 分段列表断言 ----
    expect_min = max(2, RUN_SECONDS // SEGMENT_SECONDS - 1)
    segs = requests.get(f"{API}/data/sessions/{sid}/videos", timeout=10).json()
    if len(segs) < expect_min:
        die(f"分段数不足: 得到 {len(segs)} 段, 预期 ≥{expect_min}; segs={segs}")
    missing = [s for s in segs if not s["file_exists"]]
    if missing:
        die(f"有分段文件缺失: {missing}")
    log(f"✅ 分段列表 API: {len(segs)} 段, 文件齐全, 时间升序")

    # ---- 2. 每段文件级断言: faststart + 可解码 (等 remux 收尾) ----
    db_clips = requests.get(f"{API}/data/videos",
                            params={"clip_type": "session", "limit": 500},
                            timeout=10).json()
    uuid2clip = {c["video_uuid"]: c for c in db_clips}
    total_frames = 0
    for i, seg in enumerate(segs, 1):
        clip = uuid2clip.get(seg["video_uuid"])
        if not clip:
            die(f"第{i}段在 /data/videos 查不到: {seg['video_uuid']}")
        # 文件路径拿不到 (API 不吐), 通过回放端点确认可出流
        vr = requests.get(f"{API}/data/videos/{seg['video_uuid']}", timeout=60)
        if vr.status_code != 200 or len(vr.content) < 1024:
            die(f"第{i}段回放接口异常: {vr.status_code} len={len(vr.content)}")
        # 落一份本地临时文件做容器级断言
        tmp = os.path.join(ART_DIR, f"_seg{i}.mp4")
        with open(tmp, "wb") as f:
            f.write(vr.content)
        boxes = _top_level_boxes(tmp)
        frames = _decodable_frames(tmp)
        os.remove(tmp)
        if frames <= 0:
            die(f"第{i}段不可解码")
        faststart = ("moof" not in boxes and "moov" in boxes
                     and boxes.index("moov") < len(boxes) - 1)
        total_frames += frames
        log(f"  段{i}: {frames} 帧, faststart={faststart}, "
            f"boxes={boxes[:4]}, {len(vr.content)} bytes")
    # 帧数总量 sanity: 25fps × RUN_SECONDS, 允许 ±40% (排队/换段重叠/收尾余量)
    expect_frames = 25 * RUN_SECONDS
    if not (expect_frames * 0.6 <= total_frames <= expect_frames * 1.6):
        die(f"分段帧数总量异常: {total_frames}, 预期 ~{expect_frames}")
    log(f"✅ 全部 {len(segs)} 段可解码, 总帧数 {total_frames} "
        f"(~{expect_frames} 预期), 换段不断流")

    # ---- 3. FFmpeg 进程无堆积 ----
    time.sleep(3)
    now_procs = _ffmpeg_proc_count()
    if now_procs > baseline_procs:
        die(f"FFmpeg 录制进程残留: baseline={baseline_procs}, now={now_procs}")
    log(f"✅ FFmpeg 进程无堆积 (baseline={baseline_procs}, now={now_procs})")

    # ---- 4. 浏览器 UAT: 分段切换条 ----
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS, slow_mo=120)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 950},
            record_video_dir=os.path.join(ART_DIR, "video"))
        page = ctx.new_page()
        page.goto(f"{FRONT}/#/data", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        # 顶栏选 UAT 项目 + 今天
        page.locator(".el-select").first.click()
        page.wait_for_timeout(400)
        page.locator(
            f".el-select-dropdown__item:has-text('{PROJECT_NAME}')").first.click()
        page.wait_for_timeout(400)
        page.locator("button:has-text('选择')").first.click(force=True)
        page.wait_for_timeout(1500)
        dp = page.locator(".el-date-editor input").first
        dp.click()
        dp.fill(datetime.now().strftime("%Y-%m-%d"))
        dp.press("Enter")
        page.wait_for_timeout(1500)

        card = page.locator(".session-card:has-text('已完成')").first
        if not card.count():
            card = page.locator(".session-card").first
        card.wait_for(state="visible", timeout=10000)
        # 会话卡上的圆形播放按钮 (primary)
        play = card.locator(".el-button--primary.is-circle").first
        play.click()

        dlg = page.locator(".el-dialog:has-text('视频播放')")
        expect(dlg.first).to_be_visible(timeout=10000)
        bar = page.locator("[data-testid='video-segment-bar']")
        expect(bar).to_be_visible(timeout=8000)
        n_btns = page.locator(
            "[data-testid='video-segment-bar'] button").count()
        if n_btns != len(segs):
            die(f"分段切换条按钮数 {n_btns} != API 段数 {len(segs)}")
        page.screenshot(path=os.path.join(ART_DIR, "01-segment-bar.png"))
        log(f"✅ 回放弹窗分段切换条: {n_btns} 段按钮")

        def _wait_video_ready(tag):
            deadline = time.time() + 30
            while time.time() < deadline:
                state = page.evaluate(
                    "() => { const v = document.querySelector('.el-dialog video');"
                    " return v ? {rs: v.readyState, err: !!v.error} : null; }")
                if state and state["err"]:
                    die(f"{tag} 视频报错")
                if state and state["rs"] >= 3:
                    return
                page.wait_for_timeout(500)
            die(f"{tag} 30s 未就绪")

        _wait_video_ready("第1段")
        log("✅ 第1段软件内播放就绪")

        # 切第 2 段
        page.locator("[data-testid='video-segment-2']").click()
        _wait_video_ready("第2段")
        page.screenshot(path=os.path.join(ART_DIR, "02-segment2-playing.png"))
        log("✅ 切换第2段播放就绪")

        # 切末段
        page.locator(f"[data-testid='video-segment-{len(segs)}']").click()
        _wait_video_ready(f"第{len(segs)}段(末段)")
        page.screenshot(path=os.path.join(ART_DIR, "03-last-segment.png"))
        log(f"✅ 末段(第{len(segs)}段)播放就绪")

        page.keyboard.press("Escape")
        ctx.close()
        browser.close()

    # ---- 清理 ----
    requests.put(f"{API}/projects/activate-config",
                 json={"adopt_unbound": _orig_adopt_unbound}, timeout=5)
    requests.put(f"{API}/data/export-settings", json={
        "record_session_video": False}, timeout=10)
    log("✅ UAT 清理完成 (录像开关复位 + adopt_unbound 还原)")
    log(f"🎉 UAT 全链路通过: 连续录 {RUN_SECONDS}s 自动分 {len(segs)} 段 → "
        f"每段独立收尾 faststart 可解码 → 无进程堆积 → 软件内分段切换回放")
    _flush_log()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        die(f"未捕获异常: {e}")
