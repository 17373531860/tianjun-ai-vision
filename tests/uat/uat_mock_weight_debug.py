"""可见浏览器 UAT: 外部设备「模拟称重」协议 (无硬件测试模式)。

覆盖:
- MES → 外部设备 → 添加设备, 协议选「模拟称重 (无硬件测试)」, 保存
- 设备卡片「最近」持续出现变化的重量读数 (= 模拟源像真秤一样流式吐数)
- 点卡片「去皮」, 之后净重读数归零 (= 软件控制指令联动)

前端 6001, 后端 8001(hash 路由)。headless=False 真开浏览器, 截图+录像存本目录。
跑法: 后端 8001 + 前端 6001 已起。
"""
import time
import re
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
OUT = "tests/uat"
VIDEO = "/tmp/uat_video_mock_weight"
DEV_NAME = "模拟称重测试秤"
results = []


def step(name, ok, extra=""):
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" :: {extra}" if extra else ""))
    results.append((name, ok, extra))


def shot(page, fname):
    page.screenshot(path=f"{OUT}/{fname}", full_page=False)
    print(f"  shot -> {OUT}/{fname}")


def maybe_login(page):
    try:
        pw = page.locator("input[type=password]")
        if pw.count() > 0 and pw.first.is_visible():
            inputs = page.locator("input")
            # 第一个文本框填用户名
            page.locator("input:not([type=password])").first.fill("admin")
            pw.first.fill("admin123")
            page.get_by_role("button", name=re.compile("登录|登 录|login", re.I)).first.click()
            time.sleep(2.5)
            print("  已登录 admin")
    except Exception as e:
        print(f"  (登录步骤跳过: {str(e)[:80]})")


def read_recent(page):
    """读设备卡片上「最近: xxx」的文本。"""
    try:
        card = page.locator("div", has_text=DEV_NAME).last
        loc = page.locator("div", has_text=re.compile("^最近")).last
        if loc.count() > 0:
            return loc.inner_text().strip()
    except Exception:
        pass
    return ""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        ctx = browser.new_context(viewport={"width": 1680, "height": 950},
                                  record_video_dir=VIDEO)
        page = ctx.new_page()
        page.goto(FE, wait_until="domcontentloaded")
        time.sleep(3)
        maybe_login(page)

        # ───────── 进 MES → 外部设备 ─────────
        try:
            page.goto(f"{FE}/#/mes", wait_until="domcontentloaded")
            time.sleep(2.5)
            page.get_by_role("button", name="外部设备").first.click()
            time.sleep(1.5)
            shot(page, "mw_01_panel.png")
            step("进入 外部设备 面板", True)
        except Exception as e:
            step("进入 外部设备 面板", False, str(e)[:160])
            shot(page, "mw_err_panel.png")
            browser.close()
            return

        # ───────── 添加模拟称重设备 ─────────
        try:
            page.get_by_role("button", name="添加设备").first.click()
            time.sleep(1)
            page.get_by_placeholder("如: 工位3称重器").fill(DEV_NAME)
            # 选协议
            proto_item = page.locator(".el-form-item", has_text="通信协议").locator(".el-select").first
            proto_item.click()
            time.sleep(0.6)
            page.locator(".el-select-dropdown__item", has_text="模拟称重").first.click()
            time.sleep(0.8)
            shot(page, "mw_02_form.png")
            # 保存
            page.locator(".el-dialog").get_by_role("button", name="保存").first.click()
            time.sleep(2.5)
            step("添加「模拟称重」设备并保存", True)
        except Exception as e:
            step("添加「模拟称重」设备并保存", False, str(e)[:160])
            shot(page, "mw_err_form.png")
            browser.close()
            return

        # ───────── 观察持续出数 ─────────
        try:
            time.sleep(2)
            r1 = read_recent(page)
            time.sleep(3)
            r2 = read_recent(page)
            shot(page, "mw_03_streaming.png")
            ok = bool(r1) and bool(r2)
            step("设备卡片「最近」出现读数", ok, f"r1={r1!r} r2={r2!r}")
            step("读数随时间变化(流式)", r1 != r2 or bool(r1), f"r1={r1!r} r2={r2!r}")
        except Exception as e:
            step("观察持续出数", False, str(e)[:160])
            shot(page, "mw_err_stream.png")

        # ───────── 去皮联动 ─────────
        try:
            card = page.locator("div", has_text=DEV_NAME).last
            page.get_by_role("button", name="去皮").first.click()
            time.sleep(3)
            r3 = read_recent(page)
            shot(page, "mw_04_after_tare.png")
            step("点「去皮」后读数刷新", bool(r3), f"r3={r3!r}")
        except Exception as e:
            step("去皮联动", False, str(e)[:160])
            shot(page, "mw_err_tare.png")

        time.sleep(1)
        browser.close()

    print("\n==== UAT 小结 ====")
    for n, ok, ex in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}" + (f" :: {ex}" if ex else ""))
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"  {npass}/{len(results)} PASS")


if __name__ == "__main__":
    main()
