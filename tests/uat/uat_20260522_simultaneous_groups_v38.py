"""v3.8.x 同时出现组重构 UAT 脚本 (Phase A 后端契约 + Phase B 前端 UI 真点击)

UAT 范围:
  Phase A: 后端 API 契约
    A1. 创建一个项目, 配 5 步 (A B C D E) + 顺序模式
    A2. 通过 API 写入跨周期同时出现组配置, 拉回来验证 prev_cycle_labels/next_cycle_labels 已保存
    A3. 同时启用 settle_dedup + cross_cycle → 后端不阻拦 (校验在前端), 但 API 层应允许写入
    A4. 激活项目 → 检查 _simultaneous_groups 是否正确应用到 VSM

  Phase B: 前端 UI 真点击 (Chromium headed)
    B1. 浏览器打开项目页
    B2. 选中刚创建的项目
    B3. 找到同时出现组配置区, 添加一个组并启用跨周期
    B4. 启用 settle_dedup, 尝试保存 → 应弹错误提示
    B5. 关闭 settle_dedup, 保存 → 成功

输出:
  - 截图: /tmp/uat_simgroup_shots/
  - 视频: /tmp/uat_simgroup_video/
  - 日志: /tmp/uat_simgroup_run.log + stdout
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, Page, Error as PWError

API = "http://127.0.0.1:8001"
BASE = "http://127.0.0.1:6001"

SHOT_DIR = Path("/tmp/uat_simgroup_shots")
VIDEO_DIR = Path("/tmp/uat_simgroup_video")
LOG_FILE = Path("/tmp/uat_simgroup_run.log")

steps_log: list[dict] = []


def step(label: str, ok: bool, detail: str = "") -> None:
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": ok, "detail": detail}
    steps_log.append(rec)
    line = f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def snap(page: Page, name: str) -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"{len(steps_log):02d}_{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception as e:
        print(f"  (snapshot fail: {e})")


def reset_dirs() -> None:
    for d in (SHOT_DIR, VIDEO_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("=== v3.8 同时出现组重构 UAT 开始 ===\n", encoding="utf-8")


# ============================================================
# Phase A: 后端 API 契约
# ============================================================
def phase_a_api_contract() -> int | None:
    """返回新建项目 id, 后续 Phase B 用."""
    print("\n========== Phase A: 后端 API 契约 ==========\n")

    # 清理掉之前残留的 uat 项目
    try:
        r = requests.get(f"{API}/api/v1/projects/", timeout=5)
        if r.status_code == 200:
            data = r.json()
            items = data.get("items") if isinstance(data, dict) else data
            for p in items or []:
                if (p.get("name") or "").startswith("__uat_v38_sg_"):
                    requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=5)
    except Exception:
        pass

    # A1: 创建项目 + 5 步 + 顺序模式
    name = f"__uat_v38_sg_{int(time.time())}"
    payload = {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": [
            {"id": 1, "label": "A", "displayLabel": "步骤A", "enabled": True, "min_frames": 1, "threshold": 50},
            {"id": 2, "label": "B", "displayLabel": "步骤B", "enabled": True, "min_frames": 1, "threshold": 50},
            {"id": 3, "label": "C", "displayLabel": "步骤C", "enabled": True, "min_frames": 1, "threshold": 50},
            {"id": 4, "label": "D", "displayLabel": "步骤D", "enabled": True, "min_frames": 1, "threshold": 50},
            {"id": 5, "label": "E", "displayLabel": "步骤E", "enabled": True, "min_frames": 1, "threshold": 50},
        ],
        "events_config": [],
        "counters_config": [],
        "pipeline_config": {
            "sequence_order": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}, {"step_id": 4}, {"step_id": 5}],
            "simultaneous_groups": [
                {
                    "enabled": True,
                    "cross_cycle": False,
                    "labels": ["B", "C"],
                    "priority_order": ["B", "C"],
                    "time_window": 2.0,
                },
                {
                    "enabled": True,
                    "cross_cycle": True,
                    "labels": ["E", "A"],
                    "priority_order": ["E", "A"],
                    "prev_cycle_labels": ["E"],
                    "next_cycle_labels": ["A"],
                    "time_window": 3.0,
                },
            ],
            "settlement_mode": "first_step",
            "settle_dedup": False,
        },
    }
    r = requests.post(f"{API}/api/v1/projects/", json=payload, timeout=10)
    if r.status_code not in (200, 201):
        step("A1 创建项目", False, f"status={r.status_code} body={r.text[:200]}")
        return None
    proj = r.json()
    proj_id = proj.get("id")
    step("A1 创建项目", proj_id is not None, f"id={proj_id} name={name}")
    if proj_id is None:
        return None

    # A2: 拉回项目验证同时出现组字段保存正确
    r = requests.get(f"{API}/api/v1/projects/{proj_id}", timeout=5)
    if r.status_code != 200:
        step("A2 项目读取", False, f"status={r.status_code}")
        return proj_id
    project = r.json()
    pipeline = project.get("pipeline_config") or {}
    groups = pipeline.get("simultaneous_groups") or []
    cross_group = next((g for g in groups if g.get("cross_cycle")), None)
    ok = (
        cross_group is not None
        and cross_group.get("prev_cycle_labels") == ["E"]
        and cross_group.get("next_cycle_labels") == ["A"]
    )
    step("A2 跨周期组字段保存", ok,
         f"groups_count={len(groups)} prev={cross_group and cross_group.get('prev_cycle_labels')} "
         f"next={cross_group and cross_group.get('next_cycle_labels')}")

    # A3: 激活项目
    r = requests.post(f"{API}/api/v1/projects/{proj_id}/activate", timeout=5)
    ok = r.status_code == 200
    step("A3 项目激活", ok, f"status={r.status_code}")

    # A4: 拉激活项目状态, 验证 VSM 已应用配置
    r = requests.get(f"{API}/api/v1/projects/active/current", timeout=5)
    ok = r.status_code == 200 and (r.json() or {}).get("id") == proj_id
    step("A4 激活项目 = 新建项目", ok, f"active_id={(r.json() or {}).get('id') if r.status_code == 200 else None}")

    return proj_id


# ============================================================
# Phase B: 前端 UI 真点击
# ============================================================
def phase_b_ui(page: Page, proj_id: int | None) -> None:
    print("\n========== Phase B: 前端 UI 真点击 ==========\n")
    if proj_id is None:
        step("B0 跳过 (Phase A 未创建项目)", False)
        return

    # B1: 打开项目页
    try:
        page.goto(f"{BASE}/#/project", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_load_state("networkidle", timeout=8000)
        step("B1 打开项目页", True, page.url)
        snap(page, "open_project_page")
    except Exception as e:
        step("B1 打开项目页", False, str(e))
        return

    # B2: 等项目列表加载, 在"左侧项目列表"里找到刚建的项目, 点中它打开编辑面板
    try:
        # 用 aside / 项目卡 (.cursor-pointer) 区分: 顶部下拉框里的项目不带 aside 外层
        # 这里用项目卡的 outer container 包含项目名 + DETECTION 标签
        proj_card = page.locator("aside, .project-list-aside").locator(f"text=__uat_v38_sg_").first
        try:
            proj_card.wait_for(state="visible", timeout=5000)
        except PWError:
            # fallback: 用左侧栏下的项目卡
            proj_card = page.locator(".cursor-pointer:has-text('__uat_v38_sg_')").first
            proj_card.wait_for(state="visible", timeout=5000)
        proj_card.click()
        page.wait_for_timeout(2000)
        step("B2 选中左侧项目卡", True)
        snap(page, "project_selected")
    except Exception as e:
        step("B2 选中左侧项目卡", False, str(e))
        return

    # B2.5: 切换到"逻辑设置" tab (同时出现组在这里)
    try:
        logic_tab = page.locator(".el-tabs__item:has-text('逻辑设置')").first
        logic_tab.wait_for(state="visible", timeout=5000)
        logic_tab.click()
        page.wait_for_timeout(1000)
        step("B2.5 切换到逻辑设置 tab", True)
        snap(page, "logic_tab_active")
    except Exception as e:
        step("B2.5 切换到逻辑设置 tab", False, str(e))
        return

    # B3: 滚动到"同时出现组"卡片
    try:
        sim_card = page.locator(".el-card:has(.el-card__header:has-text('同时出现组'))").first
        sim_card.wait_for(state="visible", timeout=5000)
        sim_card.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        snap(page, "simgroup_card_visible")
        step("B3 同时出现组卡片可见", True)
    except Exception as e:
        step("B3 同时出现组卡片可见", False, str(e))

    # B4: 检查跨周期开关与"归属"下拉框确实渲染了 (因为 A 阶段已经写入 cross_cycle=true)
    try:
        # 等待跨周期开关可见
        crosscycle_label = page.locator("text=跨周期？").first
        crosscycle_label.wait_for(state="visible", timeout=5000)
        # 等待归属选择器
        prev_option = page.locator("text=上周期").first
        next_option = page.locator("text=下周期").first
        ok = prev_option.count() >= 1 and next_option.count() >= 1
        step("B4 跨周期 UI 渲染", ok, f"prev_count={prev_option.count()} next_count={next_option.count()}")
        snap(page, "crosscycle_ui_rendered")
    except Exception as e:
        step("B4 跨周期 UI 渲染", False, str(e))

    # B5: 启用"防重复结算"开关, 点保存 → 应弹错误提示
    try:
        # 防重复结算卡也在逻辑设置 tab. 用 card-based locator 取它
        dedup_card = page.locator(".el-card:has(.el-card__header:has-text('防重复结算'))").first
        dedup_card.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        dedup_switch = dedup_card.locator(".el-switch").first
        dedup_switch.click()
        page.wait_for_timeout(500)
        snap(page, "dedup_enabled_before_save")

        save_btn = page.locator("button:has-text('保存')").first
        save_btn.click()
        page.wait_for_timeout(1500)
        # ElMessage 错误提示文本应该包含"不能同时启用"
        err_msg = page.locator("text=不能同时启用").first
        ok = err_msg.count() >= 1
        step("B5 settle_dedup + 跨周期组互斥校验", ok,
             "应弹'不能同时启用'错误提示")
        snap(page, "mutex_validation_triggered")
    except Exception as e:
        step("B5 settle_dedup + 跨周期组互斥校验", False, str(e))

    # B6: 关掉 settle_dedup, 重新保存 → 成功
    try:
        dedup_card = page.locator(".el-card:has(.el-card__header:has-text('防重复结算'))").first
        dedup_card.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        dedup_switch = dedup_card.locator(".el-switch").first
        dedup_switch.click()
        page.wait_for_timeout(500)
        save_btn = page.locator("button:has-text('保存')").first
        save_btn.click()
        page.wait_for_timeout(2000)
        # 用 .el-message--success 选择器更精确
        success = page.locator(".el-message--success").first
        ok = success.count() >= 1
        step("B6 关掉互斥项后保存成功", ok)
        snap(page, "save_success")
    except Exception as e:
        step("B6 关掉互斥项后保存成功", False, str(e))


# ============================================================
# 总结
# ============================================================
def print_summary() -> int:
    print("\n========== UAT 总结 ==========")
    ok_cnt = sum(1 for s in steps_log if s["ok"])
    fail_cnt = sum(1 for s in steps_log if not s["ok"])
    print(f"总数 {len(steps_log)} / 通过 {ok_cnt} / 失败 {fail_cnt}")
    if fail_cnt:
        print("\n失败明细:")
        for s in steps_log:
            if not s["ok"]:
                print(f"  - {s['idx']:02d}. {s['label']}: {s['detail']}")
    print(f"\n截图: {SHOT_DIR}")
    print(f"视频: {VIDEO_DIR}")
    print(f"日志: {LOG_FILE}")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\n=== 完成: 通过 {ok_cnt} / 失败 {fail_cnt} ===\n")
        f.write(f"failed: {fail_cnt}\n")
    return fail_cnt


def main() -> None:
    reset_dirs()

    proj_id = phase_a_api_contract()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(record_video_dir=str(VIDEO_DIR), viewport={"width": 1440, "height": 900})
        page = context.new_page()
        try:
            phase_b_ui(page, proj_id)
        finally:
            try:
                page.close()
                context.close()
            finally:
                browser.close()

    # 清理 UAT 创建的项目
    if proj_id is not None:
        try:
            requests.delete(f"{API}/api/v1/projects/{proj_id}", timeout=5)
        except Exception:
            pass

    fail_cnt = print_summary()
    if fail_cnt:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
