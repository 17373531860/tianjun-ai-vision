"""可见浏览器 UAT — 包装强制结案 + 待机不结算 + 滑块预设 UI 收口 (v3.23, 2026-06-22).

金标准验收 (run-tests skill 路径 H): headless=False + 录像 + 截图 + API 后端契约 + DB 落库三证据.

前置 (跑前先起好, 见脚本顶部常量):
  - 后端: RUNTIME_MODE=test TIANJUN_DATA_DIR=/tmp/uat_pkg_data uvicorn ... --port 8011
  - 前端: VITE_API_BASE_URL=http://localhost:8011/api/v1 npx vite --port 6011
  - 测试库已建 admin/op1 两账号 (admin=超管, op1=操作员仅监控权)

验收点:
  Phase A (API 契约 + DB 落库):
    A1 扫单开工 → 工单进行中
    A2 强制结案理由为空 → 400
    A3 管理员填理由强制结案 → 200, last_run 已完成 + forced_by 非空
    A4 DB: forced_reason/forced_by 落库 + status=completed + 协调器无进行中
    A5 无进行中工单再强制结案 → 409
    A6 操作员账号强制结案 → 403 (无权限)
  Phase B (可见浏览器):
    B1 管理员 UI 登录 → 监控页见包装卡
    B2 有进行中工单 → 「强制结案」红按钮可见
    B3 点按钮 → 必填理由对话框 → 填理由 → 确认 → 成功 + 工单收尾 (按钮消失)
    B4 操作员 UI 登录 → 监控页有进行中工单时「强制结案」按钮不可见 (权限隐藏)
    B5 设置→包装箱结算→新建配置: 默认托盘口径显①②; 套「上银」预设后①②消失、⑦滑块口径出现
"""
from __future__ import annotations

import io
import os
import sqlite3
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8011/api/v1"
FRONT = "http://127.0.0.1:6011"
DB = "/tmp/uat_pkg_data/sql_app.db"
SHOTS = "/tmp/uat_force_settle_shots"
VIDEO = "/tmp/uat_force_settle_video"
RUNLOG = "/tmp/uat_force_settle_run.log"

ADMIN = ("admin", "admin12345")
OPER = ("op1", "op12345")

os.makedirs(SHOTS, exist_ok=True)
os.makedirs(VIDEO, exist_ok=True)

steps = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps.append(rec)
    print(f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}")
    return ok


def login(creds):
    r = requests.post(f"{API}/auth/login",
                      json={"username": creds[0], "password": creds[1]}, timeout=10)
    r.raise_for_status()
    return r.json()["token"]


def H(token):
    return {"Authorization": f"Bearer {token}"}


def create_config(h):
    # 清旧 __uat 配置
    for c in (requests.get(f"{API}/packaging-flows", headers=h).json() or {}).get("items", []):
        if str(c.get("name", "")).startswith("__uat"):
            requests.put(f"{API}/packaging-flows/{c['id']}", headers=h, json={"enabled": False})
            requests.delete(f"{API}/packaging-flows/{c['id']}", headers=h)
    r = requests.post(f"{API}/packaging-flows", headers=h, json={
        "name": "__uat_强制结案", "enabled": True, "channel_id": 0,
        "count_unit": "sliders", "items_per_box_source": "config",
        "items_per_box_fixed": 96, "slider_total_field": "dispatch_qty",
        "on_mes_fail": "offline", "on_forced_stop": "settle",
        "forced_settle_on_standby": False,
    })
    r.raise_for_status()
    return r.json()["id"]


def open_order(h, code):
    r = requests.post(f"{API}/packaging-flows/scan", headers=h,
                      json={"code": code, "channel_id": 0}, timeout=10)
    r.raise_for_status()
    return r.json()


def get_state(h, cid):
    r = requests.get(f"{API}/packaging-flows/{cid}/state", headers=h, timeout=10)
    return r.json().get("state") if r.status_code == 200 else None


def db_last_run(cid):
    c = sqlite3.connect(DB)
    try:
        row = c.execute(
            "select order_no,status,final_result,forced_reason,forced_by "
            "from packaging_flow_runs where flow_config_id=? order by id desc limit 1",
            (cid,)).fetchone()
    finally:
        c.close()
    if not row:
        return None
    return dict(zip(["order_no", "status", "final_result", "forced_reason", "forced_by"], row))


# ════════════════════════ Phase A: API 契约 + DB 落库 ════════════════════════
def phase_a():
    print("\n──────── Phase A: API 契约 + DB 落库 ────────")
    at = login(ADMIN)
    ah = H(at)
    cid = create_config(ah)
    step("A0 建滑块包装配置", True, f"cfg_id={cid}")

    open_order(ah, "JOBUATA01")
    st = get_state(ah, cid)
    step("A1 扫单开工→工单进行中", st and st.get("status") in ("order_loaded", "running"),
         f"status={st and st.get('status')}")

    r = requests.post(f"{API}/packaging-flows/{cid}/force-settle", headers=ah,
                      json={"reason": "   "})
    step("A2 理由为空被拒绝", r.status_code == 400, f"code={r.status_code}")

    reason = "产线临时停线收工_UAT"
    r = requests.post(f"{API}/packaging-flows/{cid}/force-settle", headers=ah,
                      json={"reason": reason})
    j = r.json() if r.status_code == 200 else {}
    lr = j.get("last_run") or {}
    step("A3 管理员填理由强制结案→200", r.status_code == 200 and j.get("success") is True,
         f"code={r.status_code} forced_by={j.get('forced_by')}")
    step("A3b last_run 已完成 + forced_by", lr.get("status") == "completed" and bool(j.get("forced_by")),
         f"last_run.status={lr.get('status')}")

    row = db_last_run(cid)
    step("A4 DB 落库 forced_reason/forced_by/status", bool(row)
         and row["forced_reason"] == reason and bool(row["forced_by"])
         and row["status"] == "completed",
         f"row={row}")
    step("A4b 协调器无进行中工单", get_state(ah, cid) is None)

    r = requests.post(f"{API}/packaging-flows/{cid}/force-settle", headers=ah,
                      json={"reason": "再来一次"})
    step("A5 无进行中工单再结案→409", r.status_code == 409, f"code={r.status_code}")

    # A6 操作员无权限
    open_order(ah, "JOBUATA02")  # 先开一单, 避免 409 掩盖 403
    ot = login(OPER)
    r = requests.post(f"{API}/packaging-flows/{cid}/force-settle", headers=H(ot),
                      json={"reason": "操作员尝试"})
    step("A6 操作员强制结案→403", r.status_code == 403, f"code={r.status_code}")
    # 收尾这单 (admin)
    requests.post(f"{API}/packaging-flows/{cid}/force-settle", headers=ah,
                  json={"reason": "A6 收尾"})
    return cid, ah


# ════════════════════════ 浏览器辅助 ════════════════════════
def ui_login(page, creds):
    # 关键: 清存储后必须整页 reload 让内存里的 Pinia store 归零, 否则上个账号的登录态
    # 还在内存里, 进登录页会被守卫直接重定向走 → 抓不到表单.
    try:
        page.evaluate("localStorage.clear(); sessionStorage.clear();")
    except Exception:
        page.goto(f"{FRONT}/#/login")
        page.wait_for_load_state("networkidle")
        page.evaluate("localStorage.clear(); sessionStorage.clear();")
    page.reload()  # store 从空 token 重新 init → 匿名态
    page.wait_for_load_state("networkidle")
    page.goto(f"{FRONT}/#/login")  # 此时内存已匿名, 登录页正常显示表单
    page.wait_for_load_state("networkidle")
    page.wait_for_selector('input[placeholder="请输入用户名"]', timeout=10000)
    time.sleep(0.8)
    page.locator('input[placeholder="请输入用户名"]').fill(creds[0])
    page.locator('input[placeholder="请输入密码"]').fill(creds[1])
    page.get_by_role("button", name="登录", exact=True).click()
    page.wait_for_load_state("networkidle")
    time.sleep(1.5)


def goto_monitor(page):
    page.goto(f"{FRONT}/#/monitor")
    page.wait_for_load_state("networkidle")
    time.sleep(2.5)  # 给包装卡轮询 (1.5s) 两个周期


# ════════════════════════ Phase B: 可见浏览器 ════════════════════════
def phase_b(cid, ah):
    print("\n──────── Phase B: 可见浏览器 ────────")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=280,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 1000},
            ignore_https_errors=True,
        )
        page = ctx.new_page()
        try:
            # B1 管理员登录 → 监控页见包装卡
            page.goto(f"{FRONT}/#/login")
            page.wait_for_load_state("networkidle")
            ui_login(page, ADMIN)
            goto_monitor(page)
            page.screenshot(path=f"{SHOTS}/B1_admin_monitor.png", full_page=True)
            body = page.evaluate("document.body.innerText")
            step("B1 管理员监控页见包装卡", "包装箱结算" in body, "卡标题出现")

            # B2 开一单 → 强制结案按钮可见
            open_order(ah, "JOBUATB01")
            time.sleep(3.0)  # 等轮询拉到进行中工单
            btn = page.get_by_role("button", name="强制结案")
            vis = btn.count() > 0 and btn.first.is_visible()
            page.screenshot(path=f"{SHOTS}/B2_force_btn_visible.png", full_page=True)
            step("B2 有进行中工单→强制结案按钮可见", vis, f"count={btn.count()}")

            # B3 点按钮 → 必填理由对话框 → 填理由 → 确认 → 工单收尾
            if vis:
                btn.first.click()
                page.wait_for_selector(".el-message-box", timeout=5000)
                page.screenshot(path=f"{SHOTS}/B3a_reason_dialog.png", full_page=True)
                step("B3a 弹出必填理由对话框", page.locator(".el-message-box").is_visible())
                page.locator(".el-message-box__input textarea").fill("UAT 浏览器强制结案理由")
                page.locator(".el-message-box__btns button", has_text="确认强制结案").click()
                time.sleep(3.0)  # 等接口 + 轮询刷新
                page.screenshot(path=f"{SHOTS}/B3b_after_settle.png", full_page=True)
                gone = page.get_by_role("button", name="强制结案").count() == 0 \
                    or not page.get_by_role("button", name="强制结案").first.is_visible()
                row = db_last_run(cid)
                step("B3b 确认后工单收尾(按钮消失)+落库",
                     gone and row and row["status"] == "completed"
                     and row["forced_reason"] == "UAT 浏览器强制结案理由",
                     f"row={row}")

            # B4 操作员登录 → 有进行中工单时按钮不可见 (权限隐藏)
            open_order(ah, "JOBUATB02")  # admin 再开一单
            ui_login(page, OPER)
            goto_monitor(page)
            time.sleep(2.0)
            obtn = page.get_by_role("button", name="强制结案")
            hidden = obtn.count() == 0 or not obtn.first.is_visible()
            page.screenshot(path=f"{SHOTS}/B4_operator_no_btn.png", full_page=True)
            step("B4 操作员无强制结案按钮(权限隐藏)", hidden, f"count={obtn.count()}")
            # admin 收尾这单
            requests.post(f"{API}/packaging-flows/{cid}/force-settle", headers=ah,
                          json={"reason": "B4 收尾"})

            # B5 管理员 → 设置 → 包装箱结算 → 新建配置 → 套上银预设, 验 UI 收口
            ui_login(page, ADMIN)
            page.goto(f"{FRONT}/#/settings")
            page.wait_for_load_state("networkidle")
            time.sleep(1.5)
            page.get_by_role("tab", name="包装箱结算").click()
            time.sleep(1.0)
            page.get_by_role("button", name="新建配置").click()
            page.wait_for_selector(".el-dialog", timeout=5000)
            time.sleep(0.8)
            page.screenshot(path=f"{SHOTS}/B5a_dialog_default_trays.png", full_page=True)
            g1_before = page.get_by_text("① 工单与箱数", exact=False).count()
            g7_before = page.get_by_text("⑦ 滑块口径设置", exact=False).count()
            step("B5a 默认托盘口径显①工单与箱数, 不显⑦",
                 g1_before > 0 and g7_before == 0, f"①={g1_before} ⑦={g7_before}")

            page.get_by_role("button", name="一键套用「上银包装线」预设").click()
            time.sleep(1.5)
            page.screenshot(path=f"{SHOTS}/B5b_after_hiwin_preset.png", full_page=True)
            g1_after = page.get_by_text("① 工单与箱数", exact=False).count()
            g2_after = page.get_by_text("② 数量规格", exact=False).count()
            g7_after = page.get_by_text("⑦ 滑块口径设置", exact=False).count()
            step("B5b 套上银预设后①②消失、⑦滑块口径出现",
                 g1_after == 0 and g2_after == 0 and g7_after > 0,
                 f"①={g1_after} ②={g2_after} ⑦={g7_after}")
        finally:
            ctx.close()  # flush 视频
            browser.close()


def report():
    ok = sum(1 for s in steps if s["ok"])
    bad = [s for s in steps if not s["ok"]]
    print("\n──────── 汇总 ────────")
    for s in steps:
        print(f"[{'OK' if s['ok'] else '!!'}] {s['idx']:02d}. {s['label']}  {s['detail']}")
    print(f"\npassed: {ok}/{len(steps)}  failed: {len(bad)}")
    with open(RUNLOG, "w", encoding="utf-8") as f:
        for s in steps:
            f.write(f"[{'OK' if s['ok'] else 'FAIL'}] {s['idx']:02d}. {s['label']} :: {s['detail']}\n")
        f.write(f"\npassed: {ok}/{len(steps)}\nfailed: {len(bad)}\n")
    print(f"视频: {VIDEO}/  截图: {SHOTS}/  日志: {RUNLOG}")
    return len(bad) == 0


if __name__ == "__main__":
    cid, ah = phase_a()
    phase_b(cid, ah)
    all_ok = report()
    raise SystemExit(0 if all_ok else 1)
