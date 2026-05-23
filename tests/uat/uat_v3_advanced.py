"""V3 进阶 UAT — 三块"硬"功能契约验证

Phase D: 导出真生成 docx / xlsx，解开来核字段是不是真的渲染进去了
Phase E: Cluster 主从（双后端 8011/8021），副机上报 → 主机汇总成 box_complete
Phase F: Tracking-style 动态 ROI（同 label 但 bbox 中心位置随帧移动；step_roi 只覆盖中段帧）
         → step_count 应该恰好等于"中段帧数 ≥ min_frames 段"
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

API_M = "http://127.0.0.1:8011"   # master
API_S = "http://127.0.0.1:8021"   # slave (Phase E)

OUT_DIR = Path("/tmp/uat_v3_out")
RUN_LOG = Path("/tmp/uat_v3_run.log")

steps_log: list[dict] = []


def step(label: str, ok: bool, detail: str = "") -> None:
    rec = {"idx": len(steps_log) + 1, "label": label, "ok": ok, "detail": detail}
    steps_log.append(rec)
    print(f"[{'OK' if ok else '!!'}] {rec['idx']:02d}. {label}  {detail}")


def reset_dirs() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 共享工具
# ============================================================
def stop_all_synth(api: str = API_M, channel: int = 0) -> None:
    for path in (
        f"/api/v1/source/detection/stop?channel={channel}",
        f"/api/v1/test/synthetic/stop?channel={channel}",
        f"/api/v1/source/detection/reset-stats?channel={channel}",
    ):
        try:
            requests.post(f"{api}{path}", timeout=4)
        except Exception:
            pass
    time.sleep(0.3)


def cleanup_uat_projects(api: str = API_M) -> None:
    try:
        r = requests.get(f"{api}/api/v1/projects", timeout=5)
        if r.status_code == 200:
            data = r.json()
            items = data.get("items") if isinstance(data, dict) and "items" in data else data
            for p in items or []:
                if (p.get("name") or "").startswith("__uat_"):
                    requests.delete(f"{api}/api/v1/projects/{p['id']}", timeout=5)
    except Exception:
        pass


def cleanup_uat_templates(api: str = API_M) -> None:
    try:
        r = requests.get(f"{api}/api/v1/export/templates", timeout=5)
        if r.status_code == 200:
            data = r.json()
            items = data.get("items") if isinstance(data, dict) and "items" in data else data
            for t in items or []:
                if (t.get("name") or "").startswith("__uat_"):
                    requests.delete(f"{api}/api/v1/export/templates/{t['id']}", timeout=5)
    except Exception:
        pass


def full_project_payload(name: str) -> dict:
    return {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "pipeline_config": {
            "settlement_mode": "last_step",
            "sequence_order": [
                {"step_id": 1}, {"step_id": 2}, {"step_id": 3},
            ],
        },
        "steps_config": [
            {"id": 1, "label": "step_a", "enabled": True, "threshold": 50,
             "min_frames": 1, "color": "#22c55e"},
            {"id": 2, "label": "step_b", "enabled": True, "threshold": 50,
             "min_frames": 1, "color": "#3b82f6"},
            {"id": 3, "label": "step_c", "enabled": True, "threshold": 50,
             "min_frames": 1, "color": "#f97316"},
        ],
        "events_config": [
            {"id": 1, "name": "合格(OK)", "color": "#10b981",
             "actions": [{"counter_name": "合格总数", "delta": 1},
                         {"counter_name": "总产量", "delta": 1}],
             "show_notification": False, "toast_id": "ok"},
            {"id": 2, "name": "不良(NG)", "color": "#ef4444",
             "actions": [{"counter_name": "不良总数", "delta": 1},
                         {"counter_name": "总产量", "delta": 1}],
             "show_notification": False, "toast_id": "ng"},
        ],
        "counters_config": [
            {"name": "合格总数", "value": 0},
            {"name": "不良总数", "value": 0},
            {"name": "总产量", "value": 0},
        ],
        "alarm_config": {"enabled": False, "triggers": {}},
        "detection_config": {},
        "data_config": {},
    }


def push_full_config(api: str, pid: int, payload: dict, channel: int = 0) -> None:
    body = {
        "project_id": pid,
        "name": payload["name"],
        "task_type": payload.get("task_type", "detection"),
        "logic_mode": payload.get("logic_mode", "sequential"),
        "steps_config": payload.get("steps_config", []),
        "pipeline_config": payload.get("pipeline_config", {}),
        "events_config": payload.get("events_config", []),
        "counters_config": payload.get("counters_config", []),
        "data_config": payload.get("data_config", {}),
    }
    r = requests.post(f"{api}/api/v1/source/detection/set-project?channel={channel}",
                      json=body, timeout=10)
    assert r.status_code == 200, f"set-project: {r.status_code} {r.text[:300]}"


def create_activate_push(api: str, payload: dict) -> int:
    r = requests.post(f"{api}/api/v1/projects", json=payload, timeout=10)
    assert r.status_code in (200, 201), f"create: {r.status_code} {r.text[:300]}"
    pid = r.json()["id"]
    r = requests.post(f"{api}/api/v1/projects/{pid}/activate", timeout=10)
    assert r.status_code == 200, f"activate: {r.status_code} {r.text[:300]}"
    push_full_config(api, pid, payload)
    return pid


def drive_synthetic_one_ok_cycle(api: str = API_M) -> None:
    """跑一遍 ok_sequential_cycle 让 DB 落一条 cycle，便于后面导出取真数据。"""
    # 先把残留 session 强制结掉
    requests.post(f"{api}/api/v1/source/detection/reset-stats?channel=0", timeout=5)
    time.sleep(0.3)
    r = requests.post(f"{api}/api/v1/test/synthetic/start",
                      json={"scenario": "ok_sequential_cycle.json", "channel": 0,
                            "with_project": False, "logic_mode": "sequential"},
                      timeout=10)
    assert r.status_code == 200, f"synth start: {r.status_code} {r.text[:300]}"
    r = requests.post(f"{api}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=10)
    assert r.status_code == 200
    # 要求计数器 >=1 同时 step_counts 三步都到位（避免上次 mgr 残留误导）
    deadline = time.time() + 15.0
    while time.time() < deadline:
        rr = requests.get(f"{api}/api/v1/source/detection/results?channel=0",
                          timeout=4).json()
        ctr = rr.get("counters") or {}
        sc = rr.get("step_counts") or {}
        if (ctr.get("合格总数") or 0) >= 1 and all(
            sc.get(k, 0) >= 1 for k in ("step_a", "step_b", "step_c")
        ):
            break
        time.sleep(0.2)
    # 主动 end_session 把 cycle 落库
    requests.post(f"{api}/api/v1/source/detection/stop?channel=0", timeout=5)
    requests.post(f"{api}/api/v1/test/synthetic/stop?channel=0", timeout=5)
    requests.post(f"{api}/api/v1/source/detection/reset-stats?channel=0", timeout=5)
    time.sleep(0.5)


def latest_session_cycle_ids(api: str = API_M) -> tuple[int | None, int | None]:
    """取最近 5 个 session，挑第一个有 cycle 的。"""
    r = requests.get(f"{api}/api/v1/data/sessions?limit=5", timeout=5)
    if r.status_code != 200:
        return None, None
    data = r.json()
    items = data.get("items") if isinstance(data, dict) and "items" in data else data
    if not items:
        return None, None
    for it in items:
        sid = it.get("id")
        if not sid:
            continue
        rc = requests.get(f"{api}/api/v1/data/sessions/{sid}/cycles?limit=1",
                          timeout=5)
        if rc.status_code != 200:
            continue
        cdata = rc.json()
        citems = cdata.get("items") if isinstance(cdata, dict) and "items" in cdata else cdata
        if citems:
            return sid, citems[0].get("id")
    return items[0].get("id") if items else None, None


# ============================================================
# Phase D: Export 渲染真文件
# ============================================================
def phase_d():
    print("\n========== Phase D: 导出真生成 docx / xlsx，验渲染内容 ==========\n")

    # 先跑一个 OK cycle 让 DB 有真数据
    pname = f"__uat_v3_d_{uuid.uuid4().hex[:6]}"
    pid = create_activate_push(API_M, full_project_payload(pname))
    drive_synthetic_one_ok_cycle(API_M)
    sid, cid = latest_session_cycle_ids(API_M)
    step("D0 准备：跑一个 OK cycle 落库", sid is not None and cid is not None,
         f"session={sid} cycle={cid}")
    if not (sid and cid):
        return pid

    # ---- D1: txt 渲染基线（最快验 jinja 上下文 OK）----
    # 注意：steps 在顶层，不是 cycle.steps
    tpl = (
        "[TXT_REPORT]\n"
        "session_id={{ session.id }}\n"
        "cycle_id={{ cycle.id }}\n"
        "is_good={{ cycle.is_good }}\n"
        "now_ymdhms={{ now_ymdhms }}\n"
        "app_name={{ app.name }}\n"
        "step_count={{ steps | length }}\n"
        "labels={% for s in steps %}{{ s.label }}|{% endfor %}\n"
    )
    body = {
        "cycle_id": cid,
        "template_content": tpl,
        "filename_template": "uat_d_{{ cycle.id }}.txt",
        "fmt": "txt",
        "encoding": "utf-8",
    }
    r = requests.post(f"{API_M}/api/v1/export/render", json=body, timeout=15)
    txt_ok = (r.status_code == 200)
    txt_path = OUT_DIR / "D1.txt"
    if txt_ok:
        txt_path.write_bytes(r.content)
    txt = (r.text if txt_ok else "")
    cond = (txt_ok and "[TXT_REPORT]" in txt
            and f"cycle_id={cid}" in txt
            and "is_good=True" in txt
            and re.search(r"now_ymdhms=\d{4}", txt) is not None
            and "step_a" in txt and "step_b" in txt and "step_c" in txt)
    step("D1 [TXT] /export/render 真出文件 + 字段渲染对", cond,
         f"status={r.status_code} len={len(r.content)} preview={txt[:120]!r}")

    # ---- D2: docx 渲染（路线 A，自动样式）----
    docx_tpl = (
        "天军 UAT 测试报告\n"
        "Session ID: {{ session.id }}\n"
        "Cycle ID: {{ cycle.id }}\n"
        "Result: {{ '合格 OK' if cycle.is_good else '不良 NG' }}\n"
        "Steps: {% for s in steps %}{{ s.label }}({{ s.duration }}s) {% endfor %}\n"
        "Generated at: {{ now_ymdhms }}\n"
    )
    body["template_content"] = docx_tpl
    body["filename_template"] = "uat_d_{{ cycle.id }}.docx"
    body["fmt"] = "docx"
    r = requests.post(f"{API_M}/api/v1/export/render", json=body, timeout=20)
    docx_path = OUT_DIR / "D2.docx"
    docx_ok = False
    docx_text = ""
    if r.status_code == 200:
        docx_path.write_bytes(r.content)
        # 解开 docx (本质是 zip) 读 word/document.xml
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                xml = zf.read("word/document.xml").decode("utf-8")
            # 拼出全部 <w:t> 文本
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            root = ET.fromstring(xml)
            docx_text = "\n".join(t.text or "" for t in root.iter(f"{{{ns['w']}}}t"))
            docx_ok = (
                "天军 UAT 测试报告" in docx_text
                and f"Cycle ID: {cid}" in docx_text
                and ("合格 OK" in docx_text)
                and "step_a" in docx_text and "step_c" in docx_text
            )
        except Exception as e:
            docx_text = f"<unzip-err: {e}>"
    step("D2 [DOCX] 真生成 .docx，解 zip 读 document.xml 字段都在",
         docx_ok,
         f"status={r.status_code} bytes={len(r.content)} extracted_chars={len(docx_text)}")

    # ---- D3: xlsx 渲染 ----
    xlsx_tpl = (
        "Session,Cycle,Result,StepCount\n"
        "{{ session.id }},{{ cycle.id }},{{ '合格' if cycle.is_good else '不良' }},"
        "{{ cycle.steps | length }}\n"
    )
    body["template_content"] = xlsx_tpl
    body["filename_template"] = "uat_d_{{ cycle.id }}.xlsx"
    body["fmt"] = "xlsx"
    r = requests.post(f"{API_M}/api/v1/export/render", json=body, timeout=20)
    xlsx_path = OUT_DIR / "D3.xlsx"
    xlsx_ok = False
    xlsx_dump = ""
    if r.status_code == 200:
        xlsx_path.write_bytes(r.content)
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                # 共享字符串表 + sheet1
                sst = zf.read("xl/sharedStrings.xml").decode("utf-8") if "xl/sharedStrings.xml" in zf.namelist() else ""
                sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
            xlsx_dump = sst + "\n--SHEET--\n" + sheet
            # session.id / cycle.id 应作为 inline 数字或字符串出现
            xlsx_ok = (
                str(sid) in xlsx_dump and str(cid) in xlsx_dump
                and ("合格" in xlsx_dump or "Result" in xlsx_dump)
                and "Session" in xlsx_dump
            )
        except Exception as e:
            xlsx_dump = f"<unzip-err: {e}>"
    step("D3 [XLSX] 真生成 .xlsx，sst+sheet 含 session/cycle id 与表头",
         xlsx_ok,
         f"status={r.status_code} bytes={len(r.content)} dump_len={len(xlsx_dump)}")

    # ---- D4: 用 file 命令 sanity 检查 MIME ----
    file_outputs = {}
    for p in (txt_path, docx_path, xlsx_path):
        if p.exists():
            try:
                file_outputs[p.name] = subprocess.check_output(
                    ["file", "-b", str(p)], timeout=4
                ).decode().strip()
            except Exception as e:
                file_outputs[p.name] = f"err: {e}"
    expected_keywords = {
        "D1.txt": "ASCII text" in file_outputs.get("D1.txt", "")
                   or "UTF-8 Unicode" in file_outputs.get("D1.txt", "")
                   or "Unicode" in file_outputs.get("D1.txt", ""),
        "D2.docx": "Microsoft Word" in file_outputs.get("D2.docx", "")
                    or "Zip archive" in file_outputs.get("D2.docx", "")
                    or "OpenXML" in file_outputs.get("D2.docx", ""),
        "D3.xlsx": "Microsoft Excel" in file_outputs.get("D3.xlsx", "")
                    or "Zip archive" in file_outputs.get("D3.xlsx", "")
                    or "OpenXML" in file_outputs.get("D3.xlsx", ""),
    }
    step("D4 file(1) 识别三种格式都没坏",
         all(expected_keywords.values()),
         f"types={file_outputs}")

    return pid


# ============================================================
# Phase E: Cluster 主从（双后端）
# ============================================================
def phase_e():
    print("\n========== Phase E: Cluster 主从（双后端 8011 主 / 8021 副）==========\n")

    # 等 slave 8021 可访问
    slave_ready = False
    for _ in range(40):
        try:
            r = requests.get(f"{API_S}/api/v1/system/version", timeout=2)
            if r.status_code == 200:
                slave_ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not slave_ready:
        step("E0 副机 8021 启动可达", False, "wait timeout")
        return
    step("E0 副机 8021 启动可达", True, "")

    # ---- E1: 主机配置成 master，期望 2 个工位 (st_main, st_slave) ----
    r = requests.put(f"{API_M}/api/v1/cluster/config", json={
        "role": "master",
        "station_id": "st_main",
        "expected_stations": ["st_main", "st_slave"],
        "sync_mode": "all_match",
        "timeout_sec": 30,
        "timeout_push": False,
        "enabled": True,
    }, timeout=8)
    step("E1 主机配置 role=master, expected=[st_main, st_slave]",
         r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")

    # ---- E2: 副机配置成 slave，指向主机 ----
    r = requests.put(f"{API_S}/api/v1/cluster/config", json={
        "role": "slave",
        "master_url": API_M,
        "station_id": "st_slave",
        "enabled": True,
    }, timeout=8)
    step("E2 副机配置 role=slave, master_url=8011",
         r.status_code == 200, f"status={r.status_code}")

    # ---- E3: 副机心跳 → 主机 /slaves 能看到 ----
    r = requests.post(f"{API_M}/api/v1/cluster/heartbeat", json={
        "station_id": "st_slave",
        "port": 8021,
        "hostname": "uat-slave",
        "project": "__uat_e",
        "channel_count": 1,
        "detecting": False,
    }, timeout=8)
    hb_ok = (r.status_code == 200)
    time.sleep(0.5)
    r = requests.get(f"{API_M}/api/v1/cluster/slaves", timeout=5)
    slaves = (r.json().get("slaves") if r.status_code == 200 else []) or []
    seen = any(s.get("station_id") == "st_slave" for s in slaves)
    step("E3 副机心跳 → 主机 /cluster/slaves 列出 st_slave",
         hb_ok and seen, f"hb={r.status_code if not hb_ok else 'OK'} slaves={[s.get('station_id') for s in slaves]}")

    # ---- E4: 双工位上报同一个 box_serial → 主机汇总 → box_complete ----
    # 生产里副机的 cluster_collector 完成 cycle 后会调主机的 /cluster/report；
    # 这里直接对主机发两次上报（station_id 不同），等价于副机已经把数据推过来。
    box = f"BOX_UAT_{uuid.uuid4().hex[:6]}"
    r1 = requests.post(f"{API_M}/api/v1/cluster/report", json={
        "station_id": "st_main",
        "box_serial": box,
        "is_good": True,
        "event_name": "合格(OK)",
        "cycle_context": {
            "id": 1,
            "step_count": 3,
            "test_values": [1.23, 4.56, 7.89],
        },
    }, timeout=8)
    r2 = requests.post(f"{API_M}/api/v1/cluster/report", json={
        "station_id": "st_slave",
        "box_serial": box,
        "is_good": True,
        "event_name": "合格(OK)",
        "cycle_context": {
            "id": 2,
            "step_count": 3,
            "test_values": [9.99],
        },
    }, timeout=8)
    # 主从都上报后等主机聚合
    time.sleep(1.5)
    r = requests.get(f"{API_M}/api/v1/cluster/boxes/{box}", timeout=8)
    box_ok = False
    box_dump = {}
    if r.status_code == 200:
        box_dump = r.json()
        st_set = {s.get("station_id") for s in box_dump.get("stations") or []}
        summary = box_dump.get("summary") or {}
        box_ok = (
            {"st_main", "st_slave"}.issubset(st_set)
            and (summary.get("overall_result") == "OK"
                 or summary.get("status") in ("complete", "pushed", "ok", "good", "completed"))
        )
    step("E4 主+副工位都上报同一 box → 主机聚合 box_complete (overall_result=OK)",
         box_ok,
         f"r1={r1.status_code} r2={r2.status_code} stations={[s.get('station_id') for s in (box_dump.get('stations') or [])]} "
         f"summary={box_dump.get('summary')}")

    # ---- E5: 副机 NG 上报 → 主机汇总应判 NG ----
    box_ng = f"BOX_UAT_NG_{uuid.uuid4().hex[:6]}"
    requests.post(f"{API_M}/api/v1/cluster/report", json={
        "station_id": "st_main", "box_serial": box_ng, "is_good": True,
        "event_name": "合格(OK)", "cycle_context": {"id": 10},
    }, timeout=8)
    requests.post(f"{API_M}/api/v1/cluster/report", json={
        "station_id": "st_slave", "box_serial": box_ng, "is_good": False,
        "event_name": "不良(NG)", "cycle_context": {"id": 11},
    }, timeout=8)
    time.sleep(1.5)
    r = requests.get(f"{API_M}/api/v1/cluster/boxes/{box_ng}", timeout=8)
    ng_ok = False
    ng_dump = {}
    if r.status_code == 200:
        ng_dump = r.json()
        summary = ng_dump.get("summary") or {}
        ng_ok = (summary.get("overall_result") == "NG")
    step("E5 副机 NG 上报 → 主机聚合 overall_result=NG（任一 NG 整盒判 NG）",
         ng_ok, f"summary={ng_dump.get('summary')}")

    # 清理
    try:
        requests.delete(f"{API_M}/api/v1/cluster/boxes/{box}", timeout=5)
        requests.delete(f"{API_M}/api/v1/cluster/boxes/{box_ng}", timeout=5)
    except Exception:
        pass


# ============================================================
# Phase F: Tracking-style 动态 ROI
# ============================================================
def moving_target_scenario() -> dict:
    """同 label 但 bbox 中心在帧间移动；step_a 的 ROI 只覆盖 0.4-0.6 中段。

    timeline （60 fps）:
      frames 0-29  : bbox 中心 (0.10, 0.50) — 在 ROI 外（左侧）
      frames 30-89 : bbox 中心 (0.50, 0.50) — 在 ROI 内（中央）
      frames 90-119: bbox 中心 (0.90, 0.50) — 在 ROI 外（右侧）

    step_a 的 ROI = [(0.4,0.4),(0.6,0.4),(0.6,0.6),(0.4,0.6)] — 仅覆盖中央方块。

    期望:
      - step_counts.step_a == 1（只有中段帧序列被认定，构成 1 次步骤）
      - 而不是 0（不会全 ROI 外被吃掉）也不是 2（左/右段不应当成第 2 个步骤）
      - 验证证据：ng_step_cycle_counts 不应含 step_a（中段被认；左/右段不算独立步骤）
    """
    # 用动态 bbox：每 5 帧切一段，模拟"持续运动"
    timeline = []

    def seg(a: int, b: int, cx: float, cy: float):
        bw = bh = 0.06
        timeline.append({
            "from": a, "to": b,
            "detections": [{
                "label": "step_a", "confidence": 0.95,
                "bbox": [cx - bw / 2, cy - bh / 2, bw, bh],
            }]
        })

    # 0-29 ROI 外（左）
    for i in range(0, 30, 5):
        seg(i, i + 4, 0.10, 0.50)
    # 30-89 ROI 内（中）
    for i in range(30, 90, 5):
        seg(i, i + 4, 0.50, 0.50)
    # 90-119 ROI 外（右）
    for i in range(90, 120, 5):
        seg(i, i + 4, 0.90, 0.50)
    # 120 之后空白让 step disappear → 触发结算
    timeline.append({"from": 120, "to": 240, "detections": []})

    return {
        "name": "moving_target_with_central_roi",
        "fps": 60,
        "step_rois": {
            "step_a": [[0.40, 0.40], [0.60, 0.40], [0.60, 0.60], [0.40, 0.60]],
        },
        "timeline": timeline,
    }


def phase_f():
    print("\n========== Phase F: Tracking-style 动态 ROI（移动目标 + 中央 ROI）==========\n")

    # 单步骤项目
    pname = f"__uat_v3_f_{uuid.uuid4().hex[:6]}"
    payload = full_project_payload(pname)
    payload["steps_config"] = [
        {"id": 1, "label": "step_a", "enabled": True, "threshold": 50,
         "min_frames": 1, "color": "#22c55e"},
    ]
    payload["pipeline_config"]["sequence_order"] = [{"step_id": 1}]
    pid = create_activate_push(API_M, payload)

    # 跑剧本（with_project=True 让 step_rois 注入到 step_roi_polygons）
    stop_all_synth(API_M)
    r = requests.post(f"{API_M}/api/v1/test/synthetic/start", json={
        "scenario_json": moving_target_scenario(),
        "channel": 0, "with_project": True,
        "logic_mode": "sequential",
    }, timeout=10)
    assert r.status_code == 200, r.text[:300]
    r = requests.post(f"{API_M}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=10)
    assert r.status_code == 200

    deadline = time.time() + 8.0
    final = {}
    series = []
    while time.time() < deadline:
        rr = requests.get(f"{API_M}/api/v1/source/detection/results?channel=0",
                          timeout=4).json()
        final = rr
        sc = rr.get("step_counts") or {}
        series.append({"t": round(time.time(), 2), "step_a": sc.get("step_a", 0)})
        time.sleep(0.2)
    sc = final.get("step_counts") or {}
    a_count = sc.get("step_a", 0)
    # 严格期望 == 1：只有中央 ROI 内的连续帧形成 1 个 step
    step("F1 [动态 ROI] 移动目标穿过 ROI: 仅中段算 step_a，count == 1",
         a_count == 1,
         f"step_counts={sc} series_len={len(series)}")

    # F2: 把 step_roi 缩到一个根本不覆盖任何 bbox 中心的小角落 → count == 0
    stop_all_synth(API_M)
    no_match_scenario = moving_target_scenario()
    no_match_scenario["step_rois"]["step_a"] = [
        [0.01, 0.01], [0.05, 0.01], [0.05, 0.05], [0.01, 0.05]
    ]
    r = requests.post(f"{API_M}/api/v1/test/synthetic/start", json={
        "scenario_json": no_match_scenario,
        "channel": 0, "with_project": True,
        "logic_mode": "sequential",
    }, timeout=10)
    r = requests.post(f"{API_M}/api/v1/source/detection/start?channel=0",
                      json={"conf": 0.25, "iou": 0.45}, timeout=10)
    deadline = time.time() + 6.0
    final = {}
    while time.time() < deadline:
        final = requests.get(f"{API_M}/api/v1/source/detection/results?channel=0",
                              timeout=4).json()
        time.sleep(0.2)
    a_count2 = (final.get("step_counts") or {}).get("step_a", 0)
    step("F2 [动态 ROI] step_roi 缩到无效角落 → count == 0（同 detection 流，仅 ROI 改变）",
         a_count2 == 0, f"step_counts={final.get('step_counts') or {}}")

    stop_all_synth(API_M)
    try:
        requests.delete(f"{API_M}/api/v1/projects/{pid}", timeout=5)
    except Exception:
        pass


# ============================================================
# 入口
# ============================================================
def main() -> int:
    reset_dirs()
    pid_d = None
    try:
        pid_d = phase_d()
        phase_e()
        phase_f()
    finally:
        cleanup_uat_projects(API_M)
        cleanup_uat_projects(API_S)
        cleanup_uat_templates(API_M)
        if pid_d:
            try:
                requests.delete(f"{API_M}/api/v1/projects/{pid_d}", timeout=5)
            except Exception:
                pass

    failed = sum(1 for s in steps_log if not s["ok"])
    print(f"\n=== UAT V3 总结 ===")
    print(f"步骤数 {len(steps_log)} / 通过 {len(steps_log)-failed} / 失败 {failed}")
    print(f"导出产物: {OUT_DIR}")
    RUN_LOG.write_text(
        json.dumps({"steps": steps_log, "failed": failed},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
