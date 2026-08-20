# -*- coding: utf-8 -*-
"""录像归档 二~四期 — 可见浏览器 UAT（路径 H, 金标准全链路）。

现场叙事:
  质检主管配一条「NG 证据归档」规则: 目的地本地目录 + 附带 NG 关键帧快照 +
  打包成证据包 zip → 产线跑一个 NG 周期 (synthetic 剧本漏步骤, last_first 结算
  即 NG) → NG 结算瞬间推理线程抽带框关键帧, 归档 worker 把 录像+关键帧+meta
  打成一个 zip 自动落位 → 主管在台账看到成功记录, 再用「导出证据包」按当日
  批量下载一份 zip 复查。

运行前提: backend 8002 (RUNTIME_MODE=test) + frontend 6002 + 根目录 ffmpeg。

用法:
  ~/miniconda3/envs/tianjun/bin/python tests/uat/uat_video_archive_phase234.py

证据产出: tests/uat/artifacts/video-archive-phase234/ (截图 + 录屏 + run.log)
"""
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright, expect

API = os.environ.get("UAT_API", "http://localhost:8002/api/v1")
FRONT = os.environ.get("UAT_FRONT", "http://localhost:6002")
CH = 2  # 未绑定工位 (同一期 UAT)

ART_DIR = os.path.join(os.path.dirname(__file__), "artifacts",
                       "video-archive-phase234")
DEST_DIR = "/tmp/uat_va_evidence"
RULE_NAME = "__uat_NG证据归档"
PROJECT_NAME = f"__uat_vae_{int(time.time())}"

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


_orig_adopt_unbound = None


def seed_backend():
    global _orig_adopt_unbound
    shutil.rmtree(DEST_DIR, ignore_errors=True)

    _orig_adopt_unbound = requests.get(
        f"{API}/projects/activate-config", timeout=5).json()["adopt_unbound"]
    requests.put(f"{API}/projects/activate-config",
                 json={"adopt_unbound": True}, timeout=5)

    for r in requests.get(f"{API}/export/video-archive/rules",
                          timeout=5).json()["items"]:
        if r["name"].startswith("__uat_"):
            requests.delete(f"{API}/export/video-archive/rules/{r['id']}",
                            timeout=5)
    for p in requests.get(f"{API}/projects", timeout=5).json():
        if isinstance(p, dict) and (p.get("name") or "").startswith("__uat_vae_"):
            requests.delete(f"{API}/projects/{p['id']}", timeout=5)

    payload = {
        "name": PROJECT_NAME, "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": [
            {"id": i, "label": lab, "enabled": True, "min_frames": 1,
             "threshold": 0.3}
            for i, lab in enumerate(["A", "B", "C", "D"], start=1)
        ],
        "events_config": [
            {"id": 1, "name": "合格", "actions": []},
            {"id": 2, "name": "不合格", "actions": []},
        ],
        "pipeline_config": {
            "sequence_order": [{"step_id": i} for i in range(1, 5)],
            "settlement_mode": "last_first",
            "settle_dedup": False,
        },
        "counters_config": [], "alarm_config": {},
        "detection_config": {},
        "data_config": {
            "record_cycle_video": True, "record_step_video": False,
            "record_session_video": False,
            "video_quality": "medium", "video_fps": 25,
        },
    }
    r = requests.post(f"{API}/projects", json=payload, timeout=10)
    assert r.status_code in (200, 201), f"create project: {r.text[:300]}"
    pid = r.json()["id"]
    r = requests.post(f"{API}/projects/{pid}/activate", timeout=30)
    assert r.status_code == 200, f"activate: {r.text[:300]}"
    log(f"✅ last_first 项目已建并激活 (id={pid})")
    return pid


def run_one_ng_cycle():
    """剧本只出 A → D (漏 B/C), last_first 结算 → NG 周期 (触发关键帧抽帧)。"""
    scenario = {
        "name": "uat_vae_ng_cycle", "fps": 30,
        "timeline": [
            {"from": 0, "to": 9, "detections": []},
            {"from": 10, "to": 49, "detections": [
                {"label": "A", "confidence": 0.95, "bbox": [0.1, 0.1, 0.18, 0.18]}]},
            {"from": 50, "to": 99, "detections": [
                {"label": "D", "confidence": 0.95, "bbox": [0.7, 0.1, 0.18, 0.18]}]},
            {"from": 100, "to": 200, "detections": []},
        ],
    }
    r = requests.post(f"{API}/test/synthetic/start", json={
        "scenario_json": scenario, "channel": CH, "with_project": False,
    }, timeout=15)
    assert r.status_code == 200, f"synthetic start: {r.text[:300]}"
    r = requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"conf": 0.25}, timeout=60)
    assert r.status_code == 200, f"detection start: {r.text[:300]}"
    log("✅ synthetic NG 剧本 (A→D 漏 B/C) + 检测已启动, 等待 NG 结算…")

    cycle = None
    deadline = time.time() + 40
    while time.time() < deadline:
        cycle = _find_uat_cycle()
        if cycle and cycle.get("end_time") and cycle.get("is_good") is False:
            break
        time.sleep(1.0)
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=30)
    requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=15)
    if not (cycle and cycle.get("end_time") and cycle.get("is_good") is False):
        die(f"40s 内没等到 NG 结算周期 (got={cycle})")
    log(f"✅ NG 周期已结算: cycle_id={cycle['id']}")
    return cycle


def _find_uat_cycle():
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        body = requests.get(
            f"{API}/data/sessions/by-date/{today}", timeout=5).json()
        mine = [s for s in (body.get("sessions") or [])
                if s.get("project_name") == PROJECT_NAME]
        for s in sorted(mine, key=lambda x: x.get("id", 0), reverse=True):
            cy = requests.get(
                f"{API}/data/sessions/{s['id']}/cycles", timeout=5).json()
            for c in sorted(cy.get("items") or [],
                            key=lambda x: x.get("id", 0), reverse=True):
                if c.get("end_time") and c.get("is_good") is False:
                    return c
    except Exception:
        return None
    return None


def main():
    os.makedirs(os.path.join(ART_DIR, "video"), exist_ok=True)
    seed_backend()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=150)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 950},
            record_video_dir=os.path.join(ART_DIR, "video"),
            accept_downloads=True,
        )
        page = ctx.new_page()

        # ---- 1. UI 建「NG 证据归档」规则 (关键帧 + 证据包 zip) ----
        page.goto(f"{FRONT}/#/data", wait_until="domcontentloaded")
        page.get_by_role("tab", name="存储与清理").click()
        card = page.locator("[data-test='video-archive-card']")
        expect(card).to_be_visible(timeout=10000)
        card.get_by_role("button", name="配置录像归档").click()
        dialog = page.locator(".va-dialog")
        expect(dialog).to_be_visible(timeout=5000)
        dialog.get_by_role("button", name="新建规则").click()
        editor = page.locator(".el-dialog").filter(has_text="新建归档规则")
        expect(editor).to_be_visible(timeout=5000)
        editor.get_by_placeholder("如：NG 录像归档到质量部网盘").fill(RULE_NAME)
        editor.get_by_placeholder(
            "如 D:\\NG归档 或 \\\\server\\quality\\videos").fill(DEST_DIR)
        # 证据能力: 附带关键帧 + 打包 zip (默认仅 NG 保持)
        editor.locator(".el-form-item", has_text="附带 NG 关键帧快照"
                       ).locator(".el-switch").click()
        editor.locator(".el-form-item", has_text="打包成证据包"
                       ).locator(".el-switch").click()
        page.screenshot(path=os.path.join(ART_DIR, "01-evidence-rule-form.png"))
        editor.get_by_role("button", name="保存").click()
        expect(editor).to_be_hidden(timeout=5000)
        expect(dialog.locator(".el-table").first).to_contain_text(
            RULE_NAME, timeout=5000)
        page.screenshot(path=os.path.join(ART_DIR, "02-rule-created.png"))

        rules = requests.get(f"{API}/export/video-archive/rules",
                             timeout=5).json()["items"]
        mine = [x for x in rules if x["name"] == RULE_NAME]
        if not mine or not mine[0]["attach_keyframe"] or not mine[0]["bundle_zip"]:
            die(f"规则落库不对: {mine}")
        log("✅ UI 建规则 → 后端落库 (attach_keyframe + bundle_zip 双开)")
        page.keyboard.press("Escape")

        # ---- 2. 跑一个 NG 周期 (真录像 + 结算瞬间抽关键帧) ----
        r = requests.put(f"{API}/data/export-settings", json={
            "record_cycle_video": True, "video_fps": 25}, timeout=10)
        assert r.status_code == 200
        cycle = run_one_ng_cycle()

        # ---- 3. 等证据包 zip 自动落位并解剖 ----
        day_dir = os.path.join(DEST_DIR, datetime.now().strftime("%Y-%m-%d"))
        zips = []
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.isdir(day_dir):
                zips = [f for f in os.listdir(day_dir) if f.endswith(".zip")]
                if zips:
                    break
            time.sleep(1.0)
        if not zips:
            st = requests.get(f"{API}/export/video-archive/status",
                              timeout=5).json()
            die(f"30s 内没出现证据包 zip; status={json.dumps(st, ensure_ascii=False)}")
        zpath = os.path.join(day_dir, sorted(zips)[0])
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            has_mp4 = any(n.endswith(".mp4") for n in names)
            has_jpg = any(n.endswith(".jpg") for n in names)
            has_meta = any(n.endswith("_meta.json") for n in names)
            if not has_mp4:
                die(f"证据包缺录像: {names}")
            if not has_jpg:
                die(f"证据包缺 NG 关键帧 (结算瞬间抽帧链路断): {names}")
            if not has_meta:
                die(f"证据包缺 meta.json: {names}")
            meta = json.loads(zf.read(
                [n for n in names if n.endswith("_meta.json")][0]))
            if meta.get("result") != "NG":
                die(f"meta 结果不是 NG: {meta}")
        log(f"✅ 证据包 zip 自动落位且三件俱全 (录像+关键帧+meta): "
            f"{os.path.basename(zpath)} → {names}")

        # ---- 4. UI 台账观测 ----
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("tab", name="存储与清理").click()
        expect(card).to_be_visible(timeout=10000)
        card.get_by_role("button", name="配置录像归档").click()
        expect(dialog).to_be_visible(timeout=5000)
        dialog.get_by_role("tab", name="归档台账").click()
        expect(dialog.locator(".el-table").last).to_contain_text(
            "success", timeout=8000)
        page.screenshot(path=os.path.join(ART_DIR, "03-ledger.png"))
        log("✅ 归档台账呈现 success 记录")

        # ---- 5. UI 手动「导出证据包」当日批量下载 ----
        dialog.get_by_role("button", name="导出证据包").click()
        pack = page.locator(".el-dialog").filter(has_text="只打包 NG 周期")
        expect(pack).to_be_visible(timeout=5000)
        date_input = pack.locator("input").first
        date_input.fill(datetime.now().strftime("%Y-%m-%d"))
        date_input.press("Enter")  # 提交 v-model 并收起日期面板
        page.screenshot(path=os.path.join(ART_DIR, "04-pack-form.png"))
        with page.expect_download(timeout=60000) as dl_info:
            pack.get_by_role("button", name="打包下载").click()
        dl = dl_info.value
        saved = os.path.join(ART_DIR, "manual_evidence_pack.zip")
        dl.save_as(saved)
        with zipfile.ZipFile(saved) as zf:
            names = zf.namelist()
            if not any(n.endswith("video.mp4") for n in names):
                die(f"手动证据包缺录像: {names}")
            if not any(n.endswith("meta.json") for n in names):
                die(f"手动证据包缺 meta: {names}")
        log(f"✅ UI 手动导出证据包成功 ({len(names)} 个文件): {names[:6]}…")
        page.screenshot(path=os.path.join(ART_DIR, "05-pack-downloaded.png"))

        ctx.close()
        browser.close()

    # ---- 6. 清理 ----
    for r in requests.get(f"{API}/export/video-archive/rules",
                          timeout=5).json()["items"]:
        if r["name"].startswith("__uat_"):
            requests.delete(f"{API}/export/video-archive/rules/{r['id']}",
                            timeout=5)
    requests.put(f"{API}/data/export-settings",
                 json={"record_cycle_video": False}, timeout=10)
    requests.put(f"{API}/projects/activate-config",
                 json={"adopt_unbound": bool(_orig_adopt_unbound)}, timeout=5)
    log("✅ UAT 清理完成 (规则删除 + 录像开关复位 + adopt_unbound 还原)")

    log("🎉 UAT 全链路通过: UI 配证据规则 → NG 周期结算瞬间抽关键帧 → "
        "证据包 zip 自动落位 (录像+关键帧+meta) → 台账观测 → UI 手动批量导出")
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
