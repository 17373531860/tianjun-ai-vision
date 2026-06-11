"""后端契约 UAT — 步骤耗时三档 每步事件触发 + 提示框队列 (v1.2.0).

直接 import 插件后端 (file loader, 包名带连字符), mock PluginHost,
驱动 step_tick / step_change / pre_cycle_end, 断言:
  - 警告越线 → trigger_alarm(每步 warn_event) + 入 warn 提示框队列
  - 最长越线 → trigger_alarm(每步 ng_event) + 入 ng 提示框队列 + 标记 NG
  - 未达最短 → trigger_alarm(每步 ng_event) + 入 ng 提示框队列 + 标记 NG
  - pre_cycle_end → override_result=NG
  - pending-toasts drain 消费即清
  - warn_toast=False → 报警照触发但不入提示框队列
"""
import importlib.util
import json
import sys

PLUGIN = "/home/qianqian/桌面/word/tianjun-main/plugins-examples/fujian-jinlong/backend/__init__.py"

spec = importlib.util.spec_from_file_location("fjjl_be", PLUGIN)
mod = importlib.util.module_from_spec(spec)
sys.modules["fjjl_be"] = mod
spec.loader.exec_module(mod)


class FakeHost:
    def __init__(self):
        self.alarms = []
        self.store = {}

    def read_system_config(self, k):
        return self.store.get(k)

    def write_system_config(self, k, v, description=""):
        self.store[k] = v
        return True

    def trigger_alarm(self, channel_id, event_type, reason=""):
        self.alarms.append({"ch": channel_id, "event": event_type, "reason": reason})
        return True


host = FakeHost()
mod._HOST = host

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  [{extra}]" if extra else ""))


def set_cfg(cfg):
    host.store[mod.CONFIG_KEY] = json.dumps(cfg, ensure_ascii=False)
    mod._load_config(force=True)
    host.alarms.clear()
    mod._PENDING_TOASTS.clear()


# ============ 用例1: 配置读写带新字段 ============
print("\n[1] 配置读写保留每步事件/提示框字段")
cfg_in = {
    "enabled": True,
    "default": {"min_sec": 0, "warn_sec": 0, "max_sec": 0},
    "steps": {
        "stepA": {"min_sec": 1, "warn_sec": 2, "max_sec": 5,
                  "warn_event": "event3", "ng_event": "event5",
                  "warn_toast": True, "ng_toast": True},
    },
}
norm = mod._normalize_config(cfg_in)
sA = norm["steps"]["stepA"]
check("warn_event 落库", sA.get("warn_event") == "event3", sA.get("warn_event"))
check("ng_event 落库", sA.get("ng_event") == "event5", sA.get("ng_event"))
check("warn_toast 落库", sA.get("warn_toast") is True)
check("ng_toast 落库", sA.get("ng_toast") is True)
th = mod._thresholds_for(norm, "stepA")
check("_thresholds_for 合并事件", th["warn_event"] == "event3" and th["ng_event"] == "event5",
      f"{th['warn_event']}/{th['ng_event']}")

# ============ 用例2: 警告越线 → warn_event + warn toast ============
print("\n[2] 警告越线 → 触发每步 warn_event + 入 warn 提示框")
set_cfg(cfg_in)
mod.on_cycle_start({"channel_id": 0, "cycle_id": 100})
mod.on_step_tick({"channel_id": 0, "cycle_id": 100, "step_label": "stepA",
                  "step_name": "拧螺丝A", "elapsed_sec": 2.5})
check("trigger_alarm 用 warn_event=event3", any(a["event"] == "event3" for a in host.alarms),
      str(host.alarms))
toasts = mod._drain_toasts()
check("warn 提示框入队 level=warn", any(t["level"] == "warn" and t["step"] == "拧螺丝A" for t in toasts),
      str(toasts))

# ============ 用例3: 最长越线 → ng_event + ng toast + 标记NG ============
print("\n[3] 最长越线 → 触发每步 ng_event + 入 ng 提示框 + 标记NG")
set_cfg(cfg_in)
mod.on_cycle_start({"channel_id": 0, "cycle_id": 101})
mod.on_step_tick({"channel_id": 0, "cycle_id": 101, "step_label": "stepA",
                  "step_name": "拧螺丝A", "elapsed_sec": 6.0})
check("trigger_alarm 用 ng_event=event5", any(a["event"] == "event5" for a in host.alarms),
      str(host.alarms))
toasts = mod._drain_toasts()
check("ng 提示框入队 level=ng", any(t["level"] == "ng" for t in toasts), str(toasts))
ov = mod.on_pre_cycle_end({"channel_id": 0, "cycle_id": 101})
check("pre_cycle_end 强制 NG", ov.get("override_result") == "NG", str(ov))

# ============ 用例4: 未达最短 → ng_event + ng toast ============
print("\n[4] 未达最短 → 触发每步 ng_event + 入 ng 提示框")
set_cfg(cfg_in)
mod.on_cycle_start({"channel_id": 1, "cycle_id": 200})
mod.on_step_change({"channel_id": 1, "cycle_id": 200, "step_label": "stepA",
                    "step_name": "拧螺丝A", "duration": 0.5})
check("trigger_alarm 用 ng_event=event5 (ch1)", any(a["event"] == "event5" and a["ch"] == 1 for a in host.alarms),
      str(host.alarms))
toasts = mod._drain_toasts()
check("ng 提示框入队 (ch1)", any(t["level"] == "ng" and t["channel"] == 1 for t in toasts), str(toasts))

# ============ 用例5: drain 消费即清 ============
print("\n[5] pending-toasts 消费即清")
set_cfg(cfg_in)
mod.on_step_tick({"channel_id": 0, "cycle_id": 300, "step_label": "stepA",
                  "step_name": "拧螺丝A", "elapsed_sec": 2.5})
first = mod._drain_toasts()
second = mod._drain_toasts()
check("第一次 drain 有数据", len(first) >= 1, str(len(first)))
check("第二次 drain 为空(已清)", len(second) == 0, str(len(second)))

# ============ 用例6: warn_toast=False → 报警触发但不弹提示框 ============
print("\n[6] warn_toast=False → 报警照响, 提示框不弹")
cfg6 = json.loads(json.dumps(cfg_in))
cfg6["steps"]["stepA"]["warn_toast"] = False
set_cfg(cfg6)
mod.on_step_tick({"channel_id": 0, "cycle_id": 400, "step_label": "stepA",
                  "step_name": "拧螺丝A", "elapsed_sec": 2.5})
check("报警仍触发(event3)", any(a["event"] == "event3" for a in host.alarms), str(host.alarms))
toasts = mod._drain_toasts()
check("提示框不入队(warn_toast=False)", len(toasts) == 0, str(toasts))

# ============ 用例7: 不同步骤不同事件 (percell 独立) ============
print("\n[7] 不同步骤选不同事件 (percell 独立)")
cfg7 = {
    "enabled": True, "default": {"min_sec": 0, "warn_sec": 0, "max_sec": 0},
    "steps": {
        "stepA": {"warn_sec": 2, "warn_event": "event3", "warn_toast": True},
        "stepB": {"warn_sec": 2, "warn_event": "event4", "warn_toast": True},
    },
}
set_cfg(cfg7)
mod.on_step_tick({"channel_id": 0, "cycle_id": 500, "step_label": "stepA",
                  "step_name": "A", "elapsed_sec": 3})
mod.on_step_tick({"channel_id": 0, "cycle_id": 500, "step_label": "stepB",
                  "step_name": "B", "elapsed_sec": 3})
evs = {a["event"] for a in host.alarms}
check("stepA→event3 且 stepB→event4 (各自独立)", "event3" in evs and "event4" in evs, str(evs))

print(f"\n{'='*50}\n通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败用例:", FAIL)
    sys.exit(1)
print("✅ 后端契约全绿")
