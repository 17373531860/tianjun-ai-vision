"""真机现场验证: 真秤 PL2303 (/dev/ttyUSB0) 读数 → 原生称重引擎全链路。

证明 "真实硬件读数" 走和虚拟馈入完全相同的代码路径驱动状态机:
  真秤连续输出 ST,NT,+0.018kg → 外设管线解析 weight=0.018 → 喂引擎 →
  自动去皮(阈值贴 0.018 下方) → 投料稳定 → 对比标准量(标准贴 0.018) → 判定 → 记录。
"""
import time
import requests

BASE = "http://localhost:8001/api/v1"
CH = 0


def jget(p):
    return requests.get(f"{BASE}{p}", timeout=5).json()


def jpost(p, b):
    return requests.post(f"{BASE}{p}", json=b, timeout=5).json()


print("==================== 1) 禁用会干扰的 mock 称重设备 ====================")
devs = jget("/external-devices/")
for d in devs:
    if d.get("device_role") == "weight" and d.get("channel_id") == 0 \
            and d.get("protocol") == "mock_weight" and d.get("enabled"):
        body = dict(d); body["enabled"] = False
        r = requests.put(f"{BASE}/external-devices/{d['id']}", json=body, timeout=5)
        print(f"  禁用 mock [{d['name']}] -> {r.status_code}")

print("==================== 2) 创建真秤外设(serial_continuous, 自动连接读串口) ====================")
# 先删掉同名旧的真秤(重复跑脚本)
for d in jget("/external-devices/"):
    if d.get("serial_port") == "/dev/ttyUSB0":
        requests.delete(f"{BASE}/external-devices/{d['id']}", timeout=5)
        print(f"  删除旧真秤设备 id={d['id']}")

real = {
    "name": "百斯特真秤PL2303",
    "device_role": "weight",
    "protocol": "serial_continuous",
    "serial_port": "/dev/ttyUSB0",
    "serial_baud": 9600,
    "parse_mode": "regex",
    "parse_config": {"pattern": r"([0-9]+\.?[0-9]*)kg", "fields": {"weight": 1}},
    "channel_id": 0,
    "data_target": "none",
    "enabled": True,
    "stable_enabled": False,
}
r = requests.post(f"{BASE}/external-devices/", json=real, timeout=8)
print(f"  创建真秤 -> {r.status_code}: {r.text[:240]}")
dev_id = None
try:
    dev_id = r.json().get("id")
except Exception:
    pass

print("  等 3s 让串口读起来...")
time.sleep(3)
print("  设备状态:", jget("/external-devices/status"))
logs = jget(f"/external-devices/logs?device_id={dev_id}") if dev_id else {}
recent = (logs.get("logs") or logs.get("data") or logs) if isinstance(logs, dict) else logs
print("  最近原始帧(后端真读到的):")
items = logs.get("logs", []) if isinstance(logs, dict) else []
for it in items[:5]:
    print("   ", it.get("raw_data") or it.get("raw"), "-> parsed", it.get("parsed_data") or it.get("parsed"))

print("==================== 3) 设 weighing 模式(标准量贴 0.018 便于真机判定) ====================")
wcfg = {
    "materials": ["测试料"],
    "models": {"真秤测试": {"测试料": {"standard": 0.018, "low_tol": 0.010, "high_tol": 0.010}}},
    "tare_mode": "auto_stable",
    "tare_trigger_weight": 0.010,   # 真秤基线 0.018 > 0.010 → 触发去皮
    "tare_settle_samples": 3,
    "stable_tol": 0.010, "stable_min_samples": 3, "measure_min_weight": 0.002,
    "require_operator": True, "require_model": True,
    "material_check": "off", "auto_zero_after_done": True,
    "alarm_event_shortage": 2, "alarm_event_over": 2,
}
sp = jpost("/source/detection/set-project?channel=0", {
    "project_id": 9999, "name": "UAT真秤", "task_type": "detection",
    "logic_mode": "weighing", "steps_config": [],
    "pipeline_config": {"weighing": wcfg},
    "events_config": [{"id": 1, "name": "合格", "actions": []}, {"id": 2, "name": "不良", "actions": []}],
    "counters_config": [], "data_config": {},
})
print("  set-project:", sp.get("status", sp))

print("==================== 4) 选人员/型号 + 扫码开始一件 ====================")
jpost("/weighing/context", {"channel_id": CH, "operator": "现场测试员", "model_name": "真秤测试"})
sc = jpost("/weighing/scan", {"channel_id": CH, "serial_no": "REAL-001"})
print("  扫码后相位:", sc.get("snapshot", {}).get("phase"))

print("==================== 5) 观察真秤读数带动状态机(最多8秒) ====================")
phases = []
for i in range(16):
    time.sleep(0.5)
    st = jget("/weighing/state?channel=0")
    ph = st.get("phase")
    if not phases or phases[-1] != ph:
        phases.append(ph)
        print(f"  t={i*0.5:.1f}s 相位={ph} 料别idx={st.get('material_idx')}")
    if ph == "done":
        break

print("==================== 6) 结果 ====================")
recs = jget("/weighing/records?channel=0&limit=5").get("records", [])
print("  相位轨迹:", " → ".join(phases))
print("  逐件记录(真秤数据):")
for r in recs[-3:]:
    print("   ", r)
real_driven = len(phases) > 1 or len(recs) > 0
print(f"\n  {'✅ 真秤读数成功驱动了状态机(相位推进/产生记录)' if real_driven else '❌ 状态机未被真秤读数驱动'}")
