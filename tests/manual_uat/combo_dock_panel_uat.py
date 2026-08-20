# -*- coding: utf-8 -*-
"""判型实时看板「停靠化」改版 — 可见浏览器 UAT (headless=False).

背景 (2026-08-13): 老板反馈画面中间悬浮的大字卡压画面不美观, 圈了底部
SOP 流程卡片条右侧空档要求放那里。改版后 live_display 渲染为与 SOP 卡片
同行右侧停靠的「判型实时看板」(非 absolute 悬浮层), 配置键不变
(position 保留但失效)。

验证点:
  1. 看板在视频容器**外部**下方 (DOM: 不含 absolute 类, y 坐标 > 视频底边);
  2. 看板与 SOP 流程卡片同一行, 停靠在其右侧;
  3. 每个判型标签一块瓦片大号计数, 缸型琥珀瓦片在最右;
  4. mock PLC 切缸型 4→6, 瓦片机型标记即时刷新;
  5. 截图证据给老板过目。

跑法 (先起 backend 8002 [RUNTIME_MODE=test] + frontend 6002):
  python tests/manual_uat/combo_dock_panel_uat.py
证据落地: tests/manual_uat/evidence/combo_dock_panel_<date>/
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

API = "http://localhost:8002"
BASE = f"http://localhost:{sys.argv[1] if len(sys.argv) > 1 else '6002'}"
NAME = "__uat_停靠看板"
PLC_NAME = "__uat_虚拟PLC停靠"
CH = 0

EVIDENCE = Path(__file__).parent / "evidence" / f"combo_dock_panel_{datetime.now():%Y-%m-%d}"
EVIDENCE.mkdir(parents=True, exist_ok=True)

verdict = {"steps": [], "pass": True}


def step(name: str, ok: bool, detail: str = ""):
    verdict["steps"].append({"name": name, "ok": ok, "detail": detail})
    verdict["pass"] = verdict["pass"] and ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def cleanup():
    requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=5)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel={CH}", timeout=5)
    payload = requests.get(f"{API}/api/v1/projects", timeout=5).json()
    for p in (payload.get("items") if isinstance(payload, dict) else payload) or []:
        if (p.get("name") or "").startswith("__uat_"):
            requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=5)
    conns = requests.get(f"{API}/api/v1/plc/connections", timeout=5).json()
    for c in conns.get("connections") or []:
        if (c.get("name") or "").startswith("__uat_"):
            requests.delete(f"{API}/api/v1/plc/connections/{c['id']}", timeout=5)


def _det(label, x, y):
    return {"label": label, "confidence": 0.92, "bbox": [x, y, 0.15, 0.15]}


def _scn_live():
    """区A 两个位置 + 区B 一个位置锁定后长驻 (看板计数 2/1, 不结算)。"""
    return {"name": "dock_live", "fps": 60, "timeline": [
        {"from": 0, "to": 9, "detections": []},
        {"from": 10, "to": 129, "detections": [_det("区A", 0.1, 0.1)]},
        {"from": 130, "to": 149, "detections": []},
        {"from": 150, "to": 269, "detections": [_det("区A", 0.4, 0.1)]},
        {"from": 270, "to": 289, "detections": []},
        {"from": 290, "to": 409, "detections": [_det("区B", 0.7, 0.5)]},
        {"from": 410, "to": 14400, "detections": []},
    ]}


def _counters():
    body = requests.get(f"{API}/api/v1/source/detection/results?channel={CH}",
                        timeout=5).json()
    return body.get("counters") or {}, body


def _cv(body):
    return body.get("combo_verdict") or {}


def _wait(pred, timeout=60):
    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        _, body = _counters()
        try:
            if pred(body):
                break
        except Exception:
            pass
        time.sleep(0.4)
    time.sleep(1.2)  # 留轮询拍给前端渲染
    return body


def main() -> int:
    cleanup()

    # ---- mock PLC (缸型点位 cyl_type=4) ----
    r = requests.post(f"{API}/api/v1/plc/connections", json={
        "name": PLC_NAME, "driver": "mock", "enabled": True,
        "conn_params": {"store_id": "uat_dock", "poll_interval_ms": 50},
        "points": [{"key": "cyl_type", "addr": "m2", "type": "int16", "dir": "read"}],
        "read_rules": [], "write_rules": [], "options": {},
    }, timeout=10)
    r.raise_for_status()
    plc_id = r.json()["id"]
    requests.post(f"{API}/api/v1/plc/connections/{plc_id}/mock-set",
                  json={"point": "cyl_type", "value": 4}, timeout=5).raise_for_status()

    # ---- 项目: 判定表 + live_display 开 ----
    r = requests.post(f"{API}/api/v1/projects", json={
        "name": NAME, "task_type": "detection", "logic_mode": "detection",
        "steps_config": [
            {"id": 1, "label": "区A", "name": "区域A", "enabled": True, "min_frames": 1, "threshold": 30},
            {"id": 2, "label": "区B", "name": "区域B", "enabled": True, "min_frames": 1, "threshold": 30},
            {"id": 3, "label": "收尾", "name": "收尾", "enabled": True, "min_frames": 1, "threshold": 30},
        ],
        "pipeline_config": {
            "settlement_mode": "last_step", "detection_steps": [1, 2, 3],
            "combo_table": {
                "enabled": True, "labels": ["区A", "区B"], "count_mode": "positional",
                "tracking": {"iou": 0.4, "ema_alpha": 0.6, "min_consecutive": 3,
                             "pending_ttl": 10, "perish_ticks": 0, "idle_reset_ticks": 0},
                "rows": [
                    {"counts": [2, 1], "verdict": "OK", "tag": "机型X", "plc_code": "4"},
                    {"counts": [3, 1], "verdict": "OK", "tag": "机型Y", "plc_code": "6"},
                ],
                "live_display": {"enabled": True, "size": "large", "show_plc_type": True},
                "plc_display": {"connection_id": plc_id, "point": "cyl_type"},
            },
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": []},
            {"id": 2, "name": "不合格(NG)", "actions": []},
        ],
        "counters_config": [{"name": "合格总数", "value": 0}, {"name": "不良总数", "value": 0}],
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=10)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})

        # ---- 启动剧本 + 检测 ----
        detail = requests.get(f"{API}/api/v1/projects/{pid}", timeout=5).json()
        requests.post(f"{API}/api/v1/test/synthetic/start", json={
            "scenario_json": _scn_live(), "channel": CH, "with_project": False,
        }, timeout=10).raise_for_status()
        sp = {k: detail.get(k) for k in (
            "steps_config", "pipeline_config", "events_config",
            "counters_config", "data_config", "logic_mode")}
        sp["data_config"] = sp.get("data_config") or {}
        requests.post(f"{API}/api/v1/source/detection/set-project?channel={CH}",
                      json={**sp, "project_id": pid, "name": NAME},
                      timeout=10).raise_for_status()
        requests.post(f"{API}/api/v1/source/detection/start?channel={CH}",
                      json={"conf": 0.25, "iou": 0.45}, timeout=10).raise_for_status()
        page.goto(f"{BASE}/#/monitor", wait_until="domcontentloaded")

        body = _wait(lambda b: (_cv(b).get("positional_counts") or {}).get("区A") == 2
                     and (_cv(b).get("positional_counts") or {}).get("区B") == 1)
        pc = _cv(body).get("positional_counts") or {}
        step("计数就绪 (区A=2, 区B=1)", pc.get("区A") == 2 and pc.get("区B") == 1,
             json.dumps(pc, ensure_ascii=False))

        # ---- 1. 看板存在且为停靠 (非悬浮) ----
        panel = page.locator(".combo-big-card").first
        panel.wait_for(state="visible", timeout=10000)
        cls = panel.get_attribute("class") or ""
        step("看板不再是悬浮层 (无 absolute 类)", "absolute" not in cls, cls)
        video_box = page.locator("canvas").first.bounding_box() or {}
        panel_box = panel.bounding_box() or {}
        below = (panel_box.get("y", 0)
                 >= video_box.get("y", 0) + video_box.get("height", 0) - 5)
        step("看板在视频下方 (不遮挡画面)", below,
             f"video_bottom={video_box.get('y', 0) + video_box.get('height', 0):.0f} "
             f"panel_y={panel_box.get('y', 0):.0f}")

        # ---- 2. 与 SOP 流程卡片同行, 停靠右侧 ----
        sop = page.locator("div:has(> div > span:text('SOP流程卡片'))").first
        sop_box = sop.bounding_box() or {}
        same_row = abs(panel_box.get("y", 0) - sop_box.get("y", -999)) < 10
        right_of = panel_box.get("x", 0) >= (sop_box.get("x", 0)
                                             + sop_box.get("width", 0) - 5)
        step("看板与 SOP 卡片同一行且在其右侧", same_row and right_of,
             f"sop=({sop_box.get('x', 0):.0f},{sop_box.get('y', 0):.0f},"
             f"w={sop_box.get('width', 0):.0f}) "
             f"panel=({panel_box.get('x', 0):.0f},{panel_box.get('y', 0):.0f})")

        # ---- 3. 瓦片内容: 标签计数 + 缸型 ----
        txt = panel.inner_text()
        step("瓦片含 区A/区B 计数与缸型标记 机型X",
             "区A" in txt and "区B" in txt and "当前缸型" in txt and "机型X" in txt,
             txt.replace("\n", " "))
        page.screenshot(path=str(EVIDENCE / "01_dock_panel_plc4.png"), full_page=False)

        # ---- 4. PLC 切缸型 4→6 即时刷新 ----
        requests.post(f"{API}/api/v1/plc/connections/{plc_id}/mock-set",
                      json={"point": "cyl_type", "value": 6}, timeout=5).raise_for_status()
        _wait(lambda b: str((_cv(b).get("plc_type") or {}).get("value")) == "6",
              timeout=20)
        time.sleep(1.2)
        txt2 = panel.inner_text()
        step("PLC 切 4→6 后缸型标记刷新为 机型Y", "机型Y" in txt2,
             txt2.replace("\n", " "))
        page.screenshot(path=str(EVIDENCE / "02_dock_panel_plc6.png"), full_page=False)

        # ---- 5. PLC 出未登记值 (11) → 缸型瓦片红色警示 (2026-08-14 现场) ----
        requests.post(f"{API}/api/v1/plc/connections/{plc_id}/mock-set",
                      json={"point": "cyl_type", "value": 11}, timeout=5).raise_for_status()
        _wait(lambda b: str((_cv(b).get("plc_type") or {}).get("value")) == "11",
              timeout=20)
        time.sleep(1.2)
        txt3 = panel.inner_text()
        step("PLC=11 (判定表只登记 4/6) → 瓦片显示未登记警示",
             "11" in txt3 and "未在判定表登记" in txt3, txt3.replace("\n", " "))
        tile = panel.locator("div").filter(has_text="当前缸型").last
        tile_cls = tile.get_attribute("class") or ""
        step("未登记瓦片转红 (red 样式类)", "red" in tile_cls, tile_cls)
        page.screenshot(path=str(EVIDENCE / "03_plc11_unregistered_red.png"),
                        full_page=False)

        # ---- 6. 切回已登记值 4 → 警示消失恢复琥珀 ----
        requests.post(f"{API}/api/v1/plc/connections/{plc_id}/mock-set",
                      json={"point": "cyl_type", "value": 4}, timeout=5).raise_for_status()
        _wait(lambda b: str((_cv(b).get("plc_type") or {}).get("value")) == "4",
              timeout=20)
        time.sleep(1.2)
        txt4 = panel.inner_text()
        step("切回 PLC=4 → 警示消失, 恢复 机型X",
             "机型X" in txt4 and "未在判定表登记" not in txt4,
             txt4.replace("\n", " "))
        page.screenshot(path=str(EVIDENCE / "04_plc4_registered_back.png"),
                        full_page=False)

        browser.close()

    requests.post(f"{API}/api/v1/source/detection/stop?channel={CH}", timeout=5)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel={CH}", timeout=5)
    cleanup()

    (EVIDENCE / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 50}\n总判定: {'PASS' if verdict['pass'] else 'FAIL'}"
          f"  (证据: {EVIDENCE})")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
