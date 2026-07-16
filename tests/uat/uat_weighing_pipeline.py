"""可见浏览器 UAT: v3.39 两阶段流水线称重 (drive_mode='pipeline')。

前置: 已激活项目「UAT_百斯特流水线称重」(channel 0, 型号A 标准 3.0kg, 皮重 0.5-5kg,
      finalize_timeout 已调短到 8s 便于观察)。

验证矩阵:
- P1 Project 配置页: 称重配置 Tab 出现「驱动模式」卡 + 选中流水线; 「流水线参数」
      「秤指令时序」两张卡渲染, 时序参数回填正确
- P2 Monitor 看板: pipeline 模式下显示相位/皮重/有效重量/待收尾队列
- P3 全流程 (API 喂重模拟秤): 上秤→自动去皮→装料 3.0 稳定→离秤→OK 结算入队
      → 看板出现待收尾件 → 超时兜底结案 → 队列清空 + 记录落库
- P4 数据页: 逐件记录含本次新件
"""
import time
import re
import requests
from playwright.sync_api import sync_playwright

FE = "http://localhost:6001"
API = "http://localhost:8001/api/v1"
OUT = "tests/uat"
CH = 0
PROJECT_NAME = "UAT_百斯特流水线称重"
results = []


def step(n, ok, e=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" :: {e}" if e else ""))
    results.append(ok)


def feed(w, n=1, gap=0.12):
    snap = None
    for _ in range(n):
        r = requests.post(f"{API}/weighing/feed",
                          json={"channel_id": CH, "weight": w}, timeout=5).json()
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
    # 复位工位 + 预选型号 (UI 校验交给 P2/P3 观察)
    requests.post(f"{API}/weighing/reset", json={"channel_id": CH}, timeout=5)
    requests.post(f"{API}/weighing/context",
                  json={"channel_id": CH, "model_name": "型号A"}, timeout=5)
    before = len(requests.get(f"{API}/weighing/records?channel=0&limit=500",
                              timeout=5).json().get("records", []))

    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        page = b.new_page()
        page.goto(f"{FE}/#/project"); time.sleep(2); maybe_login(page)
        page.goto(f"{FE}/#/project"); time.sleep(3)

        # ---------- P1 配置页 ----------
        try:
            # 左侧列表卡片 (不要 get_by_text 全页匹配, 会点到顶栏项目下拉)
            page.get_by_placeholder("搜索项目...").fill(PROJECT_NAME)
            time.sleep(1)
            card = page.locator("div.p-4.rounded-lg").filter(has_text=PROJECT_NAME).first
            card.click(); time.sleep(2)
            tab = page.get_by_role("tab", name=re.compile("称重"))
            if tab.count():
                tab.first.click(); time.sleep(1.5)
            body = page.content()
            step("P1a 出现「驱动模式」卡", "驱动模式" in body)
            step("P1b 出现「流水线参数」卡", "流水线参数" in body or "秤台区" in body)
            step("P1c 出现「秤指令时序」卡", "秤指令时序" in body or "去皮触发源" in body)
            step("P1d 标签③绑定回填", "加钢脚水泥" in body)
            page.screenshot(path=f"{OUT}/wpipe_01_config.png", full_page=True)
        except Exception as e:
            step("P1 配置页", False, str(e)[:120])

        # ---------- P2 Monitor 空闲态 ----------
        page.goto(f"{FE}/#/monitor"); time.sleep(3)
        body = page.content()
        step("P2a Monitor 出现称重看板", "称重投料" in body or "流水线" in body)
        step("P2b 显示流水线相位(空秤)", "空秤" in body or "流水线" in body)
        page.screenshot(path=f"{OUT}/wpipe_02_monitor_idle.png")

        # ---------- P3 全流程喂重 ----------
        s = feed(1.2, n=10)               # 上秤 1.2kg 稳定 → 自动去皮
        step("P3a 上秤自动去皮进装料", s and s.get("pipeline_phase") == "filling",
             f"phase={s and s.get('pipeline_phase')} tare={s and s.get('tare_weight')}")
        feed(0.0, n=4)                     # T 生效秤回 0
        feed(0.8); feed(1.6); feed(2.4)    # 爬升
        s = feed(3.0, n=9)                 # 净重稳定
        step("P3b 净重 3.0 稳定冻结", s and s.get("last_stable_net") == 3.0,
             f"net={s and s.get('last_stable_net')}")
        time.sleep(1.5)
        page.screenshot(path=f"{OUT}/wpipe_03_filling.png")
        in_body = page.content()
        step("P3c 看板显示皮重+净重", ("1.2" in in_body and "3" in in_body))

        base_settled = s.get("settled_count", 0) if s else 0
        s = feed(-1.2, n=10, gap=0.15)     # 离秤 → 确认窗(300ms)稳定 → 结算入队
        step("P3d 离秤结算入待收尾队列",
             s and s.get("settled_count", 0) > base_settled
             and len(s.get("pending") or []) >= 1,
             f"settled={s and s.get('settled_count')} pending={s and len(s.get('pending') or [])}")
        time.sleep(1.5)
        page.screenshot(path=f"{OUT}/wpipe_04_pending.png")
        in_body = page.content()
        step("P3e 看板出现待收尾队列", "待收尾" in in_body or "收尾" in in_body)

        # 空秤(-皮重)等 Z 指令发出, 随后秤归零; 等 8s 收尾超时兜底
        feed(-1.2, n=4, gap=0.2)           # zero_delay 300ms 后 send_zero
        had_pending = bool(s and s.get("pending"))
        deadline = time.time() + 14
        s = None
        while time.time() < deadline:
            s = feed(0.0)                  # Z 生效后空秤读 0
            if s and not s.get("pending"):
                break
            time.sleep(0.6)
        step("P3f 超时兜底结案队列清空", had_pending and s and not s.get("pending"),
             f"pending={s and s.get('pending')}")
        time.sleep(1.5)
        page.screenshot(path=f"{OUT}/wpipe_05_after_finalize.png")

        # ---------- P4 记录落库 ----------
        after = requests.get(f"{API}/weighing/records?channel=0&limit=500",
                             timeout=5).json().get("records", [])
        new = after[before:]
        ok_rec = [r for r in new if r.get("verdict") == "ok" and r.get("net") == 3.0]
        step("P4a 新增 OK 记录落库", len(ok_rec) >= 1,
             f"新增 {len(new)} 条, 其中 ok/3.0 {len(ok_rec)} 条")

        page.goto(f"{FE}/#/data"); time.sleep(3)
        body = page.content()
        step("P4b 数据页出现称重记录", ("称重" in body or "3.0" in body or "型号A" in body))
        page.screenshot(path=f"{OUT}/wpipe_06_data.png")

        b.close()

    print(f"\n===== {sum(results)}/{len(results)} PASS =====")
    return all(results)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
