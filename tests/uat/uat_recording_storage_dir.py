# -*- coding: utf-8 -*-
"""自定义录像存储位置 (v3.54) — 可见浏览器 UAT（路径 H, 金标准全链路）。

现场叙事:
  操作员嫌系统盘容量小, 在数据中心「记录设置」把录像存储位置改到大容量盘
  (/tmp/uat_rec_custom) → 产线跑一个检测周期 (synthetic 剧本源, 真 FFmpeg
  周期录像) → 录像文件直接写进自定义目录, 系统盘默认目录零新增 →
  操作员在数据中心点开该周期录像, 软件内正常回放 (转码/出流全链路不受
  目录位置影响) → 清空配置恢复默认。

运行前提:
  - backend 8002 以 RUNTIME_MODE=test 启动 (synthetic 端点)
  - frontend dev 6002
  - 项目根 ffmpeg/ffmpeg 可执行 (周期录像依赖)

用法:
  ~/miniconda3/envs/tianjun/bin/python tests/uat/uat_recording_storage_dir.py

证据产出: tests/uat/artifacts/recording-storage-dir/ (截图 + 录屏 + run.log)
"""
import os
import shutil
import sys
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright, expect

API = os.environ.get("UAT_API", "http://localhost:8002/api/v1")
FRONT = os.environ.get("UAT_FRONT", "http://localhost:6002")
CH = int(os.environ.get("UAT_CHANNEL", "0"))  # adopt_unbound 临时开, 同归档 UAT 先例

ART_DIR = os.path.join(os.path.dirname(__file__), "artifacts",
                       "recording-storage-dir")
CUSTOM_DIR = "/tmp/uat_rec_custom"
PROJECT_NAME = f"__uat_recdir_{int(time.time())}"

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


def _list_files_recursive(root):
    out = set()
    if os.path.isdir(root):
        for dp, _dirs, fns in os.walk(root):
            for f in fns:
                out.add(os.path.join(dp, f))
    return out


# ============================================================
# 后端数据准备 (同归档 UAT 先例)
# ============================================================

_orig_adopt_unbound = None


def seed_backend():
    global _orig_adopt_unbound
    shutil.rmtree(CUSTOM_DIR, ignore_errors=True)
    requests.put(f"{API}/data/storage/recording-dir", json={"dir": ""},
                 timeout=5)

    _orig_adopt_unbound = requests.get(
        f"{API}/projects/activate-config", timeout=5).json()["adopt_unbound"]
    requests.put(f"{API}/projects/activate-config",
                 json={"adopt_unbound": True}, timeout=5)
    log(f"✅ adopt_unbound 临时开 (原值 {_orig_adopt_unbound}, 结束还原)")

    for p in requests.get(f"{API}/projects", timeout=5).json():
        if isinstance(p, dict) and (p.get("name") or "").startswith("__uat_recdir_"):
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


def run_one_cycle():
    scenario = {
        "name": "uat_recdir_one_cycle", "fps": 30,
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

    # 等一个完整 OK 周期 (A→B→C→D 全走完, 录像才有实际时长;
    # last_first 起步可能先结出瞬时 NG 空周期, 跳过它们)
    cycle = None
    deadline = time.time() + 60
    while time.time() < deadline:
        c = _find_uat_cycle(good_only=True)
        if c and c.get("end_time") and c.get("video_id"):
            cycle = c
            break
        time.sleep(1.0)
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=30)
    requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=15)
    if not (cycle and cycle.get("end_time")):
        die("40s 内没等到已结算的周期")
    log(f"✅ 周期已结算落库: cycle_id={cycle['id']} is_good={cycle.get('is_good')} "
        f"video_id={cycle.get('video_id')}")
    return cycle


def _find_uat_cycle(good_only=False):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        body = requests.get(
            f"{API}/data/sessions/by-date/{today}", timeout=5).json()
        sessions = body.get("sessions") or []
        mine = [s for s in sessions if s.get("project_name") == PROJECT_NAME]
        for s in sorted(mine, key=lambda x: x.get("id", 0), reverse=True):
            cy = requests.get(
                f"{API}/data/sessions/{s['id']}/cycles", timeout=5).json()
            for c in sorted(cy.get("items") or [],
                            key=lambda x: x.get("id", 0), reverse=True):
                if c.get("end_time") and (not good_only or c.get("is_good")):
                    c["_session_id"] = s["id"]
                    return c
    except Exception:
        return None
    return None


# ============================================================
# 主流程
# ============================================================

def main():
    os.makedirs(os.path.join(ART_DIR, "video"), exist_ok=True)

    pid = seed_backend()

    # 基线: 默认目录现有文件快照 (跑周期后应零新增)
    default_root = requests.get(
        f"{API}/data/storage/recording-dir", timeout=5).json()["default_root"]
    baseline_default = _list_files_recursive(os.path.join(default_root, "cycles"))
    log(f"✅ 默认目录基线快照: {len(baseline_default)} 个既有文件")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=120)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 950},
            record_video_dir=os.path.join(ART_DIR, "video"),
        )
        page = ctx.new_page()

        # ---- 1. UI 设置自定义录像目录 ----
        page.goto(f"{FRONT}/#/data", wait_until="domcontentloaded")
        page.get_by_role("tab", name="记录设置").click()
        page.wait_for_selector("[data-testid='recording-dir-status']",
                               state="visible", timeout=10000)
        page.screenshot(path=os.path.join(ART_DIR, "01-default-state.png"))
        inp = page.locator("input[data-testid='recording-dir-input'], "
                           "[data-testid='recording-dir-input'] input").first
        inp.fill(CUSTOM_DIR)
        page.locator("[data-testid='recording-dir-save']").click()
        expect(page.locator("[data-testid='recording-dir-status']")
               ).to_contain_text("自定义", timeout=8000)
        page.screenshot(path=os.path.join(ART_DIR, "02-custom-saved.png"))
        log(f"✅ UI 保存自定义目录 {CUSTOM_DIR}, 状态卡显示「自定义」")

        state = requests.get(f"{API}/data/storage/recording-dir",
                             timeout=5).json()
        if not (state["using_custom"] and state["effective_root"] == CUSTOM_DIR):
            die(f"后端生效状态不对: {state}")
        log("✅ 后端 GET 双向验证: using_custom=true, effective_root 一致")

        # ---- 2. 跑一个真录像周期 ----
        r = requests.put(f"{API}/data/export-settings", json={
            "record_cycle_video": True, "video_fps": 25}, timeout=10)
        assert r.status_code == 200
        cycle = run_one_cycle()

        # ---- 3. 录像文件落位断言 ----
        # 周期录像文件名 = cycle_<完整uuid>_HHMMSS.mp4, API 返回的 cycle_uuid
        # 是完整 uuid 的前缀, 用它在自定义目录下匹配 (等延迟释放线程收尾)。
        prefix = f"cycle_{cycle['cycle_uuid']}"
        vp = None
        deadline = time.time() + 20
        while time.time() < deadline:
            hits = [p for p in _list_files_recursive(CUSTOM_DIR)
                    if os.path.basename(p).startswith(prefix)
                    and p.endswith(".mp4") and os.path.getsize(p) > 0]
            if hits:
                vp = hits[0]
                break
            time.sleep(0.5)
        if not vp:
            die(f"自定义目录下没找到本周期录像 (前缀 {prefix}); "
                f"目录现状: {sorted(_list_files_recursive(CUSTOM_DIR))[:5]}")

        # 等录像收尾: 延迟释放线程 1.5s 后 release, 文件尺寸涨到位并稳定
        last_size, stable = -1, 0
        deadline = time.time() + 30
        while time.time() < deadline:
            size = os.path.getsize(vp)
            if size > 10_000 and size == last_size:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
            last_size = size
            time.sleep(1.0)
        if not (last_size > 10_000 and stable >= 2):
            die(f"录像文件没有收尾到位: {vp} size={last_size}")
        log(f"✅ 录像直接写进自定义目录且收尾完整: {vp} ({last_size} bytes)")

        after_default = _list_files_recursive(os.path.join(default_root, "cycles"))
        new_in_default = after_default - baseline_default
        if new_in_default:
            die(f"默认目录不应有新增录像: {new_in_default}")
        log("✅ 系统盘默认目录零新增 (录像未经过默认目录)")

        # ---- 4. 软件内回放 ----
        # 后端出流链路 (含浏览器兼容转码)
        vr = requests.get(f"{API}/data/videos/{cycle['video_id']}", timeout=120)
        if vr.status_code != 200 or len(vr.content) < 1024:
            die(f"回放接口异常: {vr.status_code} len={len(vr.content)}")
        log(f"✅ 回放接口 200 (含转码), 返回 {len(vr.content)} bytes")

        # UI 播放弹窗真开: 顶栏选 UAT 项目 → 选今天 → 点开会话
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
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
        # 点有周期的已完成会话 (启动记录里可能还有一张 0 周期的运行中空卡)
        card = page.locator(".session-card:has-text('已完成')").first
        if not card.count():
            card = page.locator(".session-card").first
        card.wait_for(state="visible", timeout=10000)
        card.click()
        page.wait_for_timeout(1500)
        # 点 OK 周期行的播放按钮 (瞬时 NG 空周期的录像没有实际内容)
        play_btn = page.locator(
            ".el-table__row:has(.el-tag:has-text('OK')) .el-button.is-link").first
        try:
            play_btn.wait_for(state="visible", timeout=15000)
        except Exception:
            page.screenshot(path=os.path.join(ART_DIR, "debug-no-play-btn.png"),
                            full_page=True)
            raise
        play_btn.click()
        dlg = page.locator(".el-dialog:has-text('视频播放')")
        expect(dlg.first).to_be_visible(timeout=10000)
        # 等 <video> 元数据就绪且无错误 (转码可能耗几秒)
        ok = False
        deadline = time.time() + 60
        while time.time() < deadline:
            st = page.evaluate(
                "() => { const v = document.querySelector('.el-dialog video');"
                " if (!v) return null;"
                " return { err: !!v.error, ready: v.readyState, dur: v.duration || 0 }; }")
            if st and not st["err"] and st["ready"] >= 2 and st["dur"] > 0:
                ok = True
                break
            time.sleep(1.0)
        if not ok:
            die(f"软件内回放未就绪: {st}")
        page.screenshot(path=os.path.join(ART_DIR, "03-playback.png"))
        log(f"✅ 软件内回放成功 (readyState={st['ready']}, "
            f"duration={st['dur']:.1f}s)")

        # 关闭播放弹窗 (遮罩会挡住后续 tab 点击)
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)

        # ---- 5. UI 恢复默认 ----
        page.get_by_role("tab", name="记录设置").click()
        page.wait_for_selector("[data-testid='recording-dir-status']",
                               state="visible", timeout=10000)
        inp = page.locator("input[data-testid='recording-dir-input'], "
                           "[data-testid='recording-dir-input'] input").first
        inp.fill("")
        page.locator("[data-testid='recording-dir-save']").click()
        expect(page.locator("[data-testid='recording-dir-status']")
               ).to_contain_text("默认", timeout=8000)
        page.screenshot(path=os.path.join(ART_DIR, "04-reset-default.png"))
        state = requests.get(f"{API}/data/storage/recording-dir",
                             timeout=5).json()
        if state["using_custom"]:
            die(f"恢复默认失败: {state}")
        log("✅ UI 清空恢复默认, 后端双向验证通过")

        # 恢复默认后历史录像 (在自定义目录里) 仍可回放
        vr = requests.get(f"{API}/data/videos/{cycle['video_id']}", timeout=60)
        if vr.status_code != 200:
            die(f"恢复默认后历史录像回放失败: {vr.status_code}")
        log("✅ 恢复默认后, 自定义目录里的历史录像仍可回放 (绝对路径寻址)")

        ctx.close()
        browser.close()

    # ---- 6. 清理 ----
    requests.put(f"{API}/data/export-settings",
                 json={"record_cycle_video": False}, timeout=10)
    requests.put(f"{API}/projects/activate-config",
                 json={"adopt_unbound": bool(_orig_adopt_unbound)}, timeout=5)
    log("✅ UAT 清理完成 (录像开关复位 + adopt_unbound 还原; "
        "项目与自定义目录录像保留供人工检视)")

    log("🎉 UAT 全链路通过: UI 改存储位置 → 真录像直写自定义目录 → "
        "默认目录零新增 → 软件内回放 → 恢复默认后历史仍可看")
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
