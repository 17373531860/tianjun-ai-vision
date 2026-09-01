"""v3.56 周期多码采集 — 可见浏览器 UAT（路径 H）

现场叙事（一号工位焊接组装）：
  1. 工程师在 MES管理→扫码器→多码采集 里为当前项目配 4 类码
     （母排1 + 盖板1 + 芯子N + 工件码收尾）并启用。
  2. 检测主页对应工位出现「多码采集」进度面板（三工位=紧凑态）。
  3. 操作员逐个扫码，面板实时 +1；扫错点「明细→✕」删码重扫；
     扫工件码（字母+数字）触发结算，码齐判 OK、少扫判 NG。
  4. kiosk 副屏只读可看进度/上组结果，无任何纠错按钮。

跑法（先起 backend 8001 + frontend 6001）：
  /Users/tianjun/miniconda3/envs/tianjun/bin/python tests/uat/uat_scan_collect_ui.py

证据：/tmp/uat_scan_collect/ 截图 + run.log；/tmp/uat_video/ 录像。
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:6001")
API = os.environ.get("E2E_API_URL", "http://localhost:8001") + "/api/v1"
SHOT_DIR = Path("/tmp/uat_scan_collect")
VIDEO_DIR = Path("/tmp/uat_video")
SHOT_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

PASSED, FAILED = [], []
LOG = open(SHOT_DIR / "run.log", "w", encoding="utf-8")


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
    time.sleep(0.6)  # 等 150ms 轮询把状态带到前端
    return r


def sc_state(channel):
    return requests.get(f"{API}/scan-collect/state?channel={channel}", timeout=5).json()


def main():
    ts = int(time.time())
    proj_name = f"__uat_sc_{ts}"

    # ---------- 准备：建项目并激活 ----------
    r = requests.post(f"{API}/projects/", json={
        "name": proj_name, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    assert r.status_code in (200, 201), f"建项目失败: {r.status_code} {r.text[:200]}"
    pid = r.json()["id"]
    r = requests.post(f"{API}/projects/{pid}/activate", timeout=30)
    assert r.status_code == 200, f"激活失败: {r.status_code}"
    log(f"准备完成: 项目 {proj_name} (id={pid}) 已激活")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(record_video_dir=str(VIDEO_DIR),
                                  viewport={"width": 1600, "height": 950})
        page = ctx.new_page()

        try:
            # ---------- A. MES→扫码器→多码采集 配置 (T4) ----------
            page.goto(f"{BASE}/#/mes", wait_until="domcontentloaded")
            page.get_by_role("button", name="扫码器").click()
            page.get_by_role("tab", name="多码采集").click()
            expect(page.get_by_test_id("sc-project-select")).to_be_visible(timeout=8000)
            shot(page, "mes_scan_collect_tab")
            check("A1 MES扫码器页出现「多码采集」tab", True)

            # 默认应选中当前激活项目
            sel_text = page.get_by_test_id("sc-project-select").inner_text()
            check("A2 项目选择器默认当前激活项目", proj_name in sel_text, sel_text)

            page.get_by_role("button", name="填入示例").click()
            rows = page.locator(".el-table__body tr")
            expect(rows).to_have_count(3, timeout=5000)
            # 芯子应扫数量 6 → 2 (UAT 提速)。
            # 注: 名称列是 el-input, "芯子码"在 value 里不在文本节点, has_text 匹配不到;
            # 示例填入顺序固定 busbar/chip/fixture → 芯子=第 2 行。
            chip_count = rows.nth(1).locator(".el-input-number input")
            chip_count.fill("2")
            chip_count.press("Enter")
            page.get_by_test_id("sc-enabled-switch").click()
            shot(page, "config_filled")
            page.get_by_test_id("sc-save-btn").click()
            expect(page.locator(".el-message--success").last).to_be_visible(timeout=5000)
            shot(page, "config_saved_toast")
            check("A3 配置保存成功 toast", True)

            # T5: 落库双向验证
            cfg = requests.get(f"{API}/scan-collect/config?project_id={pid}", timeout=5).json()
            chip = next((s for s in cfg["slots"] if s["key"] == "chip"), None)
            check("A4 GET config 落库验证", cfg["enabled"] and len(cfg["slots"]) == 3
                  and chip and chip["count"] == 2,
                  json.dumps({"enabled": cfg["enabled"], "slots": len(cfg["slots"]),
                              "chip_count": chip and chip["count"]}, ensure_ascii=False))

            # ---------- B. 检测主页三工位紧凑面板 ----------
            # 注: 激活项目被哪个工位收养取决于现有绑定 (v3.51 adopt_unbound),
            # 面板出现在绑定该项目的工位上 — 动态定位, 不写死 ch0。
            page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")
            panel = page.locator('[data-testid^="triple-scan-"]').first
            expect(panel).to_be_visible(timeout=15000)
            uat_ch = int(panel.get_attribute("data-testid").rsplit("-", 1)[-1])
            log(f"多码采集面板落在工位 {uat_ch + 1} (ch{uat_ch})")
            total = panel.get_by_test_id("scan-total")
            expect(total).to_have_text("0/4", timeout=8000)
            shot(page, "monitor_panel_0of5")
            check("B1 三工位紧凑面板出现且 0/4", True)

            # ---------- C. 扫码链路: 正则分类入槽 (母排^M / 芯子13位数字 / 工装^H-C) ----------
            scan("M010200519A100005036272608310061", uat_ch)   # → 母排
            scan("9260000144908", uat_ch)   # → 芯子1
            scan("9260000145631", uat_ch)   # → 芯子2
            expect(total).to_have_text("3/4", timeout=8000)
            shot(page, "scanned_3of4")
            st = sc_state(uat_ch)
            by_key = {s["key"]: s for s in st["slots"]}
            check("C1 三码按正则正确入槽",
                  by_key["busbar"]["got"] == 1 and by_key["chip"]["got"] == 2,
                  json.dumps({k: v["got"] for k, v in by_key.items()}, ensure_ascii=False))

            # 芯子槽已满再扫芯子 → 槽位级 on_overflow=ng_alarm (示例预置): 拒收不入槽
            scan("9260000146072", uat_ch)
            st = sc_state(uat_ch)
            check("C2 芯子多扫按槽位策略拒收 (确认单 4.4)",
                  {s["key"]: s["got"] for s in st["slots"]}["chip"] == 2)

            # ---------- D. 纠错: 明细弹层删单码 → 重扫 ----------
            panel.get_by_test_id("scan-detail-btn").click()
            pop = page.locator(".el-popover:visible, .el-popper:visible").last
            expect(pop.get_by_test_id("scan-code-chip").first).to_be_visible(timeout=5000)
            shot(page, "detail_popover")
            # 删掉扫错的芯子2
            chip2 = pop.get_by_test_id("scan-code-chip").filter(has_text="9260000145631")
            chip2.get_by_test_id("scan-code-remove").click()
            expect(total).to_have_text("2/4", timeout=8000)
            shot(page, "removed_to_2of4")
            check("D1 明细弹层删单码生效 3/4→2/4", True)
            st = sc_state(uat_ch)
            check("D2 删码后后端状态同步", 
                  {s["key"]: s["got"] for s in st["slots"]}["chip"] == 1)
            page.keyboard.press("Escape")
            scan("9260000146312", uat_ch)   # 重扫芯子2

            # ---------- E. 收尾码结算 OK ----------
            gid_before = sc_state(uat_ch)["group_id"]
            scan("H-C035-527-5", uat_ch)  # 工装码 ^H-C → 收尾结算
            expect(total).to_have_text("0/4", timeout=8000)  # 结算后新组归零
            shot(page, "settled_reset_0of5")
            st = sc_state(uat_ch)
            last = st.get("last_settled") or {}
            check("E1 码齐+收尾码 → 结算 OK",
                  last.get("result") == "ok" and last.get("workpiece_sn") == "H-C035-527-5",
                  json.dumps(last, ensure_ascii=False, default=str)[:200])
            # T5: 逐码记录落库
            recs = requests.get(f"{API}/scan-collect/records?group_id={gid_before}",
                                timeout=5).json()
            check("E2 records 落库: 4 条且 group_result=ok",
                  len(recs) == 4 and all(r["group_result"] == "ok" for r in recs),
                  f"{len(recs)} 条")
            check("E3 工件绑定: records 带 workpiece_id",
                  all(r["workpiece_id"] for r in recs))

            # ---------- F. 少扫 NG ----------
            # 示例预设 ng_pending=true (确认单 5.3 补扫): 少扫收尾 → 挂起不结算
            scan("M010200519A100005036272608310060", uat_ch)
            scan("H-C035-527-1", uat_ch)  # 直接扫工装收尾 → 缺芯子 → NG 挂起
            time.sleep(0.8)
            st = sc_state(uat_ch)
            check("F1 少扫收尾 → NG 挂起 (不立即结算)",
                  bool(st.get("pending_ng")),
                  json.dumps(st.get("pending_ng"), ensure_ascii=False)[:160])
            expect(panel.get_by_test_id("scan-pending-chip")).to_be_visible(timeout=8000)
            shot(page, "ng_pending_chip")
            check("F2 面板出现「NG 挂起」徽标", True)
            # 人工按 NG 放行 → ng_missing 结算
            r = requests.post(f"{API}/scan-collect/resolve-ng",
                              json={"channel_id": uat_ch}, timeout=5)
            time.sleep(0.8)
            last = sc_state(uat_ch).get("last_settled") or {}
            check("F3 按 NG 放行 → ng_missing 结算 + 缺项明细",
                  r.ok and (last.get("result") or "").startswith("ng")
                  and last.get("missing"),
                  json.dumps({"result": last.get("result"),
                              "missing": last.get("missing")}, ensure_ascii=False)[:200])
            shot(page, "ng_after_missing")

            # ---------- G. 清空重扫 ----------
            scan("M010200519A100005036272608310099", uat_ch)
            scan("9260000149999", uat_ch)
            expect(total).to_have_text("2/4", timeout=8000)
            panel.get_by_test_id("scan-detail-btn").click()
            pop = page.locator(".el-popover:visible, .el-popper:visible").last
            pop.get_by_test_id("scan-clear-btn").click()
            page.locator(".el-popconfirm__action button").filter(has_text="清空重扫").last.click()
            expect(total).to_have_text("0/4", timeout=8000)
            shot(page, "cleared_0of5")
            check("G1 清空重扫按钮生效 2/4→0/4", True)
            page.keyboard.press("Escape")

            # ---------- H. kiosk 副屏只读 ----------
            scan("M010200519A100005036272608310777", uat_ch)
            page.goto(f"{BASE}/#/monitor?kiosk=1&channel={uat_ch}", wait_until="domcontentloaded")
            kpanel = page.get_by_test_id("scan-slots-panel").first
            expect(kpanel).to_be_visible(timeout=15000)
            expect(kpanel.get_by_test_id("scan-total")).to_have_text("1/4", timeout=8000)
            shot(page, "kiosk_readonly_panel")
            check("H1 kiosk 面板可见且进度同步", True)
            check("H2 kiosk 只读: 无清空按钮",
                  kpanel.get_by_test_id("scan-clear-btn").count() == 0)
            check("H3 kiosk 只读: 无删码按钮",
                  kpanel.get_by_test_id("scan-code-remove").count() == 0)
            check("H4 kiosk 显示上组 NG 结果",
                  kpanel.get_by_test_id("scan-last-settled").count() == 1
                  and "NG" in kpanel.get_by_test_id("scan-last-settled").inner_text())

            # ---------- I. 工件追溯反查组件码 (确认单 7.5) ----------
            page.goto(f"{BASE}/#/mes", wait_until="domcontentloaded")
            page.get_by_role("button", name="工件追溯").click()
            page.get_by_placeholder("搜索序列号/条码...").fill("H-C035-527-5")
            page.keyboard.press("Enter")
            row = page.locator(".el-table__body tr", has_text="H-C035-527-5").first
            expect(row).to_be_visible(timeout=8000)
            row.click()
            codes_block = page.get_by_test_id("wp-scan-codes")
            expect(codes_block).to_be_visible(timeout=8000)
            shot(page, "workpiece_trace_codes")
            text = codes_block.inner_text()
            check("I1 追溯详情列出全部组件码",
                  all(c in text for c in ("M010200519A100005036272608310061", "9260000144908", "9260000146312")))
            check("I2 纠错删掉的码不进追溯", "9260000145631" not in text)

        finally:
            # ---------- 清理 ----------
            try:
                for _ch in range(3):
                    requests.post(f"{API}/scan-collect/clear", json={"channel_id": _ch}, timeout=5)
                requests.put(f"{API}/scan-collect/config?project_id={pid}",
                             json={"enabled": False, "slots": []}, timeout=5)
                requests.delete(f"{API}/projects/{pid}", timeout=10)
                log(f"清理完成: 项目 {pid} 已删除, 配置已停用")
            except Exception as e:
                log(f"清理异常(不影响结论): {e}")
            page.wait_for_timeout(800)
            ctx.close()
            browser.close()

    log(f"===== 结果: passed:{len(PASSED)} failed:{len(FAILED)} =====")
    if FAILED:
        log("失败项: " + ", ".join(FAILED))
    LOG.close()
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
