# -*- coding: utf-8 -*-
"""LG 工时看板插件 UAT (T4/T5) — 可见浏览器验证。

现场叙事:
  领导打开监控页 → 整页被插件覆盖成工时看板 (KPI/Lean 堆叠/占比环/趋势/步骤统计);
  工程师到项目页逻辑设置 tab 给步骤选 VA/BVA/NVA → 落到
  Project.steps_config[i].plugin_data["lg-worktime"].value_type (T5 双向验证)。

前置:
  - 后端 8004 已启动且 lg-worktime 插件 active
  - 前端 6001 (baseURL 钉 8004)
  - /tmp/seed_lg_demo.py 已造好 7 天数据 (今日 22 轮全 OK)

跑法:
  cd tests/uat && python uat_lg_worktime_dashboard.py
"""
from __future__ import annotations

import json
import time
import urllib.request

from _common import UatRun, filter_console_errors, launch_browser
from playwright.sync_api import sync_playwright

FRONT = "http://localhost:6001"
BACK = "http://localhost:8004/api/v1"
CC = "lg-worktime"


def api_get(path: str):
    with urllib.request.urlopen(f"{BACK}{path}", timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    run = UatRun("lg_worktime_dashboard")
    summary = api_get(f"/plugins/{CC}/dashboard/summary")
    run.step("后端 summary 有今日数据", summary["total_cycles"] > 0,
             f"total={summary['total_cycles']} good={summary['good_cycles']}")

    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(p, record_video_dir=run.video_dir)

        # ---------- T4-1: 监控页被整页覆盖成工时看板 ----------
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        try:
            page.wait_for_selector(".lgwt-dashboard", timeout=25000)
            run.step("monitor.layout.body 已被插件看板覆盖", True)
        except Exception as e:
            run.shot(page, "00_dashboard_missing")
            run.step("monitor.layout.body 已被插件看板覆盖", False, str(e))
            ctx.close(); browser.close()
            return run.finish()
        time.sleep(4)  # 等轮询出数 + ECharts 动画
        run.shot(page, "01_dashboard_full")

        body = page.evaluate("document.body.innerText")

        # ---------- T4-2: KPI 与后端 API 对得上 ----------
        run.step("KPI 总轮次上屏", str(summary["total_cycles"]) in body,
                 f"期望含 {summary['total_cycles']}")
        yr = summary["yield_rate"]
        run.step("KPI 良率上屏", yr is None or f"{yr:g}" in body or f"{yr:.1f}" in body,
                 f"期望含 {yr}")
        ct_avg = summary["ct_avg"]
        run.step("KPI 平均CT上屏", ct_avg is None or f"{ct_avg:.1f}" in body,
                 f"期望含 {ct_avg:.1f}")
        run.step("LEAN 三类标签上屏",
                 all(k in body for k in ("VA", "BVA", "NVA")))

        # ---------- T4-3: ECharts 图表真的画出来了 ----------
        n_canvas = page.locator(".lgwt-dashboard canvas").count()
        run.step("ECharts 画布渲染 (堆叠图/占比环/趋势)", n_canvas >= 3,
                 f"canvas 数={n_canvas}")

        # ---------- T4-4: 步骤统计表出全 5 个步骤 ----------
        steps_api = api_get(f"/plugins/{CC}/dashboard/step-averages")
        labels = [s["label"] for s in steps_api["steps"]]
        present = [lb for lb in labels if lb in body]
        run.step("步骤统计表上屏", len(present) == len(labels) and len(labels) == 5,
                 f"api={len(labels)} 上屏={len(present)}")

        # ---------- T5: 项目页步骤价值单元格 → 落库双向验证 ----------
        projects = api_get("/projects").get("items", [])
        proj = next((x for x in projects if x["name"] == "LG装配工位A"), None)
        run.step("找到 UAT 项目", proj is not None)
        if proj:
            page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
            time.sleep(2)
            # 左侧项目卡片 (避开顶栏项目下拉里的同名文本)
            page.locator('.cursor-pointer:has-text("LG装配工位A")').first.click()
            time.sleep(1.5)
            page.locator('.el-tabs__item:has-text("步骤设置")').first.click()
            time.sleep(1.5)
            run.shot(page, "02_project_steps_tab")

            cells = page.locator("select.lgwt-cell-select")
            n_cells = cells.count()
            run.step("步骤表出现插件价值单元格", n_cells == 5, f"个数={n_cells}")

            if n_cells == 5:
                # fetch_part 当前 BVA (造数) → 改成 NVA
                before = api_get(f"/projects/{proj['id']}")
                vt_before = before["steps_config"][0]["plugin_data"][CC]["value_type"]
                cells.nth(0).select_option("NVA")
                time.sleep(2)
                run.shot(page, "03_value_changed_nva")
                after = api_get(f"/projects/{proj['id']}")
                vt_after = after["steps_config"][0]["plugin_data"][CC]["value_type"]
                run.step("T5 改价值 → GET 项目验证落库",
                         vt_before == "BVA" and vt_after == "NVA",
                         f"{vt_before} → {vt_after}")
                # 其它步骤的 plugin_data 不被误伤
                others_ok = all(
                    after["steps_config"][i]["plugin_data"][CC]["value_type"]
                    == before["steps_config"][i]["plugin_data"][CC]["value_type"]
                    for i in range(1, 5))
                run.step("其它步骤配置未被误伤", others_ok)
                # 还原
                cells.nth(0).select_option("BVA")
                time.sleep(2)
                restored = api_get(f"/projects/{proj['id']}")
                run.step("还原回 BVA 成功",
                         restored["steps_config"][0]["plugin_data"][CC]["value_type"] == "BVA")

        # ---------- 回看板收尾 ----------
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        try:
            page.wait_for_selector(".lgwt-dashboard", timeout=15000)
            time.sleep(3)
        except Exception:
            pass
        run.shot(page, "04_dashboard_final")

        real = filter_console_errors(console_errs)
        run.step("控制台无前端逻辑报错", not real, f"真报错={real[:3]}")

        ctx.close()
        browser.close()

    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
