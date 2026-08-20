"""B1② 外部 MES 推送并发派发 (v3.49 起默认开) 测试。

默认开: get_async_dispatch True, 走每工位执行器 —— 同工位 FIFO 严格保序 /
        跨工位互不阻塞 / 提交非阻塞。
显式关: 走原内联派发 (字节级一致)。
v3.49: 每工位积压超上限不再丢最新, 改为落盘 gateway_spool.jsonl,
        积压清空后由 _drain_gateway_spool_once 回放补账。
"""
import json
import os
import threading
import time


def _make_manager():
    from backend.services.mes_hooks import MESHookManager
    return MESHookManager()


def _wait_drain(mgr, cid, timeout=4.0):
    deadline = time.time() + timeout
    while mgr._gateway_pending.get(cid, 0) > 0 and time.time() < deadline:
        time.sleep(0.01)


def test_default_on(app):
    """v3.49: 没配过 SystemConfig 时默认开 (新装机/从未动过开关的升级客户)。"""
    from backend.db.database import SessionLocal
    from backend.models.models import SystemConfig

    db = SessionLocal()
    try:
        db.query(SystemConfig).filter(
            SystemConfig.key == "mes_async_dispatch").delete()
        db.commit()
    finally:
        db.close()

    mgr = _make_manager()
    assert mgr.get_async_dispatch() is True
    mgr._load_async_dispatch_config()
    assert mgr.get_async_dispatch() is True, "缺省(无 SystemConfig 行)应默认开"


def test_explicit_off_respected(app):
    """升级客户显式关过 → 重启后保持关 (走旧内联路径)。"""
    mgr = _make_manager()
    mgr.set_async_dispatch(False)
    assert mgr.get_async_dispatch() is False

    mgr2 = _make_manager()
    mgr2._load_async_dispatch_config()
    assert mgr2.get_async_dispatch() is False, "显式 false 必须尊重, 不能被默认开覆盖"

    mgr.set_async_dispatch(True)  # 复位回默认态, 不污染其他用例


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


def test_dispatch_gateway_inline_when_off(app, monkeypatch):
    """显式关 → dispatch_gateway 在调用线程内联推送 (与旧版一致)。"""
    import backend.services.mes_gateway as gw_mod
    calls = []

    class FakeGW:
        def dispatch(self, event_type, ctx, channel_id):
            calls.append((event_type, channel_id, threading.current_thread().name))

    monkeypatch.setattr(gw_mod, "get_mes_gateway", lambda: FakeGW())

    mgr = _make_manager()
    mgr._async_dispatch_enabled = False
    mgr.dispatch_gateway("box_complete", {"box_serial": "B-1"}, None)
    assert calls and calls[0][0] == "box_complete"
    assert calls[0][2] == threading.current_thread().name, "关时必须内联同步执行"


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


def test_per_channel_cap_spools_excess_to_disk(app, monkeypatch, tmp_path):
    """v3.49: 积压超上限 → 落盘 gateway_spool.jsonl (不再丢最新)。"""
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
    mgr._gateway_spool_file = str(tmp_path / "gateway_spool.jsonl")
    for i in range(10):
        mgr._submit_gateway_dispatch("cycle_end", {"seq": i}, 0)

    assert mgr._gateway_pending.get(0, 0) <= 3, \
        f"积压超过每工位上限: {mgr._gateway_pending}"

    # 超上限的 7 条应全部落盘 (FIFO)
    with open(mgr._gateway_spool_file, encoding="utf-8") as f:
        spooled = [json.loads(ln) for ln in f if ln.strip()]
    assert [p["ctx"]["seq"] for p in spooled] == [3, 4, 5, 6, 7, 8, 9], \
        f"落盘内容不对: {[p['ctx'].get('seq') for p in spooled]}"
    assert all(p["event_type"] == "cycle_end" and p["channel_id"] == 0
               for p in spooled)

    release.set()
    _wait_drain(mgr, 0)
    assert sorted(done) == [0, 1, 2], f"执行器内的 3 条应正常推完: {done}"


def test_spool_replay_after_backlog_clears(app, monkeypatch, tmp_path):
    """MES 恢复(积压清空)后, _drain_gateway_spool_once 按 FIFO 回放落盘推送。"""
    import backend.services.mes_gateway as gw_mod
    done = []
    lock = threading.Lock()

    class FakeGW:
        def dispatch(self, event_type, ctx, channel_id):
            with lock:
                done.append((event_type, ctx.get("seq"), channel_id))

    monkeypatch.setattr(gw_mod, "get_mes_gateway", lambda: FakeGW())

    mgr = _make_manager()
    mgr._gateway_spool_file = str(tmp_path / "gateway_spool.jsonl")
    # 预置 3 条落盘记录 (模拟断连期积压)
    with open(mgr._gateway_spool_file, "w", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps({"event_type": "cycle_end",
                                "ctx": {"seq": i}, "channel_id": 1,
                                "created_at": time.time()}) + "\n")

    mgr._drain_gateway_spool_once(max_items=10)
    _wait_drain(mgr, 1)

    assert [d[1] for d in done] == [0, 1, 2], f"回放未按 FIFO: {done}"
    assert not os.path.exists(mgr._gateway_spool_file), "回放完 spool 文件应删除"
    assert mgr._gateway_spool_replay_count == 3


def test_spool_replay_holds_while_backlog_high(app, monkeypatch, tmp_path):
    """对应工位积压仍高 (≥上限一半) 时不回放, 避免落盘↔回放打转。"""
    mgr = _make_manager()
    mgr._GATEWAY_MAX_PENDING = 4          # 回放阈值 = 2
    mgr._gateway_spool_file = str(tmp_path / "gateway_spool.jsonl")
    with open(mgr._gateway_spool_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({"event_type": "cycle_end",
                            "ctx": {"seq": 0}, "channel_id": 0,
                            "created_at": time.time()}) + "\n")

    mgr._gateway_pending[0] = 3           # 模拟该工位积压未消化
    mgr._drain_gateway_spool_once(max_items=10)

    assert os.path.exists(mgr._gateway_spool_file), "积压未清时不应回放/清空 spool"
    assert mgr._gateway_spool_replay_count == 0
