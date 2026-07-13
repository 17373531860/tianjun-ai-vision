"""T4/T5/T7 UAT: 包装结算「工单号识别规则」— 真浏览器配置 + 真后端行为验证.

现场叙事 (2026-07-12 上银): 箱标签上并排印着数量条形码 (如 80.00). 开机后没有在途
工单时, 第一枪误扫数量码会开出一张叫 "80.00" 的垃圾工单. 加"工单号识别规则"(默认空
=不过滤), 上银填 ^JOB: 开第一单前先过格式关, 数量码被拒并提示; 有在途工单时此规则
不参与, 仍走原有复合取段+标签不符逻辑.

验证: ① 浏览器编辑上银SY配置 → 组③出现"工单号识别规则"输入框 → 填 ^JOB 保存;
     ② GET 回查落库; ③ 真后端扫码: 无在途工单扫 80.00 → 拒(不开单),
     扫 JOB260600151202 → 正常开单; 有在途工单扫 80.00 → 走标签不符(非识别规则拒).
     结束后强制结案清理, 不留测试工单.
"""
import time

import requests
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
BE = "http://localhost:8001"
OUT = "tests/uat"
VIDEO_DIR = "/tmp/uat_video"
results = []


def step(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {extra}" if extra else ""))
    results.append((name, ok))


def scan(code):
    r = requests.post(f"{BE}/api/v1/packaging-flows/scan",
                      json={"code": code, "channel_id": 0}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()


def get_state():
    r = requests.get(f"{BE}/api/v1/packaging-flows/1/state", timeout=10)
    return (r.json() or {}).get("state") if r.status_code == 200 else None


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        page = browser.new_page(viewport={"width": 1680, "height": 950},
                                record_video_dir=VIDEO_DIR)
        page.goto(f"{FE}/#/settings", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        time.sleep(1.5)

        page.get_by_role("tab", name="包装箱结算").click()
        time.sleep(1)

        row = page.locator("tr", has_text="上银SY包装线").first
        row.get_by_role("button", name="编辑").click()
        page.wait_for_selector(".el-dialog", state="visible", timeout=5000)
        time.sleep(0.8)

        page.get_by_text("③ 标签校验", exact=False).click()
        time.sleep(0.8)
        has_field = page.get_by_text("工单号识别规则", exact=True).count() > 0
        step("组③ 出现「工单号识别规则」输入框", has_field)
        page.screenshot(path=f"{OUT}/ocp_01_field.png")

        item = page.get_by_text("工单号识别规则", exact=True).locator(
            "xpath=ancestor::div[contains(@class,'el-form-item')][1]")
        inp = item.locator("input").first
        inp.click()
        inp.fill("^JOB")
        time.sleep(0.5)
        page.screenshot(path=f"{OUT}/ocp_02_filled.png")

        page.locator(".el-dialog__footer").get_by_role(
            "button", name="保存").first.click()
        time.sleep(1.5)
        page.screenshot(path=f"{OUT}/ocp_03_saved.png")
        browser.close()

    # T5: GET 回查落库
    r = requests.get(f"{BE}/api/v1/packaging-flows", timeout=10)
    cfg = [c for c in r.json().get("items", []) if c["name"] == "上银SY包装线"][0]
    step("落库: order_code_pattern='^JOB'", cfg.get("order_code_pattern") == "^JOB",
         f"实际={cfg.get('order_code_pattern')!r}")
    step("落库: 复合取段配置未被破坏",
         cfg["composite_label_enabled"] is True and cfg["composite_prefix"] == "JOB")

    # 真后端行为: 无在途工单 → 第一枪数量码被拒
    if get_state() is not None:
        requests.post(f"{BE}/api/v1/packaging-flows/1/force-settle",
                      json={"reason": "UAT 前置清场", "channel_id": 0}, timeout=10)
    scan("80.00")
    step("无在途工单扫 80.00 → 拒扫不开单", get_state() is None)

    # 工单码正常开单
    scan("JOB260600151202")
    st = get_state()
    step("扫 JOB260600151202 → 正常开单 (补符号)",
         st is not None and st.get("order_no") == "JOB260600151-202",
         f"state={None if st is None else st.get('order_no')}")

    # 有在途工单时规则不参与: 数量码走标签不符 (工单保持)
    scan("80.00")
    st = get_state()
    step("有在途工单扫 80.00 → 工单保持不被切换/污染",
         st is not None and st.get("order_no") == "JOB260600151-202")

    # 清场: 强制结案 + 删测试 run 痕迹
    requests.post(f"{BE}/api/v1/packaging-flows/1/force-settle",
                  json={"reason": "UAT 收尾清场(工单号识别规则)", "channel_id": 0},
                  timeout=10)
    step("收尾: 强制结案清场", get_state() is None)

    ok = sum(1 for _, o in results if o)
    print(f"\n工单号识别规则 UAT 通过 {ok}/{len(results)} (视频: {VIDEO_DIR})")
    raise SystemExit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
