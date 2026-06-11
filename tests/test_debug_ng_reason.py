"""面向功能测试: "一直 NG 时调试日志必须能说出为什么 NG".

验收契约 (2026-06-11 全局调试系统补课):
  1. 开 backend.settlement 开关 → synthetic 真跑 NG 剧本 (缺 step_b)
     → /debug/logs 里必须出现带"缺少哪一步"的结算 NG 日志 (全链路, 非 mock)
  2. 开关全关 → 同剧本再跑 → 不产生任何结算日志 (零差异/零开销对照)
  3. per_item 漏件 NG → 日志必须说出"哪一步漏了几件"
  4. 周期不开始 → 日志必须说出拒绝原因 (首步检出不足)

走与生产完全相同的 pipeline (synthetic 注入 → step_stats → settlement →
_trigger_event), 不构造中间数据绕过上游 (v3.7.0 假阳性教训).
"""
from __future__ import annotations

import time

import pytest

from backend.core import debug_center


# ==================== 工具 ====================

NG_SCENARIO = "ng_missing_step.json"      # step_a 出现 → step_b 全程缺 → step_c 出现
SEQ_STEPS = ["step_a", "step_b", "step_c"]


def _start_ng_scenario(client, channel: int = 0):
    """启动 NG 剧本 + 三步顺序项目 (剧本里没有 step_b, 必须显式传 project_steps)"""
    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario": NG_SCENARIO,
        "channel": channel,
        "with_project": True,
        "project_steps": SEQ_STEPS,
        "logic_mode": "sequential",
    })
    assert r.status_code == 200, f"start synthetic 失败: {r.text[:300]}"
    r = client.post(f"/api/v1/source/detection/start?channel={channel}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, f"detection/start 失败: {r.text[:300]}"


def _stop_all(client, channel: int = 0):
    client.post(f"/api/v1/source/detection/stop?channel={channel}")
    client.post(f"/api/v1/test/synthetic/stop?channel={channel}")


def _wait_for_log(client, predicate, timeout_sec: float = 15.0):
    """轮询 /debug/logs 直到某条日志满足 predicate, 返回该条目或 None"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = client.get("/api/v1/debug/logs?limit=2000")
        if r.status_code == 200:
            for entry in r.json().get("logs", []):
                if predicate(entry):
                    return entry
        time.sleep(0.3)
    return None


@pytest.fixture
def clean_debug(client):
    """前后都把开关归零 + 清缓冲, 保证测试间零串扰"""
    debug_center.set_flags({k: False for k in debug_center.BACKEND_CATEGORIES})
    debug_center.clear_logs()
    _stop_all(client, 0)
    yield
    _stop_all(client, 0)
    debug_center.set_flags({k: False for k in debug_center.BACKEND_CATEGORIES})
    debug_center.clear_logs()


# ==================== 1. NG 原因全链路 (sequential) ====================

def test_ng_reason_visible_when_settlement_flag_on(client, clean_debug):
    """开 backend.settlement → 跑缺 step_b 的剧本 → 日志必须说出缺哪步"""
    r = client.put("/api/v1/debug/flags", json={"flags": {"backend.settlement": True}})
    assert r.status_code == 200 and r.json()["flags"]["backend.settlement"] is True

    _start_ng_scenario(client, 0)
    try:
        entry = _wait_for_log(
            client,
            lambda e: (e["category"] == "backend.settlement"
                       and "NG" in e["action"]
                       and "step_b" in e.get("detail", "")),
        )
        assert entry is not None, (
            "NG 已发生但调试日志没有说出'缺少 step_b' — "
            "『一直 NG 要能知道为什么』契约被破坏"
        )
    finally:
        _stop_all(client, 0)


# ==================== 2. 零差异对照 (开关全关) ====================

def test_no_settlement_logs_when_flags_off(client, clean_debug):
    """开关全关 → 同剧本跑一轮 → 不允许产生任何结算类日志 (零开销承诺)"""
    _start_ng_scenario(client, 0)
    try:
        # 给足一个剧本循环的时间让 NG 真实发生
        time.sleep(5.0)
        r = client.get("/api/v1/debug/logs?limit=2000")
        assert r.status_code == 200
        settlement_logs = [e for e in r.json().get("logs", [])
                           if e["category"] in ("backend.settlement", "backend.per_item")]
        assert settlement_logs == [], f"开关全关仍产生了日志: {settlement_logs[:3]}"
    finally:
        _stop_all(client, 0)


# ==================== 3. per_item 漏件 NG 原因 ====================

def _make_per_item_stub():
    """最小 stub: 继承 PerItemMixin, 锁定 2 件只覆盖 1 件 → settle 必判 NG.

    settle 的输入就是'锁定个体表状态', 上游锁定路径另有'周期开始/周期未开始'埋点,
    此处直接构造该状态不算绕过上游.
    """
    from backend.api.source_per_item_mixin import (
        PerItemMixin, _PerItemStep, _PerItemSession,
    )

    class _Stub(PerItemMixin):
        def __init__(self):
            self.channel_id = 0
            self.current_cycle_steps = []
            self.cycle_start_time = None
            self.events = []
            self._per_item_last_ng_detail = None

        def _trigger_event(self, event_id, reason):
            self.events.append((event_id, reason))

    stub = _Stub()
    step = _PerItemStep({"id": "s1", "label": "拧紧", "displayLabel": "拧紧",
                         "per_item": {"item_label": "螺丝", "action_label": "拧"}})
    step.lock_items_from_boxes(
        [(0.1, 0.1, 0.05, 0.05), (0.5, 0.5, 0.05, 0.05)], frame_id=1, ts=0.0)
    next(iter(step.items.values())).covered = True   # 2 锁 1 覆盖 → 漏 1 件

    sess = _PerItemSession()
    sess.cycle_active = True
    sess.cycle_start_time = 0.0
    stub._per_item_steps = [step]
    stub._per_item_session = sess
    return stub


def test_per_item_ng_reason_says_which_step_missing_how_many(client, clean_debug):
    debug_center.set_flags({"backend.per_item": True})
    stub = _make_per_item_stub()
    stub._per_item_settle_cycle(time.time())

    logs = debug_center.get_logs()["logs"]
    ng = [e for e in logs
          if e["category"] == "backend.per_item" and e["action"] == "周期结算 NG"]
    assert ng, f"per_item NG 未产生调试日志: {logs[-5:]}"
    detail = ng[-1]["detail"]
    assert "拧紧" in detail and "1/2" in detail and "共漏1件" in detail, (
        f"NG 原因不完整, 无法回答'为什么NG': {detail}"
    )
    # NG 事件确实触发 (走的是真结算路径)
    assert any(eid == 2 for eid, _ in stub.events)


def test_per_item_ok_no_false_ng_log(client, clean_debug):
    """对照: 全覆盖 → 结算 OK, 不允许误报 NG 日志"""
    debug_center.set_flags({"backend.per_item": True})
    stub = _make_per_item_stub()
    for st in stub._per_item_steps[0].items.values():
        st.covered = True
    stub._per_item_steps[0].completed = True
    stub._per_item_settle_cycle(time.time())

    logs = debug_center.get_logs()["logs"]
    assert any(e["action"] == "周期结算 OK" for e in logs
               if e["category"] == "backend.per_item")
    assert not any(e["action"] == "周期结算 NG" for e in logs
                   if e["category"] == "backend.per_item")


# ==================== 4. 周期不开始的拒绝原因 ====================

def test_per_item_cycle_not_starting_reason(client, clean_debug):
    """固定数量模式检出不足 → 日志必须说'首步检出不足' (答'周期为什么一直不开始')"""
    from backend.api.source_per_item_mixin import _PerItemStep, _PerItemSession

    debug_center.set_flags({"backend.per_item": True})
    stub = _make_per_item_stub()
    step = _PerItemStep({"id": "s1", "label": "拧紧",
                         "per_item": {"item_label": "螺丝", "expected_count": 14}})
    sess = _PerItemSession()
    sess.cycle_active = False
    stub._per_item_steps = [step]
    stub._per_item_session = sess
    stub._per_item_config = {
        "stability_window_frames": 3,
        "stability_iou_threshold": 0.5,
        "stability_count_ratio": 0.85,
        "stability_count_tolerance": 0,
        "require_exact_count": False,
        "lock_lookahead_seconds": 0,
    }

    # 连续 3 帧只检出 5 个 (期望 14) → 永远开不了周期
    boxes = {"螺丝": [(0.1 * i, 0.1, 0.05, 0.05) for i in range(5)]}
    for i in range(3):
        stub._per_item_try_start_cycle(boxes, current_time=float(i))

    assert not sess.cycle_active, "检出不足不应开周期"
    logs = debug_center.get_logs()["logs"]
    rejects = [e for e in logs
               if e["category"] == "backend.per_item" and e["action"] == "周期未开始"]
    assert rejects, "周期未开始却没有任何拒绝原因日志"
    assert "首步检出不足" in rejects[-1]["detail"], rejects[-1]["detail"]
