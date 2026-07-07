"""可见浏览器 UAT 补验: 上一轮因定位/条件显示判 FAIL 的 3 项。

- B3: 轮询间隔在卡片内按行定位改值 (避开其他 tab 残留的禁用 input)
- A7: 先选「完工信号字段」, 再确认「完工真值词表」出现
- C2: USB 扫码枪对话框先切用途=绑工件, 再确认工位下拉含「自定义…」并手填
"""
import time
import requests
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
BE = "http://localhost:8001/api/v1"
OUT = "tests/uat"
results = []


def step(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {extra}" if extra else ""))
    results.append((name, ok, extra))


def shot(page, fname):
    page.screenshot(path=f"{OUT}/{fname}")
    print(f"  shot -> {OUT}/{fname}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=140)
        page = browser.new_page(viewport={"width": 1680, "height": 950})
        page.goto(FE, wait_until="domcontentloaded")
        time.sleep(3)

        # ── B3: 轮询间隔 卡内按行改值 ──
        try:
            page.goto(f"{FE}/#/settings", wait_until="domcontentloaded")
            time.sleep(2)
            page.get_by_role("tab", name="轮询间隔").click()
            time.sleep(1.5)
            row = page.get_by_text("集群-包装箱列表", exact=True)
            box = row.locator("xpath=ancestor::div[contains(@class,'justify-between')]")
            inp = box.locator(".el-input-number input").first
            inp.click(); inp.fill(""); inp.type("7000"); inp.press("Enter")
            time.sleep(1.5)
            shot(page, "cfg2_b3_polling.png")
            got = requests.get(f"{BE}/system/polling", timeout=5).json()
            step("B3 轮询间隔改值落库(cluster_boxes=7000)",
                 got.get("cluster_boxes") == 7000, f"api={got.get('cluster_boxes')}")
        except Exception as e:
            step("B3 轮询间隔改值", False, str(e)[:160])

        # ── A7: 选完工信号字段后真值词表出现 ──
        try:
            page.goto(f"{FE}/#/mes", wait_until="domcontentloaded")
            time.sleep(2)
            page.get_by_role("button", name="工单接收").click()
            time.sleep(2)
            # 点「完工信号字段」下拉
            cf = page.get_by_text("完工信号字段", exact=True)
            cf_box = cf.locator("xpath=ancestor::div[contains(@class,'el-form-item')]")
            cf_box.locator(".el-select").first.click()
            time.sleep(1)
            # 选第一个候选项
            opts = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
            if opts.count() > 0:
                opts.first.click()
            else:
                # 无候选则手动输入 is_complete
                cf_box.locator("input").first.fill("is_complete")
                cf_box.locator("input").first.press("Enter")
            time.sleep(1.5)
            shot(page, "cfg2_a7_truewords.png")
            has_tw = page.get_by_text("完工真值词表").count() > 0
            step("A7 选完工信号字段后「完工真值词表」出现", has_tw)
        except Exception as e:
            step("A7 完工真值词表条件显示", False, str(e)[:160])

        # ── A6: 各类文案覆盖 始终在 ──
        try:
            has_msg = page.get_by_text("各类文案覆盖").count() > 0
            step("A6 工单接收面板出现「各类文案覆盖」", has_msg)
        except Exception as e:
            step("A6 各类文案覆盖", False, str(e)[:120])

        # ── C2: USB 对话框切绑工件后工位下拉含自定义 ──
        try:
            page.get_by_role("button", name="扫码器").click()
            time.sleep(2)
            page.get_by_role("button", name="添加 USB 扫码枪").click()
            time.sleep(1.5)
            shot(page, "cfg2_c2_usb_open.png")
            # 切用途=绑工件
            page.get_by_role("radio", name="绑工件").click()
            time.sleep(1)
            shot(page, "cfg2_c2_usb_bind.png")
            has_custom = page.get_by_text("自定义…").count() > 0
            step("C2 USB 工位下拉含「自定义…」", has_custom)
            if has_custom:
                # 选自定义, 出现手填框
                bindbox = page.get_by_text("绑工件到工位", exact=True).locator(
                    "xpath=ancestor::div[contains(@class,'el-form-item')]")
                bindbox.locator(".el-select").first.click()
                time.sleep(0.8)
                page.locator(".el-select-dropdown:visible .el-select-dropdown__item",
                             has_text="自定义…").first.click()
                time.sleep(1)
                shot(page, "cfg2_c2_usb_custom_input.png")
                has_input = bindbox.locator(".el-input-number").count() > 0
                step("C2 选自定义后出现手填工位框", has_input)
        except Exception as e:
            step("C2 USB 自定义工位", False, str(e)[:160])

        time.sleep(1)
        browser.close()

    print("\n==================== UAT2 汇总 ====================")
    ok = sum(1 for _, o, _ in results if o)
    for n, o, e in results:
        print(f"  {'✓' if o else '✗'} {n}" + (f"  ({e})" if e and not o else ""))
    print(f"  通过 {ok}/{len(results)}")


if __name__ == "__main__":
    main()
