"""无插件状态回归: 停用 lg-worktime 后, 主程序前端应完全回到原生形态.

headless 跑 (多 agent 并行, 不允许抢显示器/鼠标)。检查点:
  1. /monitor 不出现插件 shell (.lgwt-shell / .lgwt-app), 原生 Monitor 控件在
  2. /project /model /data /settings 均为原生页面 (无 showcase iframe)
  3. console 无 lgwt/plugin 相关报错
  4. /plugins/active/manifest 为空
"""
import json
import re
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

FE = "http://localhost:6004"
BE = "http://localhost:8004"
SHOT = "evidence/lgwt_pkg_no_plugin.png"

errors = []


def check(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        errors.append(name)


def api(path):
    with urllib.request.urlopen(BE + path, timeout=5) as r:
        return json.loads(r.read().decode() or "null")


manifest = api("/api/v1/plugins/active/manifest")
check("后端无 active 插件", manifest in (None, {}), str(manifest)[:80])

console_errs = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1680, "height": 1000})
    pg.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)

    pg.goto(FE + "/#/monitor", wait_until="domcontentloaded")
    pg.wait_for_timeout(6000)  # 等插件 bootstrap 窗口过掉 (应该什么都不装)

    # 若宿主"恢复上次路由"跳走了, 拉回 monitor
    if "/monitor" not in pg.url:
        pg.evaluate("window.location.hash = '#/monitor'")
        pg.wait_for_timeout(2500)

    check("monitor 无插件 shell", pg.locator(".lgwt-shell").count() == 0
          and pg.evaluate("document.body.classList.contains('lgwt-app')") is False)
    # 原生 Monitor 特征: 顶部导航 + 页面主体存在
    check("原生导航栏在", pg.locator("nav, .el-menu, header").count() > 0,
          f"count={pg.locator('nav, .el-menu, header').count()}")
    body_txt = pg.inner_text("body")
    check("monitor 页面有原生内容", len(body_txt.strip()) > 50, f"len={len(body_txt)}")
    check("monitor 无 showcase iframe", pg.locator("iframe").count() == 0)
    pg.screenshot(path=SHOT)

    for route in ["project", "model", "data", "settings"]:
        pg.evaluate(f"window.location.hash = '#/{route}'")
        pg.wait_for_timeout(2000)
        n_iframe = pg.locator("iframe").count()
        n_shell = pg.locator(".lgwt-shell").count()
        check(f"/{route} 原生 (无iframe/无shell)", n_iframe == 0 and n_shell == 0,
              f"iframe={n_iframe} shell={n_shell}")

    b.close()

plugin_errs = [e for e in console_errs if re.search(r"lgwt|plugin|插件", e, re.I)]
check("console 无插件相关报错", not plugin_errs, "; ".join(plugin_errs[:3]))
noisy = [e for e in console_errs if "favicon" not in e][:5]
if noisy:
    print("[INFO] 其它 console error (供参考):", "; ".join(noisy))

print("\n结果:", "全部通过" if not errors else f"失败 {len(errors)} 项: {errors}")
sys.exit(1 if errors else 0)
