# -*- coding: utf-8 -*-
"""UAT: 授权激活后"后端后台启动"新交互（v3.47 客户反馈: 授权后一直转圈）。

客户现场叙事:
1. 操作员在激活页导入授权文件, 主进程验签通过后**立即**返回结果 (老版本会把
   后端冷启动整个串在这次 IPC 里, 按钮转圈可达几分钟, 失败还永远不解开)。
2. 前端立刻显示绿色"激活成功, 系统启动中 (首次可能需要几分钟)"等待态,
   按钮进入 loading + disabled。
3. 后端启动成功 → 主进程发 license-activated → 前端自动跳主页;
   启动失败 → 主进程发 license-backend-start-failed → 前端停掉等待态,
   显示红色错误"请重启软件", 不再无限转圈。

激活页只在 Electron 壳内出现 (非 Electron 直接跳主页), 本脚本用
add_init_script 注入 mock electronAPI 模拟主进程的**新 IPC 契约**,
在真浏览器里验证前端三个状态的渲染与转换。Electron 主进程侧
(main.js import-license 后台化) 由 node 逻辑测试 + 人工验证覆盖。

跑法:
    cd tests/uat && python uat_20260807_activation_backend_async_start.py
    (需前端 dev 服务器, 默认 http://localhost:6005, 可用 UAT_FRONT 覆盖)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

FRONT = os.environ.get("UAT_FRONT", "http://localhost:6005")

# 模拟主进程新契约的 electronAPI mock:
# - importLicense 立即 resolve {valid:true} (新行为: 不再等后端冷启动)
# - 事件回调存到 window.__cb 供测试脚本手动触发
MOCK_ELECTRON_API = """
window.__cb = { activated: null, startFailed: null };
window.__licensed = false;   // 导入成功后置 true, 否则路由守卫会把跳主页弹回激活页
window.electronAPI = {
  isElectron: true,
  getAppInfo: async () => ({ version: '3.47.0-uat' }),
  getLicenseStatus: async () => ({ valid: window.__licensed, machineId: 'TJ-UAT0-MOCK-1234' }),
  importLicense: async () => {
    await new Promise(r => setTimeout(r, 300));   // 模拟文件选择+验签的短耗时
    window.__licensed = true;
    return { valid: true, message: 'ok' };
  },
  onLicenseActivated: (cb) => { window.__cb.activated = cb; },
  onLicenseBackendStartFailed: (cb) => { window.__cb.startFailed = cb; },
};
"""


def main() -> int:
    run = UatRun("activation_backend_async_start")
    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(
            p, headless=os.environ.get("UAT_HEADLESS", "0") == "1",
            record_video_dir=run.video_dir)
        ctx.add_init_script(MOCK_ELECTRON_API)
        # add_init_script 只对之后新建的 page 生效, 重新开一页
        page.close()
        page = ctx.new_page()
        page.set_default_timeout(15000)
        page.on("console", lambda m: console_errs.append(
            (m.text, (m.location or {}).get("url", ""))) if m.type == "error" else None)

        # ---- 阶段 1: 激活页渲染 (出厂默认态: 全新 context 无 localStorage) ----
        page.goto(f"{FRONT}/#/activation")
        page.wait_for_load_state("networkidle")
        time.sleep(1.0)
        run.shot(page, "01_activation_initial")
        body = page.evaluate("document.body.innerText")
        run.step("激活页渲染: 标题在", "Software Activation" in body)
        run.step("机器码显示", "TJ-UAT0-MOCK-1234" in body)
        run.step("导入按钮可点",
                 page.locator("button:has-text('Import License File')").is_enabled())

        # ---- 阶段 2: 点导入 → 立即进入"启动中"等待态 ----
        page.locator("button:has-text('Import License File')").click()
        time.sleep(1.2)   # importLicense mock 300ms + 渲染
        run.shot(page, "02_starting_state")
        body = page.evaluate("document.body.innerText")
        run.step("绿色启动中提示出现",
                 "Activation successful" in body and "starting" in body)
        run.step("按钮进入等待态 (disabled)",
                 page.locator("button:has-text('Import License File')").is_disabled())
        run.step("仍在激活页 (未跳主页)", "#/activation" in page.url)

        # v3.43 规矩: 随时间变化的状态要延迟后再断言一次 (模拟后端冷启动几十秒的等待期)
        time.sleep(4.0)
        run.shot(page, "03_starting_state_persists")
        body = page.evaluate("document.body.innerText")
        run.step("4 秒后等待态仍在 (不闪退不复位)",
                 "starting" in body and "#/activation" in page.url)

        # ---- 阶段 3: 主进程报"后端启动失败" → 显示错误, 解除转圈 ----
        page.evaluate("window.__cb.startFailed && window.__cb.startFailed("
                      "{ message: 'Backend startup timeout' })")
        time.sleep(1.0)
        run.shot(page, "04_backend_start_failed")
        body = page.evaluate("document.body.innerText")
        run.step("失败提示出现 (请重启软件)",
                 "backend failed to start" in body and "restart" in body)
        run.step("失败原因显示", "Backend startup timeout" in body)
        run.step("按钮恢复可点 (不再无限转圈)",
                 page.locator("button:has-text('Import License File')").is_enabled())

        # ---- 阶段 4: 重新导入 → 后端启动成功 → license-activated 跳主页 ----
        page.locator("button:has-text('Import License File')").click()
        time.sleep(1.2)
        page.evaluate("window.__cb.activated && window.__cb.activated()")
        time.sleep(2.0)
        run.shot(page, "05_activated_navigated")
        run.step("license-activated 后跳离激活页", "#/activation" not in page.url)

        real = filter_console_errors(console_errs, extra_noise=("Network Error", "AxiosError"))
        run.step("控制台无前端逻辑报错", not real, f"真报错={real[:3]}")

        ctx.close()
        browser.close()
    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
