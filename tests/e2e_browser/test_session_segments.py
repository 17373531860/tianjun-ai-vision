"""会话录像分段回放 (v3.54 长录像治理) 的 CI e2e 回归。

覆盖:
  1. 分段列表 API /data/sessions/{id}/videos: 升序 + segment_index + file_exists
  2. 播放弹窗分段切换条: 多段会话出现 N 个分段按钮, 点击切换 <video> 源
  3. 单段会话 (老数据形态) 不出现切换条 (零行为差异)

真实轮转 (到点换段/收尾 remux) 在单测 test_session_segmentation.py 与
UAT uat_session_segments.py 里用真 ffmpeg 验; 本文件只守 UI 契约,
种子直接写 DB(与 test_data_video_panel 同套路), __e2e_ 前缀, 测完自清。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta

import pytest
import requests

E2E_PREFIX = "__e2e_"


def _db_session():
    """连「正在跑的后端」的真实 DB (同 test_data_video_panel 的套路)。"""
    from backend.models import (  # noqa: F401
        auth_models as _a, mes_models as _m, export_models as _e,
        notify_models as _n, weighing_models as _w, plugin_models as _p,
    )
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    backend_db = os.environ.get("E2E_BACKEND_DB") or os.path.join(repo_root, "backend", "sql_app.db")
    assert os.path.exists(backend_db), f"后端 DB 不存在: {backend_db}"
    engine = create_engine(f"sqlite:///{backend_db}", connect_args={"check_same_thread": False})
    return sessionmaker(bind=engine)()


def _seed_session_with_segments(api_url, tmp_path, n_segments):
    """建一个带 n 段 session 录像的会话, 返回 (seed_dict, cleanup_fn)。"""
    from backend.models.models import DetectionSession, VideoClip

    r = requests.get(f"{api_url}/api/v1/projects", timeout=10)
    items = (r.json() or {}).get("items", [])
    proj = next((p for p in items if (p.get("name") or "").startswith(E2E_PREFIX)), None)
    assert proj, "conftest 应已建 __e2e_ 项目"

    db = _db_session()
    token = uuid.uuid4().hex[:6]
    name = f"{E2E_PREFIX}seg-{token}"
    base = datetime.now().replace(microsecond=0)

    clip_uuids, paths = [], []
    for i in range(n_segments):
        p = tmp_path / f"seg_{token}_{i}.mp4"
        p.write_bytes(b"\x00" * 2048)  # 假文件: 只验控件与切换, 不验解码
        paths.append(str(p))
        clip_uuids.append(f"{E2E_PREFIX}sv{i}-{token}")

    sess = DetectionSession(
        session_uuid=name, name=name, start_time=base,
        end_time=base + timedelta(hours=n_segments),
        project_id=proj["id"], channel_id=0,
        video_id=clip_uuids[0], video_path=paths[0],
    )
    db.add(sess)
    db.flush()
    for i in range(n_segments):
        db.add(VideoClip(
            video_uuid=clip_uuids[i], clip_type="session", related_id=sess.id,
            file_path=paths[i], file_name=os.path.basename(paths[i]),
            file_size=2048,
            start_time=base + timedelta(hours=i),
            end_time=base + timedelta(hours=i + 1),
        ))
    db.commit()
    sid = sess.id
    db.close()

    seed = {"session_id": sid, "name": name, "project_name": proj["name"],
            "clip_uuids": clip_uuids}

    def cleanup():
        db2 = _db_session()
        from backend.models.models import DetectionSession as DS, VideoClip as VC
        db2.query(VC).filter(VC.related_id == sid, VC.clip_type == "session").delete()
        db2.query(DS).filter(DS.id == sid).delete()
        db2.commit()
        db2.close()

    return seed, cleanup


@pytest.fixture
def seeded_multi_segment(cleanup_e2e_resources, api_url, tmp_path):
    seed, cleanup = _seed_session_with_segments(api_url, tmp_path, 3)
    yield seed
    cleanup()


@pytest.fixture
def seeded_single_segment(cleanup_e2e_resources, api_url, tmp_path):
    seed, cleanup = _seed_session_with_segments(api_url, tmp_path, 1)
    yield seed
    cleanup()


def test_session_videos_api(api_url, seeded_multi_segment):
    seed = seeded_multi_segment
    r = requests.get(
        f"{api_url}/api/v1/data/sessions/{seed['session_id']}/videos", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert [x["video_uuid"] for x in body] == seed["clip_uuids"], "应按时间升序"
    assert [x["segment_index"] for x in body] == [1, 2, 3]
    assert all(x["file_exists"] for x in body)
    assert all(x["end_time"] for x in body)


def _open_session_play_dialog(page, base_url, seed):
    page.goto(f"{base_url}/#/data", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2500)
    page.locator(".el-select").first.click()
    page.wait_for_timeout(400)
    page.locator(f".el-select-dropdown__item:has-text('{seed['project_name']}')").first.click()
    page.wait_for_timeout(400)
    page.locator("button:has-text('选择')").first.click(force=True)
    page.wait_for_timeout(1500)
    dp = page.locator(".el-date-editor input").first
    dp.click()
    dp.fill(datetime.now().strftime("%Y-%m-%d"))
    dp.press("Enter")
    page.wait_for_timeout(1500)
    card = page.locator(f".session-card:has-text('{seed['name']}')").first
    card.wait_for(state="visible", timeout=10000)
    # 会话卡右上圆形播放按钮 (primary circle)
    card.locator(".el-button--primary.is-circle").first.click()
    page.wait_for_timeout(1500)
    dlg = page.locator(".el-dialog:has-text('视频播放')")
    assert dlg.count() >= 1, "播放弹窗未出现"


def test_segment_bar_switching(page, base_url, seeded_multi_segment):
    seed = seeded_multi_segment
    _open_session_play_dialog(page, base_url, seed)

    bar = page.locator("[data-testid='video-segment-bar']")
    assert bar.count() == 1, "多段会话应出现分段切换条"
    btns = bar.locator("button")
    assert btns.count() == 3, f"分段按钮数 {btns.count()} != 3"

    # 初始播第 1 段
    src = page.evaluate("() => { const v = document.querySelector('.el-dialog video'); return v ? v.src : '' }")
    assert seed["clip_uuids"][0] in src, f"初始应播第1段: {src}"

    # 切第 2 段 → <video> 源切换
    page.locator("[data-testid='video-segment-2']").click()
    page.wait_for_timeout(800)
    src = page.evaluate("() => { const v = document.querySelector('.el-dialog video'); return v ? v.src : '' }")
    assert seed["clip_uuids"][1] in src, f"切换后应播第2段: {src}"


def test_single_segment_no_bar(page, base_url, seeded_single_segment):
    seed = seeded_single_segment
    _open_session_play_dialog(page, base_url, seed)

    assert page.locator("[data-testid='video-segment-bar']").count() == 0, \
        "单段会话 (老数据形态) 不应出现分段切换条"
    src = page.evaluate("() => { const v = document.querySelector('.el-dialog video'); return v ? v.src : '' }")
    assert seed["clip_uuids"][0] in src
