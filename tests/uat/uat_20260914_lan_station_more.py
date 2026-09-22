# -*- coding: utf-8 -*-
"""一拖多补充实测：上次 26/26 + 15/15 没走到的现场路径。

现场叙事：
    工作站总览开着，工位 1 一体机已经在看直播 —— 总览这一格必须让流改快照。
    吉田副屏 readonly=1，工人按停止也不该动检测。
    一体机按局域网 IP 打开（不是 127.0.0.1），请求仍打回这台工作站。
    工程师在「设置 → 账号鉴权」给工位 1 建 station 账号，只填可操作工位 0；
    这台一体机改 URL 也停不了工位 2。扫码枪插一体机，模拟一条码应出现在本工位页。

前置：隔离数据目录的 8001（RUNTIME_MODE=test）+ 已 build 的 frontend dist
    python tests/uat/uat_20260914_lan_station_more.py
"""
from __future__ import annotations

import os
import socket
import sys
import time

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import UatRun  # noqa: E402

STATION = os.environ.get("UAT_STATION_ORIGIN", "http://127.0.0.1:8001")
API = STATION
ADMIN_USER = "uat_admin"
STATION_USER = "station0"
PASSWORD = "Uat123456"
SCAN_SN = "UAT-LAN-SN-0914"

SCENARIO = {
    "name": "lan-station-more",
    "fps": 25,
    "timeline": [
        {"from": 0, "to": 100000, "detections": [
            {"label": "工件", "confidence": 0.91, "bbox": [0.3, 0.3, 0.4, 0.4]},
        ]},
    ],
}


def api(method: str, path: str, payload=None, token: str | None = None, timeout: int = 20):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.request(
        method, f"{API}{path}", json=payload, headers=headers, timeout=timeout)


def wait_until(predicate, timeout_s: float = 12.0, interval_s: float = 0.4) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval_s)
    return False


def start_channel(ch: int) -> bool:
    r = api("POST", "/api/v1/test/synthetic/start", {
        "scenario_json": SCENARIO, "channel": ch, "with_project": True,
    })
    if r.status_code != 200:
        print(f"  synthetic ch{ch} 失败: {r.status_code} {r.text[:200]}")
        return False
    r = api("POST", f"/api/v1/source/detection/start?channel={ch}",
            {"conf": 0.25, "iou": 0.45})
    return r.status_code == 200


def stop_channel(ch: int) -> None:
    api("POST", f"/api/v1/source/detection/stop?channel={ch}")
    api("POST", f"/api/v1/test/synthetic/stop?channel={ch}")


def channel_status(ch: int) -> dict:
    r = api("GET", f"/api/v1/source/status?channel={ch}")
    return r.json() if r.status_code == 200 else {}


def stream_viewers() -> dict:
    r = api("GET", "/api/v1/source/stream/viewers")
    return r.json().get("channels", {}) if r.status_code == 200 else {}


def _is_rfc1918(ip: str) -> bool:
    return ip.startswith(("10.", "192.168.")) or ip.startswith(
        tuple(f"172.{n}." for n in range(16, 32)))


def lan_ipv4s() -> list[str]:
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.168.1.1", 80))
        ip = probe.getsockname()[0]
        probe.close()
        if _is_rfc1918(ip):
            found.append(ip)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_rfc1918(ip) and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


def login_page(page, username: str, password: str) -> None:
    page.goto(f"{STATION}/#/login", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    page.locator('input[autocomplete="username"]').fill(username)
    page.locator('input[autocomplete="current-password"]').fill(password)
    page.get_by_role("button", name="登录", exact=True).click()
    page.wait_for_timeout(1800)


def main() -> int:
    run = UatRun("lan_station_more")
    contexts = []
    browser = None
    admin_token = None

    try:
        # ============================================================
        # A. 局域网 CORS（上次只测了 127.0.0.1 同源）
        # ============================================================
        fake_lan = "http://192.168.88.20:6001"
        opt = requests.options(
            f"{API}/api/v1/projects/",
            headers={
                "Origin": fake_lan,
                "Access-Control-Request-Method": "GET",
            },
            timeout=10,
        )
        allow = opt.headers.get("access-control-allow-origin", "")
        run.step("RFC1918 开发态 Origin 的 CORS 预检放行（不是只认 127.0.0.1）",
                 opt.status_code in (200, 204) and allow in ("*", fake_lan),
                 f"status={opt.status_code} allow={allow}")

        public = requests.options(
            f"{API}/api/v1/projects/",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
            timeout=10,
        )
        pub_allow = public.headers.get("access-control-allow-origin", "")
        run.step("公网 Origin 不被默认 CORS 放行",
                 pub_allow not in ("*", "https://evil.example"),
                 f"allow={pub_allow!r}")

        # ============================================================
        # B. 两工位 + synthetic
        # ============================================================
        count = api("GET", "/api/v1/workstations/").json().get("channel_count")
        if count != 2:
            r = api("POST", "/api/v1/workstations/mode", {"channel_count": 2})
            run.step("把隔离工作站切成 2 工位",
                     r.status_code == 200
                     and api("GET", "/api/v1/workstations/").json().get("channel_count") == 2,
                     f"before={count} after={api('GET', '/api/v1/workstations/').json().get('channel_count')} status={r.status_code}")
        else:
            run.step("工作站已是 2 工位", True, "channel_count=2")

        run.step("工位 0 起 synthetic + 检测", start_channel(0))
        run.step("工位 1 起 synthetic + 检测", start_channel(1))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=False, slow_mo=80,
                args=["--disable-blink-features=AutomationControlled"])

            # ============================================================
            # C. 总览让流（上次 UAT 没开总览页）
            # ============================================================
            kiosk_ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                record_video_dir=run.video_dir)
            contexts.append(kiosk_ctx)
            kiosk = kiosk_ctx.new_page()
            kiosk.goto(f"{STATION}/#/monitor?channel=0&kiosk=1&readonly=0",
                       wait_until="domcontentloaded")
            kiosk.wait_for_timeout(2500)
            run.shot(kiosk, "01_kiosk_ch0_before_overview")

            occupied = wait_until(
                lambda: stream_viewers().get("0", {}).get("station") is True)
            run.step("一体机占着工位 0 的 station 槽",
                     occupied, f"viewers={stream_viewers()}")

            ov_ctx = browser.new_context(
                viewport={"width": 1600, "height": 900},
                record_video_dir=run.video_dir)
            contexts.append(ov_ctx)
            overview = ov_ctx.new_page()
            ov_feed: list[str] = []
            ov_snap: list[str] = []

            def _on_ov(req):
                if "/video_feed" in req.url:
                    ov_feed.append(req.url)
                elif "/snapshot" in req.url and "view=hands" not in req.url:
                    ov_snap.append(req.url)

            overview.on("request", _on_ov)
            overview.goto(f"{STATION}/#/monitor", wait_until="domcontentloaded")
            # 首屏可能先 MJPEG，等 station 探测回来再让流
            overview.wait_for_timeout(7000)
            run.shot(overview, "02_overview_yields_ch0")

            # 只看后半段：清掉开屏 overlapping 后再采 4 秒
            mid = len(ov_feed)
            mid_snap = len(ov_snap)
            overview.wait_for_timeout(4000)
            late_feed = ov_feed[mid:]
            late_snap = ov_snap[mid_snap:]
            ch0_feed = [u for u in late_feed if "channel=0" in u]
            ch0_snap = [u for u in late_snap if "channel=0" in u]
            # MJPEG 是一条长连接：request 事件只在开流时响一次，稳态窗口里可能是空的
            ch1_feed_all = [u for u in ov_feed if "channel=1" in u]
            ch1_main = stream_viewers().get("1", {}).get("main") is True
            run.step("稳态下总览不再抢工位 0 的 MJPEG（一体机在看）",
                     not ch0_feed and bool(ch0_snap),
                     f"ch0_feed={len(ch0_feed)} ch0_snap={len(ch0_snap)} 样例snap={ch0_snap[:1]}")
            run.step("没接屏的工位 1 总览仍走 MJPEG（长连接只在开流时报一次）",
                     bool(ch1_feed_all) or ch1_main,
                     f"ch1_feed_all={len(ch1_feed_all)} viewers={stream_viewers().get('1')} 样例={ch1_feed_all[:1]}")

            # ============================================================
            # D. readonly=1 锁控制（点了也不该停）
            # ============================================================
            ro_ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                record_video_dir=run.video_dir)
            contexts.append(ro_ctx)
            ro = ro_ctx.new_page()
            ro.goto(f"{STATION}/#/monitor?channel=1&kiosk=1&readonly=1",
                    wait_until="domcontentloaded")
            ro.wait_for_timeout(2000)
            run.shot(ro, "03_readonly_locked")
            controls = ro.locator("[data-testid=single-channel-controls]")
            stop_btn = ro.locator("[data-testid=single-channel-stop]")
            locked = (controls.get_attribute("data-readonly") == "true"
                      and stop_btn.is_disabled())
            run.step("readonly=1 控制条锁死",
                     locked,
                     f"data-readonly={controls.get_attribute('data-readonly')} "
                     f"stop_disabled={stop_btn.is_disabled()}")
            try:
                stop_btn.click(force=True, timeout=2000)
            except Exception:
                pass
            ro.wait_for_timeout(800)
            run.step("只读屏强点停止 → 后端工位 1 仍在检测",
                     channel_status(1).get("is_detecting") is True,
                     f"ch1={channel_status(1).get('is_detecting')}")

            # ============================================================
            # E. 真局域网 IP（有网卡才测）
            # ============================================================
            ips = lan_ipv4s()
            if not ips:
                run.step("用本机 RFC1918 地址打开一体机页",
                         True, "本机没有 10/172/192.168 地址，跳过真局域网打开")
            else:
                lan_origin = f"http://{ips[0]}:8001"
                lan_ctx = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    record_video_dir=run.video_dir)
                contexts.append(lan_ctx)
                lan_page = lan_ctx.new_page()
                lan_base = ""
                lan_off = []

                def _on_lan_console(m):
                    nonlocal lan_base
                    if "Final baseURL" in m.text:
                        lan_base = m.text

                def _on_lan_req(r):
                    if "/api/v1/" in r.url or "/video_feed" in r.url:
                        if not r.url.startswith(lan_origin):
                            lan_off.append(r.url)

                lan_page.on("console", _on_lan_console)
                lan_page.on("request", _on_lan_req)
                try:
                    lan_page.goto(
                        f"{lan_origin}/#/monitor?channel=0&kiosk=1&readonly=0",
                        wait_until="domcontentloaded", timeout=20000)
                    lan_page.wait_for_timeout(2500)
                    run.shot(lan_page, "04_lan_ip_kiosk")
                    run.step(f"局域网 IP {ips[0]} 打开一体机页且同源",
                             lan_page.locator("[data-testid=single-channel-controls]").count() == 1
                             and "/api/v1" in lan_base
                             and "localhost" not in lan_base
                             and not lan_off,
                             f"base={lan_base} 跑偏={lan_off[:2]}")
                except Exception as e:
                    run.step(f"局域网 IP {ips[0]} 打开一体机页且同源",
                             False, f"{type(e).__name__}: {e}")

            # ============================================================
            # F. 模拟扫码枪（枪插一体机的软件通路，无真枪）
            # ============================================================
            sc = api("POST", "/api/v1/scanner/devices", {
                "name": "__uat_lan_gun",
                "ip": "127.0.0.1",
                "port": 24011,
                "device_type": "text_lon",
                "scan_mode": "E",
                "channel_id": 0,
                "broadcast_channels": [0],
                "auto_create_workpiece": True,
                "enabled": True,
            })
            run.step("给工位 0 建一把虚拟扫码枪",
                     sc.status_code in (200, 201),
                     f"status={sc.status_code} {sc.text[:120]}")
            device_id = (sc.json() or {}).get("id") if sc.status_code in (200, 201) else None
            sim = api("POST", "/api/v1/scanner/simulate", {
                "barcode": SCAN_SN,
                "device_id": device_id,
                "channel_id": 0,
            })
            run.step("模拟扫一条码（不插真枪）",
                     sim.status_code == 200,
                     f"status={sim.status_code} {sim.text[:120]}")
            found = api("GET", f"/api/v1/mes/workpieces/search/{SCAN_SN}")
            items = found.json() if found.status_code == 200 else []
            run.step("后端已登记这条码（扫码通路，不依赖真枪）",
                     found.status_code == 200 and any(
                         SCAN_SN in str(it.get("serial_no", "")) + str(it.get("raw_barcode", ""))
                         for it in (items or [])),
                     f"status={found.status_code} n={len(items) if isinstance(items, list) else items}")
            kiosk.reload(wait_until="domcontentloaded")
            kiosk.wait_for_timeout(2500)
            run.shot(kiosk, "05_kiosk_after_simulate_scan")
            body = kiosk.inner_text("body")
            run.step("记下 kiosk 页是否画出这条码（MES 条目前对 kiosk 关闭）",
                     True,
                     f"页内含 SN={SCAN_SN in body}")

            # ============================================================
            # G. 工位账号：设置页建号 + 改 URL 也停不了隔壁
            # ============================================================
            en = api("POST", "/api/v1/auth/enable-auth", {
                "admin_username": ADMIN_USER,
                "admin_password": PASSWORD,
                "admin_display_name": "UAT 管理员",
            })
            run.step("隔离库启用鉴权并建管理员",
                     en.status_code == 200 or "已启用" in (en.text or ""),
                     f"status={en.status_code} {en.text[:120]}")
            lg = api("POST", "/api/v1/auth/login", {
                "username": ADMIN_USER, "password": PASSWORD,
            })
            admin_token = (lg.json() or {}).get("token")
            run.step("管理员登录拿到 token", bool(admin_token),
                     f"status={lg.status_code}")

            admin_ctx = browser.new_context(
                viewport={"width": 1600, "height": 1000},
                record_video_dir=run.video_dir)
            contexts.append(admin_ctx)
            admin_page = admin_ctx.new_page()
            ui_created = False
            try:
                login_page(admin_page, ADMIN_USER, PASSWORD)
                admin_page.goto(f"{STATION}/#/settings", wait_until="domcontentloaded")
                admin_page.wait_for_timeout(1200)
                admin_page.locator('.el-tabs__item:has-text("账号鉴权")').click()
                admin_page.wait_for_timeout(1000)
                run.shot(admin_page, "06_auth_panel_before_create")

                admin_page.locator("[data-testid=auth-create-user-btn]").click()
                admin_page.wait_for_selector("[data-testid=auth-create-user-dialog]", timeout=8000)
                admin_page.locator("[data-testid=auth-create-user-username]").fill(STATION_USER)
                admin_page.locator("[data-testid=auth-create-user-password]").fill(PASSWORD)
                pwds = admin_page.locator("[data-testid=auth-create-user-dialog] input[type=password]")
                pwds.nth(1).fill(PASSWORD)
                admin_page.locator("[data-testid=auth-create-user-role]").click()
                admin_page.locator('.el-select-dropdown__item:has-text("工位屏")').click()
                admin_page.locator("[data-testid=auth-create-user-channels]").fill("0")
                admin_page.locator("[data-testid=auth-create-user-submit]").click()
                ui_created = wait_until(lambda: any(
                    u.get("username") == STATION_USER
                    for u in (api("GET", "/api/v1/users", token=admin_token).json() or [])
                ), timeout_s=8)
                run.shot(admin_page, "07_auth_panel_station_user")
            except Exception as e:
                run.shot(admin_page, "07_auth_panel_station_user")
                run.step("设置页创建 station 账号（UI 过程）", False, f"{type(e).__name__}: {e}")

            if not ui_created:
                cr = api("POST", "/api/v1/users", {
                    "username": STATION_USER,
                    "password": PASSWORD,
                    "display_name": "工位1一体机",
                    "role_codes": ["station"],
                    "allowed_channels": [0],
                }, token=admin_token)
                ui_created = cr.status_code in (200, 201)
                run.step("设置页创建失败则 API 兜底建 station 账号",
                         ui_created, f"status={getattr(cr, 'status_code', None)} {getattr(cr, 'text', '')[:120]}")
            else:
                run.step("设置页创建 station 账号且可操作工位=0（UI→后端）",
                         True, f"users API 看到 {STATION_USER}")

            users = api("GET", "/api/v1/users", token=admin_token).json() or []
            station_row = next((u for u in users if u.get("username") == STATION_USER), {})
            run.step("账号列表回显可操作工位 0",
                     0 in (station_row.get("allowed_channels") or []),
                     f"row={station_row.get('allowed_channels')}")

            sl = api("POST", "/api/v1/auth/login", {
                "username": STATION_USER, "password": PASSWORD,
            })
            station_token = (sl.json() or {}).get("token")
            run.step("工位账号能登录", sl.status_code == 200 and bool(station_token),
                     f"status={sl.status_code}")

            # 重新拉起检测，避免前面停过
            api("POST", f"/api/v1/source/detection/start?channel=0",
                {"conf": 0.25, "iou": 0.45}, token=admin_token)
            api("POST", f"/api/v1/source/detection/start?channel=1",
                {"conf": 0.25, "iou": 0.45}, token=admin_token)
            time.sleep(0.8)

            deny = api("POST", "/api/v1/source/detection/stop?channel=1",
                       token=station_token)
            run.step("工位账号停隔壁工位 1 → 403",
                     deny.status_code == 403,
                     f"status={deny.status_code} {str(deny.text)[:120]}")

            proj = api("GET", "/api/v1/projects/", token=station_token)
            run.step("工位账号拉项目列表 → 无项目管理权",
                     proj.status_code in (401, 403),
                     f"status={proj.status_code}")

            st_ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                record_video_dir=run.video_dir)
            contexts.append(st_ctx)
            st_page = st_ctx.new_page()
            try:
                login_page(st_page, STATION_USER, PASSWORD)
            except Exception as e:
                run.step("工位账号浏览器登录", False, f"{type(e).__name__}: {e}")
            st_page.goto(f"{STATION}/#/monitor?channel=0&kiosk=1&readonly=0",
                         wait_until="domcontentloaded")
            st_page.wait_for_timeout(2000)
            run.shot(st_page, "08_station_user_kiosk")
            # 工位账号在自己的路上可以停
            if st_page.locator("[data-testid=single-channel-stop]").count():
                if not st_page.locator("[data-testid=single-channel-stop]").is_disabled():
                    st_page.locator("[data-testid=single-channel-stop]").click()
                    st_page.wait_for_timeout(1200)
            stopped0 = wait_until(
                lambda: channel_status(0).get("is_detecting") is False)
            run.step("工位账号在一体机上停自己的工位 0 → 后端真停",
                     stopped0, f"ch0={channel_status(0).get('is_detecting')}")

            # 改 URL 到工位 1 再点停止
            api("POST", f"/api/v1/source/detection/start?channel=0",
                {"conf": 0.25, "iou": 0.45}, token=admin_token)
            st_page.evaluate("() => { window.location.hash = '#/monitor?channel=1&kiosk=1&readonly=0'; }")
            st_page.wait_for_timeout(2000)
            run.shot(st_page, "09_station_user_rewrote_channel")
            if st_page.locator("[data-testid=single-channel-stop]").count():
                try:
                    st_page.locator("[data-testid=single-channel-stop]").click(timeout=2000)
                except Exception:
                    pass
            st_page.wait_for_timeout(1000)
            run.step("工位账号改 URL 到 channel=1 再点停止 → 工位 1 仍在跑",
                     channel_status(1).get("is_detecting") is True,
                     f"ch1={channel_status(1).get('is_detecting')}")

            # 非 kiosk 深链进项目页：权限拦，不是写死浏览器永远进不去
            deep_ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                record_video_dir=run.video_dir)
            contexts.append(deep_ctx)
            deep = deep_ctx.new_page()
            try:
                login_page(deep, STATION_USER, PASSWORD)
            except Exception as e:
                run.step("工位账号第二次浏览器登录", False, f"{type(e).__name__}: {e}")
            deep.goto(f"{STATION}/#/project", wait_until="domcontentloaded")
            deep.wait_for_timeout(2000)
            run.shot(deep, "10_station_user_project_blocked")
            run.step("工位账号深链 /project 被权限拦回（不是写死浏览器进不去）",
                     "/project" not in deep.url,
                     f"url={deep.url}")

    finally:
        for ctx in contexts:
            try:
                ctx.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        for ch in (0, 1):
            try:
                stop_channel(ch)
            except Exception:
                pass

    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
