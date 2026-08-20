"""配置面板 E2E — 包装结算 (上银包装线, v3.21 M6).

铁律「带 UI 的功能改动必须走 E2E 点击验证」: 验证 PackagingFlowPanel 的
新建 → 套用物品计数预设 → 保存 整条链路真把字段传到后端并落库
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

    # 包装结算已从系统设置迁到 MES 管理页（自绘按钮 tab）
    page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_load_state("networkidle")

    # 切到「包装结算」Tab
    page.get_by_role("button", name="包装结算").click()
    page.wait_for_timeout(800)

    # 新建配置 → 对话框
    page.get_by_role("button", name="新建配置").click()
    page.wait_for_selector(".el-dialog", state="visible", timeout=5000)

    # 填配置名称
    page.locator('input[placeholder="包装线-1"]').fill(name)

    # 套用物品计数预设
    page.get_by_role("button", name=re.compile("套用预设")).click()
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
    # v3.45 组⑧ 预设: 每箱扫标签放行 + 从标签取本箱数量 (第3段) + 收尾对账
    assert cfg["box_label_scan_required"] is True, cfg.get("box_label_scan_required")
    assert cfg["label_qty_enabled"] is True, cfg.get("label_qty_enabled")
    assert cfg["label_qty_segment"] == 3, cfg.get("label_qty_segment")
    assert cfg["unauthorized_cycle_action"] == "hold", cfg.get("unauthorized_cycle_action")
    assert cfg["label_total_check"] is True, cfg.get("label_total_check")
    assert cfg["event_box_not_scanned"] == 3, cfg.get("event_box_not_scanned")
    assert cfg["enabled"] is False  # 新建默认不启用 → 零影响

    # 列表里能看到这一行
    assert page.get_by_text(name, exact=True).count() >= 1

    _cleanup_pkg(api_url)


def test_packaging_panel_group8_manual_toggle_roundtrip(page, base_url, api_url):
    """组⑧「箱标签扫码」手动逐项配置 → 保存 → 后端契约核对 (v3.45).

    覆盖: 总开关联动子项显隐 / 取量段号+正则 / 重扫 update 档 /
    未扫做完整箱 book 档 / 收尾对账开关 — 全部真点击真落库.
    """
    _cleanup_pkg(api_url)
    name = f"__e2e_pkg_{uuid.uuid4().hex[:6]}"

    page.goto(f"{base_url}/#/mes", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="包装结算").click()
    page.wait_for_timeout(800)
    page.get_by_role("button", name="新建配置").click()
    page.wait_for_selector(".el-dialog", state="visible", timeout=5000)
    dlg = page.locator(".el-dialog").last
    page.locator('input[placeholder="包装线-1"]').fill(name)

    # 切滑块口径 (组⑧ 仅 sliders 显示)
    dlg.get_by_text("按物品总数").first.click()
    page.wait_for_timeout(400)
    # 展开组⑧
    dlg.get_by_text("⑧ 箱标签扫码").click()
    page.wait_for_timeout(400)

    # 开总开关前子项不可见; 开后显现 (联动显隐)
    _qty_item = dlg.locator(".el-form-item", has_text="从标签取本箱数量")
    assert _qty_item.count() == 0
    dlg.locator(".el-form-item", has_text="每箱必须扫箱标签").first \
       .locator(".el-switch").click()
    page.wait_for_timeout(300)
    assert _qty_item.count() >= 1

    dlg.locator(".el-form-item", has_text="从标签取本箱数量").first \
       .locator(".el-switch").click()
    page.wait_for_timeout(300)
    dlg.locator(".el-form-item", has_text="数量在第几段").first \
       .locator("input").first.fill("4")
    dlg.locator(".el-form-item", has_text="数量段识别正则").first \
       .locator("input").first.fill(r"\d+\.\d+")
    dlg.locator(".el-form-item", has_text="已放行后重扫标签").first \
       .get_by_text("更新本箱目标").click()
    dlg.locator(".el-form-item", has_text="未扫标签做完整箱").first \
       .get_by_text("报警后照常落账").click()
    dlg.locator(".el-form-item", has_text="收尾数量对账").first \
       .locator(".el-switch").click()
    page.wait_for_timeout(300)

    page.locator(".el-dialog__footer").get_by_role("button", name="保存").first.click()
    page.wait_for_timeout(1200)

    r = requests.get(f"{api_url}/api/v1/packaging-flows", timeout=10)
    found = [c for c in (r.json() or {}).get("items", []) if c.get("name") == name]
    assert found, f"配置 {name} 未落库"
    cfg = found[0]
    assert cfg["box_label_scan_required"] is True, cfg
    assert cfg["label_qty_enabled"] is True, cfg
    assert cfg["label_qty_segment"] == 4, cfg.get("label_qty_segment")
    assert cfg["label_qty_pattern"] == r"\d+\.\d+", cfg.get("label_qty_pattern")
    assert cfg["label_rescan_action"] == "update", cfg.get("label_rescan_action")
    assert cfg["unauthorized_cycle_action"] == "book", cfg.get("unauthorized_cycle_action")
    assert cfg["label_total_check"] is True, cfg.get("label_total_check")

    _cleanup_pkg(api_url)
