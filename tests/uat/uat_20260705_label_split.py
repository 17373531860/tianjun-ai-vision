# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 同标签区域拆分（虚拟步骤）+ 工件就位提示 (v3.32)。

现场叙事: 电机装配线, 模型只出「打螺丝」一个标签, 前/后罩各 4 颗螺丝要求对角顺序,
螺丝本体被罩子遮挡只能靠"打的位置"区分。客户在项目页配置一条拆分规则(四象限),
区域名自动生成虚拟步骤 螺丝1~4 进顺序判定; 监控页画面上常驻显示拆分区域 + 就位引导框,
打螺丝动作按所在象限被改写成 螺丝N 计步, 打完四颗按顺序结算 OK。

覆盖:
  A: 项目页 — 拆分规则卡片/编辑器(新建规则+四象限模板)/就位提示卡片(开关+画引导框)
  B: 落库双向 — 保存配置 → GET 复核 label_splits/placement_guide/虚拟步骤/序列
  C: 监控页运行时 — synthetic 剧本注入「打螺丝」四象限先后出现 → 画面出现区域叠加 +
     检测框标签=螺丝N + 就位提示 + 步骤计数/OK 周期增长
  D: 规则删除级联 — UI 删除规则 → 虚拟步骤 + 序列引用一并清掉并落库

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
PNAME = f"__uat_ls_{SUF}"
CH = 0

run = UatRun("label_split")
pid = None


# ==================== synthetic 剧本: 打螺丝按象限 1→2→3→4 循环 ====================

def _screw(cx, cy):
    return {"label": "打螺丝", "confidence": 0.92,
            "bbox": [cx - 0.05, cy - 0.05, 0.1, 0.1]}


ANCHOR = {"label": "前罩", "confidence": 0.97, "bbox": [0.2, 0.2, 0.6, 0.6]}


def _scenario(loops=40):
    """每轮: 四象限各 30 帧 + 10 帧空档 (fps=60 ≈ 2.7s/轮)。前罩全程在场。
    first_step 结算: 下一轮的螺丝1 出现即收掉上一周期 → OK 计数持续增长。"""
    tl = [{"from": 0, "to": 9, "detections": [ANCHOR]}]
    quads = [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)]
    f = 10
    for _ in range(loops):
        for cx, cy in quads:
            tl.append({"from": f, "to": f + 29, "detections": [ANCHOR, _screw(cx, cy)]})
            tl.append({"from": f + 30, "to": f + 39, "detections": [ANCHOR]})
            f += 40
    tl.append({"from": f, "to": f + 600, "detections": [ANCHOR]})
    return {"name": "uat_label_split_loop", "fps": 60, "timeline": tl}


# ==================== UI 小助手 ====================

def _open_project_steps_tab(page):
    page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
    page.wait_for_selector("text=项目管理", timeout=15000)
    time.sleep(1.2)
    page.locator("input[placeholder*='搜索项目']").fill(PNAME)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{PNAME}')").first.click()
    time.sleep(0.8)
    page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
    time.sleep(0.8)


def _fill_creatable_select(page, scope, text):
    """el-select(filterable allow-create): 点开 → 敲字 → 回车创建/选中。"""
    scope.click()
    time.sleep(0.4)
    page.keyboard.type(text, delay=40)
    time.sleep(0.5)
    page.keyboard.press("Enter")
    time.sleep(0.4)


def _canvas_draw_polygon(page, canvas, rel_points):
    """在 canvas 上按相对坐标序列点点, 再点「完成绘制」闭合。"""
    box = canvas.bounding_box()
    for rx, ry in rel_points:
        page.mouse.click(box["x"] + box["width"] * rx, box["y"] + box["height"] * ry)
        time.sleep(0.25)


# ==================== 主流程 ====================

try:
    # ---- 0. API 建项目 (打螺丝 + 前罩 两个原始标签步骤) ----
    r = requests.post(f"{API}/projects", json={
        "name": PNAME, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/projects/{pid}", json={"steps_config": [
        {"id": 1, "label": "打螺丝", "displayLabel": "打螺丝", "enabled": True, "threshold": 50},
        {"id": 2, "label": "前罩", "displayLabel": "前罩", "enabled": True, "threshold": 50},
    ]}, timeout=10).raise_for_status()
    run.step("A0 API 建测试项目(打螺丝/前罩)", True, f"id={pid}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)

        # ---- A. 项目页: 卡片在 + 新建规则 ----
        _open_project_steps_tab(page)
        body = page.evaluate("document.body.innerText")
        run.step("A1 步骤设置页出现「同标签区域拆分」+「工件就位提示」卡片",
                 "同标签区域拆分" in body and "工件就位提示" in body)
        run.shot(page, "01_steps_tab_cards")

        page.locator("button:has-text('新建拆分规则')").click()
        time.sleep(1.2)
        # 注意: 对话框标题会随选中原始标签动态变 (新建… → 拆分规则·打螺丝),
        # 用框内固定文案「区域定位方式」定位, 不用标题
        dlg = page.locator(".el-dialog:has-text('区域定位方式')")
        run.step("A2 拆分规则编辑器打开", dlg.count() > 0)

        # 原始标签 = 打螺丝 (allow-create select)
        _fill_creatable_select(page, dlg.locator(".el-select").first, "打螺丝")
        # 四象限模板一键生成 4 区域 (区域名在 input.value 里, innerText 拿不到)
        dlg.locator("button:has-text('四象限模板')").click()
        time.sleep(0.8)
        input_vals = dlg.locator("input").evaluate_all(
            "els => els.map(e => e.value).join('|')")
        run.step("A3 四象限模板生成 螺丝1~4 区域",
                 all(f"螺丝{i}" in input_vals for i in range(1, 5)),
                 f"inputs={input_vals[:120]}")
        run.shot(page, "02_split_dialog_quadrant")

        dlg.locator("button:has-text('保存规则')").click()
        time.sleep(1.2)
        body = page.evaluate("document.body.innerText")
        run.step("A4 保存规则后规则行 + 虚拟步骤(带拆分徽标)出现在步骤表",
                 "螺丝1" in body and "拆分" in body and "固定画面" in body)
        run.shot(page, "03_rule_row_and_virtual_steps")

        # 关掉原始标签步骤 打螺丝/前罩 的启用 (被拆分/是锚点, 不参与序列)
        for lbl in ("打螺丝", "前罩"):
            page.locator(f"tbody tr:has-text('{lbl}') .el-switch").first.click()
            time.sleep(0.5)
        run.step("A5 原始标签步骤已禁用(拆分后由虚拟步骤参与判定)", True)

        # ---- 就位提示: 开开关 + 锚点标签 + 画引导框 ----
        guide_card = page.locator("div.border.rounded:has-text('工件就位提示')")
        guide_card.locator(".el-switch").first.click()
        time.sleep(0.6)
        _fill_creatable_select(page, guide_card.locator(".el-select").first, "前罩")
        guide_card.locator("button:has-text('绘制引导框')").click()
        time.sleep(1.5)
        roi_dlg = page.locator(".el-dialog:has-text('绘制工件就位引导框')")
        run.step("A6 就位引导框编辑器打开(复用ROI编辑器)", roi_dlg.count() > 0)
        canvas = roi_dlg.locator("canvas").first
        _canvas_draw_polygon(page, canvas, [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)])
        roi_dlg.locator("button:has-text('完成绘制')").click()
        time.sleep(0.5)
        run.shot(page, "04_guide_polygon_drawn")
        roi_dlg.locator("button:has-text('保存 ROI')").click()
        time.sleep(0.8)
        run.step("A7 引导框已保存(4顶点)", "已保存" in page.evaluate("document.body.innerText")
                 or guide_card.locator("svg polygon").count() > 0)

        # ---- B. 保存配置 → GET 复核落库 ----
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.5)
        detail = requests.get(f"{API}/projects/{pid}", timeout=10).json()
        pc = detail.get("pipeline_config") or {}
        splits = pc.get("label_splits") or []
        sc = detail.get("steps_config") or []
        virt = [s for s in sc if s.get("split_origin")]
        virt_labels = {s.get("label") for s in virt}
        seq_ids = [it.get("step_id") for it in (pc.get("sequence_order") or [])]
        virt_ids = {s["id"] for s in virt}
        pg = pc.get("placement_guide") or {}
        run.step("B1 落库: label_splits 1条规则/4区域/源标签=打螺丝",
                 len(splits) == 1 and splits[0].get("source_label") == "打螺丝"
                 and len(splits[0].get("regions") or []) == 4,
                 f"splits={len(splits)} regions={len((splits[0].get('regions') or [])) if splits else 0}")
        run.step("B2 落库: 虚拟步骤 螺丝1~4 带 split_origin",
                 virt_labels == {f"螺丝{i}" for i in range(1, 5)}, f"virt={sorted(virt_labels)}")
        run.step("B3 落库: 序列=4个虚拟步骤(原始标签已禁用剔除)",
                 set(seq_ids) == virt_ids and 1 not in seq_ids and 2 not in seq_ids,
                 f"seq={seq_ids}")
        run.step("B4 落库: placement_guide 启用+锚点=前罩+4顶点",
                 pg.get("enabled") is True and pg.get("anchor_label") == "前罩"
                 and len(pg.get("polygon") or []) >= 3,
                 f"pg={ {k: (len(v) if k == 'polygon' and v else v) for k, v in pg.items()} }")

        # 编辑器回显: 点编辑 → 4 区域回显 → 取消
        page.locator("tbody tr:has-text('固定画面') button:has-text('编辑')").first.click()
        time.sleep(1.2)
        edlg = page.locator(".el-dialog:has-text('拆分规则 · 打螺丝')")
        e_body = page.evaluate("document.body.innerText")
        run.step("B5 编辑回显: 规则/4区域完整回显", edlg.count() > 0
                 and all(f"螺丝{i}" in e_body for i in range(1, 5)))
        run.shot(page, "05_rule_edit_echo")
        edlg.locator("button:has-text('取消')").click()
        time.sleep(0.6)

        # ---- C. 运行时: synthetic 剧本 + 监控页叠加 ----
        # 先经 API 激活 + 启动, 再进监控页: Navbar loadProjects 会按后端 is_active
        # 自动选中本项目, autoRestoreSource 看到后端已在跑 → 自动接上轮询与视频流
        # (监控页挂载后才启动检测的话, 页面不会自己开始轮询)
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
        run.step("C0 synthetic 剧本 + 检测已启动", r.status_code == 200, f"status={r.status_code}")

        # API 侧: 虚拟标签到达前端出口 + 就位态 + OK 周期
        deadline = time.time() + 30
        seen_virtual, in_pos, ok_count = False, False, 0
        while time.time() < deadline:
            b = requests.get(f"{API}/source/detection/results?channel={CH}", timeout=5).json()
            labels = {d.get("label") for d in (b.get("detections") or [])}
            if any(str(l).startswith("螺丝") for l in labels):
                seen_virtual = True
            if (b.get("placement_guide") or {}).get("in_position"):
                in_pos = True
            ok_count = (b.get("counters") or {}).get("合格总数", 0)
            if seen_virtual and in_pos and ok_count >= 1:
                break
            time.sleep(0.5)
        run.step("C1 检测出口标签=虚拟步骤(螺丝N), 原名不再出现", seen_virtual)
        run.step("C2 就位提示运行态 in_position=True", in_pos)
        run.step("C3 顺序模式按虚拟步骤结算出 OK 周期", ok_count >= 1, f"合格总数={ok_count}")

        # 浏览器监控页: 区域叠加 + 引导框 + 螺丝N 框 (人眼证据 = 截图/录像)
        # hash 路由切换不整页重载 → Navbar 不会重拉项目列表, 必须 reload 让它
        # 按后端 is_active 自动选中本项目
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        page.reload(wait_until="domcontentloaded")
        time.sleep(8)
        m_body = page.evaluate("document.body.innerText")
        run.step("C-pre 监控页自动选中激活项目并接上运行态",
                 PNAME in m_body and "检测中" in m_body)
        run.shot(page, "06_monitor_overlay_a")
        time.sleep(3)
        run.shot(page, "07_monitor_overlay_b")
        m_body = page.evaluate("document.body.innerText")
        run.step("C4 监控页步骤卡显示虚拟步骤 螺丝1~4",
                 all(f"螺丝{i}" in m_body for i in range(1, 5)))
        # 只查视频区那块检测叠加 canvas (absolute 定位那张), 不能全局扫 —— ECharts 也是 canvas
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
        run.step("C5 检测叠加画布已绘制(区域/引导框非空)",
                 isinstance(canvas_painted, (int, float)) and canvas_painted > 50,
                 f"painted_px_samples={canvas_painted}")

        requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/test/synthetic/stop?channel={CH}", timeout=10)

        # ---- D. 规则删除级联 ----
        _open_project_steps_tab(page)
        page.locator("tbody tr:has-text('固定画面') button:has-text('删除')").first.click()
        time.sleep(0.8)
        page.locator(".el-message-box button:has-text('删除')").click()
        # 成功 toast 文案里带被移除的虚拟步骤名, 等 toast 消失再查表, 免得误判
        time.sleep(4.0)
        steps_table = page.locator("div.border.rounded:has-text('标签与检测属性')").first.inner_text()
        run.step("D1 删除规则后虚拟步骤从步骤表消失",
                 all(f"螺丝{i}" not in steps_table for i in range(1, 5)),
                 f"table={steps_table[:80]}")
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.5)
        detail = requests.get(f"{API}/projects/{pid}", timeout=10).json()
        pc = detail.get("pipeline_config") or {}
        sc = detail.get("steps_config") or []
        seq_ids = [it.get("step_id") for it in (pc.get("sequence_order") or [])]
        run.step("D2 落库: 规则清空 + 虚拟步骤/序列引用级联移除",
                 not (pc.get("label_splits") or [])
                 and not any(s.get("split_origin") for s in sc)
                 and not any(sid in seq_ids for sid in virt_ids),
                 f"splits={pc.get('label_splits')} seq={seq_ids}")
        run.shot(page, "08_after_rule_delete")

        errs = filter_console_errors(cerrs)
        run.step("E1 无前端 console 错误", len(errs) == 0, f"errs={errs[:3]}")

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
