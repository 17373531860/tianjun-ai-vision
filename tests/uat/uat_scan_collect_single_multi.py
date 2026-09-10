"""v3.56.0a — 单工位完整面板 + 多工位紧凑态 双向 UAT。

现场六和是多工位，但「本件扫完」在两种形态落点不同：
  · 单工位 / 放大 / kiosk：完整面板标题栏直接出按钮
  · 双/三工位：紧凑一行，按钮在「明细」浮层里
本脚本切工位数验证两条 UI 路径，收尾恢复原工位数。

跑法（backend 8001 + frontend 6001）：
  python tests/uat/uat_scan_collect_single_multi.py
证据：/tmp/uat_scan_sm/ 截图 + run.log；/tmp/uat_video/
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:6001")
API = os.environ.get("E2E_API_URL", "http://localhost:8001") + "/api/v1"
SHOT_DIR = Path("/tmp/uat_scan_sm")
VIDEO_DIR = Path("/tmp/uat_video")
SHOT_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

PASSED, FAILED = [], []
LOG = open(SHOT_DIR / "run.log", "w", encoding="utf-8")

BUS = "M010200519A100005036272608310061"
CHIP1, CHIP2 = "9260000144908", "9260000145631"
FIX = "H-C035-527-5"


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    log(f"{'✅ PASS' if cond else '❌ FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def shot(page, name):
    p = SHOT_DIR / f"{len(PASSED)+len(FAILED):02d}_{name}.png"
    page.screenshot(path=str(p))
    log(f"📷 {p.name}")


def scan(code, channel):
    r = requests.post(f"{API}/scanner/simulate",
                      json={"barcode": code, "channel_id": channel}, timeout=5)
    log(f"🔫 扫码 ch{channel}: {code} → {r.status_code}")
    time.sleep(0.6)
    return r


def sc_state(channel):
    return requests.get(f"{API}/scan-collect/state?channel={channel}",
                        timeout=5).json()


def set_count(n):
    r = requests.post(f"{API}/workstations/mode",
                      json={"channel_count": n, "channels": []}, timeout=15)
    log(f"工位数 → {n}: HTTP {r.status_code}")
    return r.status_code == 200


def bind_project(ch, pid):
    r = requests.put(f"{API}/workstations/channel-config",
                     json={"channel_id": ch, "project_id": pid}, timeout=10)
    log(f"绑定 ch{ch} → project {pid}: HTTP {r.status_code}")
    return r.status_code == 200


def activate(pid):
    r = requests.post(f"{API}/projects/{pid}/activate", timeout=30)
    log(f"激活项目 {pid}: HTTP {r.status_code}")
    return r.status_code == 200


def configure(page, proj_name):
    page.goto(f"{BASE}/#/mes", wait_until="domcontentloaded")
    page.get_by_role("button", name="扫码器").click()
    page.get_by_role("tab", name="多码采集").click()
    expect(page.get_by_test_id("sc-project-select")).to_be_visible(timeout=8000)
    page.get_by_role("button", name="填入示例").click()
    rows = page.locator(".el-table__body tr")
    expect(rows).to_have_count(3, timeout=5000)
    chip = rows.nth(1).locator(".el-input-number input")
    chip.fill("2")
    chip.press("Enter")
    page.get_by_test_id("sc-enabled-switch").click()
    page.get_by_test_id("sc-save-btn").click()
    expect(page.locator(".el-message--success").last).to_be_visible(timeout=5000)


def main():
    ts = int(time.time())
    orig = requests.get(f"{API}/workstations/", timeout=10).json()
    orig_count = orig.get("channel_count") or 1
    orig_binds = {}
    for k, v in (orig.get("source_configs") or {}).items():
        if isinstance(v, dict) and v.get("project_id") is not None:
            orig_binds[int(k)] = v["project_id"]
    orig_active = None
    for p in requests.get(f"{API}/projects/", timeout=10).json().get("items", []):
        if p.get("is_active"):
            orig_active = p["id"]
            break
    log(f"原工位数={orig_count} 原绑定={orig_binds} 原激活={orig_active}")

    r = requests.post(f"{API}/projects/", json={
        "name": f"__uat_sm_{ts}", "task_type": "detection",
        "logic_mode": "sequential",
    }, timeout=10)
    assert r.status_code in (200, 201), r.text[:200]
    pid = r.json()["id"]
    assert requests.post(f"{API}/projects/{pid}/activate", timeout=30).status_code == 200

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(record_video_dir=str(VIDEO_DIR),
                                  viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        try:
            configure(page, f"__uat_sm_{ts}")
            cfg = requests.get(f"{API}/scan-collect/config?project_id={pid}",
                               timeout=5).json()
            cfg["settle_on"] = "all_filled"
            cfg["standby_silent"] = False
            cfg.pop("project_id", None)
            requests.put(f"{API}/scan-collect/config?project_id={pid}",
                         json=cfg, timeout=5)

            # ---------- 单工位完整面板 ----------
            assert set_count(1)
            bind_project(0, pid)
            activate(pid)
            time.sleep(1.5)
            page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")
            panel = page.get_by_test_id("scan-slots-panel").first
            expect(panel).to_be_visible(timeout=15000)
            # 完整态没有「明细」按钮
            check("S1 单工位是完整面板(无明细按钮)",
                  panel.get_by_test_id("scan-detail-btn").count() == 0)
            scan(BUS, 0)
            scan(CHIP1, 0)
            expect(panel.get_by_test_id("scan-total")).to_have_text("2/4", timeout=8000)
            btn = panel.get_by_test_id("scan-settle-now-btn")
            expect(btn).to_be_visible(timeout=5000)
            check("S2 单工位标题栏直接出「本件扫完」", btn.is_visible())
            shot(page, "single_settle_btn")
            btn.click()
            page.get_by_role("button", name="立即结算").click()
            # 完整态挂起是横幅不是紧凑徽标
            expect(panel.get_by_test_id("scan-pending-banner")).to_be_visible(timeout=8000)
            st = sc_state(0)
            check("S3 单工位本件扫完→挂起", bool(st.get("pending_ng")),
                  (st.get("pending_ng") or {}).get("reason", ""))
            shot(page, "single_pending")
            scan(CHIP2, 0)
            scan(FIX, 0)
            expect(panel.get_by_test_id("scan-total")).to_have_text("0/4", timeout=8000)
            last = sc_state(0).get("last_settled") or {}
            check("S4 单工位补扫转 OK", last.get("result") == "ok" and last.get("was_pending"),
                  last.get("reason", ""))
            shot(page, "single_ok")

            # ---------- 多工位紧凑态 ----------
            assert set_count(3)
            for _ch in (0, 1, 2):
                bind_project(_ch, pid)
            activate(pid)
            time.sleep(1.5)
            page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")
            cpanel = page.locator('[data-testid^="triple-scan-"], '
                                  '[data-testid^="dual-scan-"]').first
            for _i in range(8):
                if cpanel.count() > 0:
                    break
                time.sleep(1)
                if _i == 3:
                    page.reload(wait_until="domcontentloaded")
            expect(cpanel).to_be_visible(timeout=15000)
            ch = int(cpanel.get_attribute("data-testid").rsplit("-", 1)[-1])
            check("M1 三工位是紧凑态(有明细按钮)",
                  cpanel.get_by_test_id("scan-detail-btn").count() == 1)
            scan(BUS, ch)
            scan(CHIP1, ch)
            expect(cpanel.get_by_test_id("scan-total")).to_have_text("2/4", timeout=8000)
            check("M2 紧凑标题栏没有直接的本件扫完",
                  cpanel.get_by_test_id("scan-settle-now-btn").count() == 0)
            cpanel.get_by_test_id("scan-detail-btn").click()
            pop = page.locator(".scan-slots-popover")
            sbtn = pop.get_by_test_id("scan-settle-now-btn")
            expect(sbtn).to_be_visible(timeout=5000)
            check("M3 明细浮层里有「本件扫完」", sbtn.is_visible())
            shot(page, "multi_settle_in_popover")
            sbtn.click()
            expect(cpanel.get_by_test_id("scan-pending-chip")).to_be_visible(timeout=8000)
            st = sc_state(ch)
            check("M4 多工位本件扫完→挂起", bool(st.get("pending_ng")))
            shot(page, "multi_pending")
            page.keyboard.press("Escape")
            scan(CHIP2, ch)
            scan("H-C035-527-9", ch)
            expect(cpanel.get_by_test_id("scan-total")).to_have_text("0/4", timeout=8000)
            last = sc_state(ch).get("last_settled") or {}
            check("M5 多工位补扫转 OK", last.get("result") == "ok" and last.get("was_pending"),
                  last.get("reason", ""))
            shot(page, "multi_ok")

        finally:
            try:
                set_count(orig_count)
                for _ch, _opid in orig_binds.items():
                    bind_project(_ch, _opid)
                for _ch in range(4):
                    requests.post(f"{API}/scan-collect/clear",
                                  json={"channel_id": _ch}, timeout=5)
                requests.put(f"{API}/scan-collect/config?project_id={pid}",
                             json={"enabled": False, "slots": []}, timeout=5)
                requests.delete(f"{API}/projects/{pid}", timeout=10)
                if orig_active:
                    activate(orig_active)
                log("清理完成, 工位数/绑定/激活项目已恢复")
            except Exception as e:
                log(f"清理异常: {e}")
            ctx.close()
            browser.close()

    log(f"===== UAT: {len(PASSED)} PASS / {len(FAILED)} FAIL =====")
    for f in FAILED:
        log(f"  ❌ {f}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
