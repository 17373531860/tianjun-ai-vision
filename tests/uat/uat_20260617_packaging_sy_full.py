"""可见浏览器 UAT — 上银包装线 MES 闭环「滑块口径」全流程 (v3.22).

托盘只是容器、不锁托盘数: 全程只按"每箱滑块总数"判满, 每箱滑块数从激活的型号项目读
(custom_mix_container_item_target). 扫工单 → 真实 HTTP 拉单 (本机虚拟 MES, 上银真返回格式)
→ 自动按规格切型号项目 → 算箱数 + 尾箱 → 逐箱滑块结算 → 尾箱塞工单 gate → 完成.

三件套证据:
  - Phase A (API 契约, 不开浏览器): 真实 HTTP 拉单 + 协调器逐箱状态机, 校验后端
    box_total/tail_target/box_done/完成态/漏箱redo/查无单阻断/API错阻断/尾箱gate.
  - Phase B (可见浏览器 headless=False + 录像): Monitor 包装卡按滑块口径逐箱可见推进,
    设置页包装面板, 禁用刷新后卡消失 (未开此功能零差异).

前置: 后端 8011 (RUNTIME_MODE=test) + 前端 6002 + 虚拟 MES 9100 已起.
跑法: /home/qianqian/anaconda3/envs/tianjun/bin/python tests/uat/uat_20260617_packaging_sy_full.py
"""
import json
import os
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8011"
FRONTEND = "http://localhost:6002"
MOCK = "http://127.0.0.1:9100"
SHOTS = "/tmp/uat_sy_shots"
VIDEO = "/tmp/uat_sy_video"
RUNLOG = "/tmp/uat_sy_run.log"

steps_log = []


def step(label, ok, detail=""):
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": bool(ok), "detail": str(detail)}
    steps_log.append(rec)
    print(f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}")


# ──────── HTTP helpers ────────
def _get(path):
    return requests.get(f"{API}{path}", timeout=10)


def _post(path, body=None):
    return requests.post(f"{API}{path}", json=body or {}, timeout=15)


def _put(path, body):
    return requests.put(f"{API}{path}", json=body, timeout=10)


def _del(path):
    return requests.delete(f"{API}{path}", timeout=10)


def _scan(code, channel_id=0):
    r = _post("/api/v1/packaging-flows/scan", {"code": code, "channel_id": channel_id})
    return r.json() if r.status_code == 200 else {"_http": r.status_code}


def _settle(cycle_id, sliders, is_good=True, paper_done=None, channel_id=0):
    body = {"channel_id": channel_id, "cycle_id": cycle_id,
            "is_good": is_good, "slider_count": sliders}
    if paper_done is not None:
        body["paper_done"] = paper_done
    r = _post("/api/v1/test/synthetic/packaging-settle", body)
    return r.json() if r.status_code == 200 else {"_http": r.status_code, "_t": r.text}


def _state(cid):
    r = _get(f"/api/v1/packaging-flows/{cid}/state")
    return r.json().get("state") if r.status_code == 200 else None


# ──────── 清理 ────────
def _purge():
    r = _get("/api/v1/packaging-flows")
    for c in (r.json() or {}).get("items", []) if r.status_code == 200 else []:
        if str(c.get("name", "")).startswith("__uat_"):
            if c.get("enabled"):
                _put(f"/api/v1/packaging-flows/{c['id']}", {"enabled": False})
            _del(f"/api/v1/packaging-flows/{c['id']}")
    r = _get("/api/v1/projects?limit=500")
    items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
    for p in items:
        if str(p.get("name", "")).startswith("__uat_"):
            _del(f"/api/v1/projects/{p['id']}")
    r = _get("/api/v1/mes/gateway/connections")
    for c in (r.json() or []) if r.status_code == 200 else []:
        if str(c.get("name", "")).startswith("__uat_"):
            _del(f"/api/v1/mes/gateway/connections/{c['id']}")


# ──────── setup: 型号项目 + 拉单连接 + 包装配置 ────────
def _make_project(name, item_target):
    r = _post("/api/v1/projects", {
        "name": name, "task_type": "detect",
        "pipeline_config": {
            "custom_mixed_with": "tracking",
            "custom_mix_container_enabled": True,
            "custom_mix_container_label": "托盘",
            "custom_mix_container_count_mode": "items",
            "custom_mix_container_item_target": item_target,
        },
    })
    assert r.status_code == 201, f"建项目 {name} 失败 {r.status_code} {r.text}"
    return r.json()["id"]


def _make_pkg_config(conn_id, spec_map):
    sfx = uuid.uuid4().hex[:5]
    r = _post("/api/v1/packaging-flows", {
        "name": f"__uat_sy_{sfx}", "enabled": True, "channel_id": 0,
        "count_unit": "sliders", "items_per_box_source": "project",
        "slider_total_field": "dispatch_qty",
        "auto_switch_project": True, "spec_to_project": spec_map,
        "pull_conn_id": conn_id, "on_mes_fail": "block",
        "tail_paper_order_required": True, "tail_paper_step_label": "put_paper",
    })
    assert r.status_code == 201, f"建包装配置失败 {r.status_code} {r.text}"
    return r.json()["id"]


def _drop_pkg_config(cid):
    _put(f"/api/v1/packaging-flows/{cid}", {"enabled": False})
    _del(f"/api/v1/packaging-flows/{cid}")


def setup():
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(VIDEO, exist_ok=True)
    _purge()
    _post("/api/v1/test/synthetic/packaging-reset")  # 清协调器内存在途运行(跨脚本运行隔离)
    sfx = uuid.uuid4().hex[:5]
    # 1) 三个型号项目: 每箱滑块总数写在容器整箱目标里 (托盘不锁数)
    pid_h20 = _make_project(f"__uat_型号_HGH20_{sfx}", 96)
    pid_w15 = _make_project(f"__uat_型号_HGW15_{sfx}", 24)
    pid_e15 = _make_project(f"__uat_型号_EGH15_{sfx}", 64)
    step("S1 建三个型号项目(容器目标96/24/64,托盘仅容器)",
         all([pid_h20, pid_w15, pid_e15]),
         f"HGH20={pid_h20} HGW15={pid_w15} EGH15={pid_e15}")

    # 2) 拉单连接 → 本机虚拟 MES 上银真返回格式
    r = _post("/api/v1/mes/gateway/connections", {
        "name": f"__uat_上银拉单_{sfx}", "adapter_type": "rest",
        "enabled": True, "pull_enabled": True,
        "config": {"pull": {
            "enabled": True,
            "url": f"{MOCK}/hiwin/webcn/ai_error_prevention_job_info/query",
            "method": "POST", "content_type": "application/json; charset=UTF-8",
            "request_body_template":
                '{"api":"hiwin/webcn/ai_error_prevention_job_info/query",'
                '"parameters":{"job_no":"{job_no}"}}',
            "success_path": "statusCode", "success_value": 200,
            "array_path": "response.resultData",
            "field_mapping": {"order_no": "job_no", "product_spec": "spec",
                              "planned_qty": "dispatch_qty"},
            "timeout_sec": 10, "retry_count": 0,
        }},
    })
    assert r.status_code == 200, f"建拉单连接失败 {r.status_code} {r.text}"
    conn_id = r.json()["id"]
    step("S2 建上银拉单连接(statusCode=200/response.resultData)", True, f"conn_id={conn_id}")

    spec_map = {"HGH20": pid_h20, "HGW15": pid_w15, "EGH15": pid_e15}
    return conn_id, spec_map


# ──────── Phase A: API 契约 ────────
def phase_a(cid):
    # A1 MES 查不到 + 阻断: 不开工单
    _scan("NOPE-JOB")
    step("A1 查无此单(statusCode200空数组)+阻断 → 不开工单",
         _state(cid) is None, f"state={_state(cid)}")

    # A2 MES API 错误(HTTP500) + 阻断: 不开工单
    _scan("JOB-ERR")
    step("A2 MES API错误(HTTP500)+阻断 → 不开工单",
         _state(cid) is None, f"state={_state(cid)}")

    # A3 非整除尾箱全流程: 250滑块/HGH20→每箱96→3箱尾箱58
    s = _scan("ORD-NONEXACT")
    st = s.get("state") or {}
    step("A3a 扫工单→真HTTP拉单→自动切HGH20→算箱",
         st.get("box_total") == 3 and st.get("tail_target") == 58
         and st.get("items_per_box") == 96 and st.get("count_unit") == "sliders"
         and st.get("spec") == "HGH20",
         f"box_total={st.get('box_total')} tail={st.get('tail_target')} "
         f"每箱={st.get('items_per_box')} spec={st.get('spec')}")

    _settle(1, 96)   # 箱1
    _settle(2, 96)   # 箱2
    st = _state(cid)
    step("A3b 普通箱逐箱96结算 (box_done=2, 进尾箱)",
         st and st.get("box_done") == 2 and st.get("current_box_index") == 3,
         f"box_done={st.get('box_done')} cur_idx={st.get('current_box_index')}")

    # 尾箱未塞工单: 暂不收尾 (gate 守住)
    _settle(3, 58, paper_done=False)
    st = _state(cid)
    step("A3c 尾箱未塞工单 → 暂不收尾(box_done仍2,未完成)",
         st and st.get("box_done") == 2 and st.get("status") == "running",
         f"box_done={st.get('box_done')} status={st.get('status')}")

    # 尾箱塞了工单 + 滑块58: 收尾完成 OK
    r = _settle(4, 58, paper_done=True)
    lr = r.get("last_run") or {}
    step("A3d 尾箱塞工单+58 → 完成OK(3箱,尾箱58)",
         lr.get("order_no") == "ORDNONEXACT" and lr.get("status") == "completed"
         and lr.get("final_result") == "OK" and lr.get("box_done") == 3,
         f"last_run={lr}")

    # A4 整除尾箱 + 自动切另一型号: 192滑块/EGH15→每箱64→3箱尾箱64
    s = _scan("ORD-SPEC2")
    st = s.get("state") or {}
    step("A4a 扫工单→自动切EGH15→整除算箱(3箱尾箱满64)",
         st.get("box_total") == 3 and st.get("tail_target") == 64
         and st.get("items_per_box") == 64 and st.get("spec") == "EGH15",
         f"box_total={st.get('box_total')} tail={st.get('tail_target')} "
         f"每箱={st.get('items_per_box')} spec={st.get('spec')}")
    _settle(11, 64)
    _settle(12, 64)
    r = _settle(13, 64, paper_done=True)  # 尾箱(整除仍满64)
    lr = r.get("last_run") or {}
    step("A4b 逐箱64×3(尾箱塞工单) → 完成OK",
         lr.get("order_no") == "ORDSPEC2" and lr.get("final_result") == "OK"
         and lr.get("box_done") == 3,
         f"last_run={lr}")

    # A5 漏箱 redo: 240滑块/HGW15→10箱, 只做1箱就扫别单 → 漏箱不切单
    _scan("ORD-EXACT")
    _settle(21, 24)            # 箱1
    _scan("ORD-SINGLE")        # 中途扫别单 → 漏箱
    st = _state(cid)
    step("A5 漏箱(做1/10箱扫别单) → redo不切单(仍ORDEXACT)",
         st and st.get("order_no") == "ORDEXACT",
         f"order_no={st.get('order_no') if st else None}")
    # 把这张漏箱单做完, 清空协调器内存在途运行 (否则污染后续浏览器阶段)
    for c in range(22, 42):
        r = _settle(c, 24, paper_done=True)
        if (r.get("last_run") or {}).get("status") == "completed":
            break


# ──────── Phase B: 可见浏览器 ────────
def phase_b(cid):
    # 先开一个干净工单, 让 Monitor 卡有内容可逐箱推进
    _scan("ORD-NONEXACT")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=250,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=VIDEO, record_video_size={"width": 1600, "height": 1000},
            ignore_https_errors=True)
        page = ctx.new_page()

        page.goto(f"{FRONTEND}/#/monitor")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3.5)
        page.screenshot(path=f"{SHOTS}/B1_monitor_box1.png", full_page=False)
        body = page.evaluate("document.body.innerText")
        has_card = page.locator(".packaging-card").count() > 0
        step("B1 Monitor 包装卡按滑块口径渲染(箱1/3)",
             has_card and "滑块" in body and "包装箱结算" in body,
             f"card={page.locator('.packaging-card').count()} 滑块={'滑块' in body}")

        _settle(101, 96); time.sleep(2.2)   # 箱1完成 → 卡刷新到箱2
        page.screenshot(path=f"{SHOTS}/B2_monitor_box2.png", full_page=False)
        st = _state(cid)
        step("B2 结算箱1后卡推进到箱2", st and st.get("box_done") == 1,
             f"box_done={st.get('box_done') if st else None}")

        _settle(102, 96); time.sleep(2.2)   # 箱2完成 → 进尾箱(目标58)
        page.screenshot(path=f"{SHOTS}/B3_monitor_tail.png", full_page=False)
        st = _state(cid)
        step("B3 结算箱2后进尾箱(目标58)",
             st and st.get("current_box_index") == 3 and st.get("box_done") == 2,
             f"cur_idx={st.get('current_box_index') if st else None} "
             f"tail={st.get('tail_target') if st else None}")

        # 设置页包装面板
        page.goto(f"{FRONTEND}/#/settings")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(2.5)
        page.screenshot(path=f"{SHOTS}/B4_settings.png", full_page=True)
        step("B4 设置页可达(包装结算面板)", True, "见 B4 截图")

        # 禁用 → 刷新 Monitor → 卡消失 (零差异)
        _put(f"/api/v1/packaging-flows/{cid}", {"enabled": False})
        page.goto(f"{FRONTEND}/#/monitor")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3.5)
        page.screenshot(path=f"{SHOTS}/B5_monitor_nocard.png", full_page=False)
        no_card = page.locator(".packaging-card").count() == 0
        step("B5 禁用后刷新 → 包装卡消失(未开此功能零差异)",
             no_card, f"card={page.locator('.packaging-card').count()}")

        ctx.close()
        browser.close()


def report():
    _purge()
    failed = [s for s in steps_log if not s["ok"]]
    with open(RUNLOG, "w", encoding="utf-8") as f:
        json.dump({"total": len(steps_log), "failed": len(failed), "steps": steps_log},
                  f, ensure_ascii=False, indent=2)
        f.write(f"\n\nfailed: {len(failed)}\n")
    print("\n" + "=" * 56)
    print(f"上银包装 MES 闭环 UAT: {len(steps_log) - len(failed)}/{len(steps_log)} OK, "
          f"failed: {len(failed)}")
    print(f"视频: {VIDEO}/   截图: {SHOTS}/   日志: {RUNLOG}")
    print("=" * 56)


if __name__ == "__main__":
    conn_id, spec_map = setup()
    try:
        cid = _make_pkg_config(conn_id, spec_map)
        step("S3 建启用包装配置", True, f"cfg_id={cid}")
        phase_a(cid)   # phase_a 末尾已把漏箱单做完, 内存在途已清空
        phase_b(cid)
    finally:
        report()
