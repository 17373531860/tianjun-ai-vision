#!/usr/bin/env python3
"""
压力测试 v2：完全模拟真实前端行为
- MJPEG 视频流持续读取（模拟 <img> 标签）
- 每秒轮询 /detection/results（模拟前端定时器）
- 每 2 秒轮询 /status
- 录制功能开启（session recording）
- 资源监控 + 安全阈值自动停止
"""

import subprocess
import time
import requests
import json
import os
import signal
import sys
import threading
import psutil

BASE_URL = "http://localhost:8001"
API = f"{BASE_URL}/api/v1/source"

VIDEO_PATH = "/home/qianqian/桌面/word/tianjun副本/backend/uploads/videos/8428cd8c9cb54f54ac88636f1a8dbf96_260114-10.mp4"
MODEL_PATH = "/home/qianqian/桌面/word/tianjun副本/backend/uploads/models/66e32c94a28a41669fdbb54abd9de03d_best(4).pt"

PROJECT_CONFIG = {
    "project_id": 4,
    "name": "JT-SOP2",
    "logic_mode": "sequential",
    "steps_config": [
        {"label": "拿取产品", "enabled": True},
        {"label": "调节螺丝", "enabled": True},
        {"label": "检查内框活动性", "enabled": True},
        {"label": "转动滑轮", "enabled": True},
        {"label": "检查外观2", "enabled": True},
        {"label": "确认内框有无脱落", "enabled": True},
        {"label": "放计数板", "enabled": True},
    ],
    "pipeline_config": {
        "sequence_order": [{"step_id": i} for i in range(1, 8)],
        "detection_steps": [1, 2, 3, 4, 5, 6, 7],
        "simultaneous_groups": [{
            "enabled": True,
            "labels": ["放计数板", "拿取产品", "调节螺丝"],
            "time_window": 4,
            "priority_order": ["放计数板", "拿取产品", "调节螺丝"]
        }]
    },
    "events_config": [],
    "counters_config": []
}

SAFETY_THRESHOLD_MB = 2048
TEST_DURATION = 180
SAMPLE_INTERVAL = 5

backend_proc = None
samples = []
mjpeg_running = False
poll_running = False
mjpeg_bytes_total = 0
mjpeg_frames_total = 0
poll_count = 0


def get_gpu_mem():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,power.draw,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        parts = result.stdout.strip().split(", ")
        return {
            "gpu_mem_used_mb": int(parts[0]),
            "gpu_mem_total_mb": int(parts[1]),
            "gpu_power_w": float(parts[2]),
            "gpu_temp_c": int(parts[3]),
        }
    except Exception as e:
        return {"gpu_error": str(e)}


def get_backend_mem():
    try:
        for proc in psutil.process_iter(['pid', 'cmdline', 'memory_info']):
            cmdline = " ".join(proc.info.get('cmdline') or [])
            if 'uvicorn' in cmdline and 'backend.main' in cmdline:
                mem = proc.info['memory_info']
                children_rss = 0
                for child in psutil.Process(proc.pid).children(recursive=True):
                    try:
                        children_rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                return {
                    "backend_pid": proc.pid,
                    "backend_rss_mb": round((mem.rss + children_rss) / 1024 / 1024, 1),
                }
        return {"backend_pid": None}
    except Exception as e:
        return {"backend_error": str(e)}


def mjpeg_reader():
    """模拟前端 <img src="/video_feed"> 持续读取 MJPEG 流"""
    global mjpeg_bytes_total, mjpeg_frames_total, mjpeg_running
    print("[MJPEG] 流读取线程启动")
    while mjpeg_running:
        try:
            resp = requests.get(f"{BASE_URL}/video_feed", stream=True, timeout=5)
            boundary = b'--frame'
            buf = b''
            for chunk in resp.iter_content(chunk_size=4096):
                if not mjpeg_running:
                    break
                buf += chunk
                mjpeg_bytes_total += len(chunk)
                while boundary in buf:
                    idx = buf.index(boundary)
                    mjpeg_frames_total += 1
                    buf = buf[idx + len(boundary):]
        except requests.exceptions.Timeout:
            if mjpeg_running:
                time.sleep(0.5)
        except Exception as e:
            if mjpeg_running:
                time.sleep(1)
    print("[MJPEG] 流读取线程结束")


def results_poller():
    """模拟前端每秒轮询 /detection/results"""
    global poll_count, poll_running
    print("[POLL] 结果轮询线程启动")
    while poll_running:
        try:
            r = requests.get(f"{API}/detection/results", timeout=3)
            poll_count += 1
        except Exception:
            pass
        time.sleep(1)

    print("[POLL] 结果轮询线程结束")


def status_poller():
    """模拟前端轮询 /status"""
    global poll_running
    while poll_running:
        try:
            requests.get(f"{API}/status", timeout=3)
            requests.get(f"{API}/health", timeout=3)
        except Exception:
            pass
        time.sleep(2)


def sample_resources(elapsed_sec):
    vm = psutil.virtual_memory()
    sample = {
        "time_sec": round(elapsed_sec, 1),
        "sys_total_mb": round(vm.total / 1024 / 1024),
        "sys_used_mb": round(vm.used / 1024 / 1024),
        "sys_available_mb": round(vm.available / 1024 / 1024),
        "sys_percent": vm.percent,
        "swap_used_mb": round(psutil.swap_memory().used / 1024 / 1024),
        "mjpeg_frames": mjpeg_frames_total,
        "mjpeg_mb": round(mjpeg_bytes_total / 1024 / 1024, 1),
        "polls": poll_count,
    }
    sample.update(get_gpu_mem())
    sample.update(get_backend_mem())
    try:
        r = requests.get(f"{API}/health", timeout=3)
        if r.ok:
            h = r.json()
            gpu = h.get("gpu")
            if gpu and "memory_allocated_mb" in gpu:
                sample["torch_allocated_mb"] = gpu["memory_allocated_mb"]
                sample["torch_reserved_mb"] = gpu["memory_reserved_mb"]
    except Exception:
        pass
    return sample


def print_sample(s):
    backend_str = f"Backend: {s.get('backend_rss_mb', '?')}MB"
    sys_str = f"SYS: {s['sys_used_mb']}MB/{s['sys_total_mb']}MB ({s['sys_percent']}%) avail={s['sys_available_mb']}MB swap={s['swap_used_mb']}MB"
    gpu_str = f"GPU: {s.get('gpu_mem_used_mb', '?')}MB {s.get('gpu_temp_c', '?')}°C"
    stream_str = f"MJPEG: {s['mjpeg_frames']}帧/{s['mjpeg_mb']}MB | Polls: {s['polls']}"
    line = f"[{s['time_sec']:6.1f}s] {sys_str} | {backend_str} | {gpu_str} | {stream_str}"
    print(line)
    return line


def print_trend_report():
    if len(samples) < 2:
        print("\n[报告] 采样不足")
        return

    print("\n" + "=" * 90)
    print("资源趋势报告（完全仿真版）")
    print("=" * 90)

    detect_samples = [s for s in samples if s.get("backend_rss_mb") and s["time_sec"] > 0]
    if not detect_samples:
        detect_samples = samples

    sys_used = [s["sys_used_mb"] for s in detect_samples]
    print(f"\n系统内存: {sys_used[0]}MB → {sys_used[-1]}MB (变化: {sys_used[-1]-sys_used[0]:+d}MB)")

    backend_rss = [s.get("backend_rss_mb", 0) for s in detect_samples if s.get("backend_rss_mb")]
    if len(backend_rss) >= 2:
        print(f"后端 RSS: {backend_rss[0]}MB → {backend_rss[-1]}MB (变化: {backend_rss[-1]-backend_rss[0]:+.1f}MB)")
        duration_min = (detect_samples[-1]["time_sec"] - detect_samples[0]["time_sec"]) / 60
        if duration_min > 0.5:
            rate = (backend_rss[-1] - backend_rss[0]) / duration_min
            print(f"  增长速率: {rate:+.1f} MB/分钟")
            if rate > 50:
                print(f"  ⚠️  内存泄漏！")
            elif rate > 10:
                print(f"  ⚠️  轻微增长，需关注")
            else:
                print(f"  ✅ 内存稳定")

    gpu_mem = [s.get("gpu_mem_used_mb", 0) for s in detect_samples if s.get("gpu_mem_used_mb")]
    if gpu_mem:
        print(f"GPU 显存: {gpu_mem[0]}MB → {gpu_mem[-1]}MB (变化: {gpu_mem[-1]-gpu_mem[0]:+d}MB)")

    swap = [s["swap_used_mb"] for s in detect_samples]
    print(f"Swap: {swap[0]}MB → {swap[-1]}MB")

    avail = [s["sys_available_mb"] for s in detect_samples]
    min_avail = min(avail)
    print(f"最低可用内存: {min_avail}MB")

    print(f"\nMJPEG 流: 共 {mjpeg_frames_total} 帧, {mjpeg_bytes_total/1024/1024:.1f}MB")
    print(f"API 轮询: {poll_count} 次")
    print("=" * 90)


def start_backend():
    global backend_proc
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "0.0.0.0", "--port", "8001", "--no-access-log"],
        cwd="/home/qianqian/桌面/word/tianjun副本",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
    )
    print(f"[启动] 后端 PID={backend_proc.pid}")
    for i in range(30):
        time.sleep(1)
        try:
            r = requests.get(f"{BASE_URL}/health", timeout=2)
            if r.ok:
                print(f"[启动] 就绪 ({i+1}s)")
                return True
        except Exception:
            pass
    print("[启动] 超时！")
    return False


def stop_all():
    global backend_proc, mjpeg_running, poll_running
    mjpeg_running = False
    poll_running = False
    try:
        requests.post(f"{API}/detection/stop", timeout=5)
        time.sleep(0.5)
        requests.post(f"{API}/video/stop", timeout=5)
    except Exception:
        pass
    if backend_proc:
        backend_proc.terminate()
        try:
            backend_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend_proc.kill()
        print("[停止] 后端已停止")


def cleanup(signum=None, frame=None):
    print("\n[清理] 正在停止...")
    stop_all()
    time.sleep(1)
    print_trend_report()
    sys.exit(0)


signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


def main():
    global mjpeg_running, poll_running

    print("=" * 90)
    print("压力测试 v2：完全仿真（MJPEG流 + 结果轮询 + 状态轮询 + 录制）")
    print(f"时长: {TEST_DURATION}s | 采样: {SAMPLE_INTERVAL}s | 安全阈值: {SAFETY_THRESHOLD_MB}MB")
    print("=" * 90)

    baseline = sample_resources(0)
    print("\n[基线]")
    print_sample(baseline)
    samples.append(baseline)

    print("\n[1] 启动后端...")
    if not start_backend():
        return

    time.sleep(2)

    print("[2] 设置项目 + 启动视频 + 启动检测...")
    requests.post(f"{API}/detection/set-project", json=PROJECT_CONFIG, timeout=30)
    requests.post(f"{API}/video/start",
                  json={"file_path": VIDEO_PATH, "speed": 1.0}, timeout=30)
    time.sleep(2)
    r = requests.post(f"{API}/detection/start",
                      json={"model_path": MODEL_PATH, "conf": 0.25, "iou": 0.45}, timeout=120)
    print(f"  检测: {r.json()}")
    time.sleep(3)

    # 启动前端模拟线程
    print("\n[3] 启动前端模拟（MJPEG流 + 轮询）...")
    mjpeg_running = True
    poll_running = True

    t_mjpeg = threading.Thread(target=mjpeg_reader, daemon=True)
    t_poll = threading.Thread(target=results_poller, daemon=True)
    t_status = threading.Thread(target=status_poller, daemon=True)
    t_mjpeg.start()
    t_poll.start()
    t_status.start()

    time.sleep(2)

    print("\n[4] 持续监控中...")
    print("-" * 120)

    start_time = time.time()

    while True:
        elapsed = time.time() - start_time

        if elapsed >= TEST_DURATION:
            print(f"\n[完成] 测试时间到 ({TEST_DURATION}s)")
            break

        s = sample_resources(elapsed)
        samples.append(s)
        print_sample(s)

        if s["sys_available_mb"] < SAFETY_THRESHOLD_MB:
            print(f"\n⚠️  [安全停止] 可用内存仅 {s['sys_available_mb']}MB!")
            break

        if s.get("swap_used_mb", 0) > 2000:
            print(f"\n⚠️  [安全停止] Swap {s['swap_used_mb']}MB!")
            break

        if backend_proc and backend_proc.poll() is not None:
            print(f"\n💥 [异常] 后端已崩溃! exit={backend_proc.returncode}")
            break

        time.sleep(SAMPLE_INTERVAL)

    print("-" * 120)
    stop_all()
    time.sleep(2)
    print_trend_report()

    log_path = "/home/qianqian/桌面/word/tianjun副本/stress_test_result.json"
    with open(log_path, "w") as f:
        json.dump(samples, f, indent=2, ensure_ascii=False)
    print(f"详细数据: {log_path}")


if __name__ == "__main__":
    main()
