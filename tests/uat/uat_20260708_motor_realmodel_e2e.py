# -*- coding: utf-8 -*-
"""可见浏览器 UAT: 电机装配全流程 — 真实模型 × 真实视频 整链路验收。

现场叙事:
  客户训好的 YOLO 模型(工件/盖罩/打螺丝/力矩/标记/工件横放 6 类)第一次进软件。
  操作员上传模型 → 配「锚点跟随 × 多轮次」拆分项目(锚点=工件, 盖罩切轮,
  前后罩共享 4 个螺丝区域; 力矩/标记走整幅区域拆分只吃轮次前缀) →
  用客户原始产线视频回放 → 监控页看客户全流程 13 步逐一计数:
  前罩螺丝1-4 → 前罩力矩 → 前罩标记 → 后罩螺丝1-4 → 后罩力矩 → 后罩标记
  → 工件横放, 轮次角标切换、对角顺序全对(违序 0 次)、工件1 正常 OK 结算。

前置: main 后端 8001 (tianjun env) + 前端 6001 已起, GPU 可用。
项目 __uat_ 前缀, 收尾删除并恢复原激活项目; 上传的模型保留(后续真项目要用)。
"""
from __future__ import annotations

import os
import sys
import time
import uuid

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _common import UatRun, launch_browser, filter_console_errors  # noqa: E402
from _motor_flow_config import FLOW_STEPS, SCREW_STEPS, build_flow_config  # noqa: E402

API = "http://127.0.0.1:8001/api/v1"
FRONT = "http://localhost:6001"
CH = 0

MODEL_PT = "/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/file/2026-07/best(3).pt"
VIDEO = ("/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/video/"
         "2026-07/1593386d5368d7afda8e0b4f47b15587.mp4")

# 项目配置(13 步全流程)唯一事实源: _motor_flow_config.py
# 参数标定依据见该模块 docstring(真实模型逐帧轨迹 _motor_trace_95_255.json)
VSTEPS = list(FLOW_STEPS)

run = UatRun("motor_realmodel_e2e")
pid = None
prev_active = None


def _project_payload():
    return build_flow_config(speedup=1.0)


def _results():
    # GPU 推理满载/换页卡顿时后端偶发响应 >15s, 单次超时不应打断整个 UAT
    for attempt in range(5):
        try:
            return requests.get(f"{API}/source/detection/results?channel={CH}",
                                timeout=15).json()
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(2)


# ---- 后台遥测: 0.5s 采样轮次/计步/计数器, 落 telemetry.jsonl 供事后精确复盘 ----
import json as _json
import threading

_telemetry_stop = threading.Event()


def _telemetry_loop():
    path = f"{run.dir}/telemetry.jsonl"
    last = None
    with open(path, "a") as f:
        while not _telemetry_stop.is_set():
            try:
                b = requests.get(
                    f"{API}/source/detection/results?channel={CH}",
                    timeout=5).json()
                vp = requests.get(f"{API}/source/video/info?channel={CH}",
                                  timeout=5).json()
                rec = {
                    "wall": round(time.time(), 1),
                    "video_t": round(vp.get("current_time", -1), 1)
                    if vp.get("status") == "success" else -1,
                    "rounds": (b.get("label_split_rounds") or {}).get("打螺丝"),
                    "sc": {k: v for k, v in (b.get("step_counts") or {}).items()
                           if v},
                    "ctr": {k: v for k, v in (b.get("counters") or {}).items()
                            if v},
                    "cycle": b.get("current_cycle_steps"),
                }
                key = _json.dumps(rec, ensure_ascii=False, sort_keys=True,
                                  default=str)
                key_nowall = key.replace(f'"wall": {rec["wall"]}', "") \
                                .replace(f'"video_t": {rec["video_t"]}', "")
                if key_nowall != last:
                    f.write(key + "\n")
                    f.flush()
                    last = key_nowall
            except Exception:
                pass
            _telemetry_stop.wait(0.5)


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
    if pid:
        try:
            requests.delete(f"{API}/projects/{pid}", timeout=10)
        except Exception:
            pass


try:
    # ============ 0. 健康检查 ============
    ok_be = requests.get(f"{API}/projects", timeout=20).status_code == 200
    ok_fe = requests.get(FRONT, timeout=10).status_code == 200
    run.step("00 前后端健康(8001/6001)", ok_be and ok_fe)

    # ============ 1. 上传模型(重复上传自动跳过) ============
    mlist = requests.get(f"{API}/models", timeout=10).json().get("items") or []
    existed = next((m for m in mlist if m.get("name") == "电机装配打螺丝"), None)
    if existed:
        model_path = existed["file_path"]
        run.step("01 模型仓库已有同名模型, 复用", True, model_path)
    else:
        with open(MODEL_PT, "rb") as f:
            r = requests.post(f"{API}/models/upload",
                              files={"file": ("motor_screw_best.pt", f)},
                              data={"name": "电机装配打螺丝", "version": "v1",
                                    "framework": "PyTorch",
                                    "description": "电机装配线 6 类: 工件/盖罩/打螺丝/力矩/标记/工件横放"},
                              timeout=120)
        r.raise_for_status()
        body = r.json()
        model_path = body["file_path"]
        labels = body.get("labels") or []
        run.step("01 模型上传入库 + 标签自动解析",
                 set(labels) == {"工件", "盖罩", "打螺丝", "力矩", "标记", "工件横放"},
                 f"labels={labels}")

    # ============ 2. 建项目(锚点×多轮次×违序事件) ============
    cur = requests.get(f"{API}/projects", timeout=10).json().get("items") or []
    prev_active = next((p["id"] for p in cur if p.get("is_active")), None)
    pname = f"__uat_motor_{uuid.uuid4().hex[:5]}"
    r = requests.post(f"{API}/projects", json={
        "name": pname, "task_type": "detection", "logic_mode": "sequential",
    }, timeout=10)
    r.raise_for_status()
    pid = r.json()["id"]
    requests.put(f"{API}/projects/{pid}", json=_project_payload(),
                 timeout=10).raise_for_status()
    requests.post(f"{API}/projects/{pid}/activate", timeout=15).raise_for_status()
    run.step("02 项目创建+激活(锚点跟随×2轮×严格顺序×违序事件)", True, f"id={pid}")

    # 下发到通道
    detail = requests.get(f"{API}/projects/{pid}", timeout=10).json()
    requests.post(f"{API}/source/detection/set-project?channel={CH}", json={
        "project_id": pid, "name": detail["name"], "task_type": detail["task_type"],
        "logic_mode": detail["logic_mode"], "steps_config": detail["steps_config"],
        "pipeline_config": detail["pipeline_config"],
        "events_config": detail.get("events_config") or [],
        "counters_config": detail.get("counters_config") or [],
    }, timeout=10).raise_for_status()

    # ============ 3. 起视频源 + 加载模型 ============
    # ⚠️ 起播点是本 UAT 的命门(第一次跑就栽在这): 视频 44s 有一次「盖罩」,
    # 但那是**上一个工件的后罩**(完整工件流程 105s→195s)。从它前面起播,
    # 引擎会把后罩当第1轮前罩, 轮次错位一拍 → 违序/NG 全是连锁误报。
    # 正确起播点 = 100s: 上一工件已横放下线(94s), 下一工件前罩 105s 才盖 —
    # 引擎看到的第一次盖罩就是真前罩。
    # 另: Monitor 页挂载会重新下发项目配置 → 引擎重建(轮次归零), 所以先 0.1x
    # 慢速钉住画面, 等模型+页面全就位再放行原速。
    requests.post(f"{API}/source/video/start?channel={CH}",
                  json={"file_path": VIDEO, "speed": 0.1},
                  timeout=15).raise_for_status()
    requests.post(f"{API}/source/video/progress?channel={CH}",
                  json={"progress": 98.0 / 650.0}, timeout=10)
    r = requests.post(f"{API}/source/detection/start?channel={CH}",
                      json={"conf": 0.4, "iou": 0.45, "model_path": model_path},
                      timeout=120)
    r.raise_for_status()
    deadline = time.time() + 90
    loaded = False
    while time.time() < deadline:
        st = requests.get(f"{API}/source/status?channel={CH}", timeout=5).json()
        if st.get("model_loaded") and st.get("is_detecting"):
            loaded = True
            break
        time.sleep(1)
    run.step("03 视频源+真实模型检测启动(GPU)", loaded)

    # UAT_RECORD=0: 本机内存吃紧(swap 打满)时录像会把推理线程挤进换页地狱
    # (实测单帧延迟 12~16s, 步骤整段漏看) — 关录像只留截图, 绿了再补录像档
    record_dir = run.video_dir if os.environ.get("UAT_RECORD", "1") != "0" else None
    with sync_playwright() as p:
        browser, ctx, page, cerrs = launch_browser(p, record_video_dir=record_dir)
        page.goto(f"{FRONT}/#/monitor", wait_until="domcontentloaded")
        time.sleep(8)   # Monitor 挂载 + set-project 重建引擎在此完成

        # 引擎就位确认: 轮次运行态已透出(round=0 尚未开始), 再放行视频
        b0 = _results()
        rnd0 = (b0.get("label_split_rounds") or {}).get("打螺丝") or {}
        run.step("03b 引擎就位(轮次运行态 round=0, 视频停在工件1前罩之前)",
                 rnd0.get("round") == 0, f"rounds={rnd0}")
        # 计数器/计步跨检测会话在通道上持久 — 放行视频前清零, 否则上一轮 UAT
        # 的违序/NG 残值会污染本轮断言
        requests.post(f"{API}/source/detection/reset-stats?channel={CH}",
                      timeout=10)
        # 开结算调试通道: 步骤被时长门/阈值/ROI 拒绝的原因全部进调试日志,
        # 跑完拉 /debug/logs 落进证据目录, 失败可精确复盘
        requests.put(f"{API}/debug/flags",
                     json={"flags": {"backend.settlement": True}}, timeout=10)
        requests.post(f"{API}/debug/logs/clear", timeout=10)
        threading.Thread(target=_telemetry_loop, daemon=True).start()
        requests.post(f"{API}/source/video/progress?channel={CH}",
                      json={"progress": 100.0 / 650.0}, timeout=10)
        requests.post(f"{API}/source/video/speed?channel={CH}",
                      json={"speed": 1.0}, timeout=10)

        # ============ 4. 第1轮: 前罩 4 颗对角 → 力矩 → 标记 ============
        # 工件1 时间线(逐帧轨迹标定): 前罩盖 105s → 前罩螺丝 112~122s →
        # 前罩力矩 126~138s → 前罩标记 140~147s → 后罩盖 151s →
        # 后罩螺丝 158~168s → 后罩力矩 172~183s → 后罩标记 185~194s →
        # 工件横放 192~215s → 工件2 前罩盖 227s → 首颗螺丝 235s(触发结算)
        seen_round1 = seen_round2 = seen_in_position = False
        front_screws_body = None
        deadline = time.time() + 75          # 视频 100→122s 原速 + 缓冲
        while time.time() < deadline:
            b = _results()
            sc = b.get("step_counts") or {}
            rnd = (b.get("label_split_rounds") or {}).get("打螺丝") or {}
            if rnd.get("round") == 1:
                seen_round1 = True
            if (b.get("placement_guide") or {}).get("in_position"):
                seen_in_position = True
            if all(sc.get(f"前罩螺丝{i}", 0) >= 1 for i in range(1, 5)):
                front_screws_body = b
                break
            time.sleep(1)
        sc = (front_screws_body or b).get("step_counts") or {}
        run.step("04 第1轮(前罩)4颗虚拟螺丝全部计数",
                 front_screws_body is not None,
                 f"counts={ {k: v for k, v in sc.items() if '螺丝' in k} }")
        run.step("05 轮次角标到过第1轮(盖罩触发)", seen_round1)
        run.shot(page, "01_round1_front_screws")

        # 前罩力矩(126~138s) + 前罩标记(140~147s)
        front_tm_body = None
        deadline = time.time() + 60          # 视频 122→148s 原速 + 缓冲
        while time.time() < deadline:
            b = _results()
            sc = b.get("step_counts") or {}
            if sc.get("前罩力矩", 0) >= 1 and sc.get("前罩标记", 0) >= 1:
                front_tm_body = b
                break
            time.sleep(1)
        sc = (front_tm_body or b).get("step_counts") or {}
        run.step("06 前罩力矩+前罩标记 计数(整幅区域拆分吃轮次前缀)",
                 front_tm_body is not None,
                 f"力矩={sc.get('前罩力矩')} 标记={sc.get('前罩标记')}")
        run.shot(page, "02_round1_torque_mark")

        # ============ 5. 第2轮: 翻面后罩 4 颗 → 力矩 → 标记 → 横放 ============
        back_screws_body = None
        deadline = time.time() + 70          # 视频 148→168s 原速 + 缓冲
        while time.time() < deadline:
            b = _results()
            sc = b.get("step_counts") or {}
            rnd = (b.get("label_split_rounds") or {}).get("打螺丝") or {}
            if rnd.get("round") == 2:
                seen_round2 = True
            if all(sc.get(f"后罩螺丝{i}", 0) >= 1 for i in range(1, 5)):
                back_screws_body = b
                break
            time.sleep(1)
        sc = (back_screws_body or b).get("step_counts") or {}
        run.step("07 第2轮(后罩)4颗虚拟螺丝全部计数",
                 back_screws_body is not None,
                 f"counts={ {k: v for k, v in sc.items() if '螺丝' in k} }")
        run.step("08 轮次角标到过第2轮", seen_round2)
        run.shot(page, "03_round2_back_screws")

        # 后罩力矩(172~183s) + 后罩标记(185~194s) + 工件横放(192~215s)
        # 注: 194s 横放中的收尾标记与后罩标记同轮次同名 — accept_once 去重,
        # 只计 1 次且不允许误报违序(这正是全流程比纯螺丝难的地方)
        tail_body = None
        deadline = time.time() + 90          # 视频 168→216s 原速 + 缓冲
        while time.time() < deadline:
            b = _results()
            sc = b.get("step_counts") or {}
            if (sc.get("后罩力矩", 0) >= 1 and sc.get("后罩标记", 0) >= 1
                    and sc.get("工件横放", 0) >= 1):
                tail_body = b
                break
            time.sleep(1)
        sc = (tail_body or b).get("step_counts") or {}
        run.step("09 后罩力矩+后罩标记+工件横放 计数(13步全流程收尾)",
                 tail_body is not None,
                 f"力矩={sc.get('后罩力矩')} 标记={sc.get('后罩标记')} "
                 f"横放={sc.get('工件横放')}")
        run.step("10 收尾标记/横放二段出现未重复计数(accept_once 去重)",
                 sc.get("后罩标记", 0) == 1 and sc.get("工件横放", 0) == 1)
        run.shot(page, "04_round2_tail_steps")

        # ============ 6. 工件2 开工 → 工件1 OK 结算 + 轮次回绕 ============
        settled_body = None
        deadline = time.time() + 60          # 视频 216→235s 原速 + 缓冲
        while time.time() < deadline:
            b = _results()
            ctr = b.get("counters") or {}
            sc = b.get("step_counts") or {}
            if ctr.get("合格总数", 0) >= 1 and sc.get("前罩螺丝1", 0) >= 2:
                settled_body = b
                break
            time.sleep(1)
        fin = settled_body or b
        ctr = fin.get("counters") or {}
        run.step("11 工件1 OK 结算: 13步齐全(工件2 首颗触发 first_step 结算)",
                 settled_body is not None, f"counters={ctr}")
        rnd = (fin.get("label_split_rounds") or {}).get("打螺丝") or {}
        run.step("12 工件2 盖前罩后轮次回绕到第1轮",
                 rnd.get("round") == 1, f"rounds={rnd}")
        run.step("13 全流程顺序全对: 违序 0 次",
                 ctr.get("违序次数", 0) == 0, f"违序次数={ctr.get('违序次数')}")
        sc = fin.get("step_counts") or {}
        run.step("14 原始标签(打螺丝/力矩/标记)未直接计数(全部改写为虚拟步骤)",
                 sc.get("打螺丝", 0) == 0 and sc.get("力矩", 0) == 0
                 and sc.get("标记", 0) == 0)
        run.step("15 就位提示: 作业期间工件在引导框内(in_position 出现过)",
                 seen_in_position)
        run.shot(page, "05_wp1_settled_wp2_round1")

        # ============ 7. 监控页人眼证据 ============
        m_body = page.evaluate("document.body.innerText")
        run.step("16 监控页显示 13 个全流程步骤卡片",
                 all(v in m_body for v in VSTEPS))
        run.step("17 监控页显示自定义计数器(违序次数)", "违序次数" in m_body)
        run.shot(page, "06_monitor_full_evidence")

        real = filter_console_errors(cerrs)
        run.step("18 前端 console 无逻辑报错", not real, f"真报错={real[:3]}")
        ctx.close()
        browser.close()
finally:
    _telemetry_stop.set()
    time.sleep(0.6)
    try:
        logs = requests.get(f"{API}/debug/logs?limit=2000", timeout=10).json()
        with open(f"{run.dir}/backend_debug.log", "w") as f:
            for e in logs.get("logs") or logs.get("items") or []:
                f.write(_json.dumps(e, ensure_ascii=False) + "\n")
        requests.put(f"{API}/debug/flags",
                     json={"flags": {"backend.settlement": False}}, timeout=10)
    except Exception:
        pass
    _cleanup()

raise SystemExit(run.finish())
