"""LG 工时看板 v1.3 回归: 多工位全放视频 + 进度条 + 操作日志 + 流程带完整性.

现场叙事:
  1. 双/三工位每个工位都接同一段飞书录像, 各自独立推理
  2. 每个视频面板底部有进度条 (mm:ss + 可点击跳转)
  3. 右栏/独立列出现「操作日志」(事件/周期/启停), 当前周期缩成紧凑卡
  4. 流程带三种布局都是完整 9 步 (用户反馈"多工位看着步骤变少" — 实为滚动窗口)

用法 (dev: 后端 8004 + 前端 6004 + lg-worktime v1.3.0 已激活):
    python tests/uat/uat_lg_worktime_multivideo.py

产出: evidence/lgwt_mv_{2,3}ws.png + lgwt_mv_1ws.png + lgwt_mv_seek.png
"""
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6004"
BACKEND = "http://localhost:8004/api/v1"
VIDEO = "/Users/tianjun/Downloads/飞书20260804-171657.mp4"
MODEL = ("/Users/tianjun/Projects/tianjun-worktime/backend/uploads/models/"
         "3f0ccf3b0cbf4277b3a330aedce73541_best_lg_feishu.pt")
OUT = Path(__file__).resolve().parents[2] / "evidence"
OUT.mkdir(exist_ok=True)
fails = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        fails.append(msg)


def stop_all_detection():
    """切工位数前停所有通道检测 (对齐现场操作; macOS 上检测中重建通道会触发
    Metal 断言崩后端 — MTLCommandBuffer scheduled handler after commit)."""
    for ch in range(3):
        try:
            st = requests.get(f"{BACKEND}/source/status",
                              params={"channel": ch}, timeout=10).json()
            if st.get("is_detecting"):
                requests.post(f"{BACKEND}/source/detection/stop",
                              params={"channel": ch}, timeout=30)
                print(f"[uat] ch{ch} 检测已停")
        except Exception:
            pass
    time.sleep(1.0)


def set_mode(n):
    stop_all_detection()
    requests.post(f"{BACKEND}/workstations/mode",
                  json={"channel_count": n, "channels": []}, timeout=20).raise_for_status()
    print(f"[uat] channel_count -> {n}")


def start_channel(ch):
    """指定工位: 钉 CPU → 放视频 → 下发项目 → 起检测 (带 model_path, 按 mgr.device 加载)。
    ⚠ 必须下发项目配置: 否则 VSM 无 steps_config, 流程带退化成"只显示见过的标签"."""
    requests.post(f"{BACKEND}/workstations/{ch}/gpu",
                  json={"device": "cpu"}, timeout=10).raise_for_status()
    requests.post(f"{BACKEND}/source/video/start", params={"channel": ch},
                  json={"file_path": VIDEO, "speed": 2.0}, timeout=30).raise_for_status()
    proj = requests.get(f"{BACKEND}/projects/2", timeout=10).json()
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
    st = requests.get(f"{BACKEND}/source/status", params={"channel": ch}, timeout=10).json()
    if not st.get("is_detecting"):
        r = requests.post(f"{BACKEND}/source/detection/start", params={"channel": ch},
                          json={"model_path": MODEL}, timeout=120)
        print(f"[uat] ch{ch} detection/start: {r.status_code}")
    requests.post(f"{BACKEND}/source/video/progress", params={"channel": ch},
                  json={"progress": 0.05}, timeout=10)
    st2 = requests.get(f"{BACKEND}/source/status", params={"channel": ch}, timeout=10).json()
    print(f"[uat] ch{ch}:", {k: st2.get(k) for k in
                             ("is_running", "is_detecting", "model_loaded", "fps_actual")})
    return bool(st2.get("is_detecting"))


def painted_count(page):
    """有非透明像素的检测框 canvas 数."""
    return page.evaluate("""() => {
      let n = 0;
      document.querySelectorAll('canvas.fjjl-det-overlay').forEach((c) => {
        const w = c.width || 0, h = c.height || 0;
        if (w < 2 || h < 2) return;
        const d = c.getContext('2d').getImageData(0, 0, w, h).data;
        for (let i = 3; i < d.length; i += 32) { if (d[i] > 0) { n++; return; } }
      });
      return n;
    }""")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1680, "height": 945})

        def goto():
            page.goto(f"{FRONTEND}/monitor", wait_until="domcontentloaded")
            page.wait_for_selector(".lgwt-dashboard", timeout=25000)
            page.wait_for_timeout(4500)

        # ================= 双工位: 两路同视频 =================
        set_mode(2)
        ok(start_channel(0), "ch0 视频+检测已启动")
        ok(start_channel(1), "ch1 视频+检测已启动")
        goto()
        ok(page.locator(".lgwt-main.mode-dual").count() == 1, "2工位: mode-dual")
        imgs = page.locator("img.lgwt-video-img").count()
        ok(imgs == 2, f"2工位: 两路视频画面 (imgs={imgs})")
        vbars = page.locator(".lgwt-vbar").count()
        ok(vbars == 2, f"2工位: 两条进度条 (vbars={vbars})")
        flow2 = page.locator(".lgwt-flow-card").count()
        ok(flow2 == 18, f"2工位: 流程带完整 2×9 步 (cards={flow2})")
        page.wait_for_timeout(4000)
        pc = painted_count(page)
        ok(pc >= 1, f"2工位: 检测框实画 (painted canvases={pc})")
        ok(page.locator(".lgwt-card-oplog").count() == 1, "2工位: 操作日志面板在右栏")
        ok(page.locator(".lgwt-card-live.is-compact").count() == 1, "2工位: 当前周期紧凑卡")
        page.screenshot(path=str(OUT / "lgwt_mv_2ws.png"))

        # ================= 三工位: 三路同视频 =================
        set_mode(3)
        ok(start_channel(0), "ch0 视频+检测已启动")
        ok(start_channel(1), "ch1 视频+检测已启动")
        ok(start_channel(2), "ch2 视频+检测已启动")
        goto()
        ok(page.locator(".lgwt-main.mode-focus").count() == 1, "3工位: mode-focus")
        thumbs_live = page.evaluate("""() =>
          [...document.querySelectorAll('.lgwt-thumb img')].filter(i => i.src).length""")
        ok(thumbs_live == 3, f"3工位: 三张缩略图有画面 (n={thumbs_live})")
        ok(page.locator(".lgwt-station .lgwt-vbar").count() == 1, "3工位: 焦点面板有进度条")
        flow3 = page.locator(".lgwt-flow-card").count()
        ok(flow3 == 9, f"3工位: 焦点流程带完整 9 步 (cards={flow3})")
        ok(page.locator(".lgwt-card-oplog").count() == 1, "3工位: 操作日志面板在右栏")
        page.wait_for_timeout(3000)
        # 日志应已有行 (周期/事件)
        rows = page.locator(".lgwt-oplog-row").count()
        ok(rows >= 1, f"3工位: 操作日志有内容 (rows={rows})")
        page.screenshot(path=str(OUT / "lgwt_mv_3ws.png"))

        # ================= 单工位: 操作日志占原当前周期列 + seek =================
        set_mode(1)
        goto()
        ok(page.locator(".lgwt-main.mode-single").count() == 1, "1工位: mode-single")
        ok(page.locator(".lgwt-live-col .lgwt-card-oplog").count() == 1,
           "1工位: 操作日志占原「当前周期」独立列")
        ok(page.locator(".lgwt-rail .lgwt-card-live.is-compact").count() == 1,
           "1工位: 当前周期紧凑卡进右栏")
        flow1 = page.locator(".lgwt-flow-card").count()
        ok(flow1 == 9, f"1工位: 流程带完整 9 步 (cards={flow1})")
        ok(page.locator(".lgwt-vbar").count() == 1, "1工位: 视频进度条")

        # 进度条点击 seek: 点 80% 处 → 后端 progress 应跳到 ~0.8
        track = page.locator(".lgwt-vbar-track").first
        box = track.bounding_box()
        page.mouse.click(box["x"] + box["width"] * 0.8, box["y"] + box["height"] / 2)
        time.sleep(1.5)
        info = requests.get(f"{BACKEND}/source/video/info",
                            params={"channel": 0}, timeout=10).json()
        prog = info.get("progress")
        ok(prog is not None and abs(prog - 0.8) < 0.08,
           f"进度条点击跳转生效 (点80% → 后端 progress={None if prog is None else round(prog, 3)})")
        page.screenshot(path=str(OUT / "lgwt_mv_seek.png"))
        page.screenshot(path=str(OUT / "lgwt_mv_1ws.png"))

        browser.close()

    # 清场: 停 ch1/ch2 检测无从谈起 (单工位模式已回收), 视频回活跃段
    requests.post(f"{BACKEND}/source/video/progress", params={"channel": 0},
                  json={"progress": 0.05}, timeout=10)

    print("\n==== RESULT ====")
    if fails:
        print(f"failed:{len(fails)}")
        for f in fails:
            print(" -", f)
        return 1
    print("failed:0 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
