# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 同标签区域拆分·多轮次 + 严格顺序违序即时事件 (v3.32)。

现场叙事: 电机装配线, 前罩/后罩各打 4 颗螺丝且打的位置在画面里完全重叠,
只能靠工序时序区分——先盖前罩打 4 颗, 翻面盖后罩再打 4 颗。客户在拆分规则里
启用「多轮次」: 检测到「盖罩」重新出现即切下一轮, 同一批区域第 1 轮改写成
前罩螺丝1~4、第 2 轮改写成后罩螺丝1~4; 打错对角顺序时不再等周期结算,
当场触发「违序警告」事件(报警/计数)。

覆盖:
  A: 项目页 — 拆分编辑器多轮次区(开关/切换标签/前缀) → 8 个虚拟步骤进步骤表;
     逻辑设置结算卡片选「违序即时事件」
  B: 落库双向 — rounds 段 + strict_order_violation_event_id + 8 虚拟步骤/序列
  C: 监控页运行时 — synthetic 两轮剧本: 第1轮打出前罩螺丝1~4, 盖罩重现切第2轮
     打出后罩螺丝1~4, 下一工件首步结算 OK; 随后越序打第3颗 → 违序次数当场 +1
  D: 无前端 console 错误

前置: 隔离 UAT 栈 — 后端 8002 (RUNTIME_MODE=test + 独立 TIANJUN_DATA_DIR),
前端 6002 (VITE_API_BASE_URL 指 8002)。测试项目 __uat_ 前缀, 收尾删除。
"""
from __future__ import annotations

import sys
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8002/api/v1"
FRONT = "http://localhost:6002"
SUF = uuid.uuid4().hex[:5]
PNAME = f"__uat_lsr_{SUF}"
CH = 0

run = UatRun("label_split_rounds")
pid = None


# ==================== synthetic 剧本: 两轮四象限 + 越序违规 ====================

def _screw(cx, cy):
    return {"label": "打螺丝", "confidence": 0.92,
            "bbox": [cx - 0.05, cy - 0.05, 0.1, 0.1]}


COVER = {"label": "盖罩", "confidence": 0.96, "bbox": [0.3, 0.3, 0.4, 0.4]}
QUADS = [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)]


def _scenario():
    """工件1: 盖罩→前罩4颗 → 盖罩离场4s重现(> gap 3s 才算新一轮)→后罩4颗 → 工件下线;
    工件2: 盖罩(回绕第1轮)→螺丝1(结算工件1 OK)→跳打象限3(前罩螺丝3 越序→当场报)。
    尾巴拖长到 ~5min, 保证监控页人眼检查时检测仍在跑。"""
    tl = [{"from": 0, "to": 9, "detections": [COVER]}]
    GAP = 240   # 4s @60fps, 大于规则默认 trigger_gap_seconds=3.0

    def _round(f):
        for cx, cy in QUADS:
            tl.append({"from": f, "to": f + 29, "detections": [COVER, _screw(cx, cy)]})
            tl.append({"from": f + 30, "to": f + 39, "detections": [COVER]})
            f += 40
        return f

    f = _round(10)                                                    # 第1轮
    tl.append({"from": f, "to": f + GAP - 1, "detections": []})       # 盖罩离场 4s
    tl.append({"from": f + GAP, "to": f + GAP + 29, "detections": [COVER]})  # 重现 → 第2轮
    f = _round(f + GAP + 30)                                          # 第2轮
    tl.append({"from": f, "to": f + GAP - 1, "detections": []})       # 工件下线 4s
    f += GAP
    tl.append({"from": f, "to": f + 29, "detections": [COVER]})       # 工件2 → 回绕第1轮
    tl.append({"from": f + 30, "to": f + 59,
               "detections": [COVER, _screw(*QUADS[0])]})             # 螺丝1 → 结算OK
    tl.append({"from": f + 60, "to": f + 89, "detections": [COVER]})
    tl.append({"from": f + 90, "to": f + 209,
               "detections": [COVER, _screw(*QUADS[2])]})             # 跳打第3颗 → 违序 2s
    tl.append({"from": f + 210, "to": f + 18000, "detections": [COVER]})
    return {"name": "uat_ls_rounds", "fps": 60, "timeline": tl}


# ==================== UI 小助手 ====================

def _open_project_steps_tab(page):
    page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("text=项目管理", timeout=15000)
    time.sleep(1.2)
    page.locator("input[placeholder*='搜索项目']").fill(PNAME)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{PNAME}')").first.click()
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


EXPECTED_VIRT = [f"{p}螺丝{i}" for p in ("前罩", "后罩") for i in range(1, 5)]


# ==================== 主流程 ====================

try:
    # ---- 0. API 建项目: 打螺丝原始步骤 + 违序警告事件/计数器 ----
    r = requests.post(f"{API}/projects", json={
        "name": PNAME, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/projects/{pid}", json={
        "steps_config": [
            {"id": 1, "label": "打螺丝", "displayLabel": "打螺丝",
             "enabled": True, "threshold": 50},
        ],
        "events_config": [
            {"id": 1, "name": "合格(OK)", "color": "#10b981", "toast_id": "ok",
             "actions": [{"counter_name": "合格总数", "delta": 1},
                         {"counter_name": "总产量", "delta": 1}]},
            {"id": 2, "name": "不良(NG)", "color": "#ef4444", "toast_id": "ng",
             "actions": [{"counter_name": "不良总数", "delta": 1},
                         {"counter_name": "总产量", "delta": 1}]},
            {"id": 3, "name": "违序警告", "color": "#f59e0b", "toast_id": "ok",
             "show_notification": True,
             "actions": [{"counter_name": "违序次数", "delta": 1}]},
        ],
        "counters_config": [
            {"name": "合格总数", "value": 0}, {"name": "不良总数", "value": 0},
            {"name": "总产量", "value": 0},
            # 自定义计数器要 show_in_monitor=True 才会出现在监控页统计板 (v3.8.x 设计)
            {"name": "违序次数", "value": 0, "show_in_monitor": True},
        ],
    }, timeout=10).raise_for_status()
    run.step("A0 API 建测试项目(打螺丝 + 违序警告事件)", True, f"id={pid}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)

        # ---- A. 拆分编辑器: 四象限 + 多轮次 ----
        _open_project_steps_tab(page)
        page.locator("button:has-text('新建拆分规则')").click()
        time.sleep(1.2)
        dlg = page.locator(".el-dialog:has-text('区域定位方式')")
        run.step("A1 拆分规则编辑器打开", dlg.count() > 0)

        _fill_creatable_select(page, dlg.locator(".el-select").first, "打螺丝")
        dlg.locator("button:has-text('四象限模板')").click()
        time.sleep(0.8)
        # 多轮次: 对话框唯一 el-switch; 切换标签=盖罩; 前缀用默认 前罩/后罩
        dlg.locator(".el-switch").first.click()
        time.sleep(0.6)
        d_body = dlg.first.inner_text()
        run.step("A2 多轮次区展开(切换标签/轮数/每轮前缀)",
                 "轮次切换标签" in d_body and "每轮前缀" in d_body)
        rounds_box = dlg.locator("div.border:has-text('多轮次拆分')").last
        _fill_creatable_select(page, rounds_box.locator(".el-select").first, "盖罩")
        run.shot(page, "01_split_dialog_rounds")
        dlg.locator("button:has-text('保存规则')").click()
        time.sleep(1.2)

        body = page.evaluate("document.body.innerText")
        run.step("A3 保存规则后 8 个轮次虚拟步骤进步骤表",
                 all(lbl in body for lbl in EXPECTED_VIRT),
                 f"缺={[l for l in EXPECTED_VIRT if l not in body][:4]}")
        run.shot(page, "02_eight_virtual_steps")

        # 原始标签步骤禁用(由虚拟步骤参与判定)
        page.locator("tbody tr:has-text('打螺丝') .el-switch").first.click()
        time.sleep(0.5)

        # ---- A4. 逻辑设置: 违序即时事件选「违序警告」 ----
        page.locator(".el-tabs__item:has-text('逻辑设置')").first.click()
        time.sleep(0.8)
        l_body = page.evaluate("document.body.innerText")
        run.step("A4 结算卡片出现「违反严格顺序时立即触发」配置行",
                 "违反严格顺序时立即触发" in l_body)
        row = page.locator("div:has(> span:has-text('违反严格顺序时立即触发'))").last
        row.locator(".el-select").first.click()
        time.sleep(0.5)
        page.locator(".el-select-dropdown__item:has-text('违序警告')").last.click()
        time.sleep(0.5)
        run.shot(page, "03_violation_event_selected")

        # ---- B. 保存配置 → 落库复核; 严格顺序旗标走 API 补(存量功能非本次验证点) ----
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.5)
        detail = requests.get(f"{API}/projects/{pid}", timeout=10).json()
        pc = detail.get("pipeline_config") or {}
        splits = pc.get("label_splits") or []
        rd = (splits[0].get("rounds") or {}) if splits else {}
        virt = [s for s in (detail.get("steps_config") or []) if s.get("split_origin")]
        run.step("B1 落库: rounds 启用/切换标签=盖罩/前缀=前罩·后罩",
                 rd.get("enabled") is True and rd.get("trigger_label") == "盖罩"
                 and rd.get("prefixes") == ["前罩", "后罩"], f"rounds={rd}")
        run.step("B2 落库: 8 个轮次虚拟步骤 + 全部进序列",
                 {s["label"] for s in virt} == set(EXPECTED_VIRT)
                 and {s["id"] for s in virt} <= {it.get("step_id")
                                                 for it in (pc.get("sequence_order") or [])},
                 f"virt={sorted(s['label'] for s in virt)}")
        run.step("B3 落库: strict_order_violation_event_id=3(违序警告)",
                 pc.get("strict_order_violation_event_id") == 3,
                 f"val={pc.get('strict_order_violation_event_id')}")

        # 8 个虚拟步骤全部打上严格顺序(首步会被 first_step 结算自动豁免)
        steps_cfg = detail["steps_config"]
        for s in steps_cfg:
            if s.get("split_origin"):
                s["strict_order"] = True
        requests.put(f"{API}/projects/{pid}",
                     json={"steps_config": steps_cfg}, timeout=10).raise_for_status()
        run.step("B4 虚拟步骤严格顺序已置位(API)", True)

        # ---- C. 运行时: 激活 + synthetic + 监控页 ----
        requests.post(f"{API}/projects/{pid}/activate", timeout=15)
        requests.post(f"{API}/test/synthetic/start", json={
            "scenario_json": _scenario(), "channel": CH, "with_project": False,
        }, timeout=10).raise_for_status()
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
        run.step("C0 synthetic 两轮+违序剧本已启动", r.status_code == 200)

        # API 侧跟踪: 第1轮前罩标签 → 第2轮后罩标签 → OK 结算 → 违序计数
        deadline = time.time() + 60
        seen_front, seen_back, ok_count, vio_count = False, False, 0, 0
        rounds_rt = None
        while time.time() < deadline:
            b = requests.get(f"{API}/source/detection/results?channel={CH}",
                             timeout=5).json()
            labels = {d.get("label") for d in (b.get("detections") or [])}
            if any(str(l).startswith("前罩螺丝") for l in labels):
                seen_front = True
            if any(str(l).startswith("后罩螺丝") for l in labels):
                seen_back = True
            if b.get("label_split_rounds"):
                rounds_rt = b["label_split_rounds"]
            cnt = b.get("counters") or {}
            ok_count = cnt.get("合格总数", 0)
            vio_count = cnt.get("违序次数", 0)
            if seen_front and seen_back and ok_count >= 1 and vio_count >= 1:
                break
            time.sleep(0.5)
        run.step("C1 第1轮检测出口=前罩螺丝N", seen_front)
        run.step("C2 盖罩重现切轮后检测出口=后罩螺丝N", seen_back)
        run.step("C3 两轮8步按序结算 OK 周期", ok_count >= 1, f"合格总数={ok_count}")
        run.step("C4 越序打第3颗 → 违序警告当场触发", vio_count >= 1,
                 f"违序次数={vio_count}")
        run.step("C5 轮次运行态透出(/detection/results.label_split_rounds)",
                 bool(rounds_rt and rounds_rt.get("打螺丝", {}).get("count") == 2),
                 f"rounds={rounds_rt}")

        # 浏览器监控页人眼证据: 轮次角标画布 + 8 步骤卡 + 违序计数
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        page.reload(wait_until="domcontentloaded")
        time.sleep(8)
        m_body = page.evaluate("document.body.innerText")
        run.step("C6 监控页自动选中激活项目并接上运行态",
                 PNAME in m_body and "检测中" in m_body)
        run.shot(page, "04_monitor_rounds_a")
        time.sleep(3)
        run.shot(page, "05_monitor_rounds_b")
        m_body = page.evaluate("document.body.innerText")
        run.step("C7 监控页步骤卡显示两轮虚拟步骤",
                 all(lbl in m_body for lbl in EXPECTED_VIRT),
                 f"缺={[l for l in EXPECTED_VIRT if l not in m_body][:4]}")
        run.step("C8 监控页违序计数可见", "违序次数" in m_body)
        canvas_painted = page.evaluate(
            """() => {
              const c = document.querySelector('canvas.absolute.top-0.left-0.pointer-events-none');
              if (!c || !c.width || !c.height) return 'no-canvas';
              const ctx = c.getContext('2d');
              const d = ctx.getImageData(0, 0, c.width, c.height).data;
              let hits = 0;
              for (let i = 3; i < d.length; i += 40) { if (d[i] > 0) hits++; }
              return hits;
            }"""
        )
        run.step("C9 检测叠加画布已绘制(区域名带轮次前缀+轮次角标)",
                 isinstance(canvas_painted, (int, float)) and canvas_painted > 50,
                 f"painted_px_samples={canvas_painted}")

        requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)

        errs = filter_console_errors(cerrs)
        run.step("D1 无前端 console 错误", len(errs) == 0, f"errs={errs[:3]}")

        ctx.close()
        browser.close()
except Exception:  # noqa: BLE001 — 主流程异常必须显式记录, 不能被 finally 的 SystemExit 吞掉
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
