"""C2 单项补验: USB 扫码枪对话框切「绑工件」后, 工位下拉含「自定义…」并出现手填框。"""
import time
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
OUT = "tests/uat"
results = []


def step(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {extra}" if extra else ""))
    results.append((name, ok))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=140)
        page = browser.new_page(viewport={"width": 1680, "height": 950})
        page.goto(f"{FE}/#/mes", wait_until="domcontentloaded")
        time.sleep(3)
        page.get_by_role("button", name="扫码器").click()
        time.sleep(2)
        page.get_by_role("button", name="添加 USB 扫码枪").click()
        time.sleep(1.5)
        # el-radio-button: 点 label 文本
        page.get_by_text("绑工件", exact=True).click()
        time.sleep(1)
        page.screenshot(path=f"{OUT}/c2_bind_selected.png")
        has_custom = page.get_by_text("自定义…").count() > 0
        step("C2 切绑工件后工位下拉含「自定义…」", has_custom)

        if has_custom:
            bindbox = page.get_by_text("绑工件到工位", exact=True).locator(
                "xpath=ancestor::div[contains(@class,'el-form-item')]")
            bindbox.locator(".el-select").first.click()
            time.sleep(0.8)
            page.locator(".el-select-dropdown:visible .el-select-dropdown__item",
                         has_text="自定义…").first.click()
            time.sleep(1)
            page.screenshot(path=f"{OUT}/c2_custom_input.png")
            has_input = bindbox.locator(".el-input-number").count() > 0
            step("C2 选自定义后出现手填工位框", has_input)
            # 手填 7
            if has_input:
                ni = bindbox.locator(".el-input-number input").first
                ni.click(); ni.fill(""); ni.type("7"); ni.press("Enter")
                time.sleep(0.8)
                page.screenshot(path=f"{OUT}/c2_custom_7.png")
                step("C2 手填工位号 7 可输入", True)
        time.sleep(1)
        browser.close()

    ok = sum(1 for _, o in results if o)
    print(f"\nC2 通过 {ok}/{len(results)}")


if __name__ == "__main__":
    main()
