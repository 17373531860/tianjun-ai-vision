# -*- coding: utf-8 -*-
"""UAT: 捷昌整改批次 (v3.49 WS5) 可见浏览器验收 — PostgreSQL 专项.

与 uat_20260812_jc_overhaul.py (SQLite 主场) 配对。本脚本验证后端切
DATABASE_URL=postgresql 后的专项面:
  Phase A — API 契约: db-info dialect=postgresql / 真迁移数据可读 (projects) /
            KV 配置读写 (scan-pair 开关) / 集群配置读写 / 网关连接 CRUD
  Phase B — 可见浏览器 (headless=False + 录像): Settings 数据库卡片显示
            PostgreSQL, Data 数据中心可用, Monitor 渲染, MES 关键 tab,
            控制台无真报错

前置:
  1. PG 16 在 127.0.0.1:5433, 库 tianjun_uat 已经 alembic 建表 +
     scripts/db/sqlite_to_pg.py 从 backend/sql_app.db 真迁移 + --verify 通过
  2. 后端 8001 以 DATABASE_URL=postgresql+psycopg2://tianjun@127.0.0.1:5433/tianjun_uat 启动
  3. 前端 6001 已启动
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

run = UatRun("jc_overhaul_pg")
step = run.step


# ==================== Phase A: API 契约 (PG 后端) ====================

def phase_a():
    # A1 db-info 必须是 postgresql 且指向 UAT 库
    info = requests.get(f"{V1}/system/db-info", timeout=5).json()
    step("A1 db-info dialect=postgresql + 指向 tianjun_uat",
         info.get("dialect") == "postgresql" and info.get("connected") is True
         and "tianjun_uat" in (info.get("location") or ""),
         f"dialect={info.get('dialect')} ver={info.get('server_version')} "
         f"loc={info.get('location')}")

    # A2 真迁移数据可读: projects 列表非空 (来自 sqlite_to_pg 搬迁的开发库数据)
    plist = requests.get(f"{V1}/projects", timeout=10).json()
    items = plist.get("items") if isinstance(plist, dict) else plist
    items = items or []
    migrated = [p for p in items if not (p.get("name") or "").startswith("__uat_")]
    step("A2 迁移数据可读 (projects 非空且 JSON 配置结构化)",
         len(migrated) >= 1 and isinstance(
             (migrated[0].get("steps_config") or []), list),
         f"count={len(migrated)} first={migrated[0].get('name') if migrated else None}")

    # A3 PG 上 KV 配置写读: scan-pair 新码先上屏开关
    requests.put(f"{V1}/scanner/scan-pair/new-code-first",
                 json={"enabled": False}, timeout=5)
    off = requests.get(f"{V1}/scanner/scan-pair/new-code-first", timeout=5).json()
    requests.put(f"{V1}/scanner/scan-pair/new-code-first",
                 json={"enabled": True}, timeout=5)
    on = requests.get(f"{V1}/scanner/scan-pair/new-code-first", timeout=5).json()
    step("A3 PG 上 KV 开关写读回滚", off.get("enabled") is False
         and on.get("enabled") is True)

    # A4 PG 上集群配置写读还原
    orig = requests.get(f"{V1}/cluster/config", timeout=5).json()
    requests.put(f"{V1}/cluster/config",
                 json={"report_timeout_sec": 9, "report_async": True}, timeout=5)
    got = requests.get(f"{V1}/cluster/config", timeout=5).json()
    step("A4 PG 上集群上报配置落库", got.get("report_timeout_sec") == 9
         and got.get("report_async") is True,
         f"timeout={got.get('report_timeout_sec')}")
    requests.put(f"{V1}/cluster/config", json={
        "report_timeout_sec": orig.get("report_timeout_sec", 10),
        "report_async": orig.get("report_async", True),
    }, timeout=5)

    # A5 PG 上网关连接 CRUD (config JSON 字段含 retry_budget_sec)
    payload = {
        "name": f"__uat_pggw_{uuid.uuid4().hex[:6]}", "adapter_type": "http_post",
        "config": {"url": "http://127.0.0.1:9/dead", "retry_budget_sec": 45},
        "enabled": False,
    }
    r = requests.post(f"{V1}/mes/gateway/connections", json=payload, timeout=5)
    ok = r.status_code in (200, 201)
    gw_id = r.json().get("id") if ok else None
    got_budget = None
    if ok:
        got = requests.get(f"{V1}/mes/gateway/connections/{gw_id}", timeout=5).json()
        got_budget = (got.get("config") or {}).get("retry_budget_sec")
        requests.delete(f"{V1}/mes/gateway/connections/{gw_id}", timeout=5)
    step("A5 PG 上网关连接 CRUD + JSON config 回读", ok and got_budget == 45,
         f"create={r.status_code} budget={got_budget}")

    # A6 数据中心会话查询在 PG 上可用 (方言兼容 sql_compat 面)
    r = requests.get(f"{V1}/data/sessions", params={"page": 1, "page_size": 5},
                     timeout=10)
    step("A6 PG 上 /data/sessions 查询可用", r.status_code == 200,
         f"status={r.status_code} body={str(r.text)[:80]}")


# ==================== Phase B: 可见浏览器 ====================

def phase_b():
    # 前置: 在 PG 上创建并激活一个 __uat_ 项目 (Data 页需要激活项目才出数据区)
    pname = f"__uat_pg_{uuid.uuid4().hex[:6]}"
    p = requests.post(f"{V1}/projects", json={
        "name": pname, "task_type": "detection", "logic_mode": "sequential",
        "steps_config": [
            {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True}],
        "events_config": [
            {"id": 1, "name": "合格", "type": "ok", "enabled": True}],
    }, timeout=10).json()
    requests.post(f"{V1}/projects/{p['id']}/activate", timeout=10)

    with sync_playwright() as pw:
        browser, ctx, page, console_errs = launch_browser(
            pw, record_video_dir=run.video_dir)

        # ---- B1 Settings 性能设置数据库卡片显示 PostgreSQL ----
        page.goto(f"{FRONT}/#/settings", wait_until="domcontentloaded")
        time.sleep(2.5)
        page.locator(".el-tabs__item:has-text('性能设置')").first.click()
        time.sleep(1.5)
        tag = page.locator("[data-testid='db-dialect-tag']")
        tag.scroll_into_view_if_needed()
        run.shot(page, "B1_settings_dbcard_pg")
        card_text = page.locator("[data-testid='db-info-card']").inner_text() \
            if page.locator("[data-testid='db-info-card']").count() else \
            page.evaluate("document.body.innerText")
        step("B1 数据库卡片显示 PostgreSQL + 版本/位置",
             tag.inner_text().strip() == "PostgreSQL"
             and "tianjun_uat" in card_text,
             f"tag={tag.inner_text().strip()!r}")

        # ---- B2 Data 数据中心在 PG 上可用 ----
        page.goto(f"{FRONT}/#/data", wait_until="domcontentloaded")
        time.sleep(3)
        run.shot(page, "B2_data_center_pg")
        body = page.evaluate("document.body.innerText")
        step("B2 数据中心渲染 (PG 后端)",
             "会话" in body or "Session" in body or "导出" in body)

        # ---- B3 Monitor 渲染 (PG 后端) ----
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(3)
        run.shot(page, "B3_monitor_pg")
        body = page.evaluate("document.body.innerText")
        step("B3 Monitor 渲染 (PG 后端)",
             ("开始" in body or "停止" in body) and "运行时间" in body)

        # ---- B4 MES 关键 tab (工单管理 / 外部对接 / 集群汇总) ----
        page.goto(f"{FRONT}/#/mes", wait_until="domcontentloaded")
        time.sleep(3)
        all_ok = True
        for t in ["工单管理", "外部对接", "集群汇总"]:
            try:
                page.locator(f"button:has-text('{t}')").first.click(timeout=5000)
                time.sleep(1.2)
                run.shot(page, f"B4_mes_{t}_pg")
            except Exception as e:
                all_ok = False
                step(f"B4 MES tab「{t}」(PG)", False, str(e)[:100])
        step("B4 MES 关键 tab 可进入 (PG 后端)", all_ok)

        # ---- B5 PG 写入项目在 UI 可见 (写路径全链路) ----
        page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
        time.sleep(2.5)
        run.shot(page, "B5_project_list_pg")
        visible = page.locator(f"text={pname}").count() >= 1
        step("B5 PG 写入项目在 UI 可见", visible and p.get("id"))

        # ---- B6 控制台无真报错 ----
        real = filter_console_errors(console_errs)
        real = [e for e in real if "not configured" not in str(e)]
        step("B6 控制台无前端逻辑报错 (PG 后端)", not real, f"真报错={real[:3]}")

        ctx.close(); browser.close()

    # 收尾: 删 __uat_ 项目
    requests.delete(f"{V1}/projects/{p['id']}", timeout=5)


if __name__ == "__main__":
    phase_a()
    phase_b()
    raise SystemExit(run.finish())
