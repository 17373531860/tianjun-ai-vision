"""可见浏览器 UAT — 场景E: v3.23.1 新功能 NG 补做缺步骤延迟落账.

前置(admin API): synthetic 跑 ng_missing_step (step_a/step_c 出, step_b 缺) + 项目开 ng_remediation
  → 顺序周期缺 step_b → NG → 延迟落账挂起 (不计数, 等人工)
浏览器(op001): 登录 → 监控页 → 人工确认遮罩显示「缺步骤 step_b」+ 三按钮
  → 点「补步骤 — 判合格」→ 该挂起周期信任补做判 OK 落账 (不重置)
断言: 遮罩出现三按钮 + 缺步骤含 step_b + 点击补步骤成功
"""
import os, time, json, requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://127.0.0.1:6001"
SHOTS = "/tmp/uat_E_shots"
os.makedirs(SHOTS, exist_ok=True)
ADMIN_U, ADMIN_P = "admin", "admin123456"
OP_U, OP_P = "op001", "op123456"


def admin_tok():
    return requests.post(f"{API}/auth/login", json={"username": ADMIN_U, "password": ADMIN_P}).json()["token"]


def ensure_pending(tok):
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    scen = json.load(open("tests/scenarios/ng_missing_step.json"))
    requests.post(f"{API}/test/synthetic/start", headers=H, json={
        "channel": 0, "scenario_json": scen, "with_project": True,
        "project_steps": ["step_a", "step_b", "step_c"], "logic_mode": "sequential",
        "project_id": 10, "ng_remediation": {"enabled": True, "allow_step": True, "allow_count": True},
        "fps": 60})
    requests.post(f"{API}/source/detection/start?channel=0", headers=H, json={"conf": 0.25, "iou": 0.45})
    for _ in range(20):
        d = requests.get(f"{API}/source/detection/results?channel=0", headers=H).json()
        pr = d.get("pending_remediation")
        if pr:
            print(f"[setup] 缺步挂起触发: missing={pr.get('missing')} cycle={pr.get('cycle_id')} reason={pr.get('reason')}")
            return True
        time.sleep(1)
    return False


def main():
    tok = admin_tok()
    if not ensure_pending(tok):
        print("[FAIL] 缺步挂起未触发"); return 1

    ok = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, slow_mo=300,
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(viewport={"width": 1600, "height": 1000})
        pg = ctx.new_page()
        pg.goto(f"{FRONT}/#/login", wait_until="domcontentloaded"); time.sleep(2.5)
        pg.fill('input[placeholder="请输入用户名"]', OP_U)
        pg.fill('input[placeholder="请输入密码"]', OP_P)
        pg.get_by_role("button", name="登录", exact=True).click(); time.sleep(2.5)
        pg.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded"); time.sleep(4)
        pg.screenshot(path=f"{SHOTS}/E1_overlay_three_buttons.png", full_page=True)
        body = pg.evaluate("document.body.innerText")
        c1 = "需要人工确认" in body
        c2 = "step_b" in body
        c3 = "补步骤" in body and "认 NG" in body and "重做" in body
        print(f"[chk] 遮罩出现: {c1} | 缺步骤含 step_b: {c2} | 三按钮(补步骤/认NG/重做): {c3}")
        ok += [c1, c2, c3]

        # 点「补步骤 — 判合格」
        clicked = False
        for sel in ['button:has-text("补步骤")', 'text=补步骤 — 判合格']:
            try:
                pg.click(sel, timeout=3000); clicked = True; break
            except Exception:
                continue
        print(f"[act] 点击「补步骤 — 判合格」: {clicked}")
        ok.append(clicked)
        time.sleep(2.5)
        pg.screenshot(path=f"{SHOTS}/E2_after_supplement.png", full_page=True)
        ctx.close(); b.close()

    print("\n[结果]", "PASS ✅" if all(ok) else "FAIL ❌", f"({sum(ok)}/{len(ok)})")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
