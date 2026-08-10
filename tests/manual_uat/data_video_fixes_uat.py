"""v3.48.1 数据中心录像三修 — 可见浏览器 UAT（headless=False）

验证矩阵:
  A1 周期列表出现「全部/仅OK/仅NG」筛选, 选「仅 NG」后行内全是 NG tag
  A2 UI 行数与后端 GET ?result=ng 的 total 一致（UI→后端双向）
  A3 点 NG 周期「播放」→ 弹窗出现 + <video> 能进入可播状态(readyState>=2, 无 error)
  A4 倍速按钮组存在, 点 2x 后 video.playbackRate == 2
  A5 「下载录像」按钮存在, 点击触发下载(捕获 download 事件)
证据: tests/manual_uat/evidence/data_video_2026-08-10/
"""
import os
import sys
import time
import uuid
from datetime import datetime

import numpy as np
import requests

BASE = "http://localhost:6001"
API = "http://localhost:8001"
EV = os.path.join(os.path.dirname(__file__), "evidence", "data_video_2026-08-10")
os.makedirs(EV, exist_ok=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def make_h264_clip(path: str) -> bool:
    """macOS VideoToolbox 直接产 H.264(avc1), 浏览器可播。"""
    import cv2
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"avc1"), 10, (320, 240))
    if not w.isOpened():
        return False
    for i in range(30):
        frame = np.full((240, 320, 3), (i * 8 % 255, 40, 200), np.uint8)
        cv2.putText(frame, f"NG-{i}", (60, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
        w.write(frame)
    w.release()
    return os.path.getsize(path) > 0


def seed():
    # 全量注册 ORM(无 models/__init__ 聚合), 否则跨模块外键解析失败
    from backend.models import (  # noqa: F401
        auth_models as _a, mes_models as _m2, export_models as _e,
        notify_models as _n, weighing_models as _w, plugin_models as _p,
    )
    from backend.db.database import SessionLocal
    from backend.models.models import DetectionSession, DetectionCycle, VideoClip
    from backend.core.config import settings

    clip_dir = os.path.join(settings.RECORDING_DIR, "uat_vfix")
    os.makedirs(clip_dir, exist_ok=True)
    clip_path = os.path.join(clip_dir, "uat_ng_clip.mp4")
    assert make_h264_clip(clip_path), "cv2 avc1 生成失败"

    # 会话列表按当前项目过滤 → 种子必须挂在一个项目上, UI 里再切到该项目
    pr = requests.get(f"{API}/api/v1/projects", timeout=10).json()
    items = pr.get("items", [])
    assert items, "无项目可挂"
    proj = next((p for p in items if p.get("is_active")), items[0])
    project_id, project_name = proj["id"], proj["name"]

    db = SessionLocal()
    token = uuid.uuid4().hex[:6]
    sess = DetectionSession(
        session_uuid=f"UAT录像修复-{token}", start_time=datetime.now(),
        project_id=project_id, channel_id=0, name=f"UAT录像修复-{token}",
    )
    db.add(sess)
    db.flush()
    vid_uuid = f"uatv-{token}"
    db.add(VideoClip(video_uuid=vid_uuid, clip_type="cycle",
                     file_path=clip_path, file_name="uat_ng_clip.mp4",
                     file_size=os.path.getsize(clip_path), created_at=datetime.now()))
    for i, good in enumerate([True, True, False, False, False]):
        db.add(DetectionCycle(
            cycle_uuid=f"uatc-{i}-{token}", session_id=sess.id, cycle_number=i + 1,
            start_time=datetime.now(), end_time=datetime.now(), is_good=good,
            video_id=vid_uuid if not good else None,
        ))
    db.commit()
    sid = sess.id
    db.close()
    return sid, token, project_name


def cleanup(sid):
    from backend.db.database import SessionLocal
    from backend.models.models import DetectionSession, DetectionCycle, VideoClip
    db = SessionLocal()
    db.query(DetectionCycle).filter(DetectionCycle.session_id == sid).delete()
    db.query(DetectionSession).filter(DetectionSession.id == sid).delete()
    db.query(VideoClip).filter(VideoClip.video_uuid.like("uatv-%")).delete(synchronize_session=False)
    db.commit()
    db.close()


def main():
    sid, token, project_name = seed()
    results = {}
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=False)
            page = b.new_page()
            page.goto(f"{BASE}/#/data")
            page.wait_for_timeout(3000)

            # 数据中心需要先选项目: 顶栏项目下拉 → 选种子项目 → 点「选择」
            page.locator(".el-select").first.click()
            page.wait_for_timeout(500)
            page.locator(f".el-select-dropdown__item:has-text('{project_name}')").first.click()
            page.wait_for_timeout(500)
            page.locator("button:has-text('选择')").first.click(force=True)
            page.wait_for_timeout(2000)

            # 选今天日期(会话列表按日期加载)
            today = datetime.now().strftime("%Y-%m-%d")
            dp = page.locator(".el-date-editor input").first
            dp.click()
            dp.fill(today)
            dp.press("Enter")
            page.wait_for_timeout(2000)

            # 选中种子会话
            page.locator(f"text=UAT录像修复-{token}").first.click()
            page.wait_for_timeout(1500)

            # A1 筛选控件出现 + 选仅NG
            flt = page.locator("[data-testid='cycle-result-filter']")
            assert flt.count() == 1, "A1 结果筛选控件未出现"
            flt.locator(".el-radio-button:has-text('仅 NG')").click()
            page.wait_for_timeout(1200)
            rows = page.locator(".el-table__body-wrapper .el-tag:has-text('NG')")
            ok_rows = page.locator(".el-table__body-wrapper .el-tag:has-text('OK')")
            page.screenshot(path=os.path.join(EV, "01_ng_filter_on.png"))
            assert ok_rows.count() == 0, f"A1 仅NG下不应有OK行: {ok_rows.count()}"
            results["A1_ng_filter"] = f"pass (NG tags={rows.count()})"

            # A2 与后端双向
            r = requests.get(f"{API}/api/v1/data/sessions/{sid}/cycles", params={"result": "ng"}, timeout=10)
            assert r.status_code == 200 and r.json()["total"] == 3, r.text[:200]
            assert rows.count() == 3, f"A2 UI 行数 {rows.count()} != 后端 3"
            results["A2_backend_match"] = "pass (total=3)"

            # A3 播放 NG 视频
            # 周期行的播放按钮是图标 link 按钮
            page.locator(".el-table__body .el-button.is-link").first.click()
            page.wait_for_timeout(2500)
            dlg = page.locator(".el-dialog:has-text('视频播放')")
            assert dlg.count() >= 1, "A3 播放弹窗未出现"
            state = page.evaluate("() => { const v = document.querySelector('.el-dialog video'); return v ? {ready: v.readyState, err: v.error ? v.error.code : null, rate: v.playbackRate} : null }")
            assert state, "A3 <video> 不存在"
            assert state["err"] is None, f"A3 视频报错 code={state['err']}"
            assert state["ready"] >= 2, f"A3 视频未进入可播状态 readyState={state['ready']}"
            page.screenshot(path=os.path.join(EV, "02_ng_video_playing.png"))
            results["A3_playback"] = f"pass (readyState={state['ready']})"

            # A4 倍速
            sp = page.locator("[data-testid='video-speed-group']")
            assert sp.count() == 1, "A4 倍速控件未出现"
            sp.locator(".el-radio-button:has-text('2x')").click()
            page.wait_for_timeout(600)
            rate = page.evaluate("() => document.querySelector('.el-dialog video').playbackRate")
            assert rate == 2, f"A4 playbackRate={rate}"
            page.screenshot(path=os.path.join(EV, "03_speed_2x.png"))
            results["A4_speed"] = "pass (2x)"

            # A5 下载
            btn = page.locator("[data-testid='video-download-btn']")
            assert btn.count() == 1, "A5 下载按钮未出现"
            with page.expect_download(timeout=15000) as dl:
                btn.click()
            path = dl.value.path()
            assert path and os.path.getsize(path) > 0, "A5 下载文件为空"
            results["A5_download"] = f"pass ({os.path.getsize(path)}B)"

            b.close()
    finally:
        cleanup(sid)

    print("=" * 50)
    for k, v in results.items():
        print(f"  {k}: {v}")
    print("=" * 50)
    assert len(results) == 5
    print("UAT 全部通过")


if __name__ == "__main__":
    main()
