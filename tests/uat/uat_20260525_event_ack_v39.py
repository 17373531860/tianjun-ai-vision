"""UAT (面向功能测试) — v3.9.x 事件人工确认机制 (require_ack + 超时自动确认).

客户现场叙事:
  操作员选择某个 NG / 自定义事件配上"需人工确认", 检测时该事件触发后:
    1. 原 NG 提示框正常弹 + 工位边框红框 (toast / 计数器累加)
    2. 同时弹"确认重做"全屏覆盖层, 画面定格在事件触发瞬间
    3. 工人重做这一件, 点"我已确认 — 重做工位 X" 按钮 → 清当前周期运行时
       (合格 / 不合格 / 自定义计数器累计值不动)
    4. 如果设了 ack_timeout_sec > 0 + 工人离岗未确认 → 自动解除阻塞

三段验证:
  Phase A: API 契约 + 真实状态机闭环
           创建 last_first 项目 + 不合格事件 require_ack=True / timeout=20s
           注入 last_first_settlement.json 剧本 (周期 2 跳 D → R3 触发 NG)
           polling 等 pending_ack.active=True → 调 ack-event → 验清除 + 计数保留

  Phase B: 浏览器真渲染 (Playwright headed + 录像 + 截图)
           Project 页选中项目 → 事件配置 tab → 验证"需人工确认"开关已勾
           Monitor 页启动检测 + 注入剧本 → 等覆盖层显示 → 截图 → 点确认 → 截图

  Phase C: 超时自动确认 (API 端验收)
           创建短 timeout (3s) 项目 → 触发阻塞 → 等 5s → 验证 pending_ack 自动清

三件套证据:
  视频:    /tmp/uat_ack_video/*.webm
  截图:    /tmp/uat_ack_shots/*.png
  日志:    /tmp/uat_ack_run.json + /tmp/uat_ack_backend.log
"""
import json
import os
import time
import uuid
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# ============================================================
# 配置
# ============================================================
API = "http://127.0.0.1:8011"          # UAT 独立后端
FRONTEND = "http://127.0.0.1:6001"     # UAT 独立前端 (VITE_API_BASE_URL=API)
SHOTS = "/tmp/uat_ack_shots"
VIDEO = "/tmp/uat_ack_video"
LOG = "/tmp/uat_ack_run.json"
BACKEND_LOG = "/tmp/uat_ack_backend.log"

Path(SHOTS).mkdir(parents=True, exist_ok=True)
Path(VIDEO).mkdir(parents=True, exist_ok=True)

steps_log = []


def step(label: str, ok: bool, detail: str = ""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    sym = "OK" if ok else "!!"
    print(f"[{sym}] {rec['idx']:02d}. {label}  {detail}")


# ============================================================
# 项目工厂 — last_first + 不合格事件 require_ack
# ============================================================
def make_payload(name_suffix: str, ng_require_ack: bool = True,
                 ng_timeout_sec: int = 20):
    return {
        "name": f"__uat_ack_{name_suffix}_{int(time.time() * 1000)}",
        "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": [
            {"id": i, "label": lbl, "displayLabel": lbl, "enabled": True,
             "min_frames": 1, "threshold": 0.3,
             "strict_order": False}
            for i, lbl in enumerate(["A", "B", "C", "D"], start=1)
        ],
        "events_config": [
            {"id": 1, "name": "合格", "actions": [{"counter_name": "合格总数", "delta": 1}],
             "show_notification": True, "toast_id": "ok",
             "require_ack": False, "ack_timeout_sec": 0},
            {"id": 2, "name": "不合格", "actions": [{"counter_name": "不良总数", "delta": 1}],
             "show_notification": True, "toast_id": "ng",
             "require_ack": ng_require_ack, "ack_timeout_sec": ng_timeout_sec},
        ],
        "counters_config": [
            {"name": "合格总数", "value": 0},
            {"name": "不良总数", "value": 0},
            {"name": "总产量", "value": 0},
        ],
        "pipeline_config": {
            "sequence_order": [{"step_id": i} for i in range(1, 5)],
            "simultaneous_groups": [],
            "settlement_mode": "last_first",
            "settle_dedup": False,
        },
    }


def cleanup_all():
    """清掉所有 __uat_ack_ 前缀的项目."""
    try:
        r = requests.get(f"{API}/api/v1/projects", timeout=4)
        if r.status_code != 200:
            return
        data = r.json()
        items = data.get("items") if isinstance(data, dict) else data
        for p in items or []:
            name = p.get("name") or ""
            if name.startswith("__uat_ack_"):
                try:
                    requests.delete(f"{API}/api/v1/projects/{p['id']}", timeout=4)
                except Exception:
                    pass
    except Exception:
        pass


def stop_clean(channel: int = 0):
    """统一停止 + 清状态 (确保前一阶段不污染)."""
    try:
        requests.post(f"{API}/api/v1/source/detection/stop?channel={channel}", timeout=4)
    except Exception:
        pass
    try:
        requests.post(f"{API}/api/v1/test/synthetic/stop?channel={channel}", timeout=4)
    except Exception:
        pass
    try:
        requests.post(f"{API}/api/v1/source/detection/reset-stats?channel={channel}", timeout=4)
    except Exception:
        pass
    # 兜底确认: 即便没阻塞调一次也无副作用 (返回 acked=False)
    try:
        requests.post(f"{API}/api/v1/source/detection/ack-event?channel={channel}", timeout=4)
    except Exception:
        pass


def wait_pending_ack(channel: int = 0, timeout: float = 15.0) -> dict:
    """polling 等 pending_ack.active=True, 返回阻塞时刻的 results dict."""
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{API}/api/v1/source/detection/results?channel={channel}", timeout=3)
            if r.status_code == 200:
                last = r.json() or {}
                pa = last.get("pending_ack") or {}
                if pa.get("active"):
                    return last
        except Exception:
            pass
        time.sleep(0.2)
    return last


def wait_pending_ack_clear(channel: int = 0, timeout: float = 10.0) -> dict:
    """polling 等 pending_ack.active=False."""
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{API}/api/v1/source/detection/results?channel={channel}", timeout=3)
            if r.status_code == 200:
                last = r.json() or {}
                pa = last.get("pending_ack") or {}
                if not pa.get("active"):
                    return last
        except Exception:
            pass
        time.sleep(0.2)
    return last


# ============================================================
# Phase A: API 契约 + 真实状态机闭环
# ============================================================
def phase_a_api():
    print("\n========== Phase A: API 契约 + 真实状态机闭环 ==========")

    # ── A1: 创建项目, 验 require_ack 落库
    payload = make_payload("a1_basic")
    r = requests.post(f"{API}/api/v1/projects/", json=payload, timeout=8)
    step("A1.1 创建带 require_ack 的项目",
         r.status_code in (200, 201),
         f"status={r.status_code}")
    if r.status_code not in (200, 201):
        print(f"  详情: {r.text[:400]}")
        return False
    proj = r.json()
    proj_id = proj["id"]
    proj_name = proj["name"]

    # 验 events_config 第二条 require_ack=True / ack_timeout_sec=20
    ev_cfg = proj.get("events_config") or []
    ng_event = next((e for e in ev_cfg if e.get("id") == 2), None) or {}
    step("A1.2 不合格事件 require_ack=True 落库",
         ng_event.get("require_ack") is True,
         f"实际 require_ack={ng_event.get('require_ack')}")
    step("A1.3 不合格事件 ack_timeout_sec=20 落库",
         int(ng_event.get("ack_timeout_sec") or 0) == 20,
         f"实际 timeout={ng_event.get('ack_timeout_sec')}")

    # ── A2: 激活 + 启动检测 + 注入剧本 (last_first_settlement.json 周期 2 跳 D 触发 NG)
    stop_clean(0)
    r = requests.post(f"{API}/api/v1/projects/{proj_id}/activate", timeout=8)
    step("A2.1 激活项目", r.status_code == 200, f"status={r.status_code}")

    r = requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario": "last_first_settlement.json",
        "channel": 0,
        "with_project": False,
    }, timeout=8)
    step("A2.2 注入 synthetic 剧本", r.status_code == 200, f"status={r.status_code}")

    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=8)
    step("A2.3 启动检测", r.status_code == 200, f"status={r.status_code}")

    # ── A3: polling 等 pending_ack.active=True
    print("  等待状态机跑到 R3 触发不合格 (period 2 跳 D)...")
    blocked = wait_pending_ack(0, timeout=15.0)
    pa = blocked.get("pending_ack") or {}
    step("A3.1 detection/results 出现 pending_ack.active=True",
         pa.get("active") is True,
         f"pa={pa}")
    step("A3.2 阻塞事件名='不合格'",
         pa.get("event_name") == "不合格",
         f"实际={pa.get('event_name')}")
    step("A3.3 阻塞 timeout_sec=20",
         int(pa.get("timeout_sec") or 0) == 20,
         f"实际={pa.get('timeout_sec')}")
    step("A3.4 阻塞 started_at 是合理时间戳",
         (pa.get("started_at") or 0) > time.time() - 60,
         f"实际={pa.get('started_at')}")

    # 阻塞时计数应已累加 (NG 事件已触发, actions 已经执行)
    counters_at_block = (blocked.get("counters") or {})
    ng_count_at_block = int(counters_at_block.get("不良总数") or 0)
    step("A3.5 阻塞瞬间不良总数已累计 (说明事件正常执行)",
         ng_count_at_block >= 1,
         f"不良总数={ng_count_at_block}")

    # ── A4: 验"状态机冻结" — 阻塞期间 current_cycle_steps 不再增长 (新检测不被处理)
    # 注: synthetic 剧本走独立推理循环 (不走真实 capture_loop), 故 fps 仍正常.
    # 在真实摄像头场景下, capture_loop 守门会让 fps_actual 衰减为 0 (画面定格).
    cycle_steps_at_block = list(blocked.get("current_cycle_steps") or [])
    time.sleep(2.0)
    r = requests.get(f"{API}/api/v1/source/detection/results?channel=0", timeout=3)
    cycle_steps_after = list((r.json() or {}).get("current_cycle_steps") or [])
    step("A4.1 阻塞期间 current_cycle_steps 不再增长 (状态机冻结)",
         cycle_steps_after == cycle_steps_at_block,
         f"前={cycle_steps_at_block}, 后={cycle_steps_after}")

    # ── A5: 阻塞期间再触发的事件应被丢弃 (后续帧仍在喂, 但 _trigger_event 入口守门)
    # 验证策略: 阻塞 2 秒后看 events_log 不再增长 (跟阻塞瞬间对比)
    events_at_block = blocked.get("recent_events") or []
    events_after = (r.json() or {}).get("recent_events") or []
    new_events_during_block = [e for e in events_after if e.get("seq", 0) > max(
        [e0.get("seq", 0) for e0 in events_at_block] or [0])]
    step("A5.1 阻塞期间无新事件入 events_log (守门生效)",
         len(new_events_during_block) == 0,
         f"新增事件数={len(new_events_during_block)}")

    # ── A6: 调 ack-event → 验阻塞清除 + 计数保留
    r = requests.post(f"{API}/api/v1/source/detection/ack-event?channel=0", timeout=4)
    body = r.json() if r.status_code == 200 else {}
    step("A6.1 ack-event 接口 acked=True",
         bool(body.get("acked")),
         f"body={body}")

    cleared = wait_pending_ack_clear(0, timeout=5.0)
    pa2 = cleared.get("pending_ack") or {}
    step("A6.2 ack 后 pending_ack.active=False",
         pa2.get("active") is False,
         f"pa={pa2}")

    counters_after_ack = (cleared.get("counters") or {})
    step("A6.3 ack 后不良总数保留 (未回滚)",
         int(counters_after_ack.get("不良总数") or 0) >= ng_count_at_block,
         f"前={ng_count_at_block}, 后={counters_after_ack.get('不良总数')}")

    # 收尾
    stop_clean(0)
    return True


# ============================================================
# Phase B: 浏览器真渲染 (Playwright headed + 录像 + 截图)
# ============================================================
def phase_b_browser():
    print("\n========== Phase B: 浏览器真渲染 ==========")

    # 创建一个全新的 require_ack 项目供浏览器用
    payload = make_payload("b_browser", ng_timeout_sec=30)
    r = requests.post(f"{API}/api/v1/projects/", json=payload, timeout=8)
    if r.status_code not in (200, 201):
        step("B0.0 创建浏览器测试项目", False, f"status={r.status_code}")
        return False
    proj_b = r.json()
    proj_b_id = proj_b["id"]
    proj_b_name = proj_b["name"]
    step("B0.0 创建浏览器测试项目", True, f"id={proj_b_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300,
                                    args=["--disable-blink-features=AutomationControlled",
                                          "--disable-dev-shm-usage"])
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 1000},
            ignore_https_errors=True,
        )
        page = ctx.new_page()

        try:
            # B1: 进 Project 页
            page.goto(f"{FRONTEND}/#/project")
            page.wait_for_load_state("networkidle")
            time.sleep(2.0)
            page.screenshot(path=f"{SHOTS}/B1_project_page.png", full_page=True)
            body_text = page.evaluate("document.body.innerText") or ""
            step("B1 Project 页加载",
                 ("项目" in body_text or "Project" in body_text) and len(body_text) > 100,
                 f"body 长度={len(body_text)}")

            # B2: 选中项目
            try:
                target = page.locator("div.cursor-pointer", has=page.get_by_text(proj_b_name, exact=True)).first
                target.scroll_into_view_if_needed(timeout=4000)
                target.click(timeout=4000)
                time.sleep(1.5)
                page.screenshot(path=f"{SHOTS}/B2_project_selected.png", full_page=True)
                step("B2 选中浏览器测试项目", True, proj_b_name)
            except Exception as e:
                step("B2 选中浏览器测试项目", False, f"定位失败: {e}")

            # B3: 切到事件配置 tab (找含"事件"的 tab)
            switched = False
            for name_try in ["事件配置", "事件设置", "事件"]:
                try:
                    page.get_by_role("tab", name=name_try, exact=False).first.click(timeout=2000)
                    time.sleep(1.0)
                    switched = True
                    break
                except Exception:
                    pass
            page.screenshot(path=f"{SHOTS}/B3_events_tab.png", full_page=True)
            step("B3 切到事件配置 tab", switched)

            # B4: 验"需人工确认"开关存在 + 已勾选 (因为项目里 NG 事件 require_ack=True)
            ack_label_visible = False
            ack_checked = False
            try:
                page.wait_for_selector("text=需人工确认", timeout=4000)
                ack_label_visible = page.get_by_text("需人工确认（重做本周期）").first.is_visible()
            except Exception:
                ack_label_visible = False
            step("B4.1 '需人工确认' 开关已渲染", ack_label_visible)

            try:
                # element-plus el-checkbox 选中态: label 上加 'is-checked' class.
                # 直接定位 label.el-checkbox.is-checked 含目标文字的, count>0 即说明已勾.
                ack_checked = page.locator("label.el-checkbox.is-checked",
                                           has_text="需人工确认").count() > 0
            except Exception:
                ack_checked = False
            step("B4.2 该开关已勾选 (与项目 require_ack=True 一致)", ack_checked)

            # 验超时秒数输入框存在 + 显示 30 (我们配的)
            timeout_visible = False
            timeout_value_ok = False
            try:
                page.wait_for_selector("text=超时自动确认（秒）", timeout=3000)
                timeout_visible = True
                inputs = page.locator("input[type='number']").all()
                for inp in inputs:
                    try:
                        v = inp.evaluate("el => el.value")
                        if v and int(v) == 30:
                            timeout_value_ok = True
                            break
                    except Exception:
                        pass
            except Exception:
                pass
            step("B4.3 '超时自动确认（秒）' 字段已渲染", timeout_visible)
            step("B4.4 超时输入框值=30 (与项目配置一致)", timeout_value_ok)

            # B5: 先用 API 起检测+剧本 → 再进 Monitor 页, 这样 onMounted 看到
            #     is_running=True 会立刻 startPolling, 拿到 pending_ack 渲染覆盖层.
            #     如果先进 Monitor 再起检测, 浏览器不会自动开 polling (Monitor 只在
            #     mount 时检查一次 source status).
            stop_clean(0)
            requests.post(f"{API}/api/v1/projects/{proj_b_id}/activate", timeout=8)

            r = requests.post(f"{API}/api/v1/test/synthetic/start", json={
                "scenario": "last_first_settlement.json",
                "channel": 0,
                "with_project": False,
            }, timeout=8)
            r2 = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                               json={"conf": 0.25, "iou": 0.45}, timeout=8)
            step("B5.1 通过 API 启动检测 + 剧本",
                 r.status_code == 200 and r2.status_code == 200,
                 f"synth={r.status_code}, det={r2.status_code}")

            # 现在进 Monitor 页, onMounted 会看到 is_running=True 自动 startPolling
            page.goto(f"{FRONTEND}/#/monitor")
            page.wait_for_load_state("networkidle")
            time.sleep(1.5)
            page.screenshot(path=f"{SHOTS}/B5_monitor_initial.png", full_page=True)

            # 等浏览器轮询拿到 pending_ack=True, 覆盖层渲染
            print("  等待覆盖层在浏览器渲染 (剧本约 2-3s 触发 NG)...")
            overlay_shown = False
            for _ in range(30):
                try:
                    if page.get_by_text("需要人工确认", exact=False).first.is_visible():
                        overlay_shown = True
                        break
                except Exception:
                    pass
                time.sleep(0.5)
            page.screenshot(path=f"{SHOTS}/B5_overlay_shown.png", full_page=True)
            step("B5.2 覆盖层在浏览器渲染 ('需要人工确认' 文字可见)", overlay_shown)

            # B6: 验覆盖层关键文字
            for txt, lbl in [("不合格", "B6.1 含触发事件名 '不合格'"),
                             ("我已确认", "B6.2 含确认按钮文字"),
                             ("工位 1", "B6.3 含工位编号")]:
                visible = False
                try:
                    visible = page.get_by_text(txt, exact=False).first.is_visible()
                except Exception:
                    visible = False
                step(lbl, visible)

            # B7: 点确认按钮
            ack_clicked = False
            try:
                btn = page.get_by_role("button", name="我已确认", exact=False).first
                btn.click(timeout=4000)
                ack_clicked = True
                time.sleep(1.0)
            except Exception as e:
                step("B7.0 点击确认按钮 (定位)", False, str(e))
            page.screenshot(path=f"{SHOTS}/B7_after_ack.png", full_page=True)
            step("B7.1 已点击 '我已确认' 按钮", ack_clicked)

            # B8: 覆盖层应消失.
            # 必须先停掉剧本 + 检测, 否则剧本继续跑 1-2 秒就会触发下一个 NG 阻塞,
            # 浏览器 polling 拿到 → 覆盖层立刻弹回, 假阴性 fail.
            stop_clean(0)
            time.sleep(2.0)  # 等浏览器 1Hz polling 拿到 active=False
            still_shown = True
            try:
                # is_visible 找不到元素时返回 False (而不是抛异常), 这里用 count 判更稳
                still_shown = page.locator("text=需要人工确认").count() > 0 \
                    and page.locator("text=需要人工确认").first.is_visible()
            except Exception:
                still_shown = False
            step("B8 停掉剧本+确认后, 覆盖层消失", not still_shown)
        finally:
            ctx.close()
            browser.close()

    return True


# ============================================================
# Phase C: 超时自动确认 (短 timeout 验收)
# ============================================================
def phase_c_timeout():
    print("\n========== Phase C: 超时自动确认 ==========")

    payload = make_payload("c_timeout", ng_timeout_sec=3)
    r = requests.post(f"{API}/api/v1/projects/", json=payload, timeout=8)
    if r.status_code not in (200, 201):
        step("C0.0 创建短 timeout 项目", False, f"status={r.status_code}")
        return False
    proj = r.json()
    pid = proj["id"]
    step("C0.0 创建短 timeout 项目 (timeout=3s)", True, f"id={pid}")

    stop_clean(0)
    requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=8)
    requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario": "last_first_settlement.json",
        "channel": 0,
        "with_project": False,
    }, timeout=8)
    requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                  json={"conf": 0.25, "iou": 0.45}, timeout=8)

    # 等 pending_ack.active=True
    blocked = wait_pending_ack(0, timeout=15.0)
    pa = blocked.get("pending_ack") or {}
    step("C1.1 触发阻塞 (timeout=3)",
         pa.get("active") is True and int(pa.get("timeout_sec") or 0) == 3,
         f"pa={pa}")

    started = pa.get("started_at") or 0
    print(f"  等 5 秒, 验证超时自动解除 (started_at={started})...")
    time.sleep(5.0)

    # 不调 ack, 直接看 pending_ack 是否自动清
    cleared = wait_pending_ack_clear(0, timeout=2.0)
    pa2 = cleared.get("pending_ack") or {}
    step("C1.2 5 秒后 pending_ack.active 自动清 (超时机制生效)",
         pa2.get("active") is False,
         f"pa={pa2}")

    stop_clean(0)
    return True


# ============================================================
# Main + 报告
# ============================================================
def write_report():
    fail = sum(1 for r in steps_log if not r["ok"])
    summary = {
        "total": len(steps_log),
        "passed": len(steps_log) - fail,
        "failed": fail,
        "steps": steps_log,
    }
    with open(LOG, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print(f"UAT 报告: total={summary['total']} passed={summary['passed']} failed={fail}")
    print(f"  视频: {VIDEO}/*.webm")
    print(f"  截图: {SHOTS}/*.png")
    print(f"  日志: {LOG}")
    print(f"  后端: {BACKEND_LOG}")
    if fail > 0:
        print("\n失败项:")
        for r in steps_log:
            if not r["ok"]:
                print(f"  !! {r['idx']:02d} {r['label']}  {r['detail']}")
    print(f"failed: {fail}")
    return fail


def main():
    print("=" * 60)
    print("UAT — 事件人工确认机制 (v3.9.x)")
    print("=" * 60)

    cleanup_all()
    try:
        phase_a_api()
        phase_b_browser()
        phase_c_timeout()
    finally:
        cleanup_all()
        fail = write_report()
        return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
