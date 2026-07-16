# -*- coding: utf-8 -*-
"""在途报警软件内消除 · 可见浏览器 UAT (v3.39 川南反馈第 2 条)。

现场叙事 (2026-07-15 客户原话: "没发开工信息时触发了报警, 无法通过 post 消除,
能否把报警消除绑定到清零按钮上, 或其他地方能在软件上消除"):
  1. 在途报警挂着、上游又不回推消除命令 → 横幅一直在, 软件内无出口。
  2. v3.39 新增两个出口 (入站配置 → 监控页报警横幅, 默认全关):
     - "横幅手动消除": 横幅上出现按钮, 确认后清全部在途报警
     - "清零联动消除": 监控页点"清零"时顺带清全部在途报警
  3. 预期 UI: 默认横幅无按钮 (老行为); 开关打开后按钮出现、点击确认后
     横幅消失; 清零联动开启后点清零横幅同样消失。

跑法(前置: 后端 8013 RUNTIME_MODE=test + 前端 dev 6003 已起):
  cd tests/uat && UAT_DB=/tmp/uat_alarm_clear/sql_app.db python uat_20260715_alarm_manual_clear.py
"""
import os
import sqlite3
import time

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, launch_browser, filter_console_errors

API = os.environ.get("UAT_API", "http://127.0.0.1:8013")
FRONT = os.environ.get("UAT_FRONT", "http://127.0.0.1:6003")
DB = os.environ.get("UAT_DB", "/tmp/uat_alarm_clear/sql_app.db")
PREFIX = "CNAC-"

run = UatRun("alarm_manual_clear")


# ============================================================
# 数据工具
# ============================================================
def seed_alarms(n=2):
    con = sqlite3.connect(DB, timeout=10)
    try:
        for i in range(n):
            con.execute(
                "INSERT INTO external_active_alarms "
                "(task_no, product_code, step_code, operator, warning_text, "
                " channel_id, event_type, status, raised_at) "
                "VALUES (?, 'P1', 'S1', '张三', '缺件报警', 0, 'ng', 'active', "
                " datetime('now'))",
                (f"{PREFIX}{int(time.time())}-{i}",))
        con.commit()
    finally:
        con.close()


def active_count():
    con = sqlite3.connect(DB, timeout=10)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM external_active_alarms WHERE status='active'"
        ).fetchone()[0]
    finally:
        con.close()


def wipe_alarms():
    con = sqlite3.connect(DB, timeout=10)
    try:
        con.execute("DELETE FROM external_active_alarms")
        con.commit()
    finally:
        con.close()


def reset_banner_cfg(**banner_over):
    cfg = requests.get(f"{API}/api/v1/mes/inbound/config", timeout=10).json()
    banner = cfg.get("alarm_banner") or {}
    banner.update({"allow_manual_clear": False, "clear_on_counter_reset": False,
                   "poll_interval_sec": 1, **banner_over})
    cfg["alarm_banner"] = banner
    r = requests.put(f"{API}/api/v1/mes/inbound/config", json=cfg, timeout=10)
    assert r.status_code == 200, r.text


def switch_by_label(page, label):
    page.locator(".el-form-item", has=page.locator(
        f".el-form-item__label:text-is('{label}')")).first \
        .locator(".el-switch").first.click()
    time.sleep(0.3)


def main():
    r = requests.get(f"{API}/api/v1/projects", timeout=10)
    run.step("后端就绪", r.status_code == 200, f"HTTP {r.status_code}")
    run.step("前端就绪", requests.get(FRONT, timeout=10).status_code == 200)

    wipe_alarms()
    reset_banner_cfg()

    # 建带计数器的项目并激活 (清零按钮前端逻辑要求项目有 counters_config)
    items = requests.get(f"{API}/api/v1/projects", timeout=10).json()
    items = items.get("items") or items
    proj = next((p for p in items if p["name"] == "CNAC-P1"), None)
    if proj is None:
        pr = requests.post(f"{API}/api/v1/projects", json={
            "name": "CNAC-P1", "task_type": "detection", "logic_mode": "sequential",
            "counters_config": [{"id": "total", "name": "总数", "value": 0,
                                 "trigger": "cycle_end"}],
        }, timeout=10)
        run.step("建真项目(带计数器)", pr.status_code in (200, 201), f"HTTP {pr.status_code}")
        proj = pr.json()
    ar = requests.post(f"{API}/api/v1/projects/{proj['id']}/activate", timeout=30)
    run.step("激活项目", ar.status_code == 200, f"HTTP {ar.status_code}")

    # 红前置: 配置关着时端点必须 403
    seed_alarms(1)
    r = requests.post(f"{API}/api/v1/mes/inbound/active-alarms/clear-manual", timeout=10)
    run.step("配置默认关: 手动消除端点 403", r.status_code == 403, f"HTTP {r.status_code}")
    run.step("报警未被动过", active_count() == 1, f"active={active_count()}")

    with sync_playwright() as p:
        browser, ctx, page, console_errors = launch_browser(
            p, headless=False, record_video_dir="/tmp/uat_video")

        # ---------- A. 默认态: 横幅在、无手动消除按钮 (老行为保持) ----------
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4.0)
        banner_visible = page.locator(".ext-alarm-banner").count() > 0
        run.step("监控页出现在途报警横幅", banner_visible)
        btn_absent = page.locator(".ext-alarm-clear-btn").count() == 0
        run.step("默认无「手动消除」按钮 (开关关)", btn_absent)
        run.shot(page, "01_banner_default_no_btn")

        # ---------- B. 配置页开两个开关 (UI→后端落库闭环) ----------
        page.goto(f"{FRONT}/#/mes", wait_until="domcontentloaded")
        time.sleep(3.0)
        page.get_by_text("工单接收", exact=False).first.click()
        time.sleep(2.0)
        switch_by_label(page, "横幅手动消除")
        switch_by_label(page, "清零联动消除")
        run.shot(page, "02_switches_on")
        page.get_by_role("button", name="保存配置").click()
        time.sleep(1.5)
        cfg = requests.get(f"{API}/api/v1/mes/inbound/config", timeout=10).json()
        b = cfg.get("alarm_banner") or {}
        run.step("开关经 UI 保存后真实落库",
                 b.get("allow_manual_clear") is True and
                 b.get("clear_on_counter_reset") is True, str({
                     "allow_manual_clear": b.get("allow_manual_clear"),
                     "clear_on_counter_reset": b.get("clear_on_counter_reset")}))

        # ---------- C. 横幅手动消除: 按钮出现 → 点击确认 → 横幅消失 ----------
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4.0)
        # 配置 15s 热刷新太慢, 等按钮最多 20s (横幅自身 1s 轮询)
        btn = page.locator(".ext-alarm-clear-btn")
        deadline = time.time() + 20
        while time.time() < deadline and btn.count() == 0:
            time.sleep(1.0)
        run.step("开关开启后横幅出现「手动消除」按钮", btn.count() > 0)
        run.shot(page, "03_banner_with_btn")
        btn.first.click()
        time.sleep(0.8)
        run.shot(page, "04_confirm_dialog")
        page.get_by_role("button", name="消除", exact=True).click()
        time.sleep(2.0)
        run.step("点击确认后在途报警落库清零", active_count() == 0,
                 f"active={active_count()}")
        deadline = time.time() + 8
        while time.time() < deadline and page.locator(".ext-alarm-banner").count() > 0:
            time.sleep(0.5)
        run.step("横幅消失", page.locator(".ext-alarm-banner").count() == 0)
        run.shot(page, "05_banner_gone")

        # ---------- D. 清零联动: 再挂报警 → 点清零 → 报警同步消除 ----------
        seed_alarms(2)
        deadline = time.time() + 8
        while time.time() < deadline and page.locator(".ext-alarm-banner").count() == 0:
            time.sleep(0.5)
        run.step("新报警横幅重新出现", page.locator(".ext-alarm-banner").count() > 0,
                 f"active={active_count()}")
        run.shot(page, "06_banner_back")
        page.get_by_role("button", name="清零").first.click()
        time.sleep(3.0)
        run.step("点「清零」后在途报警联动清零", active_count() == 0,
                 f"active={active_count()}")
        deadline = time.time() + 8
        while time.time() < deadline and page.locator(".ext-alarm-banner").count() > 0:
            time.sleep(0.5)
        run.step("横幅随之消失", page.locator(".ext-alarm-banner").count() == 0)
        run.shot(page, "07_after_reset_counters")

        errs = filter_console_errors(console_errors)
        run.step("浏览器 console 无异常报错", len(errs) == 0,
                 "; ".join(str(e) for e in errs[:3]))
        ctx.close()
        browser.close()

    reset_banner_cfg()
    wipe_alarms()
    raise SystemExit(run.finish())


if __name__ == "__main__":
    main()
