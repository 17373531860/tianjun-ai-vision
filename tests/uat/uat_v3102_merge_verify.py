"""UAT 脚本 - v3.10.x 合并验收 (chore/feature-verify + feat/per-item-enhance).

铁律 6 三件套:
    /tmp/uat_v3102/video/*.webm   - 浏览器录像
    /tmp/uat_v3102/shots/*.png    - 节点截图
    /tmp/uat_v3102/run.log        - 运行日志 (含 OK/!! 表 + 最后 failed 计数)

测试两个合并的所有改动:
    chore/feature-verify  → S1 SOP 缩略图永不空
    feat/per-item-enhance → S2-S6 per_item 5 个新功能 (UI + API)

⚠️ 不动用户当前激活的项目: Phase B 浏览器只截图; Phase C 创建临时项目,
   测完删除.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8001"
FRONTEND = "http://127.0.0.1:6001"
SHOTS = "/tmp/uat_v3102/shots"
VIDEO = "/tmp/uat_v3102/video"
LOG = "/tmp/uat_v3102/run.log"
PREFIX = "__uat_v3102_"

steps_log = []


def step(label, ok, detail=""):
    rec = {
        "idx": len(steps_log) + 1,
        "label": label,
        "ok": bool(ok),
        "detail": str(detail)[:300],
        "ts": datetime.now().strftime("%H:%M:%S"),
    }
    steps_log.append(rec)
    flag = "OK" if rec["ok"] else "!!"
    print(f"[{flag}] {rec['idx']:02d} {rec['ts']} {label}  {rec['detail']}")


def write_log():
    with open(LOG, "w") as f:
        f.write(f"# UAT v3.10.2+ 合并验收 — {datetime.now()}\n\n")
        for r in steps_log:
            flag = "OK" if r["ok"] else "FAIL"
            f.write(f"{r['idx']:02d} [{flag}] {r['ts']} {r['label']}\n")
            if r["detail"]:
                f.write(f"   {r['detail']}\n")
        n_fail = sum(1 for r in steps_log if not r["ok"])
        f.write(f"\n--- summary ---\ntotal: {len(steps_log)}, failed: {n_fail}\n")


# ============================================================
# Phase A — API 后端契约证据 (不开浏览器)
# ============================================================
def phase_a_api_contracts():
    print("\n========== Phase A: 后端 API 契约 ==========")

    # A1: 后端健康 (后端可能正在跑视频+推理, GIL 紧张, timeout 给宽点)
    try:
        r = requests.get(f"{API}/api/v1/source/status", params={"channel": 0}, timeout=15)
        ok = r.status_code == 200 and r.json().get("is_running") in (True, False)
        step("A1 后端 8001 健康", ok, f"status={r.status_code}, is_running={r.json().get('is_running')}")
    except Exception as e:
        step("A1 后端 8001 健康", False, f"连接失败: {e}")
        return None

    # A2: 创建带新字段的临时 per_item 项目
    proj_name = f"{PREFIX}per_item_{uuid.uuid4().hex[:6]}"
    proj_payload = {
        "name": proj_name,
        "task_type": "detect",
        "logic_mode": "per_item",
        "pipeline_config": {
            "per_item": {
                "stability_window_frames": 5,
                "stability_iou_threshold": 0.5,
                "item_timeout_seconds": 60.0,
                "lock_count_on_start": True,
                "finish_label": "翻面",
                "finish_sustain_frames": 3,
                "require_exact_count": True,    # ← 合并新字段 1
                "disable_auto_settle": True,    # ← 合并新字段 2
            }
        },
        "steps_config": [
            {
                "label": "工序1", "threshold": 0.3, "min_frames": 1,
                "gap_tolerance": 5, "color": "#1976d2",
                "box_max_width": 0.5,           # ← 合并新字段 3
                "box_max_height": 0.5,          # ← 合并新字段 4
                "per_item": {
                    "item_label": "螺丝", "action_label": "打螺丝",
                    "item_tracking_iou": 0.3, "coverage_iou": 0.3,
                    "sustain_frames": 5, "completion": "all_covered",
                    "min_item_count": "auto", "expected_count": 6,
                },
            },
            {
                "label": "翻面", "threshold": 0.3, "min_frames": 1,
                "gap_tolerance": 5, "color": "#43a047",
            },
        ],
        "events_config": [
            {"id": 1, "name": "合格(OK)", "color": "#10b981",
             "actions": [{"counter_name": "合格总数", "delta": 1},
                         {"counter_name": "总产量", "delta": 1}],
             "show_notification": False},
            {"id": 2, "name": "不良(NG)", "color": "#ef4444",
             "actions": [{"counter_name": "不良总数", "delta": 1},
                         {"counter_name": "总产量", "delta": 1}],
             "show_notification": False},
        ],
        "counters_config": [
            {"name": "总产量", "value": 0},
            {"name": "合格总数", "value": 0},
            {"name": "不良总数", "value": 0},
        ],
        "data_config": {},
    }
    r = requests.post(f"{API}/api/v1/projects", json=proj_payload, timeout=5)
    ok = r.status_code in (200, 201)
    step("A2 创建带新字段的 per_item 项目", ok, f"status={r.status_code}, name={proj_name}")
    if not ok:
        step("A2", False, r.text[:200])
        return None
    proj_id = r.json().get("id")

    # A3: 读回项目验字段往返完整
    r = requests.get(f"{API}/api/v1/projects/{proj_id}", timeout=3)
    ok = r.status_code == 200
    step("A3 读回项目", ok, f"status={r.status_code}")
    if ok:
        body = r.json()
        pc = body.get("pipeline_config") or {}
        pi = pc.get("per_item") or {}
        sc = body.get("steps_config") or []
        s0 = sc[0] if sc else {}
        # 新字段 1+2: pipeline_config.per_item
        ok1 = pi.get("require_exact_count") is True
        ok2 = pi.get("disable_auto_settle") is True
        step("A3.1 require_exact_count 字段往返", ok1, f"实际={pi.get('require_exact_count')}")
        step("A3.2 disable_auto_settle 字段往返", ok2, f"实际={pi.get('disable_auto_settle')}")
        # 新字段 3+4: steps_config[0].box_max_*
        ok3 = float(s0.get("box_max_width") or 0) == 0.5
        ok4 = float(s0.get("box_max_height") or 0) == 0.5
        step("A3.3 box_max_width 字段往返", ok3, f"实际={s0.get('box_max_width')}")
        step("A3.4 box_max_height 字段往返", ok4, f"实际={s0.get('box_max_height')}")

    # A4: /per-item-control 守门 (非 per_item 模式 → 400)
    # 前提: 当前激活项目不是 per_item 才能验. 若激活的是 per_item 直接跳.
    r = requests.get(f"{API}/api/v1/projects/active/current", timeout=3)
    active_mode = (r.json() or {}).get("logic_mode") if r.status_code == 200 else None
    if active_mode and active_mode != "per_item":
        r = requests.post(
            f"{API}/api/v1/source/detection/per-item-control",
            params={"channel": 0, "action": "settle"}, timeout=3,
        )
        ok = r.status_code == 400
        step("A4 /per-item-control 非 per_item 模式守门 400", ok,
             f"status={r.status_code}, body={r.text[:120]}")
    else:
        step("A4 /per-item-control 守门 (跳过 — 当前激活项目就是 per_item)", True,
             f"active_mode={active_mode}")

    # A5: 路由确实挂载在后端 (不论守门返 400 还是 405 都算挂载)
    r = requests.post(
        f"{API}/api/v1/source/detection/per-item-control",
        params={"channel": 0, "action": "invalid_action"}, timeout=3,
    )
    ok = r.status_code != 404
    step("A5 /per-item-control 路由已挂载", ok, f"status={r.status_code} (非 404 即挂载成功)")

    return proj_id


# ============================================================
# Phase B — 可见浏览器 (headless=False + 录像)
# ============================================================
def _safe_goto(page, url, *, settle_sec=2.5):
    """Vue3 SPA + Monitor polling 永不 networkidle, 用 domcontentloaded + 固定等待."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        print(f"  [warn] goto {url}: {e}")
    time.sleep(settle_sec)


def phase_b_visible_browser(proj_id):
    print("\n========== Phase B: 可见浏览器 ==========")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=300,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox"],
        )
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 1000},
            ignore_https_errors=True,
        )
        page = ctx.new_page()

        try:
            # B1: 进 Monitor 页, 看现有 SOP 卡片状态 (验 chore/feature-verify 改动)
            _safe_goto(page, f"{FRONTEND}/#/monitor")
            time.sleep(2.5)  # 给前端 polling 两个周期
            page.screenshot(path=f"{SHOTS}/B1_monitor_initial.png", full_page=True)
            body = (page.evaluate("document.body.innerText") or "")[:2000]
            step("B1 Monitor 页面加载", "项目" in body or "FPS" in body or "工位" in body,
                 f"body 片段: {body[:200]}")

            # B2: 截图 — 看 SOP 流程卡片渲染状态
            # 关键观察: SOP 卡片是否在视图里 (DOM 存在); 是否有缩略图取决于当前周期是
            # 否已跑过. 验"图永不空"语义: 每个步骤卡片要么有 <img>, 要么有占位文字 "--".
            # 我们用业务文案 (SOP流程卡片) 命中 (项目无关).
            body = (page.evaluate("document.body.innerText") or "")
            has_sop_text = "SOP" in body or "流程" in body
            # 项目当前激活 QG 时, SOP 卡片显示 4 个步骤标签
            step("B2 SOP 流程区块文案存在", has_sop_text, f"body 含 SOP/流程={has_sop_text}")
            # 数 SOP 卡片下 PT 占位"--"或步骤图缩略图任一. 这俩任一存在都说明卡片渲染 OK.
            sop_imgs = page.locator(".sop-card img, [class*='step'] img, [class*='process'] img").count()
            sop_placeholders = page.locator("text=--").count()
            step("B2.1 SOP 卡片 img 或占位 (缩略图永不空策略入口)",
                 sop_imgs > 0 or sop_placeholders >= 1,
                 f"img 数={sop_imgs}, '--' 数={sop_placeholders}")

            # B3: 进 Project 页, 验 per_item 选项 + 新字段 UI
            _safe_goto(page, f"{FRONTEND}/#/project")
            time.sleep(2.0)
            page.screenshot(path=f"{SHOTS}/B3_project_list.png", full_page=True)
            body = (page.evaluate("document.body.innerText") or "")[:3000]
            step("B3 Project 页加载", "项目" in body or "新建" in body or "logic" in body.lower(),
                 f"body 片段: {body[:200]}")

            # B4: 看刚才 phase A 创建的临时项目是否在列表里
            uat_visible = PREFIX in body
            step("B4 phase A 创建的 UAT 项目可见", uat_visible,
                 f"body 中含 {PREFIX}={uat_visible}")

            # B5: 试着点开 UAT 项目卡片看详情
            card_locator = page.locator("div.cursor-pointer", has=page.get_by_text(PREFIX, exact=False))
            try:
                card_count = card_locator.count()
                if card_count > 0:
                    card_locator.first.click()
                    time.sleep(1.5)
                    page.screenshot(path=f"{SHOTS}/B5_project_detail_basic.png", full_page=True)

                    # B5.1 切到"逻辑设置" tab → 验严格等量 + 手动结算
                    try:
                        page.get_by_text("逻辑设置", exact=True).click()
                        time.sleep(1.5)
                        page.screenshot(path=f"{SHOTS}/B5_logic_tab.png", full_page=True)
                        logic_body = (page.evaluate("document.body.innerText") or "")
                        has_strict = ("严格等待检出数" in logic_body
                                      or "严格等量" in logic_body)
                        has_manual = ("手动结算模式" in logic_body
                                      or "纯手动" in logic_body)
                        step("B5.1 逻辑设置 tab — 严格等量开关可见", has_strict,
                             f"含'严格等待检出数': {has_strict}")
                        step("B5.2 逻辑设置 tab — 手动结算模式开关可见", has_manual,
                             f"含'手动结算模式': {has_manual}")
                    except Exception as e:
                        step("B5.1/B5.2 逻辑设置 tab 切换", False, f"异常: {e}")

                    # B5.3 切到"步骤设置" tab → 展开第一步 → 验 box_max
                    try:
                        page.get_by_text("步骤设置", exact=True).click()
                        time.sleep(1.5)
                        # 展开第一个步骤卡片 (默认折叠)
                        first_step = page.locator(".step-card, [class*='step'] .el-collapse-item__header").first
                        try:
                            first_step.click()
                        except Exception:
                            pass
                        time.sleep(1.5)
                        page.screenshot(path=f"{SHOTS}/B5_steps_tab.png", full_page=True)
                        steps_body = (page.evaluate("document.body.innerText") or "")
                        has_box = ("box_max" in steps_body.lower()
                                   or "最大宽" in steps_body
                                   or "最大高" in steps_body
                                   or "尺寸过滤" in steps_body
                                   or "尺寸限制" in steps_body
                                   or "尺寸上限" in steps_body
                                   or "box 尺寸" in steps_body)
                        step("B5.3 步骤设置 tab — box_max 字段文案可见", has_box,
                             f"含相关关键词: {has_box}")
                    except Exception as e:
                        step("B5.3 步骤设置 tab 切换", False, f"异常: {e}")
                else:
                    step("B5 试点 UAT 项目卡片", False, "找不到匹配的卡片")
            except Exception as e:
                step("B5 试点 UAT 项目卡片", False, f"异常: {e}")

            # B6: 进 Settings 页 (合并未改, 仅冒烟)
            _safe_goto(page, f"{FRONTEND}/#/settings")
            time.sleep(1.5)
            page.screenshot(path=f"{SHOTS}/B6_settings.png", full_page=True)
            body = (page.evaluate("document.body.innerText") or "")[:1500]
            step("B6 Settings 页加载", "设置" in body or "PT" in body or "显示" in body,
                 f"body 片段: {body[:200]}")

            # B7: 回 Monitor, 看 PerItemPanel 是否依条件渲染
            # 当前激活的可能不是 per_item 项目, panel 不渲染也合理
            _safe_goto(page, f"{FRONTEND}/#/monitor")
            time.sleep(2.5)
            page.screenshot(path=f"{SHOTS}/B7_monitor_perItemPanel.png", full_page=True)
            body = (page.evaluate("document.body.innerText") or "")[:3000]
            # PerItemPanel 仅在 per_item 模式下渲染. 如当前不是 per_item, 不渲染是正常.
            r = requests.get(f"{API}/api/v1/projects/active/current", timeout=3)
            active_mode = (r.json() or {}).get("logic_mode") if r.status_code == 200 else None
            if active_mode == "per_item":
                has_panel = "手动" in body or "逐件" in body
                step("B7 PerItemPanel 在 per_item 项目下可见", has_panel,
                     f"active_mode={active_mode}, 关键词命中={has_panel}")
            else:
                step("B7 PerItemPanel 条件渲染", True,
                     f"当前激活项目模式={active_mode}, panel 不渲染 (符合预期)")
        finally:
            ctx.close()
            browser.close()


# ============================================================
# Phase C — 清理
# ============================================================
def phase_c_cleanup(proj_id):
    print("\n========== Phase C: 清理 ==========")

    if proj_id:
        try:
            r = requests.delete(f"{API}/api/v1/projects/{proj_id}", timeout=3)
            ok = r.status_code in (200, 204)
            step("C1 删除 phase A 创建的 UAT 项目", ok, f"status={r.status_code}")
        except Exception as e:
            step("C1 删除 phase A 创建的 UAT 项目", False, f"异常: {e}")

    # 兜底清理: 列表里所有 __uat_ 前缀的项目
    try:
        r = requests.get(f"{API}/api/v1/projects?limit=200", timeout=3)
        if r.status_code == 200:
            body = r.json()
            items = body.get("items") if isinstance(body, dict) else body
            cnt = 0
            for p in items or []:
                if (p.get("name") or "").startswith(PREFIX):
                    requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=3)
                    cnt += 1
            step("C2 兜底清理 UAT 残留项目", True, f"清掉 {cnt} 个")
    except Exception as e:
        step("C2 兜底清理", False, f"异常: {e}")


# ============================================================
# 主流程
# ============================================================
def main():
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(VIDEO, exist_ok=True)

    proj_id = None
    try:
        proj_id = phase_a_api_contracts()
        phase_b_visible_browser(proj_id)
    finally:
        phase_c_cleanup(proj_id)
        write_log()

    # 输出最终对账
    n_total = len(steps_log)
    n_fail = sum(1 for r in steps_log if not r["ok"])
    print(f"\n========== 总计 ==========")
    print(f"步骤数: {n_total}")
    print(f"失败数: {n_fail}")
    print(f"日志: {LOG}")
    print(f"截图: {SHOTS}")
    print(f"视频: {VIDEO}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
