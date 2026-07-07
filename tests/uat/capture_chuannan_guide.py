"""川南火工对接·配置操作手册 — 从零截图捕获 (面向空白软件客户的手把手教程)。

隔离空白栈: 后端 8013 (TIANJUN_DATA_DIR=/tmp/chuannan_guide_data, 已删空项目/模型),
            前端 6003 (VITE_API_BASE_URL=http://127.0.0.1:8013/api/v1)。

产物截图存 tests/uat/evidence_chuannan_guide/ (供 gen_chuannan_config_guide.py 嵌入)。
跑法: python tests/uat/capture_chuannan_guide.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("TIANJUN_DATA_DIR", "/tmp/chuannan_guide_data")

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8013/api/v1"
FE = "http://127.0.0.1:6003"
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence_chuannan_guide")
os.makedirs(SHOTS, exist_ok=True)

PRODUCT = "JS-2024-01"        # 演示产品代号 (项目名 = 产品代号, 才能自动切)
ALARM = {
    "task_no": "TASK-2024-0608",
    "product_code": PRODUCT,
    "step_code": "工序2-工步3",
    "operator": "张工",
    "warning_text": "螺丝漏打 (第3工位)",
}

log = []


def shot(page, name, desc=""):
    path = os.path.join(SHOTS, name)
    try:
        page.screenshot(path=path, full_page=False, animations="disabled", timeout=15000)
        log.append((name, True, desc))
        print(f"[OK] {name}  {desc}", flush=True)
    except Exception as e:
        log.append((name, False, str(e)[:80]))
        print(f"[!!] {name}  {str(e)[:80]}", flush=True)


def put_inbound_config():
    """配好川南入站 (含开工四要素全开), 让配置页截图呈现已填状态。"""
    cfg = {
        "enabled": True,
        "field_map": {
            "task_no": "TaskNo", "product_code": "ProductCode",
            "step_code": "StepCode", "operator": "Operator", "begin_time": "BeginTime",
        },
        "required_fields": ["task_no"],
        "switch_project_on_task": True,
        "match_project_by_name": True,
        "create_work_order_on_task": True,
        "reject_duplicate_task": True,
        "alarm_event_name": ["cycle_end"],
        "alarm_record_on_results": ["NG"],
        "alarm_dedup_sec": 5,
        "alarm_clear_match_fields": ["task_no", "product_code", "step_code", "operator"],
        "alarm_banner": {
            "enabled": True, "position": "top", "color": "#dc2626",
            "poll_interval_sec": 3, "show_task_no": True, "show_product_code": True,
            "show_step_code": True, "show_operator": True, "show_time": True,
        },
        "task_info_display": {
            "show_task_no": True, "show_product_code": True,
            "show_step_code": True, "show_operator": True,
        },
    }
    r = requests.put(f"{API}/mes/inbound/config", json=cfg, timeout=10)
    print("put inbound config:", r.status_code, flush=True)


def seed_alarm():
    import backend.models.models  # noqa: F401
    import backend.models.mes_models  # noqa: F401
    import backend.models.auth_models  # noqa: F401
    import backend.models.export_models  # noqa: F401
    import backend.models.plugin_models  # noqa: F401
    from backend.db.database import SessionLocal
    from backend.services.external_alarm import record_active_alarm
    db = SessionLocal()
    try:
        record_active_alarm(db, ALARM, event_type="cycle_end", channel_id=0, dedup_sec=0)
        db.commit()
    finally:
        db.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})

        # ========== 一、初始配置: 建项目命名为产品代号 ==========
        page.goto(f"{FE}/#/project", wait_until="domcontentloaded")
        time.sleep(3)
        shot(page, "00_blank_projects.png", "空白软件: 项目列表为空")

        # 新建项目对话框 → 命名为产品代号
        try:
            page.get_by_role("button", name="新建项目").first.click()
            time.sleep(1)
            dlg = page.locator(".el-dialog:has-text('新建项目')").first
            dlg.locator("input").first.fill(PRODUCT)
            time.sleep(0.5)
            shot(page, "01_new_project_dialog.png", "新建项目: 项目名称填成产品代号")
            page.get_by_role("button", name="创 建").first.click()
            time.sleep(2)
        except Exception as e:
            print("create project UI err:", str(e)[:120], flush=True)
            # 兜底: API 建项目
            try:
                requests.post(f"{API}/projects", json={"name": PRODUCT, "task_type": "detection",
                              "logic_mode": "sequential"}, timeout=10)
            except Exception:
                pass
        page.goto(f"{FE}/#/project", wait_until="domcontentloaded")
        time.sleep(2.5)
        shot(page, "02_project_created.png", "项目建好: 列表出现以产品代号命名的项目")

        # 配置入站 (建项目后, 让后续配置页呈现已填状态)
        put_inbound_config()

        # ========== 二、入站配置 (工单接收) ==========
        page.goto(f"{FE}/#/mes", wait_until="domcontentloaded")
        time.sleep(3)
        try:
            page.wait_for_selector("button:has-text('工单接收')", timeout=15000)
            page.locator("button:has-text('工单接收')").first.click()
            time.sleep(2)
            shot(page, "10_inbound_top.png", "工单接收: 接收地址 + 字段映射 + 响应约定")
            # 逐区滚动截图
            for anchor, fn in [
                ("按产品码切项目", "11_inbound_switch_project.png"),
                ("报警闭环", "12_inbound_alarm_closure.png"),
                ("监控页报警横幅", "13_inbound_banner.png"),
                ("持续显示要素", "14_inbound_taskinfo.png"),
            ]:
                try:
                    page.get_by_text(anchor, exact=False).first.scroll_into_view_if_needed()
                    time.sleep(0.7)
                    shot(page, fn, f"工单接收: {anchor}")
                except Exception as e:
                    print(f"anchor {anchor} err:", str(e)[:80], flush=True)
        except Exception as e:
            print("inbound page err:", str(e)[:120], flush=True)

        # ========== 三、出站配置 (外部对接) ==========
        page.goto(f"{FE}/#/mes", wait_until="domcontentloaded")
        time.sleep(3)
        try:
            page.wait_for_selector("button:has-text('外部对接')", timeout=15000)
            page.locator("button:has-text('外部对接')").first.click()
            time.sleep(1.5)
            shot(page, "20_gateway_list.png", "外部对接: MES 连接列表")
            page.locator("button:has-text('新建连接')").first.click()
            time.sleep(1.2)
            shot(page, "21_gateway_dialog.png", "新建连接: 地址 / 协议 / 推送时机")
            # 套川南预设
            try:
                page.locator(".el-select").filter(has_text="选择预设模板").first.click()
                time.sleep(0.6)
                page.locator(".el-select-dropdown__item", has_text="川南报警上报").first.click()
                time.sleep(1.0)
                shot(page, "23_gateway_preset.png", "套用川南报警预设: 字段映射模板一键填好")
            except Exception as e:
                print("preset err:", str(e)[:80], flush=True)
            # 附带截图开关 (滚到截图相关)
            try:
                page.get_by_text("附带截图", exact=False).first.scroll_into_view_if_needed()
                time.sleep(0.6)
                shot(page, "22_gateway_snapshot.png", "附带现场截图 + 截图大小上限 (纯 Base64)")
            except Exception as e:
                print("snapshot opt err:", str(e)[:80], flush=True)
        except Exception as e:
            print("gateway page err:", str(e)[:120], flush=True)

        # ========== 四、监控页效果 (报警横幅) ==========
        # 先开工 (建工单, 供四要素), 再种在途报警 (供横幅)
        body = {"TaskNo": ALARM["task_no"], "ProductCode": ALARM["product_code"],
                "StepCode": ALARM["step_code"], "Operator": ALARM["operator"],
                "BeginTime": "2026-06-08T08:30:00"}
        try:
            requests.post(f"{API}/mes/inbound/task", json=body, timeout=10)
        except Exception:
            pass
        requests.post(f"{API}/mes/inbound/alarm/clear", json=body, timeout=10)
        seed_alarm()

        page.goto(f"{FE}/#/monitor", wait_until="domcontentloaded")
        time.sleep(5)  # 给横幅两个轮询周期拉在途报警
        banner_on = page.locator(".ext-alarm-banner").count() > 0
        shot(page, "30_monitor_banner_on.png", f"监控页在途报警持续横幅 (banner_dom={banner_on})")

        # 外部消除 → 横幅撤
        requests.post(f"{API}/mes/inbound/alarm/clear", json=body, timeout=10)
        time.sleep(5)
        shot(page, "31_monitor_banner_off.png", "中控消除后横幅自动撤")

        # ========== 五、显示设置 (全屏/kiosk, 供装机节) ==========
        try:
            page.goto(f"{FE}/#/settings", wait_until="domcontentloaded")
            time.sleep(2.5)
            page.get_by_text("窗口模式", exact=False).first.scroll_into_view_if_needed()
            time.sleep(0.6)
            shot(page, "40_settings_fullscreen.png", "显示设置: 主窗口全屏 (kiosk 风格) 开关")
        except Exception as e:
            print("settings err:", str(e)[:80], flush=True)

        browser.close()

    ok = sum(1 for _, s, _ in log if s)
    print(f"\n截图完成: {ok}/{len(log)} 成功, 存于 {SHOTS}", flush=True)


if __name__ == "__main__":
    main()
