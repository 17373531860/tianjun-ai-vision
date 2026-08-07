"""LG 工时看板 v1.4.1 满负荷 UAT: 多视频多工位跑满 + VA 参数配置 + 设备卡钉底.

现场叙事:
  1. 项目管理(展会页)步骤表新增「动作价值」列 → 改 VA/BVA/NVA → 保存 → 落库
  2. 单工位: 视频靠左铺满面板; 操作日志列底部钉死设备状态卡
  3. 双工位: 两个不同视频 + 同一模型, 双路同时推理
  4. 三工位: 两同一不同视频 + 同一模型, 三路同时推理
  5. 全程采样各通道 fps (视频取流) / fps_inference (模型推理), 对照视频原生 30fps
  6. 盯前端: console error / pageerror / 5xx 响应全程收集, 应为 0

用法 (dev: 后端 8004 + 前端 6004 + lg-worktime v1.4.1 已激活):
    python tests/uat/uat_lg_worktime_loadtest.py

产出: evidence/lgwt_load_{1,2,3}ws.png + lgwt_load_va_config.png
"""
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6004"
BACKEND = "http://localhost:8004/api/v1"
VIDEO_A = "/Users/tianjun/Downloads/飞书20260804-171657.mp4"
VIDEO_B = "/Users/tianjun/Downloads/飞书20260804-171701.mp4"
MODEL = ("/Users/tianjun/Projects/tianjun-worktime/backend/uploads/models/"
         "3f0ccf3b0cbf4277b3a330aedce73541_best_lg_feishu.pt")
PID = 2
OUT = Path(__file__).resolve().parents[2] / "evidence"
OUT.mkdir(exist_ok=True)
fails = []
front_errors = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        fails.append(msg)


def stop_all_detection():
    for ch in range(3):
        try:
            st = requests.get(f"{BACKEND}/source/status", params={"channel": ch}, timeout=10).json()
            if st.get("is_detecting"):
                requests.post(f"{BACKEND}/source/detection/stop", params={"channel": ch}, timeout=30)
        except Exception:
            pass
    time.sleep(1.0)


def set_mode(n):
    stop_all_detection()
    requests.post(f"{BACKEND}/workstations/mode",
                  json={"channel_count": n, "channels": []}, timeout=30).raise_for_status()
    print(f"[uat] channel_count -> {n}")
    time.sleep(1.0)


def set_project(ch):
    """给通道下发项目配置 (对齐现场启动路径)。⚠ 不下发的话 VSM 无 steps_config,
    流程带会退化成"只显示见过的标签" (2026-08-05 用户实测踩过: 工位2/3 只剩 2 步)."""
    proj = requests.get(f"{BACKEND}/projects/{PID}", timeout=10).json()
    requests.post(f"{BACKEND}/source/detection/set-project", params={"channel": ch}, json={
        "project_id": proj["id"], "name": proj["name"],
        "task_type": proj.get("task_type") or "detection",
        "logic_mode": proj.get("logic_mode") or "sequential",
        "steps_config": proj.get("steps_config") or [],
        "pipeline_config": proj.get("pipeline_config") or {},
        "events_config": proj.get("events_config") or [],
        "counters_config": proj.get("counters_config") or [],
        "data_config": proj.get("data_config") or {},
    }, timeout=30).raise_for_status()


def start_channel(ch, video):
    """钉 CPU → 放视频 (1x 原速, 满帧口径) → 下发项目 → 起检测."""
    requests.post(f"{BACKEND}/workstations/{ch}/gpu", json={"device": "cpu"}, timeout=10).raise_for_status()
    requests.post(f"{BACKEND}/source/video/start", params={"channel": ch},
                  json={"file_path": video, "speed": 1.0}, timeout=30).raise_for_status()
    set_project(ch)
    st = requests.get(f"{BACKEND}/source/status", params={"channel": ch}, timeout=10).json()
    if not st.get("is_detecting"):
        r = requests.post(f"{BACKEND}/source/detection/start", params={"channel": ch},
                          json={"model_path": MODEL}, timeout=120)
        r.raise_for_status()
    print(f"[uat] ch{ch} <- {Path(video).name} @1x + 项目{PID}, 检测已起")


def sample_fps(channels, seconds, label):
    """采样各通道 fps / fps_inference / 视频位置推进, 返回统计."""
    stats = {ch: {"fps": [], "inf": [], "pos": []} for ch in channels}
    t_end = time.time() + seconds
    while time.time() < t_end:
        for ch in channels:
            try:
                d = requests.get(f"{BACKEND}/source/detection/results",
                                 params={"channel": ch}, timeout=5).json()
                stats[ch]["fps"].append(float(d.get("fps") or 0))
                stats[ch]["inf"].append(float(d.get("fps_inference") or 0))
                v = requests.get(f"{BACKEND}/source/video/info",
                                 params={"channel": ch}, timeout=5).json()
                stats[ch]["pos"].append(float(v.get("current_time") or 0))
            except Exception:
                pass
        time.sleep(3)
    print(f"[uat] === {label} fps 统计 ===")
    for ch in channels:
        f, i, p = stats[ch]["fps"], stats[ch]["inf"], stats[ch]["pos"]
        if not f:
            ok(False, f"{label} ch{ch}: 无采样")
            continue
        avg_f, min_f = sum(f) / len(f), min(f)
        avg_i = sum(i) / len(i) if i else 0
        moved = (p[-1] - p[0]) if len(p) >= 2 else 0
        print(f"  ch{ch}: 取流 avg={avg_f:.1f} min={min_f:.1f} | 推理 avg={avg_i:.1f} "
              f"| 视频推进 {moved:.1f}s / {seconds}s ({moved / seconds:.2f}x)")
        ok(avg_f >= 25, f"{label} ch{ch} 取流均值 {avg_f:.1f}fps 应≥25 (视频原生30)")
        # CPU 满载 + 浏览器多路全尺寸 MJPEG 时视频按 0.3~0.9x 节流是预期
        # (逐帧处理不丢帧); 这里只断言"持续推进不停滞", 速率如实上报
        ok(moved > seconds * 0.2, f"{label} ch{ch} 视频应持续推进不停滞 (实际 {moved:.1f}s)")
    return stats


def hook_frontend(page):
    page.on("console", lambda m: front_errors.append(f"console.{m.type}: {m.text}")
            if m.type == "error" and "favicon" not in m.text else None)
    page.on("pageerror", lambda e: front_errors.append(f"pageerror: {e}"))
    page.on("response", lambda r: front_errors.append(f"HTTP {r.status} {r.url[-80:]}")
            if r.status >= 500 else None)


def goto_monitor(page):
    """宿主冷启动会「恢复上次路由」(去过 /#/project 后整页加载 /monitor 会被弹回),
    所以整页加载后再用应用内 hash 导航到 /monitor (非冷启动, 不触发恢复)."""
    page.goto(f"{FRONTEND}/monitor", wait_until="domcontentloaded")
    time.sleep(2)
    if not page.locator(".lgwt-shell").count():
        page.evaluate("window.location.hash = '#/monitor'")
    page.wait_for_selector(".lgwt-shell", timeout=60000)
    time.sleep(3)


def check_device_pinned(page, tag):
    """日志列固定 2:1 铺满: 上 2/3 操作日志 + 下 1/3 设备状态 (与日志条数无关)."""
    col = page.locator(".lgwt-live-col").bounding_box()
    log = page.locator(".lgwt-live-col .lgwt-card-oplog").bounding_box()
    dev = page.locator(".lgwt-live-col .lgwt-card-device").bounding_box()
    assert col and log and dev, f"{tag}: 日志列/设备卡不存在"
    ratio = log["height"] / (log["height"] + dev["height"])
    ok(0.62 < ratio < 0.72, f"{tag}: 操作日志应占列高 2/3 (实际 {ratio:.0%})")
    gap = (col["y"] + col["height"]) - (dev["y"] + dev["height"])
    ok(abs(gap) < 8, f"{tag}: 设备卡应贴列底 (距底 {gap:.0f}px)")


def va_config_roundtrip(page):
    """展会项目页步骤表「动作价值」UI→DB 双向验证."""
    page.goto(f"{FRONTEND}/#/project", wait_until="domcontentloaded")
    page.wait_for_selector("iframe[src*='showcase-app']", timeout=20000)
    time.sleep(3)
    fr = page.frame_locator("iframe[src*='showcase-app']")
    fr.locator(f"[data-project-card][data-project-id='{PID}']").click()
    time.sleep(1.5)
    fr.locator(".project-tab[data-project-tab='steps']").click()
    time.sleep(1.0)
    sels = fr.locator("select[data-k='__lgwt_vt']")
    n = sels.count()
    ok(n >= 9, f"步骤表应出现动作价值列 (9 步, 实际 {n})")
    page.screenshot(path=str(OUT / "lgwt_load_va_config.png"))
    if not n:
        return
    before = requests.get(f"{BACKEND}/projects/{PID}", timeout=10).json()
    orig = (((before["steps_config"][0].get("plugin_data") or {}).get("lg-worktime") or {})
            .get("value_type") or "VA")
    target = "NVA" if orig != "NVA" else "BVA"
    sels.nth(0).select_option(target)
    fr.locator("#projSaveBtn").click()
    time.sleep(3)
    after = requests.get(f"{BACKEND}/projects/{PID}", timeout=10).json()
    got = (((after["steps_config"][0].get("plugin_data") or {}).get("lg-worktime") or {})
           .get("value_type"))
    ok(got == target, f"动作价值 UI 改 {orig}→{target} 应落库 (DB={got})")
    n_steps = len(after["steps_config"])
    ok(n_steps == len(before["steps_config"]),
       f"保存不应增删步骤 ({len(before['steps_config'])}→{n_steps})")
    # 还原, 不污染演示数据
    sels.nth(0).select_option(orig)
    fr.locator("#projSaveBtn").click()
    time.sleep(3)
    back = requests.get(f"{BACKEND}/projects/{PID}", timeout=10).json()
    got2 = (((back["steps_config"][0].get("plugin_data") or {}).get("lg-worktime") or {})
            .get("value_type"))
    ok(got2 == orig, f"动作价值还原 {target}→{orig} (DB={got2})")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1680, "height": 945})
        hook_frontend(page)

        # ---- 0. VA/BVA/NVA 参数配置 (项目管理·展会页) ----
        print("[uat] === VA 参数配置验证 ===")
        va_config_roundtrip(page)

        # ---- 1. 单工位: 视频铺满 + 设备卡钉底 ----
        print("[uat] === 单工位 ===")
        set_mode(1)
        start_channel(0, VIDEO_A)
        goto_monitor(page)
        check_device_pinned(page, "1工位")
        vw = page.locator(".lgwt-video-wrap").first.bounding_box()
        st = page.locator(".lgwt-station").first.bounding_box()
        gap_r = (st["x"] + st["width"]) - (vw["x"] + vw["width"])
        print(f"  视频宽 {vw['width']:.0f} 面板宽 {st['width']:.0f} 右侧余 {gap_r:.0f}px")
        ok(gap_r < 60, f"1工位视频应基本铺满面板 (右侧余 {gap_r:.0f}px)")
        sample_fps([0], 15, "1工位")
        page.screenshot(path=str(OUT / "lgwt_load_1ws.png"))

        # ---- 2. 双工位: 两个不同视频 + 同一模型 ----
        print("[uat] === 双工位 (视频A + 视频B, 同模型) ===")
        set_mode(2)
        start_channel(0, VIDEO_A)
        start_channel(1, VIDEO_B)
        goto_monitor(page)
        ok(page.locator(".lgwt-station").count() == 2, "双工位应 2 面板")
        sample_fps([0, 1], 45, "2工位")
        page.screenshot(path=str(OUT / "lgwt_load_2ws.png"))

        # ---- 3. 三工位: 两同一不同视频 + 同一模型 ----
        print("[uat] === 三工位 (A+A+B, 同模型) ===")
        set_mode(3)
        start_channel(0, VIDEO_A)
        start_channel(1, VIDEO_A)
        start_channel(2, VIDEO_B)
        goto_monitor(page)
        check_device_pinned(page, "3工位")
        ok(page.locator(".lgwt-thumb").count() == 3, "三工位缩略列应 3 张")
        sample_fps([0, 1, 2], 45, "3工位")
        # 点缩略图切到工位3 (看 B 视频面板), 顺带盯前端
        page.locator(".lgwt-thumb").nth(2).click()
        time.sleep(2)
        page.screenshot(path=str(OUT / "lgwt_load_3ws.png"))

        # ---- 4. 前端异常汇总 ----
        real_errors = [e for e in front_errors if "ResizeObserver" not in e]
        ok(not real_errors, f"前端应无异常 (收集到 {len(real_errors)} 条)")
        for e in real_errors[:10]:
            print("   前端异常:", e)

        browser.close()

    if fails:
        print(f"[uat] FAIL ({len(fails)}): {fails}")
        sys.exit(1)
    print("[uat] 满负荷 UAT 全部通过 (检测保持运行中, 可直接上浏览器看)")


if __name__ == "__main__":
    main()
