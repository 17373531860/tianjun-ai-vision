"""可见浏览器 UAT t5: 称重模式监控看板 + 逐件数据页。

前置: 已激活一个 weighing 项目(materials=钢帽/钢脚水泥, model 标准型号 std 2.0/1.5)。
流程:
- Monitor 出现称重看板, 型号下拉含「标准型号」
- 用看板 UI 选人员/型号 + 扫码开始 (证明控件可用)
- API 喂重模拟电子秤: 放盆去皮→投钢帽2.0(ok)→放盆→投钢脚1.5(ok)→本件完成
- 看板显示两道料 ok + 本件合格
- 数据页出现称重逐件记录表 (2 行)
"""
import time
import re
import requests
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
API = "http://localhost:8001/api/v1"
OUT = "tests/uat"
CH = 0
results = []


def step(n, ok, e=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" :: {e}" if e else ""))
    results.append(ok)


def feed(w):
    requests.post(f"{API}/weighing/feed", json={"channel_id": CH, "weight": w}, timeout=5)


def feed_stable(w, n=4, gap=0.05):
    snap = None
    for _ in range(n):
        r = requests.post(f"{API}/weighing/feed", json={"channel_id": CH, "weight": w}, timeout=5).json()
        snap = r.get("snapshot")
        time.sleep(gap)
    return snap


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
        page.goto(f"{FE}/#/monitor"); time.sleep(2); maybe_login(page)
        page.goto(f"{FE}/#/monitor"); time.sleep(3)

        body = page.content()
        step("Monitor 出现称重投料看板", "称重投料" in body)
        step("型号下拉含标准型号", "标准型号" in body or page.get_by_text("标准型号").count() > 0)
        page.screenshot(path=f"{OUT}/wmon_01_idle.png")

        # 用看板 UI 选人员/型号
        try:
            page.get_by_placeholder("操作人员").first.fill("演示员")
            sel = page.locator(".el-select").filter(has_text="选择水泥型号").first
            sel.click(); time.sleep(0.6)
            opt = page.get_by_role("option").filter(has_text="标准型号")
            if opt.count(): opt.first.click(); time.sleep(0.3)
            page.get_by_text("确定人员/型号", exact=True).first.click(); time.sleep(1)
            st = requests.get(f"{API}/weighing/state?channel=0", timeout=5).json()
            step("看板 UI 设置人员/型号", st.get("operator") == "演示员" and st.get("model_name") == "标准型号",
                 f"op={st.get('operator')} model={st.get('model_name')}")
        except Exception as e:
            step("看板 UI 设置人员/型号", False, str(e)[:90])

        # 扫码开始
        try:
            page.get_by_placeholder("产品序列号").first.fill("DEMO-001")
            page.get_by_text("扫码开始", exact=True).first.click(); time.sleep(1.2)
            st = requests.get(f"{API}/weighing/state?channel=0", timeout=5).json()
            step("扫码开始→进入待去皮", st.get("phase") == "await_tare", f"phase={st.get('phase')}")
        except Exception as e:
            step("扫码开始→进入待去皮", False, str(e)[:90])
        page.screenshot(path=f"{OUT}/wmon_02_scanned.png")

        # 模拟电子秤喂数: 钢帽水泥
        feed_stable(0.6)          # 放料盆 → 触发去皮
        snap = feed_stable(2.0)   # 投钢帽 2.0kg → ok, 推进到钢脚
        ok1 = snap and snap.get("material_idx") == 1
        step("投钢帽2.0kg判合格并推进到第二道料", ok1, f"idx={snap.get('material_idx') if snap else None}")
        time.sleep(1)
        page.screenshot(path=f"{OUT}/wmon_03_material1_done.png")

        # 钢脚水泥
        feed_stable(0.6)          # 放料盆 → 去皮
        snap = feed_stable(1.5)   # 投钢脚 1.5kg → ok → 本件完成
        done = snap and snap.get("phase") == "done"
        step("投钢脚1.5kg→本件完成(done)", done, f"phase={snap.get('phase') if snap else None}")
        time.sleep(1.2)
        body = page.content()
        step("看板显示本件合格", "本件合格" in body)
        page.screenshot(path=f"{OUT}/wmon_04_product_done.png")

        # 数据页
        page.goto(f"{FE}/#/data"); time.sleep(3)
        body = page.content()
        step("数据页出现称重逐件记录", "称重投料逐件记录" in body)
        recs = requests.get(f"{API}/weighing/records?channel=0&limit=50", timeout=5).json().get("records", [])
        step("逐件记录有2条(钢帽+钢脚)", len(recs) == 2, f"n={len(recs)}")
        page.screenshot(path=f"{OUT}/wmon_05_data_records.png")

        b.close()

    print("\n  控制台 error:", len(errs))
    for e in errs[:5]:
        print("   ", e[:120])
    print(f"\n==== {sum(results)}/{len(results)} PASS ====")


if __name__ == "__main__":
    main()
