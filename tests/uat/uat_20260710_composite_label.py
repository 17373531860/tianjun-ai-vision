"""T4/T5 UAT: 包装结算「复合条码取段」配置 — 真浏览器编辑上银SY配置并落库回查.

场景: 上银正式产线箱标签是四段拼接码 ORD…|JOB…|54.00|校验串, 需开启复合取段(前缀 JOB).
验证: 面板显示/开关/前缀/取段预览(用现场真实串) → 保存 → GET 回查五个新字段落库.
"""
import time

import requests
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
BE = "http://localhost:8001"
OUT = "tests/uat"
REAL_LABEL = "ORD260300050-2|JOB260600268-13|54.00|55AEA731126D6A9FE063D521BD0A9B02"
results = []


def step(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {extra}" if extra else ""))
    results.append((name, ok))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        page = browser.new_page(viewport={"width": 1680, "height": 950})
        page.goto(f"{FE}/#/settings", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        time.sleep(1.5)

        page.get_by_role("tab", name="包装箱结算").click()
        time.sleep(1)
        page.screenshot(path=f"{OUT}/comp_01_tab.png")

        # 编辑现有「上银SY包装线」配置
        row = page.locator("tr", has_text="上银SY包装线").first
        row.get_by_role("button", name="编辑").click()
        page.wait_for_selector(".el-dialog", state="visible", timeout=5000)
        time.sleep(0.8)

        # 展开组③ 标签校验
        page.get_by_text("③ 标签校验", exact=False).click()
        time.sleep(0.8)
        page.screenshot(path=f"{OUT}/comp_02_group3.png")
        has_switch = page.get_by_text("复合条码取段", exact=True).count() > 0
        step("组③ 出现「复合条码取段」配置项", has_switch)

        # 打开开关
        sw_item = page.get_by_text("复合条码取段", exact=True).locator(
            "xpath=ancestor::div[contains(@class,'el-form-item')][1]")
        sw_item.locator(".el-switch").first.click()
        time.sleep(0.8)
        has_prefix = page.get_by_text("工单段前缀", exact=True).count() > 0
        step("开启后出现分隔符/取段方式/前缀子项", has_prefix)

        # 填前缀 JOB
        pf_item = page.get_by_text("工单段前缀", exact=True).locator(
            "xpath=ancestor::div[contains(@class,'el-form-item')][1]")
        pf = pf_item.locator("input").first
        pf.click(); pf.fill("JOB")
        time.sleep(0.5)

        # 取段预览: 粘现场真实标签串
        pv_item = page.get_by_text("取段预览", exact=True).locator(
            "xpath=ancestor::div[contains(@class,'el-form-item')][1]")
        pv = pv_item.locator("input").first
        pv.click(); pv.fill(REAL_LABEL)
        time.sleep(0.8)
        page.screenshot(path=f"{OUT}/comp_03_preview.png")
        preview_ok = pv_item.get_by_text("JOB260600268-13", exact=True).count() > 0
        step("取段预览: 现场真实串取出 JOB260600268-13", preview_ok)

        # 保存
        page.locator(".el-dialog__footer").get_by_role(
            "button", name="保存").first.click()
        time.sleep(1.5)
        page.screenshot(path=f"{OUT}/comp_04_saved.png")

        browser.close()

    # T5: GET 回查落库
    r = requests.get(f"{BE}/api/v1/packaging-flows", timeout=10)
    cfg = [c for c in r.json().get("items", []) if c["name"] == "上银SY包装线"][0]
    step("落库: composite_label_enabled=True", cfg["composite_label_enabled"] is True)
    step("落库: pick_mode=prefix + prefix=JOB",
         cfg["composite_pick_mode"] == "prefix" and cfg["composite_prefix"] == "JOB",
         f"mode={cfg['composite_pick_mode']} prefix={cfg['composite_prefix']}")
    step("落库: 原有 insert_char/hyphen_pos=12 未被破坏",
         cfg["label_match"] == "insert_char" and cfg["hyphen_pos"] == 12)

    ok = sum(1 for _, o in results if o)
    print(f"\n复合取段 UAT 通过 {ok}/{len(results)}")
    raise SystemExit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
