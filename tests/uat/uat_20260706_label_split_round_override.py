# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 多轮次拆分·每轮独立区域 (v3.32 增量 — region_overrides)。

现场叙事: 电机翻面盖后罩后, 打螺丝的位置和前罩那一轮**对不上**(不重叠)。
客户在拆分编辑器里把区域列表切到「第2轮」, 单独画一批区域——第1轮继续用
共享的四象限, 第2轮只认新画的位置; 打回老位置的螺丝在第2轮不再算数。

覆盖:
  A: 编辑器 — 四象限共享区域 + 多轮次 + 切到第2轮作用域画独立区域(右上角一块,
     命名螺丝1) → 虚拟步骤 = 前罩螺丝1~4 + 后罩螺丝1(只5个, 不该出现后罩螺丝2~4)
  B: 落库 — rounds.region_overrides 只有 "2" 一轮、一块区域
  C: 运行时 — 第1轮老位置命中前罩螺丝1; 切轮后新位置命中后罩螺丝1;
     全程不得出现后罩螺丝2(若引擎错用共享区域, 新位置会落进共享螺丝2 → 立刻暴露)
  D: 监控页人眼证据 + 无 console 错误

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
PNAME = f"__uat_lso_{uuid.uuid4().hex[:5]}"
CH = 0

run = UatRun("label_split_round_override")
pid = None

COVER = {"label": "盖罩", "confidence": 0.96, "bbox": [0.35, 0.55, 0.3, 0.3]}
OLD_POS = (0.25, 0.25)   # 共享区域螺丝1(左上象限)中心
NEW_POS = (0.75, 0.25)   # 第2轮独立区域(右上)中心 = 共享螺丝2的位置 → 错用共享会报后罩螺丝2


def _screw(cx, cy):
    return {"label": "打螺丝", "confidence": 0.92,
            "bbox": [cx - 0.05, cy - 0.05, 0.1, 0.1]}


def _scenario():
    """盖罩→老位置打1颗(前罩螺丝1) → 离场4s重现(第2轮) → 老位置打(应被丢) →
    新位置打(后罩螺丝1) → 长尾拖住供监控页人眼检查。"""
    return {"name": "uat_ls_override", "fps": 60, "timeline": [
        {"from": 0, "to": 59, "detections": [COVER]},
        {"from": 60, "to": 179, "detections": [COVER, _screw(*OLD_POS)]},
        {"from": 180, "to": 419, "detections": []},                   # 离场 4s > gap 3s
        {"from": 420, "to": 479, "detections": [COVER]},              # 重现 → 第2轮
        {"from": 480, "to": 599, "detections": [COVER, _screw(*OLD_POS)]},   # 老位置 → 丢
        {"from": 600, "to": 779, "detections": [COVER, _screw(*NEW_POS)]},   # 新位置 → 后罩螺丝1
        {"from": 780, "to": 18000, "detections": [COVER, _screw(*NEW_POS)]},
    ]}


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


EXPECTED_VIRT = [f"前罩螺丝{i}" for i in range(1, 5)] + ["后罩螺丝1"]
FORBIDDEN_VIRT = ["后罩螺丝2", "后罩螺丝3", "后罩螺丝4"]

try:
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
    }, timeout=10).raise_for_status()
    run.step("A0 API 建测试项目", True, f"id={pid}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)

        # ---- A. 编辑器: 共享四象限 + 多轮次 + 第2轮独立区域(画布画一块) ----
        _open_project_steps_tab(page)
        page.locator("button:has-text('新建拆分规则')").click()
        time.sleep(1.2)
        dlg = page.locator(".el-dialog:has-text('区域定位方式')")
        run.step("A1 拆分规则编辑器打开", dlg.count() > 0)

        _fill_creatable_select(page, dlg.locator(".el-select").first, "打螺丝")
        dlg.locator("button:has-text('四象限模板')").click()
        time.sleep(0.8)
        dlg.locator(".el-switch").first.click()
        time.sleep(0.6)
        rounds_box = dlg.locator("div.border:has-text('多轮次拆分')").last
        _fill_creatable_select(page, rounds_box.locator(".el-select").first, "盖罩")

        # 切到第2轮作用域 → 应提示"沿用共享区域"
        dlg.locator(".el-radio-button:has-text('第2轮')").first.click()
        time.sleep(0.6)
        d_body = dlg.first.inner_text()
        run.step("A2 第2轮作用域默认提示沿用共享区域", "沿用共享区域" in d_body)

        # 添加区域(自动进入绘制) → 画布右上角画四点闭合
        dlg.locator("button:has-text('添加区域')").click()
        time.sleep(0.6)
        canvas = dlg.locator("canvas").first
        box = canvas.bounding_box()
        for rx, ry in [(0.55, 0.05), (0.95, 0.05), (0.95, 0.45), (0.55, 0.45)]:
            page.mouse.click(box["x"] + box["width"] * rx, box["y"] + box["height"] * ry)
            time.sleep(0.25)
        dlg.locator("button:has-text('完成绘制')").click()
        time.sleep(0.5)
        # 区域改名为 螺丝1 (第2轮里"打第1颗"的新位置)
        dlg.locator("input[placeholder*='区域名']").first.fill("螺丝1")
        time.sleep(0.4)
        d_body = dlg.first.inner_text()
        run.step("A3 画完后提示第2轮使用独立区域", "第2轮使用独立区域" in d_body)
        run.shot(page, "01_round2_override_drawn")

        dlg.locator("button:has-text('保存规则')").click()
        time.sleep(1.2)
        body = page.evaluate("document.body.innerText")
        run.step("A4 虚拟步骤 = 前罩螺丝1~4 + 后罩螺丝1(共5个)",
                 all(l in body for l in EXPECTED_VIRT)
                 and not any(l in body for l in FORBIDDEN_VIRT),
                 f"缺={[l for l in EXPECTED_VIRT if l not in body]} "
                 f"多={[l for l in FORBIDDEN_VIRT if l in body]}")
        run.step("A5 规则表标注第2轮独立区域", "第2轮独立区域" in body)
        run.shot(page, "02_five_virtual_steps")

        # 原始标签步骤禁用(由虚拟步骤参与判定)
        page.locator("tbody tr:has-text('打螺丝') .el-switch").first.click()
        time.sleep(0.5)

        # ---- B. 落库 ----
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.5)
        detail = requests.get(f"{API}/projects/{pid}", timeout=10).json()
        pc = detail.get("pipeline_config") or {}
        splits = pc.get("label_splits") or []
        ov = ((splits[0].get("rounds") or {}).get("region_overrides") or {}) if splits else {}
        virt = {s["label"] for s in (detail.get("steps_config") or []) if s.get("split_origin")}
        run.step("B1 落库: region_overrides 只有第2轮一块区域",
                 set(ov.keys()) == {"2"} and len(ov.get("2") or []) == 1
                 and ov["2"][0]["name"] == "螺丝1"
                 and len(ov["2"][0].get("polygon") or []) >= 3, f"ov={ov}")
        run.step("B2 落库: 虚拟步骤 5 个按轮展开", virt == set(EXPECTED_VIRT),
                 f"virt={sorted(virt)}")

        # ---- C. 运行时: 第2轮只认新位置 ----
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
        run.step("C0 synthetic 位移剧本已启动", r.status_code == 200)

        deadline = time.time() + 45
        seen = set()
        round2_rt = False
        while time.time() < deadline:
            b = requests.get(f"{API}/source/detection/results?channel={CH}",
                             timeout=5).json()
            for d in (b.get("detections") or []):
                lbl = str(d.get("label") or "")
                if "螺丝" in lbl:
                    seen.add(lbl)
            rt = (b.get("label_split_rounds") or {}).get("打螺丝") or {}
            if rt.get("round") == 2:
                round2_rt = True
            if "前罩螺丝1" in seen and "后罩螺丝1" in seen and round2_rt:
                break
            time.sleep(0.4)
        run.step("C1 第1轮老位置 → 前罩螺丝1", "前罩螺丝1" in seen, f"seen={sorted(seen)}")
        run.step("C2 切轮后新位置 → 后罩螺丝1", "后罩螺丝1" in seen)
        run.step("C3 第2轮不误用共享区域(后罩螺丝2 从未出现)",
                 not any(l in seen for l in FORBIDDEN_VIRT), f"seen={sorted(seen)}")
        run.step("C4 轮次运行态到达第2轮", round2_rt)

        # ---- D. 监控页人眼证据(此刻第2轮: 画布应画独立区域那一块) ----
        # hash 路由同文档导航偶发被 SPA 吞掉 → 带哈希校验重试
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
        run.step("D1 监控页接上运行态", PNAME in m_body and "检测中" in m_body,
                 f"hash={page.evaluate('location.hash')}")
        run.step("D2 监控页步骤卡含两轮虚拟步骤",
                 all(l in m_body for l in EXPECTED_VIRT),
                 f"缺={[l for l in EXPECTED_VIRT if l not in m_body]}")
        run.shot(page, "03_monitor_round2_override")

        requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)

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
