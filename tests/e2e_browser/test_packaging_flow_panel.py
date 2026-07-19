"""配置面板 E2E — 包装结算 (上银包装线, v3.21 M6).

铁律「带 UI 的功能改动必须走 E2E 点击验证」: 验证 PackagingFlowPanel 的
新建 → 一键套用上银预设 → 保存 整条链路真把字段传到后端并落库
(拦下 axios 漏传字段 / 表单校验阻断 / 预设按钮点不动 / 保存按钮 disabled 等前端 bug).

前置: 后端 8001 + 前端 6001 已起 (conftest 没起则整组 skip).
"""
import re
import uuid

import requests


def _cleanup_pkg(api_url):
    try:
        r = requests.get(f"{api_url}/api/v1/packaging-flows", timeout=10)
        for c in (r.json() or {}).get("items", []):
            if str(c.get("name", "")).startswith("__e2e_"):
                if c.get("enabled"):
                    requests.put(f"{api_url}/api/v1/packaging-flows/{c['id']}",
                                 json={"enabled": False}, timeout=10)
                requests.delete(f"{api_url}/api/v1/packaging-flows/{c['id']}", timeout=10)
    except Exception:
        pass


def test_packaging_panel_create_with_hiwin_preset(page, base_url, api_url):
    _cleanup_pkg(api_url)
    name = f"__e2e_pkg_{uuid.uuid4().hex[:6]}"

    page.goto(f"{base_url}/#/settings", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_load_state("networkidle")

    # 切到「包装箱结算」Tab
    page.get_by_role("tab", name="包装箱结算").click()
    page.wait_for_timeout(800)

    # 新建配置 → 对话框
    page.get_by_role("button", name="新建配置").click()
    page.wait_for_selector(".el-dialog", state="visible", timeout=5000)

    # 填配置名称
    page.locator('input[placeholder="上银包装线-1"]').fill(name)

    # 一键套用上银预设
    page.get_by_role("button", name=re.compile("一键套用")).click()
    page.wait_for_timeout(500)

    # 保存 (对话框 footer 内的"保存")
    page.locator(".el-dialog__footer").get_by_role("button", name="保存").first.click()
    page.wait_for_timeout(1200)

    # ── 后端契约: 字段真落库 + 预设值正确 ──
    r = requests.get(f"{api_url}/api/v1/packaging-flows", timeout=10)
    found = [c for c in (r.json() or {}).get("items", []) if c.get("name") == name]
    assert found, f"配置 {name} 未落库 (axios 可能漏传/保存按钮没点动)"
    cfg = found[0]
    # v3.22 起上银预设 = insert_char 补符号 (主单号12位后补 '-'), 长度校验关
    assert cfg["label_match"] == "insert_char", cfg["label_match"]
    assert cfg["hyphen_pos"] == 12, cfg["hyphen_pos"]
    assert cfg["label_len"] == 0, cfg["label_len"]
    # v3.30.1 复合条码取段: 正式产线四段拼接箱标签按前缀 JOB 取工单段
    assert cfg["composite_label_enabled"] is True, cfg
    assert cfg["composite_pick_mode"] == "prefix", cfg["composite_pick_mode"]
    assert cfg["composite_prefix"] == "JOB", cfg["composite_prefix"]
    assert cfg["order_code_pattern"] == "^JOB", cfg.get("order_code_pattern")
    # v3.43 放工单=工单收尾: 预设开 (现场放工单常晚于周期结束), 全局默认关
    assert cfg["tail_paper_order_required"] is True, cfg.get("tail_paper_order_required")
    assert cfg["tail_paper_as_close_action"] is True, cfg.get("tail_paper_as_close_action")
    # v3.43 缺工单判定方式二选一: 预设 = 默认扫新单判定 (时限 0 = 不用时限模式)
    assert cfg["tail_paper_scan_alarm"] is True, cfg.get("tail_paper_scan_alarm")
    assert cfg["tail_paper_timeout_s"] == 0, cfg.get("tail_paper_timeout_s")
    assert cfg["block_completed_order_rescan"] is True, cfg.get("block_completed_order_rescan")
    assert cfg["event_completed_order_rescan"] == 3, cfg.get("event_completed_order_rescan")
    # v3.42.1 预设补齐: 自动切项目(同名兜底) + 异常→事件映射 + 每箱96固定值
    assert cfg["auto_switch_project"] is True, cfg.get("auto_switch_project")
    assert cfg["match_project_by_name"] is True, cfg.get("match_project_by_name")
    assert cfg["items_per_box_source"] == "config", cfg.get("items_per_box_source")
    assert cfg["items_per_box_fixed"] == 96, cfg.get("items_per_box_fixed")
    assert cfg["event_missing_paper"] == 2, cfg.get("event_missing_paper")
    assert cfg["event_missing_nozzle"] == 3, cfg.get("event_missing_nozzle")
    assert cfg["event_mes_fail"] == 3, cfg.get("event_mes_fail")
    assert cfg["on_short_box"] == "redo", cfg["on_short_box"]
    assert cfg["on_mes_fail"] == "block", cfg["on_mes_fail"]
    assert cfg["enabled"] is False  # 新建默认不启用 → 零影响

    # 列表里能看到这一行
    assert page.get_by_text(name, exact=True).count() >= 1

    _cleanup_pkg(api_url)
