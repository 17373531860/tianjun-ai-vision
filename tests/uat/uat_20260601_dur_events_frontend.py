"""v1.2.0 前端闭环 UAT — 每步「事件下拉 + 提示框开关」渲染/保存/回填 + 提示框弹出.

验证用户本次需求:
  1. 三档单元格里出现「警告事件下拉 + 提示框勾选」「NG事件下拉 + 提示框勾选」(percell)
  2. 事件下拉选项来自报警灯事件列表
  3. 选事件 + 勾提示框 → 防抖保存 → 后端确实收到 warn_event/ng_event/warn_toast/ng_toast
  4. 刷新回填
  5. 提示框弹出(方案B): 拦截注入一条 pending-toast → 前端轮询自绘 toast DOM 真的弹出
"""
import time
import requests
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8011/api/v1"
FE = "http://127.0.0.1:6011"
SHOTS = "/tmp/uat_dur_v120"
import os
os.makedirs(SHOTS, exist_ok=True)
logs = []


def step(l, ok, d=""):
    logs.append((l, bool(ok), d))
    print(f"[{'OK' if ok else '!!'}] {l}  {d}", flush=True)


# 激活项目名
projs = requests.get(f"{B}/projects").json()
plist = projs if isinstance(projs, list) else projs.get("data") or projs.get("projects") or []
active = next((p for p in plist if p.get("is_active")), plist[0] if plist else None)
PROJ = active["name"] if active else "ZJ"
step("拿到激活项目名", bool(active), f"name={PROJ}")

# 清空三档配置
requests.put(f"{B}/plugins/internal-demo/durations/step-durations",
             json={"enabled": True, "default": {"min_sec": 0, "warn_sec": 0, "max_sec": 0},
                   "steps": {}, "alarm_event": {"warn": "event2", "ng": "event2"}}, timeout=10)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
    ctx = b.new_context(viewport={"width": 1680, "height": 1020}, record_video_dir=f"{SHOTS}/video")
    pg = ctx.new_page()
    pg.set_default_timeout(60000)
    clogs = []
    pg.on("console", lambda m: clogs.append(m.text))

    pg.goto(f"{FE}/#/project", wait_until="commit", timeout=90000)
    for i in range(12):
        time.sleep(4)
        if pg.evaluate("document.body ? document.body.innerText.length : 0") > 80:
            break
        if i == 2:
            try: pg.reload(wait_until="commit", timeout=90000)
            except Exception: pass
    time.sleep(3)
    pg.screenshot(path=f"{SHOTS}/01_project_page.png", full_page=True)

    # 选项目卡片
    try:
        pg.locator("div.cursor-pointer", has_text=PROJ).first.click(timeout=10000)
        time.sleep(2.5)
    except Exception as e:
        print("  点项目卡片失败:", e, flush=True)
    # 步骤设置 tab
    try:
        pg.get_by_role("tab", name="步骤设置").click(timeout=5000)
    except Exception:
        try: pg.locator("text=步骤设置").first.click(timeout=5000)
        except Exception as e: print("  点步骤设置失败:", e, flush=True)
    time.sleep(2.5)

    # 等三档渲染 (插件轮询注册 slot)
    dur = pg.locator("input[style*='46px']")
    for _ in range(10):
        if dur.count() >= 3: break
        time.sleep(2)
    pg.screenshot(path=f"{SHOTS}/02_step_settings.png", full_page=True)
    step("三档秒数输入框出现", dur.count() >= 3, f"input数={dur.count()}")

    # 新控件: 事件下拉(select max-width:74px) + 提示框 checkbox(input width:11px)
    ev_sel = pg.locator("select[style*='74px']")
    toast_chk = pg.locator("input[style*='11px']")
    step("每步出现『触发事件』下拉(警告+NG)", ev_sel.count() >= 2, f"select数={ev_sel.count()}")
    step("每步出现『提示框』勾选(警告+NG)", toast_chk.count() >= 2, f"checkbox数={toast_chk.count()}")

    # 下拉选项来自报警灯事件列表
    opts = []
    if ev_sel.count() >= 1:
        opts = ev_sel.first.locator("option").all_inner_texts()
    step("事件下拉有选项(来自报警灯配置)", len(opts) >= 1, f"options={opts}")

    if dur.count() >= 3 and ev_sel.count() >= 2:
        # 填三档 + 选事件 + 取消NG提示框
        dur.nth(0).fill("1.5"); dur.nth(0).dispatch_event("change")
        dur.nth(1).fill("3");   dur.nth(1).dispatch_event("change")
        dur.nth(2).fill("5");   dur.nth(2).dispatch_event("change")
        # 警告事件选第一个 option 的 value, NG 事件选最后一个
        warn_val = ev_sel.first.locator("option").first.get_attribute("value")
        ng_val = ev_sel.nth(1).locator("option").last.get_attribute("value")
        ev_sel.first.select_option(warn_val)
        ev_sel.nth(1).select_option(ng_val)
        # 取消 NG 提示框(第2个checkbox)
        try:
            if toast_chk.nth(1).is_checked():
                toast_chk.nth(1).uncheck()
        except Exception: pass
        time.sleep(2.8)  # 防抖800ms + 网络
        pg.screenshot(path=f"{SHOTS}/03_filled_events.png", full_page=True)
        step("已填三档+选事件+取消NG提示框", True, f"warn_event={warn_val} ng_event={ng_val}")

        # 后端核对
        after = requests.get(f"{B}/plugins/internal-demo/durations/step-durations").json()
        steps_cfg = after.get("steps") or {}
        hit = None
        for lab, e in steps_cfg.items():
            if e.get("min_sec") == 1.5 and e.get("warn_event") == warn_val and e.get("ng_event") == ng_val:
                hit = (lab, e); break
        step("后端收到每步事件配置(warn_event/ng_event)", hit is not None, f"steps={steps_cfg}")
        if hit:
            step("后端收到 NG提示框=关闭(ng_toast=False)", hit[1].get("ng_toast") is False,
                 f"ng_toast={hit[1].get('ng_toast')}")

        # 刷新回填
        pg.reload(wait_until="commit", timeout=90000)
        time.sleep(8)
        try:
            pg.locator("div.cursor-pointer", has_text=PROJ).first.click(timeout=10000)
            time.sleep(2.5)
            try: pg.get_by_role("tab", name="步骤设置").click(timeout=5000)
            except Exception: pg.locator("text=步骤设置").first.click(timeout=5000)
            time.sleep(2.5)
        except Exception as e:
            print("  刷新后重选失败:", e, flush=True)
        ev2 = pg.locator("select[style*='74px']")
        dur2 = pg.locator("input[style*='46px']")
        if dur2.count() >= 3 and ev2.count() >= 2:
            rv = [dur2.nth(0).input_value(), dur2.nth(1).input_value(), dur2.nth(2).input_value()]
            wsel = ev2.first.input_value()
            pg.screenshot(path=f"{SHOTS}/04_reload_refill.png", full_page=True)
            ok_num = rv[0] in ("1.5", "1.50") and rv[1] in ("3", "3.0") and rv[2] in ("5", "5.0")
            step("刷新后三档数字回填", ok_num, f"回填={rv}")
            step("刷新后警告事件下拉回填", wsel == warn_val, f"下拉={wsel} 期望={warn_val}")
        else:
            step("刷新后控件可见", False, f"dur={dur2.count()} sel={ev2.count()}")

    # ===== 提示框弹出(方案B): 拦截注入一条 pending-toast, 验证前端自绘 toast =====
    injected = {"n": 0}
    def handle_toasts(route):
        injected["n"] += 1
        # 只注入前2次, 之后清空避免无限弹
        if injected["n"] <= 2:
            route.fulfill(status=200, content_type="application/json",
                          body='{"toasts":[{"channel":0,"level":"ng","step":"扭5N螺丝","reason":"超过最长时间 6.0s ≥ 5.0s"}]}')
        else:
            route.fulfill(status=200, content_type="application/json", body='{"toasts":[]}')
    pg.route("**/durations/pending-toasts", handle_toasts)
    time.sleep(3.5)  # 等轮询(1.2s)拿到注入数据 → _showDurationToast 弹
    pg.screenshot(path=f"{SHOTS}/05_toast_popup.png", full_page=True)
    toast_seen = pg.locator("text=不合格").count() > 0 or pg.locator("text=超过最长时间").count() > 0
    step("提示框真的弹出(前端自绘 toast DOM)", toast_seen,
         f"不合格={pg.locator('text=不合格').count()} 越线文案={pg.locator('text=超过最长时间').count()}")
    pg.unroute("**/durations/pending-toasts")

    dlog = [c for c in clogs if "fujian" in c or "提示框" in c or "事件列表" in c or "durations" in c]
    print("\n--- 插件 console 日志 ---", flush=True)
    for c in dlog[-10:]:
        print("  ", c, flush=True)

    ctx.close(); b.close()

failed = [x for x in logs if not x[1]]
print(f"\n{'='*50}\n通过 {len(logs)-len(failed)} / 失败 {len(failed)}", flush=True)
if failed:
    print("失败:", [f[0] for f in failed], flush=True)
