# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 项目页步骤/物品设置 Tab 外置为 StepsConfigTab.vue（拆分批次 P-5）。

现场叙事: 操作员在项目页打开「步骤设置」Tab, 表A(标签与检测属性)/表B(模式行为参数)/
表C(混合模式物品校验)三张表必须与拆分前一致:
  sequential → 表A+表B(顺序模式列); 结算步骤的严格顺序开关禁用(结算判定 prop 链);
               步骤ROI「设置」按钮拉起外置 ROI 编辑器(emit 链); 禁用步骤后序列同步剔除并落库;
               连续重复步骤的消失等待禁用(连续重复 prop 链)
  custom+per_item 混合 → 表A出现「角色」列, 切「物品」→ 表C出现该行, 保存后逐件参数落库
  tracking   → Tab 标签变「物品设置」, 表B为跟踪模式物品行为列

前置: 后端 8001 + 前端 6001 已启动。测试项目 __uat_ 前缀, 收尾删除。
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
SUF = uuid.uuid4().hex[:5]
P_SEQ = f"__uat_st_seq_{SUF}"
P_MIX = f"__uat_st_mix_{SUF}"
P_TRK = f"__uat_st_trk_{SUF}"

TWO_STEPS = [
    {"id": 1, "label": "step_a", "name": "步骤A", "enabled": True},
    {"id": 2, "label": "step_b", "name": "步骤B", "enabled": True},
]

run = UatRun("steps_tab_split")
pids = {}


def _mk_project(name, logic_mode, extra_pipeline=None):
    r = requests.post(f"{API}/projects", json={
        "name": name, "task_type": "detection", "logic_mode": logic_mode,
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    body = {"steps_config": [dict(s) for s in TWO_STEPS]}
    if extra_pipeline:
        body["pipeline_config"] = extra_pipeline
    requests.put(f"{API}/projects/{pid}", json=body, timeout=10).raise_for_status()
    return pid


def _open_steps_tab(page, name, tab_label="步骤设置"):
    page.locator("input[placeholder*='搜索项目']").fill(name)
    time.sleep(0.6)
    page.locator(f"div.p-4:has-text('{name}')").first.click()
    time.sleep(0.8)
    page.locator(f".el-tabs__item:has-text('{tab_label}')").first.click()
    time.sleep(0.8)
    return page.evaluate("document.body.innerText")


def _settlement_switches_disabled(page, label):
    """表B某行最后两个开关(严格顺序/单次接受)是否禁用（结算步骤 prop 链验证用）。
    注意行里还有「超时NG」开关会因无最大持续默认禁用, 不能整行找 is-disabled。"""
    return page.evaluate(
        """(label) => {
          const boxes = [...document.querySelectorAll('div.border.rounded')];
          const box = boxes.find(b => b.innerText.includes('步骤行为参数'));
          if (!box) return null;
          const row = [...box.querySelectorAll('tbody tr')].find(r => r.innerText.includes(label));
          if (!row) return null;
          const sw = [...row.querySelectorAll('.el-switch')].slice(-2);
          if (sw.length < 2) return null;
          return sw.every(s => s.classList.contains('is-disabled'));
        }""",
        label,
    )


try:
    pids["seq"] = _mk_project(P_SEQ, "sequential")
    pids["mix"] = _mk_project(P_MIX, "custom", {"custom_mixed_with": "per_item"})
    pids["trk"] = _mk_project(P_TRK, "tracking")
    print(f">>> 三个测试项目就绪: {pids}")

    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=run.video_dir)
        page.goto(f"{FRONT}/#/project", wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        time.sleep(1.5)

        # ---- 1. sequential: 表A + 表B(顺序模式) ----
        body = _open_steps_tab(page, P_SEQ)
        run.step("A1 sequential: 表A/表B 标题在",
                 "标签与检测属性" in body and "步骤行为参数 · 顺序模式" in body)
        run.step("A2 表A 两个步骤行都渲染", "step_a" in body and "step_b" in body)

        # 结算步骤 prop 链: 默认 first_step 结算 → step_a 的严格顺序/单次接受禁用, step_b 不禁
        d_a = _settlement_switches_disabled(page, "step_a")
        d_b = _settlement_switches_disabled(page, "step_b")
        run.step("A3 结算判定 prop 链: step_a 禁用开关/step_b 可用",
                 d_a is True and d_b is False, f"step_a={d_a} step_b={d_b}")
        run.shot(page, "01_seq_tables")

        # ROI emit 链: 表A step_a 行点「设置」→ 外置 RoiEditorDialog 打开
        page.locator("tbody tr:has-text('step_a') button:has-text('设置')").first.click()
        time.sleep(1.2)
        # 步骤 ROI 弹窗标题为「绘制步骤 [step_a] ROI（框中心须在区域内才算该步骤）」
        roi_dlg = page.locator(".el-dialog:has-text('绘制步骤 [step_a] ROI')")
        run.step("A4 步骤ROI按钮拉起外置编辑器(emit 链)", roi_dlg.count() > 0)
        run.shot(page, "02_seq_roi_dialog")
        page.locator(".el-dialog button:has-text('取消')").first.click()
        time.sleep(0.6)

        # 禁用 step_b → 序列/检测清单同步剔除(共享 stepEnabled.js) → 保存落库
        page.locator("tbody tr:has-text('step_b') .el-switch").first.click()
        time.sleep(0.6)
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        detail = requests.get(f"{API}/projects/{pids['seq']}", timeout=10).json()
        pc = detail.get("pipeline_config") or {}
        seq_ids = [it.get("step_id") for it in (pc.get("sequence_order") or [])]
        det_ids = pc.get("detection_steps") or []
        run.step("A5 落库: 禁用步骤已从序列/检测清单剔除",
                 2 not in seq_ids and 2 not in det_ids,
                 f"seq={seq_ids} det={det_ids}")

        # 连续重复 prop 链: 后端置序列 a,a → 刷新后 step_a 消失等待输入禁用
        requests.put(f"{API}/projects/{pids['seq']}", json={
            "pipeline_config": {**pc, "sequence_order": [{"step_id": 1}, {"step_id": 1}]},
        }, timeout=10).raise_for_status()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("text=项目管理", timeout=15000)
        time.sleep(1.2)
        _open_steps_tab(page, P_SEQ)
        dup_disabled = page.evaluate(
            """() => {
              const boxes = [...document.querySelectorAll('div.border.rounded')];
              const box = boxes.find(b => b.innerText.includes('步骤行为参数'));
              if (!box) return null;
              const row = [...box.querySelectorAll('tbody tr')].find(r => r.innerText.includes('step_a'));
              if (!row) return null;
              return !!row.querySelector('.el-input-number.is-disabled');
            }"""
        )
        run.step("A6 连续重复 prop 链: step_a 消失等待禁用", dup_disabled is True,
                 f"disabled={dup_disabled}")
        run.shot(page, "03_seq_dup_disabled")

        # ---- 2. custom+per_item 混合: 角色列 + 表C ----
        body = _open_steps_tab(page, P_MIX)
        run.step("B1 混合模式: 表A 出现「角色」列", "角色" in body)
        # step_b 角色切「物品」→ 表C 行出现(ensureMixItemDefaults 注入逐件默认参数)
        page.locator("tbody tr:has-text('step_b') .el-select").first.click()
        time.sleep(0.5)
        page.locator(".el-select-dropdown__item:visible", has_text="物品").first.click()
        time.sleep(0.8)
        body_after = page.evaluate("document.body.innerText")
        run.step("B2 表C 物品校验参数出现", "物品校验参数 · 混合逐件覆盖" in body_after)
        run.shot(page, "04_mix_table_c")
        page.locator("button:has-text('保存配置')").click()
        time.sleep(2.0)
        detail = requests.get(f"{API}/projects/{pids['mix']}", timeout=10).json()
        sb = next((s for s in (detail.get("steps_config") or []) if s.get("label") == "step_b"), {})
        run.step("B3 落库: step_b 角色=物品 + 逐件默认参数注入",
                 sb.get("detect_role") == "item" and (sb.get("per_item") or {}).get("item_label") == "step_b",
                 f"detect_role={sb.get('detect_role')} per_item={sb.get('per_item')}")

        # ---- 3. tracking: Tab 名「物品设置」+ 跟踪行为列 ----
        body = _open_steps_tab(page, P_TRK, tab_label="物品设置")
        run.step("C1 tracking: 物品行为参数(跟踪模式)在",
                 "物品行为参数 · 跟踪模式" in body and "计数模式" in body)
        run.shot(page, "05_tracking_table_b")

        errs = filter_console_errors(cerrs)
        run.step("D1 无前端 console 错误", len(errs) == 0, f"errs={errs[:3]}")

        ctx.close()
        browser.close()
finally:
    for pid in pids.values():
        try:
            requests.delete(f"{API}/projects/{pid}", timeout=10)
        except Exception:
            pass
    run.finish()
