# -*- coding: utf-8 -*-
"""v3.49 WS2: 集群副机上报异步化测试。

背景: 原 report_to_master 的 requests.post(timeout=10) 跑在 mes-hook-worker,
主机慢/断网时把扫码配对等整个 hook 队列堵 10s/箱 (捷昌工位2 延迟根因之一)。

覆盖:
  1. enqueue_report 非阻塞 + 发送线程真正 POST 出去
  2. 发送失败 → 落盘 cluster_report_spool.jsonl (不丢)
  3. 主机恢复 → 按 FIFO 顺序补发, spool 清空
  4. spool 非空时新上报排到 spool 尾部 (严格全局 FIFO, 不越过积压)
  5. report_timeout_sec / report_async 配置 KV 读写往返
  6. report_async=False 时 _cluster_dispatch 走旧同步内联路径
"""
import json
import os
import threading
import time

import pytest


def _make_collector(tmp_path):
    from backend.services.cluster_collector import ClusterCollector
    c = ClusterCollector()
    c._report_spool_file = str(tmp_path / "cluster_report_spool.jsonl")
    return c


def _item_seqs(spool_file):
    if not os.path.exists(spool_file):
        return []
    with open(spool_file, encoding="utf-8") as f:
        return [json.loads(ln)["payload"]["box_serial"] for ln in f if ln.strip()]


def test_enqueue_nonblocking_and_sender_posts(app, tmp_path, monkeypatch):
    c = _make_collector(tmp_path)
    posted = []
    monkeypatch.setattr(
        c, "_post_report",
        lambda payload, url: (posted.append((payload["box_serial"], url)),
                              {"success": True})[1])

    t0 = time.time()
    r = c.enqueue_report({}, "BOX-1", "B-0", "http://master:8001")
    assert time.time() - t0 < 0.1, "enqueue_report 阻塞了调用方"
    assert r["success"] and r["queued"] == "memory"

    c.start()
    try:
        deadline = time.time() + 3
        while not posted and time.time() < deadline:
            time.sleep(0.02)
    finally:
        c.stop()
    assert posted and posted[0][0] == "BOX-1", f"发送线程未推送: {posted}"


def test_failure_spools_to_disk(app, tmp_path, monkeypatch):
    c = _make_collector(tmp_path)
    monkeypatch.setattr(c, "_post_report",
                        lambda payload, url: {"success": False, "error": "conn refused"})

    c.enqueue_report({}, "BOX-A", "B-0", "http://master:8001")
    c.start()
    try:
        deadline = time.time() + 3
        while c._spool_report_size() == 0 and time.time() < deadline:
            time.sleep(0.02)
    finally:
        c.stop()

    assert _item_seqs(c._report_spool_file) == ["BOX-A"], "失败上报未落盘"


def test_recovery_replays_fifo(app, tmp_path, monkeypatch):
    c = _make_collector(tmp_path)
    # 预置 3 条断网期积压
    for i in range(3):
        c._spool_report_append({
            "payload": {"station_id": "B-0", "box_serial": f"BOX-{i}",
                        "cycle_context": {}, "is_good": True, "event_name": None},
            "master_url": "http://master:8001", "created_at": time.time(),
        })

    posted = []
    monkeypatch.setattr(
        c, "_post_report",
        lambda payload, url: (posted.append(payload["box_serial"]),
                              {"success": True})[1])

    c.start()
    try:
        deadline = time.time() + 5
        while len(posted) < 3 and time.time() < deadline:
            time.sleep(0.02)
    finally:
        c.stop()

    assert posted == ["BOX-0", "BOX-1", "BOX-2"], f"补发未按 FIFO: {posted}"
    assert not os.path.exists(c._report_spool_file), "补发完 spool 应清空"
    assert c._report_spool_replay_count == 3


def test_new_report_appends_behind_spool_backlog(app, tmp_path):
    """spool 有积压时, 新上报必须排到 spool 尾部, 不允许越过积压直发。"""
    c = _make_collector(tmp_path)
    c._spool_report_append({
        "payload": {"station_id": "B-0", "box_serial": "OLD-1",
                    "cycle_context": {}, "is_good": True, "event_name": None},
        "master_url": "http://master:8001", "created_at": time.time(),
    })

    r = c.enqueue_report({}, "NEW-1", "B-0", "http://master:8001")
    assert r["queued"] == "spool", "有积压时新上报应落盘排队"
    assert _item_seqs(c._report_spool_file) == ["OLD-1", "NEW-1"]
    assert c._report_queue.qsize() == 0


def test_report_timing_config_roundtrip(app):
    """report_timeout_sec / report_async 走 SystemConfig KV, 读写往返 + bool 化。"""
    from backend.db.database import SessionLocal
    from backend.services.cluster_collector import ClusterCollector

    c = ClusterCollector()
    db = SessionLocal()
    try:
        vals = c.save_timing_config(db, {"report_timeout_sec": 30,
                                         "report_async": False})
        db.commit()
        assert vals["report_timeout_sec"] == 30
        assert vals["report_async"] == 0
    finally:
        db.close()

    c.invalidate_config_cache()
    cfg = c.get_config()
    assert cfg["report_timeout_sec"] == 30
    assert cfg["report_async"] is False, "get_config 应把 KV 0/1 转成 bool"
    assert c._report_timeout == 30

    # 复位默认, 不污染其他用例
    db = SessionLocal()
    try:
        c.save_timing_config(db, {"report_timeout_sec": 10, "report_async": True})
        db.commit()
    finally:
        db.close()
    c.invalidate_config_cache()
    assert c.get_config()["report_async"] is True


def test_cluster_dispatch_sync_path_when_async_off(app, monkeypatch):
    """report_async=False → _cluster_dispatch 走旧同步 report_to_master。"""
    from backend.services.mes_hooks import MESHookManager
    import backend.services.cluster_collector as cc_mod

    calls = {"sync": 0, "async": 0}

    class FakeCollector:
        def get_config(self, db=None):
            return {"enabled": True, "station_id": "B", "role": "slave",
                    "sync_mode": "wait_all", "master_url": "http://master:8001",
                    "channel_station_map": {}, "report_async": False}

        def report_to_master(self, **kw):
            calls["sync"] += 1
            return {"success": True}

        def enqueue_report(self, **kw):
            calls["async"] += 1
            return {"success": True, "queued": "memory"}

    monkeypatch.setattr(cc_mod, "get_cluster_collector", lambda: FakeCollector())

    mgr = MESHookManager()
    ctx = {"workpiece": {"serial_no": "BOX-SYNC-1"}}
    skip = mgr._cluster_dispatch(None, ctx, 0, True, "cycle_ok")
    assert skip is True
    assert calls == {"sync": 1, "async": 0}, f"应走同步路径: {calls}"


def test_cluster_dispatch_async_path_default(app, monkeypatch):
    """report_async 默认(缺省=真) → _cluster_dispatch 走 enqueue_report。"""
    from backend.services.mes_hooks import MESHookManager
    import backend.services.cluster_collector as cc_mod

    calls = {"sync": 0, "async": 0}

    class FakeCollector:
        def get_config(self, db=None):
            return {"enabled": True, "station_id": "B", "role": "slave",
                    "sync_mode": "wait_all", "master_url": "http://master:8001",
                    "channel_station_map": {}, "report_async": True}

        def report_to_master(self, **kw):
            calls["sync"] += 1
            return {"success": True}

        def enqueue_report(self, **kw):
            calls["async"] += 1
            return {"success": True, "queued": "memory"}

    monkeypatch.setattr(cc_mod, "get_cluster_collector", lambda: FakeCollector())

    mgr = MESHookManager()
    ctx = {"workpiece": {"serial_no": "BOX-ASYNC-1"}}
    skip = mgr._cluster_dispatch(None, ctx, 0, True, "cycle_ok")
    assert skip is True
    assert calls == {"sync": 0, "async": 1}, f"应走异步路径: {calls}"
