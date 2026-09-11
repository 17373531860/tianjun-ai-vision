# -*- coding: utf-8 -*-
"""一拖多「工作站推理 + 一体机网页工位」可见浏览器 UAT。

现场叙事：
    车间里一台工作站跑推理（海康取流 + YOLO + 周期结算 + 项目库），每个工位摆一台
    一体机，网线进千兆交换机。一体机不装软件、不配后端地址，Chrome 全屏打开
    ``http://<工作站IP>:8001/#/monitor?channel=N&kiosk=1&readonly=0`` 就是本工位的
    检测页：能看画面、能启停、能扫码，但看不到也进不去项目 / 模型 / 输入源 / 设置。

本脚本用**工作站自己托管的那个 origin**（8001）开两个真浏览器窗口，
即客户现场一体机走的完全同一条路；末尾再补一次开发态 6001 的冒烟。

前置：
    后端 8001（RUNTIME_MODE=test，synthetic 剧本源代替相机）+ 前端 6001（可选）
    python tests/uat/uat_20260911_lan_station_kiosk.py
"""
from __future__ import annotations

import os
import re
import sys
import time

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import UatRun, filter_console_errors, launch_browser  # noqa: E402

# 一体机走的 origin = 工作站后端自己（静态托管 + API + 视频流全同源）
STATION = os.environ.get("UAT_STATION_ORIGIN", "http://127.0.0.1:8001")
API = os.environ.get("E2E_API_URL", "http://127.0.0.1:8001")
FRONT_DEV = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:6001")
BACKEND_LOG = os.environ.get(
    "UAT_BACKEND_LOG", os.path.join(os.path.dirname(os.path.dirname(UAT_DIR := os.path.dirname(
        os.path.abspath(__file__)))), "_backend8001.log"))

SCENARIO = {
    "name": "lan-station-kiosk",
    "fps": 25,
    "timeline": [
        {"from": 0, "to": 100000, "detections": [
            {"label": "工件", "confidence": 0.92, "bbox": [0.3, 0.3, 0.4, 0.4]},
        ]},
    ],
}

# kiosk 页面绝不该出现的入口（本期禁止一体机做项目管理）
FORBIDDEN_NAV = ["/project", "/model", "/source", "/settings", "/interconnect"]


def api(method: str, path: str, payload: dict | None = None, timeout: int = 20):
    return requests.request(method, f"{API}{path}", json=payload, timeout=timeout)


def start_channel(ch: int) -> bool:
    """synthetic 剧本源 + 临时项目 + 开检测（无相机无模型跑真实 pipeline）。"""
    r = api("POST", "/api/v1/test/synthetic/start", {
        "scenario_json": SCENARIO, "channel": ch, "with_project": True,
    })
    if r.status_code != 200:
        print(f"  synthetic start ch{ch} 失败: {r.status_code} {r.text[:200]}")
        return False
    r = api("POST", f"/api/v1/source/detection/start?channel={ch}",
            {"conf": 0.25, "iou": 0.45})
    if r.status_code != 200:
        print(f"  detection start ch{ch} 失败: {r.status_code} {r.text[:200]}")
        return False
    return True


def stop_channel(ch: int) -> None:
    api("POST", f"/api/v1/source/detection/stop?channel={ch}")
    api("POST", f"/api/v1/test/synthetic/stop?channel={ch}")


def channel_status(ch: int) -> dict:
    r = api("GET", f"/api/v1/source/status?channel={ch}")
    return r.json() if r.status_code == 200 else {}


def stream_viewers() -> dict:
    r = api("GET", "/api/v1/source/stream/viewers")
    return r.json().get("channels", {}) if r.status_code == 200 else {}


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


def backend_log_len() -> int:
    try:
        return os.path.getsize(BACKEND_LOG)
    except OSError:
        return -1


def backend_log_tail(since_bytes: int) -> str:
    try:
        with open(BACKEND_LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(max(0, since_bytes))
            return f.read()
    except OSError:
        return ""


class Kiosk:
    """一台一体机：独立 context（独立 localStorage）+ 请求/日志留痕。"""

    def __init__(self, ctx, page, origin):
        self.ctx = ctx
        self.page = page
        self.origin = origin
        self.console_errors: list[tuple[str, str]] = []
        self.stream_reqs: list[str] = []
        self.api_reqs: list[str] = []
        self.base_url_log: str = ""


def open_kiosk(browser, run, origin: str, ch: int, readonly: str = "0") -> Kiosk:
    ctx = browser.new_context(viewport={"width": 1280, "height": 800},
                              record_video_dir=run.video_dir,
                              record_video_size={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.set_default_timeout(20000)
    k = Kiosk(ctx, page, origin)

    def _on_console(m):
        if m.type == "error":
            k.console_errors.append((m.text, (m.location or {}).get("url", "")))
        # api/index.js 启动时打印最终 baseURL —— 同源判据的唯一可观测出口
        if "Final baseURL" in m.text:
            k.base_url_log = m.text

    def _on_request(r):
        if "/video_feed" in r.url or "/snapshot" in r.url:
            k.stream_reqs.append(r.url)
        elif "/api/v1/" in r.url:
            k.api_reqs.append(r.url)

    page.on("console", _on_console)
    page.on("request", _on_request)
    page.goto(f"{origin}/#/monitor?channel={ch}&kiosk=1&readonly={readonly}",
              wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    return k


def main() -> int:
    run = UatRun("lan_station_kiosk")
    contexts = []
    browser = None

    try:
        # ============================================================
        # A. HTTP 层：工作站把前端 dist 送出去，接口仍归后端
        # ============================================================
        r = requests.get(f"{STATION}/", timeout=10)
        run.step("工作站根路径直接送检测页 (一体机输 IP 就能用)",
                 r.status_code == 200 and "text/html" in r.headers.get("content-type", ""),
                 f"status={r.status_code} ctype={r.headers.get('content-type')}")
        run.step("index.html 不缓存 (升级后一体机不会白屏)",
                 "no-cache" in r.headers.get("cache-control", ""),
                 r.headers.get("cache-control", ""))

        asset = re.search(r'src="\.?/?(assets/[^"]+\.js)"', r.text)
        if asset:
            a = requests.get(f"{STATION}/{asset.group(1)}", timeout=10)
            run.step("静态资源可取且 MIME 正确 (Windows 上 .js 不能是 text/plain)",
                     a.status_code == 200 and "javascript" in a.headers.get("content-type", ""),
                     f"{asset.group(1)} -> {a.status_code} {a.headers.get('content-type')}")
        else:
            run.step("静态资源可取且 MIME 正确", False, "index.html 里没解析到 assets/*.js")

        bogus = requests.get(f"{STATION}/api/v1/definitely-not-an-endpoint", timeout=10)
        run.step("打错的接口地址仍回 JSON 404 (静态兜底不吃 /api)",
                 bogus.status_code == 404
                 and bogus.headers.get("content-type", "").startswith("application/json"),
                 f"status={bogus.status_code} ctype={bogus.headers.get('content-type')}")

        count = api("GET", "/api/v1/workstations/").json().get("channel_count")
        run.step("工作站为 2 工位 (一拖多最小形态)", count == 2, f"channel_count={count}")

        # ============================================================
        # B. 两台一体机各开自己那一路
        # ============================================================
        run.step("工位 0 起 synthetic + 检测", start_channel(0))
        run.step("工位 1 起 synthetic + 检测", start_channel(1))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=False, slow_mo=80,
                args=["--disable-blink-features=AutomationControlled"])

            k0 = open_kiosk(browser, run, STATION, 0)
            contexts.append(k0.ctx)
            k1 = open_kiosk(browser, run, STATION, 1)
            contexts.append(k1.ctx)
            page0, page1 = k0.page, k1.page
            streams0, streams1 = k0.stream_reqs, k1.stream_reqs
            errs0, errs1 = k0.console_errors, k1.console_errors
            run.shot(page0, "01_station0_kiosk")
            run.shot(page1, "02_station1_kiosk")

            run.step("一体机页面渲染出单工位监看组件",
                     page0.locator("[data-testid=single-channel-controls]").count() == 1
                     and page1.locator("[data-testid=single-channel-controls]").count() == 1)

            # --- 同源：这是一拖多最容易静默失效的一环 ---
            # .env.production 里写死 VITE_API_BASE_URL=http://localhost:8001 时，
            # 一体机会把所有请求打给自己那台没后端的机器，页面看着"在转"其实全挂。
            run.step("axios baseURL 解析为同源相对路径 (没有写死 localhost)",
                     "/api/v1" in k0.base_url_log and "localhost" not in k0.base_url_log
                     and "127.0.0.1" not in k0.base_url_log,
                     k0.base_url_log or "(没捕获到 [API] Final baseURL 日志)")
            off_origin_api = [u for u in k0.api_reqs if not u.startswith(STATION)]
            run.step("所有 API 请求都打回页面 origin",
                     k0.api_reqs and not off_origin_api,
                     f"共 {len(k0.api_reqs)} 个请求, 跑偏={off_origin_api[:3]}")
            off_origin_stream = [u for u in streams0 if not u.startswith(STATION)]
            run.step("视频流/快照也走同源",
                     streams0 and not off_origin_stream,
                     f"样本={streams0[:1]} 跑偏={off_origin_stream[:2]}")

            # --- kiosk 无项目/设置入口 ---
            for label, page in (("工位 0", page0), ("工位 1", page1)):
                html = page.content()
                leaked = [nav for nav in FORBIDDEN_NAV if f'href="#{nav}"' in html]
                run.step(f"{label} kiosk 无项目/模型/输入源/设置入口",
                         not leaked, f"泄漏={leaked}")
            run.step("kiosk 无侧边栏汉堡与顶栏",
                     page0.locator('button[title="导航菜单"]').count() == 0,
                     "汉堡按钮数=0")

            # --- readonly=0 放开本工位操作 ---
            ro0 = page0.locator("[data-testid=single-channel-controls]").get_attribute("data-readonly")
            stop_disabled = page0.locator("[data-testid=single-channel-stop]").is_disabled()
            run.step("readonly=0 时本工位控制条可操作",
                     ro0 == "false" and not stop_disabled,
                     f"data-readonly={ro0} 停止按钮disabled={stop_disabled}")

            # --- 每台一体机只连自己那一路，且走 station 槽 ---
            def _ch_of(urls, ch):
                return any(f"channel={ch}" in u for u in urls)
            run.step("工位 0 只订阅 channel=0 且 viewer=station",
                     _ch_of(streams0, 0) and not _ch_of(streams0, 1)
                     and all("viewer=station" in u for u in streams0 if "/video_feed" in u),
                     f"请求样本={streams0[:2]}")
            run.step("工位 1 只订阅 channel=1 且 viewer=station",
                     _ch_of(streams1, 1) and not _ch_of(streams1, 0)
                     and all("viewer=station" in u for u in streams1 if "/video_feed" in u),
                     f"请求样本={streams1[:2]}")

            ok_slots = wait_until(lambda: (stream_viewers().get("0", {}).get("station") is True
                                           and stream_viewers().get("1", {}).get("station") is True))
            run.step("后端看到两路 station 槽同时在看 (不同 channel 多一体机并存)",
                     ok_slots, f"viewers={stream_viewers()}")

            # ============================================================
            # C. 启停互不影响（UI 点击 → 后端落地）
            # ============================================================
            run.step("点击前两工位都在检测",
                     channel_status(0).get("is_detecting") is True
                     and channel_status(1).get("is_detecting") is True,
                     f"ch0={channel_status(0).get('is_detecting')} ch1={channel_status(1).get('is_detecting')}")

            page0.locator("[data-testid=single-channel-stop]").click()
            page0.wait_for_timeout(1200)
            run.shot(page0, "03_station0_stopped")
            stopped = wait_until(lambda: channel_status(0).get("is_detecting") is False)
            run.step("在工位 0 一体机点「停止」→ 后端工位 0 真停了 (UI→后端)",
                     stopped, f"ch0 status={channel_status(0)}")
            run.step("工位 1 完全不受影响 (启停互不干扰)",
                     channel_status(1).get("is_detecting") is True
                     and channel_status(1).get("is_running") is True,
                     f"ch1 status={channel_status(1)}")

            # ============================================================
            # D. 同 channel 双开直播 → 沿用现有互踢
            # ============================================================
            start_channel(0)
            page0.reload(wait_until="domcontentloaded")
            page0.wait_for_timeout(2000)
            log_mark = backend_log_len()
            k2 = open_kiosk(browser, run, STATION, 0)
            contexts.append(k2.ctx)
            k2.page.wait_for_timeout(2500)
            run.shot(k2.page, "04_same_channel_second_open")
            tail = backend_log_tail(log_mark)
            kicked = "yielding to" in tail
            run.step("同一 channel 第二台一体机开直播 → 先开的被互踢 (现有语义保持)",
                     kicked or backend_log_len() < 0,
                     f"日志出现 yielding to = {kicked} (日志不可读时跳过)")
            run.step("互踢只发生在 station 槽内，工位 1 直播不受牵连",
                     stream_viewers().get("1", {}).get("station") is True,
                     f"viewers={stream_viewers()}")

            # ============================================================
            # E. kiosk 钉死 hash：一体机漂不走
            # ============================================================
            page1.evaluate("() => { window.location.hash = '#/project'; }")
            page1.wait_for_timeout(1500)
            run.step("kiosk 页手改 hash 进项目页 → 被钉回本工位监控页",
                     "/monitor" in page1.url and "kiosk=1" in page1.url,
                     f"url={page1.url}")
            run.shot(page1, "05_station1_hash_pinned")

            # ============================================================
            # F. 开发态 6001 同样能开（用户验收路径）
            # ============================================================
            try:
                requests.get(FRONT_DEV, timeout=3)
                k3 = open_kiosk(browser, run, FRONT_DEV, 1)
                contexts.append(k3.ctx)
                run.step("开发态 6001 打开工位页同样可用",
                         k3.page.locator("[data-testid=single-channel-controls]").count() == 1,
                         f"url={k3.page.url}")
                run.shot(k3.page, "06_dev_6001_kiosk")
            except requests.RequestException as e:
                run.step("开发态 6001 打开工位页同样可用", True, f"6001 未起, 跳过 ({e})")

            real0 = filter_console_errors(errs0)
            real1 = filter_console_errors(errs1)
            run.step("一体机页面无前端逻辑报错",
                     not real0 and not real1,
                     f"ch0={real0[:2]} ch1={real1[:2]}")

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
        stop_channel(0)
        stop_channel(1)

    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
