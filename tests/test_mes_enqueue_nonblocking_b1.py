"""B1① MES hook 队列满时不阻塞结算线程测试。

队列满 + critical 任务: 必须立即返回(不再 put(timeout=0.8) 阻塞), 且落盘保留。
"""
import time


def _noop():
    pass


def test_b1_full_queue_does_not_block_caller():
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr.enabled = True

    # 填满队列(maxsize=500), 不启动 worker 消费
    while True:
        try:
            mgr._task_queue.put_nowait((_noop, (), {}))
        except Exception:
            break
    assert mgr._task_queue.full()

    spills = {"n": 0}
    mgr._spill_task = lambda *a, **k: (spills.__setitem__("n", spills["n"] + 1) or True)

    t0 = time.time()
    mgr._enqueue(_noop, critical=True)
    elapsed = time.time() - t0

    # 关键: 不再阻塞 0.8s, 应几乎瞬时返回
    assert elapsed < 0.2, f"_enqueue 阻塞了 {elapsed:.3f}s, B1① 失效"
    # 关键任务已落盘保留, 且计入丢弃统计
    assert spills["n"] == 1
    assert mgr._queue_drop_count >= 1


def test_b1_non_critical_dropped_fast_no_spill():
    from backend.services.mes_hooks import MESHookManager
    mgr = MESHookManager()
    mgr.enabled = True
    while True:
        try:
            mgr._task_queue.put_nowait((_noop, (), {}))
        except Exception:
            break

    spills = {"n": 0}
    mgr._spill_task = lambda *a, **k: (spills.__setitem__("n", spills["n"] + 1) or True)

    t0 = time.time()
    mgr._enqueue(_noop, critical=False)
    elapsed = time.time() - t0
    assert elapsed < 0.2
    assert spills["n"] == 0  # 非关键不落盘
