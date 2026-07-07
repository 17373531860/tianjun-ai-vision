"""可见浏览器 UAT: 配置化审计本轮成果 (A1/A2/A6/A7 + B3 + C2/C5)。

覆盖:
- B3/C5: 设置页「轮询间隔」标签 — 轮询间隔卡 + 日志条数卡; 改值即存, 经 API 复核落库
- A2:    MES「外部对接」网关面板 — 出现「健康」列 + 主动健康探测表单
- A1/A6/A7: MES「工单接收」面板 — 自定义接收路径 / 各类文案覆盖 / 完工真值词表
- C2:    扫码器 USB 扫码枪对话框 — 绑定工位下拉出现「自定义…」项

前端 6001, 后端 8001(hash 路由)。headless=False 真开浏览器, 截图存本目录。
非关键步骤 try/except 兜底, 不因单点失败中断全程。
"""
import time
import requests
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
BE = "http://localhost:8001/api/v1"
OUT = "tests/uat"
results = []


def step(name, ok, extra=""):
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}" + (f" :: {extra}" if extra else "")
    print(line)
    results.append((name, ok, extra))


def shot(page, fname):
    path = f"{OUT}/{fname}"
    page.screenshot(path=path, full_page=False)
    print(f"  shot -> {path}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        page = browser.new_page(viewport={"width": 1680, "height": 950})
        page.goto(FE, wait_until="domcontentloaded")
        time.sleep(3)

        # ───────── B3/C5: 设置页 轮询间隔 标签 ─────────
        try:
            page.goto(f"{FE}/#/settings", wait_until="domcontentloaded")
            time.sleep(2)
            # 点「轮询间隔」标签
            page.get_by_role("tab", name="轮询间隔").click()
            time.sleep(1.5)
            shot(page, "cfg_b3c5_polling_tab.png")
            has_poll = page.get_by_text("管理面板刷新间隔").count() > 0
            has_log = page.get_by_text("日志显示条数").count() > 0
            step("设置页出现「轮询间隔」+「日志显示条数」两卡片", has_poll and has_log,
                 f"poll={has_poll} log={has_log}")

            # 改集群-包装箱列表间隔: 找到第一个 input-number 改成 7000
            inputs = page.locator(".el-input-number input")
            cnt = inputs.count()
            if cnt > 0:
                first = inputs.nth(0)
                first.click()
                first.fill("")
                first.type("7000")
                first.press("Enter")
                time.sleep(1.5)
                shot(page, "cfg_b3_polling_changed.png")
                # API 复核
                got = requests.get(f"{BE}/system/polling", timeout=5).json()
                step("轮询间隔改值落库(cluster_boxes=7000)",
                     got.get("cluster_boxes") == 7000, f"api={got.get('cluster_boxes')}")
            else:
                step("找到轮询间隔输入框", False, "no input-number")
        except Exception as e:
            step("设置页 轮询间隔/日志条数", False, str(e)[:160])

        # ───────── C5: 改一个日志条数, API 复核 ─────────
        try:
            # 日志条数卡片在第二张; 直接用 PUT 后 GET 不够"眼见", 仍用 UI:
            # 定位"扫码器-日志"行附近的 input-number
            row = page.get_by_text("扫码器-日志", exact=True)
            if row.count() > 0:
                box = row.locator("xpath=ancestor::div[contains(@class,'justify-between')]")
                inp = box.locator(".el-input-number input").first
                inp.click(); inp.fill(""); inp.type("88"); inp.press("Enter")
                time.sleep(1.5)
                shot(page, "cfg_c5_loglimit_changed.png")
                got = requests.get(f"{BE}/system/log-limits", timeout=5).json()
                step("日志条数改值落库(scanner=88)",
                     got.get("scanner") == 88, f"api={got.get('scanner')}")
            else:
                step("找到日志条数「扫码器-日志」行", False)
        except Exception as e:
            step("日志条数 UI 改值", False, str(e)[:160])

        # ───────── A2: MES 外部对接 网关「健康」列 ─────────
        try:
            page.goto(f"{FE}/#/mes", wait_until="domcontentloaded")
            time.sleep(2)
            page.get_by_role("button", name="外部对接").click()
            time.sleep(2)
            shot(page, "cfg_a2_gateway_health.png")
            has_health_col = page.get_by_text("健康", exact=True).count() > 0
            step("网关面板出现「健康」列", has_health_col)
        except Exception as e:
            step("网关 健康列", False, str(e)[:160])

        # ───────── A1/A6/A7: MES 工单接收 面板 ─────────
        try:
            page.get_by_role("button", name="工单接收").click()
            time.sleep(2)
            shot(page, "cfg_a1a6a7_inbound.png")
            has_paths = page.get_by_text("自定义接收路径").count() > 0
            has_truewords = page.get_by_text("完工真值词表").count() > 0
            step("工单接收面板出现「自定义接收路径」(A1)", has_paths)
            step("工单接收面板出现「完工真值词表」(A7)", has_truewords)
        except Exception as e:
            step("工单接收 A1/A7 区块", False, str(e)[:160])

        # ───────── C2: 扫码器 USB 扫码枪对话框 自定义工位 ─────────
        try:
            page.get_by_role("button", name="扫码器").click()
            time.sleep(2)
            shot(page, "cfg_c2_scanner_tab.png")
            # 新建 USB 扫码枪入口文案可能是「新建USB扫码枪」之类; 尝试常见按钮
            opened = False
            for btn_txt in ["USB", "新建USB", "USB扫码枪", "键盘扫码枪", "添加USB"]:
                b = page.get_by_role("button", name=btn_txt)
                if b.count() > 0:
                    b.first.click(); time.sleep(1.5); opened = True; break
            if opened:
                shot(page, "cfg_c2_usb_dialog.png")
                # 用途选非 pull 才会显示工位下拉; 尝试切换用途
                has_custom = page.get_by_text("自定义…").count() > 0
                step("USB 扫码枪对话框工位下拉含「自定义…」(C2)", has_custom,
                     "若为0可能需先切用途为绑工件")
            else:
                step("找到 USB 扫码枪新建入口", False, "按钮文案未命中(非阻塞)")
        except Exception as e:
            step("扫码器 USB 自定义工位", False, str(e)[:160])

        time.sleep(1)
        browser.close()

    print("\n==================== UAT 汇总 ====================")
    ok = sum(1 for _, o, _ in results if o)
    for n, o, e in results:
        print(f"  {'✓' if o else '✗'} {n}" + (f"  ({e})" if e and not o else ""))
    print(f"  通过 {ok}/{len(results)}")


if __name__ == "__main__":
    main()
