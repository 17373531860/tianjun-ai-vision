# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 就位引导框显示策略 (v3.32 增量 — always/fade_on_ready/hide_on_ready)。

现场叙事: 工人反馈"工件放好后绿框一直亮着碍眼"。项目页就位提示卡片新增
「就位后引导框怎么显示」三档: 常驻(默认) / 淡化(半透明细框) / 隐藏。
未就位时永远完整显示黄色提醒, 不影响引导作用。

覆盖:
  A: 未就位阶段 — 三档策略下引导框都完整显示(黄色提醒)
  B: 就位后 — hide_on_ready 画布清空 / fade_on_ready 有细框 / always 完整绿框
  C: 人眼证据截图

前置: 隔离栈 后端 8001(RUNTIME_MODE=test) + 前端 6001。项目 __uat_ 前缀, 收尾删除。
"""
from __future__ import annotations

import sys
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"
PNAME = f"__uat_pgd_{uuid.uuid4().hex[:5]}"
CH = 0

run = UatRun("placement_guide_display")
pid = None

# 引导框贴边、锚点框缩在中心: 画布外圈环带里只可能出现引导框的描绘,
# 从而把"引导框是否显示"与"检测框本身的描绘"区分开(检测框永远画, 与本功能无关)
GUIDE_POLY = [[0.02, 0.02], [0.98, 0.02], [0.98, 0.98], [0.02, 0.98]]
ANCHOR = {"label": "前罩", "confidence": 0.97, "bbox": [0.45, 0.45, 0.1, 0.1]}


def _scenario():
    """前 20s 空画面(未就位, 留足页面加载时间) → 之后前罩常驻框内(已就位)。"""
    return {"name": "uat_pg_display", "fps": 60, "timeline": [
        {"from": 0, "to": 1199, "detections": []},
        {"from": 1200, "to": 60000, "detections": [ANCHOR]},
    ]}


STEPS = [{"id": 1, "label": "前罩", "displayLabel": "前罩",
          "enabled": True, "threshold": 50}]


def _pipeline_cfg(display):
    return {
        "sequence_order": [{"step_id": 1}],
        "settlement_mode": "first_step",
        "placement_guide": {
            "enabled": True, "anchor_label": "前罩",
            "polygon": GUIDE_POLY, "mode": "hint", "display": display,
        },
    }


def _apply_display(display, scenario):
    """策略切换一条龙: 写库(监控页叠加层读 DB 配置) + 下发通道 + 起剧本/检测。"""
    requests.put(f"{API}/projects/{pid}", json={
        "steps_config": STEPS, "pipeline_config": _pipeline_cfg(display),
    }, timeout=10).raise_for_status()
    requests.post(f"{API}/test/synthetic/start", json={
        "scenario_json": scenario, "channel": CH, "with_project": False,
    }, timeout=10).raise_for_status()
    requests.post(f"{API}/source/detection/set-project?channel={CH}", json={
        "project_id": pid, "name": PNAME, "task_type": "detection",
        "logic_mode": "sequential", "steps_config": STEPS,
        "pipeline_config": _pipeline_cfg(display),
        "events_config": [], "counters_config": [],
    }, timeout=10).raise_for_status()
    requests.post(f"{API}/source/detection/start?channel={CH}",
                  json={"conf": 0.25, "iou": 0.45}, timeout=15).raise_for_status()


def _stop_all():
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
    requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)
    time.sleep(1)


# 只统计外圈环带(边缘 15% 以内)的着色像素 — 该区域只有贴边引导框会画到,
# 中心的锚点检测框/标签文字采不到, 避免误判
CANVAS_PAINTED_JS = """() => {
  const c = document.querySelector('canvas.absolute.top-0.left-0.pointer-events-none');
  if (!c || !c.width || !c.height) return -1;
  const ctx = c.getContext('2d');
  const w = c.width, h = c.height;
  const d = ctx.getImageData(0, 0, w, h).data;
  const mx = w * 0.15, my = h * 0.15;
  let hits = 0;
  for (let i = 3; i < d.length; i += 16) {
    if (d[i] === 0) continue;
    const px = ((i - 3) / 4) % w, py = Math.floor(((i - 3) / 4) / w);
    if (px < mx || px > w - mx || py < my || py > h - my) hits++;
  }
  return hits;
}"""


def _wait_in_position(want, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = requests.get(f"{API}/source/detection/results?channel={CH}", timeout=5).json()
        if bool((b.get("placement_guide") or {}).get("in_position")) == want:
            return True
        time.sleep(0.5)
    return False


def _painted(page, settle_s=2.0):
    time.sleep(settle_s)
    return page.evaluate(CANVAS_PAINTED_JS)


try:
    r = requests.post(f"{API}/projects", json={
        "name": PNAME, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.post(f"{API}/projects/{pid}/activate", timeout=15)
    run.step("A0 建项目并激活", True, f"id={pid}")

    READY_SCENARIO = {"name": "pg_ready", "fps": 60, "timeline": [
        {"from": 0, "to": 60000, "detections": [ANCHOR]}]}

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)

        def _goto_monitor():
            # 监控页叠加层读的是 DB 项目配置(前端 store), 每次改库后必须整页刷新
            page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
            page.reload(wait_until="domcontentloaded")
            time.sleep(6)

        # ---- 策略1: hide_on_ready — 未就位显示, 就位后画布清空 ----
        _apply_display("hide_on_ready", _scenario())
        _goto_monitor()

        run.step("B1 [hide] 未就位阶段运行态 in_position=False", _wait_in_position(False, 10))
        painted_pre = _painted(page)
        run.step("B2 [hide] 未就位时引导框完整显示(画布非空)", painted_pre > 50,
                 f"painted={painted_pre}")
        run.shot(page, "01_hide_not_ready_visible")

        run.step("B3 [hide] 前罩入框后运行态 in_position=True", _wait_in_position(True, 30))
        painted_post = _painted(page, settle_s=3.0)
        run.step("B4 [hide] 就位后引导框从画面消失(画布清空)",
                 0 <= painted_post <= 5, f"painted={painted_post}")
        run.shot(page, "02_hide_ready_hidden")
        _stop_all()

        # ---- 策略2: fade_on_ready — 就位后仍有细框(画布非空但比完整档少) ----
        _apply_display("fade_on_ready", READY_SCENARIO)
        _goto_monitor()
        run.step("C1 [fade] 就位运行态", _wait_in_position(True, 20))
        painted_fade = _painted(page, settle_s=3.0)
        run.step("C2 [fade] 就位后保留半透明细框(画布非空)", painted_fade > 5,
                 f"painted={painted_fade}")
        run.shot(page, "03_fade_ready_thin_frame")
        _stop_all()

        # ---- 策略3: always — 就位后完整绿框+文字(默认行为不回归) ----
        _apply_display("always", READY_SCENARIO)
        _goto_monitor()
        run.step("D1 [always] 就位运行态", _wait_in_position(True, 20))
        painted_always = _painted(page, settle_s=3.0)
        run.step("D2 [always] 就位后完整绿框(画布明显多于淡化档)",
                 painted_always > painted_fade,
                 f"always={painted_always} fade={painted_fade}")
        run.shot(page, "04_always_ready_full_frame")

        errs = filter_console_errors(cerrs)
        run.step("E1 无前端 console 错误", len(errs) == 0, f"errs={errs[:3]}")

        ctx.close()
        browser.close()
except Exception:  # noqa: BLE001
    import traceback
    _tb = traceback.format_exc()
    print(_tb, flush=True)
    run.step("EXCEPTION 主流程异常中断", False, _tb.strip().splitlines()[-1][:200])
finally:
    try:
        requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)
    except Exception:
        pass
    if pid is not None:
        try:
            requests.delete(f"{API}/projects/{pid}", timeout=10)
        except Exception:
            pass
    raise SystemExit(run.finish())
