# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 拆分功能组合矩阵 + 零差异对照 (v3.32 收官验收)。

现场叙事:
  1. 老客户: 昨天之前配好的拆分项目(没有多轮次/没有显示策略/没有违序事件)
     升级后打开编辑器不炸、检测行为一颗螺丝都不差 —— 零差异。
  2. 新客户: 工件每次放的位置有偏差 → 选「锚点跟随」, 在标准位置抓一次锚点框;
     又要前后罩两轮 → 同一条规则再开多轮次。全程手点配置, 工件整体偏移
     +0.1/+0.05 后两轮 8 颗螺丝仍然颗颗对号入座。

覆盖:
  A: 零差异 — v1 形态配置(无任何新键) API 下发 → 编辑器打开老规则正常回填(轮次关) →
     运行四螺丝剧本: 螺丝1~4 计步 + OK 周期 + 轮次运行态为空(不画角标) + 就位提示照旧
  B: 组合 — 全 UI 配置「锚点跟随 × 多轮次」: 抓锚点框→四象限→开轮次→落库校验
  C: 运行 — 工件偏移 + 盖罩两轮剧本: 前罩/后罩螺丝1~4 共 8 步全计到 + 轮次到 2
  D: 监控页人眼证据 + 无 console 错误

前置: 后端 8001(RUNTIME_MODE=test) + 前端 6001。项目 __uat_ 前缀, 收尾删除。
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
CH = 0

run = UatRun("label_split_matrix")
pid_a = pid_b = None

Q1 = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
Q2 = [[0.5, 0.0], [1.0, 0.0], [1.0, 0.5], [0.5, 0.5]]
Q3 = [[0.0, 0.5], [0.5, 0.5], [0.5, 1.0], [0.0, 1.0]]
Q4 = [[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]]
QUADS = [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)]
GUIDE_POLY = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]

ANCHOR_CAL = {"label": "前罩", "confidence": 0.97, "bbox": [0.2, 0.2, 0.6, 0.6]}
# 运行时工件整体偏移 (+0.1, +0.05), 尺寸不变
ANCHOR_RT = {"label": "前罩", "confidence": 0.97, "bbox": [0.3, 0.25, 0.6, 0.6]}
SHIFT = (0.1, 0.05)
COVER = {"label": "盖罩", "confidence": 0.96, "bbox": [0.35, 0.55, 0.3, 0.3]}


def _screw(cx, cy):
    return {"label": "打螺丝", "confidence": 0.92,
            "bbox": [cx - 0.05, cy - 0.05, 0.1, 0.1]}


# ==================== Phase A: 零差异配置(v1 形态) ====================

def _legacy_project_payload():
    """v3.32 新键(rounds/display/violation_event)一个都不带的配置。"""
    steps = [{"id": 100, "label": "打螺丝", "displayLabel": "打螺丝",
              "enabled": False, "threshold": 50}]
    steps += [
        {"id": 100 + i, "label": f"螺丝{i}", "displayLabel": f"螺丝{i}",
         "enabled": True, "threshold": 50, "min_frames": 1,
         "split_origin": "ls_legacy"}
        for i in range(1, 5)
    ]
    return {
        "steps_config": steps,
        "pipeline_config": {
            "sequence_order": [{"step_id": 100 + i} for i in range(1, 5)],
            "settlement_mode": "first_step",
            "settle_dedup": False,
            "label_splits": [{
                "id": "ls_legacy", "enabled": True, "source_label": "打螺丝",
                "mode": "fixed", "unmatched": "drop",
                "regions": [{"name": f"螺丝{i + 1}", "polygon": poly}
                            for i, poly in enumerate([Q1, Q2, Q3, Q4])],
            }],
            "placement_guide": {"enabled": True, "anchor_label": "前罩",
                                "polygon": GUIDE_POLY, "mode": "hint"},
        },
        "events_config": [
            {"id": 1, "name": "合格(OK)", "actions": [
                {"counter_name": "合格总数", "delta": 1}]},
            {"id": 2, "name": "不合格(NG)", "actions": [
                {"counter_name": "不良总数", "delta": 1}]},
        ],
        "counters_config": [{"name": "合格总数", "value": 0},
                            {"name": "不良总数", "value": 0}],
    }


def _legacy_scenario():
    """前罩常驻引导框内, 四颗螺丝按序打完, 第二工件首颗触发结算, 长尾供人眼看。"""
    tl = [{"from": 0, "to": 59, "detections": [ANCHOR_CAL]}]
    f = 60
    for cx, cy in QUADS:
        tl.append({"from": f, "to": f + 29, "detections": [ANCHOR_CAL, _screw(cx, cy)]})
        tl.append({"from": f + 30, "to": f + 39, "detections": [ANCHOR_CAL]})
        f += 40
    tl.append({"from": f, "to": f + 29, "detections": [ANCHOR_CAL, _screw(0.25, 0.25)]})
    tl.append({"from": f + 30, "to": 18000, "detections": [ANCHOR_CAL]})
    return {"name": "uat_legacy_zero_diff", "fps": 60, "timeline": tl}


# ==================== Phase C: 锚点×多轮次运行剧本 ====================

def _combo_scenario():
    """偏移后的工件: 盖罩两轮各打四颗(位置=标定象限中心+偏移), 轮间盖罩离场4s。"""
    def sc(cx, cy):
        return _screw(cx + SHIFT[0], cy + SHIFT[1])

    tl = [{"from": 0, "to": 59, "detections": [ANCHOR_RT]},
          {"from": 60, "to": 119, "detections": [ANCHOR_RT, COVER]}]  # → 第1轮

    def one_round(f):
        for cx, cy in QUADS:
            tl.append({"from": f, "to": f + 29,
                       "detections": [ANCHOR_RT, COVER, sc(cx, cy)]})
            tl.append({"from": f + 30, "to": f + 39,
                       "detections": [ANCHOR_RT, COVER]})
            f += 40
        return f

    f = one_round(120)
    tl.append({"from": f, "to": f + 239, "detections": [ANCHOR_RT]})       # 盖罩离场 4s
    tl.append({"from": f + 240, "to": f + 299,
               "detections": [ANCHOR_RT, COVER]})                          # → 第2轮
    f = one_round(f + 300)
    tl.append({"from": f, "to": 18000, "detections": [ANCHOR_RT, COVER]})  # 长尾
    return {"name": "uat_anchor_rounds_combo", "fps": 60, "timeline": tl}


# ==================== 共用操作 ====================

def _open_project_steps_tab(page, pname):
    page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("text=项目管理", timeout=15000)
    time.sleep(1.2)
    page.locator("input[placeholder*='搜索项目']").fill(pname)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{pname}')").first.click()
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
    time.sleep(0.8)


def _fill_creatable_select(page, scope, text):
    scope.click()
    time.sleep(0.4)
    page.keyboard.type(text, delay=40)
    time.sleep(0.5)
    page.keyboard.press("Enter")
    time.sleep(0.4)


def _set_project_and_start(pid):
    detail = requests.get(f"{API}/projects/{pid}", timeout=10).json()
    requests.post(f"{API}/source/detection/set-project?channel={CH}", json={
        "project_id": pid, "name": detail["name"], "task_type": detail["task_type"],
        "logic_mode": detail["logic_mode"], "steps_config": detail["steps_config"],
        "pipeline_config": detail["pipeline_config"],
        "events_config": detail.get("events_config") or [],
        "counters_config": detail.get("counters_config") or [],
    }, timeout=10).raise_for_status()
    r = requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"conf": 0.25, "iou": 0.45}, timeout=15)
    r.raise_for_status()


def _stop_all():
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
    requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)


def _goto_monitor(page):
    m_body = ""
    for _ in range(3):
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(1)
        page.reload(wait_until="domcontentloaded")
        time.sleep(8)
        m_body = page.evaluate("document.body.innerText")
        if str(page.evaluate("location.hash")).startswith("#/monitor") \
                and "检测中" in m_body:
            break
    return m_body


try:
    # ============ Phase A: 零差异对照 ============
    pname_a = f"__uat_lsz_{uuid.uuid4().hex[:5]}"
    r = requests.post(f"{API}/projects", json={
        "name": pname_a, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    r.raise_for_status()
    pid_a = r.json()["id"]
    requests.put(f"{API}/projects/{pid_a}", json=_legacy_project_payload(),
                 timeout=10).raise_for_status()
    run.step("A0 API 下发 v1 形态老配置(无任何 v3.32 新键)", True, f"id={pid_a}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)

        # A1: 老规则进编辑器不炸 + 轮次默认关
        _open_project_steps_tab(page, pname_a)
        body = page.evaluate("document.body.innerText")
        run.step("A1 老规则在规则表可见(固定画面, 无轮次标注)",
                 "固定画面" in body and "轮 · 切换" not in body)
        page.locator("tbody tr:has-text('固定画面') button:has-text('编辑')").first.click()
        time.sleep(1.2)
        dlg = page.locator(".el-dialog:has-text('区域定位方式')")
        d_body = dlg.first.inner_text() if dlg.count() else ""
        run.step("A2 编辑器正常回填老规则(多轮次开关存在且关闭)",
                 dlg.count() > 0 and "多轮次拆分" in d_body
                 and "第2轮使用独立区域" not in d_body)
        run.shot(page, "A_01_legacy_rule_dialog")
        dlg.locator(".el-dialog__footer button:has-text('取消')").first.click()
        time.sleep(0.8)

        # A3: 运行零差异剧本
        requests.post(f"{API}/projects/{pid_a}/activate", timeout=15)
        requests.post(f"{API}/test/synthetic/start", json={
            "scenario_json": _legacy_scenario(), "channel": CH,
            "with_project": False,
        }, timeout=10).raise_for_status()
        _set_project_and_start(pid_a)

        deadline = time.time() + 40
        ok_body = None
        while time.time() < deadline:
            b = requests.get(f"{API}/source/detection/results?channel={CH}",
                             timeout=5).json()
            sc_ = b.get("step_counts") or {}
            ctr = b.get("counters") or {}
            if all(sc_.get(f"螺丝{i}", 0) >= 1 for i in range(1, 5)) \
                    and ctr.get("合格总数", 0) >= 1:
                ok_body = b
                break
            time.sleep(0.5)
        run.step("A3 零差异运行: 螺丝1~4 计步 + OK 周期结算",
                 ok_body is not None,
                 f"step_counts={(ok_body or b).get('step_counts')} "
                 f"counters={(ok_body or b).get('counters')}")
        fin = ok_body or b
        run.step("A4 轮次运行态为空(老配置不画角标)",
                 not fin.get("label_split_rounds"),
                 f"rounds={fin.get('label_split_rounds')}")
        pg = fin.get("placement_guide") or {}
        run.step("A5 就位提示照旧工作(in_position=True, 无 display 字段)",
                 pg.get("in_position") is True and "display" not in pg, f"pg={pg}")

        m_body = _goto_monitor(page)
        run.step("A6 监控页显示老项目虚拟步骤",
                 all(f"螺丝{i}" in m_body for i in range(1, 5)))
        run.shot(page, "A_02_monitor_zero_diff")
        _stop_all()

        # ============ Phase B: 全 UI 配置 锚点跟随 × 多轮次 ============
        pname_b = f"__uat_lsc_{uuid.uuid4().hex[:5]}"
        r = requests.post(f"{API}/projects", json={
            "name": pname_b, "task_type": "detection", "logic_mode": "sequential",
        }, timeout=10)
        r.raise_for_status()
        pid_b = r.json()["id"]
        requests.put(f"{API}/projects/{pid_b}", json={
            "steps_config": [{"id": 1, "label": "打螺丝", "displayLabel": "打螺丝",
                              "enabled": True, "threshold": 50}],
            "events_config": [
                {"id": 1, "name": "合格(OK)", "actions": [
                    {"counter_name": "合格总数", "delta": 1}]},
                {"id": 2, "name": "不合格(NG)", "actions": [
                    {"counter_name": "不良总数", "delta": 1}]},
            ],
            "counters_config": [{"name": "合格总数", "value": 0},
                                {"name": "不良总数", "value": 0}],
        }, timeout=10).raise_for_status()

        # 标定画面: 锚点在标准位置, 供「抓取锚点框」
        requests.post(f"{API}/test/synthetic/start", json={
            "scenario_json": {"name": "uat_anchor_cal", "fps": 30, "timeline": [
                {"from": 0, "to": 30000, "detections": [ANCHOR_CAL]}]},
            "channel": CH, "with_project": False,
        }, timeout=10).raise_for_status()
        requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"conf": 0.25, "iou": 0.45}, timeout=15).raise_for_status()

        _open_project_steps_tab(page, pname_b)
        page.locator("button:has-text('新建拆分规则')").click()
        time.sleep(1.2)
        dlg = page.locator(".el-dialog:has-text('区域定位方式')")
        _fill_creatable_select(page, dlg.locator(".el-select").first, "打螺丝")
        dlg.locator("button:has-text('四象限模板')").click()
        time.sleep(0.8)
        # 锚点跟随 + 抓取标定
        dlg.locator(".el-radio-button:has-text('锚点跟随')").first.click()
        time.sleep(0.6)
        anchor_box = dlg.locator("div.border:has-text('锚点标签')").last
        _fill_creatable_select(page, anchor_box.locator(".el-select").first, "前罩")
        dlg.locator("button:has-text('从当前画面抓取锚点框')").click()
        time.sleep(1.5)
        run.step("B1 抓取锚点框成功(已标定)",
                 dlg.locator("text=已标定").count() > 0)
        # 多轮次
        dlg.locator(".el-switch").first.click()
        time.sleep(0.6)
        rounds_box = dlg.locator("div.border:has-text('多轮次拆分')").last
        _fill_creatable_select(page, rounds_box.locator(".el-select").first, "盖罩")
        run.shot(page, "B_01_anchor_rounds_dialog")
        dlg.locator("button:has-text('保存规则')").click()
        time.sleep(1.2)
        body = page.evaluate("document.body.innerText")
        run.step("B2 规则表显示锚点跟随+2轮, 虚拟步骤 8 个",
                 "锚点跟随" in body and "前罩螺丝1" in body and "后罩螺丝4" in body)
        # 原始标签步骤禁用
        page.locator("tbody tr:has-text('打螺丝') .el-switch").first.click()
        time.sleep(0.5)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.5)
        detail = requests.get(f"{API}/projects/{pid_b}", timeout=10).json()
        splits = (detail.get("pipeline_config") or {}).get("label_splits") or []
        s0 = splits[0] if splits else {}
        rd = s0.get("rounds") or {}
        run.step("B3 落库: mode=anchor + anchor_ref + rounds(盖罩×2)",
                 s0.get("mode") == "anchor" and s0.get("anchor_label") == "前罩"
                 and (s0.get("anchor_ref") or {}).get("w", 0) > 0
                 and rd.get("enabled") is True and rd.get("count") == 2
                 and rd.get("trigger_label") == "盖罩",
                 f"mode={s0.get('mode')} ref={s0.get('anchor_ref')} rounds={rd}")
        virt = {s["label"] for s in (detail.get("steps_config") or [])
                if s.get("split_origin")}
        run.step("B4 落库: 8 个虚拟步骤",
                 virt == {f"{p_}螺丝{i}" for p_ in ("前罩", "后罩")
                          for i in range(1, 5)}, f"virt={sorted(virt)}")
        _stop_all()

        # ============ Phase C: 偏移工件两轮运行 ============
        requests.post(f"{API}/projects/{pid_b}/activate", timeout=15)
        requests.post(f"{API}/test/synthetic/start", json={
            "scenario_json": _combo_scenario(), "channel": CH,
            "with_project": False,
        }, timeout=10).raise_for_status()
        _set_project_and_start(pid_b)

        expected8 = [f"{p_}螺丝{i}" for p_ in ("前罩", "后罩") for i in range(1, 5)]
        deadline = time.time() + 60
        round2_rt = False
        fin = {}
        while time.time() < deadline:
            fin = requests.get(f"{API}/source/detection/results?channel={CH}",
                               timeout=5).json()
            rt = (fin.get("label_split_rounds") or {}).get("打螺丝") or {}
            if rt.get("round") == 2:
                round2_rt = True
            sc_ = fin.get("step_counts") or {}
            if all(sc_.get(l, 0) >= 1 for l in expected8) and round2_rt:
                break
            time.sleep(0.5)
        sc_ = fin.get("step_counts") or {}
        run.step("C1 偏移工件: 两轮 8 个虚拟步骤全部计到",
                 all(sc_.get(l, 0) >= 1 for l in expected8),
                 f"缺={[l for l in expected8 if sc_.get(l, 0) < 1]}")
        run.step("C2 轮次运行态到达第2轮", round2_rt)
        run.step("C3 原始标签「打螺丝」未漏进状态机",
                 sc_.get("打螺丝", 0) == 0, f"打螺丝={sc_.get('打螺丝', 0)}")

        # ============ Phase D: 监控页人眼证据 ============
        m_body = _goto_monitor(page)
        run.step("D1 监控页接上组合项目运行态",
                 pname_b in m_body and "检测中" in m_body,
                 f"hash={page.evaluate('location.hash')}")
        run.step("D2 监控页步骤卡含两轮虚拟步骤",
                 all(l in m_body for l in expected8),
                 f"缺={[l for l in expected8 if l not in m_body]}")
        run.shot(page, "C_01_monitor_anchor_rounds")
        time.sleep(3)
        run.shot(page, "C_02_monitor_anchor_rounds_later")
        _stop_all()

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
        _stop_all()
    except Exception:
        pass
    for _pid in (pid_a, pid_b):
        if _pid is not None:
            try:
                requests.delete(f"{API}/projects/{_pid}", timeout=10)
            except Exception:
                pass
    raise SystemExit(run.finish())
