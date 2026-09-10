"""MPS 三线程推理微基准: 定位三工位帧率瓶颈 (2026-08-25, M5 Pro).

四种方案对比 (每方案 3 线程 x N 次推理, 统计每线程 fps):
  A. 现状: 全局锁 + 锁内 .cpu() + 锁内 torch.mps.synchronize()
  B. 全局锁 + 锁内 .cpu(), 不做全设备 synchronize
  C. 完全并发无锁 (.cpu() 在各自线程) —— 同时是崩溃压测
  D. 并发无锁 + half=True

跑法: conda activate tianjun && python tests/uat/bench_mps_concurrency.py
崩溃 (Metal 断言弑进程) 会让脚本非零退出, 属于有效结论。
"""
import os
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;1")
import sys
import time
import threading

import cv2  # noqa: E402
import torch  # noqa: E402
from ultralytics import YOLO  # noqa: E402

MODEL = "/Users/tianjun/Public/测试使用/2K17421.pt"
VIDEO_DIR = "/Users/tianjun/Public/测试使用/2K17421(1号工位）视频"
N_THREADS = 3
N_ITERS = 150
LOCK = threading.RLock()


def grab_frame():
    vids = [f for f in os.listdir(VIDEO_DIR) if f.lower().endswith((".mp4", ".avi"))]
    cap = cv2.VideoCapture(os.path.join(VIDEO_DIR, sorted(vids)[0]))
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame


def bench(name, use_lock, do_sync, half, frame):
    # 每线程独立模型实例 (与主程序每通道一个 ChannelManager 一致)
    models = []
    for _ in range(N_THREADS):
        m = YOLO(MODEL)
        m.to("mps")
        m.predict(frame, verbose=False, device="mps", half=half)  # warmup
        models.append(m)

    fps_out = [0.0] * N_THREADS
    err = []

    def worker(i):
        m = models[i]
        t0 = time.time()
        try:
            for _ in range(N_ITERS):
                if use_lock:
                    with LOCK:
                        r = list(m.predict(frame, verbose=False, device="mps",
                                           stream=True, half=half))
                        r = [x.cpu() for x in r]
                        if do_sync:
                            torch.mps.synchronize()
                else:
                    r = list(m.predict(frame, verbose=False, device="mps",
                                       stream=True, half=half))
                    r = [x.cpu() for x in r]
                _ = [len(x.boxes) for x in r]  # 锁外取值, 模拟状态机消费
        except Exception as e:  # noqa: BLE001
            err.append(f"T{i}: {type(e).__name__}: {e}")
            return
        fps_out[i] = N_ITERS / (time.time() - t0)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    t_all = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_t = time.time() - t_all
    per = ", ".join(f"{f:.1f}" for f in fps_out)
    agg = N_THREADS * N_ITERS / total_t
    print(f"[{name}] 每线程fps: [{per}]  合计吞吐: {agg:.1f}fps"
          + (f"  错误: {err}" if err else ""), flush=True)
    del models
    torch.mps.empty_cache()
    return min(fps_out), err


def main():
    frame = grab_frame()
    print(f"帧尺寸: {frame.shape}, torch {torch.__version__}, "
          f"iters={N_ITERS} x {N_THREADS}线程", flush=True)

    bench("A 锁+全同步(现状)", use_lock=True, do_sync=True, half=False, frame=frame)
    bench("B 锁+仅.cpu()   ", use_lock=True, do_sync=False, half=False, frame=frame)
    bench("C 并发无锁      ", use_lock=False, do_sync=False, half=False, frame=frame)
    bench("D 并发无锁+half ", use_lock=False, do_sync=False, half=True, frame=frame)
    print("全部方案跑完, 进程存活 (无 Metal 断言崩溃)", flush=True)


if __name__ == "__main__":
    sys.exit(main())
