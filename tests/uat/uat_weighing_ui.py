"""可见浏览器 UAT: 称重投料模式 Project 配置界面。

覆盖:
- 新建项目时逻辑模式可选「称重投料模式」
- 进入项目后出现「称重配置」页签
- 料别(钢帽/钢脚水泥)默认在; 添加型号; 填标准量
- 保存配置 → GET /api/v1/projects 校验 pipeline_config.weighing 真落库

前端 6001, 后端 8001(hash 路由)。headless=False 真开浏览器, 截图存本目录。
"""
import time
import re
import requests
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
API = "http://localhost:8001/api/v1"
OUT = "tests/uat"
PROJ_NAME = f"UAT称重UI_{int(time.time())}"
results = []


def step(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {extra}" if extra else ""))
    results.append((name, ok))


def shot(page, f):
    page.screenshot(path=f"{OUT}/{f}", full_page=False)
    print(f"  shot -> {OUT}/{f}")


def maybe_login(page):
    try:
        pw = page.locator("input[type=password]")
        if pw.count() > 0 and pw.first.is_visible():
            page.locator("input:not([type=password])").first.fill("admin")
            pw.first.fill("admin123")
            page.get_by_role("button", name=re.compile("登录|登 录|login", re.I)).first.click()
            time.sleep(2.5)
            print("  已登录 admin")
    except Exception as e:
        print(f"  (登录跳过: {str(e)[:80]})")


def main():
    errs = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        page = b.new_page()
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        page.goto(f"{FE}/#/project")
        time.sleep(2)
        maybe_login(page)
        page.goto(f"{FE}/#/project")
        time.sleep(2)

        # 1) 新建项目, 逻辑模式选称重
        page.get_by_role("button", name=re.compile("新建项目")).first.click()
        time.sleep(1)
        page.locator(".el-dialog input[type=text]").first.fill(PROJ_NAME)
        # 逻辑模式下拉选「称重投料模式」
        page.locator(".el-dialog .el-select").last.click()
        time.sleep(0.6)
        page.get_by_role("option", name=re.compile("称重投料模式")).first.click()
        time.sleep(0.4)
        shot(page, "wui_01_create_dialog.png")
        step("新建对话框含称重投料模式选项", True)
        page.get_by_role("button", name=re.compile("^创建$")).first.click()
        time.sleep(2.5)

        # 2) 称重配置页签可见
        tab = page.get_by_role("tab", name=re.compile("称重配置"))
        vis = tab.count() > 0 and tab.first.is_visible()
        step("称重配置页签出现", vis)
        if vis:
            tab.first.click()
            time.sleep(1)
        shot(page, "wui_02_weighing_tab.png")

        # 3) 默认料别在
        body = page.content()
        step("默认料别钢帽水泥/钢脚水泥在", "钢帽水泥" in body and "钢脚水泥" in body)

        # 4) 添加型号
        model_input = page.get_by_placeholder("新型号名")
        model_input.fill("UAT型号A")
        page.get_by_role("button", name=re.compile("添加型号")).first.click()
        time.sleep(1)
        shot(page, "wui_03_model_added.png")
        step("型号添加后出现标准量表", "UAT型号A" in page.content())

        # 5) 填标准量(第一个 input-number)
        nums = page.locator(".el-input-number input")
        if nums.count() > 0:
            nums.first.fill("12.5")
            nums.first.press("Enter")
            time.sleep(0.5)
        shot(page, "wui_04_standard_filled.png")
        step("标准量输入框可填", nums.count() > 0)

        # 6) 保存配置
        page.get_by_role("button", name=re.compile("保存配置")).first.click()
        time.sleep(2.5)
        shot(page, "wui_05_saved.png")

        b.close()

    # 7) 后端校验落库
    projs = requests.get(f"{API}/projects/", timeout=5).json()
    plist = projs if isinstance(projs, list) else projs.get("data", projs.get("projects", []))
    target = None
    for pr in plist:
        if pr.get("name") == PROJ_NAME:
            target = pr
            break
    persisted = False
    detail = {}
    if target:
        d = requests.get(f"{API}/projects/{target['id']}", timeout=5).json()
        detail = d if isinstance(d, dict) else {}
        d = detail.get("data", detail)
        w = (d.get("pipeline_config") or {}).get("weighing") or {}
        persisted = (d.get("logic_mode") == "weighing"
                     and "UAT型号A" in (w.get("models") or {})
                     and abs(float((w.get("models", {}).get("UAT型号A", {}).get("钢帽水泥", {}) or {}).get("standard", 0)) - 12.5) < 0.01)
        print("  落库 weighing:", w)
    step("配置真落库(logic_mode=weighing + 型号标准量)", persisted)

    print("\n  控制台 error:", len(errs))
    for e in errs[:5]:
        print("   ", e[:120])
    npass = sum(1 for _, ok in results if ok)
    print(f"\n==== {npass}/{len(results)} PASS ====")


if __name__ == "__main__":
    main()
