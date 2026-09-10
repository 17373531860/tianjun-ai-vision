"""MPS 读写锁语义崩溃压测 + FP16 数值对比 (2026-08-25).

压测部分: 模拟目标设计 —— 推理并发放行(共享), empty_cache / 模型加载释放独占。
用一把 RWLock: 3 推理线程持读锁并发 predict; 1 捣乱线程每 2s 持写锁
empty_cache, 中途还做一次"卸载+重载模型"(写锁)。这是 2026-08-07 弑进程的
原始工况 (推理中 empty_cache / 释放), 若 5 分钟存活则读写锁方案成立。

FP16 部分: 同一批真实帧上跑 FP32 与 FP16, 对比框数与置信度漂移,
判断调参阈值是否会被 half 打翻。

跑法: conda activate tianjun && python -u tests/uat/bench_mps_stress.py
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
STRESS_SECONDS = 300


class RWLock:
    """写优先读写锁: 读者(推理)并发, 写者(empty_cache/加载/释放)独占."""

    def __init__(self):
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    def acquire_read(self):
        with self._cond:
            while self._writer or self._writers_waiting:
                self._cond.wait()
            self._readers += 1

    def release_read(self):
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self):
        with self._cond:
            self._writers_waiting += 1
            while self._writer or self._readers:
                self._cond.wait()
            self._writers_waiting -= 1
            self._writer = True

    def release_write(self):
        with self._cond:
            self._writer = False
            self._cond.notify_all()


def frames_from_video(n=8):
    vids = sorted(f for f in os.listdir(VIDEO_DIR)
                  if f.lower().endswith((".mp4", ".avi")))
    cap = cv2.VideoCapture(os.path.join(VIDEO_DIR, vids[0]))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * i / n))
        ok, f = cap.read()
        if ok:
            out.append(f)
    cap.release()
    return out


def fp16_compare(frames):
    print("== FP16 数值对比 ==", flush=True)
    m = YOLO(MODEL)
    m.to("mps")
    max_conf_drift = 0.0
    box_mismatch = 0
    for i, f in enumerate(frames):
        r32 = m.predict(f, verbose=False, device="mps", conf=0.25, half=False)[0]
        r16 = m.predict(f, verbose=False, device="mps", conf=0.25, half=True)[0]
        c32 = sorted(r32.boxes.conf.cpu().tolist(), reverse=True)
        c16 = sorted(r16.boxes.conf.cpu().tolist(), reverse=True)
        if len(c32) != len(c16):
            box_mismatch += 1
            print(f"  帧{i}: 框数不同 fp32={len(c32)} fp16={len(c16)}", flush=True)
        for a, b in zip(c32, c16):
            max_conf_drift = max(max_conf_drift, abs(a - b))
        print(f"  帧{i}: 框数 {len(c32)}/{len(c16)}, "
              f"conf fp32={[round(x,3) for x in c32[:5]]} fp16={[round(x,3) for x in c16[:5]]}",
              flush=True)
    print(f"最大置信度漂移: {max_conf_drift:.4f}, 框数不一致帧: {box_mismatch}/{len(frames)}",
          flush=True)
    del m
    torch.mps.empty_cache()
    return max_conf_drift, box_mismatch


def stress(frames):
    print(f"== 读写锁混合操作压测 ({STRESS_SECONDS}s) ==", flush=True)
    lock = RWLock()
    stop = threading.Event()
    counts = [0, 0, 0]
    cache_clears = [0]
    reloads = [0]
    errors = []

    models = []
    for _ in range(3):
        m = YOLO(MODEL)
        m.to("mps")
        m.predict(frames[0], verbose=False, device="mps", half=True)
        models.append(m)

    def infer_worker(i):
        j = 0
        while not stop.is_set():
            try:
                lock.acquire_read()
                try:
                    r = list(models[i].predict(frames[j % len(frames)], verbose=False,
                                               device="mps", stream=True, half=True))
                    r = [x.cpu() for x in r]
                finally:
                    lock.release_read()
                _ = [len(x.boxes) for x in r]
                counts[i] += 1
                j += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"infer T{i}: {type(e).__name__}: {e}")
                return

    def chaos_worker():
        t0 = time.time()
        reloaded = False
        while not stop.is_set():
            time.sleep(2)
            try:
                lock.acquire_write()
                try:
                    torch.mps.empty_cache()
                    cache_clears[0] += 1
                    # 中途做一次真实的"卸载+重载" (原始崩溃工况)
                    if not reloaded and time.time() - t0 > STRESS_SECONDS / 2:
                        old = models[1]
                        models[1] = None
                        del old
                        import gc
                        gc.collect()
                        torch.mps.empty_cache()
                        nm = YOLO(MODEL)
                        nm.to("mps")
                        nm.predict(frames[0], verbose=False, device="mps", half=True)
                        models[1] = nm
                        reloads[0] += 1
                        reloaded = True
                        print(f"  [{time.time()-t0:.0f}s] 模型热重载完成", flush=True)
                finally:
                    lock.release_write()
            except Exception as e:  # noqa: BLE001
                errors.append(f"chaos: {type(e).__name__}: {e}")
                return

    threads = [threading.Thread(target=infer_worker, args=(i,)) for i in range(3)]
    threads.append(threading.Thread(target=chaos_worker))
    t0 = time.time()
    for t in threads:
        t.start()
    while time.time() - t0 < STRESS_SECONDS and not errors:
        time.sleep(10)
        el = time.time() - t0
        fps = [c / el for c in counts]
        print(f"  [{el:.0f}s] 每路fps: {[f'{f:.1f}' for f in fps]}, "
              f"cache清理: {cache_clears[0]}, 重载: {reloads[0]}", flush=True)
    stop.set()
    for t in threads:
        t.join(timeout=30)
    el = time.time() - t0
    print(f"压测结束: {el:.0f}s, 总推理 {sum(counts)} 次, "
          f"每路均值 {sum(counts)/3/el:.1f}fps, 错误: {errors or '无'}", flush=True)
    return errors


def main():
    frames = frames_from_video()
    print(f"取样 {len(frames)} 帧, torch {torch.__version__}", flush=True)
    drift, mismatch = fp16_compare(frames)
    errs = stress(frames)
    ok = not errs and drift < 0.03
    print(f"结论: FP16漂移{'可接受' if drift < 0.03 else '过大'} "
          f"({drift:.4f}), 压测{'存活' if not errs else '失败'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
