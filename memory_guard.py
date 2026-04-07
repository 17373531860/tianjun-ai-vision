#!/usr/bin/env python3
"""
内存安全守卫 - 防止系统因内存不足而死机
每3秒检查一次，自动采取保护措施：
  - 可用内存 < 2GB：杀掉多余的 FFmpeg 进程（保留最多2个）
  - 可用内存 < 1.5GB：杀掉所有 FFmpeg 进程
  - 可用内存 < 1GB：向后端发送停止检测信号
"""
import os, time, signal, subprocess, sys

BACKEND_URL = "http://127.0.0.1:8001"
CHECK_INTERVAL = 3  # 秒
WARN_THRESHOLD_MB = 2000    # < 2GB: 清理多余 FFmpeg
DANGER_THRESHOLD_MB = 1500  # < 1.5GB: 杀所有 FFmpeg
CRITICAL_THRESHOLD_MB = 1000 # < 1GB: 停止检测

def get_available_mb():
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) // 1024
    return 99999

def get_ffmpeg_pids():
    try:
        out = subprocess.check_output(['pgrep', '-f', 'ffmpeg'], text=True, stderr=subprocess.DEVNULL)
        return [int(p) for p in out.strip().split('\n') if p]
    except subprocess.CalledProcessError:
        return []

def get_python_backend_rss_mb():
    try:
        out = subprocess.check_output(
            ['ps', 'aux'], text=True, stderr=subprocess.DEVNULL
        )
        for line in out.split('\n'):
            if 'anaconda3/envs/tianjun/bin/python' in line and 'memory_guard' not in line:
                parts = line.split()
                return int(parts[5]) // 1024  # RSS in KB -> MB
    except:
        pass
    return 0

def kill_ffmpeg(pids, keep=0):
    if len(pids) <= keep:
        return 0
    to_kill = pids[:len(pids) - keep]
    killed = 0
    for pid in to_kill:
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except:
            pass
    return killed

def stop_detection():
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{BACKEND_URL}/api/v1/source/detection/stop",
            method='POST',
            data=b'',
            headers={'Content-Type': 'application/json'}
        )
        urllib.request.urlopen(req, timeout=3)
        return True
    except:
        return False

def main():
    print(f"[守卫] 内存安全守卫已启动 (PID={os.getpid()})")
    print(f"[守卫] 阈值: 警告<{WARN_THRESHOLD_MB}MB, 危险<{DANGER_THRESHOLD_MB}MB, 紧急<{CRITICAL_THRESHOLD_MB}MB")
    print(f"[守卫] 每 {CHECK_INTERVAL} 秒检查一次，Ctrl+C 停止")
    print("=" * 70)

    start_time = time.time()

    while True:
        try:
            avail = get_available_mb()
            ffmpeg_pids = get_ffmpeg_pids()
            backend_rss = get_python_backend_rss_mb()
            elapsed = int(time.time() - start_time)
            
            status = "✓ 安全"
            action = ""

            if avail < CRITICAL_THRESHOLD_MB:
                status = "!!! 紧急"
                killed = kill_ffmpeg(ffmpeg_pids, keep=0)
                stopped = stop_detection()
                action = f"杀FFmpeg×{killed}, 停检测={'成功' if stopped else '失败'}"
            elif avail < DANGER_THRESHOLD_MB:
                status = "!! 危险"
                killed = kill_ffmpeg(ffmpeg_pids, keep=0)
                action = f"杀全部FFmpeg×{killed}"
            elif avail < WARN_THRESHOLD_MB:
                status = "! 警告"
                if len(ffmpeg_pids) > 2:
                    killed = kill_ffmpeg(ffmpeg_pids, keep=2)
                    action = f"杀多余FFmpeg×{killed}"

            ts = time.strftime('%H:%M:%S')
            line = (f"[{ts}] {elapsed:>4}s | 可用={avail:>5}MB | "
                    f"后端={backend_rss:>5}MB | FFmpeg={len(ffmpeg_pids)}个 | {status}")
            if action:
                line += f" → {action}"
            print(line, flush=True)

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n[守卫] 已停止")
            break
        except Exception as e:
            print(f"[守卫] 错误: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main()
