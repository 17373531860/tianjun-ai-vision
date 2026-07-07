"""推理线程唯一性回归 (2026-07 TP 频闪真因).

现场事故: 恢复播放时采集线程自启 + resume 两路并发调用启动方法, "检查-启动"
两步间无锁 → 同通道起了两条推理线程, 一条正常出结果、一条抢不到新帧发布
空结果, 交替覆盖发布给前端 → 标注框逐帧频闪 (诊断转储: 2s 内在场翻转 97 次)。

三道保障, 各锁一条:
  1. 启动加锁: N 路并发 start 只允许起 1 条线程
  2. 代数守门: 线程本体发现代数过期立即退出 (防僵尸复活成第二条)
  3. stop 先作废代数再放倒运行标志: 假死被放弃的旧线程不会因新 start
     把运行标志重新置真而复活
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from backend.api.source import VideoSourceManager
from backend.api.source_inference_loop_mixin import InferenceLoopMixin


def _make_host():
    """最小宿主: 只带启动/停止推理线程所需字段, 不碰真模型/真视频。"""
    h = SimpleNamespace(
        _inference_thread=None,
        _inference_running=False,
        _inference_start_lock=threading.Lock(),
        _inference_generation=0,
        channel_id=0,
    )
    started = []

    def fake_loop(gen):
        started.append(gen)
        while h._inference_running and gen == h._inference_generation:
            time.sleep(0.005)

    h._inference_loop = fake_loop
    return h, started


def test_concurrent_start_spawns_single_thread():
    """10 路并发 start → 只起 1 条推理线程 (事故复刻: 采集自启 + resume 撞车)。"""
    h, started = _make_host()
    barrier = threading.Barrier(10)

    def racer():
        barrier.wait()
        VideoSourceManager._start_inference_thread(h)

    racers = [threading.Thread(target=racer) for _ in range(10)]
    for t in racers:
        t.start()
    for t in racers:
        t.join(timeout=5)
    time.sleep(0.05)
    try:
        assert len(started) == 1, f"并发启动起了 {len(started)} 条线程: {started}"
        assert h._inference_thread.is_alive()
    finally:
        h._inference_generation += 1
        h._inference_running = False
        h._inference_thread.join(timeout=2)


def test_stale_generation_thread_exits_immediately():
    """真实推理循环: 代数对不上时立即退出, 不进推理体 (防僵尸线程复活)。"""
    h = SimpleNamespace(
        _inference_running=True,
        is_detecting=True,
        _inference_generation=5,
        latency=0,
    )
    # my_gen=3 != 当前代数 5 → while 首轮就 return, 不会碰任何未初始化的推理字段
    InferenceLoopMixin._inference_loop(h, my_gen=3)


def test_restart_bumps_generation_so_old_thread_dies():
    """stop→start 后代数前进 ≥2, 旧线程即使运行标志复活也会因代数过期退出。"""
    h, started = _make_host()
    VideoSourceManager._start_inference_thread(h)
    gen1 = h._inference_generation
    # 真实 stop 会做 CUDA 同步 (无 GPU 环境只打 WARN), 可接受
    VideoSourceManager._stop_inference_thread(h)
    assert h._inference_generation > gen1, "stop 必须作废旧代数"
    VideoSourceManager._start_inference_thread(h)
    try:
        assert h._inference_generation >= gen1 + 2
        assert started == [gen1, h._inference_generation], \
            f"启动记录异常: {started}"
    finally:
        h._inference_generation += 1
        h._inference_running = False
        h._inference_thread.join(timeout=2)
