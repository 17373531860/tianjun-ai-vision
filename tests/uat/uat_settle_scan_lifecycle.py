# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 齐件即结算 + 扫码器生命周期闭环 (v3.50 捷昌二期).

现场叙事: 捷昌装配线操作员扫码 → 放料 → 所有物品放齐并稳定 N 帧后周期立即
判 OK (不等物品离开) → 扫码器只在 OK 后重新亮灯 (NG 灭灯等人工恢复),
亮灯同时作废旧码, 已合格的码永久拒绝 → 形成"码-合格-码"一一对应闭环。

覆盖 (T7 人眼证据, 参数全闭环):
1. 项目页逻辑设置: ROI离开策略下露出"全部合格立即结算"开关 → 打开;
   切"全部消失"策略开关消失 (语义守门) → 切回 ROI离开
2. 物品设置 tab: 开关开启后露出"确认放入帧数"列 → 填 5 → 保存 →
   GET /projects/{id} 验证 tracking_settle_on_complete + settle_confirm_frames 落库
3. MES 扫码器面板: 手动添加 LON 扫码器 → 默认 A 模式下生命周期选项置灰 →
   切 C 模式解锁 → 重新亮灯时机=仅合格 + 亮灯作废旧码 + 强制去重 → 保存 →
   GET /scanner/devices 验证三字段落库
4. 监控页"恢复扫码"人工出口: 注入 scanner_resume_blocked (模拟 ok_only 下
   NG 灭灯) → 按钮出现 → 点击 → 真调 POST /scanner/resume 返回 200

端口可用环境变量覆盖 (main 默认 6001/8001):
  UAT_FE_URL / UAT_API_URL
headless=False 真开浏览器; 截图 + 视频 + run.log 落盘。
"""
import json
import os
import re
import time
import uuid
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FE = os.environ.get("UAT_FE_URL", "http://localhost:6001")
API = os.environ.get("UAT_API_URL", "http://localhost:8001/api/v1")
TS = time.strftime("%Y%m%d_%H%M%S")
OUT = Path(os.environ.get("UAT_OUT_DIR", f"tests/uat/settle_scan_{TS}"))
VIDEO_DIR = Path(os.environ.get("UAT_VIDEO_DIR", f"/tmp/uat_video/settle_scan_{TS}"))
OUT.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

PROJ_NAME = f"__uat_捷昌齐件_{int(time.time())}"
DEV_NAME = f"__uat_捷昌枪_{uuid.uuid4().hex[:6]}"
results = []


def step(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {extra}" if extra else ""))
    results.append((name, ok))


def shot(page, f):
    path = OUT / f
    page.screenshot(path=str(path), full_page=False)
    print(f"  shot -> {path}")


def maybe_login(page):
    try:
        pw = page.locator("input[type=password]")
        if pw.count() > 0 and pw.first.is_visible():
            page.locator("input:not([type=password])").first.fill("admin")
            pw.first.fill("admin123")
            page.get_by_role("button", name=re.compile("登录|登 录|login", re.I)).first.click()
            time.sleep(2.5)
            print("  已登录 admin")
    except Exception as e:
        print(f"  (登录跳过: {str(e)[:80]})")


def create_tracking_project():
    r = requests.post(f"{API}/projects", json={
        "name": PROJ_NAME,
        "task_type": "detection",
        "logic_mode": "tracking",
        "pipeline_config": {
            "logic_mode": "tracking",
            "tracking_cycle_strategy": "roi_exit",
        },
        "steps_config": [
            {"id": 1, "label": "螺丝", "displayLabel": "螺丝", "enabled": True,
             "threshold": 50, "count_mode": "track", "expected_count": 2},
        ],
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    print(f"  已创建 UAT 项目 {PROJ_NAME} id={pid}")
    return pid


def open_project(page, name, tab=None):
    page.goto(f"{FE}/#/project")
    time.sleep(2.0)
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.8)
    page.locator(f"div.p-4:has-text('{name}')").first.click(timeout=8000)
    time.sleep(1.0)
    if tab:
        page.locator(f".el-tabs__item:has-text('{tab}')").first.click()
        time.sleep(0.8)


SOC_SWITCH = "[data-testid='settle-on-complete-switch']"


def part1_project_config(page, pid):
    """逻辑设置开关 + 物品设置确认帧数 → 保存落库。"""
    open_project(page, PROJ_NAME, tab="逻辑设置")
    ok = page.locator(SOC_SWITCH).count() > 0
    step("1.1 ROI离开策略下露出「全部合格立即结算」开关", ok)
    shot(page, "01_logic_roi_exit_switch.png")

    # 语义守门: 全部消失策略下开关消失
    page.locator("label:has-text('全部消失')").first.click()
    time.sleep(0.6)
    gone = page.locator(SOC_SWITCH).count() == 0
    step("1.2 全部消失策略下开关隐藏 (语义守门)", gone)
    shot(page, "02_logic_all_gone_no_switch.png")

    # 切回 ROI离开并打开开关
    page.locator("label:has-text('ROI')").first.click()
    time.sleep(0.6)
    page.locator(SOC_SWITCH).click()
    time.sleep(0.5)
    shot(page, "03_logic_switch_on.png")

    # 物品设置: 确认放入帧数列出现 → 填 5
    page.locator(".el-tabs__item:has-text('物品设置')").first.click()
    time.sleep(0.8)
    col_visible = "确认放入帧数" in page.evaluate("document.body.innerText")
    step("2.1 开关开启后物品设置露出「确认放入帧数」列", col_visible)
    inp = page.locator("[data-testid='settle-confirm-frames-input'] input").first
    inp.click()
    page.keyboard.press("Meta+a")
    page.keyboard.type("5")
    page.keyboard.press("Tab")
    time.sleep(0.5)
    shot(page, "04_steps_confirm_frames_5.png")

    page.locator("button:has-text('保存配置')").click()
    time.sleep(2.0)

    detail = requests.get(f"{API}/projects/{pid}", timeout=10).json()
    pc = detail.get("pipeline_config") or {}
    soc_saved = pc.get("tracking_settle_on_complete") is True
    frames_saved = int((detail.get("steps_config") or [{}])[0]
                       .get("settle_confirm_frames") or 0) == 5
    step("2.2 tracking_settle_on_complete=True 落库", soc_saved, str(pc.get("tracking_settle_on_complete")))
    step("2.3 螺丝步骤 settle_confirm_frames=5 落库", frames_saved)


def part2_scanner_panel(page):
    """扫码器面板: 生命周期三选项 A 模式置灰 → C 模式解锁 → 配置保存落库。"""
    page.goto(f"{FE}/#/mes")
    time.sleep(2.0)
    page.locator("button:has-text('扫码器')").first.click()
    time.sleep(1.0)
    page.locator("button:has-text('手动添加')").first.click()
    time.sleep(1.0)
    dlg = page.locator(".el-dialog:has-text('添加设备')")
    step("3.1 添加设备对话框打开", dlg.count() > 0)

    body = dlg.inner_text()
    rendered = all(k in body for k in ("重新亮灯时机", "亮灯作废旧码", "强制去重"))
    step("3.2 三个生命周期选项渲染", rendered)

    resume_item = dlg.locator(".el-form-item:has-text('重新亮灯时机')").first
    resume_item.scroll_into_view_if_needed()
    time.sleep(0.3)
    wrapper_cls = (resume_item.locator(".el-select .el-select__wrapper").first
                   .get_attribute("class") or "")
    step("3.3 默认 A 持续模式下「重新亮灯时机」置灰", "is-disabled" in wrapper_cls)
    shot(page, "05_scanner_lifecycle_disabled_A.png")

    # 填名称/IP + 切 C 模式
    dlg.locator(".el-form-item:has-text('名称') input").first.fill(DEV_NAME)
    dlg.locator(".el-form-item:has-text('IP 地址') input").first.fill("127.0.0.1")
    dlg.locator(".el-form-item:has-text('扫描模式') .el-select").first.click()
    time.sleep(0.6)
    page.locator(".el-select-dropdown__item:visible:has-text('C 单次/周期')").first.click()
    time.sleep(0.5)

    wrapper_cls2 = (resume_item.locator(".el-select .el-select__wrapper").first
                    .get_attribute("class") or "")
    step("3.4 切 C 模式后「重新亮灯时机」解锁", "is-disabled" not in wrapper_cls2)

    # 仅合格 + 作废旧码 + 强制去重
    resume_item.locator(".el-select").first.click()
    time.sleep(0.6)
    page.locator(".el-select-dropdown__item:visible:has-text('仅合格')").first.click()
    time.sleep(0.4)
    dlg.locator(".el-form-item:has-text('亮灯作废旧码') .el-switch").first.click()
    time.sleep(0.3)
    dlg.locator(".el-form-item:has-text('强制去重') .el-switch").first.click()
    time.sleep(0.3)
    shot(page, "06_scanner_lifecycle_configured.png")

    dlg.locator("button:has-text('确定'), button:has-text('保存')").last.click()
    time.sleep(2.0)

    devices = requests.get(f"{API}/scanner/devices", timeout=10).json()
    dev = next((d for d in devices if d.get("name") == DEV_NAME), None)
    saved = (dev is not None and dev["resume_on"] == "ok_only"
             and dev["rearm_forget_last"] is True and dev["strict_ok_dedup"] is True)
    step("3.5 仅合格+作废旧码+强制去重 三字段落库", saved,
         json.dumps({k: dev.get(k) for k in ("resume_on", "rearm_forget_last",
                                             "strict_ok_dedup")}, ensure_ascii=False)
         if dev else "设备未落库")
    shot(page, "07_scanner_device_card.png")
    return dev["id"] if dev else None


def part3_monitor_resume(page):
    """监控页恢复扫码按钮: 注入 NG 灭灯阻塞态 → 按钮出现 → 点击真调端点。"""
    def _fake_running(route):
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        data["is_running"] = True
        data["is_detecting"] = True
        data["source_type"] = data.get("source_type") or "video"
        route.fulfill(status=resp.status,
                      headers={"content-type": "application/json"},
                      body=json.dumps(data))

    def _inject(route):
        resp = route.fetch()
        try:
            data = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        mes = data.get("mes") or {}
        mes["scanner_resume_blocked"] = True
        data["mes"] = mes
        data["is_running"] = True
        data["is_detecting"] = True
        route.fulfill(status=resp.status,
                      headers={"content-type": "application/json"},
                      body=json.dumps(data))

    page.route("**/api/v1/source/status*", _fake_running)
    page.route("**/api/v1/source/detection/results*", _inject)
    resume_status = []
    page.on("response", lambda r: resume_status.append(r.status)
            if "/scanner/resume" in r.url else None)

    page.goto(f"{FE}/#/monitor")
    btn = page.locator("[data-testid='resume-scanner-btn']")
    try:
        btn.wait_for(state="visible", timeout=15000)
        step("4.1 NG 灭灯阻塞时监控页出现「恢复扫码」按钮", True)
    except Exception:
        step("4.1 NG 灭灯阻塞时监控页出现「恢复扫码」按钮", False)
        shot(page, "08_monitor_no_button_FAIL.png")
        return
    shot(page, "08_monitor_resume_button.png")

    btn.click()
    deadline = time.time() + 8
    while time.time() < deadline and not resume_status:
        page.wait_for_timeout(300)
    ok = bool(resume_status) and resume_status[0] == 200
    step("4.2 点击后真调 POST /scanner/resume 返回 200", ok,
         f"status={resume_status[:1]}")
    time.sleep(1.0)
    shot(page, "09_monitor_after_resume_click.png")
    page.unroute("**/api/v1/source/status*")
    page.unroute("**/api/v1/source/detection/results*")


def main():
    errs = []
    log_path = OUT / "run.log"
    pid = None
    dev_id = None
    print(f"UAT FE={FE} API={API} OUT={OUT} VIDEO={VIDEO_DIR}")
    try:
        pid = create_tracking_project()
        with sync_playwright() as p:
            b = p.chromium.launch(headless=False)
            context = b.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(VIDEO_DIR),
                record_video_size={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

            page.goto(f"{FE}/#/project")
            time.sleep(2.0)
            maybe_login(page)

            part1_project_config(page, pid)
            dev_id = part2_scanner_panel(page)
            part3_monitor_resume(page)

            context.close()
            b.close()
    finally:
        if dev_id:
            try:
                requests.delete(f"{API}/scanner/devices/{dev_id}", timeout=10)
                print(f"  已清理 UAT 扫码器 id={dev_id}")
            except Exception as e:
                print(f"  (清理扫码器失败: {e})")
        if pid:
            try:
                requests.delete(f"{API}/projects/{pid}", timeout=10)
                print(f"  已清理 UAT 项目 id={pid}")
            except Exception as e:
                print(f"  (清理项目失败: {e})")

    print("\n==== 汇总 ====")
    lines = [f"FE={FE}", f"API={API}", f"OUT={OUT}", f"VIDEO={VIDEO_DIR}"]
    for name, ok in results:
        line = f"  [{'PASS' if ok else 'FAIL'}] {name}"
        print(line)
        lines.append(line)
    fails = [n for n, ok in results if not ok]
    if errs:
        err_line = f"  console errors: {len(errs)} 条 (前3): {errs[:3]}"
        print(err_line)
        lines.append(err_line)
    summary = "ALL PASS" if not fails else f"FAIL: {fails}"
    print(summary)
    lines.append(summary)
    videos = list(VIDEO_DIR.glob("*.webm"))
    lines.append(f"videos={videos}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"run.log -> {log_path}")
    if videos:
        print(f"video -> {videos[0]}")
    if fails:
        raise SystemExit(summary)


if __name__ == "__main__":
    main()
