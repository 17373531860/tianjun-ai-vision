# -*- coding: utf-8 -*-
"""v3.38 每通道落库线程 (PersistWorker) 单测。

RFC「收尾持久化出推理线程」的地基件, 钉死四条不变量:
- W1 严格 FIFO: 作业执行顺序 == 提交顺序 (step → reconcile → cycle_end 依赖它)
- W2 异常隔离: 单作业抛错不杀线程, 后续作业照常执行
- W3 flush 语义: 返回时此前提交的作业全部执行完
- W4 同步回退: TIANJUN_SYNC_PERSIST=1 时原地执行 (现场兜底/测试确定性)
- W5 队列打满: 退化为调用方同步执行, 作业不丢
"""
import threading
import time

import backend.api.source_persist_worker as spw
from backend.api.source_persist_worker import PersistWorker


def test_w1_fifo_order():
    w = PersistWorker(0)
    seen = []
    for i in range(50):
        w.submit(f"job{i}", lambda i=i: seen.append(i))
    assert w.flush(timeout=5)
    assert seen == list(range(50)), "作业必须严格按提交顺序执行"


def test_w2_exception_isolation():
    w = PersistWorker(0)
    seen = []

    def boom():
        raise RuntimeError("作业内部炸了")

    w.submit("boom", boom)
    w.submit("after", lambda: seen.append("after"))
    assert w.flush(timeout=5)
    assert seen == ["after"], "异常作业后线程必须继续消费"
    st = w.stats()
    assert st["failed"] == 1 and st["done"] >= 1 and st["alive"]


def test_w3_flush_waits_for_slow_job():
    w = PersistWorker(0)
    done_flag = []

    def slow():
        time.sleep(0.3)
        done_flag.append(1)

    w.submit("slow", slow)
    t0 = time.time()
    assert w.flush(timeout=5)
    assert done_flag == [1], "flush 返回前慢作业必须已完成"
    assert time.time() - t0 >= 0.25


def test_w4_sync_mode_runs_inline(monkeypatch):
    monkeypatch.setenv("TIANJUN_SYNC_PERSIST", "1")
    w = PersistWorker(0)
    seen = []
    w.submit("inline", lambda: seen.append(threading.current_thread().name))
    # 同步模式: submit 返回时已执行完, 且在调用方线程
    assert seen == [threading.current_thread().name]
    assert w.stats()["alive"] is False, "同步模式不应拉起工作线程"


def test_w5_queue_full_falls_back_inline(monkeypatch):
    monkeypatch.setattr(spw, "_QUEUE_MAX", 2)
    w = PersistWorker(0)
    # 塞一个慢作业占住线程, 再灌满队列
    gate = threading.Event()
    w.submit("block", gate.wait)  # 占住工作线程
    time.sleep(0.05)  # 等它被取走
    w.submit("q1", lambda: None)
    w.submit("q2", lambda: None)
    # 队列已满(2件) → 第三件应原地执行, 不丢
    seen = []
    w.submit("overflow", lambda: seen.append("ran"))
    assert seen == ["ran"], "队列打满时必须退化为同步执行, 作业不能丢"
    assert w.stats()["inline_fallbacks"] == 1
    gate.set()
    assert w.flush(timeout=5)
