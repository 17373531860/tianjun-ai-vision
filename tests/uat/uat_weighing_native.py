"""原生称重投料模式 — 运行中后端 UAT (HTTP 驱动, 虚拟喂数据)。

对照《萍乡百斯特项目 终验收标准》第六章, 在真实运行的后端上跑全流程:
  set-project 设 weighing 模式 → 引擎登记 ch0 →
  6.5 未选人员/型号扫码被拦 → 选了才能开始 →
  6.4 放件自动去皮 → 6.2 缺料/超量报警 → 6.1 逐件记录 → 6.3 视觉投错拦截。

虚拟喂重量走 /weighing/feed (等价真秤一帧读数); 真秤路径已单独验证可读(9600/8N1 ST,NT帧)。
"""
import requests

BASE = "http://localhost:8001/api/v1"
CH = 0
R = []  # (项, 期望, 实际, 通过?)


def check(name, expect, actual):
    ok = (expect == actual) if not callable(expect) else expect(actual)
    R.append((name, expect if not callable(expect) else "(谓词)", actual, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: 期望={expect if not callable(expect) else '谓词'} 实际={actual}")


def post(path, body):
    return requests.post(f"{BASE}{path}", json=body, timeout=5).json()


def get(path):
    return requests.get(f"{BASE}{path}", timeout=5).json()


def feed(w, n=4):
    snap = None
    for _ in range(n):
        snap = post("/weighing/feed", {"channel_id": CH, "weight": w}).get("snapshot")
    return snap


# ==================== 0. 设 weighing 模式 ====================
weighing_cfg = {
    "materials": ["钢帽水泥", "钢脚水泥"],
    "models": {
        "型号A": {
            "钢帽水泥": {"standard": 0.500, "low_tol": 0.020, "high_tol": 0.020},
            "钢脚水泥": {"standard": 0.300, "low_tol": 0.020, "high_tol": 0.020},
        }
    },
    "tare_mode": "auto_stable", "tare_trigger_weight": 0.05, "tare_settle_samples": 3,
    "stable_tol": 0.003, "stable_min_samples": 3, "measure_min_weight": 0.005,
    "require_operator": True, "require_model": True,
    "material_check": "sequence", "auto_zero_after_done": True,
    "alarm_event_shortage": 2, "alarm_event_over": 2,
    "alarm_event_wrong": 2, "alarm_event_precheck": 2,
}
setproj = post("/source/detection/set-project?channel=0", {
    "project_id": 9999, "name": "UAT称重", "task_type": "detection",
    "logic_mode": "weighing",
    "steps_config": [], "pipeline_config": {"weighing": weighing_cfg},
    "events_config": [
        {"id": 1, "name": "合格", "actions": []},
        {"id": 2, "name": "不良", "actions": []},
    ],
    "counters_config": [], "data_config": {},
})
print("set-project:", setproj.get("success", setproj))
st = get("/weighing/state?channel=0")
check("0 引擎登记ch0为称重模式", lambda s: s is not None and s.get("phase") == "idle", st)

# ==================== 6.5 前置校验 ====================
# 未选人员/型号直接扫码 → 拦截 (snapshot 仍 idle, 没绑 sn)
post("/weighing/reset", {"channel_id": CH})
scan1 = post("/weighing/scan", {"channel_id": CH, "serial_no": "SN-PRECHK"})
check("6.5 未选人员型号扫码被拦(不开始)",
      lambda s: s.get("snapshot", {}).get("phase") == "idle"
      and not s.get("snapshot", {}).get("product_sn"), scan1)

# 选人员+型号 → 再扫 → 开始
post("/weighing/context", {"channel_id": CH, "operator": "张三", "model_name": "型号A"})
scan2 = post("/weighing/scan", {"channel_id": CH, "serial_no": "SN-001"})
check("6.5 选了人员型号后可开始",
      lambda s: s.get("snapshot", {}).get("phase") == "await_tare", scan2)

# ==================== 6.4 自动去皮 ====================
snap = feed(0.20)  # 放空盆毛重 0.2 稳定 → 自动去皮
check("6.4 放件自动去皮进入投料", "filling", snap.get("phase"))

# ==================== 6.2 缺料 (料别0 钢帽) ====================
feed(0.45)  # 标准0.5 下容差0.02 → 0.45<0.48 缺料
recs = get("/weighing/records?channel=0&limit=10")["records"]
last = recs[-1] if recs else {}
check("6.2 缺料判定 shortage", "shortage", last.get("verdict"))
check("6.1 逐件记录净值=0.45", 0.45, last.get("net"))
check("6.1 记录带 sn/人员/型号", lambda r: r.get("sn") == "SN-001" and r.get("operator") == "张三"
      and r.get("model") == "型号A", last)

# ==================== 料别1 钢脚: 超量 → 完成 ====================
feed(0.15)  # 第二道料放件去皮
snap = feed(0.35)  # 标准0.3 上容差0.02 → 0.35>0.32 超量, 且两道料完成
recs = get("/weighing/records?channel=0&limit=10")["records"]
over_rec = recs[-1]
check("6.2 超量判定 over", "over", over_rec.get("verdict"))
check("6.1 整件两道料都记录(共≥2条)", lambda n: n >= 2, len(recs))
check("一件完成后置零复位(phase=done)", "done", snap.get("phase"))

# ==================== 6.3 视觉投错品类拦截 (虚拟标签) ====================
post("/weighing/context", {"channel_id": CH, "operator": "张三", "model_name": "型号A"})
# 临时切到 visual 料检模式重设项目
wcfg2 = dict(weighing_cfg); wcfg2["material_check"] = "visual"
post("/source/detection/set-project?channel=0", {
    "project_id": 9999, "name": "UAT称重", "task_type": "detection",
    "logic_mode": "weighing", "steps_config": [],
    "pipeline_config": {"weighing": wcfg2},
    "events_config": [{"id": 2, "name": "不良", "actions": []}],
    "counters_config": [], "data_config": {},
})
post("/weighing/context", {"channel_id": CH, "operator": "张三", "model_name": "型号A"})
post("/weighing/scan", {"channel_id": CH, "serial_no": "SN-WRONG"})
feed(0.20)  # 去皮
recs_before = len(get("/weighing/records?channel=0&limit=50")["records"])
post("/weighing/material-label", {"channel_id": CH, "label": "钢脚水泥"})  # 应投钢帽, 视觉识别成钢脚
snap = feed(0.50)  # 投料但料别错
recs_after = len(get("/weighing/records?channel=0&limit=50")["records"])
check("6.3 投错品类被拦截(不产生新记录)", recs_before, recs_after)
check("6.3 拦截后停在投料相位等纠正", "filling", snap.get("phase"))
# 纠正: 视觉识别成正确料别 → 放行记录
post("/weighing/material-label", {"channel_id": CH, "label": "钢帽水泥"})
feed(0.50)
recs_corr = len(get("/weighing/records?channel=0&limit=50")["records"])
check("6.3 纠正料别后正常记录", lambda n: n > recs_after, recs_corr)

# ==================== 汇总 ====================
print("\n==================== UAT 结果汇总 ====================")
passed = sum(1 for r in R if r[3])
for name, exp, act, ok in R:
    print(f"  [{'✓' if ok else '✗'}] {name}")
print(f"\n{passed}/{len(R)} 通过")
print("=" * 54)
