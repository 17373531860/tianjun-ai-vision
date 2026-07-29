"""UAT: v3.45 作业员从用户名单选择（可选项） — 可见浏览器人眼验收。

场景（萍乡百斯特 2026-07-25）: 客户要求回传达梦的 OPERATOR 可强制从软件维护的
用户名单里选（新增/禁用/删除在 设置→用户与权限），落地为称重配置·前置要求开关。

走查路径:
  1. 项目页 → 称重配置 → 前置要求: 看到新开关「作业员从用户名单选择」→ UI 打开 → 保存
  2. 激活项目 → 监控页: "人员"变为下拉, 展开可见用户名单
  3. 选中一人 → 确定人员/型号 → 底部上下文与引擎状态都是所选人
  4. 对照组: 未开开关的称重项目, 人员仍为自由填写输入框

证据: tests/uat/evidence_20260725_operator_from_users/*.png
运行: ~/anaconda3/envs/tianjun/bin/python tests/uat/uat_20260725_operator_from_users.py
前置: main 后端 8001 + 前端 6001 已启动
"""
import os
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

API = "http://localhost:8001"
FRONT = "http://localhost:6001"
EVD = os.path.join(os.path.dirname(__file__), "evidence_20260725_operator_from_users")
os.makedirs(EVD, exist_ok=True)


def make_project(flag=None):
    weighing = {"enabled": True, "materials": ["钢帽水泥"]}
    if flag is not None:
        weighing["operator_from_users"] = flag
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": f"__uat_op_{'on' if flag else 'off'}_{uuid.uuid4().hex[:5]}",
        "task_type": "detection", "logic_mode": "weighing",
        "pipeline_config": {"weighing": weighing},
        "steps_config": [],
        "events_config": [{"id": 1, "name": "合格", "type": "ok", "enabled": True}],
        "counters_config": [], "alarm_config": {}, "detection_config": {},
        "data_config": {}, "default_model_id": None, "model_format": "pytorch_fp32",
    }, timeout=10)
    r.raise_for_status()
    return r.json()


def main():
    ok, fail = [], []
    projects = requests.get(f"{API}/api/v1/projects", timeout=10).json()["items"]
    orig_active = next((p["id"] for p in projects if p.get("is_active")), None)
    p_on = make_project(None)   # 从 UI 打开开关, 验证保存链路
    p_off = make_project(None)  # 对照组保持默认关
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False, slow_mo=250)
            page = browser.new_page(viewport={"width": 1600, "height": 900})

            # ---- 1. 项目页开开关并保存 ----
            page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
            page.wait_for_selector("text=项目管理", timeout=10000)
            time.sleep(1.5)
            page.get_by_text(p_on["name"], exact=False).first.click()
            time.sleep(1)
            page.locator(".el-tabs__item:has-text('称重配置')").first.click()
            time.sleep(1)
            row = page.locator("div.flex:has-text('作业员从用户名单选择')").last
            row.scroll_into_view_if_needed()
            page.screenshot(path=f"{EVD}/01_config_switch.png")
            row.locator(".el-switch").first.click()
            time.sleep(0.5)
            page.locator("button:has-text('保存配置')").click()
            time.sleep(2)
            w = (requests.get(f"{API}/api/v1/projects/{p_on['id']}", timeout=10)
                 .json().get("pipeline_config") or {}).get("weighing") or {}
            (ok if w.get("operator_from_users") is True else fail).append(
                f"T1 配置开关保存落库 = {w.get('operator_from_users')}")

            # ---- 2. 监控页下拉 ----
            requests.post(f"{API}/api/v1/projects/{p_on['id']}/activate",
                          timeout=30).raise_for_status()
            expected = requests.get(f"{API}/api/v1/weighing/operators",
                                    timeout=10).json()["operators"]
            page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
            page.reload(wait_until="domcontentloaded")  # hash 跳转不刷 store, 强制整页加载
            time.sleep(3.5)
            # 按组件类名定位 (引擎会保留上次人员, 有值时占位符不显示, 不能按占位符定位)
            sel = page.locator(".weighing-op-select")
            (ok if sel.count() > 0 else fail).append(
                f"T2 人员渲染为名单下拉 (count={sel.count()})")
            if sel.count() and expected:
                sel.first.click()
                time.sleep(1)
                page.screenshot(path=f"{EVD}/02_monitor_dropdown_open.png")
                opts = page.locator(".el-select-dropdown__item:visible")
                texts = [opts.nth(i).inner_text().strip() for i in range(opts.count())]
                missing = [n for n in expected if n not in texts]
                (ok if not missing else fail).append(
                    f"T3 下拉选项与用户名单一致 (名单={expected}, 下拉={texts})")

                # ---- 3. 选中 → 确定 → 引擎上下文 ----
                opts.first.click()
                time.sleep(0.5)
                page.locator("button:has-text('确定人员/型号')").click()
                time.sleep(1.5)
                page.screenshot(path=f"{EVD}/03_context_applied.png")
                snap = requests.get(f"{API}/api/v1/weighing/state",
                                    params={"channel": 0}, timeout=10).json()
                (ok if snap.get("operator") == expected[0] else fail).append(
                    f"T4 引擎上下文 operator = {snap.get('operator')} (期望 {expected[0]})")

            # ---- 4. 对照组: 默认关 = 自由填写 ----
            requests.post(f"{API}/api/v1/projects/{p_off['id']}/activate",
                          timeout=30).raise_for_status()
            page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
            page.reload(wait_until="domcontentloaded")
            time.sleep(3.5)
            free = page.locator(".weighing-op-input")
            page.screenshot(path=f"{EVD}/04_default_off_free_input.png")
            (ok if free.count() > 0 else fail).append(
                f"T5 默认关仍为自由填写输入框 (count={free.count()})")

            browser.close()
    finally:
        if orig_active:
            requests.post(f"{API}/api/v1/projects/{orig_active}/activate", timeout=30)
        for p in (p_on, p_off):
            requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=10)

    print("\n===== UAT 结果 =====")
    for line in ok:
        print("  PASS", line)
    for line in fail:
        print("  FAIL", line)
    print(f"===== {len(ok)} PASS / {len(fail)} FAIL, 证据在 {EVD} =====")
    raise SystemExit(1 if fail else 0)


if __name__ == "__main__":
    main()
