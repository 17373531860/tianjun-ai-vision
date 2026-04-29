"""v3.1.2 多工位广播结算联动 (broadcast settle mode) 仿真测试.

覆盖场景:
  - independent (默认): 各广播工位独立结算, 不联动
  - primary 模式 + 容器从工位:
      * 件数 >= min_items → 跟随结算
      * 件数 < min_items   → 跳过 (上游空箱不被冤判)
      * 件数 = 0           → 跳过
  - primary 模式 + 非容器从工位: 件数 >= min_items 时整盘结算
  - 防重入: 主工位被联动结算时不再二次传染

不依赖真实推理/相机, 用最小 stub 取代 VideoSourceManager.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.scanner import ScannerConnection, ScannerService


# ---------- Stubs ----------
class FakeManager:
    """仿 VideoSourceManager 的强制结算行为, 只追踪调用次数和参数."""

    def __init__(self, channel_id: int, container_mode: bool, item_count: int,
                 has_active_cycle: bool = True):
        self.channel_id = channel_id
        self._container_mode = container_mode
        self._force_settling_in_progress = False
        self._item_count = item_count
        self._has_active_cycle = has_active_cycle
        self.force_settle_calls = []  # [(min_items, reason)]
        self.notify_called_after_force = []

    def force_settle_pending_cycle(self, min_items: int = 1, reason: str = "") -> int:
        self.force_settle_calls.append({"min_items": min_items, "reason": reason})
        if self._container_mode:
            if self._item_count < min_items:
                return 0
            return 1
        if not self._has_active_cycle:
            return 0
        if self._item_count < min_items:
            return 0
        return 1


class FakeChannelManager:
    def __init__(self):
        self.channels = {}

    def get(self, ch):
        if ch not in self.channels:
            raise ValueError(f"channel {ch} not configured")
        return self.channels[ch]


# ---------- Helpers ----------
def make_service(connections: list[ScannerConnection]) -> ScannerService:
    svc = ScannerService.__new__(ScannerService)
    svc._connections = {c.device_id: c for c in connections}
    return svc


def make_conn(device_id, channel_id, broadcast_channels, settle_mode,
              primary_ch=None, min_items=1, name="scn"):
    return ScannerConnection(
        device_id=device_id,
        name=name,
        ip=f"10.0.0.{device_id}",
        port=55256,
        channel_id=channel_id,
        enabled=True,
        broadcast_channels=broadcast_channels,
        broadcast_settle_mode=settle_mode,
        primary_settle_channel=primary_ch,
        primary_settle_min_items=min_items,
    )


def patch_channel_manager(svc, ch_mgr):
    """Monkey-patch import 路径, 让 _dispatch_force_settle 拿到我们的 FakeChannelManager."""
    import backend.api.channel_manager as cm
    cm.get_channel_manager = lambda: ch_mgr  # type: ignore


# ---------- Test cases ----------
def test_independent_no_trigger():
    """模式 independent: 主工位结算不应触发任何强制结算."""
    conn = make_conn(1, 0, [0, 1], "independent")
    svc = make_service([conn])
    ch_mgr = FakeChannelManager()
    ch_mgr.channels[1] = FakeManager(1, container_mode=True, item_count=5)
    patch_channel_manager(svc, ch_mgr)

    triggered = svc.notify_cycle_settled(channel_id=0)
    assert triggered == [], f"independent 模式不应触发, got {triggered}"
    assert ch_mgr.channels[1].force_settle_calls == []
    print("✓ test_independent_no_trigger")


def test_primary_container_above_threshold():
    """primary 模式: 容器从工位件数充足 → 跟随结算."""
    conn = make_conn(1, 0, [0, 1], "primary", primary_ch=0, min_items=1)
    svc = make_service([conn])
    ch_mgr = FakeChannelManager()
    ch_mgr.channels[1] = FakeManager(1, container_mode=True, item_count=3)
    patch_channel_manager(svc, ch_mgr)

    triggered = svc.notify_cycle_settled(channel_id=0)
    assert triggered == [1], f"应带动 ch1, got {triggered}"
    assert len(ch_mgr.channels[1].force_settle_calls) == 1
    assert ch_mgr.channels[1].force_settle_calls[0]["min_items"] == 1
    print("✓ test_primary_container_above_threshold")


def test_primary_container_below_threshold():
    """primary 模式: 容器从工位件数不足 → 跳过."""
    conn = make_conn(1, 0, [0, 1], "primary", primary_ch=0, min_items=3)
    svc = make_service([conn])
    ch_mgr = FakeChannelManager()
    ch_mgr.channels[1] = FakeManager(1, container_mode=True, item_count=1)
    patch_channel_manager(svc, ch_mgr)

    triggered = svc.notify_cycle_settled(channel_id=0)
    # 调用了 force_settle, 但因件数不足返回 0, 视为未触发
    assert triggered == [], f"件数不足应跳过, got {triggered}"
    assert len(ch_mgr.channels[1].force_settle_calls) == 1
    print("✓ test_primary_container_below_threshold")


def test_primary_empty_box_skipped():
    """primary 模式: 空箱(件数=0) 永远跳过, 即使 min_items=0."""
    # min_items=0 时 item_count=0 也算 >= 0, 注意这里我们测的是 min_items=1 (默认)
    conn = make_conn(1, 0, [0, 1], "primary", primary_ch=0, min_items=1)
    svc = make_service([conn])
    ch_mgr = FakeChannelManager()
    ch_mgr.channels[1] = FakeManager(1, container_mode=True, item_count=0)
    patch_channel_manager(svc, ch_mgr)

    triggered = svc.notify_cycle_settled(channel_id=0)
    assert triggered == [], "空箱应跳过"
    print("✓ test_primary_empty_box_skipped")


def test_primary_min_items_zero_aggressive():
    """primary 模式 min_items=0: 即使空箱也跟随结算 (激进, 给极端场景)."""
    conn = make_conn(1, 0, [0, 1], "primary", primary_ch=0, min_items=0)
    svc = make_service([conn])
    ch_mgr = FakeChannelManager()
    ch_mgr.channels[1] = FakeManager(1, container_mode=True, item_count=0)
    patch_channel_manager(svc, ch_mgr)

    triggered = svc.notify_cycle_settled(channel_id=0)
    assert triggered == [1], f"min_items=0 时空箱也应触发, got {triggered}"
    print("✓ test_primary_min_items_zero_aggressive")


def test_primary_non_container_with_active_cycle():
    """primary 模式: 非容器从工位且有活跃 cycle, 件数足 → 整盘结算."""
    conn = make_conn(1, 0, [0, 1], "primary", primary_ch=0, min_items=1)
    svc = make_service([conn])
    ch_mgr = FakeChannelManager()
    ch_mgr.channels[1] = FakeManager(1, container_mode=False, item_count=5,
                                       has_active_cycle=True)
    patch_channel_manager(svc, ch_mgr)

    triggered = svc.notify_cycle_settled(channel_id=0)
    assert triggered == [1]
    print("✓ test_primary_non_container_with_active_cycle")


def test_primary_wrong_source_channel():
    """primary_settle_channel=0 时, 来自 ch=1 的结算事件不应触发联动."""
    conn = make_conn(1, 0, [0, 1], "primary", primary_ch=0)
    svc = make_service([conn])
    ch_mgr = FakeChannelManager()
    ch_mgr.channels[0] = FakeManager(0, container_mode=True, item_count=5)
    ch_mgr.channels[1] = FakeManager(1, container_mode=True, item_count=5)
    patch_channel_manager(svc, ch_mgr)

    triggered = svc.notify_cycle_settled(channel_id=1)  # 非主工位结算
    assert triggered == [], f"非主工位结算不应联动, got {triggered}"
    print("✓ test_primary_wrong_source_channel")


def test_primary_three_channels():
    """primary 模式 3 工位场景: 主工位结算, 其他两个全跟随."""
    conn = make_conn(1, 0, [0, 1, 2], "primary", primary_ch=0, min_items=1)
    svc = make_service([conn])
    ch_mgr = FakeChannelManager()
    ch_mgr.channels[1] = FakeManager(1, container_mode=True, item_count=2)
    ch_mgr.channels[2] = FakeManager(2, container_mode=True, item_count=4)
    patch_channel_manager(svc, ch_mgr)

    triggered = svc.notify_cycle_settled(channel_id=0)
    assert sorted(triggered) == [1, 2], f"应同时带动 ch1+ch2, got {triggered}"
    print("✓ test_primary_three_channels")


def test_disabled_when_one_channel_only():
    """单工位 (broadcast_channels 只有 1 个) 不应触发联动."""
    conn = make_conn(1, 0, [0], "primary", primary_ch=0, min_items=1)
    svc = make_service([conn])
    ch_mgr = FakeChannelManager()
    patch_channel_manager(svc, ch_mgr)

    triggered = svc.notify_cycle_settled(channel_id=0)
    assert triggered == [], f"单工位无意义, got {triggered}"
    print("✓ test_disabled_when_one_channel_only")


def test_force_settle_pending_cycle_real():
    """真实测试 force_settle_pending_cycle 在容器模式下的行为."""
    from backend.api.source_session_lifecycle_mixin import SessionLifecycleMixin
    from backend.api.source_container_grouping_mixin import ContainerGroupingMixin

    class FakeVSM(SessionLifecycleMixin, ContainerGroupingMixin):
        def __init__(self):
            self.channel_id = 0
            self._container_mode = True
            self._container_label = "箱子"
            self._box_objects = {
                "箱子1": {  # 满箱
                    "display_id": "箱子1",
                    "first_seen": 0, "last_seen": 0, "gone_frames": 0,
                    "item_class_counts": {"a": 2, "b": 3},
                    "items_ever_seen": {},
                    "is_complete": False, "had_roi": False,
                    "bbox": {"x": 0, "y": 0, "w": 0.1, "h": 0.1},
                },
                "箱子2": {  # 空箱
                    "display_id": "箱子2",
                    "first_seen": 0, "last_seen": 0, "gone_frames": 0,
                    "item_class_counts": {},
                    "items_ever_seen": {},
                    "is_complete": False, "had_roi": False,
                    "bbox": {"x": 0.5, "y": 0, "w": 0.1, "h": 0.1},
                },
            }
            self._box_settled_results = []
            self._box_counter = 2
            self._force_settling_in_progress = False
            self.project_config = {
                "pipeline_config": {"counting_expected_items": {"a": 2, "b": 3}},
                "steps_config": [],
                "id": 1,
            }
            self.step_display_names = {"a": "A 件", "b": "B 件"}
            self.current_cycle_steps = []
            self.current_cycle_id = None  # 容器模式不需要 cycle_id

        def _settle_box(self, did, expected):
            # 只记录, 不真正落库 (避免触发 _trigger_event/end_cycle)
            self._box_settled_results.append({"display_id": did})
            self._box_objects.pop(did, None)

        def _settle_counting_cycle(self, *a, **kw):
            pass

    vsm = FakeVSM()
    n = vsm.force_settle_pending_cycle(min_items=1, reason="test")
    settled_ids = [r["display_id"] for r in vsm._box_settled_results]
    assert "箱子1" in settled_ids, "满箱应被结算"
    assert "箱子2" not in settled_ids, "空箱不应被结算"
    assert "箱子2" in vsm._box_objects, "空箱应留在 _box_objects 里"
    assert n == 1
    print("✓ test_force_settle_pending_cycle_real (容器: 满箱结算, 空箱跳过)")


def test_force_settle_reentry_guard():
    """已处于强制结算中的工位再被调用应直接返回 0."""
    from backend.api.source_session_lifecycle_mixin import SessionLifecycleMixin

    class FakeVSM(SessionLifecycleMixin):
        def __init__(self):
            self.channel_id = 0
            self._force_settling_in_progress = True  # 已在结算中
            self.project_config = {"pipeline_config": {}}
            self._container_mode = False

    vsm = FakeVSM()
    n = vsm.force_settle_pending_cycle(min_items=1, reason="reentry")
    assert n == 0, f"应被防重入拦下, got {n}"
    print("✓ test_force_settle_reentry_guard")


if __name__ == "__main__":
    test_independent_no_trigger()
    test_primary_container_above_threshold()
    test_primary_container_below_threshold()
    test_primary_empty_box_skipped()
    test_primary_min_items_zero_aggressive()
    test_primary_non_container_with_active_cycle()
    test_primary_wrong_source_channel()
    test_primary_three_channels()
    test_disabled_when_one_channel_only()
    test_force_settle_pending_cycle_real()
    test_force_settle_reentry_guard()
    print("\n=== 11/11 通过 ===")
