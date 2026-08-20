# -*- coding: utf-8 -*-
"""UAT: 捷昌整改批次 (v3.49 WS1~WS5) 可见浏览器验收 — SQLite 主场.

覆盖 (对应 T7 验收矩阵):
  Phase A — API 契约: scan_pair 新码先上屏开关 / MES 并发派发 / 网关重试预算 /
            集群上报超时+异步化 / db-info / slave 断连时上报链路状态可观测
  Phase B — 可见浏览器 (headless=False + 录像): Monitor 单工位 + 6 工位网格,
            Settings 双开关 + 数据库卡片, MES 全 tab 巡检, Gateway 弹窗重试预算,
            ClusterPanel slave 上报状态区, Data 数据中心
  Phase C — 收尾清理 + 控制台报错断言

PG 专项 (数据库卡片显示 PostgreSQL / Data 页可用) 由
uat_20260812_jc_overhaul_pg.py 单独跑 (需切后端 DATABASE_URL).

前置: 后端 8001 (RUNTIME_MODE=test) + 前端 6001 已启动。
"""
from __future__ import annotations

import time
import uuid

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, launch_browser, filter_console_errors

API = "http://localhost:8001"
FRONT = "http://localhost:6001"
V1 = f"{API}/api/v1"

run = UatRun("jc_overhaul")
step = run.step


# ==================== Phase A: API 契约 ====================

def phase_a():
    # A1 scan_pair 新码先上屏: 默认开 + 写读回滚
    r = requests.get(f"{V1}/scanner/scan-pair/new-code-first", timeout=5)
    step("A1a 新码先上屏默认开", r.status_code == 200 and r.json().get("enabled") is True,
         f"resp={r.json()}")
    requests.put(f"{V1}/scanner/scan-pair/new-code-first", json={"enabled": False}, timeout=5)
    off = requests.get(f"{V1}/scanner/scan-pair/new-code-first", timeout=5).json()
    step("A1b 关闭后回读为关", off.get("enabled") is False)
    requests.put(f"{V1}/scanner/scan-pair/new-code-first", json={"enabled": True}, timeout=5)
    on = requests.get(f"{V1}/scanner/scan-pair/new-code-first", timeout=5).json()
    step("A1c 恢复开", on.get("enabled") is True)

    # A2 MES 外推并发派发: 默认开
    r = requests.get(f"{V1}/mes/gateway/async-dispatch", timeout=5)
    step("A2 MES 并发派发默认开", r.status_code == 200 and r.json().get("enabled") is True,
         f"resp={r.json() if r.status_code == 200 else r.status_code}")

    # A3 网关连接 retry_budget_sec 持久化 (与前端 GatewayPanel 一致, 存 config JSON 内)
    payload = {
        "name": f"__uat_gw_{uuid.uuid4().hex[:6]}", "adapter_type": "http_post",
        "config": {"url": "http://127.0.0.1:9/dead", "retry_budget_sec": 30},
        "enabled": False,
    }
    r = requests.post(f"{V1}/mes/gateway/connections", json=payload, timeout=5)
    ok = r.status_code in (200, 201)
    gw_id = r.json().get("id") if ok else None
    if ok:
        got = requests.get(f"{V1}/mes/gateway/connections/{gw_id}", timeout=5).json()
        step("A3 网关重试预算 30s 落库回读",
             (got.get("config") or {}).get("retry_budget_sec") == 30,
             f"got={(got.get('config') or {}).get('retry_budget_sec')}")
        requests.delete(f"{V1}/mes/gateway/connections/{gw_id}", timeout=5)
    else:
        step("A3 网关重试预算 30s 落库回读", False, f"create failed {r.status_code}: {r.text[:120]}")

    # A4 集群上报配置: report_timeout_sec / report_async 写读还原
    orig = requests.get(f"{V1}/cluster/config", timeout=5).json()
    requests.put(f"{V1}/cluster/config",
                 json={"report_timeout_sec": 7, "report_async": True}, timeout=5)
    got = requests.get(f"{V1}/cluster/config", timeout=5).json()
    step("A4 集群上报超时/异步化落库", got.get("report_timeout_sec") == 7
         and got.get("report_async") is True,
         f"timeout={got.get('report_timeout_sec')} async={got.get('report_async')}")

    # A5 db-info (SQLite 主场)
    info = requests.get(f"{V1}/system/db-info", timeout=5).json()
    step("A5 db-info dialect=sqlite + location 非空",
         info.get("dialect") == "sqlite" and bool(info.get("location")),
         f"dialect={info.get('dialect')}")

    # A6 slave 断连可观测: 切 slave + master 指死端口 → report-status 可读
    requests.put(f"{V1}/cluster/config", json={
        "role": "slave", "master_url": "http://127.0.0.1:9",
    }, timeout=5)
    st = requests.get(f"{V1}/cluster/report-status", timeout=5)
    body = st.json() if st.status_code == 200 else {}
    step("A6 slave 上报链路状态端点可读 (queued/spooled/replayed 字段)",
         st.status_code == 200 and all(
             k in body for k in ("queued", "spooled", "spool_replayed_total")),
         f"status={st.status_code} body_keys={sorted(body.keys())[:8]}")
    return orig


# ==================== Phase B: 可见浏览器 ====================

def phase_b(orig_cluster_cfg):
    # 前置: 激活一个 __uat_ 项目, 避免 Data/Alarm 空占位
    pname = f"__uat_jc_{uuid.uuid4().hex[:6]}"
    p = requests.post(f"{V1}/projects", json={
        "name": pname, "task_type": "detection", "logic_mode": "sequential",
        "steps_config": [
            {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
            {"id": 2, "label": "step_b", "name": "步骤B", "enabled": True}],
        "events_config": [
            {"id": 1, "name": "合格", "type": "ok", "enabled": True},
            {"id": 2, "name": "NG", "type": "ng", "enabled": True}],
    }, timeout=10).json()
    requests.post(f"{V1}/projects/{p['id']}/activate", timeout=10)

    with sync_playwright() as pw:
        browser, ctx, page, console_errs = launch_browser(
            pw, record_video_dir=run.video_dir)

        # ---- B1 Monitor 单工位 ----
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(3)
        run.shot(page, "B1_monitor_single")
        body = page.evaluate("document.body.innerText")
        step("B1 Monitor 单工位渲染", ("开始" in body or "停止" in body) and "运行时间" in body)

        # ---- B2 Monitor 6 工位网格总览 + 放大 ----
        # (多工位页有持续轮询, networkidle 永不满足, 只等 DOM + 固定 sleep)
        requests.post(f"{V1}/workstations/mode", json={"channel_count": 6}, timeout=10)
        page.reload(wait_until="domcontentloaded")
        time.sleep(3.5)
        run.shot(page, "B2a_monitor_grid6")
        step("B2a 6 工位总览网格", page.locator("text=多工位总览").count() == 1
             and page.locator("text=/^工位\\d+/").count() == 6)
        page.locator("text=/^工位5/").first.click()
        time.sleep(1.5)
        run.shot(page, "B2b_monitor_zoom_ch5")
        step("B2b 点卡片放大单路", page.get_by_role(
            "button", name="‹ 返回总览").count() == 1)
        page.get_by_role("button", name="‹ 返回总览").click(); time.sleep(1)
        requests.post(f"{V1}/workstations/mode", json={"channel_count": 1}, timeout=10)

        # ---- B3 Settings 显示设置双开关 (真点一遍 + 后端回读) ----
        page.goto(f"{FRONT}/#/settings", wait_until="domcontentloaded")
        time.sleep(2.5)
        sw = page.locator("[data-testid='scan-pair-new-first-switch']")
        sw.scroll_into_view_if_needed()
        run.shot(page, "B3a_settings_switches")
        step("B3a 两个新开关可见",
             sw.is_visible() and
             page.locator("[data-testid='mes-async-dispatch-switch']").is_visible())
        sw.click(); time.sleep(1)
        after = requests.get(f"{V1}/scanner/scan-pair/new-code-first", timeout=5).json()
        step("B3b UI 点开关 → 后端变关", after.get("enabled") is False)
        sw.click(); time.sleep(1)
        back = requests.get(f"{V1}/scanner/scan-pair/new-code-first", timeout=5).json()
        step("B3c 再点恢复开", back.get("enabled") is True)

        # ---- B4 Settings 性能设置数据库卡片 ----
        page.locator(".el-tabs__item:has-text('性能设置')").first.click()
        time.sleep(1.5)
        tag = page.locator("[data-testid='db-dialect-tag']")
        tag.scroll_into_view_if_needed()
        run.shot(page, "B4_settings_dbcard")
        step("B4 数据库卡片显示 SQLite", tag.inner_text().strip() == "SQLite")

        # ---- B5 MES 全 tab 巡检 ----
        page.goto(f"{FRONT}/#/mes", wait_until="domcontentloaded")
        time.sleep(3)
        tabs = ["工单管理", "工件追溯", "缺陷分析", "扫码器", "外部对接",
                "工单拉取", "工单接收", "外部设备", "PLC 对接", "集群汇总"]
        all_ok = True
        for t in tabs:
            try:
                page.locator(f"button:has-text('{t}')").first.click(timeout=5000)
                time.sleep(1.2)
                run.shot(page, f"B5_mes_{t}")
            except Exception as e:
                all_ok = False
                step(f"B5 MES tab「{t}」", False, str(e)[:100])
        step("B5 MES 全 10 tab 可进入", all_ok)

        # ---- B6 Gateway 弹窗: 重试预算字段 ----
        page.locator("button:has-text('外部对接')").first.click(); time.sleep(1.2)
        page.locator("button:has-text('新建连接')").first.click(); time.sleep(1)
        budget = page.locator("[data-testid='gw-retry-budget'] input")
        budget.scroll_into_view_if_needed()
        run.shot(page, "B6_gateway_retry_budget")
        step("B6 网关弹窗重试预算输入框", budget.is_visible()
             and budget.input_value() in ("0", ""))
        page.keyboard.press("Escape"); time.sleep(0.6)

        # ---- B7 ClusterPanel slave 上报链路状态区 (A6 已设 slave+死主机) ----
        page.locator("button:has-text('集群汇总')").first.click(); time.sleep(1.5)
        status_area = page.locator("[data-testid='cluster-report-status']")
        status_area.scroll_into_view_if_needed()
        run.shot(page, "B7_cluster_report_status")
        stext = status_area.inner_text() if status_area.count() else ""
        step("B7 slave 上报链路状态区可见 (队列/积压/补发)",
             all(k in stext for k in ("内存队列", "落盘积压", "已补发")), stext[:80])

        # ---- B8 Data 数据中心 ----
        page.goto(f"{FRONT}/#/data", wait_until="domcontentloaded")
        time.sleep(3)
        run.shot(page, "B8_data_center")
        body = page.evaluate("document.body.innerText")
        step("B8 数据中心渲染", "会话" in body or "Session" in body or "导出" in body)

        # ---- B9 控制台无真报错 ----
        # 「Channel N not configured」是 B2 降工位时前端轮询打到裁撤中通道的
        # 瞬时 404 (多工位 → 单工位切换竞态), 属预期噪声
        real = filter_console_errors(console_errs)
        real = [e for e in real if "infer-once" not in str(e)
                and "not configured" not in str(e)]
        step("B9 控制台无前端逻辑报错", not real, f"真报错={real[:3]}")

        ctx.close(); browser.close()

    # 收尾: 还原集群配置 + 删 __uat_ 项目
    requests.put(f"{V1}/cluster/config", json={
        "role": orig_cluster_cfg.get("role", "standalone"),
        "master_url": orig_cluster_cfg.get("master_url", ""),
        "report_timeout_sec": orig_cluster_cfg.get("report_timeout_sec", 10),
        "report_async": orig_cluster_cfg.get("report_async", True),
    }, timeout=5)
    plist = requests.get(f"{V1}/projects", timeout=5).json()
    if isinstance(plist, dict):
        plist = plist.get("items") or []
    for proj in plist:
        if isinstance(proj, dict) and (proj.get("name") or "").startswith("__uat_"):
            requests.delete(f"{V1}/projects/{proj['id']}", timeout=5)


if __name__ == "__main__":
    orig_cfg = phase_a()
    phase_b(orig_cfg)
    raise SystemExit(run.finish())
