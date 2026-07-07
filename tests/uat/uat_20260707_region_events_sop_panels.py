# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 区域事件模式 SOP 流程卡片 + 步骤统计打通 (v3.32)。

现场叙事: TP 工位操作员在监控页开检测, 工人拿测硬度笔压在工件上 →
SOP「测硬度」卡片青色点亮 + PT 实时走表; 动作确认 → 卡片变绿带截图、
步骤统计行翻"已检测"+定格 PT; 工件出下料口 → 周期结算, 面板清屏进下一循环。
此前该模式两块面板全程空白 (客户报障), 本 UAT 用真实 TP 视频 + 真模型
走完整链路留人眼证据。

前置: main 栈 — 后端 8001 + 前端 6001 已启动; TP 项目 (id=22) 已配好规则。
"""
from __future__ import annotations

import sys
import time

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"
CH = 0
TP_PID = 22
TP_VIDEO = ("/home/qianqian/桌面/word/tianjun-main/backend/"
            "uploads/videos/4b0995aa8f96075a8f51533e0d0fd324.mp4")

run = UatRun("region_events_sop_panels")

_CARD_CLASS_JS = """(label) => {
  const title = [...document.querySelectorAll('span')]
    .find(s => s.textContent.trim() === 'SOP流程卡片');
  if (!title) return null;
  const panel = title.closest('.h-44');
  if (!panel) return null;
  const card = [...panel.querySelectorAll('.w-32')]
    .find(c => c.innerText.includes(label));
  return card ? card.className : null;
}"""

_TABLE_ROW_JS = """(label) => {
  const row = [...document.querySelectorAll('tbody tr')]
    .find(tr => tr.innerText.includes(label));
  return row ? row.innerText : null;
}"""


def _results():
    return requests.get(f"{API}/source/detection/results?channel={CH}",
                        timeout=5).json()


try:
    # ---- 0. 起 TP 项目 + 现场视频 + 检测 ----
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
    requests.post(f"{API}/source/video/stop?channel={CH}", timeout=10)
    r = requests.post(f"{API}/projects/{TP_PID}/activate", timeout=30)
    run.step("00 激活 TP 项目", r.status_code == 200, f"status={r.status_code}")
    r = requests.post(f"{API}/source/video/start?channel={CH}",
                      json={"file_path": TP_VIDEO}, timeout=20)
    run.step("01 现场视频源已起", r.status_code == 200, f"status={r.status_code}")
    r = requests.post(f"{API}/source/detection/start?channel={CH}", json={},
                      timeout=60)
    run.step("02 检测已启动 (真模型)", r.status_code == 200, f"status={r.status_code}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(6)
        body = page.evaluate("document.body.innerText")
        run.step("03 监控页接上运行态, 步骤行=动作名",
                 "测硬度" in body and "扫码" in body and "下工件" in body)
        run.shot(page, "01_monitor_initial")

        # ---- A. 动作进行中: SOP 卡片青色点亮 + PT 实时 ----
        active_seen = None      # (label, class)
        active_shot = False
        completed_seen = None   # (label, row_text)
        settled = False
        base_ok = (_results().get("counters") or {}).get("合格总数", 0)

        # "进行中"青色窗口很短 (确认帧数 ~0.6s), 高频采样 + 跨多个周期抓取;
        # 三个目标态都抓到即提前收工
        deadline = time.time() + 150
        while time.time() < deadline:
            d = _results()
            infl = d.get("step_inflight_durations") or {}
            cyc = d.get("current_cycle_steps") or []
            counters = d.get("counters") or {}

            if infl and active_seen is None:
                # 只有还没确认进周期的动作才可能是青色 (确认后锁绿)
                for lbl in infl:
                    if lbl in cyc:
                        continue
                    cls = page.evaluate(_CARD_CLASS_JS, lbl)
                    if cls and "border-cyan-500" in cls:
                        active_seen = (lbl, cls)
                        if not active_shot:
                            run.shot(page, f"02_action_in_progress_{lbl}")
                            active_shot = True
                        break

            if cyc and completed_seen is None:
                lbl = cyc[0]
                row = page.evaluate(_TABLE_ROW_JS, lbl)
                cls = page.evaluate(_CARD_CLASS_JS, lbl)
                if row and "已检测" in row and cls and "border-green-500" in cls:
                    completed_seen = (lbl, row.replace("\t", " | "))
                    run.shot(page, f"03_action_confirmed_{lbl}")

            if counters.get("合格总数", 0) >= base_ok + 1 and not settled:
                settled = True
                run.shot(page, "04_cycle_settled")
            if active_seen and completed_seen and settled:
                break
            time.sleep(0.12)

        run.step("A1 动作进行中: SOP 卡片青色点亮 (in-flight 驱动)",
                 active_seen is not None, f"{active_seen}")
        run.step("A2 动作确认: 步骤统计行翻已检测 + SOP 卡片变绿",
                 completed_seen is not None, f"{completed_seen}")
        run.step("A3 周期结算 (合格总数 +1)", settled,
                 f"base={base_ok}")

        # ---- B. 双向验证: 前端显示的 PT 与后端权威值对上 ----
        d = _results()
        lcs = d.get("last_cycle_sum_step_durations") or {}
        shots_keys = list((d.get("step_screenshots") or {}).keys())
        run.step("B1 后端 PT 合并档历史有值 (测硬度)",
                 lcs.get("测硬度", 0) > 0, f"last_cycle_sum={lcs}")
        run.step("B2 后端步骤截图已产出 (SOP 缩略图数据源)",
                 "测硬度" in shots_keys, f"keys={shots_keys}")
        snap = d.get("region_events")
        run.step("B3 引擎快照键透出 (/results.region_events)",
                 isinstance(snap, dict) and snap.get("rules"),
                 f"rules={len((snap or {}).get('rules') or [])}")

        # SOP 卡片有截图 img (动作确认后)
        has_img = page.evaluate("""() => {
          const title = [...document.querySelectorAll('span')]
            .find(s => s.textContent.trim() === 'SOP流程卡片');
          if (!title) return false;
          const panel = title.closest('.h-44');
          return !!panel && [...panel.querySelectorAll('img')]
            .some(i => (i.src || '').startsWith('data:image'));
        }""")
        run.step("B4 SOP 卡片渲染动作截图缩略图", bool(has_img))
        run.shot(page, "05_final_panels")

        errs = filter_console_errors(cerrs)
        run.step("C1 无前端 console 错误", len(errs) == 0, f"errs={errs[:3]}")

        ctx.close()
        browser.close()
except Exception:  # noqa: BLE001 — 主流程异常必须显式记录
    import traceback
    _tb = traceback.format_exc()
    print(_tb, flush=True)
    run.step("EXCEPTION 主流程异常中断", False, _tb.strip().splitlines()[-1][:200])
finally:
    try:
        requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/source/video/stop?channel={CH}", timeout=10)
    except Exception:
        pass
    raise SystemExit(run.finish())
