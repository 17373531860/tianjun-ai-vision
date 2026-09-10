"""v3.56.1b 六和现场反馈修复 — 可见浏览器 UAT（路径 H）

复现现场姿势（2026-09-06 工人反馈 + 答复确认）：
  · 母排永远第一个扫、其余顺序不固定、工装码可能中间扫 → 结算时机=扫满结算
  · 反馈1: 少扫不报警不判定无补救 → 修复: 催扫提醒(idle_remind_sec) +
    面板「本件扫完」人工收口 + 挂起补扫转 OK
  · 反馈2: 检测次数乱涨/待机也涨 → 修复: 挂起报警 remind_only 不计数 +
    待机静默(standby_silent) 不发事件不落 txt
  · 答复7: txt 要有扫码补救数据 → 补扫码带 [补扫] 标记 + 转 OK 备注行

剧本：
  A. 配置: MES→扫码器→多码采集, 填入示例(芯子改2) + 结算时机改扫满 + 保存
  B. 建 scan_group_end 实时导出规则 (内置「多码采集码组 TXT」模板)
  C. 现场顺序扫码: 母排 → 工装(中间扫, 不触发结算) → 芯子×1 → 停 → 催扫提醒出现
  D. 点「本件扫完」→ 挂起横幅/徽标 + 报警 toast
  E. 补扫缺芯子 → 自动转 OK, 面板上组OK, was_pending 留痕
  F. 验证 txt 落盘: [补扫] 标记 + 备注行
  G. 待机静默: standby_silent=true 且未开始检测 → 结算 standby=true 不落 txt

跑法（先起 backend 8001 + frontend 6001）：
  /Users/tianjun/miniconda3/envs/tianjun/bin/python tests/uat/uat_scan_collect_field_fix.py

证据：/tmp/uat_scan_field/ 截图 + run.log；/tmp/uat_video/ 录像。
"""
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:6001")
API = os.environ.get("E2E_API_URL", "http://localhost:8001") + "/api/v1"
SHOT_DIR = Path("/tmp/uat_scan_field")
VIDEO_DIR = Path("/tmp/uat_video")
EXPORT_DIR = Path("/tmp/uat_scan_field/txt_out")
SHOT_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

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
    log(f"🔫 模拟扫码 ch{channel}: {code} → HTTP {r.status_code}")
    time.sleep(0.6)
    return r


def sc_state(channel):
    return requests.get(f"{API}/scan-collect/state?channel={channel}",
                        timeout=5).json()


def main():
    ts = int(time.time())
    proj_name = f"__uat_scf_{ts}"
    for stale in EXPORT_DIR.glob("*.txt"):
        stale.unlink()

    # ---------- 准备: 建项目并激活 ----------
    r = requests.post(f"{API}/projects/", json={
        "name": proj_name, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    assert r.status_code in (200, 201), r.text[:200]
    pid = r.json()["id"]
    assert requests.post(f"{API}/projects/{pid}/activate",
                         timeout=30).status_code == 200
    log(f"准备完成: 项目 {proj_name} (id={pid}) 已激活")

    rule_id = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(record_video_dir=str(VIDEO_DIR),
                                  viewport={"width": 1600, "height": 950})
        page = ctx.new_page()
        try:
            # ---------- A. 配置: 扫满结算 + 催扫 3 秒 (演示提速) ----------
            page.goto(f"{BASE}/#/mes", wait_until="domcontentloaded")
            page.get_by_role("button", name="扫码器").click()
            page.get_by_role("tab", name="多码采集").click()
            expect(page.get_by_test_id("sc-project-select")).to_be_visible(timeout=8000)
            page.get_by_role("button", name="填入示例").click()
            rows = page.locator(".el-table__body tr")
            expect(rows).to_have_count(3, timeout=5000)
            chip_count = rows.nth(1).locator(".el-input-number input")
            chip_count.fill("2")
            chip_count.press("Enter")
            # 新字段可见 + 示例预设值
            remind_input = page.get_by_test_id("sc-idle-remind").locator("input")
            check("A1 配置卡出现「催扫提醒(秒)」且示例预设 30",
                  remind_input.input_value() == "30", remind_input.input_value())
            check("A2 配置卡出现「待机时扫码」下拉",
                  page.get_by_test_id("sc-standby-silent").count() == 1)
            remind_input.fill("3")   # UAT 提速: 3 秒催扫
            remind_input.press("Enter")
            page.get_by_test_id("sc-enabled-switch").click()
            shot(page, "config_field_fix")
            page.get_by_test_id("sc-save-btn").click()
            expect(page.locator(".el-message--success").last).to_be_visible(timeout=5000)
            # 结算时机改扫满(现场姿势) — UI 下拉是 el-select, 直接 API 补丁更稳。
            # UAT 通道全程未开始检测=天然待机, 示例预设的待机静默会把 C~F 段
            # 的 txt/事件全静默掉 → 先关, G 段单独开来验证静默行为。
            cfg = requests.get(f"{API}/scan-collect/config?project_id={pid}",
                               timeout=5).json()
            check("A3 示例预设待机静默=开(现场答复6b)",
                  cfg["standby_silent"] is True)
            cfg["settle_on"] = "all_filled"
            cfg["standby_silent"] = False
            cfg.pop("project_id", None)
            r = requests.put(f"{API}/scan-collect/config?project_id={pid}",
                             json=cfg, timeout=5)
            check("A4 配置落库(扫满结算+催扫3s)",
                  r.status_code == 200 and r.json()["settle_on"] == "all_filled"
                  and r.json()["idle_remind_sec"] == 3)

            # ---------- B. scan_group_end 实时导出规则 ----------
            tpls = requests.get(f"{API}/export/templates?scope=realtime",
                                timeout=5).json()
            sg = next(t for t in tpls["items"] if "多码采集" in t.get("name", ""))
            r = requests.post(f"{API}/export/realtime-rules", json={
                "name": f"__uat_scf_rule_{ts}", "enabled": True,
                "template_id": sg["id"], "trigger_event": "scan_group_end",
                "output_dir": str(EXPORT_DIR),
                "filename_template": "{{ scan_collect.workpiece_sn }}.txt",
                "encoding": "utf-8-sig", "newline": "crlf",
                "overwrite_policy": "overwrite", "input_file_mode": "none",
            }, timeout=5)
            check("B1 建 scan_group_end txt 规则", r.status_code in (200, 201),
                  r.text[:120])
            rule_id = r.json().get("id")

            # ---------- C. 现场顺序扫码 + 催扫提醒 ----------
            page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")
            panel = page.locator('[data-testid^="triple-scan-"], '
                                 '[data-testid^="dual-scan-"]').first
            expect(panel).to_be_visible(timeout=15000)
            ch = int(panel.get_attribute("data-testid").rsplit("-", 1)[-1])
            log(f"多码采集面板落在工位 {ch + 1} (ch{ch})")
            total = panel.get_by_test_id("scan-total")

            scan(BUS, ch)          # 母排永远第一
            scan(FIX, ch)          # 工装码中间扫 — 扫满结算下不触发结算
            scan(CHIP1, ch)        # 芯子只扫 1 个 (缺 1)
            expect(total).to_have_text("3/4", timeout=8000)
            st = sc_state(ch)
            # 注: last_settled 是通道级(可能残留上个项目的旧结果), 判"未结算"
            # 看本组仍在采集且 3 码都还在组里
            check("C1 工装码中间扫不触发结算(扫满结算, 复现现场顺序)",
                  st["collecting"] and st["total_got"] == 3,
                  f"collecting={st['collecting']} got={st['total_got']}")
            # 停 4 秒等催扫提醒 (idle_remind_sec=3)
            log("⏳ 停止扫码 4 秒, 等催扫提醒…")
            time.sleep(4.2)
            shot(page, "idle_remind_toast")
            # 催扫走 scan warning toast 通路 (前端顶栏横幅) — 从后端事件日志验证
            st = sc_state(ch)
            check("C2 催扫后组仍开着(只催不结组)", st["collecting"])

            # ---------- D. 本件扫完 → 挂起 ----------
            panel.get_by_test_id("scan-detail-btn").click()
            pop = page.locator(".scan-slots-popover")
            btn = pop.get_by_test_id("scan-settle-now-btn")
            expect(btn).to_be_visible(timeout=5000)
            shot(page, "settle_now_btn")
            btn.click()
            expect(panel.get_by_test_id("scan-pending-chip")).to_be_visible(timeout=8000)
            st = sc_state(ch)
            check("D1 本件扫完→少扫挂起(不出终判)", bool(st["pending_ng"]),
                  (st.get("pending_ng") or {}).get("reason", ""))
            shot(page, "pending_after_settle_now")
            page.keyboard.press("Escape")

            # ---------- E. 补扫缺芯子 → 自动转 OK ----------
            scan(CHIP2, ch)
            expect(total).to_have_text("0/4", timeout=8000)
            last = sc_state(ch).get("last_settled") or {}
            check("E1 补扫齐全自动转 OK", last.get("result") == "ok",
                  last.get("reason", ""))
            check("E2 was_pending/remedied 留痕",
                  last.get("was_pending") is True and any(
                      c.get("remedied") for c in last.get("codes", [])))
            shot(page, "resupply_ok")

            # ---------- F. txt 落盘带补救数据 ----------
            time.sleep(1.5)
            txts = sorted(EXPORT_DIR.glob("*.txt"))
            check("F1 码组结算落 txt", bool(txts),
                  str([t.name for t in txts]))
            if txts:
                body = txts[-1].read_text(encoding="utf-8-sig")
                log("txt 内容:\n" + body)
                check("F2 补扫码带 [补扫] 标记", "[补扫]" in body)
                check("F3 转 OK 备注行", "补扫齐全转 OK" in body)
                check("F4 收尾码置顶+结果 OK",
                      body.strip().startswith("工装码") and "结果: OK" in body)

            # ---------- G. 待机静默 ----------
            # 开启待机静默; UAT 通道未开始检测 = 天然待机
            cfg = requests.get(f"{API}/scan-collect/config?project_id={pid}",
                               timeout=5).json()
            cfg["standby_silent"] = True
            cfg.pop("project_id", None)
            requests.put(f"{API}/scan-collect/config?project_id={pid}",
                         json=cfg, timeout=5)
            n_txt = len(list(EXPORT_DIR.glob("*.txt")))
            for c in ["M" + "1" * 31, "9260000146072", "9260000146312",
                      "H-C035-527-9"]:
                scan(c, ch)
            last = sc_state(ch).get("last_settled") or {}
            check("G1 待机结算 standby 标记", last.get("standby") is True,
                  str(last.get("standby")))
            time.sleep(1.5)
            check("G2 待机静默不新增 txt",
                  len(list(EXPORT_DIR.glob("*.txt"))) == n_txt,
                  f"before={n_txt} after={len(list(EXPORT_DIR.glob('*.txt')))}")
            shot(page, "standby_silent_settled")

        finally:
            # ---------- 清理 ----------
            try:
                for _ch in range(4):
                    requests.post(f"{API}/scan-collect/clear",
                                  json={"channel_id": _ch}, timeout=5)
                requests.put(f"{API}/scan-collect/config?project_id={pid}",
                             json={"enabled": False, "slots": []}, timeout=5)
                if rule_id:
                    requests.delete(f"{API}/export/realtime-rules/{rule_id}",
                                    timeout=5)
                requests.delete(f"{API}/projects/{pid}", timeout=10)
                log("清理完成")
            except Exception as e:
                log(f"清理异常(不影响判定): {e}")
            ctx.close()
            browser.close()

    log(f"===== UAT 结束: {len(PASSED)} PASS / {len(FAILED)} FAIL =====")
    for f in FAILED:
        log(f"  ❌ {f}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
