"""三档配置UI前端闭环: 项目页填数字→防抖保存→后端核对→刷新回填.

直接验证用户最早报的两个 bug:
  1. "数字填进去不显示" → 刷新后 input 回填
  2. 三档功能后端没生效 → PUT 后 GET 后端确认收到
"""
import time
import requests
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8011/api/v1"
FE = "http://127.0.0.1:6011"
SHOTS = "/tmp/uat_jinlong/shots"
logs = []


def step(l, ok, d=""):
    logs.append((l, bool(ok), d))
    print(f"[{'OK' if ok else '!!'}] {l}  {d}", flush=True)


# 先把后端三档配置清空, 确保填写前是干净状态
requests.put(f"{B}/plugins/internal-demo/durations/step-durations",
             json={"enabled": True, "default": {"min_sec": 0, "warn_sec": 0, "max_sec": 0},
                   "steps": {}, "alarm_event": {"warn": "event2", "ng": "event2"}}, timeout=10)
before = requests.get(f"{B}/plugins/internal-demo/durations/step-durations").json()
step("前置: 后端三档配置已清空", not before.get("steps"), f"steps={before.get('steps')}")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu",
                                               "--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(viewport={"width": 1600, "height": 1000}, record_video_dir="/tmp/uat_jinlong/video")
    pg = ctx.new_page()
    pg.set_default_timeout(60000)
    console_logs = []
    pg.on("console", lambda m: console_logs.append(m.text))

    pg.goto(f"{FE}/#/project", wait_until="commit", timeout=90000)
    reloaded = False
    for i in range(12):
        time.sleep(4)
        blen = pg.evaluate("document.body ? document.body.innerText.length : 0")
        if blen and blen > 80:
            break
        if not reloaded and i >= 2:
            try:
                pg.reload(wait_until="commit", timeout=90000)
            except Exception:
                pass
            reloaded = True
    time.sleep(3)
    pg.screenshot(path=f"{SHOTS}/F1_project_page.png", full_page=True)

    # 点左侧项目卡片(div.cursor-pointer, 避开顶部下拉), 选运行中的 599
    card = pg.locator("div.cursor-pointer", has_text="__uat_jinlong_tiers_1780310599").first
    try:
        card.click(timeout=10000)
        time.sleep(2.5)
    except Exception as e:
        print("  点项目卡片失败:", e, flush=True)
    # 切到「步骤设置」tab (name=steps)
    try:
        pg.get_by_role("tab", name="步骤设置").click(timeout=5000)
        time.sleep(1.5)
    except Exception:
        try:
            pg.locator("text=步骤设置").first.click(timeout=5000)
            time.sleep(1.5)
        except Exception as e:
            print("  点步骤设置tab失败:", e, flush=True)
    time.sleep(2)
    pg.screenshot(path=f"{SHOTS}/F1b_after_select.png", full_page=True)

    # 三档输入框特征: buildDurationCell 渲染 input style 含 46px (轮询等渲染)
    dur = pg.locator("input[style*='46px']")
    n = 0
    for _ in range(10):
        n = dur.count()
        if n >= 3:
            break
        time.sleep(2)
    pg.screenshot(path=f"{SHOTS}/F1b_after_select.png", full_page=True)
    step("项目页找到三档输入框(最短/警告/最长)", n >= 3, f"input数={n}")

    if n >= 3:
        # 填第一个步骤行的三档: 最短1.5 / 警告3 / 最长5
        vals = ["1.5", "3", "5"]
        for idx, v in enumerate(vals):
            el = dur.nth(idx)
            el.fill(v)
            el.dispatch_event("change")
            el.dispatch_event("blur")
            time.sleep(0.3)
        step("前端三档数字已填写(1.5/3/5)", True)
        # 等防抖800ms + 网络
        time.sleep(2.5)
        pg.screenshot(path=f"{SHOTS}/F2_filled.png", full_page=True)

        # 后端核对: PUT 是否真到了后端
        after = requests.get(f"{B}/plugins/internal-demo/durations/step-durations").json()
        steps_cfg = after.get("steps") or {}
        got = None
        for lab, e in steps_cfg.items():
            if e.get("min_sec") == 1.5 and e.get("warn_sec") == 3 and e.get("max_sec") == 5:
                got = (lab, e)
                break
        step("后端确实收到三档配置(治'后端没生效')", got is not None, f"steps={steps_cfg}")

        # 刷新页面验回填(治'数字填进去不显示')
        pg.reload(wait_until="commit", timeout=90000)
        time.sleep(8)
        try:
            pg.locator("div.cursor-pointer", has_text="__uat_jinlong_tiers_1780310599").first.click(timeout=10000)
            time.sleep(2.5)
            try:
                pg.get_by_role("tab", name="步骤设置").click(timeout=5000)
            except Exception:
                pg.locator("text=步骤设置").first.click(timeout=5000)
            time.sleep(2.5)
        except Exception as e:
            print("  刷新后重选项目失败:", e, flush=True)
        dur2 = pg.locator("input[style*='46px']")
        if dur2.count() >= 3:
            v0 = dur2.nth(0).input_value()
            v1 = dur2.nth(1).input_value()
            v2 = dur2.nth(2).input_value()
            pg.screenshot(path=f"{SHOTS}/F3_reload_refill.png", full_page=True)
            refill_ok = (v0 in ("1.5", "1.50") and v1 in ("3", "3.0") and v2 in ("5", "5.0"))
            step("刷新后数字正确回填(治'数字填进去不显示')", refill_ok, f"回填=[{v0},{v1},{v2}]")
        else:
            step("刷新后三档输入框可见", False, f"input数={dur2.count()}")

    dlog = [c for c in console_logs if "fujian" in c or "三档" in c or "durations" in c]
    print("\n--- 插件相关 console 日志 ---", flush=True)
    for c in dlog[-8:]:
        print("  ", c, flush=True)

    ctx.close()
    b.close()

failed = [x for x in logs if not x[1]]
print(f"\nfailed: {len(failed)}", flush=True)
