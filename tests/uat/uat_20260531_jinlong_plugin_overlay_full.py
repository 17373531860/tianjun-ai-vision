"""福建金龙插件 — 全链路逐步验收 (用户已配好 JL_GW1/JL_GW2 + 视频).

逐步检查:
  1. 后端 / 前端存活
  2. 插件 active + ESM 含 overlay 代码
  3. 两工位项目 / 视频 / 模型路径
  4. 重启视频 + 开检测 → API 有 detections
  5. Playwright 进 Monitor → 插件 DOM + overlay canvas 有绘制

证据: /tmp/uat_jinlong_overlay_full/
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:6001"
API = "http://127.0.0.1:8001/api/v1"
OUT = Path("/tmp/uat_jinlong_overlay_full")
OUT.mkdir(parents=True, exist_ok=True)

steps: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str = "") -> bool:
    steps.append((label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{len(steps):02d}] [{mark}] {label}" + (f"  — {detail}" if detail else ""))
    return ok


def must(label: str, ok: bool, detail: str = "") -> None:
    if not record(label, ok, detail):
        raise SystemExit(1)


def canvas_has_strokes(page, selector: str) -> tuple[bool, str]:
    """采样 canvas 像素，非全透明即认为有检测框绘制."""
    return page.evaluate(
        """(sel) => {
          const c = document.querySelector(sel);
          if (!c) return { ok: false, reason: 'no-canvas' };
          const ctx = c.getContext('2d');
          const w = c.width, h = c.height;
          if (!w || !h) return { ok: false, reason: 'zero-size', w, h };
          const data = ctx.getImageData(0, 0, w, h).data;
          let colored = 0;
          for (let i = 3; i < data.length; i += 16) {
            if (data[i] > 0) { colored++; if (colored > 5) break; }
          }
          return { ok: colored > 5, colored, w, h };
        }""",
        selector,
    )


def main() -> None:
    print("\n=== 福建金龙插件全链路验收 ===\n")

    # --- 1. 服务存活 ---
    print("[阶段 1] 服务存活")
    try:
        rb = requests.get(f"{API}/projects", timeout=30)
        rf = requests.get(BASE, timeout=10)
        must("后端 /api/v1/projects 200", rb.status_code == 200, str(rb.status_code))
        must("前端首页 200", rf.status_code == 200, str(rf.status_code))
    except Exception as exc:
        must("服务可达", False, str(exc))

    # --- 2. 插件 ---
    print("\n[阶段 2] 插件连接")
    plugins = requests.get(f"{API}/plugins", timeout=5).json()
    active = plugins.get("active_customer_code")
    items = plugins.get("items") or []
    active_row = next((x for x in items if x.get("is_active")), None)
    must("插件 internal-demo 已激活", active == "internal-demo", f"active={active}")
    must(
        "插件 runtime loaded",
        (active_row or {}).get("runtime_status") == "loaded",
        str((active_row or {}).get("runtime_status")),
    )
    esm = requests.get(
        f"{API}/plugins/active/assets/frontend/dist/index.esm.js", timeout=10
    ).text
    must("插件 ESM 含 overlay canvas 类名", "fjjl-det-overlay" in esm)
    must("插件 ESM 调 renderDetectionOverlay", "renderDetectionOverlay" in esm)

    # --- 3. 用户配置 ---
    print("\n[阶段 3] 工位 / 项目 / 资产")
    ws = requests.get(f"{API}/workstations/", timeout=5).json()
    cfgs = ws.get("source_configs") or {}
    ch0 = cfgs.get("0") or {}
    ch1 = cfgs.get("1") or {}
    must("工位1 项目 JL_GW1 (id=2)", ch0.get("project_id") == 2, str(ch0))
    must("工位2 项目 JL_GW2 (id=3)", ch1.get("project_id") == 3, str(ch1))
    must("工位1 视频文件已配", bool(ch0.get("video_file")), ch0.get("video_file", ""))
    must("工位2 视频文件已配", bool(ch1.get("video_file")), ch1.get("video_file", ""))

    m1 = requests.get(f"{API}/models/3", timeout=5).json()
    m2 = requests.get(f"{API}/models/4", timeout=5).json()
    gw1_model = m1.get("file_path") or ""
    gw2_model = m2.get("file_path") or ""
    must("JL_GW1 模型路径存在", Path(gw1_model).is_file(), gw1_model)
    must("JL_GW2 模型路径存在", Path(gw2_model).is_file(), gw2_model)

    # --- 4. 重启视频 + 检测 ---
    print("\n[阶段 4] 重启视频并开检测")
    for ch in (0, 1):
        requests.post(f"{API}/source/detection/stop", params={"channel": ch}, timeout=10)
    time.sleep(0.5)

    vid0 = ch0["video_file"]
    vid1 = ch1["video_file"]
    for ch, vid in ((0, vid0), (1, vid1)):
        r = requests.post(
            f"{API}/source/video/start",
            params={"channel": ch},
            json={"file_path": vid, "speed": 1.0},
            timeout=20,
        )
        must(f"工位{ch+1} 视频重启", r.status_code == 200, r.text[:120])

    for ch, mp in ((0, gw1_model), (1, gw2_model)):
        r = requests.post(
            f"{API}/source/detection/start",
            params={"channel": ch},
            json={"model_path": mp, "conf": 0.5, "iou": 0.45, "session_name": f"uat-overlay-ch{ch}"},
            timeout=60,
        )
        must(f"工位{ch+1} 检测启动", r.status_code == 200, r.text[:120])

    # 等推理出框
    det_ok = False
    for _ in range(30):
        time.sleep(1)
        n0 = len(requests.get(f"{API}/source/detection/results", params={"channel": 0}, timeout=5).json().get("detections") or [])
        n1 = len(requests.get(f"{API}/source/detection/results", params={"channel": 1}, timeout=5).json().get("detections") or [])
        if n0 > 0 or n1 > 0:
            det_ok = True
            record("后端 API 返回 detections", True, f"ch0={n0} ch1={n1}")
            break
    must("后端推理有检测框数据", det_ok, "30s 内 detections 仍为空")

    # --- 5. 浏览器 + 插件 UI + overlay ---
    print("\n[阶段 5] 浏览器 Monitor + 检测框 overlay")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000)

        page.screenshot(path=str(OUT / "01_monitor_init.png"), full_page=True)
        must("插件双工位容器 .fjjl-grid 存在", page.locator(".fjjl-grid").count() > 0)

        overlays = page.locator(".fjjl-det-overlay")
        must("两路 overlay canvas 在 DOM", overlays.count() >= 2, f"count={overlays.count()}")

        # 轮询等两路 canvas 都画出框 (最多 25s)
        ch_results = [{}, {}]
        deadline = time.time() + 25
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            for i in range(2):
                if ch_results[i].get("ok"):
                    continue
                ch_results[i] = overlays.nth(i).evaluate(
                    """(c) => {
                      const ctx = c.getContext('2d');
                      const w = c.width, h = c.height;
                      if (!w || !h) return { ok: false, reason: 'zero-size', w, h };
                      const data = ctx.getImageData(0, 0, w, h).data;
                      let colored = 0;
                      for (let j = 3; j < data.length; j += 16) {
                        if (data[j] > 0) { colored++; if (colored > 5) break; }
                      }
                      return { ok: colored > 5, colored, w, h };
                    }"""
                )
            if ch_results[0].get("ok") and ch_results[1].get("ok"):
                break

        page.screenshot(path=str(OUT / "02_monitor_after_wait.png"), full_page=True)
        for i, res in enumerate(ch_results):
            record(f"工位{i+1} overlay canvas 有绘制", res.get("ok"), str(res))

        must(
            "两路视频上都有检测框",
            ch_results[0].get("ok") and ch_results[1].get("ok"),
            str(ch_results),
        )

        # 插件是否收到主程序 actions
        has_actions = page.evaluate(
            """() => {
              const cells = document.querySelectorAll('.fjjl-video-cell');
              return cells.length >= 2;
            }"""
        )
        record("插件 VideoCell 已挂载", has_actions)

        browser.close()

    print("\n=== 汇总 ===")
    passed = sum(1 for _, ok, _ in steps if ok)
    failed = [s for s, ok, d in steps if not ok]
    print(f"通过 {passed}/{len(steps)}")
    if failed:
        print("失败项:", ", ".join(f[0] for f in failed))
        sys.exit(1)
    print(f"截图目录: {OUT}")
    print("全部通过 ✓")


if __name__ == "__main__":
    main()
