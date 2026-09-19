"""v3.60.1 多码采集通道结算不弹「未绑定工件码」警告 (六和焊接组装工位反馈 2026-09-19).

背景
====
v3.56 周期多码采集是 mes_hooks._handle_scan 的优先互斥路径: 码进槽位状态机后
直接 return, 永远不走单码 scan_pair / pending_workpiece 绑定 → 视觉周期结算时
`had_workpiece` 恒为 False。而 _trigger_event 里 should_warn_no_barcode 只认
"有扫码器 + 该工位没禁扫 + 周期没绑工件" 三条 → 启用多码采集的工位**每次结算
合格都误弹**「未绑定工件码」toast。

修复: should_warn_no_barcode 判定新增第 4 种静默情形 —— 当前项目启用了多码采集
(ScanCollectEngine.get_config 非 None, 走配置缓存零 DB 开销) → 不弹。

测试策略 (对齐 test_event_trigger_router_hook.py 的最小宿主姿势):
- SimpleNamespace 拼最小 host + stub 的 _mes_hook (有扫码器/未禁扫/无绑定工件)
- 直接以普通函数调用 EventTriggerMixin._trigger_event
- 多码采集配置通过引擎 _cfg_cache 注入 (不碰 DB), 用后清理
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.api.source_event_trigger_mixin import EventTriggerMixin
from backend.services.scan_collect import get_scan_collect_engine

PID_SCAN_COLLECT = 91001   # 启用多码采集的项目
PID_PLAIN = 91002          # 未启用多码采集的项目


class _MesHookStub:
    """有扫码器在册 + 工位未禁扫 + 周期没绑工件 → 老逻辑必弹未绑码."""

    def __init__(self):
        self._inspecting_workpiece = {}
        self._pending_workpiece = {}

    def is_channel_scan_disabled(self, channel_id):
        return False

    def has_any_scanner_present(self):
        return True


def _make_host(project_id: int):
    return SimpleNamespace(
        project_config={
            "id": project_id,
            "pipeline_config": {},
            "events_config": [
                {"id": 1, "name": "合格", "show_notification": True, "actions": []},
                {"id": 2, "name": "不合格", "show_notification": True, "actions": []},
            ],
            "logic_mode": "detection",
            "steps_config": [],
        },
        recording_enabled=True,
        _last_ng_time=0,
        counters={},
        cycle_start_time=None,
        cycle_times=[],
        ng_cycle_times=[],
        ng_step_cycle_counts={},
        current_cycle_steps=[],
        _mes_hook=_MesHookStub(),
        channel_id=0,
        _event_seq=0,
        events_log=[],
        current_cycle_id=None,
        _discard_empty_cycle=lambda: None,
        end_cycle=lambda **kwargs: None,
        _persist_counters=lambda: None,
        _dispatch_event_alarm=lambda *a, **k: None,
    )


@pytest.fixture()
def seeded_engine():
    """向引擎配置缓存注入两个项目: 一个启用多码采集, 一个明确无配置."""
    engine = get_scan_collect_engine()
    with engine._lock:
        engine._cfg_cache[PID_SCAN_COLLECT] = {
            "enabled": True,
            "config": {
                "slots": [
                    {"key": "busbar", "label": "母排码", "count": 1},
                    {"key": "chip", "label": "芯子码", "count": 6},
                    {"key": "fixture", "label": "工装码", "count": 1,
                     "role": "closing"},
                ],
            },
        }
        engine._cfg_cache[PID_PLAIN] = None  # 显式 None: 免 DB 查询
    yield engine
    engine.invalidate_config(PID_SCAN_COLLECT)
    engine.invalidate_config(PID_PLAIN)


def _last_event(host):
    assert host.events_log, "事件应已写入 events_log"
    return host.events_log[-1]


def test_多码采集通道_合格结算_不弹未绑码(seeded_engine):
    host = _make_host(PID_SCAN_COLLECT)
    ok = EventTriggerMixin._trigger_event(host, 1, reason="全部步骤完成")
    assert ok is True
    evt = _last_event(host)
    assert evt.get("should_warn_no_barcode") is False, (
        "多码采集接管的通道: 码由槽位状态机独占消费, 不走单码绑定, "
        f"结算不应弹「未绑定工件码」, got {evt}"
    )


def test_多码采集通道_NG结算_同样不弹未绑码(seeded_engine):
    host = _make_host(PID_SCAN_COLLECT)
    ok = EventTriggerMixin._trigger_event(host, 2, reason="缺少步骤: ['芯子1']")
    assert ok is True
    assert _last_event(host).get("should_warn_no_barcode") is False


def test_未启用多码采集_行为零差异_照常弹未绑码(seeded_engine):
    """回归守门: 普通单码绑定工位, 有扫码器+没绑码 → 照旧提醒."""
    host = _make_host(PID_PLAIN)
    ok = EventTriggerMixin._trigger_event(host, 1, reason="全部步骤完成")
    assert ok is True
    evt = _last_event(host)
    assert evt.get("should_warn_no_barcode") is True, (
        f"未启用多码采集的工位不应受本修复影响, got {evt}"
    )


def test_已绑工件_多码采集与否都不弹(seeded_engine):
    """had_workpiece=True 的既有静默路径不受影响."""
    host = _make_host(PID_PLAIN)
    host._mes_hook._inspecting_workpiece[0] = 12345
    EventTriggerMixin._trigger_event(host, 1, reason="ok")
    assert _last_event(host).get("should_warn_no_barcode") is False
