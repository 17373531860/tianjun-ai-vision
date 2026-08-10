"""LG 工时看板全面回归: 数据口径 + 单/双/三工位布局 + OK/NG 提示框.

现场叙事:
  1. 领导在 Monitor 看板看当天数据; KPI/底表只应统计当前项目, 不混旧 demo 项目
  2. 单/双/三工位三种布局都要正常渲染, 且每种布局宿主都要有列级 Toast 层
  3. 检测运行中周期结束 → OK/NG 提示框真实弹出 (multiActiveToasts → 宿主渲染)

用法 (dev: 后端 8004 + 前端 6004 + lg-worktime 插件已激活):
    python tests/uat/uat_lg_worktime_full_check.py

产出: evidence/lgwt_full_{1,2,3}ws.png + lgwt_toast_live.png
"""
import re
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

FRONTEND = "http://localhost:6004"
BACKEND = "http://localhost:8004/api/v1"
VIDEO = "/Users/tianjun/Downloads/飞书20260804-171657.mp4"
OUT = Path(__file__).resolve().parents[2] / "evidence"
OUT.mkdir(exist_ok=True)
fails = []


def ok(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        fails.append(msg)


def set_channel_count(n):
    r = requests.post(f"{BACKEND}/workstations/mode",
                      json={"channel_count": n, "channels": []}, timeout=15)
    r.raise_for_status()
    print(f"[uat] channel_count -> {n}")


def ensure_detecting():
    st = requests.get(f"{BACKEND}/source/status", params={"channel": 0}, timeout=10).json()
    if not st.get("is_running"):
        requests.post(f"{BACKEND}/source/video/start", params={"channel": 0},
                      json={"file_path": VIDEO, "speed": 2.0}, timeout=30)
        time.sleep(1.5)
    if not st.get("is_detecting"):
        # body 必填 (DetectionStartRequest), 空 dict = 沿用已加载模型
        requests.post(f"{BACKEND}/source/detection/start", params={"channel": 0},
                      json={}, timeout=90)
        time.sleep(2)
    # 无论如何回到有动作的段落 (片尾是空画面, 播完 ended 不再出事件)
    requests.post(f"{BACKEND}/source/video/progress", params={"channel": 0},
                  json={"progress": 0.05}, timeout=10)
    st2 = requests.get(f"{BACKEND}/source/status", params={"channel": 0}, timeout=10).json()
    print("[uat] source:", {k: st2.get(k) for k in
                            ("is_running", "is_detecting", "model_loaded", "fps_actual")})
    return st2.get("is_detecting")


def active_project():
    d = requests.get(f"{BACKEND}/projects", timeout=10).json()
    for p in d["items"]:
        if p.get("is_active"):
            steps = [s.get("name") or s.get("label") for s in (p.get("steps_config") or [])]
            return p["id"], p["name"], steps
    # 无激活项目 (可能被别的测试搅了) → 按 ch0 持久化绑定的项目重新激活
    import json as _json
    from pathlib import Path as _P
    cfg = _json.loads((_P(__file__).resolve().parents[2]
                       / "backend" / "workstation_config.json").read_text())
    pid = (cfg.get("channels", {}).get("0", {}) or {}).get("project_id")
    candidates = [pid] if pid else []
    # ch0 绑定可能悬空 (被别的测试删了) → 退回项目列表里 id 最大的真实项目
    candidates += [p["id"] for p in sorted(d["items"], key=lambda x: -x["id"])]
    for cand in candidates:
        r = requests.post(f"{BACKEND}/projects/{cand}/activate", timeout=60)
        print(f"[uat] 无激活项目, 尝试激活 project {cand}: {r.status_code}")
        if r.status_code == 200:
            return active_project()
    return None, None, []


def toast_grid_info(page):
    return page.evaluate("""() => {
      const els = [...document.querySelectorAll("div[class*='z-[45]']")];
      const grid = els.find(e => (e.style.gridTemplateColumns || '').includes('repeat'));
      if (!grid) return null;
      const m = (grid.style.gridTemplateColumns || '').match(/repeat\\((\\d+)/);
      return { cols: m ? Number(m[1]) : null, children: grid.children.length };
    }""")


def main():
    pid, pname, psteps = active_project()
    print(f"[uat] 激活项目 id={pid} {pname} 步骤={psteps}")
    detecting = ensure_detecting()
    ok(detecting, "ch0 检测已启动 (视频 2x)")

    dash_reqs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1680, "height": 945})
        page.on("request", lambda r: dash_reqs.append(r.url)
                if "/plugins/lg-worktime/dashboard/" in r.url else None)

        def goto(n):
            set_channel_count(n)
            dash_reqs.clear()
            page.goto(f"{FRONTEND}/monitor", wait_until="domcontentloaded")
            page.wait_for_selector(".lgwt-dashboard", timeout=25000)
            page.wait_for_timeout(4000)

        def assert_scope(tag):
            """全部工位口径: 每类核心请求 (无 channel_id) 的最后一次必须带当前项目.
            首拉可能赶在 currentProject 灌入前 (无 project_id, 可容忍),
            但 watch(currentProject) 应立即重拉带上 → 最后一次必须正确."""
            latest = {}
            for u in dash_reqs:
                m = re.search(r"/(summary|step-averages|trend)\b", u)
                if m and "channel_id" not in u:
                    latest[m.group(1)] = u
            bad = {k: u for k, u in latest.items() if f"project_id={pid}" not in u}
            ok(len(latest) == 3 and not bad,
               f"{tag}: 全部工位口径带 project_id={pid} (违例 {list(bad) or '无'})")

        # ---------- 双工位 ----------
        goto(2)
        ok(page.locator(".lgwt-main.mode-dual").count() == 1, "2工位: mode-dual")
        ok(page.locator(".lgwt-station").count() == 2, "2工位: 两个面板")
        g = toast_grid_info(page)
        ok(g and g["cols"] == 2, f"2工位: 宿主 Toast 层 2 列 ({g})")
        assert_scope("2工位")
        page.screenshot(path=str(OUT / "lgwt_full_2ws.png"))

        # ---------- 三工位 ----------
        goto(3)
        ok(page.locator(".lgwt-main.mode-focus").count() == 1, "3工位: mode-focus")
        ok(page.locator(".lgwt-thumb").count() == 3, "3工位: 缩略列 3 张")
        g = toast_grid_info(page)
        ok(g and g["cols"] == 3, f"3工位: 宿主 Toast 层 3 列 ({g})")
        assert_scope("3工位")
        page.screenshot(path=str(OUT / "lgwt_full_3ws.png"))

        # ---------- 单工位 + 数据口径 ----------
        goto(1)
        ok(page.locator(".lgwt-main.mode-single").count() == 1, "1工位: mode-single")
        g = toast_grid_info(page)
        ok(g and g["cols"] == 1, f"1工位: 宿主 Toast 层 1 列 ({g})")

        # 数据口径①: 全部工位请求必须带当前项目 project_id
        page.wait_for_timeout(2000)
        assert_scope("1工位")

        # 数据口径②: 底表步骤只属于当前项目 (不混旧 demo fetch_part)
        labels = page.locator(".lgwt-td-label").all_inner_texts()
        alien = [x for x in labels if x and x not in psteps]
        ok(len(alien) == 0, f"底表步骤全属当前项目 (rows={len(labels)}, 混入={alien})")

        # 数据口径③: KPI 与后端 /summary?project_id 一致
        api = requests.get(f"{BACKEND}/plugins/lg-worktime/dashboard/summary",
                           params={"project_id": pid}, timeout=10).json()
        kpi_txts = page.locator(".lgwt-kpi-value").all_inner_texts()
        total = str(api.get("total_cycles", ""))
        ok(any(t.strip().startswith(total) for t in kpi_txts),
           f"KPI 今日完成={total} 与后端一致 (页面 KPI={kpi_txts[:4]})")

        page.screenshot(path=str(OUT / "lgwt_full_1ws.png"))

        # ---------- OK/NG 提示框实弹 ----------
        # 前面 2/3 工位阶段可能耗尽视频, 重新拉回活跃段并确保检测在跑
        ensure_detecting()
        page.wait_for_timeout(1500)
        print("[uat] 等真实 OK/NG Toast (视频 2x, 最多 150s)...")
        seen = {}
        deadline = time.time() + 150
        toast_sel = "div[class*='z-[45]'] .rounded-xl"
        while time.time() < deadline:
            cnt = page.locator(toast_sel).count()
            if cnt > 0:
                for i in range(cnt):
                    try:
                        txt = page.locator(toast_sel).nth(i).inner_text(timeout=500)
                    except Exception:
                        continue
                    head = txt.split("\n")[0].strip()
                    if head and head not in seen:
                        seen[head] = True
                        page.screenshot(
                            path=str(OUT / f"lgwt_toast_live_{len(seen)}.png"))
                        print(f"    toast[{len(seen)}]: {txt!r}")
                if len(seen) >= 2:
                    break
            page.wait_for_timeout(400)
        ok(len(seen) >= 1, f"周期 OK/NG 提示框真实弹出 (kinds={list(seen)})")

        # 后端仍活着
        alive = requests.get(f"{BACKEND}/source/status",
                             params={"channel": 0}, timeout=10).status_code == 200
        ok(alive, "后端全程存活")

        browser.close()

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
