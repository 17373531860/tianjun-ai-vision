"""可见浏览器诊断/验证 — 操作员离开超时(秒) 保存后是否回退。

根因: 同屏两个"保存配置"按钮——项目页右上角 el-button(存项目) + 插件表单底部
      (存插件配置)。客户改完超时点了项目那个 -> 插件配置没提交 -> 切回显示 600。
修法: 插件按钮改名"保存传感器清洁配置", 不再与项目保存重名。

本脚本两场景 (每次"重新进入"都新开 page = 干净 remount, 等价切走切回):
  A. 改 5 -> 点【项目页右上角】保存配置(el-button) -> 重新进入 -> 应回退到旧值 (复现 bug)
  B. 改 5 -> 点【插件】保存传感器清洁配置 -> 重新进入 -> 应仍是 5 (修复生效/持久化)

跑法 (后端 8001 + 前端 6001 已起好):
  DISPLAY=:0 python tests/uat/uat_20260627_absent_timeout_persist.py
"""
import os
import sys
import time
import requests
from playwright.sync_api import sync_playwright

API = os.environ.get("UAT_API", "http://127.0.0.1:8001")
WEB = os.environ.get("UAT_WEB", "http://127.0.0.1:6001")
CFG = f"{API}/api/v1/plugins/sensor-clean/swab/config"
SHOT = "/tmp/uat_absent_shots"
os.makedirs(SHOT, exist_ok=True)

PROJ_NAME = "传感器清洁-视角1"
TIMEOUT_KW = "操作员离开 · 超时"

results = []


def rec(label, ok, detail=""):
    results.append({"ok": bool(ok)})
    print(f"[{'OK' if ok else '!!'}] {len(results):02d}. {label}  {detail}", flush=True)


def db_set_timeout(val):
    requests.post(CFG, json={"operator_absent_timeout_sec": val,
                             "operator_absent_enabled": False}, timeout=8)


def db_get_timeout():
    return requests.get(CFG, timeout=8).json().get("operator_absent_timeout_sec")


def open_tab(ctx, tag):
    """新开 page -> 项目页 -> 选项目 -> 点插件 Tab -> 返回 (page, 超时输入框 locator)。"""
    page = ctx.new_page()
    page.on("console", lambda m: (m.type == "error") and print("   [err]", m.text, flush=True))
    page.goto(f"{WEB}/#/project", wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)
    # 用搜索框过滤, 再点【左侧列表卡片】(x<260, 排除顶部下拉框同名文字)
    try:
        page.get_by_placeholder("搜索项目").fill(PROJ_NAME, timeout=5000)
        time.sleep(2)
    except Exception:
        pass
    page.keyboard.press("Escape")  # 关掉可能误开的顶部下拉
    time.sleep(0.5)
    # 只点左侧列表卡片: x<260 且 y>120 (排除顶部 y≈16 的下拉框)
    cands = page.get_by_text(PROJ_NAME, exact=False)
    for i in range(cands.count()):
        el = cands.nth(i)
        try:
            box = el.bounding_box()
            if box and box["x"] < 260 and box["y"] > 120:
                el.click(timeout=5000)
                break
        except Exception:
            continue
    time.sleep(2.5)
    try:
        page.get_by_text("传感器清洁配置", exact=False).first.click(timeout=6000)
        time.sleep(2.5)
    except Exception:
        pass
    loc = page.locator(f"xpath=//label[contains(text(),'{TIMEOUT_KW}')]/following-sibling::input")
    try:
        loc.wait_for(state="visible", timeout=8000)
    except Exception:
        page.screenshot(path=f"{SHOT}/{tag}_NOTAB.png")
        return page, None
    return page, loc


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})

        # ============ 合并后: 点【项目页保存配置】一并存插件, 插件无自带按钮 ============
        db_set_timeout(600)
        page, loc = open_tab(ctx, "S1")
        rec("进入配置 Tab 读到超时框", loc is not None, f"初值={loc.input_value() if loc else 'NA'}")
        if loc is None:
            return finish(browser)

        # 1) 插件已无自带保存按钮
        plugin_btn = page.locator("button:not([class*='el-button']):has-text('保存传感器清洁配置')")
        old_btn = page.locator("button:not([class*='el-button']):has-text('保存配置')")
        rec("插件自带保存按钮已删除", plugin_btn.count() == 0 and old_btn.count() == 0,
            f"改名按钮={plugin_btn.count()} 裸保存配置={old_btn.count()}")

        # 2) 改 600 -> 5, 点【项目页右上角 el-button 保存配置】
        loc.click(); loc.fill(""); loc.fill("5"); time.sleep(0.4)
        proj_save = page.locator("button.el-button:has-text('保存配置')")
        proj_save.first.click(timeout=8000)
        time.sleep(3)
        page.screenshot(path=f"{SHOT}/S_proj_saved.png")
        # 保存后 DB 应已是 5 (插件 onSave 随项目保存一并触发)
        rec("点项目保存按钮后 DB 立即变 5 (合并保存生效)", db_get_timeout() == 5,
            f"DB={db_get_timeout()}")
        page.close()

        # 3) 重新进入 -> 仍为 5 (持久化)
        page2, loc2 = open_tab(ctx, "S2")
        v = loc2.input_value() if loc2 else None
        rec("切走切回后超时值仍为 5 (持久)", v == "5", f"重进读到={v}; DB={db_get_timeout()}")
        page2.close()

        return finish(browser)


def finish(browser):
    try:
        browser.close()
    except Exception:
        pass
    ok = sum(1 for r in results if r["ok"])
    failed = len(results) - ok
    print(f"\n===== 汇总: {ok}/{len(results)} 通过, failed={failed} =====", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
