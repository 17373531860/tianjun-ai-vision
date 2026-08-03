# -*- coding: utf-8 -*-
"""v3.46 USB 键盘扫码枪与网络扫码器配置面对齐 — 确定性回归。

背景：usb_hid 扫码枪后端不建网络连接（插上即键盘），而 mes_hooks 的按工位
配置检索（先扫后检/无码告警/重复扫码策略/OK冷却/迟到补绑等）历史上只遍历
网络连接表 → USB 枪即使 DB 里写了这些字段也不生效；注入端点则临时造一个
写死默认值的假连接。v3.46 起 ScannerService 为 usb_hid 按落库配置构造完整
连接对象（纯配置载体），配置检索与注入链路两边都吃到真实配置。

兼容性钉子（对应"不能影响存量客户"的要求）：
  - 两开关默认关 → USB 枪不计入 has_any_scanner_present（"未绑码"提示行为零变化）
  - 用途为 拉工单/报警确认 的枪即使 DB 字段被误置 true 也不参与闸门，
    也不承担注入链路的按工位配置兜底
"""
from types import SimpleNamespace

import pytest


def _make_usb_dev(dev_id=7, channel_id=0, usage="bind", enabled=True,
                  scan_required=False, warn_no_barcode=False, **extra):
    """伪 ScannerDevice：带 _start_device usb_hid 分支读取的字段面。"""
    fields = dict(
        id=dev_id, name=f"usb-gun-{dev_id}", device_type="usb_hid",
        ip="", port=0, channel_id=channel_id, enabled=enabled,
        parse_config={"usb": {"usage": usage}},
        scan_required=scan_required, warn_no_barcode=warn_no_barcode,
        dedup_interval_sec=2, auto_create_workpiece=True, auto_link_order=True,
        broadcast_channels=None,
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


@pytest.fixture()
def svc_and_hook(monkeypatch):
    """裸 ScannerService + 裸 MESHookManager, 前者被 monkeypatch 成全局单例。"""
    from backend.services.scanner import ScannerService
    from backend.services import scanner as scanner_module
    from backend.services.mes_hooks import MESHookManager

    svc = ScannerService()
    monkeypatch.setattr(scanner_module, "get_scanner_service", lambda: svc)
    hook = MESHookManager()
    hook.enabled = True
    return svc, hook


def test_usb_bind_gun_scan_required_gates_its_channel(svc_and_hook):
    svc, hook = svc_and_hook
    svc.add_device(_make_usb_dev(channel_id=1, usage="bind", scan_required=True))

    assert hook.is_scan_required(1) is True
    assert hook.is_scan_required(0) is False       # 别的工位不受影响
    assert hook.is_warn_no_barcode(1) is False     # 只开了先扫后检


def test_usb_gun_warn_no_barcode_and_scanner_present(svc_and_hook):
    svc, hook = svc_and_hook
    svc.add_device(_make_usb_dev(channel_id=0, usage="both", warn_no_barcode=True))

    assert hook.has_any_scanner_present() is True  # 开了开关才计入
    assert hook.is_warn_no_barcode(0) is True
    assert hook.is_scan_required(0) is False


def test_usb_gun_flags_off_keeps_legacy_behavior(svc_and_hook):
    """兼容性：开关全关的 USB 枪不改变任何闸门/提示行为（存量客户零变化）。"""
    svc, hook = svc_and_hook
    svc.add_device(_make_usb_dev(usage="bind"))

    assert hook.has_any_scanner_present() is False
    assert hook.is_scan_required(0) is False
    assert hook.is_warn_no_barcode(0) is False


def test_non_bind_usage_never_gates_even_if_db_flag_true(svc_and_hook):
    """拉工单 / 报警确认按钮的枪与周期绑定无关, 字段误置 true 也不拦周期。"""
    svc, hook = svc_and_hook
    svc.add_device(_make_usb_dev(dev_id=8, usage="pull",
                                 scan_required=True, warn_no_barcode=True))
    svc.add_device(_make_usb_dev(dev_id=9, usage="ack", channel_id=0,
                                 scan_required=True, warn_no_barcode=True))

    assert hook.is_scan_required(0) is False
    assert hook.is_warn_no_barcode(0) is False
    assert hook.has_any_scanner_present() is False


def test_remove_and_stop_all_clear_usb_records(svc_and_hook):
    svc, hook = svc_and_hook
    svc.add_device(_make_usb_dev(dev_id=7, usage="bind", scan_required=True))
    assert hook.is_scan_required(0) is True

    svc.remove_device(7)
    assert hook.is_scan_required(0) is False

    svc.add_device(_make_usb_dev(dev_id=7, usage="bind", scan_required=True))
    svc.stop_all()
    assert hook.is_scan_required(0) is False


# ============ v3.46 第二批: 绑定行为配置面对齐 ============

def test_usb_gun_behavior_config_synced_to_channel(svc_and_hook):
    """重复扫码策略 / OK冷却 / 迟到补绑 按工位检索能查到 USB 枪的落库配置。"""
    svc, hook = svc_and_hook
    svc.add_device(_make_usb_dev(
        dev_id=11, channel_id=2, usage="bind",
        duplicate_scan_action="queue",
        ok_rescan_cooldown_sec=30,
        late_scan_bind_window_sec=5,
    ))

    assert hook._get_duplicate_scan_action(2) == "queue"
    assert hook._get_ok_rescan_cooldown(2) == 30
    assert hook._get_late_bind_window(2) == 5
    # 别的工位仍是全局默认
    assert hook._get_duplicate_scan_action(0) == "overwrite"
    assert hook._get_ok_rescan_cooldown(0) == 0
    assert hook._get_late_bind_window(0) == 3


def test_simulate_scan_resolves_usb_device_config(svc_and_hook, monkeypatch):
    """注入端点按工位兜底命中 USB 枪时, 走它的真实配置而非临时假连接。"""
    svc, hook = svc_and_hook
    svc.add_device(_make_usb_dev(dev_id=12, channel_id=0, usage="bind",
                                 dedup_interval_sec=5))
    seen = []
    monkeypatch.setattr(svc, "_on_data_received",
                        lambda conn, raw: seen.append(conn))

    svc.simulate_scan("SN-001", channel_id=0)
    assert seen and seen[0].device_id == 12
    assert seen[0].dedup_interval_sec == 5

    # 显式 device_id 也能直达 USB 记录
    seen.clear()
    svc.simulate_scan("SN-002", device_id=12, channel_id=0)
    assert seen and seen[0].device_id == 12


def test_simulate_scan_ignores_pull_usage_gun(svc_and_hook, monkeypatch):
    """拉工单用途的 USB 枪不承担绑定链路配置, 无绑定枪时回退临时虚拟连接。"""
    svc, hook = svc_and_hook
    svc.add_device(_make_usb_dev(dev_id=13, channel_id=3, usage="pull"))
    seen = []
    monkeypatch.setattr(svc, "_on_data_received",
                        lambda conn, raw: seen.append(conn))

    svc.simulate_scan("SN-003", channel_id=3)
    assert seen and seen[0].device_id != 13   # 落到 QA 虚拟兜底连接
