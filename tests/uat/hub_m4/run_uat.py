"""Fleet Hub M4 可见浏览器 UAT — 客户现场拓扑还原（4 机 12 工位）。

现场: 某客户 4 台工控机共 12 工位（1×2 + 2×3 + 1×4），枢纽部署在独立服务器。
本剧本用 4 个契约级假边缘还原该拓扑, headed 浏览器 + 慢速 + 录像走完整验收流:

  1. 登录（错口令被拒 → 正确口令进墙）
  2. 逐台纳管 4 台边缘机 → 墙上 4 组 12 工位
  3. 断掉 3 号机 → 墙面 alerts 出现离线告警 → 恢复
  4. 下钻 2 号机工位 1 → 远程开始检测（确认框）→ 状态翻"检测中"
  5. 停止检测（danger 确认）→ 切项目（下拉 + danger 确认）→ 消警
  6. 断言各假边缘状态: 只有被操作的机器/工位被改
  7. 登出

运行 (tianjun conda env):
  python tests/uat/hub_m4/run_uat.py            # headed + 慢速 + 录像
  HEADLESS=1 python tests/uat/hub_m4/run_uat.py # CI 冒烟形态

证据落 test-results/uat_hub_m4/: 视频 + 关键步骤截图 + run.log。
前置: hub/frontend/dist 已构建 (cd hub/frontend && npm run build)。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "test-results" / "uat_hub_m4"
OUT.mkdir(parents=True, exist_ok=True)

_log_file = open(OUT / "run.log", "w", encoding="utf-8")


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    _log_file.write(line + "\n")
    _log_file.flush()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_uvicorn(app, port: int):
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return server
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"uvicorn :{port} 未起来")


def main():
    from tests.hub.fake_edge import create_fake_edge

    # ---- 现场拓扑: 4 机 12 工位 (1×2 + 2×3 + 1×4) ----
    topology = [("装配1号机", 2), ("装配2号机", 3), ("包装1号机", 3), ("包装2号机", 4)]
    edges = []
    for i, (name, n_st) in enumerate(topology, start=1):
        app = create_fake_edge(node_id=f"edge-uat-{i:02d}", station_count=n_st)
        port = free_port()
        server = start_uvicorn(app, port)
        edges.append({"name": name, "app": app, "server": server,
                      "url": f"http://127.0.0.1:{port}", "stations": n_st})
        log(f"假边缘已起: {name} ({n_st} 工位) @ {edges[-1]['url']}")

    os.environ["HUB_DATA_DIR"] = str(OUT / "hub_data")
    os.environ["HUB_ENABLE_POLLER"] = "1"
    from hub.backend.main import create_app
    hub_port = free_port()
    start_uvicorn(create_app(), hub_port)
    hub_url = f"http://127.0.0.1:{hub_port}"
    log(f"枢纽已起: {hub_url}")

    headless = os.environ.get("HEADLESS", "0") == "1"
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless,
                                    slow_mo=0 if headless else 400)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(OUT), record_video_size={"width": 1440, "height": 900})
        page = ctx.new_page()

        # ---- 1. 登录 ----
        log("步骤1: 登录 (先错口令)")
        page.goto(f"{hub_url}/#/login", wait_until="domcontentloaded")
        page.wait_for_selector('[data-test="login-submit"]')
        page.fill('[data-test="login-username"]', "admin")
        page.fill('[data-test="login-password"]', "wrong-password")
        page.click('[data-test="login-submit"]')
        page.wait_for_selector('[data-test="login-error"]')
        log("  错口令被拒 ✓")
        page.fill('[data-test="login-password"]', "admin123")
        page.click('[data-test="login-submit"]')
        page.wait_for_selector('[data-test="wall-empty"]')
        log("  登录成功, 空墙 ✓")
        page.screenshot(path=str(OUT / "01_login_empty_wall.png"))

        # ---- 2. 纳管 4 台 ----
        log("步骤2: 逐台纳管 4 台边缘机")
        for e in edges:
            page.click('[data-test="enroll-open"]')
            page.wait_for_selector('[data-test="enroll-dialog"]')
            page.fill('[data-test="enroll-name"]', e["name"])
            page.fill('[data-test="enroll-url"]', e["url"])
            page.fill('[data-test="enroll-key"]', "tk_uat")
            page.click('[data-test="enroll-submit"]')
            page.wait_for_selector('[data-test="enroll-dialog"]',
                                   state="detached", timeout=10000)
            page.wait_for_selector(f'[data-test="group-card-{e["name"]}"]',
                                   timeout=10000)
            log(f"  已纳管 {e['name']} ✓")
        tiles = page.locator('[data-test^="station-tile-"]')
        page.wait_for_function(
            "document.querySelectorAll('[data-test^=\"station-tile-\"]').length === 12",
            timeout=10000)
        log(f"  墙上 12 工位齐 ✓ (实际 {tiles.count()})")
        page.screenshot(path=str(OUT / "02_wall_4_nodes_12_stations.png"),
                        full_page=True)

        # ---- 3. 断 3 号机 → 告警 → 恢复 ----
        log("步骤3: 断掉 包装1号机, 等墙面离线告警")
        edges[2]["server"].should_exit = True
        page.wait_for_selector('[data-test="alert-offline"]', timeout=30000)
        log("  离线告警出现 ✓")
        page.screenshot(path=str(OUT / "03_offline_alert.png"))
        edges[2]["server"] = start_uvicorn(edges[2]["app"],
                                           int(edges[2]["url"].rsplit(":", 1)[1]))
        page.wait_for_selector('[data-test="alert-offline"]', state="detached",
                               timeout=30000)
        log("  恢复后告警消失 ✓")

        # ---- 4/5. 下钻 装配2号机 工位1: 全套写操作 ----
        target = edges[1]
        log("步骤4: 下钻 装配2号机 工位1, 远程开始检测")
        page.click('[data-test="group-card-装配2号机"] [data-test$="-1"]')
        page.wait_for_selector('[data-test="station-panel"]')
        page.wait_for_selector('[data-test="op-start_detection"]:not([disabled])',
                               timeout=15000)
        page.click('[data-test="op-start_detection"]')
        page.wait_for_selector('[data-test="confirm-dialog"]')
        page.screenshot(path=str(OUT / "04_confirm_start.png"))
        page.click('[data-test="confirm-go"]')
        page.wait_for_selector('[data-test="field-detecting"]:has-text("检测中")',
                               timeout=10000)
        assert target["app"].state.edge["detecting"][1] is True
        assert target["app"].state.edge["detecting"][0] is False, "工位0不应被波及"
        log("  远程开始检测 ✓ (只有目标工位被改)")
        page.screenshot(path=str(OUT / "05_detecting.png"))

        log("步骤5: 停止 → 切项目 → 消警")
        page.click('[data-test="op-stop_detection"]')
        page.wait_for_selector('[data-test="confirm-dialog"]')
        page.click('[data-test="confirm-go"]')
        page.wait_for_selector('[data-test="field-detecting"]:has-text("待机")',
                               timeout=10000)
        assert target["app"].state.edge["detecting"][1] is False
        log("  停止检测 ✓")

        page.click('[data-test="op-activate_project"]')
        page.wait_for_selector('[data-test="project-select"]')
        page.select_option('[data-test="project-select"]', "2")
        page.screenshot(path=str(OUT / "06_project_select.png"))
        page.click('[data-test="confirm-go"]')
        page.wait_for_selector('[data-test="op-notice"]', timeout=8000)
        assert target["app"].state.edge["active_project_id"] == 2
        log("  切项目 → 项目2 ✓")

        page.click('[data-test="op-ack_alarm"]')
        page.wait_for_selector('[data-test="confirm-dialog"]')
        page.click('[data-test="confirm-go"]')
        page.wait_for_selector('[data-test="op-notice"]', timeout=8000)
        assert target["app"].state.edge["alarm_active"][1] is False
        assert edges[0]["app"].state.edge["alarm_active"][0] is True, \
            "其他机器报警不应被波及"
        log("  消警 ✓ (定向, 其他机器不受波及)")
        page.screenshot(path=str(OUT / "07_all_ops_done.png"))

        # ---- 6. 锁徽章 + 登出 ----
        page.wait_for_selector('[data-test="lock-tag"]')
        log("步骤6: 锁徽章可见 ✓, 返回墙登出")
        page.click('[data-test="back-to-wall"]')
        page.wait_for_selector('[data-test="wall-totals"]')
        page.screenshot(path=str(OUT / "08_final_wall.png"), full_page=True)
        page.click('[data-test="logout-btn"]')
        page.wait_for_selector('[data-test="login-submit"]')
        log("  登出 ✓")

        ctx.close()
        browser.close()

    log("UAT 全部通过 — 证据在 test-results/uat_hub_m4/")
    _log_file.close()


if __name__ == "__main__":
    main()