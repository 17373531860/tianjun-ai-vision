"""可见浏览器 UAT: 证明称重模式对老程序零污染。

- 新建一个【顺序模式】项目 → 不该出现「称重配置」页签
- 保存 → GET 校验 pipeline_config 里【没有】weighing 键 (DB 零污染)
- 再把逻辑模式切到【称重投料】→ 称重配置页签即时出现 (运行时切换才注入)
"""
import time
import re
import requests
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
API = "http://localhost:8001/api/v1"
OUT = "tests/uat"
PROJ = f"UAT零污染_{int(time.time())}"
results = []


def step(n, ok, e=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" :: {e}" if e else ""))
    results.append(ok)


def maybe_login(page):
    try:
        pw = page.locator("input[type=password]")
        if pw.count() and pw.first.is_visible():
            page.locator("input:not([type=password])").first.fill("admin")
            pw.first.fill("admin123")
            page.get_by_role("button", name=re.compile("登录|login", re.I)).first.click()
            time.sleep(2.5)
    except Exception:
        pass


def main():
    errs = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        page = b.new_page()
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        page.goto(f"{FE}/#/project"); time.sleep(2); maybe_login(page)
        page.goto(f"{FE}/#/project"); time.sleep(2)

        # 1) 新建顺序模式项目 (默认 logic_mode)
        page.get_by_role("button", name=re.compile("新建项目")).first.click(); time.sleep(1)
        page.locator(".el-dialog input[type=text]").first.fill(PROJ)
        page.get_by_role("button", name=re.compile("^创建$")).first.click(); time.sleep(2.5)

        tab = page.get_by_role("tab", name=re.compile("称重配置"))
        step("顺序项目【无】称重配置页签", tab.count() == 0 or not tab.first.is_visible())

        # 2) 保存顺序项目
        page.get_by_role("button", name=re.compile("保存配置")).first.click(); time.sleep(2)

        # 3) 切逻辑模式到称重 (基础设置页内单选)
        basic_tab = page.get_by_role("tab", name=re.compile("基础设置"))
        if basic_tab.count(): basic_tab.first.click(); time.sleep(0.8)
        radio = page.locator("input[type=radio][value=weighing]")
        switched = False
        if radio.count():
            radio.first.check(force=True); time.sleep(1.2)
            switched = True
        tab2 = page.get_by_role("tab", name=re.compile("称重配置"))
        step("切到称重模式后页签即时出现", switched and tab2.count() > 0 and tab2.first.is_visible())
        page.screenshot(path=f"{OUT}/wzero_after_switch.png")
        b.close()

    # 4) 后端校验: 顺序项目存库时 pipeline_config 无 weighing
    items = requests.get(f"{API}/projects/", timeout=5).json().get("items", [])
    tgt = next((x for x in items if x["name"] == PROJ), None)
    no_weighing = False
    if tgt:
        d = requests.get(f"{API}/projects/{tgt['id']}", timeout=5).json()
        d = d.get("data", d)
        pc = d.get("pipeline_config") or {}
        no_weighing = (d.get("logic_mode") != "weighing") and ("weighing" not in pc)
        print("  顺序项目 logic_mode=", d.get("logic_mode"), "| 含weighing键?", "weighing" in pc)
    step("顺序项目存库 pipeline_config 不含 weighing 键 (DB零污染)", no_weighing)

    print("  控制台 error:", len(errs))
    print(f"\n==== {sum(results)}/{len(results)} PASS ====")


if __name__ == "__main__":
    main()
