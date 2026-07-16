# -*- coding: utf-8 -*-
"""川南 2026-07-16 问题3 · 可见浏览器 UAT (v3.40 末步 OK/NG 闪烁修复验收)。

现场叙事 (客户原话: "最后一个步骤会在 ok 和 ng 来回闪烁, 最后判定最后一步 ng;
前面有步骤 ng 才出现, 全 ok 没这个问题"):
  1. 顺序项目 A-B-C-D, 操作员漏做 B (周期跑偏 → 前面有步骤 NG)。
  2. 装好的成品滞留画面, 末步 D 的标签被 YOLO 时有时无地反复识别。
  3. 旧行为: 周期已偏成 [A,C,D] (长度3), 位置指针错位指到期望第4位='D',
     D 的每次余像闪现都被误判"合法连续重复"再次入周期 → [A,C,D,D,...]
     → 前端末步结果列 OK/NG 来回闪, 结算多报"重复步骤 D", NG 账挂到末步。
  4. v3.40 修复: 周期偏离期望前缀后位置指针失效(后端) + 末步结果一旦给出
     即锁定不随余像回退(前端)。
  5. 预期: D 行结果列一路稳定 OK 不闪 NG; 结算原因只有"缺少 B"。

跑法(前置: 后端 RUNTIME_MODE=test + 前端 dev 已起):
  cd tests/uat && UAT_API=http://127.0.0.1:8013 UAT_FRONT=http://127.0.0.1:6003 \
    python uat_20260716_cn_last_step_flicker.py
"""
import os
import time

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, launch_browser, filter_console_errors

API = os.environ.get("UAT_API", "http://127.0.0.1:8001")
FRONT = os.environ.get("UAT_FRONT", "http://127.0.0.1:6001")
FPS = 30

run = UatRun("cn_last_step_flicker")


def fr(sec):
    return int(sec * FPS)


def seg(t0, t1, labels):
    return {"from": fr(t0), "to": fr(t1), "detections": [
        {"label": l, "confidence": 0.95,
         "bbox": [0.1 + 0.15 * i, 0.1, 0.2 + 0.15 * i, 0.3]}
        for i, l in enumerate(labels)]}


# 剧本: A ok → (B 漏做) → C ok → D ok → 成品滞留 D 反复闪现 → 下一周期 A 触发结算
TIMELINE = [
    seg(0, 2, ["A"]), seg(2, 2.5, []),
    seg(2.5, 4.5, ["C"]), seg(4.5, 5, []),
    seg(5, 7, ["D"]), seg(7, 8, []),
    seg(8, 8.3, ["D"]), seg(8.3, 9, []),
    seg(9, 9.3, ["D"]), seg(9.3, 10, []),
    seg(10, 10.3, ["D"]), seg(10.3, 11, []),
    seg(11, 11.3, ["D"]), seg(11.3, 12, []),
    seg(12, 14, []),
    seg(14, 16, ["A"]), seg(16, 40, []),
]


def setup_project():
    steps = [{"id": f"s{i+1}", "label": l, "threshold": 25, "min_frames": 3,
              "enabled": True, "color": "#1976d2"} for i, l in enumerate("ABCD")]
    pipeline = {"sequence_order": [{"step_id": s["id"]} for s in steps],
                "settlement_mode": "first_step", "settle_dedup": False}
    events = [
        {"id": 1, "name": "合格(OK)", "actions": [
            {"counter_name": "合格总数", "delta": 1},
            {"counter_name": "总产量", "delta": 1}]},
        {"id": 2, "name": "不合格(NG)", "actions": [
            {"counter_name": "不良总数", "delta": 1},
            {"counter_name": "总产量", "delta": 1}]},
    ]
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": f"CNFLK-{time.strftime('%H%M%S')}", "task_type": "detection",
        "logic_mode": "sequential", "steps_config": steps,
        "pipeline_config": pipeline, "events_config": events}, timeout=15)
    run.step("建顺序项目 A-B-C-D", r.status_code in (200, 201), f"HTTP {r.status_code}")
    pid = r.json()["id"]
    ar = requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=30)
    run.step("激活项目", ar.status_code == 200, f"HTTP {ar.status_code}")
    return pid


def main():
    run.step("后端就绪",
             requests.get(f"{API}/api/v1/projects", timeout=10).status_code == 200)
    run.step("前端就绪", requests.get(FRONT, timeout=10).status_code == 200)
    setup_project()

    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(4)

        # 起剧本 (用已激活真项目配置) + 检测
        r = requests.post(f"{API}/api/v1/test/synthetic/start", json={
            "scenario_json": {"name": "cn-flicker", "fps": FPS, "timeline": TIMELINE},
            "channel": 0, "with_project": False}, timeout=15)
        run.step("起 synthetic 剧本(漏B+末步余像闪现)", r.status_code == 200,
                 f"HTTP {r.status_code}")
        r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                          json={"conf": 0.25}, timeout=30)
        run.step("开始检测", r.status_code == 200, f"HTTP {r.status_code}")

        # 全程采样 D 行结果列, 记录状态序列
        d_states, t0 = [], time.time()
        while time.time() - t0 < 20:
            rows = page.evaluate("""() => {
                const trs = [...document.querySelectorAll('tbody tr')];
                for (const tr of trs) {
                    const tds = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
                    if (tds[1] === 'D') return tds.join(':');
                }
                return '';
            }""")
            if rows and (not d_states or d_states[-1] != rows):
                d_states.append(rows)
            time.sleep(0.2)
        run.shot(page, "01_周期尾部_D行应稳定OK")

        seq = " -> ".join(d_states)
        print(f"  D 行状态序列: {seq}")
        d_results = [s.split(":")[-1] for s in d_states]
        ng_count = d_results.count("NG")
        run.step("末步 D 全程未闪 NG", ng_count == 0, f"NG 出现 {ng_count} 次 | {seq}")
        # OK 一旦出现, 本周期内不得回退到 '--' (锁定语义)。
        # 周期结算后表格整体清回"待检测"属正常翻篇, 截断到清表前判定。
        try:
            first_ok = d_results.index("OK")
            in_cycle = []
            for s in d_states[first_ok:]:
                if "待检测" in s:
                    break  # 新周期清表, 之后不算
                in_cycle.append(s.split(":")[-1])
            regressed = any(v != "OK" for v in in_cycle)
        except ValueError:
            first_ok, regressed = -1, True
        run.step("末步 D 结果一旦 OK 即锁定不回退(本周期内)",
                 first_ok >= 0 and not regressed, f"first_ok_idx={first_ok}")

        # 结算核对: 总产量 1, NG 原因只有"缺少 B"
        d = requests.get(f"{API}/api/v1/source/detection/results?channel=0",
                         timeout=5).json()
        counters = d.get("counters") or {}
        run.step("周期已结算(总产量=1)", counters.get("总产量") == 1,
                 f"counters={ {k: v for k, v in counters.items() if not str(k).startswith('_')} }")
        run.step("判定为 NG(缺 B, 账不挂末步重复)", counters.get("不良总数") == 1)
        events = d.get("recent_events") or []
        reasons = [e.get("reason", "") for e in events]
        has_missing_b = any("缺少" in rs and "B" in rs for rs in reasons)
        has_dup_d = any("重复步骤" in rs and "D" in rs for rs in reasons)
        run.step("结算原因=缺少B", has_missing_b, f"reasons={reasons}")
        run.step("结算原因不含'重复步骤D'(老bug特征)", not has_dup_d, f"reasons={reasons}")

        real = filter_console_errors(console_errs)
        run.step("控制台无前端逻辑报错", not real, f"真报错={real[:3]}")
        ctx.close()
        browser.close()

    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=10)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=10)
    run.step("清理完成", True)
    return run.finish()


if __name__ == "__main__":
    raise SystemExit(main())
