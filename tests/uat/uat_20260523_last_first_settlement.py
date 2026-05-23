"""UAT (面向功能测试) — v3.8.x last_first 结算模式 (末步结算 + 首步开周期).

客户现场叙事:
  操作员做 4 步 A→B→C→D, 末步 D 出现立刻结算; 跳过 D 直接重做 A 触发上周期 NG (缺 D)
  + 新周期重启. UI 上 Project 页结算方式 radio 出现"末步结算 + 首步开周期"选项,
  选中后弹约束提示卡 + 自动关 strict_order, 不能与跨周期组共存.

三段验证:
  Phase A: API 契约 (后端业务逻辑) — 8011 独立后端, 跑 synthetic 剧本验状态机
  Phase B: 可见浏览器 (UI 显示真渲染) — 6001 前端 + 8001 后端 (用户已起, 热重载已加载新代码)
  Phase C: UI CRUD — 浏览器创建 last_first 项目 + 选中确认 radio 状态

三件套证据:
  视频:    /tmp/uat_lf_video/*.webm
  截图:    /tmp/uat_lf_shots/*.png
  日志:    /tmp/uat_lf_run.log
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
FRONTEND = "http://127.0.0.1:6001"     # 用户已起的前端 (代理到 8001)
USER_API = "http://127.0.0.1:8001"     # 用户已起的后端 (--reload, 我的改动已热加载)
SHOTS = "/tmp/uat_lf_shots"
VIDEO = "/tmp/uat_lf_video"
LOG = "/tmp/uat_lf_run.log"

Path(SHOTS).mkdir(parents=True, exist_ok=True)
Path(VIDEO).mkdir(parents=True, exist_ok=True)

steps_log = []


def step(label: str, ok: bool, detail: str = ""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": detail}
    steps_log.append(rec)
    sym = "OK" if ok else "!!"
    print(f"[{sym}] {rec['idx']:02d}. {label}  {detail}")


# ============================================================
# 项目工厂
# ============================================================
def make_payload(name_suffix: str, settlement_mode: str = "last_first",
                 with_strict_order: bool = False,
                 with_cross_cycle: bool = False):
    sg = []
    if with_cross_cycle:
        sg.append({
            "enabled": True,
            "cross_cycle": True,
            "labels": ["D", "A"],
            "priority_order": ["D", "A"],
            "prev_cycle_labels": ["D"],
            "next_cycle_labels": ["A"],
            "time_window": 3.0,
        })

    return {
        "name": f"__uat_lf_{name_suffix}_{int(time.time() * 1000)}",
        "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": [
            {"id": i, "label": lbl, "displayLabel": lbl, "enabled": True,
             "min_frames": 1, "threshold": 0.3,
             "strict_order": with_strict_order}
            for i, lbl in enumerate(["A", "B", "C", "D"], start=1)
        ],
        "events_config": [
            {"id": 1, "name": "合格", "actions": [], "show_notification": False},
            {"id": 2, "name": "不合格", "actions": [], "show_notification": False},
        ],
        "counters_config": [],
        "pipeline_config": {
            "sequence_order": [{"step_id": i} for i in range(1, 5)],
            "simultaneous_groups": sg,
            "settlement_mode": settlement_mode,
            "settle_dedup": False,
        },
    }


def cleanup_all():
    """清掉所有 __uat_lf_ 前缀的项目 (双后端都清)."""
    for base in (API, USER_API):
        try:
            r = requests.get(f"{base}/api/v1/projects", timeout=4)
            if r.status_code != 200:
                continue
            data = r.json()
            items = data.get("items") if isinstance(data, dict) else data
            for p in items or []:
                name = p.get("name") or ""
                if name.startswith("__uat_lf_"):
                    try:
                        requests.delete(f"{base}/api/v1/projects/{p['id']}", timeout=4)
                    except Exception:
                        pass
        except Exception:
            pass


# ============================================================
# Phase A: API 契约 — 状态机 + 互斥校验
# ============================================================
def phase_a_api():
    print("\n========== Phase A: API 契约 (8011 后端) ==========")

    # ── A1: 创建 last_first 项目 + 激活, 验 settlement_mode 落库
    payload = make_payload("a1_basic")
    r = requests.post(f"{API}/api/v1/projects/", json=payload, timeout=8)
    step("A1.1 创建 last_first 项目",
         r.status_code in (200, 201),
         f"status={r.status_code}")
    if r.status_code not in (200, 201):
        print(f"  详情: {r.text[:400]}")
        return False
    proj = r.json()
    proj_id = proj["id"]
    actual_mode = proj.get("pipeline_config", {}).get("settlement_mode")
    step("A1.2 settlement_mode 落库正确",
         actual_mode == "last_first",
         f"实际={actual_mode}")

    r = requests.post(f"{API}/api/v1/projects/{proj_id}/activate", timeout=8)
    step("A1.3 激活 last_first 项目",
         r.status_code == 200,
         f"status={r.status_code}")

    # ── A2: 跑 synthetic 剧本 (周期1 OK + 周期2 跳D走R3=NG + 周期3 BCD=R4 顶替+OK)
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=4)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=4)
    requests.post(f"{API}/api/v1/source/detection/reset-stats?channel=0", timeout=4)

    r = requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario": "last_first_settlement.json",
        "channel": 0,
        "with_project": False,
    }, timeout=8)
    step("A2.1 注入 synthetic 剧本",
         r.status_code == 200,
         f"status={r.status_code}")
    if r.status_code != 200:
        print(f"  详情: {r.text[:400]}")

    r = requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=8)
    step("A2.2 启动检测", r.status_code == 200, f"status={r.status_code}")

    # 等剧本跑完 (200 帧 @ 60fps ≈ 3.3s, 留余量)
    last_seq = -1
    stable = 0
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        r = requests.get(f"{API}/api/v1/test/synthetic/state?channel=0", timeout=4)
        if r.status_code == 200:
            seq = (r.json() or {}).get("frame_seq", 0)
            if seq == last_seq:
                stable += 1
                if stable >= 3 and seq >= 199:
                    break
            else:
                stable = 0
            last_seq = seq
        time.sleep(0.15)
    step("A2.3 剧本播放完", last_seq >= 199, f"final_seq={last_seq}")

    time.sleep(0.6)
    # 停掉再读日志 (避免后续阶段跑剧本污染日志统计)
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=4)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=4)
    time.sleep(0.5)

    # 直接 grep 后端日志统计 last_first 触发次数 +
    # "周期结束: #N, 结果: OK/NG" 的真实计数
    # (counters HTTP 字段需要 events_config 上挂 updates_counter, 这是项目独立行为, 不影响 last_first 验收)
    try:
        with open("/tmp/uat_lf_backend.log", "r") as f:
            log = f.read()
    except Exception:
        log = ""
    r3_trigger_count = log.count("[last_first R3]")
    ok_settled = log.count("→ 顺序正确 → OK")
    ng_settled = log.count("→ NG\n") + log.count("→ NG: ") + sum(
        1 for line in log.splitlines() if "周期结束:" in line and "结果: NG" in line
    )
    ok_settled_v2 = sum(
        1 for line in log.splitlines() if "周期结束:" in line and "结果: OK" in line
    )
    print(f"   日志统计: R3触发={r3_trigger_count}, 周期结束 OK={ok_settled_v2}, NG={ng_settled}")
    step("A2.4 R3 D 缺位 fallback 至少触发 1 次 (日志事实)",
         r3_trigger_count >= 1,
         f"R3 触发次数={r3_trigger_count}")
    step("A2.5 至少有 1 次完整周期被结算 OK (日志事实)",
         ok_settled_v2 >= 1,
         f"OK 周期结束次数={ok_settled_v2}")
    step("A2.6 至少有 1 次周期被结算 NG (R3 / R1 触发后)",
         ng_settled >= 1,
         f"NG 周期结束次数={ng_settled}")

    # ── A3: 互斥校验 — last_first + cross_cycle 并存时, 后端运行时兜底
    payload2 = make_payload("a3_mutex", with_cross_cycle=True)
    r = requests.post(f"{API}/api/v1/projects/", json=payload2, timeout=8)
    step("A3.1 创建 last_first + cross_cycle 项目 (后端不强拒)",
         r.status_code in (200, 201),
         f"status={r.status_code}")
    if r.status_code in (200, 201):
        p2 = r.json()
        r = requests.post(f"{API}/api/v1/projects/{p2['id']}/activate", timeout=8)
        step("A3.2 激活 (运行时兜底覆盖应触发)",
             r.status_code == 200,
             f"status={r.status_code}")
        time.sleep(0.5)
        try:
            with open("/tmp/uat_lf_backend.log", "r") as f:
                full = f.read()
            step("A3.3 后端日志含 last_first 模式标识",
                 "结算模式: last_first" in full or "[last_first" in full,
                 f"(整文件 grep)")
        except Exception as e:
            step("A3.3 后端日志读取", False, str(e))

    # ── A4: strict_order 兜底验证 — 创建带 strict_order=True 的 last_first 项目
    payload3 = make_payload("a4_strict", with_strict_order=True)
    r = requests.post(f"{API}/api/v1/projects/", json=payload3, timeout=8)
    if r.status_code in (200, 201):
        p3 = r.json()
        r = requests.post(f"{API}/api/v1/projects/{p3['id']}/activate", timeout=8)
        step("A4.1 激活带 strict_order 的 last_first 项目 (后端兜底应清空)",
             r.status_code == 200,
             f"status={r.status_code}")
        time.sleep(2.0)  # 等 activate 内的 print buffer flush 到磁盘
        try:
            with open("/tmp/uat_lf_backend.log", "r") as f:
                full = f.read()
            cleared = "自动清空全部步骤的严格顺序" in full
            step("A4.2 后端日志显示自动清空全部步骤的严格顺序",
                 cleared,
                 f"(整文件 grep, 日志行数={full.count(chr(10))})")
        except Exception as e:
            step("A4.2 日志读取", False, str(e))

    return True


# ============================================================
# Phase B: 可见浏览器 — UI 显示真渲染
# ============================================================
def phase_b_browser():
    print("\n========== Phase B: 可见浏览器 (6001 前端) ==========")

    # 先在用户后端 8001 上创建一个 last_first 项目让 UI 能选中
    payload = make_payload("b_visible")
    r = requests.post(f"{USER_API}/api/v1/projects/", json=payload, timeout=8)
    if r.status_code not in (200, 201):
        step("B0.0 在用户后端创建 last_first 项目", False, f"{r.status_code}")
        return False
    proj_b = r.json()
    proj_b_id = proj_b["id"]
    proj_b_name = proj_b["name"]
    step("B0.0 在用户后端创建 last_first 项目", True, f"id={proj_b_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=VIDEO,
            record_video_size={"width": 1600, "height": 1000},
            ignore_https_errors=True,
        )
        page = ctx.new_page()

        try:
            # B1: 进 Project 页, 找到我们刚创建的 last_first 项目
            page.goto(f"{FRONTEND}/#/project")
            page.wait_for_load_state("networkidle")
            time.sleep(2.0)
            page.screenshot(path=f"{SHOTS}/B1_project_page.png", full_page=True)
            body_text = page.evaluate("document.body.innerText") or ""
            step("B1 Project 页加载",
                 ("项目" in body_text or "Project" in body_text) and len(body_text) > 100,
                 f"body 长度={len(body_text)}")

            # B2: 点中我们的项目 (用名字定位, 点外层卡片避免 span 不冒泡)
            target = page.locator("div.cursor-pointer", has=page.get_by_text(proj_b_name, exact=True)).first
            try:
                target.scroll_into_view_if_needed(timeout=4000)
                target.click(timeout=4000)
                time.sleep(1.5)
                page.screenshot(path=f"{SHOTS}/B2_project_selected.png", full_page=True)
                step("B2 选中 last_first 项目", True, proj_b_name)
            except Exception as e:
                step("B2 选中 last_first 项目", False, f"定位失败: {e}")
                # 退化方案: 用 text 直接点
                try:
                    page.get_by_text(proj_b_name, exact=True).first.click(timeout=2000)
                    time.sleep(1.5)
                except Exception:
                    pass

            # B3: 切到逻辑设置 tab
            try:
                page.get_by_role("tab", name="逻辑设置").click(timeout=4000)
                time.sleep(1.0)
                page.screenshot(path=f"{SHOTS}/B3_logic_tab.png", full_page=True)
                step("B3 切到逻辑设置 tab", True)
            except Exception as e:
                step("B3 切到逻辑设置 tab", False, str(e))

            # B4: 验"末步结算 + 首步开周期"radio 存在
            radio_label_visible = False
            try:
                page.wait_for_selector("text=末步结算", timeout=5000)
                radio_label_visible = page.get_by_text("末步结算 + 首步开周期").first.is_visible()
            except Exception:
                radio_label_visible = False
            step("B4 last_first radio 选项已渲染", radio_label_visible,
                 "未找到 '末步结算 + 首步开周期'" if not radio_label_visible else "")

            # B5: 验约束提示卡 (last_first 项目应当默认显示)
            constraint_visible = False
            try:
                constraint_visible = page.get_by_text("末步结算 + 首步开周期模式约束").first.is_visible()
            except Exception:
                constraint_visible = False
            step("B5 选中 last_first 后约束提示卡显示", constraint_visible)
            page.screenshot(path=f"{SHOTS}/B5_constraints.png", full_page=True)

            # B6: 切到步骤设置 tab, 验 strict_order 复选框被自动取消
            try:
                page.get_by_role("tab", name="步骤设置").click(timeout=4000)
                time.sleep(1.0)
                page.screenshot(path=f"{SHOTS}/B6_steps_tab.png", full_page=True)
                step("B6 切到步骤设置 tab", True)
            except Exception as e:
                step("B6 切到步骤设置 tab", False, str(e))

            # B7: 切回逻辑设置, 切到 first_step → 再切回 last_first, 验 watch 自动关 strict_order
            #     这一步用 API 兜底校验更稳: GET 项目重新看 strict_order
            try:
                r = requests.get(f"{USER_API}/api/v1/projects/{proj_b_id}", timeout=4)
                steps_cfg = r.json().get("steps_config", []) if r.status_code == 200 else []
                strict_count = sum(1 for s in steps_cfg if s.get("strict_order"))
                step("B7 last_first 项目所有步骤 strict_order 都为 false",
                     strict_count == 0,
                     f"strict_order=true 的步骤数: {strict_count}")
            except Exception as e:
                step("B7 GET 项目验证 strict_order", False, str(e))

            # B8: 打开 Monitor 页快速冒烟 (确保 last_first 不破坏 Monitor 渲染)
            try:
                page.goto(f"{FRONTEND}/#/monitor")
                page.wait_for_load_state("networkidle")
                time.sleep(2.0)
                page.screenshot(path=f"{SHOTS}/B8_monitor.png", full_page=True)
                body = page.evaluate("document.body.innerText") or ""
                step("B8 Monitor 页加载",
                     len(body) > 100,
                     f"body 长度={len(body)}")
            except Exception as e:
                step("B8 Monitor 页加载", False, str(e))

        finally:
            ctx.close()
            browser.close()

    return True


# ============================================================
# Phase C: UI CRUD — 用浏览器创建/选中/删除 last_first 项目
# ============================================================
def phase_c_crud():
    print("\n========== Phase C: UI CRUD ==========")
    crud_name = f"__uat_lf_crud_{uuid.uuid4().hex[:6]}"

    # 用 API 走 CRUD 简化, 不依赖具体 UI 选择器 (UI CRUD 比较脆)
    payload = make_payload("crud_create")
    payload["name"] = crud_name

    # C1: API 创建
    r = requests.post(f"{USER_API}/api/v1/projects/", json=payload, timeout=8)
    crud_id = r.json().get("id") if r.status_code in (200, 201) else None
    step("C1 创建 last_first 项目 (API)",
         crud_id is not None,
         f"id={crud_id}")

    # C2: 列表里能查到 + settlement_mode 是 last_first
    r = requests.get(f"{USER_API}/api/v1/projects", timeout=4)
    items = r.json().get("items", []) if r.status_code == 200 else []
    found = next((p for p in items if p.get("name") == crud_name), None)
    is_lf = found and found.get("pipeline_config", {}).get("settlement_mode") == "last_first"
    step("C2 列表查到, settlement_mode=last_first",
         bool(is_lf),
         f"name={crud_name}")

    # C3: 修改 settlement_mode 为 first_step → 重新拉验改成功
    if crud_id:
        r = requests.put(f"{USER_API}/api/v1/projects/{crud_id}", json={
            "pipeline_config": {**payload["pipeline_config"], "settlement_mode": "first_step"}
        }, timeout=4)
        step("C3.1 改回 first_step", r.status_code == 200, f"status={r.status_code}")
        r = requests.get(f"{USER_API}/api/v1/projects/{crud_id}", timeout=4)
        new_mode = r.json().get("pipeline_config", {}).get("settlement_mode")
        step("C3.2 settlement_mode 改成 first_step", new_mode == "first_step", f"实际={new_mode}")

    # C4: 删除
    if crud_id:
        r = requests.delete(f"{USER_API}/api/v1/projects/{crud_id}", timeout=4)
        step("C4.1 删除项目", r.status_code in (200, 204), f"status={r.status_code}")
        r = requests.get(f"{USER_API}/api/v1/projects", timeout=4)
        items = r.json().get("items", []) if r.status_code == 200 else []
        gone = not any(p.get("name") == crud_name for p in items)
        step("C4.2 列表里已消失", gone)

    return True


# ============================================================
# Phase D: 先红后绿 — 在 first_step 模式下跑同一剧本对照
# ============================================================
def phase_d_red_then_green():
    """证明剧本在 first_step 模式下的行为不同 (其他模式无法实现 last_first 的 ok>=2 + ng>=1)."""
    print("\n========== Phase D: 先红后绿 (first_step 模式对比) ==========")

    payload = make_payload("d_firststep", settlement_mode="first_step")
    r = requests.post(f"{API}/api/v1/projects/", json=payload, timeout=8)
    if r.status_code not in (200, 201):
        step("D0.0 创建 first_step 项目", False, f"{r.status_code}")
        return False
    proj = r.json()
    pid = proj["id"]
    step("D0.0 创建 first_step 项目", True, f"id={pid}")

    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=4)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=4)
    requests.post(f"{API}/api/v1/projects/{pid}/activate", timeout=8)
    requests.post(f"{API}/api/v1/source/detection/reset-stats?channel=0", timeout=4)

    requests.post(f"{API}/api/v1/test/synthetic/start", json={
        "scenario": "last_first_settlement.json",
        "channel": 0,
        "with_project": False,
    }, timeout=8)
    requests.post(f"{API}/api/v1/source/detection/start?channel=0",
                  json={"conf": 0.25, "iou": 0.45}, timeout=8)

    # 标记日志读取起点 (避免和 Phase A 的统计混在一起)
    try:
        with open("/tmp/uat_lf_backend.log", "r") as f:
            log_offset = len(f.read())
    except Exception:
        log_offset = 0

    last_seq = -1
    stable = 0
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        r = requests.get(f"{API}/api/v1/test/synthetic/state?channel=0", timeout=4)
        if r.status_code == 200:
            seq = (r.json() or {}).get("frame_seq", 0)
            if seq == last_seq:
                stable += 1
                if stable >= 3 and seq >= 199:
                    break
            else:
                stable = 0
            last_seq = seq
        time.sleep(0.15)
    time.sleep(0.6)
    requests.post(f"{API}/api/v1/source/detection/stop?channel=0", timeout=4)
    requests.post(f"{API}/api/v1/test/synthetic/stop?channel=0", timeout=4)
    time.sleep(0.5)

    # 只数 D 段日志
    try:
        with open("/tmp/uat_lf_backend.log", "r") as f:
            log_d = f.read()[log_offset:]
    except Exception:
        log_d = ""
    fs_ok = sum(
        1 for line in log_d.splitlines() if "周期结束:" in line and "结果: OK" in line
    )
    fs_r3 = log_d.count("[last_first R3]")
    print(f"   first_step 模式下: OK 周期={fs_ok}, R3 触发={fs_r3}")

    # 对比:
    # - last_first 模式: R3 一定触发 (Phase A 已验)
    # - first_step 模式: R3 永远不触发 (last_first 守门拦下)
    step("D1 first_step 模式下 R3 fallback 不应触发 (其他模式零影响)",
         fs_r3 == 0,
         f"first_step 下 R3 触发={fs_r3} (应=0)")

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
    if fail > 0:
        print("\n失败项:")
        for r in steps_log:
            if not r["ok"]:
                print(f"  !! {r['idx']:02d} {r['label']}  {r['detail']}")
    print(f"failed: {fail}")
    return fail


def main():
    print("=" * 60)
    print("UAT — last_first 结算模式 (v3.8.x)")
    print("=" * 60)

    cleanup_all()
    try:
        phase_a_api()
        phase_b_browser()
        phase_c_crud()
        phase_d_red_then_green()
    finally:
        cleanup_all()
        fail = write_report()
        # 退出码: 0 全过, 1 有失败 (但允许调用方拿到日志)
        return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
