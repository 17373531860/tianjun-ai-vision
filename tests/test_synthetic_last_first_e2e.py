"""v3.8.x last_first 结算模式 — 面向功能 E2E 测试

⚠ 已知串污染: 与 test_synthetic_*_e2e.py 同类, 全套 pytest 跑时偶发 cycle_times 不推进.
  单跑必过.

走客户实际路径验证"末步结算 + 首步开周期"模式:
  1. POST /projects/ 创建 last_first 项目
  2. POST /projects/{id}/activate 激活
  3. POST /test/synthetic/start 注入剧本
     (周期 1 ABCD OK / 周期 2 ABC 跳 D 直接 A → R3 fallback / 周期 3 ABCD OK)
  4. POST /source/detection/start 启动真实推理
  5. 等剧本跑完
  6. 通过 mgr 状态断言:
     - cycle_times 至少 2 个 OK (周期 1 + 周期 3)
     - ng_cycle_times 至少 1 个 NG (周期 2 缺 D)
     - settlement_mode == 'last_first'
"""
from __future__ import annotations

import time
import pytest


def _make_payload(name_suffix: str = ""):
    name = f"__e2e_lastfirst_{name_suffix}_{int(time.time() * 1000)}"
    return {
        "name": name,
        "task_type": "detection",
        "logic_mode": "sequential",
        "steps_config": [
            {"id": 1, "label": "A", "displayLabel": "步骤A", "enabled": True, "min_frames": 1, "threshold": 0.3},
            {"id": 2, "label": "B", "displayLabel": "步骤B", "enabled": True, "min_frames": 1, "threshold": 0.3},
            {"id": 3, "label": "C", "displayLabel": "步骤C", "enabled": True, "min_frames": 1, "threshold": 0.3},
            {"id": 4, "label": "D", "displayLabel": "步骤D", "enabled": True, "min_frames": 1, "threshold": 0.3},
        ],
        "events_config": [
            {"id": 1, "name": "合格", "actions": [], "show_notification": False},
            {"id": 2, "name": "不合格", "actions": [], "show_notification": False},
        ],
        "counters_config": [],
        "pipeline_config": {
            "sequence_order": [{"step_id": i} for i in range(1, 5)],
            "simultaneous_groups": [],
            "settlement_mode": "last_first",
            "settle_dedup": False,
        },
    }


def _create_and_activate(client, payload):
    r = client.post("/api/v1/projects/", json=payload)
    assert r.status_code in (200, 201), f"创建项目失败: {r.status_code} {r.text[:300]}"
    proj_id = r.json().get("id")
    r2 = client.post(f"/api/v1/projects/{proj_id}/activate")
    assert r2.status_code == 200, f"激活失败: {r2.text[:300]}"
    return proj_id


def _stop_all(client, channel: int = 0):
    client.post(f"/api/v1/source/detection/stop?channel={channel}")
    client.post(f"/api/v1/test/synthetic/stop?channel={channel}")


def _wait_for_scenario_done(client, channel: int = 0, max_wait: float = 12.0):
    last_seq = -1
    stable_for = 0
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/test/synthetic/state?channel={channel}")
        if r.status_code != 200:
            time.sleep(0.1)
            continue
        seq = (r.json() or {}).get("frame_seq", 0)
        if seq == last_seq:
            stable_for += 1
            if stable_for >= 3:
                return seq
        else:
            stable_for = 0
        last_seq = seq
        time.sleep(0.15)
    return last_seq


def _get_mgr(channel: int = 0):
    from backend.api.source import _get_mgr as _gm
    return _gm(channel)


@pytest.fixture
def fresh_channel(client):
    _stop_all(client, 0)
    try:
        client.post("/api/v1/source/detection/reset-stats?channel=0")
    except Exception:
        pass
    mgr = _get_mgr(0)
    if hasattr(mgr, 'cycle_times'):
        mgr.cycle_times.clear()
    if hasattr(mgr, 'ng_cycle_times'):
        mgr.ng_cycle_times.clear()
    if hasattr(mgr, 'cycle_start_time'):
        mgr.cycle_start_time = None
    if hasattr(mgr, '_clear_step_runtime_state'):
        mgr._clear_step_runtime_state()
    yield 0
    _stop_all(client, 0)
    try:
        r = client.get("/api/v1/projects/")
        if r.status_code == 200:
            data = r.json()
            items = data.get("items") if isinstance(data, dict) else data
            for p in items or []:
                if (p.get("name") or "").startswith("__e2e_lastfirst_"):
                    client.delete(f"/api/v1/projects/{p['id']}")
    except Exception:
        pass


def test_last_first_two_ok_one_ng(client, fresh_channel):
    """剧本: ABCD (OK) → ABC + 跳 D + A (NG 缺 D, R3 fallback) → BCD (OK).

    断言:
      - settlement_mode 已正确同步为 'last_first'
      - cycle_times 至少 2 个 (周期 1 + 周期 3)
      - ng_cycle_times 至少 1 个 (周期 2 缺 D)
    """
    ch = fresh_channel
    payload = _make_payload(name_suffix="basic")
    _create_and_activate(client, payload)

    r = client.post("/api/v1/test/synthetic/start", json={
        "scenario": "last_first_settlement.json",
        "channel": ch,
        "with_project": False,
    })
    assert r.status_code == 200, f"synthetic 启动失败: {r.text[:300]}"

    r = client.post(f"/api/v1/source/detection/start?channel={ch}",
                    json={"conf": 0.25, "iou": 0.45})
    assert r.status_code == 200, f"detection 启动失败: {r.text[:300]}"

    final_seq = _wait_for_scenario_done(client, ch, max_wait=12.0)
    assert final_seq >= 199, f"剧本未跑完, final_seq={final_seq}"

    time.sleep(0.5)

    mgr = _get_mgr(ch)
    ok_count = len(mgr.cycle_times)
    ng_count = len(mgr.ng_cycle_times)
    print(f"[E2E·last_first] settlement_mode={mgr.settlement_mode} "
          f"OK={ok_count} NG={ng_count} "
          f"current_cycle_steps={mgr.current_cycle_steps} "
          f"_blocked={mgr._blocked_labels} pending={mgr._pending_first_step}")

    assert mgr.settlement_mode == 'last_first', \
        f"激活后 settlement_mode 应为 last_first, 实际 {mgr.settlement_mode}"
    assert ok_count >= 2, \
        f"周期 1 + 周期 3 应至少 2 个 OK 结算, 实际 ok_count={ok_count} ng_count={ng_count}"
    assert ng_count >= 1, \
        f"周期 2 (跳 D 直接 A) 应触发 R3 fallback NG, 实际 ng_count={ng_count}"
