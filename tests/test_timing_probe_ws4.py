# -*- coding: utf-8 -*-
"""v3.49 WS4: 结算耗时埋点 (backend.timing 调试类别) 测试。

设计: 只做埋点不做优化——现场用数据说话再决定瘦身。
  - hook 队列滞留 (入队→出队 lag) + handler 执行耗时: 4 元组带入队时间戳
  - 慢任务 (lag>0.5s / exec>1s) 无条件打 [MES][SLOW], 不开调试也留痕
  - scan_pair 结算 / 外推(同步内联+异步) / 集群上报 分段耗时进 backend.timing
  - backend.timing 默认关 (debug_center 原则: 客户机零开销)
"""
import time

import pytest


def test_timing_category_registered():
    from backend.core import debug_center
    assert "backend.timing" in debug_center.BACKEND_CATEGORIES
    assert debug_center.get_flags().get("backend.timing") is False, "默认必须关"


def test_enqueue_carries_timestamp():
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr.enabled = True

    def _noop(db):
        pass

    before = time.time()
    mgr._enqueue(_noop)
    task = mgr._task_queue.get_nowait()
    assert len(task) == 4, "任务应为 4 元组 (func, args, kwargs, enq_ts)"
    assert before <= task[3] <= time.time()


def test_worker_handles_legacy_3_tuple_and_slow_warn(capfd):
    """老 3 元组兼容 + 慢任务 [MES][SLOW] 无条件留痕。"""
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()

    done = []

    def _slow_handler(db):
        time.sleep(1.05)
        done.append(1)

    def _legacy_handler(db):
        done.append(2)

    mgr.enabled = True
    mgr.start()
    try:
        # 老 3 元组直接塞队列 (模拟未升级的旁路入队方)
        mgr._task_queue.put_nowait((_legacy_handler, (), {}))
        # 新 4 元组 + 慢 handler
        mgr._enqueue(_slow_handler)
        deadline = time.time() + 6
        while len(done) < 2 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        mgr.stop()

    assert sorted(done) == [1, 2], f"两种元组都必须被处理: {done}"
    out = capfd.readouterr().out
    assert "[MES][SLOW]" in out and "_slow_handler" in out, \
        f"慢任务必须无条件留痕: {out[-500:]}"


def test_scan_pair_settle_timing_logged(monkeypatch):
    from backend.core import debug_center
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()

    class FakeSource:
        def settle_for_scan_pair(self, *, force_ng=False, reason="",
                                 prev_wp_id=None, prev_scanned_at=None):
            return 1

    import backend.api.channel_manager as cm_mod
    monkeypatch.setattr(cm_mod.channel_manager, "get", lambda ch: FakeSource())

    debug_center.set_flags({"backend.timing": True})
    try:
        since = debug_center.get_logs()["max_seq"]
        mgr._dispatch_scan_pair_settle(0, force_ng=False, reason="t")
        logs = debug_center.get_logs(since_seq=since)["logs"]
        actions = [e["action"] for e in logs if e["category"] == "backend.timing"]
        assert "scan_pair 结算耗时" in actions, f"缺埋点: {actions}"
    finally:
        debug_center.set_flags({"backend.timing": False})


def test_inline_gateway_dispatch_timing_logged(monkeypatch):
    from backend.core import debug_center
    from backend.services.mes_hooks import MESHookManager
    import backend.services.mes_gateway as gw_mod

    mgr = MESHookManager()
    mgr._async_dispatch_enabled = False  # 同步内联路径

    class FakeGW:
        def dispatch(self, event_type, ctx, channel_id=None):
            return None

    monkeypatch.setattr(gw_mod, "get_mes_gateway", lambda: FakeGW())

    debug_center.set_flags({"backend.timing": True})
    try:
        since = debug_center.get_logs()["max_seq"]
        mgr.dispatch_gateway("cycle_end", {}, 0)
        logs = debug_center.get_logs(since_seq=since)["logs"]
        actions = [e["action"] for e in logs if e["category"] == "backend.timing"]
        assert "外推耗时(同步内联)" in actions, f"缺埋点: {actions}"
    finally:
        debug_center.set_flags({"backend.timing": False})


def test_cluster_post_report_timing_logged(monkeypatch):
    from backend.core import debug_center
    import backend.services.cluster_collector as cc_mod
    from backend.services.cluster_collector import ClusterCollector

    c = ClusterCollector()

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"success": True}

    monkeypatch.setattr(cc_mod.requests, "post",
                        lambda url, json=None, timeout=None: FakeResp())

    debug_center.set_flags({"backend.timing": True})
    try:
        since = debug_center.get_logs()["max_seq"]
        r = c._post_report({"station_id": "B", "box_serial": "BOX-T",
                            "cycle_context": {}, "is_good": True,
                            "event_name": None}, "http://master:8001")
        assert r["success"] is True
        logs = debug_center.get_logs(since_seq=since)["logs"]
        actions = [e["action"] for e in logs if e["category"] == "backend.timing"]
        assert "集群上报耗时" in actions, f"缺埋点: {actions}"
    finally:
        debug_center.set_flags({"backend.timing": False})


def test_timing_off_no_logs(monkeypatch):
    """默认关: 埋点一条不出 (客户机零开销原则)。"""
    from backend.core import debug_center
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()

    class FakeSource:
        def settle_for_scan_pair(self, **kw):
            return 1

    import backend.api.channel_manager as cm_mod
    monkeypatch.setattr(cm_mod.channel_manager, "get", lambda ch: FakeSource())

    assert debug_center.is_on("backend.timing") is False
    since = debug_center.get_logs()["max_seq"]
    mgr._dispatch_scan_pair_settle(0, force_ng=False, reason="t")
    logs = debug_center.get_logs(since_seq=since)["logs"]
    assert not [e for e in logs if e["category"] == "backend.timing"]
