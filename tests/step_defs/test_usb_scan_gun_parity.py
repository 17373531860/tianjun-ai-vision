# -*- coding: utf-8 -*-
"""USB 键盘扫码枪行为对齐 (v3.46) BDD step 实现。

面向功能: 不 mock 处理链, 走 POST /scanner/simulate → ScannerService
._on_data_received → MESHookManager._handle_scan 真实链路, 断言扫码事件 /
扫码器在位标记 / 先扫后检闸门这些"操作员可感知"的行为结果。

隔离要点:
  - MES 扫码进队列异步处理 → 断言前轮询 detection/results 最多 ~4s
  - 每个场景建自己的枪、场景收尾删枪 (remove_device 同步清 USB 登记表),
    避免"上一场景的防呆开关"污染 scanner_present 判定
  - 背景步骤 clear_pending_scan(force) 清掉残留待检工件与去重缓存
"""
from __future__ import annotations

import time
import uuid

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

from ._synthetic_helpers import start_synthetic, detection_results

scenarios("../features/usb_scan_gun_parity.feature")

SC = "/api/v1/scanner"


@pytest.fixture(autouse=True)
def _gun_janitor(client, ctx):
    """场景级兜底清理: 断言失败也把本场景建的枪删掉,
    防止防呆开关污染后续场景的 scanner_present 判定。"""
    yield
    for gid in ctx.get("guns", []):
        client.delete(f"{SC}/devices/{gid}")

# 空剧本: 全程无检出 → 不起周期, 待检工件保持 pending, 便于观察重复扫码策略
EMPTY_SCENARIO = {"name": "usb_parity_idle", "fps": 30,
                  "timeline": [{"from": 0, "to": 10_000, "detections": []}]}


def _mk_gun(client, ctx, *, usage="bind", scan_required=False,
            warn_no_barcode=False, duplicate_scan_action="overwrite"):
    body = {
        "name": f"BDD-PARITY-{uuid.uuid4().hex[:6]}",
        "ip": "", "port": 0, "channel_id": 0, "enabled": True,
        "device_type": "usb_hid",
        "scan_required": scan_required,
        "warn_no_barcode": warn_no_barcode,
        "duplicate_scan_action": duplicate_scan_action,
        "dedup_interval_sec": 0,   # 场景内连扫不同码, 关掉去重免干扰
        "parse_config": {"usb": {"usage": usage, "order_pattern": "",
                                 "pull_conn_id": None}},
    }
    r = client.post(f"{SC}/devices", json=body)
    assert r.status_code in (200, 201), r.text[:300]
    dev = r.json().get("data") or r.json()
    ctx.setdefault("guns", []).append(dev["id"])
    ctx["gun_id"] = dev["id"]
    return dev


def _scan_event(client, channel=0):
    r = detection_results(client, channel)
    if r.status_code != 200:
        return None
    mes = (r.json() or {}).get("mes") or {}
    return mes.get("scan_event")


def _wait_scan_event(client, serial, timeout=4.0):
    """扫码进 MES 是异步队列 → 轮询到事件序列号匹配为止。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _scan_event(client)
        if last and last.get("serial_no") == serial:
            return last
        time.sleep(0.15)
    return last


# ---------------- given ----------------

@given("后端处于测试模式")
def _given_test_mode():
    import os
    assert os.environ.get("RUNTIME_MODE") == "test"


@given("通道 0 挂载空剧本与最小项目")
def _given_synthetic(client):
    # 空剧本提不出标签 → 必须显式给 project_steps, 否则项目不挂载、
    # 工位项目号为空, 扫码在 ScannerService 就被丢弃 (project_id empty)。
    # project_id 用种子项目的真实 id, 让工件登记的外键有真实归属。
    pr = client.get("/api/v1/projects")
    assert pr.status_code == 200, pr.text[:200]
    items = pr.json() if isinstance(pr.json(), list) else \
        (pr.json().get("items") or pr.json().get("data") or [])
    assert items, "测试库应有种子项目"
    r = client.post("/api/v1/test/synthetic/start", json={
        "channel": 0, "with_project": True, "logic_mode": "sequential",
        "scenario_json": EMPTY_SCENARIO,
        "project_steps": ["step_a"],
        "project_id": items[0]["id"],
    })
    assert r.status_code == 200, r.text[:300]
    assert r.json().get("project_applied") is True, r.text[:300]


@given("通道 0 的扫码状态已清空")
def _given_clear(client, ctx):
    r = client.post("/api/v1/source/detection/clear_pending_scan"
                    "?channel=0&force=true")
    assert r.status_code == 200, r.text[:300]
    # 兜底扫除其他测试文件残留的 BDD- 前缀测试枪: 按工位取重复扫码策略
    # 是"第一把枪说了算", 残留枪会遮蔽本文件自建枪的策略配置
    lr = client.get(f"{SC}/devices")
    if lr.status_code == 200:
        body = lr.json()
        items = body if isinstance(body, list) else \
            (body.get("items") or body.get("data") or [])
        for d in items:
            if isinstance(d, dict) and str(d.get("name", "")).startswith("BDD-"):
                client.delete(f"{SC}/devices/{d['id']}")
    ctx["guns"] = []


@given(parsers.parse('已有一把绑工件用途的 USB 枪 且开关为 "{mode}"'))
def _given_bind_gun_switches(client, ctx, mode):
    on = mode == "先扫后检+无码告警"
    _mk_gun(client, ctx, usage="bind", scan_required=on, warn_no_barcode=on)


@given(parsers.parse('已有一把绑工件用途的 USB 枪 且重复扫码策略为 "{action}"'))
def _given_bind_gun_action(client, ctx, action):
    _mk_gun(client, ctx, usage="bind", duplicate_scan_action=action)


@given("已有一把拉工单用途且防呆字段误置为真的 USB 枪")
def _given_pull_gun_flags(client, ctx):
    _mk_gun(client, ctx, usage="pull", scan_required=True, warn_no_barcode=True)


# ---------------- when ----------------

@when(parsers.parse('我用该 USB 枪注入工件码 "{code}"'))
def _when_scan(client, ctx, code):
    r = client.post(f"{SC}/simulate", json={
        "barcode": code, "channel_id": 0, "device_id": ctx["gun_id"]})
    assert r.status_code == 200, r.text[:300]
    # 关键断言: 注入解析到的是这把 USB 枪的配置载体, 不是 QA 虚拟兜底连接
    assert r.json().get("device_id") == ctx["gun_id"], r.text[:300]


@when(parsers.parse('等到扫码事件变为 "{serial}"'))
def _when_wait_event(client, ctx, serial):
    evt = _wait_scan_event(client, serial)
    assert evt and evt.get("serial_no") == serial, f"扫码事件未出现: {evt}"


# ---------------- then ----------------

@then(parsers.parse('通道 0 的最近扫码事件应为 "{serial}"'))
def _then_event_is(client, ctx, serial):
    evt = _wait_scan_event(client, serial)
    assert evt and evt.get("serial_no") == serial, \
        f"期望扫码事件 {serial}, 实际 {evt}"


@then(parsers.parse('通道 0 的最近扫码事件应保持为 "{serial}"'))
def _then_event_stays(client, ctx, serial):
    # 给异步队列足够时间处理第二枚码 (若未被拒绝, 事件会被顶掉)
    time.sleep(1.2)
    evt = _scan_event(client)
    assert evt and evt.get("serial_no") == serial, \
        f"拒绝策略失效: 期望事件保持 {serial}, 实际 {evt}"


@then("系统应识别为存在扫码器")
def _then_scanner_present(client, ctx):
    assert _scanner_present(client) is True


@then("系统应识别为不存在扫码器")
def _then_scanner_absent(client, ctx):
    assert _scanner_present(client) is False


@then("通道 0 应要求先扫码")
def _then_scan_required(client, ctx):
    assert _hook().is_scan_required(0) is True


@then("通道 0 不应要求先扫码")
def _then_scan_not_required(client, ctx):
    assert _hook().is_scan_required(0) is False


# ---------------- helpers ----------------

def _hook():
    from backend.services.mes_hooks import get_mes_hook
    return get_mes_hook()


def _scanner_present(client):
    r = detection_results(client, 0)
    assert r.status_code == 200, r.text[:200]
    mes = (r.json() or {}).get("mes") or {}
    return bool(mes.get("scanner_present"))
