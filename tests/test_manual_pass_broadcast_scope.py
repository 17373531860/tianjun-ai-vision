"""任务 B：人工合格放行的组口径覆盖扫码枪广播面。"""
import threading
from types import SimpleNamespace

import pytest

from backend import manual_pass_v3530a as manual_pass


class FakeVsm:
    def __init__(self, channel_id, *, active=True, complete=False):
        self.channel_id = channel_id
        self.is_detecting = True
        self._tracking_cycle_active = active
        self._tracking_was_complete = complete
        self._force_settling_in_progress = False
        self.project_config = {
            "logic_mode": "tracking",
            "pipeline_config": {
                "manual_pass_enabled": True,
                "tracking_settle_on_complete": True,
                "tracking_cycle_strategy": "roi_exit",
            },
        }
        self.requests = []

    def request_manual_pass(self, scope="channel", source="api", user=""):
        self.requests.append({"scope": scope, "source": source, "user": user})
        ok, msg = manual_pass._manual_pass_gate(self)
        return {"ok": ok, "msg": msg or "人工合格放行已排队"}


class FakeScannerService:
    def __init__(self, connections, detecting=(0, 1, 2)):
        self._connections = {i: conn for i, conn in enumerate(connections)}
        self.detecting = set(detecting)

    def _resolve_bound_channels(self, conn):
        if getattr(conn, "broken", False):
            raise RuntimeError("坏连接")
        return list(conn.bound)

    @staticmethod
    def _effective_resume_on(conn):
        return conn.resume_on

    def _detecting_channels(self, bound):
        return set(bound) & self.detecting


def scanner_conn(bound, *, scan_mode="E", resume_on="ok_only", broken=False):
    return SimpleNamespace(
        name=f"scanner-{bound}",
        device_type="text_lon",
        scan_mode=scan_mode,
        resume_on=resume_on,
        bound=bound,
        broken=broken,
    )


@pytest.fixture
def install_runtime(monkeypatch):
    def _install(vsms, *, scanners=(), detecting=(0, 1, 2), groups=()):
        channel_manager = SimpleNamespace(channels=vsms)
        group_map = {g["id"]: dict(g) for g in groups}
        reverse = {
            int(channel_id): group["id"]
            for group in groups
            for channel_id in group.get("member_channel_ids", [])
        }
        coordinator = SimpleNamespace(
            _lock=threading.Lock(),
            _groups=group_map,
            _channel_to_group=reverse,
        )
        service = FakeScannerService(scanners, detecting=detecting)
        monkeypatch.setattr(
            "backend.api.channel_manager.channel_manager", channel_manager
        )
        monkeypatch.setattr(
            "backend.services.channel_group_coordinator.get_coordinator",
            lambda: coordinator,
        )
        monkeypatch.setattr(
            "backend.services.scanner.get_scanner_service", lambda: service
        )
        return service

    return _install


def test_e_broadcast_enables_group_without_channel_group(install_runtime):
    current = FakeVsm(0)
    install_runtime(
        {0: current, 1: FakeVsm(1), 2: FakeVsm(2)},
        scanners=[scanner_conn([2, 1, 0])],
        detecting=(0, 1),
        groups=[],
    )

    assert manual_pass.manual_pass_group_available(0) is True
    assert [m.channel_id for m in manual_pass.list_hung_manual_pass_siblings(0)] == [1]


def test_channel_group_and_scanner_members_are_stable_union(install_runtime):
    current = FakeVsm(0)
    install_runtime(
        {0: current, 1: FakeVsm(1), 2: FakeVsm(2)},
        scanners=[scanner_conn([0, 1])],
        groups=[{
            "id": 9,
            "member_channel_ids": [2, 0],
            "settle_strategy": "synchronized_all_ok",
        }],
    )

    siblings = manual_pass.list_hung_manual_pass_siblings(0)

    assert [m.channel_id for m in siblings] == [1, 2]


def test_single_channel_scanner_keeps_group_unavailable(install_runtime):
    current = FakeVsm(0)
    install_runtime({0: current}, scanners=[scanner_conn([0])], groups=[])

    assert manual_pass.manual_pass_group_available(0) is False
    assert manual_pass.list_hung_manual_pass_siblings(0) == []


def test_broken_scanner_connection_is_isolated(install_runtime):
    current = FakeVsm(0)
    install_runtime(
        {0: current, 1: FakeVsm(1)},
        scanners=[scanner_conn([0, 99], broken=True), scanner_conn([0, 1])],
        groups=[],
    )

    assert manual_pass.manual_pass_group_available(0) is True
    assert [m.channel_id for m in manual_pass.list_hung_manual_pass_siblings(0)] == [1]


def test_group_queue_preserves_provenance_and_skips_completed_member(install_runtime):
    current = FakeVsm(0)
    sibling = FakeVsm(1)
    completed = FakeVsm(2, complete=True)
    install_runtime(
        {0: current, 1: sibling, 2: completed},
        scanners=[scanner_conn([0, 1, 2])],
        groups=[],
    )

    assert manual_pass.manual_pass_group_available(0) is True

    result = manual_pass.queue_manual_pass_scope(
        0, scope="group", source="trigger", user="operator-a"
    )

    assert result["ok"] is True
    assert result["msg"] == "人工合格放行已排队"
    assert result["queued"] == [0, 1]
    assert current.requests == [{
        "scope": "channel", "source": "trigger", "user": "operator-a"
    }]
    assert sibling.requests == [{
        "scope": "channel", "source": "trigger", "user": "operator-a"
    }]
    assert completed.requests == []
    assert result["skipped"] == [{"channel": 2, "reason": "已齐件，请等自动结算"}]


def test_request_contract_keeps_source_user_and_ok_msg_shape():
    current = FakeVsm(0)

    result = manual_pass.request_manual_pass(
        current, scope="group", source="api", user="operator-b"
    )

    assert result == {"ok": True, "msg": "人工合格放行已排队", "channel": 0}
    assert current._manual_pass_request["scope"] == "group"
    assert current._manual_pass_request["source"] == "api"
    assert current._manual_pass_request["user"] == "operator-b"


def test_force_pass_rechecks_gate_after_waiting_for_settle_lock(monkeypatch):
    """自动结算若在等锁期间完成，人工放行不得补开周期或重复加产量。"""
    current = FakeVsm(0)
    calls = {"ensure": 0, "settle": 0}

    class CompleteDuringAcquireLock:
        def __init__(self, manager):
            self.manager = manager
            self.released = False

        def acquire(self, blocking=False):
            assert blocking is False
            self.manager._tracking_cycle_active = False
            return True

        def release(self):
            self.released = True

    current._settle_lock = CompleteDuringAcquireLock(current)
    current._tracking_objects = {}
    current._tracking_class_counters = {}
    current._event_counters = {}
    current._stack_counters = {}

    def ensure_cycle(_now):
        calls["ensure"] += 1
        return True

    def settle_cycle(*_args):
        calls["settle"] += 1

    current._soc_ensure_db_cycle = ensure_cycle
    current._settle_counting_cycle = settle_cycle
    monkeypatch.setattr(
        "backend.plugin_system.hook_dispatch.fire_plugin_hook",
        lambda *_args, **_kwargs: {},
    )

    result = manual_pass.force_pass_counting_cycle(
        current,
        {"scope": "channel", "source": "api", "user": "operator-race"},
    )

    assert result == {"ok": False, "msg": "当前没有挂账周期"}
    assert calls == {"ensure": 0, "settle": 0}
    assert current._settle_lock.released is True
