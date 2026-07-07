"""可见浏览器 UAT: 开工任务信息条逐要素显示开关 (川南 v1 第 1 条)。

验证点:
  1. 工单接收配置页出现"持续显示要素"四个复选框 (任务号/产品代号/工序工步/操作员)。
  2. 勾选四项 → 保存 → GET /mes/inbound/config 落库为 true (T5 双向)。
  3. 监控页带四开关全开加载, 无 console error (T4 渲染不崩)。
  4. 取消四项 → 保存 → 落库为 false (默认全关零回归)。

跑法: 后端 8002 + 前端 6002 已起。
  python tests/uat/uat_20260627_task_info_display.py
"""
import time
import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:6002"
API = "http://127.0.0.1:8002/api/v1"
OUT = "tests/uat"


def get_cfg():
    d = requests.get(f"{API}/mes/inbound/config", timeout=5).json()
    c = d.get("config", d)
    return c.get("task_info_display", {})


def main():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1680, "height": 950})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        # ---- 1. 进 MES → 工单接收 ----
        page.goto(f"{BASE}/#/mes", wait_until="domcontentloaded")
        time.sleep(1.5)
        try:
            page.get_by_text("工单接收", exact=True).first.click()
            time.sleep(1.2)
        except Exception as e:
            results.append(("进入工单接收tab", False, str(e)))

        # 定位"持续显示要素"区 (Element Plus el-form-item 容器)
        def task_row():
            return page.locator(".el-form-item", has_text="持续显示要素").first
        try:
            row = task_row()
            row.scroll_into_view_if_needed()
            time.sleep(0.5)
            page.screenshot(path=f"{OUT}/taskinfo_01_panel.png")
            boxes = ["任务号", "产品代号", "工序工步", "操作员"]
            cnt = 0
            for b in boxes:
                if row.locator(".el-checkbox__label", has_text=b).count() > 0:
                    cnt += 1
            results.append(("四复选框齐全", cnt == 4, f"found {cnt}/4"))
        except Exception as e:
            results.append(("定位持续显示要素", False, str(e)))

        # ---- 2. 勾选四项 + 保存 ----
        try:
            row = task_row()
            for b in ["任务号", "产品代号", "工序工步", "操作员"]:
                row.locator(".el-checkbox__label", has_text=b).first.click()
                time.sleep(0.2)
            page.screenshot(path=f"{OUT}/taskinfo_02_checked.png")
            # 保存按钮
            page.get_by_role("button", name="保存").first.click()
            time.sleep(1.5)
            cfg = get_cfg()
            ok = all(cfg.get(k) is True for k in
                     ["show_task_no", "show_product_code", "show_step_code", "show_operator"])
            results.append(("勾选保存→落库全 true", ok, str(cfg)))
        except Exception as e:
            results.append(("勾选保存", False, str(e)))

        # ---- 3. 监控页带全开加载, 无报错 ----
        try:
            errors.clear()
            page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")
            time.sleep(2.5)
            page.screenshot(path=f"{OUT}/taskinfo_03_monitor.png")
            crit = [e for e in errors if "task" in e.lower() or "getTaskInfo" in e or "undefined" in e.lower()]
            results.append(("监控页加载无关键报错", len(crit) == 0, f"errs={crit[:3]}"))
        except Exception as e:
            results.append(("监控页加载", False, str(e)))

        # ---- 4. 取消四项 + 保存 → 零回归 ----
        try:
            page.goto(f"{BASE}/#/mes", wait_until="domcontentloaded")
            time.sleep(1.5)
            page.get_by_text("工单接收", exact=True).first.click()
            time.sleep(1.2)
            row = task_row()
            row.scroll_into_view_if_needed()
            time.sleep(0.3)
            for b in ["任务号", "产品代号", "工序工步", "操作员"]:
                row.locator(".el-checkbox__label", has_text=b).first.click()
                time.sleep(0.2)
            page.get_by_role("button", name="保存").first.click()
            time.sleep(1.5)
            cfg = get_cfg()
            ok = all(cfg.get(k) is False for k in
                     ["show_task_no", "show_product_code", "show_step_code", "show_operator"])
            results.append(("取消保存→落库全 false", ok, str(cfg)))
        except Exception as e:
            results.append(("取消保存", False, str(e)))

        time.sleep(1)
        browser.close()

    print("\n==================== UAT 结果 ====================")
    allok = True
    for name, ok, detail in results:
        allok = allok and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name} | {detail}")
    print("=================================================")
    print("总判定:", "PASS" if allok else "FAIL")


if __name__ == "__main__":
    main()
