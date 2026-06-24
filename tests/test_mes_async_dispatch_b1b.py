"""B1② 外部 MES 推送并发派发 (默认关) 测试。

默认关: get_async_dispatch False, 走原内联派发 (字节级一致)。
开关开: 每工位一条执行器 —— 同工位 FIFO 严格保序 / 跨工位互不阻塞 / 提交非阻塞 /
        每工位积压超上限丢最新 (MES 长时间不通的背压)。
"""
import threading
import time


def _make_manager():
    from backend.services.mes_hooks import MESHookManager
    return MESHookManager()


def _wait_drain(mgr, cid, timeout=4.0):
    deadline = time.time() + timeout
    while mgr._gateway_pending.get(cid, 0) > 0 and time.time() < deadline:
        time.sleep(0.01)


def test_default_off(app):
    mgr = _make_manager()
    assert mgr.get_async_dispatch() is False


def test_toggle_persists_and_reloads(app):
    from backend.db.database import SessionLocal
    from backend.models.models import SystemConfig

    mgr = _make_manager()
    mgr.set_async_dispatch(True)
    assert mgr.get_async_dispatch() is True

    db = SessionLocal()
    try:
        row = db.query(SystemConfig).filter(
            SystemConfig.key == "mes_async_dispatch").first()
        assert row is not None and str(row.value).lower() == "true"
    finally:
        db.close()

    # 新管理器从盘读回 (模拟重启)
    mgr2 = _make_manager()
    mgr2._load_async_dispatch_config()
    assert mgr2.get_async_dispatch() is True

    mgr.set_async_dispatch(False)  # 复位, 不污染其他用例


def test_per_channel_fifo_order(app, monkeypatch):
    import backend.services.mes_gateway as gw_mod
    recorded = []
    lock = threading.Lock()

    class FakeGW:
        def dispatch(self, event_type, ctx, channel_id):
            time.sleep(0.01)
            with lock:
                recorded.append((channel_id, ctx["seq"]))

    monkeypatch.setattr(gw_mod, "get_mes_gateway", lambda: FakeGW())

    mgr = _make_manager()
    for i in range(6):
        mgr._submit_gateway_dispatch("cycle_end", {"seq": i}, 0)
    _wait_drain(mgr, 0)

    seqs = [s for (c, s) in recorded if c == 0]
    assert seqs == [0, 1, 2, 3, 4, 5], f"同工位推送未严格保序: {seqs}"


def test_cross_channel_nonblocking(app, monkeypatch):
    import backend.services.mes_gateway as gw_mod
    order = []
    lock = threading.Lock()
    slow_started = threading.Event()

    class FakeGW:
        def dispatch(self, event_type, ctx, channel_id):
            if channel_id == 0:
                slow_started.set()
                time.sleep(0.4)  # 慢工位 (模拟 MES 慢)
            with lock:
                order.append(channel_id)

    monkeypatch.setattr(gw_mod, "get_mes_gateway", lambda: FakeGW())

    mgr = _make_manager()
    t0 = time.time()
    mgr._submit_gateway_dispatch("cycle_end", {"seq": 0}, 0)  # 慢工位
    assert time.time() - t0 < 0.15, "提交外推阻塞了调用线程"

    slow_started.wait(1.0)
    mgr._submit_gateway_dispatch("cycle_end", {"seq": 0}, 1)  # 快工位, 不同 channel

    deadline = time.time() + 2.0
    while 1 not in order and time.time() < deadline:
        time.sleep(0.01)
    assert order and order[0] == 1, f"快工位被慢工位阻塞 (未隔离), order={order}"

    _wait_drain(mgr, 0)
    _wait_drain(mgr, 1)


def test_per_channel_cap_drops_excess(app, monkeypatch):
    import backend.services.mes_gateway as gw_mod
    release = threading.Event()
    done = []

    class FakeGW:
        def dispatch(self, event_type, ctx, channel_id):
            release.wait(3.0)  # 卡住, 模拟 MES 长时间不通
            done.append(ctx["seq"])

    monkeypatch.setattr(gw_mod, "get_mes_gateway", lambda: FakeGW())

    mgr = _make_manager()
    mgr._GATEWAY_MAX_PENDING = 3
    for i in range(10):
        mgr._submit_gateway_dispatch("cycle_end", {"seq": i}, 0)

    assert mgr._gateway_pending.get(0, 0) <= 3, \
        f"积压超过每工位上限: {mgr._gateway_pending}"

    release.set()
    _wait_drain(mgr, 0)
    assert len(done) <= 3, f"超上限部分未被丢弃, 完成了 {len(done)} 条"
