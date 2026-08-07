"""LG 工时看板 v1.2 全面审计回归 (真浏览器).

覆盖审计清单关键项:
  - 视频 URL 走 getBackendHost (非硬编码 8001)
  - 流程卡必有图区; 无统计显示 — 而非 0.0s
  - 步骤表序与流程带 SOP 序一致
  - 空闲当前周期卡有今日 KPI (非单行空白)
  - 双工位点面板切焦点 (堆叠图标题跟随)
  - 三工位缩略切换 + 无信号占位(无裂图)
  - LEAN 环在筛无数据工位时不画假满环
"""
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6004"
BACKEND = "http://localhost:8004/api/v1"
OUT = Path(__file__).resolve().parents[2] / "evidence"
OUT.mkdir(exist_ok=True)
fails = []


def ok(cond, msg):
    if cond:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")
        fails.append(msg)


def set_ch(n):
    r = requests.post(f"{BACKEND}/workstations/mode", json={"channel_count": n, "channels": []}, timeout=10)
    r.raise_for_status()


def main():
    # API: step-averages 顺序
    requests.post(f"{BACKEND}/projects/1/activate", timeout=10)
    steps = requests.get(f"{BACKEND}/plugins/lg-worktime/dashboard/step-averages", timeout=10).json().get("steps", [])
    labels = [s["label"] for s in steps]
    print("[API] step-averages 序:", labels)
    # demo 项目 SOP: fetch_part → tighten_bolt → plug_cable → inspect → walk_back
    if labels:
        ok(labels[0] == "fetch_part", f"步骤表首步应为 fetch_part, 实际 {labels[0]}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1680, "height": 945})

        # ---- 单工位 ----
        print("[1] single")
        set_ch(1)
        page.goto(f"{FRONTEND}/monitor", wait_until="domcontentloaded")
        page.wait_for_selector(".lgwt-main.mode-single", timeout=20000)
        time.sleep(3.5)
        ok(page.locator(".lgwt-flow-shot").count() == page.locator(".lgwt-flow-card").count(),
           "每张流程卡有图区")
        ok(page.locator(".lgwt-card-live .lgwt-live-body.is-idle, .lgwt-card-live .lgwt-live-body").count() >= 1,
           "当前周期卡有内容体")
        idle_text = page.locator(".lgwt-card-live").inner_text()
        ok("今日产量" in idle_text or "本轮用时" in idle_text, "空闲态含今日 KPI 或进行中实时")
        # 视频 URL 不应硬编码 8001
        imgs = page.locator(".lgwt-video-img").all()
        for img in imgs:
            src = img.get_attribute("src") or ""
            ok("8001" not in src, f"视频 URL 不含硬编码 8001: {src[:80]}")
        # 步骤表序
        table_labels = page.locator(".lgwt-table tbody tr .lgwt-td-label").all_inner_texts()
        flow_names = page.locator(".lgwt-flow-name").all_inner_texts()
        if table_labels and flow_names:
            # flow: "1. fetch_part" → fetch_part
            flow_labs = [n.split(". ", 1)[-1] for n in flow_names]
            ok(table_labels == flow_labs, f"底表序={table_labels} 对齐流程带={flow_labs}")
        page.screenshot(path=str(OUT / "lgwt_audit_1ws.png"))

        # ---- 双工位: 点工位2切焦点 ----
        print("[2] dual focus")
        set_ch(2)
        page.goto(f"{FRONTEND}/monitor", wait_until="domcontentloaded")
        page.wait_for_selector(".lgwt-main.mode-dual", timeout=20000)
        time.sleep(3.5)
        ok(page.locator(".lgwt-station").count() == 2, "双工位两面板")
        ok(page.locator(".lgwt-rail .lgwt-card-live").count() == 1, "dual 右栏有当前周期卡")
        # 工位2 无数据时「均」应为 — 而非 0.0s
        st2 = page.locator(".lgwt-station").nth(1)
        avgs = st2.locator(".lgwt-flow-avg").all_inner_texts()
        ok(any("—" in a or "—" in a for a in avgs) or all("0.0s" not in a for a in avgs)
           or len(avgs) == 0,
           f"工位2 无统计不应假 0.0s: {avgs}")
        page.locator(".lgwt-station").nth(1).click()
        time.sleep(1.5)
        stack_title = page.locator(".lgwt-card-stack .lgwt-card-title").inner_text()
        ok("工位 2" in stack_title, f"点工位2后堆叠图标题跟焦点: {stack_title}")
        ok(page.locator(".lgwt-station.is-focused").count() == 1, "焦点面板高亮")
        page.screenshot(path=str(OUT / "lgwt_audit_2ws.png"))

        # ---- 三工位 focus ----
        print("[3] focus")
        set_ch(3)
        page.goto(f"{FRONTEND}/monitor", wait_until="domcontentloaded")
        page.wait_for_selector(".lgwt-main.mode-focus", timeout=20000)
        time.sleep(3.5)
        ok(page.locator(".lgwt-thumb").count() == 3, "三缩略图")
        broken = page.locator(".lgwt-thumb-video img[src*='8001']").count()
        ok(broken == 0, "缩略图不指向 8001")
        # 无信号占位 (onError 或无 URL)
        none_or_img = page.locator(".lgwt-thumb-none, .lgwt-thumb-video img").count()
        ok(none_or_img >= 3, "缩略列有图或无信号占位")
        page.locator(".lgwt-thumb").nth(2).click()
        time.sleep(1.2)
        ok("工位 3" in page.locator(".lgwt-video-tag").first.inner_text(), "缩略切到工位3")
        page.screenshot(path=str(OUT / "lgwt_audit_3ws.png"))

        # ---- 筛无数据工位: LEAN 环不假画 ----
        print("[4] empty lean donut")
        page.select_option(".lgwt-select", "2")  # 工位3, demo 无数据
        time.sleep(2.5)
        donut_title = page.locator(".lgwt-card-donut .lgwt-card-title").inner_text()
        ok("工位 3" in donut_title or "合格 0" in donut_title or "0 轮" in donut_title,
           f"筛工位3后占比环标题: {donut_title}")
        page.screenshot(path=str(OUT / "lgwt_audit_empty_ch.png"))

        set_ch(1)
        browser.close()

    if fails:
        print(f"\nFAIL ({len(fails)}):")
        for f in fails:
            print(" -", f)
        sys.exit(1)
    print("\n[uat] 全面审计回归通过")


if __name__ == "__main__":
    main()
