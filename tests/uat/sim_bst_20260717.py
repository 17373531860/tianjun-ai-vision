# -*- coding: utf-8 -*-
"""百斯特全链路仿真 (2026-07-17): 真视频 + 新模型 + 秤读数脚本 + 达梦模拟。

链路: 284a 现场视频(文件源) → best_bst_20260716_v2.pt 实时推理 → 三标签喂
流水线称重状态机; 模拟秤按视频时间轴播放毛重脚本(响应 T/Z 指令) → 外设管线
→ 称重引擎; 离秤结算 → 待收尾队列 → 视觉「加钢脚水泥」FIFO 结案 → MES 网关
数据库直写(SQLite 代 达梦) 落中间表。

7 个周期脚本: 4 合格(净重 3.5) + 1 缺料(3.10) + 1 超量(3.90) + 1 皮重超范围报警。
"""
import json
import os
import sqlite3
import sys
import time

import requests

API = "http://localhost:8001/api/v1"
CH = 0
PROJECT_ID = 31
DEVICE_ID = 2
CONN_ID = 1
MODEL_FILE = "/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/file/2026-07/best_bst_20260716_v2.pt"
VIDEO_FILE = "/home/qianqian/文档/xwechat_files/wxid_9j6tgdyqgpon22_030a/msg/video/2026-07/284a510a5730f274a17d53e45b936cca.mp4"
DM_DB = "/tmp/bst_dm_sim.db"
RUN_SECONDS = 265

TARE = 1.20          # XP-70 皮重 (合法范围 1.1-1.3)
STD = 3.5            # 钢帽水泥标准量 ±0.25


# ==================== 秤读数脚本 (按 284a 视频时间轴对齐) ====================
def build_mock_script():
    """[{"weight": 毛重, "hold": 秒}] — 与视频里 7 个周期的动作时刻对齐。

    视频动作时刻(推理时间线实测): 工件上秤 14.1/52.6/84.5/120.7/164/201;
    加钢脚水泥 63.8/98.6/133.6/177.6/212.4/243.6 (各自结案上一件)。
    """
    seg = []
    t = [0.0]

    def hold(w, dur):
        seg.append({"weight": round(w, 3), "hold": round(dur, 2)})
        t[0] += dur

    def ramp(frm, to, steps=4, step_hold=0.8):
        for i in range(1, steps + 1):
            hold(frm + (to - frm) * i / steps, step_hold)

    def cycle(place_at, fill_at, net, depart_at, next_place):
        """一件: 上秤(皮重) → 去皮 → 装料爬升 → 稳定 → 离秤。"""
        if place_at > t[0]:
            hold(0.0, place_at - t[0])
        hold(TARE, fill_at - t[0])            # 上秤等自动去皮(引擎发T, 模拟秤归零)
        gross = TARE + net
        ramp(TARE, gross)                     # 装料爬升 (每档0.8s, 不会误稳定)
        hold(gross, depart_at - t[0])         # 到量稳定 → 引擎冻结净重
        hold(0.0, max(0.4, next_place - t[0]))  # 离秤(读数=-皮重) → 结算 → Z

    cycle(14.5, 17.0, 3.50, 50.5, 53.0)     # c1 OK
    cycle(53.0, 55.5, 3.50, 82.5, 85.0)     # c2 OK
    cycle(85.0, 87.5, 3.50, 118.5, 121.0)   # c3 OK
    cycle(121.0, 123.5, 3.10, 162.0, 164.5) # c4 缺料 (3.10 < 3.25)
    cycle(164.5, 167.0, 3.90, 199.5, 201.5) # c5 超量 (3.90 > 3.75)
    cycle(201.5, 204.0, 3.50, 230.0, 232.5) # c6 OK
    hold(6.80, 8.0)                          # c7 皮重超范围(6.8 >> 1.3) → 报警
    hold(0.0, 30.0)                          # 收尾静默
    return seg


# ==================== 工具 ====================
def api(method, path, **kw):
    r = requests.request(method, f"{API}{path}", timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except ValueError:
        return {}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ==================== Phase 1: 环境装配 ====================
def setup():
    # 1. 上传新模型 (重名跳过)
    models = api("GET", "/models").get("models") or api("GET", "/models").get("items") or []
    model_path = None
    for m in models:
        if m.get("version") == "20260716v2":
            model_path = m["file_path"]
            log(f"模型已存在 id={m['id']}")
            break
    if not model_path:
        with open(MODEL_FILE, "rb") as f:
            m = api("POST", "/models/upload",
                    files={"file": ("best_bst_20260716_v2.pt", f)},
                    data={"name": "百斯特三标签", "version": "20260716v2",
                          "framework": "PyTorch",
                          "description": "工件上秤/加水泥/加钢脚水泥 yolo12s imgsz1280"})
        model_path = m["file_path"]
        log(f"模型上传完成 id={m['id']} labels={m.get('labels')}")

    # 2. 上传视频
    with open(VIDEO_FILE, "rb") as f:
        v = api("POST", "/source/video/upload",
                files={"file": ("bst_284a_现场.mp4", f, "video/mp4")})
    video_path = v["file_path"]
    log(f"视频上传完成 {video_path}")

    # 3. 项目 31: 绑模拟秤 + 三班制 + 显示重量
    p = api("GET", f"/projects/{PROJECT_ID}")
    w = p["pipeline_config"]["weighing"]
    w["weight_device_id"] = DEVICE_ID
    w["show_monitor_weights"] = True
    data_cfg = p.get("data_config") or {}
    data_cfg["shift_split_enabled"] = True
    data_cfg["shifts"] = [
        {"name": "早班", "start": "08:00"},
        {"name": "中班", "start": "16:00"},
        {"name": "夜班", "start": "00:00"},
    ]
    api("PUT", f"/projects/{PROJECT_ID}",
        json={"pipeline_config": p["pipeline_config"], "data_config": data_cfg})
    api("POST", f"/projects/{PROJECT_ID}/activate")
    log("项目31 已更新并激活: 绑定模拟秤 + 三班制(拆分开)")

    # 4. 达梦模拟: SQLite 中间表 + 网关连接指到它
    if os.path.exists(DM_DB):
        os.remove(DM_DB)
    db = sqlite3.connect(DM_DB)
    db.execute("""CREATE TABLE T_WEIGH_RECORD (
        ID INTEGER PRIMARY KEY AUTOINCREMENT,
        SN TEXT, MODEL_NAME TEXT, OPERATOR TEXT, MATERIAL TEXT,
        NET_WEIGHT REAL, TARE_WEIGHT REAL, VERDICT TEXT,
        FINALIZE_STATUS TEXT, SHIFT TEXT, CREATE_TIME TEXT)""")
    db.commit(); db.close()
    api("PUT", f"/mes/gateway/connections/{CONN_ID}", json={
        "enabled": True,
        "push_events": ["weighing_product_done"],
        "config": {
            "db_type": "sqlite", "database": DM_DB, "table": "T_WEIGH_RECORD",
            "template": {
                "SN": "{sn}", "MODEL_NAME": "{model}", "OPERATOR": "{operator}",
                "MATERIAL": "{material}", "NET_WEIGHT": "{net}",
                "TARE_WEIGHT": "{tare}", "VERDICT": "{verdict}",
                "FINALIZE_STATUS": "{finalize_status}", "SHIFT": "{shift}",
                "CREATE_TIME": "{timestamp}",
            },
        },
    })
    log(f"达梦模拟中间表就绪 {DM_DB} + 网关连接{CONN_ID}已启用")

    # 5. 模拟秤: 先停, 换上视频对齐脚本
    api("PUT", f"/external-devices/{DEVICE_ID}", json={
        "enabled": False,
        "stable_enabled": False,
        "protocol_config": {
            "mock_script": build_mock_script(),
            "loop": False, "poll_interval": 0.2, "decimals": 3,
        },
    })
    log("模拟秤脚本已装载 (视频时间轴对齐, 7 周期)")
    return model_path, video_path


# ==================== Phase 2: 跑仿真 ====================
def run(model_path, video_path):
    p = api("GET", f"/projects/{PROJECT_ID}")
    api("POST", f"/source/detection/set-project?channel={CH}", json={
        "project_id": PROJECT_ID, "name": p["name"], "task_type": p["task_type"],
        "logic_mode": p.get("logic_mode"), "steps_config": p.get("steps_config") or [],
        "pipeline_config": p.get("pipeline_config") or {},
        "events_config": p.get("events_config") or [],
        "counters_config": p.get("counters_config") or [],
        "data_config": p.get("data_config") or {},
    })
    api("POST", "/weighing/reset", json={"channel_id": CH})
    api("POST", "/weighing/context",
        json={"channel_id": CH, "operator": "仿真-张师傅", "model_name": "XP-70"})
    before = len(api("GET", f"/weighing/records?channel={CH}&limit=500").get("records", []))

    api("POST", f"/source/video/start?channel={CH}",
        json={"file_path": video_path, "speed": 1.0})
    api("POST", f"/source/detection/start?channel={CH}",
        json={"model_path": model_path, "conf": 0.4, "iou": 0.45,
              "session_name": "百斯特仿真20260717"})
    t0 = time.time()
    api("PUT", f"/external-devices/{DEVICE_ID}", json={"enabled": True})
    log("=== 仿真开始: 视频+推理+模拟秤 已同步启动 ===")

    last_phase = None
    while time.time() - t0 < RUN_SECONDS:
        time.sleep(5)
        try:
            s = api("GET", f"/weighing/state?channel={CH}")
            snap = s.get(str(CH)) or s.get(CH) or s
            phase = snap.get("pipeline_phase")
            line = (f"t={time.time()-t0:5.0f}s phase={phase} "
                    f"eff={snap.get('effective_weight')} tare={snap.get('tare_weight')} "
                    f"net={snap.get('last_stable_net')} "
                    f"pending={len(snap.get('pending') or [])} "
                    f"settled={snap.get('settled_count')}")
            if phase != last_phase or int(time.time() - t0) % 20 < 5:
                log(line)
            last_phase = phase
        except Exception as e:
            log(f"轮询异常: {e}")

    api("POST", f"/source/detection/stop?channel={CH}")
    api("POST", f"/source/video/stop?channel={CH}")
    api("PUT", f"/external-devices/{DEVICE_ID}", json={"enabled": False})
    log("=== 仿真结束, 已停检测/视频/模拟秤 ===")
    return before


# ==================== Phase 3: 验证 ====================
def verify(before):
    ok = []

    def check(name, cond, detail=""):
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        ok.append(bool(cond))

    recs = api("GET", f"/weighing/records?channel={CH}&limit=500").get("records", [])
    new = recs[before:]
    verdicts = [r.get("verdict") for r in new]
    check("离秤结算 6 件", len(new) == 6, f"实际 {len(new)}: {verdicts}")
    check("4 件合格", verdicts.count("ok") == 4, str(verdicts))
    check("1 件缺料", verdicts.count("shortage") == 1,
          str([(r['verdict'], r['net']) for r in new]))
    check("1 件超量", verdicts.count("over") == 1)
    tares = [r.get("tare") for r in new if r.get("tare")]
    check("皮重全部≈1.20", all(abs(t - TARE) < 0.05 for t in tares), str(tares))

    s = api("GET", f"/weighing/state?channel={CH}")
    snap = s.get(str(CH)) or s.get(CH) or s
    check("待收尾队列已清空", not snap.get("pending"), str(snap.get("pending")))

    db = sqlite3.connect(DM_DB)
    rows = db.execute(
        "SELECT SN, MODEL_NAME, NET_WEIGHT, VERDICT, FINALIZE_STATUS, SHIFT, OPERATOR "
        "FROM T_WEIGH_RECORD ORDER BY ID").fetchall()
    db.close()
    print("  达梦(模拟)中间表行:")
    for r in rows:
        print("   ", r)
    check("达梦中间表 6 行", len(rows) == 6, f"实际 {len(rows)}")
    check("全部视觉收尾确认", all(r[4] == "confirmed" for r in rows),
          str([r[4] for r in rows]))
    check("班次字段已填", all(r[5] for r in rows), str([r[5] for r in rows]))
    check("型号/操作员透传", all(r[1] == "XP-70" and r[6] == "仿真-张师傅" for r in rows))

    print(f"\n===== 验证 {sum(ok)}/{len(ok)} PASS =====")
    return all(ok)


if __name__ == "__main__":
    model_path, video_path = setup()
    if "--setup-only" in sys.argv:
        sys.exit(0)
    before = run(model_path, video_path)
    time.sleep(3)
    sys.exit(0 if verify(before) else 1)
