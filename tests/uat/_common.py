# -*- coding: utf-8 -*-
"""UAT 可见浏览器脚本共用库（v3.31 抽取）。

历史上 90+ 个 tests/uat/uat_*.py 各自复制粘贴同一套样板：
浏览器启动参数 / step 记录器 / 安全截图 / 三件套证据路径 / 控制台噪声过滤 / 登录。
本库把这些收口成一份；**新 UAT 脚本一律 import 本库，存量脚本不回改**。

典型用法::

    from _common import UatRun, launch_browser, login, filter_console_errors
    from playwright.sync_api import sync_playwright

    run = UatRun("chuannan_alarm")          # 证据落 tests/uat/evidence_<日期>_chuannan_alarm/
    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto("http://localhost:6001/#/monitor", wait_until="commit")
        run.shot(page, "01_monitor")
        run.step("看板已渲染", "运行正常" in page.evaluate("document.body.innerText"))
        ...
        real = filter_console_errors(console_errs)
        run.step("控制台无前端逻辑报错", not real, f"真报错={real[:3]}")
        ctx.close(); browser.close()
    raise SystemExit(run.finish())          # 写 run.json + 打印汇总, 有失败步骤则退出码 1

约定（对齐 run-tests skill 第 7 节可见浏览器 UAT 模板）:
- headless=False 是金标准, CI 里不要跑本目录脚本（CI 回归走 tests/e2e_browser/）
- 三件套 = 视频(webm) + 截图(png) + run.json, 全部落在同一个 evidence 目录
- evidence_*/ 目录已被 .gitignore, 不会污染仓库
"""
from __future__ import annotations

import json
import os
import time
from datetime import date

# ==================== 证据目录与 step 记录 ====================

UAT_DIR = os.path.dirname(os.path.abspath(__file__))

# 视频流/探活类噪声, 不算前端逻辑报错（与历史脚本 INFRA 清单一致）
CONSOLE_NOISE = (
    "video_feed", "/snapshot", "ERR_CONNECTION_REFUSED", "favicon",
    "net::ERR", "ERR_ABORTED", "404 (Not Found)",
)


class UatRun:
    """一次 UAT 运行：step 记录 + 截图 + 三件套证据目录 + run.json 汇总。"""

    def __init__(self, name: str, evidence_dir: str | None = None):
        self.name = name
        self.t0 = time.time()
        self.steps: list[dict] = []
        if evidence_dir is None:
            evidence_dir = os.path.join(
                UAT_DIR, f"evidence_{date.today().strftime('%Y%m%d')}_{name}")
        self.dir = evidence_dir
        self.video_dir = self.dir  # Playwright record_video_dir 直接用证据目录
        os.makedirs(self.dir, exist_ok=True)

    # -------- step 记录器 --------
    def step(self, label: str, ok, detail: str = "") -> bool:
        ok = bool(ok)
        self.steps.append({"idx": len(self.steps) + 1, "label": label,
                           "ok": ok, "detail": str(detail)})
        print(f"[{'OK' if ok else '!!'}] {len(self.steps):02d}. {label}  {detail}",
              flush=True)
        return ok

    # -------- 安全截图（瞬时动画/超时不炸整个脚本） --------
    def shot(self, page, name: str) -> str:
        path = os.path.join(self.dir, name if name.endswith(".png") else f"{name}.png")
        try:
            page.screenshot(path=path, animations="disabled",
                            caret="initial", timeout=8000)
            print(f">>> shot ok: {path}", flush=True)
        except Exception as e:  # noqa: BLE001 — 截图失败不应中断 UAT 主流程
            print(f">>> shot FAILED(non-fatal): {type(e).__name__}: {e}", flush=True)
        return path

    # -------- 收尾：写 run.json + 打印汇总, 返回退出码 --------
    def finish(self) -> int:
        failed = [s for s in self.steps if not s["ok"]]
        summary = {
            "name": self.name,
            "elapsed_s": round(time.time() - self.t0, 1),
            "total": len(self.steps),
            "passed": len(self.steps) - len(failed),
            "failed": len(failed),
            "steps": self.steps,
        }
        run_json = os.path.join(self.dir, "run.json")
        with open(run_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print("=" * 60, flush=True)
        print(f"===== 汇总: {summary['passed']}/{summary['total']} 通过, "
              f"失败 {summary['failed']} =====", flush=True)
        print(f"证据: {self.dir}/ (截图 + 视频 + run.json)", flush=True)
        return 1 if failed else 0


# ==================== 浏览器启动 ====================

def launch_browser(p, *, headless: bool = False, slow_mo: int = 120,
                   viewport: tuple[int, int] = (1600, 1000),
                   record_video_dir: str | None = None,
                   default_timeout_ms: int = 15000):
    """按项目 UAT 金标准启动 chromium。

    返回 (browser, context, page, console_errors)：
    console_errors 是 [(text, url), ...] 实时累积的控制台 error 列表,
    收尾用 filter_console_errors() 剔除视频流噪声后再断言。
    """
    browser = p.chromium.launch(
        headless=headless, slow_mo=slow_mo,
        args=["--disable-blink-features=AutomationControlled"])
    ctx_kwargs: dict = {
        "viewport": {"width": viewport[0], "height": viewport[1]},
        "ignore_https_errors": True,
    }
    if record_video_dir:
        os.makedirs(record_video_dir, exist_ok=True)
        ctx_kwargs["record_video_dir"] = record_video_dir
        ctx_kwargs["record_video_size"] = {"width": viewport[0], "height": viewport[1]}
    ctx = browser.new_context(**ctx_kwargs)
    page = ctx.new_page()
    page.set_default_timeout(default_timeout_ms)

    console_errors: list[tuple[str, str]] = []
    page.on("console", lambda m: console_errors.append(
        (m.text, (m.location or {}).get("url", ""))) if m.type == "error" else None)
    return browser, ctx, page, console_errors


def filter_console_errors(console_errors, extra_noise: tuple = ()) -> list:
    """剔除视频流/探活等基础设施噪声, 只留真正的前端逻辑报错。"""
    noise = CONSOLE_NOISE + tuple(extra_noise)
    return [(t, u) for (t, u) in console_errors
            if not any(k in (t + " " + u) for k in noise)]


# ==================== 登录（v3.10.0 账号鉴权启用时用） ====================

def login(page, front: str, username: str, password: str,
          wait_after_s: float = 2.5) -> bool:
    """在 /#/login 页填账号密码点登录, 返回是否成功跳离登录页。

    选择器对齐 v3.10.0 登录页（Element Plus）:
    用户名 placeholder 含「用户名」, 密码 input[type=password],
    登录按钮 el-button--primary.el-button--large。
    """
    if "/login" not in page.url:
        page.goto(f"{front}/#/login", wait_until="domcontentloaded", timeout=15000)
        time.sleep(1.0)
    page.locator('input[placeholder*="用户名"]').first.fill(username)
    page.locator('input[type="password"]').first.fill(password)
    page.locator(
        'button.el-button--primary.el-button--large:has-text("登录")').first.click()
    time.sleep(wait_after_s)
    return "/login" not in page.url
