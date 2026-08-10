"""数据中心录像三修 (v3.48.1) 的 CI e2e 回归。

覆盖:
  1. 周期列表「全部/仅OK/仅NG」结果筛选: 控件出现、仅NG 时无 OK 行、行数与后端一致
  2. 视频播放弹窗: 倍速按钮组 + 「下载录像」按钮出现, 点 2x 后 playbackRate==2

种子直接写 DB(与 UAT 同套路), 用 __e2e_ 前缀, 测完自清。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest
import requests

E2E_PREFIX = "__e2e_"


def _db_session():
    """连「正在跑的后端」的真实 DB。

    ⚠️ 不能用 backend.db.database.SessionLocal: 根 conftest 把测试进程的
    TIANJUN_DATA_DIR 隔离到了临时目录, 那个 SessionLocal 指向的是隔离库,
    种子写进去后端根本看不见。e2e 的后端跑在仓库默认 DATA_DIR(backend/)。
    """
    # 全量注册 ORM(无 models/__init__ 聚合), 否则跨模块外键解析失败
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


@pytest.fixture
def seeded_video_session(cleanup_e2e_resources, api_url, tmp_path):
    """挂在 conftest 激活的 __e2e_ 项目下: 2 OK + 3 NG 周期, NG 挂一个视频。

    显式依赖 cleanup_e2e_resources: 保证先清旧建新项目、种子再挂上去,
    否则种子可能挂在马上要被清掉的旧 __e2e_ 项目上。
    """
    from backend.models.models import DetectionSession, DetectionCycle, VideoClip

    r = requests.get(f"{api_url}/api/v1/projects", timeout=10)
    items = (r.json() or {}).get("items", [])
    proj = next((p for p in items if (p.get("name") or "").startswith(E2E_PREFIX)), None)
    assert proj, "conftest 应已建 __e2e_ 项目"

    clip = tmp_path / "e2e_clip.mp4"
    clip.write_bytes(b"\x00" * 1024)  # 假视频: 只验控件, 不验解码

    db = _db_session()
    token = uuid.uuid4().hex[:6]
    name = f"{E2E_PREFIX}vfix-{token}"
    sess = DetectionSession(
        session_uuid=name, name=name, start_time=datetime.now(),
        project_id=proj["id"], channel_id=0,
    )
    db.add(sess)
    db.flush()
    vid = f"{E2E_PREFIX}v-{token}"
    db.add(VideoClip(video_uuid=vid, clip_type="cycle", file_path=str(clip),
                     file_name="e2e_clip.mp4", file_size=1024, created_at=datetime.now()))
    for i, good in enumerate([True, True, False, False, False]):
        db.add(DetectionCycle(
            cycle_uuid=f"{E2E_PREFIX}c{i}-{token}", session_id=sess.id,
            cycle_number=i + 1, start_time=datetime.now(), end_time=datetime.now(),
            is_good=good, video_id=vid if not good else None,
        ))
    db.commit()
    sid, pname = sess.id, proj["name"]
    db.close()

    yield {"session_id": sid, "token": token, "project_name": pname, "name": name}

    db = _db_session()
    db.query(DetectionCycle).filter(DetectionCycle.session_id == sid).delete()
    db.query(DetectionSession).filter(DetectionSession.id == sid).delete()
    db.query(VideoClip).filter(VideoClip.video_uuid == vid).delete()
    db.commit()
    db.close()


def _open_data_page_with_session(page, base_url, seed):
    page.goto(f"{base_url}/#/data", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2500)
    # 顶栏选项目
    page.locator(".el-select").first.click()
    page.wait_for_timeout(400)
    page.locator(f".el-select-dropdown__item:has-text('{seed['project_name']}')").first.click()
    page.wait_for_timeout(400)
    page.locator("button:has-text('选择')").first.click(force=True)
    page.wait_for_timeout(1500)
    # 选今天
    dp = page.locator(".el-date-editor input").first
    dp.click()
    dp.fill(datetime.now().strftime("%Y-%m-%d"))
    dp.press("Enter")
    page.wait_for_timeout(1500)
    # 选种子会话
    page.locator(f"text={seed['name']}").first.click(timeout=10000)
    page.wait_for_timeout(1200)


def test_cycle_result_filter(page, base_url, api_url, seeded_video_session):
    seed = seeded_video_session
    _open_data_page_with_session(page, base_url, seed)

    flt = page.locator("[data-testid='cycle-result-filter']")
    assert flt.count() == 1, "结果筛选控件未出现"

    flt.locator(".el-radio-button:has-text('仅 NG')").click()
    page.wait_for_timeout(1200)
    ng_tags = page.locator(".el-table__body-wrapper .el-tag:has-text('NG')")
    ok_tags = page.locator(".el-table__body-wrapper .el-tag:has-text('OK')")
    assert ok_tags.count() == 0, f"仅NG 下不应有 OK 行: {ok_tags.count()}"

    r = requests.get(f"{api_url}/api/v1/data/sessions/{seed['session_id']}/cycles",
                     params={"result": "ng"}, timeout=10)
    assert r.status_code == 200
    assert r.json()["total"] == 3
    assert ng_tags.count() == 3, f"UI NG 行数 {ng_tags.count()} != 后端 3"


def test_video_dialog_speed_and_download(page, base_url, seeded_video_session):
    seed = seeded_video_session
    _open_data_page_with_session(page, base_url, seed)

    # 仅NG 后点第一行的播放图标按钮
    page.locator("[data-testid='cycle-result-filter'] .el-radio-button:has-text('仅 NG')").click()
    page.wait_for_timeout(1200)
    page.locator(".el-table__body .el-button.is-link").first.click()
    page.wait_for_timeout(1500)

    dlg = page.locator(".el-dialog:has-text('视频播放')")
    assert dlg.count() >= 1, "播放弹窗未出现"

    sp = page.locator("[data-testid='video-speed-group']")
    assert sp.count() == 1, "倍速控件未出现"
    sp.locator(".el-radio-button:has-text('2x')").click()
    page.wait_for_timeout(500)
    rate = page.evaluate("() => { const v = document.querySelector('.el-dialog video'); return v ? v.playbackRate : null }")
    assert rate == 2, f"playbackRate={rate}"

    assert page.locator("[data-testid='video-download-btn']").count() == 1, "下载按钮未出现"
