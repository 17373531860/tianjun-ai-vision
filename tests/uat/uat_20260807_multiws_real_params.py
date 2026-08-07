"""UAT 2026-08-07: 多工位新布局 × 真模型真视频 × 参数反复更改一致性验证.

场景 (吉田客户真实资产, 本机 /Users/tianjun/Public/测试使用/):
  ch0 = 2K17421.pt + 1号工位录像 (7 步 SOP)
  ch1 = 3K11311.pt + 对应录像   (7 步 SOP)
  ch2 = K10916.pt  + 对应录像   (6 步 SOP)

验证矩阵:
  P1 真实推理跑起来: 3 通道 is_detecting / fps>0 / detections 出现
  P2 计数一致性: 后端 counters 单调不减; UI 三行计数卡与 API 数值一致
  P3 项目设置反复改: 禁用一步 + 改显示名 → SOP 卡数量/名称跟随, 计数不清零
  P4 显示设置反复改: ngTop3/不良卡/PT列/合格率环 关→隐藏, 恢复→回来
  P5 检测参数反复改: conf 0.8 → 0.25 重启检测, 每次都恢复检测中

跑法: conda tianjun env python 直跑本文件 (后端 8002 / 前端 6002 需已启动)
产物: evidence/shots_real/*.png + run 日志 stdout
"""
import json
import os
import signal
import sys
import time
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

# 全局看门狗: 任何情况下 12 分钟必须结束 (上一轮曾无输出挂 65min)
signal.alarm(720)

API = "http://localhost:8002"
FRONTEND = "http://localhost:6002"
ASSETS = "/Users/tianjun/Public/测试使用"
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "evidence", "shots_real")
os.makedirs(OUT, exist_ok=True)

CHANNELS = [
    dict(ch=0, name="吉田-2K17421-1号工位",
         model=f"{ASSETS}/2K17421.pt",
         video=f"{ASSETS}/2K17421(1号工位）视频/Video_20260730152737358.avi",
         labels=["拿取产品", "调节螺丝", "检查内框活动性", "转动滑轮", "检查外观", "确认内框有无下落", "放计数板"]),
    dict(ch=1, name="吉田-3K11311",
         model=f"{ASSETS}/3K11311.pt",
         video=f"{ASSETS}/3K11311/Video_20260803102754209.avi",
         labels=["拿取产品", "调节螺丝", "检查内框活动性", "转动滑轮", "检查外观", "确认内框有无下落", "放计数板"]),
    dict(ch=2, name="吉田-K10916",
         model=f"{ASSETS}/K10916.pt",
         video=f"{ASSETS}/k10916/Video_20260730163014396.avi",
         labels=["拿取产品", "调节螺丝", "转动滑轮", "检查外观", "确认内框活动性", "放计数板"]),
]

PASS, FAIL = [], []


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def check(cond, msg):
    (PASS if cond else FAIL).append(msg)
    log(("  ✅ " if cond else "  ❌ ") + msg)


def project_payload(spec, disable_last=False, rename_first=None):
    steps = []
    for i, lb in enumerate(spec["labels"]):
        s = {"id": f"s{i}", "label": lb, "enabled": True, "threshold": 50, "min_frames": 2}
        if rename_first and i == 0:
            s["displayLabel"] = rename_first
        steps.append(s)
    if disable_last:
        steps[-1]["enabled"] = False
    return {
        "project_id": 99000 + spec["ch"],
        "name": spec["name"],
        "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": steps,
        # 计数器只靠事件 actions 里的 counter 动作递增 (product 行为, Project 页默认生成),
        # actions 留空则总产量/合格/不良永远 0 (上一轮实测踩到)
        "events_config": [
            {"id": 1, "name": "OK", "show_notification": True, "actions": [
                {"type": "counter", "counter_name": "总产量", "value": 1},
                {"type": "counter", "counter_name": "合格总数", "value": 1},
            ]},
            {"id": 2, "name": "NG", "show_notification": True, "actions": [
                {"type": "counter", "counter_name": "总产量", "value": 1},
                {"type": "counter", "counter_name": "不良总数", "value": 1},
            ]},
        ],
        "counters_config": [],
        # idle_timeout_seconds=8: 视频动作顺序与配置不严格一致时, 周期靠空闲超时强制结算,
        # 否则 sequential 周期永不闭合、总产量一直 0 (上一轮实测踩到)
        # sequence_order 必填: 顺序模式结算按它给期望序列, 缺了周期被静默丢弃 (上上轮实测踩到)
        "pipeline_config": {
            "settlement_mode": "first_step",
            "idle_timeout_seconds": 8,
            "sequence_order": [{"step_id": f"s{i}"} for i in range(len(spec["labels"]))],
        },
        "data_config": {},
    }


def api(method, path, timeout=30, **kw):
    r = requests.request(method, f"{API}/api/v1{path}", timeout=timeout, **kw)
    return r


def results(ch):
    return api("GET", f"/source/detection/results?channel={ch}").json()


def counters(ch):
    c = results(ch).get("counters") or {}
    return (c.get("总产量", 0), c.get("合格总数", 0), c.get("不良总数", 0))


def main():
    for spec in CHANNELS:
        assert os.path.isfile(spec["model"]) and os.path.isfile(spec["video"]), f"资产缺失: {spec}"

    original_count = api("GET", "/workstations/").json()["channel_count"]
    print(f"原始工位数 {original_count} → 切 3 工位")
    api("POST", "/workstations/mode", json={"channel_count": 3}).raise_for_status()

    try:
        # ---------- P0 启动: set-project → video/start → detection/start ----------
        # ⚠️ macOS 已知限制: 多通道并发 MPS 推理会触发 Metal 断言崩溃
        # (addScheduledHandler after commit), 本 UAT 全通道强制 CPU。
        # 客户机 Windows CUDA 不受影响。
        log("== P0 启动 3 通道 真视频+真模型 (device=cpu 绕开 MPS 并发崩溃) ==")
        for spec in CHANNELS:
            ch = spec["ch"]
            r = api("POST", f"/workstations/{ch}/gpu", json={"device": "cpu"})
            check(r.status_code == 200, f"ch{ch} 强制 CPU 设备: {r.status_code}")
            r = api("POST", f"/source/detection/set-project?channel={ch}", json=project_payload(spec))
            check(r.status_code == 200, f"ch{ch} set-project: {r.status_code}")
            r = api("POST", f"/source/video/start?channel={ch}", json={"file_path": spec["video"], "speed": 3.0})
            check(r.status_code == 200, f"ch{ch} video/start(3x): {r.status_code} {r.text[:80]}")
            # CPU 模型加载 + 960px warmup 可能远超 30s, 单独放宽
            r = api("POST", f"/source/detection/start?channel={ch}", timeout=300,
                    json={"model_path": spec["model"], "conf": 0.3, "iou": 0.45})
            check(r.status_code == 200, f"ch{ch} detection/start: {r.status_code} {r.text[:80]}")

        # ---------- P1 推理健康 ----------
        log("== P1 等 20s 模型加载+推理起跑 ==")
        time.sleep(20)
        for spec in CHANNELS:
            d = results(spec["ch"])
            check(d.get("is_detecting") is True, f"ch{spec['ch']} is_detecting")
            check((d.get("fps") or 0) > 0, f"ch{spec['ch']} fps={d.get('fps')}")

        # ---------- P2 计数验证: 长窗口 + 视频放完自动重启 (让 first_step 结算多轮) ----------
        # CPU 推理 ~3fps, 瞬时 detections 字段很难恰好采到 → 检测证据改用 step_counts 累计;
        # 视频 300s/3x=100s 一轮, 放完自动重启, 第二轮"拿取产品"触发 first_step 结算 → 总产量动
        log("== P2 跑 130s, 视频循环重启, 计数单调性 + 步骤活动 ==")
        t1 = {s["ch"]: counters(s["ch"]) for s in CHANNELS}
        seen_step = {s["ch"]: False for s in CHANNELS}
        deadline = time.time() + 130
        while time.time() < deadline:
            time.sleep(5)
            for spec in CHANNELS:
                ch = spec["ch"]
                d = results(ch)
                if sum((d.get("step_counts") or {}).values()) > 0 or d.get("detections"):
                    seen_step[ch] = True
                if not d.get("is_running"):
                    log(f"  ch{ch} 视频放完 → 重启视频+检测 (计数应保留)")
                    api("POST", f"/source/video/start?channel={ch}",
                        json={"file_path": spec["video"], "speed": 3.0})
                    api("POST", f"/source/detection/start?channel={ch}", timeout=300,
                        json={"model_path": spec["model"], "conf": 0.3, "iou": 0.45})
        t2 = {s["ch"]: counters(s["ch"]) for s in CHANNELS}
        for spec in CHANNELS:
            ch = spec["ch"]
            check(seen_step[ch], f"ch{ch} 步骤统计/检测框有活动")
            check(all(b >= a for a, b in zip(t1[ch], t2[ch])),
                  f"ch{ch} 计数单调不减 {t1[ch]} → {t2[ch]}")
            total, ok, ng = t2[ch]
            check(total == 0 or abs(total - (ok + ng)) <= 1,
                  f"ch{ch} 总产量≈合格+不良 ({total} vs {ok}+{ng})")
        check(any(t2[s["ch"]][0] > 0 for s in CHANNELS),
              f"至少一个通道产生了已结算周期 (总产量>0): {t2}")
        log(f"  计数快照: {t2}")

        # ---------- P2b UI 与 API 一致 ----------
        log("== P2b 浏览器核对 UI 计数与 API 一致 ==")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1920, "height": 1080},
                                      record_video_dir=OUT)
            page = ctx.new_page()
            page.goto(f"{FRONTEND}/#/monitor", wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            page.screenshot(path=f"{OUT}/p2_triple_real.png")

            ui_totals = page.locator("xpath=//div[text()='总产量']/following-sibling::div").all_inner_texts()
            api_totals = [counters(s["ch"])[0] for s in CHANNELS]
            check(len(ui_totals) == 3, f"UI 有 3 张总产量卡: {ui_totals}")
            for i, spec in enumerate(CHANNELS):
                if i < len(ui_totals):
                    ui_v = int(ui_totals[i])
                    check(abs(ui_v - api_totals[i]) <= 1,
                          f"ch{spec['ch']} UI 总产量 {ui_v} ≈ API {api_totals[i]}")

            # 合格率环与 NG TOP3 存在 (默认显示设置)
            check(page.locator("text=NG 步骤 TOP3").count() == 3, "默认设置: 3 行都有 NG TOP3")
            check(page.locator("svg circle").count() >= 6, "默认设置: 3 个合格率环渲染")

            # ---------- P3 项目设置反复改 ----------
            log("== P3 ch0 项目设置: 禁用末步 + 首步改名 ==")
            before = counters(0)
            r = api("POST", "/source/detection/set-project?channel=0",
                    json=project_payload(CHANNELS[0], disable_last=True, rename_first="拿取产品(改)"))
            check(r.status_code == 200, f"ch0 重新 set-project: {r.status_code}")
            time.sleep(3)
            d = results(0)
            enabled = [s for s in (d.get("project_config") or {}).get("steps_config", [])
                       if s.get("enabled") is not False]
            check(len(enabled) == 6, f"ch0 后端生效步骤 7→6: {len(enabled)}")
            after = counters(0)
            check(all(b >= a for a, b in zip(before, after)),
                  f"ch0 改项目配置计数不清零 {before} → {after}")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            check(page.locator("text=拿取产品(改)").count() >= 1, "UI 显示改名后的步骤名")
            row0_cards = page.locator("xpath=(//div[span[text()='SOP']])[1]/following-sibling::div/div").count()
            print(f"  (ch0 SOP 卡片数 DOM 采样: {row0_cards})")
            page.screenshot(path=f"{OUT}/p3_project_changed.png")

            # 改回去
            r = api("POST", "/source/detection/set-project?channel=0", json=project_payload(CHANNELS[0]))
            check(r.status_code == 200, "ch0 项目配置还原")

            # ---------- P4 显示设置反复改 ----------
            log("== P4 显示设置: 关 ngTop3/不良卡/PT列/合格率环 → 隐藏; 还原 → 回来 ==")
            page.evaluate("""() => {
              const cur = JSON.parse(localStorage.getItem('display_settings') || '{}');
              cur.monitor = Object.assign({}, cur.monitor, {
                ngTop3: false,
                capacityChart: false,
                stepTableColumns: { showNo: true, showStep: true, showStatus: true, showPt: false, showResult: true },
                defaultCounters: { showTotal: true, showGood: true, showBad: false, showNgSteps: true },
                ctMode: 'last',
              });
              localStorage.setItem('display_settings', JSON.stringify(cur));
            }""")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            check(page.locator("text=NG 步骤 TOP3").count() == 0, "关 ngTop3 → TOP3 面板消失")
            check(page.locator("xpath=//div[text()='不良']").count() == 0, "关 showBad → 不良计数卡消失")
            check(page.locator("th", has_text="PT/s").count() == 0, "关 showPt → PT 列消失")
            check(page.locator("xpath=//div[text()='合格率']").count() == 0, "关 capacityChart → 合格率环消失")
            page.screenshot(path=f"{OUT}/p4_display_off.png")

            page.evaluate("() => localStorage.removeItem('display_settings')")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            check(page.locator("text=NG 步骤 TOP3").count() == 3, "还原显示设置 → TOP3 回来")
            check(page.locator("xpath=//div[text()='不良']").count() == 3, "还原 → 不良卡回来")
            check(page.locator("xpath=//div[text()='合格率']").count() == 3, "还原 → 合格率环回来")
            page.screenshot(path=f"{OUT}/p4_display_restored.png")

            # ---------- P5 检测参数反复改 ----------
            log("== P5 conf 0.8 → 0.25 反复重启检测 ==")
            for conf in (0.8, 0.25):
                # 先重启视频保证有 ~46s 播放余量, 避免视频恰好放完导致检测自动停 (时序竞态)
                api("POST", "/source/video/start?channel=0",
                    json={"file_path": CHANNELS[0]["video"], "speed": 3.0})
                r = api("POST", "/source/detection/start?channel=0", timeout=300,
                        json={"model_path": CHANNELS[0]["model"], "conf": conf, "iou": 0.45})
                check(r.status_code == 200, f"ch0 conf={conf} 重启检测: {r.status_code}")
                time.sleep(6)
                d = results(0)
                check(d.get("is_detecting") is True and (d.get("fps") or 0) > 0,
                      f"ch0 conf={conf} 后仍检测中 fps={d.get('fps')}")
            page.screenshot(path=f"{OUT}/p5_conf_cycled.png")

            ctx.close()
            browser.close()
    finally:
        log("== 清理: 停检测/停视频/还原工位数 ==")
        try:
            for spec in CHANNELS:
                api("POST", f"/source/detection/stop?channel={spec['ch']}")
                api("POST", f"/source/video/stop?channel={spec['ch']}")
            api("POST", "/workstations/mode", json={"channel_count": original_count})
            print(f"工位数已还原 {original_count}")
        except requests.exceptions.ConnectionError:
            print("⚠️ 后端已失联, 清理跳过 (重启后端后需手动核对工位数)")

    print(f"\n===== 结果: PASS {len(PASS)} / FAIL {len(FAIL)} =====")
    for m in FAIL:
        print("  ❌", m)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
