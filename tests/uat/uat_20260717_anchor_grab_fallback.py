# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 抓取锚点框单帧推理兜底 (v3.42.0 现场缺陷修复).

现场叙事:
  客户现场部署工程师配「打螺丝」拆分规则时点「从当前画面抓取锚点框」, 顶部
  弹红色提示"当前画面没有检测到工件"。根因: 检测中侧边栏锁菜单切不进项目页,
  停止/待机后实时检测结果又被清空 —— 打包版里标定流程是死环。
  修复: 抓取时若无实时结果, 后端对当前显示帧现推一帧 (infer-once)。

本 UAT 验证两条现场通路 (真前端 + 真模型 + 客户真实视频):
  路 A(待机): 监控页启动检测 → 工件出框 → 待机 → 菜单切项目页(不再被拦)
             → 打开打螺丝拆分编辑器 → 抓取 → 绿色"已标定"
  路 B(停止): 恢复检测 → 停止(画面定格) → 同一编辑器再抓 → 仍成功
  以及零回归面: 检测运行中侧边栏仍然锁菜单(老约束不动)。

前置: main 后端 8001 (tianjun env, --reload 已加载新端点) + 前端 6001,
      模型「电机装配打螺丝」已在模型仓库, GPU 可用。
不保存任何项目配置 (编辑器只开不存), 收尾停视频/检测。
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

run = UatRun("anchor_grab_fallback")
prev_active = None


def _results():
    for attempt in range(5):
        try:
            return requests.get(f"{API}/source/detection/results?channel={CH}",
                                timeout=15).json()
        except requests.RequestException:
            if attempt == 4:
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
    # ============ 0. 健康检查 ============
    ok_be = requests.get(f"{API}/projects", timeout=5).status_code == 200
    ok_fe = requests.get(FRONT, timeout=5).status_code == 200
    run.step("00 前后端健康(8001/6001)", ok_be and ok_fe)

    mlist = requests.get(f"{API}/models", timeout=10).json().get("items") or []
    model = next((m for m in mlist if m.get("name") == "电机装配打螺丝"), None)
    run.step("01 模型仓库有「电机装配打螺丝」", model is not None)
    if model is None:
        return

    # 激活电机项目再开浏览器 —— 监控页挂载会把「激活项目」的配置+模型重新
    # 下发到通道; 若激活的是别的项目, 本 UAT 指定的电机模型会被顶掉
    global prev_active
    plist = requests.get(f"{API}/projects?limit=200",
                         timeout=10).json().get("items") or []
    prev_active = next((p["id"] for p in plist if p.get("is_active")), None)
    motor = next((p for p in plist if p.get("name") == PNAME), None)
    run.step("01b 找到「电机装配全流程」项目", motor is not None)
    if motor is None:
        return
    if prev_active != motor["id"]:
        requests.post(f"{API}/projects/{motor['id']}/activate",
                      timeout=15).raise_for_status()
    else:
        prev_active = None  # 本来就是激活态, 收尾不用恢复

    # ============ 1. 起视频(慢速钉在工件在位画面) + 起检测 ============
    # 115s: 工件1 在位打前罩螺丝, 「工件」持续稳定可见
    requests.post(f"{API}/source/video/start?channel={CH}",
                  json={"file_path": VIDEO, "speed": 0.05},
                  timeout=15).raise_for_status()
    requests.post(f"{API}/source/video/progress?channel={CH}",
                  json={"progress": 115.0 / 650.0}, timeout=10)
    r = requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"conf": 0.4, "iou": 0.45,
                            "model_path": model["file_path"]},
                      timeout=120)
    run.step("02 视频+检测启动", r.status_code == 200, r.text[:120])

    # 注: 电机项目激活后「工件」是锚点标签而非步骤, 不发布到实时结果 —
    # 这里只确认推理真的在跑(检测态+推理FPS>0), 抓取判据在步骤 08/11
    fps = 0
    t_end = time.time() + 90
    while time.time() < t_end:
        st = _results()
        fps = st.get("fps_inference") or 0
        if st.get("is_detecting") and fps > 0:
            break
        time.sleep(0.5)
    run.step("03 检测真实在跑(检测态+推理FPS>0)", fps > 0, f"fps={fps}")

    with sync_playwright() as p:
        browser, ctx, page, console_errs = launch_browser(
            p, record_video_dir=run.video_dir)

        def _open_sidebar():
            if not page.locator("nav >> text=项目管理").first.is_visible():
                page.locator("button[title='导航菜单']").first.click()
                time.sleep(0.8)

        # ============ 2. 零回归面: 检测中菜单仍被锁 ============
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(3.0)
        _open_sidebar()
        page.locator("nav >> text=项目管理").first.click()
        time.sleep(1.2)
        still_monitor = "/monitor" in page.url
        blocked_tip = page.locator("text=检测运行中").first.is_visible()
        run.step("04 检测中点菜单仍被拦(老约束零回归)",
                 still_monitor and blocked_tip,
                 f"url={page.url} tip={blocked_tip}")
        run.shot(page, "04_检测中菜单被拦")

        # ============ 3. 路 A: 待机 → 切项目页 → 抓取成功 ============
        requests.post(f"{API}/source/detection/standby?channel={CH}", timeout=10)
        time.sleep(1.5)
        dets_after = _results().get("detections") or []
        run.step("05 待机后实时检测结果被清空(缺陷前提复现)", not dets_after,
                 f"残留={len(dets_after)}")

        # 前端 isDetecting 靠轮询刷新, 等侧边栏红条消失(状态已翻)再点菜单
        _open_sidebar()
        t_end = time.time() + 20
        while time.time() < t_end:
            if not page.locator("aside >> text=检测运行中").first.is_visible():
                break
            time.sleep(0.5)
        page.locator("nav >> text=项目管理").first.click()
        # 判据: 项目页搜索框出现(比轮询 url 可靠, hash 路由更新有延迟)
        nav_ok = True
        try:
            page.wait_for_selector("input[placeholder*='搜索项目']",
                                   timeout=10000)
        except Exception:
            nav_ok = False
        run.step("06 待机后菜单可以切到项目页", nav_ok, page.url)

        page.locator("input[placeholder*='搜索项目']").fill(PNAME)
        time.sleep(0.6)
        page.locator(f"div.p-4:has-text('{PNAME}')").first.click()
        time.sleep(1.2)
        page.locator(".el-tabs__item:has-text('步骤设置')").first.click()
        time.sleep(1.2)
        row = page.locator("tr", has=page.locator("text=锚点跟随")).first
        row.locator("button:has-text('编辑')").first.click()
        time.sleep(1.5)
        dlg = page.locator(".el-dialog:visible").first
        run.step("07 打开打螺丝拆分编辑器", dlg.is_visible())
        dlg.locator("button:has-text('刷新画面快照')").first.click()
        time.sleep(1.2)
        run.shot(page, "07_编辑器已开_待机态")

        # 判据必须硬: 该规则历史上已标定过(绿色徽标本来就在), 直接断言
        # ① 抓取真的走了 infer-once 兜底且返回含「工件」 ② 弹出成功 toast
        def _grab_and_assert(tag: str):
            with page.expect_response(
                    lambda r: "/detection/infer-once" in r.url,
                    timeout=15000) as resp_info:
                dlg.locator(
                    "button:has-text('从当前画面抓取锚点框')").first.click()
            resp = resp_info.value
            body = resp.json() if resp.status == 200 else {}
            labels = [d.get("label") for d in body.get("detections") or []]
            toast_seen = False
            t_end2 = time.time() + 6
            while time.time() < t_end2:
                if page.locator(
                        ".el-message--success:has-text('已标定锚点框')").count():
                    toast_seen = True
                    break
                time.sleep(0.2)
            return (resp.status == 200 and body.get("mode") == "one_shot"
                    and "工件" in labels and toast_seen,
                    f"status={resp.status} mode={body.get('mode')} "
                    f"labels={labels[:6]} toast={toast_seen}")

        ok_a, detail_a = _grab_and_assert("A")
        run.step("08 路A(待机): 抓取走单帧推理兜底成功", ok_a, detail_a)
        run.shot(page, "08_待机态抓取成功")

        # ============ 4. 路 B: 恢复检测 → 停止(定格) → 再抓仍成功 ============
        requests.post(f"{API}/source/detection/resume-inference?channel={CH}",
                      timeout=30)
        t_end = time.time() + 30
        while time.time() < t_end:
            if _results().get("is_detecting"):
                break
            time.sleep(0.5)
        run.step("09 恢复检测成功", bool(_results().get("is_detecting")))
        time.sleep(5)  # 等推理线程热起来再停(模拟现场"跑了一会儿再点停止")
        requests.post(f"{API}/source/detection/pause?channel={CH}", timeout=10)
        time.sleep(1.5)
        st = _results()
        run.step("10 停止后: 结果清空+画面定格", not (st.get("detections") or []),
                 f"is_running={st.get('is_running')}")

        dlg.locator("button:has-text('刷新画面快照')").first.click()
        time.sleep(1.0)
        ok_b, detail_b = _grab_and_assert("B")
        run.step("11 路B(停止定格): 抓取仍成功", ok_b, detail_b)
        run.shot(page, "11_停止态抓取成功")

        # 不保存: 直接关掉编辑器与配置页, 不污染线上项目
        page.keyboard.press("Escape")
        time.sleep(0.8)

        real_errs = filter_console_errors(console_errs)
        run.step("12 控制台无前端逻辑报错", not real_errs,
                 f"真报错={real_errs[:3]}")
        ctx.close()
        browser.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
    raise SystemExit(run.finish())
