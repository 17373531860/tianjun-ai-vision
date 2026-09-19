# -*- coding: utf-8 -*-
"""UAT 2026-09-17: 检测框 sidecar + 带框版录像（回放叠加 / 下载 / 归档投递）。

客户诉求链: 录像本身保持无框原片, 但 ①回放时可开关叠加检测框;
②可下载烧框 MP4 拷出去; ③归档规则可勾「投递带框版」自动送到客户导出路径。

四个 Phase:
  A. 数据页「记录设置」UI 开关: 录制周期视频 → 记录检测框数据 行出现 →
     开启 → GET /data/export-settings 双 true (UI→落库双向)
  B. 真实检测: 9.16 装配演示项目 + 真模型 + 真视频跑一个周期 →
     周期录像旁产出 .boxes.json → GET /videos/{id}/boxes 200
  C. 回放叠加 UI: 数据页点周期回放 → 播放器出现「检测框」开关 →
     打开后 canvas 叠加层可见 → 「下载带框版」按钮在 →
     GET /videos/{id}/annotated 200 且渲染帧真有绿框 (cv2 像素级验证)
  D. 归档带框投递: 归档弹窗新建规则勾「投递带框版录像」→ 落库验证 →
     backfill 触发归档 → 目的地文件 ≠ 原片字节 (烧框重编码) 且首帧有绿框

跑法 (后端 8001 + 前端 6001 已起, tianjun conda env):
  cd tests/uat && python uat_20260917_boxes_sidecar_annotated.py
"""
import json
import os
import sqlite3
import time
import urllib.request
from datetime import date

from _common import UatRun, launch_browser, filter_console_errors
from playwright.sync_api import sync_playwright

API = "http://localhost:8001/api/v1"
FRONT = "http://localhost:6001"
DB = "/Users/tianjun/Projects/tianjun-ai-vision/backend/sql_app.db"
VIDEO = "/tmp/916_dataset/demo_916.mp4"
MODEL = ("/Users/tianjun/Projects/tianjun-ai-vision/backend/uploads/models/"
         "e29d37c812634af383ee2dea9600637a_assembly_916_v2.pt")
PROJECT_ID = 10
SESSION_NAME = "__uat_boxes_sidecar"
RULE_NAME = "__uat_boxes_归档带框"
ARCHIVE_DEST = "/tmp/uat_boxes_archive_dest"

run = UatRun("boxes_sidecar_annotated")


def api(method, path, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    r = urllib.request.urlopen(req, timeout=timeout)
    body = r.read()
    return json.loads(body) if body else {}


def api_raw(path, timeout=300):
    """GET 二进制 (annotated 下载用), 返回 (status, bytes)。"""
    try:
        r = urllib.request.urlopen(API + path, timeout=timeout)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def q(sql, args=()):
    con = sqlite3.connect(DB)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def select_project(page):
    """顶栏项目下拉选中演示项目 (Data 页需要项目上下文)。"""
    page.locator(".el-select").first.click()
    time.sleep(0.6)
    page.locator(".el-select-dropdown__item",
                 has_text="9.16 装配演示").first.click()
    time.sleep(1.2)


def green_box_in_video(path, max_frames=120):
    """cv2 逐帧找纯绿检测框像素 (g>150, r<100, b<100 连片)。"""
    import cv2
    cap = cv2.VideoCapture(path)
    try:
        for _ in range(max_frames):
            ok, frame = cap.read()
            if not ok:
                break
            b, g, r = frame[:, :, 0].astype(int), frame[:, :, 1].astype(int), \
                frame[:, :, 2].astype(int)
            hits = ((g > 150) & (r < 100) & (b < 100)).sum()
            if hits > 50:
                return True
        return False
    finally:
        cap.release()


def main():
    # ============ Phase 0: 环境快照 ============
    orig = api("GET", "/data/export-settings")
    run.step("P0 环境快照", True,
             f"record_cycle_video={orig.get('record_cycle_video')} "
             f"record_boxes_data={orig.get('record_boxes_data')}")
    # 出厂对齐: 先确保两开关都关, Phase A 从关→开走真实 UI 路径
    api("PUT", "/data/export-settings",
        {"record_cycle_video": False, "record_boxes_data": False})
    # 清理上轮 UAT 残留
    for r_ in api("GET", "/export/video-archive/rules").get("items", []):
        if r_.get("name", "").startswith("__uat_"):
            api("DELETE", f"/export/video-archive/rules/{r_['id']}")
    try:
        api("POST", "/source/detection/stop?channel=0")
        api("POST", "/source/video/stop?channel=0")
    except Exception:
        pass
    time.sleep(1)

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(
            p, record_video_dir=run.video_dir)

        # ============ Phase A: 记录设置 UI 开关 ============
        page.goto(FRONT + "/#/data", wait_until="domcontentloaded")
        time.sleep(2.0)
        select_project(page)
        run.shot(page, "A0_data_page")

        page.locator(".el-tabs__item", has_text="记录设置").click()
        time.sleep(1.0)
        boxes_row = page.locator('[data-testid="record-boxes-switch"]')
        run.step("A1 周期视频未开时「记录检测框数据」行隐藏",
                 boxes_row.count() == 0 or not boxes_row.first.is_visible())

        cycle_row = page.locator(".setting-row", has_text="录制周期视频").first
        cycle_row.locator(".el-switch").click()
        time.sleep(1.2)
        run.step("A2 开启录制周期视频后「记录检测框数据」行出现",
                 boxes_row.first.is_visible())
        run.shot(page, "A2_boxes_switch_visible")

        boxes_row.first.click()
        time.sleep(1.5)
        cfg = api("GET", "/data/export-settings")
        run.step("A3 UI 开关 → 后端落库 (双向验证)",
                 cfg.get("record_cycle_video") is True
                 and cfg.get("record_boxes_data") is True,
                 f"GET export-settings = cycle_video:{cfg.get('record_cycle_video')} "
                 f"boxes:{cfg.get('record_boxes_data')}")
        run.shot(page, "A3_switches_on")

        # ============ Phase B: 真实检测产出 sidecar ============
        proj = api("GET", f"/projects/{PROJECT_ID}")
        api("POST", "/source/detection/set-project?channel=0", {
            "project_id": PROJECT_ID, "name": proj["name"],
            "task_type": proj["task_type"], "logic_mode": proj["logic_mode"],
            "steps_config": proj["steps_config"] or [],
            "pipeline_config": proj["pipeline_config"] or {},
            "events_config": proj["events_config"] or [],
            "counters_config": proj.get("counters_config") or [],
            "data_config": proj.get("data_config") or {},
        })
        api("POST", "/source/video/start?channel=0",
            {"file_path": VIDEO, "speed": 1.5})
        d = api("POST", "/source/detection/start?channel=0", {
            "model_path": MODEL, "conf": 0.2, "iou": 0.45,
            "session_name": SESSION_NAME,
        })
        sid = d.get("session_id")
        run.step("B1 检测已启动", bool(sid), f"session_id={sid}")

        # 等周期结算 (视频 262s @1.5x ≈ 175s, 最多等 260s)
        cycle_row_db = None
        t0 = time.time()
        while time.time() - t0 < 260:
            rows = q("select id, cycle_uuid, video_path, video_id from "
                     "detection_cycles where session_id=? and end_time is not null",
                     (sid,))
            if rows and rows[0][2]:
                cycle_row_db = rows[0]
                break
            time.sleep(5)
        run.step("B2 周期已结算且带录像", cycle_row_db is not None,
                 f"等待 {round(time.time() - t0)}s, cycle={cycle_row_db}")

        api("POST", "/source/detection/stop?channel=0")
        api("POST", "/source/video/stop?channel=0")
        time.sleep(6)  # 延迟释放 1.5s + sidecar flush + 归档 notify

        if cycle_row_db is None:
            run.step("B3~D 后续依赖周期录像, 全部跳过", False, "B2 失败")
            ctx.close(); browser.close()
            return

        cycle_id, cycle_uuid, video_path, video_uuid = cycle_row_db
        sidecar = video_path + ".boxes.json"
        run.step("B3 sidecar 文件落盘", os.path.isfile(sidecar), sidecar)
        st, body = api_raw(f"/data/videos/{video_uuid}/boxes")
        n_frames = 0
        if st == 200:
            n_frames = len(json.loads(body).get("frames", []))
        run.step("B4 GET /videos/{id}/boxes 返回检测框数据",
                 st == 200 and n_frames > 0, f"status={st} entries={n_frames}")

        # ============ Phase C: 回放叠加 UI ============
        page.goto(FRONT + "/#/data", wait_until="domcontentloaded")
        time.sleep(2.0)
        select_project(page)
        today = date.today().strftime("%Y-%m-%d")
        dp = page.locator('input[placeholder="选择日期"]').first
        dp.click(); time.sleep(0.5)
        dp.fill(today); dp.press("Enter")
        time.sleep(2.5)
        play_btn = page.locator('[data-testid="cycle-play-btn"]')
        if play_btn.count() == 0:
            # 兜底: 先点会话行
            try:
                page.get_by_text(SESSION_NAME, exact=False).first.click()
                time.sleep(2.0)
            except Exception:
                pass
        run.step("C1 周期表出现回放按钮", play_btn.count() > 0,
                 f"count={play_btn.count()}")
        run.shot(page, "C1_cycle_table")

        play_btn.first.click()
        time.sleep(3.0)
        toggle = page.locator('[data-testid="video-boxes-toggle"]')
        run.step("C2 播放器出现「检测框」开关", toggle.first.is_visible())
        dl_btn = page.locator('[data-testid="video-download-annotated-btn"]')
        run.step("C3 「下载带框版」按钮可见", dl_btn.first.is_visible())
        run.shot(page, "C2_player_toggle")

        toggle.locator(".el-switch").click()
        time.sleep(2.0)
        overlay = page.locator('[data-testid="video-boxes-overlay"]')
        run.step("C4 打开开关后叠加 canvas 可见", overlay.first.is_visible())
        run.shot(page, "C4_overlay_on")

        # 带框下载走 API 验证 (浏览器原生下载拿不到完成事件)
        st, blob = api_raw(f"/data/videos/{video_uuid}/annotated", timeout=300)
        run.step("C5 GET /videos/{id}/annotated 渲染成功",
                 st == 200 and len(blob) > 10000,
                 f"status={st} size={len(blob)}")
        boxed_cache = None
        if st == 200:
            boxed_cache = os.path.join(run.dir, "annotated_download.mp4")
            with open(boxed_cache, "wb") as f:
                f.write(blob)
            run.step("C6 渲染产物帧内真有绿色检测框 (cv2 像素级)",
                     green_box_in_video(boxed_cache), boxed_cache)

        # 关掉播放器
        page.keyboard.press("Escape"); time.sleep(1.0)

        # ============ Phase D: 归档规则带框投递 ============
        os.makedirs(ARCHIVE_DEST, exist_ok=True)
        page.locator(".el-tabs__item", has_text="存储与清理").click()
        time.sleep(1.0)
        page.locator("button", has_text="配置录像归档").click()
        time.sleep(1.5)
        page.locator("button", has_text="新建规则").click()
        time.sleep(1.0)
        editor = page.locator(".el-dialog", has_text="新建归档规则").last
        editor.locator('input[placeholder*="NG 录像归档"]').fill(RULE_NAME)
        # 结果筛选 → 全部周期
        editor.locator(".el-form-item", has_text="结果筛选").locator(
            ".el-select").click()
        time.sleep(0.5)
        page.locator(".el-select-dropdown__item", has_text="全部周期").last.click()
        time.sleep(0.5)
        editor.locator('input[placeholder*="NG归档"]').fill(ARCHIVE_DEST)
        annotated_sw = editor.locator('[data-testid="rule-annotated-switch"]')
        annotated_sw.click()
        time.sleep(0.5)
        run.shot(page, "D1_rule_editor")
        editor.locator("button", has_text="保存").click()
        time.sleep(2.0)

        rules = api("GET", "/export/video-archive/rules").get("items", [])
        rule = next((r_ for r_ in rules if r_["name"] == RULE_NAME), None)
        run.step("D2 规则落库且 annotated_video=true (双向验证)",
                 rule is not None and rule.get("annotated_video") is True,
                 f"rule={{'id': {rule and rule.get('id')}, "
                 f"'annotated_video': {rule and rule.get('annotated_video')}}}")
        run.shot(page, "D2_rule_list")

        if rule:
            api("POST", "/export/video-archive/backfill",
                {"rule_id": rule["id"], "date_from": today, "date_to": today})
            dest_file = None
            t0 = time.time()
            while time.time() - t0 < 90:
                found = []
                for root, _dirs, files in os.walk(ARCHIVE_DEST):
                    found += [os.path.join(root, f_) for f_ in files
                              if f_.endswith(".mp4")]
                if found:
                    dest_file = found[0]
                    break
                time.sleep(3)
            run.step("D3 归档 worker 已投递到目的地", dest_file is not None,
                     f"dest={dest_file}")
            if dest_file:
                orig_size = os.path.getsize(video_path)
                dest_size = os.path.getsize(dest_file)
                run.step("D4 投递物为烧框重编码版 (≠原片字节)",
                         dest_size != orig_size,
                         f"原片 {orig_size}B vs 投递 {dest_size}B")
                run.step("D5 投递物帧内真有绿色检测框 (cv2 像素级)",
                         green_box_in_video(dest_file), dest_file)

        # 控制台报错检查
        real = filter_console_errors(cerrs)
        run.step("E1 前端控制台无逻辑报错", not real, f"errs={real[:3]}")

        ctx.close(); browser.close()

    # ============ 清理 ============
    for r_ in api("GET", "/export/video-archive/rules").get("items", []):
        if r_.get("name", "").startswith("__uat_"):
            api("DELETE", f"/export/video-archive/rules/{r_['id']}")
    api("PUT", "/data/export-settings", {
        "record_cycle_video": bool(orig.get("record_cycle_video")),
        "record_boxes_data": bool(orig.get("record_boxes_data")),
    })
    run.step("Z1 清理完成 (规则删除 + 设置还原)", True)


if __name__ == "__main__":
    try:
        main()
    finally:
        raise SystemExit(run.finish())
