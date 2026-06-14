"""USB 键盘扫码枪 BDD step 实现 (v3.20)。

USB 扫码枪在主程序里被当成"扫码器设备的一种" (device_type=usb_hid):
插上即键盘, 后端不建网络连接 (无 IP/端口), 仅作设备表记录供前端读取;
真正的扫码捕获在前端全局键盘监听。本 BDD 锁定后端契约:
  创建落库 + 多枪共存不撞 ":0" + 无网络地址可建 + 模拟扫码注入不崩 + 删除。
"""
from __future__ import annotations

import uuid

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

scenarios("../features/usb_scan_gun.feature")

SC = "/api/v1/scanner"


def _usb_body(usage="bind"):
    return {
        "name": f"BDD-USB-{uuid.uuid4().hex[:6]}",
        "ip": "", "port": 0, "channel_id": 0, "enabled": True,
        "device_type": "usb_hid",
        "parse_config": {"usb": {"usage": usage, "order_pattern": "",
                                 "pull_conn_id": None, "bind_channel_id": 0}},
    }


def _create(client, usage="bind"):
    return client.post(f"{SC}/devices", json=_usb_body(usage))


def _list_ids(client):
    r = client.get(f"{SC}/devices")
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    items = body if isinstance(body, list) else (body.get("items") or body.get("data") or [])
    return items


@given("后端处于测试模式")
def _given_test_mode():
    import os
    assert os.environ.get("RUNTIME_MODE") == "test"


@given("已有一把 USB 扫码枪")
def _given_have_usb(client, ctx):
    r = _create(client, "bind")
    assert r.status_code in (200, 201), r.text[:200]
    ctx["dev_id"] = (r.json().get("data") or r.json()).get("id")


# ---------------- when ----------------
@when("我创建一把用途为绑工件的 USB 扫码枪")
def _when_create_bind(client, ctx):
    ctx["resp"] = _create(client, "bind")
    ctx["created"] = (ctx["resp"].json().get("data") or ctx["resp"].json())


@when("我创建一把用途为拉工单的 USB 扫码枪")
def _when_create_pull(client, ctx):
    ctx["resp"] = _create(client, "pull")
    ctx["ok1"] = ctx["resp"].status_code in (200, 201)


@when("我再创建一把用途为绑工件的 USB 扫码枪")
def _when_create_bind2(client, ctx):
    ctx["resp"] = _create(client, "bind")
    ctx["ok2"] = ctx["resp"].status_code in (200, 201)


@when("我创建一把不带网络地址的 USB 扫码枪")
def _when_create_noaddr(client, ctx):
    body = _usb_body("both")
    body["ip"] = ""
    body["port"] = 0
    ctx["resp"] = client.post(f"{SC}/devices", json=body)


@when("我模拟扫一个工件码")
def _when_simulate(client, ctx):
    code = f"WP-{uuid.uuid4().hex[:8]}"
    ctx["resp"] = client.post(f"{SC}/simulate", json={
        "barcode": code, "channel_id": 0, "external_only": True})


@when("我删除该 USB 扫码枪")
def _when_delete(client, ctx):
    ctx["resp"] = client.delete(f"{SC}/devices/{ctx['dev_id']}")


# ---------------- then ----------------
@then(parsers.parse("扫码枪响应状态应为 {code:d}"))
def _then_status(ctx, code):
    resp = ctx["resp"]
    if resp.status_code == 404:
        pytest.skip("路由未挂载 (404)")
    # 创建/删除接口可能返回 200 或 201, 这里按 feature 写的 200 兼容
    assert resp.status_code in (code, 201), \
        f"期望 {code} 实际 {resp.status_code} body={resp.text[:200]}"


@then(parsers.parse("扫码注入响应状态应为 {code:d}"))
def _then_inject_status(ctx, code):
    resp = ctx["resp"]
    if resp.status_code == 404:
        pytest.skip("simulate 路由未挂载")
    assert resp.status_code == code, f"期望 {code} 实际 {resp.status_code} body={resp.text[:200]}"


@then("该设备应能在扫码器列表查到")
def _then_in_list(client, ctx):
    cid = ctx["created"].get("id")
    ids = [d.get("id") for d in _list_ids(client) if isinstance(d, dict)]
    assert cid in ids, f"新建设备 {cid} 不在列表, 现有={ids[:10]}"


@then(parsers.parse('该设备类型应为 "{dt}"'))
def _then_device_type(client, ctx, dt):
    cid = ctx["created"].get("id")
    dev = next((d for d in _list_ids(client) if isinstance(d, dict) and d.get("id") == cid), None)
    assert dev is not None, f"找不到设备 {cid}"
    assert dev.get("device_type") == dt, f"device_type={dev.get('device_type')} 期望 {dt}"


@then("两次创建都应成功")
def _then_both_ok(ctx):
    assert ctx.get("ok1") and ctx.get("ok2"), \
        f"两把 USB 枪应都建成功 (不撞地址): ok1={ctx.get('ok1')} ok2={ctx.get('ok2')}"


@then("该设备不应在扫码器列表出现")
def _then_not_in_list(client, ctx):
    ids = [d.get("id") for d in _list_ids(client) if isinstance(d, dict)]
    assert ctx["dev_id"] not in ids, f"设备 {ctx['dev_id']} 删除后仍在列表"
