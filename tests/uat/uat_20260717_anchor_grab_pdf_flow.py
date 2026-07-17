# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 严格照部署指南 v1.2 的操作顺序全程真点击走标定流程。

与 uat_20260717_anchor_grab_fallback.py 的区别:
  - **页面切换只走侧边栏菜单点击**, 浏览器只在最开始 goto 一次应用首页;
  - **启用项目 / 开始检测 / 待机 / 停止全部点真按钮**, 不再用 API 代劳
    (API 仅用于: 布置"摄像头画面"= 起视频文件, 以及事后断言/收尾清理)。

操作剧本 (= 部署指南 4.2 节第 3 条「现场标准操作顺序」+ 第七节):
  项目管理页点「电机装配全流程」卡片 → 点「启用当前项目」
  → 菜单切检测中心 → 点「开始」(模型进显存) → 工件摆稳(视频钉住)
  → 点「待机」→ 菜单切项目管理 → 打开打螺丝拆分编辑器
  → 点「刷新画面快照」→ 点「从当前画面抓取锚点框」→ 绿色"已标定"     (路A)
  → 菜单切检测中心 → 点「开始」(待机恢复) → 点「停止」(画面定格)
  → 菜单切项目管理 → 同一编辑器再抓一次 → 仍成功                    (路B)

前置: main 后端 8001 (tianjun env) + 前端 6001, 模型「电机装配打螺丝」在库,
      「电机装配全流程」项目已配好拆分规则且绑定默认模型。
不保存任何配置 (编辑器只开不存), 收尾停视频/检测并恢复原激活项目。
"""
from __future__ import annotations

import sys
import time

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"
CH = 0
PNAME = "电机装配全流程"
VIDEO = ("/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/video/"
         "2026-07/1593386d5368d7afda8e0b4f47b15587.mp4")

run = UatRun("anchor_grab_pdf_flow")
prev_active = None


def _results():
    for attempt in range(6):
        try:
            return requests.get(f"{API}/source/detection/results?channel={CH}",
                                timeout=20).json()
        except requests.RequestException:
            if attempt == 5:
                raise
            time.sleep(2)


def _cleanup():
    try:
        requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
        requests.post(f"{API}/source/video/stop?channel={CH}", timeout=10)
    except Exception:
        pass
    if prev_active:
        try:
            requests.post(f"{API}/projects/{prev_active}/activate", timeout=10)
        except Exception:
            pass


def main():
    global prev_active
    # ============ 0. 健康检查 + 记录原激活项目(收尾恢复用) ============
    ok_be = requests.get(f"{API}/projects", timeout=5).status_code == 200
    ok_fe = requests.get(FRONT, timeout=5).status_code == 200
    run.step("00 前后端健康(8001/6001)", ok_be and ok_fe)

    plist = requests.get(f"{API}/projects?limit=200",
                         timeout=10).json().get("items") or []
    motor = next((p for p in plist if p.get("name") == PNAME), None)
    prev_active = next((p["id"] for p in plist if p.get("is_active")), None)
    run.step("01 「电机装配全流程」项目存在且绑了默认模型",
             motor is not None and motor.get("default_model_id"),
             f"id={motor and motor['id']} model_id={motor and motor.get('default_model_id')}")
    if motor is None:
        return
    if prev_active == motor["id"]:
        prev_active = None  # 本来就激活, 收尾不用恢复

    # ============ 1. 布置"摄像头画面": 视频文件慢速钉在工件在位帧 ============
    requests.post(f"{API}/source/detection/stop?channel={CH}", timeout=10)
    requests.post(f"{API}/source/video/start?channel={CH}",
                  json={"file_path": VIDEO, "speed": 0.05},
                  timeout=15).raise_for_status()
    requests.post(f"{API}/source/video/progress?channel={CH}",
                  json={"progress": 115.0 / 650.0}, timeout=10)
    run.step("02 视频源就绪(115s 工件在位, 0.05x 慢速≈钉住)", True)

    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(
            p, record_video_dir=run.video_dir)

        def open_sidebar():
            if not page.locator("nav >> text=项目管理").first.is_visible():
                page.locator("button[title='导航菜单']").first.click()
                time.sleep(0.8)

        def nav_to(menu_text: str, landmark_selector: str, timeout_ms=12000):
            """全程唯一的切页方式: 点侧边栏菜单, 等落地标志出现。"""
            open_sidebar()
            page.locator(f"nav >> text={menu_text}").first.click()
            page.wait_for_selector(landmark_selector, timeout=timeout_ms)
            time.sleep(0.8)

        # ============ 2. 唯一一次 goto: 应用首页, 之后全靠点 ============
        page.goto(FRONT, wait_until="domcontentloaded")
        time.sleep(3.0)

        # ============ 3. 项目管理页: 点卡片 → 点「启用当前项目」 ============
        nav_to("项目管理", "input[placeholder*='搜索项目']")
        page.locator("input[placeholder*='搜索项目']").fill(PNAME)
        time.sleep(0.6)
        page.locator(f"div.p-4:has-text('{PNAME}')").first.click()
        time.sleep(1.0)
        page.locator("button:has-text('启用当前项目')").first.click()
        time.sleep(2.0)
        act = requests.get(f"{API}/projects/{motor['id']}", timeout=10).json()
        run.step("03 点「启用当前项目」→ 后端确认已激活",
                 bool(act.get("is_active")), f"is_active={act.get('is_active')}")
        run.shot(page, "03_项目已启用")

        # ============ 4. 菜单切检测中心 → 点「开始」 ============
        nav_to("检测中心", "button:has-text('开始')")
        page.locator("button:has-text('开始')").first.click()
        # 完整启动 = 模型加载进显存, GPU 冷启动可到 60s
        detecting = False
        t_end = time.time() + 120
        while time.time() < t_end:
            st = _results()
            if st.get("is_detecting") and (st.get("fps_inference") or 0) > 0:
                detecting = True
                break
            time.sleep(1.0)
        run.step("04 点「开始」→ 检测真跑起来(模型进显存)", detecting,
                 f"fps={_results().get('fps_inference')}")
        time.sleep(3.0)  # 工件摆稳(画面本来就钉着), 象征性停留
        run.shot(page, "04_检测中")

        # ============ 5. 路A: 点「待机」→ 菜单切项目页 → 抓取 ============
        page.locator("button:has-text('待机')").first.click()
        time.sleep(2.0)
        st = _results()
        run.step("05 点「待机」→ 检测停/实时结果清空(缺陷前提)",
                 (not st.get("is_detecting")) and not (st.get("detections") or []),
                 f"is_detecting={st.get('is_detecting')} 残留={len(st.get('detections') or [])}")

        nav_to("项目管理", "input[placeholder*='搜索项目']")
        page.locator("input[placeholder*='搜索项目']").fill(PNAME)
        time.sleep(0.6)
        page.locator(f"div.p-4:has-text('{PNAME}')").first.click()
        time.sleep(1.2)
        page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
        time.sleep(1.2)

        def open_split_editor():
            row = page.locator("tr", has=page.locator("text=锚点跟随")).first
            row.locator("button:has-text('编辑')").first.click()
            time.sleep(1.5)
            d = page.locator(".el-dialog:visible").first
            d.locator("button:has-text('刷新画面快照')").first.click()
            time.sleep(1.2)
            return d

        def grab_and_assert(dlg):
            """硬判据: 抓取请求真走 infer-once 且返回含「工件」+ 成功 toast。"""
            with page.expect_response(
                    lambda r: "/detection/infer-once" in r.url,
                    timeout=15000) as resp_info:
                dlg.locator(
                    "button:has-text('从当前画面抓取锚点框')").first.click()
            resp = resp_info.value
            body = resp.json() if resp.status == 200 else {}
            labels = [d.get("label") for d in body.get("detections") or []]
            toast_seen = False
            t2 = time.time() + 6
            while time.time() < t2:
                if page.locator(
                        ".el-message--success:has-text('已标定锚点框')").count():
                    toast_seen = True
                    break
                time.sleep(0.2)
            return (resp.status == 200 and body.get("mode") == "one_shot"
                    and "工件" in labels and toast_seen,
                    f"status={resp.status} mode={body.get('mode')} "
                    f"labels={labels[:6]} toast={toast_seen}")

        dlg = open_split_editor()
        run.step("06 打开打螺丝拆分编辑器+刷新快照", dlg.is_visible())
        ok_a, detail_a = grab_and_assert(dlg)
        run.step("07 路A(点待机后): 抓取成功", ok_a, detail_a)
        run.shot(page, "07_待机态抓取成功")
        dlg.locator("button:has-text('取消')").first.click()
        time.sleep(0.8)

        # ============ 6. 路B: 回检测中心点「开始」→ 点「停止」→ 再抓 ============
        nav_to("检测中心", "button:has-text('开始')")
        page.locator("button:has-text('开始')").first.click()
        detecting = False
        t_end = time.time() + 60
        while time.time() < t_end:
            if _results().get("is_detecting"):
                detecting = True
                break
            time.sleep(1.0)
        run.step("08 点「开始」从待机恢复检测", detecting)
        time.sleep(4.0)  # 现场语义: 跑了一会儿再停
        page.locator("button:has-text('停止')").first.click()
        time.sleep(2.5)
        st = _results()
        run.step("09 点「停止」→ 画面定格/结果清空",
                 (not st.get("is_detecting")) and not (st.get("detections") or []),
                 f"is_running={st.get('is_running')}")
        run.shot(page, "09_已停止定格")

        nav_to("项目管理", "input[placeholder*='搜索项目']")
        page.locator("input[placeholder*='搜索项目']").fill(PNAME)
        time.sleep(0.6)
        page.locator(f"div.p-4:has-text('{PNAME}')").first.click()
        time.sleep(1.2)
        page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
        time.sleep(1.2)
        dlg = open_split_editor()
        ok_b, detail_b = grab_and_assert(dlg)
        run.step("10 路B(点停止后): 抓取仍成功", ok_b, detail_b)
        run.shot(page, "10_停止态抓取成功")
        dlg.locator("button:has-text('取消')").first.click()
        time.sleep(0.8)

        real_errs = filter_console_errors(console_errs)
        run.step("11 控制台无前端逻辑报错", not real_errs,
                 f"真报错={real_errs[:3]}")
        ctx.close()
        browser.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
    raise SystemExit(run.finish())
