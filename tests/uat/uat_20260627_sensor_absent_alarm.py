#!/usr/bin/env python
"""现场验证（in-process 集成）：传感器清洁插件离岗判定 → NG → 硬件报警 + 配置持久化。

回答客户两点疑问：
  1) 离岗超时秒数改了能不能存住（保存退出后是否回退）。
  2) 离岗超时设 1 秒 + 连 NG，是否真触发主程序硬件报警 (灯/蜂鸣) 链路。

做法（不依赖摄像头/模型）：
  - 用临时 DATA_DIR 起一套干净 DB（不污染主库）。
  - 真实 channel_manager 建工位1（通道1）并挂含 NG(id=2) 事件的项目。
  - 真实 PluginHost（带 runtime.event_trigger / alarm_trigger / system_config_write）。
  - monkeypatch alarm_router.trigger_alarm 抓取「真的派发到报警器」的调用。
  - 真实加载插件 backend 包，存配置 → 冷读回放（模拟重启）验持久化；
    再逐帧喂「通道1 无人」跨过 1 秒 → 看是否 trigger_event(1, 2) → 派发 event2 报警。
"""
from __future__ import annotations

import os
import sys
import json
import tempfile
import importlib.util
from pathlib import Path

REPO = Path("/home/qianqian/桌面/word/tianjun-main")
sys.path.insert(0, str(REPO))
# 临时数据目录：隔离主库，避免写脏主程序 sql_app.db
os.environ["TIANJUN_DATA_DIR"] = tempfile.mkdtemp(prefix="sc_absent_itest_")
os.environ["BACKEND_SKIP_INIT"] = "1"

_results = []


def check(name, cond, extra=""):
    _results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({extra})" if extra else ""))


# ---------------------------------------------------------------------------
# 0. 建干净 DB 表
# ---------------------------------------------------------------------------
from backend.db.database import engine
from backend.models.models import Base

Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------------------------
# 1. 真实工位1（通道1）+ 含 NG(id=2) 的项目
# ---------------------------------------------------------------------------
from backend.api.channel_manager import channel_manager

channel_manager.set_channel_count(2)
mgr1 = channel_manager.channels[1]
mgr1.set_project_config({
    "id": 999,
    "name": "itest-视角2-换棉签",
    "task_type": "detection",
    "logic_mode": "detection",
    "pipeline_config": {"logic_mode": "detection"},
    "steps_config": [{"id": 1, "label": "更换棉签", "order": 1, "confidence": 0.7}],
    "events_config": [
        {"id": 1, "name": "合格(OK)", "actions": [{"counter_name": "合格总数", "delta": 1}],
         "show_notification": True, "toast_id": "ok"},
        {"id": 2, "name": "不良(NG)", "actions": [{"counter_name": "不良总数", "delta": 1},
                                                   {"counter_name": "离岗告警次数", "delta": 1}],
         "show_notification": True, "toast_id": "ng"},
    ],
    "counters_config": [
        {"name": "合格总数", "value": 0},
        {"name": "不良总数", "value": 0},
        {"name": "离岗告警次数", "value": 0},
    ],
    "alarm_config": {},
    "detection_config": {},
    "data_config": {},
})

# ---------------------------------------------------------------------------
# 2. 抓「报警器派发」调用 (monkeypatch)
# ---------------------------------------------------------------------------
from backend.api import alarm as alarm_mod

alarm_calls = []  # [(event_type, channel_id)]
_orig_trigger = alarm_mod.alarm_router.trigger_alarm


def _spy_trigger_alarm(event_type, channel_id=None, *a, **kw):
    alarm_calls.append((event_type, channel_id))
    print(f"    >>> alarm_router.trigger_alarm(event_type={event_type!r}, channel_id={channel_id})")
    return True


alarm_mod.alarm_router.trigger_alarm = _spy_trigger_alarm

# ---------------------------------------------------------------------------
# 3. 真实 PluginHost + 加载插件 backend 包
# ---------------------------------------------------------------------------
from backend.plugin_system.registry import PluginHost

PLUGIN_DIR = REPO / "plugins-examples" / "sensor-clean"
host = PluginHost(
    customer_code="sensor-clean",
    plugin_dir=str(PLUGIN_DIR),
    main_version="3.27.0",
    capabilities=["runtime.event_trigger", "runtime.alarm_trigger", "runtime.system_config_write"],
)

BACKEND_DIR = PLUGIN_DIR / "backend"
PKG = "sc_absent_pkg"
for m in list(sys.modules):
    if m == PKG or m.startswith(PKG + "."):
        del sys.modules[m]
spec = importlib.util.spec_from_file_location(
    PKG, BACKEND_DIR / "__init__.py",
    submodule_search_locations=[str(BACKEND_DIR)],
)
pkg = importlib.util.module_from_spec(spec)
sys.modules[PKG] = pkg
spec.loader.exec_module(pkg)
sc = sys.modules[PKG + ".hooks"]

sc.set_host(host)


def frame(ch, t, dets):
    sc.on_detection_frame({"channel_id": ch, "timestamp": t, "detections": dets})


print("\n=== A. 配置持久化（保存→冷读回放，模拟重启）===")
sc.save_config({
    "swap_channel": 1,
    "operator_absent_enabled": True,
    "operator_absent_timeout_sec": 1,
    "operator_absent_event_id": 2,
})
cfg1 = sc.get_config()
check("保存后立即读: 超时=1", cfg1.get("operator_absent_timeout_sec") == 1,
      f"实际={cfg1.get('operator_absent_timeout_sec')}")
check("保存后立即读: 启用=True", cfg1.get("operator_absent_enabled") is True)

# 模拟"退出再进来": 丢掉内存缓存, 从 DB 冷读
sc.reload_config()
cfg2 = sc.get_config()
check("重启后冷读: 超时仍=1（未回退）", cfg2.get("operator_absent_timeout_sec") == 1,
      f"实际={cfg2.get('operator_absent_timeout_sec')}")
check("重启后冷读: 启用仍=True（未回退）", cfg2.get("operator_absent_enabled") is True)

# 直接核 DB 里那行系统配置, 证明真落库了
raw = host.read_system_config("plugin_sensor_clean_config")
saved = json.loads(raw) if isinstance(raw, str) else (raw or {})
check("DB 系统配置行: 超时=1", saved.get("operator_absent_timeout_sec") == 1,
      f"DB={saved.get('operator_absent_timeout_sec')}")

print("\n=== B. 离岗 1 秒 → 触发 NG → 派发硬件报警 ===")
alarm_calls.clear()
# 通道1 持续无人: t=0 设截止(=0+1), t=2 已超 1 秒 → 命中离岗
frame(1, 0.0, [])
state_mid = sc.get_state()
frame(1, 2.0, [])
state_after = sc.get_state()

ng_alarm = any(et == "event2" and ch == 1 for (et, ch) in alarm_calls)
check("离岗命中 → 派发 event2 报警(灯/蜂鸣链路)", ng_alarm,
      f"alarm_calls={alarm_calls}")
check("主程序不良计数器 +1（事件联动）", mgr1.counters.get("不良总数", 0) >= 1,
      f"不良总数={mgr1.counters.get('不良总数')}")
check("离岗告警次数 +1（上主页计数器）", mgr1.counters.get("离岗告警次数", 0) >= 1,
      f"离岗告警次数={mgr1.counters.get('离岗告警次数')}")

# ---------------------------------------------------------------------------
alarm_mod.alarm_router.trigger_alarm = _orig_trigger
print("\n=== 汇总 ===")
ok = all(_results)
print(f"  {sum(_results)}/{len(_results)} 通过")
sys.exit(0 if ok else 1)
