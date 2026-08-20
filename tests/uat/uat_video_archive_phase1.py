# -*- coding: utf-8 -*-
"""录像归档一期 — 可见浏览器 UAT（路径 H, 金标准全链路）。

现场叙事:
  质检主管在数据中心「存储与清理」页配一条归档规则（全部周期 → /tmp/uat_video_archive,
  按周期号+结果命名）→ 产线跑一个检测周期（synthetic 剧本源, 真 FFmpeg 周期录像）→
  周期结束录像收尾后, 归档 worker 自动把 mp4 按模板改名拷到目标目录 → 主管在
  归档台账里看到成功记录, 目标目录出现证据文件。

运行前提:
  - backend 8002 以 RUNTIME_MODE=test 启动 (synthetic 端点)
  - frontend dev 6002
  - 项目根 ffmpeg/ffmpeg 可执行 (周期录像依赖)

用法:
  ~/miniconda3/envs/tianjun/bin/python tests/uat/uat_video_archive_phase1.py

证据产出: tests/uat/artifacts/video-archive-phase1/ (截图 + 录屏 + run.log)
"""
import json
import os
import shutil
import sys
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright, expect

API = os.environ.get("UAT_API", "http://localhost:8002/api/v1")
FRONT = os.environ.get("UAT_FRONT", "http://localhost:6002")
# 工位 2: 本机开发库工位 0/1 分别绑死项目 2/3 (adopt_unbound=false 时激活
# 不会接管它们), 用未绑定的工位 2 + 临时打开收养开关让 UAT 项目落到它头上
CH = 2

ART_DIR = os.path.join(os.path.dirname(__file__), "artifacts", "video-archive-phase1")
DEST_DIR = "/tmp/uat_video_archive"
RULE_NAME = "__uat_归档规则_全部周期"
PROJECT_NAME = f"__uat_va_{int(time.time())}"

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


# ============================================================
# 后端数据准备
# ============================================================

_orig_adopt_unbound = None


def seed_backend():
    global _orig_adopt_unbound
    # 0) 目标目录清场
    shutil.rmtree(DEST_DIR, ignore_errors=True)

    # 0.5) 临时打开"激活收养未绑定工位"开关 (本库默认关, 结束还原)
    _orig_adopt_unbound = requests.get(
        f"{API}/projects/activate-config", timeout=5).json()["adopt_unbound"]
    requests.put(f"{API}/projects/activate-config",
                 json={"adopt_unbound": True}, timeout=5)
    log(f"✅ adopt_unbound 临时开 (原值 {_orig_adopt_unbound}, 结束还原)")

    # 1) 清掉旧 UAT 规则/项目
    for r in requests.get(f"{API}/export/video-archive/rules", timeout=5).json()["items"]:
        if r["name"].startswith("__uat_"):
            requests.delete(f"{API}/export/video-archive/rules/{r['id']}", timeout=5)
    for p in requests.get(f"{API}/projects", timeout=5).json():
        if isinstance(p, dict) and (p.get("name") or "").startswith("__uat_va_"):
            requests.delete(f"{API}/projects/{p['id']}", timeout=5)

    # 3) 建 last_first 项目并激活 (末步出现即结算, 一个剧本一个周期)
    # ⚠ data_config 必须带录像开关: Data 页挂载会把激活项目的 data_config
    # 整体同步覆写到全局 export-settings (loadExportSettings 既有设计),
    # 空 data_config 会把手动 PUT 的开关刷回默认 false。
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


def run_one_cycle():
    """synthetic 跑一个 A→B→C→D 完整周期 (末步 D 出现即 OK 结算)。"""
    scenario = {
        "name": "uat_va_one_cycle", "fps": 30,
        "timeline": [
            {"from": 0, "to": 9, "detections": []},
            {"from": 10, "to": 39, "detections": [
                {"label": "A", "confidence": 0.95, "bbox": [0.1, 0.1, 0.18, 0.18]}]},
            {"from": 40, "to": 69, "detections": [
                {"label": "B", "confidence": 0.95, "bbox": [0.3, 0.1, 0.18, 0.18]}]},
            {"from": 70, "to": 99, "detections": [
                {"label": "C", "confidence": 0.95, "bbox": [0.5, 0.1, 0.18, 0.18]}]},
            {"from": 100, "to": 129, "detections": [
                {"label": "D", "confidence": 0.95, "bbox": [0.7, 0.1, 0.18, 0.18]}]},
            {"from": 130, "to": 200, "detections": []},
        ],
    }
    r = requests.post(f"{API}/test/synthetic/start", json={
        "scenario_json": scenario, "channel": CH, "with_project": False,
    }, timeout=15)
    assert r.status_code == 200, f"synthetic start: {r.text[:300]}"
    r = requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"conf": 0.25}, timeout=60)
    assert r.status_code == 200, f"detection start: {r.text[:300]}"
    log("✅ synthetic 剧本 + 检测已启动, 等待周期结算…")

    # 等周期出现在 DB (带 video_path)
    cycle = None
    deadline = time.time() + 40
    while time.time() < deadline:
        cycle = _find_uat_cycle()
        if cycle and cycle.get("end_time"):
            break
        time.sleep(1.0)
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=30)
    requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=15)
    if not (cycle and cycle.get("end_time")):
        die("40s 内没等到已结算的周期 (last_first 剧本没触发 cycle_end?)")
    log(f"✅ 周期已结算落库: cycle_id={cycle['id']} is_good={cycle.get('is_good')}")
    return cycle


def _find_uat_cycle():
    """按今天的 session 找本 UAT 项目的最新周期。"""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        body = requests.get(
            f"{API}/data/sessions/by-date/{today}", timeout=5).json()
        sessions = body.get("sessions") or []
        # 只看本 UAT 项目的 session (名字每次运行唯一), 别捞到开发库里别的。
        # 注: 不按 channel_id 过滤 — by-date 列表里该字段与实际工位有出入 (另案)
        mine = [s for s in sessions if s.get("project_name") == PROJECT_NAME]
        for s in sorted(mine, key=lambda x: x.get("id", 0), reverse=True):
            cy = requests.get(
                f"{API}/data/sessions/{s['id']}/cycles", timeout=5).json()
            for c in sorted(cy.get("items") or [],
                            key=lambda x: x.get("id", 0), reverse=True):
                if c.get("end_time"):
                    return c
    except Exception:
        return None
    return None


# ============================================================
# 主流程
# ============================================================

def main():
    os.makedirs(os.path.join(ART_DIR, "video"), exist_ok=True)

    seed_backend()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=150)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 950},
            record_video_dir=os.path.join(ART_DIR, "video"),
        )
        page = ctx.new_page()

        # ---- 1. Data 页 → 存储与清理 → 录像归档卡 ----
        page.goto(f"{FRONT}/#/data", wait_until="domcontentloaded")
        page.get_by_role("tab", name="存储与清理").click()
        card = page.locator("[data-test='video-archive-card']")
        expect(card).to_be_visible(timeout=10000)
        page.screenshot(path=os.path.join(ART_DIR, "01-card-initial.png"))
        log("✅ 存储与清理页出现「录像归档」卡")

        # ---- 2. 打开弹窗, UI 建规则 ----
        card.get_by_role("button", name="配置录像归档").click()
        dialog = page.locator(".va-dialog")
        expect(dialog).to_be_visible(timeout=5000)
        dialog.get_by_role("button", name="新建规则").click()
        editor = page.locator(".el-dialog").filter(has_text="新建归档规则")
        expect(editor).to_be_visible(timeout=5000)
        editor.get_by_placeholder("如：NG 录像归档到质量部网盘").fill(RULE_NAME)
        editor.get_by_placeholder(
            "如 D:\\NG归档 或 \\\\server\\quality\\videos").fill(DEST_DIR)
        # 结果筛选改成「全部周期」(synthetic 周期是 OK)
        editor.locator(".el-select").first.click()
        page.locator(".el-select-dropdown__item").filter(
            has_text="全部周期").first.click()
        page.screenshot(path=os.path.join(ART_DIR, "02-rule-form.png"))
        editor.get_by_role("button", name="保存").click()
        expect(editor).to_be_hidden(timeout=5000)
        expect(dialog.locator(".el-table").first).to_contain_text(
            RULE_NAME, timeout=5000)
        page.screenshot(path=os.path.join(ART_DIR, "03-rule-created.png"))

        # 双向验证: 规则落库
        rules = requests.get(f"{API}/export/video-archive/rules",
                             timeout=5).json()["items"]
        mine = [x for x in rules if x["name"] == RULE_NAME]
        if not mine or mine[0]["result_filter"] != "all":
            die(f"规则落库不对: {mine}")
        log("✅ UI 建规则 → 后端落库双向验证通过 (result_filter=all)")

        # 关掉弹窗跑周期
        page.keyboard.press("Escape")

        # ---- 3. 跑一个真录像周期 ----
        # 再确认全局录像开关 (Data 页挂载会用项目 data_config 覆写全局,
        # 项目里已带 true; 这里 PUT 一次双保险, 必须在 detection start 之前)
        r = requests.put(f"{API}/data/export-settings", json={
            "record_cycle_video": True, "video_fps": 25}, timeout=10)
        assert r.status_code == 200
        got = requests.get(f"{API}/data/export-settings", timeout=5).json()
        if not got.get("record_cycle_video"):
            die("record_cycle_video 置位失败")
        log("✅ 周期录像开关确认为开 (record_cycle_video=true)")
        cycle = run_one_cycle()

        # ---- 4. 等归档 worker 自动搬运 (1.5s drain + 文件稳定 + 拷贝) ----
        # 注: last_first 剧本可能结算多个周期 (含 D 残影空周期 NG), 逐个都会归档
        day_dir = os.path.join(DEST_DIR, datetime.now().strftime("%Y-%m-%d"))
        files = []
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.isdir(day_dir):
                files = [f for f in os.listdir(day_dir) if f.endswith(".mp4")]
                if files:
                    break
            time.sleep(1.0)
        if not files:
            st = requests.get(f"{API}/export/video-archive/status", timeout=5).json()
            die(f"30s 内目标目录没出现归档文件; status={json.dumps(st, ensure_ascii=False)}")
        log(f"✅ 归档文件自动落位: {day_dir} 共 {len(files)} 个 mp4")
        import re
        for f in files:
            if not re.match(r"^\d+_(OK|NG)(_\d+)?\.mp4$", f):
                die(f"文件名模板渲染不对: {f} (期望 <周期号>_<OK|NG>.mp4)")
            if os.path.getsize(os.path.join(day_dir, f)) <= 0:
                die(f"归档文件是空的: {f}")
        log("✅ 全部文件名符合模板 <周期号>_<OK|NG>.mp4 且非空")

        # 台账 API 交叉验证: success 记录的 dest_path 真实存在
        logs_body = requests.get(
            f"{API}/export/video-archive/logs",
            params={"status": "success", "limit": 5}, timeout=5).json()
        if not logs_body["items"]:
            die("归档台账没有 success 记录")
        sample = logs_body["items"][0]
        if not (sample["dest_path"] and os.path.isfile(sample["dest_path"])):
            die(f"台账 dest_path 与磁盘不符: {sample['dest_path']}")
        log(f"✅ 台账 success 记录与磁盘文件一致 (cycle #{sample['cycle_id']} → "
            f"{os.path.basename(sample['dest_path'])})")

        # 源文件仍在 (一期只拷贝不删) — 用台账里的 src_path 验证
        if not (sample["src_path"] and os.path.isfile(sample["src_path"])):
            die(f"源录像文件不见了 (一期不应删除本地件): {sample['src_path']}")
        log("✅ 本地源录像保留 (一期只拷不删)")

        # ---- 5. UI 观测: 状态条 + 台账 ----
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("tab", name="存储与清理").click()
        expect(card).to_be_visible(timeout=10000)
        expect(card).to_contain_text("累计成功", timeout=5000)
        page.screenshot(path=os.path.join(ART_DIR, "04-card-after-archive.png"))
        card.get_by_role("button", name="配置录像归档").click()
        expect(dialog).to_be_visible(timeout=5000)
        expect(dialog.locator(".va-status-bar")).to_contain_text("✓", timeout=8000)
        page.screenshot(path=os.path.join(ART_DIR, "05-dialog-status.png"))
        dialog.get_by_role("tab", name="归档台账").click()
        expect(dialog.locator(".el-table").last).to_contain_text(
            "success", timeout=5000)
        page.screenshot(path=os.path.join(ART_DIR, "06-ledger.png"))
        log("✅ UI 状态卡 + 弹窗状态条 + 归档台账 全部呈现成功记录")

        ctx.close()
        browser.close()

    # ---- 6. 清理 ----
    for r in requests.get(f"{API}/export/video-archive/rules", timeout=5).json()["items"]:
        if r["name"].startswith("__uat_"):
            requests.delete(f"{API}/export/video-archive/rules/{r['id']}", timeout=5)
    requests.put(f"{API}/data/export-settings",
                 json={"record_cycle_video": False}, timeout=10)
    requests.put(f"{API}/projects/activate-config",
                 json={"adopt_unbound": bool(_orig_adopt_unbound)}, timeout=5)
    log("✅ UAT 清理完成 (规则删除 + 录像开关复位 + adopt_unbound 还原; "
        "项目与归档文件保留供人工检视)")

    log("🎉 UAT 全链路通过: UI 建规则 → 真录像周期 → 自动归档 → 模板命名 → 台账观测")
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
