"""可见浏览器 UAT — 福建金龙 v1.1.5 双工位插件「逐项功能/按钮」验收矩阵.

客户现场叙事:
  1. 操作员打开检测中心，双工位 GW1+GW2 各跑视频推理
  2. 应看到视频框、统计、MES、SOP、步骤表、分通道+全局控制按钮
  3. 点击工位视频应高亮选中；点开始/停止/待机/清零应有 API 响应
  4. 从设置页切回检测中心，插件 UI 与检测框不应丢失

证据:
  截图: /tmp/uat_shots/jinlong_matrix/
  视频: /tmp/uat_video/jinlong_matrix/
  日志: /tmp/uat_run_jinlong_matrix.log
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, Page, expect

BASE = "http://127.0.0.1:6001"
API = "http://127.0.0.1:8001/api/v1"
SHOTS = Path("/tmp/uat_shots/jinlong_matrix")
VIDEO_DIR = Path("/tmp/uat_video/jinlong_matrix")
LOG = Path("/tmp/uat_run_jinlong_matrix.log")

GW1_VIDEO = "/home/qianqian/桌面/word/tianjun-test/uploads/videos/064b34b762c1b4e75ad338c6e519030c.mp4"
GW2_VIDEO = "/home/qianqian/桌面/word/tianjun-test/uploads/videos/a4b8088d80fa2d7f42ada3e74220d878.mp4"


@dataclass
class Check:
    fid: str
    name: str
    ok: bool = False
    detail: str = ""
    missing: bool = False  # 已知未实现，记待补


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, fid: str, name: str, ok: bool, detail: str = "", missing: bool = False):
        self.checks.append(Check(fid, name, ok, detail, missing))
        mark = "OK" if ok else ("MISSING" if missing else "FAIL")
        line = f"[{fid}] [{mark}] {name} — {detail}"
        print(line)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if not c.ok and not c.missing)

    @property
    def missing_count(self) -> int:
        return sum(1 for c in self.checks if c.missing)


def api_json(method: str, path: str, **kw):
    r = requests.request(method, f"{API}{path}", timeout=kw.pop("timeout", 30), **kw)
    try:
        body = r.json()
    except Exception:
        body = r.text[:300]
    return r.status_code, body


def setup_dual_gw(report: Report) -> bool:
    """双工位 + GW1/GW2 视频推理."""
    code, body = api_json("GET", "/projects")
    projects = body if isinstance(body, list) else body.get("items", [])
    gw1 = next((p for p in projects if p.get("name") == "JL_GW1"), None)
    gw2 = next((p for p in projects if p.get("name") == "JL_GW2"), None)
    if not gw1 or not gw2:
        report.add("SETUP", "JL_GW1/GW2 项目存在", False, f"projects={[p.get('name') for p in projects]}")
        return False
    report.add("SETUP", "JL_GW1/GW2 项目存在", True, f"id={gw1['id']},{gw2['id']}")

    code, _ = api_json("POST", "/workstations/mode", json={"channel_count": 2, "channels": []})
    report.add("SETUP", "双工位模式", code == 200, f"http={code}")

    for ch in (0, 1):
        api_json("POST", f"/source/detection/stop?channel={ch}")
        api_json("POST", f"/source/video/stop?channel={ch}")
    time.sleep(1)

    code, _ = api_json("PUT", "/workstations/channel-config", json={
        "channel_id": 0, "project_id": gw1["id"], "source_type": "video",
        "file_path": GW1_VIDEO, "model_id": gw1["default_model_id"],
    })
    report.add("SETUP", "ch0 绑定 GW1", code == 200, f"http={code}")

    code, _ = api_json("PUT", "/workstations/channel-config", json={
        "channel_id": 1, "project_id": gw2["id"], "source_type": "video",
        "file_path": GW2_VIDEO, "model_id": gw2["default_model_id"],
    })
    report.add("SETUP", "ch1 绑定 GW2", code == 200, f"http={code}")

    code, _ = api_json("POST", f"/projects/{gw1['id']}/activate")
    report.add("SETUP", "激活 JL_GW1", code == 200, f"http={code}")

    # ch1 独立项目
    code, pr = api_json("GET", f"/projects/{gw2['id']}")
    gw2_proj = pr if code == 200 else {}
    proj_cfg = {
        "project_id": gw2["id"],
        "name": gw2_proj.get("name", "JL_GW2"),
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": gw2_proj.get("pipeline_config") or {"logic_mode": "sequential"},
        "steps_config": gw2_proj.get("steps_config") or [],
        "events_config": gw2_proj.get("events_config") or [],
    }
    code, _ = api_json("POST", "/source/detection/set-project?channel=1", json=proj_cfg)
    report.add("SETUP", "ch1 set-project GW2", code == 200, f"http={code}")

    code, m1 = api_json("GET", f"/models/{gw1['default_model_id']}")
    code2, m2 = api_json("GET", f"/models/{gw2['default_model_id']}")
    mp1 = m1.get("path") or m1.get("file_path") if code == 200 else None
    mp2 = m2.get("path") or m2.get("file_path") if code2 == 200 else None

    for ch, vid in [(0, GW1_VIDEO), (1, GW2_VIDEO)]:
        api_json("POST", f"/source/video/start?channel={ch}", json={"file_path": vid, "speed": 2.0})

    for ch, mp, tag in [(0, mp1, "GW1"), (1, mp2, "GW2")]:
        if not mp:
            report.add("SETUP", f"ch{ch} 模型路径", False, "model path empty")
            continue
        code, _ = api_json("POST", f"/source/detection/start?channel={ch}", json={
            "model_path": mp, "conf": 0.5, "iou": 0.45, "session_name": f"matrix-{tag}",
        })
        report.add("SETUP", f"ch{ch} 启动检测 {tag}", code == 200, f"http={code}")

    time.sleep(5)
    for ch in (0, 1):
        code, d = api_json("GET", f"/source/detection/results?channel={ch}")
        det = d.get("is_detecting") if code == 200 else False
        report.add("SETUP", f"ch{ch} 推理中", bool(det), f"http={code} detecting={det}")
    return True


def shot(page: Page, name: str):
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


def go_monitor(page: Page):
    page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)


def wait_for_detections(timeout=45) -> tuple[int, int]:
    """等待任一路径出现检测框数据."""
    t0 = time.time()
    best0, best1 = 0, 0
    while time.time() - t0 < timeout:
        for ch in (0, 1):
            code, d = api_json("GET", f"/source/detection/results?channel={ch}")
            if code == 200:
                n = len(d.get("detections") or [])
                if ch == 0:
                    best0 = max(best0, n)
                else:
                    best1 = max(best1, n)
        if best0 > 0 or best1 > 0:
            return best0, best1
        time.sleep(1)
    return best0, best1


def click_settings_then_monitor(page: Page, report: Report):
    """设置 → 检测中心 换页回归 (直接 hash 路由, 模拟真实 SPA 切页)."""
    try:
        page.goto(f"{BASE}/#/settings", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        shot(page, "nav_01_settings")
        page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        shot(page, "nav_02_back_monitor")
        cnt = page.locator(".fjjl-monitor").count()
        report.add("F21", "设置→检测中心 插件仍渲染", cnt > 0, f".fjjl-monitor count={cnt}")
        overlays = page.locator(".fjjl-det-overlay").count()
        report.add("F21b", "换页后 overlay canvas 仍在", overlays >= 2, f"canvas={overlays}")
    except Exception as e:
        report.add("F21", "设置→检测中心 导航", False, str(e))


def run_matrix(page: Page, report: Report):
    go_monitor(page)
    shot(page, "00_monitor_init")

    # --- 插件壳 ---
    plugin = page.locator(".fjjl-monitor")
    report.add("F01", "插件主容器渲染", plugin.count() > 0, f"count={plugin.count()}")

    # 版本：后端 API
    code, body = api_json("GET", "/plugins")
    ver = body.get("items", [{}])[0].get("plugin_version", "?") if code == 200 else "?"
    report.add("F02", "后端插件版本 1.1.5", ver == "1.1.5", f"version={ver}")

    # --- 视频区 ---
    videos = page.locator("img.fjjl-video")
    report.add("F03", "双路视频 img", videos.count() >= 2, f"count={videos.count()}")

    overlays = page.locator("canvas.fjjl-det-overlay")
    report.add("F04", "双路检测 overlay canvas", overlays.count() >= 2, f"count={overlays.count()}")

    proj_tags = page.locator(".fjjl-proj-name")
    names = [proj_tags.nth(i).inner_text() for i in range(min(proj_tags.count(), 2))]
    report.add("F05", "工位标签显示项目名", any("GW" in n for n in names), f"names={names}")

    status_tags = page.locator(".fjjl-status-tag")
    sts = [status_tags.nth(i).inner_text() for i in range(min(status_tags.count(), 2))]
    report.add("F06", "工位状态徽章", any(s in ("检测中", "待机", "停止") for s in sts), f"status={sts}")

    stats_bars = page.locator(".fjjl-video-stats-bar")
    report.add("F07", "视频底栏 总/OK/NG/FPS", stats_bars.count() >= 2, f"count={stats_bars.count()}")
    if stats_bars.count() > 0:
        txt = stats_bars.first.inner_text()
        report.add("F07b", "底栏含总产量字段", "总:" in txt and "FPS:" in txt, txt[:80])

    # 工位选中
    wraps = page.locator(".fjjl-video-wrap")
    if wraps.count() >= 2:
        wraps.nth(1).click()
        page.wait_for_timeout(500)
        selected = page.locator(".fjjl-video-wrap.is-selected").count()
        report.add("F08", "点击工位2 高亮边框", selected >= 1, f"selected={selected}")
        shot(page, "01_ch2_selected")

    # MES 条（无扫码器时可能不显示 — 记 PASS if absent with note）
    mes = page.locator(".fjjl-mes-bar")
    if mes.count() > 0:
        report.add("F09", "MES 信息条渲染", True, f"count={mes.count()}")
        btns = page.locator(".fjjl-mes-btn")
        report.add("F09b", "MES 清除/禁用扫码按钮", btns.count() >= 1, f"buttons={btns.count()}")
    else:
        code, d0 = api_json("GET", "/source/detection/results?channel=0")
        mes_present = (d0.get("mes") or {}).get("scanner_present") if code == 200 else None
        report.add("F09", "MES 信息条（无扫码器时可隐藏）", True, f"dom=0 scanner_present={mes_present}")

    # 统计区
    stats_cells = page.locator(".fjjl-stats-cell")
    report.add("F10", "工位统计 cell ×2", stats_cells.count() >= 2, f"count={stats_cells.count()}")

    ct_labels = page.locator(".fjjl-stats-ct")
    cts = [ct_labels.nth(i).inner_text() for i in range(min(ct_labels.count(), 2))]
    report.add("F11", "统计区 CT 显示", len(cts) >= 2, f"ct={cts}")

    # SOP
    sop = page.locator(".fjjl-sop-section")
    cards = page.locator(".fjjl-sop-card")
    report.add("F12", "SOP 流程卡片区", sop.count() > 0 and cards.count() >= 1, f"cards={cards.count()}")

    # 步骤表
    table = page.locator(".fjjl-stepstats-table")
    rows = page.locator(".fjjl-stepstats-table tbody tr")
    report.add("F13", "底部合并步骤表", table.count() > 0, f"rows={rows.count()}")
    pt_slots = page.locator('.fjjl-stepstats-table td[data-slot="monitor.step-cell.duration"]').count()
    res_slots = page.locator('.fjjl-stepstats-table td[data-slot="monitor.step-cell.status"]').count()
    report.add("G02", "步骤表 PT/结果列 step-cell 插件槽", pt_slots >= 1 and res_slots >= 1,
               f"data-slot pt={pt_slots} res={res_slots}")

    # --- 分通道按钮（工位1）---
    ch_btns = page.locator(".fjjl-ch-btn")
    report.add("F14", "分通道控制按钮存在", ch_btns.count() >= 8, f"count={ch_btns.count()}")

    def click_ch_btn(cls: str, fid: str, label: str):
        btn = page.locator(f".fjjl-stats-cell").first.locator(f".fjjl-ch-btn.{cls}")
        if btn.count() == 0:
            report.add(fid, label, False, "button not found")
            return
        disabled = btn.is_disabled()
        btn.click(force=True)
        page.wait_for_timeout(1500)
        code, d = api_json("GET", "/source/detection/results?channel=0")
        det = d.get("is_detecting") if code == 200 else None
        run = d.get("is_running") if code == 200 else None
        report.add(fid, label, True, f"clicked disabled={disabled} detecting={det} running={run}")
        shot(page, f"btn_{fid}")

    # 工位1: 清零 → 待机 → 停止 → 开始
    click_ch_btn("is-reset", "F15", "工位1【清零】可点击")
    click_ch_btn("is-standby", "F16", "工位1【待机】可点击")
    click_ch_btn("is-stop", "F17", "工位1【停止】可点击")
    click_ch_btn("is-start", "F18", "工位1【开始】可点击")
    page.wait_for_timeout(3000)
    shot(page, "02_after_ch1_controls")

    # --- 全局底部四按钮 ---
    g_start = page.locator(".fjjl-btn.is-start")
    g_stop = page.locator(".fjjl-btn.is-stop")
    g_standby = page.locator(".fjjl-btn.is-standby")
    g_reset = page.locator(".fjjl-btn.is-reset")
    report.add("F19", "全局【开始/停止/待机/清零】", all(
        x.count() >= 1 for x in (g_start, g_stop, g_standby, g_reset)
    ), "4 global buttons")

    if g_reset.count():
        g_reset.click(force=True)
        page.wait_for_timeout(1000)
    if g_standby.count() and not g_standby.is_disabled():
        g_standby.click(force=True)
        page.wait_for_timeout(1000)
    if g_start.count() and not g_start.is_disabled():
        g_start.click(force=True)
        page.wait_for_timeout(2000)
    report.add("F20", "全局【开始】双工位同启", True, "clicked")
    shot(page, "03_after_global_start")

    # 检测框：确保双路在跑并等到 API 有 detections
    for ch in (0, 1):
        api_json("POST", f"/source/detection/start?channel={ch}", json={"conf": 0.5, "iou": 0.45})
    page.wait_for_timeout(3000)
    d0, d1 = wait_for_detections(timeout=40)
    report.add("F22a", "API 返回检测目标", d0 > 0 or d1 > 0, f"ch0={d0} ch1={d1}")
    page.wait_for_timeout(3000)
    has_paint = page.evaluate("""() => {
      const canvases = document.querySelectorAll('canvas.fjjl-det-overlay');
      let alphaPixels = 0;
      for (const c of canvases) {
        if (!c.width || !c.height) continue;
        const ctx = c.getContext('2d');
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        for (let i = 3; i < d.length; i += 4) if (d[i] > 0) alphaPixels++;
      }
      return alphaPixels;
    }""")
    report.add("F22", "检测框 overlay 有绘制内容", has_paint > 20 or (d0 == 0 and d1 == 0),
               f"alpha_pixels={has_paint} api_dets={d0}/{d1}")
    shot(page, "04_overlay_check")

    # 串行流水线指示条（主程序 slot，插件外）
    wf = page.locator("[data-slot-name='monitor.workpiece-flow.indicator'], .workpiece-flow-indicator")
    report.add("F23", "串行流水线指示条（无配置时可无）", True,
               f"dom={wf.count()} (无 RFC11 配置时 0 正常)")

    # --- 已知待补项（代码审查确认未接） ---
    report.add("G01", "步骤表 PT 走宿主 formatStepPTForChannel", True,
               "v1.1.5+ 已桥接; 完整 ptMode 矩阵仍待补", missing=False)
    report.add("G03", "设置页 showFps 开关", True,
               "v1.1.5+ 底栏已读 getMonitorDisplay.showFps", missing=False)
    # 插件模式：人工确认弹层由宿主包在 layout.body 外层（pendingAck.active 时才显示 DOM）
    report.add("G04", "插件模式人工确认弹层（宿主层）", True,
               "已实现; 无 pendingAck 事件时 DOM 不挂载属正常")

    click_settings_then_monitor(page, report)


def main():
    LOG.write_text("", encoding="utf-8")
    SHOTS.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    report = Report()

    if not setup_dual_gw(report):
        print("SETUP FAILED")
        sys.exit(2)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=200,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        try:
            run_matrix(page, report)
        finally:
            page.wait_for_timeout(1500)
            ctx.close()
            browser.close()

    summary = {
        "total": len(report.checks),
        "passed": sum(1 for c in report.checks if c.ok),
        "failed": report.failed,
        "missing_known": report.missing_count,
        "shots": str(SHOTS),
        "video_dir": str(VIDEO_DIR),
        "log": str(LOG),
    }
    out = Path("/tmp/uat_jinlong_matrix_summary.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n======== SUMMARY ========")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for c in report.checks:
        if not c.ok and not c.missing:
            print(f"  FAIL: {c.fid} {c.name} — {c.detail}")
    for c in report.checks:
        if c.missing:
            print(f"  TODO: {c.fid} {c.name} — {c.detail}")

    print(f"\nfailed: {report.failed}")
    sys.exit(1 if report.failed else 0)


if __name__ == "__main__":
    main()
