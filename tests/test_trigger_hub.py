"""
RFC 14 统一触发中心 — 回归测试。

四层:
  1. 触发源参数校验 (纯函数, 不碰硬件)
  2. 动作注册表上移兼容 (PLC 老路径 import 仍通, 两边同一张表)
  3. mock 触发源全链路: API CRUD → 脉冲/电平注入 → 防抖/边沿/min_interval →
     动作执行 (set_var 观察) → 触发历史/计数器
  4. HTTP 触发入口 (key 路由 + 密钥鉴权) / 手动试触发 / 导入导出

隔离纪律: 本文件建的触发源名前缀 TRGT-, janitor 收尾全删 (引擎线程随删停)。
"""
import time

import pytest

from backend.services.triggers.sources.http_source import HttpSource
from backend.services.triggers.sources.pixel_region import PixelRegionSource
from backend.services.triggers.sources.serial_pattern import SerialPatternSource
from backend.services.triggers.sources.timer_source import TimerSource


def _wait_until(fn, timeout=3.0, interval=0.03):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


# ============================================================
# 1. 触发源参数校验
# ============================================================

def test_pixel_region_params():
    ok = {"region": [0, 0, 100, 100]}
    assert PixelRegionSource.validate_params(ok) is None
    assert PixelRegionSource.validate_params({})                       # 缺 region
    assert PixelRegionSource.validate_params({"region": [5, 5, 5, 9]})  # x2<=x1
    assert PixelRegionSource.validate_params(
        {"region": [0, 0, 9, 9], "mode": "nope"})
    assert PixelRegionSource.validate_params(
        {"region": [0, 0, 9, 9], "mode": "color_match"})               # 缺 target_bgr
    assert PixelRegionSource.validate_params(
        {"region": [0, 0, 9, 9], "mode": "color_match",
         "target_bgr": [0, 0, 255]}) is None


def test_timer_params():
    assert TimerSource.validate_params({"interval_s": 5}) is None
    assert TimerSource.validate_params({"daily": ["07:30", "19:30"]}) is None
    assert TimerSource.validate_params({})                # 两者都缺
    assert TimerSource.validate_params({"interval_s": 0})
    assert TimerSource.validate_params({"daily": ["25:99"]})


def test_http_params():
    assert HttpSource.validate_params({"key": "start-line"}) is None
    assert HttpSource.validate_params({})
    assert HttpSource.validate_params({"key": "a b/c"})   # 非法字符


def test_serial_pattern_params():
    ok = {"port": "COM3", "pattern": "^TRIG"}
    assert SerialPatternSource.validate_params(ok) is None
    assert SerialPatternSource.validate_params({"pattern": "^T"})   # 缺 port
    assert SerialPatternSource.validate_params({"port": "COM3"})    # 缺 pattern
    assert SerialPatternSource.validate_params(
        {"port": "COM3", "pattern": "(["})                          # 正则不合法


def test_hid_key_params():
    # 校验是 classmethod, 不 import pynput (缺库环境也要能校验配置)
    from backend.services.triggers.sources.hid_key import HidKeySource
    assert HidKeySource.validate_params({"key": "f9"}) is None
    assert HidKeySource.validate_params({})
    assert HidKeySource.validate_params({"key": "f9", "long_press_ms": 10})


# ============================================================
# 2. 动作注册表上移兼容
# ============================================================

def test_action_registry_shared_with_plc():
    """PLC 老 import 路径仍通, 且与触发中心是同一张表 (插件注册一次两边可用)。"""
    from backend.services.plc import rule_actions
    from backend.services.triggers import actions as trigger_actions
    assert rule_actions.ACTION_REGISTRY is trigger_actions.ACTION_REGISTRY
    assert rule_actions.register_plc_action is trigger_actions.register_trigger_action
    # RFC 13 六件套 + RFC 14 四件套全部在册
    for name in ("bind_sn", "switch_project", "start_detection", "stop_detection",
                 "write_points", "alarm", "set_var",
                 "manual_settle", "clear_reset", "trigger_event", "ack_alarm"):
        assert name in trigger_actions.ACTION_REGISTRY, name


# ============================================================
# 3. mock 触发源全链路 (API)
# ============================================================

@pytest.fixture()
def trigger_janitor(client):
    """收尾: 删本文件建的 TRGT- 触发源 (引擎随删除停止)。"""
    yield
    r = client.get("/api/v1/triggers/channels")
    if r.status_code == 200:
        for t in r.json().get("triggers", []):
            if str(t.get("name", "")).startswith("TRGT-"):
                client.delete(f"/api/v1/triggers/channels/{t['id']}")


def _mk_trigger(client, name, *, type_="mock", params=None, rules=None,
                options=None, enabled=True):
    body = {
        "name": name, "type": type_, "enabled": enabled,
        "params": params or {},
        "rules": rules if rules is not None else [{
            "name": "观察",
            "when": [{"trigger": "pulse"}],
            "actions": [{"do": "set_var", "name": "seen", "value": "yes"}],
        }],
        "options": options or {"default_channel": 0},
    }
    r = client.post("/api/v1/triggers/channels", json=body)
    assert r.status_code == 200, r.text[:400]
    return r.json()["id"]


def test_trigger_types_actions_templates(client):
    types = {t["name"]: t for t in client.get("/api/v1/triggers/types").json()["types"]}
    for t in ("pixel_region", "hid_key", "http", "serial_pattern", "timer", "mock"):
        assert t in types, t
    assert types["mock"]["available"] is True
    assert types["pixel_region"]["available"] is True

    actions = client.get("/api/v1/triggers/actions").json()["actions"]
    assert "manual_settle" in actions and "bind_sn" in actions

    tpls = client.get("/api/v1/triggers/templates").json()["templates"]
    keys = {t["key"] for t in tpls}
    assert {"PIXEL_SETTLE_BUTTON", "FOOT_PEDAL_SETTLE", "MOCK_DEMO"} <= keys


def test_trigger_crud_and_validation(client, trigger_janitor):
    # 参数校验 400: pixel_region 缺 region / timer 全缺
    bad = {"name": "TRGT-bad", "type": "pixel_region", "params": {}}
    assert client.post("/api/v1/triggers/channels", json=bad).status_code == 400
    bad2 = {"name": "TRGT-bad2", "type": "timer", "params": {}}
    assert client.post("/api/v1/triggers/channels", json=bad2).status_code == 400

    tid = _mk_trigger(client, "TRGT-crud")
    rows = client.get("/api/v1/triggers/channels").json()["triggers"]
    row = next(t for t in rows if t["id"] == tid)
    assert row["runtime"]["status"] == "running"

    # 禁用 → 引擎撤下, live 404
    client.post(f"/api/v1/triggers/channels/{tid}/enable", json={"enabled": False})
    assert _wait_until(
        lambda: client.get(f"/api/v1/triggers/channels/{tid}/live").status_code == 404)

    # 再启用 → 引擎回来
    client.post(f"/api/v1/triggers/channels/{tid}/enable", json={"enabled": True})
    assert _wait_until(lambda: (
        client.get(f"/api/v1/triggers/channels/{tid}/live").status_code == 200))

    assert client.delete(f"/api/v1/triggers/channels/{tid}").json()["success"] is True


def test_mock_pulse_fires_rule_and_history(client, trigger_janitor):
    tid = _mk_trigger(client, "TRGT-pulse")
    r = client.post(f"/api/v1/triggers/channels/{tid}/mock-fire",
                    json={"meta": {"src": "unit"}})
    assert r.status_code == 200, r.text[:300]

    live = _wait_until(lambda: (
        lambda d: d if d.get("counters", {}).get("fires") else None
    )(client.get(f"/api/v1/triggers/channels/{tid}/live").json()))
    assert live, "mock 脉冲未触发规则"
    assert live["vars"].get("seen") == "yes", "set_var 动作未执行"

    hist = client.get(f"/api/v1/triggers/channels/{tid}/history").json()["history"]
    assert hist and hist[0]["rule"] == "观察" and hist[0]["edge"] == "pulse"
    logs = client.get(f"/api/v1/triggers/channels/{tid}/logs").json()["logs"]
    assert any("触发" in l["detail"] for l in logs)


def test_mock_level_edge_and_min_interval(client, trigger_janitor):
    """电平语义: 首次锁存不触发 / rising 触发 / min_interval 拦连击。"""
    tid = _mk_trigger(client, "TRGT-level", rules=[{
        "name": "边沿",
        "when": [{"trigger": "rising"}],
        "debounce_ms": 0,
        "min_interval_ms": 60_000,     # 大间隔: 第二个上升沿必被拦
        "actions": [{"do": "set_var", "name": "edge_seen", "value": "yes"}],
    }])

    def _level(v):
        r = client.post(f"/api/v1/triggers/channels/{tid}/mock-level",
                        json={"value": v})
        assert r.status_code == 200, r.text[:300]

    def _counters():
        return client.get(f"/api/v1/triggers/channels/{tid}/live").json()["counters"]

    # 启动即高电平: 只锁存不触发 ("边沿要有边")
    _level(True)
    time.sleep(0.1)
    assert _counters()["fires"] == 0

    # 落下再抬起 → rising 触发一次
    _level(False)
    _level(True)
    assert _wait_until(lambda: _counters()["fires"] == 1), "rising 未触发"

    # 再来一个上升沿 → 被 min_interval 拦
    _level(False)
    _level(True)
    assert _wait_until(lambda: _counters()["suppressed"] >= 1), "min_interval 未拦截"
    assert _counters()["fires"] == 1


def test_manual_test_fire(client, trigger_janitor):
    """手动试触发绕过信号链路直接执行动作 (现场联调按钮)。"""
    tid = _mk_trigger(client, "TRGT-test")
    r = client.post(f"/api/v1/triggers/channels/{tid}/test", json={"rule_index": 0})
    assert r.status_code == 200
    live = _wait_until(lambda: (
        lambda d: d if d.get("vars", {}).get("seen") == "yes" else None
    )(client.get(f"/api/v1/triggers/channels/{tid}/live").json()))
    assert live, "试触发动作未执行"
    # 超界 rule_index 400
    assert client.post(f"/api/v1/triggers/channels/{tid}/test",
                       json={"rule_index": 9}).status_code == 400


def test_http_fire_routing_and_auth(client, trigger_janitor):
    tid = _mk_trigger(
        client, "TRGT-http", type_="http",
        params={"key": "trgt-unit", "secret": "s3cret",
                "extract": {"order": "payload.order_no"}},
        rules=[{
            "name": "HTTP",
            "when": [{"trigger": "pulse"}],
            "actions": [{"do": "set_var", "name": "order", "value": "{{order}}"}],
        }])

    # 无密钥 → 403; 错 key → 404
    assert client.post("/api/v1/triggers/fire/trgt-unit", json={}).status_code == 403
    assert client.post("/api/v1/triggers/fire/no-such-key", json={}).status_code == 404

    # 带密钥 + payload 变量提取 → 动作模板可用
    r = client.post("/api/v1/triggers/fire/trgt-unit",
                    json={"payload": {"order_no": "WO-77"}},
                    headers={"X-Trigger-Secret": "s3cret"})
    assert r.status_code == 200, r.text[:300]
    live = _wait_until(lambda: (
        lambda d: d if d.get("vars", {}).get("order") == "WO-77" else None
    )(client.get(f"/api/v1/triggers/channels/{tid}/live").json()))
    assert live, f"payload 变量未进动作模板: {live}"


def test_trigger_export_import(client, trigger_janitor):
    tid = _mk_trigger(client, "TRGT-exp", enabled=False)
    data = client.get(f"/api/v1/triggers/channels/{tid}/export").json()
    assert "id" not in data and "enabled" not in data
    data["name"] = "TRGT-imp"
    r = client.post("/api/v1/triggers/import", json=data)
    assert r.status_code == 200
    assert r.json()["enabled"] is False      # 导入默认不启用
    rows = client.get("/api/v1/triggers/channels").json()["triggers"]
    assert any(t["name"] == "TRGT-imp" for t in rows)
