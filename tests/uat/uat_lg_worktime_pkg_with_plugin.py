"""打包件安装回归: lg-worktime 从 .tjvplugin 解包安装目录加载后, 插件功能应全在.

headless 跑 (多 agent 并行, 不允许抢显示器/鼠标)。检查点:
  1. 后端: active manifest = 1.5.2, dashboard API / settings API / device-info 正常
  2. 后端: 18 个导出字段进中央仓库字段树 (plugin 分组)
  3. 前端 /monitor: lgwt shell + 看板容器渲染 (KPI/图表节点在)
  4. 前端 /project /settings: showcase iframe 接管
  5. console 无插件加载报错
"""
import json
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

FE = "http://localhost:6004"
BE = "http://localhost:8004"

errors = []


def check(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        errors.append(name)


def api(path):
    with urllib.request.urlopen(BE + path, timeout=8) as r:
        return json.loads(r.read().decode() or "null")


# ---- 后端面 ----
m = api("/api/v1/plugins/active/manifest")
check("active manifest 1.5.2", m.get("plugin_version") == "1.5.2"
      and m.get("customer_code") == "lg-worktime", str(m.get("plugin_version")))
check("main_version_min=3.46.0", m.get("main_version_min") == "3.46.0")

st = api("/api/v1/plugins/lg-worktime/status")
check("runtime loaded/health ok",
      st["plugin"]["runtime_status"] == "loaded" and st["plugin"]["health"] == "ok",
      f"{st['plugin']['runtime_status']}/{st['plugin']['health']}")
check("install_path 指向打包安装目录",
      st["plugin"].get("install_path", "").endswith("plugins/lg-worktime")
      if "install_path" in st["plugin"] else True)

summary = api("/api/v1/plugins/lg-worktime/dashboard/summary")
check("dashboard/summary 可用", isinstance(summary, dict) and "kpi" in json.dumps(summary)[:2000] or summary, str(summary)[:80])

settings = api("/api/v1/plugins/lg-worktime/dashboard/settings")
check("dashboard/settings 可用", isinstance(settings, dict) and settings, str(settings)[:80])

dev = api("/api/v1/plugins/lg-worktime/dashboard/device-info")
check("device-info 可用", isinstance(dev, dict) and dev.get("uptime_seconds") is not None, str(dev)[:80])

tree = api("/api/v1/export/fields")
pg = [g for g in tree["groups"] if g["group"] == "plugin"]
check("导出字段树含插件分组18字段", pg and pg[0]["count"] == 18,
      f"count={pg[0]['count'] if pg else 0}")

# ---- 前端面 ----
console_errs = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg2 = b.new_page(viewport={"width": 1680, "height": 1000})
    pg2.on("console", lambda msg: console_errs.append(msg.text) if msg.type == "error" else None)

    pg2.goto(FE + "/#/monitor", wait_until="domcontentloaded")
    pg2.wait_for_timeout(9000)  # 等后端探活 + 插件 ESM 异步加载
    if "/monitor" not in pg2.url:
        pg2.evaluate("window.location.hash = '#/monitor'")
        pg2.wait_for_timeout(3000)

    check("monitor 挂上插件 shell", pg2.locator(".lgwt-shell").count() > 0,
          f"shell={pg2.locator('.lgwt-shell').count()}")
    check("看板容器渲染", pg2.locator(".lgwt-dash, .lgwt-kpi, [class*=lgwt]").count() > 3,
          f"lgwt节点={pg2.locator('[class*=lgwt]').count()}")
    pg2.screenshot(path="evidence/lgwt_pkg_monitor.png")

    for route in ["project", "settings"]:
        pg2.evaluate(f"window.location.hash = '#/{route}'")
        pg2.wait_for_timeout(3500)
        n_iframe = pg2.locator("iframe").count()
        check(f"/{route} showcase iframe 接管", n_iframe >= 1, f"iframe={n_iframe}")
    pg2.screenshot(path="evidence/lgwt_pkg_project.png")

    b.close()

load_errs = [e for e in console_errs
             if re.search(r"lgwt|plugin|插件", e, re.I) and "favicon" not in e]
check("console 无插件加载报错", not load_errs, "; ".join(load_errs[:3]))

print("\n结果:", "全部通过" if not errors else f"失败 {len(errors)} 项: {errors}")
sys.exit(1 if errors else 0)
