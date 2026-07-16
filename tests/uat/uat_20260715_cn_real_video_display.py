# -*- coding: utf-8 -*-
"""川南显示定制 · 真实视频 + 真实模型 + 仿真中控 全链路可见浏览器 UAT (v3.39)。

与 synthetic 版 (uat_20260715_task_info_layout.py) 的区别: 这次全部来真的 ——
  - 视频源:   oppo 装电池真实产线录像 (.avi, 视频文件源)
  - 推理:     真 YOLO 模型 best.pt (CUDA), 不是 synthetic 剧本
  - 开工报文: 由「川南中控模拟器」(mock_chuannan_mcs.py, 9100) 主动 POST 给天军入站,
              走客户现场同款链路: 中控 → /mes/inbound/task → 建单 → 自动开始检测
  - 显示定制: 默认原界面 → UI 点开关 (关工单徽标 + 开两行表格 + 隐藏扫码按钮) → 复核

跑法(前置: 后端 8013 RUNTIME_MODE=test + 前端 6003 + 仿真中控 9100 已起):
  cd tests/uat && python uat_20260715_cn_real_video_display.py
"""
import os
import time

import requests
from playwright.sync_api import sync_playwright

from _common import UatRun, launch_browser, filter_console_errors

API = os.environ.get("UAT_API", "http://127.0.0.1:8013")
FRONT = os.environ.get("UAT_FRONT", "http://127.0.0.1:6003")
MCS = os.environ.get("UAT_MCS", "http://127.0.0.1:9100")

VIDEO = "/home/qianqian/1.py/output/oppo/装电池/video/Video_20260331083930218.avi"
MODEL = "/home/qianqian/1.py/output/oppo/装电池/model/best.pt"
LABELS = ["撕璃形纸", "翻电池", "安装电池", "翻手机"]
TASK_NO = f"CNRV-{time.strftime('%H%M%S')}"
PROJECT_NAME = "川南真机-装电池"

run = UatRun("cn_real_video_display")


def body_text(page):
    return page.evaluate("document.body.innerText")


def setup_real_project() -> int:
    """真模型上传 → 真项目(默认模型) → 激活。"""
    r = requests.get(f"{API}/api/v1/projects", timeout=10)
    run.step("后端就绪", r.status_code == 200, f"HTTP {r.status_code}")
    run.step("前端就绪", requests.get(FRONT, timeout=10).status_code == 200)
    run.step("仿真中控就绪",
             requests.get(f"{MCS}/health", timeout=10).json().get("code") == 0)

    with open(MODEL, "rb") as f:
        mr = requests.post(f"{API}/api/v1/models/upload",
                           files={"file": ("best.pt", f)},
                           data={"name": "cn-real-best", "model_type": "detection"},
                           timeout=120)
    run.step("上传真实模型", mr.status_code == 201, f"HTTP {mr.status_code} {mr.text[:80]}")
    model_id = mr.json()["id"]

    steps = [{"label": lb, "threshold": 25, "min_frames": 1, "enabled": True}
             for lb in LABELS]
    pr = requests.post(f"{API}/api/v1/projects", json={
        "name": PROJECT_NAME, "task_type": "detection", "logic_mode": "detection",
        "steps_config": steps, "default_model_id": model_id}, timeout=15)
    run.step("创建真实项目(挂默认模型)", pr.status_code == 201,
             f"HTTP {pr.status_code} {pr.text[:80]}")
    pid = pr.json()["id"]
    ar = requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=60)
    run.step("激活项目(真模型加载)", ar.status_code == 200, f"HTTP {ar.status_code}")
    return pid


def setup_inbound_customer_style():
    """按客户现场配置: 开工建单 + 自动开始检测 + 四要素全开; 显示层默认原样。"""
    cfg = requests.get(f"{API}/api/v1/mes/inbound/config", timeout=10).json()
    cfg.update({"enabled": True, "create_work_order_on_task": True,
                "switch_project_on_task": False, "start_detection_on_task": True})
    cfg["task_info_display"] = {
        "show_task_no": True, "show_product_code": True,
        "show_step_code": True, "show_operator": True,
        "show_order_chip": True, "two_line_layout": False}
    r = requests.put(f"{API}/api/v1/mes/inbound/config", json=cfg, timeout=10)
    run.step("入站配置=客户现场同款(建单+自动开始检测+四要素)",
             r.status_code == 200, f"HTTP {r.status_code}")


def start_real_video_only():
    """只开视频源不开检测 —— 检测由中控开工报文自动拉起 (客户期望的时序)。"""
    r = requests.post(f"{API}/api/v1/source/video/start?channel=0",
                      json={"file_path": VIDEO, "speed": 2.0, "loop": True},
                      timeout=30)
    run.step("启动真实视频源(不开检测)", r.status_code == 200, f"HTTP {r.status_code}")
    time.sleep(2)
    d = requests.get(f"{API}/api/v1/source/detection/results?channel=0",
                     timeout=5).json()
    run.step("开工前: 视频在跑但未检测",
             d.get("is_running") and not d.get("is_detecting"),
             f"running={d.get('is_running')} detecting={d.get('is_detecting')}")


def mcs_post_task_start():
    """仿真中控主动推开工 → 天军应 建单 + 自动开始检测(真模型真视频)。"""
    r = requests.post(f"{MCS}/drive/start", json={
        "task_no": TASK_NO, "product_code": "OPPO-BATT",
        "step_code": "5.1", "operator": "川南操作员"}, timeout=30)
    resp = (r.json() or {}).get("resp") or {}
    run.step("中控→天军 开工报文 code=0", resp.get("code") == 0,
             f"resp={resp.get('code')} msg={str(resp.get('message'))[:60]}")

    # 真模型加载 + 检测拉起需要几秒 (CUDA)
    deadline, detecting = time.time() + 30, False
    while time.time() < deadline:
        d = requests.get(f"{API}/api/v1/source/detection/results?channel=0",
                         timeout=5).json()
        if d.get("is_detecting"):
            detecting = True
            break
        time.sleep(1)
    run.step("开工后自动开始检测(真YOLO拉起)", detecting)

    mes = (d.get("mes") or {})
    run.step("工位已绑定中控工单", (mes.get("order") or {}).get("order_no") == TASK_NO,
             f"order={(mes.get('order') or {}).get('order_no')}")

    # 真推理出框 (检测数 > 0)
    deadline, seen = time.time() + 30, 0
    while time.time() < deadline:
        d = requests.get(f"{API}/api/v1/source/detection/results?channel=0",
                         timeout=5).json()
        seen = len(d.get("detections") or [])
        if seen > 0:
            break
        time.sleep(1)
    run.step("真实推理出目标框", seen > 0, f"当前帧检测数={seen}")


def visible_verify():
    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(p, record_video_dir=run.video_dir)

        # ---- A. 默认态: 徽标+单行要素+扫码按钮, 真画面在跑 ----
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(6)
        txt = body_text(page)
        run.step("默认: 工单徽标在屏", "工单:" in txt)
        run.step("默认: 四要素单行在屏", "任务号:" in txt and TASK_NO in txt)
        run.step("默认: 无两行表格", page.locator(".task-info-table").count() == 0)
        run.step("默认: 扫码操作按钮在屏", "禁用扫码" in txt)
        run.step("真画面: 检测中状态", "检测中" in txt)
        run.shot(page, "01_真视频默认原界面")

        # ---- B. UI 点开关: 关徽标 + 开两行 ----
        page.goto(f"{FRONT}/#/mes", wait_until="domcontentloaded")
        time.sleep(3)
        page.get_by_text("工单接收", exact=False).first.click()
        time.sleep(2)
        for label in ("工单进度徽标", "两行表格布局"):
            page.locator(".el-form-item", has=page.locator(
                f".el-form-item__label:text-is('{label}')")).first \
                .locator(".el-switch").first.click()
            time.sleep(0.3)
        page.get_by_role("button", name="保存配置").click()
        time.sleep(1.5)
        ti = (requests.get(f"{API}/api/v1/mes/inbound/config", timeout=10).json()
              .get("task_info_display") or {})
        run.step("UI 保存后配置落库",
                 ti.get("show_order_chip") is False and ti.get("two_line_layout") is True,
                 str({k: ti.get(k) for k in ("show_order_chip", "two_line_layout")}))

        # ---- C. 监控页复核: 徽标消失 + 两行表格 + 真检测未中断 ----
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(6)
        txt = body_text(page)
        run.step("徽标已隐藏", "工单:" not in txt)
        tbl = page.locator(".task-info-table")
        run.step("两行表格已渲染", tbl.count() > 0)
        heads = tbl.locator("th").all_inner_texts() if tbl.count() else []
        run.step("表头 = 四要素", heads == ["任务号", "产品代号", "工序工步", "操作员"],
                 str(heads))
        cells = tbl.locator("td").all_inner_texts() if tbl.count() else []
        run.step("信息行 = 中控开工值",
                 cells == [TASK_NO, "OPPO-BATT", "5.1", "川南操作员"], str(cells))
        run.step("切显示配置不打断真检测", "检测中" in txt)
        run.shot(page, "02_真视频两行表格")

        # ---- D. 系统设置隐藏扫码按钮 ----
        page.goto(f"{FRONT}/#/settings", wait_until="domcontentloaded")
        time.sleep(3)
        row = page.locator("div.flex.items-center.justify-between",
                           has=page.get_by_text("扫码操作按钮", exact=True)).first
        row.locator(".el-switch").click()
        time.sleep(1)
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(5)
        txt = body_text(page)
        run.step("扫码按钮已隐藏", "禁用扫码" not in txt and "清除本次扫码" not in txt)
        run.step("四要素仍在屏", TASK_NO in txt)
        run.shot(page, "03_真视频扫码按钮隐藏")

        errs = filter_console_errors(console_errs)
        run.step("浏览器 console 无异常报错", len(errs) == 0,
                 "; ".join(str(e) for e in errs[:3]))
        ctx.close()
        browser.close()


def verify_mcs_log():
    """仿真中控侧流水: 确认它真发出了开工且拿到 code=0 (双向链路真实闭环)。"""
    log = requests.get(f"{MCS}/log", timeout=10).json()
    items = log if isinstance(log, list) else log.get("items") or log.get("log") or []
    sent = [x for x in items if x.get("kind", "").startswith("开工")]
    ok = any((x.get("detail") or {}).get("resp", {}).get("code") == 0 for x in sent)
    run.step("中控流水: 开工报文已发出且天军回 code=0", ok, f"共 {len(sent)} 条开工记录")


def main():
    setup_real_project()
    setup_inbound_customer_style()
    start_real_video_only()
    mcs_post_task_start()
    visible_verify()
    verify_mcs_log()
    # 收尾
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=30)
    requests.post(f"{API}/api/v1/source/video/stop?channel=0", timeout=15)
    raise SystemExit(run.finish())


if __name__ == "__main__":
    main()
